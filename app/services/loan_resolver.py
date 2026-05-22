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
    project_forward,
    replay_confirmed_history,
)
from app.utils.money import MONTHS_PER_YEAR, round_money

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


@dataclass(frozen=True)
class PayoffScenarios:
    """Single-return-value bundle for the Payoff Calculator's three scenarios.

    Frozen because the composer returns a snapshot the caller renders;
    every consumer (chart series + summary card) reads from one
    instance, so chart and summary cannot diverge by construction.
    The architectural fix this snapshot implements is documented at
    ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``;
    chart-summary divergence was the failure mode that motivated the
    split.

    All three forward slices start from the same
    ``(starting_balance, starting_date, remaining_months,
    applicable_rate)`` tuple produced by a single
    :func:`replay_confirmed_history` call; they differ only in
    ``monthly_override`` and ``extra_monthly``.  Chart rendering is
    ``history_rows + <slice>_forward``; the prefix is byte-identical
    across slices because replay returns the same row list.

    Attributes:
        history_rows: Confirmed-payment rows from origination (or the
            latest anchor) through ``as_of``.  Every row carries
            ``is_confirmed=True``.  Empty when no confirmed payments
            exist at or before ``as_of``.
        original_forward: Pure contractual amortization from
            ``replay.balance_as_of`` forward -- no override, no
            extra.  Models "what the lender would amortize the
            remaining balance to" if the user paid exactly the
            contractual P&I every month.
        committed_forward: Contractual amortization with projected
            transfers routed through ``monthly_override`` -- the
            user's planned outlay, no acceleration.
        accelerated_forward: ``committed_forward`` plus
            ``extra_monthly`` applied to every non-override month.
            Override months ignore extra -- the load-bearing
            distinction that makes the "extra applied to ghost
            historical months" bug structurally impossible (no row
            of any forward slice has a payment_date at or before the
            last replay row).
        months_saved: ``len(committed_forward) - len(accelerated_forward)``.
            Number of payments avoided by paying ``extra_monthly`` per
            non-override month.  Zero when ``extra_monthly == 0`` or
            when the schedules pay off at the same month boundary.
        interest_saved: ``round_money(sum(committed.interest) -
            sum(accelerated.interest))``.  Total interest avoided by
            the acceleration.  Zero or negative is meaningful (a
            negative value would indicate a corner case where extra
            slightly increases total interest -- not expected under
            normal inputs).
        payoff_date_committed: ``committed_forward[-1].payment_date``
            or ``as_of`` when the slice is empty.  The date the loan
            reaches zero under the planned-payment scenario.
        payoff_date_accelerated: ``accelerated_forward[-1].payment_date``
            or ``as_of`` when the slice is empty.
        total_interest_committed: Life-of-remaining-loan interest under
            the committed scenario, rounded via ``round_money``.
            Excludes ``history_rows`` (already paid).
        total_interest_accelerated: Same for the accelerated scenario.
    """

    history_rows: list[AmortizationRow]
    original_forward: list[AmortizationRow]
    committed_forward: list[AmortizationRow]
    accelerated_forward: list[AmortizationRow]
    months_saved: int
    interest_saved: Decimal
    payoff_date_committed: date
    payoff_date_accelerated: date
    total_interest_committed: Decimal
    total_interest_accelerated: Decimal


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
            monthly_rate = rate_at / MONTHS_PER_YEAR
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
    :func:`compute_payoff_scenarios` (the "Committed with no extra"
    composition: ``history_rows + committed_forward``); derives the
    payoff date and total interest from the same schedule.

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
    3. Generate the schedule via :func:`compute_payoff_scenarios`
       with ``extra_monthly=0`` and the confirmed-only payment list.
       ARM vs. fixed-rate anchor handling lives inside the composer
       (Phase 6 of the amortization-engine split); the resolver no
       longer reaches the engine directly.
       ``LoanState.schedule = history_rows + committed_forward``.
    4. Derive the current balance from the anchor + confirmed-payment
       replay (independent of the schedule walk -- the resolver owns
       its balance derivation so a future schedule refactor cannot
       silently change ``state.current_balance``).
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

    base_rate = Decimal(str(loan_params.interest_rate))

    # Schedule generation routes through the scenario composer
    # (Phase 6 of the amortization-engine split -- architectural plan:
    # ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``).
    # ``compute_payoff_scenarios`` calls ``replay_confirmed_history``
    # once and ``project_forward`` once with ``extra_monthly=0`` to
    # produce a "Committed with no extra" composition;
    # ``LoanState.schedule`` is the concatenation of the confirmed-
    # history rows and the forward-projected rows.  ARM vs. fixed-rate
    # anchor handling is owned by the composer (it inspects
    # ``loan_params.is_arm`` and forwards the anchor to replay for ARM
    # only -- the same is_arm-gated passthrough the prior direct
    # engine call implemented inline above).  Passing
    # only ``confirmed_after_anchor`` keeps the resolver's confirmed-
    # only contract intact: every entry feeds replay, none becomes a
    # forward override.  Fixed-rate trueups remain a follow-up: see
    # F-8 in
    # ``docs/audits/financial_calculations/remediation_follow_up.md``.
    scenarios = compute_payoff_scenarios(
        loan_params=loan_params,
        anchor_events=anchor_events,
        payments=confirmed_after_anchor,
        rate_changes=rate_changes,
        extra_monthly=ZERO_MONEY,
        as_of=as_of,
    )
    schedule = list(scenarios.history_rows) + list(
        scenarios.committed_forward
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


def _build_monthly_override(
    payments: list[PaymentRecord],
    as_of: date,
) -> dict[tuple[int, int], Decimal]:
    """Group projection-eligible payments into a (year, month) sum.

    The composer routes two payment classes through
    ``project_forward``'s ``monthly_override``:

    * Every projected (``is_confirmed=False``) payment regardless of
      date.  These are the user's planned future outlays from
      recurring transfer templates; they belong on the forward side
      because they have not actually happened yet.
    * Confirmed payments dated strictly after ``as_of``.  Rare data
      hygiene case (a user marked a future payment as settled);
      treated as a projection so the replay window stops cleanly at
      ``as_of`` and the forward slice picks the payment up.

    Payments with multiple entries in the same calendar month are
    summed so the override map is a "total planned outlay for this
    month" view -- matching how ``project_forward`` consumes it.

    Args:
        payments: The full prepared payment list, typically from
            :func:`app.services.loan_payment_service.prepare_payments_for_engine`.
            Mixed confirmed/projected; the function filters
            internally.
        as_of: Cutoff date used to separate replay history from
            forward projection.  Confirmed payments at or before
            ``as_of`` are consumed by replay and excluded here.

    Returns:
        A dict mapping ``(year, month) -> Decimal`` total payment.
        Empty dict when no projection-eligible payment exists.
    """
    override: dict[tuple[int, int], Decimal] = {}
    for payment in payments:
        # Confirmed payments at or before as_of belong to replay,
        # not projection -- exclude them.  Everything else
        # (projected payments + confirmed payments past as_of) is a
        # forward-only concept.
        if payment.is_confirmed and payment.payment_date <= as_of:
            continue
        key = (payment.payment_date.year, payment.payment_date.month)
        override[key] = override.get(key, ZERO_MONEY) + payment.amount
    return override


def _remaining_rate_changes(
    rate_changes: list[RateChangeRecord] | None,
    next_pay_date: date,
) -> list[RateChangeRecord]:
    """Filter ``rate_changes`` to entries effective at or after ``next_pay_date``.

    ``project_forward``'s ARM behavior only needs rate transitions
    that fire within its window.  Replay consumes the pre-window
    transitions internally to compute ``applicable_rate_as_of``,
    which is then the projection's starting rate, so passing
    already-consumed transitions to projection would be wasted work
    (and a future-defensive guard against double-applying a
    transition at the replay/projection boundary).

    Args:
        rate_changes: Optional full rate-change history (possibly
            unsorted).  ``None`` or empty is treated as "no remaining
            transitions."
        next_pay_date: First payment_date of the forward projection
            (replay's ``next_pay_date``).  Entries strictly before
            this date are dropped.

    Returns:
        A list of :class:`RateChangeRecord` whose ``effective_date``
        is at or after ``next_pay_date``.  Empty list when no entry
        qualifies.
    """
    if not rate_changes:
        return []
    return [
        change for change in rate_changes
        if change.effective_date >= next_pay_date
    ]


def _contractual_payment_for_projection(
    loan_params,
    base_rate: Decimal,
    replay_balance_as_of: Decimal,
    replay_applicable_rate: Decimal,
    replay_remaining_months: int,
) -> Decimal:
    """Return the contractual P&I baseline ``project_forward`` should use.

    For fixed-rate loans the contractual payment is the original
    amortization of ``original_principal`` over ``term_months`` at
    ``base_rate`` -- constant for the life of the loan, matching the
    resolver's existing fixed-rate behavior
    (``_compute_monthly_payment`` lines 469-475).  Pre-payments
    accelerate the payoff date rather than shrinking the monthly
    payment.

    For ARM loans the contractual is the re-amortized payment at
    ``as_of``: remaining balance over remaining months at the
    applicable rate.  This matches the resolver's ARM-outside-window
    branch and is correct as the projection's starting baseline --
    ARM rate transitions inside ``project_forward`` will recompute
    this as transitions fire.

    Args:
        loan_params: Loan parameter object exposing ``is_arm``,
            ``original_principal``, ``term_months``.
        base_rate: Loan's original interest rate as Decimal.
        replay_balance_as_of: ``ReplayResult.balance_as_of`` -- the
            outstanding balance at the projection's start.
        replay_applicable_rate: ``ReplayResult.applicable_rate_as_of``.
        replay_remaining_months: ``ReplayResult.remaining_months_as_of``.

    Returns:
        Decimal contractual P&I payment.  ``Decimal("0.00")`` when
        the loan has no principal or no remaining months (handled by
        ``calculate_monthly_payment``'s guard).
    """
    is_arm = bool(getattr(loan_params, "is_arm", False))
    if is_arm:
        return calculate_monthly_payment(
            replay_balance_as_of,
            replay_applicable_rate,
            replay_remaining_months,
        )
    return calculate_monthly_payment(
        Decimal(str(loan_params.original_principal)),
        base_rate,
        loan_params.term_months,
    )


def compute_payoff_scenarios(
    *,
    loan_params,
    anchor_events: list,
    payments: list[PaymentRecord] | None,
    rate_changes: list[RateChangeRecord] | None,
    extra_monthly: Decimal,
    as_of: date,
) -> PayoffScenarios:
    """Single source of truth for the Payoff Calculator's three scenarios.

    Calls :func:`replay_confirmed_history` ONCE to derive a
    deterministic-past slice plus the starting state, then calls
    :func:`project_forward` THREE times from the same starting
    ``(balance, date, remaining_months, rate)`` tuple, differing only
    in ``monthly_override`` and ``extra_monthly``.  The chart series
    (Original / Committed / Accelerated) and the summary metrics
    (months_saved, interest_saved, payoff dates, life-of-remaining-
    loan interest) all derive from the single return value, so chart
    and summary cannot diverge.

    Routes projected payments forward through ``monthly_override``
    instead of relying on the engine's "apply extra when no payment
    record exists" convention -- the architectural fix for the
    "extra applied to ghost historical months" bug documented at
    ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``.
    Override months never receive ``extra_monthly``; non-override
    months always do (when extra is non-zero).  This makes the buggy
    parameter combination structurally inexpressible.

    Algorithm:

    1. Pick the latest :class:`LoanAnchorEvent` from
       ``anchor_events`` (same selector the resolver uses).
    2. For ARM loans, pass the anchor balance + date to replay so the
       running balance snaps to the verified value at ``anchor_date``.
       Fixed-rate loans do not snap (the origination anchor's
       balance equals ``original_principal`` so the snap would be a
       no-op anyway; explicit None is clearer).
    3. Filter ``payments`` to confirmed entries with
       ``payment_date <= as_of``; the rest become forward overrides
       (see :func:`_build_monthly_override`).
    4. Replay produces ``history_rows``, ``balance_as_of``,
       ``next_pay_date``, ``remaining_months_as_of``,
       ``applicable_rate_as_of``.
    5. Three forward projections share that starting state.  Their
       contractual P&I is derived once via
       :func:`_contractual_payment_for_projection`; rate-change
       remainders (transitions effective at or after
       ``next_pay_date``) are passed to all three so ARM behavior is
       consistent across the trio.
    6. Summary metrics derive from the same forward slices --
       ``months_saved`` is a length diff, ``interest_saved`` is a
       row-sum diff.

    Args:
        loan_params: A :class:`LoanParams`-shaped object exposing
            ``origination_date`` (date), ``term_months`` (int),
            ``original_principal`` (Decimal/str), ``interest_rate``
            (Decimal/str), ``payment_day`` (int), ``is_arm`` (bool),
            and (for ARM) ``arm_first_adjustment_months`` (int |
            None).  Duck-typed test fixtures work unchanged.
        anchor_events: Non-empty list of LoanAnchorEvent-shaped
            objects (``anchor_date``, ``anchor_balance``,
            ``created_at``).  Commit-12's origination backfill
            guarantees at least one event per loan; an empty list
            raises a ValueError via ``_select_latest_anchor``.
        payments: Prepared list of :class:`PaymentRecord` (typically
            from
            :func:`app.services.loan_payment_service.prepare_payments_for_engine`).
            May be ``None`` or empty.  The composer separates
            confirmed-pre-as_of (replay) from everything else
            (override) internally.
        rate_changes: Optional ARM rate transitions.  ``None`` or
            empty for fixed-rate loans.  Replay consumes
            pre-``next_pay_date`` entries; the composer slices the
            remainder for projection.
        extra_monthly: Additional principal payment applied to every
            non-override month in the accelerated scenario.  ``0``
            collapses the accelerated slice to the committed slice
            (``months_saved == 0``, ``interest_saved == 0``).
        as_of: Evaluation date.  The replay/projection boundary.
            Typically ``date.today()`` from the route.

    Returns:
        A :class:`PayoffScenarios` with the three forward slices and
        the four summary metrics.

    Raises:
        ValueError: When ``anchor_events`` is empty (via
            ``_select_latest_anchor``).
    """
    anchor = _select_latest_anchor(anchor_events)
    is_arm = bool(getattr(loan_params, "is_arm", False))
    base_rate = Decimal(str(loan_params.interest_rate))
    orig_principal = Decimal(str(loan_params.original_principal))

    # ARM anchors snap replay's running balance to the verified value
    # at anchor_date; fixed-rate loans do not snap (the origination
    # anchor is a no-op snap, and a hypothetical fixed-rate trueup
    # is the F-8 follow-up).  This mirrors the resolver's
    # is_arm-gated anchor passthrough (resolve_loan lines 589-596).
    if is_arm:
        replay_anchor_balance: Decimal | None = Decimal(
            str(anchor.anchor_balance)
        )
        replay_anchor_date: date | None = anchor.anchor_date
    else:
        replay_anchor_balance = None
        replay_anchor_date = None

    # Replay consumes confirmed payments at or before as_of.  An
    # unconfirmed payment (Projected status) is a future commitment,
    # not a historical fact, so it must not reduce the replayed
    # balance.  Confirmed payments past as_of are routed through
    # monthly_override below.
    confirmed_for_replay = [
        payment
        for payment in (payments or [])
        if payment.is_confirmed and payment.payment_date <= as_of
    ]

    replay = replay_confirmed_history(
        origination_date=loan_params.origination_date,
        original_principal=orig_principal,
        annual_rate=base_rate,
        term_months=loan_params.term_months,
        payment_day=loan_params.payment_day,
        confirmed_payments=confirmed_for_replay,
        rate_changes=rate_changes,
        anchor_balance=replay_anchor_balance,
        anchor_date=replay_anchor_date,
        as_of=as_of,
    )

    monthly_override = _build_monthly_override(payments or [], as_of)
    rate_changes_remaining = _remaining_rate_changes(
        rate_changes, replay.next_pay_date,
    )
    contractual = _contractual_payment_for_projection(
        loan_params,
        base_rate,
        replay.balance_as_of,
        replay.applicable_rate_as_of,
        replay.remaining_months_as_of,
    )

    # All three forward slices share starting state; only override
    # presence and extra_monthly vary.  The architectural plan's
    # critical regression-prevention property -- chart and summary
    # cannot diverge -- is enforced HERE by funnelling all three
    # through the same primitive call shape.
    projection_kwargs = {
        "starting_balance": replay.balance_as_of,
        "starting_date": replay.next_pay_date,
        "annual_rate": replay.applicable_rate_as_of,
        "remaining_months": replay.remaining_months_as_of,
        "payment_day": loan_params.payment_day,
        "contractual_payment": contractual,
        "rate_changes_remaining": (
            rate_changes_remaining if rate_changes_remaining else None
        ),
    }
    override_for_projection = monthly_override if monthly_override else None

    original_forward = project_forward(
        **projection_kwargs,
        monthly_override=None,
        extra_monthly=ZERO_MONEY,
    )
    committed_forward = project_forward(
        **projection_kwargs,
        monthly_override=override_for_projection,
        extra_monthly=ZERO_MONEY,
    )
    accelerated_forward = project_forward(
        **projection_kwargs,
        monthly_override=override_for_projection,
        extra_monthly=extra_monthly,
    )

    # Summary metrics derive from the same forward slices the chart
    # plots -- the load-bearing single-source-of-truth guarantee.
    months_saved = len(committed_forward) - len(accelerated_forward)
    total_interest_committed_full = sum(
        (row.interest for row in committed_forward), ZERO_MONEY,
    )
    total_interest_accelerated_full = sum(
        (row.interest for row in accelerated_forward), ZERO_MONEY,
    )
    interest_saved_full = (
        total_interest_committed_full - total_interest_accelerated_full
    )

    payoff_date_committed = (
        committed_forward[-1].payment_date
        if committed_forward else as_of
    )
    payoff_date_accelerated = (
        accelerated_forward[-1].payment_date
        if accelerated_forward else as_of
    )

    return PayoffScenarios(
        history_rows=replay.rows,
        original_forward=original_forward,
        committed_forward=committed_forward,
        accelerated_forward=accelerated_forward,
        months_saved=months_saved,
        interest_saved=round_money(interest_saved_full),
        payoff_date_committed=payoff_date_committed,
        payoff_date_accelerated=payoff_date_accelerated,
        total_interest_committed=round_money(total_interest_committed_full),
        total_interest_accelerated=round_money(
            total_interest_accelerated_full
        ),
    )
