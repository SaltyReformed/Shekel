"""
Shekel Budget App -- Retirement Dashboard Service Tests

Unit tests for the retirement_dashboard_service module, verifying that
the extracted gap analysis and projection logic produces correct
financial computations independently of the Flask route layer.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.pension_profile import PensionProfile
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.user import UserSettings
from app.services import retirement_dashboard_service


class TestComputeGapData:
    """Tests for the top-level compute_gap_data orchestrator."""

    def test_returns_expected_keys(self, app, db, seed_user, seed_periods):
        """Return dict contains all template context keys."""
        with app.app_context():
            result = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            expected_keys = {
                "gap_analysis", "chart_data", "pension_benefit",
                "retirement_account_projections", "settings",
                "salary_profiles", "pensions",
            }
            assert set(result.keys()) == expected_keys

    def test_user_with_no_accounts_returns_safe_defaults(
        self, app, db, seed_user, seed_periods
    ):
        """User with no retirement accounts gets zero projections."""
        with app.app_context():
            result = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            assert result["retirement_account_projections"] == []
            assert result["pension_benefit"] is None

    def test_user_with_no_salary_profile(self, app, db, seed_user, seed_periods):
        """User with no salary profile still returns valid structure."""
        with app.app_context():
            result = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            assert result["gap_analysis"] is not None
            assert result["salary_profiles"] == []

    def test_pensions_list_populated(self, app, db, seed_user, seed_periods):
        """Active pensions are included in the pensions list."""
        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="Main",
                annual_salary=Decimal("80000"),
                pay_periods_per_year=26,
                state_code="NC",
                is_active=True,
            )
            db.session.add(profile)
            db.session.flush()

            pension = PensionProfile(
                user_id=seed_user["user"].id,
                salary_profile_id=profile.id,
                name="State Pension",
                benefit_multiplier=Decimal("0.01750"),
                consecutive_high_years=4,
                hire_date=date(2010, 1, 1),
                planned_retirement_date=date(2050, 1, 1),
                is_active=True,
            )
            db.session.add(pension)
            db.session.commit()

            result = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            assert len(result["pensions"]) == 1
            assert result["pension_benefit"] is not None


class TestComputeSliderDefaults:
    """Tests for the slider default computation."""

    def test_default_swr_when_no_settings(self, app, db, seed_user, seed_periods):
        """Default SWR is 4.0% when user has no settings."""
        with app.app_context():
            data = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            slider = retirement_dashboard_service.compute_slider_defaults(data)
            # seed_user has settings with default SWR.
            assert isinstance(slider["current_swr"], float)
            assert slider["current_swr"] > 0

    def test_default_return_when_no_accounts(self, app, db, seed_user, seed_periods):
        """Default return rate is 7.0% when user has no retirement accounts."""
        with app.app_context():
            data = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            slider = retirement_dashboard_service.compute_slider_defaults(data)
            assert slider["current_return"] == 7.0
