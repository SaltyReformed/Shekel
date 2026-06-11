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
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.user import User
from app import ref_cache
from app.enums import RoleEnum
from app.exceptions import NotFoundError, ValidationError
from app.services.entry_credit_workflow import sync_entry_payback
from app.utils.balance_predicates import (
    is_cancelled,
    is_done,
    is_projected_clause,
)
# ``is_credit`` from balance_predicates collides with the
# ``is_credit: bool`` keyword argument on this module's
# ``create_entry`` / ``update_entry`` functions.  Aliasing the
# predicate keeps both the helper accessible and the public
# function signatures stable.
from app.utils.balance_predicates import is_credit as txn_is_credit
from app.utils.entry_partition import partition_entries
from app.utils.log_events import (
    BUSINESS,
    EVT_ENTRIES_CLEARED_ON_ANCHOR_TRUEUP,
    EVT_ENTRY_CLEARED_TOGGLED,
    EVT_ENTRY_CREATED,
    EVT_ENTRY_DELETED,
    EVT_ENTRY_UPDATED,
    log_event,
)
from app.utils.money import percent_complete

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
    # Centralized ``is_done`` predicate (D6-09 / MED-02) so the
    # actual-recompute trigger shares one definition with every
    # other per-status equality check in the project.
    if is_done(txn) and txn.entries:
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


@dataclass(frozen=True)
class EntryDetails:
    """The content of a purchase entry -- the add-purchase form's inputs.

    The user-supplied fields of a :class:`TransactionEntry` (what was
    bought, how much, when, and whether paid by credit card), bundled so
    ``create_entry`` takes them as one cohesive argument distinct from the
    routing/ownership context (the parent transaction and the acting user).

    Fields:
        amount:      Positive Decimal for the purchase amount.
        description: Store name or brief note (1--200 chars).
        entry_date:  Date of the purchase.
        is_credit:   Whether this was paid with a credit card.
    """

    amount: Decimal
    description: str
    entry_date: date
    is_credit: bool = False


def create_entry(
    transaction_id: int,
    user_id: int,
    details: EntryDetails,
) -> TransactionEntry:
    """Create a new purchase entry against a transaction.

    Validates ownership (including companion resolution), entry
    capability, transfer guard, expense-only guard, and status guard
    before creating the entry.

    Args:
        transaction_id: Parent transaction ID.
        user_id: The creating user's ID (owner or companion).
        details: :class:`EntryDetails` -- the purchase content (amount,
            description, entry_date, is_credit).

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

    # Entry-capable: purchase tracking must be enabled, via the template
    # (template-generated rows) or the row's own is_envelope flag (ad-hoc
    # rows).  Resolved by Transaction.tracks_purchases.
    if not txn.tracks_purchases:
        raise ValidationError(
            "This transaction does not support individual purchase tracking. "
            "Enable 'Track individual purchases' on the transaction "
            "or its template first."
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
    # Routed through the centralized per-status predicates
    # (D6-09 / MED-02) so the two guards share one definition with
    # every other ``status == cancelled`` / ``status == credit``
    # comparison in the project.
    if is_cancelled(txn):
        raise ValidationError(
            "Cannot add entries to a cancelled transaction."
        )
    if txn_is_credit(txn):
        raise ValidationError(
            "Cannot add entries to a transaction with Credit status. "
            "Entry-capable transactions handle credit at the entry level."
        )

    entry = TransactionEntry(
        transaction_id=transaction_id,
        user_id=user_id,
        amount=details.amount,
        description=details.description,
        entry_date=details.entry_date,
        is_credit=details.is_credit,
    )
    db.session.add(entry)
    db.session.flush()

    log_event(
        logger, logging.INFO, EVT_ENTRY_CREATED, BUSINESS,
        "Transaction entry created",
        user_id=user_id,
        owner_id=owner_id,
        transaction_id=transaction_id,
        entry_id=entry.id,
        amount=str(details.amount),
        is_credit=details.is_credit,
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

    log_event(
        logger, logging.INFO, EVT_ENTRY_UPDATED, BUSINESS,
        "Transaction entry updated",
        user_id=user_id,
        owner_id=owner_id,
        transaction_id=entry.transaction_id,
        entry_id=entry_id,
        # Sorting fields_changed keeps the structured log deterministic
        # so dashboards can group by it without ordering noise.
        fields_changed=sorted(valid_updates.keys()),
    )

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

    log_event(
        logger, logging.INFO, EVT_ENTRY_DELETED, BUSINESS,
        "Transaction entry deleted",
        user_id=user_id,
        owner_id=owner_id,
        transaction_id=transaction_id,
        entry_id=entry_id,
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
    debit_entries, credit_entries = partition_entries(entries)
    sum_debit = sum((e.amount for e in debit_entries), Decimal("0"))
    sum_credit = sum((e.amount for e in credit_entries), Decimal("0"))
    return sum_debit, sum_credit


def build_entry_sums_dict(
    transactions: list,
) -> dict[int, dict]:
    """Build a {txn_id: sums_dict} mapping for transactions with entries.

    Used by grid routes and HTMX cell-render endpoints to pre-compute
    entry aggregates for the cell template.  Only transactions with
    non-empty entries are included in the result.

    The dict carries ``remaining`` and ``over_budget`` so the grid
    cell template renders without inline Jinja arithmetic
    (E-16 / MED-04).  ``remaining`` is computed via
    :func:`compute_remaining` (the E-21 declared base
    ``estimated_amount`` minus the sum of all entries), so the cell's
    over-budget styling is driven by the same single rule that the
    dashboard bill row sees via ``bill.entry_remaining``.

    Pure function -- no database access beyond what was already loaded
    on the Transaction objects (expects entries to be accessible, either
    via eager load or lazy access).

    Args:
        transactions: List of Transaction objects with entries accessible.

    Returns:
        dict mapping transaction ID to {"debit": Decimal, "credit": Decimal,
        "total": Decimal, "count": int, "remaining": Decimal,
        "over_budget": bool, "pct": Decimal}.  Empty dict if no transactions
        have entries.  ``pct`` is the entries-to-estimate ratio clamped
        to [0, 100] via :func:`pct_complete`; it drives the mobile
        progress-bar's ``data-progress-pct`` attribute on the unified
        ``render_row_card`` macro per mobile-first v3 plan Commit 13.
    """
    result: dict[int, dict] = {}
    for txn in transactions:
        if txn.entries:
            debit, credit = compute_entry_sums(txn.entries)
            total = debit + credit
            estimated = Decimal(str(txn.estimated_amount))
            remaining = compute_remaining(estimated, txn.entries)
            result[txn.id] = {
                "debit": debit,
                "credit": credit,
                "total": total,
                "count": len(txn.entries),
                "remaining": remaining,
                "over_budget": remaining < Decimal("0"),
                "pct": pct_complete(total, estimated),
            }
    return result


def build_entry_lists_dict(
    transactions: list,
) -> dict[int, dict]:
    """Build a {txn_id: entry_list_data} mapping for envelope transactions.

    Pre-computes the entry-list rendering inputs that
    ``_render_entry_list`` in ``app/routes/entries.py`` produces per
    HTMX request, so the mobile grid macro can render entries inline
    on the initial grid response instead of lazy-loading them one
    request per envelope card.  With 6 visible pay periods and ~10
    envelope templates each, the lazy-load fan-out is ~60 parallel
    GETs on the entries endpoint, which exceeds the
    ``RATELIMIT_DEFAULT`` ceiling of ``30 per minute`` and leaves the
    over-limit cards stuck on the loading spinner.  Server-side
    rendering eliminates the fan-out entirely.

    Only purchase-tracking rows (``txn.tracks_purchases`` -- a template
    with ``is_envelope`` set, or an ad-hoc row carrying its own
    ``is_envelope`` flag) get an entry, matching the macro's guard for
    whether to render the inline entries section.  Non-tracking
    transactions are silently skipped.

    Pure function -- expects ``entries`` and ``template`` to be eager-
    loaded on the Transaction objects.  Mirrors
    ``build_entry_sums_dict``'s pure-function contract above.

    Args:
        transactions: List of Transaction objects with ``entries`` and
            ``template`` accessible.

    Returns:
        dict mapping envelope transaction ID to a dict with three
        keys consumed by ``grid/_transaction_entries.html``:

          - ``entries`` (list[TransactionEntry]): the entries ordered
            by entry_date, matching the order the entries relationship
            already enforces on the Transaction model.
          - ``remaining`` (Decimal): estimated_amount minus the sum of
            all entries (debit + credit), via :func:`compute_remaining`.
          - ``out_of_period_ids`` (set[int]): entry IDs whose
            ``entry_date`` falls outside the parent pay period's date
            range, surfacing the OP-4 date-awareness warning that
            ``_render_entry_list`` would emit.

        Empty dict when no transaction in the input has an envelope
        template.
    """
    result: dict[int, dict] = {}
    for txn in transactions:
        if not txn.tracks_purchases:
            continue
        entries = list(txn.entries)
        remaining = compute_remaining(txn.estimated_amount, entries)
        out_of_period_ids = {
            e.id for e in entries
            if not check_entry_date_in_period(e.entry_date, txn)
        }
        result[txn.id] = {
            "entries": entries,
            "remaining": remaining,
            "out_of_period_ids": out_of_period_ids,
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

    Per E-21 (audit MED-03 / F-028 / F-056) the budget base for an
    entry-tracked bill row is ``estimated_amount`` unconditionally --
    never ``actual_amount`` and never status-dependent.  This is why
    the signature takes ``estimated_amount`` directly rather than the
    whole ``Transaction``: the base cannot be switched on at runtime;
    callers that want to display "remaining" against a different base
    are out of contract and must compute it themselves.  The dashboard
    bill row, the companion entry data builder, and the entries
    partial all pass ``txn.estimated_amount`` (verified) so they
    share one declared base with the row's amount cell and
    over-budget flag.

    Pure function -- no database access.

    Args:
        estimated_amount: The transaction's budgeted amount -- the
            E-21 declared base for the row's plan-vs-actual figures.
        entries: List of TransactionEntry objects.

    Returns:
        Decimal -- the remaining budget (negative means overspent).
    """
    total_spent = sum((e.amount for e in entries), Decimal("0"))
    return estimated_amount - total_spent


def pct_complete(total: Decimal, target: Decimal) -> Decimal:
    """Compute percent complete, clamped to [0, 100].

    Feeds the entry-tracking progress-bar widths on the companion
    transaction card (and any other surface that needs an entry
    aggregate as a percentage of its declared budget base).  Returns a
    Decimal so money math never crosses the Decimal/float boundary at
    the route layer (MED-04 / E-16): the companion route used to
    ``float(total / estimated * Decimal("100"))`` inline, which violated
    the "money math is service-layer Decimal, not route-layer float"
    standard.  Thin domain-named wrapper over
    :func:`app.utils.money.percent_complete` -- the single numeric
    contract the dashboard and companion progress surfaces both share.

    The two-decimal-place result is safe to render as-is in CSS width
    values: ``data-progress-pct="55.50"`` is parsed by
    ``app/static/js/progress_bar.js`` via ``parseFloat`` before being
    applied as an inline width, and CSS itself accepts the decimal
    notation in ``%`` values.

    Args:
        total: Sum of entries against the budgeted line.
        target: Budgeted estimated amount.  If <= 0 the function
            returns ``Decimal("0")`` rather than dividing by zero or
            producing a misleading negative percentage.

    Returns:
        Decimal in [0, 100] quantised to two decimal places when the
        guard does not fire; ``Decimal("0")`` when ``target <= 0``.
    """
    return percent_complete(total, target)


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


def clear_entries_for_anchor_true_up(owner_id: int, account_id: int) -> int:
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
        (via pay_period.user_id) AND sits on the trued-up account
        (via transaction.account_id == account_id).  A true-up
        declares the real balance of ONE specific checking account,
        so it must not clear entries on the owner's other checking
        accounts (accounts carry no per-type uniqueness -- a user may
        hold more than one checking account).  Clearing them there
        would drop those accounts' reservation without ever raising
        their anchor, silently inflating their projected balance.
      - Parent transaction is not soft-deleted.
      - Parent transaction is in Projected status (settled parents
        are already excluded from the entry formula, so their entries
        do not affect balances either way).
      - Entry date is on or before today (future-dated entries cannot
        have posted to checking yet, so leave them uncleared).
      - Entry is_cleared is currently FALSE (no-op otherwise).

    Not scoped by scenario_id: transactions are scenario-scoped, but
    Phase 1 is baseline-only (every transaction lives in the single
    baseline scenario), so account_id fully isolates the clear today.
    When what-if scenarios land (Phase 3), the true-up routes must
    thread an operating-scenario context in here too.

    Credit entries are included in the flip for consistency, but the
    balance calculator ignores is_cleared on credit entries, so this
    has no effect on balances for credit rows.

    Does NOT commit -- the calling route owns the session boundary so
    the anchor history row and the cleared flips land in the same DB
    transaction.

    Args:
        owner_id: The user_id whose entries should be reconciled.
        account_id: The id of the checking account being trued up.
            Only entries on transactions belonging to this account are
            flipped.

    Returns:
        int -- number of entry rows updated.
    """
    today = date.today()

    # Synchronize_session='fetch' because later code in the same request
    # (e.g. balance calculator rendering the grid) may hold refs to the
    # affected entry rows and needs to see the updated flag.
    # The Projected filter is routed through the centralized
    # ``is_projected_clause`` (D6-09 / MED-02) so every SQL filter
    # over Projected shares one definition.
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
                    Transaction.account_id == account_id,
                    Transaction.is_deleted.is_(False),
                    is_projected_clause(Transaction),
                )
            ),
        )
        .update(
            {TransactionEntry.is_cleared: True},
            synchronize_session="fetch",
        )
    )

    if updated:
        log_event(
            logger, logging.INFO,
            EVT_ENTRIES_CLEARED_ON_ANCHOR_TRUEUP, BUSINESS,
            "Transaction entries cleared on anchor true-up",
            user_id=owner_id,
            account_id=account_id,
            cleared_count=updated,
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

    log_event(
        logger, logging.INFO, EVT_ENTRY_CLEARED_TOGGLED, BUSINESS,
        "Transaction entry is_cleared toggled",
        user_id=user_id,
        owner_id=owner_id,
        transaction_id=entry.transaction_id,
        entry_id=entry_id,
        is_cleared=entry.is_cleared,
    )

    return entry
