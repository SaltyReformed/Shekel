"""The ``migrate`` command: X-cx's migration of the plan into the tracker (piece L7).

The plan in ``docs/plans/`` moves into the tracker as cards (ruling ``balance:R-BAL170``;
the L7 design's draft 4).  Every phase READS the registries and arc documents as this
checkout's HEAD commit holds them, from git, whatever the checkout's own files say
(rulings ``balance:R-BAL257``, ``R-BAL259``), so every card quotes the text of the commit
its As filed block names; and the input file the developer approved.

``check`` WRITES NOTHING, anywhere.  It prints what would move, arc by arc,
and what is filed already (:data:`_migrate_source.MAPPED`); the census of every
steps-section line
(:func:`_migrate_bodies.census`); how many cards hold a bare ``#N``, which GitHub would
link to an unrelated card (draft 4 s.3, decided after the rehearsal measures it); the
writes a run would make if every card were new; and every refusal, from the
registries (:mod:`_migrate_source`), the input file (:mod:`_migrate_input`) and the
cards' text (:mod:`_migrate_bodies`).

``history`` and ``open`` WRITE, on the tracker ``--target`` names (``plan`` or
``rehearsal``, with no default, draft 4 s.6), as quill's App: ``history`` files every
ruling, and ``open``, refused until it has, every other card, then orders the board
(:mod:`_migrate_run`).  Each runs only when nothing above refuses -- the registries, the
input file, the cards' text, its census -- and then only on a tracker the plan refuses
nothing on: a plan that refuses anything writes nothing.  A run cut short is resumed by
running the same command again.

Usage, from the repository root::

    python -m tools.quill.migrate check --input PATH
    python -m tools.quill.migrate history --input PATH --target plan|rehearsal
    python -m tools.quill.migrate open --input PATH --target plan|rehearsal

The input file lives OUTSIDE the repository, and one inside it is refused: its text may
quote a real figure, which the tracker allows and the public repository does not
(``R-BAL172``, ``R-BAL132``; :mod:`_migrate_input`).

Exit status: 0 nothing refused (and for ``history`` and ``open``, every write made); 1
something refused (each on a ``REFUSED:`` line), and nothing written -- or an error none
of these names (an App key file that cannot be parsed, say), with Python's traceback in
place of any ``REFUSED:`` line, which may follow writes; 2 nothing checked:
the input file could not be read (or is not UTF-8), git could not read HEAD or a planning
file in it (or the file is not text), the input file is inside the repository, or the
command line is not the form above; 3 a ``history`` or ``open`` run STOPPED, its cause
on a ``stopped:`` line: the App's key file could not be read; GitHub refused or did not
answer (the connect included); the connect found the target no board, or one not linked
to its repository (:func:`_tracker.board_of`); a write's answer read back otherwise than
it was sent (a create's type, labels or milestone, or a ruling's close: "fix it by
hand"); the tracker's read was refused (its numbering probe, two milestones of one title,
or a read GitHub cut short: a card holding more labels, sub-issues, blockers or board items
than one read returns); a card the run writes on was under another parent, or gone; or,
after the writes, the tracker read again had writes left or refused anything, or the board
did not show a card, a move or its desired order, or could not be ordered (it held a card
the desired order does not).  What it wrote stands; settle what the line names, then run
it again.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import requests

from tools.ci.arcs import ARCS, REPO
from tools.ci.gitcmd import GitError, git
from tools.quill._github import GitHubError
from tools.quill._migrate_bodies import ON_A_CARD, Built, Inputs, bare_numbers, build, census
from tools.quill._migrate_input import Decisions, InputError, grade, load
from tools.quill._migrate_run import Clock, Filing, Run, Stopped
from tools.quill._migrate_source import (
    MAPPED,
    Item,
    Registries,
    Source,
    read_registries,
    read_source,
)
from tools.quill._tracker import Tracker, TrackerError
from tools.quill.setup_tracker import PLAN, REHEARSAL, Place

#: The kinds of card, in the order a report names them.
KINDS = ("step", "finding", "question", "ruling")
#: The trackers a run may write, by ``--target`` (draft 4 s.6; no default).
TARGETS = {"plan": PLAN, "rehearsal": REHEARSAL}


def writes(item: Item) -> int:
    """The writes a run makes to file ``item`` as a new card: the create (born marked,
    labels and milestone in it), its parent link, a link per open blocker, a question's
    As filed comment, its board place, and the last write -- the mark's removal, which
    for a ruling is one PATCH with its close (draft 4 s.11 V3-8).  The board's moves to
    reach its desired order are counted apart, once the tracker is read.  A card already
    filed (:data:`_migrate_source.MAPPED`) takes its links alone."""
    links = (item.links.parent is not None) + len(item.links.blockers)
    if item.key in MAPPED:
        return links
    return 1 + links + (item.kind == "question") + (item.links.place is not None) + 1


@dataclass(frozen=True)
class Prepared:
    """What every phase reads before any tracker: the registries' items, the input file's
    decisions, each card's text, the census, and every refusal of the four."""

    source: Source
    decisions: Decisions
    built: Mapping[str, Built]
    places: Mapping[str, Counter]
    refusals: tuple[str, ...]


def prepare(registries: Registries, data: dict) -> Prepared:
    """The registries read (:func:`_migrate_source.read_source`), the input file graded
    (:func:`_migrate_input.grade`), the cards' text built and its census taken
    (:mod:`_migrate_bodies`), with the refusals of all four: what ``check`` reports and
    a writing phase gates on."""
    source = read_source(registries)
    decisions, problems = grade(data, source, registries)
    inputs = Inputs(source, registries, decisions)
    built, built_problems = build(inputs)
    places, census_problems = census(inputs, built)
    return Prepared(source, decisions, built, places,
                    (*source.refusals, *problems, *built_problems, *census_problems))


def check(registries: Registries, data: dict) -> tuple[list[str], int]:
    """The report ``check`` prints, and how many refusals it holds."""
    prepared = prepare(registries, data)
    source, built, refusals = prepared.source, prepared.built, prepared.refusals
    lines = [*_counts(source, registries), *_census_lines(prepared.places),
             _linked(source, built), _writes(source)]
    lines += [f"REPORT: {line}" for line in source.reports]
    lines += [f"REFUSED: {line}" for line in refusals]
    lines.append(f"{len(refusals)} refusal(s)")
    return lines, len(refusals)


def _counts(source: Source, registries: Registries) -> list[str]:
    """The cards that move, by arc and kind."""
    kinds = Counter((item.arc, item.kind) for item in source.items)
    filed = ", ".join(f"{key} is plan#{number}" for key, number in sorted(MAPPED.items()))
    return [f"check at {registries.commit}: {len(source.items)} cards, "
            f"{len(source.items) - len(MAPPED)} to file" + (f" ({filed} already)" if filed
                                                            else ""),
            *(f"  {arc}: " + ", ".join(f"{kind}s {kinds[arc, kind]}" for kind in KINDS)
              for arc in ARCS)]


def _census_lines(places: Mapping[str, Counter]) -> list[str]:
    """The census, one line an arc: its non-blank lines, those on a card first."""
    lines = []
    for arc, counted in places.items():
        rest = sorted((place, count) for place, count in counted.items() if place != ON_A_CARD)
        lines.append(f"census {arc}: {sum(counted.values())} lines -- {ON_A_CARD} "
                     f"{counted[ON_A_CARD]}" + "".join(f"; {place} {count}"
                                                       for place, count in rest))
    return lines


def _linked(source: Source, built: Mapping[str, Built]) -> str:
    """How many cards of each kind hold a bare ``#N`` (:func:`_migrate_bodies.bare_numbers`)."""
    linked = Counter(item.kind for item in source.items
                     if item.key in built and bare_numbers(built[item.key]))
    return ("cards holding a bare #N, which GitHub links to card N: "
            + (", ".join(f"{kind}s {linked[kind]}" for kind in KINDS if linked[kind])
               or "none"))


def _writes(source: Source) -> str:
    """The writes a run would make if every card were new (:func:`writes`)."""
    history = sum(writes(item) for item in source.items if item.kind == "ruling")
    rest = sum(writes(item) for item in source.items if item.kind != "ruling")
    milestones = len({item.outcome for item in source.items if item.outcome is not None})
    return (f"writes if every card is new: {history} filing the rulings, then "
            f"{rest + milestones} filing the rest and the {milestones} milestones, before the "
            "board's moves")


def parser() -> argparse.ArgumentParser:
    """The command line: ``check --input PATH``, and ``history`` or ``open`` with
    ``--target`` besides."""
    top = argparse.ArgumentParser(prog="migrate", description=__doc__.splitlines()[0])
    commands = top.add_subparsers(dest="command", required=True)
    for command, said in (("check", "what would move, and what is refused"),
                          ("history", "file every ruling (first)"),
                          ("open", "file every other card, then order the board")):
        each = commands.add_parser(command, help=said)
        each.add_argument("--input", required=True, type=Path,
                          help="the input file, outside the repository")
        if command != "check":
            each.add_argument("--target", required=True, choices=sorted(TARGETS),
                              help="the tracker written: the real one or the rehearsal's")
    return top


def run(command: str, prepared: Prepared, connect: Callable[[], Tracker],
        clock: Clock) -> int:
    """Run the writing phase ``command`` (``history`` or ``open``) on the tracker
    ``connect`` reaches, once nothing ``prepared`` holds refuses; its exit status."""
    if prepared.refusals:
        return _refused(prepared.refusals)
    filing = Filing(prepared.source.items, prepared.built, prepared.decisions.milestones)
    try:
        writer = Run(connect, filing, clock)
        refusals = writer.history() if command == "history" else writer.open()
    except (Stopped, GitHubError, TrackerError, requests.RequestException,
            OSError) as error:
        print(f"stopped: {error}", file=sys.stderr)
        return 3
    if refusals:
        return _refused(refusals)
    print(f"{command}: done, {writer.made} write(s) made")
    return 0


def _refused(refusals: Sequence[str]) -> int:
    """Print ``refusals``, each on a ``REFUSED:`` line, and that nothing was written; exit
    status 1."""
    print("\n".join([*(f"REFUSED: {line}" for line in refusals),
                     f"{len(refusals)} refusal(s); nothing written"]))
    return 1


def main(argv: Sequence[str] | None = None,
         read: Callable[[Path, str], Registries] = read_registries, root: Path = REPO,
         connect: Callable[[Place], Tracker] = Tracker.connect, clock: Clock = Clock()) -> int:
    """Run one command on the repository at ``root``, reading its registries at HEAD with
    ``read`` (:func:`_migrate_source.read_registries`), and writing (``history``, ``open``)
    on the tracker ``connect`` reaches for the ``--target`` place, timed by ``clock``; its
    exit status."""
    args = parser().parse_args(argv)
    try:
        if args.input.resolve().is_relative_to(root.resolve()):
            raise InputError(f"{args.input} is inside the repository; the input file may "
                             "quote a real figure, so it lives outside it (R-BAL132)")
        data = load(args.input)
        commit = git(root, "rev-parse", "HEAD").strip()
        registries = read(root, commit)
    except (InputError, OSError, UnicodeDecodeError, GitError) as error:
        print(f"failed: {error}", file=sys.stderr)
        return 2
    if args.command != "check":
        place = TARGETS[args.target]
        return run(args.command, prepare(registries, data), lambda: connect(place), clock)
    lines, refused = check(registries, data)
    print("\n".join(lines))
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
