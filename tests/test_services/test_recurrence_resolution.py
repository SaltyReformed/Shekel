"""The pure recurrence resolver (plan steps R2c-1 and R2d).

``app.services.recurrence.resolve`` is the one place the closed ``pattern_id``
vocabulary becomes the two-axis one, and it is pure: a spec and a
:class:`~app.services.recurrence.PeriodCalendar` in, a
:class:`~app.services.recurrence.ResolvedRecurrence` out.  So every case below
is exercised at EXACT dates against a hand-built schedule -- no database, no
clock -- and each assertion carries the arithmetic that produces it.

**Nothing here is stored**, which is why this file carries the derivation's
whole burden of proof.  Plan step R2d settled that the two-axis values are
computed on demand rather than persisted beside the columns they are derived
from, so there is no backfill and no column shape to assert against: if the
derivation is wrong, only a test like these can say so.  The migration test
file that used to duplicate this coverage against a frozen copy of the same
derivation is gone with the copy.

The schedule these tests resolve against is the developer's own shape: first
payday 2026-03-26, 14-day cadence.  That is deliberate rather than arbitrary --
the live derivation was measured on it while building R2b, so an assertion here
can be checked against a real answer.

What the classes cover, in the order the plan reasons about them:

* one class per anchor FAMILY (pay-period space, calendar, first-of-month),
  because the families are different derivations rather than seven pattern
  branches, plus :class:`TestTheRetiredOncePattern` for the eighth ``ref``
  row that no longer HAS a family;
* :class:`TestMonthEndClamping`, the ``recurrence_month_anchors``
  discriminator (ruling R-R3): present exactly when the anchor month was too
  short to hold the day the user meant;
* :class:`TestTotality`, the case that motivated the ruling that
  ``anchor_date`` is the bound rather than a period boundary -- a rule whose
  bound falls past the materialised horizon still resolves;
* :class:`TestRefusals`, the three broken invariants the resolver refuses
  rather than papering over.
"""

from datetime import date, timedelta

import pytest

from app import ref_cache
from app.enums import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrencePatternEnum,
    RecurrenceUnitEnum,
)
from app.extensions import db
from app.models.ref import RecurrencePattern
from app.services.recurrence import (
    PeriodCalendar,
    RecurrenceResolutionError,
    RecurrenceSpec,
    SchedulePeriod,
    resolve,
)

#: The developer's own schedule shape, so an assertion here can be checked
#: against the answer measured on live data while building R2b.
_FIRST_PAYDAY = date(2026, 3, 26)
_CADENCE_DAYS = 14
_PERIOD_COUNT = 61


#: The owner every spec and calendar in this file names.  One constant rather
#: than a literal per call site, because :func:`resolve` REFUSES a spec paired
#: with another user's schedule -- so a mismatch here would read as a
#: derivation failure rather than as the typo it is.
_USER_ID = 1

#: The ``ref.recurrence_patterns`` row plan step R2e-3 stopped naming.  Its
#: enum member is gone; the row survives to R9 (ruling R-R11), so the name is
#: spelled here rather than read off a member that no longer exists.
_RETIRED_PATTERN_NAME = "Once"


def build_calendar(
    first_payday: date = _FIRST_PAYDAY,
    cadence_days: int = _CADENCE_DAYS,
    count: int = _PERIOD_COUNT,
    user_id: int = _USER_ID,
) -> PeriodCalendar:
    """Return a contiguous calendar, built the way the app builds one.

    Mirrors ``pay_period_service.generate_pay_periods``: each period ends
    ``cadence_days - 1`` after it starts and the next opens the following day.
    ``period_id`` is ``index + 1`` so a test can name a start period by the
    same number it names its index by.

    Args:
        first_payday: The first period's ``start_date``.
        cadence_days: Days between paydays.
        count: How many periods to build.
        user_id: The owner the calendar declares, which ``resolve`` checks
            against the spec's.

    Returns:
        The :class:`~app.services.recurrence.PeriodCalendar`.
    """
    return PeriodCalendar(user_id=user_id, periods=tuple(
        SchedulePeriod(
            period_id=index + 1,
            period_index=index,
            start_date=first_payday + timedelta(days=cadence_days * index),
            end_date=first_payday + timedelta(
                days=cadence_days * index + cadence_days - 1,
            ),
        )
        for index in range(count)
    ))


def spec_for(pattern: RecurrencePatternEnum, **overrides) -> RecurrenceSpec:
    """Return a spec naming *pattern*, with *overrides* applied.

    Args:
        pattern: The pattern member to resolve the id of.
        **overrides: Any :class:`~app.services.recurrence.RecurrenceSpec`
            field to set.

    Returns:
        The spec.
    """
    return RecurrenceSpec(
        user_id=_USER_ID,
        pattern_id=ref_cache.recurrence_pattern_id(pattern),
        **overrides,
    )


@pytest.mark.usefixtures("app")
class TestPayPeriodSpaceFamily:
    """Every Period / Every N Periods -- the paycheck-rhythm anchor."""

    def test_every_period_anchors_on_the_schedule_opening(self):
        """With no bounds, the anchor is the schedule's first payday.

        ``resolve_generation_plan`` defaults ``effective_from`` to
        ``periods[0].start_date`` for a rule naming no start period, so the
        first occurrence is that day: 2026-03-26.
        """
        resolved = resolve(
            spec_for(RecurrencePatternEnum.EVERY_PERIOD), build_calendar(),
        )

        assert resolved.anchor_date == date(2026, 3, 26)
        assert resolved.interval_n == 1
        assert resolved.unit is RecurrenceUnitEnum.PERIOD
        assert resolved.placement is PeriodPlacementEnum.CONTAINING_DATE
        assert resolved.shift is BusinessDayShiftEnum.NONE
        assert resolved.nominal_day is None

    def test_a_start_period_moves_the_anchor_to_its_start(self):
        """The chosen first paycheck becomes the opening bound.

        Period index 3 opens 2026-03-26 + 3 x 14 days = 2026-05-07, which
        dominates the schedule opening in the effective-start maximum.
        """
        resolved = resolve(
            spec_for(RecurrencePatternEnum.EVERY_PERIOD, start_period_id=4),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2026, 5, 7)

    def test_a_mid_period_start_date_anchors_on_the_date_not_the_period(self):
        """``anchor_date`` holds the bound itself, not the period boundary.

        The developer's ruling: an occurrence is a DATE and ``placement_id``
        is what carries it onto a period.  2026-04-22 falls inside the period
        2026-04-09..2026-04-22, and CONTAINING_DATE placement resolves both
        readings to that same period -- so the generated rows are unchanged
        while the stored value stays the fact the user stated.
        """
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.EVERY_PERIOD,
                start_date=date(2026, 4, 22),
            ),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2026, 4, 22)

    def test_every_n_periods_phases_on_the_chosen_start_period(self):
        """``offset_periods`` is DERIVED from the start period, every write.

        Period index 5 with an interval of 3 phases the rule at
        ``5 % 3 == 2``, and the interval the form submitted is kept because
        this is the one pattern whose interval is authored rather than named.
        """
        spec = spec_for(
            RecurrencePatternEnum.EVERY_N_PERIODS,
            interval_n=3, start_period_id=6,
        )
        calendar = build_calendar()

        resolved = resolve(spec, calendar)

        assert resolved.interval_n == 3
        assert resolved.offset_periods == 2
        assert resolved.anchor_date == date(2026, 6, 4)

    def test_every_n_periods_without_a_start_period_keeps_the_authored_phase(self):
        """With no start period there is nothing to derive the phase from.

        The authored value is then the only statement of phase available, so
        it rides through -- which is also what lets the R1 characterization
        oracle sweep every phase of every interval.  The ANCHOR moves with it
        (see :class:`TestTheTwoVocabulariesAgree`); a phase the anchor did not
        carry would be a rule whose two halves state different cadences.
        """
        spec = spec_for(
            RecurrencePatternEnum.EVERY_N_PERIODS,
            interval_n=4, offset_periods=3,
        )
        calendar = build_calendar()

        resolved = resolve(spec, calendar)

        assert resolved.interval_n == 4
        assert resolved.offset_periods == 3
        # Period index 3 opens 2026-03-26 + 3 x 14 days.
        assert resolved.anchor_date == date(2026, 5, 7)

    def test_a_non_every_n_pattern_never_carries_a_phase(self):
        """Only ``Every N Periods`` reads ``offset_periods``, so only it sets one.

        A submitted phase on any other pattern is discarded rather than
        stored, which is what makes the column meaningless-by-construction
        for them instead of meaningless-by-convention.
        """
        spec = spec_for(
            RecurrencePatternEnum.MONTHLY, offset_periods=3, day_of_month=15,
        )

        assert resolve(spec, build_calendar()).offset_periods == 0


@pytest.mark.usefixtures("app")
class TestCalendarFamily:
    """Monthly / Quarterly / Semi-Annual / Annual -- the calendar anchor."""

    def test_monthly_anchors_on_the_first_matching_day(self):
        """First day-15 on or after the 2026-03-26 opening is 2026-04-15."""
        resolved = resolve(
            spec_for(RecurrencePatternEnum.MONTHLY, day_of_month=15),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2026, 4, 15)
        assert resolved.interval_n == 1
        assert resolved.unit is RecurrenceUnitEnum.MONTH

    def test_a_start_date_past_the_months_day_skips_to_the_next_month(self):
        """A bound of 2026-04-20 on a day-15 rule anchors 2026-05-15.

        The occurrence-bounded reading (ruling R-R6, ledger row D5): the
        engine bounds PERIODS, so it still generates a row dated 2026-04-15
        for this rule -- ten days before the window the user stated.  The
        anchor is the first occurrence at or after the bound, so that row is
        not one of this rule's occurrences at all.
        """
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.MONTHLY,
                day_of_month=15, start_date=date(2026, 4, 20),
            ),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2026, 5, 15)

    def test_quarterly_anchors_in_its_own_residue_class(self):
        """A February quarterly fires Feb / May / Aug / Nov; first is 2026-05-10.

        February is month 2, so the cycle's months are ``2, 5, 8, 11``.  The
        opening bound 2026-03-26 falls after February's, so the first
        occurrence is May's.
        """
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.QUARTERLY,
                month_of_year=2, day_of_month=10,
            ),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2026, 5, 10)
        assert resolved.interval_n == 3
        assert resolved.unit is RecurrenceUnitEnum.MONTH

    def test_semi_annual_carries_the_six_month_interval(self):
        """A January semi-annual fires Jan / Jul; first after March is 2026-07-20."""
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.SEMI_ANNUAL,
                month_of_year=1, day_of_month=20,
            ),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2026, 7, 20)
        assert resolved.interval_n == 6

    def test_annual_rolls_to_next_year_when_its_day_has_passed(self):
        """March 15 has passed the 2026-03-26 bound, so the anchor is 2027-03-15."""
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.ANNUAL,
                month_of_year=3, day_of_month=15,
            ),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2027, 3, 15)
        assert resolved.interval_n == 1
        assert resolved.unit is RecurrenceUnitEnum.YEAR

    def test_a_rule_naming_no_day_or_month_takes_the_engines_own_default(self):
        """``or 1`` is mirrored, not re-invented.

        ``match_periods`` coerces a malformed calendar rule with
        ``rule.month_of_year or 1`` / ``rule.day_of_month or 1``
        (``recurrence_engine.py:504-518``), so an annual rule carrying neither
        fires on 1 January.  The resolver reproduces that rather than choosing
        a different fiction -- the honest fix is R7 refusing the rule at the
        door, not two different silent defaults.
        """
        resolved = resolve(
            spec_for(RecurrencePatternEnum.ANNUAL), build_calendar(),
        )

        assert resolved.anchor_date == date(2027, 1, 1)


@pytest.mark.usefixtures("app")
class TestFirstOfMonthFamily:
    """Monthly First -- "each month's first paycheck"."""

    def test_it_anchors_on_the_effective_month_when_that_month_qualifies(self):
        """March's first paycheck IS the bound, so March can honour the rule.

        The schedule opens 2026-03-26 and that is also March's earliest
        payday, so the effective month qualifies and the anchor is 2026-03-01.
        This is the value the live Monthly First rule carries.
        """
        resolved = resolve(
            spec_for(RecurrencePatternEnum.MONTHLY_FIRST), build_calendar(),
        )

        assert resolved.anchor_date == date(2026, 3, 1)
        assert resolved.placement is PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER

    def test_a_month_with_no_paycheck_cannot_honour_the_rule(self):
        """A cadence longer than a month leaves months with no first paycheck.

        ``cadence_days`` is user-selectable 1..365
        (``ck_pay_schedule_cadence_range``).  At 90 days with paydays
        2026-06-01 / 08-30 / 11-28, a bound of 2026-09-05 has no September or
        October paycheck to anchor on, so the first month that can honour the
        rule is November.

        An earlier one-step form of this derivation answered 2026-10-01 -- a
        month with no paycheck at all -- because it assumed the month AFTER
        the bound always qualifies.  Three occurrences would then place into
        the single 2026-11-28 period and collide on
        ``idx_transactions_template_period_scenario``.
        """
        calendar = build_calendar(
            first_payday=date(2026, 6, 1), cadence_days=90, count=8,
        )

        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.MONTHLY_FIRST,
                start_date=date(2026, 9, 5),
            ),
            calendar,
        )

        assert resolved.anchor_date == date(2026, 11, 1)

    def test_a_mid_month_bound_at_a_long_cadence_skips_to_the_paying_month(self):
        """Second measured case of the same defect.

        Bound 2026-06-15: June's own first paycheck (06-01) precedes it, and
        July has none, so the answer is August -- not the July the one-step
        form returned.
        """
        calendar = build_calendar(
            first_payday=date(2026, 6, 1), cadence_days=90, count=8,
        )

        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.MONTHLY_FIRST,
                start_date=date(2026, 6, 15),
            ),
            calendar,
        )

        assert resolved.anchor_date == date(2026, 8, 1)

    def test_it_skips_a_month_whose_first_paycheck_precedes_the_bound(self):
        """Starting at April's SECOND paycheck anchors in May, not April.

        April's paydays are 04-09 (index 1) and 04-23 (index 2).  Choosing
        index 2 as the first paycheck means April's own first paycheck,
        04-09, precedes the bound -- so April cannot honour a rule that fires
        on each month's FIRST paycheck, and the anchor is 2026-05-01.
        Placement then lands the first row on 2026-05-07, May's first payday.
        Today's engine puts that row on 04-23, the paycheck the rule's own
        name says it should not use (ruling R-R6).
        """
        resolved = resolve(
            spec_for(RecurrencePatternEnum.MONTHLY_FIRST, start_period_id=3),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2026, 5, 1)

    def test_it_keeps_the_month_when_the_chosen_paycheck_is_that_months_first(self):
        """Index 1 IS April's first paycheck, so April qualifies."""
        resolved = resolve(
            spec_for(RecurrencePatternEnum.MONTHLY_FIRST, start_period_id=2),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2026, 4, 1)

    def test_a_december_bound_rolls_the_month_into_the_next_year(self):
        """The month-after fallback crosses the year boundary correctly.

        A bound of 2026-12-20 falls after December's first payday, so the
        first month that can honour the rule is January 2027.
        """
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.MONTHLY_FIRST,
                start_date=date(2026, 12, 20),
            ),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2027, 1, 1)


@pytest.mark.usefixtures("app")
class TestTheRetiredOncePattern:
    """The surviving ``Once`` ``ref`` row resolves to nothing (plan step R2e-3).

    ``Once`` used to be a member here, resolving to EXACTLY the every-period
    value -- so a consumer holding only a
    :class:`~app.services.recurrence.ResolvedRecurrence` could not tell "does
    not recur" from "every paycheck", and plan step R7c's downgrade could not
    have mapped ``(1, period, containing_date)`` back to one pattern.  Ruling
    R-R4 retired it rather than keep the ambiguity.

    The ``ref`` ROW deliberately survives to plan step R9 (ruling R-R11):
    ``ref_cache.init`` raises for an enum member with no row, so deleting it in
    the release that deleted the member would leave the deploy's auto-rollback
    image unable to boot.  What must be true is that the survivor is
    UNREACHABLE, and this is the last line of that: a rule that names it --
    from hand-edited data, or a row the migration missed -- fails loudly at the
    resolver rather than being read as an every-paycheck cadence.
    """

    def test_the_surviving_once_row_is_refused_by_the_resolver(self, app):
        """A rule naming the retired row raises rather than resolving.

        The id is looked up from the live ``ref`` table rather than
        hard-coded: on a migration-built database ``a3b1c2d4e5f6`` appends two
        rows after the initial seed, so the ids are not in enum order and a
        literal 8 would test the wrong row.
        """
        with app.app_context():
            once_row = (
                db.session.query(RecurrencePattern)
                .filter_by(name=_RETIRED_PATTERN_NAME)
                .one()
            )
            spec = RecurrenceSpec(user_id=_USER_ID, pattern_id=once_row.id)

            with pytest.raises(
                RecurrenceResolutionError, match=str(once_row.id),
            ):
                resolve(spec, build_calendar())

    def test_no_enum_member_names_the_retired_row(self, app):
        """The row exists AND no member names it -- both halves, together.

        Either half alone passes for the wrong reason: without the first, a
        deleted row would satisfy "no member names it" while breaking the
        rollback image; without the second, re-adding the member would satisfy
        "the row exists" while re-introducing the ambiguity.
        """
        with app.app_context():
            assert (
                db.session.query(RecurrencePattern)
                .filter_by(name=_RETIRED_PATTERN_NAME)
                .count()
            ) == 1
            assert _RETIRED_PATTERN_NAME not in {
                member.value for member in RecurrencePatternEnum
            }


@pytest.mark.usefixtures("app")
class TestMonthEndClamping:
    """The ``recurrence_month_anchors`` discriminator (ruling R-R3)."""

    def test_a_day_31_rule_anchored_in_a_short_month_records_its_nominal_day(self):
        """April cannot hold the 31st, so the nominal day is carried separately.

        Anchoring at 2026-04-30 and reading the day back off the anchor would
        fire the rule on the 30th forever; the subtype row is what keeps "the
        last day of every month" meaning that.
        """
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.MONTHLY,
                day_of_month=31, start_date=date(2026, 4, 1),
            ),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2026, 4, 30)
        assert resolved.nominal_day == 31

    def test_a_day_30_rule_anchored_in_april_needs_no_row(self):
        """April's 30th IS the 30th -- nothing was clamped, so nothing is stored.

        Presence is the discriminator: a row exists only where the clamp lost
        information.  Landing on a month's last day is not enough on its own.
        """
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.MONTHLY,
                day_of_month=30, start_date=date(2026, 4, 1),
            ),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2026, 4, 30)
        assert resolved.nominal_day is None

    def test_a_day_29_annual_rule_in_a_common_february_records_its_day(self):
        """February 2027 has 28 days, so a Feb-29 annual rule clamps to the 28th."""
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.ANNUAL,
                month_of_year=2, day_of_month=29,
            ),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2027, 2, 28)
        assert resolved.nominal_day == 29

    def test_a_day_15_rule_never_records_a_nominal_day(self):
        """No month is short enough to clamp a day in 1-28, so it costs nothing."""
        resolved = resolve(
            spec_for(RecurrencePatternEnum.MONTHLY, day_of_month=15),
            build_calendar(),
        )

        assert resolved.nominal_day is None

    def test_a_pay_period_rule_never_records_a_nominal_day(self):
        """The clamp is a calendar-unit concern; period rules fire on no day.

        Guards the ordering inside the discriminator: a period-unit rule
        carries no day of the month, so it cannot have had one clamped.
        """
        resolved = resolve(
            spec_for(RecurrencePatternEnum.EVERY_PERIOD, day_of_month=31),
            build_calendar(),
        )

        assert resolved.nominal_day is None


@pytest.mark.usefixtures("app")
class TestTotality:
    """The resolver answers for every rule the application can build."""

    def test_a_bound_past_the_horizon_still_resolves(self):
        """A loan originating past the last materialised period is resolvable.

        The case that decided the anchor's definition.  Plan step R2b anchored
        a pay-period rule on "the first period ending on or after the bound",
        which does not exist here -- the schedule ends 2028-07-26 and the
        bound is 2030-01-15 -- so that derivation returned nothing at all,
        which a NOT NULL column could not have held.  Reachable today:
        ``loan_recurrence_sync._sync_loan_cadence`` stamps ``start_date`` onto
        ANY rule, including a day-less every-paycheck one.
        """
        calendar = build_calendar()
        assert calendar.periods[-1].end_date == date(2028, 7, 26)

        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.EVERY_PERIOD,
                start_date=date(2030, 1, 15),
            ),
            calendar,
        )

        assert resolved.anchor_date == date(2030, 1, 15)

    def test_a_calendar_rule_past_the_horizon_still_resolves(self):
        """Same for the calendar family, which walks months rather than periods."""
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.MONTHLY,
                day_of_month=22, start_date=date(2030, 1, 15),
            ),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2030, 1, 22)

    def test_a_first_of_month_rule_past_the_horizon_still_resolves(self):
        """A month with no materialised payday cannot honour the rule, so the
        anchor moves to the next month rather than to nothing.

        Scanning materialised months instead would answer with whatever month
        the horizon happens to reach, which is a fact about the horizon rather
        than about the rule.
        """
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.MONTHLY_FIRST,
                start_date=date(2030, 1, 15),
            ),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2030, 2, 1)

    def test_every_pattern_resolves_with_no_parameters_at_all(self):
        """No pattern in the closed set can fail to produce a complete tuple.

        The property plan step R7c's NOT NULL columns will rest on, asserted
        over the whole set rather than sampled: whatever the eight patterns
        are, each resolves to a COMPLETE two-axis value.  A pattern that
        resolved to a partial one would become an un-migratable row at R7c.
        """
        calendar = build_calendar()

        for pattern in RecurrencePatternEnum:
            resolved = resolve(spec_for(pattern), calendar)

            assert resolved.anchor_date is not None, pattern
            assert isinstance(resolved.unit, RecurrenceUnitEnum), pattern
            assert isinstance(resolved.placement, PeriodPlacementEnum), pattern
            assert isinstance(resolved.shift, BusinessDayShiftEnum), pattern
            assert resolved.interval_n >= 1, pattern


#: The R1 oracle's own space of ``Every N Periods`` phases: every interval
#: 1..8 crossed with every phase that interval can actually hold.  A phase is
#: only meaningful modulo the interval, so ``(interval=2, offset=5)`` names no
#: rule that can exist -- enumerating the 36 legal pairs is what keeps those
#: 28 impossible ones from being generated and then skipped.
_LEGAL_PHASES = [
    (interval_n, offset_periods)
    for interval_n in range(1, 9)
    for offset_periods in range(interval_n)
]


def _phase_id(value: int) -> str:
    """Name a parametrized phase component so test ids read as numbers."""
    return str(value)


@pytest.mark.usefixtures("app")
class TestTheTwoVocabulariesAgree:
    """The invariant a self-consistency check cannot see.

    Re-resolving a rule and comparing to itself proves IDEMPOTENCE.  It does
    not prove that the closed-set half and the two-axis half describe the SAME
    cadence -- and a neutral review found two shapes where they did not.  The
    agreement condition for the pay-period family is exact and needs no
    engine: the period containing ``anchor_date`` must be one the old matcher
    fires on, i.e. ``(period_index - offset_periods) % interval_n == 0``.
    """

    @staticmethod
    def assert_anchor_is_in_phase(
        spec: RecurrenceSpec, calendar: PeriodCalendar,
    ) -> None:
        """Assert the anchor's own period is one the old engine fires on.

        Takes the SPEC rather than a resolved value so the helper resolves it
        itself: the invariant is about the pairing of the anchor with the
        phase the ROW stores, and both come out of the one call.
        """
        resolved = resolve(spec, calendar)
        offset_periods = resolved.offset_periods
        containing = [
            period for period in calendar.periods
            if period.start_date <= resolved.anchor_date <= period.end_date
        ]
        assert containing, (
            f"anchor {resolved.anchor_date} falls in no period, so the "
            f"phase cannot be checked"
        )
        index = containing[0].period_index
        assert (index - offset_periods) % resolved.interval_n == 0, (
            f"anchor {resolved.anchor_date} lies in period index {index}, "
            f"which the old matcher does NOT fire on "
            f"(offset={offset_periods}, interval={resolved.interval_n})"
        )

    def test_a_submitted_phase_with_no_start_period_moves_the_anchor(self):
        """A phase the anchor did not carry made the halves state 3 vs 3.

        Measured: an every-3-paychecks rule phased at 2 stored
        ``offset_periods = 2`` -- the old engine fires period indices 2, 5, 8
        -- beside an anchor in period index 0, whose two-axis reading fires 0,
        3, 6.  Plan step R4 would have picked the second silently.  Period
        index 2 opens 2026-03-26 + 2 x 14 days.
        """
        calendar = build_calendar()
        spec = spec_for(
            RecurrencePatternEnum.EVERY_N_PERIODS,
            interval_n=3, offset_periods=2,
        )

        assert resolve(spec, calendar).anchor_date == date(2026, 4, 23)
        self.assert_anchor_is_in_phase(spec, calendar)

    def test_a_start_date_past_the_start_period_keeps_the_phase(self):
        """The second measured shape: a bound that outruns the start period.

        ``loan_recurrence_sync._sync_loan_cadence`` stamps ``start_date`` onto
        ANY rule, so "every 5 paychecks into my mortgage" reaches this.  With
        a bound of 2026-09-15 the old engine fires the first period index that
        is both at or after the bound AND a multiple of 5 -- index 15, opening
        2026-03-26 + 15 x 14 days = 2026-10-22.  Anchoring on the bound put it
        in index 12 instead: six weeks of cash-timing divergence, chosen at
        R4's cutover with nothing in the row to detect it.
        """
        calendar = build_calendar()
        spec = spec_for(
            RecurrencePatternEnum.EVERY_N_PERIODS,
            interval_n=5, start_date=date(2026, 9, 15),
        )

        assert resolve(spec, calendar).offset_periods == 0
        assert resolve(spec, calendar).anchor_date == date(2026, 10, 22)
        self.assert_anchor_is_in_phase(spec, calendar)

    @pytest.mark.parametrize(
        ("interval_n", "offset_periods"), _LEGAL_PHASES, ids=_phase_id,
    )
    def test_the_anchor_is_in_phase_for_every_interval_and_offset(
        self, interval_n, offset_periods,
    ):
        """Swept, not sampled: the R1 oracle's own space of phases.

        The R1 oracle sweeps every interval 1..8 crossed with every legal
        phase, so every one of those shapes must resolve to an anchor the old
        matcher agrees with -- that agreement is what makes plan step R4's
        cutover a no-op rather than a silent re-phasing.

        The parametrization enumerates only the 36 LEGAL pairs (see
        :data:`_LEGAL_PHASES`); it used to cross 8 x 8 and ``pytest.skip`` the
        28 impossible ones at runtime, which reported as 28 skips a reader
        could not tell apart from tests someone had disabled.
        """
        self.assert_anchor_is_in_phase(
            spec_for(
                RecurrencePatternEnum.EVERY_N_PERIODS,
                interval_n=interval_n, offset_periods=offset_periods,
            ),
            build_calendar(),
        )

    def test_every_period_still_anchors_on_the_bound_itself(self):
        """The phase-carrying anchor is scoped to the pattern that needs one.

        ``Every Period`` fires on every paycheck, so its phase is trivially
        satisfied and the anchor stays the bound (ruling R-R8) rather than
        advancing to a boundary.
        """
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.EVERY_PERIOD,
                start_date=date(2026, 4, 22),
            ),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2026, 4, 22)

    def test_a_phased_rule_past_the_horizon_still_resolves(self):
        """Totality is not sacrificed to carry the phase.

        No materialised period satisfies a bound past 2028-07-26, so the
        anchor falls back to the bound.  Nothing generates there either way
        -- there are no periods to fire in -- and the value stays derivable,
        which is what plan step R7c's NOT NULL columns will require.
        """
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.EVERY_N_PERIODS,
                interval_n=3, start_date=date(2030, 1, 15),
            ),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2030, 1, 15)


@pytest.mark.usefixtures("app")
class TestAStatedDayOrMonthMustBeInItsColumnsDomain:
    """NULL states nothing and defaults; 0 states something impossible.

    **Plan step R4a changed this, and the change is a behaviour decision.**
    Plan step R2c-1 mirrored the reverse matcher's ``rule.day_of_month or 1``
    exactly, so 0 and NULL both resolved to 1 -- deliberately, because the
    preview endpoint reads the value straight from ``request.args`` and a
    ``<input type="number" min="1">`` does not stop a user typing 0, and
    ``is not None`` would have let the 0 reach ``date(y, m, 0)`` as a 500.

    Two things then changed under it.  ``ck_recurrence_rules_dom`` /
    ``ck_recurrence_rules_moy`` bound the columns to ``NULL OR 1..31`` /
    ``NULL OR 1..12``, and ``_author`` writes the AUTHORED value verbatim --
    so a spec carrying 0 was an unhandled ``IntegrityError`` at the flush, the
    exact failure the R4a door says it closes.  And the preview endpoint now
    catches ``RecurrenceResolutionError``, so a refusal there is a muted line
    rather than the 500 the coercion existed to avoid.

    So the door refuses a STATED 0 and the reader still defaults a NULL.  The
    two concerns live in one place each, instead of one ``or`` doing both and
    disagreeing with the column at zero.
    """

    def test_a_null_day_still_defaults_to_the_first(self):
        """NULL states no day, which the matcher has always read as the 1st."""
        resolved = resolve(
            spec_for(RecurrencePatternEnum.MONTHLY, day_of_month=None),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2026, 4, 1)

    def test_a_null_month_still_defaults_to_january(self):
        """NULL states no cycle month, which reads as January."""
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.ANNUAL,
                month_of_year=None,
                day_of_month=15,
            ),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2027, 1, 15)

    @pytest.mark.parametrize("day", [0, -1, 32, 99])
    def test_a_stated_day_outside_1_31_is_refused(self, day):
        """``ck_recurrence_rules_dom`` is the domain; the door mirrors it."""
        with pytest.raises(RecurrenceResolutionError, match="day_of_month"):
            resolve(
                spec_for(RecurrencePatternEnum.MONTHLY, day_of_month=day),
                build_calendar(),
            )

    @pytest.mark.parametrize("month", [0, -1, 13, 99])
    def test_a_stated_month_outside_1_12_is_refused(self, month):
        """``ck_recurrence_rules_moy`` is the domain; the door mirrors it.

        A 0 month was the silent one: residue ``(0 - 1) % 12`` is 11, so the
        anchor landed in DECEMBER -- eleven months of projected spend in the
        wrong year, with no error anywhere.
        """
        with pytest.raises(RecurrenceResolutionError, match="month_of_year"):
            resolve(
                spec_for(
                    RecurrencePatternEnum.ANNUAL,
                    month_of_year=month,
                    day_of_month=15,
                ),
                build_calendar(),
            )


@pytest.mark.usefixtures("app")
class TestRefusals:
    """Four broken invariants, refused loudly rather than papered over."""

    def test_an_unknown_pattern_id_is_refused(self):
        """A pattern this application does not model has no derivable cadence."""
        spec = RecurrenceSpec(user_id=_USER_ID, pattern_id=999_999)

        with pytest.raises(RecurrenceResolutionError, match="999999"):
            resolve(spec, build_calendar())

    def test_another_users_schedule_is_refused(self):
        """A first occurrence is measured against the OWNER's schedule.

        Pairing a rule with somebody else's calendar produces a plausible
        WRONG date rather than an error -- the derivation runs happily, it
        just answers for the wrong paydays.  Nothing in the application does
        that today, but three call sites derive the calendar's owner from a
        different object than the rule's (``calendar_for(account.user_id)``,
        ``calendar_for(first_period.user_id)``, ``calendar_for(rule.user_id)``),
        so the pairing was an assumption until it was checked.
        """
        spec = spec_for(RecurrencePatternEnum.EVERY_PERIOD)
        other_calendar = build_calendar(user_id=_USER_ID + 1)

        with pytest.raises(RecurrenceResolutionError, match="cannot be resolved"):
            resolve(spec, other_calendar)

    @pytest.mark.parametrize("pattern", list(RecurrencePatternEnum))
    def test_a_non_positive_interval_is_refused_for_every_pattern(
        self, pattern,
    ):
        """Mirrors ``ck_recurrence_rules_positive_interval``, at the door.

        Swept over the whole enum, and the calendar patterns are the ones
        that matter.  The check used to read the RESOLVED interval, which for
        Monthly / Quarterly / Semi-Annual / Annual is a hard-coded 1, 3, 6 or
        1 that can never be non-positive -- so an authored 0 was never looked
        at, and the write door wrote it verbatim into a ``NOT NULL`` column
        carrying ``CHECK (interval_n > 0)``.  The result was an unhandled
        ``IntegrityError`` raised out of the flush, from a value the door's
        own docstring claimed it refused.

        Unreachable through the forms today (both Marshmallow schemas carry
        ``validate.Range(min=1)``), but the guarantee belongs at the door that
        writes the column, not in a different layer -- and
        ``_recurrence_preview`` already reads this field straight from
        ``request.args``.
        """
        spec = spec_for(pattern, interval_n=0)

        with pytest.raises(RecurrenceResolutionError, match="positive"):
            resolve(spec, build_calendar())

    def test_an_empty_schedule_is_refused(self):
        """A rule has nothing to anchor against when its owner has no periods.

        Registration bootstraps a pay period for every user, so this is a
        broken invariant rather than a state to invent a value for.
        """
        spec = spec_for(RecurrencePatternEnum.MONTHLY, day_of_month=15)

        with pytest.raises(RecurrenceResolutionError, match="no pay periods"):
            resolve(spec, PeriodCalendar(user_id=1, periods=()))


@pytest.mark.usefixtures("app")
class TestScheduleShapes:
    """The calendar's own assumptions, at shapes the app permits."""

    def test_a_gap_in_the_schedule_does_not_break_the_month_lookup(self):
        """Pay periods are NOT contiguous by construction (finding D7).

        ``pay_period_service._reject_overlapping_batch`` requires only that a
        new batch start AFTER the latest existing ``end_date``, so a user who
        generates their real schedule later than the period registration
        bootstrapped leaves a gap.  ``earliest_start_in_month`` takes a
        minimum over the periods that exist rather than walking, so a month
        the gap swallows simply has no payday.
        """
        bootstrap = SchedulePeriod(
            period_id=1, period_index=0,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 18),
        )
        real = build_calendar()
        calendar = PeriodCalendar(user_id=1, periods=(bootstrap,) + real.periods)

        assert calendar.earliest_start_in_month(2026, 1) == date(2026, 1, 5)
        assert calendar.earliest_start_in_month(2026, 2) is None
        assert calendar.opening_bound() == date(2026, 1, 5)

    def test_a_ninety_day_cadence_resolves_a_monthly_rule(self):
        """A cadence longer than a month leaves months with no payday at all.

        ``cadence_days`` is user-selectable 1..365
        (``ck_pay_schedule_cadence_range``), and the calendar derivation never
        consults the schedule, so a monthly rule anchors on its own day
        whatever the paycheck rhythm.
        """
        calendar = build_calendar(cadence_days=90, count=8)

        resolved = resolve(
            spec_for(RecurrencePatternEnum.MONTHLY, day_of_month=15), calendar,
        )

        assert resolved.anchor_date == date(2026, 4, 15)

    def test_a_period_id_naming_no_period_is_treated_as_absent(self):
        """A stale start-period id cannot shift the anchor.

        ``start_period_id`` is ``ON DELETE SET NULL``, but an id held in
        memory can outlive its row -- and a cross-user id reaches the resolver
        through the preview endpoint's args.  Neither is in this owner's
        calendar, so neither contributes to the bound.
        """
        resolved = resolve(
            spec_for(RecurrencePatternEnum.EVERY_PERIOD, start_period_id=99_999),
            build_calendar(),
        )

        assert resolved.anchor_date == date(2026, 3, 26)
