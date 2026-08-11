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
hand may not be -- and it cannot live in ``app.services.recurrence``, whose
modules are deliberately free of Flask, the ORM, the clock and the database,
all of which this needs.

Flask-isolated (plain values in, no ``request`` / ``session`` reads); it reads
the database through ``pay_period_service`` and never writes.
"""
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.exceptions import RecurrenceWindowError
from app.models.pay_period import PayPeriod
from app.services import pay_period_service
from app.services.pay_calendar import PayCalendar, calendar_for


@dataclass(frozen=True)
class GenerationSchedule:
    """The OWNER's whole pay-period schedule, and the slice a pass writes into.

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

    Naming the two facts separately is the fix, and **the separation is
    enforced rather than conventional**.  Both classmethods load the whole
    schedule from the database themselves, so a caller states the window and
    has no way to state the schedule; and :meth:`__post_init__` refuses any
    value whose ``calendar`` is not exactly its ``periods``, so the D22 shape
    -- a narrowed calendar beside a matching window -- cannot be built through
    the public constructor either.  An adversarial review found the first
    draft's claim was carried by the docstring alone: the generated
    ``__init__`` accepted a batch as all three fields and every check passed.

    Measured direction of the change, over every contiguous window of the
    production schedule -- 86,986 ``(rule, window)`` pairs: the whole-schedule
    reading NEVER names a period the window reading does not.  It named fewer
    in 1,008 of them and the same set in the rest, so on live data this can
    only ever remove a row that no occurrence justified.

    Attributes:
        periods: The owner's whole schedule as ORM rows, in ``period_index``
            order.  This is what the paycheck calculator means by
            ``all_periods``.
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`,
            loaded through that package's one door.  It USED to be built from
            *periods* rather than loaded, on the ground that two reads could
            describe different schedules; plan step **C2-b2** inverted that,
            because the door takes no window argument and so cannot be handed
            a slice at all.  What was a construction rule is now a property of
            the only way to get one, and :meth:`__post_init__`'s first check
            became the cross-read consistency assert it describes.
        write_periods: The periods this pass may write into, keyed by
            ``budget.pay_periods.id``.  A read-only mapping, and always a
            subset of *periods*.  Keyed by id rather than held as a list
            because the generation seam asks exactly one question of it --
            "is the period this occurrence placed on one I may write into,
            and if so which ORM row is it" -- and a mapping answers both at
            once.
    """

    periods: tuple[PayPeriod, ...]
    calendar: PayCalendar
    write_periods: Mapping[int, PayPeriod]

    def __post_init__(self) -> None:
        """Refuse any value whose three fields do not describe ONE schedule.

        Two checks, and between them they make the defect this class exists to
        end unconstructible rather than merely discouraged:

        1. **The calendar IS the schedule.**  ``calendar`` must carry exactly
           ``periods``, in the same order.  It was written for the D22 shape
           -- resolving against a NARROWED calendar beside a matching window,
           which made an extend re-read every rule as though the owner's pay
           history began at the new batch -- and since plan step **C2-b2**
           that shape is unconstructible: the calendar comes from
           :func:`~app.services.pay_calendar.calendar_for`, which has no
           window argument.  What the check still catches is real and is why
           it stays: the two reads are separate statements under READ
           COMMITTED, so a concurrent schedule write between them is visible
           here, and a STORED ``period_index`` whose order disagrees with its
           own payday order (legacy data, which the derived ordinal cannot
           reproduce) is refused rather than silently re-phasing every
           ``Every N Periods`` rule.
        2. **The window is part of the schedule.**  A row written into a
           period the rule was never resolved against is a row placed by
           nothing: the occurrence walk cannot have named it.  The only ways
           in are a caller pairing one user's template with another user's
           period, or a period id that no longer exists -- and both would
           otherwise be SILENT, because the intersection in
           ``recurrence_engine.resolve_generation_plan`` would simply match
           nothing and the pass would report "generated 0 rows" for a
           definition that fires every paycheck.

        Raises:
            RecurrenceWindowError: When the calendar is not the schedule, when
                a period is unsaved, or when a write-window period is absent
                from the schedule.
        """
        schedule_ids = tuple(period.id for period in self.periods)
        if any(period_id is None for period_id in schedule_ids):
            raise RecurrenceWindowError(
                f"user {self.calendar.user_id}'s schedule contains an UNSAVED "
                f"pay period, which has no id to match a window against.  A "
                f"generate pass resolves and writes by pay-period id, so a "
                f"schedule read back from the database is the only kind it "
                f"can use."
            )
        calendar_ids = tuple(
            period.period_id for period in self.calendar.periods
        )
        if calendar_ids != schedule_ids:
            raise RecurrenceWindowError(
                f"the calendar describes {len(calendar_ids)} pay period(s) and "
                f"the schedule {len(schedule_ids)}, or they are not the same "
                f"periods in the same order.  These are two reads of "
                f"budget.pay_periods -- one ordered by the stored "
                f"period_index, one by payday -- so they disagree when a "
                f"concurrent write lands between them, or when a stored "
                f"ordinal's order disagrees with its own payday order.  The "
                f"second would silently re-phase every Every N Periods rule "
                f"for this owner, so it is refused rather than answered.  "
                f"Rebuild the schedule with the pay-period reset, which "
                f"refuses when any transaction is already settled -- an owner "
                f"with settled history needs the budget.pay_periods rows "
                f"corrected directly."
            )
        owned = set(schedule_ids)
        stray = sorted(
            period_id for period_id in self.write_periods
            if period_id not in owned
        )
        if stray:
            raise RecurrenceWindowError(
                f"pay period id(s) {stray} are not in this owner's schedule of "
                f"{len(self.periods)} periods, so a rule resolved against that "
                f"schedule can never place a row in them.  Generating into a "
                f"period the recurrence was not resolved against would write a "
                f"row nothing selected."
            )

    @classmethod
    def _load(cls, user_id: int, choose_window) -> "GenerationSchedule":
        """Build the value from the owner's OWN schedule and a chosen window.

        The single body both public constructors call.  They differ only in
        which window they choose, and their agreement on everything else is
        what the class's guarantee rests on -- so it is one function rather
        than two that happen to match.  ONE schedule read, whichever door was
        used.

        Args:
            user_id: The owning user.  The schedule is read for them here, and
                nowhere else, which is what stops a caller supplying one.
            choose_window: Called with the loaded periods; returns
                ``{pay_periods.id: PayPeriod}`` for what this pass may write
                into.

        Returns:
            The frozen :class:`GenerationSchedule`.

        Raises:
            RecurrenceWindowError: See :meth:`__post_init__`.
        """
        periods = tuple(pay_period_service.get_all_periods(user_id))
        return cls(
            periods=periods,
            calendar=calendar_for(user_id),
            write_periods=MappingProxyType(choose_window(periods)),
        )

    @classmethod
    def for_user(cls, user_id: int) -> "GenerationSchedule":
        """Load the owner's schedule with EVERY period open for writing.

        What the create, unarchive, salary and template-edit paths mean: they
        re-drive a template across the whole schedule and let the per-period
        skip predicate (``_recurrence_common.should_skip_period``) decide what
        is already there.

        Args:
            user_id: The owning user.

        Returns:
            The schedule, its window covering every period.
        """
        return cls._load(
            user_id,
            lambda periods: {period.id: period for period in periods},
        )

    @classmethod
    def for_periods(
        cls, user_id: int, write_periods: Iterable[PayPeriod],
    ) -> "GenerationSchedule":
        """Load the owner's schedule, opening only *write_periods* for writing.

        What the extend / regenerate / reset repopulation means (write into
        the periods just created) and what the carry-forward generate branch
        means (write into exactly this one period).  **The schedule is loaded
        here rather than taken from the caller**, which is the whole point: the
        caller states the window and cannot state the schedule, so the window
        can no longer stand in for it.

        Args:
            user_id: The owning user.
            write_periods: The periods this pass may write into.  Must already
                be flushed -- an unsaved period has no id to match against a
                schedule read back from the database.
                ``pay_period_write.record_paydays`` flushes before returning,
                so every repopulation caller already satisfies this.

        Returns:
            The schedule, its window covering exactly *write_periods*.

        Raises:
            RecurrenceWindowError: When a write period is unsaved, or is not
                one of this owner's (see :meth:`__post_init__`).
        """
        window = {}
        for period in write_periods:
            if period.id is None:
                raise RecurrenceWindowError(
                    f"pay period at index {period.period_index} has no id, so "
                    f"it cannot be matched against the owner's loaded "
                    f"schedule.  Flush new periods before populating them."
                )
            window[period.id] = period
        return cls._load(user_id, lambda _periods: window)


__all__ = ["GenerationSchedule"]
