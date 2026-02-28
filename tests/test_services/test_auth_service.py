"""
Shekel Budget App — Auth Service Tests

Tests for password hashing, verification, and user authentication
(§1.10 of the test plan).
"""

import pytest

from app.extensions import db
from app.models.user import User
from app.services import auth_service
from app.exceptions import AuthError


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
