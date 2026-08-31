"""Architecture test: ``budget.account_openings`` has exactly ONE writer.

Plan step **X-f3c-2b-2a**, ruling **R-ES** applied one table over.  An account's
opening equity is the level every balance the app renders for that account is
stacked on, and the table it lives in is append-only: a restatement is a new
row, and the rules that make one safe -- the owner's write lock, ruling
**R-EQ**'s did-this-change compare, and the audit line naming the figure AND
its provenance -- belong to the TABLE rather than to whichever function does
the INSERT.

**Why a test rather than a docstring.**  The assertion table already proved
that a second writer is what actually happens: ``account_service.create_account``
constructed :class:`~app.models.account.AccountAnchorHistory` itself until plan
step X-f1e2, and the two writers differed in every rule that is not the row's
columns -- one took the lock, ran the compare and logged; the other did none of
it.  This table went the same way for the same reason: the factory built its
own row while it was the only writer, and adding the restatement door would
have made two.  Nothing mechanical bit on the first instance and nothing would
bite on the next, which is what this file is for.

What this test enforces
-----------------------

For every ``.py`` file under ``app/``: zero ``ast.Call`` whose callee resolves
to the name ``AccountOpening``, except in
``app/services/opening_service.py``.  Migrations are outside ``app/`` and are
deliberately out of scope -- ``a7c41f9d2b60`` seeded the table in raw SQL and
is frozen history, and ruling **R-HY** already forbids a migration rewriting
one of these rows.

Why AST, not grep
-----------------

The name appears in prose all over this arc -- ``app/models/account_opening.py``
defines the class, and eight modules' docstrings cite it -- so a text search
answers with the documentation rather than with the writers.  Walking
``ast.Call`` nodes sees only construction, which is the act the rule is about.
It also cannot be fooled by an ``import ... as`` alias, because the ALIAS is
what appears at the call site and the import that bound it is resolved here
too.

The negative case
-----------------

:meth:`TestTheOneWriter.test_the_scanner_fires_on_a_planted_construction`
plants the violation in a temporary file and asserts the scanner finds it.  A
census that returns "no violations" is indistinguishable from a census that
looked in the wrong place -- this repo has measured that failure eight
separate ways -- so the passing claim is only worth what the firing proof is.
"""

import ast
from pathlib import Path

import pytest

#: The class whose construction is the write.
_MODEL_NAME = "AccountOpening"

#: The ONE module allowed to construct it, relative to the repo root.
_THE_WRITER = "app/services/opening_service.py"

#: Where the model itself is defined.  Its ``class AccountOpening`` statement
#: is not a construction, so it never appears in the census -- but the module
#: is listed here so a reader is not left wondering whether the scanner is
#: simply blind to it.
_MODEL_MODULE = "app/models/account_opening.py"


def _repo_root() -> Path:
    """Return the repository root, from this file's own location."""
    return Path(__file__).resolve().parents[2]


def _aliases_bound_to_the_model(tree: ast.AST) -> set[str]:
    """Return every local name in *tree* bound to the model class.

    Both spellings that reach a construction: ``from ... import AccountOpening``
    (the name itself) and ``from ... import AccountOpening as X`` (the alias).
    A plain ``import app.models.account_opening`` binds the MODULE, so a
    construction through it reads as ``ast.Attribute`` and is caught by
    :func:`_constructions_in` separately.

    Args:
        tree: The parsed module.

    Returns:
        The set of local names that construct the model when called.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for name in node.names:
                if name.name == _MODEL_NAME:
                    bound.add(name.asname or name.name)
    return bound


def _constructions_in(source: str) -> list[int]:
    """Return the line numbers where *source* CONSTRUCTS the model.

    Two callee shapes count, and nothing else does: a bare ``Name`` bound by an
    ``ImportFrom`` above, and an ``Attribute`` whose final attribute is the
    class name (``account_opening.AccountOpening(...)``, the module-import
    spelling).  A bare ``Name`` that was never imported is NOT counted -- it
    would be a ``NameError`` at runtime, and counting it would make the census
    fire on a local variable that happens to share the name.

    Args:
        source: The module's text.

    Returns:
        The 1-indexed line numbers of the constructions, ascending.
    """
    tree = ast.parse(source)
    aliases = _aliases_bound_to_the_model(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if isinstance(callee, ast.Name) and callee.id in aliases:
            lines.append(node.lineno)
        elif isinstance(callee, ast.Attribute) and callee.attr == _MODEL_NAME:
            lines.append(node.lineno)
    return sorted(lines)


def _app_modules() -> list[Path]:
    """Return every Python module under ``app/``."""
    return sorted((_repo_root() / "app").rglob("*.py"))


class TestTheOneWriter:
    """Nothing outside ``opening_service`` may construct an opening row."""

    def test_only_opening_service_constructs_an_account_opening(self):
        """The census: one writer, and the failure names every other site.

        The message lists file and line rather than a count, because the fix
        for a violation is to route that site through
        ``opening_service.stage_account_opening`` -- and a reader who has to go
        find the site first is a reader who will route it wrong.
        """
        root = _repo_root()
        offenders: list[str] = []
        writer_found = False
        for path in _app_modules():
            relative = path.relative_to(root).as_posix()
            lines = _constructions_in(path.read_text(encoding="utf-8"))
            if not lines:
                continue
            if relative == _THE_WRITER:
                writer_found = True
                continue
            offenders.extend(f"{relative}:{line}" for line in lines)

        assert not offenders, (
            f"{_MODEL_NAME} is constructed outside {_THE_WRITER}: "
            f"{', '.join(offenders)}.  budget.account_openings is append-only "
            "and its writer owns the owner's write lock, ruling R-EQ's "
            "did-this-change compare and the audit line (plan step "
            "X-f3c-2b-2a).  Route the site through "
            "opening_service.stage_account_opening rather than adding a "
            "second writer."
        )
        # **The positive half, and it is not decoration.**  An empty offender
        # list is what a scanner that resolved no aliases at all would also
        # produce; finding the WRITER proves the resolution works on the real
        # tree rather than only on the planted fixture below.
        assert writer_found, (
            f"the census found no {_MODEL_NAME} construction in "
            f"{_THE_WRITER} either, so it is measuring nothing -- check that "
            "the writer still constructs the row directly."
        )

    def test_the_model_module_is_not_itself_a_writer(self):
        """Defining the class is not constructing one, and the scanner knows it.

        Stated as its own case because it is the one file a reader would
        expect the census to trip on, and a scanner that DID trip on it would
        have to carry an exemption -- which is the allowlist this test exists
        instead of.
        """
        source = (_repo_root() / _MODEL_MODULE).read_text(encoding="utf-8")
        assert _constructions_in(source) == []

    @pytest.mark.parametrize("spelling", [
        "from app.models.account_opening import AccountOpening\n"
        "row = AccountOpening(account_id=1)\n",
        "from app.models.account_opening import AccountOpening as Opening\n"
        "row = Opening(account_id=1)\n",
        "from app.models import account_opening\n"
        "row = account_opening.AccountOpening(account_id=1)\n",
    ], ids=["direct", "aliased", "module-qualified"])
    def test_the_scanner_fires_on_a_planted_construction(self, spelling):
        """The MUTATION: each spelling a second writer could use is detected.

        Three, because the alias and the module-qualified forms are exactly how
        a census written as a grep for ``AccountOpening(`` would miss one and
        report green.  A control that has never been seen to fire is a control
        nobody has measured.
        """
        assert _constructions_in(spelling) == [2]

    def test_the_scanner_does_not_fire_on_the_bare_name(self):
        """A name nobody imported is not a construction.

        The other direction of the same care: counting an unbound ``Name``
        would make the census fire on a local variable or a mock that happens
        to share the class's name, and a control that cries wolf gets an
        allowlist added to it, which is how a fence stops meaning anything.
        """
        assert _constructions_in("AccountOpening = object\nrow = AccountOpening()\n") == []
