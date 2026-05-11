"""Tests for the C-40 / F-026 fix to migration ``efffcf647644``.

The migration now adds ``budget.transactions.account_id`` via the
safe three-step pattern (add nullable, backfill, alter NOT NULL).
The backfill SQL resolves ``account_id`` through three COALESCE
tiers in order of specificity:

  1. ``auth.user_settings.default_grid_account_id``
  2. First active Checking account for the owning user
  3. First active account of any type for the owning user

Tests load the migration module dynamically (the
``migrations/versions`` directory has no ``__init__.py`` so it is not
on the import path) and execute the exact ``BACKFILL_SQL`` string
that the migration runs against the live database, ensuring the
tests and the production migration cannot drift.

A module-level fixture temporarily drops the NOT NULL constraint on
``budget.transactions.account_id`` for tests that need to insert
rows with NULL ``account_id``, restoring the constraint after each
test so subsequent tests see the production schema.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; ``unused-argument`` is unavoidable for fixtures requested
# for their side effects (the ``nullable_account_id`` fixture relaxes
# a schema constraint and restores it on teardown -- the test bodies
# do not reference the yielded value but must receive the fixture).
# pylint: disable=unused-argument
from __future__ import annotations

import importlib.util
import pathlib
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.account import Account
from app.models.ref import AccountType, Status, TransactionType


# ---------------------------------------------------------------------------
# Migration module loader
# ---------------------------------------------------------------------------


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)


def _load_migration(filename: str):
    """Load an Alembic migration file as a Python module via importlib.

    The ``migrations/versions`` directory has no ``__init__.py`` so
    standard ``import`` would fail; alembic itself loads scripts via
    importlib at runtime.  We mirror that behaviour to access module-
    level constants (``BACKFILL_SQL``) directly from tests.
    """
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_M_ACCOUNT_ID = _load_migration(
    "efffcf647644_add_account_id_column_to_transactions.py"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_transaction_with_null_account_id(
    db,
    *,
    pay_period_id: int,
    scenario_id: int,
    name: str,
    estimated_amount: Decimal,
) -> int:
    """Insert a row into budget.transactions with account_id = NULL.

    Returns the new row's id.  Requires the test fixture to have
    dropped the NOT NULL constraint on account_id; otherwise the
    INSERT fails with the expected NOT NULL violation.

    The INSERT explicitly enumerates every NOT NULL column so the
    test does not depend on database-side defaults that could shift
    if a future migration changes them.  ``status_id`` and
    ``transaction_type_id`` are resolved via the seeded ref data
    (Projected status, Expense type) -- the choice does not affect
    backfill semantics, which depend only on ``pay_period_id``.
    """
    projected = (
        db.session.query(Status).filter_by(name="Projected").one()
    )
    expense = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    row = db.session.execute(_db.text(
        "INSERT INTO budget.transactions "
        "(account_id, pay_period_id, scenario_id, status_id, name, "
        " transaction_type_id, estimated_amount, version_id, "
        " is_deleted, is_override) "
        "VALUES (NULL, :pp, :sc, :st, :name, :tt, :amt, 1, FALSE, FALSE) "
        "RETURNING id"
    ), {
        "pp": pay_period_id,
        "sc": scenario_id,
        "st": projected.id,
        "name": name,
        "tt": expense.id,
        "amt": str(estimated_amount),
    }).scalar()
    db.session.flush()
    return row


def _resolved_account_id(db, transaction_id: int) -> int | None:
    """Read account_id back from the DB for a specific transaction row.

    Bypasses the SQLAlchemy ORM identity map so the value reflects
    the latest committed/flushed state rather than a possibly stale
    in-memory object.
    """
    return db.session.execute(_db.text(
        "SELECT account_id FROM budget.transactions WHERE id = :id"
    ), {"id": transaction_id}).scalar()


# ---------------------------------------------------------------------------
# Fixture: temporarily drop NOT NULL on budget.transactions.account_id
# ---------------------------------------------------------------------------


@pytest.fixture
def nullable_account_id(db):
    """Drop NOT NULL on budget.transactions.account_id for one test.

    The C-40 backfill tests need to insert ``budget.transactions``
    rows with NULL ``account_id`` to exercise the backfill UPDATE.
    The production schema enforces NOT NULL on the column (the whole
    point of the migration), so the fixture relaxes the constraint
    for the duration of one test and restores it in a try/finally.

    Cleanup deletes any rows still carrying NULL ``account_id``
    before re-applying the NOT NULL constraint -- otherwise the
    ALTER would fail with the very same NOT NULL violation the
    migration was designed to prevent, leaving the schema half-
    reverted for the next test.  This belt-and-braces cleanup
    matters because the per-worker test database persists across
    tests in the same worker (conftest's ``db`` fixture truncates
    rows but does not reset DDL); a schema regression here would
    cascade to every later test that touches the column.
    """
    db.session.execute(_db.text(
        "ALTER TABLE budget.transactions "
        "ALTER COLUMN account_id DROP NOT NULL"
    ))
    db.session.commit()
    try:
        yield
    finally:
        # Defensive cleanup: a test failure could leave NULL rows
        # behind that would block the ALTER ... SET NOT NULL.  Delete
        # them so the schema reliably restores to the production state.
        db.session.rollback()
        db.session.execute(_db.text(
            "DELETE FROM budget.transactions WHERE account_id IS NULL"
        ))
        db.session.execute(_db.text(
            "ALTER TABLE budget.transactions "
            "ALTER COLUMN account_id SET NOT NULL"
        ))
        db.session.commit()


# ---------------------------------------------------------------------------
# F-026 backfill resolution tests
# ---------------------------------------------------------------------------


class TestBackfillResolution:
    """The backfill SQL resolves account_id through three COALESCE tiers.

    Each test sets up a different state of the user's account
    inventory and verifies that the backfill picks the expected
    account, mirroring the documented tier order:

      1. ``auth.user_settings.default_grid_account_id``
      2. First active Checking account (sort_order ASC, id ASC)
      3. First active account of any type (sort_order ASC, id ASC)
    """

    def test_resolves_from_default_grid_account_when_set(
        self, db, seed_user, seed_periods, nullable_account_id
    ):
        """Tier 1: explicit default_grid_account_id wins over every fallback.

        Even when the user has a Checking account that would satisfy
        tier 2, the explicitly-chosen default_grid_account_id is
        preferred.  This test sets default_grid_account_id to a
        second account (Savings) and verifies the backfill picks the
        Savings account, not the seeded Checking account.
        """
        # Create a Savings account in addition to the seeded Checking.
        savings_type = (
            db.session.query(AccountType).filter_by(name="Savings").one()
        )
        savings = Account(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            current_anchor_balance=Decimal("0.00"),
        )
        db.session.add(savings)
        db.session.flush()

        # Point the user's default at Savings, NOT at Checking.
        seed_user["settings"].default_grid_account_id = savings.id
        db.session.flush()

        txn_id = _make_transaction_with_null_account_id(
            db,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            name="Tier1 Default Grid",
            estimated_amount=Decimal("10.00"),
        )

        db.session.execute(_db.text(_M_ACCOUNT_ID.BACKFILL_SQL))
        db.session.flush()

        assert _resolved_account_id(db, txn_id) == savings.id, (
            "Tier 1 resolution failed: backfill ignored "
            "default_grid_account_id and fell through to a lower tier."
        )

    def test_falls_back_to_first_checking_account_when_default_grid_is_null(
        self, db, seed_user, seed_periods, nullable_account_id
    ):
        """Tier 2: a NULL default_grid_account_id falls through to Checking.

        The seeded user has exactly one Checking account named
        ``Checking`` and a NULL default_grid_account_id (the
        UserSettings row has no default set in the seed_user fixture).
        The backfill should resolve to the Checking account.
        """
        # seed_user["settings"].default_grid_account_id defaults to NULL.
        assert seed_user["settings"].default_grid_account_id is None, (
            "Test premise broken: seed_user already has a default "
            "grid account set; tier-2 fallback cannot be exercised."
        )

        txn_id = _make_transaction_with_null_account_id(
            db,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            name="Tier2 Checking Fallback",
            estimated_amount=Decimal("20.00"),
        )

        db.session.execute(_db.text(_M_ACCOUNT_ID.BACKFILL_SQL))
        db.session.flush()

        assert _resolved_account_id(db, txn_id) == seed_user["account"].id, (
            "Tier 2 resolution failed: backfill did not pick the user's "
            "active Checking account when default_grid_account_id was NULL."
        )

    def test_falls_back_to_any_active_account_when_no_checking_exists(
        self, db, seed_user, seed_periods, nullable_account_id
    ):
        """Tier 3: with no Checking account, any active account is picked.

        Delete the seeded Checking account, create a Savings account,
        leave default_grid_account_id NULL.  The backfill should
        resolve to the Savings account via the third tier of the
        COALESCE.  Deletion is the realistic shape for tier 3: a user
        who explicitly removed the seeded Checking account and only
        operates a Savings account.
        """
        # Add a Savings account first so we can safely delete the
        # Checking one (the user must retain at least one account for
        # the backfill to resolve).
        savings_type = (
            db.session.query(AccountType).filter_by(name="Savings").one()
        )
        savings = Account(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            current_anchor_balance=Decimal("0.00"),
        )
        db.session.add(savings)
        db.session.flush()

        # Now delete the seeded Checking.  The seeded
        # current_anchor_period_id pointing at this account must be
        # cleared first (FK is ON DELETE SET NULL but explicit is
        # clearer for readers).
        checking = seed_user["account"]
        checking.current_anchor_period_id = None
        db.session.flush()
        db.session.delete(checking)
        db.session.flush()

        # Sanity: tier 1 and tier 2 should both miss for this user.
        assert seed_user["settings"].default_grid_account_id is None
        no_checking = db.session.execute(_db.text(
            "SELECT count(*) FROM budget.accounts a "
            "JOIN ref.account_types ref_at ON ref_at.id = a.account_type_id "
            "WHERE a.user_id = :uid AND ref_at.name = 'Checking' "
            "  AND a.is_active = TRUE"
        ), {"uid": seed_user["user"].id}).scalar()
        assert no_checking == 0

        txn_id = _make_transaction_with_null_account_id(
            db,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            name="Tier3 Any-Active Fallback",
            estimated_amount=Decimal("30.00"),
        )

        db.session.execute(_db.text(_M_ACCOUNT_ID.BACKFILL_SQL))
        db.session.flush()

        assert _resolved_account_id(db, txn_id) == savings.id, (
            "Tier 3 resolution failed: backfill did not pick the only "
            "active account available to the user."
        )

    def test_skips_inactive_accounts_in_tier_two(
        self, db, seed_user, seed_periods, nullable_account_id
    ):
        """An inactive Checking account is not eligible for tier-2 backfill.

        Mark the seeded Checking account inactive and create a second
        active Checking account.  The backfill must pick the active
        Checking, not the inactive one.  Verifies the
        ``a.is_active = TRUE`` predicate in tier 2 of the COALESCE.
        """
        seed_user["account"].is_active = False
        db.session.flush()

        checking_type = (
            db.session.query(AccountType).filter_by(name="Checking").one()
        )
        active_checking = Account(
            user_id=seed_user["user"].id,
            account_type_id=checking_type.id,
            name="Active Checking",
            current_anchor_balance=Decimal("0.00"),
            is_active=True,
        )
        db.session.add(active_checking)
        db.session.flush()

        txn_id = _make_transaction_with_null_account_id(
            db,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            name="Skip Inactive Checking",
            estimated_amount=Decimal("40.00"),
        )

        db.session.execute(_db.text(_M_ACCOUNT_ID.BACKFILL_SQL))
        db.session.flush()

        assert _resolved_account_id(db, txn_id) == active_checking.id, (
            "Tier 2 picked the inactive Checking account, violating "
            "the ``is_active = TRUE`` predicate."
        )

    def test_is_idempotent_against_already_populated_rows(
        self, db, seed_user, seed_periods, nullable_account_id
    ):
        """Re-running the backfill does not change rows with non-NULL account_id.

        The ``WHERE t.account_id IS NULL`` guard on the UPDATE means
        rows already pointing at an account survive a re-run
        untouched.  This is the load-bearing idempotency invariant
        for fresh-DB bring-ups that subsequently re-stamp the
        migration (or for staging rebuilds against a partially-
        backfilled snapshot).
        """
        # Insert one row WITH account_id set and one WITHOUT.
        db.session.execute(_db.text(
            "INSERT INTO budget.transactions "
            "(account_id, pay_period_id, scenario_id, status_id, name, "
            " transaction_type_id, estimated_amount, version_id, "
            " is_deleted, is_override) "
            "VALUES (:acc, :pp, :sc, :st, :name, :tt, :amt, 1, FALSE, FALSE) "
            "RETURNING id"
        ), {
            "acc": seed_user["account"].id,
            "pp": seed_periods[0].id,
            "sc": seed_user["scenario"].id,
            "st": db.session.query(Status).filter_by(name="Projected").one().id,
            "name": "Already Populated",
            "tt": db.session.query(TransactionType).filter_by(name="Expense").one().id,
            "amt": "50.00",
        })
        db.session.flush()
        populated_id = db.session.execute(_db.text(
            "SELECT id FROM budget.transactions WHERE name = 'Already Populated'"
        )).scalar()

        # Create a second account so we can detect any (incorrect)
        # rewrite of the already-populated row.
        savings_type = (
            db.session.query(AccountType).filter_by(name="Savings").one()
        )
        savings = Account(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            current_anchor_balance=Decimal("0.00"),
        )
        db.session.add(savings)
        db.session.flush()
        # Point default_grid_account_id at Savings so a buggy backfill
        # would overwrite the Checking-linked row with Savings.
        seed_user["settings"].default_grid_account_id = savings.id
        db.session.flush()

        # Run the backfill twice.
        db.session.execute(_db.text(_M_ACCOUNT_ID.BACKFILL_SQL))
        db.session.flush()
        db.session.execute(_db.text(_M_ACCOUNT_ID.BACKFILL_SQL))
        db.session.flush()

        # The already-populated row must still point at Checking.
        assert _resolved_account_id(db, populated_id) == seed_user["account"].id, (
            "Backfill rewrote a row whose account_id was already non-NULL; "
            "the WHERE account_id IS NULL guard is broken."
        )

    def test_leaves_null_when_user_has_no_active_accounts(
        self, db, seed_user, seed_periods, nullable_account_id
    ):
        """All three tiers miss when the user has no active accounts.

        Disable the seeded Checking account and leave the user with
        no other accounts.  All three COALESCE tiers should return
        NULL, leaving the transaction's account_id NULL.  The
        migration's post-backfill RuntimeError path covers this
        scenario; we verify the SQL leaves the row NULL so the
        operator-facing error message fires.
        """
        # default_grid_account_id is already NULL.
        # Disable the only account.
        seed_user["account"].is_active = False
        db.session.flush()

        txn_id = _make_transaction_with_null_account_id(
            db,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            name="Unresolvable",
            estimated_amount=Decimal("60.00"),
        )

        db.session.execute(_db.text(_M_ACCOUNT_ID.BACKFILL_SQL))
        db.session.flush()

        assert _resolved_account_id(db, txn_id) is None, (
            "Backfill resolved an account_id for a user with no active "
            "accounts; an unintended fallback path was triggered."
        )
