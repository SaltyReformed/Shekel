"""
Shekel Budget App -- Rate-Period Amortization Engine

Pure-function model of a loan as an ordered sequence of fixed-rate
periods.  No Flask, no ``db`` -- the caller loads the data and passes
plain values; every function returns plain data.

## Why this exists

A conventional monthly-accrual mortgage (and an ARM inside any one of
its fixed-rate periods) accrues interest as ``balance * annual_rate /
12`` and is repaid by a level payment that is fixed for the whole
period.  The level payment recasts ONLY at a rate adjustment, never
month to month, and never because the borrower prepaid or because a
displayed balance was corrected.  The earlier resolver modeled only
the first ARM window and, past it, re-amortized the payment every
month over a shrinking term -- the few-dollar "creep" the user
reported.  This module replaces that with the correct model:

1. A loan has fixed-rate periods bounded by ``origination`` then, for
   an ARM, ``origination + arm_first_adjustment_months`` and every
   ``arm_adjustment_interval_months`` after.  A fixed-rate loan is one
   period spanning the full term.
2. Each period carries the annual rate in effect at its start and a
   level P&I (principal + interest, NO escrow) held constant for the
   whole period.  The P&I is the recorded recast figure when the user
   supplied one (``RateHistory.monthly_pi``); otherwise it is derived
   by amortizing the period's contractual start balance over the term
   remaining at the period start.  Recording is required for a
   mid-life loan whose start balance is not reconstructable from the
   app's partial history -- the recorded value is on every statement.
3. The current balance is derived by :func:`replay_schedule`, which
   advances one scheduled step per confirmed payment from the latest
   anchor: ``principal = period_pi - interest``.  The cash amount and
   escrow NEVER enter the balance, so an escrow change cannot drift it.

The monetary rounding boundary is :func:`app.utils.money.round_money`
(2dp, ROUND_HALF_UP) -- the convention every hand-computed financial
test in this project assumes.
"""

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.amortization_engine import (
    AmortizationRow,
    calculate_monthly_payment,
)
from app.utils.money import MONTHS_PER_YEAR, round_money

ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class RatePeriod:
    """One fixed-rate period of a loan.

    Frozen because the period set is a derived snapshot consumers read
    and render; the immutability guarantees the same instance cannot be
    silently amended between the balance walk and the payment display.

    Attributes:
        index: 0-based position; period 0 always starts at origination.
        start_date: First calendar day the period's rate/payment apply.
            Period 0's ``start_date`` equals the loan's origination
            date.
        annual_rate: Annual interest rate in effect for the period (a
            decimal fraction, e.g. ``Decimal("0.06")``).
        period_pi: Level P&I held constant for the whole period -- the
            recorded recast figure when supplied, else the amortization
            of the period's contractual start balance over
            ``term_months_at_start``.  Display this as the loan's
            monthly P&I when ``as_of`` falls in this period.
        start_month_index: Whole calendar months from origination to
            ``start_date`` (period 0 is 0).
        term_months_at_start: Contractual months remaining at the
            period start (``original term - start_month_index``); the
            denominator the level payment amortizes over.
    """

    index: int
    start_date: date
    annual_rate: Decimal
    period_pi: Decimal
    start_month_index: int
    term_months_at_start: int


@dataclass(frozen=True)
class ScheduleReplay:
    """Result of replaying confirmed payments forward from an anchor.

    Mirrors the role of :class:`amortization_engine.ReplayResult` but
    derives each step's principal from the period's level P&I rather
    than from a cash amount.  The fields are exactly what a forward
    projection needs to pick up where the replay leaves off.

    Attributes:
        rows: One :class:`AmortizationRow` per confirmed payment
            consumed (``is_confirmed=True``), in payment-date order.
            Empty when no confirmed payment falls in
            ``(anchor_date, as_of]``.
        balance_as_of: Outstanding balance after the last consumed
            payment, quantized to cents.  Equals the anchor balance
            when no payment was consumed.
        next_pay_date: First payment date a forward projection should
            use -- the month after the last consumed payment, or the
            month after the anchor when none was consumed (day-clamped
            to ``payment_day``).
        remaining_months_as_of: Contractual months left before
            ``next_pay_date`` (``term - months consumed before it``),
            floored at 0.
        current_period: The :class:`RatePeriod` containing ``as_of`` --
            its ``annual_rate`` and ``period_pi`` are the loan's current
            rate and monthly P&I.
    """

    rows: list[AmortizationRow]
    balance_as_of: Decimal
    next_pay_date: date
    remaining_months_as_of: int
    current_period: RatePeriod


@dataclass(frozen=True)
class LoanTerms:
    """The immutable identity of a loan -- everything fixed at origination.

    Bundled so the period builder takes one cohesive value object rather
    than a long parameter list, and so the same identity threads through
    the resolver unchanged.  The mutable, dated facts (rate changes,
    recorded recasts, anchors, confirmed payments) are passed separately
    because they evolve over the life of the loan.

    Attributes:
        origination_date: Loan origination date (period 0 start).
        original_principal: Loan amount at origination; must be > 0.
        base_rate: Original annual rate (decimal fraction); the
            rate-lookup fallback when no rate change applies.
        term_months: Original term in months; must be > 0.
        is_arm: Whether the loan is adjustable-rate.
        arm_first_adjustment_months: Months from origination to the
            first rate adjustment (``None`` for a fixed-rate loan).
        arm_adjustment_interval_months: Months between subsequent
            adjustments (``None`` when only the first is modeled).
    """

    origination_date: date
    original_principal: Decimal
    base_rate: Decimal
    term_months: int
    is_arm: bool
    arm_first_adjustment_months: int | None
    arm_adjustment_interval_months: int | None


@dataclass(frozen=True)
class BalanceAnchor:
    """A dated balance assertion -- the starting point for a replay.

    The pure-data analogue of a ``LoanAnchorEvent``: the verified
    balance and the date it was verified always travel together, so
    they are one value object.

    Attributes:
        balance: Outstanding balance verified on ``as_of_date``.
        as_of_date: The date the balance was verified.  Confirmed
            payments at or before it are already reflected in
            ``balance`` and are not replayed again.
    """

    balance: Decimal
    as_of_date: date


def _months_between(start: date, end: date) -> int:
    """Return whole calendar months from ``start`` to ``end`` (day ignored).

    Matches :func:`amortization_engine.calculate_remaining_months`'
    convention: the delta between 2026-01-15 and 2027-01-01 is 12.
    Negative deltas are returned as-is; callers clamp where needed.
    """
    return (end.year - start.year) * 12 + (end.month - start.month)


def payment_number(origination_date: date, payment_date: date) -> int:
    """Return the scheduled-payment number (from origination) for a payment date.

    Payment N falls N whole calendar months after origination -- the first
    contractual payment, one month after origination, is payment 1.  Used
    to number the amortization schedule CONTINUOUSLY from origination so a
    mid-life loan's rows reflect total payments made (e.g. payment 90 for a
    loan in its 90th month) rather than the projected slice restarting at 1.

    This is the same figure the replay already stamps on each confirmed
    row's ``month``; exposing it lets the dashboard renumber the projected
    rows on the same scale.

    Args:
        origination_date: The loan's origination date.
        payment_date: The payment's date (true monthly due date).

    Returns:
        The 1-based payment number.  A payment dated in the origination
        month itself returns 0; callers display contractual schedules
        whose first row is one month after origination (payment 1).
    """
    return _months_between(origination_date, payment_date)


def _add_months(start: date, months: int) -> date:
    """Return ``start`` advanced by ``months`` calendar months, day-clamped.

    Clamps the day to the target month's length (2026-01-31 + 1 month
    is 2026-02-28).  Used to place period boundaries relative to the
    origination date.
    """
    target_month_zero = start.month - 1 + months
    target_year = start.year + target_month_zero // 12
    target_month = target_month_zero % 12 + 1
    last_day = calendar.monthrange(target_year, target_month)[1]
    return date(target_year, target_month, min(start.day, last_day))


def _advance_one_month(reference: date, payment_day: int) -> date:
    """Return the payment date in the month after ``reference``.

    Day-clamped to ``payment_day`` (or the month's last day).  Used to
    compute the projection's first payment date from the last replayed
    payment or the anchor.
    """
    month_zero = reference.month  # reference.month - 1 + 1 == next month, 0-based
    target_year = reference.year + month_zero // 12
    target_month = month_zero % 12 + 1
    last_day = calendar.monthrange(target_year, target_month)[1]
    return date(target_year, target_month, min(payment_day, last_day))


def monthly_due_date(period_start: date, payment_day: int) -> date:
    """Return a loan payment's true monthly due date from its pay-period start.

    A recurring loan payment is recorded against the pay period whose
    range contains its real monthly due date, but the resolver keys the
    :class:`~app.services.amortization_engine.PaymentRecord` to the
    pay-period START -- a biweekly date that can fall up to ~2 weeks
    before the contractual due date.  The pay-period start is too coarse
    for the anchor-boundary comparison ("did this payment come due after
    the balance was last verified?"): a balance true-up dated between a
    pay period's start and that period's payment due date would otherwise
    strand the payment in the gap, excluding it from the replay forever
    even after the user marks it paid.

    This recovers the contractual due date: the first ``payment_day`` of
    the month on or after ``period_start``, clamping ``payment_day`` to
    the month's length for short months (a ``payment_day`` of 31 resolves
    to Feb 28/29).  Because the payment's pay period was chosen to contain
    that due date, the first ``payment_day`` at or after the period start
    is exactly the due date.

    Args:
        period_start: The pay-period start date the PaymentRecord is keyed
            to (``PaymentRecord.payment_date``).
        payment_day: The loan's contractual day-of-month due day
            (``LoanParams.payment_day``), 1-31.

    Returns:
        The first date on or after ``period_start`` whose day equals
        ``payment_day`` (day-clamped to the month length).
    """
    last_day = calendar.monthrange(period_start.year, period_start.month)[1]
    candidate = date(
        period_start.year, period_start.month, min(payment_day, last_day),
    )
    if candidate >= period_start:
        return candidate
    # payment_day already passed in period_start's own month -- the due
    # date is the same day in the following month.
    return _advance_one_month(period_start, payment_day)


def _rate_at_date(
    rate_changes: list | None,
    target_date: date,
    base_rate: Decimal,
) -> Decimal:
    """Return the annual rate in effect on ``target_date``.

    The most recent rate change with ``effective_date <= target_date``,
    falling back to ``base_rate`` (the loan's original
    ``interest_rate``) when none qualifies.  Mirrors
    :func:`amortization_engine._find_applicable_rate` but operates on
    the unsorted public :class:`RateChangeRecord` list.

    Args:
        rate_changes: Optional :class:`RateChangeRecord` list (unsorted).
        target_date: Date to query.
        base_rate: Fallback when no rate change applies.

    Returns:
        The applicable annual rate as a Decimal.
    """
    if not rate_changes:
        return base_rate
    applicable = base_rate
    for change in sorted(rate_changes, key=lambda r: r.effective_date):
        if change.effective_date <= target_date:
            applicable = change.interest_rate
        else:
            break
    return applicable


def _recorded_pi_at_date(
    recorded_period_pi: dict[date, Decimal] | None,
    target_date: date,
) -> Decimal | None:
    """Return the recorded recast P&I governing ``target_date``, or None.

    ``recorded_period_pi`` maps a recast effective date to the lender's
    stated P&I (from ``RateHistory.monthly_pi``).  Returns the value
    for the most recent key ``<= target_date`` so a recast recorded a
    few days off the computed period boundary still governs the period;
    ``None`` when no recorded recast applies (the period's P&I is then
    derived).

    Args:
        recorded_period_pi: Optional ``{effective_date: monthly_pi}``.
        target_date: The period start date being resolved.

    Returns:
        The governing recorded P&I, or ``None``.
    """
    if not recorded_period_pi:
        return None
    applicable: Decimal | None = None
    for effective_date in sorted(recorded_period_pi):
        if effective_date <= target_date:
            applicable = recorded_period_pi[effective_date]
        else:
            break
    return applicable


def _amortize_forward(
    balance: Decimal,
    annual_rate: Decimal,
    level_payment: Decimal,
    months: int,
) -> Decimal:
    """Walk ``months`` of level-payment amortization and return the balance.

    Pure contractual walk used to find a period's start balance from
    the prior period's start balance (so an unrecorded recast P&I can
    be derived).  Each month: ``interest = round_money(balance *
    rate/12)``, ``principal = level_payment - interest``, ``balance =
    round_money(balance - principal)``, floored at zero.  A zero rate
    accrues no interest.

    Args:
        balance: Starting balance.
        annual_rate: Annual rate (decimal fraction).
        level_payment: The period's level P&I.
        months: Number of months to walk (``<= 0`` returns ``balance``).

    Returns:
        The balance after ``months`` payments, quantized to cents.
    """
    monthly_rate = annual_rate / MONTHS_PER_YEAR if annual_rate > 0 else ZERO_MONEY
    for _ in range(max(0, months)):
        if balance <= 0:
            return ZERO_MONEY
        interest = round_money(balance * monthly_rate)
        principal = level_payment - interest
        if principal >= balance:
            return ZERO_MONEY
        balance = round_money(balance - principal)
        if balance < 0:
            return ZERO_MONEY
    return round_money(balance)


def _period_boundary_dates(
    terms: "LoanTerms",
    rate_changes: list | None,
) -> list[date]:
    """Return the dates (ascending) where a fixed-rate period starts.

    Always begins with the origination date.  Two sources add further
    boundaries:

    * **Recorded rate changes.**  A rate change recasts the payment, so
      each :class:`RateChangeRecord` whose ``effective_date`` is strictly
      after origination and before the term end begins a new period.
      This is what makes a loan whose rate history is recorded -- but
      whose ARM cadence columns are unset -- recast correctly at the
      adjustment instead of staying frozen at the origination payment.
    * **ARM cadence.**  For an ARM, ``arm_first_adjustment_months`` and
      ``arm_adjustment_interval_months`` project FUTURE boundaries past
      the last recorded rate change (where no rate-change row exists
      yet), so the forward schedule still recasts on schedule.

    A fixed-rate loan with no rate changes is a single period spanning
    the full term.  Boundaries are deduplicated, so a rate change that
    coincides with a cadence adjustment date counts once.

    Args:
        terms: The loan's :class:`LoanTerms`.
        rate_changes: Optional :class:`RateChangeRecord` list; each
            change's ``effective_date`` (after origination, within term)
            begins a period.

    Returns:
        Ascending, deduplicated list of boundary dates beginning with
        the origination date.
    """
    origination = terms.origination_date
    term_end = _add_months(origination, terms.term_months)
    boundaries = {origination}
    for change in (rate_changes or []):
        if origination < change.effective_date < term_end:
            boundaries.add(change.effective_date)
    first = terms.arm_first_adjustment_months
    if terms.is_arm and first is not None and 0 < first < terms.term_months:
        interval = terms.arm_adjustment_interval_months
        offset = first
        while offset < terms.term_months:
            boundaries.add(_add_months(origination, offset))
            if interval is None or interval <= 0:
                break
            offset += interval
    return sorted(boundaries)


def build_rate_periods(
    *,
    terms: LoanTerms,
    rate_changes: list | None,
    recorded_period_pi: dict[date, Decimal] | None,
) -> list[RatePeriod]:
    """Build the ordered fixed-rate periods spanning the whole loan term.

    Period boundaries come from the recorded rate changes (each recast
    is a boundary) and, for an ARM, the cadence
    (``arm_first_adjustment_months`` + ``arm_adjustment_interval_months``)
    for future boundaries -- see :func:`_period_boundary_dates`.  Each
    period's rate is sampled at its start via :func:`_rate_at_date`.
    Each period's level P&I is the
    recorded recast (:func:`_recorded_pi_at_date`) when supplied, else
    the amortization of the period's contractual start balance over the
    term remaining at the start.  Start balances are produced by walking
    the contractual schedule forward from ``original_principal`` period
    by period (:func:`_amortize_forward`), so a derived recast matches
    what a lender would set for an on-schedule loan; the recorded value
    overrides it where the contractual assumption does not hold (a
    mid-life loan, a prepayment).

    Pure: no I/O, deterministic for a given input tuple.

    Args:
        terms: The loan's immutable :class:`LoanTerms` (origination
            date, original principal, base rate, term, and ARM cadence).
        rate_changes: Optional :class:`RateChangeRecord` list giving the
            rate per period.
        recorded_period_pi: Optional ``{effective_date: monthly_pi}`` of
            lender-recorded recast payments.

    Returns:
        A non-empty list of :class:`RatePeriod` in start-date order.
    """
    boundaries = _period_boundary_dates(terms, rate_changes)
    periods: list[RatePeriod] = []
    balance = Decimal(str(terms.original_principal))
    for index, start_date in enumerate(boundaries):
        annual_rate = _rate_at_date(rate_changes, start_date, terms.base_rate)
        start_month_index = _months_between(terms.origination_date, start_date)
        term_at_start = terms.term_months - start_month_index
        recorded = _recorded_pi_at_date(recorded_period_pi, start_date)
        if recorded is not None:
            period_pi = round_money(Decimal(str(recorded)))
        else:
            period_pi = round_money(
                calculate_monthly_payment(balance, annual_rate, term_at_start)
            )
        periods.append(RatePeriod(
            index=index,
            start_date=start_date,
            annual_rate=annual_rate,
            period_pi=period_pi,
            start_month_index=start_month_index,
            term_months_at_start=term_at_start,
        ))
        # Advance the contractual balance to the next period start so the
        # next period's derived recast amortizes the right remaining
        # balance.  Skipped after the final period.
        if index + 1 < len(boundaries):
            months_in_period = _months_between(
                start_date, boundaries[index + 1],
            )
            balance = _amortize_forward(
                balance, annual_rate, period_pi, months_in_period,
            )
    return periods


def period_for_date(periods: list[RatePeriod], target: date) -> RatePeriod:
    """Return the period whose half-open span contains ``target``.

    The latest period with ``start_date <= target``; the first period
    when ``target`` precedes it (a payment dated before origination is
    governed by the origination period).

    Args:
        periods: Non-empty list from :func:`build_rate_periods`.
        target: The date to locate.

    Returns:
        The governing :class:`RatePeriod`.

    Raises:
        ValueError: If ``periods`` is empty.
    """
    if not periods:
        raise ValueError("period_for_date requires a non-empty period list.")
    chosen = periods[0]
    for period in periods:
        if period.start_date <= target:
            chosen = period
        else:
            break
    return chosen


def _replay_payment_row(
    balance: Decimal,
    period: RatePeriod,
    pay_date: date,
    origination_date: date,
) -> AmortizationRow:
    """Apply one scheduled payment and return its row (carrying the new balance).

    Interest accrues on ``balance`` at the period rate; principal is
    ``period_pi - interest``.  A principal that would overrun the
    balance is capped so the loan closes exactly at zero.  The returned
    row's ``remaining_balance`` is the post-payment balance the caller
    carries forward.

    Args:
        balance: Outstanding balance before this payment (> 0).
        period: The :class:`RatePeriod` governing ``pay_date``.
        pay_date: The payment's date.
        origination_date: Loan origination, for the row's month index.

    Returns:
        The :class:`AmortizationRow` for this confirmed payment.
    """
    if period.annual_rate > 0:
        interest = round_money(balance * (period.annual_rate / MONTHS_PER_YEAR))
    else:
        interest = ZERO_MONEY
    principal = period.period_pi - interest
    if principal >= balance:
        principal = balance
        payment = principal + interest
        new_balance = ZERO_MONEY
    else:
        payment = period.period_pi
        new_balance = round_money(balance - principal)
        if new_balance < 0:
            new_balance = ZERO_MONEY
    return AmortizationRow(
        month=_months_between(origination_date, pay_date),
        payment_date=pay_date,
        payment=round_money(payment),
        principal=round_money(principal),
        interest=interest,
        extra_payment=ZERO_MONEY,
        remaining_balance=new_balance,
        is_confirmed=True,
        interest_rate=period.annual_rate,
    )


def replay_schedule(
    *,
    periods: list[RatePeriod],
    anchor: BalanceAnchor,
    confirmed_payment_dates: list[date],
    payment_day: int,
    as_of: date,
) -> ScheduleReplay:
    """Replay confirmed payments forward from the anchor along the schedule.

    Advances one scheduled step per confirmed payment that clears two
    eligibility boundaries, in due-date order (see
    :func:`_replay_payment_row` for the per-step math):

    * its true monthly due date (see :func:`monthly_due_date`) is strictly
      after ``anchor.as_of_date`` -- the payment came due after the
      balance was last verified, so it is not already baked into the
      anchor; and
    * its pay-period start is at or before ``as_of`` -- its pay period has
      begun, so it is historical rather than a forward projection.

    The cash amount and escrow are NOT inputs -- only the COUNT and dates
    of the confirmed payments matter, so a payment that bundled escrow
    cannot over-reduce principal.

    Three dates with distinct jobs:

    * The RATE (and therefore the interest/principal split and the running
      balance) is selected by the pay-period start, so this function's
      ``balance_as_of`` is independent of the dating choices below.
    * Each replayed ROW is dated by the true monthly due date, so the
      schedule shows real statement dates and ``next_pay_date`` advances to
      the correct following month (a pay-period-start dating would print
      the biweekly date and land the projection one month early).
    * The as_of cap uses the pay-period start (the replay-vs-projection
      split), matching :func:`_build_monthly_override`.

    Using the due date for the anchor boundary is what lets a true-up dated
    mid-pay-period (one day after a period's biweekly start but before that
    period's monthly payment is due) still replay that payment.

    Args:
        periods: Non-empty list from :func:`build_rate_periods`.
            ``periods[0].start_date`` is the origination date and
            ``periods[0].term_months_at_start`` is the original term.
        anchor: The :class:`BalanceAnchor` to start from (the latest
            ``LoanAnchorEvent``).  Payments whose due date is at or before
            its date are already reflected in its balance and are skipped.
        confirmed_payment_dates: Pay-period-start dates of confirmed
            (settled) payments.  Kept when the true monthly due date is
            after ``anchor.as_of_date`` and the pay-period start is at or
            before ``as_of``.
        payment_day: Day of month payments are due.  Drives the due-date
            classification, the replayed row dates, and ``next_pay_date``.
        as_of: Evaluation date; payments whose pay period has not begun by
            it are not replayed.

    Returns:
        A :class:`ScheduleReplay` with the consumed rows, the balance as
        of ``as_of``, the next projection date, the remaining months,
        and the current period.

    Raises:
        ValueError: If ``periods`` is empty.
    """
    if not periods:
        raise ValueError("replay_schedule requires a non-empty period list.")

    origination_date = periods[0].start_date
    # Two different dates govern the two eligibility boundaries:
    #   * Anchor (lower) boundary -- the true monthly DUE date.  A payment
    #     due after the anchor but whose biweekly pay period started on or
    #     before it must still be replayed; comparing the pay-period start
    #     here would strand it (the mid-period-true-up bug).
    #   * as_of (upper) cap -- the PAY-PERIOD START.  This is the
    #     replay-vs-projection split: a confirmed payment whose pay period
    #     has begun is historical, even if pre-paid a few days before its
    #     due date.  ``_build_monthly_override`` uses the same pay-period
    #     start so the two partitions stay exact complements.
    eligible = sorted(
        d for d in confirmed_payment_dates
        if anchor.as_of_date < monthly_due_date(d, payment_day)
        and d <= as_of
    )

    balance = Decimal(str(anchor.balance))
    rows: list[AmortizationRow] = []
    for period_start in eligible:
        if balance <= 0:
            break
        # Rate (and therefore the interest/principal split and the running
        # balance) is selected by the pay-period start, so the replayed
        # balance is unchanged by this dating.  The ROW is dated by the
        # true monthly due date, so the schedule shows the real statement
        # date and ``next_pay_date`` advances to the correct following
        # month rather than landing one month early.
        period = period_for_date(periods, period_start)
        due_date = monthly_due_date(period_start, payment_day)
        row = _replay_payment_row(balance, period, due_date, origination_date)
        balance = row.remaining_balance
        rows.append(row)

    if rows:
        next_pay_date = _advance_one_month(rows[-1].payment_date, payment_day)
    else:
        next_pay_date = _advance_one_month(anchor.as_of_date, payment_day)

    remaining_months_as_of = max(
        0,
        periods[0].term_months_at_start
        - max(0, _months_between(origination_date, next_pay_date) - 1),
    )

    return ScheduleReplay(
        rows=rows,
        balance_as_of=round_money(balance),
        next_pay_date=next_pay_date,
        remaining_months_as_of=remaining_months_as_of,
        current_period=period_for_date(periods, as_of),
    )
