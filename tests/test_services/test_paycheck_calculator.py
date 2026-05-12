"""
Shekel Budget App -- Unit Tests for Paycheck Calculator

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
from app import ref_cache
from app.enums import CalcMethodEnum, DeductionTimingEnum


def _timing_id(name):
    """Resolve a deduction timing name (e.g. 'pre_tax') to its integer ID."""
    _map = {e.value: e for e in DeductionTimingEnum}
    return ref_cache.deduction_timing_id(_map[name])


def _pct_id():
    """Return the integer ID for the 'percentage' calc method."""
    return ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)


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
        # Resolve integer IDs from the ref_cache for ID-based comparisons.
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import CalcMethodEnum, DeductionTimingEnum  # pylint: disable=import-outside-toplevel
        _timing_map = {e.value: e for e in DeductionTimingEnum}
        _method_map = {e.value: e for e in CalcMethodEnum}
        self.deduction_timing_id = ref_cache.deduction_timing_id(_timing_map[deduction_timing])
        self.calc_method_id = ref_cache.calc_method_id(_method_map[calc_method])


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
        # Resolve the integer ID from the ref_cache for ID-based lookups.
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TaxTypeEnum  # pylint: disable=import-outside-toplevel
        _name_to_enum = {e.value: e for e in TaxTypeEnum}
        self.tax_type_id = ref_cache.tax_type_id(_name_to_enum[tax_type_name])


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
        # Check in 2027 -- still just one application.
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

        This is a legacy edge case -- the UI now requires effective_year for
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
                                       _timing_id("pre_tax"), _pct_id(), True)
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
                                       _timing_id("pre_tax"), _pct_id(), False)
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
                                       _timing_id("pre_tax"), _pct_id(), False)
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
                                       _timing_id("pre_tax"), _pct_id(), False)
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
                                       _timing_id("pre_tax"), _pct_id(), False)
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


# ── Pre-Tax Deduction Tax Impact Tests ─────────────────────────


class TestPreTaxDeductionTaxImpact:
    """Verify that pre-tax deductions reduce income taxes but NOT FICA.

    This is the core tax calculation invariant for U.S. payroll:
      - Federal income tax: computed on (gross - pre_tax - std_deduction)
      - State income tax: computed on (gross - pre_tax - state_std_deduction)
      - Social Security: computed on gross (NOT reduced by pre-tax deductions)
      - Medicare: computed on gross (NOT reduced by pre-tax deductions)

    Each test computes expected values by hand from first principles and
    compares against the calculator output. The hand calculations follow
    the IRS Pub 15-T Percentage Method pipeline exactly:
      1. Annualize gross: gross_biweekly * 26
      2. Subtract annualized pre-tax deductions
      3. Subtract standard deduction
      4. Apply marginal brackets
      5. De-annualize: annual_tax / 26

    All tests use the simple_bracket_set fixture (0-50k@10%, 50k+@22%,
    std_deduction=$15,000) and nc_state_config (NC 4.5% flat, no state
    standard deduction) unless otherwise noted.

    Baseline reference (no deductions, $60k salary, established by
    TestCalculatePaycheckPipeline.test_basic_paycheck_no_deductions):
      gross=2307.69, federal=173.08, state=103.85, SS=143.08,
      medicare=33.46, net=1854.22
    """

    def test_flat_pretax_deduction_reduces_federal_and_state(
        self, simple_tax_configs
    ):
        """$200/paycheck pre-tax 401(k) lowers federal and state taxes.

        This test catches the section 3.1 bug if it were to exist: taxes
        computed on gross instead of taxable income.

        Hand calculation:
          gross = 60000/26 = $2,307.69
          pre_tax = $200.00
          taxable_biweekly = 2307.69 - 200 = $2,107.69

          Federal (Pub 15-T):
            annual_income = 2307.69 * 26 = $59,999.94
            annual_pre_tax = 200 * 26 = $5,200.00
            adjusted = 59,999.94 - 5,200 = $54,799.94
            taxable = 54,799.94 - 15,000 = $39,799.94
            tax = 39,799.94 * 0.10 = $3,979.99  (all in 10% bracket)
            per_period = 3,979.99 / 26 = $153.08

          State:
            annual = 2,107.69 * 26 = $54,799.94
            tax = 54,799.94 * 0.045 = $2,466.00
            per_period = 2,466.00 / 26 = $94.85

        Without deduction: federal=173.08, state=103.85 (baseline).
        Reduction: federal drops by $20.00 (= $200 * 10% marginal rate),
                   state drops by $9.00 (= $200 * 4.5% flat rate).
        """
        profile = FakeProfile(
            annual_salary=60000,
            deductions=[
                FakeDeduction(
                    name="401k", amount="200",
                    deduction_timing="pre_tax",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        with_ded = calculate_paycheck(
            profile, period, [period], simple_tax_configs
        )

        # Baseline comparison (from established test):
        baseline_federal = Decimal("173.08")
        baseline_state = Decimal("103.85")

        # Federal tax must be LOWER with pre-tax deduction.
        assert with_ded.federal_tax == Decimal("153.08"), (
            f"federal_tax: expected 153.08, got {with_ded.federal_tax}"
        )
        assert with_ded.federal_tax < baseline_federal, (
            "Pre-tax deduction must reduce federal tax"
        )

        # State tax must be LOWER with pre-tax deduction.
        assert with_ded.state_tax == Decimal("94.85"), (
            f"state_tax: expected 94.85, got {with_ded.state_tax}"
        )
        assert with_ded.state_tax < baseline_state, (
            "Pre-tax deduction must reduce state tax"
        )

        # Taxable income field must equal gross minus pre-tax.
        assert with_ded.taxable_income == Decimal("2107.69"), (
            f"taxable_income: expected 2107.69, "
            f"got {with_ded.taxable_income}"
        )

        # Verify the magnitude of tax reduction matches marginal rates.
        # $200 * 10% marginal bracket = $20.00/period federal reduction.
        assert baseline_federal - with_ded.federal_tax == Decimal("20.00"), (
            f"Federal reduction should be $20.00 "
            f"(= $200 * 10% bracket rate), "
            f"got {baseline_federal - with_ded.federal_tax}"
        )
        # $200 * 4.5% NC flat rate = $9.00/period state reduction.
        assert baseline_state - with_ded.state_tax == Decimal("9.00"), (
            f"State reduction should be $9.00 "
            f"(= $200 * 4.5% flat rate), "
            f"got {baseline_state - with_ded.state_tax}"
        )

    def test_pretax_deduction_does_not_reduce_fica(
        self, simple_tax_configs
    ):
        """FICA (SS + Medicare) must be computed on gross, NOT taxable income.

        Pre-tax 401(k) deductions reduce federal/state income tax bases but
        do NOT reduce FICA wages (IRC Section 3121 -- 401(k) contributions
        are subject to FICA). If this test fails, FICA is being incorrectly
        computed on taxable income instead of gross.

        Baseline FICA (no deductions):
          SS = 2307.69 * 0.062 = $143.08
          Medicare = 2307.69 * 0.0145 = $33.46

        With $200 pre-tax deduction: FICA must be IDENTICAL.
        """
        no_ded_profile = FakeProfile(
            annual_salary=60000,
            created_at=date(2026, 1, 1),
        )
        with_ded_profile = FakeProfile(
            annual_salary=60000,
            deductions=[
                FakeDeduction(
                    name="401k", amount="200",
                    deduction_timing="pre_tax",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        no_ded = calculate_paycheck(
            no_ded_profile, period, [period], simple_tax_configs
        )
        with_ded = calculate_paycheck(
            with_ded_profile, period, [period], simple_tax_configs
        )

        # SS must be identical -- computed on gross, not taxable.
        assert with_ded.social_security == no_ded.social_security, (
            f"SS changed with pre-tax deduction: "
            f"{no_ded.social_security} -> {with_ded.social_security}. "
            f"FICA must be computed on gross, not taxable income."
        )
        assert with_ded.social_security == Decimal("143.08"), (
            f"SS: expected 143.08, got {with_ded.social_security}"
        )

        # Medicare must be identical -- computed on gross, not taxable.
        assert with_ded.medicare == no_ded.medicare, (
            f"Medicare changed with pre-tax deduction: "
            f"{no_ded.medicare} -> {with_ded.medicare}. "
            f"FICA must be computed on gross, not taxable income."
        )
        assert with_ded.medicare == Decimal("33.46"), (
            f"Medicare: expected 33.46, got {with_ded.medicare}"
        )

        # Gross must also be identical (deductions don't change gross).
        assert with_ded.gross_biweekly == no_ded.gross_biweekly

    def test_percentage_pretax_deduction_reduces_taxes(
        self, simple_tax_configs
    ):
        """6% percentage-based pre-tax 401(k) reduces income taxes correctly.

        Percentage deductions use calc_method='percentage' and are computed
        as a percentage of gross_biweekly. The resulting amount must reduce
        the tax base for income taxes but not FICA.

        Hand calculation:
          gross = 60000/26 = $2,307.69
          deduction = 2307.69 * 0.06 = $138.46
          taxable_biweekly = 2307.69 - 138.46 = $2,169.23

          Federal:
            annual_pre_tax = 138.46 * 26 = $3,599.96
            adjusted = 59,999.94 - 3,599.96 = $56,399.98
            taxable = 56,399.98 - 15,000 = $41,399.98
            tax = 41,399.98 * 0.10 = $4,140.00
            per_period = 4,140.00 / 26 = $159.23

          State:
            annual = 2,169.23 * 26 = $56,399.98
            tax = 56,399.98 * 0.045 = $2,538.00
            per_period = 2,538.00 / 26 = $97.62

          FICA: unchanged at SS=$143.08, Medicare=$33.46
        """
        profile = FakeProfile(
            annual_salary=60000,
            deductions=[
                FakeDeduction(
                    name="401k", amount="0.06",
                    calc_method="percentage",
                    deduction_timing="pre_tax",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            profile, period, [period], simple_tax_configs
        )

        # Deduction amount computed from gross.
        assert result.pre_tax_deductions[0].amount == Decimal("138.46"), (
            f"6% of 2307.69: expected 138.46, "
            f"got {result.pre_tax_deductions[0].amount}"
        )

        assert result.taxable_income == Decimal("2169.23"), (
            f"taxable_income: expected 2169.23, "
            f"got {result.taxable_income}"
        )

        assert result.federal_tax == Decimal("159.23"), (
            f"federal_tax: expected 159.23, got {result.federal_tax}"
        )
        assert result.state_tax == Decimal("97.62"), (
            f"state_tax: expected 97.62, got {result.state_tax}"
        )

        # FICA on gross -- unaffected by percentage deduction.
        assert result.social_security == Decimal("143.08"), (
            f"SS: expected 143.08, got {result.social_security}"
        )
        assert result.medicare == Decimal("33.46"), (
            f"Medicare: expected 33.46, got {result.medicare}"
        )

        # Net pay end-to-end.
        # 2307.69 - 138.46 - 159.23 - 97.62 - 143.08 - 33.46 = 1735.84
        assert result.net_pay == Decimal("1735.84"), (
            f"net_pay: expected 1735.84, got {result.net_pay}"
        )

    def test_third_paycheck_skipped_deduction_increases_taxes(
        self, simple_tax_configs
    ):
        """On a 3rd paycheck, 24/yr deductions are skipped, raising taxes.

        When a 24-per-year deduction (e.g., health insurance) is skipped on
        a 3rd paycheck, the pre-tax deduction total is lower, which means
        taxable income is higher, which means income taxes are higher. This
        is correct real-world payroll behavior.

        Setup: 3 periods in January (p1=Jan 2, p2=Jan 16, p3=Jan 30).
        p3 is the 3rd paycheck. $100/paycheck health insurance at 24/yr.

        On p2 (normal paycheck, deduction applies):
          pre_tax = $100
          annual_pre_tax = 100 * 26 = $2,600
          federal taxable = (59,999.94 - 2,600 - 15,000) = $42,399.94
          federal = 42,399.94 * 0.10 = $4,239.99 / 26 = $163.08
          state = (2207.69 * 26) * 0.045 = $2,583.00 / 26 = $99.35

        On p3 (3rd paycheck, deduction skipped):
          pre_tax = $0
          federal = $173.08 (same as no-deduction baseline)
          state = $103.85

        Tax increase on 3rd paycheck:
          federal: 173.08 - 163.08 = $10.00 (= $100 * 10% bracket)
          state: 103.85 - 99.35 = $4.50 (= $100 * 4.5% flat)
        """
        profile = FakeProfile(
            annual_salary=60000,
            deductions=[
                FakeDeduction(
                    name="Health Insurance", amount="100",
                    deductions_per_year=24,
                    deduction_timing="pre_tax",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        # 3 periods in January to trigger 3rd paycheck detection.
        p1 = FakePeriod(start_date=date(2026, 1, 2), period_id=1)
        p2 = FakePeriod(start_date=date(2026, 1, 16), period_id=2)
        p3 = FakePeriod(start_date=date(2026, 1, 30), period_id=3)
        all_periods = [p1, p2, p3]

        normal = calculate_paycheck(
            profile, p2, all_periods, simple_tax_configs
        )
        third = calculate_paycheck(
            profile, p3, all_periods, simple_tax_configs
        )

        # On normal paycheck, deduction applies.
        assert len(normal.pre_tax_deductions) == 1, (
            "Normal paycheck should have 1 pre-tax deduction"
        )
        assert normal.pre_tax_deductions[0].amount == Decimal("100.00")
        assert normal.federal_tax == Decimal("163.08"), (
            f"Normal federal: expected 163.08, got {normal.federal_tax}"
        )
        assert normal.state_tax == Decimal("99.35"), (
            f"Normal state: expected 99.35, got {normal.state_tax}"
        )

        # On 3rd paycheck, deduction is skipped.
        assert len(third.pre_tax_deductions) == 0, (
            "3rd paycheck should have 0 pre-tax deductions "
            "(24/yr deduction skipped)"
        )
        assert third.is_third_paycheck is True

        # 3rd paycheck taxes are HIGHER because deduction was skipped.
        assert third.federal_tax == Decimal("173.08"), (
            f"3rd paycheck federal: expected 173.08, "
            f"got {third.federal_tax}"
        )
        assert third.state_tax == Decimal("103.85"), (
            f"3rd paycheck state: expected 103.85, "
            f"got {third.state_tax}"
        )
        assert third.federal_tax > normal.federal_tax, (
            "3rd paycheck federal should be higher (deduction skipped)"
        )
        assert third.state_tax > normal.state_tax, (
            "3rd paycheck state should be higher (deduction skipped)"
        )

        # Tax increase exactly matches deduction * marginal rate.
        assert third.federal_tax - normal.federal_tax == Decimal("10.00"), (
            f"Federal increase: expected $10 "
            f"(= $100 * 10% bracket), "
            f"got {third.federal_tax - normal.federal_tax}"
        )
        assert third.state_tax - normal.state_tax == Decimal("4.50"), (
            f"State increase: expected $4.50 "
            f"(= $100 * 4.5% flat), "
            f"got {third.state_tax - normal.state_tax}"
        )

        # FICA identical on both (gross is the same).
        assert third.social_security == normal.social_security
        assert third.medicare == normal.medicare

    def test_multiple_pretax_deductions_stack(
        self, simple_tax_configs
    ):
        """Two pre-tax deductions ($200 + $100) stack to reduce taxes by $300.

        Hand calculation:
          gross = $2,307.69
          total_pre_tax = $300.00
          taxable_biweekly = 2307.69 - 300 = $2,007.69
          annual_pre_tax = 300 * 26 = $7,800

          Federal:
            adjusted = 59,999.94 - 7,800 = $52,199.94
            taxable = 52,199.94 - 15,000 = $37,199.94
            tax = 37,199.94 * 0.10 = $3,719.99 / 26 = $143.08

          State:
            annual = 2,007.69 * 26 = $52,199.94
            tax = 52,199.94 * 0.045 = $2,349.00 / 26 = $90.35

          Federal reduction from baseline: 173.08 - 143.08 = $30.00
            = $300 * 10% bracket rate
          State reduction from baseline: 103.85 - 90.35 = $13.50
            = $300 * 4.5% flat rate

          Net = 2307.69 - 300 - 143.08 - 90.35 - 143.08 - 33.46 = $1,597.72
        """
        profile = FakeProfile(
            annual_salary=60000,
            deductions=[
                FakeDeduction(
                    name="401k", amount="200",
                    deduction_timing="pre_tax",
                ),
                FakeDeduction(
                    name="Health", amount="100",
                    deduction_timing="pre_tax",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            profile, period, [period], simple_tax_configs
        )

        assert result.total_pre_tax == Decimal("300.00"), (
            f"total_pre_tax: expected 300, got {result.total_pre_tax}"
        )
        assert result.taxable_income == Decimal("2007.69"), (
            f"taxable_income: expected 2007.69, "
            f"got {result.taxable_income}"
        )
        assert result.federal_tax == Decimal("143.08"), (
            f"federal_tax: expected 143.08, got {result.federal_tax}"
        )
        assert result.state_tax == Decimal("90.35"), (
            f"state_tax: expected 90.35, got {result.state_tax}"
        )

        # Reduction from baseline matches total deduction * marginal rate.
        assert Decimal("173.08") - result.federal_tax == Decimal("30.00"), (
            "Federal reduction should be $30 = $300 * 10%"
        )
        assert Decimal("103.85") - result.state_tax == Decimal("13.50"), (
            "State reduction should be $13.50 = $300 * 4.5%"
        )

        # FICA still on gross.
        assert result.social_security == Decimal("143.08")
        assert result.medicare == Decimal("33.46")

        # End-to-end net pay.
        assert result.net_pay == Decimal("1597.72"), (
            f"net_pay: expected 1597.72, got {result.net_pay}"
        )

    def test_post_tax_deduction_does_not_affect_any_tax(
        self, simple_tax_configs
    ):
        """Post-tax deductions (e.g., Roth IRA) must NOT change any tax amount.

        A post-tax deduction reduces net pay but has zero impact on federal,
        state, SS, or Medicare. If this test fails, post-tax deductions are
        leaking into the tax base calculation.

        All tax values must match the no-deduction baseline exactly:
          federal=173.08, state=103.85, SS=143.08, Medicare=33.46

        Net = 2307.69 - 173.08 - 103.85 - 143.08 - 33.46 - 200.00
            = $1,654.22
        """
        no_ded_profile = FakeProfile(
            annual_salary=60000,
            created_at=date(2026, 1, 1),
        )
        post_ded_profile = FakeProfile(
            annual_salary=60000,
            deductions=[
                FakeDeduction(
                    name="Roth IRA", amount="200",
                    deduction_timing="post_tax",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        no_ded = calculate_paycheck(
            no_ded_profile, period, [period], simple_tax_configs
        )
        post_ded = calculate_paycheck(
            post_ded_profile, period, [period], simple_tax_configs
        )

        # Every tax field must be identical.
        assert post_ded.federal_tax == no_ded.federal_tax == Decimal("173.08"), (
            f"Post-tax deduction changed federal: "
            f"{no_ded.federal_tax} -> {post_ded.federal_tax}"
        )
        assert post_ded.state_tax == no_ded.state_tax == Decimal("103.85"), (
            f"Post-tax deduction changed state: "
            f"{no_ded.state_tax} -> {post_ded.state_tax}"
        )
        assert post_ded.social_security == no_ded.social_security == Decimal("143.08"), (
            f"Post-tax deduction changed SS: "
            f"{no_ded.social_security} -> {post_ded.social_security}"
        )
        assert post_ded.medicare == no_ded.medicare == Decimal("33.46"), (
            f"Post-tax deduction changed Medicare: "
            f"{no_ded.medicare} -> {post_ded.medicare}"
        )

        # Taxable income must also be unchanged.
        assert post_ded.taxable_income == no_ded.taxable_income, (
            "Post-tax deduction should not affect taxable_income"
        )

        # Only net pay changes (reduced by $200 post-tax).
        assert post_ded.net_pay == Decimal("1654.22"), (
            f"net_pay: expected 1654.22, got {post_ded.net_pay}"
        )
        assert post_ded.net_pay == no_ded.net_pay - Decimal("200.00"), (
            "Net pay should decrease by exactly the post-tax amount"
        )

    def test_mixed_pre_and_post_tax_deductions(
        self, simple_tax_configs
    ):
        """Pre-tax and post-tax deductions interact correctly.

        $200 pre-tax 401(k) + $150 post-tax Roth IRA. Only the pre-tax
        deduction reduces the tax base. Post-tax is subtracted after taxes.

        Tax values should match the "$200 pre-tax only" scenario:
          federal=$153.08, state=$94.85, SS=$143.08, Medicare=$33.46

        Net = 2307.69 - 200.00 - 153.08 - 94.85 - 143.08 - 33.46 - 150.00
            = $1,533.22
        """
        profile = FakeProfile(
            annual_salary=60000,
            deductions=[
                FakeDeduction(
                    name="401k", amount="200",
                    deduction_timing="pre_tax",
                ),
                FakeDeduction(
                    name="Roth IRA", amount="150",
                    deduction_timing="post_tax",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            profile, period, [period], simple_tax_configs
        )

        # Taxes match the $200 pre-tax scenario (post-tax has no effect).
        assert result.federal_tax == Decimal("153.08"), (
            f"federal: expected 153.08, got {result.federal_tax}"
        )
        assert result.state_tax == Decimal("94.85"), (
            f"state: expected 94.85, got {result.state_tax}"
        )
        assert result.social_security == Decimal("143.08"), (
            f"SS: expected 143.08, got {result.social_security}"
        )
        assert result.medicare == Decimal("33.46"), (
            f"Medicare: expected 33.46, got {result.medicare}"
        )

        # Both deduction types present in their respective lists.
        assert result.total_pre_tax == Decimal("200.00")
        assert result.total_post_tax == Decimal("150.00")

        # Net pay accounts for both deduction types.
        assert result.net_pay == Decimal("1533.22"), (
            f"net_pay: expected 1533.22, got {result.net_pay}"
        )

    def test_state_tax_with_standard_deduction_and_pretax(
        self, simple_bracket_set, standard_fica
    ):
        """State standard deduction and pre-tax deductions both reduce state tax.

        The state tax pipeline is: (gross - pre_tax) * 26 - state_std_ded,
        then multiply by flat rate. Both reductions must apply.

        Setup: NC 4.5% flat rate WITH $12,750 standard deduction.

        Without pre-tax deductions:
          annual = 2307.69 * 26 = $59,999.94
          state_taxable = 59,999.94 - 12,750 = $47,249.94
          tax = 47,249.94 * 0.045 = $2,126.25 / 26 = $81.78

        With $200 pre-tax deduction:
          annual = 2107.69 * 26 = $54,799.94
          state_taxable = 54,799.94 - 12,750 = $42,049.94
          tax = 42,049.94 * 0.045 = $1,892.25 / 26 = $72.78

        Reduction: 81.78 - 72.78 = $9.00 (= $200 * 4.5% flat rate)
        """
        # State config with standard deduction (not in default fixture).
        state_with_std_ded = FakeStateTaxConfig(flat_rate="0.045")
        state_with_std_ded.standard_deduction = Decimal("12750")

        configs = {
            "bracket_set": simple_bracket_set,
            "state_config": state_with_std_ded,
            "fica_config": standard_fica,
        }

        no_ded_profile = FakeProfile(
            annual_salary=60000,
            created_at=date(2026, 1, 1),
        )
        with_ded_profile = FakeProfile(
            annual_salary=60000,
            deductions=[
                FakeDeduction(
                    name="401k", amount="200",
                    deduction_timing="pre_tax",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        no_ded = calculate_paycheck(
            no_ded_profile, period, [period], configs
        )
        with_ded = calculate_paycheck(
            with_ded_profile, period, [period], configs
        )

        # Without deduction: state std ded reduces the base.
        assert no_ded.state_tax == Decimal("81.78"), (
            f"No-deduction state: expected 81.78, "
            f"got {no_ded.state_tax}"
        )

        # With deduction: both reductions apply.
        assert with_ded.state_tax == Decimal("72.78"), (
            f"With-deduction state: expected 72.78, "
            f"got {with_ded.state_tax}"
        )

        # Reduction matches deduction * flat rate.
        assert no_ded.state_tax - with_ded.state_tax == Decimal("9.00"), (
            f"State reduction: expected $9.00 "
            f"(= $200 * 4.5%), "
            f"got {no_ded.state_tax - with_ded.state_tax}"
        )

    def test_net_pay_end_to_end_with_pretax(
        self, simple_tax_configs
    ):
        """Full pipeline net pay with pre-tax deduction matches hand calc.

        This is the integration check: every component (gross, deductions,
        each tax type, net) is verified in one pass. If any upstream value
        is wrong, the net pay will not match.

        Net = gross - pre_tax - federal - state - SS - medicare - post_tax
            = 2307.69 - 200.00 - 153.08 - 94.85 - 143.08 - 33.46 - 0
            = $1,683.22
        """
        profile = FakeProfile(
            annual_salary=60000,
            deductions=[
                FakeDeduction(
                    name="401k", amount="200",
                    deduction_timing="pre_tax",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        r = calculate_paycheck(
            profile, period, [period], simple_tax_configs
        )

        # Verify every component individually.
        assert r.gross_biweekly == Decimal("2307.69")
        assert r.total_pre_tax == Decimal("200.00")
        assert r.taxable_income == Decimal("2107.69")
        assert r.federal_tax == Decimal("153.08")
        assert r.state_tax == Decimal("94.85")
        assert r.social_security == Decimal("143.08")
        assert r.medicare == Decimal("33.46")
        assert r.total_post_tax == Decimal("0")

        # Verify net pay matches the formula.
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
            f"net_pay {r.net_pay} != formula result {expected_net}"
        )
        assert r.net_pay == Decimal("1683.22"), (
            f"net_pay: expected 1683.22, got {r.net_pay}"
        )

    def test_pretax_deduction_in_higher_bracket_larger_reduction(
        self, simple_tax_configs
    ):
        """Pre-tax deduction in a higher bracket produces a larger tax savings.

        At $120k salary, the marginal bracket is 22% (income above $50k in
        the simple_bracket_set). A $500 pre-tax deduction at this income
        saves $500 * 22% = $110/period in federal tax, compared to
        $500 * 10% = $50/period at the $60k income level.

        This test verifies the calculator correctly applies marginal rates,
        not average rates, to the deduction amount.

        Without deduction ($120k):
          gross = 120000/26 = $4,615.38 (120000/26 = 4615.384615... -> 4615.38)
          annual = 4615.38 * 26 = $119,999.88
          taxable = 119,999.88 - 15,000 = $104,999.88
          tax: 50000*0.10 + 54999.88*0.22 = 5000 + 12099.97 = $17,099.97
          per_period = 17,099.97 / 26 = $657.69

        With $500 pre-tax deduction:
          annual_pre_tax = 500 * 26 = $13,000
          adjusted = 119,999.88 - 13,000 = $106,999.88
          taxable = 106,999.88 - 15,000 = $91,999.88
          tax: 50000*0.10 + 41999.88*0.22 = 5000 + 9239.97 = $14,239.97
          per_period = 14,239.97 / 26 = $547.69

        Reduction = 657.69 - 547.69 = $110.00 = $500 * 22%
        """
        no_ded_profile = FakeProfile(
            annual_salary=120000,
            created_at=date(2026, 1, 1),
        )
        with_ded_profile = FakeProfile(
            annual_salary=120000,
            deductions=[
                FakeDeduction(
                    name="401k", amount="500",
                    deduction_timing="pre_tax",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        no_ded = calculate_paycheck(
            no_ded_profile, period, [period], simple_tax_configs
        )
        with_ded = calculate_paycheck(
            with_ded_profile, period, [period], simple_tax_configs
        )

        assert no_ded.federal_tax == Decimal("657.69"), (
            f"No-deduction federal: expected 657.69, "
            f"got {no_ded.federal_tax}"
        )
        assert with_ded.federal_tax == Decimal("547.69"), (
            f"With-deduction federal: expected 547.69, "
            f"got {with_ded.federal_tax}"
        )

        # Reduction = $500 * 22% marginal bracket = $110.00
        reduction = no_ded.federal_tax - with_ded.federal_tax
        assert reduction == Decimal("110.00"), (
            f"Federal reduction: expected $110.00 "
            f"(= $500 * 22% marginal bracket), got {reduction}"
        )

        # FICA unchanged at higher income.
        assert with_ded.social_security == no_ded.social_security
        assert with_ded.medicare == no_ded.medicare


# ── Calibration Override Tests ───────────────────────────────────


class FakeCalibration:
    """Minimal stand-in for a CalibrationOverride."""

    def __init__(self, federal_rate, state_rate, ss_rate, medicare_rate,
                 is_active=True):
        self.effective_federal_rate = Decimal(str(federal_rate))
        self.effective_state_rate = Decimal(str(state_rate))
        self.effective_ss_rate = Decimal(str(ss_rate))
        self.effective_medicare_rate = Decimal(str(medicare_rate))
        self.is_active = is_active


class TestCalibrationIntegration:
    """Tests for calibration override integration in calculate_paycheck."""

    def test_calibrated_paycheck_uses_override_rates(
        self, simple_tax_configs
    ):
        """When calibration is active, taxes use effective rates, not brackets.

        Profile: $60,000 salary, no deductions.
        Gross biweekly = 60000/26 = $2,307.69
        Taxable = $2,307.69 (no pre-tax deductions)

        Calibrated rates:
          federal = 0.10000 -> 2307.69 * 0.10 = $230.77
          state = 0.05000 -> 2307.69 * 0.05 = $115.38
          ss = 0.06200 -> 2307.69 * 0.062 = $143.08
          medicare = 0.01450 -> 2307.69 * 0.0145 = $33.46
        """
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        result = calculate_paycheck(
            profile, period, [period], simple_tax_configs,
            calibration=cal,
        )

        assert result.federal_tax == Decimal("230.77")
        assert result.state_tax == Decimal("115.38")
        assert result.social_security == Decimal("143.08")
        assert result.medicare == Decimal("33.46")

        expected_net = (
            Decimal("2307.69")
            - Decimal("230.77")
            - Decimal("115.38")
            - Decimal("143.08")
            - Decimal("33.46")
        )
        assert result.net_pay == expected_net

    def test_calibrated_paycheck_differs_from_bracket_based(
        self, simple_tax_configs
    ):
        """Calibrated taxes differ from bracket-based for the same profile.

        This proves the calibration path is actually being used.
        """
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        # Bracket-based calculation.
        bracket_result = calculate_paycheck(
            profile, period, [period], simple_tax_configs,
        )

        # Calibrated with intentionally different rates.
        cal = FakeCalibration(
            federal_rate="0.15000",
            state_rate="0.03000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )
        cal_result = calculate_paycheck(
            profile, period, [period], simple_tax_configs,
            calibration=cal,
        )

        assert cal_result.federal_tax != bracket_result.federal_tax, (
            "Calibrated federal tax should differ from bracket-based"
        )
        assert cal_result.state_tax != bracket_result.state_tax, (
            "Calibrated state tax should differ from bracket-based"
        )

    def test_inactive_calibration_uses_brackets(
        self, simple_tax_configs
    ):
        """When calibration.is_active is False, bracket-based taxes are used."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        # Bracket-based (no calibration).
        bracket_result = calculate_paycheck(
            profile, period, [period], simple_tax_configs,
        )

        # Inactive calibration should be ignored.
        cal = FakeCalibration(
            federal_rate="0.50000",
            state_rate="0.50000",
            ss_rate="0.50000",
            medicare_rate="0.50000",
            is_active=False,
        )
        result = calculate_paycheck(
            profile, period, [period], simple_tax_configs,
            calibration=cal,
        )

        assert result.federal_tax == bracket_result.federal_tax
        assert result.state_tax == bracket_result.state_tax
        assert result.social_security == bracket_result.social_security
        assert result.medicare == bracket_result.medicare
        assert result.net_pay == bracket_result.net_pay

    def test_none_calibration_uses_brackets(
        self, simple_tax_configs
    ):
        """calibration=None (default) produces the same result as omitting it."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        result_omitted = calculate_paycheck(
            profile, period, [period], simple_tax_configs,
        )
        result_none = calculate_paycheck(
            profile, period, [period], simple_tax_configs,
            calibration=None,
        )

        assert result_omitted.net_pay == result_none.net_pay
        assert result_omitted.federal_tax == result_none.federal_tax

    def test_calibration_with_pretax_deductions(
        self, simple_tax_configs
    ):
        """Calibrated federal/state use taxable (gross - pre-tax), not gross.

        Profile: $60k, $200/paycheck 401k pre-tax.
        Gross = $2,307.69, taxable = $2,107.69
        federal rate 0.10 -> 2107.69 * 0.10 = $210.77
        ss rate 0.062 -> 2307.69 * 0.062 = $143.08 (gross, not taxable)
        """
        profile = FakeProfile(
            annual_salary=60000,
            deductions=[
                FakeDeduction(
                    name="401k", amount="200",
                    deduction_timing="pre_tax",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        result = calculate_paycheck(
            profile, period, [period], simple_tax_configs,
            calibration=cal,
        )

        # Federal uses taxable (2107.69), not gross (2307.69).
        assert result.federal_tax == Decimal("210.77")
        # SS uses gross.
        assert result.social_security == Decimal("143.08")

    def test_calibration_with_post_tax_deductions(
        self, simple_tax_configs
    ):
        """Post-tax deductions are still subtracted after calibrated taxes.

        If the code accidentally skips post-tax deductions when calibration
        is active, net pay would be too high.
        """
        profile = FakeProfile(
            annual_salary=60000,
            deductions=[
                FakeDeduction(
                    name="Roth", amount="150",
                    deduction_timing="post_tax",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        result = calculate_paycheck(
            profile, period, [period], simple_tax_configs,
            calibration=cal,
        )

        # Post-tax deduction of $150 must appear.
        assert result.total_post_tax == Decimal("150.00"), (
            f"Post-tax deduction missing: expected 150.00, "
            f"got {result.total_post_tax}"
        )
        # Net = gross - pre_tax(0) - federal - state - ss - medicare - post_tax
        gross = Decimal("2307.69")
        expected = (
            gross
            - Decimal("230.77")   # federal: 2307.69 * 0.10
            - Decimal("115.38")   # state: 2307.69 * 0.05
            - Decimal("143.08")   # ss: 2307.69 * 0.062
            - Decimal("33.46")    # medicare: 2307.69 * 0.0145
            - Decimal("150.00")   # post-tax Roth
        )
        assert result.net_pay == expected, (
            f"Net pay with post-tax: expected {expected}, got {result.net_pay}"
        )

    def test_calibration_with_mixed_deductions(
        self, simple_tax_configs
    ):
        """Pre-tax deductions reduce taxable base; post-tax deductions reduce net.

        Profile: $60k, $200 pre-tax 401k, $150 post-tax Roth.
        Gross = 2307.69, taxable = 2107.69
        federal = 2107.69 * 0.10 = 210.77 (uses taxable)
        ss = 2307.69 * 0.062 = 143.08 (uses gross)
        net = 2307.69 - 200 - 210.77 - 105.38 - 143.08 - 33.46 - 150 = 1465.00
        """
        profile = FakeProfile(
            annual_salary=60000,
            deductions=[
                FakeDeduction(name="401k", amount="200", deduction_timing="pre_tax"),
                FakeDeduction(name="Roth", amount="150", deduction_timing="post_tax"),
            ],
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 1, 16), period_id=1)

        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        result = calculate_paycheck(
            profile, period, [period], simple_tax_configs,
            calibration=cal,
        )

        assert result.taxable_income == Decimal("2107.69")
        assert result.federal_tax == Decimal("210.77")
        assert result.state_tax == Decimal("105.38")   # 2107.69 * 0.05
        assert result.social_security == Decimal("143.08")
        assert result.medicare == Decimal("33.46")
        assert result.total_pre_tax == Decimal("200.00")
        assert result.total_post_tax == Decimal("150.00")

        expected_net = (
            Decimal("2307.69")
            - Decimal("200.00")
            - Decimal("210.77")
            - Decimal("105.38")
            - Decimal("143.08")
            - Decimal("33.46")
            - Decimal("150.00")
        )
        assert result.net_pay == expected_net

    def test_calibration_on_third_paycheck(self, simple_tax_configs):
        """On a 3rd paycheck, 24-per-year deductions are skipped.

        This changes the taxable income and therefore the calibrated
        federal/state amounts.  The calibrated rates must be applied
        to the correct (higher) taxable base.
        """
        profile = FakeProfile(
            annual_salary=60000,
            deductions=[
                FakeDeduction(
                    name="401k", amount="200",
                    deduction_timing="pre_tax",
                    deductions_per_year=24,
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        # 3 periods in January to trigger 3rd paycheck detection.
        p1 = FakePeriod(start_date=date(2026, 1, 2), period_id=1)
        p2 = FakePeriod(start_date=date(2026, 1, 16), period_id=2)
        p3 = FakePeriod(start_date=date(2026, 1, 30), period_id=3)
        all_periods = [p1, p2, p3]

        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        # Non-3rd paycheck: deduction applies, taxable = 2307.69 - 200 = 2107.69
        normal = calculate_paycheck(
            profile, p1, all_periods, simple_tax_configs,
            calibration=cal,
        )
        assert normal.total_pre_tax == Decimal("200.00")
        assert normal.federal_tax == Decimal("210.77")  # 2107.69 * 0.10

        # 3rd paycheck: 24-per-year deduction is SKIPPED, taxable = 2307.69
        third = calculate_paycheck(
            profile, p3, all_periods, simple_tax_configs,
            calibration=cal,
        )
        assert third.is_third_paycheck is True
        assert third.total_pre_tax == Decimal("0.00"), (
            "24-per-year deduction should be skipped on 3rd paycheck"
        )
        assert third.federal_tax == Decimal("230.77"), (
            "3rd paycheck federal should be 2307.69 * 0.10 (full gross as taxable)"
        )
        # Higher taxable -> higher federal/state than normal paycheck.
        assert third.federal_tax > normal.federal_tax

    def test_calibration_does_not_bypass_gross_computation(
        self, simple_tax_configs
    ):
        """Calibration only overrides taxes, not gross or deductions.

        gross_biweekly, pre-tax deductions, post-tax deductions, and
        raise application must all work identically to the bracket path.
        """
        profile = FakeProfile(
            annual_salary=60000,
            raises=[
                FakeRaise(percentage="0.03", effective_month=1,
                          effective_year=2026),
            ],
            deductions=[
                FakeDeduction(name="401k", amount="200", deduction_timing="pre_tax"),
            ],
            created_at=date(2026, 1, 1),
        )
        period = FakePeriod(start_date=date(2026, 2, 13), period_id=2)

        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        cal_result = calculate_paycheck(
            profile, period, [period], simple_tax_configs,
            calibration=cal,
        )
        bracket_result = calculate_paycheck(
            profile, period, [period], simple_tax_configs,
        )

        # Gross, raises, and deductions must be identical.
        assert cal_result.gross_biweekly == bracket_result.gross_biweekly, (
            "Calibration must not affect gross computation"
        )
        assert cal_result.annual_salary == bracket_result.annual_salary, (
            "Calibration must not affect raise application"
        )
        assert cal_result.total_pre_tax == bracket_result.total_pre_tax, (
            "Calibration must not affect pre-tax deductions"
        )
        assert cal_result.taxable_income == bracket_result.taxable_income, (
            "Calibration must not affect taxable income"
        )

    def test_project_salary_uses_calibration(self, simple_tax_configs):
        """project_salary passes calibration to every period's calculation."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        periods = [
            FakePeriod(start_date=date(2026, 1, 16), period_id=1),
            FakePeriod(start_date=date(2026, 1, 30), period_id=2),
            FakePeriod(start_date=date(2026, 2, 13), period_id=3),
        ]

        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        # With calibration.
        cal_breakdowns = project_salary(
            profile, periods, simple_tax_configs, calibration=cal,
        )
        # Without calibration.
        bracket_breakdowns = project_salary(
            profile, periods, simple_tax_configs,
        )

        assert len(cal_breakdowns) == 3
        for i, (cb, bb) in enumerate(zip(cal_breakdowns, bracket_breakdowns)):
            assert cb.federal_tax != bb.federal_tax, (
                f"Period {i}: calibrated federal should differ from brackets"
            )
            assert cb.federal_tax == Decimal("230.77"), (
                f"Period {i}: expected 230.77 (2307.69 * 0.10)"
            )


class TestBiweeklyResidueDocstring:
    """Verify the $0.13 biweekly rounding residue is documented in docstrings.

    F-127 of the 2026-04-15 security audit classified the biweekly
    quantisation residue as an accepted simplification (real-payroll
    parity > residue elimination).  The closure condition is that the
    residue is acknowledged in module- and function-level docstrings so
    a future reader does not file a duplicate "bug" against
    well-understood behaviour, and so reconciliation work against W-2
    Box 1 has a discoverable explanation.

    Arithmetic context: $75,000 / 26 = $2,884.6153... -> $2,884.62
    (rounded half-up) * 26 = $75,000.12.  The residue (+$0.12) is
    bounded by 26 * $0.005 = $0.13/year.
    """

    def test_module_docstring_acknowledges_biweekly_residue(self):
        """Module docstring names the residue, its bound, and the audit ID.

        Asserts the literal phrase ``accepted simplification`` plus the
        ``$0.13`` bound plus the audit identifier ``F-127`` so wording
        drift breaks the test (the audit-closure language uses this
        exact phrasing).
        """
        from app.services import paycheck_calculator  # pylint: disable=import-outside-toplevel

        doc = paycheck_calculator.__doc__ or ""
        assert "accepted simplification" in doc.lower()
        assert "$0.13" in doc
        assert "F-127" in doc

    def test_calculate_paycheck_docstring_references_residue(self):
        """Function docstring on ``calculate_paycheck`` points at the residue.

        The function-level docstring must reference the rounding
        residue so a caller reading only the function signature in an
        IDE tooltip learns about the behaviour; relying solely on the
        module docstring leaves the discoverability gap that F-127
        flagged.
        """
        doc = calculate_paycheck.__doc__ or ""
        assert "residue" in doc.lower()
        assert "F-127" in doc
