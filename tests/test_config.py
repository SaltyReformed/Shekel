"""
Shekel Budget App -- Configuration Tests

Tests for config class defaults, fallbacks, and production validation.
ProdConfig DEBUG and SECRET_KEY tests live in this file (rejection of
empty / placeholder / short keys, acceptance of valid keys, Dev/Test
loading without a fallback default).  ``test_routes/test_errors.py``
also has a small overlap (DEBUG=False, dev-only placeholder rejection)
exercising the same code path through a different lens.
"""

import pytest

from app.config import BaseConfig, DevConfig, ProdConfig, TestConfig


# A plausible SECRET_KEY shape for tests that expect ProdConfig to
# accept the value: 64-character hex (256 bits).  Not a real secret;
# tests that deploy this value would still fail because it is short
# entropy and well-known.  Used only for unit tests of ProdConfig.
_VALID_SECRET_KEY = "0123456789abcdef" * 4  # 64 chars


class TestDevConfig:
    """Tests for DevConfig defaults and fallbacks."""

    def test_database_uri_is_not_none(self):
        """DevConfig.SQLALCHEMY_DATABASE_URI is never None.

        With the fallback default, this attribute is always a valid
        connection string, either from DATABASE_URL in the environment
        or from the hardcoded peer-auth default.
        """
        assert DevConfig.SQLALCHEMY_DATABASE_URI is not None
        assert DevConfig.SQLALCHEMY_DATABASE_URI != ""

    def test_database_uri_is_valid_postgresql_uri(self):
        """The URI is a valid PostgreSQL connection string."""
        uri = DevConfig.SQLALCHEMY_DATABASE_URI
        assert uri.startswith("postgresql")

    def test_debug_enabled(self):
        """DevConfig has DEBUG=True for development."""
        assert DevConfig.DEBUG is True

    def test_devconfig_loads_without_default(self, monkeypatch):
        """DevConfig has no SECRET_KEY fallback default (F-016).

        BaseConfig used to provide ``"dev-only-change-me-in-production"``
        as a fallback default, which silently leaked the public default
        into any environment that forgot to set ``SECRET_KEY``.  The
        post-remediation behavior is to inherit ``None`` from BaseConfig
        when the env var is unset, and rely on the developer's .env (or
        conftest.py for tests) to populate it.  DevConfig itself does
        not validate, so instantiation must succeed even when the value
        is None.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", None)
        config = DevConfig()
        assert config.SECRET_KEY is None


class TestTestConfig:
    """Tests for TestConfig defaults and fallbacks."""

    def test_database_uri_is_not_none(self):
        """TestConfig.SQLALCHEMY_DATABASE_URI is never None."""
        assert TestConfig.SQLALCHEMY_DATABASE_URI is not None
        assert TestConfig.SQLALCHEMY_DATABASE_URI != ""

    def test_database_uri_is_valid_postgresql_uri(self):
        """The test URI is a valid PostgreSQL connection string."""
        uri = TestConfig.SQLALCHEMY_DATABASE_URI
        assert uri.startswith("postgresql")

    def test_csrf_disabled(self):
        """TestConfig disables CSRF for test convenience."""
        assert TestConfig.WTF_CSRF_ENABLED is False

    def test_testing_flag(self):
        """TestConfig has TESTING=True."""
        assert TestConfig.TESTING is True

    def test_testconfig_loads_without_default(self, monkeypatch):
        """TestConfig has no SECRET_KEY fallback default (F-016).

        Same rationale as ``test_devconfig_loads_without_default``.
        TestConfig must load with ``SECRET_KEY=None`` so unit tests
        that explicitly clear the value do not see a hidden fallback.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", None)
        config = TestConfig()
        assert config.SECRET_KEY is None


class TestProdConfig:
    """Tests for ProdConfig validation.

    Covers SECRET_KEY (empty / placeholder / short / valid),
    DATABASE_URL, and TOTP_ENCRYPTION_KEY paths through
    ``ProdConfig.__init__``.
    """

    def test_prodconfig_rejects_empty_secret_key(self, monkeypatch):
        """ProdConfig raises ValueError when SECRET_KEY is empty (F-016).

        An unset env var collapses to ``None`` after BaseConfig load.
        The empty-string case is the equivalent for an explicitly
        cleared variable.  Both paths must raise with a message that
        names SECRET_KEY and tells the operator how to generate one.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", "")
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        with pytest.raises(ValueError, match="SECRET_KEY is required"):
            ProdConfig()

    def test_prodconfig_rejects_none_secret_key(self, monkeypatch):
        """ProdConfig raises ValueError when SECRET_KEY is None.

        This path fires when the env var is completely unset and the
        BaseConfig fallback default has been removed (F-016 fix).
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", None)
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        with pytest.raises(ValueError, match="SECRET_KEY is required"):
            ProdConfig()

    def test_prodconfig_rejects_placeholder_dev_only(self, monkeypatch):
        """ProdConfig rejects any SECRET_KEY starting with ``dev-only``.

        Defends against the historical fallback default
        ``dev-only-change-me-in-production`` and any future placeholder
        following the same convention.  See F-110.
        """
        monkeypatch.setattr(
            BaseConfig, "SECRET_KEY", "dev-only-change-me-in-production"
        )
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        with pytest.raises(ValueError, match="known placeholder"):
            ProdConfig()

    def test_prodconfig_rejects_placeholder_change_me(self, monkeypatch):
        """ProdConfig rejects the literal ``.env.example`` placeholder.

        ``change-me-to-a-random-secret-key`` was the literal value in
        ``.env.example:11`` before the F-110 fix.  An operator who
        copies ``.env.example`` to ``.env`` without editing must hit a
        startup failure, not silently deploy under a public key.
        """
        monkeypatch.setattr(
            BaseConfig, "SECRET_KEY", "change-me-to-a-random-secret-key"
        )
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        with pytest.raises(ValueError, match="known placeholder"):
            ProdConfig()

    def test_prodconfig_rejects_placeholder_dev_secret(self, monkeypatch):
        """ProdConfig rejects the docker-compose.dev.yml placeholder.

        ``dev-secret-key-not-for-production`` was hardcoded in the dev
        compose file before the F-111 fix.  Belt-and-braces -- the
        compose file no longer hardcodes it, but if the value still
        leaks into a production deployment via copy/paste it must be
        rejected at startup.
        """
        monkeypatch.setattr(
            BaseConfig, "SECRET_KEY", "dev-secret-key-not-for-production"
        )
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        with pytest.raises(ValueError, match="known placeholder"):
            ProdConfig()

    def test_prodconfig_rejects_short_secret_key(self, monkeypatch):
        """ProdConfig rejects SECRET_KEY shorter than 32 characters.

        ASVS L2 V6.4.2 floor.  31 characters is exactly one short.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", "a" * 31)
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        with pytest.raises(ValueError, match="at least 32 characters"):
            ProdConfig()

    def test_prodconfig_accepts_valid_secret_key(self, monkeypatch):
        """ProdConfig instantiates cleanly with a 64-char hex key.

        Closes the loop on the rejection cases above by proving that
        a properly-shaped SECRET_KEY is accepted.  Without this test
        a buggy validator could reject everything and still pass the
        rejection tests.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", _VALID_SECRET_KEY)
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        config = ProdConfig()
        assert config.SECRET_KEY == _VALID_SECRET_KEY

    def test_prodconfig_validates_secret_key_before_database_url(
        self, monkeypatch
    ):
        """SECRET_KEY validation fires before DATABASE_URL validation.

        Operator misconfiguring both should see the SECRET_KEY error
        first because it is the higher-severity gap.  Locking the
        order in a test prevents an accidental refactor from swapping
        them.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", "")
        monkeypatch.setattr(ProdConfig, "SQLALCHEMY_DATABASE_URI", None)
        with pytest.raises(ValueError, match="SECRET_KEY is required"):
            ProdConfig()

    def test_requires_database_url(self, monkeypatch):
        """ProdConfig raises ValueError when DATABASE_URL is missing.

        Unlike DevConfig, production must NEVER fall back to a default.
        Missing DATABASE_URL is a fatal deployment error.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", _VALID_SECRET_KEY)
        monkeypatch.setattr(ProdConfig, "SQLALCHEMY_DATABASE_URI", None)
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", "test-key")
        with pytest.raises(ValueError, match="DATABASE_URL"):
            ProdConfig()

    def test_prod_config_has_pool_settings(self):
        """ProdConfig explicitly sets connection pool options."""
        opts = ProdConfig.SQLALCHEMY_ENGINE_OPTIONS
        assert "pool_size" in opts
        assert "pool_recycle" in opts
        assert "pool_pre_ping" in opts
        assert opts["pool_pre_ping"] is True
        assert opts["connect_args"]["connect_timeout"] == 5

    def test_totp_key_optional_at_startup(self, monkeypatch):
        """ProdConfig does not crash when TOTP_ENCRYPTION_KEY is missing.

        The key is only needed when a user enables MFA, not at app
        startup.  Enforcement happens in mfa_service.get_encryption_key().
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", _VALID_SECRET_KEY)
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        monkeypatch.setattr(BaseConfig, "TOTP_ENCRYPTION_KEY", None)
        config = ProdConfig()
        assert config.TOTP_ENCRYPTION_KEY is None
