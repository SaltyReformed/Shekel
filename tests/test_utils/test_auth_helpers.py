"""
Shekel Budget App - Auth Helpers Tests

Tests for the reusable ownership verification helpers in
app/utils/auth_helpers.py.  Uses test_request_context + login_user
to set up a real Flask-Login request context for each test.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from flask import Blueprint, Flask
from flask_login import login_user

from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType, Status, TransactionType
from app.models.transaction import Transaction
from app.utils.auth_helpers import (
    fresh_login_required,
    get_or_404,
    get_owned_via_parent,
)
from app.utils.session_helpers import FRESH_LOGIN_AT_KEY


class TestGetOr404:
    """Tests for the get_or_404 ownership helper (Pattern A)."""

    def test_returns_owned_record(self, app, db, seed_user):
        """Happy path: returns the record when it belongs to the current user."""
        with app.test_request_context():
            login_user(seed_user["user"])
            result = get_or_404(Account, seed_user["account"].id)
            assert result is not None
            assert result.id == seed_user["account"].id

    def test_returns_none_for_nonexistent_pk(self, app, db, seed_user):
        """Returns None when no record exists at the given PK."""
        with app.test_request_context():
            login_user(seed_user["user"])
            result = get_or_404(Account, 999999)
            assert result is None

    def test_returns_none_for_other_users_record(self, app, db, seed_user, second_user):
        """Core security test: user A cannot load user B's record."""
        with app.test_request_context():
            login_user(seed_user["user"])
            # second_user's account belongs to a different user.
            result = get_or_404(Account, second_user["account"].id)
            assert result is None

    def test_returns_none_for_pk_zero(self, app, db, seed_user):
        """PK=0 does not exist in PostgreSQL autoincrement; must not crash."""
        with app.test_request_context():
            login_user(seed_user["user"])
            result = get_or_404(Account, 0)
            assert result is None

    def test_custom_user_id_field_nonexistent(self, app, db, seed_user):
        """Passing a nonexistent field name returns None (safe fallback)."""
        with app.test_request_context():
            login_user(seed_user["user"])
            # Account has user_id, but "nonexistent" does not exist --
            # getattr returns None which != current_user.id.
            result = get_or_404(Account, seed_user["account"].id,
                                user_id_field="nonexistent")
            assert result is None


class TestGetOwnedViaParent:
    """Tests for the get_owned_via_parent ownership helper (Pattern B)."""

    def _create_transaction(self, seed_user, period):
        """Helper: create a projected expense in the given period."""
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        txn = Transaction(
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=projected.id,
            name="Test Expense",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("50.00"),
        )
        db.session.add(txn)
        db.session.flush()
        return txn

    def test_returns_owned_child_record(self, app, db, seed_user, seed_periods):
        """Happy path: returns the child when its parent belongs to the current user."""
        with app.test_request_context():
            login_user(seed_user["user"])
            txn = self._create_transaction(seed_user, seed_periods[0])
            result = get_owned_via_parent(Transaction, txn.id, "pay_period")
            assert result is not None
            assert result.id == txn.id

    def test_returns_none_for_nonexistent_pk(self, app, db, seed_user, seed_periods):
        """Returns None when no child record exists at the given PK."""
        with app.test_request_context():
            login_user(seed_user["user"])
            result = get_owned_via_parent(Transaction, 999999, "pay_period")
            assert result is None

    def test_returns_none_for_other_users_child(self, app, db, seed_user, second_user, seed_periods):
        """Core security test: user A cannot load user B's child record."""
        with app.test_request_context():
            login_user(seed_user["user"])

            # Create a pay period for the second user.
            from app.services import pay_period_service
            periods2 = pay_period_service.generate_pay_periods(
                user_id=second_user["user"].id,
                start_date=date(2026, 3, 1),
                num_periods=2,
                cadence_days=14,
            )
            db.session.flush()

            # Create a transaction owned by the second user.
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            txn2 = Transaction(
                pay_period_id=periods2[0].id,
                scenario_id=second_user["scenario"].id,
                account_id=second_user["account"].id,
                status_id=projected.id,
                name="Other User Expense",
                category_id=second_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("99.00"),
            )
            db.session.add(txn2)
            db.session.flush()

            result = get_owned_via_parent(Transaction, txn2.id, "pay_period")
            assert result is None

    def test_returns_none_when_parent_attr_missing(self, app, db, seed_user, seed_periods):
        """Bad parent_attr name returns None instead of crashing."""
        with app.test_request_context():
            login_user(seed_user["user"])
            txn = self._create_transaction(seed_user, seed_periods[0])
            result = get_owned_via_parent(
                Transaction, txn.id, "nonexistent_relationship",
            )
            assert result is None

    def test_returns_none_when_parent_user_id_attr_missing(self, app, db, seed_user, seed_periods):
        """Bad parent_user_id_attr name returns None instead of crashing."""
        with app.test_request_context():
            login_user(seed_user["user"])
            txn = self._create_transaction(seed_user, seed_periods[0])
            result = get_owned_via_parent(
                Transaction, txn.id, "pay_period",
                parent_user_id_attr="nonexistent",
            )
            assert result is None


class TestFreshLoginRequiredMisuse:
    """``fresh_login_required`` MUST be called with parentheses.

    Without the runtime guard, a developer who writes
    ``@fresh_login_required`` (no parens) would silently bind the
    view function as ``max_age_minutes`` -- the wrapper would then
    call ``timedelta(minutes=<view_function>)`` on the first request,
    surfacing as a confusing TypeError far from the actual mistake.
    The guard catches the misuse at decoration time so the error
    points directly at the bad source line.
    """

    def test_no_parens_misuse_raises_typeerror(self):
        """Calling without parens (passing a function) raises TypeError."""
        def fake_view():
            """Fake view function used to trigger the misuse."""
            return "fake-view-body"

        with pytest.raises(TypeError, match="parentheses"):
            fresh_login_required(fake_view)

    def test_parens_with_no_args_works(self):
        """``@fresh_login_required()`` returns a usable decorator.

        Smoke test that the documented call form -- empty parens --
        produces a decorator that wraps a view without raising.
        """
        decorator = fresh_login_required()

        @decorator
        def view():
            """Fake view function."""
            return "ok"

        # Just verify the wrap succeeded and the wrapper is callable;
        # behavior is exercised in the integration tests in
        # ``tests/test_adversarial/test_step_up.py``.
        assert callable(view)

    def test_explicit_max_age_works(self):
        """``@fresh_login_required(max_age_minutes=N)`` is accepted."""
        decorator = fresh_login_required(max_age_minutes=10)

        @decorator
        def view():
            """Fake view function."""
            return "ok"

        assert callable(view)


class TestFreshLoginRequiredDecorator:
    """The decorator allows or redirects based on ``_fresh_login_at``.

    Tests use a tiny throwaway Flask app rather than the project's
    full app so the decorator is exercised in isolation -- a
    regression in any other middleware (CSRF, login_required,
    rate-limiting) cannot mask a regression in the decorator
    itself.
    """

    def _make_test_app(self):
        """Build a minimal Flask app with one decorated test route.

        The route is registered without ``login_required`` so the
        test can drive ``_fresh_login_at`` manipulation directly via
        ``session_transaction`` without first having to log in via
        a full ``/login`` round-trip.
        """
        app = Flask(__name__)
        app.config["SECRET_KEY"] = "test-key-for-fresh-login-decorator-tests"
        app.config["FRESH_LOGIN_MAX_AGE_MINUTES"] = 5

        # Register a fake auth.reauth endpoint so url_for inside the
        # decorator can resolve it.  The decorator does not actually
        # call the view, only generates a URL pointing at it.
        bp = Blueprint("auth", __name__)

        @bp.route("/reauth")
        def reauth():
            """Fake reauth view; only here so url_for resolves."""
            return "fake-reauth"

        app.register_blueprint(bp)

        @app.route("/decorated", methods=["POST"])
        @fresh_login_required()
        def decorated():
            """Decorated test endpoint; returns marker on success."""
            return "decorated-body"

        @app.route("/decorated-strict", methods=["POST"])
        @fresh_login_required(max_age_minutes=1)
        def decorated_strict():
            """Decorated test endpoint with explicit 1-min window."""
            return "strict-body"

        return app

    def test_fresh_session_passes(self):
        """A fresh ``_fresh_login_at`` lets the request through."""
        app = self._make_test_app()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[FRESH_LOGIN_AT_KEY] = (
                datetime.now(timezone.utc).isoformat()
            )

        resp = client.post("/decorated")
        assert resp.status_code == 200
        assert resp.data == b"decorated-body"

    def test_missing_fresh_login_redirects(self):
        """A missing ``_fresh_login_at`` redirects to /reauth."""
        app = self._make_test_app()
        client = app.test_client()

        resp = client.post("/decorated")
        assert resp.status_code == 302
        assert "/reauth" in resp.headers.get("Location", "")

    def test_stale_fresh_login_redirects(self):
        """A stale ``_fresh_login_at`` redirects to /reauth."""
        app = self._make_test_app()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[FRESH_LOGIN_AT_KEY] = (
                datetime.now(timezone.utc) - timedelta(minutes=10)
            ).isoformat()

        resp = client.post("/decorated")
        assert resp.status_code == 302
        assert "/reauth" in resp.headers.get("Location", "")

    def test_explicit_max_age_overrides_config(self):
        """A view-level ``max_age_minutes`` overrides app config.

        ``decorated_strict`` declares ``max_age_minutes=1``.  A
        2-minute-old fresh-login passes the config-default 5-minute
        window but must FAIL the explicit 1-minute window.
        """
        app = self._make_test_app()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[FRESH_LOGIN_AT_KEY] = (
                datetime.now(timezone.utc) - timedelta(minutes=2)
            ).isoformat()

        # Default-window endpoint: 2 min < 5 min, passes.
        resp = client.post("/decorated")
        assert resp.status_code == 200

        # Strict endpoint: 2 min > 1 min, redirects.
        resp = client.post("/decorated-strict")
        assert resp.status_code == 302

    def test_htmx_request_returns_204_with_hx_redirect(self):
        """HTMX requests get ``204 + HX-Redirect``, not 302.

        See the integration test in ``test_step_up.py`` for the
        rationale: a 302 would render /reauth's HTML into whatever
        fragment slot the original request targeted.
        """
        app = self._make_test_app()
        client = app.test_client()

        resp = client.post("/decorated", headers={"HX-Request": "true"})
        assert resp.status_code == 204
        assert "HX-Redirect" in resp.headers
        assert "/reauth" in resp.headers["HX-Redirect"]
        assert resp.data == b""

    def test_malformed_fresh_login_treated_as_stale(self):
        """A non-ISO-8601 ``_fresh_login_at`` is treated as stale.

        Fail-closed for tampered cookies -- matches the policy of
        ``_idle_session_is_fresh`` and ``_mfa_pending_is_fresh``.
        Without this, a tampered cookie would 500 the request via
        a ``ValueError`` from ``fromisoformat``.
        """
        app = self._make_test_app()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[FRESH_LOGIN_AT_KEY] = "not-an-iso-timestamp"

        resp = client.post("/decorated")
        assert resp.status_code == 302
        assert "/reauth" in resp.headers.get("Location", "")

    def test_naive_fresh_login_treated_as_stale(self):
        """A timezone-naive ``_fresh_login_at`` is treated as stale.

        Naive datetimes raise ``TypeError`` on the timezone-aware
        subtraction.  Reject explicitly so the failure mode is
        consistent with the malformed case.
        """
        app = self._make_test_app()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[FRESH_LOGIN_AT_KEY] = (
                datetime.now().replace(tzinfo=None).isoformat()
            )

        resp = client.post("/decorated")
        assert resp.status_code == 302

    def test_future_dated_fresh_login_treated_as_fresh(self):
        """A future-dated ``_fresh_login_at`` is treated as fresh.

        Mirrors ``_idle_session_is_fresh``: a clock jump (NTP
        correction, manual adjustment, VM resume) must not silently
        force every active session through /reauth.  An attacker who
        forged a future-dated value would already need ``SECRET_KEY``,
        at which point the future-date check adds no defensive value
        beyond the cookie signature itself.

        This is the INVERSE of ``_mfa_pending_is_fresh`` (commit
        C-08), which DOES reject future-dated -- that gate is
        single-use and the strict-rejection cost is one extra
        password retry, vs. a 30-minute window of being bumped
        through /reauth on every request here.
        """
        app = self._make_test_app()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[FRESH_LOGIN_AT_KEY] = (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat()

        resp = client.post("/decorated")
        assert resp.status_code == 200, (
            "Future-dated _fresh_login_at must be accepted; "
            "rejecting it would log every user out after a clock "
            "jump."
        )

    def test_redirect_includes_next_param(self):
        """The /reauth URL carries the original request URL as ``next``."""
        app = self._make_test_app()
        client = app.test_client()

        resp = client.post("/decorated")
        location = resp.headers.get("Location", "")
        assert "next=" in location, (
            "Decorator must include the original URL as ``next`` so "
            "/reauth can return the user there after success.  "
            f"Got Location={location!r}."
        )
