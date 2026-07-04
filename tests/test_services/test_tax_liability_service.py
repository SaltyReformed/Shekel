"""
Shekel Budget App -- Annual Tax Liability Service Tests

Hand-confirmed assertions for ``tax_liability_service.compute_annual_liability``:
the filing-time FEDERAL + NC-STATE annual liability the analytics Taxes tab
builds its refund estimate on (T-P1).  Configs are seeded through the
canonical ``auth_service._seed_tax_data_for_user`` path so the numbers anchor
on the same 2025/2026 DEFAULT_* seeds a registered user receives; the profile
is built inline (the established test_tax_config_service pattern).

Every expected figure is hand-computed in the test docstring, including how
ROUND_HALF_UP resolves the NC half-cent.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.services.auth_service import _seed_tax_data_for_user
from app.services.exceptions import InvalidFilingStatusError
from app.services.tax_liability_service import AnnualLiability, compute_annual_liability


def _make_profile(
    seed_user,
    *,
    name="Liability Test Profile",
    state_code="NC",
    filing_status_name="single",
    additional_income="0.00",
    additional_deductions="0.00",
    qualifying_children=0,
    other_dependents=0,
):
    """Build and flush an active single/NC SalaryProfile for the seeded user.

    Mirrors the ``test_tax_config_service`` profile helper, adding the W-4
    fields the liability service reads (Step 4(a) income and dependent
    counts) plus the Step 4(b) deductions the service must IGNORE.  ``name``
    is a parameter because the ``uq_salary_profiles_user_scenario_name``
    constraint forbids two same-named profiles for one user + scenario.
    """
    filing_status = (
        _db.session.query(FilingStatus).filter_by(name=filing_status_name).one()
    )
    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        name=name,
        annual_salary=Decimal("110000.00"),
        pay_periods_per_year=26,
        filing_status_id=filing_status.id,
        state_code=state_code,
        is_active=True,
        additional_income=Decimal(additional_income),
        additional_deductions=Decimal(additional_deductions),
        qualifying_children=qualifying_children,
        other_dependents=other_dependents,
    )
    _db.session.add(profile)
    _db.session.flush()
    return profile


def _seed_and_profile(seed_user, **profile_kwargs):
    """Seed the DEFAULT_* tax configs for the user and build a profile."""
    _seed_tax_data_for_user(seed_user["user"].id)
    profile = _make_profile(seed_user, **profile_kwargs)
    _db.session.commit()
    return profile


class TestWorkedAnchor:
    """The developer's locked worked anchor (single, NC, 2026 seeds)."""

    def test_anchor_federal_and_state(self, app, db, seed_user):
        """Anchor: wages 110,000; pre-tax 12,000; 4(a) 1,200; 4(b) 3,000.

        Federal (2026 single, std ded 16,100):
          taxable   = 110000 + 1200 - 12000 - 16100 = 83,100.00
          brackets  = 12400*0.10 + (50400-12400)*0.12 + (83100-50400)*0.22
                    = 1240.00 + 4560.00 + 7194.00 = 12,994.00
          credits   = 0  ->  liability = 12,994.00
          (4(b) 3,000 is set on the profile but MUST NOT change this.)
        State (NC 2026, flat 3.99%, std ded 12,750):
          base      = 110000 + 1200 - 12000 = 99,200.00
          taxable   = 99200 - 12750 = 86,450.00
          raw       = 86450 * 0.0399 = 3,449.3550 exactly
          liability = 3,449.36 (ROUND_HALF_UP on the .0050 half-cent)
        """
        profile = _seed_and_profile(
            seed_user,
            additional_income="1200.00",
            additional_deductions="3000.00",
        )
        result = compute_annual_liability(
            seed_user["user"].id, profile, 2026,
            Decimal("110000.00"), Decimal("12000.00"),
        )

        assert isinstance(result, AnnualLiability)
        assert result.tax_year == 2026
        assert result.annual_wage_income == Decimal("110000.00")
        assert result.annual_pretax == Decimal("12000.00")
        assert result.additional_income == Decimal("1200.00")

        # Federal layer.
        assert result.federal.taxable == Decimal("83100.00")
        assert result.federal.liability == Decimal("12994.00")
        assert result.federal.standard_deduction == Decimal("16100.00")
        assert result.federal.child_credit_amount == Decimal("2000.00")
        assert result.federal.other_dependent_credit_amount == Decimal("500.00")
        assert result.federal.qualifying_children == 0
        assert result.federal.other_dependents == 0

        # State layer.
        assert result.state.taxable_base == Decimal("99200.00")
        assert result.state.liability == Decimal("3449.36")
        assert result.state.flat_rate == Decimal("0.0399")
        assert result.state.standard_deduction == Decimal("12750.00")


class TestFourBExclusion:
    """W-4 Step 4(b) additional deductions never change filing liability."""

    def test_4b_does_not_change_liability(self, app, db, seed_user):
        """Two profiles differing ONLY in 4(b) yield identical liability.

        The service never reads ``additional_deductions``, so both resolve to
        the anchor federal liability 12,994.00.
        """
        _seed_tax_data_for_user(seed_user["user"].id)
        no_4b = _make_profile(
            seed_user, name="No 4b", additional_income="1200.00",
        )
        with_4b = _make_profile(
            seed_user,
            name="With 4b",
            additional_income="1200.00",
            additional_deductions="3000.00",
        )
        db.session.commit()

        base = compute_annual_liability(
            seed_user["user"].id, no_4b, 2026,
            Decimal("110000.00"), Decimal("12000.00"),
        )
        alt = compute_annual_liability(
            seed_user["user"].id, with_4b, 2026,
            Decimal("110000.00"), Decimal("12000.00"),
        )
        assert base.federal.liability == alt.federal.liability == Decimal("12994.00")
        assert base.federal.taxable == alt.federal.taxable == Decimal("83100.00")


class TestConfigYearSelection:
    """load_tax_configs_for_year selects the requested year's brackets."""

    def test_2025_vs_2026_differ(self, app, db, seed_user):
        """Same inputs against 2025 vs 2026 seeds produce different results.

        2025 single (std ded 15,000):
          taxable   = 110000 + 1200 - 12000 - 15000 = 84,200.00
          brackets  = 11925*0.10 + (48475-11925)*0.12 + (84200-48475)*0.22
                    = 1192.50 + 4386.00 + 7859.50 = 13,438.00
        State NC 2025 (flat 4.25%, std ded 12,750):
          raw       = (99200 - 12750) * 0.0425 = 86450 * 0.0425 = 3,674.125
          liability = 3,674.13 (ROUND_HALF_UP on the .005 half-cent)
        2026 anchor liability is 12,994.00 / 3,449.36 (see TestWorkedAnchor).
        """
        profile = _seed_and_profile(seed_user, additional_income="1200.00")

        r2026 = compute_annual_liability(
            seed_user["user"].id, profile, 2026,
            Decimal("110000.00"), Decimal("12000.00"),
        )
        r2025 = compute_annual_liability(
            seed_user["user"].id, profile, 2025,
            Decimal("110000.00"), Decimal("12000.00"),
        )

        assert r2025.tax_year == 2025
        assert r2025.federal.standard_deduction == Decimal("15000.00")
        assert r2025.federal.taxable == Decimal("84200.00")
        assert r2025.federal.liability == Decimal("13438.00")
        assert r2025.state.flat_rate == Decimal("0.0425")
        assert r2025.state.liability == Decimal("3674.13")

        # 2026 differs on both layers (std ded 16,100; rate 3.99%).
        assert r2026.federal.standard_deduction == Decimal("16100.00")
        assert r2026.federal.liability == Decimal("12994.00")
        assert r2026.state.liability == Decimal("3449.36")
        assert r2025.federal.liability != r2026.federal.liability


class TestDependentCredits:
    """Dependent counts flow off the profile into the nonrefundable credit."""

    def test_two_children_one_other_dependent(self, app, db, seed_user):
        """2 children + 1 other dependent = 4,500 credit off the anchor tax.

          credit    = 2*2000 + 1*500 = 4,500.00
          liability = 12994.00 - 4500.00 = 8,494.00
        """
        profile = _seed_and_profile(
            seed_user,
            additional_income="1200.00",
            qualifying_children=2,
            other_dependents=1,
        )
        result = compute_annual_liability(
            seed_user["user"].id, profile, 2026,
            Decimal("110000.00"), Decimal("12000.00"),
        )
        assert result.federal.qualifying_children == 2
        assert result.federal.other_dependents == 1
        assert result.federal.liability == Decimal("8494.00")


class TestClampAndMissingConfigs:
    """Zero-clamp, None-state, and missing-bracket-set contracts."""

    def test_low_income_clamps_both_layers_to_zero(self, app, db, seed_user):
        """Income below both standard deductions -> zero federal and state.

        wages 10,000; pre-tax 0; 4(a) 0:
          federal taxable = 10000 - 16100 < 0 -> 0.00 -> liability 0.00
          state base      = 10000 -> (10000 - 12750) < 0 -> liability 0.00
        """
        profile = _seed_and_profile(seed_user)
        result = compute_annual_liability(
            seed_user["user"].id, profile, 2026,
            Decimal("10000.00"), Decimal("0.00"),
        )
        assert result.federal.taxable == Decimal("0")
        assert result.federal.liability == Decimal("0")
        assert result.state.taxable_base == Decimal("10000.00")
        assert result.state.liability == Decimal("0.00")

    def test_none_state_config_zero_state_liability(self, app, db, seed_user):
        """A state with no configured tax yields zero state liability.

        Only NC is seeded, so a PA profile resolves state_config None:
        state liability 0.00, rate/std-ded None; federal is unaffected
        (bracket sets are state-independent -> anchor liability 12,994.00).
        """
        profile = _seed_and_profile(
            seed_user, state_code="PA", additional_income="1200.00",
        )
        result = compute_annual_liability(
            seed_user["user"].id, profile, 2026,
            Decimal("110000.00"), Decimal("12000.00"),
        )
        assert result.federal.liability == Decimal("12994.00")
        assert result.state.liability == Decimal("0")
        assert result.state.flat_rate is None
        assert result.state.standard_deduction is None
        # The base is still reported for context.
        assert result.state.taxable_base == Decimal("99200.00")

    def test_missing_bracket_set_raises(self, app, db, seed_user):
        """No bracket set for the year -> InvalidFilingStatusError.

        Nothing is seeded, so the current year (the fallback year) has no
        bracket set and no fallback applies -- the engine raises, consistent
        with calculate_federal_withholding on a None bracket set.
        """
        profile = _make_profile(seed_user, additional_income="1200.00")
        db.session.commit()
        current_year = date.today().year
        with pytest.raises(InvalidFilingStatusError):
            compute_annual_liability(
                seed_user["user"].id, profile, current_year,
                Decimal("110000.00"), Decimal("12000.00"),
            )
