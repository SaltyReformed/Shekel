"""
Shekel Budget App -- Companion Route Guard Tests

Comprehensive tests for Commit 9: verifies that @require_owner
blocks companion access on all owner-only routes, that companion
login routing works correctly, that mark_done allows companion
access on visible transactions, and that the nav bar renders
role-appropriate content.
"""

import pytest
from decimal import Decimal

from app import ref_cache
from app.enums import RoleEnum, StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.ref import TransactionType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password


# ── Companion blocked from all guarded routes ────────────────────────


class TestCompanionGuardedRoutes:
    """Verify that companions receive 404 on every owner-only route.

    Tests all 17 guarded blueprints: grid, templates, settings,
    dashboard, accounts, analytics, categories, debt_strategy,
    investment, loan, obligations, pay_periods, retirement,
    salary, savings, transfers, charts.
    """

    @pytest.mark.parametrize("url", [
        "/grid",
        "/templates",
        "/settings",
        "/dashboard",
        "/accounts",
        "/analytics",
        "/categories",
        "/debt-strategy",
        "/accounts/99999/investment",
        "/accounts/99999/loan",
        "/obligations",
        "/pay-periods/generate",
        "/retirement",
        "/salary",
        "/savings",
        "/transfers",
        "/charts",
    ])
    def test_companion_blocked_from_guarded_route(
        self, companion_client, url,
    ):
        """Companion gets 404 on every owner-only route.

        The @require_owner decorator fires before the route body,
        so the companion is blocked regardless of URL parameters or
        data availability.  Returns 404 (not 403) per the security
        response rule.
        """
        resp = companion_client.get(url)
        assert resp.status_code == 404, (
            f"Expected 404 for companion on {url}, got {resp.status_code}"
        )


# ── Owner access regression ──────────────────────────────────────────


class TestOwnerAccessRegression:
    """Verify owner users still pass @require_owner on guarded routes.

    Tests a representative subset of guarded routes with seed_user
    data to confirm @require_owner does not block legitimate owners.
    """

    def test_owner_can_access_settings(self, auth_client):
        """Owner can access /settings -- not blocked by @require_owner."""
        resp = auth_client.get("/settings")
        assert resp.status_code == 200

    def test_owner_can_access_templates(self, auth_client):
        """Owner can access /templates -- empty list is fine."""
        resp = auth_client.get("/templates")
        assert resp.status_code == 200

    def test_owner_can_access_retirement(self, auth_client):
        """Owner can access /retirement planning page."""
        resp = auth_client.get("/retirement")
        assert resp.status_code == 200

    def test_owner_can_access_grid(self, auth_client, seed_periods):
        """Owner can access /grid with pay periods."""
        resp = auth_client.get("/grid")
        assert resp.status_code == 200

    def test_owner_can_access_analytics(self, auth_client):
        """Owner can access /analytics -- verifies lazy tab loading."""
        resp = auth_client.get("/analytics")
        assert resp.status_code == 200

    def test_owner_can_access_salary(self, auth_client):
        """Owner can access /salary -- empty profile list is fine."""
        resp = auth_client.get("/salary")
        assert resp.status_code == 200

    def test_owner_can_access_savings(self, auth_client):
        """Owner can access /savings dashboard."""
        resp = auth_client.get("/savings")
        assert resp.status_code == 200

    def test_owner_can_access_transfers(self, auth_client):
        """Owner can access /transfers -- empty list is fine."""
        resp = auth_client.get("/transfers")
        assert resp.status_code == 200

    def test_owner_can_access_dashboard(self, auth_client, seed_periods):
        """Owner can access /dashboard with periods."""
        resp = auth_client.get("/dashboard")
        assert resp.status_code == 200


# ── Companion login routing ──────────────────────────────────────────


class TestCompanionLoginRouting:
    """Verify login redirects companions to /companion/ and owners to /dashboard."""

    def test_companion_login_redirects_to_companion_index(
        self, app, db, seed_companion,
    ):
        """POST /login as companion redirects to /companion/ (not /dashboard).

        Verifies the login route checks user.role_id after login_user()
        and redirects companions to their dedicated view.
        """
        client = app.test_client()
        resp = client.post("/login", data={
            "email": "companion@shekel.local",
            "password": "companionpass",
        })
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/companion/")

    def test_owner_login_redirects_to_dashboard(self, app, db, seed_user):
        """POST /login as owner redirects to /dashboard.

        Verifies the owner login path is unchanged by companion routing.
        """
        client = app.test_client()
        resp = client.post("/login", data={
            "email": "test@shekel.local",
            "password": "testpass",
        })
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "/dashboard" in location or location.endswith("/")

    def test_companion_login_ignores_next_param(
        self, app, db, seed_companion,
    ):
        """Companion login ignores ?next=/grid and goes to /companion/.

        Prevents companions from following next URLs to guarded routes,
        which would result in 404 after @require_owner.
        """
        client = app.test_client()
        resp = client.post("/login?next=/grid", data={
            "email": "companion@shekel.local",
            "password": "companionpass",
        })
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/companion/")
        assert "/grid" not in resp.headers["Location"]

    def test_authenticated_companion_visiting_login_redirects(
        self, companion_client,
    ):
        """Already-authenticated companion visiting /login goes to /companion/.

        Tests the "already authenticated" check at the top of the login route.
        """
        resp = companion_client.get("/login")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/companion/")

    def test_authenticated_owner_visiting_login_redirects(self, auth_client):
        """Already-authenticated owner visiting /login goes to /dashboard.

        Verifies the owner's "already authenticated" redirect is unchanged.
        """
        resp = auth_client.get("/login")
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "/dashboard" in location or location.endswith("/")

    def test_authenticated_companion_visiting_register_redirects(
        self, companion_client,
    ):
        """Already-authenticated companion visiting /register goes to /companion/.

        Prevents companions from being redirected to /dashboard from
        the registration page's authenticated-check.
        """
        resp = companion_client.get("/register")
        # Could be 302 (redirect) or 404 (registration disabled).
        if resp.status_code == 302:
            assert resp.headers["Location"].endswith("/companion/")


# ── Companion-accessible routes ──────────────────────────────────────


class TestCompanionAccessibleRoutes:
    """Verify routes that companions CAN access work correctly."""

    def test_companion_can_access_companion_page(self, companion_client):
        """Companion can access /companion/ and gets 200.

        The companion stub page should render without errors.
        """
        resp = companion_client.get("/companion/")
        assert resp.status_code == 200

    def test_owner_redirected_from_companion_page(self, auth_client):
        """Owner accessing /companion/ is redirected to /grid.

        The companion index route checks role and redirects owners
        to the grid (their normal landing page).
        """
        resp = auth_client.get("/companion/")
        assert resp.status_code == 302
        assert "/grid" in resp.headers["Location"]

    def test_companion_can_access_login_page(self, app, db, seed_companion):
        """Companion can access GET /login (unauthenticated).

        Authentication routes must be accessible by all users.
        """
        client = app.test_client()
        resp = client.get("/login")
        assert resp.status_code == 200


# ── Decorator order ──────────────────────────────────────────────────


class TestDecoratorOrder:
    """Verify @login_required fires before @require_owner."""

    def test_unauthenticated_user_gets_login_redirect(self, client):
        """Unauthenticated user hitting a guarded route gets login redirect.

        This proves @login_required runs before @require_owner.
        If the order were reversed, the unauthenticated user would
        get 404 (from require_owner failing to find current_user.role_id)
        instead of a redirect to /login.
        """
        resp = client.get("/grid")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_unauthenticated_user_on_settings(self, client):
        """Unauthenticated user hitting /settings gets login redirect."""
        resp = client.get("/settings")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_unauthenticated_user_on_dashboard(self, client):
        """Unauthenticated user hitting /dashboard gets login redirect."""
        resp = client.get("/dashboard")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


# ── Mark-done companion access ───────────────────────────────────────


def _create_companion_test_transaction(
    db, seed_user, seed_periods, companion_visible, template_name="Test Item",
):
    """Create a template + transaction for companion mark_done testing.

    Args:
        db: The database session fixture.
        seed_user: The seed_user fixture dict.
        seed_periods: The seed_periods fixture (list of PayPeriod).
        companion_visible: Whether the template is companion-visible.
        template_name: Name for the template and transaction.

    Returns:
        The created Transaction object.
    """
    expense_type = (
        db.session.query(TransactionType)
        .filter_by(name="Expense").one()
    )
    category = list(seed_user["categories"].values())[0]

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        name=template_name,
        default_amount=Decimal("500.00"),
        transaction_type_id=expense_type.id,
        account_id=seed_user["account"].id,
        category_id=category.id,
        companion_visible=companion_visible,
        is_envelope=False,
    )
    db.session.add(template)
    db.session.flush()

    txn = Transaction(
        name=template_name,
        estimated_amount=Decimal("500.00"),
        transaction_type_id=expense_type.id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        pay_period_id=seed_periods[0].id,
        account_id=seed_user["account"].id,
        category_id=category.id,
        scenario_id=seed_user["scenario"].id,
        template_id=template.id,
    )
    db.session.add(txn)
    db.session.commit()
    return txn


def _login_companion(app):
    """Create an authenticated companion test client.

    Must be called within an app context, after seed_companion
    has created the companion user.

    Args:
        app: The Flask application.

    Returns:
        An authenticated FlaskClient for the companion user.
    """
    comp = app.test_client()
    resp = comp.post("/login", data={
        "email": "companion@shekel.local",
        "password": "companionpass",
    })
    assert resp.status_code == 302, (
        f"Companion login failed with status {resp.status_code}"
    )
    return comp


class TestMarkDoneCompanionAccess:
    """Test companion access to the mark_done route."""

    def test_companion_marks_visible_transaction_done(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """Companion can mark a companion-visible transaction as Paid.

        Verifies _get_accessible_transaction_for_status allows
        companion access when template.companion_visible is True.
        Arithmetic: status changes from Projected to Paid.
        """
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods,
            companion_visible=True,
            template_name="Groceries",
        )

        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 200

        db.session.refresh(txn)
        done_id = ref_cache.status_id(StatusEnum.DONE)
        assert txn.status_id == done_id

    def test_companion_blocked_from_non_visible_transaction(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """Companion gets 404 for mark_done on non-visible transactions.

        Verifies _get_accessible_transaction_for_status rejects
        companion access when template.companion_visible is False.
        """
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods,
            companion_visible=False,
            template_name="Mortgage",
        )

        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 404

    def test_companion_blocked_from_other_owner_transaction(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """Companion gets 404 for transactions belonging to a different owner.

        Creates a second owner with a companion-visible transaction.
        The companion (linked to seed_user) cannot mark it as done
        because the pay_period belongs to a different owner.
        """
        from app.models.ref import AccountType  # pylint: disable=import-outside-toplevel

        # Create a second owner with minimal data.
        second_user = User(
            email="second@shekel.local",
            password_hash=hash_password("secondpass123"),
            display_name="Second Owner",
        )
        db.session.add(second_user)
        db.session.flush()

        settings = UserSettings(user_id=second_user.id)
        db.session.add(settings)

        checking_type = (
            db.session.query(AccountType).filter_by(name="Checking").one()
        )
        account = Account(
            user_id=second_user.id,
            account_type_id=checking_type.id,
            name="Checking",
            current_anchor_balance=Decimal("1000.00"),
        )
        db.session.add(account)

        scenario = Scenario(
            user_id=second_user.id,
            name="Baseline",
            is_baseline=True,
        )
        db.session.add(scenario)
        db.session.flush()

        category = Category(
            user_id=second_user.id,
            group_name="Home",
            item_name="Rent",
        )
        db.session.add(category)
        db.session.flush()

        # Create a period for the second owner.
        from datetime import date  # pylint: disable=import-outside-toplevel
        period = PayPeriod(
            user_id=second_user.id,
            start_date=date(2026, 1, 2),
            end_date=date(2026, 1, 15),
            period_index=0,
        )
        db.session.add(period)
        db.session.flush()

        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense").one()
        )

        template = TransactionTemplate(
            user_id=second_user.id,
            name="Other Groceries",
            default_amount=Decimal("400.00"),
            transaction_type_id=expense_type.id,
            account_id=account.id,
            category_id=category.id,
            companion_visible=True,
            is_envelope=False,
        )
        db.session.add(template)
        db.session.flush()

        txn = Transaction(
            name="Other Groceries",
            estimated_amount=Decimal("400.00"),
            transaction_type_id=expense_type.id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            pay_period_id=period.id,
            account_id=account.id,
            category_id=category.id,
            scenario_id=scenario.id,
            template_id=template.id,
        )
        db.session.add(txn)
        db.session.commit()

        # Companion (linked to seed_user) tries to mark the second owner's txn.
        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 404

    def test_owner_mark_done_regression(
        self, auth_client, db, seed_user, seed_periods,
    ):
        """Owner can still mark their own transactions as Paid.

        Regression test ensuring the companion-aware access function
        does not break the existing owner mark_done flow.
        """
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods,
            companion_visible=True,
            template_name="Owner Groceries",
        )

        resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 200

        db.session.refresh(txn)
        done_id = ref_cache.status_id(StatusEnum.DONE)
        assert txn.status_id == done_id

    def test_companion_blocked_from_templateless_transaction(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """Companion gets 404 for ad-hoc transactions (no template).

        Transactions without a template (template_id is None) are
        inaccessible to companions because
        _get_accessible_transaction_for_status requires a template
        with companion_visible=True.
        """
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense").one()
        )
        category = list(seed_user["categories"].values())[0]

        txn = Transaction(
            name="Ad-hoc expense",
            estimated_amount=Decimal("100.00"),
            transaction_type_id=expense_type.id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            pay_period_id=seed_periods[0].id,
            account_id=seed_user["account"].id,
            category_id=category.id,
            scenario_id=seed_user["scenario"].id,
        )
        db.session.add(txn)
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 404


# ── Companion-guarded transaction routes ─────────────────────────────


class TestCompanionBlockedTransactionRoutes:
    """Verify companion is blocked from owner-only transaction routes.

    All transaction routes except mark_done have @require_owner.
    """

    def test_companion_blocked_from_update_transaction(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """Companion gets 404 on PATCH /transactions/<id>."""
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods,
            companion_visible=True,
        )
        comp = _login_companion(app)
        resp = comp.patch(
            f"/transactions/{txn.id}",
            data={"estimated_amount": "999.00"},
        )
        assert resp.status_code == 404

    def test_companion_blocked_from_cancel(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """Companion gets 404 on POST /transactions/<id>/cancel."""
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods,
            companion_visible=True,
        )
        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/cancel")
        assert resp.status_code == 404

    def test_companion_blocked_from_mark_credit(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """Companion gets 404 on POST /transactions/<id>/mark-credit."""
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods,
            companion_visible=True,
        )
        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-credit")
        assert resp.status_code == 404

    def test_companion_blocked_from_delete(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """Companion gets 404 on DELETE /transactions/<id>."""
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods,
            companion_visible=True,
        )
        comp = _login_companion(app)
        resp = comp.delete(f"/transactions/{txn.id}")
        assert resp.status_code == 404

    def test_companion_blocked_from_get_cell(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """Companion gets 404 on GET /transactions/<id>/cell."""
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods,
            companion_visible=True,
        )
        comp = _login_companion(app)
        resp = comp.get(f"/transactions/{txn.id}/cell")
        assert resp.status_code == 404

    def test_companion_blocked_from_quick_edit(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """Companion gets 404 on GET /transactions/<id>/quick-edit."""
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods,
            companion_visible=True,
        )
        comp = _login_companion(app)
        resp = comp.get(f"/transactions/{txn.id}/quick-edit")
        assert resp.status_code == 404

    def test_companion_blocked_from_full_edit(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """Companion gets 404 on GET /transactions/<id>/full-edit."""
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods,
            companion_visible=True,
        )
        comp = _login_companion(app)
        resp = comp.get(f"/transactions/{txn.id}/full-edit")
        assert resp.status_code == 404

    def test_companion_blocked_from_create_inline(
        self, app, db, seed_companion,
    ):
        """Companion gets 404 on POST /transactions/inline."""
        comp = _login_companion(app)
        resp = comp.post("/transactions/inline", data={})
        assert resp.status_code == 404

    def test_companion_blocked_from_create_transaction(
        self, app, db, seed_companion,
    ):
        """Companion gets 404 on POST /transactions."""
        comp = _login_companion(app)
        resp = comp.post("/transactions", data={})
        assert resp.status_code == 404

    def test_companion_blocked_from_carry_forward(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """Companion gets 404 on POST /pay-periods/<id>/carry-forward."""
        comp = _login_companion(app)
        resp = comp.post(f"/pay-periods/{seed_periods[0].id}/carry-forward")
        assert resp.status_code == 404


# ── Nav bar content ──────────────────────────────────────────────────


class TestNavBarContent:
    """Verify the nav bar renders role-appropriate content."""

    def test_companion_nav_shows_my_budget(self, companion_client):
        """Companion sees 'My Budget' link in nav bar.

        The companion nav should show only 'My Budget' and logout,
        not the full owner navigation.
        """
        resp = companion_client.get("/companion/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "My Budget" in html

    def test_companion_nav_hides_owner_links(self, companion_client):
        """Companion nav does NOT contain full owner navigation links.

        Verifies that Dashboard, Budget, Recurring, Salary, Settings,
        etc. are not present in the companion's rendered nav bar.
        """
        resp = companion_client.get("/companion/")
        assert resp.status_code == 200
        html = resp.data.decode()
        # These are the text labels from the owner nav links.
        assert "> Dashboard" not in html
        assert "> Budget" not in html
        assert "> Recurring" not in html
        assert "> Salary" not in html
        assert "> Settings" not in html

    def test_owner_nav_shows_full_navigation(self, auth_client):
        """Owner sees full navigation links.

        Verifies Dashboard, Budget, Recurring, etc. are present
        in the owner's rendered nav bar.
        """
        resp = auth_client.get("/settings")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Dashboard" in html
        assert "Budget" in html
        assert "Recurring" in html
        assert "Settings" in html

    def test_companion_nav_shows_logout(self, companion_client):
        """Companion can see and use the Logout button."""
        resp = companion_client.get("/companion/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Logout" in html


# ── Context processor ────────────────────────────────────────────────


class TestContextProcessor:
    """Verify COMPANION_ROLE_ID is injected into template context."""

    def test_companion_role_id_available_in_context(self, auth_client):
        """COMPANION_ROLE_ID context variable is set correctly.

        Verified indirectly: the owner's nav bar renders the full
        navigation (not companion nav), which proves the template
        compared current_user.role_id against COMPANION_ROLE_ID
        and branched correctly.
        """
        resp = auth_client.get("/settings")
        assert resp.status_code == 200
        html = resp.data.decode()
        # If COMPANION_ROLE_ID were None or missing, the template
        # conditional would fail or render companion nav for owners.
        assert "Dashboard" in html
        assert "My Budget" not in html
