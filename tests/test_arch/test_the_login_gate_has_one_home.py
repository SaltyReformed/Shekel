"""Architecture test: no view in ``app/`` gates itself; the login gate is ONE hook.

Plan step ``bank_import:X-gi-4``, ruling **bank_import:R-BI4**.  Whether a
request needs a session is decided in ``app/login_gate.py`` for the whole
application, and a ``@login_required`` on a view would be that decision's
SECOND home -- a maintenance contract (CLAUDE.md rule 14) that nothing
reconciles, harmless on the day it is written and a lie the day someone reads
it as the reason the route is protected.  A future lane adding a route follows
one rule: **write no decorator; the route is gated by existing.**  A route that
must be public is named in :data:`~app.login_gate.PUBLIC_ENDPOINTS`, and
``tests/test_routes/test_auth_required.py`` pins that list.

What this test enforces
-----------------------

For every Python file under ``app/``: no binding of Flask-Login's
``login_required`` by NAME -- ``from flask_login import login_required``
(aliased or not), or ``flask_login.login_required`` read as an attribute
off the package.  ``fresh_login_required`` is the project's own step-up
decorator (:mod:`app.utils.auth_helpers`) and is a different name; the
scanner matches the attribute exactly, and the case below pins that it is
not caught.

**What it does not see, stated rather than implied**: a star import
(``from flask_login import *``), which pylint's ``wildcard-import`` refuses
under the 10.00 floor CI and the Stop hook enforce; and a name reached
through ``getattr(flask_login, "login_required")``, which nothing catches
and which no honest module writes.

Why AST, not grep
-----------------

The name appears in prose: docstrings that explain the gate say
"no view carries ``@login_required``", and a grep would flag the sentence
that states the rule.  Import and attribute nodes cannot be prose.
"""

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"

_THE_DECORATOR = "login_required"


def _self_gating_sites(source: str, filename: str) -> list[str]:
    """Return one message per binding of ``login_required`` in *source*.

    Args:
        source: The module's text.
        filename: Named in each message so a failure points at the file.

    Returns:
        Empty when the module binds the decorator nowhere; otherwise one
        line per site, with its line number and shape.
    """
    tree = ast.parse(source, filename=filename)
    sites: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == _THE_DECORATOR:
                    sites.append(
                        f"{filename}:{node.lineno} from {node.module} import "
                        f"{alias.name}"
                        + (f" as {alias.asname}" if alias.asname else "")
                    )
        elif isinstance(node, ast.Attribute) and node.attr == _THE_DECORATOR:
            sites.append(
                f"{filename}:{node.lineno} {ast.unparse(node)}"
            )
    return sites


def _app_modules() -> list[Path]:
    """Every Python module anywhere under ``app/``, ``__pycache__`` excluded."""
    return sorted(
        path for path in APP_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
    )


class TestNoViewGatesItself:
    """The rule on the real tree, and the scanner's own firing arms."""

    def test_the_scan_reaches_the_route_modules(self) -> None:
        """A scan over an empty list is green for nothing; pin its reach."""
        scanned = _app_modules()
        assert any(
            "routes" in path.parts for path in scanned
        ), f"no route module under {APP_DIR}"
        assert (APP_DIR / "login_gate.py") in scanned

    def test_no_module_under_app_binds_login_required(self) -> None:
        """The gate has one home.  Censused 2026-09-11: 53 modules had two."""
        sites: list[str] = []
        for path in _app_modules():
            sites.extend(
                _self_gating_sites(path.read_text(encoding="utf-8"), str(path))
            )
        assert not sites, (
            "A view must not gate itself: app/login_gate.py refuses every "
            "anonymous request that is not declared public, so a "
            "@login_required is a second home for that decision (rule 14). "
            "Delete it; a route is protected by existing.  Sites:\n"
            + "\n".join(sites)
        )

    def test_the_scanner_catches_the_from_import(self, tmp_path: Path) -> None:
        """The spelling every deleted site used."""
        scratch = tmp_path / "gates_itself.py"
        scratch.write_text(
            "from flask_login import current_user, login_required\n",
            encoding="utf-8",
        )
        sites = _self_gating_sites(scratch.read_text(encoding="utf-8"), "x.py")
        assert sites == ["x.py:1 from flask_login import login_required"]

    def test_the_scanner_catches_an_alias_and_an_attribute(
        self, tmp_path: Path,
    ) -> None:
        """The two ways round a name-keyed grep."""
        scratch = tmp_path / "gates_itself_quietly.py"
        scratch.write_text(
            "from flask_login import login_required as gate\n"
            "import flask_login\n"
            "wrapped = flask_login.login_required\n",
            encoding="utf-8",
        )
        sites = _self_gating_sites(scratch.read_text(encoding="utf-8"), "x.py")
        assert sites == [
            "x.py:1 from flask_login import login_required as gate",
            "x.py:3 flask_login.login_required",
        ]

    def test_the_step_up_decorator_is_not_caught(self, tmp_path: Path) -> None:
        """``fresh_login_required`` is the project's own and stays."""
        scratch = tmp_path / "steps_up.py"
        scratch.write_text(
            "from app.utils.auth_helpers import fresh_login_required\n"
            "import app.utils.auth_helpers as helpers\n"
            "wrapped = helpers.fresh_login_required()\n",
            encoding="utf-8",
        )
        assert not _self_gating_sites(
            scratch.read_text(encoding="utf-8"), "x.py",
        )
