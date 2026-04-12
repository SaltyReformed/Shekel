"""
Shekel Budget App -- Settings Routes

User preferences management (grid defaults, inflation rate, etc.).
Settings dashboard consolidating all configuration sections.
"""

import logging
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import RoleEnum
from app.utils.auth_helpers import require_owner

from app.extensions import db
from app.models.account import Account
from app.schemas.validation import (
    CompanionCreateSchema,
    CompanionEditSchema,
    UserSettingsSchema,
)
from app.models.category import Category
from app.models.ref import AccountType, AccountTypeCategory, FilingStatus, TaxType
from app.models.tax_config import TaxBracketSet, FicaConfig, StateTaxConfig
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password

logger = logging.getLogger(__name__)

settings_bp = Blueprint("settings", __name__)

_VALID_SECTIONS = [
    "general", "categories", "pay-periods", "tax", "account-types",
    "retirement", "companions", "security",
]


@settings_bp.route("/settings", methods=["GET"])
@login_required
@require_owner
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
    categories = []
    archived_categories = []
    group_names = []
    icon_choices = []
    mfa_enabled = False

    if section == "general":
        accounts = (
            db.session.query(Account)
            .filter_by(user_id=current_user.id, is_active=True)
            .order_by(Account.sort_order, Account.name)
            .all()
        )
    elif section == "categories":
        all_categories = (
            db.session.query(Category)
            .filter_by(user_id=current_user.id)
            .order_by(Category.sort_order, Category.group_name, Category.item_name)
            .all()
        )
        active_categories = [c for c in all_categories if c.is_active]
        archived_categories = [c for c in all_categories if not c.is_active]
        # Distinct group names from active categories for the add/edit dropdown (5A.4-2).
        group_names = sorted(set(cat.group_name for cat in active_categories))
        for cat in active_categories:
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
        categories = (
            db.session.query(AccountTypeCategory)
            .order_by(AccountTypeCategory.id)
            .all()
        )
        icon_choices = [
            "bi-bank", "bi-wallet2", "bi-piggy-bank", "bi-cash-stack",
            "bi-safe", "bi-heart-pulse", "bi-credit-card", "bi-house",
            "bi-car-front", "bi-mortarboard", "bi-cash-coin",
            "bi-graph-up-arrow", "bi-bar-chart-line", "bi-building",
            "bi-briefcase", "bi-coin", "bi-currency-exchange",
        ]
    # elif section == "retirement": settings already loaded above.
    # "companions" is handled inline in the render_template call below
    # -- no per-branch locals are needed because _load_companions_context
    # returns the four template variables as a single dict.
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
        categories=categories,
        archived_categories=archived_categories,
        group_names=group_names,
        icon_choices=icon_choices,
        mfa_enabled=mfa_enabled,
        **(
            _load_companions_context(request.args.get("edit"))
            if section == "companions"
            else _empty_companions_context()
        ),
    )


@settings_bp.route("/settings", methods=["POST"])
@login_required
@require_owner
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

    if "large_transaction_threshold" in data and data["large_transaction_threshold"] is not None:
        settings.large_transaction_threshold = data["large_transaction_threshold"]

    if "trend_alert_threshold" in data and data["trend_alert_threshold"] is not None:
        # User enters integer percentage (e.g. 15 for 15%); store as decimal fraction.
        settings.trend_alert_threshold = (
            Decimal(str(data["trend_alert_threshold"])) / Decimal("100")
        )

    if "anchor_staleness_days" in data and data["anchor_staleness_days"] is not None:
        settings.anchor_staleness_days = data["anchor_staleness_days"]

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


# --- Companion account management ----------------------------------------
#
# Companions are created, edited, deactivated, and reactivated here.
# Every route below applies two guards on top of the module-level
# @login_required + @require_owner decorators:
#   1. Target user must have role_id == COMPANION.  This rejects any
#      attempt to edit another owner (or the current user).
#   2. Target user's linked_owner_id must equal current_user.id.  This
#      rejects any cross-owner tampering even though this app only has
#      one owner today.
# A failing guard returns 404, matching the project rule "404 for both
# 'not found' and 'not yours.'"


def _empty_companions_context():
    """Return the default template kwargs for a non-companion section.

    Used by settings.show() when the active section is not
    ``companions`` so the template still receives the four keys it
    expects (active_companions, inactive_companions,
    edit_companion_id, form_values) as empty placeholders.
    """
    return {
        "active_companions": [],
        "inactive_companions": [],
        "edit_companion_id": None,
        "form_values": {},
    }


def _load_companion_or_404(companion_id):
    """Return the owned companion row or abort with 404.

    Applies both defense-in-depth checks (role and linked owner) and
    uses the same 404 response for missing rows and unowned rows.
    """
    target = db.session.get(User, companion_id)
    companion_role = ref_cache.role_id(RoleEnum.COMPANION)
    if target is None:
        abort(404)
    if target.role_id != companion_role:
        abort(404)
    if target.linked_owner_id != current_user.id:
        abort(404)
    return target


def _load_companions_context(raw_edit_id):
    """Return the dict of template variables for the companions section.

    Args:
        raw_edit_id: The ``?edit=<id>`` query-string value, or None.
            Invalid or non-numeric values fall back silently to the
            create form.

    Returns:
        A dict with keys ``active_companions``, ``inactive_companions``,
        ``edit_companion_id``, and ``form_values``.  The form_values
        dict pre-populates the edit form with the matching companion's
        email and display name when a valid ``edit`` id is supplied.
    """
    companions = (
        db.session.query(User)
        .filter_by(
            linked_owner_id=current_user.id,
            role_id=ref_cache.role_id(RoleEnum.COMPANION),
        )
        .order_by(User.display_name, User.email)
        .all()
    )
    active = [c for c in companions if c.is_active]
    inactive = [c for c in companions if not c.is_active]

    edit_id = None
    values = {}
    if raw_edit_id and raw_edit_id.isdigit():
        candidate = next(
            (c for c in active if c.id == int(raw_edit_id)), None,
        )
        if candidate is not None:
            edit_id = candidate.id
            values = {
                "email": candidate.email,
                "display_name": candidate.display_name or "",
            }
    return {
        "active_companions": active,
        "inactive_companions": inactive,
        "edit_companion_id": edit_id,
        "form_values": values,
    }


def _render_companions_section(errors=None, form_values=None,
                               edit_companion_id=None):
    """Re-render the companion section with validation errors preserved.

    Used when create/edit form submission fails validation.  Reloads
    the companion list so the sidebar/list render correctly even
    though the form has errors.  The supplied form_values and
    edit_companion_id override whatever _load_companions_context
    would derive from the query string.
    """
    ctx = _load_companions_context(None)
    return render_template(
        "settings/dashboard.html",
        active_section="companions",
        settings=current_user.settings,
        accounts=[],
        grouped={},
        errors=errors or {},
        filing_statuses=[],
        tax_types=[],
        bracket_sets=[],
        fica_configs=[],
        state_configs=[],
        account_types=[],
        types_in_use=set(),
        categories=[],
        archived_categories=[],
        group_names=[],
        icon_choices=[],
        mfa_enabled=False,
        active_companions=ctx["active_companions"],
        inactive_companions=ctx["inactive_companions"],
        edit_companion_id=edit_companion_id,
        form_values=form_values or {},
    )


@settings_bp.route("/settings/companions", methods=["POST"])
@login_required
@require_owner
def companion_create():
    """Create a new companion user linked to the current owner."""
    schema = CompanionCreateSchema()
    errors = schema.validate(request.form)
    if errors:
        # Preserve user-entered email and display name, but never echo
        # the password back into the form.
        form_values = {
            "email": request.form.get("email", "").strip().lower(),
            "display_name": request.form.get("display_name", "").strip(),
        }
        return _render_companions_section(
            errors=errors, form_values=form_values,
        ), 400

    data = schema.load(request.form)

    # Email uniqueness: check against the entire users table, not just
    # companions.  An owner cannot reuse their own email or any other
    # user's email.
    existing = (
        db.session.query(User)
        .filter(db.func.lower(User.email) == data["email"])
        .first()
    )
    if existing is not None:
        return _render_companions_section(
            errors={"email": ["Email address is already in use."]},
            form_values={
                "email": data["email"],
                "display_name": data["display_name"],
            },
        ), 400

    companion_role = ref_cache.role_id(RoleEnum.COMPANION)
    companion = User(
        email=data["email"],
        password_hash=hash_password(data["password"]),
        display_name=data["display_name"],
        role_id=companion_role,
        linked_owner_id=current_user.id,
        is_active=True,
    )
    db.session.add(companion)
    db.session.flush()

    # Create UserSettings alongside the User, matching register_user()
    # and seed_companion.py so settings.show() never sees a None on
    # the companion's first access.
    db.session.add(UserSettings(user_id=companion.id))
    db.session.commit()

    flash(f"Companion account '{data['display_name']}' created.", "success")
    return redirect(url_for("settings.show", section="companions"))


@settings_bp.route(
    "/settings/companions/<int:companion_id>/edit", methods=["POST"],
)
@login_required
@require_owner
def companion_edit(companion_id):
    """Update an existing companion's email, name, and optional password."""
    companion = _load_companion_or_404(companion_id)

    schema = CompanionEditSchema()
    errors = schema.validate(request.form)
    if errors:
        form_values = {
            "email": request.form.get("email", "").strip().lower(),
            "display_name": request.form.get("display_name", "").strip(),
        }
        return _render_companions_section(
            errors=errors,
            form_values=form_values,
            edit_companion_id=companion.id,
        ), 400

    data = schema.load(request.form)

    # Email uniqueness: allow the companion to keep their own email,
    # but reject any other user's email.
    existing = (
        db.session.query(User)
        .filter(db.func.lower(User.email) == data["email"])
        .filter(User.id != companion.id)
        .first()
    )
    if existing is not None:
        return _render_companions_section(
            errors={"email": ["Email address is already in use."]},
            form_values={
                "email": data["email"],
                "display_name": data["display_name"],
            },
            edit_companion_id=companion.id,
        ), 400

    companion.email = data["email"]
    companion.display_name = data["display_name"]

    new_password = data.get("password") or ""
    if new_password:
        companion.password_hash = hash_password(new_password)
        # Invalidate any live companion session -- matches the behavior
        # of change_password in auth.py for the owner's own sessions.
        companion.session_invalidated_at = db.func.now()

    db.session.commit()

    flash(f"Companion account '{companion.display_name}' updated.", "success")
    return redirect(url_for("settings.show", section="companions"))


@settings_bp.route(
    "/settings/companions/<int:companion_id>/deactivate", methods=["POST"],
)
@login_required
@require_owner
def companion_deactivate(companion_id):
    """Soft-delete a companion by flipping is_active to False.

    The companion's entries, MFA config, and settings row are
    preserved.  Flask-Login's user_loader rejects any session whose
    user has is_active=False, so all live companion sessions become
    invalid on their next request.
    """
    companion = _load_companion_or_404(companion_id)

    if not companion.is_active:
        flash("Companion account is already deactivated.", "info")
        return redirect(url_for("settings.show", section="companions"))

    companion.is_active = False
    # Belt-and-suspenders: stamp the invalidation time so session
    # checks that look at session_invalidated_at also reject live
    # sessions, not just the is_active flag.
    companion.session_invalidated_at = db.func.now()
    db.session.commit()

    flash(
        f"Companion account '{companion.display_name}' deactivated. "
        "Their entries are preserved.",
        "success",
    )
    return redirect(url_for("settings.show", section="companions"))


@settings_bp.route(
    "/settings/companions/<int:companion_id>/reactivate", methods=["POST"],
)
@login_required
@require_owner
def companion_reactivate(companion_id):
    """Restore access for a previously deactivated companion.

    Only is_active is flipped back to True.  session_invalidated_at
    is intentionally left at the deactivation timestamp so any stale
    session cookies from before deactivation remain invalid -- the
    companion must log in fresh.
    """
    companion = _load_companion_or_404(companion_id)

    if companion.is_active:
        flash("Companion account is already active.", "info")
        return redirect(url_for("settings.show", section="companions"))

    companion.is_active = True
    db.session.commit()

    flash(
        f"Companion account '{companion.display_name}' reactivated.",
        "success",
    )
    return redirect(url_for("settings.show", section="companions"))
