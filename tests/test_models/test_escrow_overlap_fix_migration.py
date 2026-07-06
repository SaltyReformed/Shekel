"""Tests for the f2a7c1e9b4d3 escrow rename-duplicate overlap-fix migration.

The migration (``docs/design/escrow_line_identity_refactor.md`` context) corrects
the double-count the temporal-escrow migration ``d1e7c4a2f9b3`` created on a loan
whose escrow line was RENAMED: both the old (deactivated) and new (active) rows
were backfilled to start at origination, overlapping.  This migration collapses
the subsumed rename-duplicate (a closed version fully date-subsumed by another
same-account, same-amount, same-start version) to an empty range so only the
surviving version counts.

The migration is at HEAD when these tests run (the template builder upgraded
base->head), so its collapse SQL is exercised directly against engineered rows
(the same pattern ``test_escrow_temporal_schema_migration.py`` uses for its
derivations).  There is no exclusion constraint, so overlapping rows can be
staged freely to drive the correction.

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app.extensions import db
from app.models.loan_features import EscrowComponent
from tests._test_helpers import create_loan_account, load_migration_module

_MIGRATION = "f2a7c1e9b4d3_escrow_components_fix_rename_duplicate_overlap.py"
_ORIGINATION = date(2018, 12, 1)
_OLD_END = date(2026, 4, 3)


def _collapse_sql() -> str:
    """Return the migration's collapse UPDATE SQL (the money-critical derivation)."""
    return load_migration_module(_MIGRATION)._COLLAPSE_RENAME_DUPLICATE_SQL


def _run_collapse() -> None:
    """Execute the migration's collapse UPDATE against the test DB and commit."""
    db.session.execute(text(_collapse_sql()))
    db.session.commit()


def _add_component(loan_id, name, annual, effective_date, end_date):
    """Insert one escrow component version (flushed)."""
    comp = EscrowComponent(
        account_id=loan_id, name=name, annual_amount=annual,
        effective_date=effective_date, end_date=end_date,
    )
    db.session.add(comp)
    db.session.flush()
    return comp


class TestMigrationChain:
    """The migration is correctly chained onto the current head."""

    def test_migration_chained(self):
        """revision / down_revision link this migration onto e7c4a9f1b2d6."""
        module = load_migration_module(_MIGRATION)
        assert module.revision == "f2a7c1e9b4d3"
        assert module.down_revision == "e7c4a9f1b2d6"


class TestCollapseDerivation:
    """The collapse targets exactly the rename-duplicate signature."""

    def test_collapses_subsumed_same_amount_duplicate(self, app, db, seed_user):
        """The renamed (closed, subsumed, same-amount, same-start) line collapses.

        Mirrors the real Mortgage: "Property Tax & Insurance" [2018-12-01,
        2026-04-03) and "Tax and Insurance" [2018-12-01, open), both $7,403.88 --
        the old name is fully subsumed by the open one.  After the collapse the
        old row is an empty [2026-04-03, 2026-04-03) range (never active) and the
        surviving row alone covers every date, so escrow is single-counted.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Rename Dup Loan",
                origination_date=_ORIGINATION,
            )
            old = _add_component(
                loan.id, "Property Tax & Insurance", Decimal("7403.88"),
                _ORIGINATION, _OLD_END,
            )
            new = _add_component(
                loan.id, "Tax and Insurance", Decimal("7403.88"),
                _ORIGINATION, None,
            )
            db.session.commit()

            # Before: both active on a pre-2026-04-03 date -> double-counted.
            assert old.is_active_on(date(2026, 3, 26)) is True
            assert new.is_active_on(date(2026, 3, 26)) is True

            _run_collapse()
            db.session.refresh(old)
            db.session.refresh(new)

            # The old (subsumed) row is now an empty range: never active.
            assert old.effective_date == _OLD_END
            assert old.end_date == _OLD_END
            assert old.is_active_on(date(2026, 3, 26)) is False
            # The surviving row is untouched and still covers every date.
            assert new.effective_date == _ORIGINATION
            assert new.end_date is None
            assert new.is_active_on(date(2026, 3, 26)) is True

    def test_idempotent(self, app, db, seed_user):
        """A second run makes no further change (already-collapsed rows are skipped)."""
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Idempotent Loan",
                origination_date=_ORIGINATION,
            )
            old = _add_component(
                loan.id, "Old", Decimal("7403.88"), _ORIGINATION, _OLD_END,
            )
            _add_component(
                loan.id, "New", Decimal("7403.88"), _ORIGINATION, None,
            )
            db.session.commit()

            _run_collapse()
            db.session.refresh(old)
            assert old.effective_date == _OLD_END  # collapsed once

            _run_collapse()  # second run
            db.session.refresh(old)
            assert old.effective_date == _OLD_END  # unchanged

    def test_distinct_amount_subset_is_not_collapsed(self, app, db, seed_user):
        """A genuinely distinct line (different amount) is NOT collapsed.

        A $1,200 line subsumed by a $7,403.88 line shares the account and start
        date but not the amount, so it is a legitimate second escrow charge (e.g.
        PMI), not a rename-duplicate -- the collapse must leave it intact.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Distinct Amount Loan",
                origination_date=_ORIGINATION,
            )
            pmi = _add_component(
                loan.id, "PMI", Decimal("1200.00"), _ORIGINATION,
                date(2023, 1, 1),
            )
            _add_component(
                loan.id, "Tax", Decimal("7403.88"), _ORIGINATION, None,
            )
            db.session.commit()

            _run_collapse()
            db.session.refresh(pmi)

            # Untouched: still active over its real [2018-12-01, 2023-01-01) life.
            assert pmi.effective_date == _ORIGINATION
            assert pmi.end_date == date(2023, 1, 1)
            assert pmi.is_active_on(date(2020, 6, 1)) is True
