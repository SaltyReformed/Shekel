"""Controls for ``ci.yml``'s wiring to the CI modules: every grader is judged, nothing fails green.

``ci.yml`` is wired to ``ci_scope``'s answer: every code-grading JOB is guarded
by it, the plan gate is not, the guard names the classifier, and the
``lint-and-test`` aggregate judges every grader through ``ci_verdict``.
(Re-expressed over jobs when ``bank_import:X-gy`` split the single job, the
developer confirming the change under rule 5, 2026-09-22.)  Since step X-cx's
L4 the scope job also hands each event's change set to the ``commit-trailers``
job, and the merge queue's ``merge_group`` event triggers the workflow.  Split
out of ``test_ci_scope.py`` by that step, unchanged but for this docstring and
the imports.
"""
from __future__ import annotations

import subprocess
import time

import pytest
import yaml

from tools.ci import arcs, ci_verdict, scratch


# ---------------------------------------------------------------- the wiring


GUARD = "needs.scope.outputs.scope != 'registry-only'"

#: Jobs that may run code without the guard: the classifier, the card-trailer
#: check (step X-cx's L4), the plan gate, the tax-law check (plan step
#: salary:X-at-4), the aggregate that judges them, and the polyglot linters
#: (every scope).
UNGUARDED_JOBS = {"scope", "commit-trailers", "plan-gate", "tax-law", "lint-and-test",
                  "polyglot-lint"}

#: The tax-law job's one grading step (ruling salary:R-SAL74).
TAX_LAW_STEP = "Is next year's tax law in? (refuses from December 1)"

#: The steps inside an UNGUARDED job that may run code -- the old single job's
#: step-level allowlist, carried to the jobs its steps now live in.
UNGUARDED_CODE_STEPS = {
    ("scope", "Classify the change set"),
    ("commit-trailers", "Card trailers (every scope)"),
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

#: The jobs whose every step grades: the six ``lint-and-test`` needs, and the
#: aggregate itself.
GRADED_JOBS = ("scope", "commit-trailers", "plan-gate", "tax-law", "lint", "test",
               "lint-and-test")


def _jobs() -> dict[str, dict]:
    """Read the jobs out of the live workflow file."""
    text = (arcs.REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
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
        assert "python -m tools.ci.ci_scope" in steps["scope"]["run"]
        assert "--no-renames" in steps["scope"]["run"], "a rename must show both paths"
        assert "pull_request" in steps["scope"]["run"], "a push to main must be full"
        assert _jobs()["scope"]["outputs"] == {
            "scope": "${{ steps.scope.outputs.scope }}",
            "base": "${{ steps.scope.outputs.base }}",
            "head": "${{ steps.scope.outputs.head }}",
        }

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

    def test_this_package_is_linted_and_tested_in_both_scopes(self):
        """``tools/ci`` decides what runs, so its own floor and tests run in every scope.

        It is not a registry-only prefix (a change to it runs everything), but
        a registry pass still runs the classifier it holds, so its tests must
        not be skipped by the scope they grade.
        """
        run = _steps("plan-gate")["Plan gate (every scope)"]["run"]
        assert "pylint tools/ci/ --fail-under=10 --fail-on=E,F" in run
        assert "pytest tools/ci -c /dev/null -q" in run

    def test_no_code_running_job_escapes_the_guard(self):
        """A job that runs pytest, pylint, a script or ``test.sh`` is guarded or named here."""
        for name, job in _jobs().items():
            if name in UNGUARDED_JOBS:
                continue
            runs = " ".join(step.get("run", "") for step in job["steps"])
            if any(tool in runs for tool in ("pytest", "pylint", "python ", "scripts/test.sh")):
                assert job.get("if") == GUARD, name

    def test_no_code_running_step_in_an_unguarded_job_escapes_the_allowlist(self):
        """Inside every unguarded job but the polyglot linters, only named steps run code.

        The single job held this per STEP; a grader added to an unguarded job
        would run on a registry pass and be judged by nobody's guard.
        """
        for name in ("scope", "commit-trailers", "plan-gate", "tax-law", "lint-and-test"):
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
        text = (arcs.REPO / ".github/workflows/tax-law.yml").read_text(encoding="utf-8")
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
        text = (arcs.REPO / ".github/workflows/docker-publish.yml").read_text(
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
        text = (arcs.REPO / ".github/workflows/docker-publish.yml").read_text(
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


class TestTheChangeSetIsHandedOver:
    """The merge queue's trigger, and the change set's two ends the scope job hands over.

    Step X-cx's L4: the scope job is the one place that reads which payload
    field holds a pull request's, a merge group's or a push's two ends; the
    ``commit-trailers`` job reads them from its outputs.
    """

    def test_the_merge_queue_triggers_the_workflow(self):
        """``merge_group`` beside ``pull_request``; ``push`` still only to ``main``."""
        text = (arcs.REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        # PyYAML reads the bare key ``on`` as the boolean True (YAML 1.1).
        triggers = yaml.safe_load(text)[True]
        assert set(triggers) == {"push", "pull_request", "merge_group"}
        assert triggers["push"] == {"branches": ["main"]}

    def test_the_card_trailer_job_checks_the_scope_jobs_range_over_full_history(self):
        """Unguarded, waiting on the scope job, a full checkout, and exactly this command."""
        job = _jobs()["commit-trailers"]
        assert job["needs"] == "scope" and "if" not in job
        checkout = job["steps"][0]
        assert checkout["with"] == {"fetch-depth": 0, "persist-credentials": False}
        step = _steps("commit-trailers")["Card trailers (every scope)"]
        assert step["env"] == {"BASE_SHA": "${{ needs.scope.outputs.base }}",
                               "HEAD_SHA": "${{ needs.scope.outputs.head }}"}
        # EXACTLY this: a ``|| true`` would let a refusal fall on the floor.
        assert step["run"].strip() == (
            'python -m tools.ci.commit_trailers "${BASE_SHA}" "${HEAD_SHA}"'
        )

    @pytest.fixture(name="ran")
    def _ran(self, tmp_path):
        """Run the scope step's text, as bash, in a scratch repository; its outputs and log.

        A stand-in ``python`` answers ``registry-only`` for ``-m
        tools.ci.ci_scope`` and fails on anything else, so the step's own shell
        is what is graded: which ends it reads for each event, and when it
        asks the classifier at all.
        """
        repo = tmp_path / "code"
        repo.mkdir()
        scratch.run(repo, "init", "--quiet", "--initial-branch=dev")
        base = scratch.commit(repo, "base")
        head = scratch.commit(repo, "head", parents=[base])
        stand_in = tmp_path / "python"
        stand_in.write_text('#!/bin/sh\ntest "$*" = "-m tools.ci.ci_scope" || exit 9\n'
                            'cat >/dev/null\necho registry-only\n')
        stand_in.chmod(0o755)
        run = {s.get("id"): s for s in _jobs()["scope"]["steps"]}["scope"]["run"]

        def _run(event: str, **ends: str) -> tuple[dict[str, str], str]:
            output = tmp_path / f"output_{event}"
            output.write_text("")
            env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "GITHUB_OUTPUT": str(output),
                   "EVENT_NAME": event, "HOME": str(tmp_path)}
            env.update({name: ends.get(name, "") for name in (
                "PR_BASE", "PR_HEAD", "GROUP_BASE", "GROUP_HEAD", "PUSH_BEFORE", "PUSH_AFTER")})
            done = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-c", run], cwd=repo,
                                  env=env, capture_output=True, text=True, check=False)
            assert done.returncode == 0, done.stderr
            pairs = [line.split("=", 1) for line in output.read_text().splitlines()]
            return dict(pairs), done.stdout

        return _run, base, head

    def test_a_pull_request_and_a_merge_group_are_classified_and_handed_over(self, ran):
        """Each reads its own payload fields, asks the classifier, and hands both ends on."""
        run, base, head = ran
        assert run("pull_request", PR_BASE=base, PR_HEAD=head)[0] == {
            "base": base, "head": head, "scope": "registry-only"}
        assert run("merge_group", GROUP_BASE=base, GROUP_HEAD=head)[0] == {
            "base": base, "head": head, "scope": "registry-only"}

    def test_a_push_is_full_and_still_hands_its_range_over(self, ran):
        """A release runs everything; its before and after are still the commits to check."""
        run, base, head = ran
        outputs, log = run("push", PUSH_BEFORE=base, PUSH_AFTER=head)
        assert outputs == {"base": base, "head": head, "scope": "full"}
        assert "has no pull-request change set" in log

    def test_an_unreadable_change_set_is_full_and_its_ends_are_handed_over_as_given(self, ran):
        """A diff git cannot take answers ``full``; the trailer check then fails on the ends."""
        run, _base, head = ran
        outputs, log = run("pull_request", PR_BASE="e" * 40, PR_HEAD=head)
        assert outputs == {"base": "e" * 40, "head": head, "scope": "full"}
        assert "not trusting an unreadable change set" in log

    def test_any_other_event_hands_over_no_range(self, ran):
        """No change set: ``full`` here, and an empty range the trailer check refuses."""
        run, _base, _head = ran
        assert run("workflow_dispatch")[0] == {"base": "", "head": "", "scope": "full"}


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
            "printf '%s' \"${NEEDS}\" | python -m tools.ci.ci_verdict"
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
