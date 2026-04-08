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

    def test_calendar_tab_htmx(self, app, auth_client, seed_user):
        """GET /analytics/calendar with HX-Request returns 200 with placeholder."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"coming soon" in resp.data

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
