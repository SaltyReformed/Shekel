"""
Shekel Budget App -- The recurrence vocabulary (plan steps R2e-2, R7b-2)

``ref.recurrence_patterns`` is a table; ``RecurrencePatternEnum`` is what the
application can actually resolve.  Every recurrence surface used to read the
TABLE -- the picker offered its rows, both write doors accepted them, and the
preview previewed them -- while ``resolve`` raises for any id no enum member
names and no route catches that.  The gap between the two sets is a 500 waiting
for them to diverge, and plan step R2e-3 is where they diverge on purpose: the
``Once`` enum member goes while its ``ref`` row survives to R9, because deleting
the row in the same release would leave the auto-rollback image unable to boot
(ruling R-R11).

**Plan step R7b-2 moved where that gap can be reached.**  A form no longer posts
a pattern id at all: it authors ``(interval_n, unit, placement)`` and the write
door encodes it, so the membership question the doors ask is now asked on the
two AUTHORED axes -- ``modelled_unit`` and ``modelled_placement`` -- while
``modelled_pattern`` is left with the one caller that still reads a STORED
pattern, the edit form deciding whether it can preselect anything.

The load-bearing tests are the two in
:class:`TestAnUnmodelledRowIsInvisible` that ask whether the offer set is
driven by a ``ref`` TABLE -- a surplus ``recurrence_units`` row, and the
``WEEK`` member that really exists and has no pattern to be stored as.  An
adversarial review of plan step R7b-2 is why they are asked on the UNIT table:
the version asked on the PATTERN table could not fail, because since R7b-2
``_picker`` does not read that table or import its model at all.
"""
from app import ref_cache
from app.enums import (
    PeriodPlacementEnum,
    RecurrencePatternEnum,
    RecurrenceUnitEnum,
)
from app.extensions import db
from app.models.ref import RecurrencePattern, RecurrenceUnit
from app.services.recurrence import (
    UNAVAILABLE_PATTERN_MESSAGE,
    cadence_options,
    modelled_pattern,
    modelled_placement,
    modelled_unit,
)


def _insert_unmodelled_pattern(name="Every Blue Moon"):
    """Insert a ``ref.recurrence_patterns`` row no enum member names.

    The post-R2e-3 state, reachable today only by hand: ``ref_cache.init``
    requires every ENUM member to have a row, and does not forbid the reverse.

    Args:
        name: The row's name; must not collide with an enum member's value.

    Returns:
        int: The new row's primary key.
    """
    assert name not in {m.value for m in RecurrencePatternEnum}
    row = RecurrencePattern(name=name)
    db.session.add(row)
    db.session.flush()
    return row.id


class TestModelledPattern:
    """The membership answer the edit form asks of a STORED pattern."""

    def test_every_member_round_trips_through_its_id(self, app):
        """``id -> member`` inverts ``ref_cache.recurrence_pattern_id``."""
        with app.app_context():
            for member in RecurrencePatternEnum:
                pattern_id = ref_cache.recurrence_pattern_id(member)
                assert modelled_pattern(pattern_id) is member

    def test_an_id_that_is_no_row_at_all_is_none(self, app):
        """A fabricated id answers ``None`` rather than raising.

        The door that asks is reading a row it is about to let the user
        repair; an unknown id is a warning and a blank control, not a 500.
        """
        with app.app_context():
            assert modelled_pattern(99_999_999) is None


class TestModelledUnit:
    """The membership answer for the first AUTHORED axis (plan step R7b-2)."""

    def test_every_member_round_trips_through_its_id(self, app):
        """``id -> member`` inverts ``ref_cache.recurrence_unit_id``."""
        with app.app_context():
            for member in RecurrenceUnitEnum:
                assert modelled_unit(
                    ref_cache.recurrence_unit_id(member),
                ) is member

    def test_an_id_that_is_no_row_at_all_is_none(self, app):
        """A fabricated id answers ``None``.

        This one IS submission-facing -- it is what the schema field asks of a
        posted ``recurrence_unit`` -- so the answer has to be refusable as a
        field error rather than raised.
        """
        with app.app_context():
            assert modelled_unit(99_999_999) is None


class TestModelledPlacement:
    """The membership answer for the second authored axis."""

    def test_every_member_round_trips_through_its_id(self, app):
        """``id -> member`` inverts ``ref_cache.period_placement_id``."""
        with app.app_context():
            for member in PeriodPlacementEnum:
                assert modelled_placement(
                    ref_cache.period_placement_id(member),
                ) is member

    def test_an_id_that_is_no_row_at_all_is_none(self, app):
        """A fabricated id answers ``None``."""
        with app.app_context():
            assert modelled_placement(99_999_999) is None


class TestAnUnmodelledRowIsInvisible:
    """A ``ref`` row the enum does not name reaches no recurrence surface.

    This is the property plan step R2e-3 depends on.  It keeps the ``Once``
    row so the previous image can still boot, and this class is why keeping it
    costs nothing: the row is unreachable, not merely unoffered.
    """

    def test_a_surplus_UNIT_row_does_not_move_the_offer_set(self, app):
        """The picker's options are unchanged by a new ``ref.recurrence_units`` row.

        **This test used to insert a surplus PATTERN row, and an adversarial
        review of plan step R7b-2 showed that could not fail.**  Before plan
        step R2e-2 the picker was
        ``db.session.query(RecurrencePattern).all()``, so a surplus pattern row
        would have been offered and the assertion was load-bearing.  Since
        R7b-2 the options are derived from ``PATTERN_DERIVATIONS``, an
        ENUM-keyed table, and ``_picker`` does not import the pattern model at
        all -- so inserting into that table could not move the answer whatever
        the producer did.

        The UNIT table is where the same mistake is now available: a picker
        that offered ``db.session.query(RecurrenceUnit).all()`` would offer
        ``WEEK`` -- which the closed set cannot store -- and this row besides.
        Comparing the whole tuple rather than a count is what catches a row
        that changes only a LABEL.
        """
        with app.app_context():
            before = cadence_options()

            surplus = RecurrenceUnit(name="Fortnight")
            db.session.add(surplus)
            db.session.flush()

            assert db.session.get(RecurrenceUnit, surplus.id) is not None
            assert cadence_options() == before

    def test_the_offer_set_omits_a_unit_the_closed_set_cannot_store(self, app):
        """``WEEK`` is a modelled unit with no pattern, so it is not offered.

        The property the surplus-row test above stands in for, asked of a
        member that really exists: ``RecurrenceUnitEnum.WEEK`` has a ``ref``
        row and an enum member, and no closed-set pattern stores it until plan
        step R8.  A table-driven offer set would offer it and
        ``encode_cadence`` would refuse the save.
        """
        with app.app_context():
            week_id = ref_cache.recurrence_unit_id(RecurrenceUnitEnum.WEEK)

            assert modelled_unit(week_id) is RecurrenceUnitEnum.WEEK
            assert week_id not in {
                option.unit_id for option in cadence_options()
            }

    def test_a_ref_row_the_enum_does_not_name_is_not_modelled(self, app):
        """The membership answer for such a row is ``None``.

        Which is what makes the edit form refuse to preselect it: the row
        EXISTS, so the ``db.session.get`` probe this replaced would have said
        yes.
        """
        with app.app_context():
            unmodelled_id = _insert_unmodelled_pattern()

            assert db.session.get(RecurrencePattern, unmodelled_id) is not None
            assert modelled_pattern(unmodelled_id) is None

    def test_every_id_an_option_carries_resolves_on_its_own_axis(self, app):
        """Each offered id names a value on the axis its field claims.

        **There is deliberately no "and none of them is a pattern id" arm.**
        The three ``ref`` tables are separate sequences that all start at 1, so
        comparing an option's ``unit_id`` against the pattern ids is either
        vacuously true or a false FAILURE depending on how far each sequence
        has run -- it measures seeding order, not the property.  What the two
        tests above establish behaviourally is the real statement: the offer
        set is not driven by any ``ref`` table, so there is no pattern id in it
        to find.
        """
        with app.app_context():
            options = cadence_options()

            assert options, "the offer set is empty"
            for option in options:
                assert modelled_unit(option.unit_id) is not None, option
                assert modelled_placement(option.placement_id) is not None, (
                    option
                )


class TestTheUnavailablePatternMessage:
    """What an edit form TELLS the user about a rule it cannot preselect."""

    def test_it_names_the_state_and_the_repair(self, app):
        """The copy has to do the whole job, because no control shows the state.

        Before plan step R7b-2 the message sat above a ``<select>`` that still
        carried the stored pattern as a trailing option, so the option itself
        said which rule was affected.  The two-axis controls render UNSET, so
        the sentence is the only thing the user has: it must say that the
        pattern is gone, that a cadence must be chosen, and that saving
        unchanged will be refused -- the last being what keeps the warning
        honest rather than advisory.
        """
        assert "no longer" in UNAVAILABLE_PATTERN_MESSAGE
        assert "Choose how often it repeats" in UNAVAILABLE_PATTERN_MESSAGE
        assert "refused" in UNAVAILABLE_PATTERN_MESSAGE
