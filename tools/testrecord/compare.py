"""Two records compared test by test (:func:`differences`): what each test did in one record
and not in the other, by kind.  The second record's places are read in the first's through
a translation (a moved file's line to the source line it was copied from), and its node ids
through a map; two runs of one layout compare under the identity of both.

Before two records are compared, each one's assert explanations and exception messages are
normalized by kind (:func:`normalized`): a value that differs from run to run, or names the
tree or the module the record was measured in, is rewritten to one spelling, and Flask's
session cookie is read as the session it signs.  Each kind is anchored on exactly the
context it was measured in (the coordinator's rulings on the R-BAL303 pilot's Fork 3 and on
PR A's item (3), 2026-10-16).  A value a kind does not match is compared as it was recorded,
so a shape no kind was measured on is a difference, never a match.

Nothing here starts a process, so every kind can be shown on a hand-built record.
"""
from __future__ import annotations

import ast
import base64
import binascii
import json
import re
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from functools import partial

from tools.testrecord.record import Record, Trace

#: A place of the second record read as the first's: its file and line to the file and line
#: it stands for, or None for a line that stands for none (a line a move composed).  The
#: lines of one file stand for lines of one file: an arc joins two lines of a file, and
#: :func:`differences` refuses a translation that parts one.
Translate = Callable[[str, int], tuple[str, int] | None]

#: The kinds of difference, in the order they are reported for one test: a test only one
#: record holds; another outcome; a line one phase executed in one record only; an arc
#: (a transition between two lines both records executed) in one record only; another
#: sequence of the passing asserts pytest rewrote; another exception ending a phase (its
#: type's qualified name and its message; never its traceback, its place, its module or a
#: chained cause, which the record does not hold), or one ending it in one record only; and
#: a line of the second record that stands for none.
KINDS = ("missing", "outcome", "lines", "arcs", "asserts", "raised", "untranslated")


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


def _raised(trace: Trace, phase: str) -> tuple[str, ...]:
    """The exception that ended ``trace``'s ``phase``, as its type's qualified name and its
    message; empty when the phase raised none."""
    return trace.raised.get(phase, ())


#: What one phase holds that two records must hold alike as it is, by kind, in
#: :data:`KINDS` order: its passing asserts and the exception that ended it.
_HELD = (("asserts", _asserts), ("raised", _raised))


def _arc_differences(test: str, phase: str, first: Trace, second: Trace, translate: Translate
                     ) -> tuple[list[Difference], set[tuple[str, int]]]:
    """One phase's lines and arcs differences, and every line of the second record's phase
    that no translation stands for."""
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
    return found, untranslated


def _phase_differences(test: str, phase: str, first: Trace, second: Trace,
                       translate: Translate) -> list[Difference]:
    """Every difference of one phase of one test, in :data:`KINDS` order."""
    found, untranslated = _arc_differences(test, phase, first, second, translate)
    for kind, held in _HELD:
        one, other = held(first, phase), held(second, phase)
        if one != other:
            found.append(Difference(test, kind, phase, one, other))
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
        phases = sorted({phase for held in (one, other)
                         for phase in (*held.arcs, *held.asserts, *held.raised)})
        for phase in phases:
            found += _phase_differences(test, phase, one, other, translate)
    return found


#: A UUID as ``str(uuid.UUID(...))`` writes it.
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

#: A session cookie's value as Flask signs it (itsdangerous): ``.`` when its payload is
#: zlib-compressed, then the payload, its timestamp and its signature, each base64url.
_SIGNED = re.compile(r"(\.?)([A-Za-z0-9_-]+)\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")

#: The values a set-order element may be, alone or as the items of a tuple: the hashable
#: scalars a set's repr prints in a form that does not depend on the process.
_SCALARS = (str, bytes, int, float, bool, type(None))


def _session(match: re.Match[str]) -> str:
    """A ``Set-Cookie`` header's session value (``match``'s second group, after the header
    its first) read as the session it signs, in compact JSON with sorted keys: its timestamp
    and signature dropped, and a top-level ``csrf_token`` (Flask-WTF's random token) as
    ``<csrf-token>``; no other field is rewritten.  The value as it is unless it decodes, as
    Flask encodes it, to a JSON object (the coordinator's ruling on item (3)'s review H1,
    2026-10-16).  The payload holds what the app flashed, which no other explanation of the
    test may hold: ``Recurring monthly transfer of $1,611.77 created from Checking to My
    Mortgage.``, the same in every run of both layouts while the timestamp and signature
    differ (test_loan, PR A's item (2) re-records)."""
    signed = _SIGNED.fullmatch(match.group(2))
    if signed is None:
        return match.group(0)
    payload = signed.group(2)
    try:
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        session = json.loads((zlib.decompress(raw) if signed.group(1) else raw).decode("utf-8"))
    except (binascii.Error, zlib.error, UnicodeDecodeError, json.JSONDecodeError,
            RecursionError):
        return match.group(0)
    if not isinstance(session, dict):
        return match.group(0)
    if "csrf_token" in session:
        session["csrf_token"] = "<csrf-token>"
    return (f"{match.group(1)}<session "
            f"{json.dumps(session, sort_keys=True, separators=(',', ':'))}>")


def _scalar(value: object) -> bool:
    """Whether ``value`` is one of :data:`_SCALARS`, or a tuple of them (one level)."""
    return isinstance(value, _SCALARS) or (
        isinstance(value, tuple) and all(isinstance(item, _SCALARS) for item in value))


def _sorted_set(side: str) -> str | None:
    """``side`` re-rendered with its elements sorted by their repr, when it is byte for byte
    the repr of a set of :func:`_scalar` values: it parses as a set display whose values'
    reprs, joined as a set's repr joins them, are the side, and no two values are equal.
    Else None.  ``ast.literal_eval`` alone would read text no set printed as one,
    canonicalized: ``{1, True}`` as ``{1}``, ``{1.00}`` as ``{1.0}`` (measured on CPython
    3.14.8, 2026-10-16), so two different texts would compare as one.  The parser refuses
    text by raising :class:`SyntaxError`; :class:`MemoryError` when deep nesting overflows its
    stack; :class:`RecursionError` when tens of thousands of chained operators overflow the
    compiler's (40,000 measured); and :class:`ValueError` (a ``UnicodeEncodeError``) on a lone
    surrogate.  The repr of an integer past the digit limit (a hex literal parses) raises
    :class:`ValueError`.

    A repr pytest cut short can be a set's repr too: of the 228 cuts its ``_ellipsize`` makes
    (sizes 5 to 232) of the savings instance's right set as the source layout printed it,
    161 are (pytest 9.1.1, 2026-10-16), and no text tells a cut from a string holding
    ``...``.  No cut side reaches here from a recorded run: at assertion verbosity 2, which
    :data:`tools.testrecord.record.SETTINGS` sets, pytest prints both sides of a comparison
    in full."""
    try:
        node = ast.parse(side, mode="eval").body
    except (SyntaxError, MemoryError, RecursionError, ValueError):
        return None
    if not isinstance(node, ast.Set):
        return None
    try:
        values = [ast.literal_eval(element) for element in node.elts]
        if not all(_scalar(value) for value in values):
            return None
        texts = [repr(value) for value in values]
    except (ValueError, TypeError):
        return None
    if side != "{" + ", ".join(texts) + "}" or len(set(values)) != len(values):
        return None
    return "{" + ", ".join(sorted(texts)) + "}"


def _set_order(explanation: str) -> str:
    """``explanation`` with each side of its first line's `` == `` re-rendered by
    :func:`_sorted_set`, when every side is a set's repr; else as it is.  A set literal's
    iteration order depends on what its process compiled before it, under one hash seed
    (measured 2026-10-16, CPython 3.14.8), so the two layouts of a split print one literal's
    elements in two orders: test_savings_dashboard_service's
    ``TestComputeDashboardData::test_returns_expected_keys``.  pytest's diff below the first
    line sorts a set's elements where it can order them.  A line holding no `` == `` is no
    comparison of sets and is compared as it is: a set printed alone (a passing ``assert s``,
    or a failing assert's own message) is outside the kind's measured context (the branch's
    neutral review of M-2, n1 and L-1, M-A).  Every `` == `` splits the line, so each set of
    a chain is re-rendered; one inside a string element leaves a side holding half a string,
    which is no set's repr, so that line is compared as it is (the coordinator's ruling on
    delta review 4's L-1, 2026-10-16, which deleted a guard that compared every line holding
    two `` == `` as it is)."""
    first, newline, rest = explanation.partition("\n")
    if " == " not in first:
        return explanation
    sides = [_sorted_set(side) for side in first.split(" == ")]
    if None in sides:
        return explanation
    return " == ".join(sides) + newline + rest


def _kinds(root: str, relocated: Mapping[str, str]) -> dict[str, Callable[[str], str]]:
    """Each normalization kind's rewrite of one explanation, by name, in the order they
    apply, for a record measured under ``root`` of a move that wrote each module of
    ``relocated`` from the module it maps to (dotted names).

    Each kind but ``run-socket`` matches exactly the context its every measured instance
    had, in the 60,312 explanations of 48 recorded runs of the four merged splits (the
    R-BAL303 pilot's 32 and PR A's item (2)'s 16, 2026-10-16; the number of explanations each
    kind changed follows its name), and widening one needs a measured instance and the
    coordinator's ruling, as a new kind does:

    - ``tree-root`` (168): the root, a directory, so followed by ``/``: a module's ``from
      '...'`` and a ``PosixPath``.  First, as the coordinator ruled; the pilot's unanchored
      UUID pattern matched inside a scratch root, which ``uuid`` below cannot.
    - ``address`` (4,380): a default repr's memory address, which its ``>`` closes.
    - ``csrf-token`` (5,100): Flask-WTF's token as the hidden field's ``value`` and the meta
      tag's ``content``, each directly after the name.
    - ``session-cookie`` (132): Flask's session in a ``Set-Cookie`` header's repr, up to the
      ``;`` that ends it, read as the session it signs (:func:`_session`).
    - ``run-socket`` (852): the suite wrapper's per-run socket directory, by its name alone,
      as the pilot wrote it (ruled); every instance was in a database URL's host.
    - ``uuid`` (132): a response's ``X-Request-Id`` header in its repr.
    - ``relocated-repr`` (420): a moved module's name in the default repr of an object of a
      class it defines, read as the module it moved from.  Never a class's repr (none
      measured), and never a module's name as a value.
    - ``set-order`` (48 in item (2)'s 16 runs): last, so it sorts the text the other kinds
      wrote (:func:`_set_order`).  It also reorders a set printed in value order already (an
      int set, sorted by repr), and counts it, though that order was no noise."""
    tree = re.compile(re.escape(root) + "(?=/)")
    moved = re.compile("<(" + "|".join(map(re.escape, relocated)) + r")\.(?=\w+ object at 0x)")

    def relocate(text: str) -> str:
        return moved.sub(lambda m: f"<{relocated[m.group(1)]}.", text) if relocated else text

    return {
        "tree-root": partial(tree.sub, "<root>"),
        "address": partial(re.compile(r" at 0x[0-9a-f]+(?=>)").sub, " at 0x?"),
        "csrf-token": partial(re.compile(
            r'(name="csrf_token" value="|name="csrf-token" content=")[^"]+').sub,
            r"\1<csrf-token>"),
        "session-cookie": partial(re.compile(
            r"""(\('Set-Cookie', 'session=)([^;'"]+)(?=;)""").sub, _session),
        "run-socket": partial(re.compile(r"shekel-testrun-\d+").sub, "shekel-testrun-?"),
        "uuid": partial(re.compile(r"(\('X-Request-Id', ')" + _UUID + "(?=')").sub,
                        r"\1<uuid>"),
        "relocated-repr": relocate,
        "set-order": _set_order,
    }


def normalized(record: Record, root: str, relocated: Mapping[str, str]
               ) -> tuple[Record, dict[str, int]]:
    """``record`` with every passing assert's explanation and every exception's message
    rewritten by each normalization kind in turn (:func:`_kinds`), and how many of those
    texts each kind changed, every kind named, in the order they apply.  ``root`` is the root
    the record was measured under, in the spelling its texts hold; ``relocated`` maps each
    module a move wrote to the module it moved from, by dotted name (empty: nothing moved).
    Outcomes, arcs, each assert's line and text, and each exception's type are kept as they
    are.  Every record is normalized by this one function, so the same normalization holds
    within a layout and between two (the coordinator's ruling on delta review 4's H-1,
    2026-10-16).  A failing assert's message is ``assert`` and pytest's explanation, the
    assert's own message before it when it has one, and each kind reads it where it reads
    an explanation, but ``set-order``: that kind reads a first line that is sets' reprs
    joined by `` == ``, and a failing assert's first line is not, so a failing set
    comparison is compared as it was recorded, and a set printed in two orders is a
    difference (the branch's neutral review of H-1, M1: fail closed; widening the kind needs
    a measured instance and the coordinator's ruling)."""
    kinds = _kinds(root, relocated)
    changed = dict.fromkeys(kinds, 0)

    def rewritten(explanation: str) -> str:
        for kind, rewrite in kinds.items():
            text = rewrite(explanation)
            if text != explanation:
                changed[kind] += 1
            explanation = text
        return explanation

    tests = {nodeid: replace(trace, asserts={
        phase: tuple((line, orig, rewritten(expl)) for line, orig, expl in held)
        for phase, held in trace.asserts.items()}, raised={
        phase: (kind, rewritten(message)) for phase, (kind, message) in trace.raised.items()})
        for nodeid, trace in record.tests.items()}
    return replace(record, tests=tests), changed
