"""Tests for ``_validate_collateral_link`` (the home-equity "secured by" guard).

The validator points a secured liability at the Asset it is secured by.
It returns ``None`` when the link is legal (or cleared) and a
``(message, category)`` flash tuple otherwise.
"""

from decimal import Decimal

from app.models.asset_appreciation_params import AssetAppreciationParams  # noqa: F401
from app.models.ref import AccountType
from app.services import account_service
from app.utils.account_validation import _validate_collateral_link
from tests._test_helpers import create_loan_account


def _make_property(db, seed_user, periods, name="House"):
    """Create a Property (Asset) account for the seeded user."""
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


class TestValidateCollateralLink:
    """Each legality rule the validator enforces."""

    def test_none_clears_link(self, app, db, seed_user, seed_periods_today):
        """A ``None`` collateral id clears the link and always validates."""
        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            assert _validate_collateral_link(
                None, loan, seed_user["user"].id,
            ) is None

    def test_valid_property_on_loan(self, app, db, seed_user, seed_periods_today):
        """A same-owner Property securing a loan validates."""
        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            prop = _make_property(db, seed_user, seed_periods_today)
            assert _validate_collateral_link(
                prop.id, loan, seed_user["user"].id,
            ) is None

    def test_self_link_rejected(self, app, db, seed_user, seed_periods_today):
        """An account may not secure itself."""
        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            assert _validate_collateral_link(
                loan.id, loan, seed_user["user"].id,
            ) == ("An account cannot secure itself.", "danger")

    def test_cross_user_target_rejected(
        self, app, db, seed_user, seed_second_user, seed_periods_today,
    ):
        """A target owned by another user is rejected indistinguishably."""
        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            other = seed_second_user["account"]  # second user's account
            # Not-yours collapses into the same response as not-found so the
            # field cannot probe for another owner's account ids.
            assert _validate_collateral_link(
                other.id, loan, seed_user["user"].id,
            ) == ("Invalid linked account.", "danger")

    def test_nonexistent_target_rejected(self, app, db, seed_user, seed_periods_today):
        """A non-existent target id is rejected."""
        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            assert _validate_collateral_link(
                999999, loan, seed_user["user"].id,
            ) == ("Invalid linked account.", "danger")

    def test_non_asset_target_rejected(self, app, db, seed_user, seed_periods_today):
        """A liability target (another loan) is not a valid securing asset."""
        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            other_loan = create_loan_account(
                seed_user, db.session, name="Car Loan",
            )
            assert _validate_collateral_link(
                other_loan.id, loan, seed_user["user"].id,
            ) == ("The securing account must be an asset.", "danger")

    def test_non_amortizing_source_rejected(self, app, db, seed_user, seed_periods_today):
        """Only an amortizing liability can be secured by an asset."""
        with app.app_context():
            checking = seed_user["account"]  # Checking: not amortizing
            prop = _make_property(db, seed_user, seed_periods_today)
            assert _validate_collateral_link(
                prop.id, checking, seed_user["user"].id,
            ) == ("Only a loan can be secured by an asset.", "danger")
