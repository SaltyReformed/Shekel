"""
Shekel Budget App -- Unit Tests for Compound Growth Engine

Tests the growth projection service including compound growth,
contribution limits, employer contributions, and year boundary resets.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import pytest

from app.services.growth_engine import (
    ContributionRecord,
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
        """Balance grows at assumed rate with no contributions.

        period_return = (1.07)^(13/365) - 1
        growth = (10000 * period_return).quantize(0.01) = 24.13
        end_balance = 10000 + 24.13 = 10024.13
        """
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=biweekly_periods[:1],
        )
        assert len(result) == 1
        assert result[0].contribution == ZERO
        assert result[0].employer_contribution == ZERO
        # (1.07)^(13/365) - 1 ≈ 0.002413; 10000 * 0.002413 = 24.13
        assert result[0].growth == Decimal("24.13"), (
            f"Period 0 growth: expected 24.13, got {result[0].growth}"
        )
        assert result[0].end_balance == Decimal("10024.13"), (
            f"Period 0 end_balance: expected 10024.13, "
            f"got {result[0].end_balance}"
        )

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
        """Contributions added each period.

        Each period has 13 days. Growth compounds on starting balance,
        then $500 contribution is added.
        P0: 10000 + 24.13 + 500 = 10524.13
        P1: 10524.13 + 25.39 + 500 = 11049.52
        P2: 11049.52 + 26.66 + 500 = 11576.18
        """
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=biweekly_periods[:3],
            periodic_contribution=Decimal("500"),
        )
        for pb in result:
            assert pb.contribution == Decimal("500"), (
                f"Expected contribution 500, got {pb.contribution}"
            )
        # Period 0: growth on 10000
        assert result[0].growth == Decimal("24.13"), (
            f"P0 growth: expected 24.13, got {result[0].growth}"
        )
        assert result[0].end_balance == Decimal("10524.13"), (
            f"P0 end: expected 10524.13, got {result[0].end_balance}"
        )
        # Period 1: growth on 10524.13
        assert result[1].growth == Decimal("25.39"), (
            f"P1 growth: expected 25.39, got {result[1].growth}"
        )
        assert result[1].end_balance == Decimal("11049.52"), (
            f"P1 end: expected 11049.52, got {result[1].end_balance}"
        )
        # Period 2: growth on 11049.52
        assert result[2].growth == Decimal("26.66"), (
            f"P2 growth: expected 26.66, got {result[2].growth}"
        )
        assert result[2].end_balance == Decimal("11576.18"), (
            f"P2 end: expected 11576.18, got {result[2].end_balance}"
        )

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
        """Balance decreases with negative return.

        period_return = (0.90)^(13/365) - 1
        growth = (10000 * period_return).quantize(0.01) = -37.46
        end_balance = 10000 - 37.46 = 9962.54
        """
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("-0.10"),
            periods=biweekly_periods[:1],
        )
        # (0.90)^(13/365) - 1 ≈ -0.003746; 10000 * -0.003746 = -37.46
        assert result[0].growth == Decimal("-37.46"), (
            f"Expected growth -37.46, got {result[0].growth}"
        )
        assert result[0].end_balance == Decimal("9962.54"), (
            f"Expected end_balance 9962.54, got {result[0].end_balance}"
        )

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
        """Synthetic periods integrate with project_balance.

        Independent computation replicates the growth engine formula:
        For each period (14-day cadence → 13 actual days per period):
          period_return = (1 + 0.07)^(period_days / 365) - 1
          growth = (balance * period_return).quantize(0.01, ROUND_HALF_UP)
          balance = balance + growth + 500
        Starting from balance = 10,000 over 27 periods (one calendar year).
        """
        periods = generate_projection_periods(date(2026, 1, 1), date(2026, 12, 31))
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=periods,
            periodic_contribution=Decimal("500"),
        )
        assert len(result) == len(periods)

        # Independent loop computing the expected final balance.
        expected_balance = Decimal("10000")
        for period in periods:
            period_days = (period.end_date - period.start_date).days
            period_return_rate = (
                (1 + Decimal("0.07"))
                ** (Decimal(str(period_days)) / Decimal("365"))
                - 1
            )
            growth = (expected_balance * period_return_rate).quantize(
                TWO_PLACES, rounding=ROUND_HALF_UP
            )
            expected_balance = expected_balance + growth + Decimal("500")

        assert result[-1].end_balance == expected_balance

    def test_year_boundaries_correct_for_limit_reset(self):
        """Periods crossing year boundary have correct year in start_date."""
        periods = generate_projection_periods(date(2026, 12, 20), date(2027, 1, 31))
        years = [p.start_date.year for p in periods]
        assert 2026 in years
        assert 2027 in years


# ── Tests: Contribution-Aware Projection ──────────────────────


class TestContributionAwareProjection:
    """Tests for contribution-list-aware projections.

    Verifies that project_balance() correctly uses per-period contribution
    amounts from ContributionRecord instances, with proper fallback to
    periodic_contribution, annual limit capping, employer match computation,
    YTD tracking, and is_confirmed propagation.
    """

    def test_no_contributions_unchanged(self, biweekly_periods):
        """contributions=None produces identical output to omitting the parameter."""
        kwargs = {
            "current_balance": Decimal("10000"),
            "assumed_annual_return": Decimal("0.07"),
            "periods": biweekly_periods[:3],
            "periodic_contribution": Decimal("500"),
        }
        baseline = project_balance(**kwargs)
        with_none = project_balance(**kwargs, contributions=None)
        assert baseline == with_none

    def test_empty_contributions_unchanged(self, biweekly_periods):
        """contributions=[] produces identical output to contributions=None."""
        kwargs = {
            "current_balance": Decimal("10000"),
            "assumed_annual_return": Decimal("0.07"),
            "periods": biweekly_periods[:3],
            "periodic_contribution": Decimal("500"),
        }
        baseline = project_balance(**kwargs, contributions=None)
        with_empty = project_balance(**kwargs, contributions=[])
        assert baseline == with_empty

    def test_contributions_applied_per_period(self, biweekly_periods):
        """Explicit contributions for all periods override periodic_contribution.

        0% return, start=$10,000.
        P0: +$300 = $10,300
        P1: +$500 = $10,800
        P2: +$200 = $11,000
        """
        periods = biweekly_periods[:3]
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("300"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("500"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("200"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=Decimal("999"),  # Should NOT be used.
            contributions=contributions,
        )
        assert result[0].contribution == Decimal("300")
        assert result[1].contribution == Decimal("500")
        assert result[2].contribution == Decimal("200")
        assert result[0].end_balance == Decimal("10300")
        assert result[1].end_balance == Decimal("10800")
        assert result[2].end_balance == Decimal("11000")

    def test_contributions_partial_with_fallback(self, biweekly_periods):
        """Periods without contributions fall back to periodic_contribution.

        0% return, start=$10,000, periodic=$200.
        P0: +$300 (record) = $10,300
        P1: +$500 (record) = $10,800
        P2: +$100 (record) = $10,900
        P3: +$200 (fallback) = $11,100
        P4: +$200 (fallback) = $11,300
        """
        periods = biweekly_periods[:5]
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("300"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("500"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("100"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=Decimal("200"),
            contributions=contributions,
        )
        assert result[0].contribution == Decimal("300")
        assert result[1].contribution == Decimal("500")
        assert result[2].contribution == Decimal("100")
        assert result[3].contribution == Decimal("200")
        assert result[4].contribution == Decimal("200")
        assert result[4].end_balance == Decimal("11300")

    def test_zero_contribution_does_not_fallback(self, biweekly_periods):
        """A $0 contribution record means no contribution -- not a fallback.

        0% return, start=$10,000, periodic=$500.
        P0: +$500 (record) = $10,500
        P1: +$0 (explicit zero, NOT fallback) = $10,500
        P2: +$500 (record) = $11,000
        """
        periods = biweekly_periods[:3]
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("500"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("0"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("500"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=Decimal("500"),
            contributions=contributions,
        )
        assert result[1].contribution == Decimal("0")
        assert result[1].end_balance == Decimal("10500")
        assert result[2].end_balance == Decimal("11000")

    def test_annual_limit_caps_contributions(self, biweekly_periods):
        """Contributions capped at annual limit.

        0% return, start=$10,000, limit=$1,000.
        P0: min($300, $1,000) = $300, remaining=$700
        P1: min($300, $700) = $300, remaining=$400
        P2: min($300, $400) = $300, remaining=$100
        P3: min($300, $100) = $100, remaining=$0
        Total: $1,000.
        """
        periods = biweekly_periods[:4]
        contributions = [
            ContributionRecord(p.start_date, Decimal("300"), True)
            for p in periods
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            contributions=contributions,
            annual_contribution_limit=Decimal("1000"),
        )
        total = sum(pb.contribution for pb in result)
        assert total == Decimal("1000")
        assert result[0].contribution == Decimal("300")
        assert result[1].contribution == Decimal("300")
        assert result[2].contribution == Decimal("300")
        assert result[3].contribution == Decimal("100")

    def test_year_boundary_resets_with_contributions(self, cross_year_periods):
        """Annual limit resets at year boundary with contribution records.

        0% return, start=$10,000, limit=$5,000.
        P0 (2026): min($3,000, $5,000) = $3,000, remaining=$2,000
        P1 (2026): min($3,000, $2,000) = $2,000, remaining=$0
        Year boundary -- remaining resets to $5,000.
        P2 (2027): min($3,000, $5,000) = $3,000, remaining=$2,000
        P3 (2027): min($3,000, $2,000) = $2,000, remaining=$0
        Total: $10,000.
        """
        contributions = [
            ContributionRecord(p.start_date, Decimal("3000"), True)
            for p in cross_year_periods
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=cross_year_periods,
            contributions=contributions,
            annual_contribution_limit=Decimal("5000"),
        )
        assert result[0].contribution == Decimal("3000")
        assert result[1].contribution == Decimal("2000")
        assert result[2].contribution == Decimal("3000")
        assert result[3].contribution == Decimal("2000")
        total = sum(pb.contribution for pb in result)
        assert total == Decimal("10000")

    def test_employer_match_uses_period_contribution(self, biweekly_periods):
        """Employer match computed from per-period contribution, not static.

        Match: 100% up to 6% of $2,500 gross = $150 matchable.
        Record contribution=$100 -- match=min($100, $150) * 1.0 = $100.
        Static periodic=$150 would give match=$150 -- must NOT be used.
        """
        periods = biweekly_periods[:1]
        employer_params = {
            "type": "match",
            "match_percentage": Decimal("1.0"),
            "match_cap_percentage": Decimal("0.06"),
            "gross_biweekly": Decimal("2500"),
        }
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("100"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=Decimal("150"),
            employer_params=employer_params,
            contributions=contributions,
        )
        # Match is on $100 (from record), not $150 (static).
        assert result[0].employer_contribution == Decimal("100.00")
        assert result[0].contribution == Decimal("100")

    def test_employer_match_with_varying_contributions(self, biweekly_periods):
        """Employer match varies with different per-period contributions.

        Match: 100% up to 6% of $2,500 = $150 matchable.
        P0: $100 -- match=$100
        P1: $200 -- match=min($200, $150) = $150
        P2: $50 -- match=$50
        """
        periods = biweekly_periods[:3]
        employer_params = {
            "type": "match",
            "match_percentage": Decimal("1.0"),
            "match_cap_percentage": Decimal("0.06"),
            "gross_biweekly": Decimal("2500"),
        }
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("100"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("50"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            employer_params=employer_params,
            contributions=contributions,
        )
        assert result[0].employer_contribution == Decimal("100.00")
        assert result[1].employer_contribution == Decimal("150.00")
        assert result[2].employer_contribution == Decimal("50.00")

    def test_is_confirmed_propagated(self, biweekly_periods):
        """is_confirmed flag matches input records; fallback periods are False."""
        periods = biweekly_periods[:3]
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("100"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("200"), False),
            # P2 has no record -- fallback.
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=Decimal("300"),
            contributions=contributions,
        )
        assert result[0].is_confirmed is True
        assert result[1].is_confirmed is False
        assert result[2].is_confirmed is False

    def test_is_confirmed_all_confirmed_same_date(self, biweekly_periods):
        """Multiple confirmed contributions on same date -- is_confirmed=True."""
        periods = biweekly_periods[:1]
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 2), Decimal("300"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            contributions=contributions,
        )
        assert result[0].is_confirmed is True
        assert result[0].contribution == Decimal("500")

    def test_is_confirmed_mixed_same_date(self, biweekly_periods):
        """Confirmed + projected on same date -- is_confirmed=False."""
        periods = biweekly_periods[:1]
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 2), Decimal("300"), False),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            contributions=contributions,
        )
        assert result[0].is_confirmed is False
        assert result[0].contribution == Decimal("500")

    def test_multiple_contributions_same_date_summed(self, biweekly_periods):
        """Two contributions on the same period are summed.

        P0: $200 + $300 = $500.
        End: $10,000 + $500 = $10,500.
        """
        periods = biweekly_periods[:1]
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 2), Decimal("300"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            contributions=contributions,
        )
        assert result[0].contribution == Decimal("500")
        assert result[0].end_balance == Decimal("10500")

    def test_unsorted_contributions_handled(self, biweekly_periods):
        """Non-chronological contributions produce the same result as sorted."""
        periods = biweekly_periods[:3]
        sorted_contribs = [
            ContributionRecord(date(2026, 1, 2), Decimal("100"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("300"), True),
        ]
        unsorted_contribs = list(reversed(sorted_contribs))

        kwargs = {
            "current_balance": Decimal("10000"),
            "assumed_annual_return": Decimal("0"),
            "periods": periods,
        }
        sorted_result = project_balance(**kwargs, contributions=sorted_contribs)
        unsorted_result = project_balance(
            **kwargs, contributions=unsorted_contribs
        )
        assert sorted_result == unsorted_result

    def test_contribution_record_validation_negative(self):
        """Negative contribution amount raises ValueError."""
        with pytest.raises(ValueError, match="amount must be >= 0"):
            ContributionRecord(date(2026, 1, 2), Decimal("-100"), True)

    def test_contribution_record_validation_types(self):
        """Wrong types raise TypeError for each field."""
        with pytest.raises(TypeError, match="contribution_date must be a date"):
            ContributionRecord("2026-01-02", Decimal("100"), True)
        with pytest.raises(TypeError, match="amount must be a Decimal"):
            ContributionRecord(date(2026, 1, 2), 100.0, True)
        with pytest.raises(TypeError, match="is_confirmed must be a bool"):
            ContributionRecord(date(2026, 1, 2), Decimal("100"), "yes")

    def test_ytd_tracking_uses_actual_amounts(self, biweekly_periods):
        """YTD contributions reflect actual per-period amounts, not static.

        P0: $100 -- ytd=$100
        P1: $200 -- ytd=$300
        P2: $300 -- ytd=$600
        """
        periods = biweekly_periods[:3]
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("100"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("300"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=Decimal("999"),
            contributions=contributions,
        )
        assert result[0].ytd_contributions == Decimal("100")
        assert result[1].ytd_contributions == Decimal("300")
        assert result[2].ytd_contributions == Decimal("600")

    def test_contribution_limit_remaining_reflects_actuals(self, biweekly_periods):
        """contribution_limit_remaining computed from actual per-period amounts.

        Limit=$1,000.
        P0: $100 -- remaining=$900
        P1: $200 -- remaining=$700
        P2: $300 -- remaining=$400
        """
        periods = biweekly_periods[:3]
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("100"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("300"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            contributions=contributions,
            annual_contribution_limit=Decimal("1000"),
        )
        assert result[0].contribution_limit_remaining == Decimal("900")
        assert result[1].contribution_limit_remaining == Decimal("700")
        assert result[2].contribution_limit_remaining == Decimal("400")

    def test_zero_return_rate_with_contributions(self, biweekly_periods):
        """0% return: balance grows only by contributions.

        Start=$10,000.
        P0: +$100 = $10,100
        P1: +$200 = $10,300
        P2: +$300 = $10,600
        """
        periods = biweekly_periods[:3]
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("100"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("300"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            contributions=contributions,
        )
        for pb in result:
            assert pb.growth == ZERO
        assert result[0].end_balance == Decimal("10100")
        assert result[1].end_balance == Decimal("10300")
        assert result[2].end_balance == Decimal("10600")

    def test_no_employer_params_with_contributions(self, biweekly_periods):
        """employer_params=None with contributions: no employer match applied."""
        periods = biweekly_periods[:3]
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("100"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("300"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            employer_params=None,
            contributions=contributions,
        )
        for pb in result:
            assert pb.employer_contribution == ZERO
        assert result[2].end_balance == Decimal("10600")
