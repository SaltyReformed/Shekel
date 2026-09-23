"""Architecture test: the migration chain has ONE runner, ``app/migration_runner.py``.

Plan step ``balance:X-cv``, rulings **R-BAL114** (one runner) and **R-BAL120**
(its home).  The deploy (``scripts/init_database.py``) and the test-template
build (``scripts/build_test_template.py``) run the chain through
:mod:`app.migration_runner`, on a connection they hand over, so every template
build pushes it through the path a deploy takes.  A second module that built an
Alembic ``Config`` or called an ``alembic.command`` would be a second spelling
of "run the migrations" (CLAUDE.md rule 14): the template build could drift back
to Alembic's own-connection path and stop grading the deploy's with every test
still green -- which the leaf's adversarial review showed by reverting the
builder and watching nothing fail.

What this test enforces
-----------------------

For every Python module under ``app/`` and ``scripts/`` except
``app/migration_runner.py``: no import of ``alembic.command`` or
``alembic.config``, in any spelling -- ``from alembic import command`` (or
``config``), ``from alembic.command import ...``, ``from alembic.config import
...``, ``import alembic.command``.  ``migrations/env.py`` imports
``alembic.context``, which is the runner's other side, not a runner.

**What it does not see, stated rather than implied**: ``flask db`` (Flask-
Migrate's own commands, the one runner that is not this one by design), an
import by string (``importlib`` / ``__import__``), and the test suite, which
builds a ``Config`` only to read the chain's head.

Why AST, not grep: the names appear in prose, this docstring among them.
"""

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_THE_RUNNER = _REPO_ROOT / "app" / "migration_runner.py"
_RUNNER_MODULES = frozenset({"alembic.command", "alembic.config"})


def _runner_imports(source: str, filename: str) -> list[str]:
    """Return one message per import of an Alembic runner module in *source*.

    Args:
        source: The module's text.
        filename: Named in each message so a failure points at the file.

    Returns:
        Empty when the module imports neither ``alembic.command`` nor
        ``alembic.config``; otherwise one line per site.
    """
    sites: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            sites.extend(
                f"{filename}:{node.lineno} import {alias.name}"
                for alias in node.names if alias.name in _RUNNER_MODULES
            )
        elif isinstance(node, ast.ImportFrom):
            if node.module in _RUNNER_MODULES:
                sites.append(f"{filename}:{node.lineno} from {node.module} import")
            elif node.module == "alembic":
                sites.extend(
                    f"{filename}:{node.lineno} from alembic import {alias.name}"
                    for alias in node.names
                    if f"alembic.{alias.name}" in _RUNNER_MODULES
                )
    return sites


def _scanned_modules() -> list[Path]:
    """Every Python module under ``app/`` and ``scripts/``, ``__pycache__`` excluded."""
    return sorted(
        path
        for top in ("app", "scripts")
        for path in (_REPO_ROOT / top).rglob("*.py")
        if "__pycache__" not in path.parts
    )


class TestTheMigrationRunnerHasOneHome:
    """The rule on the real tree, and the scanner's own firing arms."""

    def test_the_scan_reaches_both_callers_and_the_runner(self) -> None:
        """A scan over the wrong files is green for nothing; pin its reach."""
        scanned = _scanned_modules()
        assert _REPO_ROOT / "scripts" / "init_database.py" in scanned
        assert _REPO_ROOT / "scripts" / "build_test_template.py" in scanned
        assert _THE_RUNNER in scanned

    def test_the_runner_is_the_only_module_that_imports_alembic_commands(
        self,
    ) -> None:
        """Censused 2026-09-23: before X-cv's leaf 1b, both scripts had their own."""
        sites: list[str] = []
        for path in _scanned_modules():
            if path == _THE_RUNNER:
                continue
            sites.extend(
                _runner_imports(path.read_text(encoding="utf-8"), str(path))
            )
        assert not sites, (
            "The migration chain has one runner, app/migration_runner.py "
            "(rulings R-BAL114, R-BAL120).  Run it through upgrade_to_head / "
            "stamp_head on a connection you hand over, not through a Config "
            "of your own.  Sites:\n" + "\n".join(sites)
        )

    def test_the_scanner_sees_the_runner_s_own_imports(self) -> None:
        """The real module it exempts is one the scanner would catch."""
        sites = _runner_imports(
            _THE_RUNNER.read_text(encoding="utf-8"), "migration_runner.py",
        )
        assert len(sites) == 2, sites

    def test_the_scanner_catches_every_spelling(self) -> None:
        """Each way of importing a runner module is a site; a context import is not."""
        source = (
            "from alembic import command, context\n"
            "from alembic.config import Config\n"
            "from alembic.command import upgrade\n"
            "import alembic.command\n"
            "from alembic import config as alembic_config\n"
        )
        assert _runner_imports(source, "x.py") == [
            "x.py:1 from alembic import command",
            "x.py:2 from alembic.config import",
            "x.py:3 from alembic.command import",
            "x.py:4 import alembic.command",
            "x.py:5 from alembic import config",
        ]
