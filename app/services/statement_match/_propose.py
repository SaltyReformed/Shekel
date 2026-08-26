"""What the app OFFERS -- proposals only, never a write.

Ruling **R-FP**: *a match is a PROPOSAL, never a silent apply*.  This module is
the proposing half and it is deliberately pure: no database, no clock, no
request.  It takes the recorded bank lines and the account's candidate rows and
returns what it believes goes with what, for a human to accept or reject.

**The predicate is EXACT AMOUNT inside a day window, and the measurement is why
it is not more than that.**  Taken on the developer's own 2026-08-16 SECU
export against a production clone: of 231 lines inside the pay calendar's span,
an exact-amount predicate pairs 58 uniquely at plus-or-minus 14 days and leaves
only 4 ambiguous -- and 35 of those 58 carry a day the app got wrong, by as
much as 8 days.  Widening to plus-or-minus 30 days made it WORSE: 50 unique and
21 ambiguous, because a monthly commitment starts matching its neighbour.  A
description compare buys nothing on this data -- the bank calls a grocery run
``POINT OF SALE DEBIT L340 DATE 03-26 HARRIS TEETER`` where the app calls it
``Groceries`` -- so the window is the whole rule and the review screen is what
makes it safe.

**An exact figure is not the whole rule any more, and being an exact GATE was a
`$356.61` defect** (plan step ``bank_import:X-f6d-1``, ruling **R-GD(b)**,
finding **N-335**).  A line three cents from the row it belonged to was offered
nothing, so the screen's cheapest remaining act recorded it a SECOND time.  The
exact predicate is now the FIRST of two tiers rather than the only one:
:func:`_one_to_one` and :func:`_groups` are unchanged, and :func:`_near_misses`
then scores what they leave.  Two tiers rather than one composite cost, because
an exact figure is a categorically stronger claim than a near one -- so no near
pairing can ever displace an exact one, and the optimality argument
:func:`_least_cost_pairing` rests on is untouched.

**What admits a near miss is measured, and one inherited claim was measured
FALSE.**  Re-derived 2026-08-22 on a production clone carrying the developer's
own 376 recorded lines: of the 114 lines the exact tier leaves, nine have a
legal same-sign in-window row within 1% of the line, and they are NOT separable
by distance.  A `Lowe's` swipe sits **0.106%** from a ``CC Payback: Mint
Mobile`` row and an `Amazon` swipe **0.339%** from a ``Kayla`` envelope, while
two genuine `Geico` pairs sit **0.180%** out -- so any bound admitting the true
ones admits those.  What separates them is whether the app's own row NAMES the
bank's merchant: 5 of 5 same-movement pairs do, 4 of 4 undecidable ones do not,
and requiring it pushes the nearest wrong candidate out to **4.76%**.  See
:func:`_near_score`.

**Nothing here scores a GROUP, and that is measured rather than inherited from
the door.**  Over the same leftovers, ``n choose k`` puts 20 lines within 1% of
some same-day row set -- a Van Loan transfer landing inside a Capital One
payment at **0.0064%**, TIGHTER than the six genuinely-true payroll groups at
0.0019%.  A subset sum can hit any target, so a near miss over one is a
coincidence rather than evidence.  The residual on a group the OWNER asserts is
plan step ``X-f6d-4``'s, through the hand-build form ruling **R-FP** reserves
to them.

**Repeated amounts are assigned GLOBALLY, not first-come.**  Five bank lines and
five app rows all reading `$1,910.95` is not a hypothetical: it is this
developer's mortgage transfer, monthly, and a greedy left-to-right pass pairs
them by file order rather than by proximity.  :func:`_assign` minimises the
total day distance over each amount group instead, so a pairing is the best one
available rather than the first one found.

**A GROUP is proposed only where it is unambiguous** -- N app rows the app
believes could all have moved on ONE day, summing EXACTLY to one line.  The
bank shows one payroll deposit where the app holds three rows, and that shape
is worth offering; a general subset sum over an account's whole history is not,
because the number of subsets that hit a given cent is large and every one of
them would be a proposal a human has to refute.

**Every row carries the WINDOW the app believes its money moved in**
(:attr:`~._offers.CandidateRow.expected_window`), and that one accessor is what
bounds both passes: a settled row is a point at its settle day -- unless it is
a PURCHASE the reconcile panel ticked, which spans back to the day it was made
because that settle day is a bound rather than an observation -- an unsettled
purchase a point at the day it was made, and a bill the whole of its pay
period.  Nothing here is unbounded, which it was until ``bank_import:X-f6a-3c`` --
finding **N-312** -- and the two constants that stood in for a bound on the
group path (a global "undated pool" and its unrendered size) are deleted rather
than reported, because a row that carries its own window needs neither.

Services-boundary discipline: plain data in, frozen dataclasses out.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations

from ._near import near_misses
from ._offers import BankLine, CandidateRow, MatchProposal, RowKind
from ._pairing import (
    DAY_WINDOW,
    day_distance,
    days_outside,
    exactly_matched_but_outside_the_window,
    within_window,
)


#: The largest number of candidate rows a single recorded DAY may hold and
#: still be searched for groups.  Beyond it ``n choose k`` produces more
#: coincidences than explanations, and the day is reported on
#: :attr:`ProposedMatches.crowded_days` rather than silently passed over.
#:
#: **Re-measured at plan step X-f6a-3c-1, against the bucketing that replaced
#: the undated POOL.**  A day's bucket is now the rows settled on it PLUS the
#: unsettled rows whose window reaches it (:func:`_day_buckets`), which is a
#: bigger set than "rows settled on this day" and a far smaller one than "every
#: undated row on the account": measured on the developer's own clone the
#: busiest bucket holds **31** where the busiest settled DAY holds 9 -- a
#: figure two earlier docstrings gave as 5, counting a different population.
#: 12 was sized against that older count and is not the number this bucketing
#: needs.
#:
#: **32 is a COST bound and the cost was measured, not assumed.**  A completely
#: full day costs ``C(32,2)+C(32,3)+C(32,4) = 41,416`` subset sums; the whole
#: of the developer's account renders its proposals in about 70 ms against the
#: 3.6-4.6 s ``candidates_for`` above it takes for the same rows.  It is set at
#: the measurement rather than far above it because a day OVER it is REPORTED
#: (:attr:`ProposedMatches.crowded_days`) rather than silently passed over, so the cap
#: binding is visible work rather than a hidden loss -- and because every extra
#: row multiplies the subsets that can hit a given cent by coincidence, which
#: is a proposal the owner has to refute.
MAX_GROUP_DAY_ROWS: int = 32

#: The largest number of app rows a GROUP proposal will combine.  The bank
#: splits nothing further than this in the developer's own data -- a payroll
#: deposit is at most three rows (salary, phone allowance, health allowance) --
#: and each step up multiplies the subsets considered, so a wider bound would
#: buy proposals nobody asked for at a cost that grows like ``n choose k``.
MAX_GROUP_ROWS: int = 4


@dataclass(frozen=True)
class ProposedMatches:
    """What one proposing pass offered, and what it declined to search.

    **The two travel together because a bound that does not say what it dropped
    reads as a clean sweep**, and until plan step ``bank_import:X-f6a-3c-2``
    they did not: :func:`propose` returned the offers alone and the review
    screen recomputed the skipped days by calling
    ``skipped_group_days`` over EVERY candidate, while the search itself only
    ever saw the rows no one-to-one proposal had claimed.  Two populations, one
    cap, and the reported set was a strict SUPERSET -- so the screen could name
    a day too crowded to search that had in fact been searched, on the one
    screen whose docstrings say four times that a bound must never be silent.
    That was finding **N-322**, and the remedy is this type: the search reports
    its own bound rather than a reader re-deriving it from a different set.

    Attributes:
        proposals: What the app believes goes with what, best first.
        crowded_days: The days :func:`_groups` did not search, ascending,
            because their bucket held more than :data:`MAX_GROUP_DAY_ROWS`
            candidate rows.  Empty on the developer's own data, where the
            busiest bucket carries 31 against a cap of 32.
        declined_lines: WHICH lines this pass CONSIDERED a candidate for and
            then declined to conclude about, and the sentence each tier gives
            for its own refusal.  **The difference between *there is nothing*
            and *we threw the only candidate away*, which until plan step
            ``bank_import:X-ge-1`` this type could not express** -- and the
            distinction is the whole safety of ruling **R-GH**'s automatic
            door: under a human tick the person reading the screen was the
            check, and that door has no person.

            **Every tier contributes its OWN refusals, in its own words**
            (:func:`~._near.near_misses`, :func:`~._pairing
            .exactly_matched_but_outside_the_window`), which is the only shape
            that keeps this total: a bound a tier applies and does not put here
            is one nothing can see, and a reader re-deriving it from a
            different population is finding **N-322** itself.  A tier added
            later must report or it is not a bound.

            It carries what ``undecided_near_lines`` carried until that step --
            a NEAR candidate the tier admitted and would not choose between --
            plus the two rejections that were swallowed: a near candidate
            refused for want of the merchant in its label or for the day
            window, and an EXACT candidate the window refused.  **Measured on
            the developer's own 378 lines**: the contest is empty and the newly
            reported rejections touch 12 of the 80 lines a standing rule would
            file, `$391.77` -- one of them his own `Apple Music` row, one day
            past the window from the `Apple` line that would have been filed a
            second time.

            **It is the LINE IDS rather than a count since plan step
            ``bank_import:X-f6d-3``.**  A count can only be reported in a panel
            at the foot of the screen, which names no line -- and the act it
            should prompt is *build this one by hand rather than record it a
            second time*, which is offered against one specific line, in two
            different cards.  A bound the owner cannot act on is barely a
            bound; the count is still ``len`` of this and nothing needs it.
    """

    proposals: "tuple[MatchProposal, ...]"
    crowded_days: "tuple[date, ...]"
    declined_lines: "dict[int, str]" = field(default_factory=dict)



def _assign(
    lines: "list[BankLine]", rows: "list[CandidateRow]",
) -> "list[tuple[BankLine, CandidateRow]]":
    """Pair equal-amount lines and rows so the total day distance is least.

    **The whole reason this is not a greedy loop.**  Within one amount group
    every line matches every row on the figure, so the only thing separating
    one pairing from another is proximity -- and a left-to-right pass takes
    whatever comes first, which on five monthly `$1,910.95` transfers is file
    order.

    **It is optimal rather than heuristic, and the argument is short enough to
    state.**  Both sides are points on the day line and the cost is
    ``|x - y|``, so no optimal pairing ever CROSSES: for ``l1 < l2`` and
    ``r1 < r2``, replacing ``(l1,r2),(l2,r1)`` with ``(l1,r1),(l2,r2)`` cannot
    increase the total, and it cannot break the window either -- a legal
    crossing bounds both uncrossed distances strictly inside it.  So a best
    pairing is order-preserving, and searching the order-preserving ones is the
    O(n*m) table in :func:`_least_cost_pairing` rather than a factorial.

    **Rows carrying NO settle day are matched in a SECOND pass of the same
    table, after it.**  A dated row that genuinely sits near a line always
    wins, and only the lines nothing dated could explain reach an unsettled
    one; that is the arm which settles a row the app never marked as having
    happened.  **It is no longer unbounded, and it is no longer greedy**: since
    plan step X-f6a-3c every such row carries the WINDOW the app believes the
    money moved in (:attr:`~._offers.CandidateRow.expected_window`), so it has
    a position, a distance and a legality test exactly as a dated row does --
    which is what lets one table serve both and stops this arm reaching a
    projection eighteen months out (finding **N-312**).

    **BOTH arms re-ask :func:`~._pairing.within_window` per PAIR, and its absence was a
    shipped defect** (plan step X-f6a-3a).  The caller's filter is an ``any``
    over the amount group -- a row legal against ONE line survives it -- and
    each arm then assigned a surviving row to whichever line it liked.
    Measured on the developer's own clone: 3 proposals were offered whose
    Accept could never succeed, the worst pairing a line posted 2026-06-01 with
    a purchase made 2026-07-27, 56 days later.
    **A first fix guarded only this arm** on the reasoning that
    :func:`_least_cost_pairing` is "bounded by :data:`~._pairing.DAY_WINDOW`, which
    subsumes the floor" -- false, because the window bounds a row by its own
    settle day and the floor is about the purchase day and the line's stated
    one.  Adversarial test-quality review 2026-08-18 built the dated case that
    proved it.

    Args:
        lines: Bank lines sharing one signed amount.
        rows: Candidate rows sharing that same amount.

    Returns:
        One ``(line, row)`` pair per matched line, in *lines*' own order.  A
        line with no legal partner is simply absent, which is the honest shape:
        a statement may show a movement the app never recorded at all.
    """
    # **Ordered by the WINDOW, exactly as the undated arm below is, because
    # that is the position :func:`_least_cost_pairing` pairs against and its
    # no-crossing argument assumes.**  This sorted on ``settled_on`` while a
    # settled row's window WAS that day, which made the two keys the same key.
    # Plan step ``bank_import`` 2026-08-22 broke that equivalence: a purchase
    # ticked on the reconcile panel now opens its window at ``purchased_on``
    # while ``settled_on`` stays the assertion day -- and every reconciled row
    # on the developer's account shares ``2026-08-18``, so the key collapsed to
    # ``row_id``, the file order this whole function exists to eliminate.
    # Measured on five identical `$1,910.95` transfers: 3 of 5 lines paired, 1
    # of those to the right month, 2 lines left unexplained -- and an
    # unexplained line is what the merchant rule offers to RECORD, which is
    # the duplicate this arc just finished removing 50 of.  Re-keyed: 5 of 5,
    # all correct.  Found by three independent adversarial reviews.
    dated = sorted(
        (row for row in rows if row.settled_on is not None),
        key=lambda row: (row.expected_window or (), row.row_id),
    )
    # **Rows with no settle day, ordered by their WINDOW**, because that is the
    # position :func:`_least_cost_pairing` pairs against.  A row carrying no
    # window at all sorts LAST and is refused by :func:`~._pairing.within_window` like
    # any other illegal pair -- it is not filtered out here, because filtering
    # would be a second statement of "an undatable row is not offerable" beside
    # the one that rule already has, and the two could then disagree.  Measured
    # by mutation 2026-08-19: with the filter in place, inverting that rule
    # changed no test.
    undated = sorted(
        (row for row in rows if row.settled_on is None),
        key=lambda row: (
            row.expected_window is None, row.expected_window or (), row.row_id,
        ),
    )
    ordered = sorted(lines, key=lambda line: (line.posted_on, line.line_id))

    paired = _least_cost_pairing(ordered, dated)
    taken = {line.line_id for line, _ in paired}
    # **The SAME table, over the rows the dated pass did not explain.**  It was
    # a greedy first-legal-by-``row_id`` loop until X-f6a-3c, and then briefly a
    # greedy NEAREST-legal loop, and both are wrong in the same way: they
    # minimise distance one line at a time and so can strand a later line whose
    # only legal partner the earlier one took.  Measured by adversarial design
    # review 2026-08-19 on two same-amount purchases and two lines -- the
    # greedy pass offered ONE proposal where a table offers two -- which
    # violates the rule :func:`_least_cost_pairing` states in bold for the
    # dated arm: *pairs outrank cost*, because a proposal the owner never sees
    # cannot be reviewed.  One rule for both arms rather than two.
    paired += _least_cost_pairing(
        [line for line in ordered if line.line_id not in taken], undated,
    )
    position = {line.line_id: index for index, line in enumerate(lines)}
    paired.sort(key=lambda pair: position[pair[0].line_id])
    return paired


def _least_cost_pairing(
    lines: "list[BankLine]", rows: "list[CandidateRow]",
) -> "list[tuple[BankLine, CandidateRow]]":
    """Return the cheapest order-preserving pairing of *lines* to *rows*.

    ``table[i][j]`` holds ``(-pairs, cost)`` over the first *i* lines and the
    first *j* rows, so a plain :func:`min` reads as *as many pairs as possible,
    then as cheap as possible*.  **Pairs outrank cost deliberately**: a
    proposal the owner never sees cannot be reviewed, so leaving a line
    unexplained to save two days of distance would hide work rather than order
    it.

    **The cost is :func:`~._pairing.days_outside`, which is ONE rule over both arms.**
    Most settled rows' windows are the single day they settled on, so the
    distance from a line is exactly what this table always measured; an
    unsettled row's is its pay period or its purchase day, and the distance is
    how far the bank posted OUTSIDE that -- zero when the app's own belief
    already covers the bank's day.  Writing the two as one function is what let
    the undated arm stop being a greedy loop.

    **A reconciled PURCHASE's window is a SPAN, and the cost signal inside it
    is flat**: :func:`~._pairing.days_outside` scores every day in the span zero, so
    among rows whose spans all reach a line the table is choosing on the
    tie-break rather than on distance.  That is why the caller orders its rows
    by WINDOW -- the ordering is what carries the information the cost no
    longer does.

    Args:
        lines: Bank lines sharing one amount, ASCENDING by day.
        rows: Candidate rows sharing it, ASCENDING by the window they occupy,
            every one of them carrying one.

    Returns:
        The chosen pairs, ascending by day.  Empty when either side is.
        Optimal among ORDER-PRESERVING pairings, which is exactly optimal while
        the windows do not nest -- a purchase's single day can sit inside a
        bill's fortnight, and there the table is a bound rather than the
        minimum.  Stated rather than claimed away: what it still guarantees is
        the property that matters, that no line is left unexplained while a
        legal partner goes unused.
    """
    n, m = len(lines), len(rows)
    if not n or not m:
        return []
    table = [[(0, 0)] * (m + 1) for _ in range(n + 1)]
    move = [[""] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            options = [
                (table[i - 1][j], "skip-line"),
                (table[i][j - 1], "skip-row"),
            ]
            # **The legality test is per PAIR here**, and its absence was a
            # shipped defect: the caller's filter is an ``any`` over the amount
            # group, so a row legal against one line could be assigned to
            # another.  Found by adversarial test-quality review 2026-08-18.
            #
            # ``distance <= DAY_WINDOW`` stood BESIDE this call until plan step
            # X-f6a-3c and is gone rather than dropped by accident:
            # :func:`~._pairing.within_window` IS the distance test plus the purchase
            # floor, now that every row carries the window the distance is
            # measured from.  Asking both was two spellings of one bound, which
            # is the shape that let the two disagree in the first place -- the
            # docstring above used to claim ``DAY_WINDOW`` "subsumes the
            # floor", which was false.
            if within_window(rows[j - 1], lines[i - 1]):
                distance = days_outside(
                    rows[j - 1].expected_window, lines[i - 1].posted_on,
                )
                pairs, cost = table[i - 1][j - 1]
                options.append(((pairs - 1, cost + distance), "pair"))
            table[i][j], move[i][j] = min(options)

    chosen = []
    i, j = n, m
    while i and j:
        step = move[i][j]
        if step == "pair":
            chosen.append((lines[i - 1], rows[j - 1]))
            i, j = i - 1, j - 1
        elif step == "skip-line":
            i -= 1
        else:
            j -= 1
    chosen.reverse()
    return chosen


def _one_to_one(
    lines: "list[BankLine]", rows: "list[CandidateRow]",
) -> "list[MatchProposal]":
    """Return the one-line-one-row proposals, grouped by amount and assigned.

    Args:
        lines: Every unmatched bank line.
        rows: Every candidate row.

    Returns:
        One :class:`~._offers.MatchProposal` per pairing the assignment chose.
    """
    rows_by_amount: "dict[Decimal, list[CandidateRow]]" = defaultdict(list)
    for row in rows:
        rows_by_amount[row.cash_amount].append(row)
    lines_by_amount: "dict[Decimal, list[BankLine]]" = defaultdict(list)
    for line in lines:
        lines_by_amount[line.amount].append(line)

    proposals = []
    for amount, group_lines in lines_by_amount.items():
        # Rows legal against AT LEAST ONE line here, so the assignment has a
        # pool to optimise over.  **The per-PAIR test is inside**
        # :func:`_assign`, and it has to be: this filter is an ``any``, so a
        # row legal against one line survives it and could then be handed a
        # line it is illegal against.  That is not hypothetical -- it was
        # measured at 3 unacceptable proposals on the developer's own clone
        # before X-f6a-3a, all of them through the undated arm.
        group_rows = [
            row for row in rows_by_amount.get(amount, ())
            if any(within_window(row, line) for line in group_lines)
        ]
        for line, row in _assign(group_lines, group_rows):
            proposals.append(MatchProposal(
                lines=(line,), rows=(row,),
                # ``_day_distance`` answers None for a row carrying NO day, and
                # ``or 0`` would read that as "the day already agrees" -- so the
                # screen printed *confirms the day you already had* beside
                # *marks 1 row(s) as happened*, two lines contradicting each
                # other on the arm built for rows nobody has settled.  The
                # distance is genuinely UNKNOWN, and the value type says so.
                day_gap=day_distance(row, line.posted_on),
            ))
    return proposals


def _day_buckets(
    rows: "list[CandidateRow]",
) -> "tuple[dict[date, list[CandidateRow]], list[date]]":
    """Return the days a GROUP may be searched on, and the days too crowded to.

    **One bucketing, ONE consumer**, and getting there took two steps.  It was
    two implementations of what a day holds -- one searching, one reporting --
    each re-deriving the undated pool's membership test, and the reporting one
    then blamed "crowded days" for a bound that was really the pool's; that
    was collapsed to one bucketing with two callers at plan step X-f6a-3c-1,
    and the two callers still bucketed different POPULATIONS (finding
    **N-322**).  Since X-f6a-3c-2 :func:`_day_sums` is the only caller and it
    returns what it skipped, so the reported bound IS the applied one.

    **A day's bucket is the rows the app believes could have moved on it**:
    the rows it recorded as settling that day, plus every unsettled row whose
    own window (:attr:`~._offers.CandidateRow.expected_window`) reaches it --
    a purchase around the day it was made, a bill around its pay period.

    **The window is widened by :data:`~._pairing.DAY_WINDOW` here, exactly as
    :func:`~._pairing.within_window` widens it, and the two MUST agree.**  A first
    version of this step left the bucket strict on the reasoning that the slack
    is the line-to-day tolerance and applying it twice is too generous.  That
    opened a silent gap between what may be PAIRED and what may be GROUPED: a
    bill budgeted 2026-08-13..08-26 and paid on 08-30 alongside a settled
    partner is legal for the line and was unbuildable into a group, so a
    proposal the previous implementation offered simply disappeared with
    nothing to report it.  A bound that only one of two passes applies is not a
    bound, it is a disagreement.  Found by adversarial design review
    2026-08-19.

    **What widening costs was measured rather than assumed**, because the
    strict form was chosen to protect a real proposal: at the old cap of 20 it
    pushed the open pay period's days over :data:`MAX_GROUP_DAY_ROWS` and lost
    the developer's own `$2,611.90` payroll group.  The cap is what was wrong,
    not the widening -- re-sized, the widened bucketing offers the SAME 124
    proposals including all 3 groups, with 0 days reported crowded and
    ``propose`` running in about 70 ms.

    **The undated POOL this replaced was a global bound and could not work.**
    A row with no settle day used to join EVERY day's bucket, which is only
    affordable while there are few such rows -- and an account's unsettled
    backlog is not few: the developer's carries 674, of which 600 are
    projections dated past the statement's last day.  So the arm was switched
    off wholesale by a ``MAX_GROUP_UNDATED`` of 6, a bound that was computed,
    published as ``undated_pool_too_large()``, returned **674**, and was
    rendered NOWHERE (findings **N-315** and **N-316**).  Both are deleted
    rather than reported, because a row that carries its own window does not
    need a pool: the 600 forward projections join no bucket at all, and the 74
    rows the statement could actually have shown join the days they belong to.
    Measured: the proposals this offers on the developer's own statement are
    the SAME 124, including all 3 groups.

    Args:
        rows: The candidate rows.

    Returns:
        ``(searchable, skipped)`` -- the buckets holding at most
        :data:`MAX_GROUP_DAY_ROWS` rows, and the days holding more, ascending.
        The cap and the membership rule are each stated ONCE here, which is
        what the two implementations before it could not manage.

        **There is now exactly ONE caller and it reports what it skipped**
        (plan step ``bank_import:X-f6a-3c-2``, finding **N-322**).  A second
        caller bucketed a different POPULATION until then -- ``_reads`` asked
        over every candidate while :func:`_groups` searches only the rows no
        one-to-one proposal claimed -- so the reported set was a SUPERSET and a
        day could be named too crowded to search that had been searched.  The
        skipped days ride out on :class:`ProposedMatches` instead, which is the
        search's own answer rather than a reader's re-derivation.
    """
    buckets: "dict[date, list[CandidateRow]]" = {}
    for row in rows:
        if row.settled_on is None:
            continue
        window = row.expected_window
        # **A day is a bucket because the app believes money MOVED on it**, and
        # for a purchase ticked on the reconcile panel the recorded settle day
        # is the one day it is sure the money did not move on -- it is the day
        # a BALANCE was asserted.  Such a row anchors its own bucket at the day
        # it was made instead, which is where a group of purchases made
        # together actually forms.  Without this the only bucket an account's
        # reconciled rows could offer was the assertion day they all share, so
        # every group among them sat months from any line and the search
        # returned nothing -- silently, because a bucket nobody can reach is
        # not a crowded day and is reported by nothing.
        if window is not None and window[0] != window[1]:
            buckets.setdefault(window[0], []).append(row)
        buckets.setdefault(row.settled_on, []).append(row)
    for row in rows:
        window = row.expected_window
        # **A row whose window is a POINT at its own settle day is already
        # wholly represented by the loop above.**  Every settled row was one
        # until a reconciled PURCHASE started spanning back to ``purchased_on``
        # (:attr:`~._offers.CandidateRow.expected_window`), and this arm went on
        # skipping all of them -- so the span was honoured by ``_within_window``
        # and invisible here, which is precisely the disagreement this
        # function's own docstring says is not a bound.  The skip is now about
        # the WINDOW's shape rather than about the column being populated.
        if (
            row.settled_on is not None
            and window is not None
            and window[0] == window[1]
        ):
            continue
        if window is None:
            # A row the app can date no way at all joins no day.  It is
            # unconstructible through either candidate arm -- both fill
            # ``expected_on`` from a NOT NULL column -- and the branch is here
            # because the accessor is TOTAL: the alternative reading, "joins
            # every day", is the pool this function exists to remove.
            continue
        first, last = window
        slack = timedelta(days=DAY_WINDOW)
        for day, day_rows in buckets.items():
            # The days the loop above already filed it under.
            if day == row.settled_on or (
                row.settled_on is not None and day == first
            ):
                continue
            if first - slack <= day <= last + slack:
                day_rows.append(row)

    searchable = {
        day: day_rows for day, day_rows in buckets.items()
        if len(day_rows) <= MAX_GROUP_DAY_ROWS
    }
    skipped = sorted(day for day in buckets if day not in searchable)
    return searchable, skipped


def _day_sums(
    rows: "list[CandidateRow]",
) -> "tuple[dict[date, dict[Decimal, list[tuple[CandidateRow, ...]]]], list[date]]":
    """Return, per recorded day, every small row set and what it sums to.

    Computed ONCE over the account rather than re-searched per bank line, which
    is what keeps grouping linear in the statement's length: the sums for a day
    do not depend on which line is asking.  A day holding more rows than
    :data:`MAX_GROUP_DAY_ROWS` is skipped whole and RETURNED beside the sums,
    because ``n choose k`` over a large day produces more coincidences than
    explanations -- and a bound that is silent about what it dropped reads as
    "nothing to group here".

    **The skipped days come back from HERE rather than from a second bucketing
    a reader performs** (plan step ``bank_import:X-f6a-3c-2``, finding
    **N-322**): they are a fact about the search that ran, and this is the
    function that runs it.

    **Which rows a day holds is :func:`_day_buckets`'**, including the
    unsettled ones: ruling **R-FV**'s third arm has to reach the group path,
    because a split payroll deposit with one unsettled member is the ordinary
    shape.

    Args:
        rows: The candidate rows.

    Returns:
        ``({day: {signed sum: [row sets]}}, skipped days)`` -- the sums over
        sets of 2 to :data:`MAX_GROUP_ROWS` rows, and the days this pass did
        not search, ascending.
    """
    searchable, skipped = _day_buckets(rows)
    sums: "dict[date, dict[Decimal, list[tuple[CandidateRow, ...]]]]" = {}
    for day, day_rows in searchable.items():
        day_sums: "dict[Decimal, list[tuple[CandidateRow, ...]]]" = defaultdict(list)
        for size in range(2, min(len(day_rows), MAX_GROUP_ROWS) + 1):
            for combo in combinations(day_rows, size):
                total = sum(
                    (row.cash_amount for row in combo), Decimal("0.00"),
                )
                day_sums[total].append(combo)
        if day_sums:
            sums[day] = day_sums
    return sums, skipped


def _holds_a_parent_and_its_child(
    rows: "tuple[CandidateRow, ...]",
) -> bool:
    """Return whether *rows* names an envelope AND a purchase inside it.

    The proposer's half of :func:`~._accept._reject_parent_and_its_own_purchase`.
    That refusal exists because an envelope's cash leg already covers its own
    outstanding purchases, so a group naming both counts one purchase twice --
    and a proposer that could not see the relation offered exactly that.  Live
    on the developer's own clone: 28 envelopes carrying 73 debit purchases, so
    both sides sit in the candidate pool.

    Args:
        rows: A candidate group.

    Returns:
        Whether any purchase in it belongs to a transaction also in it.
    """
    transactions = {
        row.row_id for row in rows if row.kind is RowKind.TRANSACTION
    }
    return any(
        row.parent_id in transactions
        for row in rows if row.kind is RowKind.PURCHASE
    )


def _groups(
    lines: "list[BankLine]", rows: "list[CandidateRow]",
) -> "tuple[list[MatchProposal], list[date]]":
    """Return the proposals where several app rows sum to one bank line.

    R-FS's second shape, in the direction the app can propose without guessing.
    The rows considered for one line are those of a single day's bucket
    (:func:`_day_buckets`) inside the window, which is what a split deposit
    looks like: three rows that all settled the day the paycheck landed, or two
    that did and one the app has not marked as having happened at all.

    **The other direction -- N bank lines summing to one app row -- is
    deliberately NOT proposed automatically**, and that is measured rather than
    cautious.  On the developer's own accounts no settled envelope's period
    swipes sum to its close within `$25`, because the app holds only some of
    what the bank shows; that gap is what plan step ``bank_import:X-f6a-3b``
    closes by letting a bank line BECOME a purchase.  The owner builds such a
    group on the review screen's own hand-build form, which posts to
    :func:`~._accept.accept_match` like any other match -- the refusal here is
    to GUESS one, not to record one.

    Args:
        lines: The bank lines still unexplained by a one-to-one proposal.
        rows: The candidate rows still unused by one.

    Returns:
        ``(proposals, crowded days)`` -- one proposal per line that exactly one
        same-day row set sums to, and the days this search declined to look at.
        A line that several distinct sets could explain gets none: an ambiguous
        proposal is a question dressed as an answer.  **The crowded days are
        THIS search's**, over the population it was actually handed, which is
        finding **N-322**'s fix.
    """
    sums, crowded = _day_sums(rows)
    proposals = []
    for line in lines:
        # **Keyed by the ROW SET, not by (day, set), and that is a bug fix.**
        # A combo holding a settled row can only appear in that row's own
        # bucket; a combo of rows NOBODY has settled appears in every bucket
        # its members' windows jointly cover, so counting `(day, combo)` pairs
        # made one unambiguous set look like several and the test below
        # refused it.  Measured: two projected bills summing to a line were
        # proposed with one settled row on the account and REFUSED with two,
        # the second settled row being unrelated and two days away.  Found by
        # adversarial financial review 2026-08-19, on the arm this step's own
        # bucketing had just switched back on.
        found: "dict[frozenset, tuple[date, tuple[CandidateRow, ...]]]" = {}
        for day, day_sums in sums.items():
            if abs((day - line.posted_on).days) > DAY_WINDOW:
                continue
            for combo in day_sums.get(line.amount, ()):
                # Every member must be legally datable to this line's day, and
                # no two of them may be an envelope and a purchase inside it:
                # the accept door refuses both, so offering either is an Accept
                # button that cannot succeed.
                if not all(within_window(row, line) for row in combo):
                    continue
                if _holds_a_parent_and_its_child(combo):
                    continue
                found.setdefault(
                    frozenset((row.kind, row.row_id) for row in combo),
                    (day, combo),
                )
        if len(found) != 1:
            continue
        day, combo = next(iter(found.values()))
        proposals.append(MatchProposal(
            lines=(line,), rows=combo,
            # **``None`` when NO member carries a day**, exactly as
            # :func:`_one_to_one` answers it.  ``day`` is the bucket's key, and
            # a bucket key belongs to the rows that SETTLED on it -- so reading
            # it as the group's own day captioned a group of rows nobody has
            # settled as *confirms the day you already had*, beside *marks 2
            # row(s) as happened*: the two contradicting captions
            # :attr:`~._offers.MatchProposal.day_gap` was made three-valued to
            # stop.  Where any member IS settled the key is that member's own
            # ``settled_on``, because a settled row joins only its own bucket.
            day_gap=(
                abs((day - line.posted_on).days)
                if any(row.settled_on is not None for row in combo)
                else None
            ),
        ))
    return proposals, crowded


def propose(
    lines: "list[BankLine]", rows: "list[CandidateRow]",
) -> ProposedMatches:
    """Return what the app believes goes with what, best first.

    THREE passes, and the order between them is the rule: each one sees only
    what the ones before it could not explain, because the stronger
    explanation of the same money is the one a reviewer should be shown.

    * :func:`_one_to_one` -- one line, one row, the figures EQUAL;
    * :func:`_groups` -- several rows summing EXACTLY to one line;
    * :func:`~._near.near_misses` -- one line, one row, the figures NEARLY
      equal and the row naming the bank's merchant (plan step
      ``bank_import:X-f6d-1``, ruling **R-GD(b)**).

    **The near tier is LAST and that is the whole reason it is a tier.**  An
    exact figure is a categorically stronger claim than a near one, so a near
    pairing may never take a line or a row an exact one wanted -- which is
    also what leaves :func:`_least_cost_pairing`'s optimality argument
    untouched, and what makes this step's diff readable against the 132
    proposals the exact tiers already offered.

    Nothing here writes, and nothing here decides -- every proposal is a
    question put to the owner (ruling **R-FP**).  A SCORE is not a licence.

    Args:
        lines: The account's unmatched bank lines.
        rows: The account's candidate rows
            (:func:`~._candidates.candidates_for`).

    Returns:
        A :class:`ProposedMatches`.  Its proposals are ordered by how much
        accepting would change -- the ones that merely CONFIRM a day the app
        already had first, then the day corrections, then the rows nobody has
        settled, and LAST the ones that also move an AMOUNT, each block in
        statement order.  Its crowded days are the ones the GROUP pass
        declined to search, published here rather than re-derived by a reader
        over a different population (finding **N-322**), and its undecided
        count is the near tier's own bound.
    """
    one_to_one = _one_to_one(lines, rows)
    spoken_for_lines = {
        line.line_id for proposal in one_to_one for line in proposal.lines
    }
    spoken_for_rows = {
        (row.kind, row.row_id)
        for proposal in one_to_one for row in proposal.rows
    }
    grouped, crowded = _groups(
        [line for line in lines if line.line_id not in spoken_for_lines],
        [row for row in rows if (row.kind, row.row_id) not in spoken_for_rows],
    )
    exact = one_to_one + grouped
    spoken_for_lines = {
        line.line_id for proposal in exact for line in proposal.lines
    }
    spoken_for_rows = {
        (row.kind, row.row_id) for proposal in exact for row in proposal.rows
    }
    residue_lines = [
        line for line in lines if line.line_id not in spoken_for_lines
    ]
    residue_rows = [
        row for row in rows if (row.kind, row.row_id) not in spoken_for_rows
    ]
    near, declined = near_misses(residue_lines, residue_rows)
    # **Each tier reports the bound IT applies, and this is where the reports
    # are joined** (plan step ``bank_import:X-ge-1``, finding **N-322**'s rule).
    # The near tier's own refusals are already in ``declined``; the exact
    # tier's is the day window, which :mod:`._pairing` owns and now publishes.
    # A near refusal WINS on a line that has both: it is the narrower claim,
    # naming a figure the app scored rather than one it merely equalled.
    for line_id, sentence in exactly_matched_but_outside_the_window(
        residue_lines, residue_rows,
    ).items():
        declined.setdefault(line_id, sentence)
    # **The key must survive a ``None`` gap**, which the three-valued
    # ``day_gap`` introduced and a first draft of this sort did not: mixing one
    # undated proposal with one dated one raised ``TypeError`` inside the page
    # render, so the whole feature 500'd on the developer's own data.  Dated
    # proposals order by how far the app is out; the undated ones -- which
    # SETTLE a row rather than re-date one -- follow, because confirming and
    # correcting are what a reviewer scans a statement for.
    #
    # **A repricing proposal sorts after all of them**, which is the same
    # ascending-by-how-much-changes rule one term further out: it is the only
    # proposal on this card that moves an AMOUNT, and ruling **R-FZ(c)** gives
    # it its own sweep for exactly that reason.  Its own count is named at the
    # top of the card, so sorting it last hides nothing.
    return ProposedMatches(
        proposals=tuple(sorted(
            exact + near,
            key=lambda proposal: (
                proposal.reprices,
                proposal.day_gap is None,
                proposal.day_gap if proposal.day_gap is not None else 0,
                proposal.posts_on,
            ),
        )),
        crowded_days=tuple(crowded),
        declined_lines=declined,
    )
