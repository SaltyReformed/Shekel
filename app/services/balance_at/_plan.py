"""Balance-at-T seam -- a loan's forward PLAN: payment RECORDS, not schedule rows.

Plan step **C6a** (``docs/audits/balance_architecture/README.md``).  A loan's
future balance is a fold over the payments it is going to make, in two tiers:

* **PLANNED** -- the loan's PROJECTED transfer shadows
  (:func:`app.services.loan_loaders.projected_income_shadows`), each at its LIVE
  D3 cash (:func:`app.services.loan_payment_service.live_loan_transfer_amounts` =
  P&I + current escrow + ``extra_principal``, the SAME cash the checking side
  shows leaving).  A record is the evidence a payment will happen; where the
  record's due date has already passed but it has not settled, it is clamped
  forward to ``as_of + 1d`` -- "a plan cannot have already happened" (ruling D1).
* **ESTIMATED** -- for every FUTURE contractual installment slot no projected
  record covers (a loan with no recurring transfer, or the tail beyond the
  materialized ~2-year pay-period window), the contractual P&I synthesized from
  :func:`app.services.loan_resolution.contractual_schedule_from_origination` (the
  producer already shared with the property-equity back-projection) -- its
  installment DATE and P&I, never its ``remaining_balance``, which this re-folds.

**Why this replaces the schedule walk (finding B-9).**  The retired
:func:`app.services.account_projection.forward_balance_at_date` amortized one
contractual installment per month whether or not any payment was recorded, so an
overdue installment nobody paid still paid the loan down.  Here an installment
pays the loan down only where a payment RECORD stands behind it (PLANNED) or where
the loan is genuinely predicted to keep paying (a FUTURE ESTIMATED slot); an
overdue slot with no record is neither, so it holds flat -- honest delinquency.

**Why in the seam, not the ``loan_ledger`` leaf.**  The plan composes the loan's
projected records, its live D3 cash, and the resolver's contractual schedule --
all above the pure leaf -- so it is a SEAM responsibility, exactly as
:func:`app.services.balance_at.positions` composes the past fold with the forward
projection.  The one arithmetic it shares with the leaf is
:func:`app.services.loan_ledger.split_payment_cash`, so a PLANNED / ESTIMATED
payment splits its cash byte-identically to an ACTUAL settled one.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models.account import Account
from app.services import escrow_calculator, loan_loaders, loan_resolver
from app.services.loan_ledger import (
    confirmed_shadows_through,
    sample_cumulative,
    split_payment_cash,
)
from app.services.loan_loaders import loan_payment_due_date
from app.services.loan_payment_service import live_loan_transfer_amounts
from app.services.loan_resolution import contractual_schedule_from_origination
from app.services.rate_period_engine import period_for_date
from app.services.resolution_context import BalanceContext, require_scenario

_ZERO_MONEY = Decimal("0.00")
_ONE_DAY = timedelta(days=1)


def _month_slot(due: date) -> tuple[int, int]:
    """Return the ``(year, month)`` installment slot a due date occupies.

    The de-dup key that keeps one calendar installment folded once: a contractual
    ESTIMATED synthesis is skipped when a PLANNED record or a settled payment
    already occupies its month (the same month-keyed slot the C3c interest merge
    uses, ``_loan_interest._due_slot``).
    """
    return (due.year, due.month)


@dataclass(frozen=True)
class PlannedPayment:
    """One forward payment a loan is projected to make -- a RECORD, not a balance.

    The unit of :func:`loan_plan`.  It carries the cash a payment will move and
    the three facts the fold needs to split it (:func:`split_payment_cash`), plus
    the two dates the projection keys on -- but NO balance: the balance is the
    FOLD of these, computed by :func:`fold_forward`, never stored on a record.

    Attributes:
        due_date: The contractual installment this payment satisfies (contract
            time).  Orders the split walk and keys the PLANNED-vs-ESTIMATED slot
            de-dup, so a late or clamped settlement never re-splits an installment
            (ruling R-A).
        effective_date: When the paydown becomes VISIBLE to a balance read --
            ``max(due_date, as_of + 1d)`` (ruling D1: a plan cannot have already
            happened).  For a normal future installment this is its due date; for
            an overdue-but-still-projected one it is tomorrow.
        cash: The cash this payment moves -- the PLANNED tier's live D3 amount, or
            the ESTIMATED tier's contractual P&I.
        escrow: The monthly escrow embedded in ``cash`` (``0.00`` for an ESTIMATED
            payment, whose contractual P&I carries no escrow), backed out so
            ``principal = cash - interest - escrow``.
        annual_rate: The annual rate governing this installment's interest accrual
            (resolved at the payment's rate period).
        is_estimated: ``True`` for a synthesized contractual installment, ``False``
            for a real projected-shadow record -- carried for display / debugging;
            the fold treats both alike.
    """

    due_date: date
    effective_date: date
    cash: Decimal
    escrow: Decimal
    annual_rate: Decimal
    is_estimated: bool


@dataclass(frozen=True)
class _ForwardInputs:
    """The resolved, record-free context a forward plan is built and folded from.

    Bundles the loan facts both tiers of :func:`loan_plan` read -- its rate
    periods (each installment's rate and P&I), its escrow lines, its contractual
    due day, and the read pass's as-of clamp -- so the two builders take one
    cohesive value instead of four loose arguments, and the loan is resolved once.

    Attributes:
        periods: The loan's rate periods
            (:func:`app.services.loan_resolver.resolve_periods`).
        escrow_lines: The loan's escrow lines with their full version history.
        payment_day: The loan's contractual due day (the due-date fallback).
        as_of: The read pass's as-of; the clamp floor is ``as_of + 1d`` and the
            past/future boundary is ``as_of`` itself.
    """

    periods: list
    escrow_lines: list
    payment_day: int
    as_of: date


def _planned_from_shadows(
    projected_shadows: list,
    live_cash: dict[int, Decimal],
    fwd: _ForwardInputs,
) -> list[PlannedPayment]:
    """Build the PLANNED tier: one record per projected transfer shadow.

    Each projected loan-side income shadow becomes a :class:`PlannedPayment` at
    its LIVE D3 cash (``live_cash`` override, falling back to the stored
    ``effective_amount`` for a shadow that needs no override -- a manual payment
    with no standing extra, or an operator-overridden one, exactly as the checking
    side reads it).  The rate and escrow are resolved on the shadow's OWN
    pay-period start -- the same date the ACTUAL fold and the live-cash derivation
    key on -- so the cash a payment carries and the escrow its split backs out are
    the same figure by construction.

    Args:
        projected_shadows: The loan's projected income shadows
            (:func:`app.services.loan_loaders.projected_income_shadows`).
        live_cash: ``{transaction_id: live cash}``
            (:func:`app.services.loan_payment_service.live_loan_transfer_amounts`).
        fwd: The resolved :class:`_ForwardInputs`.

    Returns:
        One :class:`PlannedPayment` per projected shadow (``is_estimated=False``).
    """
    planned: list[PlannedPayment] = []
    clamp_floor = fwd.as_of + _ONE_DAY
    for shadow in projected_shadows:
        due = loan_payment_due_date(shadow, fwd.payment_day)
        period_start = shadow.pay_period.start_date
        cash = live_cash.get(shadow.id, shadow.effective_amount)
        escrow = escrow_calculator.escrow_monthly_as_of(
            fwd.escrow_lines, period_start,
        )
        annual_rate = period_for_date(fwd.periods, period_start).annual_rate
        planned.append(PlannedPayment(
            due_date=due,
            effective_date=max(due, clamp_floor),
            cash=cash,
            escrow=escrow,
            annual_rate=annual_rate,
            is_estimated=False,
        ))
    return planned


def _estimated_from_contract(
    contractual: list,
    covered_slots: set[tuple[int, int]],
    fwd: _ForwardInputs,
) -> list[PlannedPayment]:
    """Build the ESTIMATED tier: contractual installments no payment covers.

    Walks the pure contractual schedule
    (:func:`app.services.loan_resolution.contractual_schedule_from_origination`)
    and synthesizes a :class:`PlannedPayment` for every FUTURE installment
    (``payment_date >= as_of``) whose ``(year, month)`` slot no payment already
    covers.  A strictly-PAST installment (``payment_date < as_of``) is NEVER
    synthesized -- the past is the ACTUAL fold's, and an overdue installment with
    no record pays nothing (the B-9 fix).

    **Two kinds of already-covered slot are excluded, and BOTH matter.**  The
    contractual synthesis is a schedule-row estimate, so it can collide with a real
    payment exactly as the C3c interest merge does (``_loan_interest`` excludes the
    same slots):

    * a **PLANNED** record's slot -- a projected shadow this pass will fold forward;
    * a **settled** payment's slot that is ALREADY inside the fold's seed -- a
      payment settled by ``as_of`` (so counted in the confirmed present) whose
      contractual installment is due AT OR AFTER ``as_of`` (an early- or
      on-day-settled payment).  Without this the ESTIMATED tier would re-synthesize
      an installment the seed already paid, and :func:`fold_forward` would subtract
      its principal a SECOND time (understating the debt by one installment).

    The contractual row supplies only the installment DATE and its P&I
    (``row.payment``, escrow-free by construction -- the schedule is fed no
    payments); the balance is re-folded by :func:`fold_forward`, never read off
    ``row.remaining_balance``.  Escrow is ``0.00`` because the contractual P&I
    carries none (and escrow is a wash for the balance either way).

    Args:
        contractual: The pure contractual schedule from origination to payoff.
        covered_slots: The ``{(year, month)}`` slots a PLANNED record OR a
            seed-included settled payment already covers -- excluded here so a slot
            is folded exactly once.
        fwd: The resolved :class:`_ForwardInputs` (its rate periods govern each
            installment's rate; its ``as_of`` is the past/future boundary).

    Returns:
        One :class:`PlannedPayment` per uncovered future contractual installment
        (``is_estimated=True``).
    """
    estimated: list[PlannedPayment] = []
    clamp_floor = fwd.as_of + _ONE_DAY
    for row in contractual:
        due = row.payment_date
        if due < fwd.as_of:
            # The past is ACTUAL-only; an overdue installment with no record
            # pays nothing (finding B-9 / D1).
            continue
        if _month_slot(due) in covered_slots:
            # A PLANNED record or a seed-included settled payment already covers
            # this installment; synthesizing here too would double-count its
            # paydown.
            continue
        estimated.append(PlannedPayment(
            due_date=due,
            effective_date=max(due, clamp_floor),
            cash=row.payment,
            escrow=_ZERO_MONEY,
            annual_rate=period_for_date(fwd.periods, due).annual_rate,
            is_estimated=True,
        ))
    return estimated


def loan_plan(account: Account, ctx: BalanceContext) -> list[PlannedPayment]:
    """Return *account*'s forward payment plan -- PLANNED records then ESTIMATED fill.

    The unified forward record stream a loan's projected balance folds (see the
    module docstring): every projected transfer shadow at its live D3 cash, plus a
    synthesized contractual installment for each future slot no record covers, out
    to payoff.  The list carries NO balance; a caller folds it with
    :func:`fold_forward` seeded from the loan's confirmed present.

    Args:
        account: The amortizing loan account (the caller owns the ownership
            check).
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`
            -- its scenario scopes the shadows and the resolution, and its
            ``as_of`` is the clamp floor and the past/future boundary.

    Returns:
        The loan's :class:`PlannedPayment` list, ascending by ``(effective_date,
        due_date)``.  ``[]`` when *account* is not a configured loan (no
        :class:`~app.models.loan_params.LoanParams`).

    Raises:
        ValueError: When ``ctx.scenario`` is None (guard a nullable baseline
            first).
    """
    require_scenario(ctx)
    resolved = ctx.resolved_loan(account)
    if resolved is None:
        return []
    params = resolved.params
    rate_changes = resolved.context.rate_changes
    fwd = _ForwardInputs(
        periods=loan_resolver.resolve_periods(params, rate_changes),
        escrow_lines=loan_loaders.load_escrow_lines(account.id),
        payment_day=params.payment_day,
        as_of=ctx.as_of,
    )

    projected_shadows = loan_loaders.projected_income_shadows(
        account.id, ctx.scenario_id,
    )
    live_cash = live_loan_transfer_amounts(ctx.scenario_id, projected_shadows)
    planned = _planned_from_shadows(projected_shadows, live_cash, fwd)

    # Slots the fold already accounts for and the ESTIMATED tier must NOT
    # re-synthesize: the PLANNED records this pass folds forward, plus the settled
    # payments ALREADY in the seed (visible by as_of) whose installment is due at
    # or after as_of -- an early-settled payment the contractual synthesis would
    # otherwise double-count.
    settled_seed_slots = {
        _month_slot(loan_payment_due_date(shadow, params.payment_day))
        for shadow in confirmed_shadows_through(
            account.id, ctx.scenario_id, ctx.as_of,
        )
    }
    covered_slots = {
        _month_slot(payment.due_date) for payment in planned
    } | settled_seed_slots

    contractual = contractual_schedule_from_origination(params, rate_changes)
    estimated = _estimated_from_contract(contractual, covered_slots, fwd)

    return sorted(
        planned + estimated,
        key=lambda payment: (payment.effective_date, payment.due_date),
    )


def _paydown_steps(
    seed: Decimal, plan: list[PlannedPayment],
) -> list[tuple[date, Decimal]]:
    """Split each planned payment on the running balance, tagged by visible date.

    Walks *plan* in DUE (contract) order from *seed*, so interest accrues on the
    right balance and a late-clamped payment never re-splits an installment, and
    returns each payment's paydown as a NEGATIVE balance change keyed by its
    EFFECTIVE (visible) date -- the steps :func:`_sample_from_steps` prefix-sums.

    Args:
        seed: The balance the projection starts from.
        plan: The loan's :func:`loan_plan` payment records.

    Returns:
        ``[(effective_date, balance_change), ...]`` in due order (balance_change is
        ``-principal``).
    """
    ordered = sorted(
        plan, key=lambda payment: (payment.due_date, payment.effective_date),
    )
    steps: list[tuple[date, Decimal]] = []
    balance = seed
    for payment in ordered:
        parts = split_payment_cash(
            payment.cash, balance, payment.annual_rate, payment.escrow,
        )
        balance = parts.balance_after
        steps.append((payment.effective_date, -parts.principal))
    return steps


def _sample_from_steps(
    seed: Decimal,
    owed_from: date,
    steps: list[tuple[date, Decimal]],
    dates: list[date],
) -> dict[date, Decimal]:
    """Prefix-sum the paydown *steps* from *seed* and read each date off it.

    Re-keys the (contract-order) steps by their visible date, prefix-sums from the
    seed, and bisects for each requested date -- the same visible-order read
    :func:`app.services.loan_ledger.fold_from_walk` makes for the ACTUAL past.  A
    date before *owed_from* owes ``0.00`` (the loan does not exist yet).

    Args:
        seed: The balance before any paydown.
        owed_from: The loan's ``origination_date``; a date before it owes ``0.00``.
        steps: The ``(effective_date, balance_change)`` steps from
            :func:`_paydown_steps`.
        dates: The dates to value, in any order.  Duplicates collapse.

    Returns:
        ``{date: balance owed}`` -- one cent-quantized ``Decimal`` per date.
    """
    # The prefix-sum and per-date read is the shared fold-sampling core; only the
    # origination gate (a date before the loan exists owes 0.00) is projection-specific.
    sampled = sample_cumulative(
        seed, sorted(steps, key=lambda step: step[0]), dates,
    )
    return {
        on_date: (_ZERO_MONEY if on_date < owed_from else balance)
        for on_date, balance in sampled.items()
    }


def fold_forward(
    seed: Decimal,
    owed_from: date,
    plan: list[PlannedPayment],
    dates: list[date],
) -> dict[date, Decimal]:
    """Fold the confirmed-present *seed* forward over *plan* to a balance per date.

    The projection half of :func:`app.services.balance_at.positions`, expressed as
    a fold rather than a schedule-row walk.  Splits each planned payment on the
    running balance in DUE (contract) order (:func:`_paydown_steps`), then re-keys
    each paydown by its EFFECTIVE (visible) date and prefix-sums
    (:func:`_sample_from_steps`) -- the SAME contract-order-split / visible-order-read
    shape :func:`app.services.loan_ledger.fold_from_walk` uses for the ACTUAL past,
    so the past and the future fold consistently.  A date before ``owed_from`` (the
    loan's origination) owes ``0.00``.

    Args:
        seed: The balance the projection starts from -- the loan's confirmed
            present for an originated loan, or the balance it will OPEN at for one
            not yet originated (the caller supplies the right one, as
            :attr:`~app.services.net_worth_kernel.DebtSchedule.projection_seed`
            does today).
        owed_from: The loan's ``origination_date``; a date before it owes
            ``0.00``.
        plan: The loan's :func:`loan_plan` payment records (any order).
        dates: The dates to value the loan at, in any order.  Duplicates collapse.

    Returns:
        ``{date: balance owed}`` -- one cent-quantized ``Decimal`` per requested
        date.  ``{}`` for an empty *dates*.
    """
    return _sample_from_steps(
        seed, owed_from, _paydown_steps(seed, plan), dates,
    )
