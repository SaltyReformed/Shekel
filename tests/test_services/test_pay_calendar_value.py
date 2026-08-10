"""
Shekel Budget App -- The pay calendar VALUE (plan step C2-a)

C1 proved the derivation; this proves the value built on it.  The step's whole
point is that ``app/`` holds SIX implementations of "which pay period contains
this date" (ledger row **P6**) which disagree at the edges, so these tests are
written at the edges rather than in the middle:

* the three QUESTIONS answer differently, and each difference is pinned --
  containment says ``None`` past the horizon where the span PROJECTS and the
  filing rule CLAMPS.  A test that only asked mid-schedule would pass against
  all six of the implementations this value replaces;
* the FILING rule is proven equal to the chain it deletes
  (``loan_ledger.find_period_containing_date`` composed with
  ``resolve_anchor_pay_period``) over every MATERIALISED shape.  A GAPPED
  shape is absent because it is unconstructible here -- derived periods tile,
  which is the value's own invariant -- so the gapped half of that
  equivalence lives in the session probe that drove the 2026-08-10 ruling,
  over stored-style periods where a hole still exists;
* the TILING invariant is asserted directly, because it is what makes the
  recurrence arc's ``PeriodCalendar.__post_init__`` refusals unconstructible
  rather than merely unused;
* a WINDOW keeps the ends the whole calendar derived (ledger row **P14**), and
  the control shows the value a window would report if it were re-derived --
  the ``$150,000.00`` shape;
* production's own numbers appear as the four ``loan_opening`` entries dated
  years before the first payday, which is the state the filing clamp exists for
  and the one a contiguous test schedule never reaches.

**No date here is read from a clock.**  Every date is a literal or explicit
``timedelta`` arithmetic on one, so these pass identically under
``TZ=Pacific/Kiritimati`` and under the ``SHEKEL_FAKE_TODAY`` sweep.  The value
has no clock; a test that gave it one would be testing the fixture.
"""

from datetime import date, timedelta

import pytest

from app.services.pay_calendar import (
    DerivedPeriod,
    PayCalendar,
    PayCalendarError,
    PeriodWindow,
)

#: A contiguous biweekly schedule -- production's shape, four paydays of it.
#: Ids are deliberately not 0-based so a test cannot pass by confusing an id
#: with an ordinal, which is the confusion ledger row P13 is about.
BIWEEKLY = [
    (10, date(2026, 1, 2)),
    (11, date(2026, 1, 16)),
    (12, date(2026, 1, 30)),
    (13, date(2026, 2, 13)),
]

#: Paydays that are NOT one cadence apart, so ``lead(start) - 1`` and
#: ``start + cadence - 1`` give different answers.  Every test about a derived
#: end needs this shape: on ``BIWEEKLY`` the two rules coincide, so an
#: assertion written there passes against the defect it is meant to catch.
OFF_CADENCE = [
    (10, date(2026, 1, 2)),
    (11, date(2026, 1, 16)),
    (12, date(2026, 1, 20)),
]

#: A calendar holding an UNSAVED candidate payday, which
#: :func:`~app.services.pay_calendar.derive_periods` accepts by design and plan
#: step C3's writer will build.  ``filing_period`` must not hand one of these
#: to a ``NOT NULL`` foreign key.
WITH_UNSAVED = [
    (None, date(2026, 1, 2)),
    (11, date(2026, 1, 16)),
]

#: The shapes whose paydays are all SAVED.  The filing-rule equivalence is
#: claimed over these only: the chain it replaces reads ORM rows.
MATERIALISED_SHAPES = [
    ("contiguous biweekly", BIWEEKLY, 14),
    ("off-cadence paydays", OFF_CADENCE, 14),
    ("one payday only", [(1, date(2026, 1, 2))], 14),
    (
        "paydays a day apart",
        [(1, date(2026, 1, 2)), (2, date(2026, 1, 3)), (3, date(2026, 1, 4))],
        14,
    ),
    ("a long stretch between paydays",
     [(1, date(2026, 1, 2)), (2, date(2026, 3, 20))], 14),
    ("monthly cadence", [(1, date(2026, 1, 1)), (2, date(2026, 2, 1))], 30),
]

#: Every schedule shape the payday model can express, including the ones
#: production cannot supply.  Each is a ``(name, paydays, cadence)`` triple.
SHAPES = [
    ("contiguous biweekly", BIWEEKLY, 14),
    ("off-cadence paydays", OFF_CADENCE, 14),
    ("an unsaved candidate payday", WITH_UNSAVED, 14),
    ("one payday only", [(1, date(2026, 1, 2))], 14),
    (
        "paydays a day apart -- the one-day period ck_pay_periods_date_order "
        "forbids and C4 legalises",
        [(1, date(2026, 1, 2)), (2, date(2026, 1, 3)), (3, date(2026, 1, 4))],
        14,
    ),
    (
        "a long stretch between paydays",
        [(1, date(2026, 1, 2)), (2, date(2026, 3, 20))],
        14,
    ),
    ("monthly cadence", [(1, date(2026, 1, 1)), (2, date(2026, 2, 1))], 30),
]


def calendar(paydays=None, cadence=14, user_id=1):
    """Build a calendar, defaulting to the contiguous biweekly shape.

    Args:
        paydays: ``(period_id, payday)`` pairs; ``BIWEEKLY`` when omitted.
        cadence: Days between paydays.
        user_id: The owner.

    Returns:
        The :class:`~app.services.pay_calendar.PayCalendar`.
    """
    return PayCalendar.from_paydays(
        BIWEEKLY if paydays is None else paydays, cadence, user_id,
    )


class TestThePeriodsAreDerivedAndCannotBeSuppliedOrChanged:
    """The value stores the paydays and derives everything else."""

    def test_periods_are_not_an_init_argument(self):
        """Supplying periods is refused, so no caller can plant a hole.

        The whole tiling argument rests on the periods coming from the
        derivation.  If they could be passed in, every invariant below would be
        a claim about a well-behaved caller.
        """
        with pytest.raises(TypeError):
            PayCalendar(
                user_id=1, paydays=(), cadence_days=14, periods=(),
            )

    def test_the_value_is_frozen(self):
        """A derived value that could be reassigned would need a reconciler."""
        cal = calendar()
        with pytest.raises(AttributeError):
            cal.periods = ()

    def test_paydays_are_canonicalised_so_input_order_does_not_leak(self):
        """One payday SET is one value, whatever order it arrived in.

        The firing control for a real defect: before ``__post_init__``
        rewrote ``paydays`` off the derivation, these two compared UNEQUAL
        while every answer they gave was identical.
        """
        forward = calendar(BIWEEKLY)
        backward = calendar(list(reversed(BIWEEKLY)))
        assert forward == backward
        assert forward.paydays == backward.paydays == tuple(BIWEEKLY)

    def test_equality_is_on_the_facts(self):
        """Owner and cadence are facts; changing either is a different calendar."""
        assert calendar() != calendar(user_id=2)
        assert calendar() != calendar(cadence=15)

    def test_a_bad_payday_set_is_refused_at_construction(self):
        """Validation happens once, where the fact enters."""
        with pytest.raises(PayCalendarError):
            calendar([(1, date(2026, 1, 2)), (2, date(2026, 1, 2))])


class TestThePeriodsTile:
    """``[opening_bound(), horizon()]`` is covered once, with no hole."""

    @pytest.mark.parametrize(
        "name,paydays,cadence", SHAPES, ids=[s[0][:40] for s in SHAPES],
    )
    def test_every_day_in_the_saved_span_has_exactly_one_period(
        self, name, paydays, cadence,
    ):
        """Consecutive paydays define adjacent intervals, by construction.

        This is what makes the recurrence arc's ``PeriodCalendar.__post_init__``
        refusals -- an overlapping or reversed schedule -- unconstructible here
        rather than merely unchecked.  A fence with no subject left.
        """
        cal = calendar(paydays, cadence)
        day = cal.opening_bound()
        while day <= cal.horizon():
            covering = [
                period for period in cal.periods
                if period.start_date <= day <= period.end_date
            ]
            assert len(covering) == 1, f"{name}: {day} covered by {len(covering)}"
            day += timedelta(days=1)

    def test_a_gap_between_paydays_is_absorbed_not_left_as_a_hole(self):
        """The state five runtime fences police is not expressible here.

        Paydays 2026-01-02, 01-16 then a jump to 02-20 -- a five-day hole under
        the STORED model, where ``end_date`` would sit at 01-29 and nothing
        would cover 01-30..02-19.  Derived, the second period simply runs to
        the day before the next payday.
        """
        cal = calendar(
            [(1, date(2026, 1, 2)), (2, date(2026, 1, 16)), (3, date(2026, 2, 20))],
        )
        assert cal.periods[1].end_date == date(2026, 2, 19)
        for day in (date(2026, 1, 30), date(2026, 2, 19)):
            assert cal.period_containing(day) is cal.periods[1]


class TestTheThreeQuestionsAnswerDifferently:
    """Containment, the span, and the filing rule diverge past the horizon."""

    def test_at_the_horizon_all_three_agree(self):
        """Inside the schedule there is one answer, which is why P6 went unnoticed."""
        cal = calendar()
        last_day = cal.horizon()
        assert cal.period_containing(last_day) is cal.periods[-1]
        assert cal.span_containing(last_day) is cal.periods[-1]
        assert cal.filing_period(last_day) is cal.periods[-1]

    def test_one_day_past_the_horizon_they_split(self):
        """The edge the six implementations disagreed at, pinned.

        Containment refuses, the span projects a period no foreign key can
        point at, and the filing rule clamps back onto a saved row.
        """
        cal = calendar()
        beyond = cal.horizon() + timedelta(days=1)

        assert cal.period_containing(beyond) is None

        span = cal.span_containing(beyond)
        assert span is not None
        assert span.period_id is None
        assert span.period_index == cal.periods[-1].period_index + 1
        assert span.start_date == beyond
        assert span.end_date == beyond + timedelta(days=13)
        assert span.end_is_projected is True

        filed = cal.filing_period(beyond)
        assert filed is cal.periods[-1]
        assert filed.period_id == 13

    def test_before_the_first_payday_the_span_refuses_and_filing_clamps(self):
        """Nothing is projected backwards; a record still needs a paycheck.

        This is production's own state: four ``loan_opening`` entries are dated
        2018-12-01 and 2023-02-14 against a first payday of 2026-03-26.
        """
        cal = calendar()
        long_before = date(2018, 12, 1)
        assert cal.period_containing(long_before) is None
        assert cal.span_containing(long_before) is None
        assert cal.filing_period(long_before) is cal.periods[0]

    def test_the_span_is_total_from_the_opening_payday_onward(self):
        """Two years of days, every one covered by the span it reports."""
        cal = calendar()
        day = cal.opening_bound()
        end = day + timedelta(days=730)
        while day <= end:
            span = cal.span_containing(day)
            assert span is not None, day
            assert span.start_date <= day <= span.end_date, day
            day += timedelta(days=1)

    def test_projected_spans_are_contiguous_with_the_saved_ones(self):
        """The projection continues the schedule rather than restarting it."""
        cal = calendar()
        previous = cal.periods[-1]
        for step in range(1, 30):
            day = cal.horizon() + timedelta(days=step * 14 - 13)
            span = cal.span_containing(day)
            assert span.start_date == previous.end_date + timedelta(days=1)
            assert span.period_index == previous.period_index + 1
            previous = span

    def test_the_span_projects_at_the_owners_cadence_not_a_fixed_fortnight(self):
        """Ledger row P20: the axis this replaces was hardcoded to 14 days."""
        cal = calendar([(1, date(2026, 1, 1)), (2, date(2026, 2, 1))], cadence=30)
        span = cal.span_containing(date(2026, 4, 15))
        assert span.start_date == date(2026, 4, 2)
        assert span.end_date == date(2026, 5, 1)


class TestTheFilingRuleEqualsTheChainItDeletes:
    """The 2026-08-10 ruling's evidence, re-run as a test."""

    @staticmethod
    def chain(cal, day):
        """Answer as ``loan_ledger`` does today: containment, else two fallbacks.

        A transcription of ``find_period_containing_date`` composed with
        ``resolve_anchor_pay_period``, kept here rather than imported so this
        test still grades the rule after plan step C2-d deletes both.

        Args:
            cal: The calendar to answer over.
            day: The date to file.

        Returns:
            The :class:`~app.services.pay_calendar.DerivedPeriod` the old chain
            would choose.
        """
        containing, fallback = None, None
        for period in cal.periods:
            if period.start_date <= day <= period.end_date:
                if containing is None or period.period_index > containing.period_index:
                    containing = period
            elif period.end_date < day:
                if fallback is None or period.period_index > fallback.period_index:
                    fallback = period
        located = containing if containing is not None else fallback
        return located if located is not None else cal.periods[0]

    @pytest.mark.parametrize(
        "name,paydays,cadence", MATERIALISED_SHAPES,
        ids=[s[0][:40] for s in MATERIALISED_SHAPES],
    )
    def test_the_two_rules_agree_on_every_day_of_every_shape(
        self, name, paydays, cadence,
    ):
        """One clamp replaces three branches over two functions.

        Over MATERIALISED shapes only, and the scope is the honest one:
        the chain being deleted reads ``PayPeriod`` ORM rows, every one of
        which is saved, so it has no behaviour over an unsaved candidate to
        be equivalent TO.  Where the calendar holds one the new rule
        deliberately differs -- it skips it, because the answer feeds a
        ``NOT NULL`` foreign key.
        """
        cal = calendar(paydays, cadence)
        day = cal.opening_bound() - timedelta(days=40)
        end = cal.horizon() + timedelta(days=200)
        while day <= end:
            assert cal.filing_period(day) == self.chain(cal, day), f"{name} {day}"
            day += timedelta(days=1)

    def test_the_comparison_can_fail(self):
        """The firing control: the old chain and a WRONG rule must not agree.

        Without this the test above would pass over any rule that happened to
        equal the transcription, including a transcription that had drifted.
        """
        cal = calendar()
        beyond = cal.horizon() + timedelta(days=1)
        assert self.chain(cal, beyond) is cal.periods[-1]
        assert cal.periods[0] != self.chain(cal, beyond)

    def test_filing_never_returns_an_unsaved_or_projected_period(self):
        """A ``NOT NULL`` foreign key cannot point at ``period_id = None``.

        ``WITH_UNSAVED`` is the shape that made this fire.  Before an
        adversarial review of this step, ``filing_period`` searched every
        payday and returned the unsaved one -- on the ordinary containment path
        as well as the clamp -- because "not a projection" was mistaken for
        "saved".
        """
        for paydays in (BIWEEKLY, OFF_CADENCE, WITH_UNSAVED):
            cal = calendar(paydays)
            for offset in (-500, 0, 5, 400, 5000):
                filed = cal.filing_period(
                    cal.opening_bound() + timedelta(days=offset),
                )
                assert filed.period_id is not None, (paydays, offset)

    def test_filing_raises_when_every_payday_is_an_unsaved_candidate(self):
        """There is no id to point at, so there is no safe value to invent."""
        cal = calendar([(None, date(2026, 1, 2)), (None, date(2026, 1, 16))])
        with pytest.raises(PayCalendarError, match="no materialised pay period"):
            cal.filing_period(date(2026, 1, 5))

    def test_filing_on_an_empty_calendar_raises_rather_than_guessing(self):
        """The companion role holds no paydays, and production has one."""
        empty = PayCalendar.from_paydays([], 14, user_id=2)
        with pytest.raises(PayCalendarError, match="no materialised pay period"):
            empty.filing_period(date(2026, 1, 1))


class TestTheTwoOrderingSearchesAreMirrors:
    """``on_or_after`` and ``on_or_before`` bracket any day."""

    def test_on_or_before_is_the_missing_mirror(self):
        """Three modules open-coded this; one of them as a scan with fallbacks."""
        cal = calendar()
        assert cal.period_starting_on_or_before(date(2026, 1, 15)) is cal.periods[0]
        assert cal.period_starting_on_or_before(date(2026, 1, 16)) is cal.periods[1]
        assert cal.period_starting_on_or_before(date(2025, 1, 1)) is None

    def test_on_or_after_answers_the_next_paycheck(self):
        """The half the recurrence placement axis already used."""
        cal = calendar()
        assert cal.period_starting_on_or_after(date(2026, 1, 3)) is cal.periods[1]
        assert cal.period_starting_on_or_after(date(2026, 1, 16)) is cal.periods[1]
        assert cal.period_starting_on_or_after(date(2030, 1, 1)) is None

    def test_they_bracket_every_day_inside_the_schedule(self):
        """A day is between the paycheck that opened and the one that follows."""
        cal = calendar()
        day = cal.opening_bound()
        while day <= cal.horizon():
            before = cal.period_starting_on_or_before(day)
            after = cal.period_starting_on_or_after(day)
            assert before is not None
            assert before.start_date <= day
            if after is not None:
                assert after.start_date >= day
                assert after.period_index - before.period_index in (0, 1)
            day += timedelta(days=1)

    def test_on_or_before_and_containment_differ_only_past_the_horizon(self):
        """Where they differ is exactly where P6's implementations disagreed."""
        cal = calendar()
        day = cal.opening_bound()
        while day <= cal.horizon():
            assert cal.period_containing(day) is cal.period_starting_on_or_before(day)
            day += timedelta(days=1)
        beyond = cal.horizon() + timedelta(days=1)
        assert cal.period_containing(beyond) is None
        assert cal.period_starting_on_or_before(beyond) is cal.periods[-1]


class TestAWindowIsAViewAndKeepsTheRealEnds:
    """Ledger row P14, made structural by the type."""

    def test_a_window_keeps_the_end_the_whole_calendar_derived(self):
        """The defect: re-deriving over a slice moves its last period's end.

        **OFF-CADENCE on purpose.**  A first cut of this test used ``BIWEEKLY``,
        where the paydays sit exactly one cadence apart so ``lead(start) - 1``
        and ``start + cadence - 1`` COINCIDE -- the assertion held identically
        against a ``window()`` that re-derived its slice, so it graded nothing.
        An adversarial review of this step caught it.  Here the third payday is
        four days after the second, so the two rules disagree by ten days.
        """
        cal = calendar(OFF_CADENCE)
        window = cal.window(first_index=0, count=2)
        assert [period.end_date for period in window] == [
            date(2026, 1, 15), date(2026, 1, 19),
        ]

    def test_the_control_shows_what_re_deriving_the_slice_would_give(self):
        """The firing control for the test above -- the ``$150,000.00`` shape.

        Building a CALENDAR from the same two paydays projects the second one's
        end off the cadence instead of reading it from the payday that follows,
        and it lands TEN DAYS LATER than the truth.  One period, two answers,
        decided by which window asked -- which is what a window being a
        different TYPE prevents.
        """
        cal = calendar(OFF_CADENCE)
        re_derived = calendar(OFF_CADENCE[:2])
        assert cal.periods[1].end_date == date(2026, 1, 19)
        assert re_derived.periods[1].end_date == date(2026, 1, 29)
        assert re_derived.periods[1].end_date - cal.periods[1].end_date == (
            timedelta(days=10)
        )
        assert re_derived.periods[1].end_is_projected is True
        assert cal.periods[1].end_is_projected is False

    def test_a_window_is_not_a_calendar(self):
        """It carries no cadence and no owner, so nothing can derive from it."""
        window = calendar().window(0, 2)
        assert isinstance(window, PeriodWindow)
        assert not isinstance(window, PayCalendar)
        assert not hasattr(window, "cadence_days")

    def test_a_window_answers_containment_within_itself(self):
        """Scoped on purpose: a day outside the reported columns has no column."""
        cal = calendar()
        window = cal.window(first_index=1, count=2)
        assert window.containing(date(2026, 1, 20)) is cal.periods[1]
        assert window.containing(date(2026, 1, 2)) is None
        assert window.containing(date(2026, 2, 20)) is None

    def test_windows_past_the_end_and_of_no_periods_are_empty_not_errors(self):
        """"No periods requested" and "the calendar ends first" are answers."""
        cal = calendar()
        assert len(cal.window(first_index=99, count=3)) == 0
        assert len(cal.window(first_index=0, count=0)) == 0
        assert len(cal.window(first_index=2, count=10)) == 2

    def test_overlapping_takes_both_bounds_inclusively(self):
        """The calendar-month slice the reporting surfaces ask for."""
        cal = calendar()
        window = cal.overlapping(date(2026, 1, 15), date(2026, 1, 16))
        assert [period.period_id for period in window] == [10, 11]

    def test_a_crossed_range_is_refused_rather_than_answered_empty(self):
        """An empty range and a crossed one look identical in the result."""
        with pytest.raises(PayCalendarError, match="ends before it starts"):
            calendar().overlapping(date(2026, 2, 1), date(2026, 1, 1))


class TestTheAxisReplacesTheSyntheticProjection:
    """Ledger rows P17 and P20."""

    def test_the_axis_is_saved_where_it_can_be_and_projected_beyond(self):
        """No fabricated id ever enters the real ``pay_periods.id`` namespace."""
        cal = calendar()
        axis = cal.axis(date(2026, 2, 20), date(2026, 4, 10))
        assert [period.period_id for period in axis] == [13, None, None, None, None]
        assert [period.period_index for period in axis] == [3, 4, 5, 6, 7]

    def test_the_axis_tiles_the_range_it_is_asked_for(self):
        """Every day between the bounds falls in exactly one axis period."""
        cal = calendar()
        first, last = date(2026, 2, 20), date(2027, 6, 30)
        axis = cal.axis(first, last)
        day = first
        while day <= last:
            covering = [
                period for period in axis
                if period.start_date <= day <= period.end_date
            ]
            assert len(covering) == 1, f"{day} covered by {len(covering)}"
            day += timedelta(days=1)

    def test_the_axis_of_a_monthly_owner_is_monthly(self):
        """The defect P20 prices at $588,959.22 over twenty years.

        The replaced producer took ``cadence_days=14`` at all six call sites,
        so a monthly-paid owner was credited 26 contributions a year instead of
        12.  Here the count follows the owner's own cadence.
        """
        cal = calendar([(1, date(2026, 1, 1)), (2, date(2026, 2, 1))], cadence=30)
        axis = cal.axis(date(2026, 1, 1), date(2026, 12, 31))
        assert len(axis) == 13
        biweekly = calendar()
        assert len(biweekly.axis(date(2026, 1, 2), date(2026, 12, 31))) == 26

    def test_an_axis_wholly_inside_the_schedule_projects_nothing(self):
        """No projection where the saved calendar already answers."""
        cal = calendar()
        axis = cal.axis(date(2026, 1, 2), date(2026, 2, 12))
        assert all(period.period_id is not None for period in axis)

    def test_an_empty_calendar_yields_an_empty_axis(self):
        """Nothing to project FROM, so nothing is invented."""
        empty = PayCalendar.from_paydays([], 14, user_id=2)
        assert len(empty.axis(date(2026, 1, 1), date(2027, 1, 1))) == 0


class TestTheRemainingLookupsMovedIntact:
    """What the recurrence arc's calendar already answered, unchanged."""

    def test_the_bounds_of_an_empty_calendar_are_none_not_an_error(self):
        """The companion role, which by design holds no paydays of its own."""
        empty = PayCalendar.from_paydays([], 14, user_id=2)
        assert empty.opening_bound() is None
        assert empty.horizon() is None
        assert empty.period_containing(date(2026, 1, 1)) is None
        assert empty.span_containing(date(2026, 1, 1)) is None

    def test_the_horizon_is_the_saved_end_and_does_not_move_with_the_projection(self):
        """The recurrence engine bounds generation by it to tell hole from "not yet"."""
        cal = calendar()
        assert cal.horizon() == date(2026, 2, 26)
        cal.span_containing(date(2030, 1, 1))
        assert cal.horizon() == date(2026, 2, 26)


class TestPeriodByIdIsIdentityNotASearch:
    """Plan step C2-b1: which STORED paycheck a rule's start period names."""

    def test_it_finds_the_period_carrying_the_id(self):
        """Keyed on ``period_id``, which is not the ordinal and not the index."""
        found = calendar().period_by_id(12)

        assert found is not None
        assert found.start_date == date(2026, 1, 30)
        # The fixture's ids start at 10 on purpose: an implementation that
        # confused the id with the ordinal would answer the FIRST period here.
        assert found.period_index == 2

    def test_none_in_is_none_out(self):
        """A rule may legitimately name no start period."""
        assert calendar().period_by_id(None) is None

    def test_an_id_that_names_no_period_answers_none(self):
        """A stale in-memory id outliving its ``ON DELETE SET NULL`` row."""
        assert calendar().period_by_id(9999) is None

    def test_it_answers_over_an_off_cadence_schedule_too(self):
        """Identity does not depend on the spacing, unlike every other lookup."""
        found = calendar(OFF_CADENCE).period_by_id(12)

        assert found is not None
        assert found.start_date == date(2026, 1, 20)

    def test_the_searchable_id_space_holds_only_saved_periods(self):
        """Every projection carries ``period_id = None``, so identity is saved-only.

        Ledger row **P21**: an ``{p.id: ...}`` map over a projected axis
        collapses because the projections SHARE that ``None``.  Here the
        consequence is the safe one -- a lookup cannot hand an unsaved period to
        a caller about to write a foreign key.

        Asserted structurally rather than by round-tripping the projection's own
        ``period_id``, which an adversarial review of this step showed was
        vacuous: that value IS ``None``, so the call returned at the guard
        without ever reaching the scan, and the test would have passed against a
        completely broken one.
        """
        cal = calendar()
        projected = cal.span_containing(date(2027, 1, 1))

        assert projected is not None and projected.period_id is None
        assert projected not in cal.periods
        assert all(period.period_id is not None for period in cal.periods)


class TestEarliestStartInMonthIsWhatMonthlyFirstAsks:
    """Plan step C2-b1: when a month's FIRST paycheck lands."""

    def test_it_returns_the_earliest_payday_of_a_month_holding_two(self):
        """January 2026 holds 01-02 and 01-16; the pattern fires on the first."""
        assert calendar().earliest_start_in_month(2026, 1) == date(2026, 1, 2)

    def test_it_returns_the_only_payday_of_a_month_holding_one(self):
        """February 2026 holds 02-13 alone."""
        assert calendar().earliest_start_in_month(2026, 2) == date(2026, 2, 13)

    def test_a_month_the_schedule_covers_but_opens_no_payday_in_answers_none(self):
        """A real answer, not an error -- and the distinction that matters.

        OFF_CADENCE opens 2026-01-02, 01-16 and 01-20, so its last period runs
        01-20 to 02-02 at a 14-day cadence: February 2nd IS covered by a
        paycheck, and February opens none.  "Which paycheck covers this day"
        and "does a paycheck START this month" are different questions, and a
        ``Monthly First`` rule asks the second.
        """
        cal = calendar(OFF_CADENCE)

        assert cal.period_containing(date(2026, 2, 2)) is not None
        assert cal.earliest_start_in_month(2026, 2) is None

    def test_a_month_past_the_horizon_answers_none_and_is_not_projected(self):
        """SAVED periods only, so a Monthly First rule cannot fire into thin air.

        This is the method's one sharp edge: the calendar PROJECTS for
        ``span_containing`` and must not here, because the answer selects a
        paycheck a generated row will be seated in by ``pay_period_id``.
        """
        cal = calendar()
        assert cal.span_containing(date(2026, 6, 1)) is not None
        assert cal.earliest_start_in_month(2026, 6) is None

    def test_it_does_not_confuse_the_same_month_in_another_year(self):
        """Both halves of the key are read, which one careless filter would not."""
        cal = calendar([(30, date(2025, 1, 9)), (31, date(2026, 1, 23))])

        assert cal.earliest_start_in_month(2025, 1) == date(2025, 1, 9)
        assert cal.earliest_start_in_month(2026, 1) == date(2026, 1, 23)


class TestTheDerivedPeriodContract:
    """What every consumer reads off the value."""

    def test_exactly_one_saved_period_reports_a_projected_end(self):
        """C1's flag, unchanged: the last one, and only when there is one."""
        cal = calendar()
        assert [period.end_is_projected for period in cal.periods] == [
            False, False, False, True,
        ]
        assert PayCalendar.from_paydays([], 14, 1).periods == ()

    def test_a_projected_span_is_a_derived_period_like_any_other(self):
        """One shape for saved and projected, discriminated only by ``period_id``."""
        span = calendar().span_containing(date(2027, 1, 1))
        assert isinstance(span, DerivedPeriod)
        assert span.period_id is None
