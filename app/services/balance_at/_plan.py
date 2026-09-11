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
  (:func:`app.services.loan_loaders.projected_income_shadows`), each at what
  the amount model resolves for it (:func:`app.services.cash_ledger.amounts_by_id`,
  the SAME cash the checking side shows leaving).  A record is the evidence a
  payment will happen; where the record's due date has already passed but it
  has not settled, it is clamped forward to ``as_of + 1d`` -- "a plan cannot
  have already happened" (ruling D1).
* **ESTIMATED** -- **what a generate pass over the owner's whole schedule
  would write, priced as those rows would be priced** (plan step **R16-b-2**).
  EVERY active recurring transfer into the loan is walked on its OWN cadence
  under its AUTHORED closing (:func:`._plan_definitions.estimated_from_definitions`); each
  occurrence the schedule can place that NO ROW IN ANY STATE answers is dated
  as generation would date its row and priced through the amount model's own
  arm (:func:`app.services.cash_ledger.definition_cash`).  A loan with no
  definition at all is priced by its CONTRACT, one installment per future
  slot no record covers (:func:`_estimated_from_contract`), dated and rated by
  the contractual schedule
  (:func:`~app.services.balance_at._resolution.contractual_schedule_from_origination`)
  -- its installment DATE and P&I, never its ``remaining_balance``, which this
  re-folds.

**Why every definition, and why on its own cadence** (rulings **R-R35**,
**R-R36**, **R-R37**, **R-R65**; findings **D47**, **D48**).  Until R16-b-2
this tier synthesized one slot per CONTRACTUAL month and priced every slot
from ONE definition picked by lowest id, so it modelled the contract's rhythm
carrying one definition's price: re-authoring the Van's payment to every
paycheck left the estimate byte-identical at 12 installments a year against
the rule's 26.07, and re-pointing a ``$500.00`` every-paycheck sweep at the
Mortgage priced every uncovered installment at ``$500.00`` against a
``$616.99`` escrow, read the payoff ``None`` and grew the balance to
``$1,059,869.99`` by 2053 -- where summing each definition's occurrences
answered ``2034-10-01``.  The other two tiers (the settled fold and the
PLANNED tier) already summed every transfer into the loan with no template
filter; this was the one outlier, and four tie-break remedies were refused
to delete the question rather than answer it (R-R35).

**Why the fold prices an occurrence NO ROW answers, past or future** (ruling
**R-R64**, finding **D46**).  Generation has been exhaustive over the
schedule since R17, so "no row at all" for an occurrence the schedule places
means "not generated yet" -- and a cancelled or deleted row still ANSWERS
its occurrence (the owner un-planned it).  Before R-R64 an occurrence dated
before ``as_of`` that no row answered when a pass read the loan was priced by
neither tier (B-9's "the past is the ACTUAL fold's"), so the payoff read one
installment late and generation bounded by that payoff wrote one installment
past the loan's life: ``$1,200`` at 3% over three months, ``as_of``
2026-03-01, the 02-15 row wiped by the reset door -- 4 rows against 3.
**Bounded by the SCHEDULE**, which the ruling's first statement did not say
and which is what makes it correct on live data: an occurrence the owner's
pay calendar cannot place (before its first payday under ``CONTAINING_DATE``)
is neither generated nor estimated.  The Mortgage's rule starts 2019-01-01
against a schedule opening 2026-03-26; priced literally, its 87 pre-schedule
occurrences would pay ``$166,252.65`` the day after ``as_of``.

**Why the DEFINITION and not the contract** (plan step **R7d-a**).  This tier
priced every uncovered slot at the contract's P&I, whatever the loan's own
recurring payment said it would pay, so "what will this loan be paid in month
M" had two answers and which one the fold used depended on whether the row had
been WRITTEN yet.  R16-b-2 finished that argument: the estimate is now the
row's own pricing function over the columns the row would carry, so the two
answers are one by construction rather than by two arms agreeing.  Two
consequences of the contract-priced tier, both measured on a production clone
with a PLANTED underpayment -- the Van Loan's definition moved to ``$300.00``
against its ``$531.94`` contractual installment, rolled back after each
probe:

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
the owner's definitions say the loan will be paid (ESTIMATED); an occurrence the
owner UN-PLANNED -- a cancelled or deleted row -- pays nothing, and since R16-b-2
its month is CHARGED anyway (rulings **R-R37**, **R-R71**; finding **D53**), so
a delinquent balance grows in the projection rather than holding flat.  What
B-9 keeps is the narrower claim: the fold never invents a payment nothing
stands behind -- and never one the write door would refuse: an occurrence at or
before the loan's origination (ruling R-C, the door's
:func:`~app.services.loan_loaders.precedes_origination`) is dropped with every
other payment at or before the loan's latest assertion, of which the
origination is the first (:func:`loan_plan`, ruling **R-R72**), because
generation cannot write it and the fold would erase it if it could.

**The CHARGE calendar is the contract's, not the payments'** (plan step
**R16-b-2**, rulings **R-R68** and **R-R71**; findings **D53**, **D54**).
One :class:`AccrualCharge` per CONTRACTUAL installment dated after the loan's
LAST BALANCE ASSERTION visible at the read -- the origination anchor, or the
latest true-up -- whose month the settled seed did not charge, past or future;
the sequence runs through the post-contractual extension and as far as the
last plan payment.  **The same assertion bounds the PAYMENTS**: a planned row
or an estimated occurrence whose installment is due at or before it is not
folded, because the settled walk will not fold it either once it settles --
it walks the payment and then resets at the anchor
(:func:`~app.services.loan_ledger.replay_loan_events`), so its cash never
reaches the post-assertion balance.  An adversarial review of this step
measured the plan folding three projected rows a true-up had already
subsumed, `$5,955.13` that vanished the day they settled.  Charges and
payments read the one boundary
(:func:`~app.services.rate_period_engine.due_after_anchor`).
Deriving the calendar from the payments was exact only while this tier filled
every month; summing definitions on their own cadence deletes that fill, so a
quarterly definition would have collapsed the charge set with the payment set
(40 charges where the contract owes 120, ``$367.98`` of interest against
``$1,096.34``).  Seed-aware because the settled walk has already charged the
month of every settled payment, and a forward charge on the same month was
finding D54's double charge (``$2,026.52`` for one September, ``$1,014.06``
twice) -- unreachable now rather than guarded.  **Anchor-bounded rather than
``as_of``-bounded, and an adversarial review of this step is why**: charging
"every month at or after ``as_of``" made a month the owner CANCELLED accrue
while it lay ahead of the read and accrue nowhere once the read passed it,
so the same records answered two payoffs on two days (``$12,000`` at 6% over
six months, May cancelled: ``2026-09-01`` read in April, ``2026-08-01`` read
in May).  A skipped month owes its interest -- and its escrow, which the
servicer advanced -- whichever side of today it is on, and a catch-up payment
at the definition's level cash clears those arrears before it reaches
principal, which is what the servicer's books say too; the assertion is the
boundary because the owner's own statement of the balance supersedes every
month before it.  **That boundary is the design of record** (ruling
**R-R72**): a loan's balance is the replay from its LATEST assertion over the
CONTRACT's calendar.  Two halves of it are later steps'.  ``R16-c`` applies
this same predicate in the settled walk (D53's past half): until then the
walk charges only the months it saw paid, so a read AT ``as_of`` holds the
seed flat where the read after it carries the skipped months' interest.  And
``R20`` records the balance the owner states at SETUP as the assertion it is
(a ``tracking_start``), where today the setup door stores it in the params
row's demoted current-principal column and nothing reads it (finding
**REC-519**):
a loan configured mid-life without a separate tracking-start therefore has
only its origination assertion, and this calendar reads it as unpaid since
origination -- which is what its records say, and not a bound this module
guesses around.

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
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.services import escrow_calculator, loan_loaders, loan_resolver
from app.services.loan_ledger import (
    AccrualCharge,
    charges_for_due_dates,
    installment_slot,
)
from app.services.cash_ledger import amounts_by_id
from app.services.loan_ledger import anchor_visible_on, confirmed_shadows_through
from app.services.loan_loaders import loan_payment_due_date
from app.services.rate_period_engine import due_after_anchor, period_for_date
from app.utils.amount_relationships import pricing_load_options
from app.utils.dates import add_months
from app.utils.money import round_money

from ._context import BalanceContext, _memoize_once, require_scenario
from ._plan_definitions import estimated_from_definitions
from ._plan_records import _ONE_DAY, LoanForwardPlan, PlannedPayment
from ._resolution import (
    contractual_schedule_from_origination,
    resolved_loan,
)

_ZERO_MONEY = Decimal("0.00")

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


@dataclass(frozen=True)
class _ForwardInputs:
    """The resolved, record-free context a forward plan is built and folded from.

    Bundles the loan facts both tiers of :func:`loan_plan` read -- its rate
    periods (each installment's rate and P&I), its escrow lines, its contractual
    due day, and the read pass's as-of clamp -- so the builders take one
    cohesive value instead of loose arguments, and the loan is resolved once.

    **It carried the loan's STANDING payment until plan step R16-b-2**, the one
    definition ``active_recurring_transfer_template`` picks by lowest id, which
    priced every ESTIMATED installment.  The tier sums EVERY definition now
    (:func:`._plan_definitions.estimated_from_definitions`) and prices each occurrence through
    the amount model's own arm, so no definition rides here: the PLANNED tier
    prices its rows through the amount model and the ESTIMATED tier prices its
    occurrences through the same arms, and a standing extra lands in each
    exactly once because both read it off the definition's settings row.

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
    priced: dict[int, Decimal],
    fwd: _ForwardInputs,
) -> list[PlannedPayment]:
    """Build the PLANNED tier: one record per projected transfer shadow.

    Each projected loan-side income shadow becomes a :class:`PlannedPayment` at
    what a SCREEN would show for it, which since plan step X-au-d is simply
    what the amount model RESOLVES for it
    (:func:`~app.services.cash_ledger.amounts_by_id`).

    **It read ``settled_contribution`` with the live map laid over it by hand
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
    :func:`~app.services.row_valuation.settled_contribution`, which refused a row
    whose plan was derived (since plan step X-bx it refuses any row that has not
    settled, whatever prices it) -- and the row was restated as **"ONE unrouted
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
    """Build the ESTIMATED tier for a loan with NO definition: the contract's own installments.

    Walks the pure contractual schedule
    (:func:`app.services.balance_at._resolution.contractual_schedule_from_origination`)
    and synthesizes a :class:`PlannedPayment` for every FUTURE installment
    (``payment_date >= as_of``) whose ``(year, month)`` slot no payment already
    covers, at the contract's P&I plus that installment's escrow.  Nothing
    else is known about how such a loan will be paid, so the contract is the
    only estimate there is -- and it is the CONTRACT's figure, residual last
    row included, rather than a definition's level payment.

    **The one arm plan step R16-b-2 left here.**  Until that step this
    function was the whole ESTIMATED tier, pricing every uncovered slot from
    the loan's standing payment; a loan WITH definitions is estimated by
    :func:`._plan_definitions.estimated_from_definitions` now, each definition on its own
    cadence and by occurrence identity rather than by month slot.  A strictly-PAST
    installment (``payment_date < as_of``) is never synthesized here: with no
    definition there is nothing generation would write, so ruling **R-R64**
    has no occurrence to price and B-9's rule stands for this arm.

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

    **The escrow PRICES the installment here and is CHARGED by the period**, and
    plan step R16-a is what separated the two.  The contract's installment is
    escrow-INCLUSIVE as the owner pays it, so this tier reads the escrow to
    know what the installment costs; what the fold then backs out of principal
    is the CHARGE :func:`_charges_for` states for the period, once, however
    many payments fall in it.

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
            installment's rate; its escrow lines price every synthesized
            installment; its ``as_of`` is the past/future boundary).

    Returns:
        One :class:`PlannedPayment` per uncovered future contractual installment
        (``is_estimated=True``), plus the post-contractual extension installments.
    """
    estimated: list[PlannedPayment] = []
    clamp_floor = fwd.as_of + _ONE_DAY

    def _synthesize(due: date, contractual_pi: Decimal) -> None:
        """Append one uncovered future ESTIMATED installment."""
        if due < fwd.as_of or installment_slot(due) in covered_slots:
            # The past is ACTUAL-only for a loan nothing generates into (B-9 /
            # D1); a covered slot the PLANNED tier or the seed already folds
            # would double-count here.
            return
        # The installment's OWN escrow, on its OWN due date -- ruling D5's
        # contract time, the same date and function the PLANNED tier and the
        # genesis split read (``_installment_cash``), so an escrow version
        # effective mid-horizon reaches every tier alike.
        escrow = escrow_calculator.escrow_monthly_as_of(fwd.escrow_lines, due)
        estimated.append(PlannedPayment(
            due_date=due,
            effective_date=max(due, clamp_floor),
            cash=round_money(contractual_pi + escrow),
            is_estimated=True,
        ))

    for row in contractual:
        _synthesize(row.payment_date, row.payment)

    # Extend past the contractual payoff so an underpaying loan clears a few months
    # late instead of reporting no payoff (N-16).  The contractual P&I past the
    # schedule is the last rate period's (period_for_date returns it for any date
    # past the periods).  The fold caps a healthy loan's extra installments to
    # no-ops.
    for due in _extension_dates(contractual):
        _synthesize(due, period_for_date(fwd.periods, due).period_pi)
    return estimated


def _charge_dates(contractual: list, through: date) -> list[date]:
    """Return the contract's installment sequence from origination through *through*.

    The one statement of the loan's CHARGE calendar's dates: every row of the
    contractual schedule, then the level sequence continued one month at a
    time -- past the contractual payoff by at least
    :data:`_PAYOFF_EXTENSION_MONTHS` (finding N-16: an underpaying loan clears
    late, and a payoff the sequence stops short of reads as no payoff), and as
    far as *through* whenever a plan payment lies beyond that.  The
    contract-only estimate walks the same sequence for its extension and the
    definition walk takes its window from it, so the three cannot stop at
    different months.  Empty for an empty schedule.

    *through* covers what an adversarial review of this step found: a loan
    more than sixty months past its contractual last installment with a live
    projected row (``_secured_debt`` names the matured-but-owing loan as
    reachable) would have folded that row against NO charge, where the
    calendar used to follow the payments.

    Args:
        contractual: The pure contractual schedule from origination to payoff.
        through: The last day a plan payment falls on; the sequence runs to
            the later of that day's MONTH and the extension's end.

    Returns:
        The installment dates, ascending, one month apart past the contract.
    """
    if not contractual:
        return []
    dates = [row.payment_date for row in contractual]
    last_due = dates[-1]
    months_out = 1
    # By MONTH, not by day: a payment on the 10th of a month whose contract
    # day is the 22nd still faces that month's charge, dated the 22nd.
    while (
        months_out <= _PAYOFF_EXTENSION_MONTHS
        or installment_slot(add_months(last_due, months_out))
        <= installment_slot(through)
    ):
        dates.append(add_months(last_due, months_out))
        months_out += 1
    return dates


def _extension_dates(contractual: list) -> list[date]:
    """Return the :data:`_PAYOFF_EXTENSION_MONTHS` installment dates past the contract's last.

    The contract-only estimate's extension and the definition walk's window:
    :func:`_charge_dates` beyond the contractual rows, cut at the extension.

    Args:
        contractual: The pure contractual schedule from origination to payoff.

    Returns:
        The extension dates, ascending, one month apart from the last
        contractual installment; empty for an empty schedule.
    """
    if not contractual:
        return []
    return _charge_dates(contractual, contractual[-1].payment_date)[
        len(contractual):
    ]


def _charges_for(
    contractual: list,
    payments: list[PlannedPayment],
    seed_slots: set[tuple[int, int]],
    last_anchor: date,
    fwd: _ForwardInputs,
) -> list[AccrualCharge]:
    """Return one :class:`AccrualCharge` per CONTRACTUAL installment the plan owes.

    **The TIME half of the plan, on the CONTRACT's calendar** (plan step
    **R16-b-2**, rulings **R-R68** and **R-R71**).  A loan charges a month's
    interest because a month passed and impounds a month's escrow because a
    month began, on the contract's installment dates, whatever the owner's
    payments do.  Until this step the calendar was derived from the PAYMENTS
    -- one charge per month they occupied -- which was exact only while the
    ESTIMATED tier filled every month; summing definitions on their own
    cadence deletes that fill, and a definition sparser than monthly would
    have collapsed the charge set with the payment set (finding **D48**'s
    quarterly case: 40 charges where the contract owes 120).

    Two rules decide which contractual installments are charged, and each
    is a finding closed rather than a case handled:

    * **every installment after the loan's LAST BALANCE ASSERTION** visible
      at the read -- the origination anchor or the latest true-up -- whether
      or not a payment lands in it, past or future (rulings **R-R37**,
      **R-R71** and **R-R72**, finding **D53**), so an occurrence the owner
      cancelled leaves its month charged and the balance grows.  The
      assertion is the boundary because the owner's own statement of the
      balance supersedes every installment at or before it -- charged OR
      paid, which is why :func:`loan_plan` drops the payments on the same
      predicate (:func:`~app.services.rate_period_engine.due_after_anchor`);
      ``as_of`` is deliberately NOT the boundary, because a skipped month
      owes its interest whichever side of today it is on, and bounding at
      the read day made the same records answer two payoffs on two days;
    * **never an installment whose month the SEED already charged** -- the
      settled walk charged the month of every payment settled by ``as_of``,
      and a forward charge on the same month was finding **D54**'s double
      charge.  Unreachable now rather than guarded.

    The dates come from the contractual schedule itself
    (:func:`_charge_dates`), origination through the post-contractual
    extension and as far as the last plan payment, so a charge is resolved --
    its rate period, its escrow -- on the contract's own day (ruling D5's
    contract time).  The charges are BUILT by
    :func:`~app.services.loan_ledger.charges_for_due_dates`, the one producer
    of an :class:`AccrualCharge` both walks share; handed one date per month
    it collapses nothing and dates each at the contract's day.

    A plan payment dated before the loan's first installment has no
    contractual month to be charged against and pays what stands: ruling R-C
    allows an ad-hoc extra after origination and before the first installment,
    and that payment is pure principal, which is what an early extra IS.

    Args:
        contractual: The pure contractual schedule from origination to payoff.
        payments: The plan's forward payment records, in any order -- every
            one already after *last_anchor* (:func:`loan_plan` drops the
            rest), so the calendar reaches the last of them.
        seed_slots: The ``{(year, month)}`` months of every payment settled by
            ``as_of`` -- what the seed already charged.
        last_anchor: The date of the loan's latest balance assertion visible
            at the read.
        fwd: The resolved :class:`_ForwardInputs` -- its rate periods and
            escrow lines are what each charge is resolved against.

    Returns:
        One :class:`AccrualCharge` per charged installment, ascending by
        ``on_date``.
    """
    horizon = max(
        (payment.due_date for payment in payments), default=fwd.as_of,
    )
    charged = [
        due
        for due in _charge_dates(contractual, horizon)
        if installment_slot(due) not in seed_slots
        and due_after_anchor(last_anchor, due)
    ]
    return charges_for_due_dates(charged, fwd.periods, fwd.escrow_lines)


def _seed_boundaries(
    account: Account, resolved, ctx: BalanceContext,
) -> tuple[set[tuple[int, int]], date]:
    """Return what the seed already accounts for: its charged months and its last anchor.

    Two facts about the confirmed present the forward plan folds from, read
    once and handed to both tiers and the charge calendar:

    * the ``(year, month)`` of every payment SETTLED by ``as_of`` (visible by
      ``as_of``) -- what the settled walk already charged and paid.  The
      charge calendar excludes them (a forward charge there is finding
      **D54**'s double charge) and the contract-only estimate excludes them
      too, with the PLANNED records' months, so a month is folded exactly
      once;
    * the date of the latest balance ASSERTION the seed applied -- the
      origination anchor or the last true-up visible by ``as_of`` -- which is
      the boundary the charge calendar starts after (ruling **R-R71**).  For
      a loan not yet originated no anchor is visible, and the boundary is its
      origination date: the origination anchor IS that date, so the two
      spellings are one predicate.

    Args:
        account: The loan account.
        resolved: The pass's :class:`~._resolution.ResolvedLoan`.
        ctx: The read pass.

    Returns:
        ``(seed_slots, last_anchor)``.
    """
    seed_slots = {
        installment_slot(
            loan_payment_due_date(shadow, resolved.params.payment_day),
        )
        for shadow in confirmed_shadows_through(
            account.id, ctx.scenario_id, ctx.as_of,
        )
    }
    last_anchor = max(
        (
            fact.anchor_date
            for fact in resolved.anchor_facts
            if anchor_visible_on(fact.anchor_date) <= ctx.as_of
        ),
        default=resolved.params.origination_date,
    )
    return seed_slots, last_anchor


def loan_plan(account: Account, ctx: BalanceContext) -> LoanForwardPlan:
    """Return *account*'s forward model -- what it will be CHARGED and what it PAYS.

    The unified forward record stream a loan's projected balance folds (see the
    module docstring): every projected transfer shadow at its resolved cash,
    plus what every definition paying into the loan would generate that no row
    answers yet -- or, for a loan with no definition, the contract's own
    installments -- out to payoff and the post-contractual extension, LESS
    every payment due at or before the loan's latest balance assertion (the
    assertion subsumes it, ruling R-R72); and, beside them, one
    :class:`AccrualCharge` per contractual installment the plan owes.  The
    value carries NO balance; a caller folds it with
    :func:`._plan_fold.fold_forward` seeded from the loan's confirmed present.

    Args:
        account: The amortizing loan account (the caller owns the ownership
            check).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            -- its scenario scopes the shadows and the resolution, its
            calendar places the definitions' occurrences, and its ``as_of`` is
            the clamp floor and the past/future boundary.

    Returns:
        The loan's :class:`LoanForwardPlan` -- its payments ascending by
        ``(effective_date, due_date)``, its charges by ``on_date``.  Both empty
        when *account* is not a configured loan (no
        :class:`~app.models.loan_params.LoanParams`).

    Raises:
        BaselineMissingError: When ``ctx.scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        AmountUnresolvable: When a definition states no price for an
            occurrence it names (see :func:`._plan_definitions.estimated_from_definitions`).
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
        as_of=ctx.as_of,
    )

    projected_shadows = loan_loaders.projected_income_shadows(
        account.id, ctx.scenario_id, options=pricing_load_options(),
    )
    # The pass's OWN loan derivation, not a second one built here: this line
    # called ``live_loan_transfer_amounts`` directly while the cash fold built a
    # basis that called it again, so one request resolved the same loan twice
    # (finding **N-268**'s shape).  Plan step X-au-c2b made the derivation a
    # read-pass value, so both readers ask the same one.
    planned = _planned_from_shadows(
        projected_shadows,
        amounts_by_id(projected_shadows, ctx.amounts()),
        fwd,
    )

    seed_slots, last_anchor = _seed_boundaries(account, resolved, ctx)
    contractual = contractual_schedule_from_origination(params, rate_changes)
    if resolved.definitions and contractual:
        # The walk's window: the extension past the contract, or the same
        # span past the READ when the loan has outlived it -- a matured loan
        # still owing, with a live definition, is paid by that definition's
        # occurrences beyond the extension (an adversarial review of this
        # step found the window ending in 2025 for a loan read in 2026, so
        # the plan held only the saved horizon's rows and the payoff read
        # ``None`` where the definition cleared it).
        estimated = estimated_from_definitions(
            account, resolved, ctx, fwd.as_of,
            through=max(
                _extension_dates(contractual)[-1],
                add_months(fwd.as_of, _PAYOFF_EXTENSION_MONTHS),
            ),
        )
    else:
        # No definition at all: the contract is the only estimate there is.
        covered_slots = {
            installment_slot(payment.due_date) for payment in planned
        } | seed_slots
        estimated = _estimated_from_contract(contractual, covered_slots, fwd)

    # An installment due at or before the loan's latest assertion is INSIDE
    # that assertion, planned or estimated (ruling R-R72): the settled walk
    # resets at the anchor after walking the day's payments, so a row due
    # before it contributes nothing to the post-assertion balance once it
    # settles -- and a plan that folded it would move by its whole cash the
    # day it did.  The same predicate bounds the charges (``_charges_for``).
    payments = sorted(
        (
            payment for payment in planned + estimated
            if due_after_anchor(last_anchor, payment.due_date)
        ),
        key=lambda payment: (payment.effective_date, payment.due_date),
    )
    return LoanForwardPlan(
        payments=payments,
        charges=_charges_for(
            contractual, payments, seed_slots, last_anchor, fwd,
        ),
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
