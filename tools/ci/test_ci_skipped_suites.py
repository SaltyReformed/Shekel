"""``ci_scope``'s census reads every file the suites ``ci.yml``'s guard skips import: each
skipped suite is run here, as ``ci.yml`` starts it, in a scratch repository (step
balance:X-er, the review of 53fc83b84's L3).

Split out of ``test_ci_workflow.py`` by step balance:X-er's L-2, when this half outgrew one
module: unchanged but for this docstring, the imports, and the workflow the derivation
reads, which the tests hand it from ``conftest.py``'s one reader.  The guard and the net it
shares with the wiring's tests are :mod:`tools.ci.ci_yml`'s.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci import arcs, ci_scope, ci_yml


#: How a guarded step starts a suite: pytest itself, or the wrapper, which hands its
#: arguments to pytest behind a ``-m`` of its own (``scripts/test.sh``'s last lines).  That
#: ``-m`` is not added here: a marker expression only deselects, and a deselected test's
#: packages are never set up, so leaving it out can only ADD imports to the scratch run.
LAUNCHERS = ("pytest", arcs.WRAPPER)

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


def _skipped_invocations(workflow: dict) -> dict[str, list[str]]:
    """Every suite the ``registry-only`` scope skips, as ``ci.yml`` starts it: each line of a
    guarded job's step that begins with a :data:`LAUNCHERS` entry, split as bash splits it,
    keyed by its step's name.  A guarded line that names pytest or the wrapper any other way
    (``python -m pytest``, ``timeout 900 ./scripts/test.sh``) fails here rather than going
    unseen."""
    found: dict[str, list[str]] = {}
    for job in workflow["jobs"].values():
        if job.get("if") != ci_yml.GUARD:
            continue
        for step in job["steps"]:
            for line in step.get("run", "").splitlines():
                words = shlex.split(line)
                if not words or words[0] not in LAUNCHERS:
                    assert not ci_yml.names_a_suite(line), (
                        f"{step['name']!r} starts a suite as {line!r}, which this "
                        f"derivation does not read: start it with one of {LAUNCHERS}")
                    continue
                assert step["name"] not in found, f"{step['name']!r} starts two suites"
                found[step["name"]] = words
    return found


@pytest.fixture(name="imported", scope="module")
def _imported(tmp_path_factory, read_workflow) -> tuple[Path, dict[str, set[str]]]:
    """Run every skipped suite once in one scratch repository; the repository and, per step,
    the repository-relative paths of the files its run imported.

    pytest is the console script ``ci.yml`` calls, in the repository's own ``pytest.ini``,
    with the caller's environment less every ``GIT_*`` (a hook's binding) and ``PYTEST_*``
    (an outer run's options and xdist identity) variable: neither is the step's.  Each run
    must pass, so one that collected nothing (exit 5) or named a path the tree lacks (exit
    4) fails here rather than reading as clean.
    """
    repo = tmp_path_factory.mktemp("repo").resolve()
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
    for step, words in _skipped_invocations(read_workflow()).items():
        log.unlink(missing_ok=True)
        done = subprocess.run([str(pytest_script), *words[1:]], cwd=repo, env=env,
                              capture_output=True, text=True, check=False)
        assert done.returncode == 0, (step, words, done.stdout[-2000:], done.stderr)
        runs[step] = {Path(line).resolve().relative_to(repo).as_posix()
                      for line in log.read_text(encoding="utf-8").splitlines()}
    return repo, runs


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
        census = {path.relative_to(repo).as_posix() for path in ci_scope.censused(repo=repo)}
        assert {step: sorted(files - census) for step, files in runs.items()} == {
            step: [] for step in runs}

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
