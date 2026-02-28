"""
Shekel Budget App — Pay Period Service Tests

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
        """Generate 5 periods — assert count and 14-day spans."""
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
        """A date within the first period (Jan 2–15) should return it."""
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
