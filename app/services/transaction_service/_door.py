"""
Shekel Budget App -- Transaction Service: what a DOOR's requested status means

The route layer's ONE status entry point.  A door states the status the USER
asked for; :func:`apply_requested_status` decides what applying it means --
dispatching a settle to the verb, taking back what a settle derived on the way
out, and reconciling the posted ledger either way.

**It exists because a ROUTE was making that decision, and making it wrong**
(finding **N-219**, plan step X-ap): the transaction PATCH handler called the
status seam directly, so marking a row Paid from the full-edit popover flipped
the status without asking what the row was WORTH.

Flask-isolated: plain data and ORM rows in, mutations applied in place, no
``request`` / ``session`` imports, no commit.
"""

from datetime import date

from app.services import posting_service
from app.models.transaction import Transaction
from app.services.status_seam import apply_status_change
from app.services.transaction_service._settle import (
    settle_transaction,
    settles_from_entries,
)
from app.services.transaction_service._status_rules import (
    reject_mismatched_settled_status,
)
from app.utils.balance_predicates import (
    enters_settled_band,
    leaves_settled_band,
)


def _release_derived_actual(txn: Transaction) -> None:
    """Drop an envelope's DERIVED actual when the row stops being settled.

    **A settle writes ``actual_amount`` for an envelope; a revert must take it
    back**, because what that column holds for such a row is not a fact the
    user authored -- it is ``sum(entries)`` at the moment of the settle, which
    :func:`._settle.settle_from_entries` wrote in the same statement as the status and
    the settle day.  The seam already clears ``settled_on`` on the way out, on
    exactly this reasoning; the derived amount is the same kind of value and was
    being left behind.

    **Measured, and it is why this exists rather than being argued.**
    Production row 2281 *Groceries* is Projected today carrying
    ``actual_amount = 533.08`` against a `$500.00` budget -- written by
    ``settle_from_entries`` (audit_log id 2978, one statement with ``status_id``
    and ``settled_on``) and left behind by a later revert.  The valuation
    is ``COALESCE(actual, estimated)``, so that row projects at its SPEND rather
    than its budget, and a purchase deleted while it is Projected does not move
    the figure: :func:`app.services.entry_service._resync_settled_envelope` is
    gated on the settled band, correctly, because a Projected row's actual is
    not yet a fact.  The result is a stored derived value with nothing that can
    re-derive it.

    **Only the DERIVED kind is released**, which is what makes this narrow
    enough to be right: a BILL's ``actual_amount`` is a figure a HUMAN read off
    a statement (ruling **R-FB**), and clearing that on a revert would delete
    the user's own correction.  :func:`._settle.settles_from_entries` is the same
    predicate the settle branches on and the same one the edit doors offer an
    amount box on (ruling **R-FF**), so a row's amount is derived, correctable
    and released by ONE rule rather than three.

    Mutates in place; does NOT flush or commit.

    Args:
        txn: The row leaving the settled band, still in its settled status.
            Read for ``tracks_purchases`` and ``entries``.
    """
    if settles_from_entries(txn):
        txn.actual_amount = None


def apply_requested_status(
    txn: Transaction,
    new_status_id: int,
    *,
    settled_on: date | None = None,
) -> None:
    """Apply the status a DOOR requested, and reconcile the ledger to it.

    **The route layer's ONE status entry point, and the reason it exists is
    that a route was deciding what a status change MEANS.**  The transaction
    PATCH handler and the cancel handler each called
    ``status_seam.apply_status_change`` directly -- the MECHANICS primitive,
    which verifies the transition, assigns the column and maintains the settle
    day, and deliberately does nothing else.  For "the user cancelled this row"
    the mechanics ARE the whole act.  For "the user marked this row Paid" they
    are not: settling also decides what the row is WORTH
    (:func:`._settle.settle_transaction`), and a door that flips the status without
    asking books whatever figure the row happened to be carrying.  That is
    finding **N-219** on the transaction PATCH door, and the shape of it is a
    ROUTE holding a money rule -- this arc's own root cause 1.

    So the routes stop choosing.  A door states the status the USER asked for
    and this decides what applying it means.  After plan step X-ap the only
    ``app/`` callers of the seam are this function, :func:`._settle.settle_transaction`,
    :func:`._settle.settle_from_entries`, ``credit_workflow`` (Credit and its revert,
    neither of them settled statuses) and ``transfer_service._status`` (a transfer and
    its two shadows, which settle through ``transfer_service`` by transfer
    invariants 3 and 4) -- so a FOURTH transaction settle door cannot be opened
    by reaching for the obvious primitive, which is how the third one was.

    **The ledger reconcile is here rather than at each door** for the reason
    :func:`._settle.settle_transaction` states for its own: every status change moves
    the row's posted effect (a settle posts it, a cancel reverses it), and a
    door that forgets posts nothing while reporting success.  It is reconciled
    LAST, after the caller's own field writes, so it reads the final amount and
    category rather than the pre-edit ones -- the discipline
    ``transfer_service.update_transfer`` documents.  A caller that edits
    posting-relevant fields WITHOUT changing the status still owes its own
    reconcile; there is no status change for this function to hang one on.

    Does NOT flush or commit -- the caller owns the session boundary.

    Args:
        txn: The transaction whose status the door is changing.  Must be a
            REGULAR row: a transfer shadow's status is its parent's
            (``transfer_service.update_transfer``), and the PATCH route branches
            a shadow away before it reaches here.
        new_status_id: The ``ref.statuses.id`` the door asked for -- the
            SUBMITTED status when the form carried one, else the row's own (an
            edit that changes only the settle day is an identity transition).
        settled_on: The civil day the money moved, when the door knows it, after
            the door's own :func:`app.services.status_seam.settle_day_for_status`
            reading of the submission.  ``None`` leaves the seam's rule in force.

    Raises:
        ValidationError: From an illegal transition or the seam's settle-day
            refusals.  A 400 at the route.
        PostingError: From the reconcile, on a broken ledger invariant.
            Deliberately NOT a sibling of ``ValidationError`` -- it must fail
            loud rather than render as a designed refusal.
    """
    # THE DISPATCH, and it is the whole of finding **N-219**'s fix.  Moving a
    # row INTO the settled band is a SETTLE, which decides an amount before it
    # decides a status; every other status change is the mechanics alone.  The
    # verb reconciles the ledger itself, so this arm returns rather than falling
    # through to a second reconcile of the same row.
    if enters_settled_band(txn, new_status_id):
        reject_mismatched_settled_status(txn, new_status_id)
        settle_transaction(txn, settled_on=settled_on)
        return
    # The other direction is the settle's own act undone: an envelope's
    # ``actual_amount`` was DERIVED from its entries by the settle, so leaving
    # the band takes it back.  Read BEFORE the seam and applied AFTER it, and
    # both halves of that matter -- the predicate is about the status the row
    # is LEAVING, and a refused transition must leave the row untouched (the
    # ordering ``apply_status_change`` uses for its own three refusals).  It
    # lands before the reconcile below, which reads the row's contribution.
    releases_derived_actual = leaves_settled_band(txn, new_status_id)
    apply_status_change(txn, new_status_id, settled_on=settled_on)
    if releases_derived_actual:
        _release_derived_actual(txn)
    posting_service.sync_transaction_postings(
        txn, settled=txn.status.is_settled,
    )
