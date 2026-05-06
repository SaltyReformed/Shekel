"""
Shekel Budget App -- TOTP Encryption Key Rotation

One-shot operations utility that re-wraps every
``auth.mfa_configs.totp_secret_encrypted`` blob under the current
primary key (``TOTP_ENCRYPTION_KEY``).  Intended to be run AFTER an
operator has:

    1. Generated a new primary key
       (``Fernet.generate_key().decode()``).
    2. Moved the previous primary key to ``TOTP_ENCRYPTION_KEY_OLD``.
    3. Set the new key as ``TOTP_ENCRYPTION_KEY``.
    4. Restarted the application container so the new key list takes
       effect at the runtime layer.

The application is fully usable between steps 4 and 5 because
``mfa_service.get_encryption_key()`` returns a ``MultiFernet`` that
decrypts under either the new primary or the retired key.  This
script's role is to migrate the at-rest ciphertexts forward so the
operator can safely remove the retired key from
``TOTP_ENCRYPTION_KEY_OLD`` at the next deploy.

Usage:
    python scripts/rotate_totp_key.py --confirm

The ``--confirm`` flag is mandatory: running without it prints a
short usage hint and exits with code 1, never touching the database.

Idempotency:
    The script is safe to run repeatedly.  Each row is first probed
    with the primary key alone; rows that already decrypt under the
    primary are counted as ``already_current`` and left untouched.
    Only rows that fail the primary probe are re-encrypted.

Exit codes:
    0  Successful rotation, every row accounted for.
    1  ``--confirm`` flag was not supplied.
    2  Successful run but at least one row could not be decrypted
       under any configured key.  Operator action required: do NOT
       remove ``TOTP_ENCRYPTION_KEY_OLD`` until the row is recovered
       or the user re-enrolls MFA.

Test entry point:
    ``execute_rotation(db.session)`` returns a
    ``(rotated, already_current, skipped)`` triple.  Tests call this
    directly with the test session and never create a separate Flask
    app.
"""

import argparse
import logging
import os
import sys

# Ensure the project root is on sys.path so 'app' is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Per-row outcome sentinels.  Returned by ``_rotate_one_config`` and
# tallied by ``execute_rotation``.  Module-level constants rather than
# magic strings so a typo at the call site fails the linter.
_OUTCOME_ALREADY_CURRENT = "already_current"
_OUTCOME_ROTATED = "rotated"
_OUTCOME_SKIPPED = "skipped"


def _rotate_one_config(config, primary_only, multi, logger) -> str:
    """Rotate a single ``MfaConfig`` row in place.

    Encapsulates the per-row classification logic so
    ``execute_rotation`` only has to drive the loop and aggregate
    counts.  Splitting the logic also lets unit tests target a single
    row without seeding a full session.

    The row is classified as one of:

      - ``_OUTCOME_ALREADY_CURRENT`` -- the ciphertext already decrypts
        under the primary key alone.  No mutation.  This is the
        idempotency guard.
      - ``_OUTCOME_ROTATED`` -- the ciphertext required a retired key
        for decryption; ``MultiFernet.rotate`` re-wrapped it under the
        primary.  ``config.totp_secret_encrypted`` is mutated in place.
      - ``_OUTCOME_SKIPPED`` -- no configured key could decrypt the
        ciphertext.  Logged at ERROR level naming the row id; no
        mutation.

    Args:
        config: The ``MfaConfig`` row whose
            ``totp_secret_encrypted`` blob will be classified and
            optionally re-wrapped.
        primary_only: A bare ``Fernet`` initialised on the primary
            key.  Used as the idempotency probe.
        multi: The ``MultiFernet`` initialised on primary plus any
            retired keys.  Used to actually rotate.
        logger: ``logging.Logger`` instance used to surface the row
            id of any skipped row.

    Returns:
        One of the ``_OUTCOME_*`` sentinel strings.
    """
    # Local imports keep the module importable before the app is
    # initialised (matches scripts/rotate_sessions.py convention).
    from cryptography.fernet import InvalidToken  # pylint: disable=import-outside-toplevel

    ciphertext = config.totp_secret_encrypted

    # Idempotency probe: does the ciphertext already decrypt under
    # the primary alone?  If yes, no rotation is needed; skip.
    try:
        primary_only.decrypt(ciphertext)
        return _OUTCOME_ALREADY_CURRENT
    except InvalidToken:
        # Fall through to the rotation path below.  Both real
        # decrypt failures (wrong key) and malformed ciphertexts
        # raise InvalidToken from Fernet.decrypt.
        pass

    # The row is encrypted under a non-primary key.  MultiFernet
    # tries primary first and then each retired key; on success it
    # re-encrypts under the primary with a fresh IV and timestamp.
    try:
        config.totp_secret_encrypted = multi.rotate(ciphertext)
    except InvalidToken:
        # No configured key matches.  Log the row id (NEVER the
        # ciphertext or any plaintext) and report the skip so the
        # remaining rows still get migrated.  The non-zero exit
        # code on the CLI side surfaces this to the operator.
        logger.error(
            "MFA config id=%d cannot be decrypted under any "
            "configured key. Row left untouched. Investigate "
            "before removing TOTP_ENCRYPTION_KEY_OLD.",
            config.id,
        )
        return _OUTCOME_SKIPPED

    return _OUTCOME_ROTATED


def execute_rotation(db_session) -> tuple[int, int, int]:
    """Re-encrypt every MFA config under the current primary key.

    The core data operation, separated from app creation so tests can
    call it directly with the test database session.  The function
    fetches every ``MfaConfig`` row whose ``totp_secret_encrypted``
    column is non-NULL and dispatches each to ``_rotate_one_config``,
    which classifies it as already-current, rotated, or skipped.  The
    classification rules and the choice to use ``MultiFernet.rotate``
    (rather than a manual decrypt+encrypt) are documented in
    ``_rotate_one_config``.

    Args:
        db_session (sqlalchemy.orm.Session): A SQLAlchemy session
            bound to a database that already has the
            ``auth.mfa_configs`` table.

    Returns:
        A ``(rotated, already_current, skipped)`` triple of row counts.
        The three values always sum to the number of MFA configs with
        a non-NULL ``totp_secret_encrypted`` column at the time the
        query was issued.

    Raises:
        RuntimeError: If ``TOTP_ENCRYPTION_KEY`` is unset or empty.
            Without a primary key the rotation has no target, so we
            fail fast rather than silently leaving the table in its
            previous state.

    Side effects:
        - Mutates ``totp_secret_encrypted`` on rows that need rotation.
        - Commits the transaction once at the end (single commit so
          either the whole rotation succeeds or the whole rotation
          rolls back on a database error).
        - Emits a structured log event ``totp_key_rotated`` at
          ``WARNING`` level with the three counts.
    """
    # Local imports keep this module importable even when the app
    # package has not yet been initialized (matches the convention used
    # in scripts/rotate_sessions.py and scripts/audit_cleanup.py).
    # pylint: disable=import-outside-toplevel
    from cryptography.fernet import Fernet

    from app.models.user import MfaConfig
    from app.services import mfa_service
    from app.utils.log_events import AUTH, log_event
    # pylint: enable=import-outside-toplevel

    logger = logging.getLogger(__name__)

    # The primary-only Fernet is the idempotency probe.  We deliberately
    # re-read the env var here rather than poking at MultiFernet's
    # private ``_fernets`` list -- the env var is the contract.
    primary_key = os.getenv("TOTP_ENCRYPTION_KEY")
    if not primary_key:
        raise RuntimeError(
            "TOTP_ENCRYPTION_KEY environment variable is not set."
        )
    primary_only = Fernet(primary_key)
    multi = mfa_service.get_encryption_key()

    counts = {
        _OUTCOME_ROTATED: 0,
        _OUTCOME_ALREADY_CURRENT: 0,
        _OUTCOME_SKIPPED: 0,
    }
    configs = (
        db_session.query(MfaConfig)
        .filter(MfaConfig.totp_secret_encrypted.isnot(None))
        .all()
    )
    for config in configs:
        outcome = _rotate_one_config(config, primary_only, multi, logger)
        counts[outcome] += 1

    db_session.commit()

    log_event(
        logger,
        logging.WARNING,
        "totp_key_rotated",
        AUTH,
        "TOTP encryption key rotation completed.",
        rotated=counts[_OUTCOME_ROTATED],
        already_current=counts[_OUTCOME_ALREADY_CURRENT],
        skipped=counts[_OUTCOME_SKIPPED],
    )
    return (
        counts[_OUTCOME_ROTATED],
        counts[_OUTCOME_ALREADY_CURRENT],
        counts[_OUTCOME_SKIPPED],
    )


def run_rotation() -> tuple[int, int, int]:
    """Create the Flask app and execute the rotation.

    Convenience wrapper for CLI use.  Tests should call
    ``execute_rotation()`` directly with the test db session.

    Returns:
        The ``(rotated, already_current, skipped)`` triple from
        ``execute_rotation``.
    """
    # Local imports so the module is importable even when create_app
    # would fail (e.g. config validation in a test that has not yet
    # patched the environment).
    # pylint: disable=import-outside-toplevel
    from app import create_app
    from app.extensions import db
    # pylint: enable=import-outside-toplevel

    app = create_app()
    with app.app_context():
        return execute_rotation(db.session)


def parse_args(argv=None):
    """Parse command-line arguments.

    Args:
        argv (list[str] | None): Argument list (defaults to
            ``sys.argv[1:]`` when ``None``).

    Returns:
        argparse.Namespace with ``confirm`` (bool).
    """
    parser = argparse.ArgumentParser(
        description=(
            "Re-wrap every auth.mfa_configs ciphertext under the "
            "current TOTP_ENCRYPTION_KEY primary key.  Run during a "
            "key rotation, after the new key has been promoted to "
            "TOTP_ENCRYPTION_KEY and the previous key has been moved "
            "to TOTP_ENCRYPTION_KEY_OLD.  See "
            "docs/runbook_secrets.md."
        ),
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help=(
            "Acknowledge that the script will mutate every MFA "
            "configuration row.  Required: the script refuses to run "
            "without this flag."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    """CLI entry point.

    Args:
        argv (list[str] | None): Argument list (defaults to
            ``sys.argv[1:]`` when ``None``).

    Returns:
        Process exit code:

          - ``0`` -- rotation completed and every row was decryptable.
          - ``1`` -- ``--confirm`` was not supplied; database untouched.
          - ``2`` -- rotation completed but at least one row was
            skipped because no configured key could decrypt it.  The
            operator must reconcile the row before pruning
            ``TOTP_ENCRYPTION_KEY_OLD``.
    """
    args = parse_args(argv)
    if not args.confirm:
        print(
            "Refusing to run without --confirm.  Re-run as:\n"
            "    python scripts/rotate_totp_key.py --confirm",
            file=sys.stderr,
        )
        return 1
    rotated, already_current, skipped = run_rotation()
    print(
        f"Rotated {rotated}; already current {already_current}; "
        f"skipped {skipped}."
    )
    if skipped > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
