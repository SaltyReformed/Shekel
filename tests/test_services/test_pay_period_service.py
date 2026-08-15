"""
Shekel Budget App -- Pay Period Service Tests

Integration tests for the pay-period READERS.  All of them issue DB queries,
so these run against a real PostgreSQL database using the shared
app/db/bare_user fixtures from conftest.

**The writer's tests moved out at plan step C3-b**, with the writer, to
``test_pay_period_write.py``.  What is left here answers "which periods does
this owner have", which plan step C2-f points at ``pay_calendar.PayCalendar``.

**Three readers' tests moved out at C2-f1, WITH the behaviour they grade** --
none was deleted, because none of the behaviour was:

* ``get_next_period`` -> ``test_pay_calendar_value.TestPeriodStartingAfter``,
  beside the ``period_starting_before`` mirror that also replaced
  ``companion_service.get_previous_period``.
* ``get_current_and_future_periods`` ->
  ``tests/test_routes/test_period_options.py``, because the rule it encoded is
  the FORM's ("an already-closed period is not somewhere a row moves TO")
  rather than the calendar's.
* ``get_overlapping_periods`` -> nothing moved, because **it had no direct
  test call site at all**: the retired SQL was exercised only through
  ``calendar_service`` and ``spending_report_service``, never graded on its own.
  ``test_pay_calendar_value``'s ``overlapping`` tests grade the predicate that
  replaced it, and grade it harder -- they cover the crossed range, which the
  query answered with an empty list and the calendar refuses.
"""

from datetime import date

from app.services import pay_period_service


# ---------------------------------------------------------------------------
# TestGetCurrentPeriod
# ---------------------------------------------------------------------------


class TestGetCurrentPeriod:
    """Tests for get_current_period()."""

    def test_returns_period_containing_date(self, app, db, bare_user, bare_periods):
        """A date within the first period (Jan 2-15) should return it."""
        with app.app_context():
            period = pay_period_service.get_current_period(
                bare_user["user"].id,
                as_of=date(2026, 1, 5),
            )
            assert period is not None
            assert period.period_index == 0
            assert period.start_date == date(2026, 1, 2)
            assert period.end_date == date(2026, 1, 15)

    def test_no_period_contains_date_returns_none(self, app, db, bare_user, bare_periods):
        """A date before all periods returns None."""
        with app.app_context():
            period = pay_period_service.get_current_period(
                bare_user["user"].id,
                as_of=date(2020, 1, 1),
            )
            assert period is None

    def test_custom_as_of_date(self, app, db, bare_user, bare_periods):
        """Targeting the 3rd period (index 2) returns the correct period."""
        with app.app_context():
            # Period 2: starts Jan 2 + 28 days = Jan 30, ends Feb 12.
            period = pay_period_service.get_current_period(
                bare_user["user"].id,
                as_of=date(2026, 2, 1),
            )
            assert period is not None
            assert period.period_index == 2
            assert period.start_date == date(2026, 1, 30)


# ---------------------------------------------------------------------------
# TestGetPeriodsInRange
# ---------------------------------------------------------------------------


class TestGetPeriodsInRange:
    """Tests for get_periods_in_range()."""

    def test_returns_correct_window_by_index(self, app, db, bare_user, bare_periods):
        """Requesting start_index=2, count=3 returns indices 2, 3, 4."""
        with app.app_context():
            periods = pay_period_service.get_periods_in_range(
                bare_user["user"].id,
                start_index=2,
                count=3,
            )
            assert len(periods) == 3
            assert [p.period_index for p in periods] == [2, 3, 4]

    def test_range_beyond_available_returns_partial(self, app, db, bare_user, bare_periods):
        """Requesting past the end returns only what exists."""
        with app.app_context():
            periods = pay_period_service.get_periods_in_range(
                bare_user["user"].id,
                start_index=8,
                count=5,
            )
            assert len(periods) == 2
            assert [p.period_index for p in periods] == [8, 9]


# ---------------------------------------------------------------------------
# TestGetAllPeriods
# ---------------------------------------------------------------------------


class TestGetAllPeriods:
    """Tests for get_all_periods()."""

    def test_returns_all_periods_ordered_by_index(self, app, db, bare_user, bare_periods):
        """Should return all 10 periods ordered 0..9."""
        with app.app_context():
            periods = pay_period_service.get_all_periods(bare_user["user"].id)
            assert len(periods) == 10
            assert [p.period_index for p in periods] == list(range(10))


# ---------------------------------------------------------------------------
# TestNegativeAndBoundaryPaths
# ---------------------------------------------------------------------------


class TestNegativeAndBoundaryPaths:
    """Boundary-condition tests for the pay-period readers.

    The batch-size and large-batch cases moved to
    ``test_pay_period_write.py`` with the writer at plan step C3-b; what is
    left here is the date-boundary behaviour of the lookups.
    """

    def test_get_current_period_exact_start_date(self, app, db, bare_user, bare_periods):
        """get_current_period with as_of equal to a period's start_date returns that period.

        Off-by-one on date boundaries is a classic bug. The first day of a
        period must be included in that period, not the previous one.
        """
        with app.app_context():
            # Period 0 starts on 2026-01-02.
            period = pay_period_service.get_current_period(
                bare_user["user"].id,
                as_of=date(2026, 1, 2),
            )
            assert period is not None
            assert period.period_index == 0
            assert period.start_date == date(2026, 1, 2)

    def test_get_current_period_exact_end_date(self, app, db, bare_user, bare_periods):
        """get_current_period with as_of equal to a period's end_date returns that period.

        The last day of a period must be included in that period. If the
        boundary were exclusive, the user would see no current period on the
        last day of a pay cycle.
        """
        with app.app_context():
            # Period 0 ends on 2026-01-15 (start + 13 days).
            period = pay_period_service.get_current_period(
                bare_user["user"].id,
                as_of=date(2026, 1, 15),
            )
            assert period is not None
            assert period.period_index == 0
            assert period.end_date == date(2026, 1, 15)

    def test_get_current_period_after_all_periods(self, app, db, bare_user, bare_periods):
        """get_current_period returns None for a date after all generated periods.

        With 10 periods starting 2026-01-02 at 14-day cadence, the last period
        (index 9) ends on 2026-05-21. A date of 2026-05-22 is outside all ranges.
        Ensures the service does not crash or return a wrong period when the
        date is outside all generated ranges.
        """
        with app.app_context():
            # Period 9: start = 2026-05-08, end = 2026-05-21.
            period = pay_period_service.get_current_period(
                bare_user["user"].id,
                as_of=date(2026, 5, 22),
            )
            assert period is None

    def test_get_periods_in_range_start_beyond_available(
        self, app, db, bare_user, bare_periods
    ):
        """get_periods_in_range with start_index beyond all periods returns empty list.

        A race condition or stale UI could request a range beyond what exists.
        bare_periods has indices 0-9; requesting start_index=15 finds nothing.
        """
        with app.app_context():
            periods = pay_period_service.get_periods_in_range(
                bare_user["user"].id,
                start_index=15,
                count=5,
            )
            assert periods == []

    def test_get_periods_in_range_negative_start(self, app, db, bare_user, bare_periods):
        """get_periods_in_range with negative start_index starts from index 0.

        Negative start_index is treated as a literal value in the SQL query.
        Since no period has a negative index, the filter ``period_index >= -1``
        effectively starts from index 0.  With count=5, the upper bound is
        ``period_index < 4``, so indices 0, 1, 2, 3 are returned (4 periods,
        not the 5 requested).
        """
        with app.app_context():
            # SQL: period_index >= -1 AND period_index < 4
            periods = pay_period_service.get_periods_in_range(
                bare_user["user"].id,
                start_index=-1,
                count=5,
            )
            # Returns 4 periods (indices 0-3), not 5.
            assert len(periods) == 4
            assert [p.period_index for p in periods] == [0, 1, 2, 3]



