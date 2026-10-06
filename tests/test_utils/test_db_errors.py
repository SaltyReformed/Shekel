"""Tests for ``app.utils.db_errors`` -- reading WHICH refusal the database reported.

Plan step balance:X-dj.  The module answers by SQLSTATE rather than by
exception class, because the class is the driver's choice and moved when the
project changed drivers.  Every case that asserts a code provokes a REAL
refusal from the test database, so the codes are the server's; the one forged
error is the case with no code to read at all.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.utils.db_errors import (
    UNDEFINED_COLUMN,
    UNDEFINED_TABLE,
    is_schema_behind_code,
    sqlstate_of,
)
from tests._test_helpers import RAISE_EXCEPTION_SQLSTATE, refused_by_database_rule


def _refusal_of(db, statement):
    """Run *statement*, which must be refused, and return SQLAlchemy's error.

    Args:
        db: The Flask-SQLAlchemy extension (the ``db`` fixture).
        statement: SQL the test database refuses.

    Returns:
        The :class:`~sqlalchemy.exc.ProgrammingError` it raised, after the
        session has been rolled back so the test can go on using it.
    """
    with pytest.raises(ProgrammingError) as excinfo:
        db.session.execute(text(statement))
    db.session.rollback()
    return excinfo.value


class TestASchemaBehindTheCodeIsReadOffTheCode:
    """``is_schema_behind_code`` answers yes to 42P01 and 42703, and to nothing else."""

    def test_a_missing_table_is_a_schema_behind(self, db):
        """A pending migration that creates a table."""
        refusal = _refusal_of(db, "SELECT 1 FROM ref.no_such_table_x_dj")
        assert sqlstate_of(refusal) == UNDEFINED_TABLE
        assert is_schema_behind_code(refusal) is True

    def test_a_missing_column_is_a_schema_behind(self, db):
        """A pending migration that adds a column to a table that exists."""
        refusal = _refusal_of(db, "SELECT no_such_column_x_dj FROM ref.account_types")
        assert sqlstate_of(refusal) == UNDEFINED_COLUMN
        assert is_schema_behind_code(refusal) is True

    def test_a_trigger_refusal_shares_the_class_and_is_not_a_missing_table(self, db):
        """The case the narrowing exists for.

        Under psycopg 3 a PL/pgSQL ``RAISE`` arrives as the SAME
        ``ProgrammingError`` class a missing table does -- asserted here by
        the ``pytest.raises`` in :func:`_refusal_of` -- so only the code tells
        them apart.
        """
        refusal = _refusal_of(
            db, "DO $$ BEGIN RAISE EXCEPTION 'refused by a rule'; END $$",
        )
        assert sqlstate_of(refusal) == RAISE_EXCEPTION_SQLSTATE
        assert is_schema_behind_code(refusal) is False

    def test_a_malformed_statement_is_not_a_missing_table(self, db):
        """``syntax_error`` (42601) is a ``ProgrammingError`` under both drivers."""
        refusal = _refusal_of(db, "SELEC 1")
        assert sqlstate_of(refusal) == "42601"
        assert is_schema_behind_code(refusal) is False

    def test_an_error_that_carries_no_driver_diagnostic_is_not_one(self):
        """No code to read is not read as a schema behind its code."""
        forged = ProgrammingError("SELECT 1", {}, Exception("no diagnostic"))
        assert sqlstate_of(forged) is None
        assert is_schema_behind_code(forged) is False


class TestTheTwoBootstrapTolerancesReadTheCode:
    """Both consumers absorb a schema behind the code and let every other refusal through.

    ``app._seed_ref_tables`` and ``app.ref_cache._state._load_rows`` each
    caught ``ProgrammingError`` whole until plan step balance:X-dj, which under
    psycopg 3 also holds a trigger's ``RAISE``.  Each case below makes the
    consumer meet a REAL refusal of the named kind.
    """

    @staticmethod
    def _seed_that_runs(statement):
        """Return a stand-in for ``seed_reference_data`` that runs *statement*.

        Args:
            statement: SQL the test database refuses.

        Returns:
            A one-argument callable taking the session, as the real seed does.
        """
        def _seed(session):
            """Run the refused statement where the seed would have written."""
            session.execute(text(statement))
        return _seed

    def test_the_seed_skips_a_missing_table(self, app, db, monkeypatch):
        """42P01 is the boot-time race the seed tolerance exists for."""
        # pylint: disable=import-outside-toplevel
        import app as app_package
        from app import ref_seeds

        monkeypatch.setattr(
            ref_seeds, "seed_reference_data",
            self._seed_that_runs("SELECT 1 FROM ref.no_such_table_x_dj"),
        )
        with app.app_context():
            app_package._seed_ref_tables()  # pylint: disable=protected-access

    def test_the_seed_lets_a_malformed_statement_through(self, app, db, monkeypatch):
        """Anything but a schema behind its code is a real failure, and now surfaces."""
        # pylint: disable=import-outside-toplevel
        import app as app_package
        from app import ref_seeds

        monkeypatch.setattr(
            ref_seeds, "seed_reference_data", self._seed_that_runs("SELEC 1"),
        )
        with app.app_context():
            with pytest.raises(ProgrammingError) as excinfo:
                app_package._seed_ref_tables()  # pylint: disable=protected-access
        assert sqlstate_of(excinfo.value) == "42601"

    def test_the_ref_cache_lets_a_refused_query_through(self, app, db, monkeypatch):
        """``ref_cache.init`` records a MISSING table; it does not swallow a refusal.

        The ``loan_anchor_sources`` read is made to meet a trigger-style
        ``RAISE`` -- the class the narrowing exists to keep out of the
        tolerance -- and ``init`` must raise rather than report the table
        unavailable.  The cache is rebuilt whole afterwards so later tests are
        unaffected.
        """
        # pylint: disable=import-outside-toplevel
        from app import ref_cache

        with app.app_context():
            real_query = type(db.session).query

            def fake_query(session, model):
                """Refuse the one ref read; serve every other one."""
                if model.__name__ == "LoanAnchorSource":
                    session.execute(text(
                        "DO $$ BEGIN RAISE EXCEPTION 'refused by a rule'; END $$"
                    ))
                return real_query(session, model)

            monkeypatch.setattr(type(db.session), "query", fake_query)
            try:
                with pytest.raises(ProgrammingError) as excinfo:
                    ref_cache.init(db.session)
                assert sqlstate_of(excinfo.value) == RAISE_EXCEPTION_SQLSTATE
            finally:
                monkeypatch.undo()
                db.session.rollback()
                ref_cache.init(db.session)

    def test_the_seed_tolerates_a_column_a_pending_migration_adds(self, app, db):
        """The development entrypoint's case: a model names a column the schema lacks.

        ``scripts/init_database.py`` builds the app -- and so runs this seed
        -- BEFORE it migrates, and ``flask db upgrade`` does the same; a
        pending migration that adds a column to a ref table leaves the seed
        meeting 42703.  A tolerance of 42P01 alone made this a crash (the
        adversarial review of this step measured it on
        ``has_revolving_credit``).  The seed's own rollback undoes the DROP.
        """
        # pylint: disable=import-outside-toplevel
        import app as app_package

        with app.app_context():
            db.session.execute(text(
                "ALTER TABLE ref.account_types DROP COLUMN has_revolving_credit"
            ))
            app_package._seed_ref_tables()  # pylint: disable=protected-access

    def test_the_ref_cache_records_a_table_behind_its_model_unavailable(self, app, db):
        """The same column gone: the cache reports the table, and does not raise."""
        # pylint: disable=import-outside-toplevel
        from app import ref_cache

        with app.app_context():
            try:
                db.session.execute(text(
                    "ALTER TABLE ref.account_types DROP COLUMN has_revolving_credit"
                ))
                assert "account_types" in ref_cache.init(db.session)
            finally:
                db.session.rollback()
                ref_cache.init(db.session)


class TestTheSuitesRefusalHelperReadsTheCode:
    """``refused_by_database_rule`` grades the CODE as well as the message.

    Ruling balance:R-BAL210: the suite's refusal assertions check
    PostgreSQL's SQLSTATE, because the message alone cannot tell a trigger's
    ``RAISE`` from any other refusal that happens to say the same words.  The
    helper's message arm is ``pytest.raises``'; its code arm is the one this
    project wrote, so it is graded here with a REAL refusal on each side.
    """

    def test_a_rules_refusal_satisfies_it(self, db):
        """``RAISE EXCEPTION`` with no ``ERRCODE`` is P0001: a rule's refusal."""
        with refused_by_database_rule("refused by a rule"):
            db.session.execute(text(
                "DO $$ BEGIN RAISE EXCEPTION 'refused by a rule'; END $$"
            ))
        db.session.rollback()

    def test_a_matching_message_under_another_code_fails_it(self, db):
        """The same words under P0002 (``no_data_found``) are not a rule's refusal.

        Same driver class, same message -- only the code differs, so only
        the code arm can fail it.
        """
        with pytest.raises(AssertionError, match="not a database rule's"):
            with refused_by_database_rule("refused by a rule"):
                db.session.execute(text(
                    "DO $$ BEGIN RAISE EXCEPTION USING ERRCODE = 'P0002', "
                    "MESSAGE = 'refused by a rule'; END $$"
                ))
        db.session.rollback()
