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

  1. **Migration backfill** (C3-4) -- load the migration module dynamically
     and assert its ``DIAGNOSTIC_SELECT`` names the columns an operator needs
     when the backfill cannot resolve a row.

     **The two DATA-PATH cases (C3-1 / C3-2) were DELETED at plan step
     X-f1c3b**, under the precedent the developer set on 2026-08-03 for the 22
     historical-migration tests that migration ``a3f7c8e21b64`` stranded.  They
     ran ``cfb15e782f86``'s frozen ``INSERT_HISTORY_SQL`` verbatim against a
     database at HEAD, and that string inserts
     ``account_anchor_history.pay_period_id``, which ruling R-EO deletes.  The
     same three measured facts carry the deletion here: a real ``base -> head``
     upgrade is unaffected (``cfb15e782f86`` runs ~30 revisions before the
     drop, against a schema that still has the column -- verified by a clean
     template rebuild); on any database already past it the migration never
     runs again; and rewinding to it requires downgrading through
     ``a3f7c8e21b64``, whose downgrade REFUSES once any row carries a settle
     day.  **The backfill's data path is unreachable with data, permanently.**
     The alternative -- a fixture that ADDS the dropped column back to
     reconstruct a historical schema -- is the shape that ruling declined, and
     it is a larger reconstruction than the ``DROP NOT NULL`` the deleted
     fixture performed.

  2. **Model rejection** (C3-6) -- attempting to flush an ``Account``
     with NULL anchor columns raises ``IntegrityError``.  Locks the
     storage-tier guarantee.

  3. **Creation paths** (C3-5) -- the ``auth_service.register_user``
     signup path and the ``/accounts`` POST route both write the
     ``current_anchor_balance``, ``current_anchor_period_id``, and a
     matching ``AccountAnchorHistory`` row at the moment the account
     exists.  Locks the spec contract "always create the origination
     ``AccountAnchorHistory`` and set the anchor columns at creation."
     Since ruling R-EO the history row carries a DAY rather than a period, so
     these assert ``observed_on`` and read the period off the account.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; the test bodies receive fixtures via name binding.
from __future__ import annotations

import importlib.util
import pathlib
from datetime import timedelta
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
from app.utils.dates import display_today


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

        **"Today" here is the USER's, not the process's** (finding R2,
        ``anchor_settle_partition.md`` Section 9).  ``register_user`` builds the
        bootstrap period from :func:`~app.utils.dates.display_today`
        (``auth_service.py:698``) so the period and the origination assertion
        ``create_account`` dates come off ONE clock.  Asserting ``date.today()``
        here pinned the PROCESS zone instead: it passes in a dev shell running
        Eastern and FAILS in CI, which runs UTC, for the four hours a day the two
        calendars disagree -- which is exactly how it failed the merge gate at
        03:56 UTC on 2026-08-01, reading ``2026-07-31 != 2026-08-01``.
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
            signup_day = display_today()
            period = db.session.get(PayPeriod, account.current_anchor_period_id)
            assert period is not None
            assert period.user_id == user.id
            assert period.period_index == 0
            assert period.start_date == signup_day
            assert period.end_date == signup_day + timedelta(days=13)

            histories = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).all()
            assert len(histories) == 1
            # The assertion carries no pay period since ruling R-EO -- what it
            # carries is the DAY, which the account's cache column resolves its
            # period from.  Signup day is inside the period asserted above.
            assert histories[0].observed_on == signup_day
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
            # The assertion is a DAY and a balance (ruling R-EO); the period
            # asserted above is the account's cache column, resolved from it.
            assert (
                current_period.start_date
                <= history.observed_on
                <= current_period.end_date
            )
            assert history.anchor_balance == Decimal("1500.00")
            assert history.notes == "origination"
