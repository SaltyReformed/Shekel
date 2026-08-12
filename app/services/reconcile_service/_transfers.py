"""
Shekel Budget App -- The TRANSFER arm of the outstanding set

The last of the package's three arms (see :mod:`app.services.reconcile_service`
for what an arm is): a TRANSFER's shadow on the account whose balance was
asserted.  Money moving between two of the owner's own accounts still leaves
one of them, so a checking statement shows it exactly as it shows a bill -- and
until plan step X-f2-c3 the panel could not settle one.  Replayed over
production's 57 Checking assertion days, **8 days would have carried a transfer
offer, `$5,442.89`**: six `$500.00` savings transfers, one `$1,910.95` Mortgage
payment and one `$531.94` Van Loan payment.

**Its settle is ``transfer_service.update_transfer``, and that is the whole
reason it is a separate arm** (ruling **R-FA**).  A transfer is THREE rows -- a
parent and two shadows -- and ``CLAUDE.md`` transfer invariants 3 and 4 say they
move together, so ``transaction_service.settle_transaction`` REFUSES a shadow
outright.  Ticking one here therefore settles the leg on the OTHER account too,
which is a fact about the act rather than about any row and is why the panel
prints it once under the section heading
(:attr:`~app.services.reconcile_service.OfferKind.section_note`).

**Nothing here decides what a tick BOOKS or whether a submitted figure is a
CORRECTION.**  Both are the transfer service's, published as
``transfer_service.settle_amount`` and ``transfer_service.is_correction``, and
read from here.  The loan-payment FREEZE the step specification names is inside
the first of those: an auto-derived loan payment books its live payment-date
cash rather than the creation-time escrow its estimate carries, and because the
panel's figure and the booked figure come from one expression they cannot
drift.

**Its scope, bound and loader are :mod:`._rows`'** -- the same ones the
transaction arm uses, with the complementary membership clause.  What stays
here is which rows are this arm's, what one is worth, and what a tick means.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, frozen dataclasses out.
  - All monetary arithmetic uses :class:`~decimal.Decimal`.
  - The writer mutates and does NOT commit -- the caller owns the session
    boundary.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import transfer_service
from app.services.reconcile_service import _rows
from app.services.reconcile_service._offers import (
    OfferKind,
    OutstandingTransaction,
)
from app.utils.log_events import EVT_TRANSFERS_RECONCILED


def _settle_one(
    shadow: Transaction,
    submitted: Decimal | None,
    statement: _rows.Statement,
) -> bool:
    """Settle one transfer through the service; say if a human's figure won.

    This arm's settle, named by :func:`arm`.
    **Both legs and the parent move in this one call**, which is transfer
    invariants 3 and 4 held structurally rather than by this function
    remembering them, and it is why a tick here settles the matching row on the
    other account (the section note says so on the panel).

    **The status is DONE for all three rows, including the INCOME leg.**  The
    Paid/Received split is a display convention for ordinary rows and it is
    meaningless for a pair whose whole point is that one leg is each, so the
    transfer service sets one status on all three and this asks for the one it
    uses.

    The submitted figure is handed straight through: whether it beats the
    loan-payment freeze, and whether it is written at all, are the transfer
    service's rules and are not restated here.

    Args:
        shadow: The leg on this account, still Projected.
        submitted: The figure the panel's amount box posted, or ``None``.
        statement: The statement being reconciled; its day is what both legs
            record the money as having moved on.

    Returns:
        Whether the service booked *submitted* as a correction -- asked of its
        own published predicate BEFORE the settle, the same shape the
        transaction arm uses and for the same reason (finding **N-231**): a
        count read off the column afterwards cannot tell a human's figure from
        the freeze the settle writes for itself.
    """
    corrected = transfer_service.is_correction(shadow, submitted)
    updates = {
        "status_id": ref_cache.status_id(StatusEnum.DONE),
        "settled_on": statement.observed_on,
    }
    if submitted is not None:
        updates["actual_amount"] = submitted
    transfer_service.update_transfer(
        shadow.transfer_id, statement.owner_id, **updates,
    )
    return corrected





def arm(owner_id: int) -> _rows.Arm:
    """Return which rows are THIS arm's, for *owner_id*.

    Built per call rather than held as a module constant -- unlike the
    transaction arm's, whose membership is one column test -- because this
    arm's second clause is a statement over ``budget.transfers`` and a query
    needs a session.

    Two clauses:

    * ``transfer_id IS NOT NULL`` -- the complement of the transaction arm's
      own clause.  The two PARTITION the table, which is why neither is a
      default in :class:`~app.services.reconcile_service._rows.Arm`.
    * **the parent transfer is this owner's and is not soft-deleted.**  It
      belongs in the SCOPE rather than in a guard at the writer, because the
      writer acts on the PARENT and not on the shadow it was handed: a shadow
      whose parent has gone is not money this account owes, and offering one
      would send ``update_transfer`` looking for a row it treats as absent --
      a ``NotFoundError`` this route has no handler for, i.e. a 500 on a money
      door.  The owner half is the deliberate redundancy
      :func:`~app.services.reconcile_service._assemble._block_headings`
      documents: the shared scope already reaches the owner through the pay
      period, so it can change no answer today, and the cost is one indexed
      predicate inside a semi-join.

    No eager load beyond the two the shared bound already requires: this arm
    prices a row through ``transfer_service.settle_amount``, which resolves the
    parent transfer and its template through the loan service's own statement
    rather than through a relationship walk.

    Args:
        owner_id: The user_id whose rows to scope to.

    Returns:
        The :class:`~app.services.reconcile_service._rows.Arm` for this arm.
    """
    return _rows.Arm(
        kind_clauses=(
            Transaction.transfer_id.is_not(None),
            Transaction.transfer_id.in_(
                db.session.query(Transfer.id).filter(
                    Transfer.user_id == owner_id,
                    Transfer.is_deleted.is_(False),
                )
            ),
        ),
        settle=_settle_one,
        event=EVT_TRANSFERS_RECONCILED,
    )


def outstanding_transfers(
    owner_id: int, account_id: int, observed_on: date,
) -> "dict[int, OutstandingTransaction]":
    """Return this arm's offers, ``{shadow transaction id: offer}``.

    The transfers this account is still holding forward on the day the balance
    was asserted -- a savings sweep the statement shows leaving, a loan payment
    it shows going out, or money arriving from another account.  Ticking one
    records that the bank moved it by that day
    (:func:`app.services.reconcile_service._rows.record_settled`).

    **Every offer is CORRECTABLE, and that follows from ruling R-FF rather than
    being a choice made here.**  A tick is correctable exactly when the settle
    verb takes its MANUAL branch, and a transfer has no other branch to take: a
    shadow carries no template and a False ``is_envelope``, so it is never
    purchase-tracked and there are no entries for a figure to be derived from
    (measured on production: 342 shadows, 0 entries against any of them).  The
    box is PREFILLED with what the tick would book, so an untouched tick is an
    echo and writes nothing -- ``transfer_service.is_correction`` is what tells
    the two apart.

    **The tally follows the LEG and the section follows the ACT.**
    ``is_income`` puts the expense leg among the payments and the income leg
    among the deposits, because that is what a statement shows; ``kind`` is
    ``TRANSFER`` for both, because a transfer is one act and reads as one
    section (ruling **R-FC**).

    Reads only (no writes, no commit).

    Args:
        owner_id: The user_id whose rows to list.
        account_id: The cash account whose balance was asserted.
        observed_on: The civil day that balance was true for.

    Returns:
        ``{transaction_id: OutstandingTransaction}`` keyed on the SHADOW's own
        id -- which is the parent key the assembler unions on, and correctly
        so: a shadow can carry no purchases, so its block is always childless
        and never shares a key with another arm's.  Empty for an account
        holding no overdue transfer, which is production's state at its latest
        assertion today.
    """
    statement = _rows.Statement(owner_id, account_id, observed_on)
    return {
        shadow.id: OutstandingTransaction(
            transaction_id=shadow.id,
            attributed_on=_rows.attributed_on(shadow),
            amount=transfer_service.settle_amount(shadow),
            # Always the whole figure: a shadow can hold no entries, so there
            # is no card half for the statement to disagree with (N-226).
            cash_amount=None,
            is_correctable=True,
            is_income=shadow.is_income,
            kind=OfferKind.TRANSFER,
        )
        for shadow in _rows.outstanding_rows(arm(owner_id), statement)
    }
