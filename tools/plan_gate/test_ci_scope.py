"""Controls for ``ci_scope``: the classifier, its derivation, its census and its wiring.

Four claims, each graded in the direction it can fail:

1. :func:`ci_scope.scope_of` answers ``registry-only`` for a change set under
   the prefixes and ``full`` for everything else, including the shapes that
   LOOK registry-only (a sibling directory sharing the prefix's spelling, an
   absolute path, a ``..``, an empty set).
2. The prefixes are DERIVED from ``_registry``: a seventh arc document widens
   them with no edit to ``ci_scope``.
3. The census finds a registry read in each spelling it claims to see and
   ignores prose about one; on this repository it finds nothing unexempted,
   and an exemption that excuses nothing is itself reported.
4. ``ci.yml`` is wired to the answer: every code-grading JOB is guarded by
   it, the plan gate is not, the guard names this classifier, and the
   ``lint-and-test`` aggregate judges every grader through ``ci_verdict``.
   (Re-expressed over jobs when ``bank_import:X-gy`` split the single job,
   the developer confirming the change under rule 5, 2026-09-22.)
"""
from __future__ import annotations

import io
import subprocess
import textwrap
import time
from pathlib import Path

import pytest
import yaml

import _registry as registry
import ci_scope
import ci_verdict

FULL, RO = ci_scope.FULL, ci_scope.REGISTRY_ONLY


class TestTheClassifier:
    """Claim 1."""

    @pytest.mark.parametrize("changed", [
        ["docs/plans/steps.md"],
        ["docs/plans/ledger.md", "docs/plans/rulings.md", "docs/plans/steps.md"],
        ["docs/plans/historical/salary_s3e2_as_built_2026-09-11.md"],
        ["docs/plans/implementation_plan_salary.md"],
        ["docs/audits/balance_architecture/README.md"],
        ["docs/audits/balance_architecture/archive/x_bx_as_built_2026-09-11.md"],
        ["tools/plan_gate/_census.py", "tools/plan_gate/test_census.py"],
        ["docs/plans/steps.md", "", "  ", "tools/plan_gate/ci_scope.py"],
    ])
    def test_a_change_set_under_the_prefixes_is_registry_only(self, changed):
        """Every path under a prefix, blank lines ignored: registry-only."""
        assert ci_scope.scope_of(changed) == RO

    @pytest.mark.parametrize("changed", [
        [],
        ["", "  "],
        ["app/services/paycheck_calculator.py"],
        ["docs/plans/steps.md", "app/services/paycheck_calculator.py"],
        ["docs/plans/steps.md", "tests/test_services/test_paycheck_calculator.py"],
        ["docs/plans/steps.md", ".github/workflows/ci.yml"],
        ["docs/plans/steps.md", "CLAUDE.md"],
        ["docs/plans/steps.md", ".claude/agents/code-reviewer.md"],
        ["docs/plans/steps.md", "scripts/hooks/session-start.sh"],
        ["docs/plans/steps.md", "migrations/versions/abc.py"],
        ["docs/runbook.md"],
        ["docs/testing-standards.md"],
        ["docs/audits/polyglot-cleanup/README.md"],
        ["docs/plans2/steps.md"],
        ["docs/plansteps.md"],
        ["docs/plans"],
        ["tools/plan_gate_v2/x.py"],
        ["tools/pylint/tests/test_x.py"],
        ["/docs/plans/steps.md"],
        ["docs/plans/../../app/x.py"],
    ])
    def test_anything_else_is_full(self, changed):
        """One path outside, an empty set, a look-alike, an absolute or a ``..``: full."""
        assert ci_scope.scope_of(changed) == FULL

    def test_the_prefixes_themselves_are_a_pure_directory_set(self):
        """The three directories a registry-only change set may touch, sorted."""
        assert [str(p) for p in ci_scope.registry_only_prefixes()] == [
            "docs/audits/balance_architecture", "docs/plans", "tools/plan_gate",
        ]


class TestThePrefixesAreDerived:
    """Claim 2."""

    def test_a_seventh_arc_document_widens_the_set_with_no_edit_here(self, monkeypatch):
        """A new entry in ``_registry.ARC_DOCS`` is a new prefix, with no edit here."""
        widened = dict(registry.ARC_DOCS)
        widened["seventh"] = registry.REPO / "docs/audits/seventh_arc/README.md"
        monkeypatch.setattr(registry, "ARC_DOCS", widened)
        assert "docs/audits/seventh_arc" in {str(p) for p in ci_scope.registry_only_prefixes()}
        assert ci_scope.scope_of(["docs/audits/seventh_arc/archive/x.md"]) == RO

    def test_an_arc_document_moved_out_of_docs_moves_the_boundary_with_it(self, monkeypatch):
        """The set follows ``_registry``; it holds no spelling of its own to go stale."""
        moved = dict(registry.ARC_DOCS)
        moved["balance"] = registry.REPO / "docs/plans/implementation_plan_balance.md"
        monkeypatch.setattr(registry, "ARC_DOCS", moved)
        assert ci_scope.scope_of(["docs/audits/balance_architecture/README.md"]) == FULL


# ---------------------------------------------------------------- the census


def _scratch(tmp_path: Path, name: str, body: str) -> Path:
    """Write one file under ``<tmp>/tests/`` and return the scratch repo root."""
    root = tmp_path / "tests"
    root.mkdir(exist_ok=True)
    (root / name).write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def _readers(repo: Path) -> list[str]:
    """Census the scratch repo's ``tests/`` root, reported relative to it."""
    return ci_scope.registry_readers([repo / "tests"], repo=repo)


class TestTheCensusFindsEachSpellingItClaims:
    """Claim 3, the positive direction: one case per spelling the docstring names."""

    def test_a_joined_literal(self, tmp_path):
        """``Path("docs/plans/rulings.md")``."""
        repo = _scratch(tmp_path, "test_a.py", '''
            from pathlib import Path
            def test_x():
                assert "R-HW" in Path("docs/plans/rulings.md").read_text()
        ''')
        assert _readers(repo) == ["tests/test_a.py:4: docs/plans/rulings.md"]

    def test_a_slash_chain_of_pieces(self, tmp_path):
        """``ROOT / "docs" / "plans" / "ledger.md"``, reported once, whole."""
        repo = _scratch(tmp_path, "test_b.py", '''
            from pathlib import Path
            ROOT = Path(__file__).resolve().parents[2]
            def test_x():
                assert ROOT / "docs" / "plans" / "ledger.md"
        ''')
        assert _readers(repo) == ["tests/test_b.py:5: docs/plans/ledger.md"]

    def test_a_slash_chain_with_a_joined_piece_and_a_variable_leaf(self, tmp_path):
        """The shape the moved arm had: ``parents[2] / "docs/plans" / registry``."""
        repo = _scratch(tmp_path, "test_c.py", '''
            import pathlib
            def test_x(registry):
                path = pathlib.Path(__file__).resolve().parents[2] / "docs/plans" / registry
                assert path.read_text()
        ''')
        assert _readers(repo) == ["tests/test_c.py:4: docs/plans"]

    def test_an_os_path_join(self, tmp_path):
        """``os.path.join(root, "docs", "plans", "steps.md")``."""
        repo = _scratch(tmp_path, "test_d.py", '''
            import os
            def test_x(root):
                assert open(os.path.join(root, "docs", "plans", "steps.md")).read()
        ''')
        assert _readers(repo) == ["tests/test_d.py:4: docs/plans/steps.md"]

    def test_a_joinpath(self, tmp_path):
        """``root.joinpath("docs", "plans", "steps.md")``."""
        repo = _scratch(tmp_path, "test_j.py", '''
            def test_x(root):
                assert root.joinpath("docs", "plans", "steps.md").read_text()
        ''')
        assert _readers(repo) == ["tests/test_j.py:3: docs/plans/steps.md"]

    def test_a_relative_climb_is_normalised(self, tmp_path):
        """``Path(__file__).parent / "../../docs/plans/steps.md"`` reaches the registry."""
        repo = _scratch(tmp_path, "test_k.py", '''
            from pathlib import Path
            def test_x():
                assert (Path(__file__).parent / "../../docs/plans/steps.md").read_text()
        ''')
        assert _readers(repo) == ["tests/test_k.py:4: ../../docs/plans/steps.md"]

    @pytest.mark.parametrize("text,expected", [
        ("../../docs/plans/steps.md", True),
        ("docs/../app/x.py", False),
        ("docs/plans/../../app/x.py", False),
        ("../../../etc/passwd", False),
        ("..", False),
        ("../.docs/plans/x.md", False),
        ("..docs/plans/x.md", False),
        (".../docs/plans/x.md", False),
        ("/docs/plans/x.md", True),
        ("docs/./plans/x.md", True),
    ])
    def test_leading_climbs_are_segments_not_characters(self, text, expected):
        """``..`` is dropped as a SEGMENT; ``..docs`` and ``.docs`` are names."""
        prefixes = ci_scope.registry_only_prefixes()
        # Pylint: protected-access -- the control grades the normalisation
        # step on its own, below the public census that calls it.
        assert ci_scope._names_a_registry_path(text, prefixes) is expected  # pylint: disable=protected-access

    def test_a_path_constructor_with_positional_pieces(self, tmp_path):
        """``Path("docs", "plans", "steps.md")`` and ``Path("docs") / "plans"``."""
        repo = _scratch(tmp_path, "test_m.py", '''
            from pathlib import Path, PurePosixPath
            def test_x(root):
                assert Path("docs", "plans", "steps.md").read_text()
                assert (Path("docs") / "plans" / "ledger.md").read_text()
                assert PurePosixPath(root, "docs", "plans")
        ''')
        assert _readers(repo) == [
            "tests/test_m.py:4: docs/plans/steps.md",
            "tests/test_m.py:5: docs/plans/ledger.md",
            "tests/test_m.py:6: docs/plans",
        ]

    def test_nested_joinpaths_and_a_chain_inside_one(self, tmp_path):
        """``(root / "docs").joinpath("plans", "x")`` and ``.joinpath().joinpath()``."""
        repo = _scratch(tmp_path, "test_n.py", '''
            from pathlib import Path
            def test_x(root):
                assert (root / "docs").joinpath("plans", "steps.md").read_text()
                assert Path("docs").joinpath("plans").joinpath("ledger.md").read_text()
        ''')
        assert _readers(repo) == [
            "tests/test_n.py:4: docs/plans/steps.md",
            "tests/test_n.py:5: docs/plans/ledger.md",
        ]

    def test_a_helper_module_the_suite_imports_is_censused(self, tmp_path):
        """A read in ``tests/_test_helpers.py`` is a read by every test importing it."""
        repo = _scratch(tmp_path, "_helpers.py", '''
            from pathlib import Path
            LEDGER = Path(__file__).resolve().parents[1] / "docs" / "plans" / "ledger.md"
        ''')
        assert _readers(repo) == ["tests/_helpers.py:3: docs/plans/ledger.md"]

    def test_an_f_string_completing_a_root(self, tmp_path):
        """``f"{root}/docs/plans/steps.md"``: the literal part starts with ``/``."""
        repo = _scratch(tmp_path, "test_e.py", '''
            def test_x(root):
                assert open(f"{root}/docs/plans/steps.md").read()
        ''')
        assert _readers(repo) == ["tests/test_e.py:3: /docs/plans/steps.md"]

    def test_the_balance_arc_document_and_the_gate_package_count_too(self, tmp_path):
        """The other two prefixes are reads as much as ``docs/plans`` is."""
        repo = _scratch(tmp_path, "test_f.py", '''
            from pathlib import Path
            def test_x(root):
                assert (root / "docs/audits/balance_architecture/README.md").read_text()
                assert (root / "tools/plan_gate/_census.py").read_text()
        ''')
        assert _readers(repo) == [
            "tests/test_f.py:4: docs/audits/balance_architecture/README.md",
            "tests/test_f.py:5: tools/plan_gate/_census.py",
        ]

    def test_a_conftest_is_collected_and_counted(self, tmp_path):
        """A ``conftest.py`` is loaded beside the tests it serves, so it is censused."""
        repo = _scratch(tmp_path, "conftest.py", '''
            from pathlib import Path
            STEPS = Path(__file__).resolve().parents[1] / "docs" / "plans" / "steps.md"
        ''')
        assert _readers(repo) == ["tests/conftest.py:3: docs/plans/steps.md"]

    def test_a_file_that_will_not_parse_is_reported_not_skipped(self, tmp_path):
        """A census that skips what it cannot read would pass on nothing."""
        repo = _scratch(tmp_path, "test_g.py", '''
            def test_x(:
                pass
        ''')
        assert _readers(repo) == ["tests/test_g.py:2: SyntaxError"]

    def test_a_chain_is_reported_once_though_every_link_yields_it(self, tmp_path):
        """Only the outermost ``/`` of a chain is reported; its inner links are not reads."""
        repo = _scratch(tmp_path, "test_h.py", '''
            from pathlib import Path
            def test_x(root, name):
                assert (root / "docs" / "plans" / "historical" / name).exists()
        ''')
        assert _readers(repo) == ["tests/test_h.py:4: docs/plans/historical"]


class TestTheCensusIgnoresProse:
    """Claim 3, the negative direction: what is NOT a read."""

    @pytest.mark.parametrize("body", [
        # a docstring discussing the registries
        '''
            def test_x():
                """See docs/plans/steps.md and tools/plan_gate/_census.py for why."""
                assert True
        ''',
        # a module docstring
        '''
            """This suite mirrors docs/plans/conventions.md rule 6."""
            def test_x():
                assert True
        ''',
        # an assertion MESSAGE naming a path mid-string
        '''
            def test_x(value):
                assert value, "cited from app/ but has no row in docs/plans/ledger.md"
        ''',
        # a comment
        '''
            def test_x():
                # the census lives in docs/plans/steps.md
                assert True
        ''',
        # a read of a document OUTSIDE the prefixes
        '''
            from pathlib import Path
            def test_x(root):
                assert (root / "docs" / "runbook.md").read_text()
        ''',
        # a sibling directory sharing the spelling
        '''
            from pathlib import Path
            def test_x(root):
                assert (root / "docs/plans2/steps.md").read_text()
        ''',
        # ``str.join`` is not ``os.path.join``: no string positional arguments
        '''
            def test_x(parts):
                assert ", ".join(parts)
        ''',
    ])
    def test_prose_and_outside_reads_are_not_readers(self, tmp_path, body):
        """Docstrings, comments, messages, outside paths and near-misses: not reads."""
        assert not _readers(_scratch(tmp_path, "test_p.py", body))

    def test_the_manual_harness_directory_is_not_censused(self, tmp_path):
        """``tests/manual/verify_*.py`` reads the registries by design and runs nowhere."""
        repo = _scratch(tmp_path, "test_p.py", "def test_x():\n    assert True\n")
        manual = repo / "tests" / "manual"
        manual.mkdir()
        (manual / "verify_registry.py").write_text(
            'from pathlib import Path\nLEDGER = Path("docs/plans/ledger.md")\n',
            encoding="utf-8",
        )
        assert not _readers(repo)

    def test_a_censused_module_importing_the_manual_harnesses_is_reported(self, tmp_path):
        """The directory is left out because nothing imports it; every spelling counts."""
        repo = _scratch(tmp_path, "test_q.py", '''
            from manual.verify_registry import LEDGER
            import tests.manual.verify_registry
            from tests import manual
            import os, manual.verify_registry
            from tests.manual import verify_registry
            import manual
            from manual_helpers import x
            from mytests.manual import y
            import app.tests.manual_thing
            def test_x():
                assert LEDGER
        ''')
        assert ci_scope.imports_of_the_uncensused([repo / "tests"], repo=repo) == [
            "tests/test_q.py:2", "tests/test_q.py:3", "tests/test_q.py:4",
            "tests/test_q.py:5", "tests/test_q.py:6", "tests/test_q.py:7",
        ]

    def test_an_unparseable_module_is_reported_by_the_import_control_too(self, tmp_path):
        """A file the import control cannot read is a finding, not a pass."""
        repo = _scratch(tmp_path, "test_u.py", "def test_x(:\n    pass\n")
        assert ci_scope.imports_of_the_uncensused([repo / "tests"], repo=repo) == [
            "tests/test_u.py:1: SyntaxError",
        ]


class TestThisRepositoryHasNoUnexemptedReader:
    """Claim 3 on the live tree: the control the whole scope rests on."""

    def test_no_skipped_suite_reads_a_registry_only_path(self):
        """The standing census, on this repository, is empty once exemptions are applied."""
        assert ci_scope.unexempted_readers() == [], (
            "a test the registry-only scope SKIPS reads a registry-only path, so "
            "its outcome can move on a pull request that runs no tests: move the "
            "arm into tools/plan_gate (as test_citations.py was), or, if the read "
            "is of a scratch tree, exempt it in ci_scope.EXEMPT_READERS with why"
        )

    def test_nothing_censused_imports_the_manual_harnesses(self):
        """``tests/manual`` is uncensused because nothing runs it AND nothing imports it."""
        assert not ci_scope.imports_of_the_uncensused()

    def test_pytest_would_collect_nothing_under_the_uncensused_directories(self):
        """The other half of the premise: no ``test_*.py`` or ``conftest.py`` lives there."""
        for directory in ci_scope.NOT_CENSUSED:
            root = registry.REPO / directory
            collected = [p for pat in ci_scope.COLLECTED_BY_PYTEST for p in root.rglob(pat)]
            assert not collected, (
                f"{directory} holds {[p.name for p in collected]}: pytest would collect "
                f"them and the census would not see them; move them or census the directory"
            )
        assert (registry.REPO / "pytest.ini").read_text().count("python_files = test_*.py") == 1

    def test_an_unparseable_file_cannot_be_excused(self, monkeypatch, tmp_path):
        """A ``SyntaxError`` hit keys as itself, line and all, so no entry can match it."""
        repo = _scratch(tmp_path, "test_g.py", "def test_x(:\n    pass\n")
        monkeypatch.setattr(ci_scope, "EXEMPT_READERS", {"tests/test_g.py: SyntaxError": "no"})
        assert ci_scope.unexempted_readers([repo / "tests"], repo=repo) == [
            "tests/test_g.py:1: SyntaxError",
            "tests/test_g.py: SyntaxError: exempted but the census finds no such read; "
            "delete the entry",
        ]

    def test_every_exemption_still_excuses_a_real_hit(self):
        """The raw census must find each exempted ``<file>: <path>`` read."""
        keys = {ci_scope.exemption_key(hit) for hit in ci_scope.registry_readers()}
        assert set(ci_scope.EXEMPT_READERS) <= keys

    def test_an_exemption_is_one_read_not_one_file(self, monkeypatch, tmp_path):
        """A second, different read in an exempted file is still caught."""
        repo = _scratch(tmp_path, "test_s.py", '''
            from pathlib import Path
            def test_x(linked):
                (linked / "docs" / "plans").mkdir()
                assert (Path(__file__).resolve().parents[2] / "docs/plans/steps.md").read_text()
        ''')
        monkeypatch.setattr(ci_scope, "EXEMPT_READERS",
                            {"tests/test_s.py: docs/plans": "the scratch mkdir"})
        assert ci_scope.unexempted_readers([repo / "tests"], repo=repo) == [
            "tests/test_s.py:5: docs/plans/steps.md",
        ]

    def test_an_exemption_that_excuses_nothing_is_reported(self, monkeypatch):
        """A stale exemption is a finding, so the list cannot outlive what it excuses."""
        widened = dict(ci_scope.EXEMPT_READERS)
        widened["tests/test_nothing_here.py: docs/plans"] = "a stale entry"
        monkeypatch.setattr(ci_scope, "EXEMPT_READERS", widened)
        assert ci_scope.unexempted_readers() == [
            "tests/test_nothing_here.py: docs/plans: exempted but the census finds "
            "no such read; delete the entry"
        ]

    def test_the_control_fires_on_a_reader_the_scope_would_skip(self, monkeypatch, tmp_path):
        """Point the census at a scratch tree holding one reader: the arm must name it."""
        repo = _scratch(tmp_path, "test_r.py", '''
            from pathlib import Path
            def test_x(root):
                assert (root / "docs/plans/steps.md").read_text()
        ''')
        monkeypatch.setattr(ci_scope, "EXEMPT_READERS", {})
        assert ci_scope.unexempted_readers([repo / "tests"], repo=repo) == [
            "tests/test_r.py:4: docs/plans/steps.md",
        ]


# ------------------------------------------------------------------- the CLI


class TestTheCommandLine:
    """What ``ci.yml`` actually invokes."""

    def test_the_scope_is_read_from_stdin_and_printed(self, monkeypatch, capsys):
        """``git diff --name-only ... | python ci_scope.py`` prints one word."""
        monkeypatch.setattr("sys.stdin", io.StringIO("docs/plans/steps.md\n"))
        assert ci_scope.main([]) == 0
        assert capsys.readouterr().out == "registry-only\n"

    def test_an_empty_stdin_prints_full(self, monkeypatch, capsys):
        """No changed files is ``full``, not a crash and not ``registry-only``."""
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        assert ci_scope.main([]) == 0
        assert capsys.readouterr().out == "full\n"

    def test_readers_prints_the_unexempted_census(self, capsys):
        """``--readers`` prints the same census the standing control asserts empty."""
        assert ci_scope.main(["--readers"]) == 0
        assert capsys.readouterr().out == ""

    def test_any_other_argument_is_a_usage_error(self, capsys):
        """The CLI takes ``--readers`` or nothing."""
        assert ci_scope.main(["--scope"]) == 2
        assert "usage" in capsys.readouterr().err


# ---------------------------------------------------------------- the wiring


GUARD = "needs.scope.outputs.scope != 'registry-only'"

#: Jobs that may run code without the guard: the classifier, the plan gate,
#: the tax-law check (plan step salary:X-at-4), the aggregate that judges them,
#: and the polyglot linters (every scope).
UNGUARDED_JOBS = {"scope", "plan-gate", "tax-law", "lint-and-test", "polyglot-lint"}

#: The tax-law job's one grading step (ruling salary:R-SAL74).
TAX_LAW_STEP = "Is next year's tax law in? (refuses from December 1)"

#: The steps inside an UNGUARDED job that may run code -- the old single job's
#: step-level allowlist, carried to the jobs its steps now live in.
UNGUARDED_CODE_STEPS = {
    ("scope", "Classify the change set"),
    ("plan-gate", "Install dependencies"),
    ("plan-gate", "Plan gate (every scope)"),
    ("tax-law", "Install dependencies"),
    ("tax-law", TAX_LAW_STEP),
    ("lint-and-test", "Verdict"),
}

#: The ONE step condition a graded job may carry.  A step skipped by its own
#: ``if:`` leaves its job ``success``, so ``ci_verdict`` would read a skipped
#: grader as green: every other step in a graded job runs unconditionally.
STEP_CONDITIONS = {
    ("test", "Run audit-trigger benchmarks (serial)"): "strategy.job-index == 0",
}

#: The jobs whose every step grades: the five ``lint-and-test`` needs, and the
#: aggregate itself.
GRADED_JOBS = ("scope", "plan-gate", "tax-law", "lint", "test", "lint-and-test")


def _jobs() -> dict[str, dict]:
    """Read the jobs out of the live workflow file."""
    text = (registry.REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text)["jobs"]


def _steps(job: str) -> dict[str, dict]:
    """One job's steps, keyed by name."""
    return {step["name"]: step for step in _jobs()[job]["steps"]}


class TestTheWorkflowIsWiredToTheAnswer:
    """Claim 4: read ``ci.yml`` and hold every job to the scope it should have."""

    def test_the_classifier_step_exists_and_calls_this_module(self):
        """The ``scope`` job pipes a rename-visible diff into ``ci_scope.py``."""
        steps = {s.get("id"): s for s in _jobs()["scope"]["steps"] if s.get("id")}
        assert "scope" in steps, "no step with id: scope"
        assert "python tools/plan_gate/ci_scope.py" in steps["scope"]["run"]
        assert "--no-renames" in steps["scope"]["run"], "a rename must show both paths"
        assert "pull_request" in steps["scope"]["run"], "a push to main must be full"
        assert _jobs()["scope"]["outputs"] == {"scope": "${{ steps.scope.outputs.scope }}"}

    def test_the_classifier_step_is_unconditional(self):
        """A skipped classifier has no output; the guards must never see that state."""
        job = _jobs()["scope"]
        assert "if" not in job and "needs" not in job
        steps = {s.get("id"): s for s in job["steps"] if s.get("id")}
        assert "if" not in steps["scope"]

    def test_the_guard_fails_closed_on_a_missing_output(self):
        """Every scope guard in the file is ``!= 'registry-only'``: no output runs everything."""
        conditions = [job["if"] for job in _jobs().values() if "if" in job]
        conditions += [
            step["if"] for job in _jobs().values() for step in job["steps"] if "if" in step
        ]
        scoped = [c for c in conditions if "scope" in c]
        assert scoped, "no guarded job at all"
        assert set(scoped) == {GUARD}
        assert "== 'full'" not in GUARD

    def test_the_plan_gate_runs_in_both_scopes_and_the_code_graders_only_in_full(self):
        """The plan gate is unguarded and waits on nothing; every code grader is guarded."""
        jobs = _jobs()
        gate = jobs["plan-gate"]
        assert "if" not in gate and "needs" not in gate
        run = _steps("plan-gate")["Plan gate (every scope)"]["run"]
        assert "pytest tools/plan_gate" in run
        assert "pylint tools/plan_gate/" in run
        for job in ("lint", "test"):
            assert jobs[job].get("if") == GUARD, f"{job!r} is not guarded by {GUARD}"
            assert jobs[job].get("needs") == "scope", f"{job!r} does not wait on the classifier"
        graders = {
            "lint": [
                "Lint with pylint",
                "Lint scripts with pylint",
                "Lint checker package with pylint",
                "Lint verification harnesses with pylint",
                "Test and apply custom pylint checkers",
                "Cross-tree duplicate-code (pylint app/ + scripts/)",
            ],
            "test": [
                "Run tests",
                "Run audit-trigger benchmarks (serial)",
            ],
        }
        for job, names in graders.items():
            steps = _steps(job)
            for name in names:
                assert name in steps, f"{name!r} is no longer in the guarded {job!r} job"

    def test_no_code_running_job_escapes_the_guard(self):
        """A job that runs pytest, pylint, a script or ``test.sh`` is guarded or named here."""
        for name, job in _jobs().items():
            if name in UNGUARDED_JOBS:
                continue
            runs = " ".join(step.get("run", "") for step in job["steps"])
            if any(tool in runs for tool in ("pytest", "pylint", "python ", "scripts/test.sh")):
                assert job.get("if") == GUARD, name

    def test_no_code_running_step_in_an_unguarded_job_escapes_the_allowlist(self):
        """Inside the classifier, plan-gate, tax-law and aggregate jobs, only named steps run code.

        The single job held this per STEP; a grader added to an unguarded job
        would run on a registry pass and be judged by nobody's guard.
        """
        for name in ("scope", "plan-gate", "tax-law", "lint-and-test"):
            for step in _jobs()[name]["steps"]:
                run = step.get("run", "")
                if any(tool in run for tool in ("pytest", "pylint", "python ", "scripts/test.sh")):
                    assert (name, step["name"]) in UNGUARDED_CODE_STEPS, (name, step["name"])

    def test_no_step_in_a_graded_job_carries_a_condition_of_its_own(self):
        """Every step of a graded job runs, bar the one serial-benchmark leg.

        A step skipped by its own ``if:`` leaves its job ``success`` -- the
        skipped-reads-as-passing hole ``lint-and-test`` exists to close, one
        level down.  The single job held this as ``set(guards) == {GUARD}``
        over EVERY step condition; this is that claim over the jobs.
        """
        for name in GRADED_JOBS:
            for step in _jobs()[name]["steps"]:
                assert step.get("if") == STEP_CONDITIONS.get((name, step["name"])), (
                    name, step["name"], step.get("if"),
                )

    def test_no_graded_step_or_job_may_fail_without_failing(self):
        """``continue-on-error`` would turn a red grader green before the verdict sees it."""
        for name in GRADED_JOBS:
            job = _jobs()[name]
            assert "continue-on-error" not in job, name
            for step in job["steps"]:
                assert "continue-on-error" not in step, (name, step["name"])

    def test_the_tax_law_check_runs_in_every_scope_at_the_refuse_stage(self):
        """From December 1 every pull request waits for next year's tax law (R-SAL74).

        Unguarded and waiting on nothing, like the plan gate, because the
        ruling refuses EVERY release and a registry-only pull request is one;
        and at the REFUSE stage, never the notice stage, which would start
        refusing a month early.  The weekly watch is the NOTICE stage, on a
        schedule.
        """
        job = _jobs()["tax-law"]
        assert "if" not in job and "needs" not in job
        assert _steps("tax-law")[TAX_LAW_STEP]["run"].strip() == (
            "python scripts/check_tax_law.py refuse"
        )
        text = (registry.REPO / ".github/workflows/tax-law.yml").read_text(encoding="utf-8")
        watch = yaml.safe_load(text)
        # PyYAML reads the bare key ``on`` as the boolean True (YAML 1.1).
        assert "schedule" in watch[True]
        job = watch["jobs"]["watch"]
        runs = [step.get("run", "") for step in job["steps"]]
        assert "python scripts/check_tax_law.py notice" in runs
        # A watch that may fail without failing emails nobody.
        assert "continue-on-error" not in job and "if" not in job
        for step in job["steps"]:
            assert "continue-on-error" not in step and "if" not in step, step.get("name")

    def test_the_release_image_waits_for_the_tax_law_check(self):
        """No image is built from December 1 without next year (ruling salary:R-SAL88).

        The pull-request check is judged when a PR is pushed, not when it
        merges, and a tag skips PRs; the publish workflow's own check is judged
        when the image is built.  It is a separate job holding read-only
        permissions (its install runs third-party code, and the build job can
        sign and push), the build NEEDS it, and nothing lets it skip or fail
        green.
        """
        text = (registry.REPO / ".github/workflows/docker-publish.yml").read_text(
            encoding="utf-8",
        )
        jobs = yaml.safe_load(text)["jobs"]
        check = jobs["tax-law"]
        assert check["permissions"] == {"contents": "read"}
        assert "if" not in check and "needs" not in check and "continue-on-error" not in check
        runs = [step.get("run", "") for step in check["steps"]]
        assert "python scripts/check_tax_law.py refuse" in runs
        for step in check["steps"]:
            assert "continue-on-error" not in step and "if" not in step, step.get("name")
        # EVERY other job waits for the check: a second publishing job added
        # beside the build would otherwise escape it.
        for name, job in jobs.items():
            if name != "tax-law":
                assert job.get("needs") == "tax-law", name
                assert "if" not in job, f"an if: could run {name!r} after the check failed"
                assert "continue-on-error" not in job, f"{name!r} could fail green"
        # A re-run of only the failed build reuses a check judged earlier, so
        # the check hands over the instant its refusal starts and the build's
        # FIRST step -- before any checkout or login -- refuses once it has.
        assert check["outputs"] == {"refuse_from": "${{ steps.refuse_from.outputs.epoch }}"}
        emit = next(step for step in check["steps"] if step.get("id") == "refuse_from")
        assert "python scripts/check_tax_law.py refuse --starts-epoch" in emit["run"]
        gate = jobs["build-and-push"]["steps"][0]
        assert gate["env"] == {"REFUSE_FROM": "${{ needs.tax-law.outputs.refuse_from }}"}
        # No step of the build may run past a failed gate or fail green: an
        # ``if: always()`` on the build step would build after the refusal.
        for step in jobs["build-and-push"]["steps"]:
            assert "if" not in step and "continue-on-error" not in step, step.get("name")
        # Both executed steps name ``bash``, so GitHub runs them the way
        # ``_run_step`` below does (pipefail included).
        assert gate["shell"] == "bash" and emit["shell"] == "bash"

    @staticmethod
    def _publish_jobs() -> dict[str, dict]:
        """Read the jobs out of the live release-image workflow."""
        text = (registry.REPO / ".github/workflows/docker-publish.yml").read_text(
            encoding="utf-8",
        )
        return yaml.safe_load(text)["jobs"]

    @staticmethod
    def _run_step(run: str, env: dict[str, str]) -> subprocess.CompletedProcess:
        """Run a step's ``run`` text as GitHub runs a ``shell: bash`` step.

        ``bash --noprofile --norc -eo pipefail`` is GitHub's invocation for an
        explicit ``shell: bash``; a step with NO ``shell:`` gets ``bash -e``
        instead, which is why the wiring test above requires ``shell: bash``
        on both steps this runs.
        """
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", run],
            env={"PATH": "/usr/bin:/bin", **env},
            capture_output=True, text=True, check=False,
        )

    def test_the_build_gate_lets_only_a_future_instant_through(self):
        """The build's first step, RUN: through only while the refusal instant is ahead.

        A re-run of only the failed build reuses a check judged earlier
        (ruling R-SAL88), so this step is what refuses it.  Every value that
        is not a well-formed future instant must refuse -- an empty output, a
        malformed one, one bash cannot compare -- because a check that handed
        nothing has vouched for nothing.
        """
        run = self._publish_jobs()["build-and-push"]["steps"][0]["run"]
        now = int(time.time())
        future = str(now + 3600)
        malformed = "no usable refusal instant"
        missing = "Next year's tax law is missing"
        # value -> (exit status, the reason it must give)
        cases = {
            future: (0, "Before the tax-law refusal"),
            str(now - 1): (1, missing),
            str(now): (1, missing),
            "0": (1, missing),
            "": (1, malformed),
            "abc": (1, malformed),
            # Malformed FUTURE instants: each is what only the digits-only
            # check refuses, and each stays in the future on every run.
            " " + future: (1, malformed),
            future + " ": (1, malformed),
            "+" + future: (1, malformed),
            "-5": (1, malformed),
            "0x10": (1, malformed),
            "\uff11" + future[1:]: (1, malformed),
            "9" * 19: (1, malformed),
            "9" * 23: (1, malformed),
        }
        for value, (expected, reason) in cases.items():
            result = self._run_step(run, {"REFUSE_FROM": value})
            assert result.returncode == expected, (value, result.stdout, result.stderr)
            assert reason in result.stdout + result.stderr, (value, result.stdout, result.stderr)

    def test_the_check_hands_its_instant_to_the_build(self, tmp_path):
        """The check job's output step, RUN: it writes the script's answer, or fails.

        A stand-in ``python`` prints what ``check_tax_law.py refuse
        --starts-epoch`` would; a failing one must fail the step and write
        nothing, so the build is handed no instant and refuses.
        """
        jobs = self._publish_jobs()
        emit = next(step for step in jobs["tax-law"]["steps"] if step.get("id") == "refuse_from")
        stand_in = tmp_path / "python"
        output = tmp_path / "github_output"
        env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "GITHUB_OUTPUT": str(output)}

        # A value the real law never yields, so an emit step that hard-codes
        # today's answer cannot pass.
        stand_in.write_text(
            '#!/bin/sh\n'
            'test "$*" = "scripts/check_tax_law.py refuse --starts-epoch" || exit 9\n'
            'echo 4242424242\n',
        )
        stand_in.chmod(0o755)
        output.write_text("")
        result = self._run_step(emit["run"], env)
        assert result.returncode == 0, result.stderr
        assert output.read_text() == "epoch=4242424242\n"

        # A failing script writes NOTHING, silent or not: a value printed
        # before a failure is no answer.
        for failing in ("exit 1\n", "echo 4242424242\nexit 1\n"):
            stand_in.write_text("#!/bin/sh\n" + failing)
            output.write_text("")
            result = self._run_step(emit["run"], env)
            assert result.returncode != 0, failing
            assert output.read_text() == "", failing

    def test_the_plan_gate_no_longer_hides_inside_the_checker_step(self):
        """Step 5b used to carry ``pytest tools/plan_gate``; a guarded copy is a second run."""
        checker_step = _steps("lint")["Test and apply custom pylint checkers"]
        assert "tools/plan_gate" not in checker_step["run"]


class TestTheRequiredCheckJudgesEveryGrader:
    """``lint-and-test`` -- the check branch protection requires -- answers for every job.

    GitHub counts a SKIPPED required check as passing, so the aggregate must
    run whatever happened and hand every result to ``ci_verdict``.
    """

    def test_the_aggregate_runs_always_and_needs_every_grader(self):
        """``if: always()``, and ``needs`` is exactly the jobs ``ci_verdict`` grades.

        And EVERY job in the workflow but the aggregate and the separately
        required polyglot linters: a grading job left out of ``needs`` is
        judged by no required check at all.  In the single-job era any new
        step was inside the required check by construction; this is what
        keeps a new JOB there.
        """
        jobs = _jobs()
        job = jobs["lint-and-test"]
        assert job.get("if") == "always()"
        assert set(job["needs"]) == set(ci_verdict.EVERY_SCOPE + ci_verdict.FULL_SCOPE_ONLY)
        assert set(jobs) - {"lint-and-test", "polyglot-lint"} == set(job["needs"])

    def test_the_aggregate_hands_the_whole_needs_context_to_the_verdict(self):
        """The verdict step pipes ``toJSON(needs)`` into ``ci_verdict.py``."""
        step = _steps("lint-and-test")["Verdict"]
        assert step["env"] == {"NEEDS": "${{ toJSON(needs) }}"}
        # EXACTLY this: a ``|| true`` or a second command after the pipe would
        # let the verdict's exit 1 fall on the floor.
        assert step["run"].strip() == (
            "printf '%s' \"${NEEDS}\" | python tools/plan_gate/ci_verdict.py"
        )

    def test_the_verdicts_classes_are_the_workflows_guards(self):
        """``EVERY_SCOPE`` jobs are unguarded; ``FULL_SCOPE_ONLY`` jobs carry the guard.

        The verdict lets a ``FULL_SCOPE_ONLY`` job skip on a registry pass; a
        job that list names but the workflow does not guard -- or the reverse
        -- would make the verdict and the workflow disagree about what a
        registry pass may skip.
        """
        jobs = _jobs()
        for name in ci_verdict.EVERY_SCOPE:
            assert "if" not in jobs[name], name
        for name in ci_verdict.FULL_SCOPE_ONLY:
            assert jobs[name].get("if") == GUARD, name
