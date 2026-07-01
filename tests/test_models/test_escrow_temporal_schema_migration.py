"""Tests for the d1e7c4a2f9b3 escrow-effective-dating schema migration.

Temporal-escrow prerequisite (see
``docs/audits/balance_architecture/implementation_plan_temporal_escrow.md``).
The migration replaces ``budget.escrow_components.is_active`` with an
effective-dated ``[effective_date, end_date)`` range.

The migration is already at HEAD when these tests run (the template builder
upgraded base->head), so the per-worker DB shows the post-migration schema.
Following the split the sibling schema-migration tests use
(``test_ledger_account_kind_schema_migration.py``), these tests assert the
migration is chained and its post-migration schema matches the model, and they
exercise the backfill DERIVATIONS as SELECTs over engineered rows -- the money-
critical part, because a later commit dates each historical payment's escrow by
the backfilled ``effective_date``.  The full executable upgrade->downgrade
round-trip was run during development against the test DB (``flask db
downgrade`` -> ``flask db upgrade`` returned the DB to head with no error and no
model-vs-migration drift), which is the safe place for DDL that would otherwise
break every other test in the xdist worker.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app.extensions import db
from app.models.loan_features import EscrowComponent
from app.models.ref import AccountType
from app.services import account_service
from tests._test_helpers import create_loan_account, load_migration_module

_MIGRATION = "d1e7c4a2f9b3_escrow_components_effective_dating.py"

# Mirrors the migration's ``_BACKFILL_EFFECTIVE_DATE_SQL`` COALESCE derivation
# (origination floor, else the row's own creation date) as a SELECT over one
# escrow row, so a regression in that derivation fails here.
_EFFECTIVE_DATE_DERIVATION = (
    "SELECT COALESCE("
    "  (SELECT lp.origination_date FROM budget.loan_params lp "
    "   WHERE lp.account_id = ec.account_id), "
    "  (ec.created_at AT TIME ZONE 'UTC')::date) "
    "FROM budget.escrow_components ec WHERE ec.id = :eid"
)

# Mirrors the migration's ``_BACKFILL_END_DATE_SQL`` GREATEST derivation.
_END_DATE_DERIVATION = (
    "SELECT GREATEST((ec.updated_at AT TIME ZONE 'UTC')::date, ec.effective_date) "
    "FROM budget.escrow_components ec WHERE ec.id = :eid"
)


def _bare_mortgage(seed_user, name):
    """A Mortgage account with NO LoanParams (the no-loan_params backfill case)."""
    acct_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=acct_type.id,
            name=name,
            anchor_balance=Decimal("0.00"),
        ),
    )
    db.session.add(account)
    db.session.commit()
    return account


class TestMigrationChainAndShape:
    """The migration is correctly chained and its schema matches the model."""

    def test_migration_chained(self):
        """revision / down_revision link this migration onto the Commit-2 head."""
        module = load_migration_module(_MIGRATION)
        assert module.revision == "d1e7c4a2f9b3"
        assert module.down_revision == "efca4315bf81"

    def test_is_active_dropped_and_range_columns_present(self, app):
        """is_active is gone; effective_date is NOT NULL, end_date is nullable."""
        with app.app_context():
            rows = db.session.execute(text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'budget' "
                "AND table_name = 'escrow_components' "
                "AND column_name IN ('is_active', 'effective_date', 'end_date')"
            )).all()
            shape = {name: nullable for name, nullable in rows}
            assert "is_active" not in shape
            assert shape["effective_date"] == "NO"
            assert shape["end_date"] == "YES"

    def test_range_check_and_partial_unique_exist(self, app):
        """The >= range CHECK and the active-only partial unique are present."""
        with app.app_context():
            check_def = db.session.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_escrow_components_date_range'"
            )).scalar()
            assert check_def is not None and ">=" in check_def

            index_def = db.session.execute(text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'uq_escrow_components_account_name_active'"
            )).scalar()
            assert index_def is not None
            assert "UNIQUE" in index_def
            assert "end_date IS NULL" in index_def


class TestEffectiveDateBackfillDerivation:
    """``effective_date`` = origination floor, else the row's creation date."""

    def test_uses_loan_origination_date(self, app, seed_user):
        """A component on a configured loan backfills to its origination date."""
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="BackfillLoan",
                origination_date=date(2020, 1, 1),
            )
            comp = EscrowComponent(
                account_id=loan.id, name="Tax",
                annual_amount=Decimal("1200.00"),
            )
            db.session.add(comp)
            db.session.commit()

            derived = db.session.execute(
                text(_EFFECTIVE_DATE_DERIVATION), {"eid": comp.id},
            ).scalar()
            # loan_params present -> the origination floor, so every historical
            # payment (all on or after origination) sees this component.
            assert derived == date(2020, 1, 1)

    def test_falls_back_to_created_at_without_loan_params(self, app, seed_user):
        """A component on an account with no LoanParams uses its creation date."""
        with app.app_context():
            acct = _bare_mortgage(seed_user, "NoParamsLoan")
            comp = EscrowComponent(
                account_id=acct.id, name="Tax",
                annual_amount=Decimal("1200.00"),
            )
            db.session.add(comp)
            db.session.commit()

            created_date = db.session.execute(text(
                "SELECT (created_at AT TIME ZONE 'UTC')::date "
                "FROM budget.escrow_components WHERE id = :eid"
            ), {"eid": comp.id}).scalar()
            derived = db.session.execute(
                text(_EFFECTIVE_DATE_DERIVATION), {"eid": comp.id},
            ).scalar()
            # No loan_params -> the COALESCE subquery is NULL -> created_at date.
            assert derived == created_date


class TestEndDateBackfillDerivation:
    """Inactive rows close to ``GREATEST(updated_at::date, effective_date)``."""

    def test_greatest_floors_to_effective_date(self, app, seed_user):
        """A future effective_date wins the GREATEST (the zero-length floor)."""
        with app.app_context():
            acct = _bare_mortgage(seed_user, "EndDateLoan")
            comp = EscrowComponent(
                account_id=acct.id, name="Tax",
                annual_amount=Decimal("1200.00"),
                effective_date=date(2099, 1, 1),  # after updated_at (today)
            )
            db.session.add(comp)
            db.session.commit()

            derived = db.session.execute(
                text(_END_DATE_DERIVATION), {"eid": comp.id},
            ).scalar()
            # updated_at::date (today) < effective_date -> floors to
            # effective_date, so end_date == effective_date is a valid
            # zero-length range under the >= CHECK.
            assert derived == date(2099, 1, 1)

    def test_greatest_uses_updated_at_when_later(self, app, seed_user):
        """A past effective_date yields the updated_at date (the real removal)."""
        with app.app_context():
            acct = _bare_mortgage(seed_user, "EndDateLoan2")
            comp = EscrowComponent(
                account_id=acct.id, name="Tax",
                annual_amount=Decimal("1200.00"),
                effective_date=date(2000, 1, 1),  # before updated_at (today)
            )
            db.session.add(comp)
            db.session.commit()

            updated_date = db.session.execute(text(
                "SELECT (updated_at AT TIME ZONE 'UTC')::date "
                "FROM budget.escrow_components WHERE id = :eid"
            ), {"eid": comp.id}).scalar()
            derived = db.session.execute(
                text(_END_DATE_DERIVATION), {"eid": comp.id},
            ).scalar()
            assert derived == updated_date


class TestDowngradeIsActiveDerivation:
    """The downgrade restores ``is_active = (end_date IS NULL)``."""

    def test_open_range_is_active_closed_is_not(self, app, seed_user):
        """An open range restores to active; a closed range to inactive."""
        with app.app_context():
            acct = _bare_mortgage(seed_user, "DowngradeLoan")
            active = EscrowComponent(
                account_id=acct.id, name="Active",
                annual_amount=Decimal("1200.00"),
                effective_date=date(2025, 1, 1), end_date=None,
            )
            removed = EscrowComponent(
                account_id=acct.id, name="Removed",
                annual_amount=Decimal("600.00"),
                effective_date=date(2025, 1, 1), end_date=date(2025, 6, 1),
            )
            db.session.add_all([active, removed])
            db.session.commit()

            # Mirrors the downgrade's ``_RESTORE_IS_ACTIVE_SQL`` derivation.
            derived = dict(db.session.execute(text(
                "SELECT name, (end_date IS NULL) FROM budget.escrow_components "
                "WHERE account_id = :aid"
            ), {"aid": acct.id}).all())
            assert derived == {"Active": True, "Removed": False}
