"""Shekel Budget App -- MFA Service.

Handles TOTP secret generation, verification, backup code management,
and secret encryption/decryption. No Flask imports -- pure service module.
"""

import os
import secrets
from io import BytesIO
from base64 import b64encode

import bcrypt
import pyotp
import qrcode
from cryptography.fernet import Fernet, MultiFernet


def _build_fernet_list():
    """Build the ordered list of Fernet instances backing the MultiFernet.

    The primary key (``TOTP_ENCRYPTION_KEY``) is used for encryption AND
    appears first for decryption.  ``TOTP_ENCRYPTION_KEY_OLD``, if set,
    contains zero or more comma-separated retired keys; each is wrapped
    in a Fernet and tried in order after the primary on decrypt.  Blank
    entries (empty strings, whitespace-only) are skipped so an operator
    can leave a stray comma after pruning a key without breaking
    startup.

    Returns:
        list[Fernet]: Non-empty list with the primary key at index 0.

    Raises:
        RuntimeError: If ``TOTP_ENCRYPTION_KEY`` is unset or empty.
        ValueError: If any configured key fails to initialize as a
            Fernet instance.  ``Fernet`` raises ``ValueError`` for
            wrong-length keys and ``binascii.Error`` (a ``ValueError``
            subclass) for non-base64 input, so a single ``ValueError``
            catch covers both invalid forms.
    """
    primary_key = os.getenv("TOTP_ENCRYPTION_KEY")
    if not primary_key:
        raise RuntimeError(
            "TOTP_ENCRYPTION_KEY environment variable is not set."
        )
    fernets = [Fernet(primary_key)]
    old_keys_raw = os.getenv("TOTP_ENCRYPTION_KEY_OLD", "")
    for raw in old_keys_raw.split(","):
        candidate = raw.strip()
        if candidate:
            fernets.append(Fernet(candidate))
    return fernets


def get_encryption_key():
    """Load the MultiFernet cipher from the environment.

    The returned cipher encrypts with the primary key
    (``TOTP_ENCRYPTION_KEY``) and decrypts with any primary-or-retired
    key listed in ``TOTP_ENCRYPTION_KEY_OLD``.  This makes
    ``TOTP_ENCRYPTION_KEY`` rotation a non-destructive operation:

      1. Move the existing primary value into ``TOTP_ENCRYPTION_KEY_OLD``.
      2. Set the new key as ``TOTP_ENCRYPTION_KEY``.  The application
         can immediately decrypt legacy ciphertexts via the retired key
         and writes new ciphertexts under the new primary.
      3. Run ``scripts/rotate_totp_key.py --confirm`` to re-wrap every
         existing ciphertext under the new primary.
      4. Remove the retired value from ``TOTP_ENCRYPTION_KEY_OLD`` at
         the next deploy.

    See ``docs/runbook_secrets.md`` for the full procedure.

    The public API exposed by ``MultiFernet`` is identical to
    ``Fernet`` -- ``encrypt``, ``decrypt``, and ``rotate`` -- so all
    callers of this function continue to work unchanged.

    Returns:
        MultiFernet: A cipher initialized with the primary key first
            and any retired keys appended in declaration order.

    Raises:
        RuntimeError: If ``TOTP_ENCRYPTION_KEY`` is unset or empty.
        ValueError: If any configured key cannot be parsed as a Fernet
            key (wrong length or non-base64 input).
    """
    return MultiFernet(_build_fernet_list())


def generate_totp_secret():
    """Generate a random base32-encoded TOTP secret.

    Returns:
        str: A random base32 string suitable for TOTP provisioning.
    """
    return pyotp.random_base32()


def encrypt_secret(plaintext_secret):
    """Encrypt a TOTP secret for database storage.

    Args:
        plaintext_secret: The base32-encoded secret string to encrypt.

    Returns:
        bytes: The Fernet-encrypted ciphertext.
    """
    return get_encryption_key().encrypt(plaintext_secret.encode("utf-8"))


def decrypt_secret(encrypted_secret):
    """Decrypt a TOTP secret retrieved from the database.

    Args:
        encrypted_secret: The Fernet-encrypted ciphertext bytes.

    Returns:
        str: The original base32-encoded plaintext secret.
    """
    return get_encryption_key().decrypt(encrypted_secret).decode("utf-8")


def get_totp_uri(secret, email, issuer="Shekel"):
    """Build an otpauth:// provisioning URI for QR code generation.

    Args:
        secret: The base32-encoded TOTP secret.
        email: The user's email address (used as the account name).
        issuer: The service name shown in authenticator apps.

    Returns:
        str: An otpauth://totp/ URI string.
    """
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def generate_qr_code_data_uri(uri):
    """Generate a base64-encoded PNG data URI from an otpauth:// URI.

    Args:
        uri: The otpauth:// provisioning URI to encode as a QR code.

    Returns:
        str: A data:image/png;base64,... string suitable for an <img> src attribute.
    """
    img = qrcode.make(uri)
    buf = BytesIO()
    img.save(buf, format="PNG")
    encoded = b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"


def verify_totp_code(secret, code):
    """Verify a 6-digit TOTP code against a secret.

    Allows one period (30 seconds) of clock drift in either direction
    via valid_window=1.

    Args:
        secret: The base32-encoded TOTP secret.
        code: The 6-digit code string from the user's authenticator app.

    Returns:
        bool: True if the code is valid, False otherwise.
    """
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def generate_backup_codes(count=10):
    """Generate a list of single-use backup codes.

    Each code is 14 random bytes rendered as 28 lowercase hex characters,
    yielding 112 bits of entropy. Bytes are sourced from
    ``secrets.token_hex`` which wraps the operating system's CSPRNG
    (``os.urandom``).

    The 112-bit width is the ASVS L2 V2.6.2 minimum for lookup secrets and
    matches the threat model used for this app: the bcrypt hashes (cost 12)
    leak via a backup or host compromise, and an attacker mounts an offline
    GPU brute-force. At ~10^12 bcrypt cost-12 hashes/second, exhausting
    2^112 candidates per code averages on the order of millions of years.
    The previous 32-bit width was crackable in seconds on a consumer GPU.

    Args:
        count: Number of backup codes to generate. Defaults to 10. Values
            <= 0 produce an empty list because ``range(count)`` is empty.

    Returns:
        list[str]: ``count`` plaintext backup code strings, each exactly
            28 characters of lowercase hexadecimal (``[0-9a-f]``). The
            caller is responsible for hashing them with
            :func:`hash_backup_codes` before persistence.
    """
    return [secrets.token_hex(14) for _ in range(count)]


def hash_backup_codes(codes, rounds=None):
    """Hash a list of plaintext backup codes with bcrypt.

    Args:
        codes: List of plaintext backup code strings.
        rounds: Optional bcrypt cost factor (default 12, use 4 for tests).

    Returns:
        list[str]: Bcrypt hash strings, one per code.
    """
    return [
        bcrypt.hashpw(
            c.encode("utf-8"),
            bcrypt.gensalt(rounds=rounds) if rounds else bcrypt.gensalt(),
        ).decode("utf-8")
        for c in codes
    ]


def verify_backup_code(code, hashed_codes):
    """Check a plaintext backup code against a list of bcrypt hashes.

    Args:
        code: The plaintext backup code to verify.
        hashed_codes: List of bcrypt hash strings to check against.

    Returns:
        int: The index of the matching hash, or -1 if no match found.
    """
    for idx, hashed in enumerate(hashed_codes):
        if bcrypt.checkpw(code.encode("utf-8"), hashed.encode("utf-8")):
            return idx
    return -1
