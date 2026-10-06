"""The tracker tool's own questions of the code repository's git history.

What a card's commit trailers MEAN -- whether a card shipped, ``Ships:`` and
``Reopens:``, "later" as ancestry -- is :mod:`tools.ci.trailers`, the one home
the tool and CI's ``commit_trailers`` check both read (step X-cx's L4 moved it
there).  This module keeps what only the tool asks: bring ``dev`` up to date,
when a branch's work started (``spec-history --since``), which branch is
checked out, whether ``origin`` holds a branch, and which GitHub repository
``origin`` is.

Every call starts git through :mod:`tools.ci.gitcmd`, so ``root`` alone
decides which repository is read; nothing here writes the repository but
``fetch``.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from tools.ci.gitcmd import GitError, git, run
from tools.ci.trailers import DEV


def fetch(root: Path) -> None:
    """Bring :data:`tools.ci.trailers.DEV` up to date, so "shipped" is today's answer, not
    the last fetch's."""
    git(root, "fetch", "--quiet", "origin", "dev")


def author_date(root: Path, ref: str) -> str:
    """When ``ref``'s commit was first written, ISO 8601 with its offset.

    The AUTHOR date, which a rebase or an amend keeps: the committer date moves
    later with each, and a window starting at it would then miss what happened
    between the two -- narrowing R-BAL174's review in the unsafe direction.
    """
    return git(root, "log", "-1", "--format=%aI", ref).strip()


def started(root: Path, ref: str) -> str:
    """When the work on branch ``ref`` started: the earliest author date among its own
    commits (those not on :data:`tools.ci.trailers.DEV`) and the commits they grew from.

    The commits they grew from are the boundary of that set -- every commit not
    of the branch that one of its own commits sits on, under any line a merge
    brought in; the own commits are there too because a rebase, a cherry-pick or
    an amend keeps each one's author date while moving it onto a newer base.  So
    every commit of the branch falls inside the window ``spec-history --since
    <ref>`` reads (R-BAL174: the spec edits since the work started).  A ref with
    no commit off dev is read as its own start: a branch just cut from dev
    starts at its tip.  So is a branch already MERGED into dev, whose start git
    no longer records; its window then starts at its last commit.
    """
    dates = git(root, "log", "--boundary", "--format=%aI", ref, "--not", DEV).split()
    if not dates:
        return author_date(root, ref)
    return min(dates, key=datetime.fromisoformat)


def current_branch(root: Path) -> str | None:
    """The branch checked out in ``root``; None when HEAD is detached."""
    done = run(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if done.returncode not in (0, 1):
        raise GitError(f"git symbolic-ref HEAD in {root}: {done.stderr.strip()}")
    return done.stdout.strip() if done.returncode == 0 else None


def pushed(root: Path, branch: str) -> bool:
    """Whether ``origin`` holds ``branch`` (asked of ``origin``, not the last fetch)."""
    done = run(root, "ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}")
    if done.returncode not in (0, 2):
        raise GitError(f"git ls-remote origin {branch}: {done.stderr.strip()}")
    return done.returncode == 0


def origin_repository(root: Path) -> str:
    """``owner/name`` of the GitHub repository ``origin`` points at."""
    url = git(root, "remote", "get-url", "origin").strip()
    found = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
    if not found:
        raise GitError(f"origin is not a GitHub repository: {url}")
    return found.group(1)
