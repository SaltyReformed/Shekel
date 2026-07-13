"""Consistency guard for the pylint hard-gate ``--fail-on`` symbol list.

The project's custom financial-correctness checkers are enforced as pylint hard
failures (``--fail-on=E,F,shekel-...``) in seven executable locations: the three
``pylint`` floor steps in CI (``.github/workflows/ci.yml``: app/, scripts/, and
the ``shekel_checkers`` package), the ``pylint-app`` / ``pylint-scripts`` /
``pylint-checkers`` pre-commit hooks (``.pre-commit-config.yaml``), and the
hard-block tier of the per-edit hook (``scripts/hooks/post-edit-python.sh``,
which spells the same symbols as ``--enable``).

Two drift classes make a hard gate silently stop firing, and pylint reports
NEITHER as an error -- an unknown ``--fail-on`` symbol is ignored, not rejected:

1. A checker symbol is renamed in the ``shekel_checkers`` package but a gate list keeps
   the old (now-unknown) name -- that checker no longer fails the build, and
   nothing complains.
2. One of the copies drifts out of step with the others.

These tests close both.  ``test_gate_list_matches_the_registry`` pins the
canonical list to an exact bijection with the registered checker messages, so
adding, removing, or renaming a checker fails until the list is updated in
lockstep.  ``test_every_gate_location_carries_the_canonical_list`` asserts every
executable location carries that identical list.  Together they turn the
"silently ignored symbol" failure mode into a red test.

Scope note: the same list is echoed in prose (``CLAUDE.md`` command examples,
the CI step comment).  This guard pins the executable gates the audit named
(finding L1); a prose echo that happens to contain the exact string is pinned
incidentally by the content check, but a reworded doc is deliberately not
constrained here.
"""

import inspect
import re
from pathlib import Path

from pylint.checkers import BaseChecker

import shekel_checkers

# The canonical hard-gate list.  ``E`` (errors) and ``F`` (fatals) are pylint
# built-in categories; the rest are this project's custom checkers.  This
# literal is the single source of truth the tests enforce across every gate
# location -- change it here and the location assertion demands the same edit in
# all five gate lists.
_CANONICAL_FAIL_ON = (
    "E,F,"
    "shekel-decimal-from-float,"
    "shekel-refname-compare,"
    "shekel-bare-money-quantize,"
    "shekel-disable-rationale,"
    "shekel-original-principal-as-balance,"
    "shekel-balance-producer-bypass,"
    "shekel-transaction-status-bypass,"
    "shekel-ledger-model-bypass,"
    "shekel-unclassified-fenced-export"
)

# repo root: this file is <root>/tools/pylint/tests/test_*.py
_REPO_ROOT = Path(__file__).resolve().parents[3]

# The financial-correctness list as spelled in a gate file: the ``E,F`` prefix
# followed by one or more comma-joined ``shekel-*`` symbols.  Matches the
# full-list occurrences only -- NOT the single-symbol
# ``--enable=shekel-decimal-from-float`` tests/ scan or the
# ``--fail-on=duplicate-code`` cross-tree hook.
_LIST_RE = re.compile(r"E,F,shekel-[a-z-]+(?:,shekel-[a-z-]+)*")

# Every executable gate file and the minimum number of full-list occurrences it
# must carry (CI: the app + scripts pylint steps; pre-commit: the app + scripts
# hooks; the per-edit hook: the hard-block tier).  ``>=`` so an added gate does
# not trip the floor; the per-occurrence content check pins the actual text.
_GATE_FILES = (
    (Path(".github/workflows/ci.yml"), 2),
    (Path(".pre-commit-config.yaml"), 2),
    (Path("scripts/hooks/post-edit-python.sh"), 1),
)


def _registered_shekel_symbols() -> set[str]:
    """Return the set of ``shekel-*`` message symbols the plugin registers."""
    symbols: set[str] = set()
    for _, obj in inspect.getmembers(shekel_checkers, inspect.isclass):
        if (
            issubclass(obj, BaseChecker)
            and obj is not BaseChecker
            and getattr(obj, "msgs", None)
        ):
            for spec in obj.msgs.values():
                symbols.add(spec[1])
    return symbols


def _canonical_shekel_symbols() -> set[str]:
    """Return the ``shekel-*`` symbols named in the canonical gate list."""
    return {
        token
        for token in _CANONICAL_FAIL_ON.split(",")
        if token.startswith("shekel-")
    }


def test_gate_list_matches_the_registry() -> None:
    """The canonical list names exactly the registered checkers -- a bijection.

    Any drift fails: a NEW checker not yet gated, a REMOVED checker still gated
    (a symbol pylint would silently ignore), or a RENAMED checker (old name
    gated-but-unregistered AND new name registered-but-ungated) all break this
    assertion.  This encodes the project policy that every custom checker is a
    hard gate; an intentionally advisory future checker would have to update
    this test deliberately, not drop its enforcement silently.
    """
    assert _canonical_shekel_symbols() == _registered_shekel_symbols()


def test_every_gate_location_carries_the_canonical_list() -> None:
    """Every executable gate location carries the identical canonical list.

    Reads each gate file, extracts every full-list occurrence, and asserts each
    equals the canonical string and appears at least the expected number of
    times -- so a copy that drifts, or a gate that is deleted, fails here.
    """
    for rel_path, min_occurrences in _GATE_FILES:
        text = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
        matches = _LIST_RE.findall(text)
        assert len(matches) >= min_occurrences, (
            f"{rel_path}: expected >= {min_occurrences} hard-gate list "
            f"occurrence(s), found {len(matches)}."
        )
        for occurrence in matches:
            assert occurrence == _CANONICAL_FAIL_ON, (
                f"{rel_path}: a gate list drifted from the canonical "
                f"--fail-on list.\n  found:     {occurrence}\n"
                f"  canonical: {_CANONICAL_FAIL_ON}"
            )
