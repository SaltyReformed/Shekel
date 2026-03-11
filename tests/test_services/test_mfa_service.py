"""Tests for the MFA service module."""

import re

import pyotp

from app.services import mfa_service


class TestGenerateSecret:
    """Tests for mfa_service.generate_totp_secret()."""

    def test_generates_base32_string(self):
        """generate_totp_secret() returns a valid base32 string."""
        secret = mfa_service.generate_totp_secret()
        assert isinstance(secret, str)
        assert len(secret) > 0
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
