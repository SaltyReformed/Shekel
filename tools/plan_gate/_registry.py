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
from dataclasses import dataclass
from pathlib import Path

from _classes import decomposition_leaf_keys
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

#: Split a markdown row on pipes that are not backslash-escaped.  A cell may
#: carry a literal ``\|`` (the balance ledger's N-73 row holds
#: ``Decimal \| None``), and a naive ``split("|")`` reads that row as having an
#: extra cell and reports the table broken when it is not.
UNESCAPED_PIPE_RX = re.compile(r"(?<!\\)\|")

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

#: An ``order`` cell placing a step in the sequence: ``#12``.  The three legal
#: spellings of that column are this, ``container`` and ``SHIPPED``; anything
#: else is a row a reader cannot place.
ORDER_CELL_RX = re.compile(r"^#(?P<rank>\d+)$")

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
}


@dataclass(frozen=True)
class LedgerRow:
    """One finding, as ``ledger.md`` states it."""

    arc: str
    ident: str
    also: str
    finding: str
    worst: str
    status: str
    owner: str

    @property
    def key(self) -> str:
        """The row's real primary key: the arc AND the id, never the id alone."""
        return f"{self.arc}:{self.ident}"

    @property
    def width(self) -> int:
        """The row's total character width, which rule 4's per-row cap grades.

        A property on the row rather than a sum at each call site, so the arm
        and its controls cannot disagree about what a row's size even is --
        the same reason ``COMMIT_SHA`` is shared rather than re-spelled.
        """
        return sum(len(cell) for cell in (
            self.arc, self.ident, self.also, self.finding,
            self.worst, self.status, self.owner,
        ))


@dataclass(frozen=True)
class StepRow:
    """One step, as ``steps.md`` indexes it."""

    arc: str
    ident: str
    aliases: str
    title: str
    state: str
    commit: str
    blocked: str

    @property
    def key(self) -> str:
        """The step's real primary key: the arc AND the id, never the id alone."""
        return f"{self.arc}:{self.ident}"

    @property
    def shipped(self) -> bool:
        """Whether this step has landed, which is what rule 2 turns on."""
        return self.state == "SHIPPED"

    def alias_keys(self) -> list[str]:
        """The ``arc:id`` keys this step is also known by."""
        return _key_list(self.aliases)

    @property
    def is_decomposed_parent(self) -> bool:
        """Whether this row DECLARES itself the parent of a decomposition.

        **Declared, never derived, and that is the whole design.**  Rule 2 puts
        a decomposition in the id, so deriving the relation by longest id
        prefix is the obvious implementation -- and it is wrong on this corpus:
        ``R-F1`` is a string prefix of ``R-F10``, ``R-F12`` and ``R-F13``,
        which are unrelated findings-steps, and ``R-F1`` is SHIPPED while all
        three are open.  A derived arm would have reported three false
        failures on its first run.

        Consulting only DECLARED parents removes that class by construction
        rather than by an exception list -- which is finding **N-147**'s defect
        (a rule enforced by a list of names that must be kept complete) and is
        exactly what Phase G exists to delete.
        """
        return "decomposed parent" in self.title.casefold()

    @property
    def rank(self) -> int | None:
        """This step's place in the execution order, or ``None``.

        ``None`` for a container and for a shipped step, which is the whole
        point of the column: neither is a thing a reader can pick up, so
        neither carries a position in the sequence.
        """
        match = ORDER_CELL_RX.match(self.state)
        return int(match.group("rank")) if match else None

    @property
    def is_container(self) -> bool:
        """Whether the ``order`` cell declares this row a grouping."""
        return self.state == "container"

    def blocked_keys(self) -> list[str]:
        """The ``arc:id`` keys this step may not ship before (rule 13).

        The same grammar as :meth:`alias_keys`, through the same function: both
        cells carry a ``/``-separated list of annotated step keys, and a second
        copy of that grammar is the denormalization these registries exist to
        remove.  The annotation is real and load-bearing -- ``CC3b`` carries
        ``balance:X-f1 (shipped; absorbed the X-f1b leaf this once named)`` --
        so the key is parsed OUT of the entry rather than the entry being read
        as a key.
        """
        return _key_list(self.blocked)


@dataclass(frozen=True)
class Fork:
    """Two steps in different arcs that are COMPETING remedies for one defect."""

    defect: str
    remedies: str
    ruled: str

    @property
    def is_ruled(self) -> bool:
        """Whether the developer has decided which remedy wins.

        **A fork is ruled only when the cell NAMES one of its own remedies.**
        The test used to be ``"NOT YET RULED" not in ruled and ruled.strip()``,
        which read the exact house spelling as unruled and every other way of
        saying the same thing as RULED: an adversarial review measured ``TBD``,
        ``pending``, ``?`` and even lowercase ``not yet ruled`` all returning
        True, and a True here makes :func:`fork_violations` skip the fork
        entirely -- so both competing remedies become tickable.  That is rule
        11, the predicate that exists BECAUSE ``P3`` / ``N-123`` went unnoticed
        from April to 2026-08-09.

        Naming a remedy is also the only spelling a later reader can act on,
        and it is what :func:`Fork.winner` needs to check the defect row was
        re-pointed.
        """
        return self.winner is not None

    @property
    def winner(self) -> str | None:
        """The ``arc:id`` of the remedy that won, or ``None`` while unruled."""
        for key in self.remedy_keys():
            if key in self.ruled:
                return key
        return None

    def remedy_keys(self) -> list[str]:
        """Every ``arc:id`` named as a competing remedy."""
        return re.findall(r"\b([a-z_]+:[A-Za-z0-9][A-Za-z0-9-]*)", self.remedies)

    def defect_keys(self) -> list[str]:
        """Every ``arc:id`` the defect cell names.

        More than one where the same defect was recorded in two arcs' ledgers
        (``pay_calendar:P3 = balance:N-123``).  After those rows are merged only
        ONE of them is still live, which is why the predicate asks for at least
        one live row rather than for all of them.
        """
        return re.findall(r"\b([a-z_]+:[A-Za-z0-9][A-Za-z0-9-]*)", self.defect)


def _key_list(cell: str) -> list[str]:
    """Return the ``arc:id`` keys a ``/``-separated step-key cell names.

    Shared by :meth:`StepRow.alias_keys` and :meth:`StepRow.blocked_keys`,
    which is the whole point: ``aliases`` and ``blocked by`` carry the SAME
    shape -- a list of step keys, each optionally annotated in parentheses --
    and rule 13 grades the second against the first's identity classes, so the
    two cells being read by two grammars would let a class agree about its
    names and disagree about what parsed as one.

    An entry with no ``:`` is dropped rather than reported, because ``--`` and
    prose asides are both legal in these cells and neither names a step.

    Args:
        cell: The raw cell text, ``--`` for none.

    Returns:
        The ``arc:id`` keys, in the order the cell states them.
    """
    if cell.strip() == "--":
        return []
    out = []
    for part in cell.split(" / "):
        token = part.split(" (")[0].strip()
        if ":" in token:
            out.append(token)
    return out


def _rows(text: str, columns: int) -> list[list[str]]:
    """Return every ``columns``-cell body row of every table in *text*."""
    out = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in UNESCAPED_PIPE_RX.split(line)[1:-1]]
        if len(cells) != columns:
            continue
        if not cells[0] or set(cells[0]) <= {"-", ":"}:
            continue
        if cells[0] in ("arc", "defect", "file", "id"):
            continue
        out.append(cells)
    return out


def ledger_rows() -> list[LedgerRow]:
    """Every finding in ``ledger.md``."""
    return [LedgerRow(*cells) for cells in _rows(LEDGER.read_text(), 7)]


def step_rows() -> list[StepRow]:
    """Every step in ``steps.md``.

    The forks table in the same file has three columns, so the column count
    separates them without needing to locate either heading.
    """
    return [StepRow(*cells) for cells in _rows(STEPS.read_text(), 7)]


def forks() -> list[Fork]:
    """Every unruled-fork row in ``steps.md``."""
    return [Fork(*cells) for cells in _rows(STEPS.read_text(), 3)]


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
    Deriving both would claim ``R-F1`` as the parent of ``R-F10`` / ``R-F12`` /
    ``R-F13``; deriving neither would need a name list, which is N-147's defect.
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
REGISTRY_CAPS = {
    "ledger.md": 240,
    "steps.md": 260,
    "conventions.md": 280,
    "verification.md": 120,
    "lessons.md": 200,
}

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
#: **2,000 is a FIRST FLOOR, deliberately above the p90 of 1,674.**  It is set
#: where it catches rows that have become specifications without demanding a
#: rewrite of the 51 rows over 1,200, which would be an unreviewed edit to half
#: the registry.  It comes DOWN as rule 5 archives closed findings, the way the
#: arc-document caps came down when the registries left.
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
