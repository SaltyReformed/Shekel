"""The pure recurrence resolver (plan steps R2c-1, R2d, R7c-b).

``app.services.recurrence.resolve`` turns an AUTHORED recurrence into what it
means against one schedule, and it is pure: a spec and a
:class:`~app.services.pay_calendar.PayCalendar` in, a
:class:`~app.services.recurrence.ResolvedRecurrence` out.  So every case below
is exercised at EXACT dates against a hand-built schedule -- no database, no
clock -- and each assertion carries the arithmetic that produces it.

**Plan step R7c-b removed most of what this file used to prove, by deleting
what it was proving.**  Until then the resolver RECONSTRUCTED a rule's first
occurrence on every read, from ``(start_date, day_of_month, month_of_year)``
plus the owner's schedule, through three derivations -- an effective-start
maximum, a month-ordinal residue walk, and a scan of the schedule's own months
for ``Monthly First``.  Ruling **R-R16** made that date an authored column, so
all three are gone and the classes that measured them went with them:

* ``TestCalendarFamily`` and ``TestFirstOfMonthFamily`` covered two of the
  derivations directly;
* ``TestTheHorizonDependentFirstOfMonthAnchor`` measured plan ledger row
  **D10** -- extending the schedule could move a ``Monthly First`` rule's first
  occurrence a month earlier.  A stored date cannot move, so the row CLOSES and
  :class:`TestTheFirstOccurrenceIsAuthored` asserts the property that replaced
  it;
* ``TestMonthEndClamping`` measured which anchors recorded a ``nominal_day``;
  the pair is authored now and :class:`TestTheNominalDayPair` covers the
  invariant that keeps the two fields in step.

What is LEFT for this file to prove is smaller and sharper: the two derivations
``resolve`` still makes (the pay-period normalisation and the cycle phase), the
structural invariant on the ``(starts_on, nominal_day)`` pair, and the refusals.

The schedule these tests resolve against is the developer's own shape: first
payday 2026-03-26, 14-day cadence.  That is deliberate rather than arbitrary --
the live derivation was measured on it while building R2b, so an assertion here
can be checked against a real answer.
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
from app.services.pay_calendar import PayCalendar
from app.services.recurrence import (
    RecurrenceResolutionError,
    RecurrenceSpec,
    decode_pattern,
    is_offerable_nominal_day,
    occurrence_placements,
    offerable_nominal_days,
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

#: Every ``(interval_n, offset)`` pair a paycheck-space cadence can reach, for
#: intervals 1..8.  A phase is only meaningful modulo its interval, so this is
#: the COMPLETE space rather than a sample -- and since plan step R7b-4 a phase
#: is reached by moving the rule's first occurrence rather than by stating one,
#: so the sweep exercises the derivation over the whole space instead of
#: assuming it.
_LEGAL_PHASES = [
    (interval_n, offset)
    for interval_n in range(1, 9)
    for offset in range(interval_n)
]


def build_calendar(
    first_payday: date = _FIRST_PAYDAY,
    cadence_days: int = _CADENCE_DAYS,
    count: int = _PERIOD_COUNT,
    user_id: int = _USER_ID,
) -> PayCalendar:
    """Return a contiguous calendar, built the way the app builds one.

    Hands over the PAYDAYS and lets the value derive the rest, which is what
    ``pay_calendar.calendar_for`` does: each period ends the day before the
    next payday, and the last one ``cadence_days - 1`` after its own.  On a
    contiguous run those two rules coincide, so this reproduces exactly the
    schedule ``pay_period_write.record_paydays`` materialises.
    ``period_id`` is ``index + 1`` so a test can name a period by the same
    number it names its index by.

    Args:
        first_payday: The first period's ``start_date``.
        cadence_days: Days between paydays.
        count: How many periods to build.
        user_id: The owner the calendar declares, which ``resolve`` checks
            against the spec's.

    Returns:
        The :class:`~app.services.pay_calendar.PayCalendar`.
    """
    return PayCalendar.from_paydays(
        paydays=[
            (index + 1, first_payday + timedelta(days=cadence_days * index))
            for index in range(count)
        ],
        cadence_days=cadence_days,
        user_id=user_id,
    )


def payday(index: int) -> date:
    """Return the payday of period *index* on the default schedule.

    Named rather than open-coded because half the cases below turn on which
    PAYCHECK a date falls in, and ``2026-03-26 + 5 x 14`` written out at each
    site is an arithmetic error waiting to be missed in review.

    Args:
        index: The 0-based period index.

    Returns:
        That period's ``start_date``.
    """
    return _FIRST_PAYDAY + timedelta(days=_CADENCE_DAYS * index)


def spec_for(
    pattern: RecurrencePatternEnum, starts_on: date, **overrides,
) -> RecurrenceSpec:
    """Return a spec for the cadence *pattern* names, starting on *starts_on*.

    **Keyed on the closed-set member even though the spec no longer carries
    one** (plan step R7b), and deliberately: every case in this file was
    written against a named pattern and hand-checked at exact dates, so
    re-keying them onto ``(interval, unit, placement)`` by hand would be a
    silent opportunity to change what a case measures.  The translation goes
    through :func:`~app.services.recurrence.decode_pattern`, the same seam the
    read door uses for the interval, so a case still means what its name says.

    A case ABOUT the two-axis vocabulary states the axes directly instead --
    see the last of :class:`TestRefusals`.

    Args:
        pattern: The pattern member whose cadence to build.
        starts_on: The rule's first occurrence.  Required here as it is on the
            spec, because a recurrence with no first occurrence has no cadence
            (ruling R-R16).
        **overrides: Any :class:`~app.services.recurrence.RecurrenceSpec`
            field to set.

    Returns:
        The spec.
    """
    # Decoded at TWO, not one, and an adversarial review of plan step R7b-1
    # is why: a pattern that names its own interval ignores this number, and
    # ``Every N Periods`` -- the one that does not -- would otherwise collapse
    # onto ``Every Period``'s reading, silently dropping a member from every
    # sweep in this file that claims to cover the whole enum.
    interval_override = overrides.pop("interval_n", None)
    reading = decode_pattern(ref_cache.recurrence_pattern_id(pattern), 2)
    return RecurrenceSpec(
        user_id=_USER_ID,
        unit=reading.cadence.unit,
        starts_on=starts_on,
        interval_n=(
            reading.cadence.interval_n if interval_override is None
            else interval_override
        ),
        placement=reading.placement,
        **overrides,
    )


@pytest.mark.usefixtures("app")
class TestTheFirstOccurrenceIsAuthored:
    """A calendar cadence's first occurrence is what the caller stated.

    **This is plan ledger row D10's replacement**, and it is the stronger
    property.  That row recorded that a ``Monthly First`` rule's anchor was
    HORIZON-DEPENDENT: ``_first_of_month_anchor`` scanned the schedule's own
    months and fell back past the last payday, so extending the schedule could
    move the first occurrence a month earlier.  Nothing here scans anything --
    the date is the caller's -- so the class asserts what that buys: the same
    spec resolves to the same date against schedules that share nothing but
    their owner.
    """

    def test_a_monthly_rule_keeps_the_date_it_states(self):
        """The resolved value passes ``starts_on`` through unchanged.

        A monthly rule stating 2026-07-15 resolves to 2026-07-15, whatever the
        schedule opens on.  Before plan step R7c-b the answer was "the first
        15th on or after the schedule's opening payday", which is the same date
        only by coincidence of this schedule.
        """
        resolved = resolve(
            spec_for(RecurrencePatternEnum.MONTHLY, date(2026, 7, 15)),
            build_calendar(),
        )

        assert resolved.starts_on == date(2026, 7, 15)
        assert resolved.interval_n == 1
        assert resolved.unit is RecurrenceUnitEnum.MONTH
        assert resolved.placement is PeriodPlacementEnum.CONTAINING_DATE
        assert resolved.shift is BusinessDayShiftEnum.NONE
        assert resolved.nominal_day is None

    @pytest.mark.parametrize(
        "pattern",
        [
            RecurrencePatternEnum.MONTHLY,
            RecurrencePatternEnum.QUARTERLY,
            RecurrencePatternEnum.SEMI_ANNUAL,
            RecurrencePatternEnum.ANNUAL,
            RecurrencePatternEnum.MONTHLY_FIRST,
        ],
        ids=lambda pattern: pattern.name,
    )
    def test_no_calendar_cadence_moves_when_the_schedule_does(self, pattern):
        """Extending or shortening the schedule cannot move the first occurrence.

        The three schedules below share only their owner: 61 biweekly periods
        from 2026-03-26, 4 of them, and 12 quarterly ones from a different
        year.  The old derivation answered a different date on each for at
        least one of these cadences -- ``Monthly First`` moved a month at a
        time, and every calendar cadence moved with the OPENING payday, which
        is what ``GREATEST(opening, start_date)`` made it a function of.
        """
        stated = date(2026, 11, 1)
        spec = spec_for(pattern, stated)

        answers = {
            resolve(spec, calendar).starts_on
            for calendar in (
                build_calendar(),
                build_calendar(count=4),
                build_calendar(
                    first_payday=date(2024, 1, 1), cadence_days=90, count=12,
                ),
            )
        }

        assert answers == {stated}

    def test_a_first_occurrence_before_the_schedule_opens_is_kept(self):
        """A calendar rule may start before the owner's first payday.

        2026-01-15 precedes the schedule's opening (2026-03-26).  The old
        derivation CLAMPED to the opening through its maximum; the date is the
        user's statement now, so it is kept -- and the occurrences before the
        schedule opens simply place nowhere, which is what
        ``placed_periods`` already drops.  Keeping it is what makes the value
        a fact about the RULE rather than about the owner's schedule.
        """
        calendar = build_calendar()
        resolved = resolve(
            spec_for(RecurrencePatternEnum.MONTHLY, date(2026, 1, 15)),
            calendar,
        )

        assert resolved.starts_on == date(2026, 1, 15)
        placed = [
            placement for placement in occurrence_placements(
                resolved, calendar,
            )
            if placement.period is not None
        ]
        assert placed[0].occurrence == date(2026, 4, 15), (
            "the first PLACEABLE occurrence is the first one the schedule "
            "reaches, which is April's -- January's, February's and March's "
            "are named and unplaced"
        )


@pytest.mark.usefixtures("app")
class TestThePayPeriodNormalisation:
    """The ONE derivation ``resolve`` still makes about the date.

    A paycheck-space cadence's occurrences are PAYDAYS, so a date that is not
    one does not name an occurrence.  ``resolve`` answers the payday of the
    first paycheck that has not ENDED before it -- which is
    ``_occurrence._period_walk``'s own admission test -- so ``starts_on`` is
    that walk's first element by construction.

    **That removes plan ledger row D6's asymmetry rather than restating it.**
    Until plan step R7c-b the field meant the first occurrence for three units
    and an opening BOUND for the fourth, and a separate ``first_occurrence``
    function existed to reconcile the two.
    """

    def test_a_payday_is_its_own_first_occurrence(self):
        """A date that IS a payday normalises to itself.

        Period index 3 opens 2026-03-26 + 3 x 14 = 2026-05-07.
        """
        resolved = resolve(
            spec_for(RecurrencePatternEnum.EVERY_PERIOD, payday(3)),
            build_calendar(),
        )

        assert resolved.starts_on == date(2026, 5, 7) == payday(3)

    def test_a_mid_period_date_normalises_onto_its_paycheck(self):
        """A date inside a paycheck resolves to that paycheck's payday.

        2026-05-14 falls inside period index 3 (2026-05-07..2026-05-20), and
        the money leaves on the payday -- so the rule bills in THAT paycheck
        rather than the next, which is what lets a loan whose first
        installment falls mid-period pay from it (plan step C9a).

        Before plan step R7c-b the stored value was the mid-period date and
        ``first_occurrence`` did this conversion separately; the two could not
        disagree, but they were two functions where one will do.
        """
        resolved = resolve(
            spec_for(RecurrencePatternEnum.EVERY_PERIOD, date(2026, 5, 14)),
            build_calendar(),
        )

        assert resolved.starts_on == payday(3)

    def test_a_date_below_the_opening_names_the_first_paycheck(self):
        """There is no earlier paycheck for such a rule to bill in.

        A bound of 2026-01-01 precedes the schedule's opening payday
        2026-03-26, so the first paycheck the rule can bill in is that one.
        Unlike a CALENDAR cadence, whose occurrences are dates it can name
        before the schedule reaches them, a paycheck-space rule has nothing to
        name there at all.
        """
        resolved = resolve(
            spec_for(RecurrencePatternEnum.EVERY_PERIOD, date(2026, 1, 1)),
            build_calendar(),
        )

        assert resolved.starts_on == _FIRST_PAYDAY

    def test_a_date_past_the_horizon_projects_the_payday(self):
        """The answer is TOTAL, which is what the NOT NULL column requires.

        The 61-period schedule's last payday is 2026-03-26 + 60 x 14 =
        2028-07-13.  A date one cadence past it has no SAVED paycheck, so the
        calendar projects forward at the owner's own cadence -- the same answer
        the schedule will hold once it extends.
        """
        last_payday = payday(_PERIOD_COUNT - 1)
        assert last_payday == date(2028, 7, 13)

        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.EVERY_PERIOD,
                last_payday + timedelta(days=_CADENCE_DAYS + 3),
            ),
            build_calendar(),
        )

        assert resolved.starts_on == last_payday + timedelta(
            days=_CADENCE_DAYS,
        )

    @pytest.mark.parametrize("offset_days", [0, 1, 7, 13])
    def test_it_is_the_walks_own_first_yield(self, offset_days):
        """The normalisation and the occurrence walk cannot disagree.

        Stated as a property rather than as a date: whatever ``resolve``
        answers, walking the rule's own occurrences must yield it FIRST.  That
        is the invariant the deleted ``first_occurrence`` function existed to
        hold across two modules, and it is structural now -- but a structural
        claim still has to be shown once.
        """
        calendar = build_calendar()
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.EVERY_PERIOD,
                payday(4) + timedelta(days=offset_days),
            ),
            calendar,
        )

        placements = occurrence_placements(resolved, calendar)

        assert placements[0].occurrence == resolved.starts_on


@pytest.mark.usefixtures("app")
class TestThePhaseIsReadOffTheFirstOccurrence:
    """``offset_periods`` is DERIVED, and from ONE fact.

    **Plan ledger rows D21 and D24 close here.**  The phase used to be read
    from the rule's start PERIOD when the schedule handed in contained it and
    from the stored ``offset_periods`` COLUMN when it did not -- two paths, one
    of which could disagree with the anchor.  It is the ordinal of the paycheck
    ``starts_on`` falls in, on every path, and nothing reads the column at all.
    """

    def test_it_is_the_ordinal_of_the_starting_paycheck(self):
        """Period index 5 opens 2026-06-04, so an interval-3 rule phases at 2.

        ``5 % 3 == 2``.  The interval the form submitted is kept because this
        is the one pattern whose interval is authored rather than named by its
        pattern.
        """
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.EVERY_N_PERIODS, payday(5), interval_n=3,
            ),
            build_calendar(),
        )

        assert payday(5) == date(2026, 6, 4)
        assert resolved.interval_n == 3
        assert resolved.offset_periods == 2
        assert resolved.starts_on == date(2026, 6, 4)

    def test_a_mid_period_date_phases_on_the_paycheck_it_falls_in(self):
        """The date need not be a payday for the phase to be exact.

        2026-06-10 falls INSIDE period index 5 (2026-06-04..2026-06-17), so an
        interval-3 rule starting then phases at ``5 % 3 == 2`` -- the same
        answer the payday itself gives.  It is the PAYCHECK the money comes out
        of that the cadence counts, not the calendar day.
        """
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.EVERY_N_PERIODS,
                date(2026, 6, 10), interval_n=3,
            ),
            build_calendar(),
        )

        assert resolved.offset_periods == 2
        assert resolved.starts_on == payday(5)

    def test_a_loans_first_installment_phases_the_rule_on_its_own_paycheck(self):
        """The shape that CHANGED at plan step R7b-4, stated with its numbers.

        ``loan_recurrence_sync`` stamps a first occurrence onto ANY rule, so
        "every 5 paychecks into my mortgage" reaches this.  2026-09-15 falls in
        period index 12 (2026-03-26 + 12 x 14 = 2026-09-10, spanning to
        2026-09-23).

        **Before**: nothing derived a phase without a start PERIOD, so the rule
        kept the column's 0 and the anchor advanced to the first index at or
        after the bound that was a multiple of 5 -- index 15, opening
        2026-10-22.  That aligned the loan to the SCHEDULE's origin, a fact the
        loan has nothing to do with.

        **After**: ``12 % 5 == 2``, and the rule fires 12, 17, 22 -- starting
        from the paycheck the first installment is actually due out of.  Six
        weeks earlier than the old answer, and ``$0.00`` on live data: all 46
        production rules carry ``interval_n = 1``.
        """
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.EVERY_N_PERIODS,
                date(2026, 9, 15), interval_n=5,
            ),
            build_calendar(),
        )

        assert resolved.offset_periods == 2
        assert resolved.starts_on == payday(12) == date(2026, 9, 10)

    @pytest.mark.parametrize(
        ("interval_n", "offset"), _LEGAL_PHASES, ids=str,
    )
    def test_the_first_occurrence_is_in_phase_for_every_pair(
        self, interval_n, offset,
    ):
        """The invariant a self-consistency check cannot see.

        Re-resolving a rule and comparing it to itself proves IDEMPOTENCE.  It
        does not prove that the stored phase and the stored date describe the
        SAME cadence -- and a neutral review of plan step R7b-4 found two
        shapes where they did not.  The agreement condition is exact and needs
        no engine: the period containing ``starts_on`` must be one the walk
        fires on, i.e. ``(period_index - offset_periods) % interval_n == 0``.

        Swept over the COMPLETE phase space for intervals 1..8, reached by
        moving the rule's first occurrence rather than by stating a phase --
        which is what exercises the derivation instead of assuming it.
        """
        calendar = build_calendar()
        spec = spec_for(
            RecurrencePatternEnum.EVERY_N_PERIODS,
            payday(offset), interval_n=interval_n,
        )

        resolved = resolve(spec, calendar)

        assert resolved.offset_periods == offset % interval_n
        containing = [
            period for period in calendar.periods
            if period.start_date <= resolved.starts_on <= period.end_date
        ]
        assert containing, (
            f"first occurrence {resolved.starts_on} falls in no period, so "
            f"the phase cannot be checked"
        )
        index = containing[0].period_index
        assert (index - resolved.offset_periods) % interval_n == 0, (
            f"first occurrence {resolved.starts_on} lies in period index "
            f"{index}, which the walk does NOT fire on "
            f"(offset={resolved.offset_periods}, interval={interval_n})"
        )

    @pytest.mark.parametrize(
        "pattern",
        [
            RecurrencePatternEnum.EVERY_PERIOD,
            RecurrencePatternEnum.MONTHLY,
            RecurrencePatternEnum.QUARTERLY,
            RecurrencePatternEnum.SEMI_ANNUAL,
            RecurrencePatternEnum.ANNUAL,
            RecurrencePatternEnum.MONTHLY_FIRST,
        ],
        ids=lambda pattern: pattern.name,
    )
    def test_no_other_cadence_ever_carries_a_phase(self, pattern):
        """Only ``Every N Periods`` reads the phase, so only it derives one.

        A rule of any other cadence starting in period index 5 still resolves
        to phase 0: the derivation is scoped to the PERIOD unit at an interval
        above 1, so nothing else acquires one.  That is what makes the column
        meaningless-by-construction for the rest rather than
        meaningless-by-convention.
        """
        assert resolve(
            spec_for(pattern, payday(5)), build_calendar(),
        ).offset_periods == 0


@pytest.mark.usefixtures("app")
class TestTheNominalDayPair:
    """``(starts_on, nominal_day)`` is one fact in two fields, kept in step.

    ``nominal_day`` records the day a rule MEANS when ``starts_on``'s own month
    was too short to hold it -- April has no 31st, so a day-31 rule first
    occurring there carries ``2026-04-30`` and ``31`` (ruling R-R3).  Presence
    is the discriminator, and plan step R7c-b is what made presence IMPLY that
    the clamp happened: the invariant is refused at CONSTRUCTION and mirrored
    by ``ck_recurrence_rules_nominal_day``, so
    ``_occurrence._require_generable``'s clamp branch was deleted rather than
    kept passing.
    """

    @pytest.mark.parametrize(
        ("starts_on", "expected"),
        [
            (date(2026, 4, 30), (31,)),
            (date(2026, 2, 28), (29, 30, 31)),
            (date(2024, 2, 29), (30, 31)),
            (date(2026, 6, 30), (31,)),
            (date(2026, 1, 31), ()),
            (date(2026, 6, 15), ()),
            (date(2026, 2, 27), ()),
        ],
        ids=lambda value: str(value),
    )
    def test_which_days_a_date_leaves_open(self, starts_on, expected):
        """A date is ambiguous only when it is its month's SHORT last day.

        2026-04-30 is April's last day and April has no 31st, so it could mean
        "the 30th" or "the last day of the month".  2026-01-31 could not: 31 is
        the largest day there is, and the walk already clamps it per month, so
        the date says the whole cadence.  2026-06-15 is not a month end at all.
        """
        assert offerable_nominal_days(
            RecurrenceUnitEnum.MONTH, starts_on,
        ) == expected

    def test_a_cadence_with_no_day_coordinate_offers_none(self):
        """A paycheck-space rule has no day of the month for a month to lose."""
        assert offerable_nominal_days(
            RecurrenceUnitEnum.PERIOD, date(2026, 4, 30),
        ) == ()

    def test_the_pair_survives_resolution(self):
        """A stated nominal day reaches the resolved value unchanged."""
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.MONTHLY, date(2026, 4, 30),
                nominal_day=31,
            ),
            build_calendar(),
        )

        assert resolved.starts_on == date(2026, 4, 30)
        assert resolved.nominal_day == 31
        assert resolved.day_of_month == 31

    def test_the_pair_is_what_keeps_a_month_end_rule_on_month_ends(self):
        """The whole point of the second field, in dates.

        A rule first occurring 2026-04-30 and MEANING the 31st fires on the
        last day of every month.  Without the nominal day the same date means
        the 30th, and May's payment lands a day early -- for good.
        """
        calendar = build_calendar()
        month_end = resolve(
            spec_for(
                RecurrencePatternEnum.MONTHLY, date(2026, 4, 30),
                nominal_day=31,
            ),
            calendar,
        )
        the_thirtieth = resolve(
            spec_for(RecurrencePatternEnum.MONTHLY, date(2026, 4, 30)),
            calendar,
        )

        def first_four(resolved):
            """Return the first four occurrence dates."""
            return [
                placement.occurrence
                for placement in occurrence_placements(resolved, calendar)
            ][:4]

        assert first_four(month_end) == [
            date(2026, 4, 30), date(2026, 5, 31),
            date(2026, 6, 30), date(2026, 7, 31),
        ]
        assert first_four(the_thirtieth) == [
            date(2026, 4, 30), date(2026, 5, 30),
            date(2026, 6, 30), date(2026, 7, 30),
        ]

    @pytest.mark.parametrize(
        ("starts_on", "nominal_day"),
        [
            (date(2026, 4, 15), 30),
            (date(2026, 1, 31), 31),
            (date(2026, 4, 30), 28),
            (date(2026, 2, 28), 15),
            (date(2026, 2, 28), 32),
        ],
        ids=lambda value: str(value),
    )
    def test_a_contradictory_pair_cannot_be_CONSTRUCTED(
        self, starts_on, nominal_day,
    ):
        """The spec refuses it, so no caller can pass one on.

        ``(2026-04-15, 30)`` is the case ``ck_recurrence_rules_nominal_day``
        ADMITTED before plan step R7c-b completed it: 30 is in the domain and
        exceeds 15, and only a walk-time guard caught that April has a 30th.
        ``(2026-01-31, 31)`` is a redundant statement of a day the date holds.
        """
        assert not is_offerable_nominal_day(
            RecurrenceUnitEnum.MONTH, starts_on, nominal_day,
        )
        with pytest.raises(RecurrenceResolutionError, match="nominal_day"):
            spec_for(
                RecurrencePatternEnum.MONTHLY, starts_on,
                nominal_day=nominal_day,
            )

    def test_a_paycheck_cadence_cannot_carry_one(self):
        """A cadence with no day-of-month coordinate names no day at all."""
        with pytest.raises(RecurrenceResolutionError, match="nominal_day"):
            spec_for(
                RecurrencePatternEnum.EVERY_PERIOD, date(2026, 4, 30),
                nominal_day=31,
            )


@pytest.mark.usefixtures("app")
class TestTheDayOfMonthAccessor:
    """The ONE reader of the pair, so the join is written once."""

    def test_it_answers_the_dates_own_day_when_nothing_clamped(self):
        """Every rule whose day is 1-28, and every 31st of a long month."""
        resolved = resolve(
            spec_for(RecurrencePatternEnum.MONTHLY, date(2026, 1, 31)),
            build_calendar(),
        )

        assert resolved.nominal_day is None
        assert resolved.day_of_month == 31

    def test_it_answers_the_nominal_day_when_the_month_clamped(self):
        """The day the rule MEANS, not the day the date could hold."""
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.MONTHLY, date(2026, 2, 28),
                nominal_day=30,
            ),
            build_calendar(),
        )

        assert resolved.day_of_month == 30

    @pytest.mark.parametrize(
        "pattern",
        [
            RecurrencePatternEnum.EVERY_PERIOD,
            RecurrencePatternEnum.EVERY_N_PERIODS,
        ],
        ids=lambda pattern: pattern.name,
    )
    def test_a_paycheck_cadence_has_no_day_of_the_month(self, pattern):
        """``None`` is ABSENCE rather than a missing value.

        A paycheck-space rule has no day-of-month coordinate, and answering the
        date's own day would invent one the cadence never uses -- which is what
        ``compute_due_date`` would then date every generated row from.
        """
        assert resolve(
            spec_for(pattern, payday(2)), build_calendar(),
        ).day_of_month is None


@pytest.mark.usefixtures("app")
class TestTotality:
    """The resolver answers for every reachable shape, or raises.

    A ``NOT NULL`` column has no room for the ``None`` a partial derivation
    would leave, which is why the pay-period normalisation projects past the
    horizon rather than answering the last saved payday.
    """

    @pytest.mark.parametrize(
        "pattern", list(RecurrencePatternEnum), ids=lambda p: p.name,
    )
    def test_every_pattern_resolves_from_one_date(self, pattern):
        """The whole closed set, with nothing stated but the first occurrence.

        Swept over the enum rather than over a list, so a member added without
        a resolution is a failure here rather than a 500 on the surface that
        meets it first.
        """
        spec = spec_for(pattern, payday(2))

        resolved = resolve(spec, build_calendar())

        # The EXACT date, for every pattern, and it is the same one: a PAYDAY
        # is a real occurrence of a calendar cadence (returned verbatim) and
        # the payday of the paycheck a pay-period cadence bills in (returned by
        # the normalisation).  Plan step R7c-b weakened this to
        # ``is not None``, which a resolver answering ANY date would satisfy --
        # including one that silently re-anchored the rule on the schedule's
        # opening, which is the exact defect the ruling behind this class
        # removed.
        assert resolved.starts_on == payday(2)
        # The interval ROUND-TRIPS: 3 for Quarterly, 6 for Semi-Annual, 1
        # elsewhere.  ``>= 1`` held for every one of those and for a resolver
        # that answered 1 for all seven -- which is the live money defect
        # ``stored_interval`` exists to prevent (12 occurrences a year where 4
        # are owed).
        assert resolved.interval_n == spec.interval_n
        assert resolved.unit is spec.unit
        assert resolved.placement is spec.placement
        assert resolved.shift is BusinessDayShiftEnum.NONE

    def test_a_first_occurrence_past_the_horizon_still_resolves(self):
        """A loan originating past the materialised schedule has a date.

        ``loan_recurrence_sync`` stamps a first installment onto any rule, so
        a mortgage closing after the last generated payday reaches this -- and
        before ruling R-R8 there was no derivable answer at all.
        """
        last_payday = payday(_PERIOD_COUNT - 1)
        beyond = last_payday + timedelta(days=365)

        # The PROJECTED payday, computed here the way the calendar computes it
        # -- whole cadences past the last saved one -- rather than asserted to
        # merely exist.  365 is not a multiple of 14, so the projection lands
        # BELOW ``beyond``: 26 x 14 = 364 days on, one day short of it.  That
        # gap is the whole content of the case, and ``is not None`` (which plan
        # step R7c-b left here) could not see it: a resolver that answered
        # ``beyond`` verbatim for the pay-period unit would have passed while
        # seating a paycheck-space rule on a day that is not a payday.
        projected = last_payday + timedelta(
            days=_CADENCE_DAYS * ((beyond - last_payday).days // _CADENCE_DAYS),
        )
        assert projected < beyond

        for pattern, expected in (
            (RecurrencePatternEnum.EVERY_PERIOD, projected),
            # A calendar cadence names its own dates, so the horizon is not its
            # business and the authored date is returned untouched.
            (RecurrencePatternEnum.MONTHLY, beyond),
            (RecurrencePatternEnum.MONTHLY_FIRST, beyond),
        ):
            assert resolve(
                spec_for(pattern, beyond), build_calendar(),
            ).starts_on == expected, pattern.name


@pytest.mark.usefixtures("app")
class TestTheRetiredOncePattern:
    """The eighth ``ref`` row, which no enum member names.

    Plan step R2e-3 deleted the ``Once`` member and left its row to R9 (ruling
    R-R11), because deleting both in one release would leave the auto-rollback
    image unable to boot.  The row must therefore be unreadable rather than
    merely unoffered.
    """

    def test_the_surviving_once_row_is_refused_by_the_decoder(self, app):
        """A stored rule naming it cannot be read into a cadence."""
        with app.app_context():
            row = (
                db.session.query(RecurrencePattern)
                .filter_by(name=_RETIRED_PATTERN_NAME).one()
            )

            with pytest.raises(RecurrenceResolutionError, match="pattern id"):
                decode_pattern(row.id, 1)

    def test_no_enum_member_names_the_retired_row(self, app):
        """The row exists AND no member names it -- both halves, together.

        Either half alone passes for the wrong reason, which is why the pair
        is asserted in one case: without the first, a DELETED row would satisfy
        "no member names it" while breaking the auto-rollback image the ``Once``
        row survives to R9 for (ruling R-R11); without the second, re-adding the
        member would satisfy "the row exists" while re-introducing the
        ambiguity ``decode_pattern`` refuses one case up.

        Plan step R7c-b dropped the first half and the docstring that said why;
        restored, because "the set the application models is narrower than the
        table" is a statement about BOTH sides.
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
class TestRefusals:
    """The broken invariants the resolver refuses rather than papering over.

    Each is a state that would otherwise produce a plausible WRONG date rather
    than an error, which on a recurring bill means money in the wrong paycheck.
    """

    def test_another_users_schedule_is_refused(self):
        """A first occurrence is measured against the OWNER's schedule.

        Two call sites derive the calendar's owner from a different object than
        the rule's, so the pairing is checked rather than assumed.
        """
        with pytest.raises(RecurrenceResolutionError, match="cannot be resolved"):
            resolve(
                spec_for(RecurrencePatternEnum.EVERY_PERIOD, payday(0)),
                build_calendar(user_id=_USER_ID + 1),
            )

    @pytest.mark.parametrize("pattern", list(RecurrencePatternEnum))
    @pytest.mark.parametrize("interval_n", [0, -1, -12])
    def test_a_non_positive_interval_is_refused_for_every_cadence(
        self, pattern, interval_n,
    ):
        """Mirrors ``ck_recurrence_rules_positive_interval``, at the door.

        **Swept over the whole enum, and the calendar cadences are the ones
        that matter** -- so the sweep is the test rather than a decoration.
        The check used to read the RESOLVED interval, which for Monthly /
        Quarterly / Semi-Annual / Annual is a hard-coded 1, 3, 6 or 1 that can
        never be non-positive: an authored 0 was never looked at, and the write
        door wrote it verbatim into a ``NOT NULL`` column carrying
        ``CHECK (interval_n > 0)``.  The result was an unhandled
        ``IntegrityError`` out of the flush, from a value the door's own
        docstring claimed it refused.

        Plan step R7c-b collapsed this to ``EVERY_N_PERIODS`` alone -- the ONE
        cadence whose interval is a column, which is exactly the scoping the
        sweep exists to catch -- so a re-scoped check would have passed.
        Restored, with the negative values that collapse also introduced kept.

        Unreachable through the forms (both schemas carry
        ``validate.Range(min=1)``), but the guarantee belongs at the door that
        writes the column, and ``_recurrence_preview`` reads this field
        straight from ``request.args``.
        """
        with pytest.raises(RecurrenceResolutionError, match="interval_n"):
            resolve(
                spec_for(pattern, payday(0), interval_n=interval_n),
                build_calendar(),
            )

    @pytest.mark.parametrize("day", [0, 32, -1, 99])
    def test_a_stated_due_day_outside_1_31_is_refused(self, day):
        """The last column DOMAIN the write door writes verbatim.

        ``ck_recurrence_rules_due_dom`` bounds it, so letting one through would
        raise an unhandled ``IntegrityError`` naming neither the field nor the
        value.  ``0`` is refused rather than read as absence: the column is
        nullable and Python truthiness conflates the two where the CHECK does
        not.
        """
        with pytest.raises(RecurrenceResolutionError, match="due_day_of_month"):
            resolve(
                spec_for(
                    RecurrencePatternEnum.MONTHLY, date(2026, 4, 15),
                    due_day_of_month=day,
                ),
                build_calendar(),
            )

    def test_a_null_due_day_states_nothing_and_passes(self):
        """``NULL`` is the value that means "this rule states no due day"."""
        resolved = resolve(
            spec_for(
                RecurrencePatternEnum.MONTHLY, date(2026, 4, 15),
                due_day_of_month=None,
            ),
            build_calendar(),
        )

        assert resolved.starts_on == date(2026, 4, 15)

    def test_an_empty_schedule_is_refused_for_a_paycheck_cadence(self):
        """There is no paycheck to normalise onto.

        Registration bootstraps a schedule, so an owner with none is a broken
        invariant rather than a state to paper over.  A CALENDAR cadence needs
        no schedule at all now, which is why the refusal is scoped to the unit
        that does -- see the case below.
        """
        empty = PayCalendar.from_paydays(
            paydays=[], cadence_days=_CADENCE_DAYS, user_id=_USER_ID,
        )

        with pytest.raises(RecurrenceResolutionError, match="no pay periods"):
            resolve(
                spec_for(RecurrencePatternEnum.EVERY_PERIOD, payday(0)), empty,
            )

    def test_a_calendar_cadence_needs_no_schedule_at_all(self):
        """What the authored date bought, stated as the case that changed.

        Before plan step R7c-b every cadence was measured against the schedule
        through ``GREATEST(opening, start_date)``, so an owner with no periods
        could not resolve a monthly bill either.  A calendar cadence names its
        own dates now, so the refusal narrows to the unit whose occurrences ARE
        paydays.
        """
        empty = PayCalendar.from_paydays(
            paydays=[], cadence_days=_CADENCE_DAYS, user_id=_USER_ID,
        )

        resolved = resolve(
            spec_for(RecurrencePatternEnum.MONTHLY, date(2026, 7, 15)), empty,
        )

        assert resolved.starts_on == date(2026, 7, 15)
