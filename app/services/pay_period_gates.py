"""
Shekel Budget App -- Pay Period Gates

**The predicates that decide whether a destructive schedule change MAY
proceed**, split out of :mod:`app.services.pay_period_admin` at plan step
``pay_calendar:C14-f`` (developer ruling 2026-09-07, closing the pressure
ledger row **PC-498** recorded).

The split is the module it left already argued for, one level down.  Plan step
C3-a moved the read-only lock classifier into
:mod:`app.services.pay_period_locks` "because a read-predicate and four
destructive writers are two concerns"; the same sentence separates *deciding*
that a schedule may change from *orchestrating* the change.  What stays in
``pay_period_admin`` is the four doors -- extend, truncate, regenerate, reset;
what lives here is every gate they consult before touching a row.

**Why it happened when it did, stated rather than left to git blame.**
``pay_period_admin`` stood at 996 of pylint's 1,000-line ceiling, and
``C14-f``'s gap confirmation needed eleven lines of it -- a parameter, its
threading and the ``Args`` entry ``W9015`` requires.  **PC-498 named that
remedy a SPLIT and not a trim**, because answering a ceiling by deleting
argument is how a module loses the reasoning that keeps it correct, and it
named the PLACEMENT the developer's rather than the next session's to assume
(the precedent is **R-PC60**).  He placed it here.  *That confirmation --
``reject_unconfirmed_gap``, ``Confirmations.gap``, ``PayPeriodGapRequired``
and the banner -- was DELETED whole at plan step ``pay_calendar:C17-c-2a``
(ruling **R-PC67**): a hole in the schedule is refused by
``pay_period_batch.reject_skipped_paycheck``, beside the floor, rather than
confirmed.  The one overridable gate left is the discard one, and the doors
take it as ``confirm_discard``.*

**These names are PUBLIC and were private before the move.**  A function
another module calls is part of this module's surface; keeping the underscore
would have made every call site read as a privacy breach and taught the next
reader that the boundary does not mean anything.  The behaviour is unchanged
-- this is a move and a rename, and nothing here computes differently than it
did inside ``pay_period_admin``.

**The dependency runs ONE WAY**: this module imports nothing from
``pay_period_admin``, and that is a property rather than an accident -- a gate
that called a door would be the cycle the C3-a split exists to prevent.
"""

import logging
from datetime import date

from sqlalchemy import or_

from app.exceptions import (
    PayPeriodDiscardRequired,
    PayPeriodLocked,
)
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services._recurrence_common import log_resource_access_denied
from app.services.pay_calendar import DerivedPeriod, PeriodWindow
from app.services.pay_period_locks import PeriodLockReason
from app.utils.balance_predicates import is_projected_clause, settled_status_ids
from app.utils.log_events import ACCESS, EVT_RESOURCE_NOT_FOUND, log_event

logger = logging.getLogger(__name__)


def log_unresolved_period(user_id: int, period_id: int) -> None:
    """Emit the F-144 access-denied trail for an id that resolved to nothing.

    **A new ownership-failure branch owes an ACCESS event**, which is the
    invariant ``utils.auth_helpers`` states for its own doors ("every
    ownership-failure branch in this module emits a structured ``log_event`` so
    probing patterns surface in dashboards and SOC alerting") and which plan
    step C3-a's first cut left silent: an authenticated owner sweeping ids
    through ``POST /pay-periods/truncate`` was correctly refused every time and
    recorded nowhere.

    The two branches ``get_or_404`` distinguishes are distinguished HERE too,
    and for its reasons: a missing pk is common in normal use -- the
    discard-confirm panel re-posts an id a concurrent truncate has since
    deleted -- so it is INFO, while a pk owned by somebody else is the IDOR
    signal and is WARNING.  **Splitting the LOG is not an oracle**: the caller
    raises one message for both, and an event goes to the log rather than to
    the response.

    ``get_or_404`` itself cannot be reused: it reads ``current_user`` and
    ``request.path``, and this module may not import Flask.  The cross-user
    half therefore goes through ``_recurrence_common.log_resource_access_denied``,
    the Flask-free helper built for exactly this shape, rather than through a
    second copy of its fixed keyword set.

    Args:
        user_id: The requesting owner -- never the row's owner.
        period_id: The submitted ``budget.pay_periods.id`` that resolved to
            none of *user_id*'s periods.
    """
    owner_id = (
        db.session.query(PayPeriod.user_id)
        .filter(PayPeriod.id == period_id)
        .scalar()
    )
    if owner_id is None:
        log_event(
            logger, logging.INFO,
            EVT_RESOURCE_NOT_FOUND, ACCESS,
            "Pay-period truncate named a non-existent primary key",
            user_id=user_id, model="PayPeriod", pk=period_id,
        )
        return
    log_resource_access_denied(
        logger, user_id=user_id, model="PayPeriod", pk=period_id,
        owner_id=owner_id,
    )



def gate_deletable_tail(
    periods: PeriodWindow,
    kept: "DerivedPeriod | None",
    confirm_discard: bool,
    locks: "dict[int, PeriodLockReason | None]",
) -> "list[DerivedPeriod]":
    """Return the periods after *kept*, having refused if any may not go.

    The shared gate of truncate: :func:`truncate_pay_periods` reaches it with
    a period resolved from a submitted id, and :func:`regenerate_pay_periods`
    with one it computed itself.  Both refusals live here so the two callers
    cannot drift on which rows a truncate protects -- the split is plan step
    C3-a's, made because only ONE of them takes user input and so only one has
    an id to refuse.

    **It DECIDES; it does not delete** (plan step C3-b).  The removal is
    ``pay_period_write``'s, which carries it out beside whatever the same
    operation records.  That split is what lets regenerate be ONE write: the
    gate runs here, and the writer sees the operation's final payday set rather
    than the half-applied one an adversarial review caught it refusing against.

    *The composition had a SECOND reason until plan step ``pay_calendar:C4-c``,
    and it is gone rather than merely unmentioned.*  While the two derived
    columns were stored, a tail delete left the new last period holding its old
    successor's end -- paydays ``[Jan 2, Jan 16, Feb 11]`` truncated through
    Jan 16 kept a stored end of Feb 10 where the derivation says Jan 29 -- so
    the writer re-materialised what survived, and getting that for free was
    half the argument for one call.  C4-c dropped the columns: a delete removes
    rows and moves no value anywhere (``retire_paydays``' own docstring states
    it), so what is left is the refusals, and they are enough.

    **The tail is defined by PAYDAY, not by ordinal**, and that is the same
    normalization the arc is about: ``start_date`` is the only fact in the row,
    ``period_index`` is derived from it, and plan step C4-c dropped the ordinal
    altogether.  Since plan step C3-b the two select the same rows by
    construction rather than by data: ``pay_period_write`` is the only writer
    of this table and reads the ordinal off the derivation, where it IS the
    position in payday order (0 disagreements on 61 production rows,
    2026-08-10).

    **But this function does not RELY on that, and an adversarial review of
    C3-a is why.**  Its first cut took the boundary from
    :func:`regenerate_keep_through_period`, which picked by LIST POSITION over
    a list ``get_all_periods`` ordered by ``period_index`` -- so the boundary
    was chosen in ordinal space and applied in payday space, and the two
    agreeing was an unfenced assumption about data rather than a property of
    the code.  That helper sorted by payday to close it, and plan step C2-f3b
    deleted even the sort: a :class:`~app.services.pay_calendar.PeriodWindow`
    is ordered at construction, so neither function can be handed an order to
    get wrong.  This one is a filter and reads no order at all.

    **The LOCKS arrive as an argument** (plan step C2-f3b), and that is what
    lets a door read them once.  This function classified its own tail while
    ``regenerate_pay_periods`` had already classified the whole schedule to
    find where that tail opens -- two classifies and two independent
    ``date.today()`` reads for one decision.  Taking the map means the caller
    resolves the owner's day once and both consumers read the same answer.

    Args:
        periods: The owner's saved periods as one window, read under the
            caller's advisory lock.
        kept: The last period to KEEP, or ``None`` to delete every period in
            *periods*.  ``None`` is reachable only from regenerate, whose
            rebuildable tail can start at the very first period; it then
            generates a fresh schedule inside the same transaction, so no
            committed state is ever period-less.
        confirm_discard: When True, proceed past the discard gate.
        locks: The caller's lock classification, covering at least *periods*.
            Indexed rather than ``.get``-ed below: a to-delete period missing
            from it is a caller that classified a different set, and treating
            the miss as "unlocked" is the direction that deletes settled money.

    Returns:
        The periods that may be deleted, empty when *kept* is already the last.

    Raises:
        PayPeriodLocked: A to-delete period is hard-locked.
        PayPeriodDiscardRequired: A to-delete period holds unrecoverable
            rows and ``confirm_discard`` is False.
    """
    if kept is None:
        to_delete = list(periods)
    else:
        to_delete = [p for p in periods if p.start_date > kept.start_date]
    if not to_delete:
        return []

    blocking = {
        period.period_id: locks[period.period_id]
        for period in to_delete
        if locks[period.period_id] is not None
    }
    if blocking:
        # Posting-ledger protection, two layers: a period holding settled
        # (posted) transactions classifies SETTLED_TXN, and a period whose
        # journal entries do not net to zero per ledger account (a loan
        # opening / true-up correction, or attribution drift) classifies
        # LEDGER_POSTINGS -- so truncate refuses both.
        # journal_entries.pay_period_id is ON DELETE CASCADE, so deleting a
        # posted period disposes its ledger entries + legs at the DB tier
        # (outside the ORM, where the balanced-journal trigger never fires on
        # DELETE); that is safe only for a period whose postings net to zero
        # per account (a self-cancelling original + reversal pair).  Whoever
        # relaxes these locks MUST first reverse the postings
        # (posting_service.reverse_postings_before_delete / the loan sync).
        raise PayPeriodLocked(blocking)

    if not confirm_discard:
        discardable = count_discardable_items(
            [period.period_id for period in to_delete],
        )
        if discardable > 0:
            raise PayPeriodDiscardRequired(discardable)

    return to_delete



def can_reset_pay_periods(user_id: int) -> bool:
    """Return whether a full reset is currently offered to the user.

    The read-only UI predicate: reset is offered only when the user has no
    settled transactions, the same bound :func:`reset_pay_periods`
    enforces.  The settings page calls this to show or hide the reset
    control; the service's own gate (which raises
    :class:`~app.exceptions.PayPeriodResetBlocked`) remains the
    authoritative defense, so a stale page that posts anyway is still
    refused.

    Args:
        user_id: The owning user's id.

    Returns:
        ``True`` when the user has zero settled (non-deleted)
        transactions, else ``False``.
    """
    return settled_transaction_count(user_id) == 0



def regenerate_keep_through_period(
    periods: PeriodWindow,
    locks: "dict[int, PeriodLockReason | None]",
    as_of: date,
) -> "DerivedPeriod | None":
    """Return the last period regenerate KEEPS, or ``None`` to keep none.

    Everything up to and including the last period that has already started or
    is locked is kept; the first NOT-YET-STARTED AND unlocked period is where
    the rebuildable tail begins, so this returns the period BEFORE it.  "Not
    yet started" is ``start_date > as_of`` STRICTLY: a period whose
    ``start_date == as_of`` is the current in-progress period -- the same
    inclusive bound :meth:`~app.services.pay_calendar.PayCalendar
    .period_containing` applies, and which ``get_current_period`` applied in
    SQL until plan step C2-f3a deleted it -- so on a payday it is kept, not
    rebuilt.  When there is no rebuildable future tail
    (every period has started or is locked), it returns the LAST period -- the
    truncate is then a no-op and regenerate degrades to an append from
    ``new_start_date``.

    **It returns a PERIOD rather than an ordinal, and ``None`` rather than
    ``-1``** (plan step C3-a).  The sentinel had to be a number below every
    real ``period_index`` because the truncate it fed compared ordinals; with
    :func:`gate_deletable_tail` comparing PAYDAYS there is no "one before the
    first payday" to name, and inventing one would be an ordinal surviving in
    a function whose whole point is that ordinals do not.

    **It walked in PAYDAY order because an adversarial review of C3-a found it
    walking in ORDINAL order; plan step C2-f3b deleted the sort that fixed
    that.**  The first cut walked ``periods`` as handed over -- which
    ``pay_period_service.get_all_periods`` ordered by ``period_index`` -- and
    returned ``periods[position - 1]``, so the boundary was chosen in ordinal
    space while the delete that consumes it selects in payday space.  C3-a
    sorted here, which removed the assumption by asserting the order at one
    site; a :class:`~app.services.pay_calendar.PeriodWindow` is sorted at
    construction, so there is no order left for a caller to state and the sort
    had nothing to correct.  A fence deleted by making its subject
    unconstructible, which is what this arc is for.

    Args:
        periods: The owner's saved periods as one window, read under the
            caller's advisory lock.  Taken as an argument rather than
            re-queried so the boundary and the delete that consumes it see one
            snapshot.
        locks: The caller's lock classification, covering *periods*.  Taken for
            the reason :func:`gate_deletable_tail` takes it: this function and
            that one used to classify separately, against two independently
            read clocks.  **Only two of its three reasons are reachable here**,
            and the third is unreachable by proof rather than by data: a
            candidate satisfies ``start_date > as_of``, a derived period always
            has ``end_date >= start_date``, so no candidate can satisfy
            ``end_date < as_of``.  What can keep a not-yet-started period out of
            the rebuildable tail is settled money or posted ledger entries,
            never HISTORICAL.
        as_of: The owner's civil day, resolved once by the caller.

    Returns:
        The last :class:`~app.services.pay_calendar.DerivedPeriod` to keep, or
        ``None`` when the rebuildable tail starts at the very first period (or
        the owner has no periods at all) -- both meaning "keep none of them".
    """
    if not periods:
        return None
    for position, period in enumerate(periods):
        if period.start_date > as_of and locks[period.period_id] is None:
            return periods[position - 1] if position > 0 else None
    return periods[-1]



def count_discardable_items(period_ids):
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



def settled_transaction_count(user_id: int) -> int:
    """Count the user's non-deleted settled transactions (the reset gate).

    Scopes through :class:`PayPeriod` because ``Transaction`` carries no
    ``user_id`` of its own.  "Settled" reuses the canonical
    ``balance_predicates.settled_status_ids`` (Paid or Received)
    and excludes soft-deleted rows -- exactly how the lock classifier
    decides a period is settled, so a row that does not lock a period also
    does not block a reset.  A settled transfer is counted via its settled
    shadow transactions (transfer invariant 3: a shadow's status equals
    its parent's), so no separate transfer scan is needed.

    Args:
        user_id: The owning user's id.

    Returns:
        The number of settled, non-deleted transactions the user has.
    """
    return (
        db.session.query(Transaction.id)
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(
            PayPeriod.user_id == user_id,
            Transaction.status_id.in_(settled_status_ids()),
            Transaction.is_deleted.is_(False),
        )
        .count()
    )
