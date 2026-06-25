"""Tests for the ``AssetAppreciationParams`` model.

One-to-one with an Account (eager backref), a bounded appreciation rate
(``> -1 AND <= 1``, permitting depreciation for a future Vehicle type),
and a unique account_id.
"""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.asset_appreciation_params import AssetAppreciationParams
from app.models.ref import AccountType
from app.services import account_service


def _make_property(db, seed_user, periods, name="House"):
    """Create a Property account (no params row) for the seeded user."""
    property_type = (
        db.session.query(AccountType).filter_by(name="Property").one()
    )
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=property_type.id,
            name=name,
            anchor_balance=Decimal("400000.00"),
            anchor_period_id=periods[0].id,
        ),
    )
    db.session.commit()
    return acct


class TestAssetAppreciationParams:
    """Model invariants for the appreciation params row."""

    def test_one_to_one_eager_backref(self, app, db, seed_user, seed_periods_today):
        """``account.asset_appreciation_params`` reads the linked rate."""
        with app.app_context():
            acct = _make_property(db, seed_user, seed_periods_today)
            db.session.add(AssetAppreciationParams(
                account_id=acct.id, annual_appreciation_rate=Decimal("0.03500"),
            ))
            db.session.commit()
            db.session.refresh(acct)
            assert acct.asset_appreciation_params is not None
            assert acct.asset_appreciation_params.annual_appreciation_rate == (
                Decimal("0.03500")
            )

    def test_rate_at_upper_bound_and_negative_allowed(self, app, db, seed_user, seed_periods_today):
        """Rate 1 (inclusive) and a negative (depreciation) rate are accepted."""
        with app.app_context():
            acct = _make_property(db, seed_user, seed_periods_today)
            # Upper bound 1.00000 is inclusive (<= 1); -0.10000 is depreciation.
            db.session.add(AssetAppreciationParams(
                account_id=acct.id, annual_appreciation_rate=Decimal("1.00000"),
            ))
            db.session.commit()
            other = _make_property(db, seed_user, seed_periods_today, name="Car")
            db.session.add(AssetAppreciationParams(
                account_id=other.id, annual_appreciation_rate=Decimal("-0.10000"),
            ))
            db.session.commit()  # no IntegrityError

    def test_rate_above_one_rejected(self, app, db, seed_user, seed_periods_today):
        """A rate above 1 (100%/yr) violates the CHECK."""
        with app.app_context():
            acct = _make_property(db, seed_user, seed_periods_today)
            db.session.add(AssetAppreciationParams(
                account_id=acct.id, annual_appreciation_rate=Decimal("1.50000"),
            ))
            with pytest.raises(IntegrityError):
                db.session.flush()
            db.session.rollback()

    def test_rate_at_or_below_minus_one_rejected(self, app, db, seed_user, seed_periods_today):
        """A rate of -1 (the exclusive lower bound) violates the CHECK."""
        with app.app_context():
            acct = _make_property(db, seed_user, seed_periods_today)
            db.session.add(AssetAppreciationParams(
                account_id=acct.id, annual_appreciation_rate=Decimal("-1.00000"),
            ))
            with pytest.raises(IntegrityError):
                db.session.flush()
            db.session.rollback()

    def test_account_id_unique(self, app, db, seed_user, seed_periods_today):
        """Only one appreciation params row may exist per account."""
        with app.app_context():
            acct = _make_property(db, seed_user, seed_periods_today)
            db.session.add(AssetAppreciationParams(
                account_id=acct.id, annual_appreciation_rate=Decimal("0.03000"),
            ))
            db.session.commit()
            db.session.add(AssetAppreciationParams(
                account_id=acct.id, annual_appreciation_rate=Decimal("0.04000"),
            ))
            with pytest.raises(IntegrityError):
                db.session.flush()
            db.session.rollback()
