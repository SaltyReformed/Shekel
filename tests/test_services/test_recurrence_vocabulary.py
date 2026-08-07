"""
Shekel Budget App -- The recurrence vocabulary (plan step R2e-2)

``ref.recurrence_patterns`` is a table; ``RecurrencePatternEnum`` is what the
application can actually resolve.  Every recurrence surface used to read the
TABLE -- the picker offered its rows, both write doors accepted them, and the
preview previewed them -- while ``resolve`` raises for any id no enum member
names and no route catches that.  The gap between the two sets is a 500 waiting
for them to diverge, and plan step R2e-3 is where they diverge on purpose: the
``Once`` enum member goes while its ``ref`` row survives to R9, because
deleting the row in the same release would leave the auto-rollback image unable
to boot (ruling R-R11).

These tests pin the module that closes the gap.  The load-bearing one is
:meth:`TestAnUnmodelledRowIsInvisible.test_a_ref_row_the_enum_does_not_name_is_not_offered`
-- it manufactures exactly the post-R2e-3 state (a row with no enum member) and
asserts the vocabulary does not see it.  Every other test in this file would
still pass if the producer were driven off the table.
"""
from app import ref_cache
from app.enums import RecurrencePatternEnum
from app.extensions import db
from app.models.ref import RecurrencePattern
from app.services.recurrence import (
    UNAVAILABLE_PATTERN_LABEL,
    PatternChoice,
    modelled_pattern,
    pattern_choices,
    pattern_choices_for,
    pattern_labels_by_name,
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


class TestPatternChoices:
    """The picker's options come from the enum, in a fixed order."""

    def test_one_choice_per_modelled_pattern_in_declaration_order(self, app):
        """Every enum member appears exactly once, in declaration order.

        The order is what the user sees, so it is asserted as a sequence
        rather than as a set: an unordered ``SELECT`` (what this replaced)
        can reorder a dropdown between two deploys with no code change.
        """
        with app.app_context():
            choices = pattern_choices()

            assert [c.pattern_id for c in choices] == [
                ref_cache.recurrence_pattern_id(member)
                for member in RecurrencePatternEnum
            ]

    def test_every_choice_is_a_pattern_choice_with_a_non_empty_label(self, app):
        """Each option carries a renderable label, so no option renders blank.

        The template no longer has a fallback expression -- it prints
        ``choice.label`` verbatim -- so a missing label would ship an empty
        ``<option>`` rather than degrade to the pattern's raw name.
        """
        with app.app_context():
            choices = pattern_choices()

            assert len(choices) == len(list(RecurrencePatternEnum))
            for choice in choices:
                assert isinstance(choice, PatternChoice)
                assert choice.label.strip()

    def test_the_labels_are_the_copy_the_picker_has_always_shown(self, app):
        """The seven labels are pinned verbatim (no silent copy change).

        R2e-2 moved this table out of the ``inject_recurrence_labels`` context
        processor; the move must not have edited a word of it.  R2e-3 removed
        the eighth entry, "One-time", with the ``Once`` member -- "does not
        repeat" is the form's own empty option, not a pattern -- and must not
        have edited the remaining seven either.
        """
        with app.app_context():
            by_id = {c.pattern_id: c.label for c in pattern_choices()}

            expected = {
                RecurrencePatternEnum.EVERY_PERIOD: "Every paycheck",
                RecurrencePatternEnum.EVERY_N_PERIODS: "Every N paychecks",
                RecurrencePatternEnum.MONTHLY: "Monthly (specific day)",
                RecurrencePatternEnum.MONTHLY_FIRST: (
                    "Monthly (first paycheck of month)"
                ),
                RecurrencePatternEnum.QUARTERLY: "Quarterly",
                RecurrencePatternEnum.SEMI_ANNUAL: "Every 6 months",
                RecurrencePatternEnum.ANNUAL: "Yearly",
            }
            assert len(by_id) == len(expected)
            for member, label in expected.items():
                assert by_id[ref_cache.recurrence_pattern_id(member)] == label

    def test_the_ids_are_the_ref_rows_the_names_belong_to(self, app):
        """Each choice's id is the ``ref`` row whose name is the enum value.

        Proves the enum-driven producer did not merely invent a stable
        ordering: the ids it emits are the same rows the form posts back and
        ``budget.recurrence_rules.pattern_id`` stores.
        """
        with app.app_context():
            names_by_id = {
                row.id: row.name
                for row in db.session.query(RecurrencePattern).all()
            }
            emitted = [c.pattern_id for c in pattern_choices()]

            assert [names_by_id[pid] for pid in emitted] == [
                member.value for member in RecurrencePatternEnum
            ]


class TestModelledPattern:
    """The membership answer every recurrence door asks."""

    def test_every_member_round_trips_through_its_id(self, app):
        """``id -> member`` inverts ``ref_cache.recurrence_pattern_id``."""
        with app.app_context():
            for member in RecurrencePatternEnum:
                pattern_id = ref_cache.recurrence_pattern_id(member)
                assert modelled_pattern(pattern_id) is member

    def test_an_id_that_is_no_row_at_all_is_none(self, app):
        """A fabricated id answers ``None`` rather than raising.

        The doors that ask are reading user input; an unknown id is a flash,
        not a 500.
        """
        with app.app_context():
            assert modelled_pattern(99_999_999) is None


class TestAnUnmodelledRowIsInvisible:
    """A ``ref`` row the enum does not name reaches no recurrence surface.

    This is the property plan step R2e-3 depends on.  It keeps the ``Once``
    row so the previous image can still boot, and this class is why keeping it
    costs nothing: the row is unreachable, not merely unoffered.
    """

    def test_a_ref_row_the_enum_does_not_name_is_not_offered(self, app):
        """The picker skips a row with no enum member.

        Manufactures the post-R2e-3 state directly.  Before R2e-2 the picker
        was ``db.session.query(RecurrencePattern).all()``, so this row would
        have been rendered as a selectable option.
        """
        with app.app_context():
            unmodelled_id = _insert_unmodelled_pattern()

            offered = {c.pattern_id for c in pattern_choices()}

            assert unmodelled_id not in offered
            assert len(offered) == len(list(RecurrencePatternEnum))

    def test_a_ref_row_the_enum_does_not_name_is_not_modelled(self, app):
        """The membership answer for such a row is ``None``.

        Which is what makes the doors refuse it: the row EXISTS, so the
        ``db.session.get`` probe it replaced would have said yes.
        """
        with app.app_context():
            unmodelled_id = _insert_unmodelled_pattern()

            assert db.session.get(RecurrencePattern, unmodelled_id) is not None
            assert modelled_pattern(unmodelled_id) is None

    def test_it_carries_no_label_either(self, app):
        """The name-keyed label projection omits the unmodelled row.

        The projection is derived from the same table the picker reads, so
        there is no second list that could still name it.
        """
        with app.app_context():
            _insert_unmodelled_pattern(name="Every Blue Moon")

            assert "Every Blue Moon" not in pattern_labels_by_name()


class TestPatternChoicesFor:
    """An edit form's options carry the stored pattern even when unmodelled."""

    def test_a_modelled_stored_pattern_adds_nothing(self, app):
        """The common case is exactly :func:`pattern_choices`.

        Without this, a producer that appended an "Unavailable" entry to every
        edit form would satisfy the two tests below.
        """
        with app.app_context():
            for member in RecurrencePatternEnum:
                pattern_id = ref_cache.recurrence_pattern_id(member)
                assert pattern_choices_for(pattern_id) == pattern_choices()

    def test_no_stored_pattern_adds_nothing(self, app):
        """A create form, or a template naming no rule, gets the plain set."""
        with app.app_context():
            assert pattern_choices_for(None) == pattern_choices()

    def test_an_unmodelled_stored_pattern_is_appended_last(self, app):
        """The stored id becomes one trailing, clearly-labelled option.

        Appended rather than merged into the ordered set: it is a read-out of
        this rule's own value, not a cadence the picker proposes, and the
        modelled options must keep their order and their position.
        """
        with app.app_context():
            unmodelled_id = _insert_unmodelled_pattern()

            choices = pattern_choices_for(unmodelled_id)

            assert choices[:-1] == pattern_choices()
            assert choices[-1] == PatternChoice(
                pattern_id=unmodelled_id, label=UNAVAILABLE_PATTERN_LABEL,
            )

    def test_the_label_is_not_the_ref_rows_own_name(self, app):
        """The extra option does not put a second display path on the table.

        Reading ``pattern.name`` would show the user an internal seed string
        AND re-introduce the table-driven label lookup this step removed.
        """
        with app.app_context():
            unmodelled_id = _insert_unmodelled_pattern(name="Every Blue Moon")

            choices = pattern_choices_for(unmodelled_id)

            assert choices[-1].label == UNAVAILABLE_PATTERN_LABEL
            assert "Every Blue Moon" not in {c.label for c in choices}


class TestPatternLabelsByName:
    """The name-keyed projection is derived, not a second copy."""

    def test_its_keys_are_the_modelled_patterns_ref_names(self, app):
        """Keys equal ``member.value``, which equals the row's ``name``.

        ``ref_cache.init`` already requires that equality; asserting it
        against the live rows is what makes the projection safe for the
        ``recurrence_cell`` macro, whose only key is a persisted rule's
        ``pattern.name``.
        """
        with app.app_context():
            labels = pattern_labels_by_name()
            names_by_id = {
                row.id: row.name
                for row in db.session.query(RecurrencePattern).all()
            }

            assert set(labels) == {
                names_by_id[ref_cache.recurrence_pattern_id(member)]
                for member in RecurrencePatternEnum
            }

    def test_its_values_are_the_pickers_own_labels(self, app):
        """The two spellings of one label table agree, member for member.

        The context processor used to declare its own copy of these eight
        strings; a divergence there would have shown the user one label on the
        form and another on the Recurring list.
        """
        with app.app_context():
            labels = pattern_labels_by_name()
            by_id = {c.pattern_id: c.label for c in pattern_choices()}

            for member in RecurrencePatternEnum:
                assert (
                    labels[member.value]
                    == by_id[ref_cache.recurrence_pattern_id(member)]
                )
