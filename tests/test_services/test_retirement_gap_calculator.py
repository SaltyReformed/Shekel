"""
Shekel Budget App — Unit Tests for Retirement Income Gap Calculator

Tests the gap analysis pipeline including income calculation,
pension integration, required savings, and after-tax views.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.retirement_gap_calculator import (
    RetirementGapAnalysis,
    calculate_gap,
    ZERO,
)


class TestCalculateGap:
    def test_surplus(self):
        """Projected savings exceed required savings."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2500"),
            monthly_pension_income=Decimal("2000"),
            retirement_account_projections=[
                {"projected_balance": Decimal("500000"), "is_traditional": False},
            ],
            safe_withdrawal_rate=Decimal("0.04"),
            planned_retirement_date=date(2050, 1, 1),
        )
        # Net monthly = 2500 * 26 / 12 = 5416.67
        # Gap = 5416.67 - 2000 = 3416.67
        # Required = 3416.67 * 12 / 0.04 = 1,025,001
        # Projected = 500000
        assert result.pre_retirement_net_monthly == Decimal("5416.67")
        assert result.monthly_income_gap == Decimal("3416.67")
        assert result.projected_total_savings == Decimal("500000")
        assert result.savings_surplus_or_shortfall < ZERO  # actually shortfall

    def test_shortfall(self):
        """Projected savings less than required."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2500"),
            monthly_pension_income=ZERO,
            retirement_account_projections=[
                {"projected_balance": Decimal("100000"), "is_traditional": False},
            ],
            safe_withdrawal_rate=Decimal("0.04"),
        )
        assert result.savings_surplus_or_shortfall < ZERO

    def test_no_pension(self):
        """Full gap equals net income when no pension."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2000"),
            monthly_pension_income=ZERO,
        )
        assert result.monthly_income_gap == result.pre_retirement_net_monthly

    def test_pension_covers_all_income(self):
        """Pension covers all income → zero gap."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2000"),
            monthly_pension_income=Decimal("10000"),
        )
        assert result.monthly_income_gap == ZERO
        assert result.required_retirement_savings == ZERO

    def test_after_tax_view_traditional(self):
        """Tax rate applied to traditional account balances."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2000"),
            monthly_pension_income=ZERO,
            retirement_account_projections=[
                {"projected_balance": Decimal("400000"), "is_traditional": True},
                {"projected_balance": Decimal("100000"), "is_traditional": False},
            ],
            estimated_tax_rate=Decimal("0.20"),
        )
        # After-tax: 400000 * 0.80 + 100000 = 420000
        assert result.after_tax_projected_savings == Decimal("420000.00")
        assert result.after_tax_surplus_or_shortfall is not None

    def test_after_tax_all_roth(self):
        """All Roth → no tax impact."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2000"),
            monthly_pension_income=ZERO,
            retirement_account_projections=[
                {"projected_balance": Decimal("500000"), "is_traditional": False},
            ],
            estimated_tax_rate=Decimal("0.20"),
        )
        assert result.after_tax_projected_savings == Decimal("500000.00")

    def test_custom_swr(self):
        """Different SWR changes required savings."""
        result_4 = calculate_gap(
            net_biweekly_pay=Decimal("2000"),
            monthly_pension_income=ZERO,
            safe_withdrawal_rate=Decimal("0.04"),
        )
        result_3 = calculate_gap(
            net_biweekly_pay=Decimal("2000"),
            monthly_pension_income=ZERO,
            safe_withdrawal_rate=Decimal("0.03"),
        )
        assert result_3.required_retirement_savings > result_4.required_retirement_savings

    def test_zero_net_pay(self):
        """Zero income results in zero gap."""
        result = calculate_gap(
            net_biweekly_pay=ZERO,
            monthly_pension_income=ZERO,
        )
        assert result.pre_retirement_net_monthly == ZERO
        assert result.monthly_income_gap == ZERO
        assert result.required_retirement_savings == ZERO

    def test_multiple_accounts_summed(self):
        """Multiple retirement accounts summed correctly."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2000"),
            monthly_pension_income=ZERO,
            retirement_account_projections=[
                {"projected_balance": Decimal("100000"), "is_traditional": True},
                {"projected_balance": Decimal("200000"), "is_traditional": False},
                {"projected_balance": Decimal("50000"), "is_traditional": True},
            ],
        )
        assert result.projected_total_savings == Decimal("350000")

    def test_no_retirement_accounts(self):
        """No accounts → full shortfall."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2000"),
            monthly_pension_income=ZERO,
            retirement_account_projections=[],
        )
        assert result.projected_total_savings == ZERO
        assert result.savings_surplus_or_shortfall < ZERO

    def test_no_tax_rate_skips_after_tax(self):
        """No tax rate → after-tax fields are None."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2000"),
            monthly_pension_income=ZERO,
            estimated_tax_rate=None,
        )
        assert result.after_tax_projected_savings is None
        assert result.after_tax_surplus_or_shortfall is None

    def test_planned_retirement_date_passed_through(self):
        """Retirement date stored in result."""
        dt = date(2050, 6, 15)
        result = calculate_gap(
            net_biweekly_pay=Decimal("2000"),
            planned_retirement_date=dt,
        )
        assert result.planned_retirement_date == dt

    def test_pension_taxed_when_tax_rate_provided(self):
        """Pension income reduced by estimated tax rate when provided."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2500"),
            monthly_pension_income=Decimal("5000"),
            estimated_tax_rate=Decimal("0.20"),
        )
        # Net monthly = 2500 * 26 / 12 = 5416.67
        # After-tax pension = 5000 * 0.80 = 4000
        # Gap = 5416.67 - 4000 = 1416.67
        assert result.after_tax_monthly_pension == Decimal("4000.00")
        assert result.monthly_income_gap == Decimal("1416.67")
        assert result.required_retirement_savings > ZERO

    def test_pension_not_taxed_without_tax_rate(self):
        """Without tax rate, pension used as-is (gross) — backward compatible."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2500"),
            monthly_pension_income=Decimal("5000"),
        )
        # Net monthly = 5416.67
        # Gap = 5416.67 - 5000 = 416.67 (using gross pension)
        assert result.after_tax_monthly_pension is None
        assert result.monthly_income_gap == Decimal("416.67")

    def test_pension_tax_creates_gap_where_none_existed(self):
        """Gross pension > net income, but after-tax pension < net income."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2000"),
            monthly_pension_income=Decimal("5000"),
            estimated_tax_rate=Decimal("0.25"),
        )
        # Net monthly = 2000 * 26 / 12 = 4333.33
        # After-tax pension = 5000 * 0.75 = 3750
        # Gap = 4333.33 - 3750 = 583.33
        assert result.monthly_income_gap == Decimal("583.33")
        assert result.required_retirement_savings > ZERO

    def test_pension_tax_zero_pension(self):
        """Tax on zero pension is still zero — no division issues."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2000"),
            monthly_pension_income=ZERO,
            estimated_tax_rate=Decimal("0.20"),
        )
        assert result.after_tax_monthly_pension == ZERO
        assert result.monthly_income_gap == result.pre_retirement_net_monthly
