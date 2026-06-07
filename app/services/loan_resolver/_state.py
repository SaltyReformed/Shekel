"""Loan-resolver state: the current (balance, payment, schedule, payoff) producer.

:func:`resolve_loan` is the single-source-of-truth producer every loan-touching
surface reads through; :func:`compute_monthly_payment_baseline` is the
cheaper "what does the user pay each month" lookup that skips the balance
replay and schedule generation.

Pure: no Flask, no ``db.session``; the caller loads the data and passes it in.
"""

import dataclasses
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.amortization_engine import (
    AmortizationRow,
    PaymentRecord,
    RateChangeRecord,
)
from app.services.rate_period_engine import period_for_date
from app.utils.money import round_money

from ._payoff import compute_payoff_scenarios
from ._periods import (
    ZERO_MONEY,
    LoanInputs,
    _replay_from_anchor,
    _resolve_periods,
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
    # Pylint: ``unused-argument`` -- ``anchor_events`` and ``payments`` are
    # unused: the current period's level P&I is anchor-independent -- a
    # property of the loan's contractual rate-period structure, not of the
    # running balance.  Both stay in the signature for caller compatibility
    # (loan_payment_service.compute_contractual_pi passes them).
    # pylint: disable=unused-argument
    return period_for_date(
        _resolve_periods(loan_params, rate_changes), as_of,
    ).period_pi


def resolve_loan(loan_inputs: LoanInputs, as_of: date) -> LoanState:
    """Resolve a loan to its (balance, payment, schedule, payoff, interest).

    Single-source-of-truth producer for every loan-touching surface.
    Replays confirmed payments forward from the latest
    :class:`LoanAnchorEvent` to derive the current balance; computes
    the monthly payment per the ARM-window-aware rules documented at
    package scope; generates the full schedule via
    :func:`._payoff.compute_payoff_scenarios` (the "Committed with no
    extra" composition: ``history_rows + committed_forward``); derives
    the payoff date and total interest from the same schedule.

    The function is pure: it takes plain data (the :class:`LoanInputs`
    bundle of model instances and plain Python lists), returns a frozen
    :class:`LoanState`, and performs no I/O.  This honors the services
    boundary so the resolver is safe to call from any layer (route,
    service, test) and produces deterministic output for a given input.

    Algorithm (see the package docstring for the full rationale):

    1. Pick the latest anchor by ``(anchor_date, created_at)`` DESC.
    2. Filter ``loan_inputs.payments`` to confirmed entries;
       ``replay_schedule`` then keeps those whose true monthly due date
       is after the anchor date and whose pay period has begun by
       ``as_of``.  Projected (unconfirmed) payments are NOT replayed --
       they are future commitments, not historical fact.
    3. Generate the schedule via :func:`._payoff.compute_payoff_scenarios`
       with ``extra_monthly=0`` and the confirmed-only payment list.
       ARM vs. fixed-rate anchor handling lives inside the composer
       (Phase 6 of the amortization-engine split); the resolver no
       longer reaches the engine directly.
       ``LoanState.schedule = history_rows + committed_forward``.
    4. Derive the current balance from the anchor + confirmed-payment
       replay via :func:`._periods._replay_from_anchor` (independent of
       the schedule walk -- the resolver owns its balance derivation so a
       future projection change cannot silently change
       ``state.current_balance``).
    5. Compute the monthly payment per ARM-in-window vs.
       ARM-out-of-window vs. fixed-rate rules.
    6. Return the LoanState; consumers read its fields without
       recomputing.

    Args:
        loan_inputs: The loan's loaded :class:`LoanInputs` bundle
            (``loan_params``, ``anchor_events``, ``payments``,
            ``rate_changes``).  ``anchor_events`` must be non-empty
            (the Commit-12 invariant); an empty list raises a
            ValueError.  Only confirmed payments are replayed.
        as_of: The evaluation date.  Drives the current-balance walk
            and the out-of-window monthly-payment computation.

    Returns:
        A :class:`LoanState` with the five resolver fields.

    Raises:
        ValueError: When ``loan_inputs.anchor_events`` is empty (the
            Commit-12 invariant is violated and the caller's data is bad).
    """
    # Filter payments to confirmed only.  An unconfirmed payment is a
    # Projected transfer the user has not yet marked received/settled; it
    # is a future commitment, not a historical fact, so it must not reduce
    # the principal.  The anchor boundary itself (and the as-of cap) is
    # owned by replay_schedule, which classifies each payment by its true
    # monthly due date: a pay-period start can fall up to ~2 weeks before
    # the contractual due date, so filtering on it HERE would strand a
    # payment whose pay period straddles a mid-period balance true-up.
    confirmed = [
        payment
        for payment in (loan_inputs.payments or [])
        if payment.is_confirmed
    ]

    periods = _resolve_periods(
        loan_inputs.loan_params, loan_inputs.rate_changes,
    )

    # Schedule generation routes through the scenario composer
    # (Phase 6 of the amortization-engine split -- architectural plan:
    # ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``).
    # ``compute_payoff_scenarios`` calls ``replay_schedule``
    # once and ``project_forward`` once with ``extra_monthly=0`` to
    # produce a "Committed with no extra" composition;
    # ``LoanState.schedule`` is the concatenation of the confirmed-
    # history rows and the forward-projected rows.  ARM vs. fixed-rate
    # anchor handling is owned by the composer (it inspects
    # ``loan_params.is_arm`` and forwards the anchor to replay for ARM
    # only -- the same is_arm-gated passthrough the prior direct
    # engine call implemented inline above).  Passing a confirmed-only
    # ``payments`` view keeps the resolver's confirmed-only contract
    # intact: every entry feeds replay (which drops pre-anchor entries
    # by due date), none becomes a forward override.  Fixed-rate trueups
    # remain a follow-up: see F-8 in
    # ``docs/audits/financial_calculations/remediation_follow_up.md``.
    scenarios = compute_payoff_scenarios(
        loan_inputs=dataclasses.replace(loan_inputs, payments=confirmed),
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
    # via the same ``_replay_from_anchor`` the composer uses, but as its
    # own call independent of the schedule generation above so a future
    # projection change cannot silently move it.
    current_balance_full = _replay_from_anchor(
        loan_inputs, periods, as_of,
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
        payoff_date = loan_inputs.loan_params.origination_date
        total_interest_full = ZERO_MONEY

    return LoanState(
        current_balance=round_money(current_balance_full),
        monthly_payment=monthly_payment,
        schedule=schedule,
        payoff_date=payoff_date,
        total_interest=round_money(total_interest_full),
    )
