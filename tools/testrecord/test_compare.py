"""Two records compared test by test (:func:`tools.testrecord.compare.differences`), on
hand-built records: each kind of difference, the identity of two equal records, and the
second record read through a translation and an id map.
"""
from __future__ import annotations

import pytest

from tools.testrecord.compare import differences
from tools.testrecord.record import Record, Trace

#: A moved test's file in the second record, and the file it was moved from.
SOURCE, PART = "tests/test_big.py", "tests/test_big/test_part.py"


def trace(arcs: dict | None = None, asserts: dict | None = None,
          outcome: str = "passed") -> Trace:
    """A test as a record holds it: ``arcs`` by phase and file, ``asserts`` by phase."""
    return Trace(outcome=outcome,
                 arcs={phase: {path: frozenset(held) for path, held in files.items()}
                       for phase, files in (arcs or {}).items()},
                 asserts={phase: tuple(held) for phase, held in (asserts or {}).items()})


def of(**tests: Trace) -> Record:
    """A record of ``tests``, by node id."""
    return Record(tests=dict(tests), order=(), exitstatus=0)


#: One test calling a function at app line 10, which takes the branch at line 11 to 12.
BASE = trace({"call": {"app/m.py": {(-10, 11), (11, 12), (12, -10)},
                       SOURCE: {(-5, 6), (6, -5)}}},
             {"call": [(6, "x == 1", "1 == 1")]})


def kinds(found) -> list[tuple[str, str | None]]:
    """Each difference's kind and phase."""
    return [(d.kind, d.phase) for d in found]


def test_equal_records_differ_in_nothing():
    """The same record twice: no difference."""
    assert not differences(of(t=BASE), of(t=BASE))


def test_a_test_only_one_record_holds_is_missing_from_the_other():
    """A test in the first only, and one in the second only, each named on its side."""
    found = differences(of(t=BASE, u=BASE), of(t=BASE, v=BASE))
    assert [(d.test, d.kind, d.first, d.second) for d in found] == [
        ("u", "missing", ("u",), ()), ("v", "missing", (), ("v",))]


def test_another_outcome_is_a_difference():
    """The same trace, ending otherwise."""
    found = differences(of(t=BASE), of(t=trace(BASE.arcs, BASE.asserts, "failed")))
    assert [(d.kind, d.first, d.second) for d in found] == [
        ("outcome", ("passed",), ("failed",))]


def test_a_line_one_record_executed_is_a_lines_difference():
    """The other side of the branch: line 12 in the first, 13 in the second; the arcs
    touching those lines are the lines' difference, not an arcs one."""
    other = trace({"call": {"app/m.py": {(-10, 11), (11, 13), (13, -10)},
                            SOURCE: {(-5, 6), (6, -5)}}}, {"call": [(6, "x == 1", "1 == 1")]})
    found = differences(of(t=BASE), of(t=other))
    assert [(d.kind, d.first, d.second) for d in found] == [
        ("lines", (("app/m.py", 12),), (("app/m.py", 13),))]


def test_a_transition_between_lines_both_executed_is_an_arcs_difference():
    """The same lines, reached in another order: an arc only one record took."""
    other = trace({"call": {"app/m.py": {(-10, 11), (11, 12), (12, -10), (12, 11)},
                            SOURCE: {(-5, 6), (6, -5)}}}, {"call": [(6, "x == 1", "1 == 1")]})
    found = differences(of(t=BASE), of(t=other))
    assert [(d.kind, d.first, d.second) for d in found] == [
        ("arcs", (), (("app/m.py", 12, 11),))]


def test_another_compared_value_is_an_asserts_difference():
    """The same assert passing on another value: the explanations differ."""
    other = trace(BASE.arcs, {"call": [(6, "x == 1", "1.0 == 1")]})
    found = differences(of(t=BASE), of(t=other))
    assert [(d.kind, d.first, d.second) for d in found] == [
        ("asserts", (("x == 1", "1 == 1"),), (("x == 1", "1.0 == 1"),))]


def test_an_assert_on_another_line_alone_is_no_difference():
    """The line an assert reports is its place, which the arcs compare through the
    translation; the sequence compares its text and explanation."""
    other = trace(BASE.arcs, {"call": [(99, "x == 1", "1 == 1")]})
    assert not differences(of(t=BASE), of(t=other))


def test_the_same_asserts_in_another_phase_differ():
    """A phase's asserts are compared with the same phase's."""
    other = trace(BASE.arcs, {"setup": [(6, "x == 1", "1 == 1")]})
    assert kinds(differences(of(t=BASE), of(t=other))) == [
        ("asserts", "call"), ("asserts", "setup")]


def test_a_moved_test_is_read_through_the_translation_and_the_id_map():
    """The second record's test is in the part, its lines 105 and 106 copied from source
    lines 5 and 6, and its id is mapped to the source's: no difference, the entry and exit
    arcs (negative lines) translated by their line."""
    moved = trace({"call": {"app/m.py": {(-10, 11), (11, 12), (12, -10)},
                            PART: {(-105, 106), (106, -105)}}},
                  {"call": [(106, "x == 1", "1 == 1")]})
    table = {(PART, 105): (SOURCE, 5), (PART, 106): (SOURCE, 6)}

    def translate(path, line):
        return table.get((path, line)) if path == PART else (path, line)
    assert not differences(of(**{"src::t": BASE}), of(**{"part::t": moved}),
                           {"part::t": "src::t"}, translate)


def test_a_line_no_source_line_stands_for_is_untranslated():
    """A composed line the second record executed is reported, and the arc through it is
    left out of the comparison (it stands for no source arc)."""
    moved = trace({"call": {"app/m.py": {(-10, 11), (11, 12), (12, -10)},
                            PART: {(-105, 106), (106, -105), (106, 107)}}},
                  {"call": [(106, "x == 1", "1 == 1")]})
    table = {(PART, 105): (SOURCE, 5), (PART, 106): (SOURCE, 6)}

    def translate(path, line):
        return table.get((path, line)) if path == PART else (path, line)
    found = differences(of(t=BASE), of(t=moved), None, translate)
    assert [(d.kind, d.second) for d in found] == [("untranslated", ((PART, 107),))]


def test_an_arc_translated_into_two_files_is_refused():
    """An arc joins two lines of one file; a translation that parts them is wrong, and the
    arc's lines would leave the comparison unreported."""
    moved = trace({"call": {PART: {(-105, 106), (106, -105)}}})
    table = {(PART, 105): (SOURCE, 5), (PART, 106): ("tests/conftest.py", 6)}

    def translate(path, line):
        return table.get((path, line)) if path == PART else (path, line)
    with pytest.raises(ValueError, match="into two files"):
        differences(of(t=BASE), of(t=moved), None, translate)


@pytest.mark.parametrize("second, ids", [
    ({"one": BASE, "two": trace(outcome="failed")}, {"one": "src", "two": "src"}),
    ({"src": trace(outcome="failed"), "one": BASE}, {"one": "src"})],
    ids=["two-mapped-to-one", "one-unmapped-under-a-mapped-name"])
def test_two_tests_read_as_one_are_refused(second, ids):
    """Two of the second record's tests read as one of the first's would compare one of
    them and drop the other, whichever the record held last."""
    with pytest.raises(ValueError, match="read as 'src'"):
        differences(of(src=BASE), of(**second), ids)
