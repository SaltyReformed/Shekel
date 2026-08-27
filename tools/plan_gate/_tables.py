"""The GRAMMAR of a planning-registry table: its rows, cells and headers.

Split out of :mod:`_registry` on 2026-08-14, when the header-anchored reader
(finding **N-234**) took that module past pylint's 1,000-line ceiling.  This
project's ruling on an over-ceiling module is that it SPLITS rather than being
shaved a line at a time (findings **N-152** / **N-156** / **N-201**), and
``_staging.py`` is the same move on the control side.

The seam is I/O: everything here is PURE -- it takes text and returns rows, and
names no path.  Reading a document, and therefore every path a control
monkeypatches, stays in :mod:`_registry`, so a staged copy is still what its
predicates see.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Split a markdown row on pipes that are not backslash-escaped.  A cell may
#: carry a literal ``\|`` (the balance ledger's N-73 row holds
#: ``Decimal \| None``), and a naive ``split("|")`` reads that row as having an
#: extra cell and reports the table broken when it is not.
UNESCAPED_PIPE_RX = re.compile(r"(?<!\\)\|")

#: Where a registry id ENDS and its provenance annotation begins.  The same
#: ``id (annotation)`` grammar the owner and blocker columns use, applied to the
#: id cell itself -- see :attr:`LedgerRow.bare_ident` and finding **N-254**.
_IDENT_ANNOTATION_RX = re.compile(r"\s*\(")

#: The header row that identifies each parsed table, spelled exactly as the
#: document writes it.  :func:`rows_under` locates a table by this rather than
#: by its column count -- see that function for finding **N-234**, the defect
#: the width-keyed reader produced on its first encounter with a new table.
LEDGER_HEADER = (
    "arc", "id", "also", "finding (one line)", "worst measured", "status",
    "owner",
)
STEPS_HEADER = (
    "arc", "id", "also", "what this step does", "order", "commit", "starts",
)
FORKS_HEADER = ("defect", "competing remedies", "ruled")
RULINGS_HEADER = ("arc", "id", "also", "date", "what was ruled")

#: An ``order`` cell placing a step in the sequence: ``#12``.  The three legal
#: spellings of that column are this, ``container`` and ``SHIPPED``; anything
#: else is a row a reader cannot place.
ORDER_CELL_RX = re.compile(r"^#(?P<rank>\d+)$")


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
    def bare_ident(self) -> str:
        """The id ALONE, with its provenance annotation stripped.

        A finding is born with its provenance in the same cell -- the same
        ``id (annotation)`` grammar rules 1 and 13 give the owner and blocker
        columns.  Reading the annotation as PART of the key is finding
        **N-254**: four ids each named two findings (``N-244``..``N-247``, two
        sessions on successive days) and the uniqueness arm reported none,
        because the two cells differ in their parentheses.

        It broke the ``also`` relation the other way too: a ``~ balance:N-191``
        reference is written bare and could never match an annotated row's key.
        Nothing had referenced an annotated row yet.
        """
        return _IDENT_ANNOTATION_RX.split(self.ident, maxsplit=1)[0].strip()

    @property
    def key(self) -> str:
        """The row's real primary key: the arc AND the id, never the id alone."""
        return f"{self.arc}:{self.bare_ident}"

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
    def bare_ident(self) -> str:
        """The id ALONE -- the same rule :attr:`LedgerRow.bare_ident` states.

        No step id carries an annotation today.  It is read the same way anyway,
        because a key that means one thing in one registry and another in its
        sibling is how a rule stated for one artifact comes to be graded on one
        artifact (conventions.md rule 3's own sentence).
        """
        return _IDENT_ANNOTATION_RX.split(self.ident, maxsplit=1)[0].strip()

    @property
    def key(self) -> str:
        """The step's real primary key: the arc AND the id, never the id alone."""
        return f"{self.arc}:{self.bare_ident}"

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
        ``pay_calendar:C1`` is SHIPPED and is a string prefix of ``C10``,
        ``C11`` and ``C12``, three unrelated OPEN steps, so a derived arm
        reports three false failures.  **The worked example was the recurrence
        arc's ``R-F1`` against ``R-F10`` / ``R-F12`` / ``R-F13`` until
        2026-08-20**, and it went stale in both directions -- R-F13 is not in
        the index at all and the other two have SHIPPED -- which is why
        :func:`_staging.a_prefix_trap` now derives the specimen instead of
        three docstrings naming one.

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
class RulingRow:
    """One developer ruling, as ``rulings.md`` states it.

    **The arc is a CELL and the key is the pair**, which is the whole reason
    this registry exists.  Ruling ids came from one global sequence spelled
    across five arc documents, none of them parsed, so two arcs taking one id
    was invisible to every gate -- finding **N-367**, three instances, the
    third on 2026-08-27 when all three minting sessions had checked and a
    ``docs/`` grep could not see an unmerged branch.  Holding the pair here
    makes the collision LEGAL and gradeable rather than silent: what a shared
    id costs is a bare citation resolving to two rules, which is a property of
    the CITATION and not of the ruling.
    """

    arc: str
    ident: str
    also: str
    date: str
    rule: str

    @property
    def bare_ident(self) -> str:
        """The id ALONE, with any provenance annotation stripped.

        The same ``id (annotation)`` grammar :attr:`LedgerRow.bare_ident`
        reads, through the same expression, because a key that means one thing
        in one registry and another in its sibling is how a rule stated for one
        artifact comes to be graded on one artifact (conventions.md rule 3).
        """
        return _IDENT_ANNOTATION_RX.split(self.ident, maxsplit=1)[0].strip()

    @property
    def key(self) -> str:
        """The ruling's real primary key: the arc AND the id, never the id."""
        return f"{self.arc}:{self.bare_ident}"

    def also_keys(self) -> list[str]:
        """The keys this ruling is ALSO recorded under, arc-qualified here.

        The cell carries BARE ids rather than ``arc:id`` keys, and that is not
        an inconsistency with :meth:`StepRow.alias_keys`: a step aliases ACROSS
        arcs (``pay_calendar:C2`` is ``balance:X-l``) and a ruling never does,
        so the arc is redundant in every cell it could appear in.  It is added
        back here so a caller compares keys with keys.

        TWO cells use it today, and both are one rule taken on two days:
        ``R-L`` / ``R-Y`` and ``R-T`` / ``R-X``.

        **``R-FA`` is deliberately NOT a third**, and an adversarial review is
        why.  Its cell held only the parenthetical naming the ``R-EX`` that
        commit ``daa9c402`` recorded it as -- which parsed to nothing, so the
        arm read no alias while the docstring claimed one.  Spelling it in
        this grammar instead makes the gate RED and correctly so:
        ``balance:R-EX`` is separately a LIVE ruling (``X-ad``'s), so claiming
        ``R-FA`` is "also known as R-EX" would assert that a citation of
        ``R-EX`` resolves here -- which is **N-217**'s defect, not its remedy.
        That provenance is a fact about a commit, so it lives in the rule
        text, and this column carries only aliases that RESOLVE.
        """
        if self.also.strip() == "--":
            return []
        out = []
        for part in self.also.split(" / "):
            token = _IDENT_ANNOTATION_RX.split(part, maxsplit=1)[0].strip()
            if token:
                out.append(f"{self.arc}:{token}")
        return out

    @property
    def width(self) -> int:
        """The row's total character width, which rule 4's per-row cap grades.

        Read by :func:`_rulings.row_width_violations` against
        :data:`_rulings.RULINGS_ROW_CAP`.  It shipped unread for one draft --
        the property written and no arm behind it -- which is the half of the
        2026-08-25 cap precedent this registry had not brought across.
        """
        return sum(len(cell) for cell in (
            self.arc, self.ident, self.also, self.date, self.rule,
        ))


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


def cells(line: str) -> list[str] | None:
    """Return a markdown row's cells, or ``None`` when *line* is not a row.

    Args:
        line: One line of a document.

    Returns:
        The stripped cells between the outer pipes, or ``None``.
    """
    if not line.strip().startswith("|"):
        return None
    return [c.strip() for c in UNESCAPED_PIPE_RX.split(line)[1:-1]]


def rows_under(text: str, header: tuple[str, ...]) -> list[list[str]]:
    """Return every body row of every table in *text* whose header is *header*.

    **A table is located by its own HEADER, not by its column count** (finding
    **N-234**).  Splitting by WIDTH made ``forks()`` take every three-column row
    in ``steps.md``, so a table added there for any other purpose silently joined
    whichever registry matched its shape -- a ``| arc | document | section |``
    reference table read as four unruled forks and turned four controls red.  A
    header is what a table IS: same header, same kind of row (``steps.md``'s
    order, containers and shipped sections are one registry in three), and a
    different header is a different subject whatever its width.

    A row inside a matched table whose cell count differs is DROPPED, unchanged:
    that is how an unescaped ``|`` surfaces, and rule 3's count arm reports it.

    Args:
        text: The whole document.
        header: The header row's cells, verbatim.

    Returns:
        Every body row, as a list of cells.

    Raises:
        AssertionError: When no table carries *header*.  A missing table is not
            an empty one -- a restructured document would empty a registry and
            every predicate over it would pass.
    """
    out: list[list[str]] = []
    found = False
    in_table = False
    for line in text.splitlines():
        row = cells(line)
        if row is None:
            in_table = False
            continue
        if tuple(row) == header:
            found = True
            in_table = True
            continue
        if not in_table:
            continue
        if not row[0] or set(row[0]) <= {"-", ":"}:
            continue
        if len(row) != len(header):
            continue
        out.append(row)
    assert found, (
        f"no table with the header {' | '.join(header)!r} -- the document was "
        f"restructured and this registry would otherwise read as EMPTY, which "
        f"every predicate over it would pass (conventions.md rule 3)"
    )
    return out
