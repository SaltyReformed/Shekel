"""Loan-resolver state: the current (balance, payment, schedule, payoff) producer.

:func:`resolve_loan` is the single-source-of-truth producer every loan-touching
surface reads through; :func:`compute_monthly_payment_baseline` is the
cheaper "what does the user pay each month" lookup that skips the balance
replay and schedule generation.

Pure: no Flask, no ``db.session``; the caller loads the data and passes it in.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.amortization_engine import (
    AmortizationRow,
    RateChangeRecord,
)
from app.services.rate_period_engine import period_for_date
from app.utils.money import round_money

from ._payoff import compute_payoff_scenarios
from ._periods import (
    ZERO_MONEY,
    ConfirmedLedgerView,
    LoanInputs,
    _replay_from_anchor,
    resolve_periods,
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
        current_balance: What the loan owes AS OF ``as_of`` -- the balance
            after replaying confirmed payments forward from the latest anchor.
            Display this instead of ``LoanParams.current_principal``.
            ``Decimal("0.00")`` for a loan whose ``origination_date`` is after
            ``as_of``: it does not exist yet, so it owes nothing (see
            :func:`resolve_loan`).  This is NOT the forward projection's seed --
            for a not-yet-originated loan the two differ, and the seed is the
            seam's (:attr:`~app.services.net_worth_kernel.DebtSchedule.projection_seed`).
        monthly_payment: P&I payment as of ``as_of``.  For an ARM
            inside its fixed-rate window this is held constant for
            every ``as_of`` in the window (E-02 invariant).  Outside
            the window or for a fixed-rate loan this is the
            contractual / re-amortized payment per the resolver
            algorithm in the module docstring.
        current_rate: Annual interest rate in effect on ``as_of`` (a
            decimal fraction, e.g. ``Decimal("0.06875")``) -- the
            governing rate period's rate.  The single source of truth
            for "the loan's current rate" that DH-#56 retired the
            ``LoanParams.interest_rate`` column in favour of: every
            display and money surface (loan card, /savings cards,
            debt-strategy accrual, payoff/refinance calculators) reads
            this instead of the stored column.
        schedule: Full amortization schedule, with confirmed rows
            reflecting actual paid amounts and projected rows paying
            each month's rate-period level P&I (recorded recast or
            schedule-derived -- the same figures this card displays).
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
    current_rate: Decimal
    schedule: list[AmortizationRow]
    payoff_date: date
    total_interest: Decimal


def compute_monthly_payment_baseline(
    loan_params,
    rate_changes: list[RateChangeRecord] | None,
    as_of: date,
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
    the running balance, so no anchor or payment feed is taken (the
    read-switch arc's final commit dropped the old unused
    compatibility parameters).

    Args:
        loan_params: Loan parameter object exposing the fields
            :func:`build_rate_periods` reads (origination, principal,
            base rate, term, ARM cadence).
        rate_changes: Optional ARM rate-history feeding each period's
            rate and any recorded recast P&I.  ``None`` or empty for a
            fixed-rate loan.
        as_of: Evaluation date; selects the governing rate period.

    Returns:
        Rounded Decimal monthly P&I, equal to
        ``resolve_loan(...).monthly_payment`` for the same inputs.
    """
    return period_for_date(
        resolve_periods(loan_params, rate_changes), as_of,
    ).period_pi


def current_rate_baseline(
    loan_params,
    rate_changes: list[RateChangeRecord] | None,
    as_of: date,
) -> Decimal:
    """Return the loan's current annual interest rate -- the rate-period rate.

    Single source of truth for "what rate is in effect on ``as_of``" for callers
    that need the rate WITHOUT the full balance replay and schedule generation
    :func:`resolve_loan` runs -- the standalone amortization-schedule route,
    whose ARM rate column falls back to it for rows carrying no per-row rate, and
    which composes its own schedule (so a full resolve just to read the rate
    would derive the schedule twice).  Returns the same value as
    ``resolve_loan(...).current_rate`` for the same inputs -- the governing rate
    period's annual rate (DH-#56: the resolver-derived rate that replaced the
    retired ``LoanParams.interest_rate`` column) -- by the same cheap rate-period
    lookup :func:`compute_monthly_payment_baseline` uses for the payment.

    Args:
        loan_params: Loan parameter object exposing the fields
            :func:`build_rate_periods` reads (origination, principal,
            base rate, term, ARM cadence).
        rate_changes: Optional ARM rate-history feeding each period's
            rate.  ``None`` or empty for a fixed-rate loan.
        as_of: Evaluation date; selects the governing rate period.

    Returns:
        The Decimal annual rate (a fraction, e.g. ``Decimal("0.06000")``),
        equal to ``resolve_loan(...).current_rate`` for the same inputs.
    """
    return period_for_date(
        resolve_periods(loan_params, rate_changes), as_of,
    ).annual_rate


def resolve_loan(
    loan_inputs: LoanInputs,
    as_of: date,
    confirmed_view: ConfirmedLedgerView | None = None,
    extra_principal: Decimal = ZERO_MONEY,
) -> LoanState:
    """Resolve a loan to its (balance, payment, schedule, payoff, interest).

    Single-source-of-truth producer for every loan-touching surface.
    Replays confirmed payments forward from the latest
    :class:`LoanAnchorEvent` to derive the current balance; computes
    the monthly payment per the ARM-window-aware rules documented at
    package scope; generates the full schedule via
    :func:`._payoff.compute_payoff_scenarios` (the COMMITTED, plan-aware
    composition ``history_rows + committed_forward``, honoring the projected
    recurring payments and the standing ``extra_principal``); derives the payoff
    date and total interest from the same schedule.

    The function is pure: it takes plain data (the :class:`LoanInputs`
    bundle of model instances and plain Python lists), returns a frozen
    :class:`LoanState`, and performs no I/O.  This honors the services
    boundary so the resolver is safe to call from any layer (route,
    service, test) and produces deterministic output for a given input.

    Algorithm (see the package docstring for the full rationale):

    1. Pick the latest anchor by ``(anchor_date, created_at)`` DESC.
    2. Generate the schedule via :func:`._payoff.compute_payoff_scenarios`
       with the FULL payment list and the standing ``extra_principal``
       (``extra_monthly=0``: the payoff lever's what-if extra is not part of
       the committed plan).  The composer replays confirmed-pre-``as_of``
       payments and routes everything else (projected recurring payments, any
       confirmed payment past ``as_of``) forward through ``monthly_override``,
       applying the standing extra to every forward month.  ARM vs. fixed-rate
       anchor handling lives inside the composer (Phase 6 of the
       amortization-engine split); the resolver no longer reaches the engine
       directly.
    3. ``LoanState.schedule = history_rows + committed_forward`` -- the
       COMMITTED, plan-aware trajectory (step 8).  Projected (unconfirmed)
       payments never reduce the current balance (step 4 derives it
       independently); they surface only in this forward schedule, as planned
       commitments, not historical fact.
    4. Derive the current balance from the anchor + confirmed-payment
       replay via :func:`._periods._replay_from_anchor` (independent of
       the schedule walk -- the resolver owns its balance derivation so a
       future projection change cannot silently change
       ``state.current_balance``) -- unless *as_of* PRECEDES the loan's
       ``origination_date``, in which case the loan does not exist yet and
       owes ``0.00``.  See the inline comment: the replay's anchor selection
       is not ``as_of``-filtered, so without this guard a loan configured
       before it closes reports its full principal as owed today.
    5. Compute the monthly payment per ARM-in-window vs.
       ARM-out-of-window vs. fixed-rate rules.
    6. Return the LoanState; consumers read its fields without
       recomputing.

    Args:
        loan_inputs: The loan's loaded :class:`LoanInputs` bundle
            (``loan_params``, ``anchor_events``, ``payments``,
            ``rate_changes``).  ``anchor_events`` must be non-empty
            (the Commit-12 invariant); an empty list raises a
            ValueError.  Only confirmed payments are replayed into the
            balance; projected payments feed the committed forward schedule.
        as_of: The evaluation date.  Drives the current-balance walk
            and the out-of-window monthly-payment computation.
        confirmed_view: The loan's genesis-ledger confirmed view (the read
            switch), or ``None`` to keep the anchor replay.  When supplied,
            its ``balance`` becomes BOTH the ``current_balance`` AND the
            forward projection's starting balance, and its ``history_rows``
            become the schedule's confirmed slice (threaded once to
            :func:`._payoff.compute_payoff_scenarios`) -- one bundle, so the
            headline balance, the schedule's history, and the projection
            cannot desync off-schedule.  ``None`` leaves this function on the
            anchor replay unchanged (an unconfigured loan, or a caller that
            deliberately reads the schedule balance -- e.g. the "ever paid
            off" ``date.max`` probe).
        extra_principal: The loan's standing monthly overpayment (from
            ``loan_payment_settings``; ``Decimal("0.00")`` when none), applied
            to every forward month of the committed schedule so the payoff date,
            total interest, and forward balances reflect the real plan (step 8).
            The summary read path (``loan_resolution.resolve_loan_bundle``) loads
            it centrally via
            :func:`recurring_transfer_query.loan_standing_extra_for_account` and
            threads it into ``resolve_loan_seeded``; a direct caller (e.g. the
            ``date.max`` probe) may leave it ``0.00``.

    Returns:
        A :class:`LoanState` with the five resolver fields.

    Raises:
        ValueError: When ``loan_inputs.anchor_events`` is empty (the
            Commit-12 invariant is violated and the caller's data is bad).
    """
    periods = resolve_periods(
        loan_inputs.loan_params, loan_inputs.rate_changes,
    )

    # Schedule generation routes through the scenario composer
    # (Phase 6 of the amortization-engine split -- architectural plan:
    # ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``).
    # ``compute_payoff_scenarios`` calls ``replay_schedule`` once (confirmed
    # history, balance, remaining term) and ``project_forward`` once to build
    # the COMMITTED trajectory: it partitions the FULL ``payments`` view into
    # confirmed-pre-``as_of`` (replayed) and everything else (projected
    # recurring payments + any confirmed payment past ``as_of``, routed forward
    # through ``monthly_override``), then applies the standing ``extra_principal``
    # to every forward month.  ``extra_monthly=0`` because the payoff lever's
    # what-if extra is NOT part of the committed plan.  ``LoanState.schedule`` is
    # the confirmed-history rows plus that committed forward slice -- the loan's
    # real plan, not the lender minimum (the step-8 seam fix,
    # ``docs/design/escrow_line_identity_refactor.md`` Sec. 16).  ARM vs.
    # fixed-rate anchor handling is owned by the composer.  The current balance
    # below is derived INDEPENDENTLY (an unconfirmed payment never reduces it),
    # so routing projected payments forward here cannot move ``current_balance``.
    # Fixed-rate trueups remain a follow-up: see F-8 in
    # ``docs/audits/financial_calculations/remediation_follow_up.md``.
    scenarios = compute_payoff_scenarios(
        loan_inputs=loan_inputs,
        extra_monthly=ZERO_MONEY,
        as_of=as_of,
        confirmed_view=confirmed_view,
        extra_principal=extra_principal,
    )
    schedule = list(scenarios.history_rows) + list(
        scenarios.committed_forward
    )

    # Current balance: the genesis-ledger confirmed balance when the read
    # switch supplies a view, else the schedule-replay balance.  The replay
    # advances one scheduled step per confirmed payment from the latest
    # anchor (principal = period P&I - interest), discarding the cash and
    # escrow.  Threading ONE ``confirmed_view`` here AND into the composer
    # above (never two mechanisms) keeps this headline balance, the
    # schedule's confirmed rows, and the forward seed identical, so they
    # cannot desync off-schedule.  The schedule-replay call stays independent
    # of the composer's own so a future projection change cannot silently
    # move the unseeded balance.
    if as_of < loan_inputs.loan_params.origination_date:
        # A loan does not exist before it originates, and cannot owe anything.
        # The replay would answer otherwise: ``select_latest_anchor`` picks the
        # latest anchor BY DATE with no ``as_of`` filter, so a loan resolved
        # today seeds its balance from an origination anchor dated in the
        # FUTURE and reports its full principal as owed NOW.  Measured: a
        # $200,000 mortgage originating 2026-07-01, read on 2026-03-20, showed
        # $200,000 owed at every pay period back to January -- four months
        # before it closed.
        #
        # This guard is the resolver's half of ONE rule the whole loan stack
        # keeps: a loan owes nothing until it originates.  The rule is asked of
        # the FACT -- ``origination_date`` -- everywhere it is asked, so every
        # asker agrees by construction rather than by luck.  The genesis walk
        # deliberately does NOT apply it: it RECORDS every anchor whatever its
        # date and leaves "has this happened yet?" to the readers
        # (``loan_ledger.walk_loan_ledger``).  ``loan_payment_service.confirmed_loan_view``
        # is the reader that applies it here, withholding a ledger view for a
        # loan that has not originated -- which is what keeps the branch below
        # on the replay, and what keeps the ledger's honest "nothing has
        # happened" 0.00 out of the projection's seed.
        #
        # It guards the BALANCE only, deliberately NOT ``_replay_from_anchor``:
        # ``compute_payoff_scenarios`` shares that replay for the SCHEDULE's
        # starting state, and filtering the anchor out there would collapse the
        # loan's whole forward schedule to nothing.  "What is owed now" and
        # "what balance does the schedule start from" are two questions, and
        # this is where they separate.  The projection's seed is supplied by
        # the seam (``net_worth_kernel.DebtSchedule.projection_seed``).
        current_balance_full = ZERO_MONEY
    else:
        current_balance_full = (
            _replay_from_anchor(loan_inputs, periods, as_of).balance_as_of
            if confirmed_view is None
            else confirmed_view.balance
        )

    # Monthly P&I is the current rate period's level payment, held
    # constant within the period and recast only at an adjustment
    # boundary -- independent of the anchor balance, so a balance
    # true-up never moves the displayed payment.  The same period's
    # rate is the loan's current rate (DH-#56: the resolver-derived
    # source of truth that replaced the LoanParams.interest_rate column).
    current_period = period_for_date(periods, as_of)
    monthly_payment = current_period.period_pi
    current_rate = current_period.annual_rate

    # Derive payoff_date and total_interest from the single
    # schedule generation (DRY -- no second engine call).
    if schedule:
        payoff_date = schedule[-1].payment_date
        total_interest_full = sum(
            (row.interest for row in schedule), ZERO_MONEY,
        )
    else:
        payoff_date = loan_inputs.loan_params.origination_date
        total_interest_full = ZERO_MONEY

    return LoanState(
        current_balance=round_money(current_balance_full),
        monthly_payment=monthly_payment,
        current_rate=current_rate,
        schedule=schedule,
        payoff_date=payoff_date,
        total_interest=round_money(total_interest_full),
    )
