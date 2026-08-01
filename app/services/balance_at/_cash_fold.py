"""Balance-at-T seam -- the CASH fold: a walk of facts sampled into a balance.

Plan steps **X-b** and **X-c1** (``docs/audits/balance_architecture/README.md``).
"A cash account is an event stream" (Section 3), and this module is the half that
turns that stream into money: the :mod:`app.services.cash_ledger` leaf owns the
WALK (the facts), this owns the FOLD (the balance).  The same split plan step
D-fold made on the loan side, for the same reason -- *a fold is a balance; a walk
is a fact* -- and the same placement: because the prefix-sum lives HERE, a
consumer legitimately holding a
:class:`~app.services.cash_ledger.CashLedgerWalk` (the posting writer at plan
step X-d) cannot reach a balance from a public leaf name.

**Three tiers, ONE :func:`~app.services.balance_at._fold.sample_cumulative`, no
branch.**  Every date is answered off a single running total
(:func:`_running_steps`) assembled from:

* the **SEED** -- the account's first assertion back-projected over the records
  it already contains (ruling R-I).  See :func:`_actual_steps`.
* the **ACTUAL** steps -- X-a's walk re-keyed by the day each event became
  visible (:func:`app.services.cash_ledger.dated_deltas`, the ONE statement of
  that clock, shared with the posting writer so the fold and the posted ledger
  cannot drift).
* the **PLANNED** steps -- the still-Projected rows, each landing at
  ``max(its attribution date, as_of + 1 day)`` (ruling R-G: "a plan cannot have
  already happened").  The cash twin of
  :func:`app.services.balance_at._plan.fold_forward`.  See :func:`_cash_plan`
  and :func:`_planned_day_nets`.

**Three readers of that ONE row set** (plan steps X-c1 / X-c2b2, ruling R-K).
:func:`fold_cash_balances` samples the running total at a list of dates -- the
balance a scalar or a daily series asks for.  :func:`cash_period_balances`
samples it at each pay period's end -- the per-period map.
:func:`cash_period_view` samples the same period ends AND groups the very same
rows a second way, by the period each was BUDGETED to, so the grid's balance row
and its subtotal rows reconcile by construction with a named remainder for what
neither clock alone can explain.  The assembly is shared rather than duplicated
(:func:`assemble`): one walk, one plan load, one valuation, whichever reader is
asking.

Keeping it to one sample is not economy, it is the correctness property: a fold
assembled from a past producer spliced to a future producer needs a rule for the
join, and every such rule in this codebase's history is a place the two sides
disagreed.  There is no join here -- the planned steps are simply later steps on
the same running total.

**What this fixes, and it is measured.**  The shipping projection starts at the
LATEST assertion and sums only the still-Projected rows forward, so:

* a settled row attributed AFTER that assertion is counted by NO producer
  (finding cash D1 -- on production 2026-07-25, ``$2,000.00`` of a Money Market
  transfer and ``$108.15`` on Checking, invisible at that instant, and
  ``$53,880.81`` gross across 130 rows in 45 assertion gaps historically).  Here
  it is an ordinary ACTUAL step that rides on top of the assertion it followed.
* a date before the latest assertion reads TODAY's balance (the scalar) or
  nothing at all (the period map) -- finding B-18 / cash D3.  Here every
  assertion is replayed, so a past date reads the balance in force THEN.
* an overdue-but-still-Projected bill is absorbed by the next re-anchor and
  silently deleted from the projection (on real data, one re-anchor every 2.3
  days).  Here R-G clamps it forward instead.

**TOTAL over every date and every account.**  No date is refused and no account
is: an account with no assertion history folds from a zero seed over whatever
facts it has, a date before every event reads the seed, and a future date
answers.  That totality is what deletes a partial producer's need to be composed
with a seed, a flag or a fallback -- and every such composition is a new producer
that can disagree with the others (plan Section 3).  A caller that must
distinguish "holds nothing" from "no account" asks the account row, never this
function's number.

**Kind-blind by design (ruling R-J).**  It consults no
:class:`~app.services.account_projection.AccountProjectionKind`: this is the
cash-flow view, whose balance must reconcile with the transaction rows rendered
beside it whatever the account.  What keeps a LOAN out of that view is a gate at
the SOURCE -- ``resolve_grid_account`` since plan step A1 and
``resolve_analytics_account`` since X-a1 -- not a refusal in here, which would
reintroduce exactly the partiality above.

**WIRED since plan step X-c2b2.**  Every cash figure the app renders is one of
these three readings: the seam's three cash-flow entries
(:mod:`app.services.balance_at._cash_flow`), the kernel's PLAIN fall-through and
INTEREST seed (:mod:`app.services.balance_at._kernel`), the kind-correct
scalar's PLAIN and degraded-AMORTIZING branches
(:mod:`app.services.balance_at._kind_correct`), and the grid's whole column set
(:mod:`app.services.balance_at._grid`).  That is the cutover: the settled drop,
the scalar/daily fork and the pre-anchor fabrication close together, because one
total fold subsumes all three.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from bisect import bisect_right
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services.cash_ledger import (
    CashLedgerWalk,
    dated_deltas,
    live_amount_overrides,
    planned_cash_rows,
    sum_projected,
    walk_cash_ledger,
)
from app.utils.dates import attribution_date
from app.utils.money import round_money

from ._fold import sample_cumulative

_ZERO_MONEY = Decimal("0.00")
# Ruling R-G's clamp floor: the earliest day a plan can still happen is the day
# AFTER the reader's as-of.  Named (and spelled the same way) as the loan twin's
# ``_plan._ONE_DAY``, because it is the same rule -- ruling D1 for loans, R-G for
# cash -- and the two are meant to read as one.
_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class AssembledCashFold:
    """One account's whole running total, plus the facts it was built from.

    The output of :func:`assemble`, and the reason the three readers below are
    readings of ONE valued row set rather than three producers a test keeps in
    step (ruling R-K).  It carries the sampling inputs (:attr:`seed` /
    :attr:`steps`) AND the groupings the period view regroups the same rows by
    (:attr:`walk` / :attr:`plan` / :attr:`day_nets`), so no reader re-loads or
    re-values anything a sibling reader already has.

    **A fourth reader lives one module over** (plan step X-g1): the modelled
    asset fold (:mod:`app.services.balance_at._asset_fold`) takes this whole
    record and resolves two MORE event kinds onto the same running total --
    CONTRIBUTION and ACCRUAL.  That is why :func:`assemble` and this record are
    seam-visible rather than module-private: a modelled asset IS its cash fold
    plus a modelled return (plan Section 3.2), so the two must share ONE
    assembly rather than the modelled side re-deriving a cash basis.  It reads
    :attr:`walk` for the latest ASSERTION, which is where its accrual window
    opens (ruling R-L, generalised at ruling R-Y).

    Attributes:
        scenario_id: The budget scenario the rows below were scoped by.  Carried
            so the record is self-describing: a reader that resolves something
            FURTHER off this fold -- the modelled tiers
            (:func:`app.services.balance_at._asset_fold.resolve`) load a
            contribution feed of their own -- scopes that load off the fold it is
            extending rather than off a scenario passed beside it.  A caller
            cannot then hand the two different scenarios, which is the only way
            they could have disagreed (plan Section 8: an argument a caller can
            get wrong is a defect, not a contract).
        seed: The balance before every step (ruling R-I's back-projection).
        steps: The dated deltas, ASCENDING by date -- the ACTUAL tier, the
            opening's compensator, and the PLANNED tier merged into one list.
        walk: The account's :class:`~app.services.cash_ledger.CashLedgerWalk`
            (the settled facts and the assertion corrections).
        plan: The account's :class:`_CashPlan` (its still-Projected rows, the
            day each lands on, and the live override map).
        day_nets: The PLANNED tier's per-day nets.
    """

    scenario_id: int
    seed: Decimal
    steps: "list[tuple[date, Decimal]]"
    walk: CashLedgerWalk
    plan: _CashPlan
    day_nets: "dict[date, Decimal]"


def assemble(
    account: Account, scenario_id: int, as_of: date,
) -> AssembledCashFold:
    """Walk the account's facts and load its plan -- ONCE, for every reader.

    Args:
        account: The account to value.  Its ``id`` scopes the walk and the plan,
            and its ``user_id`` scopes the live salary override; its KIND is not
            consulted (ruling R-J).  Must be attached to ``db.session``.
        scenario_id: The budget scenario whose rows to fold.  Assertions are
            per-ACCOUNT and replay in every scenario; only the transaction rows
            are scenario-scoped.
        as_of: The reader's NOW, and since plan step X-c2c1 deleted the
            reservation's entry window it does exactly ONE job: it is the floor
            a still-Projected row's effective date is clamped up to (ruling
            R-G).  It decides WHEN a row lands, never what it is worth.

    Returns:
        The :class:`AssembledCashFold`.
    """
    walk = walk_cash_ledger(account.id, scenario_id)
    plan = _cash_plan(account, scenario_id, as_of)
    day_nets = _planned_day_nets(plan)
    seed, steps = _running_steps(walk, day_nets)
    return AssembledCashFold(
        scenario_id=scenario_id,
        seed=seed, steps=steps, walk=walk, plan=plan, day_nets=day_nets,
    )


def fold_cash_balances(
    account: Account,
    scenario_id: int,
    as_of: date,
    dates: list[date],
) -> dict[date, Decimal]:
    """Return the account's folded cash balance at each of *dates*.

    The cash counterpart of
    :func:`app.services.balance_at._fold.fold_loan_balances`, and the producer
    the seam's cash-flow SCALAR and DAILY SERIES read (plan step X-c2b2).  ONE
    walk of the account's facts plus ONE load of its plan, sampled at every
    requested date -- so N dates cost one pass, not N, which is what lets the
    daily series be a sampling of the period map's own running total rather than
    a second producer that drifts from it (finding cash D2: ``$15.96`` apart on
    the real Checking account the day before the cutover, ``$246.36`` at the
    worst day of the current period).

    **Two dates, and they are not the same date** (the contract the seam's
    context documents): *as_of* is the reader's NOW -- what decides that a plan
    cannot already have happened -- while each of *dates* is a VALUATION date,
    which may be long before it (a historical read) or long after (a projection).
    Passing ``as_of`` as a valuation date is ordinary, not special.

    Args:
        account: The account to value (see :func:`assemble`).
        scenario_id: The budget scenario whose rows to fold.
        as_of: The reader's NOW (ruling R-G's clamp floor).
        dates: The dates to value the account at, in any order.  Duplicates
            collapse.

    Returns:
        ``{date: balance}`` -- one cent-quantized ``Decimal`` per distinct
        requested date.  ``{}`` for an empty *dates*.
    """
    folded = assemble(account, scenario_id, as_of)
    return sample_cumulative(folded.seed, folded.steps, dates)


def cash_period_balances(
    account: Account,
    scenario_id: int,
    as_of: date,
    periods: "list[PayPeriod]",
) -> "OrderedDict[int, Decimal]":
    """Return the account's folded balance at each period's END, by period id.

    The per-period map (plan step X-c2b2): the seam's ``cash_balance_map``, the
    kernel's PLAIN fall-through, and the INTEREST accrual's SEED all read it, so
    a period column, the scalar sampled at that column's end date, and the daily
    series' last day of it are one running total read at three grains.

    **TOTAL over the periods it is given**, which is the cutover's whole point:
    the shipping producer carried the anchor forward and OMITTED every
    pre-anchor period, so a past column rendered blank while the account plainly
    held money (finding cash D3 / B-18 -- on the real Checking account, eight
    blank columns).  Every assertion is replayed here, so a past period reads the
    balance in force THEN.

    Args:
        account: The account to value (see :func:`assemble`).
        scenario_id: The budget scenario whose rows to fold.
        as_of: The reader's NOW (ruling R-G's clamp floor).
        periods: The pay periods to value, in the caller's display order.  They
            need not be contiguous and need not start at the account's anchor.

    Returns:
        ``OrderedDict`` period id -> cent-quantized ``Decimal``, in the order
        *periods* was given.  EVERY input period is present.
    """
    return _period_balances(
        assemble(account, scenario_id, as_of), periods,
    )


def _period_balances(
    folded: AssembledCashFold, periods: "list[PayPeriod]",
) -> "OrderedDict[int, Decimal]":
    """Sample an assembled fold at each period's ``end_date``, keyed by period id.

    Args:
        folded: The account's :class:`AssembledCashFold`.
        periods: The pay periods to value, in the caller's display order.

    Returns:
        ``OrderedDict`` period id -> cent-quantized ``Decimal``.
    """
    sampled = sample_cumulative(
        folded.seed, folded.steps, [period.end_date for period in periods],
    )
    return OrderedDict(
        (period.id, sampled[period.end_date]) for period in periods
    )


def _actual_steps(
    walk: CashLedgerWalk,
) -> "tuple[Decimal, list[tuple[date, Decimal]]]":
    """Return the ``(seed, steps)`` the RECORDED facts contribute.

    The steps are the leaf's :func:`app.services.cash_ledger.dated_deltas` plus
    ONE appended step, and nothing is re-keyed or re-valued.  That restraint is
    deliberate: the same re-key is what the posting writer consumes at plan step
    X-d, and a second statement of "which day does this event count from, and for
    how much" is precisely how the fold and the posted ledger drift apart (the
    shape plan step E1a found on the loan side).  The one appended step is the
    seed's compensator, below.

    **The seed is ruling R-I, and the mechanism is one subtraction.**  The walk
    seeds at zero, so a prefix taken BEFORE the account's first assertion is that
    assertion's preceding records summed from nothing -- ``-$500.00`` on a real
    account, a balance it never had.  A cash assertion is a RESET, not an
    origination: unlike a loan's ``origination_date`` (a fact, so ``0.00`` before
    it is TRUE), an account's first
    :class:`~app.models.account.AccountAnchorHistory` row is a TRACKING start, and
    on the real data it is a ``cfb15e782f86`` BACKFILL row created days to weeks
    after the account existed and held money.  So the fold BACK-PROJECTS: the
    opening's correction moves out of the step list and into the SEED, which is
    the same thing as saying the FIRST assertion books no correction while every
    later one keeps its reset.

    Concretely, with ``A`` the asserted balance and ``P`` the sum of the records
    attributed at or before it (which is exactly the walk's own
    :attr:`~app.services.cash_ledger.CashAnchorCorrection.balance_before`, since
    the running balance starts at zero and no assertion precedes this one), the
    opening's emitted correction is ``A - P``.  Seeding there and booking an
    equal-and-opposite step on the opening's own day gives, at a date ``D``:

    * ``D`` before the opening: ``(A - P) + <records through D>`` -- the assertion
      carried backward over what it already contains, holding flat at ``A - P``
      before the earliest record;
    * ``D`` at or after it: ``(A - P) + P + (P - A) + ... = <the zero-seeded
      total>`` -- byte-identical to the walk, which is the half R-I does not
      touch.

    That cancellation depends on ``dated_deltas`` emitting the opening at exactly
    the day and amount the compensator books.  It no longer RE-DERIVES either
    (plan step X-c1): both read the correction's own
    :attr:`~app.services.cash_ledger.CashAnchorCorrection.observed_on` /
    :attr:`~app.services.cash_ledger.CashAnchorCorrection.delta`, so the pair is
    stated once on the record and the leaf's list is a merge of the same pair.
    The pin stays, because "one statement" is a property of today's code rather
    than of the contract:
    ``TestTheOpeningMovesIntoTheSeed.test_at_and_after_the_opening_it_equals_the_zero_seeded_walk``
    asserts the at-and-after region equals a zero-seeded sample of the same
    steps.  Measured: seeding at zero while keeping the compensator -- the
    half-applied form -- fails 27 of this step's 29 tests, that pin among them.

    Args:
        walk: The account's :class:`~app.services.cash_ledger.CashLedgerWalk`.

    Returns:
        ``(seed, steps)`` -- the balance before every step, and the dated deltas
        with the opening's compensator appended.  ``steps`` is a fresh list the
        caller owns and may extend, and it is NOT sorted: the compensator is
        appended after ``dated_deltas``' own ordering, so the caller sorts once
        after merging in the planned tier (which
        :func:`~app.services.balance_at._fold.sample_cumulative` requires).
        Within-day order is immaterial -- ``sample_cumulative`` reads a day's
        boundary AFTER every step on it, so only the day's SUM is observable.
    """
    steps = dated_deltas(walk)
    if not walk.anchor_corrections:
        # No assertion at all: production-unreachable (migration
        # ``cfb15e782f86`` plus the account factory guarantee an opening), and
        # the walk is empty here anyway.  Folding it from zero is the honest
        # fold of no facts rather than a raise -- the totality rule.
        return _ZERO_MONEY, steps

    opening = walk.anchor_corrections[0]
    steps.append((opening.observed_on, -opening.delta))
    return opening.delta, steps


def _running_steps(
    walk: CashLedgerWalk, day_nets: "dict[date, Decimal]",
) -> "tuple[Decimal, list[tuple[date, Decimal]]]":
    """Return the ``(seed, steps)`` a whole cash account folds from.

    The ONE assembly, and the reason both readers in this module are readings of
    ONE valued row set rather than two producers a test keeps in step (ruling
    R-K): :func:`fold_cash_balances` samples these steps for a balance, and
    :func:`cash_period_view` samples them for its balance row while grouping the
    SAME facts on the budget clock beside it.

    Args:
        walk: The account's :class:`~app.services.cash_ledger.CashLedgerWalk`.
        day_nets: The PLANNED tier's per-day nets
            (:func:`_planned_day_nets`), merged in as later steps on the same
            running total -- never spliced on as a second producer's series.

    Returns:
        ``(seed, steps)`` with *steps* ASCENDING by date, which is what
        :func:`~app.services.balance_at._fold.sample_cumulative` requires.
    """
    seed, steps = _actual_steps(walk)
    steps.extend(day_nets.items())
    steps.sort(key=lambda step: step[0])
    return seed, steps


@dataclass(frozen=True)
class _CashPlan:
    """The account's still-Projected rows, loaded and dated once.

    The PLANNED tier's inputs, shared by the two readers in this module so a
    plan is loaded (and its live override map built) ONCE per call whichever
    reader is asking.  Splitting the LOAD from the reduction is what lets the
    period view group the same rows on the budget clock without a second query
    -- redundant derivation being where a divergence hides (the lesson the read
    pass's context was built on).

    Attributes:
        rows: Every still-Projected balance-contributing row for the account in
            the scenario, unwindowed (:func:`~app.services.cash_ledger.planned_cash_rows`).
        by_day: The same rows keyed by the day each LANDS on (ruling R-G's
            clamp) -- the cash clock.  Empty when the account has no plan.
        overrides: The live ``{transaction_id: Decimal}`` map, built ONCE over
            the whole plan and threaded into every reduction (the established
            build-once-and-thread pattern): each seam picks its own candidates
            and both filter to ``is_projected``, so a map built over the plan
            alone is identical on every key that can matter.
    """

    rows: "list[Transaction]"
    by_day: "dict[date, list[Transaction]]"
    overrides: "dict[int, Decimal]"


def _cash_plan(
    account: Account, scenario_id: int, as_of: date,
) -> _CashPlan:
    """Load the account's plan and key each row onto the day it lands on.

    The PLANNED tier's load, and the reason it lives in this READER rather than
    in the clock-free leaf: a plan's effective date is a function of *as_of*
    (ruling R-G), exactly as the loan plan's is (plan step C6a).

    **Where a planned row lands.**  Its nominal day is the shared
    :func:`~app.utils.dates.attribution_date` -- its ``due_date``, falling back to
    its pay period's ``start_date``, clamped into that period's span -- the SAME
    rule the calendar groups its day cells by, so a flow's cell and the balance
    line's step for it cannot land on different days.  That day is then clamped
    UP to ``as_of + 1``: a plan cannot have already happened, so an overdue bill
    moves forward rather than being absorbed by the next assertion's reset.
    Rejected at the ruling: landing it on its nominal date, which on real data
    (one re-anchor every 2.3 days on Checking) silently deletes nearly every
    unpaid past-due bill within days of its being entered.

    **The load is separate from the reduction, and that is plan step X-c1's
    doing.**  The rows are kept, not just their per-day totals, because the
    period view reduces the SAME rows a second way -- by the budget column they
    were attributed to -- and re-loading them for that would be two answers to
    "what is in this account's plan" a day apart from each other.

    Args:
        account: The account whose plan to load (its ``user_id`` scopes the live
            salary override).
        scenario_id: The budget scenario the rows live in.
        as_of: The reader's NOW -- the floor ruling R-G clamps a landing day up
            to.

    Returns:
        The account's :class:`_CashPlan`; its ``rows`` and ``by_day`` are empty
        for an account with no plan.
    """
    rows = planned_cash_rows(account.id, scenario_id)
    if not rows:
        return _CashPlan(rows=[], by_day={}, overrides={})

    not_before = as_of + _ONE_DAY
    by_day: "dict[date, list[Transaction]]" = defaultdict(list)
    for txn in rows:
        period = txn.pay_period
        nominal = attribution_date(
            txn.due_date, period.start_date, period.end_date,
        )
        by_day[max(nominal, not_before)].append(txn)
    return _CashPlan(
        rows=rows,
        by_day=dict(by_day),
        overrides=live_amount_overrides(account, scenario_id, rows),
    )


def _planned_day_nets(plan: _CashPlan) -> "dict[date, Decimal]":
    """Return ``{day: net}`` for the plan -- what each landing day is WORTH.

    The PLANNED tier's reduction, split from its load (:func:`_cash_plan`) so the
    two readers in this module share one valuation.

    **What a planned row is WORTH is not re-implemented here.**  Each day's group
    is reduced by the shared
    :func:`~app.services.cash_ledger.sum_projected` -- the same engine the
    shipping period walk and the grid's subtotal row both call, carrying the
    entries-aware reservation for an envelope expense and the live override for a
    salary paycheck or a derived loan debit.  Reducing per GROUP rather than per
    ROW is what keeps that one rule intact: ``sum_projected`` is additive over
    disjoint groups, so the days of a period sum to the period's net exactly --
    which is also why :func:`_budget_legs` may reduce the same rows grouped by
    pay period and get an answer that reconciles with this one to the cent.

    **The reservation reads no clock, and that is the whole of finding N-39's
    resolution.**  This reduction once passed *as_of* as ``sum_projected``'s
    entry-date window, so an entry dated after the reader's now could not reduce
    the reservation early -- a deliberate pick between the two answers the three
    shipping producers gave (the scalar windowed, the grid and the daily ramp did
    not).  Ruling R-M closed the fork at the SOURCE rather than picking a winner:
    plan step X-c0 refuses ``entry_date > display_today()`` at both write doors,
    after which the window could drop nothing, and plan step X-c2c1 deleted it.
    So a planned row's WORTH is now a function of the row alone and *as_of*
    survives in this module for exactly one job -- ruling R-G's clamp, which
    :func:`_cash_plan` applied when it keyed these groups by day.

    Args:
        plan: The account's :class:`_CashPlan`, already keyed onto the day each
            row lands on (ruling R-G's clamp, applied at load).

    Returns:
        ``{day: net}`` -- one entry per day carrying at least one planned row,
        valued as signed income-minus-expense.  ``{}`` for an account with no
        plan.
    """
    nets: "dict[date, Decimal]" = {}
    for day, txns in plan.by_day.items():
        income, expense = sum_projected(txns, plan.overrides)
        nets[day] = income - expense
    return nets


@dataclass(frozen=True)
class CashPeriodFigures:
    """One pay period's cash column: the balance, the subtotals, the remainder.

    The per-period output of :func:`cash_period_view`, and the three grid rows
    ruling R-K makes ONE row set grouped two ways.  For every period and every
    account kind, in terms of :attr:`balance` below::

        balance(p.end) - balance(p.start - 1 day) == net + reconciliation

    (the boundary form, so the FIRST period is covered too -- it has no
    predecessor column to subtract).  That identity is a property of the
    construction, not a coincidence: :attr:`net` sums the rows attributed to the
    period on the BUDGET clock, :attr:`reconciliation` is what the same rows and
    the same assertions contribute on the CASH clock MINUS that budget sum, and
    the balances are the fold of those very steps.  Verified on the prod-shape
    clone 2026-07-25 over 360 (account, period) pairs -- 6 non-loan accounts x
    60 periods -- with zero breaks.

    An INTEREST account's modelled accrual is NOT in :attr:`balance` (this is
    the kind-blind CASH-FLOW view), so a grid layering it back on adds R-K's
    third term and reads
    ``balance[p] - balance[p-1] == net[p] + reconciliation[p] + increments[p]``.

    Attributes:
        balance: The fold's balance at the period's ``end_date``, cent-quantized
            -- assertions replayed, settled cash counted from the day it moved,
            and the still-projected plan clamped forward (ruling R-G).
        income: Every row ATTRIBUTED to this period whose type is income --
            settled at its confirmed cash leg, still-projected at its live or
            entries-aware amount.  A magnitude, cent-quantized.
        expense: The same, for expense rows.  A magnitude (positive), so the
            column reads ``income`` minus ``expense``.
        net: ``round_money(income - expense)`` -- rounded ONCE at the boundary
            rather than as the difference of two separately-rounded legs, the
            same discipline :class:`~app.services.cash_ledger.PeriodSubtotal`
            keeps and for the same reason: it is the figure the balance
            roll-forward has to reconcile with.
        reconciliation: What the two subtotal rows cannot explain about the
            balance change: money BUDGETED here that moved in another period (or
            has not moved yet), money that MOVED here but is budgeted elsewhere,
            and the balance ASSERTIONS the user made inside the period.  Zero for
            a period where every row settled in its own column and nobody
            re-anchored -- which is every FUTURE period, so the row is
            conditional on screen.
    """

    balance: Decimal
    income: Decimal
    expense: Decimal
    net: Decimal
    reconciliation: Decimal


@dataclass(frozen=True)
class CashPeriodView:
    """A whole account's cash columns, and the income basis they were valued on.

    The output of :func:`cash_period_view`.  The override map rides on the
    result rather than being the caller's ARGUMENT (ruling R-Q): it is what the
    projection was actually computed with, so a consumer that renders the
    individual rows beside the columns -- the budget grid -- prices each row off
    the same map its balance row folded, BY CONSTRUCTION.  Passing it in was the
    only way for the two to disagree, and an argument a caller can get wrong is a
    defect rather than a contract (plan Section 8).

    Attributes:
        columns: ``OrderedDict`` period id -> :class:`CashPeriodFigures`, in the
            order the caller's *periods* were given.  Every input period is
            present.
        amount_overrides: The live ``{transaction_id: Decimal}`` map the
            still-Projected rows were valued through -- recomputed salary income
            and derived loan debits.  ``{}`` for an account with no plan.
    """

    columns: "OrderedDict[int, CashPeriodFigures]"
    amount_overrides: "dict[int, Decimal]"


def cash_period_view(
    account: Account,
    scenario_id: int,
    as_of: date,
    periods: "list[PayPeriod]",
) -> CashPeriodView:
    """Return the account's cash column for each of *periods* -- ruling R-K.

    ONE valued row set, grouped on TWO clocks, plus the assertion steps.  The
    same walk and the same plan :func:`fold_cash_balances` folds are grouped here
    a second way -- by the pay period each row was BUDGETED to -- so the grid's
    balance row and its subtotal rows stop being two producers that a test has to
    keep in step and become two readings of one set.

    **Why the subtotals had to change basis, measured.**  Today's subtotal counts
    only rows that are still UNPAID (``cash_ledger.sum_projected`` filters through
    ``is_projected``), while a balance folded from the facts counts money that
    MOVED.  On the real Checking account that identity breaks on 8 of 59 period
    pairs -- worst ``$2,505.17`` -- and every past column reads ``$0.00`` income
    and ``$0.00`` expenses while thousands of dollars moved through it
    (finding N-41).  So :attr:`~CashPeriodFigures.income` /
    :attr:`~CashPeriodFigures.expense` count EVERY row attributed to the period,
    which is budget-vs-actual and fixes the ``$0.00`` past columns as a side
    effect.  Verified on the prod-shape clone 2026-07-25: the eight past columns
    go from ``$0.00`` to real figures, the current column from ``-$140.63`` to
    ``$3,153.22``, and every FUTURE column is unchanged to the cent (nothing has
    settled there, so the two bases coincide).

    **What the remainder holds, and why it is one row.**  Three things the
    subtotal rows structurally cannot say: a row that settled outside its own pay
    period (19 of the real account's 130 settled rows; nets to ``$0.00`` across
    history and swings to ``-$2,007.46`` inside one period), a still-projected row
    ruling R-G clamped forward out of its column, and the balance ASSERTIONS (51
    after the opening on the real account, ``-$2,906.31`` net).  Rejected at the
    ruling: leaving the subtotals unpaid-only, which turns this row into a garbage
    bucket holding all real past activity, and shipping no remainder row at all,
    which leaves a visible contradiction on the screen.

    **The OPENING assertion is not in it** (ruling R-I): the fold moves that one
    correction into its seed -- back-projecting the first assertion over the
    records it already contains -- so it moves no balance inside any period and
    must not appear in a period's remainder either.

    Kind-blind, exactly as the fold is (ruling R-J): this is the CASH-FLOW view,
    whose balance has to reconcile with the transaction rows rendered beside it.
    An INTEREST account's accrual is layered on ABOVE this by the grid view,
    which is the ``+ increments[p]`` term of R-K's identity.

    Args:
        account: The account to project.  Must be attached to ``db.session``;
            its kind is not consulted.
        scenario_id: The budget scenario whose rows to group.
        as_of: The reader's NOW (ruling R-G's clamp floor) -- NOT a valuation
            date; each period is valued at its own ``end_date``.
        periods: The pay periods to report, in the caller's display order.  They
            need not be contiguous and need not start at the account's anchor:
            each period's figures are read off its OWN span, so a window is a
            window rather than a re-based projection.

    Returns:
        The :class:`CashPeriodView`: one :class:`CashPeriodFigures` per input
        period (a period with no rows and no assertions reports zeros against
        its folded balance), plus the live override map the projection was
        computed with.
    """
    return period_view_of(assemble(account, scenario_id, as_of), periods)


def period_view_of(
    folded: AssembledCashFold, periods: "list[PayPeriod]",
) -> CashPeriodView:
    """Regroup an ALREADY-assembled fold into its per-period columns.

    :func:`cash_period_view`'s body, split from its assembly so a reader that
    needs the cash columns AND something else off the same account pays for ONE
    walk, ONE plan load and ONE valuation rather than two (plan step X-g2a).
    The grid is that reader: from plan step X-g2b it renders the modelled
    balance -- :func:`app.services.balance_at._asset_fold.resolve` over this very
    :class:`AssembledCashFold` -- beside the budget-clock subtotals this returns,
    and calling both entry points would have assembled the account twice.

    Taking the assembled record rather than the account is what makes the
    sharing STRUCTURAL: the columns and whatever the caller resolves beside them
    are readings of one valued row set by construction, not two producers that a
    test keeps in step.

    Args:
        folded: The account's :class:`AssembledCashFold` (:func:`assemble`).
        periods: The pay periods to report, in the caller's display order.  See
            :func:`cash_period_view` for the windowing contract.

    Returns:
        The :class:`CashPeriodView`.
    """
    spans = _PeriodSpans.of(periods)
    return CashPeriodView(
        columns=_assemble_figures(
            periods,
            _period_balances(folded, periods),
            _budget_legs(folded.walk, folded.plan, spans),
            _cash_sums(folded.walk, folded.day_nets, spans),
            _assertion_sums(folded.walk, spans),
        ),
        amount_overrides=folded.plan.overrides,
    )


@dataclass(frozen=True)
class _PeriodSpans:
    """The reported periods, indexed for "which column does this day fall in".

    The CASH clock's grouping key.  A day is answered by the period whose
    ``[start_date, end_date]`` span CONTAINS it, and by nothing otherwise -- no
    nearest-period fallback, deliberately.  The seam's
    :func:`~app.services.loan_ledger.find_period_containing_date` does fall back
    to the latest period that ENDED before the target, which is right for the
    question it answers (an anchor correction needs a home period, and
    ``journal_entries.pay_period_id`` is NOT NULL) and wrong for this one: the
    identity this index serves reads a period's balance change as the steps
    inside its own span, so a step in a gap or past the horizon belongs to NO
    column and must not be pulled into the previous one.

    Pay periods do not overlap -- the generator rejects a batch whose earliest
    start falls on or before the latest existing ``end_date``, because two
    periods covering one day also make ``get_current_period`` nondeterministic --
    so the latest period STARTING on or before a day is the only candidate that
    can contain it, and one bisect answers.

    Attributes:
        starts: The periods' ``start_date`` values, ascending -- the bisect key.
        periods: The same periods in that ascending order.
    """

    starts: "list[date]"
    periods: "list[PayPeriod]"

    @classmethod
    def of(cls, periods: "list[PayPeriod]") -> "_PeriodSpans":
        """Index *periods* by start date (the caller's order is not assumed).

        Args:
            periods: The pay periods to report, in any order.

        Returns:
            The :class:`_PeriodSpans` index.
        """
        ordered = sorted(periods, key=lambda period: period.start_date)
        return cls(
            starts=[period.start_date for period in ordered], periods=ordered,
        )

    def containing(self, day: date) -> "int | None":
        """Return the id of the period whose span contains *day*, else ``None``.

        Args:
            day: The calendar day to place.

        Returns:
            The containing period's id, or ``None`` when *day* falls in a gap,
            before the first reported period, or after the last one's end.
        """
        index = bisect_right(self.starts, day) - 1
        if index < 0:
            return None
        period = self.periods[index]
        return period.id if day <= period.end_date else None

    def zeroed(self) -> "dict[int, Decimal]":
        """Return a ``{period_id: 0.00}`` accumulator over every reported period.

        Returns:
            One zero per reported period, so a component's dict is TOTAL over
            the window and a period with nothing in it reads ``0.00`` rather
            than being missing.
        """
        return {period.id: _ZERO_MONEY for period in self.periods}


def _budget_legs(
    walk: CashLedgerWalk,
    plan: _CashPlan,
    spans: _PeriodSpans,
) -> "dict[int, tuple[Decimal, Decimal]]":
    """Return ``{period_id: (income, expense)}`` on the BUDGET clock.

    Every row ATTRIBUTED to a reported period, whatever day its money moved:
    settled rows at the confirmed cash leg the walk already valued them at, and
    still-projected rows at the shared ``sum_projected`` reduction -- the same
    engine :func:`_planned_day_nets` reduces the same rows through on the other
    clock, which is why the two groupings reconcile to the cent.

    The income / expense split follows the transaction TYPE
    (:attr:`~app.services.cash_ledger.CashSourceFact.is_income`), never the sign
    of the row's value, and the difference is observable: a settled expense whose
    ``actual_amount`` was corrected BELOW its credit-card entries has a POSITIVE
    cash leg (it nets money back into checking) and still belongs on the expense
    row, as a negative expense.  An expense that came back is not income.  The
    net and the balance agree either way, which is exactly why the shape is
    pinned -- it is the only one that can tell the two rules apart, and without
    it this classification would be an untested claim
    (``test_cash_period_view.py``:
    ``test_a_row_counts_on_its_TYPE_row_even_when_its_cash_leg_inverts``).

    Args:
        walk: The account's walk -- its settled facts carry both clocks.
        plan: The account's :class:`_CashPlan`.
        spans: The reported periods.

    Returns:
        ``{period_id: (income, expense)}`` -- both magnitudes, UNROUNDED (the
        caller rounds once at the boundary), and total over the window.
    """
    income = spans.zeroed()
    expense = spans.zeroed()
    for fact in walk.source_facts:
        if fact.pay_period_id not in income:
            continue
        if fact.is_income:
            income[fact.pay_period_id] += fact.delta
        else:
            # A settled expense's leg is NEGATIVE (money left), and the expense
            # row on screen is a magnitude.
            expense[fact.pay_period_id] -= fact.delta
    by_period: "dict[int, list[Transaction]]" = defaultdict(list)
    for txn in plan.rows:
        if txn.pay_period_id in income:
            by_period[txn.pay_period_id].append(txn)
    for period_id, txns in by_period.items():
        projected_income, projected_expense = sum_projected(txns, plan.overrides)
        income[period_id] += projected_income
        expense[period_id] += projected_expense
    return {
        period_id: (income[period_id], expense[period_id])
        for period_id in income
    }


def _cash_sums(
    walk: CashLedgerWalk,
    day_nets: "dict[date, Decimal]",
    spans: _PeriodSpans,
) -> "dict[int, Decimal]":
    """Return ``{period_id: net}`` on the CASH clock -- what MOVED in the period.

    The same rows :func:`_budget_legs` groups by budget column, grouped instead
    by the day each one's money moves: a settled row on its ``settled_on``
    (``paid_at``'s display-timezone civil day, resolved once on the fact --
    ruling R-DH), a planned row on the day ruling R-G lands it.
    Assertions are NOT here -- they are :func:`_assertion_sums`, because an
    assertion is not a row.

    Args:
        walk: The account's walk.
        day_nets: The plan's per-day nets (:func:`_planned_day_nets`).
        spans: The reported periods.

    Returns:
        ``{period_id: net}`` -- signed, UNROUNDED, total over the window.
    """
    moved = spans.zeroed()
    for fact in walk.source_facts:
        period_id = spans.containing(fact.settled_on)
        if period_id is not None:
            moved[period_id] += fact.delta
    for day, net in day_nets.items():
        period_id = spans.containing(day)
        if period_id is not None:
            moved[period_id] += net
    return moved


def _assertion_sums(
    walk: CashLedgerWalk, spans: _PeriodSpans,
) -> "dict[int, Decimal]":
    """Return ``{period_id: correction}`` -- what the user's true-ups booked.

    Every assertion correction EXCEPT the opening's, on the civil day it was
    asserted.  The opening is excluded because ruling R-I moves it into the
    fold's seed (:func:`_actual_steps`), where it back-projects over the records
    it already contains rather than stepping the balance on its own day; counting
    it here would put a jump in a column the balance never took.

    The slice is the exact COMPLEMENT of :func:`_actual_steps`' ``[0]``, and
    that is why it is a slice rather than a second ``is_opening`` test: two
    independent predicates could come to disagree about which correction the
    seed swallowed, while ``[0]`` and ``[1:]`` partition the list by
    construction.

    Args:
        walk: The account's walk.  Its ``anchor_corrections`` are chronological
            and the FIRST is the opening (the leaf's own contract).
        spans: The reported periods.

    Returns:
        ``{period_id: correction}`` -- signed, UNROUNDED, total over the window.
    """
    asserted = spans.zeroed()
    for correction in walk.anchor_corrections[1:]:
        period_id = spans.containing(correction.observed_on)
        if period_id is not None:
            asserted[period_id] += correction.delta
    return asserted


def _assemble_figures(
    periods: "list[PayPeriod]",
    balances: "OrderedDict[int, Decimal]",
    legs: "dict[int, tuple[Decimal, Decimal]]",
    moved: "dict[int, Decimal]",
    asserted: "dict[int, Decimal]",
) -> "OrderedDict[int, CashPeriodFigures]":
    """Combine the three groupings into one :class:`CashPeriodFigures` per period.

    The remainder is computed HERE and directly -- ``what moved in the column``
    minus ``what was budgeted to it``, plus the assertions -- rather than as the
    leftover of the fold's balance change.  That distinction is the step's whole
    verification standard: a remainder defined as ``balance_delta - net`` makes
    the identity arithmetically true and therefore untestable, and it would
    silently ABSORB a row the budget grouping got wrong.  Computed from the row
    set, the identity is a claim the fold can falsify (Section 7.2).

    Rounding is once at the boundary, on the same discipline
    :class:`~app.services.cash_ledger.PeriodSubtotal` keeps: every leg reaching
    this point is already cent-quantized (stored ``Numeric(12,2)`` columns, and
    both live-override seams return ``round_money``), so the rounding is a no-op
    on real data and the identity holds on the DISPLAYED figures, not merely on
    the raw ones.

    Args:
        periods: The pay periods to report, in display order.
        balances: The fold sampled at every period ``end_date``, keyed by
            period id (:func:`_period_balances`).
        legs: The budget-clock ``(income, expense)`` per period.
        moved: The cash-clock net per period.
        asserted: The assertion corrections per period.

    Returns:
        ``OrderedDict`` period id -> :class:`CashPeriodFigures`.
    """
    figures: "OrderedDict[int, CashPeriodFigures]" = OrderedDict()
    for period in periods:
        income, expense = legs[period.id]
        net = income - expense
        figures[period.id] = CashPeriodFigures(
            balance=balances[period.id],
            income=round_money(income),
            expense=round_money(expense),
            net=round_money(net),
            reconciliation=round_money(
                moved[period.id] - net + asserted[period.id],
            ),
        )
    return figures
