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

**Repeated amounts are assigned GLOBALLY, not first-come.**  Five bank lines and
five app rows all reading `$1,910.95` is not a hypothetical: it is this
developer's mortgage transfer, monthly, and a greedy left-to-right pass pairs
them by file order rather than by proximity.  :func:`_assign` minimises the
total day distance over each amount group instead, so a pairing is the best one
available rather than the first one found.

**A GROUP is proposed only where it is unambiguous** -- N app rows sharing one
day that sum EXACTLY to one line.  The bank shows one payroll deposit where the
app holds three rows, and that shape is worth offering; a general subset sum
over an account's whole history is not, because the number of subsets that hit
a given cent is large and every one of them would be a proposal a human has to
refute.

Services-boundary discipline: plain data in, frozen dataclasses out.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from itertools import combinations

from ._offers import BankLine, CandidateRow, MatchProposal, RowKind

#: How far a recorded settle day may sit from the bank's posted day and still
#: be proposed as the same movement.  **Measured rather than chosen**: on the
#: developer's own statement 58 lines pair uniquely at 14 days with 4
#: ambiguous, against 55 / 1 at 7 and 50 / 21 at 30.  The value is the knee of
#: that curve -- far enough to catch a settle recorded a week late, near enough
#: that a monthly commitment cannot reach its neighbour.
DAY_WINDOW: int = 14

#: The largest UNDATED pool that may join every day's group search.  An
#: undated row has no day to be grouped by, so it composes with any day's set
#: -- which is only affordable while there are few of them.  The developer's
#: own account carries 674, and admitting those put every day over
#: :data:`MAX_GROUP_DAY_ROWS` and killed the group arm entirely.  Sized to the
#: shape the arm exists for: a split deposit whose unsettled members number a
#: handful, not an account's whole backlog.
MAX_GROUP_UNDATED: int = 6

#: The largest number of candidate rows a single recorded DAY may hold and
#: still be searched for groups.  Beyond it ``n choose k`` produces more
#: coincidences than explanations, and the day is reported by
#: :func:`skipped_group_days` rather than silently passed over.  The developer's
#: busiest day carries 5.
MAX_GROUP_DAY_ROWS: int = 12

#: The largest number of app rows a GROUP proposal will combine.  The bank
#: splits nothing further than this in the developer's own data -- a payroll
#: deposit is at most three rows (salary, phone allowance, health allowance) --
#: and each step up multiplies the subsets considered, so a wider bound would
#: buy proposals nobody asked for at a cost that grows like ``n choose k``.
MAX_GROUP_ROWS: int = 4


def _day_distance(row: CandidateRow, posted_on: date) -> "int | None":
    """Return how far *row*'s recorded day sits from *posted_on*.

    Args:
        row: The candidate.
        posted_on: The bank's day.

    Returns:
        The absolute distance in days, or ``None`` for a row carrying no day at
        all -- there is no distance from "never observed to have moved", and
        treating one as zero would rank an unsettled row above a settled one
        that is genuinely a day out.
    """
    if row.settled_on is None:
        return None
    return abs((row.settled_on - posted_on).days)


def _window_anchor(row: CandidateRow) -> "date | None":
    """Return the day *row* sits on for the purposes of the search window.

    **A PURCHASE is never truly undated, and treating one as if it were is how
    a `$25.00` May swipe came to be offered against a `$25.00` July purchase.**
    :func:`_day_distance` answers ``None`` for a row carrying no ``settled_on``
    -- correct, because that column is the CASH clock and a row never observed
    to move has no distance from a bank day.  But a purchase carries a second
    clock, ``purchased_on``, which every purchase has.  So "undated" is true of
    a purchase's cash clock and false of the purchase.

    **What made this load-bearing is ruling R-FW.**  Until plan step X-f6a-3a
    the purchase day bounded the pairing implicitly: a line posted before the
    purchase was made could never be ACCEPTED, so offering it was merely
    useless.  R-FW makes that pairing legal -- the match now CORRECTS the
    purchase day -- which left an undated purchase with no bound at all, since
    :data:`DAY_WINDOW` is measured from ``settled_on`` and it has none.
    Measured on the developer's own statement, the three worst pairings then
    re-dated a purchase by 39, 40 and 59 days on an exact-amount coincidence,
    overwriting the one fact that would have exposed the mis-pairing.  The 14
    pairings the step exists for are 1 to 5 days out, so every one survives the
    bound.  Found by two independent adversarial reviews 2026-08-18.

    **A TRANSACTION keeps answering ``None``, and that is not an oversight.**
    Its ``expected_on`` is its pay period's START -- a budgeting fact, not an
    observation of when money moved -- so bounding a bill by 14 days from it
    would refuse exactly the arm that settles a row the app never marked as
    having happened, 11 of them inside the developer's own statement span.

    Args:
        row: The candidate.

    Returns:
        The day to measure the window from, or ``None`` for a row that
        genuinely has none.
    """
    if row.settled_on is not None:
        return row.settled_on
    if row.kind is RowKind.PURCHASE:
        return row.expected_on
    return None


def _within_window(row: CandidateRow, line: BankLine) -> bool:
    """Return whether *row* may be paired with *line* at all.

    Two independent tests, and both are about THIS pair rather than about the
    row: a distance the owner would recognise, and a pairing the write door can
    actually carry out.

    **The distance**, measured from :func:`_window_anchor` rather than from
    ``settled_on`` alone.  A TRANSACTION carrying no settle day is within the
    window whatever its distance: the bank is the only evidence it has about
    when that money moved, so there is nothing to be far from, and that is the
    arm which reaches the rows inside a statement's span that had never been
    marked as having happened.  A PURCHASE is anchored on the day it was MADE,
    because it always has one.

    **The FLOOR, and forgetting one was a real defect.**  A purchase cannot
    reach the bank before it was made, so ``entry_service.update_entry``'s
    ``_reject_settled_before_purchase`` refuses that pairing at the door -- and
    a proposer blind to ``purchased_on`` renders an Accept button that can
    never succeed.  Measured on the developer's own clone: 23 (line, undated
    purchase) pairs where the line posted BEFORE the purchase was made.  Found
    by adversarial security review 2026-08-17.

    **What plan step X-f6a-3a changed is that the floor is now SATISFIABLE
    rather than merely refusing**, because a match may move the purchase day
    (ruling **R-FW**).  The pairing is legal when the app's own purchase day
    already sits on or before the bank's, OR when the day the match would write
    does -- so a purchase the owner recorded days late is a CORRECTION to offer
    instead of a line the review screen leaves looking unexplained.  It is the
    same measurement that forced the ruling: on the developer's own statement
    14 unexplained lines worth `$1,028.66` are an exact amount at the same
    merchant as an unmatched purchase, refused only because the recorded
    purchase day was 1 to 5 days after the bank posted it -- and the door
    X-f6a-3b builds would have offered to record every one of them a SECOND
    time.

    **The floor is asked of the LINE, not of a bare day, and that is the bug
    fix rather than a signature tidy-up.**  ``_one_to_one`` kept a row that was
    legal against ANY line sharing its amount, and :func:`_assign`'s undated arm
    then paired it with whichever line was free -- so a per-GROUP survival test
    stood in for a per-PAIR legality test.  Measured on the developer's own
    clone at HEAD: 3 offered proposals whose Accept could never succeed, the
    worst pairing a line posted 2026-06-01 with a purchase made 2026-07-27.

    Args:
        row: The candidate.
        line: The bank line it would be paired with.

    Returns:
        Whether to consider the pairing at all.
    """
    if (
        row.kind is RowKind.PURCHASE
        and row.expected_on is not None
        and line.posted_on < row.expected_on
        and line.posted_on < line.happened_on
    ):
        return False
    anchor = _window_anchor(row)
    if anchor is None:
        return True
    return abs((anchor - line.posted_on).days) <= DAY_WINDOW


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

    **Rows carrying NO day sit outside that argument and are matched after
    it.**  An unobserved row has no position on the line, so a dated row that
    genuinely sits near a line always wins and only the lines nothing dated
    could explain reach one.  That is the arm that settles a row the app never
    marked as having happened -- 11 of them inside the developer's own
    statement span.

    **BOTH arms re-ask :func:`_within_window` per PAIR, and its absence was a
    shipped defect** (plan step X-f6a-3a).  The caller's filter is an ``any``
    over the amount group -- a row legal against ONE line survives it -- and
    each arm then assigned a surviving row to whichever line it liked.
    Measured on the developer's own clone: 3 proposals were offered whose
    Accept could never succeed, the worst pairing a line posted 2026-06-01 with
    a purchase made 2026-07-27, 56 days later.
    **A first fix guarded only this arm** on the reasoning that
    :func:`_least_cost_pairing` is "bounded by :data:`DAY_WINDOW`, which
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
    dated = sorted(
        (row for row in rows if row.settled_on is not None),
        key=lambda row: (row.settled_on, row.row_id),
    )
    undated = sorted(
        (row for row in rows if row.settled_on is None),
        key=lambda row: row.row_id,
    )
    ordered = sorted(lines, key=lambda line: (line.posted_on, line.line_id))

    paired = _least_cost_pairing(ordered, dated)
    taken = {line.line_id for line, _ in paired}
    for line in ordered:
        if line.line_id in taken or not undated:
            continue
        legal = next(
            (
                index for index, row in enumerate(undated)
                if _within_window(row, line)
            ),
            None,
        )
        if legal is None:
            continue
        paired.append((line, undated.pop(legal)))
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

    Args:
        lines: Bank lines sharing one amount, ASCENDING by day.
        rows: Candidate rows sharing it, ASCENDING by day, every one of them
            carrying a day.

    Returns:
        The chosen pairs, ascending by day.  Empty when either side is.
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
            distance = abs(
                (rows[j - 1].settled_on - lines[i - 1].posted_on).days
            )
            # **The legality test is per PAIR here too**, and its absence was
            # the same defect the undated arm carried.  ``distance`` bounds a
            # row by its own settle day; it says nothing about whether the
            # write door could carry the pairing out, and the caller's filter
            # is an ``any`` over the amount group -- so a row legal against one
            # line could be assigned to another.  The docstring above used to
            # claim ``DAY_WINDOW`` "subsumes the floor", which is false: the
            # floor is about ``expected_on`` and the line's stated day, and the
            # window is about ``settled_on``.  Found by adversarial test-quality
            # review 2026-08-18.
            if distance <= DAY_WINDOW and _within_window(
                rows[j - 1], lines[i - 1],
            ):
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
            if any(_within_window(row, line) for line in group_lines)
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
                day_gap=_day_distance(row, line.posted_on),
            ))
    return proposals


def _day_sums(
    rows: "list[CandidateRow]",
) -> "dict[date, dict[Decimal, list[tuple[CandidateRow, ...]]]]":
    """Return, per recorded day, every small row set and what it sums to.

    Computed ONCE over the account rather than re-searched per bank line, which
    is what keeps grouping linear in the statement's length: the sums for a day
    do not depend on which line is asking.  A day holding more rows than
    :data:`MAX_GROUP_DAY_ROWS` is skipped whole and named by
    :func:`skipped_group_days`, because ``n choose k`` over a large day
    produces more coincidences than explanations -- and a bound that is silent
    about what it dropped reads as "nothing to group here".

    **A row carrying NO day joins every day's bucket, and ONLY while there are
    few enough of them to search.**  Ruling **R-FV**'s third arm has to reach
    the group path -- a split payroll deposit with one unsettled member is the
    ordinary shape -- so an undated row, having no day to disagree with,
    composes with any day's set.  But an account's undated pool is not small:
    the developer's carries **674** of 825 candidates, and admitting all of
    them put every day over :data:`MAX_GROUP_DAY_ROWS`, which killed the group
    arm outright and reported 51 days as "too crowded" when the real cause was
    this rule.  Found by adversarial financial review 2026-08-17, which
    measured 0 groups proposed where the previous implementation proposed 3.

    So the undated pool joins only when it is under
    :data:`MAX_GROUP_UNDATED`, and :func:`undated_pool_too_large` REPORTS when
    it is not -- for the reason :func:`skipped_group_days` exists: a bound that
    says nothing about what it dropped reads as a clean sweep.

    Args:
        rows: The candidate rows.

    Returns:
        ``{day: {signed sum: [row sets]}}`` over sets of 2 to
        :data:`MAX_GROUP_ROWS` rows.
    """
    dated: "dict[date, list[CandidateRow]]" = defaultdict(list)
    undated = [row for row in rows if row.settled_on is None]
    for row in rows:
        if row.settled_on is not None:
            dated[row.settled_on].append(row)
    joinable = undated if len(undated) <= MAX_GROUP_UNDATED else []
    by_day = {day: day_rows + joinable for day, day_rows in dated.items()}

    sums: "dict[date, dict[Decimal, list[tuple[CandidateRow, ...]]]]" = {}
    for day, day_rows in by_day.items():
        if len(day_rows) > MAX_GROUP_DAY_ROWS:
            continue
        day_sums: "dict[Decimal, list[tuple[CandidateRow, ...]]]" = defaultdict(list)
        for size in range(2, min(len(day_rows), MAX_GROUP_ROWS) + 1):
            for combo in combinations(day_rows, size):
                total = sum(
                    (row.cash_amount for row in combo), Decimal("0.00"),
                )
                day_sums[total].append(combo)
        if day_sums:
            sums[day] = day_sums
    return sums


def skipped_group_days(rows: "list[CandidateRow]") -> "list[date]":
    """Return the days :func:`propose` refused to search for GROUPS.

    **A bound that does not say what it dropped reads as a clean sweep.**  A
    day holding more than :data:`MAX_GROUP_DAY_ROWS` candidate rows is not
    grouped, so a line a group on that day would have explained is listed as
    unmatched -- and the owner has to be told that is a limit rather than an
    absence, on the screen, so they can build the group by hand.

    Args:
        rows: The candidate rows the proposals were built from.

    Returns:
        The days that were skipped, ascending.  Empty on the developer's own
        data, where the busiest day carries 5 candidate rows.
    """
    undated = sum(1 for row in rows if row.settled_on is None)
    joinable = undated if undated <= MAX_GROUP_UNDATED else 0
    by_day: "dict[date, int]" = defaultdict(int)
    for row in rows:
        if row.settled_on is not None:
            by_day[row.settled_on] += 1
    # ``+ joinable`` because that is what :func:`_day_sums` actually puts in a
    # day's bucket.  Counting the whole undated pool instead named every day on
    # the developer's account as crowded, which was a true statement about a
    # rule nobody wanted rather than about the day.
    return sorted(day for day, count in by_day.items()
                  if count + joinable > MAX_GROUP_DAY_ROWS)


def undated_pool_too_large(rows: "list[CandidateRow]") -> int:
    """Return how many undated rows were kept OUT of every group search.

    The second bound :func:`_day_sums` applies, reported for the same reason
    the first is.  ``0`` when the pool was small enough to search, which is the
    only case in which a group may name a row nobody has settled.

    Args:
        rows: The candidate rows the proposals were built from.

    Returns:
        The size of the excluded pool, or ``0``.
    """
    undated = sum(1 for row in rows if row.settled_on is None)
    return undated if undated > MAX_GROUP_UNDATED else 0


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
) -> "list[MatchProposal]":
    """Return the proposals where several app rows sum to one bank line.

    R-FS's second shape, in the direction the app can propose without guessing.
    The rows considered for one line are those sharing a single recorded day
    inside the window, which is what a split deposit looks like: three rows
    that all settled the day the paycheck landed.

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
        One proposal per line that exactly one same-day row set sums to.  A
        line that several distinct sets could explain gets none: an ambiguous
        proposal is a question dressed as an answer.
    """
    sums = _day_sums(rows)
    proposals = []
    for line in lines:
        found = [
            (day, combo)
            for day, day_sums in sums.items()
            if abs((day - line.posted_on).days) <= DAY_WINDOW
            for combo in day_sums.get(line.amount, ())
            # Every member must be legally datable to this line's day, and no
            # two of them may be an envelope and a purchase inside it: the
            # accept door refuses both, so offering either is an Accept button
            # that cannot succeed.
            if all(_within_window(row, line) for row in combo)
            and not _holds_a_parent_and_its_child(combo)
        ]
        if len(found) != 1:
            continue
        day, combo = found[0]
        proposals.append(MatchProposal(
            lines=(line,), rows=combo,
            day_gap=abs((day - line.posted_on).days),
        ))
    return proposals


def propose(
    lines: "list[BankLine]", rows: "list[CandidateRow]",
) -> "list[MatchProposal]":
    """Return what the app believes goes with what, best first.

    Two passes, and the order between them is the rule: a line explained
    one-to-one is not offered a group as well, because the simpler explanation
    of the same money is the one a reviewer should be shown.

    Nothing here writes, and nothing here decides -- every proposal is a
    question put to the owner (ruling **R-FP**).

    Args:
        lines: The account's unmatched bank lines.
        rows: The account's candidate rows
            (:func:`~._candidates.candidates_for`).

    Returns:
        The proposals, ordered by how far the app's own day sits from the
        bank's and then by the bank's day -- so the ones that merely CONFIRM
        what the app already holds come first and the corrections follow, each
        block in statement order.
    """
    one_to_one = _one_to_one(lines, rows)
    spoken_for_lines = {
        line.line_id for proposal in one_to_one for line in proposal.lines
    }
    spoken_for_rows = {
        (row.kind, row.row_id)
        for proposal in one_to_one for row in proposal.rows
    }
    grouped = _groups(
        [line for line in lines if line.line_id not in spoken_for_lines],
        [row for row in rows if (row.kind, row.row_id) not in spoken_for_rows],
    )
    # **The key must survive a ``None`` gap**, which the three-valued
    # ``day_gap`` introduced and a first draft of this sort did not: mixing one
    # undated proposal with one dated one raised ``TypeError`` inside the page
    # render, so the whole feature 500'd on the developer's own data.  Dated
    # proposals order by how far the app is out; the undated ones -- which
    # SETTLE a row rather than re-date one -- follow, because confirming and
    # correcting are what a reviewer scans a statement for.
    return sorted(
        one_to_one + grouped,
        key=lambda proposal: (
            proposal.day_gap is None,
            proposal.day_gap if proposal.day_gap is not None else 0,
            proposal.posts_on,
        ),
    )
