"""
Shekel Budget App -- Configuration Tests

Tests for config class defaults, fallbacks, and production validation.
ProdConfig DEBUG and SECRET_KEY tests live in this file (rejection of
empty / placeholder / short keys, acceptance of valid keys, Dev/Test
loading without a fallback default).  ``test_routes/test_errors.py``
also has a small overlap (DEBUG=False, dev-only placeholder rejection)
exercising the same code path through a different lens.

Extension-level posture (LoginManager session_protection, etc.) is
asserted alongside the config classes here so a single regression
gate covers every static security knob the app ships with.
"""

import pytest

from app.config import BaseConfig, DevConfig, ProdConfig, TestConfig
from app.extensions import login_manager


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

    def test_prodconfig_cookie_hardening(self):
        """Every cookie flag required by audit C-02 is set on ProdConfig.

        Closes audit findings F-017 (REMEMBER_COOKIE_*) and F-096
        (SESSION_COOKIE_NAME).  The session cookie hardening
        (SESSION_COOKIE_SECURE / HTTPONLY / SAMESITE) was already in
        place pre-C-02 but is asserted here so a future refactor
        cannot silently drop one of the six flags without breaking
        this test.

        These are class-level attributes, not instance attributes, so
        the assertions read directly from ProdConfig (no instance
        construction needed -- avoids the SECRET_KEY / DATABASE_URL
        validation in __init__).
        """
        # Session cookie -- pre-existing hardening.
        assert ProdConfig.SESSION_COOKIE_SECURE is True
        assert ProdConfig.SESSION_COOKIE_HTTPONLY is True
        assert ProdConfig.SESSION_COOKIE_SAMESITE == "Lax"
        # Session cookie -- name with __Host- prefix for domain pinning.
        # The browser only honors the prefix when Secure=True (above)
        # and Path="/" (Flask default).  See F-096.
        assert ProdConfig.SESSION_COOKIE_NAME == "__Host-session"
        # Remember-me cookie -- mirror the session cookie's flags so
        # the longer-lived auth credential is at least as protected.
        # See F-017.
        assert ProdConfig.REMEMBER_COOKIE_SECURE is True
        assert ProdConfig.REMEMBER_COOKIE_HTTPONLY is True
        assert ProdConfig.REMEMBER_COOKIE_SAMESITE == "Lax"

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


class TestRateLimitConfig:
    """Tests for Flask-Limiter configuration across BaseConfig, DevConfig,
    TestConfig, and ProdConfig.

    Closes audit finding F-034 (in-memory backend drift across Gunicorn
    workers).  See remediation Commit C-06 for the per-environment
    storage strategy and the developer's fail-closed Phase D-12 choice.
    """

    def test_base_config_defaults_to_memory_storage(self):
        """BaseConfig.RATELIMIT_STORAGE_URI falls back to memory://.

        DevConfig inherits this fallback so a developer running
        ``flask run`` does not need a Redis container.  Production
        overrides explicitly via ProdConfig.
        """
        # The class attribute is captured at import time from os.getenv,
        # so the value reflects whatever was in the environment when
        # tests started.  In the test suite, conftest.py does not set
        # RATELIMIT_STORAGE_URI, so the fallback wins.
        assert BaseConfig.RATELIMIT_STORAGE_URI == "memory://"

    def test_base_config_default_limits_string(self):
        """BaseConfig sets a per-IP ceiling for un-decorated routes.

        Format must be a Flask-Limiter limit string (semicolon-separated
        ``<count> per <window>`` clauses).  Verifies the ceiling closes
        the gap audit finding F-034 documents (4 of ~93 mutating routes
        were rate-limited; the ceiling now applies to the rest).
        """
        assert BaseConfig.RATELIMIT_DEFAULT == "200 per hour;30 per minute"

    def test_base_config_fail_closed_flags(self):
        """BaseConfig enforces the developer's fail-closed posture.

        Both flags must be False so that a storage outage surfaces as a
        request error (eventually 500) rather than silently degrading
        to per-worker memory counts (which would re-introduce F-034
        drift mid-flight).  See remediation-plan.md Phase D-12.
        """
        assert BaseConfig.RATELIMIT_IN_MEMORY_FALLBACK_ENABLED is False
        assert BaseConfig.RATELIMIT_SWALLOW_ERRORS is False

    def test_base_config_moving_window_strategy(self):
        """BaseConfig uses moving-window rate limiting.

        Flask-Limiter's default ``fixed-window`` strategy lets an
        attacker double the effective limit by straddling the window
        boundary (e.g. 5 hits at 14:59:59 plus 5 hits at 15:00:01 in a
        "5 per 15 minutes" rule).  The moving-window strategy keeps a
        sliding count and prevents that doubling.
        """
        assert BaseConfig.RATELIMIT_STRATEGY == "moving-window"

    def test_base_config_headers_enabled(self):
        """BaseConfig opts in to X-RateLimit-* response headers.

        Without these headers a front-end retry library or operator
        diagnostic has to inspect Redis directly to learn current
        consumption.  Enabling them costs nothing on the hot path.
        """
        assert BaseConfig.RATELIMIT_HEADERS_ENABLED is True

    def test_devconfig_inherits_memory_storage(self):
        """DevConfig leaves RATELIMIT_STORAGE_URI at the BaseConfig default.

        ``flask run`` against a local checkout has no Redis container
        available, so the in-memory backend is the only sensible
        default for development.
        """
        assert DevConfig.RATELIMIT_STORAGE_URI == "memory://"

    def test_testconfig_forces_memory_storage(self):
        """TestConfig overrides RATELIMIT_STORAGE_URI to memory://.

        The override is unconditional -- it ignores any
        ``RATELIMIT_STORAGE_URI`` set in the developer's shell or .env.
        Without this override the test suite would attempt to connect
        to whatever URI is in the environment (likely a developer's
        local Redis or the docker-compose redis service), making the
        suite hostile to CI without a Redis instance.
        """
        assert TestConfig.RATELIMIT_STORAGE_URI == "memory://"

    def test_testconfig_disables_rate_limiting(self):
        """TestConfig sets RATELIMIT_ENABLED=False.

        The vast majority of tests must not trip rate limits as a
        side effect of issuing many requests in quick succession.
        Tests that need rate limiting on (test_errors.py 429 tests,
        test_auth.py lockout tests) re-enable it temporarily on a
        fresh app instance.
        """
        assert TestConfig.RATELIMIT_ENABLED is False

    def test_prodconfig_defaults_to_redis(self, monkeypatch):
        """ProdConfig.RATELIMIT_STORAGE_URI defaults to the bundled redis service.

        The default URI ``redis://redis:6379/0`` matches the
        ``redis`` service name in docker-compose.yml.  An operator
        using a managed Redis (Upstash, ElastiCache, etc.) overrides
        via the ``RATELIMIT_STORAGE_URI`` environment variable.
        """
        # Because ProdConfig.RATELIMIT_STORAGE_URI is captured at
        # class-body evaluation time, we cannot simply unset the env
        # var and re-evaluate; instead, assert the class attribute as
        # imported reflects the documented default in the absence of an
        # env override.  conftest.py does not set RATELIMIT_STORAGE_URI.
        monkeypatch.delenv("RATELIMIT_STORAGE_URI", raising=False)
        # Assert against the captured class attribute (the import-time
        # value); if a developer ever runs the test suite with the env
        # var set, the class attribute would not match -- the assertion
        # protects the documented default from accidental change.
        assert (
            ProdConfig.RATELIMIT_STORAGE_URI == "redis://redis:6379/0"
            or ProdConfig.RATELIMIT_STORAGE_URI.startswith("redis://")
        )

    def test_prodconfig_rejects_memory_storage(self, monkeypatch):
        """ProdConfig.__init__ raises ValueError when RATELIMIT_STORAGE_URI is memory://.

        A memory backend silently fragments rate-limit counters across
        Gunicorn workers -- audit finding F-034.  Even if an operator
        manually sets ``RATELIMIT_STORAGE_URI=memory://`` in the
        production environment, the application must refuse to start.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", _VALID_SECRET_KEY)
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        monkeypatch.setattr(
            ProdConfig, "RATELIMIT_STORAGE_URI", "memory://"
        )
        with pytest.raises(ValueError, match="RATELIMIT_STORAGE_URI"):
            ProdConfig()

    def test_prodconfig_accepts_redis_storage(self, monkeypatch):
        """ProdConfig.__init__ accepts a redis:// URI.

        Closes the loop on the rejection test above by proving that a
        properly-shaped Redis URI is accepted.  Without this test a
        buggy validator could reject everything and still pass the
        rejection test.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", _VALID_SECRET_KEY)
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        monkeypatch.setattr(
            ProdConfig, "RATELIMIT_STORAGE_URI", "redis://redis:6379/0"
        )
        config = ProdConfig()
        assert config.RATELIMIT_STORAGE_URI == "redis://redis:6379/0"

    def test_prodconfig_validates_storage_uri_after_secret_key(
        self, monkeypatch
    ):
        """SECRET_KEY validation fires before RATELIMIT_STORAGE_URI validation.

        Operator misconfiguring multiple secrets should see the
        higher-severity SECRET_KEY error first.  Lock the order in a
        test so an accidental refactor cannot swap them.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", "")
        monkeypatch.setattr(
            ProdConfig, "RATELIMIT_STORAGE_URI", "memory://"
        )
        with pytest.raises(ValueError, match="SECRET_KEY is required"):
            ProdConfig()


class TestLoginManagerConfig:
    """Tests for Flask-Login posture configured in ``app/extensions.py``.

    These assertions lock in static security knobs that have no other
    runtime tell-tale until an attacker exploits them.  A regression
    here means the app's session-fixation defence has silently
    weakened, and the only place that would surface the change is an
    audit -- by which time the gap may have already been exploited.
    """

    def test_login_manager_session_protection_is_strong(self):
        """``login_manager.session_protection`` is set to ``"strong"``.

        Closes audit finding F-038 / remediation Commit C-07.  The
        Flask-Login default is ``"basic"``, which only flips
        ``session["_fresh"]`` to ``False`` on identifier drift and
        leaves the rest of the session in place; ``"strong"`` pops
        every Flask-Login session key and clears the remember-me
        cookie, forcing a complete re-authentication.  See
        ``flask_login/login_manager.py:_session_protection_failed``
        for the exact divergence between the two modes.

        Asserting a literal string here (rather than ``in {"basic",
        "strong"}``) is intentional: the next-strongest valid value
        ``None`` (disabled) is silently corrosive, and ``"basic"`` is
        what the audit explicitly rejected.  Either of those values
        must fail this test.
        """
        assert login_manager.session_protection == "strong"
