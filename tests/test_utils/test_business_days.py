"""Tests for the shared business-day calendar (plan step ``pay_calendar:C14-a``).

The module is one of the few in this application whose answer can be
checked against a published external record, and this file does exactly
that rather than re-deriving it.  **Every expected holiday below is
TRANSCRIBED from the federal holiday calendar, not computed**: a test that
asked :func:`~app.utils.business_days.federal_holidays` to grade itself --
by rebuilding the nth-weekday arithmetic in the assertion -- would share
one producer with its subject and measure nothing.

The five transcribed years are chosen so that between them they exercise
every branch the module has:

- **2020** predates Juneteenth, so it holds TEN holidays and pins the
  :data:`~app.utils.business_days.JUNETEENTH_FIRST_YEAR` floor.
- **2021** is the FIRST Juneteenth year, and it lands on a Saturday, so the
  floor and the backward observance are pinned in the same row.
- **2025** has no weekend collision at all -- the plain case.
- **2026** puts Independence Day on a Saturday, so it pins the backward
  observance, and it is the year ledger row **N-398** is about.
- **2027** is the hardest year available: Juneteenth and Christmas both
  fall on a Saturday, Independence Day on a Sunday, and New Year's Day
  2028 falls on a Saturday so its observance lands on **2027**-12-31.
  That last one is why the year holds TWELVE.
"""
from datetime import date, timedelta

import pytest

from app.enums import BusinessDayShiftEnum
from app.utils.business_days import (
    federal_holidays,
    is_business_day,
    shift_to_business_day,
)
from app.utils.dates import CALENDAR_DATE_MAX, CALENDAR_DATE_MIN

# Every year this application's calendar admits
# (``app.utils.dates.CALENDAR_DATE_MIN``..``CALENDAR_DATE_MAX``).  The
# whole-calendar checks below sweep it INSIDE one test rather than through
# ``parametrize``: ``tests/conftest.py``'s ``db`` fixture is autouse and
# drops and clones a database per ITEM, so 101 parametrised items bought
# ~101 database clones to run 0.002 s of arithmetic.  The failure message
# carries the offending years, which is strictly more legible than 101
# separate red lines.
CALENDAR_YEARS = range(2000, 2101)

# Transcribed, never computed.  See the module docstring.
OBSERVED_HOLIDAYS = {
    2020: {
        date(2020, 1, 1), date(2020, 1, 20), date(2020, 2, 17),
        date(2020, 5, 25), date(2020, 7, 3), date(2020, 9, 7),
        date(2020, 10, 12), date(2020, 11, 11), date(2020, 11, 26),
        date(2020, 12, 25),
    },
    2021: {
        date(2021, 1, 1), date(2021, 1, 18), date(2021, 2, 15),
        date(2021, 5, 31), date(2021, 6, 18), date(2021, 7, 5),
        date(2021, 9, 6), date(2021, 10, 11), date(2021, 11, 11),
        date(2021, 11, 25), date(2021, 12, 24), date(2021, 12, 31),
    },
    2025: {
        date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17),
        date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4),
        date(2025, 9, 1), date(2025, 10, 13), date(2025, 11, 11),
        date(2025, 11, 27), date(2025, 12, 25),
    },
    2026: {
        date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
        date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3),
        date(2026, 9, 7), date(2026, 10, 12), date(2026, 11, 11),
        date(2026, 11, 26), date(2026, 12, 25),
    },
    2027: {
        date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15),
        date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5),
        date(2027, 9, 6), date(2027, 10, 11), date(2027, 11, 11),
        date(2027, 11, 25), date(2027, 12, 24), date(2027, 12, 31),
    },
}


class TestFederalHolidays:
    """Grade the computed set against the transcribed federal calendar."""

    @pytest.mark.parametrize("year", sorted(OBSERVED_HOLIDAYS))
    def test_matches_the_transcribed_calendar_exactly(self, year):
        """The computed year equals the published one, set for set."""
        assert federal_holidays(year) == OBSERVED_HOLIDAYS[year]

    def test_pre_2021_years_hold_ten_and_omit_juneteenth(self):
        """Juneteenth is absent before 2021, so 2020 holds ten holidays."""
        assert len(federal_holidays(2020)) == 10
        assert date(2020, 6, 19) not in federal_holidays(2020)

    def test_2021_is_the_first_year_holding_juneteenth(self):
        """The floor is inclusive, and 2021 is transcribed like any year.

        The whole year sits in :data:`OBSERVED_HOLIDAYS`, so the set
        assertion above already grades it against a transcribed oracle;
        this states the FLOOR itself, which is the one thing a set
        equality for a single year does not say out loud.  It replaced an
        ``assert federal_holidays(2021) & {06-18, 06-19}`` that accepted
        EITHER date -- including 2021-06-19, a Saturday and the wrong
        answer -- and reported a failure by dumping two whole sets.
        """
        assert date(2021, 6, 18) in federal_holidays(2021)
        assert not federal_holidays(2020) & {date(2020, 6, 18), date(2020, 6, 19)}

    def test_2027_carries_new_year_2028_because_it_falls_on_a_saturday(self):
        """A New Year's observance can land in the PRECEDING year."""
        assert date(2027, 12, 31) in federal_holidays(2027)
        assert len(federal_holidays(2027)) == 12

    def test_2028_does_not_also_claim_its_own_new_years_day(self):
        """The spillover leaves 2028, it does not get counted twice."""
        # 2028-01-01 is a Saturday observed on 2027-12-31, so the day
        # itself is NOT an observed holiday of 2028.  Were it counted in
        # both years, the Saturday would suppress a business day for no
        # reason and ``is_business_day`` would disagree with itself
        # depending on which year's set it consulted.
        assert date(2028, 1, 1) not in federal_holidays(2028)

    def test_every_observed_day_falls_inside_its_own_year(self):
        """The set is closed over its year, which is what lets the
        single-year lookup in ``is_business_day`` be total."""
        stray = {
            year: sorted(day for day in federal_holidays(year) if day.year != year)
            for year in CALENDAR_YEARS
        }
        assert not {y: d for y, d in stray.items() if d}

    def test_no_observed_holiday_ever_falls_on_a_weekend(self):
        """Observance is what moves a weekend holiday onto a weekday."""
        weekend = {
            year: sorted(day for day in federal_holidays(year) if day.weekday() >= 5)
            for year in CALENDAR_YEARS
        }
        assert not {y: d for y, d in weekend.items() if d}

    def test_every_year_of_the_app_calendar_holds_its_exact_count(self):
        """The size of every year 2000-2100, to the holiday.

        Ten statutory holidays before Juneteenth and eleven after, moved by
        the one thing that can change a year's size: a New Year's Day
        falling on a Saturday is observed on 31 December, so the year that
        OWNS it loses one and the year BEFORE it gains one.  The two can
        never BOTH apply to one year -- 1 January of the next year is one
        or two weekdays later than this year's, so it cannot also be a
        Saturday, and there is no such year between 1900 and 2200.  *An
        earlier draft of this docstring called them independent.*

        **What this test actually grades, said honestly.**  It is a whole
        calendar oracle for the Juneteenth FLOOR and the New Year
        SPILLOVER, and it is blind by construction to everything else: a
        COUNT cannot see where a weekday-anchored holiday lands, because
        moving one cannot collide it with another.  An adversarial review
        measured that -- reverting ``_last_weekday`` to the Memorial Day
        2027 defect, and computing MLK as the second Monday of January,
        both leave this test GREEN.  ``_nth_weekday`` and ``_last_weekday``
        are graded by the five transcribed years above and by
        :meth:`test_memorial_day_is_always_the_last_monday_of_may`, not
        here.  *An earlier draft claimed those two functions were "what it
        is really grading", which is the one thing it cannot do.*

        Note also that ``expected`` is not an independent oracle for the
        rule it encodes: the Juneteenth floor and the spillover are
        restated here in miniature, so a shared misreading would pass
        both.  The transcribed 2021 and 2027 rows are what pin those two
        independently.

        A first draft asserted only ``expected`` or ``expected + 1`` and
        FAILED on fifteen years -- 2005 holds nine, not ten, because
        2005-01-01 was a Saturday.  The draft's arithmetic was wrong, not
        the module's.
        """
        wrong = {}
        for year in CALENDAR_YEARS:
            expected = 10 if year < 2021 else 11
            if date(year, 1, 1).weekday() == 5:
                expected -= 1
            if date(year + 1, 1, 1).weekday() == 5:
                expected += 1
            actual = len(federal_holidays(year))
            if actual != expected:
                wrong[year] = (actual, expected)
        assert not wrong, f"(actual, expected) by year: {wrong}"

    def test_memorial_day_is_always_the_last_monday_of_may(self):
        """The defect this file caught, pinned as a property.

        Memorial Day is May's only federal holiday, so it is findable
        without naming a date.  "Last Monday" is then checked by the
        definition itself -- a week later is June -- rather than against a
        recomputed nth-weekday, which is what makes this independent of
        the function it grades.  Beside the transcribed years it is
        also the only whole-calendar guard on ``_last_weekday``.

        ``_last_weekday`` began the month's search at the 28th and stepped
        forward in weeks, so in any month whose last such weekday falls on
        the 29th, 30th or 31st it answered a week early: Memorial Day 2027
        came back as 2027-05-24 against a true 2027-05-31.
        """
        wrong = {}
        for year in CALENDAR_YEARS:
            memorial = next(
                day for day in federal_holidays(year) if day.month == 5
            )
            if memorial.weekday() != 0 or (
                memorial + timedelta(days=7)
            ).month != 6:
                wrong[year] = memorial
        assert not wrong, f"not the last Monday of May: {wrong}"


class TestIsBusinessDay:
    """Pin what 'money moves on this day' means."""

    def test_an_ordinary_weekday_is_a_business_day(self):
        """2026-01-02 is a Friday and no holiday."""
        assert is_business_day(date(2026, 1, 2))

    @pytest.mark.parametrize("day", [date(2026, 1, 3), date(2026, 1, 4)])
    def test_a_weekend_is_not(self, day):
        """Saturday and Sunday both fail."""
        assert not is_business_day(day)

    def test_a_midweek_holiday_is_not_a_business_day(self):
        """**Ledger row N-398's day.**

        2026-01-01 is a THURSDAY.  A weekend-only rule would call it a
        business day and the whole of this arc's payday shift would never
        fire on the production owner's Thursday schedule.
        """
        assert date(2026, 1, 1).weekday() == 3
        assert not is_business_day(date(2026, 1, 1))

    def test_a_saturday_holidays_observed_friday_is_not_a_business_day(self):
        """2026-07-04 falls on a Saturday, so the Friday is closed."""
        assert not is_business_day(date(2026, 7, 3))

    def test_the_new_year_spillover_closes_the_preceding_december(self):
        """2027-12-31 is closed for New Year's Day 2028."""
        assert not is_business_day(date(2027, 12, 31))


class TestShiftToBusinessDay:
    """Pin the one displacement both consumers call."""

    @pytest.mark.parametrize("shift", list(BusinessDayShiftEnum))
    def test_a_business_day_is_never_moved(self, shift):
        """No convention displaces a day that is already a business day."""
        assert shift_to_business_day(date(2026, 1, 2), shift) == date(2026, 1, 2)

    @pytest.mark.parametrize(
        "day", [date(2026, 1, 1), date(2026, 1, 3), date(2026, 7, 3)],
    )
    def test_none_moves_nothing_at_all(self, day):
        """``NONE`` is the default every unasked owner holds (R-PC56)."""
        assert shift_to_business_day(day, BusinessDayShiftEnum.NONE) == day

    def test_prior_reproduces_the_ruling_s_worked_example(self):
        """**R-PC47's own numbers.**

        The rhythm names 2026-01-01; payroll really paid 2025-12-31, which
        moves one $3,526.00 gross paycheck out of 2026 and into 2025.
        """
        assert shift_to_business_day(
            date(2026, 1, 1), BusinessDayShiftEnum.PRIOR,
        ) == date(2025, 12, 31)

    def test_next_takes_the_same_day_the_other_way(self):
        """The mirror: 2026-01-02, the Friday after the holiday."""
        assert shift_to_business_day(
            date(2026, 1, 1), BusinessDayShiftEnum.NEXT,
        ) == date(2026, 1, 2)

    def test_prior_walks_a_holiday_and_a_weekend_together(self):
        """2027-12-25 is a Saturday whose observance takes the Friday.

        Backward: the 25th is a weekend, the 24th is the observed
        Christmas, so the landing is Thursday the 23rd.  A single-step
        implementation stops on the 24th and dates a paycheck on a day the
        bank is shut.
        """
        assert shift_to_business_day(
            date(2027, 12, 25), BusinessDayShiftEnum.PRIOR,
        ) == date(2027, 12, 23)

    def test_next_walks_a_weekend_into_the_following_month(self):
        """2026-07-04 is a Saturday; forward lands on Monday the 6th."""
        assert shift_to_business_day(
            date(2026, 7, 4), BusinessDayShiftEnum.NEXT,
        ) == date(2026, 7, 6)

    def test_prior_crosses_a_year_boundary_through_the_spillover(self):
        """2028-01-01 backward lands in 2027, two closures down.

        The Saturday itself, then 2027-12-31 -- which is a business day
        UNLESS the spillover is modelled.  This case fails outright if
        :func:`federal_holidays` stops at its own year's statute.
        """
        assert shift_to_business_day(
            date(2028, 1, 1), BusinessDayShiftEnum.PRIOR,
        ) == date(2027, 12, 30)

    def test_next_crosses_a_year_boundary(self):
        """2027-12-31 forward is the Monday of the new year."""
        assert shift_to_business_day(
            date(2027, 12, 31), BusinessDayShiftEnum.NEXT,
        ) == date(2028, 1, 3)

    @pytest.mark.parametrize(
        "shift", [BusinessDayShiftEnum.PRIOR, BusinessDayShiftEnum.NEXT],
    )
    def test_the_displacement_is_idempotent(self, shift):
        """Applying it twice is applying it once, over four whole years.

        **The landing is always a business day, so the displacement has a
        fixed point.**  That and no more: this is NOT the non-compounding
        guarantee, and an earlier draft of this docstring called it one.
        Compounding does not come from re-applying the shift -- it comes
        from a caller re-anchoring its cadence on the OUTPUT, and
        ``shift_to_business_day`` stays perfectly idempotent while that
        happens.  The discriminating code is the caller's, so the property
        belongs to the leaf that adds one; here there is no caller at all.
        """
        day = date(2025, 1, 1)
        while day <= date(2028, 12, 31):
            once = shift_to_business_day(day, shift)
            assert is_business_day(once)
            assert shift_to_business_day(once, shift) == once
            day += timedelta(days=1)


class TestTheAnswerMayLeaveTheApplicationCalendar:
    """Pin the escape at both ends, because a caller has to bound it.

    Both ends of ``CALENDAR_DATE_MIN``..``CALENDAR_DATE_MAX`` are
    non-business days -- 2000-01-01 is a Saturday and 2100-12-31 is the
    observed New Year's Day 2101 -- so the displacement walks off the
    calendar at each end.  These are the INTENDED answers: the module
    answers the arithmetic question it was asked, and the columns that
    persist a date state the bound (``ck_pay_schedule_history_opens_range``
    and its two siblings).  They are pinned here so the leaf that writes a
    shifted date DISCOVERS the escape from a test rather than from an
    ``IntegrityError``.
    """

    def test_both_ends_of_the_app_calendar_are_non_business_days(self):
        """The premise, asserted rather than assumed."""
        assert not is_business_day(CALENDAR_DATE_MIN)
        assert CALENDAR_DATE_MIN.weekday() == 5
        assert not is_business_day(CALENDAR_DATE_MAX)
        assert CALENDAR_DATE_MAX in federal_holidays(CALENDAR_DATE_MAX.year)

    def test_prior_from_the_floor_lands_below_the_app_calendar(self):
        """2000-01-01 backward is 1999-12-30, outside the calendar."""
        landed = shift_to_business_day(
            CALENDAR_DATE_MIN, BusinessDayShiftEnum.PRIOR,
        )
        assert landed == date(1999, 12, 30)
        assert landed < CALENDAR_DATE_MIN

    def test_next_from_the_ceiling_lands_above_the_app_calendar(self):
        """2100-12-31 forward is 2101-01-03, outside the calendar."""
        landed = shift_to_business_day(
            CALENDAR_DATE_MAX, BusinessDayShiftEnum.NEXT,
        )
        assert landed == date(2101, 1, 3)
        assert landed > CALENDAR_DATE_MAX


class TestAnUnrecognisedConventionIsRefused:
    """A value outside the enum must not inherit a money-moving default."""

    @pytest.mark.parametrize("bogus", [None, 1, "prior", object()])
    def test_a_non_member_raises_rather_than_shifting_forward(self, bogus):
        """The residue is refused, not given ``NEXT``'s behaviour.

        ``2026-01-01`` is a holiday, so a dispatch written as "PRIOR, else
        forward" would silently answer 2026-01-02 for every value here --
        a money date moved by a convention nobody chose.  The ref-cache
        INTEGER id is in the list deliberately: it is the value most likely
        to arrive by mistake, since every other ref comparison in this
        application is an integer id.
        """
        with pytest.raises(ValueError, match="unhandled business-day shift"):
            shift_to_business_day(date(2026, 1, 1), bogus)

    def test_a_non_member_still_returns_an_ordinary_business_day(self):
        """The refusal is reached only when a shift is actually needed.

        2026-01-02 is a business day, so no convention can move it and the
        function answers before it ever dispatches.  Stated because it is
        the one input shape where a bogus value does NOT raise, and a
        reader who assumes otherwise would write the wrong guard upstream.
        """
        assert shift_to_business_day(date(2026, 1, 2), None) == date(2026, 1, 2)
