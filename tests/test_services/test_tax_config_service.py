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
    profile_tax_series,
    resolve_tax_year,
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
        filing_status_id=filing_status.id,
        state_code=state_code,
        is_active=True,
    )
    db.session.add(profile)
    db.session.flush()
    return profile


def _seed_state_config(
    seed_user, tax_year, flat_rate, *, state_code="NC", filing_status_name="single",
):
    """Seed a flat StateTaxConfig for ``tax_year``; returns it.

    T-P5: state configs are filing-status-keyed, so the row carries a
    filing status (defaults to ``single``, matching the ``_make_profile``
    default so ``load_tax_configs`` resolves it for the test profile).
    """
    flat_type = db.session.query(TaxType).filter_by(name="flat").one()
    filing_status = (
        db.session.query(FilingStatus).filter_by(name=filing_status_name).one()
    )
    config = StateTaxConfig(
        user_id=seed_user["user"].id,
        state_code=state_code,
        tax_year=tax_year,
        tax_type_id=flat_type.id,
        filing_status_id=filing_status.id,
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
                filing_status_id=filing_status.id,
                state_code="PA",
                is_active=True,
            )
            db.session.add(profile)
            db.session.flush()

            result = load_tax_configs(
                seed_user["user"].id, profile, date.today().year,
            )

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
                filing_status_id=filing_status.id,
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

            result = load_tax_configs(user.id, profile, date.today().year)

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
                filing_status_id=filing_status.id,
                flat_rate=Decimal("0.0399"),
            )
            state_other = StateTaxConfig(
                user_id=user.id,
                state_code="NC",
                tax_year=other_year,
                tax_type_id=flat_type.id,
                filing_status_id=filing_status.id,
                flat_rate=Decimal("0.0500"),
            )
            db.session.add_all([state_current, state_other])
            db.session.flush()

            # The current year's own configs.
            result_default = load_tax_configs(
                user.id, profile, date.today().year,
            )
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


class TestResolveTaxYear:
    """resolve_tax_year: the ONE substitution rule, and it reads no clock.

    A pure function over the configured-year set, so these cases use ABSOLUTE
    years and pass on every calendar day -- which is itself the property under
    test.  The rule it replaced consulted ``date.today().year`` and therefore
    could not be stated without one.
    """

    def test_an_exactly_configured_year_resolves_to_itself(self):
        """A year with its own configuration is never substituted."""
        assert resolve_tax_year(2026, (2025, 2026)) == 2026

    def test_a_future_year_resolves_to_the_latest_configured(self):
        """An unconfigured FUTURE year takes the newest published rules."""
        assert resolve_tax_year(2029, (2025, 2026)) == 2026

    def test_it_reaches_back_not_forward_when_both_are_possible(self):
        """With configuration on both sides, the year AT OR BEFORE wins.

        Tax rules take effect and persist, so 2027 is governed by 2026's
        published rules, not by 2030's -- even though 2030 is the nearer year
        in absolute distance.
        """
        assert resolve_tax_year(2027, (2026, 2030)) == 2026

    def test_a_year_predating_all_configuration_reaches_forward(self):
        """The weaker arm: a historical year with nothing at or before it.

        An approximation for a year the user never configured, and the only
        answer available other than none at all.
        """
        assert resolve_tax_year(2019, (2025, 2026)) == 2025

    def test_no_configuration_at_all_resolves_to_nothing(self):
        """An empty candidate set has nothing to substitute, so it says so."""
        assert resolve_tax_year(2026, ()) is None

    def test_the_current_year_being_unconfigured_does_not_strand_it(self):
        """The New Year cliff, stated as the rule that removes it.

        The retired rule substituted the CURRENT calendar year, so a request
        FOR that year found nothing to redirect to and resolved to no
        configuration -- which the paycheck engine reads as zero Social
        Security.  Measured on production data 2026-08-11: 40 of 51 live-priced
        salary rows moved and projected income rose $8,460.50 on 2027-01-01.
        Here 2027 is both the requested year and "today", and it still resolves.
        """
        assert resolve_tax_year(2027, (2025, 2026)) == 2026


class TestProfileTaxSeries:
    """profile_tax_series: three INDEPENDENT candidate sets, scoped as the loader is."""

    def test_each_kind_keeps_its_own_years(self, app, db, seed_user):
        """The three series are separate; one kind's year is not another's."""
        with app.app_context():
            profile = _make_profile(seed_user)
            _seed_state_config(seed_user, 2024, Decimal("0.0399"))
            db.session.add(FicaConfig(
                user_id=seed_user["user"].id, tax_year=2031,
                ss_rate=Decimal("0.0620"), ss_wage_base=Decimal("200000.00"),
                medicare_rate=Decimal("0.0145"),
            ))
            db.session.flush()

            series = profile_tax_series(seed_user["user"].id, profile)

            assert sorted(series.state_configs) == [2024]
            assert sorted(series.fica_configs) == [2031]
            assert sorted(series.bracket_sets) == []

    def test_a_year_configured_for_another_filing_status_is_not_a_candidate(
        self, app, db, seed_user,
    ):
        """Scoping matches the loader's, so no cross-status substitution.

        ``load_tax_configs`` reads the state config by
        ``(user, state, year, filing_status)``.  A year counted as configured
        on another status would resolve to it and then load ``None`` -- the
        silent-zero-withholding outcome this rule exists to prevent.
        """
        with app.app_context():
            profile = _make_profile(seed_user, filing_status_name="single")
            _seed_state_config(
                seed_user, 2024, Decimal("0.0500"),
                filing_status_name="married_jointly",
            )
            db.session.flush()

            series = profile_tax_series(seed_user["user"].id, profile)

            assert series.state_configs == {}


class TestOneKindsYearsDoNotDecideAnotherKinds:
    """A PARTIALLY configured year must not zero the kinds it lacks.

    The defect an adversarial review caught in this change's first draft, which
    resolved ONE year for the profile from the UNION of the three tables.  A
    year present in only one table then became the resolved year for itself AND
    every later year, and the two missing kinds silently returned ``None`` --
    the same zero-withholding failure the change exists to remove, widened from
    one year to the whole horizon.

    It is the state the app's own settings screen produces: that screen writes
    ``StateTaxConfig`` and ``FicaConfig`` for any year in ``[2000, 2100]``, and
    nothing in ``app/`` ever creates a ``TaxBracketSet`` outside the signup
    seed.  Measured on a clone of production 2026-08-11 under the union rule:
    saving one 2027 state-tax row dropped a 2028 paycheck's Social Security to
    ``$0.00`` and raised its net by **$216.63**.
    """

    def test_a_state_only_year_does_not_strand_the_bracket_set_or_fica(
        self, app, db, seed_user,
    ):
        """2027 has only a state config; the other two still resolve to 2026."""
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _make_profile(seed_user)
            flat_type = db.session.query(TaxType).filter_by(name="flat").one()
            _seed_state_config(seed_user, 2026, Decimal("0.0399"))
            db.session.add_all([
                TaxBracketSet(
                    user_id=user_id, tax_year=2026,
                    filing_status_id=profile.filing_status_id,
                    standard_deduction=Decimal("15000.00"),
                ),
                FicaConfig(
                    user_id=user_id, tax_year=2026,
                    ss_rate=Decimal("0.0620"),
                    ss_wage_base=Decimal("184500.00"),
                    medicare_rate=Decimal("0.0145"),
                ),
                # The one form save: a 2027 STATE config and nothing else.
                StateTaxConfig(
                    user_id=user_id, state_code="NC", tax_year=2027,
                    tax_type_id=flat_type.id,
                    filing_status_id=profile.filing_status_id,
                    flat_rate=Decimal("0.0450"),
                ),
            ])
            db.session.flush()

            for requested in (2027, 2028):
                result = load_tax_configs_for_year(user_id, profile, requested)
                assert result["bracket_set"] is not None, requested
                assert result["bracket_set"].tax_year == 2026, requested
                assert result["fica_config"] is not None, requested
                assert result["fica_config"].tax_year == 2026, requested
                # The state config resolves on its OWN series, so it takes the
                # 2027 row the user actually saved.
                assert result["state_config"].tax_year == 2027, requested

    def test_a_fica_only_year_does_not_strand_the_state_config_or_brackets(
        self, app, db, seed_user,
    ):
        """The mirror image: a FICA-only year leaves the other two on 2026."""
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _make_profile(seed_user)
            _seed_state_config(seed_user, 2026, Decimal("0.0399"))
            db.session.add_all([
                TaxBracketSet(
                    user_id=user_id, tax_year=2026,
                    filing_status_id=profile.filing_status_id,
                    standard_deduction=Decimal("15000.00"),
                ),
                FicaConfig(
                    user_id=user_id, tax_year=2026,
                    ss_rate=Decimal("0.0620"),
                    ss_wage_base=Decimal("184500.00"),
                    medicare_rate=Decimal("0.0145"),
                ),
                FicaConfig(
                    user_id=user_id, tax_year=2027,
                    ss_rate=Decimal("0.0620"),
                    ss_wage_base=Decimal("190000.00"),
                    medicare_rate=Decimal("0.0145"),
                ),
            ])
            db.session.flush()

            result = load_tax_configs_for_year(user_id, profile, 2028)

            assert result["fica_config"].tax_year == 2027
            assert result["fica_config"].ss_wage_base == Decimal("190000.00")
            assert result["state_config"].tax_year == 2026
            assert result["bracket_set"].tax_year == 2026


class TestLoadTaxConfigsForYear:
    """load_tax_configs_for_year: resolve which year applies, then load it (DH-#30)."""

    def test_returns_target_year_configs_when_present(self, app, db, seed_user):
        """A year that HAS configs returns them, with no substitution."""
        with app.app_context():
            profile = _make_profile(seed_user)
            _seed_state_config(seed_user, 2025, Decimal("0.0399"))
            _seed_state_config(seed_user, 2026, Decimal("0.0500"))

            result = load_tax_configs_for_year(
                seed_user["user"].id, profile, 2026,
            )

            assert result["state_config"].flat_rate == Decimal("0.0500")
            assert result["state_config"].tax_year == 2026

    def test_an_unconfigured_year_loads_the_latest_configured_year(
        self, app, db, seed_user,
    ):
        """A year with no configs loads the newest configured year's."""
        with app.app_context():
            profile = _make_profile(seed_user)
            _seed_state_config(seed_user, 2026, Decimal("0.0399"))

            result = load_tax_configs_for_year(
                seed_user["user"].id, profile, 2031,
            )

            assert result["state_config"].flat_rate == Decimal("0.0399")
            assert result["state_config"].tax_year == 2026

    def test_a_user_with_no_configuration_gets_none(self, app, db, seed_user):
        """Nothing configured anywhere is the ONLY way all three come back None.

        It is never merely because the REQUESTED year is unconfigured, which is
        what the retired current-year fallback produced every New Year.
        """
        with app.app_context():
            profile = _make_profile(seed_user)
            # Nothing seeded for any year.

            result = load_tax_configs_for_year(
                seed_user["user"].id, profile, date.today().year,
            )

            assert result["bracket_set"] is None
            assert result["state_config"] is None
            assert result["fica_config"] is None

    def test_the_resolution_does_not_move_with_the_calendar(
        self, app, db, seed_user,
    ):
        """The same request resolves the same way whatever year it is asked in.

        The property the whole change exists for: 2027, 2028 and 2029 all
        resolve to 2026's rules, and none of them consults today.  Under the
        retired rule, whichever of those years happened to BE the current year
        resolved to nothing at all.
        """
        with app.app_context():
            profile = _make_profile(seed_user)
            _seed_state_config(seed_user, 2026, Decimal("0.0399"))

            for requested in (2027, 2028, 2029):
                result = load_tax_configs_for_year(
                    seed_user["user"].id, profile, requested,
                )
                assert result["state_config"].tax_year == 2026, requested


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

    def test_an_unconfigured_period_year_resolves_in_its_own_slot(
        self, app, db, seed_user,
    ):
        """A period year with no configs keys its own slot to the resolved year.

        The slot stays keyed by the PERIOD's year -- the caller looks it up by
        ``period.start_date.year`` -- while the configs inside it are the
        latest configured year's.
        """
        with app.app_context():
            profile = _make_profile(seed_user)
            _seed_state_config(seed_user, 2026, Decimal("0.0399"))

            periods = [
                _FakePeriod(date(2026, 6, 1)),
                _FakePeriod(date(2030, 1, 1)),  # no configs for this year
            ]
            result = load_tax_configs_for_periods(
                seed_user["user"].id, profile, periods,
            )

            assert set(result.keys()) == {2026, 2030}
            assert result[2030]["state_config"].flat_rate == Decimal("0.0399")
            assert result[2030]["state_config"].tax_year == 2026

    def test_empty_periods_returns_empty_mapping(self, app, db, seed_user):
        """No periods -> empty mapping."""
        with app.app_context():
            profile = _make_profile(seed_user)
            assert load_tax_configs_for_periods(
                seed_user["user"].id, profile, [],
            ) == {}
