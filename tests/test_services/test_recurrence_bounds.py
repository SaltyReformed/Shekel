"""When a recurrence STOPS -- the closing bound's three shapes (R7b-3).

``budget.recurrence_rules`` records the bound as two nullable columns under
``ck_recurrence_rules_single_end_bound``; above the columns it is ONE value
with three shapes.  What this file holds to that:

* every shape's own answers, at exact values;
* the ROUND TRIP through the columns, which is what makes the write door's
  split and the read door's rejoin one statement rather than two that agree;
* the CLOSED SET -- that :data:`~app.services.recurrence.END_BOUND_KINDS` names
  every shape there is, so a shape added for plan step R8 and left off the
  tuple fails here rather than shipping unofferable;
* the LAZINESS of ``has_closed``'s reading argument, which is a COST property
  and not a correctness one: the unbounded shape must answer without a pay
  calendar, or ``obligations_aggregator`` would resolve a rule for every
  template on the page when 41 of the 46 live rules need nothing;
* and the HORIZON guard both bounded shapes carry, which is the correctness
  half: a walk that stopped at the schedule's end proves nothing about what
  the rule names beyond it, and reading its silence as "finished" would drop a
  live commitment out of two money totals.

Pure: no database, no clock, no app context.
"""
import dataclasses
from datetime import date

import pytest

from app.services.recurrence import (
    END_BOUND_KINDS,
    NEVER_ENDS,
    BoundReading,
    EndBound,
    EndBoundColumns,
    EndBoundInputError,
    EndsAfterOccurrences,
    EndsOnDate,
    NeverEnds,
    RecurrenceResolutionError,
    end_bound_from_columns,
    end_bound_from_token,
)

#: An arbitrary day, used wherever the specific date does not matter.
_DAY = date(2027, 3, 1)

#: One instance of each shape, so a property can be asserted over the WHOLE
#: closed set rather than over three hand-written examples.
#:
#: Keyed by class and read through :func:`sample_bound`, which FAILS for a
#: shape that has none: the shapes differ in what they are constructed from,
#: so a table is unavoidable, and the point is that adding a shape without
#: adding its sample breaks the suite loudly instead of quietly narrowing
#: every sweep below to the three that existed when they were written.
_SAMPLES: dict[type[EndBound], EndBound] = {
    NeverEnds: NEVER_ENDS,
    EndsOnDate: EndsOnDate(on=_DAY),
    EndsAfterOccurrences: EndsAfterOccurrences(count=12),
}


def sample_bound(kind: type[EndBound]) -> EndBound:
    """Return one instance of *kind*, failing when the table has none.

    Shared with ``test_recurrence_describe``, whose own totality sweep needs
    the same set.

    Args:
        kind: A member of
            :data:`~app.services.recurrence.END_BOUND_KINDS`.

    Returns:
        The sample instance.
    """
    sample = _SAMPLES.get(kind)
    assert sample is not None, (
        f"{kind.__name__} has no sample in _SAMPLES, so every sweep over "
        f"END_BOUND_KINDS in this suite silently skips it.  Add one."
    )
    return sample


def _never_called() -> BoundReading:
    """Fail if a shape asks for a reading it must answer without.

    Returns:
        Never returns.

    Raises:
        AssertionError: Always -- reaching it IS the failure.
    """
    raise AssertionError(
        "this shape resolved a rule against its owner's schedule to answer "
        "has_closed; the unbounded shape must not, or the obligations filter "
        "loads a pay calendar for every template on the page"
    )


def _reading(*occurrences, horizon=date(2099, 12, 31)):
    """Return a :class:`BoundReading` stated directly.

    Args:
        *occurrences: The dates the rule names through *horizon*, ascending.
        horizon: The last day the schedule reaches.  Defaults far enough out
            that a case not about truncation does not have to think about it.

    Returns:
        A callable yielding the reading, which is the shape ``has_closed``
        takes.
    """
    value = BoundReading(occurrences=tuple(occurrences), horizon=horizon)
    return lambda: value


class TestTheShapesAreAClosedSet:
    """The three shapes are stated once, and everything reads that statement."""

    def test_every_concrete_shape_is_listed(self):
        """A shape added and left off the tuple is unofferable, silently.

        The offer set, the schema's accepted tokens and
        :func:`~app.services.recurrence.end_bound_from_token`'s dispatch all
        read :data:`~app.services.recurrence.END_BOUND_KINDS`, so a class the
        tuple does not name can be constructed in code and never chosen by a
        user -- which is how plan step R8's own bounds would ship half-wired.

        **Scoped to shapes declared in ``app/``**, and an adversarial review
        measured why: ``__subclasses__()`` is a live interpreter-wide registry,
        ``test_recurrence_describe`` declares a stand-in shape inside a test
        body to reach the unworded-shape refusal, and a class object survives
        until the cyclic collector takes it.  Unscoped, this gate failed
        whenever the two files shared an xdist worker in that order -- a test
        failing for a reason unrelated to the code, which is broken rather than
        flaky.
        """
        declared_in_app = {
            kind for kind in EndBound.__subclasses__()
            if kind.__module__.startswith("app.")
        }

        assert declared_in_app == set(END_BOUND_KINDS)

    def test_every_shape_has_its_own_token(self):
        """Two shapes sharing a token would make one of them unreachable."""
        tokens = [kind.token for kind in END_BOUND_KINDS]

        assert sorted(tokens) == sorted(set(tokens))
        assert tokens == ["never", "on_date", "after_occurrences"]

    def test_the_base_shape_cannot_be_built(self):
        """``EndBound`` is abstract, so "a bound with no answers" has no value.

        A default on the base -- "a shape that does not recognise the question
        runs forever" -- would make a half-written shape read as an
        indefinite commitment, which on a financial surface is a bill the app
        goes on charging.
        """
        with pytest.raises(TypeError, match="abstract"):
            EndBound()  # pylint: disable=abstract-class-instantiated


class TestTheUnboundedShape:
    """``NeverEnds``: 41 of the 46 live production rules (2026-08-13)."""

    def test_it_writes_both_columns_null(self):
        """Both NULL is the exclusive arc's "no bound stated" state."""
        assert NEVER_ENDS.columns() == EndBoundColumns(
            end_date=None, max_occurrences=None,
        )

    def test_it_admits_every_occurrence(self):
        """Nothing stops the walk, at any count or any date."""
        assert NEVER_ENDS.admits(emitted=0, occurrence=date(2026, 1, 1))
        assert NEVER_ENDS.admits(emitted=10_000, occurrence=date(2099, 12, 31))

    def test_it_never_closes_and_never_walks(self):
        """An indefinite commitment is live forever, and costs no schedule.

        The one shape that answers without a reading, which is what keeps the
        obligations filter from resolving a rule per template: 41 of the 46
        live production rules are this shape (measured 2026-08-13).
        """
        assert NEVER_ENDS.has_closed(
            on=date(2099, 12, 31), reading=_never_called,
        ) is False

    def test_its_instances_are_interchangeable(self):
        """Field-less and frozen, so :data:`NEVER_ENDS` is not a special one."""
        assert NeverEnds() == NEVER_ENDS


class TestTheDateShape:
    """``EndsOnDate``: what today's "End Date" input authors."""

    def test_it_writes_the_date_column_only(self):
        """The count column stays NULL, which is the CHECK held structurally."""
        assert EndsOnDate(on=_DAY).columns() == EndBoundColumns(
            end_date=_DAY, max_occurrences=None,
        )

    def test_the_bound_date_itself_is_admitted(self):
        """Inclusive: a bill due the day the rule ends is still due.

        Defect D5's other half -- the reverse matcher bounded PERIODS, so it
        both dropped occurrences inside the window and emitted ones outside
        it.
        """
        bound = EndsOnDate(on=_DAY)

        assert bound.admits(emitted=0, occurrence=_DAY) is True
        assert bound.admits(
            emitted=0, occurrence=date(2027, 2, 28),
        ) is True
        assert bound.admits(
            emitted=0, occurrence=date(2027, 3, 2),
        ) is False

    def test_the_count_is_not_read(self):
        """A date bound stops on a date whatever the walk has emitted."""
        bound = EndsOnDate(on=_DAY)

        assert bound.admits(emitted=10_000, occurrence=_DAY) is True

    def test_a_bound_already_past_needs_no_walk(self):
        """The cheap arm: past the bound nothing can fall on or after *on*.

        Kept as a short circuit rather than folded into the walk, so the
        commonest date-bounded case -- an expired rule -- costs no schedule.
        """
        assert EndsOnDate(on=_DAY).has_closed(
            on=date(2099, 1, 1), reading=_never_called,
        ) is True

    def test_it_is_live_while_it_still_owes_an_occurrence(self):
        """An occurrence ON the day asked about is still owed."""
        assert EndsOnDate(on=_DAY).has_closed(
            on=date(2027, 2, 1), reading=_reading(date(2027, 2, 1)),
        ) is False

    def test_it_closes_once_its_last_occurrence_has_passed(self):
        """**The ruled change** (developer 2026-08-13, plan ledger row D33).

        A bound is a validity WINDOW, not a last occurrence, so a yearly bill
        that last fired in January and is bounded 1 March owes nothing from
        February onward -- and used to go on counting until March, while the
        same schedule written as "for 1 occurrence" stopped in January.  One
        reading of "is this still a commitment", so two ways of writing one
        schedule cannot disagree.
        """
        assert EndsOnDate(on=_DAY).has_closed(
            on=date(2027, 2, 1), reading=_reading(date(2027, 1, 15)),
        ) is True

    def test_a_schedule_short_of_the_bound_leaves_it_LIVE(self):
        """The guard that stops the change dropping a real obligation.

        A walk that stopped at the horizon proves nothing about what the rule
        names beyond it.  Answering "closed" there would take an owner who has
        not extended their pay schedule and remove a live commitment from
        ``/obligations`` and the emergency-fund baseline -- the more dangerous
        of the two errors, and the reason the exact reading needs a horizon at
        all.
        """
        assert EndsOnDate(on=_DAY).has_closed(
            on=date(2027, 2, 1),
            reading=_reading(date(2027, 1, 15), horizon=date(2027, 1, 20)),
        ) is False

    def test_a_blank_date_is_refused_against_its_own_field(self):
        """Choosing "on a date" and leaving it blank is a mistake, not a never.

        Reading it as indefinite would take a user who meant to STOP a
        recurring bill and silently leave it running.
        """
        with pytest.raises(EndBoundInputError) as exc_info:
            EndsOnDate.from_payload(end_date=None, max_occurrences=None)

        assert exc_info.value.field == "end_date"
        assert "date this stops" in exc_info.value.message

    def test_a_submitted_date_builds_the_shape(self):
        """The count input is not read -- this shape's own input is the date."""
        assert EndsOnDate.from_payload(
            end_date=_DAY, max_occurrences=99,
        ) == EndsOnDate(on=_DAY)


class TestTheCountShape:
    """``EndsAfterOccurrences``: given its first author at plan step R7b-3."""

    def test_it_writes_the_count_column_only(self):
        """The date column stays NULL, which is the CHECK held structurally."""
        assert EndsAfterOccurrences(count=12).columns() == EndBoundColumns(
            end_date=None, max_occurrences=12,
        )

    def test_it_admits_exactly_its_count(self):
        """The twelfth occurrence is emitted; the thirteenth is not."""
        bound = EndsAfterOccurrences(count=12)

        assert bound.admits(emitted=0, occurrence=_DAY) is True
        assert bound.admits(emitted=11, occurrence=_DAY) is True
        assert bound.admits(emitted=12, occurrence=_DAY) is False

    def test_the_date_is_not_read(self):
        """A count bound stops after N whatever date the occurrence carries."""
        bound = EndsAfterOccurrences(count=1)

        assert bound.admits(emitted=0, occurrence=date(2099, 12, 31)) is True

    def test_a_count_below_one_is_unconstructible(self):
        """``ck_recurrence_rules_positive_max_occurrences``, held by the type.

        A frozen dataclass cannot be mutated past its own ``__post_init__``,
        so this is every path there is -- which is the difference between a
        type invariant and a refusal at the write door, where the offending
        value already exists by the time anything inspects it.
        """
        for count in (0, -1):
            with pytest.raises(EndBoundInputError) as exc_info:
                EndsAfterOccurrences(count=count)
            assert exc_info.value.field == "max_occurrences"
            assert str(count) in exc_info.value.message

    def test_one_occurrence_is_a_legal_bound(self):
        """The control: the refusal is at 0, not at "small"."""
        assert EndsAfterOccurrences(count=1).max_occurrences == 1

    def test_it_closes_once_the_full_count_has_passed(self):
        """Three occurrences, all before the day asked about, means spent."""
        assert EndsAfterOccurrences(count=3).has_closed(
            on=date(2026, 4, 1),
            reading=_reading(
                date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1),
            ),
        ) is True

    def test_a_partly_spent_count_is_still_live(self):
        """Two of three have happened, so the commitment has one left."""
        assert EndsAfterOccurrences(count=3).has_closed(
            on=date(2026, 3, 1),
            reading=_reading(
                date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1),
            ),
        ) is False

    def test_an_occurrence_ON_the_day_asked_about_is_still_owed(self):
        """The boundary, and it matches the date shape's exactly.

        The two shapes have to agree here or the same schedule written two
        ways leaves the obligations total on different days -- which is the
        disagreement ruling D33 removed.
        """
        assert EndsAfterOccurrences(count=2).has_closed(
            on=date(2026, 2, 1),
            reading=_reading(date(2026, 1, 1), date(2026, 2, 1)),
        ) is False

    def test_a_schedule_short_of_the_count_leaves_it_live(self):
        """An un-extended pay schedule must not drop a live obligation.

        The count's exhaustion depends on when the paychecks fall, so a
        schedule that has not been extended far enough holds fewer than the
        count -- and answering "closed" there would silently remove a real
        commitment from ``/obligations`` and the ``/savings`` baseline.
        """
        assert EndsAfterOccurrences(count=3).has_closed(
            on=date(2026, 4, 1),
            reading=_reading(date(2026, 1, 1), date(2026, 2, 1)),
        ) is False

    def test_a_schedule_that_reaches_nothing_leaves_it_live(self):
        """The same guard at its extreme: an owner with no pay periods."""
        assert EndsAfterOccurrences(count=3).has_closed(
            on=date(2026, 4, 1), reading=_reading(horizon=None),
        ) is False

    def test_a_blank_count_is_refused_against_its_own_field(self):
        """Choosing "after N" and leaving N blank is a mistake, not a never."""
        with pytest.raises(EndBoundInputError) as exc_info:
            EndsAfterOccurrences.from_payload(
                end_date=None, max_occurrences=None,
            )

        assert exc_info.value.field == "max_occurrences"
        assert "how many times" in exc_info.value.message

    def test_a_submitted_count_builds_the_shape(self):
        """The date input is not read -- this shape's own input is the count."""
        assert EndsAfterOccurrences.from_payload(
            end_date=_DAY, max_occurrences=6,
        ) == EndsAfterOccurrences(count=6)


class TestTheColumnRoundTrip:
    """The write door's split and the read door's rejoin are one statement."""

    @pytest.mark.parametrize("kind", END_BOUND_KINDS)
    def test_no_shape_can_write_both_columns(self, kind):
        """The CENTRAL invariant, asserted over the closed set.

        ``ck_recurrence_rules_single_end_bound`` needs no door refusal because
        no shape emits both columns -- and that is what this says, for every
        shape there is rather than for three someone listed.  A shape added at
        plan step R8 whose ``columns()`` returned both would otherwise pass
        the whole suite, reach ``_author``, and die at the flush as the
        ``CheckViolation`` this module exists to make impossible.
        """
        columns = sample_bound(kind).columns()

        assert (
            columns.end_date is None or columns.max_occurrences is None
        )

    @pytest.mark.parametrize("kind", END_BOUND_KINDS)
    def test_no_shape_writes_a_pair_the_columns_cannot_name(self, kind):
        """Every shape survives the columns, so every shape is STORABLE.

        The other half: a shape emitting ``(None, None)`` writes nothing and
        reads back as "never ends", so a bounded rule would be persisted
        unbounded.  Only ``NeverEnds`` may round-trip to itself through an
        empty pair, and the round-trip below is what says so.
        """
        bound = sample_bound(kind)
        columns = bound.columns()

        assert end_bound_from_columns(
            columns.end_date, columns.max_occurrences,
        ) == bound

    @pytest.mark.parametrize("bound", [
        NEVER_ENDS,
        EndsOnDate(on=_DAY),
        EndsAfterOccurrences(count=12),
    ])
    def test_every_shape_survives_the_columns(self, bound):
        """Author it, store it, read it back: the same bound.

        A round trip that lost a shape would be a rule that stops reading as
        one that does not, which is the whole reason the split is confined to
        two functions.
        """
        columns = bound.columns()

        assert end_bound_from_columns(
            columns.end_date, columns.max_occurrences,
        ) == bound

    def test_both_columns_null_is_the_unbounded_shape(self):
        """Absence is the discriminator (ruling R-R13), not a missing value."""
        assert end_bound_from_columns(None, None) == NEVER_ENDS

    def test_a_row_holding_both_bounds_is_refused(self):
        """Two answers to "when does this stop", so neither is picked.

        ``ck_recurrence_rules_single_end_bound`` refuses the pair in the
        table, so a row reaching here was written around the constraint.
        Choosing one would keep charging a bill past a stop the user set.
        """
        with pytest.raises(RecurrenceResolutionError) as exc_info:
            end_bound_from_columns(_DAY, 12)

        assert "two closing bounds" in str(exc_info.value)
        assert "2027-03-01" in str(exc_info.value)
        assert "12" in str(exc_info.value)

    def test_a_stored_count_below_one_is_a_broken_invariant(self):
        """The same value the FORM refuses, retyped for the storage side.

        The rule is stated once -- ``EndsAfterOccurrences.__post_init__``'s --
        and only its meaning changes with the side of the door it is read
        from: a user's 0 is a mistake to report, a column's 0 is a row written
        around ``ck_recurrence_rules_positive_max_occurrences``.
        """
        with pytest.raises(RecurrenceResolutionError) as exc_info:
            end_bound_from_columns(None, 0)

        assert "names no occurrence" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, EndBoundInputError)


class TestTheFormTokenDispatch:
    """A submission names a shape; the shape fills itself in."""

    @pytest.mark.parametrize("token, payload, expected", [
        ("never", {}, NEVER_ENDS),
        ("on_date", {"end_date": _DAY}, EndsOnDate(on=_DAY)),
        (
            "after_occurrences",
            {"max_occurrences": 12},
            EndsAfterOccurrences(count=12),
        ),
    ])
    def test_each_token_builds_its_shape(self, token, payload, expected):
        """Every token in the closed set reaches the shape that owns it."""
        assert end_bound_from_token(
            token,
            end_date=payload.get("end_date"),
            max_occurrences=payload.get("max_occurrences"),
        ) == expected

    def test_every_listed_shape_is_reachable_by_its_own_token(self):
        """The dispatch table and the offer set cannot come apart.

        Read together with
        :meth:`TestTheShapesAreAClosedSet.test_every_concrete_shape_is_listed`,
        this says a shape is offerable, submittable and constructible or none
        of the three.
        """
        for kind in END_BOUND_KINDS:
            built = end_bound_from_token(
                kind.token, end_date=_DAY, max_occurrences=12,
            )
            assert isinstance(built, kind)

    def test_an_unknown_token_is_refused_against_the_mode_control(self):
        """A hand-assembled POST naming no shape has nothing to save.

        The dispatch IS the validation: a ``OneOf`` beside it would be a
        second statement of which shapes exist, able to accept what this
        cannot build.
        """
        with pytest.raises(EndBoundInputError) as exc_info:
            end_bound_from_token(
                "whenever", end_date=_DAY, max_occurrences=None,
            )

        assert exc_info.value.field == "recurrence_end_mode"

    def test_the_missing_input_refusal_names_the_control_not_the_mode(self):
        """The user answered the mode correctly; the payload is what is blank."""
        with pytest.raises(EndBoundInputError) as exc_info:
            end_bound_from_token(
                "on_date", end_date=None, max_occurrences=None,
            )

        assert exc_info.value.field == "end_date"


class TestTheShapesAreValues:
    """Frozen, comparable records -- an authored bound is not mutable state."""

    @pytest.mark.parametrize("kind", END_BOUND_KINDS)
    def test_no_shape_can_be_mutated_after_construction(self, kind):
        """What makes a shape's own refusals cover every path, not the first.

        ``EndsAfterOccurrences.__post_init__`` is only the whole story if the
        value cannot be edited afterwards; a settable ``count`` would let a 0
        in behind it.  Asserted over EVERY shape, and on each one's OWN field
        -- setting a name a shape does not have would prove only that frozen
        dataclasses reject arbitrary attributes, which is not the claim.
        """
        bound = sample_bound(kind)
        own_fields = [field.name for field in dataclasses.fields(bound)]
        if not own_fields:
            pytest.skip(f"{kind.__name__} carries no value to mutate")

        for name in own_fields:
            with pytest.raises(dataclasses.FrozenInstanceError):
                setattr(bound, name, None)
