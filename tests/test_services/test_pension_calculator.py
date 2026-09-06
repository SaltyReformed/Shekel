"""
Shekel Budget App -- Unit Tests for Pension Calculator

Tests the pension benefit calculation including years of service,
high-salary average computation, and salary projection integration.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.pension_calculator import (
    PensionBenefit,
    calculate_benefit,
    project_salaries_by_year,
    _calculate_years_of_service,
    _compute_high_salary_average,
    ZERO,
)


# ── Fake Objects ─────────────────────────────────────────────────


class FakeRaise:
    """A raise-shaped value carrying exactly what the walk reads.

    **It has no raise TYPE, and the absence is the point** (plan step
    salary:S3-c, ruling R-SAL11).  It carried a ``raise_type_id`` resolved
    through the ref cache while the projection discriminated cola-type
    raises from merit and custom ones to decide which the merit horizon
    stopped.  Nothing discriminates now -- every raise stops at its own
    stored ``terminal_year`` -- so a type on this double would assert a
    distinction the producer can no longer make, which is how a test starts
    describing a rule that is not there.
    """

    def __init__(self, percentage=None, flat_amount=None,
                 effective_month=3, effective_year=2026,
                 is_recurring=False, terminal_year=None):
        self.percentage = Decimal(str(percentage)) if percentage else None
        self.flat_amount = Decimal(str(flat_amount)) if flat_amount else None
        self.effective_month = effective_month
        self.effective_year = effective_year
        self.is_recurring = is_recurring
        #: The last year this raise is believed to happen, ``None`` for
        #: indefinitely.  Read directly by ``salary_raises._applications``.
        self.terminal_year = terminal_year


# ── Tests ────────────────────────────────────────────────────────


class TestCalculateBenefit:
    def test_basic_benefit(self):
        """multiplier * years * average = expected benefit."""
        salary_by_year = [
            (2040, Decimal("80000")),
            (2041, Decimal("82000")),
            (2042, Decimal("84000")),
            (2043, Decimal("86000")),
        ]
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=4,
            hire_date=date(2018, 7, 1),
            planned_retirement_date=date(2043, 7, 1),
            salary_by_year=salary_by_year,
        )
        assert result.years_of_service == Decimal("25.00")
        assert result.high_salary_average == Decimal("83000.00")
        # 0.0185 * 25 * 83000 = 38387.50
        assert result.annual_benefit == Decimal("38387.50")
        assert result.monthly_benefit == Decimal("3198.96")

    def test_high_salary_average_correct_window(self):
        """Highest consecutive window selected."""
        salary_by_year = [
            (2035, Decimal("60000")),
            (2036, Decimal("70000")),
            (2037, Decimal("80000")),
            (2038, Decimal("90000")),
            (2039, Decimal("85000")),
        ]
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=3,
            hire_date=date(2010, 1, 1),
            planned_retirement_date=date(2040, 1, 1),
            salary_by_year=salary_by_year,
        )
        # Best 3-year window: 2037-2039 = (80000+90000+85000)/3 = 85000
        assert result.high_salary_average == Decimal("85000.00")

    def test_fewer_years_than_window(self):
        """Less data than window uses all available."""
        salary_by_year = [
            (2040, Decimal("80000")),
            (2041, Decimal("85000")),
        ]
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=4,
            hire_date=date(2030, 1, 1),
            planned_retirement_date=date(2042, 1, 1),
            salary_by_year=salary_by_year,
        )
        assert result.high_salary_average == Decimal("82500.00")

    def test_empty_salary_projections(self):
        """No salary data returns zero benefit."""
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=4,
            hire_date=date(2020, 1, 1),
            planned_retirement_date=date(2045, 1, 1),
            salary_by_year=[],
        )
        assert result.annual_benefit == ZERO
        assert result.monthly_benefit == ZERO

    def test_monthly_is_annual_divided_by_12(self):
        """Monthly benefit = annual / 12."""
        salary_by_year = [(2040, Decimal("100000"))]
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.02"),
            consecutive_high_years=1,
            hire_date=date(2020, 1, 1),
            planned_retirement_date=date(2040, 1, 1),
            salary_by_year=salary_by_year,
        )
        expected_annual = Decimal("0.02") * Decimal("20.00") * Decimal("100000")
        expected_monthly = (expected_annual / 12).quantize(Decimal("0.01"))
        assert result.monthly_benefit == expected_monthly

    def test_very_short_service(self):
        """Less than 1 year of service.

        days = (2026-06-01 - 2026-01-01) = 151
        years = (151 / 365.25).quantize(0.01) = 0.41
        high_salary_avg = 80000.00 (window min(4,1)=1)
        annual = 0.0185 * 0.41 * 80000 = 606.80
        monthly = 606.80 / 12 = 50.57
        """
        salary_by_year = [(2026, Decimal("80000"))]
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=4,
            hire_date=date(2026, 1, 1),
            planned_retirement_date=date(2026, 6, 1),
            salary_by_year=salary_by_year,
        )
        assert result.years_of_service == Decimal("0.41"), (
            f"Expected 0.41 years, got {result.years_of_service}"
        )
        assert result.annual_benefit == Decimal("606.80"), (
            f"Expected annual 606.80, got {result.annual_benefit}"
        )
        assert result.monthly_benefit == Decimal("50.57"), (
            f"Expected monthly 50.57, got {result.monthly_benefit}"
        )


class TestYearsOfService:
    def test_exact_years(self):
        result = _calculate_years_of_service(date(2000, 1, 1), date(2025, 1, 1))
        assert result == Decimal("25.00")

    def test_zero_service(self):
        result = _calculate_years_of_service(date(2026, 1, 1), date(2026, 1, 1))
        assert result == ZERO

    def test_negative_service(self):
        result = _calculate_years_of_service(date(2026, 1, 1), date(2025, 1, 1))
        assert result == ZERO

    def test_none_dates(self):
        assert _calculate_years_of_service(None, date(2040, 1, 1)) == ZERO
        assert _calculate_years_of_service(date(2020, 1, 1), None) == ZERO


class TestHighSalaryAverage:
    def test_single_year(self):
        avg, window = _compute_high_salary_average(
            [(2040, Decimal("80000"))], 1
        )
        assert avg == Decimal("80000.00")

    def test_highest_at_end(self):
        data = [
            (2036, Decimal("60000")),
            (2037, Decimal("70000")),
            (2038, Decimal("80000")),
            (2039, Decimal("90000")),
        ]
        avg, window = _compute_high_salary_average(data, 2)
        # Best 2-year: 2038-2039 = (80000+90000)/2 = 85000
        assert avg == Decimal("85000.00")
        assert len(window) == 2


class TestProjectSalariesByYear:
    def test_no_raises(self):
        result = project_salaries_by_year(Decimal("80000"), [], 2026, 2028)
        assert len(result) == 3
        for year, salary in result:
            assert salary == Decimal("80000.00")

    def test_with_recurring_raise(self):
        """Recurring 3% raise with no end year compounds each year.

        Each year is evaluated at December 1, so month >= effective_month=3
        always applies.
        2026: 1 application  -> 80000 * 1.03   = 82400.00
        2027: 2 applications -> 80000 * 1.03^2 = 84872.00
        2028: 3 applications -> 80000 * 1.03^3 = 87418.16
        """
        raises = [
            FakeRaise(percentage="0.03", effective_month=3,
                      effective_year=2026, is_recurring=True),
        ]
        result = project_salaries_by_year(
            Decimal("80000"), raises, 2026, 2028,
        )
        # 80000 * 1.03 = 82400.00
        assert result[0][1] == Decimal("82400.00"), (
            f"2026 salary: expected 82400.00, got {result[0][1]}"
        )
        # 80000 * 1.03^2 = 84872.00
        assert result[1][1] == Decimal("84872.00"), (
            f"2027 salary: expected 84872.00, got {result[1][1]}"
        )
        # 80000 * 1.03^3 = 87418.16
        assert result[2][1] == Decimal("87418.16"), (
            f"2028 salary: expected 87418.16, got {result[2][1]}"
        )

    def test_recurring_raise_highest_years_near_retirement(self):
        """A raise with no end year extrapolates to retirement.

        A 2.5% recurring raise believed indefinitely from 2026 to 2046
        makes the last 4 years the highest, which is what the high-salary
        window must then select.
        """
        raises = [
            FakeRaise(percentage="0.025", effective_month=1,
                      effective_year=2026, is_recurring=True),
        ]
        salary_by_year = project_salaries_by_year(
            Decimal("90000"), raises, 2026, 2046,
        )
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=4,
            hire_date=date(2006, 6, 1),
            planned_retirement_date=date(2046, 6, 1),
            salary_by_year=salary_by_year,
        )
        high_years = [y for y, _ in result.high_salary_years]
        # The 4 highest consecutive salary years must be the last 4
        assert high_years == [2043, 2044, 2045, 2046], (
            f"Expected highest years near retirement, got {high_years}"
        )


class TestTheEndYearOnEachRaise:
    """How long each raise is believed, read off the raise (**R-SAL11**).

    **Translated from ``TestMeritHorizon`` at plan step salary:S3-c, and
    every figure below is the one that class asserted.**  That is the
    evidence rather than a convenience: ``pension_calculator
    ._terminate_after_horizon`` did nothing but ASSIGN a terminal year --
    ``None`` for a recurring cola, ``start_year + N`` for everything else --
    so stating the same terminal year on the raise itself has to reproduce
    the same walk to the cent.  Where a case's answer genuinely MOVES, it is
    the one about a one-time raise, and that test states its own before and
    after.

    Each raise evaluates at December 1, so the effective month never gates
    the December-of-year application.
    """

    def test_a_raise_with_an_end_year_stops_while_one_without_continues(self):
        """Two 10% raises, one ending 2028 and one believed indefinitely.

        base 100,000; both recurring from 2026, one effective January and
        ending 2028, the other effective July with no end year.  Through
        2028 both apply once per year, so by year Y the salary is
        100000 * 1.10^(2*(Y-2025)):
          2026: 100000 * 1.10^2 = 121,000.00
          2028 (the end year): 100000 * 1.10^6 = 177,156.10
        After it the ended raise contributes nothing further and only the
        other compounds from the 2028 salary:
          2029: 177,156.10 * 1.10   = 194,871.71  (NOT 100000*1.10^8 =
                                       214,358.88, which is both-continue)
          2031: 177,156.10 * 1.10^3 = 235,794.77
        """
        raises = [
            FakeRaise(percentage="0.10", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      terminal_year=2028),
            FakeRaise(percentage="0.10", effective_month=7,
                      effective_year=2026, is_recurring=True),
        ]
        result = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2031,
        ))
        # 100000 * 1.10 * 1.10 = 121000.00
        assert result[2026] == Decimal("121000.00")
        # 100000 * 1.10^6 = 177156.10 (both raises still applying)
        assert result[2028] == Decimal("177156.10")
        # 177156.10 * 1.10 = 194871.71 (the ended raise contributes nothing)
        assert result[2029] == Decimal("194871.71")
        # 177156.10 * 1.10^3 = 235794.77
        assert result[2031] == Decimal("235794.77")

    def test_a_raise_with_no_end_year_compounds_uninterrupted(self):
        """A raise believed indefinitely drops and repeats no occurrence.

        base 100,000; 10% (July) recurring from 2026 with no end year:
          2026: 100000 * 1.10   = 110,000.00
          2028: 100000 * 1.10^3 = 133,100.00
          2029: 100000 * 1.10^4 = 146,410.00
          2030: 100000 * 1.10^5 = 161,051.00
        """
        raises = [
            FakeRaise(percentage="0.10", effective_month=7,
                      effective_year=2026, is_recurring=True),
        ]
        result = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2030,
        ))
        assert result[2026] == Decimal("110000.00")   # 100000 * 1.10
        assert result[2028] == Decimal("133100.00")   # 100000 * 1.10^3
        assert result[2029] == Decimal("146410.00")   # 100000 * 1.10^4
        assert result[2030] == Decimal("161051.00")   # 100000 * 1.10^5

    def test_the_salary_plateaus_after_the_only_raises_end_year(self):
        """One 10% raise ending 2028, and nothing else moves the salary.

        base 100,000; recurring from 2026, ending 2028:
          2026: 100000 * 1.10   = 110,000.00
          2028: 100000 * 1.10^3 = 133,100.00
          2029: 133,100.00 (nothing applies)
          2030: 133,100.00 (nothing applies)
        """
        raises = [
            FakeRaise(percentage="0.10", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      terminal_year=2028),
        ]
        result = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2030,
        ))
        assert result[2026] == Decimal("110000.00")   # 100000 * 1.10
        assert result[2028] == Decimal("133100.00")   # 100000 * 1.10^3
        assert result[2029] == Decimal("133100.00")   # plateau
        assert result[2030] == Decimal("133100.00")   # plateau

    def test_a_future_scheduled_raise_is_not_pulled_before_its_start(self):
        """A raise effective 2031 first applies in ITS year, never earlier (H1).

        base 100,000; a 10% recurring raise effective 2031, no end year:
          2026-2030: 100,000.00  (it does not exist yet)
          2031: 100,000 * 1.10   = 110,000.00  (first application)
          2032: 100,000 * 1.10^2 = 121,000.00

        H1 was the defect this guards: the merit horizon this step deleted
        expressed itself by RE-ANCHORING a raise's effective year past a
        cutoff so a second compounding pass counted only the occurrences
        beyond it, and a plain reset pulled this 2031 raise back to 2029 --
        110,000.00 in 2029 and 146,410.00 by 2032.  It needed a
        ``max(own, anchor)`` floor to stop that.  Nothing has moved an
        effective year since plan step salary:S3-a, so the floor has no
        subject; the case is kept because a future implementation could
        reintroduce one.
        """
        raises = [
            FakeRaise(percentage="0.10", effective_month=7,
                      effective_year=2031, is_recurring=True),
        ]
        result = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2032,
        ))
        assert result[2028] == Decimal("100000.00")  # not live yet
        assert result[2029] == Decimal("100000.00")  # NOT 110,000 (H1 bug)
        assert result[2030] == Decimal("100000.00")
        assert result[2031] == Decimal("110000.00")  # 100000 * 1.10
        assert result[2032] == Decimal("121000.00")  # 100000 * 1.10^2

    def test_mixed_flat_and_percentage_raises_walk_chronologically(self):
        """Flat and percentage raises compound in the order the money arrives.

        base 100,000; a flat $1,000 recurring raise + a 10% recurring one,
        both effective 2026 and neither ending.  ``apply_raises`` applies
        each APPLICATION on the date it lands, flat before percentage
        within a date (M-01):

          2026: (100,000 + 1,000) * 1.10 = 111,100.00
          2027: (111,100 + 1,000) * 1.10 = 123,310.00
          2028: (123,310 + 1,000) * 1.10 = 136,741.00
          2029: (136,741 + 1,000) * 1.10 = 151,515.10
          2030: (151,515.10 + 1,000) * 1.10 = 167,766.61

        The two VALUE pins are what this grades: revert the chronological
        walk and they fail -- by 352.00 at 2028 and 1,336.94 at 2030.

        **It used to assert an INVARIANCE as well**, running the same
        raises at ``merit_horizon_years`` 10 and 2 and asserting the two
        answered identically -- which graded that a recurring cola was not
        given a terminal year.  Plan step salary:S3-c deleted the parameter
        and the type test with it, so both calls became the same call and
        the assertion became a tautology.  It is dropped rather than
        rewritten: the direction it half-covered -- that a raise WITH an end
        year really stops -- is graded by
        ``test_a_raise_with_an_end_year_stops_while_one_without_continues``
        and ``test_the_salary_plateaus_after_the_only_raises_end_year``,
        each of which a no-op end year fails.
        """
        raises = [
            FakeRaise(flat_amount="1000", effective_month=1,
                      effective_year=2026, is_recurring=True),
            FakeRaise(percentage="0.10", effective_month=1,
                      effective_year=2026, is_recurring=True),
        ]
        result = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2030,
        ))
        # Third year of the walk above.
        assert result[2028] == Decimal("136741.00")
        # Fifth year of the walk above.
        assert result[2030] == Decimal("167766.61")

    def test_a_one_time_raise_dated_late_now_actually_happens(self):
        """The behaviour change: a late one-time raise is no longer dropped.

        **This is the one figure in this class that MOVES**, and it moves
        because measurement 3 of ruling **R-SAL11** is the defect it names.
        Under the deleted merit horizon every one-time raise was handed the
        global cutoff as its terminal year, so a promotion the owner had
        recorded for a year past it applied ZERO times and the projection
        silently never showed it.  A one-time raise carries no end year at
        all now (``ck_salary_raises_terminal_year_only_on_a_recurring_
        raise``), so a recorded raise happens.

        Three runs against the same 3% January recurring raise, base
        100,000, asked at 2035:

          the recurring raise alone            -> 134,391.64
          + one-time $2,000 in 2028            -> 136,851.39   (unchanged)
          + one-time $2,000 in 2035            -> 136,391.64   (WAS
                                                  134,391.64: dropped)

        The 2028 one is unchanged because it always fell inside the old
        cutoff of 2031, and it is kept here as the control: a build that
        dropped every one-time raise, or one that applied the late one
        twice, fails one of the three.
        """
        recurring = FakeRaise(percentage="0.03", effective_month=1,
                              effective_year=2026, is_recurring=True)
        within = FakeRaise(flat_amount="2000", effective_month=5,
                           effective_year=2028, is_recurring=False)
        late = FakeRaise(flat_amount="2000", effective_month=5,
                         effective_year=2035, is_recurring=False)
        base = Decimal("100000")
        alone = dict(project_salaries_by_year(base, [recurring], 2026, 2035))
        with_within = dict(project_salaries_by_year(
            base, [recurring, within], 2026, 2035))
        with_late = dict(project_salaries_by_year(
            base, [recurring, late], 2026, 2035))

        # 100000 * 1.03^10
        assert alone[2035] == Decimal("134391.64")
        # It lands in May 2035, after that year's January application, and
        # nothing follows it: 134,391.6379... + 2,000.
        assert with_late[2035] == Decimal("136391.64")
        assert with_late[2035] > alone[2035]
        # ((100000 * 1.03^3) + 2000) * 1.03^7 -- seven later applications
        # compound it, which is why it answers more than the late one.
        assert with_within[2035] == Decimal("136851.39")
        assert with_within[2035] > with_late[2035]

    def test_an_ended_raise_does_not_compound_later_flat_dollars(self):
        """A raise that stopped in 2031 must not grow 2040's flat money.

        The regression that parked the first attempt at plan step S4.  A
        recurring flat $1,500 raise with no end year and a recurring 4% one
        ending 2031, base 100,000.  Once the 4% raise ends, each later year
        may only add $1,500 -- its multiplier has no claim on money that
        arrives after it stopped:

          2031: 136,879.34
          2032: 138,379.34   (+1,500.00)
          2033: 139,879.34   (+1,500.00)
          2040: 150,379.34   (+1,500.00 a year, seven more times)

        Applying the ended raise to those additions instead gives
        ``1,500 * 1.04^6 = 1,897.98`` a year, which is what that attempt
        produced, because it removed the two-phase split while the walk
        still grouped applications by raise.  It answered ``155,001.58`` at
        2040 against the ``150,379.34`` asserted here -- **+4,622.24** --
        and 1,040.43 of the gap is already present at 2031 itself
        (137,919.77 against 136,879.34), so the divergence was never purely
        post-cutoff.  It reads correctly here only because the walk orders
        by date, so this case pins BOTH rules at once.
        """
        flat = FakeRaise(flat_amount="1500", effective_month=1,
                         effective_year=2026, is_recurring=True)
        ending = FakeRaise(percentage="0.04", effective_month=1,
                           effective_year=2026, is_recurring=True,
                           terminal_year=2031)
        result = dict(project_salaries_by_year(
            Decimal("100000"), [flat, ending], 2026, 2040,
        ))
        assert result[2031] == Decimal("136879.34")
        assert result[2032] == Decimal("138379.34")
        assert result[2033] == Decimal("139879.34")
        assert result[2040] == Decimal("150379.34")
        # Stated as the increment, because that is the defect's shape.
        assert result[2032] - result[2031] == Decimal("1500.00")
        assert result[2033] - result[2032] == Decimal("1500.00")

    def test_real_shaped_pair_one_ending_and_one_not(self):
        """3% July raise with no end year + 2.5% January one ending 2031.

        base 100,000; both recurring from 2026; projected 2026..2035.  The
        2.5% raise applies six times (2026..2031); the 3% one is never
        believed to stop and applies once per year.  Every expected value
        is therefore ``100,000 * 1.025^6 * 1.03^k`` with k the count of 3%
        applications, computed independently of the producer:

          2026: 1.025^1 * 1.03^1  -> 105,575.00
          2031: 1.025^6 * 1.03^6  -> 138,473.46   (its last year)
          2032: 1.025^6 * 1.03^7  -> 142,627.66
          2033: 1.025^6 * 1.03^8  -> 146,906.49
          2035: 1.025^6 * 1.03^10 -> 155,853.10

        **The oracle is absolute, not relative.**  This test used to assert
        ``result[2032] == round_money(result[2031] * 1.03)`` -- it read a
        year back out of the producer and re-compounded it, and an oracle
        built from the value under test cannot catch an error that moves
        both years together.
        """
        raises = [
            FakeRaise(percentage="0.03", effective_month=7,
                      effective_year=2026, is_recurring=True),
            FakeRaise(percentage="0.025", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      terminal_year=2031),
        ]
        result = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2035,
        ))
        # 100000 * 1.025 * 1.03 = 105575.00 (both apply)
        assert result[2026] == Decimal("105575.00")
        # Its last believed year: six of each.
        assert result[2031] == Decimal("138473.46")
        # Past it the 2.5% exponent STAYS at 6 while the 3%'s climbs.
        assert result[2032] == Decimal("142627.66")
        assert result[2033] == Decimal("146906.49")
        assert result[2035] == Decimal("155853.10")
        # And it is genuinely stopped: had it kept applying, 2032 would be
        # 1.025^7 * 1.03^7 = 146,193.35, which is strictly more.
        assert result[2032] < Decimal("146193.35")
