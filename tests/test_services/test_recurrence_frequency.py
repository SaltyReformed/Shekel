"""How often a recurrence fires, with no schedule (plan step R7a-2b).

``resolve()`` answers what a recurrence MEANS against one owner's pay
calendar, and most of that answer needs no calendar: a pattern's interval and
unit are properties of the PATTERN.  ``cadence_of`` is that schedule-free half,
and it exists because ``obligations_aggregator`` -- which turns every recurring
template into a monthly figure on ``/savings`` and the Recurring surface -- has
no calendar and could therefore not use the two-axis vocabulary at all.

**Written where the old shapes could not answer.**  The seven-branch switch and
the three-member ``frozenset`` this replaced were both correct for the cadences
someone had listed, so a test over those cadences alone cannot tell the
derivation from the enumeration.  Every case here either uses a cadence the
closed set cannot name, varies the OWNER, or pins a boundary.

Clock discipline (``.claude/rules/testing.md``): nothing here reads a clock.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    PeriodPlacementEnum,
    RecurrencePatternEnum,
    RecurrenceUnitEnum,
)
from app.services.pay_calendar import PayCadence
from app.services.recurrence import (
    Cadence,
    RecurrenceFrequencyError,
    RecurrenceResolutionError,
    cadence_of,
    decode_pattern,
)
# The ENCODER is package-private -- ``__init__`` re-exports its inverse and not
# it, because the write door is its only production caller.  Naming its
# definition site is what lets the round-trip below test the mapping itself
# rather than one direction of it.
from app.services.recurrence._frequency import encode_cadence

#: 14 days between paydays, 26 a year.
_BIWEEKLY = PayCadence(cadence_days=14)

#: 7 days between paydays, 52 a year.
_WEEKLY = PayCadence(cadence_days=7)

#: 30 days between paydays, 12 a year -- a paycheck IS a month.
_MONTHLY_PAID = PayCadence(cadence_days=30)


class TestUnitsPerYearIsExact:
    """The count before the interval divides it, and it is a whole number."""

    @pytest.mark.parametrize(
        "unit,expected",
        [
            (RecurrenceUnitEnum.WEEK, "52"),
            (RecurrenceUnitEnum.MONTH, "12"),
            (RecurrenceUnitEnum.YEAR, "1"),
        ],
    )
    def test_a_calendar_unit_is_the_same_for_every_owner(self, unit, expected):
        """No owner varies how many months are in a year.

        The pay cadence is handed in and must be ignored: a monthly bill is
        monthly for a weekly-paid owner too.  Asserting across three cadences
        is what makes "ignored" a measurement rather than a claim.
        """
        cadence = Cadence(interval_n=1, unit=unit)
        for pay in (_BIWEEKLY, _WEEKLY, _MONTHLY_PAID):
            assert cadence.units_per_year(pay) == Decimal(expected)

    def test_the_week_unit_derives_52_from_the_same_rule_as_a_pay_cadence(self):
        """``round(365.2425 / 7) = 52`` -- not a hardcoded 52.

        Plan step R8 is the WEEK unit's first writer; this pins that it
        inherits the paycheck-count rule rather than a second one invented for
        it, by asserting the two agree.
        """
        assert Cadence(
            interval_n=1, unit=RecurrenceUnitEnum.WEEK,
        ).units_per_year(_BIWEEKLY) == PayCadence(
            cadence_days=7,
        ).periods_per_year

    def test_the_period_unit_is_the_OWNER_S_count(self):
        """The one unit whose year depends on who is asking.

        26 biweekly, 52 weekly, 12 monthly-paid -- three answers for one
        cadence value, which is the whole reason this takes a parameter.
        """
        cadence = Cadence(interval_n=1, unit=RecurrenceUnitEnum.PERIOD)
        assert cadence.units_per_year(_BIWEEKLY) == Decimal("26")
        assert cadence.units_per_year(_WEEKLY) == Decimal("52")
        assert cadence.units_per_year(_MONTHLY_PAID) == Decimal("12")

    def test_it_does_NOT_apply_the_interval(self):
        """Every 3 paychecks still reports 26 units a year, not 8.67.

        The accuracy decision this method exists for: handing back the exact
        whole count lets a money conversion divide ONCE by the exact integer
        ``interval_n * 12``.  Returning the quotient instead rounds twice and
        moved 31,072 displayed cents in a 52,000,000-case sweep -- wrongly.
        ``occurrences_per_year`` is the one that applies the interval.
        """
        every_third = Cadence(
            interval_n=3, unit=RecurrenceUnitEnum.PERIOD,
        )
        assert every_third.units_per_year(_BIWEEKLY) == Decimal("26")
        assert every_third.occurrences_per_year(_BIWEEKLY) == (
            Decimal("26") / 3
        )

    def test_a_unit_with_no_yearly_count_is_refused(self):
        """A member added to the enum without one raises rather than guesses.

        Constructed by hand because every member HAS a count today -- which is
        the point: the refusal is the control on the next member added, and a
        control that cannot be exercised is not one.  A silent default here
        would put a wrong monthly figure in the emergency-fund baseline.
        """
        class _Unlisted:  # pylint: disable=too-few-public-methods
            """A unit member this module has no yearly count for."""

            def __repr__(self):
                return "<Unlisted>"

        with pytest.raises(RecurrenceFrequencyError, match="no yearly count"):
            Cadence(
                interval_n=1, unit=_Unlisted(),
            ).units_per_year(_BIWEEKLY)


class TestCadenceOfReadsAPatternWithNoSchedule:
    """The seam the monthly equivalent needed and could not have."""

    @pytest.mark.parametrize(
        "pattern,interval_n,expected_interval,expected_unit",
        [
            (RecurrencePatternEnum.EVERY_PERIOD, 1, 1,
             RecurrenceUnitEnum.PERIOD),
            (RecurrencePatternEnum.EVERY_N_PERIODS, 4, 4,
             RecurrenceUnitEnum.PERIOD),
            (RecurrencePatternEnum.MONTHLY, 1, 1, RecurrenceUnitEnum.MONTH),
            (RecurrencePatternEnum.MONTHLY_FIRST, 1, 1,
             RecurrenceUnitEnum.MONTH),
            (RecurrencePatternEnum.QUARTERLY, 1, 3, RecurrenceUnitEnum.MONTH),
            (RecurrencePatternEnum.SEMI_ANNUAL, 1, 6,
             RecurrenceUnitEnum.MONTH),
            (RecurrencePatternEnum.ANNUAL, 1, 1, RecurrenceUnitEnum.YEAR),
        ],
    )
    def test_every_modelled_pattern_reads(
        self, app, pattern, interval_n, expected_interval, expected_unit,
    ):
        """All seven, with no calendar anywhere in the call.

        Total over the closed set: a pattern with no entry would raise a
        ``KeyError`` here rather than silently reading as something else.
        """
        with app.app_context():
            cadence = cadence_of(
                ref_cache.recurrence_pattern_id(pattern), interval_n,
            )
            assert cadence == Cadence(
                interval_n=expected_interval, unit=expected_unit,
            )

    def test_the_authored_interval_is_read_only_by_every_n_periods(self, app):
        """A hidden form input cannot make a Quarterly rule read as monthly.

        ``interval_n`` is a column on every rule but means something for only
        one pattern, and the form submits its hidden input regardless.  A
        Quarterly rule carrying ``interval_n = 7`` still reads ``(3, MONTH)``.
        """
        with app.app_context():
            assert cadence_of(
                ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.QUARTERLY,
                ), 7,
            ) == Cadence(interval_n=3, unit=RecurrenceUnitEnum.MONTH)

    def test_an_unmodelled_pattern_is_REFUSED_not_answered(self, app):
        """The ``None`` this replaced is the point of the change.

        ``amount_to_monthly`` used to answer ``None`` for a pattern the enum
        does not name, and ``obligations_aggregator`` dropped the template --
        so the same row 500'd the Recurring surface (through ``read_rule``,
        which resolves and RAISES) while quietly leaving the obligation out of
        the ``/savings`` emergency-fund baseline.  One state, two dispositions.
        Ruled 2026-08-11: refuse, like every other reader of a stored rule.
        """
        with app.app_context():
            surplus = max(
                ref_cache.recurrence_pattern_id(member)
                for member in RecurrencePatternEnum
            ) + 1
            with pytest.raises(
                RecurrenceResolutionError,
                match=f"pattern id {surplus} matches no RecurrencePatternEnum",
            ):
                cadence_of(surplus, 1)

    def test_a_non_positive_interval_is_refused(self, app):
        """Zero would divide by zero in the occurrence rate.

        The refusal names the value and the pattern; it named the owner too
        until this seam moved out of ``_resolution``, which reads a RULE where
        this reads a PATTERN and has no owner to name.
        """
        with app.app_context():
            with pytest.raises(
                RecurrenceResolutionError, match="must be positive, got 0",
            ):
                cadence_of(
                    ref_cache.recurrence_pattern_id(
                        RecurrencePatternEnum.EVERY_N_PERIODS,
                    ), 0,
                )



class TestTheStorageEncodingRoundTrips:
    """``encode_cadence`` and ``decode_pattern`` are inverses (plan step R7b).

    **This replaced a tautology, and the replacement is the point.**  The class
    here before asserted that ``resolve`` and ``cadence_of`` read one pattern
    the same way -- true by construction, since both consulted the same table
    through the same helper, and labelled at the time as a change detector
    rather than a control.  The vocabularies are genuinely two now: a caller
    AUTHORS ``(interval, unit, placement)`` and the table stores a pattern id,
    so there is a real mapping with a real direction, and a real way for one
    side to drift.

    A round trip is what catches that drift, and it catches it in the direction
    a hand-written inverse fails in -- an entry changed on one side only.  The
    inverse is COMPUTED from the forward table, so that class of defect is
    structurally gone; this is what says so rather than assuming it.
    """

    @pytest.mark.parametrize("submitted", [1, 2, 3, 4, 6, 12])
    def test_every_stored_reading_re_encodes_to_itself(self, app, submitted):
        """Decode, encode, decode again: the cadence never moves.

        Swept over every pattern the application models AND over intervals
        that are and are not a pattern's own, because the column is read for
        exactly one cadence and ignored for the rest -- so a decoder that
        stopped ignoring it would move only the calendar readings.
        """
        with app.app_context():
            for member in RecurrencePatternEnum:
                pattern_id = ref_cache.recurrence_pattern_id(member)
                reading = decode_pattern(pattern_id, submitted)

                encoded = encode_cadence(
                    reading.cadence.interval_n,
                    reading.cadence.unit,
                    reading.placement,
                )
                again = decode_pattern(
                    ref_cache.recurrence_pattern_id(encoded.pattern),
                    encoded.interval_n,
                )

                assert again == reading, member
                # The COLUMN too, and an adversarial review required it: the
                # decode ignores that column for every pattern but one, so an
                # encoder that wrote a MONTH count into a column spelled "every
                # N pay periods" would round-trip green here.
                assert encoded.interval_n == (
                    reading.cadence.interval_n
                    if reading.cadence.unit is RecurrenceUnitEnum.PERIOD
                    and reading.cadence.interval_n > 1
                    else 1
                ), member

    def test_the_pattern_itself_round_trips_where_it_names_one_cadence(
        self, app,
    ):
        """A stored pattern re-encodes to itself, with ONE stated exception.

        ``Every N Periods`` with ``N = 1`` and ``Every Period`` are the SAME
        cadence -- every paycheck -- and the encoder canonicalises onto the
        named one.  That is deliberate and it is what plan step R7c\'s
        downgrade needs: two names for one reading make the reverse mapping
        ambiguous, so the encoder picks and this pins which.

        Every other member is its own round trip, which is what says the
        inverse table is complete rather than merely non-empty.
        """
        with app.app_context():
            for member in RecurrencePatternEnum:
                reading = decode_pattern(
                    ref_cache.recurrence_pattern_id(member), 1,
                )
                encoded = encode_cadence(
                    reading.cadence.interval_n,
                    reading.cadence.unit,
                    reading.placement,
                )

                expected = (
                    RecurrencePatternEnum.EVERY_PERIOD
                    if member is RecurrencePatternEnum.EVERY_N_PERIODS
                    else member
                )
                assert encoded.pattern is expected, member

    def test_the_inverse_covers_every_modelled_pattern(self, app):
        """No member of the enum is unreachable through the encoder.

        A member the inverse table missed would be decodable and NOT
        encodable: a rule already stored could be read but never re-authored,
        so the read-modify-re-author idiom every in-place writer uses would
        raise on it.
        """
        with app.app_context():
            reachable = set()
            for member in RecurrencePatternEnum:
                reading = decode_pattern(
                    ref_cache.recurrence_pattern_id(member), 2,
                )
                reachable.add(
                    encode_cadence(
                        reading.cadence.interval_n,
                        reading.cadence.unit,
                        reading.placement,
                    ).pattern
                )

            # Swept at interval 2 rather than 1 precisely so ``Every N
            # Periods`` is reachable.  At 1 it canonicalises onto ``Every
            # Period`` (the case above pins that), so a sweep at 1 would report
            # it missing -- and a genuinely absent entry would then be hidden
            # behind the same symptom.
            assert reachable == set(RecurrencePatternEnum)

    @pytest.mark.parametrize(
        "interval_n,unit",
        [
            (2, RecurrenceUnitEnum.MONTH),
            (2, RecurrenceUnitEnum.YEAR),
            (1, RecurrenceUnitEnum.WEEK),
            (4, RecurrenceUnitEnum.MONTH),
        ],
    )
    def test_a_cadence_with_no_pattern_to_name_it_is_refused(
        self, app, interval_n, unit,
    ):
        """The gap plan step R7c closes, stated once at the encoder.

        Each of these resolves and walks correctly -- the two-axis model has no
        trouble with "every 2 months" -- and none of them can be STORED, because
        the table names its cadence with a closed pattern set.  Refusing is what
        stops such a rule being written as a DIFFERENT cadence that happens to
        have a name.
        """
        with app.app_context():
            with pytest.raises(RecurrenceResolutionError, match="no recurrence"):
                encode_cadence(
                    interval_n, unit, PeriodPlacementEnum.CONTAINING_DATE,
                )
