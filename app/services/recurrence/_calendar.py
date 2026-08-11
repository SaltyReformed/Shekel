"""
Shekel Budget App -- The pay-period schedule, as the resolver reads it

A recurrence rule's first occurrence is not a property of the rule alone: it
is measured against the owner's pay-period schedule.
Every caller is handed that schedule as ORM rows and converts it here
(:meth:`PeriodCalendar.from_pay_periods`); the resolver
(:mod:`app.services.recurrence._resolution`) and the occurrence engine see
only the frozen value objects, so both are pure functions of two values and
can be exercised at exact dates without a database.

:class:`PeriodCalendar` deliberately exposes only the questions the derivation
and the occurrence engine ask -- the schedule's opening bound and horizon, one
period by id, a month's earliest payday, and the two placement searches
(:meth:`PeriodCalendar.period_containing` and
:meth:`PeriodCalendar.period_starting_on_or_after`).  It is not a general
pay-period API; the general one is :mod:`app.services.pay_period_service`, and
widening this to mirror it would put a second schedule reader in the tree.

**Period order is by ``period_index``, and that is also date order.**
Since plan step **C3-b** ``pay_period_write`` reads both stored columns off
``pay_calendar.derive_periods``, where the ordinal IS the position in payday
order -- so index order and calendar order cannot disagree by construction
rather than by a guard.  (Before it, ``pay_period_service``'s
``_reject_overlapping_batch`` held the property as an invariant: a new batch
had to start strictly after the latest existing ``end_date``.)  Plan step R3's
placement searches BISECT on that order, which turns the property into a
load-bearing one, so :meth:`__post_init__` CHECKS it: an out-of-order or
overlapping schedule would otherwise place a row in the wrong pay period
silently, the same failure mode the cash fold's identical bisect carries
(``balance_at._cash_periods._PeriodSpans``).

What the derivation does NOT promise for data written BEFORE C3-b is
CONTIGUITY: the old guard rejected overlaps, not gaps, so such a schedule may
leave a date covered by no period at all (finding D7).
Nothing here assumes otherwise --
:meth:`PeriodCalendar.earliest_start_in_month` takes a minimum over the periods
that exist rather than indexing into a walk, and
:meth:`PeriodCalendar.period_containing` answers ``None`` for a day in a gap
rather than pulling it into the neighbouring period.
"""
from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from operator import attrgetter

from app.exceptions import ShekelError
from app.services.pay_calendar import (
    containing_period,
    earliest_start_in_month,
    final_covered_day,
    opening_payday,
    period_by_id,
)

#: The bisect key for both placement searches: a period's opening payday.
#: Module-level so the two searches cannot key on different fields.
_BY_START_DATE = attrgetter("start_date")

#: What a refused schedule tells the operator to do about it.  Named once and
#: appended to both refusals: an error a user cannot act on is a dead end, and
#: the in-app repair does not cover every user (see
#: :class:`RecurrenceScheduleError`).
_REPAIR_HINT = (
    "Rebuild the schedule with the pay-period reset, which refuses when any "
    "transaction is already settled -- an owner with settled history needs "
    "the budget.pay_periods rows corrected directly."
)


class RecurrenceScheduleError(ShekelError, ValueError):
    """A pay-period schedule cannot be searched by date.

    A broken invariant, not user input: ``pay_period_write`` is the only writer
    of ``budget.pay_periods`` rows and the derivation it materialises cannot
    express the states this names, so reaching a user as a 500 is the correct
    disposition -- there is no form field to flash it against and no safe
    answer to give instead.

    Also a ``ValueError`` because it is raised from
    :meth:`PeriodCalendar.__post_init__`, where Python's own contract for a
    rejected constructor argument is ``ValueError``; a caller catching either
    name gets it.

    **The recovery is not in-app for every user**, which is why the message
    says so: ``pay_period_admin.reset_pay_periods`` rebuilds a schedule, but
    it refuses when the owner holds any settled transaction, so a user with
    settled history and a corrupt schedule needs the rows repaired directly.
    """


@dataclass(frozen=True)
class SchedulePeriod:
    """One pay period, reduced to the four fields the resolver reads.

    Attributes:
        period_id: The ``budget.pay_periods.id`` this came from, or ``None``
            for an unsaved period.  Only :meth:`PeriodCalendar.period_by_id`
            reads it, and only to resolve a rule's chosen start period, so a
            schedule built in memory resolves everything else unchanged.
        period_index: The owner's 0-based ordinal for the period.
        start_date: The payday the period opens on.
        end_date: The last day the period covers.
    """

    period_id: int | None
    period_index: int
    start_date: date
    end_date: date


@dataclass(frozen=True)
class PeriodCalendar:
    """A user's pay-period schedule, ordered by ``period_index``.

    **Carries the owner, and the resolver refuses a mismatch.**  A recurrence
    anchor is measured against a schedule, so resolving one user's rule
    against another user's schedule silently produces a first occurrence that
    is wrong rather than an error.  Nothing in the application does that today
    -- but two call sites derive the calendar's owner from a DIFFERENT object
    than the rule's (``loan_recurrence_sync`` uses ``account.user_id``,
    ``pay_period_admin`` uses ``first_period.user_id``), so the pairing is an
    assumption rather than a fact until it is checked.  Recording the owner
    here is what lets :func:`~app.services.recurrence.resolve` check it.

    **The check is vacuous on the generation path and that is worth stating.**
    :class:`~app.services.generation_schedule.GenerationSchedule` builds the
    calendar for the user it loaded the schedule for, and every generate pass
    is entered with the template's own owner, so on that path the guard
    compares a value against itself.  It still bites where it was written for
    -- the two call sites above, which pass an owner they derived elsewhere.
    A reader must not mistake this for a check on the generation path.

    Attributes:
        user_id: The user whose schedule this is.
        periods: The owner's periods in ``period_index`` order.  Empty is a
            legal value here but not a resolvable one -- see
            :meth:`opening_bound`.
    """

    user_id: int
    periods: tuple[SchedulePeriod, ...]

    def __post_init__(self) -> None:
        """Refuse a schedule whose periods do not tile the calendar forward.

        The property ``pay_period_write`` gets by construction at the write
        door (its stored columns come from the derivation), checked again at
        the value boundary because plan step R3's placement searches DEPEND on
        it: they bisect
        over ``periods`` keyed on ``start_date``, so a schedule whose index
        order disagrees with its date order returns a plausible WRONG period
        rather than an error, and a generated bill lands in a paycheck the
        user did not budget it into.

        Overlap is refused for the same reason it is refused upstream: two
        periods covering one day give "which period contains this date" two
        answers, and the search would silently return whichever started
        later.  GAPS are legal -- the schedule may leave a date covered by no
        period at all (finding D7), which the searches answer with ``None``.

        O(n) once per calendar against an O(n) construction, and a calendar is
        built once per request (``app.services.recurrence.calendar_for``), so
        the check costs nothing measurable and converts a silent misplacement
        into a loud refusal.

        Raises:
            RecurrenceScheduleError: When a period ends before it starts, or
                when a period opens on or before its predecessor's last
                covered day.
        """
        for period in self.periods:
            if period.end_date < period.start_date:
                raise RecurrenceScheduleError(
                    f"pay period index {period.period_index} ends "
                    f"{period.end_date} before it starts {period.start_date}, "
                    f"so it covers no day.  A schedule of such periods cannot "
                    f"be searched by date.  {_REPAIR_HINT}"
                )
        for earlier, later in zip(self.periods, self.periods[1:]):
            if later.start_date <= earlier.end_date:
                raise RecurrenceScheduleError(
                    f"pay period index {later.period_index} opens "
                    f"{later.start_date}, on or before index "
                    f"{earlier.period_index}'s last covered day "
                    f"{earlier.end_date}.  Index order must also be date "
                    f"order and periods must not overlap "
                    f"(pay_period_write materialises both columns from the "
                    f"payday derivation, which cannot express either state); a "
                    f"date search over an overlapping "
                    f"schedule returns a plausible wrong pay period instead "
                    f"of an error.  {_REPAIR_HINT}"
                )

    @classmethod
    def from_pay_periods(
        cls, pay_periods: Iterable, user_id: int,
    ) -> "PeriodCalendar":
        """Build a calendar from ``PayPeriod`` ORM rows.

        Sorts by ``period_index`` rather than trusting the caller's query
        order: every current caller goes through
        ``pay_period_service.get_all_periods`` (which orders by index), but a
        calendar whose order depended on how it was fetched would make
        :meth:`opening_bound` answer differently for the same schedule.

        The owner is passed rather than read off the rows.  Reading it would
        mean either trusting the first row (which says nothing about the
        rest) or scanning for disagreement -- and either way an UNSAVED
        ``PayPeriod``, which this class accepts by design, carries no
        ``user_id`` to read.  The caller always knows which user it queried
        for; making it say so costs one argument and removes the question.

        Args:
            pay_periods: An iterable of
                :class:`~app.models.pay_period.PayPeriod` rows, saved or not.
            user_id: The user whose schedule these periods are.

        Returns:
            The frozen calendar.
        """
        rows = sorted(pay_periods, key=lambda period: period.period_index)
        return cls(
            user_id=user_id,
            periods=tuple(
                SchedulePeriod(
                    period_id=period.id,
                    period_index=period.period_index,
                    start_date=period.start_date,
                    end_date=period.end_date,
                )
                for period in rows
            ),
        )

    def opening_bound(self) -> date | None:
        """Return the schedule's first payday, or ``None`` when it is empty.

        This is the floor every rule's effective start is measured from
        (``_resolution._effective_start`` takes it as one of three bounds).
        It USED to be reached a second way -- ``resolve_generation_plan``
        defaulted ``effective_from`` to ``periods[0].start_date`` for a rule
        naming no start period -- and plan step R4b-1 deleted that default
        because this floor already states it.  One producer, not two that
        happen to agree.

        Returns:
            The earliest period's ``start_date``, or ``None`` for an empty
            schedule.
        """
        return opening_payday(self.periods)

    def horizon(self) -> date | None:
        """Return the last day the schedule covers, or ``None`` when empty.

        The symmetric partner of :meth:`opening_bound`, and the window plan
        step R3's occurrence engine generates through by default: past this
        day no occurrence can be placed, because there is no period to place
        it in.

        The LAST period's ``end_date`` is that day rather than a maximum over
        all of them, because :meth:`__post_init__` refuses a schedule in which
        an earlier period outlives a later one.

        Returns:
            The last covered day, or ``None`` for an empty schedule.
        """
        return final_covered_day(self.periods)

    def period_containing(self, day: date) -> SchedulePeriod | None:
        """Return the period whose span covers *day*, or ``None``.

        One half of the placement axis
        (:class:`~app.enums.PeriodPlacementEnum` ``CONTAINING_DATE``): today's
        Monthly, Quarterly, Semi-Annual and Annual patterns put a row in the
        period their occurrence date falls inside.

        Periods do not overlap (:meth:`__post_init__`), so the latest period
        STARTING on or before *day* is the only candidate that can contain it
        and one bisect answers.  ``None`` is a real answer, not an error: pay
        periods are not contiguous by construction -- the generator rejects
        overlaps, not gaps -- so a date can fall in a hole, and it can fall
        past the horizon or before the schedule opens (finding D7).  Answering
        ``None`` rather than the neighbouring period is the point: pulling a
        bill into a paycheck whose span does not contain it is a silent
        misplacement of real money.

        Args:
            day: The calendar day to place.

        Returns:
            The containing :class:`SchedulePeriod`, or ``None``.
        """
        return containing_period(self.periods, day)

    def period_starting_on_or_after(self, day: date) -> SchedulePeriod | None:
        """Return the first period opening on or after *day*, or ``None``.

        The other half of the placement axis
        (:class:`~app.enums.PeriodPlacementEnum`
        ``PERIOD_STARTING_ON_OR_AFTER``): today's ``Monthly First`` pattern
        puts a row in the first PAYCHECK on or after its occurrence, which is
        the 1st of a month.

        Args:
            day: The calendar day to place.

        Returns:
            The first :class:`SchedulePeriod` whose ``start_date`` is on or
            after *day*, or ``None`` when the schedule reaches no such period
            -- a day past the materialised horizon, where the answer is "not
            yet" rather than "never".
        """
        index = bisect_left(self.periods, day, key=_BY_START_DATE)
        if index >= len(self.periods):
            return None
        return self.periods[index]

    def period_by_id(self, period_id: int | None) -> SchedulePeriod | None:
        """Return the period with *period_id*, or ``None``.

        Delegates to the shared primitive since plan step C2-b1, for the reason
        the three searches above delegate: one question must not have two
        implementations, and this one decides which stored paycheck a rule's
        authored start period names.

        Args:
            period_id: A ``budget.pay_periods.id``, or ``None``.

        Returns:
            The matching :class:`SchedulePeriod`, or ``None`` when
            *period_id* is ``None`` or names no period in this schedule (a
            rule whose start period was deleted -- the FK is
            ``ON DELETE SET NULL``, but a stale in-memory id can outlive it).
        """
        return period_by_id(self.periods, period_id)

    def earliest_start_in_month(self, year: int, month: int) -> date | None:
        """Return the earliest payday falling in *year* / *month*.

        The one question ``Monthly First`` asks: that pattern fires on each
        month's FIRST paycheck, so whether a given month can honour a rule
        depends on when its first paycheck lands.  Delegates to the shared
        primitive since plan step C2-b1.

        Args:
            year: Calendar year.
            month: Calendar month, 1-12.

        Returns:
            The earliest ``start_date`` in that month, or ``None`` when the
            schedule materialises no period there.  ``None`` is a real answer,
            not an error: a cadence longer than a month leaves months with no
            payday at all, and the horizon ends somewhere.
        """
        return earliest_start_in_month(self.periods, year, month)
