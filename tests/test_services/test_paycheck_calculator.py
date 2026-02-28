"""
Shekel Budget App — Unit Tests for Paycheck Calculator

Tests the recurring raise compounding logic in
paycheck_calculator._apply_raises() and the full calculate_paycheck()
pipeline including deductions, taxes, 3rd-paycheck detection, inflation,
cumulative wages, and project_salary().
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import pytest

from app.services.paycheck_calculator import (
    _apply_raises,
    _is_third_paycheck,
    _is_first_paycheck_of_month,
    _inflation_years,
    _get_cumulative_wages,
    _calculate_deductions,
    calculate_paycheck,
    project_salary,
    DeductionLine,
    PaycheckBreakdown,
    ZERO,
    TWO_PLACES,
)


# ── Fake Objects ─────────────────────────────────────────────────


class FakeRaiseType:
    def __init__(self, name="merit"):
        self.name = name


class FakeRaise:
    """Minimal stand-in for a SalaryRaise ORM object."""

    def __init__(self, percentage=None, flat_amount=None,
                 effective_month=3, effective_year=2026,
                 is_recurring=False):
        self.percentage = Decimal(str(percentage)) if percentage else None
        self.flat_amount = Decimal(str(flat_amount)) if flat_amount else None
        self.effective_month = effective_month
        self.effective_year = effective_year
        self.is_recurring = is_recurring
        self.raise_type = FakeRaiseType()


class FakePeriod:
    """Minimal stand-in for a PayPeriod ORM object."""

    def __init__(self, start_date, period_id=1):
        self.start_date = start_date
        self.id = period_id


class FakeDeductionTiming:
    def __init__(self, name="pre_tax"):
        self.name = name


class FakeCalcMethod:
    def __init__(self, name="flat"):
        self.name = name


class FakeDeduction:
    """Minimal stand-in for a PaycheckDeduction ORM object."""

    def __init__(self, name="401k", amount="200", deductions_per_year=26,
                 calc_method="flat", deduction_timing="pre_tax",
                 inflation_enabled=False, inflation_rate=None,
                 inflation_effective_month=None, is_active=True):
        self.name = name
        self.amount = Decimal(str(amount))
        self.deductions_per_year = deductions_per_year
        self.calc_method = FakeCalcMethod(calc_method)
        self.deduction_timing = FakeDeductionTiming(deduction_timing)
        self.inflation_enabled = inflation_enabled
        self.inflation_rate = Decimal(str(inflation_rate)) if inflation_rate else None
        self.inflation_effective_month = inflation_effective_month
        self.is_active = is_active


class FakeBracket:
    def __init__(self, min_income, max_income, rate, sort_order):
        self.min_income = min_income
        self.max_income = max_income
        self.rate = rate
        self.sort_order = sort_order


class FakeBracketSet:
    def __init__(self, standard_deduction=Decimal("15000"),
                 child_credit_amount=Decimal("2000"),
                 other_dependent_credit_amount=Decimal("500"),
                 brackets=None):
        self.standard_deduction = standard_deduction
        self.child_credit_amount = child_credit_amount
        self.other_dependent_credit_amount = other_dependent_credit_amount
        self.brackets = brackets or []


class FakeTaxType:
    def __init__(self, name="flat"):
        self.name = name


class FakeStateTaxConfig:
    def __init__(self, flat_rate="0.045", tax_type_name="flat"):
        self.flat_rate = Decimal(str(flat_rate))
        self.tax_type = FakeTaxType(tax_type_name)


class FakeFicaConfig:
    def __init__(self, ss_rate="0.062", ss_wage_base="168600",
                 medicare_rate="0.0145", medicare_surtax_rate="0.009",
                 medicare_surtax_threshold="200000"):
        self.ss_rate = Decimal(str(ss_rate))
        self.ss_wage_base = Decimal(str(ss_wage_base))
        self.medicare_rate = Decimal(str(medicare_rate))
        self.medicare_surtax_rate = Decimal(str(medicare_surtax_rate))
        self.medicare_surtax_threshold = Decimal(str(medicare_surtax_threshold))


class FakeProfile:
    """Minimal stand-in for a SalaryProfile ORM object."""

    def __init__(self, annual_salary, raises=None, deductions=None,
                 pay_periods_per_year=26, created_at=None,
                 additional_income=0, additional_deductions=0,
                 extra_withholding=0, qualifying_children=0,
                 other_dependents=0):
        self.annual_salary = Decimal(str(annual_salary))
        self.raises = raises or []
        self.deductions = deductions or []
        self.pay_periods_per_year = pay_periods_per_year
        self.created_at = created_at
        self.additional_income = Decimal(str(additional_income))
        self.additional_deductions = Decimal(str(additional_deductions))
        self.extra_withholding = Decimal(str(extra_withholding))
        self.qualifying_children = qualifying_children
        self.other_dependents = other_dependents


# ── Pytest Fixtures ──────────────────────────────────────────────


@pytest.fixture
def simple_bracket_set():
    """2-bracket progressive set: 10% up to $50k, 22% above."""
    return FakeBracketSet(
        standard_deduction=Decimal("15000"),
        brackets=[
            FakeBracket(Decimal("0"), Decimal("50000"), Decimal("0.10"), 0),
            FakeBracket(Decimal("50000"), None, Decimal("0.22"), 1),
        ],
    )


@pytest.fixture
def nc_state_config():
    """NC flat rate 4.5%."""
    return FakeStateTaxConfig(flat_rate="0.045")


@pytest.fixture
def standard_fica():
    """Standard 2026 FICA rates."""
    return FakeFicaConfig()


@pytest.fixture
def simple_tax_configs(simple_bracket_set, nc_state_config, standard_fica):
    """Combined tax config dict."""
    return {
        "bracket_set": simple_bracket_set,
        "state_config": nc_state_config,
        "fica_config": standard_fica,
    }


@pytest.fixture
def base_profile():
    """$60k salary, 26 periods, no raises/deductions."""
    return FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))


@pytest.fixture
def biweekly_periods():
    """26 FakePeriod objects for 2026, starting Jan 2."""
    start = date(2026, 1, 2)
    periods = []
    for i in range(26):
        d = date.fromordinal(start.toordinal() + i * 14)
        periods.append(FakePeriod(start_date=d, period_id=i + 1))
    return periods


# ── Existing Tests: Recurring Raise Compounding ──────────────────


class TestRecurringRaiseCompounding:
    """Verify that recurring raises compound correctly across years."""

    def test_recurring_raise_not_yet_effective(self):
        """Before effective month in effective year, raise should not apply."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = FakePeriod(start_date=date(2026, 2, 1))
        result = _apply_raises(profile, period)
        assert result == Decimal("100000.00")

    def test_recurring_raise_first_year_at_effective_month(self):
        """In effective year at effective month, raise should apply once."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = FakePeriod(start_date=date(2026, 3, 1))
        result = _apply_raises(profile, period)
        # 100000 * 1.03 = 103000
        assert result == Decimal("103000.00")

    def test_recurring_raise_first_year_after_effective_month(self):
        """Later in effective year, raise should still apply once."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = FakePeriod(start_date=date(2026, 6, 1))
        result = _apply_raises(profile, period)
        assert result == Decimal("103000.00")

    def test_recurring_raise_second_year_before_month(self):
        """Next year before effective month: still only 1 application."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = FakePeriod(start_date=date(2027, 1, 1))
        result = _apply_raises(profile, period)
        # Only 1 full year passed (2027 - 2026 = 1), but month not reached
        assert result == Decimal("103000.00")

    def test_recurring_raise_second_year_after_month(self):
        """Next year after effective month: 2 total applications (compounded)."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = FakePeriod(start_date=date(2027, 4, 1))
        result = _apply_raises(profile, period)
        # 100000 * 1.03 * 1.03 = 106090
        assert result == Decimal("106090.00")

    def test_recurring_raise_third_year(self):
        """Two years later after effective month: 3 total applications."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = FakePeriod(start_date=date(2028, 6, 1))
        result = _apply_raises(profile, period)
        # 100000 * 1.03^3 = 109272.70
        expected = (Decimal("100000") * Decimal("1.03") ** 3).quantize(Decimal("0.01"))
        assert result == expected

    def test_one_time_raise_applies_once(self):
        """A non-recurring raise should only apply once."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.05", effective_month=1,
                              effective_year=2026, is_recurring=False)],
        )
        # Check in 2027 — still just one application.
        period = FakePeriod(start_date=date(2027, 6, 1))
        result = _apply_raises(profile, period)
        assert result == Decimal("105000.00")

    def test_recurring_flat_raise(self):
        """Recurring flat raise should add the flat amount each year."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(flat_amount="5000", effective_month=1,
                              effective_year=2026, is_recurring=True)],
        )
        period = FakePeriod(start_date=date(2028, 6, 1))
        result = _apply_raises(profile, period)
        # 3 applications: 100000 + 5000 + 5000 + 5000 = 115000
        assert result == Decimal("115000.00")

    def test_recurring_raise_second_year_at_effective_month(self):
        """March 2026 raise checked in March 2027 = 2 applications.

        Validates the `+1` in the application count formula:
          years_passed = 2027 - 2026 = 1
          period_month (3) >= eff_month (3) → applications = 1 + 1 = 2
        This is correct because the raise applied in March 2026 AND
        recurs again in March 2027.
        """
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = FakePeriod(start_date=date(2027, 3, 1))
        result = _apply_raises(profile, period)
        # 100000 * 1.03^2 = 106090
        assert result == Decimal("106090.00")

    def test_recurring_raise_no_effective_year(self):
        """Recurring raise with no effective_year applies once if month reached."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=None, is_recurring=True)],
        )
        period = FakePeriod(start_date=date(2027, 4, 1))
        result = _apply_raises(profile, period)
        assert result == Decimal("103000.00")


# ── New Tests ────────────────────────────────────────────────────


class TestPaycheckBreakdownProperties:
    """Verify computed properties on the PaycheckBreakdown dataclass."""

    def test_total_pre_tax_sums_deductions(self):
        breakdown = PaycheckBreakdown(
            period_id=1,
            annual_salary=Decimal("60000"),
            gross_biweekly=Decimal("2307.69"),
            pre_tax_deductions=[
                DeductionLine("401k", Decimal("200.00")),
                DeductionLine("HSA", Decimal("50.00")),
            ],
        )
        assert breakdown.total_pre_tax == Decimal("250.00")

    def test_total_post_tax_sums_deductions(self):
        breakdown = PaycheckBreakdown(
            period_id=1,
            annual_salary=Decimal("60000"),
            gross_biweekly=Decimal("2307.69"),
            post_tax_deductions=[
                DeductionLine("Roth IRA", Decimal("100.00")),
                DeductionLine("Life Ins", Decimal("25.00")),
            ],
        )
        assert breakdown.total_post_tax == Decimal("125.00")

    def test_total_taxes_sums_all_tax_fields(self):
        breakdown = PaycheckBreakdown(
            period_id=1,
            annual_salary=Decimal("60000"),
            gross_biweekly=Decimal("2307.69"),
            federal_tax=Decimal("200.00"),
            state_tax=Decimal("80.00"),
            social_security=Decimal("143.08"),
            medicare=Decimal("33.46"),
        )
        assert breakdown.total_taxes == Decimal("456.54")

    def test_empty_deductions_return_zero(self):
        breakdown = PaycheckBreakdown(
            period_id=1,
            annual_salary=Decimal("60000"),
            gross_biweekly=Decimal("2307.69"),
        )
        assert breakdown.total_pre_tax == Decimal("0")
        assert breakdown.total_post_tax == Decimal("0")
        assert breakdown.total_taxes == Decimal("0")


class TestCalculatePaycheckPipeline:
    """End-to-end tests for calculate_paycheck()."""

    def test_basic_paycheck_no_deductions(self, base_profile, simple_tax_configs):
        """Gross, taxes, and net verified end-to-end."""
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)
        all_periods = [period]

        result = calculate_paycheck(base_profile, period, all_periods,
                                    simple_tax_configs)

        assert result.annual_salary == Decimal("60000.00")
        # 60000 / 26
        assert result.gross_biweekly == Decimal("2307.69")
        assert result.federal_tax > ZERO
        assert result.state_tax > ZERO
        assert result.social_security > ZERO
        assert result.medicare > ZERO
        assert result.net_pay > ZERO
        assert result.net_pay < result.gross_biweekly

    def test_net_pay_formula(self, base_profile, simple_tax_configs):
        """net = gross - pre_tax - fed - state - ss - medicare - post_tax."""
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)
        all_periods = [period]

        r = calculate_paycheck(base_profile, period, all_periods,
                               simple_tax_configs)

        expected_net = (
            r.gross_biweekly
            - r.total_pre_tax
            - r.federal_tax
            - r.state_tax
            - r.social_security
            - r.medicare
            - r.total_post_tax
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        assert r.net_pay == expected_net

    def test_gross_biweekly_calculation(self, simple_tax_configs):
        """annual / 26, quantized to 2 places."""
        profile = FakeProfile(annual_salary=75000, created_at=date(2026, 1, 1))
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(profile, period, [period], simple_tax_configs)

        expected_gross = (Decimal("75000") / 26).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        assert result.gross_biweekly == expected_gross

    def test_taxable_income_floors_at_zero(self, simple_tax_configs):
        """When pre_tax deductions > gross, taxable income should be 0."""
        profile = FakeProfile(
            annual_salary=60000,
            deductions=[
                FakeDeduction(name="Mega401k", amount="3000",
                              deduction_timing="pre_tax"),
            ],
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(profile, period, [period], simple_tax_configs)

        assert result.taxable_income == ZERO

    def test_no_bracket_set_zero_federal(self, nc_state_config, standard_fica):
        """bracket_set=None → federal=0."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)
        configs = {
            "bracket_set": None,
            "state_config": nc_state_config,
            "fica_config": standard_fica,
        }

        result = calculate_paycheck(profile, period, [period], configs)

        assert result.federal_tax == ZERO

    def test_no_state_config_zero_state(self, simple_bracket_set, standard_fica):
        """state_config=None → state=0."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)
        configs = {
            "bracket_set": simple_bracket_set,
            "state_config": None,
            "fica_config": standard_fica,
        }

        result = calculate_paycheck(profile, period, [period], configs)

        assert result.state_tax == ZERO

    def test_no_fica_config_zero_fica(self, simple_bracket_set, nc_state_config):
        """fica_config=None → ss=0, medicare=0."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)
        configs = {
            "bracket_set": simple_bracket_set,
            "state_config": nc_state_config,
            "fica_config": None,
        }

        result = calculate_paycheck(profile, period, [period], configs)

        assert result.social_security == ZERO
        assert result.medicare == ZERO

    def test_all_tax_configs_none(self):
        """All None → only gross minus deductions."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)
        configs = {
            "bracket_set": None,
            "state_config": None,
            "fica_config": None,
        }

        result = calculate_paycheck(profile, period, [period], configs)

        assert result.federal_tax == ZERO
        assert result.state_tax == ZERO
        assert result.social_security == ZERO
        assert result.medicare == ZERO
        assert result.net_pay == result.gross_biweekly

    def test_w4_fields_passed_to_federal(self, simple_tax_configs):
        """additional_income and extra_withholding affect the result."""
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        base = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        base_result = calculate_paycheck(base, period, [period],
                                         simple_tax_configs)

        with_w4 = FakeProfile(
            annual_salary=60000, created_at=date(2026, 1, 1),
            additional_income=10000, extra_withholding=50,
        )
        w4_result = calculate_paycheck(with_w4, period, [period],
                                       simple_tax_configs)

        assert w4_result.federal_tax > base_result.federal_tax


class TestDeductionCalculation:
    """Tests for _calculate_deductions and deduction behavior in pipeline."""

    def test_flat_pre_tax_deduction(self, base_profile, simple_tax_configs):
        """Flat amount subtracted before taxes."""
        base_profile.deductions = [
            FakeDeduction(name="401k", amount="200", deduction_timing="pre_tax"),
        ]
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(base_profile, period, [period],
                                    simple_tax_configs)

        assert len(result.pre_tax_deductions) == 1
        assert result.pre_tax_deductions[0].name == "401k"
        assert result.pre_tax_deductions[0].amount == Decimal("200.00")

    def test_flat_post_tax_deduction(self, base_profile, simple_tax_configs):
        """Flat amount subtracted after taxes."""
        base_profile.deductions = [
            FakeDeduction(name="Roth", amount="150", deduction_timing="post_tax"),
        ]
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(base_profile, period, [period],
                                    simple_tax_configs)

        assert len(result.post_tax_deductions) == 1
        assert result.post_tax_deductions[0].amount == Decimal("150.00")

    def test_percentage_deduction(self, base_profile, simple_tax_configs):
        """Percentage of gross_biweekly."""
        base_profile.deductions = [
            FakeDeduction(name="401k", amount="0.06", calc_method="percentage",
                          deduction_timing="pre_tax"),
        ]
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(base_profile, period, [period],
                                    simple_tax_configs)

        gross = Decimal("60000") / 26
        expected = (gross * Decimal("0.06")).quantize(TWO_PLACES,
                                                     rounding=ROUND_HALF_UP)
        assert result.pre_tax_deductions[0].amount == expected

    def test_inactive_deduction_skipped(self, base_profile, simple_tax_configs):
        """is_active=False excluded."""
        base_profile.deductions = [
            FakeDeduction(name="Old Plan", amount="200", is_active=False),
        ]
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(base_profile, period, [period],
                                    simple_tax_configs)

        assert len(result.pre_tax_deductions) == 0

    def test_timing_filter(self, base_profile, simple_tax_configs):
        """Pre-tax deduction not in post-tax list and vice versa."""
        base_profile.deductions = [
            FakeDeduction(name="401k", amount="200", deduction_timing="pre_tax"),
            FakeDeduction(name="Roth", amount="100", deduction_timing="post_tax"),
        ]
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(base_profile, period, [period],
                                    simple_tax_configs)

        pre_names = [d.name for d in result.pre_tax_deductions]
        post_names = [d.name for d in result.post_tax_deductions]
        assert "401k" in pre_names
        assert "401k" not in post_names
        assert "Roth" in post_names
        assert "Roth" not in pre_names

    def test_24_per_year_skipped_on_third_paycheck(self):
        """deductions_per_year=24 + is_third → skipped."""
        profile = FakeProfile(
            annual_salary=60000, created_at=date(2026, 1, 1),
            deductions=[
                FakeDeduction(name="Health", amount="100",
                              deductions_per_year=24),
            ],
        )
        # Build a month with 3 paychecks
        p1 = FakePeriod(start_date=date(2026, 1, 2), period_id=1)
        p2 = FakePeriod(start_date=date(2026, 1, 16), period_id=2)
        p3 = FakePeriod(start_date=date(2026, 1, 30), period_id=3)
        all_periods = [p1, p2, p3]

        gross = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                 rounding=ROUND_HALF_UP)
        result = _calculate_deductions(profile, p3, all_periods, gross,
                                       "pre_tax", True)
        assert len(result) == 0

    def test_12_per_year_only_first_of_month(self):
        """deductions_per_year=12 applied on first paycheck of month."""
        profile = FakeProfile(
            annual_salary=60000, created_at=date(2026, 1, 1),
            deductions=[
                FakeDeduction(name="Life", amount="50",
                              deductions_per_year=12),
            ],
        )
        p1 = FakePeriod(start_date=date(2026, 2, 13), period_id=4)
        p2 = FakePeriod(start_date=date(2026, 2, 27), period_id=5)
        all_periods = [p1, p2]

        gross = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                 rounding=ROUND_HALF_UP)
        result = _calculate_deductions(profile, p1, all_periods, gross,
                                       "pre_tax", False)
        assert len(result) == 1
        assert result[0].amount == Decimal("50")

    def test_12_per_year_skipped_non_first(self):
        """deductions_per_year=12 skipped on second paycheck of month."""
        profile = FakeProfile(
            annual_salary=60000, created_at=date(2026, 1, 1),
            deductions=[
                FakeDeduction(name="Life", amount="50",
                              deductions_per_year=12),
            ],
        )
        p1 = FakePeriod(start_date=date(2026, 2, 13), period_id=4)
        p2 = FakePeriod(start_date=date(2026, 2, 27), period_id=5)
        all_periods = [p1, p2]

        gross = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                 rounding=ROUND_HALF_UP)
        result = _calculate_deductions(profile, p2, all_periods, gross,
                                       "pre_tax", False)
        assert len(result) == 0


class TestThirdPaycheckDetection:
    """Tests for _is_third_paycheck()."""

    def test_month_with_two_paychecks_returns_false(self):
        """Standard month with 2 paychecks → False."""
        p1 = FakePeriod(start_date=date(2026, 2, 13), period_id=1)
        p2 = FakePeriod(start_date=date(2026, 2, 27), period_id=2)
        all_periods = [p1, p2]

        assert _is_third_paycheck(p2, all_periods) is False

    def test_third_paycheck_returns_true(self):
        """Month with 3 start dates → 3rd is True."""
        p1 = FakePeriod(start_date=date(2026, 1, 2), period_id=1)
        p2 = FakePeriod(start_date=date(2026, 1, 16), period_id=2)
        p3 = FakePeriod(start_date=date(2026, 1, 30), period_id=3)
        all_periods = [p1, p2, p3]

        assert _is_third_paycheck(p3, all_periods) is True

    def test_first_period_of_month_returns_false(self):
        """First period in a 3-paycheck month is not a 3rd paycheck."""
        p1 = FakePeriod(start_date=date(2026, 1, 2), period_id=1)
        p2 = FakePeriod(start_date=date(2026, 1, 16), period_id=2)
        p3 = FakePeriod(start_date=date(2026, 1, 30), period_id=3)
        all_periods = [p1, p2, p3]

        assert _is_third_paycheck(p1, all_periods) is False


class TestFirstPaycheckOfMonth:
    """Tests for _is_first_paycheck_of_month()."""

    def test_first_period_in_month_returns_true(self):
        p1 = FakePeriod(start_date=date(2026, 3, 6), period_id=1)
        p2 = FakePeriod(start_date=date(2026, 3, 20), period_id=2)
        all_periods = [p1, p2]

        assert _is_first_paycheck_of_month(p1, all_periods) is True

    def test_second_period_in_month_returns_false(self):
        p1 = FakePeriod(start_date=date(2026, 3, 6), period_id=1)
        p2 = FakePeriod(start_date=date(2026, 3, 20), period_id=2)
        all_periods = [p1, p2]

        assert _is_first_paycheck_of_month(p2, all_periods) is False


class TestInflationAdjustment:
    """Tests for _inflation_years() and inflation in deductions."""

    def test_one_year_inflation(self):
        """amount * (1 + rate)^1."""
        profile = FakeProfile(
            annual_salary=60000, created_at=date(2025, 1, 1),
            deductions=[
                FakeDeduction(name="Health", amount="100",
                              inflation_enabled=True, inflation_rate="0.03",
                              inflation_effective_month=1),
            ],
        )
        period = FakePeriod(start_date=date(2026, 6, 1), period_id=1)

        years = _inflation_years(period, profile, 1)
        assert years == 1

        # Verify in deduction calculation
        gross = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                 rounding=ROUND_HALF_UP)
        result = _calculate_deductions(profile, period, [period], gross,
                                       "pre_tax", False)
        expected = (Decimal("100") * Decimal("1.03")).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        assert result[0].amount == expected

    def test_two_years_compound_inflation(self):
        """amount * (1 + rate)^2."""
        profile = FakeProfile(
            annual_salary=60000, created_at=date(2024, 1, 1),
            deductions=[
                FakeDeduction(name="Health", amount="100",
                              inflation_enabled=True, inflation_rate="0.03",
                              inflation_effective_month=1),
            ],
        )
        period = FakePeriod(start_date=date(2026, 6, 1), period_id=1)

        years = _inflation_years(period, profile, 1)
        assert years == 2

        gross = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                 rounding=ROUND_HALF_UP)
        result = _calculate_deductions(profile, period, [period], gross,
                                       "pre_tax", False)
        expected = (Decimal("100") * Decimal("1.03") ** 2).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        assert result[0].amount == expected

    def test_before_effective_month_reduces_years(self):
        """period_month < eff_month → years - 1."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2024, 1, 1))
        # Period is in March, effective month is June
        period = FakePeriod(start_date=date(2026, 3, 1), period_id=1)

        years = _inflation_years(period, profile, 6)
        # 2026 - 2024 = 2, but month 3 < 6 → 2 - 1 = 1
        assert years == 1

    def test_created_at_none_zero_years(self):
        """profile.created_at=None → no inflation."""
        profile = FakeProfile(annual_salary=60000, created_at=None)
        period = FakePeriod(start_date=date(2026, 6, 1), period_id=1)

        years = _inflation_years(period, profile, 1)
        assert years == 0

    def test_same_year_as_creation_zero_years(self):
        """Year 0 = no inflation."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        period = FakePeriod(start_date=date(2026, 6, 1), period_id=1)

        years = _inflation_years(period, profile, 1)
        assert years == 0


class TestCumulativeWages:
    """Tests for _get_cumulative_wages()."""

    def test_sums_prior_periods_in_same_year(self):
        """Adds gross for earlier periods."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        p1 = FakePeriod(start_date=date(2026, 1, 2), period_id=1)
        p2 = FakePeriod(start_date=date(2026, 1, 16), period_id=2)
        p3 = FakePeriod(start_date=date(2026, 1, 30), period_id=3)
        all_periods = [p1, p2, p3]

        result = _get_cumulative_wages(profile, p3, all_periods)

        gross_per = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                     rounding=ROUND_HALF_UP)
        assert result == gross_per * 2

    def test_first_period_zero_cumulative(self):
        """First period → 0."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        p1 = FakePeriod(start_date=date(2026, 1, 2), period_id=1)

        result = _get_cumulative_wages(profile, p1, [p1])
        assert result == ZERO

    def test_different_year_periods_excluded(self):
        """Prior year periods skipped."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2025, 1, 1))
        p_prev = FakePeriod(start_date=date(2025, 12, 19), period_id=25)
        p1 = FakePeriod(start_date=date(2026, 1, 2), period_id=1)
        all_periods = [p_prev, p1]

        result = _get_cumulative_wages(profile, p1, all_periods)
        assert result == ZERO


class TestProjectSalary:
    """Tests for project_salary()."""

    def test_returns_one_breakdown_per_period(self, base_profile,
                                              simple_tax_configs):
        """len(result) == len(periods)."""
        periods = [
            FakePeriod(start_date=date(2026, 1, 2), period_id=1),
            FakePeriod(start_date=date(2026, 1, 16), period_id=2),
            FakePeriod(start_date=date(2026, 1, 30), period_id=3),
        ]

        result = project_salary(base_profile, periods, simple_tax_configs)

        assert len(result) == 3
        assert all(isinstance(r, PaycheckBreakdown) for r in result)

    def test_raise_event_appears_in_correct_period(self, simple_tax_configs):
        """raise_event populated at raise month."""
        profile = FakeProfile(
            annual_salary=60000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=False)],
            created_at=date(2026, 1, 1),
        )
        periods = [
            FakePeriod(start_date=date(2026, 2, 13), period_id=1),
            FakePeriod(start_date=date(2026, 3, 13), period_id=2),
            FakePeriod(start_date=date(2026, 4, 10), period_id=3),
        ]

        result = project_salary(profile, periods, simple_tax_configs)

        assert result[0].raise_event == ""
        assert "MERIT" in result[1].raise_event
        assert result[2].raise_event == ""

    def test_empty_periods_empty_result(self, base_profile, simple_tax_configs):
        """[] → []."""
        result = project_salary(base_profile, [], simple_tax_configs)
        assert result == []
