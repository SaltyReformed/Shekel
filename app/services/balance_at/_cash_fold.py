"""Balance-at-T seam -- the CASH fold: a walk of facts sampled into a balance.

Plan step **X-b** (``docs/audits/balance_architecture/README.md``).  "A cash
account is an event stream" (Section 3), and this module is the half that turns
that stream into money: the :mod:`app.services.cash_ledger` leaf owns the WALK
(the facts), this owns the FOLD (the balance).  The same split plan step D-fold
made on the loan side, for the same reason -- *a fold is a balance; a walk is a
fact* -- and the same placement: because the prefix-sum lives HERE, a consumer
legitimately holding a :class:`~app.services.cash_ledger.CashLedgerWalk` (the
posting writer at plan step X-d) cannot reach a balance from a public leaf name.

**Three tiers, ONE :func:`~app.services.balance_at._fold.sample_cumulative`, no
branch.**  Every date is answered off a single running total assembled from:

* the **SEED** -- the account's first assertion back-projected over the records
  it already contains (ruling R-I).  See :func:`_actual_steps`.
* the **ACTUAL** steps -- X-a's walk re-keyed by the day each event became
  visible (:func:`app.services.cash_ledger.dated_deltas`, the ONE statement of
  that clock, shared with the posting writer so the fold and the posted ledger
  cannot drift).
* the **PLANNED** steps -- the still-Projected rows, each landing at
  ``max(its attribution date, as_of + 1 day)`` (ruling R-G: "a plan cannot have
  already happened").  The cash twin of
  :func:`app.services.balance_at._plan.fold_forward`.  See
  :func:`_planned_steps`.

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

**ADDITIVE and unwired at X-b.**  Nothing in production calls this yet; its only
caller is its oracle (``tests/test_services/test_cash_fold.py``).  The cutover is
plan step X-c, and it is the step where money moves.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from app.models.account import Account
from app.models.transaction import Transaction
from app.services.cash_ledger import (
    CashLedgerWalk,
    dated_deltas,
    live_amount_overrides,
    planned_cash_rows,
    sum_projected,
    walk_cash_ledger,
)
from app.utils.dates import attribution_date, utc_civil_date

from ._fold import sample_cumulative

_ZERO_MONEY = Decimal("0.00")
# Ruling R-G's clamp floor: the earliest day a plan can still happen is the day
# AFTER the reader's as-of.  Named (and spelled the same way) as the loan twin's
# ``_plan._ONE_DAY``, because it is the same rule -- ruling D1 for loans, R-G for
# cash -- and the two are meant to read as one.
_ONE_DAY = timedelta(days=1)


def fold_cash_balances(
    account: Account,
    scenario_id: int,
    as_of: date,
    dates: list[date],
) -> dict[date, Decimal]:
    """Return the account's folded cash balance at each of *dates*.

    The cash counterpart of
    :func:`app.services.balance_at._fold.fold_loan_balances`, and the producer
    plan step X-c points all three cash seam entries at.  ONE walk of the
    account's facts plus ONE load of its plan, sampled at every requested date --
    so N dates cost one pass, not N.

    **Two dates, and they are not the same date** (the contract the seam's
    context documents): *as_of* is the reader's NOW -- what decides that a plan
    cannot already have happened -- while each of *dates* is a VALUATION date,
    which may be long before it (a historical read) or long after (a projection).
    Passing ``as_of`` as a valuation date is ordinary, not special.

    Args:
        account: The account to value.  Its ``id`` scopes the walk and the plan,
            and its ``user_id`` scopes the live salary override; its KIND is not
            consulted (ruling R-J).  Must be attached to ``db.session``.
        scenario_id: The budget scenario whose rows to fold.  Assertions are
            per-ACCOUNT and replay in every scenario; only the transaction rows
            are scenario-scoped.
        as_of: The reader's NOW -- the floor a still-Projected row's effective
            date is clamped up to (ruling R-G), and the date its entries-aware
            reservation is valued at.
        dates: The dates to value the account at, in any order.  Duplicates
            collapse.

    Returns:
        ``{date: balance}`` -- one cent-quantized ``Decimal`` per distinct
        requested date.  ``{}`` for an empty *dates*.
    """
    seed, steps = _actual_steps(walk_cash_ledger(account.id, scenario_id))
    steps.extend(_planned_steps(account, scenario_id, as_of))
    steps.sort(key=lambda step: step[0])
    return sample_cumulative(seed, steps, dates)


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

    That cancellation depends on ``dated_deltas`` emitting the opening at
    ``utc_civil_date(asserted_at)`` valued at ``anchor_balance - balance_before``,
    which this function re-derives (``dated_deltas`` returns bare tuples, so the
    opening's own step cannot be identified in its output).  It is a
    re-derivation, so it is PINNED rather than trusted:
    ``TestTheOpeningMovesIntoTheSeed.test_at_and_after_the_opening_it_equals_the_zero_seeded_walk``
    asserts the at-and-after region equals a zero-seeded sample of the same
    steps, and fails the moment the leaf's emission and this compensator stop
    agreeing.  Measured: seeding at zero while keeping the compensator -- the
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
    correction = opening.anchor.anchor_balance - opening.balance_before
    steps.append(
        (utc_civil_date(opening.anchor.asserted_at), -correction),
    )
    return correction, steps


def _planned_steps(
    account: Account, scenario_id: int, as_of: date,
) -> "list[tuple[date, Decimal]]":
    """Return the ``(day, net)`` steps the account's still-Projected rows contribute.

    The PLANNED tier, and the reason it lives in this READER rather than in the
    clock-free leaf: a plan's effective date is a function of *as_of* (ruling
    R-G), exactly as the loan plan's is (plan step C6a).

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

    **What a planned row is WORTH is not re-implemented here.**  Each day's group
    is reduced by the shared
    :func:`~app.services.cash_ledger.sum_projected` -- the same engine the
    shipping period walk and the grid's subtotal row both call, carrying the
    entries-aware reservation for an envelope expense and the live override for a
    salary paycheck or a derived loan debit.  Reducing per GROUP rather than per
    ROW is what keeps that one rule intact: ``sum_projected`` is additive over
    disjoint groups, so the days of a period sum to the period's net exactly.

    The reservation is valued at *as_of* (``sum_projected``'s entry-date window),
    so an entry dated AFTER the reader's now cannot reduce the reservation early.
    That is R-G's own principle applied one level down -- a purchase that has not
    happened cannot have cleared the bank -- and it is a deliberate choice between
    the two the shipping producers already make (the scalar windows, the grid and
    the daily ramp do not).  It moves no money on today's real data: measured
    2026-07-25, ZERO entries on projected rows are dated after today in either
    database.  Recorded as finding N-39 for plan step X-c to rule on before the
    cutover makes it visible.

    The live override map is built ONCE over the whole plan and threaded into
    every group (the established build-once-and-thread pattern): each seam picks
    its own candidates and both filter to ``is_projected``, so a map built over
    the plan alone is identical on every key that can matter.

    Args:
        account: The account whose plan to fold (its ``user_id`` scopes the live
            salary override).
        scenario_id: The budget scenario the rows live in.
        as_of: The reader's NOW.

    Returns:
        ``[(day, net), ...]`` in arbitrary order -- one step per day carrying at
        least one planned row, valued as signed income-minus-expense.  ``[]`` for
        an account with no plan.
    """
    rows = planned_cash_rows(account.id, scenario_id)
    if not rows:
        return []

    amount_overrides = live_amount_overrides(account, scenario_id, rows)
    not_before = as_of + _ONE_DAY
    by_day: "dict[date, list[Transaction]]" = defaultdict(list)
    for txn in rows:
        period = txn.pay_period
        nominal = attribution_date(
            txn.due_date, period.start_date, period.end_date,
        )
        by_day[max(nominal, not_before)].append(txn)

    steps: "list[tuple[date, Decimal]]" = []
    for day, txns in by_day.items():
        income, expense = sum_projected(txns, amount_overrides, as_of=as_of)
        steps.append((day, income - expense))
    return steps
