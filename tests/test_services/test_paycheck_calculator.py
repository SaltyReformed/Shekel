"""
Shekel Budget App -- Unit Tests for Paycheck Calculator

Tests the recurring raise compounding logic in
paycheck_calculator.apply_raises() and the full calculate_paycheck()
pipeline including deductions, taxes, 3rd-paycheck detection, inflation,
cumulative wages, and project_salary().
"""

import pathlib
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import pytest

from app.services.exceptions import InvalidGrossPayError
from app.services.tax_calculator import calculate_fica
from app.services.paycheck_calculator import (
    apply_raises,
    _is_third_paycheck,
    _month_ordinal,
    _inflation_years,
    _get_cumulative_wages,
    _calculate_deductions,
    _DeductionContext,
    calculate_paycheck,
    project_salary,
    DeductionLine,
    DeductionBreakdown,
    Earnings,
    PaycheckBreakdown,
    PeriodInfo,
    TaxLines,
    ZERO,
)
from app import ref_cache
from app.enums import CalcMethodEnum, DeductionTimingEnum
from app.services.pay_calendar import (
    DerivedPeriod,
    PayCadence,
    PayCalendarError,
)
from app.services.payroll_basis import gross_per_paycheck
from tests._test_helpers import payroll_basis

#: The cent quantum these cases round their hand-computed expectations to.
#:
#: Defined HERE rather than imported from the engine since plan step
#: **balance:X-aw**, which deleted the module constant with the residue
#: distribution that was its only production reader.  A constant kept alive in
#: ``app/`` for tests alone is the speculative shape ``CLAUDE.md`` rule 13
#: forbids, and an expectation spelled independently of the code it grades is
#: the point of a test.
TWO_PLACES = Decimal("0.01")


def _timing_id(name):
    """Resolve a deduction timing name (e.g. 'pre_tax') to its integer ID."""
    _map = {e.value: e for e in DeductionTimingEnum}
    return ref_cache.deduction_timing_id(_map[name])


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


def _period(start_date, period_id=1):
    """A REAL :class:`DerivedPeriod` for the engine under test.

    It was a ``FakePeriod`` stand-in carrying ``start_date`` and ``id`` until
    pay-calendar plan step C2-f2d-3 moved this engine onto the derived
    calendar.  Building the production value rather than a duck type is what
    makes these cases grade the attributes the engine actually reads: a
    stand-in with the old ``id`` spelling would have kept passing while every
    production caller broke.

    ``end_date`` and ``period_index`` are filled to be internally consistent
    with a biweekly rhythm; the engine reads neither (AST-censused before the
    move -- ``start_date`` and the id are its whole period surface), so they
    are here because the frozen value requires them, not because anything
    under test consults them.
    """
    return DerivedPeriod(
        period_id=period_id,
        period_index=period_id - 1,
        start_date=start_date,
        end_date=start_date + timedelta(days=13),
        end_is_projected=False,
    )


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
                 inflation_effective_month=None, is_active=True,
                 annual_cap=None):
        self.name = name
        self.amount = Decimal(str(amount))
        self.deductions_per_year = deductions_per_year
        self.calc_method = FakeCalcMethod(calc_method)
        self.deduction_timing = FakeDeductionTiming(deduction_timing)
        self.inflation_enabled = inflation_enabled
        self.inflation_rate = Decimal(str(inflation_rate)) if inflation_rate else None
        self.inflation_effective_month = inflation_effective_month
        self.is_active = is_active
        # Calendar-year dollar ceiling (PaycheckDeduction.annual_cap); None =
        # uncapped.  Mirrors the real model column the calculator clamps on.
        self.annual_cap = Decimal(str(annual_cap)) if annual_cap is not None else None
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
    """Minimal stand-in for a SalaryProfile ORM object.

    **It carries no paycheck count**, and has not since plan step R-F16 dropped
    ``salary_profiles.pay_periods_per_year``.  How often the owner is paid
    reaches the engine on the :class:`PayrollBasis` beside the profile, which
    every call here builds through the shared ``payroll_basis`` helper.
    """

    def __init__(self, annual_salary, raises=None, deductions=None,
                 created_at=None,
                 additional_income=0, additional_deductions=0,
                 extra_withholding=0, qualifying_children=0,
                 other_dependents=0):
        self.annual_salary = Decimal(str(annual_salary))
        self.raises = raises or []
        self.deductions = deductions or []
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
    """26 derived pay periods for 2026, starting Jan 2."""
    start = date(2026, 1, 2)
    periods = []
    for i in range(26):
        d = date.fromordinal(start.toordinal() + i * 14)
        periods.append(_period(start_date=d, period_id=i + 1))
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
        period = _period(start_date=date(2026, 2, 1))
        result = apply_raises(profile.annual_salary, profile.raises, period.start_date)
        assert result == Decimal("100000.00")

    def test_recurring_raise_first_year_at_effective_month(self):
        """In effective year at effective month, raise should apply once."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = _period(start_date=date(2026, 3, 1))
        result = apply_raises(profile.annual_salary, profile.raises, period.start_date)
        # 100000 * 1.03 = 103000
        assert result == Decimal("103000.00")

    def test_recurring_raise_first_year_after_effective_month(self):
        """Later in effective year, raise should still apply once."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = _period(start_date=date(2026, 6, 1))
        result = apply_raises(profile.annual_salary, profile.raises, period.start_date)
        assert result == Decimal("103000.00")

    def test_recurring_raise_second_year_before_month(self):
        """Next year before effective month: still only 1 application."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = _period(start_date=date(2027, 1, 1))
        result = apply_raises(profile.annual_salary, profile.raises, period.start_date)
        # Only 1 full year passed (2027 - 2026 = 1), but month not reached
        assert result == Decimal("103000.00")

    def test_recurring_raise_second_year_after_month(self):
        """Next year after effective month: 2 total applications (compounded)."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = _period(start_date=date(2027, 4, 1))
        result = apply_raises(profile.annual_salary, profile.raises, period.start_date)
        # 100000 * 1.03 * 1.03 = 106090
        assert result == Decimal("106090.00")

    def test_recurring_raise_third_year(self):
        """Two years later after effective month: 3 total applications."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = _period(start_date=date(2028, 6, 1))
        result = apply_raises(profile.annual_salary, profile.raises, period.start_date)
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
        period = _period(start_date=date(2027, 6, 1))
        result = apply_raises(profile.annual_salary, profile.raises, period.start_date)
        assert result == Decimal("105000.00")

    def test_recurring_flat_raise(self):
        """Recurring flat raise should add the flat amount each year."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(flat_amount="5000", effective_month=1,
                              effective_year=2026, is_recurring=True)],
        )
        period = _period(start_date=date(2028, 6, 1))
        result = apply_raises(profile.annual_salary, profile.raises, period.start_date)
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
        period = _period(start_date=date(2027, 3, 1))
        result = apply_raises(profile.annual_salary, profile.raises, period.start_date)
        # 100000 * 1.03^2 = 106090
        assert result == Decimal("106090.00")


class TestRaiseOrdering:
    """Same-date raises apply flat-before-percentage, deterministically (M-01 / deep-hunt #12)."""

    def test_same_date_flat_applies_before_percentage(self):
        """A flat + percentage raise on one date apply flat-first, whatever the input order.

        Raise application is non-commutative, so the documented
        flat-before-percentage contract must hold regardless of the
        order the DB returns the rows in:
          flat-first:        (100000 + 5000) * 1.03 = 108150.00
          percentage-first:   100000 * 1.03 + 5000  = 108000.00
        The engine must always produce the flat-first value.
        """
        flat = FakeRaise(flat_amount="5000", effective_month=1, effective_year=2026)
        pct = FakeRaise(percentage="0.03", effective_month=1, effective_year=2026)
        as_of = date(2026, 6, 1)
        expected = Decimal("108150.00")  # (100000 + 5000) * 1.03

        # Both input orders must yield the flat-first result.  The
        # [pct, flat] order is the revert-proof case: without the method
        # tie-break the stable sort keeps percentage first -> 108000.00.
        assert apply_raises(Decimal("100000"), [flat, pct], as_of) == expected
        assert apply_raises(Decimal("100000"), [pct, flat], as_of) == expected


class TestChronologicalRaiseOrder:
    """Applications land in DATE order, not grouped by raise.

    Until this rule, ``apply_raises`` sorted the RAISES and then applied
    each one's whole run of yearly applications before starting the next.
    A percentage raise therefore multiplied flat dollars that had not
    arrived yet.  These cases grade the interleaving; the counts are
    unchanged, so an owner holding only percentage raises is unaffected,
    which the last case pins.
    """

    def test_a_percentage_does_not_compound_flat_dollars_that_arrive_later(self):
        """A recurring flat COLA and a recurring percentage merit interleave yearly.

        base 100,000; flat $1,500 and 4%, both recurring from 2026-01.
        Each year the flat lands, then the percentage multiplies what is
        there -- so the percentage never grows a dollar that arrives after
        it:
          2026: (100,000 + 1,500) * 1.04 = 105,560.00
          2027: (105,560 + 1,500) * 1.04 = 111,342.40
          2028: (111,342.40 + 1,500) * 1.04 = 117,356.10

        The old grouped order added all three $1,500s to the base first
        and compounded once: 104,500 * 1.04^3 = 117,548.29 at 2028, which
        credited three years of growth to dollars that had not arrived.
        """
        flat = FakeRaise(flat_amount="1500", effective_month=1,
                         effective_year=2026, is_recurring=True)
        pct = FakeRaise(percentage="0.04", effective_month=1,
                        effective_year=2026, is_recurring=True)
        base = Decimal("100000")
        assert apply_raises(base, [flat, pct], date(2026, 12, 1)) == Decimal("105560.00")
        assert apply_raises(base, [flat, pct], date(2027, 12, 1)) == Decimal("111342.40")
        assert apply_raises(base, [flat, pct], date(2028, 12, 1)) == Decimal("117356.10")

    def test_input_order_still_does_not_change_the_answer(self):
        """Reversing the input list changes nothing (M-01 determinism preserved).

        The ordering key is the application's date and method, never the
        row order, so the DB may return these two in either order.  Same
        2028 value as the case above.
        """
        flat = FakeRaise(flat_amount="1500", effective_month=1,
                         effective_year=2026, is_recurring=True)
        pct = FakeRaise(percentage="0.04", effective_month=1,
                        effective_year=2026, is_recurring=True)
        base = Decimal("100000")
        assert apply_raises(base, [pct, flat], date(2028, 12, 1)) == Decimal("117356.10")

    def test_a_one_time_raise_lands_on_its_own_date_not_last(self):
        """A mid-series one-time raise compounds only what preceded it.

        base 100,000; a recurring flat $1,000 from 2026-01 and a ONE-TIME
        10% on 2027-06.  In date order:
          2026-01 +1,000 -> 101,000
          2027-01 +1,000 -> 102,000
          2027-06 *1.10  -> 112,200
          2028-01 +1,000 -> 113,200.00

        The old order applied the flat's three additions first and then
        the 10%, reaching 113,300.00 -- the extra $100 being 10% of a
        2028 dollar credited in 2027.
        """
        flat = FakeRaise(flat_amount="1000", effective_month=1,
                         effective_year=2026, is_recurring=True)
        once = FakeRaise(percentage="0.10", effective_month=6,
                         effective_year=2027, is_recurring=False)
        result = apply_raises(Decimal("100000"), [flat, once], date(2028, 12, 1))
        assert result == Decimal("113200.00")

    def test_percentage_only_raises_are_completely_unaffected(self):
        """Ordering cannot matter when every raise is a percentage.

        Multiplication commutes, so regrouping the applications changes
        nothing -- which is why this rule moves no money for an owner
        whose raises are all percentages.  3% from 2026-01 and 2% from
        2026-07, at 2027-12: both have two applications, and
        ``100,000 * 1.03^2 * 1.02^2 = 110,376.04``.
        """
        merit = FakeRaise(percentage="0.03", effective_month=1,
                          effective_year=2026, is_recurring=True)
        cola = FakeRaise(percentage="0.02", effective_month=7,
                         effective_year=2026, is_recurring=True)
        result = apply_raises(Decimal("100000"), [merit, cola], date(2027, 12, 1))
        assert result == Decimal("110376.04")


# ── New Tests ────────────────────────────────────────────────────


class TestPaycheckBreakdownProperties:
    """Verify computed properties on the PaycheckBreakdown dataclass."""

    def test_total_pre_tax_sums_deductions(self):
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(period_id=1),
            earnings=Earnings(
                annual_salary=Decimal("60000"),
                gross_biweekly=Decimal("2307.69"),
            ),
            deductions=DeductionBreakdown(
                pre_tax=[
                    DeductionLine("401k", Decimal("200.00")),
                    DeductionLine("HSA", Decimal("50.00")),
                ],
            ),
        )
        assert breakdown.deductions.total_pre_tax == Decimal("250.00")

    def test_total_post_tax_sums_deductions(self):
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(period_id=1),
            earnings=Earnings(
                annual_salary=Decimal("60000"),
                gross_biweekly=Decimal("2307.69"),
            ),
            deductions=DeductionBreakdown(
                post_tax=[
                    DeductionLine("Roth IRA", Decimal("100.00")),
                    DeductionLine("Life Ins", Decimal("25.00")),
                ],
            ),
        )
        assert breakdown.deductions.total_post_tax == Decimal("125.00")

    def test_total_taxes_sums_all_tax_fields(self):
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(period_id=1),
            earnings=Earnings(
                annual_salary=Decimal("60000"),
                gross_biweekly=Decimal("2307.69"),
            ),
            taxes=TaxLines(
                federal=Decimal("200.00"),
                state=Decimal("80.00"),
                social_security=Decimal("143.08"),
                medicare=Decimal("33.46"),
            ),
        )
        assert breakdown.taxes.total == Decimal("456.54")

    def test_empty_deductions_return_zero(self):
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(period_id=1),
            earnings=Earnings(
                annual_salary=Decimal("60000"),
                gross_biweekly=Decimal("2307.69"),
            ),
        )
        assert breakdown.deductions.total_pre_tax == Decimal("0")
        assert breakdown.deductions.total_post_tax == Decimal("0")
        assert breakdown.taxes.total == Decimal("0")


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
        period = _period(
            start_date=date(2026, 1, 16), period_id=1
        )
        all_periods = [period]

        result = calculate_paycheck(
            payroll_basis(base_profile, all_periods), period,
            simple_tax_configs
        )

        assert result.earnings.annual_salary == Decimal("60000.00"), (
            f"annual_salary: expected 60000.00, "
            f"got {result.earnings.annual_salary}"
        )
        # 60000 / 26 = 2307.692307... -> 2307.69
        assert result.earnings.gross_biweekly == Decimal("2307.69"), (
            f"gross_biweekly: expected 2307.69, "
            f"got {result.earnings.gross_biweekly}"
        )
        # Pub 15-T: annual=59999.94, taxable=44999.94
        # 44999.94*0.10=4499.994->4499.99, /26=173.08
        assert result.taxes.federal == Decimal("173.08"), (
            f"federal_tax: expected 173.08, "
            f"got {result.taxes.federal}"
        )
        # state: 59999.94*0.045=2699.9973->2700.00
        # 2700.00/26=103.846...->103.85
        assert result.taxes.state == Decimal("103.85"), (
            f"state_tax: expected 103.85, "
            f"got {result.taxes.state}"
        )
        # SS: 2307.69*0.062=143.07678->143.08
        assert result.taxes.social_security == Decimal("143.08"), (
            f"social_security: expected 143.08, "
            f"got {result.taxes.social_security}"
        )
        # Medicare: 2307.69*0.0145=33.461505->33.46
        assert result.taxes.medicare == Decimal("33.46"), (
            f"medicare: expected 33.46, "
            f"got {result.taxes.medicare}"
        )
        # net = 2307.69 - 173.08 - 103.85 - 143.08 - 33.46
        assert result.earnings.net_pay == Decimal("1854.22"), (
            f"net_pay: expected 1854.22, "
            f"got {result.earnings.net_pay}"
        )

    def test_net_pay_formula(self, base_profile, simple_tax_configs):
        """net = gross - pre_tax - fed - state - ss - medicare - post_tax."""
        period = _period(start_date=date(2026, 1, 16), period_id=1)
        all_periods = [period]

        r = calculate_paycheck(
            payroll_basis(base_profile, all_periods), period,
            simple_tax_configs)

        # Hardcoded correctness anchor: base_profile=$60k salary,
        # same setup as test_basic_paycheck_no_deductions.
        # gross=2307.69 - fed=173.08 - state=103.85
        #   - SS=143.08 - med=33.46 = 1854.22
        assert r.earnings.net_pay == Decimal("1854.22"), (
            f"net_pay: expected 1854.22, got {r.earnings.net_pay}"
        )

        # Secondary: internal consistency check (formula holds).
        expected_net = (
            r.earnings.gross_biweekly
            - r.deductions.total_pre_tax
            - r.taxes.federal
            - r.taxes.state
            - r.taxes.social_security
            - r.taxes.medicare
            - r.deductions.total_post_tax
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        assert r.earnings.net_pay == expected_net, (
            f"Consistency check: net_pay={r.earnings.net_pay}, "
            f"formula result={expected_net}"
        )

    def test_gross_biweekly_calculation(self, simple_tax_configs):
        """annual / 26, quantized to 2 places."""
        profile = FakeProfile(annual_salary=75000, created_at=date(2026, 1, 1))
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs)

        expected_gross = (Decimal("75000") / 26).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        assert result.earnings.gross_biweekly == expected_gross

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
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs)

        assert result.earnings.taxable_income == ZERO

    def test_no_bracket_set_zero_federal(self, nc_state_config, standard_fica):
        """bracket_set=None → federal=0."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        period = _period(start_date=date(2026, 1, 16), period_id=1)
        configs = {
            "bracket_set": None,
            "state_config": nc_state_config,
            "fica_config": standard_fica,
        }

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            configs)

        assert result.taxes.federal == ZERO

    def test_no_state_config_zero_state(self, simple_bracket_set, standard_fica):
        """state_config=None → state=0."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        period = _period(start_date=date(2026, 1, 16), period_id=1)
        configs = {
            "bracket_set": simple_bracket_set,
            "state_config": None,
            "fica_config": standard_fica,
        }

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            configs)

        assert result.taxes.state == ZERO

    def test_no_fica_config_zero_fica(self, simple_bracket_set, nc_state_config):
        """fica_config=None → ss=0, medicare=0."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        period = _period(start_date=date(2026, 1, 16), period_id=1)
        configs = {
            "bracket_set": simple_bracket_set,
            "state_config": nc_state_config,
            "fica_config": None,
        }

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            configs)

        assert result.taxes.social_security == ZERO
        assert result.taxes.medicare == ZERO

    def test_all_tax_configs_none(self):
        """All None → only gross minus deductions."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        period = _period(start_date=date(2026, 1, 16), period_id=1)
        configs = {
            "bracket_set": None,
            "state_config": None,
            "fica_config": None,
        }

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            configs)

        assert result.taxes.federal == ZERO
        assert result.taxes.state == ZERO
        assert result.taxes.social_security == ZERO
        assert result.taxes.medicare == ZERO
        assert result.earnings.net_pay == result.earnings.gross_biweekly

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
        period = _period(
            start_date=date(2026, 1, 16), period_id=1
        )

        base = FakeProfile(
            annual_salary=60000,
            created_at=date(2026, 1, 1),
        )
        base_result = calculate_paycheck(
            payroll_basis(base, [period]), period,
            simple_tax_configs
        )

        with_w4 = FakeProfile(
            annual_salary=60000,
            created_at=date(2026, 1, 1),
            additional_income=10000,
            extra_withholding=50,
        )
        w4_result = calculate_paycheck(
            payroll_basis(with_w4, [period]), period,
            simple_tax_configs
        )

        # Base: 4499.99/26=173.076923->173.08
        assert base_result.taxes.federal == Decimal("173.08"), (
            f"base federal_tax: expected 173.08, "
            f"got {base_result.taxes.federal}"
        )
        # W-4: (6099.99/26)+50=234.615+50=284.615->284.62
        assert w4_result.taxes.federal == Decimal("284.62"), (
            f"w4 federal_tax: expected 284.62, "
            f"got {w4_result.taxes.federal}"
        )


class TestDeductionCalculation:
    """Tests for _calculate_deductions and deduction behavior in pipeline."""

    def test_flat_pre_tax_deduction(self, base_profile, simple_tax_configs):
        """Flat amount subtracted before taxes."""
        base_profile.deductions = [
            FakeDeduction(name="401k", amount="200", deduction_timing="pre_tax"),
        ]
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(base_profile, [period]), period,
            simple_tax_configs)

        assert len(result.deductions.pre_tax) == 1
        assert result.deductions.pre_tax[0].name == "401k"
        assert result.deductions.pre_tax[0].amount == Decimal("200.00")

    def test_flat_post_tax_deduction(self, base_profile, simple_tax_configs):
        """Flat amount subtracted after taxes."""
        base_profile.deductions = [
            FakeDeduction(name="Roth", amount="150", deduction_timing="post_tax"),
        ]
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(base_profile, [period]), period,
            simple_tax_configs)

        assert len(result.deductions.post_tax) == 1
        assert result.deductions.post_tax[0].amount == Decimal("150.00")

    def test_percentage_deduction(self, base_profile, simple_tax_configs):
        """Percentage of gross_biweekly."""
        base_profile.deductions = [
            FakeDeduction(name="401k", amount="0.06", calc_method="percentage",
                          deduction_timing="pre_tax"),
        ]
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(base_profile, [period]), period,
            simple_tax_configs)

        gross = Decimal("60000") / 26
        expected = (gross * Decimal("0.06")).quantize(TWO_PLACES,
                                                     rounding=ROUND_HALF_UP)
        assert result.deductions.pre_tax[0].amount == expected

    def test_inactive_deduction_skipped(self, base_profile, simple_tax_configs):
        """is_active=False excluded."""
        base_profile.deductions = [
            FakeDeduction(name="Old Plan", amount="200", is_active=False),
        ]
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(base_profile, [period]), period,
            simple_tax_configs)

        assert len(result.deductions.pre_tax) == 0

    def test_timing_filter(self, base_profile, simple_tax_configs):
        """Pre-tax deduction not in post-tax list and vice versa."""
        base_profile.deductions = [
            FakeDeduction(name="401k", amount="200", deduction_timing="pre_tax"),
            FakeDeduction(name="Roth", amount="100", deduction_timing="post_tax"),
        ]
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(base_profile, [period]), period,
            simple_tax_configs)

        pre_names = [d.name for d in result.deductions.pre_tax]
        post_names = [d.name for d in result.deductions.post_tax]
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
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)
        p2 = _period(start_date=date(2026, 1, 16), period_id=2)
        p3 = _period(start_date=date(2026, 1, 30), period_id=3)
        all_periods = [p1, p2, p3]

        gross = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                 rounding=ROUND_HALF_UP)
        result = _calculate_deductions(
            _DeductionContext(
                payroll_basis(profile, all_periods), p3, gross, 3,
            ),
            _timing_id("pre_tax"),
        )
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
        p1 = _period(start_date=date(2026, 2, 13), period_id=4)
        p2 = _period(start_date=date(2026, 2, 27), period_id=5)
        all_periods = [p1, p2]

        gross = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                 rounding=ROUND_HALF_UP)
        result = _calculate_deductions(
            _DeductionContext(
                payroll_basis(profile, all_periods), p1, gross, 1,
            ),
            _timing_id("pre_tax"),
        )
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
        p1 = _period(start_date=date(2026, 2, 13), period_id=4)
        p2 = _period(start_date=date(2026, 2, 27), period_id=5)
        all_periods = [p1, p2]

        gross = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                 rounding=ROUND_HALF_UP)
        result = _calculate_deductions(
            _DeductionContext(
                payroll_basis(profile, all_periods), p2, gross, 2,
            ),
            _timing_id("pre_tax"),
        )
        assert len(result) == 0

    # ── Commit 32 / MED-07 / PA-22: pct-of-zero-gross boundary ────

    def test_percentage_pre_tax_of_zero_gross_is_zero(self):
        """Percentage pre-tax deduction with gross_biweekly=0 yields 0.

        Pinning the PA-22 edge that 07_test_gaps Slice-3 / Concept 7 / 8
        flag as UNTESTED: a percentage deduction applied to a zero
        biweekly gross must produce a Decimal("0.00") line, never a
        negative or undefined value.  The amount is
            gross_biweekly * pct -> 0 * 0.06 = 0
        quantized HALF_UP to 0.00.  Asserting the exact edge value rather
        than just `len(result) == 1` proves the edge BEHAVIOR
        (testing-standards.md "Edge Case Tests").
        """
        profile = FakeProfile(
            annual_salary=60000, created_at=date(2026, 1, 1),
            deductions=[
                FakeDeduction(name="401k", amount="0.06",
                              calc_method="percentage",
                              deduction_timing="pre_tax"),
            ],
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = _calculate_deductions(
            _DeductionContext(
                payroll_basis(profile, [period]), period, Decimal("0.00"), 1,
            ),
            _timing_id("pre_tax"),
        )
        assert len(result) == 1
        assert result[0].name == "401k"
        assert result[0].amount == Decimal("0.00"), (
            f"Expected 0.00, got {result[0].amount}"
        )

    def test_percentage_post_tax_of_zero_gross_is_zero(self):
        """Percentage post-tax deduction with gross_biweekly=0 yields 0.

        Mirror of test_percentage_pre_tax_of_zero_gross_is_zero for the
        post-tax timing.  Both timings share the same parameterized
        producer (F-038/F-039 AGREE), so this is the post-side edge
        proof.  amount = 0 * 0.04 = 0, quantized HALF_UP to 0.00.
        """
        profile = FakeProfile(
            annual_salary=60000, created_at=date(2026, 1, 1),
            deductions=[
                FakeDeduction(name="Roth", amount="0.04",
                              calc_method="percentage",
                              deduction_timing="post_tax"),
            ],
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = _calculate_deductions(
            _DeductionContext(
                payroll_basis(profile, [period]), period, Decimal("0.00"), 1,
            ),
            _timing_id("post_tax"),
        )
        assert len(result) == 1
        assert result[0].name == "Roth"
        assert result[0].amount == Decimal("0.00"), (
            f"Expected 0.00, got {result[0].amount}"
        )


class TestDeductionAnnualCap:
    """``PaycheckDeduction.annual_cap`` throttles a deduction once its
    calendar-year total reaches the cap, then resumes the next January
    (deep-hunt #2 -- the cap was stored/validated/rendered but never
    enforced).  Each test would fail with the cap unenforced (the old
    behavior subtracted the full amount every period).
    """

    @staticmethod
    def _gross(annual="60000"):
        return (Decimal(annual) / 26).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    def _amounts_over(self, profile, periods, *, timing="pre_tax",
                      month_ordinal=1, history_opens_on=None):
        """Per-period deduction amount for the single deduction on ``profile``.

        ``month_ordinal`` is stated rather than derived so these cases grade
        the CAP and nothing else: every one of them uses a 26-per-year
        deduction, which is taken on every payday, so ordinal 1 makes the
        cadence arm a no-op and any zero in the result is the cap's doing.

        ``history_opens_on`` says how far back the owner's paychecks reach and
        defaults to the column's own ``None``, which since ruling
        **balance:R-IA**'s 2026-08-31 amendment means NOT STATED: the engine
        counts only the recorded paydays.  So every case here measures what it
        measured before plan step **balance:X-bh-2**, and the one case about
        the backward rhythm states its floor.
        """
        gross = self._gross(str(profile.annual_salary))
        amounts = []
        for p in periods:
            lines = _calculate_deductions(
                _DeductionContext(
                    payroll_basis(
                        profile, periods, history_opens_on=history_opens_on,
                    ),
                    p, gross, month_ordinal,
                ),
                _timing_id(timing),
            )
            amounts.append(lines[0].amount)
        return amounts

    def test_flat_deduction_clamps_to_cap_then_stops(self):
        """$600/period under a $1000 cap applies 600, 400, 0, 0."""
        profile = FakeProfile(
            annual_salary=60000, created_at=date(2026, 1, 1),
            deductions=[FakeDeduction(name="HSA", amount="600",
                                      annual_cap="1000")],
        )
        # Four 2026 periods; deductions_per_year defaults to 26 (no cadence
        # skip), so only the cap zeroes the later periods.
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
            _period(start_date=date(2026, 2, 13), period_id=4),
        ]
        # P1: YTD 0 -> full 600.  P2: YTD 600, room 400 -> 400 (lands on cap).
        # P3, P4: YTD 1200 >= cap -> 0.  Sum of applied == 1000 exactly.
        assert self._amounts_over(profile, periods) == [
            Decimal("600"), Decimal("400"), Decimal("0"), Decimal("0"),
        ]

    def test_cap_with_room_does_not_throttle(self):
        """A cap above the annual total leaves every period at full amount."""
        profile = FakeProfile(
            annual_salary=60000, created_at=date(2026, 1, 1),
            deductions=[FakeDeduction(name="401k", amount="200",
                                      annual_cap="100000")],
        )
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
        ]
        # 200/period never approaches a $100k cap -> unchanged.
        assert self._amounts_over(profile, periods) == [
            Decimal("200"), Decimal("200"),
        ]

    def _december_opening_profile(self):
        """A $600 deduction under a $1,000 cap, on a December-opening set."""
        return FakeProfile(
            annual_salary=60000, created_at=date(2026, 1, 1),
            deductions=[FakeDeduction(name="HSA", amount="600",
                                      annual_cap="1000")],
        ), [
            _period(start_date=date(2026, 12, 4), period_id=1),
            _period(start_date=date(2026, 12, 18), period_id=2),
            _period(start_date=date(2027, 1, 1), period_id=3),
        ]

    def test_cap_is_calendar_year_scoped_and_resets(self):
        """The cap resets each January: a new-year period starts fresh.

        Stated with NO pay history, which since ruling **balance:R-IA**'s
        2026-08-31 amendment is an owner nobody has asked -- so the engine
        counts only the three recorded paydays and the hand-computed figures
        below are unchanged by plan step balance:X-bh-2.  That the default is
        the SAFE one is the amendment's whole point, and this case is where it
        shows: a fixture that says nothing measures what it always measured.
        """
        profile, periods = self._december_opening_profile()

        # 2026 exhausts the cap (600 then 400); the 2027 period counts only
        # same-year prior periods (none), so the cap is fresh -> full 600.
        assert self._amounts_over(profile, periods) == [
            Decimal("600"), Decimal("400"), Decimal("600"),
        ]

    def test_a_STATED_history_exhausts_the_cap_BEFORE_the_record_opens(self):
        """What the backward rhythm changes for a cap, stated as money.

        Plan step **balance:X-bh-2**.  The same three periods for an owner who
        HAS said when their paychecks began: the rhythm runs back to that day,
        so by the time the record opens on 2026-12-04 they have already been
        paid 24 times that year and a $1,000 cap on a $600 deduction is long
        gone.  Both December periods clamp to zero and January resets.

        This is finding **N-390** priced on the deduction side: before this
        step the same owner was charged $1,000 of a cap they had already
        spent, because the year opened where the RECORD did rather than where
        they did.
        """
        profile, periods = self._december_opening_profile()

        # 2026-12-04 less 24 fortnights is 2026-01-02, so the year holds 24
        # rhythm paydays before the record opens -- 24 x $600 against a
        # $1,000 cap.
        assert (periods[0].start_date - date(2026, 1, 2)).days == 24 * 14
        assert self._amounts_over(
            profile, periods, history_opens_on=date(2025, 1, 1),
        ) == [Decimal("0"), Decimal("0"), Decimal("600")]

    def test_percentage_deduction_capped_on_dollar_total(self):
        """A percentage deduction is clamped on its cumulative dollar amount."""
        profile = FakeProfile(
            annual_salary=60000, created_at=date(2026, 1, 1),
            deductions=[FakeDeduction(name="401k", amount="0.10",
                                      calc_method="percentage",
                                      annual_cap="400")],
        )
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
        ]
        # gross 60000/26 = 2307.69; 10% = 230.77/period.  P1: 230.77.
        # P2: room 400-230.77 = 169.23 -> 169.23.  P3: cap exhausted -> 0.
        assert self._amounts_over(profile, periods) == [
            Decimal("230.77"), Decimal("169.23"), Decimal("0"),
        ]

    def test_capped_deduction_raises_net_pay_once_exhausted(self):
        """End-to-end: a capped-out period nets more than a pre-cap period.

        Proves the clamp flows through ``calculate_paycheck`` to net pay --
        the headline harm in deep-hunt #2 was understated net pay.
        """
        profile = FakeProfile(
            annual_salary=60000, created_at=date(2026, 1, 1),
            deductions=[FakeDeduction(name="HSA", amount="600",
                                      deduction_timing="post_tax",
                                      annual_cap="1000")],
        )
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
        ]
        configs = {"bracket_set": None, "state_config": None, "fica_config": None}
        nets = [
            calculate_paycheck(
            payroll_basis(profile, periods), p,
            configs).earnings.net_pay
            for p in periods
        ]
        # Post-tax deduction reduces net directly.  P1 takes 600, P2 takes the
        # capped 400, P3 takes 0 -> net rises as the deduction shrinks.
        assert nets[1] - nets[0] == Decimal("200.00")  # 600 - 400 deducted
        assert nets[2] - nets[1] == Decimal("400.00")  # 400 - 0 deducted


class TestThirdPaycheckDetection:
    """Tests for ``_month_ordinal`` and the ``_is_third_paycheck`` rule on it.

    They were two functions each scanning a caller-supplied period list until
    plan step **balance:X-bh-1** -- ``_is_third_paycheck(period, all_periods)``
    and ``_is_first_paycheck_of_month(period, all_periods)``.  Both asked one
    question of one payday, so both are now the ORDINAL, counted off the
    owner's calendar, and the cases below grade the number and the two
    predicates that read it.
    """

    def test_month_with_two_paychecks_places_the_second(self):
        """Standard month with 2 paychecks: the second is ordinal 2."""
        p1 = _period(start_date=date(2026, 2, 13), period_id=1)
        p2 = _period(start_date=date(2026, 2, 27), period_id=2)
        basis = payroll_basis(FakeProfile(annual_salary=60000), [p1, p2])

        assert _month_ordinal(basis.calendar, p2.start_date) == 2
        assert _is_third_paycheck(_month_ordinal(basis.calendar, p2.start_date)) is False

    def test_third_paycheck_of_the_month_is_ordinal_three(self):
        """Month with 3 paydays: the third is ordinal 3 and reads as third."""
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)
        p2 = _period(start_date=date(2026, 1, 16), period_id=2)
        p3 = _period(start_date=date(2026, 1, 30), period_id=3)
        basis = payroll_basis(FakeProfile(annual_salary=60000), [p1, p2, p3])

        assert _month_ordinal(basis.calendar, p3.start_date) == 3
        assert _is_third_paycheck(_month_ordinal(basis.calendar, p3.start_date)) is True

    def test_first_period_of_a_three_paycheck_month_is_ordinal_one(self):
        """The first payday of a 3-paycheck month is not a 3rd paycheck."""
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)
        p2 = _period(start_date=date(2026, 1, 16), period_id=2)
        p3 = _period(start_date=date(2026, 1, 30), period_id=3)
        basis = payroll_basis(FakeProfile(annual_salary=60000), [p1, p2, p3])

        assert _month_ordinal(basis.calendar, p1.start_date) == 1
        assert _is_third_paycheck(_month_ordinal(basis.calendar, p1.start_date)) is False

    def test_a_neighbouring_month_does_not_count(self):
        """Only the payday's OWN calendar month is counted.

        The two January paydays sit within a cadence of the February one, so a
        rule that counted a window rather than a month would place the
        February payday at 3 and skip a 24-per-year deduction on it.
        """
        jan1 = _period(start_date=date(2026, 1, 2), period_id=1)
        jan2 = _period(start_date=date(2026, 1, 16), period_id=2)
        jan3 = _period(start_date=date(2026, 1, 30), period_id=3)
        feb = _period(start_date=date(2026, 2, 13), period_id=4)
        basis = payroll_basis(
            FakeProfile(annual_salary=60000), [jan1, jan2, jan3, feb],
        )

        assert _month_ordinal(basis.calendar, feb.start_date) == 1

    def test_a_payday_past_the_horizon_is_placed_by_the_cadence(self):
        """A projected payday is counted, not answered as if it stood alone.

        The calendar holds 2026-01-02 and 2026-01-16 only, so its horizon is
        2026-01-29 and every later payday is projected at the 14-day cadence:
        01-30, 02-13, 02-27, 03-13, 03-27.  Counting only SAVED paydays would
        place BOTH March paydays at 0 -- so a 24-per-year deduction could
        never be skipped past the horizon and a 12-per-year one could never be
        taken.  The count projects forward instead (``pay_calendar:R-PC9``).
        """
        jan1 = _period(start_date=date(2026, 1, 2), period_id=1)
        jan2 = _period(start_date=date(2026, 1, 16), period_id=2)
        basis = payroll_basis(FakeProfile(annual_salary=60000), [jan1, jan2])
        # Built directly rather than through ``_period``: a period past the
        # horizon carries ``period_id = None``, which is exactly what marks it
        # unmaterialised, and that helper numbers its index off the id.
        first_of_march = DerivedPeriod(
            period_id=None, period_index=6, start_date=date(2026, 3, 13),
            end_date=date(2026, 3, 26), end_is_projected=True,
        )
        second_of_march = DerivedPeriod(
            period_id=None, period_index=7, start_date=date(2026, 3, 27),
            end_date=date(2026, 4, 9), end_is_projected=True,
        )

        assert _month_ordinal(basis.calendar, first_of_march.start_date) == 1
        assert _month_ordinal(basis.calendar, second_of_march.start_date) == 2

    def test_a_payday_below_the_opening_is_counted_for_a_STATED_owner(self):
        """The rhythm runs backwards too, which is plan step balance:X-bh-2.

        The calendar opens 2026-01-16, so the January payday two weeks before
        it -- 2026-01-02 -- is one the owner was really paid on and the app
        holds no row for.  It is counted once they SAY their paychecks began
        earlier: the opening payday then reads 2 and the one after it reads 3,
        so a 24-per-year deduction is SKIPPED on 2026-01-30 where before this
        step it was taken, and a 12-per-year one stops being taken on
        2026-01-16.  That is ledger row **N-390** on the month side.
        """
        opening = _period(start_date=date(2026, 1, 16), period_id=1)
        later = _period(start_date=date(2026, 1, 30), period_id=2)
        basis = payroll_basis(
            FakeProfile(annual_salary=60000), [opening, later],
            history_opens_on=date(2025, 1, 1),
        )

        assert _month_ordinal(basis.calendar, opening.start_date) == 2
        assert _month_ordinal(basis.calendar, later.start_date) == 3

    def test_an_UNSTATED_owner_counts_only_the_record(self):
        """THE CONTROL, and the reading every owner starts at.

        The same two paydays for somebody nobody has asked: nothing runs below
        the record, so the ordinals are 1 and 2 -- exactly what this calendar
        answered for every owner before plan step balance:X-bh-2.  ``NULL`` is
        an absence, not a claim (ruling **balance:R-IA** as amended
        2026-08-31), and the safe direction for an absence is to count less.
        """
        opening = _period(start_date=date(2026, 1, 16), period_id=1)
        later = _period(start_date=date(2026, 1, 30), period_id=2)
        basis = payroll_basis(FakeProfile(annual_salary=60000),
                              [opening, later])

        assert _month_ordinal(basis.calendar, opening.start_date) == 1
        assert _month_ordinal(basis.calendar, later.start_date) == 2

    def test_a_floor_ON_the_opening_payday_also_counts_only_the_record(self):
        """The owner whose first paycheck IS the first one recorded.

        Distinct from the case above in what it MEANS -- a statement rather
        than an absence -- and identical in what it answers, which is why both
        stand: ``pay_calendar:R-PC14`` calls this owner ordinary, and the
        engine must not tell them apart.
        """
        opening = _period(start_date=date(2026, 1, 16), period_id=1)
        later = _period(start_date=date(2026, 1, 30), period_id=2)
        basis = payroll_basis(
            FakeProfile(annual_salary=60000), [opening, later],
            history_opens_on=opening.start_date,
        )

        assert _month_ordinal(basis.calendar, opening.start_date) == 1
        assert _month_ordinal(basis.calendar, later.start_date) == 2


class TestTheEngineRefusesAPaycheckItCannotPlace:
    """The PUBLIC door refuses a payday off the owner's rhythm, from a caller's shape.

    ``_month_ordinal``'s refusal is graded directly in
    ``test_pay_calendar_rhythm``; this grades it where a caller meets it --
    through :func:`calculate_paycheck` -- and it exists because the argument
    that the refusal is unreachable is an ONLY-WAY argument.

    Every one of the 13 call sites in ``app/`` draws its period from the same
    calendar it prices against, so none can reach this today.  That claim is
    one unenumerated writer from false, and plan step **balance:X-bh-1** made
    it expensive rather than cheap to be wrong about: the engine used to return
    a confident number for a payday it could not place and now RAISES, so a
    14th caller -- a period built from a form value, a saved template, a
    backfill script -- meets a ``PayCalendarError`` on a money path instead of
    a wrong figure.  This case is that caller, written from their shape.  If
    one ever appears for real, it fails HERE rather than 500ing in production.

    **Plan step balance:X-bh-2 narrowed WHAT is unplaceable and did not remove
    the refusal**, which is why these cases changed shape rather than going
    away.  A day BELOW the opening payday used to be unplaceable by
    construction; the rhythm now runs backward, so such a day is placed when
    it falls on the rhythm and inside the owner's stated history.  Two kinds
    remain: a day OFF the rhythm's phase -- another owner's payday, or one
    assembled by hand -- and a day below a stated ``history_opens_on``.

    Measured on the developer's own schedule (opening 2026-03-26): the payday
    2026-03-12, which he really was paid on and the app holds no row for,
    answered ``net $2,454.10`` at plan step X-bh-1's ancestor, then RAISED at
    X-bh-1, and is priced correctly as March's first paycheck now.  The last
    case below is that day.
    """

    @staticmethod
    def _biweekly_from_march():
        """The developer's shape: six biweekly paydays opening 2026-03-26."""
        return [
            _period(start_date=date(2026, 3, 26) + timedelta(days=14 * i),
                    period_id=i + 1)
            for i in range(6)
        ]

    def test_a_payday_OFF_the_rhythm_is_refused_not_priced(
        self, simple_tax_configs,
    ):
        """A period built by hand, one day out of phase, raises.

        2026-03-13 is a day nobody on this schedule is paid on: the rhythm
        runs 2026-03-12, 03-26, 04-09 in both directions from the record, and
        a day between two paydays has no position in its month to answer with.
        This is the shape a form value or another owner's period has.
        """
        profile = FakeProfile(
            annual_salary=91675, created_at=date(2026, 1, 1),
            deductions=[FakeDeduction(name="Health", amount="500",
                                      deductions_per_year=24)],
        )
        basis = payroll_basis(profile, self._biweekly_from_march())
        unheld = _period(start_date=date(2026, 3, 13), period_id=99)

        with pytest.raises(PayCalendarError, match="is not paid on"):
            calculate_paycheck(basis, unheld, simple_tax_configs)

    def test_a_payday_below_a_STATED_opening_is_refused_not_priced(
        self, simple_tax_configs,
    ):
        """On the rhythm, below the floor: the owner says they were not paid then.

        The other half of what stays unplaceable after plan step
        balance:X-bh-2.  2026-03-12 IS on this schedule's rhythm, so it is
        priced for an owner who has stated nothing -- but an owner whose
        paychecks began on 2026-03-26 was not paid on it, and answering a
        position anyway would put a 12-per-year deduction on a paycheck that
        never happened.
        """
        profile = FakeProfile(annual_salary=91675, created_at=date(2026, 1, 1))
        schedule = self._biweekly_from_march()
        basis = payroll_basis(
            profile, schedule, history_opens_on=date(2026, 3, 26),
        )
        unheld = _period(start_date=date(2026, 3, 12), period_id=99)

        with pytest.raises(PayCalendarError, match="is not paid on"):
            calculate_paycheck(basis, unheld, simple_tax_configs)

    def test_the_refusal_names_the_day_so_a_traceback_is_actionable(
        self, simple_tax_configs,
    ):
        """The message carries the payday and the owner, not just a type.

        A refusal a reader cannot act on sends them to the wrong cause, which
        is what the message this asserts exists to prevent.
        """
        profile = FakeProfile(annual_salary=91675, created_at=date(2026, 1, 1))
        basis = payroll_basis(profile, self._biweekly_from_march())
        unheld = _period(start_date=date(2026, 3, 13), period_id=99)

        with pytest.raises(PayCalendarError) as caught:
            calculate_paycheck(basis, unheld, simple_tax_configs)

        message = str(caught.value)
        assert "2026-03-13" in message
        assert "user 1" in message

    def test_a_payday_the_calendar_DOES_hold_is_priced(
        self, simple_tax_configs,
    ):
        """THE CONTROL: the refusal fires on the mismatch, not on every call.

        Without this, both cases above would pass against an engine that
        refused everything.
        """
        profile = FakeProfile(annual_salary=91675, created_at=date(2026, 1, 1))
        schedule = self._biweekly_from_march()
        basis = payroll_basis(profile, schedule)

        result = calculate_paycheck(basis, schedule[0], simple_tax_configs)

        # $91,675 / 26 = $3,525.96, the rate ruling balance:R-HW fixed.
        assert result.earnings.gross_biweekly == Decimal("3525.96")

    def test_the_owners_real_unrecorded_payday_is_PRICED_once_he_STATES_it(
        self, simple_tax_configs,
    ):
        """THE SECOND CONTROL, and it is what plan step balance:X-bh-2 bought.

        2026-03-12 is a day the developer really was paid on and the app holds
        no row for -- the exact day X-bh-1's refusal named.  Once he says his
        paychecks began earlier it is on the rhythm, so it prices at the same
        rate as a recorded paycheck AND takes March's first position, which is
        the one a 12-per-year deduction is charged on.

        Paired with the two refusals above, this is what separates "the rhythm
        reaches here" from "the engine answers anything": one day apart, 03-12
        prices and 03-13 raises.
        """
        profile = FakeProfile(annual_salary=91675, created_at=date(2026, 1, 1))
        schedule = self._biweekly_from_march()
        basis = payroll_basis(
            profile, schedule, history_opens_on=date(2025, 1, 1),
        )
        unrecorded = _period(start_date=date(2026, 3, 12), period_id=99)

        result = calculate_paycheck(basis, unrecorded, simple_tax_configs)

        assert result.earnings.gross_biweekly == Decimal("3525.96")
        assert _month_ordinal(basis.calendar, unrecorded.start_date) == 1

    def test_the_same_day_still_RAISES_for_an_owner_who_stated_nothing(
        self, simple_tax_configs,
    ):
        """The amendment, graded at the door a caller meets.

        ``NULL`` is not a claim, so an owner nobody has asked has no rhythm
        below their record and 2026-03-12 is unplaceable for them -- exactly
        as it was at plan step balance:X-bh-1, and for the same reason: the
        engine would otherwise answer a confident number for a paycheck it has
        no basis to place.  This is the pair that makes the case above about
        the STATED fact rather than about the day.
        """
        profile = FakeProfile(annual_salary=91675, created_at=date(2026, 1, 1))
        basis = payroll_basis(profile, self._biweekly_from_march())
        unrecorded = _period(start_date=date(2026, 3, 12), period_id=99)

        with pytest.raises(PayCalendarError, match="is not paid on"):
            calculate_paycheck(basis, unrecorded, simple_tax_configs)


class TestFirstPaycheckOfMonth:
    """The 12-per-year deduction cadence, which is ordinal 1 and only that."""

    def test_first_period_in_month_is_ordinal_one(self):
        p1 = _period(start_date=date(2026, 3, 6), period_id=1)
        p2 = _period(start_date=date(2026, 3, 20), period_id=2)
        basis = payroll_basis(FakeProfile(annual_salary=60000), [p1, p2])

        assert _month_ordinal(basis.calendar, p1.start_date) == 1

    def test_second_period_in_month_is_not_ordinal_one(self):
        p1 = _period(start_date=date(2026, 3, 6), period_id=1)
        p2 = _period(start_date=date(2026, 3, 20), period_id=2)
        basis = payroll_basis(FakeProfile(annual_salary=60000), [p1, p2])

        assert _month_ordinal(basis.calendar, p2.start_date) == 2


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
        period = _period(start_date=date(2026, 6, 1), period_id=1)

        years = _inflation_years(period.start_date, profile, 1)
        assert years == 1

        # Verify in deduction calculation
        gross = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                 rounding=ROUND_HALF_UP)
        result = _calculate_deductions(
            _DeductionContext(
                payroll_basis(profile, [period]), period, gross, 1,
            ),
            _timing_id("pre_tax"),
        )
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
        period = _period(start_date=date(2026, 6, 1), period_id=1)

        years = _inflation_years(period.start_date, profile, 1)
        assert years == 2

        gross = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                 rounding=ROUND_HALF_UP)
        result = _calculate_deductions(
            _DeductionContext(
                payroll_basis(profile, [period]), period, gross, 1,
            ),
            _timing_id("pre_tax"),
        )
        expected = (Decimal("100") * Decimal("1.03") ** 2).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        assert result[0].amount == expected

    def test_inflated_percentage_rounds_once_at_return(self):
        """E-26(a): full precision through pct x inflation, ONE rounding.

        gross 1000.49, 5% deduction, 3% inflation, 1 year:
          exact: 1000.49 * 0.05 = 50.0245; * 1.03 = 51.525235
          -> round once HALF_UP -> 51.53.
        The pre-ratification double-quantize gave 51.52 (50.0245 ->
        50.02 first, then 50.02 * 1.03 = 51.5206 -> 51.52) -- the
        intermediate quantize ate the half-cent the full-precision
        product carries.  Ratified 2026-06-11 ("quantize once at
        return"); this pin fails under a restored double-quantize.
        """
        profile = FakeProfile(
            annual_salary=60000, created_at=date(2025, 1, 1),
            deductions=[
                FakeDeduction(name="401k", amount="0.05",
                              calc_method="percentage",
                              inflation_enabled=True, inflation_rate="0.03",
                              inflation_effective_month=1),
            ],
        )
        period = _period(start_date=date(2026, 6, 1), period_id=1)
        result = _calculate_deductions(
            _DeductionContext(
                payroll_basis(profile, [period]), period, Decimal("1000.49"), 1,
            ),
            _timing_id("pre_tax"),
        )
        assert result[0].amount == Decimal("51.53")

    def test_flat_four_decimal_amount_rounds_to_cents(self):
        """E-26(a): a 4dp flat amount rounds once at the line boundary.

        The column is Numeric(12, 4), so a flat deduction can carry
        sub-cent precision (schema places=4).  Pre-ratification the raw
        4dp value flowed UNQUANTIZED into the taxable/net math while
        the line displayed at 2dp -- a sub-cent display-vs-math
        divergence.  500.1234 -> 500.12 (HALF_UP): the displayed line
        now equals the amount actually subtracted.
        """
        profile = FakeProfile(
            annual_salary=60000, created_at=date(2025, 1, 1),
            deductions=[FakeDeduction(name="HSA", amount="500.1234")],
        )
        period = _period(start_date=date(2026, 6, 1), period_id=1)
        result = _calculate_deductions(
            _DeductionContext(
                payroll_basis(profile, [period]), period, Decimal("2307.69"), 1,
            ),
            _timing_id("pre_tax"),
        )
        assert result[0].amount == Decimal("500.12")

    def test_before_effective_month_reduces_years(self):
        """period_month < eff_month → years - 1."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2024, 1, 1))
        # Period is in March, effective month is June
        period = _period(start_date=date(2026, 3, 1), period_id=1)

        years = _inflation_years(period.start_date, profile, 6)
        # 2026 - 2024 = 2, but month 3 < 6 → 2 - 1 = 1
        assert years == 1

    def test_created_at_none_zero_years(self):
        """profile.created_at=None → no inflation."""
        profile = FakeProfile(annual_salary=60000, created_at=None)
        period = _period(start_date=date(2026, 6, 1), period_id=1)

        years = _inflation_years(period.start_date, profile, 1)
        assert years == 0

    def test_same_year_as_creation_zero_years(self):
        """Year 0 = no inflation."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        period = _period(start_date=date(2026, 6, 1), period_id=1)

        years = _inflation_years(period.start_date, profile, 1)
        assert years == 0


class TestCumulativeWages:
    """Tests for _get_cumulative_wages()."""

    def test_sums_prior_periods_in_same_year(self):
        """Adds gross for earlier periods."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)
        p2 = _period(start_date=date(2026, 1, 16), period_id=2)
        p3 = _period(start_date=date(2026, 1, 30), period_id=3)
        all_periods = [p1, p2, p3]

        result = _get_cumulative_wages(payroll_basis(profile, all_periods), p3)

        gross_per = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                     rounding=ROUND_HALF_UP)
        assert result == gross_per * 2

    def test_first_period_zero_cumulative(self):
        """First period → 0."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)

        result = _get_cumulative_wages(payroll_basis(profile, [p1]), p1)
        assert result == ZERO

    def test_different_year_periods_excluded(self):
        """Prior year periods skipped."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2025, 1, 1))
        p_prev = _period(start_date=date(2025, 12, 19), period_id=25)
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)
        all_periods = [p_prev, p1]

        result = _get_cumulative_wages(payroll_basis(profile, all_periods), p1)
        assert result == ZERO


class TestProjectSalary:
    """Tests for project_salary()."""

    def test_returns_one_breakdown_per_period(self, base_profile,
                                              simple_tax_configs):
        """len(result) == len(periods)."""
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
        ]

        result = project_salary(payroll_basis(base_profile, periods), periods, simple_tax_configs)

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
            _period(start_date=date(2026, 2, 13), period_id=1),
            _period(start_date=date(2026, 3, 13), period_id=2),
            _period(start_date=date(2026, 4, 10), period_id=3),
        ]

        result = project_salary(payroll_basis(profile, periods), periods, simple_tax_configs)

        assert result[0].period.raise_event == ""
        assert "MERIT" in result[1].period.raise_event
        assert result[2].period.raise_event == ""

    def test_recurring_raise_event_not_shown_before_effective_year(self, simple_tax_configs):
        """A recurring raise badges no event in years before its effective_year (deep-hunt #13).

        A recurring raise effective March 2027 must not show a raise
        event on the March 2026 paycheck (whose gross is unchanged), and
        must show one once it actually recurs in March 2027.  The pre-fix
        recurring branch matched on month alone, badging "MERIT" in 2026
        while apply_raises applied nothing.
        """
        profile = FakeProfile(
            annual_salary=60000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2027, is_recurring=True)],
            created_at=date(2026, 1, 1),
        )
        # Before the effective year: no event anywhere in 2026, salary flat.
        periods_2026 = [
            _period(start_date=date(2026, 2, 13), period_id=1),
            _period(start_date=date(2026, 3, 13), period_id=2),
            _period(start_date=date(2026, 4, 10), period_id=3),
        ]
        result_2026 = project_salary(payroll_basis(profile, periods_2026), periods_2026, simple_tax_configs)
        assert all(r.period.raise_event == "" for r in result_2026)
        assert result_2026[1].earnings.annual_salary == Decimal("60000.00")

        # In the effective year the raise recurs at its month: the event
        # shows at March 2027 and is absent the month before, so the fix
        # gates on the year without over-suppressing the legitimate event.
        periods_2027 = [
            _period(start_date=date(2027, 2, 12), period_id=27),
            _period(start_date=date(2027, 3, 12), period_id=28),
        ]
        result_2027 = project_salary(payroll_basis(profile, periods_2027), periods_2027, simple_tax_configs)
        assert result_2027[0].period.raise_event == ""
        assert "MERIT" in result_2027[1].period.raise_event
        assert result_2027[1].earnings.annual_salary == Decimal("61800.00")  # 60000 * 1.03

    def test_empty_periods_empty_result(self, base_profile, simple_tax_configs):
        """[] → []."""
        result = project_salary(
            payroll_basis(base_profile, []), [], simple_tax_configs,
        )
        assert result == []

    def test_configs_by_year_applies_each_periods_own_year(
        self, base_profile, simple_bracket_set, standard_fica,
    ):
        """DH-#30: in configs_by_year mode each period uses its own year's configs.

        Same $60k profile and identical gross both years, but 2027 carries
        a higher state flat rate (9.0%) than 2026 (4.5%), so the 2027
        period's net pay must be strictly lower -- proving the per-period
        year selection, not a single shared config set, drives the tax.
        Revert-proof: applying a single dict to both periods would make the
        two nets equal.
        """
        configs_by_year = {
            2026: {
                "bracket_set": simple_bracket_set,
                "state_config": FakeStateTaxConfig(flat_rate="0.045"),
                "fica_config": standard_fica,
            },
            2027: {
                "bracket_set": simple_bracket_set,
                "state_config": FakeStateTaxConfig(flat_rate="0.090"),
                "fica_config": standard_fica,
            },
        }
        periods = [
            _period(start_date=date(2026, 6, 5), period_id=1),
            _period(start_date=date(2027, 6, 4), period_id=2),
        ]

        result = project_salary(
            payroll_basis(base_profile, periods), periods, configs_by_year=configs_by_year,
        )

        assert result[0].earnings.gross_biweekly == result[1].earnings.gross_biweekly
        assert result[1].earnings.net_pay < result[0].earnings.net_pay

    def test_requires_exactly_one_config_source(
        self, base_profile, simple_tax_configs,
    ):
        """ValueError when neither or both config sources are supplied."""
        periods = [_period(start_date=date(2026, 1, 2), period_id=1)]
        with pytest.raises(ValueError, match="exactly one"):
            project_salary(payroll_basis(base_profile, periods), periods)
        with pytest.raises(ValueError, match="exactly one"):
            project_salary(
            payroll_basis(base_profile, periods), periods, simple_tax_configs,
                configs_by_year={2026: simple_tax_configs},
            )


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
            payroll_basis(profile, biweekly_periods), biweekly_periods, simple_tax_configs
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
            assert results[i].taxes.social_security == full_ss, (
                f"period {i+1}: SS expected {full_ss}, "
                f"got {results[i].taxes.social_security}"
            )

        # Period 22: partial SS (crosses cap this period)
        # cumulative = 21*7692.31 = 161538.51
        # cumul + gross = 169230.82 > 168600
        assert results[21].taxes.social_security == partial_ss, (
            f"period 22 (transition): SS expected "
            f"{partial_ss}, got {results[21].taxes.social_security}"
        )

        # Periods 23-26: zero SS (already over cap)
        for i in range(22, 26):
            assert results[i].taxes.social_security == Decimal("0.00"), (
                f"period {i+1}: SS expected 0.00, "
                f"got {results[i].taxes.social_security}"
            )

        # Medicare: constant across all 26 periods (no cap)
        for i in range(26):
            assert results[i].taxes.medicare == expected_medicare, (
                f"period {i+1}: medicare expected "
                f"{expected_medicare}, "
                f"got {results[i].taxes.medicare}"
            )

        # Cumulative SS verification
        # 21*476.92 + 437.81 + 4*0.00 = 10453.13
        total_ss = sum(
            r.taxes.social_security for r in results
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
            payroll_basis(profile, biweekly_periods), biweekly_periods, simple_tax_configs
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
            assert results[i].taxes.medicare == base_med, (
                f"period {i+1}: medicare expected "
                f"{base_med}, got {results[i].taxes.medicare}"
            )

        # Period 18: partial surtax (crosses threshold)
        # cumul = 17*11538.46 = 196153.82
        # surtax_income = 207692.28 - 200000 = 7692.28
        # surtax = 7692.28*0.009 = 69.23052->69.23
        assert results[17].taxes.medicare == trans_med, (
            f"period 18 (transition): medicare expected "
            f"{trans_med}, got {results[17].taxes.medicare}"
        )

        # Periods 19-26: full surtax (cumul >= threshold)
        # surtax = 11538.46*0.009 = 103.84614->103.85
        for i in range(18, 26):
            assert results[i].taxes.medicare == full_surtax_med, (
                f"period {i+1}: medicare expected "
                f"{full_surtax_med}, "
                f"got {results[i].taxes.medicare}"
            )


# ── Annual Projection Tests ─────────────────────────────────────


class TestAnnualProjection:
    """Tests for full-year salary projection correctness."""

    def test_26_period_annual_net_pay_sum(
        self, base_profile, biweekly_periods,
        simple_tax_configs
    ):
        """C27-3: Annual totals across 26 periods for a $60k salary.

        Every period is identical at this salary, which is the contract ruling
        **balance:R-HW** states: gross = $60,000 / 26 = $2307.6923... ->
        $2307.69, and net = 2307.69 - 173.08 - 103.85 - 143.08 - 33.46 =
        $1854.22 on all 26.

        Federal/state/SS/medicare are byte-identical across the year because
        the gross is: one figure in, one figure out.  No cap moves a later
        period either -- the largest cumulative-before is 25 x $2,307.69 =
        $57,692.25, under the SS wage base ($168,600) and the surtax threshold
        ($200,000).

        **Re-pinned at plan step balance:X-aw**, which superseded MED-05 /
        PA-07.  Under that rule the first 6 periods carried $2307.70 and the
        year summed to $60,000.00 exactly; the year now sums to
        26 * $2307.69 = $59,999.94, six cents under the contract salary.
        ``TestTheGrossIsARateAndNotAShareOfAYear`` owns that gap as its own
        subject; this case owns the ANNUAL TOTALS built on top of it.
        """
        results = project_salary(
            payroll_basis(base_profile, biweekly_periods), biweekly_periods, simple_tax_configs
        )

        assert len(results) == 26, (
            f"expected 26 results, got {len(results)}"
        )

        # 26 * $2307.69 = $59,999.94 -- six cents under the contract salary,
        # which is the cost ruling R-HW accepts (plan step balance:X-aw).
        total_gross = sum(r.earnings.gross_biweekly for r in results)
        assert total_gross == Decimal("59999.94"), (
            f"total gross: expected 59999.94 (26 * 2307.69), "
            f"got {total_gross}"
        )

        # Every period carries the same rate.
        for i in range(26):
            assert results[i].earnings.gross_biweekly == Decimal("2307.69"), (
                f"period {i+1}: expected 2307.69, "
                f"got {results[i].earnings.gross_biweekly}"
            )

        # 173.08 * 26 = 4500.08 (per-period federal byte-identical;
        # both 2307.69 and 2307.70 annualise to the same 10%-bracket
        # withholding after the standard deduction).
        total_federal = sum(r.taxes.federal for r in results)
        assert total_federal == Decimal("173.08") * 26, (
            f"total federal: expected 4500.08, got {total_federal}"
        )

        # 103.85 * 26 = 2700.10
        total_state = sum(r.taxes.state for r in results)
        assert total_state == Decimal("103.85") * 26, (
            f"total state: expected 2700.10, got {total_state}"
        )

        # 143.08 * 26 = 3720.08 (FICA per-period unchanged: both
        # 2307.69*0.062 and 2307.70*0.062 round to 143.08).
        total_ss = sum(r.taxes.social_security for r in results)
        assert total_ss == Decimal("143.08") * 26, (
            f"total SS: expected 3720.08, got {total_ss}"
        )

        # 33.46 * 26 = 869.96 (both grosses round to the same medicare).
        total_medicare = sum(r.taxes.medicare for r in results)
        assert total_medicare == Decimal("33.46") * 26, (
            f"total medicare: expected 869.96, got {total_medicare}"
        )

        # 26 * $1854.22 = $48,209.72, one net figure for the whole year.
        total_net = sum(r.earnings.net_pay for r in results)
        assert total_net == Decimal("48209.72"), (
            f"total net: expected 48209.72 (26 * 1854.22), got {total_net}"
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
        """C27-3 corollary: the $60k breakdown is IDENTICAL across 26 periods.

        Every field of every period matches -- gross, net and all four
        withholding lines -- because the gross is a rate (ruling
        **balance:R-HW**) and nothing else in this profile varies by period.
        The year's cumulative max is $59,999.94, under the SS cap ($168,600)
        and the surtax threshold ($200,000), so no cap moves a later period.

        **Re-pinned at plan step balance:X-aw.** Under MED-05 / PA-07 this
        case asserted TWO cent-equivalence groups -- the first 6 periods at
        $2307.70 / $1854.23 and the last 20 at $2307.69 / $1854.22 -- which
        was the residue distribution showing through into net pay.  The
        "all 26 identical" invariant it replaced is restored, and it is now
        structural rather than a property of where the residue happened to
        land: the producer cannot see a period at all.
        """
        results = project_salary(
            payroll_basis(base_profile, biweekly_periods), biweekly_periods, simple_tax_configs
        )

        assert len(results) == 26, (
            f"expected 26 results, got {len(results)}"
        )

        # $60,000 / 26 = $2307.6923... -> $2307.69; net = $2307.69 - $173.08
        # - $103.85 - $143.08 - $33.46 = $1854.22.
        assert results[0].earnings.gross_biweekly == Decimal("2307.69")
        assert results[0].earnings.net_pay == Decimal("1854.22")

        first = results[0]
        for i in range(1, 26):
            r = results[i]
            assert r.earnings.gross_biweekly == first.earnings.gross_biweekly, (
                f"period {i+1}: gross {r.earnings.gross_biweekly} != "
                f"period 1 gross {first.earnings.gross_biweekly}"
            )
            assert r.earnings.net_pay == first.earnings.net_pay, (
                f"period {i+1}: net {r.earnings.net_pay} != "
                f"period 1 net {first.earnings.net_pay}"
            )
            assert r.taxes.federal == first.taxes.federal, (
                f"period {i+1}: federal {r.taxes.federal} != "
                f"period 1 federal {first.taxes.federal}"
            )
            assert r.taxes.state == first.taxes.state, (
                f"period {i+1}: state {r.taxes.state} != "
                f"period 1 state {first.taxes.state}"
            )
            assert r.taxes.social_security == first.taxes.social_security, (
                f"period {i+1}: SS {r.taxes.social_security} != "
                f"period 1 SS {first.taxes.social_security}"
            )
            assert r.taxes.medicare == first.taxes.medicare, (
                f"period {i+1}: medicare {r.taxes.medicare} != "
                f"period 1 medicare {first.taxes.medicare}"
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
        period = _period(
            start_date=date(2026, 1, 16), period_id=1
        )

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        assert result.earnings.gross_biweekly == Decimal("0.00"), (
            f"gross: expected 0.00, "
            f"got {result.earnings.gross_biweekly}"
        )
        assert result.taxes.federal == Decimal("0.00"), (
            f"federal: expected 0.00, "
            f"got {result.taxes.federal}"
        )
        assert result.taxes.state == Decimal("0.00"), (
            f"state: expected 0.00, "
            f"got {result.taxes.state}"
        )
        assert result.taxes.social_security == Decimal("0.00"), (
            f"SS: expected 0.00, "
            f"got {result.taxes.social_security}"
        )
        assert result.taxes.medicare == Decimal("0.00"), (
            f"medicare: expected 0.00, "
            f"got {result.taxes.medicare}"
        )
        assert result.earnings.net_pay == Decimal("0.00"), (
            f"net: expected 0.00, "
            f"got {result.earnings.net_pay}"
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
        period = _period(
            start_date=date(2026, 1, 16), period_id=1
        )

        with pytest.raises(InvalidGrossPayError):
            calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
            )


# ── Negative-Path and Boundary-Condition Tests ─────────────────────


class TestNegativeAndBoundaryPaths:
    """Negative-path and boundary-condition tests for the paycheck calculator.

    Verifies behavior with zero/edge-case salary profiles, excessive
    deductions, and unusual pay frequencies.
    """

    def test_a_zero_paycheck_count_cannot_be_expressed(self):
        """A zero paycheck count is REFUSED, not silently defaulted (R-F16).

        Input: a cadence of 0 days, which is what a stored
        ``pay_periods_per_year`` of 0 amounted to before plan step R-F16.
        Expected: ``PayCadence`` refuses at construction, so no
        :class:`PayrollBasis` and no paycheck can be built from it.

        **This replaces an assertion that the engine SILENTLY defaulted a zero
        count to 26**, via ``profile.pay_periods_per_year or 26``, whose own
        note asked for "a ValidationError guard if 0 is invalid user input".
        Dropping the column is that guard: the count is now derived from
        ``budget.pay_schedule.cadence_days``, ``ck_pay_schedule_cadence_range``
        bounds that column to 1..365 in the database, and
        :func:`~app.services.pay_calendar.validate_cadence` refuses the same
        range in front of it -- so the misconfigured profile the old test
        described is no longer a state the application can hold.  Division by
        zero in the pipeline is prevented by the value not existing rather
        than by a falsy-coalesce nobody could see.
        """
        from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
            PayCadence,
            PayCalendarError,
        )

        with pytest.raises(PayCalendarError):
            PayCadence(cadence_days=0)

    def test_a_biweekly_cadence_prices_the_known_paycheck(
        self, simple_tax_configs
    ):
        """26 paychecks a year is what a 14-day cadence derives.

        Input: a $60,000 profile on the default 14-day cadence.
        Expected: the known $60k / 26 figures -- gross $2,307.69, net
        $1,854.22 -- proving the DERIVED count reproduces exactly what the
        dropped column's 26 produced, which is the no-op claim plan step
        R-F16 makes for every owner whose two stored answers agreed.
        """
        period = _period(start_date=date(2026, 1, 16), period_id=1)
        profile = FakeProfile(
            annual_salary=60000,
            created_at=date(2026, 1, 1),
        )

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        assert result.earnings.gross_biweekly == Decimal("2307.69")
        assert result.earnings.net_pay == Decimal("1854.22")

    def test_a_once_a_year_cadence_has_no_rounding_artifacts(
        self, simple_bracket_set, nc_state_config, standard_fica
    ):
        """Annual pay frequency (1 period/year) produces no rounding artifacts.

        Input: annual_salary=78000, a 365-day cadence (1 paycheck a year), no
        raises/deductions.
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
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)
        tax_configs = {
            "bracket_set": simple_bracket_set,
            "state_config": nc_state_config,
            "fica_config": standard_fica,
        }

        # ``round(365.2425 / 365) = 1``: the once-a-year cadence, DERIVED
        # rather than stated as a count (plan step R-F16).
        result = calculate_paycheck(
            payroll_basis(profile, [period], cadence_days=365), period,
            tax_configs,
        )

        # gross = 78000 / 1 = 78000.00 (exact, no rounding)
        assert result.earnings.gross_biweekly == Decimal("78000.00"), (
            f"gross: expected 78000.00, got {result.earnings.gross_biweekly}"
        )
        # Federal: 50000*0.10 + 13000*0.22 = 7860.00 / 1 = 7860.00
        assert result.taxes.federal == Decimal("7860.00"), (
            f"federal: expected 7860.00, got {result.taxes.federal}"
        )
        # State: 78000*0.045 = 3510.00 / 1 = 3510.00
        assert result.taxes.state == Decimal("3510.00"), (
            f"state: expected 3510.00, got {result.taxes.state}"
        )
        # SS: 78000*0.062 = 4836.00
        assert result.taxes.social_security == Decimal("4836.00"), (
            f"SS: expected 4836.00, got {result.taxes.social_security}"
        )
        # Medicare: 78000*0.0145 = 1131.00
        assert result.taxes.medicare == Decimal("1131.00"), (
            f"medicare: expected 1131.00, got {result.taxes.medicare}"
        )
        # net = 78000 - 7860 - 3510 - 4836 - 1131 = 60663.00
        assert result.earnings.net_pay == Decimal("60663.00"), (
            f"net: expected 60663.00, got {result.earnings.net_pay}"
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
            deductions=[
                FakeDeduction(
                    name="Excessive Post Tax",
                    amount="2000",
                    deduction_timing="post_tax",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs)

        assert result.earnings.gross_biweekly == Decimal("1153.85")
        assert result.taxes.federal == Decimal("57.69")
        assert result.taxes.state == Decimal("51.92")
        assert result.taxes.social_security == Decimal("71.54")
        assert result.taxes.medicare == Decimal("16.73")

        # The calculator returns negative net pay when post-tax deductions exceed
        # take-home. The route layer should warn the user.
        assert result.earnings.net_pay == Decimal("-1044.03"), (
            f"net_pay: expected -1044.03, got {result.earnings.net_pay}"
        )

    def test_zero_annual_salary(self, simple_tax_configs):
        """Zero salary produces zero in every field without error.

        Input: annual_salary=0 on the default 14-day cadence, no deductions.
        Expected: All fields (including annual_salary, taxable_income) are zero.
        Why: A zero-salary profile (e.g., a template or placeholder) must not
        produce NaN, crash, or negative values in any tax calculation.
        """
        profile = FakeProfile(
            annual_salary=0,
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs)

        assert result.earnings.annual_salary == Decimal("0.00")
        assert result.earnings.gross_biweekly == Decimal("0.00")
        assert result.earnings.taxable_income == Decimal("0.00")
        assert result.taxes.federal == Decimal("0.00")
        assert result.taxes.state == Decimal("0.00")
        assert result.taxes.social_security == Decimal("0.00")
        assert result.taxes.medicare == Decimal("0.00")
        assert result.earnings.net_pay == Decimal("0.00")

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
            deductions=[
                FakeDeduction(
                    name="Mega Pre Tax",
                    amount="2500",
                    deduction_timing="pre_tax",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs)

        # gross = 52000/26 = 2000.00
        assert result.earnings.gross_biweekly == Decimal("2000.00")
        # taxable_biweekly = max(2000 - 2500, 0) = 0.00 (clamped by source code)
        assert result.earnings.taxable_income == Decimal("0.00"), (
            f"taxable_income should be clamped to 0, got {result.earnings.taxable_income}"
        )
        # Federal/state: 0 taxable → 0 tax
        assert result.taxes.federal == Decimal("0.00")
        assert result.taxes.state == Decimal("0.00")
        # FICA is computed on gross, not taxable income.
        # SS: 2000*0.062 = 124.00
        assert result.taxes.social_security == Decimal("124.00")
        # Medicare: 2000*0.0145 = 29.00
        assert result.taxes.medicare == Decimal("29.00")
        # net = 2000 - 2500 - 0 - 0 - 124 - 29 = -653.00
        # The calculator allows negative net when deductions exceed gross.
        assert result.earnings.net_pay == Decimal("-653.00"), (
            f"net_pay: expected -653.00, got {result.earnings.net_pay}"
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
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        with_ded = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        # Baseline comparison (from established test):
        baseline_federal = Decimal("173.08")
        baseline_state = Decimal("103.85")

        # Federal tax must be LOWER with pre-tax deduction.
        assert with_ded.taxes.federal == Decimal("153.08"), (
            f"federal_tax: expected 153.08, got {with_ded.taxes.federal}"
        )
        assert with_ded.taxes.federal < baseline_federal, (
            "Pre-tax deduction must reduce federal tax"
        )

        # State tax must be LOWER with pre-tax deduction.
        assert with_ded.taxes.state == Decimal("94.85"), (
            f"state_tax: expected 94.85, got {with_ded.taxes.state}"
        )
        assert with_ded.taxes.state < baseline_state, (
            "Pre-tax deduction must reduce state tax"
        )

        # Taxable income field must equal gross minus pre-tax.
        assert with_ded.earnings.taxable_income == Decimal("2107.69"), (
            f"taxable_income: expected 2107.69, "
            f"got {with_ded.earnings.taxable_income}"
        )

        # Verify the magnitude of tax reduction matches marginal rates.
        # $200 * 10% marginal bracket = $20.00/period federal reduction.
        assert baseline_federal - with_ded.taxes.federal == Decimal("20.00"), (
            f"Federal reduction should be $20.00 "
            f"(= $200 * 10% bracket rate), "
            f"got {baseline_federal - with_ded.taxes.federal}"
        )
        # $200 * 4.5% NC flat rate = $9.00/period state reduction.
        assert baseline_state - with_ded.taxes.state == Decimal("9.00"), (
            f"State reduction should be $9.00 "
            f"(= $200 * 4.5% flat rate), "
            f"got {baseline_state - with_ded.taxes.state}"
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
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        no_ded = calculate_paycheck(
            payroll_basis(no_ded_profile, [period]), period,
            simple_tax_configs
        )
        with_ded = calculate_paycheck(
            payroll_basis(with_ded_profile, [period]), period,
            simple_tax_configs
        )

        # SS must be identical -- computed on gross, not taxable.
        assert with_ded.taxes.social_security == no_ded.taxes.social_security, (
            f"SS changed with pre-tax deduction: "
            f"{no_ded.taxes.social_security} -> {with_ded.taxes.social_security}. "
            f"FICA must be computed on gross, not taxable income."
        )
        assert with_ded.taxes.social_security == Decimal("143.08"), (
            f"SS: expected 143.08, got {with_ded.taxes.social_security}"
        )

        # Medicare must be identical -- computed on gross, not taxable.
        assert with_ded.taxes.medicare == no_ded.taxes.medicare, (
            f"Medicare changed with pre-tax deduction: "
            f"{no_ded.taxes.medicare} -> {with_ded.taxes.medicare}. "
            f"FICA must be computed on gross, not taxable income."
        )
        assert with_ded.taxes.medicare == Decimal("33.46"), (
            f"Medicare: expected 33.46, got {with_ded.taxes.medicare}"
        )

        # Gross must also be identical (deductions don't change gross).
        assert with_ded.earnings.gross_biweekly == no_ded.earnings.gross_biweekly

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
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        # Deduction amount computed from gross.
        assert result.deductions.pre_tax[0].amount == Decimal("138.46"), (
            f"6% of 2307.69: expected 138.46, "
            f"got {result.deductions.pre_tax[0].amount}"
        )

        assert result.earnings.taxable_income == Decimal("2169.23"), (
            f"taxable_income: expected 2169.23, "
            f"got {result.earnings.taxable_income}"
        )

        assert result.taxes.federal == Decimal("159.23"), (
            f"federal_tax: expected 159.23, got {result.taxes.federal}"
        )
        assert result.taxes.state == Decimal("97.62"), (
            f"state_tax: expected 97.62, got {result.taxes.state}"
        )

        # FICA on gross -- unaffected by percentage deduction.
        assert result.taxes.social_security == Decimal("143.08"), (
            f"SS: expected 143.08, got {result.taxes.social_security}"
        )
        assert result.taxes.medicare == Decimal("33.46"), (
            f"Medicare: expected 33.46, got {result.taxes.medicare}"
        )

        # Net pay end-to-end.
        # 2307.69 - 138.46 - 159.23 - 97.62 - 143.08 - 33.46 = 1735.84
        assert result.earnings.net_pay == Decimal("1735.84"), (
            f"net_pay: expected 1735.84, got {result.earnings.net_pay}"
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
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)
        p2 = _period(start_date=date(2026, 1, 16), period_id=2)
        p3 = _period(start_date=date(2026, 1, 30), period_id=3)
        all_periods = [p1, p2, p3]

        normal = calculate_paycheck(
            payroll_basis(profile, all_periods), p2,
            simple_tax_configs
        )
        third = calculate_paycheck(
            payroll_basis(profile, all_periods), p3,
            simple_tax_configs
        )

        # On normal paycheck, deduction applies.
        assert len(normal.deductions.pre_tax) == 1, (
            "Normal paycheck should have 1 pre-tax deduction"
        )
        assert normal.deductions.pre_tax[0].amount == Decimal("100.00")
        assert normal.taxes.federal == Decimal("163.08"), (
            f"Normal federal: expected 163.08, got {normal.taxes.federal}"
        )
        assert normal.taxes.state == Decimal("99.35"), (
            f"Normal state: expected 99.35, got {normal.taxes.state}"
        )

        # On 3rd paycheck, deduction is skipped.
        assert len(third.deductions.pre_tax) == 0, (
            "3rd paycheck should have 0 pre-tax deductions "
            "(24/yr deduction skipped)"
        )
        assert third.period.is_third_paycheck is True

        # 3rd paycheck taxes are HIGHER because deduction was skipped.
        assert third.taxes.federal == Decimal("173.08"), (
            f"3rd paycheck federal: expected 173.08, "
            f"got {third.taxes.federal}"
        )
        assert third.taxes.state == Decimal("103.85"), (
            f"3rd paycheck state: expected 103.85, "
            f"got {third.taxes.state}"
        )
        assert third.taxes.federal > normal.taxes.federal, (
            "3rd paycheck federal should be higher (deduction skipped)"
        )
        assert third.taxes.state > normal.taxes.state, (
            "3rd paycheck state should be higher (deduction skipped)"
        )

        # Tax increase exactly matches deduction * marginal rate.
        assert third.taxes.federal - normal.taxes.federal == Decimal("10.00"), (
            f"Federal increase: expected $10 "
            f"(= $100 * 10% bracket), "
            f"got {third.taxes.federal - normal.taxes.federal}"
        )
        assert third.taxes.state - normal.taxes.state == Decimal("4.50"), (
            f"State increase: expected $4.50 "
            f"(= $100 * 4.5% flat), "
            f"got {third.taxes.state - normal.taxes.state}"
        )

        # FICA identical on both (gross is the same).
        assert third.taxes.social_security == normal.taxes.social_security
        assert third.taxes.medicare == normal.taxes.medicare

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
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        assert result.deductions.total_pre_tax == Decimal("300.00"), (
            f"total_pre_tax: expected 300, got {result.deductions.total_pre_tax}"
        )
        assert result.earnings.taxable_income == Decimal("2007.69"), (
            f"taxable_income: expected 2007.69, "
            f"got {result.earnings.taxable_income}"
        )
        assert result.taxes.federal == Decimal("143.08"), (
            f"federal_tax: expected 143.08, got {result.taxes.federal}"
        )
        assert result.taxes.state == Decimal("90.35"), (
            f"state_tax: expected 90.35, got {result.taxes.state}"
        )

        # Reduction from baseline matches total deduction * marginal rate.
        assert Decimal("173.08") - result.taxes.federal == Decimal("30.00"), (
            "Federal reduction should be $30 = $300 * 10%"
        )
        assert Decimal("103.85") - result.taxes.state == Decimal("13.50"), (
            "State reduction should be $13.50 = $300 * 4.5%"
        )

        # FICA still on gross.
        assert result.taxes.social_security == Decimal("143.08")
        assert result.taxes.medicare == Decimal("33.46")

        # End-to-end net pay.
        assert result.earnings.net_pay == Decimal("1597.72"), (
            f"net_pay: expected 1597.72, got {result.earnings.net_pay}"
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
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        no_ded = calculate_paycheck(
            payroll_basis(no_ded_profile, [period]), period,
            simple_tax_configs
        )
        post_ded = calculate_paycheck(
            payroll_basis(post_ded_profile, [period]), period,
            simple_tax_configs
        )

        # Every tax field must be identical.
        assert post_ded.taxes.federal == no_ded.taxes.federal == Decimal("173.08"), (
            f"Post-tax deduction changed federal: "
            f"{no_ded.taxes.federal} -> {post_ded.taxes.federal}"
        )
        assert post_ded.taxes.state == no_ded.taxes.state == Decimal("103.85"), (
            f"Post-tax deduction changed state: "
            f"{no_ded.taxes.state} -> {post_ded.taxes.state}"
        )
        assert post_ded.taxes.social_security == no_ded.taxes.social_security == Decimal("143.08"), (
            f"Post-tax deduction changed SS: "
            f"{no_ded.taxes.social_security} -> {post_ded.taxes.social_security}"
        )
        assert post_ded.taxes.medicare == no_ded.taxes.medicare == Decimal("33.46"), (
            f"Post-tax deduction changed Medicare: "
            f"{no_ded.taxes.medicare} -> {post_ded.taxes.medicare}"
        )

        # Taxable income must also be unchanged.
        assert post_ded.earnings.taxable_income == no_ded.earnings.taxable_income, (
            "Post-tax deduction should not affect taxable_income"
        )

        # Only net pay changes (reduced by $200 post-tax).
        assert post_ded.earnings.net_pay == Decimal("1654.22"), (
            f"net_pay: expected 1654.22, got {post_ded.earnings.net_pay}"
        )
        assert post_ded.earnings.net_pay == no_ded.earnings.net_pay - Decimal("200.00"), (
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
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        # Taxes match the $200 pre-tax scenario (post-tax has no effect).
        assert result.taxes.federal == Decimal("153.08"), (
            f"federal: expected 153.08, got {result.taxes.federal}"
        )
        assert result.taxes.state == Decimal("94.85"), (
            f"state: expected 94.85, got {result.taxes.state}"
        )
        assert result.taxes.social_security == Decimal("143.08"), (
            f"SS: expected 143.08, got {result.taxes.social_security}"
        )
        assert result.taxes.medicare == Decimal("33.46"), (
            f"Medicare: expected 33.46, got {result.taxes.medicare}"
        )

        # Both deduction types present in their respective lists.
        assert result.deductions.total_pre_tax == Decimal("200.00")
        assert result.deductions.total_post_tax == Decimal("150.00")

        # Net pay accounts for both deduction types.
        assert result.earnings.net_pay == Decimal("1533.22"), (
            f"net_pay: expected 1533.22, got {result.earnings.net_pay}"
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
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        no_ded = calculate_paycheck(
            payroll_basis(no_ded_profile, [period]), period,
            configs
        )
        with_ded = calculate_paycheck(
            payroll_basis(with_ded_profile, [period]), period,
            configs
        )

        # Without deduction: state std ded reduces the base.
        assert no_ded.taxes.state == Decimal("81.78"), (
            f"No-deduction state: expected 81.78, "
            f"got {no_ded.taxes.state}"
        )

        # With deduction: both reductions apply.
        assert with_ded.taxes.state == Decimal("72.78"), (
            f"With-deduction state: expected 72.78, "
            f"got {with_ded.taxes.state}"
        )

        # Reduction matches deduction * flat rate.
        assert no_ded.taxes.state - with_ded.taxes.state == Decimal("9.00"), (
            f"State reduction: expected $9.00 "
            f"(= $200 * 4.5%), "
            f"got {no_ded.taxes.state - with_ded.taxes.state}"
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
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        r = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        # Verify every component individually.
        assert r.earnings.gross_biweekly == Decimal("2307.69")
        assert r.deductions.total_pre_tax == Decimal("200.00")
        assert r.earnings.taxable_income == Decimal("2107.69")
        assert r.taxes.federal == Decimal("153.08")
        assert r.taxes.state == Decimal("94.85")
        assert r.taxes.social_security == Decimal("143.08")
        assert r.taxes.medicare == Decimal("33.46")
        assert r.deductions.total_post_tax == Decimal("0")

        # Verify net pay matches the formula.
        expected_net = (
            r.earnings.gross_biweekly
            - r.deductions.total_pre_tax
            - r.taxes.federal
            - r.taxes.state
            - r.taxes.social_security
            - r.taxes.medicare
            - r.deductions.total_post_tax
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        assert r.earnings.net_pay == expected_net, (
            f"net_pay {r.earnings.net_pay} != formula result {expected_net}"
        )
        assert r.earnings.net_pay == Decimal("1683.22"), (
            f"net_pay: expected 1683.22, got {r.earnings.net_pay}"
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
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        no_ded = calculate_paycheck(
            payroll_basis(no_ded_profile, [period]), period,
            simple_tax_configs
        )
        with_ded = calculate_paycheck(
            payroll_basis(with_ded_profile, [period]), period,
            simple_tax_configs
        )

        assert no_ded.taxes.federal == Decimal("657.69"), (
            f"No-deduction federal: expected 657.69, "
            f"got {no_ded.taxes.federal}"
        )
        assert with_ded.taxes.federal == Decimal("547.69"), (
            f"With-deduction federal: expected 547.69, "
            f"got {with_ded.taxes.federal}"
        )

        # Reduction = $500 * 22% marginal bracket = $110.00
        reduction = no_ded.taxes.federal - with_ded.taxes.federal
        assert reduction == Decimal("110.00"), (
            f"Federal reduction: expected $110.00 "
            f"(= $500 * 22% marginal bracket), got {reduction}"
        )

        # FICA unchanged at higher income.
        assert with_ded.taxes.social_security == no_ded.taxes.social_security
        assert with_ded.taxes.medicare == no_ded.taxes.medicare


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
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs,
            calibration=cal,
        )

        assert result.taxes.federal == Decimal("230.77")
        assert result.taxes.state == Decimal("115.38")
        assert result.taxes.social_security == Decimal("143.08")
        assert result.taxes.medicare == Decimal("33.46")

        expected_net = (
            Decimal("2307.69")
            - Decimal("230.77")
            - Decimal("115.38")
            - Decimal("143.08")
            - Decimal("33.46")
        )
        assert result.earnings.net_pay == expected_net

    def test_calibration_reproduces_cafeteria_reduced_paycheck(
        self, simple_tax_configs
    ):
        """Production-path lock: calculate_paycheck with an active calibration
        reproduces a real pay stub whose Social Security is assessed on a
        Section 125 cafeteria-reduced base (SS calibration fix, 2026-06-01).

        This is the assertion that was ABSENT and let the SS regression
        ship.  The prior code forced statutory 6.2% on the full gross in the
        calibration path, so calculate_paycheck overstated SS and understated
        net by the cafeteria gap; no test exercised the production path
        against a non-statutory effective_ss_rate.

        Developer's real 2026 pay stub:
          annual_salary $91,675, 26 periods -> gross 91675/26 = $3,525.96
          pre-tax deductions  = $706.95 -> taxable = $2,819.01
          post-tax deductions = $21.82
          actual stub: federal $0, state $84.00, SS $194.36 (5.51% of gross,
            NOT statutory $218.61), medicare $45.45.
          Net on Shekel's computed gross:
            3525.96 - 706.95 - 0 - 84.00 - 194.36 - 45.45 - 21.82 = 2473.38
          (the stub's own net is $2,473.42; the $0.04 gap is a separate,
          trivial salary-rounding item -- 91675/26 = 3525.96 vs the stub's
          $3,526.00 gross.)

        Rates are derived exactly as calibrate_confirm does (against the
        ACTUAL stub gross/taxable) then applied by calculate_paycheck
        (against the computed gross), exercising the real derive -> apply
        path end to end.
        """
        from app.services.calibration_service import (  # pylint: disable=import-outside-toplevel
            PayStubActuals,
            derive_effective_rates,
        )

        # Derived against the ACTUAL stub gross 3526.00 and taxable
        # 3526.00 - 706.95 = 2819.05 (the basis calibrate_confirm uses).
        rates = derive_effective_rates(
            PayStubActuals(
                actual_gross_pay=Decimal("3526.00"),
                actual_federal_tax=Decimal("0.00"),
                actual_state_tax=Decimal("84.00"),
                actual_social_security=Decimal("194.36"),
                actual_medicare=Decimal("45.45"),
                taxable_income=Decimal("2819.05"),
            )
        )
        cal = FakeCalibration(
            federal_rate=rates.effective_federal_rate,
            state_rate=rates.effective_state_rate,
            ss_rate=rates.effective_ss_rate,
            medicare_rate=rates.effective_medicare_rate,
        )

        deductions = [
            FakeDeduction(name="FSA", amount="133.33", deduction_timing="pre_tax"),
            FakeDeduction(name="Vision", amount="12.06", deduction_timing="pre_tax"),
            FakeDeduction(name="Dental", amount="40.00", deduction_timing="pre_tax"),
            FakeDeduction(name="Health", amount="310.00", deduction_timing="pre_tax"),
            FakeDeduction(
                name="State Retirement", amount="211.56",
                deduction_timing="pre_tax",
            ),
            FakeDeduction(name="Child AD&D", amount="0.13", deduction_timing="post_tax"),
            FakeDeduction(name="Spouse VTL", amount="2.16", deduction_timing="post_tax"),
            FakeDeduction(name="Child VTL", amount="1.50", deduction_timing="post_tax"),
            FakeDeduction(name="EE AD&D", amount="5.40", deduction_timing="post_tax"),
            FakeDeduction(name="Spouse AD&D", amount="1.08", deduction_timing="post_tax"),
            FakeDeduction(name="EE VTL", amount="10.80", deduction_timing="post_tax"),
            FakeDeduction(
                name="Dependent Basic Term Life", amount="0.75",
                deduction_timing="post_tax",
            ),
        ]
        profile = FakeProfile(
            annual_salary=91675,
            deductions=deductions,
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs,
            calibration=cal,
        )

        # Computed gross 91675/26 = 3525.96 (single-period half-up fallback).
        assert result.earnings.gross_biweekly == Decimal("3525.96")
        assert result.deductions.total_pre_tax == Decimal("706.95")
        assert result.deductions.total_post_tax == Decimal("21.82")
        assert result.taxes.federal == Decimal("0.00")
        assert result.taxes.state == Decimal("84.00")
        assert result.taxes.medicare == Decimal("45.45")
        # SS uses effective_ss_rate (cafeteria-reduced), NOT statutory 6.2%.
        assert result.taxes.social_security == Decimal("194.36"), (
            f"SS must reproduce the stub's $194.36, got {result.taxes.social_security}"
        )
        # Regression guard: statutory 6.2% would be 3525.96 * 0.062 = 218.61,
        # the wrong value the pre-fix calibration path produced.
        assert result.taxes.social_security != Decimal("218.61")
        assert result.earnings.net_pay == Decimal("2473.38"), (
            f"Net must reproduce 2473.38, got {result.earnings.net_pay}"
        )

    def test_calibrated_paycheck_differs_from_bracket_based(
        self, simple_tax_configs
    ):
        """Calibrated taxes differ from bracket-based for the same profile.

        This proves the calibration path is actually being used.
        """
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        # Bracket-based calculation.
        bracket_result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs,
        )

        # Calibrated with intentionally different rates.
        cal = FakeCalibration(
            federal_rate="0.15000",
            state_rate="0.03000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )
        cal_result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs,
            calibration=cal,
        )

        assert cal_result.taxes.federal != bracket_result.taxes.federal, (
            "Calibrated federal tax should differ from bracket-based"
        )
        assert cal_result.taxes.state != bracket_result.taxes.state, (
            "Calibrated state tax should differ from bracket-based"
        )

    def test_inactive_calibration_uses_brackets(
        self, simple_tax_configs
    ):
        """When calibration.is_active is False, bracket-based taxes are used."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        # Bracket-based (no calibration).
        bracket_result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs,
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
            payroll_basis(profile, [period]), period,
            simple_tax_configs,
            calibration=cal,
        )

        assert result.taxes.federal == bracket_result.taxes.federal
        assert result.taxes.state == bracket_result.taxes.state
        assert result.taxes.social_security == bracket_result.taxes.social_security
        assert result.taxes.medicare == bracket_result.taxes.medicare
        assert result.earnings.net_pay == bracket_result.earnings.net_pay

    def test_none_calibration_uses_brackets(
        self, simple_tax_configs
    ):
        """calibration=None (default) produces the same result as omitting it."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result_omitted = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs,
        )
        result_none = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs,
            calibration=None,
        )

        assert result_omitted.earnings.net_pay == result_none.earnings.net_pay
        assert result_omitted.taxes.federal == result_none.taxes.federal

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
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs,
            calibration=cal,
        )

        # Federal uses taxable (2107.69), not gross (2307.69).
        assert result.taxes.federal == Decimal("210.77")
        # SS uses gross.
        assert result.taxes.social_security == Decimal("143.08")

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
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs,
            calibration=cal,
        )

        # Post-tax deduction of $150 must appear.
        assert result.deductions.total_post_tax == Decimal("150.00"), (
            f"Post-tax deduction missing: expected 150.00, "
            f"got {result.deductions.total_post_tax}"
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
        assert result.earnings.net_pay == expected, (
            f"Net pay with post-tax: expected {expected}, got {result.earnings.net_pay}"
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
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs,
            calibration=cal,
        )

        assert result.earnings.taxable_income == Decimal("2107.69")
        assert result.taxes.federal == Decimal("210.77")
        assert result.taxes.state == Decimal("105.38")   # 2107.69 * 0.05
        assert result.taxes.social_security == Decimal("143.08")
        assert result.taxes.medicare == Decimal("33.46")
        assert result.deductions.total_pre_tax == Decimal("200.00")
        assert result.deductions.total_post_tax == Decimal("150.00")

        expected_net = (
            Decimal("2307.69")
            - Decimal("200.00")
            - Decimal("210.77")
            - Decimal("105.38")
            - Decimal("143.08")
            - Decimal("33.46")
            - Decimal("150.00")
        )
        assert result.earnings.net_pay == expected_net

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
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)
        p2 = _period(start_date=date(2026, 1, 16), period_id=2)
        p3 = _period(start_date=date(2026, 1, 30), period_id=3)
        all_periods = [p1, p2, p3]

        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        # Non-3rd paycheck: deduction applies, taxable = 2307.69 - 200 = 2107.69
        normal = calculate_paycheck(
            payroll_basis(profile, all_periods), p1,
            simple_tax_configs,
            calibration=cal,
        )
        assert normal.deductions.total_pre_tax == Decimal("200.00")
        assert normal.taxes.federal == Decimal("210.77")  # 2107.69 * 0.10

        # 3rd paycheck: 24-per-year deduction is SKIPPED, taxable = 2307.69
        third = calculate_paycheck(
            payroll_basis(profile, all_periods), p3,
            simple_tax_configs,
            calibration=cal,
        )
        assert third.period.is_third_paycheck is True
        assert third.deductions.total_pre_tax == Decimal("0.00"), (
            "24-per-year deduction should be skipped on 3rd paycheck"
        )
        assert third.taxes.federal == Decimal("230.77"), (
            "3rd paycheck federal should be 2307.69 * 0.10 (full gross as taxable)"
        )
        # Higher taxable -> higher federal/state than normal paycheck.
        assert third.taxes.federal > normal.taxes.federal

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
        period = _period(start_date=date(2026, 2, 13), period_id=2)

        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        cal_result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs,
            calibration=cal,
        )
        bracket_result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs,
        )

        # Gross, raises, and deductions must be identical.
        assert cal_result.earnings.gross_biweekly == bracket_result.earnings.gross_biweekly, (
            "Calibration must not affect gross computation"
        )
        assert cal_result.earnings.annual_salary == bracket_result.earnings.annual_salary, (
            "Calibration must not affect raise application"
        )
        assert cal_result.deductions.total_pre_tax == bracket_result.deductions.total_pre_tax, (
            "Calibration must not affect pre-tax deductions"
        )
        assert cal_result.earnings.taxable_income == bracket_result.earnings.taxable_income, (
            "Calibration must not affect taxable income"
        )

    def test_project_salary_uses_calibration(self, simple_tax_configs):
        """project_salary passes calibration to every period's calculation."""
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        periods = [
            _period(start_date=date(2026, 1, 16), period_id=1),
            _period(start_date=date(2026, 1, 30), period_id=2),
            _period(start_date=date(2026, 2, 13), period_id=3),
        ]

        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        # With calibration.
        cal_breakdowns = project_salary(
            payroll_basis(profile, periods), periods, simple_tax_configs, calibration=cal,
        )
        # Without calibration.
        bracket_breakdowns = project_salary(
            payroll_basis(profile, periods), periods, simple_tax_configs,
        )

        assert len(cal_breakdowns) == 3
        for i, (cb, bb) in enumerate(zip(cal_breakdowns, bracket_breakdowns)):
            assert cb.taxes.federal != bb.taxes.federal, (
                f"Period {i}: calibrated federal should differ from brackets"
            )
            assert cb.taxes.federal == Decimal("230.77"), (
                f"Period {i}: expected 230.77 (2307.69 * 0.10)"
            )


class TestTheGrossIsARateAndNotAShareOfAYear:
    """Plan step **balance:X-aw** / ruling **balance:R-HW**: the per-paycheck
    gross is the salary over the owner's paycheck count, and nothing else.

    These replace ``TestBiweeklyResidueReconciliation``, which graded audit
    MED-05 / PA-07's contract -- the annual quantisation residue distributed
    across a calendar year so the year summed to the annual salary exactly.
    That contract is superseded, and the case that mattered most is the one it
    could not have: :meth:`test_the_gross_does_not_move_with_the_period_list`
    is finding **N-239**, and it FAILS on the superseded rule by construction,
    because deciding which paychecks got the residue cent required counting the
    period rows the caller happened to pass.
    """

    @pytest.mark.parametrize(
        "annual_salary,expected_gross",
        [
            # Hand-computed: annual / 26, ROUND_HALF_UP at the cent.
            # $50,000 / 26 = $1923.0769... -> $1923.08
            (Decimal("50000"), Decimal("1923.08")),
            # $75,000 / 26 = $2884.6153... -> $2884.62
            (Decimal("75000"), Decimal("2884.62")),
            # $100,000 / 26 = $3846.1538... -> $3846.15
            (Decimal("100000"), Decimal("3846.15")),
            # $60,000 / 26 = $2307.6923... -> $2307.69
            (Decimal("60000"), Decimal("2307.69")),
            # $78,000 / 26 = $3000.00 exact -- no rounding at all.
            (Decimal("78000"), Decimal("3000.00")),
            # The owner's own salary, whose stub pays a flat $3,526.00:
            # $91,675 / 26 = $3525.9615... -> $3525.96.
            (Decimal("91675"), Decimal("3525.96")),
        ],
    )
    def test_every_paycheck_of_a_salary_segment_pays_the_same_figure(
        self, annual_salary, expected_gross, biweekly_periods,
        simple_tax_configs,
    ):
        """All 26 paychecks carry ONE figure, which is what a real stub does.

        The superseded rule gave the earliest few paychecks of the year an
        extra cent, so a year held two distinct grosses differing by $0.01.
        A real employer pays one number: the owner's own nine measured payroll
        deposits all carry a gross of $3,526.00.
        """
        profile = FakeProfile(
            annual_salary=annual_salary, created_at=date(2026, 1, 1),
        )

        results = project_salary(
            payroll_basis(profile, biweekly_periods), biweekly_periods, simple_tax_configs
        )

        assert len(results) == 26
        grosses = {r.earnings.gross_biweekly for r in results}
        assert grosses == {expected_gross}, (
            f"expected one gross {expected_gross} across the year, "
            f"got {sorted(grosses)}"
        )

    def test_the_gross_does_not_move_with_the_period_list(
        self, simple_tax_configs,
    ):
        """Finding **N-239**: extending the schedule must not re-price a paycheck.

        Prices ONE fixed paycheck -- 2026-01-02, the owner's first payday of
        the year -- against period lists of five very different sizes, and
        asserts every caller gets the same answer.  A route preview passes a
        single period; a year-scoped caller passes that year; the recurrence
        engine's schedule extend passes only the rows it just created.

        **The SUBJECT has to sit where the superseded rule put its residue, or
        this case grades nothing.**  An earlier draft used the 14th payday and
        PASSED on the pre-X-aw code, because MED-05 / PA-07 gave the extra cent
        to the earliest 6 periods of the year and the 14th was never one of
        them -- the assertion held under both rules and the test measured
        nothing.  Period 1 is inside that window, so the old rule answers
        $2307.70 from a 26- or 27-period list and $2307.69 from a shorter one
        (which fell to its half-up fallback): two figures for one paycheck.

        $60,000 / 26 = $2307.6923... -> $2307.69, whoever asks and however
        much of the schedule they hold.

        The real-data form of the same defect: on the owner's own schedule,
        filling 2028 from its 16 stored rows to its 26 paydays moved six
        already-settled paychecks by a cent each ($3,930.07 -> $3,930.06).
        """
        profile = FakeProfile(annual_salary=60000, created_at=date(2026, 1, 1))
        # 27 is not a typo: a biweekly calendar year holds 27 paydays about
        # one year in eleven.  A Jan 2 phase is NOT one of them -- Jan 2 plus
        # 26 x 14 days lands 2027-01-01, so 2026 holds 26 -- and the 27th
        # element here therefore falls in the NEXT year.  It still discriminates
        # (the superseded rule counted 26 same-year periods either way, so both
        # the 26- and 27-element lists answered $2307.70), and it is the case
        # that exercises a list running past its own year's end.  The owner's
        # real phase, Jan 1, is a genuine 27-payday 2026.
        list_sizes = (1, 3, 16, 26, 27)
        subject = _period(start_date=date(2026, 1, 2), period_id=1)

        answers = {}
        for size in list_sizes:
            all_periods = [subject] + [
                _period(start_date=date(2026, 1, 2) + timedelta(days=14 * i),
                        period_id=i + 1)
                for i in range(1, size)
            ]
            assert len(all_periods) == size
            answers[size] = calculate_paycheck(
            payroll_basis(profile, all_periods), subject,
            simple_tax_configs,
            ).earnings.gross_biweekly

        assert set(answers.values()) == {Decimal("2307.69")}, (
            "one paycheck must have ONE gross however much of the schedule "
            f"the caller holds; got {answers}"
        )

    def test_a_mid_year_raise_gives_two_constant_figures(
        self, biweekly_periods, simple_tax_configs,
    ):
        """A raise changes the rate ONCE; each side of it is flat.

        A non-recurring 10% raise effective July 2026 splits the year at the
        14th period (Jan 2 + 13*14 days = Jul 3).  Before it every paycheck is
        $60,000 / 26 = $2307.69; after it every paycheck is
        $66,000 / 26 = $2538.4615... -> $2538.46.  The superseded rule gave the
        first three of the pre-raise run and the first two of the post-raise
        run an extra cent, so each run held two figures.
        """
        profile = FakeProfile(
            annual_salary=60000,
            created_at=date(2026, 1, 1),
            raises=[
                FakeRaise(
                    percentage="0.10",
                    effective_month=7, effective_year=2026,
                    is_recurring=False,
                ),
            ],
        )

        results = project_salary(
            payroll_basis(profile, biweekly_periods), biweekly_periods, simple_tax_configs
        )

        assert len(results) == 26
        pre = {r.earnings.gross_biweekly for r in results[:13]}
        post = {r.earnings.gross_biweekly for r in results[13:]}
        assert [r.earnings.annual_salary for r in results[:13]] == (
            [Decimal("60000.00")] * 13
        )
        assert [r.earnings.annual_salary for r in results[13:]] == (
            [Decimal("66000.00")] * 13
        )
        assert pre == {Decimal("2307.69")}, f"pre-raise run: {sorted(pre)}"
        assert post == {Decimal("2538.46")}, f"post-raise run: {sorted(post)}"

        # What ruling R-HW costs in a RAISE year, pinned: the superseded rule
        # made each segment total its exact pro-rata share ($30,000.00 and
        # $33,000.00, summing to $63,000.00). Each is now the flat rate times
        # the segment, three cents and two cents under respectively.
        pre_total = sum(r.earnings.gross_biweekly for r in results[:13])
        post_total = sum(r.earnings.gross_biweekly for r in results[13:])
        assert pre_total == Decimal("29999.97"), f"pre-raise total {pre_total}"
        assert post_total == Decimal("32999.98"), f"post-raise total {post_total}"
        assert pre_total + post_total == Decimal("62999.95")

    @pytest.mark.parametrize(
        "annual_salary,expected_year_total,expected_gap",
        [
            # 26 * round(annual/26) against the annual salary.  The gap is
            # what ruling R-HW accepts and MED-05 / PA-07 existed to close;
            # it is stated per row so the cost is reviewable rather than
            # asserted as "close enough".
            # 26 * 2307.69 = 59999.94, $0.06 under $60,000.
            (Decimal("60000"), Decimal("59999.94"), Decimal("-0.06")),
            # 26 * 1923.08 = 50000.08, $0.08 OVER $50,000 -- the gap has no
            # fixed sign, because the per-paycheck figure rounds either way.
            (Decimal("50000"), Decimal("50000.08"), Decimal("0.08")),
            # 26 * 3000.00 = 78000.00 exactly: a salary that divides evenly
            # has no gap at all.
            (Decimal("78000"), Decimal("78000.00"), Decimal("0.00")),
            # The owner's: 26 * 3525.96 = 91674.96, $0.04 under $91,675 --
            # and the employer's own flat $3,526.00 sums to $91,676.00, which
            # is $1.00 over.  Neither the app nor payroll hits the salary
            # exactly, which is why the identity was given up (finding N-391).
            (Decimal("91675"), Decimal("91674.96"), Decimal("-0.04")),
        ],
    )
    def test_the_year_no_longer_sums_to_the_annual_salary_exactly(
        self, annual_salary, expected_year_total, expected_gap,
        biweekly_periods, simple_tax_configs,
    ):
        """The COST of ruling R-HW, pinned so it cannot drift unnoticed.

        MED-05 / PA-07 added the residue distribution to make this sum exact.
        Ruling R-HW gives that up deliberately: the identity is not one payroll
        honours, and buying it cost a per-paycheck figure that no stub shows
        and a dependence on which pay-period rows exist (finding N-239).
        """
        profile = FakeProfile(
            annual_salary=annual_salary, created_at=date(2026, 1, 1),
        )

        results = project_salary(
            payroll_basis(profile, biweekly_periods), biweekly_periods, simple_tax_configs
        )

        total = sum(r.earnings.gross_biweekly for r in results)
        assert total == expected_year_total
        assert total - annual_salary.quantize(TWO_PLACES) == expected_gap

    # **``test_the_investment_projection_prices_the_same_gross`` was DELETED
    # at plan step salary:R14-b, and what it graded is worth stating.**  It
    # pinned that the paycheck engine and ``investment_projection`` rounded a
    # percentage deduction's gross identically -- driven at the owner's own
    # 2027 salary of $96,785.88, where until plan step balance:X-aw the two
    # answered $3,722.54 and $3,722.53 on 5 of his 63 saved periods, so a
    # percentage was taken against a gross a cent below the one the paycheck
    # subtracted it from.  X-aw made both ask
    # ``payroll_basis.gross_per_paycheck``, and this case held them there.
    #
    # It has no successor because it has no SUBJECT: the second producer is
    # gone.  The contribution feed reads this engine's own breakdown now
    # (ruling **R-SAL2**), so the agreement the case enforced is structural
    # rather than asserted -- and the caveat it carried is settled with it.
    # Its own docstring recorded that it graded the rounding rule and NOT the
    # wiring, because the two sides' INPUTS still differed by any applicable
    # raise (finding **D45**), and that "a case asserting these two equal on a
    # raise-BEARING profile would fail, and should".  There is one input now,
    # and ``test_income_service.TestThePerPeriodGrossIsTheENGINES`` grades a
    # raise-bearing profile end to end.


class TestGrossPerPaycheck:
    """The producer's own contract, graded directly rather than through the engine.

    Plan step **balance:X-aw** added it as a public function with two callers,
    and an adversarial review noted it had no case of its own -- every other
    test reaches it through ``calculate_paycheck``, which cannot exercise the
    boundaries below.
    """

    def test_it_rounds_half_up_at_an_exact_half_cent(self):
        """The rounding MODE, at the one input where the modes disagree.

        None of the salaries the engine tests land on a half cent, so the mode
        is inherited from ``round_money`` and graded nowhere in this file.
        $91,000.13 / 26 = $3,500.005 exactly -- ROUND_HALF_UP gives $3,500.01,
        Python's default ROUND_HALF_EVEN (banker's) gives $3,500.00. A money
        figure must never reach the even-rounding default implicitly.
        """
        assert gross_per_paycheck(
            Decimal("91000.13"), Decimal("26"),
        ) == Decimal("3500.01")

    def test_a_float_salary_is_refused_rather_than_rounded(self):
        """A ``float`` cannot reach the cent quantisation.

        The refusal is the DIVISION's, not ``round_money``'s: Decimal refuses
        to divide by a float operand, so the value never reaches the money
        boundary at all. Either way the imprecision a float carries cannot be
        laundered into a paycheck.
        """
        with pytest.raises(TypeError):
            gross_per_paycheck(91675.00, Decimal("26"))

    @pytest.mark.parametrize("cadence_days,count", [
        (7, "52"), (14, "26"), (15, "24"), (30, "12"), (365, "1"),
    ])
    def test_the_count_is_the_divisor_at_every_cadence(
        self, cadence_days, count,
    ):
        """The owner's rhythm decides the figure, which is finding F-16's rule.

        $78,000 divides evenly by 52, 26, 24, 12 and 1, so each expectation is
        exact arithmetic with no rounding to reason about -- the case grades
        the DIVISOR and nothing else.
        """
        expected = (Decimal("78000") / Decimal(count)).quantize(TWO_PLACES)
        assert gross_per_paycheck(
            Decimal("78000"), PayCadence(cadence_days).periods_per_year,
        ) == expected


class TestTheGrossContractIsDocumented:
    """The per-paycheck gross contract is stated where a reader will find it.

    Replaces ``TestBiweeklyResidueDocstring``, which pinned MED-05 / PA-07's
    wording so a revert to F-127's could not pass silently.  Ruling
    **balance:R-HW** superseded MED-05 / PA-07, so these pin the NEW contract
    for the same reason: the residue-distribution wording must not creep back
    in and read as though it still applied.
    """

    def test_module_docstring_names_the_rate_contract(self):
        """Module docstring states the rule, what it replaced, and its cost."""
        from app.services import paycheck_calculator  # pylint: disable=import-outside-toplevel

        doc = paycheck_calculator.__doc__ or ""
        assert "RATE" in doc
        assert "X-aw" in doc
        assert "R-HW" in doc
        # The supersession trail stays legible in both directions.
        assert "MED-05" in doc and "PA-07" in doc
        # Both spellings appear and both are load-bearing: this contract
        # SUPERSEDED MED-05 / PA-07, which had itself superseded F-127.
        assert "superseding" in doc and "superseded" in doc
        assert "N-239" in doc
        # The cost is stated rather than quietly dropped.
        assert "no longer sum to the annual salary exactly" in doc

    def test_calculate_paycheck_docstring_points_at_the_one_producer(self):
        """The function docstring names the producer and denies the list.

        A caller reading only the signature in an IDE tooltip has to learn
        that the owner's payday SET does NOT reach the gross -- that is the
        whole content of finding N-239.  The set is no longer an argument at
        all: plan step **balance:X-bh-1** moved the four judgements that DO
        read it onto the calendar the basis carries.
        """
        doc = calculate_paycheck.__doc__ or ""
        assert "gross_per_paycheck" in doc
        assert "RATE" in doc
        assert "the payday SET does not reach it" in doc

    def test_the_producer_states_what_it_gave_up(self):
        """``gross_per_paycheck`` carries the ruling and the measured cost."""
        from app.services.payroll_basis import (  # pylint: disable=import-outside-toplevel
            gross_per_paycheck,
        )

        doc = gross_per_paycheck.__doc__ or ""
        assert "R-HW" in doc
        assert "N-239" in doc
        assert "MED-05" in doc

    @pytest.mark.parametrize("registry,ident", [
        ("rulings.md", "| balance | R-HW |"),
        ("rulings.md", "| balance | R-IA |"),
        ("rulings.md", "| balance | R-IF |"),
        ("ledger.md", "| salary | N-391 "),
        ("ledger.md", "| pay_calendar | N-398 "),
        ("ledger.md", "| recurrence | N-399 "),
    ])
    def test_the_plan_identifiers_this_step_cites_actually_exist(
        self, registry, ident,
    ):
        """A citation is only worth as much as the row it names.

        The three cases above pin STRINGS in docstrings, which is what they are
        for -- the superseded wording must not creep back. But a string pin
        cannot tell a recorded ruling from an invented one, and an adversarial
        review of this step found exactly that state: `R-HW` cited eighteen
        times from `app/` and `tests/` while `rulings.md` ended at `R-HO`, and
        `N-390` / `N-391` cited while `ledger.md` ended at `N-388`. The plan
        gate could not see it, because **it runs only when a planning document
        is edited** and the code commit edits none.

        **This is deliberately scoped to the ids THIS step mints, and
        `tools/plan_gate/_rulings.py:135-141` says why the general arm cannot
        exist**: "an arc document may name no ruling id that has no
        `rulings.md` row" would fire on 88 live citations today, because an
        archived ruling's text stays in its archive. Scoped to a handful of
        live ids it is decidable, and it is the difference between grading the
        citation and grading the ruling.

        **`N-390` LEFT this list at plan step balance:X-bh-2, which closed
        it**, and the removal is the arm working rather than being weakened. A
        closed finding leaves `ledger.md` by design -- `ledger.md`'s own
        preamble says a row leaves when its fix SHIPS -- so pinning a closed id
        here would assert the opposite of the convention and fail forever. What
        replaces it is what that step LEFT live: `N-398`, `N-399` and the
        ruling pair `R-IA` / `R-IF`, all four cited from `app/` today.

        *It caught a real defect on the way out.* X-bh-2 committed its code and
        its plan documents separately, and the full suite ran against the
        documents in their PRE-change state -- so both commits were green alone
        and the pair was red. This case is the only thing in the corpus that
        would have said so, and it says it about the SECOND commit, which is
        the one no code-side gate looks at.
        """
        path = (
            pathlib.Path(__file__).resolve().parents[2] / "docs/plans" / registry
        )
        assert ident in path.read_text(encoding="utf-8"), (
            f"{ident.strip('| ')} is cited from app/ but has no row in "
            f"docs/plans/{registry}. conventions.md rules 1 and 9"
        )



# ── CRIT-03 / F-037 integration: calibration path SS cap ──────────


class TestCalibrationSSCapIntegration:
    """End-to-end integration: calibrated paycheck honours the SS cap.

    Verifies that calculate_paycheck plumbs cumulative_wages into the
    calibration branch correctly and that the year-total SS on the
    calibration path equals the bracket-path year-total to the cent
    for the F-037 worked example ($312k salary, 26 periods at $12,000).

    Pre-fix (audit 2026-05-19): the calibration branch never received
    cumulative_wages, so SS accrued for every period of the year
    (26 * $744.00 = $19,344.00), overstating FICA by $7,905.00 vs the
    correct $11,439.00 (= ss_wage_base * ss_rate = 184500 * 0.062).
    """

    @staticmethod
    def _high_earner_periods():
        """26 biweekly periods starting 2026-01-02."""
        start = date(2026, 1, 2)
        return [
            _period(
                start_date=date.fromordinal(
                    start.toordinal() + i * 14
                ),
                period_id=i + 1,
            )
            for i in range(26)
        ]

    @staticmethod
    def _fica_2026():
        """Seed 2026 FICA: ss_rate 0.062, ss_wage_base $184,500."""
        return FakeFicaConfig(
            ss_rate="0.062",
            ss_wage_base="184500",
        )

    def _tax_configs(self, simple_bracket_set, nc_state_config):
        """Tax configs with the 2026-seed wage base."""
        return {
            "bracket_set": simple_bracket_set,
            "state_config": nc_state_config,
            "fica_config": self._fica_2026(),
        }

    def test_calibration_year_ss_matches_bracket_year_ss(
        self, simple_bracket_set, nc_state_config,
    ):
        """C18-3 integration: 26-period year SS sums match to the cent.

        $312,000 salary, 26 periods, $12,000/period gross, calibration
        active with effective_ss_rate = statutory 0.062.  Both paths must
        produce the IRS-invariant year total $11,439.00.
        """
        profile = FakeProfile(
            annual_salary=312000,
            created_at=date(2026, 1, 1),
        )
        periods = self._high_earner_periods()
        tax_configs = self._tax_configs(
            simple_bracket_set, nc_state_config
        )
        cal = FakeCalibration(
            federal_rate="0.20000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        bracket = project_salary(payroll_basis(profile, periods), periods, tax_configs)
        calibrated = project_salary(
            payroll_basis(profile, periods), periods, tax_configs, calibration=cal,
        )

        bracket_year_ss = sum(r.taxes.social_security for r in bracket)
        cal_year_ss = sum(r.taxes.social_security for r in calibrated)

        # Bracket path year SS: 15 * (12000*0.062) + 279.00 + 10 * 0.00
        # = 15*744.00 + 279.00 = 11160.00 + 279.00 = 11439.00
        assert bracket_year_ss == Decimal("11439.00"), (
            f"Bracket year SS must be 11439.00 (ss_wage_base * ss_rate); "
            f"got {bracket_year_ss}"
        )
        assert cal_year_ss == bracket_year_ss, (
            f"Calibration year SS ({cal_year_ss}) must equal bracket "
            f"year SS ({bracket_year_ss}); pre-fix divergence was "
            f"$7,905.00 (F-037)"
        )

    def test_calibration_partial_period_at_cap(
        self, simple_bracket_set, nc_state_config,
    ):
        """C18-5 integration: period 16 SS = $279.00 (partial crossing).

        After 15 periods at $12,000 each, cumul = $180,000.  Period 16
        crosses the $184,500 cap: ss_taxable = $4,500.00, SS = $279.00.
        Periods 17-26 must be exactly $0.00.
        """
        profile = FakeProfile(
            annual_salary=312000,
            created_at=date(2026, 1, 1),
        )
        periods = self._high_earner_periods()
        tax_configs = self._tax_configs(
            simple_bracket_set, nc_state_config
        )
        cal = FakeCalibration(
            federal_rate="0.20000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        results = project_salary(
            payroll_basis(profile, periods), periods, tax_configs, calibration=cal,
        )

        # Periods 1-15 (indexes 0-14): full SS.  12000.00 * 0.062 = 744.00.
        for i in range(15):
            assert results[i].taxes.social_security == Decimal("744.00"), (
                f"Period {i+1}: SS expected 744.00, got "
                f"{results[i].taxes.social_security}"
            )

        # Period 16 (index 15): partial.  cumul=180000, ss_taxable=4500.
        # 4500 * 0.062 = 279.00.
        assert results[15].taxes.social_security == Decimal("279.00"), (
            f"Period 16 (partial crossing): SS expected 279.00, got "
            f"{results[15].taxes.social_security}"
        )

        # Periods 17-26 (indexes 16-25): cumul >= cap, SS = 0.00.
        for i in range(16, 26):
            assert results[i].taxes.social_security == Decimal("0.00"), (
                f"Period {i+1}: SS expected 0.00 (over cap), got "
                f"{results[i].taxes.social_security}"
            )
