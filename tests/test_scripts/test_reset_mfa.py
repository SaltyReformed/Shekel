"""Tests for the MFA reset CLI script."""

import pytest

from app.extensions import db
from app.models.user import MfaConfig
from app.services import mfa_service
from scripts.reset_mfa import reset_mfa


class TestResetMfa:
    """Tests for scripts/reset_mfa.py reset_mfa() function."""

    def _enable_mfa(self, user_id):
        """Helper to enable MFA for a user with a known secret.

        Args:
            user_id: The user's primary key.
        """
        mfa_config = MfaConfig(
            user_id=user_id,
            is_enabled=True,
            totp_secret_encrypted=mfa_service.encrypt_secret("JBSWY3DPEHPK3PXP"),
            backup_codes=mfa_service.hash_backup_codes(["aaaaaaaa", "bbbbbbbb"]),
        )
        db.session.add(mfa_config)
        db.session.commit()

    def test_reset_mfa_disables_for_user(self, app, db, seed_user):
        """reset_mfa() clears MFA config for the given email."""
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)

            reset_mfa(seed_user["user"].email)

            # Reload and verify all MFA fields are cleared.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert config.is_enabled is False
            assert config.totp_secret_encrypted is None
            assert config.backup_codes is None
            assert config.confirmed_at is None

    def test_reset_mfa_user_not_found(self, app, db, capsys):
        """reset_mfa() prints error and exits for unknown email."""
        with app.app_context():
            with pytest.raises(SystemExit) as exc_info:
                reset_mfa("nobody@example.com")

            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "No user found" in captured.out

    def test_reset_mfa_not_enabled(self, app, db, seed_user, capsys):
        """reset_mfa() prints message when MFA is not enabled."""
        with app.app_context():
            # No MFA enabled for seed_user -- just call reset_mfa.
            reset_mfa(seed_user["user"].email)

            captured = capsys.readouterr()
            assert "MFA is not enabled" in captured.out

    def test_reset_empty_email(self, app, db, seed_user, capsys):
        """reset_mfa('') exits with code 1 and prints error without DB changes."""
        with app.app_context():
            mfa_count_before = db.session.query(MfaConfig).count()

            with pytest.raises(SystemExit) as exc_info:
                reset_mfa("")

            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "No user found" in captured.out

            # Verify no database state was altered.
            mfa_count_after = db.session.query(MfaConfig).count()
            assert mfa_count_after == mfa_count_before

    def test_reset_none_email(self, app, db, seed_user, capsys):
        """reset_mfa(None) exits with code 1 and prints error without DB changes."""
        with app.app_context():
            mfa_count_before = db.session.query(MfaConfig).count()

            with pytest.raises(SystemExit) as exc_info:
                reset_mfa(None)

            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "No user found" in captured.out

            # Verify no database state was altered.
            mfa_count_after = db.session.query(MfaConfig).count()
            assert mfa_count_after == mfa_count_before

    def test_reset_partial_mfa_state(self, app, db, seed_user, capsys):
        """reset_mfa() handles a disabled MfaConfig with an orphaned TOTP secret.

        When is_enabled=False but totp_secret_encrypted still has data,
        the function checks is_enabled first and prints 'MFA is not enabled'
        without clearing the orphaned secret.
        """
        with app.app_context():
            # Create an MfaConfig with is_enabled=False but leftover secret.
            mfa_config = MfaConfig(
                user_id=seed_user["user"].id,
                is_enabled=False,
                totp_secret_encrypted=mfa_service.encrypt_secret("JBSWY3DPEHPK3PXP"),
                backup_codes=None,
                confirmed_at=None,
            )
            db.session.add(mfa_config)
            db.session.commit()

            reset_mfa(seed_user["user"].email)

            captured = capsys.readouterr()
            assert "MFA is not enabled" in captured.out

            # Reload and check final state.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert config.is_enabled is False
            # BUG: The orphaned totp_secret_encrypted is NOT cleared because
            # the function returns early when is_enabled is False.  For
            # security, the reset script should clear leftover secrets even
            # when MFA is technically disabled.
            assert config.totp_secret_encrypted is not None
            assert config.backup_codes is None
            assert config.confirmed_at is None

    def test_reset_mfa_idempotent(self, app, db, seed_user, capsys):
        """Calling reset_mfa() twice does not crash on the second call."""
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)

            # First reset disables MFA.
            reset_mfa(seed_user["user"].email)
            first_out = capsys.readouterr().out
            assert "MFA has been disabled" in first_out

            # Second reset -- MFA is already disabled.
            reset_mfa(seed_user["user"].email)
            second_out = capsys.readouterr().out
            assert "MFA is not enabled" in second_out

            # Final DB state: MFA fully cleared.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert config.is_enabled is False
            assert config.totp_secret_encrypted is None
            assert config.backup_codes is None
            assert config.confirmed_at is None
