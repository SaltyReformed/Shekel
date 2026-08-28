"""What removing a transaction MEANS: the one sequence, and its order.

Plan step ``bank_import:X-gb``.  This sequence existed TWICE -- once in
``routes.transactions.mutations.delete_transaction`` and once in
``statement_match._release._remove``, whose own docstring says it "takes the
transaction delete sequence WHOLE" -- and a third caller would have made three.
Each step of it is a money rule with an ORDER that is load-bearing, so two
copies is two places for that order to drift.

**The order, and why each step is where it is:**

1. **Withdraw the matches this delete would leave naming no app row at all**
   (:mod:`app.services.match_withdrawal`, developer ruling 2026-08-25) -- for
   the row, its purchases AND its live CC-payback chain, because all of them
   go in this one commit.  A match asserts that a bank line IS these rows; when
   the last of them stops existing the assertion is withdrawn and the line is
   unexplained again.  FIRST, while the members still exist to be read: their
   foreign keys are ``ON DELETE CASCADE``, so after the delete there is nothing
   left to say which lines were freed.
2. **Take down the live CC payback chain** (``credit_workflow``), because
   ``transactions.credit_payback_for_id`` is ``ON DELETE SET NULL`` -- without
   this a projected payback survives its source and inflates the next period
   with no offsetting credit row.  Step 1 has already withdrawn its matches, so
   that helper no longer does: one withdrawal per press is what lets the
   dialog's figure and the receipt's figure be one derivation.
3. **Reverse the postings** (``posting_service``), while
   ``journal_entries.transaction_id`` and ``.transaction_entry_id`` still link
   them.  Both are ``ON DELETE SET NULL``: reversing afterwards is impossible
   and the original legs would be stranded on their ledger accounts with
   nothing to offset them.
4. **Remove the row**, soft or hard by whether a template generated it.

**Why the FORK at step 4 is about the template and not about the status.**  A
template-linked row is one instance of a rule that keeps generating: deleting
it hard would let the next regeneration put it straight back, so the row stays
as a tombstone the engine reads (``recurrence_engine`` skips the OCCURRENCE
a ``is_deleted`` row answers -- ``_recurrence_common.OccurrenceClaims`` counts
every state -- and both generation indexes exclude it so a replacement can be
created only where the owner has not said no).  An ad-hoc row answers to nobody, so it goes.

Boundary discipline (``CLAUDE.md`` Architecture): an ORM row in, a frozen
dataclass out, no Flask import.  It MUTATES and does NOT commit -- the caller
owns the unit of work.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import credit_workflow, match_withdrawal, posting_service
from app.services.match_withdrawal import MatchWithdrawal
from app.services.transaction_service._row_rules import deletion_refusal


@dataclass(frozen=True)
class RowDeletion:
    """What deleting one row takes with it -- READ or DONE, one shape.

    **The dialog prints this and the press performs it**, which is the shape
    :func:`~app.services.statement_match.planned_removals` already has one door
    over: two derivations would let a destructive control promise one thing and
    do another.  A first build derived the dialog from the ROW alone while the
    press also tore down the row's CC-payback chain -- measured at a `$200.00`
    card payment silently un-explained, over a dialog that named no bank line
    at all and a log line reading ``matches_withdrawn=0`` (adversarial review,
    2026-08-25).

    Attributes:
        soft: Whether the row stays as a tombstone (a template generated it)
            rather than leaving the table.  The two are one act to the owner
            and two facts to the engine, so the door reports which it was
            instead of the caller re-deriving it from a column on a row it may
            no longer hold.
        paybacks: The names of the live CC payback rows that go down with it,
            nearest first -- ``()`` for the ordinary row that has none.  NAMED
            rather than counted, because they are budget lines in a future
            period that the owner will otherwise find missing.
        withdrawn: The matches withdrawn across the WHOLE set
            (:class:`~app.services.match_withdrawal.MatchWithdrawal`), so a
            receipt can name the bank lines that are unexplained again without
            asking a relation the delete has already destroyed.
    """

    soft: bool
    paybacks: "tuple[str, ...]"
    withdrawn: MatchWithdrawal


def _leaves_the_table(txn: Transaction) -> "tuple[bool, list[Transaction]]":
    """Return whether *txn* itself goes, and the payback chain that always does.

    **The soft arm removes NOTHING from the table**, and that distinction is
    what the match withdrawal has to see: a member's foreign key CASCADES only
    on a real ``DELETE``, so a soft-deleted row keeps its membership and the act
    still names it.  Withdrawing there would destroy an accepted act for a
    change that ``templates/crud`` un-archives with a shipped button.  A first
    build derived the going set from the row alone and withdrew on the soft arm
    too; the control that caught it is
    ``TestAnActIsWITHDRAWNONLYWhenItLosesItsLastRow``.

    **The payback chain goes either way**, because
    :func:`~app.services.credit_workflow.delete_payback_on_source_delete` hard-
    deletes every level whatever arm the source takes -- a projected payback
    that outlived a soft-deleted source would inflate the next period with no
    offsetting credit row.

    Args:
        txn: The row being deleted.

    Returns:
        ``(soft, leaving)`` -- whether the row stays as a tombstone, and every
        row this commit really removes from the table.
    """
    soft = txn.template_id is not None
    chain = credit_workflow.live_payback_chain(txn)
    return soft, ([] if soft else [txn]) + chain


def preview_deletion(txn: Transaction) -> RowDeletion:
    """Return what deleting *txn* WOULD take, without taking any of it.

    The read half of :func:`delete_transaction`, over the same row set, for the
    confirm dialog on a delete control.  Writes nothing.

    Args:
        txn: The row a screen is offering to delete.

    Returns:
        Its :class:`RowDeletion`.  ``soft`` says which arm the press would
        take, so a dialog can promise the right thing about permanence.
    """
    soft, leaving = _leaves_the_table(txn)
    return RowDeletion(
        soft=soft,
        paybacks=tuple(
            row.name for row in leaving if row.id != txn.id
        ),
        withdrawn=match_withdrawal.pending_for_rows(leaving),
    )


def delete_transaction(txn: Transaction, owner_id: int) -> RowDeletion:
    """Remove *txn* from the books, soft or hard, with everything it holds.

    The module docstring carries the order and why each step is where it is.

    Does NOT commit -- the caller owns the session boundary, which is what lets
    the whole sequence land atomically with whatever else that request writes.

    Args:
        txn: The row to delete.  Must still be flushed (``txn.id`` set) so the
            reversal entries can link by it and the match members can be read
            back.
        owner_id: The user the caller proved owns it, recorded on the events.

    Returns:
        What the delete did, as :class:`RowDeletion` -- the same shape
        :func:`preview_deletion` returned for the same row.

    Raises:
        ValidationError: When :func:`~._row_rules.deletion_refusal` names a
            reason this row may not be deleted on its own -- a transfer shadow
            or a CC payback.  It fires BEFORE anything is written, so a refused
            delete leaves the database exactly as it was.
        PostingError: From the ledger reconcile, on a broken invariant.
    """
    refusal = deletion_refusal(txn)
    if refusal is not None:
        raise ValidationError(refusal)

    soft, leaving = _leaves_the_table(txn)
    paybacks = tuple(row.name for row in leaving if row.id != txn.id)
    withdrawn = match_withdrawal.withdraw_for_rows(leaving, owner_id)
    credit_workflow.delete_payback_on_source_delete(txn, owner_id)
    posting_service.reverse_postings_before_delete(txn)

    if soft:
        txn.is_deleted = True
    else:
        db.session.delete(txn)
    return RowDeletion(soft=soft, paybacks=paybacks, withdrawn=withdrawn)
