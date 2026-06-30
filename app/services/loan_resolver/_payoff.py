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
from app.services.rate_period_engine import monthly_due_date, period_for_date
from app.utils.money import round_money

from ._periods import (
    ZERO_MONEY,
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
    loan_inputs: LoanInputs, as_of: date,
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
    # The balance is schedule-driven: replay advances one scheduled step
    # per confirmed payment from the latest anchor, reducing principal by
    # (period P&I - interest).  The cash amount and escrow never enter,
    # so an escrow change cannot drift the recorded balance, and the
    # historical rows match the loan card's current_balance by
    # construction (both read this same engine via ``_replay_from_anchor``).
    replay = _replay_from_anchor(loan_inputs, periods, as_of)

    # Contractual P&I for the forward projection is the SAME current-
    # period level payment that drives ``LoanState.monthly_payment`` on
    # the loan card, so the card and the schedule's projected rows agree
    # by construction (both read the rate-period engine via ``as_of``).
    contractual = period_for_date(periods, as_of).period_pi

    monthly_override = _build_monthly_override(
        loan_inputs.payments or [],
        as_of,
        loan_inputs.loan_params.payment_day,
    )

    # Surface historical overpayments via the ``extra_payment`` field
    # without coupling replay back to the threshold/preparation cycle.
    # Replay returns ``extra_payment=0`` (see its docstring); applying the
    # SSOT ``contractual`` here shows the schedule's Extra column as the
    # difference between each recorded payment and the resolver's
    # monthly_payment.  A user paying exactly
    # ``state.monthly_payment + monthly_escrow`` has extra 0; a $2080
    # payment against $1580 contractual surfaces $500.  This closes the
    # D-1 divergence ("historical extra computed against original-terms
    # even for an ARM whose rate has adjusted") because ``contractual``
    # IS the ARM-aware SSOT value.
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
    projection_inputs = ProjectionInputs(
        starting_balance=replay.balance_as_of,
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
) -> PayoffScenarios:
    """Single source of truth for the Payoff Calculator's three scenarios.

    Calls :func:`rate_period_engine.replay_schedule` ONCE to derive a
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
        ValueError: When ``loan_inputs.anchor_events`` is empty (via
            ``._periods.select_latest_anchor``).
    """
    prep = _build_forward_inputs(loan_inputs, as_of)

    # All three forward slices share starting state; only override
    # presence and extra_monthly vary.  The architectural plan's
    # critical regression-prevention property -- chart and summary
    # cannot diverge -- is enforced HERE by funnelling all three
    # through the same primitive call shape.
    original_forward = project_forward(
        prep.projection_inputs,
        monthly_override=None,
        extra_monthly=ZERO_MONEY,
    )
    committed_forward = project_forward(
        prep.projection_inputs,
        monthly_override=prep.monthly_override,
        extra_monthly=ZERO_MONEY,
    )
    accelerated_forward = project_forward(
        prep.projection_inputs,
        monthly_override=prep.monthly_override,
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

    Args:
        loan_inputs: The loan's loaded :class:`LoanInputs` bundle
            (``anchor_events`` must be non-empty, the Commit-12
            invariant).
        target_date: The user's desired payoff date.
        as_of: Evaluation date (the replay/projection boundary);
            typically ``date.today()`` from the route.

    Returns:
        A :class:`TargetDateOutlook`; see its attribute docs for the
        ``None`` / ``0.00`` semantics.

    Raises:
        ValueError: When ``loan_inputs.anchor_events`` is empty (via
            ``._periods.select_latest_anchor``).
    """
    prep = _build_forward_inputs(loan_inputs, as_of)

    committed_forward = project_forward(
        prep.projection_inputs,
        monthly_override=prep.monthly_override,
        extra_monthly=ZERO_MONEY,
    )
    committed_payoff_date = (
        committed_forward[-1].payment_date if committed_forward else None
    )

    required_extra = required_extra_for_projection(
        prep.projection_inputs,
        target_date,
        monthly_override=prep.monthly_override,
    )
    return TargetDateOutlook(
        committed_payoff_date=committed_payoff_date,
        required_extra=required_extra,
    )
