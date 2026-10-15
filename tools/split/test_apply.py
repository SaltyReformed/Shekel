"""The split applied to the sample, and the static proof: green on the tool's own output, and
RED on every deliberately broken split the coordinator's ruling lists (a proof graded only by
its passes measures nothing)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools.split._layout import Layout
from tools.split._prove import prove_static
from tools.split._render import importer_rewrites, line_limit, render_all, write
from tools.split._source import collect_rules, read_module
from tools.split.conftest import SAMPLE, layout_of

PACKAGE = "tests/test_area/test_sample"


def applied(repo: Path) -> Layout:
    """Split the sample under its plan and return the layout the prover holds it to."""
    layout = layout_of(repo)
    texts = render_all(layout, line_limit(repo))
    write(layout, repo, texts, importer_rewrites(layout, repo))
    return layout


def proof(repo: Path, layout: Layout) -> list[str]:
    """The static proof's failures for the tree as it stands."""
    return prove_static(layout, repo, collect_rules(repo))


def text(repo: Path, module: str) -> str:
    """One output file's text."""
    return (repo / PACKAGE / f"{module}.py").read_text(encoding="utf-8")


def rewrite(repo: Path, module: str, old: str, new: str) -> None:
    """Replace ``old`` (which must occur exactly once) with ``new`` in one output."""
    current = text(repo, module)
    assert current.count(old) == 1, f"{old!r} occurs {current.count(old)} times in {module}"
    (repo / PACKAGE / f"{module}.py").write_text(current.replace(old, new), encoding="utf-8")


class TestTheSampleSplits:
    """The tool's own split of the sample proves, and has the shape the rulings set."""

    def test_the_static_proof_passes(self, repo):
        """Nothing fails on the tool's own output."""
        assert not proof(repo, applied(repo))

    def test_the_package_holds_exactly_its_files(self, repo):
        """__init__, conftest, _helpers and the three parts; the source is gone."""
        applied(repo)
        assert sorted(p.name for p in (repo / PACKAGE).iterdir()) == [
            "__init__.py", "_helpers.py", "conftest.py", "test_big_early.py",
            "test_big_late.py", "test_first.py"]
        assert not (repo / f"{PACKAGE}.py").exists()

    def test_init_is_the_module_docstring_verbatim(self, repo):
        """No re-export shim: the package's __init__ is the docstring alone."""
        applied(repo)
        assert text(repo, "__init__") == "\n".join(SAMPLE.splitlines()[:4]) + "\n"

    def test_each_part_imports_only_what_it_reads(self, repo):
        """test_first reads re, pytest and three helpers; nothing else."""
        applied(repo)
        imports = [ast.unparse(n) for n in ast.parse(text(repo, "test_first")).body
                   if isinstance(n, (ast.Import, ast.ImportFrom))]
        assert imports == ["import re", "import pytest",
                           "from ._helpers import _START, _make, _CASES"]

    def test_the_autouse_fixture_is_in_conftest_and_the_unused_import_where_placed(self, repo):
        """conftest holds the fixture; _helpers holds the placed unused import."""
        applied(repo)
        assert "def _frozen(monkeypatch):" in text(repo, "conftest")
        assert "unused_thing" in text(repo, "_helpers")

    def test_a_split_class_keeps_its_name_and_its_members_move_once(self, repo):
        """Each slice is TestBig on the mixin; the docstring and _shared are on the mixin."""
        applied(repo)
        assert "class TestBig(_TestBigMembers):" in text(repo, "test_big_early")
        assert "class TestBig(_TestBigMembers):" in text(repo, "test_big_late")
        helpers = text(repo, "_helpers")
        assert '"""The big class, which is split across two parts."""' in helpers
        assert helpers.count("def _shared(self):") == 1
        assert "# a comment above b" in text(repo, "test_big_early")

    def test_the_importer_is_pointed_at_helpers(self, repo):
        """The one importer now reads _make from the package's _helpers."""
        applied(repo)
        importer = (repo / "tests/test_other.py").read_text(encoding="utf-8")
        assert "from tests.test_area.test_sample._helpers import _make" in importer


class TestEveryProofArmFails:
    """Each arm, shown failing on a split broken by hand after the tool wrote it."""

    def test_a_dropped_test(self, repo):
        """A test deleted from its part is held zero times."""
        layout = applied(repo)
        rewrite(repo, "test_first", '''def test_top_level():
    """A module-level test."""
    assert _START.year == 2026
''', "")
        assert any("test_top_level is held 0 times" in f for f in proof(repo, layout))

    def test_a_duplicated_test(self, repo):
        """A test copied into a second part is held twice."""
        layout = applied(repo)
        current = text(repo, "test_first")
        start = current.index("def test_top_level")
        (repo / PACKAGE / "test_big_late.py").write_text(
            text(repo, "test_big_late") + "\n\n" + current[start:], encoding="utf-8")
        assert any("test_top_level is held 2 times" in f for f in proof(repo, layout))

    def test_a_dropped_comment_line(self, repo):
        """A comment line lost from a block is a line difference ast.dump cannot see."""
        layout = applied(repo)
        rewrite(repo, "_helpers", "# The day everything starts.\n", "")
        failures = proof(repo, layout)
        assert any("_START: lines differ" in f for f in failures)
        assert not any("_START: ast.dump" in f for f in failures)

    def test_a_reordered_pair(self, repo):
        """Two nodes swapped within a part are out of source order."""
        layout = applied(repo)
        current = text(repo, "test_first")
        cut = current.index("# -- First section")
        top = current.index("def test_top_level")
        swapped = current[:cut] + current[top:].rstrip("\n") + "\n\n\n" + current[cut:top]
        (repo / PACKAGE / "test_first.py").write_text(swapped.rstrip("\n") + "\n",
                                                      encoding="utf-8")
        assert any("out of source order" in f for f in proof(repo, layout))

    def test_an_autouse_fixture_left_in_a_part(self, repo):
        """The autouse fixture written into a part is held where fixtures may not live."""
        layout = applied(repo)
        conftest = text(repo, "conftest")
        fixture = conftest[conftest.index("@pytest.fixture"):]
        (repo / PACKAGE / "conftest.py").write_text(conftest[:conftest.index("@pytest.fixture")]
                                                    .rstrip("\n") + "\n", encoding="utf-8")
        (repo / PACKAGE / "test_first.py").write_text(
            text(repo, "test_first") + "\n\n" + fixture, encoding="utf-8")
        failures = proof(repo, layout)
        assert any("test_first: holds fixture _frozen" in f for f in failures)

    def test_a_file_relative_path(self, repo):
        """__file__ injected into a test is a line difference and an unresolved name."""
        layout = applied(repo)
        rewrite(repo, "test_first", "assert _START.year == 2026",
                "assert _START.year == 2026 and __file__")
        failures = proof(repo, layout)
        assert any("test_top_level: lines differ" in f for f in failures)
        assert any("reads __file__" in f for f in failures)

    def test_a_split_globals_pair(self, repo):
        """A node moved to a part the plan did not put it in is refused by placement, which
        is what keeps a globals() patcher beside its readers."""
        layout = applied(repo)
        current = text(repo, "test_first")
        start = current.index("def test_top_level")
        rewrite(repo, "test_first", current[start:], "")
        (repo / PACKAGE / "test_big_early.py").write_text(
            text(repo, "test_big_early") + "\n\n" + current[start:], encoding="utf-8")
        assert any("which the plan places in test_first" in f for f in proof(repo, layout))

    def test_an_unplaced_import(self, repo):
        """The placed unused import deleted is a source alias no output holds."""
        layout = applied(repo)
        rewrite(repo, "_helpers", "helper_one, helper_two, unused_thing",
                "helper_one, helper_two")
        assert any("held by no output: ['unused_thing']" in f for f in proof(repo, layout))

    def test_a_test_name_imported_into_a_part(self, repo):
        """A Test* name imported into a part would be collected twice."""
        layout = applied(repo)
        rewrite(repo, "test_big_late", "from ._helpers import _TestBigMembers",
                "from ._helpers import _TestBigMembers\nfrom tests.shared import TestShared")
        assert any("TestShared, which pytest would collect again" in f
                   for f in proof(repo, layout))

    def test_a_part_over_the_line_budget(self, repo):
        """A part padded past 999 lines is over the budget."""
        layout = applied(repo)
        rewrite(repo, "test_first", '"""The first section and the top-level test."""',
                '"""The first section and the top-level test.' + "\n" * 1000 + '"""')
        assert any("over the 1000-line budget" in f for f in proof(repo, layout))

    def test_a_hook_function(self, repo):
        """A pytest hook written into conftest is a node the source never held."""
        layout = applied(repo)
        (repo / PACKAGE / "conftest.py").write_text(
            text(repo, "conftest") + "\n\ndef pytest_collection_modifyitems(items):\n"
            '    """Reorder."""\n    items.reverse()\n', encoding="utf-8")
        assert any("pytest_collection_modifyitems is not in the source" in f
                   for f in proof(repo, layout))

    def test_a_slice_member_edited(self, repo):
        """A split class's test changed in its slice fails per member."""
        layout = applied(repo)
        rewrite(repo, "test_big_early", "assert self._shared() == 2",
                "assert self._shared() == 3")
        failures = proof(repo, layout)
        assert any("TestBig.test_a: lines differ" in f for f in failures)
        assert any("TestBig.test_a: ast.dump differs" in f for f in failures)

    def test_a_mixin_missing_its_docstring(self, repo):
        """The mixin must carry the class's docstring verbatim."""
        layout = applied(repo)
        rewrite(repo, "_helpers", '"""The big class, which is split across two parts."""',
                '"""The big class."""')
        assert any("not the class's docstring and members" in f for f in proof(repo, layout))

    def test_an_importer_left_pointing_at_the_source(self, repo):
        """An importer reverted to the old module path is still importing the source."""
        layout = applied(repo)
        path = repo / "tests/test_other.py"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "test_sample._helpers import", "test_sample import"), encoding="utf-8")
        assert any("still imports the source module" in f for f in proof(repo, layout))


@pytest.mark.parametrize("module", ["test_first", "test_big_early", "test_big_late",
                                    "conftest", "_helpers"])
def test_every_output_reads_back_through_the_strict_reader(repo, module):
    """Each output parses under the same refusals the source passed."""
    applied(repo)
    assert read_module(repo / PACKAGE / f"{module}.py", collect_rules(repo),
                       relative="_helpers").units
