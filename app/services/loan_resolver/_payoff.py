"""Loan-resolver payoff composer: the three-scenario "what-if" producer.

Single source of truth for the Payoff Calculator's Original / Committed /
Accelerated scenarios.  Replays the past once and projects three ways from
one shared starting state so the chart series and the summary metrics derive
from the same return value and cannot diverge.

Pure: no Flask, no ``db.session``; the caller loads the data and passes it in.
"""

import dataclasses
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.amortization_engine import (
    AmortizationRow,
    PaymentRecord,
    ProjectionInputs,
    project_forward,
    required_extra_for_projection,
)
from app.services.rate_period_engine import period_for_date
from app.utils.money import round_money

from ._periods import (
    ZERO_MONEY,
    ConfirmedLedgerView,
    LoanInputs,
    _replay_from_anchor,
    _terms_from_periods,
    resolve_periods,
)


@dataclass(frozen=True)
class PayoffScenarios:  # pylint: disable=too-many-instance-attributes
    """Single-return-value bundle for the Payoff Calculator's three scenarios.

    Pylint: ``too-many-instance-attributes`` (10/7) -- suppressed
    because this is a deliberate single-return result aggregate -- the
    chart series (``history_rows`` plus the three forward slices) and
    the four summary metrics are the one cohesive contract the Payoff
    Calculator's chart and summary card both read, flat.  Splitting it
    would fragment that contract for no design gain (same rationale as
    :class:`~app.services.amortization_engine.AmortizationRow`).

    Frozen because the composer returns a snapshot the caller renders;
    every consumer (chart series + summary card) reads from one
    instance, so chart and summary cannot diverge by construction.
    The architectural fix this snapshot implements is documented at
    ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``;
    chart-summary divergence was the failure mode that motivated the
    split.

    All three forward slices start from the same
    ``(starting_balance, starting_date, remaining_months,
    terms_schedule)`` state produced by a single
    :func:`rate_period_engine.replay_schedule` call plus the loan's
    rate-period terms feed; they differ only in
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
    * Confirmed payments whose pay-period start is after ``as_of``.  Rare
      data hygiene case (a user marked a future payment as settled);
      treated as a projection so the replay window stops cleanly at
      ``as_of`` and the forward slice picks the payment up.

    Two dates with distinct jobs (the same split ``replay_schedule`` makes):

    * The replay/projection CUT keys on the pay-period-start date, the same
      date ``replay_schedule`` uses for its ``as_of`` cap, so the two
      partitions are exact complements: a confirmed payment is in replay
      XOR projection, never both and never neither.
    * The override MONTH is the payment's own due month
      (:attr:`PaymentRecord.due_date`), matching the due-date dating
      ``replay_schedule`` gives its rows and ``project_forward`` its forward
      rows.  Keying on the pay-period-start month instead would land each
      planned amount one month early -- a latent error whenever planned
      amounts vary month to month.

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
        # Key on the payment's own due month so the planned amount lands on
        # the same forward row project_forward generates (it advances from
        # replay's due-date-derived next_pay_date).
        key = (payment.due_date.year, payment.due_date.month)
        override[key] = override.get(key, ZERO_MONEY) + payment.amount
    return override


@dataclass(frozen=True)
class _ProjectionPrep:
    """The replay-derived inputs the payoff composer builds its result from.

    Produced once by :func:`_build_forward_inputs` so
    :func:`compute_payoff_scenarios` reads three values from one local
    instead of threading the replay, override map, contractual P&I, and
    rate-period set through its body (which pushed it over the
    local-variable limit), leaving the composer a thin
    "project three ways, then summarize" orchestrator.

    Attributes:
        projection_inputs: The shared :class:`ProjectionInputs` all three
            forward slices project from -- same starting balance, date,
            remaining months, and rate-period terms feed (each month's
            SSOT rate and contractual P&I).
        history_rows: The confirmed-payment history slice (origination or
            latest anchor through ``as_of``), each row's ``extra_payment``
            surfaced against the SSOT contractual payment.
        monthly_override: The ``(year, month) -> Decimal`` planned-outlay
            map for the committed / accelerated slices, or ``None`` when
            no projection-eligible payment exists.
    """

    projection_inputs: ProjectionInputs
    history_rows: list[AmortizationRow]
    monthly_override: dict[tuple[int, int], Decimal] | None


def _build_forward_inputs(
    loan_inputs: LoanInputs,
    as_of: date,
    confirmed_view: ConfirmedLedgerView | None = None,
) -> _ProjectionPrep:
    """Replay the past and assemble the shared inputs for the three forward slices.

    The single setup phase of :func:`compute_payoff_scenarios`: replay
    confirmed payments from the latest anchor, derive the SSOT
    contractual P&I and the planned-outlay override map, surface
    historical overpayments on the history rows, and build the one
    :class:`ProjectionInputs` all three slices share.

    Args:
        loan_inputs: The loan's loaded input bundle.
        as_of: The replay/projection boundary date.
        confirmed_view: The loan's genesis-ledger confirmed view (the read
            switch), or ``None`` to keep the anchor replay for both halves.
            When supplied, its ``balance`` overrides the projection's starting
            BALANCE and its ``history_rows`` REPLACE the replay's confirmed
            rows (the ledger rows arrive complete, actual extra included, so
            the D-1 extra re-derivation below is skipped for them).  The
            starting date and remaining months stay the replay's: both derive
            from the LAST post-anchor payment (or the anchor date when none),
            a fact the two producers agree on even where their ROW SETS differ
            (the ledger keeps pre-true-up payments and true due dates; the
            replay drops the former and redistributes biweekly collisions) --
            so only the balance and the row economics differ, and they do so
            exactly off-schedule (the ledger books the REAL principal /
            interest paid, the replay the SCHEDULED figures).  The forward
            slices then amortize the real owed balance over the remaining
            contractual months.

    Returns:
        A :class:`_ProjectionPrep` with the shared projection inputs, the
        confirmed-payment history slice, and the forward override map.

    Raises:
        ValueError: When ``loan_inputs.anchor_events`` is empty (via
            :func:`._periods._replay_from_anchor`).
    """
    periods = resolve_periods(
        loan_inputs.loan_params, loan_inputs.rate_changes,
    )
    # The replay balance is schedule-driven: replay advances one scheduled
    # step per confirmed payment from the latest anchor, reducing principal
    # by (period P&I - interest).  The cash amount and escrow never enter,
    # so an escrow change cannot drift the recorded balance.  Under the read
    # switch the replay still supplies the projection's starting DATE and
    # remaining MONTHS (payment-count facts, identical under both
    # producers); its rows and balance are the fallback when no ledger view
    # is supplied.
    replay = _replay_from_anchor(loan_inputs, periods, as_of)

    # Contractual P&I for the forward projection is the SAME current-
    # period level payment that drives ``LoanState.monthly_payment`` on
    # the loan card, so the card and the schedule's projected rows agree
    # by construction (both read the rate-period engine via ``as_of``).
    contractual = period_for_date(periods, as_of).period_pi

    monthly_override = _build_monthly_override(
        loan_inputs.payments or [],
        as_of,
    )

    if confirmed_view is not None:
        # The ledger rows carry their ACTUAL economics -- principal,
        # interest, and extra measured against the governing period's
        # contractual P&I -- so they are used verbatim (re-deriving extra
        # here would wipe the actual value: the ledger row's ``payment`` is
        # already the contractual-shaped portion).
        history_rows = list(confirmed_view.history_rows)
    else:
        # Surface historical overpayments via the ``extra_payment`` field
        # without coupling replay back to the threshold/preparation cycle.
        # Replay returns ``extra_payment=0`` (see its docstring); applying
        # the SSOT ``contractual`` here shows the schedule's Extra column as
        # the difference between each recorded payment and the resolver's
        # monthly_payment.  This closes the D-1 divergence ("historical
        # extra computed against original-terms even for an ARM whose rate
        # has adjusted") because ``contractual`` IS the ARM-aware SSOT value.
        history_rows = [
            dataclasses.replace(
                row,
                extra_payment=round_money(
                    max(row.payment - contractual, ZERO_MONEY)
                ),
            )
            for row in replay.rows
        ]

    # The projection's terms feed is the loan's FULL rate-period set
    # (past periods included), so every forward month -- including the
    # gap months of a stale anchor whose ``next_pay_date`` lags
    # ``as_of`` -- is governed by its true period's rate AND level P&I.
    # The rate-period engine stays the single producer of those figures
    # (recorded recast or schedule-derived), which is what makes the
    # projected rows and the loan card agree at every date, not just at
    # ``as_of`` (DH-#1).
    #
    # The starting BALANCE is the read switch's one seam: the genesis-ledger
    # confirmed balance when a view is supplied, else the schedule-replay
    # balance.  The starting DATE and remaining MONTHS stay the replay's
    # (see the ``confirmed_view`` arg doc), so seeding the real owed balance
    # amortizes it over the same remaining term.
    starting_balance = (
        replay.balance_as_of if confirmed_view is None
        else confirmed_view.balance
    )
    projection_inputs = ProjectionInputs(
        starting_balance=starting_balance,
        starting_date=replay.next_pay_date,
        remaining_months=replay.remaining_months_as_of,
        payment_day=loan_inputs.loan_params.payment_day,
        terms_schedule=_terms_from_periods(periods),
    )
    return _ProjectionPrep(
        projection_inputs=projection_inputs,
        history_rows=history_rows,
        monthly_override=(monthly_override or None),
    )


def compute_payoff_scenarios(
    *,
    loan_inputs: LoanInputs,
    extra_monthly: Decimal,
    as_of: date,
    confirmed_view: ConfirmedLedgerView | None = None,
    extra_principal: Decimal = ZERO_MONEY,
) -> PayoffScenarios:
    """Single source of truth for the Payoff Calculator's three scenarios.

    Calls :func:`rate_period_engine.replay_schedule` ONCE to derive a
    deterministic-past slice plus the starting state, then calls
    :func:`project_forward` THREE times from the same starting
    ``(balance, date, remaining_months, rate)`` tuple, differing only
    in ``monthly_override`` and the extra applied.  The chart series
    (Original / Committed / Accelerated) and the summary metrics
    (months_saved, interest_saved, payoff dates, life-of-remaining-
    loan interest) all derive from the single return value, so chart
    and summary cannot diverge.

    Two extras (step 5).  ``extra_principal`` is the loan's STANDING
    overpayment (from ``loan_payment_settings``): it is part of the real plan,
    so it accelerates the COMMITTED and ACCELERATED slices (every forward month,
    override and contractual alike -- the engine no longer exempts override
    months).  ``extra_monthly`` is the payoff lever's ADDITIONAL what-if extra,
    previewed on top in the ACCELERATED slice only.  ``original_forward`` stays
    the pure contractual reference (no override, no extra), so committed-vs-
    original quantifies the whole plan (standing extra included) and
    accelerated-vs-committed quantifies just the lever.

    Routes projected payments forward through ``monthly_override``
    instead of relying on the engine's "apply extra when no payment
    record exists" convention -- the architectural fix for the
    "extra applied to ghost historical months" bug documented at
    ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``.
    The forward slices are all after the replay boundary, so no extra ever
    lands on a historical month.  The extra flows through PROJECTED override
    amounts, which are base-only (the standing extra is a live parameter, never
    baked into a projected shadow's stored amount), so there is no double-count
    on them.  ONE narrow edge is exempt from that guarantee: a CONFIRMED payment
    whose pay-period start is after ``as_of`` is routed to the override
    (:func:`_build_monthly_override`) carrying its FROZEN actual (base + the
    standing extra frozen at settlement), so for a loan with a standing extra
    that one month's forward chart double-applies it.  It is display-only (the
    ledger balance is authoritative) and requires marking a future-period
    payment settled -- the rare data-hygiene case the override routing names.

    Algorithm:

    1. Replay confirmed payments from the latest
       :class:`LoanAnchorEvent` via :func:`._periods._replay_from_anchor`
       (the same shared helper the resolver uses for its current balance).
    2. Replay starts at the verified anchor balance (ARM and fixed-rate
       alike).  Pre-anchor confirmed payments are filtered inside
       replay; their effect is already baked into the anchor balance.
    3. Group projected payments (and any confirmed payments past
       ``as_of``) by ``(year, month)`` for the forward overrides
       (see :func:`_build_monthly_override`).
    4. Replay produces ``history_rows``, ``balance_as_of``,
       ``next_pay_date``, ``remaining_months_as_of``, and the
       ``current_period`` (its rate and level P&I).
    5. Three forward projections share that starting state.  Each
       month's contractual P&I and rate come from the loan's full
       rate-period terms feed -- the same figures the loan card reads
       via :func:`period_for_date` -- so the schedule's projected P&I
       matches ``LoanState.monthly_payment`` by construction in every
       period, recorded recasts included (DH-#1), and ARM behavior is
       identical across the trio.
    6. Summary metrics derive from the same forward slices --
       ``months_saved`` is a length diff, ``interest_saved`` is a
       row-sum diff.

    Args:
        loan_inputs: The loan's loaded :class:`LoanInputs` bundle
            (``loan_params``, ``anchor_events``, ``payments``,
            ``rate_changes``).  ``anchor_events`` must be non-empty
            (the Commit-12 invariant); an empty list raises a
            ValueError via ``._periods.select_latest_anchor``.  The
            composer separates confirmed-pre-as_of payments (replay)
            from everything else (override) internally; the full
            rate-period terms feed governs the forward slices month by
            month.
        extra_monthly: The payoff lever's ADDITIONAL what-if extra, applied to
            every month of the ACCELERATED scenario on top of the standing
            ``extra_principal``.  ``0`` collapses accelerated to committed
            (``months_saved == 0``, ``interest_saved == 0``).
        as_of: Evaluation date.  The replay/projection boundary.
            Typically ``date.today()`` from the route.
        confirmed_view: The loan's genesis-ledger confirmed view (the read
            switch) -- its balance seeds the forward slices and its
            ledger-derived rows become ``history_rows`` -- or ``None`` to keep
            the anchor replay for both.  Threaded to
            :func:`_build_forward_inputs`; see its arg doc.  The caller reads
            it once (via ``loan_payment_service.confirmed_loan_view``) so the
            chart / summary / table all derive from the same real owed
            balance and actual history the loan card shows.
        extra_principal: The loan's STANDING monthly overpayment (from
            ``loan_payment_settings``; ``0.00`` when none).  Part of the real
            plan, so it accelerates BOTH the committed and accelerated slices
            (never the pure-contractual original).  The accelerated slice adds
            ``extra_monthly`` on top of it.

    Returns:
        A :class:`PayoffScenarios` with the three forward slices and
        the four summary metrics.

    Raises:
        ValueError: When ``loan_inputs.anchor_events`` is empty (via
            ``._periods.select_latest_anchor``).
    """
    prep = _build_forward_inputs(loan_inputs, as_of, confirmed_view)

    # All three forward slices share starting state; only override presence and
    # the extra applied vary.  Original is the pure contractual reference (no
    # extra); committed carries the standing extra_principal (the real plan);
    # accelerated adds the lever's extra_monthly on top.  Funnelling all three
    # through one primitive call shape keeps chart and summary in lockstep.
    original_forward = project_forward(
        prep.projection_inputs,
        monthly_override=None,
        extra_monthly=ZERO_MONEY,
    )
    committed_forward = project_forward(
        prep.projection_inputs,
        monthly_override=prep.monthly_override,
        extra_monthly=extra_principal,
    )
    accelerated_forward = project_forward(
        prep.projection_inputs,
        monthly_override=prep.monthly_override,
        extra_monthly=extra_principal + extra_monthly,
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
        committed_forward[-1].payment_date if committed_forward else as_of
    )
    payoff_date_accelerated = (
        accelerated_forward[-1].payment_date
        if accelerated_forward else as_of
    )

    return PayoffScenarios(
        history_rows=prep.history_rows,
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


@dataclass(frozen=True)
class TargetDateOutlook:
    """The target-date calculator's committed-plan answer (F-27).

    One :func:`_build_forward_inputs` setup drives BOTH figures, so the
    plan's payoff date and the additional-extra search rest on the same
    replay-derived starting state and override map -- they cannot
    diverge the way two independently-built projections could.

    Attributes:
        committed_payoff_date: When the user's current plan (projected
            recurring payments within their horizon, contractual
            beyond) retires the loan.  ``None`` when the loan is
            already paid off (empty committed slice).
        required_extra: The extra monthly payment needed ON TOP of the
            committed plan to retire the loan by the target date,
            applied to non-override months (the same convention the
            payoff calculator's accelerated scenario uses).  ``None``
            when the target date is in the past; ``Decimal("0.00")``
            when the plan already hits the target.
    """

    committed_payoff_date: date | None
    required_extra: Decimal | None


def target_date_outlook(
    *,
    loan_inputs: LoanInputs,
    target_date: date,
    as_of: date,
    confirmed_view: ConfirmedLedgerView | None = None,
    extra_principal: Decimal = ZERO_MONEY,
) -> TargetDateOutlook:
    """Answer "when does my plan pay off, and what extra hits my target?".

    The committed-plan half of the F-27 fix: the target-date payoff
    calculator used to binary-search the required extra against the
    CONTRACTUAL schedule only, telling a user already paying $500/mo
    over contractual that they need the full extra again.  This
    composer-level producer honors the user's projected recurring
    payments exactly the way :func:`compute_payoff_scenarios`'s
    committed/accelerated scenarios do -- same replay, same
    planned-outlay override map, same in-window convention -- and
    delegates the search to
    :func:`amortization_engine.required_extra_for_projection`.

    Step 5: the loan's STANDING ``extra_principal`` is part of the committed
    plan, so it drives ``committed_payoff_date`` and is netted out of
    ``required_extra`` -- the returned figure is the extra needed ON TOP of the
    standing overpayment, not counting it twice.

    Args:
        loan_inputs: The loan's loaded :class:`LoanInputs` bundle
            (``anchor_events`` must be non-empty, the Commit-12
            invariant).
        target_date: The user's desired payoff date.
        as_of: Evaluation date (the replay/projection boundary);
            typically ``date.today()`` from the route.
        confirmed_view: The loan's genesis-ledger confirmed view (the read
            switch), or ``None`` to keep the anchor replay.  Threaded to
            :func:`_build_forward_inputs` so the required-extra search runs
            against the real owed balance -- the same balance the loan card
            and the payoff calculator's other results show.
        extra_principal: The loan's standing monthly overpayment (``0.00`` when
            none); part of the committed plan, netted out of ``required_extra``.

    Returns:
        A :class:`TargetDateOutlook`; see its attribute docs for the
        ``None`` / ``0.00`` semantics.

    Raises:
        ValueError: When ``loan_inputs.anchor_events`` is empty (via
            ``._periods.select_latest_anchor``).
    """
    prep = _build_forward_inputs(loan_inputs, as_of, confirmed_view)

    committed_forward = project_forward(
        prep.projection_inputs,
        monthly_override=prep.monthly_override,
        extra_monthly=extra_principal,
    )
    committed_payoff_date = (
        committed_forward[-1].payment_date if committed_forward else None
    )

    # The search finds the TOTAL extra to hit the target (applied to every
    # forward month); the standing extra_principal is part of that total, so the
    # extra the user must ADD on top of their plan is the difference (never
    # negative -- a plan that already hits the target needs no more).
    total_required = required_extra_for_projection(
        prep.projection_inputs,
        target_date,
        monthly_override=prep.monthly_override,
    )
    required_extra = (
        None if total_required is None
        else max(ZERO_MONEY, total_required - extra_principal)
    )
    return TargetDateOutlook(
        committed_payoff_date=committed_payoff_date,
        required_extra=required_extra,
    )
