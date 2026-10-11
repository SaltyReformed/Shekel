"""The ``migrate`` command: X-cx's migration of the plan into the tracker (piece L7).

The plan in ``docs/plans/`` moves into the tracker as cards (ruling ``balance:R-BAL170``;
the L7 design's draft 4).  ``check`` is the first of the command's phases and the only
one built yet (piece L7's leaf B1): it READS the registries and arc documents as this
checkout's HEAD commit holds them, from git, whatever the checkout's own files say
(rulings ``balance:R-BAL257``, ``R-BAL259``), so every card quotes the text of the commit
its As filed block names; and the input file the developer approved.  It WRITES NOTHING,
anywhere.  It prints what would move, arc by arc,
and what is filed already (:data:`_migrate_source.MAPPED`); the census of every
steps-section line
(:func:`_migrate_bodies.census`); how many cards hold a bare ``#N``, which GitHub would
link to an unrelated card (draft 4 s.3, decided after the rehearsal measures it); the
writes a run would make if every card were new; and every refusal, from the
registries (:mod:`_migrate_source`), the input file (:mod:`_migrate_input`) and the
cards' text (:mod:`_migrate_bodies`).

Usage, from the repository root::

    python -m tools.quill.migrate check --input PATH

The input file lives OUTSIDE the repository, and one inside it is refused: its text may
quote a real figure, which the tracker allows and the public repository does not
(``R-BAL172``, ``R-BAL132``; :mod:`_migrate_input`).

Exit status: 0 nothing refused; 1 something refused (each on a ``REFUSED:`` line);
2 nothing checked: the input file could not be read (or is not UTF-8), git could not
read HEAD or a planning file in it (or the file is not text), the input file is inside
the repository, or the command line is not the form above.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from tools.ci.arcs import ARCS, REPO
from tools.ci.gitcmd import GitError, git
from tools.quill._migrate_bodies import ON_A_CARD, Built, Inputs, bare_numbers, build, census
from tools.quill._migrate_input import InputError, grade, load
from tools.quill._migrate_source import (
    MAPPED,
    Item,
    Registries,
    Source,
    read_registries,
    read_source,
)

#: The kinds of card, in the order a report names them.
KINDS = ("step", "finding", "question", "ruling")


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


def check(registries: Registries, data: dict) -> tuple[list[str], int]:
    """The report ``check`` prints, and how many refusals it holds."""
    source = read_source(registries)
    decisions, problems = grade(data, source, registries)
    inputs = Inputs(source, registries, decisions)
    built, built_problems = build(inputs)
    places, census_problems = census(inputs, built)
    refusals = [*source.refusals, *problems, *built_problems, *census_problems]
    lines = [*_counts(source, registries), *_census_lines(places), _linked(source, built),
             _writes(source)]
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
    """The command line: ``check --input PATH``."""
    top = argparse.ArgumentParser(prog="migrate", description=__doc__.splitlines()[0])
    commands = top.add_subparsers(dest="command", required=True)
    checking = commands.add_parser("check", help="what would move, and what is refused")
    checking.add_argument("--input", required=True, type=Path,
                          help="the input file, outside the repository")
    return top


def main(argv: Sequence[str] | None = None,
         read: Callable[[Path, str], Registries] = read_registries, root: Path = REPO) -> int:
    """Run one command on the repository at ``root``, reading its registries at HEAD with
    ``read`` (:func:`_migrate_source.read_registries`); its exit status."""
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
    lines, refused = check(registries, data)
    print("\n".join(lines))
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
