"""
Shekel Budget App — Unit Tests for Federal Withholding Calculation

Tests the IRS Publication 15-T Percentage Method implementation in
tax_calculator.calculate_federal_withholding().

All bracket data used here is labeled as test fixtures and does not
represent actual IRS publications.
"""

import pytest
from decimal import Decimal

from app.services.tax_calculator import (
    calculate_federal_withholding,
    calculate_federal_tax,
    _apply_marginal_brackets,
)
from app.services.exceptions import (
    InvalidDependentCountError,
    InvalidGrossPayError,
    InvalidPayPeriodsError,
    InvalidFilingStatusError,
)


# ── Test Fixtures ─────────────────────────────────────────────────


class FakeBracket:
    """Minimal stand-in for a TaxBracket ORM object."""

    def __init__(self, min_income, max_income, rate, sort_order):
        self.min_income = min_income
        self.max_income = max_income
        self.rate = rate
        self.sort_order = sort_order


class FakeBracketSet:
    """Minimal stand-in for a TaxBracketSet ORM object.

    Bracket data below is for testing only and does not represent
    actual IRS bracket values.
    """

    def __init__(
        self,
        standard_deduction=Decimal("15000"),
        child_credit_amount=Decimal("2000"),
        other_dependent_credit_amount=Decimal("500"),
        brackets=None,
    ):
        self.standard_deduction = standard_deduction
        self.child_credit_amount = child_credit_amount
        self.other_dependent_credit_amount = other_dependent_credit_amount
        self.brackets = brackets or []


def _single_brackets():
    """Test fixture: simplified progressive bracket structure."""
    return [
        FakeBracket(Decimal("0"),      Decimal("10000"),  Decimal("0.10"), 0),
        FakeBracket(Decimal("10000"),  Decimal("40000"),  Decimal("0.12"), 1),
        FakeBracket(Decimal("40000"),  Decimal("85000"),  Decimal("0.22"), 2),
        FakeBracket(Decimal("85000"),  Decimal("160000"), Decimal("0.24"), 3),
        FakeBracket(Decimal("160000"), Decimal("210000"), Decimal("0.32"), 4),
        FakeBracket(Decimal("210000"), Decimal("540000"), Decimal("0.35"), 5),
        FakeBracket(Decimal("540000"), None,              Decimal("0.37"), 6),
    ]


@pytest.fixture
def single_bracket_set():
    """Single filer bracket set with $15,000 standard deduction."""
    return FakeBracketSet(
        standard_deduction=Decimal("15000"),
        child_credit_amount=Decimal("2000"),
        other_dependent_credit_amount=Decimal("500"),
        brackets=_single_brackets(),
    )


# ── Test 1: Zero Tax (credits exceed tax) ─────────────────────────


class TestZeroTaxScenario:
    """When credits exceed computed tax, withholding should be zero."""

    def test_credits_exceed_tax(self, single_bracket_set):
        """Test fixture: low income + 3 children = credits wipe out tax."""
        # Gross ~$30k/year => taxable ~$15k => tax ~$1,600
        # 3 children * $2,000 = $6,000 credit => tax zeroed out
        result = calculate_federal_withholding(
            gross_pay=Decimal("1153.85"),  # ~$30k / 26
            pay_periods=26,
            bracket_set=single_bracket_set,
            qualifying_children=3,
        )
        assert result == Decimal("0.00")

    def test_income_below_standard_deduction(self, single_bracket_set):
        """Income below standard deduction yields zero tax."""
        # ~$14,300/year < $15,000 standard deduction
        result = calculate_federal_withholding(
            gross_pay=Decimal("550.00"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        assert result == Decimal("0.00")

    def test_zero_gross_pay(self, single_bracket_set):
        """Zero gross pay yields zero withholding."""
        result = calculate_federal_withholding(
            gross_pay=Decimal("0"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        assert result == Decimal("0.00")


# ── Test 2: Positive Tax Scenario ──────────────────────────────────


class TestPositiveTaxScenario:
    """Standard withholding scenarios that produce positive tax."""

    def test_basic_biweekly_withholding(self, single_bracket_set):
        """Test fixture: $60k salary, biweekly, no dependents."""
        # Annual: $60,000 - $15,000 std ded = $45,000 taxable
        # Bracket calc: $10,000 * 0.10 + $30,000 * 0.12 + $5,000 * 0.22
        #             = $1,000 + $3,600 + $1,100 = $5,700
        # Per period: $5,700 / 26 = $219.23 (rounded)
        result = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),  # $60k / 26
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        assert result == Decimal("219.23")

    def test_weekly_pay_frequency(self, single_bracket_set):
        """Same annual salary but weekly pay frequency."""
        # $60k / 52 weeks = $1,153.85/week
        # Same annual tax $5,700 / 52 = $109.62 (rounded)
        result = calculate_federal_withholding(
            gross_pay=Decimal("1153.85"),  # $60k / 52
            pay_periods=52,
            bracket_set=single_bracket_set,
        )
        # Slight rounding difference due to gross_pay * 52 vs exact $60k
        assert result > Decimal("0")
        # Verify it's roughly half the biweekly amount
        assert Decimal("108") < result < Decimal("111")

    def test_monthly_pay_frequency(self, single_bracket_set):
        """Monthly pay frequency."""
        result = calculate_federal_withholding(
            gross_pay=Decimal("5000.00"),  # $60k / 12
            pay_periods=12,
            bracket_set=single_bracket_set,
        )
        assert result == Decimal("475.00")

    def test_extra_withholding_added(self, single_bracket_set):
        """W-4 Step 4(c) extra withholding adds to each period."""
        base = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        with_extra = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),
            pay_periods=26,
            bracket_set=single_bracket_set,
            extra_withholding=Decimal("50.00"),
        )
        assert with_extra == base + Decimal("50.00")

    def test_additional_income_increases_tax(self, single_bracket_set):
        """W-4 Step 4(a) additional income raises withholding."""
        base = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        with_additional = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),
            pay_periods=26,
            bracket_set=single_bracket_set,
            additional_income=Decimal("10000"),
        )
        assert with_additional > base

    def test_additional_deductions_reduce_tax(self, single_bracket_set):
        """W-4 Step 4(b) additional deductions lower withholding."""
        base = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        with_deductions = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),
            pay_periods=26,
            bracket_set=single_bracket_set,
            additional_deductions=Decimal("5000"),
        )
        assert with_deductions < base

    def test_pre_tax_deductions_reduce_tax(self, single_bracket_set):
        """Annualized pre-tax deductions (retirement, etc.) lower withholding."""
        base = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        with_pretax = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),
            pay_periods=26,
            bracket_set=single_bracket_set,
            pre_tax_deductions=Decimal("6000"),
        )
        assert with_pretax < base


# ── Test 3: High Income (Multiple Brackets) ───────────────────────


class TestHighIncomeScenario:
    """High earners spanning many or all bracket tiers."""

    def test_income_spans_all_brackets(self, single_bracket_set):
        """Test fixture: $600k salary spans into the top bracket."""
        # Taxable: $600,000 - $15,000 = $585,000
        # Brackets:
        #   $10,000 * 0.10 =  $1,000
        #   $30,000 * 0.12 =  $3,600
        #   $45,000 * 0.22 =  $9,900
        #   $75,000 * 0.24 = $18,000
        #   $50,000 * 0.32 = $16,000
        #  $330,000 * 0.35 = $115,500
        #   $45,000 * 0.37 = $16,650
        # Total = $180,650
        # Per period: $180,650 / 26 = $6,948.08
        result = calculate_federal_withholding(
            gross_pay=Decimal("23076.92"),  # $600k / 26
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        # Verify it's in the right ballpark (rounding from gross_pay * 26)
        assert Decimal("6940") < result < Decimal("6960")

    def test_very_high_income_top_bracket_only(self):
        """Test fixture: income so high most is taxed at top rate."""
        # Simplified: two brackets for clarity
        brackets = [
            FakeBracket(Decimal("0"), Decimal("100000"), Decimal("0.10"), 0),
            FakeBracket(Decimal("100000"), None, Decimal("0.37"), 1),
        ]
        bracket_set = FakeBracketSet(
            standard_deduction=Decimal("15000"),
            brackets=brackets,
        )
        # $1M/year => taxable $985,000
        # $100k * 0.10 = $10,000 + $885,000 * 0.37 = $327,450
        # Total = $337,450 / 26 = $12,978.85
        result = calculate_federal_withholding(
            gross_pay=Decimal("38461.54"),  # ~$1M / 26
            pay_periods=26,
            bracket_set=bracket_set,
        )
        assert result > Decimal("12900")


# ── Test 4: Bracket Boundary Conditions ────────────────────────────


class TestBracketBoundary:
    """Income exactly at bracket thresholds."""

    def test_income_exactly_at_first_bracket_top(self, single_bracket_set):
        """Taxable income exactly at the first bracket boundary ($10,000)."""
        # Need annual gross = $10,000 + $15,000 std ded = $25,000
        # Tax = $10,000 * 0.10 = $1,000
        # Per period: $1,000 / 26 = $38.46
        result = calculate_federal_withholding(
            gross_pay=Decimal("961.54"),  # ~$25k / 26
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        # Verify it's close to $38.46
        assert Decimal("38") < result < Decimal("39")

    def test_income_one_dollar_into_next_bracket(self, single_bracket_set):
        """Taxable income $1 above the first bracket starts 12% rate."""
        # $25,001 annual => taxable $10,001
        # Tax = $10,000 * 0.10 + $1 * 0.12 = $1,000.12
        # Per period: $1,000.12 / 26 = $38.47
        result = calculate_federal_withholding(
            gross_pay=Decimal("961.58"),  # ~$25,001 / 26
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        assert result >= Decimal("38.46")

    def test_income_exactly_at_standard_deduction(self, single_bracket_set):
        """Income exactly equal to standard deduction = zero tax."""
        # $15,000 / 26 = $576.92
        result = calculate_federal_withholding(
            gross_pay=Decimal("576.92"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        # 576.92 * 26 = 14,999.92 < 15,000 std ded => zero
        assert result == Decimal("0.00")

    def test_single_bracket_no_remainder(self):
        """Flat tax: single bracket covering all income."""
        brackets = [
            FakeBracket(Decimal("0"), None, Decimal("0.15"), 0),
        ]
        bracket_set = FakeBracketSet(
            standard_deduction=Decimal("0"),
            brackets=brackets,
        )
        # $52,000/year, 26 periods => $2,000/period
        # Tax: $52,000 * 0.15 = $7,800 / 26 = $300.00
        result = calculate_federal_withholding(
            gross_pay=Decimal("2000"),
            pay_periods=26,
            bracket_set=bracket_set,
        )
        assert result == Decimal("300.00")


# ── Test 5: Invalid Input Rejection ────────────────────────────────


class TestInputValidation:
    """Defensive validation raises domain-specific exceptions."""

    def test_negative_gross_pay(self, single_bracket_set):
        with pytest.raises(InvalidGrossPayError):
            calculate_federal_withholding(
                gross_pay=Decimal("-100"),
                pay_periods=26,
                bracket_set=single_bracket_set,
            )

    def test_zero_pay_periods(self, single_bracket_set):
        with pytest.raises(InvalidPayPeriodsError):
            calculate_federal_withholding(
                gross_pay=Decimal("2000"),
                pay_periods=0,
                bracket_set=single_bracket_set,
            )

    def test_negative_pay_periods(self, single_bracket_set):
        with pytest.raises(InvalidPayPeriodsError):
            calculate_federal_withholding(
                gross_pay=Decimal("2000"),
                pay_periods=-1,
                bracket_set=single_bracket_set,
            )

    def test_none_bracket_set(self):
        with pytest.raises(InvalidFilingStatusError):
            calculate_federal_withholding(
                gross_pay=Decimal("2000"),
                pay_periods=26,
                bracket_set=None,
            )

    def test_negative_qualifying_children(self, single_bracket_set):
        with pytest.raises(InvalidDependentCountError):
            calculate_federal_withholding(
                gross_pay=Decimal("2000"),
                pay_periods=26,
                bracket_set=single_bracket_set,
                qualifying_children=-1,
            )

    def test_negative_other_dependents(self, single_bracket_set):
        with pytest.raises(InvalidDependentCountError):
            calculate_federal_withholding(
                gross_pay=Decimal("2000"),
                pay_periods=26,
                bracket_set=single_bracket_set,
                other_dependents=-2,
            )


# ── Test: Dependent Credits ────────────────────────────────────────


class TestDependentCredits:
    """W-4 Step 3 credit calculations."""

    def test_child_credits_reduce_tax(self, single_bracket_set):
        """Each qualifying child reduces annual tax by child_credit_amount."""
        no_kids = calculate_federal_withholding(
            gross_pay=Decimal("3846.15"),  # ~$100k / 26
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        two_kids = calculate_federal_withholding(
            gross_pay=Decimal("3846.15"),
            pay_periods=26,
            bracket_set=single_bracket_set,
            qualifying_children=2,
        )
        # 2 kids * $2,000 = $4,000 annual credit / 26 = ~$153.85 less per period
        diff = no_kids - two_kids
        assert Decimal("153") < diff < Decimal("155")

    def test_other_dependent_credits(self, single_bracket_set):
        """Other dependents use the other_dependent_credit_amount."""
        no_deps = calculate_federal_withholding(
            gross_pay=Decimal("3846.15"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        two_other = calculate_federal_withholding(
            gross_pay=Decimal("3846.15"),
            pay_periods=26,
            bracket_set=single_bracket_set,
            other_dependents=2,
        )
        # 2 * $500 = $1,000 / 26 = ~$38.46 less
        diff = no_deps - two_other
        assert Decimal("38") < diff < Decimal("39")


# ── Test: Legacy calculate_federal_tax wrapper ─────────────────────


class TestLegacyWrapper:
    """Backward compatibility with the original calculate_federal_tax."""

    def test_returns_annual_tax(self, single_bracket_set):
        """Legacy function returns the full annual amount."""
        result = calculate_federal_tax(Decimal("60000"), single_bracket_set)
        # $60k - $15k = $45k taxable
        # $10k*0.10 + $30k*0.12 + $5k*0.22 = $5,700
        assert result == Decimal("5700.00")

    def test_none_bracket_set_returns_zero(self):
        result = calculate_federal_tax(Decimal("60000"), None)
        assert result == Decimal("0")

    def test_below_deduction_returns_zero(self, single_bracket_set):
        result = calculate_federal_tax(Decimal("10000"), single_bracket_set)
        assert result == Decimal("0")


# ── Test: _apply_marginal_brackets (internal helper) ───────────────


class TestApplyMarginalBrackets:
    """Direct tests for the bracket iteration logic."""

    def test_empty_brackets(self):
        result = _apply_marginal_brackets(Decimal("50000"), [])
        assert result == Decimal("0")

    def test_zero_taxable_income(self):
        result = _apply_marginal_brackets(Decimal("0"), _single_brackets())
        assert result == Decimal("0")

    def test_negative_taxable_income(self):
        result = _apply_marginal_brackets(Decimal("-1000"), _single_brackets())
        assert result == Decimal("0")
