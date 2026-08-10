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
)
from app.services.rate_period_engine import period_for_date
from app.utils.dates import has_settled_by
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

    * Every payment whose cash has NOT moved (``settled_on is None``),
      regardless of date.  These are the user's planned future outlays from
      recurring transfer templates; they belong on the forward side
      because they have not actually happened yet.
    * Payments settled AFTER ``as_of``.  The case this is FOR is a read of a
      PAST date, where a payment settled since is correctly still a projection
      at that date.  It is not claimed empty for a today-read: the write door
      does refuse a future settle day, but against a different clock and it is
      not the only writer -- see :func:`compute_payoff_scenarios`, which states
      that in full rather than leaving a guarantee here it cannot keep.

    Two dates with distinct jobs (the same split ``replay_schedule`` makes):

    * The replay/projection CUT keys on the SETTLED day, through the same
      :func:`~app.utils.dates.has_settled_by` predicate
      ``replay_schedule`` caps on, so a payment is never in BOTH halves and
      never dropped because the two spellings disagreed.  (It can still be in
      neither: ``replay_schedule`` further drops a candidate an anchor subsumes
      or that falls past a payoff, and this function correctly does not take
      those back -- see that function.)  The cut keyed on the pay-period start
      until plan step **X-an** -- the FUNDING basis, which the ledger does not
      use -- and finding **N-187** is what that cost: a payment settled before
      its funding period began was already paid down in the ledger balance
      seeding this projection AND planned here, so its installment was paid
      twice.
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
            Mixed settled/projected; the function filters
            internally.
        as_of: Cutoff date used to separate replay history from
            forward projection.  Payments settled at or before ``as_of``
            are consumed by replay and excluded here.

    Returns:
        A dict mapping ``(year, month) -> Decimal`` total payment.
        Empty dict when no projection-eligible payment exists.
    """
    override: dict[tuple[int, int], Decimal] = {}
    for payment in payments:
        # A payment whose cash has already moved by as_of belongs to replay,
        # not projection -- exclude it.  Everything else (payments not yet
        # settled + payments settled after as_of) is a forward-only concept.
        # ONE predicate, shared with replay_schedule's as_of cap, so the split
        # is a property of one rule rather than of two comparisons that happen
        # to agree (plan step X-an).
        if has_settled_by(payment.settled_on, as_of):
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
            contractual months.  **That agreement is what plan step X-an
            established, and it did not hold before** (finding **N-187**): the
            two producers cut history at different dates, so a payment settled
            outside its own pay period was the ledger's last one and not the
            replay's, seeding a balance one installment ahead of the date and
            month count derived beside it.

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
    on them.  ONE narrow edge is exempt from that guarantee: a SETTLED payment
    whose settle day is after ``as_of`` is routed to the override
    (:func:`_build_monthly_override`) carrying its FROZEN actual (base + the
    standing extra frozen at settlement), so for a loan with a standing extra
    that one month's forward chart double-applies it.  It is display-only (the
    ledger balance is authoritative), and plan step **X-an** narrowed WHEN it
    can arise: the edge used to be any payment settled before its pay period
    began, which is an ordinary early payment rather than a data-hygiene case
    (finding **N-187**).  What is left is a read of a PAST date whose loan has
    been paid since -- where treating the payment as a projection is the correct
    answer for that date, and only the frozen extra inside its amount is off.

    **It is not claimed unreachable for a today-read, deliberately.**  The
    write door refuses a future settle day
    (:func:`app.services.status_seam.reject_future_settle_day`) against
    ``display_today()`` while ``BalanceContext``'s default ``as_of`` is
    ``date.today()``, and finding **N-191** records that those are two clocks
    with no rule tying them; and the door is not the only writer -- a bulk
    ``query.update`` reaches ``settled_on`` without passing it.  "Unreachable
    through the seam" is what can be said, and that is weaker than unreachable.

    Algorithm:

    1. Replay confirmed payments from the latest
       :class:`LoanAnchorEvent` via :func:`._periods._replay_from_anchor`
       (the same shared helper the resolver uses for its current balance).
    2. Replay starts at the verified anchor balance (ARM and fixed-rate
       alike).  Pre-anchor confirmed payments are filtered inside
       replay; their effect is already baked into the anchor balance.
    3. Group the payments whose cash has not moved by ``as_of`` by
       ``(year, month)`` for the forward overrides
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
            composer separates payments SETTLED by ``as_of`` (replay)
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
            it once (via ``balance_at.confirmed_view``) so the
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
