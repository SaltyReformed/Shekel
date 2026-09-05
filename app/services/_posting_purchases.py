"""The PURCHASE posting source: an envelope's own purchases as cash movements.

Ruling **R-FM** (plan step X-f3b), whose whole content is one sentence: *a
purchase that has cleared the bank is a cash posting, and its envelope's close
books only what its purchases did not*.  This module is that source's write
half -- what makes a purchase postable, what its two legs are, and the
reconcile-to-target emission for one of them.

**Why a module of its own rather than more of :mod:`app.services.posting_service`.**
The purchase is a THIRD posting source beside the transfer and the transaction,
with its own concrete linkage (``journal_entries.transaction_entry_id``), its
own source kind (``ref.posting_sources`` ``purchase``), its own target rule and
its own day.  Adding it inline took the writer module 319 lines past pylint's
1,000-line ceiling; growing past a gate is a signal, and the seam the ceiling
was measuring is exactly this one.  The split follows the sibling-split
convention ``posting_reads`` was created by.

**It holds no public door, deliberately.**  The two doors a caller reaches --
``posting_service.sync_purchase_postings`` and
``posting_service.reverse_purchase_postings_before_delete`` -- stay in the
writer module, because both must run the account anchor self-heal that module
owns, and because ``posting_service`` remains the ledger's ONE public surface.
What lives here is what those doors are made of.

**The dependency runs one way**: this module imports the balanced-write leaf
(:mod:`app.services._posting_write`) and the chart resolvers, and never
``posting_service`` itself -- the same direction, and for the same cycle
reason, that :mod:`app.services._posting_reconcile` keeps.

Flask-isolated and commit-free like its consumers: flushes so the caller sees
assigned ids; the caller owns the transaction boundary.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.extensions import db
from app.models.journal_entry import JournalEntry
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import ledger_account_service
from app.services._posting_write import (
    _MAX_DESCRIPTION_LENGTH,
    emit_source_deltas,
    source_entry_builder,
)
from app.services.posting_reads import _ledger_account_for
from app.utils.balance_predicates import is_balance_contributing


def posted_purchase_exists_clause():
    """Return the SQL form of "this transaction holds a posted purchase".

    :func:`purchase_posts` asks the question of ONE loaded row; this asks it of
    a query, as a correlated ``EXISTS`` over ``budget.transaction_entries``.  It
    is the same two narrowings that predicate makes about the purchase itself --
    a recorded posting day, and a DEBIT -- and deliberately not the third: what
    the PARENT contributes is the caller's own filter, because the two callers
    want opposite answers.  ``resync_all_cash_postings`` wants every row that
    could hold a leg; a bulk archive wants the rows it is about to stop
    contributing.

    **It exists because the clause was written twice** (caught by pylint's
    ``duplicate-code`` at plan step X-f3b, which is what that checker is for):
    once in the deploy resync's WHERE and once in the template routes' bulk
    guard.  Two spellings of "can this row hold postings" is the shape ruling
    R-FM's whole family of fixes exists to prevent, one tier up.

    Returns:
        A SQLAlchemy ``EXISTS`` clause, correlated to ``Transaction``, for use
        in any query rooted there.
    """
    return (
        db.session.query(TransactionEntry.id)
        .filter(
            TransactionEntry.transaction_id == Transaction.id,
            TransactionEntry.settled_on.isnot(None),
            TransactionEntry.is_credit.is_(False),
        )
        .exists()
    )


def purchase_posts(txn: Transaction, entry) -> bool:
    """Return whether *entry* books a cash leg of its own -- ruling **R-FM**.

    The ONE statement of "is this purchase in the ledger" on the write side,
    and the twin of the read side's own three narrowings
    (:func:`app.services.cash_ledger._events._posted_purchase_facts`).  All
    three are load-bearing and each is stated there in full:

    * the PARENT contributes to a balance at all (not soft-deleted, not Credit
      or Cancelled) -- :func:`~app.services.cash_ledger.settled_cash_leg`'s
      totality rule, extended to the family it now has;
    * the purchase is a DEBIT -- a card purchase leaves through its own CC
      Payback sibling and never touches this account;
    * its bank posting day is RECORDED -- the trigger itself.

    It reads no status of the parent beyond the contributing gate, deliberately:
    a purchase against a still-PROJECTED envelope has left the bank exactly as
    one against a closed envelope has.

    Args:
        txn: The parent transaction.
        entry: One of its ``budget.transaction_entries`` rows.

    Returns:
        True when the ledger should hold a cash leg for *entry*.
    """
    return (
        is_balance_contributing(txn)
        and not entry.is_credit
        and entry.settled_on is not None
    )


def _purchase_target(entry, txn: Transaction, owner_id: int) -> dict[int, Decimal]:
    """Return the debit-positive ledger target for a POSTED purchase.

    The purchase analog of :func:`_settled_target`:
    ``{cash_ledger_id: -amount, category_ledger_id: +amount}``, summing to zero
    by construction.  There is no sign branch and no credit term.

    **The absence of a sign branch is what makes a REFUND work, and it was not
    designed for one** (ruling **bank_import:R-II**).  A purchase's whole amount
    leaves the account, so the expression was written for an expense -- and
    because it is arithmetic rather than a case analysis, a NEGATIVE purchase
    passes through it correctly: at ``-28.29`` it emits
    ``{cash: +28.29, category: -28.29}``, money coming back and a
    contra-expense, which is exactly what a merchant credit is.  Measured
    end-to-end on a production clone before the constraint moved, against a
    ``+28.29`` control that produced the mirror image.

    **The counter leg is the ENVELOPE's own category** (ruling **R-FM**,
    developer 2026-08-15).  A purchase carries no category of its own, and the
    expense it records is its parent's: booking it there recognises the expense
    in the right category on the day it happens, and the parent's close then
    books only the remainder to the SAME account, so the two always sum to the
    row's whole debit total.  Rejected: booking it to Uncategorized until the
    close, which shows an open envelope's real spend as uncategorised on the
    income statement and makes every close write a reclassification pair.

    A re-category of the parent is therefore a re-category of its purchases, and
    it reconciles by the same mechanism the parent's own leg uses: the sync
    reads the OLD legs back from the ledger and reverses them
    (``routes/transactions/mutations`` lists ``category_id`` among the fields
    that raise a reconcile, on a Projected row as well as a settled one).

    Args:
        entry: The purchase.  Its ``account_id`` IS its parent's
            (``fk_transaction_entries_parent_account`` makes any other value
            unwritable), so the cash account is read straight off it.
        txn: Its parent transaction, taken as an ARGUMENT rather than through
            ``entry.transaction`` so the caller that already holds it -- every
            caller does -- pays no lazy load, and so the category booked here is
            provably the one the parent's own leg books.
        owner_id: The owning user's id (``txn.user_id``, and
            ``txn.pay_period.user_id`` until plan step
            ``pay_calendar:C13-b``), the category account's owner.

    Returns:
        ``{cash_ledger_id: -amount, category_ledger_id: +amount}``.

    Raises:
        PostingError: If the purchase's account has no linked ledger account.
        ValueError: Propagated from the resolver if the parent's non-NULL
            ``category_id`` names no category owned by ``owner_id``.
    """
    cash_ledger = _ledger_account_for(entry.account_id)
    category_ledger = ledger_account_service.get_or_create_category_ledger_account(
        owner_id, txn.category_id, LedgerAccountClassEnum.EXPENSE,
    )
    amount = Decimal(str(entry.amount))
    return {cash_ledger.id: -amount, category_ledger.id: amount}


def emit_purchase_deltas(
    entry, txn: Transaction, *, posted: bool, owner_id: int,
) -> "list[JournalEntry]":
    """Emit the delta entries for ONE purchase's own cash leg -- ruling **R-FM**.

    The purchase analog of :func:`_emit_transaction_deltas`, and deliberately
    the SAME shape: reconcile-to-target over the ``(pay period, entry date)``
    keys the purchase has already posted to, unioned with the one it should
    hold.  That is what makes every lifecycle act on a purchase one call -- a
    posting day recorded, corrected, or cleared; a parent cancelled or
    soft-deleted; an amount edited; a credit flag flipped -- and a repeat sync a
    no-op.  A ``settled_on`` MOVE reconciles as two keys, the old date reversing
    to zero and the new one posting fresh, exactly as finding **N-13**'s
    transaction twin does.

    **The PERIOD is the parent's and the DATE is the purchase's**, which is the
    same two-clock split every cash source keeps: the budget column a purchase
    spends is its envelope's, and the day its money moved is its own.

    Args:
        entry: The purchase.
        txn: Its parent transaction.
        posted: Whether the ledger should hold a cash leg for it
            (:func:`purchase_posts`; ``False`` also means "reverse it", which
            is what the teardown doors pass).
        owner_id: ``txn.user_id``.

    Returns:
        The emitted delta entries; ``[]`` when the ledger is already at target.
    """
    targets: "dict[tuple[int, date], dict[int, Decimal]]" = {}
    if posted:
        targets[(txn.pay_period_id, entry.settled_on)] = _purchase_target(
            entry, txn, owner_id,
        )
    return emit_source_deltas(
        targets=targets,
        source_filter=JournalEntry.transaction_entry_id == entry.id,
        kind_id=ref_cache.posting_kind_id(PostingKindEnum.EXPENSE),
        build_entry=source_entry_builder(
            user_id=owner_id,
            scenario_id=txn.scenario_id,
            source_kind_id=ref_cache.posting_source_id(
                PostingSourceEnum.PURCHASE
            ),
            description=entry.description[:_MAX_DESCRIPTION_LENGTH],
            transaction_entry_id=entry.id,
        ),
        log_label=f"purchase {entry.id} (posted={posted})",
    )
