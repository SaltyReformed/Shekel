"""
Tests for the escrow calculator service.
"""

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.services.escrow_calculator import (
    build_escrow_display,
    calculate_monthly_escrow,
    calculate_total_payment,
)


def _comp(name, annual, inflation=None, is_active=True, created_at=None, id=1):
    """Helper to create a mock escrow component."""
    return SimpleNamespace(
        id=id,
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


class TestBuildEscrowDisplay:
    """Tests for the display DTO builder (MED-04 / E-16, C31-3)."""

    def test_c31_3_escrow_per_period_server_decimal(self):
        """C31-3 -- per-component monthly is server-computed in Decimal.

        Arithmetic: 4800 / 12 = 400.00 exact; 2400 / 12 = 200.00 exact.
        Both quantised HALF_UP to two places.  No float cast.
        """
        components = [
            _comp("Property Tax", "4800", id=1),
            _comp("Insurance", "2400", inflation="0.03", id=2),
        ]
        rows = build_escrow_display(components)
        assert len(rows) == 2
        assert rows[0].id == 1
        assert rows[0].name == "Property Tax"
        assert rows[0].annual_amount == Decimal("4800.00")
        assert rows[0].monthly_amount == Decimal("400.00")
        assert rows[0].inflation_rate is None
        assert rows[0].inflation_rate_pct is None
        assert rows[1].id == 2
        assert rows[1].annual_amount == Decimal("2400.00")
        assert rows[1].monthly_amount == Decimal("200.00")
        # 0.03 * 100 = 3.00 (Decimal -- no float drift)
        assert rows[1].inflation_rate == Decimal("0.03")
        assert rows[1].inflation_rate_pct == Decimal("3.00")
        # Type assertions: every monetary/percentage field is Decimal.
        for row in rows:
            assert isinstance(row.annual_amount, Decimal)
            assert isinstance(row.monthly_amount, Decimal)

    def test_inactive_excluded(self):
        """Inactive components are filtered the same way as the
        :func:`calculate_monthly_escrow` aggregate, so the per-row
        display and the badge total can never disagree."""
        components = [
            _comp("Property Tax", "4800", id=1),
            _comp("Old Insurance", "1200", is_active=False, id=2),
        ]
        rows = build_escrow_display(components)
        assert len(rows) == 1
        assert rows[0].id == 1

    def test_uneven_division_rounds_half_up(self):
        """1000 / 12 = 83.3333... -> HALF_UP rounds to 83.33.

        Hand calc: 1000 / 12 = 83.333... -> quantize 0.01 HALF_UP -> 83.33
        (the third decimal is a 3, so the cents digit is not bumped).
        """
        components = [_comp("Edge", "1000", id=1)]
        rows = build_escrow_display(components)
        assert rows[0].monthly_amount == Decimal("83.33")

    def test_half_up_rounding_boundary(self):
        """500 / 12 = 41.6666... -> HALF_UP rounds to 41.67.

        Hand calc: 500 / 12 = 41.6666... -> quantize 0.01 HALF_UP -> 41.67
        (the third decimal is a 6, so the cents digit is bumped from 6 to 7).
        For a single component the allocation has nothing to distribute
        differently: the row IS the total, so it equals the badge.
        """
        components = [_comp("Edge", "500", id=1)]
        rows = build_escrow_display(components)
        assert rows[0].monthly_amount == Decimal("41.67")
        assert rows[0].monthly_amount == calculate_monthly_escrow(components)


class TestEscrowDisplayCentAllocation:
    """The deep-hunt #17 fix: rows cent-allocate to the badge total.

    ``build_escrow_display`` used to round EACH row HALF_UP while
    ``calculate_monthly_escrow`` (the badge and the loan-payment money
    figure) sums full-precision monthlies and rounds ONCE -- so two
    $100/yr components rendered rows summing to 16.66 beside a 16.67
    badge on the same escrow tab.  Largest-remainder allocation makes
    the rows sum exactly to the badge while keeping every row within
    one cent of its exact ``annual / 12``; the aggregate rule itself
    (E-26 sum-then-round) is untouched.
    """

    def test_two_equal_components_sum_to_badge(self):
        """Two $100/yr components: rows sum to the 16.67 badge.

        Hand calc: exact each = 100 / 12 = 8.3333...; full-precision
        sum = 16.6666... -> badge round_money -> 16.67.  Floors are
        8.33 + 8.33 = 16.66, so ONE leftover cent goes to the largest
        remainder; remainders tie (both .00333), so input order breaks
        the tie -> rows [8.34, 8.33].  The old per-row HALF_UP gave
        [8.33, 8.33] = 16.66 != 16.67 (the registered defect).
        """
        components = [
            _comp("Property Tax", "100", id=1),
            _comp("Insurance", "100", id=2),
        ]
        rows = build_escrow_display(components)
        badge = calculate_monthly_escrow(components)
        assert badge == Decimal("16.67")
        assert [r.monthly_amount for r in rows] == [
            Decimal("8.34"), Decimal("8.33"),
        ]
        assert sum(r.monthly_amount for r in rows) == badge

    def test_three_components_distribute_two_cents(self):
        """Three $50/yr components: two leftover cents distributed.

        Hand calc: exact each = 50 / 12 = 4.1666...; full-precision sum
        = 12.50 exactly -> badge 12.50.  Floors are 4.16 x 3 = 12.48,
        leaving TWO cents for the two largest remainders; all three tie
        (.00666), so input order gives [4.17, 4.17, 4.16].
        """
        components = [
            _comp("Tax", "50", id=1),
            _comp("Insurance", "50", id=2),
            _comp("HOA", "50", id=3),
        ]
        rows = build_escrow_display(components)
        badge = calculate_monthly_escrow(components)
        assert badge == Decimal("12.50")
        assert [r.monthly_amount for r in rows] == [
            Decimal("4.17"), Decimal("4.17"), Decimal("4.16"),
        ]
        assert sum(r.monthly_amount for r in rows) == badge

    def test_largest_remainder_wins_the_cent(self):
        """The extra cent goes to the row nearest its next cent, not row 1.

        Hand calc: 119/12 = 9.91666... (remainder .00666);
        100/12 = 8.33333... (remainder .00333).  Full-precision sum =
        18.25 exactly -> badge 18.25; floors 9.91 + 8.33 = 18.24 leave
        one cent, which must go to the SECOND-listed-but-larger
        remainder when ordered [smaller, larger] -- proving allocation
        ranks by remainder, not input position.
        """
        components = [
            _comp("Insurance", "100", id=1),   # remainder .00333
            _comp("Property Tax", "119", id=2),  # remainder .00666
        ]
        rows = build_escrow_display(components)
        badge = calculate_monthly_escrow(components)
        assert badge == Decimal("18.25")
        assert [r.monthly_amount for r in rows] == [
            Decimal("8.33"), Decimal("9.92"),
        ]
        assert sum(r.monthly_amount for r in rows) == badge

    def test_every_row_within_one_cent_of_exact(self):
        """Allocated rows never drift more than a cent from annual/12.

        Sweep a mixed set; for each row |allocated - exact| < 0.01 and
        the set sums to the badge.  Pins the allocation's two
        guarantees together.
        """
        components = [
            _comp("Tax", "1000", id=1),
            _comp("Insurance", "500", id=2),
            _comp("HOA", "100", id=3),
            _comp("Flood", "85", id=4),
        ]
        rows = build_escrow_display(components)
        badge = calculate_monthly_escrow(components)
        assert sum(r.monthly_amount for r in rows) == badge
        for row in rows:
            exact = row.annual_amount / Decimal("12")
            assert abs(row.monthly_amount - exact) < Decimal("0.01")
