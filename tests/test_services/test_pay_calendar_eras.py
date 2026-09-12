"""
Shekel Budget App -- the pay calendar's ERA readers (plan step C17-b-2).

Rulings **R-PC66** and **R-PC72** (developer, 2026-09-11): a pay schedule is
a sequence of eras, an era governs from its first payday to the next era's,
and every reader of the calendar anchors on the phase of the era covering its
own day rather than on a recorded cash payday.  This module grades the
producers that rule is built from -- :mod:`app.services.pay_calendar._eras` --
and the piecewise projection over them, which no other suite drives across a
seam between two eras.

Three things are pinned here that the derivation and value suites cannot see:

* **the matching rule** -- a recorded payday stands for the planned payday
  NEAREST to it, a tie exactly half a cadence off going to the LATER one --
  against a brute-force listing of the grid rather than the producer's loop;
* **the seam** -- an era's projected paydays stop where the next era's first
  payday begins, measured in CASH days, so a day is never paid twice and never
  left in no paycheck where two conventions meet;
* **the ordinal** -- a projected ``period_index`` continues the saved sequence
  across the seam by exactly the number of paydays between.

The projection sweep at the bottom is a brute-force reference: it LISTS every
era's cash paydays inside its window and walks
:func:`~app.services.pay_calendar._views.projected_paychecks` against the list,
over randomised era sequences whose seams the write door's floor would admit.

**No date here is read from a clock.**  Every date is a literal or is derived
from one by explicit arithmetic, so these pass identically under
``TZ=Pacific/Kiritimati`` and the weekly ``SHEKEL_FAKE_TODAY`` sweep
(``docs/test-suite-clocks.md``).
"""

import random
from datetime import date, timedelta
from itertools import islice

import pytest

from app.enums import BusinessDayShiftEnum
from app.services.pay_calendar import (
    PayCalendar,
    PayCalendarError,
    PeriodWindow,
    derive_periods,
    era_index_at,
    payday_after,
    planned_paydays_after,
    projected_payday,
)
# Package-PRIVATE on purpose: the producers below have no caller outside the
# package, and the W9910 gate (``shekel-private-module-import``) runs over
# ``app/`` and ``scripts/``, not this tree -- the same reach
# ``test_pay_calendar_derivation.py`` takes for ``_projection``.
from app.services.pay_calendar._eras import (
    first_payday_of,
    horizon_step,
    last_step_of,
    matched_step,
    step_after,
)
from app.services.pay_calendar._projection import project_period_after
from app.services.pay_calendar._views import projected_paychecks
from app.services.pay_rhythm import era_covering
from app.utils.business_days import (
    is_business_day,
    shift_to_business_day,
    shortest_collision_free_cadence,
)
from tests._test_helpers import era_of, rhythm_of
from tests.oracles.pay_calendar_derivation import next_grid_payday_after

PRIOR, NEXT, NONE = (
    BusinessDayShiftEnum.PRIOR, BusinessDayShiftEnum.NEXT,
    BusinessDayShiftEnum.NONE,
)

#: The worked example the step was ruled on.  ``A`` pays every 14 days from
#: Friday 2026-01-02; ``B`` pays every 7 days from Wednesday 2026-07-01, a day
#: OFF ``A``'s grid (its nearest are 06-19 and 07-03).  Truncated back to
#: 03-13 the owner holds both eras and a record that ends inside ``A``.
ERA_A = era_of(date(2026, 1, 2), 14)
ERA_B = era_of(date(2026, 7, 1), 7)
TRUNCATED_RECORD = [(1, date(2026, 1, 2)), (2, date(2026, 3, 13))]

#: 2030-11-28 is the fourth Thursday of November 2030 -- Thanksgiving -- and
#: this era's grid passes through it: 2030-01-10 + 23 fortnights.  Under
#: ``prior`` payroll pays it 2030-11-27, which is the specification's own
#: worked example (steps.md, C17-b-2).
THANKSGIVING_ERA = era_of(date(2030, 1, 10), 14, PRIOR)


def _calendar(paydays, eras, history_opens_on=None):
    """Build a calendar over *paydays* and *eras* for owner 1."""
    return PayCalendar.from_paydays(
        paydays, eras, user_id=1, history_opens_on=history_opens_on,
    )


# ---------------------------------------------------------------------------
# The matching rule
# ---------------------------------------------------------------------------


class TestARecordedPaydayStandsForItsNearestPlannedOne:
    """``matched_step``: which planned paycheck a recorded payday IS.

    Ruled 2026-09-11 on the fork the step presented: the plan is the truth for
    the future, the record for the past, and a record is matched to the
    planned payday nearest it.  The alternative -- the first grid payday
    strictly after the record -- names the very paycheck an early payment
    already stands for, and projects income the owner never receives.
    """

    def test_a_payday_on_the_grid_is_its_own_step(self):
        assert matched_step(date(2026, 1, 2), rhythm_of(14), date(2026, 1, 30)) == 2

    def test_a_payday_paid_EARLY_stands_for_the_planned_one_it_precedes(self):
        """The catalogue's thirteen-day shape: 01-15 IS the 01-16 paycheck."""
        assert matched_step(date(2026, 1, 2), rhythm_of(14), date(2026, 1, 15)) == 1

    def test_a_payday_paid_LATE_stands_for_the_planned_one_it_follows(self):
        assert matched_step(date(2026, 1, 2), rhythm_of(14), date(2026, 1, 20)) == 1

    def test_a_tie_goes_to_the_LATER_planned_payday(self):
        """Seven days off a fortnight: read as the next paycheck paid early.

        The poorer forecast -- the following paycheck lands further out.  ONE
        rule for both halves: the backward walk reads the same answer for the
        opening payday, where it is the over-counting direction, which the
        producer's docstring names as the price of a record being one
        paycheck.  Pinned in both directions so ``<`` and ``<=`` cannot be
        swapped silently.
        """
        assert matched_step(date(2026, 1, 2), rhythm_of(14), date(2026, 1, 9)) == 1
        assert matched_step(date(2026, 1, 2), rhythm_of(14), date(2026, 1, 8)) == 0

    def test_it_measures_from_the_DISPLACED_payday_not_the_nominal_one(self):
        """Thanksgiving 2030 under ``prior``: paid 11-27, stands for nominal 11-28.

        Measured against the nominal grid the record would sit a day BEFORE
        its own step; measured against the cash day it sits ON it.
        """
        era = THANKSGIVING_ERA
        nominal_steps = (date(2030, 11, 28) - era.effective_from).days // 14
        assert projected_payday(era.effective_from, era.rhythm, nominal_steps) == (
            date(2030, 11, 27)
        )
        assert matched_step(era.effective_from, era.rhythm, date(2030, 11, 27)) == (
            nominal_steps
        )

    def test_it_agrees_with_a_brute_force_listing_over_randomised_records(self):
        """The loop is graded against an explicit grid, not against itself."""
        rng = random.Random(1702)
        floor = shortest_collision_free_cadence()
        phase = date(2027, 3, 5)
        for _ in range(3000):
            cadence = rng.randint(floor, 45)
            shift = rng.choice([PRIOR, NEXT, NONE])
            record = phase + timedelta(days=rng.randint(-200, 900))
            rhythm = rhythm_of(cadence, shift)

            expected_next = next_grid_payday_after(phase, cadence, shift, record)
            matched = matched_step(phase, rhythm, record)

            assert projected_payday(phase, rhythm, matched + 1) == expected_next, (
                cadence, shift.name, record,
            )


class TestTheStepAfterADerivedDay:
    """``step_after``: the first grid step PAID strictly after a day."""

    def test_it_answers_the_step_whose_payday_clears_the_day(self):
        assert step_after(date(2026, 1, 2), rhythm_of(14), date(2026, 1, 15)) == 1
        assert step_after(date(2026, 1, 2), rhythm_of(14), date(2026, 1, 16)) == 2

    def test_it_reads_the_displaced_payday(self):
        """Thanksgiving 2030 under ``prior`` is paid 11-27, so 11-27 is cleared only by the step after it."""
        era = THANKSGIVING_ERA
        steps = step_after(era.effective_from, era.rhythm, date(2030, 11, 27))
        assert projected_payday(era.effective_from, era.rhythm, steps) == date(2030, 12, 12)

    def test_it_refuses_when_no_step_within_two_cadences_clears_the_day(self):
        """N-493's reported hole, stated once here and inherited by every caller.

        A one-day cadence under ``prior`` across a weekend: three consecutive
        nominal days pay on the same Friday, so no candidate clears it.
        """
        friday = date(2026, 1, 9)
        with pytest.raises(PayCalendarError, match="within two cadences"):
            step_after(friday, rhythm_of(1, PRIOR), friday)


# ---------------------------------------------------------------------------
# Which era covers a day
# ---------------------------------------------------------------------------


class TestWhichEraCoversADay:
    """``era_index_at`` reads the rule in CASH days; ``era_covering`` in nominal."""

    def test_a_day_before_every_era_is_the_earliest_eras(self):
        assert era_index_at((ERA_A, ERA_B), date(2025, 6, 1)) == 0

    def test_a_day_between_two_eras_belongs_to_the_earlier(self):
        assert era_index_at((ERA_A, ERA_B), date(2026, 6, 30)) == 0
        assert era_index_at((ERA_A, ERA_B), date(2026, 7, 1)) == 1

    def test_an_era_minted_on_a_holiday_under_prior_owns_its_first_paycheck(self):
        """The cash and nominal readings PART on a displaced first payday.

        An era minted on Thanksgiving 2030 under ``prior`` pays its first
        paycheck on 11-27, a day before its own ``effective_from``.  That
        paycheck is the new era's -- the nominal reading hands it to the old
        era, which would close it on the old grid and project the new era's
        first payday on top of it.
        """
        old = era_of(date(2030, 1, 5), 7)
        new = era_of(date(2030, 11, 28), 14, PRIOR)
        assert first_payday_of(new) == date(2030, 11, 27)

        assert era_index_at((old, new), date(2030, 11, 27)) == 1
        assert era_covering((old, new), date(2030, 11, 27)) is old

    def test_the_two_readings_agree_wherever_no_first_payday_is_displaced(self):
        for day in (date(2026, 3, 1), date(2026, 6, 30), date(2026, 7, 1),
                    date(2027, 1, 1)):
            assert (ERA_A, ERA_B)[era_index_at((ERA_A, ERA_B), day)] is (
                era_covering((ERA_A, ERA_B), day)
            )


# ---------------------------------------------------------------------------
# The next payday after the record
# ---------------------------------------------------------------------------


class TestTheNextPaydayAfterTheRecord:
    """``payday_after`` and ``horizon_step``: where the record's continuation opens."""

    def test_the_specifications_worked_example(self):
        """A last recorded 2030-11-27 projects 12-12, where the recorded anchor gave 12-11.

        The step's own worked example (steps.md #10): the record is the
        nominal 11-28 Thanksgiving paid a day early under ``prior``; anchored
        on the record the next payday came a day early too, and every
        boundary after it.
        """
        eras = (THANKSGIVING_ERA,)
        assert payday_after(eras, date(2030, 11, 27)) == date(2030, 12, 12)
        assert projected_payday(date(2030, 11, 27), THANKSGIVING_ERA.rhythm, 1) == (
            date(2030, 12, 11)
        )

    def test_it_is_strictly_after_the_record_below_the_collision_floor(self):
        """Ledger row PC-505, closed: a sub-floor pairing cannot derive a reversed period.

        The row's own case -- paydays 2030-11-13 and 11-15 at a two-day
        cadence under ``prior`` -- derived ``start 11-15, end 11-14`` while
        the last end stepped one cadence from the record and displaced it
        BACK.  The next payday is now found strictly after the record it
        follows, so the period closes on or after the day it opens.
        """
        derived = derive_periods(
            [(1, date(2030, 11, 13)), (2, date(2030, 11, 15))],
            (era_of(date(2030, 11, 13), 2, PRIOR),),
        )
        assert derived[-1].end_date >= derived[-1].start_date
        assert derived[-1].end_date == date(2030, 11, 18)

    def test_it_rolls_into_the_next_era_when_the_covering_eras_grid_is_spent(self):
        """The last A payday before B is 06-19; the payday after it is B's first."""
        assert horizon_step((ERA_A, ERA_B), date(2026, 6, 19)) == (1, 0)
        assert payday_after((ERA_A, ERA_B), date(2026, 6, 19)) == date(2026, 7, 1)

    def test_it_stays_on_the_covering_eras_grid_while_that_grid_reaches(self):
        assert horizon_step((ERA_A, ERA_B), date(2026, 3, 13)) == (0, 6)
        assert payday_after((ERA_A, ERA_B), date(2026, 3, 13)) == date(2026, 3, 27)

    def test_the_planned_paydays_after_the_record_are_one_sequence(self):
        """``payday_after`` is the FIRST of ``planned_paydays_after``; the batch ceiling is the second.

        Plan step ``pay_calendar:C17-c-2a``: the floor and its mirror read
        one sequence, so they cannot come apart.  Worked on the ruled
        example with the record ending 03-13 (A's 06-19 is the last of A's
        paydays either way, so the sequence crosses the seam at 07-01).
        """
        eras = (ERA_A, ERA_B)
        planned = planned_paydays_after(eras, date(2026, 3, 13))
        first = next(planned)
        assert first == payday_after(eras, date(2026, 3, 13)) == date(2026, 3, 27)
        assert next(planned) == date(2026, 4, 10)
        assert list(islice(planned_paydays_after(eras, date(2026, 6, 5)), 3)) == [
            date(2026, 6, 19), date(2026, 7, 1), date(2026, 7, 8),
        ]

    def test_an_eras_last_step_is_the_one_before_the_next_eras_first_payday(self):
        """``A`` pays 06-19 as its last; 07-03 would be its next but B pays 07-01."""
        assert last_step_of((ERA_A, ERA_B), 0) == 12
        assert projected_payday(ERA_A.effective_from, ERA_A.rhythm, 12) == date(2026, 6, 19)
        assert last_step_of((ERA_A, ERA_B), 1) is None

    def test_a_record_is_matched_over_the_PLANNED_list_across_the_seam(self):
        """The 02-02 paycheck paid a day early stands for the NEXT era's first, not the old era's last.

        Eras 14 days from 01-02 and 7 days from 02-02; the record ends 02-01.
        Inside the first era's grid alone the nearest planned payday is
        01-30 (two days), so the record would close on 02-01 and a paycheck
        would be projected on 02-02 -- the phantom the matching rule exists to
        refuse.  Over the planned list the nearest is the next era's 02-02
        (one day), and the paycheck runs to 02-08.  *An adversarial review of
        this step built the case.*
        """
        eras = (era_of(date(2026, 1, 2), 14), era_of(date(2026, 2, 2), 7))
        derived = derive_periods(
            [(1, date(2026, 1, 2)), (2, date(2026, 1, 16)), (3, date(2026, 2, 1))],
            eras,
        )
        assert derived[-1].end_date == date(2026, 2, 8)
        assert payday_after(eras, date(2026, 2, 1)) == date(2026, 2, 9)
        assert horizon_step(eras, date(2026, 2, 1)) == (1, 1)

    def test_the_first_paycheck_of_a_holiday_minted_era_closes_on_its_own_grid(self):
        """The prior-displaced first payday belongs to the new era, so its period does too."""
        old = era_of(date(2030, 1, 5), 7)
        new = era_of(date(2030, 11, 28), 14, PRIOR)
        assert payday_after((old, new), date(2030, 11, 27)) == date(2030, 12, 12)


# ---------------------------------------------------------------------------
# What the calendar refuses
# ---------------------------------------------------------------------------


class TestTheEraSequenceIsValidated:
    """``validate_eras``, through the derivation: what no calendar can be derived from."""

    def test_no_era_is_refused(self):
        with pytest.raises(PayCalendarError, match="at least one era"):
            derive_periods([], ())

    def test_eras_out_of_order_are_refused(self):
        with pytest.raises(PayCalendarError, match="strictly ascending"):
            derive_periods([], (ERA_B, ERA_A))

    def test_a_repeated_effective_from_is_refused(self):
        with pytest.raises(PayCalendarError, match="strictly ascending"):
            derive_periods([], (ERA_A, era_of(ERA_A.effective_from, 7)))

    def test_first_paydays_that_cross_are_refused(self):
        """Ascending phases whose FIRST PAYDAYS invert: one era would pay nothing.

        ``next`` pushes a Saturday phase to Monday; ``prior`` pulls the
        following Sunday's back to Friday.  Refused loudly at the value
        boundary, as a repeated payday is; which DOOR keeps a stored sequence
        out of this state is the writer's question (the floor bounds the
        batch's NEW paydays, not the era's first, and an adversarial review
        of this step drove a sequence through the pure door functions that
        reaches it -- see the ledger row the tick files).
        """
        saturday, sunday = date(2026, 11, 28), date(2026, 11, 29)
        crossed = (era_of(saturday, 14, NEXT), era_of(sunday, 7, PRIOR))
        assert first_payday_of(crossed[0]) > first_payday_of(crossed[1])
        with pytest.raises(PayCalendarError, match="does not fall after"):
            derive_periods([], crossed)

    def test_every_eras_cadence_is_validated(self):
        with pytest.raises(PayCalendarError, match="at least 1 day"):
            derive_periods([], (ERA_A, era_of(date(2027, 1, 1), 0)))


# ---------------------------------------------------------------------------
# The piecewise projection
# ---------------------------------------------------------------------------


class TestTheProjectionIsPiecewise:
    """The ruled worked example, read off the calendar the app builds."""

    def test_the_last_saved_paycheck_closes_on_ITS_eras_grid(self):
        """03-13 is A's paycheck, so it runs to 03-26, not to a day on B's grid."""
        calendar = _calendar(TRUNCATED_RECORD, (ERA_A, ERA_B))
        assert calendar.periods[-1].end_date == date(2026, 3, 26)
        assert calendar.periods[-1].end_is_projected is True

    def test_april_to_june_project_on_A_and_july_onward_on_B(self):
        calendar = _calendar(TRUNCATED_RECORD, (ERA_A, ERA_B))
        projected = list(
            period for period in projected_paychecks(calendar.periods, calendar.eras)
            if period.start_date <= date(2026, 7, 22)
        )
        assert [p.start_date for p in projected] == [
            date(2026, 3, 27), date(2026, 4, 10), date(2026, 4, 24),
            date(2026, 5, 8), date(2026, 5, 22), date(2026, 6, 5),
            date(2026, 6, 19),
            date(2026, 7, 1), date(2026, 7, 8), date(2026, 7, 15),
            date(2026, 7, 22),
        ]

    def test_the_seam_closes_As_last_paycheck_the_day_before_B_opens(self):
        calendar = _calendar(TRUNCATED_RECORD, (ERA_A, ERA_B))
        last_of_a = calendar.span_containing(date(2026, 6, 30))
        first_of_b = calendar.span_containing(date(2026, 7, 1))
        assert (last_of_a.start_date, last_of_a.end_date) == (
            date(2026, 6, 19), date(2026, 6, 30),
        )
        assert (first_of_b.start_date, first_of_b.end_date) == (
            date(2026, 7, 1), date(2026, 7, 7),
        )

    def test_the_ordinal_continues_across_the_seam(self):
        calendar = _calendar(TRUNCATED_RECORD, (ERA_A, ERA_B))
        assert calendar.periods[-1].period_index == 1
        assert calendar.span_containing(date(2026, 3, 27)).period_index == 2
        assert calendar.span_containing(date(2026, 6, 19)).period_index == 8
        assert calendar.span_containing(date(2026, 7, 1)).period_index == 9
        assert calendar.span_containing(date(2026, 7, 8)).period_index == 10

    def test_the_axis_tiles_across_the_seam(self):
        """``PeriodWindow`` refuses a hole and an overlap; the seam produces neither."""
        calendar = _calendar(TRUNCATED_RECORD, (ERA_A, ERA_B))
        window = calendar.projection_axis(date(2026, 3, 13), date(2026, 9, 30))
        assert window.periods[0].start_date == date(2026, 3, 13)
        assert window.periods[-1].end_date >= date(2026, 9, 30)
        assert PeriodWindow(periods=window.periods).periods == window.periods

    def test_a_single_era_owner_reads_exactly_as_before(self):
        """Production's shape: one era, ``none`` -- ``$0.00`` by construction."""
        paydays = [
            (index + 1, date(2026, 3, 26) + timedelta(days=14 * index))
            for index in range(63)
        ]
        calendar = _calendar(paydays, (era_of(date(2026, 3, 26), 14),))
        assert calendar.periods[-1].end_date == date(2028, 8, 23)
        span = calendar.span_containing(date(2030, 1, 1))
        # 2026-03-26 + 98 fortnights is 2029-12-27 (1,372 days; 2028 is a
        # leap year), the 99th paycheck, running to the day before 2030-01-10.
        assert span.start_date == date(2029, 12, 27)
        assert span.end_date == date(2030, 1, 9)
        assert span.period_index == 98
        assert span.start_date == date(2026, 3, 26) + timedelta(days=14 * 98)

    def test_it_matches_a_brute_force_listing_over_randomised_era_sequences(self):
        """The seam and the ordinal, swept rather than argued.

        The reference LISTS each era's cash paydays -- its grid displaced
        under its own convention, from its first payday up to the day before
        the next era's first -- and concatenates the eras; the producer walks
        :func:`~app.services.pay_calendar._views.projected_paychecks` from the
        record.  Starts, ends and ordinals must agree period for period.

        The sequences are built the way the doors build them: each later
        era's first payday lands at or after the previous era's NEXT payday
        past the record it left (the floor) and before the one after that
        (the gap rule, R-PC67), and the record ends somewhere inside an era
        that is not the last -- so every case crosses at least one seam.
        Cadences run from the collision floor to 40, both displacing
        conventions and ``none`` are drawn, and the counters below assert
        that a seam was actually displaced and that a case rolled straight
        into the next era from the record.
        """
        rng = random.Random(4471)
        floor = shortest_collision_free_cadence()
        mismatches, displaced_seams, rolled_at_record = [], 0, 0

        for _ in range(600):
            eras = [era_of(
                date(2027, 1, 1) + timedelta(days=rng.randint(0, 27)),
                rng.randint(floor, 40), rng.choice([PRIOR, NEXT, NONE]),
            )]
            for _more in range(rng.randint(1, 2)):
                previous = eras[-1]
                # The record the previous era left: its first payday plus a
                # few of its own paychecks; the next era opens between the
                # following payday and the one after it.
                left = previous.effective_from + timedelta(
                    days=previous.rhythm.cadence_days * rng.randint(1, 4),
                )
                lower = next_grid_payday_after(
                    previous.effective_from, previous.rhythm.cadence_days,
                    previous.rhythm.shift, left,
                )
                candidate_shift = rng.choice([PRIOR, NEXT, NONE])
                candidate_cadence = rng.randint(floor, 40)
                phase = None
                for offset in range(previous.rhythm.cadence_days):
                    nominal = lower + timedelta(days=offset)
                    if nominal <= previous.effective_from:
                        continue
                    if shift_to_business_day(nominal, candidate_shift) >= lower:
                        phase = nominal
                        break
                if phase is None:
                    break
                eras.append(era_of(phase, candidate_cadence, candidate_shift))
            if len(eras) < 2:
                continue
            eras = tuple(eras)
            # The record: the earliest era's first payday and a few more of
            # its paydays, so the horizon sits inside an era that is not the
            # last.
            recorded_era = eras[0]
            recorded = [
                projected_payday(recorded_era.effective_from, recorded_era.rhythm, n)
                for n in range(rng.randint(1, 3))
            ]
            recorded = [d for d in recorded if d < first_payday_of(eras[1])]
            try:
                calendar = _calendar(
                    [(i + 1, d) for i, d in enumerate(recorded)], eras,
                )
            except PayCalendarError:
                # A generated sequence the calendar refuses (first paydays
                # crossing at a seam two conventions share) is a sequence no
                # door writes; the reference has nothing to say about it.
                continue

            reference = []
            for index, era in enumerate(eras):
                bound = (
                    first_payday_of(eras[index + 1])
                    if index + 1 < len(eras) else date(2028, 12, 31)
                )
                steps = 0
                while True:
                    payday = projected_payday(era.effective_from, era.rhythm, steps)
                    if payday >= bound:
                        break
                    reference.append(payday)
                    steps += 1
            reference = [d for d in reference if d > recorded[-1]]
            reference = reference[:40]
            walked = []
            for period in projected_paychecks(calendar.periods, calendar.eras):
                if len(walked) == len(reference):
                    break
                walked.append(period)

            where = tuple((e.effective_from, e.rhythm.cadence_days, e.rhythm.shift.name) for e in eras)
            expected_ends = [
                following - timedelta(days=1)
                for following in reference[1:]
            ]
            if (
                [p.start_date for p in walked] != reference
                or [p.end_date for p in walked[:-1]] != expected_ends
                or [p.period_index for p in walked] != list(
                    range(len(recorded), len(recorded) + len(walked)),
                )
            ):
                mismatches.append((where, recorded))
            displaced_seams += any(
                not is_business_day(e.effective_from) for e in eras[1:]
            )
            rolled_at_record += horizon_step(eras, recorded[-1])[0] > 0

        assert not mismatches, mismatches[:3]
        assert displaced_seams > 0, "no seam fell on a closed day"
        assert rolled_at_record > 0, "no record ended on an era's last payday"

    def test_a_projected_period_is_never_offered_a_step_outside_its_era(self):
        """A day just past the seam is B's step 0, not A's step 13 or B's step -1."""
        calendar = _calendar(TRUNCATED_RECORD, (ERA_A, ERA_B))
        found = project_period_after(calendar.periods, calendar.eras, date(2026, 7, 2))
        assert found.start_date == date(2026, 7, 1)
        assert found.end_date == date(2026, 7, 7)
