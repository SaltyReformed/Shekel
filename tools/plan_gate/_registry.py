"""Parsing and predicates for the SHARED planning registries.

``_plan_gate.py`` grades ONE document at a time and cannot see a relation that
spans two.  That blindness is what this module exists to remove: ``C2``,
``X-l`` and ``R-F12`` are one step under three names, and ``P3`` / ``N-123``
is one defect whose two arcs ruled OPPOSITE remedies -- both were prose in
three files with nothing reconciling them, and the second went unnoticed from
April to 2026-08-09.

Three registries are graded here:

``docs/plans/ledger.md``       every open finding in every arc, ``arc`` a COLUMN
``docs/plans/steps.md``        every step, one line each, plus the unruled forks
``docs/plans/conventions.md``  the rules, one copy

Neither the owner grammar nor the checkbox grammar is re-implemented:
:func:`_plan_gate.split_owners`, ``OWNER_RX`` and ``CHECKBOX_RX`` carry
false-positive fixes measured against a real ledger, and a second copy of
either would be the very defect this restructure removes.  Rule 12 reconciles
this module's reading of a checkbox against the ticked-entry arm's, so two
grammars would let the index and the specifications disagree about what a
checkbox even is.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from _classes import decomposition_leaf_keys
from _tables import (
    FORKS_HEADER,
    LEDGER_HEADER,
    STEPS_HEADER,
    Fork,
    LedgerRow,
    StepRow,
    rows_under,
)
from _plan_gate import (
    CHECKBOX_RX,
    COMMIT_SHA,
    NON_STEP_OWNERS,
    OWNER_RX,
    split_owners,
)

REPO = Path(__file__).resolve().parents[2]
PLANS = REPO / "docs" / "plans"

LEDGER = PLANS / "ledger.md"
STEPS = PLANS / "steps.md"
CONVENTIONS = PLANS / "conventions.md"


STATED_COUNT_RX = re.compile(r"\*\*The ledger stands at (?P<count>\d+) rows?\.?\*\*")

#: ``steps.md``'s own two self-counts, graded from 2026-08-11 by
#: :func:`steps_stated_count_violation`.  Both had gone stale by then, which is
#: what rule 3 already says about the sibling registry.  The patterns are
#: deliberately anchored on the LIVE wording -- a pattern that matches nothing
#: reads as "no count is claimed" and passes, which is the failure mode the
#: balance README's own row-count arm shipped with.
STEPS_COUNT_RX = re.compile(r"\*\*(?P<total>\d+) steps?, (?P<open>\d+) open\.?\*\*")
BLOCKED_GRAPH_RX = re.compile(
    r"holds (?P<edges>\d+) edges? over (?P<rows>\d+) rows?",
)

#: A ``steps.md`` ``commit`` cell naming a hash: the WHOLE cell is one
#: backticked sha.  The shape comes from :data:`_plan_gate.COMMIT_SHA` so the
#: index and the specifications cannot disagree about what a hash is; the
#: anchoring is this module's, because here the discriminator is that the cell
#: holds nothing else.
COMMIT_CELL_RX = re.compile(rf"^`{COMMIT_SHA}`$")

#: The three-colour marks :func:`_first_cycle` walks with.  GREY means "on the
#: current path", which is the only state that distinguishes a cycle from a
#: node merely reached twice -- a two-colour visited set reports a diamond
#: (two steps blocked by one third) as a cycle, which every one of these
#: registries contains.
_WHITE, _GREY, _BLACK = 0, 1, 2


#: One arc document, by the slug its registry rows carry.
ARC_DOCS = {
    "balance": REPO / "docs/audits/balance_architecture/README.md",
    "recurrence": PLANS / "implementation_plan_recurrence_redesign.md",
    "pay_calendar": PLANS / "implementation_plan_pay_calendar.md",
    "credit_card": PLANS / "implementation_plan_credit_card.md",
    "bank_import": PLANS / "implementation_plan_bank_import.md",
    "salary": PLANS / "implementation_plan_salary.md",
}


def ledger_rows() -> list[LedgerRow]:
    """Every finding in ``ledger.md``."""
    return [
        LedgerRow(*cells)
        for cells in rows_under(LEDGER.read_text(), LEDGER_HEADER)
    ]


def step_rows() -> list[StepRow]:
    """Every step in ``steps.md``: its order, its containers and its shipped.

    Three SECTIONS, one registry, so all three are read -- they share a header
    because they hold the same kind of row.  The forks table in the same file
    carries a different one and is :func:`forks`'s alone (finding **N-234**).
    """
    return [
        StepRow(*cells)
        for cells in rows_under(STEPS.read_text(), STEPS_HEADER)
    ]


def forks() -> list[Fork]:
    """Every unruled-fork row in ``steps.md``."""
    return [
        Fork(*cells) for cells in rows_under(STEPS.read_text(), FORKS_HEADER)
    ]


def arc_checkboxes(arc: str) -> dict[str, bool]:
    """Return ``{step id: ticked}`` for every checkbox in *arc*'s document.

    Fenced regions are removed first: a ``##``-prefixed line inside a fence
    would otherwise truncate the scan and silently drop every step after it.
    """
    text = ARC_DOCS[arc].read_text()
    found, fenced = {}, False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = CHECKBOX_RX.match(line)
        if match:
            found[match.group("step")] = match.group("tick").lower() == "x"
    return found


def stated_count_violation() -> str | None:
    """Rule 3: ``ledger.md`` states its own size and the number is checked."""
    text = LEDGER.read_text()
    match = STATED_COUNT_RX.search(text)
    if match is None:
        return (
            "ledger.md states no row count.  conventions.md rule 3 requires the "
            "sentence '**The ledger stands at N rows.**'"
        )
    stated, actual = int(match.group("count")), len(ledger_rows())
    if stated != actual:
        return (
            f"ledger.md says it stands at {stated} rows and the table holds "
            f"{actual} (conventions.md rule 3)"
        )
    return None


def steps_stated_count_violation() -> list[str]:
    """Rule 3, on the registry that stated its size and was NOT graded.

    **``ledger.md``'s count has been checked since rule 3 was written and
    ``steps.md``'s two were not**, so both of its counts went stale exactly the
    way rule 3 exists to prevent -- and did, measured on 2026-08-11: the header
    read "112 steps, 96 open" against a table holding 113 and 95, wrong in BOTH
    directions inside one merge, because one session appended a step while
    another ticked one.  A cold reader is told what may start now by a number
    the gate had no opinion about.

    Two sentences are graded, and they are separate arms because they go stale
    for different reasons: the SIZE moves when a step is appended, the OPEN
    count when one is ticked, and the GRAPH size when a ``blocked by`` edge is
    written.  A single arm reporting "something is off" would send a reader to
    re-derive all three.

    Returns:
        One message per disagreement; empty when the header is true.
    """
    text = STEPS.read_text()
    problems: list[str] = []
    rows = step_rows()

    match = STEPS_COUNT_RX.search(text)
    if match is None:
        problems.append(
            "steps.md states no step count.  conventions.md rule 3 requires "
            "the sentence '**N steps, M open.**'",
        )
    else:
        stated_total = int(match.group("total"))
        stated_open = int(match.group("open"))
        # OPEN is the complement of SHIPPED, never a literal word in the cell.
        # The `order` column now carries a rank, `container` or `SHIPPED`, and
        # an arm keyed on the word "open" counted ZERO the moment that column
        # started saying something useful -- a gate that reads a spelling
        # rather than a state stops grading the instant the spelling improves.
        actual_open = sum(1 for row in rows if not row.shipped)
        if stated_total != len(rows):
            problems.append(
                f"steps.md says it holds {stated_total} steps and the table "
                f"holds {len(rows)} (conventions.md rule 3)",
            )
        if stated_open != actual_open:
            problems.append(
                f"steps.md says {stated_open} are open and {actual_open} are "
                f"(conventions.md rule 3)",
            )

    graph = BLOCKED_GRAPH_RX.search(text)
    if graph is None:
        problems.append(
            "steps.md states no `blocked by` graph size.  conventions.md "
            "rule 3 requires the phrase 'holds N edges over M rows'",
        )
    else:
        edges = sum(len(row.blocked_keys()) for row in rows)
        carriers = sum(1 for row in rows if row.blocked_keys())
        if int(graph.group("edges")) != edges:
            problems.append(
                f"steps.md says the graph holds {graph.group('edges')} edges "
                f"and it holds {edges} (conventions.md rule 3)",
            )
        if int(graph.group("rows")) != carriers:
            problems.append(
                f"steps.md says those edges span {graph.group('rows')} rows "
                f"and they span {carriers} (conventions.md rule 3)",
            )

    return problems


def owner_violations() -> list[str]:
    """Rules 1 and 2: every row names a LIVE owner, never a shipped step."""
    steps = {row.key: row for row in step_rows()}
    problems: list[str] = []
    for row in ledger_rows():
        cell = row.owner.strip()
        if not cell:
            problems.append(f"{row.key}: empty owner (conventions.md rule 1)")
            continue
        for part in split_owners(cell):
            match = OWNER_RX.match(part)
            if match is None:
                problems.append(
                    f"{row.key}: owner {part!r} does not match the owner grammar "
                    f"(conventions.md rule 1)",
                )
                continue
            owner, note = match.group("owner"), match.group("note")
            if owner in NON_STEP_OWNERS:
                if owner == "operator" and not note:
                    problems.append(
                        f"{row.key}: 'operator' must state the question "
                        f"(conventions.md rule 1)",
                    )
                if owner == "developer-decision" and not (
                    note and re.search(r"\d{4}-\d{2}-\d{2}", note)
                ):
                    problems.append(
                        f"{row.key}: 'developer-decision' must carry the date the "
                        f"fork was taken (conventions.md rule 1)",
                    )
                continue
            step = steps.get(f"{row.arc}:{owner}")
            if step is None:
                problems.append(
                    f"{row.key}: owner {owner!r} names no step in steps.md for arc "
                    f"{row.arc!r} (conventions.md rule 1)",
                )
            elif step.shipped:
                problems.append(
                    f"{row.key}: owner {owner!r} names a SHIPPED step "
                    f"({step.commit}).  A step that ships re-points every row that "
                    f"named it (conventions.md rule 2)",
                )
    return problems


def unique_key_violations() -> list[str]:
    """Rule 10: ``(arc, id)`` is a WHOLE key, and it is unique across the corpus.

    **A blank half is checked before uniqueness, and that ordering is the
    point.**  ``_rows`` drops a row whose FIRST cell is empty, so an empty
    ``arc`` costs a row and the rule 3 count arm reports it.  An empty ``ident``
    is in the second cell: the row parses, the table still holds 138 rows, the
    stated count still agrees, and the row's key silently becomes ``balance:``.
    Every other predicate then grades a finding that has no name -- and a
    finding nobody can cite is a finding nobody closes.
    """
    problems: list[str] = []
    for label, rows in (
        ("ledger.md", [(row.arc, row.ident, row.key) for row in ledger_rows()]),
        ("steps.md", [(row.arc, row.ident, row.key) for row in step_rows()]),
    ):
        seen: set[str] = set()
        for arc, ident, key in rows:
            if not arc.strip() or not ident.strip():
                problems.append(
                    f"{label}: row {key!r} has an empty arc or id.  The key is "
                    f"(arc, id) and BOTH halves are required "
                    f"(conventions.md rule 10)",
                )
            if key in seen:
                problems.append(
                    f"{label}: duplicate key {key!r}.  The key is (arc, id) and it "
                    f"is unique (conventions.md rule 10)",
                )
            seen.add(key)
    return problems


def alias_violations() -> list[str]:
    """Rule 11, first half: an identity class shares ONE tick state."""
    steps = {row.key: row for row in step_rows()}
    problems: list[str] = []
    for row in step_rows():
        for alias in row.alias_keys():
            other = steps.get(alias)
            if other is None:
                problems.append(
                    f"{row.key}: alias {alias!r} names no step in steps.md "
                    f"(conventions.md rule 11)",
                )
                continue
            if other.shipped != row.shipped:
                problems.append(
                    f"{row.key} is {row.state} while its alias {alias} is "
                    f"{other.state}.  They are ONE step under two names "
                    f"(conventions.md rule 11)",
                )
    return problems


def fork_violations() -> list[str]:
    """Rule 11, second half: a fork binds its remedies AND its defect row.

    Three arms, because a fork that only guards ticks stops guarding anything
    the moment it is ruled -- and a ruling nobody acts on is the state this
    registry exists to make visible:

    1. while UNRULED, neither competing remedy may be ticked;
    2. the defect cell must name at least one LIVE ledger row (only one stays
       live once the two arcs' rows are merged);
    3. once RULED, that row is OWNED by the remedy that won.

    Arm 3 is the one an adversarial review found missing: ``P3`` / ``N-123``
    carried ``developer-decision`` while the fork was open, and rule 2 -- which
    re-points a row when its owner ships -- never fires on a row that names no
    step.  So the row could have kept pointing at a decision that had been
    taken, indefinitely, with every gate green.
    """
    steps = {row.key: row for row in step_rows()}
    ledger = {row.key: row for row in ledger_rows()}
    problems: list[str] = []
    for fork in forks():
        for key in fork.remedy_keys():
            step = steps.get(key)
            if step is None:
                problems.append(
                    f"fork {fork.defect!r}: remedy {key!r} names no step in "
                    f"steps.md (conventions.md rule 11)",
                )
            elif step.shipped and not fork.is_ruled:
                problems.append(
                    f"fork {fork.defect!r} is NOT YET RULED but its remedy {key} "
                    f"is already SHIPPED.  Whichever ships first decides for both "
                    f"arcs, so it may not ship before the ruling is recorded "
                    f"(conventions.md rule 11)",
                )
        live = [key for key in fork.defect_keys() if key in ledger]
        if not live:
            problems.append(
                f"fork {fork.defect!r}: its defect names no live ledger.md row "
                f"({fork.defect_keys()}).  A fork about nothing decides nothing "
                f"(conventions.md rule 11)",
            )
            continue
        if fork.winner is None:
            continue
        arc, ident = fork.winner.split(":", 1)
        for key in live:
            row = ledger[key]
            owners = [OWNER_RX.match(p) for p in split_owners(row.owner.strip())]
            named = {m.group("owner") for m in owners if m}
            if row.arc != arc or ident not in named:
                problems.append(
                    f"fork {fork.defect!r} was RULED for {fork.winner}, but its "
                    f"defect row {key} is owned by {row.owner!r} in arc "
                    f"{row.arc!r}.  A ruled fork's row names the remedy that "
                    f"WON -- and since rule 1 resolves an owner within the row's "
                    f"own arc, the row belongs in {arc!r} "
                    f"(conventions.md rule 11)",
                )
    return problems


def also_violations() -> list[str]:
    """The ``also`` column's two relations mean opposite things, and are checked.

    ``ledger.md`` and ``conventions.md`` both give this distinction a section
    headed "why conflating them deletes work", and until an adversarial review
    asked, both were prose that no predicate read:

    * ``= arc:id`` -- the same claim, MERGED into this row, so the target must
      NOT still be a live row;
    * ``~ arc:id`` -- a distinct finding sharing a root cause, which must NOT be
      merged, so the target must still BE a live row.

    Rewriting all three ``=`` relations as ``~`` was measured to change nothing
    anywhere.  The stale reference this arm catches is not hypothetical either:
    after the 2026-08-09 merges ``N-128``'s cell still named ``recurrence:F-10``,
    a row that no longer existed, and it was found by reading rather than by a
    gate.
    """
    live = {row.key for row in ledger_rows()}
    problems: list[str] = []
    for row in ledger_rows():
        cell = row.also.strip()
        if cell == "--":
            continue
        for relation, key in re.findall(
            r"([=~])\s*([a-z_]+:[A-Za-z0-9][A-Za-z0-9-]*)", cell,
        ):
            if relation == "=" and key in live:
                problems.append(
                    f"{row.key}: `also` says '= {key}', which means MERGED into "
                    f"this row -- but {key} is still its own live row "
                    f"(ledger.md's two relations)",
                )
            if relation == "~" and key not in live:
                problems.append(
                    f"{row.key}: `also` says '~ {key}', a DISTINCT finding that "
                    f"must not be merged -- but {key} names no live row "
                    f"(ledger.md's two relations)",
                )
    return problems


def index_agreement_violations() -> list[str]:
    """Rule 12: ``steps.md`` and the arc documents agree in BOTH directions."""
    problems: list[str] = []
    indexed: dict[str, set[str]] = {arc: set() for arc in ARC_DOCS}
    by_key = {row.key: row for row in step_rows()}
    for row in step_rows():
        if row.arc not in ARC_DOCS:
            problems.append(
                f"{row.key}: arc {row.arc!r} is not one of {sorted(ARC_DOCS)} "
                f"(conventions.md rule 12)",
            )
            continue
        indexed[row.arc].add(row.ident)
    for arc, document in ARC_DOCS.items():
        specified = arc_checkboxes(arc)
        for ident in sorted(indexed[arc] - set(specified)):
            problems.append(
                f"{arc}:{ident} is indexed in steps.md but has NO specification in "
                f"{document.name} (conventions.md rule 12)",
            )
        for ident in sorted(set(specified) - indexed[arc]):
            problems.append(
                f"{arc}:{ident} is specified in {document.name} but is NOT "
                f"indexed in steps.md (conventions.md rule 12)",
            )
        for ident, ticked in specified.items():
            row = by_key.get(f"{arc}:{ident}")
            if row is not None and row.shipped != ticked:
                problems.append(
                    f"{arc}:{ident} is {'ticked' if ticked else 'unticked'} in "
                    f"{document.name} but {row.state} in steps.md "
                    f"(conventions.md rule 12)",
                )
    return problems


def commit_column_violations() -> list[str]:
    """Rule 7, the INDEX side: a shipped step names its commit, an open one does not.

    Rule 7 makes a shipped step's SPECIFICATION open with its hash, and
    :func:`_plan_gate.ticked_entry_violations` grades that.  Nothing graded the
    other half -- ``steps.md`` has carried a ``commit`` column since the
    registries were split, and three of twelve SHIPPED rows held ``--``
    (``X-ae``, ``X-af``, ``X-f1``) while their own arc entries cited a hash.
    The index said "shipped" and refused to say what shipped it.

    Deliberately NOT checked: that the two hashes are EQUAL.  ``X-aj1``'s cell
    names `1688f508`, the first of the three commits its entry lists, while the
    entry opens with the merge `dde107f6`; ``X-an``'s cell names the last of
    two.  Both are correct answers to "which commit", and an equality arm would
    force one convention onto a column where the useful hash genuinely differs
    by step.  What matters is that the index names ONE.

    Returns:
        One message per shipped row with no hash, and per open row with one.
    """
    problems: list[str] = []
    for row in step_rows():
        cell = row.commit.strip()
        if row.shipped and not COMMIT_CELL_RX.match(cell):
            problems.append(
                f"{row.key} is SHIPPED but its `commit` cell is {cell!r}.  A "
                f"shipped step names the commit that shipped it, so a reader "
                f"can read the code instead of prose about it "
                f"(conventions.md rule 7)",
            )
        if not row.shipped and cell != "--":
            problems.append(
                f"{row.key} is {row.state} but its `commit` cell names "
                f"{cell!r}.  A step that has not shipped has no commit "
                f"(conventions.md rule 7)",
            )
    return problems


def decomposition_violations() -> list[str]:
    """Rule 13, the decomposition half: a parent ticks with the LAST of its leaves.

    Rule 2 states it in prose -- "a decomposition appends a suffix ... a
    DECOMPOSED parent ticks with the last of its leaves" -- and nothing graded
    it, so a parent could ship over open work with every gate green.  The
    readiness question makes it concrete: ``X-f``, ``X-aj``, ``X-i`` and
    ``X-x`` all read as pickable work while their own leaves are open, because
    a container is not a task.

    **The parent set is DECLARED** (:attr:`StepRow.is_decomposed_parent`) and
    the leaf set is derived by :func:`decomposition_leaf_keys` FROM that set.
    Deriving both would claim ``pay_calendar:C1`` as the parent of ``C10`` /
    ``C11`` / ``C12`` (the live specimen :func:`_staging.a_prefix_trap`
    derives); deriving neither would need a name list, N-147's defect.
    Declaring the small set and deriving the large one costs one phrase per
    parent and has no list to rot.  **This arm re-spelled that derivation
    inline until 2026-08-11**, per-arc, so it could not see a class whose leaves
    are filed under a sibling's name -- marking all three of `X-l` / `C2` /
    `R-F12` shipped over five open leaves reported ONE of the three.

    **A parent with NO leaves in the table is NOT a failure.**  Rule 5 archives
    a completed span, and ``X-f1``'s fourteen leaves have already left this
    index -- grading their absence would refuse a legal archive and make rule 5
    and rule 13 contradict each other.

    Returns:
        One message per parent that has shipped ahead of an open leaf.
    """
    rows = step_rows()
    problems: list[str] = []
    for parent in rows:
        if not parent.is_decomposed_parent or not parent.shipped:
            continue
        by_key = {row.key: row for row in rows}
        open_leaves = sorted(
            key for key in decomposition_leaf_keys(parent, rows)
            if not by_key[key].shipped
        )
        if open_leaves:
            problems.append(
                f"{parent.key} is SHIPPED but declares itself a DECOMPOSED "
                f"parent and its leaves {open_leaves} are open.  A parent ticks "
                f"with the LAST of its leaves (conventions.md rule 13)",
            )
    return problems


def _first_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """Return one cycle in *graph* as a key path, or ``None`` when acyclic.

    An iterative three-colour depth-first search rather than recursion: the
    corpus is small today, but a gate that raises ``RecursionError`` on a deep
    chain fails in a way that reads as a broken gate rather than as a broken
    plan.

    Returns ONE cycle, not all of them.  A second cycle is almost always the
    same edit's other face, and a failure listing every rotation of every cycle
    buries the one edge the author has to reconsider.

    Args:
        graph: ``{step key: [keys it is blocked by]}``.  A key named as a
            blocker but absent as a node is ignored here -- the referential arm
            reports it, and reporting the same edge twice would make one broken
            cell look like two defects.

    Returns:
        The cycle as ``[a, b, ..., a]``, or ``None``.
    """
    colour = dict.fromkeys(graph, _WHITE)
    for root in graph:
        if colour[root] != _WHITE:
            continue
        # (node, iterator over the keys it is blocked by) -- the explicit stack.
        stack: list[tuple[str, Iterator[str]]] = [(root, iter(graph[root]))]
        path = [root]
        colour[root] = _GREY
        while stack:
            node, pending = stack[-1]
            nxt = next(pending, None)
            if nxt is None:
                colour[node] = _BLACK
                stack.pop()
                path.pop()
                continue
            if nxt not in graph or colour[nxt] == _BLACK:
                continue
            if colour[nxt] == _GREY:
                return path[path.index(nxt):] + [nxt]
            colour[nxt] = _GREY
            path.append(nxt)
            stack.append((nxt, iter(graph[nxt])))
    return None


def blocked_by_violations() -> list[str]:
    """Rule 13: ``blocked by`` is the dependency GRAPH, and it is graded.

    **The column was parsed and never read.**  ``StepRow.blocked`` has existed
    since the registries were split, and no arm consulted it -- so every edge
    in it was decoration, and the one contradiction it should have caught was
    found by hand instead: ``steps.md`` recorded ``R6 blocked by balance:X-an``
    while the recurrence document derived ``R6`` from a column ``R5`` creates
    behind ``X-f4``, three steps past ``X-an``.

    Five arms, each for a distinct way an edge lies:

    1. **no self-block** -- checked FIRST, because a self-edge resolves through
       :func:`step_rows` to the row itself and would otherwise pass every
       later arm while naming an ordering nothing can satisfy;
    2. **referential** -- a blocker names a real step, which is rule 1's arm one
       tier up: an id that survives a rename or a decomposition is a dependency
       on nothing;
    3. **shipped consistency** -- a SHIPPED step is never blocked by an OPEN
       one.  That is not a bookkeeping slip: either the work shipped before its
       stated prerequisite, or the prerequisite was never real;
    4. **acyclic** -- see :func:`_first_cycle`;
    5. **alias coherence** -- an identity class shares ONE blocker set.  ``C2``,
       ``X-l`` and ``R-F12`` are one commit, so a blocker recorded on one name
       and not the others leaves two of the three rows reading as READY.  This
       is exactly rule 11's tick-state arm on the other column, and the failure
       it prevents is the one that makes a reader pick up blocked work.

    Returns:
        One message per violation, each citing the rule.
    """
    rows = step_rows()
    steps = {row.key: row for row in rows}
    graph = {row.key: row.blocked_keys() for row in rows}
    problems: list[str] = []
    for row in rows:
        for key in row.blocked_keys():
            if key == row.key:
                problems.append(
                    f"{row.key} is blocked by ITSELF.  A self-edge names an "
                    f"ordering nothing can satisfy (conventions.md rule 13)",
                )
                continue
            blocker = steps.get(key)
            if blocker is None:
                problems.append(
                    f"{row.key}: `blocked by` names {key!r}, which is no step in "
                    f"steps.md.  A dependency on a step that does not exist is a "
                    f"dependency on nothing (conventions.md rule 13)",
                )
                continue
            if row.shipped and not blocker.shipped:
                problems.append(
                    f"{row.key} is {row.state} while the step it is blocked by, "
                    f"{key}, is {blocker.state}.  Either it shipped before its "
                    f"stated prerequisite or the prerequisite was never real "
                    f"(conventions.md rule 13)",
                )
    cycle = _first_cycle(graph)
    if cycle is not None:
        problems.append(
            f"`blocked by` has a CYCLE: {' -> '.join(cycle)}.  That is not a "
            f"scheduling preference, it is work no order satisfies "
            f"(conventions.md rule 13)",
        )
    for row in rows:
        mine = set(row.blocked_keys())
        for alias in row.alias_keys():
            other = steps.get(alias)
            if other is None or set(other.blocked_keys()) == mine:
                continue
            problems.append(
                f"{row.key} is blocked by {sorted(mine)} while its alias {alias} "
                f"is blocked by {sorted(set(other.blocked_keys()))}.  They are ONE "
                f"step under two names, so they share ONE blocker set -- "
                f"otherwise one of the two rows reads as READY "
                f"(conventions.md rule 13)",
            )
    return problems


#: Rule 4's cap, for the registries.  **They went uncapped until 2026-08-11
#: while rule 4 said "Every document is capped", and the arc documents were the
#: only four the gate held** -- which is rule 3's own sentence one rule over: a
#: rule stated for one artifact and graded on one artifact is a rule the second
#: artifact does not have.  ``ledger.md`` is the one that actually grows, and it
#: is the one that had no forcing function at all.
#:
#: Each is a CEILING WITH ROOM TO WORK, not a number fitted to today's file.
#: When one binds, rule 5 is the answer: archive a completed span.  Raising it
#: is not.
#:
#: **``ledger.md`` was raised 240 -> 241 on 2026-08-13, by developer ruling,
#: and it is the exception rule 5 names rather than a revision of it.**  It was
#: raised by ONE line, which is what it took to land the `recurrence:R7b-2`
#: merge and nothing more, so the forcing function bites again on the very next
#: row.  Recorded here because the alternative reading -- that the cap drifts
#: when it binds -- is the thing rule 5 exists to prevent.
#:
#: **What made rule 5 unavailable is worth stating, because it is a fact about
#: the gate and not about that merge.**  Rule 5's remedy is to archive a
#: COMPLETED span, and ``ledger.md`` had none left: an audit of all 30
#: ``recurrence`` rows found every one live, and :func:`owner_violations`
#: already guarantees no row in ANY arc names a shipped step, so completed
#: spans have by construction already left.  At 198 open rows the headroom arm
#: is no longer satisfiable by archival -- it is reporting how much work is
#: OPEN, which is a different signal from the one rule 5 answers.
#:
#: The relief that does exist: 10 rows are blocked on nothing but a decision
#: (``operator`` or ``developer-decision`` owners), and ruling any one of them
#: closes a row.  Prefer that to a second raise.
#:
#: **``ledger.md`` was raised 241 -> 250 on 2026-08-14 -- the SECOND raise, by
#: developer ruling, and recorded rather than quietly applied.**  Plan step
#: ``pay_calendar:C2-f1`` recorded six findings its two adversarial reviews
#: opened and landed two lines above the headroom arm; the paragraph above had
#: already measured why rule 5 cannot answer, and nothing about that changed.
#:
#: **Two raises in as many days is the signal, so the ruling names the fix and
#: dates it.**  The decision-blocked pile is now **14** rows, not the 10 above,
#: and the developer has committed the NEXT session to clearing it -- every one
#: ruled closes a row, which is what pays this raise back.  Recorded here
#: because if that pass does not happen, this comment is the evidence that the
#: cap drifted rather than the ledger shrinking, which is precisely what rule 5
#: exists to make visible.
#: **``ledger.md`` was raised 250 -> 260 on 2026-08-18 -- the THIRD raise, by
#: developer ruling, and the condition the SECOND one was granted on was NOT
#: met.**  That raise named the fix and dated it: clear the decision-blocked
#: pile, then **14** rows, because ruling any one of them closes a row.  Four
#: days later the pile is still **14** -- two were cleared and two more opened
#: (``balance:N-299`` and ``N-300``, the calendar sweep's clock findings) -- so
#: the paragraph above is now doing exactly the job it was written for: it is
#: the evidence that the cap drifted rather than the ledger shrinking.
#:
#: **What this raise also settles is that rule 5 can never answer for THIS
#: file, and the reason is structural rather than circumstantial.**  Plan step
#: ``bank_import:X-f6a-3a`` swept all 217 rows before asking: exactly 7 cite a
#: code symbol that no longer exists, 2 of those are false positives, and all 5
#: of the rest are LIVE defects whose citation went stale when code was renamed
#: -- the ``N-97``/``N-18`` shape.  There is no dead weight.  61% of rows carry
#: ``$0.00`` or "latent" as their worst measured, so the file is design debt
#: awaiting a step, and 152 of 217 belong to one arc.  **One finding is one
#: line, so a line cap on this file is a cap on how many defects the project is
#: allowed to have MEASURED** -- it binds hardest when adversarial review is
#: working, which is the discipline the whole project leans on.  The developer
#: was offered the structural fixes (drop the line cap and keep
#: :data:`LEDGER_ROW_CAP`, cap per arc, or split the balance arc out) and ruled
#: for a condensed header plus this raise, keeping the forcing function while
#: the arcs that own 35 of these rows (``X-ai``, ``X-ak``, ``X-f3c``) land.
#: **``ledger.md``'s LINE cap was DROPPED on 2026-08-25, by developer ruling,
#: and this is the FOURTH entry in the history above rather than a fourth
#: raise.**  The paragraph above had already measured why: rule 5 can never
#: answer for this file, one finding is one line, and a line cap on it is a cap
#: on how many defects the project is allowed to have MEASURED.  Two more
#: findings hit it on 2026-08-25 at ``bank_import:X-gb``, three days after
#: ``X-ga`` hit it, and the assistant routed around it by writing them into
#: code docstrings -- which is the failure this records: the cap did not force
#: the ledger to shrink, it forced a measured defect out of the registry that
#: exists to hold it.
#:
#: **What replaces it, and why each piece.**  :data:`LEDGER_ROW_CAP` stays and
#: is now the whole of rule 4 for this file -- it is the arm that actually
#: prevents the failure the line cap was reached for, a row swelling into the
#: arc document's argument.  :data:`LEDGER_RUNAWAY_ROWS` is a backstop set far
#: above any real backlog, so an accident that duplicates the table still
#: fails.  And :func:`open_findings_by_arc` puts the backlog in the FILE, where
#: :func:`stated_arc_counts_violation` grades that the number is true -- because
#: the thing worth forcing was never the file's length: it is that the pile is
#: looked at.  *This said the function "PRINTS the backlog every run" until
#: 2026-09-01, and it does not: the plan gate contains no ``print`` call at all,
#: and a number on gate stdout would be read by nobody anyway.  The report lives
#: where every reader of the registry meets it, which is the stronger form.*
#: On the day of the ruling that pile was 227 rows, 156 of
#: them ``balance``, with 21 blocked on nothing but a decision -- the same 21
#: whose clearance the 250 -> 260 raise had been granted against.
#: **``steps.md``'s LINE cap was DROPPED on 2026-08-25, by developer ruling, on
#: the argument the entry above had already made for ``ledger.md`` -- the same
#: day, one registry over.**  One STEP is one line there, so a line cap on the
#: file is a cap on how finely the plan is allowed to DECOMPOSE, and
#: decomposition is the discipline every arc here runs on (rule 2 makes it the
#: id's job, and ``feedback``'s multi-leaf rule makes it how work spans
#: sessions).  It bound on ``recurrence:R7d``, whose reader census turned one
#: step into seven leaves: the file reached 246 against the 240 headroom floor,
#: and **not one SHIPPED row was free to archive** -- every one is cited by a
#: live sentence or is another step's rule 13 blocker, which a 2026-08-19
#: session had already recorded for the four obvious recurrence candidates
#: (``historical/recurrence_completed_findings_span_as_built_2026-08-19.md``).
#: So rule 5 could not answer, exactly as it could not for ``ledger.md``, and
#: the file is 178 table rows against 44 lines of prose -- there is nothing
#: else in it to trim.
#:
#: **What replaces it.**  :data:`~_order.DESCRIPTION_CAP` is the per-ROW cap and
#: is now the whole of rule 4 for this file -- it prevents the failure a line
#: cap was reached for, a row swelling into the arc document's specification.
#: :data:`STEPS_RUNAWAY_ROWS` is the runaway backstop a dropped cap owes.  And
#: what the length was ever a proxy for is graded directly and always was: rule
#: 3's four counts in the header, and rule 14's dense ranks, which keep "the
#: first row that is not done" the answer however long the table grows.  Nobody
#: reads this file end to end; they read row one.
REGISTRY_CAPS = {
    # **RAISED by the developer 2026-09-05 on rule 4's terms**: lessons.md
    # 200 -> 280, conventions.md 280 -> 320.  Each had come to REST on its own
    # headroom floor -- lessons.md at 179-180 since 2026-08-16 after growing
    # 143 -> 180 in five days, conventions.md at 256 of a 260 ceiling -- and a
    # file pinned at its floor reads here exactly like one that has STOPPED
    # growing, so an edit there DISPLACES a rule or a lesson rather than adds
    # one.  Rule 5 answers neither: a rule and a lesson have no span to end.
    "conventions.md": 320,
    "verification.md": 120,
    "lessons.md": 280,
}

#: The number of ``steps.md`` rows that can only be an accident.
#:
#: Not a forcing function -- :data:`REGISTRY_CAPS` no longer holds this file,
#: for the reasons above.  This is the runaway backstop a dropped cap owes: a
#: duplicated table or a generator loop fails loudly instead of committing.
#: Set far above the 157 steps the index held when the cap was dropped, so it
#: can never bind on a real decomposition.
STEPS_RUNAWAY_ROWS = 400

#: The number of ``ledger.md`` rows that can only be an accident.
#:
#: Not a forcing function -- :data:`REGISTRY_CAPS` no longer holds this file,
#: for the reasons above.  This is the runaway backstop a dropped cap owes:
#: a duplicated table or a generator loop fails loudly instead of committing.
#: Set far above the 227 rows the ledger held when the cap was dropped, so it
#: can never bind on a real finding.
LEDGER_RUNAWAY_ROWS = 400

#: The widest a single ``ledger.md`` row may be, in characters.
#:
#: **A row had reached 3,536 characters against a 409-character median.**  At
#: that size it is not an index entry, it is the arc document's argument living
#: in the registry -- which is exactly what rule 5 exists to keep out, and what
#: makes the table unreadable as a table.  ``steps.md`` has held its
#: descriptions to one sentence since rule 14; this is that rule's twin for the
#: ledger, and the ledger went without it for as long as it did because rule 14
#: was written about the index and graded only there.
#:
#: **2,000 is a FIRST FLOOR, deliberately above the p90 when it was set.**  It
#: catches rows that have become specifications without demanding a rewrite of
#: the rows just under it, which would be an unreviewed edit to half the
#: registry.
#:
#: **It does NOT come down as rule 5 archives closed findings, and that sentence
#: stood here until plan step ``balance:X-au-g-2b`` MEASURED it false**
#: (2026-09-01).  Why archiving moves a corpus statistic the wrong way, and what
#: replaced the arm that assumed otherwise, is on
#: :func:`_row_width.crowded_ledger_rows` -- once, where the decision lives.  The two
#: figures this paragraph quoted beside it (a p90 of 1,674, and "the 51 rows
#: over 1,200") were stale by then as well, at 1,686 and 90, and are dropped
#: rather than re-pinned: a measurement quoted as a REASON decays invisibly,
#: because nobody re-checks a premise.
LEDGER_ROW_CAP = 2000


def registry_line_cap_violations() -> list[str]:
    """Rule 4: every registry is under its line cap.

    Returns:
        One message per registry over :data:`REGISTRY_CAPS`.
    """
    problems = []
    for name, cap in sorted(REGISTRY_CAPS.items()):
        path = PLANS / name
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines <= cap:
            continue
        problems.append(
            f"{name} is {lines} lines against rule 4's {cap}-line cap (over by "
            f"{lines - cap}). Archive a COMPLETED span under rule 5 -- closed "
            "findings to their arc's as-built record, shipped steps to theirs. "
            "Do not raise the cap and do not trim a live row."
        )
    return problems


def ledger_runaway_violation() -> "str | None":
    """The backstop a dropped line cap owes.

    Returns:
        The message when ``ledger.md`` holds more rows than any real backlog
        could, or ``None``.
    """
    rows = len(ledger_rows())
    if rows <= LEDGER_RUNAWAY_ROWS:
        return None
    return (
        f"ledger.md holds {rows} rows against the {LEDGER_RUNAWAY_ROWS}-row "
        "runaway backstop. This is not rule 4's forcing function -- that cap "
        "was dropped 2026-08-25 -- it is the arm that says a table this size "
        "is an accident. Check for a duplicated block before doing anything "
        "else."
    )


def steps_runaway_violation() -> "str | None":
    """The backstop ``steps.md``'s dropped line cap owes.

    Returns:
        The message when ``steps.md`` holds more step rows than any real plan
        could, or ``None``.
    """
    rows = len(step_rows())
    if rows <= STEPS_RUNAWAY_ROWS:
        return None
    return (
        f"steps.md holds {rows} steps against the {STEPS_RUNAWAY_ROWS}-row "
        "runaway backstop. This is not rule 4's forcing function -- that cap "
        "was dropped 2026-08-25 -- it is the arm that says a table this size "
        "is an accident. Check for a duplicated block before doing anything "
        "else."
    )


def open_findings_by_arc() -> "list[tuple[str, int]]":
    """Return each arc's open-finding count, largest first.

    **The signal the line cap was standing in for**, published so the backlog
    is looked at rather than bumped into.  A count is REPORTED and never
    fails: what a gate must not do is refuse to record a defect somebody has
    just measured, which is what the dropped cap did twice in three days.

    Returns:
        ``(arc, open rows)`` pairs, largest first, then alphabetical.
    """
    counts: dict[str, int] = {}
    for row in ledger_rows():
        counts[row.arc] = counts.get(row.arc, 0) + 1
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))


def stated_arc_counts_violation() -> "str | None":
    """Rule 3, applied to the BACKLOG the dropped line cap was standing in for.

    **A registry that states its own size has that number CHECKED**, and since
    2026-08-25 ``ledger.md`` states its per-arc split as well as its total.
    That sentence is where the developer's ruling put the forcing function: a
    gate may not refuse to record a measured defect, but the pile it records
    into has to be READ, and a number in the file every reader opens is read
    where a line printed at commit time is not.

    Returns:
        The message when the stated split disagrees with the table, or
        ``None``.
    """
    text = LEDGER.read_text(encoding="utf-8")
    # WHITESPACE-COLLAPSED before matching, because the sentence is ordinary
    # prose that the markdown formatter re-wraps at 100 characters -- and it
    # once wrapped BETWEEN an arc's name and its count, which made a true
    # sentence read as a missing arc.  The parser follows the prose rather than
    # the prose being written to suit the parser.
    sentence = " ".join(text.split("By arc:", 1)[-1].split(".", 1)[0].split())
    stated = {
        arc: int(count)
        for arc, count in re.findall(r"([a-z_]+) (\d+)", sentence)
    } if "By arc:" in text else {}
    actual = dict(open_findings_by_arc())
    if stated == actual:
        return None
    return (
        f"ledger.md's by-arc line says {stated or 'nothing'} and the table "
        f"holds {actual}. The split is the backlog this file states instead of "
        "a line cap (developer ruling 2026-08-25); a stale one hides the pile "
        "it exists to keep in front of a reader (conventions.md rule 3)"
    )


def ledger_row_cap_violations() -> list[str]:
    """Rule 4: no ``ledger.md`` row has grown into a specification.

    Returns:
        One message per row over :data:`LEDGER_ROW_CAP`.
    """
    problems = []
    for row in ledger_rows():
        width = row.width
        if width <= LEDGER_ROW_CAP:
            continue
        problems.append(
            f"{row.key} is {width} characters against rule 4's "
            f"{LEDGER_ROW_CAP}-character row cap (over by "
            f"{width - LEDGER_ROW_CAP}). A ledger row is an INDEX entry: the "
            "defect, what it costs, what is ruled, who owns it. The narrative "
            "of how it was found, what each review said and what was tried "
            "belongs in the owning step's specification, where the person who "
            "picks that step up will read it."
        )
    return problems
