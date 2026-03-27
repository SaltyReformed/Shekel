"""
Tests for the investment projection helper.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.investment_projection import (
    calculate_investment_inputs,
    InvestmentInputs,
)


@dataclass
class FakeDeduction:
    amount: Decimal
    calc_method_name: str
    annual_salary: Decimal
    pay_periods_per_year: int


@dataclass
class FakeContribution:
    """Shadow income transaction representing a contribution (transfer into account)."""
    estimated_amount: Decimal
    pay_period_id: int


@dataclass
class FakePeriod:
    id: int
    start_date: date
    period_index: int


@dataclass
class FakeInvestmentParams:
    assumed_annual_return: Decimal
    annual_contribution_limit: Decimal
    employer_contribution_type: str
    employer_flat_percentage: Decimal = Decimal("0")
    employer_match_percentage: Decimal = Decimal("0")
    employer_match_cap_percentage: Decimal = Decimal("0")


class TestCalculateInvestmentInputs:

    def test_no_deductions_no_transfers(self):
        """No deductions or transfers → zero contributions and zero YTD."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=[],
            all_contributions=[], all_periods=[current_period], current_period=current_period,
        )
        assert result.periodic_contribution == Decimal("0")
        assert result.employer_params is None
        assert result.ytd_contributions == Decimal("0")
        assert result.annual_contribution_limit == Decimal("23500")

    def test_flat_deduction(self):
        """Flat deduction amount adds directly to periodic contribution."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        deductions = [FakeDeduction(amount=Decimal("500.00"), calc_method_name="flat",
                                     annual_salary=Decimal("100000"), pay_periods_per_year=26)]
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=deductions,
            all_contributions=[], all_periods=[current_period], current_period=current_period,
        )
        assert result.periodic_contribution == Decimal("500.00")

    def test_percentage_deduction(self):
        """Percentage deduction computed as gross_biweekly * rate."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        deductions = [FakeDeduction(amount=Decimal("0.07"), calc_method_name="percentage",
                                     annual_salary=Decimal("100000"), pay_periods_per_year=26)]
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=deductions,
            all_contributions=[], all_periods=[current_period], current_period=current_period,
        )
        gross = (Decimal("100000") / 26).quantize(Decimal("0.01"))
        expected = (gross * Decimal("0.07")).quantize(Decimal("0.01"))
        assert result.periodic_contribution == expected

    def test_transfer_contributions_averaged(self):
        """Transfer contributions averaged across distinct periods with transfers."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=None,
            employer_contribution_type="none",
        )
        contributions = [
            FakeContribution(estimated_amount=Decimal("200"), pay_period_id=1),
            FakeContribution(estimated_amount=Decimal("200"), pay_period_id=2),
            FakeContribution(estimated_amount=Decimal("300"), pay_period_id=3),
        ]
        periods = [FakePeriod(id=i, start_date=date(2026, 1, 2), period_index=i) for i in range(1, 4)]
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=[],
            all_contributions=contributions, all_periods=periods, current_period=periods[0],
        )
        assert result.periodic_contribution == Decimal("233.33")

    def test_employer_flat_percentage(self):
        """Employer flat_percentage populates employer_params with correct values."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="flat_percentage", employer_flat_percentage=Decimal("0.05"),
        )
        deductions = [FakeDeduction(amount=Decimal("500.00"), calc_method_name="flat",
                                     annual_salary=Decimal("100000"), pay_periods_per_year=26)]
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=deductions,
            all_contributions=[], all_periods=[current_period], current_period=current_period,
        )
        assert result.employer_params is not None
        assert result.employer_params["type"] == "flat_percentage"
        assert result.employer_params["flat_percentage"] == Decimal("0.05")
        gross = (Decimal("100000") / 26).quantize(Decimal("0.01"))
        assert result.employer_params["gross_biweekly"] == gross

    def test_employer_match(self):
        """Employer match type populates match_percentage and cap fields."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="match", employer_match_percentage=Decimal("1.0"),
            employer_match_cap_percentage=Decimal("0.06"),
        )
        deductions = [FakeDeduction(amount=Decimal("500.00"), calc_method_name="flat",
                                     annual_salary=Decimal("100000"), pay_periods_per_year=26)]
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=deductions,
            all_contributions=[], all_periods=[current_period], current_period=current_period,
        )
        assert result.employer_params is not None
        assert result.employer_params["type"] == "match"
        assert result.employer_params["match_percentage"] == Decimal("1.0")
        assert result.employer_params["match_cap_percentage"] == Decimal("0.06")

    def test_ytd_contributions_from_transfers(self):
        """YTD contributions sum only current-year contributions up to current period."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        periods = [
            FakePeriod(id=1, start_date=date(2025, 12, 19), period_index=0),
            FakePeriod(id=2, start_date=date(2026, 1, 2), period_index=1),
            FakePeriod(id=3, start_date=date(2026, 1, 16), period_index=2),
            FakePeriod(id=4, start_date=date(2026, 1, 30), period_index=3),
            FakePeriod(id=5, start_date=date(2026, 2, 13), period_index=4),
        ]
        contributions = [
            FakeContribution(estimated_amount=Decimal("500"), pay_period_id=1),
            FakeContribution(estimated_amount=Decimal("500"), pay_period_id=2),
            FakeContribution(estimated_amount=Decimal("500"), pay_period_id=3),
            FakeContribution(estimated_amount=Decimal("500"), pay_period_id=4),
            FakeContribution(estimated_amount=Decimal("500"), pay_period_id=5),
        ]
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=[],
            all_contributions=contributions, all_periods=periods, current_period=periods[3],
        )
        assert result.ytd_contributions == Decimal("1500")

    def test_combined_deductions_and_transfers(self):
        """Deductions and contributions both add to periodic_contribution."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        deductions = [FakeDeduction(amount=Decimal("500.00"), calc_method_name="flat",
                                     annual_salary=Decimal("100000"), pay_periods_per_year=26)]
        contributions = [
            FakeContribution(estimated_amount=Decimal("200"), pay_period_id=1),
            FakeContribution(estimated_amount=Decimal("200"), pay_period_id=2),
        ]
        periods = [FakePeriod(id=1, start_date=date(2026, 1, 2), period_index=0),
                    FakePeriod(id=2, start_date=date(2026, 1, 16), period_index=1)]
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=deductions,
            all_contributions=contributions, all_periods=periods, current_period=periods[0],
        )
        assert result.periodic_contribution == Decimal("700.00")

    def test_employer_flat_uses_salary_gross_when_no_deductions(self):
        """Employer flat_percentage works even without deductions targeting the account."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="flat_percentage",
            employer_flat_percentage=Decimal("0.05"),
        )
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)

        result = calculate_investment_inputs(
            account_id=10,
            investment_params=params,
            deductions=[],
            all_contributions=[],
            all_periods=[current_period],
            current_period=current_period,
            salary_gross_biweekly=Decimal("3846.15"),
        )

        assert result.employer_params is not None
        assert result.employer_params["gross_biweekly"] == Decimal("3846.15")
        assert result.periodic_contribution == Decimal("0")

    def test_deduction_gross_overrides_salary_gross(self):
        """When deductions exist, their derived gross takes precedence."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="flat_percentage",
            employer_flat_percentage=Decimal("0.05"),
        )
        deductions = [
            FakeDeduction(
                amount=Decimal("500.00"),
                calc_method_name="flat",
                annual_salary=Decimal("120000"),
                pay_periods_per_year=26,
            ),
        ]
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)

        result = calculate_investment_inputs(
            account_id=10,
            investment_params=params,
            deductions=deductions,
            all_contributions=[],
            all_periods=[current_period],
            current_period=current_period,
            salary_gross_biweekly=Decimal("3846.15"),
        )

        expected_gross = (Decimal("120000") / 26).quantize(Decimal("0.01"))
        assert result.employer_params["gross_biweekly"] == expected_gross

    def test_no_employer_when_type_none(self):
        """Employer type 'none' produces employer_params=None."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=[],
            all_contributions=[], all_periods=[current_period], current_period=current_period,
        )
        assert result.employer_params is None

    def test_empty_periods_none_current_period(self):
        """Empty period list and None current_period does not crash.

        When no periods exist yet (fresh user), the function should still
        return a valid InvestmentInputs with zero contributions and ytd.
        Expected: periodic_contribution=0, ytd_contributions=0.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=[],
            all_contributions=[], all_periods=[], current_period=None,
        )
        assert result.periodic_contribution == Decimal("0")
        assert result.ytd_contributions == Decimal("0")
        assert result.employer_params is None
        assert result.gross_biweekly == Decimal("0")

    def test_zero_contribution_rate(self):
        """Percentage deduction at 0% produces zero contribution.

        Scenario: employee sets 401k contribution to 0% temporarily.
        Expected: periodic_contribution=0, no employer match triggered.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="match",
            employer_match_percentage=Decimal("1.0"),
            employer_match_cap_percentage=Decimal("0.06"),
        )
        deductions = [FakeDeduction(
            amount=Decimal("0"),
            calc_method_name="percentage",
            annual_salary=Decimal("100000"),
            pay_periods_per_year=26,
        )]
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=deductions,
            all_contributions=[], all_periods=[current_period], current_period=current_period,
        )
        # gross * 0% = 0
        assert result.periodic_contribution == Decimal("0")
        # Employer params are still populated (the match params exist even if contribution is 0)
        assert result.employer_params is not None
        expected_gross = (Decimal("100000") / 26).quantize(Decimal("0.01"))
        assert result.gross_biweekly == expected_gross

    def test_negative_deduction_amount(self):
        """Negative flat deduction amount is accepted without validation.

        The source does not guard against negative amounts. A negative
        deduction effectively reduces the total periodic contribution.
        # BUG: negative deduction amount is silently accepted -- consider
        # adding a guard in the service.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        deductions = [FakeDeduction(
            amount=Decimal("-500.00"),
            calc_method_name="flat",
            annual_salary=Decimal("100000"),
            pay_periods_per_year=26,
        )]
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=deductions,
            all_contributions=[], all_periods=[current_period], current_period=current_period,
        )
        assert result.periodic_contribution == Decimal("-500.00")

    def test_pre_filtered_contributions_only(self):
        """Only non-deleted contributions for this account are passed in.

        The caller pre-filters deleted contributions and contributions for
        other accounts before calling calculate_investment_inputs.  This test
        verifies that a single valid contribution produces the correct
        periodic and YTD values.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        contributions = [
            FakeContribution(estimated_amount=Decimal("200"), pay_period_id=1),
        ]
        periods = [
            FakePeriod(id=1, start_date=date(2026, 1, 2), period_index=0),
            FakePeriod(id=2, start_date=date(2026, 1, 16), period_index=1),
        ]
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=[],
            all_contributions=contributions, all_periods=periods, current_period=periods[0],
        )
        # 1 contribution across 1 period -- periodic = $200
        assert result.periodic_contribution == Decimal("200")
        # YTD only includes current_period=periods[0], which has the $200 contribution
        assert result.ytd_contributions == Decimal("200")

    def test_none_current_period_with_contributions(self):
        """None current_period skips YTD calculation but still averages contributions.

        When current_period is None (e.g., no period is current), the
        function should still compute periodic_contribution from contributions
        but set ytd_contributions to 0.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        contributions = [
            FakeContribution(estimated_amount=Decimal("200"), pay_period_id=1),
            FakeContribution(estimated_amount=Decimal("400"), pay_period_id=2),
        ]
        periods = [
            FakePeriod(id=1, start_date=date(2026, 1, 2), period_index=0),
            FakePeriod(id=2, start_date=date(2026, 1, 16), period_index=1),
        ]
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=[],
            all_contributions=contributions, all_periods=periods, current_period=None,
        )
        # (200 + 400) / 2 periods = 300
        assert result.periodic_contribution == Decimal("300")
        assert result.ytd_contributions == Decimal("0")
