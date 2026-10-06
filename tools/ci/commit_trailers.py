"""Refuse a change set whose commits break the card-trailer rules (step X-cx's L4, card plan#4).

Git only, no tracker access.  Two rules, each read with :mod:`tools.ci.trailers`,
the one reader the tracker tool also asks what shipped:

1. **Citation shape.**  Every ``Ships:`` and ``Reopens:`` trailer on the change
   set's commits names a card as ``plan#<number>``, or, until step X-cx's
   cutover (L8), the old ``<arc>:<id>`` form the plan gate reads.  Anything
   else is a citation git would silently read as naming nothing.

2. **A revert carries the opposite of every card trailer on what it undoes.**
   Undoing a commit that carries ``Ships: plan#N`` must carry ``Reopens:
   plan#N``, or git keeps calling the card shipped after its code is gone (the
   card's rule).  Undoing a commit that carries ``Reopens: plan#N`` must carry
   ``Ships: plan#N``: reverting a revert brings the code back, and without the
   trailer git keeps calling the card unshipped (decided by the session under
   ruling ``balance:R-BAL207``; ruling R-BAL181 already says a card shipped
   again after a reopen stands).

**What a revert is: git's own record, at the start of a line.**  ``git
revert`` writes ``This reverts commit <id>.``; with ``--reference`` the id is
shortened and followed by ``(<subject>, <date>)``; reverting a merge adds
``, reversing`` and a next line ``changes made to <parent>.``, naming the
parent it kept.  The GitHub "Revert" button writes the same.  A change undone
by hand, with no such line, cannot be told from new work and is not read.

**What a revert undoes.**  The commit it names; for a MERGE, every commit the
merge brought in beside the parent it kept (``<parent>..<merge>``), since the
trailers sit on those commits and not on the merge.  A revert naming a commit
this repository does not hold, a merge without the parent it kept, or a parent
that is not one of the merge's, is refused: what it undid cannot be read, and
a check that cannot read its subject has not checked it.

**The change set** is ``<base>..<head>``: the commits ``head`` holds and
``base`` does not.  ``ci.yml``'s ``scope`` job hands over the pair for a pull
request, a merge-queue group and a push to ``main``.

Usage, at the repository root::

    python -m tools.ci.commit_trailers <base-sha> <head-sha>

Exit status: 0 nothing refused; 1 refused, each reason printed; 2 the command
line or git failed, so nothing was checked.
"""
from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path

from tools.ci import trailers
from tools.ci.arcs import REPO
from tools.ci.gitcmd import GitError, git, run

#: Each card trailer's opposite: what a revert of a commit carrying it must carry.
_OPPOSITE = {trailers.SHIPS: trailers.REOPENS, trailers.REOPENS: trailers.SHIPS}

#: A full commit id, SHA-1 or SHA-256; a change set's two ends must be one.
_FULL_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

#: Git's revert record, at the start of a line; the id may be shortened.
_REVERTS = re.compile(r"^This reverts commit (?P<named>[0-9a-f]{4,64})\b", re.MULTILINE)

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


def undone(root: Path, named: str, kept: str | None) -> trailers.History | str:
    """The card trailers on what a revert of ``named`` undid, or why that cannot be read.

    Args:
        root: The repository.
        named: The commit the revert record names.
        kept: The parent a merge revert says it kept, or None.

    Returns:
        The :class:`tools.ci.trailers.History` of the undone commits, or one
        sentence saying why they cannot be read.
    """
    full = _resolve(root, named)
    if full is None:
        return f"it says it reverts commit {named}, which this repository does not hold"
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


def _debts(sha: str, subject: str, what: trailers.History,
           carries: set[tuple[str, int]]) -> list[str]:
    """One sentence per card trailer on the undone commits whose opposite the revert lacks."""
    owed: dict[tuple[str, int], trailers.Trailer] = {}
    for trailer in what.trailers:
        owed.setdefault((_OPPOSITE[trailer.key], trailer.card), trailer)
    return [
        f"{sha[:12]} ({subject}) undoes {cause.sha[:12]} ({cause.subject}), which carries "
        f"`{cause.key}: plan#{card}`; add the trailer `{key}: plan#{card}` to "
        f"{sha[:12]}'s message"
        for (key, card), cause in sorted(owed.items()) if (key, card) not in carries
    ]


def _revert_reasons(root: Path, commit: tuple[str, str, str],
                    carries: set[tuple[str, int]]) -> list[str]:
    """Why one commit of the change set breaks rule 2, one sentence per debt; empty when
    it reverts nothing or pays every debt.  ``commit`` is ``(sha, subject, message)``."""
    sha, subject, message = commit
    reasons: list[str] = []
    for named, kept in reverts(message):
        what = undone(root, named, kept)
        if isinstance(what, str):
            reasons.append(f"{sha[:12]} ({subject}): {what}")
        else:
            reasons.extend(_debts(sha, subject, what, carries))
    return reasons


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
    for reason in reasons:
        print(f"REFUSED -- {reason}")
    print(f"commit trailers: {count} commit(s) in {base[:12]}..{head[:12]}, "
          f"{len(reasons)} refused")
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())
