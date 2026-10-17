"""Two records compared test by test (:func:`tools.testrecord.compare.differences`), on
hand-built records: each kind of difference, the identity of two equal records, and the
second record read through a translation and an id map.  Then each record's explanations
normalized (:func:`tools.testrecord.compare.normalized`): every kind on an instance measured
in a recorded run, every anchor on a text it must leave as it is, the counts, and the known
answers still flagged once both records are normalized.
"""
from __future__ import annotations

import ast
import base64
import re
import textwrap
import zlib

import pytest

from tools.testrecord.compare import differences, normalized
from tools.testrecord.record import Record, Trace, record

#: A moved test's file in the second record, and the file it was moved from.
SOURCE, PART = "tests/test_big.py", "tests/test_big/test_part.py"


def trace(arcs: dict | None = None, asserts: dict | None = None,
          outcome: str = "passed", raised: dict | None = None) -> Trace:
    """A test as a record holds it: ``arcs`` by phase and file, ``asserts`` by phase, and
    ``raised``, the exception that ended each phase that raised one, by phase."""
    return Trace(outcome=outcome,
                 arcs={phase: {path: frozenset(held) for path, held in files.items()}
                       for phase, files in (arcs or {}).items()},
                 asserts={phase: tuple(held) for phase, held in (asserts or {}).items()},
                 raised={phase: tuple(held) for phase, held in (raised or {}).items()})


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


#: An assert failing in a call, as a record holds the exception that ended it.
FAILING = ("AssertionError", "assert 2 == 3\n +  where 2 = double(1)")


@pytest.mark.parametrize("first, second, phase", [
    (trace(outcome="skipped", raised={"setup": ("Skipped", "on purpose")}),
     trace(outcome="skipped", raised={"setup": ("Skipped", "in tests.test_big.test_part")}),
     "setup"),
    (trace(BASE.arcs, outcome="xfailed", raised={"call": FAILING}),
     trace(BASE.arcs, outcome="xfailed", raised={"call": (FAILING[0], "assert 2 == 4")}),
     "call"),
    (trace(BASE.arcs, outcome="failed", raised={"call": FAILING}),
     trace(BASE.arcs, outcome="failed", raised={"call": ("ValueError", FAILING[1])}), "call"),
], ids=["a-skip-reason", "an-xfail-message", "another-type"])
def test_another_exception_ending_a_phase_is_a_raised_difference(first, second, phase):
    """The same test ending the same way, its phase ended by another exception: a skip's
    other reason (in a phase holding no arc and no assert), an expected failure's assert
    failing on another value, or another type with the same message (the coordinator's
    ruling on delta review 4's H-1, 2026-10-16)."""
    found = differences(of(t=first), of(t=second))
    assert [(d.kind, d.phase, d.first, d.second) for d in found] == [
        ("raised", phase, first.raised[phase], second.raised[phase])]


def test_an_exception_ending_a_phase_in_one_record_only_is_a_raised_difference():
    """An expected failure whose call failed in one record, and whose setup xfailed before
    it ran in the other: each phase's exception is the one record's only."""
    notrun = ("XFailed", "[NOTRUN] reason")
    found = differences(of(t=trace(BASE.arcs, outcome="xfailed", raised={"call": FAILING})),
                        of(t=trace(BASE.arcs, outcome="xfailed", raised={"setup": notrun})))
    assert [(d.kind, d.phase, d.first, d.second) for d in found] == [
        ("raised", "call", FAILING, ()), ("raised", "setup", (), notrun)]


@pytest.mark.parametrize("outcome, phase, raised", [
    ("failed", "call", FAILING), ("error", "setup", ("RuntimeError", "setup fails")),
    ("skipped", "setup", ("Skipped", "skipped")), ("xfailed", "call", FAILING)],
    ids=["failed", "error", "skipped", "xfailed"])
def test_the_same_exception_ending_a_phase_is_no_difference(outcome, phase, raised):
    """A test ending the same way in both records, its phase ended by the same exception."""
    one, other = (trace(BASE.arcs, BASE.asserts, outcome, {phase: raised}) for _ in "12")
    assert not differences(of(t=one), of(t=other))


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


#: The root PR A's item (2) re-recorded test_loan's source layout under (2026-10-16), a
#: scratch path holding a UUID; and the modules the loan split wrote, each read as the source
#: module it moved from.
ROOT = ("/tmp/claude-1000/-home-josh-projects-shekel-xcx-l7/"
        "255d5a62-df2f-49c8-a755-43b9063a04be/scratchpad/item2/rr/test_loan/before")
LOAN = "tests.test_routes.test_loan"
MOVED = {f"{LOAN}.{module}": LOAN for module in ("test_params", "_helpers", "conftest")}

#: A Flask-WTF token as the re-records hold it.
TOKEN = ("ImNmZDhlM2RjMDJlNzdmNTYxOWNkY2NiYjY3MDUxMDAzNmY1OWI4MGYi"
         ".atKaMw.KkAbmwafD8DhzCV_Wn0JcVthIMk")

#: Flask's signed session as the re-records hold it.
SESSION = ("eyJfZmxhc2hlcyI6W3siIHQiOlsid2FybmluZyIsIlBsZWFzZSBsb2cgaW4gdG8gYWNjZXNzIHRoaXMgcGFn"
           "ZS4iXX1dfQ.atKaPA.kuX_ONU6gMYGVY0VZV63pY_6fMk")


def norm(explanation: str, relocated: dict | None = None) -> tuple[str, list[str]]:
    """``explanation`` normalized as the one passing assert of a one-test record measured
    under :data:`ROOT` (of the loan split, unless ``relocated`` says otherwise), and the
    kinds that changed it."""
    found, changed = normalized(of(t=trace(asserts={"call": [(1, "x", explanation)]})), ROOT,
                                MOVED if relocated is None else relocated)
    return found.tests["t"].asserts["call"][0][2], [kind for kind, n in changed.items() if n]


#: Each kind's instance in PR A's item (2) re-records (2026-10-16): the recorded text, the
#: text normalized, and the kinds that change it.  Verbatim, but for the module row's root:
#: it was recorded under the savings file's root, and is shown here under :data:`ROOT`.
MEASURED = {
    "tree-root-module": (
        f"<module 'app.services.balance_at' from '{ROOT}/app/services/balance_at/__init__.py'>",
        "<module 'app.services.balance_at' from '<root>/app/services/balance_at/__init__.py'>",
        ["tree-root"]),
    "tree-root-path": (f"PosixPath('{ROOT}/app/routes/loan')",
                       "PosixPath('<root>/app/routes/loan')", ["tree-root"]),
    "address": ("<flask_sqlalchemy.query.Query object at 0x7fd1db5f3bd0>",
                "<flask_sqlalchemy.query.Query object at 0x?>", ["address"]),
    "csrf-token-field": (f'<input type="hidden" name="csrf_token" value="{TOKEN}">',
                         '<input type="hidden" name="csrf_token" value="<csrf-token>">',
                         ["csrf-token"]),
    "csrf-token-meta": (f'<meta name="csrf-token" content="{TOKEN}">',
                        '<meta name="csrf-token" content="<csrf-token>">', ["csrf-token"]),
    "session-cookie": (f"('Set-Cookie', 'session={SESSION}; HttpOnly; Path=/')",
                       "('Set-Cookie', 'session=<session {\"_flashes\":[{\" t\":[\"warning\","
                       "\"Please log in to access this page.\"]}]}>; HttpOnly; Path=/')",
                       ["session-cookie"]),
    "run-socket": ("<SQLAlchemy postgresql://shekel_user:***@/shekel_test_main?host=%2Frun%2F"
                   "user%2F1000%2Fshekel-testrun-3822585>",
                   "<SQLAlchemy postgresql://shekel_user:***@/shekel_test_main?host=%2Frun%2F"
                   "user%2F1000%2Fshekel-testrun-?>", ["run-socket"]),
    "uuid": ("('X-Request-Id', 'dc923010-24aa-4582-be20-29fbdabeaa71')",
             "('X-Request-Id', '<uuid>')", ["uuid"]),
    "relocated-repr": (f"<{LOAN}.test_params.TestRecordTrackingStartRoute object at "
                       "0x7fe08463d450>",
                       f"<{LOAN}.TestRecordTrackingStartRoute object at 0x?>",
                       ["address", "relocated-repr"]),
}


@pytest.mark.parametrize("raw, expected, changed", list(MEASURED.values()), ids=list(MEASURED))
def test_each_kind_rewrites_its_measured_instance(raw, expected, changed):
    """Each kind rewrites its value as a recorded run held it, and only the kinds listed
    change the text."""
    assert norm(raw) == (expected, changed)


#: Text each anchor leaves as it is: the context its kind was measured in, missing or other.
UNANCHORED = {
    "tree-root-sibling": f"PosixPath('{ROOT}2/app/routes/loan')",
    "address-unclosed": "'the block at 0x7fd1db5f3bd0 is free'",
    "csrf-token-another-attribute-between": (
        f'<input name="csrf_token" type="hidden" value="{TOKEN}">'),
    "session-cookie-another-name": f"Holder(db_session={SESSION}; open)",
    "session-cookie-unended": f"('Set-Cookie', 'session={SESSION}')",
    "uuid-not-the-header": "UUID('dc923010-24aa-4582-be20-29fbdabeaa71')",
    "relocated-repr-a-class": f"<class '{LOAN}.test_params.TestRecordTrackingStartRoute'>",
    "relocated-repr-not-an-object": f"<{LOAN}.test_params.TestRecordTrackingStartRoute>",
    "relocated-repr-a-module-not-moved": f"<{LOAN}.test_other.TestOther object at 0x?>",
    "relocated-repr-a-module-name": f"'{LOAN}._helpers'",
}


@pytest.mark.parametrize("text", list(UNANCHORED.values()), ids=list(UNANCHORED))
def test_text_outside_its_kinds_measured_context_is_compared_as_it_is(text):
    """A value in no measured context is no kind's: it is compared as it was recorded."""
    assert norm(text) == (text, [])


def test_a_root_holding_a_uuid_is_rewritten_whole_by_the_root_kind_alone():
    """The pilot's measured order failure: its UUID kind matched inside a scratch root, and
    run before the root kind it left the root unmatched.  The root is rewritten whole, and
    the UUID kind, anchored on the header, changes nothing in it."""
    assert re.search(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/", ROOT)
    assert norm(f"PosixPath('{ROOT}/app/routes/loan/_bp.py')") == (
        "PosixPath('<root>/app/routes/loan/_bp.py')", ["tree-root"])


def test_with_nothing_moved_no_repr_is_relocated():
    """A record of no move: no module reads as another, a repr naming no module included."""
    assert norm(MEASURED["relocated-repr"][0], relocated={}) == (
        f"<{LOAN}.test_params.TestRecordTrackingStartRoute object at 0x?>", ["address"])
    assert norm("<.Nameless object at 0x1f>", relocated={}) == (
        "<.Nameless object at 0x?>", ["address"])


def test_each_kind_counts_the_explanations_it_changed():
    """Three asserts: one two kinds change, one holding two addresses, one no kind changes.
    A kind counts explanations, not matches, and every kind is named, none changed as 0;
    the outcome, the arcs, and each assert's line and text are kept."""
    held = trace(BASE.arcs, {"call": [
        (6, "a", MEASURED["relocated-repr"][0]),
        (7, "b", "<flask.Flask object at 0x1f> is not <flask.Flask object at 0x2e>"),
        (8, "c", "1 == 1")]}, "failed")
    found, changed = normalized(of(t=held), ROOT, MOVED)
    assert changed == {"tree-root": 0, "address": 2, "csrf-token": 0, "session-cookie": 0,
                       "run-socket": 0, "uuid": 0, "relocated-repr": 1, "set-order": 0}
    kept = found.tests["t"]
    assert (kept.outcome, kept.arcs) == ("failed", held.arcs)
    assert [entry[:2] for entry in kept.asserts["call"]] == [(6, "a"), (7, "b"), (8, "c")]


def test_an_exceptions_message_is_normalized_by_the_same_kinds_and_counted():
    """An exception's message is normalized and counted as an explanation is: a moved
    object's repr in each layout's failing message is one text normalized, while the records
    differ as recorded."""
    moved, kept = (f"assert <{module}.TestRecordTrackingStartRoute object at {address}> is None"
                   for module, address in ((f"{LOAN}.test_params", "0x7fe08463d450"),
                                           (LOAN, "0x7fe08463d999")))
    records = [of(t=trace(outcome="failed", raised={"call": ("AssertionError", message)}))
               for message in (moved, kept)]
    assert kinds(differences(*records)) == [("raised", "call")]
    normal = [normalized(held, ROOT, MOVED) for held in records]
    assert not differences(normal[0][0], normal[1][0])
    assert normal[0][0].tests["t"].raised == {"call": (
        "AssertionError", f"assert <{LOAN}.TestRecordTrackingStartRoute object at 0x?> is None")}
    assert [kind for kind, n in normal[0][1].items() if n] == ["address", "relocated-repr"]


def test_a_failing_set_comparison_is_compared_as_it_was_recorded():
    """set-order reads a first line that is two sets' reprs around one `` == ``; a failing
    assert's first line opens with ``assert``, so one set printed in two orders there is a
    difference after normalization, where the same sides in a passing explanation are sorted
    (the branch's neutral review of H-1, M1: fail closed)."""
    records = [of(t=trace(outcome="failed", raised={"call": ("AssertionError", text)}))
               for text in ("assert {'b', 'a'} == {'c'}", "assert {'a', 'b'} == {'c'}")]
    normal = [normalized(held, ROOT, MOVED) for held in records]
    assert kinds(differences(normal[0][0], normal[1][0])) == [("raised", "call")]
    assert [changed["set-order"] for _, changed in normal] == [0, 0]
    assert norm("{'b', 'a'} == {'c'}") == ("{'a', 'b'} == {'c'}", ["set-order"])


def _set(*names) -> str:
    """The repr of a set of ``names``, its elements in the order given."""
    return "{" + ", ".join(map(repr, names)) + "}"


#: test_savings_dashboard_service's ``TestComputeDashboardData::test_returns_expected_keys``
#: as PR A's item (2) re-recorded it (2026-10-16), ``set(result.keys()) == expected_keys``:
#: the left set printed alike in all four runs, the right one (a literal) alike within each
#: layout and in another order between them, and pytest's sorted diff below the first line.
KEYS = ("net_worth", "account_data", "total_savings", "property_equity", "emergency_metrics",
        "savings_accounts", "debt_summary", "goal_data", "group_subtotals",
        "avg_monthly_expenses", "sparklines", "archived_accounts", "grouped_accounts")
RIGHT_BEFORE = ("net_worth", "total_savings", "savings_accounts", "emergency_metrics",
                "debt_summary", "property_equity", "goal_data", "group_subtotals",
                "avg_monthly_expenses", "sparklines", "grouped_accounts", "archived_accounts",
                "account_data")
RIGHT_AFTER = ("net_worth", "account_data", "total_savings", "savings_accounts",
               "emergency_metrics", "debt_summary", "property_equity", "goal_data",
               "group_subtotals", "avg_monthly_expenses", "sparklines", "archived_accounts",
               "grouped_accounts")
DIFF = ("\n  \n  Full diff:\n    {\n" + "".join(f"        {name!r},\n" for name in sorted(KEYS))
        + "    }")


def test_a_set_literal_prints_its_elements_sorted_in_both_layouts():
    """The savings instance: the layouts' explanations differ as recorded and are one text
    normalized, each set's elements sorted by their repr, the diff below kept as it is."""
    before = f"{_set(*KEYS)} == {_set(*RIGHT_BEFORE)}{DIFF}"
    after = f"{_set(*KEYS)} == {_set(*RIGHT_AFTER)}{DIFF}"
    assert before != after
    expected = f"{_set(*sorted(KEYS))} == {_set(*sorted(KEYS))}{DIFF}"
    assert norm(before) == (expected, ["set-order"])
    assert norm(after) == (expected, ["set-order"])


@pytest.mark.parametrize("raw, expected", [
    ("{'{b', 'a}'} == {'a}', '{b'}", "{'a}', '{b'} == {'a}', '{b'}"),
    ("{None, 2.5, b'x', 'a', (1, 'b'), True} == {True, (1, 'b'), 'a', b'x', 2.5, None}",
     "{'a', (1, 'b'), 2.5, None, True, b'x'} == {'a', (1, 'b'), 2.5, None, True, b'x'}"),
], ids=["braces-inside-a-string", "mixed-types"])
def test_a_set_of_scalars_is_sorted_by_repr(raw, expected):
    """A brace inside a string is the string's (the side is parsed, not scanned), and the
    order is total over mixed types (by repr)."""
    assert norm(raw) == (expected, ["set-order"])


def test_set_order_sorts_the_text_the_other_kinds_wrote():
    """One set of two strings printed by two runs, its addresses ordering the elements one
    way in one run and the other way in the other: sorted after the addresses are one
    spelling, the two runs print it alike."""
    one, two = ("'<A object at 0x2> b'", "'<A object at 0x1> c'"), (
        "'<A object at 0x1> b'", "'<A object at 0x2> c'")
    expected = ("{'<A object at 0x?> b', '<A object at 0x?> c'} == "
                "{'<A object at 0x?> b', '<A object at 0x?> c'}")
    for first, second in (one, two):
        raw = f"{{{first}, {second}}} == {{{second}, {first}}}"
        assert norm(raw) == (expected, ["address", "set-order"])


#: First lines set-order leaves as they are, by why.
NOT_TWO_SETS = {
    "an-element-holding-equals-in-both-sets": "{'b', 'x == y'} == {'x == y', 'b'}",
    "an-element-holding-equals-in-the-left-set": "{'b', 'x == y'} == {'b', 'a'}",
    "an-element-holding-equals-in-the-right-set": "{'b', 'a'} == {'a', 'b == c'}",
    "one-side-a-set": "{'b', 'a'} == expected_keys",
    "a-non-literal-element": (
        "{datetime.date(2024, 1, 4), 'a'} == {'a', datetime.date(2024, 1, 4)}"),
    "a-nested-frozenset": "{frozenset({2, 1}), 3} == {3, frozenset({1, 2})}",
    "frozensets": "frozenset({'b', 'a'}) == frozenset({'a', 'b'})",
    "a-nested-tuple": "{((2, 1), 3), 4} == {4, ((2, 1), 3)}",
    "a-complex-element": "{1j, 2} == {2, 1j}",
    "a-cut-repr": (
        "{'account_data'...total_savings'} == {'account_data', 'net_worth', 'total_savings'}"),
    "an-ellipsis-element": "{'b', 'a', ...} == {'a', 'b', ...}",
    "two-equal-values": "{1, True} == {True, 1}",
    "a-float-not-as-repr-prints-it": "{1.00, 2} == {2, 1.00}",
    "a-double-quoted-string": "{\"b\", 'a'} == {'a', \"b\"}",
    "elements-not-joined-as-repr-joins": "{2,1} == {1,2}",
    "nesting-past-the-parser-stack": "{" + "-" * 200_000 + "1} == {1}",
    "an-integer-past-the-digit-limit": "{0x" + "f" * 4000 + "} == {1}",
    "an-unhashable-element": "{{[1]}} == {1}",
    "chained-operators-past-the-compilers-stack": "{x" + "[0]" * 40_000 + "} == {1}",
    "a-lone-surrogate": "{'\ud800'} == {'a'}",
}


@pytest.mark.parametrize("text", list(NOT_TWO_SETS.values()), ids=list(NOT_TWO_SETS))
def test_a_line_that_is_not_two_sets_reprs_is_compared_as_it_is(text):
    """Fail closed: a first line that does not hold exactly one `` == `` between two sides
    that are each byte for byte the repr of a set of scalars is compared as recorded."""
    assert norm(text) == (text, [])


#: The known answers, as the R-BAL303 pilot recorded them (2026-10-16): each test's node id,
#: its first record's call and its second's.  (a) test_loan's parents[2] glob: the second
#: layout never runs the loop's body, source line 3477 (its arcs here are a loop's, in the
#: measured difference's shape), and compares against '' (the first's compared text, the loan
#: package's source, is 434,863 characters, cut here; no kind matches anywhere in the whole
#: of it, measured); (b) a split class's docstring; (c) a moved helper's module name.
KA_LOAN = "tests/test_routes/test_loan.py"
KNOWN_ANSWERS = {
    "a": (f"{KA_LOAN}::TestPayoffChartShape::test_no_direct_calculate_summary_call", trace(
        {"call": {KA_LOAN: {(3476, 3477), (3477, 3476), (3476, 3478)}}},
        {"call": [(3478, '"amortization_engine.calculate_summary" not in text',
                   "'amortization_engine.calculate_summary' not in '\"\"\"\\nShekel Budget App "
                   "-- Loan Route Package\\n\\nUnified dashboard, parameter updates, escrow / "
                   "rate-history mana'")]}), trace(
        {"call": {KA_LOAN: {(3476, 3478)}}},
        {"call": [(3478, '"amortization_engine.calculate_summary" not in text',
                   "'amortization_engine.calculate_summary' not in ''\n  \n  "
                   "'amortization_engine.calculate_summary' is contained here:")]})),
    "b": ("tests/test_ka/test_sample.py::TestBig::test_a", trace(asserts={"call": [(
        9, "held.__doc__", "'The big class, which is split across two parts.'\n +  where 'The "
        "big class, which is split across two parts.' = <class "
        "'tests.test_ka.test_sample.TestBig'>.__doc__")]}), trace(asserts={"call": [(
            9, "held.__doc__", "'test_a; members on _TestBigMembers.'\n +  where 'test_a; "
            "members on _TestBigMembers.' = <class "
            "'tests.test_ka.test_sample.test_first.TestBig'>.__doc__")]})),
    "c": ("tests/test_ka/test_sample.py::TestBig::test_b", trace(asserts={"call": [(
        12, 'getattr(_make, "__mod" + "ule__")', "'tests.test_ka.test_sample'\n +  where "
        "'tests.test_ka.test_sample' = getattr(_make, ('__mod' + 'ule__'))")]}), trace(
            asserts={"call": [(12, 'getattr(_make, "__mod" + "ule__")',
                               "'tests.test_ka.test_sample._helpers'\n +  where "
                               "'tests.test_ka.test_sample._helpers' = getattr(_make, "
                               "('__mod' + 'ule__'))")]})),
}

#: The modules each known answer's split wrote, read as its source module.
KA_MOVED = {"a": {f"{LOAN}.{m}": LOAN for m in ("test_payoff_chart", "_helpers", "conftest")},
            **dict.fromkeys("bc", {f"tests.test_ka.test_sample.{m}": "tests.test_ka.test_sample"
                                   for m in ("test_first", "test_second", "_helpers",
                                             "conftest")})}


@pytest.mark.parametrize("answer, flagged", [
    ("a", [("lines", "call"), ("asserts", "call")]), ("b", [("asserts", "call")]),
    ("c", [("asserts", "call")])])
def test_the_known_answers_stay_flagged_under_every_kind(answer, flagged):
    """Each known answer differs between its records as recorded, and in the same kinds and
    phases once both are normalized by every kind."""
    nodeid, first, second = KNOWN_ANSWERS[answer]
    records = [of(**{nodeid: held}) for held in (first, second)]
    assert kinds(differences(*records)) == flagged
    normal = [normalized(held, ROOT, KA_MOVED[answer])[0] for held in records]
    assert kinds(differences(*normal)) == flagged


#: A sample test comparing two sets of 40 strings, each side of its explanation's first line
#: 440 characters.
LONG_SET = textwrap.dedent('''\
    NAMES = [f"name_{i:02d}" for i in range(40)]


    def test_a_long_set():
        assert set(NAMES) == set(reversed(NAMES))
    ''')


def test_a_recorded_run_prints_a_long_set_whole_for_set_order_to_sort(project, out):
    """set-order is sound only on sides pytest printed whole (a cut side can be a set's repr:
    :func:`tools.testrecord.compare._sorted_set`), which it does at the assertion verbosity
    :func:`tools.testrecord.record.record` runs at: both sets are in the recorded first line
    whole, and set-order sorts them."""
    (project / "tests/test_long_set.py").write_text(LONG_SET, encoding="utf-8")
    nodeid = "tests/test_long_set.py::test_a_long_set"
    found = record(project, ["tests/test_long_set.py"], out, extra=("-n", "0"))
    expl = found.tests[nodeid].asserts["call"][0][2]
    names = {f"name_{i:02d}" for i in range(40)}
    assert [ast.literal_eval(side) for side in expl.partition("\n")[0].split(" == ")] == [
        names, names]
    rewritten, changed = normalized(found, str(project), {})
    whole = "{" + ", ".join(map(repr, sorted(names))) + "}"
    assert rewritten.tests[nodeid].asserts["call"][0][2].partition("\n")[0] == (
        f"{whole} == {whole}")
    assert changed["set-order"] == 1


def _cookie(value: str) -> str:
    """A ``Set-Cookie`` header holding the session ``value``, as a response's headers' repr
    prints it."""
    return f"('Set-Cookie', 'session={value}; HttpOnly; Path=/')"


def _b64(raw: bytes) -> str:
    """``raw`` in base64url without padding, as itsdangerous writes a payload."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _signed(session: str, stamp: str) -> str:
    """The session ``session`` (its JSON) as Flask writes it into a cookie: compressed,
    base64url, then ``stamp``, its timestamp and signature."""
    return "." + _b64(zlib.compress(session.encode("utf-8"))) + "." + stamp


#: What every session below holds of its login, as test_loan's sessions recorded it.
LOGIN = ('"_fresh":true,"_fresh_login_at":"2026-03-20T12:00:00+00:00","_id":"03f095a1c03fdce8'
         'fe2ea94737ef9d62c456faf66d67aac9e9d7b2f6548211aab71d5ce5c550e74c31a055e110b5ce1429cf'
         'cf4eb860fe2c460a0c6f830c7f59","_session_created_at":"2026-03-20T12:00:00+00:00",'
         '"_session_last_activity_at":"2026-03-20T12:00:00+00:00","_user_id":"1"')

#: The session cookie test_loan's ``TestTransferPrompt::test_create_transfer_success`` set
#: in a run of each layout (PR A's item (2) re-records, 2026-10-16): one session under two
#: timestamps and signatures.  Built from the session's JSON, each is byte for byte the
#: recorded value (checked against the record, 2026-10-16).
TRANSFER = [_signed('{"_flashes":[{" t":["success","Recurring monthly transfer of $1,611.77 '
                    'created from Checking to My Mortgage."]}],' + LOGIN + "}", stamp)
            for stamp in ("atKafw.38yj424ctC3uovqzp8z4x62SMCY",
                          "atKbPw.Sm-dVcef33x_jfgRe08qNV3shn0")]

#: The session cookie ``TestPaymentDrift::test_track_payment_flips_to_derive_and_clear`` set
#: in two runs of the source layout: two sessions, differing in the random token Flask-WTF
#: keeps in the session; built and checked as :data:`TRANSFER` is.
DRIFT = [_signed('{"_flashes":[{" t":["success","\'Checking -> My Mortgage Payment\' now tracks '
                 'the loan automatically ($1,611.77 this month)."]}],' + LOGIN
                 + f',"csrf_token":"{token}"}}', stamp)
         for token, stamp in (("435894ec41df905c2bf586159a848a03f276ec5c",
                               "atKajA.MEPF5mFdjcdKUCwGxkhlcHstu-Q"),
                              ("62970b9e607e34710d29dedd8ae41dab4627631e",
                               "atKb6w.CkAieUmtTPygXx5QRdIJOSbdKUc"))]


def _payload(value: str) -> bytes:
    """A compressed session cookie's payload, decoded."""
    payload = value[1:].split(".")[0]
    return zlib.decompress(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def test_a_session_is_compared_by_what_it_holds_and_not_by_when_it_was_signed():
    """The two layouts' transfer cookie, under two timestamps, is one session normalized,
    which shows the flash; the same cookie flashing $1,611.78 instead is another (the review's
    H1: rewriting the whole value erased the only record of the app's output)."""
    assert TRANSFER[0] != TRANSFER[1]
    one, other = (norm(_cookie(value)) for value in TRANSFER)
    assert one == other
    assert "Recurring monthly transfer of $1,611.77 created" in one[0]
    assert one[1] == ["session-cookie"]
    raw = _payload(TRANSFER[0])
    assert raw.count(b"$1,611.77") == 1
    changed = "." + _b64(zlib.compress(raw.replace(b"$1,611.77", b"$1,611.78"))) + ".atKafw.38yj"
    assert norm(_cookie(changed))[0] == one[0].replace("$1,611.77", "$1,611.78")


def test_the_random_token_a_session_holds_is_one_placeholder():
    """Two runs' drift cookies differ in the session's own csrf_token, and are one session
    normalized, the token as ``<csrf-token>``; a csrf_token below the top level is kept, and
    a session's keys are in one order whatever order its text held them in."""
    assert _payload(DRIFT[0]) != _payload(DRIFT[1])
    one, other = (norm(_cookie(value)) for value in DRIFT)
    assert one == other
    assert '"csrf_token":"<csrf-token>"' in one[0]
    nested = _b64(b'{"csrf_token":"a1","form":{"csrf_token":"b2"}}') + ".atKafw.38yj"
    assert norm(_cookie(nested)) == (_cookie(
        '<session {"csrf_token":"<csrf-token>","form":{"csrf_token":"b2"}}>'), ["session-cookie"])
    assert norm(_cookie(_b64(b'{"b":1,"a":2}') + ".atKafw.38yj")) == norm(
        _cookie(_b64(b'{"a":2,"b":1}') + ".atKbPw.Sm-d"))


#: Session values no session decodes from, by why: each is left whole.
UNDECODED = {
    "not-three-segments": _b64(b'{"a":1}') + ".atKafw",
    "not-base64": "abcde.atKafw.38yj",
    "not-zlib": "." + _b64(b"not compressed") + ".atKafw.38yj",
    "not-utf-8": _b64(b"\xff\xfe") + ".atKafw.38yj",
    "not-json": _b64(b"{not json") + ".atKafw.38yj",
    "json-nested-past-the-decoders-stack": _b64(b"[" * 100_000) + ".atKafw.38yj",
    "json-not-an-object": _b64(b"[1, 2]") + ".atKafw.38yj",
}


@pytest.mark.parametrize("value", list(UNDECODED.values()), ids=list(UNDECODED))
def test_a_session_value_that_does_not_decode_is_left_whole(value):
    """Fail closed: a value Flask did not encode as a session is compared as recorded."""
    assert norm(_cookie(value)) == (_cookie(value), [])
