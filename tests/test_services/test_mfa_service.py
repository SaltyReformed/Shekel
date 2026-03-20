"""Tests for the MFA service module."""

import re

import pyotp
import pytest

from app.services import mfa_service


class TestGenerateSecret:
    """Tests for mfa_service.generate_totp_secret()."""

    def test_generates_base32_string(self):
        """generate_totp_secret() returns a valid base32 string."""
        secret = mfa_service.generate_totp_secret()
        assert isinstance(secret, str)
        # pyotp.random_base32() default length is 32 characters
        assert len(secret) == 32
        # Valid base32 characters only (A-Z, 2-7, =).
        assert re.fullmatch(r"[A-Z2-7=]+", secret)

    def test_generates_unique_secrets(self):
        """Two calls return different secrets."""
        s1 = mfa_service.generate_totp_secret()
        s2 = mfa_service.generate_totp_secret()
        assert s1 != s2


class TestEncryptDecrypt:
    """Tests for mfa_service.encrypt_secret() and decrypt_secret()."""

    def test_round_trip(self):
        """Encrypting then decrypting returns the original secret."""
        secret = mfa_service.generate_totp_secret()
        encrypted = mfa_service.encrypt_secret(secret)
        decrypted = mfa_service.decrypt_secret(encrypted)
        assert decrypted == secret

    def test_encrypted_differs_from_plaintext(self):
        """Encrypted output is not the same as the plaintext."""
        secret = mfa_service.generate_totp_secret()
        encrypted = mfa_service.encrypt_secret(secret)
        assert encrypted != secret.encode("utf-8")


class TestVerifyTotpCode:
    """Tests for mfa_service.verify_totp_code()."""

    def test_valid_code_accepted(self):
        """verify_totp_code() returns True for the current valid code."""
        secret = mfa_service.generate_totp_secret()
        code = pyotp.TOTP(secret).now()
        assert mfa_service.verify_totp_code(secret, code) is True

    def test_invalid_code_rejected(self):
        """verify_totp_code() returns False for a wrong code."""
        secret = mfa_service.generate_totp_secret()
        assert mfa_service.verify_totp_code(secret, "000000") is False


class TestBackupCodes:
    """Tests for backup code generation, hashing, and verification."""

    def test_generate_backup_codes_count(self):
        """generate_backup_codes() returns the requested number of codes."""
        assert len(mfa_service.generate_backup_codes()) == 10
        assert len(mfa_service.generate_backup_codes(5)) == 5

    def test_generate_backup_codes_format(self):
        """Each backup code is an 8-character hex string."""
        codes = mfa_service.generate_backup_codes()
        for code in codes:
            assert len(code) == 8
            assert re.fullmatch(r"[0-9a-f]{8}", code)

    def test_hash_and_verify_round_trip(self):
        """A generated code matches its own hash via verify_backup_code()."""
        codes = mfa_service.generate_backup_codes()
        hashed = mfa_service.hash_backup_codes(codes)
        assert mfa_service.verify_backup_code(codes[0], hashed) == 0

    def test_verify_wrong_code_returns_negative(self):
        """verify_backup_code() returns -1 for an unrecognized code."""
        codes = mfa_service.generate_backup_codes()
        hashed = mfa_service.hash_backup_codes(codes)
        assert mfa_service.verify_backup_code("zzzzzzzz", hashed) == -1

    def test_verify_returns_correct_index(self):
        """verify_backup_code() returns the index of the matching hash."""
        codes = mfa_service.generate_backup_codes()
        hashed = mfa_service.hash_backup_codes(codes)
        assert mfa_service.verify_backup_code(codes[2], hashed) == 2


class TestGetTotpUri:
    """Tests for mfa_service.get_totp_uri()."""

    def test_uri_format(self):
        """get_totp_uri() returns an otpauth:// URI with correct parameters."""
        secret = mfa_service.generate_totp_secret()
        uri = mfa_service.get_totp_uri(secret, "test@example.com")
        assert uri.startswith("otpauth://totp/")
        assert "Shekel" in uri
        assert "test%40example.com" in uri or "test@example.com" in uri


class TestGenerateQrCode:
    """Tests for mfa_service.generate_qr_code_data_uri()."""

    def test_returns_data_uri(self):
        """generate_qr_code_data_uri() returns a data:image/png;base64 string."""
        data_uri = mfa_service.generate_qr_code_data_uri("otpauth://totp/Test?secret=ABC")
        assert data_uri.startswith("data:image/png;base64,")


class TestNegativeAndBoundaryPaths:
    """Negative-path and boundary-condition tests for MFA service functions.

    Covers: corrupted/empty ciphertext, wrong-length TOTP codes, non-numeric
    codes, empty-string codes, zero/negative backup code counts, and empty
    secret round-trip encryption.
    """

    def test_decrypt_corrupted_ciphertext(self):
        """Decrypting corrupted ciphertext raises InvalidToken.

        Corrupted database entries (disk errors, migration bugs) must produce
        a clear error, not silently return garbage that gets used as a TOTP secret.
        """
        from cryptography.fernet import InvalidToken

        with pytest.raises(InvalidToken):
            mfa_service.decrypt_secret(b"not-valid-fernet-token")

    def test_decrypt_empty_bytes(self):
        """Decrypting empty bytes raises InvalidToken.

        Empty ciphertext could happen if the database column was cleared
        without proper cleanup.
        """
        from cryptography.fernet import InvalidToken

        with pytest.raises(InvalidToken):
            mfa_service.decrypt_secret(b"")

    def test_verify_totp_wrong_length_five_digits(self):
        """A 5-digit TOTP code is rejected (returns False, does not crash).

        A user manually typing a code could miss a digit. pyotp's verify()
        returns False for wrong-length codes.
        """
        secret = mfa_service.generate_totp_secret()
        result = mfa_service.verify_totp_code(secret, "12345")
        assert result is False

    def test_verify_totp_non_numeric(self):
        """A non-numeric TOTP code is rejected (returns False, does not crash).

        Copy-paste errors could insert non-numeric characters. pyotp's verify()
        handles non-numeric input gracefully.
        """
        secret = mfa_service.generate_totp_secret()
        result = mfa_service.verify_totp_code(secret, "abcdef")
        assert result is False

    def test_verify_totp_empty_string(self):
        """An empty-string TOTP code is rejected (returns False, does not crash).

        Empty form submission must be handled gracefully.
        """
        secret = mfa_service.generate_totp_secret()
        result = mfa_service.verify_totp_code(secret, "")
        assert result is False

    def test_generate_backup_codes_zero_count(self):
        """generate_backup_codes(0) returns an empty list, not a crash.

        A misconfiguration passing count=0 must produce an empty list.
        range(0) produces an empty iterator.
        """
        result = mfa_service.generate_backup_codes(0)
        assert result == []

    def test_generate_backup_codes_negative_count(self):
        """generate_backup_codes(-1) returns an empty list, not a crash.

        range(-1) produces an empty iterator, same as range(0).
        """
        result = mfa_service.generate_backup_codes(-1)
        assert result == []

    def test_verify_totp_seven_digits(self):
        """A 7-digit TOTP code is rejected (returns False).

        Authenticator apps produce 6-digit codes. More digits must be rejected.
        """
        secret = mfa_service.generate_totp_secret()
        result = mfa_service.verify_totp_code(secret, "1234567")
        assert result is False

    def test_encrypt_empty_string_secret(self):
        """Encrypting an empty string round-trips correctly.

        Edge case where a secret field is cleared but encrypt is still called.
        Fernet encrypts empty strings without error.
        """
        encrypted = mfa_service.encrypt_secret("")
        decrypted = mfa_service.decrypt_secret(encrypted)
        assert decrypted == ""

    def test_get_totp_uri_format_with_explicit_issuer(self):
        """get_totp_uri produces a well-formed otpauth:// URI with explicit issuer.

        Malformed URIs would produce unscannable QR codes, locking users
        out of MFA setup.
        """
        secret = mfa_service.generate_totp_secret()
        uri = mfa_service.get_totp_uri(secret, "user@example.com", issuer="Shekel")

        assert uri.startswith("otpauth://totp/")
        # Issuer appears in the URI.
        assert "Shekel" in uri
        # Email appears (URL-encoded @ is %40).
        assert "user%40example.com" in uri or "user@example.com" in uri
