"""
Tests for the investment projection helper.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import CalcMethodEnum, EmployerContributionTypeEnum
from app.services.growth_engine import ContributionRecord
from app.services.investment_projection import (
    build_contribution_timeline,
    calculate_investment_inputs,
    InvestmentInputs,
    PricedContribution,
)
from app.services.pay_calendar import PayCalendar

#: The read pass's clock for every timeline case here.  It is an ARGUMENT since
#: plan step C2-f2c -- ``build_contribution_timeline`` read ``date.today()``
#: until then -- so the confirmed / projected split below is decided by a
#: literal rather than by when the suite happens to run.
_AS_OF = date(2026, 6, 15)


def _priced(amount, payday, *, is_confirmed=False, account_id=1):
    """Build one :class:`PricedContribution`, the module's real input type.

    Since plan step X-au-c2 this module consumes records that were VALUED and
    SCREENED at the boundary (``projection_inputs.load_shadow_income_
    contributions_*``), not ORM rows -- so the two hand-rolled transaction
    fakes that used to mirror ``effective_amount`` and carry a status are gone,
    and with them the risk of a fake drifting from the model it imitated.

    The rules those fakes exercised did not disappear with them; they MOVED,
    and their tests moved with them to ``test_projection_inputs.py``, where the
    boundary is exercised against real rows: a settled shadow whose actual
    differs from its estimate is priced at the actual, and a Cancelled or
    Credit row is DROPPED rather than carried at zero.

    **The period key is the PAYDAY since plan step C2-f2c**, resolved at that
    same boundary.  It was the ``pay_period_id``, which every reader here then
    had to translate into a date by looking it up in a period list the caller
    supplied -- see the module under test.
    """
    return PricedContribution(
        account_id=account_id,
        payday=payday,
        amount=Decimal(str(amount)),
        is_confirmed=is_confirmed,
    )


def _periods(*paydays, cadence=14):
    """Return REAL :class:`DerivedPeriod`s opening on *paydays*.

    The ``FakePeriod`` these replaced carried an ``id`` this module no longer
    reads and a ``period_index`` it never read, and nothing held it to the
    shape of a period the application can actually produce -- one case built
    three periods that all opened on the same day, which
    :func:`~app.services.pay_calendar.derive_periods` refuses outright.  A real
    calendar's own window is the type ``/investment`` supplies since plan step
    C2-f2c and is structurally what ``/retirement``'s ORM rows are, so a case
    written over it grades both callers.
    """
    return PayCalendar.from_paydays(
        [(index, payday) for index, payday in enumerate(paydays, start=1)],
        cadence, user_id=1,
    ).saved()


def _flat_id():
    return ref_cache.calc_method_id(CalcMethodEnum.FLAT)


def _pct_id():
    return ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)


def _emp_type_id(member):
    """Resolve an EmployerContributionTypeEnum member to its ref-table id (#38)."""
    return ref_cache.employer_contribution_type_id(member)


#: ``$100,000 / 26``: the per-period gross :class:`FakeDeduction`'s default
#: salary derives at the default cadence, and the fallback the no-deduction
#: cases hand :func:`calculate_investment_inputs`.
_GROSS_BIWEEKLY = Decimal("3846.15")


@dataclass
class FakeDeduction:
    """An adapted deduction, as ``adapt_deductions`` now produces one.

    **The COUNT is the owner's cadence since plan step R-F16; the SALARY is
    still this deduction's own profile.**  ``pay_periods_per_year`` was a
    second stored answer to "how often am I paid" and is derived now.  The
    salary stays per row because an owner may hold several active profiles and
    each prices its own percentage: collapsing them to one owner-level gross
    was measured at a 39% swing on a two-job owner, and a nondeterministic one.
    The gross derived here is raise-BLIND -- finding **D45**.
    """

    amount: Decimal
    calc_method_id: int
    annual_salary: Decimal = Decimal("100000")
    periods_per_year: Decimal = Decimal("26")
    # Calendar-year ceiling (PaycheckDeduction.annual_cap); None = uncapped.
    annual_cap: Decimal | None = None


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
        current_period = _periods(date(2026, 3, 5))[0]
        result = calculate_investment_inputs(
            investment_params=params, deductions=[],
            all_contributions=[], current_period=current_period,
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
        deductions = [FakeDeduction(amount=Decimal("500.00"), calc_method_id=_flat_id())]
        current_period = _periods(date(2026, 3, 5))[0]
        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
            all_contributions=[], current_period=current_period,
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
            annual_cap=Decimal("1000.00"),
        )]
        current_period = _periods(date(2026, 3, 5))[0]
        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
            all_contributions=[], current_period=current_period,
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
        deductions = [FakeDeduction(amount=Decimal("0.07"), calc_method_id=_pct_id())]
        current_period = _periods(date(2026, 3, 5))[0]
        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
            all_contributions=[], current_period=current_period,
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
        deductions = [FakeDeduction(
            amount=Decimal("0.05"), calc_method_id=_pct_id(),
            annual_salary=Decimal("26013"),
        )]
        current_period = _periods(date(2026, 3, 5))[0]
        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
            all_contributions=[], current_period=current_period,
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
        periods = _periods(
            date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
        )
        contributions = [
            _priced(Decimal("200"), periods[0].start_date),
            _priced(Decimal("200"), periods[1].start_date),
            _priced(Decimal("300"), periods[2].start_date),
        ]
        result = calculate_investment_inputs(
            investment_params=params, deductions=[],
            all_contributions=contributions, current_period=periods[0],
        )
        # ($200 + $200 + $300) over THREE distinct paydays = $233.33.
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
        deductions = [FakeDeduction(amount=Decimal("500.00"), calc_method_id=_flat_id())]
        current_period = _periods(date(2026, 3, 5))[0]
        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
            all_contributions=[], current_period=current_period,
        )
        assert result.employer_params is not None
        assert result.employer_params["type_id"] == _emp_type_id(
            EmployerContributionTypeEnum.FLAT_PERCENTAGE,
        )
        assert result.employer_params["flat_percentage"] == Decimal("0.05")
        # The caller's engine gross, unchanged by the deduction (R-F16).
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
        deductions = [FakeDeduction(amount=Decimal("500.00"), calc_method_id=_flat_id())]
        current_period = _periods(date(2026, 3, 5))[0]
        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
            all_contributions=[], current_period=current_period,
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
        periods = _periods(
            date(2025, 12, 19), date(2026, 1, 2), date(2026, 1, 16),
            date(2026, 1, 30), date(2026, 2, 13),
        )
        contributions = [
            _priced(Decimal("500"), period.start_date) for period in periods
        ]
        result = calculate_investment_inputs(
            investment_params=params, deductions=[],
            all_contributions=contributions, current_period=periods[3],
        )
        # The 2025 payday is a different calendar year and 2026-02-13 is past
        # the current period, so three of the five count.
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
        periods = _periods(
            date(2025, 12, 19), date(2026, 1, 2), date(2026, 1, 16),
            date(2026, 1, 30), date(2026, 2, 13),
        )
        contributions = [
            _priced(Decimal("500"), period.start_date) for period in periods
        ]
        result = calculate_investment_inputs(
            investment_params=params, deductions=[],
            all_contributions=contributions, current_period=periods[3],
        )
        assert result.ytd_contributions == Decimal("1500")          # <= current (display)
        assert result.ytd_contributions_seed == Decimal("1000")     # < current (engine seed)

    def test_ytd_contributions_seed_none_current_period(self):
        """deep-hunt #10: a None current period yields a ZERO engine seed."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        contributions = [_priced(Decimal("500"), date(2026, 1, 2))]
        result = calculate_investment_inputs(
            investment_params=params, deductions=[],
            all_contributions=contributions, current_period=None,
        )
        assert result.ytd_contributions_seed == Decimal("0")

    def test_combined_deductions_and_transfers(self):
        """Deductions and contributions both add to periodic_contribution."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        deductions = [FakeDeduction(amount=Decimal("500.00"), calc_method_id=_flat_id())]
        periods = _periods(date(2026, 1, 2), date(2026, 1, 16))
        contributions = [
            _priced(Decimal("200"), periods[0].start_date),
            _priced(Decimal("200"), periods[1].start_date),
        ]
        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
            all_contributions=contributions, current_period=periods[0],
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
        current_period = _periods(date(2026, 3, 5))[0]

        result = calculate_investment_inputs(
            investment_params=params,
            deductions=[],
            all_contributions=[],
            current_period=current_period,
            salary_gross_biweekly=Decimal("3846.15"),
        )

        assert result.employer_params is not None
        assert result.employer_params["gross_biweekly"] == Decimal("3846.15")
        assert result.periodic_contribution == Decimal("0")

    def test_each_deduction_prices_from_its_OWN_profile(self):
        """Two profiles, two salaries, two percentages -- summed separately.

        Input: two 6% deductions into one account, one on a ``$91,675``
        profile and one on a ``$40,000`` profile.
        Expected: ``6% of 3,525.96 + 6% of 1,538.46 = 211.56 + 92.31 =
        $303.87``.

        **This is why the salary stays on the ROW.**  Plan step R-F16's first
        draft collapsed the basis to ONE owner-level gross -- the raise-aware
        engine figure -- which is more correct for a single-job owner and
        wrong for this one: its adversarial review measured the same two
        deductions at ``$423.12`` or ``$184.62`` depending on which profile
        ``income_service.get_current_gross_biweekly``'s unordered ``.first()``
        happened to return, a 39% swing that flips between renders with no
        data change. Multiple active profiles are a supported shape --
        ``tax_report_service`` iterates them as one filer with several jobs.
        The raise-blindness of the per-row gross is real and is finding
        **D45**; it is not fixed by deleting the per-profile basis.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(
                EmployerContributionTypeEnum.NONE,
            ),
        )
        deductions = [
            FakeDeduction(
                amount=Decimal("0.06"), calc_method_id=_pct_id(),
                annual_salary=Decimal("91675"),
            ),
            FakeDeduction(
                amount=Decimal("0.06"), calc_method_id=_pct_id(),
                annual_salary=Decimal("40000"),
            ),
        ]
        current_period = _periods(date(2026, 3, 5))[0]

        result = calculate_investment_inputs(
            investment_params=params,
            deductions=deductions,
            all_contributions=[],
            current_period=current_period,
        )

        # 91,675 / 26 = 3,525.96 -> 6% = 211.5576 -> 211.56
        # 40,000 / 26 = 1,538.46 -> 6% =  92.3076 ->  92.31
        assert result.periodic_contribution == Decimal("303.87")

    def test_a_weekly_owners_deduction_prices_over_52_paychecks(self):
        """THE CADENCE AXIS: the stamped count drives the per-period gross.

        Input: one ``$91,675`` profile, 6%, adapted at a 7-day cadence.
        Expected: ``6% of (91,675 / 52) = 6% of 1,762.98 = $105.78``, half
        the biweekly figure.
        Why: every other case in this module runs at 26, where the derived
        count and the deleted ``pay_periods_per_year`` column agree, so none
        of them can see a count that is not the owner's. This is the case that
        fails if ``periods_per_year`` stops coming from the cadence.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(
                EmployerContributionTypeEnum.NONE,
            ),
        )
        deductions = [FakeDeduction(
            amount=Decimal("0.06"), calc_method_id=_pct_id(),
            annual_salary=Decimal("91675"),
            periods_per_year=Decimal("52"),
        )]
        current_period = _periods(date(2026, 3, 5))[0]

        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
            all_contributions=[], current_period=current_period,
        )

        # 91,675 / 52 = 1,762.98 (half-up) -> 6% = 105.7788 -> 105.78
        assert result.periodic_contribution == Decimal("105.78")

    def test_a_capped_deduction_spreads_over_the_OWNERS_paychecks(self):
        """A calendar-year cap is spread across 52, not 26, for a weekly owner.

        Input: a ``$600``/period deduction under a ``$1,000`` annual cap, at a
        7-day cadence.
        Expected: ``min(600 x 52, 1000) / 52 = $19.23``.
        Why: this is the F-16 shape one table over. The sibling test above
        runs the same cap at 26 and gets ``$38.46``; with a hardcoded 26 a
        weekly owner's cap spreads over half the paychecks they receive and
        the modelled contribution is exactly DOUBLE -- compounded forward by
        the growth engine for the whole projection horizon.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(
                EmployerContributionTypeEnum.NONE,
            ),
        )
        deductions = [FakeDeduction(
            amount=Decimal("600.00"), calc_method_id=_flat_id(),
            periods_per_year=Decimal("52"),
            annual_cap=Decimal("1000.00"),
        )]
        current_period = _periods(date(2026, 3, 5))[0]

        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
            all_contributions=[], current_period=current_period,
        )

        assert result.periodic_contribution == Decimal("19.23")

    def test_no_employer_when_type_none(self):
        """Employer type 'none' produces employer_params=None."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        current_period = _periods(date(2026, 3, 5))[0]
        result = calculate_investment_inputs(
            investment_params=params, deductions=[],
            all_contributions=[], current_period=current_period,
        )
        assert result.employer_params is None

    def test_no_current_period_and_nothing_loaded_does_not_crash(self):
        """A fresh user -- no periods, so no current period -- still answers.

        The function returns a valid InvestmentInputs with zero contributions
        and zero YTD.  Named for ``current_period`` alone since plan step
        C2-f2c: the empty period LIST this also passed is no longer an
        argument, so a name promising to vary it would promise coverage the
        case cannot give.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        result = calculate_investment_inputs(
            investment_params=params, deductions=[],
            all_contributions=[], current_period=None,
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
        )]
        current_period = _periods(date(2026, 3, 5))[0]
        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
            all_contributions=[], current_period=current_period,
            salary_gross_biweekly=Decimal("3846.15"),
        )
        # gross * 0% = 0
        assert result.periodic_contribution == Decimal("0")
        # Employer params are still populated (the match params exist even if contribution is 0)
        assert result.employer_params is not None
        # The caller's gross, unchanged by the deduction (plan step R-F16).
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
        )]
        current_period = _periods(date(2026, 3, 5))[0]
        result = calculate_investment_inputs(
            investment_params=params, deductions=deductions,
            all_contributions=[], current_period=current_period,
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
        periods = _periods(date(2026, 1, 2), date(2026, 1, 16))
        contributions = [
            _priced(Decimal("200"), periods[0].start_date),
        ]
        result = calculate_investment_inputs(
            investment_params=params, deductions=[],
            all_contributions=contributions, current_period=periods[0],
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
        periods = _periods(date(2026, 1, 2), date(2026, 1, 16))
        contributions = [
            _priced(Decimal("200"), periods[0].start_date),
            _priced(Decimal("400"), periods[1].start_date),
        ]
        result = calculate_investment_inputs(
            investment_params=params, deductions=[],
            all_contributions=contributions, current_period=None,
        )
        # (200 + 400) / 2 periods = 300
        assert result.periodic_contribution == Decimal("300")
        assert result.ytd_contributions == Decimal("0")


# The ESTIMATED-vs-EFFECTIVE alignment class (deep-quality-hunt #11) lived
# here and MOVED WHOLE to ``test_projection_inputs.TestShadowContributionBoundary``
# at plan step X-au-c2, with its four cases and their hand-computed figures
# intact.  It pinned that a settled shadow whose ``actual_amount`` differs from
# its ``estimated_amount`` is read at the ACTUAL by the averaged inputs feed,
# by the YTD/limit accounting, and by the per-period timeline -- all three, so
# the cap math cannot read a different dollar than the growth engine applies.
#
# That rule did not weaken; it moved down a tier.  This module no longer values
# anything: it consumes :class:`PricedContribution` records that were valued at
# the boundary, so asserting the rule HERE would only assert that a record
# carrying $400 averages to $400.  The boundary tests grade it against real
# rows, which is where it can still fail.  What survives here, structurally, is
# the AGREEMENT half: every feed in this module reads one ``amount`` field off
# one record, so two of them pricing a row differently is no longer expressible.


# ── Fake Objects for build_contribution_timeline ──────────────


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
            amount=Decimal("500.00"), calc_method_id=_flat_id()
        )]
        periods = _periods(
            date(2020, 1, 2), date(2020, 1, 16), date(2020, 1, 30),
        )
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[],
            periods=periods, as_of=_AS_OF,
        )
        assert len(result) == 3
        for r in result:
            assert r.amount == Decimal("500.00")
            assert isinstance(r, ContributionRecord)

    def test_transfer_only(self):
        """Shadow income transactions with no deductions: one record per transaction."""
        periods = _periods(date(2020, 1, 2), date(2020, 1, 16))
        txns = [
            _priced(Decimal("200"), periods[0].start_date, is_confirmed=True),
            _priced(Decimal("300"), periods[1].start_date, is_confirmed=True),
        ]
        result = build_contribution_timeline(
            deductions=[], contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
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
            amount=Decimal("500.00"), calc_method_id=_flat_id()
        )]
        periods = _periods(date(2020, 1, 2))
        txns = [_priced(Decimal("200"), periods[0].start_date, is_confirmed=True)]
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        # One record from deduction, one from transfer, same date.
        assert len(result) == 2
        total = sum(r.amount for r in result)
        assert total == Decimal("700.00")

    def test_deduction_flat_amount(self):
        """Flat-dollar deduction: amount matches deduction.amount exactly."""
        deductions = [FakeDeduction(
            amount=Decimal("269.23"), calc_method_id=_flat_id()
        )]
        periods = _periods(date(2020, 1, 2))
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[],
            periods=periods, as_of=_AS_OF,
        )
        assert result[0].amount == Decimal("269.23")

    def test_deduction_percentage(self):
        """Percentage deduction: amount = gross_biweekly * percentage.

        7% of ($100,000 / 26) = 7% of $3846.15 = $269.23.
        """
        deductions = [FakeDeduction(
            amount=Decimal("0.07"), calc_method_id=_pct_id()
        )]
        periods = _periods(date(2020, 1, 2))
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[],
            periods=periods, as_of=_AS_OF,
        )
        # 7% of ($100,000 / 26) = 7% of $3846.15 = $269.2305 -> $269.23
        # (per the docstring); hand-computed literal, not a code mirror.
        assert result[0].amount == Decimal("269.23")

    def test_is_confirmed_deduction_past(self):
        """Deduction for a past period: is_confirmed=True."""
        deductions = [FakeDeduction(
            amount=Decimal("500"), calc_method_id=_flat_id()
        )]
        # Before the pass's clock, by a literal rather than by when this runs.
        periods = _periods(date(2020, 1, 2))
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[],
            periods=periods, as_of=_AS_OF,
        )
        assert result[0].is_confirmed is True

    def test_is_confirmed_deduction_future(self):
        """Deduction for a future period: is_confirmed=False."""
        deductions = [FakeDeduction(
            amount=Decimal("500"), calc_method_id=_flat_id()
        )]
        # After the pass's clock, by a literal rather than by when this runs.
        periods = _periods(date(2099, 1, 2))
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[],
            periods=periods, as_of=_AS_OF,
        )
        assert result[0].is_confirmed is False

    def test_is_confirmed_transfer_settled(self):
        """Settled shadow transaction: is_confirmed=True."""
        periods = _periods(date(2020, 1, 2))
        txns = [_priced(Decimal("200"), periods[0].start_date, is_confirmed=True)]
        result = build_contribution_timeline(
            deductions=[], contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        assert result[0].is_confirmed is True

    def test_is_confirmed_transfer_projected(self):
        """Projected shadow transaction: is_confirmed=False."""
        periods = _periods(date(2020, 1, 2))
        txns = [_priced(Decimal("200"), periods[0].start_date, is_confirmed=False)]
        result = build_contribution_timeline(
            deductions=[], contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        assert result[0].is_confirmed is False

    def test_is_confirmed_mixed_same_date(self):
        """Confirmed deduction + projected transfer on same date.

        Both produce records for the same date.  The growth engine's
        lookup dict applies the conservative rule (all must be confirmed).
        Here we verify both records are produced -- one True, one False.
        """
        deductions = [FakeDeduction(
            amount=Decimal("500"), calc_method_id=_flat_id()
        )]
        # Past date so the deduction is confirmed.
        periods = _periods(date(2020, 1, 2))
        txns = [_priced(Decimal("200"), periods[0].start_date, is_confirmed=False)]
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        assert len(result) == 2
        confirmed_flags = {r.is_confirmed for r in result}
        assert True in confirmed_flags   # Deduction (past).
        assert False in confirmed_flags  # Transfer (projected).

    def test_empty_both(self):
        """No deductions and no transactions: empty list returned."""
        periods = _periods(date(2020, 1, 2))
        result = build_contribution_timeline(
            deductions=[], contribution_transactions=[],
            periods=periods, as_of=_AS_OF,
        )
        assert result == []

    def test_sorted_output(self):
        """Output is sorted by contribution_date regardless of input order."""
        periods = _periods(date(2020, 1, 2), date(2020, 1, 16))
        txns = [
            _priced(Decimal("300"), periods[1].start_date, is_confirmed=True),
            _priced(Decimal("100"), periods[0].start_date, is_confirmed=True),
        ]
        result = build_contribution_timeline(
            deductions=[], contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        dates = [r.contribution_date for r in result]
        assert dates == sorted(dates)

    def test_emits_the_record_amount_untransformed(self):
        """A record's priced amount reaches its ContributionRecord unchanged.

        Renamed at plan step X-au-c2: it graded ``effective_amount``, an
        accessor this module no longer touches.  What it can still pin is that
        the timeline does not round, scale or re-derive the figure the boundary
        priced -- which is worth one case, because Path 1 beside it DOES
        transform (the annual cap clamps a deduction).
        """
        # effective_amount is a pre-computed value (property on real model).
        periods = _periods(date(2020, 1, 2))
        txns = [_priced(Decimal("999.99"), periods[0].start_date, is_confirmed=True)]
        result = build_contribution_timeline(
            deductions=[], contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        assert result[0].amount == Decimal("999.99")

    def test_multiple_deductions_summed(self):
        """Two deductions targeting the same account: amounts summed per period.

        $500 flat + 5% of $3846.15 = $500 + $192.31 = $692.31.
        """
        deductions = [
            FakeDeduction(
                amount=Decimal("500.00"), calc_method_id=_flat_id()
            ),
            FakeDeduction(
                amount=Decimal("0.05"), calc_method_id=_pct_id()
            ),
        ]
        periods = _periods(date(2020, 1, 2))
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[],
            periods=periods, as_of=_AS_OF,
        )
        # $500 flat + 5% of $3846.15 = $500.00 + $192.3075 -> $500.00 +
        # $192.31 = $692.31 (per the docstring); hand-computed literal.
        assert result[0].amount == Decimal("692.31")

    # The Cancelled / Credit skip that used to be pinned here moved with the
    # rule (plan step X-au-c2): this module no longer screens by status, the
    # boundary loader does, and it DROPS such a row rather than pricing it at
    # zero.  Its replacement is
    # ``test_projection_inputs.TestShadowContributionBoundary
    # .test_excluded_status_rows_are_dropped_not_zeroed``, which grades the
    # same rule against real rows and additionally pins why dropping and
    # zeroing are not interchangeable here.

    def test_transaction_outside_period_range_skipped(self):
        """A contribution dated outside the timeline's DOMAIN is skipped.

        The predicate reads the record's own payday since plan step C2-f2c --
        it matched a ``pay_period_id`` against an id-keyed map of *periods*
        until then -- so the case supplies a payday no period here opens on.
        """
        periods = _periods(date(2020, 1, 2))
        txns = [_priced(Decimal("200"), date(2020, 3, 5), is_confirmed=True)]
        result = build_contribution_timeline(
            deductions=[], contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
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
            annual_cap=Decimal("1000.00"),
        )]
        periods = _periods(
            date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
            date(2026, 2, 13),
        )
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[],
            periods=periods, as_of=_AS_OF,
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
            annual_cap=Decimal("1000.00"),
        )]
        periods = _periods(
            date(2026, 12, 4), date(2026, 12, 18), date(2027, 1, 1),
        )
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[],
            periods=periods, as_of=_AS_OF,
        )
        # 2026 caps at 600+400; 2027 resets -> full 600 again.
        assert [r.amount for r in result] == [
            Decimal("600.00"), Decimal("400.00"), Decimal("600.00"),
        ]

    def test_uncapped_deduction_unchanged(self):
        """A None cap is a passthrough: full amount every period, no $0 record."""
        deductions = [FakeDeduction(
            amount=Decimal("600.00"), calc_method_id=_flat_id(),
            annual_cap=None,
        )]
        periods = _periods(date(2026, 1, 2), date(2026, 1, 16))
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[],
            periods=periods, as_of=_AS_OF,
        )
        assert [r.amount for r in result] == [
            Decimal("600.00"), Decimal("600.00"),
        ]

    def test_one_capped_one_uncapped_summed_per_period(self):
        """Per-period total sums each deduction's own capped amount."""
        deductions = [
            FakeDeduction(
                amount=Decimal("600.00"), calc_method_id=_flat_id(),
                annual_cap=Decimal("1000.00"),
            ),
            FakeDeduction(
                amount=Decimal("100.00"), calc_method_id=_flat_id(),
                annual_cap=None,
            ),
        ]
        periods = _periods(
            date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
        )
        result = build_contribution_timeline(
            deductions=deductions, contribution_transactions=[],
            periods=periods, as_of=_AS_OF,
        )
        # Capped leg: 600, 400, 0.  Uncapped leg: 100 each.  Sum: 700, 500, 100.
        assert [r.amount for r in result] == [
            Decimal("700.00"), Decimal("500.00"), Decimal("100.00"),
        ]
