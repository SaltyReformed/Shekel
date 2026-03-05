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
        """Inflation applied when as_of_date provided."""
        components = [
            _comp("Property Tax", "4800", inflation="0.03",
                  created_at=datetime(2024, 1, 1)),
        ]
        # 2 years of 3% inflation: 4800 * 1.03^2 / 12
        result = calculate_monthly_escrow(components, as_of_date=date(2026, 6, 1))
        # 4800 * 1.03^2 / 12 = 424.36
        assert result == Decimal("424.36")

    def test_no_inflation_without_date(self):
        """No as_of_date → no inflation applied."""
        components = [
            _comp("Property Tax", "4800", inflation="0.03",
                  created_at=datetime(2024, 1, 1)),
        ]
        result = calculate_monthly_escrow(components)
        assert result == Decimal("400.00")


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
