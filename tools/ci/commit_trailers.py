"""Refuse a change set whose commits break the card-trailer rules (step X-cx's L4, card plan#4).

Git only, no tracker access.  Two rules, each read with :mod:`tools.ci.trailers`,
the one reader and the one ``shipped`` rule the tracker tool also asks:

1. **Citation shape.**  Every ``Ships:`` and ``Reopens:`` trailer on the change
   set's commits names a card as ``plan#<number>``, or, until step X-cx's
   cutover (L8), the old ``<arc>:<id>`` form the plan gate reads.  Anything
   else is a citation git would silently read as naming nothing.

2. **A revert carries what keeps git's answer true to the code it changes.**
   One revert commit undoes everything ANY of its revert records names (a
   squash of several reverts is one undo, judged once), and every commit it
   undoes is in the history it LANDS on, its first parent's.  Three answers
   are asked of :func:`tools.ci.trailers.shipped`, the tracker tool's own
   rule, and nothing else here decides what shipped: NOW, over that history;
   WITHOUT, over it with the undone commits' trailers taken out; and REMOVED,
   over the undone commits' trailers alone, which names each card whose
   shipped code the revert takes away.  The revert owes ``Reopens: plan#N``
   for each card REMOVED names, unless WITHOUT ships it through a
   ``Ships:`` git cancels NOW (code the revert brings back), and
   ``Ships: plan#N`` for each card shipped WITHOUT and not NOW; nothing else.
   So undoing a ship owes ``Reopens:``, even one of several ships of a card,
   since they may be its parts (``recurrence:R16-c-2`` shipped in three),
   and even when another ``Reopens:`` already cancelled it, so that
   undoing that other one later cannot bring the ship back (both decided by
   the session under ruling ``balance:R-BAL207``).  Undoing a revert's
   ``Reopens:`` owes ``Ships:``, since the code comes back.  Undoing a ship
   with the ``Reopens:`` that cancelled it, or a ``Reopens:`` that cancelled
   nothing (what one cancels is ruling ``balance:R-BAL181``'s), owes nothing.

   **Where it errs, and which way.**  Toward OPEN: one ``Reopens:`` cancels
   every ``Ships:`` of the card before it (R-BAL181), so undoing one of
   several ships reopens the card even when what remains is all of it, and
   so does undoing a later ship after a revert brought an earlier ship's code
   back (the card was shipped, so that revert owed nothing); the card stays
   open until a commit ships it again.  Toward SHIPPED, only where parallel
   lines of history meet in a merge: a ``Reopens:`` cancels no ``Ships:`` it
   was not built on, so a ``Ships:`` on one line outlives the undo of the
   card's code on another, and a revert that brings code back owes nothing,
   leaving the ``Ships:`` whose code it removes standing for such a merge to
   expose.  Each revert is judged where it lands, so no rule here sees the
   merge.  A trailer a revert does not owe is not refused: a ``Ships:`` it
   carries is a claim, as on any commit.

**What a revert is: git's own record, at the start of a line.**  ``git
revert`` writes ``This reverts commit <id>.``; with ``--reference`` the id is
shortened and followed by `` (<subject>, <date>)``; reverting a merge adds
``, reversing`` and a next line ``changes made to <parent>.``, naming the
parent it kept.  The GitHub "Revert" button writes the same.  The id must be
followed by what git writes after it (``.``, ``,``, `` (`` or the line's end),
so a sentence that merely starts with those words is not a record.  A change
undone by hand, with no such line, cannot be told from new work and is not
read.

**What a revert undoes.**  The commit it names, returning to its first parent;
for a MERGE, every commit the merge brought in beside the parent it kept
(``<parent>..<merge>``), returning to that parent, since the trailers sit on
those commits and not on the merge.  A revert naming a commit this repository
does not hold or that is not in the history the revert lands on (a rebased or
cherry-picked copy is what that history holds), a merge without the parent it
kept, or a parent that is not one of the merge's, is refused: what it undid
cannot be read, and a check that cannot read its subject has not checked it.
A commit with such a record is asked no debt either, since what it undoes is
not all known.

**The change set** is ``<base>..<head>``: the commits ``head`` holds and
``base`` does not.  ``ci.yml``'s ``scope`` job hands over the pair for a pull
request, a merge-queue group and a push to ``main``.  A change set holding no
commit is refused too: none of those events has one, so it means the two ends
are wrong.

Usage, at the repository root::

    python -m tools.ci.commit_trailers <base-sha> <head-sha>

Exit status: 0 nothing refused; 1 refused, each reason printed; 2 the command
line or git failed, or the change set holds no commit, so nothing was checked.
"""
from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path

from tools.ci import trailers
from tools.ci.arcs import REPO
from tools.ci.gitcmd import GitError, git, run

#: A full commit id, SHA-1 or SHA-256; a change set's two ends must be one.
_FULL_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

#: Git's revert record, at the start of a line; the id may be shortened, and is
#: followed by what git writes after it.
_REVERTS = re.compile(
    r"^This reverts commit (?P<named>[0-9a-f]{4,64})(?=[.,]| \(|[ \t]*$)", re.MULTILINE,
)

#: A merge revert's second half, read on the record's own line and the next.
_KEPT_PARENT = re.compile(r"reversing\s+changes made to (?P<parent>[0-9a-f]{4,64})\b")

_FIELD, _RECORD = "\x01", "\x03"


def reverts(message: str) -> list[tuple[str, str | None]]:
    """Each ``(commit named, parent kept or None)`` the message's revert records name, in order.

    A squashed commit can hold several records.  The kept parent is read only
    from the record's own line and the line after it, where git writes it.
    """
    found = []
    for record in _REVERTS.finditer(message):
        window = "\n".join(message[record.end():].split("\n", 2)[:2])
        kept = _KEPT_PARENT.search(window)
        found.append((record.group("named"), kept.group("parent") if kept else None))
    return found


def _commits(root: Path, base: str, head: str) -> Iterator[tuple[str, str, str]]:
    """``(sha, subject, message)`` for every commit of the change set, newest first."""
    out = git(root, "log", f"--format=%H{_FIELD}%s{_FIELD}%B{_RECORD}", f"{base}..{head}")
    for record in filter(str.strip, out.split(_RECORD)):
        sha, subject, message = record.lstrip("\n").split(_FIELD, 2)
        yield sha, subject, message


def _resolve(root: Path, named: str) -> str | None:
    """The full id of the commit ``named`` (a full or shortened id), or None when this
    repository holds no such commit or the shortened id is ambiguous."""
    done = run(root, "rev-parse", "--verify", "--quiet", f"{named}^{{commit}}")
    return done.stdout.strip() if done.returncode == 0 else None


def undone(root: Path, named: str, kept: str | None, onto: str) -> trailers.History | str:
    """The card trailers on what a revert of ``named`` undid, or one sentence saying why
    that cannot be read.

    Args:
        root: The repository.
        named: The commit the revert record names.
        kept: The parent a merge revert says it kept, or None.
        onto: The commit the revert lands on, its first parent.
    """
    full = _resolve(root, named)
    if full is None:
        return (f"it says it reverts commit {named}, which this repository does not hold "
                f"(if that commit was rebased, cite its new id)")
    if not trailers.is_ancestor(root, full, onto):
        return (f"it says it reverts commit {full[:12]}, which is not in the history it "
                f"lands on (if that commit was rebased or cherry-picked, cite the copy's id)")
    parents = git(root, "rev-list", "--parents", "-n", "1", full).split()[1:]
    if len(parents) < 2:
        return trailers.history(root, f"{full}^!")
    if kept is None:
        return (f"it reverts merge {full[:12]} without the line naming the parent it kept "
                f"(\"reversing changes made to <parent>\"), so what it undid cannot be read")
    kept_full = _resolve(root, kept)
    if kept_full not in parents:
        return (f"it says it kept {kept}, which is not a parent of merge {full[:12]}, so "
                f"what it undid cannot be read")
    return trailers.history(root, f"{kept_full}..{full}")


def owed_at_landing(root: Path, revert: str,
                    found: list[trailers.History]) -> dict[tuple[str, int], trailers.Trailer]:
    """``{(trailer owed, card): the undone trailer that owes it}`` for the commit ``revert``,
    whose revert records undo ``found`` (rule 2).

    NOW, WITHOUT and REMOVED are :func:`trailers.shipped`'s answers over the
    history ``revert`` lands on, over it without the undone commits' trailers,
    and over those trailers alone.
    """
    undone_trailers = tuple(trailer for each in found for trailer in each.trailers)
    gone = {trailer.sha for trailer in undone_trailers}
    landing = trailers.history(root, f"{revert}^1")
    now = trailers.shipped(root, landing)[0]
    without = trailers.shipped(root, trailers.History(
        tuple(trailer for trailer in landing.trailers if trailer.sha not in gone),
        landing.malformed))[0]
    removed = trailers.shipped(root, trailers.History(undone_trailers, ()))[0]
    debts: dict[tuple[str, int], trailers.Trailer] = {}
    for card, claims in removed.items():
        if not any(claim not in now.get(card, ()) for claim in without.get(card, ())):
            debts[(trailers.REOPENS, card)] = claims[0]
    for card in without.keys() - now.keys():
        # One always exists: a Ships standing WITHOUT and not NOW is cancelled NOW
        # only by Reopens trailers the revert undoes.
        debts[(trailers.SHIPS, card)] = next(
            undo for undo in undone_trailers if undo.key == trailers.REOPENS
            and any(trailers.cancels(root, undo, claim) for claim in without[card]))
    return debts


def _revert_reasons(root: Path, commit: tuple[str, str, str],
                    carries: set[tuple[str, int]]) -> list[str]:
    """Why one commit of the change set breaks rule 2, one sentence per debt; empty when
    it reverts nothing or pays every debt.  ``commit`` is ``(sha, subject, message)``.

    Every record the commit holds is read before any debt is asked, since a
    debt is owed by the whole undo: a record that cannot be read is refused,
    and then no debt is asked.
    """
    sha, subject, message = commit
    found = [undone(root, named, kept, f"{sha}^1") for named, kept in reverts(message)]
    unread = [f"{sha[:12]} ({subject}): {what}" for what in found if isinstance(what, str)]
    if unread or not found:
        return unread
    read = [what for what in found if isinstance(what, trailers.History)]
    return [
        f"{sha[:12]} ({subject}) undoes {cause.sha[:12]} ({cause.subject}), which carries "
        f"`{cause.key}: plan#{card}`; add the trailer `{key}: plan#{card}` to the last "
        f"paragraph of {sha[:12]}'s message"
        for (key, card), cause in sorted(owed_at_landing(root, sha, read).items())
        if (key, card) not in carries
    ]


def problems(root: Path, base: str, head: str) -> tuple[int, list[str]]:
    """How many commits the change set holds, and one sentence per broken rule.

    Raises:
        GitError: when git cannot read the change set (a shallow clone among them).
    """
    found = trailers.history(root, f"{base}..{head}")
    reasons = [f"{line} (nor the old <arc>:<id> form, read until the cutover)"
               for line in found.malformed]
    carried: dict[str, set[tuple[str, int]]] = {}
    for trailer in found.trailers:
        carried.setdefault(trailer.sha, set()).add((trailer.key, trailer.card))
    commits = list(_commits(root, base, head))
    for commit in commits:
        reasons.extend(_revert_reasons(root, commit, carried.get(commit[0], set())))
    return len(commits), reasons


def main(argv: list[str] | None = None, root: Path = REPO) -> int:
    """Check the change set ``<base>..<head>``; print each refusal; exit as the module says."""
    args = sys.argv[1:] if argv is None else argv
    if (len(args) != 2 or not all(_FULL_ID.fullmatch(a) for a in args)
            or any(set(a) == {"0"} for a in args)):
        print("usage: python -m tools.ci.commit_trailers <base-sha> <head-sha> (two full "
              f"commit ids, neither all zeros); given {args!r}", file=sys.stderr)
        return 2
    base, head = args
    try:
        count, reasons = problems(root, base, head)
    except GitError as exc:
        print(f"commit trailers: git could not read {base[:12]}..{head[:12]}: {exc}",
              file=sys.stderr)
        return 2
    if not count:
        print(f"commit trailers: {base[:12]}..{head[:12]} holds no commit, so nothing was "
              "checked; no pull request, merge group or push has an empty change set",
              file=sys.stderr)
        return 2
    for reason in reasons:
        print(f"REFUSED -- {reason}")
    print(f"commit trailers: {count} commit(s) in {base[:12]}..{head[:12]}, "
          f"{len(reasons)} refused")
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())
