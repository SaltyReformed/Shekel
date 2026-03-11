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
from cryptography.fernet import Fernet


def get_encryption_key():
    """Load the Fernet encryption key from the environment.

    Returns:
        Fernet: An initialized Fernet cipher instance.

    Raises:
        RuntimeError: If the TOTP_ENCRYPTION_KEY environment variable is not set.
    """
    key = os.getenv("TOTP_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("TOTP_ENCRYPTION_KEY environment variable is not set.")
    return Fernet(key)


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

    Each code is an 8-character lowercase hex string.

    Args:
        count: Number of backup codes to generate (default 10).

    Returns:
        list[str]: The plaintext backup code strings.
    """
    return [secrets.token_hex(4) for _ in range(count)]


def hash_backup_codes(codes):
    """Hash a list of plaintext backup codes with bcrypt.

    Args:
        codes: List of plaintext backup code strings.

    Returns:
        list[str]: Bcrypt hash strings, one per code.
    """
    return [
        bcrypt.hashpw(c.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
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
