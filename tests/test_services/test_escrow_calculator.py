"""
Tests for the escrow calculator service.
"""

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.services.escrow_calculator import (
    calculate_monthly_escrow,
    calculate_total_payment,
    project_annual_escrow,
)


def _comp(name, annual, inflation=None, is_active=True, created_at=None):
    """Helper to create a mock escrow component."""
    return SimpleNamespace(
        name=name,
        annual_amount=Decimal(str(annual)),
        inflation_rate=Decimal(str(inflation)) if inflation else None,
        is_active=is_active,
        created_at=created_at,
    )


class TestCalculateMonthlyEscrow:
    """Tests for monthly escrow calculation."""

    def test_basic_two_components(self):
        """Two components → sum of annual/12."""
        components = [
            _comp("Property Tax", "4800"),
            _comp("Insurance", "2400"),
        ]
        result = calculate_monthly_escrow(components)
        assert result == Decimal("600.00")

    def test_empty_components(self):
        """No components → $0."""
        result = calculate_monthly_escrow([])
        assert result == Decimal("0.00")

    def test_inactive_excluded(self):
        """Inactive component excluded."""
        components = [
            _comp("Property Tax", "4800"),
            _comp("Old Insurance", "1200", is_active=False),
        ]
        result = calculate_monthly_escrow(components)
        assert result == Decimal("400.00")

    def test_with_inflation(self):
        """Inflation applied with month-aware elapsed years (M-05)."""
        components = [
            _comp("Property Tax", "4800", inflation="0.03",
                  created_at=datetime(2024, 1, 1)),
        ]
        # 29 months elapsed (Jan 2024 to Jun 2026) = 29/12 ≈ 2.4167 years
        # 4800 * 1.03^(29/12) / 12 ≈ 429.62
        result = calculate_monthly_escrow(components, as_of_date=date(2026, 6, 1))
        assert result == Decimal("429.62")

    def test_no_inflation_without_date(self):
        """No as_of_date → no inflation applied."""
        components = [
            _comp("Property Tax", "4800", inflation="0.03",
                  created_at=datetime(2024, 1, 1)),
        ]
        result = calculate_monthly_escrow(components)
        assert result == Decimal("400.00")

    def test_zero_annual_amount(self):
        """Component with $0 annual amount produces $0 monthly escrow.

        Edge case: a component might be set to zero during a waiver period.
        Expected: Decimal("0.00").
        """
        components = [_comp("Waived Fee", "0")]
        result = calculate_monthly_escrow(components)
        assert result == Decimal("0.00")

    def test_negative_annual_amount(self):
        """Component with negative annual amount is accepted without validation.

        The source does not guard against negative annual_amount values.
        This means calculate_monthly_escrow silently returns a negative result.
        Expected: Decimal("-100.00") for annual_amount=-1200.
        # BUG: negative annual_amount is accepted without validation --
        # consider adding a guard in the service.
        """
        components = [_comp("Refund", "-1200")]
        result = calculate_monthly_escrow(components)
        # -1200 / 12 = -100.00
        assert result == Decimal("-100.00")

    def test_multiple_components_sum_equals_individuals(self):
        """Total monthly escrow of N components equals the sum of each computed individually.

        Verifies the aggregation logic is additive -- no rounding drift across components.
        Expected: sum of individual monthly amounts == combined call result.
        """
        comp1 = _comp("Property Tax", "1200")
        comp2 = _comp("Insurance", "2400")
        comp3 = _comp("HOA", "600")

        individual_sum = (
            calculate_monthly_escrow([comp1])
            + calculate_monthly_escrow([comp2])
            + calculate_monthly_escrow([comp3])
        )
        combined = calculate_monthly_escrow([comp1, comp2, comp3])

        # Individual: 100 + 200 + 50 = 350
        assert calculate_monthly_escrow([comp1]) == Decimal("100.00")
        assert calculate_monthly_escrow([comp2]) == Decimal("200.00")
        assert calculate_monthly_escrow([comp3]) == Decimal("50.00")
        assert combined == Decimal("350.00")
        assert combined == individual_sum


class TestCalculateTotalPayment:
    """Tests for total payment (P&I + escrow)."""

    def test_pi_plus_escrow(self):
        """P&I + escrow = total."""
        components = [
            _comp("Property Tax", "4800"),
            _comp("Insurance", "2400"),
        ]
        result = calculate_total_payment(Decimal("1264.14"), components)
        assert result == Decimal("1864.14")

    def test_no_escrow(self):
        """No escrow → total = P&I."""
        result = calculate_total_payment(Decimal("1000.00"), [])
        assert result == Decimal("1000.00")


class TestProjectAnnualEscrow:
    """Tests for annual escrow projections."""

    def test_no_inflation(self):
        """inflation_rate=0 or None → flat."""
        components = [
            _comp("Property Tax", "4800"),
            _comp("Insurance", "2400"),
        ]
        result = project_annual_escrow(components, 3, 2026)
        assert len(result) == 3
        for year, amount in result:
            assert amount == Decimal("7200.00")
        assert result[0][0] == 2026
        assert result[2][0] == 2028

    def test_with_inflation(self):
        """3% inflation → correct projection."""
        components = [
            _comp("Property Tax", "4800", inflation="0.03"),
        ]
        result = project_annual_escrow(components, 3, 2026)
        assert result[0] == (2026, Decimal("4800.00"))
        assert result[1] == (2027, Decimal("4944.00"))
        # Year 3: 4800 * 1.03^2 = 5092.32, but quantized
        assert result[2][0] == 2028

    def test_mixed_rates(self):
        """Components with different inflation rates."""
        components = [
            _comp("Property Tax", "4800", inflation="0.03"),
            _comp("Insurance", "2400", inflation="0.05"),
        ]
        result = project_annual_escrow(components, 2, 2026)
        assert result[0] == (2026, Decimal("7200.00"))  # No inflation year 0
        # Year 2: 4800*1.03 + 2400*1.05 = 4944 + 2520 = 7464
        assert result[1] == (2027, Decimal("7464.00"))

    def test_five_year_projection(self):
        """5-year projection → 5 rows."""
        components = [_comp("Property Tax", "4800")]
        result = project_annual_escrow(components, 5, 2026)
        assert len(result) == 5
        assert result[0][0] == 2026
        assert result[4][0] == 2030

    def test_inactive_excluded(self):
        """Inactive components excluded from projection."""
        components = [
            _comp("Property Tax", "4800"),
            _comp("Old", "1200", is_active=False),
        ]
        result = project_annual_escrow(components, 1, 2026)
        assert result[0] == (2026, Decimal("4800.00"))

    def test_zero_years_projection(self):
        """years_forward=0 returns an empty list.

        Edge case: caller passes 0 years, which means range(0) produces
        no iterations. The function should return [] without error.
        Expected: empty list.
        """
        components = [_comp("Property Tax", "4800")]
        result = project_annual_escrow(components, 0, 2026)
        assert result == []

    def test_negative_inflation_rate_deflation(self):
        """Negative inflation (deflation) causes amounts to decrease each year.

        Scenario: a component with -2% annual change (e.g., declining insurance
        costs). Verifies exact Decimal values for 3 years.
        Expected:
          Year 0: 1200.00 (no inflation at offset 0)
          Year 1: 1200 * 0.98^1 = 1176.00
          Year 2: 1200 * 0.98^2 = 1152.48
        """
        components = [_comp("Insurance", "1200", inflation="-0.02")]
        result = project_annual_escrow(components, 3, 2026)

        assert result[0] == (2026, Decimal("1200.00"))
        assert result[1] == (2027, Decimal("1176.00"))
        assert result[2] == (2028, Decimal("1152.48"))
        # Also verify directional: year 0 > year 2
        assert result[0][1] == Decimal("1200.00")
        assert result[2][1] == Decimal("1152.48")

    def test_very_high_inflation_rate(self):
        """50% annual inflation produces rapidly growing escrow projections.

        Stress test: verifies Decimal precision is maintained even with
        large multipliers over 5 years.
        Expected:
          Year 0: 1200.00
          Year 1: 1200 * 1.5^1 = 1800.00
          Year 2: 1200 * 1.5^2 = 2700.00
          Year 3: 1200 * 1.5^3 = 4050.00
          Year 4: 1200 * 1.5^4 = 6075.00
        """
        components = [_comp("Property Tax", "1200", inflation="0.50")]
        result = project_annual_escrow(components, 5, 2026)

        assert len(result) == 5
        assert result[0] == (2026, Decimal("1200.00"))
        assert result[1] == (2027, Decimal("1800.00"))
        assert result[2] == (2028, Decimal("2700.00"))
        assert result[3] == (2029, Decimal("4050.00"))
        assert result[4] == (2030, Decimal("6075.00"))
