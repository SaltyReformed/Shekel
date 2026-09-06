"""
Shekel Budget App -- The TRANSFER arm of the outstanding set

The last of the package's three arms (see :mod:`app.services.reconcile_service`
for what an arm is): a TRANSFER's shadow on the account whose balance was
asserted.  Money moving between two of the owner's own accounts still leaves
one of them, so a checking statement shows it exactly as it shows a bill -- and
until plan step X-f2-c3 the panel could not settle one.  **Replayed through
this producer over all 53 of production's Checking assertion DAYS** -- on a
throwaway clone, with each day's transfer shadows restored to the status they
held then -- **8 days would have carried an offer, 8 rows worth `$5,442.89`**:
six `$500.00` savings sweeps, one `$1,910.95` Mortgage payment and one
`$531.94` Van Loan payment, every one of them an expense leg.  That reproduces
ruling **R-FA**'s own OF-WHICH figure to the cent.

**53 DAYS, not 57**: the account carries 57 assertion ROWS over 53 distinct
days, and X-f2-c's own text records that confusing the two is how a figure in
this arc went wrong before.  At the LATEST assertion (2026-08-06) this arm
offers NOTHING, because every projected shadow starts 2026-08-13 -- so the
panel is unchanged on production today.

**Its settle is ``transfer_service.settle_transfer``, and that is the whole
reason it is a separate arm** (ruling **R-FA**).  A transfer is THREE rows -- a
parent and two shadows -- and ``CLAUDE.md`` transfer invariants 3 and 4 say they
move together, so ``transaction_service.settle_transaction`` REFUSES a shadow
outright.  Ticking one here therefore settles the leg on the OTHER account too,
which is a fact about the act rather than about any row and is why the panel
prints it once under the section heading
(:attr:`~app.services.reconcile_service.OfferKind.section_note`).

**Nothing here decides what a tick BOOKS, which status the rows take, or
whether a submitted figure is a CORRECTION.**  All three are the transfer
service's.  What a tick will book is published as
``transfer_service.settle_amount`` so the panel can render it; whether a figure
was a human's is the VERB's own answer, returned by the settle rather than
asked of a predicate beforehand.  The loan-payment FREEZE the step
specification names is inside both: an auto-derived loan payment books its live
payment-date figure rather than the creation-time escrow its estimate carries,
and because the panel's figure and the booked figure come from one expression
they cannot drift.

**Its scope, bound and loader are :mod:`._rows`'** -- the same ones the
transaction arm uses, with the complementary membership clause.  What stays
here is which rows are this arm's, what one is worth, and what a tick means.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, frozen dataclasses out.
  - All monetary arithmetic uses :class:`~decimal.Decimal`.
  - The writer mutates and does NOT commit -- the caller owns the session
    boundary.
"""

from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import transfer_service
from app.services.cash_ledger import AmountBasis
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

    The submitted figure and the statement's day are handed straight through:
    what a tick BOOKS, whether the figure is written at all, and which status
    the three rows take are the transfer service's rules and are not restated
    here.

    Args:
        shadow: The leg on this account, still Projected.
        submitted: The figure the panel's amount box posted, or ``None``.
        statement: The statement being reconciled; its day is what both legs
            record the money as having moved on, on the ``asserted`` basis --
            the owner asserted a BALANCE for that day, so the day bounds the
            movement from above rather than naming it (plan step **X-az**).

    Returns:
        Whether the verb booked *submitted* as a human's correction -- the
        verb's OWN answer about what it just did, which is finding **N-231**'s
        rule (a count read off the column afterwards cannot tell a human's
        figure from a machine's).  Taking it from the act rather than from a
        predicate asked beforehand is also what stops the loan freeze being
        resolved twice for one tick.
    """
    corrected = transfer_service.settle_transfer(
        shadow.transfer_id, statement.owner_id,
        submitted=submitted,
        settle_day=statement.settle_day,
    )
    # WHICH statement showed THIS LEG (ruling **R-FL**), through the transfer
    # service because the row is a SHADOW and ``CLAUDE.md``'s transfer invariant
    # 4 admits no direct mutation of one.  Only this leg takes it, even though
    # the settle above moved both: the other leg is on another account, whose
    # own statement nobody read in this act.  ``transfer_service.record_clearing``
    # carries why that asymmetry is correct.
    transfer_service.record_clearing(shadow, statement.anchor.anchor_id)
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
      would send ``settle_transfer`` looking for a row it treats as absent --
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
    statement: _rows.Statement, basis: "AmountBasis",
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
    echo and writes nothing -- ``transfer_service.settle_transfer`` tells the
    two apart as it settles, and says which it was in its return value.

    **The tally follows the LEG and the section follows the ACT.**
    ``is_income`` puts the expense leg among the payments and the income leg
    among the deposits, because that is what a statement shows; ``kind`` is
    ``TRANSFER`` for both, because a transfer is one act and reads as one
    section (ruling **R-FC**).

    Reads only (no writes, no commit).

    Args:
        statement: The :class:`~._rows.Statement` being reconciled -- whose
            calendar, which account, which assertion.  **Built ONCE by
            :func:`~._assemble.outstanding_set` and threaded**; see
            :func:`~._transactions.outstanding_transactions` for why one value
            rather than three arguments, which is pay-calendar plan step
            C4-a-2's doing.
        basis: The PANEL's :class:`~app.services.cash_ledger.AmountBasis`,
            built ONCE by :func:`~._assemble.outstanding_set` and threaded
            (plan step X-au-j, finding **N-295**).  This is the EXPENSIVE half of that
            finding: each offered shadow built its own basis and so paid the
            scenario-wide loan-config join, plus a full loan resolve for every
            derive-mode payment -- finding **N-269** reintroduced one tier up,
            exactly as N-295's impact column predicted.

    Returns:
        ``{transaction_id: OutstandingTransaction}`` keyed on the SHADOW's own
        id -- which is the parent key the assembler unions on, and correctly
        so: a shadow can carry no purchases, so its block is always childless
        and never shares a key with another arm's.  Empty for an account
        holding no overdue transfer, which is production's state at its latest
        assertion today.
    """
    return {
        shadow.id: OutstandingTransaction(
            transaction_id=shadow.id,
            attributed_on=_rows.attributed_on(statement, shadow),
            amount=transfer_service.settle_amount(shadow, basis),
            # Always the whole figure: a shadow can hold no entries, so there
            # is no card half for the statement to disagree with (N-226).
            cash_amount=None,
            is_correctable=True,
            is_income=shadow.is_income,
            kind=OfferKind.TRANSFER,
        )
        for shadow in _rows.outstanding_rows(
            arm(statement.owner_id), statement,
        )
    }
