"""The run half of the proof: the id map, the collection and outcome comparisons, pylint's
comparison, the census, and the observer plugin.  Each comparison is shown FAILING on a
hand-built observation; the observer is run under a real pytest on the sample, before and
after the tool splits it."""
from __future__ import annotations

import errno
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tools.ci import gitcmd, scratch
from tools.ci.arcs import REPO
from tools.split import _dynamic as dynamic
from tools.split._observe import OBSERVE_ENV, verdict
from tools.split._prove import line_origins
from tools.split._render import Origin
from tools.split._run import RunError, census, mentions, merged_setups, python_files
from tools.split._source import Refusal
from tools.split.conftest import (
    BEFORE_IDS,
    PACKAGE,
    SAMPLE,
    SOURCE,
    build,
    layout_of,
    plan_dict,
    plan_of,
)
from tools.split.test_apply import applied


def mapping(repo: Path) -> dict[str, str]:
    """The sample's id map."""
    return dynamic.id_map(layout_of(repo), BEFORE_IDS)


class TestTheIdMap:
    """Only the file changes, and the map is total and one-to-one."""

    def test_each_id_keeps_its_class_test_and_parameters(self, repo):
        """A whole class, a module-level test and each slice of a split class."""
        found = mapping(repo)
        assert found[f"{SOURCE}::TestFirst::test_cases[b-2]"] == (
            f"{PACKAGE}/test_first.py::TestFirst::test_cases[b-2]")
        assert found[f"{SOURCE}::test_top_level"] == f"{PACKAGE}/test_first.py::test_top_level"
        assert found[f"{SOURCE}::TestBig::test_b"] == (
            f"{PACKAGE}/test_big_early.py::TestBig::test_b")
        assert found[f"{SOURCE}::TestBig::test_c"] == (
            f"{PACKAGE}/test_big_late.py::TestBig::test_c")
        assert len(found) == len(set(found.values())) == len(BEFORE_IDS)

    def test_an_id_from_another_file_is_refused(self, repo):
        """The map covers this source's tests and nothing else."""
        with pytest.raises(Refusal, match="is not a test of"):
            dynamic.id_map(layout_of(repo), ["tests/other.py::test_x"])

    def test_a_duplicated_id_is_not_one_to_one(self, repo):
        """Two old ids mapping to one new id is refused."""
        with pytest.raises(Refusal, match="one-to-one"):
            dynamic.id_map(layout_of(repo), BEFORE_IDS + BEFORE_IDS[:1])


class TestTheCollectionComparison:
    """The split must collect exactly the mapped ids."""

    def test_identical_passes(self, repo):
        """The mapped ids, collected once each, fail nothing."""
        found = mapping(repo)
        assert not dynamic.compare_collection(found, list(found.values()))

    @pytest.mark.parametrize("change, why", [
        (lambda ids: ids[1:], "not collected after the split"),
        (lambda ids: ids + [ids[0]], "collected twice"),
        (lambda ids: ids + [f"{PACKAGE}/test_first.py::test_new"], "mapped from nothing"),
        (lambda ids: [i.replace("[b-2]", "[b-3]") for i in ids], "mapped from nothing"),
    ])
    def test_each_difference_fails(self, repo, change, why):
        """A dropped id, a doubled one, a new one, and a changed parametrize id."""
        found = mapping(repo)
        assert any(why in f for f in dynamic.compare_collection(found, change(
            list(found.values()))))


class TestTheOutcomeComparison:
    """Every test must end the same way after the split, skip / xfail / xpass included."""

    @staticmethod
    def outcomes(found: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
        """A before / after pair that agrees."""
        before = {old: "passed" for old in found}
        before[f"{SOURCE}::TestBig::test_c"] = "skipped"
        before[f"{SOURCE}::TestBig::test_d"] = "xfailed"
        before[f"{SOURCE}::TestBig::test_e"] = "xpassed"
        return before, {found[old]: status for old, status in before.items()}

    def test_identical_passes(self, repo):
        """The same outcome for every id fails nothing."""
        found = mapping(repo)
        before, after = self.outcomes(found)
        assert not dynamic.compare_outcomes(found, before, after)

    @pytest.mark.parametrize("test, status", [
        ("TestBig::test_d", "xpassed"), ("TestBig::test_e", "passed"),
        ("TestBig::test_c", "passed"), ("TestFirst::test_one", "failed"),
        ("TestFirst::test_one", "error"), ("TestFirst::test_one", None),
    ])
    def test_each_changed_outcome_fails(self, repo, test, status):
        """xfail to xpass, xpass to pass, skip to pass, pass to fail or error, or not run."""
        found = mapping(repo)
        before, after = self.outcomes(found)
        new = found[f"{SOURCE}::{test}"]
        if status is None:
            del after[new]
        else:
            after[new] = status
        assert any(test in f for f in dynamic.compare_outcomes(found, before, after))

    def test_a_scope_compares_only_its_part(self, repo):
        """A part run alone is held to its own ids, and an extra id still fails."""
        found = mapping(repo)
        before, after = self.outcomes(found)
        scope = {o for o, n in found.items() if "test_big_late.py" in n}
        alone = {found[o]: after[found[o]] for o in scope}
        assert not dynamic.compare_outcomes(found, before, alone, scope)
        alone[found[f"{SOURCE}::test_top_level"]] = "passed"
        assert dynamic.compare_outcomes(found, before, alone, scope)


class TestThePylintComparison:
    """Each message in the parts judged by its origin: no message the source did not hold at
    the line (or the import statement) it came from, and none on a line the writer composed;
    duplicate-code is left to the pair-by-pair arm."""

    #: test_salary's measured message: ``import pytest`` (source :38) splits its app imports.
    APP = "Imports from package app are not grouped"

    @staticmethod
    def message(symbol: str, line: int, path: str = "a.py", text: str = "") -> dict:
        """One pylint JSON message."""
        return {"symbol": symbol, "line": line, "path": path, "message": text}

    @staticmethod
    def judge(before: list[dict], after: list[dict], origins: dict,
              block: tuple[int, int] = (1, 0)) -> list[str]:
        """The arm's failures; the source's import block is empty unless a case gives one."""
        return dynamic.compare_pylint(before, after, origins, block)

    @staticmethod
    def copied(*source_lines: int) -> list[Origin | None]:
        """A written file whose line N is a verbatim copy of ``source_lines[N - 1]``, or a
        line the writer composed where that entry is 0."""
        return [Origin(n, n, copied=True) if n else None for n in source_lines]

    def test_fewer_or_equal_passes(self):
        """The parts may hold fewer messages, never one the source did not."""
        before = [self.message("unused-import", 3), self.message("too-many-lines", 1),
                  self.message("unused-argument", 10)]
        after = [self.message("unused-import", 2)]
        assert not self.judge(before, after, {"a.py": self.copied(0, 3)})

    def test_a_kind_the_source_did_not_hold_there_fails(self):
        """A message whose symbol its source line never carried is new."""
        failures = self.judge([self.message("unused-import", 3)],
                                          [self.message("line-too-long", 1)],
                                          {"a.py": self.copied(3)})
        assert failures == ["a.py:1: line-too-long, which source line(s) 3-3 do not hold"]

    def test_a_source_message_answers_once_per_file(self):
        """Two messages of one symbol in one file, against the one the source held: the
        second is new."""
        failures = self.judge(
            [self.message("unused-argument", 7)],
            [self.message("unused-argument", 1), self.message("unused-argument", 1)],
            {"a.py": self.copied(7)})
        assert failures == ["a.py:1: unused-argument, which source line(s) 7-7 do not hold"]

    def test_a_message_on_a_composed_line_fails(self):
        """A docstring, a ``class`` line, a mixin's line, a blank or the ``_helpers`` import
        has no origin, whatever the source held."""
        failures = self.judge([self.message("too-few-public-methods", 9)],
                                          [self.message("too-few-public-methods", 2)],
                                          {"a.py": self.copied(1, 0)})
        assert failures == ["a.py:2: too-few-public-methods on a line the writer composed"]

    def test_an_import_two_parts_read_carries_its_message_into_both(self):
        """The shape test_savings_dashboard_service measured: one source statement (line 41)
        filtered into two parts, its one W9910 now on each."""
        statement = [None, Origin(41, 41, copied=False)]
        before = [self.message("shekel-private-module-import", 41)]
        after = [self.message("shekel-private-module-import", 2, path)
                 for path in ("p.py", "q.py")]
        assert not self.judge(before, after, {"p.py": statement, "q.py": statement})

    def test_an_import_matches_anywhere_on_its_source_statement(self):
        """A statement written over lines 40-44 in the source and on one line in a part, its
        source message on a later line of the statement: the origin is the statement, not
        its first line.  (Synthetic: every import message in the three files split so far
        sits on its statement's first line; an alias carries a line of its own.)"""
        before = [self.message("shekel-private-module-import", 42)]
        assert not self.judge(
            before, [self.message("shekel-private-module-import", 1)],
            {"a.py": [Origin(40, 44, copied=False)]})

    def test_a_new_message_hidden_by_a_vanished_one_fails(self):
        """The count per kind's hole: the source held two redefined-outer-name, the parts
        hold one, on a moved line whose source line held none.  A count reads 1 <= 2."""
        before = [self.message("redefined-outer-name", 10),
                  self.message("redefined-outer-name", 20)]
        after = [self.message("redefined-outer-name", 2)]
        failures = self.judge(before, after, {"a.py": self.copied(29, 30)})
        assert failures == ["a.py:2: redefined-outer-name, which source line(s) 30-30 do not "
                            "hold"]

    def test_an_import_sequence_flag_filtering_moved_is_the_sources_defect(self):
        """test_salary's shape: the source flags :39 (``from app.services import
        account_service``); a part that does not read it holds :40 next after ``import
        pytest``, and pylint flags :40 for the same package.  Answered from the import block."""
        before = [self.message("ungrouped-imports", 39, text=self.APP)]
        after = [self.message("ungrouped-imports", 19, text=self.APP)]
        origins = {"a.py": [None] * 18 + [Origin(40, 40, copied=False)]}
        assert not self.judge(before, after, origins, block=(6, 47))
        assert self.judge(before, after, origins), "the statement alone does not answer it"

    def test_an_import_sequence_flag_the_source_block_never_held_fails(self):
        """The source's import block is clean of the check: the part's flag is new."""
        failures = self.judge([self.message("wrong-import-order", 38, text='third party '
                                            'import "pytest" should be placed before x')],
                              [self.message("ungrouped-imports", 1, text=self.APP)],
                              {"a.py": [Origin(40, 40, copied=False)]}, block=(6, 47))
        assert failures == ["a.py:1: ungrouped-imports naming 'app', which neither source "
                            "line(s) 40-40 nor the source's import block (6-47) holds"]

    def test_an_import_sequence_flag_naming_another_package_fails(self):
        """The source block flags package app; a flag for package tests is another defect."""
        failures = self.judge([self.message("ungrouped-imports", 39, text=self.APP)],
                              [self.message("ungrouped-imports", 1, text="Imports from "
                                            "package tests are not grouped")],
                              {"a.py": [Origin(40, 40, copied=False)]}, block=(6, 47))
        assert failures == ["a.py:1: ungrouped-imports naming 'tests', which neither source "
                            "line(s) 40-40 nor the source's import block (6-47) holds"]

    def test_an_import_sequence_flag_on_a_composed_import_fails(self):
        """Only a source-derived statement may be answered from the block: the ``_helpers``
        import is the writer's own."""
        failures = self.judge([self.message("ungrouped-imports", 39, text=self.APP)],
                              [self.message("ungrouped-imports", 1, text=self.APP)],
                              {"a.py": [None]}, block=(6, 47))
        assert failures == ["a.py:1: ungrouped-imports on a line the writer composed"]

    def test_a_message_pylints_own_template_does_not_read_is_refused(self):
        """The defect is read back through pylint's template; a text it does not fit stops
        the arm rather than being matched on its symbol alone."""
        with pytest.raises(Refusal, match="does not read through its own template"):
            self.judge([], [self.message("ungrouped-imports", 1, text="something else")],
                       {"a.py": [Origin(40, 40, copied=False)]}, block=(6, 47))

    def test_duplicate_code_is_left_to_the_pair_by_pair_arm(self):
        """The source's duplicate-code count is structurally zero, so it is not judged here;
        :mod:`tools.split._similar` judges each pair (test_similar.py shows it refusing)."""
        assert not self.judge([self.message("unused-import", 3)],
                                          [self.message("unused-import", 1),
                                           self.message("duplicate-code", 1)],
                                          {"a.py": self.copied(3)})



class TestTheImportersAreJudgedByOrigin:
    """An importer's only changed lines are its re-pointed imports (the coordinator's ruling on
    the follow-up leaf's fork B): a message elsewhere is answered by its own line before or
    refused, and one on a re-pointed import is named."""

    message = staticmethod(TestThePylintComparison.message)

    def test_a_message_its_line_held_before_passes(self):
        """The same symbol on the same line, re-pointed or not, is the importer's own."""
        before = [self.message("unused-argument", 9, "t.py"), self.message("unused-import", 2,
                                                                           "t.py")]
        assert dynamic.compare_importers(before, before, {"t.py": {2}}) == ([], [])

    def test_a_new_message_on_an_unchanged_line_is_refused(self):
        """A change the split made to a module it did not move."""
        failures, named = dynamic.compare_importers(
            [], [self.message("no-member", 7, "t.py")], {"t.py": {2}})
        assert failures == ["t.py:7: no-member, which the importer's line 7 did not hold "
                            "before the split"] and not named

    def test_a_new_message_hidden_by_a_vanished_one_of_its_kind_is_refused(self):
        """The per-kind unit's hole: two unused-argument before, two after, one of them on a
        line that held none."""
        before = [self.message("unused-argument", 4, "t.py"),
                  self.message("unused-argument", 9, "t.py")]
        after = [self.message("unused-argument", 4, "t.py"),
                 self.message("unused-argument", 7, "t.py")]
        assert dynamic.compare_importers(before, after, {"t.py": {2}})[0] == [
            "t.py:7: unused-argument, which the importer's line 7 did not hold before the "
            "split"]

    def test_a_new_message_on_a_repointed_import_is_named_not_refused(self):
        """test_salary's shekel-private-module-import on the re-pointed statement, any of its
        lines: the commit's own edit, named in the proof block."""
        failures, named = dynamic.compare_importers(
            [], [self.message("shekel-private-module-import", 3, "t.py")], {"t.py": {2, 3}})
        assert not failures and named == ["t.py:3: shekel-private-module-import"]


class TestTheCensus:
    """The old module's name is found in every spelling, and a path into the package is
    not."""

    def test_the_pattern(self):
        """The file name, its directories, its dotted path; not a sibling or the package."""
        mention = mentions(plan_of())
        assert sum(bool(mention.search(line)) for line in [
            "see tests/test_area/test_sample.py::TestBig",
            "``test_sample.py``",
            "from tests.test_area.test_sample import x",
            "from tests.test_area.test_sample._helpers import x",
            "tests/test_area/test_sample/test_first.py",
            "test_sample_other.py",
            "my_test_sample.py",
        ]) == 3

    @pytest.mark.parametrize("line, listed", [
        ("``test_sample.TestBig``'s ``test_a``", True),
        ("the far-leg cases in `test_sample`", True),
        ("``test_sample.test_top_level``", True),
        ("``test_sample.test_first_thing``", True),
        ("graded in test_sample.", True),
        ("moved from test_sample.py to test_sample/test_first.py", True),
        ("tests/test_area/test_sample/test_first.py", False),
        ("the test_sample/ package", False),
        ("from tests.test_area.test_sample.test_first import x", False),
        ("from tests.test_area.test_sample.conftest import x", False),
    ])
    def test_every_spelling_of_the_name(self, line, listed):
        """The unit is the module's NAME (the coordinator's ruling, 2026-10-15): a member
        (``m.Member``), a bare ``m``, a module-level test spelled in lowercase, a part's name
        as a mere PREFIX and a sentence's full stop are all mentions; only a continuation
        into the package (``m/``, ``m.<part>``, ``m.conftest``) is not, and a line holding
        both is listed."""
        assert bool(mentions(plan_of()).search(line)) is listed

    #: The lines of :meth:`tracked_notes` the census lists.
    NOTES_HIT = [("docs/notes.md", 1, "see ``test_sample.TestBig``"),
                 ("vendor/notes.md", 1, "see ``test_sample.TestFirst``")]

    @staticmethod
    def tracked_notes(parent: Path) -> Path:
        """A throwaway repository, built by :mod:`tools.ci.scratch` (which starts git in it and
        in no other), tracking a document that holds a mention of the sample's module beside a
        line holding the name as a mere substring and a path into the package, and a second
        mention force-tracked under a directory ``.gitignore`` names, which only this
        repository's own index lists (the coordinator's ruling on the delta review's M-B: an
        answer no call bound to another repository can produce); its root."""
        root = scratch.repository(parent)
        (root / ".gitignore").write_text("vendor/\n", encoding="utf-8")
        for path, text in {"docs/notes.md": "see ``test_sample.TestBig``\ntest_sample_other.py\n"
                                            "tests/test_area/test_sample/test_first.py\n",
                           "vendor/notes.md": "see ``test_sample.TestFirst``\n"}.items():
            (root / path).parent.mkdir()
            (root / path).write_text(text, encoding="utf-8")
        scratch.run(root, "add", "-A")
        scratch.run(root, "add", "-f", "vendor/notes.md")
        return root

    def test_the_census_reads_the_tracked_tree(self, tmp_path):
        """The real reader, ``git grep``, on a repository: the one mention among lines that
        only hold the name as a substring or continue into the package, and the mention
        tracked under an ignored directory, which git does not ignore because it tracks it
        (``git grep --untracked``, the census's reader until this was measured, skipped it)."""
        assert census(self.tracked_notes(tmp_path), plan_of()) == self.NOTES_HIT

    def test_the_census_reads_the_whole_tree(self, tmp_path):
        """No directory list: a top-level README and a migration's docstring are lines a
        reader follows, and the six roots the census once read missed both (lane A's live
        misses, README.md:440 and a migration's :14; the branch's neutral review, F2)."""
        root = self.tracked_notes(tmp_path)
        for path in ("README.md", "migrations/versions/a1_rename.py"):
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text("first\n``test_sample.TestFirst`` holds it\n",
                                     encoding="utf-8")
        scratch.run(root, "add", "-A")
        found = census(root, plan_of())
        assert [(path, line) for path, line, _ in found] == [
            ("README.md", 2), ("docs/notes.md", 1), ("migrations/versions/a1_rename.py", 2),
            ("vendor/notes.md", 1)]

    def test_the_census_reads_untracked_files_and_honours_gitignore(self, tmp_path):
        """``prove`` runs before the new package is tracked, so the census reads untracked
        files (the branch's neutral review, F1); an ignored file is no reader's line."""
        root = self.tracked_notes(tmp_path)
        (root / ".gitignore").write_text("vendor/\nbuild/\n", encoding="utf-8")
        for path in ("docs/draft.md", "build/out.md"):
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text("``test_sample.TestBig``\n", encoding="utf-8")
        found = census(root, plan_of())
        assert [(path, line) for path, line, _ in found] == [
            ("docs/draft.md", 1), ("docs/notes.md", 1), ("vendor/notes.md", 1)]

    def test_a_tree_git_ignores_whole_is_no_census(self, tmp_path):
        """With no file to name, the census reads none: ``git grep --no-index`` given no path
        would read every file under the root, the ignored ones too."""
        root = scratch.repository(tmp_path)
        (root / ".git" / "info" / "exclude").write_text("*\n", encoding="utf-8")
        (root / "build").mkdir()
        (root / "build" / "out.md").write_text("``test_sample.TestBig``\n", encoding="utf-8")
        assert census(root, plan_of()) == []

    def test_a_path_is_read_as_the_characters_it_holds(self, tmp_path):
        """A ``*`` in a listed path is that character, never a pattern that reaches a file
        git ignores beside it."""
        root = self.tracked_notes(tmp_path)
        (root / ".gitignore").write_text("vendor/\ndocs/ab.md\n", encoding="utf-8")
        for name in ("a*.md", "ab.md"):
            (root / "docs" / name).write_text("``test_sample.TestBig``\n", encoding="utf-8")
        found = census(root, plan_of())
        assert [path for path, _, _ in found] == ["docs/a*.md", "docs/notes.md",
                                                  "vendor/notes.md"]

    def test_a_grep_that_cannot_start_is_a_run_error(self, tmp_path, monkeypatch):
        """The tree's files are git's arguments, so a tree too large for the system's argument
        list fails to start git (E2BIG): an error naming how many files, never a traceback
        (the coordinator's condition on the one-walk census, 2026-10-15)."""
        root = self.tracked_notes(tmp_path)
        real = gitcmd.run

        def no_grep(where: Path, *args: str):
            if "grep" in args:
                raise OSError(errno.E2BIG, "Argument list too long", "git")
            return real(where, *args)
        monkeypatch.setattr(gitcmd, "run", no_grep)
        with pytest.raises(RunError, match="could not start over the tree's 3 files"):
            census(root, plan_of())

    def test_a_grep_column_setting_is_kept_out_of_the_record(self, tmp_path):
        """``grep.column`` set in the repository's config (or a user's) would put a column
        number between the line number and the text; ``--no-column`` keeps each record path,
        line and text (the delta review's M-C, census L2)."""
        root = self.tracked_notes(tmp_path)
        scratch.run(root, "config", "grep.column", "true")
        assert census(root, plan_of()) == self.NOTES_HIT

    def test_each_record_is_read_whole(self, tmp_path):
        """A path git would quote (a non-ASCII name) or that holds a colon, and a line holding
        a form feed (a line break to ``str.splitlines``, not to git) each come back as the
        file and line they are; a binary file holds no line and is skipped."""
        root = self.tracked_notes(tmp_path)
        (root / "docs" / "café:notes.md").write_text(
            "x\fsee ``test_sample.TestBig``\n", encoding="utf-8")
        (root / "docs" / "blob.bin").write_bytes(b"\0test_sample.TestBig\n")
        scratch.run(root, "add", "-A")
        found = census(root, plan_of())
        assert found == [("docs/café:notes.md", 1, "x\fsee ``test_sample.TestBig``"),
                         *self.NOTES_HIT]

    def test_a_carriage_return_inside_a_matched_line_is_an_error(self, tmp_path):
        """git's output is read as text, which ends a line at a lone carriage return too, so
        the rest of the line comes back as a record with no path: an error naming why, never
        the line cut short and listed (the review's L1)."""
        root = self.tracked_notes(tmp_path)
        (root / "docs" / "cr.md").write_bytes(b"see ``test_sample.TestBig``\rand more\n")
        with pytest.raises(RunError, match="a carriage return in a matched line ends a record"):
            census(root, plan_of())

    def test_a_crlf_line_end_is_read_as_the_lines_end(self, tmp_path):
        """A carriage return that ends a line is read as its end: the line comes back whole,
        without it, and nothing stops."""
        root = self.tracked_notes(tmp_path)
        (root / "docs" / "crlf.md").write_bytes(b"see ``test_sample.TestBig``\r\n")
        assert census(root, plan_of()) == [
            ("docs/crlf.md", 1, "see ``test_sample.TestBig``"), *self.NOTES_HIT]

    def test_a_matched_line_not_in_utf8_is_refused_naming_its_file(self, tmp_path):
        """git's output is decoded strictly, so a matched line it cannot decode stops the
        census rather than coming back misread: refused, the refusal showing the record that
        holds the byte, its path first, where it once raised UnicodeDecodeError (the delta
        review's L-B)."""
        root = self.tracked_notes(tmp_path)
        (root / "docs" / "latin.md").write_bytes(b"see ``test_sample.TestBig`` \xe9\n")
        with pytest.raises(Refusal, match=re.escape(
                "git grep's output: not UTF-8 text (invalid continuation byte at byte 44, in "
                "b'docs/latin.md\\x001\\x00see ``test_sample.TestBig`` \\xe9')")):
            census(root, plan_of())

    @pytest.mark.parametrize("binding", scratch.BINDINGS)
    def test_a_bound_environment_reaches_no_repository_but_the_one_named(
            self, binding, tmp_path, monkeypatch):
        """The 2026-10-04 incident's mechanism, which this class's first census test repeated
        on 2026-10-15: it started git as a plain subprocess, and under the pre-commit hook of
        a linked worktree its ``git init`` wrote ``core.bare=true`` into the shared
        repository's config.  With this process bound to a sentinel (proven bound, its
        indexes not empty: ``scratch._prove_bound``), the census answers the directory named
        and the sentinel -- its refs, config, HEADs and indexes -- is byte-identical
        afterwards.  The mention tracked under an ignored directory is what tells the two
        apart: a walk bound to the sentinel lists the named directory's other files as files
        the sentinel does not track, and only the named repository's own index lists that one
        (the delta review's M-B: a plain call once gave the bound and the named repository
        the same answer here).  The trailer rules and the tracker tool are held the same way
        (``tools/ci/test_trailers.py``, ``tools/quill/test__git.py``)."""
        sentinel, before = scratch.bound_sentinel(tmp_path, binding, monkeypatch.setenv)
        assert census(self.tracked_notes(tmp_path), plan_of()) == self.NOTES_HIT
        assert scratch.sentinel_state(sentinel) == before

    def test_archived_documents_are_classified(self, tmp_path):
        """The plan gate's rule from its one home (:mod:`tools.ci.archive`; the review's F3):
        archive/ and historical/ at any depth, and a first line, past leading blank lines,
        that STARTS with the banner, are records.  A live module whose first line merely
        holds ``ARCHIVED`` is live: ``tests/test_routes/test_archived_definition_edit.py``
        is one, and the rule this replaced would have hidden it."""
        for path, first in {"docs/archive/a.md": "x", "docs/plans/historical/b.md": "x",
                            "docs/c.md": "> **ARCHIVED.** old", "docs/d.md": "\n\n> **ARCHIVED.**",
                            "docs/e.md": "# Live", "tests/f.py": '"""ARCHIVED definitions."""',
                            "docs/g.md": "The banner > **ARCHIVED.** is quoted here"}.items():
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / path).write_text(first + "\n", encoding="utf-8")
        hits = dynamic.classify(tmp_path, [
            ("docs/archive/a.md", 3, "x"), ("docs/plans/historical/b.md", 4, "x"),
            ("docs/c.md", 5, "x"), ("docs/d.md", 6, "x"), ("docs/e.md", 7, "x"),
            ("tests/f.py", 8, "x"), ("docs/g.md", 9, "x")], {})
        assert [h.archived for h in hits] == [True, True, True, True, False, False, False]

    #: The sample's docstrings, each naming the old module once: a part's own plan text and a
    #: slice's, which a plan author wrote and the census must list.
    PART_TEXT = "The first section; the twin of test_sample.TestBig's opening tests."
    SLICE_TEXT = "test_a and test_b, which test_sample.py held first."

    def package_hits(self, tmp_path: Path) -> list[dynamic.Hit]:
        """The sample tracked in a repository, one of its tests citing the old path in its
        docstring and a part's and a slice's plan text naming the old module, then split and
        its package LEFT UNTRACKED, as ``prove`` finds it; the real census over it,
        classified with the verified origins (the review's F1: a hand scan of the package
        never ran the reader that missed it)."""
        root = scratch.repository(tmp_path)
        build(root, SAMPLE.replace(
            '"""One."""', '"""One; its twin lives in tests/test_area/test_sample.py."""'))
        scratch.run(root, "add", "-A")
        plan = plan_dict()
        plan["parts"][0]["docstring"] = self.PART_TEXT
        plan["parts"][1]["nodes"][0]["docstring"] = self.SLICE_TEXT
        layout = applied(root, plan)
        origins, failures = line_origins(layout, root)
        assert not failures
        assert not scratch.run(root, "ls-files", PACKAGE), "the package must be untracked"
        return dynamic.classify(root, census(root, layout.plan), origins)

    def test_the_tools_own_provenance_is_not_counted(self, tmp_path):
        """Each part's "Split from" paragraph and conftest.py's and _helpers.py's docstrings
        name the source by design: the tool composed them, and only they are dropped."""
        provenance = [h for h in self.package_hits(tmp_path) if h.provenance]
        assert sorted(Path(h.path).name for h in provenance) == [
            "_helpers.py", "conftest.py", "test_big_early.py", "test_big_late.py",
            "test_first.py"]
        assert all("test_sample.py" in h.text for h in provenance)

    def test_a_pointer_inside_the_package_is_counted(self, tmp_path):
        """The moved docstring citing the old path is the source's line, and a part's and a
        slice's plan text is the plan author's: each stays live although it sits inside the
        new package, and the importer's re-pointed import continues into the package."""
        live = [(Path(h.path).name, h.text) for h in self.package_hits(tmp_path)
                if not h.provenance and not h.archived]
        assert live == [
            ("test_big_early.py", f'"""{self.SLICE_TEXT}"""'),
            ("test_first.py", f'"""{self.PART_TEXT}'),
            ("test_first.py", '"""One; its twin lives in tests/test_area/test_sample.py."""')]


class TestTheImporterWalk:
    """Where a module importing the source may live: every ``.py`` file of the working tree
    git does not ignore, tracked or not, and present on disk (the coordinator's ruling on
    review fork L7).  Which files that reaches is graded end to end
    (``test_split.test_importers_are_found_wherever_git_does_not_ignore_them``)."""

    @staticmethod
    def walked(parent: Path) -> tuple[Path, list[Path]]:
        """A throwaway repository holding a tracked module, a tracked document, a tracked
        module deleted from disk, and a module tracked under a directory ``.gitignore``
        ignores (which only this repository's own index lists); its root, and the modules
        the walk lists."""
        root = scratch.repository(parent)
        (root / ".gitignore").write_text("vendor/\n", encoding="utf-8")
        for path in ("migrations/kept.py", "README.md", "gone.py", "vendor/kept.py"):
            (root / path).parent.mkdir(exist_ok=True)
            (root / path).write_text('"""x"""\n', encoding="utf-8")
        scratch.run(root, "add", "-A")
        scratch.run(root, "add", "-f", "vendor/kept.py")
        (root / "gone.py").unlink()
        return root, [root / "migrations/kept.py", root / "vendor/kept.py"]

    def test_only_python_files_on_disk_that_git_does_not_ignore(self, tmp_path):
        """A tracked module deleted from disk has no text to re-point, and a document is no
        module; a tracked module is listed though ``.gitignore`` names its directory, because
        git ignores only what it does not track."""
        root, listed = self.walked(tmp_path)
        assert python_files(root) == listed

    @pytest.mark.parametrize("binding", scratch.BINDINGS)
    def test_a_bound_environment_reaches_no_repository_but_the_one_named(
            self, binding, tmp_path, monkeypatch):
        """The walk is a git call, started through :mod:`tools.ci.gitcmd` like the census's
        (:meth:`TestTheCensus.test_a_bound_environment_reaches_no_repository_but_the_one_named`):
        bound to a sentinel, it lists the directory named, and the sentinel is
        byte-identical afterwards.  The module tracked under an ignored directory is what
        tells the two apart: a plain call bound to the sentinel's linked worktree still lists
        the directory's other modules, as files that worktree does not track, and only this
        repository's own index lists that one (measured on a plain-call mutant, 2026-10-15)."""
        sentinel, before = scratch.bound_sentinel(tmp_path, binding, monkeypatch.setenv)
        root, listed = self.walked(tmp_path)
        assert python_files(root) == listed
        assert scratch.sentinel_state(sentinel) == before


class TestTheFixtureSetups:
    """Every fixture the record run SETS UP, read through pytest's public hook (the
    coordinator's ruling on the follow-up leaf's fork F): one whose reach the split would
    change is refused, on hand-built observations and under a real pytest, lone and with
    xdist workers."""

    #: Where the source module's functions are defined, as the hook resolves it.
    SOURCE_FILE = "/repo/tests/test_area/test_sample.py"

    @staticmethod
    def setup(argname: str, scope: str = "function", baseid: str = "",
              file: str | None = "/venv/plugin.py") -> dict:
        """One fixture set up."""
        return {"argname": argname, "scope": scope, "baseid": baseid, "file": file,
                "by": f"{SOURCE}::test_top_level"}

    def judge(self, *setups: dict, ran: tuple[str, ...] = ("t",)) -> list[str]:
        """The arm's failures for a run that reported one test, ``t``."""
        return dynamic.fixture_failures({"outcomes": {"t": "passed"}, "setups": list(setups),
                                         "setups_ran": list(ran)}, SOURCE, self.SOURCE_FILE)

    def test_function_and_session_fixtures_from_anywhere_pass(self):
        """A plugin's or a parent conftest's fixture whose instance is per test or per run."""
        assert not self.judge(self.setup("monkeypatch"), self.setup("db", "session", "tests"),
                              self.setup("_frozen", baseid=SOURCE, file=self.SOURCE_FILE))

    @pytest.mark.parametrize("scope", ["module", "class", "package"])
    def test_a_scoped_fixture_defined_outside_the_source_is_refused(self, scope):
        """Its one instance per module, class or package may be cut into one per part."""
        failures = self.judge(self.setup("area", scope, "tests", "/repo/tests/conftest.py"))
        assert len(failures) == 1 and "may be cut into one per part" in failures[0]

    def test_a_scoped_fixture_of_the_source_itself_passes(self):
        """A class-scoped fixture a whole class defines moves with that class."""
        assert not self.judge(self.setup("_once", "class", f"{SOURCE}::TestFirst",
                                         self.SOURCE_FILE))

    @pytest.mark.parametrize("baseid", [SOURCE, f"{SOURCE}::TestFirst"])
    def test_an_imported_fixture_is_refused(self, baseid):
        """Registered on the source by an import, it would reach one part only."""
        failures = self.judge(self.setup("shared", baseid=baseid, file="/repo/tests/fx.py"))
        assert len(failures) == 1 and "registered on the source but defined in another file" in (
            failures[0])

    def test_a_scoped_fixture_whose_file_cannot_be_read_is_refused(self):
        """No code object to read: treated as defined elsewhere."""
        assert self.judge(self.setup("odd", "module", "tests", None))

    def test_a_test_no_process_recorded_is_refused(self):
        """A worker whose file is missing would leave its fixtures unseen."""
        failures = self.judge(ran=())
        assert failures == ["no process recorded the fixtures of 1 test(s) the run reported, "
                            "e.g. ['t']"]

    #: A module that reaches a parent conftest's module-scoped fixture and an imported one.
    REACHING = textwrap.dedent('''\
        """Fixtures from elsewhere."""
        import pytest

        from tests.fixtures_mod import shared_fx


        @pytest.fixture
        def own_fx():
            """Its own."""
            return 1


        def test_uses(shared_fx, area_fx, own_fx):
            """All three."""
            assert shared_fx + area_fx + own_fx == 3


        def test_twice(area_fx):
            """The module fixture again."""
            assert area_fx == 1
        ''')

    @staticmethod
    def reaching_repo(root: Path) -> Path:
        """The sample repository plus :attr:`REACHING` at ``tests/test_area/test_fx.py``, a
        module-scoped fixture in the area's conftest and a fixture module it imports."""
        build(root)
        for path, text in {
            "tests/test_area/test_fx.py": TestTheFixtureSetups.REACHING,
            "tests/test_area/conftest.py": '"""Area."""\nimport pytest\n\n\n'
                                           '@pytest.fixture(scope="module")\ndef area_fx():\n'
                                           '    """Module."""\n    return 1\n',
            "tests/fixtures_mod.py": '"""Shared."""\nimport pytest\n\n\n@pytest.fixture\n'
                                     'def shared_fx():\n    """Shared."""\n    return 1\n',
        }.items():
            (root / path).write_text(text, encoding="utf-8")
        return root

    @staticmethod
    def run(root: Path, target: str, *args: str) -> dict:
        """pytest on ``target`` with the observer, then every process's setups merged by
        the reader ``record`` uses."""
        observed = TestTheObserver.observed(root, target, False, *args)
        observed["setups"], observed["setups_ran"] = merged_setups(root / "observed.json")
        return observed

    @pytest.mark.parametrize("workers", [(), ("-n", "2")], ids=["lone", "xdist"])
    def test_the_hook_refuses_what_the_run_set_up(self, tmp_path, workers):
        """The parent conftest's module fixture and the imported one, each named once; the
        module's own fixture and pytest's and plugins' function fixtures pass."""
        root = self.reaching_repo(tmp_path)
        target = "tests/test_area/test_fx.py"
        observed = self.run(root, target, *workers)
        assert sorted(observed["outcomes"].values()) == ["passed", "passed"]
        failures = dynamic.fixture_failures(observed, target, str((root / target).resolve()))
        assert sorted(f.split(" (")[0] for f in failures) == [
            "fixture area_fx", "fixture shared_fx"]

    @pytest.mark.parametrize("workers", [(), ("-n", "2")], ids=["lone", "xdist"])
    def test_the_sample_sets_up_nothing_to_refuse(self, repo, workers):
        """The sample's own autouse fixture and the plugins' are no reason to refuse."""
        observed = self.run(repo, SOURCE, *workers)
        assert len(observed["outcomes"]) == len(BEFORE_IDS)
        assert not dynamic.fixture_failures(observed, SOURCE, str((repo / SOURCE).resolve()))

    def test_a_missing_worker_file_is_seen(self, repo):
        """One xdist worker's file deleted: its tests are no process's, and refused."""
        observed = self.run(repo, SOURCE, "-n", "2")
        files = sorted(repo.glob("observed.json.setups-gw*.json"))
        assert len(files) == 2, "both workers must have written"
        busiest = max(files, key=lambda f: len(json.loads(f.read_text(encoding="utf-8"))["ran"]))
        busiest.unlink()
        observed["setups"], observed["setups_ran"] = merged_setups(repo / "observed.json")
        failures = dynamic.fixture_failures(observed, SOURCE, str((repo / SOURCE).resolve()))
        assert len(failures) == 1 and "no process recorded the fixtures" in failures[0]


class TestTheObserver:
    """The plugin's verdicts, and the plugin run under pytest on the sample."""

    @pytest.mark.parametrize("phases, status", [
        ({"setup": ("passed", False), "call": ("passed", False)}, "passed"),
        ({"setup": ("passed", False), "call": ("failed", False)}, "failed"),
        ({"setup": ("failed", False)}, "error"),
        ({"setup": ("skipped", False)}, "skipped"),
        ({"setup": ("skipped", True)}, "xfailed"),
        ({"setup": ("passed", False), "call": ("skipped", True)}, "xfailed"),
        ({"setup": ("passed", False), "call": ("passed", True)}, "xpassed"),
        ({"setup": ("passed", False), "call": ("passed", False),
          "teardown": ("failed", False)}, "passed+teardown-error"),
    ])
    def test_verdict(self, phases, status):
        """Each phase combination names its outcome."""
        assert verdict(phases) == status

    @staticmethod
    def observed(root: Path, target: str, collect_only: bool, *extra: str) -> dict:
        """Run pytest on ``target`` in ``root`` with the observer, as the tool's runs do
        (``extra``: more pytest arguments, such as xdist's ``-n``)."""
        out = root / "observed.json"
        env = dict(os.environ)
        env[OBSERVE_ENV] = str(out)
        env["PYTHONPATH"] = str(REPO)
        args = [sys.executable, "-m", "pytest", target, "-c", "pytest.ini", "-q",
                "-p", "tools.split._observe", "-p", "no:cacheprovider", *extra]
        subprocess.run(args + (["--collect-only"] if collect_only else []), cwd=root,
                       env=env, capture_output=True, text=True, check=False)
        return json.loads(out.read_text(encoding="utf-8"))

    def test_the_split_sample_is_the_same_suite(self, repo):
        """Collected and run before and after: the same ids under the map, the same
        outcomes (pass, skip, xfail and xpass among them), and each part alone too."""
        before = self.observed(repo, SOURCE, collect_only=False)
        collected = self.observed(repo, SOURCE, collect_only=True)["collected"]
        assert sorted(collected) == sorted(BEFORE_IDS)
        assert dynamic.outcome_counts(before["outcomes"]) == {
            "passed": 6, "skipped": 1, "xfailed": 1, "xpassed": 1}
        layout = applied(repo)
        found = dynamic.id_map(layout, collected)
        after = self.observed(repo, PACKAGE, collect_only=False)
        assert not dynamic.compare_collection(
            found, self.observed(repo, PACKAGE, collect_only=True)["collected"])
        assert not dynamic.compare_outcomes(found, before["outcomes"], after["outcomes"])
        for module in ("test_first", "test_big_early", "test_big_late"):
            target = f"{PACKAGE}/{module}.py"
            alone = self.observed(repo, target, collect_only=False)["outcomes"]
            scope = {o for o, n in found.items() if n.startswith(target + "::")}
            assert scope and not dynamic.compare_outcomes(
                found, before["outcomes"], alone, scope)
