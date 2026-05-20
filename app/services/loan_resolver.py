"""
Shekel Budget App -- Loan Resolver (E-18 canonical loan producer)

Single source of truth for "this loan's current balance, monthly
payment, schedule, payoff date, and life-of-loan interest."  Every
loan-touching surface (dashboard card, /savings debt card, net-worth
liability, debt strategy, year-end summary) reads from here so the
same loan on the same day cannot show three different numbers.

Pure function, no Flask, no `db.session` reads or writes.  The caller
loads the data and passes it in; the resolver returns plain data.

## What this fixes

Pre-E-18, sixteen sites assembled their own ``(principal, rate, n)``
triple.  Two failure modes appeared on the displayed cards:

* **Symptom #3 -- frozen principal.** ``LoanParams.current_principal``
  had no settle-driven writer (`grep proved zero attribute writes`),
  so confirmed PITI transfers never reduced it.  Until a user manually
  edited the field, the card stayed at the originally-entered value.

* **Symptom #4 -- ARM fixed-window payment creep.** The ARM scalar
  site (`amortization_engine.py:950-954` pre-fix) re-amortized the
  frozen stored principal over a calendar-shrinking
  ``calculate_remaining_months`` count.  The displayed P&I drifted a
  few dollars upward every month inside the supposedly fixed-rate
  window (hand-recomputed: $2,460.45 at month 24 to $2,463.28 at
  month 25 for a 5/5 ARM at $400k/6%/360mo, both above the correct
  constant $2,398.20).  See ``docs/audits/financial_calculations/
  05_symptoms.md`` Symptom #4 for the worked example.

The resolver collapses both onto a single derivation:

1. Pick the latest ``LoanAnchorEvent`` (Commit 12 guarantees every
   loan has at least one -- the origination event).
2. Replay only ``is_confirmed`` payments whose ``payment_date`` is
   strictly after the anchor date.  Projected (unconfirmed)
   payments do not reduce the balance -- they are future
   commitments, not historical fact.
3. For an ARM whose anchor and as_of both fall inside
   ``[origination_date, origination_date + arm_first_adjustment_months)``
   (the fixed-rate window), compute the monthly payment ONCE from
   the anchor balance over the remaining contractual term as of the
   anchor date, and hold it constant for every ``as_of`` inside the
   window.  This is the E-02 fixed-window invariant.  A subsequent
   ``user_trueup`` anchor inside the window produces a new constant
   (the trueup IS the moment a new constant is born).
4. Outside the fixed-rate window (or for any non-ARM loan that is
   not yet paid off), amortize the current balance at the rate in
   effect for ``as_of`` over the remaining months.
5. Use ``round_money`` as the only rounding boundary in this module.

## What the resolver is NOT

* Not a query layer.  Callers load ``LoanAnchorEvent`` rows,
  ``PaymentRecord`` instances, and ``RateChangeRecord`` instances
  themselves (typically via ``loan_payment_service.load_loan_context``
  for the payment + rate-change feeds, and a direct query for the
  anchor events).
* Not a payment preparation step.  ``payments`` is expected to be
  the already-prepared list from
  ``loan_payment_service.prepare_payments_for_engine`` (escrow
  subtracted, biweekly redistributed).  Passing raw shadow-income
  payments will misalign principal/interest splits.
* Not a writer.  The resolver never inserts or updates anything;
  Commit 16 owns the trueup write path via ``anchor_service``.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.amortization_engine import (
    AmortizationRow,
    PaymentRecord,
    RateChangeRecord,
    calculate_monthly_payment,
    calculate_remaining_months,
    generate_schedule,
)
from app.utils.money import round_money

ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class LoanState:
    """Resolved loan state for a single ``as_of`` evaluation.

    Frozen because the resolver returns a snapshot the caller must
    not mutate.  Every consumer (loan dashboard card, /savings debt
    card, net-worth liability, debt-strategy, year-end summary) reads
    these five fields and renders them; the immutability guarantees
    the same instance cannot be silently amended between consumers.

    Attributes:
        current_balance: Loan balance after replaying confirmed
            payments forward from the latest anchor.  Display this
            instead of ``LoanParams.current_principal``.
        monthly_payment: P&I payment as of ``as_of``.  For an ARM
            inside its fixed-rate window this is held constant for
            every ``as_of`` in the window (E-02 invariant).  Outside
            the window or for a fixed-rate loan this is the
            contractual / re-amortized payment per the resolver
            algorithm in the module docstring.
        schedule: Full amortization schedule, with confirmed rows
            reflecting actual paid amounts and projected rows using
            the engine's contractual / re-amortized projections.
            Generated once via the amortization engine; consumers
            read it without recomputing.
        payoff_date: Last ``payment_date`` in ``schedule`` (the
            month the loan reaches zero).  ``origination_date`` when
            the schedule is empty (zero balance / zero remaining
            months).
        total_interest: Sum of ``row.interest`` across the schedule
            (life-of-loan total).  ``Decimal("0.00")`` when the
            schedule is empty.
    """

    current_balance: Decimal
    monthly_payment: Decimal
    schedule: list[AmortizationRow]
    payoff_date: date
    total_interest: Decimal


def _select_latest_anchor(anchor_events: list) -> object:
    """Return the most recent anchor event by (anchor_date, created_at) DESC.

    Mirrors the ORM ``backref(order_by=...)`` on
    :class:`LoanAnchorEvent`: ``anchor_date DESC, created_at DESC``.
    A loan can carry multiple events on the same day (origination
    plus a later trueup); ``created_at`` is the deterministic
    tie-breaker so the same anchor list always selects the same
    event.

    Args:
        anchor_events: Non-empty list of LoanAnchorEvent-shaped
            objects with ``anchor_date`` (date) and ``created_at``
            (datetime) attributes.

    Returns:
        The single most recent event.

    Raises:
        ValueError: If ``anchor_events`` is empty.  Commit 12's
            origination backfill guarantees at least one event per
            loan; an empty list signals a data invariant violation
            the caller must surface, not silently paper over.
    """
    if not anchor_events:
        raise ValueError(
            "loan_resolver requires at least one LoanAnchorEvent; "
            "Commit 12 backfill should have produced an origination "
            "event for every loan -- received an empty list."
        )
    return max(
        anchor_events,
        key=lambda event: (event.anchor_date, event.created_at),
    )


def _months_between(start: date, end: date) -> int:
    """Return the integer month delta from ``start`` to ``end``.

    Calendar-month arithmetic only (day-of-month is ignored): the
    delta between 2026-01-15 and 2027-01-01 is 12, matching the
    convention in :func:`amortization_engine.calculate_remaining_months`.

    Negative deltas are returned as-is (callers may want a signed
    value); the public ``calculate_remaining_months`` clamps to zero
    where appropriate, so the resolver lets the engine clamp at the
    final boundary rather than double-clamping here.

    Args:
        start: The earlier date (typically ``origination_date``).
        end: The later date (typically the anchor date or as_of).

    Returns:
        Integer count of calendar-month boundaries crossed.
    """
    return (end.year - start.year) * 12 + (end.month - start.month)


def _add_months_to_date(start: date, months: int) -> date:
    """Return ``start`` advanced by ``months`` calendar months.

    Day-clamps to the last day of the target month when the source
    day exceeds the target month's length (e.g. 2026-01-31 + 1 month
    is 2026-02-28).  Used to compute the fixed-rate-window end date
    from ``origination_date`` + ``arm_first_adjustment_months``.

    Args:
        start: The starting date.
        months: Non-negative integer month count.

    Returns:
        The clamped end-of-window date.
    """
    target_month = start.month + months
    target_year = start.year + (target_month - 1) // 12
    target_month = ((target_month - 1) % 12) + 1
    # Day-clamp via calendar.monthrange is the standard Python idiom;
    # using min() against a precomputed last-day avoids an extra
    # import here because the resolver does not otherwise need the
    # ``calendar`` module.
    last_day_lookup = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    last_day = last_day_lookup[target_month - 1]
    # February 29 only exists in leap years; if the start day is
    # March 31 etc and the target is February, clamp to 28 in
    # non-leap years.  Using the standard library would be cleaner,
    # but the lookup-plus-leap-check is self-contained and avoids
    # an otherwise-unused import.
    if target_month == 2:
        is_leap = (
            target_year % 4 == 0
            and (target_year % 100 != 0 or target_year % 400 == 0)
        )
        last_day = 29 if is_leap else 28
    return date(target_year, target_month, min(start.day, last_day))


def _rate_at_date(
    rate_changes: list[RateChangeRecord] | None,
    target_date: date,
    base_rate: Decimal,
) -> Decimal:
    """Return the annual rate in effect on ``target_date``.

    Scans ``rate_changes`` for the most recent entry with
    ``effective_date <= target_date`` and returns that rate.  Falls
    back to ``base_rate`` (the loan's original ``interest_rate``)
    when no qualifying entry exists.

    Conceptually duplicates :func:`amortization_engine._find_applicable_rate`
    but operates on the unsorted public input list rather than the
    pre-sorted internal tuple list, so the helpers stay decoupled
    (the engine's helper is private and consumes a structure built
    by another private helper).

    Args:
        rate_changes: Optional list of RateChangeRecord instances,
            unsorted.  ``None`` or an empty list bypasses the lookup.
        target_date: The date to query for the applicable rate.
        base_rate: The loan's original annual rate, returned when
            no rate change applies.

    Returns:
        The applicable annual interest rate as a Decimal.
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


def _is_in_arm_fixed_window(
    loan_params,
    anchor_date: date,
    as_of: date,
) -> bool:
    """Return True when BOTH the anchor and as_of fall inside the ARM window.

    The fixed-rate window for a 5/1, 5/5, 7/1, 10/1 ARM (etc) is
    ``[origination_date, origination_date + arm_first_adjustment_months)``.
    Inside this window the contractual payment is constant.  E-02
    requires the resolver-derived monthly payment to honor this
    invariant byte-identically for every ``as_of`` in the window.

    Both conditions must hold:

    * **Anchor in window.**  The anchor balance is the
      remaining-balance basis the constant payment is computed from.
      A trueup outside the window resets the calculation to the
      "amortize current balance" branch (post-adjustment behavior).
    * **as_of in window.**  Outside the window the payment is
      expected to re-amortize at the adjusted rate; honoring the
      in-window constant past the window-end would silently overlay
      the wrong rate.

    Args:
        loan_params: An object exposing ``is_arm`` and
            ``arm_first_adjustment_months`` attributes.  A non-ARM
            loan (or one with ``arm_first_adjustment_months is None``)
            is never in a fixed-rate window.
        anchor_date: The latest LoanAnchorEvent's anchor_date.
        as_of: The evaluation date.

    Returns:
        True if both dates fall in the half-open
        ``[origination, origination + window_months)`` interval.
    """
    is_arm = bool(getattr(loan_params, "is_arm", False))
    if not is_arm:
        return False
    window_months = getattr(loan_params, "arm_first_adjustment_months", None)
    if window_months is None or window_months <= 0:
        return False
    window_end = _add_months_to_date(
        loan_params.origination_date, window_months,
    )
    return anchor_date < window_end and as_of < window_end


def _replay_balance_from_anchor(
    anchor_balance: Decimal,
    confirmed_after_anchor: list[PaymentRecord],
    rate_changes: list[RateChangeRecord] | None,
    base_rate: Decimal,
    as_of: date,
) -> Decimal:
    """Replay confirmed post-anchor payments to derive the as_of balance.

    Direct implementation of the resolver's balance-derivation
    invariant: the current balance is the anchor balance reduced by
    the principal portion of every confirmed payment whose
    ``payment_date`` is strictly after the anchor date and not later
    than ``as_of``.  Operates on the primary data (anchor +
    confirmed payments) without depending on the engine's
    schedule-walk semantics, which matters for the fixed-rate
    trueup case where the engine's from-origination projection
    diverges from the trueup-snapped reality.

    Per-month math (mirrors the engine's per-month split):

    * ``interest = round_money(balance * (rate / 12))`` -- the
      interest accrued for the month at the rate in effect on the
      payment date.
    * ``principal_portion = payment.amount - interest`` -- the
      principal repayment.  Can be negative for an underpayment;
      that represents negative amortization (principal grows),
      which the resolver lets through for forensic correctness.
    * ``balance = round_money(balance - principal_portion)``, then
      floor at zero.  An overpayment that would drive the balance
      negative is capped at zero (the loan is paid off, the user
      does not owe the lender money).

    Payments dated after ``as_of`` are skipped: they are committed
    or settled, but the resolver returns the balance AS OF
    ``as_of`` so future-dated settlements do not retroactively
    reduce a past balance.

    Args:
        anchor_balance: The latest anchor's balance.
        confirmed_after_anchor: Confirmed payments dated strictly
            after the anchor (pre-filtered by the caller).
        rate_changes: Optional rate history -- the resolver looks
            up the per-month rate by payment date.
        base_rate: The loan's original interest rate (fallback for
            months with no applicable rate change).
        as_of: The evaluation date; payments past this date are
            ignored.

    Returns:
        Full-precision Decimal balance; the caller applies
        ``round_money`` at the LoanState boundary.
    """
    balance = anchor_balance
    sorted_payments = sorted(
        confirmed_after_anchor, key=lambda payment: payment.payment_date,
    )
    for payment in sorted_payments:
        if payment.payment_date > as_of:
            break
        rate_at = _rate_at_date(rate_changes, payment.payment_date, base_rate)
        # Zero-rate loans amortize as principal / n with no monthly
        # interest accrual; the engine handles this via the
        # ``annual_rate <= 0`` branch of ``calculate_monthly_payment``.
        # Mirror that here so a confirmed payment on a zero-rate
        # loan is treated as pure principal reduction.
        if rate_at > 0:
            monthly_rate = rate_at / Decimal("12")
            interest = round_money(balance * monthly_rate)
        else:
            interest = ZERO_MONEY
        principal_portion = payment.amount - interest
        if principal_portion >= balance:
            balance = ZERO_MONEY
        else:
            balance = round_money(balance - principal_portion)
            if balance < 0:
                balance = ZERO_MONEY
    return balance


def _compute_monthly_payment(
    loan_params,
    anchor_balance: Decimal,
    anchor_date: date,
    current_balance: Decimal,
    rate_changes: list[RateChangeRecord] | None,
    as_of: date,
    base_rate: Decimal,
    in_arm_window: bool,
) -> Decimal:
    """Return the P&I payment per the resolver's monthly-payment rules.

    Three branches map to the three cases in the module docstring:

    * **ARM inside the fixed-rate window:** payment is the level
      amortization of the anchor balance over the remaining
      contractual term as of the anchor date, at the rate in effect
      at the anchor date.  This is the E-02 fixed-window invariant
      -- the value is held constant for every ``as_of`` inside the
      window, breaking symptom #4's month-over-month creep.
    * **ARM outside the fixed-rate window:** payment is the level
      amortization of the *current* balance over the remaining
      months as of ``as_of``, at the rate in effect on ``as_of``.
      Models post-adjustment behavior where the lender re-amortizes
      after every adjustment date.
    * **Fixed-rate (non-ARM):** payment is the original contractual
      amount (``amortize(original_principal, base_rate, term_months)``).
      A fixed-rate borrower keeps paying the contractual amount even
      after prepayments; the loan pays off early rather than the
      payment shrinking.

    Args:
        loan_params: Object exposing ``is_arm``,
            ``original_principal``, ``term_months``,
            ``origination_date``.
        anchor_balance: Latest anchor's balance.
        anchor_date: Latest anchor's date.
        current_balance: Resolver-derived balance at ``as_of``.
        rate_changes: Optional rate-change history.
        as_of: Evaluation date.
        base_rate: Loan's original interest rate.
        in_arm_window: Output of
            :func:`_is_in_arm_fixed_window`.

    Returns:
        Rounded Decimal monthly payment (via ``round_money``).
    """
    is_arm = bool(getattr(loan_params, "is_arm", False))

    if is_arm and in_arm_window:
        rate_at_anchor = _rate_at_date(
            rate_changes, anchor_date, base_rate,
        )
        months_to_anchor = _months_between(
            loan_params.origination_date, anchor_date,
        )
        remaining_at_anchor = loan_params.term_months - months_to_anchor
        return round_money(
            calculate_monthly_payment(
                anchor_balance, rate_at_anchor, remaining_at_anchor,
            )
        )

    if is_arm:
        rate_at_as_of = _rate_at_date(rate_changes, as_of, base_rate)
        remaining_at_as_of = calculate_remaining_months(
            loan_params.origination_date,
            loan_params.term_months,
            as_of=as_of,
        )
        return round_money(
            calculate_monthly_payment(
                current_balance, rate_at_as_of, remaining_at_as_of,
            )
        )

    # Fixed-rate: contractual payment, never changes.
    return round_money(
        calculate_monthly_payment(
            Decimal(str(loan_params.original_principal)),
            base_rate,
            loan_params.term_months,
        )
    )


def resolve_loan(
    loan_params,
    anchor_events: list,
    payments: list[PaymentRecord] | None,
    rate_changes: list[RateChangeRecord] | None,
    as_of: date,
) -> LoanState:
    """Resolve a loan to its (balance, payment, schedule, payoff, interest).

    Single-source-of-truth producer for every loan-touching surface.
    Replays confirmed payments forward from the latest
    :class:`LoanAnchorEvent` to derive the current balance; computes
    the monthly payment per the ARM-window-aware rules documented at
    module scope; generates the full schedule via
    :func:`amortization_engine.generate_schedule`; derives the payoff
    date and total interest from the same schedule.

    The function is pure: it takes plain data (model instances and
    plain Python lists), returns a frozen :class:`LoanState`, and
    performs no I/O.  This honors the services boundary so the
    resolver is safe to call from any layer (route, service, test)
    and produces deterministic output for a given input tuple.

    Algorithm (see module docstring for the full rationale):

    1. Pick the latest anchor by ``(anchor_date, created_at)`` DESC.
    2. Filter ``payments`` to confirmed entries whose
       ``payment_date`` is strictly after the anchor date.  Projected
       (unconfirmed) payments are NOT replayed -- they are future
       commitments, not historical fact.
    3. Generate the schedule with the engine.  For ARM loans, pass
       the anchor so the engine re-amortizes from it; for
       fixed-rate, do not pass the anchor (the engine projects from
       origination using the contractual payment, which is the
       correct behavior when the only anchor is the origination
       event -- the common fixed-rate case).
    4. Derive the current balance by walking the schedule for the
       latest confirmed post-anchor row at-or-before ``as_of``.
    5. Compute the monthly payment per ARM-in-window vs.
       ARM-out-of-window vs. fixed-rate rules.
    6. Return the LoanState; consumers read its fields without
       recomputing.

    Args:
        loan_params: A :class:`LoanParams`-shaped object exposing
            ``origination_date`` (date), ``term_months`` (int),
            ``original_principal`` (Decimal/str), ``interest_rate``
            (Decimal/str), ``payment_day`` (int), ``is_arm`` (bool),
            and ``arm_first_adjustment_months`` (int | None).
            Plain SQLAlchemy model objects from ``LoanParams`` work
            unchanged; duck-typed objects (test fixtures) work too.
        anchor_events: Non-empty list of LoanAnchorEvent-shaped
            objects (``anchor_date``, ``anchor_balance``,
            ``created_at``).  Commit 12's origination backfill
            guarantees at least one event per loan; an empty list
            raises a ValueError.
        payments: Prepared list of :class:`PaymentRecord` from
            :func:`loan_payment_service.prepare_payments_for_engine`
            (escrow subtracted, biweekly redistributed).  May be
            ``None`` or empty.  Only confirmed entries with
            ``payment_date > anchor_date`` are replayed.
        rate_changes: Optional list of :class:`RateChangeRecord`
            for ARM rate-history.  ``None`` or empty for fixed-rate
            loans and for ARMs still inside their first
            fixed-rate window.
        as_of: The evaluation date.  Drives the
            current-balance walk and the out-of-window
            monthly-payment computation.

    Returns:
        A :class:`LoanState` with the five resolver fields.

    Raises:
        ValueError: When ``anchor_events`` is empty (the Commit-12
            invariant is violated and the caller's data is bad).
    """
    anchor = _select_latest_anchor(anchor_events)
    anchor_balance = Decimal(str(anchor.anchor_balance))
    anchor_date = anchor.anchor_date

    # Filter payments: confirmed and strictly after the anchor.
    # An unconfirmed payment is a Projected transfer the user has
    # not yet marked received/settled; it is a future commitment,
    # not a historical fact, so it must not reduce the principal.
    confirmed_after_anchor = [
        payment
        for payment in (payments or [])
        if payment.is_confirmed and payment.payment_date > anchor_date
    ]

    is_arm = bool(getattr(loan_params, "is_arm", False))
    base_rate = Decimal(str(loan_params.interest_rate))
    orig_principal = Decimal(str(loan_params.original_principal))

    # Schedule generation strategy:
    # * ARM -- pass the anchor so the engine re-amortizes from it;
    #   matches the existing get_loan_projection ARM pattern, which
    #   has working hand-computed coverage.
    # * Fixed-rate -- do NOT pass the anchor.  The engine's
    #   anchor-reset code path unconditionally overrides the
    #   contractual monthly_payment with a re-amortized value
    #   (using months_left from the engine's loop counter against
    #   max_months = 2 * term_months in the contractual branch),
    #   which would corrupt the schedule for fixed-rate loans.
    #   The from-origination contractual path already handles
    #   payment replay correctly via the ``payments`` parameter
    #   and is the established fixed-rate pattern in
    #   ``debt_strategy._compute_real_principal``.  Fixed-rate
    #   trueups remain a follow-up: see F-8 in
    #   ``docs/audits/financial_calculations/remediation_follow_up.md``.
    if is_arm:
        engine_anchor_balance = anchor_balance
        engine_anchor_date = anchor_date
        engine_original = None
    else:
        engine_anchor_balance = None
        engine_anchor_date = None
        engine_original = orig_principal

    schedule = generate_schedule(
        current_principal=orig_principal,
        annual_rate=base_rate,
        remaining_months=loan_params.term_months,
        origination_date=loan_params.origination_date,
        payment_day=loan_params.payment_day,
        original_principal=engine_original,
        term_months=loan_params.term_months,
        payments=confirmed_after_anchor,
        rate_changes=rate_changes,
        anchor_balance=engine_anchor_balance,
        anchor_date=engine_anchor_date,
    )

    current_balance_full = _replay_balance_from_anchor(
        anchor_balance,
        confirmed_after_anchor,
        rate_changes,
        base_rate,
        as_of,
    )

    in_window = _is_in_arm_fixed_window(loan_params, anchor_date, as_of)
    monthly_payment = _compute_monthly_payment(
        loan_params,
        anchor_balance,
        anchor_date,
        current_balance_full,
        rate_changes,
        as_of,
        base_rate,
        in_window,
    )

    # Derive payoff_date and total_interest from the single
    # schedule generation (DRY -- no second engine call).
    if schedule:
        payoff_date = schedule[-1].payment_date
        total_interest_full = sum(
            (row.interest for row in schedule), ZERO_MONEY,
        )
    else:
        payoff_date = loan_params.origination_date
        total_interest_full = ZERO_MONEY

    return LoanState(
        current_balance=round_money(current_balance_full),
        monthly_payment=monthly_payment,
        schedule=schedule,
        payoff_date=payoff_date,
        total_interest=round_money(total_interest_full),
    )
