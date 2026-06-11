"""
Shekel Budget App -- MFA Reset Script

Emergency script to disable MFA for a user when backup codes are
exhausted and the TOTP device is lost.  Requires direct database access.

The reset clears ALL stored MFA material -- the encrypted TOTP secret,
the hashed backup codes, the confirmation timestamp, and the
replay-prevention step boundary -- even when ``is_enabled`` is already
``False`` but residual material lingers on the row.  An emergency
reset must leave no decryptable secret at rest.

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


def reset_mfa(email: str | None) -> None:
    """Disable MFA and clear all stored MFA material for the user.

    Clears the encrypted TOTP secret, hashed backup codes, confirmation
    timestamp, and replay-prevention step boundary.  The clear also
    runs when ``is_enabled`` is already ``False`` but residual material
    remains (an orphaned secret from a manual DB intervention or an
    interrupted disable flow): leaving an encrypted TOTP secret at rest
    after a reset would silently revive the old secret if the row were
    ever re-enabled, and keeps secret material alive past the moment
    the operator believes it was destroyed.

    Args:
        email: The email address of the user to reset.  ``None`` and
            unknown addresses take the user-not-found exit.

    Prints status messages to stdout.
    Exits with code 1 if the user is not found.
    """
    # Pylint: import-outside-toplevel -- importing anything under
    # ``app`` executes ``app.config``, which reads ``os.environ`` at
    # import time; deferring to call time keeps this module import
    # side-effect-free, so ``--help``, argparse errors, and the
    # confirmation prompt never load or validate app config.
    # pylint: disable=import-outside-toplevel
    from app.extensions import db
    from app.models.user import MfaConfig, User
    from app.utils.log_events import AUTH, log_event
    # pylint: enable=import-outside-toplevel

    user = db.session.query(User).filter_by(email=email).first()
    if not user:
        print(f"Error: No user found with email '{email}'.")
        sys.exit(1)

    mfa_config = db.session.query(MfaConfig).filter_by(user_id=user.id).first()
    # "Nothing to clear" means no row at all, or a row already in the
    # fully-reset state.  Checking every clearable column (not just
    # ``is_enabled``) is deliberate: a disabled row can still carry an
    # orphaned encrypted secret, and the reset must remove it.
    nothing_to_clear = mfa_config is None or (
        not mfa_config.is_enabled
        and mfa_config.totp_secret_encrypted is None
        and mfa_config.backup_codes is None
        and mfa_config.confirmed_at is None
        and mfa_config.last_totp_timestep is None
    )
    if nothing_to_clear:
        print(f"MFA is not enabled for {email}.")
        return

    # Clear all MFA fields.  ``last_totp_timestep`` is reset alongside
    # the secret because the value records the highest step consumed
    # against the cleared secret -- carrying it forward to a re-
    # enrollment under a fresh secret could lock the user out if their
    # new device is set to a clock that produces codes for an earlier
    # step.  See commit C-09 of the 2026-04-15 security remediation
    # plan for the column's contract.
    mfa_config.totp_secret_encrypted = None
    mfa_config.is_enabled = False
    mfa_config.backup_codes = None
    mfa_config.confirmed_at = None
    mfa_config.last_totp_timestep = None
    db.session.commit()

    # Audit trail for the reset action.
    logger = logging.getLogger(__name__)
    log_event(
        logger, logging.WARNING, "mfa_reset", AUTH,
        "MFA reset via admin script", user_email=email,
    )

    print(f"MFA has been disabled for {email}.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]`` when
            ``None``).

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

    from app import create_app

    app = create_app()
    with app.app_context():
        reset_mfa(args.email)
