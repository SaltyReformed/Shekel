"""
Shekel Budget App — Auth Service Tests

Tests for password hashing, verification, user authentication,
and user registration (§1.10 of the test plan).
"""

import pytest
from decimal import Decimal

from app.extensions import db
from app.models.user import User, UserSettings
from app.models.category import Category
from app.models.scenario import Scenario
from app.models.tax_config import FicaConfig, StateTaxConfig, TaxBracketSet
from app.services import auth_service
from app.exceptions import AuthError, ConflictError, ValidationError


class TestHashPassword:
    """Tests for the hash_password() function."""

    def test_hash_password_returns_bcrypt_hash(self):
        """hash_password() returns a valid bcrypt hash string."""
        hashed = auth_service.hash_password("mypassword")

        # bcrypt hashes start with $2b$ (or $2a$) and are 60 chars.
        assert hashed.startswith("$2b$")
        assert len(hashed) == 60


class TestVerifyPassword:
    """Tests for the verify_password() function."""

    def test_verify_password_returns_true_for_correct_password(self):
        """verify_password() returns True when the password matches the hash."""
        hashed = auth_service.hash_password("correcthorse")

        assert auth_service.verify_password("correcthorse", hashed) is True

    def test_verify_password_returns_false_for_wrong_password(self):
        """verify_password() returns False when the password does not match."""
        hashed = auth_service.hash_password("correcthorse")

        assert auth_service.verify_password("wronghorse", hashed) is False


class TestAuthenticate:
    """Tests for the authenticate() function."""

    def test_authenticate_returns_user_on_valid_credentials(
        self, app, db, seed_user
    ):
        """authenticate() returns the User object for valid email + password."""
        with app.app_context():
            user = auth_service.authenticate("test@shekel.local", "testpass")

            assert user.id == seed_user["user"].id
            assert user.email == "test@shekel.local"

    def test_authenticate_raises_auth_error_on_wrong_email(
        self, app, db, seed_user
    ):
        """authenticate() raises AuthError when the email does not exist."""
        with app.app_context():
            with pytest.raises(AuthError, match="Invalid email or password"):
                auth_service.authenticate("nobody@shekel.local", "testpass")

    def test_authenticate_raises_auth_error_on_wrong_password(
        self, app, db, seed_user
    ):
        """authenticate() raises AuthError when the password is wrong."""
        with app.app_context():
            with pytest.raises(AuthError, match="Invalid email or password"):
                auth_service.authenticate("test@shekel.local", "wrongpass")

    def test_authenticate_raises_auth_error_on_disabled_account(
        self, app, db, seed_user
    ):
        """authenticate() raises AuthError when the account is disabled."""
        with app.app_context():
            # Disable the user account.
            user = db.session.get(User, seed_user["user"].id)
            user.is_active = False
            db.session.flush()

            with pytest.raises(AuthError, match="Account is disabled"):
                auth_service.authenticate("test@shekel.local", "testpass")


class TestChangePassword:
    """Tests for auth_service.change_password()."""

    def test_change_password_success(self, app, db, seed_user):
        """change_password() updates the password hash."""
        with app.app_context():
            user = seed_user["user"]
            auth_service.change_password(user, "testpass", "newpassword12")
            db.session.flush()

            # Verify the new password works and old one does not.
            assert auth_service.verify_password("newpassword12", user.password_hash)
            assert not auth_service.verify_password("testpass", user.password_hash)

    def test_change_password_wrong_current_raises(self, app, db, seed_user):
        """change_password() raises AuthError for wrong current password."""
        with app.app_context():
            user = seed_user["user"]
            with pytest.raises(AuthError, match="Current password is incorrect"):
                auth_service.change_password(user, "wrongpass", "newpassword12")

    def test_change_password_too_short_raises(self, app, db, seed_user):
        """change_password() raises ValidationError for short password."""
        with app.app_context():
            user = seed_user["user"]
            with pytest.raises(ValidationError, match="at least 12 characters"):
                auth_service.change_password(user, "testpass", "short")


class TestRegisterUser:
    """Tests for auth_service.register_user()."""

    def test_register_user_creates_user(self, app, db):
        """register_user() creates a User, UserSettings, and baseline Scenario.

        Verifies all three records exist in the database with the correct
        field values after a successful registration.
        """
        with app.app_context():
            user = auth_service.register_user(
                "newuser@example.com", "securepass123", "New User"
            )
            db.session.flush()

            assert user.id is not None
            assert user.email == "newuser@example.com"
            assert user.display_name == "New User"

            # Exactly one UserSettings row for this user.
            settings = db.session.query(UserSettings).filter_by(
                user_id=user.id
            ).all()
            assert len(settings) == 1

            # Exactly one baseline Scenario for this user.
            scenarios = db.session.query(Scenario).filter_by(
                user_id=user.id
            ).all()
            assert len(scenarios) == 1
            assert scenarios[0].is_baseline is True
            assert scenarios[0].name == "Baseline"

    def test_register_user_password_is_bcrypt_hashed(self, app, db):
        """register_user() stores a bcrypt hash, not the plaintext password.

        Verifies the hash starts with $2b$ (bcrypt identifier) and that
        verify_password() can round-trip the original plaintext.
        """
        with app.app_context():
            plaintext = "securepass123"
            user = auth_service.register_user(
                "hash@example.com", plaintext, "Hash Test"
            )
            db.session.flush()

            assert user.password_hash != plaintext
            assert user.password_hash.startswith("$2b$")
            assert auth_service.verify_password(plaintext, user.password_hash)

    def test_register_user_settings_have_correct_defaults(self, app, db):
        """register_user() creates UserSettings with the model-defined defaults.

        Checks each default value matches the column definition in the
        UserSettings model.
        """
        with app.app_context():
            user = auth_service.register_user(
                "defaults@example.com", "securepass123", "Defaults Test"
            )
            db.session.flush()

            settings = db.session.query(UserSettings).filter_by(
                user_id=user.id
            ).one()

            assert settings.default_inflation_rate == Decimal("0.0300")
            assert settings.grid_default_periods == 6
            assert settings.low_balance_threshold == 500
            assert settings.safe_withdrawal_rate == Decimal("0.0400")

    def test_register_user_email_is_lowercased(self, app, db):
        """register_user() lowercases the email before storage.

        Email addresses are case-insensitive per RFC 5321, so storage
        must be normalized to prevent login mismatches.
        """
        with app.app_context():
            user = auth_service.register_user(
                "UPPER@EXAMPLE.COM", "securepass123", "Upper Test"
            )
            db.session.flush()

            assert user.email == "upper@example.com"

    def test_register_user_email_is_stripped(self, app, db):
        """register_user() strips whitespace from email before storage."""
        with app.app_context():
            user = auth_service.register_user(
                "  spaced@example.com  ", "securepass123", "Spaced Test"
            )
            db.session.flush()

            assert user.email == "spaced@example.com"

    def test_register_user_display_name_is_stripped(self, app, db):
        """register_user() strips whitespace from display_name before storage."""
        with app.app_context():
            user = auth_service.register_user(
                "strip@example.com", "securepass123", "  Padded Name  "
            )
            db.session.flush()

            assert user.display_name == "Padded Name"

    def test_register_user_duplicate_email_raises_conflict(
        self, app, db, seed_user
    ):
        """register_user() raises ConflictError for a duplicate email.

        Uses the seed_user fixture which has email test@shekel.local.
        """
        with app.app_context():
            with pytest.raises(ConflictError, match="already exists"):
                auth_service.register_user(
                    "test@shekel.local", "securepass123", "Dup Test"
                )

    def test_register_user_duplicate_email_case_insensitive(
        self, app, db, seed_user
    ):
        """register_user() detects duplicates even with different casing.

        Verifies that email lowercasing happens before the uniqueness
        check, so 'TEST@SHEKEL.LOCAL' conflicts with 'test@shekel.local'.
        """
        with app.app_context():
            with pytest.raises(ConflictError):
                auth_service.register_user(
                    "TEST@SHEKEL.LOCAL", "securepass123", "Case Test"
                )

    def test_register_user_short_password_raises_validation(self, app, db):
        """register_user() raises ValidationError for passwords under 12 chars."""
        with app.app_context():
            with pytest.raises(ValidationError, match="at least 12 characters"):
                auth_service.register_user(
                    "short@example.com", "12345678901", "Short Test"
                )

    def test_register_user_exactly_12_chars_succeeds(self, app, db):
        """register_user() accepts a password that is exactly 12 characters."""
        with app.app_context():
            user = auth_service.register_user(
                "exact@example.com", "123456789012", "Exact Test"
            )
            db.session.flush()

            assert user.id is not None

    def test_register_user_invalid_email_no_at_sign(self, app, db):
        """register_user() rejects an email with no @ sign."""
        with app.app_context():
            with pytest.raises(ValidationError, match="Invalid email format"):
                auth_service.register_user(
                    "notanemail", "securepass123", "No At Test"
                )

    def test_register_user_invalid_email_no_domain(self, app, db):
        """register_user() rejects an email with no domain after @."""
        with app.app_context():
            with pytest.raises(ValidationError, match="Invalid email format"):
                auth_service.register_user(
                    "user@", "securepass123", "No Domain Test"
                )

    def test_register_user_invalid_email_no_tld(self, app, db):
        """register_user() rejects an email with no TLD (no dot in domain)."""
        with app.app_context():
            with pytest.raises(ValidationError, match="Invalid email format"):
                auth_service.register_user(
                    "user@domain", "securepass123", "No TLD Test"
                )

    def test_register_user_invalid_email_spaces(self, app, db):
        """register_user() rejects an email with internal spaces.

        After stripping, 'user @example.com' still has an internal space
        which the regex rejects.
        """
        with app.app_context():
            with pytest.raises(ValidationError, match="Invalid email format"):
                auth_service.register_user(
                    "user @example.com", "securepass123", "Space Test"
                )

    def test_register_user_empty_email_raises_validation(self, app, db):
        """register_user() raises ValidationError for an empty email."""
        with app.app_context():
            with pytest.raises(ValidationError, match="Invalid email format"):
                auth_service.register_user(
                    "", "securepass123", "Empty Email Test"
                )

    def test_register_user_empty_display_name_raises_validation(self, app, db):
        """register_user() raises ValidationError for an empty display name."""
        with app.app_context():
            with pytest.raises(ValidationError, match="Display name is required"):
                auth_service.register_user(
                    "empty@example.com", "securepass123", ""
                )

    def test_register_user_whitespace_display_name_raises_validation(
        self, app, db
    ):
        """register_user() raises ValidationError for a whitespace-only display name.

        After stripping, the display name becomes empty and is rejected.
        """
        with app.app_context():
            with pytest.raises(ValidationError, match="Display name is required"):
                auth_service.register_user(
                    "ws@example.com", "securepass123", "   "
                )

    def test_register_user_does_not_commit(self, app, db):
        """register_user() does not commit the transaction.

        The caller (route) is responsible for committing. Rolling back
        after register_user() returns should discard the new user.
        """
        with app.app_context():
            user = auth_service.register_user(
                "nocommit@example.com", "securepass123", "No Commit"
            )
            db.session.rollback()

            # The user should not exist after rollback.
            found = db.session.query(User).filter_by(
                email="nocommit@example.com"
            ).first()
            assert found is None

    def test_register_user_validation_order_email_before_password(
        self, app, db
    ):
        """register_user() validates email format before password length.

        When both email and password are invalid, the email error fires
        first because validation runs in a defined order.
        """
        with app.app_context():
            with pytest.raises(ValidationError, match="email") as exc_info:
                auth_service.register_user(
                    "notvalid", "short", "Order Test"
                )
            # Confirm it is the email error, not the password error.
            assert "12 characters" not in str(exc_info.value)

    def test_register_user_creates_default_categories(self, app, db):
        """register_user() creates 22 default categories for the new user."""
        with app.app_context():
            user = auth_service.register_user(
                "cats@example.com", "securepass123", "Category Test"
            )
            db.session.flush()

            categories = db.session.query(Category).filter_by(
                user_id=user.id
            ).all()
            assert len(categories) == 22

    def test_register_user_categories_have_correct_groups(self, app, db):
        """register_user() creates categories spanning all expected groups."""
        with app.app_context():
            user = auth_service.register_user(
                "groups@example.com", "securepass123", "Group Test"
            )
            db.session.flush()

            categories = db.session.query(Category).filter_by(
                user_id=user.id
            ).all()
            groups = {c.group_name for c in categories}
            assert groups == {
                "Income", "Home", "Auto", "Family",
                "Health", "Financial", "Credit Card",
            }

    def test_register_user_categories_include_income_salary(self, app, db):
        """register_user() creates the Income: Salary category needed for salary profiles."""
        with app.app_context():
            user = auth_service.register_user(
                "salary@example.com", "securepass123", "Salary Cat Test"
            )
            db.session.flush()

            salary_cat = db.session.query(Category).filter_by(
                user_id=user.id, group_name="Income", item_name="Salary"
            ).first()
            assert salary_cat is not None

    def test_register_user_categories_have_sort_order(self, app, db):
        """register_user() assigns sequential sort_order to categories."""
        with app.app_context():
            user = auth_service.register_user(
                "sort@example.com", "securepass123", "Sort Test"
            )
            db.session.flush()

            categories = db.session.query(Category).filter_by(
                user_id=user.id
            ).order_by(Category.sort_order).all()
            orders = [c.sort_order for c in categories]
            assert orders == list(range(22))

    def test_register_user_categories_rollback_on_failure(self, app, db):
        """register_user() categories are discarded on transaction rollback."""
        with app.app_context():
            auth_service.register_user(
                "rollback@example.com", "securepass123", "Rollback Test"
            )
            db.session.rollback()

            count = db.session.query(Category).filter_by(
                group_name="Income", item_name="Salary"
            ).count()
            assert count == 0

    def test_register_user_creates_federal_tax_brackets(self, app, db):
        """register_user() creates federal tax bracket sets for 2025 and 2026."""
        with app.app_context():
            user = auth_service.register_user(
                "tax@example.com", "securepass123", "Tax Test"
            )
            db.session.flush()

            bracket_sets = db.session.query(TaxBracketSet).filter_by(
                user_id=user.id
            ).all()
            # 4 filing statuses x 2 years = 8 bracket sets
            assert len(bracket_sets) == 8
            years = {bs.tax_year for bs in bracket_sets}
            assert years == {2025, 2026}

    def test_register_user_creates_fica_config(self, app, db):
        """register_user() creates FICA configs for 2025 and 2026."""
        with app.app_context():
            user = auth_service.register_user(
                "fica@example.com", "securepass123", "FICA Test"
            )
            db.session.flush()

            fica_configs = db.session.query(FicaConfig).filter_by(
                user_id=user.id
            ).all()
            assert len(fica_configs) == 2
            years = {fc.tax_year for fc in fica_configs}
            assert years == {2025, 2026}

    def test_register_user_creates_state_tax_config(self, app, db):
        """register_user() creates a default NC state tax config."""
        with app.app_context():
            user = auth_service.register_user(
                "state@example.com", "securepass123", "State Test"
            )
            db.session.flush()

            state_configs = db.session.query(StateTaxConfig).filter_by(
                user_id=user.id
            ).all()
            assert len(state_configs) == 1
            assert state_configs[0].state_code == "NC"
