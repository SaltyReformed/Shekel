"""
Shekel Budget App -- Analytics Route Tests

Tests for the analytics page shell and HTMX tab endpoints:
  - Authentication required for all endpoints
  - Main page renders with nav-pills and tab-content div
  - Tab endpoints return placeholders with HX-Request header
  - Tab endpoints redirect without HX-Request header
  - Nav bar shows Analytics link with correct active state
  - Charts route still functions after nav rename
"""


# ── Auth Tests ──────────────────────────────────────────────────────


class TestAnalyticsAuth:
    """Tests for authentication requirements on analytics endpoints."""

    def test_analytics_requires_auth(self, app, client):
        """GET /analytics redirects unauthenticated users to login."""
        with app.app_context():
            resp = client.get("/analytics")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_all_tabs_require_auth(self, app, client):
        """All four tab endpoints redirect unauthenticated users to login."""
        tab_urls = [
            "/analytics/calendar",
            "/analytics/year-end",
            "/analytics/variance",
            "/analytics/trends",
        ]
        with app.app_context():
            for url in tab_urls:
                resp = client.get(url)
                assert resp.status_code == 302, (
                    f"{url} did not require auth"
                )
                assert "/login" in resp.headers["Location"], (
                    f"{url} did not redirect to login"
                )


# ── Page Rendering Tests ──────────────────────────────────────────


class TestAnalyticsPage:
    """Tests for GET /analytics page structure and content."""

    def test_analytics_page_renders(self, app, auth_client, seed_user):
        """GET /analytics returns 200 with Analytics heading."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            assert b"Analytics" in resp.data

    def test_analytics_page_has_four_pills(self, app, auth_client, seed_user):
        """GET /analytics includes all four nav-pill button labels."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            html = resp.data
            assert b"Calendar" in html
            assert b"Year-End" in html
            assert b"Variance" in html
            assert b"Trends" in html

    def test_analytics_page_has_tab_content_div(self, app, auth_client, seed_user):
        """GET /analytics contains the #tab-content target div for HTMX swaps."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            assert b'id="tab-content"' in resp.data

    def test_calendar_tab_is_default_load(self, app, auth_client, seed_user):
        """Calendar pill has hx-trigger containing 'load' so it auto-loads."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            html = resp.data.decode()
            assert 'hx-trigger="click, load"' in html

    def test_other_tabs_no_auto_load(self, app, auth_client, seed_user):
        """Year-End, Variance, and Trends pills do not auto-load on page visit."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            html = resp.data.decode()
            # Only the Calendar pill should have the 'load' trigger.
            load_triggers = html.count('hx-trigger="click, load"')
            assert load_triggers == 1, (
                f"Expected exactly 1 pill with 'load' trigger, found {load_triggers}"
            )

    def test_tab_content_has_spinner(self, app, auth_client, seed_user):
        """The #tab-content div contains spinner markup as initial content."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            assert b"spinner-border" in resp.data

    def test_analytics_uses_scroll_pills(self, app, auth_client, seed_user):
        """GET /analytics uses the shekel-scroll-pills class for scroll behavior."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            assert b"shekel-scroll-pills" in resp.data


# ── HTMX Tab Tests ────────────────────────────────────────────────


class TestCalendarTab:
    """Tests for GET /analytics/calendar HTMX partial endpoint."""

    def test_calendar_tab_htmx(self, app, auth_client, seed_user, seed_periods):
        """GET /analytics/calendar with HX-Request returns 200 with calendar."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            # Calendar replaced the placeholder; month view renders by default.
            assert b"calendar-grid" in resp.data

    def test_calendar_tab_no_htmx_redirects(self, app, auth_client, seed_user):
        """GET /analytics/calendar without HX-Request redirects to /analytics."""
        with app.app_context():
            resp = auth_client.get("/analytics/calendar")
            assert resp.status_code == 302
            assert "/analytics" in resp.headers["Location"]


class TestYearEndTab:
    """Tests for GET /analytics/year-end HTMX partial endpoint."""

    def test_year_end_tab_htmx(self, app, auth_client, seed_user):
        """GET /analytics/year-end with HX-Request returns 200."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/year-end",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

    def test_year_end_tab_no_htmx_redirects(self, app, auth_client, seed_user):
        """GET /analytics/year-end without HX-Request redirects to /analytics."""
        with app.app_context():
            resp = auth_client.get("/analytics/year-end")
            assert resp.status_code == 302
            assert "/analytics" in resp.headers["Location"]


class TestVarianceTab:
    """Tests for GET /analytics/variance HTMX partial endpoint."""

    def test_variance_tab_htmx(self, app, auth_client, seed_user):
        """GET /analytics/variance with HX-Request returns 200."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/variance",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

    def test_variance_tab_no_htmx_redirects(self, app, auth_client, seed_user):
        """GET /analytics/variance without HX-Request redirects to /analytics."""
        with app.app_context():
            resp = auth_client.get("/analytics/variance")
            assert resp.status_code == 302
            assert "/analytics" in resp.headers["Location"]


class TestTrendsTab:
    """Tests for GET /analytics/trends HTMX partial endpoint."""

    def test_trends_tab_htmx(self, app, auth_client, seed_user):
        """GET /analytics/trends with HX-Request returns 200."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

    def test_trends_tab_no_htmx_redirects(self, app, auth_client, seed_user):
        """GET /analytics/trends without HX-Request redirects to /analytics."""
        with app.app_context():
            resp = auth_client.get("/analytics/trends")
            assert resp.status_code == 302
            assert "/analytics" in resp.headers["Location"]


# ── Nav Bar Tests ─────────────────────────────────────────────────


class TestAnalyticsNav:
    """Tests for nav bar updates after Charts-to-Analytics rename."""

    def test_nav_shows_analytics_link(self, app, auth_client, seed_user):
        """Authenticated pages show Analytics link in the nav bar."""
        with app.app_context():
            resp = auth_client.get("/")
            assert resp.status_code == 200
            assert b"Analytics" in resp.data
            assert b'href="/analytics"' in resp.data

    def test_nav_does_not_show_charts_link(self, app, auth_client, seed_user):
        """Nav bar no longer shows a link pointing to /charts."""
        with app.app_context():
            resp = auth_client.get("/")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'href="/charts"' not in html

    def test_charts_route_still_works(self, app, auth_client, seed_user):
        """GET /charts still returns 200 -- route is unlinked, not removed."""
        with app.app_context():
            resp = auth_client.get("/charts")
            assert resp.status_code == 200

    def test_analytics_active_nav_state(self, app, auth_client, seed_user):
        """GET /analytics shows the Analytics nav item as active."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The nav link for /analytics should have the active class.
            assert 'class="nav-link active" href="/analytics"' in html


# ── Calendar Month View Tests ────────────────────────────────────────


class TestCalendarMonthView:
    """Tests for the calendar month detail view."""

    def test_calendar_month_renders(self, app, auth_client, seed_user, seed_periods):
        """Month view renders with current month name."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-grid" in resp.data

    def test_calendar_month_navigation(self, app, auth_client, seed_user, seed_periods):
        """Month view for specific month/year contains the correct heading."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=3",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"March" in resp.data
            assert b"2026" in resp.data

    def test_calendar_month_has_day_cells(self, app, auth_client, seed_user, seed_periods):
        """Month view contains calendar-day elements."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-day" in resp.data

    def test_calendar_paycheck_highlighting(self, app, auth_client, seed_user, seed_periods):
        """Paycheck days have the calendar-paycheck CSS class."""
        with app.app_context():
            # Request a month with known paycheck days (Jan 2026 has
            # periods starting Jan 2, Jan 16, Jan 30).
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-paycheck" in resp.data

    def test_calendar_month_empty(self, app, auth_client, seed_user, seed_periods):
        """Month with no transactions renders without crash."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=4",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-grid" in resp.data

    def test_calendar_month_prev_next(self, app, auth_client, seed_user, seed_periods):
        """Month view has prev/next navigation buttons."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=6",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "month=5" in html  # prev button
            assert "month=7" in html  # next button

    def test_calendar_month_december_next_wraps(self, app, auth_client, seed_user, seed_periods):
        """December next button wraps to January of next year."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=12",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "year=2027" in html
            assert "month=1" in html

    def test_calendar_month_january_prev_wraps(self, app, auth_client, seed_user, seed_periods):
        """January prev button wraps to December of prior year."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "year=2025" in html
            assert "month=12" in html

    def test_calendar_month_year_overview_button(self, app, auth_client, seed_user, seed_periods):
        """Month view has a button to switch to year overview."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "view=year" in html

    def test_calendar_month_totals_displayed(self, app, auth_client, seed_user, seed_periods, db):
        """Month view shows income/expense/net totals."""
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum, TxnTypeEnum
            from app.models.transaction import Transaction
            from datetime import date
            from decimal import Decimal

            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Test Income",
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                estimated_amount=Decimal("3000.00"),
                due_date=date(2026, 1, 5),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"Income" in resp.data
            assert b"Expenses" in resp.data
            assert b"Net" in resp.data

    def test_calendar_default_view_is_month(self, app, auth_client, seed_user, seed_periods):
        """No view param defaults to month view."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-grid" in resp.data

    def test_calendar_invalid_month_handled(self, app, auth_client, seed_user, seed_periods):
        """Invalid month=13 clamped to valid range, no crash."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&month=13",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

    def test_calendar_today_highlighted(self, app, auth_client, seed_user, seed_periods):
        """Current month view contains today indicator class."""
        with app.app_context():
            from datetime import date
            today = date.today()
            resp = auth_client.get(
                f"/analytics/calendar?view=month&year={today.year}&month={today.month}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-day--today" in resp.data


# ── Calendar Year View Tests ─────────────────────────────────────────


class TestCalendarYearView:
    """Tests for the calendar year overview."""

    def test_calendar_year_renders(self, app, auth_client, seed_user, seed_periods):
        """Year view renders with all 12 month names."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=year&year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            for month_name in [b"January", b"February", b"March", b"April",
                               b"May", b"June", b"July", b"August",
                               b"September", b"October", b"November", b"December"]:
                assert month_name in resp.data

    def test_calendar_third_paycheck_badge(self, app, auth_client, seed_user, db):
        """Year with 26 periods shows '3rd check' badge."""
        with app.app_context():
            from app.services import pay_period_service
            from datetime import date
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=26,
                cadence_days=14,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=year&year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"3rd check" in resp.data

    def test_calendar_year_navigation(self, app, auth_client, seed_user, seed_periods):
        """Year view navigation shows correct year."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=year&year=2025",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"2025" in resp.data

    def test_calendar_year_month_click_links(self, app, auth_client, seed_user, seed_periods):
        """Month cards contain hx-get with view=month params."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=year&year=2026",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "view=month" in html

    def test_calendar_year_annual_totals(self, app, auth_client, seed_user, seed_periods):
        """Year view shows annual total labels."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=year&year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"Annual Income" in resp.data
            assert b"Annual Expenses" in resp.data
            assert b"Annual Net" in resp.data
