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
            # No MFA enabled for seed_user — just call reset_mfa.
            reset_mfa(seed_user["user"].email)

            captured = capsys.readouterr()
            assert "MFA is not enabled" in captured.out
