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
2. Replay only ``is_confirmed`` payments whose true monthly due date
   (``rate_period_engine.monthly_due_date`` of the pay-period-start the
   payment is keyed to) is strictly after the anchor date.  Comparing
   the due date rather than the pay-period start keeps a payment whose
   biweekly pay period began on or before a mid-period balance true-up
   but whose monthly payment is not due until after it.  Projected
   (unconfirmed) payments do not reduce the balance -- they are future
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

import dataclasses
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.amortization_engine import (
    AmortizationRow,
    PaymentRecord,
    RateChangeRecord,
    project_forward,
)
from app.services.rate_period_engine import (
    BalanceAnchor,
    LoanTerms,
    build_rate_periods,
    monthly_due_date,
    period_for_date,
    replay_schedule,
)
from app.utils.money import round_money

ZERO_MONEY = Decimal("0.00")


def _loan_terms_from(loan_params) -> LoanTerms:
    """Build the rate-period engine's :class:`LoanTerms` from a LoanParams.

    Reads the immutable origination fields a loan's amortization is
    defined by.  ``interest_rate`` is the engine's base rate (the
    :class:`RateHistory`-layered rate changes override it per period);
    the ARM cadence columns drive the fixed-rate period boundaries.

    Args:
        loan_params: A LoanParams-shaped object exposing
            ``origination_date``, ``original_principal``,
            ``interest_rate``, ``term_months``, ``is_arm``,
            ``arm_first_adjustment_months``, and
            ``arm_adjustment_interval_months``.

    Returns:
        The corresponding :class:`LoanTerms`.
    """
    return LoanTerms(
        origination_date=loan_params.origination_date,
        original_principal=Decimal(str(loan_params.original_principal)),
        base_rate=Decimal(str(loan_params.interest_rate)),
        term_months=loan_params.term_months,
        is_arm=bool(getattr(loan_params, "is_arm", False)),
        arm_first_adjustment_months=getattr(
            loan_params, "arm_first_adjustment_months", None,
        ),
        arm_adjustment_interval_months=getattr(
            loan_params, "arm_adjustment_interval_months", None,
        ),
    )


def _recorded_pi_from(
    rate_changes: list[RateChangeRecord] | None,
) -> dict[date, Decimal]:
    """Extract the recorded recast-P&I map from the rate-change feed.

    A rate change's ``monthly_pi`` (when present) is the lender's
    recorded recast payment for the fixed-rate period that change
    begins.  Keying by ``effective_date`` lets
    :func:`rate_period_engine.build_rate_periods` hold that exact figure
    constant for the period instead of deriving it.

    Args:
        rate_changes: Optional :class:`RateChangeRecord` list; entries
            without a ``monthly_pi`` are omitted (their period's P&I is
            derived).

    Returns:
        A ``{effective_date: monthly_pi}`` dict (empty when none recorded).
    """
    if not rate_changes:
        return {}
    return {
        change.effective_date: Decimal(str(change.monthly_pi))
        for change in rate_changes
        if change.monthly_pi is not None
    }


def _resolve_periods(loan_params, rate_changes):
    """Build the loan's rate periods from its params and rate-change feed.

    One construction shared by every resolver entry point
    (:func:`resolve_loan`, :func:`compute_monthly_payment_baseline`,
    :func:`compute_payoff_scenarios`) so the period set they read cannot
    drift apart.

    Args:
        loan_params: The loan's :class:`LoanParams`-shaped object.
        rate_changes: Optional :class:`RateChangeRecord` feed.

    Returns:
        The ordered :class:`~app.services.rate_period_engine.RatePeriod`
        list for the loan.
    """
    return build_rate_periods(
        terms=_loan_terms_from(loan_params),
        rate_changes=rate_changes,
        recorded_period_pi=_recorded_pi_from(rate_changes),
    )


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


def compute_monthly_payment_baseline(
    loan_params,
    anchor_events: list,
    rate_changes: list[RateChangeRecord] | None,
    as_of: date,
    payments: list[PaymentRecord] | None = None,
) -> Decimal:
    """Return the loan's current monthly P&I -- the rate-period level payment.

    Single source of truth for "what does the user pay each month",
    used by
    :func:`app.services.loan_payment_service.compute_contractual_pi`
    to size the escrow-subtraction threshold so the schedule's
    projected P&I matches the loan card's P&I exactly.  Returns the
    same value as ``resolve_loan(...).monthly_payment`` for the same
    inputs, without running the full balance replay or schedule
    generation.

    The monthly P&I is the level payment of the rate period containing
    ``as_of`` (see :func:`build_rate_periods`): held constant within the
    period and recast only at a rate adjustment.  It is independent of
    the running balance, so ``anchor_events`` and ``payments`` are
    accepted for caller compatibility only and are not read.

    Args:
        loan_params: Loan parameter object exposing the fields
            :func:`build_rate_periods` reads (origination, principal,
            base rate, term, ARM cadence).
        anchor_events: Accepted for caller compatibility; unused -- the
            period P&I does not depend on the anchor balance.
        rate_changes: Optional ARM rate-history feeding each period's
            rate and any recorded recast P&I.  ``None`` or empty for a
            fixed-rate loan.
        as_of: Evaluation date; selects the governing rate period.
        payments: Accepted for caller compatibility; unused.

    Returns:
        Rounded Decimal monthly P&I, equal to
        ``resolve_loan(...).monthly_payment`` for the same inputs.
    """
    # ``anchor_events`` and ``payments`` are unused: the current
    # period's level P&I is anchor-independent -- a property of the
    # loan's contractual rate-period structure, not of the running
    # balance.  Both stay in the signature for caller compatibility
    # (loan_payment_service.compute_contractual_pi passes them).
    # pylint: disable=unused-argument
    return period_for_date(
        _resolve_periods(loan_params, rate_changes), as_of,
    ).period_pi


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
    2. Filter ``payments`` to confirmed entries; ``replay_schedule``
       then keeps those whose true monthly due date is after the anchor
       date and whose pay period has begun by ``as_of``.  Projected
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
            ``None`` or empty.  Only confirmed entries whose true
            monthly due date is after the anchor date are replayed.
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

    # Filter payments to confirmed only.  An unconfirmed payment is a
    # Projected transfer the user has not yet marked received/settled; it
    # is a future commitment, not a historical fact, so it must not reduce
    # the principal.  The anchor boundary itself (and the as-of cap) is
    # owned by replay_schedule, which classifies each payment by its true
    # monthly due date: a pay-period start can fall up to ~2 weeks before
    # the contractual due date, so filtering on it HERE would strand a
    # payment whose pay period straddles a mid-period balance true-up.
    confirmed = [
        payment for payment in (payments or []) if payment.is_confirmed
    ]

    periods = _resolve_periods(loan_params, rate_changes)

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
    # engine call implemented inline above).  Passing only ``confirmed``
    # keeps the resolver's confirmed-only contract intact: every entry
    # feeds replay (which drops pre-anchor entries by due date), none
    # becomes a forward override.  Fixed-rate trueups remain a follow-up:
    # see F-8 in
    # ``docs/audits/financial_calculations/remediation_follow_up.md``.
    scenarios = compute_payoff_scenarios(
        loan_params=loan_params,
        anchor_events=anchor_events,
        payments=confirmed,
        rate_changes=rate_changes,
        extra_monthly=ZERO_MONEY,
        as_of=as_of,
    )
    schedule = list(scenarios.history_rows) + list(
        scenarios.committed_forward
    )

    # Current balance is schedule-driven: replay advances one scheduled
    # step per confirmed payment from the latest anchor (principal =
    # period P&I - interest).  The cash amount and escrow never enter,
    # so an escrow change cannot drift the recorded balance.  Derived
    # independently of the schedule generation above so a future
    # projection change cannot silently move it.
    current_balance_full = replay_schedule(
        periods=periods,
        anchor=BalanceAnchor(balance=anchor_balance, as_of_date=anchor_date),
        confirmed_payment_dates=[
            payment.payment_date for payment in confirmed
        ],
        payment_day=loan_params.payment_day,
        as_of=as_of,
    ).balance_as_of

    # Monthly P&I is the current rate period's level payment, held
    # constant within the period and recast only at an adjustment
    # boundary -- independent of the anchor balance, so a balance
    # true-up never moves the displayed payment.
    monthly_payment = period_for_date(periods, as_of).period_pi

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
    payment_day: int,
) -> dict[tuple[int, int], Decimal]:
    """Group projection-eligible payments into a (year, month) sum.

    The composer routes two payment classes through
    ``project_forward``'s ``monthly_override``:

    * Every projected (``is_confirmed=False``) payment regardless of
      date.  These are the user's planned future outlays from
      recurring transfer templates; they belong on the forward side
      because they have not actually happened yet.
    * Confirmed payments whose pay-period start is after ``as_of``.  Rare
      data hygiene case (a user marked a future payment as settled);
      treated as a projection so the replay window stops cleanly at
      ``as_of`` and the forward slice picks the payment up.

    Two dates with distinct jobs (the same split ``replay_schedule`` makes):

    * The replay/projection CUT keys on the pay-period-start date, the same
      date ``replay_schedule`` uses for its ``as_of`` cap, so the two
      partitions are exact complements: a confirmed payment is in replay
      XOR projection, never both and never neither.
    * The override MONTH is the payment's true monthly due month (see
      :func:`app.services.rate_period_engine.monthly_due_date`), matching
      the due-date dating ``replay_schedule`` gives its rows and
      ``project_forward`` its forward rows.  Keying on the pay-period-start
      month instead would land each planned amount one month early -- a
      latent error whenever planned amounts vary month to month.

    Payments with multiple entries in the same calendar month are
    summed so the override map is a "total planned outlay for this
    month" view -- matching how ``project_forward`` consumes it.

    Args:
        payments: The full prepared payment list, typically from
            :func:`app.services.loan_payment_service.prepare_payments_for_engine`.
            Mixed confirmed/projected; the function filters
            internally.
        as_of: Cutoff date used to separate replay history from
            forward projection.  Confirmed payments whose pay-period
            start is at or before ``as_of`` are consumed by replay and
            excluded here.
        payment_day: The loan's contractual day-of-month due day, used to
            derive each payment's true monthly due month for the key.

    Returns:
        A dict mapping ``(year, month) -> Decimal`` total payment.
        Empty dict when no projection-eligible payment exists.
    """
    override: dict[tuple[int, int], Decimal] = {}
    for payment in payments:
        # Confirmed payments whose pay period has begun by as_of belong to
        # replay, not projection -- exclude them.  Everything else
        # (projected payments + confirmed payments whose period has not
        # begun) is a forward-only concept.  The pay-period-start test
        # mirrors replay_schedule's as_of cap so the two are exact
        # complements.
        if payment.is_confirmed and payment.payment_date <= as_of:
            continue
        # Key on the true monthly due month so the planned amount lands on
        # the same forward row project_forward generates (it advances from
        # replay's due-date-derived next_pay_date).
        due_date = monthly_due_date(payment.payment_date, payment_day)
        key = (due_date.year, due_date.month)
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
    2. Pass the anchor balance + date to replay unconditionally
       (ARM and fixed-rate alike) so replay starts at the verified
       balance.  Pre-anchor confirmed payments are filtered inside
       replay; their effect is already baked into ``anchor_balance``.
    3. Group projected payments (and any confirmed payments past
       ``as_of``) by ``(year, month)`` for the forward overrides
       (see :func:`_build_monthly_override`).
    4. Replay produces ``history_rows``, ``balance_as_of``,
       ``next_pay_date``, ``remaining_months_as_of``, and the
       ``current_period`` (its rate and level P&I).
    5. Three forward projections share that starting state.  Their
       contractual P&I is the current rate period's level payment
       (:func:`period_for_date`), the same value the loan card reads,
       so the schedule's projected P&I matches
       ``LoanState.monthly_payment`` by construction.
       Rate-change remainders (transitions effective at or after
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
    anchor_balance = Decimal(str(anchor.anchor_balance))
    anchor_date = anchor.anchor_date
    periods = _resolve_periods(loan_params, rate_changes)

    # The balance is schedule-driven: replay advances one scheduled step
    # per confirmed payment from the latest anchor, reducing principal by
    # (period P&I - interest).  The cash amount and escrow never enter,
    # so an escrow change cannot drift the recorded balance, and the
    # historical rows match the loan card's current_balance by
    # construction (both read this same engine).  Historical rows carry
    # extra_payment=0 from the engine; the composer applies the SSOT
    # ``contractual`` post-replay (below) to surface any overpayment.
    replay = replay_schedule(
        periods=periods,
        anchor=BalanceAnchor(balance=anchor_balance, as_of_date=anchor_date),
        confirmed_payment_dates=[
            payment.payment_date
            for payment in (payments or [])
            if payment.is_confirmed
        ],
        payment_day=loan_params.payment_day,
        as_of=as_of,
    )

    monthly_override = _build_monthly_override(
        payments or [], as_of, loan_params.payment_day,
    )
    rate_changes_remaining = _remaining_rate_changes(
        rate_changes, replay.next_pay_date,
    )
    # Contractual P&I for the forward projection is the SAME current-
    # period level payment that drives ``LoanState.monthly_payment`` on
    # the loan card, so the card and the schedule's projected rows agree
    # by construction (both read the rate-period engine via ``as_of``).
    contractual = period_for_date(periods, as_of).period_pi

    # Surface historical overpayments via the ``extra_payment`` field
    # without coupling replay back to the threshold/preparation cycle.
    # Replay returns ``extra_payment=0`` (see its docstring); the
    # composer applies the SSOT ``contractual`` value post-replay so
    # the schedule's Extra column shows the difference between each
    # recorded payment and the resolver's monthly_payment.  For a
    # user paying exactly ``state.monthly_payment + monthly_escrow``,
    # the prepared payment equals ``contractual`` and extra is 0.
    # For a legitimate overpayment ($2080 against $1580 contractual),
    # extra surfaces as $500.  This closes the D-1 divergence
    # ("historical extra computed against original-terms even for an
    # ARM whose rate has adjusted") because ``contractual`` IS the
    # ARM-aware SSOT value.
    history_rows_with_extras = [
        dataclasses.replace(
            row,
            extra_payment=round_money(
                max(row.payment - contractual, ZERO_MONEY)
            ),
        )
        for row in replay.rows
    ]

    # All three forward slices share starting state; only override
    # presence and extra_monthly vary.  The architectural plan's
    # critical regression-prevention property -- chart and summary
    # cannot diverge -- is enforced HERE by funnelling all three
    # through the same primitive call shape.
    projection_kwargs = {
        "starting_balance": replay.balance_as_of,
        "starting_date": replay.next_pay_date,
        "annual_rate": period_for_date(periods, replay.next_pay_date).annual_rate,
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
        history_rows=history_rows_with_extras,
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
