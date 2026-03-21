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

from app.services.exceptions import InvalidGrossPayError
from app.services.tax_calculator import calculate_fica
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
        """Recurring raise with no effective_year applies once if month reached.

        This is a legacy edge case — the UI now requires effective_year for
        all raises.  Without an effective_year the function cannot count
        compounding applications, so it falls back to a single application.
        """
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

    def test_basic_paycheck_no_deductions(
        self, base_profile, simple_tax_configs
    ):
        """Full pipeline: $60k salary, no deductions, all exact.

        Pipeline trace:
          gross = 60000/26 = $2,307.69
          federal: annual 59999.94 - 15k std ded = 44999.94
            10% bracket: 44999.94*0.10 = 4499.994->4499.99
            per-period: 4499.99/26 = 173.08
          state: 59999.94*0.045 = 2700.00, /26 = 103.85
          SS: 2307.69*0.062 = 143.08
          Medicare: 2307.69*0.0145 = 33.46
          net: 2307.69 - 173.08 - 103.85 - 143.08 - 33.46
             = 1854.22
        """
        period = FakePeriod(
            start_date=date(2026, 1, 16), period_id=1
        )
        all_periods = [period]

        result = calculate_paycheck(
            base_profile, period, all_periods,
            simple_tax_configs
        )

        assert result.annual_salary == Decimal("60000.00"), (
            f"annual_salary: expected 60000.00, "
            f"got {result.annual_salary}"
        )
        # 60000 / 26 = 2307.692307... -> 2307.69
        assert result.gross_biweekly == Decimal("2307.69"), (
            f"gross_biweekly: expected 2307.69, "
            f"got {result.gross_biweekly}"
        )
        # Pub 15-T: annual=59999.94, taxable=44999.94
        # 44999.94*0.10=4499.994->4499.99, /26=173.08
        assert result.federal_tax == Decimal("173.08"), (
            f"federal_tax: expected 173.08, "
            f"got {result.federal_tax}"
        )
        # state: 59999.94*0.045=2699.9973->2700.00
        # 2700.00/26=103.846...->103.85
        assert result.state_tax == Decimal("103.85"), (
            f"state_tax: expected 103.85, "
            f"got {result.state_tax}"
        )
        # SS: 2307.69*0.062=143.07678->143.08
        assert result.social_security == Decimal("143.08"), (
            f"social_security: expected 143.08, "
            f"got {result.social_security}"
        )
        # Medicare: 2307.69*0.0145=33.461505->33.46
        assert result.medicare == Decimal("33.46"), (
            f"medicare: expected 33.46, "
            f"got {result.medicare}"
        )
        # net = 2307.69 - 173.08 - 103.85 - 143.08 - 33.46
        assert result.net_pay == Decimal("1854.22"), (
            f"net_pay: expected 1854.22, "
            f"got {result.net_pay}"
        )

    def test_net_pay_formula(self, base_profile, simple_tax_configs):
        """net = gross - pre_tax - fed - state - ss - medicare - post_tax."""
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)
        all_periods = [period]

        r = calculate_paycheck(base_profile, period, all_periods,
                               simple_tax_configs)

        # Hardcoded correctness anchor: base_profile=$60k salary,
        # same setup as test_basic_paycheck_no_deductions.
        # gross=2307.69 - fed=173.08 - state=103.85
        #   - SS=143.08 - med=33.46 = 1854.22
        assert r.net_pay == Decimal("1854.22"), (
            f"net_pay: expected 1854.22, got {r.net_pay}"
        )

        # Secondary: internal consistency check (formula holds).
        expected_net = (
            r.gross_biweekly
            - r.total_pre_tax
            - r.federal_tax
            - r.state_tax
            - r.social_security
            - r.medicare
            - r.total_post_tax
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        assert r.net_pay == expected_net, (
            f"Consistency check: net_pay={r.net_pay}, "
            f"formula result={expected_net}"
        )

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
        """W-4 fields increase federal withholding exactly.

        Base: $60k, no W-4.
          annual=59999.94, taxable=44999.94
          tax=4499.99, /26=173.08
        W-4: additional_income=10000, extra_withholding=50.
          annual=69999.94, taxable=54999.94
          50000*0.10+4999.94*0.22=6099.99
          (6099.99/26)+50=284.615->284.62
        """
        period = FakePeriod(
            start_date=date(2026, 1, 16), period_id=1
        )

        base = FakeProfile(
            annual_salary=60000,
            created_at=date(2026, 1, 1),
        )
        base_result = calculate_paycheck(
            base, period, [period], simple_tax_configs
        )

        with_w4 = FakeProfile(
            annual_salary=60000,
            created_at=date(2026, 1, 1),
            additional_income=10000,
            extra_withholding=50,
        )
        w4_result = calculate_paycheck(
            with_w4, period, [period], simple_tax_configs
        )

        # Base: 4499.99/26=173.076923->173.08
        assert base_result.federal_tax == Decimal("173.08"), (
            f"base federal_tax: expected 173.08, "
            f"got {base_result.federal_tax}"
        )
        # W-4: (6099.99/26)+50=234.615+50=284.615->284.62
        assert w4_result.federal_tax == Decimal("284.62"), (
            f"w4 federal_tax: expected 284.62, "
            f"got {w4_result.federal_tax}"
        )


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


# ── FICA Wage Cap Tests ─────────────────────────────────────────


class TestFICAWageCapBoundary:
    """Tests for FICA Social Security wage cap boundary."""

    def test_fica_ss_wage_cap_boundary(
        self, biweekly_periods, simple_tax_configs
    ):
        """SS tax transitions across 26 periods for $200k salary.

        gross = 200000/26 = $7,692.31
        SS cap = $168,600, rate = 6.2%
        Full SS = 7692.31*0.062 = $476.92
        Transition at period 22:
          cumulative = 21*7692.31 = $161,538.51
          ss_taxable = 168600 - 161538.51 = $7,061.49
          ss = 7061.49*0.062 = $437.81
        Periods 23-26: cumulative >= $168,600, SS = $0.00
        Total SS = 21*476.92 + 437.81 = $10,453.13

        Medicare surtax note: at period 26, cumul+gross =
        $200,000.06 triggers surtax condition but
        (0.06*0.009) rounds to $0.00, so Medicare stays
        $111.54 for all 26 periods.
        """
        profile = FakeProfile(
            annual_salary=200000,
            created_at=date(2026, 1, 1),
        )
        results = project_salary(
            profile, biweekly_periods, simple_tax_configs
        )

        # gross = 200000/26 = 7692.307692->7692.31
        # full SS = 7692.31*0.062 = 476.92322->476.92
        full_ss = Decimal("476.92")
        # partial at period 22:
        # cumul=161538.51, taxable=7061.49
        # 7061.49*0.062 = 437.81238->437.81
        partial_ss = Decimal("437.81")
        # Medicare = 7692.31*0.0145 = 111.538495->111.54
        expected_medicare = Decimal("111.54")

        assert len(results) == 26, (
            f"expected 26 results, got {len(results)}"
        )

        # Periods 1-21: full SS (under cap)
        for i in range(21):
            assert results[i].social_security == full_ss, (
                f"period {i+1}: SS expected {full_ss}, "
                f"got {results[i].social_security}"
            )

        # Period 22: partial SS (crosses cap this period)
        # cumulative = 21*7692.31 = 161538.51
        # cumul + gross = 169230.82 > 168600
        assert results[21].social_security == partial_ss, (
            f"period 22 (transition): SS expected "
            f"{partial_ss}, got {results[21].social_security}"
        )

        # Periods 23-26: zero SS (already over cap)
        for i in range(22, 26):
            assert results[i].social_security == Decimal("0.00"), (
                f"period {i+1}: SS expected 0.00, "
                f"got {results[i].social_security}"
            )

        # Medicare: constant across all 26 periods (no cap)
        for i in range(26):
            assert results[i].medicare == expected_medicare, (
                f"period {i+1}: medicare expected "
                f"{expected_medicare}, "
                f"got {results[i].medicare}"
            )

        # Cumulative SS verification
        # 21*476.92 + 437.81 + 4*0.00 = 10453.13
        total_ss = sum(
            r.social_security for r in results
        )
        assert total_ss == Decimal("10453.13"), (
            f"total SS: expected 10453.13, got {total_ss}"
        )


# ── FICA Direct Boundary Tests ─────────────────────────────────


class TestFICADirectBoundary:
    """Direct unit tests of calculate_fica at exact SS wage cap.

    Tests all three SS branches in tax_calculator.calculate_fica():
      1. cumulative >= ss_wage_base  -> SS = 0
      2. cumulative + gross > ss_wage_base  -> partial SS
      3. cumulative + gross <= ss_wage_base  -> full SS
    """

    @pytest.fixture
    def fica_config(self):
        """Standard FICA config with ss_wage_base=168600."""
        return FakeFicaConfig()

    def test_ss_at_cap_zero(self, fica_config):
        """cumulative == ss_wage_base exactly: SS = 0.00.

        Branch 1: cumulative(168600) >= ss_wage_base(168600).
        """
        result = calculate_fica(
            Decimal("1000.00"), fica_config,
            cumulative_wages=Decimal("168600"),
        )
        assert result["ss"] == Decimal("0.00"), (
            f"SS at cap: expected 0.00, "
            f"got {result['ss']}"
        )

    def test_ss_above_cap_zero(self, fica_config):
        """cumulative > ss_wage_base: SS = 0.00.

        Branch 1: cumulative(170000) >= ss_wage_base(168600).
        """
        result = calculate_fica(
            Decimal("1000.00"), fica_config,
            cumulative_wages=Decimal("170000"),
        )
        assert result["ss"] == Decimal("0.00"), (
            f"SS above cap: expected 0.00, "
            f"got {result['ss']}"
        )

    def test_ss_partial_one_dollar_under(self, fica_config):
        """cumulative = 168599, gross = 100: partial SS.

        Branch 2: cumulative(168599) + gross(100) = 168699
        > ss_wage_base(168600).
        ss_taxable = 168600 - 168599 = 1.00
        SS = 1.00 * 0.062 = 0.062 -> 0.06
        """
        result = calculate_fica(
            Decimal("100.00"), fica_config,
            cumulative_wages=Decimal("168599"),
        )
        assert result["ss"] == Decimal("0.06"), (
            f"SS partial ($1 under cap): expected 0.06, "
            f"got {result['ss']}"
        )

    def test_ss_full_well_under_cap(self, fica_config):
        """cumulative = 0, gross = 1000: full SS.

        Branch 3: cumulative(0) + gross(1000) = 1000
        <= ss_wage_base(168600).
        SS = 1000 * 0.062 = 62.00
        """
        result = calculate_fica(
            Decimal("1000.00"), fica_config,
            cumulative_wages=Decimal("0"),
        )
        assert result["ss"] == Decimal("62.00"), (
            f"SS full: expected 62.00, "
            f"got {result['ss']}"
        )

    def test_ss_partial_straddle(self, fica_config):
        """cumulative = 168000, gross = 1000: partial SS.

        Branch 2: cumulative(168000) + gross(1000) = 169000
        > ss_wage_base(168600).
        ss_taxable = 168600 - 168000 = 600
        SS = 600 * 0.062 = 37.20
        """
        result = calculate_fica(
            Decimal("1000.00"), fica_config,
            cumulative_wages=Decimal("168000"),
        )
        assert result["ss"] == Decimal("37.20"), (
            f"SS partial (straddle): expected 37.20, "
            f"got {result['ss']}"
        )


# ── Medicare Surtax Tests ───────────────────────────────────────


class TestMedicareSurtax:
    """Tests for Medicare surtax at high income levels."""

    def test_medicare_surtax_high_income(
        self, biweekly_periods, simple_tax_configs
    ):
        """Medicare surtax across 26 periods for $300k salary.

        gross = 300000/26 = $11,538.46
        base Medicare = 11538.46*0.0145 = $167.31
        surtax threshold = $200,000, rate = 0.9%

        Transition at period 18:
          cumulative = 17*11538.46 = $196,153.82
          cumul+gross = $207,692.28 > $200,000
          surtax_income = 207692.28 - 200000 = $7,692.28
          surtax = 7692.28*0.009 = $69.23
          medicare = 167.31 + 69.23 = $236.54

        Periods 19-26: cumulative >= $200,000
          surtax = 11538.46*0.009 = $103.85
          medicare = 167.31 + 103.85 = $271.16
        """
        profile = FakeProfile(
            annual_salary=300000,
            created_at=date(2026, 1, 1),
        )
        results = project_salary(
            profile, biweekly_periods, simple_tax_configs
        )

        # base Medicare = 11538.46*0.0145 = 167.30767->167.31
        base_med = Decimal("167.31")
        # transition: 167.31 + 69.23 = 236.54
        trans_med = Decimal("236.54")
        # full surtax: 167.31 + 103.85 = 271.16
        full_surtax_med = Decimal("271.16")

        assert len(results) == 26, (
            f"expected 26 results, got {len(results)}"
        )

        # Periods 1-17: base Medicare only (under threshold)
        for i in range(17):
            assert results[i].medicare == base_med, (
                f"period {i+1}: medicare expected "
                f"{base_med}, got {results[i].medicare}"
            )

        # Period 18: partial surtax (crosses threshold)
        # cumul = 17*11538.46 = 196153.82
        # surtax_income = 207692.28 - 200000 = 7692.28
        # surtax = 7692.28*0.009 = 69.23052->69.23
        assert results[17].medicare == trans_med, (
            f"period 18 (transition): medicare expected "
            f"{trans_med}, got {results[17].medicare}"
        )

        # Periods 19-26: full surtax (cumul >= threshold)
        # surtax = 11538.46*0.009 = 103.84614->103.85
        for i in range(18, 26):
            assert results[i].medicare == full_surtax_med, (
                f"period {i+1}: medicare expected "
                f"{full_surtax_med}, "
                f"got {results[i].medicare}"
            )


# ── Annual Projection Tests ─────────────────────────────────────


class TestAnnualProjection:
    """Tests for full-year salary projection correctness."""

    def test_26_period_annual_net_pay_sum(
        self, base_profile, biweekly_periods,
        simple_tax_configs
    ):
        """Annual totals across 26 periods for $60k salary.

        All periods identical (cumul max $59,999.94 is under
        SS cap $168,600 and surtax threshold $200,000).
        Per period: gross=$2,307.69, federal=$173.08,
          state=$103.85, SS=$143.08, medicare=$33.46,
          net=$1,854.22
        Annual = per_period * 26 for each field.
        """
        results = project_salary(
            base_profile, biweekly_periods, simple_tax_configs
        )

        assert len(results) == 26, (
            f"expected 26 results, got {len(results)}"
        )

        # Per-period oracle values:
        # gross=60000/26=2307.69, federal=4499.99/26=173.08
        # state=2700.00/26=103.85, SS=2307.69*0.062=143.08
        # medicare=2307.69*0.0145=33.46
        # net=2307.69-173.08-103.85-143.08-33.46=1854.22
        exp_gross = Decimal("2307.69")
        exp_net = Decimal("1854.22")

        # 2307.69 * 26 = 59999.94
        total_gross = sum(
            r.gross_biweekly for r in results
        )
        assert total_gross == exp_gross * 26, (
            f"total gross: expected 59999.94, "
            f"got {total_gross}"
        )

        # 173.08 * 26 = 4500.08
        total_federal = sum(
            r.federal_tax for r in results
        )
        assert total_federal == Decimal("173.08") * 26, (
            f"total federal: expected 4500.08, "
            f"got {total_federal}"
        )

        # 103.85 * 26 = 2700.10
        total_state = sum(
            r.state_tax for r in results
        )
        assert total_state == Decimal("103.85") * 26, (
            f"total state: expected 2700.10, "
            f"got {total_state}"
        )

        # 143.08 * 26 = 3720.08
        total_ss = sum(
            r.social_security for r in results
        )
        assert total_ss == Decimal("143.08") * 26, (
            f"total SS: expected 3720.08, "
            f"got {total_ss}"
        )

        # 33.46 * 26 = 869.96
        total_medicare = sum(
            r.medicare for r in results
        )
        assert total_medicare == Decimal("33.46") * 26, (
            f"total medicare: expected 869.96, "
            f"got {total_medicare}"
        )

        # 1854.22 * 26 = 48209.72
        total_net = sum(r.net_pay for r in results)
        assert total_net == exp_net * 26, (
            f"total net: expected 48209.72, "
            f"got {total_net}"
        )

        # Cross-check: net = gross - fed - state - ss - med
        assert total_net == (
            total_gross - total_federal - total_state
            - total_ss - total_medicare
        ), "net cross-check: components don't sum to net"

    def test_project_salary_all_periods_consistent(
        self, base_profile, biweekly_periods,
        simple_tax_configs
    ):
        """All 26 periods identical for $60k (under all caps).

        $60k cumul max = 26*2307.69 = $59,999.94, under
        SS cap ($168,600) and surtax ($200,000). Every
        period produces the exact same breakdown.
        """
        results = project_salary(
            base_profile, biweekly_periods, simple_tax_configs
        )

        assert len(results) == 26, (
            f"expected 26 results, got {len(results)}"
        )

        first = results[0]
        for i in range(1, 26):
            r = results[i]
            assert r.gross_biweekly == first.gross_biweekly, (
                f"period {i+1}: gross "
                f"{r.gross_biweekly} != "
                f"period 1 gross {first.gross_biweekly}"
            )
            assert r.federal_tax == first.federal_tax, (
                f"period {i+1}: federal "
                f"{r.federal_tax} != "
                f"period 1 federal {first.federal_tax}"
            )
            assert r.state_tax == first.state_tax, (
                f"period {i+1}: state "
                f"{r.state_tax} != "
                f"period 1 state {first.state_tax}"
            )
            assert r.social_security == first.social_security, (
                f"period {i+1}: SS "
                f"{r.social_security} != "
                f"period 1 SS {first.social_security}"
            )
            assert r.medicare == first.medicare, (
                f"period {i+1}: medicare "
                f"{r.medicare} != "
                f"period 1 medicare {first.medicare}"
            )
            assert r.net_pay == first.net_pay, (
                f"period {i+1}: net "
                f"{r.net_pay} != "
                f"period 1 net {first.net_pay}"
            )


# ── Edge Case Tests ─────────────────────────────────────────────


class TestEdgeCases:
    """Tests for edge cases: zero and negative salary."""

    def test_zero_salary(self, simple_tax_configs):
        """All fields zero when annual salary is $0.

        gross = 0/26 = 0.00
        federal: annual=0, taxable=max(0-15000,0)=0, 0.00
        state: 0*0.045 = 0.00, /26 = 0.00
        SS: 0*0.062 = 0.00
        Medicare: 0*0.0145 = 0.00
        net = 0.00
        """
        profile = FakeProfile(
            annual_salary=0,
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(
            start_date=date(2026, 1, 16), period_id=1
        )

        result = calculate_paycheck(
            profile, period, [period], simple_tax_configs
        )

        assert result.gross_biweekly == Decimal("0.00"), (
            f"gross: expected 0.00, "
            f"got {result.gross_biweekly}"
        )
        assert result.federal_tax == Decimal("0.00"), (
            f"federal: expected 0.00, "
            f"got {result.federal_tax}"
        )
        assert result.state_tax == Decimal("0.00"), (
            f"state: expected 0.00, "
            f"got {result.state_tax}"
        )
        assert result.social_security == Decimal("0.00"), (
            f"SS: expected 0.00, "
            f"got {result.social_security}"
        )
        assert result.medicare == Decimal("0.00"), (
            f"medicare: expected 0.00, "
            f"got {result.medicare}"
        )
        assert result.net_pay == Decimal("0.00"), (
            f"net: expected 0.00, "
            f"got {result.net_pay}"
        )

    def test_negative_salary_behavior(self, simple_tax_configs):
        """Negative salary cascades to InvalidGrossPayError.

        -10000/26 = -384.62 (negative gross_biweekly).
        calculate_federal_withholding validates gross_pay >= 0
        and raises InvalidGrossPayError for negative input.
        calculate_paycheck does not validate salary itself.
        """
        profile = FakeProfile(
            annual_salary=-10000,
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(
            start_date=date(2026, 1, 16), period_id=1
        )

        with pytest.raises(InvalidGrossPayError):
            calculate_paycheck(
                profile, period, [period],
                simple_tax_configs
            )


# ── Negative-Path and Boundary-Condition Tests ─────────────────────


class TestNegativeAndBoundaryPaths:
    """Negative-path and boundary-condition tests for the paycheck calculator.

    Verifies behavior with zero/edge-case salary profiles, excessive
    deductions, and unusual pay frequencies.
    """

    def test_pay_periods_per_year_zero_defaults_to_26(self, simple_tax_configs):
        """pay_periods_per_year=0 defaults to 26 via 'or 26' fallback.

        Input: Profile with pay_periods_per_year=0.
        Expected: Identical output to pay_periods_per_year=26. The source code
        has `profile.pay_periods_per_year or 26` which treats 0 as falsy.
        Why: Division by zero in the paycheck pipeline would crash grid load
        for any user with a misconfigured salary profile.
        """
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        profile_zero = FakeProfile(
            annual_salary=60000,
            pay_periods_per_year=0,
            created_at=date(2026, 1, 1),
        )
        profile_26 = FakeProfile(
            annual_salary=60000,
            pay_periods_per_year=26,
            created_at=date(2026, 1, 1),
        )

        # NOTE: Source does not raise for zero. Instead,
        # `profile.pay_periods_per_year or 26` silently defaults to 26.
        # Consider adding a ValidationError guard if 0 is invalid user input.
        result_zero = calculate_paycheck(
            profile_zero, period, [period], simple_tax_configs
        )
        result_26 = calculate_paycheck(
            profile_26, period, [period], simple_tax_configs
        )

        # Both produce identical results since 0 defaults to 26.
        assert result_zero.gross_biweekly == result_26.gross_biweekly
        assert result_zero.federal_tax == result_26.federal_tax
        assert result_zero.state_tax == result_26.state_tax
        assert result_zero.social_security == result_26.social_security
        assert result_zero.medicare == result_26.medicare
        assert result_zero.net_pay == result_26.net_pay

        # Verify actual values match known $60k/26-periods result.
        assert result_zero.gross_biweekly == Decimal("2307.69")
        assert result_zero.net_pay == Decimal("1854.22")

    def test_pay_periods_per_year_one_annual(
        self, simple_bracket_set, nc_state_config, standard_fica
    ):
        """Annual pay frequency (1 period/year) produces no rounding artifacts.

        Input: annual_salary=78000, pay_periods_per_year=1, no raises/deductions.
        Pipeline trace:
          gross = 78000 / 1 = 78000.00
          federal: taxable = 78000 - 15000 = 63000
            50000*0.10 + 13000*0.22 = 5000 + 2860 = 7860.00 / 1 = 7860.00
          state: 78000*0.045 = 3510.00 / 1 = 3510.00
          SS: 78000*0.062 = 4836.00
          Medicare: 78000*0.0145 = 1131.00
          net: 78000 - 7860 - 3510 - 4836 - 1131 = 60663.00
        Why: Annual pay frequency is a real edge case (contractors). The
        per-period conversion must not introduce rounding artifacts when periods=1.
        """
        profile = FakeProfile(
            annual_salary=78000,
            pay_periods_per_year=1,
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)
        tax_configs = {
            "bracket_set": simple_bracket_set,
            "state_config": nc_state_config,
            "fica_config": standard_fica,
        }

        result = calculate_paycheck(profile, period, [period], tax_configs)

        # gross = 78000 / 1 = 78000.00 (exact, no rounding)
        assert result.gross_biweekly == Decimal("78000.00"), (
            f"gross: expected 78000.00, got {result.gross_biweekly}"
        )
        # Federal: 50000*0.10 + 13000*0.22 = 7860.00 / 1 = 7860.00
        assert result.federal_tax == Decimal("7860.00"), (
            f"federal: expected 7860.00, got {result.federal_tax}"
        )
        # State: 78000*0.045 = 3510.00 / 1 = 3510.00
        assert result.state_tax == Decimal("3510.00"), (
            f"state: expected 3510.00, got {result.state_tax}"
        )
        # SS: 78000*0.062 = 4836.00
        assert result.social_security == Decimal("4836.00"), (
            f"SS: expected 4836.00, got {result.social_security}"
        )
        # Medicare: 78000*0.0145 = 1131.00
        assert result.medicare == Decimal("1131.00"), (
            f"medicare: expected 1131.00, got {result.medicare}"
        )
        # net = 78000 - 7860 - 3510 - 4836 - 1131 = 60663.00
        assert result.net_pay == Decimal("60663.00"), (
            f"net: expected 60663.00, got {result.net_pay}"
        )

    def test_net_pay_negative_from_excessive_post_tax(self, simple_tax_configs):
        """Excessive post-tax deductions produce negative net pay.

        Input: annual_salary=30000 (gross=1153.85/period), post-tax deduction=2000.
        Pipeline trace:
          gross = 30000/26 = 1153.85
          federal: annual=30000.10, taxable=15000.10
            15000.10*0.10 = 1500.01 / 26 = 57.69
          state: 30000.10*0.045 = 1350.00 / 26 = 51.92
          SS: 1153.85*0.062 = 71.54
          Medicare: 1153.85*0.0145 = 16.73
          post_tax: 2000.00
          net = 1153.85 - 57.69 - 51.92 - 71.54 - 16.73 - 2000.00 = -1044.03
        Why: A user misconfiguring deductions could get a negative net pay shown
        on the budget grid. The app must handle this deterministically, not crash.
        """
        profile = FakeProfile(
            annual_salary=30000,
            pay_periods_per_year=26,
            deductions=[
                FakeDeduction(
                    name="Excessive Post Tax",
                    amount="2000",
                    deduction_timing="post_tax",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(profile, period, [period], simple_tax_configs)

        assert result.gross_biweekly == Decimal("1153.85")
        assert result.federal_tax == Decimal("57.69")
        assert result.state_tax == Decimal("51.92")
        assert result.social_security == Decimal("71.54")
        assert result.medicare == Decimal("16.73")

        # The calculator returns negative net pay when post-tax deductions exceed
        # take-home. The route layer should warn the user.
        assert result.net_pay == Decimal("-1044.03"), (
            f"net_pay: expected -1044.03, got {result.net_pay}"
        )

    def test_zero_annual_salary(self, simple_tax_configs):
        """Zero salary produces zero in every field without error.

        Input: annual_salary=0, pay_periods_per_year=26, no deductions.
        Expected: All fields (including annual_salary, taxable_income) are zero.
        Why: A zero-salary profile (e.g., a template or placeholder) must not
        produce NaN, crash, or negative values in any tax calculation.
        """
        profile = FakeProfile(
            annual_salary=0,
            pay_periods_per_year=26,
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(profile, period, [period], simple_tax_configs)

        assert result.annual_salary == Decimal("0.00")
        assert result.gross_biweekly == Decimal("0.00")
        assert result.taxable_income == Decimal("0.00")
        assert result.federal_tax == Decimal("0.00")
        assert result.state_tax == Decimal("0.00")
        assert result.social_security == Decimal("0.00")
        assert result.medicare == Decimal("0.00")
        assert result.net_pay == Decimal("0.00")

    def test_massive_deductions_exceed_gross(self, simple_tax_configs):
        """Pre-tax deductions exceeding gross clamp taxable to zero.

        Input: annual_salary=52000 (gross=2000/period), pre-tax deduction=2500.
        Pipeline trace:
          gross = 52000/26 = 2000.00
          taxable_biweekly = max(2000 - 2500, 0) = 0.00 (clamped)
          federal: adjusted = max(52000 - 65000, 0) = 0, taxable = 0, tax = 0
          state: 0*26*0.045 = 0
          SS: 2000*0.062 = 124.00 (FICA uses gross, not taxable)
          Medicare: 2000*0.0145 = 29.00
          net = 2000 - 2500 - 0 - 0 - 124 - 29 = -653.00
        Why: Pre-tax deductions reducing gross below zero would produce negative
        taxable income, which could break bracket calculations.
        """
        profile = FakeProfile(
            annual_salary=52000,
            pay_periods_per_year=26,
            deductions=[
                FakeDeduction(
                    name="Mega Pre Tax",
                    amount="2500",
                    deduction_timing="pre_tax",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(profile, period, [period], simple_tax_configs)

        # gross = 52000/26 = 2000.00
        assert result.gross_biweekly == Decimal("2000.00")
        # taxable_biweekly = max(2000 - 2500, 0) = 0.00 (clamped by source code)
        assert result.taxable_income == Decimal("0.00"), (
            f"taxable_income should be clamped to 0, got {result.taxable_income}"
        )
        # Federal/state: 0 taxable → 0 tax
        assert result.federal_tax == Decimal("0.00")
        assert result.state_tax == Decimal("0.00")
        # FICA is computed on gross, not taxable income.
        # SS: 2000*0.062 = 124.00
        assert result.social_security == Decimal("124.00")
        # Medicare: 2000*0.0145 = 29.00
        assert result.medicare == Decimal("29.00")
        # net = 2000 - 2500 - 0 - 0 - 124 - 29 = -653.00
        # The calculator allows negative net when deductions exceed gross.
        assert result.net_pay == Decimal("-653.00"), (
            f"net_pay: expected -653.00, got {result.net_pay}"
        )
