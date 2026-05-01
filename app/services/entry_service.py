"""
Shekel Budget App -- Transaction Entry Service

CRUD operations, validation, and computation for individual purchase
entries on entry-capable transactions.  This service is the foundation
consumed by the balance calculator (Commit 3), entry credit workflow
(Commit 4), mark-paid logic (Commit 5), and all entry UI (Commits
7, 8, 10).

Architecture:
  - No Flask imports.  Receives plain data, returns ORM objects or
    raises exceptions.
  - All monetary arithmetic uses Decimal.
  - Flushes to the session but does NOT commit.  The caller owns the
    database transaction boundary.
"""

import logging
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.user import User
from app import ref_cache
from app.enums import RoleEnum, StatusEnum
from app.exceptions import NotFoundError, ValidationError
from app.services.entry_credit_workflow import sync_entry_payback

logger = logging.getLogger(__name__)

# Fields that can be updated on an entry via update_entry().
_UPDATABLE_FIELDS = frozenset({"amount", "description", "entry_date", "is_credit"})


def _update_actual_if_paid(txn: Transaction) -> None:
    """Re-compute actual_amount if the transaction is already Paid.

    Handles the edge case of entries added/edited/deleted after the
    transaction was marked Paid (late-posting purchases).  Per scope
    doc section 4.2: the entry sum takes precedence over any manually
    entered actual once entries exist.

    Only fires for DONE status -- RECEIVED (income) never has entries,
    and SETTLED transactions are considered finalized.

    When entries are empty (e.g. all entries deleted from a Paid txn),
    actual_amount is left unchanged so the previous value persists.
    The user can manually correct it via the full edit form.

    Does NOT commit or flush -- the calling service function owns the
    session boundary.

    Args:
        txn: The parent Transaction object.
    """
    done_id = ref_cache.status_id(StatusEnum.DONE)
    if txn.status_id == done_id and txn.entries:
        txn.actual_amount = compute_actual_from_entries(txn.entries)


def resolve_owner_id(user_id: int) -> int:
    """Return the data-owning user_id.

    For owner accounts, returns user_id unchanged.  For companion
    accounts, returns the linked_owner_id (the owner whose budget
    data the companion has access to).

    Args:
        user_id: The ID of the requesting user.

    Returns:
        int -- the ID of the user who owns the budget data.

    Raises:
        NotFoundError: If user_id does not correspond to an existing user.
        ValidationError: If a companion user has no linked_owner_id
            (indicates a data integrity issue).
    """
    user = db.session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")
    companion_role_id = ref_cache.role_id(RoleEnum.COMPANION)
    if user.role_id == companion_role_id:
        if user.linked_owner_id is None:
            raise ValidationError(
                f"Companion user {user_id} has no linked owner. "
                "This is a data integrity issue -- contact the administrator."
            )
        return user.linked_owner_id
    return user.id


# Backward-compatible alias -- existing tests reference the private name.
_resolve_owner_id = resolve_owner_id


def create_entry(
    transaction_id: int,
    user_id: int,
    amount: Decimal,
    description: str,
    entry_date: date,
    is_credit: bool = False,
) -> TransactionEntry:
    """Create a new purchase entry against a transaction.

    Validates ownership (including companion resolution), entry
    capability, transfer guard, expense-only guard, and status guard
    before creating the entry.

    Args:
        transaction_id: Parent transaction ID.
        user_id: The creating user's ID (owner or companion).
        amount: Positive Decimal for the purchase amount.
        description: Store name or brief note (1--200 chars).
        entry_date: Date of the purchase.
        is_credit: Whether this was paid with a credit card.

    Returns:
        The newly created TransactionEntry (flushed, id available).

    Raises:
        NotFoundError: Transaction not found or not accessible by this
            user.
        ValidationError: Transaction not entry-capable, is a transfer,
            is income, or has a blocked status (Cancelled or Credit).
    """
    owner_id = resolve_owner_id(user_id)

    txn = db.session.get(Transaction, transaction_id)
    if txn is None:
        raise NotFoundError(f"Transaction {transaction_id} not found.")

    # Ownership: verify via pay period (security response rule: 404).
    if txn.pay_period.user_id != owner_id:
        raise NotFoundError(f"Transaction {transaction_id} not found.")

    # Entry-capable: template must exist and have tracking enabled.
    if txn.template is None or not txn.template.is_envelope:
        raise ValidationError(
            "This transaction does not support individual purchase tracking. "
            "Enable 'Track individual purchases' on the template first."
        )

    # Transfer guard (mirrors credit_workflow.py line 59).
    if txn.transfer_id is not None:
        raise ValidationError("Cannot add entries to transfer transactions.")

    # Expense-only guard.
    if txn.is_income:
        raise ValidationError(
            "Cannot add purchase entries to income transactions."
        )

    # Status guard: CANCELLED and CREDIT transactions cannot accept entries.
    # CANCELLED is excluded from balance -- adding entries makes no sense.
    # CREDIT is blocked for entry-capable templates (OQ-10) -- credit
    # handling happens at the entry level, not the transaction level.
    cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)
    credit_id = ref_cache.status_id(StatusEnum.CREDIT)
    if txn.status_id == cancelled_id:
        raise ValidationError(
            "Cannot add entries to a cancelled transaction."
        )
    if txn.status_id == credit_id:
        raise ValidationError(
            "Cannot add entries to a transaction with Credit status. "
            "Entry-capable transactions handle credit at the entry level."
        )

    entry = TransactionEntry(
        transaction_id=transaction_id,
        user_id=user_id,
        amount=amount,
        description=description,
        entry_date=entry_date,
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()

    logger.info(
        "Created entry %d on transaction %d (amount=%s, credit=%s)",
        entry.id, transaction_id, amount, is_credit,
    )

    sync_entry_payback(transaction_id, owner_id)
    _update_actual_if_paid(txn)

    return entry


def update_entry(entry_id: int, user_id: int, **kwargs) -> TransactionEntry:
    """Update an existing entry.

    Allowed fields: amount, description, entry_date, is_credit.
    Re-validates ownership through the entry's parent transaction.

    Args:
        entry_id: The entry to update.
        user_id: The requesting user's ID (owner or companion).
        **kwargs: Fields to update (must be a subset of allowed fields).

    Returns:
        The updated TransactionEntry.

    Raises:
        NotFoundError: Entry not found or not accessible.
        ValidationError: If no valid fields provided or unknown fields
            are passed.
    """
    unknown = set(kwargs) - _UPDATABLE_FIELDS
    if unknown:
        raise ValidationError(
            f"Cannot update fields: {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(_UPDATABLE_FIELDS))}."
        )

    valid_updates = {k: v for k, v in kwargs.items() if k in _UPDATABLE_FIELDS}
    if not valid_updates:
        raise ValidationError("No fields to update.")

    entry = db.session.get(TransactionEntry, entry_id)
    if entry is None:
        raise NotFoundError(f"Entry {entry_id} not found.")

    # Re-validate ownership through the parent transaction chain.
    owner_id = resolve_owner_id(user_id)
    if entry.transaction.pay_period.user_id != owner_id:
        raise NotFoundError(f"Entry {entry_id} not found.")

    for field, value in valid_updates.items():
        setattr(entry, field, value)
    db.session.flush()

    logger.info("Updated entry %d: %s", entry_id, valid_updates)

    sync_entry_payback(entry.transaction_id, owner_id)
    _update_actual_if_paid(entry.transaction)

    return entry


def delete_entry(entry_id: int, user_id: int) -> int:
    """Hard-delete an entry.

    Re-validates ownership before deleting.  Returns the parent
    transaction_id so the caller (e.g. entry credit workflow in
    Commit 4) can sync the CC Payback amount.

    Args:
        entry_id: The entry to delete.
        user_id: The requesting user's ID (owner or companion).

    Returns:
        int -- the parent transaction_id.

    Raises:
        NotFoundError: Entry not found or not accessible.
    """
    entry = db.session.get(TransactionEntry, entry_id)
    if entry is None:
        raise NotFoundError(f"Entry {entry_id} not found.")

    # Re-validate ownership through the parent transaction chain.
    owner_id = resolve_owner_id(user_id)
    if entry.transaction.pay_period.user_id != owner_id:
        raise NotFoundError(f"Entry {entry_id} not found.")

    txn = entry.transaction
    transaction_id = entry.transaction_id
    db.session.delete(entry)
    db.session.flush()

    logger.info(
        "Deleted entry %d from transaction %d", entry_id, transaction_id,
    )

    sync_entry_payback(transaction_id, owner_id)
    _update_actual_if_paid(txn)

    return transaction_id


def get_entries_for_transaction(
    transaction_id: int, user_id: int,
) -> list[TransactionEntry]:
    """Return all entries for a transaction, ordered by entry_date ASC.

    Validates ownership before returning entries.

    Args:
        transaction_id: The parent transaction ID.
        user_id: The requesting user's ID (owner or companion).

    Returns:
        List of TransactionEntry objects ordered by entry_date ASC.

    Raises:
        NotFoundError: Transaction not found or not accessible.
    """
    owner_id = resolve_owner_id(user_id)

    txn = db.session.get(Transaction, transaction_id)
    if txn is None:
        raise NotFoundError(f"Transaction {transaction_id} not found.")
    if txn.pay_period.user_id != owner_id:
        raise NotFoundError(f"Transaction {transaction_id} not found.")

    # The entries relationship is ordered by entry_date via order_by
    # on the Transaction model (transaction.py line 132).
    return list(txn.entries)


def compute_entry_sums(
    entries: list[TransactionEntry],
) -> tuple[Decimal, Decimal]:
    """Compute (sum_debit, sum_credit) from a list of entries.

    Pure function -- no database access.

    Args:
        entries: List of TransactionEntry objects.

    Returns:
        Tuple of (sum_debit, sum_credit) as Decimals.
    """
    sum_debit = Decimal("0")
    sum_credit = Decimal("0")
    for entry in entries:
        if entry.is_credit:
            sum_credit += entry.amount
        else:
            sum_debit += entry.amount
    return sum_debit, sum_credit


def build_entry_sums_dict(
    transactions: list,
) -> dict[int, dict]:
    """Build a {txn_id: sums_dict} mapping for transactions with entries.

    Used by grid routes and HTMX cell-render endpoints to pre-compute
    entry aggregates for the cell template.  Only transactions with
    non-empty entries are included in the result.

    Pure function -- no database access beyond what was already loaded
    on the Transaction objects (expects entries to be accessible, either
    via eager load or lazy access).

    Args:
        transactions: List of Transaction objects with entries accessible.

    Returns:
        dict mapping transaction ID to {"debit": Decimal, "credit": Decimal,
        "total": Decimal, "count": int}.  Empty dict if no transactions
        have entries.
    """
    result: dict[int, dict] = {}
    for txn in transactions:
        if txn.entries:
            debit, credit = compute_entry_sums(txn.entries)
            result[txn.id] = {
                "debit": debit,
                "credit": credit,
                "total": debit + credit,
                "count": len(txn.entries),
            }
    return result


def compute_remaining(
    estimated_amount: Decimal,
    entries: list[TransactionEntry],
) -> Decimal:
    """Compute remaining budget: estimated_amount - sum of ALL entries.

    Uses the sum of ALL entries regardless of payment method (debit +
    credit) because the remaining balance represents budget consumption,
    not checking impact.  Negative values indicate overspending.

    Pure function -- no database access.

    Args:
        estimated_amount: The transaction's budgeted amount.
        entries: List of TransactionEntry objects.

    Returns:
        Decimal -- the remaining budget (negative means overspent).
    """
    total_spent = sum((e.amount for e in entries), Decimal("0"))
    return estimated_amount - total_spent


def compute_actual_from_entries(
    entries: list[TransactionEntry],
) -> Decimal:
    """Compute actual_amount for a Paid transaction: sum of ALL entries.

    The actual_amount represents total spending for analytics and
    reporting.  Both debit and credit entries contribute to the total.
    The credit portion is already handled by the CC Payback in the
    next period.

    Pure function -- no database access.

    Args:
        entries: List of TransactionEntry objects.

    Returns:
        Decimal -- sum of all entry amounts (Decimal("0") if empty).
    """
    return sum((e.amount for e in entries), Decimal("0"))


def check_entry_date_in_period(
    entry_date: date,
    transaction: Transaction,
) -> bool:
    """Check whether an entry date falls within the pay period range.

    Informational utility for UI warnings (OP-4).  Does NOT block
    entry creation or updates -- late-posting purchases may
    legitimately fall outside the period range.

    Args:
        entry_date: The date to check.
        transaction: The parent Transaction (with pay_period loaded).

    Returns:
        True if entry_date is within [start_date, end_date], False
        otherwise.
    """
    period = transaction.pay_period
    return period.start_date <= entry_date <= period.end_date


def clear_entries_for_anchor_true_up(owner_id: int) -> int:
    """Mark past-dated entries on projected parents as reconciled.

    Called from the checking-account anchor true-up routes.  The
    semantic contract: "the owner just looked at their real checking
    balance and entered it as the new anchor, so every debit purchase
    that had already posted is now reflected in that number."  We
    flip those entries to is_cleared=TRUE so the balance calculator
    stops holding back the full estimated amount (see bug fix in
    balance_calculator.py _entry_aware_amount).

    Scope:
      - Entries whose parent transaction belongs to this owner
        (via pay_period.user_id).
      - Parent transaction is not soft-deleted.
      - Parent transaction is in Projected status (settled parents
        are already excluded from the entry formula, so their entries
        do not affect balances either way).
      - Entry date is on or before today (future-dated entries cannot
        have posted to checking yet, so leave them uncleared).
      - Entry is_cleared is currently FALSE (no-op otherwise).

    Credit entries are included in the flip for consistency, but the
    balance calculator ignores is_cleared on credit entries, so this
    has no effect on balances for credit rows.

    Does NOT commit -- the calling route owns the session boundary so
    the anchor history row and the cleared flips land in the same DB
    transaction.

    Args:
        owner_id: The user_id whose entries should be reconciled.

    Returns:
        int -- number of entry rows updated.
    """
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    today = date.today()

    # Synchronize_session='fetch' because later code in the same request
    # (e.g. balance calculator rendering the grid) may hold refs to the
    # affected entry rows and needs to see the updated flag.
    updated = (
        db.session.query(TransactionEntry)
        .filter(
            TransactionEntry.is_cleared.is_(False),
            TransactionEntry.entry_date <= today,
            TransactionEntry.transaction_id.in_(
                db.session.query(Transaction.id)
                .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
                .filter(
                    PayPeriod.user_id == owner_id,
                    Transaction.is_deleted.is_(False),
                    Transaction.status_id == projected_id,
                )
            ),
        )
        .update(
            {TransactionEntry.is_cleared: True},
            synchronize_session="fetch",
        )
    )

    if updated:
        logger.info(
            "Cleared %d transaction entries on anchor true-up for user %d",
            updated, owner_id,
        )

    return updated


def toggle_cleared(entry_id: int, user_id: int) -> TransactionEntry:
    """Flip the is_cleared flag on a single entry.

    The manual override for cases where the auto-clear on anchor
    true-up is wrong for a specific purchase (e.g. a debit that
    posted after the user's most recent anchor update, or an entry
    the user wants to exclude from the reservation before they've
    formally updated the anchor).

    Re-validates ownership through the entry's parent transaction.
    Does NOT commit -- the caller owns the session boundary.

    Args:
        entry_id: The entry to toggle.
        user_id: The requesting user's ID (owner or companion).

    Returns:
        The updated TransactionEntry.

    Raises:
        NotFoundError: Entry not found or not accessible.
    """
    entry = db.session.get(TransactionEntry, entry_id)
    if entry is None:
        raise NotFoundError(f"Entry {entry_id} not found.")

    owner_id = resolve_owner_id(user_id)
    if entry.transaction.pay_period.user_id != owner_id:
        raise NotFoundError(f"Entry {entry_id} not found.")

    entry.is_cleared = not entry.is_cleared
    db.session.flush()

    logger.info(
        "Toggled entry %d is_cleared to %s", entry_id, entry.is_cleared,
    )

    return entry
