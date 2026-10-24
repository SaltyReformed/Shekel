"""Refuse a pull request whose merge writes a ship marker it may not: CI's one write rule
(ruling ``balance:R-BAL359``, the ship marker; step X-cx's M2), judged at the one gate
every change into ``dev`` or ``main`` passes, since each takes a change only through its
merge queue (``R-BAL322``).

**A card shipped when its marker ``plan/ships/N`` is on ``dev`` or ``main``**
(:func:`_git.shipped`), and a release counts the money changes it carries from the markers
it brings to ``main`` (``tools/release``).  So which change may write a marker decides
what every card's state and every release's count say.  Git cannot say which change may
write a path; this check is that one rule, judged once, where the change lands, and never
reconciled after.

**What is judged is the merge that lands.**  A merge-queue group runs on the merge commit
its target becomes, and a pull request's run on GitHub's trial merge of it
(``refs/pull/N/merge``): each a merge M whose first parent is the target's tip and whose
second the pull request's head (measured 2026-10-23 on every queue run so far, each of
one pull request; X-cx L8's ``m2_plan.md`` s.1).  So the change judged is ``M^1 -> M``
(:func:`_git.changes`): what the pull request lands, never what its branch merged from the
target.  Its number comes from the event, a queue group's from the group's branch
``gh-readonly-queue/<base>/pr-<N>-<sha>`` (:func:`named`); its base, head branch and head
commit from GitHub (:func:`pull_from_environment`, with the job's own token).  A merge
whose second parent is not that head -- a group of several pull requests might build one
(unmeasured) -- is not judged (exit 2), so the gate fails closed whatever GitHub builds.  A
pull request into a branch other than ``dev`` or ``main`` lands nothing on either: what it
carries is judged when that branch's own pull request lands it (exit 0, said).  A push to
``main`` was judged in ``main``'s queue, its only way in (exit 0, said).

**The rule, over each entry under ``plan/ships/`` the change writes (rules 1-7), and each
it leaves as it was (rule 8):**

1. **An entry that is no card's marker** is refused, as M's tree reads it by quill's one
   reading of a tree's markers (:func:`_git.lines_in`): a name that is no card number
   (:func:`_git.card_of`), a file in a directory below, ``plan/ships`` itself a file, or no
   file at all, a submodule's commit (a gitlink), which every reader of the markers names
   no card's.
2. **A delete passes, from any branch**: an undo, a revert of the card's merge or a
   person's "not done".  Rules 1 and 3-6 read only what a change adds or changes.
3. **A line the target branch's own line held once never comes back**, from any branch,
   the card's own included (:func:`_git.landings`, read at M's first parent).  A revert of
   the revert would re-ship the card with no change to its record, so a release would count
   none of the code it brings back (the ruling's report, finding 1).  A card undone ships
   again from a fresh claim, which writes a new line.
4. **A line the OTHER integration branch's own line took in passes only as carried from
   it**, from any branch, the card's own included -- a release, a resync, a branch that
   merged the other in: when the change BRINGS a commit of that line through which it took
   the line in (M holds it and M's first parent does not), and does not hold the commit of
   that line through which the branch later undid it (:func:`_git.landings`); that is, the
   merge that landed the card's code and marker together, judged where it landed.  A copy
   of the line, or a merge of the claim commit alone, brings no such commit, and would put
   the record where the code is not (measured 2026-10-23, ``m2_probe/f4.sh`` cases 7 and 8),
   as would a copy made after a release that brought the card's landing and its revert
   together; and a revert of the other branch's revert would re-ship the card with no
   change to its record, then refused by rule 3 at every release (M2's review, HIGH-1 and
   MEDIUM-3, measured).  A cherry-pick is a copy too: a picked marker is refused, and the
   line arrives through the merge that took it in.  The tracker is not read for a carried
   line: it was judged where it first landed, and the tracker has changed since
   (``R-BAL184``).
5. **Any other line only from ``card/N``**, card N's own claim (:func:`_git.claimed_card`), a
   branch of this repository: one of another repository is no claim.  And a change from
   ``card/N`` must add or change ``plan/ships/N``: a branch pushed again after its merge
   records nothing (the report's 5c).
6. **The records-only door** (``R-BAL363``): a change into ``main`` whose every entry, at
   any path, ADDS a card's marker may add one from any branch, for a card no commit of
   ``dev`` or ``main`` ever marked (:func:`_git.ever_marked`) and no claim was ever made for
   (:func:`_git.ever_claimed`): work that landed before records existed, vouched for by
   whoever writes the change.  A claimed card's work is the records' own, recorded by its
   claim.  It is deleted once the history cards (``R-BAL353``) are recorded.
7. **Every card rule 5 or 6 lets a change record is read against the tracker**
   (:func:`refusals`): a card the tracker holds; work (:func:`_state.no_work`, which names a
   container, a ruling, a question or a card with no type for ``quill sync`` too); and not
   still being filed (:func:`_state.filing_unfinished`) nor dropped while it was
   (:func:`_state.filing_ended`; ``R-BAL202``), on which the release tool's card reader
   rests (``tools/release/_cards.py``).  Whether a step is split reads which of its leaves
   git says shipped once M and the other branch meet (the merge of the two, AFTER below),
   as ``quill sync`` reads it once they have.
8. **No marker the change leaves as M's first parent holds it may change what ``dev`` and
   ``main`` together say of its card** (``R-BAL385``, widened by ``R-BAL390``), which rules
   1-7, reading what a change writes, never see.  A resync built by hand writes nothing on
   ``dev``'s line, yet one that keeps the marker a hotfix on ``main`` deleted would make the
   card read shipped again with no change to its record, and the next release would count
   none of its code (X-cx L8's resync probe, measured 2026-10-23); one that drops the
   marker of a hotfix ``main`` holds, keeping its code, would make the card read not
   shipped while its code runs; and one that keeps ``dev``'s line where ``main`` undid it
   and shipped the card again on a new one would leave the next release refused by rule 3
   (L1's review, measured).  So what BEFORE (the merge of M's first parent with the other
   branch) says of each card -- not shipped, shipped on a line, or a conflict -- is
   compared with what AFTER (the merge of M with it) says, each git's one merge that says
   what shipped (:func:`_git.merge`), and a card they answer differently, at a marker M
   leaves, is refused (:func:`_unwritten`), **unless the change shows the delete**
   (``R-BAL391``): where M holds no marker and AFTER settles none, a card BEFORE says
   shipped on a line passes when a commit the change brings -- M's, and neither its first
   parent's nor the other branch's -- deletes that very line in a change of its own, no
   merge (:func:`_shown`), as a delete on the target's own line passes by rule 2: a revert
   made in a branch that merged the other in, or a person's "not done".  A merge's
   resolution shows no delete, whichever its first parent, so a marker one drops stays
   refused, and so does a delete of an older line.  At such a marker the two merges differ only
   when their merge bases do, and those differ only when M brings a commit of the other
   branch its first parent lacks -- a resync, a release, a branch that merged the other in
   -- so a change bringing none can never trip it.  A marker that conflicts BEFORE is
   settled by the change: it passes, whatever AFTER settles it as -- ``dev`` re-shipped the
   card from a fresh claim while ``main`` deleted its old line, and a resync keeps
   ``dev``'s, the ruling's own pass case, a line judged where it landed -- unless the line
   AFTER holds is one the other branch's own line held once and undid, which rule 4
   refuses where a change writes it.  One that conflicts AFTER and not BEFORE refuses to
   judge; one that conflicts in both is passed over, so an app change lands in a week the
   two disagree.

**The tracker is read only when a card must be**, through the card-only reader
(:class:`_tracker.CardReader`) and the token :data:`TOKEN_VARIABLE` holds: never quill's
App, never the board.  In CI that token is an installation token of the read-only App
"Shekel plan reader" (Issues read-only, installed on the tracker only; ``R-BAL289``,
``R-BAL311``), minted by ``ci.yml``'s ``landing`` job for this run alone and revoked when
the job ends (GitHub expires one within the hour in any case).  The App's private key never
reaches this module, but it is in the runner's memory for the whole job, so the design
narrows what a leak from this job yields and does not close it.  A run that is handed no
key mints no token (GitHub documents that a Dependabot run and a fork's pull request get no
Actions secrets), so there a change that needs a card read refuses to judge, and one that
needs none is judged without a read.

**What cannot be read refuses to judge** (exit 2): a command line naming no pull request;
the pull request, when no token or repository is set, GitHub answers an error or cannot be
reached, or its answer lacks a field; a merge that is not the pull request's; git; a marker
the change leaves that conflicts AFTER and not BEFORE (rule 8); when a card must be read,
a marker that conflicts AFTER; and the tracker, as :func:`refusals` says.

Usage, at the repository root::

    python -m tools.quill.landing <event> <merge-sha> <pull>

``<event>`` is the workflow's event name, ``<merge-sha>`` the commit it checked out, and
``<pull>`` a pull request's number or a queue group's branch.  Exit status: 0 nothing
refused; 1 refused, each reason printed; 2 nothing judged, saying why.
"""
from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import requests

from tools.ci.arcs import REPO
from tools.ci.gitcmd import FULL_ID, GitError, git
from tools.ci.trailers import DEV, MAIN, is_ancestor, tracking
from tools.quill import _git
from tools.quill._command import with_closure
from tools.quill._github import GitHub, GitHubError
from tools.quill._state import filing_ended, filing_unfinished, no_work
from tools.quill._tracker import BareCard, CardReader, TrackerError
from tools.quill.setup_tracker import PLAN

#: The environment variable holding the token the check reads the tracker with.
TOKEN_VARIABLE = "PLAN_READ_TOKEN"
#: The environment variable holding the job's own token, which reads the pull request.
PULL_TOKEN_VARIABLE = "GITHUB_TOKEN"
#: The environment variable naming the repository the run is of (a runner default).
REPOSITORY_VARIABLE = "GITHUB_REPOSITORY"
#: Each integration branch, by the name a pull request's base gives it, with its full name.
BRANCHES = {branch.partition("/")[2]: tracking(branch) for branch in (DEV, MAIN)}
#: A pull request's number, as an event names it.
_PULL = re.compile(r"(?P<number>[1-9][0-9]*)")
#: A merge-queue group's branch, which names the last pull request it merges.
_QUEUED = re.compile(r"(?:refs/heads/)?gh-readonly-queue/.+/pr-(?P<number>[1-9][0-9]*)"
                     rf"-(?:{FULL_ID.pattern})")
#: The verb each status of a written entry reads as.
_VERBS = {"A": "adds", "M": "changes", "T": "changes the type of"}


class NotSet(LookupError):
    """An environment variable the check reads is unset or empty."""


class CannotJudge(ValueError):
    """What GitHub says of the pull request, or the merge it checked out, cannot be judged."""


@dataclass(frozen=True)
class PullRequest:
    """What GitHub says of the pull request a run judges: its ``number``; ``base``, the
    branch it merges into; ``head``, its branch, None when that branch is not this
    repository's (a fork's, or one since deleted), so no claim; and ``head_sha``, the
    commit it merges."""

    number: int
    base: str
    head: str | None
    head_sha: str


@dataclass(frozen=True)
class Landing:
    """One pull request's merge as the rule reads it: in ``root``, ``merge`` (M) lands
    ``pull`` on ``onto``, M's first parent; ``entries``, every entry M changes against it
    (:func:`_git.changes`).  What the rules read of git beyond these is read once, when a
    rule first asks (a frozen dataclass admits ``cached_property``, which writes the
    instance's dict directly), so rules 3, 4 and 8 read one walk of each line."""

    root: Path
    merge: str
    onto: str
    pull: PullRequest
    entries: tuple[_git.Entry, ...]

    @property
    def target(self) -> str:
        """The branch the pull request lands on, by its full name."""
        return BRANCHES[self.pull.base]

    @property
    def claim(self) -> int | None:
        """The card whose claim the pull request's head is (:func:`_git.claimed_card`), if
        any: a branch of another repository is none."""
        return _git.claimed_card(self.pull.head) if self.pull.head else None

    @property
    def other(self) -> str:
        """The other integration branch, by its full name."""
        return next(ref for name, ref in BRANCHES.items() if name != self.pull.base)

    @cached_property
    def lines(self) -> Mapping[int, str]:
        """Each card whose marker M's tree holds, with that marker's line, by quill's one
        reading of a tree's markers (:func:`_git.lines_in`)."""
        return _git.lines_in(self.root, self.merge)[0]

    @cached_property
    def held(self) -> Mapping[tuple[str, str], tuple[_git.Took, ...]]:
        """The lines the target's own line took in, read at M's first parent, with the
        commits that took each in and undid it (:func:`_git.landings`)."""
        return _git.landings(self.root, self.onto)

    @cached_property
    def carried(self) -> Mapping[tuple[str, str], tuple[_git.Took, ...]]:
        """The lines the other branch's own line took in, with the commits that took each in
        and undid it (:func:`_git.landings`)."""
        return _git.landings(self.root, self.other)

    @cached_property
    def deleted(self) -> frozenset[tuple[str, str]]:
        """Each marker's line, as ``(path, blob)``, that a commit the change brings deletes
        there, in a change of its own: a commit M holds and neither its first parent nor the
        other branch does, and no merge (:func:`_git.marker_changes`), so a delete it shows
        in history (``R-BAL391``).  A merge's resolution is never one, whichever its first
        parent: a marker it drops shows no delete."""
        brought = (self.merge, f"^{self.onto}", f"^{self.other}")
        return frozenset((change.entry.path, change.was)
                         for change in _git.marker_changes(self.root, brought, no_merges=True)
                         if change.entry.blob is None and change.was is not None)

    def card(self, entry: _git.Entry) -> int | None:
        """The card whose marker ``entry``, an entry M changes, is, as M's tree reads it
        (:attr:`lines`), so rule 1 gives every reader of the markers' answer: None for a
        delete, and for an entry that is no card's marker -- a name that is no card number
        (:func:`_git.card_of`), or no file, a submodule's commit (a gitlink)."""
        number = _git.card_of(entry.path)
        if number is None or entry.blob is None:
            return None
        return number if number in self.lines else None


@dataclass(frozen=True)
class _Facts:
    """What rule 6 reads of git for a change that writes a marker: ``records_only``, whether
    the change is one it reads (into ``main``, every entry an added marker); and for such a
    change, ``claimed``, the cards a claim was ever made for, and ``door``, its cards
    neither that nor ever marked on ``dev`` or ``main``."""

    records_only: bool
    claimed: frozenset[int]
    door: frozenset[int]


def _variable(name: str, what: str) -> str:
    """The value of the environment variable ``name``, without which ``what``.

    Raises:
        NotSet: when that variable is unset or empty.
    """
    value = os.environ.get(name, "")
    if not value:
        raise NotSet(f"{name} is not set, so {what}")
    return value


def reader_from_environment() -> CardReader:
    """The plan tracker's card-only reader, through the token :data:`TOKEN_VARIABLE` holds.

    Raises:
        NotSet: when that variable is unset or empty.
    """
    return CardReader(GitHub(_variable(TOKEN_VARIABLE, "the tracker cannot be read")), PLAN)


def pull_request(number: int, answer: object, repository: str) -> PullRequest:
    """Pull request ``number`` as GitHub's REST answer ``answer`` describes it, its head
    branch read as a claim only when it is a branch of ``repository``.

    Raises:
        CannotJudge: when the answer lacks a field the rule reads, or holds one that is no
            text.
    """
    try:
        base, head = answer["base"]["ref"], answer["head"]
        branch, sha, repo = head["ref"], head["sha"], head["repo"]
        owner = None if repo is None else repo["full_name"]
    except (KeyError, TypeError) as error:
        raise CannotJudge(f"GitHub's answer for pull request #{number} lacks {error}") from error
    if not all(isinstance(value, str) for value in (base, branch, sha)) or not isinstance(
            owner, str | None):
        raise CannotJudge(f"GitHub's answer for pull request #{number} holds a base, head or "
                          "head repository that is no text")
    return PullRequest(number, base, branch if owner == repository else None, sha)


def pull_from_environment(number: int) -> PullRequest:
    """Pull request ``number`` of the repository :data:`REPOSITORY_VARIABLE` names, read
    from GitHub with the token :data:`PULL_TOKEN_VARIABLE` holds (:func:`pull_request`).

    Raises:
        NotSet: when either variable is unset or empty.
        CannotJudge: as :func:`pull_request` raises it.
        _github.GitHubError: when GitHub answers the read with an error.
        requests.RequestException: when GitHub cannot be reached.
    """
    token = _variable(PULL_TOKEN_VARIABLE, "the pull request cannot be read")
    repository = _variable(REPOSITORY_VARIABLE, "the pull request cannot be read")
    answer = GitHub(token).rest("GET", f"/repos/{repository}/pulls/{number}")
    return pull_request(number, answer, repository)


def named(args: list[str]) -> int | None:
    """The pull request a command line names (the module's usage): a pull request's number
    for ``pull_request``, the number a queue group's branch ends with for ``merge_group``,
    each beside a full commit id; None for any other command line."""
    if len(args) != 3 or not FULL_ID.fullmatch(args[1]):
        return None
    event, _, pull = args
    found = {"pull_request": _PULL, "merge_group": _QUEUED}.get(event)
    match = found.fullmatch(pull) if found else None
    return int(match.group("number")) if match else None


def _landing(root: Path, merge: str, pull: PullRequest) -> Landing:
    """What the merge ``merge`` lands of ``pull`` (:class:`Landing`).

    Raises:
        CannotJudge: when ``merge`` is not a merge of two parents whose second is
            ``pull``'s head.
        tools.ci.gitcmd.GitError: when git cannot read ``merge`` or its change.
    """
    _, *parents = git(root, "rev-list", "--parents", "-n", "1", merge).split()
    if len(parents) != 2 or parents[1] != pull.head_sha:
        raise CannotJudge(
            f"{merge[:12]} is not a merge of pull request #{pull.number}'s head "
            f"{pull.head_sha[:12]} into its base (its parents: "
            f"{', '.join(parent[:12] for parent in parents) or 'none'})")
    return Landing(root, merge, parents[0], pull, _git.changes(root, parents[0], merge))


def _under_markers(path: str) -> bool:
    """Whether ``path`` is under :data:`_git.MARKERS`, as git's pathspec reads it there:
    the directory's own name, or any path below it."""
    return path == _git.MARKERS or path.startswith(f"{_git.MARKERS}/")


def _facts(landing: Landing) -> _Facts:
    """What rule 6 reads of git for ``landing``'s written markers (:class:`_Facts`)."""
    root, entries = landing.root, landing.entries
    records_only = landing.target == tracking(MAIN) and all(
        entry.status == "A" and landing.card(entry) is not None for entry in entries)
    claimed = _git.ever_claimed(root) if records_only else frozenset()
    door = (frozenset(landing.card(entry) for entry in entries) - _git.ever_marked(root)
            - claimed if records_only else frozenset())
    return _Facts(records_only, claimed, door)


def _carried(landing: Landing, took: Iterable[_git.Took]) -> bool:
    """Rule 4: whether the change brings one of the commits ``took`` names, through which
    the other branch's own line took a line in, without the commit through which it undid
    it."""
    root, merge = landing.root, landing.merge
    return any(is_ancestor(root, each.sha, merge) and not is_ancestor(root, each.sha, landing.onto)
               and (each.until is None or not is_ancestor(root, each.until, merge))
               for each in took)


def _unclaimed(landing: Landing, facts: _Facts, entry: _git.Entry, number: int) -> str:
    """Why ``entry``, a written marker of card ``number`` that no rule passes, is refused."""
    if facts.records_only:
        before = (f"a claim was made for card {number}" if number in facts.claimed
                  else f"card {number} was marked on {DEV} or {MAIN} before")
        return (f"{entry.path}: a change that only adds markers records work landed before "
                f"records existed, but {before}, so only {_git.card_branch(number)} may "
                "record it")
    return (f"{entry.path}: {landing.pull.head or 'a branch of another repository'} "
            f"{_VERBS.get(entry.status, 'writes')} it, but only card {number}'s own claim, "
            f"{_git.card_branch(number)}, may record it")


def _stray(entry: _git.Entry) -> str:
    """Rule 1: why ``entry``, which the change adds or changes and which is no card's
    marker (:meth:`Landing.card`), is refused."""
    if (number := _git.card_of(entry.path)) is None:
        return (f"{entry.path}: no card's marker is named so (a marker is the file "
                f"{_git.MARKERS}/<card number>), so no change may add or change it")
    return (f"{entry.path}: a marker is a file, and what this change writes there is none (a "
            f"submodule's commit), which no reader counts as card {number}'s marker, so no "
            "change may add or change it")


def _written(landing: Landing, facts: _Facts,
             entry: _git.Entry) -> tuple[str | None, int | None]:
    """Rules 1 and 3-6 over ``entry``, a marker path the change adds or changes: why it is
    refused, or None; and the card the tracker must be read for, or None."""
    if (number := landing.card(entry)) is None:
        return _stray(entry), None
    if (entry.path, entry.blob) in landing.held:
        return (f"{entry.path}: {_git.shown(landing.target)} held this very line once and undid "
                f"it, so it would re-ship card {number} with no change to its record, and a "
                "release would count none of the code it brings back: a card undone ships "
                f"again from a fresh claim (quill claim {number}), which writes a new line"), None
    if took := landing.carried.get((entry.path, entry.blob)):
        if _carried(landing, took):
            return None, None
        other = _git.shown(landing.other)
        return (f"{entry.path}: {other} took this very line in, but this change does not bring "
                "the commit through which it did, or holds the one through which it undid it, "
                f"so it would record card {number} where its code is not, or re-ship it with no "
                f"change to its record: the line comes from {other} only through the merge that "
                "took it in, and a card undone ships again from a fresh claim (quill claim "
                f"{number}), which writes a new line"), None
    if number in (landing.claim, *facts.door):
        return None, number
    return _unclaimed(landing, facts, entry, number), None


def _judged(landing: Landing) -> tuple[list[str], set[int]]:
    """One sentence for each refusal of rules 1-6 over ``landing``'s entries under
    :data:`_git.MARKERS`, and the cards it records that the tracker must be read for."""
    written = [entry for entry in landing.entries
               if _under_markers(entry.path) and entry.blob is not None]
    claim = landing.claim
    reasons = []
    if claim is not None and not any(landing.card(entry) == claim for entry in written):
        reasons.append(f"{landing.pull.head} adds or changes no {_git.marker(claim)}, so it "
                       f"records nothing, though a change from {landing.pull.head} records "
                       f"card {claim} (pushed again after its merge? later work is a card of "
                       "its own)")
    read: set[int] = set()
    if written:
        facts = _facts(landing)
        for entry in written:
            why, number = _written(landing, facts, entry)
            if why:
                reasons.append(why)
            if number is not None:
                read.add(number)
    return reasons, read


def _settled(root: Path, merged: _git.Merged) -> dict[int, str]:
    """Each card the merge ``merged`` says shipped, with its marker's line there: each whose
    marker its tree holds (:func:`_git.lines_in`) and does not conflict there, where the tree
    holds no settled record (:class:`_git.Merged`)."""
    return {number: line for number, line in _git.lines_in(root, merged.tree)[0].items()
            if _git.marker(number) not in merged.conflicts}


def _undid(landing: Landing, line: tuple[str, str | None]) -> bool:
    """Whether the other integration branch's own line held ``line``, a marker's ``(path,
    blob)``, and holds it no longer (:attr:`Landing.carried`)."""
    took = landing.carried.get(line)
    return bool(took) and all(each.until is not None for each in took)


def _moved(landing: Landing, number: int, was: str | None, now: str | None) -> str:
    """Rule 8's sentence for card ``number``, whose marker the change leaves as its first
    parent holds it, where BEFORE settles that marker as ``was`` and AFTER as ``now``, which
    differ: each its line, or None where the merge holds none.

    A settled merge of one marker holds one side's state of it, or a line git merged from
    the two: so where AFTER holds M's own (the target's, which M leaves as it was, ``kept``),
    the change keeps it against what git's merge of the two takes (BEFORE); and where AFTER
    holds another, what the change brings of the other branch moves their merge's answer."""
    path, kept = _git.marker(number), landing.lines.get(number)
    target, other = _git.shown(landing.target), _git.shown(landing.other)
    meet = f"once this change and {other} meet"
    if was is None:
        reads = f"card {number} would read shipped {meet}, as it does not"
    elif now is None:
        reads = f"card {number} would read not shipped {meet}, where it reads shipped"
    else:
        reads = f"card {number} would read shipped on another line {meet} than"
    claim = f"claim card {number} again (quill claim {number}), which writes a new line"
    if now != kept and kept is None:
        why = (f"{target} deleted it, and what this change brings of {other} makes their merge "
               f"take {other}'s line instead, which {other} holds again after a commit of its "
               f"history held none, with no change to its record; revert on {other} what took "
               "it back (a revert deletes the marker with its code), or leave out what this "
               f"change brings of {other}")
    elif now != kept:
        why = (f"what it brings of {other} moves their merge's answer, with no change to its "
               f"record; leave out what it brings of {other}")
    elif was is None:
        why = ("a merge keeping a marker the other side deleted re-ships a card with no change "
               "to its record, so a release would count none of its code; take the delete "
               f"their merge takes, or {claim}")
    elif now is None:
        why = (f"it drops card {number}'s marker, which {other} holds, with no change to its "
               "record, and no commit it brings, merges aside, deletes that very line; take "
               f"{other}'s file, as their merge does, then, if this change reverts card "
               f"{number}, revert it in a commit that is no merge (a revert deletes the marker "
               "with its code)")
    else:
        undid = f", which {other} held once and undid," if _undid(landing, (path, now)) else ""
        why = (f"it keeps {target}'s line{undid} where their merge takes another, with no "
               "change to its record; take the line their merge takes")
    return (f"{path}: {reads} once {target} and {other} do, though this change writes no line "
            f"of it: {why}")


def _shown(landing: Landing, number: int, was: str | None, now: str | None) -> bool:
    """Whether card ``number``'s marker, which BEFORE settles as ``was`` and AFTER as
    ``now``, is a delete shown in history (``R-BAL391``): M holds no marker there and AFTER
    none, and a commit the change brings deletes BEFORE's very line in a change of its own
    (:attr:`Landing.deleted`) -- a revert made in a branch that merged the other in, or a
    person's "not done".  Where M holds a marker the change deletes none, so whatever moves
    AFTER's answer is what it brings of the other branch."""
    return (was is not None and now is None and number not in landing.lines
            and (_git.marker(number), was) in landing.deleted)


def _unwritten(landing: Landing, after: _git.Merged, now: Mapping[int, str]) -> list[str]:
    """Rule 8 (``R-BAL385``, widened by ``R-BAL390``): one sentence for each card whose
    marker the change leaves as its first parent holds it, where AFTER (``after``, the merge
    of M with the other branch, which settles the lines ``now``) answers otherwise than
    BEFORE (the merge of M's first parent with it): not shipped, or shipped on a line.
    Refused where BEFORE settles it (:func:`_moved`), unless the change shows the delete
    in a commit of its own (:func:`_shown`, ``R-BAL391``); and where BEFORE conflicts on it,
    refused only when AFTER settles it on a line the other branch's own line held once and
    undid (:func:`_undid`).  AFTER's line there is the target's own (M's, which it holds),
    the other's (a card shipped there, which their merge reports as it is), or git's merge
    of the two, which neither held: so only the first can re-ship what a branch undid, and
    only the other branch can have.

    Raises:
        _git.MarkerConflict: When a marker the change leaves conflicts AFTER and not BEFORE.
        tools.ci.gitcmd.GitError: When git cannot merge or read either.
    """
    root = landing.root
    target, other = _git.shown(landing.target), _git.shown(landing.other)
    before = _git.merge(root, (landing.onto, landing.other))
    left = {entry.path for entry in landing.entries}
    if brought := tuple(sorted(set(after.conflicts) - set(before.conflicts) - left)):
        raise _git.MarkerConflict(
            f"{_git.shown(landing.merge)} and {other} merge with a conflict on "
            f"{', '.join(brought)}, "
            f"which {target} and {other} merge without, though this change "
            "writes none of them, so whether those cards shipped would be unknown once it "
            f"lands: settle it by merging {other} into this change first", brought)
    was = _settled(root, before)
    reasons = []
    for number in sorted(set(was) | set(now)):
        path = _git.marker(number)
        if path in left or was.get(number) == now.get(number):
            continue
        if path not in before.conflicts:
            if not _shown(landing, number, was.get(number), now.get(number)):
                reasons.append(_moved(landing, number, was.get(number), now.get(number)))
        elif _undid(landing, (path, now[number])):
            reasons.append(
                f"{path}: {target} and {other} conflict on it, and this change "
                f"settles it on a line {other} held once and undid, though it writes no line "
                f"of it, so it would re-ship card {number} with no change to its record, and a "
                "release would count none of its code: a card undone ships again from a fresh "
                f"claim (quill claim {number}), which writes a new line")
    return reasons


def _wrong(number: int, card: BareCard | None, cards: Mapping[int, BareCard],
           shipped: Iterable[int]) -> str | None:
    """What the tracker says is wrong with a change recording card ``number``, whose card
    is ``card`` (None when the tracker does not hold it), as the end of a sentence; None
    when nothing is.  ``cards`` holds every card a decision over it reads
    (:func:`_command.with_closure`), and ``shipped`` git's answer once the change and the
    other integration branch meet."""
    if card is None:
        return (f"the tracker holds no plan#{number} (a mistyped number?): a marker records "
                "a card the tracker holds")
    if what := no_work(card, cards, shipped):
        return (f"plan#{number} is {what}, which no change ships: a marker records a step or "
                "a finding (a step split into smaller steps ships as them)")
    if filing_unfinished(card):
        return f"plan#{number} is still being filed: finish it with quill file, then land"
    if filing_ended(card):
        return (f"plan#{number} was dropped while still being filed: if it is this change's "
                "work, reopen it and finish it with quill file, then land")
    return None


def refusals(numbers: set[int], shipped: Iterable[int],
             connect: Callable[[], CardReader]) -> list[str]:
    """One sentence for each card of ``numbers`` a change records that the tracker refuses
    (rule 7), by card; ``shipped`` is git's answer once the change and the other
    integration branch meet.  The tracker is read through the reader ``connect`` makes.

    Raises:
        NotSet: when ``connect`` has no token to read with.
        _tracker.TrackerError: when a card links to one the tracker lacks, or GitHub's
            answer is cut short, answers a list as null or holds a null node in one.
        _github.GitHubError: when GitHub answers a read with an error.
        requests.RequestException: when the tracker cannot be reached.
    """
    reader = connect()
    held = reader.cards(numbers)
    cards = with_closure(reader, {number: held[number] for number in numbers if number in held})
    shipped = set(shipped)
    return [f"{_git.marker(number)}: {wrong}" for number in sorted(numbers)
            if (wrong := _wrong(number, held.get(number), cards, shipped))]


def problems(root: Path, merge: str, pull: PullRequest,
             connect: Callable[[], CardReader]) -> tuple[int, list[str]]:
    """How many entries under :data:`_git.MARKERS` the merge ``merge`` of ``pull`` writes or
    deletes, and one sentence for each refusal (the module's rules); the tracker is read
    only for a card the change records by rule 5 or 6.  The merge of ``merge`` with the
    other branch (AFTER) is made once, and what it says shipped read once, by rules 7 and 8.

    Raises:
        CannotJudge: as :func:`_landing` raises it.
        tools.ci.gitcmd.GitError: when git cannot read the merge, its change or a branch's
            line, or merge either tip with the other branch; or a marker conflicts AFTER
            where :func:`_unwritten` says, or anywhere once a card must be read.
        NotSet, _tracker.TrackerError, _github.GitHubError, requests.RequestException: as
            :func:`refusals` raises them.
    """
    landing = _landing(root, merge, pull)
    reasons, read = _judged(landing)
    after = _git.merge(root, (merge, landing.other))
    now = _settled(root, after)
    reasons += _unwritten(landing, after, now)
    if read:
        after.settled()  # rule 7 reads which leaves shipped, unknown where a marker conflicts
        reasons += refusals(read, now.keys(), connect)
    return sum(1 for entry in landing.entries if _under_markers(entry.path)), reasons


def _verdict(root: Path, merge: str, pull: PullRequest,
             connect: Callable[[], CardReader]) -> int:
    """Judge the merge ``merge`` of ``pull``; print each refusal and the verdict, or why
    nothing was judged; exit as the module says."""
    try:
        count, reasons = problems(root, merge, pull, connect)
    except (CannotJudge, GitError) as error:
        print(f"landing check: {merge[:12]} could not be judged, so nothing was checked: "
              f"{error}", file=sys.stderr)
        return 2
    except (NotSet, GitHubError, requests.RequestException) as error:
        print(f"landing check: the tracker could not be read, so nothing was checked: {error}",
              file=sys.stderr)
        return 2
    except TrackerError as error:
        print("landing check: what the tracker answered cannot be judged, so nothing was "
              f"checked: {error}", file=sys.stderr)
        return 2
    for reason in reasons:
        print(f"REFUSED -- {reason}")
    print(f"landing check: pull request #{pull.number} into {pull.base} from "
          f"{pull.head or 'a branch of another repository'}: {count} ship marker change(s), "
          f"{len(reasons)} refused")
    return 1 if reasons else 0


def main(argv: list[str] | None = None,
         connect: Callable[[], CardReader] = reader_from_environment,
         pulls: Callable[[int], PullRequest] = pull_from_environment, root: Path = REPO) -> int:
    """Judge the pull request the command line names; print each refusal; exit as the module
    says."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 3 and args[0] == "push":
        print("landing check: a push to main is judged in main's merge queue, its only way in "
              "(R-BAL322), so nothing is judged here")
        return 0
    if (number := named(args)) is None:
        print("usage: python -m tools.quill.landing <event> <merge-sha> <pull> (pull_request "
              "and a pull request's number, or merge_group and its queue branch, beside the "
              f"full commit id checked out; or push); given {args!r}", file=sys.stderr)
        return 2
    try:
        pull = pulls(number)
    except (NotSet, CannotJudge, GitHubError, requests.RequestException) as error:
        print(f"landing check: pull request #{number} could not be read, so nothing was "
              f"checked: {error}", file=sys.stderr)
        return 2
    if pull.base not in BRANCHES:
        print(f"landing check: pull request #{number} merges into {pull.base}, neither dev nor "
              f"main: what it carries is judged when {pull.base}'s own pull request lands it")
        return 0
    return _verdict(root, args[1], pull, connect)


if __name__ == "__main__":
    sys.exit(main())
