"""
Shekel Budget App -- Pay Period Admin Service

The structural / destructive pay-period operations -- the lock
classifier and extend / truncate / regenerate -- kept out of the heavily
imported read/generate ``pay_period_service`` so the destructive paths
live in one isolated place.  Flask-isolated: takes and returns plain
data, never imports ``request`` / ``session``; flushes, never commits
(the route owns the transaction).

The module's foundation is the single reusable **lock classifier**: the
one place that decides whether a pay period may be deleted or rebuilt.
Truncate and regenerate consult it before touching anything; the
settings UI renders its result as a per-period lock badge.  The
operations (``extend_pay_periods`` here; truncate / regenerate to
follow) build on it and on ``pay_period_service`` / ``recurrence_engine``.
"""

import enum
import logging
from datetime import date, timedelta

from app.extensions import db
from app.exceptions import ValidationError
from app.models.account import Account
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction import Transaction
from app.services import pay_period_service, pay_schedule_service
from app.services.period_population import populate_periods_from_active_templates
from app.utils.balance_predicates import settled_status_ids

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

    The single-period source of truth, used by the settings UI to badge
    one period.  Runs three scalar ``EXISTS`` checks plus an in-memory
    date comparison; the bulk path (:func:`classify_periods_bulk`) is
    the N-period optimization and shares the precedence logic via
    :func:`_resolve_lock`.

    Args:
        period: The :class:`~app.models.pay_period.PayPeriod` to classify.
        as_of: Reference date for the historical test (defaults to
            today), matching ``pay_period_service.get_current_period``:
            the period containing ``as_of`` and every later one is not
            historical.

    Returns:
        The first applicable :class:`PeriodLockReason`, or ``None``.
    """
    if as_of is None:
        as_of = date.today()
    return _resolve_lock(
        is_historical=period.end_date < as_of,
        has_settled=_holds_settled_transaction(period.id),
        is_account_anchor=_is_account_anchor(period.id),
        is_recurrence_anchor=_is_recurrence_anchor(period.id),
    )


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


def _holds_settled_transaction(period_id: int) -> bool:
    """True if the period holds a non-deleted settled (real-money) txn.

    Uses the canonical settled-status set (Paid / Received / Settled via
    ``balance_predicates.settled_status_ids``).  A soft-deleted settled
    row does NOT lock -- the user removed it -- and Credit / Cancelled
    are not settled, so they do not lock here either (they are handled by
    the separate overridable discard gate).
    """
    return db.session.query(Transaction.id).filter(
        Transaction.pay_period_id == period_id,
        Transaction.status_id.in_(settled_status_ids()),
        Transaction.is_deleted.is_(False),
    ).first() is not None


def _is_account_anchor(period_id: int) -> bool:
    """True if any account's balance anchor points at this period."""
    return db.session.query(Account.id).filter(
        Account.current_anchor_period_id == period_id,
    ).first() is not None


def _is_recurrence_anchor(period_id: int) -> bool:
    """True if any recurrence rule's start period is this period."""
    return db.session.query(RecurrenceRule.id).filter(
        RecurrenceRule.start_period_id == period_id,
    ).first() is not None


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
