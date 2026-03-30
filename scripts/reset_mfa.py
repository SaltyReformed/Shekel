"""
Shekel Budget App -- MFA Reset Script

Emergency script to disable MFA for a user when backup codes are
exhausted and the TOTP device is lost.  Requires direct database access.

Usage:
    python scripts/reset_mfa.py <user_email>
    python scripts/reset_mfa.py --force <user_email>

Example:
    python scripts/reset_mfa.py admin@shekel.local
"""

import argparse
import logging
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

    # Audit trail for the reset action.
    from app.utils.log_events import log_event, AUTH  # pylint: disable=import-outside-toplevel

    logger = logging.getLogger(__name__)
    log_event(
        logger, logging.WARNING, "mfa_reset", AUTH,
        "MFA reset for %s via admin script", user_email=email,
    )

    print(f"MFA has been disabled for {email}.")


def parse_args(argv=None):
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        argparse.Namespace with ``email`` (str) and ``force`` (bool).
    """
    parser = argparse.ArgumentParser(
        description="Disable MFA for a user (emergency recovery)."
    )
    parser.add_argument("email", help="Email address of the user to reset.")
    parser.add_argument(
        "--force", action="store_true",
        help="Skip confirmation prompt (for scripted use).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()

    if not args.force:
        confirm = input(
            f"Reset MFA for {args.email}? This cannot be undone. [y/N] "
        )
        if confirm.lower() != "y":
            print("Aborted.")
            sys.exit(0)

    from app import create_app  # pylint: disable=import-outside-toplevel

    app = create_app()
    with app.app_context():
        reset_mfa(args.email)
