"""Tests for "Add earlier paychecks" (plan step ``pay_calendar:C18-b``).

Ruling **R-PC87**: an action beside Extend asks only how many paychecks to add
before the owner's first and records the paydays their earliest rhythm projects
just below it.  Ruling **R-PC105** (narrowing R-PC87's "moves nothing" to
paychecks): it moves that rhythm's phase down to the earliest payday it adds,
the same rhythm on the same grid.  Ruling **R-PC104**: a payday below a STATED
history is refused, by the one predicate the history setter asks.  Ruling
**R-PC103**: the forms that state a start keep their date check, with one
message per case.

Four contracts, one class each:

* the PRODUCER (``pay_calendar.earlier_paydays``) -- pure, over every cadence
  kind and convention: the days are the backward count's, and the re-phased
  era plans every payday it planned before;
* the REFUSAL a stated form meets (``pay_period_batch.reject_backward_payday``)
  -- the two messages, and the inside case's span taken from the derivation;
* the WRITER and ADMIN doors (``pay_period_write.prepend_paydays``,
  ``pay_period_admin.add_earlier_pay_periods``) -- what they record, what they
  move, what they refuse and that a refusal writes nothing -- and the era
  writer's own bound (``pay_era_write.rephase_earliest_era`` moves the phase
  down only);
* the HAZARD R-PC105 exists for -- a regenerate keeping only earlier paychecks
  restating a rhythm from the old phase or just past it -- driven through the
  real doors.

``POST /pay-periods/earlier`` is graded in
``tests/test_routes/test_pay_periods_earlier.py``.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.enums import BusinessDayShiftEnum
from app.exceptions import ValidationError
from app.extensions import db as _db
from app import ref_cache
from app.models.pay_era import PayEra
from app.models.pay_period import PayPeriod
from app.services import (
    pay_era_write,
    pay_period_admin,
    pay_period_batch,
    pay_period_write,
    pay_schedule_service,
)
from app.services.pay_calendar import (
    PayCalendar,
    PayCalendarError,
    calendar_for,
    earlier_paydays,
    first_payday_of,
    paydays_in_month_through,
    paydays_in_year_before,
    planned_paydays_after,
    projected_payday,
    schedule_for,
)
from app.services.pay_calendar._derive import validate_eras
from app.services.pay_rhythm import Era, FixedDays, Monthly, Rhythm, SemiMonthly
from tests._test_helpers import (
    all_periods,
    assert_pay_period_invariants,
    era_of,
    freeze_today,
    rhythm_of,
)

NONE = BusinessDayShiftEnum.NONE
PRIOR = BusinessDayShiftEnum.PRIOR
NEXT = BusinessDayShiftEnum.NEXT

#: One era per cadence kind and convention the producer must re-phase without
#: moving a payday.  2030-12-12 is on a 14-day grid through 2030-11-28,
#: Thanksgiving, so one step back is a closed day under both displacing
#: conventions; the month cases opening on a 31st step back into a February
#: that CLAMPS the day, which is the shape ``nominal_day`` exists to record
#: -- as does the 15th-and-31st pair from a 15th, whose step back is
#: February's clamped upper day -- and the monthly 15th steps back onto a
#: day nothing clamps.
ERA_CASES = {
    "every 14 days, none": Era(date(2026, 1, 2), Rhythm(FixedDays(14), NONE)),
    "every 14 days, prior, back over Thanksgiving": Era(
        date(2030, 12, 12), Rhythm(FixedDays(14), PRIOR),
    ),
    "every 14 days, next, back over Thanksgiving": Era(
        date(2030, 12, 12), Rhythm(FixedDays(14), NEXT),
    ),
    "every 7 days": Era(date(2026, 1, 2), Rhythm(FixedDays(7), NONE)),
    "monthly on the 31st": Era(date(2026, 3, 31), Rhythm(Monthly(31), NONE)),
    "monthly on the 15th": Era(date(2026, 3, 15), Rhythm(Monthly(15), NONE)),
    "15th and 31st, from a 31st": Era(
        date(2026, 3, 31), Rhythm(SemiMonthly((15, 31)), NONE),
    ),
    "15th and 31st, from a 15th": Era(
        date(2026, 3, 15), Rhythm(SemiMonthly((15, 31)), NONE),
    ),
    "1st and 15th": Era(date(2026, 3, 1), Rhythm(SemiMonthly((1, 15)), NONE)),
}

#: How many earlier paychecks each producer case asks for.
COUNTS = (1, 2, 5, 13)


def _grid_below(era, opening, count):
    """Return the *count* grid paydays below *opening*, found by brute force.

    The oracle the producer is graded against: every step of the era's grid
    over a window far wider than the answer, displaced, kept below the
    opening, the last *count* taken.  It shares ``projected_payday`` with the
    producer -- the displacement has one producer -- and nothing else, so it
    grades the STEP choice, which is the producer's own arithmetic.
    """
    below = [
        payday
        for payday in (
            projected_payday(era.effective_from, era.rhythm, steps)
            for steps in range(-(count + 40), 40)
        )
        if payday < opening
    ]
    return tuple(below[-count:])


class TestTheProducerIsTheBackwardCountsGrid:
    """``pay_calendar.earlier_paydays``: the days, and the era re-phased onto them."""

    @pytest.mark.parametrize("name", sorted(ERA_CASES))
    @pytest.mark.parametrize("count", COUNTS)
    def test_the_paydays_are_the_grid_days_just_below_the_opening(
        self, name, count,
    ):
        """The *count* planned paydays nearest below the record, ascending."""
        era = ERA_CASES[name]
        opening = first_payday_of(era)

        _rephased, paydays = earlier_paydays((era,), opening, count)

        assert paydays == _grid_below(era, opening, count)
        assert len(paydays) == count
        assert all(earlier < later for earlier, later in zip(paydays, paydays[1:]))
        assert paydays[-1] < opening

    @pytest.mark.parametrize("name", sorted(ERA_CASES))
    @pytest.mark.parametrize("count", COUNTS)
    def test_the_rephased_era_plans_every_payday_it_planned_before(
        self, name, count,
    ):
        """R-PC105: same rhythm, same grid -- a step renumbering and nothing else.

        Checked 120 steps either side, which crosses a February clamp for
        the month kinds and Thanksgiving for the displaced ones.
        """
        era = ERA_CASES[name]
        opening = first_payday_of(era)

        rephased, paydays = earlier_paydays((era,), opening, count)

        assert rephased.rhythm == era.rhythm
        assert rephased.effective_from < era.effective_from
        for steps in range(-120, 120):
            assert projected_payday(
                rephased.effective_from, rephased.rhythm, steps,
            ) == projected_payday(era.effective_from, era.rhythm, steps - count)
        # Its first paycheck is the earliest one the door records, and it is a
        # sequence the calendar will derive from.
        assert first_payday_of(rephased) == paydays[0]
        validate_eras((rephased,))
        pay_era_write.reject_phase_off_grid(
            rephased.effective_from, rephased.rhythm.cadence,
        )

    @pytest.mark.parametrize("name", sorted(ERA_CASES))
    def test_the_door_records_exactly_the_days_the_backward_count_stood_on(
        self, name,
    ):
        """Under a STATED history, adding the paychecks moves no count.

        The backward rhythm already counts these days (plan step
        balance:X-bh-2); recording them makes them saved instead, and every
        month ordinal and year-to-date the paycheck engine reads is the same
        tuple before and after -- which is why the move is ``$0.00`` for an
        owner who has said when their paychecks started, and why the
        producer must share the count's grid (rule 14).
        """
        era = ERA_CASES[name]
        opening = first_payday_of(era)
        record = [
            projected_payday(era.effective_from, era.rhythm, steps)
            for steps in range(0, 6)
        ]
        history = date(2020, 1, 1)
        before = PayCalendar.from_paydays(
            [(None, day) for day in record], (era,), 1, history,
        )

        rephased, paydays = earlier_paydays((era,), opening, 5)
        after = PayCalendar.from_paydays(
            [(None, day) for day in [*paydays, *record]], (rephased,), 1,
            history,
        )

        for asked in [*paydays, *record, record[-1] + timedelta(days=40)]:
            assert paydays_in_year_before(after, asked) == (
                paydays_in_year_before(before, asked)
            ), asked
            assert paydays_in_month_through(after, asked) == (
                paydays_in_month_through(before, asked)
            ), asked


class TestAStatedStartBelowTheRecordIsNamedForWhatItIs:
    """``pay_period_batch.reject_backward_payday``'s two messages (R-PC103).

    PC-499: a date before the first paycheck was refused with a message
    saying it would split a paycheck, which it cannot.  The check stays for
    the forms that state a start; each case now says what it is.
    """

    ERAS = (era_of(date(2026, 1, 2), 14),)
    RECORD = {date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30)}

    def test_a_day_before_the_first_paycheck_points_to_add_earlier(self):
        """The ruling's wording, verbatim, with this record's days."""
        with pytest.raises(ValidationError) as refused:
            pay_period_batch.reject_backward_payday(
                self.RECORD, [date(2025, 12, 19)], self.ERAS,
            )

        assert str(refused.value) == (
            "2025-12-19 is before your first paycheck (2026-01-02). To add "
            "paychecks before it, use Add earlier paychecks in Settings > "
            "Pay Periods."
        )

    def test_a_day_inside_a_paycheck_names_that_paycheck_and_the_floor(self):
        """The inside case names the containing span and the first day allowed."""
        with pytest.raises(ValidationError) as refused:
            pay_period_batch.reject_backward_payday(
                self.RECORD, [date(2026, 1, 20)], self.ERAS,
            )

        assert str(refused.value) == (
            "2026-01-20 falls inside a paycheck you already have (2026-01-16 "
            "to 2026-01-29), and splitting a paycheck isn't supported yet. "
            "Choose 2026-02-13 or later, when the next paycheck opens after "
            "your latest (2026-01-30, paid every 14 days)."
        )

    def test_the_latest_paychecks_span_ends_on_the_floors_eve(self):
        """The last paycheck has no recorded successor: it runs to the floor."""
        with pytest.raises(ValidationError) as refused:
            pay_period_batch.reject_backward_payday(
                self.RECORD, [date(2026, 2, 1)], self.ERAS,
            )

        assert "(2026-01-30 to 2026-02-12)" in str(refused.value)

    def test_the_span_is_the_DERIVED_one_where_a_payday_was_paid_early(self):
        """An early-paid record ends the paycheck before it a day early too.

        The 01-16 paycheck paid on 01-15 (ruling R-PC47's shape): the
        calendar closes 01-02's paycheck on 01-14, the day before the next
        RECORDED payday.  The plan's next payday after 01-02 is 01-16, so a
        span read off the plan instead would say 01-15 -- one day the owner's
        own calendar puts in the next paycheck.
        """
        record = {date(2026, 1, 2), date(2026, 1, 15), date(2026, 1, 30)}

        with pytest.raises(ValidationError) as refused:
            pay_period_batch.reject_backward_payday(
                record, [date(2026, 1, 10)], self.ERAS,
            )

        assert "(2026-01-02 to 2026-01-14)" in str(refused.value)

    def test_the_floor_itself_is_admitted(self):
        """The day the next paycheck opens is the first day a stated form may use."""
        pay_period_batch.reject_backward_payday(
            self.RECORD, [date(2026, 2, 13)], self.ERAS,
        )


def _record(user_id, first_payday, num_periods, rhythm):
    """Record a first schedule through the writer and commit it."""
    periods = pay_period_write.record_paydays(
        user_id=user_id, first_payday=first_payday,
        num_periods=num_periods, rhythm=rhythm,
    )
    _db.session.commit()
    return periods


def _stored_eras(user_id):
    """Return the owner's stored era rows as ``(effective_from, nominal_day, other_day)``."""
    return [
        (row.effective_from, row.nominal_day, row.other_day)
        for row in PayEra.query.filter_by(user_id=user_id)
        .order_by(PayEra.effective_from)
    ]


def _paydays(user_id):
    """Return the owner's recorded paydays, ascending."""
    return [period.start_date for period in all_periods(user_id)]


class TestTheDoorRecordsBelowAndMovesThePhase:
    """``pay_period_write.prepend_paydays`` / ``pay_period_admin.add_earlier_pay_periods``."""

    def test_it_records_the_paydays_below_the_first_and_moves_the_phase(
        self, app, db, bare_user,
    ):
        """Two earlier paychecks on a fortnight: two rows, one era moved down."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 5, rhythm_of(14))

            created = pay_period_admin.add_earlier_pay_periods(user_id, 2)
            db.session.commit()

            assert [period.start_date for period in created] == [
                date(2025, 12, 5), date(2025, 12, 19),
            ]
            assert _paydays(user_id) == [
                date(2025, 12, 5), date(2025, 12, 19), date(2026, 1, 2),
                date(2026, 1, 16), date(2026, 1, 30), date(2026, 2, 13),
                date(2026, 2, 27),
            ]
            assert _stored_eras(user_id) == [(date(2025, 12, 5), None, None)]
            assert schedule_for(user_id).eras[0].rhythm == rhythm_of(14)
            assert_pay_period_invariants(db.session, user_id)

    def test_the_plan_past_the_record_does_not_move(self, app, db, bare_user):
        """Nothing above the record changes: the next planned paydays are the same."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 5, rhythm_of(14))
            latest = _paydays(user_id)[-1]
            before = list(zip(range(30), planned_paydays_after(
                schedule_for(user_id).eras, latest,
            )))

            pay_period_admin.add_earlier_pay_periods(user_id, 3)
            db.session.commit()

            after = list(zip(range(30), planned_paydays_after(
                schedule_for(user_id).eras, latest,
            )))
            assert after == before

    @pytest.mark.parametrize(
        ("rhythm", "first_payday", "earliest", "stored"),
        [
            # Monthly on the 31st from March: one back is February, clamped,
            # so the row records the meant day beside the clamped phase.
            (Rhythm(Monthly(31), NONE), date(2026, 3, 31), date(2026, 2, 28),
             (date(2026, 2, 28), 31, None)),
            # 15th and 31st from the 15th: one back is February's upper day,
            # clamped; the row stands for the upper member, 15 the other.
            (Rhythm(SemiMonthly((15, 31)), NONE), date(2026, 3, 15),
             date(2026, 2, 28), (date(2026, 2, 28), 31, 15)),
            # From the 31st, one back is the 15th, which nothing clamps.
            (Rhythm(SemiMonthly((15, 31)), NONE), date(2026, 3, 31),
             date(2026, 3, 15), (date(2026, 3, 15), None, 31)),
        ],
    )
    def test_a_month_kind_era_round_trips_its_clamped_phase(
        self, app, db, bare_user, rhythm, first_payday, earliest, stored,
    ):
        """The re-phased era is STORED as the same rhythm and read back as it."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, first_payday, 4, rhythm)
            latest = _paydays(user_id)[-1]
            plan_before = list(zip(range(24), planned_paydays_after(
                schedule_for(user_id).eras, latest,
            )))

            pay_period_admin.add_earlier_pay_periods(user_id, 1)
            db.session.commit()

            assert _paydays(user_id)[0] == earliest
            assert _stored_eras(user_id) == [stored]
            eras = schedule_for(user_id).eras
            assert eras[0].rhythm == rhythm
            assert list(zip(range(24), planned_paydays_after(eras, latest))) == (
                plan_before
            )
            calendar_for(user_id)

    def test_only_the_earliest_era_moves(self, app, db, bare_user):
        """A later era -- a rhythm changed going forward -- is left as it was."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 4, rhythm_of(14))
            # A weekly rhythm from the plan's next payday after the record
            # (01-02 .. 02-13 recorded, so the floor is 02-27).
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 27),
                num_periods=3, rhythm=rhythm_of(7),
            )
            db.session.commit()
            assert [era[0] for era in _stored_eras(user_id)] == [
                date(2026, 1, 2), date(2026, 2, 27),
            ]

            pay_period_admin.add_earlier_pay_periods(user_id, 1)
            db.session.commit()

            assert _stored_eras(user_id) == [
                (date(2025, 12, 19), None, None), (date(2026, 2, 27), None, None),
            ]
            eras = schedule_for(user_id).eras
            assert [era.rhythm for era in eras] == [rhythm_of(14), rhythm_of(7)]
            assert_pay_period_invariants(db.session, user_id)

    def test_a_second_add_continues_below_the_first(self, app, db, bare_user):
        """The door is repeatable: each run reads the record it left."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))

            pay_period_admin.add_earlier_pay_periods(user_id, 1)
            db.session.commit()
            pay_period_admin.add_earlier_pay_periods(user_id, 1)
            db.session.commit()

            assert _paydays(user_id)[:3] == [
                date(2025, 12, 5), date(2025, 12, 19), date(2026, 1, 2),
            ]
            assert _stored_eras(user_id) == [(date(2025, 12, 5), None, None)]

    def test_a_schedule_loaded_before_the_door_reads_the_moved_phase(
        self, app, db, bare_user,
    ):
        """The door expires the session, so a row loaded earlier is re-read.

        The phase moves by a bulk UPDATE that synchronises nothing, and
        ``PaySchedule.eras`` is view-only, so a schedule the request loaded
        BEFORE the door would keep naming the old phase (2026-01-02) unless
        ``_apply`` expires it -- read here with no commit between, since a
        commit would expire it anyway (review 2 of C18-b).
        """
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            schedule = pay_schedule_service.get_schedule(user_id)
            assert schedule.eras[0].effective_from == date(2026, 1, 2)

            pay_period_admin.add_earlier_pay_periods(user_id, 1)

            assert schedule.eras[0].effective_from == date(2025, 12, 19)


class TestThePhaseMovesDownOnly:
    """``pay_era_write.rephase_earliest_era`` refuses any move but down.

    Down is what makes the grid the one bound it asks: the earliest era
    moved down cannot reach a later era's day, pass it, or be left paying
    nothing.  A move UP could do all three, and the writer asks none of
    them, so it refuses the move rather than claiming a bound it does not
    hold (review 2 of C18-b).
    """

    def test_a_move_up_is_refused_and_writes_nothing(self, app, db, bare_user):
        """One step up, still on the grid: refused before the UPDATE."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            paydays, eras = _paydays(user_id), _stored_eras(user_id)
            earliest = schedule_for(user_id).eras[0]

            with pytest.raises(ValidationError, match="cannot move up"):
                pay_era_write.rephase_earliest_era(
                    user_id,
                    pay_era_write.EarliestRephase(
                        earliest=earliest, phase=date(2026, 1, 16),
                    ),
                )

            _unchanged(user_id, paydays, eras)

    def test_a_move_to_the_same_day_is_admitted(self, app, db, bare_user):
        """Equality is admitted: "at or below", as the Args state.

        The door reaches it for the one era the C17-a migration backfilled a
        cadence below the record, when a single paycheck is added.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            earliest = schedule_for(user_id).eras[0]

            pay_era_write.rephase_earliest_era(
                user_id,
                pay_era_write.EarliestRephase(
                    earliest=earliest, phase=date(2026, 1, 2),
                ),
            )
            db.session.commit()

            assert _stored_eras(user_id) == [(date(2026, 1, 2), None, None)]


def _unchanged(user_id, paydays, eras):
    """Assert a refused door left the owner's rows exactly as they were.

    Read after a COMMIT and a fresh session, so a staged write a rollback
    would have hidden cannot pass as a refusal that wrote nothing.
    """
    _db.session.commit()
    _db.session.remove()
    assert _paydays(user_id) == paydays
    assert _stored_eras(user_id) == eras


class TestTheDoorRefusesBeforeItWrites:
    """Every refusal is asked before a statement, and leaves nothing behind."""

    @pytest.mark.parametrize("count", [0, 261])
    def test_a_batch_outside_the_bound_is_refused(self, app, db, bare_user, count):
        """The batch bound every door shares."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            paydays, eras = _paydays(user_id), _stored_eras(user_id)

            with pytest.raises(ValidationError, match="between 1 and 260"):
                pay_period_admin.add_earlier_pay_periods(user_id, count)

            _unchanged(user_id, paydays, eras)

    def test_an_owner_with_no_paydays_is_told_to_generate_first(
        self, app, db, bare_user_with_cadence,
    ):
        """A rhythm with no record has no first paycheck to add before."""
        with app.app_context():
            user_id = bare_user_with_cadence["user"].id
            eras = _stored_eras(user_id)

            with pytest.raises(ValidationError) as refused:
                pay_period_admin.add_earlier_pay_periods(user_id, 1)

            assert str(refused.value) == (
                "Generate your first pay-period schedule before adding "
                "earlier paychecks."
            )
            _unchanged(user_id, [], eras)

    def test_an_owner_with_no_schedule_meets_the_calendars_refusal(
        self, app, db, bare_user,
    ):
        """No ``budget.pay_schedule`` row: the calendar's one refusal, as at extend."""
        with app.app_context():
            with pytest.raises(PayCalendarError):
                pay_period_admin.add_earlier_pay_periods(bare_user["user"].id, 1)

    def test_a_payday_below_the_apps_calendar_is_refused(self, app, db, bare_user):
        """27 yearly paychecks below 2026-01-02 reach 1999; 26 stay in 2000."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 2, rhythm_of(365))
            paydays, eras = _paydays(user_id), _stored_eras(user_id)

            with pytest.raises(ValidationError) as refused:
                pay_period_admin.add_earlier_pay_periods(user_id, 27)

            assert str(refused.value) == (
                "1999-01-09 is before 2000-01-01, the earliest day Shekel's "
                "calendar holds. Add fewer."
            )
            _unchanged(user_id, paydays, eras)

            pay_period_admin.add_earlier_pay_periods(user_id, 26)
            db.session.commit()
            assert _paydays(user_id)[0] == date(2000, 1, 9)

    def test_below_both_the_history_and_the_calendar_the_history_is_named(
        self, app, db, bare_user,
    ):
        """The history is asked FIRST, so its ruled message is the one read.

        A history stated ON the calendar's floor (2000-01-01) and 27 yearly
        paychecks below 2026-01-02, whose earliest (1999-01-09) is under
        both: asked the other way round, the calendar's message would hide
        the one ruling R-PC104 worded (review 2 of C18-b).
        """
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 2, rhythm_of(365))
            pay_schedule_service.set_history_opening(user_id, date(2000, 1, 1))
            db.session.commit()
            paydays, eras = _paydays(user_id), _stored_eras(user_id)

            with pytest.raises(ValidationError) as refused:
                pay_period_admin.add_earlier_pay_periods(user_id, 27)

            assert str(refused.value) == (
                "1999-01-09 is before 2000-01-01, the day you saved as when "
                "your paychecks started. Change that date first, or add fewer."
            )
            _unchanged(user_id, paydays, eras)

    def test_a_payday_below_a_stated_history_is_refused(self, app, db, bare_user):
        """R-PC104: nothing is added, in the ruling's words."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            pay_schedule_service.set_history_opening(user_id, date(2025, 12, 10))
            db.session.commit()
            paydays, eras = _paydays(user_id), _stored_eras(user_id)

            with pytest.raises(ValidationError) as refused:
                pay_period_admin.add_earlier_pay_periods(user_id, 2)

            assert str(refused.value) == (
                "2025-12-05 is before 2025-12-10, the day you saved as when "
                "your paychecks started. Change that date first, or add fewer."
            )
            _unchanged(user_id, paydays, eras)

    def test_a_payday_ON_the_stated_history_is_admitted(self, app, db, bare_user):
        """Equality is not below: the same edge the setter admits."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            pay_schedule_service.set_history_opening(user_id, date(2025, 12, 5))
            db.session.commit()

            pay_period_admin.add_earlier_pay_periods(user_id, 2)
            db.session.commit()

            assert _paydays(user_id)[0] == date(2025, 12, 5)

    def test_the_setter_and_the_door_share_one_predicate(self, app, db, bare_user):
        """After the door, the setter measures against the NEW first payday.

        R-PC104's "the same rule": the history setter refuses a day after
        the record's first payday, and the record's first payday is now the
        earliest one added -- so a history the door just admitted cannot be
        re-saved one day later.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            pay_period_admin.add_earlier_pay_periods(user_id, 1)
            db.session.commit()

            with pytest.raises(ValidationError, match="2025-12-19"):
                pay_schedule_service.set_history_opening(
                    user_id, date(2025, 12, 20),
                )
            assert pay_schedule_service.record_below_history(
                date(2025, 12, 19), date(2025, 12, 20),
            )
            assert not pay_schedule_service.record_below_history(
                date(2025, 12, 19), date(2025, 12, 19),
            )
            assert not pay_schedule_service.record_below_history(
                date(2025, 12, 19), None,
            )
            assert not pay_schedule_service.record_below_history(
                None, date(2025, 12, 19),
            )


#: The day these cases are pinned to: the owner's first recorded payday
#: (2026-06-19) is still ahead, and the one "Add earlier paychecks" adds
#: (2026-06-05) has started -- the only shape in which a regenerate keeps
#: nothing but earlier paychecks.
FROZEN_TODAY = date(2026, 6, 15)


class TestARegenerateKeepingOnlyEarlierPaychecks:
    """The hazard ruling R-PC105 exists for, through the real doors.

    A new owner's rhythm starts 2026-06-19, still ahead; they add one
    earlier paycheck, 06-05, which has started.  "Regenerate the tail" then
    keeps 06-05 and restates the rhythm from a day in ``[06-19, 07-03)``.
    With the phase left at 06-19 each of these failed: from 06-19 itself the
    new era collided with the old on ``uq_pay_eras_user_effective_from``, and
    from later days the stored eras left the fortnightly era paying nothing,
    which ``validate_eras`` refuses on every read of the calendar.
    """

    @pytest.fixture(autouse=True)
    def _freeze(self, monkeypatch):
        """Pin the owner's civil day so 06-05 has started and 06-19 has not."""
        freeze_today(monkeypatch, FROZEN_TODAY)

    @pytest.mark.parametrize(
        ("new_start", "rhythm"),
        [
            (date(2026, 6, 19), rhythm_of(7)),
            (date(2026, 6, 26), rhythm_of(14)),
            (date(2026, 6, 30), Rhythm(SemiMonthly((15, 31)), NONE)),
        ],
    )
    def test_the_rebuild_succeeds_and_the_calendar_derives(
        self, app, db, bare_user, new_start, rhythm,
    ):
        """The rebuild records its rhythm, and every reader can still derive."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 6, 19), 4, rhythm_of(14))
            pay_period_admin.add_earlier_pay_periods(user_id, 1)
            db.session.commit()

            pay_period_admin.regenerate_pay_periods(
                user_id, new_start, 3, rhythm,
            )
            db.session.commit()

            calendar = calendar_for(user_id)
            assert [period.start_date for period in calendar.saved()][:2] == [
                date(2026, 6, 5), new_start,
            ]
            # The kept paycheck runs to the day before the rebuilt one.
            assert calendar.saved()[0].end_date == new_start - timedelta(days=1)
            assert [era.effective_from for era in schedule_for(user_id).eras] == [
                date(2026, 6, 5), new_start,
            ]
            assert_pay_period_invariants(db.session, user_id)


class TestTheDoorJudgesNoRhythm:
    """The phase moves IN PLACE, so a rhythm nobody stated is never re-judged.

    Review 1 of this step: the first build retired the earliest era and
    minted it again, and ``mint_era`` re-asks the cadence-convention
    pairing -- so an owner whose STORED pairing a later holiday-set change
    made illegal (ledger row **N-493**) was refused by a door that states
    no rhythm, the principle ledger row **N-494** closed, and refused after
    the retire's DELETE had run.  No door can write that owner, so the case
    writes the rows itself: every 3 days under ``prior``, below the
    collision floor ``reject_shift_on_short_cadence`` holds a displacing
    convention to today.
    """

    def test_an_owner_on_a_since_illegal_pairing_still_adds_earlier_paychecks(
        self, app, db, bare_user,
    ):
        """The paycheck is added and the stored rhythm is exactly as it was."""
        with app.app_context():
            user_id = bare_user["user"].id
            pay_schedule_service.ensure_schedule_row(user_id)
            prior_id = ref_cache.business_day_shift_id(PRIOR)
            _db.session.add(PayEra(
                user_id=user_id, effective_from=date(2026, 1, 5),
                shift_id=prior_id, cadence_days=3,
            ))
            _db.session.add_all([
                PayPeriod(user_id=user_id, start_date=day)
                for day in (date(2026, 1, 5), date(2026, 1, 8))
            ])
            _db.session.commit()
            with pytest.raises(ValidationError, match="at least"):
                pay_schedule_service.reject_shift_on_short_cadence(
                    Rhythm(FixedDays(3), PRIOR),
                )

            pay_period_admin.add_earlier_pay_periods(user_id, 1)
            _db.session.commit()
            _db.session.remove()

            assert _paydays(user_id)[0] == date(2026, 1, 2)
            rows = PayEra.query.filter_by(user_id=user_id).all()
            assert [
                (row.effective_from, row.cadence_days, row.shift_id)
                for row in rows
            ] == [(date(2026, 1, 2), 3, prior_id)]
