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
  :func:`app.services.balance_at._resolution.contractual_schedule_from_origination` (the
  producer already shared with the property-equity back-projection) -- its
  installment DATE and P&I, never its ``remaining_balance``, which this re-folds.

**Why this replaces the schedule walk (finding B-9).**  The retired forward walk
(``account_projection.forward_balance_at_date``, deleted at step C6b) amortized
one contractual installment per month whether or not any payment was recorded, so
an overdue installment nobody paid still paid the loan down.  Here an installment
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
from decimal import ROUND_CEILING, Decimal

from app.models.account import Account
from app.services import escrow_calculator, loan_loaders, loan_resolver
from app.services.loan_ledger import (
    confirmed_shadows_through,
    split_payment_cash,
)
from app.services.loan_loaders import loan_payment_due_date
from app.services.loan_payment_service import live_loan_transfer_amounts
from app.services.rate_period_engine import period_for_date
from app.utils.dates import add_months

from ._context import BalanceContext, _memoize_once, require_scenario
from ._fold import sample_cumulative
from ._resolution import (
    contractual_schedule_from_origination,
    resolved_loan,
)

_ZERO_MONEY = Decimal("0.00")
_ONE_DAY = timedelta(days=1)

# How far past the CONTRACTUAL payoff the ESTIMATED tier keeps synthesizing the
# level monthly payment (finding N-16).  A loan paying below contract leaves a
# balance behind the contractual schedule, so its fold does not reach zero at the
# contractual last installment; these extra installments let it clear a few months
# later -- a real (slightly-later) payoff instead of "no payoff".  The cap bounds
# a SEVERE underpayment (never clears within five years past contract) back to the
# ``None`` the fold reports for genuine non-amortization -- the drift that far past
# contract is what C7's payment-drift warning exists to surface.  It costs nothing
# for a healthy loan: the fold reaches zero AT the contractual date, so
# :func:`plan_payoff_date` returns that FIRST crossing and these later installments
# fold to no-ops (:func:`~app.services.loan_ledger.split_payment_cash` on a zero
# balance).
_PAYOFF_EXTENSION_MONTHS = 60

# :func:`plan_required_extra`'s bisection stops once the bracket is this narrow,
# and the answer is the upper end rounded UP to the cent -- so the reported extra
# is at most a cent above the true threshold and never below it.
_EXTRA_SEARCH_TOLERANCE = Decimal("0.001")
_CENTS = Decimal("0.01")
# How many times that search may DOUBLE its upper bound looking for an extra that
# reaches the target before declaring the target unreachable.  Twenty doublings
# is a factor of a million on the loan's own balance; a target that survives it is
# not a rounding matter but a date no payment schedule can reach.
_EXTRA_SEARCH_DOUBLINGS = 20


def _month_slot(due: date) -> tuple[int, int]:
    """Return the ``(year, month)`` installment slot a due date occupies.

    The de-dup key that keeps one calendar installment folded once: a contractual
    ESTIMATED synthesis is skipped when a PLANNED record or a settled payment
    already occupies its month (the same month-keyed slot the C6c interest merge
    uses, :func:`app.services.balance_at._loan_interest._due_slot`).
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
    due day, its standing extra, and the read pass's as-of clamp -- so the two
    builders take one cohesive value instead of loose arguments, and the loan is
    resolved once.

    Attributes:
        periods: The loan's rate periods
            (:func:`app.services.loan_resolver.resolve_periods`).
        escrow_lines: The loan's escrow lines with their full version history.
        payment_day: The loan's contractual due day (the due-date fallback).
        extra_principal: The loan's standing monthly overpayment
            (:attr:`~app.services.balance_at._resolution.ResolvedLoan.extra_principal`),
            added to each ESTIMATED installment's cash so the fold folds the SAME
            extra past the materialized-shadow horizon that the resolver's
            committed schedule applies for the whole term (finding N-15).  The
            PLANNED tier does NOT read it -- its live D3 cash already carries the
            extra -- so it lands exactly once.  ``0.00`` when the loan has no
            standing extra.
        as_of: The read pass's as-of; the clamp floor is ``as_of + 1d`` and the
            past/future boundary is ``as_of`` itself.
    """

    periods: list
    escrow_lines: list
    payment_day: int
    extra_principal: Decimal
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
    (:func:`app.services.balance_at._resolution.contractual_schedule_from_origination`)
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

    The contractual row supplies the installment DATE and its P&I (``row.payment``,
    escrow-free by construction -- the schedule is fed no payments), on top of
    which the loan's standing ``extra_principal`` (``fwd.extra_principal``) is
    added: the SAME overpayment the resolver's committed schedule applies to every
    forward month and the PLANNED tier folds into its live cash, so a loan paying
    extra keeps paying it PAST the materialized-shadow horizon rather than
    reverting to bare contractual here (finding N-15).  The extra lands exactly
    once -- the PLANNED tier owns the covered slots, this tier the rest.  The
    balance is re-folded by :func:`fold_forward`, never read off
    ``row.remaining_balance``.  Escrow is ``0.00`` because the contractual P&I
    carries none and the standing extra is pure principal (and escrow is a wash for
    the balance either way).

    **Past the contractual last row it keeps synthesizing** the level monthly
    payment for up to :data:`_PAYOFF_EXTENSION_MONTHS` more months (finding N-16):
    an UNDERPAYING loan is a balance behind the contractual schedule, so it has not
    reached zero at the contractual date, and these installments let it clear a few
    months later -- a real payoff rather than the ``None`` a truncated plan would
    report.  A HEALTHY or overpaying loan has already folded to zero by the
    contractual date, so :func:`plan_payoff_date` returns THAT crossing and these
    fold to no-ops (the balance cap) -- it cannot move.

    Args:
        contractual: The pure contractual schedule from origination to payoff.
        covered_slots: The ``{(year, month)}`` slots a PLANNED record OR a
            seed-included settled payment already covers -- excluded here so a slot
            is folded exactly once.
        fwd: The resolved :class:`_ForwardInputs` (its rate periods govern each
            installment's rate; its ``extra_principal`` is added to every
            synthesized installment; its ``as_of`` is the past/future boundary).

    Returns:
        One :class:`PlannedPayment` per uncovered future contractual installment
        (``is_estimated=True``), plus the post-contractual extension installments.
    """
    estimated: list[PlannedPayment] = []
    clamp_floor = fwd.as_of + _ONE_DAY

    def _synthesize(due: date, cash: Decimal) -> None:
        """Append one uncovered future ESTIMATED installment."""
        if due < fwd.as_of or _month_slot(due) in covered_slots:
            # The past is ACTUAL-only (an overdue installment with no record pays
            # nothing, B-9 / D1); a covered slot the PLANNED tier or the seed
            # already folds would double-count here.
            return
        estimated.append(PlannedPayment(
            due_date=due,
            effective_date=max(due, clamp_floor),
            cash=cash + fwd.extra_principal,
            escrow=_ZERO_MONEY,
            annual_rate=period_for_date(fwd.periods, due).annual_rate,
            is_estimated=True,
        ))

    for row in contractual:
        _synthesize(row.payment_date, row.payment)

    # Extend past the contractual payoff so an underpaying loan clears a few months
    # late instead of reporting no payoff (N-16).  The level payment is the last
    # rate period's P&I (period_for_date returns it for any date past the periods);
    # the fold caps a healthy loan's extra installments to no-ops.
    if contractual:
        last_due = contractual[-1].payment_date
        for months_out in range(1, _PAYOFF_EXTENSION_MONTHS + 1):
            due = add_months(last_due, months_out)
            _synthesize(due, period_for_date(fwd.periods, due).period_pi)
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
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
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
    resolved = resolved_loan(account, ctx)
    if resolved is None:
        return []
    params = resolved.params
    rate_changes = resolved.context.rate_changes
    fwd = _ForwardInputs(
        periods=loan_resolver.resolve_periods(params, rate_changes),
        escrow_lines=loan_loaders.load_escrow_lines(account.id),
        payment_day=params.payment_day,
        extra_principal=resolved.extra_principal,
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


def memoized_plan(account: Account, ctx: BalanceContext) -> list[PlannedPayment]:
    """Return *account*'s forward plan for this read pass, built at most once.

    The seam's ONE funnel for the plan: it fills the read pass's per-loan plan
    cache (:attr:`~app.services.balance_at.BalanceContext.plans`) from
    :func:`loan_plan` through the shared store-once primitive
    (``_context._memoize_once``), so a build happens at most once per account per
    pass and every later read replays it.  Every seam reader that folds a loan's
    future -- the balance (:func:`~app.services.balance_at.positions`), the derived
    payoff, the required-extra search, the projected interest, the equity chart's
    axis -- goes through here, so one ``/savings`` or property render builds a
    loan's plan exactly once.

    **The context receives no builder (plan step D-ctx-b).**  ``loan_plan`` lives
    in this seam module ABOVE the context, so the context cannot import it back to
    build the plan itself without inverting the dependency arrow and closing a real
    import cycle (finding N-25).  The earlier design INJECTED the builder into a
    context method; this funnel now fills a PUBLIC pass-through cache instead -- the
    seam owns the derivation, the context owns the storage -- so there is no builder
    argument a caller could get wrong (the Section 8 lesson the injection conceded).

    Args:
        account: The loan account to plan.  Must belong to ``ctx.user_id`` (the
            caller owns the ownership check).
        ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        The pass's memoized :class:`PlannedPayment` list for this loan.

    Raises:
        ValueError: When ``ctx.scenario`` is None -- on EVERY call, not just the
            first.  A build that raises is never cached (the cache assigns only on
            a returned value), and ``ctx.scenario`` is frozen for the pass, so the
            guard cannot be worn down by retrying: there is no state in which a
            no-baseline context starts answering.  That is the property that makes
            the fail-loud trustworthy rather than first-call-only.
    """
    return _memoize_once(ctx.plans, account.id, lambda: loan_plan(account, ctx))


@dataclass(frozen=True)
class _PlanSplit:
    """One planned payment's fold result: its visible date and the split parts.

    The per-payment output of :func:`_split_plan`, carrying what the two forward
    readers need -- the ``principal`` paydown for :func:`fold_forward` (the balance)
    and the ``interest`` for :func:`plan_interest_in_year` (the tax figure) -- keyed
    by the EFFECTIVE date the payment becomes visible on, plus the ``due_date`` that
    identifies its installment (so the tax reader can drop a slot a settled payment
    already covers).  Sharing ONE split is what keeps a loan's projected balance and
    its projected interest from disagreeing about what a future payment pays.

    Attributes:
        due_date: The contractual installment this payment satisfies -- its
            ``(year, month)`` slot identity, so :func:`plan_interest_in_year` can
            exclude an installment a settled payment already occupies.
        effective_date: When this payment's paydown becomes VISIBLE to a read
            (``max(due, as_of + 1d)``, ruling D1); both readers key their year /
            prefix-sum on it.
        interest: The interest this payment's cash paid, accrued on the running
            balance before it (an Expense leg, ``>= 0``).
        principal: The debt this payment paid down (``cash - interest - escrow``,
            capped at the balance; may be NEGATIVE for an underpayment).
        balance_after: The running balance AFTER this payment
            (:attr:`~app.services.loan_ledger.PaymentCashSplit.balance_after`) --
            what :func:`plan_payoff_date` scans for the first ``<= 0`` to find the
            date the loan clears.  ``fold_forward`` does not read it (it prefix-sums
            ``principal``); it is carried so the payoff derivation reuses the ONE
            fold rather than re-walking the plan.
    """

    due_date: date
    effective_date: date
    interest: Decimal
    principal: Decimal
    balance_after: Decimal


def _split_plan(
    seed: Decimal,
    plan: list[PlannedPayment],
    extra_monthly: Decimal = _ZERO_MONEY,
) -> list[_PlanSplit]:
    """Fold *plan* from *seed* in DUE order, returning each payment's split.

    The shared per-payment fold every forward reader runs.  It walks *plan* in DUE
    (contract) order from *seed* -- so interest accrues on the right running
    balance and a late-clamped payment never re-splits an installment (ruling R-A)
    -- splitting each payment's cash (:func:`split_payment_cash`, the ONE split),
    and returns each result keyed by its EFFECTIVE (visible) date.
    :func:`fold_forward` prefix-sums the ``principal`` side for the balance;
    :func:`plan_interest_in_year` sums the ``interest`` side for the tax figure, so
    the loan's projected balance and its projected interest come from ONE fold and
    cannot disagree.

    Args:
        seed: The balance the projection starts from.
        plan: The loan's :func:`loan_plan` payment records (any order).
        extra_monthly: A HYPOTHETICAL extra added to every payment's cash, for the
            what-if search (:func:`plan_required_extra`).  ``0.00`` -- the default,
            and what every real read passes -- folds the plan as it stands.  The
            loan's STANDING ``extra_principal`` is already inside each record's
            cash (the PLANNED tier's live D3 amount, the ESTIMATED tier's
            synthesis), so this is strictly the extra ON TOP of the user's current
            plan, which is the figure the target-date calculator reports.

    Returns:
        One :class:`_PlanSplit` per payment, in DUE order.
    """
    ordered = sorted(
        plan, key=lambda payment: (payment.due_date, payment.effective_date),
    )
    splits: list[_PlanSplit] = []
    balance = seed
    for payment in ordered:
        parts = split_payment_cash(
            payment.cash + extra_monthly, balance, payment.annual_rate,
            payment.escrow,
        )
        balance = parts.balance_after
        splits.append(_PlanSplit(
            due_date=payment.due_date,
            effective_date=payment.effective_date,
            interest=parts.interest,
            principal=parts.principal,
            balance_after=balance,
        ))
    return splits


def plan_payoff_date(
    seed: Decimal,
    plan: list[PlannedPayment],
    extra_monthly: Decimal = _ZERO_MONEY,
) -> date | None:
    """Return the DUE date *plan* drives *seed* to zero on, or ``None``.

    The loan's derived payoff date: fold *plan* from *seed* in DUE order
    (:func:`_split_plan`, the SAME fold :func:`fold_forward` runs, so the payoff
    and the balance cannot disagree about when the loan clears) and return the DUE
    date of the FIRST payment whose running balance reaches ``<= 0`` -- the
    installment that pays the loan off.  This is a fold-to-zero, NOT
    ``plan[-1].date``: the plan runs PAST the contractual payoff (the ESTIMATED
    tail's extension, :data:`_PAYOFF_EXTENSION_MONTHS`), so a loan paying extra
    reaches zero at an EARLIER installment (== the resolver's committed payoff) and
    an underpaying one at a LATER installment in the extension (a real date, where
    the resolver forces the contractual date via ``is_last_month``).

    Two ``None`` cases, kept distinct from a real payoff date so the caller can
    tell "already done" from "never pays off" (both differ from "pays off on date
    D"):

    * **Already retired** (``seed <= 0``): the loan owes nothing at the projection
      seed, so there is no FORWARD crossing to date.  The caller reads
      :attr:`~app.services.balance_at.LoanFigures.is_retired` for the paid-off
      state; this does not invent a future payoff for a loan already at zero (the
      first planned payment would otherwise look like a "payoff").
    * **Never reaches zero within the plan**: negative amortization (a payment
      below the period interest, so the balance grows), or an underpayment so
      severe the balance is still positive after the post-contractual extension.  A
      MILDER underpayment is NOT here -- the extension lets it clear a few months
      past the contractual date, and this returns that later date.  Recurrence
      stays indefinite; the ``None`` the retired case and this share is
      disambiguated by ``is_retired`` (retired here is False).

    The DUE date (contract time), not the EFFECTIVE (visible) date, is returned so
    the payoff month is the installment's own -- matching the resolver's
    ``committed_forward[-1].payment_date`` the payoff has always keyed on, and, for
    a normal future loan, equal to the effective date anyway (they differ only for
    an overdue-but-projected installment, which almost never clears a loan).

    Args:
        seed: The balance the projection starts from -- the loan's confirmed
            present (an originated loan) or its opening balance (one not yet
            originated), the SAME
            :attr:`~app.services.balance_at._kernel.DebtSchedule.projection_seed`
            :func:`fold_forward` folds.
        plan: The loan's :func:`loan_plan` payment records.
        extra_monthly: A HYPOTHETICAL extra added to every payment, used only by
            :func:`plan_required_extra`'s search.  ``0.00`` -- the default, and
            what every real read passes -- dates the plan as it stands.

    Returns:
        The DUE date the balance first reaches ``<= 0``, or ``None`` when the loan
        is already retired (``seed <= 0``) or never pays off.
    """
    if seed <= _ZERO_MONEY:
        return None
    for split in _split_plan(seed, plan, extra_monthly):
        if split.balance_after <= _ZERO_MONEY:
            return split.due_date
    return None


def plan_required_extra(
    seed: Decimal, plan: list[PlannedPayment], target_date: date,
) -> Decimal | None:
    """Return the extra per payment that clears *seed* by *target_date*.

    The target-date calculator's answer, folded from the SAME plan and the SAME
    seed :func:`plan_payoff_date` and :func:`fold_forward` use (plan step C8f).
    It answers "what must I add to every payment to be done by then", where
    "done" is the date the BALANCE reaches zero -- so the figure and the payoff
    chip beside it rest on one forward model.

    **Why it is not the schedule search it replaced.**  The retired
    ``loan_resolver.target_date_outlook`` binary-searched
    ``amortization_engine.project_forward``, which amortizes one contractual
    installment per month whether or not a payment stands behind it (finding
    B-9).  For a delinquent or drifted loan that walk retires the debt EARLIER
    than the fold does, so it could report "no extra needed" for a target the
    loan does not actually reach -- contradicting the payoff chip on the same
    screen, which folds.  Searching the fold removes the second model rather than
    relabelling its answer.

    Monotone, so a binary search is sound: every added cent is pure principal
    (``split_payment_cash`` subtracts interest and escrow first), so more extra
    can only move the zero-crossing earlier or leave it where it is.

    Args:
        seed: The balance the projection starts from -- the loan's confirmed
            present, the SAME
            :attr:`~app.services.balance_at._kernel.DebtSchedule.projection_seed`
            the balance folds.
        plan: The loan's :func:`loan_plan` payment records.  Their cash already
            carries the loan's STANDING ``extra_principal``, so the result is the
            amount needed ON TOP of the user's current plan.
        target_date: The date the user wants to be done by.

    **Every comparison here is on the EFFECTIVE date, not the due date.**  The
    payoff DATE this seam reports is the clearing installment's DUE date (contract
    time, matching what the loan card has always shown -- see
    :func:`plan_payoff_date`), but "will I be clear by X" is a question about when
    the money MOVES, and those differ for an overdue-but-still-projected payment:
    ruling D1 clamps its effective date to ``as_of + 1d`` while its due date stays
    in the past.  Comparing due dates let a target in the PAST look reachable --
    the fold "cleared" the loan on a past due date, so the search happily returned
    a six-figure extra for a date the user cannot pay on any more.

    Returns:
        ``Decimal("0.00")`` when the plan ALREADY clears the loan by
        *target_date* (including a loan that owes nothing), the searched
        per-payment extra when one exists, or ``None`` for a target no extra
        reaches.  That last has two causes: no planned payment has even HAPPENED
        by then (a target in the past, or before the next installment lands), or
        -- the termination backstop below -- the search exhausted its doublings,
        which past the first guard means the split arithmetic stopped responding
        to more principal rather than that the date is genuinely out of reach.
    """
    if seed <= _ZERO_MONEY:
        return _ZERO_MONEY

    def _clears_by(extra: Decimal) -> bool:
        """Whether *extra* per payment puts the balance at zero by the target.

        Keyed on the clearing payment's EFFECTIVE date -- when its cash actually
        moves -- so a past due date can never stand in for a payment that has not
        happened (see the note above).
        """
        for split in _split_plan(seed, plan, extra):
            if split.balance_after <= _ZERO_MONEY:
                return split.effective_date <= target_date
        return False

    if _clears_by(_ZERO_MONEY):
        return _ZERO_MONEY
    if not any(payment.effective_date <= target_date for payment in plan):
        # No planned payment has even happened by then, so no extra lands in
        # time: the target is in the past, or before the next installment.
        return None

    # An UPPER BOUND has to be found, not assumed.  The seed looks like one --
    # pay the whole balance as extra and the first installment clears it -- but
    # it is not: ``split_payment_cash`` takes interest and escrow out of the cash
    # FIRST, so on a loan whose period interest exceeds its payment cash even
    # ``seed`` leaves a residue.  Double until the bound genuinely reaches the
    # target, so the bisection below starts from an invariant that HOLDS rather
    # than one that looked obvious.
    #
    # The cap is a termination backstop, not an expected outcome: past the
    # no-payment guard above, some extra always clears the loan at the first
    # payment that lands by the target (the extra is unbounded principal), so the
    # ``else`` is not the "unreachable target" case -- that one already returned.
    # The cap exists so a future change to the split can never turn this into a
    # hang.
    low, high = _ZERO_MONEY, seed
    for _ in range(_EXTRA_SEARCH_DOUBLINGS):
        if _clears_by(high):
            break
        low, high = high, high * 2
    else:
        return None

    # Now the invariant the bisection needs holds: ``low`` misses, ``high``
    # reaches.  Narrow to well under a cent.
    while high - low > _EXTRA_SEARCH_TOLERANCE:
        mid = (low + high) / 2
        if _clears_by(mid):
            high = mid
        else:
            low = mid

    # Round UP to the cent, never to nearest.  The threshold sits between ``low``
    # and ``high``, so rounding to NEAREST can land below it -- and a fraction of
    # a cent short at the payoff boundary leaves a positive balance, which pushes
    # the payoff a whole INSTALLMENT past the target.  Measured on a randomized
    # sweep: 99 of 300 generated loans returned an extra that missed its target
    # by one month under half-up rounding.
    #
    # Both ends are tried so the answer is the EXACTLY minimal cent, not merely a
    # cent that works: when the true threshold falls on a whole cent, ``high`` is
    # a hair above it and its ceiling would overcharge the user by a cent.
    # ``ceil(low)`` is either that minimal cent or one below it, so testing it
    # first (one more fold) decides which.
    candidate = low.quantize(_CENTS, rounding=ROUND_CEILING)
    if _clears_by(candidate):
        return candidate
    return high.quantize(_CENTS, rounding=ROUND_CEILING)


def _paydown_steps(
    seed: Decimal, plan: list[PlannedPayment],
) -> list[tuple[date, Decimal]]:
    """Return each planned payment's paydown as a NEGATIVE change on its visible date.

    The balance reader's view of :func:`_split_plan`: each split's ``principal``
    paydown, negated and keyed by its EFFECTIVE (visible) date -- the steps
    :func:`_sample_from_steps` prefix-sums.

    Args:
        seed: The balance the projection starts from.
        plan: The loan's :func:`loan_plan` payment records.

    Returns:
        ``[(effective_date, balance_change), ...]`` in due order (balance_change is
        ``-principal``).
    """
    return [
        (split.effective_date, -split.principal)
        for split in _split_plan(seed, plan)
    ]


def _sample_from_steps(
    seed: Decimal,
    owed_from: date,
    steps: list[tuple[date, Decimal]],
    dates: list[date],
) -> dict[date, Decimal]:
    """Prefix-sum the paydown *steps* from *seed* and read each date off it.

    Re-keys the (contract-order) steps by their visible date, prefix-sums from the
    seed, and bisects for each requested date -- the same visible-order read
    :func:`app.services.balance_at._fold.fold_from_walk` makes for the ACTUAL past.  A
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
    shape :func:`app.services.balance_at._fold.fold_from_walk` uses for the ACTUAL past,
    so the past and the future fold consistently.  A date before ``owed_from`` (the
    loan's origination) owes ``0.00``.

    Args:
        seed: The balance the projection starts from -- the loan's confirmed
            present for an originated loan, or the balance it will OPEN at for one
            not yet originated (the caller supplies the right one, as
            :attr:`~app.services.balance_at._kernel.DebtSchedule.projection_seed`
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


def plan_interest_in_year(
    seed: Decimal,
    plan: list[PlannedPayment],
    year: int,
    exclude_slots: frozenset[tuple[int, int]] = frozenset(),
) -> Decimal:
    """Return the interest *plan*'s payments are projected to pay in *year*.

    The projected (future) half of the Schedule-A mortgage-interest figure
    (:func:`app.services.balance_at.loan_interest_in_year`), folded from the SAME
    forward payment records the loan's projected BALANCE folds
    (:func:`fold_forward` over :func:`_split_plan`) -- so the tax figure's future
    and the balance's future come from ONE model (step C6c; B-6 unified the settled
    PAST, this the future).  It sums each payment's accrued interest attributed to
    the year the payment is projected to be PAID: its EFFECTIVE date
    (``max(due, as_of + 1d)``, ruling D1), the visible / expected-paid date, so an
    overdue-but-still-projected payment's interest deducts in the year it is
    expected to clear rather than the closed year it was contractually due.

    An overdue installment with NO payment record contributes nothing: it is absent
    from *plan* entirely (:func:`loan_plan`'s ESTIMATED tier never synthesizes a
    strictly-past installment -- finding B-9), so a delinquent loan's unpaid past
    does not inflate its deduction.

    *exclude_slots* is the settled-slot MERGE: the caller passes the ``(year,
    month)`` installments its SETTLED half already counts, and this drops any plan
    record on one of them, so an installment counted as settled is not ALSO counted
    here.  The plan's own ESTIMATED tier de-dups the settled payments VISIBLE by
    ``as_of`` (``confirmed_shadows_through``, a UTC-visibility cut), but the caller's
    settled half sums the fold's WALK on a DISPLAY clock; the two cuts differ for a
    payment settled in the evening of a UTC-behind zone, so the caller closes the
    gap by handing this the WALK's slots.  See
    :func:`app.services.balance_at._loan_interest.loan_interest_in_year`.

    Args:
        seed: The balance the projection starts from -- the loan's confirmed
            present (:attr:`~app.services.balance_at._kernel.DebtSchedule.projection_seed`),
            the SAME seed :func:`positions` folds, so the interest accrues on the
            balance the loan actually projects.
        plan: The loan's :func:`loan_plan` payment records.
        year: The calendar / tax year to sum projected interest within.
        exclude_slots: The ``(year, month)`` installment slots a settled payment
            already covers, dropped from the sum (default: none).  A record is still
            FOLDED (its paydown feeds later balances), only its interest is skipped.

    Returns:
        The interest projected to be paid in *year* as a cent-quantized
        ``Decimal`` (``0.00`` when no planned payment is visible in the year).
    """
    return sum(
        (
            split.interest
            for split in _split_plan(seed, plan)
            if split.effective_date.year == year
            and (split.due_date.year, split.due_date.month) not in exclude_slots
        ),
        _ZERO_MONEY,
    )
