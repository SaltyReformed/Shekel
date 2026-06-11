"""
Shekel Budget App -- Tax Config Service Tests

Verifies load_tax_configs returns the expected structure and queries
by user_id, filing_status_id, state_code, and tax_year.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.ref import FilingStatus, TaxType
from app.models.salary_profile import SalaryProfile
from app.models.tax_config import FicaConfig, StateTaxConfig, TaxBracketSet
from app.services.tax_config_service import (
    load_tax_configs,
    load_tax_configs_for_periods,
    load_tax_configs_for_year,
)

# pylint: disable=redefined-outer-name


class _FakePeriod:
    """Minimal stand-in exposing the ``start_date`` the resolver reads."""

    def __init__(self, start_date):
        self.start_date = start_date


def _make_profile(seed_user, *, state_code="NC", filing_status_name="single"):
    """Build and flush an active SalaryProfile for the seeded user."""
    filing_status = (
        db.session.query(FilingStatus).filter_by(name=filing_status_name).one()
    )
    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        name="Test Profile",
        annual_salary=Decimal("80000.00"),
        pay_periods_per_year=26,
        filing_status_id=filing_status.id,
        state_code=state_code,
        is_active=True,
    )
    db.session.add(profile)
    db.session.flush()
    return profile


def _seed_state_config(seed_user, tax_year, flat_rate, *, state_code="NC"):
    """Seed a flat StateTaxConfig for ``tax_year``; returns it."""
    flat_type = db.session.query(TaxType).filter_by(name="flat").one()
    config = StateTaxConfig(
        user_id=seed_user["user"].id,
        state_code=state_code,
        tax_year=tax_year,
        tax_type_id=flat_type.id,
        flat_rate=flat_rate,
    )
    db.session.add(config)
    db.session.flush()
    return config


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

    def test_explicit_tax_year_selects_correct_configs(self, app, db, seed_user):
        """Passing an explicit tax_year returns configs for that year, not today's."""
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
                state_code="NC",
                is_active=True,
            )
            db.session.add(profile)
            db.session.flush()

            flat_type = (
                db.session.query(TaxType).filter_by(name="flat").one()
            )

            # Seed state configs for two different years with different rates.
            current_year = date.today().year
            other_year = current_year + 1

            state_current = StateTaxConfig(
                user_id=user.id,
                state_code="NC",
                tax_year=current_year,
                tax_type_id=flat_type.id,
                flat_rate=Decimal("0.0399"),
            )
            state_other = StateTaxConfig(
                user_id=user.id,
                state_code="NC",
                tax_year=other_year,
                tax_type_id=flat_type.id,
                flat_rate=Decimal("0.0500"),
            )
            db.session.add_all([state_current, state_other])
            db.session.flush()

            # Without explicit tax_year, should return current year's config.
            result_default = load_tax_configs(user.id, profile)
            assert result_default["state_config"] is not None
            assert result_default["state_config"].flat_rate == Decimal("0.0399")

            # With explicit tax_year, should return that year's config.
            result_explicit = load_tax_configs(user.id, profile, tax_year=other_year)
            assert result_explicit["state_config"] is not None
            assert result_explicit["state_config"].flat_rate == Decimal("0.0500")

    def test_explicit_tax_year_returns_none_for_missing_year(self, app, db, seed_user):
        """Requesting a year with no configs returns None for each key."""
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
                state_code="NC",
                is_active=True,
            )
            db.session.add(profile)
            db.session.flush()

            # No configs seeded at all -- request a specific year.
            result = load_tax_configs(user.id, profile, tax_year=2099)

            assert result["bracket_set"] is None
            assert result["state_config"] is None
            assert result["fica_config"] is None


class TestLoadTaxConfigsForYear:
    """load_tax_configs_for_year: per-year load with a current-year fallback (DH-#30)."""

    def test_returns_target_year_configs_when_present(self, app, db, seed_user):
        """A future year that HAS configs returns them, not the current year's."""
        with app.app_context():
            profile = _make_profile(seed_user)
            current_year = date.today().year
            _seed_state_config(seed_user, current_year, Decimal("0.0399"))
            _seed_state_config(seed_user, current_year + 1, Decimal("0.0500"))

            result = load_tax_configs_for_year(
                seed_user["user"].id, profile, current_year + 1,
            )

            assert result["state_config"].flat_rate == Decimal("0.0500")
            assert result["state_config"].tax_year == current_year + 1

    def test_falls_back_to_current_year_when_target_missing(self, app, db, seed_user):
        """A future year with NO configs at all falls back to the current year's."""
        with app.app_context():
            profile = _make_profile(seed_user)
            current_year = date.today().year
            _seed_state_config(seed_user, current_year, Decimal("0.0399"))
            # No configs for current_year + 5.

            result = load_tax_configs_for_year(
                seed_user["user"].id, profile, current_year + 5,
            )

            assert result["state_config"].flat_rate == Decimal("0.0399")
            assert result["state_config"].tax_year == current_year

    def test_no_fallback_when_target_is_the_fallback_year(self, app, db, seed_user):
        """When the target IS the fallback year, a missing config stays None.

        The fallback only redirects OTHER years to the current year; it
        must not loop or fabricate a config for the current year itself.
        """
        with app.app_context():
            profile = _make_profile(seed_user)
            current_year = date.today().year
            # Nothing seeded for the current year.

            result = load_tax_configs_for_year(
                seed_user["user"].id, profile, current_year,
            )

            assert result["bracket_set"] is None
            assert result["state_config"] is None
            assert result["fica_config"] is None

    def test_explicit_fallback_year_overrides_current_year(self, app, db, seed_user):
        """An explicit fallback_year is used instead of the current year."""
        with app.app_context():
            profile = _make_profile(seed_user)
            current_year = date.today().year
            _seed_state_config(seed_user, current_year - 1, Decimal("0.0425"))

            result = load_tax_configs_for_year(
                seed_user["user"].id, profile, current_year + 3,
                fallback_year=current_year - 1,
            )

            assert result["state_config"].flat_rate == Decimal("0.0425")
            assert result["state_config"].tax_year == current_year - 1


class TestLoadTaxConfigsForPeriods:
    """load_tax_configs_for_periods: one resolved config set per distinct year (DH-#30)."""

    def test_maps_each_distinct_period_year(self, app, db, seed_user):
        """Returns {year: configs} for every distinct year present in periods."""
        with app.app_context():
            profile = _make_profile(seed_user)
            current_year = date.today().year
            future_year = current_year + 1
            _seed_state_config(seed_user, current_year, Decimal("0.0399"))
            _seed_state_config(seed_user, future_year, Decimal("0.0500"))

            periods = [
                _FakePeriod(date(current_year, 6, 1)),
                _FakePeriod(date(current_year, 7, 1)),  # same year, deduped
                _FakePeriod(date(future_year, 1, 1)),
            ]
            result = load_tax_configs_for_periods(
                seed_user["user"].id, profile, periods,
            )

            assert set(result.keys()) == {current_year, future_year}
            assert result[current_year]["state_config"].flat_rate == Decimal("0.0399")
            assert result[future_year]["state_config"].flat_rate == Decimal("0.0500")

    def test_missing_future_year_falls_back_in_its_own_slot(self, app, db, seed_user):
        """A period year with no configs falls back to the current year in its slot."""
        with app.app_context():
            profile = _make_profile(seed_user)
            current_year = date.today().year
            _seed_state_config(seed_user, current_year, Decimal("0.0399"))

            periods = [
                _FakePeriod(date(current_year, 6, 1)),
                _FakePeriod(date(current_year + 4, 1, 1)),  # no configs for this year
            ]
            result = load_tax_configs_for_periods(
                seed_user["user"].id, profile, periods,
            )

            assert (
                result[current_year + 4]["state_config"].flat_rate
                == Decimal("0.0399")
            )

    def test_empty_periods_returns_empty_mapping(self, app, db, seed_user):
        """No periods -> empty mapping."""
        with app.app_context():
            profile = _make_profile(seed_user)
            assert load_tax_configs_for_periods(
                seed_user["user"].id, profile, [],
            ) == {}
