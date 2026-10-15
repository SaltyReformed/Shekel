"""The run half of the proof: the id map, the collection and outcome comparisons, pylint's
comparison, the census, and the observer plugin.  Each comparison is shown FAILING on a
hand-built observation; the observer is run under a real pytest on the sample, before and
after the tool splits it."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci.arcs import REPO
from tools.split import _dynamic as dynamic
from tools.split._model import parse_plan
from tools.split._observe import OBSERVE_ENV, verdict
from tools.split._run import census_pattern
from tools.split._source import Refusal
from tools.split.conftest import SOURCE, layout_of, plan_dict
from tools.split.test_apply import applied

PACKAGE = "tests/test_area/test_sample"

#: The sample's collection, as pytest names it.
BEFORE_IDS = [
    f"{SOURCE}::TestFirst::test_one",
    f"{SOURCE}::TestFirst::test_cases[a-1]",
    f"{SOURCE}::TestFirst::test_cases[b-2]",
    f"{SOURCE}::test_top_level",
    f"{SOURCE}::TestBig::test_a",
    f"{SOURCE}::TestBig::test_b",
    f"{SOURCE}::TestBig::test_c",
    f"{SOURCE}::TestBig::test_d",
    f"{SOURCE}::TestBig::test_e",
]


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
    """No new kind, no kind up, no duplicate-code between parts."""

    @staticmethod
    def messages(*symbols: str, path: str = "a.py") -> list[dict]:
        """pylint JSON messages carrying ``symbols``."""
        return [{"symbol": s, "path": path} for s in symbols]

    def test_fewer_or_equal_passes(self):
        """The parts may hold fewer messages, never more."""
        before = self.messages("unused-import", "too-many-lines", "unused-argument")
        assert not dynamic.compare_pylint(before, self.messages("unused-import"))

    @pytest.mark.parametrize("after, why", [
        (("unused-import", "unused-import"), "unused-import: 2 after, 1 before"),
        (("unused-import", "line-too-long"), "line-too-long: 1 after, 0 before"),
    ])
    def test_each_increase_fails(self, after, why):
        """A kind up, and a new kind."""
        failures = dynamic.compare_pylint(self.messages("unused-import"), self.messages(*after))
        assert any(why in f for f in failures)

    def test_duplicate_code_is_left_to_the_pair_by_pair_arm(self):
        """The source's duplicate-code count is structurally zero, so it is not counted here;
        :mod:`tools.split._similar` judges each pair (test_similar.py shows it refusing)."""
        assert not dynamic.compare_pylint(self.messages("unused-import"),
                                          self.messages("unused-import", "duplicate-code"))

    def test_new_kinds_are_named_per_importer(self):
        """An edited importer's new kind is reported against its own file."""
        before = self.messages("unused-argument", path="t.py")
        after = before + self.messages("shekel-private-module-import", path="t.py")
        assert dynamic.new_kinds(before, after) == {"t.py": ["shekel-private-module-import"]}


class TestTheCensus:
    """The old path is found in prose and imports, and a path into the package is not."""

    def test_the_pattern(self):
        """The file name, its directories, its dotted path; not a sibling or the package."""
        pattern = census_pattern(parse_plan(plan_dict()))
        egrep = subprocess.run(["grep", "-E", "-c", pattern], input="\n".join([
            "see tests/test_area/test_sample.py::TestBig",
            "``test_sample.py``",
            "from tests.test_area.test_sample import x",
            "from tests.test_area.test_sample._helpers import x",
            "tests/test_area/test_sample/test_first.py",
            "test_sample_other.py",
            "my_test_sample.py",
        ]) + "\n", capture_output=True, text=True, check=False)
        assert egrep.stdout.strip() == "3"

    def test_archived_documents_are_classified(self, tmp_path):
        """archive/, historical/ and an ARCHIVED first line are records; the rest is live."""
        for path, first in {"docs/archive/a.md": "x", "docs/plans/historical/b.md": "x",
                            "docs/c.md": "> **ARCHIVED.** old", "docs/d.md": "# Live"}.items():
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / path).write_text(first + "\n", encoding="utf-8")
        hits = dynamic.classify(tmp_path, ["docs/archive/a.md:3:x", "docs/plans/historical/b.md:"
                                           "4:x", "docs/c.md:5:x", "docs/d.md:6:x"])
        assert [h.archived for h in hits] == [True, True, True, False]


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
    def observed(root: Path, target: str, collect_only: bool) -> dict:
        """Run pytest on ``target`` in ``root`` with the observer, as the tool's runs do."""
        out = root / "observed.json"
        env = dict(os.environ)
        env[OBSERVE_ENV] = str(out)
        env["PYTHONPATH"] = str(REPO)
        args = [sys.executable, "-m", "pytest", target, "-c", "pytest.ini", "-q",
                "-p", "tools.split._observe", "-p", "no:cacheprovider"]
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
