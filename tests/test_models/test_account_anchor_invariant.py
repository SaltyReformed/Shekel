"""Tests for the E-19 / Commit 3 account-anchor invariant.

Migration ``cfb15e782f86`` made ``budget.accounts.current_anchor_balance``
and ``budget.accounts.current_anchor_period_id`` NOT NULL after
backfilling existing rows, and seeded a matching
``budget.account_anchor_history`` row for each.  **Ruling R-EH deleted both
columns at plan step X-f1c3c** -- they were a denormalized copy of that
history row -- so what remains of E-19 is the half that was always the fact:
every account carries an origination ASSERTION from the moment it exists, and
:func:`app.services.cash_ledger.resolve_anchor` can answer for it.  That is
what deletes CRIT-01's four NULL-anchor forks now.

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

  2. **Model rejection** (C3-6) -- DELETED at plan step X-f1c3c.  It flushed
     an ``Account`` with NULL anchor columns and asserted ``IntegrityError``;
     ruling R-EH deleted the columns, so the state it refused is not
     expressible and the ``NOT NULL`` it graded does not exist.  The invariant
     that survives -- every account has a resolvable balance -- is now the
     ORIGINATION ASSERTION, graded by the creation-path cases below and by
     ``scripts/integrity_check.py``'s re-pointed BA-01.

  3. **Creation paths** (C3-5) -- the ``auth_service.register_user``
     signup path and the ``/accounts`` POST route both write the
     origination ``AccountAnchorHistory`` row at the moment the account
     exists.  Locks the spec contract "always create the origination
     assertion".  It also asserted the ``current_anchor_*`` columns until
     ruling R-EH deleted them, and the assertion's own pay period until ruling
     R-EO deleted that; what is asserted now is the balance and the DAY.

**A WHOLE SUITE was deleted at plan step X-f1c3c and the record belongs here**,
because this file owns what remains of the contract it graded.
``tests/test_models/test_anchor_fk_deferrable.py`` (264 lines) asserted that
``accounts.current_anchor_period_id``'s foreign key was
``NO ACTION DEFERRABLE INITIALLY IMMEDIATE`` -- the catalog state, the mapper
declaration, the ``SET CONSTRAINTS ... DEFERRED`` round-trip, and the
``IntegrityError`` a period delete raised while an account still pointed at it.
**Every one of those is a property of a column that no longer exists.**  The FK,
its deferrability and the ``_DEFER_ANCHOR_FK_SQL`` the pay-period reset used are
deleted with it (rulings R-EH and R-EO), so there is nothing left to assert: not
a weakened rule, an absent one.  What the deferral existed to PROTECT -- a
balance surviving a schedule rebuild -- is graded by
``tests/test_services/test_pay_period_reset.py``'s
``test_wipes_all_and_the_balance_survives`` and
``test_the_reset_preserves_every_balance_assertion``, and the migration's own
restore of the FK is graded by
``tests/test_models/test_anchor_cache_downgrade.py``.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; the test bodies receive fixtures via name binding.
from __future__ import annotations

import importlib.util
import pathlib
from datetime import timedelta
from decimal import Decimal


from app.extensions import db as _db
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.models.user import User, UserSettings
from app.services import cash_ledger
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
        # The migration's frozen SQL names the columns it ran against; the
        # columns are gone (ruling R-EH) and a historical migration is never
        # edited to match a newer schema, so this asserts the DIAGNOSTIC still
        # names what an operator needed at the time.
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
        Checking account's origination ASSERTION carries
        ``anchor_balance=Decimal("0.00")`` observed on that same day.  It
        asserted the ``accounts.current_anchor_*`` columns beside it until
        ruling R-EH deleted them -- they were a copy of this row.

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
            # The bootstrap period covers today (cadence 14 days from
            # today).  The signup path picks period_index 0 because
            # this is the user's first period.
            signup_day = display_today()
            period = db.session.query(PayPeriod).filter_by(
                user_id=user.id, period_index=0,
            ).one()
            assert period.start_date == signup_day
            assert period.end_date == signup_day + timedelta(days=13)

            histories = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).all()
            assert len(histories) == 1
            # The account's whole anchor state IS this row (rulings R-EH and
            # R-EO): a balance and the day it was true, with no period beside
            # it and no column mirroring it.  Signup day falls inside the
            # bootstrap period asserted above, which is what ties the two
            # halves of the signup path to one clock.
            assert histories[0].observed_on == signup_day
            assert histories[0].anchor_balance == Decimal("0.00")
            assert "origination" in (histories[0].notes or "")
            assert cash_ledger.resolve_anchor(account).balance == Decimal("0.00")

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
            assert cash_ledger.resolve_anchor(account).balance == Decimal("1500.00")

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
