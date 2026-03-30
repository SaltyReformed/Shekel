"""
Shekel Budget App -- Settings Routes

User preferences management (grid defaults, inflation rate, etc.).
Settings dashboard consolidating all configuration sections.
"""

import logging
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models.account import Account
from app.schemas.validation import UserSettingsSchema
from app.models.category import Category
from app.models.ref import AccountType, FilingStatus, TaxType
from app.models.tax_config import TaxBracketSet, FicaConfig, StateTaxConfig
from app.models.user import UserSettings

logger = logging.getLogger(__name__)

settings_bp = Blueprint("settings", __name__)

_VALID_SECTIONS = ["general", "categories", "pay-periods", "tax", "account-types", "retirement", "security"]


@settings_bp.route("/settings", methods=["GET"])
@login_required
def show():
    """Display the settings dashboard."""
    section = request.args.get("section", "general")
    if section not in _VALID_SECTIONS:
        section = "general"

    # Always load settings (needed for general + retirement sections).
    settings = current_user.settings
    if settings is None:
        settings = UserSettings(user_id=current_user.id)
        db.session.add(settings)
        db.session.commit()

    # Section-specific data loading.
    accounts = []
    grouped = {}
    errors = {}
    filing_statuses = []
    tax_types = []
    bracket_sets = []
    fica_configs = []
    state_configs = []
    account_types = []
    types_in_use = set()
    mfa_enabled = False

    if section == "general":
        accounts = (
            db.session.query(Account)
            .filter_by(user_id=current_user.id, is_active=True)
            .order_by(Account.sort_order, Account.name)
            .all()
        )
    elif section == "categories":
        categories = (
            db.session.query(Category)
            .filter_by(user_id=current_user.id)
            .order_by(Category.sort_order, Category.group_name, Category.item_name)
            .all()
        )
        for cat in categories:
            grouped.setdefault(cat.group_name, []).append(cat)
    elif section == "pay-periods":
        pass  # Just needs errors={} which is already set.
    elif section == "tax":
        filing_statuses = db.session.query(FilingStatus).all()
        tax_types = db.session.query(TaxType).all()
        bracket_sets = (
            db.session.query(TaxBracketSet)
            .filter_by(user_id=current_user.id)
            .order_by(TaxBracketSet.tax_year.desc(), TaxBracketSet.filing_status_id)
            .all()
        )
        fica_configs = (
            db.session.query(FicaConfig)
            .filter_by(user_id=current_user.id)
            .order_by(FicaConfig.tax_year.desc())
            .all()
        )
        state_configs = (
            db.session.query(StateTaxConfig)
            .filter_by(user_id=current_user.id)
            .all()
        )
    elif section == "account-types":
        account_types = (
            db.session.query(AccountType)
            .order_by(AccountType.name)
            .all()
        )
        types_in_use = set(
            row[0] for row in
            db.session.query(Account.account_type_id)
            .filter_by(user_id=current_user.id)
            .distinct()
            .all()
        )
    # elif section == "retirement": settings already loaded above.
    elif section == "security":
        from app.models.user import MfaConfig  # pylint: disable=import-outside-toplevel
        mfa_config = (
            db.session.query(MfaConfig)
            .filter_by(user_id=current_user.id)
            .first()
        )
        mfa_enabled = mfa_config.is_enabled if mfa_config else False

    return render_template(
        "settings/dashboard.html",
        active_section=section,
        settings=settings,
        accounts=accounts,
        grouped=grouped,
        errors=errors,
        filing_statuses=filing_statuses,
        tax_types=tax_types,
        bracket_sets=bracket_sets,
        fica_configs=fica_configs,
        state_configs=state_configs,
        account_types=account_types,
        types_in_use=types_in_use,
        mfa_enabled=mfa_enabled,
    )


@settings_bp.route("/settings", methods=["POST"])
@login_required
def update():
    """Update user settings."""
    schema = UserSettingsSchema()
    errors = schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("settings.show", section="general"))

    data = schema.load(request.form)

    settings = current_user.settings
    if settings is None:
        settings = UserSettings(user_id=current_user.id)
        db.session.add(settings)

    if "grid_default_periods" in data and data["grid_default_periods"] is not None:
        settings.grid_default_periods = data["grid_default_periods"]

    if "default_inflation_rate" in data and data["default_inflation_rate"] is not None:
        # User enters percentage (e.g. 3 for 3%); store as decimal fraction.
        settings.default_inflation_rate = (
            Decimal(str(data["default_inflation_rate"])) / Decimal("100")
        )

    if "low_balance_threshold" in data and data["low_balance_threshold"] is not None:
        settings.low_balance_threshold = data["low_balance_threshold"]

    # Account ownership check stays in the route (can't be in schema).
    if "default_grid_account_id" in data:
        acct_id = data["default_grid_account_id"]
        if acct_id is None:
            settings.default_grid_account_id = None
        else:
            acct = db.session.get(Account, acct_id)
            if acct and acct.user_id == current_user.id and acct.is_active:
                settings.default_grid_account_id = acct_id
            else:
                flash("Invalid grid account.", "danger")
                return redirect(url_for("settings.show", section="general"))

    db.session.commit()
    flash("Settings updated.", "success")
    return redirect(url_for("settings.show", section="general"))
