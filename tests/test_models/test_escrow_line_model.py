"""Constraint + relationship tests for the supersession escrow models.

Covers :class:`~app.models.escrow_line.EscrowLine` and
:class:`~app.models.escrow_line.EscrowComponentVersion` (the Commit-1 EXPAND
tables of the escrow config redesign, ``docs/design/escrow_line_identity_refactor.md``):
the ``(line_id, effective_date)`` uniqueness, the money/rate CHECKs, the
``is_removed`` default, and the FK cascade.  These tables are not read by any
surface yet, so the tests exercise the schema directly.

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.escrow_line import EscrowComponentVersion, EscrowLine
from tests._test_helpers import create_loan_account

_ORIGINATION = date(2018, 12, 1)


def _make_line(loan_id, name="Property Tax & Insurance"):
    """Insert and flush one escrow line for ``loan_id``; return it."""
    from app.extensions import db

    line = EscrowLine(account_id=loan_id, name=name)
    db.session.add(line)
    db.session.flush()
    return line


def _make_version(line_id, effective_date, annual, *, is_removed=False):
    """Insert and flush one version under ``line_id``; return it."""
    from app.extensions import db

    version = EscrowComponentVersion(
        line_id=line_id, effective_date=effective_date,
        annual_amount=annual, is_removed=is_removed,
    )
    db.session.add(version)
    db.session.flush()
    return version


class TestVersionConstraints:
    """The version table enforces the supersession invariants at the DB tier."""

    def test_unique_line_effective_date(self, app, db, seed_user):
        """Two versions of ONE line on the SAME effective date are rejected.

        ``uq_escrow_versions_line_effective`` -- a same-day correction edits the
        row, it does not append a second.
        """
        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Uq Loan")
            line = _make_line(loan.id)
            _make_version(line.id, _ORIGINATION, Decimal("7403.88"))
            with pytest.raises(IntegrityError):
                _make_version(line.id, _ORIGINATION, Decimal("8003.88"))

    def test_same_effective_date_across_lines_allowed(self, app, db, seed_user):
        """Two DIFFERENT lines may each carry a version on the same date.

        The uniqueness is per line (tax + insurance both starting at origination
        is legitimate), so the constraint is ``(line_id, effective_date)``, not
        ``(account_id, effective_date)``.
        """
        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Two Line Loan")
            tax = _make_line(loan.id, "Tax")
            ins = _make_line(loan.id, "Insurance")
            _make_version(tax.id, _ORIGINATION, Decimal("7403.88"))
            _make_version(ins.id, _ORIGINATION, Decimal("1200.00"))
            db.session.commit()
            assert db.session.query(EscrowComponentVersion).count() == 2

    def test_negative_annual_amount_rejected(self, app, db, seed_user):
        """``ck_escrow_versions_nonneg_annual_amount`` rejects a negative amount."""
        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Neg Loan")
            line = _make_line(loan.id)
            with pytest.raises(IntegrityError):
                _make_version(line.id, _ORIGINATION, Decimal("-1.00"))

    def test_inflation_rate_above_one_rejected(self, app, db, seed_user):
        """``ck_escrow_versions_valid_inflation_rate`` rejects a fraction > 1."""
        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Infl Loan")
            line = _make_line(loan.id)
            version = EscrowComponentVersion(
                line_id=line.id, effective_date=_ORIGINATION,
                annual_amount=Decimal("7403.88"),
                inflation_rate=Decimal("1.5"),
            )
            db.session.add(version)
            with pytest.raises(IntegrityError):
                db.session.flush()

    def test_tombstone_with_nonzero_amount_rejected(self, app, db, seed_user):
        """``ck_escrow_component_versions_tombstone_zero_amount`` -- a removal carries no amount.

        A tombstone (``is_removed = True``) contributes nothing, so a non-zero
        ``annual_amount`` on it is a contradiction the storage tier rejects.
        """
        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Tombstone Loan")
            line = _make_line(loan.id)
            with pytest.raises(IntegrityError):
                _make_version(
                    line.id, _ORIGINATION, Decimal("5.00"), is_removed=True,
                )

    def test_tombstone_with_zero_amount_allowed(self, app, db, seed_user):
        """A tombstone with ``annual_amount = 0`` is the valid removal form."""
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Tombstone Zero Loan",
            )
            line = _make_line(loan.id)
            version = _make_version(
                line.id, _ORIGINATION, Decimal("0.00"), is_removed=True,
            )
            db.session.commit()
            db.session.refresh(version)
            assert version.is_removed is True
            assert version.annual_amount == Decimal("0.00")

    def test_is_removed_defaults_false(self, app, db, seed_user):
        """Omitting ``is_removed`` stores a real (non-tombstone) version."""
        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Default Loan")
            line = _make_line(loan.id)
            version = EscrowComponentVersion(
                line_id=line.id, effective_date=_ORIGINATION,
                annual_amount=Decimal("7403.88"),
            )
            db.session.add(version)
            db.session.commit()
            db.session.refresh(version)
            assert version.is_removed is False


class TestLineRelationship:
    """The line/version parent-child relationship and its cascade."""

    def test_cascade_delete_line_removes_versions(self, app, db, seed_user):
        """Deleting a line deletes its versions (``ondelete=CASCADE`` + orphan)."""
        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Cascade Loan")
            line = _make_line(loan.id)
            _make_version(line.id, _ORIGINATION, Decimal("7403.88"))
            _make_version(line.id, date(2026, 1, 1), Decimal("8003.88"))
            db.session.commit()
            assert db.session.query(EscrowComponentVersion).count() == 2

            db.session.delete(line)
            db.session.commit()
            assert db.session.query(EscrowComponentVersion).count() == 0

    def test_line_id_fk_carries_the_convention_name(self, app, db):
        """The ``line_id`` FK is named ``fk_escrow_component_versions_line_id``.

        The project's manual FK-naming convention (``app/extensions.py``,
        ``.claude/rules/database.md``) requires every inline FK to carry an
        explicit ``fk_<table>_<column>`` name so later migrations reference it by
        a stable name rather than a dialect default.  No automated gate covers
        this new inter-table FK, so this catalog assertion pins it.
        """
        with app.app_context():
            exists = db.session.execute(text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM pg_constraint cn "
                "  JOIN pg_class c ON c.oid = cn.conrelid "
                "  JOIN pg_namespace n ON n.oid = c.relnamespace "
                "  WHERE cn.conname = 'fk_escrow_component_versions_line_id' "
                "    AND n.nspname = 'budget' "
                "    AND c.relname = 'escrow_component_versions' "
                "    AND cn.contype = 'f'"
                ")"
            )).scalar()
            assert exists is True
