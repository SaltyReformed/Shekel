"""
Shekel Budget App -- Pay Period Service Tests

Integration tests for the pay period service that generates and queries
biweekly pay periods.  All functions use DB queries, so these tests
exercise the service against a real PostgreSQL database using the
shared app/db/bare_user fixtures from conftest.
"""

from datetime import date, timedelta

import pytest

from app.exceptions import ValidationError
from app.models.pay_period import MIN_MATERIALISABLE_CADENCE_DAYS
from app.models.pay_schedule import CADENCE_DAYS_MIN
from app.schemas.validation.pay_periods import CADENCE_DAYS_FORM_MIN
from app.services import pay_period_service, pay_schedule_service


# ---------------------------------------------------------------------------
# TestGeneratePayPeriods
# ---------------------------------------------------------------------------


class TestGeneratePayPeriods:
    """Tests for generate_pay_periods()."""

    def test_generates_correct_count_with_14_day_cadence(self, app, db, bare_user):
        """Generate 5 periods -- assert count and 14-day spans."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=bare_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=5,
                cadence_days=14,
            )
            db.session.commit()

            assert len(periods) == 5
            for p in periods:
                span = (p.end_date - p.start_date).days + 1
                assert span == 14

    def test_period_indices_are_sequential(self, app, db, bare_user):
        """Generated periods should have indices 0..n-1."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=bare_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=5,
            )
            db.session.commit()

            indices = [p.period_index for p in periods]
            assert indices == [0, 1, 2, 3, 4]

    def test_end_date_equals_start_plus_cadence_minus_one(self, app, db, bare_user):
        """end_date should be start_date + 13 days for 14-day cadence."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=bare_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=3,
                cadence_days=14,
            )
            db.session.commit()

            for p in periods:
                assert p.end_date == p.start_date + timedelta(days=13)

    def test_duplicate_start_date_silently_skipped(self, app, db, bare_user):
        """Re-generating with an overlapping start_date skips duplicates."""
        with app.app_context():
            user_id = bare_user["user"].id

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

    def test_appending_to_existing_periods(self, app, db, bare_user):
        """New periods after existing range get sequential indices."""
        with app.app_context():
            user_id = bare_user["user"].id

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

    def test_offset_start_before_existing_rejected(self, app, db, bare_user):
        """An offset batch whose start predates the latest existing coverage is
        rejected (DH-#39, bound tightened by fix I).

        The new periods would receive the highest period_index values while
        carrying earlier dates -- out of calendar order AND overlapping the
        existing periods' date ranges -- which silently drops their
        transactions from as-of balances.  The whole batch is refused before
        any row is written, so the original schedule is untouched.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            # Jan 2, Jan 16, Jan 30 (14-day cadence).  The latest period
            # starts Jan 30 and ends Jan 30 + 13 = Feb 12, so the
            # overlap bound (fix I) is the latest END date, 2026-02-12,
            # not the latest START date 2026-01-30.
            pay_period_service.generate_pay_periods(
                user_id=user_id, start_date=date(2026, 1, 2), num_periods=3,
            )
            db.session.commit()

            # Offset start Jan 9 falls among the existing periods.
            with pytest.raises(ValidationError) as excinfo:
                pay_period_service.generate_pay_periods(
                    user_id=user_id, start_date=date(2026, 1, 9), num_periods=4,
                )
            # The message names the latest existing END date as the bound
            # (fix I): Jan 30 + 13 days = 2026-02-12 (was 2026-01-30 when
            # the guard compared only start dates).
            assert "2026-02-12" in str(excinfo.value)
            # Nothing was created -- still exactly the original 3.
            assert len(pay_period_service.get_all_periods(user_id)) == 3

    def test_batch_starting_within_final_period_rejected(self, app, db, bare_user):
        """A batch starting INSIDE the last existing period is rejected (fix I).

        The audit's overlap-guard hole: with periods Jan 2 / Jan 16 / Jan 30
        (14-day cadence), the last period spans Jan 30 - Feb 12.  A new batch
        starting Feb 5 has a start date AFTER the latest existing START
        (Jan 30), so the old start-date-only guard ACCEPTED it -- but Feb 5
        falls WITHIN the Jan 30 period's [start, end] span, producing two
        periods covering Feb 5 - Feb 12 and a nondeterministic
        get_current_period.  The end-date bound (Feb 12) now rejects it.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_service.generate_pay_periods(
                user_id=user_id, start_date=date(2026, 1, 2), num_periods=3,
            )
            db.session.commit()

            # Feb 5 > latest start Jan 30 (old guard passed) but
            # <= latest end Feb 12 (new guard rejects).
            with pytest.raises(ValidationError) as excinfo:
                pay_period_service.generate_pay_periods(
                    user_id=user_id, start_date=date(2026, 2, 5), num_periods=4,
                )
            assert "2026-02-12" in str(excinfo.value)
            # Nothing created -- the original 3 are untouched.
            assert len(pay_period_service.get_all_periods(user_id)) == 3

    def test_batch_starting_after_final_period_end_accepted(
        self, app, db, bare_user,
    ):
        """A batch starting the day AFTER coverage ends is accepted (fix I).

        Periods Jan 2 / Jan 16 / Jan 30 end at Feb 12.  A new batch
        starting Feb 13 (one day after coverage ends, the natural next
        payday) does not overlap, so it extends the schedule forward with
        sequential indices.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_service.generate_pay_periods(
                user_id=user_id, start_date=date(2026, 1, 2), num_periods=3,
            )
            db.session.commit()

            new = pay_period_service.generate_pay_periods(
                user_id=user_id, start_date=date(2026, 2, 13), num_periods=2,
            )
            db.session.commit()

            assert len(new) == 2
            assert [p.period_index for p in new] == [3, 4]
            assert [p.start_date for p in new] == [
                date(2026, 2, 13), date(2026, 2, 27),
            ]
            # Index order still matches calendar order across the full set.
            starts = [
                p.start_date
                for p in pay_period_service.get_all_periods(user_id)
            ]
            assert starts == sorted(starts)

    def test_larger_count_rerun_from_same_start_extends_forward(
        self, app, db, bare_user
    ):
        """Re-running with the SAME start and a larger count is allowed.

        The overlapping prefix is dup-skipped and the genuinely-new periods
        all fall after the latest existing payday, so they extend forward and
        the period_index == calendar-order invariant still holds (DH-#39 only
        rejects batches that would break it).
        """
        with app.app_context():
            user_id = bare_user["user"].id
            # Jan 2, Jan 16, Jan 30.
            pay_period_service.generate_pay_periods(
                user_id=user_id, start_date=date(2026, 1, 2), num_periods=3,
            )
            db.session.commit()

            # Same start, larger count: first 3 dup-skipped; Feb 13, Feb 27 new.
            new = pay_period_service.generate_pay_periods(
                user_id=user_id, start_date=date(2026, 1, 2), num_periods=5,
            )
            db.session.commit()

            assert [p.period_index for p in new] == [3, 4]
            assert [p.start_date for p in new] == [
                date(2026, 2, 13), date(2026, 2, 27),
            ]
            # Index order still matches calendar order across the full set.
            starts = [p.start_date for p in pay_period_service.get_all_periods(user_id)]
            assert starts == sorted(starts)

    def test_invalid_start_date_raises_error(self, app, db, bare_user):
        """Passing a non-date start_date raises ValidationError."""
        with app.app_context():
            with pytest.raises(ValidationError, match="start_date must be a date"):
                pay_period_service.generate_pay_periods(
                    user_id=bare_user["user"].id,
                    start_date="2026-01-02",
                    num_periods=1,
                )

    def test_cadence_days_less_than_one_raises_error(self, app, db, bare_user):
        """cadence_days=0 raises ValidationError.

        The message moved at plan step X-ad-a: the floor is no longer 1 but
        :data:`~app.models.pay_period.MIN_MATERIALISABLE_CADENCE_DAYS`, because
        1 was accepted here and then refused by ``ck_pay_periods_date_order``
        as an unhandled 500.  Zero is still refused, for the same reason it
        always was.
        """
        with app.app_context():
            with pytest.raises(ValidationError, match="at least 2"):
                pay_period_service.generate_pay_periods(
                    user_id=bare_user["user"].id,
                    start_date=date(2026, 1, 2),
                    cadence_days=0,
                )

    def test_num_periods_zero_is_refused(self, app, db, bare_user):
        """num_periods=0 raises rather than quietly creating nothing.

        **This assertion INVERTED at plan step X-ad-a, and the old one is
        recorded here rather than deleted.**  It read ``assert periods == []``
        and called that the contract; an adversarial review showed what the
        silence cost.  ``range(0)`` yields nothing, so the call succeeded, and
        the caller found out several statements later -- at registration, as
        ``create_account`` complaining that the owner had no pay periods, a
        message naming neither the input nor anything the caller could change.
        A batch that creates nothing is a caller mistake, not a no-op, and it
        is now refused where it is made.
        """
        with app.app_context():
            with pytest.raises(ValidationError, match="between 1 and 260"):
                pay_period_service.generate_pay_periods(
                    user_id=bare_user["user"].id,
                    start_date=date(2026, 1, 2),
                    num_periods=0,
                )

    def test_num_periods_one_returns_single_period(self, app, db, bare_user):
        """num_periods=1 returns exactly one period."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=bare_user["user"].id,
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
# TestGetCurrentAndFuturePeriods
# ---------------------------------------------------------------------------


class TestGetCurrentAndFuturePeriods:
    """Tests for get_current_and_future_periods().

    bare_periods are 10 biweekly periods from 2026-01-02:
      index 0: Jan 2-15,  index 1: Jan 16-29,  index 2: Jan 30-Feb 12, ...
    """

    def test_excludes_ended_periods(self, app, db, bare_user, bare_periods):
        """Periods whose end_date is before as_of are excluded.

        as_of=2026-02-01 sits in period 2 (Jan 30-Feb 12); periods 0 and
        1 have ended, so only 2..9 are returned.
        """
        with app.app_context():
            result = pay_period_service.get_current_and_future_periods(
                bare_user["user"].id, as_of=date(2026, 2, 1),
            )
            assert [p.period_index for p in result] == [2, 3, 4, 5, 6, 7, 8, 9]

    def test_current_period_included_on_its_end_date(
        self, app, db, bare_user, bare_periods,
    ):
        """A period whose end_date equals as_of counts as current.

        as_of=2026-01-15 is period 0's end_date; end_date >= as_of holds,
        so every period (0..9) is returned.
        """
        with app.app_context():
            result = pay_period_service.get_current_and_future_periods(
                bare_user["user"].id, as_of=date(2026, 1, 15),
            )
            assert [p.period_index for p in result] == list(range(10))

    def test_include_period_id_forces_an_ended_period(
        self, app, db, bare_user, bare_periods,
    ):
        """include_period_id adds one ended period without un-excluding others.

        With as_of in period 2 and include_period_id = period 0, the
        result is [0, 2, 3, ..., 9]: period 0 is forced back in, but
        period 1 (also ended, not forced) stays excluded.
        """
        with app.app_context():
            result = pay_period_service.get_current_and_future_periods(
                bare_user["user"].id,
                as_of=date(2026, 2, 1),
                include_period_id=bare_periods[0].id,
            )
            assert [p.period_index for p in result] == [
                0, 2, 3, 4, 5, 6, 7, 8, 9,
            ]


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
# TestGetNextPeriod
# ---------------------------------------------------------------------------


class TestGetNextPeriod:
    """Tests for get_next_period()."""

    def test_returns_immediately_following_period(self, app, db, bare_user, bare_periods):
        """Next of period[3] should be period[4]."""
        with app.app_context():
            current = bare_periods[3]
            next_p = pay_period_service.get_next_period(current)
            assert next_p is not None
            assert next_p.period_index == 4

    def test_last_period_returns_none(self, app, db, bare_user, bare_periods):
        """Next of the last period (index 9) returns None."""
        with app.app_context():
            last = bare_periods[9]
            next_p = pay_period_service.get_next_period(last)
            assert next_p is None


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
    """Negative-path and boundary-condition tests for pay period service.

    Covers: negative num_periods, date boundary precision on start/end dates,
    out-of-range and negative index queries, and large batch generation.
    """

    def test_negative_num_periods_is_refused(self, app, db, bare_user):
        """num_periods=-1 raises rather than relying on range(-1) being empty.

        A UI bug or API misuse could pass a negative count.  The service must
        not create phantom periods -- and, since plan step X-ad-a, must not
        pretend the call succeeded either.  **The previous assertion was
        ``periods == []``**, which graded an ACCIDENT: nothing refused the
        value, ``range(-1)`` simply happened to yield nothing, so the caller
        got a success for an operation that did not happen.  See
        ``TestGeneratePayPeriods::test_num_periods_zero_is_refused`` for what
        that silence cost at the registration door.
        """
        with app.app_context():
            with pytest.raises(ValidationError, match="between 1 and 260"):
                pay_period_service.generate_pay_periods(
                    user_id=bare_user["user"].id,
                    start_date=date(2026, 1, 2),
                    num_periods=-1,
                )

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

    def test_generate_large_batch_104_periods(self, app, db, bare_user):
        """Generating 104 periods (2 years biweekly) produces correct count and dates.

        Production generates 52-104 periods. This verifies no performance or
        correctness issues at scale.
        """
        with app.app_context():
            start = date(2026, 1, 2)
            periods = pay_period_service.generate_pay_periods(
                user_id=bare_user["user"].id,
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


# ---------------------------------------------------------------------------
# TestEstablishSchedule
# ---------------------------------------------------------------------------


class TestTheWriterRefusesWhatItCannotMaterialise:
    """``reject_unmaterialisable_batch`` -- plan step X-ad-a.

    Two preconditions this module had never stated, both measured as real
    failures rather than argued:

    * A cadence of 1 made ``end_date == start_date``, which
      ``ck_pay_periods_date_order`` refuses -- an unhandled ``IntegrityError``
      500 reproduced on both the settings form and the registration form.
      **Not because a one-day pay cycle is illegitimate**: it is legal, and
      pay-calendar step C4 legalises it by dropping the authored column.  What
      cannot hold one is an authored ``end_date``.
    * ``num_periods`` was bounded by no service at all, so a non-form caller
      could ask for zero (failing several statements later under a message
      about accounts) or for a hundred thousand.
    """

    def test_a_one_day_cadence_is_refused_before_the_check_sees_it(
        self, app, db, bare_user,
    ):
        """Cadence 1 is a ValidationError, not a CheckViolation 500.

        Arithmetic: ``end_date = start_date + (cadence_days - 1)``, so at a
        cadence of 1 a period starting 2026-01-02 would end 2026-01-02 and
        ``CHECK (start_date < end_date)`` rejects the INSERT.
        """
        with app.app_context():
            with pytest.raises(ValidationError, match="at least 2"):
                pay_period_service.generate_pay_periods(
                    user_id=bare_user["user"].id,
                    start_date=date(2026, 1, 2),
                    num_periods=2,
                    cadence_days=1,
                )
            db.session.rollback()
            assert pay_period_service.get_all_periods(
                bare_user["user"].id,
            ) == []

    def test_a_two_day_cadence_is_accepted(self, app, db, bare_user):
        """The floor is INCLUSIVE, and this is the control for the test above.

        Without it, a refusal that also rejected 2 -- or 30 -- would look
        identical.  Two paydays two days apart give one-day-plus-one periods:
        01-02..01-03 and 01-04..01-05.
        """
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=bare_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=2,
                cadence_days=2,
            )
            db.session.commit()
            assert [(p.start_date, p.end_date) for p in periods] == [
                (date(2026, 1, 2), date(2026, 1, 3)),
                (date(2026, 1, 4), date(2026, 1, 5)),
            ]

    @pytest.mark.parametrize("count", [0, -1, 261, 100_000])
    def test_a_batch_size_outside_the_policy_is_refused(
        self, app, db, bare_user, count,
    ):
        """Zero, negative and oversized batches refuse and write nothing.

        Zero is the one that mattered: it created no periods, no error, and
        surfaced far downstream.  100000 is 383 years of fortnights in one
        transaction.
        """
        with app.app_context():
            with pytest.raises(ValidationError, match="between 1 and 260"):
                pay_period_service.generate_pay_periods(
                    user_id=bare_user["user"].id,
                    start_date=date(2026, 1, 2),
                    num_periods=count,
                    cadence_days=14,
                )
            db.session.rollback()
            assert pay_period_service.get_all_periods(
                bare_user["user"].id,
            ) == []

    def test_the_form_bound_and_the_writer_bound_agree(self):
        """The schema's cadence floor IS the writer's, not the column's.

        This is what makes ``routes/pay_periods.generate``'s error attribution
        provable: the schema bounds the cadence and the batch to exactly what
        the writer accepts, so the only service refusal that can reach that
        handler is the forward-only start-date one it renders on ``start_date``.
        """
        assert CADENCE_DAYS_FORM_MIN == MIN_MATERIALISABLE_CADENCE_DAYS
        assert CADENCE_DAYS_FORM_MIN > CADENCE_DAYS_MIN


class TestEstablishSchedule:
    """``establish_schedule`` is generate and remember-the-cadence as ONE call.

    Added at plan step X-ad-a.  Two doors need the pair -- the
    ``/pay-periods/generate`` form and ``auth_service.register_user`` -- and
    writing it twice is how an owner ends up with paydays and no
    ``budget.pay_schedule`` row, which is pay-calendar finding **P8**:
    ``resolve_cadence`` then infers the cadence back out of a period's stored
    LENGTH, the very derivation that arc is removing.
    """

    def test_creates_the_periods_and_the_schedule_row(self, app, db, bare_user):
        """One call leaves both the periods and the persisted cadence.

        Arithmetic: 4 periods from 2026-01-02 at a 7-day cadence, so the
        starts are 01-02 / 01-09 / 01-16 / 01-23 and each period ends six days
        after it starts.  The cadence is deliberately NOT 14 -- an on-default
        fixture cannot tell "the cadence was stored" from "the default was".
        """
        user_id = bare_user["user"].id
        with app.app_context():
            created = pay_period_service.establish_schedule(
                user_id=user_id,
                first_payday=date(2026, 1, 2),
                num_periods=4,
                cadence_days=7,
            )
            db.session.commit()

            assert [p.start_date for p in created] == [
                date(2026, 1, 2), date(2026, 1, 9),
                date(2026, 1, 16), date(2026, 1, 23),
            ]
            assert [p.end_date for p in created] == [
                date(2026, 1, 8), date(2026, 1, 15),
                date(2026, 1, 22), date(2026, 1, 29),
            ]
            assert pay_schedule_service.get_schedule(user_id).cadence_days == 7

    def test_refuses_an_unstorable_cadence_without_creating_periods(
        self, app, db, bare_user,
    ):
        """A cadence the schedule column refuses creates no periods either.

        The generate runs first, so a naive composition would leave the owner
        with 366-day pay periods and no schedule row -- half an operation,
        committed by whoever calls ``db.session.commit()`` next.  Here the
        refusal happens inside the same call and the caller's rollback (or, in
        a route, its error path) sees nothing to keep.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            with pytest.raises(ValidationError, match="between 1 and 365"):
                pay_period_service.establish_schedule(
                    user_id=user_id,
                    first_payday=date(2026, 1, 2),
                    num_periods=2,
                    cadence_days=366,
                )
            db.session.rollback()
            assert pay_period_service.get_all_periods(user_id) == []
            assert pay_schedule_service.get_schedule(user_id) is None

    def test_forward_only_guard_still_applies(self, app, db, bare_user):
        """A batch overlapping existing coverage is refused, cadence untouched.

        ``establish_schedule`` composes ``generate_pay_periods``; it does not
        weaken it.  The second call starts one day INSIDE the first batch's
        coverage (2026-01-02 + 27 days of coverage ends 01-29), so the
        forward-only guard refuses -- and the stored cadence stays at the
        value the successful call wrote rather than being advanced by a batch
        that created nothing.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_service.establish_schedule(
                user_id=user_id,
                first_payday=date(2026, 1, 2),
                num_periods=2,
                cadence_days=14,
            )
            db.session.flush()

            with pytest.raises(ValidationError, match="must start after"):
                pay_period_service.establish_schedule(
                    user_id=user_id,
                    first_payday=date(2026, 1, 20),
                    num_periods=2,
                    cadence_days=7,
                )
            assert pay_schedule_service.get_schedule(user_id).cadence_days == 14
