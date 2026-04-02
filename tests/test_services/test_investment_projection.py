"""
Tests for the investment projection helper.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import CalcMethodEnum
from app.services.growth_engine import ContributionRecord
from app.services.investment_projection import (
    build_contribution_timeline,
    calculate_investment_inputs,
    InvestmentInputs,
)


def _flat_id():
    return ref_cache.calc_method_id(CalcMethodEnum.FLAT)


def _pct_id():
    return ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)


@dataclass
class FakeDeduction:
    amount: Decimal
    calc_method_id: int
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
        deductions = [FakeDeduction(amount=Decimal("500.00"), calc_method_id=_flat_id(),
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
        deductions = [FakeDeduction(amount=Decimal("0.07"), calc_method_id=_pct_id(),
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
        deductions = [FakeDeduction(amount=Decimal("500.00"), calc_method_id=_flat_id(),
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
        deductions = [FakeDeduction(amount=Decimal("500.00"), calc_method_id=_flat_id(),
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
        deductions = [FakeDeduction(amount=Decimal("500.00"), calc_method_id=_flat_id(),
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
                calc_method_id=_flat_id(),
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
            calc_method_id=_pct_id(),
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
            calc_method_id=_flat_id(),
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


# ── Fake Objects for build_contribution_timeline ──────────────


@dataclass
class FakeStatus:
    """Mimics ref.Status model for testing."""
    is_settled: bool
    excludes_from_balance: bool = False


@dataclass
class FakeContribTransaction:
    """Shadow income transaction with status for timeline tests."""
    effective_amount: Decimal
    pay_period_id: int
    status: FakeStatus


# ── Tests: build_contribution_timeline ────────────────────────


class TestBuildContributionTimeline:
    """Tests for build_contribution_timeline().

    Verifies that the function correctly combines deduction-based and
    transfer-based contributions into a unified ContributionRecord list,
    with correct amounts, is_confirmed semantics, and sorting.
    """

    def test_deduction_only(self):
        """Deductions with no transfers: one record per period from deduction amount.

        Flat $500 deduction across 3 periods.
        """
        deductions = [FakeDeduction(
            amount=Decimal("500.00"), calc_method_id=_flat_id(),
            annual_salary=Decimal("100000"), pay_periods_per_year=26,
        )]
        periods = [
            FakePeriod(id=1, start_date=date(2020, 1, 2), period_index=0),
            FakePeriod(id=2, start_date=date(2020, 1, 16), period_index=1),
            FakePeriod(id=3, start_date=date(2020, 1, 30), period_index=2),
        ]
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[], periods=periods,
        )
        assert len(result) == 3
        for r in result:
            assert r.amount == Decimal("500.00")
            assert isinstance(r, ContributionRecord)

    def test_transfer_only(self):
        """Shadow income transactions with no deductions: one record per transaction."""
        settled = FakeStatus(is_settled=True)
        txns = [
            FakeContribTransaction(Decimal("200"), 1, settled),
            FakeContribTransaction(Decimal("300"), 2, settled),
        ]
        periods = [
            FakePeriod(id=1, start_date=date(2020, 1, 2), period_index=0),
            FakePeriod(id=2, start_date=date(2020, 1, 16), period_index=1),
        ]
        result = build_contribution_timeline(
            deductions=[], contribution_transactions=txns, periods=periods,
        )
        assert len(result) == 2
        assert result[0].amount == Decimal("200")
        assert result[1].amount == Decimal("300")

    def test_both_paths_summed(self):
        """Deduction and transfer on the same period produce separate records.

        The growth engine's lookup dict aggregates same-date records.
        Flat $500 deduction + $200 transfer on period 1.
        """
        deductions = [FakeDeduction(
            amount=Decimal("500.00"), calc_method_id=_flat_id(),
            annual_salary=Decimal("100000"), pay_periods_per_year=26,
        )]
        settled = FakeStatus(is_settled=True)
        txns = [FakeContribTransaction(Decimal("200"), 1, settled)]
        periods = [
            FakePeriod(id=1, start_date=date(2020, 1, 2), period_index=0),
        ]
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=txns,
            periods=periods,
        )
        # One record from deduction, one from transfer, same date.
        assert len(result) == 2
        total = sum(r.amount for r in result)
        assert total == Decimal("700.00")

    def test_deduction_flat_amount(self):
        """Flat-dollar deduction: amount matches deduction.amount exactly."""
        deductions = [FakeDeduction(
            amount=Decimal("269.23"), calc_method_id=_flat_id(),
            annual_salary=Decimal("100000"), pay_periods_per_year=26,
        )]
        periods = [FakePeriod(id=1, start_date=date(2020, 1, 2), period_index=0)]
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[], periods=periods,
        )
        assert result[0].amount == Decimal("269.23")

    def test_deduction_percentage(self):
        """Percentage deduction: amount = gross_biweekly * percentage.

        7% of ($100,000 / 26) = 7% of $3846.15 = $269.23.
        """
        deductions = [FakeDeduction(
            amount=Decimal("0.07"), calc_method_id=_pct_id(),
            annual_salary=Decimal("100000"), pay_periods_per_year=26,
        )]
        periods = [FakePeriod(id=1, start_date=date(2020, 1, 2), period_index=0)]
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[], periods=periods,
        )
        gross = (Decimal("100000") / 26).quantize(Decimal("0.01"))
        expected = (gross * Decimal("0.07")).quantize(Decimal("0.01"))
        assert result[0].amount == expected

    def test_is_confirmed_deduction_past(self):
        """Deduction for a past period: is_confirmed=True."""
        deductions = [FakeDeduction(
            amount=Decimal("500"), calc_method_id=_flat_id(),
            annual_salary=Decimal("100000"), pay_periods_per_year=26,
        )]
        # Far in the past -- guaranteed to be before today.
        periods = [FakePeriod(id=1, start_date=date(2020, 1, 2), period_index=0)]
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[], periods=periods,
        )
        assert result[0].is_confirmed is True

    def test_is_confirmed_deduction_future(self):
        """Deduction for a future period: is_confirmed=False."""
        deductions = [FakeDeduction(
            amount=Decimal("500"), calc_method_id=_flat_id(),
            annual_salary=Decimal("100000"), pay_periods_per_year=26,
        )]
        # Far in the future -- guaranteed to be after today.
        periods = [FakePeriod(id=1, start_date=date(2099, 1, 2), period_index=0)]
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[], periods=periods,
        )
        assert result[0].is_confirmed is False

    def test_is_confirmed_transfer_settled(self):
        """Settled shadow transaction: is_confirmed=True."""
        settled = FakeStatus(is_settled=True)
        txns = [FakeContribTransaction(Decimal("200"), 1, settled)]
        periods = [FakePeriod(id=1, start_date=date(2020, 1, 2), period_index=0)]
        result = build_contribution_timeline(
            deductions=[], contribution_transactions=txns, periods=periods,
        )
        assert result[0].is_confirmed is True

    def test_is_confirmed_transfer_projected(self):
        """Projected shadow transaction: is_confirmed=False."""
        projected = FakeStatus(is_settled=False)
        txns = [FakeContribTransaction(Decimal("200"), 1, projected)]
        periods = [FakePeriod(id=1, start_date=date(2020, 1, 2), period_index=0)]
        result = build_contribution_timeline(
            deductions=[], contribution_transactions=txns, periods=periods,
        )
        assert result[0].is_confirmed is False

    def test_is_confirmed_mixed_same_date(self):
        """Confirmed deduction + projected transfer on same date.

        Both produce records for the same date.  The growth engine's
        lookup dict applies the conservative rule (all must be confirmed).
        Here we verify both records are produced -- one True, one False.
        """
        deductions = [FakeDeduction(
            amount=Decimal("500"), calc_method_id=_flat_id(),
            annual_salary=Decimal("100000"), pay_periods_per_year=26,
        )]
        projected = FakeStatus(is_settled=False)
        txns = [FakeContribTransaction(Decimal("200"), 1, projected)]
        # Past date so deduction is confirmed.
        periods = [FakePeriod(id=1, start_date=date(2020, 1, 2), period_index=0)]
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=txns,
            periods=periods,
        )
        assert len(result) == 2
        confirmed_flags = {r.is_confirmed for r in result}
        assert True in confirmed_flags   # Deduction (past).
        assert False in confirmed_flags  # Transfer (projected).

    def test_empty_both(self):
        """No deductions and no transactions: empty list returned."""
        periods = [FakePeriod(id=1, start_date=date(2020, 1, 2), period_index=0)]
        result = build_contribution_timeline(
            deductions=[], contribution_transactions=[], periods=periods,
        )
        assert result == []

    def test_sorted_output(self):
        """Output is sorted by contribution_date regardless of input order."""
        settled = FakeStatus(is_settled=True)
        txns = [
            FakeContribTransaction(Decimal("300"), 2, settled),
            FakeContribTransaction(Decimal("100"), 1, settled),
        ]
        periods = [
            FakePeriod(id=1, start_date=date(2020, 1, 2), period_index=0),
            FakePeriod(id=2, start_date=date(2020, 1, 16), period_index=1),
        ]
        result = build_contribution_timeline(
            deductions=[], contribution_transactions=txns, periods=periods,
        )
        dates = [r.contribution_date for r in result]
        assert dates == sorted(dates)

    def test_uses_effective_amount(self):
        """The function uses effective_amount from the transaction object."""
        settled = FakeStatus(is_settled=True)
        # effective_amount is a pre-computed value (property on real model).
        txns = [FakeContribTransaction(Decimal("999.99"), 1, settled)]
        periods = [FakePeriod(id=1, start_date=date(2020, 1, 2), period_index=0)]
        result = build_contribution_timeline(
            deductions=[], contribution_transactions=txns, periods=periods,
        )
        assert result[0].amount == Decimal("999.99")

    def test_multiple_deductions_summed(self):
        """Two deductions targeting the same account: amounts summed per period.

        $500 flat + 5% of $3846.15 = $500 + $192.31 = $692.31.
        """
        deductions = [
            FakeDeduction(
                amount=Decimal("500.00"), calc_method_id=_flat_id(),
                annual_salary=Decimal("100000"), pay_periods_per_year=26,
            ),
            FakeDeduction(
                amount=Decimal("0.05"), calc_method_id=_pct_id(),
                annual_salary=Decimal("100000"), pay_periods_per_year=26,
            ),
        ]
        periods = [FakePeriod(id=1, start_date=date(2020, 1, 2), period_index=0)]
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[], periods=periods,
        )
        gross = (Decimal("100000") / 26).quantize(Decimal("0.01"))
        expected = Decimal("500.00") + (gross * Decimal("0.05")).quantize(Decimal("0.01"))
        assert result[0].amount == expected

    def test_excluded_transaction_skipped(self):
        """Cancelled/credit transactions (excludes_from_balance) are skipped."""
        cancelled = FakeStatus(is_settled=False, excludes_from_balance=True)
        settled = FakeStatus(is_settled=True)
        txns = [
            FakeContribTransaction(Decimal("200"), 1, cancelled),
            FakeContribTransaction(Decimal("300"), 2, settled),
        ]
        periods = [
            FakePeriod(id=1, start_date=date(2020, 1, 2), period_index=0),
            FakePeriod(id=2, start_date=date(2020, 1, 16), period_index=1),
        ]
        result = build_contribution_timeline(
            deductions=[], contribution_transactions=txns, periods=periods,
        )
        assert len(result) == 1
        assert result[0].amount == Decimal("300")

    def test_transaction_outside_period_range_skipped(self):
        """Transaction with pay_period_id not in periods list is skipped."""
        settled = FakeStatus(is_settled=True)
        txns = [FakeContribTransaction(Decimal("200"), 99, settled)]
        periods = [FakePeriod(id=1, start_date=date(2020, 1, 2), period_index=0)]
        result = build_contribution_timeline(
            deductions=[], contribution_transactions=txns, periods=periods,
        )
        assert result == []
