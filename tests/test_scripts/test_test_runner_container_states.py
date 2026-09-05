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
import re
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
_TERMINATOR = re.compile(r"^[ \t]+(;;&?|;&)$", re.M)

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
        terminators = _TERMINATOR.findall(body)

        assert terminators, "no arm terminators found; the extractor is broken"
        assert set(terminators) == {";;"}, (
            f"found {sorted(set(terminators))}: an arm falls through, so the "
            "first matching arm is no longer the only one that runs and this "
            "module's classification model no longer describes the shell"
        )

    def test_the_classifier_is_found_and_has_every_arm(self) -> None:
        """Extraction works, so the classification below is not vacuous.

        A regex that stops matching would make every parametrized case fall
        through to the catch-all and quietly agree with itself.
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
