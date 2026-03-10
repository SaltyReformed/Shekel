"""
Shekel Budget App — Pay Period Route Tests

Tests for the pay period generation form and endpoint:
  - Form rendering
  - Successful generation with defaults and custom values
  - Validation errors (missing/invalid fields)
  - Double-submit (duplicates skipped by service)
"""

from app.extensions import db
from app.models.pay_period import PayPeriod


# ── Tests ────────────────────────────────────────────────────────────


class TestPayPeriodGenerate:
    """Tests for GET/POST /pay-periods/generate."""

    def test_generate_form_redirects_to_settings(self, app, auth_client, seed_user):
        """GET /pay-periods/generate returns 302 redirect to settings dashboard."""
        with app.app_context():
            resp = auth_client.get("/pay-periods/generate")
            assert resp.status_code == 302
            assert "/settings" in resp.headers["Location"]
            assert "section=pay-periods" in resp.headers["Location"]

    def test_generate_periods_success(self, app, auth_client, seed_user):
        """POST /pay-periods/generate creates periods and redirects to grid."""
        with app.app_context():
            resp = auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-03-01",
                "num_periods": "10",
                "cadence_days": "14",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Generated 10 pay periods" in resp.data

            periods = db.session.query(PayPeriod).filter_by(
                user_id=seed_user["user"].id,
            ).all()
            assert len(periods) == 10

    def test_generate_missing_start_date(self, app, auth_client, seed_user):
        """POST /pay-periods/generate without start_date returns 422."""
        with app.app_context():
            resp = auth_client.post("/pay-periods/generate", data={
                "num_periods": "10",
            })

            assert resp.status_code == 422

    def test_generate_cadence_zero(self, app, auth_client, seed_user):
        """POST /pay-periods/generate with cadence_days=0 returns 422."""
        with app.app_context():
            resp = auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-03-01",
                "cadence_days": "0",
            })

            assert resp.status_code == 422

    def test_generate_single_period(self, app, auth_client, seed_user):
        """POST /pay-periods/generate with num_periods=1 creates one period."""
        with app.app_context():
            resp = auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-04-01",
                "num_periods": "1",
                "cadence_days": "14",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Generated 1 pay periods" in resp.data

            periods = db.session.query(PayPeriod).filter_by(
                user_id=seed_user["user"].id,
            ).all()
            assert len(periods) == 1

    def test_generate_double_submit_skips_duplicates(self, app, auth_client, seed_user):
        """Double-submit with same start_date skips overlapping periods."""
        with app.app_context():
            data = {
                "start_date": "2026-05-01",
                "num_periods": "5",
                "cadence_days": "14",
            }

            # First submit.
            auth_client.post("/pay-periods/generate", data=data,
                             follow_redirects=True)
            first_count = db.session.query(PayPeriod).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert first_count == 5

            # Second submit with same data — duplicates should be skipped.
            resp = auth_client.post("/pay-periods/generate", data=data,
                                    follow_redirects=True)
            assert resp.status_code == 200

            second_count = db.session.query(PayPeriod).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            # Should still be 5, not 10 (duplicates skipped).
            assert second_count == 5
