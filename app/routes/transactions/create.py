"""
Shekel Budget App -- Transaction route package: create handlers.

The POST routes that create transactions: the inline grid-cell create
and the ad-hoc full create.  Both verify every user-scoped FK through
the shared :func:`_resolve_owned_fks` IDOR probe before inserting.
"""

import logging

from flask import request, jsonify
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.utils.auth_helpers import require_owner
from app.routes._render_helpers import render_transaction_cell
from app.routes.transactions._bp import transactions_bp
from app.routes.transactions._helpers import (
    _create_schema,
    _inline_create_schema,
    _resolve_owned_fks,
)

logger = logging.getLogger(__name__)

# The refusal body for a raw transaction typed onto an amortizing loan
# account (ruling D4 / finding N-11): a loan's balance is ledger-derived,
# not a transaction sum.  A payment is a transfer the app splits into
# interest / escrow / principal; a balance correction is a true-up on the
# loan's own page.
_LOAN_TRANSACTION_REFUSAL = (
    "A loan's balance is not a transaction sum. Record a payment as a "
    "transfer, or a balance correction as a true-up on the loan's page."
)


def _reject_transaction_on_loan(account: Account) -> tuple[str, int] | None:
    """Refuse a raw transaction typed onto an amortizing loan account.

    A loan's balance is ledger-derived, not a transaction sum (ruling D4).
    A raw transaction posted onto a loan account books a bare cash leg onto
    the loan's linked ledger that the sum-of-postings reader counts as a
    real paydown while the loan fold cannot see it -- finding N-11, the one
    balance shape where the two producers diverge with nothing to reconcile
    them.  So it is forbidden at the transaction-create chokepoint, exactly
    as a transfer OUT of a loan is forbidden at the transfer-create
    chokepoint
    (:func:`app.services._transfer_loan_posting._reject_transfer_out_of_loan`,
    review R6).  The grid picker already refuses a loan account (step A1);
    this closes the ad-hoc and inline create endpoints the picker does not
    gate.

    Args:
        account: The resolved, ownership-checked destination
            :class:`~app.models.account.Account` for the new transaction.

    Returns:
        A ready-to-return ``(message, 422)`` Flask response tuple when
        *account* is an amortizing loan, else ``None``.
    """
    if classify_account(account) is AccountProjectionKind.AMORTIZING:
        return _LOAN_TRANSACTION_REFUSAL, 422
    return None


@transactions_bp.route("/transactions/inline", methods=["POST"])
@login_required
@require_owner
def create_inline():
    """Create a transaction from inline grid interaction.

    The quick-create form's optional name field wins when provided
    (grid audit A5: ad-hoc rows are nameable at the Tier-1 entry
    point); otherwise the name is auto-derived from the category.
    Returns the new transaction cell wrapped in a div with a unique ID
    for HTMX targeting.

    Double-submit handling (F-102 / C-22): unlike the ad-hoc
    transfer create path (F-050), no database-level uniqueness
    constraint is enforced here.  Two transactions with identical
    (account_id, category_id, amount, pay_period_id) are a
    legitimate use case -- two $4 coffees on the same day, two
    identical fast-food charges, the user genuinely buying the
    same thing twice -- and rejecting them at the database layer
    would force the user to artificially differentiate amounts
    that match real-world receipts.  The mitigation is the
    client-side ``hx-disabled-elt`` HTMX directive on every
    transaction-create form (``_transaction_quick_create.html``,
    ``_transaction_full_create.html``,
    ``grid.html#addTransactionModal``): the submit control is
    disabled while the request is in flight, preventing accidental
    re-submits from a double-click or network retry.  The residual
    risk -- a user clicks rapidly enough to bypass the disable
    state, or replays the request via the back button -- is
    accepted as operator UX rather than a financial-correctness
    concern.
    """
    errors = _inline_create_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 422

    data = _inline_create_schema.load(request.form)

    # Verify every user-scoped FK belongs to the current user before any
    # write.  Order matches the historical per-FK checks so the first
    # invalid id returns the same 404 body as before; the resolved
    # Category drives the derived transaction name below.
    objs, err = _resolve_owned_fks([
        (Account, data["account_id"], "Not found"),
        (Category, data["category_id"], "Category not found"),
        (PayPeriod, data["pay_period_id"], "Pay period not found"),
        (Scenario, data["scenario_id"], "Not found"),
    ])
    if err is not None:
        return err
    loan_refusal = _reject_transaction_on_loan(objs[Account])
    if loan_refusal is not None:
        return loan_refusal
    category = objs[Category]

    # Born Projected: a transaction can only ever be created Projected; the sole
    # path to a settled status is the status seam (mark-done / PATCH / settle).
    # ``status_id`` is not a schema field, so a submitted value was already
    # dropped; assign Projected unconditionally.
    data["status_id"] = ref_cache.status_id(StatusEnum.PROJECTED)

    # A typed name wins; an omitted or blank one (the pre_load hook
    # drops empty submits) falls back to the category display name.
    data.setdefault("name", category.display_name)

    txn = Transaction(**data)
    db.session.add(txn)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info(
        "user_id=%d created inline transaction: %s (id=%d)",
        current_user.id, txn.name, txn.id,
    )

    # Return the cell wrapped in a div with a unique ID, matching
    # the pattern used in grid.html for existing transactions.
    response = render_transaction_cell(txn, wrap_div=True)
    return response, 201, {"HX-Trigger": "balanceChanged"}


@transactions_bp.route("/transactions", methods=["POST"])
@login_required
@require_owner
def create_transaction():
    """Create an ad-hoc transaction (not from a template)."""
    errors = _create_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 422

    data = _create_schema.load(request.form)

    # Verify every user-scoped FK belongs to the current user before any
    # write (same IDOR probe as create_inline).  ``category_id`` is a
    # required field on TransactionCreateSchema and is persisted via
    # ``Transaction(**data)``, so it must be ownership-checked here too:
    # a foreign category_id otherwise satisfies the FK constraint (the row
    # exists) and links another user's category onto this transaction.
    # The resolved Account is checked for the loan-kind refusal below.
    objs, err = _resolve_owned_fks([
        (Account, data["account_id"], "Not found"),
        (Category, data["category_id"], "Category not found"),
        (PayPeriod, data["pay_period_id"], "Pay period not found"),
        (Scenario, data["scenario_id"], "Not found"),
    ])
    if err is not None:
        return err
    loan_refusal = _reject_transaction_on_loan(objs[Account])
    if loan_refusal is not None:
        return loan_refusal

    # Born Projected: see create_inline.  ``status_id`` is not a schema field,
    # so any submitted value was dropped; assign Projected unconditionally so
    # the only route to a settled status remains the status seam.
    data["status_id"] = ref_cache.status_id(StatusEnum.PROJECTED)

    txn = Transaction(**data)
    db.session.add(txn)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info(
        "user_id=%d created ad-hoc transaction: %s (id=%d)",
        current_user.id, txn.name, txn.id,
    )

    response = render_transaction_cell(txn)
    return response, 201, {"HX-Trigger": "balanceChanged"}
