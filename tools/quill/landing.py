"""Refuse a change set whose card trailers name what the tracker says no commit may land:
CI's landing check (step X-cx's L9d, leaf a; ruling ``balance:R-BAL289``, "CI's trailer
check reads the tracker with a read-only token", whose token ``balance:R-BAL311`` makes
one minted per run from a read-only GitHub App).

:mod:`tools.ci.commit_trailers` reads git alone: a trailer's shape, what a revert owes,
and a ``Reopens:`` that undoes nothing in its own history.  This check asks the tracker
what each card a trailer names IS, before it lands:

1. **A card the tracker holds.**  A ``Ships: plan#N`` or ``Reopens: plan#N`` naming a
   number the tracker has no card for is refused: most likely a mistyped number, and
   once on ``dev`` it is permanent history no tracker write can change (``R-BAL184``).
2. **A ``Ships:`` names work** (:func:`_state.is_work`: a step or a finding, and not a
   step split into smaller steps).  One naming a container, a ruling, a question or a
   card with no type is refused, saying which (:func:`_state.no_work`, which names such a
   card for ``quill sync`` too): a split step's work is its smaller steps (``R-BAL177``),
   a ruling is a record and a question the developer's.
3. **A ``Ships:`` names no card that carries the filing mark** (``R-BAL202``), by
   quill's two readings of it.  One still being filed (:func:`_state.filing_unfinished`):
   "still being filed: finish it with quill file, then land".  One CLOSED while still
   marked (:func:`_state.filing_ended`), dropped before its filing finished: reopened, its
   ``quill file`` command run again finishes it, linking it under its split step again
   if ``sync`` unlinked it, so the refusal says that or to name the work the commit
   builds.  So no card production holds carries the mark, which the release tool's card
   reader rests on (``tools/release/_cards.py``: ``sync`` unlinks a step closed while
   still being filed from its group, so one shipped after that would be held under no
   group; the coordinator's re-ruling, under ``R-BAL207``, 2026-10-17).
4. **A ``Reopens:`` is refused only when the tracker lacks its card.**  It may name any
   card production holds, a split step among them: a release takes a deploy-together
   group out of production by a ``Reopens:`` for each member it would keep
   (``tools/release/_cards.py``), so a refusal here would leave that way out unable to
   land.

**Each trailer is judged once, when it first lands** (the coordinator, under
``R-BAL207``, 2026-10-17; ``main`` beside ``dev`` since the L9d delta review's
MEDIUM-1).  The tracker changes after a trailer lands -- a step shipped, undone and
re-planned into smaller steps is a container now, which a release judges as the work it
was (``tools/release/_cards.py``) -- so this check re-run over a trailer ``dev`` or
``main`` already holds could refuse permanent history for good (``R-BAL184``): a release
branch's own commit, judged at its pull request into ``main``, would be judged again by
the push to ``main`` and by the pull request bringing ``main`` back into ``dev``.  So of
the change set ``<base>..<head>`` it judges only the commits that have not landed, which
neither :data:`tools.ci.trailers.DEV` nor :data:`tools.ci.trailers.MAIN` holds yet:
every commit of a pull request or a merge-queue group into ``dev``, of a pull request
into ``main`` a release branch's own, and of a push to ``main``, which ``main`` then
holds, none.  A change set wholly on ``dev`` or ``main`` judges nothing and says so; a
checkout that cannot read either refuses to judge.  Never judged here, since no pull
request's change set holds it: a commit pushed straight to ``dev``, an administrator's
push straight to ``main`` (its protection does not bind administrators), and the merge
commit GitHub writes when it merges a pull request, whose message is that pull request's
title (2026-10-18: merge commits only, ``merge_commit_message: PR_TITLE``).

**Whether a card is work is git's answer once those commits land**: whether a step is
split reads which of the steps linked under it git says shipped (a step closed while
still being filed is a leaf of it only once shipped, :func:`_state.leaves`), so the
shipped set is ``dev``'s history and those commits together
(:func:`tools.ci.trailers.shipped`).

**The tracker is read only when a trailer names a card**, through the card-only reader
(:class:`_tracker.CardReader`) and the token :data:`TOKEN_VARIABLE` holds: never quill's
App, never the board.  In CI that token is an installation token of the read-only App
"Shekel plan reader" (Issues read-only, installed on the tracker only; ``R-BAL311``),
minted by ``ci.yml``'s ``landing`` job for this run alone and revoked when the job ends
(GitHub expires one within the hour in any case).
The App's private key never reaches this module, but it is in the runner's memory for the
whole job, so the design narrows what a leak from this job yields and does not close it.
A run that is handed no key mints no token (GitHub documents that a Dependabot run and a
fork's pull request get no Actions secrets), so there a change set naming a card refuses
to judge, and one naming none passes without a read.  **What
cannot be read refuses to judge**: no token, an error GitHub answers, a tracker that
cannot be reached, a list GitHub cut short or answered as null, or a card linked to one
the tracker lacks exits 2, nothing checked.  So does a change set holding no commit, as
:mod:`tools.ci.commit_trailers` refuses one.

Usage, at the repository root::

    python -m tools.quill.landing <base-sha> <head-sha>

The two ends are read by :func:`tools.ci.commit_trailers.ends`, the one rule for them;
the commits that have not landed by :func:`tools.ci.commit_trailers.not_landed`, the one
rule for those, which that check's stray rule reads too; the verdict is printed by
:func:`tools.ci.commit_trailers.report`, and a git failure by
:func:`tools.ci.commit_trailers.unjudged`.  Exit status: 0 nothing refused; 1 refused,
each reason printed; 2 the command line, git or the tracker's read failed, ``dev`` or
``main`` cannot be read, or the change set holds no commit, so nothing was checked.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import requests

from tools.ci import trailers
from tools.ci.arcs import REPO
from tools.ci.commit_trailers import ends, not_landed, report, unjudged
from tools.ci.gitcmd import GitError, git
from tools.quill._command import with_closure
from tools.quill._github import GitHub, GitHubError
from tools.quill._state import filing_ended, filing_unfinished, no_work
from tools.quill._tracker import BareCard, CardReader, TrackerError
from tools.quill.setup_tracker import PLAN

#: The environment variable holding the token the check reads the tracker with.
TOKEN_VARIABLE = "PLAN_READ_TOKEN"


class NoToken(LookupError):
    """No token to read the tracker with: :data:`TOKEN_VARIABLE` is unset or empty."""


def reader_from_environment() -> CardReader:
    """The plan tracker's card-only reader, through the token :data:`TOKEN_VARIABLE` holds.

    Raises:
        NoToken: when that variable is unset or empty.
    """
    token = os.environ.get(TOKEN_VARIABLE, "")
    if not token:
        raise NoToken(f"{TOKEN_VARIABLE} is not set, so the tracker cannot be read")
    return CardReader(GitHub(token), PLAN)


def _shipped_once_landed(root: Path, judged: tuple[trailers.Trailer, ...]) -> set[int]:
    """Every card git says shipped once the commits that have not landed land: over
    ``dev``'s history and their trailers, ``judged``."""
    landed = trailers.History(trailers.history(root, trailers.DEV).trailers + judged, ())
    return set(trailers.shipped(root, landed)[0])


def _wrong(number: int, key: str, card: BareCard | None, cards: Mapping[int, BareCard],
           shipped: set[int]) -> str | None:
    """What the tracker says is wrong with a ``key`` trailer naming ``plan#<number>``, whose
    card is ``card`` (None when the tracker does not hold it), as the end of a sentence;
    None when nothing is.  ``cards`` holds every card a decision over a ``Ships:``-named
    card reads (:func:`_command.with_closure`), and ``shipped`` git's answer once the change
    set lands."""
    if card is None:
        fix = ("" if key == trailers.REOPENS else
               ", or file the card with quill file first and name the number it is given")
        return (f"the tracker holds no plan#{number} (a mistyped number?): correct the "
                f"number{fix}")
    if key == trailers.REOPENS:
        return None
    if what := no_work(card, cards, shipped):
        return (f"plan#{number} is {what}, which no commit ships: name the step or finding "
                "the commit builds (a step split into smaller steps is built as them)")
    if filing_unfinished(card):
        return f"plan#{number} is still being filed: finish it with quill file, then land"
    if filing_ended(card):
        return (f"plan#{number} was dropped while still being filed: if it is this commit's "
                "work, reopen it and finish it with quill file, then land; otherwise name the "
                "step or finding the commit builds")
    return None


def refusals(named: Mapping[tuple[int, str], Sequence[trailers.Trailer]], shipped: set[int],
             connect: Callable[[], CardReader]) -> list[str]:
    """One sentence for each ``(card, key)`` in ``named`` the tracker refuses (the module's
    rules), each naming every commit that carries it, by card; ``named`` holds the card
    trailers of the change set's commits that have not landed, and ``shipped`` git's
    answer once they land.  The tracker is read through the reader ``connect`` makes.

    Raises:
        NoToken: when ``connect`` has no token to read with.
        _tracker.TrackerError: when a card a ``Ships:`` names links to one the tracker
            lacks, or GitHub's answer is cut short.
        _github.GitHubError: when GitHub answers a read with an error.
        requests.RequestException: when the tracker cannot be reached.
    """
    reader = connect()
    held = reader.cards({number for number, _ in named})
    cards = with_closure(reader, {number: held[number] for number, key in named
                                  if key == trailers.SHIPS and number in held})
    lines = []
    for (number, key), carried in sorted(named.items()):
        if wrong := _wrong(number, key, held.get(number), cards, shipped):
            where = ", ".join(f"{trailer.sha[:12]} ({trailer.subject})" for trailer in carried)
            verb = "carries" if len(carried) == 1 else "carry"
            lines.append(f"{where} {verb} `{key}: plan#{number}`, but {wrong}")
    return lines


def problems(root: Path, base: str, head: str,
             connect: Callable[[], CardReader]) -> tuple[int, int, list[str]]:
    """How many commits the change set ``<base>..<head>`` holds, how many of them have not
    landed, and one sentence for each card trailer on those the tracker refuses
    (:func:`refusals`); the tracker is not read when none of them names a card.

    Raises:
        tools.ci.gitcmd.GitError: when git cannot read the change set or ``dev``'s
            history (a shallow clone among them), or ``dev`` or ``main`` cannot be read at
            all (:class:`tools.ci.commit_trailers.LandedUnknown`).
        NoToken, _tracker.TrackerError, _github.GitHubError, requests.RequestException: as
            :func:`refusals` raises them.
    """
    count = int(git(root, "rev-list", "--count", f"{base}..{head}"))
    fresh = not_landed(root, base, head)
    judged = tuple(trailer for trailer in trailers.history(root, f"{base}..{head}").trailers
                   if trailer.sha in fresh)
    named: dict[tuple[int, str], list[trailers.Trailer]] = {}
    for trailer in judged:
        carried = named.setdefault((trailer.card, trailer.key), [])
        if trailer.sha not in {each.sha for each in carried}:
            carried.append(trailer)
    if not named:
        return count, len(fresh), []
    return count, len(fresh), refusals(named, _shipped_once_landed(root, judged), connect)


def main(argv: list[str] | None = None,
         connect: Callable[[], CardReader] = reader_from_environment, root: Path = REPO) -> int:
    """Check the change set ``<base>..<head>``; print each refusal; exit as the module says."""
    args = sys.argv[1:] if argv is None else argv
    if (pair := ends(args)) is None:
        print("usage: python -m tools.quill.landing <base-sha> <head-sha> (two full commit "
              f"ids, neither all zeros); given {args!r}", file=sys.stderr)
        return 2
    base, head = pair
    try:
        count, fresh, reasons = problems(root, base, head, connect)
    except GitError as error:
        return unjudged("landing check", base, head, error)
    except (NoToken, GitHubError, requests.RequestException) as error:
        print(f"landing check: the tracker could not be read, so nothing was checked: {error}",
              file=sys.stderr)
        return 2
    except TrackerError as error:
        print("landing check: what the tracker answered cannot be judged, so nothing was "
              f"checked: {error}", file=sys.stderr)
        return 2
    if fresh < count:
        print(f"landing check: {count - fresh} of the {count} commit(s) are on "
              f"{trailers.DEV} or {trailers.MAIN} already, and are not judged again")
    return report("landing check", base, head, count, reasons)


if __name__ == "__main__":
    sys.exit(main())
