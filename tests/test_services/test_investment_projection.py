"""
Tests for the investment projection helper.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import EmployerContributionTypeEnum
from app.services.growth_engine import ContributionRecord
from app.services.investment_projection import (
    AccountPayrollFeed,
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
        history_opens_on=None,
    ).saved()


def _feed(periods=(), *, employee=None, gross=None, linked=None):
    """Build an :class:`AccountPayrollFeed` over *periods*' paydays.

    **The input type since plan step salary:R14-b** (ruling **R-SAL2**), in
    place of the ``FakeDeduction`` this file built for every case.  That fake
    carried ``(amount, calc_method_id, annual_salary, periods_per_year,
    annual_cap)`` -- the five fields ``adapt_deductions`` flattened a real
    deduction into -- and every case then asserted what THIS module derived
    from them.  It derives nothing now: the paycheck engine prices a
    deduction when it prices the paycheck, and the feed is the fold of its
    per-payday answer.  So the fake and the arithmetic it fed both went, and
    the cases that graded that arithmetic went with them (see the class
    docstrings for where each rule is graded now).

    Args:
        periods: The periods whose paydays the maps are keyed by.
        employee: What payroll puts in per payday -- one figure for every
            payday, or a ``{payday: amount}`` map.  ``None`` means the account
            has no employee feed at all, which is the EMPTY map and not a map
            of zeros; :attr:`AccountPayrollFeed.models_employee` tells them
            apart and the two behave differently.
        gross: The funding profile's gross per payday, same two forms.
            ``None`` means no funding profile is known, which is what
            :attr:`AccountPayrollFeed.funds_employer` reports ``False`` for.
        linked: Whether a deduction NAMES this account, whatever it pays --
            :attr:`AccountPayrollFeed.is_payroll_linked`, the PRESENCE fact
            path 1 of the timeline gates on.  Defaults to "an employee series
            was given", which is what every case here means; pass it
            explicitly for the two states that come apart, a linked deduction
            pricing ``$0.00`` and an unlinked account.

    Returns:
        The :class:`AccountPayrollFeed`.
    """
    paydays = [period.start_date for period in periods]

    def _series(value):
        if value is None:
            return {}
        if isinstance(value, dict):
            return {day: Decimal(str(amount)) for day, amount in value.items()}
        return {day: Decimal(str(value)) for day in paydays}

    return AccountPayrollFeed(
        employee_by_payday=_series(employee),
        gross_by_payday=_series(gross),
        is_payroll_linked=(employee is not None) if linked is None else linked,
    )


def _emp_type_id(member):
    """Resolve an EmployerContributionTypeEnum member to its ref-table id (#38)."""
    return ref_cache.employer_contribution_type_id(member)


#: The per-period gross the employer cases size a percentage off.  It was
#: ``$100,000 / 26`` derived from a fake deduction's salary until plan step
#: salary:R14-b; it is stated on the feed now, because a gross is a fact about
#: a PAYDAY and no longer something this module divides for itself.
_GROSS_BIWEEKLY = Decimal("3846.15")


@dataclass
class FakeInvestmentParams:
    assumed_annual_return: Decimal
    annual_contribution_limit: Decimal
    employer_contribution_type_id: int
    employer_flat_percentage: Decimal = Decimal("0")
    employer_match_percentage: Decimal = Decimal("0")
    employer_match_cap_percentage: Decimal = Decimal("0")


class TestAccountPayrollFeed:
    """The feed's own rules -- what it answers for a payday, and past one.

    Plan step **salary:R14-b**.  Everything here is about the SERIES: that a
    payday's answer is its own, that a skipped payday is a zero rather than a
    gap, and what the feed says about a payday the owner's calendar does not
    reach.  What each figure IS -- the raise, the inflation escalation, the
    cadence, the calendar-year cap -- is the paycheck engine's, graded in
    ``test_paycheck_calculator.py``; the fold that produces these maps from its
    breakdowns is graded in ``test_projection_inputs.py`` against real rows.
    """

    def test_each_payday_answers_with_its_OWN_figure(self):
        """The feed is a SERIES, which is finding D45's whole remedy.

        The feed it replaced was one scalar for every period, so an owner with
        a raise had every projected paycheck priced at one paycheck's answer.
        The figures here are the developer's own measured pair (ledger row
        **D45**): a gross of ``$3,525.96`` before the 2026-07 raise and
        ``$3,631.74`` after it.
        """
        periods = _periods(date(2026, 6, 4), date(2026, 7, 2))
        feed = _feed(periods, gross={
            periods[0].start_date: Decimal("3525.96"),
            periods[1].start_date: Decimal("3631.74"),
        })
        assert feed.gross_at(periods[0].start_date) == Decimal("3525.96")
        assert feed.gross_at(periods[1].start_date) == Decimal("3631.74")

    def test_a_skipped_payday_is_an_explicit_zero_not_a_gap(self):
        """A cadence skip reads ``$0.00``, not the previous payday's amount.

        11 of the developer's 12 live deductions are 24-per-year, which the
        engine does not take on its month's third payday.  The map is TOTAL
        over the calendar's paydays for exactly this: were the skipped payday
        merely ABSENT, :meth:`employee_at` could not tell it from a payday
        past the calendar and would hold the previous amount over the skip.
        """
        periods = _periods(
            date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
        )
        feed = _feed(periods, employee={
            periods[0].start_date: Decimal("211.56"),
            periods[1].start_date: Decimal("211.56"),
            periods[2].start_date: Decimal("0"),
        })
        assert feed.employee_at(periods[2].start_date) == Decimal("0")

    def test_the_gross_HOLDS_past_the_owners_calendar(self):
        """A payday the calendar does not reach reads the last real paycheck.

        The interim rule the developer ruled on 2026-09-04: the app holds two
        long-horizon salary models that disagree, so a projection past the
        schedule states the last real paycheck until the step that unifies
        them lands.
        """
        periods = _periods(date(2028, 7, 13), date(2028, 8, 10))
        feed = _feed(periods, gross={
            periods[0].start_date: Decimal("4047.97"),
            periods[1].start_date: Decimal("4047.97"),
        })
        assert feed.gross_at(date(2040, 1, 1)) == Decimal("4047.97")

    def test_the_hold_has_a_DIRECTION(self):
        """A payday BEFORE the calendar holds the earliest, not the latest.

        Holding the latest paycheck backward would answer a pre-schedule
        payday with a salary the owner had not yet been raised to -- here
        ``$3,631.74`` for a day before they were earning ``$3,525.96``.  No
        consumer asks the backward question today (every domain opens at or
        after the calendar's first payday), which is exactly why the
        direction lives in the value rather than in a caller's discipline: an
        answer that is wrong only because nobody asks it is the shape this
        module has shipped before.
        """
        periods = _periods(date(2026, 6, 4), date(2026, 7, 2))
        feed = _feed(periods, gross={
            periods[0].start_date: Decimal("3525.96"),
            periods[1].start_date: Decimal("3631.74"),
        })
        assert feed.gross_at(date(2020, 1, 1)) == Decimal("3525.96")
        assert feed.gross_at(date(2040, 1, 1)) == Decimal("3631.74")

    def test_the_EMPLOYEE_direction_is_per_YEAR_not_per_payday(self):
        """The employee series holds a year's average, so its ends are years.

        The gross holds at a PAYDAY in each direction; the employee amount
        holds at a COMPLETE calendar YEAR's average, because that is the span
        ``annual_cap`` is defined over.  So the two directions of the employee
        series come apart only across years, and a feed spanning one year
        answers the same figure both ways -- which is correct and is why this
        case builds two full years rather than two paydays.

        2026 pays ``$200`` a payday and 2027 pays ``$220``, and each year's
        average is its own figure because the deduction is flat.

        **The window runs one payday into 2028 so that 2027 is COVERED.**  An
        earlier fixture stopped at 2027-12-17 and this docstring claimed both
        years held 26 paydays; 2027 holds 27 here (2027-01-01 through
        2027-12-31), so that window saw 26 of 27 and the rule of the day
        graded it complete on the count.  An adversarial pass measured what
        that costs a front-loaded capped deduction -- 60% understated,
        permanently -- so covering a year now means reaching past both its
        edges, and this fixture does.
        """
        paydays = [date(2026, 1, 2) + timedelta(days=14 * i) for i in range(54)]
        periods = _periods(*paydays)
        priced = {
            day: Decimal("200") if day.year == 2026 else Decimal("220")
            for day in paydays
        }
        feed = _feed(periods, employee=priced)
        assert feed.employee_at(date(2020, 1, 1)) == Decimal("200")
        assert feed.employee_at(date(2040, 1, 1)) == Decimal("220")

    def test_a_CAPPED_deductions_hold_respects_its_annual_cap(self):
        """The hold is a YEAR's average, and the cap is why.

        **An adversarial review of this step's own fix measured the
        single-day rule wrong by 10.4x**, which is why this case exists.  A
        deduction of ``$600`` a payday against a ``$1,000`` calendar-year cap
        is priced by the engine as ``$600, $400, $0, $0 ...``: the clamp lands
        the moment the year's total reaches the cap.  Holding "the last payday
        that PAID something" picks the ``$400`` and applies it to every
        projected period with no cap and no year reset -- ``$10,400`` a year
        against a ``$1,000`` cap, compounded over the tail of a 40-year chart.

        The year's average is ``$1,000 / 26 = $38.46``, which is exactly what
        the deleted ``_annual_cap_averaged`` answered for this deduction, now
        derived from the ENGINE's own priced figures instead of a second
        formula.
        """
        paydays = [date(2026, 1, 2) + timedelta(days=14 * i) for i in range(26)]
        periods = _periods(*paydays)
        priced = dict.fromkeys(paydays, Decimal("0"))
        priced[paydays[0]] = Decimal("600")
        priced[paydays[1]] = Decimal("400")
        feed = _feed(periods, employee=priced)
        # $1,000 over the year's 26 paydays.
        assert feed.employee_at(date(2040, 1, 1)) == Decimal("38.46")

    def test_a_trailing_cadence_SKIP_does_not_delete_the_feed(self):
        """A schedule ending on a skipped payday still holds the real rate.

        A 24-per-year deduction is not taken on its month's third payday, so a
        saved schedule that happens to END on one would hold ``$0.00`` for the
        whole projection if the rule read the last payday alone -- a 1-in-13
        chance of silently deleting the feed.  Averaging the year covers it:
        the skips are inside the span being averaged, so the held figure is
        the year's true per-payday rate rather than either extreme.

        24 paydays of ``$211.56`` and 2 skipped, over a 26-payday year:
        ``24 x 211.56 / 26 = $195.29``.
        """
        paydays = [date(2026, 1, 2) + timedelta(days=14 * i) for i in range(26)]
        periods = _periods(*paydays)
        priced = {day: Decimal("211.56") for day in paydays}
        priced[paydays[24]] = Decimal("0")
        priced[paydays[25]] = Decimal("0")
        feed = _feed(periods, employee=priced)
        assert feed.employee_at(date(2040, 1, 1)) == Decimal("195.29")

    def test_an_account_that_never_received_anything_holds_zero(self):
        """A feed of zeros holds ``$0.00`` -- the answer, not the artifact."""
        periods = _periods(date(2026, 1, 2), date(2026, 1, 16))
        feed = _feed(periods, employee=Decimal("0"))
        assert feed.employee_at(date(2040, 1, 1)) == Decimal("0")
        assert feed.models_employee is False

    def test_no_funding_profile_refuses_a_gross_rather_than_answering_zero(self):
        """An unknown funding job answers ``None``, so no caller can spend it.

        The developer's 2026-09-04 ruling: an employer contribution whose
        funding job is unrecorded models NO money.  ``None`` rather than
        ``$0.00`` because a zero is a basis a percentage can be taken of, and
        the point is that there is no basis.
        """
        periods = _periods(date(2026, 1, 2))
        feed = _feed(periods, employee=Decimal("500"))
        assert feed.funds_employer is False
        assert feed.gross_at(periods[0].start_date) is None

    def test_absent_models_neither_half(self):
        """The explicit token for an account no payroll funds."""
        feed = AccountPayrollFeed.absent()
        assert feed.models_employee is False
        assert feed.funds_employer is False
        assert feed.employee_at(date(2026, 1, 2)) == Decimal("0")
        assert feed.gross_at(date(2026, 1, 2)) is None

    def test_salary_basis_resolves_in_window_then_holds(self):
        """The growth engine's ``period -> gross`` hook, over both regimes."""
        periods = _periods(date(2026, 1, 2), date(2026, 1, 16))
        feed = _feed(periods, gross={
            periods[0].start_date: Decimal("3525.96"),
            periods[1].start_date: Decimal("3631.74"),
        })
        basis = feed.salary_basis()
        assert basis(periods[0]) == Decimal("3525.96")
        beyond = _periods(date(2040, 1, 6))[0]
        assert basis(beyond) == Decimal("3631.74")

    def test_an_outer_model_replaces_the_HOLD_and_never_the_window(self):
        """``beyond`` answers only past the calendar, and only when funded.

        ``/retirement`` supplies its merit-horizon salary path here so that
        page keeps the long-horizon model it already had.  Inside the
        calendar the engine's own paycheck wins -- an outer model must not
        overwrite a real answer -- and where no funding profile is known the
        refusal stands, because an outer model must not resurrect money the
        2026-09-04 ruling withholds.
        """
        periods = _periods(date(2026, 1, 2))
        beyond_period = _periods(date(2040, 1, 6))[0]
        outer = lambda period: Decimal("9999.99")  # noqa: E731

        funded = _feed(periods, gross=Decimal("3525.96"))
        basis = funded.salary_basis(beyond=outer)
        assert basis(periods[0]) == Decimal("3525.96")
        assert basis(beyond_period) == Decimal("9999.99")

        unfunded = _feed(periods, employee=Decimal("500"))
        assert unfunded.salary_basis(beyond=outer)(beyond_period) is None


class TestCalculateInvestmentInputs:
    """What the two dashboards' per-period CARDS read.

    **This is no longer the forward walk's input** (plan step
    **salary:R14-b**): ``periodic_contribution`` was one raise-blind scalar the
    whole projection ran on, and the walk reads a dated record per period now
    (:func:`build_contribution_timeline`).  What survives here is the current
    paycheck's figure, the two YTD windows and the employer-params shape.

    **The cases that priced a deduction went with the arithmetic.**  Flat
    versus percentage, the half-cent rounding, a two-job owner's per-profile
    salary, a weekly owner's 52 paychecks, and the calendar-year cap's even
    spread were all this module deriving what the paycheck engine derives --
    graded in ``test_paycheck_calculator.py`` against a real profile, where
    the raise and the inflation escalation this module could not see are
    graded too.  The even spread has no successor anywhere and is not meant to
    have one: it was the third of four spellings of one cap, and the engine's
    front-loaded clamp is the one that survives (ruling **R-SAL2**).
    """

    def test_no_feed_no_transfers(self):
        """No payroll feed or transfers -> zero contributions and zero YTD."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        current_period = _periods(date(2026, 3, 5))[0]
        result = calculate_investment_inputs(
            investment_params=params, feed=AccountPayrollFeed.absent(),
            all_contributions=[], current_period=current_period,
        )
        assert result.periodic_contribution == Decimal("0")
        assert result.employer_params is None
        assert result.ytd_contributions == Decimal("0")
        assert result.annual_contribution_limit == Decimal("23500")

    def test_the_card_reads_the_CURRENT_periods_figure(self):
        """The per-period card is the paycheck the owner is being paid.

        It was one figure for all time, so a card rendered in July showed the
        March paycheck's deduction.  The two paydays here differ by the
        developer's own 2026-07 raise applied to a 6% deduction.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        periods = _periods(date(2026, 6, 4), date(2026, 7, 2))
        # 6% of $3,525.96 and of $3,631.74, as the engine rounds each.
        feed = _feed(periods, employee={
            periods[0].start_date: Decimal("211.56"),
            periods[1].start_date: Decimal("217.90"),
        })
        before = calculate_investment_inputs(
            investment_params=params, feed=feed,
            all_contributions=[], current_period=periods[0],
        )
        after = calculate_investment_inputs(
            investment_params=params, feed=feed,
            all_contributions=[], current_period=periods[1],
        )
        assert before.periodic_contribution == Decimal("211.56")
        assert after.periodic_contribution == Decimal("217.90")

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
            investment_params=params, feed=AccountPayrollFeed.absent(),
            all_contributions=contributions, current_period=periods[0],
        )
        # ($200 + $200 + $300) over THREE distinct paydays = $233.33.
        assert result.periodic_contribution == Decimal("233.33")

    def test_employer_flat_percentage(self):
        """Employer flat_percentage populates employer_params with correct values.

        **The dict no longer carries a gross** (plan step salary:R14-b): one
        figure sizing every period's employer contribution is what froze a 5%
        match at today's paycheck for the life of a projection.  The period's
        own comes off the feed, which
        :class:`TestAccountPayrollFeed` grades.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(
                EmployerContributionTypeEnum.FLAT_PERCENTAGE,
            ),
            employer_flat_percentage=Decimal("0.05"),
        )
        current_period = _periods(date(2026, 3, 5))[0]
        feed = _feed([current_period], gross=_GROSS_BIWEEKLY)
        result = calculate_investment_inputs(
            investment_params=params, feed=feed,
            all_contributions=[], current_period=current_period,
        )
        assert result.employer_params is not None
        assert result.employer_params["type_id"] == _emp_type_id(
            EmployerContributionTypeEnum.FLAT_PERCENTAGE,
        )
        assert result.employer_params["flat_percentage"] == Decimal("0.05")
        assert "gross_biweekly" not in result.employer_params

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
        current_period = _periods(date(2026, 3, 5))[0]
        feed = _feed([current_period], gross=_GROSS_BIWEEKLY)
        result = calculate_investment_inputs(
            investment_params=params, feed=feed,
            all_contributions=[], current_period=current_period,
        )
        assert result.employer_params is not None
        assert result.employer_params["type_id"] == _emp_type_id(
            EmployerContributionTypeEnum.MATCH,
        )
        assert result.employer_params["match_percentage"] == Decimal("1.0")
        assert result.employer_params["match_cap_percentage"] == Decimal("0.06")

    def test_a_configured_employer_contribution_is_WITHHELD_when_unfunded(self):
        """No known funding job models no employer money (developer, 2026-09-04).

        The params are configured -- a 5% flat contribution -- and the feed
        priced no gross because ``investment_params.salary_profile_id`` names
        no active profile of this owner's.  The dict is withheld rather than
        paired with a basis of zero, so the growth engine's employer arm
        cannot run at all and the surface can say WHY (the two states stay
        distinguishable: ``employer_params is None`` says no money,
        ``feed.funds_employer`` says which reason).
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(
                EmployerContributionTypeEnum.FLAT_PERCENTAGE,
            ),
            employer_flat_percentage=Decimal("0.05"),
        )
        current_period = _periods(date(2026, 3, 5))[0]
        feed = _feed([current_period], employee=Decimal("500"))
        result = calculate_investment_inputs(
            investment_params=params, feed=feed,
            all_contributions=[], current_period=current_period,
        )
        assert feed.funds_employer is False
        assert result.employer_params is None
        # The EMPLOYEE half is untouched: the ruling is about the employer's
        # money, and the owner's own deduction happened whatever the app knows
        # about which job funds the match.
        assert result.periodic_contribution == Decimal("500")

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
            investment_params=params, feed=AccountPayrollFeed.absent(),
            all_contributions=contributions, current_period=periods[3],
        )
        # The 2025 payday is a different calendar year and 2026-02-13 is past
        # the current period, so three of the five count.
        assert result.ytd_contributions == Decimal("1500")

    def test_ytd_contributions_seed_excludes_current_period(self):
        """deep-hunt #10: the engine seed YTD is STRICTLY BEFORE the current period.

        Same setup as ``test_ytd_contributions_from_transfers``: five $500
        contributions, current = periods[3] (start 2026-01-30).
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
            investment_params=params, feed=AccountPayrollFeed.absent(),
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
            investment_params=params, feed=AccountPayrollFeed.absent(),
            all_contributions=contributions, current_period=None,
        )
        assert result.ytd_contributions_seed == Decimal("0")

    def test_combined_payroll_and_transfers(self):
        """The payroll feed and the transfer average both reach the card."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        periods = _periods(date(2026, 1, 2), date(2026, 1, 16))
        feed = _feed(periods, employee=Decimal("500.00"))
        contributions = [
            _priced(Decimal("200"), periods[0].start_date),
            _priced(Decimal("200"), periods[1].start_date),
        ]
        result = calculate_investment_inputs(
            investment_params=params, feed=feed,
            all_contributions=contributions, current_period=periods[0],
        )
        assert result.periodic_contribution == Decimal("700.00")

    def test_employer_params_stand_without_an_employee_feed(self):
        """An employer FLAT percentage models money with a zero employee feed.

        The real Empower 401(k) shape: no deduction names the account, so the
        employee half is absent, and the employer half is priced off the
        funding profile's own paycheck all the same.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.FLAT_PERCENTAGE),
            employer_flat_percentage=Decimal("0.05"),
        )
        current_period = _periods(date(2026, 3, 5))[0]
        feed = _feed([current_period], gross=_GROSS_BIWEEKLY)
        result = calculate_investment_inputs(
            investment_params=params, feed=feed,
            all_contributions=[], current_period=current_period,
        )
        assert result.employer_params is not None
        assert result.periodic_contribution == Decimal("0")
        assert feed.gross_at(current_period.start_date) == _GROSS_BIWEEKLY

    def test_no_employer_when_type_none(self):
        """Employer type 'none' produces employer_params=None."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        current_period = _periods(date(2026, 3, 5))[0]
        feed = _feed([current_period], gross=_GROSS_BIWEEKLY)
        result = calculate_investment_inputs(
            investment_params=params, feed=feed,
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
            investment_params=params, feed=AccountPayrollFeed.absent(),
            all_contributions=[], current_period=None,
        )
        assert isinstance(result, InvestmentInputs)
        assert result.periodic_contribution == Decimal("0")
        assert result.ytd_contributions == Decimal("0")
        assert result.employer_params is None

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
            investment_params=params, feed=AccountPayrollFeed.absent(),
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
            investment_params=params, feed=AccountPayrollFeed.absent(),
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


class TestBuildContributionTimeline:
    """Tests for build_contribution_timeline().

    Verifies that the function correctly combines the payroll feed and the
    transfer-based contributions into a unified ContributionRecord list,
    with correct amounts, is_confirmed semantics, and sorting.

    **Path 1 computes nothing since plan step salary:R14-b**: it reads the
    feed's per-payday figure, which the paycheck engine priced.  The cases
    that graded its arithmetic -- a flat amount, a percentage of gross, two
    deductions summed, and the whole ``TestBuildContributionTimelineAnnualCap``
    class -- went with it.  The cap is the one to be explicit about: it was a
    private year-state walk here (``_period_capped_total``) reproducing what
    ``paycheck_calculator._calculate_deductions`` applies through the SAME
    ``cap_period_amount``, and ``test_paycheck_calculator.py`` grades that one
    against a real profile whose raise this one could not see.
    """

    def test_payroll_only(self):
        """A payroll feed with no transfers: one record per period."""
        periods = _periods(
            date(2020, 1, 2), date(2020, 1, 16), date(2020, 1, 30),
        )
        result = build_contribution_timeline(
            feed=_feed(periods, employee=Decimal("500.00")),
            contribution_transactions=[], periods=periods, as_of=_AS_OF,
        )
        assert len(result) == 3
        for record in result:
            assert record.amount == Decimal("500.00")
            assert isinstance(record, ContributionRecord)

    def test_a_record_is_emitted_for_every_period_including_a_zero(self):
        """A ``$0`` period is an explicit record, not a missing one.

        The difference is load bearing: a MISSING record is what makes
        ``growth_engine.project_balance`` fall back to
        ``periodic_contribution``, so a cadence-skipped payday emitted as a
        gap would be paid the current period's amount instead of nothing.
        """
        periods = _periods(
            date(2020, 1, 2), date(2020, 1, 16), date(2020, 1, 30),
        )
        feed = _feed(periods, employee={
            periods[0].start_date: Decimal("500"),
            periods[1].start_date: Decimal("500"),
            periods[2].start_date: Decimal("0"),
        })
        result = build_contribution_timeline(
            feed=feed, contribution_transactions=[],
            periods=periods, as_of=_AS_OF,
        )
        assert len(result) == 3
        assert result[2].amount == Decimal("0")

    def test_a_window_with_no_COMPLETE_year_holds_the_last_priced_payday(self):
        """Under a year of schedule there is no annual total to divide.

        **The axis three tail rules were each measured wrong on**, and the
        reason is that a sub-year window has thrown the cap away: a
        ``$500``-a-payday deduction and a ``$1,000``-capped one price
        IDENTICALLY for their first two paydays, and their true tails are
        ``$500`` and ``$38.46``.  With no complete calendar year there is no
        annual figure to derive, so the hold falls back to the last PRICED
        payday -- exact for an uncapped deduction, and for a capped one that
        payday's clamped figure, which can read either way: this fixture's
        trailing ``$0.00`` understates, while a window ending on the ``$400``
        of a ``$600``/``$1,000`` pair annualises to ``$10,400``, 10.4x.

        13 paydays of ``$500``, half a year: the tail holds ``$500``, the
        rate every priced payday shows.
        """
        paydays = [date(2026, 1, 2) + timedelta(days=14 * i) for i in range(13)]
        periods = _periods(*paydays)
        feed = _feed(periods, employee=Decimal("500"))
        assert feed.employee_at(paydays[0]) == Decimal("500")
        assert feed.employee_at(date(2040, 1, 1)) == Decimal("500")

    def test_a_COMPLETE_year_overrides_the_last_priced_payday(self):
        """With a whole year present the annual total wins, cap and all.

        The pair that makes the case above non-vacuous: the same
        ``$1,000``-capped deduction, once over 13 paydays (no complete year,
        so the tail reads the trailing clamped ``$0.00``) and once over 26
        (a complete year, so the tail is ``$1,000 / 26``).  Without both, a
        rule that ignored the complete-year branch entirely would pass.
        """
        short_days = [
            date(2026, 1, 2) + timedelta(days=14 * i) for i in range(13)
        ]
        full_days = [
            date(2026, 1, 2) + timedelta(days=14 * i) for i in range(26)
        ]
        capped = {d: Decimal("0") for d in full_days}
        capped[full_days[0]] = Decimal("600")
        capped[full_days[1]] = Decimal("400")

        short = _feed(
            _periods(*short_days),
            employee={d: capped[d] for d in short_days},
        )
        full = _feed(_periods(*full_days), employee=capped)

        assert short.employee_at(date(2040, 1, 1)) == Decimal("0")
        assert full.employee_at(date(2040, 1, 1)) == Decimal("38.46")

    def test_a_27_PAYDAY_year_is_divided_by_27(self):
        """The divisor is the year's own payday count, not the cadence.

        26 x 14 is 364 days, so a biweekly calendar throws a 27-payday
        calendar year about one year in eleven, and an adversarial pass
        measured 9.07% of default windows having one as their latest
        complete year.  Dividing that year's total by the CADENCE overstates
        every payday of the tail by 3.846% -- ``$519.23`` against a true
        ``$500.00`` -- for the whole ~38-year remainder of a retirement
        chart.  This case is the one the pair above cannot make: both its
        fixtures hold 26.
        """
        paydays = [
            date(2027, 1, 1) + timedelta(days=14 * i) for i in range(27)
        ]
        assert paydays[-1] == date(2027, 12, 31), "27 paydays inside 2027"
        # Flanked on both sides.  The flanks are belt and braces rather than
        # the thing under test: stepping one interval out from 2027-01-01
        # and 2027-12-31 already lands in 2026 and 2028, so this window
        # grades 2027 complete with or without them.
        span = [date(2026, 12, 18)] + paydays + [date(2028, 1, 14)]
        feed = _feed(_periods(*span), employee=Decimal("500"))

        assert feed.employee_at(date(2040, 1, 1)) == Decimal("500.00")

    def test_a_27_PAYDAY_year_seen_26_times_is_NOT_complete(self):
        """A count test grades a year complete that the window truncated.

        ``len(amounts) >= periods_per_year`` passes at ``26 >= 26`` for a
        27-payday year the window opened one payday late.  The year's total
        is then short by exactly the payday that was cut, and for a
        front-loaded capped deduction that payday is where the money is: an
        adversarial pass measured ``$15.38`` held here.

        **The figure being modelled is the ``$1,000`` cap**, which per payday
        is ``$38.46`` in a 26-payday year and ``$37.04`` in a 27-payday one.
        This fixture's 2027 holds 27, so the count test's ``$15.38`` is 58%
        low against that year -- but the hold is a FORWARD rate, and on this
        fixture's own rhythm 40 of the next 43 years hold 26 paydays, so
        ``$38.46`` is the better description of what it should model.  *An
        earlier revision of this docstring called ``$37.04`` "the truth"; an
        adversarial pass measured that it is this rule's own output on the
        untruncated window, which grades a fix against its own producer.*

        **And the refusal this asserts is further from truth still**, at
        ``$0.00``.  That is the trade taken deliberately: the count test was
        wrong about WHICH years it could average, and this rule is not.  It
        is NOT the case that refusing only ever lowers the figure: extend
        this window by one payday, to 2028-01-14, and the fallback reads that
        payday and holds ``$600`` where the count test holds ``$38.46``.  An
        earlier revision of this docstring claimed the opposite and used it
        as a warrant; an adversarial pass measured it false.  The salary-path
        step removes the branch rather than bounding it.
        """
        full_year = [
            date(2027, 1, 1) + timedelta(days=14 * i) for i in range(27)
        ]
        observed = full_year[1:]          # opened one payday late
        # $600 then $400 against a $1,000 cap: the cut payday paid $600.
        amounts = {day: Decimal("0") for day in observed}
        amounts[observed[0]] = Decimal("400")
        feed = _feed(_periods(*observed), employee=amounts)

        assert full_year[-1] == date(2027, 12, 31), (
            "the 27th payday must still be inside 2027 -- asserting "
            "len(full_year) instead would be true by construction and would "
            "pass for an anchor whose year holds 16"
        )
        assert feed._complete_years() == set()
        # The fallback, not 400/26 == $15.38 dressed as a year's average.
        assert feed.employee_at(date(2040, 1, 1)) == Decimal("0")

    def test_a_WEEKLY_owner_covering_a_year_gets_that_year(self):
        """Coverage, at a cadence whose count test fails differently.

        A weekly year holds 52 paydays or 53, so ``>= periods_per_year`` is
        wrong at cadence 7 the same way it is at 14 -- and this class had no
        non-biweekly case at all, which let a rule that hardcoded 26 look
        correct.  Both halves here are the weekly analogue of the biweekly
        pair above.
        """
        # (a) A 52-payday 2026 fully covered: the exact $1,000 / 52.
        paydays = [
            date(2025, 12, 29) + timedelta(days=7 * i) for i in range(60)
        ]
        in_2026 = [day for day in paydays if day.year == 2026]
        assert len(in_2026) == 52
        amounts = {day: Decimal("0") for day in paydays}
        for day in in_2026[:10]:
            amounts[day] = Decimal("100")     # $1,000, cap reached early

        feed = _feed(_periods(*paydays, cadence=7), employee=amounts)

        assert 2026 in feed._complete_years()
        assert feed.employee_at(date(2040, 1, 1)) == Decimal("19.23")

        # (b) A 53-payday 2026 seen 52 times: a count test passes it at
        # 52 >= 52 and averages a year it never saw the whole of.  This is
        # the half the biweekly 27-seen-26 case cannot reach, and the half
        # that fails if the completeness rule reverts to a count.
        long_year = [
            date(2026, 1, 1) + timedelta(days=7 * i) for i in range(53)
        ]
        assert len({day.year for day in long_year}) == 1
        assert len(long_year) == 53, "2026 holds 53 weekly paydays"
        # Price one payday, so the two rules differ by a DOLLAR and not just
        # by a predicate: a count test grades 2026 complete at 52 >= 52 and
        # averages $100 / 52 == $1.92, where refusing falls back to the last
        # priced payday, which is $0.00.
        truncated = long_year[1:]
        amounts = {day: Decimal("0") for day in truncated}
        amounts[truncated[0]] = Decimal("100")
        short = _feed(_periods(*truncated, cadence=7), employee=amounts)

        assert short._complete_years() == set()
        assert short.employee_at(date(2040, 1, 1)) == Decimal("0")

    def test_a_period_PAST_the_calendar_reads_the_held_figure(self):
        """The timeline's domain may run past the owner's saved schedule.

        The 40-year chart's axis does, which is why the timeline is assembled
        where the axis is known (plan step salary:R14-b).  Every projected
        period gets a record, so the raise-blind ``periodic_contribution``
        fallback the step deleted has nothing left to answer.
        """
        # A COMPLETE calendar year priced, so the hold has a figure at all
        # (see the sibling case that grades a window with no complete
        # year), then
        # one axis period past it.
        paydays = [date(2020, 1, 3) + timedelta(days=14 * i) for i in range(26)]
        priced = _periods(*paydays)
        feed = _feed(priced, employee=Decimal("500.00"))
        beyond = paydays[-1] + timedelta(days=14)
        axis = _periods(*paydays, beyond)
        result = build_contribution_timeline(
            feed=feed, contribution_transactions=[],
            periods=axis, as_of=_AS_OF,
        )
        assert len(result) == 27
        assert result[26].contribution_date == beyond
        # 26 x $500.00 over the year's 26 paydays.
        assert result[26].amount == Decimal("500.00")

    def test_transfer_only(self):
        """Shadow income transactions with no payroll: one record per transaction."""
        periods = _periods(date(2020, 1, 2), date(2020, 1, 16))
        txns = [
            _priced(Decimal("200"), periods[0].start_date, is_confirmed=True),
            _priced(Decimal("300"), periods[1].start_date, is_confirmed=True),
        ]
        result = build_contribution_timeline(
            feed=AccountPayrollFeed.absent(), contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        assert len(result) == 2
        assert result[0].amount == Decimal("200")
        assert result[1].amount == Decimal("300")

    def test_an_UNLINKED_account_emits_NO_path_one_records(self):
        """No deduction wired up: the fallback stays the answer.

        ``is_payroll_linked`` is the gate, and it reads ``False`` here, so the
        transfer average answers a period with no transfer -- which is the
        behaviour an account funded only by transfers relies on.
        """
        periods = _periods(date(2020, 1, 2), date(2020, 1, 16))
        result = build_contribution_timeline(
            feed=_feed(periods, employee=Decimal("0"), linked=False),
            contribution_transactions=[], periods=periods, as_of=_AS_OF,
        )
        assert result == []

    def test_a_LINKED_feed_pricing_zero_still_emits_its_zeros(self):
        """A wired-up deduction that prices $0.00 suppresses the fallback.

        The gate is PRESENCE and not price, which an adversarial review of
        this step corrected.  A deduction fully consumed by its ``annual_cap``
        across the whole priced window prices ``$0.00`` on every payday while
        being genuinely configured; gating on the price emitted no records,
        and the engine then applied the TRANSFER AVERAGE to periods that
        should contribute nothing.  The explicit zeros are what stop it.
        """
        periods = _periods(date(2020, 1, 2), date(2020, 1, 16))
        feed = AccountPayrollFeed(
            employee_by_payday={p.start_date: Decimal("0") for p in periods},
            gross_by_payday={},
            is_payroll_linked=True,
        )
        result = build_contribution_timeline(
            feed=feed,
            contribution_transactions=[
                _priced(Decimal("300"), periods[0].start_date),
            ],
            periods=periods, as_of=_AS_OF,
        )
        payroll = [r for r in result if r.amount == Decimal("0")]
        assert len(payroll) == 2, (
            "both priced paydays must carry an explicit $0 record"
        )

    def test_both_paths_summed(self):
        """Payroll and transfer on the same period produce separate records.

        The growth engine's lookup dict aggregates same-date records.
        $500 payroll + $200 transfer on period 1.
        """
        periods = _periods(date(2020, 1, 2))
        txns = [_priced(Decimal("200"), periods[0].start_date, is_confirmed=True)]
        result = build_contribution_timeline(
            feed=_feed(periods, employee=Decimal("500.00")),
            contribution_transactions=txns, periods=periods, as_of=_AS_OF,
        )
        # One record from payroll, one from the transfer, same date.
        assert len(result) == 2
        total = sum(r.amount for r in result)
        assert total == Decimal("700.00")

    def test_is_confirmed_payroll_past(self):
        """Payroll for a past period: is_confirmed=True."""
        # Before the pass's clock, by a literal rather than by when this runs.
        periods = _periods(date(2020, 1, 2))
        result = build_contribution_timeline(
            feed=_feed(periods, employee=Decimal("500")),
            contribution_transactions=[], periods=periods, as_of=_AS_OF,
        )
        assert result[0].is_confirmed is True

    def test_is_confirmed_payroll_future(self):
        """Payroll for a future period: is_confirmed=False."""
        # After the pass's clock, by a literal rather than by when this runs.
        periods = _periods(date(2099, 1, 2))
        result = build_contribution_timeline(
            feed=_feed(periods, employee=Decimal("500")),
            contribution_transactions=[], periods=periods, as_of=_AS_OF,
        )
        assert result[0].is_confirmed is False

    def test_is_confirmed_transfer_settled(self):
        """Settled shadow transaction: is_confirmed=True."""
        periods = _periods(date(2020, 1, 2))
        txns = [_priced(Decimal("200"), periods[0].start_date, is_confirmed=True)]
        result = build_contribution_timeline(
            feed=AccountPayrollFeed.absent(), contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        assert result[0].is_confirmed is True

    def test_is_confirmed_transfer_projected(self):
        """Projected shadow transaction: is_confirmed=False."""
        periods = _periods(date(2020, 1, 2))
        txns = [_priced(Decimal("200"), periods[0].start_date, is_confirmed=False)]
        result = build_contribution_timeline(
            feed=AccountPayrollFeed.absent(), contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        assert result[0].is_confirmed is False

    def test_is_confirmed_mixed_same_date(self):
        """Confirmed payroll + projected transfer on same date.

        Both produce records for the same date.  The growth engine's
        lookup dict applies the conservative rule (all must be confirmed).
        Here we verify both records are produced -- one True, one False.
        """
        # Past date so the payroll record is confirmed.
        periods = _periods(date(2020, 1, 2))
        txns = [_priced(Decimal("200"), periods[0].start_date, is_confirmed=False)]
        result = build_contribution_timeline(
            feed=_feed(periods, employee=Decimal("500")),
            contribution_transactions=txns, periods=periods, as_of=_AS_OF,
        )
        assert len(result) == 2
        confirmed_flags = {r.is_confirmed for r in result}
        assert True in confirmed_flags   # Payroll (past).
        assert False in confirmed_flags  # Transfer (projected).

    def test_empty_both(self):
        """No payroll feed and no transactions: empty list returned."""
        periods = _periods(date(2020, 1, 2))
        result = build_contribution_timeline(
            feed=AccountPayrollFeed.absent(), contribution_transactions=[],
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
            feed=AccountPayrollFeed.absent(), contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        dates = [r.contribution_date for r in result]
        assert dates == sorted(dates)

    def test_emits_the_record_amount_untransformed(self):
        """A record's priced amount reaches its ContributionRecord unchanged.

        Renamed at plan step X-au-c2: it graded ``effective_amount``, an
        accessor this module no longer touches.  What it can still pin is that
        the timeline does not round, scale or re-derive the figure the boundary
        priced -- and since plan step salary:R14-b that is true of BOTH paths,
        because path 1 stopped transforming anything too.
        """
        periods = _periods(date(2020, 1, 2))
        txns = [_priced(Decimal("999.99"), periods[0].start_date, is_confirmed=True)]
        result = build_contribution_timeline(
            feed=AccountPayrollFeed.absent(), contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        assert result[0].amount == Decimal("999.99")

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
            feed=AccountPayrollFeed.absent(), contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        assert result == []
