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

The precedence lives in :func:`_resolve_lock` and is shared by the
single-period and bulk classifiers, so the two query strategies (scalar
EXISTS vs. set membership) cannot disagree about which reason wins.
"""

import enum
import logging
from datetime import date

from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.transaction import Transaction
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


def classify_period_lock(period, as_of: date | None = None) -> PeriodLockReason | None:
    """Return the first reason ``period`` is locked, or ``None`` if mutable.

    The single-period public API, used by the settings UI to badge one
    period.  Delegates to :func:`classify_periods_bulk` over a one-element
    list so the lock rules have exactly ONE encoding -- the set queries
    plus the :func:`_resolve_lock` precedence -- and the single-period and
    bulk paths can never drift apart on this spine-critical classifier.

    Args:
        period: The :class:`~app.models.pay_period.PayPeriod` to classify.
        as_of: Reference date for the historical test (defaults to
            today), matching ``pay_period_service.get_current_period``:
            the period containing ``as_of`` and every later one is not
            historical.

    Returns:
        The first applicable :class:`PeriodLockReason`, or ``None``.
    """
    return classify_periods_bulk([period], as_of=as_of)[period.id]


def classify_periods_bulk(
    periods, as_of: date | None = None,
) -> dict[int, PeriodLockReason | None]:
    """Classify many periods with set queries instead of N x 3 scalar ones.

    Returns ``{period.id: PeriodLockReason | None}`` identical to calling
    :func:`classify_period_lock` on each period, but with two set
    queries total plus the in-memory date check -- the no-N+1 path the
    truncate operation runs over its to-delete window.

    Args:
        periods: The :class:`~app.models.pay_period.PayPeriod` objects to
            classify.
        as_of: Reference date for the historical test (defaults to today).

    Returns:
        A dict mapping each period's id to its lock reason (or ``None``).
    """
    if as_of is None:
        as_of = date.today()
    period_ids = [p.id for p in periods]
    if not period_ids:
        return {}

    settled = _period_ids_with_settled_transaction(period_ids)
    unbalanced = _period_ids_with_unbalanced_ledger(period_ids)

    return {
        period.id: _resolve_lock(
            is_historical=period.end_date < as_of,
            has_settled=period.id in settled,
            has_unbalanced_ledger=period.id in unbalanced,
        )
        for period in periods
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
