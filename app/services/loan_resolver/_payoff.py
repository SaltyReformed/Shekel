"""Loan-resolver payoff composer: the confirmed history and the CONTRACT's forward.

Replays the past once and projects the contract's remaining installments from
the resulting starting state, so a loan page's x-axis, its confirmed half and
its contract-versus-plan comparison derive from one return value.

**It composed three scenarios -- Original / Committed / Accelerated -- until
plan step R7d-g-3** (ruling **R-R88**): the committed slice routed the loan's
projected transfer rows through ``project_forward``'s ``monthly_override`` and
priced the months no row covered from the contract plus ONE picked definition's
extra, and the accelerated slice added the pay-off-sooner lever's what-if on
top.  Both were a SECOND forward walk beside the balance seam's plan fold
(``balance_at._plan`` / ``_plan_fold``), which prices every definition's own
occurrences; the seam's fold is the one forward walk now, read by the loan
page through ``balance_at.loan_installments`` / ``positions`` /
``loan_what_if_owed_at_dates``.  What stays here is what the fold does not
produce: the ledger-derived confirmed rows and the pure contractual reference.

Pure: no Flask, no ``db.session``; the caller loads the data and passes it in.
"""

import dataclasses
from dataclasses import dataclass
from datetime import date

from app.services.amortization_engine import (
    AmortizationRow,
    ProjectionInputs,
    project_forward,
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
class PayoffScenarios:
    """The confirmed history and the contract's forward, from ONE replay.

    Frozen because the composer returns a snapshot the caller renders;
    every consumer reads from one instance, so the confirmed half and the
    contractual reference cannot diverge by construction.  Two of what were
    ten fields until plan step R7d-g-3 -- see the module docstring for the
    slices and metrics that moved to the balance seam's fold.

    Attributes:
        history_rows: Confirmed-payment rows from origination (or the
            latest anchor) through ``as_of``.  Every row carries
            ``is_confirmed=True``.  Empty when no confirmed payments
            exist at or before ``as_of``.
        original_forward: Pure contractual amortization from
            ``replay.balance_as_of`` forward -- no plan, no extra.  Models
            "what the lender would amortize the remaining balance to" if the
            user paid exactly the contractual P&I every month: the band
            chart's x-axis and the lever's "current plan vs. original"
            reference.
    """

    history_rows: list[AmortizationRow]
    original_forward: list[AmortizationRow]


@dataclass(frozen=True)
class _ProjectionPrep:
    """The replay-derived inputs the payoff composer builds its result from.

    Produced once by :func:`_build_forward_inputs` so
    :func:`compute_payoff_scenarios` reads two values from one local
    instead of threading the replay, contractual P&I, and rate-period set
    through its body, leaving the composer a thin "project, then bundle"
    orchestrator.

    Attributes:
        projection_inputs: The :class:`ProjectionInputs` the contractual
            forward projects from -- starting balance, date, remaining
            months, and the rate-period terms feed (each month's SSOT rate
            and contractual P&I).
        history_rows: The confirmed-payment history slice (origination or
            latest anchor through ``as_of``), each row's ``extra_payment``
            surfaced against the SSOT contractual payment.
    """

    projection_inputs: ProjectionInputs
    history_rows: list[AmortizationRow]


def _build_forward_inputs(
    loan_inputs: LoanInputs,
    as_of: date,
    confirmed_view: ConfirmedLedgerView | None = None,
) -> _ProjectionPrep:
    """Replay the past and assemble the contractual forward's inputs.

    The single setup phase of :func:`compute_payoff_scenarios`: replay
    confirmed payments from the latest anchor, derive the SSOT
    contractual P&I, surface historical overpayments on the history rows,
    and build the :class:`ProjectionInputs` the contract's forward projects
    from.  (It built a planned-outlay override map for two further slices
    too, until plan step R7d-g-3 made the balance seam's fold the one
    planned walk.)

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
        A :class:`_ProjectionPrep` with the projection inputs and the
        confirmed-payment history slice.

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
    # The replay reads three dates per payment and no amount, so it is handed
    # the feed's DATES (plan step balance:X-bl-2b) -- an attribute read off the
    # priced records this bundle carries for the forward override, never a
    # second projection of them.
    replay = _replay_from_anchor(
        anchor_events=loan_inputs.anchor_events,
        periods=periods,
        payments=[payment.dates for payment in loan_inputs.payments or []],
        payment_day=loan_inputs.loan_params.payment_day,
        as_of=as_of,
    )

    # Contractual P&I for the forward projection is the SAME current-
    # period level payment that drives ``LoanState.monthly_payment`` on
    # the loan card, so the card and the schedule's projected rows agree
    # by construction (both read the rate-period engine via ``as_of``).
    contractual = period_for_date(periods, as_of).period_pi

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
    )


def compute_payoff_scenarios(
    *,
    loan_inputs: LoanInputs,
    as_of: date,
    confirmed_view: ConfirmedLedgerView | None = None,
) -> PayoffScenarios:
    """Replay the confirmed past once and project the CONTRACT forward from it.

    Calls :func:`rate_period_engine.replay_schedule` ONCE to derive a
    deterministic-past slice plus the starting state, then
    :func:`project_forward` once for the pure contractual reference.

    **It projected the owner's PLAN too until plan step R7d-g-3** (ruling
    **R-R88**): the committed and accelerated slices routed the projected
    transfer rows through ``monthly_override`` and applied a loan-level
    ``extra_principal`` and the lever's ``extra_monthly`` on top.  The
    override amounts were priced by amount rule 4 with each definition's
    extra INSIDE them (since ``balance:X-au-g-2c-1``), so the loan-level
    extra paid twice on every row-covered month (measured 2026-09-14: P&I
    ``$526.46`` + a ``$100`` extra arrived as ``$626.46`` and the month then
    paid ``$676.46``), and the months no row covered were priced from the
    contract plus ONE picked definition's extra (plan ledger row **D49**).
    The balance seam's plan fold already priced every definition's own
    occurrences; it is the one forward walk now
    (:func:`app.services.balance_at.loan_installments`,
    :func:`app.services.balance_at.loan_what_if_owed_at_dates`), and this
    composer keeps what the fold does not produce.

    Algorithm:

    1. Replay confirmed payments from the latest
       :class:`LoanAnchorEvent` via :func:`._periods._replay_from_anchor`
       (the same shared helper the resolver uses for its current balance).
    2. Replay starts at the verified anchor balance (ARM and fixed-rate
       alike).  Pre-anchor confirmed payments are filtered inside
       replay; their effect is already baked into the anchor balance.
    3. Replay produces ``history_rows``, ``balance_as_of``,
       ``next_pay_date``, ``remaining_months_as_of``, and the
       ``current_period`` (its rate and level P&I).
    4. The contractual forward projects from that starting state.  Each
       month's contractual P&I and rate come from the loan's full
       rate-period terms feed -- the same figures the loan card reads
       via :func:`period_for_date` -- so the projected P&I matches
       ``LoanState.monthly_payment`` by construction in every period,
       recorded recasts included (DH-#1).

    Args:
        loan_inputs: The loan's loaded :class:`LoanInputs` bundle
            (``loan_params``, ``anchor_events``, ``payments``,
            ``rate_changes``).  ``anchor_events`` must be non-empty
            (the Commit-12 invariant); an empty list raises a
            ValueError via ``._periods.select_latest_anchor``.  The
            replay reads the payments' DATES alone -- which are settled by
            ``as_of`` -- and no amount.
        as_of: Evaluation date.  The replay/projection boundary.
            Typically ``date.today()`` from the route.
        confirmed_view: The loan's genesis-ledger confirmed view (the read
            switch) -- its balance seeds the forward slice and its
            ledger-derived rows become ``history_rows`` -- or ``None`` to keep
            the anchor replay for both.  Threaded to
            :func:`_build_forward_inputs`; see its arg doc.  The caller reads
            it once (via ``balance_at.confirmed_view``) so the history and
            the contractual reference derive from the same real owed
            balance the loan card shows.

    Returns:
        A :class:`PayoffScenarios` with the confirmed history and the
        contractual forward.

    Raises:
        ValueError: When ``loan_inputs.anchor_events`` is empty (via
            ``._periods.select_latest_anchor``).
    """
    prep = _build_forward_inputs(loan_inputs, as_of, confirmed_view)
    return PayoffScenarios(
        history_rows=prep.history_rows,
        original_forward=project_forward(prep.projection_inputs),
    )
