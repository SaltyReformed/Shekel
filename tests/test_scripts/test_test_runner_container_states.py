"""``scripts/test.sh`` classifies every docker container state correctly.

The wrapper's no-restart path reports what it found rather than restarting,
and the whole of one fix in that path is the ORDER of two ``case`` arms.
Docker renders a paused container as ``Up 2 seconds (Paused)`` -- it begins
with ``Up``, so a ``Up*`` arm placed first swallows it and the wrapper prints
the everything-is-fine line while the postmaster is SIGSTOPped.  Swapping the
two arms back reintroduces that silently, and until this module existed
nothing would have failed: no test under ``tests/`` executes or inspects
``scripts/test.sh``, and the 36-arm execution harness that measured the fix
was ad-hoc, uncommitted and therefore guarding nothing after the session that
ran it.

That gap is the same one the sibling module's ``_ENV_WRITE`` pin closes --
"present but unguarded, deletable by anyone without breaking a thing" -- and
it was left open for the shell half in the very commit that closed it for the
Python half.

This is a SOURCE test: it extracts the ``case`` patterns in order and replays
real ``docker ps -a --format '{{.Status}}'`` strings through them with shell
glob semantics.  No docker daemon, no database, no container.  It therefore
also runs in CI, which the execution harness never did.
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEST_RUNNER = _REPO_ROOT / "scripts/test.sh"

# The ``case`` that classifies ``docker ps -a --format '{{.Status}}'`` output.
# The indent of the ``case`` keyword is captured so the arm indent can be
# derived from it: arms sit one level (4 spaces, per shfmt -i 4 -ci) deeper,
# and their bodies one level deeper still.  Deriving beats hardcoding, which
# would break the moment the block moved inside another conditional.
_CASE_BLOCK = re.compile(
    r'^(?P<indent>[ \t]*)case "\$_db_status" in\n(?P<body>.*?)\n(?P=indent)esac',
    re.S | re.M,
)

# One arm: exactly the arm indent, then the pattern, then ``)`` at end of
# line.  Matching on indent rather than on the pattern's own characters is
# what makes ``*'(Paused)'*)`` work -- a ``[^)]*`` pattern stops at the
# parenthesis inside ``(Paused)`` and never reaches the arm's own ``)``.
# The single quotes are SHELL quoting around characters that would otherwise
# be syntax; they are not part of the glob, so they come off before matching.
_ARM_TEMPLATE = r"^{indent}(\S[^\n]*)\)$"

# Arm terminators.  Bash has three -- ``;;`` stops, while ``;&`` falls into
# the next arm's body unconditionally and ``;;&`` re-tests the remaining
# patterns.  Modelling ``case`` as first-match-then-stop is only correct if
# every arm ends ``;;``: with ``;;&`` on the paused arm a paused container
# prints the warning AND the everything-is-fine line this fix exists to
# delete, and both shell linters accept it.
# The optional trailing comment is not decoration: anchoring the terminator
# to end-of-line made ``;;& # also print the uptime line`` INVISIBLE, and a
# comment is the form a developer changing a terminator is most likely to
# write.  Measured: with that mutation the guard reported 16 passed, bash -n,
# shellcheck and shfmt were all clean, and a paused container printed BOTH
# the PAUSED warning and the everything-is-fine line this module exists to
# keep deleted.  A trailing-whitespace variant evades the anchored form too,
# though shfmt catches that one in CI.
_TERMINATOR = re.compile(r"^[ \t]+(;;&?|;&)[ \t]*(?:#.*)?$", re.M)

# Real strings docker emits, and the state each MUST be classified as.
# Sources: `Up ... (Paused)` and `Up ... (healthy)` captured from this host;
# `Exited (0) 4 days ago` copied from two stopped test-db containers that
# exist on it; the rest are docker's documented status renderings.
_OBSERVED_STATUSES = (
    ("", "absent"),
    ("Up 14 minutes (healthy)", "up"),
    ("Up Less than a second", "up"),
    ("Up 2 seconds (Paused)", "paused"),
    ("Up Less than a second (Paused)", "paused"),
    ("Exited (0) 4 days ago", "down"),
    ("Exited (137) 2 minutes ago", "down"),
    ("Created", "down"),
    ("Dead", "down"),
    ("Restarting (1) 5 seconds ago", "down"),
    ("Removal In Progress", "down"),
)

# Which arm, by its glob, means which state, in the order the classifier
# tests them.  This is an EXACT list, not a subset: keying only the arms it
# recognised and defaulting the rest to "down" made an ADDED arm invisible,
# because a new arm that intercepts an already-"down" string changes no
# expected answer.  Requiring the exact sequence means a new state has to
# come here and to _OBSERVED_STATUSES, which is the point of a census.
_EXPECTED_ARMS = ("", "*(Paused)*", "Up*", "*")
_PATTERN_STATE = {
    "": "absent",
    "*(Paused)*": "paused",
    "Up*": "up",
    "*": "down",
}


def _case_block_body() -> tuple[str, str]:
    """Return the classifier ``case`` block's indent and body, asserting it is unique.

    ``search`` grades the FIRST match.  A second, well-formed
    ``case "$_db_status" in`` earlier in the file would be graded instead
    while the live classifier went unexamined -- measured: with a decoy in
    place and the real arms swapped, every test in this module still passed.

    Returns:
        The ``case`` keyword's leading whitespace, and the text between the
        ``case`` line and its ``esac``.
    """
    source = _TEST_RUNNER.read_text(encoding="utf-8")
    blocks = _CASE_BLOCK.findall(source)
    assert len(blocks) == 1, (
        f"expected exactly one `case \"$_db_status\"` block in "
        f"scripts/test.sh, found {len(blocks)}; this module grades the first "
        "one, so a second would leave the live classifier ungraded"
    )
    block_match = _CASE_BLOCK.search(source)
    assert block_match is not None
    return block_match.group("indent"), block_match.group("body")


def _case_arms() -> list[str]:
    """Return the classifier's glob patterns, in source order.

    Returns:
        Shell glob patterns with shell quoting removed, ordered as the
        ``case`` statement tests them.  Order is the point: ``case`` takes
        the FIRST match.
    """
    indent, body = _case_block_body()
    arm_pattern = re.compile(
        _ARM_TEMPLATE.format(indent=indent + " {4}"), re.M
    )
    return [arm.replace("'", "").strip() for arm in arm_pattern.findall(body)]


def _case_arm_bodies() -> dict[str, str]:
    """Return each arm's glob mapped to the text of its body.

    Returns:
        Glob pattern (shell quoting removed) to the lines between that arm
        and the next one, so a body can be asserted about on its own.
    """
    indent, body = _case_block_body()
    arm_line = re.compile(
        _ARM_TEMPLATE.format(indent=indent + " {4}"), re.M
    )
    matches = list(arm_line.finditer(body))
    bodies: dict[str, str] = {}
    for position, match in enumerate(matches):
        end = (
            matches[position + 1].start()
            if position + 1 < len(matches)
            else len(body)
        )
        bodies[match.group(1).replace("'", "").strip()] = body[match.end():end]
    return bodies


def _classify(status: str) -> str:
    """Classify a docker status string the way the ``case`` would.

    Args:
        status: A ``docker ps -a --format '{{.Status}}'`` value.

    Returns:
        The state name of the first arm whose glob matches.
    """
    for pattern in _case_arms():
        if pattern == "*" or fnmatch.fnmatchcase(status, pattern):
            # No ``.get`` default: an arm absent from the mapping is a state
            # nobody decided the meaning of, and guessing "down" is how an
            # added arm stayed invisible.  KeyError is the right noise.
            return _PATTERN_STATE[pattern]
    raise AssertionError(f"no arm matched {status!r}; the case has no catch-all")


class TestTestRunnerContainerStates:
    """The wrapper's container-state classifier, held to real docker output."""

    def test_the_wrapper_is_valid_bash(self) -> None:
        """``scripts/test.sh`` parses. Nothing else in the repo checks this.

        This module strips shell quoting from the arm patterns and keys its
        expectations on the STRIPPED form, so its canonical spelling of the
        paused arm is ``*(Paused)*`` -- which is precisely the spelling that
        is a SYNTAX ERROR in the shell.  Removing the quotes at the source
        was measured to leave every test here green while
        ``bash -n scripts/test.sh`` failed and the wrapper refused to run at
        all, taking every local test invocation with it.  shellcheck and
        shfmt both accept it (``*(Paused)*`` is a legal extglob pattern), so
        without this assertion the breakage reaches a developer's terminal
        before any gate.
        """
        result = subprocess.run(
            ["bash", "-n", str(_TEST_RUNNER)],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, (
            f"scripts/test.sh is not valid bash: {result.stderr.strip()}. "
            "Every test invocation in this repo goes through that wrapper, "
            "so this breaks all local testing."
        )

    def test_every_arm_terminates_with_double_semicolon(self) -> None:
        """No arm falls through. ``;;`` only -- not ``;&`` or ``;;&``.

        This module models ``case`` as first-match-then-stop, which is true
        of ``;;`` and false of the other two.  Measured: changing the paused
        arm's ``;;`` to ``;;&`` restores the everything-is-fine line beside
        the PAUSED warning, and both shell linters accept it.
        """
        _, body = _case_block_body()
        # One capturing group means findall yields STRINGS, not tuples.
        # Indexing [0] took the first CHARACTER and compared {";"} to {";;"}.
        terminators = _TERMINATOR.findall(body)
        arms = _case_arms()

        # Count first.  A SET comparison passes while terminators go missing
        # from the census -- {';;'} == {';;'} whether it holds four of them
        # or one -- so a terminator the regex stops matching would silently
        # stop being graded, which is how the anchored version hid ``;;&``
        # followed by a comment.
        assert len(terminators) == len(arms), (
            f"{len(arms)} case arms but {len(terminators)} terminators "
            f"({terminators}): one is written in a form this test cannot "
            "see, so it is ungraded rather than absent"
        )
        assert set(terminators) == {";;"}, (
            f"found {sorted(set(terminators))}: an arm falls through, so the "
            "first matching arm is no longer the only one that runs and this "
            "module's classification model no longer describes the shell"
        )

    @pytest.mark.parametrize("restart", ["", "1"])
    def test_a_docker_that_cannot_answer_is_never_called_missing(
        self, tmp_path: Path, restart: str
    ) -> None:
        """Deterministic twin of the probe below: no real docker needed.

        The sibling test drives a dead ``DOCKER_HOST`` and therefore skips
        wherever the docker CLI is absent.  A skip is invisible in a green
        run -- the point the regex-matching case had to be rewritten for --
        so the property is graded here against a stub instead, and the
        real-docker version stays as the integration check.

        Both ``RESTART_TEST_DB`` values are exercised because this fix
        landed on the no-restart path first and the restart path -- the one
        a gating run takes -- went on announcing a missing container on
        evidence that never established one.

        Args:
            tmp_path: pytest-provided directory for the stub.
            restart: The RESTART_TEST_DB value to run under.
        """
        stub = tmp_path / "docker"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            "# A docker whose daemon cannot be reached: every subcommand\n"
            "# fails the way the real CLI fails, with the reason on stderr.\n"
            "echo 'Cannot connect to the Docker daemon at "
            "unix:///var/run/docker.sock. Is the docker daemon running?' >&2\n"
            "exit 1\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)

        result = subprocess.run(
            [str(_TEST_RUNNER), "--version"],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "TEST_DB_CONTAINER": "shekel-stub-probe",
                "RESTART_TEST_DB": restart,
            },
            cwd=str(_REPO_ROOT),
            check=False,
        )

        assert "UNKNOWN" in result.stderr, (
            f"RESTART_TEST_DB={restart!r}, docker failing: "
            f"{result.stderr.strip()!r}"
        )
        for wrong in ("does not exist", "no such container"):
            assert wrong not in result.stderr, (
                f"a docker that cannot answer does not establish {wrong!r}: "
                f"{result.stderr.strip()!r}"
            )
        if not restart:
            assert "docker daemon" in result.stderr, (
                "the no-restart path must pass docker's own words through, "
                "or it cannot distinguish a dead daemon from a "
                f"misconfigured DOCKER_HOST: {result.stderr.strip()!r}"
            )

    @pytest.mark.parametrize("restart", ["", "1"])
    def test_an_unreachable_daemon_is_not_called_a_missing_container(
        self, restart: str
    ) -> None:
        """Both paths say UNKNOWN, not "missing", when docker cannot answer.

        This runs the real wrapper against a DOCKER_HOST with nothing behind
        it, which needs no daemon and no container -- only the docker CLI.
        It is the one state the ``case`` census cannot reach, because it is
        decided before the ``case``: deleting the whole branch left every
        other test in this module green.

        Both values of RESTART_TEST_DB are exercised because the fix landed
        on the no-restart path first and the restart path -- the one a
        gating run actually takes -- went on announcing a missing container
        on evidence that never established one.

        Args:
            restart: The RESTART_TEST_DB value to run under.
        """
        if shutil.which("docker") is None:
            pytest.skip("no docker CLI; this asserts CLI-level behaviour")

        env = {
            **os.environ,
            "DOCKER_HOST": "tcp://127.0.0.1:59999",
            "TEST_DB_CONTAINER": "shekel-unreachable-probe",
            "RESTART_TEST_DB": restart,
        }
        result = subprocess.run(
            [str(_TEST_RUNNER), "--version"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(_REPO_ROOT),
            check=False,
        )

        assert "UNKNOWN" in result.stderr, (
            f"RESTART_TEST_DB={restart!r} with an unreachable daemon said: "
            f"{result.stderr.strip()!r}"
        )
        assert "does not exist" not in result.stderr, (
            "the wrapper claimed the container does not exist, which an "
            "unreachable daemon does not establish: "
            f"{result.stderr.strip()!r}"
        )
        assert "no such container" not in result.stderr, (
            "same conflation on the no-restart path: "
            f"{result.stderr.strip()!r}"
        )

    @pytest.mark.parametrize("arm", ["*(Paused)*", "*"])
    def test_not_running_arms_do_not_use_the_healthy_phrasing(
        self, arm: str
    ) -> None:
        """Only the ``Up*`` arm may say "not restarting".

        That phrase is the everything-is-fine line.  A container that is
        paused or stopped is not fine, and the operator distinguishes the
        cases by the wording alone.  Measured ungraded before this test:
        copying the ``Up*`` arm's echo into the catch-all made a genuinely
        stopped container print "not restarting <name> (Exited (137) 4
        minutes ago) -- set RESTART_TEST_DB=1 ..." with the whole suite
        still green.

        Args:
            arm: The glob of a not-running arm.
        """
        bodies = _case_arm_bodies()

        assert arm in bodies, f"arm {arm!r} missing; bodies: {sorted(bodies)}"
        assert "not restarting" not in bodies[arm], (
            f"the {arm!r} arm uses the healthy 'not restarting' phrasing, so "
            "a container in that state reads as fine. Only the Up* arm may "
            "say it."
        )
        assert "not restarting" in bodies["Up*"], (
            "the Up* arm no longer says 'not restarting', so this test is "
            "asserting the absence of a phrase nothing uses"
        )

    @pytest.mark.parametrize(
        ("listed_name", "expected", "unexpected"),
        [
            # The regex form of ``rev8.test.db`` matches ``rev8-test-db``;
            # the exact form cannot.  So the wrapper must say the name is
            # absent even though the filter returned a row.
            ("rev8-test-db", "no such container", "Up 3 hours"),
            # The control.  Same probe name, and now the row IS that name,
            # so the status must come through.  Without this case the one
            # above would pass just as well if the shim were never consulted
            # or the wrapper stopped asking docker at all.
            ("rev8.test.db", "Up 3 hours", "no such container"),
        ],
    )
    def test_the_container_name_is_matched_exactly_not_as_a_regex(
        self,
        tmp_path: Path,
        listed_name: str,
        expected: str,
        unexpected: str,
    ) -> None:
        """``--filter name=`` is a REGEX; the name must be compared exactly.

        ``^NAME$`` looks anchored and is not: with
        ``TEST_DB_CONTAINER=rev8.test.db`` the dots matched a real
        ``rev8-test-db``, so the wrapper printed a DIFFERENT container's
        uptime for a name that does not exist, while the restart path --
        exact-name ``docker inspect`` -- said it did not exist.  ``.``,
        ``-`` and ``_`` are all legal in docker names.

        This drives a stub ``docker`` on PATH rather than a real daemon, so
        it is deterministic and runs everywhere.  The sibling probe below
        exercises the same property against real docker and SKIPS when the
        host offers nothing suitable -- which is what CI does, since the
        runner's only container is a service with a random hex name and no
        separator.  A skip is invisible in a green run, so the property
        needs this case to be graded at all.

        Args:
            tmp_path: pytest-provided directory for the stub.
            listed_name: The container name the stub reports.
            expected: Fragment that must appear in stderr.
            unexpected: Fragment that must not.
        """
        stub = tmp_path / "docker"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            "# Minimal docker stand-in: answers only the one query the\n"
            "# wrapper's no-restart path makes, and answers it the way a\n"
            "# REGEX filter would -- returning a row whose name may differ\n"
            "# from the one asked for.\n"
            'if [ "${1:-}" = "ps" ]; then\n'
            f"    printf '%s|Up 3 hours\\n' '{listed_name}'\n"
            "    exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)

        result = subprocess.run(
            [str(_TEST_RUNNER), "--version"],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "TEST_DB_CONTAINER": "rev8.test.db",
                "RESTART_TEST_DB": "",
            },
            cwd=str(_REPO_ROOT),
            check=False,
        )

        assert expected in result.stderr, (
            f"stub listed {listed_name!r} for probe 'rev8.test.db'; expected "
            f"{expected!r} in: {result.stderr.strip()!r}"
        )
        assert unexpected not in result.stderr, (
            f"stub listed {listed_name!r} for probe 'rev8.test.db'; "
            f"{unexpected!r} must not appear in: {result.stderr.strip()!r}"
        )

    def test_a_name_with_regex_metacharacters_matches_nothing_else(
        self,
    ) -> None:
        """``--filter name=`` is a REGEX, so the name must be matched exactly.

        With ``TEST_DB_CONTAINER=rev8.test.db`` the dots matched a real
        ``rev8-test-db`` and the wrapper reported a DIFFERENT container's
        status as though it were the one asked for -- while the restart
        path, which uses exact-name ``docker inspect``, said the same name
        did not exist. Two paths, contradictory answers, one operator-set
        value; ``.``, ``-`` and ``_`` are all legal in docker names.

        This builds its probe from a container that actually exists, by
        replacing separators with dots, so the regex form would match it and
        the exact form cannot. It only READS from docker -- it creates,
        stops and removes nothing.
        """
        if shutil.which("docker") is None:
            pytest.skip("no docker CLI; this asserts CLI-level behaviour")

        listing = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if listing.returncode != 0:
            pytest.skip("docker daemon unreachable; this needs a real listing")

        real = next(
            (
                name
                for name in listing.stdout.split()
                if "-" in name or "_" in name
            ),
            None,
        )
        if real is None:
            pytest.skip("no container with a separator in its name to probe")

        probe = real.replace("-", ".").replace("_", ".")
        exists = subprocess.run(
            ["docker", "inspect", probe],
            capture_output=True,
            text=True,
            check=False,
        )
        if exists.returncode == 0:
            pytest.skip(f"{probe!r} is itself a real container")

        result = subprocess.run(
            [str(_TEST_RUNNER), "--version"],
            capture_output=True,
            text=True,
            env={**os.environ, "TEST_DB_CONTAINER": probe, "RESTART_TEST_DB": ""},
            cwd=str(_REPO_ROOT),
            check=False,
        )

        assert "no such container" in result.stderr, (
            f"{probe!r} does not exist, but the wrapper reported a state for "
            f"it -- the filter is matching {real!r} as a regex instead of "
            f"comparing the name exactly: {result.stderr.strip()!r}"
        )

    def test_the_daemon_error_is_quoted_not_summarised(self) -> None:
        """The UNKNOWN message repeats what docker actually said.

        The branch asserts a cause from an exit status; without docker's own
        words a DOCKER_HOST typo (a CLI configuration error, healthy daemon)
        is indistinguishable from a daemon that is down.  Measured ungraded
        before this test: deleting the quoted error, and replacing it with a
        constant, both left the suite green while changing live stderr.

        The port from DOCKER_HOST is the tell -- it can only appear in the
        output if docker's message is being passed through.
        """
        if shutil.which("docker") is None:
            pytest.skip("no docker CLI; this asserts CLI-level behaviour")

        port = "59997"
        result = subprocess.run(
            [str(_TEST_RUNNER), "--version"],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "DOCKER_HOST": f"tcp://127.0.0.1:{port}",
                "TEST_DB_CONTAINER": "shekel-unreachable-probe",
                "RESTART_TEST_DB": "",
            },
            cwd=str(_REPO_ROOT),
            check=False,
        )

        assert "docker said:" in result.stderr, (
            f"the UNKNOWN message no longer quotes docker: "
            f"{result.stderr.strip()!r}"
        )
        assert port in result.stderr, (
            "docker's own error is not being passed through -- the message "
            "is a constant or a summary, so it cannot distinguish a dead "
            f"daemon from a misconfigured DOCKER_HOST: {result.stderr.strip()!r}"
        )

    def test_the_classifier_is_found_and_has_every_arm(self) -> None:
        """Extraction works, so the classification below is not vacuous.

        A regex that stops matching does NOT fail quietly -- an empty arm
        list makes ``_classify`` raise, so every parametrized case errors
        loudly.  What this pins is the other direction: extraction that
        still works but returns the WRONG arms, which classification alone
        would not notice as long as each string still reached a compatible
        arm.  (An earlier draft of this docstring claimed the quiet
        fall-through; measured false, and recorded as N-458.)
        """
        assert _case_arms() == list(_EXPECTED_ARMS), (
            f"case arms are {_case_arms()}, expected {list(_EXPECTED_ARMS)}. "
            "An EXACT match is required in both directions: a missing arm "
            "drops a state the wrapper must report, and an ADDED one is a "
            "state nothing here grades -- update _EXPECTED_ARMS, "
            "_PATTERN_STATE and _OBSERVED_STATUSES together."
        )

    def test_paused_is_tested_before_up(self) -> None:
        """``*(Paused)*`` must precede ``Up*``. That order IS the fix.

        Docker renders a paused container as ``Up ... (Paused)``, so an
        earlier ``Up*`` arm claims it and the wrapper calls a SIGSTOPped
        postmaster healthy.
        """
        arms = _case_arms()

        assert arms.index("*(Paused)*") < arms.index("Up*"), (
            f"arm order is {arms}: `Up*` now precedes `*(Paused)*`, so a "
            "paused container is reported as running. Docker's status for a "
            "paused container starts with 'Up'."
        )

    def test_catch_all_is_last(self) -> None:
        """``*`` must be final, or it swallows every state before it."""
        arms = _case_arms()

        assert arms[-1] == "*", f"catch-all is not last in {arms}"
        assert arms.count("*") == 1, f"more than one catch-all in {arms}"

    @pytest.mark.parametrize(("status", "expected"), _OBSERVED_STATUSES)
    def test_real_docker_status_strings_classify_correctly(
        self, status: str, expected: str
    ) -> None:
        """Every observed docker status reaches the arm that describes it.

        Args:
            status: A real ``docker ps -a`` status string.
            expected: The state the wrapper must report for it.
        """
        assert _classify(status) == expected, (
            f"docker status {status!r} classifies as {_classify(status)!r}, "
            f"expected {expected!r}"
        )
