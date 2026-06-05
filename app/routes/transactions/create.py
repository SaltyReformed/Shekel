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
from app.utils.auth_helpers import require_owner
from app.routes.transactions._bp import transactions_bp
from app.routes.transactions._helpers import (
    _create_schema,
    _inline_create_schema,
    _render_cell,
    _resolve_owned_fks,
)

logger = logging.getLogger(__name__)


@transactions_bp.route("/transactions/inline", methods=["POST"])
@login_required
@require_owner
def create_inline():
    """Create a transaction from inline grid interaction.

    Auto-derives the name from the category.  Returns the new
    transaction cell wrapped in a div with a unique ID for HTMX
    targeting.

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
        return jsonify(errors=errors), 400

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
    category = objs[Category]

    # Default to projected status if not specified.
    if "status_id" not in data or data["status_id"] is None:
        data["status_id"] = ref_cache.status_id(StatusEnum.PROJECTED)

    # Set the name from the category display name.
    data["name"] = category.display_name

    txn = Transaction(**data)
    db.session.add(txn)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d created inline transaction: %s (id=%d)", current_user.id, txn.name, txn.id)

    # Return the cell wrapped in a div with a unique ID, matching
    # the pattern used in grid.html for existing transactions.
    response = _render_cell(txn, wrap_div=True)
    return response, 201, {"HX-Trigger": "balanceChanged"}


@transactions_bp.route("/transactions", methods=["POST"])
@login_required
@require_owner
def create_transaction():
    """Create an ad-hoc transaction (not from a template)."""
    errors = _create_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _create_schema.load(request.form)

    # Verify every user-scoped FK belongs to the current user before any
    # write (same IDOR probe as create_inline; this route carries no
    # category).  None of the resolved rows are needed afterward.
    _, err = _resolve_owned_fks([
        (Account, data["account_id"], "Not found"),
        (PayPeriod, data["pay_period_id"], "Pay period not found"),
        (Scenario, data["scenario_id"], "Not found"),
    ])
    if err is not None:
        return err

    # Default to projected status if not specified.
    if "status_id" not in data or data["status_id"] is None:
        data["status_id"] = ref_cache.status_id(StatusEnum.PROJECTED)

    txn = Transaction(**data)
    db.session.add(txn)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d created ad-hoc transaction: %s (id=%d)", current_user.id, txn.name, txn.id)

    response = _render_cell(txn)
    return response, 201, {"HX-Trigger": "balanceChanged"}
