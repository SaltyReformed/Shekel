"""
Shekel Budget App -- Auth Service Tests

Tests for password hashing, verification, user authentication,
user registration, account lockout (F-033 / C-11), and the HIBP
breached-password check (F-086 / C-11).  See ``§1.10`` of the test
plan for the original auth-service coverage; the lockout and HIBP
sections were added in commit C-11 of the 2026-04-15 security
remediation plan.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import requests

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
        """register_user() creates 24 default categories for the new user."""
        with app.app_context():
            user = auth_service.register_user(
                "cats@example.com", "securepass123", "Category Test"
            )
            db.session.flush()

            categories = db.session.query(Category).filter_by(
                user_id=user.id
            ).all()
            assert len(categories) == 24

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
                "Health", "Financial", "Transfers", "Credit Card",
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

    def test_default_categories_include_transfers(self, app, db):
        """DEFAULT_CATEGORIES list contains the two transfer categories."""
        from app.services.auth_service import DEFAULT_CATEGORIES
        assert ("Transfers", "Incoming") in DEFAULT_CATEGORIES
        assert ("Transfers", "Outgoing") in DEFAULT_CATEGORIES

    def test_seed_user_creates_transfer_categories(self, app, db):
        """register_user() creates both Transfers: Incoming and Transfers: Outgoing."""
        with app.app_context():
            user = auth_service.register_user(
                "xfer_cats@example.com", "securepass123", "Transfer Cat Test"
            )
            db.session.flush()

            transfer_cats = db.session.query(Category).filter_by(
                user_id=user.id, group_name="Transfers"
            ).all()
            assert len(transfer_cats) == 2
            item_names = {c.item_name for c in transfer_cats}
            assert item_names == {"Incoming", "Outgoing"}

    def test_transfer_categories_have_valid_sort_order(self, app, db):
        """Transfer categories have unique, non-null sort_order values."""
        with app.app_context():
            user = auth_service.register_user(
                "xfer_sort@example.com", "securepass123", "Transfer Sort Test"
            )
            db.session.flush()

            all_cats = db.session.query(Category).filter_by(
                user_id=user.id
            ).all()
            all_orders = [c.sort_order for c in all_cats]

            # All sort_order values are non-null.
            assert all(o is not None for o in all_orders)
            # All sort_order values are unique (no collisions).
            assert len(all_orders) == len(set(all_orders))

            # Transfer categories specifically have valid sort_order.
            transfer_cats = [c for c in all_cats if c.group_name == "Transfers"]
            for cat in transfer_cats:
                assert cat.sort_order is not None

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
            assert orders == list(range(24))

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
        """register_user() creates default NC state tax configs (one per year)."""
        with app.app_context():
            user = auth_service.register_user(
                "state@example.com", "securepass123", "State Test"
            )
            db.session.flush()

            state_configs = db.session.query(StateTaxConfig).filter_by(
                user_id=user.id
            ).all()
            assert len(state_configs) == len(auth_service.DEFAULT_STATE_TAX)
            assert all(sc.state_code == "NC" for sc in state_configs)


class TestNegativeAndBoundaryPaths:
    """Negative-path and boundary-condition tests for auth service functions.

    Covers: empty/long passwords, None inputs to authenticate, password
    change boundary conditions, and bcrypt truncation behavior.
    """

    def test_hash_password_empty_string(self):
        """Hashing an empty string returns a valid bcrypt hash.

        An empty password should not crash the hashing layer. Whether the app
        should allow empty passwords is a route-level validation concern, not
        a service-level one.
        """
        hashed = auth_service.hash_password("")
        assert hashed.startswith("$2b$")
        assert len(hashed) == 60

        # Round-trip verification.
        assert auth_service.verify_password("", hashed) is True

    def test_hash_password_long_bcrypt_limit(self):
        """hash_password() rejects passwords exceeding 72 bytes.

        bcrypt only processes the first 72 bytes of a password. Rather than
        silently truncating, we raise a ValidationError so the user knows
        their full password is not being used.
        """
        long_password = "a" * 73  # One byte over bcrypt's 72-byte limit.
        with pytest.raises(ValidationError, match="72 characters or fewer"):
            auth_service.hash_password(long_password)

    def test_hash_password_exactly_72_bytes(self):
        """A password of exactly 72 bytes is accepted."""
        password_72 = "a" * 72
        hashed = auth_service.hash_password(password_72)
        assert hashed.startswith("$2b$")
        assert auth_service.verify_password(password_72, hashed) is True

    def test_authenticate_none_email(self, app, db, seed_user):
        """Authenticating with None email raises AuthError, not a TypeError.

        A malformed request could pass None. The service must raise AuthError,
        not an AttributeError or TypeError. filter_by(email=None) returns None
        (no user with email=None), which triggers the generic error.
        """
        with app.app_context():
            with pytest.raises(AuthError, match="Invalid email or password"):
                auth_service.authenticate(None, "testpass")

    def test_authenticate_none_password(self, app, db, seed_user):
        """Authenticating with None password raises AuthError, not AttributeError.

        verify_password() guards against None input by returning False,
        which causes authenticate() to raise AuthError normally.
        """
        with app.app_context():
            with pytest.raises(AuthError, match="Invalid email or password"):
                auth_service.authenticate("test@shekel.local", None)

    def test_change_password_new_equals_old(self, app, db, seed_user):
        """Changing to the same password succeeds -- no password-history enforcement.

        The service does not prevent changing to the same password. This is a
        UX concern, not a security one. bcrypt generates a new salt each time,
        so the hash differs even though the password is identical.
        """
        with app.app_context():
            user = seed_user["user"]
            # First change to a valid-length password.
            auth_service.change_password(user, "testpass", "validpass1234")
            db.session.flush()
            old_hash = user.password_hash

            # Now change to the same password.
            auth_service.change_password(user, "validpass1234", "validpass1234")
            db.session.flush()

            # The hash changed (new salt) even though the password is the same.
            assert user.password_hash != old_hash
            assert auth_service.verify_password("validpass1234", user.password_hash) is True

    def test_change_password_exactly_12_characters(self, app, db, seed_user):
        """A new password of exactly 12 characters passes the length check.

        Off-by-one on the minimum length is a classic validation bug. The
        guard is ``len(new_password) < 12``, so exactly 12 should pass.
        """
        with app.app_context():
            user = seed_user["user"]
            # "exactly12chr" is exactly 12 characters.
            auth_service.change_password(user, "testpass", "exactly12chr")
            db.session.flush()

            assert auth_service.verify_password("exactly12chr", user.password_hash) is True

    def test_change_password_11_characters_raises(self, app, db, seed_user):
        """A new password of 11 characters raises ValidationError.

        Ensures the 12-character boundary is enforced correctly.
        """
        with app.app_context():
            user = seed_user["user"]
            with pytest.raises(ValidationError, match="at least 12 characters"):
                auth_service.change_password(user, "testpass", "short11char")

    def test_authenticate_empty_string_email(self, app, db, seed_user):
        """Authenticating with empty-string email raises AuthError.

        Empty string is different from None. Both must be handled.
        filter_by(email='') returns None (no user with empty email), which
        triggers the generic AuthError.
        """
        with app.app_context():
            with pytest.raises(AuthError, match="Invalid email or password"):
                auth_service.authenticate("", "testpass")


# ---------------------------------------------------------------------------
# Account lockout (audit finding F-033 / commit C-11)
# ---------------------------------------------------------------------------


class TestAccountLockoutHelpers:
    """Tests for the lockout-config helpers in auth_service."""

    def test_default_threshold_is_ten(self, monkeypatch):
        """Without an env override, the threshold defaults to 10.

        10 was chosen in the C-11 plan to leave a comfortable margin
        for typo storms while clamping a credential-stuffing attack
        to a single attempt per lockout window per account.
        """
        monkeypatch.delenv("LOCKOUT_THRESHOLD", raising=False)
        assert auth_service._get_lockout_threshold() == 10

    def test_default_duration_is_15_minutes(self, monkeypatch):
        """Without an env override, the lockout duration is 15 minutes."""
        monkeypatch.delenv("LOCKOUT_DURATION_MINUTES", raising=False)
        assert auth_service._get_lockout_duration() == timedelta(minutes=15)

    def test_threshold_env_override_takes_effect(self, monkeypatch):
        """LOCKOUT_THRESHOLD env var is read at call time.

        Reading at call time (rather than at module import) is required
        so that the test suite and operators can change the threshold
        without restarting Python.
        """
        monkeypatch.setenv("LOCKOUT_THRESHOLD", "3")
        assert auth_service._get_lockout_threshold() == 3

    def test_duration_env_override_takes_effect(self, monkeypatch):
        """LOCKOUT_DURATION_MINUTES env var is read at call time."""
        monkeypatch.setenv("LOCKOUT_DURATION_MINUTES", "1")
        assert auth_service._get_lockout_duration() == timedelta(minutes=1)

    def test_threshold_zero_rejected(self, monkeypatch):
        """A non-positive threshold is rejected with a clear error.

        Zero or negative would lock immediately on the first wrong
        password, locking out legitimate users on a single typo.
        Treat as a config error rather than a working config.
        """
        monkeypatch.setenv("LOCKOUT_THRESHOLD", "0")
        with pytest.raises(ValueError, match="positive integer"):
            auth_service._get_lockout_threshold()

    def test_duration_zero_rejected(self, monkeypatch):
        """A non-positive duration is rejected with a clear error."""
        monkeypatch.setenv("LOCKOUT_DURATION_MINUTES", "0")
        with pytest.raises(ValueError, match="positive integer"):
            auth_service._get_lockout_duration()


class TestAccountLockoutBehaviour:
    """End-to-end tests for the lockout flow inside ``authenticate``.

    Every test re-fetches the seed user inside its own ``app_context``
    via ``db.session.get`` because the ``seed_user`` fixture's session
    closes after fixture exit; the User reference in
    ``seed_user["user"]`` is detached from the test's session and any
    in-place mutation on it would not propagate to the database.
    Mirrors the same pattern in ``test_authenticate_raises_auth_error_on_disabled_account``
    above.
    """

    def test_failed_login_increments_counter(self, app, db, seed_user):
        """Each wrong password bumps ``failed_login_count`` and commits.

        Verifies the counter survives the transactional boundary and
        the next call observes the persisted value.  Without the
        commit inside ``authenticate`` the counter would only live in
        the session and reset to 0 on the next request.
        """
        user_id = seed_user["user"].id
        with app.app_context():
            with pytest.raises(AuthError):
                auth_service.authenticate(
                    "test@shekel.local", "wrong-password",
                )

            user = db.session.get(User, user_id)
            assert user.failed_login_count == 1
            assert user.locked_until is None

    def test_three_consecutive_failures_increment_to_three(
        self, app, db, seed_user, monkeypatch,
    ):
        """Counter increments monotonically on consecutive failures.

        Three failures with a threshold of 5 leaves the counter at 3
        and the account NOT locked.  Anchors the threshold-trip test
        below: at exactly 5 failures the lockout fires.
        """
        monkeypatch.setenv("LOCKOUT_THRESHOLD", "5")
        user_id = seed_user["user"].id
        with app.app_context():
            for _ in range(3):
                with pytest.raises(AuthError):
                    auth_service.authenticate(
                        "test@shekel.local", "wrong-password",
                    )
            user = db.session.get(User, user_id)
            assert user.failed_login_count == 3
            assert user.locked_until is None

    def test_threshold_failures_set_locked_until(
        self, app, db, seed_user, monkeypatch,
    ):
        """Reaching the threshold sets ``locked_until`` and zeroes the counter.

        With threshold=3 the third failure trips the lockout.  The
        plan specifies the counter is reset on lockout-trip so a second
        lockout cycle requires another threshold-many failures rather
        than just one extra after the window expires.
        """
        monkeypatch.setenv("LOCKOUT_THRESHOLD", "3")
        monkeypatch.setenv("LOCKOUT_DURATION_MINUTES", "15")
        user_id = seed_user["user"].id
        with app.app_context():
            before = datetime.now(timezone.utc)
            for _ in range(3):
                with pytest.raises(AuthError):
                    auth_service.authenticate(
                        "test@shekel.local", "wrong-password",
                    )
            user = db.session.get(User, user_id)
            assert user.failed_login_count == 0
            assert user.locked_until is not None
            # locked_until must be approximately now + 15 min.  Use a
            # tolerant range to absorb the test runtime; 14..16 minutes
            # is plenty of slack for any plausible CI box.
            delta = user.locked_until - before
            assert timedelta(minutes=14) < delta < timedelta(minutes=16)

    def test_locked_account_rejects_correct_password(
        self, app, db, seed_user,
    ):
        """A locked account refuses even the correct password.

        This is the timing-oracle defence: while ``locked_until > now``
        the service does NOT call ``verify_password``, so an attacker
        observing response timing cannot distinguish "locked + right"
        from "locked + wrong" and cannot use a captured-but-then-
        locked window to confirm a guessed password.  Verified by
        feeding a known-correct password and confirming AuthError.
        """
        user_id = seed_user["user"].id
        with app.app_context():
            user = db.session.get(User, user_id)
            user.locked_until = (
                datetime.now(timezone.utc) + timedelta(minutes=10)
            )
            db.session.commit()

            with pytest.raises(AuthError, match="Invalid email or password"):
                auth_service.authenticate("test@shekel.local", "testpass")

    def test_locked_account_does_not_increment_counter(
        self, app, db, seed_user,
    ):
        """A locked account's counter is NOT incremented during the window.

        This matters because the service zeroes the counter at lockout-
        trip; we do not want re-locking to happen after a single retry
        during the window.  The retry-while-locked path returns early
        without touching the counter.
        """
        user_id = seed_user["user"].id
        with app.app_context():
            user = db.session.get(User, user_id)
            user.locked_until = (
                datetime.now(timezone.utc) + timedelta(minutes=10)
            )
            user.failed_login_count = 0
            db.session.commit()

            for _ in range(5):
                with pytest.raises(AuthError):
                    auth_service.authenticate(
                        "test@shekel.local", "anything",
                    )
            user = db.session.get(User, user_id)
            assert user.failed_login_count == 0

    def test_expired_lockout_allows_correct_password(
        self, app, db, seed_user,
    ):
        """A lockout in the past does not block authentication.

        ``locked_until`` is an exclusive upper bound: at the instant
        the column equals ``now`` the lockout is over.  Set the column
        a second into the past and verify a correct password succeeds,
        confirming the strict-greater-than gate.
        """
        user_id = seed_user["user"].id
        with app.app_context():
            user = db.session.get(User, user_id)
            user.locked_until = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            )
            user.failed_login_count = 0
            db.session.commit()

            result = auth_service.authenticate("test@shekel.local", "testpass")
            assert result.id == user_id

            user = db.session.get(User, user_id)
            # Successful authentication clears any residual lockout.
            assert user.locked_until is None
            assert user.failed_login_count == 0

    def test_successful_login_resets_counter(self, app, db, seed_user):
        """A successful login zeroes the counter built up by typos.

        Without this reset, a user who typoed three times then logged
        in would still be three failures away from a fresh lockout
        instead of starting from zero.
        """
        user_id = seed_user["user"].id
        with app.app_context():
            for _ in range(3):
                with pytest.raises(AuthError):
                    auth_service.authenticate(
                        "test@shekel.local", "wrong-password",
                    )
            user = db.session.get(User, user_id)
            assert user.failed_login_count == 3

            auth_service.authenticate("test@shekel.local", "testpass")
            user = db.session.get(User, user_id)
            assert user.failed_login_count == 0
            assert user.locked_until is None

    def test_successful_login_no_op_commit_on_clean_user(
        self, app, db, seed_user,
    ):
        """A login on an account with no failure history does not write.

        The service skips the commit when both ``failed_login_count``
        and ``locked_until`` are already at their cleared values.  This
        is a small efficiency concern: every login on a fresh account
        would otherwise trigger an unnecessary UPDATE.  Verified by
        comparing ``updated_at`` before and after; the TimestampMixin
        updates it on every flush, so an absence of change proves no
        UPDATE was issued.
        """
        user_id = seed_user["user"].id
        with app.app_context():
            user_before = db.session.get(User, user_id)
            original_updated_at = user_before.updated_at
            db.session.expunge(user_before)

            auth_service.authenticate("test@shekel.local", "testpass")

            user_after = db.session.get(User, user_id)
            assert user_after.updated_at == original_updated_at

    def test_lockout_stores_timezone_aware_datetime(
        self, app, db, seed_user, monkeypatch,
    ):
        """``locked_until`` is timezone-aware (UTC), not a naive datetime.

        Comparing a naive datetime to ``datetime.now(timezone.utc)`` in
        the lockout-gate check would raise TypeError under Python's
        strict comparison rules.  This test guards against a future
        edit that drops the ``timezone.utc`` argument.
        """
        monkeypatch.setenv("LOCKOUT_THRESHOLD", "1")
        user_id = seed_user["user"].id
        with app.app_context():
            with pytest.raises(AuthError):
                auth_service.authenticate(
                    "test@shekel.local", "wrong-password",
                )
            user = db.session.get(User, user_id)
            assert user.locked_until is not None
            assert user.locked_until.tzinfo is not None

    def test_check_constraint_rejects_negative_count(self, app, db, seed_user):
        """The CHECK constraint blocks writes of a negative count.

        Verifies the schema-level guard documented on
        ``ck_users_failed_login_count_non_negative``.  A future
        backfill or buggy migration that wrote a negative value would
        otherwise silently invert the lockout logic.  The test
        asserts the constraint name appears in the IntegrityError so a
        future rename is caught.
        """
        from sqlalchemy.exc import IntegrityError  # pylint: disable=import-outside-toplevel
        user_id = seed_user["user"].id
        with app.app_context():
            user = db.session.get(User, user_id)
            user.failed_login_count = -1
            with pytest.raises(
                IntegrityError,
                match="ck_users_failed_login_count_non_negative",
            ):
                db.session.commit()
            db.session.rollback()


# ---------------------------------------------------------------------------
# HIBP breached-password check (audit finding F-086 / commit C-11)
# ---------------------------------------------------------------------------


def _hibp_response_for(plain_password, count=1, extra_lines=()):
    """Build a fake HIBP API response body containing the given password.

    HIBP returns one suffix-and-count pair per line.  The full SHA-1 of
    the password is split into prefix/suffix; this helper constructs a
    response in the format the API would return for a positive match,
    plus optional decoy lines.

    Args:
        plain_password: The plaintext to embed (the suffix is computed
            from its SHA-1).
        count: The breach count to associate with the matched suffix.
            Tests use 1 or higher; zero counts are HIBP padding noise
            and the service treats them like normal lines (the suffix
            still raises if it matches).
        extra_lines: Iterable of additional ``(suffix, count)`` tuples
            mixed into the response to simulate real HIBP output.

    Returns:
        str: The complete response body in HIBP format.
    """
    sha1 = hashlib.sha1(plain_password.encode("utf-8")).hexdigest().upper()
    suffix = sha1[5:]
    lines = [f"{suffix}:{count}"]
    for extra_suffix, extra_count in extra_lines:
        lines.append(f"{extra_suffix}:{extra_count}")
    return "\r\n".join(lines)


class _FakeHibpResponse:
    """Minimal stand-in for ``requests.Response`` used in HIBP tests.

    Matches the subset of the real interface that
    ``_check_pwned_password`` uses: ``text`` and ``raise_for_status``.
    Constructing real ``requests.Response`` instances would require
    extra plumbing; this stub keeps the test setup obvious.
    """

    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        """Mimic requests.Response.raise_for_status for the 4xx path."""
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class TestHibpCheck:
    """Tests for the HIBP k-anonymity breach check."""

    def test_check_disabled_skips_request(self, monkeypatch):
        """When HIBP_CHECK_ENABLED=false, no HTTP call is issued.

        The conftest autouse fixture sets this to false so the rest
        of the suite never hits HIBP.  This test confirms the toggle
        actually short-circuits.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "false")
        called = {"count": 0}

        def _spy(*_args, **_kwargs):
            called["count"] += 1
            return _FakeHibpResponse("")

        monkeypatch.setattr(requests, "get", _spy)
        # No exception even on a known-breached password because we
        # never contact HIBP.
        auth_service._check_pwned_password("password")
        assert called["count"] == 0

    def test_breached_password_raises_validation_error(self, monkeypatch):
        """A password whose hash matches a HIBP record is rejected.

        Mocks ``requests.get`` to return a body containing the
        password's SHA-1 suffix.  The function must raise
        ``ValidationError`` with the user-facing message that does
        NOT name HIBP or quote the breach count (avoid leaking the
        upstream identity to a curious user).
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        password = "definitely-breached-password"
        body = _hibp_response_for(password, count=42)
        monkeypatch.setattr(
            requests, "get",
            lambda *args, **kwargs: _FakeHibpResponse(body),
        )
        with pytest.raises(ValidationError, match="known data breach"):
            auth_service._check_pwned_password(password)

    def test_clean_password_passes(self, monkeypatch):
        """A password whose suffix is absent from the response is accepted.

        Sends a body containing only decoy lines so the search loop
        completes without finding the real suffix.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        decoys = "\r\n".join(f"{'A' * 35}:{i}" for i in range(1, 6))
        monkeypatch.setattr(
            requests, "get",
            lambda *args, **kwargs: _FakeHibpResponse(decoys),
        )
        # Returns None on accept; absence of exception is the primary
        # assertion.  Pylint flags ``assignment-from-none`` because the
        # function's documented return-type is None on the happy path,
        # but the test deliberately captures the value to make the
        # contract self-documenting in the test source.
        result = auth_service._check_pwned_password(  # pylint: disable=assignment-from-none
            "this-passphrase-is-clean",
        )
        assert result is None

    def test_network_error_fails_open(self, monkeypatch):
        """A requests.RequestException is logged and treated as accept.

        Fail-open is the documented posture: a transient HIBP outage
        must not stop a legitimate user from registering.  The
        warning log is the operator-side signal that breach-check is
        currently degraded.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")

        def _raise_timeout(*_args, **_kwargs):
            raise requests.Timeout("simulated timeout")

        monkeypatch.setattr(requests, "get", _raise_timeout)
        # Returns None and does NOT raise.
        result = auth_service._check_pwned_password("any-password")  # pylint: disable=assignment-from-none
        assert result is None

    def test_5xx_response_fails_open(self, monkeypatch):
        """A 5xx response from HIBP is logged and treated as accept.

        ``requests.Response.raise_for_status`` raises ``HTTPError``,
        which is a subclass of ``RequestException``, so the same
        fail-open path catches it.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        monkeypatch.setattr(
            requests, "get",
            lambda *args, **kwargs: _FakeHibpResponse("", status_code=503),
        )
        result = auth_service._check_pwned_password("any-password")  # pylint: disable=assignment-from-none
        assert result is None

    def test_uses_k_anonymity_prefix(self, monkeypatch):
        """The HTTP call sends only the SHA-1 prefix, never the full hash.

        Verifies the k-anonymity property: the URL contains exactly 5
        hex characters of the hash.  Sending more would defeat the
        privacy guarantee that the password's identity stays local.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        password = "audit-this-call"
        captured = {}

        def _capture(url, *args, **kwargs):
            captured["url"] = url
            return _FakeHibpResponse("")

        monkeypatch.setattr(requests, "get", _capture)
        auth_service._check_pwned_password(password)

        # URL ends with /range/<5-char prefix>.  The full SHA-1 has 40
        # characters and the suffix is 35; only the 5-char prefix
        # should appear in the URL.
        full_sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
        assert captured["url"].endswith(f"/range/{full_sha1[:5]}")
        assert full_sha1[5:] not in captured["url"]

    def test_request_uses_padding_header(self, monkeypatch):
        """The HTTP request sets Add-Padding: true.

        Padding asks HIBP to randomise the response size so a passive
        observer cannot use response length to narrow the candidate
        set.  Required for the k-anonymity property to hold against
        a network-level adversary.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        captured = {}

        def _capture(_url, *args, **kwargs):
            captured["headers"] = kwargs.get("headers", {})
            return _FakeHibpResponse("")

        monkeypatch.setattr(requests, "get", _capture)
        auth_service._check_pwned_password("any-password")

        assert captured["headers"].get("Add-Padding") == "true"

    def test_request_honours_timeout_env(self, monkeypatch):
        """The HTTP call passes the configured ``HIBP_TIMEOUT_SECONDS``.

        The default is 3 seconds; the test uses a custom value to
        prove the env var is read at call time.  A failure here would
        mean an operator could not tune the timeout without restarting
        Python.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        monkeypatch.setenv("HIBP_TIMEOUT_SECONDS", "1.5")
        captured = {}

        def _capture(*_args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return _FakeHibpResponse("")

        monkeypatch.setattr(requests, "get", _capture)
        auth_service._check_pwned_password("any-password")

        assert captured["timeout"] == pytest.approx(1.5)

    def test_malformed_response_line_skipped(self, monkeypatch):
        """A response with a malformed line (no colon) does not crash.

        A protocol violation by HIBP should be safer to ignore than
        to surface as a 500.  The function must keep scanning for the
        real suffix on the well-formed lines.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        password = "test-password-malformed"
        sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
        body = "this-line-has-no-colon\r\n" + f"{sha1[5:]}:1"
        monkeypatch.setattr(
            requests, "get",
            lambda *args, **kwargs: _FakeHibpResponse(body),
        )
        with pytest.raises(ValidationError, match="known data breach"):
            auth_service._check_pwned_password(password)

    def test_hash_password_invokes_hibp_check(self, monkeypatch):
        """``hash_password`` runs the breach check before bcrypt.

        Verifies the integration point that closes F-086 across all
        password-set paths (register_user, change_password, companion
        creation/edit) without each having to call HIBP individually.
        Asserts that a known-breached input raises before bcrypt
        produces a hash.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        password = "another-breached-password"
        body = _hibp_response_for(password)
        monkeypatch.setattr(
            requests, "get",
            lambda *args, **kwargs: _FakeHibpResponse(body),
        )
        with pytest.raises(ValidationError, match="known data breach"):
            auth_service.hash_password(password)

    def test_hash_password_skips_hibp_when_too_long(self, monkeypatch):
        """The 72-byte length check fires before HIBP.

        Password-too-long is a deterministic local check; the upstream
        HIBP query is the slow remote step.  Ordering matters because
        an attacker submitting a 73-byte password would otherwise
        burn an HIBP request per attempt.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        called = {"count": 0}

        def _spy(*_args, **_kwargs):
            called["count"] += 1
            return _FakeHibpResponse("")

        monkeypatch.setattr(requests, "get", _spy)
        with pytest.raises(ValidationError, match="too long"):
            auth_service.hash_password("a" * 73)
        assert called["count"] == 0
