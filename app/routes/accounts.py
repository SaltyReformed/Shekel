"""
Shekel Budget App — Account Routes

CRUD for accounts and account types, plus anchor balance true-up.
Returns HTMX fragments for inline editing at the top of the grid.
"""

import logging
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for, jsonify
from flask_login import current_user, login_required

from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.schemas.validation import (
    AccountCreateSchema,
    AccountUpdateSchema,
    AccountTypeCreateSchema,
    AccountTypeUpdateSchema,
    AnchorUpdateSchema,
)
from app.services import pay_period_service

logger = logging.getLogger(__name__)

accounts_bp = Blueprint("accounts", __name__)

_anchor_schema = AnchorUpdateSchema()
_create_schema = AccountCreateSchema()
_update_schema = AccountUpdateSchema()
_type_create_schema = AccountTypeCreateSchema()
_type_update_schema = AccountTypeUpdateSchema()


# ── Account CRUD ───────────────────────────────────────────────────


@accounts_bp.route("/accounts")
@login_required
def list_accounts():
    """List all accounts and account types (two-section page)."""
    accounts = (
        db.session.query(Account)
        .filter_by(user_id=current_user.id)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    account_types = (
        db.session.query(AccountType)
        .order_by(AccountType.name)
        .all()
    )

    # Build a set of account type IDs that are in use (for delete guard).
    types_in_use = set(
        row[0] for row in
        db.session.query(Account.account_type_id)
        .filter_by(user_id=current_user.id)
        .distinct()
        .all()
    )

    return render_template(
        "accounts/list.html",
        accounts=accounts,
        account_types=account_types,
        types_in_use=types_in_use,
    )


@accounts_bp.route("/accounts/new", methods=["GET"])
@login_required
def new_account():
    """Display the account creation form."""
    account_types = (
        db.session.query(AccountType)
        .order_by(AccountType.name)
        .all()
    )
    return render_template(
        "accounts/form.html",
        account=None,
        account_types=account_types,
    )


@accounts_bp.route("/accounts", methods=["POST"])
@login_required
def create_account():
    """Create a new account."""
    errors = _create_schema.validate(request.form)
    if errors:
        flash(f"Validation error: {errors}", "danger")
        return redirect(url_for("accounts.new_account"))

    data = _create_schema.load(request.form)

    # Check for duplicate name.
    existing = (
        db.session.query(Account)
        .filter_by(user_id=current_user.id, name=data["name"])
        .first()
    )
    if existing:
        flash("An account with that name already exists.", "warning")
        return redirect(url_for("accounts.new_account"))

    anchor_balance = Decimal(str(data.pop("anchor_balance", "0") or "0"))

    current_period = pay_period_service.get_current_period(current_user.id)

    account = Account(
        user_id=current_user.id,
        current_anchor_balance=anchor_balance,
        current_anchor_period_id=current_period.id if current_period else None,
        **data,
    )
    db.session.add(account)
    db.session.commit()

    logger.info("Created account: %s (id=%d)", account.name, account.id)
    flash(f"Account '{account.name}' created.", "success")
    return redirect(url_for("accounts.list_accounts"))


@accounts_bp.route("/accounts/<int:account_id>/edit", methods=["GET"])
@login_required
def edit_account(account_id):
    """Display the account edit form."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        flash("Account not found.", "danger")
        return redirect(url_for("accounts.list_accounts"))

    account_types = (
        db.session.query(AccountType)
        .order_by(AccountType.name)
        .all()
    )
    return render_template(
        "accounts/form.html",
        account=account,
        account_types=account_types,
    )


@accounts_bp.route("/accounts/<int:account_id>", methods=["POST"])
@login_required
def update_account(account_id):
    """Update an account."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        flash("Account not found.", "danger")
        return redirect(url_for("accounts.list_accounts"))

    errors = _update_schema.validate(request.form)
    if errors:
        flash(f"Validation error: {errors}", "danger")
        return redirect(url_for("accounts.edit_account", account_id=account_id))

    data = _update_schema.load(request.form)

    # Check for duplicate name (if name is changing).
    if "name" in data and data["name"] != account.name:
        existing = (
            db.session.query(Account)
            .filter_by(user_id=current_user.id, name=data["name"])
            .first()
        )
        if existing:
            flash("An account with that name already exists.", "warning")
            return redirect(url_for("accounts.edit_account", account_id=account_id))

    # Handle anchor balance update with audit trail.
    new_anchor = data.pop("anchor_balance", None)
    if new_anchor is not None:
        new_anchor = Decimal(str(new_anchor))
        if new_anchor != account.current_anchor_balance:
            current_period = pay_period_service.get_current_period(current_user.id)
            account.current_anchor_balance = new_anchor
            if current_period:
                account.current_anchor_period_id = current_period.id
                history = AccountAnchorHistory(
                    account_id=account.id,
                    pay_period_id=current_period.id,
                    anchor_balance=new_anchor,
                )
                db.session.add(history)

    _ACCOUNT_UPDATE_FIELDS = {"name", "account_type_id", "sort_order", "is_active"}
    for field, value in data.items():
        if field in _ACCOUNT_UPDATE_FIELDS:
            setattr(account, field, value)

    db.session.commit()
    logger.info("Updated account: %s (id=%d)", account.name, account.id)
    flash(f"Account '{account.name}' updated.", "success")
    return redirect(url_for("accounts.list_accounts"))


@accounts_bp.route("/accounts/<int:account_id>/delete", methods=["POST"])
@login_required
def deactivate_account(account_id):
    """Deactivate an account (soft delete)."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        flash("Account not found.", "danger")
        return redirect(url_for("accounts.list_accounts"))

    # Guard: prevent deactivation if active transfer templates reference this account.
    from app.models.transfer_template import TransferTemplate

    active_transfers = (
        db.session.query(TransferTemplate)
        .filter(
            TransferTemplate.user_id == current_user.id,
            TransferTemplate.is_active.is_(True),
            db.or_(
                TransferTemplate.from_account_id == account_id,
                TransferTemplate.to_account_id == account_id,
            ),
        )
        .first()
    )
    if active_transfers:
        flash(
            "Cannot deactivate this account — it is used by active transfer templates. "
            "Deactivate those templates first.",
            "warning",
        )
        return redirect(url_for("accounts.list_accounts"))

    account.is_active = False
    db.session.commit()
    logger.info("Deactivated account: %s (id=%d)", account.name, account.id)
    flash(f"Account '{account.name}' deactivated.", "info")
    return redirect(url_for("accounts.list_accounts"))


@accounts_bp.route("/accounts/<int:account_id>/reactivate", methods=["POST"])
@login_required
def reactivate_account(account_id):
    """Reactivate a deactivated account."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        flash("Account not found.", "danger")
        return redirect(url_for("accounts.list_accounts"))

    account.is_active = True
    db.session.commit()
    logger.info("Reactivated account: %s (id=%d)", account.name, account.id)
    flash(f"Account '{account.name}' reactivated.", "success")
    return redirect(url_for("accounts.list_accounts"))


# ── Inline Anchor Balance Edit (Accounts List) ────────────────────


@accounts_bp.route("/accounts/<int:account_id>/inline-anchor", methods=["PATCH"])
@login_required
def inline_anchor_update(account_id):
    """HTMX endpoint: update anchor balance inline from the accounts list."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return "Not found", 404

    errors = _anchor_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _anchor_schema.load(request.form)
    new_balance = Decimal(str(data["anchor_balance"]))

    current_period = pay_period_service.get_current_period(current_user.id)

    account.current_anchor_balance = new_balance
    if current_period:
        account.current_anchor_period_id = current_period.id
        history = AccountAnchorHistory(
            account_id=account.id,
            pay_period_id=current_period.id,
            anchor_balance=new_balance,
        )
        db.session.add(history)

    db.session.commit()
    db.session.refresh(account)

    logger.info(
        "Inline anchor update: account %d set to $%s",
        account.id, new_balance,
    )

    return render_template(
        "accounts/_anchor_cell.html", acct=account, editing=False,
    )


@accounts_bp.route("/accounts/<int:account_id>/inline-anchor-form", methods=["GET"])
@login_required
def inline_anchor_form(account_id):
    """HTMX partial: show inline anchor balance edit form on accounts list."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return "Not found", 404

    return render_template(
        "accounts/_anchor_cell.html", acct=account, editing=True,
    )


@accounts_bp.route("/accounts/<int:account_id>/inline-anchor-display", methods=["GET"])
@login_required
def inline_anchor_display(account_id):
    """HTMX partial: show anchor balance display on accounts list."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return "Not found", 404

    return render_template(
        "accounts/_anchor_cell.html", acct=account, editing=False,
    )


# ── Account Type CRUD ──────────────────────────────────────────────


@accounts_bp.route("/accounts/types", methods=["POST"])
@login_required
def create_account_type():
    """Create a new account type."""
    errors = _type_create_schema.validate(request.form)
    if errors:
        flash(f"Validation error: {errors}", "danger")
        return redirect(url_for("accounts.list_accounts"))

    data = _type_create_schema.load(request.form)

    # Check for duplicate name.
    existing = (
        db.session.query(AccountType)
        .filter_by(name=data["name"])
        .first()
    )
    if existing:
        flash("An account type with that name already exists.", "warning")
        return redirect(url_for("accounts.list_accounts"))

    account_type = AccountType(**data)
    db.session.add(account_type)
    db.session.commit()

    logger.info("Created account type: %s (id=%d)", account_type.name, account_type.id)
    flash(f"Account type '{account_type.name}' created.", "success")
    return redirect(url_for("accounts.list_accounts"))


@accounts_bp.route("/accounts/types/<int:type_id>", methods=["POST"])
@login_required
def update_account_type(type_id):
    """Update an account type name."""
    account_type = db.session.get(AccountType, type_id)
    if account_type is None:
        flash("Account type not found.", "danger")
        return redirect(url_for("accounts.list_accounts"))

    errors = _type_update_schema.validate(request.form)
    if errors:
        flash(f"Validation error: {errors}", "danger")
        return redirect(url_for("accounts.list_accounts"))

    data = _type_update_schema.load(request.form)

    # Check for duplicate name.
    existing = (
        db.session.query(AccountType)
        .filter(AccountType.name == data["name"], AccountType.id != type_id)
        .first()
    )
    if existing:
        flash("An account type with that name already exists.", "warning")
        return redirect(url_for("accounts.list_accounts"))

    account_type.name = data["name"]
    db.session.commit()

    logger.info("Updated account type: %s (id=%d)", account_type.name, account_type.id)
    flash(f"Account type renamed to '{account_type.name}'.", "success")
    return redirect(url_for("accounts.list_accounts"))


@accounts_bp.route("/accounts/types/<int:type_id>/delete", methods=["POST"])
@login_required
def delete_account_type(type_id):
    """Delete an account type (only if no accounts reference it)."""
    account_type = db.session.get(AccountType, type_id)
    if account_type is None:
        flash("Account type not found.", "danger")
        return redirect(url_for("accounts.list_accounts"))

    in_use = (
        db.session.query(Account)
        .filter_by(account_type_id=type_id)
        .first()
    )
    if in_use:
        flash(
            "Cannot delete this account type — it is in use by one or more accounts.",
            "warning",
        )
        return redirect(url_for("accounts.list_accounts"))

    db.session.delete(account_type)
    db.session.commit()

    logger.info("Deleted account type: %s (id=%d)", account_type.name, type_id)
    flash(f"Account type '{account_type.name}' deleted.", "info")
    return redirect(url_for("accounts.list_accounts"))


# ── Anchor Balance True-up ─────────────────────────────────────────


@accounts_bp.route("/accounts/<int:account_id>/true-up", methods=["PATCH"])
@login_required
def true_up(account_id):
    """Update the anchor balance for an account (inline edit from grid).

    Records the true-up in anchor_history for audit trail, then
    triggers a balance recalculation via HX-Trigger.
    """
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return "Account not found", 404

    errors = _anchor_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _anchor_schema.load(request.form)
    new_balance = Decimal(str(data["anchor_balance"]))

    # Find the current pay period and set it as the anchor period.
    current_period = pay_period_service.get_current_period(current_user.id)
    if current_period is None:
        return "No current pay period found", 400

    # Update the account.
    account.current_anchor_balance = new_balance
    account.current_anchor_period_id = current_period.id

    # Record in history.
    history = AccountAnchorHistory(
        account_id=account.id,
        pay_period_id=current_period.id,
        anchor_balance=new_balance,
    )
    db.session.add(history)
    db.session.commit()
    db.session.refresh(account)

    logger.info(
        "True-up: account %d set to $%s at period %d",
        account.id, new_balance, current_period.id,
    )

    # Return the updated balance display + OOB swap for the "as of" date.
    html = render_template(
        "grid/_anchor_edit.html",
        account=account,
        editing=False,
    )
    as_of_html = (
        f'<small class="text-muted" id="anchor-as-of" hx-swap-oob="true">'
        f'as of {account.updated_at.strftime("%b %-d, %Y")}'
        f'</small>'
    )
    return html + as_of_html, 200, {"HX-Trigger": "balanceChanged"}


@accounts_bp.route("/accounts/<int:account_id>/anchor-form", methods=["GET"])
@login_required
def anchor_form(account_id):
    """HTMX partial: return the inline edit form for the anchor balance."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return "Not found", 404

    return render_template(
        "grid/_anchor_edit.html",
        account=account,
        editing=True,
    )


@accounts_bp.route("/accounts/<int:account_id>/anchor-display", methods=["GET"])
@login_required
def anchor_display(account_id):
    """HTMX partial: return the anchor balance display (non-editing)."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return "Not found", 404

    return render_template(
        "grid/_anchor_edit.html",
        account=account,
        editing=False,
    )
