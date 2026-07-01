"""Amortization projection primitives.

The forward half of the amortization engine: the value records
(:class:`PaymentRecord`, :class:`RateChangeRecord`,
:class:`PeriodTerms`, :class:`AmortizationRow`,
:class:`AmortizationSummary`, :class:`ProjectionInputs`), the standard
payment formula, the date helpers, and :func:`project_forward` itself.
Pure functions, no database access -- operates only on values passed in.

Per-month rate AND contractual P&I come from the projection's
``terms_schedule`` (:class:`PeriodTerms` entries, mapped 1:1 from the
rate-period engine's :class:`~app.services.rate_period_engine.RatePeriod`
set by the loan resolver), so the schedule's projected rows read the
SAME single source of truth the loan card displays -- a recorded recast
(``RateHistory.monthly_pi``) or the schedule-derived level payment.
The projection never re-amortizes a payment from its own what-if
balance (DH-#1: that re-derivation made the schedule diverge from the
card for any recast the replay had not yet consumed).

The payoff question layer (:mod:`._payoff`) consumes these primitives;
both are re-exported flat from the package ``__init__`` so consumers
keep importing from ``app.services.amortization_engine`` unchanged.
"""

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.utils.dates import months_between
from app.utils.money import (
    MONTHS_PER_YEAR,
    accrue_monthly_interest,
    round_money,
)


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

    The transport record for a loan's rate history (one per
    ``RateHistory`` row): the rate-period engine consumes the list to
    place period boundaries and resolve each period's rate and recorded
    P&I (:func:`~app.services.rate_period_engine.build_rate_periods`),
    and the resulting periods feed projections as
    :class:`PeriodTerms`.  The projection itself no longer reads rate
    changes directly.

    Attributes:
        effective_date: The date the new rate takes effect.
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
    months_elapsed = months_between(origination_date, as_of)
    return max(0, term_months - months_elapsed)


@dataclass
class AmortizationRow:  # pylint: disable=too-many-instance-attributes
    """A single month in an amortization schedule.

    The is_confirmed flag distinguishes historical fact from projection:
    True when the row's payment came from a confirmed PaymentRecord
    (Paid/Settled status), False when projected or computed from the
    contractual payment formula.

    Pylint: ``too-many-instance-attributes`` (9/7) -- suppressed
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
        return round_money(principal / remaining_months)

    monthly_rate = annual_rate / MONTHS_PER_YEAR
    factor = (1 + monthly_rate) ** remaining_months
    payment = principal * (monthly_rate * factor) / (factor - 1)
    return round_money(payment)


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


@dataclass(frozen=True)
class PeriodTerms:
    """The contractual terms governing one fixed-rate span of a projection.

    The projection-side mirror of
    :class:`app.services.rate_period_engine.RatePeriod`: the rate-period
    engine is the single producer of each period's rate AND level P&I
    (the recorded recast when the user supplied one, else the
    schedule-derived amortization), and the loan resolver maps every
    ``RatePeriod`` onto one ``PeriodTerms`` -- so :func:`project_forward`
    pays exactly the figures the loan card displays and the two cannot
    diverge (DH-#1).  Defined here rather than imported from the
    rate-period engine because that module already imports this package
    (the dependency points producer -> primitives).

    Attributes:
        start_date: First calendar day this entry's rate/payment govern.
            The entry governing a payment date is the LATEST one with
            ``start_date <= payment_date``; a date before every entry is
            governed by the first (mirroring
            :func:`~app.services.rate_period_engine.period_for_date`).
        annual_rate: Annual interest rate for the span (a decimal
            fraction, e.g. ``Decimal("0.06")``).  Must be >= 0.
        monthly_pi: Level P&I (principal + interest, no escrow) held
            constant for the span.  Must be >= 0 (zero only for the
            degenerate already-paid-off span).
    """

    start_date: date
    annual_rate: Decimal
    monthly_pi: Decimal

    def __post_init__(self):
        """Validate terms fields at construction time.

        Catches invalid data immediately rather than producing wrong
        results deep in the projection loop.

        Raises:
            TypeError: If ``start_date`` is not a date or either money
                field is not a Decimal.
            ValueError: If ``annual_rate`` or ``monthly_pi`` is negative.
        """
        if not isinstance(self.start_date, date):
            raise TypeError(
                f"start_date must be a date, "
                f"got {type(self.start_date).__name__}"
            )
        if not isinstance(self.annual_rate, Decimal):
            raise TypeError(
                f"annual_rate must be a Decimal, "
                f"got {type(self.annual_rate).__name__}"
            )
        if self.annual_rate < 0:
            raise ValueError(
                f"annual_rate must be >= 0, got {self.annual_rate}"
            )
        if not isinstance(self.monthly_pi, Decimal):
            raise TypeError(
                f"monthly_pi must be a Decimal, "
                f"got {type(self.monthly_pi).__name__}"
            )
        if self.monthly_pi < 0:
            raise ValueError(
                f"monthly_pi must be >= 0, got {self.monthly_pi}"
            )


def _governing_terms(
    sorted_terms: list[PeriodTerms],
    payment_date: date,
) -> PeriodTerms:
    """Return the terms entry governing ``payment_date``.

    The latest entry with ``start_date <= payment_date``; the first
    entry when ``payment_date`` precedes them all.  Mirrors
    :func:`app.services.rate_period_engine.period_for_date` so the
    projection selects terms exactly the way the loan card does.

    Args:
        sorted_terms: Non-empty list of :class:`PeriodTerms` in
            ``start_date`` order.
        payment_date: The payment date for the current schedule month.

    Returns:
        The governing :class:`PeriodTerms`.
    """
    chosen = sorted_terms[0]
    for terms in sorted_terms:
        if terms.start_date <= payment_date:
            chosen = terms
        else:
            break
    return chosen


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
        remaining_months: Maximum number of months to project.  ``<= 0``
            yields an empty projection.
        payment_day: Day-of-month payments are due, clamped to each
            month's max days (e.g. day 31 in February).
        terms_schedule: Non-empty :class:`PeriodTerms` list -- the
            single source of each month's rate AND contractual P&I
            (see :func:`_governing_terms` for the selection rule).
            The resolver maps the loan's full
            :class:`~app.services.rate_period_engine.RatePeriod` set
            here, past periods included, so a ``starting_date`` behind
            ``as_of`` (a stale anchor) is still governed by its true
            period; entries need not be pre-sorted.
    """

    starting_balance: Decimal
    starting_date: date
    remaining_months: int
    payment_day: int
    terms_schedule: list[PeriodTerms]

    def __post_init__(self):
        """Reject an empty terms schedule at construction time.

        A projection with no terms has no payment or rate to apply --
        a caller bug that must surface loudly here, not as a wrong
        schedule downstream.

        Raises:
            ValueError: If ``terms_schedule`` is empty.
        """
        if not self.terms_schedule:
            raise ValueError(
                "terms_schedule must contain at least one PeriodTerms "
                "entry -- a projection has no payment or rate without one."
            )


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
    # principal_portion < balance here (the >= case returned above), so
    # balance - principal_portion is strictly positive and round_money
    # (ROUND_HALF_UP) cannot yield a negative -- no clamp is needed.
    new_balance = round_money(balance - principal_portion)
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
    to ``[0, balance - principal_portion]`` so acceleration alone cannot
    drive the balance below zero -- which also guarantees the quantized
    new balance is non-negative without a clamp.

    Args:
        balance: Outstanding balance before this month's payment.
        interest: Period interest already computed for this month.
        monthly_payment: The month's contractual P&I (the governing
            terms entry's ``monthly_pi``).
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
    # extra is clamped to [0, balance - principal_portion] above, so
    # balance - principal_portion - extra is non-negative and round_money
    # (ROUND_HALF_UP) cannot yield a negative -- no clamp/fold is needed.
    new_balance = round_money(balance - principal_portion - extra)
    return principal_portion, monthly_payment, extra, new_balance


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
    forward-only parameters (override, extra, the rate/P&I terms
    schedule).

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
        month, the payment is the governing terms entry's
        ``monthly_pi`` plus ``extra_monthly``.  Extra is clamped so it
        cannot push the balance below zero.  The final scheduled month
        absorbs whatever residue remains after the contractual P&I
        split, regardless of extra.

    ARM behavior: each month's rate and contractual P&I come from the
    governing ``terms_schedule`` entry (:func:`_governing_terms`), so
    the payment recasts exactly where the rate-period engine says it
    does and TO the value the rate-period engine says -- the recorded
    recast (``RateHistory.monthly_pi``) when the user supplied one,
    else the schedule-derived level payment.  The projection NEVER
    re-amortizes a payment from its own what-if balance: the
    contractual P&I is a property of the loan's rate-period structure,
    not of the balance path (the E-02 invariant -- the same reason a
    balance true-up does not move the displayed payment), and the
    prior balance-reactive re-derivation is what let the schedule
    diverge from the loan card (DH-#1).

    Every row carries ``is_confirmed=False`` because projection rows
    are not facts about the recorded past, and ``interest_rate`` is
    the governing entry's rate (the replay stamps its rows the same
    way, so schedule consumers see the rate populated consistently).

    Args:
        inputs: A :class:`ProjectionInputs` bundling the starting state
            (balance, date) and the forward-only terms (remaining
            months, payment day, the rate/P&I terms schedule) that stay
            constant across a related set of projections.  See
            :class:`ProjectionInputs` for per-field semantics.
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
        Nothing.  ``PeriodTerms`` field validation and the non-empty
        ``terms_schedule`` guard happen at dataclass construction in
        the caller (:class:`ProjectionInputs` raises ``ValueError`` on
        an empty feed); this function trusts its inputs (per CLAUDE.md
        "Trust internal code and framework guarantees").
    """
    if inputs.starting_balance <= 0 or inputs.remaining_months <= 0:
        return []

    overrides = {} if monthly_override is None else monthly_override
    # One sort up front; _governing_terms scans in start-date order.
    terms_schedule = sorted(
        inputs.terms_schedule, key=lambda terms: terms.start_date,
    )

    # Defensive: coerce to Decimal even if the caller passes a value
    # whose representation could yield float-like surprises.
    balance = Decimal(str(inputs.starting_balance))

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
        if balance <= 0:
            break

        # Rate AND contractual P&I from the governing terms -- the
        # rate-period engine's figures, never re-derived from the
        # projection's own balance (DH-#1 / E-02).
        terms = _governing_terms(terms_schedule, pay_date)
        interest = accrue_monthly_interest(balance, terms.annual_rate)
        month_key = (pay_date.year, pay_date.month)

        if month_key in overrides:
            # Override path: the override amount IS the total payment for
            # the month; extra_monthly is NOT added (override months must
            # never carry extra -- the plan's regression-prevention
            # property).
            principal_portion, actual_payment, balance = (
                _apply_override_payment(
                    balance, interest, overrides[month_key],
                )
            )
            extra = Decimal("0.00")
        else:
            # No override: contractual + extra path.  extra_monthly is
            # applied here and only here; the final scheduled month
            # absorbs the residue regardless of extra.
            principal_portion, actual_payment, extra, balance = (
                _apply_contractual_payment(
                    balance, interest, terms.monthly_pi,
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
            remaining_balance=balance,
            is_confirmed=False,
            interest_rate=terms.annual_rate,
        ))

        if balance <= 0:
            break

        # Advance to the next month's payment date (day re-clamped from
        # the original payment_day, not the prior clamped day).
        pay_date = _advance_month(
            pay_date.year, pay_date.month, inputs.payment_day,
        )

    return rows
