"""
Shekel Budget App -- The pay-period schedule, as the resolver reads it

A recurrence rule's first occurrence is not a property of the rule alone: it
is measured against the owner's pay-period schedule.  ``match_periods``
reaches that schedule through ORM rows; the resolver
(:mod:`app.services.recurrence._resolution`) reaches it through the frozen
value objects here, so the derivation is a pure function of two values and can
be exercised at exact dates without a database.

:class:`PeriodCalendar` deliberately exposes only the three questions the
resolver asks -- the schedule's opening bound, one period by id, and a
month's earliest payday.  It is not a general pay-period API; the general one
is :mod:`app.services.pay_period_service`, and widening this to mirror it
would put a second schedule reader in the tree.

**Period order is by ``period_index``, and that is also date order.**
``pay_period_service._reject_overlapping_batch`` enforces a forward-only
invariant -- a new batch must start strictly after the latest existing
``end_date`` -- so index order and calendar order cannot disagree.  What the
invariant does NOT promise is CONTIGUITY: it rejects overlaps, not gaps, so a
schedule may leave a date covered by no period at all (finding D7).  Nothing
here assumes otherwise: :meth:`PeriodCalendar.earliest_start_in_month` takes a
minimum over the periods that exist rather than indexing into a walk.
"""
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class SchedulePeriod:
    """One pay period, reduced to the four fields the resolver reads.

    Attributes:
        period_id: The ``budget.pay_periods.id`` this came from, or ``None``
            for an unsaved period (the R1 characterization oracle builds its
            schedules in memory, and ``match_periods`` never reads the id).
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

    Attributes:
        periods: The owner's periods in ``period_index`` order.  Empty is a
            legal value here but not a resolvable one -- see
            :meth:`opening_bound`.
    """

    periods: tuple[SchedulePeriod, ...]

    @classmethod
    def from_pay_periods(cls, pay_periods: Iterable) -> "PeriodCalendar":
        """Build a calendar from ``PayPeriod`` ORM rows.

        Sorts by ``period_index`` rather than trusting the caller's query
        order: every current caller goes through
        ``pay_period_service.get_all_periods`` (which orders by index), but a
        calendar whose order depended on how it was fetched would make
        :meth:`opening_bound` answer differently for the same schedule.

        Args:
            pay_periods: An iterable of
                :class:`~app.models.pay_period.PayPeriod` rows, saved or not.

        Returns:
            The frozen calendar.
        """
        rows = sorted(pay_periods, key=lambda period: period.period_index)
        return cls(periods=tuple(
            SchedulePeriod(
                period_id=period.id,
                period_index=period.period_index,
                start_date=period.start_date,
                end_date=period.end_date,
            )
            for period in rows
        ))

    def opening_bound(self) -> date | None:
        """Return the schedule's first payday, or ``None`` when it is empty.

        This is the floor every rule's effective start is measured from,
        because ``recurrence_engine.resolve_generation_plan`` falls back to
        ``periods[0].start_date`` when a rule names no start period and no
        caller supplies an ``effective_from`` (``:123-124``).

        Returns:
            The earliest period's ``start_date``, or ``None`` for an empty
            schedule.
        """
        if not self.periods:
            return None
        return self.periods[0].start_date

    def period_by_id(self, period_id: int | None) -> SchedulePeriod | None:
        """Return the period with *period_id*, or ``None``.

        Args:
            period_id: A ``budget.pay_periods.id``, or ``None``.

        Returns:
            The matching :class:`SchedulePeriod`, or ``None`` when
            *period_id* is ``None`` or names no period in this schedule (a
            rule whose start period was deleted -- the FK is
            ``ON DELETE SET NULL``, but a stale in-memory id can outlive it).
        """
        if period_id is None:
            return None
        for period in self.periods:
            if period.period_id == period_id:
                return period
        return None

    def earliest_start_in_month(self, year: int, month: int) -> date | None:
        """Return the earliest payday falling in *year* / *month*.

        The one question ``Monthly First`` asks: that pattern fires on each
        month's FIRST paycheck, so whether a given month can honour a rule
        depends on when its first paycheck lands.

        Args:
            year: Calendar year.
            month: Calendar month, 1-12.

        Returns:
            The earliest ``start_date`` in that month, or ``None`` when the
            schedule materialises no period there.  ``None`` is a real answer,
            not an error: a cadence longer than a month leaves months with no
            payday at all, and the horizon ends somewhere.
        """
        starts = [
            period.start_date for period in self.periods
            if period.start_date.year == year
            and period.start_date.month == month
        ]
        if not starts:
            return None
        return min(starts)
