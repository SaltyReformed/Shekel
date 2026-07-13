"""
Shekel Budget App -- Salary route package: YTD tax checkpoint entry.

The update-from-stub write behind the analytics Taxes tab's YTD checkpoint
card (T-P2).  A user reads the five year-to-date figures (gross plus
federal / state / Social Security / Medicare withholding) off a real pay
stub and POSTs them here; the withholding-to-date producer then anchors
the refund estimate on the measured figures and models only the remaining
periods.

Blueprint choice (salary, not analytics): the checkpoint row lives in the
``salary`` schema and is scoped to a salary profile, and every salary-schema
write in the app already lives in the salary blueprint (profiles, raises,
deductions, calibration, tax config) -- the analytics blueprint owns zero
mutation routes.  The established domain-write convention therefore places
this write in the salary blueprint even though the card it renders is an
analytics surface (the response template lives under ``templates/analytics``,
where the Taxes tab (T-P4) will place it).
"""

import logging
from datetime import datetime, timezone

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.dates import to_display_date
from app.utils.error_fragments import designed_error
from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.services import tax_withholding_service
from app.services.tax_withholding_service import CheckpointFigures
from app.routes.salary._bp import salary_bp
from app.routes.salary._helpers import _ytd_checkpoint_schema

logger = logging.getLogger(__name__)


@salary_bp.route("/salary/<int:profile_id>/checkpoint", methods=["POST"])
@login_required
@require_owner
def save_ytd_checkpoint(profile_id):
    """Upsert a YTD tax checkpoint for a salary profile from a pay stub.

    Ownership: the profile must belong to the current user; a missing or
    cross-user id 404s (the project's "404 for both 'not found' and 'not
    yours'" rule).  The posted figures are validated by
    :class:`~app.schemas.validation.salary.YtdTaxCheckpointSchema`
    (non-negative, each withholding line ``<= ytd_gross``, ``as_of_date``
    not in the future) before persistence; the row itself carries the same
    invariants as DB CHECKs.

    Responses follow the salary blueprint's mutation convention:

    * HTMX request -> the ``_tax_checkpoint_card`` partial (200 on save,
      422 on a validation failure so the card shows the field errors, 500
      with a banner on a DB-tier failure);
    * full-page (non-HTMX) request -> a flash plus redirect to the
      analytics page (the card's future host), matching how the calibration
      and line-item handlers redirect on a full-page post.
    """
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    is_htmx = bool(request.headers.get("HX-Request"))

    errors = _ytd_checkpoint_schema.validate(request.form)
    if errors:
        if is_htmx:
            # Designed fragment: the card re-rendered with field errors.
            # The marker header opts the 422 back into swapping (the
            # app-wide htmx config drops 4xx bodies); replaces the
            # retired tax_checkpoint.js per-surface shim.
            return designed_error(_render_checkpoint_card(
                profile, errors=errors, form_values=request.form,
            ), 422)
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("analytics.page"))

    data = _ytd_checkpoint_schema.load(request.form)
    figures = CheckpointFigures(
        as_of_date=data["as_of_date"],
        ytd_gross=data["ytd_gross"],
        ytd_federal=data["ytd_federal"],
        ytd_state=data["ytd_state"],
        ytd_social_security=data["ytd_social_security"],
        ytd_medicare=data["ytd_medicare"],
        notes=data.get("notes"),
    )

    # Capture the requester id on the clean session up front; the failure
    # path logs after a rollback where reading the expired current_user
    # attribute would hit the rolled-back session (the calibration handler's
    # pattern).
    user_id = current_user.id
    try:
        checkpoint = tax_withholding_service.save_checkpoint(profile.id, figures)
        db.session.commit()
    except SQLAlchemyError:
        # Narrow catch (C-46 / F-145): the schema already rejects every
        # realistic client error (negative, component > gross, future or
        # garbage date), so a DB-tier failure here is an OperationalError
        # or an out-of-range DataError, not a validation miss.  Roll back,
        # log with context, and surface a recoverable failure.
        db.session.rollback()
        logger.exception(
            "user_id=%d failed to save YTD tax checkpoint for profile %d",
            user_id, profile_id,
        )
        if is_htmx:
            # Designed fragment: the card with a save-failure banner.
            # Before the marker convention this 500 body was dead UI --
            # the client could not distinguish it from an unhandled
            # crash page, so it was never swapped and the failure was
            # silent (flagged in the S5 as-built).
            return designed_error(_render_checkpoint_card(
                profile,
                save_error="Failed to save checkpoint. Please try again.",
                form_values=request.form,
            ), 500)
        flash("Failed to save checkpoint. Please try again.", "danger")
        return redirect(url_for("analytics.page"))

    logger.info(
        "user_id=%d saved YTD tax checkpoint %d for profile %d (as_of=%s)",
        current_user.id, checkpoint.id, profile_id,
        checkpoint.as_of_date.isoformat(),
    )
    if is_htmx:
        return _render_checkpoint_card(profile, saved=checkpoint)
    flash("YTD tax checkpoint saved.", "success")
    return redirect(url_for("analytics.page"))


def _render_checkpoint_card(
    profile, *, saved=None, errors=None, save_error=None, form_values=None,
):
    """Render the YTD checkpoint card partial for a profile.

    The card shows the latest checkpoint for a single tax year plus the
    entry form.  On a successful save the card is scoped to the saved
    checkpoint's year and shows the profile's latest checkpoint in that
    year (the just-saved row, unless a later-dated one exists).  On the
    error / initial path it is scoped to the display-timezone current year
    (the analytics tab's default) and repopulates the form from the
    rejected submission.

    Args:
        profile: The owned :class:`~app.models.salary_profile.SalaryProfile`.
        saved: The just-saved checkpoint on the success path, or ``None``.
        errors: The Marshmallow field-error dict on a validation failure,
            or ``None``.
        save_error: A single banner message on a DB-tier save failure, or
            ``None``.
        form_values: The submitted form values to repopulate on an error
            path (a mapping), or ``None`` (a cleared form after success).

    Returns:
        The rendered ``analytics/_tax_checkpoint_card.html`` partial.
    """
    if saved is not None:
        year = saved.as_of_date.year
    else:
        year = to_display_date(datetime.now(timezone.utc)).year
    checkpoint = tax_withholding_service.latest_checkpoint(profile.id, year)

    return render_template(
        "analytics/_tax_checkpoint_card.html",
        profile=profile,
        year=year,
        checkpoint=checkpoint,
        errors=errors or {},
        save_error=save_error,
        form_values=form_values or {},
    )
