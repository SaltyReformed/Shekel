"""
Shekel Budget App -- ``recurrence._closing``: what stops a definition, asked
"has it ended" (plan step R7d-e).

Plan step R7d-d gave the composed :class:`Closing` -- the bound an owner
AUTHORS and the stop a destination DERIVES -- and its shapes answered ONE
question, ``admits``: may the walk still emit this occurrence.  Plan step R7d-e
adds the second question the ``/obligations`` and ``/savings`` totals ask,
``has_closed``: has this definition stopped being a commitment by a given day.
Until then that question was asked of the authored bound alone, so a retired
loan's payment went on inflating both totals until some chokepoint happened to
rewrite the cached column it read.

**Three claims, each graded here rather than argued.**

1. Every derived shape answers ``has_closed``, and the reading is
   **R-R45**'s for the derived half too (ruling **R-R57**, developer
   2026-09-05): the definition OWES no occurrence on or after the day asked,
   not "the closing date has passed".  Where the two readings part -- a loan
   cleared MID-period -- the case below states the day each would answer.
2. ``Closing.has_closed`` ORs its two stops as ``admits`` ANDs them, and the
   reading both judge is walked AT MOST ONCE however many shapes ask.  The
   memo is graded by a counting callable, and the control is shown to fire:
   the shape pair that asks twice is named and counted.
3. The date-bound closure rule is ONE function
   (``_bounds.date_bound_has_closed``), so the authored ``EndsOnDate`` and the
   derived ``ClosesOn`` cannot drift on the day a commitment leaves the
   totals.  Graded by asking both the same questions over a sweep rather
   than by reading the source.

Pure: no database, no app context.  The reading is stated directly, the way
``test_recurrence_bounds`` states it for the authored shapes.
"""
from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from app.services.recurrence import (
    EMPTY,
    INDEFINITE,
    NEVER_ENDS,
    BoundReading,
    ClosesOn,
    Closing,
    DerivedStop,
    Empty,
    EndsAfterOccurrences,
    EndsOnDate,
    Indefinite,
)

#: The closing date the derived cases are stated against.
_CLOSES = date(2029, 2, 22)

#: A monthly installment day inside the cases: the 22nd of each month.
_INSTALLMENTS = tuple(
    date(2028, month, 22) for month in range(1, 13)
) + (date(2029, 1, 22), date(2029, 2, 22))


def _reading(*occurrences, horizon=date(2099, 12, 31)):
    """Return a :class:`BoundReading` stated directly, as a callable.

    Args:
        *occurrences: The dates the definition names through *horizon*,
            ascending.
        horizon: The last day the schedule reaches.  Defaults far enough out
            that a case not about truncation does not have to think about it.

    Returns:
        A callable yielding the reading, which is the shape ``has_closed``
        takes.
    """
    value = BoundReading(occurrences=tuple(occurrences), horizon=horizon)
    return lambda: value


def _never_called():
    """A reading callable that fails the test if a shape asks for it."""
    raise AssertionError(
        "has_closed read the walk where the shape can answer without it"
    )


class _Counting:
    """A reading callable that counts how often it is asked.

    The control for the ``Closing.has_closed`` memo: a shape pair that asks
    twice reads 2 without the memo and 1 with it, and a case that counts 1 on
    a pair that asks ONCE would pass for the wrong reason -- so the pair that
    asks twice is named and asserted.

    Attributes:
        calls: How many times the reading has been produced.
    """

    def __init__(self, value: BoundReading) -> None:
        """Hold the reading to hand out and start the count at zero.

        Args:
            value: The reading every ask returns.
        """
        self.value = value
        self.calls = 0

    def __call__(self) -> BoundReading:
        """Produce the reading and count the ask."""
        self.calls += 1
        return self.value


class TestTheDerivedShapesAnswerHasClosed:
    """Every shape answers, and the reading is R-R45's (ruling R-R57)."""

    def test_the_base_refuses_a_shape_without_the_answer(self):
        """A fourth shape that forgets ``has_closed`` is unconstructible.

        A default -- "a shape that does not recognise the question is still
        live" -- would keep charging a debt the owner has cleared, which is the
        partial-function defect this arc removes.  ``@abstractmethod`` makes
        the omission a ``TypeError`` at construction rather than a wrong figure
        at first use.
        """
        @dataclass(frozen=True)
        class HalfWritten(DerivedStop):
            """Answers ``admits`` and forgets ``has_closed``."""

            def admits(self, occurrence: date) -> bool:
                return True

        with pytest.raises(TypeError, match="has_closed"):
            # Pylint: ``abstract-class-instantiated`` -- instantiating the
            # half-written shape IS the assertion; the refusal is the subject.
            HalfWritten()  # pylint: disable=abstract-class-instantiated

    def test_indefinite_never_closes_and_never_reads_the_walk(self):
        """A stop that names no date ends nothing, on any day."""
        assert INDEFINITE.has_closed(
            on=date(2099, 12, 31), reading=_never_called,
        ) is False
        assert Indefinite().has_closed(
            on=date(2000, 1, 1), reading=_never_called,
        ) is False

    def test_empty_is_closed_on_every_day_and_never_reads_the_walk(self):
        """A window that admits nothing owes nothing, before or after anything.

        The loan cleared before its first installment: ``Empty`` is a
        precomputation of "no occurrence falls inside the window", so the
        answer cannot depend on the day asked and needs no schedule.
        """
        assert EMPTY.has_closed(
            on=date(2000, 1, 1), reading=_never_called,
        ) is True
        assert Empty().has_closed(
            on=date(2099, 12, 31), reading=_never_called,
        ) is True

    def test_closes_on_is_closed_once_its_date_has_passed_without_a_walk(self):
        """The cheap arm: past the closing date nothing can be owed."""
        assert ClosesOn(on=_CLOSES).has_closed(
            on=_CLOSES + timedelta(days=1), reading=_never_called,
        ) is True

    def test_closes_on_is_live_while_the_definition_still_owes(self):
        """An installment ON the day asked about is still owed.

        The Van's last installment: the loan closes 2029-02-22 and owes that
        day's payment, so on 2029-02-22 the commitment is live under both
        readings of "ended".
        """
        assert ClosesOn(on=_CLOSES).has_closed(
            on=_CLOSES, reading=_reading(*_INSTALLMENTS),
        ) is False

    def test_closes_on_ends_the_day_after_the_LAST_owed_occurrence(self):
        """**Ruling R-R57**: R-R45's reading, where the two readings part.

        A loan whose payment falls on the 22nd is trued to zero on
        2026-09-01, after its 2026-08-22 installment; the derived stop is
        ``ClosesOn(2026-09-01)``.  Under "the closing date has passed" the
        payment would stay in the ``/obligations`` and ``/savings`` totals
        through 2026-09-01 and leave on 2026-09-02 -- ten days of a commitment
        no occurrence backs.  Under R-R45 the definition owes nothing from
        2026-08-23, the day after its last owed installment, and that is the
        day it leaves.  Both days asserted, so the case cannot pass under the
        other reading.
        """
        cleared = date(2026, 9, 1)
        last_owed = date(2026, 8, 22)
        stop = ClosesOn(on=cleared)
        walk = _reading(date(2026, 7, 22), last_owed)

        assert stop.has_closed(on=last_owed, reading=walk) is False
        assert stop.has_closed(
            on=last_owed + timedelta(days=1), reading=walk,
        ) is True, (
            "the derived stop waited for its closing date: R-R57 makes it "
            "answer from what the definition still owes"
        )
        # And the day the OTHER reading would have chosen agrees, so the two
        # differ exactly on the ten days between.
        assert stop.has_closed(
            on=cleared + timedelta(days=1), reading=walk,
        ) is True

    def test_a_schedule_short_of_the_closing_date_leaves_it_LIVE(self):
        """The guard against dropping a live commitment on an unextended schedule.

        A walk that stopped at the horizon proves nothing about what the
        definition names beyond it; answering "closed" there would remove a
        real loan payment from two money totals because the owner had not
        extended their pay schedule.  The same guard the authored date shape
        holds, because it is the same rule.
        """
        assert ClosesOn(on=_CLOSES).has_closed(
            on=date(2028, 6, 1),
            reading=_reading(
                date(2028, 1, 22), horizon=date(2028, 3, 1),
            ),
        ) is False


class TestTheDateBoundRuleIsOneFunction:
    """``ClosesOn`` and ``EndsOnDate`` cannot drift: they ARE one rule."""

    @pytest.mark.parametrize(
        ("on", "occurrences", "horizon"),
        [
            # Past the bound: closed without a walk.
            (date(2029, 3, 1), (), date(2029, 12, 31)),
            # On the bound, still owing that day's installment.
            (_CLOSES, _INSTALLMENTS, date(2029, 12, 31)),
            # Between the last owed occurrence and the bound.
            (date(2029, 2, 1), (date(2029, 1, 22),), date(2029, 12, 31)),
            # Nothing owed, schedule short of the bound.
            (date(2029, 2, 1), (date(2029, 1, 22),), date(2029, 2, 10)),
            # Nothing owed, schedule reaching exactly the bound.
            (date(2029, 2, 1), (date(2029, 1, 22),), _CLOSES),
            # No occurrences at all, schedule past the bound.
            (date(2028, 1, 1), (), date(2029, 12, 31)),
            # No occurrences at all, no schedule.
            (date(2028, 1, 1), (), None),
        ],
    )
    def test_both_shapes_answer_alike(self, on, occurrences, horizon):
        """Same last day, same day asked, same walk -- same answer.

        Graded rather than read off the source: the two shapes live in two
        modules with two authors, and a refactor that re-spelled either rule
        would pass every single-shape case and fail here.
        """
        derived = ClosesOn(on=_CLOSES).has_closed(
            on=on, reading=_reading(*occurrences, horizon=horizon),
        )
        authored = EndsOnDate(on=_CLOSES).has_closed(
            on=on, reading=_reading(*occurrences, horizon=horizon),
        )
        assert derived is authored, (
            f"on {on}: derived answers {derived}, authored {authored}"
        )


class TestTheComposedValueAnswersForBothStops:
    """``Closing.has_closed`` ORs where ``admits`` ANDs, over one reading."""

    def test_no_stop_at_all_never_closes_and_never_walks(self):
        """The 41-of-46 case: unbounded rule, no loan behind it."""
        assert Closing(authored=NEVER_ENDS).has_closed(
            on=date(2099, 12, 31), reading=_never_called,
        ) is False

    def test_the_derived_stop_alone_ends_an_unbounded_rule(self):
        """The R-R56 shape: the app-bounded loan payment composes NEVER_ENDS.

        For the definition whose closing bound the app itself writes, the door
        supplies ``NEVER_ENDS`` as the authored half and the derived stop is
        the whole answer -- so a retired loan's payment ends on the loan's
        closing date and not on the cached column's.
        """
        closing = Closing(authored=NEVER_ENDS, derived=ClosesOn(on=_CLOSES))

        assert closing.has_closed(
            on=_CLOSES + timedelta(days=1), reading=_never_called,
        ) is True
        assert closing.has_closed(
            on=_CLOSES, reading=_reading(*_INSTALLMENTS),
        ) is False

    def test_an_empty_window_ends_an_unbounded_rule(self):
        """A loan cleared before its first installment: nothing is owed, ever."""
        assert Closing(authored=NEVER_ENDS, derived=EMPTY).has_closed(
            on=date(2000, 1, 1), reading=_never_called,
        ) is True

    def test_an_indefinite_stop_leaves_the_authored_bound_to_answer(self):
        """Negative amortization: the loan stops nothing, the owner's bound may."""
        live = Closing(authored=NEVER_ENDS, derived=INDEFINITE)
        bounded = Closing(
            authored=EndsOnDate(on=date(2027, 1, 1)), derived=INDEFINITE,
        )

        assert live.has_closed(
            on=date(2099, 1, 1), reading=_never_called,
        ) is False
        assert bounded.has_closed(
            on=date(2027, 1, 2), reading=_never_called,
        ) is True

    def test_the_authored_bound_alone_ends_a_loan_still_owing(self):
        """The owner said stop before the loan did: the owner's word binds.

        A second transfer into a loan keeps its authored bound (ruling
        **R-R56** names only the account's active payment), so a stop the
        owner authored before the payoff ends the commitment there.
        """
        closing = Closing(
            authored=EndsOnDate(on=date(2028, 6, 30)),
            derived=ClosesOn(on=_CLOSES),
        )

        assert closing.has_closed(
            on=date(2028, 7, 1), reading=_never_called,
        ) is True

    def test_the_derived_stop_alone_ends_a_rule_bounded_later(self):
        """The D35 shape inverted: the loan stops before the owner's bound does.

        The derived stop is EARLIER than the authored one, so past the closing
        date the commitment has ended although the authored bound is still
        ahead -- the case an AND would get wrong.
        """
        closing = Closing(
            authored=EndsOnDate(on=date(2030, 12, 31)),
            derived=ClosesOn(on=_CLOSES),
        )

        assert closing.has_closed(
            on=_CLOSES + timedelta(days=1), reading=_reading(*_INSTALLMENTS),
        ) is True

    def test_a_spent_count_ends_it_whatever_the_loan_says(self):
        """The count shape composes too: three installments, all fired."""
        closing = Closing(
            authored=EndsAfterOccurrences(count=3),
            derived=ClosesOn(on=_CLOSES),
        )
        walk = _reading(*_INSTALLMENTS[:3])

        assert closing.has_closed(on=_INSTALLMENTS[2], reading=walk) is False
        assert closing.has_closed(
            on=_INSTALLMENTS[2] + timedelta(days=1), reading=walk,
        ) is True

    def test_both_stops_still_ahead_and_owing_is_live(self):
        """Neither stop has ended it: the commitment counts."""
        closing = Closing(
            authored=EndsOnDate(on=date(2030, 12, 31)),
            derived=ClosesOn(on=_CLOSES),
        )

        assert closing.has_closed(
            on=date(2028, 6, 1), reading=_reading(*_INSTALLMENTS),
        ) is False


class TestTheHorizonBetweenTheTwoStops:
    """The arbitration the OR replaces, graded where the two stops disagree.

    Each stop tests the schedule's reach against its OWN last day, so a
    horizon that lies BETWEEN the two stops makes exactly one of them able to
    say "nothing more is owed" while the other can only say "the schedule has
    not got there".  The composition must take the one that can answer; an
    AND, a respelling as ``reaches(min(authored, derived))`` or a dropped
    ``reaches`` would pass every case in the classes above and fail here.  A
    neutral review of plan step R7d-e asked for these; before them the
    argument lived only in ``Closing.has_closed``'s docstring.
    """

    def test_the_derived_stop_answers_where_the_authored_bound_cannot_yet(self):
        """Horizon past the loan's close, short of the owner's later bound.

        Walk ``(2029-01-22,)`` under both stops, horizon 2029-06-01, asked on
        2029-02-01: nothing is owed on or after that day.  The authored
        ``EndsOnDate(2030-12-31)`` cannot say so -- the schedule stops short
        of it -- and answers "live"; the derived ``ClosesOn(2029-02-22)`` can,
        because the schedule reaches the close.  Composed: ended.
        """
        closing = Closing(
            authored=EndsOnDate(on=date(2030, 12, 31)),
            derived=ClosesOn(on=_CLOSES),
        )
        walk = _reading(date(2029, 1, 22), horizon=date(2029, 6, 1))

        assert closing.authored.has_closed(on=date(2029, 2, 1), reading=walk) is False
        assert closing.derived.has_closed(on=date(2029, 2, 1), reading=walk) is True
        assert closing.has_closed(on=date(2029, 2, 1), reading=walk) is True

    def test_the_authored_bound_answers_where_the_derived_stop_cannot_yet(self):
        """The mirror: horizon past the owner's bound, short of the payoff.

        Walk ``(2028-06-22,)``, horizon 2028-08-01, asked on 2028-06-25.  The
        authored ``EndsOnDate(2028-06-30)`` sees nothing owed and a schedule
        that reaches its day: ended.  The derived ``ClosesOn(2029-02-22)``
        cannot say so.  Composed: ended, by the owner's word.
        """
        closing = Closing(
            authored=EndsOnDate(on=date(2028, 6, 30)),
            derived=ClosesOn(on=_CLOSES),
        )
        walk = _reading(date(2028, 6, 22), horizon=date(2028, 8, 1))

        assert closing.authored.has_closed(on=date(2028, 6, 25), reading=walk) is True
        assert closing.derived.has_closed(on=date(2028, 6, 25), reading=walk) is False
        assert closing.has_closed(on=date(2028, 6, 25), reading=walk) is True

    def test_a_schedule_short_of_BOTH_stops_leaves_it_live(self):
        """Neither stop can say the definition is finished: it is not.

        Walk ``(2028-06-22,)``, horizon 2028-08-01, both stops later than that
        horizon.  Nothing is owed inside the schedule, and the schedule proves
        nothing beyond it -- the unextended-schedule guard, composed.
        """
        closing = Closing(
            authored=EndsOnDate(on=date(2030, 12, 31)),
            derived=ClosesOn(on=_CLOSES),
        )
        walk = _reading(date(2028, 6, 22), horizon=date(2028, 8, 1))

        assert closing.has_closed(on=date(2028, 6, 25), reading=walk) is False

    def test_the_derived_stop_cuts_a_count_short(self):
        """A count the loan never lets finish still ends when the loan does.

        "Ends after 3" beside a loan that closes after ONE installment: the
        walk under both stops holds one occurrence, so the count shape answers
        "live" (one of three emitted); the derived stop sees nothing owed and
        a schedule that reaches the close.  Composed: ended -- a definition
        whose walk stopped for the loan's reason is not still owed for the
        count's.
        """
        closes = date(2028, 2, 1)
        closing = Closing(
            authored=EndsAfterOccurrences(count=3),
            derived=ClosesOn(on=closes),
        )
        walk = _reading(date(2028, 1, 22), horizon=date(2028, 6, 1))

        assert closing.authored.has_closed(on=date(2028, 2, 5), reading=walk) is False
        assert closing.derived.has_closed(on=date(2028, 2, 5), reading=walk) is True
        assert closing.has_closed(on=date(2028, 2, 5), reading=walk) is True

    def test_the_reading_is_walked_at_most_once(self):
        """The memo, graded on a pair that asks TWICE.

        A date bound still ahead and a closing date still ahead, both owed:
        the authored shape asks the walk and answers "live", then the derived
        shape asks it again.  (A count bound still unspent beside the same
        closing date is the other such pair; the count shape always reads the
        walk.)  Without the memo that is two walks per row on the Recurring
        surface for every such loan payment; with it the callable is produced
        once.  The control that this pair DOES ask twice is the un-memoised
        count beside it: both shapes read the same counting callable directly
        and it reads 2.
        """
        walk = BoundReading(occurrences=_INSTALLMENTS, horizon=date(2099, 12, 31))
        authored = EndsOnDate(on=date(2030, 12, 31))
        derived = ClosesOn(on=_CLOSES)

        # The control: asked directly, the pair reads the walk twice.
        unmemoised = _Counting(walk)
        assert authored.has_closed(on=date(2028, 6, 1), reading=unmemoised) is False
        assert derived.has_closed(on=date(2028, 6, 1), reading=unmemoised) is False
        assert unmemoised.calls == 2, (
            "the control does not fire: this pair should read the walk twice "
            "when nothing memoises it"
        )

        # The claim: composed, the same pair reads it once.
        memoised = _Counting(walk)
        assert Closing(authored=authored, derived=derived).has_closed(
            on=date(2028, 6, 1), reading=memoised,
        ) is False
        assert memoised.calls == 1, (
            f"Closing.has_closed produced the reading {memoised.calls} times "
            f"for one question"
        )
