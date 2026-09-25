"""Architecture test: the owner's write lock is taken where a writing transaction BEGINS, and nowhere else.

Plan step ``balance:X-bn``, rulings **credit_card:R-CC106** ("one writer per
owner: every save takes your write lock before it reads anything, in the one
module that opens each request's database transaction"), **R-CC114** (page
loads' writes take it too) and **R-CC115** ("The lock is taken only where
database work starts ... The older calls go, and a test fails the build if
anything else takes the lock").  This is that test.

Why a second home is a defect and not a harmless repeat
-------------------------------------------------------

Before the step, sixteen service and route calls took the lock at their own
point in a transaction.  Each was a claim that ITS read was the one that
needed serialising, and a census over the whole suite found 27 endpoints that
had already locked a row by the time they reached one -- the order that
deadlocks against a door holding the owner's lock and waiting on that row.
Taken where the transaction begins, the lock precedes every read and every
row lock by construction, so a later acquisition can only be a repeat --
and a repeat reads as load-bearing to the next author, who then "fixes" a
path by adding a seventeenth at the wrong depth.

What this test enforces
-----------------------

Over every Python module under ``app/`` and ``scripts/``:

* :func:`app.services.user_write_lock.take_owner_write_lock` -- the one
  statement of the lock -- is referenced only by ``app/db_transaction.py``
  (every command transaction of a signed-in request) and by
  ``app/services/user_write_lock.py`` itself (the every-owner form).
* :func:`app.services.user_write_lock.lock_every_user_writes` -- the deploy
  reconciles' form, which takes every owner's lock ascending at the START of
  the only transactions that reconcile more than one owner -- is referenced
  only by those three reconciles' modules.
* ``pg_advisory_xact_lock`` is spelled only in ``user_write_lock.py``, so no
  module can take the lock by writing the statement out again.
* The name ``lock_user_writes`` -- the deleted per-service form -- is bound
  nowhere.

**What it does not see, stated rather than implied**: a lock reached through
``getattr`` or a string of raw SQL, which nothing catches and no honest module
writes.  It also cannot prove the lock is FIRST in a transaction; that is
:mod:`app.db_transaction`'s construction, and
``tests/test_services/test_user_write_lock.py`` measures it on real requests.

Why AST, not grep
-----------------

Every one of these names appears in prose -- the docstrings that explain the
lock say where it used to be taken -- and a grep would flag the sentences that
state the rule.  Name and attribute nodes cannot be prose.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_PRIMITIVE = "take_owner_write_lock"
_EVERY_OWNER = "lock_every_user_writes"
_STATEMENT = "pg_advisory_xact_lock"
_DELETED = "lock_user_writes"

_PRIMITIVE_HOMES = frozenset({
    "app/db_transaction.py",
    "app/services/user_write_lock.py",
})
_EVERY_OWNER_HOMES = frozenset({
    "app/services/posting_service.py",
    "app/services/account_posting_service/_sync.py",
    "app/services/loan_posting_service/_sync.py",
})
_STATEMENT_HOMES = frozenset({"app/services/user_write_lock.py"})


def _references(source: str, filename: str) -> list[tuple[str, int]]:
    """Return ``(name, line)`` for every reference to one of the lock names in *source*.

    A reference is a bare name, an attribute read off anything (the
    ``user_write_lock.take_owner_write_lock`` and ``func.pg_advisory_xact_lock``
    spellings), or a name an import binds -- so an import that is never called
    still counts, which is the point: binding the lock is the start of taking
    it.

    Args:
        source: The module's text.
        filename: Passed to the parser so a syntax error names the file.

    Returns:
        One pair per reference, in line order (``ast.walk`` is breadth-first,
        so its own order is not the file's).
    """
    watched = {_PRIMITIVE, _EVERY_OWNER, _STATEMENT, _DELETED}
    found: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Name) and node.id in watched:
            found.append((node.id, node.lineno))
        elif isinstance(node, ast.Attribute) and node.attr in watched:
            found.append((node.attr, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in watched:
                    found.append((alias.name, node.lineno))
    return sorted(found, key=lambda pair: (pair[1], pair[0]))


def _violations(relative: str, source: str) -> list[str]:
    """Return one message per reference *relative* is not a home for.

    Args:
        relative: The module's path relative to the repository root, with
            forward slashes -- the key the home sets are written in.
        source: The module's text.

    Returns:
        Empty when every reference sits in a home for its name.
    """
    homes = {
        _PRIMITIVE: _PRIMITIVE_HOMES,
        _EVERY_OWNER: _EVERY_OWNER_HOMES,
        _STATEMENT: _STATEMENT_HOMES,
        _DELETED: frozenset(),
    }
    return [
        f"{relative}:{line} {name}"
        for name, line in _references(source, relative)
        if relative not in homes[name]
    ]


def _scanned_modules() -> list[Path]:
    """Every Python module under ``app/`` and ``scripts/``, ``__pycache__`` excluded."""
    return sorted(
        path
        for top in ("app", "scripts")
        for path in (ROOT / top).rglob("*.py")
        if "__pycache__" not in path.parts
    )


class TestTheOwnerLockHasOneHome:
    """The rule on the real tree, and the scanner's own firing arms."""

    def test_the_scan_reaches_every_home(self) -> None:
        """A scan that misses a home would pass for nothing; pin its reach."""
        scanned = {
            path.relative_to(ROOT).as_posix() for path in _scanned_modules()
        }
        missing = (
            _PRIMITIVE_HOMES | _EVERY_OWNER_HOMES | _STATEMENT_HOMES
        ) - scanned
        assert not missing, f"homes the scan does not reach: {sorted(missing)}"
        assert "scripts/init_database.py" in scanned

    def test_each_home_still_holds_its_name(self) -> None:
        """A home that no longer references its name is a stale allowance.

        Every allowance below is one place the lock may be taken; an entry
        whose module stopped taking it would let a new module of that path
        take it unseen.
        """
        stale: list[str] = []
        for name, homes in (
            (_PRIMITIVE, _PRIMITIVE_HOMES),
            (_EVERY_OWNER, _EVERY_OWNER_HOMES),
            (_STATEMENT, _STATEMENT_HOMES),
        ):
            for relative in sorted(homes):
                source = (ROOT / relative).read_text(encoding="utf-8")
                if not any(
                    found == name for found, _ in _references(source, relative)
                ):
                    stale.append(f"{relative} no longer references {name}")
        assert not stale, "\n".join(stale)

    def test_nothing_else_takes_the_owner_lock(self) -> None:
        """Censused at plan step ``balance:X-bn``: sixteen calls in nine modules were deleted."""
        violations: list[str] = []
        for path in _scanned_modules():
            relative = path.relative_to(ROOT).as_posix()
            violations.extend(
                _violations(relative, path.read_text(encoding="utf-8"))
            )
        assert not violations, (
            "The owner's write lock is taken where a writing transaction "
            "BEGINS (app/db_transaction.py) and at the start of the three "
            "deploy reconciles, and nowhere else (rulings R-CC106, R-CC115). "
            "An acquisition deeper in a transaction can only repeat a lock "
            "already held -- or, off a request, be the only one, which is a "
            "writer that must take it at its own start instead.  Sites:\n"
            + "\n".join(violations)
        )

    def test_the_scanner_catches_each_spelling(self, tmp_path: Path) -> None:
        """An import, an attribute call, the raw statement, and the deleted name."""
        scratch = tmp_path / "takes_it_again.py"
        scratch.write_text(
            "from app.services.user_write_lock import take_owner_write_lock\n"
            "from app.services import user_write_lock\n"
            "from sqlalchemy import func, select\n"
            "user_write_lock.lock_every_user_writes()\n"
            "select(func.pg_advisory_xact_lock(1, 2))\n"
            "lock_user_writes(3)\n",
            encoding="utf-8",
        )
        assert _violations(
            "app/services/some_service.py",
            scratch.read_text(encoding="utf-8"),
        ) == [
            "app/services/some_service.py:1 take_owner_write_lock",
            "app/services/some_service.py:4 lock_every_user_writes",
            "app/services/some_service.py:5 pg_advisory_xact_lock",
            "app/services/some_service.py:6 lock_user_writes",
        ]

    def test_a_home_is_a_home_for_its_own_name_only(self, tmp_path: Path) -> None:
        """``db_transaction`` may take the primitive, not the every-owner form."""
        scratch = tmp_path / "request_boundary.py"
        scratch.write_text(
            "from app.services.user_write_lock import take_owner_write_lock\n"
            "from app.services.user_write_lock import lock_every_user_writes\n",
            encoding="utf-8",
        )
        assert _violations(
            "app/db_transaction.py", scratch.read_text(encoding="utf-8"),
        ) == ["app/db_transaction.py:2 lock_every_user_writes"]

    def test_prose_is_not_a_reference(self, tmp_path: Path) -> None:
        """The docstrings that say where the lock used to be taken stay legal."""
        scratch = tmp_path / "explains_it.py"
        scratch.write_text(
            '"""It called ``lock_user_writes`` and took ``pg_advisory_xact_lock``."""\n'
            "# take_owner_write_lock is db_transaction's\n",
            encoding="utf-8",
        )
        assert not _violations(
            "app/services/some_service.py",
            scratch.read_text(encoding="utf-8"),
        )
