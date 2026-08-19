"""
Shekel Budget App -- Pay Period Lock Classifier

**The one place that decides whether a pay period may be deleted or
rebuilt**, and nothing here deletes or rebuilds anything.  Truncate and
regenerate consult it before touching a row; the settings UI renders its
result as a per-period lock badge.  Flask-isolated: takes and returns plain
data, never imports ``request`` / ``session``, and issues no write of any
kind.

**It lived inside ``pay_period_admin`` until plan step C3-a** (developer
ruling, 2026-08-10), which is where its own docstring called it "the module's
foundation" while that module's other job was the four destructive writers
built ON the foundation.  Two concerns, and the seam is sharp: everything here
answers a read-only question about a period's state, everything there acts on
the answer.  The line count is what reported it -- ``pay_period_admin`` reached
pylint's 1000-line ceiling -- but the ceiling was the symptom.

The precedence lives in :func:`_resolve_lock`, which
:func:`classify_schedule_locks` is the only caller of.  *That paragraph said
the precedence was "shared by the single-period and bulk classifiers, so the two
query strategies (scalar EXISTS vs. set membership) cannot disagree" until plan
step C2-f3b re-read it: there had been ONE query strategy since the
single-period door became a delegating wrapper (``0c7bb2a``), so the property it
claimed had no subject, and that door -- which no module under ``app/`` had
called since -- is DELETED.*

**It classifies a whole PAY CALENDAR since plan step C2-f3b**, not a list of
ORM rows.  Two things moved with that, and the second is what the first is for.

**The HISTORICAL test reads the DERIVED end.**  It compared
``budget.pay_periods.end_date`` -- a stored copy of ``lead(start_date) - 1``
that plan step **C4** drops and that nothing reconciles against the paydays it
derives from.  A period this classifier calls historical is HARD-LOCKED, so a
stale column was a paycheck the app either protected or offered to delete for
the wrong reason.  Reading it off the derivation means the lock decision and
every other "which paycheck" answer in the application come from one rule, and
that this module survives C4 untouched.

**And the DOOR takes the calendar rather than a period set, which is what makes
a wrong input unconstructible.**  A first cut of this step took an iterable of
:class:`~app.services.pay_calendar.DerivedPeriod` and REFUSED one carrying no
``period_id`` -- a projection past the owner's horizon, which would key every
such period in a call under the same ``None``.  That refusal was a fence, and
all three ``app/`` callers were passing exactly one value:
:meth:`~app.services.pay_calendar.PayCalendar.saved`.  **An argument a caller
can get wrong is a defect rather than a contract** -- the sentence
:func:`~app.services.pay_calendar._views.saved_window` already makes one layer
down -- so the argument is gone and the door reads the window itself.  There is
nothing left to refuse (adversarial design review, 2026-08-19).

**``as_of`` is REQUIRED, which is a rule about clocks rather than about
defaults.**  It defaulted to ``date.today()``, so ``regenerate_pay_periods``
read the wall clock THREE times for one decision -- benign only because a
period cannot become historical between two statements of one transaction, an
argument that holds by timing rather than by construction.  A caller now
resolves the owner's civil day ONCE and hands it down.  The value every
``app/`` caller supplies is :func:`app.utils.dates.display_today`, ruled
2026-08-19 by the developer: this decides something against the OWNER's
calendar, and the process clock is the container's (finding **balance:N-191**,
which named this function as one of the two sites that needed the ruling).
"""

import enum
import logging
from datetime import date

from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.transaction import Transaction
from app.services.pay_calendar import PayCalendar
from app.utils.balance_predicates import settled_status_ids

logger = logging.getLogger(__name__)


class PeriodLockReason(enum.Enum):
    """Why a pay period may not be deleted or rebuilt.

    A non-``None`` reason is a HARD lock: the period either is historical
    or holds irreplaceable state (settled money, posted ledger entries), and
    no operation may delete or rebuild it --
    not even with ``confirm_discard``.  **An account's balance ASSERTION is no
    longer among them, and it did not become deletable**: ruling R-EO moved the
    assertion off the pay period entirely, so a schedule operation cannot reach
    it at all (see :func:`_resolve_lock`).  ``None`` means the
    period is the mutable payload truncate / regenerate may rewrite; its
    projected and ad-hoc rows are guarded separately by the overridable
    discard gate.

    The members are ordered by precedence.  The classifier returns the
    FIRST applicable reason, so a historical period that also holds a
    settled transaction reports ``HISTORICAL``.
    """

    HISTORICAL = "historical"
    SETTLED_TXN = "settled"
    LEDGER_POSTINGS = "ledger_postings"


def _resolve_lock(
    *, is_historical: bool, has_settled: bool, has_unbalanced_ledger: bool,
) -> PeriodLockReason | None:
    """Apply the lock-reason precedence to three already-computed booleans.

    The single source of truth for the ordering, shared by the
    single-period and bulk classifiers so the two query strategies
    (scalar EXISTS vs. set membership) can never disagree on which
    reason wins.

    **``ACCOUNT_ANCHOR`` left this set at plan step X-f1c3c** (ruling R-EO),
    and it left by becoming unreachable rather than by being relaxed.  It
    refused a period an account's ``current_anchor_period_id`` pointed at; that
    column is deleted, and a balance ASSERTION no longer references a pay
    period either, so no period delete can take one.  What is still worth
    protecting is the period's POSTED state, and ``LEDGER_POSTINGS`` -- which
    outranked ``ACCOUNT_ANCHOR`` anyway -- covers it: measured on the
    developer's production data, all 10 periods holding an assertion carry an
    unbalanced ledger account, so the deleted reason was refusing nothing that
    survives without it.

    **``RECURRENCE_ANCHOR`` left the same way at plan step R7b-4.**  It
    refused a period some recurrence rule's ``start_period_id`` pointed at,
    and the hazard was real while it stood: that FK is ``ON DELETE SET NULL``,
    so deleting the period silently erased the rule's opening bound.  R7b-4
    folded the FK into ``recurrence_rules.start_date`` -- a DATE, which no
    schedule operation can cascade -- so a rule's opening bound now survives
    the deletion of any period.  The lock was protecting a bound that can no
    longer be lost, which makes it unreachable rather than relaxed.

    Args:
        is_historical: The period has already ended (``end_date`` is
            before the reference date).
        has_settled: The period holds a non-deleted settled transaction.
        has_unbalanced_ledger: The period's journal entries do NOT net to
            zero per ledger account -- posted financial state a CASCADE
            delete would mis-state (see
            :func:`_period_ids_with_unbalanced_ledger`).

    Returns:
        The first applicable :class:`PeriodLockReason`, or ``None`` when
        the period is mutable.
    """
    if is_historical:
        return PeriodLockReason.HISTORICAL
    if has_settled:
        return PeriodLockReason.SETTLED_TXN
    if has_unbalanced_ledger:
        return PeriodLockReason.LEDGER_POSTINGS
    return None


def classify_schedule_locks(
    calendar: PayCalendar, *, as_of: date,
) -> "dict[int, PeriodLockReason | None]":
    """Return ``{period_id: reason | None}`` for every SAVED period of *calendar*.

    Two set queries plus an in-memory date check -- the no-N+1 path the truncate
    and regenerate doors run before they delete anything, and the settings page
    renders as a per-period badge.

    **It takes the CALENDAR, not a period set** (plan step C2-f3b).  The result
    is keyed by ``budget.pay_periods.id``, so an unmaterialised period -- a
    projection past the owner's horizon, which carries ``period_id = None`` --
    would key every such period in one call under the same entry and collapse
    them onto each other (ledger row **P21**'s shape).  Reading
    :meth:`~app.services.pay_calendar.PayCalendar.saved` here rather than taking
    its result means no caller can supply that set at all: the door's one
    argument is the owner's whole schedule, and every value it admits is one the
    derivation produced.  The refusal a first cut of this step carried has no
    subject.

    **The HISTORICAL test reads the DERIVED end**: a period has ended when the
    day before its successor's payday is behind *as_of*.  That is the same
    figure ``budget.pay_periods.end_date`` stores, read from the derivation
    instead so this decision cannot be one a stale column moves -- and so plan
    step **C4**, which drops the column, reaches this module with nothing to
    change.

    Args:
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
            Only its SAVED periods are classified; a projection names no row to
            answer about.
        as_of: The owner's civil day, for the historical test: the period
            containing *as_of* and every later one is not historical.
            **Keyword-only and REQUIRED.**  It defaulted to ``date.today()``
            until plan step C2-f3b, which is how ``regenerate_pay_periods`` came
            to read the wall clock three times for one decision; every ``app/``
            caller now resolves :func:`app.utils.dates.display_today` once and
            threads it, which is the ruling of 2026-08-19 on the two sites
            finding **balance:N-191** named.

    Returns:
        A dict mapping each saved period's ``period_id`` to its lock reason (or
        ``None``).  Empty for an owner who has never generated a schedule -- no
        periods, no queries.

    Raises:
        PayCalendarError: *calendar*'s saved periods do not cover an unbroken
            span, which :meth:`~app.services.pay_calendar.PayCalendar.saved`
            refuses.  Unreachable through
            :func:`~app.services.pay_calendar.calendar_for`, which reads saved
            rows only.
    """
    saved = calendar.saved()
    period_ids = [period.period_id for period in saved]
    if not period_ids:
        return {}

    settled = _period_ids_with_settled_transaction(period_ids)
    unbalanced = _period_ids_with_unbalanced_ledger(period_ids)

    return {
        period.period_id: _resolve_lock(
            is_historical=period.end_date < as_of,
            has_settled=period.period_id in settled,
            has_unbalanced_ledger=period.period_id in unbalanced,
        )
        for period in saved
    }


def _period_ids_with_settled_transaction(period_ids: list[int]) -> set[int]:
    """Return the subset of ``period_ids`` holding a non-deleted settled txn."""
    rows = db.session.query(Transaction.pay_period_id).filter(
        Transaction.pay_period_id.in_(period_ids),
        Transaction.status_id.in_(settled_status_ids()),
        Transaction.is_deleted.is_(False),
    ).distinct().all()
    return {row[0] for row in rows}


def _period_ids_with_unbalanced_ledger(period_ids: list[int]) -> set[int]:
    """Return the ``period_ids`` whose entries do NOT net to zero per ledger.

    The double-entry gate of the lock classifier (the 2026-07-02 adversarial
    review's R2 defense-in-depth): ``journal_entries.pay_period_id`` is
    ``ON DELETE CASCADE``, so deleting a period disposes its entries and legs
    at the DB tier -- outside the ORM, where the balanced-journal trigger
    never fires on DELETE.  That disposal is safe ONLY when the period's
    postings net to zero per ledger account (e.g. an original + its reversal,
    which the R2 attribution rule keeps in one period): the cascade then
    removes a self-cancelling pair and no account's sum moves.  A period
    whose postings carry a NON-zero per-account net -- a loan opening /
    true-up correction, or any attribution drift -- holds posted financial
    state a cascade would silently mis-state, so it hard-locks.

    A period holding a settled transaction is already locked upstream
    (``SETTLED_TXN`` precedence); this catches the posted state settled-row
    counting cannot see.

    Args:
        period_ids: The pay-period ids being classified.

    Returns:
        The subset whose postings have a non-zero net on any ledger account.
    """
    rows = (
        db.session.query(JournalEntry.pay_period_id)
        .join(Posting, Posting.journal_entry_id == JournalEntry.id)
        .filter(JournalEntry.pay_period_id.in_(period_ids))
        .group_by(JournalEntry.pay_period_id, Posting.ledger_account_id)
        .having(db.func.sum(Posting.amount) != 0)
        .all()
    )
    return {row[0] for row in rows}
