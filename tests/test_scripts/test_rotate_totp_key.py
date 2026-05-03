"""
Shekel Budget App -- Tests for scripts/rotate_totp_key.py

Covers ``execute_rotation`` and ``main`` entry points of the
TOTP-key rotation script.  ``execute_rotation`` is exercised
directly with the test database session; ``main`` is exercised via
``parse_args`` and the ``argv`` parameter to keep the tests
independent of ``sys.argv``.

Related audit findings: F-030 (TOTP_ENCRYPTION_KEY rotation
infrastructure) addressed in commit C-04.  The rotation script is
the operational control that lets an operator move every existing
ciphertext forward to a freshly-generated primary key without
requiring users to re-enroll MFA.
"""

import logging

import pytest
from cryptography.fernet import Fernet

from app.extensions import db
from app.models.user import MfaConfig, User
from app.services import mfa_service
from app.services.auth_service import hash_password
from scripts.rotate_totp_key import (
    execute_rotation,
    main,
    parse_args,
)


def _make_user(email: str) -> User:
    """Insert a minimal user row and return it.

    The MfaConfig FK requires a real user row.  Tests need users that
    exist in ``auth.users`` but do not need the full ``seed_user``
    payload (settings, account, scenario, etc.).  Creating only the
    User row also keeps the tests fast.

    Args:
        email: Unique email address for the user.

    Returns:
        The flushed (but not committed) User instance.
    """
    user = User(
        email=email,
        password_hash=hash_password("testpass12"),
        display_name=email.split("@")[0],
    )
    db.session.add(user)
    db.session.flush()
    return user


def _make_mfa_config(user_id: int, ciphertext: bytes) -> MfaConfig:
    """Insert an MfaConfig row carrying a specific ciphertext.

    The is_enabled flag is True so the row looks like a real
    enrollment, but the script's filter is ``totp_secret_encrypted IS
    NOT NULL``, not ``is_enabled``.  Both real and partial enrollments
    are covered as a result.

    Args:
        user_id:    FK target.
        ciphertext: The exact bytes to store.  Tests build these with
                    a chosen Fernet key so the rotation script's
                    behavior under that scenario can be asserted.

    Returns:
        The flushed MfaConfig instance.
    """
    config = MfaConfig(
        user_id=user_id,
        is_enabled=True,
        totp_secret_encrypted=ciphertext,
        backup_codes=mfa_service.hash_backup_codes(["a" * 28], rounds=4),
    )
    db.session.add(config)
    db.session.flush()
    return config


class TestExecuteRotation:
    """Tests for ``execute_rotation`` -- the core data operation.

    All tests in this class share the same skeleton: seed a number of
    MfaConfig rows whose ciphertexts were written under controlled
    keys, monkeypatch the env vars to model the post-rotation state,
    call ``execute_rotation``, and assert on both the returned counts
    and the resulting ciphertext state in the database.
    """

    def test_rotate_re_encrypts_all_rows_under_primary(  # pylint: disable=too-many-locals
        self, app, db, monkeypatch
    ):
        """Three configs originally encrypted under a retired key are
        all re-wrapped under the new primary.

        After the rotation:

          - The returned count tuple is (3, 0, 0) -- three rotated,
            zero already-current, zero skipped.
          - Each row's ciphertext decrypts under the new primary key
            ALONE (not just via the MultiFernet) -- the cleanup
            criterion the runbook documents.
          - The original plaintext is preserved -- a critical
            correctness invariant for an MFA secret.
        """
        old_key = Fernet.generate_key()
        new_key = Fernet.generate_key()

        # Seed three users + three MFA configs, each under the OLD key.
        # Use distinct plaintexts so a swap bug (writing the wrong
        # ciphertext to the wrong row) would surface as a mismatch.
        plaintexts = ["AAAA1111", "BBBB2222", "CCCC3333"]
        old_cipher = Fernet(old_key)
        config_ids: list[tuple[int, str]] = []
        for idx, pt in enumerate(plaintexts):
            user = _make_user(f"user{idx}@example.com")
            ct = old_cipher.encrypt(pt.encode("utf-8"))
            cfg = _make_mfa_config(user.id, ct)
            config_ids.append((cfg.id, pt))
        db.session.commit()

        # Move to post-rotation state: new is primary, old is retired.
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", new_key.decode())
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY_OLD", old_key.decode())

        rotated, already_current, skipped = execute_rotation(db.session)

        assert (rotated, already_current, skipped) == (3, 0, 0)

        # Every row must now decrypt under the new primary alone --
        # this is the criterion the runbook tells operators to use
        # before pruning TOTP_ENCRYPTION_KEY_OLD.
        new_only = Fernet(new_key)
        for config_id, expected_plaintext in config_ids:
            cfg = db.session.get(MfaConfig, config_id)
            decrypted = new_only.decrypt(cfg.totp_secret_encrypted)
            assert decrypted.decode("utf-8") == expected_plaintext, (
                f"Plaintext mismatch on config {config_id}: "
                f"expected {expected_plaintext!r}, got {decrypted!r}"
            )

    def test_rotate_is_idempotent(  # pylint: disable=too-many-locals
        self, app, db, monkeypatch
    ):
        """Re-running the rotation after a successful run reports
        every row as ``already_current`` and changes nothing.

        Idempotency is the property that lets an operator re-run the
        script without thinking about whether a previous run
        completed.  The script's idempotency guard is a primary-only
        ``Fernet.decrypt`` probe; this test pins the contract that
        rows already under the primary are skipped (``rotated == 0``,
        ``already_current == N``) and that the on-disk bytes are
        preserved unchanged across the second run.
        """
        old_key = Fernet.generate_key()
        new_key = Fernet.generate_key()

        # Seed two configs under the old key.
        plaintexts = ["FIRSTSEC", "SECONDSE"]
        old_cipher = Fernet(old_key)
        config_ids = []
        for idx, pt in enumerate(plaintexts):
            user = _make_user(f"user{idx}@example.com")
            ct = old_cipher.encrypt(pt.encode("utf-8"))
            cfg = _make_mfa_config(user.id, ct)
            config_ids.append(cfg.id)
        db.session.commit()

        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", new_key.decode())
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY_OLD", old_key.decode())

        # First run: every row is rotated.
        first_result = execute_rotation(db.session)
        assert first_result == (2, 0, 0)

        # Snapshot the ciphertext bytes after the first run so we can
        # confirm the second run does not mutate them.
        after_first = {
            cid: db.session.get(MfaConfig, cid).totp_secret_encrypted
            for cid in config_ids
        }

        # Second run: nothing should change.
        second_result = execute_rotation(db.session)
        assert second_result == (0, 2, 0)

        # The bytes themselves must be unchanged -- if the script were
        # to re-encrypt anyway it would change the IV/timestamp and
        # this assertion would fail.
        for cid in config_ids:
            cfg = db.session.get(MfaConfig, cid)
            assert cfg.totp_secret_encrypted == after_first[cid], (
                f"Idempotency broken: config {cid} ciphertext mutated on "
                "the second rotation run."
            )

    def test_rotate_skips_undecryptable_rows(  # pylint: disable=too-many-locals
        self, app, db, monkeypatch
    ):
        """A ciphertext that cannot be decrypted under any configured
        key is logged and counted as skipped, not crashed on.

        Setup: two configs encrypted under key X.  The post-rotation
        environment configures primary=Y and retired=Z (neither
        matches X).  ``MultiFernet.rotate`` raises ``InvalidToken``
        on every row.

        Expected behavior:

          - The script returns (0, 0, 2): zero rotated, zero already-
            current, two skipped.  Skipping rather than aborting lets
            the operator make progress on the rest of the table.
          - The skipped-row id is included in an ERROR-level log so
            operators can investigate before pruning
            ``TOTP_ENCRYPTION_KEY_OLD``.
          - The on-disk ciphertext is left untouched.
        """
        unknown_key = Fernet.generate_key()  # Will not be configured.
        new_key = Fernet.generate_key()
        retired_key = Fernet.generate_key()

        # Seed two configs encrypted under a key that NEITHER the
        # primary nor the retired list will know about.
        unknown_cipher = Fernet(unknown_key)
        config_ids = []
        original_cts = {}
        for idx in range(2):
            user = _make_user(f"u{idx}@example.com")
            ct = unknown_cipher.encrypt(f"plain{idx}".encode("utf-8"))
            cfg = _make_mfa_config(user.id, ct)
            config_ids.append(cfg.id)
            original_cts[cfg.id] = ct
        db.session.commit()

        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", new_key.decode())
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY_OLD", retired_key.decode())

        rotated, already_current, skipped = execute_rotation(db.session)

        assert (rotated, already_current, skipped) == (0, 0, 2)

        # The on-disk ciphertext must be preserved -- a partial write
        # would leave the row in an unknown state.
        for cid in config_ids:
            cfg = db.session.get(MfaConfig, cid)
            assert cfg.totp_secret_encrypted == original_cts[cid]

    def test_rotate_skips_logs_row_id_at_error_level(
        self, app, db, monkeypatch, caplog
    ):
        """A skipped row produces an ERROR log naming the row id.

        Operators rely on this log to know exactly which user to
        contact for a manual MFA reset.  Without the row id, recovery
        requires a full audit-log dive.
        """
        unknown_key = Fernet.generate_key()
        new_key = Fernet.generate_key()

        user = _make_user("victim@example.com")
        ct = Fernet(unknown_key).encrypt(b"unrecoverable")
        cfg = _make_mfa_config(user.id, ct)
        db.session.commit()

        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", new_key.decode())
        # No retired key -- guarantees rotate() raises InvalidToken.
        monkeypatch.delenv("TOTP_ENCRYPTION_KEY_OLD", raising=False)

        with caplog.at_level(logging.ERROR, logger="scripts.rotate_totp_key"):
            execute_rotation(db.session)

        # The error message must reference the row id; matching by id
        # is more robust than matching by message text.
        error_records = [
            r for r in caplog.records
            if r.levelno == logging.ERROR and f"id={cfg.id}" in r.getMessage()
        ]
        assert len(error_records) == 1, (
            f"Expected exactly one ERROR record naming id={cfg.id}; got "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    def test_rotate_empty_table(self, app, db, monkeypatch):
        """An empty ``auth.mfa_configs`` table returns (0, 0, 0)
        without error.

        The ``db`` fixture truncates ``auth.mfa_configs`` between
        tests, so this scenario actually triggers in CI -- not just a
        contrived edge case.  An off-by-one in the iteration logic
        could trip on the empty case.
        """
        new_key = Fernet.generate_key()
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", new_key.decode())
        monkeypatch.delenv("TOTP_ENCRYPTION_KEY_OLD", raising=False)

        assert db.session.query(MfaConfig).count() == 0
        result = execute_rotation(db.session)
        assert result == (0, 0, 0)

    def test_rotate_ignores_rows_with_null_ciphertext(self, app, db, monkeypatch):
        """Rows with ``totp_secret_encrypted = NULL`` are not counted
        and not rotated.

        ``scripts/reset_mfa.py`` clears the ciphertext to NULL when
        an admin disables MFA for a user, so production tables
        contain a mix of populated and NULL rows.  The rotation must
        ignore the NULLs cleanly -- both for performance (no point
        decrypting NULL) and for correctness (passing NULL into
        Fernet would crash).
        """
        new_key = Fernet.generate_key()
        old_key = Fernet.generate_key()

        # One real config under the old key + one with NULL ciphertext.
        user_real = _make_user("real@example.com")
        ct = Fernet(old_key).encrypt(b"realsecret")
        _make_mfa_config(user_real.id, ct)

        user_null = _make_user("null@example.com")
        null_config = MfaConfig(
            user_id=user_null.id,
            is_enabled=False,
            totp_secret_encrypted=None,
        )
        db.session.add(null_config)
        db.session.commit()

        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", new_key.decode())
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY_OLD", old_key.decode())

        rotated, already_current, skipped = execute_rotation(db.session)
        assert (rotated, already_current, skipped) == (1, 0, 0)

        # The NULL row stays NULL.
        refreshed = db.session.get(MfaConfig, null_config.id)
        assert refreshed.totp_secret_encrypted is None

    def test_rotate_emits_log_event(self, app, db, monkeypatch, caplog):
        """A WARNING-level structured log event names the three counts.

        Operations alerting and the audit log key on this event.  If
        the event name, level, or expected fields drift, every
        downstream filter built against ``totp_key_rotated`` silently
        misses real rotations.
        """
        new_key = Fernet.generate_key()
        old_key = Fernet.generate_key()

        user = _make_user("u@example.com")
        ct = Fernet(old_key).encrypt(b"ABCDEF")
        _make_mfa_config(user.id, ct)
        db.session.commit()

        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", new_key.decode())
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY_OLD", old_key.decode())

        with caplog.at_level(logging.WARNING):
            execute_rotation(db.session)

        # Find the structured event by attribute, not message text.
        matching = [
            r for r in caplog.records
            if getattr(r, "event", None) == "totp_key_rotated"
        ]
        assert len(matching) == 1, (
            f"Expected exactly one totp_key_rotated record; got "
            f"{[(r.levelname, getattr(r, 'event', None)) for r in caplog.records]}"
        )
        record = matching[0]
        assert record.levelno == logging.WARNING
        assert getattr(record, "category", None) == "auth"
        assert getattr(record, "rotated", None) == 1
        assert getattr(record, "already_current", None) == 0
        assert getattr(record, "skipped", None) == 0

    def test_rotate_raises_when_primary_key_unset(self, app, db, monkeypatch):
        """``execute_rotation`` raises ``RuntimeError`` if the primary
        key env var is unset.

        Without a primary key the rotation has no encryption target.
        Failing fast is correct: the alternative is to silently leave
        the table in its prior state, which an operator could
        misinterpret as a successful rotation.
        """
        monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)
        with pytest.raises(RuntimeError, match="TOTP_ENCRYPTION_KEY"):
            execute_rotation(db.session)


class TestMain:
    """Tests for the CLI entry point ``main(argv)``.

    Only the missing-confirm path and the exit-code wiring are
    unit-tested here.  The ``--confirm`` happy path goes through
    ``run_rotation`` which calls ``create_app()`` with the runtime
    ``FLASK_ENV`` -- a different DB from the one ``conftest.py``
    configures.  The data operation that matters is
    ``execute_rotation``; it has full coverage above.
    """

    def test_main_requires_confirm_flag(self, app, db, capsys):
        """Calling ``main([])`` without ``--confirm`` returns 1 and
        leaves the database untouched.

        This is the operational guard against an accidental run.  If
        ``--confirm`` were to become optional the script would
        silently re-encrypt every MFA row, which is exactly the
        destructive operation we want a friction step in front of.
        """
        # Snapshot the table state.  The autouse db fixture truncates
        # before each test, so the count is 0; no need to seed.
        rows_before = db.session.query(MfaConfig).count()

        exit_code = main([])

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "Refusing to run without --confirm" in captured.err

        # No mutation: still zero rows.
        rows_after = db.session.query(MfaConfig).count()
        assert rows_after == rows_before

    def test_main_returns_two_when_rows_skipped(
        self, app, db, monkeypatch, capsys
    ):
        """A successful run that skips a row exits with code 2.

        Exit code 2 is the operator-facing signal that ``--confirm``
        was honored AND the rotation completed AND the operator must
        not yet prune ``TOTP_ENCRYPTION_KEY_OLD``.  Without this
        distinct code, an operator scripting the rotation could miss
        the warning and remove a key that is still in use.

        Bypasses ``run_rotation`` (which would call ``create_app()``
        and use the production DB) by patching it to call
        ``execute_rotation`` against the test session instead.
        """
        unknown_key = Fernet.generate_key()
        new_key = Fernet.generate_key()

        user = _make_user("victim@example.com")
        ct = Fernet(unknown_key).encrypt(b"unrecoverable")
        _make_mfa_config(user.id, ct)
        db.session.commit()

        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", new_key.decode())
        monkeypatch.delenv("TOTP_ENCRYPTION_KEY_OLD", raising=False)

        # Patch run_rotation so main() runs the rotation against the
        # test database, not a freshly-built production app context.
        from scripts import rotate_totp_key  # pylint: disable=import-outside-toplevel
        monkeypatch.setattr(
            rotate_totp_key, "run_rotation",
            lambda: execute_rotation(db.session),
        )

        exit_code = main(["--confirm"])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert "Rotated 0" in captured.out
        assert "skipped 1" in captured.out

    def test_main_returns_zero_on_clean_run(
        self, app, db, monkeypatch, capsys
    ):
        """A run with zero skipped rows exits with code 0.

        The complementary case to the exit-code-2 test: when nothing
        was skipped, the operator can safely move to the cleanup
        step.  This test pins the contract that ``main`` distinguishes
        the two outcomes via exit code.
        """
        new_key = Fernet.generate_key()
        old_key = Fernet.generate_key()

        user = _make_user("u@example.com")
        ct = Fernet(old_key).encrypt(b"recoverable")
        _make_mfa_config(user.id, ct)
        db.session.commit()

        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", new_key.decode())
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY_OLD", old_key.decode())

        from scripts import rotate_totp_key  # pylint: disable=import-outside-toplevel
        monkeypatch.setattr(
            rotate_totp_key, "run_rotation",
            lambda: execute_rotation(db.session),
        )

        exit_code = main(["--confirm"])

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "Rotated 1" in captured.out
        assert "skipped 0" in captured.out


class TestParseArgs:
    """Tests for the argparse wrapper used by ``main``."""

    def test_parse_args_default_confirm_false(self):
        """``parse_args([])`` returns ``confirm=False`` (the safe
        default) without raising.

        ``--confirm`` is intentionally optional at the argparse level
        so ``main([])`` can return 1 cleanly.  If it were
        ``required=True``, ``parse_args`` would call ``sys.exit(2)``
        and ``main([])`` could not return 1.
        """
        args = parse_args([])
        assert args.confirm is False

    def test_parse_args_confirm_true(self):
        """``parse_args(["--confirm"])`` returns ``confirm=True``."""
        args = parse_args(["--confirm"])
        assert args.confirm is True

    def test_parse_args_unknown_arg_exits(self):
        """An unknown flag triggers argparse's ``SystemExit(2)``.

        Catches a regression where an over-permissive parser silently
        accepts typos like ``--confrim`` (no flag fires, script
        no-ops, operator thinks the rotation succeeded).
        """
        with pytest.raises(SystemExit):
            parse_args(["--unknown-flag"])
