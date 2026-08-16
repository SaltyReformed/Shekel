"""
Shekel Budget App -- Retirement Planning Routes

Retirement dashboard with pension management, income gap analysis,
and retirement planning settings.
"""

import logging
from datetime import date
from decimal import Decimal

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError

from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.db_errors import is_unique_violation
from app.utils.error_fragments import designed_error

from app.extensions import db
from app.models.pension_profile import PensionProfile
from app.models.salary_profile import SalaryProfile
from app.models.user import UserSettings
from app.schemas.validation import (
    PensionProfileCreateSchema,
    PensionProfileUpdateSchema,
    RetirementReadinessQuerySchema,
    RetirementSettingsSchema,
)
from app.services import (
    retirement_dashboard_service,
    retirement_levers,
    retirement_plan,
    retirement_readiness,
)
from app.services.balance_at import BalanceContext

logger = logging.getLogger(__name__)

# Percentage scaler: the blended return is carried as a fraction (the form the
# annuity factor and the growth engine take) and the assumptions rail formats
# it as a percent.  One conversion, at the display boundary.
_PCT_SCALE = Decimal("100")

# Field allowlists for the retirement update routes: which submitted form
# fields may be written back to each model via setattr.
_PENSION_FIELDS = {
    "salary_profile_id", "name", "benefit_multiplier",
    "consecutive_high_years", "hire_date",
    "earliest_retirement_date", "planned_retirement_date",
}
_SETTINGS_FIELDS = {
    "safe_withdrawal_rate", "planned_retirement_date",
    "estimated_retirement_tax_rate", "merit_raise_horizon_years",
}

# Name of the composite unique constraint that backstops the
# pension-profile double-submit fix (F-105 / C-22).  Mirrors the
# literal in ``app/models/pension_profile.py:PensionProfile.__table_args__``
# and ``migrations/versions/<C-22 revision>.py``; renaming the
# constraint requires a coordinated edit across all three sites.
_PENSION_PROFILE_UNIQUE_CONSTRAINT = "uq_pension_profiles_user_name"

retirement_bp = Blueprint("retirement", __name__)

_pension_create_schema = PensionProfileCreateSchema()
_pension_update_schema = PensionProfileUpdateSchema()
_settings_schema = RetirementSettingsSchema()
_readiness_query_schema = RetirementReadinessQuerySchema()


@retirement_bp.route("/retirement")
@login_required
@require_owner
def dashboard():
    """The direction-D retirement readiness page.

    Derives the retirement picture ONCE and shapes the readiness picture
    from it (:func:`~app.services.retirement_readiness.readiness_from_picture`);
    the levers run on the same loaded inputs and the same memoized picture.
    The context carries exactly what the rebuilt template consumes: the
    readiness dict, the lever baselines, the per-account projections +
    salary profiles for the accounts table, the blended return for the
    what-if-only assumed-return row, and the settings row the included
    assumptions rail echoes its stored values from (P4 live-verify
    defect: the P3c slim dropped ``settings``, so every rail input
    rendered its empty/fallback state -- the stored SWR invisible, the
    merit horizon showing the template literal 5).  The legacy gap-table
    context (gap analysis, chart data, SWR slider default) retired with
    the old page (P3c).

    **This route opens the render's ONE read pass and LOADS its inputs once**
    (plan steps C2-f2d-1 and C2-f2d-2, ledger rows **P43** and **P57**).  The
    readiness verdict and the lever card are two views of ONE retirement
    picture: they belong to one owner, one baseline scenario, one day and one
    plan.  Each used to build its own pass AND its own loaded inputs AND its
    own derivation of that picture -- 86 of the render's 179 queries on a
    production clone were the second copy, and the lever card's month-0 probe
    recomputed the verdict the hero had already drawn.

    **What that buys, stated exactly.**  ``picture_at(inputs, STORED_PLAN)``
    below and the lever solver's own month-0 probe are the SAME object, not two
    equal ones, so the two cards cannot state different figures for one plan.
    It does not make this render single-clock: ``compute_pension_summary``,
    ``compute_gap_net_biweekly`` and ``build_employer_salary_basis`` still read
    ``date.today().year`` for themselves.  Ledger row **P55** owns that
    remainder.  The gate for what IS claimed here is
    ``tests/test_arch/test_one_read_pass_per_render.py``.
    """
    inputs = retirement_plan.load_retirement_inputs(
        BalanceContext.build(current_user.id),
    )
    picture = retirement_plan.picture_at(inputs, retirement_plan.STORED_PLAN)
    readiness = retirement_readiness.readiness_from_picture(picture)
    return render_template(
        "retirement/dashboard.html",
        # The rail's "Assumed return" row: the rate this page's own projection
        # actually grew at, scaled to the percent the template formats.  It was
        # a third derivation of that rate until plan step C2-f2d-2.
        current_return=picture.blended_return * _PCT_SCALE,
        readiness=readiness,
        levers=retirement_levers.compute_lever_data(inputs),
        retirement_account_projections=picture.projections,
        salary_profiles=inputs.gap.salary_profiles,
        settings=inputs.gap.settings,
        date_provenance=readiness["date_provenance"],
    )


def _pension_date_errors(eff_hire, eff_earliest, eff_planned):
    """Cross-field pension date rules, shared by both pension-date writers.

    Extracted from ``update_pension`` (acceptance r2 item 2) so the
    assumptions rail's date row -- which now writes through to the
    owning pension -- enforces the SAME rules as the pension form
    instead of duplicating them: planned/earliest must fall after the
    hire date, the planned date must be in the future, and the planned
    date cannot precede the earliest retirement date when one is set.

    Args:
        eff_hire: The effective hire date (submitted or stored).
        eff_earliest: The effective earliest retirement date, or ``None``.
        eff_planned: The effective planned retirement date, or ``None``.

    Returns:
        dict mapping field name to a list of error messages; empty when
        every rule passes.
    """
    date_errors = {}
    if eff_earliest and eff_hire and eff_earliest <= eff_hire:
        date_errors.setdefault("earliest_retirement_date", []).append(
            "Must be after hire date."
        )
    if eff_planned and eff_hire and eff_planned <= eff_hire:
        date_errors.setdefault("planned_retirement_date", []).append(
            "Must be after hire date."
        )
    if eff_planned and eff_planned <= date.today():
        date_errors.setdefault("planned_retirement_date", []).append(
            "Must be in the future."
        )
    if eff_planned and eff_earliest and eff_planned < eff_earliest:
        date_errors.setdefault("planned_retirement_date", []).append(
            "Must be on or after earliest retirement date."
        )
    return date_errors


# ── Pension CRUD ─────────────────────────────────────────────────


@retirement_bp.route("/retirement/pension")
@login_required
@require_owner
def pension_list():
    """List pension profiles."""
    pensions = (
        db.session.query(PensionProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )
    salary_profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )
    return render_template(
        "retirement/pension_form.html",
        pension=None,
        pensions=pensions,
        salary_profiles=salary_profiles,
    )


def _require_owned_salary_profile(data):
    """Abort 404 if the payload links a salary profile the user does not own.

    The pension form's salary-profile dropdown lists only the current
    user's own active profiles, so a foreign or non-existent
    ``salary_profile_id`` in the submission is a forged FK (IDOR) that
    would otherwise read another user's salary and raise history into
    this user's retirement projection.  ``get_or_404`` verifies ownership
    and emits the cross-user denial audit event; its ``None`` result maps
    to a 404 per the security response rule (404 for both "not found" and
    "not yours").  An absent or ``None`` id means "no pension-salary
    link" and is left to the normal create/update path.

    Args:
        data: The schema-loaded form payload.
    """
    salary_profile_id = data.get("salary_profile_id")
    if salary_profile_id is None:
        return
    if get_or_404(SalaryProfile, salary_profile_id) is None:
        abort(404)


@retirement_bp.route("/retirement/pension", methods=["POST"])
@login_required
@require_owner
def create_pension():
    """Create a new pension profile."""
    errors = _pension_create_schema.validate(request.form)
    if errors:
        pensions = (
            db.session.query(PensionProfile)
            .filter_by(user_id=current_user.id, is_active=True)
            .all()
        )
        salary_profiles = (
            db.session.query(SalaryProfile)
            .filter_by(user_id=current_user.id, is_active=True)
            .all()
        )
        return render_template(
            "retirement/pension_form.html",
            pension=None,
            pensions=pensions,
            salary_profiles=salary_profiles,
            form_data=dict(request.form),
            errors=errors,
        ), 422

    data = _pension_create_schema.load(request.form)
    _require_owned_salary_profile(data)

    # F-17 / Commit 12: percent-to-fraction conversion happens in the
    # schema's @pre_load; ``benefit_multiplier`` arrives already
    # converted to its decimal-fraction storage form.
    pension = PensionProfile(user_id=current_user.id, **data)
    db.session.add(pension)
    try:
        db.session.commit()
    except IntegrityError as exc:
        # Duplicate-name double-submit (F-105 / C-22): the composite
        # unique ``uq_pension_profiles_user_name`` rejects the second
        # INSERT when the user clicks Save twice in a row.  Roll back
        # and treat as idempotent success: re-fetch the winning row
        # so the user lands on the retirement dashboard with the
        # pension they intended to create, regardless of which
        # request reached the database first.
        db.session.rollback()
        if not is_unique_violation(exc, _PENSION_PROFILE_UNIQUE_CONSTRAINT):
            raise
        existing = (
            db.session.query(PensionProfile)
            .filter_by(user_id=current_user.id, name=data["name"])
            .first()
        )
        if existing is None:
            # The winning row was deleted between the IntegrityError
            # and this lookup -- vanishingly unlikely.  Surface as a
            # warning and let the user retry.
            flash(
                "A pension profile with that name already exists.",
                "warning",
            )
            return redirect(url_for("retirement.dashboard"))
        logger.info(
            "Duplicate pension profile prevented; existing id=%d "
            "(idempotent success)", existing.id,
        )
        flash(f"Pension profile '{existing.name}' already exists.", "info")
        return redirect(url_for("retirement.dashboard"))

    logger.info("user_id=%d created pension profile %d", current_user.id, pension.id)
    flash(f"Pension profile '{pension.name}' created.", "success")
    return redirect(url_for("retirement.dashboard"))


@retirement_bp.route("/retirement/pension/<int:pension_id>/edit")
@login_required
@require_owner
def edit_pension(pension_id):
    """Display pension profile edit form."""
    pension = get_or_404(PensionProfile, pension_id)
    if pension is None:
        abort(404)

    salary_profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )
    return render_template(
        "retirement/pension_form.html",
        pension=pension,
        pensions=[],
        salary_profiles=salary_profiles,
    )


@retirement_bp.route("/retirement/pension/<int:pension_id>", methods=["POST"])
@login_required
@require_owner
def update_pension(pension_id):
    """Update a pension profile."""
    pension = get_or_404(PensionProfile, pension_id)
    if pension is None:
        abort(404)

    # Context needed for error re-render (same as edit_pension GET).
    salary_profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )

    errors = _pension_update_schema.validate(request.form)
    if errors:
        return render_template(
            "retirement/pension_form.html",
            pension=pension,
            pensions=[],
            salary_profiles=salary_profiles,
            form_data=dict(request.form),
            errors=errors,
        ), 422

    data = _pension_update_schema.load(request.form)
    _require_owned_salary_profile(data)

    # F-17 / Commit 12: schema's @pre_load owns the percent-to-fraction
    # conversion; ``benefit_multiplier`` arrives already as a fraction.

    # Cross-field date validation: merge submitted values with existing
    # pension data so partial updates are validated against the full state.
    eff_hire = data.get("hire_date", pension.hire_date)
    eff_earliest = data.get("earliest_retirement_date", pension.earliest_retirement_date)
    eff_planned = data.get("planned_retirement_date", pension.planned_retirement_date)

    date_errors = _pension_date_errors(eff_hire, eff_earliest, eff_planned)
    if date_errors:
        return render_template(
            "retirement/pension_form.html",
            pension=pension,
            pensions=[],
            salary_profiles=salary_profiles,
            form_data=dict(request.form),
            errors=date_errors,
        ), 422

    for field_name, value in data.items():
        if field_name in _PENSION_FIELDS:
            setattr(pension, field_name, value)

    try:
        db.session.commit()
    except IntegrityError as exc:
        # Name-collision rename (F-105 / C-22): renaming this profile
        # to a name another active pension already holds violates
        # ``uq_pension_profiles_user_name``.  Surface as a 422 with
        # the field-level error rather than crashing the request --
        # the user expects a form-level message, not a 500.
        db.session.rollback()
        if not is_unique_violation(exc, _PENSION_PROFILE_UNIQUE_CONSTRAINT):
            raise
        return render_template(
            "retirement/pension_form.html",
            pension=pension,
            pensions=[],
            salary_profiles=salary_profiles,
            form_data=dict(request.form),
            errors={"name": ["You already have a pension profile with this name."]},
        ), 422
    logger.info("user_id=%d updated pension profile %d", current_user.id, pension_id)
    flash(f"Pension profile '{pension.name}' updated.", "success")
    return redirect(url_for("retirement.dashboard"))


@retirement_bp.route("/retirement/pension/<int:pension_id>/delete", methods=["POST"])
@login_required
@require_owner
def delete_pension(pension_id):
    """Deactivate a pension profile."""
    pension = get_or_404(PensionProfile, pension_id)
    if pension is None:
        abort(404)

    pension.is_active = False
    db.session.commit()
    logger.info("user_id=%d deactivated pension profile %d", current_user.id, pension_id)
    flash(f"Pension profile '{pension.name}' deactivated.", "info")
    return redirect(url_for("retirement.dashboard"))


# ── Readiness Fragment (P3a) ─────────────────────────────────────


@retirement_bp.route("/retirement/readiness")
@login_required
@require_owner
def readiness_fragment():
    """HTMX fragment: readiness verdict with optional what-if overrides.

    Optional ``swr`` / ``return_rate`` / ``merit_raise_horizon_years``
    query parameters recompute the readiness picture as a what-if against
    the stored-settings baseline and return the panel's delta facts
    (funded-ratio delta in points, shortfall delta in dollars); optional
    ``months`` / ``contribution`` additionally recompute the lever
    outcome lines.  All validated through
    :class:`RetirementReadinessQuerySchema` (bounds -> 422 on garbage).
    Renders the minimal ``_readiness.html`` stub P3b restyles.

    **One read pass and one LOADER for the fragment** (plan steps C2-f2d-1 and
    C2-f2d-2), built here and shared by the what-if's two pictures and by the
    levers beside them.  This request published up to three pictures from three
    passes and three loads -- the stored-settings baseline, the override, and
    the lever outcome -- and the panel's whole purpose is to state the DELTA
    between the first two, so every input the halves share had better BE
    shared.  They are; the bare ``date.today()`` reads ledger row **P55** names
    are not yet.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("retirement.dashboard"))

    try:
        query_data = _readiness_query_schema.load(request.args)
    except ValidationError as exc:
        return jsonify(errors=exc.messages), 422

    inputs = retirement_plan.load_retirement_inputs(
        BalanceContext.build(current_user.id),
    )
    point = retirement_plan.PlanPoint(
        swr_override=query_data.get("swr"),
        return_rate_override=query_data.get("return_rate"),
        merit_horizon_override=query_data.get("merit_raise_horizon_years"),
    )
    whatif = retirement_readiness.compute_readiness_whatif(inputs, point)
    lever_data = None
    if (query_data.get("months") is not None
            or query_data.get("contribution") is not None):
        # The levers still solve against the STORED plan, not *point*: the
        # what-if sliders move the hero above them and not this card.  That
        # asymmetry is ledger row **P59** and plan step C2-f2d-4's subject; it
        # is preserved here so this step's numbers are provably unchanged.
        lever_data = retirement_levers.compute_lever_data(
            inputs,
            contribution_override=query_data.get("contribution"),
            months_override=query_data.get("months"),
        )
    return render_template(
        "retirement/_readiness.html",
        readiness=whatif["readiness"],
        baseline=whatif["baseline"],
        deltas=whatif["deltas"],
        levers=lever_data,
    )


# ── Retirement Settings ──────────────────────────────────────────


@retirement_bp.route("/retirement/settings", methods=["POST"])
@login_required
@require_owner
def update_settings():
    """Save retirement assumptions (per-field capable; P3a).

    The assumptions panel posts ONE field per save; a multi-field submit
    validates through the same all-optional schema.  Responses are
    fragment-shaped for the panel: a validation failure renders the
    ``_assumptions.html`` stub with field errors and the echoed input at
    422 (fragment-friendly for both HTMX and plain posts); success
    renders the refreshed panel for an HTMX request and falls back to a
    flash + redirect to the retirement page otherwise.

    Date write-through (acceptance r2 item 2, developer ruling): the date
    row is always editable and Save writes to the RESOLVED owner.  When a
    pension owns the resolved date, a submitted
    ``planned_retirement_date`` updates that owning (max-date) pension --
    enforcing the pension form's own cross-field rules via the shared
    :func:`_pension_date_errors` -- and never the settings column;
    otherwise the settings save applies unchanged.  Writes go through the
    ORM so the audited-table triggers capture them.
    """
    # Preserve original user input for form re-display on error.
    raw_form_data = dict(request.form)

    settings = (
        db.session.query(UserSettings)
        .filter_by(user_id=current_user.id)
        .first()
    )
    # The date row's provenance decides the write-through target AND how
    # the re-rendered rail captions the row.  Resolved per render below
    # -- the success branch must see the POST-save state.
    pensions = (
        db.session.query(PensionProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )

    def rail_response(rail_errors, form_data):
        """Render the assumptions fragment with freshly resolved provenance.

        Returns the body only; the 422 error callers wrap it in
        :func:`designed_error` so the fragment swaps despite the status.
        """
        return render_template(
            "retirement/_assumptions.html",
            settings=settings,
            form_data=form_data,
            errors=rail_errors,
            date_provenance=(
                retirement_dashboard_service
                .resolve_retirement_date_provenance(pensions, settings)
            ),
        )

    # F-17 / Commit 12: percent-to-fraction conversion is owned by the
    # schema's @pre_load (RetirementSettingsSchema._PERCENT_FIELDS); the
    # route forwards the raw form payload and reads back the loaded
    # fractions directly.
    errors = _settings_schema.validate(request.form)
    if errors:
        # Designed fragment: the rail re-rendered with field errors.
        # The marker header opts the 422 back into swapping; replaces
        # the swap shim that lived in retirement_controls.js.
        return designed_error(rail_response(errors, raw_form_data), 422)

    if settings is None:
        flash("Settings not found.", "danger")
        return redirect(url_for("retirement.dashboard"))

    data = _settings_schema.load(request.form)

    provenance = (
        retirement_dashboard_service.resolve_retirement_date_provenance(
            pensions, settings,
        )
    )
    if ("planned_retirement_date" in data
            and provenance["source"] == "pension"):
        # Write through to the owning pension.  The schema already
        # enforced must-be-future (M1); the shared pension rules add
        # after-hire and earliest-date constraints against the OWNER's
        # stored fields, exactly as the pension form would.
        owner = next(
            p for p in pensions if p.id == provenance["pension_id"]
        )
        pension_errors = _pension_date_errors(
            owner.hire_date,
            owner.earliest_retirement_date,
            data["planned_retirement_date"],
        )
        if pension_errors:
            return designed_error(
                rail_response(pension_errors, raw_form_data), 422,
            )
        owner.planned_retirement_date = data.pop("planned_retirement_date")

    for field_name, value in data.items():
        if field_name in _SETTINGS_FIELDS:
            setattr(settings, field_name, value)

    db.session.commit()
    logger.info("user_id=%d updated retirement settings", current_user.id)

    if request.headers.get("HX-Request"):
        return rail_response(None, None)
    flash("Retirement settings updated.", "success")
    return redirect(url_for("retirement.dashboard"))
