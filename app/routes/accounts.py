"""
Shekel Budget App -- Account Routes

CRUD for accounts and account types, plus anchor balance true-up.
Returns HTMX fragments for inline editing at the top of the grid.
"""

import logging
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for, jsonify
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import AcctCategoryEnum, AcctTypeEnum
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.schemas.validation import (
    AccountCreateSchema,
    AccountUpdateSchema,
    AccountTypeCreateSchema,
    AccountTypeUpdateSchema,
    AnchorUpdateSchema,
    InterestParamsUpdateSchema,
)
from app.services import balance_calculator, pay_period_service

logger = logging.getLogger(__name__)

accounts_bp = Blueprint("accounts", __name__)

_anchor_schema = AnchorUpdateSchema()
_create_schema = AccountCreateSchema()
_update_schema = AccountUpdateSchema()
_type_create_schema = AccountTypeCreateSchema()
_type_update_schema = AccountTypeUpdateSchema()
_interest_params_schema = InterestParamsUpdateSchema()


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
        flash("Please correct the highlighted errors and try again.", "danger")
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
    db.session.flush()

    # Auto-create type-specific params based on metadata flags.
    account_type = db.session.get(AccountType, account.account_type_id)

    # Interest-bearing types: auto-create InterestParams with sensible defaults.
    if account_type and account_type.has_interest:
        if not db.session.query(InterestParams).filter_by(account_id=account.id).first():
            db.session.add(InterestParams(account_id=account.id))

    # Investment/retirement: auto-create InvestmentParams with sensible defaults.
    investment_type_ids = {
        ref_cache.acct_type_id(AcctTypeEnum.K401),
        ref_cache.acct_type_id(AcctTypeEnum.ROTH_401K),
        ref_cache.acct_type_id(AcctTypeEnum.TRADITIONAL_IRA),
        ref_cache.acct_type_id(AcctTypeEnum.ROTH_IRA),
        ref_cache.acct_type_id(AcctTypeEnum.BROKERAGE),
    }
    acct_type_id = account_type.id if account_type else None
    if acct_type_id in investment_type_ids:
        if not db.session.query(InvestmentParams).filter_by(account_id=account.id).first():
            db.session.add(InvestmentParams(account_id=account.id))

    db.session.commit()

    logger.info("Created account: %s (id=%d)", account.name, account.id)
    flash(f"Account '{account.name}' created.", "success")

    # Redirect parameterized accounts to their configuration page.
    if account_type and account_type.has_interest:
        return redirect(url_for(
            "accounts.interest_detail", account_id=account.id, setup=1,
        ))
    # Amortizing loan types: redirect to the unified loan dashboard.
    if account_type and account_type.has_amortization:
        return redirect(url_for(
            "loan.dashboard", account_id=account.id, setup=1,
        ))
    if acct_type_id in investment_type_ids:
        return redirect(url_for(
            "investment.dashboard", account_id=account.id, setup=1,
        ))

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
        flash("Please correct the highlighted errors and try again.", "danger")
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
            "Cannot deactivate this account -- it is used by active recurring transfers. "
            "Deactivate those recurring transfers first.",
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
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("settings.show", section="account-types"))

    data = _type_create_schema.load(request.form)

    # Check for duplicate name.
    existing = (
        db.session.query(AccountType)
        .filter_by(name=data["name"])
        .first()
    )
    if existing:
        flash("An account type with that name already exists.", "warning")
        return redirect(url_for("settings.show", section="account-types"))

    account_type = AccountType(
        category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
        **data,
    )
    db.session.add(account_type)
    db.session.commit()

    logger.info("Created account type: %s (id=%d)", account_type.name, account_type.id)
    flash(f"Account type '{account_type.name}' created.", "success")
    return redirect(url_for("settings.show", section="account-types"))


@accounts_bp.route("/accounts/types/<int:type_id>", methods=["POST"])
@login_required
def update_account_type(type_id):
    """Update an account type name."""
    account_type = db.session.get(AccountType, type_id)
    if account_type is None:
        flash("Account type not found.", "danger")
        return redirect(url_for("settings.show", section="account-types"))

    errors = _type_update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("settings.show", section="account-types"))

    data = _type_update_schema.load(request.form)

    # Check for duplicate name.
    existing = (
        db.session.query(AccountType)
        .filter(AccountType.name == data["name"], AccountType.id != type_id)
        .first()
    )
    if existing:
        flash("An account type with that name already exists.", "warning")
        return redirect(url_for("settings.show", section="account-types"))

    account_type.name = data["name"]
    db.session.commit()

    logger.info("Updated account type: %s (id=%d)", account_type.name, account_type.id)
    flash(f"Account type renamed to '{account_type.name}'.", "success")
    return redirect(url_for("settings.show", section="account-types"))


@accounts_bp.route("/accounts/types/<int:type_id>/delete", methods=["POST"])
@login_required
def delete_account_type(type_id):
    """Delete an account type (only if no accounts reference it)."""
    account_type = db.session.get(AccountType, type_id)
    if account_type is None:
        flash("Account type not found.", "danger")
        return redirect(url_for("settings.show", section="account-types"))

    in_use = (
        db.session.query(Account)
        .filter_by(account_type_id=type_id)
        .first()
    )
    if in_use:
        flash(
            "Cannot delete this account type -- it is in use by one or more accounts.",
            "warning",
        )
        return redirect(url_for("settings.show", section="account-types"))

    db.session.delete(account_type)
    db.session.commit()

    logger.info("Deleted account type: %s (id=%d)", account_type.name, type_id)
    flash(f"Account type '{account_type.name}' deleted.", "info")
    return redirect(url_for("settings.show", section="account-types"))


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


# ── Interest Detail & Params ──────────────────────────────────────


@accounts_bp.route("/accounts/<int:account_id>/interest")
@login_required
def interest_detail(account_id):
    """Interest-bearing account detail page with interest projections."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return redirect(url_for("accounts.list_accounts"))

    # Verify this is an interest-bearing account type.
    if not account.account_type or not account.account_type.has_interest:
        flash("This account type does not support interest parameters.", "warning")
        return redirect(url_for("accounts.list_accounts"))

    params = (
        db.session.query(InterestParams)
        .filter_by(account_id=account.id)
        .first()
    )
    if not params:
        # Auto-create params if missing (shouldn't happen normally).
        params = InterestParams(account_id=account.id)
        db.session.add(params)
        db.session.commit()

    user_id = current_user.id
    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)

    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=user_id, is_baseline=True)
        .first()
    )

    period_ids = [p.id for p in all_periods]

    # Load transactions scoped to this account using the account_id
    # column.  This includes shadow transactions from transfers, ad-hoc
    # transactions, and template-generated transactions.  The old
    # template_account_map approach silently excluded shadow transactions
    # (template_id=None), causing HYSA projections to miss all transfer
    # deposits.  Follows the pattern in grid.py lines 87-98.
    acct_transactions = (
        db.session.query(Transaction)
        .filter(
            Transaction.account_id == account.id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.scenario_id == scenario.id,
            Transaction.is_deleted.is_(False),
        )
        .all()
    ) if scenario and period_ids else []

    anchor_balance = account.current_anchor_balance or Decimal("0.00")
    anchor_period_id = account.current_anchor_period_id or (
        current_period.id if current_period else None
    )

    balances = {}
    interest_by_period = {}
    if anchor_period_id:
        balances, interest_by_period = balance_calculator.calculate_balances_with_interest(
            anchor_balance=anchor_balance,
            anchor_period_id=anchor_period_id,
            periods=all_periods,
            transactions=acct_transactions,
            interest_params=params,
        )

    current_bal = balances.get(current_period.id) if current_period else anchor_balance

    # Build period projection data for the template.
    period_data = []
    for p in all_periods:
        if p.id in balances:
            period_data.append({
                "period": p,
                "balance": balances[p.id],
                "interest": interest_by_period.get(p.id, Decimal("0.00")),
            })

    # 3/6/12 month horizon projections.
    projected = {}
    for offset_label, offset_count in [("3 months", 6), ("6 months", 13), ("1 year", 26)]:
        if current_period:
            target_idx = current_period.period_index + offset_count
            for p in all_periods:
                if p.period_index == target_idx and p.id in balances:
                    projected[offset_label] = balances[p.id]
                    break

    return render_template(
        "accounts/interest_detail.html",
        account=account,
        params=params,
        current_balance=current_bal,
        projected=projected,
        period_data=period_data,
    )


@accounts_bp.route("/accounts/<int:account_id>/interest/params", methods=["POST"])
@login_required
def update_interest_params(account_id):
    """Update interest parameters (APY, compounding frequency)."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        flash("Account not found.", "danger")
        return redirect(url_for("accounts.list_accounts"))

    if not account.account_type or not account.account_type.has_interest:
        flash("This account type does not support interest parameters.", "warning")
        return redirect(url_for("accounts.list_accounts"))

    errors = _interest_params_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("accounts.interest_detail", account_id=account_id))

    data = _interest_params_schema.load(request.form)

    params = (
        db.session.query(InterestParams)
        .filter_by(account_id=account.id)
        .first()
    )
    if not params:
        params = InterestParams(account_id=account.id)
        db.session.add(params)

    if "apy" in data:
        # Convert percentage input (e.g. 4.5 → 0.045) for storage.
        from decimal import Decimal as D
        params.apy = D(str(data["apy"])) / D("100")
    if "compounding_frequency" in data:
        params.compounding_frequency = data["compounding_frequency"]

    db.session.commit()
    logger.info("Updated interest params for account %d", account.id)
    flash("Interest parameters updated.", "success")
    return redirect(url_for("accounts.interest_detail", account_id=account_id))


# ── Checking Detail ──────────────────────────────────────────────


@accounts_bp.route("/accounts/<int:account_id>/checking")
@login_required
def checking_detail(account_id):
    """Checking account detail page with balance projections.

    Shows the current anchor balance and projected balances at
    3, 6, and 12-month intervals, computed by the same balance
    calculator the grid uses.  No interest calculations -- APY
    on checking is negligible.
    """
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return "Not found", 404

    # Verify this is a checking account.
    if (not account.account_type
            or account.account_type_id != ref_cache.acct_type_id(AcctTypeEnum.CHECKING)):
        return "Not found", 404

    user_id = current_user.id
    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)

    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=user_id, is_baseline=True)
        .first()
    )

    period_ids = [p.id for p in all_periods]

    # Load transactions scoped to this account.  Includes shadow
    # transactions from transfers, following the pattern in grid.py
    # and hysa_detail.
    acct_transactions = (
        db.session.query(Transaction)
        .filter(
            Transaction.account_id == account.id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.scenario_id == scenario.id,
            Transaction.is_deleted.is_(False),
        )
        .all()
    ) if scenario and period_ids else []

    anchor_balance = account.current_anchor_balance or Decimal("0.00")
    anchor_period_id = account.current_anchor_period_id or (
        current_period.id if current_period else None
    )

    balances = {}
    if anchor_period_id:
        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=anchor_balance,
            anchor_period_id=anchor_period_id,
            periods=all_periods,
            transactions=acct_transactions,
        )

    current_bal = balances.get(current_period.id) if current_period else anchor_balance

    # Build period projection data for the template.
    period_data = []
    for p in all_periods:
        if p.id in balances:
            period_data.append({
                "period": p,
                "balance": balances[p.id],
            })

    # 3/6/12 month horizon projections (same offsets as HYSA detail).
    projected = {}
    for offset_label, offset_count in [("3 months", 6), ("6 months", 13), ("1 year", 26)]:
        if current_period:
            target_idx = current_period.period_index + offset_count
            for p in all_periods:
                if p.period_index == target_idx and p.id in balances:
                    projected[offset_label] = balances[p.id]
                    break

    # Find the anchor period for display in the template.
    anchor_period = None
    if anchor_period_id:
        for p in all_periods:
            if p.id == anchor_period_id:
                anchor_period = p
                break

    return render_template(
        "accounts/checking_detail.html",
        account=account,
        current_balance=current_bal,
        projected=projected,
        period_data=period_data,
        anchor_period=anchor_period,
    )
