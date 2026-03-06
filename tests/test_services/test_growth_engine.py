"""
Shekel Budget App — Unit Tests for Compound Growth Engine

Tests the growth projection service including compound growth,
contribution limits, employer contributions, and year boundary resets.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import pytest

from app.services.growth_engine import (
    ProjectedBalance,
    calculate_employer_contribution,
    generate_projection_periods,
    project_balance,
    ZERO,
    TWO_PLACES,
)


# ── Fake Objects ─────────────────────────────────────────────────


class FakePeriod:
    def __init__(self, start_date, end_date, period_id=1):
        self.id = period_id
        self.start_date = start_date
        self.end_date = end_date
        self.period_index = period_id


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def biweekly_periods():
    """10 biweekly periods starting Jan 2, 2026."""
    start = date(2026, 1, 2)
    periods = []
    for i in range(10):
        s = date.fromordinal(start.toordinal() + i * 14)
        e = date.fromordinal(s.toordinal() + 13)
        periods.append(FakePeriod(start_date=s, end_date=e, period_id=i + 1))
    return periods


@pytest.fixture
def cross_year_periods():
    """Periods that cross a year boundary (Dec 2026 to Jan 2027)."""
    return [
        FakePeriod(date(2026, 12, 5), date(2026, 12, 18), 1),
        FakePeriod(date(2026, 12, 19), date(2027, 1, 1), 2),
        FakePeriod(date(2027, 1, 2), date(2027, 1, 15), 3),
        FakePeriod(date(2027, 1, 16), date(2027, 1, 29), 4),
    ]


# ── Tests: calculate_employer_contribution ──────────────────────


class TestEmployerContribution:
    def test_none_type_returns_zero(self):
        params = {"type": "none", "gross_biweekly": Decimal("2500")}
        assert calculate_employer_contribution(params, Decimal("200")) == ZERO

    def test_flat_percentage(self):
        params = {
            "type": "flat_percentage",
            "flat_percentage": Decimal("0.05"),
            "gross_biweekly": Decimal("2500"),
        }
        result = calculate_employer_contribution(params, Decimal("0"))
        assert result == Decimal("125.00")

    def test_match_full(self):
        """Employee contributes >= matchable amount."""
        params = {
            "type": "match",
            "match_percentage": Decimal("1.0"),
            "match_cap_percentage": Decimal("0.06"),
            "gross_biweekly": Decimal("2500"),
        }
        # Employee contributes $150 (6% of $2500), match 100% up to 6%
        result = calculate_employer_contribution(params, Decimal("150"))
        assert result == Decimal("150.00")

    def test_match_partial(self):
        """Employee contributes less than matchable amount."""
        params = {
            "type": "match",
            "match_percentage": Decimal("1.0"),
            "match_cap_percentage": Decimal("0.06"),
            "gross_biweekly": Decimal("2500"),
        }
        # Employee contributes only $100 (less than $150 cap)
        result = calculate_employer_contribution(params, Decimal("100"))
        assert result == Decimal("100.00")

    def test_match_zero_employee(self):
        """No employee contribution → no match."""
        params = {
            "type": "match",
            "match_percentage": Decimal("1.0"),
            "match_cap_percentage": Decimal("0.06"),
            "gross_biweekly": Decimal("2500"),
        }
        result = calculate_employer_contribution(params, Decimal("0"))
        assert result == ZERO

    def test_none_params_returns_zero(self):
        assert calculate_employer_contribution(None, Decimal("200")) == ZERO


# ── Tests: project_balance ──────────────────────────────────────


class TestProjectBalance:
    def test_basic_growth_no_contributions(self, biweekly_periods):
        """Balance grows at assumed rate with no contributions."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=biweekly_periods[:1],
        )
        assert len(result) == 1
        assert result[0].end_balance > Decimal("10000")
        assert result[0].contribution == ZERO
        assert result[0].employer_contribution == ZERO
        assert result[0].growth > ZERO

    def test_growth_compounds_over_periods(self, biweekly_periods):
        """Growth compounds across multiple periods."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=biweekly_periods,
        )
        assert len(result) == 10
        # Each period's end_balance should be the next period's start.
        for i in range(1, len(result)):
            assert result[i].start_balance == result[i - 1].end_balance

    def test_with_periodic_contributions(self, biweekly_periods):
        """Contributions added each period."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=biweekly_periods[:3],
            periodic_contribution=Decimal("500"),
        )
        for pb in result:
            assert pb.contribution == Decimal("500")
        # End balance should reflect growth + contributions.
        assert result[-1].end_balance > Decimal("10000") + Decimal("1500")

    def test_contribution_limit_caps_contributions(self, biweekly_periods):
        """Contributions capped at annual limit."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=biweekly_periods,
            periodic_contribution=Decimal("5000"),
            annual_contribution_limit=Decimal("7000"),
        )
        total_contributions = sum(pb.contribution for pb in result)
        assert total_contributions == Decimal("7000")

    def test_year_boundary_resets_limit(self, cross_year_periods):
        """Annual limit resets at year boundary."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=cross_year_periods,
            periodic_contribution=Decimal("3000"),
            annual_contribution_limit=Decimal("5000"),
        )
        # First 2 periods in 2026: $3000 + $2000 (capped at $5000)
        # Period 3 in 2027: $3000 (limit reset)
        # Period 4 in 2027: $2000 (capped)
        total = sum(pb.contribution for pb in result)
        assert total == Decimal("10000")

    def test_employer_flat_percentage(self, biweekly_periods):
        """Employer flat percentage added each period."""
        employer_params = {
            "type": "flat_percentage",
            "flat_percentage": Decimal("0.05"),
            "gross_biweekly": Decimal("2500"),
        }
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=biweekly_periods[:1],
            periodic_contribution=Decimal("200"),
            employer_params=employer_params,
        )
        assert result[0].employer_contribution == Decimal("125.00")

    def test_employer_match(self, biweekly_periods):
        """Employer match calculated correctly."""
        employer_params = {
            "type": "match",
            "match_percentage": Decimal("1.0"),
            "match_cap_percentage": Decimal("0.06"),
            "gross_biweekly": Decimal("2500"),
        }
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=biweekly_periods[:1],
            periodic_contribution=Decimal("150"),
            employer_params=employer_params,
        )
        assert result[0].employer_contribution == Decimal("150.00")

    def test_zero_return_rate(self, biweekly_periods):
        """Only contributions grow the balance."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=biweekly_periods[:3],
            periodic_contribution=Decimal("500"),
        )
        for pb in result:
            assert pb.growth == ZERO
        assert result[-1].end_balance == Decimal("11500.00")

    def test_zero_contribution(self, biweekly_periods):
        """Only growth, no contributions."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=biweekly_periods[:3],
            periodic_contribution=ZERO,
        )
        for pb in result:
            assert pb.contribution == ZERO

    def test_empty_periods(self):
        """Empty periods list returns empty result."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=[],
        )
        assert result == []

    def test_starting_ytd_reduces_limit(self, biweekly_periods):
        """Mid-year start with existing contributions."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=biweekly_periods,
            periodic_contribution=Decimal("1000"),
            annual_contribution_limit=Decimal("7000"),
            ytd_contributions_start=Decimal("5000"),
        )
        # Only $2000 remaining limit.
        total = sum(pb.contribution for pb in result)
        assert total == Decimal("2000")

    def test_negative_return_rate(self, biweekly_periods):
        """Balance decreases with negative return."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("-0.10"),
            periods=biweekly_periods[:1],
        )
        assert result[0].growth < ZERO
        assert result[0].end_balance < Decimal("10000")

    def test_employer_does_not_count_against_limit(self, biweekly_periods):
        """Employer contributions don't reduce employee contribution limit."""
        employer_params = {
            "type": "flat_percentage",
            "flat_percentage": Decimal("0.05"),
            "gross_biweekly": Decimal("2500"),
        }
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=biweekly_periods[:2],
            periodic_contribution=Decimal("3000"),
            employer_params=employer_params,
            annual_contribution_limit=Decimal("5000"),
        )
        # Employee: $3000 + $2000 = $5000 (capped)
        # Employer: $125 + $125 = $250 (not capped)
        total_emp = sum(pb.contribution for pb in result)
        total_employer = sum(pb.employer_contribution for pb in result)
        assert total_emp == Decimal("5000")
        assert total_employer == Decimal("250.00")

    def test_no_limit_brokerage(self, biweekly_periods):
        """Brokerage accounts have no contribution limit."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=biweekly_periods,
            periodic_contribution=Decimal("5000"),
            annual_contribution_limit=None,
        )
        total = sum(pb.contribution for pb in result)
        assert total == Decimal("50000")

    def test_contribution_limit_exactly_hit(self, biweekly_periods):
        """Last contribution partially applied to hit limit exactly."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=biweekly_periods[:3],
            periodic_contribution=Decimal("2500"),
            annual_contribution_limit=Decimal("7000"),
        )
        # Period 1: $2500, Period 2: $2500, Period 3: $2000 (capped)
        assert result[0].contribution == Decimal("2500")
        assert result[1].contribution == Decimal("2500")
        assert result[2].contribution == Decimal("2000")

    def test_ytd_contributions_tracked(self, biweekly_periods):
        """ytd_contributions increments correctly."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=biweekly_periods[:3],
            periodic_contribution=Decimal("500"),
        )
        assert result[0].ytd_contributions == Decimal("500")
        assert result[1].ytd_contributions == Decimal("1000")
        assert result[2].ytd_contributions == Decimal("1500")

    def test_period_days_affect_growth(self):
        """Longer periods produce more growth."""
        short = [FakePeriod(date(2026, 1, 2), date(2026, 1, 8), 1)]
        long = [FakePeriod(date(2026, 1, 2), date(2026, 1, 29), 1)]

        short_result = project_balance(
            Decimal("10000"), Decimal("0.07"), short,
        )
        long_result = project_balance(
            Decimal("10000"), Decimal("0.07"), long,
        )
        assert long_result[0].growth > short_result[0].growth


class TestGenerateProjectionPeriods:
    def test_basic_generation(self):
        """Generates biweekly periods from start to end."""
        periods = generate_projection_periods(date(2026, 3, 6), date(2026, 6, 1))
        assert len(periods) > 0
        for p in periods:
            assert hasattr(p, "id")
            assert hasattr(p, "start_date")
            assert hasattr(p, "end_date")
            assert (p.end_date - p.start_date).days == 13  # 14-day period

    def test_period_count_one_year(self):
        """One year produces ~26-27 biweekly periods."""
        periods = generate_projection_periods(date(2026, 1, 1), date(2026, 12, 31))
        # 365 days / 14 = 26.07; last period starts day 365 (Dec 31), so 27
        assert len(periods) == 27

    def test_period_count_twenty_years(self):
        """Twenty years produces ~520 biweekly periods."""
        periods = generate_projection_periods(date(2026, 1, 1), date(2045, 12, 31))
        assert 519 <= len(periods) <= 523

    def test_sequential_ids(self):
        """Period IDs are sequential starting from 1."""
        periods = generate_projection_periods(date(2026, 1, 1), date(2026, 3, 1))
        for i, p in enumerate(periods):
            assert p.id == i + 1

    def test_no_gaps_between_periods(self):
        """Each period starts the day after the previous one ends."""
        periods = generate_projection_periods(date(2026, 1, 1), date(2026, 6, 1))
        for i in range(1, len(periods)):
            expected_start = date.fromordinal(periods[i - 1].end_date.toordinal() + 1)
            assert periods[i].start_date == expected_start

    def test_end_before_start_returns_empty(self):
        """End date before start returns empty list."""
        periods = generate_projection_periods(date(2026, 6, 1), date(2026, 1, 1))
        assert periods == []

    def test_same_day_returns_one_period(self):
        """Start equals end still returns one period."""
        periods = generate_projection_periods(date(2026, 1, 1), date(2026, 1, 1))
        assert len(periods) == 1

    def test_works_with_project_balance(self):
        """Synthetic periods integrate with project_balance."""
        periods = generate_projection_periods(date(2026, 1, 1), date(2026, 12, 31))
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=periods,
            periodic_contribution=Decimal("500"),
        )
        assert len(result) == len(periods)
        assert result[-1].end_balance > Decimal("10000") + Decimal("500") * len(periods)

    def test_year_boundaries_correct_for_limit_reset(self):
        """Periods crossing year boundary have correct year in start_date."""
        periods = generate_projection_periods(date(2026, 12, 20), date(2027, 1, 31))
        years = [p.start_date.year for p in periods]
        assert 2026 in years
        assert 2027 in years
