"""
Shekel Budget App -- Pay Period Service Tests

Integration tests for the pay period service that generates and queries
biweekly pay periods.  All functions use DB queries, so these tests
exercise the service against a real PostgreSQL database using the
shared app/db/seed_user fixtures from conftest.
"""

from datetime import date, timedelta

import pytest

from app.exceptions import ValidationError
from app.services import pay_period_service


# ---------------------------------------------------------------------------
# TestGeneratePayPeriods
# ---------------------------------------------------------------------------


class TestGeneratePayPeriods:
    """Tests for generate_pay_periods()."""

    def test_generates_correct_count_with_14_day_cadence(self, app, db, seed_user):
        """Generate 5 periods -- assert count and 14-day spans."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=5,
                cadence_days=14,
            )
            db.session.commit()

            assert len(periods) == 5
            for p in periods:
                span = (p.end_date - p.start_date).days + 1
                assert span == 14

    def test_period_indices_are_sequential(self, app, db, seed_user):
        """Generated periods should have indices 0..n-1."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=5,
            )
            db.session.commit()

            indices = [p.period_index for p in periods]
            assert indices == [0, 1, 2, 3, 4]

    def test_end_date_equals_start_plus_cadence_minus_one(self, app, db, seed_user):
        """end_date should be start_date + 13 days for 14-day cadence."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=3,
                cadence_days=14,
            )
            db.session.commit()

            for p in periods:
                assert p.end_date == p.start_date + timedelta(days=13)

    def test_duplicate_start_date_silently_skipped(self, app, db, seed_user):
        """Re-generating with an overlapping start_date skips duplicates."""
        with app.app_context():
            user_id = seed_user["user"].id

            # First batch: 3 periods starting Jan 2.
            first = pay_period_service.generate_pay_periods(
                user_id=user_id,
                start_date=date(2026, 1, 2),
                num_periods=3,
            )
            db.session.commit()
            assert len(first) == 3

            # Second batch: 3 periods starting at the same date.
            second = pay_period_service.generate_pay_periods(
                user_id=user_id,
                start_date=date(2026, 1, 2),
                num_periods=3,
            )
            db.session.commit()

            # All 3 were duplicates, so nothing new was created.
            assert len(second) == 0

            # Total in DB should still be 3.
            all_periods = pay_period_service.get_all_periods(user_id)
            assert len(all_periods) == 3

    def test_appending_to_existing_periods(self, app, db, seed_user):
        """New periods after existing range get sequential indices."""
        with app.app_context():
            user_id = seed_user["user"].id

            # First batch: indices 0-2.
            pay_period_service.generate_pay_periods(
                user_id=user_id,
                start_date=date(2026, 1, 2),
                num_periods=3,
            )
            db.session.commit()

            # Second batch: start after the first 3 periods.
            # 3 periods × 14 days = 42 days from Jan 2 → Feb 13.
            new = pay_period_service.generate_pay_periods(
                user_id=user_id,
                start_date=date(2026, 2, 13),
                num_periods=2,
            )
            db.session.commit()

            assert len(new) == 2
            assert new[0].period_index == 3
            assert new[1].period_index == 4

    def test_invalid_start_date_raises_error(self, app, db, seed_user):
        """Passing a non-date start_date raises ValidationError."""
        with app.app_context():
            with pytest.raises(ValidationError, match="start_date must be a date"):
                pay_period_service.generate_pay_periods(
                    user_id=seed_user["user"].id,
                    start_date="2026-01-02",
                    num_periods=1,
                )

    def test_cadence_days_less_than_one_raises_error(self, app, db, seed_user):
        """cadence_days=0 raises ValidationError."""
        with app.app_context():
            with pytest.raises(ValidationError, match="cadence_days must be at least 1"):
                pay_period_service.generate_pay_periods(
                    user_id=seed_user["user"].id,
                    start_date=date(2026, 1, 2),
                    cadence_days=0,
                )

    def test_num_periods_zero_returns_empty(self, app, db, seed_user):
        """num_periods=0 returns an empty list."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=0,
            )
            assert periods == []

    def test_num_periods_one_returns_single_period(self, app, db, seed_user):
        """num_periods=1 returns exactly one period."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=1,
            )
            db.session.commit()

            assert len(periods) == 1
            assert periods[0].period_index == 0
            assert periods[0].start_date == date(2026, 1, 2)
            assert periods[0].end_date == date(2026, 1, 15)


# ---------------------------------------------------------------------------
# TestGetCurrentPeriod
# ---------------------------------------------------------------------------


class TestGetCurrentPeriod:
    """Tests for get_current_period()."""

    def test_returns_period_containing_date(self, app, db, seed_user, seed_periods):
        """A date within the first period (Jan 2-15) should return it."""
        with app.app_context():
            period = pay_period_service.get_current_period(
                seed_user["user"].id,
                as_of=date(2026, 1, 5),
            )
            assert period is not None
            assert period.period_index == 0
            assert period.start_date == date(2026, 1, 2)
            assert period.end_date == date(2026, 1, 15)

    def test_no_period_contains_date_returns_none(self, app, db, seed_user, seed_periods):
        """A date before all periods returns None."""
        with app.app_context():
            period = pay_period_service.get_current_period(
                seed_user["user"].id,
                as_of=date(2020, 1, 1),
            )
            assert period is None

    def test_custom_as_of_date(self, app, db, seed_user, seed_periods):
        """Targeting the 3rd period (index 2) returns the correct period."""
        with app.app_context():
            # Period 2: starts Jan 2 + 28 days = Jan 30, ends Feb 12.
            period = pay_period_service.get_current_period(
                seed_user["user"].id,
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

    def test_returns_correct_window_by_index(self, app, db, seed_user, seed_periods):
        """Requesting start_index=2, count=3 returns indices 2, 3, 4."""
        with app.app_context():
            periods = pay_period_service.get_periods_in_range(
                seed_user["user"].id,
                start_index=2,
                count=3,
            )
            assert len(periods) == 3
            assert [p.period_index for p in periods] == [2, 3, 4]

    def test_range_beyond_available_returns_partial(self, app, db, seed_user, seed_periods):
        """Requesting past the end returns only what exists."""
        with app.app_context():
            periods = pay_period_service.get_periods_in_range(
                seed_user["user"].id,
                start_index=8,
                count=5,
            )
            assert len(periods) == 2
            assert [p.period_index for p in periods] == [8, 9]


# ---------------------------------------------------------------------------
# TestGetNextPeriod
# ---------------------------------------------------------------------------


class TestGetNextPeriod:
    """Tests for get_next_period()."""

    def test_returns_immediately_following_period(self, app, db, seed_user, seed_periods):
        """Next of period[3] should be period[4]."""
        with app.app_context():
            current = seed_periods[3]
            next_p = pay_period_service.get_next_period(current)
            assert next_p is not None
            assert next_p.period_index == 4

    def test_last_period_returns_none(self, app, db, seed_user, seed_periods):
        """Next of the last period (index 9) returns None."""
        with app.app_context():
            last = seed_periods[9]
            next_p = pay_period_service.get_next_period(last)
            assert next_p is None


# ---------------------------------------------------------------------------
# TestGetAllPeriods
# ---------------------------------------------------------------------------


class TestGetAllPeriods:
    """Tests for get_all_periods()."""

    def test_returns_all_periods_ordered_by_index(self, app, db, seed_user, seed_periods):
        """Should return all 10 periods ordered 0..9."""
        with app.app_context():
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            assert len(periods) == 10
            assert [p.period_index for p in periods] == list(range(10))


# ---------------------------------------------------------------------------
# TestNegativeAndBoundaryPaths
# ---------------------------------------------------------------------------


class TestNegativeAndBoundaryPaths:
    """Negative-path and boundary-condition tests for pay period service.

    Covers: negative num_periods, date boundary precision on start/end dates,
    out-of-range and negative index queries, and large batch generation.
    """

    def test_negative_num_periods_behavior(self, app, db, seed_user):
        """num_periods=-1 produces an empty list because range(-1) yields nothing.

        A UI bug or API misuse could pass negative counts. The service must
        not create phantom periods or crash.
        """
        with app.app_context():
            # range(-1) produces an empty iterator, so no periods are created.
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=-1,
            )
            assert periods == []

    def test_get_current_period_exact_start_date(self, app, db, seed_user, seed_periods):
        """get_current_period with as_of equal to a period's start_date returns that period.

        Off-by-one on date boundaries is a classic bug. The first day of a
        period must be included in that period, not the previous one.
        """
        with app.app_context():
            # Period 0 starts on 2026-01-02.
            period = pay_period_service.get_current_period(
                seed_user["user"].id,
                as_of=date(2026, 1, 2),
            )
            assert period is not None
            assert period.period_index == 0
            assert period.start_date == date(2026, 1, 2)

    def test_get_current_period_exact_end_date(self, app, db, seed_user, seed_periods):
        """get_current_period with as_of equal to a period's end_date returns that period.

        The last day of a period must be included in that period. If the
        boundary were exclusive, the user would see no current period on the
        last day of a pay cycle.
        """
        with app.app_context():
            # Period 0 ends on 2026-01-15 (start + 13 days).
            period = pay_period_service.get_current_period(
                seed_user["user"].id,
                as_of=date(2026, 1, 15),
            )
            assert period is not None
            assert period.period_index == 0
            assert period.end_date == date(2026, 1, 15)

    def test_get_current_period_after_all_periods(self, app, db, seed_user, seed_periods):
        """get_current_period returns None for a date after all generated periods.

        With 10 periods starting 2026-01-02 at 14-day cadence, the last period
        (index 9) ends on 2026-05-21. A date of 2026-05-22 is outside all ranges.
        Ensures the service does not crash or return a wrong period when the
        date is outside all generated ranges.
        """
        with app.app_context():
            # Period 9: start = 2026-05-08, end = 2026-05-21.
            period = pay_period_service.get_current_period(
                seed_user["user"].id,
                as_of=date(2026, 5, 22),
            )
            assert period is None

    def test_get_periods_in_range_start_beyond_available(
        self, app, db, seed_user, seed_periods
    ):
        """get_periods_in_range with start_index beyond all periods returns empty list.

        A race condition or stale UI could request a range beyond what exists.
        seed_periods has indices 0-9; requesting start_index=15 finds nothing.
        """
        with app.app_context():
            periods = pay_period_service.get_periods_in_range(
                seed_user["user"].id,
                start_index=15,
                count=5,
            )
            assert periods == []

    def test_get_periods_in_range_negative_start(self, app, db, seed_user, seed_periods):
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
                seed_user["user"].id,
                start_index=-1,
                count=5,
            )
            # Returns 4 periods (indices 0-3), not 5.
            assert len(periods) == 4
            assert [p.period_index for p in periods] == [0, 1, 2, 3]

    def test_generate_large_batch_104_periods(self, app, db, seed_user):
        """Generating 104 periods (2 years biweekly) produces correct count and dates.

        Production generates 52-104 periods. This verifies no performance or
        correctness issues at scale.
        """
        with app.app_context():
            start = date(2026, 1, 2)
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=start,
                num_periods=104,
                cadence_days=14,
            )
            db.session.commit()

            assert len(periods) == 104
            assert periods[-1].period_index == 103

            # Verify the last period's start_date.
            expected_last_start = start + timedelta(days=103 * 14)
            assert periods[-1].start_date == expected_last_start

            # Every period has end_date = start_date + 13 days.
            for p in periods:
                assert p.end_date == p.start_date + timedelta(days=13)
