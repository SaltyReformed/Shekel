"""Prove the registry migration lost nothing, and that the merges after it did not.

**This parser is deliberately DIFFERENT from the one the migration used.** The
migration found a ledger by its SECTION HEADING and walked forward; this finds
it by its COLUMN HEADER row and walks the contiguous table.  Sharing a parser
would make the two sides two producers proving each other -- the exact failure
the balance arc's verification standard names, and the reason its harnesses are
asked "can this SEE the code under test?".  It is also independent of
``tools/plan_gate/_registry.py``, which grades the registries' CONTENT and would
report a clean corpus whatever the originals had said.

Run with ``--control`` to plant a mutation against each arm and require this
script to REPORT it.  A guard whose control does not fire is not a guard.

    python tests/manual/verify_plan_registry_migration.py --control

**Two separate proofs live here, and keeping them separate is the point.**

*Proof A -- the migration.*  It compares the four originals at ``BASE_REF``
against the registries at ``MIGRATION_REF``.  **Both sides are immutable git
objects**, so this is a permanent, re-verifiable fact about one commit and it
admits NO exemption: every row must match byte for byte.  It read the LIVE
registry files until an adversarial review showed what that cost -- the live
ledger is a WORKING document, so every ordinary later edit to a row would have
turned this red, and the only way to keep it green was to exempt rows from the
comparison.  Exemptions are what a proof cannot have.  A proof about a commit
does not need them, because a commit never changes.

*Proof B -- the merges made after the migration.*  Two rows worded differently
cannot be combined mechanically, so a merge is a reviewed semantic edit and its
losslessness is not a byte comparison.  What IS mechanical is that the merge
kept the FACTS: every dollar figure, ``file.py:line`` citation and ISO date in
EITHER original row must still appear in the live survivor.

**Proof B's map can only ADD requirements, never remove one**, and that is the
whole difference from the design it replaced.  There, a declared merge exempted
its survivor from comparison, so adding one line to the map could silence a real
row loss -- a reviewer demonstrated exactly that.  Here a merge that is not
declared is caught by nothing extra, and a merge that IS declared must carry
every fact from both sides.  Declaring one cannot make the script quieter.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLANS = ROOT / "docs" / "plans"

#: The four arc documents as they stood BEFORE the migration.
BASE_REF = "345996f0"

#: The commit that built the registries.  Proof A compares two fixed commits,
#: so it stays true and re-runnable however far the live documents move on.
MIGRATION_REF = "6eeae53d"


def at(ref: str, rel: str) -> str:
    """Return *rel* as it stood at *ref*.

    Args:
        ref: Any git revision.
        rel: A repository-relative path.

    Returns:
        The file's contents at that revision.
    """
    return subprocess.run(
        ["git", "show", f"{ref}:{rel}"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout


ORIGINALS = {
    "balance": "docs/audits/balance_architecture/README.md",
    "recurrence": "docs/plans/implementation_plan_recurrence_redesign.md",
    "pay_calendar": "docs/plans/implementation_plan_pay_calendar.md",
    "credit_card": "docs/plans/implementation_plan_credit_card.md",
}
LEDGER_REL = "docs/plans/ledger.md"
STEPS_REL = "docs/plans/steps.md"

#: Rows merged AFTER the migration, as ``{absorbed: survivor}``.  Proof B holds
#: each to fact-carry-forward from BOTH originals.  The survivor is not a free
#: choice: ``_registry.owner_violations`` resolves an owner WITHIN the row's
#: arc, so a merged row sits in the arc whose step closes it -- which is why
#: ``pay_calendar:P3`` folded INTO ``balance:N-123`` once the developer ruled
#: that fork for ``balance:X-ad`` on 2026-08-09, and not before.
MERGES = {
    ("recurrence", "F-12"): ("pay_calendar", "P6"),
    ("recurrence", "F-10"): ("pay_calendar", "P2"),
    ("pay_calendar", "P3"): ("balance", "N-123"),
}

#: What a merge may not lose.  Deliberately NOT "every backticked token": a
#: merge rewords prose, and a check that forbids rewording forbids merging.
#: These three are the claims a row is CITED for -- an amount, a place in the
#: code, and a date -- and none of them has a synonym.
FACT_RX = re.compile(r"\$-?[\d,]+\.\d{2}|[\w/.]+\.py:\d+|\d{4}-\d{2}-\d{2}")

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


def _registry_rows(text: str, columns: int) -> list[list[str]]:
    """Return every ``columns``-cell body row of the registry in *text*."""
    rows = []
    for line in text.splitlines():
        if not line.startswith("| "):
            continue
        cells = split_row(line)
        if len(cells) != columns or cells[0] == "arc" or set(cells[0]) <= {"-", ":"}:
            continue
        rows.append(cells)
    return rows


def _original_ledger() -> dict[tuple[str, str], tuple[str, ...]]:
    """Every ledger row in the four originals, keyed ``(arc, id)``."""
    return {
        (arc, tuple(row)[0]): tuple(row)
        for arc, rel in ORIGINALS.items()
        for row in ledger_by_header(at(BASE_REF, rel))
    }


def ledger_problems(ledger_text: str) -> list[str]:
    """Compare every original ledger row against the migrated ledger."""
    was = _original_ledger()
    now = {
        (cells[0], unescape(cells[1])): (
            unescape(cells[1]), cells[3], cells[4], cells[5], cells[6]
        )
        for cells in _registry_rows(ledger_text, 7)
    }
    problems = []
    if len(was) != len(now):
        problems.append(
            f"LEDGER COUNT: {len(was)} rows in the originals, {len(now)} migrated",
        )
    problems += [f"LEDGER DROPPED: {a}:{i}" for a, i in sorted(set(was) - set(now))]
    problems += [f"LEDGER INVENTED: {a}:{i}" for a, i in sorted(set(now) - set(was))]
    fields = ("id", "finding", "worst measured", "status", "owner")
    for key in sorted(set(was) & set(now)):
        for field, before, after in zip(fields, was[key], now[key]):
            if before != after:
                problems.append(
                    f"LEDGER ALTERED {key[0]}:{key[1]} [{field}]\n"
                    f"      was: {before[:150]}\n      now: {after[:150]}",
                )
    return problems


def step_problems(steps_text: str) -> list[str]:
    """Compare every original checkbox against the migrated step index."""
    was = {
        (arc, sid): (ticked, first)
        for arc, rel in ORIGINALS.items()
        for sid, ticked, first in all_checkboxes(at(BASE_REF, rel))
    }
    now = {
        (cells[0], cells[1]): (cells[4] == "SHIPPED", unescape(cells[3]))
        for cells in _registry_rows(steps_text, 7)
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


def migration_problems() -> list[str]:
    """Proof A: the migration commit lost nothing from the four originals."""
    return (
        ledger_problems(at(MIGRATION_REF, LEDGER_REL))
        + step_problems(at(MIGRATION_REF, STEPS_REL))
    )


def merge_problems(ledger_text: str) -> list[str]:
    """Proof B: each declared merge carried every fact from BOTH originals.

    Args:
        ledger_text: The LIVE ``ledger.md``.

    Returns:
        One message per merge that was not made, lost its survivor, failed to
        cite the id it absorbed, or dropped a fact from either original row.
    """
    was = _original_ledger()
    rows = {
        (cells[0], unescape(cells[1])): cells
        for cells in _registry_rows(ledger_text, 7)
    }
    problems = []
    for absorbed, survivor in sorted(MERGES.items()):
        absorbed_key = f"{absorbed[0]}:{absorbed[1]}"
        if absorbed in rows:
            problems.append(f"MERGE NOT MADE: {absorbed_key} is still its own row")
        if survivor not in rows:
            problems.append(
                f"MERGE LOST ITS SURVIVOR: {absorbed_key} was merged into "
                f"{survivor[0]}:{survivor[1]}, which is not in ledger.md",
            )
            continue
        cells = rows[survivor]
        # The citation must be the ``=`` relation in the `also` COLUMN, not any
        # substring of the row: a reviewer showed that a survivor could DENY the
        # merge in prose ("NOT the same defect as ...") and still satisfy a
        # whole-row search.
        if f"= {absorbed_key}" not in cells[2]:
            problems.append(
                f"MERGE ORPHANED A CITATION: {survivor[0]}:{survivor[1]}'s `also` "
                f"column does not carry '= {absorbed_key}', so every commit "
                f"message and as-built record citing that id resolves to nothing",
            )
        carried = " | ".join(cells)
        for source in (absorbed, survivor):
            if source not in was:
                continue
            for fact in sorted(set(FACT_RX.findall(" | ".join(was[source])))):
                if fact not in carried:
                    problems.append(
                        f"MERGE DROPPED A FACT: {fact!r} was in "
                        f"{source[0]}:{source[1]} and is in no cell of "
                        f"{survivor[0]}:{survivor[1]}",
                    )
    return problems


def check() -> list[str]:
    """Both proofs, in one list."""
    return migration_problems() + merge_problems((PLANS / "ledger.md").read_text())


def _control(label: str, mutate, needle: str) -> None:
    """Run one negative control and print whether it fired.

    Args:
        label: What the mutation does, for the report.
        mutate: Callable returning the problem list under the mutation.
        needle: Substring the resulting message must contain.
    """
    fired = [p for p in mutate() if needle in p]
    verdict = "FIRED" if fired else "*** DID NOT FIRE ***"
    print(f"  {label:<58} {verdict}")
    for line in fired[:1]:
        print(f"      {line.splitlines()[0][:110]}")


def _run_controls() -> None:
    """Plant one mutation against each arm and require it to be reported."""
    print("NEGATIVE CONTROLS: one planted mutation per arm\n")
    live = (PLANS / "ledger.md").read_text()

    def drop_a_migrated_row():
        text = at(MIGRATION_REF, LEDGER_REL).replace("| balance | FU-3 |", "| balance | FU-9 |", 1)
        return ledger_problems(text)

    def untick_a_migrated_step():
        text = at(MIGRATION_REF, STEPS_REL).replace(
            "| pay_calendar | C1 |", "| pay_calendar | C9 |", 1)
        return step_problems(text)

    def deny_the_merge():
        return merge_problems(live.replace("| pay_calendar | P6 | = recurrence:F-12 |",
                                           "| pay_calendar | P6 | -- |", 1))

    def drop_a_fact():
        # The mutation must land in the SURVIVOR's own row.  Replacing the
        # first "$3,228.55" in the file hit balance:N-116 instead, and the arm
        # correctly stayed silent -- a control that passes while proving
        # nothing, which is the failure this whole file exists to refuse.
        row = next(ln for ln in live.splitlines()
                   if ln.startswith("| balance | N-123 |"))
        assert "$3,228.55" in row, "the control's anchor fact left N-123"
        return merge_problems(live.replace(row, row.replace("$3,228.55", "some money"), 1))

    def unmerge():
        return merge_problems(live.replace("| pay_calendar | P2 |", "| recurrence | F-10 |", 1))

    _control("A  a row the migration carried is dropped", drop_a_migrated_row, "DROPPED")
    _control("A  a step the migration carried is renamed", untick_a_migrated_step, "DROPPED")
    _control("B  a survivor stops citing what it absorbed", deny_the_merge, "ORPHANED")
    _control("B  a survivor drops a dollar figure", drop_a_fact, "DROPPED A FACT")
    _control("B  a declared merge was never made", unmerge, "NOT MADE")
    print()


def main() -> int:
    """Report the verdict, and with ``--control`` prove the checks can fail."""
    if "--control" in sys.argv:
        _run_controls()

    problems = check()
    if problems:
        print(f"VERIFICATION: FAILED -- {len(problems)} problem(s)\n")
        for problem in problems[:40]:
            print(f"  {problem}")
        return 1
    print(f"PROOF A -- the migration ({BASE_REF} -> {MIGRATION_REF}): PASSED")
    print("  both sides are immutable commits, so this admits no exemption:")
    print("  every ledger row in all four originals appears in the migrated")
    print("  ledger with byte-identical id / finding / worst / status / owner,")
    print("  every checkbox appears in the migrated index with its tick state")
    print("  and first line unchanged, and none was invented or dropped")
    print()
    print(f"PROOF B -- the {len(MERGES)} merges made since, against the LIVE ledger: PASSED")
    for absorbed, survivor in sorted(MERGES.items()):
        print(f"    {absorbed[0]}:{absorbed[1]:6} -> {survivor[0]}:{survivor[1]}")
    print("  each survivor cites what it absorbed in its `also` column and")
    print("  carries every dollar figure, file:line and date from BOTH originals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
