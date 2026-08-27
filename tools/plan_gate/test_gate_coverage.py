"""The gate grades its own TRIGGER: every document it reads runs it.

**Found by measurement on 2026-08-27, not by reading.**  The hook's `files`
pattern is a YAML scalar that had been re-split across two lines, and YAML
joins a folded scalar with a SPACE -- which put ``| docs/plans/implementation_
plan_bank_import\\.md`` inside the alternation with a leading space, so that
path never matched.  The comment beside the pattern warned about this exact
failure and the file had the failure anyway, which is what a comment is worth
as a safety.

**The blast radius, stated exactly, because the first version of this note
overstated it and a reviewer measured the difference.**  What was lost is the
COMMIT-TIME signal for commits touching only that file: the hook is
``pass_filenames: false``, so any run triggered by another matched file graded
it anyway, and ``.github/workflows/ci.yml`` runs ``pytest tools/plan_gate``
unconditionally on every pull request.  Nothing that reached ``dev`` through a
PR was ungraded.  A local commit got no signal, which is the whole of it.

That is the shape ``balance:X-bb`` exists for -- every arm grades the planning
documents and nothing graded the gate against them -- narrowed here to the one
property this package cannot work without: *a document the gate reads is a
document that runs the gate*.  An arm rather than a longer comment, because the
previous safety WAS a comment.

``yaml`` is a declared dev dependency (``requirements-dev.txt``, used by
``tests/test_deploy``), so this parses the config the way pre-commit does
rather than re-implementing the folding rule that caused the defect.
"""
from __future__ import annotations

import pathlib
import re

import pytest
import yaml

import _duplication as duplication
import _registry as registry

#: The hook whose job is to RUN this package when a planning document changes.
GATE_HOOK_ID = "shekel-plan-ledger-gate"

CONFIG = registry.REPO / ".pre-commit-config.yaml"


def _gate_hook() -> dict:
    """Return the hook definition that runs the plan gate.

    Selected by its ``id`` rather than by a substring of its ``args``: two
    hooks name ``tools/plan_gate`` -- this one and the pylint floor -- and a
    loose match silently graded the wrong one while this arm was being written.

    Returns:
        The hook's parsed mapping.
    """
    config = yaml.safe_load(CONFIG.read_text())
    for repo in config["repos"]:
        for hook in repo.get("hooks", []):
            if hook.get("id") == GATE_HOOK_ID:
                return hook
    raise AssertionError(f"no hook with id {GATE_HOOK_ID!r} in {CONFIG}")


def _graded_documents() -> list[pathlib.Path]:
    """Return every LIVE registry and arc document the gate reads.

    Read from :func:`_duplication.live_docs`, which is the ONE map of live
    planning documents this package keeps, rather than re-listed here.  The
    first draft did re-list them, and two of its entries were hand-spelled
    strings duplicating that map -- a second copy of the list is exactly how a
    document added to the gate goes missing from the arm that checks it runs.

    **What this deliberately does NOT cover, said here rather than left to be
    discovered.**  ``_archive.archived_docs`` walks every ``*.md`` under
    ``docs/`` -- 222 files -- and reads each archived one to grade rule 15.
    The hook matches 11 of those 222, so ADDING an archived document, which is
    the event rule 15 is about, still runs no commit-time gate.  That is a
    real hole and it is ``balance:X-bb``'s, whose subject is the gate's own
    corpus; widening the pattern to all of ``docs/**`` would make every commit
    touching any documentation run the gate, which is a decision rather than a
    tidy-up.  Recorded as a finding rather than fixed here.

    Returns:
        The paths, relative to the repository root.
    """
    seen: dict[pathlib.Path, None] = {}
    for path in duplication.live_docs().values():
        seen.setdefault(path.relative_to(registry.REPO), None)
    return list(seen)


class TestEveryDocumentTheGateReadsRunsIt:
    """A document the gate grades and the hook does not match is ungated."""

    @pytest.mark.parametrize(
        "document", [str(p) for p in _graded_documents()],
    )
    def test_the_hook_matches_it(self, document):
        """Editing this document triggers the gate."""
        pattern = _gate_hook()["files"]
        # ``re.search``, mirroring pre-commit's own matcher exactly rather
        # than relying on the ``^...$`` anchors making the two equivalent.
        assert re.search(pattern, document), (
            f"{document} is graded by tools/plan_gate and the "
            f"{GATE_HOOK_ID} hook does not match it, so editing it runs "
            f"nothing. Add it to the `files` pattern -- ON ONE LINE"
        )

    def test_the_pattern_holds_no_whitespace(self):
        """The defect's MECHANISM, caught directly rather than by its effect.

        A path is matched or not; a space inside the alternation is the ONE
        way this pattern silently stops matching a branch, and it arrives by
        editing rather than by intent.  Grading the mechanism means a NEW
        branch broken the same way fails even before it is in
        :func:`_graded_documents`.
        """
        pattern = _gate_hook()["files"]
        assert not re.search(r"\s", pattern), (
            "the `files` pattern contains whitespace, which means its YAML "
            "scalar was folded across lines -- every alternation branch after "
            "the fold now needs a leading space to match, so it matches "
            "nothing. Put the pattern on ONE line"
        )

    def test_the_hook_actually_runs_this_package(self):
        """A pattern matching everything is worth nothing if the args changed.

        The two halves are independent: this arm is about WHAT runs, the ones
        above about WHEN.  Pinned together so a hook renamed to run something
        else cannot leave the coverage arms passing over a gate that no longer
        exists.
        """
        hook = _gate_hook()
        assert hook["entry"] == "pytest"
        assert "tools/plan_gate" in hook["args"]

    def test_the_control_fires_on_a_folded_pattern(self, tmp_path, monkeypatch):
        """The defect as it actually shipped, reproduced.

        Built by FOLDING the real pattern rather than by writing a broken one,
        so the control exercises the same YAML mechanism the config hit.
        """
        text = CONFIG.read_text()
        pattern = _gate_hook()["files"]
        head, tail = pattern[:60], pattern[60:]
        folded = text.replace(
            f"files: {pattern}", f"files: {head}\n          {tail}", 1,
        )
        assert folded != text, "the real pattern was not found to fold"
        target = tmp_path / ".pre-commit-config.yaml"
        target.write_text(folded)
        monkeypatch.setattr(
            "test_gate_coverage.CONFIG", target, raising=False,
        )
        assert re.search(r"\s", _gate_hook()["files"]), (
            "folding the pattern did not introduce whitespace, so this "
            "control is not exercising the defect it names"
        )
