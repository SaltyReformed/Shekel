"""
Shekel Budget App -- Pay Period Admin Service

The structural / destructive pay-period operations -- the lock
classifier and extend / truncate / regenerate -- kept out of the heavily
imported read/generate ``pay_period_service`` so the destructive paths
live in one isolated place.  Flask-isolated: takes and returns plain
data, never imports ``request`` / ``session``; flushes / bulk-deletes,
never commits (the route owns the transaction).

The module's foundation is the single reusable **lock classifier**: the
one place that decides whether a pay period may be deleted or rebuilt.
Truncate and regenerate consult it before touching anything; the
settings UI renders its result as a per-period lock badge.  The
operations build on it and on ``pay_period_service`` /
``period_population``.
"""

import enum
import logging
from datetime import date, timedelta

from sqlalchemy import or_

from app.exceptions import (
    PayPeriodDiscardRequired,
    PayPeriodLocked,
    ValidationError,
)
from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import pay_period_service, pay_schedule_service
from app.services.period_population import populate_periods_from_active_templates
from app.utils.balance_predicates import is_projected_clause, settled_status_ids

logger = logging.getLogger(__name__)


class PeriodLockReason(enum.Enum):
    """Why a pay period may not be deleted or rebuilt.

    A non-``None`` reason is a HARD lock: the period either is historical
    or holds irreplaceable state (settled money, an account's balance
    anchor, a recurrence rule's origin), and no operation may delete or
    rebuild it -- not even with ``confirm_discard``.  ``None`` means the
    period is the mutable payload truncate / regenerate may rewrite; its
    projected and ad-hoc rows are guarded separately by the overridable
    discard gate.

    The members are ordered by precedence.  The classifier returns the
    FIRST applicable reason, so a historical period that also holds a
    settled transaction reports ``HISTORICAL``, and a future settled
    period that is also an anchor reports ``SETTLED_TXN``.
    """

    HISTORICAL = "historical"
    SETTLED_TXN = "settled"
    ACCOUNT_ANCHOR = "account_anchor"
    RECURRENCE_ANCHOR = "recurrence_anchor"


def _resolve_lock(
    *, is_historical: bool, has_settled: bool,
    is_account_anchor: bool, is_recurrence_anchor: bool,
) -> PeriodLockReason | None:
    """Apply the lock-reason precedence to four already-computed booleans.

    The single source of truth for the ordering, shared by the
    single-period and bulk classifiers so the two query strategies
    (scalar EXISTS vs. set membership) can never disagree on which
    reason wins.

    Args:
        is_historical: The period has already ended (``end_date`` is
            before the reference date).
        has_settled: The period holds a non-deleted settled transaction.
        is_account_anchor: An account's ``current_anchor_period_id``
            points at the period.
        is_recurrence_anchor: A recurrence rule's ``start_period_id``
            points at the period.

    Returns:
        The first applicable :class:`PeriodLockReason`, or ``None`` when
        the period is mutable.
    """
    if is_historical:
        return PeriodLockReason.HISTORICAL
    if has_settled:
        return PeriodLockReason.SETTLED_TXN
    if is_account_anchor:
        return PeriodLockReason.ACCOUNT_ANCHOR
    if is_recurrence_anchor:
        return PeriodLockReason.RECURRENCE_ANCHOR
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
    :func:`classify_period_lock` on each period, but with three set
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
    anchors = _period_ids_that_are_account_anchors(period_ids)
    rule_anchors = _period_ids_that_are_recurrence_anchors(period_ids)

    return {
        period.id: _resolve_lock(
            is_historical=period.end_date < as_of,
            has_settled=period.id in settled,
            is_account_anchor=period.id in anchors,
            is_recurrence_anchor=period.id in rule_anchors,
        )
        for period in periods
    }


def extend_pay_periods(user_id, num_periods, cadence_days=None):
    """Append ``num_periods`` pay periods to the end of the user's schedule.

    Tail-append only: the new periods take the highest ``period_index``
    values, so the ``period_index == calendar-order`` invariant the
    balance resolver relies on is preserved (only tail-append and
    tail-truncate do).  ``generate_pay_periods`` creates the new periods
    EMPTY -- it does not run the recurrence engine -- so they are then
    repopulated with each active template's recurring rows.

    Args:
        user_id: The owning user's id.
        num_periods: How many periods to append (>= 1; the route's
            schema validates the range).
        cadence_days: Days between paydays for the new periods.  Defaults
            to the user's resolved cadence (the stored schedule, else
            inferred from the last period's length).

    Returns:
        The list of newly created :class:`~app.models.pay_period.PayPeriod`
        objects.

    Raises:
        ValidationError: When the user has no existing periods to extend
            from (they must generate first), or when
            ``generate_pay_periods`` rejects the batch via its
            forward-only overlap guard.
    """
    existing = pay_period_service.get_all_periods(user_id)
    if not existing:
        raise ValidationError(
            "Generate your first pay-period schedule before extending it."
        )

    if cadence_days is None:
        cadence_days = pay_schedule_service.resolve_cadence(user_id)

    last = existing[-1]
    next_start = last.end_date + timedelta(days=1)
    new_periods = pay_period_service.generate_pay_periods(
        user_id, next_start, num_periods, cadence_days,
    )
    populate_periods_from_active_templates(user_id, new_periods)
    return new_periods


def truncate_pay_periods(user_id, keep_through_index, confirm_discard=False):
    """Delete the schedule tail beyond ``keep_through_index``.

    Removes every pay period whose ``period_index`` is greater than
    ``keep_through_index`` (tail-truncate preserves the index==calendar
    invariant; only tail ops do).  Two gates protect real data, checked
    in order before anything is deleted:

      1. **Hard locks (not overridable).** If any to-delete period is
         historical, holds a settled transaction, or is an account /
         recurrence anchor, raise :class:`PayPeriodLocked` and delete
         nothing.
      2. **Discard gate (overridable).** If any to-delete period holds a
         row regeneration cannot reproduce -- hand-entered, override, or
         Credit/Cancelled -- and ``confirm_discard`` is False, raise
         :class:`PayPeriodDiscardRequired` and delete nothing.

    Deletion is a single bulk ``DELETE`` so PostgreSQL performs the whole
    cascade in one pass: transactions, transfers (and both shadows,
    preserving the transfer invariant), and anchor history all go, with
    ``recurrence_rules.start_period_id`` set NULL; DB-level audit triggers
    still fire.  Per-object ``session.delete()`` would instead trip
    SQLAlchemy's nullify-on-disassociate against the NOT NULL
    ``transactions.pay_period_id`` and raise before the DB cascade fires.
    ``expire_all`` then drops the now-stale identity map.

    Args:
        user_id: The owning user's id.
        keep_through_index: The highest ``period_index`` to KEEP; every
            higher index is deleted.
        confirm_discard: When True, proceed past the discard gate (the
            user has acknowledged the loss).  Hard locks are never
            bypassed.

    Returns:
        The number of pay periods deleted (0 when the tail is already at
        or below ``keep_through_index`` -- an idempotent no-op).

    Raises:
        PayPeriodLocked: A to-delete period is hard-locked.
        PayPeriodDiscardRequired: A to-delete period holds unrecoverable
            rows and ``confirm_discard`` is False.
    """
    to_delete = [
        p for p in pay_period_service.get_all_periods(user_id)
        if p.period_index > keep_through_index
    ]
    if not to_delete:
        return 0

    locks = classify_periods_bulk(to_delete)
    blocking = {
        pid: reason for pid, reason in locks.items() if reason is not None
    }
    if blocking:
        raise PayPeriodLocked(blocking)

    period_ids = [p.id for p in to_delete]
    if not confirm_discard:
        discardable = _count_discardable_items(period_ids)
        if discardable > 0:
            raise PayPeriodDiscardRequired(discardable)

    deleted = (
        db.session.query(PayPeriod)
        .filter(PayPeriod.id.in_(period_ids))
        .delete(synchronize_session=False)
    )
    db.session.expire_all()
    return deleted


def regenerate_pay_periods(
    user_id, new_start_date, num_periods, cadence_days, confirm_discard=False,
):
    """Rebuild the not-yet-started, unlocked tail from a corrected start.

    "Fix a mistake" without per-period date editing: truncate the
    rebuildable future tail (the first not-yet-started unlocked period
    onward), then generate a fresh ``num_periods``-long schedule from
    ``new_start_date`` at ``cadence_days`` and repopulate it with the
    active templates' recurring rows.  Periods that have already started,
    are historical, hold settled money, or anchor an account / rule are
    KEPT; if any such locked period sits inside the rebuildable tail the
    truncate step refuses (history cannot be rewritten under a settled
    paycheck).  The new cadence is persisted so later extends continue at
    it.

    The whole operation is one transaction the route commits: if the
    generate step rejects ``new_start_date`` after the truncate has run,
    the route's rollback undoes the truncate too -- nothing partial ships.

    Args:
        user_id: The owning user's id.
        new_start_date: First payday of the rebuilt tail.  Must fall after
            the last RETAINED period's ``end_date`` (re-checked by
            ``generate_pay_periods``' forward-only guard).
        num_periods: How many periods to generate.
        cadence_days: Days between paydays for the rebuilt tail; also
            persisted as the user's schedule cadence.
        confirm_discard: Forwarded to the truncate step -- when False and
            the rebuildable tail holds unrecoverable rows, raise
            :class:`PayPeriodDiscardRequired` and change nothing.

    Returns:
        The list of newly created :class:`~app.models.pay_period.PayPeriod`
        objects.

    Raises:
        PayPeriodLocked: A locked period sits inside the rebuildable tail.
        PayPeriodDiscardRequired: The tail holds unrecoverable rows and
            ``confirm_discard`` is False.
        ValidationError: ``new_start_date`` overlaps or predates the
            retained schedule (``generate_pay_periods``' forward-only guard).
    """
    keep_through = _regenerate_keep_through_index(user_id)
    truncate_pay_periods(user_id, keep_through, confirm_discard=confirm_discard)
    new_periods = pay_period_service.generate_pay_periods(
        user_id, new_start_date, num_periods, cadence_days,
    )
    populate_periods_from_active_templates(user_id, new_periods)
    pay_schedule_service.upsert_schedule(user_id, cadence_days)
    return new_periods


def _regenerate_keep_through_index(user_id):
    """Return the ``keep_through_index`` regenerate truncates to.

    Everything up to and including the last period that has already
    started or is locked is kept; the first NOT-YET-STARTED AND unlocked
    period is where the rebuildable tail begins, so this returns that
    period's index minus one.  "Not yet started" is ``start_date >
    today`` STRICTLY: a period whose ``start_date == today`` is the
    current in-progress period (``get_current_period`` matches
    ``start_date <= today <= end_date``), so on a payday it is kept, not
    rebuilt.  When there is no rebuildable future tail (every period has
    started or is locked), it returns the last index -- the truncate is
    then a no-op and regenerate degrades to an append from
    ``new_start_date``.  With no periods at all, it returns -1.

    Args:
        user_id: The owning user's id.

    Returns:
        The highest ``period_index`` to keep.
    """
    periods = pay_period_service.get_all_periods(user_id)
    if not periods:
        return -1
    today = date.today()
    locks = classify_periods_bulk(periods)
    for period in periods:
        if period.start_date > today and locks[period.id] is None:
            return period.period_index - 1
    return periods[-1].period_index


def _count_discardable_items(period_ids):
    """Count rows in the periods that regeneration cannot reproduce.

    A row needs the user's confirmation before truncate / regenerate
    wipes it when it is hand-entered (no template), a manual override, or
    carries a deliberate non-Projected status (Credit / Cancelled --
    settled rows are already hard-locked upstream, so they never reach
    here).  Transfer shadows always carry ``template_id IS NULL``, so the
    transaction scan excludes them (``transfer_id IS NULL``) and transfers
    are counted once on their own table via the parallel predicate
    (``transfer_template_id`` in place of ``template_id``).  That way a
    recurring transfer (regenerable) does not falsely trip the gate while
    an ad-hoc transfer does.  The not-Projected test routes through
    ``balance_predicates.is_projected_clause`` (negated) so no inline
    status-id comparison lives here (D6-09).

    Args:
        period_ids: The pay-period ids being deleted.

    Returns:
        The number of unrecoverable rows (non-shadow transactions plus
        transfers; a transfer counts once, not its two shadows).
    """
    txn_count = db.session.query(Transaction.id).filter(
        Transaction.pay_period_id.in_(period_ids),
        Transaction.is_deleted.is_(False),
        Transaction.transfer_id.is_(None),
        or_(
            Transaction.template_id.is_(None),
            Transaction.is_override.is_(True),
            ~is_projected_clause(Transaction),
        ),
    ).count()
    transfer_count = db.session.query(Transfer.id).filter(
        Transfer.pay_period_id.in_(period_ids),
        Transfer.is_deleted.is_(False),
        or_(
            Transfer.transfer_template_id.is_(None),
            Transfer.is_override.is_(True),
            ~is_projected_clause(Transfer),
        ),
    ).count()
    return txn_count + transfer_count


def _period_ids_with_settled_transaction(period_ids: list[int]) -> set[int]:
    """Return the subset of ``period_ids`` holding a non-deleted settled txn."""
    rows = db.session.query(Transaction.pay_period_id).filter(
        Transaction.pay_period_id.in_(period_ids),
        Transaction.status_id.in_(settled_status_ids()),
        Transaction.is_deleted.is_(False),
    ).distinct().all()
    return {row[0] for row in rows}


def _period_ids_that_are_account_anchors(period_ids: list[int]) -> set[int]:
    """Return the subset of ``period_ids`` that are an account's anchor."""
    rows = db.session.query(Account.current_anchor_period_id).filter(
        Account.current_anchor_period_id.in_(period_ids),
    ).distinct().all()
    return {row[0] for row in rows}


def _period_ids_that_are_recurrence_anchors(period_ids: list[int]) -> set[int]:
    """Return the subset of ``period_ids`` that are a rule's start period."""
    rows = db.session.query(RecurrenceRule.start_period_id).filter(
        RecurrenceRule.start_period_id.in_(period_ids),
    ).distinct().all()
    return {row[0] for row in rows}
