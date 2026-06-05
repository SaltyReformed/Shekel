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
        monthly_pi: Optional recorded recast P&I (principal + interest,
            no escrow) the lender set when this rate took effect.  The
            rate-period engine holds it constant for the period this
            change begins; ``None`` means that period's P&I is derived
            by amortization instead.  Must be > 0 when present.
    """

    effective_date: date
    interest_rate: Decimal
    monthly_pi: Decimal | None = None

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
        if self.monthly_pi is not None:
            if not isinstance(self.monthly_pi, Decimal):
                raise TypeError(
                    f"monthly_pi must be a Decimal or None, "
                    f"got {type(self.monthly_pi).__name__}"
                )
            if self.monthly_pi <= 0:
                raise ValueError(
                    f"monthly_pi must be > 0 when present, got {self.monthly_pi}"
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
class AmortizationRow:  # pylint: disable=too-many-instance-attributes
    """A single month in an amortization schedule.

    The is_confirmed flag distinguishes historical fact from projection:
    True when the row's payment came from a confirmed PaymentRecord
    (Paid/Settled status), False when projected or computed from the
    contractual payment formula.

    Pylint note: ``too-many-instance-attributes`` (9) is suppressed
    because this is a cohesive value record -- one amortization-table
    row -- consumed verbatim across the loan routes, year-end summary,
    and resolver.  Every field is an irreducible column of that row;
    splitting it would fragment a single domain concept and break every
    consumer for no design gain.
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


@dataclass(frozen=True)
class ProjectionInputs:
    """Immutable starting state and forward-only terms for a projection.

    Bundles the inputs that describe a loan's state at
    ``starting_date`` together with the parameters that stay constant
    across a related set of projections.  The Payoff Calculator builds
    one ``ProjectionInputs`` and runs three projections from it
    (Original / Committed / Accelerated) varying only ``monthly_override``
    and ``extra_monthly``, so the three slices cannot diverge in their
    shared starting state -- the load-bearing single-source-of-truth
    invariant in ``loan_resolver.compute_payoff_scenarios``.

    Attributes:
        starting_balance: Outstanding balance at ``starting_date``.
            ``<= 0`` yields an empty projection (loan already paid off).
        starting_date: The first ``payment_date`` of the projection
            (typically the replay's ``next_pay_date``).  Subsequent
            dates advance one month at a time, the day clamped to each
            month's length.
        annual_rate: Starting annual interest rate as a Decimal (e.g.
            ``Decimal("0.06")`` for 6%).  ARM transitions in
            ``rate_changes_remaining`` override it mid-projection.
        remaining_months: Maximum number of months to project; also the
            input to ARM re-amortization at rate changes.  ``<= 0``
            yields an empty projection.
        payment_day: Day-of-month payments are due, clamped to each
            month's max days (e.g. day 31 in February).
        contractual_payment: Contractual P&I, frozen at projection
            start.  The per-month payment when no override exists and
            the baseline for ARM rate-change re-amortization.
        rate_changes_remaining: Optional ARM rate transitions whose
            ``effective_date`` is at or after ``starting_date``.
            ``None`` or empty leaves the rate fixed at ``annual_rate``
            for the full projection.
    """

    starting_balance: Decimal
    starting_date: date
    annual_rate: Decimal
    remaining_months: int
    payment_day: int
    contractual_payment: Decimal
    rate_changes_remaining: list[RateChangeRecord] | None = None


def _apply_override_payment(
    balance: Decimal, interest: Decimal, override_amount: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """Apply one override-month payment; return its principal split.

    The override amount IS the total payment for the month (no extra --
    the override already represents the user's planned outlay).
    Negative amortization (override below the period interest) is
    preserved as a negative principal portion.  An overpayment that
    would drive the balance below zero is capped at the remaining
    balance so the schedule closes without a sub-penny residue.

    Args:
        balance: Outstanding balance before this month's payment.
        interest: Period interest already computed for this month.
        override_amount: The total payment scheduled for the month.

    Returns:
        ``(principal_portion, actual_payment, new_balance)`` -- Decimals;
        ``new_balance`` is quantized to cents and never negative.
    """
    principal_portion = override_amount - interest
    if principal_portion >= balance:
        principal_portion = balance
        return principal_portion, principal_portion + interest, Decimal("0.00")
    new_balance = round_money(balance - principal_portion)
    # Guard against sub-penny negative balance from rounding.
    if new_balance < 0:
        new_balance = Decimal("0.00")
    return principal_portion, principal_portion + interest, new_balance


def _apply_contractual_payment(
    balance: Decimal,
    interest: Decimal,
    monthly_payment: Decimal,
    extra_monthly: Decimal,
    is_last_month: bool,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Apply one contractual (+ extra) payment; return its split.

    The remaining balance is absorbed exactly (and no extra applies)
    when the contractual principal already covers it OR this is the
    loop's last scheduled month.  Otherwise ``extra_monthly`` is clamped
    so acceleration alone cannot drive the balance below zero, and any
    sub-penny rounding residue is folded back into ``extra`` so payment,
    principal, and extra reconcile.

    Args:
        balance: Outstanding balance before this month's payment.
        interest: Period interest already computed for this month.
        monthly_payment: Current contractual P&I (post any ARM recast).
        extra_monthly: Requested additional principal for the month.
        is_last_month: True on the loop's final scheduled month, which
            forces the remaining balance to be absorbed.

    Returns:
        ``(principal_portion, actual_payment, extra, new_balance)`` --
        Decimals; ``new_balance`` is quantized to cents and never
        negative.
    """
    principal_portion = monthly_payment - interest
    # Final row absorbs the residue: the contractual principal already
    # covers the balance, or this is the last scheduled month.
    if principal_portion >= balance or is_last_month:
        principal_portion = balance
        return (
            principal_portion,
            principal_portion + interest,
            Decimal("0.00"),
            Decimal("0.00"),
        )
    # Cap extra so the balance cannot drop below zero from
    # acceleration alone.
    extra = min(extra_monthly, balance - principal_portion)
    extra = max(extra, Decimal("0.00"))
    new_balance = round_money(balance - principal_portion - extra)
    # Guard against sub-penny negative balance from rounding.
    if new_balance < 0:
        extra += new_balance
        new_balance = Decimal("0.00")
    return principal_portion, monthly_payment, extra, new_balance


@dataclass
class _ProjectionState:
    """Mutable per-month state carried through the projection loop.

    Bundles the four values that evolve together as a forward
    amortization walks month by month, so the loop body and its helpers
    share one cohesive state object instead of parallel locals.

    Attributes:
        balance: Outstanding principal after the latest applied payment.
        monthly_payment: Current contractual P&I (recast on ARM changes).
        annual_rate: Current annual rate (changes at ARM transitions).
        monthly_rate: ``annual_rate / 12`` (cached to avoid recomputing).
    """

    balance: Decimal
    monthly_payment: Decimal
    annual_rate: Decimal
    monthly_rate: Decimal


def _recast_for_rate_change(
    state: _ProjectionState,
    pay_date: date,
    rate_schedule: list[tuple[date, Decimal]],
    months_left: int,
) -> None:
    """Apply any ARM rate change effective at ``pay_date`` to ``state``.

    When the most-recent applicable rate differs from the state's
    current rate, updates the state's annual and monthly rates and
    re-amortizes the contractual ``monthly_payment`` over the remaining
    balance and ``months_left`` months at the new rate.  A no-op when no
    rate change applies (the fallback is the state's current rate, so an
    empty or fully-consumed schedule leaves the state untouched).

    Args:
        state: The loop's mutable projection state; updated in place.
        pay_date: The payment date of the current schedule month.
        rate_schedule: Sorted ``(effective_date, rate)`` tuples.
        months_left: Months remaining (including this one), the term for
            the re-amortization formula.
    """
    period_rate = _find_applicable_rate(
        pay_date, rate_schedule, state.annual_rate,
    )
    if period_rate != state.annual_rate:
        state.annual_rate = period_rate
        state.monthly_rate = (
            period_rate / 12 if period_rate > 0 else Decimal("0")
        )
        state.monthly_payment = calculate_monthly_payment(
            state.balance, period_rate, months_left,
        )


def project_forward(
    inputs: ProjectionInputs,
    *,
    monthly_override: dict[tuple[int, int], Decimal] | None = None,
    extra_monthly: Decimal = Decimal("0.00"),
) -> list[AmortizationRow]:
    """Pure forward projection from a known starting state.

    The forward half of the amortization-engine split (architectural
    plan: ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``);
    pairs with a replay of the recorded past
    (``rate_period_engine.replay_schedule``, which superseded this
    module's original ``replay_confirmed_history`` primitive).
    ``project_forward`` has no concept of history and cannot rewrite
    it -- its inputs describe a state at ``starting_date`` and a set of
    forward-only parameters (override, extra, rate changes).

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

    ARM behavior: when ``rate_changes_remaining`` is non-empty and the
    applicable rate changes for the current month, ``monthly_payment``
    is re-amortized
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
        inputs: A :class:`ProjectionInputs` bundling the starting state
            (balance, date) and the forward-only terms (annual rate,
            remaining months, payment day, contractual P&I, optional ARM
            rate changes) that stay constant across a related set of
            projections.  See :class:`ProjectionInputs` for per-field
            semantics.
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

    Returns:
        A list of ``AmortizationRow`` instances in payment-date order,
        each with ``is_confirmed=False``.  Empty when
        ``inputs.starting_balance <= 0`` or
        ``inputs.remaining_months <= 0``.

    Raises:
        Nothing.  ``RateChangeRecord`` and ``PaymentRecord`` field
        validation happens at dataclass construction in the caller;
        this function trusts its inputs (per CLAUDE.md "Trust
        internal code and framework guarantees").
    """
    if inputs.starting_balance <= 0 or inputs.remaining_months <= 0:
        return []

    overrides = {} if monthly_override is None else monthly_override
    # No origination filter: projection has no origination concept.
    rate_schedule = (
        _build_rate_change_list(inputs.rate_changes_remaining, None)
        if inputs.rate_changes_remaining
        else []
    )

    # Defensive: coerce to Decimal even if the caller passes a value
    # whose representation could yield float-like surprises.
    state = _ProjectionState(
        balance=Decimal(str(inputs.starting_balance)),
        monthly_payment=inputs.contractual_payment,
        annual_rate=inputs.annual_rate,
        monthly_rate=(
            inputs.annual_rate / 12 if inputs.annual_rate > 0 else Decimal("0")
        ),
    )

    # First payment date: the starting month with the day clamped to
    # that month's length.  Subsequent dates advance via _advance_month.
    pay_date = date(
        inputs.starting_date.year,
        inputs.starting_date.month,
        min(
            inputs.payment_day,
            calendar.monthrange(
                inputs.starting_date.year, inputs.starting_date.month,
            )[1],
        ),
    )
    rows: list[AmortizationRow] = []

    for month_num in range(1, inputs.remaining_months + 1):
        if state.balance <= 0:
            break

        # ARM rate adjustment: re-amortize over the remaining balance
        # and months at the new rate when the applicable rate changes.
        if rate_schedule:
            _recast_for_rate_change(
                state, pay_date, rate_schedule,
                inputs.remaining_months - month_num + 1,
            )

        interest = round_money(state.balance * state.monthly_rate)
        month_key = (pay_date.year, pay_date.month)

        if month_key in overrides:
            # Override path: the override amount IS the total payment for
            # the month; extra_monthly is NOT added (override months must
            # never carry extra -- the plan's regression-prevention
            # property).
            principal_portion, actual_payment, state.balance = (
                _apply_override_payment(
                    state.balance, interest, overrides[month_key],
                )
            )
            extra = Decimal("0.00")
        else:
            # No override: contractual + extra path.  extra_monthly is
            # applied here and only here; the final scheduled month
            # absorbs the residue regardless of extra.
            principal_portion, actual_payment, extra, state.balance = (
                _apply_contractual_payment(
                    state.balance, interest, state.monthly_payment,
                    extra_monthly, month_num == inputs.remaining_months,
                )
            )

        rows.append(AmortizationRow(
            month=month_num,
            payment_date=pay_date,
            payment=round_money(actual_payment),
            principal=round_money(principal_portion),
            interest=interest,
            extra_payment=round_money(extra),
            remaining_balance=state.balance,
            is_confirmed=False,
            # Rate column only when ARM data is present; else None so
            # consumers that do not render it see the field absent.
            interest_rate=state.annual_rate if rate_schedule else None,
        ))

        if state.balance <= 0:
            break

        # Advance to the next month's payment date (day re-clamped from
        # the original payment_day, not the prior clamped day).
        pay_date = _advance_month(
            pay_date.year, pay_date.month, inputs.payment_day,
        )

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
    contractual_payment: Decimal | None = None,
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
        contractual_payment: Optional SSOT contractual P&I.  When
            provided (the production path: the route passes
            ``state.monthly_payment`` from the resolver), drives both
            the standard projection and every binary-search iteration
            -- so the displayed ``total_monthly = monthly_payment +
            required_extra`` matches the loan card exactly.  When
            ``None`` (the legacy path), the function derives the
            contractual itself from
            ``(current_principal, annual_rate, remaining_months)``
            for ARM or
            ``(original_principal, annual_rate, term_months)`` for
            fixed-rate.  The legacy derivation uses ``annual_rate``
            as both the projection rate AND the contractual rate; for
            an ARM whose base rate diverges from the currently-
            applicable rate this produced the D-2 divergence where
            the binary-searched ``required_extra`` did not pair
            correctly with the loan card's ``monthly_payment``.

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

    # Derive the contractual payment when the caller did not supply
    # one.  When the caller supplies ``contractual_payment`` (the
    # production path), it is the SSOT value from the resolver and
    # the local derivation is bypassed.  ``projection_months`` still
    # widens for the fixed-rate-with-original-terms case so a
    # partially paid loan can finish before ``remaining_months``
    # without hitting the loop cap.
    has_rate_changes = rate_changes is not None and len(rate_changes) > 0
    using_contractual = (
        original_principal is not None
        and term_months is not None
        and not has_rate_changes
    )
    if contractual_payment is None:
        if using_contractual:
            contractual_payment = calculate_monthly_payment(
                original_principal, annual_rate, term_months,
            )
        else:
            contractual_payment = calculate_monthly_payment(
                current_principal, annual_rate, remaining_months,
            )
    # Coerce a caller-supplied value to Decimal in case it arrived as
    # a float-shaped DB column or string.
    else:
        contractual_payment = Decimal(str(contractual_payment))

    projection_months = (
        remaining_months + term_months
        if using_contractual
        else remaining_months
    )

    starting_date = advance_to_next_payment_date(
        origination_date, payment_day,
    )

    # All projections in this function share one starting state; only
    # ``extra_monthly`` varies between the standard run and each binary-
    # search iteration.  Building ``ProjectionInputs`` once guarantees
    # they cannot diverge in their shared inputs.
    projection_inputs = ProjectionInputs(
        starting_balance=current_principal,
        starting_date=starting_date,
        annual_rate=annual_rate,
        remaining_months=projection_months,
        payment_day=payment_day,
        contractual_payment=contractual_payment,
        rate_changes_remaining=rate_changes,
    )

    # Standard projection: no extra.  The contractual payment alone
    # determines the standard payoff date.
    standard = project_forward(
        projection_inputs, monthly_override=None, extra_monthly=Decimal("0.00"),
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
            projection_inputs, monthly_override=None, extra_monthly=mid,
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
