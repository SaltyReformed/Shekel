"""``ci_scope``'s census reads every file the suites ``ci.yml``'s guard skips import: each
skipped suite is run here, as ``ci.yml`` starts it, in a scratch repository (step
balance:X-er, the review of 53fc83b84's L3).

Split out of ``test_ci_workflow.py`` by step balance:X-er's L-2, when this half outgrew one
module.  The same step taught the derivation how Actions starts a step (ruling L-2 (k),
2026-10-17): the scratch run is given the literal environment ``ci.yml`` gives the step,
and each input of the step's run that the workflow's text sets and no scratch run can
reproduce is refused by name.  What the text does not set (the runner's own variables, an
action's exports) is named in :func:`_step_env` and :func:`_refuse_job_inputs`.  The guard
and the net this module shares with the wiring's tests are :mod:`tools.ci.ci_yml`'s, and the
workflow is handed in by ``conftest.py``'s one reader.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

from tools.ci import arcs, ci_scope, ci_yml


#: How a guarded step starts a suite: pytest itself, or the wrapper, which hands its
#: arguments to pytest behind a ``-m`` of its own (``scripts/test.sh``'s last lines).  That
#: ``-m`` is not added here: a marker expression only deselects, and a deselected test's
#: packages are never set up, so leaving it out can only ADD imports to the scratch run.
LAUNCHERS = ("pytest", arcs.WRAPPER)

#: The characters bash gives a meaning outside quotes beyond splitting words: an expansion
#: (``$``, the backtick, ``~``, the globs ``*``, ``?`` and ``[``, and a brace), an operator
#: (``|``, ``&``, ``;``, ``<``, ``>``, ``(`` and ``)``) or a comment (``#``).  A launched line
#: holding none, and not continued by a trailing backslash, is split by :func:`shlex.split`
#: into the words bash hands its launcher (ruling L-2 (k), 2026-10-17).
SHELL_SYNTAX = frozenset("$`~*?[{|&;<>()#")

#: What opens an Actions expression in a value: the runner evaluates it, so no scratch run
#: can reproduce what it yields.
EXPRESSION = "${{"

#: The project's own environment namespace.  A name in it is one the project coined, which
#: nothing a skipped suite's run loads reads (:func:`_step_env`'s census), so an expression
#: may set it; outside it, an expression is refused (ruling L-2 (k), 2026-10-17).
PROJECT_NAMESPACE = "SHEKEL_"

#: The settings under which Actions runs a step's ``run`` other than as the scratch run
#: starts it: under another shell, or in another directory.  Each may sit on the step and in
#: its job's or the workflow's ``defaults.run``.
RUN_SETTINGS = ("shell", "working-directory")

#: Actions' environment files: a step that writes one sets the environment or the ``PATH``
#: of every later step in its job, by a value no derivation can read.
ENV_FILES = ("GITHUB_ENV", "GITHUB_PATH")

#: The action that checks the repository out; its ``path`` input would put the repository
#: below the directory every ``run`` step starts in.
CHECKOUT = "actions/checkout@"

#: The scratch repository CI's skipped suites run in.  Every file records its own path when
#: it runs and imports nothing, so a path recorded is a file PYTEST imported.  Beside what
#: the suites import -- each root's files, conftests above the roots, the root package's
#: ``__init__.py`` and ``tools/__init__.py`` -- stand what they must not: a sibling package's
#: files, a module beside a conftest, and a harness under ``tests/manual``.  ``tools/`` holds
#: no conftest and ``tools/pylint`` no ``__init__.py``, as in this repository: a conftest
#: inside a package imports the package's ``__init__.py`` itself, so ``tools/__init__.py``
#: is imported here by pytest's package setup alone, the load the census's second half
#: rests on (``Package.setup``; delta review of L3, M1).
SCRATCH_TREE = (
    "__init__.py", "conftest.py", "helper.py",
    "tests/__init__.py", "tests/conftest.py", "tests/_helpers.py", "tests/test_a.py",
    "tests/sub/__init__.py", "tests/sub/conftest.py", "tests/sub/test_b.py",
    "tests/test_performance/__init__.py", "tests/test_performance/conftest.py",
    "tests/test_performance/test_p.py", "tests/manual/verify_x.py",
    "tools/__init__.py", "tools/helper.py", "tools/pylint/conftest.py",
    "tools/pylint/tests/conftest.py", "tools/pylint/tests/test_c.py",
    "tools/ci/__init__.py", "tools/ci/conftest.py", "tools/ci/test_d.py",
    "sibling/__init__.py", "sibling/conftest.py", "sibling/test_e.py",
)


class Invocation(NamedTuple):
    """How a guarded step starts a skipped suite: its launched line, and the literal
    environment Actions builds for the step (:func:`_step_env`)."""

    line: str
    env: dict[str, str]

    @property
    def words(self) -> list[str]:
        """The line split into the words bash hands its launcher (:data:`SHELL_SYNTAX`)."""
        return shlex.split(self.line)


def _launched_line(step: dict) -> str | None:
    """The line of ``step``'s ``run`` that starts a suite, or None when no line names one.

    Every line that names pytest or the wrapper (:func:`tools.ci.ci_yml.names_a_suite`) must
    be the one launched line, and that line is refused unless it is the step's FIRST
    non-blank line (an earlier line of the one bash script could ``cd``, ``export`` or
    ``source`` before it), is not continued by a trailing backslash (bash joins it with the
    next line), holds no :data:`SHELL_SYNTAX`, and begins with a :data:`LAUNCHERS` entry: a
    line naming a suite any other way (``python -m pytest``, ``timeout 900
    ./scripts/test.sh``) fails here rather than going unseen.
    """
    name = step["name"]
    lines = [line for line in step.get("run", "").splitlines() if line.strip()]
    naming = [index for index, line in enumerate(lines) if ci_yml.names_a_suite(line)]
    if not naming:
        return None
    assert len(naming) == 1, f"{name!r} starts two suites"
    line = lines[naming[0]]
    assert naming == [0], (
        f"{name!r} starts a suite on its line {line!r}, after {lines[0]!r}: an earlier line "
        f"of the step's one bash script can change the run, and this derivation reads the "
        f"launched line alone")
    assert not line.endswith("\\"), (
        f"{name!r} continues its launched line {line!r} with a trailing backslash: bash "
        f"joins it with the next line, and this derivation reads one line")
    held = "".join(sorted(SHELL_SYNTAX.intersection(line)))
    assert not held, (
        f"{name!r} starts a suite as {line!r}, which holds {held!r}: bash would expand, "
        f"operate on or comment out part of it, and this derivation runs the words "
        f"shlex.split reads")
    assert shlex.split(line)[0] in LAUNCHERS, (
        f"{name!r} starts a suite as {line!r}, which this derivation does not read: start "
        f"it with one of {LAUNCHERS}")
    return line


def _refuse_job_inputs(name: str, job: dict) -> None:
    """Fail when job ``name``, which holds a launching step, sets an input that reaches every
    step and that the scratch run does not reproduce: a ``container`` (another image's
    interpreter and plugins), a step naming one of :data:`ENV_FILES`, or a :data:`CHECKOUT`
    step with a ``path``.  ``services`` stays allowed: it starts containers beside the steps
    and changes neither their interpreter nor their directory.  What a ``uses:`` action
    exports itself (``actions/setup-python``'s variables and ``PATH`` entry, for one) is not
    in the workflow's text and is not modelled, and nor is an environment file a script the
    step runs writes without the step naming it."""
    assert "container" not in job, (
        f"job {name!r} runs its steps in a container, whose interpreter and plugins the "
        f"scratch run cannot reproduce")
    for step in job["steps"]:
        for written in ENV_FILES:
            assert written not in step.get("run", ""), (
                f"job {name!r}'s step {step['name']!r} names {written}, which sets later "
                f"steps' environment by a value no derivation reads")
        assert not (step.get("uses", "").startswith(CHECKOUT)
                    and "path" in (step.get("with") or {})), (
            f"job {name!r} checks the repository out at a path, so its run steps start "
            f"outside the repository root")


def _refuse_run_settings(workflow: dict, job: dict, step: dict) -> None:
    """Fail when Actions would run ``step`` under another shell or in another directory: one
    of :data:`RUN_SETTINGS` on the step, or in its job's or the workflow's
    ``defaults.run``."""
    for where, settings in (
            ("the workflow's defaults.run", workflow.get("defaults", {}).get("run", {})),
            ("its job's defaults.run", job.get("defaults", {}).get("run", {})),
            ("the step", step)):
        for setting in RUN_SETTINGS:
            assert setting not in settings, (
                f"{step['name']!r} runs with {setting!r} set by {where}: the scratch run "
                f"starts its words at the repository root, as Actions' default shell does")


def _step_env(workflow: dict, job: dict, step: dict) -> dict[str, str]:
    """The literal environment Actions builds for ``step`` of ``job`` in ``workflow``: the
    workflow's ``env``, then the job's, then the step's, the later winning, each value
    exactly as written.

    A value holding an :data:`EXPRESSION` is evaluated by the runner and cannot be
    reproduced, so it is refused unless its name is in :data:`PROJECT_NAMESPACE`.  An
    admitted one is left out, and so is any literal an earlier level set for its name; the
    caller's own value of it, if any, passes as for every name the step does not set.  An
    ``env`` that is not a mapping, and a name or value that is not a string, is refused too:
    PyYAML reads an unquoted ``true`` or ``1`` by YAML 1.1 and Actions by its own parser, so
    only a quoted value is one text to both.

    The namespace is admitted because nothing a skipped suite's run loads reads a name in
    it.  Measured 2026-10-17, pytest 9.1.1: every module loaded by any process of a stub run
    of the three skipped suites (``sys.modules`` at session finish, each xdist worker
    included) is 434 ``.py`` files, 224 of them third-party (pytest, the seven distributions
    it autoloads as plugins, and what they import), and one C extension (``_time_machine``,
    which imports no ``getenv``); none spells ``SHEKEL``.  A name read by a computed key: the
    ``CI`` and ``BUILD_NUMBER`` that pytest's ``running_on_ci`` reads (output only), the
    ``XDG_*`` names platformdirs reads, and a ``$NAME`` that pytest expands in a value it is
    handed (``--rootdir``, ``--junitxml``, ``cache_dir``), none of which a launched line or
    ``pytest.ini`` sets.  The whole environment is copied only for a subprocess (pytester's
    ``popen``, which no stub test calls; ``platform``; ``uuid``).  Of the variables Actions
    sets itself (``CI``, ``GITHUB_*``, ``RUNNER_*``), which ``ci.yml`` does not, pytest reads
    ``CI`` alone; they are not modelled.  What this census cannot see: a module loaded only
    under an argument the stub runs do not pass, and code that runs before any module: the
    interpreter's ``PYTHON*`` reads and site-packages' ``.pth`` hooks (coverage's reads
    ``COVERAGE_PROCESS_START``).  The project's own reads reach the walk only through files
    the census reads: ``tests/conftest.py`` reads ``SHEKEL_TEST_SHARD`` (``SHARD_ENV``) when
    it deselects.
    """
    env: dict[str, str] = {}
    for where, holder in (("the workflow", workflow), ("its job", job), ("the step", step)):
        layer = holder.get("env", {})
        assert isinstance(layer, dict), (
            f"{step['name']!r}: {where}'s env is not a mapping ({layer!r}), so no scratch "
            f"run can reproduce it")
        for name, value in layer.items():
            assert isinstance(name, str) and isinstance(value, str), (
                f"{step['name']!r}: {where} sets {name!r} to {value!r}, not a quoted string: "
                f"PyYAML reads it by YAML 1.1 and Actions by its own parser, so quote it")
            if EXPRESSION not in value:
                env[name] = value
                continue
            assert name.startswith(PROJECT_NAMESPACE), (
                f"{step['name']!r}: {where} sets {name!r} by an expression ({value!r}), which "
                f"no scratch run can reproduce: only a name in {PROJECT_NAMESPACE}* may be")
            env.pop(name, None)
    return env


def _skipped_invocations(workflow: dict) -> dict[str, Invocation]:
    """Every suite the ``registry-only`` scope skips, as ``workflow`` starts it, keyed by its
    step's name: each guarded job's launched lines (:func:`_launched_line`), split, with the
    environment Actions builds for each (:func:`_step_env`).  A guarded job holding one is
    refused any input that reaches every step unreproduced (:func:`_refuse_job_inputs`), and
    each launching step another shell or directory (:func:`_refuse_run_settings`): ruling
    L-2 (k), 2026-10-17."""
    found: dict[str, Invocation] = {}
    for name, job in workflow["jobs"].items():
        if job.get("if") != ci_yml.GUARD:
            continue
        launching = [(step, line) for step in job["steps"]
                     if (line := _launched_line(step)) is not None]
        if launching:
            _refuse_job_inputs(name, job)
        for step, line in launching:
            assert step["name"] not in found, f"two steps named {step['name']!r} start a suite"
            _refuse_run_settings(workflow, job, step)
            found[step["name"]] = Invocation(line, _step_env(workflow, job, step))
    return found


def _run_skipped(workflow: dict, repo: Path) -> dict[str, set[str]]:
    """Run every suite ``workflow`` skips once, in a scratch repository built at ``repo``;
    per step, the repository-relative paths of the files its run imported.

    pytest is the console script ``ci.yml`` calls, in the repository's own ``pytest.ini``,
    with the caller's environment less every ``GIT_*`` (a hook's binding) and ``PYTEST_*``
    (an outer run's options and xdist identity) variable, neither of them the step's, and
    over it the literal environment the step is given (:func:`_step_env`).  Each run must
    pass, so one that collected nothing (exit 5) or named a path the tree lacks (exit 4)
    fails here rather than reading as clean.
    """
    log = repo.parent / "imported.log"
    record = (f"with open({str(log)!r}, 'a', encoding='utf-8') as _log:\n"
              "    _log.write(__file__ + '\\n')\n")
    for rel in SCRATCH_TREE:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        test = "def test_x():\n    pass\n" if path.name.startswith("test_") else ""
        path.write_text(record + test, encoding="utf-8")
    shutil.copyfile(arcs.REPO / "pytest.ini", repo / "pytest.ini")
    pytest_script = Path(sys.executable).with_name("pytest")
    env = {name: value for name, value in os.environ.items()
           if not name.startswith(("GIT_", "PYTEST_"))}
    runs: dict[str, set[str]] = {}
    for step, invocation in _skipped_invocations(workflow).items():
        log.unlink(missing_ok=True)
        done = subprocess.run([str(pytest_script), *invocation.words[1:]], cwd=repo,
                              env={**env, **invocation.env},
                              capture_output=True, text=True, check=False)
        assert done.returncode == 0, (step, invocation, done.stdout[-2000:], done.stderr)
        runs[step] = {Path(line).resolve().relative_to(repo.resolve()).as_posix()
                      for line in log.read_text(encoding="utf-8").splitlines()}
    return runs


def _uncensused(repo: Path, runs: dict[str, set[str]]) -> dict[str, list[str]]:
    """Per step, the files its run imported that the census does not read, sorted."""
    census = {path.relative_to(repo).as_posix() for path in ci_scope.censused(repo=repo)}
    return {step: sorted(files - census) for step, files in runs.items()}


@pytest.fixture(name="imported", scope="module")
def _imported(tmp_path_factory, read_workflow) -> tuple[Path, dict[str, set[str]]]:
    """Run every suite the live ``ci.yml`` skips (:func:`_run_skipped`); the scratch
    repository and, per step, the files its run imported."""
    repo = tmp_path_factory.mktemp("repo").resolve()
    return repo, _run_skipped(read_workflow(), repo)


class TestTheCensusReadsWhatTheSkippedSuitesImport:
    """``ci_scope``'s census against pytest itself: each suite the ``registry-only`` scope
    skips, run in a scratch repository exactly as ``ci.yml`` starts it, imports no file the
    census does not read, and above the skipped roots the census reads exactly what the
    suites import (its dated measurement: :func:`tools.ci.ci_scope.censused`).

    The census's own tests grade it against a model of pytest; this grades the model, so a
    pytest upgrade that moves its conftest walk or its package setup fails here.
    """

    def test_the_skipped_suites_are_the_three_ci_yml_starts(self, workflow):
        """The derivation finds every suite, so an empty one cannot pass by running nothing:
        the application suite (step 7), its serial benchmarks (step 7b) and the checkers'
        tests (step 5b).  Their arguments are graded by running them, not pinned here."""
        assert set(_skipped_invocations(workflow)) == {
            "Run tests", "Run audit-trigger benchmarks (serial)",
            "Test and apply custom pylint checkers",
        }

    def test_every_skipped_suite_imports_the_repository_root_conftest(self, imported,
                                                                      workflow):
        """The probe measures: each run records the root conftest, the file the census was
        widened for (``ci_scope.censused``), so a run that recorded nothing cannot pass."""
        _repo, runs = imported
        assert {step: "conftest.py" in files for step, files in runs.items()} == {
            step: True for step in _skipped_invocations(workflow)}

    def test_no_skipped_suite_imports_a_file_the_census_does_not_read(self, imported):
        """Every file each run imported is one the census reads."""
        repo, runs = imported
        assert _uncensused(repo, runs) == {step: [] for step in runs}

    def test_above_the_roots_the_census_reads_exactly_what_the_suites_import(self, imported):
        """The files above a skipped root that the census reads are the ones some skipped
        suite imports: no fewer (a read there would go ungraded) and no more (the census
        would claim a load pytest never makes)."""
        repo, runs = imported
        roots = [repo / root for root in ci_scope.SKIPPED_TEST_ROOTS]
        above = {path.relative_to(repo).as_posix() for path in ci_scope.censused(repo=repo)
                 if not any(path.is_relative_to(root) for root in roots)}
        loaded = {rel for files in runs.values() for rel in files
                  if not any((repo / rel).is_relative_to(root) for root in roots)}
        assert above == loaded


#: The three levels an ``env`` or a ``defaults.run`` may sit at, as :func:`_workflow_with`
#: names them.
LEVELS = ("workflow", "job", "step")


def _workflow_with(*, workflow: dict | None = None, job: dict | None = None,
                   step: dict | None = None, before: tuple[dict, ...] = (),
                   run: str = "pytest -q -n 0") -> dict:
    """A scratch workflow of one guarded job whose step ``Suite`` starts the application
    suite by ``run``, after the steps ``before``; each mapping given is added at its
    level."""
    return {**(workflow or {}), "jobs": {"guarded": {
        "if": ci_yml.GUARD, **(job or {}),
        "steps": [*before, {"name": "Suite", "run": run, **(step or {})}]}}}


def _bash_arguments(line: str) -> list[str]:
    """The arguments bash hands ``line``'s launcher: the line's first word, which must be
    written unquoted, replaced by ``set --``, and each positional parameter printed."""
    launcher, _, arguments = line.lstrip().partition(" ")
    assert launcher == shlex.split(line)[0], f"{line!r}'s launcher is not written unquoted"
    script = f'set -- {arguments}\nfor word in "$@"; do printf "%s\\0" "$word"; done'
    done = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-c", script],
                          capture_output=True, text=True, check=True)
    return done.stdout.split("\0")[:-1]


def _settings(level: str, setting: str) -> dict:
    """``setting`` as ``level`` holds it: on the step itself, else in ``defaults.run``."""
    return {setting: "x"} if level == "step" else {"defaults": {"run": {setting: "x"}}}


class TestEachSkippedSuiteRunsAsActionsStartsIt:
    """The derivation against scratch workflows: what Actions builds for a launching step is
    reproduced exactly, or the derivation refuses it by name (ruling L-2 (k), 2026-10-17).
    The census's own direction, which no workflow here varies, is
    :class:`TestTheCensusReadsWhatTheSkippedSuitesImport`'s."""

    def test_a_literal_the_step_sets_reaches_its_run(self, tmp_path):
        """A literal step ``env`` that changes what pytest imports changes the scratch run
        too, so the differential sees the file: ``PYTHONPATH`` puts the scratch root on the
        path and ``PYTEST_PLUGINS`` imports its ``helper.py``, which the census does not
        read.  ``PYTEST_PLUGINS`` is a ``PYTEST_*`` name, so the step's literal reaches the
        run over the caller's stripped environment, not under it."""
        workflow = _workflow_with(step={"env": {"PYTHONPATH": ".", "PYTEST_PLUGINS": "helper"}})
        repo = tmp_path.resolve() / "repo"
        assert _uncensused(repo, _run_skipped(workflow, repo)) == {"Suite": ["helper.py"]}

    @pytest.mark.parametrize("level", LEVELS)
    def test_an_expression_outside_the_namespace_is_refused_at_every_level(self, level):
        """``HOME: ${{ runner.temp }}`` would move the user site-packages, and with it the
        plugins pytest autoloads, by a value no scratch run can know."""
        workflow = _workflow_with(**{level: {"env": {"HOME": "${{ runner.temp }}"}}})
        with pytest.raises(AssertionError, match="sets 'HOME' by an expression"):
            _skipped_invocations(workflow)

    def test_a_name_holding_the_namespace_after_its_start_is_refused(self):
        """The namespace is a PREFIX: a third party's name may hold it anywhere else."""
        workflow = _workflow_with(step={"env": {"NOT_SHEKEL_X": "${{ github.sha }}"}})
        with pytest.raises(AssertionError, match="sets 'NOT_SHEKEL_X' by an expression"):
            _skipped_invocations(workflow)

    def test_an_expression_in_the_namespace_is_admitted_and_never_reproduced(self):
        """``SHEKEL_TEST_SHARD``'s shape: admitted, and left out of the reproduced
        environment, so no run is handed the expression's text as its value."""
        workflow = _workflow_with(step={"env": {"SHEKEL_X": "${{ strategy.job-index }}",
                                                "PLAIN": "1"}})
        assert _skipped_invocations(workflow)["Suite"].env == {"PLAIN": "1"}

    def test_the_step_overrides_the_job_and_the_job_the_workflow(self):
        """Actions' precedence, the later level winning; an admitted expression at a later
        level hides an earlier literal of its name, since the runner's value replaces it."""
        workflow = _workflow_with(
            workflow={"env": {"V": "w", "W": "w", "X": "w", "SHEKEL_Y": "w"}},
            job={"env": {"W": "j", "X": "j"}},
            step={"env": {"X": "s", "SHEKEL_Y": "${{ strategy.job-index }}"}})
        assert _skipped_invocations(workflow)["Suite"].env == {"V": "w", "W": "j", "X": "s"}

    @pytest.mark.parametrize("env", [
        {"FLAG": True}, {"COUNT": 1}, {1: "x"}, "${{ fromJSON(needs.scope.outputs.env) }}",
    ], ids=["an-unquoted-boolean", "an-unquoted-number", "a-numeric-name", "not-a-mapping"])
    def test_an_env_yaml_and_actions_may_read_apart_is_refused(self, env):
        """PyYAML's ``True`` is the text ``true`` to Actions, so no ``str()`` reproduces it;
        an ``env`` written as one expression cannot be read at all."""
        with pytest.raises(AssertionError, match="not a quoted string|not a mapping"):
            _skipped_invocations(_workflow_with(step={"env": env}))

    @pytest.mark.parametrize("setting", ["shell", "working-directory"])
    @pytest.mark.parametrize("level", LEVELS)
    def test_another_shell_or_directory_is_refused_at_every_level(self, level, setting):
        """``shell`` would hand the line to another interpreter and ``working-directory``
        start it elsewhere; either may sit on the step or in a ``defaults.run``.  Spelled here
        rather than read off :data:`RUN_SETTINGS`, so a setting dropped from it fails."""
        with pytest.raises(AssertionError, match=f"runs with '{setting}' set by"):
            _skipped_invocations(_workflow_with(**{level: _settings(level, setting)}))

    @pytest.mark.parametrize("run", [
        "pytest tests $EXTRA", "pytest tests `cat args`", "pytest ~/tests", "pytest tests/*",
        "pytest tests/test_?.py", "pytest tests/test_[ab].py", "pytest tests/{a,b}",
        "pytest tests | tee log", "pytest tests & wait", "pytest tests; true",
        "pytest tests < input", "pytest tests > log", "pytest (tests", "pytest tests)",
        "pytest tests # note",
    ])
    def test_a_launched_line_bash_would_expand_or_operate_on_is_refused(self, run):
        """Each member of :data:`SHELL_SYNTAX`, spelled here as a line holding it alone rather
        than read off the constant, so a member dropped from it fails its own case."""
        with pytest.raises(AssertionError, match="which holds"):
            _skipped_invocations(_workflow_with(run=run))

    def test_a_launched_line_continued_by_a_backslash_is_refused(self):
        """bash joins ``pytest tests \\`` with the line after it; the derivation reads one."""
        with pytest.raises(AssertionError, match="trailing backslash"):
            _skipped_invocations(_workflow_with(run="pytest tests \\\n  -p helper\n"))

    @pytest.mark.parametrize("first", ["cd tests", "export PYTEST_ADDOPTS='-p helper'"])
    def test_a_launched_line_after_another_line_of_its_step_is_refused(self, first):
        """An earlier line of the step's one bash script runs first: ``cd`` moves the run,
        and ``export`` sets its environment, neither naming a suite."""
        with pytest.raises(AssertionError, match="an earlier line"):
            _skipped_invocations(_workflow_with(run=f"{first}\npytest -q\n"))

    @pytest.mark.parametrize("line", [
        "pytest", "pytest -q -n 0", "./scripts/test.sh -n logical -m \"\" --tb=short -q",
        "pytest -m '' -k \"a and not b\" --override-ini=addopts= \"c\\\"d\" 'e\\f' \"g\\h\" i\\ j",
    ])
    def test_an_accepted_line_is_split_as_bash_splits_it(self, line):
        """The claim :data:`SHELL_SYNTAX` exists to make true, graded against bash itself:
        the words bash hands the launcher are the ones the derivation runs.  Quotes, escapes,
        an empty argument and no argument at all are where shlex and bash could part."""
        invocation = _skipped_invocations(_workflow_with(run=line))["Suite"]
        assert _bash_arguments(invocation.line) == invocation.words[1:]

    def test_the_live_launched_lines_are_split_as_bash_splits_them(self, workflow):
        """The same grade over the lines ``ci.yml`` launches today, as it writes them."""
        for invocation in _skipped_invocations(workflow).values():
            assert _bash_arguments(invocation.line) == invocation.words[1:], invocation.line

    @pytest.mark.parametrize("written", ["GITHUB_ENV", "GITHUB_PATH"])
    def test_a_step_writing_an_environment_file_is_refused(self, written):
        """An earlier step's ``>> "$GITHUB_ENV"`` sets the launching step's environment.
        Spelled here rather than read off :data:`ENV_FILES`, so a file dropped from it
        fails."""
        prepare = {"name": "Prepare", "run": f'echo "PYTEST_ADDOPTS=-p helper" >> "${written}"'}
        with pytest.raises(AssertionError, match=f"names {written}"):
            _skipped_invocations(_workflow_with(before=(prepare,)))

    def test_a_job_run_in_a_container_is_refused_and_one_with_services_is_not(self):
        """A container is another interpreter; a service runs beside the steps."""
        with pytest.raises(AssertionError, match="runs its steps in a container"):
            _skipped_invocations(_workflow_with(job={"container": "python:3.14"}))
        services = {"services": {"db": {"image": "postgres:17"}}}
        assert set(_skipped_invocations(_workflow_with(job=services))) == {"Suite"}

    def test_a_checkout_at_a_path_is_refused(self):
        """The repository checked out below the workspace puts every run step outside it."""
        checkout = {"name": "Checkout", "uses": f"{CHECKOUT}v7",
                    "with": {"path": "repo", "persist-credentials": False}}
        with pytest.raises(AssertionError, match="checks the repository out at a path"):
            _skipped_invocations(_workflow_with(before=(checkout,)))
        checkout["with"] = {"persist-credentials": False}
        assert set(_skipped_invocations(_workflow_with(before=(checkout,)))) == {"Suite"}

    def test_a_guarded_job_with_no_launching_step_is_not_judged(self):
        """The job-level refusals judge a job that holds a launching step: elsewhere a
        container or an environment file reaches no suite this derivation runs."""
        workflow = _workflow_with()
        workflow["jobs"]["other"] = {"if": ci_yml.GUARD, "container": "python:3.14", "steps": [
            {"name": "Prepare", "run": 'echo "X=1" >> "$GITHUB_ENV"'}]}
        assert set(_skipped_invocations(workflow)) == {"Suite"}
