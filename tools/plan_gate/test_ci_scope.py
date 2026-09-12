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
4. ``ci.yml`` is wired to the answer: every code-grading step is guarded by
   it, the plan gate is not, and the guard names this classifier.
"""
from __future__ import annotations

import io
import textwrap
from pathlib import Path

import pytest
import yaml

import _registry as registry
import ci_scope

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


GUARD = "steps.scope.outputs.scope != 'registry-only'"


def _lint_and_test_steps() -> list[dict]:
    """Read the ``lint-and-test`` job's steps out of the live workflow file."""
    text = (registry.REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text)["jobs"]["lint-and-test"]["steps"]


class TestTheWorkflowIsWiredToTheAnswer:
    """Claim 4: read ``ci.yml`` and hold every step to the scope it should have."""

    def test_the_classifier_step_exists_and_calls_this_module(self):
        """A step with ``id: scope`` pipes a rename-visible diff into ``ci_scope.py``."""
        steps = {s.get("id"): s for s in _lint_and_test_steps() if s.get("id")}
        assert "scope" in steps, "no step with id: scope"
        assert "python tools/plan_gate/ci_scope.py" in steps["scope"]["run"]
        assert "--no-renames" in steps["scope"]["run"], "a rename must show both paths"
        assert "pull_request" in steps["scope"]["run"], "a push to main must be full"

    def test_the_classifier_step_is_unconditional(self):
        """A skipped classifier has no output; the guards must never see that state."""
        steps = {s.get("id"): s for s in _lint_and_test_steps() if s.get("id")}
        assert "if" not in steps["scope"]

    def test_the_guard_fails_closed_on_a_missing_output(self):
        """Every guard in the live file is ``!= 'registry-only'``: no output runs everything."""
        guards = [s["if"] for s in _lint_and_test_steps() if "if" in s]
        assert guards, "no guarded step at all"
        assert set(guards) == {GUARD}
        assert "== 'full'" not in GUARD

    def test_the_plan_gate_runs_in_both_scopes_and_the_code_graders_only_in_full(self):
        """The one unguarded grader is the plan gate; every code grader carries the guard."""
        by_name = {s["name"]: s for s in _lint_and_test_steps()}
        gate = by_name["Plan gate (every scope)"]
        assert "if" not in gate
        assert "pytest tools/plan_gate" in gate["run"]
        assert "pylint tools/plan_gate/" in gate["run"]
        guarded = [
            "Lint with pylint",
            "Lint scripts with pylint",
            "Lint checker package with pylint",
            "Lint verification harnesses with pylint",
            "Test and apply custom pylint checkers",
            "Cross-tree duplicate-code (pylint app/ + scripts/)",
            "Configure the test cluster as throwaway",
            "Build test template database",
            "Run tests",
            "Run audit-trigger benchmarks (serial)",
        ]
        for name in guarded:
            assert by_name[name].get("if") == GUARD, f"{name!r} is not guarded by {GUARD}"

    def test_no_code_running_step_escapes_the_guard(self):
        """A new step that runs pytest, pylint or a script must be guarded or be the plan gate."""
        allowed = {"Plan gate (every scope)", "Classify the change set", "Install dependencies"}
        for step in _lint_and_test_steps():
            run = step.get("run", "")
            if step["name"] in allowed:
                continue
            if any(tool in run for tool in ("pytest", "pylint", "python ")):
                assert step.get("if") == GUARD, step["name"]

    def test_the_plan_gate_no_longer_hides_inside_the_checker_step(self):
        """Step 5b used to carry ``pytest tools/plan_gate``; a guarded copy is a second run."""
        by_name = {s["name"]: s for s in _lint_and_test_steps()}
        assert "tools/plan_gate" not in by_name["Test and apply custom pylint checkers"]["run"]
