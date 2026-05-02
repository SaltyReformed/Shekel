"""
Shekel Budget App -- Dashboard Route Tests

Tests for the summary dashboard page, mark-paid interaction,
HTMX section refreshes, alerts, and graceful degradation.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db as _db
from app.models.account import AccountAnchorHistory
from app.models.savings_goal import SavingsGoal
from app.models.transaction import Transaction
from app.services import pay_period_service


# ── Helpers ──────────────────────────────────────────────────────────


def _add_txn(
    db_session, seed_user, period, name, amount,
    status_enum=StatusEnum.PROJECTED, is_income=False,
    due_date=None, category_key=None, is_deleted=False,
    actual_amount=None,
):
    """Create a transaction for testing."""
    type_id = (
        ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        if is_income
        else ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    )
    cat_id = None
    if category_key and category_key in seed_user["categories"]:
        cat_id = seed_user["categories"][category_key].id

    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=ref_cache.status_id(status_enum),
        name=name,
        category_id=cat_id,
        transaction_type_id=type_id,
        estimated_amount=Decimal(str(amount)),
        actual_amount=Decimal(str(actual_amount)) if actual_amount is not None else None,
        due_date=due_date,
        is_deleted=is_deleted,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


def _add_anchor_history(db_session, account, period, balance, days_ago=0):
    """Create an anchor history entry N days in the past."""
    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    entry = AccountAnchorHistory(
        account_id=account.id,
        pay_period_id=period.id,
        anchor_balance=Decimal(str(balance)),
        created_at=created,
    )
    db_session.add(entry)
    db_session.flush()
    return entry


# ── Auth and Rendering Tests ────────────────────────────────────────


class TestDashboardAuth:
    """Tests for dashboard authentication requirements."""

    def test_dashboard_requires_auth(self, app, client):
        """GET /dashboard redirects unauthenticated users to login."""
        with app.app_context():
            resp = client.get("/dashboard")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_root_requires_auth(self, app, client):
        """GET / redirects unauthenticated users to login."""
        with app.app_context():
            resp = client.get("/")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]


class TestDashboardRendering:
    """Tests for dashboard page rendering."""

    def test_root_serves_dashboard(self, app, auth_client, seed_user, seed_periods):
        """GET / returns 200 with dashboard content."""
        with app.app_context():
            resp = auth_client.get("/")
            assert resp.status_code == 200
            assert b"Upcoming Bills" in resp.data

    def test_dashboard_url_still_works(self, app, auth_client, seed_user, seed_periods):
        """GET /dashboard returns 200 with same dashboard content."""
        with app.app_context():
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert b"Upcoming Bills" in resp.data

    def test_root_does_not_contain_grid(self, app, auth_client, seed_user, seed_periods):
        """GET / does NOT contain grid-specific content."""
        with app.app_context():
            resp = auth_client.get("/")
            assert resp.status_code == 200
            assert b"grid-table" not in resp.data

    def test_dashboard_renders(self, app, auth_client, seed_user, seed_periods):
        """GET /dashboard returns 200 with section headings."""
        with app.app_context():
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert b"Upcoming Bills" in resp.data

    def test_dashboard_all_sections_present(self, app, auth_client, seed_full_user_data):
        """Dashboard with rich data contains all 7 sections."""
        with app.app_context():
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data
            assert b"Upcoming Bills" in html
            assert b"Alerts" in html
            assert b"Balance" in html
            assert b"Next Payday" in html
            assert b"Savings Goals" in html
            assert b"Spending Comparison" in html

    def test_dashboard_has_grid_link(self, app, auth_client, seed_user, seed_periods):
        """Dashboard has an 'Open Grid' link."""
        with app.app_context():
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert b"Open Grid" in resp.data


# ── Bills Display Tests ─────────────────────────────────────────────


class TestBillsDisplay:
    """Tests for bills section rendering."""

    def test_dashboard_shows_bills(self, app, auth_client, seed_user, seed_periods, db):
        """Projected expense in current period appears in bills."""
        with app.app_context():
            cur = pay_period_service.get_current_period(seed_user["user"].id)
            if cur is None:
                cur = seed_periods[0]
            _add_txn(
                db.session, seed_user, cur,
                "Rent Payment", "1200.00",
                due_date=cur.start_date,
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert b"Rent Payment" in resp.data

    def test_dashboard_hides_paid_bills(self, app, auth_client, seed_user, seed_periods, db):
        """Settled expense NOT in upcoming bills list.

        The bill must be placed in the CURRENT period (the period the
        dashboard window includes); otherwise the bill is filtered out
        by the period window rather than the status filter, and the
        test would silently pass without actually exercising the
        Paid-status exclusion.  Falling back to seed_periods[0] when
        today is outside the seeded range mirrors the production
        graceful-degradation path.
        """
        with app.app_context():
            cur = pay_period_service.get_current_period(seed_user["user"].id)
            if cur is None:
                cur = seed_periods[0]
            _add_txn(
                db.session, seed_user, cur,
                "Already Paid", "500.00",
                status_enum=StatusEnum.DONE,
                actual_amount="500.00",
                due_date=cur.start_date,
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            # The paid bill should not be in the upcoming bills section.
            # It might appear elsewhere but not as an actionable bill.
            html = resp.data.decode()
            # Check the bills section specifically -- paid bills have
            # mark-paid buttons removed.
            assert "Already Paid" not in html or "mark-paid-btn" not in html

    def test_dashboard_bills_sorted(self, app, auth_client, seed_user, seed_periods, db):
        """Bills sorted by due_date ascending."""
        with app.app_context():
            cur = pay_period_service.get_current_period(seed_user["user"].id)
            if cur is None:
                cur = seed_periods[0]
            _add_txn(db.session, seed_user, cur,
                     "Late Bill", "100.00",
                     due_date=cur.start_date + timedelta(days=10))
            _add_txn(db.session, seed_user, cur,
                     "Early Bill", "200.00",
                     due_date=cur.start_date + timedelta(days=1))
            db.session.commit()

            resp = auth_client.get("/dashboard")
            html = resp.data.decode()
            early_pos = html.find("Early Bill")
            late_pos = html.find("Late Bill")
            assert early_pos < late_pos


# ── Mark-Paid Tests ─────────────────────────────────────────────────


class TestMarkPaid:
    """Tests for the mark-paid endpoint."""

    def test_mark_paid(self, app, auth_client, seed_user, seed_periods, db):
        """POST mark-paid changes transaction status to settled."""
        with app.app_context():
            txn = _add_txn(
                db.session, seed_user, seed_periods[0],
                "Test Bill", "500.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            resp = auth_client.post(
                f"/dashboard/mark-paid/{txn.id}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

            db.session.refresh(txn)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            assert txn.status_id == done_id

    def test_mark_paid_with_actual(self, app, auth_client, seed_user, seed_periods, db):
        """POST with actual_amount saves the actual amount."""
        with app.app_context():
            txn = _add_txn(
                db.session, seed_user, seed_periods[0],
                "Bill", "500.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            resp = auth_client.post(
                f"/dashboard/mark-paid/{txn.id}",
                data={"actual_amount": "450.00"},
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.actual_amount == Decimal("450.00")

    def test_mark_paid_returns_paid_row(self, app, auth_client, seed_user, seed_periods, db):
        """POST returns HTML with paid visual state."""
        with app.app_context():
            txn = _add_txn(
                db.session, seed_user, seed_periods[0],
                "Bill", "500.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            resp = auth_client.post(
                f"/dashboard/mark-paid/{txn.id}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"bill-row--paid" in resp.data

    def test_mark_paid_htmx_trigger(self, app, auth_client, seed_user, seed_periods, db):
        """POST sets HX-Trigger: dashboardRefresh header."""
        with app.app_context():
            txn = _add_txn(
                db.session, seed_user, seed_periods[0],
                "Bill", "500.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            resp = auth_client.post(
                f"/dashboard/mark-paid/{txn.id}",
                headers={"HX-Request": "true"},
            )
            assert "dashboardRefresh" in resp.headers.get("HX-Trigger", "")

    def test_mark_paid_sets_paid_at(self, app, auth_client, seed_user, seed_periods, db):
        """After mark-paid, txn.paid_at is not None."""
        with app.app_context():
            txn = _add_txn(
                db.session, seed_user, seed_periods[0],
                "Bill", "500.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            auth_client.post(
                f"/dashboard/mark-paid/{txn.id}",
                headers={"HX-Request": "true"},
            )
            db.session.refresh(txn)
            assert txn.paid_at is not None

    def test_mark_paid_wrong_user(self, app, auth_client, seed_second_user, db):
        """POST on another user's transaction -> 404."""
        with app.app_context():
            # Create a period for the second user so ownership check fails.
            other_periods = pay_period_service.generate_pay_periods(
                user_id=seed_second_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=2,
                cadence_days=14,
            )
            db.session.commit()

            txn = _add_txn(
                db.session, seed_second_user, other_periods[0],
                "Other Bill", "500.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            resp = auth_client.post(
                f"/dashboard/mark-paid/{txn.id}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

    def test_mark_paid_requires_auth(self, app, client, seed_user, seed_periods, db):
        """Unauthenticated POST -> 302 to login."""
        with app.app_context():
            txn = _add_txn(
                db.session, seed_user, seed_periods[0],
                "Bill", "500.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            resp = client.post(f"/dashboard/mark-paid/{txn.id}")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]


# ── HTMX Section Refresh Tests ──────────────────────────────────────


class TestSectionRefresh:
    """Tests for HTMX section partials."""

    def test_bills_section_htmx(self, app, auth_client, seed_user, seed_periods):
        """GET /dashboard/bills with HX-Request -> 200."""
        with app.app_context():
            resp = auth_client.get(
                "/dashboard/bills",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

    def test_balance_section_htmx(self, app, auth_client, seed_user, seed_periods):
        """GET /dashboard/balance with HX-Request -> 200."""
        with app.app_context():
            resp = auth_client.get(
                "/dashboard/balance",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

    def test_bills_section_no_htmx_redirects(self, app, auth_client, seed_user, seed_periods):
        """GET /dashboard/bills without HX-Request -> 302 to /dashboard."""
        with app.app_context():
            resp = auth_client.get("/dashboard/bills")
            assert resp.status_code == 302
            assert "/dashboard" in resp.headers["Location"]

    def test_balance_section_no_htmx_redirects(self, app, auth_client, seed_user, seed_periods):
        """GET /dashboard/balance without HX-Request -> 302."""
        with app.app_context():
            resp = auth_client.get("/dashboard/balance")
            assert resp.status_code == 302

    def test_bills_section_requires_auth(self, app, client):
        """GET /dashboard/bills unauthenticated -> 302."""
        with app.app_context():
            resp = client.get("/dashboard/bills")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]


# ── Alert Tests ─────────────────────────────────────────────────────


class TestAlerts:
    """Tests for dashboard alerts."""

    def test_dashboard_stale_anchor_alert(self, app, auth_client, seed_user, seed_periods, db):
        """Stale anchor (>14 days) shows alert on dashboard."""
        with app.app_context():
            _add_anchor_history(
                db.session, seed_user["account"],
                seed_periods[0], "1000.00", days_ago=20,
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert b"updated" in resp.data or b"days" in resp.data

    def test_dashboard_no_alerts(self, app, auth_client, seed_user, seed_periods, db):
        """Fresh anchor -> no alert indicators."""
        with app.app_context():
            _add_anchor_history(
                db.session, seed_user["account"],
                seed_periods[0], "5000.00", days_ago=1,
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            # "All clear" message should appear when no alerts.
            assert b"All clear" in resp.data


# ── Other Section Tests ─────────────────────────────────────────────


class TestOtherSections:
    """Tests for savings goals, payday, and spending comparison sections."""

    def test_dashboard_savings_goals(self, app, auth_client, seed_full_user_data, db):
        """Active savings goal visible on dashboard."""
        with app.app_context():
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert b"Emergency Fund" in resp.data

    def test_dashboard_spending_comparison(self, app, auth_client, seed_user, seed_periods, db):
        """Spending comparison amounts visible when periods have data."""
        with app.app_context():
            cur = pay_period_service.get_current_period(seed_user["user"].id)
            if cur is None:
                cur = seed_periods[0]
            # Find prior period.
            all_p = pay_period_service.get_all_periods(seed_user["user"].id)
            prior = None
            for p in reversed(all_p):
                if p.period_index < cur.period_index:
                    prior = p
                    break

            if prior:
                _add_txn(db.session, seed_user, prior,
                         "Prior Expense", "600.00",
                         status_enum=StatusEnum.DONE,
                         actual_amount="600.00",
                         due_date=prior.start_date)
            _add_txn(db.session, seed_user, cur,
                     "Current Expense", "800.00",
                     status_enum=StatusEnum.DONE,
                     actual_amount="800.00",
                     due_date=cur.start_date)
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert b"800.00" in resp.data

    def test_dashboard_payday_info(self, app, auth_client, seed_user, seed_periods):
        """Payday section shows days until next pay when periods exist."""
        with app.app_context():
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            # Periods are in 2026, so next payday should be visible.
            assert b"payday" in resp.data.lower()


# ── Nav Bar Tests ───────────────────────────────────────────────────


class TestNavBar:
    """Tests for nav bar after route swap."""

    def test_nav_has_dashboard_link(self, app, auth_client, seed_user, seed_periods):
        """Nav bar on grid page contains 'Dashboard' link."""
        with app.app_context():
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            assert b"Dashboard" in resp.data

    def test_nav_budget_points_to_grid(self, app, auth_client, seed_user, seed_periods):
        """Budget nav link href contains '/grid'."""
        with app.app_context():
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert b'href="/grid"' in resp.data

    def test_nav_dashboard_active_on_root(self, app, auth_client, seed_user, seed_periods):
        """Dashboard nav item is active when on /."""
        with app.app_context():
            resp = auth_client.get("/")
            html = resp.data.decode()
            # The dashboard nav link should have active class.
            assert 'Dashboard' in html
            # Check active class is on the dashboard link, not budget.
            assert 'class="nav-link active" href="/"' in html or \
                   'class="nav-link active" href="/dashboard"' in html

    def test_nav_budget_active_on_grid(self, app, auth_client, seed_user, seed_periods):
        """Budget nav item is active when on /grid."""
        with app.app_context():
            resp = auth_client.get("/grid")
            html = resp.data.decode()
            assert 'class="nav-link active" href="/grid"' in html

    def test_no_redirect_loop(self, app, auth_client, seed_user, seed_periods):
        """GET / and GET /dashboard both return 200, no redirect loops."""
        with app.app_context():
            resp1 = auth_client.get("/")
            assert resp1.status_code == 200

            resp2 = auth_client.get("/dashboard")
            assert resp2.status_code == 200
