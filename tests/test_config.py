"""
Shekel Budget App -- Configuration Tests

Tests for config class defaults, fallbacks, and production validation.
ProdConfig DEBUG and SECRET_KEY tests live in test_routes/test_errors.py;
this file covers DevConfig/TestConfig fallbacks and ProdConfig DATABASE_URL
validation.
"""

import pytest

from app.config import BaseConfig, DevConfig, ProdConfig, TestConfig


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


class TestProdConfig:
    """Tests for ProdConfig validation.

    DEBUG and SECRET_KEY tests live in test_routes/test_errors.py.
    This class covers DATABASE_URL validation only.
    """

    def test_requires_database_url(self, monkeypatch):
        """ProdConfig raises ValueError when DATABASE_URL is missing.

        Unlike DevConfig, production must NEVER fall back to a default.
        Missing DATABASE_URL is a fatal deployment error.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", "secure-key-for-test")
        monkeypatch.setattr(ProdConfig, "SQLALCHEMY_DATABASE_URI", None)
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", "test-key")
        with pytest.raises(ValueError, match="DATABASE_URL"):
            ProdConfig()
