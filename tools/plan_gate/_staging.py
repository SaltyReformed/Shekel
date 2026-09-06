"""Planting a defect in a registry: the staging helpers every control shares.

A gate arm is only worth its message if it FAILS when the thing it guards
breaks, so each one has a control that plants the defect and asserts the arm
reports it.  Planting means rewriting one row of a registry, and the functions
here are how: find the row, rewrite one cell, CHOOSE a row of the right shape
to rewrite, or -- when the corpus no longer HOLDS that shape -- rewrite several
real rows back into it.

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

**The CHOOSING helpers exist because pinning a row is a measured failure
mode, four times over.**  A control that stages ``| balance | N-128 |`` by
name works until a step closes N-128 -- which `pay_calendar:C2-c` did on
2026-08-13, breaking nine controls at once; ``pay_calendar:P2`` did the same at
C2-b2; rule 5's archival of the anchor half's completed span did it to
``balance:X-an`` and ``balance:X-f3``; and `pay_calendar:C2-f3e` ticked the
`balance:X-l` / `pay_calendar:C2` / `recurrence:R-F12` identity class whole on
2026-08-20, which four controls were anchored on.  Every one of those was a
CORRECT edit
that a control failed on, which is the kind of test that gets weakened rather
than believed -- and ``test_registry_integrity``'s own docstrings say so:
*"pinning data the registry is expected to grow is a test that fails on correct
edits"*.  So a control states the SHAPE it needs and takes whatever row has it.
"""
from __future__ import annotations

import _plan_gate
import _registry as registry
from _classes import decomposition_leaf_keys, identity_class


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


def a_live_ledger_row(skip: int = 0) -> str:
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

    Args:
        skip: How many qualifying rows to pass over first.  A control needing
            TWO distinct live rows -- one to stage, one to point it at -- asks
            for the second rather than naming it, which is the whole argument
            of this helper applied twice.

    Returns:
        A :func:`row_of` prefix matching exactly one live ledger row.
    """
    passed = 0
    for row in registry.ledger_rows():
        if " " in row.ident or "(" in row.ident:
            continue
        prefix = f"| {row.arc} | {row.ident} |"
        matches = [
            ln for ln in registry.LEDGER.read_text().splitlines()
            if ln.startswith(prefix)
        ]
        if len(matches) == 1:
            if passed == skip:
                return prefix
            passed += 1
    raise AssertionError(
        f"fewer than {skip + 1} live ledger row(s) carry a bare, unambiguous id",
    )


def a_prefix_trap() -> tuple[str, list[str]]:
    """Return a SHIPPED step whose id is a string prefix of OPEN, unrelated ones.

    **DERIVED, never named**, and it is ledger row **D42**'s stated fix:
    "CHOOSE any shipped step sharing a prefix with an open one, and say so
    loudly when none exists".  The control that grades the declared-parent
    design pinned the recurrence arc's ``R-F1``.  Its sharers went stale in
    both directions: ``R-F13`` was never in this index, and ``R-F10`` and
    ``R-F12`` had both SHIPPED by 2026-08-20 -- so the control went red for the
    single reason that the corpus had progressed, and the workaround was to
    hold ``R-F1`` in a size-capped index for the control's benefit.

    **What deriving buys is one hop and a MESSAGE, not immortality.**  Exactly
    ONE pair qualifies on the live corpus (``pay_calendar:C1`` against ``C10``
    / ``C11`` / ``C12``), so the next step to tick all three takes this red
    again -- but it takes it with a sentence naming what left, where the named
    version took it with a ``KeyError``.

    TWO exclusions, both of which make the pair genuinely unrelated: a DECLARED
    parent, whose prefix-sharers ARE its leaves; and a sharer spelled
    ``<id>-<suffix>``, which is rule 2's decomposition spelling and so is a
    real parent-leaf relation whether or not anyone declared it.  Without the
    second, a future undeclared parent shipped ahead of an open leaf would be
    graded here as the TRAP -- the exact inverse of the measurement this
    preserves, reading green.

    Returns:
        ``(shipped key, [open sharer keys])`` for the first such pair in table
        order.

    Raises:
        AssertionError: The corpus holds no such pair, which means the reason
            the parent set is DECLARED rather than derived should be re-read
            rather than assumed.
    """
    rows = registry.step_rows()
    for row in rows:
        if not row.shipped or row.is_decomposed_parent:
            continue
        sharers = sorted(
            other.key for other in rows
            if other.arc == row.arc
            and other.ident != row.ident
            and other.ident.startswith(row.ident)
            and not other.ident.startswith(f"{row.ident}-")
            and not other.shipped
        )
        if sharers:
            return row.key, sharers
    raise AssertionError(
        "no SHIPPED step is a string prefix of an OPEN one anywhere in the "
        "corpus, so the id-PREFIX trap has left it entirely.  The parent set "
        "is DECLARED rather than derived BECAUSE of that trap: re-read that "
        "reasoning before relaxing anything here"
    )


def an_identity_class_with_leaves() -> tuple[list[str], list[str]]:
    """Return an identity class's member keys and its derived leaf keys.

    The specimen for every control that grades a container whose leaves are
    filed under a SIBLING's name.  Derived for the reason
    :func:`a_prefix_trap` states: three controls named
    ``balance:X-l`` / ``pay_calendar:C2`` / ``recurrence:R-F12`` and went red
    together when that class ticked at ``C2-f3e``.  **And the same honesty
    applies**: exactly ONE class qualifies today -- that same one -- so rule 5
    archiving the completed span takes these controls red again.  What is
    bought is the message and the filter below, not permanence.

    Returns:
        ``(member keys, leaf keys)`` for the first class in table order that
        declares at least one member a parent and has at least one leaf.

    Raises:
        AssertionError: The corpus holds no such class.
    """
    rows = registry.step_rows()
    for row in rows:
        if not row.alias_keys() or not row.is_decomposed_parent:
            continue
        members = identity_class(row, rows)
        if len(members) < 2:
            continue
        leaves = decomposition_leaf_keys(row, rows)
        # **The parent returned FIRST must have NO arc-local leaf**, which is
        # the whole shape under test and is not a preference.  A per-arc leaf
        # derivation -- the blind spot the class arms exist to catch -- still
        # answers correctly for a member whose own arc holds its leaves, so
        # returning `pay_calendar:C2` instead of `balance:X-l` hides the defect
        # from the controls that stage this specimen.  Measured 2026-08-20 by
        # planting that derivation: with this filter relaxed, three of the four
        # class controls go green on it; :func:`stage_an_open_leaf` asserts the
        # same property so no caller depends on the filter alone.
        if leaves and not any(
            key.startswith(f"{row.arc}:") for key in leaves
        ):
            others = [member.key for member in members if member.key != row.key]
            return [row.key, *others], leaves
    raise AssertionError(
        "no identity class in the corpus holds a declared parent whose leaves "
        "are ALL filed under a sibling's name, so the shape these controls "
        "grade cannot be staged from real rows.  That shape is what a per-arc "
        "leaf derivation is blind to; re-read `_classes.decomposition_leaf_keys` "
        "before relaxing anything here"
    )


def stage_a_fork(stage) -> str:
    """Stage one RULED cross-arc fork, built out of rows the corpus really has.

    **The fork table emptied on 2026-08-28**, when `recurrence:R17` /
    `pay_calendar:C5b` shipped the remedy `pay_calendar:P16` was ruled to and
    that defect row closed.  `P16` was the last fork, so twelve controls lost
    their subject at once -- and one of them,
    ``test_the_live_corpus_actually_contains_forks_to_grade``, exists precisely
    to ANNOUNCE that: a rule with no subject in the corpus is untested by the
    clean case.  It was reporting the truth.

    **This is the same answer** :func:`stage_a_live_container` **gives to the
    same problem**, which the corpus hit first on 2026-08-20: rather than name
    a replacement the next tick removes again, build the shape under test out
    of REAL rows and synthesise only the STATE.  Naming one is what broke three
    of these controls on 2026-08-10, when the rule worked and the fork left.

    The specimen is well formed against all three arms of
    :func:`_registry.fork_violations`: its defect cell names a LIVE ledger row,
    its remedies name two LIVE steps, and it is RULED to the remedy that row's
    owner column already names -- so arm 3, which asks that a ruled fork's row
    be owned by the winner, is satisfied by the corpus itself rather than by
    this function asserting it.

    Args:
        stage: The ``stage`` fixture, applied once.

    Returns:
        The staged fork's defect key, so a caller can find its row.

    Raises:
        AssertionError: The corpus holds no ledger row whose owner is a live
            step, or holds fewer than two live steps -- either of which would
            mean there is nothing left to build a fork out of, and the control
            should say so rather than pass.
    """
    steps = {row.key: row for row in registry.step_rows()}
    live = {key for key, row in steps.items() if not row.shipped}
    winner = defect = None
    for row in registry.ledger_rows():
        # A BARE id, for the same reason :func:`a_live_ledger_row` demands one:
        # a control locates this row again by PREFIX, and an annotated cell
        # (``N-205 (X-f1e3's design review)``) does not match ``| balance |
        # N-205 |``.  ``row.key`` strips the annotation, so without this the
        # fork would name a key no prefix lookup can find.
        if " " in row.ident or "(" in row.ident:
            continue
        owners = [
            match.group("owner")
            for match in (_plan_gate.OWNER_RX.match(part)
                          for part in _plan_gate.split_owners(
                              row.owner.strip()))
            if match
        ]
        candidates = [f"{row.arc}:{owner}" for owner in owners]
        hit = next((key for key in candidates if key in live), None)
        if hit is not None:
            winner, defect = hit, row.key
            break
    assert winner is not None, (
        "no ledger row names a live step, so no fork can be built from the "
        "corpus -- the controls below would have no subject"
    )
    loser = next(key for key in sorted(live) if key != winner)

    header = "| defect | competing remedies | ruled |\n|---|---|---|"
    stage(
        "steps", header,
        header + f"\n| {defect} | {winner} (the remedy this row's owner "
        f"column already names) **vs** {loser} (a competing remedy, staged) | "
        f"**{winner}**, staged by `stage_a_fork` -- the corpus holds no live "
        f"fork, so the shape under test is built from real rows |",
    )
    return defect


def stage_a_live_container(stage) -> list[str]:
    """Stage a real identity class back into a CONTAINER with an open leaf.

    The shape two ``_order`` controls grade -- a container whose tick rank
    comes from a leaf filed under a SIBLING's name -- and the corpus stopped
    holding it on 2026-08-20, when `pay_calendar:C2-f3e` ticked the last such
    class whole.  Rather than name a replacement that the next tick removes
    again, this re-opens REAL rows of a derived class: one shipped leaf goes
    back to a rank and every member goes back to ``container``.

    It edits rows the live files actually contain, so the parser is still
    reading the shapes those files use; what is synthetic is only the tick
    STATE, which is exactly the state under test.  The same argument
    ``TestAForkBindsItsRemediesAndItsDefectRow`` states for staging an unruled
    fork: proving the arm must not depend on the developer happening to have
    one open today.

    Args:
        stage: The ``stage`` fixture, applied once per row.

    Returns:
        The class's member keys, the first of which is the declared parent.
    """
    members, _leaf = stage_an_open_leaf(stage)
    for member in members:
        arc, ident = member.split(":", 1)
        line = row_of("steps", f"| {arc} | {ident} |")
        stage("steps", line, with_cell(line, 4, "container"))
    return members


def stage_an_open_leaf(stage) -> tuple[list[str], str]:
    """Re-open one SHIPPED leaf of a derived identity class.

    The half :func:`stage_a_live_container` and the decomposition control
    share: both need a class whose leaves are filed under a SIBLING's arc and
    one of those leaves OPEN, and they differ only in what they then do to the
    parent -- the container arms want it a ``container``, the decomposition arm
    wants it SHIPPED, which it already is.

    Args:
        stage: The ``stage`` fixture, applied once.

    Returns:
        ``(member keys, the re-opened leaf key)``; the first member is the
        declared parent.
    """
    members, leaves = an_identity_class_with_leaves()
    rows = {row.key: row for row in registry.step_rows()}

    # **The property every caller depends on, asserted HERE rather than at each
    # of them.**  The parent's leaves must all be filed under a SIBLING's arc:
    # a per-arc leaf derivation -- the blind spot these controls exist to catch
    # -- still answers correctly for a parent whose own arc holds its leaves,
    # so a specimen without this property makes every caller pass while the
    # defect is planted.  :func:`an_identity_class_with_leaves` filters for it;
    # this is the reconciler, because a filter in one place feeding three
    # controls is exactly the shape that gets "simplified" away.
    parent_arc = members[0].split(":", 1)[0]
    assert not any(key.startswith(f"{parent_arc}:") for key in leaves), (
        f"{members[0]} holds an arc-local leaf, so staging it grades nothing: "
        f"a per-arc derivation would answer correctly for it.  Leaves: {leaves}"
    )

    shipped_leaves = [key for key in leaves if rows[key].shipped]
    assert shipped_leaves, (
        f"{members[0]}'s class holds no SHIPPED leaf to re-open, so the "
        f"container-with-an-open-leaf shape cannot be staged from real rows"
    )

    # A rank ABOVE every live one, so the staged corpus holds no duplicate and
    # the arm under test reports on this row alone.
    free_rank = max(
        (row.rank for row in rows.values() if row.rank is not None), default=0,
    ) + 1
    arc, ident = shipped_leaves[0].split(":", 1)
    line = row_of("steps", f"| {arc} | {ident} |")
    stage("steps", line, with_cell(line, 4, f"#{free_rank}"))
    return members, shipped_leaves[0]
