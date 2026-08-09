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

The owner grammar is NOT re-implemented: :func:`_plan_gate.split_owners` and
``OWNER_RX`` carry five false-positive fixes each measured against a real
ledger, and a second copy of that grammar would be the very defect this
restructure removes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from _plan_gate import (
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

#: One arc document, by the slug its registry rows carry.
ARC_DOCS = {
    "balance": REPO / "docs/audits/balance_architecture/README.md",
    "recurrence": PLANS / "implementation_plan_recurrence_redesign.md",
    "pay_calendar": PLANS / "implementation_plan_pay_calendar.md",
    "credit_card": PLANS / "implementation_plan_credit_card.md",
}

#: Heading prefix bounding each arc document's steps section.  ``None`` means
#: the section runs to the next ``##`` heading of any name.
ARC_STEPS_HEADING = {
    "balance": "## 5.",
    "recurrence": "## 4.",
    "pay_calendar": "## 4.",
    "credit_card": "## The steps",
}

CHECKBOX_RX = re.compile(
    r"^\s*[-*]\s*\[(?P<tick>[ xX])\]\s*\*\*(?P<step>[A-Za-z0-9][A-Za-z0-9-]*)\b",
)


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
        if self.aliases.strip() == "--":
            return []
        out = []
        for part in self.aliases.split(" / "):
            token = part.split(" (")[0].strip()
            if ":" in token:
                out.append(token)
        return out


@dataclass(frozen=True)
class Fork:
    """Two steps in different arcs that are COMPETING remedies for one defect."""

    defect: str
    remedies: str
    ruled: str

    @property
    def is_ruled(self) -> bool:
        """Whether the developer has decided which remedy wins.

        An EMPTY cell counts as unruled, not as ruled: the failure mode this
        guards is a fork nobody has answered, and a blank is exactly what that
        looks like.
        """
        return "NOT YET RULED" not in self.ruled and bool(self.ruled.strip())

    def remedy_keys(self) -> list[str]:
        """Every ``arc:id`` named as a competing remedy."""
        return re.findall(r"\b([a-z_]+:[A-Za-z0-9][A-Za-z0-9-]*)", self.remedies)


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
    """Rule 10: ``(arc, id)`` is unique across the corpus."""
    problems: list[str] = []
    for label, keys in (
        ("ledger.md", [row.key for row in ledger_rows()]),
        ("steps.md", [row.key for row in step_rows()]),
    ):
        seen: set[str] = set()
        for key in keys:
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
    """Rule 11, second half: an UNRULED fork refuses a tick on either remedy."""
    steps = {row.key: row for row in step_rows()}
    problems: list[str] = []
    for fork in forks():
        if fork.is_ruled:
            continue
        for key in fork.remedy_keys():
            step = steps.get(key)
            if step is None:
                problems.append(
                    f"fork {fork.defect!r}: remedy {key!r} names no step in "
                    f"steps.md (conventions.md rule 11)",
                )
            elif step.shipped:
                problems.append(
                    f"fork {fork.defect!r} is NOT YET RULED but its remedy {key} "
                    f"is already SHIPPED.  Whichever ships first decides for both "
                    f"arcs, so it may not ship before the ruling is recorded "
                    f"(conventions.md rule 11)",
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
