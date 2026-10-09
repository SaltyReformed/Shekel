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

That is the shape this arm exists for -- every other arm grades the planning
documents and nothing graded the gate against them -- narrowed here to the one
property this package cannot work without: *a document the gate reads is a
document that runs the gate*.  An arm rather than a longer comment, because the
previous safety WAS a comment.

``yaml`` is a declared dev dependency (``requirements-dev.txt``, used by
``tests/test_deploy``), so this parses the config the way pre-commit does
rather than re-implementing the folding rule that caused the defect.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest
import yaml

from tools.ci import arcs
from tools.plan_gate import _duplication as duplication

#: The hook whose job is to RUN this package when a planning document changes.
GATE_HOOK_ID = "shekel-plan-ledger-gate"

#: The hook that LINTS this package at its 10.00/10 floor.
PYLINT_HOOK_ID = "pylint-plan-gate"

CONFIG = arcs.REPO / ".pre-commit-config.yaml"


def _hook(hook_id: str) -> dict:
    """Return the hook definition whose ``id`` is *hook_id*.

    Args:
        hook_id: The hook's ``id`` in the config.

    Returns:
        The hook's parsed mapping.
    """
    config = yaml.safe_load(CONFIG.read_text())
    for repo in config["repos"]:
        for hook in repo.get("hooks", []):
            if hook.get("id") == hook_id:
                return hook
    raise AssertionError(f"no hook with id {hook_id!r} in {CONFIG}")


def _gate_hook() -> dict:
    """Return the hook definition that runs the plan gate.

    Selected by its ``id`` rather than by a substring of its ``args``: two
    hooks name ``tools/plan_gate`` -- this one and the pylint floor -- and a
    loose match silently graded the wrong one while this arm was being written.

    Returns:
        The hook's parsed mapping.
    """
    return _hook(GATE_HOOK_ID)


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
    real hole and it is OPEN: widening the pattern to all of ``docs/**`` would
    make every commit touching any documentation run the gate, which is a
    decision rather than a tidy-up.  CI grades it on every pull request either
    way, so what is missing is the local signal alone.

    Returns:
        The paths, relative to the repository root.
    """
    seen: dict[pathlib.Path, None] = {}
    for path in duplication.live_docs().values():
        seen.setdefault(path.relative_to(arcs.REPO), None)
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
        # The module object itself, not its name spelled as text: the name
        # changed when ``tools/`` became a package, and a spelled name that no
        # longer imports is what this patch would then point at.
        monkeypatch.setattr(sys.modules[__name__], "CONFIG", target)
        assert re.search(r"\s", _gate_hook()["files"]), (
            "folding the pattern did not introduce whitespace, so this "
            "control is not exercising the defect it names"
        )


#: The package whose modules :func:`_imported_ci_modules` reads, for resolving a
#: relative import.
_GATE_PACKAGE = "tools.plan_gate"

#: The bottom layer whose modules the census names.
_CI_PACKAGE = "tools.ci"


def _ci_modules_in(source: str, package: str = _GATE_PACKAGE) -> set[str]:
    """Return the names of the ``tools.ci`` modules one module's *source* imports.

    Every spelling an import takes: ``from tools.ci import arcs``, ``from
    tools.ci.arc_steps import entries``, ``import tools.ci.arcs``, and a relative
    import resolved against *package* (``from ..ci import arcs``).  An import of
    the PACKAGE alone (``from tools import ci``, ``import tools.ci``) is refused:
    it hides which module is read, so no census could name it.

    Args:
        source: A module's text.
        package: The package that module sits in.

    Returns:
        The module names (``arcs``, ``arc_steps``, ...).

    Raises:
        AssertionError: The source imports the ``tools.ci`` package itself.
    """
    found: set[str] = set()
    hidden = "imports the tools.ci PACKAGE, which hides which module it reads: import the module"
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")[:len(package.split(".")) - node.level + 1]
                base = ".".join(parts + ([base] if base else []))
            names = [alias.name for alias in node.names]
            if base == _CI_PACKAGE:
                found.update(names)
            elif base.startswith(f"{_CI_PACKAGE}."):
                found.add(base.split(".")[2])
            elif f"{base}.ci" == _CI_PACKAGE and "ci" in names:
                raise AssertionError(hidden)
        elif isinstance(node, ast.Import):
            for imported in [alias.name for alias in node.names]:
                if imported == _CI_PACKAGE:
                    raise AssertionError(hidden)
                if imported.startswith(f"{_CI_PACKAGE}."):
                    found.add(imported.split(".")[2])
    return found


def _imported_ci_modules() -> list[str]:
    """Return ``tools/ci/<name>.py`` for every ``tools.ci`` module this package imports.

    An AST census of every module here, tests included (:func:`_ci_modules_in`).

    Returns:
        The repository-relative paths, sorted.
    """
    found: set[str] = set()
    for path in (arcs.REPO / "tools" / "plan_gate").glob("*.py"):
        found |= _ci_modules_in(path.read_text(encoding="utf-8"))
    return sorted(f"tools/ci/{name}.py" for name in found)


class TestEveryToolsCiModuleTheGateImportsRunsIt:
    """A ``tools/ci`` module the gate imports is part of the gate.

    Since X-cx's L4 the gate reads the arcs from ``tools/ci/arcs.py``, and since
    L7 an arc document's checkboxes, fence rule and step entries from
    ``tools/ci/arc_steps.py``.  Editing either changes what the gate grades, so
    both hooks must run on it; X-cx L7's A1 review measured that removing
    ``arc_steps`` from both patterns left every test green.
    """

    def test_the_census_finds_the_modules_the_gate_reads(self):
        """A census that finds nothing would pass every case below by grading none."""
        assert {"tools/ci/arcs.py", "tools/ci/arc_steps.py"} <= set(_imported_ci_modules())

    @pytest.mark.parametrize("hook_id", [GATE_HOOK_ID, PYLINT_HOOK_ID])
    @pytest.mark.parametrize("module", _imported_ci_modules())
    def test_both_hooks_match_it(self, module, hook_id):
        """Editing this module runs the gate's tests and its lint."""
        assert re.search(_hook(hook_id)["files"], module), (
            f"tools/plan_gate imports {module} and the {hook_id} hook does not "
            f"match it, so editing it does not run that hook at commit. Add it to "
            f"the hook's `files` pattern -- ON ONE LINE"
        )

    @pytest.mark.parametrize("source, modules", [
        ("from tools.ci import arcs, gitcmd", {"arcs", "gitcmd"}),
        ("from tools.ci.arc_steps import entries", {"arc_steps"}),
        ("import tools.ci.arcs", {"arcs"}),
        ("from ..ci import gitcmd", {"gitcmd"}),
        ("from ..ci.arcs import ARCS", {"arcs"}),
        ("from . import _registry\nfrom tools.quill import check", set()),
    ])
    def test_the_census_reads_every_spelling_of_an_import(self, source, modules):
        """Each spelling, on synthetic source: one a census missed would be ungated."""
        assert _ci_modules_in(source) == modules

    @pytest.mark.parametrize("source", [
        "from tools import ci", "import tools.ci", "from .. import ci",
    ])
    def test_an_import_of_the_package_alone_is_refused(self, source):
        """The package hides which module is read, so the census refuses it."""
        with pytest.raises(AssertionError, match="hides which module"):
            _ci_modules_in(source)
