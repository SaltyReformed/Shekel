"""The split applied to the sample, and the static proof: green on the tool's own output, and
RED on every deliberately broken split the coordinator's ruling lists (a proof graded only by
its passes measures nothing)."""
from __future__ import annotations

import ast
import errno
import re
from pathlib import Path

import pytest

from tools.split import _prove
from tools.split._dynamic import compare_pylint
from tools.split._layout import Layout
from tools.split._prove import import_block, line_origins, prove_static
from tools.split._render import (
    Origin,
    importer_rewrites,
    line_limit,
    render_all,
    text_of,
    write,
)
from tools.split._run import RunError, pylint_messages
from tools.split._source import Refusal, collect_rules, read_module
from tools.split.conftest import SAMPLE, SOURCE, layout_of, tree

PACKAGE = "tests/test_area/test_sample"


def applied(repo: Path, plan: dict | None = None) -> Layout:
    """Split the sample under ``plan`` (the sample plan when None) and return the layout the
    prover holds it to."""
    layout = layout_of(repo, plan)
    texts = render_all(layout, line_limit(repo))
    write(layout, repo, texts, importer_rewrites(layout, repo), {})
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


class TestAnImportOfTheSourceTheWriterCannotRepoint:
    """The writer and the identity screen ask ONE producer whether an import names the
    source (:func:`tools.split._source.imported`, a relative import resolved against the
    importer's package: the delta review's L-C), so they read a relative spelling alike."""

    @pytest.mark.parametrize("path, line, why", [
        ("tests/test_zz.py", "import tests.test_area.test_sample\n",
         "imports the source as a module"),
        ("tests/test_zz.py", "from tests.test_area import test_sample\n",
         "imports the source module by name from its package"),
        ("tests/test_area/test_zz.py", "from .test_sample import _make\n",
         "a relative import of the source"),
        ("tests/test_area/test_zz.py", "from . import test_sample\n",
         "a relative import of the source"),
        ("tests/test_area/deeper/test_zz.py", "from ..test_sample import _make\n",
         "a relative import of the source"),
    ], ids=["as-a-module", "by-name-from-its-package", "relative-from-a-sibling",
            "relative-by-name", "relative-from-a-package-below"])
    def test_each_is_refused(self, repo, path, line, why):
        """Each needs a hand rewrite: the tool points only ``from <source> import`` of names
        homed in _helpers.py."""
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(line, encoding="utf-8")
        with pytest.raises(Refusal, match=why):
            importer_rewrites(layout_of(repo), repo)

    def test_a_module_whose_name_only_begins_with_the_sources_is_not_an_importer(self, repo):
        """``test_sample_extra`` is another module, not one inside the source."""
        (repo / "tests/test_area/test_sample_extra.py").write_text("X = 1\n", encoding="utf-8")
        (repo / "tests/test_zz.py").write_text(
            "from tests.test_area.test_sample_extra import X\n", encoding="utf-8")
        assert [r.path for r in importer_rewrites(layout_of(repo), repo)] == [
            repo / "tests/test_other.py"]


def test_a_refused_write_leaves_the_tree_as_it_was(repo):
    """The importer's line changed between reading and writing: refused before the package
    is written or the source deleted (the review's F8a; the re-check ran after both)."""
    layout = layout_of(repo)
    texts = render_all(layout, line_limit(repo))
    rewrites = importer_rewrites(layout, repo)
    importer = repo / "tests/test_other.py"
    edited = importer.read_text(encoding="utf-8").replace(
        "import _make", "import _make  # edited", 1)
    importer.write_text(edited, encoding="utf-8")
    with pytest.raises(Refusal, match="changed since it was read"):
        write(layout, repo, texts, rewrites, {})
    assert (repo / SOURCE).read_text(encoding="utf-8") == SAMPLE
    assert not (repo / PACKAGE).exists()
    assert importer.read_text(encoding="utf-8") == edited


class TestAWriteThatFailsPartWay:
    """An :class:`OSError` mid-write undoes the write: the tree is byte-identical after it
    (the delta review's L-B: the one path that was not fail-closed for the tree)."""

    @staticmethod
    def prepared(repo: Path) -> tuple[Layout, dict[str, str], list, Path]:
        """The sample with a second importer, so one importer can be rewritten before the
        write fails at the next; its layout, texts, rewrites and the second importer."""
        second = repo / "tests/test_zz.py"
        second.write_text("from tests.test_area.test_sample import _make\n", encoding="utf-8")
        layout = layout_of(repo)
        rewrites = importer_rewrites(layout, repo)
        assert [r.path for r in rewrites] == [repo / "tests/test_other.py", second]
        return layout, render_all(layout, line_limit(repo)), rewrites, second

    @staticmethod
    def failing(monkeypatch, method: str, target: Path, seen: list, root: Path) -> None:
        """Make ``Path.<method>`` on ``target`` fail as a full disk does, a ``write_text``
        leaving the file empty; ``seen`` takes ``root``'s tree at that moment."""
        real, empty = getattr(Path, method), Path.write_bytes

        def fail(self, *args, **kwargs):
            if self != target:
                return real(self, *args, **kwargs)
            seen.append(tree(root))
            if method == "write_text":
                empty(self, b"")
            raise OSError(errno.ENOSPC, "No space left on device", str(self))
        monkeypatch.setattr(Path, method, fail)

    def test_after_the_package_is_written(self, repo, monkeypatch):
        """It fails deleting the source, with every file of the package written."""
        layout, texts, rewrites, _ = self.prepared(repo)
        before, seen = tree(repo), []
        self.failing(monkeypatch, "unlink", repo / SOURCE, seen, repo)
        with pytest.raises(RunError, match="every file it created is removed and every file "
                                           "it changed put back"):
            write(layout, repo, texts, rewrites, {})
        monkeypatch.undo()
        assert sorted(p for p in seen[0] if p.startswith(f"{PACKAGE}/")) == sorted(
            f"{PACKAGE}/{name}" for name in texts)
        assert tree(repo) == before
        assert not (repo / PACKAGE).exists()

    def test_after_an_importer_is_rewritten(self, repo, monkeypatch):
        """It fails rewriting the second importer, leaving it empty, with the first already
        re-pointed and the source deleted."""
        layout, texts, rewrites, second = self.prepared(repo)
        before, seen = tree(repo), []
        self.failing(monkeypatch, "write_text", second, seen, repo)
        with pytest.raises(RunError, match="every file it created is removed and every file "
                                           "it changed put back"):
            write(layout, repo, texts, rewrites, {})
        monkeypatch.undo()
        assert "test_sample._helpers import _make" in seen[0]["tests/test_other.py"].decode()
        assert SOURCE not in seen[0]
        assert tree(repo) == before
        assert not (repo / PACKAGE).exists()

    def test_an_undo_that_fails_is_named(self, repo, monkeypatch):
        """The second importer can be neither rewritten nor put back: the error names it,
        and the rest of the undo still ran."""
        layout, texts, rewrites, second = self.prepared(repo)
        self.failing(monkeypatch, "write_text", second, [], repo)
        self.failing(monkeypatch, "write_bytes", second, [], repo)
        with pytest.raises(RunError, match="NOT undone: " + re.escape(f"{second}: [Errno 28]")):
            write(layout, repo, texts, rewrites, {})
        monkeypatch.undo()
        assert second.read_bytes() == b""
        assert (repo / SOURCE).read_text(encoding="utf-8") == SAMPLE
        assert not (repo / PACKAGE).exists()

    def test_a_file_the_failed_step_never_touched_is_not_written_again(self, repo,
                                                                         monkeypatch):
        """The source's deletion fails, leaving it whole, and it could not be written either:
        the undo leaves it alone rather than naming it NOT undone."""
        layout, texts, rewrites, _ = self.prepared(repo)
        before = tree(repo)
        self.failing(monkeypatch, "unlink", repo / SOURCE, [], repo)
        self.failing(monkeypatch, "write_bytes", repo / SOURCE, [], repo)
        with pytest.raises(RunError, match="every file it created is removed and every file "
                                           "it changed put back"):
            write(layout, repo, texts, rewrites, {})
        monkeypatch.undo()
        assert tree(repo) == before

    def test_the_packages_space_is_freed_before_anything_is_put_back(self, repo, monkeypatch):
        """On a full disk the source can be written back only once the package's files are
        gone: the undo removes them first."""
        layout, texts, rewrites, second = self.prepared(repo)
        before, package, source = tree(repo), repo / PACKAGE, repo / SOURCE
        self.failing(monkeypatch, "write_text", second, [], repo)
        real = Path.write_bytes

        def full(self, data):
            if self == source and package.exists():
                raise OSError(errno.ENOSPC, "No space left on device", str(self))
            return real(self, data)
        monkeypatch.setattr(Path, "write_bytes", full)
        with pytest.raises(RunError, match="every file it created is removed and every file "
                                           "it changed put back"):
            write(layout, repo, texts, rewrites, {})
        monkeypatch.undo()
        assert tree(repo) == before


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
        rewrite(repo, "test_first", '"""The first section and the top-level test.\n',
                '"""The first section and the top-level test.\n' + "\n" * 1000)
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


class TestTheLineOrigins:
    """Each written line's origin, which the pylint arm judges every message by: green on the
    tool's own output, RED on a file or an origin claim the tree does not bear out."""

    def test_every_written_line_has_its_origin(self, repo):
        """No failure on the tool's own split; a copied line is its source line, a filtered
        import points at its source statement, and a composed line has no origin."""
        layout = applied(repo)
        origins, failures = line_origins(layout, repo)
        assert not failures
        assert sorted(origins) == sorted(str(p.relative_to(repo))
                                         for p in (repo / PACKAGE).glob("*.py"))
        init = origins[f"{PACKAGE}/__init__.py"]
        assert init == [Origin(n, n, copied=True) for n in range(1, len(init) + 1)]
        early = text(repo, "test_big_early").splitlines()
        lines = origins[f"{PACKAGE}/test_big_early.py"]
        assert lines[early.index("class TestBig(_TestBigMembers):")] is None
        member = lines[early.index("    def test_a(self):")]
        assert member.copied and layout.source.lines[member.first - 1] == "    def test_a(self):"
        shared = origins[f"{PACKAGE}/_helpers.py"][
            text(repo, "_helpers").splitlines().index("    def _shared(self):")]
        assert shared.copied and layout.source.lines[shared.first - 1] == "    def _shared(self):"
        statement = layout.source.lines.index("import re") + 1
        assert origins[f"{PACKAGE}/test_first.py"][
            text(repo, "test_first").splitlines().index("import re")] == Origin(
                statement, statement, copied=False)

    def test_a_file_edited_after_the_split(self, repo):
        """A part that is not what the plan renders has no origins to judge by."""
        layout = applied(repo)
        rewrite(repo, "test_first", '"""One."""', '"""One, edited."""')
        _, failures = line_origins(layout, repo)
        assert failures == [f"{PACKAGE}/test_first.py is not what the plan renders from the "
                            "recorded source"]

    @staticmethod
    def forged(monkeypatch, swap) -> None:
        """Make the writer's rendering claim a different origin for one line of test_first,
        its text unchanged, through ``swap(origin) -> origin | None`` (None: not this one)."""
        real = _prove.rendered

        def lying(layout, limit):
            files = real(layout, limit)
            lines = files["test_first.py"]
            index = next(i for i, (_, origin) in enumerate(lines)
                         if isinstance(origin, Origin) and swap(origin) is not None)
            lines[index] = (lines[index][0], swap(lines[index][1]))
            return files
        monkeypatch.setattr(_prove, "rendered", lying)

    def test_a_copy_claim_the_source_does_not_bear(self, repo, monkeypatch):
        """A line called a copy of a source line it is not is refused, not trusted."""
        layout = applied(repo)
        source = layout.source.lines
        self.forged(monkeypatch, lambda o: Origin(o.first + 1, o.first + 1, copied=True)
                    if o.copied and source[o.first - 1] != source[o.first] else None)
        _, failures = line_origins(layout, repo)
        assert len(failures) == 1 and "is called a copy of source line" in failures[0]

    def test_an_import_claim_on_lines_that_hold_no_import(self, repo, monkeypatch):
        """A line called part of an import from source lines that hold none is refused."""
        layout = applied(repo)
        self.forged(monkeypatch, lambda o: Origin(1, 1, copied=False) if not o.copied else None)
        _, failures = line_origins(layout, repo)
        assert len(failures) == 1 and "which hold no import" in failures[0]

    def test_a_composed_import_before_a_source_import(self, repo, monkeypatch):
        """test_first's ``_helpers`` import moved above its ``import pytest``, in the file
        and in the rendering alike: every origin claim holds, but a composed import now
        breaks the run of source imports the import-sequence reading rests on."""
        layout = applied(repo)
        real = _prove.rendered(layout, line_limit(repo))["test_first.py"]
        helpers = next(i for i, (line, _) in enumerate(real) if line.startswith("from ._"))
        source = next(i for i, (line, _) in enumerate(real) if line == "import pytest")
        moved = real[:source] + [real[helpers], real[source]] + [
            entry for i, entry in enumerate(real) if i > source and i != helpers]
        (repo / PACKAGE / "test_first.py").write_text(text_of(moved), encoding="utf-8")
        real_rendered = _prove.rendered
        monkeypatch.setattr(_prove, "rendered", lambda layout, limit: {
            **real_rendered(layout, limit), "test_first.py": moved})
        _, failures = line_origins(layout, repo)
        assert failures == [f"{PACKAGE}/test_first.py: an import the writer composed sits "
                            "before a source-derived one"]

    def test_the_samples_real_pylint_messages_are_judged_by_origin(self, repo):
        """pylint itself over the sample before and after: the placed unused import is
        matched to its source statement, and the mixin's composed ``class`` line, which the
        sample's minimal .pylintrc lets too-few-public-methods reach (the repository's
        disables it), is refused as new."""
        before = pylint_messages(repo, [SOURCE])
        layout = applied(repo)
        after = pylint_messages(repo, sorted(str(p.relative_to(repo))
                                             for p in (repo / PACKAGE).glob("*.py")))
        origins, failures = line_origins(layout, repo)
        assert not failures
        assert {(m["symbol"], m["path"]) for m in after} == {
            ("unused-import", f"{PACKAGE}/_helpers.py"),
            ("too-few-public-methods", f"{PACKAGE}/_helpers.py")}
        mixin = text(repo, "_helpers").splitlines().index("class _TestBigMembers:") + 1
        assert compare_pylint(before, after, origins, import_block(layout)) == [
            f"{PACKAGE}/_helpers.py:{mixin}: too-few-public-methods on a line the writer "
            "composed"]
