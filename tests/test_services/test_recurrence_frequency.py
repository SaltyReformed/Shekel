"""How often a recurrence fires, with no schedule (plan step R7a-2b).

``resolve()`` answers what a recurrence MEANS against one owner's pay
calendar, and most of that answer needs no calendar: the interval, the unit and
the placement are what the rule itself states.  This module covers that
schedule-free half.

**Written where the old shapes could not answer.**  The seven-branch switch and
the three-member ``frozenset`` this replaced were both correct for the cadences
someone had listed, so a test over those cadences alone cannot tell the
derivation from the enumeration.  Every case here either uses a cadence the
closed pattern set could not have named, varies the OWNER, or pins a boundary.

**Two classes LEFT at plan step R7c-c**, with the seam they covered:
``TestCadenceOfReadsAPatternWithNoSchedule`` (``cadence_of`` took a pattern id;
it takes a RULE now and its cases live in ``test_recurrence_reading``) and
``TestTheStorageEncodingRoundTrips`` (``encode_cadence`` / ``decode_pattern``
were inverses of each other and are both deleted).  What is here instead is
what the freed vocabulary owes: the OFFER SET is exactly the set the app can
HONOUR (plan step R8-a; it was "the resolvable set" while ``anchor_family``
gated it), the placement really is inert where the offer set says it is, and
one rhythm has one spelling.

Clock discipline (``.claude/rules/testing.md``): nothing here reads a clock.
"""

from decimal import Decimal

import pytest

from app.enums import (
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.services.pay_calendar import PayCadence
from app.services.recurrence import (
    AuthorableCadence,
    Cadence,
    RecurrenceFrequencyError,
    RecurrenceResolutionError,
    authorable_cadences,
    canonical_cadence,
    emits_period_starts,
    fires_on_day_of_month,
    has_day_of_month_coordinate,
    is_authorable,
)
# The row-date rule is package-internal -- ``__init__`` re-exports the two
# predicates a consumer outside the package asks and not this one, because it
# states a TRANSITIONAL limit plan step R5 deletes.  Naming its definition site
# is what lets the offer-set sweep below grade the SET against the thing that
# withholds, rather than against a second list.
from app.services.recurrence._frequency import (
    has_row_date_coordinate,
    require_row_date_coordinate,
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


class TestTheOfferSetIsWhatTheAppCanHonour:
    """What a form may offer is what the app can carry out (plan step R8-a).

    **The property plan step R7b-2 gave the picker, re-based twice.**  While a
    cadence had to have a NAME the binding constraint was storage, and the
    offer set was the ENCODER's table inverted; R7c-c made every reading
    storable and left the gate on ``anchor_family``, which asked whether a
    FIRST OCCURRENCE could be derived -- a question ruling **R-R16** had
    already answered for every unit by making that date authored.  R8-a
    replaced the router with the two live rules below.  These cases grade the
    set against those rules rather than against a list written here, which is
    the difference between a derivation and an enumeration that happens to
    agree.
    """

    def test_the_offer_set_is_exactly_these_five_readings(self):
        """The offer set's EXTENSION, written out, beside the derivation.

        **This case used to assert ``has_row_date_coordinate(offer.unit)`` over
        the offer set**, which is the condition ``authorable_cadences`` filters
        ON -- a tautology whose docstring claimed it proved a row could be
        dated.  Both adversarial reviews of plan step R8-a named it.  What a
        test can honestly hold here is the EXTENSION: five readings, written
        out, so a change to either derived rule surfaces as a diff a reader has
        to justify rather than as a silently different set.

        The two sweeps below still grade the DERIVATION; this grades the
        ANSWER, and the pair is the point -- a derivation asserted only against
        itself agrees with any rule at all.
        """
        assert set(authorable_cadences()) == {
            AuthorableCadence(unit=unit, placement=placement)
            for unit, placement in [
                (RecurrenceUnitEnum.PERIOD,
                 PeriodPlacementEnum.CONTAINING_DATE),
                (RecurrenceUnitEnum.MONTH,
                 PeriodPlacementEnum.CONTAINING_DATE),
                (RecurrenceUnitEnum.MONTH,
                 PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER),
                (RecurrenceUnitEnum.YEAR,
                 PeriodPlacementEnum.CONTAINING_DATE),
                (RecurrenceUnitEnum.YEAR,
                 PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER),
            ]
        }

    def test_every_honourable_pair_is_offered_unless_it_is_inert(self):
        """And nothing honourable is silently withheld.

        The REVERSE direction, and it is the one an enumeration fails in: a
        pair the rules admit but the picker forgot is a cadence the app can run
        and the user cannot choose.  The only admitted exception is a placement
        that changes nothing
        (:func:`~app.services.recurrence.emits_period_starts`), and the case
        below is what proves that claim rather than trusting it.
        """
        honourable = set()
        for unit in RecurrenceUnitEnum:
            if not has_row_date_coordinate(unit):
                continue
            for placement in PeriodPlacementEnum:
                if emits_period_starts(unit) and (
                    placement is not PeriodPlacementEnum.CONTAINING_DATE
                ):
                    continue
                honourable.add(AuthorableCadence(unit=unit, placement=placement))

        assert set(authorable_cadences()) == honourable

    def test_the_week_unit_is_the_ONE_gap_and_it_names_its_step(self):
        """The single remaining gap, named so the step that closes it sees this.

        A weekly occurrence is neither a payday nor a day of the month, and
        ``recurrence_engine.compute_due_date`` dates a generated row from
        nothing else -- so every weekly row would carry the funding PAYDAY and
        the authored weekday would be discarded.  Plan step **R5** gives a row
        its own ``occurs_on``; :func:`has_row_date_coordinate` goes with the
        function it names, and the unit becomes offerable by that deletion.

        Asserted explicitly as well as by the sweep above, because a sweep that
        agreed with a rule that had quietly started admitting the unit would go
        green while the form began offering a cadence whose rows are misdated.
        """
        offered = set(authorable_cadences())
        withheld = {
            AuthorableCadence(unit=unit, placement=placement)
            for unit in RecurrenceUnitEnum
            for placement in PeriodPlacementEnum
            if not has_row_date_coordinate(unit)
        }

        assert withheld == {
            AuthorableCadence(
                unit=RecurrenceUnitEnum.WEEK, placement=placement,
            )
            for placement in PeriodPlacementEnum
        }
        assert not offered & withheld

    def test_the_year_unit_offers_BOTH_placements(self):
        """The one reading plan step R8-a WIDENED.

        ``anchor_family`` refused ``(YEAR, first paycheck)`` because
        ``_resolution._first_of_month_anchor`` would have seated it on "the 1st
        of the first qualifying month", firing a yearly bill in whichever month
        the owner's schedule happened to open in.  Ruling **R-R16** deleted
        that derivation at plan step R7c-b and the refusal outlived it: a
        year-scale rule fires on its own authored date and defers onto the next
        paycheck, exactly as its MONTH twin already did.

        Keyed on the YEAR unit rather than on a count of offers, so a later
        step that adds a placement does not have to edit this case to keep it
        meaningful.
        """
        year_placements = {
            offer.placement for offer in authorable_cadences()
            if offer.unit is RecurrenceUnitEnum.YEAR
        }

        assert year_placements == set(PeriodPlacementEnum)

    def test_month_and_year_are_authorable_on_the_SAME_placements(self):
        """The property ``canonical_cadence`` rests on, proven not assumed.

        That function rewrites ``(12k, MONTH)`` to ``(k, YEAR)`` with no
        placement guard from plan step R8-a, and the guard it dropped existed
        because the two units were authorable on DIFFERENT placement sets.
        They cannot be now -- both have a day-of-month coordinate and neither
        emits period starts, so :func:`authorable_cadences`' two rules answer
        identically for them -- and asserting it here is what makes the
        substitution safe by derivation rather than by inspection.
        """
        by_unit = {}
        for offer in authorable_cadences():
            by_unit.setdefault(offer.unit, set()).add(offer.placement)

        assert by_unit[RecurrenceUnitEnum.MONTH] == (
            by_unit[RecurrenceUnitEnum.YEAR]
        )

    def test_the_row_date_rule_has_a_RAISING_twin(self):
        """A reader holding an unhonourable rule refuses rather than answers.

        **The refusal deleting the router deleted by accident** (plan step
        R8-a).  ``anchor_family`` RAISED for the ``WEEK`` unit, so
        ``scheduling_day_of_month`` inherited a refusal through
        ``fires_on_day_of_month``; stating that predicate directly made it
        answer ``False``, which ``recurrence_engine.compute_due_date`` reads as
        "date this row from its paycheck" -- so every weekly row would have
        been dated on the funding payday, silently, with the authored weekday
        discarded.  An existing migration case caught it, and this pins the
        rule at its own door.

        **Named rather than branched on the predicate**, because the branching
        form asserted the twin's own body against itself: it read
        ``if has_row_date_coordinate(unit): pass else: raises``, which is what
        the function does.  Naming ``WEEK`` is what makes this fail if the
        refused SET moves -- which is exactly what plan step **R5** will do to
        it, and the point at which both functions are deleted.
        ``test_the_week_unit_is_the_ONE_gap_and_it_names_its_step`` grades the
        same set from the offer side; the two together are what keep the
        predicate and its twin from covering different units.
        """
        for unit in RecurrenceUnitEnum:
            if unit is RecurrenceUnitEnum.WEEK:
                continue
            require_row_date_coordinate(unit, "a test")

        with pytest.raises(RecurrenceResolutionError, match="generated row"):
            require_row_date_coordinate(RecurrenceUnitEnum.WEEK, "a test")

    def test_a_row_is_dated_from_a_day_for_exactly_these_two_readings(self):
        """``fires_on_day_of_month``'s EXTENSION over the whole product.

        **This case asserted the function's own body against itself until an
        adversarial review of plan step R8-a named it.**  It read
        ``fires_on_day_of_month(u, p) is (has_day_of_month_coordinate(u) and p
        is CONTAINING_DATE)``, which after R8-a is character-for-character what
        the function computes -- a tautology whose docstring claimed to be the
        measurement that justified collapsing the anchor-family router.  It
        could not be: that measurement compared the router against this
        expression, and the router is DELETED, so nothing in the suite can
        re-derive it.  It is recorded where a retired measurement belongs, in
        ``historical/recurrence_r8a_as_built_2026-08-16.md``.

        What a live case can hold is the extension, written out.  It is the
        answer that decides whether a generated row is dated from the cadence
        or from its paycheck (``_reading.scheduling_day_of_month``), so a
        change to it moves DATES -- and writing the pairs down is what makes
        such a change arrive as a diff rather than as a quietly different set.
        """
        dated_from_a_day = {
            (unit, placement)
            for unit in RecurrenceUnitEnum
            for placement in PeriodPlacementEnum
            if fires_on_day_of_month(unit, placement)
        }

        assert dated_from_a_day == {
            (RecurrenceUnitEnum.MONTH, PeriodPlacementEnum.CONTAINING_DATE),
            (RecurrenceUnitEnum.YEAR, PeriodPlacementEnum.CONTAINING_DATE),
        }
        # And the two facts it is composed of really are different questions:
        # every DEFERRING reading of a month-spanning unit has a day-of-month
        # coordinate and is still dated from its paycheck.  That divergence is
        # the one plan step R7c-b's wrong-money defect turned on.
        for unit in (RecurrenceUnitEnum.MONTH, RecurrenceUnitEnum.YEAR):
            assert has_day_of_month_coordinate(unit)
            assert not fires_on_day_of_month(
                unit, PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            )

    def test_the_month_unit_offers_BOTH_placements(self):
        """Plan ledger row **D32**'s defect ceasing to exist.

        The closed set stored "every 1 month funded from the first paycheck"
        and had no quarterly or semi-annual twin, so a placement was a property
        of the ``(unit, interval)`` PAIR: raising a Monthly First rule's
        interval silently reassigned the funding choice and hid the row.  The
        offer set names pairs rather than triples now, so the MONTH unit admits
        both placements at every interval and there is no such reassignment
        left to notice.
        """
        month_placements = {
            offer.placement for offer in authorable_cadences()
            if offer.unit is RecurrenceUnitEnum.MONTH
        }

        assert month_placements == set(PeriodPlacementEnum)

    @pytest.mark.parametrize("interval_n", [1, 2, 3, 6, 7, 12, 500])
    def test_is_authorable_agrees_with_the_offer_set_at_every_interval(
        self, interval_n,
    ):
        """The validator and the form cannot disagree about the set.

        Swept over intervals the closed set could name and intervals it could
        not, because the whole content of freeing the interval is that the
        answer no longer depends on it.
        """
        for unit in RecurrenceUnitEnum:
            for placement in PeriodPlacementEnum:
                offered = AuthorableCadence(
                    unit=unit, placement=placement,
                ) in set(authorable_cadences())

                assert is_authorable(interval_n, unit, placement) is offered, (
                    f"{interval_n} {unit} {placement}"
                )

    @pytest.mark.parametrize("interval_n", [0, -1, -12])
    def test_is_authorable_refuses_a_non_positive_interval(self, interval_n):
        """The one thing about a cadence the interval still decides.

        A negative control for the sweep above: without it that sweep passes
        against an ``is_authorable`` that ignores the interval entirely, which
        is exactly what freeing it invites.
        """
        for offer in authorable_cadences():
            assert not is_authorable(
                interval_n, offer.unit, offer.placement,
            )


class TestThePlacementIsReallyInertForPaychecks:
    """``emits_period_starts`` is a claim about the WALK, proven over one.

    The offer set withholds the pay-period unit's second placement on the
    ground that both carry a payday back to its own paycheck.  That is an
    argument in a docstring until something drives it: a wrong answer here
    would mean the form hides a control that DOES decide which paycheck pays a
    bill, which is money.
    """

    def test_both_placements_place_every_payday_identically(self, app):
        """Over a whole schedule, not a sampled date.

        Every payday the shared calendar holds, under both placements: the
        pairs are equal or the unit's placement is not inert and the offer set
        is withholding a real choice.
        """
        # Pylint: ``import-outside-toplevel`` -- the shared calendar builder
        # lives in a sibling test module and importing it at module scope would
        # make this file's collection depend on that one's.
        # pylint: disable=import-outside-toplevel
        from app.services.recurrence import place
        from tests.test_services.test_recurrence_resolution import (
            build_calendar,
        )

        with app.app_context():
            calendar = build_calendar()
            assert calendar.periods, "the shared schedule must hold periods"

            for period in calendar.periods:
                payday = period.start_date
                containing = place(
                    payday, calendar, PeriodPlacementEnum.CONTAINING_DATE,
                )
                starting = place(
                    payday, calendar,
                    PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                )

                assert containing == starting, payday

    def test_a_MID_PERIOD_date_is_where_the_two_differ(self, app):
        """The negative control, SHOWN to fire.

        Without it the case above passes against a ``place`` that ignored the
        placement entirely -- which would make every "Funded from" choice on
        the form inert rather than one unit's.  A date strictly inside a
        paycheck is the shape the two placements exist to tell apart.
        """
        # pylint: disable=import-outside-toplevel
        from datetime import timedelta

        from app.services.recurrence import place
        from tests.test_services.test_recurrence_resolution import (
            build_calendar,
        )

        with app.app_context():
            calendar = build_calendar()
            mid = calendar.periods[0].start_date + timedelta(days=1)

            containing = place(
                mid, calendar, PeriodPlacementEnum.CONTAINING_DATE,
            )
            starting = place(
                mid, calendar,
                PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            )

            assert containing != starting
            assert emits_period_starts(RecurrenceUnitEnum.PERIOD)
            assert not emits_period_starts(RecurrenceUnitEnum.MONTH)


class TestOneRhythmHasOneSpelling:
    """Ruling **R-R17**, applied at the door (plan step R7c-c).

    Freeing the interval makes ``(12, MONTH)`` authorable, and it is the same
    rhythm as ``(1, YEAR)``: same month stride, same day-of-month coordinate,
    same yearly
    count.  Two spellings is the second vocabulary this arc removed from the
    table arriving back through the form -- the Recurring surface would word
    one annual bill "Every 12 months" and another "Yearly", and the obligations
    filter would group them apart.
    """

    @pytest.mark.parametrize(
        "interval_n,expected",
        [
            (12, 1),
            (24, 2),
            (120, 10),
        ],
    )
    def test_a_whole_number_of_years_in_months_becomes_years(
        self, interval_n, expected,
    ):
        """The substitution itself, at three multiples."""
        assert canonical_cadence(
            interval_n, RecurrenceUnitEnum.MONTH,
        ) == Cadence(interval_n=expected, unit=RecurrenceUnitEnum.YEAR)

    @pytest.mark.parametrize("interval_n", [1, 2, 3, 6, 11, 13, 18])
    def test_a_month_count_that_is_not_whole_years_is_left_alone(
        self, interval_n,
    ):
        """Every other month interval keeps the unit the caller stated.

        18 months is a year and a half and has no YEAR spelling; rewriting it
        to 1 or 2 years would move every occurrence after the first.
        """
        assert canonical_cadence(
            interval_n, RecurrenceUnitEnum.MONTH,
        ) == Cadence(interval_n=interval_n, unit=RecurrenceUnitEnum.MONTH)

    @pytest.mark.parametrize("placement", list(PeriodPlacementEnum))
    def test_the_substitution_holds_under_EVERY_placement(self, placement):
        """The guard that LEFT at plan step R8-a, replaced by this sweep.

        ``(12, MONTH, first paycheck)`` used to be the one cadence the
        substitution had to skip: it was authorable while its YEAR spelling was
        not, so rewriting it would have turned a storable cadence into a
        refusal at the door about to store it.  That asymmetry was
        ``anchor_family`` refusing the year-scale deferred reading on a
        derivation R7c-b had deleted; with the reading admitted the two units
        are authorable on the same placements by derivation, so the guard
        checked a state the offer set can no longer produce.

        Swept over the whole placement axis rather than dropped, because that
        is the claim the deletion rests on: the rewrite must be safe for EVERY
        placement, not merely for the one it used to skip.
        """
        canonical = canonical_cadence(12, RecurrenceUnitEnum.MONTH)

        assert canonical == Cadence(
            interval_n=1, unit=RecurrenceUnitEnum.YEAR,
        )
        assert is_authorable(12, RecurrenceUnitEnum.MONTH, placement)
        assert is_authorable(
            canonical.interval_n, canonical.unit, placement,
        )
        assert fires_on_day_of_month(RecurrenceUnitEnum.MONTH, placement) is (
            fires_on_day_of_month(canonical.unit, placement)
        )

    @pytest.mark.parametrize(
        "unit", [RecurrenceUnitEnum.PERIOD, RecurrenceUnitEnum.YEAR],
    )
    def test_no_other_unit_is_touched(self, unit):
        """Twelve PAYCHECKS is not a year, and twelve years is not anything.

        The substitution reads a MONTH span, so a unit measured in paychecks
        has none and a unit already coarse has nothing coarser to become.
        """
        assert canonical_cadence(12, unit) == Cadence(
            interval_n=12, unit=unit,
        )

    def test_it_is_idempotent(self):
        """Canonicalising twice is canonicalising once.

        ``resolve`` runs it on every read as well as every write, so a
        non-idempotent rule would re-spell a stored cadence on each pass.
        """
        once = canonical_cadence(24, RecurrenceUnitEnum.MONTH)
        twice = canonical_cadence(once.interval_n, once.unit)

        assert once == twice

    @pytest.mark.parametrize("interval_n", [12, 24, 120])
    def test_both_spellings_fire_the_same_number_of_times_a_year(
        self, interval_n,
    ):
        """The substitution is behaviour-preserving, measured not asserted.

        If the two spellings did not name one rhythm, canonicalising would
        change what a rule COSTS per month on ``/obligations`` -- so the yearly
        rate is the honest thing to compare.
        """
        months = Cadence(
            interval_n=interval_n, unit=RecurrenceUnitEnum.MONTH,
        )
        years = canonical_cadence(interval_n, RecurrenceUnitEnum.MONTH)

        assert years.occurrences_per_year(_BIWEEKLY) == (
            months.occurrences_per_year(_BIWEEKLY)
        )
