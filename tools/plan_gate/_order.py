"""The EXECUTION ORDER arms: ``steps.md`` says what to do next, and it is graded.

``_registry.py`` grades what the registries SAY about each other -- owners,
identity classes, forks, the dependency graph.  This module grades the one
thing none of that produces: **the sequence**.

The distinction is the reason the module exists.  Rule 13's ``blocked by``
column is a CONSTRAINT, and on this corpus it under-determines the answer by a
wide margin -- 38 open steps are legal to start at once, so a topological sort
answers "any of these 38" and never "this one next".  A reader asking what to
do next got a graph and a paragraph explaining that the table was an index
rather than an order.  **The sequence is therefore a DECISION**, taken from each
arc's own stated sequencing and written into the ``order`` column, and these
arms are what keep that written decision honest against the graph it must obey.

It is a SEPARATE module rather than more of ``_registry.py`` because that module
reached its 1000-line ceiling when these arms were added, and this project's own
ruling on an over-ceiling module is that it becomes a package or a sibling
rather than being shaved again (findings N-152 / N-156).  The dependency runs
one way -- this module reads ``_registry``, never the reverse -- so there is no
import cycle, and every arm reads the registry's paths at CALL time, which is
what keeps the tests' single staging fixture working unchanged.
"""
from __future__ import annotations

import re

import _registry as registry
from _classes import decomposition_leaf_keys

#: A ``starts`` cell's DERIVED head, reconciled by :func:`starts_violations`
#: against the blocker keys in the same cell.  ``NOW`` is the ready state,
#: ``after #N`` names the rank a step waits for, and ``ticks with #N`` is the
#: container spelling.  Storing a derived value at all is only legal because
#: this pattern is what lets a gate recompute it -- a copy beside no reconciler
#: is the defect several of these arcs exist to remove.
STARTS_HEAD_RX = re.compile(
    r"^(?:NOW|after #(?P<after>\d+)|ticks with #(?P<ticks>\d+))$",
)

#: How long a ``what this step does`` cell may be.  Rule 4's forcing function on
#: one cell: the order earns its place by being READABLE at a glance, and a
#: specification that will not fit belongs in the arc document the row points
#: at.  Measured against the live table with headroom, not sized to fit it.
DESCRIPTION_CAP = 400

#: Cited by every message below, so a failure sends the reader to the rule.
_RULE = "conventions.md rule 14"

#: ``steps.md``'s THIRD self-count, and the one rule 3's arms did not reach.
#: "38 of these steps are legal to start right now" is a statement about the
#: corpus that no arm graded, and it was stale by three the moment a commit
#: added a ready row -- rule 3's own sentence, on a fourth number: a count
#: stated in a registry and checked nowhere is a count that rots.  Anchored on
#: the LIVE wording, because a pattern matching nothing reads as "no count is
#: claimed" and passes.
READY_COUNT_RX = re.compile(
    r"(?P<ready>\d+) of these steps are legal to start right now",
)


#: The ONE derivation of a container's leaves, shared with
#: :func:`_registry.decomposition_violations` rather than re-spelled here.
#: Three copies of it existed until 2026-08-11 and two were per-arc, which made
#: a container whose leaves are filed under an identity SIBLING's name in
#: another arc invisible to its own reconciler -- ``balance:X-l`` and
#: ``recurrence:R-F12`` kept a ``ticks with #10`` a renumber had moved to
#: ``#17``, with every gate green.
_leaf_keys = decomposition_leaf_keys


def rank_map() -> dict[str, int]:
    """Return ``{step key: the rank it is placed at}`` over the whole corpus.

    A ranked step maps to its own ``#N``.  A CONTAINER maps to the rank of its
    LAST open leaf, because that is when it ticks -- so a step blocked by a
    container is blocked until that rank, and the arms below can compare a
    container edge against a number instead of giving up on it.  Containers
    NEST (``X-f2`` holds ``X-f2-c`` holds ``X-f2-c3``), so the pass repeats
    until it stops changing rather than assuming one level.

    A container whose leaves have all shipped maps to nothing: it should have
    ticked, which is :func:`_registry.decomposition_violations`' arm, not this
    module's.
    """
    rows = registry.step_rows()
    ranks = {row.key: row.rank for row in rows if row.rank is not None}
    containers = [row for row in rows if row.is_container]
    for _ in range(len(containers) + 1):
        changed = False
        for parent in containers:
            leaf_ranks = [
                ranks[key] for key in _leaf_keys(parent, rows)
                if key in ranks
            ]
            if not leaf_ranks:
                continue
            latest = max(leaf_ranks)
            if ranks.get(parent.key) != latest:
                ranks[parent.key] = latest
                changed = True
        if not changed:
            break
    return ranks


def _order_cell_problems(rows: list[registry.StepRow]) -> list[str]:
    """Arm 1: every row's ``order`` cell is one of the three legal spellings."""
    return [
        f"{row.key}: the `order` cell reads {row.state!r}.  It must be '#N', "
        f"'container' or 'SHIPPED' -- anything else is a row a reader cannot "
        f"place in the sequence ({_RULE})"
        for row in rows
        if not row.shipped and not row.is_container and row.rank is None
    ]


def _density_problems(by_rank: dict[int, list[registry.StepRow]]) -> list[str]:
    """Arm 2: the ranks run 1..N with no hole.

    A hole is graded because "the first row that is not done" is the whole
    interface: with #7 missing, a reader who has finished #6 cannot tell
    whether the next step is #8 or a row somebody forgot to write down.
    """
    if not by_rank:
        return []
    return [
        f"the order has no #{missing}.  Ranks are dense from 1, so that hole "
        f"makes 'the first row that is not done' ambiguous ({_RULE})"
        for missing in sorted(set(range(1, max(by_rank) + 1)) - set(by_rank))
    ]


def _shared_rank_problems(
    by_rank: dict[int, list[registry.StepRow]],
) -> list[str]:
    """Arm 3: a rank repeats only across an IDENTITY CLASS.

    ``pay_calendar:C5a`` and ``recurrence:R-F10`` are one commit under two
    names and share one position, exactly as rule 11 makes them share one tick
    state.  Two UNRELATED steps at one rank is an order that does not order.
    """
    problems = []
    for rank, sharers in sorted(by_rank.items()):
        if len(sharers) == 1:
            continue
        keys = {row.key for row in sharers}
        if any(keys - {row.key} - set(row.alias_keys()) for row in sharers):
            problems.append(
                f"#{rank} is shared by {sorted(keys)}, which are not one "
                f"identity class.  A rank repeats only where two names are ONE "
                f"commit ({_RULE})",
            )
    return problems


def _graph_consistency_problems(
    rows: list[registry.StepRow],
    ranks: dict[str, int],
) -> list[str]:
    """Arm 4: no step is ranked at or before an unshipped blocker's rank.

    **This is the arm that makes the sequence EXECUTABLE** rather than merely
    written down: without it the order is a preference that its own dependency
    graph forbids, which is the state the previous index documented in prose
    ("two rows sit ABOVE their own blockers") instead of refusing.
    """
    steps = {row.key: row for row in rows}
    problems = []
    for row in rows:
        if row.rank is None:
            continue
        for key in row.blocked_keys():
            blocker = steps.get(key)
            if blocker is None or blocker.shipped:
                continue
            at = ranks.get(key)
            if at is None:
                problems.append(
                    f"{row.key} (#{row.rank}) is blocked by {key}, which holds "
                    f"no rank at all.  An unshipped blocker a reader cannot "
                    f"place cannot be scheduled around ({_RULE})",
                )
            elif at >= row.rank and key not in row.alias_keys():
                problems.append(
                    f"{row.key} is ranked #{row.rank} and the unshipped step it "
                    f"is blocked by, {key}, is ranked #{at}.  The order states "
                    f"a sequence its own dependency graph forbids ({_RULE})",
                )
    return problems


def rank_violations() -> list[str]:
    """Rule 14: the `order` column is a TOTAL ORDER the graph allows.

    Four arms, each its own function because each fails for a different reason
    and a single message saying "the order is wrong" would send a reader to
    re-derive all four.

    Returns:
        One message per violation, each citing the rule.
    """
    rows = registry.step_rows()
    by_rank: dict[int, list[registry.StepRow]] = {}
    for row in rows:
        if row.rank is not None:
            by_rank.setdefault(row.rank, []).append(row)
    return [
        *_order_cell_problems(rows),
        *_density_problems(by_rank),
        *_shared_rank_problems(by_rank),
        *_graph_consistency_problems(rows, rank_map()),
    ]


def _container_starts_problem(
    row: registry.StepRow,
    rows: list[registry.StepRow],
    ranks: dict[str, int],
    stated: str | None,
    head: str,
) -> str | None:
    """A container does not START, it TICKS with its last leaf."""
    if stated is None:
        return (
            f"{row.key} is a container and its `starts` reads {head!r}.  A "
            f"container does not START, it TICKS with its last leaf ({_RULE})"
        )
    leaf_ranks = [
        ranks[key] for key in _leaf_keys(row, rows) if key in ranks
    ]
    if leaf_ranks and int(stated) != max(leaf_ranks):
        return (
            f"{row.key} says it ticks with #{stated} and its last open leaf is "
            f"ranked #{max(leaf_ranks)} ({_RULE})"
        )
    return None


def _step_starts_problem(
    row: registry.StepRow,
    steps: dict[str, registry.StepRow],
    ranks: dict[str, int],
    stated: str | None,
) -> str | None:
    """``NOW`` iff nothing unshipped blocks it, else the LATEST blocker's rank."""
    waits = [
        ranks[key] for key in row.blocked_keys()
        if key in steps and not steps[key].shipped and key in ranks
    ]
    if not waits and stated is not None:
        return (
            f"{row.key} says it starts after #{stated}, and every step it is "
            f"blocked by has SHIPPED.  It reads as blocked while it is ready "
            f"({_RULE})"
        )
    if waits and stated is None:
        return (
            f"{row.key} says NOW while it is blocked by unshipped work at "
            f"#{max(waits)}.  A stale NOW sends a reader at work they cannot "
            f"start ({_RULE})"
        )
    if waits and int(stated) != max(waits):
        return (
            f"{row.key} says it starts after #{stated} and its latest unshipped "
            f"blocker is ranked #{max(waits)} ({_RULE})"
        )
    return None


def ready_count_violation() -> list[str]:
    """Rule 3 on ``steps.md``'s third self-count: how many rows say ``NOW``.

    The sibling of :func:`_registry.steps_stated_count_violation`, here rather
    than there because the READY state is this module's: it is the ``starts``
    head, and a third spelling of "read the head off the cell" is the
    denormalization these registries exist to remove.

    A container is excluded for the same reason it leaves the order entirely --
    it is not a thing a reader picks up, so it is not work that can start.

    Returns:
        One message when the stated figure disagrees with the table, else none.
    """
    text = registry.STEPS.read_text()
    match = READY_COUNT_RX.search(text)
    if match is None:
        return [
            "steps.md states no ready count.  conventions.md rule 3 requires "
            f"the phrase 'N of these steps are legal to start right now' ({_RULE})",
        ]
    ready = sum(
        1 for row in registry.step_rows()
        if not row.shipped and not row.is_container
        and row.blocked.split(" / ")[0].strip() == "NOW"
    )
    if int(match.group("ready")) == ready:
        return []
    return [
        f"steps.md says {match.group('ready')} steps are legal to start right "
        f"now and {ready} are.  A stale ready count tells a cold reader how "
        f"much work is available using a number nothing checks ({_RULE})",
    ]


def starts_violations() -> list[str]:
    """Rule 14: the `starts` cell is DERIVED, and this is its reconciler.

    The cell exists so a reader is never sent to resolve a list of step keys by
    hand; that cross-referencing is what made the previous index unusable.  But
    a derived value stored beside no reconciler is this project's own root
    cause, named in three separate arcs, so it is recomputed on every commit
    that touches the file.

    A stale ``NOW`` sends a reader at work they cannot start; a stale ``after``
    hides work they can.

    Returns:
        One message per violation, each citing the rule.
    """
    rows = registry.step_rows()
    steps = {row.key: row for row in rows}
    ranks = rank_map()
    problems: list[str] = []
    for row in rows:
        if row.shipped:
            continue
        head = row.blocked.split(" / ")[0].strip()
        match = STARTS_HEAD_RX.match(head)
        if match is None:
            problems.append(
                f"{row.key}: `starts` opens with {head!r}.  It must open with "
                f"'NOW', 'after #N' or 'ticks with #N' ({_RULE})",
            )
            continue
        if row.is_container:
            found = _container_starts_problem(
                row, rows, ranks, match.group("ticks"), head,
            )
        else:
            found = _step_starts_problem(row, steps, ranks, match.group("after"))
        if found is not None:
            problems.append(found)
    return problems


def description_violations() -> list[str]:
    """Rule 14: every step says what it is, in one COMPLETE sentence.

    **The class this arm exists for was 38 rows wide.**  The index was once
    generated by taking the head of each arc entry, so rows ended ``-- **THE``,
    ``not an`` and ``the DECOMPOSED parent,`` -- cut off mid-clause.  A reader
    asking "what is X-f3" got a sentence fragment and had to open a second
    document to find out, which is the cross-referencing this registry exists
    to end.

    **Terminal punctuation is the whole predicate, and it is chosen because
    truncation cannot fake it**: a cell cut at a character boundary ends in a
    letter, a comma or a backtick, never in a full stop.

    Returns:
        One message per violation, each citing the rule.
    """
    problems = []
    for row in registry.step_rows():
        text = row.title.strip()
        if not text or text == "--":
            problems.append(
                f"{row.key} has no description.  Every step says what it is "
                f"({_RULE})",
            )
            continue
        if text[-1] not in ".!?":
            problems.append(
                f"{row.key}'s description ends {text[-40:]!r}, without terminal "
                f"punctuation.  That is the signature of a TRUNCATED cell, the "
                f"defect this arm exists for ({_RULE})",
            )
        if len(text) > DESCRIPTION_CAP:
            problems.append(
                f"{row.key}'s description is {len(text)} characters against a "
                f"{DESCRIPTION_CAP} cap.  A specification that will not fit "
                f"belongs in the arc document this row points at "
                f"(conventions.md rule 4)",
            )
    return problems
