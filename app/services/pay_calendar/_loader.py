"""The one door that reads an owner's pay calendar out of the database.

Plan step **C2-b1**.  Everything else in this package is pure -- a function of
values a caller supplies -- and that is load-bearing: it lets C1's harness drive
the derivation over production's real 61 paydays and over a generated sweep
without a database, and the two runs exercise the same code.  This module is the
one exception, named and isolated so the boundary is a FILE rather than a
convention, and the package's public surface hides it behind
:func:`calendar_for`.

**Nothing in ``app/`` called this until plan step C2-d**, which is the leaf
boundary C2-b1 shipped it behind -- the same technique C1 and C2-a used, so the
loader was proven on its own against a real schedule rather than inside a
commit that also moved every consumer.  **C2-d was that first consumer**: both
anchor-correction posting writers reach it through
:func:`app.services._posting_reconcile.filing_calendar_for`.  **C2-b2 then
brought the recurrence engine** -- its ten ``recurrence.calendar_for`` call
sites now name this function, and the second calendar type and second loader
they used were deleted, so ``calendar_for`` is one name under ``app.services``
again.  **C2-c brought the cash view and C2-e the projection axis**; both have
shipped, and this sentence listed them as "still to come" until plan step
C2-f2a corrected it.  What remains is ``C2-f``'s own leaves --
``pay_period_service``'s three surviving readers at every surface outside the
balance seam, which stopped importing that module at C2-f2a.

**Why it does NOT call ``pay_period_service.get_all_periods``**, which is the
obvious spelling and is the one this must avoid.  Plan step C2-f points that
module's six ``get_*`` readers AT this value; were the loader to read through it,
that step would close an import cycle, and the fix at that point would be to move
this module -- after ten call sites already name it.  Reading the table here
instead makes the dependency run one way for good: ``pay_period_service`` may
depend on the calendar, never the reverse.

**And why it reads only ``start_date``.**  The payday is the sole fact in the
row (``docs/plans/implementation_plan_pay_calendar.md`` section 1); ``end_date``
and ``period_index`` are derived here from it.  So the query is already written
against the schema plan step C4 leaves behind: C4 drops both columns and this
module needs no edit for it.  *It said C4 "does not touch this module" until
C4's FIRST commit, which ADDED :func:`calendar_at_cadence` here -- not because the
drop reached the query, but because the rolling top-up needed this read without
the cadence read in front of it.  The claim about the COLUMNS still holds; the
claim about the FILE did not.*

The cadence comes from ``pay_schedule_service.resolve_cadence`` rather than from
a second query of ``budget.pay_schedule``, because that function carries the
fallback for an owner with no schedule row, and a second copy of it would be a
second copy of plan finding **P8**'s circularity.
"""

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.services import pay_schedule_service

from ._cadence import PayCadence
from ._calendar import PayCalendar
from ._derive import PayCalendarError


def calendar_for(user_id: int) -> PayCalendar:
    """Return *user_id*'s whole pay calendar, derived from their paydays.

    A separate call rather than a lookup hidden inside each consumer, so a
    caller answering many questions loads the schedule ONCE and threads it --
    ``period_population.populate_periods_from_active_templates`` generates a
    batch of rules together, and a per-rule load there would be the same two
    queries repeated N times for one answer.

    Always the owner's COMPLETE payday set, which is
    :class:`~._calendar.PayCalendar`'s one uncheckable precondition (ledger rows
    **P14**, **P26**): a slice of paydays is indistinguishable from a short
    schedule, and deriving over one would give its last period a
    cadence-projected end and renumber every ``period_index`` from zero.  There
    is no ``first``/``count`` argument here and there must not be; a window is
    :meth:`~._calendar.PayCalendar.window`, which returns a
    :class:`~._calendar.PeriodWindow` and keeps the real ends.

    Args:
        user_id: The owning user.

    Returns:
        The frozen :class:`~._calendar.PayCalendar`.  **Empty is a legal answer,
        answered rather than refused**, and the reason is a SCHEDULED one rather
        than a present-day one.  Today a zero-payday owner is only the companion
        role, which ``require_owner`` 404s before any calendar is built, so no
        live page would 500 either way -- an adversarial review of this step
        REFUTED the ``recurrence._reading.resolved_recurrence`` citation an
        earlier draft of this docstring gave.  What makes it load-bearing is
        ``balance:X-ad`` (ruling R-DB): registration stops writing a bootstrap
        payday, so a brand-new owner holds none and reaches ``/templates`` on
        their first visit.  A raising loader would 500 that page.

    Raises:
        PayCalendarError: The owner has paydays and no resolvable cadence --
            reachable here only inside a COMMAND, and only if a concurrent
            truncate lands between the two reads below (see the comment there)
            -- or the rows cannot define a calendar, which for a duplicate
            payday ``uq_pay_periods_user_start`` already prevents.
    """
    # The CADENCE is read first, deliberately, and the nesting is what orders
    # the two reads: Python evaluates this argument before the call it feeds.
    #
    # **Whether the two reads can differ at all now depends on WHO is asking**
    # (plan step balance:X-i3, ruling R-GU).  Inside a QUERY -- every render,
    # which is where the calendar is read most -- the request's transaction is
    # one ``REPEATABLE READ`` snapshot, so both reads see one state of the
    # database and no interleaving is expressible.  Inside a COMMAND they are
    # still separate snapshots: the write doors reach here through
    # ``_posting_reconcile.filing_calendar_for`` and through generation, and a
    # CLI script or a deploy reconcile has no mode at all.
    #
    # So the order below still decides who loses the race, and only a command
    # can now lose it: in this order the loser sees a cadence and fewer
    # paydays, which derives a shorter calendar, while the other order sees
    # paydays and no cadence, which REFUSES.  Narrowing toward the answerable
    # state is the right way to lose a race a lock would otherwise have to
    # prevent.
    return calendar_at_cadence(
        user_id, pay_schedule_service.resolve_cadence(user_id),
    )


def calendar_at_cadence(
    user_id: int, cadence_days: "int | None",
) -> PayCalendar:
    """Return *user_id*'s pay calendar at a cadence the CALLER already holds.

    Plan step **C4**.  :func:`calendar_for`'s body, minus the read that
    resolves the cadence -- for a caller that has the owner's
    ``budget.pay_schedule`` row in hand and would otherwise pay for a second
    read of it.

    **One caller today and it is not a convenience** (finding **P70**):
    ``pay_period_admin._future_period_count`` counts the owner's remaining
    paychecks on the rolling top-up, which ``/grid`` and ``/dashboard`` run
    BEFORE they open their read pass -- deliberately, so that pass sees the
    rows the top-up creates.  So it can neither take a calendar off a pass nor
    call :func:`calendar_for` without re-reading a schedule row it has already
    read, and a redundant per-render schedule query is the defect ledger rows
    **P68** and **P69** record.  This door lets it pay for exactly the one
    query the ``COUNT(*)`` it replaced cost.

    **The payday read lives HERE rather than at that caller**, which is this
    module's whole reason for existing: it is the one place in the package that
    holds a session, so a second ``budget.pay_periods`` query written for a
    caller's convenience would be a second answer to "what are this owner's
    paydays" outside the boundary that makes the rest of the package pure.

    Args:
        user_id: The owning user.
        cadence_days: Days between paydays, as the caller already resolved it.
            ``None`` is legal ONLY for an owner with no paydays, which is the
            pairing :func:`~._derive.derive_periods` enforces; a caller holding
            a schedule row never has one.

    Returns:
        The frozen :class:`~._calendar.PayCalendar` over the owner's COMPLETE
        payday set -- :func:`calendar_for`'s own guarantee, for its reasons.

    Raises:
        PayCalendarError: The owner has paydays and *cadence_days* is ``None``,
            or the rows cannot define a calendar.
    """
    paydays = (
        db.session.query(PayPeriod.id, PayPeriod.start_date)
        .filter(PayPeriod.user_id == user_id)
        .order_by(PayPeriod.start_date)
        .all()
    )
    return PayCalendar.from_paydays(
        paydays=paydays, cadence_days=cadence_days, user_id=user_id,
    )


def cadence_for(user_id: int) -> PayCadence:
    """Return how often *user_id* is paid, without reading their paydays.

    Plan step **R7a-2a**.  The door for a consumer that needs the CADENCE and
    nothing else -- the savings dashboard's monthly-equivalent floor, the DTI
    denominator, the retirement gap's pre-retirement income, an investment
    limit spread over the year.  One query against ``budget.pay_schedule``
    instead of :func:`calendar_for`'s two, and no payday set to build: on
    production that is 61 rows this never loads.

    A caller that ALREADY holds a :class:`~._calendar.PayCalendar` must use
    :attr:`~._calendar.PayCalendar.cadence` instead, which answers from the
    calendar it already has.  Both reach one derivation
    (:class:`~._cadence.PayCadence`), so the two doors cannot disagree; what
    they differ in is how much of the schedule the caller needed anyway.

    **Resolve it once per PRODUCER and thread it.**  Section 4a of
    ``docs/plans/implementation_plan_recurrence_redesign.md`` says "once per
    request", and the honest statement is narrower, because two surfaces
    genuinely read it twice: the budget dashboard's tracks section runs both
    narrow savings producers and each resolves its own, and ``/retirement``
    calls ``load_gap_inputs`` from the gap producer and again from the lever
    solver.  Both ride on an already-recorded duplicate load (finding
    **N-115** for the dashboard core data), and collapsing either would mean
    resolving BEFORE those producers' early returns -- which is the defect
    ``_DashboardCoreData``'s docstring records.  The rule that matters is the
    one this door does enforce: never per ROW.  A monthly-equivalent
    conversion runs per recurring template, so a lookup inside that loop would
    be one query per row of a page that already has the answer.

    Args:
        user_id: The owning user.

    Returns:
        The owner's :class:`~._cadence.PayCadence`.

    Raises:
        PayCalendarError: The owner has no resolvable cadence -- neither a
            ``budget.pay_schedule`` row nor a pay period to infer one from.
            Refused rather than defaulted, for the reason
            :attr:`~._calendar.PayCalendar.cadence` gives: every monthly
            equivalent in the application is a function of this number, and
            assuming biweekly would report a weekly-paid owner's commitments at
            half their true value.  Since plan step X-ad-a registration writes
            the schedule row, so this names legacy data and the companion role,
            which ``require_owner`` 404s before a page builds.  It is also what
            :func:`~._derive.validate_cadence` refuses: the legacy fallback
            infers a cadence from the last period's stored length, which
            nothing bounds above (plan finding **P8**).
    """
    cadence_days = pay_schedule_service.resolve_cadence(user_id)
    if cadence_days is None:
        raise PayCalendarError(
            f"user {user_id} has no pay cadence: no budget.pay_schedule row "
            f"and no pay period to infer one from, so how many paychecks they "
            f"receive in a year is unanswerable.  Since plan step X-ad-a "
            f"registration writes the row, so this is legacy or companion "
            f"data rather than a state to default.  Assuming biweekly would "
            f"report a weekly-paid owner's commitments at half their true "
            f"monthly value."
        )
    return PayCadence(cadence_days=cadence_days)
