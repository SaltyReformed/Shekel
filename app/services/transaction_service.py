"""
Shekel Budget App -- Transaction Service

Cross-cutting transaction state-change helpers used by multiple
routes and services.  Each function mutates a Transaction in place
and leaves the session/commit lifecycle to the caller, matching the
pattern in ``app/services/entry_service.py``.

Architecture:
  - No Flask imports.  Receives ORM objects, mutates them, and
    raises domain exceptions on precondition violation.
  - All monetary arithmetic uses ``Decimal`` (the helpers delegate
    to ``app.services.entry_service.compute_actual_from_entries``).
  - Does NOT flush or commit -- the caller owns the transaction
    boundary so the helper can safely participate in larger atomic
    operations (e.g. the carry-forward batch in Phase 4).
"""

from datetime import datetime
from typing import Optional

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services.entry_service import compute_actual_from_entries


def settle_from_entries(
    txn: Transaction, *, paid_at: Optional[datetime] = None,
) -> None:
    """Settle a tracked-envelope transaction at sum(entries).

    Drives the entry-sum branch of the manual ``mark_done`` route and
    the source-side settlement step of the carry-forward envelope
    branch (see ``docs/carry-forward-aftermath-design.md`` Option F).
    Both call sites need the same three writes -- ``status_id``,
    ``paid_at``, ``actual_amount`` -- so the logic lives here as a
    single source of truth.

    Effect on *txn* (in place):
      - ``actual_amount`` is set to ``sum(e.amount for e in txn.entries)``,
        which is ``Decimal("0")`` when ``txn.entries`` is empty.  Empty
        entries on an envelope row settle at zero spend (the carry-forward
        branch then folds the full estimated amount into the next
        period's canonical row).
      - ``status_id`` is set to ``DONE`` for expense transactions and
        ``RECEIVED`` for income transactions, matching the display
        convention used by ``app/routes/transactions.py:mark_done``.
      - ``paid_at`` is set to the explicit *paid_at* argument when
        provided; otherwise ``db.func.now()`` so PostgreSQL evaluates
        the timestamp at flush time (consistent with the existing
        ``mark_done`` route behaviour).

    The function does NOT flush or commit -- the caller owns the
    session boundary so the settlement can participate in a larger
    atomic operation (e.g. the carry-forward batch).

    Preconditions (defensively validated, not assumed):

      1. ``txn.is_deleted`` is False.  Soft-deleted rows must not be
         resurrected via a status change.
      2. ``txn.template`` is not None and ``txn.template.is_envelope``
         is True.  Envelope semantics are the contract this helper
         relies on; calling on a non-envelope or template-less row is
         a programming error and surfaces as a ``ValidationError``.
      3. ``txn.transfer_id`` is None.  Transfer shadows must settle
         through ``app.services.transfer_service.update_transfer`` so
         both shadow legs and the parent transfer stay in sync (see
         transfer invariants in CLAUDE.md).
      4. ``txn.status`` is mutable (``status.is_immutable`` is False).
         The only mutable status in the current schema is ``Projected``;
         settling a row that is already Paid, Received, Cancelled,
         Credit, or Settled is meaningless and indicates a caller bug.

    Args:
        txn: The Transaction to settle.  Must be attached to the
            current SQLAlchemy session so the entries relationship
            resolves correctly.
        paid_at: Optional explicit timestamp.  When None (the default)
            the helper uses ``db.func.now()`` so the timestamp comes
            from the database server at flush time.

    Raises:
        ValidationError: If any precondition is violated.  The error
            message names the txn ID and the specific violated rule
            so the route layer can surface an actionable message.

    Returns:
        None.  Mutations are applied in place; the caller is
        responsible for committing the surrounding transaction.
    """
    if txn.is_deleted:
        raise ValidationError(
            f"Transaction {txn.id} is soft-deleted; "
            "settle_from_entries cannot resurrect deleted rows.",
        )
    if txn.template is None or not txn.template.is_envelope:
        raise ValidationError(
            f"Transaction {txn.id} is not envelope-tracked; "
            "settle_from_entries requires template.is_envelope is True.",
        )
    if txn.transfer_id is not None:
        raise ValidationError(
            f"Transaction {txn.id} is a transfer shadow; "
            "transfers settle via transfer_service.update_transfer.",
        )
    # Guard against settling an already-finalised row.  ``status`` may
    # be unloaded if the caller passed a detached or freshly-constructed
    # transaction; treat the missing relationship as a precondition
    # violation rather than silently coercing it to mutable.
    if txn.status is None or txn.status.is_immutable:
        status_label = (
            txn.status.name if txn.status is not None else "<unset>"
        )
        raise ValidationError(
            f"Transaction {txn.id} has an immutable status "
            f"({status_label!r}); settle_from_entries requires a "
            "mutable status (Projected).",
        )

    # Determine the destination status from the transaction type.
    # Mirrors the income/expense split in
    # app/routes/transactions.py:mark_done so the helper produces an
    # identical observable result on tracked-envelope rows.
    if txn.is_income:
        new_status_id = ref_cache.status_id(StatusEnum.RECEIVED)
    else:
        new_status_id = ref_cache.status_id(StatusEnum.DONE)

    txn.status_id = new_status_id
    txn.paid_at = paid_at if paid_at is not None else db.func.now()
    # ``compute_actual_from_entries`` returns Decimal("0") on an empty
    # list, which is the carry-forward "no spend, full rollover" case.
    txn.actual_amount = compute_actual_from_entries(txn.entries)
