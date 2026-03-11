"""
Shekel Budget App — Authentication Service

Handles password hashing and verification.  No Flask imports —
this is a pure service module.
"""

import bcrypt

from app.extensions import db
from app.models.user import User
from app.exceptions import AuthError, ValidationError


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


def change_password(user, current_password, new_password):
    """Change a user's password after verifying the current one.

    Args:
        user: The User object whose password is being changed.
        current_password: The user's current plaintext password.
        new_password: The new plaintext password (must be >= 12 chars).

    Returns:
        None on success.

    Raises:
        AuthError: If current_password does not match the stored hash.
        ValidationError: If new_password is shorter than 12 characters.
    """
    if not verify_password(current_password, user.password_hash):
        raise AuthError("Current password is incorrect.")
    if len(new_password) < 12:
        raise ValidationError("New password must be at least 12 characters.")
    user.password_hash = hash_password(new_password)
