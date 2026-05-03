"""Tests for the MFA service module."""

import re
from unittest.mock import patch

import bcrypt
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
    """Tests for backup code generation, hashing, and verification.

    Backup codes were upgraded from 32-bit (8 hex chars) to 112-bit
    (28 hex chars) entropy as part of remediation C-03 / finding F-004.
    The 112-bit width matches ASVS L2 V2.6.2 and resists offline GPU
    brute-force against bcrypt cost-12 hashes. Legacy codes enrolled
    before the upgrade remain valid until the user regenerates them
    because bcrypt is length-agnostic.
    """

    def test_generate_backup_codes_default_count(self):
        """generate_backup_codes() with no argument returns exactly 10 codes.

        The default count is the documented contract used by the
        ``/mfa/confirm`` and ``/mfa/regenerate-backup-codes`` routes.
        """
        codes = mfa_service.generate_backup_codes()
        assert len(codes) == 10

    def test_generate_backup_codes_explicit_count(self):
        """generate_backup_codes(n) returns exactly n codes for positive n."""
        for requested in (1, 5, 25):
            codes = mfa_service.generate_backup_codes(requested)
            assert len(codes) == requested, (
                f"Expected {requested} codes, got {len(codes)}"
            )

    def test_generate_backup_codes_length_and_format(self):
        """Each code is exactly 28 lowercase hex characters (112 bits).

        Width and character set together encode the entropy claim. 28
        hex chars * 4 bits/char = 112 bits, which is the ASVS L2 V2.6.2
        minimum for lookup secrets. Uppercase letters or non-hex chars
        would mean ``secrets.token_hex`` was replaced with a different
        encoder.
        """
        codes = mfa_service.generate_backup_codes()
        for code in codes:
            assert len(code) == 28, (
                f"Backup code must be 28 chars (112 bits); got {len(code)}: {code!r}"
            )
            assert re.fullmatch(r"[0-9a-f]{28}", code), (
                f"Backup code must be lowercase hex; got {code!r}"
            )

    def test_generate_backup_codes_entropy_unique_across_many_calls(self):
        """1000 generated codes contain no duplicates.

        With 112 bits of entropy the birthday-bound collision probability
        for 1000 samples is on the order of 10^-28 -- effectively zero.
        Any duplicate in a 1000-sample run indicates the entropy source
        regressed (e.g. someone replaced ``secrets.token_hex`` with a
        seeded PRNG) and would be a critical security defect for a
        money app.
        """
        sample_size = 1000
        codes = mfa_service.generate_backup_codes(sample_size)
        assert len(codes) == sample_size
        assert len(set(codes)) == sample_size, (
            "Duplicate backup codes generated -- entropy source is not "
            "behaving as a CSPRNG"
        )

    def test_generate_backup_codes_uses_secrets_token_hex_with_14_bytes(self):
        """generate_backup_codes() sources entropy from secrets.token_hex(14).

        Pins the implementation to the OS CSPRNG path with a 14-byte
        (112-bit) request. Catches a regression where someone swaps in
        ``random.choice``, ``hashlib.sha256(time.time())``, or any
        non-CSPRNG source, or where the byte count is reduced. The byte
        count is the entropy claim -- it is not an internal detail.
        """
        with patch.object(
            mfa_service.secrets, "token_hex", wraps=mfa_service.secrets.token_hex
        ) as spy:
            mfa_service.generate_backup_codes(count=10)

        assert spy.call_count == 10, (
            f"Expected 10 calls to secrets.token_hex, got {spy.call_count}"
        )
        for call in spy.call_args_list:
            args, kwargs = call
            byte_count = args[0] if args else kwargs.get("nbytes")
            assert byte_count == 14, (
                f"Expected secrets.token_hex(14); got token_hex({byte_count})"
            )

    def test_hash_and_verify_round_trip(self):
        """A freshly generated 28-char code matches its own bcrypt hash."""
        codes = mfa_service.generate_backup_codes()
        hashed = mfa_service.hash_backup_codes(codes, rounds=4)
        assert mfa_service.verify_backup_code(codes[0], hashed) == 0

    def test_verify_wrong_code_returns_negative(self):
        """verify_backup_code() returns -1 when no stored hash matches.

        Uses a 28-char string that is guaranteed not to collide with any
        randomly generated code (all 'z' is outside the [0-9a-f]
        alphabet, so it cannot equal any real backup code).
        """
        codes = mfa_service.generate_backup_codes()
        hashed = mfa_service.hash_backup_codes(codes, rounds=4)
        unrecognized = "z" * 28
        assert mfa_service.verify_backup_code(unrecognized, hashed) == -1

    def test_verify_returns_correct_index(self):
        """verify_backup_code() returns the index of the matching hash."""
        codes = mfa_service.generate_backup_codes()
        hashed = mfa_service.hash_backup_codes(codes, rounds=4)
        assert mfa_service.verify_backup_code(codes[2], hashed) == 2

    def test_verify_backup_code_accepts_legacy_8_char_codes(self):
        """Legacy 8-char codes from pre-C-03 enrollments still verify.

        Bcrypt hashes any byte string of any length up to 72 bytes, so a
        stored hash of an 8-char legacy code matches a freshly typed
        legacy code regardless of the new generator width. This is the
        compatibility guarantee documented in C-03: enrolled users do
        not get locked out -- they receive an in-app prompt to
        regenerate (delivered separately in C-16). Without this test,
        a future refactor that adds length validation to verify could
        silently break login for every pre-upgrade user.
        """
        legacy_code = "1a2b3c4d"
        assert len(legacy_code) == 8
        legacy_hash = bcrypt.hashpw(
            legacy_code.encode("utf-8"), bcrypt.gensalt(rounds=4)
        ).decode("utf-8")

        # Mixed list -- legacy hash at index 0, modern hashes after.
        modern_codes = mfa_service.generate_backup_codes(3)
        modern_hashes = mfa_service.hash_backup_codes(modern_codes, rounds=4)
        stored = [legacy_hash] + modern_hashes

        assert mfa_service.verify_backup_code(legacy_code, stored) == 0

    def test_verify_backup_code_rejects_legacy_code_of_wrong_value(self):
        """A typed legacy-length code that does not match any stored hash returns -1.

        Defends against the "any 8-char input matches" failure mode that
        would arise if length checks short-circuited verification.
        """
        legacy_code = "deadbeef"
        legacy_hash = bcrypt.hashpw(
            legacy_code.encode("utf-8"), bcrypt.gensalt(rounds=4)
        ).decode("utf-8")
        assert mfa_service.verify_backup_code("cafebabe", [legacy_hash]) == -1


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


class TestMultiFernetKeyHandling:
    """Tests for the MultiFernet primary/retired-key key list construction.

    Covers audit finding F-030 (C-04): ``get_encryption_key()`` must
    return a ``MultiFernet`` so an operator can rotate
    ``TOTP_ENCRYPTION_KEY`` without losing access to ciphertexts that
    were written under the previous primary.

    The tests pin the public contract:

      - the encryption call uses the primary key, never a retired one;
      - the decryption path tries the primary first and then each
        retired key in declaration order;
      - the comma-separated retired-key list tolerates whitespace and
        empty entries between commas;
      - any malformed retired key fails fast at startup rather than
        silently being skipped.
    """

    def test_get_encryption_key_returns_multifernet(self):
        """get_encryption_key() returns a MultiFernet, not a bare Fernet.

        The MultiFernet wrapper is what makes non-destructive key
        rotation possible.  A regression to a bare Fernet would mean
        that any ciphertext written under a retired key becomes
        unreadable the moment the operator promotes a new primary --
        the exact failure mode that finding F-030 was opened to fix.
        """
        from cryptography.fernet import MultiFernet  # pylint: disable=import-outside-toplevel

        cipher = mfa_service.get_encryption_key()
        assert isinstance(cipher, MultiFernet)

    def test_get_encryption_key_raises_if_unset(self, monkeypatch):
        """get_encryption_key() raises RuntimeError when the primary
        key env var is unset.

        The conftest autouse fixture sets ``TOTP_ENCRYPTION_KEY`` to a
        random key for every test; this test deletes it explicitly so
        we exercise the unset path.  The application must fail loudly
        rather than silently producing a Fernet over the empty string.
        """
        monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)
        with pytest.raises(RuntimeError, match="TOTP_ENCRYPTION_KEY"):
            mfa_service.get_encryption_key()

    def test_encrypt_and_decrypt_round_trip_under_primary(self):
        """Round-trip with only a primary key matches the bare-Fernet
        behavior of the old implementation.

        Regression guard for the steady-state path: most production
        deploys never set ``TOTP_ENCRYPTION_KEY_OLD``, so the
        MultiFernet must behave indistinguishably from a single-key
        Fernet for that population.
        """
        secret = mfa_service.generate_totp_secret()
        encrypted = mfa_service.encrypt_secret(secret)
        decrypted = mfa_service.decrypt_secret(encrypted)
        assert decrypted == secret

    def test_decrypt_accepts_ciphertext_from_old_key(self, monkeypatch):
        """A ciphertext encrypted under a retired key still decrypts
        once that key has been moved into ``TOTP_ENCRYPTION_KEY_OLD``.

        This is the central guarantee of the C-04 rotation strategy:
        existing MFA enrollments survive a key rotation without
        re-enrollment.  Without this test, a refactor that drops the
        retired-key handling could silently break login for every
        previously-enrolled user.
        """
        from cryptography.fernet import Fernet  # pylint: disable=import-outside-toplevel

        old_key = Fernet.generate_key()
        new_key = Fernet.generate_key()

        # Encrypt a known plaintext under the old key BEFORE rotation.
        plaintext = "JBSWY3DPEHPK3PXP"  # Sample base32 TOTP secret.
        old_cipher = Fernet(old_key)
        ciphertext = old_cipher.encrypt(plaintext.encode("utf-8"))

        # Now rotate: new is primary, old moves to retired.
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", new_key.decode())
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY_OLD", old_key.decode())

        # mfa_service.decrypt_secret routes through the MultiFernet,
        # which must consult the retired key after the primary fails.
        assert mfa_service.decrypt_secret(ciphertext) == plaintext

    def test_encrypt_uses_primary_not_old(self, monkeypatch):
        """encrypt_secret produces ciphertext under the primary key,
        not any retired key.

        The MultiFernet always encrypts with the first key in its
        ordered list.  This test pins that contract by asserting two
        complementary facts about a freshly produced ciphertext:

          1. ``Fernet(primary_key)`` alone can decrypt it.
          2. ``Fernet(retired_key)`` alone CANNOT decrypt it.

        If a future refactor accidentally swapped the order or used a
        random list element for encryption, the second assertion would
        catch it.  Without this test, encryption could regress to
        producing ciphertexts that the rotation script would have to
        re-wrap on every run.
        """
        from cryptography.fernet import Fernet, InvalidToken  # pylint: disable=import-outside-toplevel

        primary_key = Fernet.generate_key()
        retired_key = Fernet.generate_key()
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", primary_key.decode())
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY_OLD", retired_key.decode())

        plaintext = mfa_service.generate_totp_secret()
        ciphertext = mfa_service.encrypt_secret(plaintext)

        # Primary alone must decrypt it.
        primary_only = Fernet(primary_key)
        assert primary_only.decrypt(ciphertext).decode("utf-8") == plaintext

        # Retired alone must NOT decrypt it -- proves primary was used.
        retired_only = Fernet(retired_key)
        with pytest.raises(InvalidToken):
            retired_only.decrypt(ciphertext)

    def test_old_key_list_comma_separated(self, monkeypatch):
        """``TOTP_ENCRYPTION_KEY_OLD`` accepts comma-separated multiple
        retired keys.

        A long-running migration may roll the primary forward more
        than once before the rotation script catches up; in that
        window the operator stacks multiple retired keys.  The
        Fernet list must contain primary plus every retired key.

        Three retired keys is enough to catch off-by-one bugs in the
        split logic without dragging the test into the territory of
        proving all-positive-integers.
        """
        from cryptography.fernet import Fernet  # pylint: disable=import-outside-toplevel

        primary = Fernet.generate_key()
        retired1 = Fernet.generate_key()
        retired2 = Fernet.generate_key()
        retired3 = Fernet.generate_key()

        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", primary.decode())
        monkeypatch.setenv(
            "TOTP_ENCRYPTION_KEY_OLD",
            ",".join(k.decode() for k in (retired1, retired2, retired3)),
        )

        # pylint: disable=protected-access
        fernets = mfa_service._build_fernet_list()
        assert len(fernets) == 4, (
            f"Expected primary + 3 retired = 4 Fernets, got {len(fernets)}"
        )

        # Functional check: each retired key must be reachable through
        # decryption on the resulting MultiFernet, not just present in
        # the count.  Encrypt under each retired key in turn and verify
        # the assembled MultiFernet can read them all.
        from cryptography.fernet import MultiFernet  # pylint: disable=import-outside-toplevel
        multi = MultiFernet(fernets)
        for key in (retired1, retired2, retired3):
            ct = Fernet(key).encrypt(b"probe")
            assert multi.decrypt(ct) == b"probe"

    def test_old_key_ignores_blank_entries(self, monkeypatch):
        """Blank entries in ``TOTP_ENCRYPTION_KEY_OLD`` are skipped.

        Operators editing ``.env`` by hand can easily leave a stray
        comma after pruning a key (``key1,`` -> empty trailing entry)
        or insert a blank between commas (``key1, ,key2``).  Treating
        these as ignored rather than as invalid keys avoids a class of
        avoidable startup failures.
        """
        from cryptography.fernet import Fernet  # pylint: disable=import-outside-toplevel

        primary = Fernet.generate_key()
        retired1 = Fernet.generate_key()
        retired2 = Fernet.generate_key()

        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", primary.decode())
        # Mix every blank-entry pattern: empty between commas, leading
        # space, trailing comma+space.
        monkeypatch.setenv(
            "TOTP_ENCRYPTION_KEY_OLD",
            f"{retired1.decode()}, ,{retired2.decode()}, ",
        )

        # pylint: disable=protected-access
        fernets = mfa_service._build_fernet_list()
        assert len(fernets) == 3, (
            "Blank entries must be skipped; expected primary + 2 retired "
            f"= 3 Fernets, got {len(fernets)}"
        )

    def test_old_key_empty_string_is_steady_state(self, monkeypatch):
        """An empty ``TOTP_ENCRYPTION_KEY_OLD`` produces a single-key
        Fernet list.

        The steady-state production posture has the env var either
        unset or set to the empty string.  Both must yield the
        primary-only list -- an extra empty Fernet would be a runtime
        error.
        """
        from cryptography.fernet import Fernet  # pylint: disable=import-outside-toplevel

        primary = Fernet.generate_key()
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", primary.decode())
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY_OLD", "")

        # pylint: disable=protected-access
        fernets = mfa_service._build_fernet_list()
        assert len(fernets) == 1

    def test_invalid_old_key_raises(self, monkeypatch):
        """An invalid Fernet key in ``TOTP_ENCRYPTION_KEY_OLD`` raises
        ``ValueError`` at startup.

        Failing fast is the right behavior here: a silently-skipped
        bad key would mean ciphertexts written under a missing key
        become unreadable without any startup signal.

        ``Fernet`` raises ``ValueError`` for wrong-length input and
        ``binascii.Error`` (a ``ValueError`` subclass) for non-base64
        input, so a single ``ValueError`` catch covers both forms.
        """
        from cryptography.fernet import Fernet  # pylint: disable=import-outside-toplevel

        primary = Fernet.generate_key()
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", primary.decode())
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY_OLD", "not-a-valid-fernet-key")

        with pytest.raises(ValueError):
            mfa_service._build_fernet_list()  # pylint: disable=protected-access
