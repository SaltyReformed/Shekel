"""Architecture test: ``app/services/`` must never import Flask objects (B6-01, OPT-3).

CLAUDE.md "Architecture" pins the layering ``Routes (Blueprints) -> Services
(no Flask imports) -> Models``: "Services are isolated from Flask -- they
take plain data, return plain data, never import ``request``/``session``."
The financial-calculation audit's B6-01 finding
(``docs/audits/financial_calculations/06_dry_solid.md``) re-verified that
the boundary HOLDS today by grep, but called out that the rule is asserted
only by 22 prose docstrings; nothing mechanical bites if a future commit
adds a Flask import to a service. The plan's Commit 36 / OPT-3 converts
that informal contract into one AST-based scanner so the rule travels with
the test suite.

What this test enforces
-----------------------

For every ``app/services/*.py`` file: zero ``ast.Import`` or
``ast.ImportFrom`` node whose module is ``flask`` or starts with ``flask.``.
That captures the audit-listed surface (``flask``, ``flask.request``,
``flask.session``, ``flask.current_app``, ``flask.g``,
``flask.render_template``) and any flask-submodule slip route a future
violator could try.

Why AST, not regex
------------------

The audit's broader sweep produced five ``g.X`` hits in
``budget_variance_service.py`` and ``spending_trend_service.py`` that turned
out to be comprehension/lambda loop variables (``sum(g.estimated_total
for g in groups)``, ``lambda g: abs(g.variance)``), not Flask's
request-context ``g``. A regex over the file text would have to round-trip
through scope analysis to distinguish those; an AST scanner sidesteps the
issue by walking only ``Import``/``ImportFrom`` nodes -- name usage is
never inspected, so a local-scope ``g`` cannot be mis-flagged. The
``test_loop_variable_g_not_flagged`` case below pins that invariant
explicitly with a tmp-file containing the same pattern.

What is allowed
---------------

``db.session`` (the SQLAlchemy ``scoped_session`` proxy on the ``db``
extension instance imported from ``app``) IS a Flask-Bound object, but
CLAUDE.md "Architecture" explicitly permits Services -> Models via
SQLAlchemy. The 193 ``db.session.*`` references the audit catalogued are
member accesses on an extension imported from ``app``, not imports of
``flask``; the scanner never sees them as ``Import``/``ImportFrom`` nodes,
so it passes. The ``test_db_session_not_flagged`` case below pins this.

Test IDs (``remediation_plan.md`` Section 9 Commit 36 subsection E)
-------------------------------------------------------------------

- C36-1 ``test_no_flask_imports_in_services`` (main, real services)
- C36-2 ``test_linter_detects_injected_flask_import`` (negative: detects
  ``from flask import request``)
- C36-2a ``test_linter_detects_plain_import_flask`` (negative refinement:
  detects bare ``import flask``)
- C36-2b ``test_linter_detects_flask_submodule_import`` (negative
  refinement: detects ``from flask.helpers import url_for``)
- C36-3 ``test_db_session_not_flagged`` (positive: SQLAlchemy session
  usage not flagged)
- C36-3a ``test_loop_variable_g_not_flagged`` (positive refinement: the
  audit's loop-variable ``g`` false-positive class is not flagged)
"""
import ast
from pathlib import Path


SERVICES_DIR = Path(__file__).resolve().parents[2] / "app" / "services"


def _flask_import_violations(source: str, filename: str) -> list[str]:
    """Return one descriptive violation message per forbidden Flask import.

    Walks the AST of ``source`` and inspects every ``ast.Import`` and
    ``ast.ImportFrom`` node. A node is a violation when:

    - ``ast.Import``: any alias's name equals ``flask`` or starts with
      ``flask.`` (e.g. ``import flask``, ``import flask.helpers``).
    - ``ast.ImportFrom``: ``node.module`` equals ``flask`` or starts with
      ``flask.`` (e.g. ``from flask import request``,
      ``from flask.helpers import url_for``).

    Relative imports (``from .foo import bar``) carry ``node.module`` of
    the local module name and never start with ``flask.``, so they are
    not flagged. Third-party Flask extensions (``flask_login``,
    ``flask_wtf``) are separate packages; the audit's B6-01 boundary
    enumerates only ``flask`` proper, so they are out of scope here.

    Args:
        source: file contents as a single string.
        filename: display path used inside violation messages so assertion
            output names the offending file.

    Returns:
        Empty list when the file imports no Flask object; otherwise one
        human-readable string per violation naming the line number and
        the offending import shape.
    """
    tree = ast.parse(source, filename=filename)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name == "flask" or name.startswith("flask."):
                    violations.append(
                        f"{filename}:{node.lineno} forbidden import: "
                        f"import {name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "flask" or module.startswith("flask."):
                imported = ", ".join(alias.name for alias in node.names)
                violations.append(
                    f"{filename}:{node.lineno} forbidden import: "
                    f"from {module} import {imported}"
                )
    return violations


def _service_files() -> list[Path]:
    """Every Python file directly under ``app/services/``.

    Includes ``__init__.py`` and leading-underscore private modules
    (e.g. ``_recurrence_common.py``) because the no-Flask boundary binds
    the whole package, not just public names. ``glob`` skips
    ``__pycache__`` because it does not match ``*.py``.

    Returns:
        Sorted list of absolute ``Path`` objects to every service module.
    """
    return sorted(SERVICES_DIR.glob("*.py"))


class TestNoFlaskImportsInServices:
    """C36-1: enforce the no-Flask-in-services boundary on real code."""

    def test_services_dir_exists(self) -> None:
        """Sanity: the services directory the scanner reads is present.

        Guards against a silent pass if the layout moves (e.g. someone
        renames ``app/services/`` and the AST scan walks an empty list,
        producing zero violations vacuously).
        """
        assert SERVICES_DIR.is_dir(), (
            f"expected app/services/ to be a directory at {SERVICES_DIR}"
        )
        files = _service_files()
        assert files, (
            f"expected at least one app/services/*.py file under {SERVICES_DIR}"
        )

    def test_no_flask_imports_in_services(self) -> None:
        """C36-1: every ``app/services/*.py`` is free of any Flask import.

        Passes today by construction -- B6-01 grep-verified the boundary
        empty. This test makes the contract mechanical so a future Flask
        import slip is caught at CI time, not after it ships.
        """
        all_violations: list[str] = []
        for path in _service_files():
            source = path.read_text(encoding="utf-8")
            all_violations.extend(_flask_import_violations(source, str(path)))
        assert not all_violations, (
            "Services must not import Flask objects (B6-01 / CLAUDE.md "
            "Architecture: Services are isolated from Flask). "
            "Route consumers of request/session/current_app/g/"
            "render_template through the Blueprint layer and pass plain "
            "data into the service. Violations:\n"
            + "\n".join(all_violations)
        )


class TestScannerDetectsViolations:
    """C36-2 family: the scanner bites on every shape of Flask import.

    A green C36-1 plus a silent scanner is the worst failure mode --
    every "boundary holds" claim would be vacuous. These tests inject
    realistic flask-import shapes into ``tmp_path`` and assert the
    scanner reports them.
    """

    def test_linter_detects_injected_flask_import(self, tmp_path: Path) -> None:
        """C36-2: ``from flask import request`` is detected.

        Mirrors the literal injection the verification gate calls for
        (``from flask import request`` is the canonical service-layer
        violation: it pulls the request-bound proxy directly into pure
        compute). Uses tmp_path so the real ``app/services/`` tree is
        never modified.
        """
        scratch = tmp_path / "violator_request.py"
        scratch.write_text(
            '"""Scratch module: must be flagged by the scanner."""\n'
            "from flask import request\n"
            "\n"
            "def http_method() -> str:\n"
            '    """Touch ``request`` so the import is not dead."""\n'
            "    return request.method\n",
            encoding="utf-8",
        )
        violations = _flask_import_violations(
            scratch.read_text(encoding="utf-8"), str(scratch)
        )
        assert violations, (
            "scanner failed to flag ``from flask import request`` -- the "
            "literal injection the verification gate exists to catch"
        )
        assert "from flask import request" in violations[0], violations

    def test_linter_detects_plain_import_flask(self, tmp_path: Path) -> None:
        """C36-2a: bare ``import flask`` is detected.

        Covers the ``ast.Import`` branch alongside ``ast.ImportFrom``.
        Both branches of the scanner must bite or a future violator can
        smuggle a Flask reference via ``flask.Blueprint`` /
        ``flask.current_app`` without a ``from`` clause.
        """
        scratch = tmp_path / "violator_plain.py"
        scratch.write_text(
            '"""Scratch module: bare flask import."""\n'
            "import flask\n"
            "\n"
            "REQUEST_PROXY = flask.request\n",
            encoding="utf-8",
        )
        violations = _flask_import_violations(
            scratch.read_text(encoding="utf-8"), str(scratch)
        )
        assert violations, (
            "scanner failed to flag a bare ``import flask`` -- the "
            "ast.Import branch is silent"
        )
        assert "import flask" in violations[0], violations

    def test_linter_detects_flask_submodule_import(self, tmp_path: Path) -> None:
        """C36-2b: ``from flask.helpers import url_for`` is detected.

        A future violator routing around the bare-``flask`` rule by
        importing a submodule (``flask.helpers``, ``flask.globals``,
        ``flask.json``) would otherwise escape the check. Pinning the
        ``startswith('flask.')`` rule prevents that escape.
        """
        scratch = tmp_path / "violator_submodule.py"
        scratch.write_text(
            '"""Scratch module: importing a Flask submodule."""\n'
            "from flask.helpers import url_for\n"
            "\n"
            "ROUTE_BUILDER = url_for\n",
            encoding="utf-8",
        )
        violations = _flask_import_violations(
            scratch.read_text(encoding="utf-8"), str(scratch)
        )
        assert violations, (
            "scanner failed to flag a flask submodule import -- the "
            "startswith('flask.') guard is silent"
        )
        assert "flask.helpers" in violations[0], violations


class TestScannerSkipsNonImports:
    """C36-3 family: the scanner ignores legitimate non-Flask-import code.

    A scanner that bites on real services would force docstring rewrites
    or false-positive suppressions -- worse than no scanner. These tests
    pin two important non-violations:

    - ``db.session.*`` member access (SQLAlchemy, architecture-permitted).
    - Comprehension/lambda variables literally named ``g`` (the audit's
      false-positive class that motivated AST-not-regex).
    """

    def test_db_session_not_flagged(self, tmp_path: Path) -> None:
        """C36-3: ``from app import db`` + ``db.session.query(...)`` is allowed.

        ``db`` is the project's ``flask_sqlalchemy.SQLAlchemy`` instance
        imported from ``app``; ``db.session`` is its scoped session
        proxy. CLAUDE.md "Architecture" permits Services -> Models via
        SQLAlchemy, and B6-01's evidence table calls 193 ``db.session.*``
        accesses "legitimate". The scanner inspects only Import nodes,
        so member access on ``db`` cannot trigger a flag -- this test
        pins that invariant against a future scanner refactor.
        """
        scratch = tmp_path / "uses_db_session.py"
        scratch.write_text(
            '"""Scratch module: legitimate SQLAlchemy session usage."""\n'
            "from app import db\n"
            "\n"
            "def first_row_id() -> int | None:\n"
            '    """Return the first id via the SQLAlchemy session."""\n'
            "    row = db.session.execute('SELECT 1').scalar()\n"
            "    return row\n",
            encoding="utf-8",
        )
        violations = _flask_import_violations(
            scratch.read_text(encoding="utf-8"), str(scratch)
        )
        assert not violations, (
            "scanner falsely flagged ``db.session`` usage as a Flask "
            "import; CLAUDE.md permits Services -> Models via "
            "SQLAlchemy. Violations: " + str(violations)
        )

    def test_loop_variable_g_not_flagged(self, tmp_path: Path) -> None:
        """C36-3a: a comprehension variable named ``g`` is not a Flask import.

        Pins the audit's false-positive class: ``budget_variance_service``
        and ``spending_trend_service`` both bind ``g`` as a loop variable
        in sum/lambda expressions (``sum(g.estimated_total for g in
        groups)``, ``lambda g: abs(g.variance)``). The AST scanner sees
        only ``Import``/``ImportFrom`` nodes, never ``Name`` nodes, so
        such bindings can never trigger a flag -- this test pins the
        property directly with a scratch file containing the same shape.
        """
        scratch = tmp_path / "uses_g_loop_var.py"
        scratch.write_text(
            '"""Scratch module: g is a loop variable, not Flask\'s g."""\n'
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class Group:\n"
            '    """Minimal Group with one numeric attribute."""\n'
            "    estimated_total: int\n"
            "\n"
            "def total(groups: list[Group]) -> int:\n"
            '    """Sum totals using ``g`` as a comprehension binding."""\n'
            "    return sum(g.estimated_total for g in groups)\n",
            encoding="utf-8",
        )
        violations = _flask_import_violations(
            scratch.read_text(encoding="utf-8"), str(scratch)
        )
        assert not violations, (
            "scanner falsely flagged loop variable ``g`` as a Flask "
            "import; the audit's documented false-positive class. "
            "Violations: " + str(violations)
        )


