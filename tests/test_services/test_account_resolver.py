"""
Shekel Budget App — Account Resolver Tests

Tests the resolve_grid_account() fallback chain:
1. override_account_id
2. user_settings.default_grid_account_id
3. First active checking account (by sort_order, id)
4. First active account of any type
5. None
"""

from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.models.user import UserSettings
from app.services.account_resolver import resolve_grid_account


class TestResolveGridAccount:
    """Tests for resolve_grid_account()."""

    def test_no_setting_returns_checking(self, app, db, seed_user):
        """Without any setting, returns the first checking account."""
        with app.app_context():
            result = resolve_grid_account(seed_user["user"].id)
            assert result is not None
            assert result.id == seed_user["account"].id
            assert result.name == "Checking"

    def test_setting_configured_returns_that_account(self, app, db, seed_user):
        """When default_grid_account_id is set, returns that account."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="savings").one()
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                current_anchor_balance=Decimal("5000.00"),
            )
            db.session.add(savings)
            db.session.flush()

            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            settings.default_grid_account_id = savings.id
            db.session.commit()

            result = resolve_grid_account(seed_user["user"].id, settings)
            assert result.id == savings.id

    def test_inactive_configured_account_falls_back(self, app, db, seed_user):
        """When configured account is inactive, falls back to checking."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="savings").one()
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                is_active=False,
            )
            db.session.add(savings)
            db.session.flush()

            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            settings.default_grid_account_id = savings.id
            db.session.commit()

            result = resolve_grid_account(seed_user["user"].id, settings)
            assert result.id == seed_user["account"].id

    def test_override_takes_precedence(self, app, db, seed_user):
        """override_account_id takes priority over setting."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="savings").one()
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
            )
            db.session.add(savings)
            db.session.flush()

            # Set default to checking.
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            settings.default_grid_account_id = seed_user["account"].id
            db.session.commit()

            # Override to savings.
            result = resolve_grid_account(
                seed_user["user"].id, settings,
                override_account_id=savings.id,
            )
            assert result.id == savings.id

    def test_override_validates_ownership(self, app, db, seed_user):
        """Override with wrong user's account falls back."""
        with app.app_context():
            from app.models.user import User
            from werkzeug.security import generate_password_hash

            other_user = User(
                email="other@test.local",
                password_hash=generate_password_hash("pass"),
            )
            db.session.add(other_user)
            db.session.flush()

            checking_type = db.session.query(AccountType).filter_by(name="checking").one()
            other_acct = Account(
                user_id=other_user.id,
                account_type_id=checking_type.id,
                name="Other Checking",
            )
            db.session.add(other_acct)
            db.session.commit()

            result = resolve_grid_account(
                seed_user["user"].id, None,
                override_account_id=other_acct.id,
            )
            # Should NOT return other user's account; falls back to own checking.
            assert result.id == seed_user["account"].id

    def test_no_accounts_returns_none(self, app, db, seed_user):
        """When user has no active accounts, returns None."""
        with app.app_context():
            acct = db.session.get(Account, seed_user["account"].id)
            acct.is_active = False
            db.session.commit()

            result = resolve_grid_account(seed_user["user"].id)
            assert result is None

    def test_no_checking_returns_first_active(self, app, db, seed_user):
        """Without a checking account, returns first active of any type."""
        with app.app_context():
            # Deactivate checking.
            acct = db.session.get(Account, seed_user["account"].id)
            acct.is_active = False
            db.session.flush()

            savings_type = db.session.query(AccountType).filter_by(name="savings").one()
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
            )
            db.session.add(savings)
            db.session.commit()

            result = resolve_grid_account(seed_user["user"].id)
            assert result.id == savings.id

    def test_deterministic_ordering(self, app, db, seed_user):
        """With multiple checking accounts, returns the one with lowest sort_order then id."""
        with app.app_context():
            checking_type = db.session.query(AccountType).filter_by(name="checking").one()

            # The seed checking account has sort_order=0.  Create another with sort_order=0
            # but it will have a higher id.
            checking2 = Account(
                user_id=seed_user["user"].id,
                account_type_id=checking_type.id,
                name="Checking 2",
                sort_order=0,
            )
            db.session.add(checking2)
            db.session.commit()

            result = resolve_grid_account(seed_user["user"].id)
            # Should return the original (lower id).
            assert result.id == seed_user["account"].id

    def test_override_inactive_falls_back(self, app, db, seed_user):
        """Override with inactive account falls back to checking."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="savings").one()
            inactive = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Inactive Savings",
                is_active=False,
            )
            db.session.add(inactive)
            db.session.commit()

            result = resolve_grid_account(
                seed_user["user"].id, None,
                override_account_id=inactive.id,
            )
            assert result.id == seed_user["account"].id
