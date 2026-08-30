"""
Shekel Budget App -- The schedule a recurrence generate pass runs against
(plan step R4b-1)

One value, :class:`GenerationSchedule`, carrying the two facts a generate pass
needs -- the READ PASS it runs inside, and the periods it may write into -- and
keeping them apart, which is the whole point.

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

**It takes the READ PASS since plan step R7d-c-1, where it took a bare
:class:`~app.services.pay_calendar.PayCalendar`.**  A generate pass needs a
:class:`~app.services.balance_at.BalanceContext` -- plan step R7d-c-2 bounds a
loan payment by asking the loan rather than by reading
``budget.recurrence_rules.end_date`` -- and the 2026-08-16 ruling forbids a
producer below the route building one, so it is TAKEN.  Carrying it HERE rather
than as a sixth argument on ``resolve_generation_plan`` is what keeps "the
schedule this pass resolves against" and "the pass it resolves in" from being
two spellings of one owner's schedule with nothing reconciling them: the
calendar is now DERIVED from the pass (:attr:`GenerationSchedule.calendar`),
so a mismatched pair is unconstructible rather than merely discouraged.  It
also collapsed the pairs the eight construction sites were already holding --
every one of them resolved ``calendar_for(user)`` and the baseline scenario
side by side, which is exactly the two facts a pass pins.

**So it reads, where until R7d-c-1 it read nothing** -- lazily, through the
pass's own memo, which is the one read the whole request shares.  What that
costs is an ORDERING rule the write paths must keep, and the rule is stated at
:meth:`GenerationSchedule.calendar`.
"""
from collections.abc import Iterable
from dataclasses import dataclass

from app.exceptions import RecurrenceWindowError
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import PayCalendar


@dataclass(frozen=True)
class GenerationSchedule:
    """The OWNER's read pass, and the slice of their schedule a pass writes into.

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
      deductions, the annual rounding reconciliation (DELETED at plan step
      balance:X-aw; the other three still read the list) and the FICA wage-base
      cumulative all read 1-3 periods instead of 61.  One salary row was STORED
      $502.45 below its true net pay (plan ledger row **D25**); the read-time
      recompute kept that off every surface, which is measured rather than
      assumed in ``recurrence_engine._get_transaction_amount``;
    * a rule's chosen start period could not be found in the window, so the
      opening bound it states was dropped entirely (plan ledger row **D2**).

    Naming the two facts separately is the fix.  **What this class GUARANTEED
    about it changed at plan step C2-f3c, and the honest statement is weaker
    than the one a first draft made** (adversarial design review, 2026-08-19).

    Before that step this class LOADED both halves itself, so a caller could
    state the window and had no way to state the schedule: D22 was
    unconstructible through the public constructors, full stop.  It then TOOK
    the calendar, because the alternative is deriving a second one per render
    (ledger row **P68**).  Since plan step R7d-c-1 it takes the READ PASS and
    DERIVES the calendar from it, which restores the stronger property by a
    different route: no constructor here accepts a calendar at all, so the
    schedule a rule resolves against is always ``calendar_for(ctx.user_id)``
    and a caller cannot substitute one.

    **What is still NOT claimed**, because
    :class:`~app.services.pay_calendar.PeriodWindow`'s own docstring measured
    it false: that a calendar cannot be rebuilt from a window.
    ``PayCalendar.from_paydays([(p.period_id, p.start_date) for p in window],
    ...)`` is one line over the public iterator.  What R7d-c-1 changes is that
    such a value can no longer be handed to THIS class -- the pass derives its
    own -- so reproducing D22 now takes a caller who reaches past the pass's
    memo, rather than one who simply passes the wrong argument here.

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

    One state it refused is therefore no longer refused ANYWHERE, and it is
    worth naming: an owner with a scrambled stored ``period_index`` AND no
    ``budget.pay_schedule`` row.  ``pay_schedule_service.resolve_cadence``'s
    legacy fallback finds "the last period" with ``ORDER BY period_index
    DESC``, so a scrambled ordinal gives it the wrong row's length and the
    calendar's projected last end is wrong -- which this class used to make
    loud and now does not.  Both halves are legacy-only (registration writes
    the schedule row since ``balance:X-ad-a``; the writer derives the ordinal
    since ``pay_calendar:C3-b``), and the fallback is ledger rows **P8** and
    **P70**, owned by plan step **C4**, which deletes it with the column it
    reads.

    Measured direction of the R4b-1 change, over every contiguous window of the
    production schedule -- 86,986 ``(rule, window)`` pairs: the whole-schedule
    reading NEVER names a period the window reading does not.  It named fewer
    in 1,008 of them and the same set in the rest, so on live data it can
    only ever remove a row that no occurrence justified.

    Attributes:
        ctx: The read pass this generation runs inside -- the owner, the
            pinned ``as_of``, the baseline scenario, and the memos every
            derivation on the pass shares.  It answers what the schedule
            half of a pass needs (:attr:`calendar`) and, from plan step
            R7d-c-2, what a loan payment's DERIVED closing bound is.  It is
            TAKEN and never built here: the 2026-08-16 ruling and ruling
            **R-R38** together make the ROUTE the only layer that opens a
            GENERATE pass, including on a write path -- the doors that create
            pay periods split so the route can open it between their write and
            the generation.  That is narrower than "only the route calls
            ``BalanceContext.build``", which is ``pay_calendar:C11``'s end
            state and not true yet: five modules under ``app/services/`` still
            call it, one of them (``loan_recurrence_sync``) as C11's own
            carve-out.
        write_period_ids: The ``budget.pay_periods.id`` values this pass may
            write into, and always a subset of :attr:`calendar`'s materialised
            periods.  Ids rather than periods because that is the only question
            the seam asks of the window -- "is the period this occurrence was
            placed on one I may write into" -- and because the ANSWER a write
            needs, the period itself, already rode in on the placement.
    """

    ctx: BalanceContext
    write_period_ids: frozenset[int]

    @property
    def calendar(self) -> PayCalendar:
        """The owner's whole pay calendar, derived once for the whole pass.

        It answers both halves of what a pass needs about the schedule: which
        periods a rule fires in (the occurrence walk reads it) and what each of
        those periods IS (its payday, its last covered day, its id).  It is the
        OWNER's, never the window's; see
        ``recurrence_engine._get_transaction_amount`` for the $502.45 that
        distinction was worth.

        **A property over :attr:`ctx` rather than a field, since plan step
        R7d-c-1.**  A field took a value the caller derived, so a pass and a
        schedule were two spellings of one owner's calendar and nothing
        reconciled them; ``ctx.calendar()`` is the memo the rest of the request
        already reads, so there is one derivation and one answer.

        **The ORDERING rule a write path owes, and it is LOUD for one
        constructor and SILENT for the other.**  The pass's calendar memo is
        filled at its FIRST call and kept for the pass's life, so a caller that
        creates pay periods and then generates into them must not have asked
        the pass for a calendar BEFORE that write: it would answer the
        pre-write schedule, which does not hold the new periods.

        :meth:`for_period_ids` catches that, because its window comes from
        somewhere else -- the new ids are not in the stale calendar, so
        :meth:`__post_init__` refuses the value with
        :class:`~app.exceptions.RecurrenceWindowError` before a single row is
        generated.  The repopulation paths are the ones this matters for, and
        the pass they run in is opened by
        :func:`app.routes._period_population.populate_new_periods` -- after
        the door that recorded the periods has returned (ruling **R-R38**).

        **:meth:`for_pass` CANNOT catch it, and an adversarial review of plan
        step R7d-c-1 measured that after a first draft of this paragraph
        claimed otherwise.**  Its window IS
        ``ctx.calendar().saved()``, so the window is a subset of the calendar
        by construction and the refusal has nothing to compare.  A stale pass
        there UNDER-GENERATES in silence: measured on the review's probe, a
        pass resolved before two periods were recorded generated 10 rows and
        left both new paychecks empty, with no exception.  What keeps that
        unreachable today is not this type -- it is that none of the six
        ``for_pass`` callers creates a pay period, and all six build the pass
        and use it in the same breath.  A caller that ever does both owes
        itself a pass opened after its write, exactly as the repopulation
        paths take one from
        :func:`app.routes._period_population.populate_new_periods`.

        Returns:
            The owner's :class:`~app.services.pay_calendar.PayCalendar`.

        Raises:
            PayCalendarError: The owner has paydays that cannot define a
                calendar; see
                :meth:`~app.services.balance_at.BalanceContext.calendar`.
        """
        return self.ctx.calendar()

    def __post_init__(self) -> None:
        """Refuse a window naming a period this owner's calendar does not hold.

        **The window is part of the schedule.**  A row written into a period
        the rule was never resolved against is a row placed by nothing: the
        occurrence walk cannot have named it.  The ways in are a caller pairing
        one user's template with another user's period, a period id that no
        longer exists, and -- since plan step R7d-c-1 -- a pass whose calendar
        memo was filled before the write that created the ids.  All three would
        otherwise be SILENT, because the intersection in
        ``recurrence_engine.resolve_generation_plan`` would simply match
        nothing and the pass would report "generated 0 rows" for a definition
        that fires every paycheck.

        **It is the only refusal left here** (plan step C2-f3c).  Both windows
        in ``app/`` are derived from the same calendar this value carries --
        ``period_population`` narrows to the periods a write just recorded and
        then builds this value, and ``carry_forward_service`` narrows to a
        period it looked up ON the pass's calendar -- so a stray id names a
        caller that assembled the pair by hand or asked the pass too early.
        Two sibling checks went with the second read C2-f3c deleted: one
        reconciled the calendar against a tuple of ORM rows, and one refused an
        UNSAVED period, which had an id of ``None``.  A window of ids cannot
        carry an unsaved period, so that state has no spelling here any more.

        Raises:
            RecurrenceWindowError: A write-window id is not one of this
                owner's materialised periods.
        """
        owned = {period.period_id for period in self.calendar.saved()}
        # Sorted by REPR, not by value: a hand-assembled window can hold
        # ``None`` beside an int -- an unsaved period's id -- and ``sorted``
        # over a mixed set raises ``TypeError`` from inside the refusal, which
        # is the wrong failure for a caller this message exists to inform
        # (adversarial review of plan step C2-f3c).  Every window in ``app/``
        # is a set of ints; this is for the caller that is not.
        stray = sorted(
            (
                period_id for period_id in self.write_period_ids
                if period_id not in owned
            ),
            key=repr,
        )
        if stray:
            raise RecurrenceWindowError(
                f"pay period id(s) {stray} are not in user "
                f"{self.ctx.user_id}'s calendar of {len(owned)} saved "
                f"period(s), so a rule resolved against that calendar can "
                f"never place a row in them.  Generating into a period the "
                f"recurrence was not resolved against would write a row "
                f"nothing selected.  A read pass whose calendar was resolved "
                f"BEFORE the write that created these periods reads this way."
            )

    @classmethod
    def for_pass(cls, ctx: BalanceContext) -> "GenerationSchedule":
        """Open EVERY period of *ctx*'s calendar for writing.

        What the create, unarchive, salary and template-edit paths mean: they
        re-drive a template across the whole schedule and let the per-period
        claim predicate (``_recurrence_common.OccurrenceClaims``) decide what
        is already there.

        Delegates to :meth:`for_period_ids` rather than building the value
        itself, so "the window is a set of THIS calendar's saved ids" is
        stated once (adversarial review of plan step C2-f3c: the two bodies
        were one construction spelled twice).

        Named for the PASS rather than the calendar since plan step R7d-c-1,
        which is what it now takes; the old spelling would have named an
        argument this constructor no longer accepts.

        Args:
            ctx: The read pass this generation runs inside.

        Returns:
            The schedule, its window covering every materialised period.

        Raises:
            PayCalendarError: *ctx*'s saved periods do not cover an unbroken
                span (:meth:`~app.services.pay_calendar.PayCalendar.saved`).
                Unreachable through
                :func:`~app.services.pay_calendar.calendar_for`, whose periods
                come from the table and are therefore all materialised.
        """
        return cls.for_period_ids(
            ctx, (period.period_id for period in ctx.calendar().saved()),
        )

    @classmethod
    def for_period_ids(
        cls, ctx: BalanceContext, period_ids: Iterable[int],
    ) -> "GenerationSchedule":
        """Open only *period_ids* of *ctx*'s calendar for writing.

        What the extend / regenerate / reset repopulation means (write into
        the periods just recorded) and what the carry-forward generate branch
        means (write into exactly this one period).

        Args:
            ctx: The read pass this generation runs inside.
            period_ids: The ``budget.pay_periods.id`` values this pass may
                write into.  Must all be periods of *ctx*'s calendar; see
                :meth:`__post_init__`.

        Returns:
            The schedule, its window covering exactly *period_ids*.

        Raises:
            RecurrenceWindowError: An id is not one of this owner's
                materialised periods (see :meth:`__post_init__`).
            PayCalendarError: *ctx*'s saved periods do not cover an unbroken
                span; see :meth:`for_pass`.
        """
        return cls(ctx=ctx, write_period_ids=frozenset(period_ids))


__all__ = ["GenerationSchedule"]
