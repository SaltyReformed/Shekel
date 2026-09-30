"""Architecture test: a BORROWED settle day is built in exactly one place.

Plan step ``balance:X-bi-6-4c-3``, the REC-552 study's seam **S6**.  A transfer
side with no evidence of its own borrows the other side's day, and
:func:`app.services.transfer_service._side_days.borrowed_day` is the ONE
derivation of that day: every writer that (re)derives a borrowed day reaches it
through ``resolve_pair_days``.  The study's loan-side answers would add ONE
input there (a clamp on a loan side's borrowed day); a second construction
elsewhere -- a door computing the guess inline -- is a writer that input never
reaches, and the next import would silently undo an answer (measured by review
4: ``+$397.48`` for good).

What this test enforces
-----------------------

For every Python file under ``app/``: a call to ``SettleDay`` whose basis --
the ``basis=`` keyword or the second positional argument -- is the attribute
``BORROWED`` appears only inside ``borrowed_day``.

**What it does not see, stated rather than implied**: a basis reached through a
variable (``SettleDay(day=d, basis=b)`` with ``b`` bound to ``BORROWED``
elsewhere), and a stored borrowed day READ back (``settle_day_from_columns``
maps a row's id to its member, which is a read, not a derivation).  Neither
builds a guess, and no module writes the first.

Why AST, not grep
-----------------

The member's name appears in prose and in comparisons that refuse it
(``SideDay`` refuses a stated borrowed day; ``is_evidence`` tests for it); only a
call node can be a construction.
"""

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "app"
_THE_ONE_HOME = ("app/services/transfer_service/_side_days.py", "borrowed_day")


def _is_borrowed(node: ast.AST) -> bool:
    """Return whether *node* reads ``<anything>.BORROWED``."""
    return isinstance(node, ast.Attribute) and node.attr == "BORROWED"


def _borrowed_constructions() -> "list[tuple[str, str]]":
    """Return ``(file, enclosing function)`` for each borrowed ``SettleDay`` built in ``app/``."""
    found = []
    for path in sorted(_APP.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        parents = {
            child: parent
            for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            name = callee.attr if isinstance(callee, ast.Attribute) else (
                callee.id if isinstance(callee, ast.Name) else None
            )
            if name != "SettleDay":
                continue
            basis = [k.value for k in node.keywords if k.arg == "basis"]
            basis += node.args[1:2]
            if not any(_is_borrowed(value) for value in basis):
                continue
            # The NEAREST enclosing function, or the module for a
            # construction at import time.
            owner = parents.get(node)
            while owner is not None and not isinstance(
                owner, (ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                owner = parents.get(owner)
            found.append((
                str(path.relative_to(_APP.parent)),
                owner.name if owner is not None else "<module>",
            ))
    return found


def test_a_borrowed_day_is_built_only_by_borrowed_day():
    """Every construction of a borrowed ``SettleDay`` in ``app/`` is ``borrowed_day``'s own.

    The census must FIND that one construction, or it is measuring nothing --
    so the assertion is an equality with the one home, not an absence.
    """
    assert _borrowed_constructions() == [_THE_ONE_HOME]
