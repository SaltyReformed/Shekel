"""The rehearsal kit's test configuration: the ``cluster`` marker.

A test that starts a real private cluster carries it.  CI's every-scope step deselects
those (``-m "not cluster"``), since a registry-only change set cannot change the kit
(the coordinator's K1 leaf-plan ruling, item 3); CI's test job runs them on one shard (ci.yml
step 7c, ``balance:R-BAL327``), and by hand they run as ``pytest tools/rehearsal -c /dev/null
-m cluster``, on the rootless daemon ``scripts/private_cluster.py`` chooses.  The tests run
with ``-c /dev/null``, so no ini file registers the marker: this hook does.

The ``dump`` fixture is the planted archive those tests restore.  The ``planted`` bench, the
``clock`` reader and the ``moved_rows`` serve the row grader's cases and the controls' alike
(:mod:`.test_rowdiff`, :mod:`.test_rows`).
"""
from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from tools.ci import arcs
from tools.rehearsal import bench
from tools.rehearsal.rowdiff import Moved


def pytest_configure(config: pytest.Config) -> None:
    """Register ``cluster``, so pytest does not warn of it as an unknown marker."""
    config.addinivalue_line(
        "markers", "cluster: starts a real private cluster (scripts/private_cluster.py)")


@pytest.fixture(name="dump", scope="module")
def _dump(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A ``pg_dump -Fc`` archive of a planted database, begun as production's volume begins
    (``scripts/init_db.sql``: the app's schemas and an empty ``alembic_version``, which the
    deploy step's first build needs) and holding two tables: two rows of money, and one row
    whose stamp the server's clock wrote before any run reads its line (a dump's stamp, which
    the rows compare exactly)."""
    path = tmp_path_factory.mktemp("dump") / "planted.dump"
    token = bench.cluster("token").strip()
    try:
        socket_dir, container = bench.cluster(
            "start", "--", f"shekel-rehearsal-test-{os.getpid()}-{token[:8]}", token,
            bench.image_tag()).splitlines()
        with psycopg.connect(bench.cluster("url", socket_dir, "postgres").strip(),
                             autocommit=True) as connection:
            connection.execute("CREATE DATABASE planted")
        planted = bench.cluster("url", socket_dir, "planted").strip()
        with psycopg.connect(planted, autocommit=True) as connection:
            connection.execute((arcs.REPO / "scripts" / "init_db.sql").read_text(
                encoding="utf-8"))
            connection.execute(
                "CREATE TABLE ledger (id integer PRIMARY KEY, amount numeric(12, 2))")
            connection.execute("INSERT INTO ledger VALUES (1, 12.34), (2, 0.01)")
            connection.execute("CREATE TABLE stamped (id integer PRIMARY KEY, "
                               "created_at timestamptz NOT NULL DEFAULT now())")
            connection.execute("INSERT INTO stamped (id) VALUES (1)")
        with path.open("wb") as archive:
            dumped = bench.in_cluster(container, ["pg_dump", "--format=custom", "--dbname",
                                                  "planted"], stdout=archive)
        assert dumped.returncode == 0, dumped.stderr
    finally:
        bench.cluster("stop", token)
    yield path


_PLANTED_SCHEMA = """
CREATE TABLE system.audit_log (
    id bigserial PRIMARY KEY, table_schema varchar(50) NOT NULL, table_name varchar(100) NOT NULL,
    operation varchar(10) NOT NULL, row_id integer, old_data jsonb, new_data jsonb,
    changed_fields text[], user_id integer, db_user varchar(100) DEFAULT current_user,
    executed_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE note (id integer, part integer, body varchar(4) NOT NULL,
                   created_at timestamptz DEFAULT clock_timestamp(), PRIMARY KEY (id, part));
CREATE TABLE tally (word text);
CREATE TABLE timing (id integer PRIMARY KEY, due timestamptz DEFAULT now() + interval '1 day',
                     at_local timestamp DEFAULT LOCALTIMESTAMP(3),
                     at_now timestamptz DEFAULT CURRENT_TIMESTAMP,
                     on_day date DEFAULT CURRENT_DATE);
INSERT INTO note (id, part, body) VALUES (1, 1, 'abc'), (1, 2, 'de'), (2, 1, 'f');
INSERT INTO tally VALUES ('x'), ('x'), ('y');
INSERT INTO system.audit_log (table_schema, table_name, operation, row_id, new_data)
    VALUES ('public', 'note', 'INSERT', 1, '{"id": 1, "created_at": "2000-01-01T00:00:00+00:00"}');
CREATE FUNCTION audit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO system.audit_log (table_schema, table_name, operation, row_id)
        VALUES (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, 0);
    RETURN NULL;
END $$;
CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON ledger
    FOR EACH ROW EXECUTE FUNCTION audit();
CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON note
    FOR EACH ROW EXECUTE FUNCTION audit();
"""


@pytest.fixture(name="planted")
def _planted(dump: Path) -> Iterator[bench.Bench]:
    """A bench whose base copy holds the dump (its schemas, its ledger, its stamped row) and
    the planted tables: a composite key with a stamp, a multiset, a table of clock defaults,
    and an audit log a trigger writes."""
    made = bench.Bench(dump, {"base": "HEAD"})
    with made:
        with psycopg.connect(made.made["base"].url, autocommit=True) as connection:
            connection.execute(_PLANTED_SCHEMA)
        yield made


def _clock(url: str) -> datetime:
    """The cluster's clock, read on the copy at ``url``."""
    with psycopg.connect(url) as connection:
        (now,) = connection.execute("SELECT clock_timestamp()").fetchone()
    return now


@pytest.fixture(name="clock")
def _clock_reader() -> Callable[[str], datetime]:
    """The cluster's clock, read on a copy (:func:`_clock`)."""
    return _clock


_CHANGED = Moved("budget.accounts", "[3]", {"id": 3, "balance": Decimal("10.00"), "name": "A",
                                            "active": True},
                 {"id": 3, "balance": Decimal("12.50"), "name": "B", "active": False})
_MOVED = [
    _CHANGED,
    Moved("budget.accounts", "[4]", {"id": 4, "balance": Decimal("1.00"), "name": "C",
                                     "active": True},
          {"id": 4, "balance": Decimal("0.75"), "name": "C", "active": True}),
    Moved("budget.accounts", "[5]", {"id": 5, "balance": None, "name": "D", "active": True},
          {"id": 5, "balance": Decimal("2.00"), "name": "D", "active": True}),
    Moved("budget.accounts", "[6]", {"id": 6}, None),
    Moved("system.audit_log", "", None, ["budget", "accounts", "UPDATE", 3]),
]


@pytest.fixture(name="moved_rows")
def _moved_rows() -> list[Moved]:
    """Five rows that differ: a row changed in three columns, a money change, a null that
    became money, a row on one side only, and an audit event (never a table's row)."""
    return _MOVED
