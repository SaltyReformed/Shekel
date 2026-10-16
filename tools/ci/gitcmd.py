"""Run git in ONE repository: how the modules of ``tools/ci``, ``tools/quill``,
``tools/release`` and ``tools/split`` start git.

Every call runs ``git -C <root>`` through :func:`run`, with every ``GIT_*``
variable removed from the environment it inherits but :data:`PASS_THROUGH`, so
``root`` alone decides which repository is read or written, and no ``GIT_*``
variable the caller exported changes what git answers.  Variables of other names
still reach git: ``HOME``, and with it the user's global config, among them.  The
card-trailer rules
(:mod:`tools.ci.trailers`), the tracker tool's own git calls
(``tools/quill/_git.py``), the release tool, the test-file splitter's walk and
census (``tools/split/_run.py``) and the tests' throwaway repositories
(:mod:`tools.ci.scratch`) all start git here, but for one call:
``scratch._prove_bound``'s control, which must reach the binding this module
removes, to prove a test's sentinel is bound.  The plan gate starts its own
(``tools/plan_gate/_shipped.py``).  Moved here from what is now
``tools/quill/_git.py`` by step X-cx's L4.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

#: What no user setting may add to git's output (``log.showSignature`` puts gpg
#: lines into a formatted log, which would split a record).
_PLAIN = ("-c", "log.showSignature=false")

#: The ``GIT_*`` variables :func:`run` passes on from the caller's environment;
#: every other is removed.  Both set what a commit RECORDS, its author's and its
#: committer's date; of git's reads only ``git var`` answers from them, and no
#: caller asks it.  quill's tests date the scratch commits they build through
#: them (``tools/quill/test__git.py``, ``tools/quill/test_quill.py``).  The
#: census of this module's callers (2026-10-16) found no other ``GIT_*`` a caller
#: sets for git to read; the bound-sentinel cases set ``GIT_DIR`` and its kin so
#: that git must NOT read them (:func:`tools.ci.scratch.bound_sentinel`).
PASS_THROUGH = frozenset({"GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"})


class GitError(RuntimeError):
    """A git command failed; carries its stderr."""


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one git command in ``root`` and in no other repository; the finished process.

    The environment it inherits loses every ``GIT_*`` variable but
    :data:`PASS_THROUGH`.  **A variable git reads decides an answer, whatever
    ``-C`` says.**  Git runs a hook from a linked worktree with ``GIT_DIR`` and
    ``GIT_INDEX_FILE`` set to that worktree's (measured 2026-10-04), and with
    ``GIT_DIR`` set, ``git -C <root>`` no longer decides which repository a
    command reads or writes: that day the tracker tool's tests, run by the
    pre-commit hook from a linked worktree, moved the shared repository's
    ``dev`` and wrote ``core.bare=true`` into its config.  Removing only the
    variables git names as binding a repository (``rev-parse
    --local-env-vars``) was not enough: ``GIT_LITERAL_PATHSPECS`` or
    ``GIT_NOGLOB_PATHSPECS`` made the splitter's importer walk list no file at
    all, and ``GIT_GLOB_PATHSPECS`` or ``GIT_ICASE_PATHSPECS`` made its census
    fail (the second delta review of the split tool, Medium-2, 2026-10-15).  So
    the rule is the prefix: every ``GIT_*`` variable is git's, every name that
    list holds starts with ``GIT_`` (``tools/ci/test_gitcmd.py`` holds git to
    that), and the pass-through names the exceptions.
    """
    return subprocess.run(
        ["git", *_PLAIN, "-C", str(root), *args], capture_output=True, text=True, check=False,
        env={name: value for name, value in os.environ.items()
             if not name.startswith("GIT_") or name in PASS_THROUGH},
    )


def git(root: Path, *args: str) -> str:
    """Run one git command in ``root``; its standard output.

    Raises:
        GitError: when git exits non-zero, with its stderr.
    """
    done = run(root, *args)
    if done.returncode:
        raise GitError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout
