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


# Known placeholder SECRET_KEY values that have appeared in this
# repository's history, .env.example, or docker-compose files.  Any
# value in this set must be rejected at production startup because it
# is publicly known and cannot provide cryptographic confidentiality
# for session cookies or itsdangerous-signed tokens.  See audit
# findings F-001, F-016, F-110, F-111.
_KNOWN_DEFAULT_SECRETS = frozenset({
    "dev-only-change-me-in-production",
    "change-me-to-a-random-secret-key",
    "dev-secret-key-not-for-production",
})

# Minimum acceptable SECRET_KEY length for production.  32 characters
# matches the output of ``secrets.token_hex(16)`` (16 bytes / 128 bits
# of entropy) which is the floor recommended by ASVS L2 V6.4.2.  The
# generation command in .env.example produces a 64-char hex string
# (32 bytes / 256 bits) which is well above this floor.
_MIN_SECRET_KEY_LENGTH = 32


class BaseConfig:
    """Shared configuration defaults across all environments."""

    # Flask core.  No fallback default: production must fail closed
    # when SECRET_KEY is missing.  Dev and test paths set this via
    # the developer's .env file or, in the test suite, conftest.py.
    SECRET_KEY = os.getenv("SECRET_KEY")

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

    # Registration toggle -- set to 'false' to disable public /register.
    REGISTRATION_ENABLED = os.getenv(
        "REGISTRATION_ENABLED", "true"
    ).lower() in ("true", "1", "yes")

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

    # Connection pool settings for production.  Made explicit rather than
    # relying on SQLAlchemy defaults to prevent surprises under load.
    SQLALCHEMY_ENGINE_OPTIONS = {
        # Pool size per Gunicorn worker.  With 2 workers and pool_size=5,
        # the app uses up to 10 base connections + overflow.
        "pool_size": 5,
        # Allow 2 overflow connections per worker for burst traffic.
        # Total max per worker: pool_size + max_overflow = 7.
        # Total across 2 workers: 14 (well within PostgreSQL's default 100).
        "max_overflow": 2,
        # Seconds to wait for a connection from the pool before raising.
        # 30s is generous -- if the pool is exhausted for 30s, something
        # is very wrong and failing fast is better than queueing forever.
        "pool_timeout": 30,
        # Recycle connections after 30 minutes (1800s) to avoid using
        # connections that PostgreSQL or a firewall may have closed.
        # Prevents "connection reset by peer" errors after idle periods.
        "pool_recycle": 1800,
        # Pre-ping: test each connection before using it.  Catches stale
        # connections that pool_recycle missed (e.g. database restart
        # between recycle intervals).  Small overhead (~1ms per checkout)
        # but eliminates "server closed the connection unexpectedly" errors.
        "pool_pre_ping": True,
        # TCP-level connect timeout.  If the database host is unreachable,
        # fail in 5 seconds instead of waiting for the system TCP timeout.
        "connect_args": {"connect_timeout": 5},
    }

    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # __Host- prefix domain-pins the session cookie to this exact origin.
    # The prefix is enforced by the browser only when Secure=True is set
    # (already True above), Path="/" (Flask default), and the cookie has
    # NO Domain attribute (Flask never emits one when SESSION_COOKIE_DOMAIN
    # is unset, which is the default and the case here).  See audit
    # finding F-096.  Modern browsers (Chrome 49+, Firefox 49+, Safari
    # 11+) honor the prefix; older browsers ignore it gracefully and
    # treat it as an opaque cookie name.
    SESSION_COOKIE_NAME = "__Host-session"

    # Remember-me cookie hardening.  Flask-Login's Secure/HttpOnly/SameSite
    # defaults are False/False/None, so a 30-day authentication credential
    # would otherwise leak in cleartext on any HTTP fall-through and ride
    # cross-site requests in a login-CSRF chain.  Mirror the session
    # cookie's flags exactly so the longer-lived credential is at least
    # as protected.  See audit finding F-017.
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"

    def __init__(self):
        """Validate production-critical settings on instantiation.

        Raises:
            ValueError: If ``SECRET_KEY`` is missing, matches a known
                placeholder, or is shorter than the minimum acceptable
                length, or if ``DATABASE_URL`` is missing.  Each branch
                emits a distinct, actionable error message so the
                operator knows exactly which secret is misconfigured.
        """
        if not self.SECRET_KEY:
            raise ValueError(
                "SECRET_KEY is required in production. "
                "Generate with: "
                "python -c 'import secrets; print(secrets.token_hex(32))'"
            )
        if (
            self.SECRET_KEY in _KNOWN_DEFAULT_SECRETS
            or self.SECRET_KEY.startswith("dev-only")
        ):
            raise ValueError(
                "SECRET_KEY matches a known placeholder; "
                "rotate to a secure random value before deploy."
            )
        if len(self.SECRET_KEY) < _MIN_SECRET_KEY_LENGTH:
            raise ValueError(
                "SECRET_KEY must be at least "
                f"{_MIN_SECRET_KEY_LENGTH} characters."
            )
        if not self.SQLALCHEMY_DATABASE_URI:
            raise ValueError("DATABASE_URL must be set in production.")


# Map environment names to config classes for the factory.
CONFIG_MAP = {
    "development": DevConfig,
    "testing": TestConfig,
    "production": ProdConfig,
}
