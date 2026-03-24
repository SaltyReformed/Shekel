"""
Shekel Budget App -- Tax Config Service Tests

Verifies load_tax_configs returns the expected structure and queries
by user_id, filing_status_id, state_code, and tax_year.
"""

from decimal import Decimal

from app.extensions import db
from app.models.ref import FilingStatus, TaxType
from app.models.salary_profile import SalaryProfile
from app.models.tax_config import FicaConfig, StateTaxConfig, TaxBracketSet
from app.services.tax_config_service import load_tax_configs

# pylint: disable=redefined-outer-name


class TestLoadTaxConfigs:
    """load_tax_configs returns a dict with bracket_set, state_config, and fica_config."""

    def test_returns_none_values_when_no_configs_exist(self, app, db, seed_user):
        """Returns dict with None values when no tax configs are seeded."""
        with app.app_context():
            filing_status = (
                db.session.query(FilingStatus).filter_by(name="single").one()
            )
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                name="Test Profile",
                annual_salary=Decimal("80000.00"),
                pay_periods_per_year=26,
                filing_status_id=filing_status.id,
                state_code="PA",
                is_active=True,
            )
            db.session.add(profile)
            db.session.flush()

            result = load_tax_configs(seed_user["user"].id, profile)

            assert isinstance(result, dict)
            assert set(result.keys()) == {"bracket_set", "state_config", "fica_config"}
            assert result["bracket_set"] is None
            assert result["state_config"] is None
            assert result["fica_config"] is None

    def test_returns_matching_configs_when_seeded(self, app, db, seed_user):
        """Returns model instances when matching tax configs exist."""
        with app.app_context():
            user = seed_user["user"]
            filing_status = (
                db.session.query(FilingStatus).filter_by(name="single").one()
            )
            profile = SalaryProfile(
                user_id=user.id,
                scenario_id=seed_user["scenario"].id,
                name="Test Profile",
                annual_salary=Decimal("80000.00"),
                pay_periods_per_year=26,
                filing_status_id=filing_status.id,
                state_code="PA",
                is_active=True,
            )
            db.session.add(profile)
            db.session.flush()

            # Seed tax configs for the current year.
            from datetime import date
            tax_year = date.today().year

            flat_type = (
                db.session.query(TaxType).filter_by(name="flat").one()
            )

            bracket_set = TaxBracketSet(
                user_id=user.id,
                filing_status_id=filing_status.id,
                tax_year=tax_year,
                standard_deduction=Decimal("14600.00"),
                child_credit_amount=Decimal("0.00"),
                other_dependent_credit_amount=Decimal("0.00"),
            )
            db.session.add(bracket_set)

            state_config = StateTaxConfig(
                user_id=user.id,
                state_code="PA",
                tax_year=tax_year,
                tax_type_id=flat_type.id,
                flat_rate=Decimal("0.0307"),
            )
            db.session.add(state_config)

            fica_config = FicaConfig(
                user_id=user.id,
                tax_year=tax_year,
                ss_rate=Decimal("0.0620"),
                ss_wage_base=Decimal("168600.00"),
                medicare_rate=Decimal("0.0145"),
            )
            db.session.add(fica_config)
            db.session.flush()

            result = load_tax_configs(user.id, profile)

            assert result["bracket_set"] is not None
            assert isinstance(result["bracket_set"], TaxBracketSet)
            assert result["state_config"] is not None
            assert isinstance(result["state_config"], StateTaxConfig)
            assert result["fica_config"] is not None
            assert isinstance(result["fica_config"], FicaConfig)
