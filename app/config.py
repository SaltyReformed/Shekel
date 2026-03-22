"""
Shekel Budget App -- Configuration Classes

Provides Dev, Test, and Prod configuration classes loaded by the
application factory based on the FLASK_ENV environment variable.
"""

import os
from datetime import timedelta

from dotenv import load_dotenv
from sqlalchemy.pool import NullPool

# Load .env file if present (development convenience).
load_dotenv()


class BaseConfig:
    """Shared configuration defaults across all environments."""

    # Flask core
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me-in-production")

    # MFA -- Fernet key for encrypting TOTP secrets at rest.
    TOTP_ENCRYPTION_KEY = os.getenv("TOTP_ENCRYPTION_KEY")

    # SQLAlchemy
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Flask-Login session lifetime
    REMEMBER_COOKIE_DURATION = timedelta(
        days=int(os.getenv("REMEMBER_COOKIE_DURATION_DAYS", "30"))
    )

    # Budget defaults
    DEFAULT_PAY_PERIOD_HORIZON = 52  # ~2 years of biweekly periods
    DEFAULT_PAY_CADENCE_DAYS = 14  # biweekly

    # Logging
    SLOW_REQUEST_THRESHOLD_MS = int(
        os.getenv("SLOW_REQUEST_THRESHOLD_MS", "500")
    )

    # Audit
    AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "365"))


class DevConfig(BaseConfig):
    """Development configuration -- debug mode, local PostgreSQL."""

    DEBUG = True
    # Falls back to peer-auth local connection if DATABASE_URL is not
    # set in .env.  Matches the Quick Start instructions in README.md.
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", "postgresql:///shekel"
    )


class TestConfig(BaseConfig):
    """Test configuration -- separate database, no CSRF, WTF disabled."""

    TESTING = True
    # Falls back to peer-auth local test database if TEST_DATABASE_URL
    # is not set in .env.
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "TEST_DATABASE_URL", "postgresql:///shekel_test"
    )
    WTF_CSRF_ENABLED = False
    LOGIN_DISABLED = False
    RATELIMIT_ENABLED = False

    # Lower bcrypt cost for faster test execution. Default is 12;
    # 4 is the minimum and makes auth/MFA tests ~100x faster.
    BCRYPT_LOG_ROUNDS = 4

    # NullPool closes connections immediately after use -- no pooling.
    # Prevents stale/leaked connections from holding locks that block
    # TRUNCATE between tests.
    # connect_timeout: fail fast (5s) if test-db is unreachable instead
    # of waiting for the OS TCP timeout (120+ seconds).
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": NullPool,
        "connect_args": {"connect_timeout": 5},
    }


class ProdConfig(BaseConfig):
    """Production configuration -- no debug, require real secret key."""

    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")

    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    def __init__(self):
        """Validate production-critical settings on instantiation."""
        if not self.SECRET_KEY or self.SECRET_KEY.startswith("dev-only"):
            raise ValueError(
                "SECRET_KEY must be set to a secure random value in production."
            )
        if not self.SQLALCHEMY_DATABASE_URI:
            raise ValueError("DATABASE_URL must be set in production.")


# Map environment names to config classes for the factory.
CONFIG_MAP = {
    "development": DevConfig,
    "testing": TestConfig,
    "production": ProdConfig,
}
