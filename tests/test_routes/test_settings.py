"""
Shekel Budget App — Settings Route Tests

Tests for user settings page:
  - Rendering settings (with existing and auto-created settings)
  - Updating all three fields
  - Validation errors for each field
  - Partial updates (blank fields skipped)
"""

from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.models.user import UserSettings


class TestSettingsShow:
    """Tests for GET /settings."""

    def test_settings_page_renders(self, app, auth_client, seed_user):
        """GET /settings renders the settings page with general section."""
        with app.app_context():
            resp = auth_client.get("/settings")
            assert resp.status_code == 200
            assert b"General Settings" in resp.data
            assert b'name="grid_default_periods"' in resp.data
            assert b'name="default_inflation_rate"' in resp.data

    def test_settings_auto_creates_if_missing(self, app, auth_client, seed_user):
        """GET /settings auto-creates UserSettings when missing."""
        with app.app_context():
            # Delete existing settings created by seed_user.
            db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).delete()
            db.session.commit()

            resp = auth_client.get("/settings")
            assert resp.status_code == 200

            # Settings should now exist.
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            assert settings.grid_default_periods == 6  # Default value.


class TestSettingsUpdate:
    """Tests for POST /settings."""

    def test_update_all_fields(self, app, auth_client, seed_user):
        """POST /settings updates all three settings fields."""
        with app.app_context():
            resp = auth_client.post("/settings", data={
                "grid_default_periods": "10",
                "default_inflation_rate": "0.0400",
                "low_balance_threshold": "1000",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Settings updated" in resp.data

            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            assert settings.grid_default_periods == 10
            assert settings.default_inflation_rate == Decimal("0.0400")
            assert settings.low_balance_threshold == 1000

    def test_invalid_grid_periods(self, app, auth_client, seed_user):
        """POST /settings with non-numeric grid_periods flashes error."""
        with app.app_context():
            resp = auth_client.post("/settings", data={
                "grid_default_periods": "abc",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid number for grid periods" in resp.data

    def test_invalid_inflation_rate(self, app, auth_client, seed_user):
        """POST /settings with invalid Decimal for inflation flashes error."""
        with app.app_context():
            resp = auth_client.post("/settings", data={
                "default_inflation_rate": "not-a-number",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid inflation rate" in resp.data

    def test_invalid_threshold(self, app, auth_client, seed_user):
        """POST /settings with non-numeric threshold flashes error."""
        with app.app_context():
            resp = auth_client.post("/settings", data={
                "low_balance_threshold": "xyz",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid number for low balance threshold" in resp.data

    def test_blank_fields_skipped(self, app, auth_client, seed_user):
        """POST /settings with blank fields preserves existing values."""
        with app.app_context():
            # Set known values first.
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            settings.grid_default_periods = 8
            settings.low_balance_threshold = 750
            db.session.commit()

            # Submit with all blank — should not change anything.
            resp = auth_client.post("/settings", data={
                "grid_default_periods": "",
                "default_inflation_rate": "",
                "low_balance_threshold": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Settings updated" in resp.data

            db.session.refresh(settings)
            assert settings.grid_default_periods == 8
            assert settings.low_balance_threshold == 750


class TestGridAccountSetting:
    """Tests for the default grid account dropdown in settings."""

    def test_settings_renders_account_dropdown(self, app, auth_client, seed_user):
        """GET /settings renders the grid account dropdown with user's accounts."""
        with app.app_context():
            resp = auth_client.get("/settings")
            assert resp.status_code == 200
            assert b"Default Grid Account" in resp.data
            assert b"Checking" in resp.data

    def test_set_grid_account(self, app, auth_client, seed_user):
        """POST /settings with valid account id sets default_grid_account_id."""
        with app.app_context():
            resp = auth_client.post("/settings", data={
                "default_grid_account_id": str(seed_user["account"].id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            assert settings.default_grid_account_id == seed_user["account"].id

    def test_clear_grid_account(self, app, auth_client, seed_user):
        """POST /settings with blank grid account clears the setting."""
        with app.app_context():
            # Set it first.
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            settings.default_grid_account_id = seed_user["account"].id
            db.session.commit()

            resp = auth_client.post("/settings", data={
                "default_grid_account_id": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.expire_all()
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            assert settings.default_grid_account_id is None

    def test_reject_invalid_grid_account(self, app, auth_client, seed_user):
        """POST /settings with invalid account id flashes error."""
        with app.app_context():
            resp = auth_client.post("/settings", data={
                "default_grid_account_id": "999999",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid grid account" in resp.data


class TestSettingsDashboard:
    """Tests for the settings dashboard sections and redirects."""

    def test_settings_dashboard_default_section(self, app, auth_client, seed_user):
        """GET /settings renders the dashboard with General section by default."""
        with app.app_context():
            resp = auth_client.get("/settings")
            assert resp.status_code == 200
            assert b"Settings" in resp.data
            assert b"General" in resp.data

    def test_settings_dashboard_categories_section(self, app, auth_client, seed_user):
        """GET /settings?section=categories renders category management."""
        with app.app_context():
            resp = auth_client.get("/settings?section=categories")
            assert resp.status_code == 200
            assert b"Add Category" in resp.data
            assert b'name="group_name"' in resp.data

    def test_settings_dashboard_tax_section(self, app, auth_client, seed_user):
        """GET /settings?section=tax renders tax configuration."""
        with app.app_context():
            resp = auth_client.get("/settings?section=tax")
            assert resp.status_code == 200
            assert b"Tax Configuration" in resp.data
            assert b"Federal Tax Brackets" in resp.data

    def test_settings_dashboard_retirement_section(self, app, auth_client, seed_user):
        """GET /settings?section=retirement renders retirement settings."""
        with app.app_context():
            resp = auth_client.get("/settings?section=retirement")
            assert resp.status_code == 200
            assert b"Retirement Settings" in resp.data
            assert b'name="safe_withdrawal_rate"' in resp.data

    def test_settings_dashboard_account_types_section(self, app, auth_client, seed_user):
        """GET /settings?section=account-types renders account type management."""
        with app.app_context():
            resp = auth_client.get("/settings?section=account-types")
            assert resp.status_code == 200
            assert b"Account Types" in resp.data

    def test_settings_dashboard_pay_periods_section(self, app, auth_client, seed_user):
        """GET /settings?section=pay-periods renders pay period generation form."""
        with app.app_context():
            resp = auth_client.get("/settings?section=pay-periods")
            assert resp.status_code == 200
            assert b"Generate Pay Periods" in resp.data
            assert b'name="start_date"' in resp.data

    def test_settings_dashboard_invalid_section_defaults_to_general(self, app, auth_client, seed_user):
        """GET /settings?section=bogus defaults to the General section."""
        with app.app_context():
            resp = auth_client.get("/settings?section=bogus")
            assert resp.status_code == 200
            assert b"General" in resp.data

    def test_categories_get_redirects_to_settings(self, app, auth_client, seed_user):
        """GET /categories returns 302 redirect to settings dashboard."""
        with app.app_context():
            resp = auth_client.get("/categories")
            assert resp.status_code == 302
            assert "section=categories" in resp.headers["Location"]

    def test_categories_post_still_works(self, app, auth_client, seed_user):
        """POST /categories still processes form submission (not redirected)."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "TestGroup",
                "item_name": "TestItem",
            }, follow_redirects=True)
            assert resp.status_code == 200
            assert b"TestItem" in resp.data

    def test_settings_dashboard_security_section(self, app, auth_client, seed_user):
        """GET /settings?section=security renders the Security section."""
        with app.app_context():
            resp = auth_client.get("/settings?section=security")
            assert resp.status_code == 200
            assert b"Change Password" in resp.data
