"""
Shekel Budget App -- Unit Tests for Retirement Income Gap Calculator

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
    def test_shortfall_when_projected_below_required(self):
        """Projected savings of $500k fall short of ~$1.025M required.

        net_monthly = 2500 * 26 / 12 = 5416.67
        gap = 5416.67 - 2000 (pension) = 3416.67
        required = 3416.67 * 12 / 0.04 = 1,025,001.00
        shortfall = 500000 - 1025001 = -525,001.00
        """
        result = calculate_gap(
            net_biweekly_pay=Decimal("2500"),
            monthly_pension_income=Decimal("2000"),
            retirement_account_projections=[
                {"projected_balance": Decimal("500000"), "is_traditional": False},
            ],
            safe_withdrawal_rate=Decimal("0.04"),
            planned_retirement_date=date(2050, 1, 1),
        )
        assert result.pre_retirement_net_monthly == Decimal("5416.67"), (
            f"Expected net monthly 5416.67, "
            f"got {result.pre_retirement_net_monthly}"
        )
        assert result.monthly_income_gap == Decimal("3416.67"), (
            f"Expected gap 3416.67, "
            f"got {result.monthly_income_gap}"
        )
        assert result.projected_total_savings == Decimal("500000"), (
            f"Expected projected 500000, "
            f"got {result.projected_total_savings}"
        )
        # 3416.67 * 12 / 0.04 = 1025001.00
        assert result.required_retirement_savings == Decimal("1025001.00"), (
            f"Expected required 1025001.00, "
            f"got {result.required_retirement_savings}"
        )
        # 500000 - 1025001 = -525001.00
        assert result.savings_surplus_or_shortfall == Decimal("-525001.00"), (
            f"Expected shortfall -525001.00, "
            f"got {result.savings_surplus_or_shortfall}"
        )

    def test_shortfall(self):
        """Projected savings less than required.

        net_monthly = (2500 * 26 / 12).quantize(0.01) = 5416.67
        gap = 5416.67 - 0 = 5416.67
        required = (5416.67 * 12 / 0.04).quantize(0.01) = 1625001.00
        shortfall = 100000 - 1625001.00 = -1525001.00
        """
        result = calculate_gap(
            net_biweekly_pay=Decimal("2500"),
            monthly_pension_income=ZERO,
            retirement_account_projections=[
                {"projected_balance": Decimal("100000"), "is_traditional": False},
            ],
            safe_withdrawal_rate=Decimal("0.04"),
        )
        assert result.savings_surplus_or_shortfall == Decimal("-1525001.00")

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
        """Tax rate applied to traditional account balances.

        after_tax_projected = 400000*0.80 + 100000 = 420000.00
        net_monthly = 2000*26/12 = 4333.33, pension=0
        required = 4333.33*12/0.04 = 1299999.00
        after_tax_surplus = 420000 - 1299999 = -879999.00
        """
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
        assert result.after_tax_projected_savings == Decimal("420000.00"), (
            f"Expected after-tax projected 420000.00, "
            f"got {result.after_tax_projected_savings}"
        )
        # net_monthly = 2000*26/12 = 4333.33, gap = 4333.33
        # required = 4333.33*12/0.04 = 1299999.00
        # after_tax_surplus = 420000 - 1299999 = -879999.00
        assert result.after_tax_surplus_or_shortfall == Decimal("-879999.00"), (
            f"Expected after-tax shortfall -879999.00, "
            f"got {result.after_tax_surplus_or_shortfall}"
        )

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
        """Different SWR changes required savings; lower SWR → larger nest egg.

        net_monthly = (2000*26/12).quantize(0.01) = 4333.33
        gap = 4333.33 (no pension)
        required_4 = (4333.33*12/0.04).quantize(0.01) = (51999.96/0.04) = 1299999.00
        required_3 = (4333.33*12/0.03).quantize(0.01) = (51999.96/0.03) = 1733332.00
        """
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
        assert result_4.required_retirement_savings == Decimal("1299999.00")
        assert result_3.required_retirement_savings == Decimal("1733332.00")

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
        """No accounts → full shortfall.

        net_monthly = (2000 * 26 / 12).quantize(0.01) = 4333.33
        gap = 4333.33 - 0 = 4333.33
        required = (4333.33 * 12 / 0.04).quantize(0.01) = 1299999.00
        shortfall = 0 - 1299999.00 = -1299999.00
        """
        result = calculate_gap(
            net_biweekly_pay=Decimal("2000"),
            monthly_pension_income=ZERO,
            retirement_account_projections=[],
        )
        assert result.projected_total_savings == ZERO
        assert result.savings_surplus_or_shortfall == Decimal("-1299999.00")

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
        # required = gap * 12 / swr = 1416.67 * 12 / 0.04 = 425001.00
        assert result.required_retirement_savings == Decimal("425001.00"), (
            f"Expected required savings 425001.00, "
            f"got {result.required_retirement_savings}"
        )

    def test_pension_not_taxed_without_tax_rate(self):
        """Without tax rate, pension used as-is (gross) -- backward compatible."""
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
        # required = gap * 12 / swr = 583.33 * 12 / 0.04 = 174999.00
        assert result.required_retirement_savings == Decimal("174999.00"), (
            f"Expected required savings 174999.00, "
            f"got {result.required_retirement_savings}"
        )

    def test_pension_tax_zero_pension(self):
        """Tax on zero pension is still zero -- no division issues."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2000"),
            monthly_pension_income=ZERO,
            estimated_tax_rate=Decimal("0.20"),
        )
        assert result.after_tax_monthly_pension == ZERO
        assert result.monthly_income_gap == result.pre_retirement_net_monthly

    # ── Edge-case and negative-path tests ────────────────────────────

    def test_safe_withdrawal_rate_zero(self):
        """SWR=0 is guarded by the source: required nest egg defaults to ZERO.

        The source code checks `if safe_withdrawal_rate > 0:` before dividing.
        When SWR=0, the else branch sets required_retirement_savings = ZERO,
        so no ZeroDivisionError occurs.

        pre_retirement_net_monthly = (2500*26/12).quantize(0.01) = 5416.67
        monthly_income_gap = max(5416.67 - 1000, 0) = 4416.67
        required_retirement_savings = ZERO (SWR not > 0)
        projected_total_savings = 500000
        savings_surplus_or_shortfall = 500000 - 0 = 500000
        """
        result = calculate_gap(
            net_biweekly_pay=Decimal("2500"),
            monthly_pension_income=Decimal("1000"),
            retirement_account_projections=[
                {"projected_balance": Decimal("500000"), "is_traditional": False},
            ],
            safe_withdrawal_rate=Decimal("0"),
        )
        assert result.pre_retirement_net_monthly == Decimal("5416.67")
        assert result.monthly_income_gap == Decimal("4416.67")
        assert result.required_retirement_savings == ZERO
        assert result.projected_total_savings == Decimal("500000")
        assert result.savings_surplus_or_shortfall == Decimal("500000")

    def test_safe_withdrawal_rate_negative(self):
        """Negative SWR is mathematically nonsensical; source treats it like zero.

        The source guard `if safe_withdrawal_rate > 0:` is False for negative
        values, so required_retirement_savings = ZERO. No validation is
        performed -- a negative SWR silently produces the same result as SWR=0.

        # BUG: Source does not validate SWR > 0 -- negative SWR silently
        # accepted. Should raise ValidationError.
        # TODO: Source should validate safe_withdrawal_rate > 0.

        pre_retirement_net_monthly = (2500*26/12).quantize(0.01) = 5416.67
        monthly_income_gap = max(5416.67 - 1000, 0) = 4416.67
        required_retirement_savings = ZERO (SWR not > 0)
        projected_total_savings = 500000
        savings_surplus_or_shortfall = 500000 - 0 = 500000
        """
        result = calculate_gap(
            net_biweekly_pay=Decimal("2500"),
            monthly_pension_income=Decimal("1000"),
            retirement_account_projections=[
                {"projected_balance": Decimal("500000"), "is_traditional": False},
            ],
            safe_withdrawal_rate=Decimal("-0.04"),
        )
        assert result.pre_retirement_net_monthly == Decimal("5416.67")
        assert result.monthly_income_gap == Decimal("4416.67")
        assert result.required_retirement_savings == ZERO
        assert result.projected_total_savings == Decimal("500000")
        assert result.savings_surplus_or_shortfall == Decimal("500000")
        assert result.safe_withdrawal_rate == Decimal("-0.04")

    def test_tax_rate_one_hundred_percent(self):
        """100% tax rate means all traditional withdrawals taxed away entirely.

        pre_retirement_net_monthly = (2500*26/12).quantize(0.01) = 5416.67
        after_tax_monthly_pension = (2000*(1-1.00)).quantize(0.01) = 0.00
        monthly_income_gap = max(5416.67 - 0.00, 0) = 5416.67
        required = (5416.67*12/0.04).quantize(0.01) = (65000.04/0.04) = 1625001.00
        projected_total = 400000 + 100000 = 500000
        surplus = 500000 - 1625001.00 = -1125001.00
        after_tax_projected = (400000*0 + 100000).quantize(0.01) = 100000.00
        after_tax_surplus = 100000.00 - 1625001.00 = -1525001.00
        """
        result = calculate_gap(
            net_biweekly_pay=Decimal("2500"),
            monthly_pension_income=Decimal("2000"),
            retirement_account_projections=[
                {"projected_balance": Decimal("400000"), "is_traditional": True},
                {"projected_balance": Decimal("100000"), "is_traditional": False},
            ],
            safe_withdrawal_rate=Decimal("0.04"),
            estimated_tax_rate=Decimal("1.00"),
        )
        assert result.pre_retirement_net_monthly == Decimal("5416.67")
        assert result.monthly_pension_income == Decimal("2000")
        assert result.after_tax_monthly_pension == Decimal("0.00")
        assert result.monthly_income_gap == Decimal("5416.67")
        assert result.required_retirement_savings == Decimal("1625001.00")
        assert result.projected_total_savings == Decimal("500000")
        assert result.savings_surplus_or_shortfall == Decimal("-1125001.00")
        assert result.after_tax_projected_savings == Decimal("100000.00")
        assert result.after_tax_surplus_or_shortfall == Decimal("-1525001.00")

    def test_tax_rate_zero(self):
        """0% tax rate means all withdrawals are fully available.

        pre_retirement_net_monthly = (2500*26/12).quantize(0.01) = 5416.67
        after_tax_monthly_pension = (2000*(1-0)).quantize(0.01) = 2000.00
        monthly_income_gap = max(5416.67 - 2000.00, 0) = 3416.67
        required = (3416.67*12/0.04).quantize(0.01) = (41000.04/0.04) = 1025001.00
        projected_total = 500000
        surplus = 500000 - 1025001.00 = -525001.00
        after_tax_projected = (400000*1.00 + 100000).quantize(0.01) = 500000.00
        after_tax_surplus = 500000.00 - 1025001.00 = -525001.00
        """
        result = calculate_gap(
            net_biweekly_pay=Decimal("2500"),
            monthly_pension_income=Decimal("2000"),
            retirement_account_projections=[
                {"projected_balance": Decimal("400000"), "is_traditional": True},
                {"projected_balance": Decimal("100000"), "is_traditional": False},
            ],
            safe_withdrawal_rate=Decimal("0.04"),
            estimated_tax_rate=Decimal("0.00"),
        )
        assert result.pre_retirement_net_monthly == Decimal("5416.67")
        assert result.after_tax_monthly_pension == Decimal("2000.00")
        assert result.monthly_income_gap == Decimal("3416.67")
        assert result.required_retirement_savings == Decimal("1025001.00")
        assert result.projected_total_savings == Decimal("500000")
        assert result.savings_surplus_or_shortfall == Decimal("-525001.00")
        assert result.after_tax_projected_savings == Decimal("500000.00")
        assert result.after_tax_surplus_or_shortfall == Decimal("-525001.00")

    def test_tax_rate_negative(self):
        """Negative tax rate is nonsensical; source does not validate it.

        A negative tax rate inflates after-tax pension and after-tax savings
        (multiplies by > 1.0). This is mathematically valid but financially
        meaningless.

        # BUG: Source does not validate estimated_tax_rate >= 0.
        # TODO: Source should raise ValidationError for negative tax rates.

        pre_retirement_net_monthly = (2500*26/12).quantize(0.01) = 5416.67
        after_tax_monthly_pension = (2000*(1-(-0.10))).quantize(0.01)
                                  = (2000*1.10).quantize(0.01) = 2200.00
        monthly_income_gap = max(5416.67 - 2200.00, 0) = 3216.67
        required = (3216.67*12/0.04).quantize(0.01) = (38600.04/0.04) = 965001.00
        projected_total = 500000
        surplus = 500000 - 965001.00 = -465001.00
        after_tax_projected = (400000*1.10 + 100000).quantize(0.01) = 540000.00
        after_tax_surplus = 540000.00 - 965001.00 = -425001.00
        """
        result = calculate_gap(
            net_biweekly_pay=Decimal("2500"),
            monthly_pension_income=Decimal("2000"),
            retirement_account_projections=[
                {"projected_balance": Decimal("400000"), "is_traditional": True},
                {"projected_balance": Decimal("100000"), "is_traditional": False},
            ],
            safe_withdrawal_rate=Decimal("0.04"),
            estimated_tax_rate=Decimal("-0.10"),
        )
        assert result.pre_retirement_net_monthly == Decimal("5416.67")
        assert result.after_tax_monthly_pension == Decimal("2200.00")
        assert result.monthly_income_gap == Decimal("3216.67")
        assert result.required_retirement_savings == Decimal("965001.00")
        assert result.projected_total_savings == Decimal("500000")
        assert result.savings_surplus_or_shortfall == Decimal("-465001.00")
        assert result.after_tax_projected_savings == Decimal("540000.00")
        assert result.after_tax_surplus_or_shortfall == Decimal("-425001.00")

    def test_negative_net_biweekly_pay(self):
        """Negative pay (deductions exceed gross) clamps income gap to zero.

        This can happen when heavy pre-tax contributions exceed gross on
        certain pay periods. The max() clamp prevents a negative gap.

        pre_retirement_net_monthly = (-500*26/12).quantize(0.01) = -1083.33
        monthly_income_gap = max(-1083.33 - 0, 0) = 0
        required = (0*12/0.04).quantize(0.01) = 0.00
        projected_total = 0
        surplus = 0 - 0.00 = 0.00
        """
        result = calculate_gap(
            net_biweekly_pay=Decimal("-500.00"),
            monthly_pension_income=ZERO,
        )
        assert result.pre_retirement_net_monthly == Decimal("-1083.33")
        assert result.monthly_income_gap == ZERO
        assert result.required_retirement_savings == Decimal("0.00")
        assert result.projected_total_savings == ZERO
        assert result.savings_surplus_or_shortfall == Decimal("0.00")

    def test_zero_monthly_pension_income(self):
        """No pension means the full net income must be replaced by savings.

        pre_retirement_net_monthly = (3000*26/12).quantize(0.01) = 6500.00
        monthly_income_gap = max(6500.00 - 0, 0) = 6500.00
        required = (6500.00*12/0.04).quantize(0.01) = (78000.00/0.04) = 1950000.00
        projected_total = 800000
        surplus = 800000 - 1950000.00 = -1150000.00
        """
        result = calculate_gap(
            net_biweekly_pay=Decimal("3000"),
            monthly_pension_income=ZERO,
            retirement_account_projections=[
                {"projected_balance": Decimal("800000"), "is_traditional": False},
            ],
            safe_withdrawal_rate=Decimal("0.04"),
        )
        assert result.monthly_pension_income == ZERO
        assert result.monthly_income_gap == Decimal("6500.00")
        assert result.required_retirement_savings == Decimal("1950000.00")
        assert result.projected_total_savings == Decimal("800000")
        assert result.savings_surplus_or_shortfall == Decimal("-1150000.00")

    def test_all_income_sources_zero(self):
        """No pay, no pension, no accounts: gap is zero (nothing to replace).

        When net_biweekly_pay=0 and pension=0, the gap is 0 because there
        is no pre-retirement income to replace. Required savings = 0.
        """
        result = calculate_gap(
            net_biweekly_pay=ZERO,
            monthly_pension_income=ZERO,
            retirement_account_projections=[],
        )
        assert result.pre_retirement_net_monthly == ZERO
        assert result.monthly_income_gap == ZERO
        assert result.required_retirement_savings == ZERO
        assert result.projected_total_savings == ZERO
        assert result.savings_surplus_or_shortfall == ZERO

    def test_result_field_completeness(self):
        """Every field of RetirementGapAnalysis is present with expected type.

        Guards against silent field additions or removals from breaking
        downstream consumers. Uses normal inputs to produce non-None values
        for all optional fields.
        """
        result = calculate_gap(
            net_biweekly_pay=Decimal("2500"),
            monthly_pension_income=Decimal("1000"),
            retirement_account_projections=[
                {"projected_balance": Decimal("300000"), "is_traditional": True},
                {"projected_balance": Decimal("200000"), "is_traditional": False},
            ],
            safe_withdrawal_rate=Decimal("0.04"),
            planned_retirement_date=date(2050, 1, 1),
            estimated_tax_rate=Decimal("0.20"),
        )
        assert isinstance(result, RetirementGapAnalysis)
        assert isinstance(result.pre_retirement_net_monthly, Decimal)
        assert isinstance(result.monthly_pension_income, Decimal)
        assert isinstance(result.after_tax_monthly_pension, Decimal)
        assert isinstance(result.monthly_income_gap, Decimal)
        assert isinstance(result.required_retirement_savings, Decimal)
        assert isinstance(result.projected_total_savings, Decimal)
        assert isinstance(result.savings_surplus_or_shortfall, Decimal)
        assert isinstance(result.safe_withdrawal_rate, Decimal)
        assert isinstance(result.planned_retirement_date, date)
        assert isinstance(result.after_tax_projected_savings, Decimal)
        assert isinstance(result.after_tax_surplus_or_shortfall, Decimal)
        # Verify all 11 dataclass fields are present.
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(RetirementGapAnalysis)}
        assert field_names == {
            "pre_retirement_net_monthly", "monthly_pension_income",
            "after_tax_monthly_pension", "monthly_income_gap",
            "required_retirement_savings", "projected_total_savings",
            "savings_surplus_or_shortfall", "safe_withdrawal_rate",
            "planned_retirement_date", "after_tax_projected_savings",
            "after_tax_surplus_or_shortfall",
        }

    def test_large_values_no_overflow(self):
        """Decimal precision maintained at high magnitudes.

        pre_retirement_net_monthly = (20000*26/12).quantize(0.01) = 43333.33
        monthly_income_gap = max(43333.33 - 5000, 0) = 38333.33
        required = (38333.33*12/0.04).quantize(0.01) = (459999.96/0.04) = 11499999.00
        projected_total = 10000000
        surplus = 10000000 - 11499999.00 = -1499999.00
        """
        result = calculate_gap(
            net_biweekly_pay=Decimal("20000"),
            monthly_pension_income=Decimal("5000"),
            retirement_account_projections=[
                {"projected_balance": Decimal("10000000"), "is_traditional": False},
            ],
            safe_withdrawal_rate=Decimal("0.04"),
        )
        assert result.pre_retirement_net_monthly == Decimal("43333.33")
        assert result.monthly_income_gap == Decimal("38333.33")
        assert result.required_retirement_savings == Decimal("11499999.00")
        assert result.projected_total_savings == Decimal("10000000")
        assert result.savings_surplus_or_shortfall == Decimal("-1499999.00")
