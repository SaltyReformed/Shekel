"""
Shekel Budget App -- The TRANSACTION arm of the outstanding set

One of the package's arms (see :mod:`app.services.reconcile_service` for what
an arm is and how many there are): the SOURCE ROWS a statement can still
settle -- an envelope's own close, and a bill -- as opposed to the purchase
entries recorded against one, or a transfer's shadow.  It owns the three things
an arm owns, its SCOPE, its READ and its WRITE.

**Its scope, bound and loader are :mod:`._rows`', and that is finding N-225.**
This arm and the transfer arm ask one question of one table and differ only in
which rows are theirs; the shared half lives once, and the reader and the
writer here still share it literally, which is the security property.  What
stays here is what is genuinely this arm's: WHICH rows (``transfer_id IS
NULL``), what one is WORTH, and what a tick MEANS for it.

**Its settle is a service verb, and that is the difference from the purchase
arm.**  A purchase settles by stamping one column and moves no status, so that
arm's writer is a bulk ``UPDATE``.  A transaction settles through the status
seam, an amount rule and a posting reconcile -- so this writer dispatches to
``transaction_service.settle_transaction`` per row, which is the verb the
grid's Mark Paid calls (ruling **R-FA**).  Two doors restating one money rule
is this arc's own root cause 1.

**Nothing here decides what a tick BOOKS, whether the panel may offer a box for
it, or whether a submitted figure is a CORRECTION.**  All three are the verb's,
published as ``transaction_service.settle_amount``,
``transaction_service.settles_from_entries`` and
``transaction_service.is_correction``, and read from here.  A panel showing a
figure the verb would not book, an input for a value the verb would ignore, or
a telemetry count of corrections the verb never made, are the same defect one
tier up.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, frozen dataclasses out.
  - All monetary arithmetic uses :class:`~decimal.Decimal`.
  - The writer mutates and does NOT commit -- the caller owns the session
    boundary.
"""

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import selectinload

from app.models.transaction import Transaction
from app.services import transaction_service
from app.services.reconcile_service import _rows
from app.services.reconcile_service._offers import (
    OfferKind,
    OutstandingTransaction,
)
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSACTIONS_RECONCILED,
    log_event,
)

logger = logging.getLogger(__name__)

#: This arm's shape (:class:`app.services.reconcile_service._rows.Arm`).
#:
#: ``transfer_id IS NULL`` -- a transfer shadow settles through
#: ``transfer_service.update_transfer`` so both legs and the parent move
#: together (``CLAUDE.md`` transfer invariant 3), and
#: ``transaction_service.settle_transaction`` REFUSES one, so admitting it here
#: would turn a design boundary into a 400.  It is the transfer arm's
#: (:mod:`._transfers`), whose own clause is this one's complement -- the two
#: partition the table, which is why neither is a default.
#:
#: ``template`` is loaded here and not in the shared loader because only this
#: arm reads ``tracks_purchases``, which lazy-loads a template per row
#: otherwise -- an N+1 on a list the user is about to read.
_ARM = _rows.Arm(
    kind_clauses=(Transaction.transfer_id.is_(None),),
    load_options=(selectinload(Transaction.template),),
)


def _offer_kind(txn: Transaction) -> OfferKind:
    """Return the section tag this arm puts on *txn*'s offer.

    **The arm TAGS; nothing downstream derives** (see :class:`OfferKind` for
    the two defects deriving it caused, one of them a live mis-captioning of
    production's `$1,958.87` FSA reimbursement).

    Three arms of one rule, in this order:

    * INCOME is a ``DEPOSIT`` -- money arriving, which ruling **R-FD** counts
      apart from payments because a deposit and a bill do not sum to anything a
      reader wants.  Tested FIRST because an income row is never
      purchase-tracked anyway (both entry write doors are expense-only), so the
      order costs nothing and states the priority.
    * A purchase-tracked row is an ``ENVELOPE``, whether or not it currently
      holds anything.  Production's `Kayla's Spending Money` carries zero
      entries and is still an envelope; calling it a bill because it happens to
      be correctable was the renderer's proxy talking.
    * Everything else is a ``BILL``.

    Args:
        txn: The row being offered.

    Returns:
        Its :class:`OfferKind`.
    """
    if txn.is_income:
        return OfferKind.DEPOSIT
    if txn.tracks_purchases:
        return OfferKind.ENVELOPE
    return OfferKind.BILL


def _outstanding_rows(
    owner_id: int,
    account_id: int,
    observed_on: date,
    *,
    transaction_ids: "set[int] | None" = None,
) -> "list[Transaction]":
    """Return the rows this arm offers, both halves of the scope applied.

    This arm's shape handed to the shared loader.  It is a named function
    rather than two call sites naming :data:`_ARM` themselves, so the reader
    and the writer below cannot come to ask for different rows -- which is the
    sharing property stated at the arm level.

    Args:
        owner_id: The user_id whose rows to scope to.
        account_id: The cash account whose balance was asserted.
        observed_on: The civil day that balance was true for.
        transaction_ids: The writer's narrowing; ``None`` is the reader's
            "everything in scope".

    Returns:
        The matching rows, ordered by landing day then id.
    """
    return _rows.outstanding_rows(
        _ARM, owner_id, account_id, observed_on,
        transaction_ids=transaction_ids,
    )


def outstanding_transactions(
    owner_id: int, account_id: int, observed_on: date,
) -> "dict[int, OutstandingTransaction]":
    """Return this arm's offers, ``{transaction id: offer}``.

    The source rows this account is still holding forward on the day the
    balance was asserted: an envelope whose own close has not been ticked, and
    a bill the projection is still carrying.  Ticking one records that the bank
    moved the money by that day (:func:`record_settled_transactions`).

    **It returns a MAP keyed on the PARENT, which is what lets the assembler
    union it with the purchase arm** -- that arm keys its purchases on the same
    id, so an envelope with outstanding purchases AND an offerable close is ONE
    block carrying both, which is ruling **R-EW**'s shape.

    Reads only (no writes, no commit).

    Args:
        owner_id: The user_id whose rows to list.
        account_id: The cash account whose balance was asserted.
        observed_on: The civil day that balance was true for.

    Returns:
        ``{transaction_id: OutstandingTransaction}``, insertion-ordered by
        landing day then id.  Empty for an account holding nothing overdue --
        **which is NOT the steady state, and the purchase arm's twin of this
        sentence is now wrong about the panel as a whole.**  Replayed over all
        53 Checking assertion days on production, 46 would have carried at
        least one offer, because an envelope's close is offerable for the whole
        of its own period and only closing it clears it.  Finding **N-227**
        owns whether that bound is right.
    """
    return {
        txn.id: OutstandingTransaction(
            transaction_id=txn.id,
            attributed_on=_rows.attributed_on(txn),
            amount=transaction_service.settle_amount(txn),
            is_correctable=not transaction_service.settles_from_entries(txn),
            is_income=txn.is_income,
            kind=_offer_kind(txn),
        )
        for txn in _outstanding_rows(owner_id, account_id, observed_on)
    }


def record_settled_transactions(
    owner_id: int,
    account_id: int,
    transaction_ids: "set[int]",
    corrections: "dict[int, Decimal]",
    observed_on: date,
) -> int:
    """Settle *transaction_ids* as having moved by *observed_on*.

    This arm's writer: the user ticked these rows off a statement, so each
    settles through ``transaction_service.settle_transaction`` -- the grid's
    own verb -- stamping the statement's civil day rather than the seam's
    default of the user's today.

    **Every id is re-derived through the arm's scope rather than trusted.**
    An id belonging to another user, another account, a settled row, a
    soft-deleted row, a transfer shadow or a row that is not yet overdue simply
    does not come back from :func:`_outstanding_rows` and is silently skipped.
    The count returned is what actually settled, never what was asked for.

    **A correction is applied only where the panel offered a box for one**
    (rulings **R-FB** / **R-FF**), and only when it DIFFERS from what the row
    would otherwise book.  Both halves are the VERB's and neither is restated
    here: the submitted figure is handed straight to it.

    **The loop reconciles the posted ledger once PER ROW, and that is finding
    N-221 ANSWERED rather than accepted by default.**  The verb ends in
    ``posting_service.sync_transaction_postings``, which per call resolves two
    ledger accounts, reads the period's posted set and runs the anchor self-heal
    -- ruling **R-DL**'s shape one tier up.  A batch sibling is NOT built here,
    for two measured reasons.  It already exists as somebody's job: plan step
    **X-ai-a**'s stated mandate is a BATCHED cash reconcile, measured at 8 SQL
    statements against 696 assembled, and a second batch implementation written
    here is the duplication that step exists to remove.  And the cost is bounded
    by the data rather than by hope: replayed over all 53 Checking assertion
    days on production, the WORST day offers 9 transaction rows and the mean is
    **4.02** over the 46 days that carry any (a first draft said 4.2, which was
    the same replay with transfer shadows left in -- they are the transfer arm's
    and not this one's), and ``carry_forward_service`` already loops the same
    reconcile per envelope on a path with no such bound.  N-221 is therefore
    re-pointed to X-ai-a rather than closed.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        owner_id: The user_id whose rows these must be.
        account_id: The cash account the balance was asserted for.
        transaction_ids: The ids the user ticked.  An empty set is a no-op that
            issues no query.
        corrections: ``{transaction id: amount}`` for the rows whose amount box
            was submitted.  An id with no entry, and an id whose row is not
            correctable, settle at the row's own figure.
        observed_on: The civil day the asserted balance was true for, and the
            day each settled row records its money as having moved.

    Returns:
        The number of rows actually settled.

    Raises:
        ValidationError: Propagated from the settle verb -- an illegal
            transition a stale panel can still submit.  A 400 at the route.
        PostingError: Propagated from the verb's ledger reconcile.  Fails loud.
    """
    if not transaction_ids:
        return 0

    rows = _outstanding_rows(
        owner_id, account_id, observed_on, transaction_ids=transaction_ids,
    )
    corrected = 0
    for txn in rows:
        # The submitted figure is handed straight to the verb.  **This loop
        # holds no money rule at all**, and a first draft's two -- "read it only
        # where the panel offered a box" and "only when it differs from what the
        # row would otherwise book" -- were both the verb's, restated.  A review
        # measured the first: deleting it left every test green, because
        # ``settle_transaction`` routes an entries-derived row to a branch that
        # ignores ``actual_amount`` outright.  A guard nothing can observe is
        # not a guard, and two doors deciding one column's meaning separately is
        # the shape this whole arc exists to remove.
        submitted = corrections.get(txn.id)
        # Asked BEFORE the settle and of the VERB, which is finding **N-231**.
        # This counted rows whose ``actual_amount`` CHANGED -- but an envelope's
        # close always writes that column, so every envelope tick incremented a
        # count whose whole purpose is to distinguish a HUMAN's figure from a
        # machine's.  Measured: a probe settling one envelope with no correction
        # submitted logged ``corrected_count: 1``.  ``is_correction`` is the
        # verb's own branch predicate published, evaluated while the row is
        # still in its pre-settle state, which is the only moment the question
        # has an answer.
        if transaction_service.is_correction(txn, submitted):
            corrected += 1
        transaction_service.settle_transaction(
            txn, actual_amount=submitted, settled_on=observed_on,
        )

    if rows:
        log_event(
            logger, logging.INFO,
            EVT_TRANSACTIONS_RECONCILED, BUSINESS,
            "Outstanding transactions settled against a bank statement",
            user_id=owner_id,
            account_id=account_id,
            observed_on=observed_on.isoformat(),
            settled_count=len(rows),
            requested_count=len(transaction_ids),
            corrected_count=corrected,
        )

    return len(rows)
