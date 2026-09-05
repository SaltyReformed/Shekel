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

# Which arm, by its glob, means which state.  The empty pattern is the
# absent arm; anything not named here is the catch-all.
_PATTERN_STATE = {
    "": "absent",
    "*(Paused)*": "paused",
    "Up*": "up",
}


def _case_arms() -> list[str]:
    """Return the classifier's glob patterns, in source order.

    Returns:
        Shell glob patterns with shell quoting removed, ordered as the
        ``case`` statement tests them.  Order is the point: ``case`` takes
        the FIRST match.
    """
    block_match = _CASE_BLOCK.search(_TEST_RUNNER.read_text(encoding="utf-8"))
    assert block_match is not None, (
        'could not find the `case "$_db_status"` block in scripts/test.sh; '
        "if the classifier was renamed or restructured, update this module "
        "rather than deleting it -- the arm ORDER is a real fix"
    )
    arm_pattern = re.compile(
        _ARM_TEMPLATE.format(indent=block_match.group("indent") + " {4}"),
        re.M,
    )
    return [
        arm.replace("'", "").strip()
        for arm in arm_pattern.findall(block_match.group("body"))
    ]


def _classify(status: str) -> str:
    """Classify a docker status string the way the ``case`` would.

    Args:
        status: A ``docker ps -a --format '{{.Status}}'`` value.

    Returns:
        The state name of the first arm whose glob matches.
    """
    for pattern in _case_arms():
        if pattern == "*" or fnmatch.fnmatchcase(status, pattern):
            if pattern == "*":
                return "down"
            return _PATTERN_STATE.get(pattern, "down")
    raise AssertionError(f"no arm matched {status!r}; the case has no catch-all")


class TestTestRunnerContainerStates:
    """The wrapper's container-state classifier, held to real docker output."""

    def test_the_classifier_is_found_and_has_every_arm(self) -> None:
        """Extraction works, so the classification below is not vacuous.

        A regex that stops matching would make every parametrized case fall
        through to the catch-all and quietly agree with itself.
        """
        arms = _case_arms()

        assert len(arms) >= 4, f"expected at least 4 case arms, found {arms}"
        for required in ("", "*(Paused)*", "Up*", "*"):
            assert required in arms, (
                f"the {required!r} arm is missing from {arms}; each one names "
                "a distinct container state the wrapper must report"
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
