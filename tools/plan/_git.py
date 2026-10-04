"""What the code repository's git history says about the plan's cards.

**Whether a card shipped is git's answer, never the card's open or closed
state** (ruling ``balance:R-BAL170``; the card's state is display, which
``plan sync`` writes from this module's answer).  A card is shipped when a
commit on ``dev`` carries the trailer ``Ships: plan#N`` and no LATER commit
carries ``Reopens: plan#N``.

**"Later" is git's ancestry, not a date or the merge order** (ruling
``balance:R-BAL181``).  A ``Reopens:`` commit cancels every ``Ships:`` commit
for the same card in its own history -- the commits it was built on, itself
included -- and no other: a card re-shipped after it was reopened is shipped
again, and a ``Ships:`` commit on a branch the reopening never saw still
stands.  An undo (a revert) is always built on what it undoes, so a
``Reopens:`` that cancels nothing is a STRAY -- most likely a mistyped number
-- and is reported, never acted on.

**Only the trailer block is read, and git parses it** (``%(trailers)``, the
way ``tools/plan_gate/_shipped.py`` reads ``Ships: <arc>:<id>`` until step
X-cx's cutover, L8, deletes it): a sentence that mentions ``plan#7`` claims
nothing.  A value is ``plan#<number>``, or the old ``<arc>:<id>`` form the
plan gate reads, or it is reported as malformed.

**A shallow clone is refused**: its history stops short, and every card
shipped before the cut would read as unshipped.

Every call runs ``git -C <root>`` through :func:`_run`, with the variables
that bind git to some OTHER repository removed, so ``root`` alone decides
which repository is read; nothing here writes the repository but ``fetch``.
"""
from __future__ import annotations

import functools
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from setup_tracker import ARCS

SHIPS, REOPENS = "Ships", "Reopens"
#: The branch every shipped card's commit is on.
DEV = "origin/dev"
#: A trailer's value naming a card: ``plan#`` and the card's number.
_CARD = re.compile(r"plan#([1-9][0-9]*)")
#: The old form, ``<arc>:<id>``, which the plan gate reads until the cutover.
_OLD_FORM = re.compile(rf"(?:{'|'.join(ARCS)}):\S+")
_FIELD, _VALUE, _RECORD = "\x01", "\x02", "\x03"
#: What no user setting may add to git's output (``log.showSignature`` puts gpg
#: lines into a formatted log, which would split a record).
_PLAIN = ("-c", "log.showSignature=false")


class GitError(RuntimeError):
    """A git command failed; carries its stderr."""


@dataclass(frozen=True)
class Trailer:
    """One ``Ships:`` or ``Reopens:`` trailer naming a card."""

    key: str
    card: int
    sha: str
    subject: str


@dataclass(frozen=True)
class History:
    """Every card trailer on a branch, and every value that is neither ``plan#N`` nor the
    old ``<arc>:<id>`` form."""

    trailers: tuple[Trailer, ...]
    malformed: tuple[str, ...]


def repository_root() -> Path:
    """The checkout this file is in (``tools/plan/_git.py`` -> its root)."""
    return Path(__file__).resolve().parents[2]


@functools.cache
def _binding_variables() -> frozenset[str]:
    """The environment variables that bind a git process to ONE repository, as git lists them.

    **A hook exports them, and they override ``-C``.**  Git runs a hook from a
    linked worktree with ``GIT_DIR`` and ``GIT_INDEX_FILE`` set to that
    worktree's (measured 2026-10-04), and with ``GIT_DIR`` set, ``git -C
    <root>`` no longer decides which repository a command reads or writes.
    That day this package's tests, run by the pre-commit hook from a linked
    worktree, moved the shared repository's ``dev`` and wrote
    ``core.bare=true`` into its config.  Git names the set itself (``rev-parse
    --local-env-vars``, the list it clears before entering a submodule), so no
    copy of it is kept here; printing the list reads no repository.
    """
    done = subprocess.run(["git", "rev-parse", "--local-env-vars"],
                          capture_output=True, text=True, check=False)
    if done.returncode or not done.stdout.strip():
        raise GitError(f"git rev-parse --local-env-vars: {done.stderr.strip()}")
    return frozenset(done.stdout.split())


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one git command in ``root`` and in no other repository; the finished process.

    The ONE place this package starts git (and the tests' throwaway
    repositories are built through :func:`git`, so through here too): the
    environment it inherits loses :func:`_binding_variables`, so whatever the
    caller is bound to, ``root`` is the repository touched.
    """
    binding = _binding_variables()
    return subprocess.run(
        ["git", *_PLAIN, "-C", str(root), *args], capture_output=True, text=True, check=False,
        env={name: value for name, value in os.environ.items() if name not in binding},
    )


def git(root: Path, *args: str) -> str:
    """Run one git command in ``root``; its standard output."""
    done = _run(root, *args)
    if done.returncode:
        raise GitError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout


def fetch(root: Path) -> None:
    """Bring :data:`DEV` up to date, so "shipped" is today's answer, not the last fetch's."""
    git(root, "fetch", "--quiet", "origin", "dev")


def history(root: Path, ref: str = DEV) -> History:
    """Every ``Ships:`` and ``Reopens:`` trailer naming a card, on ``ref``.

    One ``git log`` over the whole branch, reading each message's trailer
    block as git parses it.

    Raises:
        GitError: when ``root`` is a shallow clone, whose history stops short.
    """
    if git(root, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise GitError(
            f"{root} is a shallow clone, so it cannot say what shipped: run "
            "`git fetch --unshallow origin` (in CI, check out with fetch-depth: 0)"
        )
    out = git(
        root, "log", ref,
        f"--format=%H{_FIELD}%s{_FIELD}%(trailers:key={SHIPS},valueonly,separator=%x02)"
        f"{_FIELD}%(trailers:key={REOPENS},valueonly,separator=%x02){_RECORD}",
    )
    trailers, malformed = [], []
    for record in filter(str.strip, out.split(_RECORD)):
        sha, subject, ships, reopens = record.strip("\n").split(_FIELD)
        for key, values in ((SHIPS, ships), (REOPENS, reopens)):
            for value in filter(None, (v.strip() for v in values.split(_VALUE))):
                card = _CARD.fullmatch(value)
                if card:
                    trailers.append(Trailer(key, int(card.group(1)), sha, subject))
                elif not _OLD_FORM.fullmatch(value):
                    malformed.append(f"{sha[:12]} {key}: {value!r} is not plan#<number>")
    return History(tuple(trailers), tuple(malformed))


def is_ancestor(root: Path, older: str, newer: str) -> bool:
    """Whether ``older`` is in ``newer``'s history (a commit is in its own)."""
    done = _run(root, "merge-base", "--is-ancestor", older, newer)
    if done.returncode not in (0, 1):
        raise GitError(f"git merge-base --is-ancestor {older} {newer}: {done.stderr.strip()}")
    return done.returncode == 0


def shipped(root: Path, found: History) -> tuple[dict[int, tuple[Trailer, ...]],
                                                 tuple[Trailer, ...]]:
    """``{card: the Ships trailers still standing}`` for every card git says shipped,
    and the STRAY ``Reopens:`` trailers, which cancel no ``Ships:``.

    A ``Ships:`` trailer stands unless a ``Reopens:`` trailer for the same card
    sits on a commit whose history holds it (R-BAL181).
    """
    ships: dict[int, list[Trailer]] = {}
    reopens: dict[int, list[Trailer]] = {}
    for trailer in found.trailers:
        (ships if trailer.key == SHIPS else reopens).setdefault(trailer.card, []).append(trailer)
    standing, cancelling = {}, set()
    for card, claims in ships.items():
        live = []
        for claim in claims:
            undone = [undo for undo in reopens.get(card, ())
                      if is_ancestor(root, claim.sha, undo.sha)]
            cancelling.update(undo.sha for undo in undone)
            if not undone:
                live.append(claim)
        if live:
            standing[card] = tuple(live)
    strays = tuple(undo for undos in reopens.values() for undo in undos
                   if undo.sha not in cancelling)
    return standing, strays


def author_date(root: Path, ref: str) -> str:
    """When ``ref``'s commit was first written, ISO 8601 with its offset.

    The AUTHOR date, which a rebase or an amend keeps: the committer date moves
    later with each, and a window starting at it would then miss what happened
    between the two -- narrowing R-BAL174's review in the unsafe direction.
    """
    return git(root, "log", "-1", "--format=%aI", ref).strip()


def started(root: Path, ref: str) -> str:
    """When the work on branch ``ref`` started: the earliest author date among its own
    commits (those not on :data:`DEV`) and the commits they grew from.

    The commits they grew from are the boundary of that set -- the dev commits
    under each line, whichever parent a merge lists first; the own commits are
    there too because a rebase, a cherry-pick or an amend keeps each one's
    author date while moving it onto a newer base.  So every commit of the
    branch falls inside the window ``spec-history --since <ref>`` reads
    (R-BAL174: the spec edits since the work started).  A ref with no commit off
    :data:`DEV` is read as its own start: a branch just cut from dev starts at
    its tip.  So is a branch already MERGED into dev, whose start git no longer
    records; its window then starts at its last commit.
    """
    dates = git(root, "log", "--boundary", "--format=%aI", ref, "--not", DEV).split()
    if not dates:
        return author_date(root, ref)
    return min(dates, key=datetime.fromisoformat)


def current_branch(root: Path) -> str | None:
    """The branch checked out in ``root``; None when HEAD is detached."""
    done = _run(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if done.returncode not in (0, 1):
        raise GitError(f"git symbolic-ref HEAD in {root}: {done.stderr.strip()}")
    return done.stdout.strip() if done.returncode == 0 else None


def pushed(root: Path, branch: str) -> bool:
    """Whether ``origin`` holds ``branch`` (asked of ``origin``, not the last fetch)."""
    done = _run(root, "ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}")
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
