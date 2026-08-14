"""How a recurrence is WORDED for display (plan step R7a).

``app.services.recurrence.describe`` replaced eight hand-written Jinja
branches keyed on the closed ``pattern_id`` set.  Three properties are what
this file exists to hold:

* **it is total** over ``(interval_n, unit, placement)``, so a cadence nothing
  can author until plan step R8 -- ``(2, MONTH)``, ``(1, WEEK)``, a
  count-bounded end -- already reads correctly rather than falling through to
  a fallback that titled a ``ref`` row's own name;
* **it says what the rule MEANS**: the month it names is the ANCHOR's, which
  is what moved 2 of the developer's 46 live rules when the surface stopped
  naming the authored ``month_of_year`` -- a column plan step R7c drops.  What
  is pinned HERE is that the anchor is the only month this reads; the shape
  that actually MOVED -- an authored month the schedule opens after -- is
  pinned end to end by
  ``test_recurring_list.TestTheRenderedRecurrenceCell``'s
  ``test_a_quarterly_definition_authored_before_the_schedule_names_its_first``;
* **it hides no bound.**  ``end_date`` and ``max_occurrences`` are mutually
  exclusive (``ck_recurrence_rules_single_end_bound``) and R8 is the count
  bound's first author, but a bound the row states and the cell omits would be
  the surface lying about when a commitment stops.

Pure: every case states a :class:`~app.services.recurrence.ResolvedRecurrence`
directly.  No database, no clock, no schedule -- the phrase is a function of
the resolved value alone, which is exactly why it could move out of a template.
"""

import dataclasses
from datetime import date

import pytest

from app.enums import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.services.recurrence import (
    END_BOUND_KINDS,
    NEVER_ENDS,
    EndBound,
    EndBoundColumns,
    EndsOnDate,
    NeverEnds,
    RecurrenceDescription,
    RecurrenceDescriptionError,
    ResolvedRecurrence,
    describe,
)
# Imported as a MODULE so a control can add a hypothetical enum member to
# the wording tables -- the half-finished edit the raises exist for.
from app.services.recurrence import _describe
from app.services.recurrence import EndsAfterOccurrences
# The shape-set sample table, shared so the totality sweep here covers the
# same closed set the bounds suite does.
from tests.test_services.test_recurrence_bounds import sample_bound
from tests.test_services.test_recurrence_occurrence import resolved_value

_DEFER = PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER
_CONTAIN = PeriodPlacementEnum.CONTAINING_DATE


class TestTheCadencePhrase:
    """One function over ``(interval_n, unit)``, including unauthorable pairs."""

    @pytest.mark.parametrize(
        ("interval_n", "expected"),
        [
            (1, "Every paycheck"),
            (2, "Every 2 paychecks"),
            (3, "Every 3 paychecks"),
        ],
    )
    def test_a_paycheck_space_rule_names_no_calendar_day(
        self, interval_n, expected,
    ):
        """The ``PERIOD`` unit's phrase is the whole phrase.

        Its occurrences are the owner's paydays rather than a day the rule
        names, and its placement is inert -- both placements carry a period's
        own ``start_date`` back to that same period -- so a parenthetical
        would state either nothing or a distinction that makes no difference.
        """
        resolved = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD,
            anchor_date=date(2026, 3, 26),
            interval_n=interval_n,
        )

        assert describe(resolved).cadence == expected

    @pytest.mark.parametrize(
        ("interval_n", "expected"),
        [
            (1, "Monthly (day 22)"),
            (2, "Every 2 months (Apr 22)"),
            (3, "Quarterly (Apr 22)"),
            (4, "Every 4 months (Apr 22)"),
            (6, "Every 6 months (Apr 22)"),
        ],
    )
    def test_a_month_rule_names_its_month_only_once_it_skips_months(
        self, interval_n, expected,
    ):
        """Every month fires -> the day alone; skipped months -> month and day.

        ``(2, MONTH)`` and ``(4, MONTH)`` are cadences no form can author until
        plan step R8, and they are the point: the phrase is derived from the
        pair rather than looked up per pattern, so they read correctly with no
        entry anywhere naming them.
        """
        resolved = resolved_value(
            unit=RecurrenceUnitEnum.MONTH,
            anchor_date=date(2026, 4, 22),
            interval_n=interval_n,
        )

        assert describe(resolved).cadence == expected

    @pytest.mark.parametrize(
        ("interval_n", "expected"),
        [
            (1, "Yearly (Nov 1)"),
            (2, "Every 2 years (Nov 1)"),
        ],
    )
    def test_a_year_rule_always_names_its_month_and_day(
        self, interval_n, expected,
    ):
        """A yearly cadence fires in one month, so naming it is the answer."""
        resolved = resolved_value(
            unit=RecurrenceUnitEnum.YEAR,
            anchor_date=date(2026, 11, 1),
            interval_n=interval_n,
        )

        assert describe(resolved).cadence == expected

    @pytest.mark.parametrize(
        ("interval_n", "expected"),
        [
            (1, "Weekly (Thursdays)"),
            (2, "Every 2 weeks (Thursdays)"),
        ],
    )
    def test_a_week_rule_names_its_weekday(self, interval_n, expected):
        """The ``WEEK`` unit's phase IS its weekday.

        No pattern resolves to this unit -- plan step R8 is its first author --
        so nothing but the derivation makes it read correctly.  2026-04-23 is
        a Thursday.
        """
        resolved = resolved_value(
            unit=RecurrenceUnitEnum.WEEK,
            anchor_date=date(2026, 4, 23),
            interval_n=interval_n,
        )

        assert describe(resolved).cadence == expected


class TestTheDayItNames:
    """The day comes from ``ResolvedRecurrence.day_of_month``, not the anchor."""

    def test_a_month_end_rule_names_the_day_it_MEANS(self):
        """A day-31 rule anchored in a 30-day month still reads "day 31".

        The anchor CLAMPS -- April has no 31st -- and ``nominal_day`` is what
        carries the day the user actually stated.  Reading ``anchor_date.day``
        instead would show "day 30" for a rule that fires on the last day of
        every month, and the cell and the grid would disagree about the same
        bill.
        """
        resolved = resolved_value(
            unit=RecurrenceUnitEnum.MONTH,
            anchor_date=date(2026, 4, 30),
            nominal_day=31,
        )

        assert describe(resolved).cadence == "Monthly (day 31)"

    def test_a_yearly_month_end_rule_names_it_too(self):
        """The same clamp, on the unit where it also names a month.

        2027-02-28 with a nominal 29 is a leap-day rule anchored in a non-leap
        year -- the shape ledger row R-R3 names.
        """
        resolved = resolved_value(
            unit=RecurrenceUnitEnum.YEAR,
            anchor_date=date(2027, 2, 28),
            nominal_day=29,
        )

        assert describe(resolved).cadence == "Yearly (Feb 29)"

    def test_an_unclamped_rule_reads_its_anchors_own_day(self):
        """With no ``nominal_day`` the anchor holds the day itself."""
        resolved = resolved_value(
            unit=RecurrenceUnitEnum.MONTH,
            anchor_date=date(2026, 4, 22),
        )

        assert describe(resolved).cadence == "Monthly (day 22)"


class TestThePlacementNote:
    """A deferring placement is named; the containing one adds nothing."""

    def test_the_first_paycheck_of_the_month_needs_no_day(self):
        """``Monthly (first paycheck)`` -- the phrase already implies the 1st.

        "The first paycheck on or after the 1st of the month" IS "the month's
        first paycheck", so naming ``day 1`` beside it would state the
        mechanism twice.  This is today's ``Monthly First`` pattern, and the
        copy is unchanged from the branch it replaced.
        """
        resolved = resolved_value(
            unit=RecurrenceUnitEnum.MONTH,
            anchor_date=date(2026, 3, 1),
            placement=_DEFER,
        )

        assert describe(resolved).cadence == "Monthly (first paycheck)"

    def test_any_other_deferring_day_IS_stated(self):
        """A deferring rule on the 15th funds from the first paycheck after it.

        Two different facts, so both are named.  Nothing can author this pair
        before plan step R7b; without the case the shortcut above would silently
        swallow the day the moment it can.
        """
        resolved = resolved_value(
            unit=RecurrenceUnitEnum.MONTH,
            anchor_date=date(2026, 3, 15),
            placement=_DEFER,
        )

        assert describe(resolved).cadence == "Monthly (day 15, first paycheck)"

    def test_a_deferring_multi_month_rule_states_both(self):
        """The shortcut is scoped to the every-month cadence, not to the note."""
        resolved = resolved_value(
            unit=RecurrenceUnitEnum.MONTH,
            anchor_date=date(2026, 3, 1),
            interval_n=3,
            placement=_DEFER,
        )

        assert describe(resolved).cadence == "Quarterly (Mar 1, first paycheck)"

    def test_a_containing_placement_adds_nothing(self):
        """The occurrence falls inside the paycheck that funds it.

        The coordinate already answers "when does the money move", so a note
        would repeat it.  Paired with the case above, this is what proves the
        note is driven by the placement rather than appended always.
        """
        resolved = resolved_value(
            unit=RecurrenceUnitEnum.MONTH,
            anchor_date=date(2026, 3, 1),
            placement=_CONTAIN,
        )

        assert describe(resolved).cadence == "Monthly (day 1)"

    def test_the_period_unit_never_carries_the_note(self):
        """Placement is INERT under ``PERIOD``, so naming it would be noise.

        Every occurrence such a rule emits is a period's own ``start_date``,
        and both placements carry that date back to the same period.
        """
        resolved = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD,
            anchor_date=date(2026, 3, 26),
            placement=_DEFER,
        )

        assert describe(resolved).cadence == "Every paycheck"


class TestTheBounds:
    """Every shape of the closing bound reaches the value; none is dropped."""

    def test_a_date_bound_is_carried_as_a_date(self):
        """The cell reads a ``date``, so it formats it like any other."""
        resolved = resolved_value(
            unit=RecurrenceUnitEnum.MONTH,
            anchor_date=date(2026, 4, 22),
            end_bound=EndsOnDate(on=date(2029, 1, 22)),
        )

        description = describe(resolved)

        # A LITERAL, not a re-derivation: asserting against ``f"{d:%b}"``
        # would check the locale-safe producer against the ``strftime`` it
        # exists to avoid, and could not fail for the reason it was written.
        assert description.stops == "until Jan 22, 2029"
        # The bound is NOT folded into the cadence phrase: the cell styles it
        # as a separate muted line, and one phrase carrying both could not be.
        assert description.cadence == "Monthly (day 22)"

    def test_a_count_bound_is_carried_too(self):
        """A rule that stops after twelve occurrences must not read forever.

        A cell saying a commitment repeats indefinitely when the rule says it
        stops is the surface lying about money still to be spent.
        """
        resolved = resolved_value(
            unit=RecurrenceUnitEnum.MONTH,
            anchor_date=date(2026, 4, 22),
            end_bound=EndsAfterOccurrences(count=12),
        )

        description = describe(resolved)

        assert description.stops == "for 12 occurrences"

    def test_an_unbounded_rule_carries_neither(self):
        """The ordinary case: indefinite, so both column reads are absent."""
        resolved = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, anchor_date=date(2026, 3, 26),
        )

        assert describe(resolved) == RecurrenceDescription(
            cadence="Every paycheck", stops=None,
        )


class TestItRefusesWhatItCannotWord:
    """A partial function over an enum is the defect this redesign removes."""

    def test_a_unit_with_no_wording_raises(self):
        """A member added to the enum without a phrase must fail loudly.

        Returning a placeholder would put a plausible-looking wrong label on a
        financial surface, which is worse than an error.  The value is
        hand-built with a non-member because every real member IS worded.
        """
        not_a_unit = ResolvedRecurrence(
            offset_periods=0,
            interval_n=1,
            unit="every blue moon",
            anchor_date=date(2026, 4, 22),
            placement=_CONTAIN,
            shift=BusinessDayShiftEnum.NONE,
            end_bound=NEVER_ENDS,
            nominal_day=None,
        )

        with pytest.raises(RecurrenceDescriptionError, match="has no wording"):
            describe(not_a_unit)

    def test_a_unit_with_a_stem_but_no_coordinate_shape_raises(
        self, monkeypatch,
    ):
        """The NARROWER gap, and it has its own message.

        A member could be added to the enum and given a plural noun while
        nobody decides what its occurrences are keyed on -- neither a weekday
        nor a day of the month.  Without this the phrase would read "Every 2
        fortnights ()" or omit the coordinate silently.

        Reached by adding the hypothetical member to the plural table, which
        is exactly the half-finished edit being guarded against; both raises
        are therefore shown to fire rather than one standing unreachable
        behind the other.
        """
        monkeypatch.setitem(_describe._UNIT_PLURALS, "fortnight", "fortnights")
        half_worded = ResolvedRecurrence(
            offset_periods=0,
            interval_n=2,
            unit="fortnight",
            anchor_date=date(2026, 4, 22),
            placement=_CONTAIN,
            shift=BusinessDayShiftEnum.NONE,
            end_bound=NEVER_ENDS,
            nominal_day=None,
        )

        with pytest.raises(
            RecurrenceDescriptionError, match="no coordinate shape",
        ):
            describe(half_worded)

    def test_a_placement_with_no_wording_raises(self):
        """Plan step R8 adds a third placement; it must be worded, not assumed.

        Falling back to the containing-date phrasing would tell the user the
        money moves on a day it does not -- the "fund in advance" placement
        ledger row D20 names funds from an EARLIER paycheck.
        """
        unworded = ResolvedRecurrence(
            offset_periods=0,
            interval_n=1,
            unit=RecurrenceUnitEnum.MONTH,
            anchor_date=date(2026, 4, 22),
            placement="the paycheck before",
            shift=BusinessDayShiftEnum.NONE,
            end_bound=NEVER_ENDS,
            nominal_day=None,
        )

        with pytest.raises(
            RecurrenceDescriptionError, match="the paycheck before",
        ):
            describe(unworded)


class TestTheDayOfMonthAccessor:
    """``ResolvedRecurrence.day_of_month`` is the ONE reader of the pair."""

    @pytest.mark.parametrize(
        "unit", [RecurrenceUnitEnum.MONTH, RecurrenceUnitEnum.YEAR],
    )
    def test_an_unclamped_anchor_answers_its_own_day(self, unit):
        """No ``nominal_day`` means the anchor holds the day itself."""
        resolved = resolved_value(unit=unit, anchor_date=date(2026, 4, 22))

        assert resolved.day_of_month == 22

    @pytest.mark.parametrize(
        "unit", [RecurrenceUnitEnum.MONTH, RecurrenceUnitEnum.YEAR],
    )
    def test_a_clamped_anchor_answers_the_nominal_day(self, unit):
        """Presence of ``nominal_day`` means the anchor month clamped it."""
        resolved = resolved_value(
            unit=unit, anchor_date=date(2026, 4, 30), nominal_day=31,
        )

        assert resolved.day_of_month == 31

    @pytest.mark.parametrize(
        "unit", [RecurrenceUnitEnum.PERIOD, RecurrenceUnitEnum.WEEK],
    )
    def test_a_unit_with_no_month_day_answers_none(self, unit):
        """Absence, not the anchor's day.

        A paycheck-space or weekly rule has no day-of-month to name, and
        answering ``anchor_date.day`` would invent a coordinate the cadence
        never uses -- which is what a caller reading the anchor directly would
        get.
        """
        resolved = resolved_value(unit=unit, anchor_date=date(2026, 4, 22))

        assert resolved.day_of_month is None

    def test_it_answers_the_nominal_day_even_when_it_equals_the_anchors(self):
        """``is None``, never truthiness.

        ``nominal_day``'s domain is 29-31, so no falsy value can reach it -- but
        a truthiness test here would silently re-clamp every later month if one
        ever did, and the accessor is the single place that would happen.
        """
        resolved = resolved_value(
            unit=RecurrenceUnitEnum.MONTH,
            anchor_date=date(2026, 1, 31),
            nominal_day=31,
        )

        assert resolved.day_of_month == 31


class TestTheStopLineIsTotalOverTheShapes:
    """One stop line per commitment, and a shape it cannot word RAISES.

    This class asserted a ``__post_init__`` guard until plan step R7b-3: the
    description carried ``until`` and ``after_occurrences`` as two independent
    fields and refused the pair, because
    ``ck_recurrence_rules_single_end_bound`` protects rows read from the TABLE
    and not a value built in memory.

    Two things replaced it, and a removed refusal has to be shown unreachable
    rather than merely gone.  The description carries ONE worded phrase, so
    there is no pair to police -- and the producer is TOTAL over the closed
    set, which is the half the two-field version never had: an adversarial
    review of this step measured that a fourth shape would have rendered NO
    stop line at all, and a cell showing none reads as a commitment that never
    ends.
    """

    def test_a_description_has_ONE_field_for_when_it_stops(self):
        """There is no pair of fields for a guard to police.

        The strongest form of "these two cannot both be set": there are not
        two, and the one that remains is a finished phrase rather than a
        value a surface re-projects into a pair.
        """
        fields = {field.name for field in dataclasses.fields(
            RecurrenceDescription,
        )}

        assert fields == {"cadence", "stops"}

    @pytest.mark.parametrize("kind", END_BOUND_KINDS)
    def test_every_shape_is_worded(self, kind):
        """Over the CLOSED SET, not over three shapes someone listed.

        A shape plan step R8 adds without copy fails here rather than
        rendering a blank line -- the same contract ``_stem`` and
        ``_placement_note`` hold for their own closed sets.
        """
        bound = sample_bound(kind)

        phrase = _describe._stops_phrase(bound)  # pylint: disable=protected-access

        if isinstance(bound, NeverEnds):
            assert phrase is None
        else:
            assert phrase, f"{kind.__name__} rendered no stop line"

    def test_an_unworded_shape_raises_rather_than_reading_as_indefinite(self):
        """The half-finished edit plan step R8 will make, caught loudly.

        A shape with no entry in the phrase table must not fall through to
        "no stop line": that is the surface saying a bill is charged forever
        when the rule says it stops.  Reached by declaring a hypothetical
        fourth shape, which is exactly the edit R8 makes.
        """
        class _StopsWhenTheLoanClears(EndBound):
            """A stand-in for a bound a later step adds."""

            token = "loan_cleared"

            def columns(self):
                """Return no columns -- it is not storable in these two.

                Returns:
                    Both column values as ``None``.
                """
                return EndBoundColumns(end_date=None, max_occurrences=None)

            def admits(self, *, emitted, occurrence):
                """Admit everything.

                Args:
                    emitted: Unread.
                    occurrence: Unread.

                Returns:
                    Always ``True``.
                """
                return True

            def has_closed(self, *, on, occurrences_before):
                """Never close.

                Args:
                    on: Unread.
                    occurrences_before: Unread.

                Returns:
                    Always ``False``.
                """
                return False

            @classmethod
            def from_payload(cls, *, end_date, max_occurrences):
                """Build it from nothing.

                Args:
                    end_date: Unread.
                    max_occurrences: Unread.

                Returns:
                    The shape.
                """
                return cls()

        with pytest.raises(RecurrenceDescriptionError, match="no wording"):
            _describe._stops_phrase(  # pylint: disable=protected-access
                _StopsWhenTheLoanClears(),
            )


class TestTheDeferredCollapseNamesItsPlacement:
    """The day is dropped only under the placement whose words imply it."""

    def test_a_future_advance_placement_keeps_the_day(self):
        """Plan step R8's fund-in-ADVANCE member must not inherit the collapse.

        "The first paycheck on or after the 1st" IS the month's first
        paycheck; "the LAST paycheck on or before the 1st" is the PREVIOUS
        month's last, so the two are different facts and the day is half the
        answer.  A condition keyed only on unit / interval / day would delete
        it silently the day ledger row D20's placement lands.

        Reached by wording a hypothetical third member, which is exactly the
        edit R8 will make.
        """
        # A stand-in for the placement R8 adds; wording it is all this needs.
        advance = "PERIOD_ENDING_ON_OR_BEFORE"
        resolved = ResolvedRecurrence(
            offset_periods=0,
            interval_n=1,
            unit=RecurrenceUnitEnum.MONTH,
            anchor_date=date(2026, 3, 1),
            placement=advance,
            shift=BusinessDayShiftEnum.NONE,
            end_bound=NEVER_ENDS,
            nominal_day=None,
        )

        # Today it is refused outright, which is the safe half of the
        # contract...
        with pytest.raises(RecurrenceDescriptionError, match="no wording"):
            describe(resolved)

    def test_todays_deferring_placement_still_collapses(self):
        """...and the member that DOES imply the day still drops it."""
        resolved = resolved_value(
            unit=RecurrenceUnitEnum.MONTH,
            anchor_date=date(2026, 3, 1),
            placement=_DEFER,
        )

        assert describe(resolved).cadence == "Monthly (first paycheck)"
