"""Temporal (effective-dated) invariants for budget.escrow_components.

Exercises the storage-tier guarantees added by migration
``d1e7c4a2f9b3_escrow_components_effective_dating.py`` -- the
``[effective_date, end_date)`` active range that replaced the ``is_active``
boolean:

  * ``ck_escrow_components_date_range`` -- ``end_date IS NULL OR end_date >=
    effective_date`` (a same-day add-then-delete zero-length range is valid).
  * ``uq_escrow_components_account_name_active`` -- at most one ACTIVE
    (``end_date IS NULL``) version per name, so a removed line item may be
    re-added under the same name.

plus the service loader that reads the currently-active set.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.loan_features import EscrowComponent
from app.models.ref import AccountType
from app.services import account_service
from app.services.loan_payment_service import load_active_escrow_components


def _mortgage(seed_user, name="Mortgage"):
    """Create and commit an amortizing (Mortgage) account for the seed user."""
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


def _component(account_id, name, *, effective_date, end_date=None):
    """Build an escrow component with explicit range dates (no default)."""
    return EscrowComponent(
        account_id=account_id,
        name=name,
        annual_amount=Decimal("1200.00"),
        effective_date=effective_date,
        end_date=end_date,
    )


class TestEscrowRangeCheck:
    """``ck_escrow_components_date_range`` on the active range."""

    def test_end_date_before_effective_rejected(self, app, seed_user):
        """end_date strictly before effective_date violates the CHECK."""
        with app.app_context():
            acct = _mortgage(seed_user)
            db.session.add(_component(
                acct.id, "Tax",
                effective_date=date(2026, 6, 1), end_date=date(2026, 3, 1),
            ))
            with pytest.raises(IntegrityError) as info:
                db.session.commit()
            assert "ck_escrow_components_date_range" in str(info.value)
            db.session.rollback()

    def test_zero_length_range_allowed(self, app, seed_user):
        """end_date == effective_date is a valid (never-active) zero-length range.

        A component added and removed on the same day: ``active_on(D)`` is
        ``effective_date <= D < end_date``, empty when the two are equal, so it
        contributes to no payment -- the honest record of a same-day add+delete.
        """
        with app.app_context():
            acct = _mortgage(seed_user)
            comp = _component(
                acct.id, "Tax",
                effective_date=date(2026, 6, 1), end_date=date(2026, 6, 1),
            )
            db.session.add(comp)
            db.session.commit()  # must not raise
            assert comp.id is not None

    def test_open_range_allowed(self, app, seed_user):
        """A NULL end_date (still in effect) satisfies the CHECK."""
        with app.app_context():
            acct = _mortgage(seed_user)
            comp = _component(
                acct.id, "Tax", effective_date=date(2026, 1, 1), end_date=None,
            )
            db.session.add(comp)
            db.session.commit()
            assert comp.end_date is None


class TestEscrowActiveNameUnique:
    """``uq_escrow_components_account_name_active`` -- one active name per loan."""

    def test_two_active_same_name_rejected(self, app, seed_user):
        """Two currently-active (end_date NULL) components sharing a name collide."""
        with app.app_context():
            acct = _mortgage(seed_user)
            db.session.add(_component(
                acct.id, "Insurance", effective_date=date(2026, 1, 1),
            ))
            db.session.commit()
            db.session.add(_component(
                acct.id, "Insurance", effective_date=date(2026, 2, 1),
            ))
            with pytest.raises(IntegrityError) as info:
                db.session.commit()
            assert "uq_escrow_components_account_name_active" in str(info.value)
            db.session.rollback()

    def test_removed_then_readded_same_name_allowed(self, app, seed_user):
        """A removed version and its re-added successor may share a name.

        The partial unique covers only ``end_date IS NULL``, so closing the
        first version's range (stamping ``end_date``) frees the name for a new
        active version -- the "remove then re-add the same line item" flow the
        total unique used to forbid.
        """
        with app.app_context():
            acct = _mortgage(seed_user)
            removed = _component(
                acct.id, "Insurance",
                effective_date=date(2026, 1, 1), end_date=date(2026, 5, 1),
            )
            db.session.add(removed)
            db.session.commit()
            readded = _component(
                acct.id, "Insurance", effective_date=date(2026, 5, 1),
            )
            db.session.add(readded)
            db.session.commit()  # must not raise
            assert readded.id is not None and readded.id != removed.id


class TestLoadActiveEscrowComponents:
    """The currently-active loader reads exactly ``end_date IS NULL``."""

    def test_excludes_removed_components(self, app, seed_user):
        """load_active_escrow_components returns only the open-range rows."""
        with app.app_context():
            acct = _mortgage(seed_user)
            db.session.add(_component(
                acct.id, "Active Tax", effective_date=date(2026, 1, 1),
            ))
            db.session.add(_component(
                acct.id, "Removed Insurance",
                effective_date=date(2026, 1, 1), end_date=date(2026, 5, 1),
            ))
            db.session.commit()

            active = load_active_escrow_components(acct.id)
            assert [c.name for c in active] == ["Active Tax"]
