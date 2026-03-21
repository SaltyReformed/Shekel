"""
Shekel Budget App -- Seed User & Default Data

Creates the single Phase 1 user plus their default checking account,
baseline scenario, user settings, and starter categories.

Validates that the password is at least 12 characters, matching the
minimum enforced by the application's change_password() and
register_user() functions.  Exits with code 1 if the password is
too short.

Usage:
    python scripts/seed_user.py

Environment variables (or .env file):
    SEED_USER_EMAIL        -- default: admin@shekel.local
    SEED_USER_PASSWORD     -- default: ChangeMe!2026
    SEED_USER_DISPLAY_NAME -- default: Budget Admin
"""

import os
import sys

# Add project root to path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models.user import User, UserSettings
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.category import Category
from app.models.ref import AccountType
from app.services.auth_service import hash_password, DEFAULT_CATEGORIES


def seed_user():
    """Create the seeded user and all associated default data.

    Validates that the password is at least 12 characters, matching the
    minimum enforced by the application's change_password() and
    register_user() functions.  Exits with code 1 if the password is
    too short.
    """
    email = os.getenv("SEED_USER_EMAIL", "admin@shekel.local")
    password = os.getenv("SEED_USER_PASSWORD", "ChangeMe!2026")
    display_name = os.getenv("SEED_USER_DISPLAY_NAME", "Budget Admin")

    # Enforce the same 12-character minimum as the app's change_password()
    # and register_user() functions.  Prevents deploying with a weak
    # default that cannot be changed through the UI.
    if len(password) < 12:
        print(
            f"Error: SEED_USER_PASSWORD must be at least 12 characters "
            f"(got {len(password)}).  Set SEED_USER_PASSWORD in .env or "
            f"environment."
        )
        sys.exit(1)

    # Check if user already exists.
    existing = db.session.query(User).filter_by(email=email).first()
    if existing:
        print(f"User '{email}' already exists (id={existing.id}).  Skipping.")
        return existing

    # Create the user.
    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
    )
    db.session.add(user)
    db.session.flush()  # Get user.id
    print(f"Created user: {email} (id={user.id})")

    # Create user settings.
    settings = UserSettings(user_id=user.id)
    db.session.add(settings)
    print("  + User settings created.")

    # Create checking account.
    checking_type = db.session.query(AccountType).filter_by(name="checking").one()
    account = Account(
        user_id=user.id,
        account_type_id=checking_type.id,
        name="Checking",
        current_anchor_balance=0,
    )
    db.session.add(account)
    print("  + Checking account created.")

    # Create baseline scenario.
    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    print("  + Baseline scenario created.")

    # Create default categories.
    for sort_idx, (group, item) in enumerate(DEFAULT_CATEGORIES):
        cat = Category(
            user_id=user.id,
            group_name=group,
            item_name=item,
            sort_order=sort_idx,
        )
        db.session.add(cat)
    print(f"  + {len(DEFAULT_CATEGORIES)} default categories created.")

    db.session.commit()
    print("\nSeed complete.  You can now log in with:")
    print(f"  Email:    {email}")
    print(f"  Password: {password}")
    return user


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        seed_user()
