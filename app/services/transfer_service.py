"""
Shekel Budget App -- Transfer Service

The single point of enforcement for all transfer mutations.  Every code
path that creates, updates, or deletes a transfer MUST go through this
service.  Direct ORM manipulation of budget.transfers is forbidden
outside this module and the transfer recurrence engine (which delegates
to this service for the final insert step).

The service enforces the five core invariants (design doc section 4.5):

  1. Every transfer has exactly two linked shadow transactions
     (one expense, one income).
  2. Shadow transactions are never orphaned.
  3. Shadow amounts always equal the transfer amount.
  4. Shadow statuses always equal the transfer status.
  5. Shadow periods always equal the transfer period.

Architecture:
  - No Flask imports.  Receives plain data, returns ORM objects or
    raises exceptions.
  - All monetary arithmetic uses Decimal.
  - Flushes to the session but does NOT commit.  The caller owns the
    database transaction boundary.
"""

import logging
from decimal import Decimal, InvalidOperation

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app import ref_cache
from app.enums import TxnTypeEnum
from app.exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)

# Category constants for shadow transaction defaults.
TRANSFER_IN_GROUP = "Transfers"
TRANSFER_IN_ITEM = "Incoming"
TRANSFER_OUT_GROUP = "Transfers"
TRANSFER_OUT_ITEM = "Outgoing"


# ── Private helpers ────────────────────────────────────────────────


def _validate_positive_amount(amount):
    """Ensure *amount* is a positive Decimal.

    Args:
        amount: The transfer amount (Decimal, int, float, or string).

    Returns:
        The validated amount as a Decimal.

    Raises:
        ValidationError: If amount is zero, negative, or not numeric.
    """
    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(
            f"Invalid amount: {amount!r}.  Must be a positive number."
        ) from exc
    if amount <= 0:
        raise ValidationError(
            "Transfer amount must be positive."
        )
    return amount


def _get_owned_account(account_id, user_id, label="Account"):
    """Load an Account and verify ownership.

    Args:
        account_id: The primary key.
        user_id:    The expected owner.
        label:      Human-readable label for error messages.

    Returns:
        The Account object.

    Raises:
        NotFoundError: If the account does not exist or belongs to
            another user.  The message is identical in both cases
            (security response rule).
    """
    acct = db.session.get(Account, account_id)
    if acct is None or acct.user_id != user_id:
        raise NotFoundError(f"{label} {account_id} not found.")
    return acct


def _get_owned_period(pay_period_id, user_id):
    """Load a PayPeriod and verify ownership.

    Imported inside the function to avoid circular imports (same
    pattern used by carry_forward_service and credit_workflow).

    Raises:
        NotFoundError: If the period does not exist or belongs to
            another user.
    """
    from app.models.pay_period import PayPeriod  # pylint: disable=import-outside-toplevel
    period = db.session.get(PayPeriod, pay_period_id)
    if period is None or period.user_id != user_id:
        raise NotFoundError(f"Pay period {pay_period_id} not found.")
    return period


def _get_owned_scenario(scenario_id, user_id):
    """Load a Scenario and verify ownership.

    Raises:
        NotFoundError: If the scenario does not exist or belongs to
            another user.
    """
    scenario = db.session.get(Scenario, scenario_id)
    if scenario is None or scenario.user_id != user_id:
        raise NotFoundError(f"Scenario {scenario_id} not found.")
    return scenario


def _get_owned_category(category_id, user_id):
    """Load a Category and verify ownership.

    Returns None if *category_id* is None (caller explicitly passed
    no category).

    Raises:
        NotFoundError: If the category does not exist or belongs to
            another user.
    """
    if category_id is None:
        return None
    cat = db.session.get(Category, category_id)
    if cat is None or cat.user_id != user_id:
        raise NotFoundError(f"Category {category_id} not found.")
    return cat


def _get_owned_transfer_template(template_id, user_id):
    """Load a TransferTemplate and verify ownership.

    Returns None if *template_id* is None.

    Raises:
        NotFoundError: If the template does not exist or belongs to
            another user.
    """
    if template_id is None:
        return None
    tpl = db.session.get(TransferTemplate, template_id)
    if tpl is None or tpl.user_id != user_id:
        raise NotFoundError(f"Transfer template {template_id} not found.")
    return tpl


def _lookup_transfer_categories(user_id):
    """Look up the default Transfers: Incoming and Transfers: Outgoing
    categories for a user.

    Returns:
        Tuple (incoming_cat_id, outgoing_cat_id).  Either may be None
        if the user deleted the default category.
    """
    incoming = (
        db.session.query(Category)
        .filter_by(
            user_id=user_id,
            group_name=TRANSFER_IN_GROUP,
            item_name=TRANSFER_IN_ITEM,
        )
        .first()
    )
    outgoing = (
        db.session.query(Category)
        .filter_by(
            user_id=user_id,
            group_name=TRANSFER_OUT_GROUP,
            item_name=TRANSFER_OUT_ITEM,
        )
        .first()
    )
    if incoming is None:
        logger.warning(
            "User %d is missing the '%s: %s' default category.  "
            "Income-side shadows will have category_id=NULL.",
            user_id, TRANSFER_IN_GROUP, TRANSFER_IN_ITEM,
        )
    if outgoing is None:
        logger.warning(
            "User %d is missing the '%s: %s' default category.  "
            "Uncategorized expense-side shadows will have category_id=NULL.",
            user_id, TRANSFER_OUT_GROUP, TRANSFER_OUT_ITEM,
        )
    return (
        incoming.id if incoming else None,
        outgoing.id if outgoing else None,
    )


def _get_transfer_or_raise(transfer_id, user_id, allow_deleted=False):
    """Load a Transfer and verify ownership and active status.

    Args:
        transfer_id:   The primary key.
        user_id:       The expected owner.
        allow_deleted: If False (default), soft-deleted transfers are
                       treated as non-existent and raise NotFoundError.
                       Set to True for operations that legitimately need
                       to act on deleted transfers (e.g. delete_transfer
                       for idempotent soft-delete, restore_transfer).

    Returns:
        The Transfer object.

    Raises:
        NotFoundError: If the transfer does not exist, belongs to
            another user, or is soft-deleted (when allow_deleted is
            False).  The message is identical in all cases (security
            response rule -- do not reveal existence to wrong user).
    """
    xfer = db.session.get(Transfer, transfer_id)
    if xfer is None or xfer.user_id != user_id:
        raise NotFoundError(f"Transfer {transfer_id} not found.")
    # Soft-deleted transfers are invisible to normal operations.
    # Without this check, update_transfer on a deleted transfer would
    # cascade into a misleading "0 shadow transactions" error from
    # _get_shadow_transactions (the shadows are also deleted).
    if not allow_deleted and xfer.is_deleted:
        raise NotFoundError(f"Transfer {transfer_id} not found.")
    return xfer


def _get_shadow_transactions(transfer_id):
    """Load shadow transactions for a transfer and identify types.

    Returns:
        Tuple (expense_shadow, income_shadow).

    Raises:
        ValidationError: If the shadow count is not exactly 2 or if
            both shadows have the same transaction type (data
            integrity violation).
    """
    shadows = (
        db.session.query(Transaction)
        .filter_by(transfer_id=transfer_id, is_deleted=False)
        .all()
    )

    if len(shadows) != 2:
        # Differentiate between a soft-deleted transfer (expected state,
        # not corruption) and a genuinely corrupt transfer missing
        # shadows (unexpected state).  _get_transfer_or_raise blocks
        # soft-deleted transfers by default, so this path should only
        # fire for real corruption -- but defense-in-depth means we
        # check anyway to produce a helpful diagnostic.
        xfer = db.session.get(Transfer, transfer_id)
        is_soft_deleted = xfer is not None and xfer.is_deleted

        shadow_ids = [s.id for s in shadows]
        if is_soft_deleted and len(shadows) == 0:
            logger.warning(
                "Transfer %d is soft-deleted.  Its shadow transactions "
                "are also soft-deleted and excluded from active queries.  "
                "This is expected, not data corruption.",
                transfer_id,
            )
            raise ValidationError(
                f"Transfer {transfer_id} is soft-deleted and cannot be "
                f"modified.  Use restore_transfer to reactivate it first."
            )

        # Genuine data integrity violation: transfer is active but has
        # the wrong number of shadows.  Fail-fast.
        logger.error(
            "Transfer %d has %d active shadow transactions (expected 2).  "
            "Shadow IDs: %s.  This indicates data corruption.",
            transfer_id, len(shadows), shadow_ids,
        )
        raise ValidationError(
            f"Transfer {transfer_id} has {len(shadows)} shadow "
            f"transactions instead of the expected 2.  "
            f"Data integrity issue -- cannot proceed."
        )

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

    expense_shadow = None
    income_shadow = None
    for s in shadows:
        if s.transaction_type_id == expense_type_id:
            expense_shadow = s
        elif s.transaction_type_id == income_type_id:
            income_shadow = s

    if expense_shadow is None or income_shadow is None:
        raise ValidationError(
            f"Transfer {transfer_id} shadows do not have the expected "
            f"expense/income type pairing.  Data integrity issue."
        )

    return expense_shadow, income_shadow


# ── Public API ─────────────────────────────────────────────────────


def create_transfer(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    user_id,
    from_account_id,
    to_account_id,
    pay_period_id,
    scenario_id,
    amount,
    status_id,
    category_id=None,
    notes=None,
    transfer_template_id=None,
    name=None,
):
    """Create a transfer and its two shadow transactions atomically.

    This is the ONLY code path that should create rows in
    budget.transfers.  It enforces invariants 1-5 from design doc
    section 4.5.

    Args:
        user_id:              Owner of the transfer.
        from_account_id:      Account money leaves (expense side).
        to_account_id:        Account money enters (income side).
        pay_period_id:        Pay period for the transfer.
        scenario_id:          Budget scenario.
        amount:               Transfer amount (positive Decimal).
        status_id:            Initial status (typically 'projected').
        category_id:          Optional spending category for the
                              expense-side shadow.  If None, falls
                              back to "Transfers: Outgoing".
        notes:                Optional notes on the transfer.
        transfer_template_id: Optional link to the generating
                              transfer template (for recurrence).
        name:                 Optional name.  If omitted, generated
                              from account names.

    Returns:
        The created Transfer object (shadows accessible via
        transfer.shadow_transactions backref).

    Raises:
        ValidationError: If amount is non-positive, accounts are the
            same, or any business rule is violated.
        NotFoundError: If any referenced entity does not exist or
            does not belong to user_id.
    """
    # ── Validate inputs ────────────────────────────────────────────
    amount = _validate_positive_amount(amount)

    if from_account_id == to_account_id:
        raise ValidationError(
            "Source and destination accounts must be different."
        )

    from_account = _get_owned_account(
        from_account_id, user_id, label="Source account"
    )
    to_account = _get_owned_account(
        to_account_id, user_id, label="Destination account"
    )
    _get_owned_period(pay_period_id, user_id)
    _get_owned_scenario(scenario_id, user_id)
    _get_owned_category(category_id, user_id)
    _get_owned_transfer_template(transfer_template_id, user_id)

    # ── Ref data lookups ───────────────────────────────────────────
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    incoming_cat_id, outgoing_cat_id = _lookup_transfer_categories(user_id)

    # ── Determine names ────────────────────────────────────────────
    transfer_name = name or f"{from_account.name} to {to_account.name}"
    expense_shadow_name = f"Transfer to {to_account.name}"
    income_shadow_name = f"Transfer from {from_account.name}"

    # ── Determine shadow categories ────────────────────────────────
    # Expense shadow: use the transfer's category if set, otherwise
    # fall back to "Transfers: Outgoing" default.
    expense_cat_id = category_id if category_id is not None else outgoing_cat_id
    # Income shadow: always uses "Transfers: Incoming" default.
    income_cat_id = incoming_cat_id

    # ── Create transfer record ─────────────────────────────────────
    xfer = Transfer(
        user_id=user_id,
        from_account_id=from_account_id,
        to_account_id=to_account_id,
        pay_period_id=pay_period_id,
        scenario_id=scenario_id,
        status_id=status_id,
        transfer_template_id=transfer_template_id,
        name=transfer_name,
        amount=amount,
        category_id=category_id,
        notes=notes,
        is_override=False,
        is_deleted=False,
    )
    db.session.add(xfer)
    # Flush to get transfer.id -- required before creating shadows
    # that reference it via transfer_id FK.
    db.session.flush()

    # ── Create expense shadow (from_account) ───────────────────────
    expense_shadow = Transaction(
        account_id=from_account_id,
        template_id=None,       # Shadows are transfer-generated, not template-generated.
        transfer_id=xfer.id,
        pay_period_id=pay_period_id,
        scenario_id=scenario_id,
        status_id=status_id,
        name=expense_shadow_name,
        category_id=expense_cat_id,
        transaction_type_id=expense_type_id,
        estimated_amount=amount,
        actual_amount=None,
        is_override=False,
        is_deleted=False,
        credit_payback_for_id=None,
        notes=None,
    )
    db.session.add(expense_shadow)

    # ── Create income shadow (to_account) ──────────────────────────
    income_shadow = Transaction(
        account_id=to_account_id,
        template_id=None,       # Shadows are transfer-generated, not template-generated.
        transfer_id=xfer.id,
        pay_period_id=pay_period_id,
        scenario_id=scenario_id,
        status_id=status_id,
        name=income_shadow_name,
        category_id=income_cat_id,
        transaction_type_id=income_type_id,
        estimated_amount=amount,
        actual_amount=None,
        is_override=False,
        is_deleted=False,
        credit_payback_for_id=None,
        notes=None,
    )
    db.session.add(income_shadow)
    db.session.flush()

    logger.info(
        "Created transfer %d (%s, $%s) with shadows %d (expense) "
        "and %d (income).",
        xfer.id, transfer_name, amount,
        expense_shadow.id, income_shadow.id,
    )
    return xfer


def update_transfer(transfer_id, user_id, **kwargs):  # pylint: disable=too-many-branches,too-many-statements
    """Update a transfer and propagate changes to shadow transactions.

    Enforces invariants 3-5: shadow amounts, statuses, and periods
    always match the parent transfer.

    Accepted kwargs:
        amount         -- New transfer amount (positive Decimal).
        status_id      -- New status for transfer and both shadows.
        pay_period_id  -- New period for transfer and both shadows.
        category_id    -- New category (expense shadow only).
        name           -- New display name (transfer only, not shadows).
        notes          -- New notes (transfer only, not shadows).
        actual_amount  -- Actual settled amount (both shadows only;
                          the Transfer model has no actual_amount
                          column).
        is_override    -- Override flag (transfer and both shadows).

    Any other kwargs are silently ignored (consistent with the
    BaseSchema EXCLUDE pattern).

    Args:
        transfer_id: The primary key of the transfer to update.
        user_id:     The expected owner (defense-in-depth).

    Returns:
        The updated Transfer object.

    Raises:
        NotFoundError: If the transfer does not exist or does not
            belong to user_id.
        ValidationError: If validation fails (non-positive amount,
            wrong period owner, data integrity issues).
    """
    xfer = _get_transfer_or_raise(transfer_id, user_id)
    expense_shadow, income_shadow = _get_shadow_transactions(transfer_id)

    # ── amount ─────────────────────────────────────────────────────
    if "amount" in kwargs:
        new_amount = _validate_positive_amount(kwargs["amount"])
        xfer.amount = new_amount
        expense_shadow.estimated_amount = new_amount
        income_shadow.estimated_amount = new_amount

    # ── status_id ──────────────────────────────────────────────────
    if "status_id" in kwargs:
        new_status_id = kwargs["status_id"]
        xfer.status_id = new_status_id
        expense_shadow.status_id = new_status_id
        income_shadow.status_id = new_status_id

    # ── pay_period_id ──────────────────────────────────────────────
    if "pay_period_id" in kwargs:
        new_period_id = kwargs["pay_period_id"]
        _get_owned_period(new_period_id, user_id)
        xfer.pay_period_id = new_period_id
        expense_shadow.pay_period_id = new_period_id
        income_shadow.pay_period_id = new_period_id

    # ── category_id ────────────────────────────────────────────────
    # Category updates apply to the expense shadow only.  The income
    # shadow retains its "Transfers: Incoming" category.
    if "category_id" in kwargs:
        new_cat_id = kwargs["category_id"]
        if new_cat_id is not None:
            _get_owned_category(new_cat_id, user_id)
            xfer.category_id = new_cat_id
            expense_shadow.category_id = new_cat_id
        else:
            # Explicitly setting category to None -- use Outgoing fallback.
            _, outgoing_cat_id = _lookup_transfer_categories(user_id)
            xfer.category_id = None
            expense_shadow.category_id = outgoing_cat_id

    # ── name ───────────────────────────────────────────────────────
    # Name is display metadata on the transfer only.  Shadow names
    # are derived from account names and do not change here.
    if "name" in kwargs:
        xfer.name = kwargs["name"]

    # ── notes ──────────────────────────────────────────────────────
    # Notes live on the transfer only; shadow transactions do not
    # carry independent notes.
    if "notes" in kwargs:
        xfer.notes = kwargs["notes"]

    # ── actual_amount ──────────────────────────────────────────────
    # The Transfer model has no actual_amount column.  This kwarg
    # updates both shadow transactions directly.
    if "actual_amount" in kwargs:
        raw = kwargs["actual_amount"]
        if raw is not None:
            try:
                actual = Decimal(str(raw))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValidationError(
                    f"Invalid actual_amount: {raw!r}."
                ) from exc
        else:
            actual = None
        expense_shadow.actual_amount = actual
        income_shadow.actual_amount = actual

    # ── is_override ────────────────────────────────────────────────
    if "is_override" in kwargs:
        flag = bool(kwargs["is_override"])
        xfer.is_override = flag
        expense_shadow.is_override = flag
        income_shadow.is_override = flag

    db.session.flush()
    logger.info("Updated transfer %d.", transfer_id)
    return xfer


def delete_transfer(transfer_id, user_id, soft=False):
    """Delete a transfer and its shadow transactions.

    Args:
        transfer_id: The primary key of the transfer to delete.
        user_id:     The expected owner (defense-in-depth).
        soft:        If True, set is_deleted=True on the transfer and
                     both shadows (preserves records).  If False,
                     physically remove the transfer; the ON DELETE
                     CASCADE FK on transactions.transfer_id removes
                     both shadows automatically.

    Returns:
        The soft-deleted Transfer if soft=True, or None if hard-deleted.

    Raises:
        NotFoundError: If the transfer does not exist or does not
            belong to user_id.
    """
    # allow_deleted=True so that idempotent soft-delete and hard-delete
    # of already-soft-deleted transfers continue to work.
    xfer = _get_transfer_or_raise(transfer_id, user_id, allow_deleted=True)

    if soft:
        xfer.is_deleted = True
        # Soft-delete must explicitly mark both shadows.  The database
        # CASCADE only fires on physical deletes, not flag changes.
        shadows = (
            db.session.query(Transaction)
            .filter_by(transfer_id=transfer_id)
            .all()
        )
        for shadow in shadows:
            shadow.is_deleted = True
        db.session.flush()
        logger.info("Soft-deleted transfer %d and %d shadows.",
                     transfer_id, len(shadows))
        return xfer

    # Hard delete -- rely on ON DELETE CASCADE to remove shadows.
    db.session.delete(xfer)
    db.session.flush()

    # Verify CASCADE removed the shadows.  If they still exist,
    # the FK was misconfigured in Task 2.
    orphan_count = (
        db.session.query(Transaction)
        .filter_by(transfer_id=transfer_id)
        .count()
    )
    if orphan_count > 0:
        logger.error(
            "CASCADE delete failed: %d orphaned shadow transactions "
            "remain for deleted transfer %d.",
            orphan_count, transfer_id,
        )

    logger.info("Hard-deleted transfer %d.", transfer_id)
    return None


def restore_transfer(transfer_id, user_id):  # pylint: disable=too-many-branches
    """Restore a soft-deleted transfer and its shadow transactions.

    This is the inverse of ``delete_transfer(soft=True)``.  Sets
    ``is_deleted=False`` on the transfer and both shadows, then
    verifies and corrects shadow invariants (amount, status, period)
    that may have drifted while the transfer was soft-deleted.

    Idempotent: calling on an already-active transfer is a no-op.

    Args:
        transfer_id: The primary key of the transfer to restore.
        user_id:     The expected owner (defense-in-depth).

    Returns:
        The restored (or already-active) Transfer object.

    Raises:
        NotFoundError: If the transfer does not exist or does not
            belong to user_id.
        ValidationError: If shadow transactions are missing or have
            an invalid type pairing, indicating data corruption that
            cannot be automatically repaired.
    """
    # Must allow deleted transfers since that is the expected input.
    xfer = _get_transfer_or_raise(transfer_id, user_id, allow_deleted=True)

    # Idempotent: if the transfer is already active, return unchanged.
    # Matches the idempotent pattern of delete_transfer(soft=True).
    if not xfer.is_deleted:
        logger.debug(
            "restore_transfer called on active transfer %d; no-op.",
            transfer_id,
        )
        return xfer

    xfer.is_deleted = False

    # Load ALL shadows without filtering by is_deleted -- they are
    # soft-deleted and that is exactly what we are undoing.  Same
    # query pattern as delete_transfer(soft=True).
    shadows = (
        db.session.query(Transaction)
        .filter_by(transfer_id=transfer_id)
        .all()
    )

    # ── Validate shadow count ───────────────────────────────────────
    if len(shadows) != 2:
        logger.error(
            "Cannot restore transfer %d: expected 2 shadow transactions, "
            "found %d.  Shadow IDs: %s.  Data integrity issue.",
            transfer_id, len(shadows), [s.id for s in shadows],
        )
        # Roll back the is_deleted change on the transfer since we
        # cannot restore it in a consistent state.
        xfer.is_deleted = True
        raise ValidationError(
            f"Transfer {transfer_id} has {len(shadows)} shadow "
            f"transactions (expected 2).  Cannot restore -- data "
            f"integrity issue requiring manual intervention."
        )

    # ── Validate shadow type pairing ────────────────────────────────
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    type_ids = {s.transaction_type_id for s in shadows}
    if type_ids != {expense_type_id, income_type_id}:
        logger.error(
            "Cannot restore transfer %d: shadow type pairing is invalid.  "
            "Expected one expense and one income, found type_ids=%s.",
            transfer_id, type_ids,
        )
        xfer.is_deleted = True
        raise ValidationError(
            f"Transfer {transfer_id} shadows do not have the expected "
            f"expense/income type pairing.  Cannot restore -- data "
            f"integrity issue requiring manual intervention."
        )

    # ── Restore shadows and verify invariants ───────────────────────
    for shadow in shadows:
        shadow.is_deleted = False

        # Invariant 3: shadow amount must match transfer amount.
        if shadow.estimated_amount != xfer.amount:
            logger.warning(
                "Correcting shadow %d estimated_amount drift: %s -> %s "
                "(transfer %d amount).",
                shadow.id, shadow.estimated_amount, xfer.amount,
                transfer_id,
            )
            shadow.estimated_amount = xfer.amount

        # Invariant 4: shadow status must match transfer status.
        if shadow.status_id != xfer.status_id:
            logger.warning(
                "Correcting shadow %d status_id drift: %s -> %s "
                "(transfer %d status).",
                shadow.id, shadow.status_id, xfer.status_id,
                transfer_id,
            )
            shadow.status_id = xfer.status_id

        # Invariant 5: shadow period must match transfer period.
        if shadow.pay_period_id != xfer.pay_period_id:
            logger.warning(
                "Correcting shadow %d pay_period_id drift: %s -> %s "
                "(transfer %d period).",
                shadow.id, shadow.pay_period_id, xfer.pay_period_id,
                transfer_id,
            )
            shadow.pay_period_id = xfer.pay_period_id

    db.session.flush()
    logger.info(
        "Restored transfer %d with %d shadows.",
        transfer_id, len(shadows),
    )
    return xfer
