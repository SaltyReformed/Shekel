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
* the FILING rule is proven equal to the chain it deleted
  (``loan_ledger.find_period_containing_date`` composed with
  ``resolve_anchor_pay_period``) over every MATERIALISED shape, and separately
  over STORED-style period rows that a calendar cannot express at all -- a
  hole, two holes, an overlap, a shared boundary day, a runaway end.  Those
  are unconstructible through :class:`PayCalendar` because derived periods
  TILE, which is the value's own invariant, so they are built by hand.
  **That second half used to live in a session probe outside the repository**
  while four docstrings cited it as proof; plan step C2-d landed it here,
  along with the counterexample that states the equivalence's PRECONDITION
  (index order must agree with date order) rather than leaving it implied;
* the TILING invariant is asserted directly, because it is what made the
  recurrence arc's ``PeriodCalendar.__post_init__`` refusals unconstructible
  rather than merely unused -- plan step C2-b2 then deleted that class, so this
  is now the only place the property is asserted at all;
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
    containing_period,
    latest_started_period,
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

#: The same shape with the candidate INSIDE the saved set rather than at its
#: head, which is the axis ``WITH_UNSAVED`` does not vary and the only one on
#: which :meth:`PayCalendar.saved` behaves differently: filtering the candidate
#: out leaves a hole where slicing never can.  ``pay_period_write`` cannot
#: build it today (its floor keeps candidates after the last saved payday) and
#: plan step **C6** builds it by design.
WITH_INTERIOR_UNSAVED = [
    (10, date(2026, 1, 2)),
    (None, date(2026, 1, 16)),
    (12, date(2026, 1, 30)),
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

        This is what made the recurrence arc's ``PeriodCalendar.__post_init__``
        refusals -- an overlapping or reversed schedule -- unconstructible here
        rather than merely unchecked; plan step **C2-b2** then deleted that
        class, so this assertion is what the property rests on now.  A fence
        whose subject went, followed by the fence.
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
    def chain(periods, day):
        """Answer as ``loan_ledger`` did: containment, else two fallbacks.

        A transcription of ``find_period_containing_date`` composed with
        ``resolve_anchor_pay_period``, kept here rather than imported because
        plan step C2-d DELETED both -- so this is the only surviving statement
        of the rule the clamp replaced, and the equivalence has to be graded
        against it rather than against a live function.

        **It reduces by ``period_index``, and that is not decoration.**  Both
        fallbacks take the highest index and the last resort takes
        ``periods[0]`` -- position in an index-ordered list.  The clamp reduces
        by ``start_date``.  On a schedule where those two orders disagree the
        rules part company, which is what
        :meth:`test_index_order_disagreeing_with_date_order_is_the_one_divergence`
        pins.

        Takes a period SEQUENCE rather than a calendar, so the same
        transcription grades both datasets: the shapes a
        :class:`~app.services.pay_calendar.PayCalendar` can hold, and the
        stored-style shapes below that it cannot.

        Args:
            periods: The periods to answer over, as the stored rows would be --
                index order, whatever their dates do.
            day: The date to file.

        Returns:
            The :class:`~app.services.pay_calendar.DerivedPeriod` the old chain
            would choose.
        """
        containing, fallback = None, None
        for period in periods:
            if period.start_date <= day <= period.end_date:
                if containing is None or period.period_index > containing.period_index:
                    containing = period
            elif period.end_date < day:
                if fallback is None or period.period_index > fallback.period_index:
                    fallback = period
        located = containing if containing is not None else fallback
        return located if located is not None else periods[0]

    @staticmethod
    def clamp(periods, day):
        """Answer as the SHIPPED rule does, over a bare period sequence.

        :meth:`~app.services.pay_calendar.PayCalendar.filing_period` cannot be
        asked these shapes -- a calendar DERIVES its periods from paydays, so
        it tiles and can hold neither a hole nor an overlap.  This composes the
        two shipped pieces that method is built from
        (:func:`~app.services.pay_calendar.latest_started_period` plus the
        clamp to the earliest), so the rule under test is production code and
        not a second transcription.

        Args:
            periods: The periods to answer over, in any order.
            day: The date to file.

        Returns:
            The :class:`~app.services.pay_calendar.DerivedPeriod` the clamp
            chooses.
        """
        by_date = tuple(sorted(periods, key=lambda p: p.start_date))
        located = latest_started_period(by_date, day)
        return located if located is not None else by_date[0]

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
            assert cal.filing_period(day) == self.chain(cal.periods, day), f"{name} {day}"
            day += timedelta(days=1)

    def test_the_comparison_can_fail(self):
        """The firing control: the old chain and a WRONG rule must not agree.

        Without this the test above would pass over any rule that happened to
        equal the transcription, including a transcription that had drifted.
        """
        cal = calendar()
        beyond = cal.horizon() + timedelta(days=1)
        assert self.chain(cal.periods, beyond) is cal.periods[-1]
        assert cal.periods[0] != self.chain(cal.periods, beyond)

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

    # ---- the shapes a calendar CANNOT hold ---------------------------

    #: STORED-style period rows: a hole, two holes, an overlap, a stored end
    #: far past the cadence, and index order disagreeing with date order.  A
    #: :class:`PayCalendar` can express none of them -- it derives its periods
    #: from paydays, so they tile -- which is why the equivalence over these
    #: shapes had lived only in the session probe that drove the 2026-08-10
    #: ruling, an artifact outside the repository.  Built as
    #: :class:`~app.services.pay_calendar.DerivedPeriod` values by hand,
    #: bypassing the derivation on purpose: the point is to feed both rules a
    #: schedule the DELETED chain could receive from ``budget.pay_periods`` and
    #: the new one never builds.
    STORED_SHAPES = [
        (
            "a 14-day hole -- the P27 shape",
            [(1, 0, date(2026, 1, 2), date(2026, 1, 15)),
             (2, 1, date(2026, 1, 30), date(2026, 2, 12))],
        ),
        (
            "two holes",
            [(1, 0, date(2026, 1, 2), date(2026, 1, 15)),
             (2, 1, date(2026, 2, 1), date(2026, 2, 14)),
             (3, 2, date(2026, 3, 1), date(2026, 3, 14))],
        ),
        (
            "an overlap -- a stored end past the next payday",
            [(1, 0, date(2026, 1, 2), date(2026, 1, 20)),
             (2, 1, date(2026, 1, 16), date(2026, 1, 29))],
        ),
        (
            "a shared boundary day -- the pair BA-04 cannot see",
            [(1, 0, date(2026, 1, 2), date(2026, 1, 15)),
             (2, 1, date(2026, 1, 15), date(2026, 1, 28))],
        ),
        (
            "a stored end 400 days past the cadence",
            [(1, 0, date(2026, 1, 2), date(2027, 3, 1)),
             (2, 1, date(2027, 3, 2), date(2027, 3, 15))],
        ),
    ]

    @staticmethod
    def _rows(spec):
        """Build stored-style periods from ``(id, index, start, end)`` tuples.

        Args:
            spec: The tuples, in the index order the old loader returned.

        Returns:
            A list of :class:`~app.services.pay_calendar.DerivedPeriod`.
        """
        return [
            DerivedPeriod(
                period_id=pid, period_index=idx, start_date=start,
                end_date=end, end_is_projected=False,
            )
            for pid, idx, start, end in spec
        ]

    @pytest.mark.parametrize(
        "name,spec", STORED_SHAPES, ids=[s[0][:40] for s in STORED_SHAPES],
    )
    def test_the_two_rules_agree_over_shapes_a_calendar_cannot_hold(
        self, name, spec,
    ):
        """A hole, an overlap and a runaway end are all invisible to the clamp.

        **This is the half of the equivalence the suite could not previously
        state**, and its absence was the load-bearing gap: the ruling's
        gapped evidence lived in a session probe nobody could re-run, while the
        docstrings cited it as proof.  A hole is what plan row **P27** is
        about, and it is the reason the arc's four OTHER cutover leaves wait on
        ``balance:X-ad`` and ``C3``.  C2-d is exempt because the clamp asks
        which period most recently OPENED, and a hole changes only which period
        a day is INSIDE.

        Every day from 60 before the first payday to 60 past the last stored
        end, so both fallback branches and the pre-schedule clamp are crossed.
        """
        rows = self._rows(spec)
        day = min(p.start_date for p in rows) - timedelta(days=60)
        end = max(p.end_date for p in rows) + timedelta(days=60)
        while day <= end:
            assert self.clamp(rows, day) == self.chain(rows, day), f"{name} {day}"
            day += timedelta(days=1)

    def test_index_order_disagreeing_with_date_order_is_the_one_divergence(self):
        """The precondition, stated as the counterexample that violates it.

        The two rules are equal on a schedule that is non-overlapping AND
        index-ordered by date.  Both halves matter, and an earlier draft of this
        step's prose named only the first.  Here the stored ordinals run
        BACKWARDS against the dates: the old chain's last resort is
        ``periods[0]`` -- lowest INDEX -- while the clamp takes the earliest
        DATE, so every day below both paydays parts them.

        Unreachable today, and the citation matters more than the reachability:
        ``pay_period_service`` holds the property with the batch guard
        ``_reject_overlapping_batch`` plus a tail-append at ``max_index + 1``,
        and **plan step C3 deletes the first of those**.  That is safe only
        because the chain needing it dies at C2-d -- which is a claim C3 must
        not have to rediscover.
        """
        rows = self._rows([
            (1, 0, date(2026, 3, 1), date(2026, 3, 14)),
            (2, 1, date(2026, 1, 2), date(2026, 1, 15)),
        ])
        below = date(2025, 12, 1)
        # The chain falls to periods[0], the row with the LOWEST index, whose
        # payday is the LATER of the two; the clamp takes the earlier payday.
        assert self.chain(rows, below).period_id == 1
        assert self.clamp(rows, below).period_id == 2
        # And they agree everywhere the two orders happen to coincide -- inside
        # each period, which is the containment branch.
        for inside in (date(2026, 1, 8), date(2026, 3, 8)):
            assert self.chain(rows, inside) == self.clamp(rows, inside)


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


class TestPeriodStartingAfter:
    """The STRICT pair, which plan step C2-f1 moved here with two deleted queries.

    ``pay_period_service.get_next_period`` asked ``period_index + 1`` and
    ``companion_service.get_previous_period`` asked ``period_index - 1`` -- one
    rule written twice, on the stored ordinal.  The assertions below are the
    ones those two carried, re-pointed at the value, plus the property their
    ordinal form could not state: on a DERIVED calendar the ordinal IS payday
    order, so stepping by index and stepping by payday cannot disagree.
    """

    def test_next_of_a_period_is_the_one_after_it(self):
        """``get_next_period(periods[N])`` was ``periods[N + 1]``; it still is."""
        cal = calendar()
        assert cal.period_starting_after(cal.periods[1].start_date) is cal.periods[2]
        assert cal.period_starting_after(cal.periods[0].start_date) is cal.periods[1]

    def test_next_of_the_last_period_is_none(self):
        """Past the last payday the schedule has not reached there yet."""
        cal = calendar()
        assert cal.period_starting_after(cal.periods[-1].start_date) is None

    def test_previous_of_the_first_period_is_none(self):
        """The mirror's end of the schedule, which the companion nav hides."""
        cal = calendar()
        assert cal.period_starting_before(cal.periods[0].start_date) is None
        assert cal.period_starting_before(cal.periods[2].start_date) is cal.periods[1]

    def test_the_two_are_inverses_across_the_whole_schedule(self):
        """Step forward then back and land where you started, at every payday.

        The property the two ordinal queries could not have: they read a
        STORED ``period_index``, so a schedule whose index order disagreed with
        its payday order would step forward into one period and back into a
        different one.  Here the ordinal is derived from payday order, so the
        round trip is closed by construction -- this asserts it rather than
        assuming it.
        """
        cal = calendar()
        for period in cal.periods[:-1]:
            forward = cal.period_starting_after(period.start_date)
            assert forward is not None
            assert cal.period_starting_before(forward.start_date) is period

    def test_strict_is_what_separates_them_from_the_inclusive_pair(self):
        """A period's own payday is excluded -- the off-by-one five callers shared.

        ``period_starting_on_or_after(payday)`` answers that period ITSELF, so
        a caller that reached for the inclusive search to mean "the next one"
        would file a credit-card payback in the very period it pays back.
        """
        cal = calendar()
        payday = cal.periods[1].start_date
        assert cal.period_starting_on_or_after(payday) is cal.periods[1]
        assert cal.period_starting_after(payday) is cal.periods[2]
        assert cal.period_starting_on_or_before(payday) is cal.periods[1]
        assert cal.period_starting_before(payday) is cal.periods[0]

    def test_they_SKIP_an_unsaved_candidate(self):
        """The materialisation filter, which is what makes the FK write safe.

        ``create_cc_payback_transaction`` writes this answer's ``period_id``
        into ``transactions.pay_period_id``, which is ``NOT NULL``, with no
        guard of its own -- because the SEARCH cannot answer a period that has
        none.  A projection is not the risk (these never project); the risk is
        the other way ``period_id`` is ``None``, an unsaved candidate, which
        ``derive_periods`` accepts by design and which plan step C3's writer
        builds.  ``WITH_INTERIOR_UNSAVED`` puts one BETWEEN two saved paydays,
        which is the position a head-or-tail shape cannot test.

        The INCLUSIVE pair is the control: it still answers the candidate, so
        this pins the filter rather than the calendar's contents.
        """
        cal = PayCalendar.from_paydays(WITH_INTERIOR_UNSAVED, 14, user_id=1)
        candidate = cal.periods[1]
        assert candidate.period_id is None

        after = cal.period_starting_after(cal.periods[0].start_date)
        assert after is cal.periods[2]
        assert after.period_id == 12

        before = cal.period_starting_before(cal.periods[2].start_date)
        assert before is cal.periods[0]
        assert before.period_id == 10

        # The control: the inclusive searches are unfiltered and unchanged,
        # which is what the recurrence engine reads.
        assert cal.period_starting_on_or_after(candidate.start_date) is candidate
        assert cal.period_starting_on_or_before(candidate.start_date) is candidate

    def test_no_materialised_period_at_all_answers_none(self):
        """Every payday an unsaved candidate leaves nothing to answer with."""
        cal = PayCalendar.from_paydays(
            [(None, date(2026, 1, 2)), (None, date(2026, 1, 16))], 14, user_id=1,
        )
        assert cal.period_starting_after(date(2026, 1, 2)) is None
        assert cal.period_starting_before(date(2026, 1, 16)) is None

    def test_a_day_before_the_first_payday_answers_the_first_period(self):
        """The forward search's other end, which the deleted query never reached.

        ``get_next_period`` took a PERIOD, so it could not be asked about a day
        below the schedule at all; the calendar can, and the answer is the
        opening paycheck rather than ``None``.
        """
        cal = calendar()
        assert cal.period_starting_after(date(2020, 1, 1)) is cal.periods[0]
        assert cal.period_starting_before(date(2020, 1, 1)) is None

    def test_they_answer_off_cadence_from_a_day_inside_a_period(self):
        """Asked mid-period rather than on a payday, on an irregular schedule.

        OFF_CADENCE on purpose: the searches key on ``start_date``, so a
        schedule whose paydays are one cadence apart cannot distinguish "the
        period after this payday" from "the period one cadence later".
        """
        cal = PayCalendar.from_paydays(OFF_CADENCE, 14, user_id=1)
        mid = cal.periods[1].start_date + timedelta(days=3)
        assert cal.period_starting_after(mid) is cal.periods[2]
        assert cal.period_starting_before(mid) is cal.periods[1]


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


class TestTheWindowTypeEnforcesItsOwnTwoInvariants:
    """Ledger rows **P24** and **P32**, made properties of the type (C2-c).

    The window was a plain frozen tuple until plan step C2-c gave it a
    consumer: the balance seam's per-period entries, whose columns ARE these
    periods.  Two things had to stop being conventions at that point, and they
    are enforced differently ON PURPOSE.

    ORDER is DERIVED -- the constructor sorts, so a caller cannot state it and
    therefore cannot state it wrongly.  CONTIGUITY is CHECKED, because it is a
    property of the input that no constructor can compute its way out of.
    """

    def test_the_periods_are_sorted_at_construction(self):
        """P24: order is not a precondition the caller has to satisfy."""
        cal = calendar()
        backwards = PeriodWindow(periods=tuple(reversed(cal.periods)))
        assert backwards.periods == cal.periods

    def test_the_control_shows_what_an_unsorted_window_would_answer(self):
        """The firing control: containment BISECTS, so disorder is silent.

        Given the periods newest-first, an unsorted bisect misses the day the
        FIRST one covers and answers correctly for a later one by accident --
        a wrong column with no error anywhere.  ``containing_period`` is the
        primitive the window delegates to, driven here on a raw tuple so the
        control is the search rather than the type that now sorts for it.
        """
        cal = calendar()
        unsorted_periods = tuple(reversed(cal.periods))
        assert containing_period(unsorted_periods, date(2026, 1, 2)) is None
        assert PeriodWindow(periods=unsorted_periods).containing(
            date(2026, 1, 2),
        ) is cal.periods[0]

    def test_a_gapped_window_is_refused_with_the_hole_named(self):
        """P32: a column set with a hole renders a balance that does not add up.

        Built by hand, because no calendar VIEW can produce one -- derived
        periods tile, so ``window``, ``overlapping``, ``axis`` and ``saved``
        all return contiguous runs.  That is why the refusal is the negative
        control for a fifth view producer rather than a fence over a live
        state.
        """
        cal = calendar()
        with pytest.raises(PayCalendarError) as excinfo:
            PeriodWindow(periods=(cal.periods[0], cal.periods[2]))
        assert "unbroken span" in str(excinfo.value)
        assert "14 day(s) in no column" in str(excinfo.value)

    def test_an_overlapping_window_is_refused_too(self):
        """The other way two periods can fail to meet, and it double-counts.

        Unconstructible from a calendar for the same reason a hole is, and
        refused by the same rule: adjacency is ``next.start == prev.end + 1``,
        which fails in both directions rather than only upward.
        """
        overlapping = (
            DerivedPeriod(
                period_id=1, period_index=0, start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 20), end_is_projected=False,
            ),
            DerivedPeriod(
                period_id=2, period_index=1, start_date=date(2026, 1, 16),
                end_date=date(2026, 1, 29), end_is_projected=True,
            ),
        )
        with pytest.raises(PayCalendarError, match="unbroken span"):
            PeriodWindow(periods=overlapping)

    def test_the_empty_and_single_period_windows_are_legal(self):
        """Vacuously contiguous, and both are real answers."""
        assert len(PeriodWindow(periods=())) == 0
        assert len(PeriodWindow(periods=(calendar().periods[0],))) == 1

    def test_every_view_that_SLICES_the_calendar_is_contiguous(self):
        """A slice of a tiling tiles, over every shape the model can express.

        The claim three of the four view producers rest on.  It is asserted
        with a COUNTER, because the interesting body is inside a
        ``zip(periods, periods[1:])`` that a one-period window never enters --
        which is exactly how the first draft of this test passed while
        checking nothing on the axis that matters (an adversarial review of
        C2-c found it).
        """
        pairs = 0
        for name, paydays, cadence in SHAPES:
            cal = calendar(paydays, cadence)
            first = cal.opening_bound()
            last = cal.horizon()
            views = [cal.window(0, len(cal.periods)), cal.window(1, 2)]
            if first is not None:
                views.append(cal.overlapping(first, last))
                views.append(cal.axis(first, last + timedelta(days=90)))
            for view in views:
                periods = list(view)
                for earlier, later in zip(periods, periods[1:]):
                    pairs += 1
                    assert later.start_date == (
                        earlier.end_date + timedelta(days=1)
                    ), name
        # The loop is not vacuous: every shape contributed adjacency pairs.
        assert pairs > 50

    def test_saved_FILTERS_and_so_can_leave_a_hole_a_slice_cannot(self):
        """The fourth producer, and the one the refusal is a live guard on.

        ``saved()`` drops the periods no foreign key can point at, and dropping
        an INTERIOR one leaves the days it covered in no column -- a filter
        does not preserve a tiling the way a slice does.  The refusal fires
        rather than the seam reporting over the hole, which is the whole point:
        a balance column with a 14-day gap in it does not add up.

        Unreachable through ``calendar_for`` (it reads saved rows only) and
        through ``pay_period_write`` (its forward-only floor keeps candidates
        after the last saved payday); plan step **C6** makes it reachable.
        """
        cal = calendar(WITH_INTERIOR_UNSAVED)
        assert [period.period_id for period in cal.periods] == [10, None, 12]

        with pytest.raises(PayCalendarError) as excinfo:
            cal.saved()

        assert "unbroken span" in str(excinfo.value)
        assert "14 day(s) in no column" in str(excinfo.value)

    def test_a_candidate_at_the_HEAD_or_TAIL_leaves_no_hole(self):
        """The control: it is the candidate's POSITION that decides, not its
        existence.

        Filtering the first or last period off a tiling leaves a shorter
        tiling, so ``saved()`` answers normally -- which is why the shape at
        the head (``WITH_UNSAVED``) could never have caught the case above.
        """
        assert [
            period.period_id for period in calendar(WITH_UNSAVED).saved()
        ] == [11]
        tail_candidate = [
            (10, date(2026, 1, 2)), (11, date(2026, 1, 16)),
            (None, date(2026, 1, 30)),
        ]
        assert [
            period.period_id for period in calendar(tail_candidate).saved()
        ] == [10, 11]


class TestTheSavedWindowIsTheBalanceSeamsDomain:
    """``PayCalendar.saved`` -- what every per-period seam entry reports over."""

    def test_it_holds_every_saved_period_in_payday_order(self):
        """The whole schedule, which is what all eight callers used to pass."""
        assert [period.period_id for period in calendar().saved()] == [
            10, 11, 12, 13,
        ]

    def test_an_unsaved_candidate_is_left_out(self):
        """The seam's maps are keyed by ``pay_periods.id``.

        A period with no id would key every one of them under ``None`` and
        collapse them onto each other, which is ledger row **P21**'s shape.
        ``derive_periods`` accepts an unsaved candidate by design and
        ``pay_period_write`` builds a calendar out of them on every write, so
        the filter is what keeps that calendar out of a balance map.
        """
        window = calendar(WITH_UNSAVED).saved()
        assert [period.period_id for period in window] == [11]

    def test_an_empty_calendar_answers_an_empty_window(self):
        """A brand-new owner has no columns, which is an answer not an error."""
        assert len(PayCalendar.from_paydays([], None, 1).saved()) == 0


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


class TestTheAxisRefusesARangeItCanOnlyHalfCover:
    """Ledger row **P23**, ruled 2026-08-14 (developer) at plan step C2-e.

    ``axis`` used to answer a range opening below the owner's first payday by
    returning the part above it -- silently, with a summary line ("the spans
    covering ``[first_day, last_day]``") that was false whenever it happened
    and a ``Returns`` block that covered only the wholly-before case.  A
    truncated axis and a complete one are indistinguishable in the result, so
    the refusal is the same argument :meth:`PayCalendar.overlapping` already
    makes for a CROSSED range, applied to the other end.

    Nothing is projected backwards (ruled 2026-08-10: before an owner's first
    payday there is no paycheck), so covering the range was never an option --
    which leaves refusing as the only answer that is not a half-truth.
    """

    def test_a_range_opening_below_the_first_payday_is_refused(self):
        """The state P23 measured: 13 days that would be covered by nothing."""
        cal = calendar()
        with pytest.raises(PayCalendarError, match="opens before user"):
            cal.axis(date(2025, 12, 20), date(2026, 3, 1))

    def test_the_refusal_names_the_bound_and_the_days_it_would_drop(self):
        """The message has to be actionable: which day, and how far short."""
        cal = calendar()
        with pytest.raises(PayCalendarError) as raised:
            cal.axis(date(2025, 12, 20), date(2026, 3, 1))
        assert "2025-12-20" in str(raised.value)
        assert "2026-01-02" in str(raised.value)
        assert "13 day(s)" in str(raised.value)

    def test_opening_exactly_ON_the_first_payday_is_accepted(self):
        """The firing control: the bound is inclusive, so this is not refused.

        Without it the test above passes against a refusal that fires one day
        too early and truncates nothing.
        """
        cal = calendar()
        axis = cal.axis(date(2026, 1, 2), date(2026, 3, 1))
        assert axis[0].start_date == date(2026, 1, 2)

    def test_an_empty_calendar_still_answers_rather_than_refusing(self):
        """No first payday means no PARTIAL coverage to hide.

        An owner with no paydays at all gets an empty window, which is what
        :meth:`PayCalendar.saved` answers them too.  The refusal is about a
        range half-covered, not about a calendar that covers nothing.
        """
        empty = PayCalendar.from_paydays([], 14, user_id=2)
        assert len(empty.axis(date(2020, 1, 1), date(2027, 1, 1))) == 0

    def test_a_crossed_range_is_still_refused_first(self):
        """The two refusals do not shadow each other."""
        cal = calendar()
        with pytest.raises(PayCalendarError, match="ends before it starts"):
            cal.axis(date(2026, 3, 1), date(2026, 2, 1))


class TestTheWindowIsASequence:
    """``PeriodWindow`` indexes and slices, added at plan step C2-e.

    Its consumers -- the seed read at the axis's opening day, the readiness
    chart's downsampled points, the growth engine's own tests -- need the i-th
    period.  Reaching through to :attr:`PeriodWindow.periods` for it is the one
    move that lets a run of periods escape the type guaranteeing their order
    and their tiling, so the type does it.
    """

    def test_an_integer_index_returns_that_period(self):
        cal = calendar()
        window = cal.saved()
        assert window[0] is window.periods[0]
        assert window[-1] is window.periods[-1]

    def test_the_index_is_the_WINDOWS_ordinal_not_the_calendars(self):
        """A window is a VIEW: ``[0]`` is where it starts, not the schedule."""
        window = calendar().window(first_index=2, count=2)
        assert window[0].period_index == 2

    def test_an_index_past_the_end_raises(self):
        with pytest.raises(IndexError):
            calendar().saved()[99]  # pylint: disable=expression-not-assigned

    def test_SLICING_is_refused_outright(self):
        """No consumer in ``app/`` slices a window, and one slice lied.

        A first cut returned a :class:`PeriodWindow` for a slice and let
        ``__post_init__`` refuse the stepped ones.  Two adversarial reviews of
        plan step C2-e landed on it: ``[::2]`` was refused as intended, but
        ``[::-1]`` TILES, so it passed the contiguity check and came back
        silently re-sorted into payday order -- a wrong answer to "walk this
        backwards", given without a word.
        """
        window = calendar().saved()
        for attempt in (slice(None, 2), slice(None, None, 2), slice(None, None, -1)):
            with pytest.raises(TypeError, match="cannot be sliced"):
                window[attempt]  # pylint: disable=expression-not-assigned

    def test_reversed_walks_it_backwards(self):
        """What a caller wanting the whole window in reverse writes instead.

        Free from :meth:`__getitem__` plus :meth:`__len__`, and the reason the
        slice branch has nothing left to serve.
        """
        window = calendar().saved()
        assert [period.period_id for period in reversed(window)] == [
            13, 12, 11, 10,
        ]


class TestTheClampedProjectionAxis:
    """``projection_axis``: ``axis`` with ONE clamp, ruled 2026-08-14.

    The pairing :meth:`PayCalendar.filing_period` already makes against
    :meth:`PayCalendar.period_starting_on_or_before` -- the strict search
    refuses or answers, and the TOTAL companion beside it states its clamp in
    the open.  Every projecting surface calls the companion, which is what lets
    the strict one refuse at all (ledger row **P23**).
    """

    def test_a_range_opening_below_the_first_payday_is_RAISED_not_refused(self):
        """The owner whose first payday has not happened yet.

        An ordinary state: the Generate form asks for "your next (or first)
        payday", so a read pass whose clock precedes the whole schedule is what
        a new owner looks like on the day they set it up.
        """
        cal = calendar()
        axis = cal.projection_axis(date(2025, 12, 20), date(2026, 3, 1))
        assert axis[0].start_date == date(2026, 1, 2)
        # The firing control: the strict sibling refuses that same range, so
        # this cannot pass by the clamp having been dropped.
        with pytest.raises(PayCalendarError, match="opens before user"):
            cal.axis(date(2025, 12, 20), date(2026, 3, 1))

    def test_a_range_already_inside_the_schedule_is_untouched(self):
        """The clamp raises nothing it does not have to."""
        cal = calendar()
        assert list(cal.projection_axis(date(2026, 1, 16), date(2026, 3, 1))) == (
            list(cal.axis(date(2026, 1, 16), date(2026, 3, 1)))
        )

    def test_a_CROSSED_range_is_refused_rather_than_emptied(self):
        """A caller with its bounds the wrong way round is a defect.

        The distinction the clamp must not swallow: a range the CLAMP empties
        is a real answer (the /retirement lever page's ``past_horizon``), while
        a crossed one is a bug.  Folding them together is the hole
        :meth:`PayCalendar.overlapping` refuses to leave open one level down,
        and an adversarial code review of this step caught a first cut doing
        exactly that.
        """
        cal = calendar()
        with pytest.raises(PayCalendarError, match="ends before it starts"):
            cal.projection_axis(date(2026, 3, 1), date(2026, 2, 1))

    def test_a_last_day_behind_the_first_payday_is_EMPTY(self):
        """The clamped-empty case, which is an answer and not a refusal."""
        cal = calendar()
        assert len(cal.projection_axis(
            date(2025, 11, 1), date(2025, 12, 1),
        )) == 0

    def test_an_empty_calendar_yields_an_empty_axis(self):
        """Nothing to project FROM, so nothing is invented -- and no refusal."""
        empty = PayCalendar.from_paydays([], 14, user_id=2)
        assert len(empty.projection_axis(
            date(2026, 1, 1), date(2027, 1, 1),
        )) == 0


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
