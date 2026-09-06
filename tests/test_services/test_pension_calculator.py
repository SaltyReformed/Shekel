"""
Shekel Budget App -- Unit Tests for Pension Calculator

Tests the pension benefit calculation including years of service,
high-salary average computation, and salary projection integration.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import RaiseTypeEnum
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
    def __init__(self, percentage=None, flat_amount=None,
                 effective_month=3, effective_year=2026,
                 is_recurring=False, raise_type=RaiseTypeEnum.MERIT):
        self.percentage = Decimal(str(percentage)) if percentage else None
        self.flat_amount = Decimal(str(flat_amount)) if flat_amount else None
        self.effective_month = effective_month
        self.effective_year = effective_year
        self.is_recurring = is_recurring
        # The merit horizon discriminates recurring cola raises from
        # merit/custom raises by ``raise_type_id`` (never the name string);
        # resolve the id at construction (ref_cache is initialised by the
        # autouse conftest fixtures).
        self.raise_type_id = ref_cache.raise_type_id(raise_type)

        class _FakeType:
            name = raise_type.value
        self.raise_type = _FakeType()


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
        # No raises: the merit horizon is irrelevant (nothing to freeze).
        result = project_salaries_by_year(
            Decimal("80000"), [], 2026, 2028, 5,
        )
        assert len(result) == 3
        for year, salary in result:
            assert salary == Decimal("80000.00")

    def test_with_recurring_raise(self):
        """Recurring 3% raise compounds each year (horizon does not bite).

        FakePeriod evaluates at month=12, so month >= effective_month=3
        always applies.  merit_horizon_years=5 -> cutoff 2026+5 = 2031,
        which is past the 2028 end year, so every year is <= cutoff and
        all raises apply exactly as before the horizon existed.
        2026: 1 application  -> 80000 * 1.03   = 82400.00
        2027: 2 applications -> 80000 * 1.03^2 = 84872.00
        2028: 3 applications -> 80000 * 1.03^3 = 87418.16
        """
        raises = [
            FakeRaise(percentage="0.03", effective_month=3,
                      effective_year=2026, is_recurring=True),
        ]
        result = project_salaries_by_year(
            Decimal("80000"), raises, 2026, 2028, 5,
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

    def test_recurring_cola_raise_highest_years_near_retirement(self):
        """A recurring COLA raise extrapolates to retirement (highest years last).

        A cola-type recurring raise keeps compounding past the merit
        cutoff, so a 2.5% cola from 2026 to 2046 still makes the last 4
        years the highest (unchanged from the pre-horizon behaviour for a
        cola raise).  merit_horizon_years=5 (cutoff 2031) does not stop a
        cola raise, so the salary rises every year through 2046.
        """
        raises = [
            FakeRaise(percentage="0.025", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.COLA),
        ]
        salary_by_year = project_salaries_by_year(
            Decimal("90000"), raises, 2026, 2046, 5,
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


class TestMeritHorizon:
    """The merit-horizon behaviour (Gate A ruling 3 / fork F4).

    Through the cutoff year (start_year + N) every raise applies; after
    the cutoff only recurring cola-type raises keep compounding, and the
    merit/custom effect earned through the cutoff persists in the salary.
    Cola discrimination is by ``raise_type_id``.  All raises evaluate at
    December 1, so the effective month never gates the December-of-year
    application.

    The horizon is a per-raise ``terminal_year`` applied in one walk;
    there is no cutoff salary and no second compounding pass.
    """

    def test_merit_freezes_after_cutoff_cola_continues(self):
        """Merit + cola through cutoff; only cola compounds after it.

        base 100,000; merit 10% (Jan) + cola 10% (July), both recurring
        from 2026; start 2026, end 2031, N=2 -> cutoff = 2028.
        Through the cutoff both apply once per year, so by year Y the
        salary is 100000 * 1.10^(2*(Y-2025)) (merit and cola each applied
        Y-2025 times):
          2026: 100000 * 1.10^2 = 121,000.00
          2028 (cutoff): 100000 * 1.10^6 = 177,156.10
        After the cutoff merit FREEZES at the 2028 base and only the 10%
        cola compounds from it:
          2029: 177,156.10 * 1.10   = 194,871.71  (NOT 100000*1.10^8 =
                                       214,358.88, which is both-continue)
          2031: 177,156.10 * 1.10^3 = 235,794.77
        """
        raises = [
            FakeRaise(percentage="0.10", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.MERIT),
            FakeRaise(percentage="0.10", effective_month=7,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.COLA),
        ]
        result = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2031, 2,
        ))
        # 100000 * 1.10 * 1.10 = 121000.00
        assert result[2026] == Decimal("121000.00")
        # 100000 * 1.10^6 = 177156.10 (cutoff, both raises applied)
        assert result[2028] == Decimal("177156.10")
        # 177156.10 * 1.10 = 194871.71 (merit frozen; cola only)
        assert result[2029] == Decimal("194871.71")
        # 177156.10 * 1.10^3 = 235794.77 (three post-cutoff cola steps)
        assert result[2031] == Decimal("235794.77")

    def test_cola_only_extrapolates_without_double_count(self):
        """A pure recurring cola compounds uninterrupted across the cutoff.

        base 100,000; cola 10% (July) recurring from 2026; start 2026, end
        2030, N=2 -> cutoff 2028.  A recurring cola is never terminated,
        so it must reproduce the same 100000 * 1.10^k curve straight
        through the cutoff (no occurrence dropped, none double-counted):
          2026: 100000 * 1.10   = 110,000.00
          2028 (cutoff): 100000 * 1.10^3 = 133,100.00
          2029: 100000 * 1.10^4 = 146,410.00
          2030: 100000 * 1.10^5 = 161,051.00
        """
        raises = [
            FakeRaise(percentage="0.10", effective_month=7,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.COLA),
        ]
        result = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2030, 2,
        ))
        assert result[2026] == Decimal("110000.00")   # 100000 * 1.10
        assert result[2028] == Decimal("133100.00")   # 100000 * 1.10^3
        assert result[2029] == Decimal("146410.00")   # 100000 * 1.10^4
        assert result[2030] == Decimal("161051.00")   # 100000 * 1.10^5

    def test_merit_only_plateaus_after_cutoff(self):
        """A pure recurring merit stops compounding after the cutoff.

        base 100,000; merit 10% (Jan) recurring from 2026; start 2026, end
        2030, N=2 -> cutoff 2028.  With no cola raise nothing compounds
        after the cutoff, so the salary plateaus at the cutoff value:
          2026: 100000 * 1.10   = 110,000.00
          2028 (cutoff): 100000 * 1.10^3 = 133,100.00
          2029: 133,100.00 (frozen)
          2030: 133,100.00 (frozen)
        """
        raises = [
            FakeRaise(percentage="0.10", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.MERIT),
        ]
        result = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2030, 2,
        ))
        assert result[2026] == Decimal("110000.00")   # 100000 * 1.10
        assert result[2028] == Decimal("133100.00")   # 100000 * 1.10^3
        assert result[2029] == Decimal("133100.00")   # frozen at cutoff
        assert result[2030] == Decimal("133100.00")   # frozen at cutoff

    def test_future_scheduled_cola_is_not_pulled_before_its_start(self):
        """A COLA that starts after the cutoff first applies in ITS year (H1).

        base 100,000; a 10% recurring COLA effective 2031; start 2026,
        end 2032, N=2 -> cutoff 2028.  A raise first applies in its OWN
        effective year, never earlier:
          2026-2030: 100,000.00  (the COLA does not exist yet)
          2031: 100,000 * 1.10   = 110,000.00  (first application)
          2032: 100,000 * 1.10^2 = 121,000.00

        H1 was the defect this guards: the old horizon RE-ANCHORED a
        cola's effective year to the far side of the cutoff so a second
        compounding pass would count only post-cutoff occurrences, and a
        plain reset pulled this 2031 COLA back to 2029 -- 110,000.00 in
        2029 and 146,410.00 by 2032.  It needed a ``max(own, anchor)``
        floor to stop that.  Nothing moves an effective year now, so the
        floor has no subject; the case is kept because a future
        implementation could reintroduce one.
        """
        raises = [
            FakeRaise(percentage="0.10", effective_month=7,
                      effective_year=2031, is_recurring=True,
                      raise_type=RaiseTypeEnum.COLA),
        ]
        result = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2032, 2,
        ))
        assert result[2028] == Decimal("100000.00")  # cutoff, COLA not live
        assert result[2029] == Decimal("100000.00")  # NOT 110,000 (H1 bug)
        assert result[2030] == Decimal("100000.00")
        assert result[2031] == Decimal("110000.00")  # 100000 * 1.10
        assert result[2032] == Decimal("121000.00")  # 100000 * 1.10^2

    def test_mixed_flat_and_percentage_colas_horizon_invariant(self):
        """Mixed flat+pct COLAs ARE horizon-invariant (L4 fixed, chronological walk).

        base 100,000; a flat $1,000 recurring COLA + a 10% recurring COLA,
        both effective 2026.  ``apply_raises`` now applies each APPLICATION
        on the date it lands, flat before percentage within a date (M-01),
        so the money compounds in the order it arrives:

          2026: (100,000 + 1,000) * 1.10 = 111,100.00
          2027: (111,100 + 1,000) * 1.10 = 123,310.00
          2028: (123,310 + 1,000) * 1.10 = 136,741.00
          2029: (136,741 + 1,000) * 1.10 = 151,515.10
          2030: (151,515.10 + 1,000) * 1.10 = 167,766.61

        **What this test does and does not grade**, stated precisely
        because an earlier version of it overclaimed and an adversarial
        review measured the overclaim.  The three VALUE pins are real:
        revert the chronological walk and they fail -- by 352.00 at 2028
        and 1,336.94 at 2030.  (An earlier draft said "by 801.02", which
        was true of the S4 commit and stopped being true here: 801.02 is
        ``continuous - horizon`` while the two-phase SPLIT still existed,
        and this step deleted the split, so under either walk the two now
        agree and nothing can diverge by it.  The sentence decayed by
        being moved, not by being wrong when written.)
        The ``horizon == continuous`` assertion grades exactly one thing
        -- that a recurring cola is NOT given a ``terminal_year`` -- since
        both calls hand identical inputs to the walk once that holds.  It
        does NOT grade that the horizon exists at all: a build that
        terminated NOTHING would pass it.  What grades that direction is
        ``test_merit_freezes_after_cutoff_cola_continues`` and
        ``test_merit_only_plateaus_after_cutoff``, and a build with no
        horizon fails both.

        **What this test used to assert**, as
        ``..._pinned_not_horizon_invariant``: ``horizon[2028] ==
        137,093.00``, ``continuous[2030] == 169,103.55`` and
        ``horizon[2030] == 168,302.53``.  Every one of those credited 10%
        growth to flat dollars that had not arrived yet -- the old rule
        added all five $1,000s to the base and only then compounded.  The
        801.02 gap it documented as review finding L4 was a symptom of
        that ordering, not of the split, which is why removing the split
        was never the fix.  Both pins re-derived per that test's own
        instruction.
        """
        raises = [
            FakeRaise(flat_amount="1000", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.COLA),
            FakeRaise(percentage="0.10", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.COLA),
        ]
        continuous = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2030, 10,
        ))
        horizon = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2030, 2,
        ))
        # Third year of the walk above, and the cutoff year for N=2.
        assert horizon[2028] == Decimal("136741.00")
        # Fifth year of the walk above.
        assert continuous[2030] == Decimal("167766.61")
        # The split path reaches the same place: this is the assertion
        # that inverts, and it was 168,302.53 before the walk was fixed.
        assert horizon[2030] == Decimal("167766.61")
        # The invariance itself, across every year rather than one pin.
        assert horizon == continuous

    def test_a_one_time_raise_past_the_cutoff_is_dropped(self):
        """A one-time raise is terminated too, and its sibling proves it.

        Every other case in this class uses recurring raises, so the
        ONE-TIME arm of ``_terminate_after_horizon`` was ungraded here --
        a build that terminated only recurring raises would have passed
        the lot.  Two runs against the same 3% January cola, N=5 (cutoff
        2031), base 100,000:

          cola alone, 2035                       -> 134,391.64
          + one-time $2,000 in 2028 (within)     -> 136,851.39
          + one-time $2,000 in 2035 (past)       -> 134,391.64

        The past-cutoff one is dropped, so it answers exactly the cola
        alone; the within-cutoff one is kept and compounds by every later
        cola, so it answers strictly more.  Asserting BOTH directions is
        what stops a rule that simply drops every one-time raise from
        passing.
        """
        cola = FakeRaise(percentage="0.03", effective_month=1,
                         effective_year=2026, is_recurring=True,
                         raise_type=RaiseTypeEnum.COLA)
        within = FakeRaise(flat_amount="2000", effective_month=5,
                           effective_year=2028, is_recurring=False,
                           raise_type=RaiseTypeEnum.MERIT)
        past = FakeRaise(flat_amount="2000", effective_month=5,
                         effective_year=2035, is_recurring=False,
                         raise_type=RaiseTypeEnum.MERIT)
        base = Decimal("100000")
        alone = dict(project_salaries_by_year(base, [cola], 2026, 2035, 5))
        with_within = dict(project_salaries_by_year(
            base, [cola, within], 2026, 2035, 5))
        with_past = dict(project_salaries_by_year(
            base, [cola, past], 2026, 2035, 5))

        assert alone[2035] == Decimal("134391.64")
        # Dropped: identical to the cola on its own.
        assert with_past[2035] == Decimal("134391.64")
        # Kept, and compounded by the seven colas that follow it.
        assert with_within[2035] == Decimal("136851.39")
        assert with_within[2035] > alone[2035]

    def test_a_terminated_merit_does_not_compound_later_flat_dollars(self):
        """A merit raise that stopped in 2031 must not grow 2040's COLA money.

        The regression that parked the first attempt at this step.  A
        recurring flat $1,500 cola and a recurring 4% merit, N=5 (cutoff
        2031), base 100,000.  Once the merit terminates, each later year
        may only add the cola's flat $1,500 -- the merit's multiplier has
        no claim on money that arrives after it stopped:

          2031: 136,879.34
          2032: 138,379.34   (+1,500.00)
          2033: 139,879.34   (+1,500.00)
          2040: 150,379.34   (+1,500.00 a year, seven more times)

        Applying the terminated merit to those additions instead gives
        ``1,500 * 1.04^6 = 1,897.98`` a year, which is what the first
        attempt at this step produced, because it removed the two-phase
        split while the walk still grouped applications by raise.  That
        attempt answered ``155,001.58`` at 2040 against the
        ``150,379.34`` asserted here -- **+4,622.24** -- and 1,040.43 of
        the gap is already present AT the cutoff year (137,919.77 against
        136,879.34), so the divergence was never purely post-cutoff.  It
        reads correctly here only because the walk orders by date, so
        this case is a pin on BOTH rules at once.
        """
        flat_cola = FakeRaise(flat_amount="1500", effective_month=1,
                              effective_year=2026, is_recurring=True,
                              raise_type=RaiseTypeEnum.COLA)
        merit = FakeRaise(percentage="0.04", effective_month=1,
                          effective_year=2026, is_recurring=True,
                          raise_type=RaiseTypeEnum.MERIT)
        result = dict(project_salaries_by_year(
            Decimal("100000"), [flat_cola, merit], 2026, 2040, 5,
        ))
        assert result[2031] == Decimal("136879.34")
        assert result[2032] == Decimal("138379.34")
        assert result[2033] == Decimal("139879.34")
        assert result[2040] == Decimal("150379.34")
        # Stated as the increment, because that is the defect's shape.
        assert result[2032] - result[2031] == Decimal("1500.00")
        assert result[2033] - result[2032] == Decimal("1500.00")

    def test_real_shaped_cola_and_merit(self):
        """Real-shaped 3% July cola + 2.5% January merit, N=5.

        base 100,000; both recurring from 2026; start 2026, end 2035,
        N=5 -> cutoff 2031.  The merit terminates at 2031 and so applies
        six times (2026..2031); the cola is never terminated and applies
        once per year.  Every expected value below is therefore
        ``100,000 * 1.025^6 * 1.03^k`` with k the cola count, computed
        independently of the producer:

          2026: 1.025^1 * 1.03^1 -> 105,575.00
          2031: 1.025^6 * 1.03^6 -> 138,473.46   (cutoff)
          2032: 1.025^6 * 1.03^7 -> 142,627.66
          2033: 1.025^6 * 1.03^8 -> 146,906.49
          2035: 1.025^6 * 1.03^10 -> 155,853.10

        **The oracle is absolute, not relative**, and that is the point of
        the rewrite.  This test used to assert
        ``result[2032] == round_money(result[2031] * 1.03)`` -- it read the
        cutoff year back out of the producer and re-compounded it, which
        is the two-phase arithmetic the horizon no longer uses, and an
        oracle built from the value under test cannot catch an error that
        moves both years together.  (An adversarial review reported that
        the old form also diverged numerically from 2034; that part did
        not reproduce -- it agrees through 2035 -- so the reason to
        replace it is the dependence, not a wrong number.)
        """
        raises = [
            FakeRaise(percentage="0.03", effective_month=7,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.COLA),
            FakeRaise(percentage="0.025", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      raise_type=RaiseTypeEnum.MERIT),
        ]
        result = dict(project_salaries_by_year(
            Decimal("100000"), raises, 2026, 2035, 5,
        ))
        # 100000 * 1.025 * 1.03 = 105575.00 (through cutoff, both apply)
        assert result[2026] == Decimal("105575.00")
        # The cutoff year itself: six of each.
        assert result[2031] == Decimal("138473.46")
        # Past it the merit exponent STAYS at 6 while the cola's climbs.
        assert result[2032] == Decimal("142627.66")
        assert result[2033] == Decimal("146906.49")
        assert result[2035] == Decimal("155853.10")
        # And the merit is genuinely frozen: had it kept applying, 2032
        # would be 1.025^7 * 1.03^7 = 146,193.35, which is strictly more.
        assert result[2032] < Decimal("146193.35")
