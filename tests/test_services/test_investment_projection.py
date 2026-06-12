"""
Tests for the investment projection helper.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import CalcMethodEnum, EmployerContributionTypeEnum
from app.services.growth_engine import ContributionRecord
from app.services.investment_projection import (
    build_contribution_timeline,
    calculate_investment_inputs,
    current_period_transfer_contribution,
    InvestmentInputs,
)


def _flat_id():
    return ref_cache.calc_method_id(CalcMethodEnum.FLAT)


def _pct_id():
    return ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)


def _emp_type_id(member):
    """Resolve an EmployerContributionTypeEnum member to its ref-table id (#38)."""
    return ref_cache.employer_contribution_type_id(member)


@dataclass
class FakeDeduction:
    amount: Decimal
    calc_method_id: int
    annual_salary: Decimal
    pay_periods_per_year: int
    # Calendar-year ceiling (PaycheckDeduction.annual_cap); None = uncapped.
    annual_cap: Decimal | None = None


@dataclass
class FakeStatus:
    """Minimal ``ref.Status`` stub for contribution filtering and timeline confirmation."""
    excludes_from_balance: bool = False
    is_settled: bool = False


@dataclass
class FakeContribution:
    """Shadow income transaction representing a contribution (transfer into account).

    Mirrors the real ``Transaction`` amount accessors: ``effective_amount``
    is the realized ``actual_amount`` when populated, else
    ``estimated_amount`` -- the same rule the model property applies for an
    active row, which is all this fake represents (the contribution feeds
    filter cancelled/credit/deleted rows out via
    ``status_contributes_to_balance`` before reading the amount).  A settled
    shadow whose actual differs from its estimate is the deep-quality-hunt
    #11 case the inputs builder must read off ``effective_amount``.
    """
    estimated_amount: Decimal
    pay_period_id: int
    status: FakeStatus = field(default_factory=FakeStatus)
    actual_amount: Decimal | None = None

    @property
    def effective_amount(self) -> Decimal:
        """Realized actual when populated, else the estimate (model parity)."""
        if self.actual_amount is not None:
            return self.actual_amount
        return self.estimated_amount


@dataclass
class FakePeriod:
    id: int
    start_date: date
    period_index: int


@dataclass
class FakeInvestmentParams:
    assumed_annual_return: Decimal
    annual_contribution_limit: Decimal
    employer_contribution_type_id: int
    employer_flat_percentage: Decimal = Decimal("0")
    employer_match_percentage: Decimal = Decimal("0")
    employer_match_cap_percentage: Decimal = Decimal("0")


class TestCalculateInvestmentInputs:

    def test_no_deductions_no_transfers(self):
        """No deductions or transfers → zero contributions and zero YTD."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            investment_params=params, deductions=[],
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
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        deductions = [FakeDeduction(amount=Decimal("500.00"), calc_method_id=_flat_id(),
                                     annual_salary=Decimal("100000"), pay_periods_per_year=26)]
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
            all_contributions=[], all_periods=[current_period], current_period=current_period,
        )
        assert result.periodic_contribution == Decimal("500.00")

    def test_capped_deduction_periodic_is_even_spread_annual_cap(self):
        """A capped deduction's periodic average is the cap spread over the year.

        The periodic contribution feeds the synthetic long-horizon chart's
        fallback; a $600/period deduction ($15,600/yr) under a $1,000 cap
        contributes the even-spread average $1,000 / 26 = $38.46 per period
        (deep-hunt #2), not the uncapped $600.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        deductions = [FakeDeduction(
            amount=Decimal("600.00"), calc_method_id=_flat_id(),
            annual_salary=Decimal("100000"), pay_periods_per_year=26,
            annual_cap=Decimal("1000.00"),
        )]
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
            all_contributions=[], all_periods=[current_period], current_period=current_period,
        )
        # min(600 * 26, 1000) / 26 = 1000 / 26 = 38.4615... -> 38.46.
        assert result.periodic_contribution == Decimal("38.46")

    def test_percentage_deduction(self):
        """Percentage deduction computed as gross_biweekly * rate."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        deductions = [FakeDeduction(amount=Decimal("0.07"), calc_method_id=_pct_id(),
                                     annual_salary=Decimal("100000"), pay_periods_per_year=26)]
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
            all_contributions=[], all_periods=[current_period], current_period=current_period,
        )
        # 7% of ($100,000 / 26) = 7% of $3846.15 = $269.2305 -> $269.23.
        # Hand-computed literal (not a re-quantize of the code's own
        # expression) so the assertion is an independent oracle.
        assert result.periodic_contribution == Decimal("269.23")

    def test_percentage_deduction_half_cent_rounds_half_up(self):
        """Per-period contribution rounds ROUND_HALF_UP at an exact half-cent.

        Pins the money-rounding MODE (deep-quality-hunt #18/#19/#63 /
        financial-audit HIGH-04 / E-26): the per-period contribution is
        rounded through ``app.utils.money.round_money`` (ROUND_HALF_UP),
        not a bare ``.quantize()`` (Python's default ROUND_HALF_EVEN).

        ``$26,013 / 26 = $1,000.50`` exactly, so 5% of that gross is
        ``$50.0250`` -- a value sitting EXACTLY on a half-cent boundary,
        the only place the two modes diverge.  ROUND_HALF_UP gives
        ``$50.03``; banker's rounding would give ``$50.02`` (round to the
        even cent).  This assertion therefore fails if the site regresses
        to a bare quantize -- the tautological re-quantize the other
        contribution tests use could not catch that.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        deductions = [FakeDeduction(amount=Decimal("0.05"), calc_method_id=_pct_id(),
                                     annual_salary=Decimal("26013"), pay_periods_per_year=26)]
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
            all_contributions=[], all_periods=[current_period], current_period=current_period,
        )
        # gross = round_money(26013 / 26) = round_money(1000.50) = 1000.50;
        # 5% -> round_money(1000.50 * 0.05) = round_money(50.0250) = 50.03
        # (HALF_UP).  Banker's rounding would yield 50.02.
        assert result.periodic_contribution == Decimal("50.03")

    def test_transfer_contributions_averaged(self):
        """Transfer contributions averaged across distinct periods with transfers."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=None,
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        contributions = [
            FakeContribution(estimated_amount=Decimal("200"), pay_period_id=1),
            FakeContribution(estimated_amount=Decimal("200"), pay_period_id=2),
            FakeContribution(estimated_amount=Decimal("300"), pay_period_id=3),
        ]
        periods = [FakePeriod(id=i, start_date=date(2026, 1, 2), period_index=i) for i in range(1, 4)]
        result = calculate_investment_inputs(
            investment_params=params, deductions=[],
            all_contributions=contributions, all_periods=periods, current_period=periods[0],
        )
        assert result.periodic_contribution == Decimal("233.33")

    def test_employer_flat_percentage(self):
        """Employer flat_percentage populates employer_params with correct values."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(
                EmployerContributionTypeEnum.FLAT_PERCENTAGE,
            ),
            employer_flat_percentage=Decimal("0.05"),
        )
        deductions = [FakeDeduction(amount=Decimal("500.00"), calc_method_id=_flat_id(),
                                     annual_salary=Decimal("100000"), pay_periods_per_year=26)]
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
            all_contributions=[], all_periods=[current_period], current_period=current_period,
        )
        assert result.employer_params is not None
        assert result.employer_params["type_id"] == _emp_type_id(
            EmployerContributionTypeEnum.FLAT_PERCENTAGE,
        )
        assert result.employer_params["flat_percentage"] == Decimal("0.05")
        # $100,000 / 26 = $3846.153... -> $3846.15 (hand-computed literal).
        assert result.employer_params["gross_biweekly"] == Decimal("3846.15")

    def test_employer_match(self):
        """Employer match type populates match_percentage and cap fields."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(
                EmployerContributionTypeEnum.MATCH,
            ),
            employer_match_percentage=Decimal("1.0"),
            employer_match_cap_percentage=Decimal("0.06"),
        )
        deductions = [FakeDeduction(amount=Decimal("500.00"), calc_method_id=_flat_id(),
                                     annual_salary=Decimal("100000"), pay_periods_per_year=26)]
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
            all_contributions=[], all_periods=[current_period], current_period=current_period,
        )
        assert result.employer_params is not None
        assert result.employer_params["type_id"] == _emp_type_id(
            EmployerContributionTypeEnum.MATCH,
        )
        assert result.employer_params["match_percentage"] == Decimal("1.0")
        assert result.employer_params["match_cap_percentage"] == Decimal("0.06")

    def test_ytd_contributions_from_transfers(self):
        """YTD contributions sum only current-year contributions up to current period."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
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
            investment_params=params, deductions=[],
            all_contributions=contributions, all_periods=periods, current_period=periods[3],
        )
        assert result.ytd_contributions == Decimal("1500")

    def test_ytd_contributions_seed_excludes_current_period(self):
        """deep-hunt #10: the engine seed YTD is STRICTLY BEFORE the current period.

        Same setup as ``test_ytd_contributions_from_transfers``: five $500
        contributions, current = periods[3] (id=4, start 2026-01-30).
        Period 1 is in 2025 (different calendar year); periods 2-4 are in
        2026 up to and including the current period.

        * ``ytd_contributions`` (the displayed limit-card value, ``<=``)
          sums periods 2, 3, 4 = $1,500 (unchanged).
        * ``ytd_contributions_seed`` (the engine seed, ``<``) sums periods
          2 and 3 only = $1,000 -- the current period's $500 is excluded
          because the growth engine's own walk applies and counts it.
          Seeding $1,500 instead would charge the current period against
          the annual limit twice.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        periods = [
            FakePeriod(id=1, start_date=date(2025, 12, 19), period_index=0),
            FakePeriod(id=2, start_date=date(2026, 1, 2), period_index=1),
            FakePeriod(id=3, start_date=date(2026, 1, 16), period_index=2),
            FakePeriod(id=4, start_date=date(2026, 1, 30), period_index=3),
            FakePeriod(id=5, start_date=date(2026, 2, 13), period_index=4),
        ]
        contributions = [
            FakeContribution(estimated_amount=Decimal("500"), pay_period_id=pid)
            for pid in (1, 2, 3, 4, 5)
        ]
        result = calculate_investment_inputs(
            investment_params=params, deductions=[],
            all_contributions=contributions, all_periods=periods, current_period=periods[3],
        )
        assert result.ytd_contributions == Decimal("1500")          # <= current (display)
        assert result.ytd_contributions_seed == Decimal("1000")     # < current (engine seed)

    def test_ytd_contributions_seed_none_current_period(self):
        """deep-hunt #10: a None current period yields a ZERO engine seed."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        contributions = [FakeContribution(estimated_amount=Decimal("500"), pay_period_id=1)]
        periods = [FakePeriod(id=1, start_date=date(2026, 1, 2), period_index=0)]
        result = calculate_investment_inputs(
            investment_params=params, deductions=[],
            all_contributions=contributions, all_periods=periods, current_period=None,
        )
        assert result.ytd_contributions_seed == Decimal("0")

    def test_combined_deductions_and_transfers(self):
        """Deductions and contributions both add to periodic_contribution."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
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
            investment_params=params, deductions=deductions,
            all_contributions=contributions, all_periods=periods, current_period=periods[0],
        )
        assert result.periodic_contribution == Decimal("700.00")

    def test_employer_flat_uses_salary_gross_when_no_deductions(self):
        """Employer flat_percentage works even without deductions targeting the account."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.FLAT_PERCENTAGE),
            employer_flat_percentage=Decimal("0.05"),
        )
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)

        result = calculate_investment_inputs(
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
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.FLAT_PERCENTAGE),
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
            investment_params=params,
            deductions=deductions,
            all_contributions=[],
            all_periods=[current_period],
            current_period=current_period,
            salary_gross_biweekly=Decimal("3846.15"),
        )

        # $120,000 / 26 = $4615.384... -> $4615.38 (hand-computed literal).
        assert result.employer_params["gross_biweekly"] == Decimal("4615.38")

    def test_no_employer_when_type_none(self):
        """Employer type 'none' produces employer_params=None."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            investment_params=params, deductions=[],
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
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        result = calculate_investment_inputs(
            investment_params=params, deductions=[],
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
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.MATCH),
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
            investment_params=params, deductions=deductions,
            all_contributions=[], all_periods=[current_period], current_period=current_period,
        )
        # gross * 0% = 0
        assert result.periodic_contribution == Decimal("0")
        # Employer params are still populated (the match params exist even if contribution is 0)
        assert result.employer_params is not None
        # $100,000 / 26 = $3846.153... -> $3846.15 (hand-computed literal).
        assert result.gross_biweekly == Decimal("3846.15")

    def test_negative_deduction_amount(self):
        """Negative flat deduction amount passes through sign-agnostically.

        Pins the service-layer contract: the service applies whatever
        amount it is handed, so a negative flat deduction reduces the
        periodic contribution arithmetically.  This is NOT a reachable
        production state (plan.md P-3, triage-verified CLOSED
        2026-06-09): the boundary rejects negative amounts twice --
        ``DeductionCreateSchema.amount`` requires
        ``Range(min=Decimal("0.0001"))`` (validation/salary.py) and the
        DB enforces ``ck_paycheck_deductions_positive_amount``
        (``amount > 0``).  Sign-guarding is the boundary's job; the
        service stays a pure function of its inputs.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        deductions = [FakeDeduction(
            amount=Decimal("-500.00"),
            calc_method_id=_flat_id(),
            annual_salary=Decimal("100000"),
            pay_periods_per_year=26,
        )]
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)
        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
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
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        contributions = [
            FakeContribution(estimated_amount=Decimal("200"), pay_period_id=1),
        ]
        periods = [
            FakePeriod(id=1, start_date=date(2026, 1, 2), period_index=0),
            FakePeriod(id=2, start_date=date(2026, 1, 16), period_index=1),
        ]
        result = calculate_investment_inputs(
            investment_params=params, deductions=[],
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
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
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
            investment_params=params, deductions=[],
            all_contributions=contributions, all_periods=periods, current_period=None,
        )
        # (200 + 400) / 2 periods = 300
        assert result.periodic_contribution == Decimal("300")
        assert result.ytd_contributions == Decimal("0")


class TestEstimatedVsEffectiveAlignment:
    """deep-quality-hunt #11: the inputs builder reads effective_amount.

    A transfer shadow's ``actual_amount`` is normally ``None`` (so
    ``effective_amount == estimated_amount``), but
    ``transfer_service._apply_actual_amount`` sets a realized actual on
    both shadows when a transfer is settled with a manual amount (the
    ``Transfer`` parent has no ``actual_amount`` column).  Once that
    happens, ``effective_amount`` (= actual) diverges from
    ``estimated_amount``.  The per-period timeline
    (``build_contribution_timeline``) applies ``effective_amount``, so the
    averaged periodic contribution and the YTD-seed/limit accounting must
    read ``effective_amount`` too -- otherwise the cap/limit math reads a
    different dollar than the growth engine actually applies.  These tests
    pin that alignment; they were proven to fail when the feeds summed
    ``estimated_amount``.
    """

    @staticmethod
    def _params(limit=None):
        return FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=limit,
            employer_contribution_type_id=_emp_type_id(
                EmployerContributionTypeEnum.NONE,
            ),
        )

    def test_periodic_contribution_uses_effective_not_estimated(self):
        """A settled shadow's realized actual drives the averaged contribution.

        One settled contribution, estimated $500 but actual $400
        (effective $400), in one period -> per-period average $400.
        Summing estimated_amount (the pre-#11 bug) would yield $500.
        """
        settled = FakeStatus(is_settled=True)
        contributions = [
            FakeContribution(
                estimated_amount=Decimal("500"), actual_amount=Decimal("400"),
                pay_period_id=1, status=settled,
            ),
        ]
        current_period = FakePeriod(id=1, start_date=date(2026, 1, 2), period_index=0)
        result = calculate_investment_inputs(
            investment_params=self._params(), deductions=[],
            all_contributions=contributions, all_periods=[current_period],
            current_period=current_period,
        )
        # effective $400 / 1 period = $400 (NOT estimated $500).
        assert result.periodic_contribution == Decimal("400")

    def test_over_contribution_actual_above_estimate_uses_effective(self):
        """The realized actual is honored when it EXCEEDS the estimate.

        Symmetric to the under-contribution case: a settled shadow
        estimated $400 but actual $500 (came in higher than planned)
        contributes its $500 effective amount -- the direction the annual
        limit/cap accounting must catch.  Summing estimated_amount (the
        pre-#11 bug) would under-count it at $400.
        """
        settled = FakeStatus(is_settled=True)
        contributions = [
            FakeContribution(
                estimated_amount=Decimal("400"), actual_amount=Decimal("500"),
                pay_period_id=1, status=settled,
            ),
        ]
        current_period = FakePeriod(id=1, start_date=date(2026, 1, 2), period_index=0)
        result = calculate_investment_inputs(
            investment_params=self._params(limit=Decimal("23500")), deductions=[],
            all_contributions=contributions, all_periods=[current_period],
            current_period=current_period,
        )
        # effective $500 / 1 period = $500 (NOT estimated $400).
        assert result.periodic_contribution == Decimal("500")
        # The displayed YTD (<= current) also reflects the realized $500.
        assert result.ytd_contributions == Decimal("500")

    def test_ytd_contributions_use_effective_not_estimated(self):
        """YTD-display and the engine seed both sum effective_amount.

        Three settled 2026 contributions, each estimated $500 but actual
        $400 (effective $400), current = period 3.  ytd_contributions
        (<= current) = 3 x $400 = $1,200; ytd_contributions_seed
        (< current) = 2 x $400 = $800.  Summing estimated_amount (the
        pre-#11 bug) would give $1,500 / $1,000.
        """
        settled = FakeStatus(is_settled=True)
        periods = [
            FakePeriod(id=1, start_date=date(2026, 1, 2), period_index=0),
            FakePeriod(id=2, start_date=date(2026, 1, 16), period_index=1),
            FakePeriod(id=3, start_date=date(2026, 1, 30), period_index=2),
        ]
        contributions = [
            FakeContribution(
                estimated_amount=Decimal("500"), actual_amount=Decimal("400"),
                pay_period_id=pid, status=settled,
            )
            for pid in (1, 2, 3)
        ]
        result = calculate_investment_inputs(
            investment_params=self._params(limit=Decimal("23500")), deductions=[],
            all_contributions=contributions, all_periods=periods,
            current_period=periods[2],
        )
        assert result.ytd_contributions == Decimal("1200")       # <= current (effective)
        assert result.ytd_contributions_seed == Decimal("800")   # < current (effective)

    def test_inputs_average_and_timeline_agree_on_effective(self):
        """The averaged inputs feed and the per-period timeline read the same dollar.

        The whole point of #11: build_contribution_timeline applies
        effective per period, so the periodic-average / YTD-seed feed must
        use effective too, or the limit/cap accounting reads a different
        number than the engine applies.  The same $500-estimated /
        $400-actual settled shadow yields $400 from BOTH feeds.
        """
        settled = FakeStatus(is_settled=True)
        contribution = FakeContribution(
            estimated_amount=Decimal("500"), actual_amount=Decimal("400"),
            pay_period_id=1, status=settled,
        )
        period = FakePeriod(id=1, start_date=date(2026, 1, 2), period_index=0)
        inputs = calculate_investment_inputs(
            investment_params=self._params(), deductions=[],
            all_contributions=[contribution], all_periods=[period],
            current_period=period,
        )
        timeline = build_contribution_timeline(
            deductions=[], contribution_transactions=[contribution], periods=[period],
        )
        # Both feeds read effective_amount ($400), so they agree.
        assert inputs.periodic_contribution == Decimal("400")
        assert len(timeline) == 1
        assert timeline[0].amount == Decimal("400")
        assert inputs.periodic_contribution == timeline[0].amount


# ── Fake Objects for build_contribution_timeline ──────────────


@dataclass
class FakeContribTransaction:
    """Shadow income transaction with status for timeline tests.

    Carries ``effective_amount`` as a pre-resolved field because the
    timeline only ever reads the resolved value; unlike
    :class:`FakeContribution` (the inputs-builder fake) it does NOT derive
    it from ``estimated_amount`` / ``actual_amount``.
    """
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
        # 7% of ($100,000 / 26) = 7% of $3846.15 = $269.2305 -> $269.23
        # (per the docstring); hand-computed literal, not a code mirror.
        assert result[0].amount == Decimal("269.23")

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
        # $500 flat + 5% of $3846.15 = $500.00 + $192.3075 -> $500.00 +
        # $192.31 = $692.31 (per the docstring); hand-computed literal.
        assert result[0].amount == Decimal("692.31")

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


class TestBuildContributionTimelineAnnualCap:
    """The deduction-funded timeline honors each deduction's ``annual_cap``
    (deep-hunt #2), matching the net-pay path: once a deduction's calendar-year
    total reaches the cap it contributes $0 for the rest of the year, then
    resumes the next January.  A fully-capped period still emits a $0 record so
    the growth engine uses 0, not the uncapped periodic-average fallback.
    """

    def test_capped_deduction_clamps_then_emits_zero(self):
        """$600/period under a $1000 cap: 600, 400, 0, 0 (a record per period)."""
        deductions = [FakeDeduction(
            amount=Decimal("600.00"), calc_method_id=_flat_id(),
            annual_salary=Decimal("100000"), pay_periods_per_year=26,
            annual_cap=Decimal("1000.00"),
        )]
        periods = [
            FakePeriod(id=1, start_date=date(2026, 1, 2), period_index=0),
            FakePeriod(id=2, start_date=date(2026, 1, 16), period_index=1),
            FakePeriod(id=3, start_date=date(2026, 1, 30), period_index=2),
            FakePeriod(id=4, start_date=date(2026, 2, 13), period_index=3),
        ]
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[], periods=periods,
        )
        # A record for every period (the $0 ones override the periodic fallback).
        assert [r.amount for r in result] == [
            Decimal("600.00"), Decimal("400.00"), Decimal("0"), Decimal("0"),
        ]
        assert sum(r.amount for r in result) == Decimal("1000.00")

    def test_cap_resets_next_calendar_year(self):
        """The cap is calendar-year scoped: the new-year period starts fresh."""
        deductions = [FakeDeduction(
            amount=Decimal("600.00"), calc_method_id=_flat_id(),
            annual_salary=Decimal("100000"), pay_periods_per_year=26,
            annual_cap=Decimal("1000.00"),
        )]
        periods = [
            FakePeriod(id=1, start_date=date(2026, 12, 4), period_index=0),
            FakePeriod(id=2, start_date=date(2026, 12, 18), period_index=1),
            FakePeriod(id=3, start_date=date(2027, 1, 1), period_index=2),
        ]
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[], periods=periods,
        )
        # 2026 caps at 600+400; 2027 resets -> full 600 again.
        assert [r.amount for r in result] == [
            Decimal("600.00"), Decimal("400.00"), Decimal("600.00"),
        ]

    def test_uncapped_deduction_unchanged(self):
        """A None cap is a passthrough: full amount every period, no $0 record."""
        deductions = [FakeDeduction(
            amount=Decimal("600.00"), calc_method_id=_flat_id(),
            annual_salary=Decimal("100000"), pay_periods_per_year=26,
            annual_cap=None,
        )]
        periods = [
            FakePeriod(id=1, start_date=date(2026, 1, 2), period_index=0),
            FakePeriod(id=2, start_date=date(2026, 1, 16), period_index=1),
        ]
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[], periods=periods,
        )
        assert [r.amount for r in result] == [
            Decimal("600.00"), Decimal("600.00"),
        ]

    def test_one_capped_one_uncapped_summed_per_period(self):
        """Per-period total sums each deduction's own capped amount."""
        deductions = [
            FakeDeduction(
                amount=Decimal("600.00"), calc_method_id=_flat_id(),
                annual_salary=Decimal("100000"), pay_periods_per_year=26,
                annual_cap=Decimal("1000.00"),
            ),
            FakeDeduction(
                amount=Decimal("100.00"), calc_method_id=_flat_id(),
                annual_salary=Decimal("100000"), pay_periods_per_year=26,
                annual_cap=None,
            ),
        ]
        periods = [
            FakePeriod(id=1, start_date=date(2026, 1, 2), period_index=0),
            FakePeriod(id=2, start_date=date(2026, 1, 16), period_index=1),
            FakePeriod(id=3, start_date=date(2026, 1, 30), period_index=2),
        ]
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[], periods=periods,
        )
        # Capped leg: 600, 400, 0.  Uncapped leg: 100 each.  Sum: 700, 500, 100.
        assert [r.amount for r in result] == [
            Decimal("700.00"), Decimal("500.00"), Decimal("100.00"),
        ]


class TestCurrentPeriodTransferContribution:
    """deep-hunt #9: the current period's transfer contribution the seed must
    remove before projecting.

    This is the amount the entries-aware end-of-current balance already
    contains AND the growth engine re-applies for the current period, so
    subtracting exactly it (and nothing else) leaves the contribution
    applied once without dropping any other current-period movement.
    """

    def test_sums_only_current_period_active_transfers(self):
        """Sums effective_amount of active shadow contributions in the current period."""
        current = FakePeriod(id=2, start_date=date(2026, 1, 16), period_index=1)
        settled = FakeStatus(is_settled=True)
        projected = FakeStatus(is_settled=False)
        txns = [
            FakeContribTransaction(Decimal("300.00"), 2, settled),    # current period
            FakeContribTransaction(Decimal("200.00"), 2, projected),  # current period
            FakeContribTransaction(Decimal("500.00"), 1, settled),    # other period -> excluded
            FakeContribTransaction(Decimal("999.00"), 3, projected),  # other period -> excluded
        ]
        # 300 + 200 fall in period 2; periods 1 and 3 are excluded.
        assert current_period_transfer_contribution(txns, current) == Decimal("500.00")

    def test_excludes_cancelled_or_credit(self):
        """Cancelled / credit transactions (excludes_from_balance) are not summed.

        This matches both what ``balance_calculator`` counts into the
        balance and what ``build_contribution_timeline`` re-applies, so the
        subtraction cancels exactly.
        """
        current = FakePeriod(id=2, start_date=date(2026, 1, 16), period_index=1)
        cancelled = FakeStatus(is_settled=False, excludes_from_balance=True)
        projected = FakeStatus(is_settled=False)
        txns = [
            FakeContribTransaction(Decimal("400.00"), 2, projected),  # counted
            FakeContribTransaction(Decimal("999.00"), 2, cancelled),  # excluded
        ]
        assert current_period_transfer_contribution(txns, current) == Decimal("400.00")

    def test_none_current_period_returns_zero(self):
        """A None current period yields ZERO (no subtraction)."""
        settled = FakeStatus(is_settled=True)
        txns = [FakeContribTransaction(Decimal("400.00"), 2, settled)]
        assert current_period_transfer_contribution(txns, None) == Decimal("0")

    def test_no_current_period_transfer_returns_zero(self):
        """No transfer in the current period -> ZERO.

        Deduction-funded or expense-only accounts have nothing to subtract,
        so the seed stays at the full end-of-current balance and the engine
        applies the deduction (not a transaction) once for the current
        period.
        """
        current = FakePeriod(id=5, start_date=date(2026, 2, 13), period_index=4)
        settled = FakeStatus(is_settled=True)
        txns = [FakeContribTransaction(Decimal("400.00"), 1, settled)]  # different period
        assert current_period_transfer_contribution(txns, current) == Decimal("0")
