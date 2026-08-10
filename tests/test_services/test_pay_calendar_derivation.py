"""
Shekel Budget App -- Pay Calendar Derivation Tests (plan step C1)

The suite half of C1's proof.  ``tests/manual/verify_pay_calendar_derivation.py``
drives the same oracle over a clone of production -- 61 contiguous biweekly
paydays at one cadence, which is ONE schedule shape however many rows it has.
These tests cover what that run structurally cannot see:

* the shapes the live data does not supply -- a one-day period, a 90-day
  cadence, a hole, an overlap, a single-payday schedule, an ordinal out of date
  order, a mid-schedule cadence change, and the empty calendar
  (``IRREGULAR_SHAPES``, each with hand-computed expected values);
* what the derivation REFUSES, which a clean database never exercises;
* the writer's output at six cadences spanning the storable range (2, 7, 14,
  30, 90, 365 of the 364 that ``ck_pay_schedule_cadence_range`` permits once
  ``ck_pay_periods_date_order`` has excluded 1), each pinned to a hand-computed
  end on BOTH branches rather than only to "the two sides agree";
* **re-derivation stability** -- whether yesterday's answer still holds after
  another payday is written.  That is the only axis on which a derived end can
  behave differently from the stored column it replaces, and no other test here
  asks about it;
* the ORACLE's own logic -- the verdict, the two controls, the owner guard --
  because it decides whether a run passed and the manual script that used to
  hold it could not be tested.

**No date here is read from a clock.**  Every date is a literal or is derived
from one by explicit ``timedelta`` arithmetic; nothing calls ``date.today()``
or ``display_today()``, so these pass identically under
``TZ=Pacific/Kiritimati`` and under the weekly ``SHEKEL_FAKE_TODAY`` sweep
(``docs/test-suite-clocks.md``).  The derivation has no clock, and a test that
gave it one would be testing the fixture.
"""

from datetime import date, datetime, timedelta

import pytest

from app.services import pay_period_service
from app.services.pay_calendar import (
    MAX_CADENCE_DAYS,
    DerivedPeriod,
    PayCalendar,
    PayCalendarError,
    derive_periods,
)
from tests.oracles.pay_calendar_derivation import (
    IRREGULAR_SHAPES,
    build_stored_rows,
    cadence_control,
    compare,
    identified_paydays,
    perturb,
    shape,
    verdict,
)

#: Production's own schedule, measured on ``shekel-prod-db`` 2026-08-08: 61
#: paydays from 2026-03-26 at a 14-day cadence, the last on 2028-07-13 with a
#: stored ``end_date`` of 2028-07-26.  Reproduced here so the suite asserts the
#: real shape rather than a convenient one.
_LIVE_FIRST_PAYDAY = date(2026, 3, 26)
_LIVE_PERIOD_COUNT = 61
_LIVE_CADENCE_DAYS = 14
_LIVE_LAST_PAYDAY = date(2028, 7, 13)
_LIVE_LAST_END = date(2028, 7, 26)

#: ``(cadence_days, first period's end, last period's end)`` for six periods
#: opening 2026-01-02.  The FIRST end exercises the ``lead(start) - 1`` branch
#: and the LAST the ``start + cadence - 1`` projection, so each row pins both.
#: Cadence 1 is absent: the writer stores ``end_date = start_date`` for it and
#: ``ck_pay_periods_date_order CHECK (start_date < end_date)`` rejects that
#: outright (plan finding P9).  The derivation handles it -- the
#: ``one_day_periods`` shape proves that at the value level -- but the column
#: cannot hold it, so there is nothing to compare against until C4.
_CADENCE_ANCHORS = (
    (2, date(2026, 1, 3), date(2026, 1, 13)),
    (7, date(2026, 1, 8), date(2026, 2, 12)),
    (14, date(2026, 1, 15), date(2026, 3, 26)),
    (30, date(2026, 1, 31), date(2026, 6, 30)),
    (90, date(2026, 4, 1), date(2027, 6, 25)),
    (365, date(2027, 1, 1), date(2031, 12, 31)),
)


# ---------------------------------------------------------------------------
# TestDerivationRefusals
# ---------------------------------------------------------------------------


class TestDerivationRefusals:
    """What the derivation will not accept, and why each refusal exists."""

    @pytest.mark.parametrize("cadence_days", [0, -1, -14, 366, 10_000])
    def test_a_cadence_outside_the_stored_range_is_refused(self, cadence_days):
        """1..365 is what ``ck_pay_schedule_cadence_range`` permits.

        Below the floor the last period would end before its own payday; above
        the ceiling the value cannot have come from a schedule row, so
        projecting a horizon off it would mean trusting a number no write door
        could have produced.
        """
        with pytest.raises(PayCalendarError, match="at least 1 day and at most 365"):
            derive_periods([(1, date(2026, 1, 2))], cadence_days)

    @pytest.mark.parametrize(
        "cadence_days", [True, False, 14.0, 14.9, "14"],
    )
    def test_a_cadence_that_is_not_a_plain_int_is_refused(self, cadence_days):
        """``bool`` and ``float`` are the dangerous two, and both are refused.

        ``True`` is an ``int`` subclass and would pass as a one-day cadence.  A
        ``float`` is silently TRUNCATED -- ``date.__add__`` reads only
        ``timedelta.days`` -- so 14.9 would produce the same calendar as 14 and
        the error would be invisible.

        **``None`` left this parametrization at plan step C2-b1** and is not an
        omission: it stopped being a type error and became a MEANING -- "this
        owner has no schedule at all" -- whose legality depends on whether they
        have paydays.  ``TestTheCadenceIsRequiredOnlyBesideAPayday`` below owns
        both directions of that rule.
        """
        with pytest.raises(PayCalendarError, match="must be a plain int"):
            derive_periods([(1, date(2026, 1, 2))], cadence_days)

    def test_the_cadence_is_validated_before_the_paydays_are_read(self):
        """An unusable cadence is refused even for an empty payday set.

        The caller has to resolve a cadence to get here at all, so a bad one is
        a bad caller whether or not this owner has paydays yet.  Refusing it
        only when the data happens to reach the projection branch would hide it
        until the day the user records their first payday.
        """
        with pytest.raises(PayCalendarError, match="at least 1 day and at most 365"):
            derive_periods([], 0)

    def test_a_repeated_payday_is_refused(self):
        """Two periods cannot share an opening day.

        The first of them would derive ``end_date = start_date - 1`` and cover
        no day at all.  ``uq_pay_periods_user_start`` makes this unreachable
        from the table; a caller assembling a payday set in memory -- which is
        exactly what C3's writer will do, from a form batch plus existing rows
        -- can still do it.
        """
        with pytest.raises(PayCalendarError, match="appears twice"):
            derive_periods(
                [
                    (1, date(2026, 1, 2)),
                    (2, date(2026, 1, 16)),
                    (3, date(2026, 1, 2)),
                ],
                14,
            )

    def test_a_datetime_is_refused(self):
        """``datetime`` is a ``date`` subclass and must not pass as one.

        Accepted, it would give every derived end a time component -- unequal
        to the DATE column it reproduces, and placing a day's money by the
        process timezone rather than the app's civil day.
        """
        with pytest.raises(PayCalendarError, match="must be a datetime.date"):
            derive_periods([(1, datetime(2026, 1, 2, 9, 30))], 14)

    @pytest.mark.parametrize("payday", ["2026-01-02", 20260102, None])
    def test_a_value_that_is_not_a_date_is_refused(self, payday):
        """An ISO string, an integer and ``None`` are all refused by type."""
        with pytest.raises(PayCalendarError, match="must be a datetime.date"):
            derive_periods([(1, payday)], 14)

    @pytest.mark.parametrize("period_id", ["41", 41.0, True, date(2026, 1, 2)])
    def test_a_period_id_that_is_not_an_int_or_none_is_refused(
        self, period_id,
    ):
        """The id is checked even though nothing derives from it.

        It rides onto ``DerivedPeriod.period_id``, whose whole purpose is to be
        what a foreign key points at, so a wrong type would surface as a failed
        lookup far from the caller that supplied it.  ``True`` is refused for
        the same reason a ``bool`` cadence is: it is an ``int`` subclass and
        would pass as row 1.
        """
        with pytest.raises(PayCalendarError, match="must be an int or None"):
            derive_periods([(period_id, date(2026, 1, 2))], 14)

    def test_a_period_id_of_none_is_accepted(self):
        """``None`` is how a period that is not materialised says so.

        A projection past the owner's horizon has no row for a foreign key to
        point at, and plan step C2 has to be able to build one.
        """
        derived = derive_periods([(None, date(2026, 1, 2))], 14)
        assert derived[0].period_id is None


# ---------------------------------------------------------------------------
# TestTheCadenceIsRequiredOnlyBesideAPayday
# ---------------------------------------------------------------------------


class TestTheCadenceIsRequiredOnlyBesideAPayday:
    """Plan step C2-b1: ``cadence_days`` and ``paydays`` travel together.

    The cadence is read for exactly one thing -- the LAST period's end -- so an
    owner with no paydays has nothing to read it for, and
    ``pay_schedule_service.resolve_cadence`` answers ``None`` for exactly that
    owner (no ``budget.pay_schedule`` row, and no period to infer one from).
    Production's companion user is one: zero paydays, measured 2026-08-08.

    The same absence BESIDE a payday is a different fact -- plan finding P8's
    broken state -- and this is the only place in the application that refuses
    it.  Both directions are asserted here because a rule tested in one
    direction is a rule that can be satisfied by refusing everything.
    """

    def test_no_paydays_and_no_cadence_derive_an_empty_calendar(self):
        """The legal pairing, and the reason the empty calendar stays buildable.

        ``recurrence._reading.resolved_recurrence`` answers ``None`` for an
        owner with no periods so the Recurring surface still renders; raising
        here would take that page to a 500 for the one owner it is written for.
        """
        assert derive_periods([], None) == ()

    def test_a_payday_with_no_cadence_is_refused(self):
        """P8's state: a payday exists and its period's end cannot be derived.

        Every alternative invents a horizon the owner never chose, so the
        refusal names the invariant rather than clamping.
        """
        with pytest.raises(PayCalendarError, match="no cadence"):
            derive_periods([(1, date(2026, 1, 2))], None)

    def test_the_refusal_counts_the_paydays_it_refused(self):
        """The message names the value, per the project's error-message rule."""
        with pytest.raises(PayCalendarError, match=r"\b3 payday\(s\)"):
            derive_periods(
                [
                    (1, date(2026, 1, 2)),
                    (2, date(2026, 1, 16)),
                    (3, date(2026, 1, 30)),
                ],
                None,
            )

    def test_an_absent_cadence_is_refused_after_a_duplicate_payday(self):
        """Order of refusals: the payday set is graded before the pairing.

        A caller who hands in both faults hears about the one that is a
        property of the data they supplied, not the one that is a property of
        their schedule row -- and this pins that order so the two refusals
        cannot start racing.
        """
        with pytest.raises(PayCalendarError, match="appears twice"):
            derive_periods(
                [(1, date(2026, 1, 2)), (2, date(2026, 1, 2))], None,
            )

    def test_a_present_cadence_is_still_graded_beside_an_empty_set(self):
        """Absence is excused; a WRONG value never is, whatever the payday set.

        ``None`` means "there is no schedule"; ``0`` means "the schedule says
        zero", which no write door could have produced.
        """
        with pytest.raises(PayCalendarError, match="at least 1 day and at most 365"):
            derive_periods([], 0)

    def test_an_empty_calendar_answers_every_question_without_a_cadence(self):
        """The pairing is safe because no method reads what is not there.

        Asserted over the whole public surface rather than the two methods that
        obviously touch the cadence: the claim being made is that NONE of them
        reads it, and a spot check of two would not be that claim.
        """
        calendar = PayCalendar.from_paydays(
            paydays=[], cadence_days=None, user_id=1,
        )
        day = date(2026, 1, 2)

        assert calendar.periods == ()
        assert calendar.opening_bound() is None
        assert calendar.horizon() is None
        assert calendar.period_containing(day) is None
        assert calendar.span_containing(day) is None
        assert calendar.period_starting_on_or_after(day) is None
        assert calendar.period_starting_on_or_before(day) is None
        assert calendar.period_by_id(1) is None
        assert calendar.earliest_start_in_month(2026, 1) is None
        assert len(calendar.window(0, 6)) == 0
        assert len(calendar.overlapping(day, date(2027, 1, 1))) == 0
        assert len(calendar.axis(day, date(2027, 1, 1))) == 0
        # The one method that MUST refuse: it exists to name a row a NOT NULL
        # foreign key can point at, and there is none.
        with pytest.raises(PayCalendarError, match="no materialised pay period"):
            calendar.filing_period(day)


# ---------------------------------------------------------------------------
# TestIrregularShapeSweep
# ---------------------------------------------------------------------------


class TestIrregularShapeSweep:
    """The generated sweep over schedules the live data cannot supply."""

    @pytest.mark.parametrize(
        "irregular", IRREGULAR_SHAPES, ids=lambda s: s.label,
    )
    def test_the_shape_derives_its_hand_computed_values(self, irregular):
        """Every derived index, end and projection flag matches by hand.

        The expected values in ``IRREGULAR_SHAPES`` were worked out on paper
        with the arithmetic written beside them, so this asserts VALUES rather
        than agreeing with whatever the code produced.
        """
        assert derive_periods(
            irregular.paydays, irregular.cadence_days,
        ) == irregular.expected

    @pytest.mark.parametrize(
        "irregular", IRREGULAR_SHAPES, ids=lambda s: s.label,
    )
    def test_the_shape_disagrees_with_exactly_the_expected_stored_rows(
        self, irregular,
    ):
        """The comparator names precisely the rows the derivation contradicts.

        Four of the shapes disagree BY DESIGN -- a hole, a shared boundary
        day, an ordinal out of date order, and a stored cadence the schedule
        has outgrown.  The rest must reproduce byte-identically.
        """
        comparison = compare(
            user_id=1,
            periods=build_stored_rows(irregular, user_id=1),
            cadence_days=irregular.cadence_days,
            cadence_is_stored=True,
        )
        assert tuple(
            row.start_date for row in comparison.disagreements
        ) == irregular.disagreeing_starts, irregular.why

    @pytest.mark.parametrize(
        "irregular", IRREGULAR_SHAPES, ids=lambda s: s.label,
    )
    def test_the_derived_spans_tile_the_calendar(self, irregular):
        """Consecutive derived periods abut exactly: no gap, no overlap.

        This is the normalization's whole claim, asserted rather than argued.
        Three of these shapes are STORED with a hole, an overlap or an ordinal
        out of order, and the derivation of each still tiles -- because a set
        of distinct sorted dates cannot produce anything else.
        """
        derived = derive_periods(irregular.paydays, irregular.cadence_days)
        for earlier, later in zip(derived, derived[1:]):
            assert earlier.end_date + timedelta(days=1) == later.start_date
            assert earlier.period_index + 1 == later.period_index

    @pytest.mark.parametrize(
        "irregular", IRREGULAR_SHAPES, ids=lambda s: s.label,
    )
    def test_exactly_the_last_end_is_projected(self, irregular):
        """Only the final period's end comes from the cadence.

        The flag is the one thing the two dropped columns could not say.  True
        for the last period of a non-empty calendar and for no other -- and for
        none at all when the calendar is empty.
        """
        derived = derive_periods(irregular.paydays, irregular.cadence_days)
        projected = [
            period for period in derived if period.end_is_projected
        ]
        assert len(projected) == (1 if derived else 0)
        if derived:
            assert projected[0] is derived[-1]

    def test_the_input_order_does_not_change_the_answer(self):
        """A calendar is a property of the payday SET, not of the query order.

        ``get_all_periods`` orders by ``period_index`` today, which C4 drops;
        the derivation must not care.
        """
        paydays = [
            (1, date(2026, 1, 2)),
            (2, date(2026, 1, 16)),
            (3, date(2026, 1, 30)),
        ]
        assert derive_periods(reversed(paydays), 14) == derive_periods(
            paydays, 14,
        )

    def test_a_single_payday_derives_one_wholly_projected_period(self):
        """The state registration leaves a new owner in, spelled out.

        ``auth_service.register_user`` writes one bootstrap payday, so this is
        every new account's first calendar: one period, its end projected off
        the cadence because there is no second payday to close it.
        """
        assert derive_periods([(7, date(2026, 3, 26))], 14) == (
            # 2026-03-26 + (14 - 1) days = 2026-04-08.
            DerivedPeriod(
                period_id=7,
                period_index=0,
                start_date=date(2026, 3, 26),
                end_date=date(2026, 4, 8),
                end_is_projected=True,
            ),
        )

    def test_an_empty_payday_set_derives_an_empty_calendar(self):
        """A companion holds no paydays of its own, and that is not an error.

        Measured on production 2026-08-08: user 2 is a companion with zero
        paydays, so no step may assume every user row has a schedule.
        """
        assert derive_periods([], 14) == ()


# ---------------------------------------------------------------------------
# TestReDerivationStability
# ---------------------------------------------------------------------------


class TestReDerivationStability:
    """Does yesterday's answer still hold after another payday is written?

    The axis every other test here is blind to, and the only one on which a
    derived end can behave differently from the stored column it replaces: a
    stored ``end_date`` cannot move when a neighbour is written, a derived one
    can.  Named by adversarial review of this step, 2026-08-08.

    The four cases below partition every append: exactly one cadence later (the
    only stable one, and the one ``extend_pay_periods`` takes), earlier than
    that, later than that, and anywhere at all for the rows that are not last.
    """

    #: The schedule every case re-derives from: two biweekly paydays, so the
    #: second period's end is the projected one under test.
    _PAYDAYS = ((1, date(2026, 1, 2)), (2, date(2026, 1, 16)))

    def _end_of(self, paydays, payday):
        """Return the derived end of *payday* within *paydays*.

        Args:
            paydays: The payday set to derive.
            payday: The payday whose period's end to return.

        Returns:
            The derived ``end_date``.
        """
        derived = derive_periods(paydays, 14)
        return next(
            period.end_date for period in derived
            if period.start_date == payday
        )

    def test_appending_exactly_one_cadence_later_moves_no_end(self):
        """The extend path's append is the only stable one.

        ``pay_period_admin.extend_pay_periods`` opens its batch at
        ``last.end_date + 1``, which for a projected end of
        ``last_start + cadence - 1`` is exactly ``last_start + cadence``.  The
        old last period's end is then ``lead - 1``, the same day the projection
        gave it -- so nothing moves and only the FLAG changes.
        """
        before = derive_periods(self._PAYDAYS, 14)
        assert before[-1].end_date == date(2026, 1, 29)
        assert before[-1].end_is_projected is True

        after = derive_periods([*self._PAYDAYS, (3, date(2026, 1, 30))], 14)
        assert after[1].start_date == date(2026, 1, 16)
        # lead - 1 = 2026-01-30 - 1 = 2026-01-29, the day the projection gave.
        assert after[1].end_date == date(2026, 1, 29)
        assert after[1].end_is_projected is False

    def test_appending_inside_the_projected_span_shortens_the_last_end(self):
        """A forward-only append can still move an end BACKWARD.

        2026-01-28 is later than every existing payday, so it is forward-only
        by the plan's own definition and is not the mid-schedule insert C6
        rules on.  It still moves the second period's end from 2026-01-29 to
        2026-01-27.  A row dated 2026-01-28 and pointed at that period is not
        left outside it -- ``utils.dates.attribution_date`` clamps -- so it
        RENDERS on 2026-01-27 instead, which is plan finding P10's damage
        reached through a door P10 does not cover.

        ``_reject_overlapping_batch`` blocks this write today because it
        compares against the STORED end; plan step C3 deletes that guard.
        """
        assert self._end_of(self._PAYDAYS, date(2026, 1, 16)) == date(2026, 1, 29)
        after = [*self._PAYDAYS, (3, date(2026, 1, 28))]
        # lead - 1 = 2026-01-28 - 1 = 2026-01-27, two days earlier.
        assert self._end_of(after, date(2026, 1, 16)) == date(2026, 1, 27)

    def test_appending_beyond_the_projected_span_lengthens_the_last_end(self):
        """The mirror case, and the reason the stable append is a single day.

        2026-02-05 leaves a would-be hole under the old model; under this one
        the preceding period simply runs on to 2026-02-04.  Correct, and still
        a moved end -- so "append forward and nothing changes" is false in both
        directions, not only downward.
        """
        after = [*self._PAYDAYS, (3, date(2026, 2, 5))]
        # lead - 1 = 2026-02-05 - 1 = 2026-02-04, six days later.
        assert self._end_of(after, date(2026, 1, 16)) == date(2026, 2, 4)

    @pytest.mark.parametrize(
        "appended",
        [(3, date(2026, 1, 28)), (3, date(2026, 1, 30)), (3, date(2026, 2, 5))],
        ids=["inside", "exactly_one_cadence", "beyond"],
    )
    def test_no_end_but_the_last_one_can_move(self, appended):
        """Every non-final end is fixed for as long as its successor is.

        The first period's end is ``lead(2026-01-16) - 1`` whatever is appended
        after it, so it is 2026-01-15 in all three cases.  That is the bound on
        the instability: only the last period's span is exposed, which is what
        makes ``end_is_projected`` the marker for it.
        """
        after = [*self._PAYDAYS, appended]
        assert self._end_of(after, date(2026, 1, 2)) == date(2026, 1, 15)
        assert self._end_of(self._PAYDAYS, date(2026, 1, 2)) == date(2026, 1, 15)


# ---------------------------------------------------------------------------
# TestTheStoredColumnsAreReproduced
# ---------------------------------------------------------------------------


class TestTheStoredColumnsAreReproduced:
    """The derivation against what the real writer actually stores."""

    def test_the_production_schedule_reproduces_byte_identically(
        self, app, db, bare_user,
    ):
        """Production's own 61-payday shape, rebuilt and diffed row by row.

        The clone run measures this against the live rows; this measures it
        against the writer that produced them, so a change to the writer is
        caught in CI rather than at the next manual run.
        """
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=bare_user["user"].id,
                start_date=_LIVE_FIRST_PAYDAY,
                num_periods=_LIVE_PERIOD_COUNT,
                cadence_days=_LIVE_CADENCE_DAYS,
            )
            db.session.commit()

            assert len(periods) == _LIVE_PERIOD_COUNT
            # 2026-03-26 + 60 * 14 days = 2028-07-13, the live last payday.
            assert periods[-1].start_date == _LIVE_LAST_PAYDAY
            assert periods[-1].end_date == _LIVE_LAST_END

            comparison = compare(
                user_id=bare_user["user"].id,
                periods=periods,
                cadence_days=_LIVE_CADENCE_DAYS,
                cadence_is_stored=True,
            )
            assert comparison.disagreements == ()
            assert len(comparison.rows) == _LIVE_PERIOD_COUNT
            assert comparison.rows[-1].end_is_projected is True

    @pytest.mark.parametrize(
        "cadence_days,first_end,last_end",
        _CADENCE_ANCHORS,
        ids=[f"cadence_{row[0]}" for row in _CADENCE_ANCHORS],
    )
    def test_every_storable_cadence_reproduces_hand_computed_ends(
        self, app, db, bare_user, cadence_days, first_end, last_end,
    ):
        """The writer's output equals the derivation, and equals known dates.

        Asserting only ``disagreements == ()`` would be satisfied by a
        derivation that used ``start + cadence - 1`` for EVERY period -- the
        pre-normalization defect -- because the writer spaces its paydays
        exactly one cadence apart, which makes the two branches agree on every
        row it writes.  So each cadence also pins the FIRST end (the
        ``lead(start) - 1`` branch) and the LAST (the projection), by hand.
        """
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=bare_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=6,
                cadence_days=cadence_days,
            )
            db.session.commit()

            derived = derive_periods(
                identified_paydays(periods), cadence_days,
            )
            assert derived[0].end_date == first_end
            assert derived[0].end_is_projected is False
            assert derived[-1].end_date == last_end
            assert derived[-1].end_is_projected is True

            comparison = compare(
                user_id=bare_user["user"].id,
                periods=periods,
                cadence_days=cadence_days,
                cadence_is_stored=True,
            )
            assert comparison.disagreements == ()

    def test_a_second_contiguous_batch_reproduces(
        self, app, db, bare_user,
    ):
        """The extend path's shape -- a batch opening the day after the last end.

        ``pay_period_admin.extend_pay_periods`` derives its start as
        ``last.end_date + 1`` (``pay_period_admin.py``), which is the one
        append that moves no previously-derived end -- see
        :class:`TestReDerivationStability`.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            first = pay_period_service.generate_pay_periods(
                user_id=user_id,
                start_date=date(2026, 1, 2),
                num_periods=3,
                cadence_days=14,
            )
            db.session.flush()
            pay_period_service.generate_pay_periods(
                user_id=user_id,
                start_date=first[-1].end_date + timedelta(days=1),
                num_periods=3,
                cadence_days=14,
            )
            db.session.commit()

            comparison = compare(
                user_id=user_id,
                periods=pay_period_service.get_all_periods(user_id),
                cadence_days=14,
                cadence_is_stored=True,
            )
            assert comparison.disagreements == ()

    def test_a_gapped_batch_diverges_on_the_row_before_the_hole(
        self, app, db, bare_user,
    ):
        """Plan finding P2, written through the REAL writer and then diffed.

        ``_reject_overlapping_batch`` refuses a batch that starts on or before
        the latest existing end and refuses nothing else, so a batch opening
        two weeks late is accepted today.  The stored calendar then leaves
        2026-01-30 through 2026-02-12 funded by no paycheck; the derivation
        cannot express that, so the period before the hole absorbs it.  This
        is the one disagreement, and it is the normalization working.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_service.generate_pay_periods(
                user_id=user_id,
                start_date=date(2026, 1, 2),
                num_periods=2,
                cadence_days=14,
            )
            db.session.flush()
            # The missing payday would have been 2026-01-30; this batch opens
            # a fortnight after THAT, and 15 days after the latest stored end
            # (2026-01-29), so the forward-only guard lets it through.
            pay_period_service.generate_pay_periods(
                user_id=user_id,
                start_date=date(2026, 2, 13),
                num_periods=2,
                cadence_days=14,
            )
            db.session.commit()

            comparison = compare(
                user_id=user_id,
                periods=pay_period_service.get_all_periods(user_id),
                cadence_days=14,
                cadence_is_stored=True,
            )
            disagreements = comparison.disagreements
            assert len(disagreements) == 1
            moved = disagreements[0]
            assert moved.start_date == date(2026, 1, 16)
            assert moved.index_agrees is True
            assert moved.stored_end == date(2026, 1, 29)
            # lead(start_date) - 1 = 2026-02-13 - 1 day.
            assert moved.derived_end == date(2026, 2, 12)


# ---------------------------------------------------------------------------
# TestThePaydayControl
# ---------------------------------------------------------------------------


class TestThePaydayControl:
    """The first control, without which byte-identity proves nothing.

    Plan step C1: "it must be paired with a perturbation control (move one
    payday and require the harness to report the shifted indices and ends), or
    the equality proves only that the harness reads what it reads."
    """

    def test_the_unperturbed_schedule_reports_no_disagreement(self):
        """The control's baseline: five ordinary fortnights agree."""
        rows = build_stored_rows(shape("biweekly_five"), user_id=1)
        assert compare(1, rows, 14, cadence_is_stored=True).disagreements == ()

    def test_moving_one_payday_reports_the_shifted_indices_and_ends(self):
        """One relocated payday, and every row it moved is named.

        The payday at position 2 (2026-01-30) is relocated to 2026-01-01, one
        day before the earliest.  Derived afresh, the payday order is
        2026-01-01, 01-02, 01-16, 02-13, 02-27, so:

        * 2026-01-01 holds stored ordinal 2 and derives 0, and its stored end
          2026-02-12 collapses to 2026-01-01 (the next payday is the day
          after);
        * 2026-01-02 holds stored ordinal 0 and derives 1, its end unchanged;
        * 2026-01-16 holds stored ordinal 1 and derives 2, and its end runs on
          from 2026-01-29 to 2026-02-12 because the payday that used to close
          it has left;
        * the last two rows are untouched -- the relocation reordered only the
          head, which is itself worth pinning: a control that moved everything
          would not show that the comparator is row-precise.
        """
        perturbation = perturb(build_stored_rows(shape("biweekly_five"), 1))
        assert perturbation is not None
        assert perturbation.moved_from == date(2026, 1, 30)
        assert perturbation.moved_to == date(2026, 1, 1)

        comparison = compare(
            1, perturbation.rows, 14, cadence_is_stored=True,
        )
        assert {
            (row.start_date, row.stored_index, row.derived_index)
            for row in comparison.disagreements
            if not row.index_agrees
        } == {
            (date(2026, 1, 1), 2, 0),
            (date(2026, 1, 2), 0, 1),
            (date(2026, 1, 16), 1, 2),
        }
        assert {
            (row.start_date, row.stored_end, row.derived_end)
            for row in comparison.disagreements
            if not row.end_agrees
        } == {
            (date(2026, 1, 1), date(2026, 2, 12), date(2026, 1, 1)),
            (date(2026, 1, 16), date(2026, 1, 29), date(2026, 2, 12)),
        }

    def test_the_relocation_never_duplicates_an_existing_payday(self):
        """The moved payday lands strictly before every other, at any cadence.

        A one-day nudge would collide on a one-day cadence and the derivation
        would refuse the whole control rather than report it.
        """
        rows = build_stored_rows(shape("one_day_periods"), user_id=1)
        perturbation = perturb(rows)
        assert perturbation is not None
        moved = [row.start_date for row in perturbation.rows]
        assert len(set(moved)) == len(moved)
        assert perturbation.moved_to < min(row.start_date for row in rows)

    def test_a_single_payday_schedule_cannot_be_perturbed(self):
        """Fewer than two paydays leaves no order to disturb.

        ``None`` rather than a no-op copy, so a harness reports that the
        control was INAPPLICABLE instead of that it failed -- every fresh
        signup is a one-payday user, so scoring that as a failure meant any
        database holding a new account could never go green.
        """
        assert perturb(build_stored_rows(shape("single_payday"), 1)) is None
        assert perturb([]) is None

    def test_the_payday_control_leaves_the_last_projected_end_alone(self):
        """Why a second control is needed, stated as an assertion.

        The relocation never touches the last payday and never touches the
        cadence, so the projected end is identical before and after.  A
        derivation that computed EVERY end from the cadence would therefore
        pass this control -- which is what :func:`cadence_control` exists for.
        """
        rows = build_stored_rows(shape("biweekly_five"), user_id=1)
        perturbation = perturb(rows)
        assert perturbation is not None
        before = derive_periods(identified_paydays(rows), 14)
        after = derive_periods(identified_paydays(perturbation.rows), 14)
        assert before[-1].end_date == after[-1].end_date == date(2026, 3, 12)


# ---------------------------------------------------------------------------
# TestTheCadenceControl
# ---------------------------------------------------------------------------


class TestTheCadenceControl:
    """The second control: the one that separates the two end branches.

    On production's regular schedule ``lead(start) - 1`` and
    ``start + cadence - 1`` agree on all 61 rows, so byte-identity cannot say
    which branch produced an end.  Re-deriving at a neighbouring cadence
    separates them.
    """

    def test_exactly_one_end_moves_by_exactly_one_day(self):
        """The correct outcome, on the ordinary schedule."""
        control = cadence_control(shape("biweekly_five").paydays, 14)
        assert control.applicable is True
        assert control.probe_cadence == 15
        assert control.expected_shift_days == 1
        # Only the last period reads the cadence, so only its end moves.
        assert control.moved == ((date(2026, 2, 27), 1),)
        assert control.fired is True

    def test_it_probes_downward_at_the_top_of_the_stored_range(self):
        """365 is the ceiling, so the probe has to go the other way.

        Without this the control would ask for a cadence the schema forbids and
        the derivation would refuse it, turning the instrument into a crash.
        """
        control = cadence_control(
            [(1, date(2026, 1, 2)), (2, date(2027, 1, 2))], MAX_CADENCE_DAYS,
        )
        assert control.probe_cadence == MAX_CADENCE_DAYS - 1
        assert control.expected_shift_days == -1
        assert control.moved == ((date(2027, 1, 2), -1),)
        assert control.fired is True

    def test_it_is_inapplicable_to_an_empty_calendar(self):
        """No periods, no end to move -- reported, not scored."""
        control = cadence_control([], 14)
        assert control.applicable is False
        assert control.fired is False
        assert control.moved == ()

    def test_it_fires_on_a_single_payday_schedule(self):
        """One period, whose only end is the projected one.

        The payday control cannot run here; this one can, so a brand-new
        account is not a wholly unmeasured user.
        """
        control = cadence_control([(1, date(2026, 3, 26))], 14)
        assert control.moved == ((date(2026, 3, 26), 1),)
        assert control.fired is True


# ---------------------------------------------------------------------------
# TestTheOraclesOwnVerdict
# ---------------------------------------------------------------------------


class TestTheOraclesOwnVerdict:
    """The pass/fail rule, which decides whether a real-data run counts."""

    @staticmethod
    def _rows(label="biweekly_five"):
        """Return a shape's stored rows for user 1.

        Args:
            label: The :data:`IRREGULAR_SHAPES` label to build.

        Returns:
            The unsaved rows.
        """
        return build_stored_rows(shape(label), user_id=1)

    def _verdict_for(self, rows, cadence_days=14, cadence_is_stored=True):
        """Run both controls and the verdict over *rows*.

        Args:
            rows: The owner's stored pay periods.
            cadence_days: The cadence to drive with.
            cadence_is_stored: Whether it came from a schedule row.

        Returns:
            The ``(passed, reasons)`` pair.
        """
        comparison = compare(1, rows, cadence_days, cadence_is_stored)
        return verdict(
            comparison,
            perturb(rows),
            cadence_control(identified_paydays(rows), cadence_days),
        )

    def test_a_clean_schedule_passes_with_no_reasons(self):
        """The ordinary case: both controls fire and nothing disagrees."""
        assert self._verdict_for(self._rows()) == (True, ())

    def test_a_real_disagreement_fails(self):
        """A hole is a disagreement the verdict must not absorb."""
        passed, reasons = self._verdict_for(self._rows("hole"))
        assert passed is False
        assert any("disagree with the stored columns" in r for r in reasons)

    def test_an_inferred_cadence_disqualifies_one_ROW_not_the_user(self):
        """Plan finding P8, scoped correctly.

        Only the LAST row's end is circular under an inferred cadence -- every
        earlier end derives from the next payday and never reads it.  The first
        cut of this rule disqualified the whole user, so a schedule-less owner
        with a genuine hole exited 0.  Here the hole still fails.
        """
        passed, reasons = self._verdict_for(
            self._rows("hole"), cadence_is_stored=False,
        )
        assert passed is False
        assert any("disagree with the stored columns" in r for r in reasons)

    def test_an_inferred_cadence_forgives_only_the_last_rows_end(self):
        """The row it does forgive, shown to be forgiven.

        ``stored_cadence_no_longer_matches`` disagrees on exactly the last
        row's end.  With the cadence STORED that is a real finding; with it
        INFERRED the comparison is circular and must not be scored.
        """
        rows = self._rows("stored_cadence_no_longer_matches")
        stored = compare(1, rows, 7, cadence_is_stored=True)
        inferred = compare(1, rows, 7, cadence_is_stored=False)
        assert len(stored.provable_disagreements) == 1
        assert inferred.provable_disagreements == ()

    def test_an_inapplicable_payday_control_is_not_a_failure(self):
        """A one-payday owner passes on their own merits.

        The cadence control still runs for them, so they are measured; it is
        the CALLER's job to refuse a whole database in which no payday control
        was ever applicable, and the manual harness does that.
        """
        assert self._verdict_for(self._rows("single_payday")) == (True, ())


# ---------------------------------------------------------------------------
# TestTheComparatorRefusesWhatItCannotMeasure
# ---------------------------------------------------------------------------


class TestTheComparatorRefusesWhatItCannotMeasure:
    """The oracle's own guards -- an instrument that lies is worse than none."""

    def test_two_owners_rows_in_one_list_are_refused(self):
        """A merged payday set derives a calendar belonging to neither owner.

        Production has exactly one owner with paydays, so a clone run cannot
        surface this mistake; the guard is the only thing that can.
        """
        rows = build_stored_rows(shape("biweekly_five"), user_id=1)
        rows[2].user_id = 2
        with pytest.raises(ValueError, match="belonging to another owner"):
            compare(1, rows, 14, cadence_is_stored=True)

    def test_unsaved_rows_with_no_owner_are_accepted(self):
        """An unsaved row carries no ``user_id`` and must not trip the guard."""
        rows = build_stored_rows(shape("biweekly_five"), user_id=1)
        for row in rows:
            row.user_id = None
        assert compare(1, rows, 14, cadence_is_stored=True).disagreements == ()

    def test_an_unknown_shape_label_raises_rather_than_skipping(self):
        """A renamed shape must not silently drop the test that used it."""
        with pytest.raises(KeyError, match="no IrregularShape labelled"):
            shape("no_such_shape")

    def test_the_perturbed_rows_are_never_attached_to_a_session(
        self, app, db, bare_user,
    ):
        """The control's copies must not reach the database it measures.

        The manual harness runs this against a real clone, so an accidental
        flush would write the perturbed calendar into it.
        """
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=bare_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=3,
                cadence_days=14,
            )
            db.session.commit()

            perturbation = perturb(periods)
            assert perturbation is not None
            assert not any(
                row in db.session for row in perturbation.rows
            )
            db.session.flush()
            assert pay_period_service.get_all_periods(
                bare_user["user"].id,
            )[0].start_date == date(2026, 1, 2)
