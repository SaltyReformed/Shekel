"""The tracker tool's own questions of the code repository's git, and its writes there: a
card's claim and its release.

**Whether a card shipped is a TREE's answer** (ruling ``balance:R-BAL359``, the ship
marker, replacing the ``Ships:`` and ``Reopens:`` trailers for quill).  Card N shipped when
the file ``plan/ships/N`` (:func:`marker`) is in the tree of the merge of ``dev`` and
``main`` (:func:`shipped`): a hotfix on ``main`` counts before ``main`` is merged back into
``dev``, and a revert on ``dev`` counts before the release.  A marker's one line is the
instant its card was claimed, so every claim writes different content.  Which change may add
or change a marker -- only the one from the card's own branch -- is CI's one write rule,
which step X-cx's M2 builds: until it lands, nothing refuses a marker any other change adds,
and every reader here takes the markers ``dev`` and ``main`` hold as they are.  This module
writes a marker only in :func:`claim`'s commit.

**A claim is the branch ``card/N`` on ``origin``** (:func:`card_branch`) whose tip neither
``dev`` nor ``main`` holds (:func:`claims`).  ``quill claim`` makes it (:func:`claim`): a
commit adding the card's marker, pushed CREATE-ONLY with a lease on the branch's absence, so
exactly one session wins -- a second claim stacked on the first, a fast-forward a plain push
would take, is refused ``stale info`` (measured on GitHub itself 2026-10-22, X-cx L8's
``m1_lease_probe``).  ``quill release`` moves the work to ``released/N/<instant>`` and then
deletes ``card/N`` with a lease on the tip it read (:func:`release`), so neither abandoned
work nor a newer push is ever lost.

**What a command reads** is :func:`read`, once: the claim branches as ``origin`` lists them
(never this clone's ``origin/card/*``, which a deleted branch leaves behind), then ONE fetch
of ``dev``, ``main`` and each of them, each by its name into its tracking ref
(:func:`fetch`), then the record and the claims from what that fetch brought.

Every call starts git through :mod:`tools.ci.gitcmd`, so ``root`` alone decides which
repository is read or written.  Nothing here writes the repository but :func:`fetch`'s
tracking refs, :func:`claim`'s objects, push and local branch, and :func:`release`'s two
pushes.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tools.ci.gitcmd import GitError, git, run
from tools.ci.trailers import DEV, MAIN, is_ancestor, tracking

#: The directory holding every card's ship marker, one file per card (R-BAL359).
MARKERS = "plan/ships"
#: A claim's branch is this and the card's number (:func:`card_branch`).
CARD_BRANCH = "card/"
#: Where a released claim's work is kept (:func:`release`).
RELEASED = "released/"
#: A card's number, as a marker's file and a claim's branch spell it.
_NUMBER = re.compile(r"[1-9][0-9]*")
#: What a marker's line says, and what :func:`claims` reads back from it.
_CLAIMED = "claimed "


@dataclass(frozen=True)
class Claim:
    """A card a session is building: ``sha``, the tip of the branch ``card/<card>`` on
    ``origin``; ``committed``, when that tip was committed (ISO 8601, with its offset); and
    ``claimed``, the instant the card's marker at that tip says it was claimed, None when the
    tip holds no marker for it."""

    card: int
    sha: str
    committed: str
    claimed: str | None

    @property
    def branch(self) -> str:
        """``card/<card>``."""
        return card_branch(self.card)


@dataclass(frozen=True)
class Shipped:
    """What git says shipped (:func:`shipped`): ``cards``, the card numbers whose markers the
    merge of ``dev`` and ``main`` holds; ``history``, one line for each entry under
    :data:`MARKERS` that is no card's marker, which no tracker write can change (R-BAL184)."""

    cards: frozenset[int]
    history: tuple[str, ...]


@dataclass(frozen=True)
class Reads:
    """What one command reads of git (:func:`read`): what shipped, and every claim by card."""

    shipped: Shipped
    claims: dict[int, Claim]
    #: Each ``card/N`` ``origin`` holds whose tip ``dev`` or ``main`` already holds, with
    #: that tip: merged work whose branch outlived its merge, which no session is building
    #: and over which no claim can be pushed (:func:`claim` creates only).
    merged: dict[int, str]


class ClaimTaken(GitError):
    """``origin`` already holds the branch a claim would create: another session claimed it,
    or wrote this very commit first (:func:`claim`)."""


def card_branch(number: int) -> str:
    """``card/<number>``: the one branch a claim of card ``number`` is."""
    return f"{CARD_BRANCH}{number}"


def marker(number: int) -> str:
    """``plan/ships/<number>``: the one file recording card ``number`` shipped."""
    return f"{MARKERS}/{number}"


def instant(when: datetime) -> str:
    """``when`` in UTC to the second, as a marker records a claim: ``2026-10-22T13:30:00Z``."""
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def on_origin(root: Path) -> dict[int, str]:
    """Every ``card/<number>`` branch ``origin`` holds, by card, with its tip: asked of
    ``origin``, not of this clone's tracking refs.  A branch under ``card/`` named by no card
    number is not a claim and is passed over."""
    found = {}
    for line in git(root, "ls-remote", "origin", f"refs/heads/{CARD_BRANCH}*").splitlines():
        sha, _, ref = line.partition("\t")
        name = ref.removeprefix(f"refs/heads/{CARD_BRANCH}")
        if ref.startswith(f"refs/heads/{CARD_BRANCH}") and _NUMBER.fullmatch(name):
            found[int(name)] = sha
    return found


def fetch(root: Path, cards: Iterable[int] = ()) -> None:
    """Bring :data:`tools.ci.trailers.DEV`, :data:`tools.ci.trailers.MAIN` and the branch
    ``card/N`` of each of ``cards`` up to date in ONE fetch, each by its name into its own
    tracking ref, so "shipped" and "claimed" are today's answers: the refs a claim's push
    writes too (git's update of a pushed branch's tracking ref), and no other."""
    refspecs = [f"+refs/heads/dev:{tracking(DEV)}", f"+refs/heads/main:{tracking(MAIN)}"]
    refspecs += [f"+refs/heads/{card_branch(number)}:refs/remotes/origin/{card_branch(number)}"
                 for number in sorted(set(cards))]
    git(root, "fetch", "--quiet", "origin", *refspecs)


def _merged_tree(root: Path) -> str:
    """The tree of the merge of :data:`DEV` and :data:`MAIN`, as git's three-way merge writes
    it.  A conflict outside :data:`MARKERS` is no record's, and the tree still holds every
    marker; a conflict on a marker leaves that card's record unknown, so it is refused.  The
    merge detects no rename (``-X no-renames``, which no setting can turn back on): two
    markers hold the same line when claimed in the same second, so a marker deleted beside
    another added reads as a rename, and the other side's change to the deleted one would
    land silently in the added one, never the conflict it is (M1's review L1, measured).

    Raises:
        GitError: When a path under :data:`MARKERS` conflicts, or the merge fails.
    """
    done = run(root, "merge-tree", "--write-tree", "-X", "no-renames", "--name-only",
               "--no-messages", "-z", tracking(DEV), tracking(MAIN))
    if done.returncode not in (0, 1):
        raise GitError(f"git merge-tree {DEV} {MAIN}: {done.stderr.strip()}")
    tree, *conflicted = [name for name in done.stdout.split("\0") if name]
    if records := sorted({name for name in conflicted if name.startswith(f"{MARKERS}/")}):
        raise GitError(f"{DEV} and {MAIN} merge with a conflict on {', '.join(records)}, so "
                       "whether those cards shipped is unknown: settle it in the merge of "
                       f"{MAIN} into {DEV} first")
    return tree


def shipped(root: Path) -> Shipped:
    """What git says shipped: every file :data:`MARKERS` holds in the merge of ``dev`` and
    ``main`` (:func:`_merged_tree`) whose name is a card number ships that card; any other
    entry there -- a name that is no number, a file in a directory below it, or
    :data:`MARKERS` itself a file -- is a HISTORY line, acted on nowhere.

    Raises:
        GitError: When a marker conflicts in that merge (:func:`_merged_tree`).
    """
    cards, strays = set(), []
    out = git(root, "ls-tree", "-r", "-z", _merged_tree(root), "--", MARKERS)
    for entry in filter(None, out.split("\0")):
        kind, path = entry.split(" ")[1], entry.partition("\t")[2]
        name = path.removeprefix(f"{MARKERS}/")
        if kind == "blob" and path.startswith(f"{MARKERS}/") and _NUMBER.fullmatch(name):
            cards.add(int(name))
        else:
            strays.append(f"{path} on {DEV} or {MAIN} is no card's marker (a marker is a file "
                           f"{MARKERS}/<card number>): it is not acted on")
    return Shipped(frozenset(cards), tuple(strays))


def _claimed_at(root: Path, sha: str, number: int) -> str | None:
    """The instant card ``number``'s marker at commit ``sha`` says it was claimed (its line,
    less :data:`_CLAIMED`); None when ``sha`` holds no such marker file.

    Raises:
        GitError: When git cannot read ``sha``'s tree or the marker.
    """
    entry = git(root, "ls-tree", "-z", sha, "--", marker(number)).rstrip("\0")
    if not entry or entry.split(" ")[1] != "blob":
        return None
    line = git(root, "cat-file", "blob", entry.split(" ")[2].partition("\t")[0]).strip()
    return line.removeprefix(_CLAIMED) if line.startswith(_CLAIMED) else line


def claims(root: Path, cards: Iterable[int]) -> dict[int, Claim]:
    """The claims among the ``card/N`` branches :func:`fetch` just brought for ``cards``: each
    whose tip neither ``dev`` nor ``main`` holds.  One whose tip they hold is merged work
    whose branch outlived its merge, so no session is building it."""
    found = {}
    for number in sorted(set(cards)):
        tip = git(root, "rev-parse", f"refs/remotes/origin/{card_branch(number)}").strip()
        if is_ancestor(root, tip, tracking(DEV)) or is_ancestor(root, tip, tracking(MAIN)):
            continue
        committed = git(root, "log", "-1", "--format=%cI", tip).strip()
        found[number] = Claim(number, tip, committed, _claimed_at(root, tip, number))
    return found


def read(root: Path) -> Reads:
    """What a command reads of git, once: the claim branches ``origin`` lists
    (:func:`on_origin`), ONE fetch of ``dev``, ``main`` and each of them (:func:`fetch`),
    then what shipped (:func:`shipped`), every claim (:func:`claims`) and every listed
    branch that is none, merged."""
    listed = on_origin(root)
    fetch(root, listed)
    found = claims(root, listed)
    merged = {number: git(root, "rev-parse", f"refs/remotes/origin/{card_branch(number)}").strip()
              for number in sorted(set(listed) - set(found))}
    return Reads(shipped(root), found, merged)


def _marker_changes(root: Path) -> list[tuple[str, str, str, str]]:
    """Every change a commit on ``dev`` or ``main`` made to an entry under :data:`MARKERS`,
    newest first: ``(status, sha, subject, path)``, the status ``A`` added, ``M`` changed,
    ``D`` deleted or ``T`` its type changed.  The ONE walk of the markers' history:
    :func:`history` and :func:`ever_marked` both read it.

    **It lists a commit for every marker any commit of ``dev``'s or ``main``'s history ever
    held.**  Such a marker arrived through a commit holding it whose parents do not: a
    commit of one parent, which ``--full-history`` lists wherever it sits -- git's default
    walk follows a merge through one parent alone when the merge matches it on the path, and
    so lost a ship and its revert on the side it pruned (M1's review H1: three histories,
    measured) -- or a merge none of whose parents holds it, whose change against its FIRST
    parent (``--diff-merges=first-parent``) lists it, as it lists every marker a merge
    brought into ``dev`` or ``main``.  Measured 2026-10-22 (git 2.56) on seven histories,
    one a marker a merge's resolution alone added and another's alone deleted, which
    ``--full-history`` without the merge's change missed.  (Git 2.56 also walked all seven
    fully with the merge's change and no ``--full-history``; nothing in git's manual says
    the one implies the other, so the walk states the flag its argument rests on.)  Read with
    no rename detection, which git turns on by default, so a marker moved to another name
    reads as deleted and added, never ``R``."""
    out = git(root, "log", "--format=%x01%H%x02%s", "--name-status", "--no-renames",
              "--full-history", "--diff-merges=first-parent", tracking(DEV), tracking(MAIN),
              "--", MARKERS)
    found = []
    for record in filter(None, out.split("\x01")):
        head, _, changes = record.partition("\n")
        sha, _, subject = head.partition("\x02")
        found += [(status, sha, subject, path) for status, _, path in
                  (line.partition("\t") for line in changes.splitlines() if line.strip())]
    return found


def history(root: Path, number: int) -> list[tuple[str, str, str]]:
    """Each commit on ``dev`` or ``main`` that added (``A``), changed (``M``), deleted
    (``D``) or changed the type of (``T``) card ``number``'s marker, newest first: ``(status,
    sha, subject)``, a merge that brought it in among them (:func:`_marker_changes`)."""
    return [(status, sha, subject) for status, sha, subject, path in _marker_changes(root)
            if path == marker(number)]


def ever_marked(root: Path) -> frozenset[int]:
    """Every card whose marker a commit on ``dev`` or ``main`` ever held
    (:func:`_marker_changes`): each card git ever said shipped, since "shipped" is a marker
    in the merge of the two, which holds no marker neither side does (no rename is
    detected).  So it holds every card the tool closed as shipped that a revert has since
    unshipped, which ``sync`` reopens, and every card :func:`shipped` names NOW."""
    names = {path.removeprefix(f"{MARKERS}/") for _, _, _, path in _marker_changes(root)}
    return frozenset(int(name) for name in names if _NUMBER.fullmatch(name))


def has_branch(root: Path, name: str) -> bool:
    """Whether this clone holds the local branch ``name``."""
    done = run(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}")
    if done.returncode not in (0, 1):
        raise GitError(f"git rev-parse refs/heads/{name}: {done.stderr.strip()}")
    return done.returncode == 0


def _put(root: Path, tree: str | None, path: list[str], blob: str) -> str:
    """The tree ``tree`` (None: an empty one) with the file at ``path`` (its names, outermost
    first) holding ``blob`` and every other entry as it was, written by ``git mktree``."""
    lines = git(root, "ls-tree", "-z", tree).split("\0") if tree else []
    entries = {line.partition("\t")[2]: line for line in lines if line}
    name, *below = path
    if below:
        held = entries.get(name)
        if held is not None and held.split(" ")[1] != "tree":
            raise GitError(f"{name} is a file where {MARKERS} needs a directory")
        inner = held.split(" ")[2].partition("\t")[0] if held else None
        entries[name] = f"040000 tree {_put(root, inner, below, blob)}\t{name}"
    else:
        entries[name] = f"100644 blob {blob}\t{name}"
    return git(root, "mktree", "-z", stdin="".join(f"{line}\0" for line in entries.values())
               ).strip()


def _pushed(root: Path, *args: str) -> tuple[str, str]:
    """One ``git push --porcelain`` of one ref to ``origin``: its flag (``*`` made, ``=``
    already there, ``-`` deleted, ``!`` refused -- by a lease as ``stale info``, ...) and
    its summary, as git prints them.

    Raises:
        GitError: When git printed no line for the ref (it failed before pushing).
    """
    done = run(root, "push", "--porcelain", *args)
    lines = [line for line in done.stdout.splitlines() if line.count("\t") >= 2]
    if not lines:
        raise GitError(f"git push {' '.join(args)}: {done.stderr.strip() or done.stdout.strip()}")
    flag, _, summary = lines[0].split("\t", 2)
    return flag, summary


def claim(root: Path, number: int, base: str, when: datetime) -> str:
    """Claim card ``number``: a commit on ``base`` adding its marker, ``claimed <when>``
    (:func:`instant`), made from objects alone -- no index, no hook, no working tree -- and
    pushed CREATE-ONLY to ``card/<number>``; its sha.  Only a push that MADE the branch
    (``*``) claims it.  An answer that ``origin`` already holds this very commit (``=``,
    which git gives without testing the lease, measured 2026-10-22) is another session's
    claim: two sessions of one machine, one identity, claiming the same card on the same base
    in the same second write the same commit, and this one pushed second (M1's review L7;
    quill never pushes its own claim twice).

    Raises:
        ClaimTaken: When ``origin`` already holds the branch.
        GitError: When the push fails otherwise.
    """
    blob = git(root, "hash-object", "-w", "--stdin",
               stdin=f"{_CLAIMED}{instant(when)}\n").strip()
    tree = _put(root, git(root, "rev-parse", f"{base}^{{tree}}").strip(),
                [*MARKERS.split("/"), str(number)], blob)
    sha = git(root, "commit-tree", tree, "-p", base, "-m", f"claim plan#{number}").strip()
    ref = f"refs/heads/{card_branch(number)}"
    flag, summary = _pushed(root, f"--force-with-lease={ref}:", "origin", f"{sha}:{ref}")
    if flag == "=" or (flag == "!" and "stale info" in summary):
        raise ClaimTaken(f"{card_branch(number)} is on origin already")
    if flag != "*":
        raise GitError(f"the push of {card_branch(number)} answered {flag} {summary}")
    return sha


def check_out(root: Path, number: int, sha: str) -> str | None:
    """Make the local branch ``card/<number>`` at ``sha``, with no upstream (so nothing is
    written to the shared configuration), and switch to it; None when switched, else what git
    said, the branch made or not."""
    branch = card_branch(number)
    git(root, "branch", "--no-track", branch, sha)
    done = run(root, "switch", "--quiet", branch)
    return None if done.returncode == 0 else done.stderr.strip()


def release(root: Path, number: int, tip: str, when: datetime) -> str:
    """Release card ``number``'s claim, whose branch's tip was read as ``tip``: that work
    pushed create-only to ``released/<number>/<when>`` (compact, since a ref holds no ``:``),
    then ``card/<number>`` deleted with a lease on ``tip``; the branch the work is kept on.

    Raises:
        GitError: When either push is refused or fails -- the delete's lease (``stale
            info``) when the branch moved since it was read, which keeps that newer work, or
            was deleted since.
    """
    kept = f"{RELEASED}{number}/{when.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    flag, summary = _pushed(root, f"--force-with-lease=refs/heads/{kept}:", "origin",
                            f"{tip}:refs/heads/{kept}")
    if flag not in ("*", "="):
        raise GitError(f"the push of {kept} answered {flag} {summary}")
    ref = f"refs/heads/{card_branch(number)}"
    flag, summary = _pushed(root, f"--force-with-lease={ref}:{tip}", "origin", f":{ref}")
    if flag == "!" and "stale info" in summary:
        raise GitError(f"{card_branch(number)} was not deleted ({flag} {summary}): it moved "
                       f"since it was read (or was deleted since), so any newer work is kept; "
                       f"its tip as read is on {kept}")
    if flag != "-":
        raise GitError(f"{card_branch(number)} was not deleted ({flag} {summary}); its tip as "
                       f"read is on {kept}")
    return kept


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
