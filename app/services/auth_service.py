"""
Shekel Budget App — Authentication Service

Handles password hashing, verification, and user registration.
No Flask imports — this is a pure service module.
"""

import re

import bcrypt

from app.extensions import db
from app.models.user import User, UserSettings
from app.models.scenario import Scenario
from app.exceptions import AuthError, ConflictError, ValidationError


def hash_password(plain_password, rounds=None):
    """Hash a plaintext password using bcrypt.

    Args:
        plain_password: The plaintext password string.
        rounds: Optional bcrypt cost factor (log2 iterations).
            Defaults to bcrypt's built-in default if not specified.

    Returns:
        The bcrypt hash as a string.
    """
    salt = bcrypt.gensalt(rounds=rounds) if rounds else bcrypt.gensalt()
    return bcrypt.hashpw(
        plain_password.encode("utf-8"), salt
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


def register_user(email, password, display_name):
    """Register a new user with default settings and a baseline scenario.

    Creates a User, UserSettings (with model defaults), and a baseline
    Scenario atomically.  Does NOT commit -- the caller is responsible
    for committing the transaction.

    Args:
        email:        The user's email address.
        password:     The plaintext password (must be >= 12 chars).
        display_name: The user's display name.

    Returns:
        The newly created User object (unflushed settings and scenario
        are attached to the same session).

    Raises:
        ValidationError: If the email format is invalid, the display
            name is empty, or the password is too short.
        ConflictError: If a user with the given email already exists.
    """
    # Sanitize inputs.
    email = email.strip().lower()
    display_name = display_name.strip()

    # Validate email format.
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValidationError("Invalid email format.")

    # Validate display name is not empty.
    if not display_name:
        raise ValidationError("Display name is required.")

    # Validate password length.
    if len(password) < 12:
        raise ValidationError("Password must be at least 12 characters.")

    # Check email uniqueness.
    if User.query.filter_by(email=email).first():
        raise ConflictError("An account with this email already exists.")

    # Create user.
    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
    )
    db.session.add(user)
    db.session.flush()

    # Create default settings (model defaults handle values).
    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    # Create baseline scenario.
    scenario = Scenario(user_id=user.id, name="Baseline", is_baseline=True)
    db.session.add(scenario)

    return user
