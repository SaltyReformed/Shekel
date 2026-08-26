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
samples it at each pay period's end -- the per-period map.  And
:mod:`._cash_periods` samples the same period ends AND groups the very same rows
a second way, by the period each was BUDGETED to, so the grid's balance row and
its subtotal rows reconcile by construction with named remainders for what
neither clock alone can explain.  The assembly is shared rather than duplicated
(:func:`assemble`): one walk, one plan load, one valuation, whichever reader is
asking.

**The third reader lives in its own module since plan step S1-c**, when ruling
R-DH (f) split its remainder in two and this one passed the 1,000-line ceiling.
Assembling a running total and regrouping it into columns are two jobs sharing
exactly one input (:class:`AssembledCashFold`), and the dependency runs one way:
:mod:`._cash_periods` imports this and this imports nothing of it.

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

from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models.account import Account
from app.models.transaction import Transaction
from app.services.cash_ledger import (
    AmountBasis,
    CashLedgerWalk,
    dated_deltas,
    planned_cash_rows,
    sum_projected,
    walk_cash_ledger,
)
from app.services.pay_calendar import DerivedPeriod, PayCalendar, PeriodWindow
from app.utils.dates import attribution_date

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
    account: Account, basis: AmountBasis, as_of: date, calendar: PayCalendar,
) -> AssembledCashFold:
    """Walk the account's facts and load its plan -- ONCE, for every reader.

    Args:
        account: The account to value.  Its ``id`` scopes the walk and the plan;
            its KIND is not consulted (ruling R-J).  Must be attached to
            ``db.session``.
        basis: The read pass's
            :class:`~app.services.cash_ledger.AmountBasis`
            (:meth:`~app.services.balance_at.BalanceContext.amounts`).  It
            carries the SCENARIO whose rows are folded -- assertions are
            per-ACCOUNT and replay in every scenario; only the transaction rows
            are scenario-scoped -- so the scenario and the pricing it resolves
            under are one argument rather than two a caller could disagree
            about (plan step X-au-c2b).
        as_of: The reader's NOW, and since plan step X-c2c1 deleted the
            reservation's entry window it does exactly ONE job: it is the floor
            a still-Projected row's effective date is clamped up to (ruling
            R-G).  It decides WHEN a row lands, never what it is worth.
        calendar: The OWNER's pay calendar
            (:meth:`~app.services.balance_at.BalanceContext.calendar`), which
            :func:`_cash_plan` clamps each planned row against.  **Required,
            and a calendar rather than the read pass** (pay-calendar plan step
            C4-a-1): a fold assembled at one scenario beside a context carrying
            another would value the plan against rows this fold never saw,
            which is the argument ruling "How the CONTRIBUTION tier learns its
            periods" already made for ``_asset_fold.resolve`` one module over,
            and a calendar carries neither a scenario nor a clock so passing
            one reintroduces nothing.

    Returns:
        The :class:`AssembledCashFold`.

    Raises:
        RuntimeError: A planned row names a pay period this calendar does not
            hold -- see :func:`_cash_plan`.
    """
    walk = walk_cash_ledger(account.id, basis.scenario_id)
    # The plan load is INDEPENDENT of the walk since plan step X-f3b (ruling
    # **R-FM**): it took the walk so the entry reservation could ask which of an
    # envelope's purchases a declared balance already contained, and a purchase
    # carrying a posting day is now a movement in the walk itself, so the
    # reservation reads the purchase and the clearing rule is only ever asked
    # where the money is replayed.
    plan = _cash_plan(account, basis, as_of, calendar)
    day_nets = _planned_day_nets(plan)
    seed, steps = _running_steps(walk, day_nets)
    return AssembledCashFold(
        scenario_id=basis.scenario_id,
        seed=seed, steps=steps, walk=walk, plan=plan, day_nets=day_nets,
    )


def fold_cash_balances(
    account: Account,
    basis: AmountBasis,
    as_of: date,
    calendar: PayCalendar,
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
        basis: The read pass's amount basis, carrying the scenario whose rows
            are folded (see :func:`assemble`).
        as_of: The reader's NOW (ruling R-G's clamp floor).
        calendar: The owner's pay calendar (see :func:`assemble`).
        dates: The dates to value the account at, in any order.  Duplicates
            collapse.

    Returns:
        ``{date: balance}`` -- one cent-quantized ``Decimal`` per distinct
        requested date.  ``{}`` for an empty *dates*.
    """
    folded = assemble(account, basis, as_of, calendar)
    return sample_cumulative(folded.seed, folded.steps, dates)


@dataclass(frozen=True)
class CashDayFacts:
    """One day's folded balance, and the THREE things that moved it.

    **The decomposition lives here, beside :func:`_running_steps`, because it
    is that assembly read back.**  A consumer deriving it -- "whatever the
    balance moved by, less the rows I can see, must be a true-up" -- would be
    correct today and silently WRONG the day a fourth tier joins the running
    total, labelling it as something it is not.  Stating the split where the
    steps are built means a new tier is a change to this class rather than a
    mislabelled number on a report.

    Attributes:
        balance: The account's cash-flow balance at the END of the day, cent
            quantized -- the same figure :func:`fold_cash_balances` samples,
            from the same running total.
        recorded: What the account's OWN settled rows moved that day
            (:attr:`~app.services.cash_ledger.CashSourceFact.delta`, summed).
            The only one of the three that is money the app believes actually
            changed hands on that day.
        asserted: What BALANCE ASSERTIONS moved that day -- the jump each reset
            booked.  **The account's OPENING assertion contributes nothing**,
            and that is ruling R-I read back rather than an exclusion: the fold
            moves its correction into the SEED and books an equal-and-opposite
            step on its own day, so its net contribution there is zero and the
            figure it established is part of the level every later day is
            measured from.
        planned: What still-Projected rows contribute that day, each landing at
            ``max(its attribution date, as_of + 1)`` (ruling R-G).  Zero for
            every day at or before *as_of*, which is what lets a reader
            comparing PAST days against an outside record ignore it.
    """

    balance: Decimal
    recorded: Decimal
    asserted: Decimal
    planned: Decimal


@dataclass(frozen=True)
class CashDaySeries:
    """A day-grain reading of one account's fold, and where its records START.

    Attributes:
        facts: One :class:`CashDayFacts` per requested day.
        first_event_on: The earliest day this account has ANY cash fact -- a
            settled row or a balance assertion -- or ``None`` for an account
            with neither.

    **``first_event_on`` travels with the facts because the walk already knows
    it and every other way of learning it is worse.**  A reader comparing the
    app against an outside record has to tell "we disagree here" from "the app
    has no records this far back", and the developer's own Checking account is
    the case: his bank statement starts 2026-01-02 where his records start
    2026-03-26, so 83 days would otherwise read as 83 defects.  Inferring it
    from a flat PREFIX of the requested range would misread a quiet opening
    week as unrecorded and drop real disagreements out of the totals; asking a
    second walk for it would be the redundant derivation the read pass exists
    to prevent.
    """

    facts: "dict[date, CashDayFacts]"
    first_event_on: "date | None"


def _day_sums(
    pairs: "list[tuple[date, Decimal]]",
) -> "dict[date, Decimal]":
    """Return ``{day: total}`` for ``(day, amount)`` pairs.

    Args:
        pairs: The dated amounts to reduce.

    Returns:
        One entry per DISTINCT day present, summed.
    """
    sums: "dict[date, Decimal]" = {}
    for day, amount in pairs:
        sums[day] = sums.get(day, _ZERO_MONEY) + amount
    return sums


def fold_cash_day_facts(
    account: Account,
    basis: AmountBasis,
    as_of: date,
    calendar: PayCalendar,
    days: list[date],
) -> "CashDaySeries":
    """Return each day's folded balance beside the three tiers that moved it.

    The fourth reader of the ONE assembled row set (plan step
    ``bank_import:X-f6e-2``), and a reading rather than a second producer: the
    balance it reports is :func:`sample_cumulative` over the very
    ``(seed, steps)`` :func:`fold_cash_balances` samples, so the two cannot
    disagree about a day's balance.  What it adds is the SPLIT of that day's
    movement, which no other reader needs and a consumer must not re-derive
    (see :class:`CashDayFacts`).

    Args:
        account: The account to value (see :func:`assemble`).
        basis: The read pass's amount basis, carrying the scenario.
        as_of: The reader's NOW (ruling R-G's clamp floor).
        calendar: The owner's pay calendar (see :func:`assemble`).
        days: The days to answer, in any order.  Duplicates collapse.

    Returns:
        The :class:`CashDaySeries` -- one :class:`CashDayFacts` per distinct
        requested day, and the account's first recorded day.

    **The three components sum to the day's change in the running total**, and
    that is arithmetic rather than a claim: ``_running_steps`` assembles
    exactly ``dated_deltas`` (the source facts and the corrections) plus the
    opening's compensator plus the planned nets, so a day's steps ARE these
    three sums.  ``balance`` is quantized where the components are exact, so a
    reader wanting the identity to the cent must compare the components rather
    than differencing two rounded balances.
    """
    folded = assemble(account, basis, as_of, calendar)
    balances = sample_cumulative(folded.seed, folded.steps, days)
    recorded = _day_sums(
        [(fact.settled_on, fact.delta) for fact in folded.walk.source_facts]
    )
    # The opening's own correction is the SEED (:func:`_actual_steps`), and the
    # compensator cancels it on its day, so it is not a movement there.
    asserted = _day_sums(
        [
            (correction.observed_on, correction.delta)
            for correction in folded.walk.anchor_corrections[1:]
        ]
    )
    starts = [
        events[0] for events in (
            [fact.settled_on for fact in folded.walk.source_facts],
            [c.observed_on for c in folded.walk.anchor_corrections],
        ) if events
    ]
    return CashDaySeries(
        facts={
            day: CashDayFacts(
                balance=balance,
                recorded=recorded.get(day, _ZERO_MONEY),
                asserted=asserted.get(day, _ZERO_MONEY),
                planned=folded.day_nets.get(day, _ZERO_MONEY),
            )
            for day, balance in balances.items()
        },
        first_event_on=min(starts) if starts else None,
    )


def cash_period_balances(
    account: Account,
    basis: AmountBasis,
    as_of: date,
    calendar: PayCalendar,
    window: PeriodWindow,
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
        basis: The read pass's amount basis, carrying the scenario whose rows
            are folded (see :func:`assemble`).
        as_of: The reader's NOW (ruling R-G's clamp floor).
        calendar: The owner's pay calendar (see :func:`assemble`).
        window: The pay periods to value, as a slice of the owner's ONE derived
            calendar
            (:meth:`~app.services.balance_at.BalanceContext.reported_periods`).
            It need not start at the account's anchor.

    Returns:
        ``OrderedDict`` period id -> cent-quantized ``Decimal``, in payday
        order.  EVERY period of *window* is present.
    """
    return _period_balances(
        assemble(account, basis, as_of, calendar), window,
    )


def _period_balances(
    folded: AssembledCashFold, window: PeriodWindow,
) -> "OrderedDict[int, Decimal]":
    """Sample an assembled fold at each period's ``end_date``, keyed by period id.

    **The end it samples at is DERIVED, since plan step C2-c.**  It was
    ``PayPeriod.end_date``, a stored copy of ``lead(start_date) - 1`` with
    nothing reconciling it to the paydays it comes from, so a schedule whose
    stored ends had drifted valued a column on a day the calendar does not
    agree is that column's last (``docs/plans/implementation_plan_pay_calendar.md``
    section 1).  Measured on both production-shaped databases the day this
    shipped: 0 of 62 and 0 of 61 stored ends differ from the derivation, so
    the class of defect is latent rather than live, and plan step C4 removes
    the column that could hold it.

    Args:
        folded: The account's :class:`AssembledCashFold`.
        window: The pay periods to value.

    Returns:
        ``OrderedDict`` period id -> cent-quantized ``Decimal``.
    """
    sampled = sample_cumulative(
        folded.seed, folded.steps, [period.end_date for period in window],
    )
    return OrderedDict(
        (period.period_id, sampled[period.end_date]) for period in window
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
        basis: The READ PASS's
            :class:`~app.services.cash_ledger.AmountBasis` -- what every row is
            priced through -- carried here and threaded into every reduction so
            no reduction reaches for one of its own.  It was BUILT here, over
            this plan's rows, until plan step X-au-c2b: a basis is pinned to an
            owner and a scenario now rather than to a row set, so the pass hands
            one down instead and a second reader of the same request stops
            paying for a second paycheck-engine run (findings **N-268**,
            **N-269**).  It was a
            ``ProjectedBasis`` carrying the account's clearing rule beside this
            until plan step X-f3b: the entry reservation was what asked, and
            ruling **R-FM** made a purchase's posted-ness a fact about the
            PURCHASE, so the reservation reads the row in front of it and the
            clearing rule stayed with the WALK, which is the only thing that
            needs it.
    """

    rows: "list[Transaction]"
    by_day: "dict[date, list[Transaction]]"
    basis: AmountBasis


def _filed_span(calendar: PayCalendar, txn: Transaction) -> DerivedPeriod:
    """Return the DERIVED span of the paycheck *txn* is filed in.

    **The span comes from the owner's paydays, not from ``txn.pay_period``**
    (pay-calendar plan step C4-a-1).  The relationship reads
    ``budget.pay_periods.end_date``, a stored copy of ``lead(start_date) - 1``
    with nothing reconciling it to the paydays it derives from, so a schedule
    whose stored ends had drifted clamped a projected row against a span its
    own calendar disagrees with -- while :func:`_period_balances` two functions
    up already samples that period at its DERIVED end.  One module, two ends,
    and pay-calendar finding **P38** is the name for it.  The other two sites
    that finding listed had already moved (``grid/_mobile_plan.html`` and
    ``savings_dashboard_service/_net_worth``, both on ``DerivedPeriod`` values
    since ``C2-f2``), so this is the last of the three and the row closes here.

    Args:
        calendar: The OWNER's pay calendar.
        txn: The row to place.  Its ``pay_period_id`` is NOT NULL in the schema,
            so the lookup is by a real foreign key rather than an optional one.

    Returns:
        The :class:`~app.services.pay_calendar.DerivedPeriod` carrying
        ``txn.pay_period_id``.

    Raises:
        RuntimeError: The calendar does not hold that period.  Since
            ``pay_period_id`` is a NOT NULL foreign key to a row that exists,
            the only way to reach this is a calendar derived for a DIFFERENT
            owner than the one whose account is being folded -- so it is an
            ownership defect that would otherwise mis-date money silently, and
            it fails loud rather than skipping the row (which would delete it
            from the projection) or falling back to the relationship (which
            would reinstate the stored end this step removes).
    """
    period = calendar.period_by_id(txn.pay_period_id)
    if period is None:
        raise RuntimeError(
            f"_cash_fold: transaction id={txn.id} is filed in pay period "
            f"id={txn.pay_period_id}, which the calendar handed to this fold "
            f"does not hold.  A calendar is one OWNER's whole saved schedule, "
            f"so this means the account being folded and the calendar belong "
            f"to different owners; investigate the caller that paired them."
        )
    return period


def _cash_plan(
    account: Account, basis: AmountBasis, as_of: date, calendar: PayCalendar,
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

    **The SPAN it clamps against is DERIVED, since pay-calendar plan step
    C4-a-1** -- see :func:`_filed_span`, which is also why this function takes a
    calendar.  It read ``txn.pay_period`` until then, so this module clamped
    against a STORED end while :func:`_period_balances` sampled the same period
    at its derived one.

    **The load is separate from the reduction, and that is plan step X-c1's
    doing.**  The rows are kept, not just their per-day totals, because the
    period view reduces the SAME rows a second way -- by the budget column they
    were attributed to -- and re-loading them for that would be two answers to
    "what is in this account's plan" a day apart from each other.

    **It no longer takes the WALK, and plan step X-f3b is why.**  It did, for
    one reason: a projected envelope's reservation had to know which of its
    purchases a declared balance already contained, and that answer
    (:attr:`~app.services.cash_ledger.CashLedgerWalk.coverage`) was read off the
    facts :func:`assemble` had already loaded rather than by resolving the
    account's assertions a second time.  Ruling **R-FM** dissolved the question:
    a purchase carrying a recorded posting day is a cash movement of its OWN in
    the walk, so the reservation asks the purchase and the clearing rule stays
    where it is asked -- in the walk, about that movement.  One less argument,
    and no shape in which the plan could be valued against a different account's
    assertions than the walk replayed.

    Args:
        account: The account whose plan to load.
        basis: The read pass's amount basis, carrying the scenario the rows live
            in and the derivations they are priced through.
        as_of: The reader's NOW -- the floor ruling R-G clamps a landing day up
            to.
        calendar: The OWNER's pay calendar, which each row's span is read from
            (:func:`_filed_span`).

    Returns:
        The account's :class:`_CashPlan`; its ``rows`` and ``by_day`` are empty
        for an account with no plan, and it carries the pass's basis either way
        so the record is self-describing.

    Raises:
        RuntimeError: A row names a pay period *calendar* does not hold
            (:func:`_filed_span`).
    """
    rows = planned_cash_rows(account.id, basis.scenario_id)
    if not rows:
        return _CashPlan(rows=[], by_day={}, basis=basis)

    not_before = as_of + _ONE_DAY
    by_day: "dict[date, list[Transaction]]" = defaultdict(list)
    for txn in rows:
        period = _filed_span(calendar, txn)
        nominal = attribution_date(
            txn.due_date, period.start_date, period.end_date,
        )
        by_day[max(nominal, not_before)].append(txn)
    return _CashPlan(rows=rows, by_day=dict(by_day), basis=basis)


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
        income, expense = sum_projected(txns, plan.basis)
        nets[day] = income - expense
    return nets
