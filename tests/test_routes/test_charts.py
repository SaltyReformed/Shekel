"""
Shekel Budget App -- Charts Route Tests (Post-Cleanup)

The /charts page was replaced by /analytics in Section 8.
Tests verify the 301 redirect and that old fragment endpoints
return 404 (removed).
"""


class TestChartsRedirect:
    """Tests for the /charts -> /analytics redirect."""

    def test_charts_redirects_to_analytics(self, app, auth_client,
                                            seed_user):
        """C18-1: GET /charts returns 301 redirect to /analytics."""
        with app.app_context():
            resp = auth_client.get("/charts")
            assert resp.status_code == 301
            assert "/analytics" in resp.headers["Location"]

    def test_charts_redirect_requires_auth(self, app, client):
        """C18-2: GET /charts unauthenticated redirects to login first."""
        with app.app_context():
            resp = client.get("/charts")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_charts_fragment_endpoints_gone(self, app, auth_client,
                                            seed_user):
        """C18-extra1: Old fragment endpoints return 404."""
        with app.app_context():
            for path in [
                "/charts/balance-over-time",
                "/charts/spending-by-category",
                "/charts/budget-vs-actuals",
                "/charts/amortization",
                "/charts/net-worth",
                "/charts/net-pay",
            ]:
                resp = auth_client.get(
                    path, headers={"HX-Request": "true"},
                )
                assert resp.status_code == 404, (
                    f"{path} should return 404 but got {resp.status_code}"
                )
