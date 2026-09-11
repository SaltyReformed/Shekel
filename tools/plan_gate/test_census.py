"""Controls for the arm that RE-RUNS a planning row's census.

The defect these guard is not a wrong document; it is a document that was right
when it was written.  Five live rows on 2026-09-11 carried counts about `app/`
that the code had moved past -- every one of those rows correctly OPEN, so
nothing about their STATE was wrong and nothing looked stale.

**The first class is the blindness guard**, because an arm that cannot run is
worse than no arm: this walk was first measured on a Python below the PEP 701
floor, three markers went into the documents at that interpreter's numbers, and
CI re-ran them on 3.14 to different figures against the same code.

**The second is the pattern-matches-nothing guard**, which is rule 3's own
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
    # 3 lines hold NEEDLE and only 2 are CODE: an f-string's interpolation is a
    # real use of the name, and the literal text around it is prose.  Before
    # PEP 701 no tokenizer could say so, which is the whole of `X-bp`'s census
    # being measured at 24 and re-measured by CI at 26.
    (app / "pkg" / "four.py").write_text(
        'LITERAL = f"NEEDLE here is literal text, so it is prose"\n'
        'INSIDE = f"{NEEDLE}"\n'
        'BOTH = f"NEEDLE beside {NEEDLE}"\n',
        encoding="utf-8",
    )
    # 1 line holds NEEDLE and NONE of it is code, so `code files` is 3 while
    # `files` is 4.  Without this file the two are equal and the `code files`
    # parametrization has no teeth: reverting the token-set fix failed
    # `code lines` and left `code files` green, which an adversarial review
    # measured rather than assumed.
    (app / "pkg" / "five.py").write_text(
        'MESSAGE = "NEEDLE appears here only as prose"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(registry, "REPO", tmp_path)
    return {"code lines": 5, "code files": 3, "comments lines": 2, "lines": 10, "files": 4}


class TestTheArmCanRun:
    """The blindness guard: an arm that cannot run says so rather than reporting clean.

    ``_shipped.history_is_gradeable`` is this same predicate one register over,
    and it was written first for the same reason: a shallow clone made those
    arms grade nothing and report clean.  Here the blindness is the TOKENIZER.
    """

    def test_this_interpreter_can_tell_an_interpolation_from_prose(self):
        """The live run is on a Python whose answer matches the one CI gives."""
        assert _census.interpreter_is_gradeable(), (
            "this interpreter predates PEP 701 (Python 3.12), so it hides an "
            "f-string's interpolated code inside one STRING token and a "
            "filtered census answers differently here than on the 3.14 the "
            "Dockerfile and ci.yml run -- three markers were measured on 3.11, "
            "committed green, and failed CI against the same code. Use the "
            "project's Python"
        )

    def test_an_older_tokenizer_reports_itself_rather_than_grading(self, monkeypatch):
        """With the predicate false the arm returns the CAUSE, not silence and not a number.

        Silence would fail the glob, no-file and will-not-compile controls with
        a bare ``assert []`` and send their reader to the wrong arm.
        """
        monkeypatch.setattr(_census, "interpreter_is_gradeable", lambda: False)
        problems = _census.census_violations()
        assert len(problems) == 1, problems
        assert "PEP 701" in problems[0], problems

    @pytest.mark.usefixtures("fixture_tree")
    def test_an_older_tokenizer_refuses_a_filtered_count(self, monkeypatch):
        """A direct caller is refused too, rather than handed a version's answer."""
        monkeypatch.setattr(_census, "interpreter_is_gradeable", lambda: False)
        paths = _census.census_paths("app/**/*.py")
        with pytest.raises(RuntimeError, match="PEP 701"):
            _census.census_count(re.compile("NEEDLE"), paths, "lines", "code")

    def test_an_unfiltered_count_needs_no_tokenizer(self, monkeypatch, fixture_tree):
        """`lines` reads raw text, so the floor does not apply to it."""
        monkeypatch.setattr(_census, "interpreter_is_gradeable", lambda: False)
        paths = _census.census_paths("app/**/*.py")
        measured = _census.census_count(re.compile("NEEDLE"), paths, "lines")
        assert measured == fixture_tree["lines"]


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

    @pytest.mark.usefixtures("fixture_tree")
    def test_an_f_string_splits_into_the_code_in_it_and_the_prose_round_it(self):
        """An interpolation is a USE of the name; the literal text beside it is not.

        **This control cost a CI cycle to learn.**  The walk was written on
        3.11, where an f-string is ONE ``STRING`` token, so it blanked the whole
        literal; the numbers went into the documents and CI's 3.14 re-ran three
        of them to different figures, counting the interpolations (right, and
        3.11 missed them) and the prose beside them (wrong, and 3.11 got that
        right).  One arm, two answers, graded by whichever interpreter arrived.

        The live specimen is ``app/models/transfer_template.py``:
        ``f"<TransferTemplate '{self.name}' ${self.default_amount}>"`` BREAKS
        when ``balance:X-bp`` deletes the column, so it belongs in that step's
        census, while ``f"... carries no due_date, so there is no date to "``
        breaks nothing and does not.
        """
        four = _census.census_paths("app/pkg/four.py")
        assert len(four) == 1, four
        raw = _census.census_count(re.compile("NEEDLE"), four, "lines")
        code = _census.census_count(re.compile("NEEDLE"), four, "lines", "code")
        assert (raw, code) == (3, 2), (raw, code)

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
        "(census 3 `x` in `app/**/*.py`)",
        "(census 3 lines x in `app/**/*.py`)",
    ])
    def test_the_control_fires_on_each_way_a_marker_can_break(self, stage_census, broken):
        """Trailing prose, a missing unit and a bare pattern all fail."""
        stage_census(broken)
        assert _census.near_miss_violations(), broken

    def test_a_marker_a_formatter_wrapped_is_still_read(self, stage_census):
        """A line break BETWEEN a marker's tokens is legal, because `rumdl fmt` makes them.

        These documents are reflowed to 100 columns by the project's own
        markdown formatter, and the marker used to demand single spaces -- so
        `rumdl fmt` split one and the near-miss arm caught it, the arm's first
        catch against a MACHINE rather than an author. Two gates were fighting
        over one line; the marker gave way, because its requirement was the
        arbitrary one.
        """
        stage_census("(census 0 lines `live_amount_overrides` in\n`app/**/*.py`)")
        assert not _census.near_miss_violations()
        assert not _census.census_violations()

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
