"""
Shekel Budget App -- Unit Tests for Pension Calculator

Tests the pension benefit calculation including years of service,
high-salary average computation, and salary projection integration.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.pension_calculator import (
    PensionBenefit,
    calculate_benefit,
    project_salaries_by_year,
    _calculate_years_of_service,
    _compute_high_salary_average,
    ZERO,
)


# ── Fake Objects ─────────────────────────────────────────────────


class FakeRaise:
    def __init__(self, percentage=None, flat_amount=None,
                 effective_month=3, effective_year=2026,
                 is_recurring=False):
        self.percentage = Decimal(str(percentage)) if percentage else None
        self.flat_amount = Decimal(str(flat_amount)) if flat_amount else None
        self.effective_month = effective_month
        self.effective_year = effective_year
        self.is_recurring = is_recurring

        class _FakeType:
            name = "merit"
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
        result = project_salaries_by_year(
            Decimal("80000"), [], 2026, 2028,
        )
        assert len(result) == 3
        for year, salary in result:
            assert salary == Decimal("80000.00")

    def test_with_recurring_raise(self):
        """Recurring 3% raise compounds each year.

        FakePeriod uses month=12, so month >= effective_month=3 always.
        2026: 1 application  → 80000 * 1.03   = 82400.00
        2027: 2 applications → 80000 * 1.03^2 = 84872.00
        2028: 3 applications → 80000 * 1.03^3 = 87418.16
        """
        raises = [
            FakeRaise(percentage="0.03", effective_month=3,
                      effective_year=2026, is_recurring=True),
        ]
        result = project_salaries_by_year(
            Decimal("80000"), raises, 2026, 2028,
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

    def test_recurring_raise_highest_years_near_retirement(self):
        """With compounding raises, highest-paid years should be near retirement."""
        raises = [
            FakeRaise(percentage="0.025", effective_month=1,
                      effective_year=2026, is_recurring=True),
        ]
        salary_by_year = project_salaries_by_year(
            Decimal("90000"), raises, 2026, 2046,
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
