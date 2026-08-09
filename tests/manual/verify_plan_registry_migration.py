"""Prove the registry migration lost nothing.

**This parser is deliberately DIFFERENT from the one the migration used.** The
migration found a ledger by its SECTION HEADING and walked forward; this finds
it by its COLUMN HEADER row and walks the contiguous table.  Sharing a parser
would make the two sides two producers proving each other -- the exact failure
the balance arc's verification standard names, and the reason its harnesses are
asked "can this SEE the code under test?".  It is also independent of
``tools/plan_gate/_registry.py``, which grades the registries' CONTENT and would
report a clean corpus whatever the originals had said.

Run with ``--control`` to plant a one-character mutation in each generated file
and require this script to REPORT it.  A guard whose control does not fire is
not a guard.

**It reads the originals from the PRE-MIGRATION COMMIT**, so it stays runnable
forever rather than being a one-shot that stopped working the moment the arc
documents were stripped.  Kept beside ``verify_balance_baseline.py`` and
``verify_pay_calendar_derivation.py`` for the same reason all three exist: a
migration that cannot be re-verified by the next reader is a claim, not a
proof.

    python tests/manual/verify_plan_registry_migration.py --control
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLANS = ROOT / "docs" / "plans"

#: The originals are read from the PRE-MIGRATION COMMIT, not from the working
#: tree, so this proof still runs after the arc documents are stripped.  A
#: one-shot check that stops being runnable the moment the migration lands
#: cannot be re-verified by the next reader, which is the standard this
#: project holds every other gate to.
BASE_REF = "345996f0"


def original(rel: str) -> str:
    """Return *rel* as it stood at the pre-migration commit."""
    return subprocess.run(
        ["git", "show", f"{BASE_REF}:{rel}"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout

ORIGINALS = {
    "balance": "docs/audits/balance_architecture/README.md",
    "recurrence": "docs/plans/implementation_plan_recurrence_redesign.md",
    "pay_calendar": "docs/plans/implementation_plan_pay_calendar.md",
    "credit_card": "docs/plans/implementation_plan_credit_card.md",
}

#: Rows MERGED after the migration, as ``{absorbed: survivor}``.
#:
#: The migration itself made NONE: two rows worded differently cannot be
#: combined mechanically, so each of these is a reviewed semantic edit made
#: after the row TEXT and the OWNER column were compared side by side.
#:
#: **Declaring them here is what keeps this a proof.**  To a byte-comparison a
#: merge and a silent loss are the same event -- a row that was there is not --
#: so an undeclared merge would either turn this script red forever, or, if the
#: comparison were relaxed to tolerate an absence, make it blind to the very
#: loss it exists to catch.  For a DECLARED merge three things are still
#: required: the absorbed row is GONE, its survivor is PRESENT, and the
#: survivor still CITES the absorbed key -- so a commit message, code comment
#: or as-built record naming the old id still resolves to a live row.
#:
#: The survivor is never a free choice.  ``_registry.owner_violations``
#: resolves an owner WITHIN the row's arc, so the merged row must sit in the
#: arc whose step closes it.
MERGES = {
    ("recurrence", "F-12"): ("pay_calendar", "P6"),
    ("recurrence", "F-10"): ("pay_calendar", "P2"),
    ("balance", "N-123"): ("pay_calendar", "P3"),
}
LEDGER_HEADER = "| id | finding (one line) | worst measured | status | owned by |"
LEDGER_HEADER_ALT = "| id | finding (one line) | worst measured | status | closed by |"
CHECKBOX_RX = re.compile(
    r"^\s*[-*]\s*\[(?P<tick>[ xX])\]\s*\*\*(?P<step>[A-Za-z0-9][A-Za-z0-9-]*)\b",
)
PIPE_RX = re.compile(r"(?<!\\)\|")


def split_row(line: str) -> list[str]:
    """Split a table row on pipes that are not backslash-escaped."""
    return [c.strip() for c in PIPE_RX.split(line)[1:-1]]


def ledger_by_header(text: str) -> list[list[str]]:
    """Find the ledger by its COLUMN HEADER and walk the contiguous table."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() in (LEDGER_HEADER, LEDGER_HEADER_ALT):
            rows = []
            for candidate in lines[i + 2:]:
                if not candidate.strip().startswith("|"):
                    break
                cells = split_row(candidate)
                if len(cells) == 5 and not set(cells[0]) <= {"-", ":"}:
                    rows.append(cells)
            return rows
    return []


def all_checkboxes(text: str) -> list[tuple[str, bool, str]]:
    """Every checkbox in the WHOLE document, fenced regions removed."""
    out, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = CHECKBOX_RX.match(line)
        if match:
            first = re.sub(r"^\s*[-*]\s*\[[ xX]\]\s*", "", line).strip()
            out.append((match.group("step"), match.group("tick").lower() == "x", first))
    return out


def unescape(text: str) -> str:
    """Undo the escaping a cell needs to carry a literal pipe."""
    return text.replace("\\|", "|")


def _registry_rows(path, columns: int) -> list[list[str]]:
    """Return every ``columns``-cell body row of the registry at *path*."""
    rows = []
    for line in path.read_text().splitlines():
        if not line.startswith("| "):
            continue
        cells = split_row(line)
        if len(cells) != columns or cells[0] == "arc" or set(cells[0]) <= {"-", ":"}:
            continue
        rows.append(cells)
    return rows


def merge_problems(rows: dict[tuple[str, str], list[str]]) -> list[str]:
    """Check every DECLARED merge actually happened and orphaned no citation.

    Args:
        rows: Every ``ledger.md`` row, keyed ``(arc, id)``.

    Returns:
        One message per merge that was not made, lost its survivor, or dropped
        the absorbed id from the survivor's ``also`` column.
    """
    problems = []
    for absorbed, survivor in sorted(MERGES.items()):
        absorbed_key = f"{absorbed[0]}:{absorbed[1]}"
        if absorbed in rows:
            problems.append(
                f"MERGE NOT MADE: {absorbed_key} is still its own row",
            )
        if survivor not in rows:
            problems.append(
                f"MERGE LOST ITS SURVIVOR: {absorbed_key} was merged into "
                f"{survivor[0]}:{survivor[1]}, which is not in ledger.md",
            )
        elif absorbed_key not in " | ".join(rows[survivor]):
            problems.append(
                f"MERGE ORPHANED A CITATION: {survivor[0]}:{survivor[1]} no "
                f"longer names {absorbed_key}, so every commit message and "
                f"as-built record citing that id resolves to nothing",
            )
    return problems


def ledger_problems() -> list[str]:
    """Compare every original ledger row against ``ledger.md``, cell by cell."""
    was = {
        (arc, tuple(row)[0]): tuple(row)
        for arc, rel in ORIGINALS.items()
        for row in ledger_by_header(original(rel))
    }
    rows = {
        (cells[0], unescape(cells[1])): cells
        for cells in _registry_rows(PLANS / "ledger.md", 7)
    }
    now = {
        key: (unescape(cells[1]), cells[3], cells[4], cells[5], cells[6])
        for key, cells in rows.items()
    }
    problems = merge_problems(rows)
    if len(was) - len(MERGES) != len(now):
        problems.append(
            f"LEDGER COUNT: {len(was)} rows in the originals and "
            f"{len(MERGES)} declared merges, but {len(now)} in ledger.md",
        )
    # An absorbed row is EXPECTED to be absent; anything else absent is a loss.
    problems += [
        f"LEDGER DROPPED: {a}:{i}"
        for a, i in sorted(set(was) - set(now) - set(MERGES))
    ]
    problems += [f"LEDGER INVENTED: {a}:{i}" for a, i in sorted(set(now) - set(was))]
    fields = ("id", "finding", "worst measured", "status", "owner")
    survivors = set(MERGES.values())
    for key in sorted(set(was) & set(now)):
        if key in survivors:
            # A survivor's cells are two rows combined, so byte-identity is
            # deliberately broken.  What still holds for it is checked above:
            # it exists, and it names the id it absorbed.
            continue
        for field, before, after in zip(fields, was[key], now[key]):
            if before != after:
                problems.append(
                    f"LEDGER ALTERED {key[0]}:{key[1]} [{field}]\n"
                    f"      was: {before[:150]}\n      now: {after[:150]}",
                )
    return problems


def step_problems() -> list[str]:
    """Compare every original checkbox against the ``steps.md`` index."""
    was = {
        (arc, sid): (ticked, first)
        for arc, rel in ORIGINALS.items()
        for sid, ticked, first in all_checkboxes(original(rel))
    }
    now = {
        (cells[0], cells[1]): (cells[4] == "SHIPPED", unescape(cells[3]))
        for cells in _registry_rows(PLANS / "steps.md", 7)
    }
    problems = [f"STEP DROPPED: {a}:{i}" for a, i in sorted(set(was) - set(now))]
    problems += [f"STEP INVENTED: {a}:{i}" for a, i in sorted(set(now) - set(was))]
    for key in sorted(set(was) & set(now)):
        (was_tick, was_title), (now_tick, now_title) = was[key], now[key]
        if was_tick != now_tick:
            problems.append(
                f"STEP STATE CHANGED {key[0]}:{key[1]}: "
                f"ticked={was_tick} -> SHIPPED={now_tick}",
            )
        if was_title != now_title:
            problems.append(
                f"STEP TITLE ALTERED {key[0]}:{key[1]}\n"
                f"      was: {was_title[:150]}\n      now: {now_title[:150]}",
            )
    return problems


def stated_count_problems() -> list[str]:
    """The ledger states its own size, and the number is checked."""
    text = (PLANS / "ledger.md").read_text()
    stated = re.search(r"\*\*The ledger stands at (\d+) rows?\.?\*\*", text)
    actual = len(_registry_rows(PLANS / "ledger.md", 7))
    if not stated:
        return ["LEDGER states no row count"]
    if int(stated.group(1)) != actual:
        return [f"LEDGER stated count {stated.group(1)} != {actual} actual rows"]
    return []


def check() -> list[str]:
    """Every way the migration could have lost something, in one list."""
    return ledger_problems() + step_problems() + stated_count_problems()


def main() -> int:
    """Report the verdict, and with ``--control`` prove the check can fail."""
    if "--control" in sys.argv:
        print("NEGATIVE CONTROL: planting one mutation in each generated file\n")
        for name, old, new, needle in (
            ("ledger.md", "| balance | FU-3 |", "| balance | FU-9 |", "FU-"),
            ("steps.md", "| pay_calendar | C1 |", "| pay_calendar | C9 |", "C9"),
            # The merge arm needs its own control: the two checks above compare
            # ids, and a merge is the one edit that legitimately removes one.
            # Dropping the absorbed id from the survivor's ``also`` column is
            # how a merge silently orphans every citation of the old id.
            ("ledger.md", "| pay_calendar | P6 | = recurrence:F-12 |",
             "| pay_calendar | P6 | -- |", "ORPHANED"),
        ):
            path = PLANS / name
            backup = path.read_text()
            assert old in backup, f"control anchor missing in {name}"
            path.write_text(backup.replace(old, new, 1))
            fired = [p for p in check() if needle in p or "C1" in p]
            path.write_text(backup)
            verdict = "FIRED" if fired else "*** DID NOT FIRE ***"
            print(f"  {name:11} mutate {old.strip()[:46]} -> ...: {verdict}")
            for line in fired[:2]:
                print(f"      {line.splitlines()[0]}")
        print()

    problems = check()
    if problems:
        print(f"LOSSLESSNESS: FAILED -- {len(problems)} problem(s)\n")
        for problem in problems[:40]:
            print(f"  {problem}")
        return 1
    print("LOSSLESSNESS: PASSED")
    print("  every ledger row in all four originals appears in ledger.md with")
    print("  byte-identical id / finding / worst-measured / status / owner cells,")
    print(f"  EXCEPT the {len(MERGES)} declared merges below, whose survivors are two")
    print("  rows combined and are therefore checked differently: present, and")
    print("  still citing the id they absorbed")
    for absorbed, survivor in sorted(MERGES.items()):
        print(f"    {absorbed[0]}:{absorbed[1]:7} -> {survivor[0]}:{survivor[1]}")
    print("  every checkbox in all four originals appears in steps.md with its")
    print("  tick state and its first line unchanged")
    print("  no row invented, none dropped, stated count agrees with the table")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
