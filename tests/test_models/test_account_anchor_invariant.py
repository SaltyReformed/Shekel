"""Tests for the E-19 / Commit 3 account-anchor NOT NULL invariant.

Migration ``cfb15e782f86`` makes ``budget.accounts.current_anchor_balance``
and ``budget.accounts.current_anchor_period_id`` NOT NULL after
backfilling existing rows from the account's earliest transaction's
pay period (else the user's earliest period) and seeding a matching
``budget.account_anchor_history`` row.  The downstream balance
resolver (Commit 4) and the canonical entries-aware producer
(Commits 5-8) depend on this invariant to delete the four
NULL-anchor forks documented in CRIT-01.

The tests exercise three layers of the contract:

  1. **Migration backfill** (C3-1 through C3-4) -- load the migration
     module dynamically and run the embedded SQL constants
     (``BACKFILL_BALANCE_SQL``, ``BACKFILL_PERIOD_SQL``,
     ``INSERT_HISTORY_SQL``) against the test database, exercising
     the same text strings the production migration uses.  A small
     fixture temporarily re-widens the columns to nullable so a row
     with NULL anchor columns can be inserted, then the test asserts
     the backfill resolves it (or raises with the diagnostic SELECT
     when unresolvable).

  2. **Model rejection** (C3-6) -- attempting to flush an ``Account``
     with NULL anchor columns raises ``IntegrityError``.  Locks the
     storage-tier guarantee.

  3. **Creation paths** (C3-5) -- the ``auth_service.register_user``
     signup path and the ``/accounts`` POST route both write the
     ``current_anchor_balance``, ``current_anchor_period_id``, and a
     matching ``AccountAnchorHistory`` row at the moment the account
     exists.  Locks the spec contract "always create the origination
     ``AccountAnchorHistory`` and set the anchor columns at creation."
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; the test bodies receive fixtures via name binding.
from __future__ import annotations

import importlib.util
import pathlib
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db as _db
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password


# ---------------------------------------------------------------------------
# Migration module loader (mirrors the pattern in test_c40_account_id_backfill)
# ---------------------------------------------------------------------------


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)


def _load_migration(filename: str):
    """Load an Alembic migration file by path via importlib.

    The ``migrations/versions`` directory has no ``__init__.py``, so a
    regular import does not work.  We mirror Alembic's own loader so
    the test can read module-level constants (``BACKFILL_BALANCE_SQL``,
    ``BACKFILL_PERIOD_SQL``, ``INSERT_HISTORY_SQL``, ``DIAGNOSTIC_SELECT``)
    directly and run the exact text the production migration runs.
    """
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_M_ANCHOR_BACKFILL = _load_migration(
    "cfb15e782f86_backfill_account_anchor_and_tighten_.py"
)


# ---------------------------------------------------------------------------
# Per-test fixture: re-widen the anchor columns to nullable so we can
# insert engineered NULL rows, then restore NOT NULL on teardown.
# The CHECK constraint must also be dropped/recreated; PG raises if
# the column is widened to nullable while a CHECK on IS NOT NULL is
# attached.
# ---------------------------------------------------------------------------


@pytest.fixture()
def nullable_anchor_columns(db):
    """Temporarily relax NOT NULL on accounts' anchor columns.

    Drops the ``ck_accounts_anchor_balance_present`` CHECK constraint
    and widens both ``current_anchor_balance`` and
    ``current_anchor_period_id`` to nullable so the test can insert a
    legacy-shaped row.  The teardown reverses both changes so the
    next test sees the production-tightened schema.  The fixture's
    yielded value is unused; the test depends on the side effect.
    """
    db.session.commit()  # close any open transaction
    _db.session.execute(_db.text(
        "ALTER TABLE budget.accounts "
        "DROP CONSTRAINT IF EXISTS ck_accounts_anchor_balance_present"
    ))
    _db.session.execute(_db.text(
        "ALTER TABLE budget.accounts "
        "ALTER COLUMN current_anchor_balance DROP NOT NULL"
    ))
    _db.session.execute(_db.text(
        "ALTER TABLE budget.accounts "
        "ALTER COLUMN current_anchor_period_id DROP NOT NULL"
    ))
    _db.session.commit()
    try:
        yield
    finally:
        # First clear any rows the test inserted with NULL anchors
        # (they would block re-tightening) then restore the schema.
        _db.session.rollback()
        _db.session.execute(_db.text(
            "UPDATE budget.accounts SET current_anchor_balance = 0.00 "
            "WHERE current_anchor_balance IS NULL"
        ))
        # NULL anchor_period rows would block tightening; resolve via
        # the user's earliest period or delete the row.  Tests that
        # rely on this fixture create their own pay periods.
        unresolved = _db.session.execute(_db.text(
            "SELECT a.id FROM budget.accounts a "
            "WHERE a.current_anchor_period_id IS NULL"
        )).fetchall()
        for (acct_id,) in unresolved:
            earliest = _db.session.execute(_db.text(
                "SELECT pp.id FROM budget.pay_periods pp "
                "JOIN budget.accounts a ON a.user_id = pp.user_id "
                "WHERE a.id = :a "
                "ORDER BY pp.period_index ASC LIMIT 1"
            ), {"a": acct_id}).scalar()
            if earliest is None:
                _db.session.execute(_db.text(
                    "DELETE FROM budget.accounts WHERE id = :a"
                ), {"a": acct_id})
            else:
                _db.session.execute(_db.text(
                    "UPDATE budget.accounts "
                    "SET current_anchor_period_id = :p WHERE id = :a"
                ), {"p": earliest, "a": acct_id})
        _db.session.execute(_db.text(
            "ALTER TABLE budget.accounts "
            "ALTER COLUMN current_anchor_balance SET NOT NULL"
        ))
        _db.session.execute(_db.text(
            "ALTER TABLE budget.accounts "
            "ALTER COLUMN current_anchor_period_id SET NOT NULL"
        ))
        _db.session.execute(_db.text(
            "ALTER TABLE budget.accounts "
            "ADD CONSTRAINT ck_accounts_anchor_balance_present "
            "CHECK (current_anchor_balance IS NOT NULL)"
        ))
        _db.session.commit()


# ---------------------------------------------------------------------------
# C3-6 -- Model-level NOT NULL enforcement.  This test does NOT require
# the nullable_anchor_columns fixture because we expect the IntegrityError
# at flush time (the constraint should fire).
# ---------------------------------------------------------------------------


class TestModelRejectsNullAnchor:
    """C3-6: storage-tier rejects NULL anchor columns.

    Uses raw ``INSERT`` statements rather than ORM constructions
    because the conftest's E-19 ``before_insert`` event listener
    (a test-only safety net for legacy helpers) would otherwise
    auto-fill ``current_anchor_period_id`` from the user's earliest
    pay period and prevent the constraint from firing.  Raw SQL
    bypasses the listener and goes straight to the database,
    proving the storage-tier guarantee holds independently of any
    ORM scaffolding.
    """

    def test_insert_with_null_anchor_period_raises_integrity_error(
        self, app, db, bare_user
    ):
        """Raw INSERT with NULL anchor_period_id trips the NOT NULL.

        ``bare_user`` has no pay periods, so even if the autofill
        listener ran it would have nothing to fill from.  The DB
        raises ``IntegrityError`` at INSERT time.
        """
        with app.app_context():
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            with pytest.raises(IntegrityError):
                db.session.execute(_db.text(
                    "INSERT INTO budget.accounts "
                    "(user_id, account_type_id, name, "
                    " current_anchor_balance, current_anchor_period_id, "
                    " sort_order, is_active, version_id) "
                    "VALUES (:u, :t, 'Bad NULL period', 100.00, NULL, "
                    " 0, TRUE, 1)"
                ), {"u": bare_user["user"].id, "t": checking_type.id})
                db.session.flush()
            db.session.rollback()

    def test_insert_with_null_anchor_balance_raises_integrity_error(
        self, app, db, seed_user
    ):
        """Raw INSERT with NULL anchor_balance trips NOT NULL + CHECK.

        ``ck_accounts_anchor_balance_present`` is named explicitly
        for the schema audit; either it or the underlying NOT NULL
        fires on this insert.  Both produce ``IntegrityError``.
        """
        with app.app_context():
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            bootstrap = seed_user["bootstrap_period"]
            with pytest.raises(IntegrityError):
                db.session.execute(_db.text(
                    "INSERT INTO budget.accounts "
                    "(user_id, account_type_id, name, "
                    " current_anchor_balance, current_anchor_period_id, "
                    " sort_order, is_active, version_id) "
                    "VALUES (:u, :t, 'Bad NULL balance', NULL, :p, "
                    " 0, TRUE, 1)"
                ), {
                    "u": seed_user["user"].id,
                    "t": checking_type.id,
                    "p": bootstrap.id,
                })
                db.session.flush()
            db.session.rollback()


# ---------------------------------------------------------------------------
# C3-1 / C3-2 / C3-3 -- Migration backfill behaviour.  These tests
# re-widen the columns to nullable, insert engineered rows with NULL
# anchors, run the backfill SQL the migration uses, and assert the
# resolved state.
# ---------------------------------------------------------------------------


class TestMigrationBackfill:
    """C3-1/2/3: migration backfill resolves NULLs or raises clearly."""

    def test_backfill_resolves_null_anchor_from_earliest_transaction(
        self, app, db, seed_user, seed_periods, nullable_anchor_columns
    ):
        """C3-1: an account with NULL anchor and one transaction is
        backfilled to the transaction's pay_period and a 0.00 balance,
        and a matching AccountAnchorHistory row is inserted.

        Arithmetic: the derivation rule picks the earliest non-deleted
        transaction's pay_period.  With the transaction sitting in
        ``seed_periods[3]`` (an arbitrary period from the seed set),
        the backfill must resolve to exactly that period_id.  The
        ``BACKFILL_BALANCE_SQL`` sets NULL -> ``Decimal("0.00")``;
        the ``INSERT_HISTORY_SQL`` creates one history row tagged with
        the same (account_id, pay_period_id, anchor_balance).
        """
        from app.models.transaction import Transaction
        from app.models.ref import Status, TransactionType

        with app.app_context():
            user = seed_user["user"]
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )

            # Insert an account with NULL anchor (allowed under the
            # nullable_anchor_columns fixture).
            db.session.execute(_db.text(
                "INSERT INTO budget.accounts "
                "(user_id, account_type_id, name, current_anchor_balance, "
                " current_anchor_period_id, sort_order, is_active, "
                " version_id) "
                "VALUES (:u, :t, 'LegacyNullAnchor', NULL, NULL, 0, TRUE, 1)"
            ), {"u": user.id, "t": checking_type.id})
            db.session.flush()
            account_id = db.session.execute(_db.text(
                "SELECT id FROM budget.accounts "
                "WHERE user_id = :u AND name = 'LegacyNullAnchor'"
            ), {"u": user.id}).scalar()

            # Insert one transaction tied to seed_periods[3] for the
            # legacy account.  The backfill should pick this period as
            # the anchor (tier 1 of the COALESCE).
            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            expense = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )
            txn = Transaction(
                account_id=account_id,
                pay_period_id=seed_periods[3].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Driving txn",
                transaction_type_id=expense.id,
                estimated_amount=Decimal("100.00"),
            )
            db.session.add(txn)
            db.session.flush()

            # Run the migration's two backfill statements verbatim, then
            # the history materialisation.
            db.session.execute(_db.text(_M_ANCHOR_BACKFILL.BACKFILL_BALANCE_SQL))
            db.session.execute(_db.text(_M_ANCHOR_BACKFILL.BACKFILL_PERIOD_SQL))
            db.session.execute(_db.text(_M_ANCHOR_BACKFILL.INSERT_HISTORY_SQL))
            db.session.flush()

            resolved = db.session.execute(_db.text(
                "SELECT current_anchor_balance, current_anchor_period_id "
                "FROM budget.accounts WHERE id = :a"
            ), {"a": account_id}).first()
            # 0.00 anchor balance (E-12 zero-is-a-value) + the
            # transaction's pay_period as the anchor period.
            assert resolved.current_anchor_balance == Decimal("0.00")
            assert resolved.current_anchor_period_id == seed_periods[3].id

            history_count = db.session.execute(_db.text(
                "SELECT count(*) FROM budget.account_anchor_history "
                "WHERE account_id = :a"
            ), {"a": account_id}).scalar()
            assert history_count == 1

            history = db.session.execute(_db.text(
                "SELECT pay_period_id, anchor_balance, notes "
                "FROM budget.account_anchor_history WHERE account_id = :a"
            ), {"a": account_id}).first()
            assert history.pay_period_id == seed_periods[3].id
            assert history.anchor_balance == Decimal("0.00")
            assert "origination backfill" in history.notes

    def test_backfill_leaves_existing_anchor_untouched(
        self, app, db, seed_user, seed_periods, nullable_anchor_columns
    ):
        """C3-2: an account with a populated anchor + matching
        AccountAnchorHistory row is not modified by the backfill.
        Re-running the migration on an already-backfilled database
        is a strict no-op: the columns are unchanged and the
        INSERT_HISTORY_SQL's NOT EXISTS guard skips the duplicate.

        Arithmetic: starting state is anchor_balance = $1234.56,
        anchor_period_id = seed_periods[2].id, with a matching
        history row already seeded.  After two consecutive backfill
        runs, the columns equal exactly that pair and the matching
        history-row count stays at 1 (no duplicate).
        """
        with app.app_context():
            # Set up the row state and a matching history row via
            # raw SQL so the assertion is decoupled from any ORM
            # session interactions with the nullable_anchor_columns
            # fixture's ALTER TABLE/COMMIT cycle.
            account_id = seed_user["account"].id
            period_id = seed_periods[2].id
            db.session.execute(_db.text(
                "UPDATE budget.accounts "
                "SET current_anchor_balance = 1234.56, "
                "    current_anchor_period_id = :p "
                "WHERE id = :a"
            ), {"p": period_id, "a": account_id})
            db.session.execute(_db.text(
                "INSERT INTO budget.account_anchor_history "
                "(account_id, pay_period_id, anchor_balance, notes) "
                "VALUES (:a, :p, 1234.56, 'pre-existing match')"
            ), {"a": account_id, "p": period_id})
            db.session.commit()

            pre_balance = Decimal("1234.56")
            pre_history_count = db.session.execute(_db.text(
                "SELECT count(*) FROM budget.account_anchor_history "
                "WHERE account_id = :a AND pay_period_id = :p "
                "  AND anchor_balance = :b"
            ), {"a": account_id, "p": period_id, "b": str(pre_balance)}).scalar()
            assert pre_history_count == 1, (
                f"setup invariant: expected 1 matching history row, "
                f"got {pre_history_count}"
            )

            # First backfill run.
            db.session.execute(_db.text(_M_ANCHOR_BACKFILL.BACKFILL_BALANCE_SQL))
            db.session.execute(_db.text(_M_ANCHOR_BACKFILL.BACKFILL_PERIOD_SQL))
            db.session.execute(_db.text(_M_ANCHOR_BACKFILL.INSERT_HISTORY_SQL))
            db.session.commit()

            # Second backfill run -- the idempotency test.
            db.session.execute(_db.text(_M_ANCHOR_BACKFILL.BACKFILL_BALANCE_SQL))
            db.session.execute(_db.text(_M_ANCHOR_BACKFILL.BACKFILL_PERIOD_SQL))
            db.session.execute(_db.text(_M_ANCHOR_BACKFILL.INSERT_HISTORY_SQL))
            db.session.commit()

            # Columns unchanged byte-for-byte.
            post = db.session.execute(_db.text(
                "SELECT current_anchor_balance, current_anchor_period_id "
                "FROM budget.accounts WHERE id = :a"
            ), {"a": account_id}).first()
            assert post.current_anchor_balance == pre_balance
            assert post.current_anchor_period_id == period_id

            # Idempotent: matching history-row count is still 1, no
            # duplicate inserted by the NOT EXISTS guard.
            post_history_count = db.session.execute(_db.text(
                "SELECT count(*) FROM budget.account_anchor_history "
                "WHERE account_id = :a AND pay_period_id = :p "
                "  AND anchor_balance = :b"
            ), {"a": account_id, "p": period_id, "b": str(pre_balance)}).scalar()
            assert post_history_count == pre_history_count == 1

    def test_diagnostic_select_contains_unresolved_columns(self):
        """C3-3: DIAGNOSTIC_SELECT names the offending account columns.

        The migration's diagnostic SELECT must be valid SQL that
        enumerates account_id, user_id, name, and both anchor columns
        for any row still NULL.  Verified textually (the migration's
        SQL is also exercised by the backfill tests above).  The
        check guards against an operator deleting the diagnostic
        without realising it is part of the RuntimeError message.
        """
        sql = _M_ANCHOR_BACKFILL.DIAGNOSTIC_SELECT
        assert "account_id" in sql
        assert "user_id" in sql
        assert "current_anchor_balance" in sql
        assert "current_anchor_period_id" in sql
        assert "IS NULL" in sql


# ---------------------------------------------------------------------------
# C3-5 -- Creation paths always write a non-NULL anchor and a matching
# AccountAnchorHistory row.
# ---------------------------------------------------------------------------


class TestCreationPathsWriteAnchor:
    """C3-5: register_user and POST /accounts always set anchor + history."""

    def test_register_user_creates_anchor_and_history(self, app, db):
        """The auth_service.register_user signup path bootstraps a pay
        period, anchors the default Checking account to it with a
        Decimal("0.00") balance, and writes an origination
        AccountAnchorHistory row.

        Arithmetic: the user has no prior periods, so the bootstrap
        period takes period_index=0 with start_date=today.  The
        Checking account is created with
        ``current_anchor_balance=Decimal("0.00")`` and
        ``current_anchor_period_id`` equal to the bootstrap.id.  The
        history row mirrors the column cache.
        """
        from app.services import auth_service

        with app.app_context():
            user = auth_service.register_user(
                email="c3-5@example.com",
                password="strong-pass-12345",
                display_name="C3-5 Tester",
            )
            db.session.commit()

            account = db.session.query(Account).filter_by(
                user_id=user.id, name="Checking",
            ).one()
            assert account.current_anchor_balance == Decimal("0.00")
            assert account.current_anchor_period_id is not None

            # The bootstrap period covers today (cadence 14 days from
            # today).  The signup path picks period_index 0 because
            # this is the user's first period.
            period = db.session.get(PayPeriod, account.current_anchor_period_id)
            assert period is not None
            assert period.user_id == user.id
            assert period.period_index == 0
            assert period.start_date == date.today()
            assert period.end_date == date.today() + timedelta(days=13)

            histories = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).all()
            assert len(histories) == 1
            assert histories[0].pay_period_id == period.id
            assert histories[0].anchor_balance == Decimal("0.00")
            assert "origination" in (histories[0].notes or "")

    def test_create_account_route_writes_anchor_and_history(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """POST /accounts creates an account with the anchor period
        set to the current pay period and writes a matching
        AccountAnchorHistory row.

        Arithmetic: ``seed_periods_today`` places today in period 4
        of the seed_user's period set.  The route resolves the
        current period via ``pay_period_service.get_current_period``
        and uses it as the anchor.  The submitted anchor_balance is
        ``$1500.00`` and must appear verbatim on both the column
        and the history row.
        """
        from app.services import pay_period_service

        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            assert current_period is not None

            resp = auth_client.post("/accounts", data={
                "name": "C3-5 Savings",
                "account_type_id": str(savings_type.id),
                "anchor_balance": "1500.00",
            })
            assert resp.status_code in (302, 303), resp.data[:200]

            account = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="C3-5 Savings",
            ).one()
            assert account.current_anchor_balance == Decimal("1500.00")
            assert account.current_anchor_period_id == current_period.id

            history = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).one()
            assert history.pay_period_id == current_period.id
            assert history.anchor_balance == Decimal("1500.00")
            assert history.notes == "origination"
