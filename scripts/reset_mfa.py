"""
Shekel Budget App -- MFA Reset Script

Emergency script to disable MFA for a user when backup codes are
exhausted and the TOTP device is lost.  Requires direct database access.

Usage:
    python scripts/reset_mfa.py <user_email>

Example:
    python scripts/reset_mfa.py josh@saltyreformed.com
"""

import os
import sys

# Add project root to path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def reset_mfa(email):
    """Disable MFA for the user with the given email address.

    Args:
        email: The email address of the user to reset.

    Prints status messages to stdout.
    Exits with code 1 if the user is not found.
    """
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from app.models.user import MfaConfig, User  # pylint: disable=import-outside-toplevel

    user = db.session.query(User).filter_by(email=email).first()
    if not user:
        print(f"Error: No user found with email '{email}'.")
        sys.exit(1)

    mfa_config = db.session.query(MfaConfig).filter_by(user_id=user.id).first()
    if not mfa_config or not mfa_config.is_enabled:
        print(f"MFA is not enabled for {email}.")
        return

    # Clear all MFA fields.
    mfa_config.totp_secret_encrypted = None
    mfa_config.is_enabled = False
    mfa_config.backup_codes = None
    mfa_config.confirmed_at = None
    db.session.commit()

    print(f"MFA has been disabled for {email}.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/reset_mfa.py <user_email>")
        sys.exit(1)

    from app import create_app  # pylint: disable=import-outside-toplevel

    app = create_app()
    with app.app_context():
        reset_mfa(sys.argv[1])
