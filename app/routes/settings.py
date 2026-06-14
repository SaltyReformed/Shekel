"""
Shekel Budget App -- Settings Routes

User preferences management (grid defaults, inflation rate, etc.).
Settings dashboard consolidating all configuration sections.
"""

import logging

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import RoleEnum
from app.utils.auth_helpers import fresh_login_required, require_owner

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
from app.models.user import MfaConfig, User, UserSettings
from app.services import (
    account_service,
    pay_period_admin,
    pay_period_service,
    pay_schedule_service,
)
from app.services.auth_service import hash_password

logger = logging.getLogger(__name__)

settings_bp = Blueprint("settings", __name__)

_VALID_SECTIONS = [
    "general", "categories", "pay-periods", "tax", "account-types",
    "retirement", "companions", "security",
]

# The scalar UserSettings fields the POST handler copies straight from the
# validated form.  All are storage-domain values: UserSettingsSchema's
# ``@pre_load`` already divided ``default_inflation_rate`` and
# ``trend_alert_threshold`` from form percent into the [0, 1] decimal fraction
# the DB CHECK enforces (E-28 / HIGH-06 / PA-01, Commit 24), so the route stores
# them verbatim.  Zero is a valid value (E-12 "zero is a value"), so each field
# is applied on ``is not None``, never on truthiness.  ``default_grid_account_id``
# is deliberately NOT in this list -- it needs a route-level ownership check.
_SIMPLE_SETTINGS_FIELDS = (
    "grid_default_periods",
    "default_inflation_rate",
    "low_balance_threshold",
    "large_transaction_threshold",
    "trend_alert_threshold",
    "anchor_staleness_days",
)

# Bootstrap icon classes offered when creating/editing a custom account type.
# Immutable static data, so it is a module-scope tuple rather than a list rebuilt
# per request (the template only iterates it -- it is never mutated).
_ACCOUNT_TYPE_ICON_CHOICES = (
    "bi-bank", "bi-wallet2", "bi-piggy-bank", "bi-cash-stack",
    "bi-safe", "bi-heart-pulse", "bi-credit-card", "bi-house",
    "bi-car-front", "bi-mortarboard", "bi-cash-coin",
    "bi-graph-up-arrow", "bi-bar-chart-line", "bi-building",
    "bi-briefcase", "bi-coin", "bi-currency-exchange",
)

# Pay-period lock-reason -> (badge label, Bootstrap badge CSS) for the
# manage UI.  This display-only mapping lives in the route layer so the
# template renders a precomputed badge instead of branching on the enum.
_PP_LOCK_BADGES = {
    pay_period_admin.PeriodLockReason.HISTORICAL: ("Past", "bg-secondary"),
    pay_period_admin.PeriodLockReason.SETTLED_TXN: (
        "Settled", "bg-warning text-dark",
    ),
    pay_period_admin.PeriodLockReason.ACCOUNT_ANCHOR: (
        "Anchor", "bg-info text-dark",
    ),
    pay_period_admin.PeriodLockReason.RECURRENCE_ANCHOR: (
        "Rule start", "bg-info text-dark",
    ),
}
_PP_MUTABLE_BADGE = ("Editable", "bg-success-subtle text-success-emphasis")


@settings_bp.route("/settings", methods=["GET"])
@login_required
@require_owner
def show():
    """Display the settings dashboard."""
    section = request.args.get("section", "general")
    if section not in _VALID_SECTIONS:
        section = "general"
    return render_settings_dashboard(section)


def _section_context(section):
    """Per-section template data for the settings dashboard.

    Returns only the active section's slice; the caller overlays it on
    the full default context so the template's "all keys always supplied"
    contract holds.  "retirement" needs no extra data (it renders from the
    loaded ``settings``), so it -- like any unknown section -- falls
    through to an empty dict.
    """
    loaders = {
        "general": lambda: {
            "accounts": account_service.list_active_accounts(current_user.id),
        },
        "categories": _load_categories_context,
        "tax": _load_tax_context,
        "account-types": _load_account_types_context,
        "companions": lambda: _load_companions_context(request.args.get("edit")),
        "security": _load_security_context,
        "pay-periods": lambda: _load_pay_periods_context(current_user.id),
    }
    loader = loaders.get(section)
    return loader() if loader is not None else {}


def render_settings_dashboard(section, extra=None, status=200):
    """Render the settings dashboard for one section.

    The single render path shared by :func:`show` and the pay-period
    management routes (``app/routes/pay_periods.py``), so the full
    template context (every section's keys plus the active section's data)
    is assembled in one place.  ``extra`` overlays error / confirm
    context; ``status`` lets a route re-render with a 422 (e.g. the
    discard-confirm panel) instead of redirecting.

    Args:
        section: The active settings section.
        extra: Optional dict overlaid on the context after the section
            data (errors, a discard-confirm payload, etc.).
        status: HTTP status for the rendered response.

    Returns:
        A Flask ``(body, status)`` response tuple.
    """
    settings = current_user.settings
    if settings is None:
        settings = UserSettings(user_id=current_user.id)
        db.session.add(settings)
        db.session.commit()

    context = _empty_section_context()
    context.update(_section_context(section))
    if extra:
        context.update(extra)

    return render_template(
        "settings/dashboard.html",
        active_section=section,
        settings=settings,
        **context,
    ), status


def _load_pay_periods_context(user_id):
    """Load the pay-periods section: the period list with lock badges.

    Each row carries a :class:`~app.models.pay_period.PayPeriod`, whether
    it is ``locked`` (immutable), and the display badge text/CSS the
    manage UI renders -- the lock-reason-to-badge mapping is resolved here
    so the template only displays, never computes.  ``pp_schedule`` is the
    persisted schedule (cadence) when one exists.
    """
    periods = pay_period_service.get_all_periods(user_id)
    locks = pay_period_admin.classify_periods_bulk(periods)
    period_rows = []
    for period in periods:
        reason = locks.get(period.id)
        badge_label, badge_class = _PP_LOCK_BADGES.get(
            reason, _PP_MUTABLE_BADGE,
        )
        period_rows.append({
            "period": period,
            "locked": reason is not None,
            "badge_label": badge_label,
            "badge_class": badge_class,
        })
    return {
        "pp_periods": period_rows,
        "pp_schedule": pay_schedule_service.get_schedule(user_id),
    }


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

    # Copy the schema-validated scalar settings (the allowlist + the
    # storage-domain conversion rationale live on _SIMPLE_SETTINGS_FIELDS).
    for field in _SIMPLE_SETTINGS_FIELDS:
        if field in data and data[field] is not None:
            setattr(settings, field, data[field])

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


def _empty_section_context():
    """Return the dashboard template's full keyword context at defaults.

    ``settings.show`` (and the companion-form re-render) always supply every
    section's template variable so the dashboard partial can reference any of
    them regardless of the active section; the active section then overrides
    only its own slice.  Keeping the defaults in one place stops these keys
    from drifting between the GET handler and the companion-form re-render.
    """
    return {
        "errors": {},
        "accounts": [],
        "grouped": {},
        "filing_statuses": [],
        "tax_types": [],
        "bracket_sets": [],
        "fica_configs": [],
        "state_configs": [],
        "account_types": [],
        "types_in_use": set(),
        "categories": [],
        "archived_categories": [],
        "group_names": [],
        "icon_choices": [],
        "mfa_enabled": False,
        "active_companions": [],
        "inactive_companions": [],
        "edit_companion_id": None,
        "form_values": {},
        "pp_periods": [],
        "pp_schedule": None,
        "pp_confirm": None,
    }


def _load_categories_context():
    """Load the categories section: grouped active categories + the archive.

    Returns ``grouped`` (group_name -> [category]), the sorted distinct
    ``group_names`` for the add/edit dropdown (5A.4-2), and the
    ``archived_categories`` list.
    """
    all_categories = (
        db.session.query(Category)
        .filter_by(user_id=current_user.id)
        .order_by(Category.sort_order, Category.group_name, Category.item_name)
        .all()
    )
    active_categories = [c for c in all_categories if c.is_active]
    grouped = {}
    for cat in active_categories:
        grouped.setdefault(cat.group_name, []).append(cat)
    return {
        "grouped": grouped,
        "group_names": sorted(set(cat.group_name for cat in active_categories)),
        "archived_categories": [c for c in all_categories if not c.is_active],
    }


def _load_tax_context():
    """Load the tax section: filing statuses / tax types (reference) plus the
    user's bracket sets, FICA configs, and state tax configs.
    """
    return {
        "filing_statuses": db.session.query(FilingStatus).all(),
        "tax_types": db.session.query(TaxType).all(),
        "bracket_sets": (
            db.session.query(TaxBracketSet)
            .filter_by(user_id=current_user.id)
            .order_by(TaxBracketSet.tax_year.desc(), TaxBracketSet.filing_status_id)
            .all()
        ),
        "fica_configs": (
            db.session.query(FicaConfig)
            .filter_by(user_id=current_user.id)
            .order_by(FicaConfig.tax_year.desc())
            .all()
        ),
        "state_configs": (
            db.session.query(StateTaxConfig)
            .filter_by(user_id=current_user.id)
            .all()
        ),
    }


def _load_account_types_context():
    """Load the account-types section: the user's visible account types, which
    type IDs are in use, the category list, and the icon choices.

    The listing is scoped to seeded built-ins (``user_id IS NULL``) plus the
    current user's own custom types (commit C-28 / F-044); other owners' custom
    types are intentionally invisible.
    """
    account_types = (
        db.session.query(AccountType)
        .filter(db.or_(
            AccountType.user_id.is_(None),
            AccountType.user_id == current_user.id,
        ))
        .order_by(AccountType.name)
        .all()
    )
    return {
        "account_types": account_types,
        "types_in_use": account_service.get_account_type_ids_in_use(current_user.id),
        "categories": (
            db.session.query(AccountTypeCategory)
            .order_by(AccountTypeCategory.id)
            .all()
        ),
        "icon_choices": _ACCOUNT_TYPE_ICON_CHOICES,
    }


def _load_security_context():
    """Load the security section: whether MFA is enabled for the user."""
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    return {"mfa_enabled": mfa_config.is_enabled if mfa_config else False}


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
    # Reuse the GET handler's full default context so the companions
    # form re-render and settings.show() cannot drift on the template's
    # key set; override only the error/companion slice.
    context = _empty_section_context()
    context.update({
        "errors": errors or {},
        "active_companions": ctx["active_companions"],
        "inactive_companions": ctx["inactive_companions"],
        "edit_companion_id": edit_companion_id,
        "form_values": form_values or {},
    })
    return render_template(
        "settings/dashboard.html",
        active_section="companions",
        settings=current_user.settings,
        **context,
    )


@settings_bp.route("/settings/companions", methods=["POST"])
@login_required
@require_owner
@fresh_login_required()
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
@fresh_login_required()
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
@fresh_login_required()
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
@fresh_login_required()
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
