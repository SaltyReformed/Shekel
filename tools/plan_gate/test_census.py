"""Controls for the arm that RE-RUNS a planning row's census.

The defect these guard is not a wrong document; it is a document that was right
when it was written.  Five live rows on 2026-09-11 carried counts about `app/`
that the code had moved past -- every one of those rows correctly OPEN, so
nothing about their STATE was wrong and nothing looked stale.

**The first class is the pattern-matches-nothing guard**, which is rule 3's own
recorded failure mode one register down: the balance README's row-count arm
once required ``rows**`` where the document wrote ``rows.**``, matched NOTHING,
read that as "no count is claimed" and passed a planted 38-against-41.  An arm
over a convention this young is exactly where that can happen again.
"""
from __future__ import annotations

import re

import pytest

import _census
import _registry as registry


@pytest.fixture(name="stage_census")
def _stage_census(tmp_path, monkeypatch):
    """Return a helper that puts one census marker into a steps.md copy."""

    def _apply(marker: str) -> None:
        target = tmp_path / registry.STEPS.name
        target.write_text(registry.STEPS.read_text() + f"\n\n{marker}\n")
        monkeypatch.setattr(registry, "STEPS", target)

    return _apply


class TestTheArmIsGradingSomething:
    """A census arm that matches no marker reports clean and grades nothing."""

    def test_the_live_corpus_carries_a_census_to_grade(self):
        """At least one live document uses the marker this arm reads."""
        assert _census.documents_carrying_a_census(), (
            "no live document carries a census marker, so this arm grades "
            "NOTHING and reports clean -- conventions.md rule 6's census half "
            "is prose again"
        )

    def test_the_live_corpus_is_clean(self):
        """Every marker in the live documents re-runs to the number it states."""
        assert not _census.census_violations()


@pytest.fixture(name="fixture_tree")
def _fixture_tree(tmp_path, monkeypatch):
    """Plant a HAND-COUNTED Python tree under a fake `app/` and point the arm at it.

    **The counts below are counted by eye, not by `census_count`.**  Every
    earlier control derived its expectation by calling the very function it was
    grading, so a miscount moved both sides together and stayed green -- which
    `lessons.md` records as *"a census and a gate can be blind the same way, and
    then they confirm each other"*.

    It is also SYNTHETIC on purpose.  The first drafts pinned
    ``transfer_id is not None`` and ``app/ref_cache/_accessors.py``, which are
    exactly what plan steps `X-bi-6` and `X-ba` exist to DELETE: both controls
    would have failed on a correct edit, the pinned-data failure ``_staging``
    records four times over and which this package keeps re-paying.
    """
    app = tmp_path / "app"
    (app / "pkg").mkdir(parents=True)
    # 3 CODE lines hold NEEDLE, 2 COMMENT lines do, 2 STRING lines do.
    (app / "one.py").write_text(
        '"""A docstring mentioning NEEDLE, which is PROSE and not a use."""\n'
        "NEEDLE = 1\n"
        "x = NEEDLE  # NEEDLE in a comment\n"
        "y = 2  # another NEEDLE comment\n"
        "z = 'NEEDLE inside a string literal'\n",
        encoding="utf-8",
    )
    # 1 CODE line, 0 comments, 0 strings. A second FILE, so files != lines.
    (app / "pkg" / "two.py").write_text("NEEDLE = 2\n", encoding="utf-8")
    # No NEEDLE at all, so it must not count as a file.
    (app / "pkg" / "three.py").write_text("OTHER = 3\n", encoding="utf-8")
    monkeypatch.setattr(registry, "REPO", tmp_path)
    return {"code lines": 3, "code files": 2, "comments lines": 2, "lines": 6, "files": 2}


class TestTheWalkCountsWhatItSays:
    """`census_count` against a tree whose every number was counted by eye."""

    @pytest.mark.parametrize(
        "spec", ["code lines", "code files", "comments lines", "lines", "files"],
    )
    def test_each_unit_and_filter_matches_the_hand_count(self, fixture_tree, spec):
        """The arm's answer equals the number a human counted for that mode."""
        token_filter, _, unit = spec.rpartition(" ")
        paths = _census.census_paths("app/**/*.py")
        measured = _census.census_count(
            re.compile("NEEDLE"), paths, unit, token_filter or None,
        )
        assert measured == fixture_tree[spec], (spec, measured, fixture_tree[spec])

    def test_code_excludes_prose_and_comments_excludes_code(self, fixture_tree):
        """The two filters disagree, which is the whole reason they exist.

        `X-ah` counted four docstrings discussing its own census as uses, and
        `X-al` counted a docstring narrating a disable that had been REMOVED.
        Both read as precise; both invented work.
        """
        paths = _census.census_paths("app/**/*.py")
        raw = _census.census_count(re.compile("NEEDLE"), paths, "lines")
        code = _census.census_count(re.compile("NEEDLE"), paths, "lines", "code")
        comments = _census.census_count(re.compile("NEEDLE"), paths, "lines", "comments")
        assert raw > code > comments, (raw, code, comments)
        assert raw == fixture_tree["lines"] and code + comments < raw


class TestACensusIsReRunRatherThanRemembered:
    """The arm walks the code, so a marker cannot disagree with it and survive."""

    def test_the_live_corpus_is_clean(self):
        """Every marker in the live documents re-runs to the number it states."""
        assert not _census.census_violations()

    def test_the_control_fires_on_a_count_the_code_has_moved_past(self, stage_census):
        """A marker whose number is wrong is refused, with both figures named."""
        stage_census("(census 99999 lines `def ` in `app/**/*.py`)")
        problems = _census.census_violations()
        assert any("says 99999 lines and the code holds" in p for p in problems), problems

    def test_a_filtered_marker_reports_its_filter(self, stage_census):
        """A wrong `code`/`comments` marker names the filter in its message."""
        stage_census("(census 99999 code lines `def ` in `app/**/*.py`)")
        problems = _census.census_violations()
        assert any("says 99999 code lines" in p for p in problems), problems


class TestAMarkerTheParserCannotReadIsRefused:
    """A `(census ...)` the regex misses reads as NO census and its number is ungraded.

    Three existed the day this arm was written and the arm found SIX: trailing
    prose inside the parenthesis (twice, both added by the commit that fixed
    other findings), and a marker line-WRAPPED between `in` and its glob
    (three times, one predating all of it). That is a CLASS, and rule 3's own
    recorded failure mode one register down: a pattern matching nothing reads
    as silence and passes.
    """

    def test_the_live_corpus_has_no_near_miss(self):
        """Every `(census N ...)` in the live documents parses."""
        assert not _census.near_miss_violations()

    @pytest.mark.parametrize("broken", [
        "(census 3 lines `x` in `app/**/*.py`, and some trailing prose)",
        "(census 3 lines `x` in\n`app/**/*.py`)",
        "(census 3 `x` in `app/**/*.py`)",
        "(census 3 lines x in `app/**/*.py`)",
    ])
    def test_the_control_fires_on_each_way_a_marker_can_break(self, stage_census, broken):
        """Trailing prose, a line break, a missing unit and a bare pattern all fail."""
        stage_census(broken)
        assert _census.near_miss_violations(), broken

    def test_the_rule_s_own_grammar_example_is_not_a_near_miss(self):
        """`(census <N> ...)` in conventions.md is a PLACEHOLDER, exempt by SHAPE.

        The arm requires a DIGIT, so the rule that states the grammar is not
        graded as a broken instance of it -- and no allowlist is needed, which
        is the kind of fence this project deletes rather than maintains.
        """
        assert "(census <N>" in (registry.PLANS / "conventions.md").read_text(encoding="utf-8")
        assert not _census.near_miss_violations()


class TestACensusMayNotReachOutsideTheCode:
    """A marker names a path in the code trees, and anything else is refused."""

    @pytest.mark.parametrize("glob", [
        "../etc/passwd",
        "/etc/passwd",
        "docs/plans/steps.md",
        "app/../../secrets/**",
    ])
    def test_an_illegal_glob_is_refused(self, stage_census, glob):
        """Escaping the tree, or counting the planning documents, is not a census."""
        stage_census(f"(census 1 lines `x` in `{glob}`)")
        problems = _census.census_violations()
        assert any("names a path outside" in p for p in problems), (glob, problems)

    def test_a_symlink_out_of_the_tree_is_not_walked(self, tmp_path, monkeypatch):
        """A FILE symlink under `app/` that points outside is excluded.

        The four globs above are all refused by the leading-slash, `..` or
        `_ROOTS` arms before containment is consulted, so this is the only
        control that reaches `p.resolve().is_relative_to(root)`. CPython's `**`
        does not descend symlinked DIRECTORIES, which leaves a symlinked file as
        the reachable case.
        """
        (tmp_path / "app").mkdir()
        outside = tmp_path.parent / f"outside_{tmp_path.name}.py"
        outside.write_text("SECRET = 1\n", encoding="utf-8")
        (tmp_path / "app" / "linked.py").symlink_to(outside)
        (tmp_path / "app" / "real.py").write_text("SECRET = 2\n", encoding="utf-8")
        monkeypatch.setattr(registry, "REPO", tmp_path)
        paths = _census.census_paths("app/**/*.py")
        assert [p.name for p in paths] == ["real.py"], paths
        assert _census.census_count(re.compile("SECRET"), paths, "lines") == 1

    def test_a_glob_matching_no_file_is_refused(self, stage_census):
        """A census over nothing reports 0, which reads as a closed finding."""
        stage_census("(census 0 lines `x` in `app/no_such_directory/**/*.py`)")
        problems = _census.census_violations()
        assert any("matches no file at all" in p for p in problems), problems

    def test_a_pattern_that_will_not_compile_is_refused(self, stage_census):
        """A broken regex is reported, never swallowed into a 0."""
        stage_census("(census 0 lines `[unclosed` in `app/**/*.py`)")
        problems = _census.census_violations()
        assert any("will not compile" in p for p in problems), problems
