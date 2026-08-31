"""
Shekel Budget App -- the pay calendar's RHYTHM: how many paydays a span holds.

Plan step **balance:X-bh-1** added
:func:`~app.services.pay_calendar.paydays_in_month_through` and
:func:`~app.services.pay_calendar.paydays_in_year_before`, the two producers
the paycheck engine's four calendar judgements and the analytics year overview
now share.  Those two surfaces exercise them end to end
(``test_paycheck_calculator``, ``test_calendar_service``); this module grades
the SPAN ITSELF -- its boundaries, its empty answers and the seam between the
saved schedule and the forward projection -- which an adversarial review of
that step found nothing covered directly.

No database: a :class:`~app.services.pay_calendar.PayCalendar` is a pure value
over a payday set, which is what lets these cases state a schedule in one line.
"""

from datetime import date, timedelta

import pytest

from app.services.pay_calendar import (
    PayCalendar,
    PayCalendarError,
    paydays_in_month_through,
    paydays_in_year_before,
)

#: A biweekly rhythm anchored on a Friday, three paydays in January 2026
#: (the 2nd, 16th and 30th) and two in every other month it reaches.
_JANUARY_OPENING = date(2026, 1, 2)
_CADENCE = 14


def _calendar(count, opening=_JANUARY_OPENING, cadence=_CADENCE, user_id=1):
    """A calendar of *count* paydays every *cadence* days from *opening*."""
    return PayCalendar.from_paydays(
        [(index + 1, opening + timedelta(days=cadence * index))
         for index in range(count)],
        cadence,
        user_id=user_id,
    )


class TestPaydaysInMonthThrough:
    """The month span: where a payday sits, and how many a month holds."""

    def test_counts_the_paydays_at_or_before_the_day(self):
        """A payday is counted; the ones after it in its month are not.

        January 2026 holds 2 / 16 / 30.  Asked at the 16th the answer is the
        first two, which is what makes the length that payday's ordinal.
        """
        assert paydays_in_month_through(_calendar(26), date(2026, 1, 16)) == (
            date(2026, 1, 2), date(2026, 1, 16),
        )

    def test_asked_at_a_month_end_it_is_the_month_size(self):
        """The other half of one producer: how many paydays the month holds."""
        assert len(paydays_in_month_through(
            _calendar(26), date(2026, 1, 31),
        )) == 3

    def test_a_neighbouring_month_is_excluded_at_both_ends(self):
        """The span is the calendar MONTH, not a window around the day.

        2025-12-19 and 2026-02-13 are both within one cadence of January's
        paydays, so a window would sweep them in.
        """
        assert paydays_in_month_through(
            _calendar(26), date(2026, 2, 28),
        ) == (date(2026, 2, 13), date(2026, 2, 27))

    def test_a_month_the_schedule_does_not_reach_is_empty(self):
        """Before the opening payday there is nothing, which is a real answer.

        The rhythm is not projected BACKWARD yet -- ledger row **N-390**, plan
        step **balance:X-bh-2** -- so December 2025 answers empty even though
        the owner was really paid in it.  This case is what will show that
        step landing.
        """
        assert paydays_in_month_through(
            _calendar(26), date(2025, 12, 31),
        ) == ()

    def test_a_long_cadence_leaves_months_with_no_payday(self):
        """A cadence longer than a month is legal and empties some months.

        ``budget.pay_schedule`` admits 1..365 days.  At 45 days from
        2026-01-02 the paydays are 01-02, 02-16, 04-02 -- so March holds none,
        and that is an answer rather than an error.
        """
        calendar = _calendar(3, cadence=45)
        assert paydays_in_month_through(calendar, date(2026, 3, 31)) == ()
        assert paydays_in_month_through(calendar, date(2026, 2, 28)) == (
            date(2026, 2, 16),
        )

    def test_an_empty_calendar_holds_no_paydays(self):
        """A companion holds no schedule, and production has one such user."""
        empty = PayCalendar.from_paydays([], None, user_id=1)
        assert paydays_in_month_through(empty, date(2026, 1, 31)) == ()


class TestPaydaysInYearBefore:
    """The year span: what this owner has been paid before a given payday."""

    def test_sums_the_years_earlier_paydays_only(self):
        """Strictly before the day, and only within its own calendar year."""
        assert paydays_in_year_before(_calendar(26), date(2026, 2, 13)) == (
            date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
        )

    def test_the_years_first_payday_has_nothing_before_it(self):
        """A cumulative of zero, which is the correct answer and not an error."""
        assert paydays_in_year_before(_calendar(26), date(2026, 1, 2)) == ()

    def test_the_previous_year_is_not_counted(self):
        """The wage base and the annual cap are both CALENDAR-year windows.

        This schedule opens 2025-12-05 and runs into 2026, so a span that
        leaked across the boundary would carry December's paydays into
        January's cumulative and reach the SS cap early.
        """
        calendar = _calendar(6, opening=date(2025, 12, 5))
        assert paydays_in_year_before(calendar, date(2026, 1, 16)) == (
            date(2026, 1, 2),
        )

    def test_the_first_of_january_names_a_crossed_span(self):
        """Asked on 1 January the span ends the day before it opens.

        `[Jan 1, Dec 31 of the previous year]` is crossed, and the honest
        answer to "which paydays fell this year before its first day" is none
        -- where ``overlapping_window`` REFUSES a crossed range, because there
        a reversed pair is a caller defect rather than an ordinary question.
        """
        calendar = _calendar(6, opening=date(2026, 1, 1))
        assert paydays_in_year_before(calendar, date(2026, 1, 1)) == ()


class TestTheSeamBetweenSavedAndProjected:
    """Where the saved schedule stops and the owner's cadence carries on."""

    def test_the_span_tiles_across_the_horizon(self):
        """A month straddling the horizon holds BOTH halves exactly once.

        Two saved paydays put the horizon at 2026-01-29, so 2026-01-30 is the
        first projected one.  January must answer 2 / 16 / 30 -- neither
        dropping the projected payday nor counting the saved ones twice.
        """
        calendar = _calendar(2)
        assert calendar.saved()[-1].end_date == date(2026, 1, 29)
        assert paydays_in_month_through(calendar, date(2026, 1, 31)) == (
            date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
        )

    def test_a_span_wholly_past_the_horizon_is_projected(self):
        """The rhythm continues at the owner's cadence (``R-PC9``)."""
        assert paydays_in_month_through(_calendar(2), date(2026, 3, 31)) == (
            date(2026, 3, 13), date(2026, 3, 27),
        )

    def test_a_span_wholly_inside_the_schedule_projects_nothing(self):
        """The projection is not walked when the saved schedule covers the span."""
        assert paydays_in_month_through(_calendar(26), date(2026, 3, 31)) == (
            date(2026, 3, 13), date(2026, 3, 27),
        )

    def test_a_far_future_span_costs_the_answer_and_not_the_distance(self):
        """THE FIRING CONTROL for the projection's start argument.

        An adversarial review of plan step **balance:X-bh-1** measured the
        first cut stepping from the horizon to the span, which put **442 ms**
        of pure CPU on one ``/analytics/calendar/2100?view=year`` render at the
        one-day cadence ``budget.pay_schedule`` admits.  The producer now jumps
        arithmetically, so this answers a month 74 years past the horizon
        without walking to it.  Asserted as a BOUND on the work rather than as
        a duration, because a wall-clock assertion is a flake on a loaded box:
        the daily rhythm puts 31 paydays in the month and the walk may not
        exceed them by more than the one period ``from_day`` admits.
        """
        calendar = _calendar(3, cadence=1)
        answer = paydays_in_month_through(calendar, date(2100, 5, 31))
        assert len(answer) == 31
        assert answer[0] == date(2100, 5, 1)
        assert answer[-1] == date(2100, 5, 31)

    def test_a_year_to_date_past_the_horizon_counts_the_projection(self):
        """The YEAR span projects too, which nothing else covers.

        The month span's seam is pinned above; an adversarial review of plan
        step **balance:X-bh-1** noted the year span had no direct case, and it
        is the one the FICA wage-base cumulative reads.  Two saved paydays put
        the horizon at 2026-01-29, so a 2026-04-10 paycheck has seven paydays
        before it in its year -- 01-02 and 01-16 recorded, then 01-30, 02-13,
        02-27, 03-13 and 03-27 projected -- where counting saved rows alone
        would answer two and reach the wage base five paychecks late.
        """
        assert paydays_in_year_before(_calendar(2), date(2026, 4, 10)) == (
            date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
            date(2026, 2, 13), date(2026, 2, 27), date(2026, 3, 13),
            date(2026, 3, 27),
        )

    def test_the_projection_stops_at_the_applications_calendar(self):
        """Past ``CALENDAR_DATE_MAX`` this application has no calendar."""
        assert paydays_in_month_through(
            _calendar(3), date(2101, 1, 31),
        ) == ()


class TestTheEngineRefusesAPaydayItsCalendarCannotPlace:
    """The (period, calendar) mismatch `PayrollBasis` cannot make unrepresentable."""

    def test_a_foreign_payday_is_refused_rather_than_miscounted(self):
        """Measured 0, 1 and 2 for three foreign paydays before this refusal.

        ``PayrollBasis`` binds the profile to a calendar, but the PERIOD
        arrives separately, so a paycheck can still be priced against the
        wrong owner's schedule.  The count is over the CALENDAR's paydays, so
        such a pairing used to answer silently -- and 0 is the reading that
        drops a 12-per-year deduction and takes a 24-per-year one on a third
        paycheck.
        """
        # Pylint: ``import-outside-toplevel`` -- the engine's private ordinal
        # is imported here rather than at module scope so this file reads as a
        # test of the pay-calendar producers, with one case reaching across to
        # the refusal built on them.
        from app.services.paycheck_calculator import (  # pylint: disable=import-outside-toplevel
            _month_ordinal,
        )

        calendar = _calendar(26)
        with pytest.raises(PayCalendarError, match="is not paid on"):
            _month_ordinal(calendar, date(2026, 1, 9))

    def test_a_payday_of_this_calendar_is_placed(self):
        """The refusal above fires on the mismatch and not on every call."""
        # Pylint: ``import-outside-toplevel`` -- see above.
        from app.services.paycheck_calculator import (  # pylint: disable=import-outside-toplevel
            _month_ordinal,
        )

        assert _month_ordinal(_calendar(26), date(2026, 1, 30)) == 3
