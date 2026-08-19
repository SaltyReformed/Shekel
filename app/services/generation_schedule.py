"""
Shekel Budget App -- The schedule a recurrence generate pass runs against
(plan step R4b-1)

One value, :class:`GenerationSchedule`, carrying the two facts a generate pass
needs about pay periods -- and keeping them apart, which is the whole point.

Its own public module rather than a class inside either engine, because three
different layers construct one: both recurrence engines consume it, the
repopulation orchestrator (``app.services.period_population``) builds it for
the extend / regenerate / reset paths, and four route handlers build it for the
create / unarchive / salary / template-edit paths.  It cannot live in
``app.services._recurrence_common`` -- that module is package-private
(``shekel-private-module-import``) and a type the route layer must name by
hand may not be -- and it cannot live in ``app.services.pay_calendar``, whose
public surface is the derivation itself and which must not learn what a
recurrence pass is.

**It reads nothing** (pay-calendar plan step C2-f3c).  It used to open its own
database read -- ``pay_period_service.get_all_periods`` for a list of ORM rows
BESIDE the calendar -- and reconciling those two reads was the whole of its
``__post_init__``.  The caller now hands over the one
:class:`~app.services.pay_calendar.PayCalendar` its request already derived, so
this module holds no session, no ORM model and no clock; every field is a plain
value the caller supplies.
"""
from collections.abc import Iterable
from dataclasses import dataclass

from app.exceptions import RecurrenceWindowError
from app.services.pay_calendar import PayCalendar


@dataclass(frozen=True)
class GenerationSchedule:
    """The OWNER's whole pay calendar, and the slice a pass writes into.

    **The value that separates two facts one argument used to carry**, which
    is plan step R4b-1's whole subject.  Both recurrence engines took a single
    ``periods`` list and used it for two unrelated jobs:

    1. the schedule the rule is RESOLVED against -- when its first occurrence
       falls, which months hold a payday, which period its chosen "First
       paycheck" is.  A fact about the OWNER.
    2. the set of periods the pass may WRITE into.  A choice the CALLER makes.

    Job 1 silently took job 2's answer.  ``period_population`` hands each
    engine only the NEWLY created periods, so an extend read every rule as
    though the owner's pay history began at the new batch.  Three measured
    consequences, all live on production (2026-08-08, against a streamed
    clone of ``shekel-prod-db``):

    * a ``Monthly First`` rule re-fired in a month it had already covered,
      because the window's own first payday in that month qualified it again
      -- 3 spurious ``Phone Allowance`` rows, $118.62, one per extend that
      lands a new period in a covered month (plan ledger row **D22**);
    * the paycheck calculator received the same truncated list as its
      ``all_periods``, so third-paycheck detection, the first-paycheck-of-month
      deductions, the annual rounding reconciliation and the FICA wage-base
      cumulative all read 1-3 periods instead of 61.  One salary row was STORED
      $502.45 below its true net pay (plan ledger row **D25**); the read-time
      recompute kept that off every surface, which is measured rather than
      assumed in ``recurrence_engine._get_transaction_amount``;
    * a rule's chosen start period could not be found in the window, so the
      opening bound it states was dropped entirely (plan ledger row **D2**).

    Naming the two facts separately is the fix, and **what keeps them apart is
    now the TYPE of each rather than a check** (plan step C2-f3c).  The
    schedule is a :class:`~app.services.pay_calendar.PayCalendar`, which is
    only ever an owner's COMPLETE payday set: the one function that produces
    one, :func:`~app.services.pay_calendar.calendar_for`, takes a user id and
    has no window argument, and narrowing a calendar yields a
    :class:`~app.services.pay_calendar.PeriodWindow` -- a different type that
    nothing can derive a calendar back out of.  The window is a set of
    ``budget.pay_periods.id`` values, which cannot describe a schedule at all.
    So the D22 shape is no longer a value this class has to refuse; it is one
    the two types cannot spell.

    **Until C2-f3c the schedule was read HERE, twice.**  This class loaded
    ``pay_period_service.get_all_periods`` for a tuple of ORM rows and
    :func:`~app.services.pay_calendar.calendar_for` for the calendar, and
    ``__post_init__`` refused any value whose two halves disagreed -- a real
    check, because they were separate statements under READ COMMITTED and
    because a stored ``period_index`` out of payday order made them differ.
    Both reasons are gone with the second read: there is one statement, its
    order is payday order by construction, and no part of the generation seam
    reads a stored ordinal or a stored end at all.  What replaced the check is
    the absence of the thing it reconciled.

    Measured direction of the R4b-1 change, over every contiguous window of the
    production schedule -- 86,986 ``(rule, window)`` pairs: the whole-schedule
    reading NEVER names a period the window reading does not.  It named fewer
    in 1,008 of them and the same set in the rest, so on live data it can
    only ever remove a row that no occurrence justified.

    Attributes:
        calendar: The owner's whole pay calendar, derived from their complete
            payday set.  It answers both halves of what a pass needs about the
            schedule: which periods a rule fires in (the occurrence walk reads
            it) and what each of those periods IS (its payday, its last covered
            day, its id).  It is the OWNER's, never the window's; see
            ``recurrence_engine._get_transaction_amount`` for the $502.45 that
            distinction was worth.
        write_period_ids: The ``budget.pay_periods.id`` values this pass may
            write into, and always a subset of *calendar*'s materialised
            periods.  Ids rather than periods because that is the only question
            the seam asks of the window -- "is the period this occurrence was
            placed on one I may write into" -- and because the ANSWER a write
            needs, the period itself, already rode in on the placement.
    """

    calendar: PayCalendar
    write_period_ids: frozenset[int]

    def __post_init__(self) -> None:
        """Refuse a window naming a period this owner's calendar does not hold.

        **The window is part of the schedule.**  A row written into a period
        the rule was never resolved against is a row placed by nothing: the
        occurrence walk cannot have named it.  The only ways in are a caller
        pairing one user's template with another user's period, or a period id
        that no longer exists -- and both would otherwise be SILENT, because
        the intersection in ``recurrence_engine.resolve_generation_plan`` would
        simply match nothing and the pass would report "generated 0 rows" for a
        definition that fires every paycheck.

        **It is the only refusal left here, and neither constructor can reach
        it** (plan step C2-f3c).  Both windows in ``app/`` are now derived from
        the same calendar this value carries -- ``period_population`` narrows
        to the periods a write just recorded and then derives the calendar that
        holds them, and ``carry_forward_service`` narrows to a period it looked
        up ON the calendar -- so a stray id names a caller that assembled the
        pair by hand.  Two sibling checks went with the second read C2-f3c
        deleted: one reconciled the calendar against a tuple of ORM rows, and
        one refused an UNSAVED period, which had an id of ``None``.  A window
        of ids cannot carry an unsaved period, so that state has no spelling
        here any more.

        Raises:
            RecurrenceWindowError: A write-window id is not one of this
                owner's materialised periods.
        """
        owned = {period.period_id for period in self.calendar.saved()}
        stray = sorted(
            period_id for period_id in self.write_period_ids
            if period_id not in owned
        )
        if stray:
            raise RecurrenceWindowError(
                f"pay period id(s) {stray} are not in user "
                f"{self.calendar.user_id}'s calendar of {len(owned)} saved "
                f"period(s), so a rule resolved against that calendar can "
                f"never place a row in them.  Generating into a period the "
                f"recurrence was not resolved against would write a row "
                f"nothing selected."
            )

    @classmethod
    def for_calendar(cls, calendar: PayCalendar) -> "GenerationSchedule":
        """Open EVERY period of *calendar* for writing.

        What the create, unarchive, salary and template-edit paths mean: they
        re-drive a template across the whole schedule and let the per-period
        skip predicate (``_recurrence_common.should_skip_period``) decide what
        is already there.

        Args:
            calendar: The owner's whole pay calendar.

        Returns:
            The schedule, its window covering every materialised period.
        """
        return cls(
            calendar=calendar,
            write_period_ids=frozenset(
                period.period_id for period in calendar.saved()
            ),
        )

    @classmethod
    def for_period_ids(
        cls, calendar: PayCalendar, period_ids: Iterable[int],
    ) -> "GenerationSchedule":
        """Open only *period_ids* of *calendar* for writing.

        What the extend / regenerate / reset repopulation means (write into
        the periods just recorded) and what the carry-forward generate branch
        means (write into exactly this one period).

        Args:
            calendar: The owner's whole pay calendar.
            period_ids: The ``budget.pay_periods.id`` values this pass may
                write into.  Must all be periods of *calendar*; see
                :meth:`__post_init__`.

        Returns:
            The schedule, its window covering exactly *period_ids*.

        Raises:
            RecurrenceWindowError: An id is not one of this owner's
                materialised periods (see :meth:`__post_init__`).
        """
        return cls(calendar=calendar, write_period_ids=frozenset(period_ids))


__all__ = ["GenerationSchedule"]
