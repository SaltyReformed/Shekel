"""
Shekel Budget App -- Pay Period Route Tests

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
        """POST /pay-periods/generate without start_date returns 422 with field error."""
        with app.app_context():
            resp = auth_client.post("/pay-periods/generate", data={
                "num_periods": "10",
            })

            assert resp.status_code == 422
            assert b"Start Date" in resp.data
            assert b"Please fix the following errors" in resp.data

    def test_generate_cadence_zero(self, app, auth_client, seed_user):
        """POST /pay-periods/generate with cadence_days=0 returns 422 with field error."""
        with app.app_context():
            resp = auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-03-01",
                "cadence_days": "0",
            })

            assert resp.status_code == 422
            assert b"Cadence Days" in resp.data
            assert b"Please fix the following errors" in resp.data

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

            # Second submit with same data -- duplicates should be skipped.
            resp = auth_client.post("/pay-periods/generate", data=data,
                                    follow_redirects=True)
            assert resp.status_code == 200

            second_count = db.session.query(PayPeriod).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            # Should still be 5, not 10 (duplicates skipped).
            assert second_count == 5


# ── Negative Path Tests ─────────────────────────────────────────────


class TestPayPeriodNegativePaths:
    """Tests for pay period generation validation and edge cases."""

    def test_generate_invalid_date_format(self, app, auth_client, seed_user):
        """Non-date string for start_date returns 422 with validation error."""
        with app.app_context():
            resp = auth_client.post("/pay-periods/generate", data={
                "start_date": "not-a-date",
                "num_periods": "10",
                "cadence_days": "14",
            })
            assert resp.status_code == 422
            assert b"Start Date" in resp.data

            count = db.session.query(PayPeriod).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_negative_num_periods(self, app, auth_client, seed_user):
        """Negative num_periods returns 422 (Range min=1 on schema)."""
        with app.app_context():
            resp = auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-01-02",
                "num_periods": "-5",
                "cadence_days": "14",
            })
            assert resp.status_code == 422

            count = db.session.query(PayPeriod).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_zero_num_periods(self, app, auth_client, seed_user):
        """Zero num_periods returns 422 (Range min=1 on schema)."""
        with app.app_context():
            resp = auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-01-02",
                "num_periods": "0",
                "cadence_days": "14",
            })
            assert resp.status_code == 422

            count = db.session.query(PayPeriod).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_extremely_large_num_periods(self, app, auth_client, seed_user):
        """num_periods exceeding max=260 returns 422 validation error."""
        with app.app_context():
            resp = auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-01-02",
                "num_periods": "999999",
                "cadence_days": "14",
            })
            # PayPeriodGenerateSchema has Range(min=1, max=260) on num_periods.
            assert resp.status_code == 422

            count = db.session.query(PayPeriod).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_negative_cadence_days(self, app, auth_client, seed_user):
        """Negative cadence_days returns 422 (Range min=1 on schema)."""
        with app.app_context():
            resp = auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-01-02",
                "num_periods": "10",
                "cadence_days": "-1",
            })
            assert resp.status_code == 422

            count = db.session.query(PayPeriod).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_missing_all_fields(self, app, auth_client, seed_user):
        """Empty form data returns 422 with required field errors."""
        with app.app_context():
            resp = auth_client.post("/pay-periods/generate", data={})
            assert resp.status_code == 422
            # start_date is the only truly required field
            # (num_periods and cadence_days have load_defaults).
            assert b"Start Date" in resp.data

            count = db.session.query(PayPeriod).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_cadence_zero_db_state(self, app, auth_client, seed_user):
        """Cadence zero returns 422 and creates no pay periods in the DB."""
        with app.app_context():
            resp = auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-03-01",
                "cadence_days": "0",
            })
            assert resp.status_code == 422
            assert b"Cadence Days" in resp.data

            count = db.session.query(PayPeriod).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0
