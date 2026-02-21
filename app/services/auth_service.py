"""
Shekel Budget App — Authentication Service

Handles password hashing and verification.  No Flask imports —
this is a pure service module.
"""

import bcrypt

from app.extensions import db
from app.models.user import User
from app.exceptions import AuthError


def hash_password(plain_password):
    """Hash a plaintext password using bcrypt.

    Args:
        plain_password: The plaintext password string.

    Returns:
        The bcrypt hash as a string.
    """
    return bcrypt.hashpw(
        plain_password.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")


def verify_password(plain_password, password_hash):
    """Verify a plaintext password against a bcrypt hash.

    Args:
        plain_password: The plaintext password to check.
        password_hash:  The stored bcrypt hash.

    Returns:
        True if the password matches, False otherwise.
    """
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        password_hash.encode("utf-8"),
    )


def authenticate(email, password):
    """Authenticate a user by email and password.

    Args:
        email:    The user's email address.
        password: The plaintext password.

    Returns:
        The User object if authentication succeeds.

    Raises:
        AuthError: If the email is not found or the password is wrong.
    """
    user = db.session.query(User).filter_by(email=email).first()
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("Invalid email or password.")
    if not user.is_active:
        raise AuthError("Account is disabled.")
    return user
