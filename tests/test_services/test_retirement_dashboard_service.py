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
    """Tests for the slider default computation.

    Post-C-45 (F-100 / F-101): the returned ``current_swr`` and
    ``current_return`` keys carry :class:`~decimal.Decimal` percentages
    quantised to ``Decimal("0.01")``.  Earlier versions returned
    ``float`` and the dashboard template's ``"%.2f"|format(...)`` masked
    the precision drift; these tests pin the new Decimal contract.
    """

    def test_default_swr_uses_user_setting_as_decimal(
        self, app, db, seed_user, seed_periods,
    ):
        """``current_swr`` is a Decimal scaled from the user's stored SWR.

        ``seed_user`` constructs ``UserSettings`` with the model-level
        default ``safe_withdrawal_rate = Decimal("0.0400")``, so the
        slider default should round-trip to ``Decimal("4.00")``.
        Arithmetic: 0.0400 * 100 = 4.00.  Asserts exact equality to
        catch any future regression that re-introduces a float cast
        (which would have produced 3.9999... or 4.000000000001 instead).
        """
        with app.app_context():
            data = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            slider = retirement_dashboard_service.compute_slider_defaults(data)
            assert isinstance(slider["current_swr"], Decimal)
            assert slider["current_swr"] == Decimal("4.00")

    def test_default_return_when_no_accounts(self, app, db, seed_user, seed_periods):
        """``current_return`` falls back to Decimal('7.00') with no accounts.

        ``seed_user`` does not seed any retirement or investment
        accounts, so the balance-weighted average has no inputs to
        weight; the function must return the module-level
        ``_DEFAULT_RETURN_PCT`` (S&P 500 long-run real return baseline).
        Asserts type as well as value to keep the Decimal contract
        pinned (F-100 fix).
        """
        with app.app_context():
            data = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            slider = retirement_dashboard_service.compute_slider_defaults(data)
            assert isinstance(slider["current_return"], Decimal)
            assert slider["current_return"] == Decimal("7.00")

    def test_default_swr_when_settings_none(self, app, db, seed_user, seed_periods):
        """``current_swr`` falls back to Decimal('4.00') when settings is None.

        ``compute_slider_defaults`` accepts the dict returned by
        ``compute_gap_data``; that dict carries ``settings = None``
        only when the user has no ``UserSettings`` row.  Splicing a
        ``settings=None`` dict in-place verifies the fallback branch
        without having to delete + recreate the seeded settings row
        (which would also need to keep the rest of ``data`` intact).
        Asserts the result is the unaltered ``_DEFAULT_SWR_PCT``
        constant (Decimal('4.00'), Trinity Study baseline).
        """
        with app.app_context():
            data = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            data["settings"] = None
            slider = retirement_dashboard_service.compute_slider_defaults(data)
            assert isinstance(slider["current_swr"], Decimal)
            assert slider["current_swr"] == Decimal("4.00")

    def test_zero_swr_round_trips_as_decimal_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """An explicit Decimal('0') SWR survives the round-trip as Decimal('0.00').

        Storing ``safe_withdrawal_rate = Decimal("0")`` is semantically
        distinct from ``None`` (the F-077 / C-24 CHECK constraint
        permits both NULL and zero; zero means "explicit zero rate,"
        NULL means "use the default").  This test pins the boundary:
        the function must NOT collapse a stored zero to
        ``_DEFAULT_SWR_PCT``.  Arithmetic: 0.0000 * 100 = 0.0000,
        quantised to Decimal('0.00').
        """
        with app.app_context():
            settings = (
                db.session.query(UserSettings)
                .filter_by(user_id=seed_user["user"].id)
                .one()
            )
            settings.safe_withdrawal_rate = Decimal("0")
            db.session.commit()

            data = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            slider = retirement_dashboard_service.compute_slider_defaults(data)
            assert isinstance(slider["current_swr"], Decimal)
            assert slider["current_swr"] == Decimal("0.00")
