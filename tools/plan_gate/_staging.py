"""Planting a defect in a registry: the staging helpers every control shares.

A gate arm is only worth its message if it FAILS when the thing it guards
breaks, so each one has a control that plants the defect and asserts the arm
reports it.  Planting means rewriting one row of a registry, and the four
functions here are how: find the row, rewrite one cell, or CHOOSE a row of the
right shape to rewrite.

**It is a sibling module for two reasons, and the second one is the interesting
one.**  ``test_registry_integrity.py`` reached pylint's 1,000-line ceiling on
2026-08-14, and this project's own ruling on an over-ceiling module is that it
becomes a package or a sibling rather than being shaved again (findings
**N-152** / **N-156**) -- the same move that split ``_order`` and ``_classes``
out of ``_registry``.  But the helpers were ALREADY shared:
``test_order_and_archive.py`` read them with
``from test_registry_integrity import _row, _with_cell``, a cross-module
PRIVATE-name import (finding **N-33**'s shape) that also made a test module a
library for another test module.  The ceiling only revealed a seam the code
already had.

**The two CHOOSING helpers exist because pinning a row is a measured failure
mode, three times over.**  A control that stages ``| balance | N-128 |`` by
name works until a step closes N-128 -- which `pay_calendar:C2-c` did on
2026-08-13, breaking nine controls at once; ``pay_calendar:P2`` did the same at
C2-b2; and rule 5's archival of the anchor half's completed span did it to
``balance:X-an`` and ``balance:X-f3``.  Every one of those was a CORRECT edit
that a control failed on, which is the kind of test that gets weakened rather
than believed -- and ``test_registry_integrity``'s own docstrings say so:
*"pinning data the registry is expected to grow is a test that fails on correct
edits"*.  So a control states the SHAPE it needs and takes whatever row has it.
"""
from __future__ import annotations

import _registry as registry


def row_of(which: str, prefix: str) -> str:
    """Return the one live row of *which* registry starting with *prefix*.

    Args:
        which: ``"ledger"`` or ``"steps"``.
        prefix: The row's leading cells, e.g. ``"| balance | N-96 |"``.

    Returns:
        The matching line, verbatim.

    Raises:
        AssertionError: When *prefix* matches other than exactly one row --
            which is the control announcing that its subject has moved, rather
            than staging nothing and passing.
    """
    source = {"ledger": registry.LEDGER, "steps": registry.STEPS}[which]
    matches = [ln for ln in source.read_text().splitlines()
               if ln.startswith(prefix)]
    assert len(matches) == 1, f"{prefix!r} matched {len(matches)} rows, expected 1"
    return matches[0]


def with_cell(line: str, index: int, value: str) -> str:
    """Return *line* with cell *index* replaced, KEEPING the column count.

    Rebuilt from cells rather than string-replaced: a naive replacement of a
    short cell value hits the first occurrence anywhere in the row, and the
    registries' rows carry the same short tokens (``--``, an id) in several
    columns.

    Args:
        line: The registry row to rewrite.
        index: The cell to replace, negative indices allowed.
        value: The new cell content, unpadded.

    Returns:
        The rewritten row.
    """
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    cells[index] = value
    return "| " + " | ".join(cells) + " |"


def an_open_step_key() -> str:
    """Return the key of an OPEN, unaliased leaf to stage as a BLOCKER.

    Chosen, never named: three controls pinned ``balance:X-f3`` and it became a
    CONTAINER on 2026-08-13.  A container carries no rank of its own and an
    identity class fires rule 11's alias arm instead of the arm under test, so
    both are excluded.

    Returns:
        An ``arc:id`` key.
    """
    open_steps = [
        row for row in registry.step_rows()
        if not row.shipped and not row.is_container and not row.alias_keys()
    ]
    assert open_steps, "the corpus holds no open leaf to stage as a blocker"
    return open_steps[0].key


def a_shipped_balance_row() -> str:
    """Return a ``| balance | <id> |`` prefix naming a SHIPPED, unaliased step.

    The SUBJECT counterpart of :func:`an_open_step_key`.  Three controls staged
    ``| balance | X-an |`` by name until rule 5 archived that span on
    2026-08-13 and all three went red on a correct edit.  Containers and
    identity-class members are excluded for the reasons that function states.

    Returns:
        A ``row_of`` prefix.
    """
    shipped = [
        row for row in registry.step_rows()
        if row.arc == "balance" and row.shipped and not row.alias_keys()
        and not row.is_container
    ]
    assert shipped, "the balance arc holds no shipped, unaliased leaf to stage"
    return f"| balance | {shipped[0].ident} |"


def a_live_ledger_row() -> str:
    """Return a ``| <arc> | <id> |`` prefix naming a live row with a BARE id.

    The ledger counterpart of :func:`a_shipped_balance_row`, and it exists for
    the same measured reason on a third registry.  Nine controls staged
    ``| balance | N-128 |`` by name until ``pay_calendar:C2-c`` CLOSED that
    finding on 2026-08-13 and all nine went red at once; the repair re-pinned
    them to ``| balance | N-96 |``, which re-arms the trap for whichever step
    closes N-96.  A finding row is the registry element MOST likely to be
    closed by a shipping step -- that is what the ledger is FOR -- so a control
    over it must state the shape it needs rather than a name.

    A BARE id is required because these controls locate their subject by row
    PREFIX: an annotated cell (``N-205 (X-f1e3's design review 2026-08-05)``)
    does not match ``| balance | N-205 |``.

    Returns:
        A :func:`row_of` prefix matching exactly one live ledger row.
    """
    for row in registry.ledger_rows():
        if " " in row.ident or "(" in row.ident:
            continue
        prefix = f"| {row.arc} | {row.ident} |"
        matches = [
            ln for ln in registry.LEDGER.read_text().splitlines()
            if ln.startswith(prefix)
        ]
        if len(matches) == 1:
            return prefix
    raise AssertionError("no live ledger row carries a bare, unambiguous id")
