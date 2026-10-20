"""The row grader (:mod:`.rowdiff`, step X-cx's K1b-1): the key and the stamps each table is
read by, the summary and the report on planted rows, and, on a real private cluster (the
``cluster`` cases, a :class:`bench.Bench` on the conftest's ``dump``), the stamps a catalog
names, the line, and every row compared.
"""
from __future__ import annotations

import psycopg
import pytest

from tools.rehearsal import bench, rowdiff
from tools.rehearsal.rowdiff import Held, Moved


def test_a_table_is_compared_by_its_key_only_when_both_sides_agree_on_one():
    """The key both sides hold; a multiset where they differ, where there is none, and for
    the audit log always; a table on one side by that side's key."""
    before = {("budget", "a"): Held(("id",)), ("budget", "b"): Held(("id",)),
              ("budget", "c"): Held(()), ("system", "audit_log"): Held(("id",)),
              ("budget", "d"): Held(("id", "part"))}
    after = {("budget", "a"): Held(("id",)), ("budget", "b"): Held(("id", "kind")),
             ("budget", "c"): Held(()), ("system", "audit_log"): Held(("id",)),
             ("budget", "e"): Held(("code",))}
    assert rowdiff.keys(before, after) == {
        ("budget", "a"): ("id",), ("budget", "b"): (), ("budget", "c"): (),
        ("budget", "d"): ("id", "part"), ("budget", "e"): ("code",),
        ("system", "audit_log"): ()}


def test_a_column_is_a_stamp_only_where_every_side_holding_its_table_says_so():
    """Both sides' stamps; a column one side's catalog calls a stamp and the other's does
    not (a cut adding or dropping a clock default) is none, so it is compared exactly; a
    table on one side by that side's stamps.  Every day of either side is named."""
    before = {("budget", "a"): Held(("id",), ("created_at", "updated_at")),
              ("budget", "b"): Held(("id",), ("created_at",), ("on_day",)),
              ("budget", "d"): Held(("id",), ("created_at",))}
    after = {("budget", "a"): Held(("id",), ("created_at", "updated_at")),
             ("budget", "b"): Held(("id",), ("created_at", "posted_at")),
             ("budget", "e"): Held(("id",), ("seen_at",), ("due_on",))}
    assert rowdiff.stamps(before, after) == {
        ("budget", "a"): ("created_at", "updated_at"), ("budget", "b"): ("created_at",),
        ("budget", "d"): ("created_at",), ("budget", "e"): ("seen_at",)}
    one, other = after, before
    assert rowdiff.stamps(one, other)[("budget", "b")] == ("created_at",)
    assert rowdiff.days(before, after) == ["budget.b.on_day", "budget.e.due_on"]


def test_the_summary_sums_each_table_and_column_and_totals_only_numbers(moved_rows):
    """Rows per table by side; per column the rows, and a total only where every changed
    value is a number on both sides (a boolean is not one, nor a null)."""
    assert rowdiff.summary(moved_rows, "base", "cut") == [
        "budget.accounts: 4 rows differ (1 only on base, 0 only on cut, 3 changed)",
        "  active: 1 rows",
        "  balance: 3 rows",
        "  name: 1 rows",
        "system.audit_log: 1 rows differ (0 only on base, 1 only on cut, 0 changed)",
    ]
    assert (rowdiff.summary(moved_rows[:2], "base", "cut")[2]
            == "  balance: 2 rows, total change +2.25")


def test_the_report_names_each_row_and_each_changed_column(moved_rows):
    """One line per row: a changed row's columns before and after, a row on one side whole."""
    assert list(rowdiff.report(moved_rows[:1] + moved_rows[3:], "base", "cut")) == [
        'budget.accounts [3]: active true -> false; balance "10.00" -> "12.50"; '
        'name "A" -> "B"',
        'budget.accounts [6]: only on base: {"id": 6}',
        'system.audit_log (no key): only on cut: ["budget", "accounts", "UPDATE", 3]',
    ]



@pytest.mark.cluster
def test_a_stamp_is_a_timestamp_whose_default_is_a_clock_read_and_nothing_else(planted):
    """From the catalog: ``clock_timestamp()``, ``CURRENT_TIMESTAMP``, ``LOCALTIMESTAMP(3)``
    and the audit log's ``now()`` make stamps, ``now()`` plus an interval does not, and a
    date the server's day fills is a day."""
    with psycopg.connect(planted.made["base"].url) as connection:
        held = rowdiff.catalog(connection)
    assert held[("public", "note")] == Held(("id", "part"), ("created_at",))
    assert held[("public", "timing")] == Held(("id",), ("at_local", "at_now"), ("on_day",))
    assert held[("system", "audit_log")] == Held(("id",), ("executed_at",))
    assert held[("public", "tally")] == Held(())


@pytest.mark.cluster
def test_a_stamp_later_than_the_line_reads_as_deploy_and_every_other_is_compared(clock, planted):
    """R-BAL331: two copies whose stamps the clock re-wrote after the line, one after the
    other (the audit log's own clock and the stamps of the rows its events wrote with them),
    read the same, the audit log's id and database user still left out; read through a line
    later than both, the same stamps differ.  A stamp moved earlier, a changed value, a row
    more in a multiset and the audit event the change's trigger wrote are each found."""
    base = planted.made["base"]
    line = clock(base.url)
    one = planted.clone(base.database, "one")
    other = planted.clone(base.database, "other")
    for url, also in ((one, ""), (other, ", id = id + 100, db_user = 'someone'")):
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute("SET session_replication_role = replica")
            connection.execute("UPDATE note SET created_at = now()")
            connection.execute("UPDATE system.audit_log SET executed_at = now(), new_data = "
                               "new_data || jsonb_build_object('created_at', now())" + also)
    assert not rowdiff.compare(one, other, line)
    assert {row.table for row in rowdiff.compare(one, other, clock(base.url))} == {
        "public.note", "system.audit_log"}
    with psycopg.connect(other, autocommit=True) as connection:
        connection.execute("UPDATE note SET body = 'dx' WHERE id = 1 AND part = 2")
        connection.execute("INSERT INTO tally VALUES ('x')")
        connection.execute("SET session_replication_role = replica")
        connection.execute("UPDATE note SET created_at = '2000-01-01T00:00:00+00:00' "
                           "WHERE id = 2")
    deployed = rowdiff.DEPLOY
    assert rowdiff.compare(one, other, line) == [
        Moved("public.note", "[1, 2]", {"id": 1, "part": 2, "body": "de", "created_at": deployed},
              {"id": 1, "part": 2, "body": "dx", "created_at": deployed}),
        Moved("public.note", "[2, 1]", {"id": 2, "part": 1, "body": "f", "created_at": deployed},
              {"id": 2, "part": 1, "body": "f", "created_at": "2000-01-01T00:00:00+00:00"}),
        Moved("public.tally", "", None, {"word": "x"}),
        Moved("system.audit_log", "", None, ["public", "note", "UPDATE", 0, None, None, None,
                                              None, {"executed_at": deployed}]),
    ]
    assert rowdiff.multisets(one, other) == ["public.tally", "system.audit_log"]


@pytest.mark.cluster
def test_a_dump_holding_a_stamp_later_than_the_line_measures_nothing(clock, planted):
    """The line is read on a copy every stamp of which is earlier; a row's stamp, the audit
    log's own clock and a stamp of a row an event wrote, each dated ahead of it, refuse the
    run, each named."""
    url = planted.made["base"].url
    assert rowdiff.read_line(url) < clock(url)
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute("SET session_replication_role = replica")
        connection.execute("UPDATE note SET created_at = now() + interval '1 day' WHERE id = 2")
        connection.execute("UPDATE system.audit_log SET executed_at = now() + interval '1 day', "
                           "new_data = new_data || "
                           "jsonb_build_object('created_at', now() + interval '1 day')")
    with pytest.raises(bench.BenchError) as refused:
        rowdiff.read_line(url)
    assert str(refused.value).startswith("the dump holds stamps later than the cluster's clock")
    assert str(refused.value).endswith(
        "cannot be told from them: public.note.created_at, system.audit_log.executed_at, "
        "system.audit_log.new_data.created_at")
