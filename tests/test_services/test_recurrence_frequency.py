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
from app.enums import RecurrencePatternEnum, RecurrenceUnitEnum
from app.services.pay_calendar import PayCadence
from app.services.recurrence import (
    Cadence,
    RecurrenceFrequencyError,
    RecurrenceResolutionError,
    cadence_of,
)

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


class TestTheTwoReadingsCannotDisagree:
    """One table, read by ``resolve`` and by ``cadence_of``.

    **This cannot fail against the current code and that is stated rather than
    dressed up**, which an adversarial review of plan step R7a-2b required: both
    sides read the same ``PATTERN_DERIVATIONS`` entry through the same
    ``resolved_interval``, so the assertion is ``f(x) == f(x)`` today.  It is a
    CHANGE DETECTOR, not a control -- the day someone gives ``_resolution`` its
    own copy of the table, or lets R7c's stored columns drift from the derived
    reading, this is what fails.  Kept for that day, and labelled so nobody
    counts it as evidence the split is safe.
    """

    def test_a_resolved_recurrence_carries_the_same_cadence(
        self, app, seed_user, seed_periods,
    ):
        """``resolve``'s (interval, unit) equals ``cadence_of``'s, per pattern.

        ``resolve`` derives an anchor against the owner's schedule and
        ``cadence_of`` does not, but the two-axis half must be identical.
        """
        from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
            calendar_for,
        )
        from app.services.recurrence import (  # pylint: disable=import-outside-toplevel
            RecurrenceSpec,
            resolve,
        )

        with app.app_context():
            user_id = seed_user["user"].id
            calendar = calendar_for(user_id)
            for member in RecurrencePatternEnum:
                pattern_id = ref_cache.recurrence_pattern_id(member)
                spec = RecurrenceSpec(
                    user_id=user_id,
                    pattern_id=pattern_id,
                    interval_n=2,
                    day_of_month=15,
                    month_of_year=3,
                )
                resolved = resolve(spec, calendar)
                assert Cadence(
                    interval_n=resolved.interval_n, unit=resolved.unit,
                ) == cadence_of(pattern_id, 2), member
