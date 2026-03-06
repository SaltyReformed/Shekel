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
class FakeTransfer:
    to_account_id: int
    amount: Decimal
    pay_period_id: int
    is_deleted: bool = False


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
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=[],
            all_transfers=[], all_periods=[current_period], current_period=current_period,
        )
        assert result.periodic_contribution == Decimal("0")
        assert result.employer_params is None
        assert result.ytd_contributions == Decimal("0")
        assert result.annual_contribution_limit == Decimal("23500")

    def test_flat_deduction(self):
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
            all_transfers=[], all_periods=[current_period], current_period=current_period,
        )
        assert result.periodic_contribution == Decimal("500.00")

    def test_percentage_deduction(self):
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
            all_transfers=[], all_periods=[current_period], current_period=current_period,
        )
        gross = (Decimal("100000") / 26).quantize(Decimal("0.01"))
        expected = (gross * Decimal("0.07")).quantize(Decimal("0.01"))
        assert result.periodic_contribution == expected

    def test_transfer_contributions_averaged(self):
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=None,
            employer_contribution_type="none",
        )
        transfers = [
            FakeTransfer(to_account_id=10, amount=Decimal("200"), pay_period_id=1),
            FakeTransfer(to_account_id=10, amount=Decimal("200"), pay_period_id=2),
            FakeTransfer(to_account_id=10, amount=Decimal("300"), pay_period_id=3),
            FakeTransfer(to_account_id=99, amount=Decimal("1000"), pay_period_id=1),
        ]
        periods = [FakePeriod(id=i, start_date=date(2026, 1, 2), period_index=i) for i in range(1, 4)]
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=[],
            all_transfers=transfers, all_periods=periods, current_period=periods[0],
        )
        assert result.periodic_contribution == Decimal("233.33")

    def test_employer_flat_percentage(self):
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="flat_percentage", employer_flat_percentage=Decimal("0.05"),
        )
        deductions = [FakeDeduction(amount=Decimal("500.00"), calc_method_name="flat",
                                     annual_salary=Decimal("100000"), pay_periods_per_year=26)]
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=deductions,
            all_transfers=[], all_periods=[current_period], current_period=current_period,
        )
        assert result.employer_params is not None
        assert result.employer_params["type"] == "flat_percentage"
        assert result.employer_params["flat_percentage"] == Decimal("0.05")
        gross = (Decimal("100000") / 26).quantize(Decimal("0.01"))
        assert result.employer_params["gross_biweekly"] == gross

    def test_employer_match(self):
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
            all_transfers=[], all_periods=[current_period], current_period=current_period,
        )
        assert result.employer_params is not None
        assert result.employer_params["type"] == "match"
        assert result.employer_params["match_percentage"] == Decimal("1.0")
        assert result.employer_params["match_cap_percentage"] == Decimal("0.06")

    def test_ytd_contributions_from_transfers(self):
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
        transfers = [
            FakeTransfer(to_account_id=10, amount=Decimal("500"), pay_period_id=1),
            FakeTransfer(to_account_id=10, amount=Decimal("500"), pay_period_id=2),
            FakeTransfer(to_account_id=10, amount=Decimal("500"), pay_period_id=3),
            FakeTransfer(to_account_id=10, amount=Decimal("500"), pay_period_id=4),
            FakeTransfer(to_account_id=10, amount=Decimal("500"), pay_period_id=5),
            FakeTransfer(to_account_id=99, amount=Decimal("999"), pay_period_id=2),
        ]
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=[],
            all_transfers=transfers, all_periods=periods, current_period=periods[3],
        )
        assert result.ytd_contributions == Decimal("1500")

    def test_combined_deductions_and_transfers(self):
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        deductions = [FakeDeduction(amount=Decimal("500.00"), calc_method_name="flat",
                                     annual_salary=Decimal("100000"), pay_periods_per_year=26)]
        transfers = [
            FakeTransfer(to_account_id=10, amount=Decimal("200"), pay_period_id=1),
            FakeTransfer(to_account_id=10, amount=Decimal("200"), pay_period_id=2),
        ]
        periods = [FakePeriod(id=1, start_date=date(2026, 1, 2), period_index=0),
                    FakePeriod(id=2, start_date=date(2026, 1, 16), period_index=1)]
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=deductions,
            all_transfers=transfers, all_periods=periods, current_period=periods[0],
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
            all_transfers=[],
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
            all_transfers=[],
            all_periods=[current_period],
            current_period=current_period,
            salary_gross_biweekly=Decimal("3846.15"),
        )

        expected_gross = (Decimal("120000") / 26).quantize(Decimal("0.01"))
        assert result.employer_params["gross_biweekly"] == expected_gross

    def test_no_employer_when_type_none(self):
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            account_id=10, investment_params=params, deductions=[],
            all_transfers=[], all_periods=[current_period], current_period=current_period,
        )
        assert result.employer_params is None
