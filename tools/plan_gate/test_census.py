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


class TestACensusIsReRunRatherThanRemembered:
    """The arm walks the code, so a marker cannot disagree with it and survive."""

    def test_the_control_fires_on_a_count_the_code_has_moved_past(self, stage_census):
        """A marker whose number is wrong is refused, with both figures named."""
        stage_census("(census 99999 lines `def ` in `app/**/*.py`)")
        problems = _census.census_violations()
        assert any("says 99999 lines and the code holds" in p for p in problems), problems

    def test_a_correct_count_passes(self, stage_census):
        """The same marker, measured rather than invented, is accepted.

        The pair is what proves the arm discriminates: one number apart, and it
        must answer differently.
        """
        paths = _census.census_paths("app/ref_cache/_accessors.py")
        true = _census.census_count(re.compile(r"^def [a-z]"), paths, "lines")
        stage_census(f"(census {true} lines `^def [a-z]` in `app/ref_cache/_accessors.py`)")
        assert not _census.census_violations()

    def test_files_and_lines_are_told_apart(self, stage_census):
        """`files` counts modules and `lines` counts sites, and they differ here.

        ``transfer_id is not None`` is the live specimen behind `X-bi-6`: 20
        sites across 12 modules, which is the conflation rule 6 names.
        """
        glob, pattern = "app/**/*.py", re.compile("transfer_id is not None")
        paths = _census.census_paths(glob)
        lines = _census.census_count(pattern, paths, "lines")
        files = _census.census_count(pattern, paths, "files")
        assert lines > files, (lines, files)
        stage_census(f"(census {files} lines `transfer_id is not None` in `{glob}`)")
        assert _census.census_violations()


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
