"""
Shekel Budget App -- Amortization Engine

Pure-function service for loan amortization calculations.
Generates amortization schedules, summary metrics, and payoff analysis.
No database access -- operates only on values passed in.

Supports payment-aware projections: when a list of PaymentRecord
instances is provided, the schedule replays actual/committed payments
month-by-month instead of assuming the contractual amount.  This
enables three projection scenarios from the same engine:

  1. Original schedule -- payments=None, extra_monthly=0
  2. Committed schedule -- payments=confirmed+projected transfers
  3. What-if schedule -- payments=confirmed, extra_monthly=user input
"""

import calendar
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.utils.money import round_money

logger = logging.getLogger(__name__)

TWO_PLACES = Decimal("0.01")


@dataclass(frozen=True)
class PaymentRecord:
    """A single payment applied to a loan.

    Used to replay actual or committed payments through the amortization
    schedule so projections reflect real payment history rather than
    assuming the contractual amount every month.

    Attributes:
        payment_date: The date the payment was made or is projected.
            Matched to the schedule by year-month, not exact day, so
            biweekly payment dates (e.g. 2026-03-06) correctly map to
            the monthly schedule period (2026-03).
        amount: The total payment amount (principal + interest).  Must
            be >= 0.  A zero amount represents a missed payment where
            only interest accrues (negative amortization).
        is_confirmed: True if the payment is Paid/Settled (historical
            fact).  False if Projected (future commitment).
    """

    payment_date: date
    amount: Decimal
    is_confirmed: bool

    def __post_init__(self):
        """Validate payment record fields at construction time.

        Catches invalid data immediately rather than producing wrong
        results deep in the schedule loop.

        Raises:
            TypeError: If payment_date is not a date, amount is not a
                Decimal, or is_confirmed is not a bool.
            ValueError: If amount is negative.
        """
        if not isinstance(self.payment_date, date):
            raise TypeError(
                f"payment_date must be a date, got {type(self.payment_date).__name__}"
            )
        if not isinstance(self.amount, Decimal):
            raise TypeError(
                f"amount must be a Decimal, got {type(self.amount).__name__}"
            )
        if self.amount < 0:
            raise ValueError(
                f"amount must be >= 0, got {self.amount}"
            )
        if not isinstance(self.is_confirmed, bool):
            raise TypeError(
                f"is_confirmed must be a bool, got {type(self.is_confirmed).__name__}"
            )


@dataclass(frozen=True)
class RateChangeRecord:
    """A historical or scheduled rate change applied to an ARM loan.

    Used to replay known rate adjustments through the amortization
    schedule so projections reflect the loan's actual rate history
    rather than assuming a fixed rate for the entire term.

    Attributes:
        effective_date: The date the new rate takes effect.  Matched
            to schedule months by finding the most recent entry with
            effective_date <= payment_date.
        interest_rate: The new annual interest rate as a Decimal
            (e.g., Decimal("0.065") for 6.5%).  Must be >= 0.
    """

    effective_date: date
    interest_rate: Decimal

    def __post_init__(self):
        """Validate rate change record fields at construction time.

        Catches invalid data immediately rather than producing wrong
        results deep in the schedule loop.

        Raises:
            TypeError: If effective_date is not a date or interest_rate
                is not a Decimal.
            ValueError: If interest_rate is negative.
        """
        if not isinstance(self.effective_date, date):
            raise TypeError(
                f"effective_date must be a date, "
                f"got {type(self.effective_date).__name__}"
            )
        if not isinstance(self.interest_rate, Decimal):
            raise TypeError(
                f"interest_rate must be a Decimal, "
                f"got {type(self.interest_rate).__name__}"
            )
        if self.interest_rate < 0:
            raise ValueError(
                f"interest_rate must be >= 0, got {self.interest_rate}"
            )


def calculate_remaining_months(
    origination_date: date, term_months: int, as_of: date | None = None,
) -> int:
    """Return how many payment months remain on a loan.

    Calculates months elapsed from *origination_date* to *as_of* (default
    today) and subtracts from *term_months*.  Never returns below 0.
    """
    if as_of is None:
        as_of = date.today()
    months_elapsed = (
        (as_of.year - origination_date.year) * 12
        + (as_of.month - origination_date.month)
    )
    return max(0, term_months - months_elapsed)


@dataclass
class AmortizationRow:
    """A single month in an amortization schedule.

    The is_confirmed flag distinguishes historical fact from projection:
    True when the row's payment came from a confirmed PaymentRecord
    (Paid/Settled status), False when projected or computed from the
    contractual payment formula.
    """

    month: int
    payment_date: date
    payment: Decimal
    principal: Decimal
    interest: Decimal
    extra_payment: Decimal
    remaining_balance: Decimal
    is_confirmed: bool = False
    interest_rate: Decimal | None = None


@dataclass
class AmortizationSummary:
    """High-level metrics for a loan."""
    monthly_payment: Decimal
    total_interest: Decimal
    payoff_date: date
    total_interest_with_extra: Decimal
    payoff_date_with_extra: date
    months_saved: int
    interest_saved: Decimal


@dataclass(frozen=True)
class ReplayResult:
    """Result of replaying confirmed history up to an as_of date.

    Returned by ``replay_confirmed_history``.  Captures the
    deterministic-past slice of a loan's amortization schedule plus
    the starting state a forward projection needs to pick up where
    replay leaves off, so a caller can compose replay + projection
    without re-deriving balance, next pay date, or remaining months.

    Attributes:
        rows: One ``AmortizationRow`` per replayed month, ordered by
            ``payment_date``.  Every row has ``is_confirmed=True``
            because the function semantically only consumes confirmed
            inputs (the caller filters to confirmed before calling;
            mixed-confirmation inputs are still labelled confirmed in
            the output, preserving the "replay is the
            deterministic-past slice" invariant).  Empty when no
            confirmed payment falls in ``[origination_date, as_of]``.
        balance_as_of: Outstanding loan balance at the close of the
            last replayed month.  When ``rows`` is empty, equals
            ``anchor_balance`` (the anchor IS the starting state;
            replay does not invent pre-anchor history).  Already
            quantized to cents via ``round_money``.
        next_pay_date: First ``payment_date`` a forward projection
            should use.  When ``rows`` is empty, this is
            ``anchor_date + 1 month`` (clamped by ``payment_day``).
            When ``rows`` is non-empty, this is the month after
            ``rows[-1].payment_date``.
        remaining_months_as_of: ``term_months`` minus the count of
            calendar months from ``origination_date`` to
            ``next_pay_date - 1 month`` (i.e. the months already
            consumed by the schedule before projection picks up).
            Floors at 0.
        applicable_rate_as_of: The annual rate in effect at
            ``next_pay_date``.  The most recent rate change with
            ``effective_date <= next_pay_date``, or ``annual_rate``
            when no rate change qualifies.  ``project_forward`` uses
            this as its starting rate so the boundary between replay
            and projection inherits the right rate exactly.
    """

    rows: list[AmortizationRow]
    balance_as_of: Decimal
    next_pay_date: date
    remaining_months_as_of: int
    applicable_rate_as_of: Decimal


def calculate_monthly_payment(
    principal: Decimal,
    annual_rate: Decimal,
    remaining_months: int,
) -> Decimal:
    """Standard amortization formula: M = P * [r(1+r)^n] / [(1+r)^n - 1].

    Returns $0 if principal <= 0 or remaining_months <= 0.
    For zero-rate loans, returns principal / remaining_months.
    """
    if principal <= 0 or remaining_months <= 0:
        return Decimal("0.00")

    if annual_rate <= 0:
        return (principal / remaining_months).quantize(TWO_PLACES, ROUND_HALF_UP)

    monthly_rate = annual_rate / 12
    factor = (1 + monthly_rate) ** remaining_months
    payment = principal * (monthly_rate * factor) / (factor - 1)
    return payment.quantize(TWO_PLACES, ROUND_HALF_UP)


def _advance_month(year: int, month: int, day: int) -> date:
    """Move forward one month, clamping the day to the month's max days."""
    month += 1
    if month > 12:
        month = 1
        year += 1
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, max_day))


def advance_to_next_payment_date(
    reference_date: date, payment_day: int,
) -> date:
    """Return the first payment date that follows ``reference_date``.

    Public helper shared by callers that need to derive the starting
    date of a forward projection from an anchor or origination date.
    ``project_forward`` expects ``starting_date`` to be the first
    payment date of the projection; for both ``calculate_payoff_by_date``
    and ``refinance_calculate`` that date is the month after the
    reference date with the day clamped to the month's last valid day
    (e.g., day 31 in February becomes the 28th or 29th).

    Args:
        reference_date: The anchor date (origination, today's first
            of month, or any other reference).  The returned date is
            in the month immediately following.
        payment_day: Day-of-month payments are due.  Clamped to the
            target month's max days so a payment_day of 31 always
            produces a valid date.

    Returns:
        The next payment date after ``reference_date``.
    """
    return _advance_month(
        reference_date.year, reference_date.month, payment_day,
    )


def _build_payment_lookups(
    payments: list[PaymentRecord],
    origination_date: date | None,
) -> tuple[dict[tuple[int, int], Decimal], dict[tuple[int, int], bool]]:
    """Build year-month lookup dicts from a list of PaymentRecord instances.

    Sums multiple payments in the same month.  For the is_confirmed flag,
    a month is considered confirmed only when ALL payments in that month
    are confirmed -- a mix of confirmed and projected means the month's
    total is not fully confirmed.

    Payments dated before origination_date are silently filtered (they
    may exist as data artifacts from before the loan started).

    Args:
        payments: Non-empty list of PaymentRecord instances.
        origination_date: Loan origination date.  Payments before this
            date are excluded.  If None, no filtering is applied.

    Returns:
        (amount_by_month, confirmed_by_month) where:
            amount_by_month: dict mapping (year, month) -> total Decimal
            confirmed_by_month: dict mapping (year, month) -> bool
    """
    sorted_payments = sorted(payments, key=lambda p: p.payment_date)

    amount_by_month: dict[tuple[int, int], Decimal] = {}
    confirmed_by_month: dict[tuple[int, int], bool] = {}

    for payment in sorted_payments:
        # Filter pre-origination payments.
        if origination_date is not None and payment.payment_date < origination_date:
            continue

        key = (payment.payment_date.year, payment.payment_date.month)
        amount_by_month[key] = amount_by_month.get(key, Decimal("0")) + payment.amount
        # A month is confirmed only if ALL its payments are confirmed.
        if key in confirmed_by_month:
            confirmed_by_month[key] = confirmed_by_month[key] and payment.is_confirmed
        else:
            confirmed_by_month[key] = payment.is_confirmed

    return amount_by_month, confirmed_by_month


def _build_rate_change_list(
    rate_changes: list[RateChangeRecord],
    origination_date: date | None,
) -> list[tuple[date, Decimal]]:
    """Build a sorted, deduplicated list of (effective_date, rate) tuples.

    Pre-origination entries are filtered.  When multiple entries share
    the same effective_date, the last one (after stable sort) wins --
    this is deterministic but likely a data entry error, so a warning
    is logged.

    Args:
        rate_changes: Non-empty list of RateChangeRecord instances.
        origination_date: Loan origination date.  Entries before this
            date are excluded.  If None, no filtering is applied.

    Returns:
        List of (effective_date, interest_rate) sorted by effective_date.
    """
    sorted_changes = sorted(rate_changes, key=lambda r: r.effective_date)

    # Filter pre-origination entries.
    if origination_date is not None:
        sorted_changes = [
            r for r in sorted_changes
            if r.effective_date >= origination_date
        ]

    # Deduplicate by effective_date (last entry wins).
    seen_dates: dict[date, Decimal] = {}
    for record in sorted_changes:
        if record.effective_date in seen_dates:
            logger.warning(
                "Multiple rate changes on %s: overriding %.5f with %.5f",
                record.effective_date,
                seen_dates[record.effective_date],
                record.interest_rate,
            )
        seen_dates[record.effective_date] = record.interest_rate

    return sorted(seen_dates.items(), key=lambda x: x[0])


def _find_applicable_rate(
    payment_date: date,
    rate_schedule: list[tuple[date, Decimal]],
    base_rate: Decimal,
) -> Decimal:
    """Find the applicable annual rate for a given payment date.

    Scans the rate_schedule for the most recent entry with
    effective_date <= payment_date.  Falls back to base_rate if
    no entry qualifies.

    Args:
        payment_date: The payment date for the current schedule month.
        rate_schedule: Sorted list of (effective_date, rate) tuples.
        base_rate: The loan's original annual rate (fallback).

    Returns:
        The applicable annual interest rate as a Decimal.
    """
    applicable_rate = base_rate
    for effective_date, rate in rate_schedule:
        if effective_date <= payment_date:
            applicable_rate = rate
        else:
            break
    return applicable_rate


def replay_confirmed_history(
    *,
    origination_date: date,
    original_principal: Decimal,
    annual_rate: Decimal,
    term_months: int,
    payment_day: int,
    confirmed_payments: list[PaymentRecord],
    rate_changes: list[RateChangeRecord] | None,
    anchor_balance: Decimal | None,
    anchor_date: date | None,
    as_of: date,
) -> ReplayResult:
    """Deterministic replay of confirmed payments forward from the anchor.

    History cannot be what-if'ed: this function deliberately omits
    any acceleration parameter, so the caller cannot apply
    hypothetical principal-acceleration to the recorded past.  The
    first half of the amortization-engine split (architectural plan:
    ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``);
    pairs with ``project_forward`` for the future.

    **Anchor-seeded.**  The anchor (a ``LoanAnchorEvent``) is the
    single source of truth for "what was the balance at date X."
    Replay starts at ``anchor_balance`` on ``anchor_date`` and walks
    forward, applying only confirmed payments whose ``payment_date``
    is strictly after ``anchor_date`` and at or before ``as_of``.
    Months between ``anchor_date`` and the first eligible payment,
    or gaps between consecutive eligible payments, are NOT
    fabricated; replay returns only what was actually recorded.
    This matches ``loan_resolver._replay_balance_from_anchor`` (the
    resolver's current-balance derivation) so the engine schedule
    and the resolver-derived current balance cannot diverge.

    For each eligible confirmed payment, ordered by ``payment_date``:

      - The applicable rate is the most recent rate change with
        ``effective_date <= payment_date`` (falls back to
        ``annual_rate``).  ARM rate transitions re-amortize the
        ``monthly_payment`` baseline (used for the informational
        "extra" calculation) over the remaining balance and remaining
        months at the new rate.
      - Interest is ``balance * (rate / 12)`` rounded to cents at the
        applicable rate.  Principal portion is
        ``total_payment - interest`` (may be negative for negative
        amortization).  Extra is ``max(total_payment -
        monthly_payment, 0)``.  Overpayment is capped at the
        remaining balance.
      - The row's ``month`` field is the integer month delta from
        ``origination_date`` to ``payment_date`` (1-based; Jan 2019
        is month 1 of a Dec 2018 origination).  This survives the
        anchor-seeded start because the loan's calendar position is
        independent of where replay picked up.

    When ``anchor_balance`` and ``anchor_date`` are both ``None``,
    the function defaults to ``(origination_date, original_principal)``
    as the implicit anchor -- the "no anchor recorded, start from
    origination" case the architectural plan documents.  Production
    callers always pass an explicit anchor (Commit 12's backfill
    guarantees at least one anchor event per loan); the None/None
    default exists to keep direct test invocations and the migration
    backfill's intent expressible without constructing a synthetic
    anchor tuple.

    All produced rows are unconditionally ``is_confirmed=True``;
    mixed-confirmation inputs are not distinguished at the row level
    because replay's semantic role is "the deterministic-past
    slice."

    Args:
        origination_date: Loan origination date.  Used to compute
            each row's ``month`` field and ``remaining_months_as_of``.
            Does NOT seed the starting balance -- the anchor does.
        original_principal: Loan amount at origination.  Used as the
            implicit-anchor balance when ``anchor_balance is None``
            and as the contractual-payment baseline for the
            informational "extra" calculation.  Must be > 0 for any
            rows to be produced.
        annual_rate: Base annual interest rate as a Decimal
            (e.g. ``Decimal("0.06")`` for 6%).  Used for the
            contractual-payment derivation and as the rate-change
            fallback when no entry qualifies.
        term_months: Original loan term in months.  Used for the
            contractual payment formula and for
            ``remaining_months_as_of``.
        payment_day: Day-of-month payments are due.  Clamped to the
            month's max days for short months (e.g. day 31 in
            February).
        confirmed_payments: Confirmed payment records.  The function
            does not enforce ``is_confirmed=True`` on its input;
            mixed inputs are still labelled confirmed in the output.
            Pre-anchor and post-as_of entries are filtered.  Empty
            list is valid (produces zero rows; ``balance_as_of`` is
            the anchor balance).
        rate_changes: Optional ARM rate transitions.  ``None`` or
            empty leaves the rate fixed at ``annual_rate`` through
            the replayed window.
        anchor_balance: Verified balance at ``anchor_date`` -- the
            starting balance for replay.  When ``None`` (and
            ``anchor_date`` is also ``None``), defaults to
            ``original_principal`` with the implicit anchor at
            ``origination_date``.
        anchor_date: The date the anchor balance was verified.  Only
            confirmed payments strictly after this date are
            consumed; pre-anchor payments are filtered (their effect
            is already baked into ``anchor_balance``).  ``None``
            requires ``anchor_balance`` to also be ``None`` and
            defaults to ``origination_date``.
        as_of: Cutoff date.  No row is produced for a confirmed
            payment with ``payment_date > as_of``.  Determines
            ``remaining_months_as_of`` and ``next_pay_date``.

    Returns:
        A ``ReplayResult`` describing the replayed slice plus the
        starting state a forward projection needs.

    Raises:
        Nothing.  Validates nothing beyond ``PaymentRecord`` /
        ``RateChangeRecord`` field validation (handled at
        dataclass construction by the caller).
    """
    # Resolve the implicit anchor (origination + original_principal)
    # when no explicit anchor is passed.  Production callers (the
    # scenario composer) always pass an explicit anchor; this default
    # preserves the direct-invocation shape used by tests and by the
    # migration backfill where "no recorded trueup, start from
    # origination" is the intent.
    if anchor_balance is None and anchor_date is None:
        effective_anchor_balance = Decimal(str(original_principal))
        effective_anchor_date = origination_date
    elif anchor_balance is not None and anchor_date is not None:
        effective_anchor_balance = Decimal(str(anchor_balance))
        effective_anchor_date = anchor_date
    else:
        # Mixed None/non-None is a caller bug; treat the supplied
        # half as authoritative and default the other to keep the
        # function total.  Avoids a TypeError surface that callers
        # could trip on legitimately ambiguous inputs.
        effective_anchor_balance = (
            Decimal(str(anchor_balance))
            if anchor_balance is not None
            else Decimal(str(original_principal))
        )
        effective_anchor_date = (
            anchor_date if anchor_date is not None else origination_date
        )

    # Build rate change schedule once; consumed both during the
    # per-row interest calc and for applicable_rate_as_of.
    has_rate_changes = rate_changes is not None and len(rate_changes) > 0
    if has_rate_changes:
        rate_schedule = _build_rate_change_list(
            rate_changes, origination_date,
        )
    else:
        rate_schedule = []

    def _months_from_origination(target: date) -> int:
        """Return integer month delta from ``origination_date`` to ``target``.

        Used to compute each row's ``month`` field and the
        ``remaining_months_as_of`` value.  Day-of-month is ignored
        (calendar-month arithmetic only); negative results are
        clamped at the call site.
        """
        return (
            (target.year - origination_date.year) * 12
            + (target.month - origination_date.month)
        )

    # Helper: compute next_pay_date from a starting (year, month) by
    # advancing one calendar month, day-clamped to ``payment_day``.
    def _next_pay_date_from(starting_year: int, starting_month: int) -> date:
        return _advance_month(starting_year, starting_month, payment_day)

    # Early exit guards.  Empty result honors the anchor for
    # balance_as_of and projects from the anchor + 1 month.
    if (original_principal <= 0
            or term_months <= 0
            or effective_anchor_balance <= 0):
        empty_next_pay = _next_pay_date_from(
            effective_anchor_date.year, effective_anchor_date.month,
        )
        empty_rate = annual_rate
        if rate_schedule:
            empty_rate = _find_applicable_rate(
                empty_next_pay, rate_schedule, annual_rate,
            )
        return ReplayResult(
            rows=[],
            balance_as_of=round_money(effective_anchor_balance),
            next_pay_date=empty_next_pay,
            remaining_months_as_of=max(
                0,
                term_months - _months_from_origination(empty_next_pay) + 1,
            ),
            applicable_rate_as_of=empty_rate,
        )

    # Eligible payments: strictly after the anchor and at or before
    # as_of.  Sorted by date so the iteration order is deterministic
    # regardless of payload order.  Pre-anchor payments are filtered
    # because their effect is already baked into anchor_balance;
    # applying them again would double-count.
    eligible_payments = sorted(
        [
            payment
            for payment in confirmed_payments
            if effective_anchor_date < payment.payment_date <= as_of
        ],
        key=lambda payment: payment.payment_date,
    )

    # Contractual payment baseline (informational; drives the "extra"
    # field on each row).  Held at the original-terms formula and
    # updated only on ARM rate transitions.  Replay's payment amount
    # is always the recorded value, never this baseline.
    monthly_payment = calculate_monthly_payment(
        original_principal, annual_rate, term_months,
    )
    current_annual_rate = annual_rate
    balance = effective_anchor_balance
    rows: list[AmortizationRow] = []

    for payment in eligible_payments:
        if balance <= 0:
            break

        pay_date = payment.payment_date

        # ARM rate transition: when the applicable rate changes, the
        # monthly_payment baseline re-amortizes over the remaining
        # balance and remaining months at the new rate.  Affects only
        # the informational "extra" field; the per-row interest is
        # computed against the period rate directly below.
        if rate_schedule:
            period_rate = _find_applicable_rate(
                pay_date, rate_schedule, annual_rate,
            )
            if period_rate != current_annual_rate:
                current_annual_rate = period_rate
                months_left = max(
                    0,
                    term_months - _months_from_origination(pay_date) + 1,
                )
                if months_left > 0:
                    monthly_payment = calculate_monthly_payment(
                        balance, current_annual_rate, months_left,
                    )

        monthly_rate = (
            current_annual_rate / 12
            if current_annual_rate > 0
            else Decimal("0")
        )
        interest = round_money(balance * monthly_rate)

        total_payment = payment.amount
        principal_portion = total_payment - interest
        extra = max(total_payment - monthly_payment, Decimal("0.00"))

        # Overpayment cap: if the principal portion would drive the
        # balance below zero, absorb the remaining balance exactly
        # and recompute the actual payment + extra.
        if principal_portion >= balance:
            principal_portion = balance
            actual_payment = principal_portion + interest
            extra = max(actual_payment - monthly_payment, Decimal("0.00"))
            balance = Decimal("0.00")
        else:
            actual_payment = principal_portion + interest
            balance -= principal_portion
            balance = round_money(balance)
            # Guard against sub-penny negative balance from rounding.
            if balance < 0:
                balance = Decimal("0.00")

        # Record the rate used for this period when ARM data is
        # present; otherwise leave None so consumers that do not
        # render the rate column see the field absent.
        row_rate = current_annual_rate if rate_schedule else None

        rows.append(AmortizationRow(
            month=_months_from_origination(pay_date),
            payment_date=pay_date,
            payment=round_money(actual_payment),
            principal=round_money(principal_portion),
            interest=interest,
            extra_payment=round_money(extra),
            remaining_balance=balance,
            is_confirmed=True,
            interest_rate=row_rate,
        ))

    # next_pay_date: the month after the last replayed row, or the
    # month after the anchor when no row was emitted.  This is the
    # boundary the composer hands to ``project_forward`` so the
    # transition is seamless.
    if rows:
        last_pay_date = rows[-1].payment_date
        next_pay_date = _next_pay_date_from(
            last_pay_date.year, last_pay_date.month,
        )
    else:
        next_pay_date = _next_pay_date_from(
            effective_anchor_date.year, effective_anchor_date.month,
        )

    if rate_schedule:
        applicable_rate_as_of = _find_applicable_rate(
            next_pay_date, rate_schedule, annual_rate,
        )
    else:
        applicable_rate_as_of = annual_rate

    # remaining_months_as_of: the loan's contractual months minus the
    # months already consumed before ``next_pay_date``.  Derived from
    # the calendar position (months_from_origination(next_pay_date)
    # is the 1-based index of the next scheduled payment), NOT from
    # ``len(rows)`` -- a Shekel user with most history pre-anchor has
    # few rows but many consumed months.
    months_consumed_before_next = max(
        0, _months_from_origination(next_pay_date) - 1,
    )
    remaining_months_as_of = max(0, term_months - months_consumed_before_next)

    return ReplayResult(
        rows=rows,
        balance_as_of=balance,
        next_pay_date=next_pay_date,
        remaining_months_as_of=remaining_months_as_of,
        applicable_rate_as_of=applicable_rate_as_of,
    )


def project_forward(
    *,
    starting_balance: Decimal,
    starting_date: date,
    annual_rate: Decimal,
    remaining_months: int,
    payment_day: int,
    contractual_payment: Decimal,
    monthly_override: dict[tuple[int, int], Decimal] | None = None,
    extra_monthly: Decimal = Decimal("0.00"),
    rate_changes_remaining: list[RateChangeRecord] | None = None,
) -> list[AmortizationRow]:
    """Pure forward projection from a known starting state.

    The second half of the amortization-engine split (architectural
    plan: ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``);
    pairs with ``replay_confirmed_history`` for the past.  ``project_forward``
    has no concept of history and cannot rewrite it -- its inputs
    describe a state at ``starting_date`` and a set of forward-only
    parameters (override, extra, rate changes).

    Two payment paths run per month:

      - **Override.**  When ``(year, month)`` is in
        ``monthly_override``, that value is the TOTAL payment for the
        month.  ``extra_monthly`` is NOT added on top -- the override
        already represents the user's planned outlay (e.g., from a
        projected transfer template).  ``extra_payment`` on the row
        is reported as ``$0.00``; the override IS the payment.
        Negative amortization (override below the period's interest)
        is preserved by leaving ``principal_portion`` negative.  An
        overpayment that would drive the balance below zero is capped
        at the remaining balance (the row absorbs the residue and the
        balance closes at zero).
      - **Contractual + extra.**  When no override exists for the
        month, the payment is ``contractual_payment + extra_monthly``.
        Extra is clamped so it cannot push the balance below zero.
        The final scheduled month absorbs whatever residue remains
        after the contractual P&I split, regardless of extra.

    ARM behavior matches ``replay_confirmed_history``: when
    ``rate_changes_remaining`` is non-empty and the applicable rate
    changes for the current month, ``monthly_payment`` is re-amortized
    over the remaining balance and remaining months at the new rate.
    ``rate_changes_remaining`` is expected to contain only rate
    changes whose ``effective_date`` is at or after ``starting_date``
    (the caller -- typically the scenario composer -- filters out
    rate changes already consumed by replay).  No origination-based
    filter is applied here because projection has no origination date
    concept; entries with duplicate ``effective_date`` are
    deduplicated with a warning, matching
    ``_build_rate_change_list``'s behavior.

    Every row carries ``is_confirmed=False`` because projection rows
    are not facts about the recorded past.  When
    ``rate_changes_remaining`` is non-empty, ``interest_rate`` is the
    applicable rate for the row; otherwise it is ``None`` so
    consumers that do not render the rate column see the field
    absent.

    Args:
        starting_balance: Outstanding balance at ``starting_date``.
            Must be > 0 for any rows to be produced; ``<= 0`` returns
            an empty list (the loan is already paid off).
        starting_date: The first ``payment_date`` of the projection.
            The caller (typically ``replay_confirmed_history``'s
            ``next_pay_date``) determines this; ``project_forward``
            does not derive it from an origination date.  Subsequent
            payment dates advance one month at a time using
            ``payment_day`` clamped to each month's length.
        annual_rate: Annual interest rate as a Decimal (e.g.
            ``Decimal("0.06")`` for 6%).  The starting rate; ARM
            transitions in ``rate_changes_remaining`` override it
            mid-projection.
        remaining_months: Maximum number of months to project.  Used
            both as the loop cap and as the input to ARM
            re-amortization at rate changes.  ``<= 0`` returns an
            empty list.
        payment_day: Day-of-month payments are due.  Clamped to each
            month's max days (e.g. day 31 in February).
        contractual_payment: The contractual P&I (frozen at projection
            start, typically derived by the caller from the original
            terms via ``calculate_monthly_payment``).  Used as the
            payment for months without an override and as the baseline
            for ARM rate-change re-amortization.
        monthly_override: Optional ``(year, month) -> Decimal`` map.
            Each entry replaces the contractual payment for that month
            and suppresses ``extra_monthly`` for that month.  ``None``
            and an empty dict are equivalent -- no months are
            overridden.
        extra_monthly: Additional principal payment applied to each
            non-override month.  Clamped per month so the balance
            cannot drop below zero from extra alone.  Override months
            ignore this parameter entirely -- the override IS the
            user's planned outlay for that month, not a baseline that
            extra accelerates further.
        rate_changes_remaining: Optional ARM rate transitions whose
            ``effective_date`` is at or after ``starting_date``.
            ``None`` or empty leaves the rate fixed at ``annual_rate``
            for the full projection.

    Returns:
        A list of ``AmortizationRow`` instances in payment-date order,
        each with ``is_confirmed=False``.  Empty when
        ``starting_balance <= 0`` or ``remaining_months <= 0``.

    Raises:
        Nothing.  ``RateChangeRecord`` and ``PaymentRecord`` field
        validation happens at dataclass construction in the caller;
        this function trusts its inputs (per CLAUDE.md "Trust
        internal code and framework guarantees").
    """
    if starting_balance <= 0 or remaining_months <= 0:
        return []

    overrides = {} if monthly_override is None else monthly_override

    has_rate_changes = (
        rate_changes_remaining is not None
        and len(rate_changes_remaining) > 0
    )
    if has_rate_changes:
        # No origination filter: projection has no origination concept.
        rate_schedule = _build_rate_change_list(
            rate_changes_remaining, None,
        )
    else:
        rate_schedule = []

    # Defensive: coerce to Decimal even if caller passes a value
    # whose representation could yield float-like surprises.
    balance = Decimal(str(starting_balance))
    monthly_payment = contractual_payment
    current_annual_rate = annual_rate
    monthly_rate = annual_rate / 12 if annual_rate > 0 else Decimal("0")

    pay_year = starting_date.year
    pay_month = starting_date.month
    rows: list[AmortizationRow] = []

    for month_num in range(1, remaining_months + 1):
        if balance <= 0:
            break

        # Calculate payment date for this month.
        max_day = calendar.monthrange(pay_year, pay_month)[1]
        pay_date = date(pay_year, pay_month, min(payment_day, max_day))

        # ARM rate adjustment: when the applicable rate changes,
        # re-amortize the remaining balance over the remaining months
        # at the new rate.  Matches ``replay_confirmed_history``.
        if rate_schedule:
            period_rate = _find_applicable_rate(
                pay_date, rate_schedule, current_annual_rate,
            )
            if period_rate != current_annual_rate:
                current_annual_rate = period_rate
                monthly_rate = (
                    current_annual_rate / 12
                    if current_annual_rate > 0
                    else Decimal("0")
                )
                months_left = remaining_months - month_num + 1
                monthly_payment = calculate_monthly_payment(
                    balance, current_annual_rate, months_left,
                )

        # Period interest at the applicable rate.
        interest = round_money(balance * monthly_rate)

        month_key = (pay_year, pay_month)
        has_override = month_key in overrides

        if has_override:
            # Override path: the override amount IS the total payment
            # for the month.  extra_monthly is NOT added (the
            # architectural plan's critical regression-prevention
            # property -- override months must never carry extra).
            total_payment = overrides[month_key]
            principal_portion = total_payment - interest
            # extra is structurally zero for override months: the
            # override already represents the user's planned outlay.
            extra = Decimal("0.00")

            if principal_portion >= balance:
                # Overpayment cap: absorb remaining balance exactly so
                # the schedule closes without a sub-penny residue.
                principal_portion = balance
                actual_payment = principal_portion + interest
                balance = Decimal("0.00")
            else:
                actual_payment = principal_portion + interest
                balance -= principal_portion
                balance = round_money(balance)
                # Guard against sub-penny negative balance from
                # rounding (mirrors the engine's payment-record
                # branch).
                if balance < 0:
                    balance = Decimal("0.00")
        else:
            # No override: contractual + extra path.  ``extra_monthly``
            # is applied here and only here -- the architectural fix
            # for the "extra applied to ghost historical months" bug.
            principal_portion = monthly_payment - interest
            is_final = (
                principal_portion >= balance
                or month_num == remaining_months
            )
            if is_final:
                principal_portion = balance
                actual_payment = principal_portion + interest
                extra = Decimal("0.00")
                balance = Decimal("0.00")
            else:
                actual_payment = monthly_payment
                # Cap extra so the balance cannot drop below zero
                # from acceleration alone.
                extra = min(extra_monthly, balance - principal_portion)
                extra = max(extra, Decimal("0.00"))
                balance -= principal_portion + extra
                balance = round_money(balance)
                # Guard against sub-penny negative balance from
                # rounding.
                if balance < 0:
                    extra += balance
                    balance = Decimal("0.00")

        # Record the rate used for this period when ARM data is
        # present; otherwise leave None so consumers that do not
        # render the rate column see the field absent.
        row_rate = current_annual_rate if rate_schedule else None

        rows.append(AmortizationRow(
            month=month_num,
            payment_date=pay_date,
            payment=round_money(actual_payment),
            principal=round_money(principal_portion),
            interest=interest,
            extra_payment=round_money(extra),
            remaining_balance=balance,
            is_confirmed=False,
            interest_rate=row_rate,
        ))

        if balance <= 0:
            break

        # Advance to next month.
        pay_month += 1
        if pay_month > 12:
            pay_month = 1
            pay_year += 1

    return rows


def calculate_payoff_by_date(
    current_principal: Decimal,
    annual_rate: Decimal,
    remaining_months: int,
    target_date: date,
    origination_date: date,
    payment_day: int,
    original_principal: Decimal | None = None,
    term_months: int | None = None,
    rate_changes: list[RateChangeRecord] | None = None,
) -> Decimal | None:
    """Calculate required extra monthly payment to pay off by target_date.

    Reframed in terms of :func:`project_forward` (Phase 7 of the
    amortization-engine split documented in
    ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``).
    The function is a pure forward projection from a known starting
    state (``current_principal`` at ``origination_date``), so it maps
    naturally onto the projection primitive: one ``project_forward``
    call establishes the standard payoff date, then a binary search
    repeats the projection with successively larger ``extra_monthly``
    values until the schedule pays off by ``target_date``.  External
    behavior is preserved bit-for-bit; the projected-payments
    follow-up (OPT-1 / F-N) is explicitly out of scope here per
    design decision D-F.

    Args:
        current_principal: Outstanding balance to project from.
            ``<= 0`` returns ``Decimal("0.00")``.
        annual_rate: Starting annual interest rate as a Decimal
            (e.g., ``Decimal("0.065")`` for 6.5%).
        remaining_months: Months remaining on the loan.  Doubles as
            the projection's loop cap for ARM loans and the date-delta
            boundary for the "already past / too soon" guards.
        target_date: The user's desired payoff date.
        origination_date: Anchor for the projection's first payment
            date (``origination_date + 1 month`` clamped to
            ``payment_day``).  The caller is responsible for passing
            "today's first of month" when projecting from current
            state rather than from real origination (see
            ``app.routes.loan.payoff_calculate`` target-date branch).
        payment_day: Day-of-month payments are due.
        original_principal: Original loan amount used to derive the
            contractual payment for fixed-rate loans.  When ``None``
            or when ARM rate changes are present, the contractual
            payment is re-amortized from
            ``(current_principal, annual_rate, remaining_months)``.
        term_months: Original loan term in months.  Used with
            ``original_principal`` to derive the contractual payment.
            Also expands the projection's loop cap to
            ``remaining_months + term_months`` so a partially paid
            loan that pays off before ``remaining_months`` can still
            absorb its final-row residue without hitting the cap.
        rate_changes: Optional list of :class:`RateChangeRecord`
            instances for ARM loans.  Forwarded as
            ``rate_changes_remaining`` to ``project_forward``.

    Returns:
        ``None`` if ``target_date`` is in the past (no extra payment
        can change history).  ``Decimal("0.00")`` when no extra is
        required because the standard schedule already pays off by
        ``target_date`` (or the loan is already paid off).
        Otherwise, the binary-searched Decimal extra-monthly payment
        rounded to cents that achieves payoff at or before
        ``target_date``.
    """
    if current_principal <= 0 or remaining_months <= 0:
        return Decimal("0.00")

    # Derive the contractual payment: from original terms when
    # provided and the loan is fixed-rate; otherwise re-amortize from
    # current state at the current rate (ARM path).
    has_rate_changes = rate_changes is not None and len(rate_changes) > 0
    using_contractual = (
        original_principal is not None
        and term_months is not None
        and not has_rate_changes
    )
    if using_contractual:
        contractual_payment = calculate_monthly_payment(
            original_principal, annual_rate, term_months,
        )
        # Generous cap: a partially paid loan (current < original)
        # pays off before ``remaining_months`` with the contractual
        # payment, so widen ``projection_months`` to cover the
        # early-payoff case; the final-row absorption in
        # ``project_forward`` fires on balance reaching zero, not on
        # hitting the loop cap.
        projection_months = remaining_months + term_months
    else:
        contractual_payment = calculate_monthly_payment(
            current_principal, annual_rate, remaining_months,
        )
        projection_months = remaining_months

    starting_date = advance_to_next_payment_date(
        origination_date, payment_day,
    )

    # Standard projection: no extra.  The contractual payment alone
    # determines the standard payoff date.
    standard = project_forward(
        starting_balance=current_principal,
        starting_date=starting_date,
        annual_rate=annual_rate,
        remaining_months=projection_months,
        payment_day=payment_day,
        contractual_payment=contractual_payment,
        monthly_override=None,
        extra_monthly=Decimal("0.00"),
        rate_changes_remaining=rate_changes,
    )
    if not standard:
        return Decimal("0.00")

    standard_payoff = standard[-1].payment_date
    if standard_payoff <= target_date:
        return Decimal("0.00")

    # Calculate how many months until target_date based on the same
    # starting reference point ``starting_date`` would land on.  The
    # inclusive ``+ 1`` matches the legacy convention so the "target
    # in the past" / "target later than remaining_months" gates fire
    # for the same inputs as before.
    start_year = starting_date.year
    start_month = starting_date.month

    target_months = (
        (target_date.year - start_year) * 12
        + (target_date.month - start_month)
        + 1  # inclusive
    )

    if target_months <= 0:
        return None  # Target date is in the past.

    if target_months >= remaining_months:
        return Decimal("0.00")

    # Binary search for the required extra payment.  Each iteration
    # repeats the projection with a candidate ``extra_monthly`` and
    # narrows the bracket until the bisection width drops below one
    # cent (the convergence criterion the legacy implementation
    # used).
    lo = Decimal("0.01")
    hi = current_principal  # Upper bound: pay it all off immediately.

    for _ in range(100):  # Max iterations for convergence.
        mid = ((lo + hi) / 2).quantize(TWO_PLACES, ROUND_HALF_UP)
        schedule = project_forward(
            starting_balance=current_principal,
            starting_date=starting_date,
            annual_rate=annual_rate,
            remaining_months=projection_months,
            payment_day=payment_day,
            contractual_payment=contractual_payment,
            monthly_override=None,
            extra_monthly=mid,
            rate_changes_remaining=rate_changes,
        )
        if not schedule:
            return mid

        actual_payoff = schedule[-1].payment_date
        if actual_payoff <= target_date:
            hi = mid
        else:
            lo = mid

        if hi - lo <= Decimal("0.01"):
            break

    return hi


