"""Two records compared test by test (:func:`differences`): what each test did in one record
and not in the other, by kind.  The second record's places are read in the first's through
a translation (a moved file's line to the source line it was copied from), and its node ids
through a map; two runs of one layout compare under the identity of both.

Nothing here starts a process, so every kind can be shown on a hand-built record.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tools.testrecord.record import Record, Trace

#: A place of the second record read as the first's: its file and line to the file and line
#: it stands for, or None for a line that stands for none (a line a move composed).  The
#: lines of one file stand for lines of one file: an arc joins two lines of a file, and
#: :func:`differences` refuses a translation that parts one.
Translate = Callable[[str, int], tuple[str, int] | None]

#: The kinds of difference, in the order they are reported for one test: a test only one
#: record holds; another outcome; a line one phase executed in one record only; an arc
#: (a transition between two lines both records executed) in one record only; another
#: sequence of the passing asserts pytest rewrote; and a line of the second record that
#: stands for none.
KINDS = ("missing", "outcome", "lines", "arcs", "asserts", "untranslated")


@dataclass(frozen=True)
class Difference:
    """One difference for one test: its node id in the first record (in the second when
    only the second holds it), the kind, the phase (None for a whole-test kind), and what
    the first record holds that the second does not, and the reverse."""

    test: str
    kind: str
    phase: str | None
    first: tuple
    second: tuple


def same_place(path: str, line: int) -> tuple[str, int]:
    """The identity translation: two runs of one layout."""
    return path, line


def _arc_places(trace: Trace, phase: str, translate: Translate
                ) -> tuple[set[tuple[str, int, int]], set[tuple[str, int]]]:
    """``trace``'s arcs in ``phase``, each endpoint read through ``translate`` (an entry or
    exit, a negative line, by its line), and every line no translation stands for.
    Refused when ``translate`` reads an arc's two lines into two files."""
    arcs, untranslated = set(), set()
    for path, held in trace.arcs.get(phase, {}).items():
        for arc in held:
            ends = [translate(path, abs(line)) for line in arc]
            if None in ends:
                untranslated |= {(path, abs(line)) for line, end in zip(arc, ends)
                                 if end is None}
                continue
            if ends[0][0] != ends[1][0]:
                raise ValueError(f"{path}'s arc {arc} translates into two files, "
                                 f"{ends[0][0]} and {ends[1][0]}")
            arcs.add((ends[0][0], *(end[1] if line > 0 else -end[1]
                                    for line, end in zip(arc, ends))))
    return arcs, untranslated


def _lines(arcs: set[tuple[str, int, int]]) -> set[tuple[str, int]]:
    """The lines the arcs executed: their positive endpoints."""
    return {(path, line) for path, *ends in arcs for line in ends if line > 0}


def _asserts(trace: Trace, phase: str) -> tuple[tuple[str, str], ...]:
    """``trace``'s passing asserts in ``phase``, in order, as their text and explanation:
    the line is the place, which the arcs already compare through the translation."""
    return tuple((orig, expl) for _, orig, expl in trace.asserts.get(phase, ()))


def _phase_differences(test: str, phase: str, first: Trace, second: Trace,
                       translate: Translate) -> list[Difference]:
    """Every difference of one phase of one test."""
    found = []
    first_arcs, _ = _arc_places(first, phase, same_place)
    second_arcs, untranslated = _arc_places(second, phase, translate)
    first_lines, second_lines = _lines(first_arcs), _lines(second_arcs)
    if first_lines != second_lines:
        found.append(Difference(test, "lines", phase, tuple(sorted(first_lines - second_lines)),
                                tuple(sorted(second_lines - first_lines))))
    differing = first_lines ^ second_lines
    only = [{arc for arc in arcs - other if not _lines({arc}) & differing}
            for arcs, other in ((first_arcs, second_arcs), (second_arcs, first_arcs))]
    if any(only):
        found.append(Difference(test, "arcs", phase, tuple(sorted(only[0])),
                                tuple(sorted(only[1]))))
    first_asserts, second_asserts = _asserts(first, phase), _asserts(second, phase)
    if first_asserts != second_asserts:
        found.append(Difference(test, "asserts", phase, first_asserts, second_asserts))
    if untranslated:
        found.append(Difference(test, "untranslated", phase, (), tuple(sorted(untranslated))))
    return found


def differences(first: Record, second: Record, ids: dict[str, str] | None = None,
                translate: Translate = same_place) -> list[Difference]:
    """Every difference between ``first`` and ``second``, test by test in node-id order and
    by :data:`KINDS` within a test.  ``ids`` maps each of the second's node ids to the
    first's (the identity when None); ``translate`` reads the second's places as the
    first's.  Refused when two of the second's tests read as one of the first's: one would
    be compared and the other dropped unreported."""
    named: dict[str, Trace] = {}
    for nodeid, trace in second.tests.items():
        name = (ids or {}).get(nodeid, nodeid)
        if name in named:
            raise ValueError(f"two of the second record's tests read as {name!r}, "
                             f"{nodeid!r} among them: each must read as its own")
        named[name] = trace
    found = []
    for test in sorted(set(first.tests) | set(named)):
        if test not in named or test not in first.tests:
            found.append(Difference(test, "missing", None, (test,) * (test in first.tests),
                                    (test,) * (test in named)))
            continue
        one, other = first.tests[test], named[test]
        if one.outcome != other.outcome:
            found.append(Difference(test, "outcome", None, (one.outcome,), (other.outcome,)))
        phases = sorted(set(one.arcs) | set(other.arcs) | set(one.asserts) | set(other.asserts))
        for phase in phases:
            found += _phase_differences(test, phase, one, other, translate)
    return found
