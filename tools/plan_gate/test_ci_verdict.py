"""Controls for ``ci_verdict``: the ``lint-and-test`` check fails closed.

The ``needs`` shapes below are the real ones.  Captured on the hosted runner
2026-09-22 (run 35813806288, a throwaway probe workflow): a matrix job with
one failing leg of three reports ``"failure"`` for the whole job, a job whose
dependency failed reports ``"skipped"``, and a job skipped by its own ``if:``
reports ``"skipped"`` too -- which is why ``skipped`` is judged against the
scope rather than trusted.  Every entry carries ``result`` and ``outputs``, and
only ``scope`` has an output.
"""
from __future__ import annotations

import io
import json

import pytest

import ci_verdict


def _needs(scope_output="full", **results):
    """A ``needs`` context: every grader succeeded unless overridden.

    Args:
        scope_output: The classifier's output, or ``None`` for a scope job
            that produced none (it failed, or never set it).
        **results: ``job_id=result`` overrides; ``plan_gate`` names the
            ``plan-gate`` job.
    """
    needs = {
        "scope": {
            "result": "success",
            "outputs": {} if scope_output is None else {"scope": scope_output},
        },
        "plan-gate": {"result": "success", "outputs": {}},
        "lint": {"result": "success", "outputs": {}},
        "test": {"result": "success", "outputs": {}},
    }
    for job, result in results.items():
        needs[job.replace("_", "-")]["result"] = result
    return needs


class TestAFullRunIsGreenOnlyWhenEveryGraderPassed:
    """Scope ``full``: every job must have succeeded."""

    def test_every_grader_succeeded_is_green(self):
        """The one green shape of a full run."""
        assert not ci_verdict.verdict(_needs())

    @pytest.mark.parametrize("job", ["scope", "plan_gate", "lint", "test"])
    @pytest.mark.parametrize("result", ["failure", "cancelled", "skipped"])
    def test_any_grader_not_succeeding_is_red(self, job, result):
        """A failed, cancelled or SKIPPED grader turns the check red.

        ``test`` skipped is the case this module exists for: a shard's job
        skipped because a dependency failed would otherwise read as a pass.
        """
        reasons = ci_verdict.verdict(_needs(**{job: result}))
        assert len(reasons) == 1
        assert reasons[0].startswith(f"{job.replace('_', '-')}: {result!r}")


class TestARegistryOnlyRunSkipsOnlyTheCodeGraders:
    """Scope ``registry-only``: lint and the shards may skip, nothing else."""

    def test_skipped_code_graders_are_green(self):
        """The registry pass: classifier + plan gate succeeded, code skipped."""
        assert not ci_verdict.verdict(
            _needs(scope_output="registry-only", lint="skipped", test="skipped")
        )

    @pytest.mark.parametrize("result", ["failure", "cancelled", "skipped"])
    def test_the_plan_gate_must_still_succeed(self, result):
        """The one grader a registry pass has may not skip, fail or cancel."""
        reasons = ci_verdict.verdict(
            _needs(scope_output="registry-only", plan_gate=result, lint="skipped", test="skipped")
        )
        assert reasons == [f"plan-gate: {result!r}, where this scope requires success"]

    @pytest.mark.parametrize("result", ["failure", "cancelled"])
    def test_a_code_grader_that_ran_and_failed_is_still_red(self, result):
        """Skipping is allowed; failing is not, in any scope."""
        reasons = ci_verdict.verdict(
            _needs(scope_output="registry-only", lint=result, test="skipped")
        )
        assert reasons == [f"lint: {result!r}, where this scope requires skipped or success"]


class TestAnUnreadableScopeIsFull:
    """Anything but exactly ``registry-only`` demands every grader succeeded."""

    @pytest.mark.parametrize("scope", [None, "", "registry_only", "Registry-Only", "full"])
    def test_skipped_code_graders_under_a_non_registry_scope_are_red(self, scope):
        """A missing or misspelt answer never licenses a skip."""
        reasons = ci_verdict.verdict(_needs(scope_output=scope, lint="skipped", test="skipped"))
        assert reasons == [
            "lint: 'skipped', where this scope requires success",
            "test: 'skipped', where this scope requires success",
        ]

    def test_a_failed_classifier_is_red_whatever_follows(self):
        """The classifier failing leaves no output; its own result is red.

        Its dependants are skipped for want of it, so they are red too: three
        reasons, the classifier first.
        """
        reasons = ci_verdict.verdict(
            _needs(scope_output=None, scope="failure", lint="skipped", test="skipped")
        )
        assert reasons == [
            "scope: 'failure', where this scope requires success",
            "lint: 'skipped', where this scope requires success",
            "test: 'skipped', where this scope requires success",
        ]


class TestTheVerdictKnowsExactlyWhatItGrades:
    """An unknown job or a missing one is red: nothing is graded by default."""

    def test_a_job_the_verdict_does_not_name_is_red(self):
        """A grader added to ``needs`` without a rule here is refused."""
        needs = {**_needs(), "benchmarks": {"result": "success", "outputs": {}}}
        assert ci_verdict.verdict(needs) == [
            "benchmarks: a job this verdict does not know how to grade",
        ]

    @pytest.mark.parametrize("job", ["scope", "plan-gate", "lint", "test"])
    def test_a_named_job_missing_from_needs_is_red(self, job):
        """Dropping a grader from ``needs`` cannot quietly stop grading it."""
        needs = _needs()
        del needs[job]
        assert f"{job}: absent from needs, so it was never graded" in ci_verdict.verdict(needs)


class TestTheCommandLine:
    """What ``ci.yml``'s aggregate job actually runs."""

    def test_green_exits_zero_and_says_so(self, monkeypatch, capsys):
        """The real JSON shape on stdin; exit 0 and a GREEN line."""
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_needs())))
        assert ci_verdict.main() == 0
        out = capsys.readouterr().out
        assert out.splitlines()[-1] == "lint-and-test: GREEN"
        assert "scope: full" in out

    def test_red_exits_one_and_names_the_defect(self, monkeypatch, capsys):
        """A failed shard: exit 1, the job named, RED last."""
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_needs(test="failure"))))
        assert ci_verdict.main() == 1
        out = capsys.readouterr().out.splitlines()
        assert "RED -- test: 'failure', where this scope requires success" in out
        assert out[-1] == "lint-and-test: RED"
