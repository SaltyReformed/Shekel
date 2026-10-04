"""``salary.salary_profiles.stub_gross_includes_after_tax`` -- plan step **salary:S11-c-2b**.

Ruling **R-SAL102** ("A yes/no on each job", finding SAL-592): one stored
yes/no per salary job, ``false`` the answer every job starts with.  Migration
``1f431fec6547`` adds it NOT NULL with a server default and no backfill, on
the argument that ``false`` is every existing row's true value.

* :class:`TestWhatEachDirectionCalls` -- the two directions driven with a
  recording ``op``: one ADD COLUMN (NOT NULL, default false) and no raw SQL,
  which is where a backfill would arrive; one DROP COLUMN back.
* :class:`TestTheRoundTrip` -- both directions RUN on this test's own
  database: a downgrade drops the column and keeps the row, and the upgrade
  gives the existing row ``false`` -- so a "yes" does not survive a
  downgrade, as the migration's docstring says.
* :class:`TestTheTable` -- the migrated table matches the model, defaults
  included, and the revision sits on the head it was written against.
"""

import pytest
import sqlalchemy
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from app.extensions import db
from tests._test_helpers import (
    load_migration_module,
    make_salary_profile,
    run_migration_callable,
)

_MIGRATION = "1f431fec6547_a_job_says_what_its_stub_s_gross_includes.py"
_COLUMN = "stub_gross_includes_after_tax"


def _column():
    """``(is_nullable, column_default)`` of the column, or ``None`` when it is absent."""
    row = db.session.execute(sqlalchemy.text(
        "SELECT is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = 'salary' AND table_name = 'salary_profiles' "
        "AND column_name = :column"
    ), {"column": _COLUMN}).one_or_none()
    return None if row is None else (row.is_nullable, row.column_default)


class TestWhatEachDirectionCalls:
    """Each direction CALLED with ``op`` replaced by a recorder."""

    class _Recorder:
        """Stand-in for ``alembic.op`` recording calls instead of running them."""

        def __init__(self):
            """Start with an empty call log."""
            self.calls: "list[tuple]" = []

        def add_column(self, table, column, schema=None):
            """Record one ADD COLUMN, its nullability and its server default's text."""
            self.calls.append((
                "add_column", schema, table, column.name, column.nullable,
                str(column.server_default.arg),
            ))

        def drop_column(self, table, column, schema=None):
            """Record one DROP COLUMN."""
            self.calls.append(("drop_column", schema, table, column))

        def execute(self, statement):
            """Record any raw SQL -- there should be none."""
            self.calls.append(("execute", statement))

    def _drive(self, monkeypatch, direction):
        """Call *direction* on the migration with a recording ``op``."""
        module = load_migration_module(_MIGRATION)
        recorder = self._Recorder()
        monkeypatch.setattr(module, "op", recorder)
        getattr(module, direction)()
        return recorder.calls

    def test_upgrade_adds_one_not_null_column_defaulting_to_false(self, monkeypatch):
        """One ADD COLUMN and nothing else: no ``execute``, so no backfill."""
        assert self._drive(monkeypatch, "upgrade") == [
            ("add_column", "salary", "salary_profiles", _COLUMN, False, "false"),
        ]

    def test_downgrade_drops_the_column_and_nothing_else(self, monkeypatch):
        """One DROP COLUMN back."""
        assert self._drive(monkeypatch, "downgrade") == [
            ("drop_column", "salary", "salary_profiles", _COLUMN),
        ]


@pytest.mark.usefixtures("db")
class TestTheRoundTrip:
    """Both directions run on this test's own database (the ``db`` fixture's clone)."""

    def test_down_drops_the_answer_and_up_restores_false(self, app, seed_user):
        """A "yes" job survives as a row; the column goes, then returns as "no"."""
        with app.app_context():
            profile = make_salary_profile(seed_user, db.session, name="Yes Job")
            profile.stub_gross_includes_after_tax = True
            db.session.commit()
            profile_id = profile.id
            migration = load_migration_module(_MIGRATION)

            run_migration_callable(migration.downgrade, db.session)
            assert _column() is None
            assert db.session.execute(sqlalchemy.text(
                "SELECT count(*) FROM salary.salary_profiles WHERE id = :id"
            ), {"id": profile_id}).scalar() == 1

            run_migration_callable(migration.upgrade, db.session)
            assert _column() == ("NO", "false")
            assert db.session.execute(sqlalchemy.text(
                f"SELECT {_COLUMN} FROM salary.salary_profiles WHERE id = :id"
            ), {"id": profile_id}).scalar() is False


class TestTheTable:
    """The migrated table and the model agree; the revision sits where it was written."""

    def test_autogenerate_sees_no_drift_on_the_profile_table(self, app):
        """The migrated ``salary.salary_profiles`` matches the model, defaults included."""
        with app.app_context():
            ctx = MigrationContext.configure(
                connection=db.session.connection(),
                opts={
                    "compare_type": True,
                    "compare_server_default": True,
                    "include_schemas": True,
                    "include_object": lambda obj, name, type_, *_: (
                        type_ != "table" or (obj.schema, name) == ("salary", "salary_profiles")
                    ),
                },
            )
            assert compare_metadata(ctx, db.metadata) == []

    def test_the_live_column_is_not_null_defaulting_to_false(self, app):
        """Asked of PostgreSQL, not the model: an INSERT that omits it stores ``false``."""
        with app.app_context():
            assert _column() == ("NO", "false")

    def test_revision_and_down_revision(self):
        """The revision hangs off the head it was written against (d3b8f5a1c7e2)."""
        migration = load_migration_module(_MIGRATION)
        assert migration.revision == "1f431fec6547"
        assert migration.down_revision == "d3b8f5a1c7e2"
