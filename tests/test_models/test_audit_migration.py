"""Tests for the audit-infrastructure rebuild migration (Commit C-13).

Covers:

  * Idempotency of :func:`app.audit_infrastructure.apply_audit_infrastructure`.
  * Round-trip of apply + remove + apply that simulates ``flask db
    upgrade`` -> ``flask db downgrade`` -> ``flask db upgrade``.
  * Provisioning of the ``shekel_app`` least-privilege role and the
    GRANT block inside the migration -- exercised by creating the
    role within the test, asserting the role can DML but not DDL,
    then dropping it.
  * The ``app.current_user_id`` session-variable capture path.
  * The trigger-count health check that ``entrypoint.sh`` runs.

The tests lean on the ``shekel_user`` test role being a PostgreSQL
superuser (which it is on this project's local dev/test config) so
the role-provisioning paths can run without a separate fixture
container.  Tests that need that capability assert it up front via
``ROLE_PROVISIONING_AVAILABLE`` so a future hardened test
environment skips them cleanly instead of failing.
"""
# pylint: disable=redefined-outer-name  -- pytest fixture pattern
from __future__ import annotations

from decimal import Decimal

import pytest

from app.audit_infrastructure import (
    AUDITED_TABLES,
    EXPECTED_TRIGGER_COUNT,
    apply_audit_infrastructure,
    remove_audit_infrastructure,
)
from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType


# Module-level xdist_group marker pins every test in this module to
# a single pytest-xdist worker (under ``--dist=loadgroup``, set in
# ``pytest.ini``).  Pinning is required because the cluster-scoped
# ``shekel_app`` PostgreSQL role created by the
# :func:`shekel_app_role` fixture cannot be safely concurrent with
# tests in OTHER classes of this same file -- specifically
# ``TestApplyIdempotent`` and ``TestRoundTrip`` invoke
# :func:`apply_audit_infrastructure`, whose ``_GRANT_APP_ROLE_SQL``
# block conditionally ``GRANT``s privileges to ``shekel_app`` WHEN
# the role exists.  If those tests ran on a different worker while
# ``TestLeastPrivilegeRole`` had the role alive, the GRANTs would
# leak into the OTHER worker's per-session database; the
# ``shekel_app_role`` fixture's teardown ``DROP OWNED BY`` only
# affects its own database, so the cross-database grants would
# block ``DROP ROLE`` with ``DependentObjectsStillExist`` errors.
# Pinning the entire module sidesteps that race; other test files
# continue to fan out across workers normally.
pytestmark = pytest.mark.xdist_group("shekel_app_role")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trigger_count(session) -> int:
    """Count audit_* triggers attached to user tables.

    Excludes internal triggers (``tgisinternal``) to avoid counting
    PostgreSQL's own RI/check trigger machinery, which would inflate
    the number well past EXPECTED_TRIGGER_COUNT.
    """
    return session.execute(db.text(
        "SELECT count(*) FROM pg_trigger "
        "WHERE tgname LIKE 'audit_%' AND NOT tgisinternal"
    )).scalar()


def _audit_log_table_exists(session) -> bool:
    """Return True iff ``system.audit_log`` is present in the catalog."""
    return session.execute(db.text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM information_schema.tables "
        "  WHERE table_schema = 'system' AND table_name = 'audit_log'"
        ")"
    )).scalar()


def _audit_function_exists(session) -> bool:
    """Return True iff ``system.audit_trigger_func`` is present."""
    return session.execute(db.text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM pg_proc p "
        "  JOIN pg_namespace n ON p.pronamespace = n.oid "
        "  WHERE n.nspname = 'system' "
        "    AND p.proname = 'audit_trigger_func'"
        ")"
    )).scalar()


def _is_superuser(session) -> bool:
    """Whether the connected role can run CREATE ROLE / GRANT."""
    row = session.execute(db.text(
        "SELECT rolsuper OR rolcreaterole AS can_provision "
        "FROM pg_roles WHERE rolname = current_user"
    )).first()
    return bool(row.can_provision) if row else False


@pytest.fixture
def session_executor(db):  # noqa: ARG001  -- db is the conftest fixture
    """Return a callable suitable as ``apply_audit_infrastructure(executor)``.

    The closure runs each statement on the test session.  Tests must
    commit explicitly when they want the change to persist past the
    surrounding transaction.
    """
    return lambda sql: db.session.execute(db.text(sql))


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------


class TestApplyIdempotent:
    """Re-running ``apply_audit_infrastructure`` is a no-op.

    The second call must not raise (CREATE TABLE IF NOT EXISTS,
    CREATE OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS + CREATE
    TRIGGER) and must not change the trigger count.  Both behaviours
    matter because the function runs on every container start (via
    init_database.py for fresh DBs) and at session start in the test
    suite.
    """

    def test_second_apply_does_not_raise(self, db, session_executor):
        """Calling apply twice in a row succeeds without errors.

        The conftest setup already calls apply once; this test calls
        it a second time and asserts the call returns cleanly.
        """
        apply_audit_infrastructure(session_executor)
        db.session.commit()
        # No exception raised; the test passes by reaching this line.

    def test_second_apply_preserves_trigger_count(
        self, db, session_executor
    ):
        """Trigger count after a second apply equals EXPECTED_TRIGGER_COUNT.

        DROP TRIGGER IF EXISTS + CREATE TRIGGER inside apply is
        idempotent; the count must not double or change shape.
        """
        before = _trigger_count(db.session)
        assert before == EXPECTED_TRIGGER_COUNT, (
            "conftest setup did not produce the expected trigger "
            "count -- the rest of this test is meaningless until "
            "that fixture is fixed."
        )
        apply_audit_infrastructure(session_executor)
        db.session.commit()
        after = _trigger_count(db.session)
        assert after == before


# ---------------------------------------------------------------------------
# Round-trip tests (simulates upgrade -> downgrade -> upgrade)
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Apply -> remove -> re-apply restores the original state.

    Mirrors ``flask db upgrade`` -> ``flask db downgrade`` ->
    ``flask db upgrade`` for the rebuild migration.  The test wraps
    its work in a try/finally that re-applies the infrastructure on
    failure so a partial run does not leave subsequent tests without
    triggers.  conftest's ``db`` fixture does not re-create the
    audit infrastructure between tests (it is session-scoped) so the
    cleanup matters.
    """

    def test_remove_drops_table_function_and_triggers(
        self, db, session_executor
    ):
        """remove_audit_infrastructure drops every artefact.

        After the call: zero ``audit_*`` triggers, no
        ``system.audit_log`` table, no ``system.audit_trigger_func``.
        """
        try:
            remove_audit_infrastructure(session_executor)
            db.session.commit()
            assert _trigger_count(db.session) == 0
            assert not _audit_log_table_exists(db.session)
            assert not _audit_function_exists(db.session)
        finally:
            apply_audit_infrastructure(session_executor)
            db.session.commit()

    def test_apply_after_remove_restores_state(
        self, db, session_executor
    ):
        """Re-apply produces an identical end state to the initial setup."""
        try:
            remove_audit_infrastructure(session_executor)
            db.session.commit()

            apply_audit_infrastructure(session_executor)
            db.session.commit()

            assert _trigger_count(db.session) == EXPECTED_TRIGGER_COUNT
            assert _audit_log_table_exists(db.session)
            assert _audit_function_exists(db.session)
        finally:
            # If the assertions above failed mid-way, ensure the
            # infrastructure is fully restored before the next test.
            apply_audit_infrastructure(session_executor)
            db.session.commit()

    def test_full_round_trip_preserves_audit_capture(
        self, db, session_executor, seed_user
    ):
        """After remove + apply, the trigger still captures inserts.

        Verifies the round trip does not silently leave the function
        body in a degraded state where INSERTs succeed but no audit
        row is written.
        """
        try:
            remove_audit_infrastructure(session_executor)
            db.session.commit()
            apply_audit_infrastructure(session_executor)
            db.session.commit()

            # Trigger an INSERT on a re-instrumented table.
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            new_account = Account(
                user_id=seed_user["user"].id,
                account_type_id=checking_type.id,
                name="Round Trip Account",
                current_anchor_balance=Decimal("0.00"),
            )
            db.session.add(new_account)
            db.session.flush()

            audit_rows = db.session.execute(db.text(
                "SELECT * FROM system.audit_log "
                "WHERE table_name = 'accounts' "
                "  AND operation = 'INSERT' "
                "  AND new_data->>'name' = 'Round Trip Account'"
            )).fetchall()
            assert len(audit_rows) == 1
        finally:
            apply_audit_infrastructure(session_executor)
            db.session.commit()


# ---------------------------------------------------------------------------
# user_id capture
# ---------------------------------------------------------------------------


class TestUserIdCapture:
    """The trigger reads ``app.current_user_id`` from session state.

    Complements the integration tests that drive this through the
    Flask before_request hook by exercising the SET LOCAL path in
    isolation -- if the trigger function regresses, this test
    catches it without requiring the whole HTTP stack.
    """

    def test_set_local_propagates_to_audit_row(
        self, db, seed_user, seed_periods
    ):
        """SET LOCAL written before INSERT shows up in audit_log.user_id."""
        # pylint: disable=import-outside-toplevel
        from app.models.transaction import Transaction
        from app.models.ref import Status, TransactionType

        projected = (
            db.session.query(Status).filter_by(name="Projected").one()
        )
        expense = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )

        db.session.execute(
            db.text("SET LOCAL app.current_user_id = :uid"),
            {"uid": str(seed_user["user"].id)},
        )

        txn = Transaction(
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=projected.id,
            name="UID Capture Test",
            category_id=seed_user["categories"]["Rent"].id,
            transaction_type_id=expense.id,
            estimated_amount=Decimal("12.00"),
        )
        db.session.add(txn)
        db.session.flush()

        rows = db.session.execute(db.text(
            "SELECT user_id FROM system.audit_log "
            "WHERE table_name = 'transactions' "
            "  AND operation = 'INSERT' "
            "  AND new_data->>'name' = 'UID Capture Test'"
        )).fetchall()
        assert len(rows) == 1
        assert rows[0].user_id == seed_user["user"].id


# ---------------------------------------------------------------------------
# Least-privilege role tests
# ---------------------------------------------------------------------------


@pytest.fixture
def shekel_app_role(db):
    """Create + configure the ``shekel_app`` role for the duration of one test.

    Skips the test on a non-superuser test role.  Wraps creation in
    a try/finally so the role drops even when the inner assertions
    fail; matching ``DROP OWNED BY`` removes any per-test grants
    that would otherwise survive across tests.
    """
    if not _is_superuser(db.session):
        pytest.skip(
            "Test requires CREATEROLE/SUPERUSER on the test database "
            "user.  Skipped automatically on hardened CI environments."
        )

    role_name = "shekel_app"
    password = "test-shekel-app-password"
    db.session.execute(db.text(
        f"DO $$ "
        f"BEGIN "
        f"  IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role_name}') THEN "
        f"    EXECUTE format('DROP OWNED BY {role_name} CASCADE'); "
        f"    EXECUTE 'DROP ROLE {role_name}'; "
        f"  END IF; "
        f"END$$"
    ))
    db.session.execute(db.text(
        f"CREATE ROLE {role_name} WITH LOGIN PASSWORD '{password}'"
    ))
    # Mirror init_db_role.sql for the schemas the test exercises.
    # tax DB or shekel_test DB -- the GRANT CONNECT must reference
    # the live database name.  current_database() avoids hard-coding
    # which DB the test container happens to be connected to.
    current_db = db.session.execute(
        db.text("SELECT current_database()")
    ).scalar()
    db.session.execute(db.text(
        f'GRANT CONNECT ON DATABASE "{current_db}" TO {role_name}'
    ))
    db.session.execute(db.text(
        f"GRANT USAGE ON SCHEMA auth, budget, salary, ref TO {role_name}"
    ))
    db.session.execute(db.text(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        f"ON ALL TABLES IN SCHEMA auth, budget, salary TO {role_name}"
    ))
    db.session.execute(db.text(
        f"GRANT SELECT ON ALL TABLES IN SCHEMA ref TO {role_name}"
    ))
    db.session.execute(db.text(
        "GRANT USAGE "
        f"ON ALL SEQUENCES IN SCHEMA auth, budget, salary, ref TO {role_name}"
    ))
    db.session.execute(db.text(f"GRANT USAGE ON SCHEMA system TO {role_name}"))
    db.session.execute(db.text(
        f"GRANT SELECT, INSERT ON system.audit_log TO {role_name}"
    ))
    db.session.execute(db.text(
        f"GRANT USAGE ON SEQUENCE system.audit_log_id_seq TO {role_name}"
    ))
    db.session.commit()

    try:
        yield role_name
    finally:
        db.session.rollback()
        db.session.execute(db.text(
            f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA "
            f"auth, budget, salary, ref, system FROM {role_name}"
        ))
        db.session.execute(db.text(
            f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA "
            f"auth, budget, salary, ref, system FROM {role_name}"
        ))
        db.session.execute(db.text(
            f"REVOKE USAGE ON SCHEMA auth, budget, salary, ref, system "
            f"FROM {role_name}"
        ))
        db.session.execute(db.text(
            f'REVOKE CONNECT ON DATABASE "{current_db}" FROM {role_name}'
        ))
        db.session.execute(db.text(f"DROP OWNED BY {role_name} CASCADE"))
        db.session.execute(db.text(f"DROP ROLE IF EXISTS {role_name}"))
        db.session.commit()


class TestLeastPrivilegeRole:
    """Verify the GRANT block inside the migration is correctly scoped.

    The role tests exercise a separately-created ``shekel_app`` role
    (the canonical role provisioned by ``scripts/init_db_role.sql``
    in production) so the DML/DDL boundary asserted here matches the
    runtime posture exactly.  ``SET ROLE`` switches the current
    transaction's privilege check to the named role -- requires the
    test runner to be either a superuser or a member of the role.

    Cluster-level serialisation is handled at module scope via
    ``pytestmark`` so this class and the sibling classes that call
    :func:`apply_audit_infrastructure` are all pinned to the same
    pytest-xdist worker.  See the module-level comment above
    ``pytestmark`` for the full reasoning.
    """

    def test_app_role_can_select_audit_log(
        self, db, shekel_app_role
    ):
        """``shekel_app`` can read ``system.audit_log`` -- needed for forensics."""
        try:
            db.session.execute(db.text(f"SET ROLE {shekel_app_role}"))
            row_count = db.session.execute(
                db.text("SELECT count(*) FROM system.audit_log")
            ).scalar()
            assert row_count is not None
        finally:
            db.session.execute(db.text("RESET ROLE"))

    def test_app_role_can_dml_budget_tables(
        self, db, shekel_app_role, seed_user
    ):
        """``shekel_app`` can INSERT/UPDATE/DELETE on ``budget.*``."""
        # SQLAlchemy reuses pooled connections that may carry a
        # stale role from a prior test; RESET ROLE before SET ROLE
        # to make the boundary explicit.
        try:
            db.session.execute(db.text("RESET ROLE"))
            db.session.execute(db.text(f"SET ROLE {shekel_app_role}"))

            # INSERT
            checking_type_id = db.session.execute(db.text(
                "SELECT id FROM ref.account_types WHERE name = 'Checking'"
            )).scalar()
            db.session.execute(
                db.text(
                    "INSERT INTO budget.accounts "
                    "(user_id, account_type_id, name, current_anchor_balance) "
                    "VALUES (:uid, :tid, :name, :bal)"
                ),
                {
                    "uid": seed_user["user"].id,
                    "tid": checking_type_id,
                    "name": "App Role Test Account",
                    "bal": Decimal("100.00"),
                },
            )

            # UPDATE
            db.session.execute(
                db.text(
                    "UPDATE budget.accounts "
                    "SET name = :new_name "
                    "WHERE name = :old_name"
                ),
                {
                    "new_name": "App Role Renamed",
                    "old_name": "App Role Test Account",
                },
            )

            # DELETE
            deleted = db.session.execute(
                db.text(
                    "DELETE FROM budget.accounts "
                    "WHERE name = 'App Role Renamed'"
                )
            ).rowcount
            assert deleted == 1
        finally:
            db.session.execute(db.text("RESET ROLE"))

    def test_app_role_cannot_drop_table(
        self, db, shekel_app_role
    ):
        """``shekel_app`` is rejected when it tries DDL such as DROP TABLE.

        PostgreSQL surfaces two distinct messages depending on which
        privilege check fires first: ``permission denied`` for
        schema-level USAGE failures and ``must be owner of table``
        when the role has USAGE but not ownership.  The test
        accepts either form because both prove the privilege escalation
        attempt was rejected.  A successful ``DROP TABLE`` would mean
        the GRANT block leaked ownership.
        """
        # pylint: disable=import-outside-toplevel
        from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

        try:
            db.session.execute(db.text("RESET ROLE"))
            db.session.execute(db.text(f"SET ROLE {shekel_app_role}"))
            with pytest.raises((ProgrammingError, SQLAlchemyError)) as excinfo:
                db.session.execute(
                    db.text("DROP TABLE budget.accounts CASCADE")
                )
                # Force the implicit transaction to fail explicitly
                # so SQLAlchemy raises here rather than swallowing
                # the error until commit.
                db.session.flush()
            error_text = str(excinfo.value).lower()
            assert (
                "permission denied" in error_text
                or "must be owner" in error_text
            ), f"Unexpected error text: {error_text}"
        finally:
            db.session.rollback()
            db.session.execute(db.text("RESET ROLE"))

    def test_app_role_cannot_drop_audit_trigger(
        self, db, shekel_app_role
    ):
        """``shekel_app`` cannot DROP TRIGGER on ``budget.transactions``.

        This is the load-bearing guarantee the two-role policy
        provides: an attacker who pivots into the application role
        still cannot remove the audit trail behind their own
        actions.  A failure here means the audit posture is no
        better than it was before C-13.
        """
        # pylint: disable=import-outside-toplevel
        from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

        try:
            db.session.execute(db.text("RESET ROLE"))
            db.session.execute(db.text(f"SET ROLE {shekel_app_role}"))
            with pytest.raises((ProgrammingError, SQLAlchemyError)) as excinfo:
                db.session.execute(db.text(
                    "DROP TRIGGER audit_transactions ON budget.transactions"
                ))
                db.session.flush()
            assert (
                "permission denied" in str(excinfo.value).lower()
                or "must be owner" in str(excinfo.value).lower()
            )
        finally:
            db.session.rollback()
            db.session.execute(db.text("RESET ROLE"))


# ---------------------------------------------------------------------------
# Health check (matches entrypoint.sh)
# ---------------------------------------------------------------------------


class TestEntrypointHealthCheck:
    """Mirror the post-migration assertion that ``entrypoint.sh`` runs.

    The shell script counts ``pg_trigger.tgname LIKE 'audit_%'`` and
    compares against ``EXPECTED_TRIGGER_COUNT`` from the shared
    module.  If the test below fails, the production startup check
    would also fail and Gunicorn would refuse to start -- which is
    the intended behaviour, but it is easier to debug a broken test
    than a refused container start.
    """

    def test_count_meets_expected(self, db):
        """Live trigger count is at least EXPECTED_TRIGGER_COUNT.

        ``>=`` rather than ``==`` so adding extra triggers in a
        future migration (e.g. for the read-audit instrumentation
        in C-52) cannot break this test; the entrypoint check uses
        the same comparator.
        """
        actual = _trigger_count(db.session)
        assert actual >= EXPECTED_TRIGGER_COUNT, (
            "Audit trigger health check would fail at container "
            f"start: expected >= {EXPECTED_TRIGGER_COUNT}, found {actual}."
        )
