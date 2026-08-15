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

from decimal import Decimal

from sqlalchemy.orm import selectinload

from app.models.transaction import Transaction
from app.services import cash_ledger, transaction_service
from app.services.cash_ledger import AnchorPoint
from app.services.reconcile_service import _rows
from app.services.reconcile_service._offers import (
    OfferKind,
    OutstandingTransaction,
)
from app.utils.log_events import EVT_TRANSACTIONS_RECONCILED


def _cash_amount(txn: Transaction, booked: Decimal) -> "Decimal | None":
    """Return what the STATEMENT shows for *txn*, or ``None`` when it is *booked*.

    Finding **N-226**.  An envelope settles at ``sum(entries)`` over EVERY
    entry it holds, and a card purchase is one of those -- but a card purchase
    never touches checking: it leaves later through its own CC Payback sibling,
    which is exactly why the purchase arm refuses to OFFER one.  So the figure
    a tick books and the figure the bank showed are two different numbers for
    one row, and this screen is the one read beside a paper statement.

    **It prints both rather than changing what a tick books**, which is the
    only correct direction: ``actual_amount`` legitimately IS total spend, the
    posted ledger already subtracts the credit sum
    (``cash_ledger.settled_cash_leg``), and moving the booked figure would make
    the panel disagree with the grid and the analytics.

    The credit term is ``cash_ledger.credit_entry_sum`` rather than a second
    ``entry.is_credit`` reduction here: one rule, one statement, so a change to
    what "on a card" means cannot leave the panel saying the old thing.

    Args:
        txn: The row being offered, with ``entries`` loaded.
        booked: What a tick would book (``transaction_service.settle_amount``).

    Returns:
        ``booked`` minus the card entries when the row holds any, else
        ``None`` -- which is every bill, every deposit and every envelope whose
        purchases were all debits.  Production carries 18 card entries in
        history and ZERO on a Projected envelope today, so this is latent
        rather than live.
    """
    on_card = cash_ledger.credit_entry_sum(txn)
    if not on_card:
        return None
    return booked - on_card


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


def _settle_one(
    txn: Transaction,
    submitted: Decimal | None,
    statement: _rows.Statement,
) -> bool:
    """Settle one row through the grid's own verb; say if a human's figure won.

    This arm's settle, named by :data:`ARM`.
    The submitted figure is handed STRAIGHT to the verb: **this function holds
    no money rule at all**, and a first draft's two -- "read it only where the
    panel offered a box" and "only when it differs from what the row would
    otherwise book" -- were both the verb's, restated.  A review measured the
    first: deleting it left every test green, because ``settle_transaction``
    routes an entries-derived row to a branch that ignores ``actual_amount``
    outright.  A guard nothing can observe is not a guard, and two doors
    deciding one column's meaning separately is the shape this whole arc exists
    to remove.

    Args:
        txn: The row to settle, still Projected.
        submitted: The figure the panel's amount box posted, or ``None``.
        statement: The statement being reconciled; its day is what the settle
            records the money as having moved on, rather than the seam's
            default of the user's today.

    Returns:
        Whether the verb booked *submitted* as a correction -- asked of its own
        published predicate BEFORE the settle, because the settle mutates the
        figures the question is about (finding **N-231**).
    """
    corrected = transaction_service.is_correction(txn, submitted)
    transaction_service.settle_transaction(
        txn, actual_amount=submitted, settled_on=statement.observed_on,
    )
    # WHICH statement showed this row (ruling **R-FL**), recorded HERE rather
    # than inside ``settle_transaction`` -- and that placement is the rule.  The
    # verb is shared with the grid's Mark Paid, which settles a row without any
    # statement having shown it, so a link written there would record an
    # observation nobody made.  Ticking a row on this panel IS the observation.
    #
    # It follows the settle, which RELEASES any prior link as it stamps the day
    # (``status_seam``): the release is about the day that moved, and this is
    # the new day's own fact.
    txn.reconciled_by_id = statement.anchor.anchor_id
    return corrected


#: What this arm IS (:class:`app.services.reconcile_service._rows.Arm`): which
#: rows are its own, how one settles, and what it calls the act in the log.
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
#:
#: PUBLIC within the package: the reader below and
#: :func:`app.services.reconcile_service._assemble.record_reconciliation` both
#: name it, and it being ONE value is what stops them scoping differently.
ARM = _rows.Arm(
    kind_clauses=(Transaction.transfer_id.is_(None),),
    settle=_settle_one,
    event=EVT_TRANSACTIONS_RECONCILED,
    load_options=(selectinload(Transaction.template),),
)


def outstanding_transactions(
    owner_id: int, account_id: int, anchor: AnchorPoint,
) -> "dict[int, OutstandingTransaction]":
    """Return this arm's offers, ``{transaction id: offer}``.

    The source rows this account is still holding forward on the day the
    balance was asserted: an envelope whose own close has not been ticked, and
    a bill the projection is still carrying.  Ticking one records that the bank
    moved the money by that day
    (:func:`app.services.reconcile_service._rows.record_settled`).

    **It returns a MAP keyed on the PARENT, which is what lets the assembler
    union it with the purchase arm** -- that arm keys its purchases on the same
    id, so an envelope with outstanding purchases AND an offerable close is ONE
    block carrying both, which is ruling **R-EW**'s shape.

    Reads only (no writes, no commit).

    Args:
        owner_id: The user_id whose rows to list.
        account_id: The cash account whose balance was asserted.
        anchor: The governing assertion -- the STATEMENT being
            reconciled against.  Its ``observed_on`` bounds the offer
            set and its id is what a tick records (ruling **R-FL**).

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
    statement = _rows.Statement(owner_id, account_id, anchor)
    return {
        txn.id: _offer(txn)
        for txn in _rows.outstanding_rows(ARM, statement)
    }


def _offer(txn: Transaction) -> OutstandingTransaction:
    """Return the offer this arm makes for one row.

    Args:
        txn: A row in scope, with ``entries``, ``pay_period`` and ``template``
            loaded.

    Returns:
        Its :class:`OutstandingTransaction`.  ``amount`` is resolved once and
        passed to :func:`_cash_amount` rather than resolved twice: the two
        figures are the same number seen two ways, and asking the verb again
        would be a second answer to one money question.
    """
    booked = transaction_service.settle_amount(txn)
    return OutstandingTransaction(
        transaction_id=txn.id,
        attributed_on=_rows.attributed_on(txn),
        amount=booked,
        cash_amount=_cash_amount(txn, booked),
        is_correctable=not transaction_service.settles_from_entries(txn),
        is_income=txn.is_income,
        kind=_offer_kind(txn),
    )
