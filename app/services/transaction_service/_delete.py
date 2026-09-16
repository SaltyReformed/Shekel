"""What removing a transaction MEANS: the one sequence, and its order.

Plan step ``bank_import:X-gb``.  This sequence existed TWICE -- once in
``routes.transactions.mutations.delete_transaction`` and once in
``statement_match._release._remove``, whose own docstring says it "takes the
transaction delete sequence WHOLE" -- and a third caller would have made three.
Each step of it is a money rule with an ORDER that is load-bearing, so two
copies is two places for that order to drift.

**The order, and why each step is where it is.**  ``0`` is OWNERSHIP and it
is numbered from zero because it is not part of the sequence -- it is the
door's own precondition, asked before the sequence starts.

0. **Reconcile the ``owner_id`` the caller states against the row it hands in**
   (finding **N-373**, landed at plan step ``pay_calendar:C13-b``).  This door
   took an owner for its EVENTS and checked it against the row nowhere, while
   its sibling ``entry_service.delete_entry`` re-validated -- so of the two
   doors ``statement_match._release._remove`` calls, one asked and one
   trusted, and it was that asymmetry that let the by-id refetch look safe.
   It is one comparison because the owner is a COLUMN since plan step
   ``pay_calendar:C13-a``; before that it was a join walk through the paycheck
   and the finding was SEQUENCED behind the key for exactly that reason.
   FIRST, ahead of ``deletion_refusal``, so a caller naming the wrong owner is
   told nothing about the row -- the ordering ``entry_service._doors`` states
   for the same pair of guards.
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
4. **Remove the row**, soft or hard by whether its definition RECURS.
5. **Dispose of the definition the row was the LAST of** (plan step
   ``balance:X-bi-7b``, rulings **R-BAL23** / **R-BAL27**): a one-off is a
   rule-less definition plus its placed row, and a rule-less definition with
   no row defines nothing -- so its series and it go, through
   ``definition_delete.permanently_delete_definition``, the ONE act both
   definition doors already call.  AFTER the row, and asked of the row's
   definition rather than assumed: a definition holding any other row (a
   bank-born envelope's other paychecks, a cleared cadence's survivors, a
   soft-deleted one-off) stays.  Where a standing merchant rule names the
   definition the delete never gets here -- ``deletion_refusal`` refused it
   at step 0's neighbour, because the rule would cascade away with it.

**Why the FORK at step 4 is about the cadence and not about the status.**  A
row of a recurring definition is one instance of a rule that keeps generating:
deleting it hard would let the next regeneration put it straight back, so the
row stays as a tombstone the engine reads (``recurrence_engine`` skips the
OCCURRENCE a ``is_deleted`` row answers -- ``_recurrence_common.OccurrenceClaims``
counts every state -- and both generation indexes exclude it so a replacement
can be created only where the owner has not said no).  A row nothing
regenerates -- a link-less one, or one whose definition has no rule -- answers
to nobody, so it goes.  **Keyed on ``Transaction.recurs`` since plan step
``balance:X-bi-7a``**, not on the link: a rule-less definition's row is a
one-off (ruling **R-BAL20**), and a soft arm there would keep a tombstone no
pass will ever read, restorable from an archive drawer for a definition that
generates nothing -- the twin's live defect on its own table (finding
**N-386**).

Boundary discipline (``CLAUDE.md`` Architecture): an ORM row in, a frozen
dataclass out, no Flask import.  It MUTATES and does NOT commit -- the caller
owns the unit of work.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import (
    credit_workflow,
    definition_delete,
    match_withdrawal,
    posting_service,
)
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
        soft: Whether the row stays as a tombstone (its definition recurs,
            ``Transaction.recurs``) rather than leaving the table.  The two
            are one act to the owner and two facts to the engine, so the door
            reports which it was
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
        disposes_definition: Whether the row's DEFINITION goes with it --
            a one-off's, of which this row is the last (step 5 of the module
            docstring's order; rulings **R-BAL23** / **R-BAL27**, plan step
            ``balance:X-bi-7b``).  Carried so the dialog can say the ITEM
            goes and not only the row, off the same answer the press acts
            on (``definition_delete.is_last_row_of_its_definition``).
    """

    soft: bool
    paybacks: "tuple[str, ...]"
    withdrawn: MatchWithdrawal
    disposes_definition: bool


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
    soft = txn.recurs
    chain = credit_workflow.live_payback_chain(txn)
    return soft, ([] if soft else [txn]) + chain


def preview_deletion(
    txn: Transaction, *, last_row_of_definition: "bool | None" = None,
) -> RowDeletion:
    """Return what deleting *txn* WOULD take, without taking any of it.

    The read half of :func:`delete_transaction`, over the same row set, for the
    confirm dialog on a delete control.  Writes nothing.

    Args:
        txn: The row a screen is offering to delete.
        last_row_of_definition: Whether deleting *txn* would leave a
            rule-less definition with no row, when the caller has already
            asked (the card render asks once for this and for
            :func:`~._row_rules.deletion_refusal`); ``None`` asks here.

    Returns:
        Its :class:`RowDeletion`.  ``soft`` says which arm the press would
        take, so a dialog can promise the right thing about permanence, and
        ``disposes_definition`` whether the item goes with the row.
    """
    if last_row_of_definition is None:
        last_row_of_definition = definition_delete.is_last_row_of_its_definition(txn)
    soft, leaving = _leaves_the_table(txn)
    return RowDeletion(
        soft=soft,
        paybacks=tuple(
            row.name for row in leaving if row.id != txn.id
        ),
        withdrawn=match_withdrawal.pending_for_rows(leaving),
        disposes_definition=last_row_of_definition,
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
        owner_id: The user the caller proved owns it, recorded on the events
            AND reconciled against the row (step 0 of the module docstring's
            order, finding **N-373**).

    Returns:
        What the delete did, as :class:`RowDeletion` -- the same shape
        :func:`preview_deletion` returned for the same row.

    Raises:
        NotFoundError: When *owner_id* is not *txn*'s owner.  Both callers
            prove ownership before reaching here -- the route through its own
            helper, ``_release`` through a ``StatementMatch`` filtered on
            ``user_id`` AND ``account_id`` -- so this is defence in depth and
            not a live defect (finding **N-373** measured ``$0.00``).  What it
            stops being is a PERMISSION a third caller inherits.  The message
            is the row's id and nothing else, per the security response rule:
            "not yours" and "not found" read alike.
            **NEITHER caller catches it, deliberately** (CLAUDE.md rule 13): the
            route's own helper reads the SAME attribute off the SAME instance
            one line earlier with no write between, so an arm here would be
            handling a state that cannot occur.  A third caller that has not
            proved ownership gets a loud 500, which is the right answer to a
            bug in a door that deletes budget rows.
        ValidationError: When :func:`~._row_rules.deletion_refusal` names a
            reason this row may not be deleted on its own -- a transfer shadow
            or a CC payback.  It fires BEFORE anything is written, so a refused
            delete leaves the database exactly as it was.
        PostingError: From the ledger reconcile, on a broken invariant.
    """
    if txn.user_id != owner_id:
        raise NotFoundError(f"Transaction {txn.id} not found.")

    # Asked ONCE and threaded: the refusal's third arm and step 5 below want
    # the same answer, and one request asks a producer once.
    last_row_of_definition = definition_delete.is_last_row_of_its_definition(txn)
    refusal = deletion_refusal(
        txn, last_row_of_definition=last_row_of_definition,
    )
    if refusal is not None:
        raise ValidationError(refusal)

    soft, leaving = _leaves_the_table(txn)
    paybacks = tuple(row.name for row in leaving if row.id != txn.id)
    # The definition is read off the row BEFORE the row is deleted: the
    # relationship may not be loaded yet, and a lazy load on an instance the
    # session has already deleted is not a read this door may rely on.
    definition = txn.template if last_row_of_definition else None
    withdrawn = match_withdrawal.withdraw_for_rows(leaving, owner_id)
    credit_workflow.delete_payback_on_source_delete(txn, owner_id)
    posting_service.reverse_postings_before_delete(txn)

    if soft:
        txn.is_deleted = True
    else:
        db.session.delete(txn)
    if definition is not None:
        # Step 5.  The act's own read autoflushes the row's DELETE first, so
        # its scan of the definition's non-settled rows finds none and what
        # is left to remove is the definition and its series.
        definition_delete.permanently_delete_definition(definition)
    return RowDeletion(
        soft=soft, paybacks=paybacks, withdrawn=withdrawn,
        disposes_definition=last_row_of_definition,
    )
