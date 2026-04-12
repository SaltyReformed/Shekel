"""
Shekel Budget App -- Seed Companion User

Creates a companion user account linked to the primary owner.
Prompts for companion email, display name, and password.

Idempotent: if a user with the given email already exists, updates
the role_id and linked_owner_id instead of creating a new user.

Usage:
    python scripts/seed_companion.py

The script must be run after seed_user.py (an owner must exist).
"""

import getpass
import os
import sys

# Add project root to path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, ref_cache
from app.enums import RoleEnum
from app.extensions import db
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password

MIN_PASSWORD_LENGTH = 12


def seed_companion():
    """Create or update a companion user linked to the primary owner.

    Prompts interactively for email, display name, and password.
    Locates the first owner-role user in the database and links
    the companion to them.

    The password must be at least 12 characters, matching the
    minimum enforced by the application's register_user() and
    change_password() functions.
    """
    owner_role_id = ref_cache.role_id(RoleEnum.OWNER)
    companion_role_id = ref_cache.role_id(RoleEnum.COMPANION)

    # Find the primary owner account.
    owner = (
        db.session.query(User)
        .filter_by(role_id=owner_role_id)
        .first()
    )
    if owner is None:
        print(
            "Error: No owner user found. Run seed_user.py first.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Linking companion to owner: {owner.email} (id={owner.id})")

    # Prompt for companion details.
    email = input("Companion email: ").strip()
    if not email:
        print("Error: Email cannot be empty.", file=sys.stderr)
        sys.exit(1)

    display_name = input("Companion display name: ").strip()
    if not display_name:
        print("Error: Display name cannot be empty.", file=sys.stderr)
        sys.exit(1)

    password = getpass.getpass("Companion password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Error: Passwords do not match.", file=sys.stderr)
        sys.exit(1)
    if len(password) < MIN_PASSWORD_LENGTH:
        print(
            f"Error: Password must be at least {MIN_PASSWORD_LENGTH} "
            f"characters (got {len(password)}).",
            file=sys.stderr,
        )
        sys.exit(1)

    # Check if user already exists -- update instead of creating.
    existing = db.session.query(User).filter_by(email=email).first()
    if existing:
        existing.role_id = companion_role_id
        existing.linked_owner_id = owner.id
        existing.password_hash = hash_password(password)
        existing.display_name = display_name
        db.session.commit()
        print(f"Updated existing user '{email}' to companion role (id={existing.id}).")
        return existing

    # Create new companion user.
    companion = User(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
        role_id=companion_role_id,
        linked_owner_id=owner.id,
    )
    db.session.add(companion)
    db.session.flush()

    settings = UserSettings(user_id=companion.id)
    db.session.add(settings)
    db.session.commit()

    print(f"Created companion user: {email} (id={companion.id})")
    print(f"  Linked to owner: {owner.email} (id={owner.id})")
    print("  Password: [set via prompt]")
    return companion


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        seed_companion()
