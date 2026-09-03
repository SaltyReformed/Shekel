"""Balance-at-T seam -- BUILDING a loan's forward plan: records, not schedule rows.

Plan step **C6a** (``docs/audits/balance_architecture/README.md``).  A loan's
future balance is a fold over what it will be CHARGED and what it will PAY;
this module builds that model (:class:`LoanForwardPlan`) and :mod:`._plan_fold`
folds it.

**The two halves are separate values, and plan step R16-a is why.**  A loan
charges interest because time passed and impounds escrow because a month began;
a payment moves cash on whatever date its recurring definition names.  While the
charge rode ON the payment record, the payment COUNT was the clock -- measured on
a production clone, 30 payments of ``$531.94`` fourteen days apart charged the
same ``$1,096.34`` as 30 a month apart, split for split, so a loan paid twice as
fast modelled the identical interest.  :class:`AccrualCharge` is the time half
and :class:`PlannedPayment` the cash half, and neither can be derived from the
other.

The PAYMENTS come in two tiers:

* **PLANNED** -- the loan's PROJECTED transfer shadows
  (:func:`app.services.loan_loaders.projected_income_shadows`), each at its LIVE
  D3 cash (:meth:`app.services.cash_ledger.LoanPricing.live_cash` =
  P&I + current escrow + ``extra_principal``, the SAME cash the checking side
  shows leaving).  A record is the evidence a payment will happen; where the
  record's due date has already passed but it has not settled, it is clamped
  forward to ``as_of + 1d`` -- "a plan cannot have already happened" (ruling D1).
* **ESTIMATED** -- for every FUTURE contractual installment slot no projected
  record covers (a loan with no recurring transfer, or the tail beyond the
  materialized ~2-year pay-period window), what the loan's own STANDING PAYMENT
  says that installment costs
  (:func:`app.services.recurring_transfer_query.standing_installment_cash`), dated
  and rated by the contractual schedule
  (:func:`app.services.balance_at._resolution.contractual_schedule_from_origination`,
  the producer already shared with the property-equity back-projection) -- its
  installment DATE and P&I, never its ``remaining_balance``, which this re-folds.

**Why the DEFINITION and not the contract** (plan step **R7d-a**).  This tier
priced every uncovered slot at the contract's P&I, whatever the loan's own
recurring payment said it would pay, so "what will this loan be paid in month
M" had two answers and which one the fold used depended on whether the row had
been WRITTEN yet.  Two consequences, both measured on a production clone with a
PLANTED underpayment -- the Van Loan's definition moved to ``$300.00`` against
its ``$531.94`` contractual installment, rolled back after each probe:

* the plan SWITCHED figures at the materialized horizon, projecting a payoff of
  ``2030-02-22`` with rows to 2028-07 and ``2030-04-22`` with rows to 2029-01 --
  the owner's figure inside the horizon and the servicer's past it;
* and the payoff moved with materialization.  ``regenerate_pay_periods`` deletes
  the rebuildable tail of pay periods (``budget.transfers.pay_period_id``
  CASCADEs) and repopulates, and the payoff read AT that moment was
  ``2029-02-22`` against ``2030-02-22`` before and after -- twelve months early,
  on the very figure the recurrence's closing bound is derived from.

One rule for both tiers closes both: the loan is modelled as paying what the
owner has told it to pay, resolved AS OF each installment, materialised or not.
**Unplanted, on the developer's own data, it moves ``$0.00``** -- 776 forward
figures byte-identical -- and the reason is worth stating, because it is a
precondition rather than a general result: both templates state exactly P&I plus
an escrow that is CONSTANT across the whole horizon, so ``stated price - escrow``
equals the contractual P&I at every installment.  A future-dated escrow version
would break that equality, and there is none to measure against.

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
projection.  What :mod:`._plan_fold` does NOT own is the arithmetic: since plan
step X-au-g-2c-3b-2 it maps this model onto
:func:`app.services.loan_ledger.replay_loan_events`, the ONE replay the settled
walk runs, so a PLANNED / ESTIMATED payment is charged and allocated
byte-identically to an ACTUAL settled one.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models.account import Account
from app.services import escrow_calculator, loan_loaders, loan_resolver
from app.services.loan_ledger import (
    AccrualCharge,
    charges_for_due_dates,
    installment_slot,
)
from app.services.cash_ledger import amounts_by_id
from app.services.loan_ledger import confirmed_shadows_through
from app.services.loan_loaders import loan_payment_due_date
from app.services.rate_period_engine import period_for_date
from app.services.recurring_transfer_query import (
    StandingPayment,
    standing_installment_cash,
)
from app.utils.dates import add_months

from ._context import BalanceContext, _memoize_once, require_scenario
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
# :func:`._plan_fold.plan_payoff_date` returns that FIRST crossing and these later installments
# fold to no-ops (the allocator's closed-loan arm on a zero balance).
_PAYOFF_EXTENSION_MONTHS = 60

def _month_slot(due: date) -> tuple[int, int]:
    """Return the ``(year, month)`` installment slot a due date occupies.

    The de-dup key that keeps one calendar installment folded once: a contractual
    ESTIMATED synthesis is skipped when a PLANNED record or a settled payment
    already occupies its month (the same month-keyed slot the C6c interest merge
    uses, :func:`app.services.balance_at._loan_interest._due_slot`).
    """
    return installment_slot(due)


@dataclass(frozen=True)
class PlannedPayment:
    """One forward payment a loan is projected to make -- a RECORD, not a balance.

    The CASH half of :func:`loan_plan`.  It carries the cash a payment will move
    and the two dates the projection keys on -- but no rate, no escrow and no
    balance.  What a period CHARGES is :class:`AccrualCharge`; the balance is the
    FOLD of the two, computed by :func:`._plan_fold.fold_forward`, never stored on a record.

    **It stopped carrying its own rate and escrow at plan step R16-a**, and the
    reason is the defect that step exists to close.  While a payment carried the
    charge it was to be split against, the fold charged one month of interest per
    payment RECORD -- so the payment count was the clock, and a loan paid twice as
    fast modelled the identical interest (measured on a production clone: 30
    payments of ``$531.94`` fourteen days apart and 30 a month apart both charge
    ``$1,096.34``, split for split).  A rate and an escrow belong to a period of
    TIME; a cash figure and its dates belong to a payment; and the two are now
    separate values.

    Attributes:
        due_date: The contractual installment this payment satisfies (contract
            time).  Orders the split walk and keys the PLANNED-vs-ESTIMATED slot
            de-dup, so a late or clamped settlement never re-splits an installment
            (ruling R-A).
        effective_date: When the paydown becomes VISIBLE to a balance read --
            ``max(due_date, as_of + 1d)`` (ruling D1: a plan cannot have already
            happened).  For a normal future installment this is its due date; for
            an overdue-but-still-projected one it is tomorrow.
        cash: The cash this payment moves -- the PLANNED tier's live D3 amount,
            or what the ESTIMATED tier's standing-payment rule says this
            installment costs
            (:func:`~app.services.recurring_transfer_query.standing_installment_cash`).
            Escrow-INCLUSIVE where the definition states a price, which is why
            the charge it clears is backed out of principal rather than added to
            it.
        is_estimated: ``True`` for a synthesized contractual installment, ``False``
            for a real projected-shadow record -- carried for display / debugging;
            the fold treats both alike.
    """

    due_date: date
    effective_date: date
    cash: Decimal
    is_estimated: bool


@dataclass(frozen=True)
class LoanForwardPlan:
    """A loan's forward model: what it will be CHARGED and what it will PAY.

    :func:`loan_plan`'s whole answer, and the shape plan step **R16-a** gave it.
    The two lists are independent by construction -- charges come from the loan's
    own note and the passage of time, payments from whatever the owner's
    recurring definitions say -- and :func:`._plan_fold._split_plan` walks them merged in
    contract order.  That independence is what makes a payment cadence a
    non-question: a definition emits payments on its own dates and the charges do
    not move.

    Attributes:
        payments: The forward payment records, PLANNED then ESTIMATED, ascending
            by ``(effective_date, due_date)``.  Empty for an account that is not
            a configured loan.
        charges: One :class:`AccrualCharge` per accrual period those payments
            occupy, ascending by ``on_date``.
    """

    payments: list[PlannedPayment]
    charges: list[AccrualCharge]


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
        standing: What the loan's own recurring payment says one installment
            costs
            (:attr:`~app.services.balance_at._resolution.ResolvedLoan.standing`),
            which prices every ESTIMATED installment
            (:func:`~app.services.recurring_transfer_query.standing_installment_cash`).
            Its standing extra lands there too, so the fold folds the SAME extra
            past the materialized-shadow horizon that the resolver's committed
            schedule applies for the whole term (finding N-15).  The PLANNED
            tier does NOT read this -- its live D3 cash already carries both the
            base and the extra -- so each lands exactly once.  ``None`` when the
            loan has no recurring payment, and the contract is then the only
            estimate there is.
        as_of: The read pass's as-of; the clamp floor is ``as_of + 1d`` and the
            past/future boundary is ``as_of`` itself.
    """

    periods: list
    escrow_lines: list
    payment_day: int
    standing: StandingPayment | None
    as_of: date


def _planned_from_shadows(
    projected_shadows: list,
    priced: dict[int, Decimal],
    fwd: _ForwardInputs,
) -> list[PlannedPayment]:
    """Build the PLANNED tier: one record per projected transfer shadow.

    Each projected loan-side income shadow becomes a :class:`PlannedPayment` at
    what a SCREEN would show for it, which since plan step X-au-d is simply
    what the amount model RESOLVES for it
    (:func:`~app.services.cash_ledger.amounts_by_id`).

    **It read ``owned_contribution`` with the live map laid over it by hand
    until plan step X-au-g-2c-1**, and that was the SECOND unrouted reader of a
    projected loan-side shadow -- the same row class, the same accessor, the
    same refusal.  The first was
    ``loan_payment_service.get_payment_history``; routing only that one would
    have left this to 500 the moment the cutover emptied the column, on
    ``/savings`` and every surface that folds a loan's forward plan.  An
    adversarial review found it by censusing the accessor's callers rather than
    trusting the finding's own count of them, which is the lesson: **"one
    unrouted reader" was itself an unmeasured claim.**

    The two-line merge it replaced -- a resolved figure with a live override on
    top -- was ``display_amounts_by_id``, extracted at X-au-c2b so one
    composition lived in one place rather than at two call sites (finding
    **N-224**'s shape).  Both cutovers have since deleted the override term
    itself, so that function collapsed onto ``amounts_by_id`` and the merge has
    no second half left to get wrong.

    **The cycle a much older draft blamed is DELETED and was never the
    reason.**  It read: asking the resolver here would ask the loan to price
    the rows its own price is derived from, because pricing routed
    ``resolve_transaction_amount`` -> ``LoanPricing.derive_cash`` ->
    ``_resolve_loan_basis`` -> ``load_loan_context`` ->
    ``get_payment_history``.  ``_resolve_loan_basis`` reads the loan's TERMS
    and nothing else
    (:func:`~app.services.loan_resolver.compute_monthly_payment_baseline`), so
    it loads no payment history at all.

    **Finding N-266 (a) is CLOSED at plan step X-au-g-2c-1, and its DIAGNOSIS
    was wrong twice before its remedy was right.**  It first recorded an
    irreducible CYCLE: the rule that priced a loan payment routed back through
    the payment feed.  Plan step X-au-g-1 deleted that path, leaving the
    conclusion standing on something smaller -- ``get_payment_history`` priced
    each row through
    :func:`~app.services.row_valuation.owned_contribution`, which REFUSES a row
    whose plan is derived -- and the row was restated as **"ONE unrouted
    reader"**.  That count was itself unmeasured, and an adversarial review of
    X-au-g-2c-1 censused the accessor: there were **TWO**, and the second is
    this function.  Both are routed now, so the loan-side INCOME leg is
    declarable; had only the named one moved, the cutover would have 500'd
    every surface that folds a loan's forward plan.

    **The other seven callers of that accessor really are settled-only**, which
    is what makes "two" a census rather than a second guess:
    ``cash_ledger.settled_cash_leg``, ``loan_ledger._events.loan_event_stream``
    (``._split.split_one_payment`` until plan step X-au-g-2c-3b-2),
    ``loan_posting_service._sync`` and ``._display``,
    ``savings_dashboard_service._metrics``, and the spending report's
    ``_window`` and ``_breakdown`` -- each loading rows filtered to the settled
    statuses in SQL.

    **What has NOT changed is the DATA, and that is a fact about production
    rather than about the loan.**  ``ck_transactions_amount_ownership`` is a
    biconditional -- ``(amount_source_id IS NULL) = (estimated_amount IS NOT
    NULL)`` -- so a row states EITHER what prices it or its own figure, never
    both.  Every loan-payment shadow is still on the second side of it:
    re-measured against production 2026-09-01 (stamp ``a4c6f1d92b73``), all 58
    shadows and all 175 transfers carry ``amount_source_id IS NULL``, so the
    resolver classifies them ``AmountRule.OWN`` and answers from the very
    column the old fallback read.  **This routing therefore moves ``$0.00``**;
    what it removes is the interval in which stamping that column would have
    broken this surface.

    **Until then a projected row's figure is a stored copy of its definition's
    price and nothing keeps the two equal** (finding **N-401**, owned by
    ``X-au-f``).  They are equal today -- all 48
    projected loan shadows match their definition to the cent on the same clone
    -- but an edit to a generator-written row moves the derived payoff with no
    signal: halving the Van Loan's 24 future rows while the definition sat at
    ``$531.94`` moved it ``2029-02-22`` -> ``2030-04-22``.

    **Reading the DEFINITION here instead is not the remedy, and it is a money
    defect in its own right.**  It was built and reverted: an ad-hoc
    ``$2,100.00`` payment into a loan whose contractual installment is
    ``$2,035.15`` was repriced to ``$2,035.15``, discarding ``$64.85`` of
    principal the owner is actually paying
    (``test_loan_plan_assembly.test_a_projected_record_makes_its_slot_planned_not_estimated``
    is what caught it), and the underpayment direction is worse -- a ``$500.00``
    payment would project as ``$2,035.15`` and claim a payoff the owner never
    reaches.  BOTH directions of this column are wrong, which is the whole
    argument for the cutover: ``estimated_amount`` carries two facts, the plan's
    price and what the OWNER said, and no reading rule can tell them apart.
    ``amount_source_id`` is what distinguishes them.  **Stamping it used to
    FORCE this call site to move, and that is why it moved FIRST**: the old
    fallback raised on a NULL column, so the cutover that empties it would have
    taken this surface down in the interval before the move.  Routing ahead of
    the stamp is byte-identical -- every one of these rows carries
    ``amount_source_id IS NULL`` today, so the resolver dispatches to
    ``AmountRule.OWN`` and answers from the same column -- which is what makes
    it safe to do early rather than late.

    **It resolves NO rate and NO escrow since plan step R16-a**: those belong to
    the period, not to the payment, and :func:`_charges_for` resolves them on the
    period's own date -- the earliest due in it, which for a month holding one
    payment IS this record's due date, so ruling D5's contract time and finding
    N-34's due-date keying are unchanged.  What this builds is cash and its two
    dates.

    Args:
        projected_shadows: The loan's projected income shadows
            (:func:`app.services.loan_loaders.projected_income_shadows`).
        priced: ``{transaction_id: the figure a screen shows}``
            (:func:`app.services.cash_ledger.amounts_by_id` over the
            pass's own basis).  Indexed with ``[]``: it covers every row it was
            built over, so a shadow it forgot raises where it is read rather
            than defaulting to a fabricated figure.
        fwd: The resolved :class:`_ForwardInputs`.

    Returns:
        One :class:`PlannedPayment` per projected shadow (``is_estimated=False``).
    """
    planned: list[PlannedPayment] = []
    clamp_floor = fwd.as_of + _ONE_DAY
    for shadow in projected_shadows:
        due = loan_payment_due_date(shadow, fwd.payment_day)
        # ONE map, no fallback.  The ``is None`` dance this replaced existed
        # because a live cash of ``Decimal("0")`` is a real answer (a waived
        # payment) and truthiness would have priced the shadow off the column
        # the loan superseded.  There is no column left to fall back to and no
        # override left to choose between: the shadow is DERIVED (plan step
        # X-au-g-2c-2) and the resolver is its only answer.
        cash = priced[shadow.id]
        planned.append(PlannedPayment(
            due_date=due,
            effective_date=max(due, clamp_floor),
            cash=cash,
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
      an installment the seed already paid, and :func:`._plan_fold.fold_forward` would subtract
      its principal a SECOND time (understating the debt by one installment).

    The contractual row supplies the installment DATE and its P&I (``row.payment``,
    escrow-free by construction -- the schedule is fed no payments); what the
    installment COSTS is then the loan's own standing payment's answer
    (:func:`~app.services.recurring_transfer_query.standing_installment_cash`, plan
    step **R7d-a**), which is the contract's P&I plus escrow plus the standing
    extra for a DERIVE-mode payment or for a loan with none, and the definition's
    stated base plus the extra where the owner has stated one.  The extra is the
    SAME overpayment the resolver's committed schedule applies to every forward
    month and the PLANNED tier folds into its live cash, so a loan paying extra
    keeps paying it PAST the materialized-shadow horizon rather than reverting to
    bare contractual here (finding N-15); the BASE now carries the same way, which
    is what stops the plan switching figures at the horizon.  Each lands exactly
    once -- the PLANNED tier owns the covered slots, this tier the rest.  The
    balance is re-folded by :func:`._plan_fold.fold_forward`, never read off
    ``row.remaining_balance``.

    **The escrow PRICES the installment here and is CHARGED by the period**, and
    plan step R16-a is what separated the two.  A stated base is escrow-INCLUSIVE
    (an owner types the whole mortgage payment), so this tier reads the escrow to
    know what the definition costs; what the fold then backs out of principal is
    the CHARGE :func:`_charges_for` states for the period, once, however many
    payments fall in it.  Until R16-a both were a field on the record, which is
    why a tier calling its escrow zero would have paid the loan down by the
    escrow every month -- and why a second payment in a month would have paid it
    twice.

    **Past the contractual last row it keeps synthesizing** the level monthly
    payment for up to :data:`_PAYOFF_EXTENSION_MONTHS` more months (finding N-16):
    an UNDERPAYING loan is a balance behind the contractual schedule, so it has not
    reached zero at the contractual date, and these installments let it clear a few
    months later -- a real payoff rather than the ``None`` a truncated plan would
    report.  A HEALTHY or overpaying loan has already folded to zero by the
    contractual date, so :func:`._plan_fold.plan_payoff_date` returns THAT crossing and these
    fold to no-ops (the balance cap) -- it cannot move.

    Args:
        contractual: The pure contractual schedule from origination to payoff.
        covered_slots: The ``{(year, month)}`` slots a PLANNED record OR a
            seed-included settled payment already covers -- excluded here so a slot
            is folded exactly once.
        fwd: The resolved :class:`_ForwardInputs` (its rate periods govern each
            installment's rate; its ``standing`` payment and its escrow lines
            price every synthesized installment; its ``as_of`` is the
            past/future boundary).

    Returns:
        One :class:`PlannedPayment` per uncovered future contractual installment
        (``is_estimated=True``), plus the post-contractual extension installments.
    """
    estimated: list[PlannedPayment] = []
    clamp_floor = fwd.as_of + _ONE_DAY

    def _synthesize(due: date, contractual_pi: Decimal) -> None:
        """Append one uncovered future ESTIMATED installment."""
        if due < fwd.as_of or _month_slot(due) in covered_slots:
            # The past is ACTUAL-only (an overdue installment with no record pays
            # nothing, B-9 / D1); a covered slot the PLANNED tier or the seed
            # already folds would double-count here.
            return
        # The installment's OWN escrow, on its OWN due date -- ruling D5's
        # contract time, the same date and function the PLANNED tier and the
        # genesis split read (``_shadow_live_amount``), so an escrow version
        # effective mid-horizon reaches every tier alike.
        escrow = escrow_calculator.escrow_monthly_as_of(fwd.escrow_lines, due)
        estimated.append(PlannedPayment(
            due_date=due,
            effective_date=max(due, clamp_floor),
            cash=standing_installment_cash(
                fwd.standing, contractual_pi, escrow, due,
            ),
            is_estimated=True,
        ))

    for row in contractual:
        _synthesize(row.payment_date, row.payment)

    # Extend past the contractual payoff so an underpaying loan clears a few months
    # late instead of reporting no payoff (N-16).  The contractual P&I past the
    # schedule is the last rate period's (period_for_date returns it for any date
    # past the periods), and ``_synthesize`` applies the SAME standing-payment
    # rule to it -- so a loan the owner underpays extends at what the owner
    # actually pays, which is the whole point of the extension: extending at the
    # contract's figure would clear the very loan the extension exists to model.
    # The fold caps a healthy loan's extra installments to no-ops.
    if contractual:
        last_due = contractual[-1].payment_date
        for months_out in range(1, _PAYOFF_EXTENSION_MONTHS + 1):
            due = add_months(last_due, months_out)
            _synthesize(due, period_for_date(fwd.periods, due).period_pi)
    return estimated


def _charges_for(
    payments: list[PlannedPayment], fwd: _ForwardInputs,
) -> list[AccrualCharge]:
    """Return one :class:`AccrualCharge` per accrual period *payments* occupy.

    **The TIME half of the plan, derived independently of how many payments land
    in a period** (plan step **R16-a**).  A loan charges a month's interest
    because a month passed, and impounds a month's escrow because a month began;
    while both rode on the payment record, N payments inside one month charged N
    months and the payment count was the clock.

    The period is the contractual MONTH -- the unit
    :func:`~app.utils.money.accrue_monthly_interest` already divides the annual
    rate by, and the unit :func:`~app.services.escrow_calculator.escrow_monthly_as_of`
    already answers in -- keyed by :func:`_month_slot`, the same key the
    PLANNED-vs-ESTIMATED de-dup uses.

    **The charge is dated at the EARLIEST payment due in its period, and that is
    what makes this byte-identical for a monthly loan.**  With one payment to a
    month that date IS the payment's own due date, so the rate and the escrow
    resolve exactly where the payment used to resolve them itself -- contract
    time, ruling D5.  Deriving the date from the CONTRACTUAL schedule instead
    would have been the more obvious rule and is not the safer one: a payment
    whose stored due date is not on the contractual day would then have its
    charge resolved on a different date from the one that priced it.

    **A period with no payment at all gets no charge**, which is today's rule
    kept deliberately rather than extended.  The ESTIMATED tier fills every
    future slot no record covers, so for a healthy loan every period has a
    payment and the question does not arise; where it does arise -- a delinquent
    loan, whose overdue months the tier never synthesizes (finding B-9's "an
    overdue installment with no record pays nothing") -- charging them would make
    a delinquent balance GROW where the module docstring says it holds flat.
    That is a ruling, not a refactor, and it is carried as its own ledger row.

    Args:
        payments: The plan's forward payment records, in any order.
        fwd: The resolved :class:`_ForwardInputs` -- its rate periods and escrow
            lines are what each charge is resolved against.

    Returns:
        One :class:`AccrualCharge` per occupied period, ascending by ``on_date``.
    """
    return charges_for_due_dates(
        [payment.due_date for payment in payments],
        fwd.periods,
        fwd.escrow_lines,
    )


def loan_plan(account: Account, ctx: BalanceContext) -> LoanForwardPlan:
    """Return *account*'s forward model -- what it will be CHARGED and what it PAYS.

    The unified forward record stream a loan's projected balance folds (see the
    module docstring): every projected transfer shadow at its live D3 cash, plus a
    synthesized contractual installment for each future slot no record covers, out
    to payoff -- and, beside them, one :class:`AccrualCharge` per accrual period
    those payments occupy.  The value carries NO balance; a caller folds it with
    :func:`._plan_fold.fold_forward` seeded from the loan's confirmed present.

    Args:
        account: The amortizing loan account (the caller owns the ownership
            check).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            -- its scenario scopes the shadows and the resolution, and its
            ``as_of`` is the clamp floor and the past/future boundary.

    Returns:
        The loan's :class:`LoanForwardPlan` -- its payments ascending by
        ``(effective_date, due_date)``, its charges by ``on_date``.  Both empty
        when *account* is not a configured loan (no
        :class:`~app.models.loan_params.LoanParams`).

    Raises:
        BaselineMissingError: When ``ctx.scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
    """
    require_scenario(ctx)
    resolved = resolved_loan(account, ctx)
    if resolved is None:
        return LoanForwardPlan(payments=[], charges=[])
    params = resolved.params
    rate_changes = resolved.context.rate_changes
    fwd = _ForwardInputs(
        periods=loan_resolver.resolve_periods(params, rate_changes),
        escrow_lines=loan_loaders.load_escrow_lines(account.id),
        payment_day=params.payment_day,
        standing=resolved.standing,
        as_of=ctx.as_of,
    )

    projected_shadows = loan_loaders.projected_income_shadows(
        account.id, ctx.scenario_id,
    )
    # The pass's OWN loan derivation, not a second one built here: this line
    # called ``live_loan_transfer_amounts`` directly while the cash fold built a
    # basis that called it again, so one request resolved the same loan twice
    # (finding **N-268**'s shape).  Plan step X-au-c2b made the derivation a
    # read-pass value, so both readers ask the same one.
    priced = amounts_by_id(projected_shadows, ctx.amounts())
    planned = _planned_from_shadows(projected_shadows, priced, fwd)

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

    payments = sorted(
        planned + estimated,
        key=lambda payment: (payment.effective_date, payment.due_date),
    )
    return LoanForwardPlan(
        payments=payments, charges=_charges_for(payments, fwd),
    )


def memoized_plan(account: Account, ctx: BalanceContext) -> LoanForwardPlan:
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
        The pass's memoized :class:`LoanForwardPlan` for this loan.

    Raises:
        BaselineMissingError: When ``ctx.scenario`` is None -- on EVERY call, not just the
            first.  A build that raises is never cached (the cache assigns only on
            a returned value), and ``ctx.scenario`` is frozen for the pass, so the
            guard cannot be worn down by retrying: there is no state in which a
            no-baseline context starts answering.  That is the property that makes
            the fail-loud trustworthy rather than first-call-only.
    """
    return _memoize_once(
        ctx, ctx.plans, account, lambda: loan_plan(account, ctx),
    )
