"""
Shekel Budget App -- Unit Tests for Pension Calculator

Tests the pension benefit calculation including years of service,
high-salary average computation, and salary projection integration.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import RaiseTypeEnum
from app.services.pension_calculator import (
    PensionBenefit,
    calculate_benefit,
    project_salaries_by_year,
    _calculate_years_of_service,
    _compute_high_salary_average,
    ZERO,
)
from app.utils.money import round_money


# ── Fake Objects ─────────────────────────────────────────────────


class FakeRaise:
    def __init__(self, percentage=None, flat_amount=None,
                 effective_month=3, effective_year=2026,
                 is_recurring=False, raise_type=RaiseTypeEnum.MERIT):
        self.percentage = Decimal(str(percentage)) if percentage else None
        self.flat_amount = Decimal(str(flat_amount)) if flat_amount else None
        self.effective_month = effective_month
        self.effective_year = effective_year
        self.is_recurring = is_recurring
        # The merit horizon discriminates recurring cola raises from
        # merit/custom raises by ``raise_type_id`` (never the name string);
        # resolve the id at construction (ref_cache is initialised by the
        # autouse conftest fixtures).
        self.raise_type_id = ref_cache.raise_type_id(raise_type)

        class _FakeType:
            name = raise_type.value
        self.raise_type = _FakeType()


# ── Tests ────────────────────────────────────────────────────────


class TestCalculateBenefit:
    def test_basic_benefit(self):
        """multiplier * years * average = expected benefit."""
        salary_by_year = [
            (2040, Decimal("80000")),
            (2041, Decimal("82000")),
            (2042, Decimal("84000")),
            (2043, Decimal("86000")),
        ]
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=4,
            hire_date=date(2018, 7, 1),
            planned_retirement_date=date(2043, 7, 1),
            salary_by_year=salary_by_year,
        )
        assert result.years_of_service == Decimal("25.00")
        assert result.high_salary_average == Decimal("83000.00")
        # 0.0185 * 25 * 83000 = 38387.50
        assert result.annual_benefit == Decimal("38387.50")
        assert result.monthly_benefit == Decimal("3198.96")

    def test_high_salary_average_correct_window(self):
        """Highest consecutive window selected."""
        salary_by_year = [
            (2035, Decimal("60000")),
            (2036, Decimal("70000")),
            (2037, Decimal("80000")),
            (2038, Decimal("90000")),
            (2039, Decimal("85000")),
        ]
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=3,
            hire_date=date(2010, 1, 1),
            planned_retirement_date=date(2040, 1, 1),
            salary_by_year=salary_by_year,
        )
        # Best 3-year window: 2037-2039 = (80000+90000+85000)/3 = 85000
        assert result.high_salary_average == Decimal("85000.00")

    def test_fewer_years_than_window(self):
        """Less data than window uses all available."""
        salary_by_year = [
            (2040, Decimal("80000")),
            (2041, Decimal("85000")),
        ]
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=4,
            hire_date=date(2030, 1, 1),
            planned_retirement_date=date(2042, 1, 1),
            salary_by_year=salary_by_year,
        )
        assert result.high_salary_average == Decimal("82500.00")

    def test_empty_salary_projections(self):
        """No salary data returns zero benefit."""
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=4,
            hire_date=date(2020, 1, 1),
            planned_retirement_date=date(2045, 1, 1),
            salary_by_year=[],
        )
        assert result.annual_benefit == ZERO
        assert result.monthly_benefit == ZERO

    def test_monthly_is_annual_divided_by_12(self):
        """Monthly benefit = annual / 12."""
        salary_by_year = [(2040, Decimal("100000"))]
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.02"),
            consecutive_high_years=1,
            hire_date=date(2020, 1, 1),
            planned_retirement_date=date(2040, 1, 1),
            salary_by_year=salary_by_year,
        )
        expected_annual = Decimal("0.02") * Decimal("20.00") * Decimal("100000")
        expected_monthly = (expected_annual / 12).quantize(Decimal("0.01"))
        assert result.monthly_benefit == expected_monthly

    def test_very_short_service(self):
        """Less than 1 year of service.

        days = (2026-06-01 - 2026-01-01) = 151
        years = (151 / 365.25).quantize(0.01) = 0.41
        high_salary_avg = 80000.00 (window min(4,1)=1)
        annual = 0.0185 * 0.41 * 80000 = 606.80
        monthly = 606.80 / 12 = 50.57
        """
        salary_by_year = [(2026, Decimal("80000"))]
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=4,
            hire_date=date(2026, 1, 1),
            planned_retirement_date=date(2026, 6, 1),
            salary_by_year=salary_by_year,
        )
        assert result.years_of_service == Decimal("0.41"), (
            f"Expected 0.41 years, got {result.years_of_service}"
        )
        assert result.annual_benefit == Decimal("606.80"), (
            f"Expected annual 606.80, got {result.annual_benefit}"
        )
        assert result.monthly_benefit == Decimal("50.57"), (
            f"Expected monthly 50.57, got {result.monthly_benefit}"
        )


class TestYearsOfService:
    def test_exact_years(self):
        result = _calculate_years_of_service(date(2000, 1, 1), date(2025, 1, 1))
        assert result == Decimal("25.00")

    def test_zero_service(self):
        result = _calculate_years_of_service(date(2026, 1, 1), date(2026, 1, 1))
        assert result == ZERO

    def test_negative_service(self):
        result = _calculate_years_of_service(date(2026, 1, 1), date(2025, 1, 1))
        assert result == ZERO

    def test_none_dates(self):
        assert _calculate_years_of_service(None, date(2040, 1, 1)) == ZERO
        assert _calculate_years_of_service(date(2020, 1, 1), None) == ZERO


class TestHighSalaryAverage:
    def test_single_year(self):
        avg, window = _compute_high_salary_average(
            [(2040, Decimal("80000"))], 1
        )
        assert avg == Decimal("80000.00")

    def test_highest_at_end(self):
        data = [
            (2036, Decimal("60000")),
            (2037, Decimal("70000")),
            (2038, Decimal("80000")),
            (2039, Decimal("90000")),
        ]
        avg, window = _compute_high_salary_average(data, 2)
        # Best 2-year: 2038-2039 = (80000+90000)/2 = 85000
        assert avg == Decimal("85000.00")
        assert len(window) == 2


class TestProjectSalariesByYear:
    def test_no_raises(self):
        # No raises: the merit horizon is irrelevant (nothing to freeze).
        result = project_salaries_by_year(
            Decimal("80000"), [], 2026, 2028, 5,
        )
        assert len(result) == 3
        for year, salary in result:
            assert salary == Decimal("80000.00")

    def test_with_recurring_raise(self):
        """Recurring 3% raise compounds each year (horizon does not bite).

        FakePeriod evaluates at month=12, so month >= effective_month=3
        always applies.  merit_horizon_years=5 -> cutoff 2026+5 = 2031,
        which is past the 2028 end year, so every year is <= cutoff and
        all raises apply exactly as before the horizon existed.
        2026: 1 application  -> 80000 * 1.03   = 82400.00
        2027: 2 applications -> 80000 * 1.03^2 = 84872.00
        2028: 3 applications -> 80000 * 1.03^3 = 87418.16
        """
        raises = [
            FakeRaise(percentage="0.03", effective_month=3,
                      effective_year=2026, is_recurring=True),
        ]
        result = project_salaries_by_year(
            Decimal("80000"), raises, 2026, 2028, 5,
        )
        # 80000 * 1.03 = 82400.00
        assert result[0][1] == Decimal("82400.00"), (
            f"2026 salary: expected 82400.00, got {result[0][1]}"
        )
        # 80000 * 1.03^2 = 84872.00
        assert result[1][1] == Decimal("84872.00"), (
            f"2027 salary: expected 84872.00, got {result[1][1]}"
        )
        # 80000 * 1.03^3 = 87418.16
        assert result[2][1] == Decimal("87418.16"), (
            f"2028 salary: expected 87418.16, got {result[2][1]}"
        )

    def test_recurring_cola_raise_highest_years_near_retirement(self):
        """A recurring COLA raise extrapolates to retirement (highest years last).

        A cola-type recurring raise keeps compounding past the merit
        cutoff, so a 2.5% cola from 2026 to 2046 still makes the last 4
        years the highest (unchanged from the pre-horizon behaviour for a
        cola raise).  merit_horizon_years=5 (cutoff 2031) does not stop a
        cola raise, so the salary rises every year through 2046.
        """
        raises = [
            FakeRaise(percentage="0.025", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.COLA),
        ]
        salary_by_year = project_salaries_by_year(
            Decimal("90000"), raises, 2026, 2046, 5,
        )
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=4,
            hire_date=date(2006, 6, 1),
            planned_retirement_date=date(2046, 6, 1),
            salary_by_year=salary_by_year,
        )
        high_years = [y for y, _ in result.high_salary_years]
        # The 4 highest consecutive salary years must be the last 4
        assert high_years == [2043, 2044, 2045, 2046], (
            f"Expected highest years near retirement, got {high_years}"
        )


class TestMeritHorizon:
    """The merit-horizon behaviour (Gate A ruling 3 / fork F4).

    Through the cutoff year (start_year + N) every raise applies; after
    the cutoff only recurring cola-type raises keep compounding, from the
    cutoff salary -- merit/custom raises stop but their earned effect
    persists in the base.  Cola discrimination is by ``raise_type_id``.
    All raises evaluate at December 1, so the effective month never gates
    the December-of-year application.
    """

    def test_merit_freezes_after_cutoff_cola_continues(self):
        """Merit + cola through cutoff; only cola compounds after it.

        base 100,000; merit 10% (Jan) + cola 10% (July), both recurring
        from 2026; start 2026, end 2031, N=2 -> cutoff = 2028.
        Through the cutoff both apply once per year, so by year Y the
        salary is 100000 * 1.10^(2*(Y-2025)) (merit and cola each applied
        Y-2025 times):
          2026: 100000 * 1.10^2 = 121,000.00
          2028 (cutoff): 100000 * 1.10^6 = 177,156.10
        After the cutoff merit FREEZES at the 2028 base and only the 10%
        cola compounds from it:
          2029: 177,156.10 * 1.10   = 194,871.71  (NOT 100000*1.10^8 =
                                       214,358.88, which is both-continue)
          2031: 177,156.10 * 1.10^3 = 235,794.77
        """
        raises = [
            FakeRaise(percentage="0.10", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.MERIT),
            FakeRaise(percentage="0.10", effective_month=7,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.COLA),
        ]
        result = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2031, 2,
        ))
        # 100000 * 1.10 * 1.10 = 121000.00
        assert result[2026] == Decimal("121000.00")
        # 100000 * 1.10^6 = 177156.10 (cutoff, both raises applied)
        assert result[2028] == Decimal("177156.10")
        # 177156.10 * 1.10 = 194871.71 (merit frozen; cola only)
        assert result[2029] == Decimal("194871.71")
        # 177156.10 * 1.10^3 = 235794.77 (three post-cutoff cola steps)
        assert result[2031] == Decimal("235794.77")

    def test_cola_only_extrapolates_without_double_count(self):
        """A pure recurring cola compounds uninterrupted across the cutoff.

        base 100,000; cola 10% (July) recurring from 2026; start 2026, end
        2030, N=2 -> cutoff 2028.  With only a cola raise the re-anchored
        post-cutoff compounding must reproduce the same 100000 * 1.10^k
        curve (no occurrence dropped, none double-counted):
          2026: 100000 * 1.10   = 110,000.00
          2028 (cutoff): 100000 * 1.10^3 = 133,100.00
          2029: 100000 * 1.10^4 = 146,410.00
          2030: 100000 * 1.10^5 = 161,051.00
        """
        raises = [
            FakeRaise(percentage="0.10", effective_month=7,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.COLA),
        ]
        result = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2030, 2,
        ))
        assert result[2026] == Decimal("110000.00")   # 100000 * 1.10
        assert result[2028] == Decimal("133100.00")   # 100000 * 1.10^3
        assert result[2029] == Decimal("146410.00")   # 100000 * 1.10^4
        assert result[2030] == Decimal("161051.00")   # 100000 * 1.10^5

    def test_merit_only_plateaus_after_cutoff(self):
        """A pure recurring merit stops compounding after the cutoff.

        base 100,000; merit 10% (Jan) recurring from 2026; start 2026, end
        2030, N=2 -> cutoff 2028.  With no cola raise nothing compounds
        after the cutoff, so the salary plateaus at the cutoff value:
          2026: 100000 * 1.10   = 110,000.00
          2028 (cutoff): 100000 * 1.10^3 = 133,100.00
          2029: 133,100.00 (frozen)
          2030: 133,100.00 (frozen)
        """
        raises = [
            FakeRaise(percentage="0.10", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.MERIT),
        ]
        result = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2030, 2,
        ))
        assert result[2026] == Decimal("110000.00")   # 100000 * 1.10
        assert result[2028] == Decimal("133100.00")   # 100000 * 1.10^3
        assert result[2029] == Decimal("133100.00")   # frozen at cutoff
        assert result[2030] == Decimal("133100.00")   # frozen at cutoff

    def test_future_scheduled_cola_is_not_pulled_before_its_start(self):
        """A COLA that starts after the cutoff first applies in ITS year (H1).

        base 100,000; a 10% recurring COLA effective 2031; start 2026,
        end 2032, N=2 -> cutoff 2028, re-anchor year 2029.  The re-anchor
        must floor at the raise's OWN effective year
        (max(2031, 2029) = 2031), never pull it backward:
          2026-2030: 100,000.00  (the COLA does not exist yet)
          2031: 100,000 * 1.10   = 110,000.00  (first application)
          2032: 100,000 * 1.10^2 = 121,000.00
        The pre-fix bug re-anchored it to 2029, yielding 110,000.00 in
        2029 and 146,410.00 (1.10^4) in 2032.
        """
        raises = [
            FakeRaise(percentage="0.10", effective_month=7,
                      effective_year=2031, is_recurring=True,
                      raise_type=RaiseTypeEnum.COLA),
        ]
        result = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2032, 2,
        ))
        assert result[2028] == Decimal("100000.00")  # cutoff, COLA not live
        assert result[2029] == Decimal("100000.00")  # NOT 110,000 (H1 bug)
        assert result[2030] == Decimal("100000.00")
        assert result[2031] == Decimal("110000.00")  # 100000 * 1.10
        assert result[2032] == Decimal("121000.00")  # 100000 * 1.10^2

    def test_mixed_flat_and_percentage_colas_horizon_invariant(self):
        """Mixed flat+pct COLAs ARE horizon-invariant (L4 fixed, chronological walk).

        base 100,000; a flat $1,000 recurring COLA + a 10% recurring COLA,
        both effective 2026.  ``apply_raises`` now applies each APPLICATION
        on the date it lands, flat before percentage within a date (M-01),
        so the money compounds in the order it arrives:

          2026: (100,000 + 1,000) * 1.10 = 111,100.00
          2027: (111,100 + 1,000) * 1.10 = 123,310.00
          2028: (123,310 + 1,000) * 1.10 = 136,741.00
          2029: (136,741 + 1,000) * 1.10 = 151,515.10
          2030: (151,515.10 + 1,000) * 1.10 = 167,766.61

        **This test is not a tautology**: ``project_salaries_by_year``
        still splits at the cutoff and still re-anchors the post-cutoff
        colas, so the N=2 and N=10 calls run genuinely different code
        paths.  They agree because the WALK is now order-correct, not
        because the horizon was removed -- revert the walk and the two
        diverge again by 801.02.

        **What this test used to assert**, as
        ``..._pinned_not_horizon_invariant``: ``horizon[2028] ==
        137,093.00``, ``continuous[2030] == 169,103.55`` and
        ``horizon[2030] == 168,302.53``.  Every one of those credited 10%
        growth to flat dollars that had not arrived yet -- the old rule
        added all five $1,000s to the base and only then compounded.  The
        801.02 gap it documented as review finding L4 was a symptom of
        that ordering, not of the split, which is why removing the split
        was never the fix.  Both pins re-derived per that test's own
        instruction.
        """
        raises = [
            FakeRaise(flat_amount="1000", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.COLA),
            FakeRaise(percentage="0.10", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.COLA),
        ]
        continuous = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2030, 10,
        ))
        horizon = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2030, 2,
        ))
        # Third year of the walk above, and the cutoff year for N=2.
        assert horizon[2028] == Decimal("136741.00")
        # Fifth year of the walk above.
        assert continuous[2030] == Decimal("167766.61")
        # The split path reaches the same place: this is the assertion
        # that inverts, and it was 168,302.53 before the walk was fixed.
        assert horizon[2030] == Decimal("167766.61")
        # The invariance itself, across every year rather than one pin.
        assert horizon == continuous

    def test_real_shaped_cola_and_merit(self):
        """Real-shaped 3% July cola + 2.5% January merit, N=5.

        base 100,000; both recurring from 2026; start 2026, end 2033,
        N=5 -> cutoff 2031.  Through the cutoff both apply:
          2026: 100000 * 1.025 * 1.03 = 105,575.00
        After the cutoff only the 3% cola compounds, from the 2031 salary
        (call it S).  The 2.5% merit is frozen, so:
          2032 == round(S * 1.03)       (cola only -- NOT S * 1.025 * 1.03)
          2033 == round(S * 1.03^2)
        Asserting 2032/2033 against S proves the merit stopped while the
        cola continued, without hand-computing the 6th-power cutoff salary.
        """
        raises = [
            FakeRaise(percentage="0.03", effective_month=7,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.COLA),
            FakeRaise(percentage="0.025", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.MERIT),
        ]
        result = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2033, 5,
        ))
        # 100000 * 1.025 * 1.03 = 105575.00 (through cutoff, both apply)
        assert result[2026] == Decimal("105575.00")
        cutoff_salary = result[2031]
        # Post-cutoff: only the 3% cola compounds from the cutoff salary.
        assert result[2032] == round_money(cutoff_salary * Decimal("1.03"))
        assert result[2033] == round_money(
            cutoff_salary * Decimal("1.03") * Decimal("1.03")
        )
        # And the merit is genuinely frozen: applying it too would give
        # a strictly larger 2032 than the cola-only value.
        assert result[2032] < round_money(
            cutoff_salary * Decimal("1.025") * Decimal("1.03")
        )
