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
commit that also moved every consumer.  **C2-d is that first consumer**: both
anchor-correction posting writers reach it through
:func:`app.services._posting_reconcile.filing_calendar_for`.  Still to come:
C2-b2 points the ten ``recurrence.calendar_for`` call sites here and deletes the
calendar they use today, and C2-c / C2-e / C2-f bring the cash view, the
projection axis and ``pay_period_service``'s readers.

**Two functions named ``calendar_for`` live under ``app.services`` until
C2-b2**, this one and ``recurrence._authoring.calendar_for``, and they return
different types (:class:`~._calendar.PayCalendar` against the recurrence arc's
``PeriodCalendar``).  Import the package rather than the bare name where the
distinction could be missed; C2-b2 deletes the other.

**Why it does NOT call ``pay_period_service.get_all_periods``**, which is the
obvious spelling and is the one this must avoid.  Plan step C2-f points that
module's six ``get_*`` readers AT this value; were the loader to read through it,
that step would close an import cycle, and the fix at that point would be to move
this module -- after ten call sites already name it.  Reading the table here
instead makes the dependency run one way for good: ``pay_period_service`` may
depend on the calendar, never the reverse.

**And why it reads only ``start_date``.**  The payday is the sole fact in the
row (``docs/plans/implementation_plan_pay_calendar.md`` section 1); ``end_date``
and ``period_index`` are derived here from it.  So this query is already written
against the schema plan step C4 leaves behind -- C4 drops both columns and does
not touch this module.

The cadence comes from ``pay_schedule_service.resolve_cadence`` rather than from
a second query of ``budget.pay_schedule``, because that function carries the
fallback for an owner with no schedule row, and a second copy of it would be a
second copy of plan finding **P8**'s circularity.
"""

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.services import pay_schedule_service

from ._calendar import PayCalendar


def calendar_for(user_id: int) -> PayCalendar:
    """Return *user_id*'s whole pay calendar, derived from their paydays.

    A separate call rather than a lookup hidden inside each consumer, so a
    caller answering many questions loads the schedule ONCE and threads it --
    ``pay_period_admin._repoint_recurrence_rules`` re-authors a batch of
    recurrence rules together, and a per-rule load there would be the same two
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
            reachable here only if a concurrent truncate lands between the two
            reads below -- or the rows cannot define a calendar, which for a
            duplicate payday ``uq_pay_periods_user_start`` already prevents.
    """
    # The CADENCE is read first, deliberately.  Both reads are separate
    # snapshots under READ COMMITTED, so a concurrent truncate can land between
    # them; in this order the loser sees a cadence and fewer paydays, which
    # derives a shorter calendar, while the other order sees paydays and no
    # cadence, which REFUSES.  Narrowing toward the answerable state is the
    # right way to lose a race a lock would otherwise have to prevent.
    cadence_days = pay_schedule_service.resolve_cadence(user_id)
    paydays = (
        db.session.query(PayPeriod.id, PayPeriod.start_date)
        .filter(PayPeriod.user_id == user_id)
        .order_by(PayPeriod.start_date)
        .all()
    )
    return PayCalendar.from_paydays(
        paydays=paydays, cadence_days=cadence_days, user_id=user_id,
    )
