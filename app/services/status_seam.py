"""
Shekel Budget App -- Transaction Status Seam

The single status-mechanics primitive for non-transfer transactions.
``apply_status_change`` is the ONE place a ``Transaction.status_id`` is assigned
outside the transfer service.  Every status-changing path -- the manual
``mark_done`` branch, the inline PATCH, ``cancel``, ``mark_as_credit`` /
``unmark_credit``, and the envelope ``settle_from_entries`` -- routes through it
so the status mechanics are uniform and impossible to skip; the
``shekel-transaction-status-bypass`` pylint checker (W9907) enforces the rule by
flagging any other ``status_id`` assignment.  It is the transaction analog of
``transfer_service._apply_status_change`` (which keeps its own private seam for a
transfer's two shadow rows).

Architecture:
  - A LOW-LEVEL primitive: it depends only on the state machine, the
    settled-status predicate, the session, and the model -- never on the
    higher-level services that call it (``transaction_service``,
    ``credit_workflow``, the route layer, and the future loan / paycheck settle
    paths in Build-Order Steps 4-5).  Living below its callers is what keeps it
    free of the ``transaction_service <- entry_service <- entry_credit_workflow
    <- credit_workflow`` import cycle: were the seam in ``transaction_service``
    (which imports ``entry_service``), ``credit_workflow`` could not import it
    without closing that cycle.
  - No Flask imports.  Mutates the passed Transaction in place; does NOT flush or
    commit -- the caller owns the session boundary.
"""

from datetime import datetime
from typing import Optional

from app.extensions import db
from app.models.transaction import Transaction
from app.services.state_machine import verify_transition
from app.utils.balance_predicates import settled_status_ids


def apply_status_change(
    txn: Transaction, new_status_id: int, *, paid_at: Optional[datetime] = None,
) -> None:
    """Apply a non-transfer status transition -- the single transaction status seam.

    The ONE place a non-transfer ``Transaction.status_id`` may be assigned.
    Every status-changing path -- the manual ``mark_done`` branch, the inline
    PATCH, ``cancel``, ``mark_as_credit`` / ``unmark_credit``, and the envelope
    ``transaction_service.settle_from_entries`` -- routes through here so the
    status mechanics are uniform and impossible to skip; the
    ``shekel-transaction-status-bypass`` pylint checker (W9907) enforces the rule
    by flagging any other ``status_id`` assignment outside this module and
    ``transfer_service`` (which mirrors ``status_id`` onto a transfer's two
    shadow rows).  Mirrors ``transfer_service._apply_status_change``.

    Does the status MECHANICS only, in order:

      1. ``verify_transition`` -- the state-machine legality gate; raises
         ``ValidationError`` on an illegal move (e.g. Settled -> Projected),
         which the route layer surfaces as a 400.
      2. assign ``status_id``.
      3. maintain ``paid_at`` (see the *paid_at* arg).
      4. ``db.session.expire(txn, ["status"])`` so a pre-commit reader (a cell
         render, a test assertion) sees the new ``Status`` row, not the stale
         ``lazy="joined"`` one -- the exact trap ``mark_as_credit`` documented
         and handled inline before this seam absorbed it.

    It deliberately does NOT post to the ledger and does NOT flush or commit:
    ledger emission is reconciled once at the END of each handler, after every
    effect field is applied, never at the status flip (Build-Order Step 3,
    Commit 6 -- the same placement ``transfer_service.update_transfer`` uses);
    the caller owns the session boundary.

    Args:
        txn: The Transaction whose status changes.  Must be session-attached so
            the ``status`` expire reloads; ``txn.status_id`` is read as the
            current state for the transition check.
        new_status_id: The ``ref.statuses.id`` to move to.
        paid_at: Payment-timestamp policy.  ``None`` (the default) DERIVES the
            timestamp from *new_status_id*: stamp ``db.func.now()`` on entering
            a settled status (Paid / Received / Settled) that has none yet,
            preserve an existing one on an idempotent re-settle, and clear it on
            entering a non-settled status (so a reverted / cancelled / credited
            row drops its stale payment time).  A non-``None`` ``datetime`` is
            written verbatim (carry-forward back-dating).  This differs
            deliberately from ``transfer_service`` -- where an explicit
            ``paid_at=None`` clears -- because no transaction caller needs to
            clear while settling, so ``None`` is free to mean "derive" and the
            seam needs no separate sentinel.

    Raises:
        ValidationError: If the transition is illegal for the transaction
            workflow (propagated from ``verify_transition``).
    """
    verify_transition(txn.status_id, new_status_id, context="transaction")
    txn.status_id = new_status_id

    # paid_at maintenance.  An explicit timestamp wins; otherwise derive from
    # the new status: clear when leaving the settled band, stamp now() on the
    # first entry into it, and leave an existing stamp untouched on a re-settle
    # (so editing a Paid row -- which re-submits its unchanged status_id -- never
    # churns the original payment time).
    if paid_at is not None:
        txn.paid_at = paid_at
    elif new_status_id not in settled_status_ids():
        txn.paid_at = None
    elif txn.paid_at is None:
        txn.paid_at = db.func.now()

    db.session.expire(txn, ["status"])
