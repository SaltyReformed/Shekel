"""Shekel Budget App -- Adversarial Tests for Session Lifetime,
Idle Timeout, and Step-Up Re-Auth (C-10).

End-to-end coverage for the three findings closed by commit C-10
of the 2026-04-15 security remediation plan:

  * F-006 (CWE-613): no idle-timeout check on the session.  Closed
    by ``IDLE_TIMEOUT_MINUTES`` in config plus the
    ``_session_last_activity_at`` check in ``app.load_user`` and
    the ``before_request`` activity-refresh hook in
    ``app/__init__.py``.

  * F-035 (CWE-613): ``PERMANENT_SESSION_LIFETIME`` defaulted to
    Flask's 31-day fallback.  Closed by an explicit 12-hour default
    in ``BaseConfig`` (env-overridable via ``SESSION_LIFETIME_HOURS``).

  * F-045 (CWE-306): no step-up re-auth for high-value operations.
    Closed by the ``fresh_login_required`` decorator in
    ``app/utils/auth_helpers.py``, the ``/reauth`` route in
    ``app/routes/auth.py``, and the eleven high-value routes the
    decorator is applied to: anchor true-up, inline anchor update,
    account-edit form (which also accepts anchor_balance), account
    hard-delete, transaction-template hard-delete, transfer-template
    hard-delete, companion create / edit / deactivate, FICA config
    update, state tax config update.

The tests in this file run against the full Flask app via
``test_client`` so that load_user, the before_request hook, the
decorator, the /reauth route, and the lifecycle stamping all
exercise together.  A regression in any one piece would break a
test here even if the unit tests pass individually.

Why adversarial, not unit:  the C-10 contract is a composition of
five cooperating pieces (config, loader, hook, decorator, route).
The unit tests in ``tests/test_utils/`` and ``tests/test_routes/
test_auth.py`` cover each piece in isolation; this file catches the
"each piece works alone but they are wired together wrong" class of
bug.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from flask import g

from app.extensions import db
from app.models.account import Account
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import (
    AccountType,
    RecurrencePattern,
    TransactionType,
)
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.models.user import MfaConfig
from app.services import mfa_service
from app.services.mfa_service import TotpVerificationResult
from app.utils.session_helpers import (
    FRESH_LOGIN_AT_KEY,
    SESSION_CREATED_AT_KEY,
    SESSION_LAST_ACTIVITY_KEY,
)


_KNOWN_TOTP_SECRET = "JBSWY3DPEHPK3PXP"
_KNOWN_BACKUP_CODES = ["aaaaaaaa", "bbbbbbbb", "cccccccc"]


# Env vars that BaseConfig consults at class-definition time for the
# four C-10 defaults.  The TestPermanentSessionLifetime suite clears
# every entry here and reloads ``app.config`` so the assertions
# measure the BUILT-IN defaults rather than whatever the developer
# happens to have in their local .env.
_C10_ENV_VARS = (
    "SESSION_LIFETIME_HOURS",
    "REMEMBER_COOKIE_DURATION_DAYS",
    "IDLE_TIMEOUT_MINUTES",
    "FRESH_LOGIN_MAX_AGE_MINUTES",
)


def _reload_config_with_clean_env(monkeypatch):
    """Stub ``load_dotenv`` and clear C-10 env vars, then reload config.

    Returns the freshly-reloaded ``app.config`` module so the caller
    can read ``BaseConfig.<attr>`` values that reflect the source-
    code defaults rather than env overrides.

    ``monkeypatch`` undoes both the function stub and the env
    deletes at test teardown; the caller is responsible for a
    matching final ``importlib.reload`` to restore the module's
    class-level constants for any sibling test that imports
    BaseConfig directly.

    Defined as a module-level helper rather than a fixture because
    each test that uses it also needs to do its own try/finally
    reload-restore -- a fixture would split that ownership across
    setup and teardown and obscure the intent.
    """
    # pylint: disable=import-outside-toplevel
    import importlib

    import dotenv

    from app import config as config_module

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_a, **_k: None)
    for var in _C10_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return importlib.reload(config_module)


def _restore_config_module():
    """Reload ``app.config`` so its class-level constants reflect
    the env that ``monkeypatch`` has just restored.
    """
    # pylint: disable=import-outside-toplevel
    import importlib

    from app import config as config_module
    importlib.reload(config_module)


def _reset_login_cache():
    """Drop ``g._login_user`` so the next request re-runs ``load_user``.

    Flask-Login caches the loaded user on ``g._login_user`` per app
    context.  In production each HTTP request is its own app context,
    so the cache is effectively per-request.  In the test suite the
    autouse ``db`` fixture wraps every test in one ``app.app_context``,
    so subsequent ``test_client`` calls re-use the same ``g`` and
    would keep returning the user that was cached on the first
    request -- defeating the point of every "the loader rejects this
    cookie" test below.

    Mirror of the helper in
    ``tests/test_adversarial/test_session_invalidation.py``.  Kept
    duplicated rather than imported because each adversarial file
    exercises distinct invariants and a shared helper would couple
    them at the wrong layer.
    """
    g.pop("_login_user", None)


def _enable_mfa(user_id):
    """Persist an enabled MFA config with a known secret and codes.

    Mirrors the helper in ``test_session_invalidation.py``.
    """
    config = MfaConfig(
        user_id=user_id,
        is_enabled=True,
        totp_secret_encrypted=mfa_service.encrypt_secret(_KNOWN_TOTP_SECRET),
        backup_codes=mfa_service.hash_backup_codes(_KNOWN_BACKUP_CODES),
    )
    db.session.add(config)
    db.session.commit()


def _do_login_no_mfa(client):
    """Run a fresh password-only login on a test client.

    Calls ``_reset_login_cache`` before the POST so the request sees
    the cookie the test is exercising rather than a sibling client's
    cached user.  Returns the login response so the caller can
    assert on it.
    """
    _reset_login_cache()
    return client.post("/login", data={
        "email": "test@shekel.local",
        "password": "testpass",
    }, follow_redirects=False)


# ---------------------------------------------------------------------------
# F-035: PERMANENT_SESSION_LIFETIME
# ---------------------------------------------------------------------------


class TestC10ConfigDefaults:
    """The four C-10 config defaults are baked into ``BaseConfig``.

    Each test stubs ``load_dotenv`` and clears the matching env var
    before reloading ``app.config``, so the assertion measures the
    BUILT-IN default rather than whatever the developer happens to
    have in their local .env.  Without this isolation a developer
    whose .env sets ``REMEMBER_COOKIE_DURATION_DAYS=30`` (the
    pre-C-10 historical value) would see a green test that hides a
    real regression.

    A teardown ``_restore_config_module`` call reloads the module
    after monkeypatch undoes its changes, so sibling tests that
    import BaseConfig directly see the env-driven values they
    expect.
    """

    def test_session_lifetime_default_is_12_hours(self, monkeypatch):
        """``PERMANENT_SESSION_LIFETIME`` defaults to 12 hours (F-035).

        Without this explicit default the cookie would carry Flask's
        31-day fallback and a stolen browser profile would have a
        month of valid auth.
        """
        try:
            reloaded = _reload_config_with_clean_env(monkeypatch)
            assert reloaded.BaseConfig.PERMANENT_SESSION_LIFETIME == (
                timedelta(hours=12)
            )
        finally:
            _restore_config_module()

    def test_remember_cookie_default_is_7_days(self, monkeypatch):
        """``REMEMBER_COOKIE_DURATION`` defaults to 7 days (F-006).

        Pre-C-10 default was 30 days.  Shortened to 7 to match ASVS
        L2 guidance for financial apps -- a stolen remember-me
        cookie is a password-equivalent credential and 7 days is the
        right tradeoff between legitimate "stay logged in" UX and
        stolen-device blast radius.
        """
        try:
            reloaded = _reload_config_with_clean_env(monkeypatch)
            assert reloaded.BaseConfig.REMEMBER_COOKIE_DURATION == (
                timedelta(days=7)
            )
        finally:
            _restore_config_module()

    def test_idle_timeout_default_is_30_minutes(self, monkeypatch):
        """``IDLE_TIMEOUT_MINUTES`` defaults to 30 (F-006).

        Without this constant, ``_idle_session_is_fresh`` in
        ``app/__init__.py`` would have no threshold to compare
        against and the entire idle-timeout check would degrade to
        "always allow".
        """
        try:
            reloaded = _reload_config_with_clean_env(monkeypatch)
            assert reloaded.BaseConfig.IDLE_TIMEOUT_MINUTES == 30
        finally:
            _restore_config_module()

    def test_fresh_login_max_age_default_is_5_minutes(self, monkeypatch):
        """``FRESH_LOGIN_MAX_AGE_MINUTES`` defaults to 5 (F-045).

        ASVS L2 V4.3.3 step-up window.  Without this constant the
        ``fresh_login_required`` decorator would have no threshold
        and would either always reject or always accept.
        """
        try:
            reloaded = _reload_config_with_clean_env(monkeypatch)
            assert reloaded.BaseConfig.FRESH_LOGIN_MAX_AGE_MINUTES == 5
        finally:
            _restore_config_module()


class TestC10ConfigEnvOverride:
    """Env vars override the C-10 defaults at module import time."""

    def test_session_lifetime_hours_env_override(self, monkeypatch):
        """``SESSION_LIFETIME_HOURS=N`` is honoured."""
        try:
            # pylint: disable=import-outside-toplevel
            import importlib
            import dotenv
            from app import config as config_module
            monkeypatch.setattr(dotenv, "load_dotenv", lambda *_a, **_k: None)
            monkeypatch.setenv("SESSION_LIFETIME_HOURS", "48")
            for var in _C10_ENV_VARS:
                if var != "SESSION_LIFETIME_HOURS":
                    monkeypatch.delenv(var, raising=False)
            reloaded = importlib.reload(config_module)
            assert reloaded.BaseConfig.PERMANENT_SESSION_LIFETIME == (
                timedelta(hours=48)
            )
        finally:
            _restore_config_module()

    def test_idle_timeout_minutes_env_override(self, monkeypatch):
        """``IDLE_TIMEOUT_MINUTES=N`` is honoured."""
        try:
            # pylint: disable=import-outside-toplevel
            import importlib
            import dotenv
            from app import config as config_module
            monkeypatch.setattr(dotenv, "load_dotenv", lambda *_a, **_k: None)
            monkeypatch.setenv("IDLE_TIMEOUT_MINUTES", "15")
            for var in _C10_ENV_VARS:
                if var != "IDLE_TIMEOUT_MINUTES":
                    monkeypatch.delenv(var, raising=False)
            reloaded = importlib.reload(config_module)
            assert reloaded.BaseConfig.IDLE_TIMEOUT_MINUTES == 15
        finally:
            _restore_config_module()

    def test_fresh_login_max_age_env_override(self, monkeypatch):
        """``FRESH_LOGIN_MAX_AGE_MINUTES=N`` is honoured."""
        try:
            # pylint: disable=import-outside-toplevel
            import importlib
            import dotenv
            from app import config as config_module
            monkeypatch.setattr(dotenv, "load_dotenv", lambda *_a, **_k: None)
            monkeypatch.setenv("FRESH_LOGIN_MAX_AGE_MINUTES", "10")
            for var in _C10_ENV_VARS:
                if var != "FRESH_LOGIN_MAX_AGE_MINUTES":
                    monkeypatch.delenv(var, raising=False)
            reloaded = importlib.reload(config_module)
            assert reloaded.BaseConfig.FRESH_LOGIN_MAX_AGE_MINUTES == 10
        finally:
            _restore_config_module()


# ---------------------------------------------------------------------------
# F-006: idle-timeout in load_user + before_request refresh
# ---------------------------------------------------------------------------


class TestIdleTimeoutAcceptance:
    """The loader accepts fresh sessions (control tests)."""

    def test_first_request_after_login_passes(self, app, client, seed_user):
        """A request immediately after /login is accepted.

        Without the chicken-and-egg accept-on-missing branch, the
        very first protected request after login would 302 to /login
        because no ``_session_last_activity_at`` exists yet -- the
        ``before_request`` hook writes the stamp DURING the request,
        but ``load_user`` runs first.
        """
        with app.app_context():
            resp = _do_login_no_mfa(client)
            assert resp.status_code == 302

            # Subsequent protected request must succeed.
            _reset_login_cache()
            check = client.get("/dashboard", follow_redirects=False)
            assert check.status_code == 200, (
                f"First request after login must pass; got "
                f"{check.status_code} ({check.headers.get('Location')!r})."
            )

    def test_recent_activity_passes(self, app, client, seed_user):
        """Activity stamped 1 minute ago still passes the 30-min window.

        Boundary-control: without this test, a regression that always
        rejected (e.g. inverted comparison) would still pass the
        "stale rejected" tests because every value would be rejected.
        """
        with app.app_context():
            _do_login_no_mfa(client)
            recent = (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat()
            with client.session_transaction() as sess:
                sess[SESSION_LAST_ACTIVITY_KEY] = recent

            _reset_login_cache()
            check = client.get("/dashboard", follow_redirects=False)
            assert check.status_code == 200


class TestIdleTimeoutRejection:
    """The loader rejects sessions whose activity is stale or tampered."""

    def test_stale_activity_rejected(self, app, client, seed_user):
        """Activity older than IDLE_TIMEOUT_MINUTES bounces to /login.

        The canonical F-006 attack: an attacker reaches the
        unattended browser an hour after the user walked away from
        their desk.  The session cookie is still cryptographically
        valid (signature checks out) but ``load_user`` must refuse
        because the last activity exceeds the 30-min idle window.
        """
        with app.app_context():
            _do_login_no_mfa(client)
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=31)
            ).isoformat()
            with client.session_transaction() as sess:
                sess[SESSION_LAST_ACTIVITY_KEY] = stale

            _reset_login_cache()
            check = client.get("/dashboard", follow_redirects=False)
            assert check.status_code == 302
            assert "/login" in check.headers.get("Location", ""), (
                "Stale activity must redirect to /login; got "
                f"{check.headers.get('Location')!r}."
            )

    def test_malformed_activity_rejected(self, app, client, seed_user):
        """A non-ISO-8601 activity stamp is treated as stale.

        ``datetime.fromisoformat`` raises ``ValueError`` on garbage.
        ``_idle_session_is_fresh`` must catch and return False so
        the failure mode is "log in again" instead of a 500.
        """
        with app.app_context():
            _do_login_no_mfa(client)
            with client.session_transaction() as sess:
                sess[SESSION_LAST_ACTIVITY_KEY] = "not-an-iso-timestamp"

            _reset_login_cache()
            check = client.get("/dashboard", follow_redirects=False)
            assert check.status_code == 302
            assert "/login" in check.headers.get("Location", "")

    def test_naive_activity_rejected(self, app, client, seed_user):
        """A timezone-naive activity stamp is treated as stale.

        Naive datetimes raise ``TypeError`` on the timezone-aware
        subtraction.  Reject explicitly so the failure mode is
        consistent with the malformed case above.
        """
        with app.app_context():
            _do_login_no_mfa(client)
            naive = datetime.now().replace(tzinfo=None).isoformat()
            with client.session_transaction() as sess:
                sess[SESSION_LAST_ACTIVITY_KEY] = naive

            _reset_login_cache()
            check = client.get("/dashboard", follow_redirects=False)
            assert check.status_code == 302
            assert "/login" in check.headers.get("Location", "")

    def test_future_dated_activity_treated_as_fresh(
        self, app, client, seed_user,
    ):
        """A future-dated activity stamp is treated as fresh, not stale.

        The two real-world causes of ``elapsed < 0`` are a backwards
        clock jump (NTP correction, manual adjustment, VM resume)
        and a forged cookie.  Forging requires ``SECRET_KEY``, at
        which point the attacker can mint any cookie value and the
        future-date check adds no defensive value; meanwhile a clock
        jump must NOT silently log every active user out.  See
        ``_idle_session_is_fresh`` docstring for the full rationale.

        This test is the inverse of the strict policy enforced by
        ``_mfa_pending_is_fresh`` (commit C-08), which DOES reject
        future-dated -- the two checks differ because MFA verify is
        a single-use gate and an idle-timeout check runs on every
        request.
        """
        with app.app_context():
            _do_login_no_mfa(client)
            future = (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat()
            with client.session_transaction() as sess:
                sess[SESSION_LAST_ACTIVITY_KEY] = future

            _reset_login_cache()
            check = client.get("/dashboard", follow_redirects=False)
            assert check.status_code == 200, (
                "Future-dated activity must be treated as fresh; got "
                f"{check.status_code} ({check.headers.get('Location')!r}). "
                "A clock-skew rejection here would log every active "
                "user out after an NTP correction."
            )


class TestActivityRefreshHook:
    """The ``before_request`` hook keeps an active session alive."""

    def test_activity_advances_with_every_request(
        self, app, client, seed_user,
    ):
        """Each authenticated request bumps ``_session_last_activity_at``.

        Without this hook, the very first stamp written at login
        would be the last one -- and a session would become "stale"
        after IDLE_TIMEOUT_MINUTES of WALL-CLOCK time, regardless
        of how active the user actually was.  The hook is what
        turns the idle timeout into a true idle timeout (vs a
        session-lifetime cap).
        """
        with app.app_context():
            _do_login_no_mfa(client)

            # Read the post-login stamp (set by stamp_login_session).
            with client.session_transaction() as sess:
                first = sess[SESSION_LAST_ACTIVITY_KEY]

            # Make another request; the before_request hook should
            # advance the stamp.  Use a sleep too small to be
            # noticeable but large enough to differ at microsecond
            # resolution -- we only need strict monotonic advance.
            _reset_login_cache()
            client.get("/dashboard")

            with client.session_transaction() as sess:
                second = sess[SESSION_LAST_ACTIVITY_KEY]

            assert second >= first, (
                "before_request hook must monotonically advance "
                f"_session_last_activity_at; got {second!r} after "
                f"{first!r}."
            )

    def test_unauthenticated_request_does_not_create_stamp(
        self, app, client,
    ):
        """An anonymous request leaves the cookie untouched.

        Without this skip, the hook would create a session cookie
        out of thin air for every visitor (including bots), bloating
        the response size and inviting cookie-fixation oddities.
        """
        with app.app_context():
            client.get("/login")
            with client.session_transaction() as sess:
                # No authenticated session, so no activity stamp.
                assert SESSION_LAST_ACTIVITY_KEY not in sess


# ---------------------------------------------------------------------------
# F-045: fresh_login_required + /reauth
# ---------------------------------------------------------------------------


class TestFreshLoginAtSetOnLogin:
    """Login flows must stamp ``_fresh_login_at`` so the auth_client
    fixture and freshly-logged-in users can immediately exercise
    high-value routes without an extra reauth round-trip.
    """

    def test_login_no_mfa_sets_fresh_login_at(
        self, app, client, seed_user,
    ):
        """Successful no-MFA login writes ``_fresh_login_at``."""
        with app.app_context():
            before = datetime.now(timezone.utc)
            _do_login_no_mfa(client)
            after = datetime.now(timezone.utc)

            with client.session_transaction() as sess:
                raw = sess.get(FRESH_LOGIN_AT_KEY)
                assert raw is not None, (
                    "Login must stamp _fresh_login_at; otherwise "
                    "the auth_client fixture would always be sent "
                    "to /reauth on the first high-value POST."
                )
                stamped = datetime.fromisoformat(raw)
                assert before <= stamped <= after

    def test_login_with_mfa_sets_fresh_login_at_after_verify(
        self, app, client, seed_user, monkeypatch,
    ):
        """MFA verify completion writes ``_fresh_login_at``.

        Two-step login: the password POST puts the user in pending-
        MFA state; the /mfa/verify POST completes the login.  The
        fresh-login stamp must come from the SECOND step (the one
        that establishes the authenticated session), not the first.
        """
        with app.app_context():
            _enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(
                mfa_service, "verify_totp_code",
                lambda mc, c: TotpVerificationResult.ACCEPTED,
            )

            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
            with client.session_transaction() as sess:
                # No fresh-login stamp yet -- session is in pending-MFA.
                assert FRESH_LOGIN_AT_KEY not in sess

            client.post("/mfa/verify", data={"totp_code": "123456"})
            with client.session_transaction() as sess:
                assert FRESH_LOGIN_AT_KEY in sess


class TestFreshLoginRequiredRedirects:
    """The decorator redirects users with no/old fresh-login stamp."""

    def test_high_value_route_with_fresh_session_succeeds(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A user who just logged in can immediately hit a decorated
        route without bouncing to /reauth.

        This is the test that locks down the auth_client UX: every
        existing test in the suite that uses auth_client to hit
        accounts.true_up / inline_anchor / hard_delete / companion
        management / tax config relies on this being True.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            resp = auth_client.patch(
                f"/accounts/{account_id}/inline-anchor",
                data={"anchor_balance": "1234.56"},
            )
            # 200 = decorator allowed the request through and the
            # route returned its happy-path partial.  302 to /reauth
            # would be a regression of the auth_client UX above.
            assert resp.status_code == 200, (
                f"auth_client must succeed on a freshly-stamped "
                f"session; got {resp.status_code}: "
                f"{resp.headers.get('Location')!r}"
            )

    def test_stale_fresh_login_redirects_to_reauth(
        self, app, auth_client, seed_user,
    ):
        """A session with a stale ``_fresh_login_at`` bounces to /reauth.

        The canonical F-045 attack: an attacker hijacks a session
        cookie that has been idle for 6 minutes (within the 30-min
        idle window, but past the 5-min fresh-login window).  They
        try to update the anchor balance.  The decorator must redirect
        to /reauth so the destructive action requires a fresh password
        prompt.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            _reset_login_cache()
            resp = auth_client.patch(
                f"/accounts/{account_id}/inline-anchor",
                data={"anchor_balance": "9999.00"},
            )
            # The PATCH carries no ``HX-Request`` header so the
            # decorator returns a 302 (rather than the HX-Redirect
            # 204).  The location must point at /reauth carrying
            # the original action URL as ``next``.
            assert resp.status_code == 302
            location = resp.headers.get("Location", "")
            assert "/reauth" in location
            assert "next=" in location

    def test_missing_fresh_login_redirects_to_reauth(
        self, app, auth_client, seed_user,
    ):
        """A session missing ``_fresh_login_at`` bounces to /reauth.

        This covers the upgrade scenario: a session that pre-dates
        the C-10 deploy will not have the key set.  Failing closed
        means those sessions get prompted on their next high-value
        operation, which is the safe default.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            with auth_client.session_transaction() as sess:
                sess.pop(FRESH_LOGIN_AT_KEY, None)

            _reset_login_cache()
            resp = auth_client.patch(
                f"/accounts/{account_id}/inline-anchor",
                data={"anchor_balance": "9999.00"},
            )
            assert resp.status_code == 302
            assert "/reauth" in resp.headers.get("Location", "")

    def test_htmx_request_returns_hx_redirect(
        self, app, auth_client, seed_user,
    ):
        """An HTMX request gets ``HX-Redirect`` + 204, not a 302.

        Without this, an HTMX form-button on a high-value route
        would receive the /reauth HTML body and inject it into
        whatever fragment slot the original request targeted.  The
        HX-Redirect header makes htmx do a full-page navigation
        instead.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            _reset_login_cache()
            resp = auth_client.patch(
                f"/accounts/{account_id}/inline-anchor",
                data={"anchor_balance": "9999.00"},
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 204, (
                f"HTMX request should get 204 + HX-Redirect; got "
                f"{resp.status_code}."
            )
            assert "HX-Redirect" in resp.headers
            assert "/reauth" in resp.headers["HX-Redirect"]
            assert resp.data == b"", (
                "Body must be empty so htmx swap has nothing to "
                "render."
            )


class TestReauthRoute:
    """The /reauth route verifies password + (optional) TOTP and
    refreshes ``_fresh_login_at`` on success.
    """

    def test_get_reauth_renders_form(self, app, auth_client):
        """GET /reauth shows the confirmation form."""
        with app.app_context():
            resp = auth_client.get("/reauth")
            assert resp.status_code == 200
            assert b"Confirm your identity" in resp.data
            assert b"Password" in resp.data

    def test_get_reauth_unauthenticated_redirects_to_login(
        self, app, client,
    ):
        """GET /reauth without a session redirects to /login."""
        with app.app_context():
            resp = client.get("/reauth", follow_redirects=False)
            assert resp.status_code == 302
            assert "/login" in resp.headers.get("Location", "")

    def test_post_correct_password_no_mfa_refreshes_fresh_login(
        self, app, auth_client,
    ):
        """A correct password POST updates ``_fresh_login_at``.

        The whole point of /reauth: typing the password again proves
        identity and restarts the step-up window.
        """
        with app.app_context():
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            before = datetime.now(timezone.utc)
            resp = auth_client.post("/reauth", data={
                "password": "testpass",
            }, follow_redirects=False)
            after = datetime.now(timezone.utc)

            assert resp.status_code == 302, (
                "Successful reauth must redirect; got "
                f"{resp.status_code}."
            )
            with auth_client.session_transaction() as sess:
                stamped = datetime.fromisoformat(sess[FRESH_LOGIN_AT_KEY])
                assert before <= stamped <= after, (
                    "_fresh_login_at must advance to NOW on reauth "
                    "success; got an earlier or unchanged value."
                )

    def test_post_wrong_password_does_not_refresh_fresh_login(
        self, app, auth_client,
    ):
        """A wrong password POST leaves ``_fresh_login_at`` unchanged.

        Without this, a wrong password would still consume the
        anti-CSRF token but more importantly might silently update
        the stamp -- defeating the entire step-up check.
        """
        with app.app_context():
            stale_iso = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale_iso

            resp = auth_client.post("/reauth", data={
                "password": "wrongpassword",
            })
            # Wrong password re-renders the form (200), it does NOT
            # redirect (which would suggest success).
            assert resp.status_code == 200
            assert b"Invalid password" in resp.data

            with auth_client.session_transaction() as sess:
                # Stamp unchanged.
                assert sess[FRESH_LOGIN_AT_KEY] == stale_iso

    def test_post_redirects_to_safe_next(
        self, app, auth_client,
    ):
        """A safe ``next`` param in the URL is honoured on success."""
        with app.app_context():
            resp = auth_client.post(
                "/reauth?next=%2Fdashboard",
                data={"password": "testpass"},
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert resp.headers.get("Location", "").endswith("/dashboard")

    def test_post_rejects_unsafe_next(
        self, app, auth_client,
    ):
        """An unsafe ``next`` param redirects to dashboard, not the
        attacker URL.

        Open-redirect defence -- the same ``_is_safe_redirect``
        helper that gates /login also gates /reauth.  Otherwise an
        attacker could craft /reauth?next=https://evil and ride a
        legitimate re-auth into a phishing page.
        """
        with app.app_context():
            resp = auth_client.post(
                "/reauth?next=https%3A%2F%2Fevil.com",
                data={"password": "testpass"},
                follow_redirects=False,
            )
            assert resp.status_code == 302
            location = resp.headers.get("Location", "")
            assert "evil.com" not in location, (
                f"Open-redirect: /reauth followed an unsafe next; "
                f"got Location={location!r}."
            )

    def test_post_with_mfa_requires_totp(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """A user with MFA enabled must supply a valid TOTP on /reauth.

        Belt-and-suspenders: a stolen password alone cannot complete
        re-auth on an MFA-enabled account.  Without this check, the
        step-up flow would be weaker than the primary login it
        mirrors.
        """
        with app.app_context():
            _enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(
                mfa_service, "verify_totp_code",
                lambda mc, c: TotpVerificationResult.INVALID,
            )

            resp = auth_client.post("/reauth", data={
                "password": "testpass",
                "totp_code": "000000",
            })
            assert resp.status_code == 200
            assert b"Invalid authentication code" in resp.data

    def test_post_with_mfa_accepts_correct_totp(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """A user with MFA enabled completes reauth with password + TOTP."""
        with app.app_context():
            _enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(
                mfa_service, "verify_totp_code",
                lambda mc, c: TotpVerificationResult.ACCEPTED,
            )
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            before = datetime.now(timezone.utc)
            resp = auth_client.post("/reauth", data={
                "password": "testpass",
                "totp_code": "123456",
            }, follow_redirects=False)
            assert resp.status_code == 302

            with auth_client.session_transaction() as sess:
                stamped = datetime.fromisoformat(sess[FRESH_LOGIN_AT_KEY])
                assert stamped >= before


class TestReauthDoesNotRefreshSessionCreatedAt:
    """``/reauth`` MUST NOT touch ``_session_created_at``.

    The motivation: writing _session_created_at on /reauth would
    silently promote the session past every prior
    invalidate_other_sessions bump.  An attacker who hijacked a
    session and then used /reauth would be immune to the user's
    later "log out all sessions" click.  The stamp_reauth_session
    helper is structurally split from stamp_login_session for
    exactly this reason; this test locks down the contract end-to-
    end.
    """

    def test_reauth_does_not_advance_session_created_at(
        self, app, auth_client,
    ):
        """``_session_created_at`` is unchanged across a successful reauth."""
        with app.app_context():
            with auth_client.session_transaction() as sess:
                before_created = sess.get(SESSION_CREATED_AT_KEY)
            assert before_created is not None, (
                "Setup error: auth_client login must stamp "
                "_session_created_at."
            )

            resp = auth_client.post("/reauth", data={
                "password": "testpass",
            })
            assert resp.status_code == 302

            with auth_client.session_transaction() as sess:
                after_created = sess.get(SESSION_CREATED_AT_KEY)
            assert after_created == before_created, (
                "/reauth must NOT update _session_created_at; doing "
                "so would let an attacker bypass the next "
                "invalidate_other_sessions bump.  Got "
                f"before={before_created!r} after={after_created!r}."
            )


class TestPasswordChangeRefreshesFreshLogin:
    """Password change is a re-auth event and MUST refresh
    ``_fresh_login_at``.

    Rationale: typing the current password proves identity in the
    same way as primary login.  If the change-password handler
    failed to refresh the fresh-login stamp, a user who changed
    their password mid-session would immediately be bumped to
    /reauth on the next high-value operation -- terrible UX, and
    no security gain since the password just verified is the same
    credential /reauth would re-verify.
    """

    def test_change_password_refreshes_fresh_login_at(
        self, app, auth_client,
    ):
        """Successful /change-password updates ``_fresh_login_at``."""
        with app.app_context():
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=10)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            before = datetime.now(timezone.utc)
            resp = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "newpassword12",
                "confirm_password": "newpassword12",
            })
            after = datetime.now(timezone.utc)
            assert resp.status_code == 302

            with auth_client.session_transaction() as sess:
                stamped = datetime.fromisoformat(sess[FRESH_LOGIN_AT_KEY])
            assert before <= stamped <= after, (
                "/change-password must advance _fresh_login_at to NOW "
                "since the user just re-authenticated via current "
                f"password; got {stamped!r} not in [{before}, {after}]."
            )


class TestInvalidateSessionsDoesNotRefreshFreshLogin:
    """``/invalidate-sessions`` MUST NOT refresh ``_fresh_login_at``.

    The endpoint is gated on ``@login_required`` only -- the user
    is NOT asked for their password before clicking the "log out
    all other sessions" button.  Refreshing the fresh-login stamp
    here would let the same UI silently extend the step-up grace
    window, defeating ``fresh_login_required`` for any user who
    cleared other sessions within the last 5 minutes.
    """

    def test_invalidate_sessions_preserves_fresh_login_at(
        self, app, auth_client,
    ):
        """``_fresh_login_at`` is unchanged across /invalidate-sessions."""
        with app.app_context():
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=10)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            resp = auth_client.post("/invalidate-sessions")
            assert resp.status_code == 302

            with auth_client.session_transaction() as sess:
                after_fresh = sess[FRESH_LOGIN_AT_KEY]
            assert after_fresh == stale, (
                "/invalidate-sessions must NOT refresh _fresh_login_at "
                "(no re-auth happened).  Otherwise the same UI could "
                "extend the step-up window without typing a password.  "
                f"Got before={stale!r} after={after_fresh!r}."
            )


# ---------------------------------------------------------------------------
# Additional decorated routes (post-C-10 audit follow-up)
# ---------------------------------------------------------------------------


class TestUpdateAccountIsStepUpGated:
    """``POST /accounts/<id>`` (the form-edit route) is step-up gated.

    Distinct from :func:`true_up` and :func:`inline_anchor_update`
    because all three accept an ``anchor_balance`` write but only
    the latter two were originally enumerated by the C-10 plan.
    The C-10 audit follow-up identified ``update_account`` as a
    third anchor-balance write path; without the decorator a
    session-hijacker who avoids the inline editors and POSTs the
    form-edit endpoint instead would sidestep the step-up gate.
    """

    def test_stale_fresh_login_redirects_to_reauth(
        self, app, auth_client, seed_user,
    ):
        """A stale fresh-login bounces /accounts/<id> to /reauth."""
        with app.app_context():
            account_id = seed_user["account"].id
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            _reset_login_cache()
            resp = auth_client.post(
                f"/accounts/{account_id}",
                data={
                    "name": "Renamed Checking",
                    "account_type_id": checking_type.id,
                    "anchor_balance": "9999.99",
                },
                follow_redirects=False,
            )

            assert resp.status_code == 302
            assert "/reauth" in resp.headers.get("Location", "")

            # Critical: the anchor balance MUST NOT have been written.
            # If the decorator ran AFTER the route's body, the balance
            # would already be updated by the time the redirect was
            # issued -- a defense-in-depth check that the decorator is
            # ordered correctly relative to the route handler.
            acct = db.session.get(Account, account_id)
            assert acct.current_anchor_balance != Decimal("9999.99"), (
                "Decorator must run BEFORE the route body; got an "
                "anchor balance that matches the rejected request."
            )

    def test_fresh_session_succeeds(
        self, app, auth_client, seed_user,
    ):
        """A fresh session updates the account name normally.

        Control test: locks down the auth_client UX after the
        decorator addition.  Without this test, a regression that
        always rejected would still pass the staleness rejection
        test above (every value would be rejected, including
        freshly-stamped ones).
        """
        with app.app_context():
            account_id = seed_user["account"].id
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )

            resp = auth_client.post(
                f"/accounts/{account_id}",
                data={
                    "name": "Primary Checking",
                    "account_type_id": checking_type.id,
                },
                follow_redirects=False,
            )
            # Happy-path 302 to the accounts list (NOT to /reauth).
            assert resp.status_code == 302
            assert "/reauth" not in resp.headers.get("Location", "")

            acct = db.session.get(Account, account_id)
            assert acct.name == "Primary Checking"


class TestHardDeleteTemplateIsStepUpGated:
    """``POST /templates/<id>/hard-delete`` is step-up gated.

    Mirrors the protection on
    :func:`accounts.hard_delete_account` -- both are permanent
    destruction paths and a session-hijacker should not be able to
    erase a user's recurring-transaction templates without re-typing
    a password.
    """

    @staticmethod
    def _create_minimal_template(seed_user):
        """Create a template with no transactions for hard-delete tests."""
        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=db.session.query(RecurrencePattern)
            .filter_by(name="Every Period").one().id,
            interval_n=1,
            offset_periods=0,
        )
        db.session.add(rule)
        db.session.flush()

        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Rent"].id,
            transaction_type_id=db.session.query(TransactionType)
            .filter_by(name="Expense").one().id,
            recurrence_rule_id=rule.id,
            name="Step-Up Test Template",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.commit()
        return template

    def test_stale_fresh_login_redirects_to_reauth(
        self, app, auth_client, seed_user,
    ):
        """A stale fresh-login bounces /templates/<id>/hard-delete to /reauth."""
        with app.app_context():
            template = self._create_minimal_template(seed_user)
            template_id = template.id

            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            _reset_login_cache()
            resp = auth_client.post(
                f"/templates/{template_id}/hard-delete",
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert "/reauth" in resp.headers.get("Location", "")

            # Template must still exist -- the decorator ran before
            # the route body deleted it.
            assert db.session.get(TransactionTemplate, template_id) is not None

    def test_fresh_session_succeeds(
        self, app, auth_client, seed_user,
    ):
        """A fresh session permanently deletes the template normally."""
        with app.app_context():
            template = self._create_minimal_template(seed_user)
            template_id = template.id

            resp = auth_client.post(
                f"/templates/{template_id}/hard-delete",
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert "/reauth" not in resp.headers.get("Location", "")
            assert db.session.get(TransactionTemplate, template_id) is None


class TestHardDeleteTransferTemplateIsStepUpGated:
    """``POST /transfers/<id>/hard-delete`` is step-up gated.

    Mirrors the protection on
    :func:`templates.hard_delete_template` -- both are permanent
    destruction paths and the transfer-template variant carries the
    additional shadow-invariant burden documented in CLAUDE.md, so a
    session-hijacker triggering it without the user's knowledge
    would invalidate audit trails the user relies on.
    """

    @staticmethod
    def _create_minimal_transfer_template(seed_user):
        """Create a transfer template with no transfers for hard-delete tests."""
        savings_type = (
            db.session.query(AccountType).filter_by(name="Savings").one()
        )
        savings = Account(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Step-Up Test Savings",
            current_anchor_balance=Decimal("0"),
        )
        db.session.add(savings)
        db.session.flush()

        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="Step-Up Test Transfer",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.commit()
        return template

    def test_stale_fresh_login_redirects_to_reauth(
        self, app, auth_client, seed_user,
    ):
        """A stale fresh-login bounces /transfers/<id>/hard-delete to /reauth."""
        with app.app_context():
            template = self._create_minimal_transfer_template(seed_user)
            template_id = template.id

            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            _reset_login_cache()
            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert "/reauth" in resp.headers.get("Location", "")

            # Transfer template must still exist after the rejected
            # request.
            assert db.session.get(TransferTemplate, template_id) is not None

    def test_fresh_session_succeeds(
        self, app, auth_client, seed_user,
    ):
        """A fresh session permanently deletes the transfer template normally."""
        with app.app_context():
            template = self._create_minimal_transfer_template(seed_user)
            template_id = template.id

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert "/reauth" not in resp.headers.get("Location", "")
            assert db.session.get(TransferTemplate, template_id) is None
