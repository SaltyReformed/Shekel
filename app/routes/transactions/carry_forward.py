"""
Shekel Budget App -- Transaction route package: carry-forward handlers.

The carry-forward preview (GET, read-only plan) and the carry-forward
mutator (POST), which copy a past period's unpaid items into the current
period.  Both apply identical ownership / configuration checks via the
shared :func:`_resolve_carry_forward_context`.
"""

import logging

from flask import render_template
from flask_login import current_user, login_required

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.services import carry_forward_service, pay_period_service
from app.services.scenario_resolver import get_baseline_scenario
from app.exceptions import NotFoundError, ValidationError
from app.utils.auth_helpers import require_owner
from app.routes.transactions._bp import transactions_bp

logger = logging.getLogger(__name__)


def _resolve_carry_forward_context(period_id):
    """Resolve source period, current period, and baseline scenario.

    Shared by both ``carry_forward`` (POST mutator) and
    ``carry_forward_preview`` (GET preview) so they apply identical
    ownership and configuration checks.

    Each return is a ``(payload, status, headers)`` tuple where
    *payload* is None when the lookups succeed.  Caller pattern:

        ctx, err = _resolve_carry_forward_context(period_id)
        if err is not None:
            return err
        source_period, current_period, scenario = ctx

    Returns:
        Tuple of ``((source_period, current_period, scenario), None)``
        on success, or ``(None, error_response)`` on failure.  The
        error response is a Flask-compatible ``(body, status_code)``
        tuple that the caller returns directly to HTMX.
    """
    source_period = db.session.get(PayPeriod, period_id)
    if source_period is None or source_period.user_id != current_user.id:
        return None, ("Not found", 404)

    current_period = pay_period_service.get_current_period(current_user.id)
    if current_period is None:
        return None, ("No current period found", 400)

    scenario = get_baseline_scenario(current_user.id)
    if not scenario:
        return None, ("No baseline scenario", 400)

    return (source_period, current_period, scenario), None


@transactions_bp.route(
    "/pay-periods/<int:period_id>/carry-forward-preview", methods=["GET"],
)
@login_required
@require_owner
def carry_forward_preview(period_id: int):
    """HTMX partial: return the carry-forward preview modal.

    Mirrors the POST ``carry_forward`` route's ownership/configuration
    checks, then asks the service for a read-only plan and renders the
    Bootstrap 5 modal partial.  No database writes happen here -- the
    user sees what WOULD happen and confirms via the modal's button,
    which posts to the existing ``carry_forward`` endpoint.

    Returns 404 for "period not found" and "period not yours" (security
    response rule), 400 for missing pay-period configuration (no
    current period, no baseline scenario), 200 with the rendered
    modal HTML for the success case.

    Args:
        period_id: pay_period.id of the source period (the past
            period the user clicked Carry Fwd on).

    Returns:
        Flask response tuple: rendered modal HTML or an error message
        with the appropriate status code.
    """
    ctx, err = _resolve_carry_forward_context(period_id)
    if err is not None:
        return err
    source_period, current_period, scenario = ctx

    try:
        preview = carry_forward_service.preview_carry_forward(
            period_id, current_period.id, current_user.id, scenario.id,
        )
    except NotFoundError as exc:
        return str(exc), 404

    return render_template(
        "grid/_carry_forward_preview_modal.html",
        preview=preview,
        source_period=source_period,
        current_period=current_period,
    )


@transactions_bp.route("/pay-periods/<int:period_id>/carry-forward", methods=["POST"])
@login_required
@require_owner
def carry_forward(period_id):
    """Carry forward all unpaid items from a period to the current period."""
    ctx, err = _resolve_carry_forward_context(period_id)
    if err is not None:
        return err
    _source_period, current_period, scenario = ctx

    # Pylint: ``duplicate-code`` -- the commit + ``NotFoundError`` -> 404 /
    # ``ValidationError`` -> rollback -> 400 translation below is generic
    # Flask error-handling boilerplate that also appears in
    # ``mutations.unmark_credit`` (and ~3 other route files), but the two
    # routes are unrelated -- a period-batch carry-forward vs a single-row
    # credit unmark -- and differ in everything around it (StaleData
    # handling, the ``count`` return value, the success body), so a shared
    # wrapper would over-couple them (coding-standards rule 13).  One-sided
    # ``duplicate-code`` disable (see plan.md Phase 3 split-trap notes);
    # ``mutations.unmark_credit`` stays un-disabled.
    # pylint: disable=duplicate-code
    try:
        count = carry_forward_service.carry_forward_unpaid(
            period_id, current_period.id, current_user.id, scenario.id
        )
        db.session.commit()
    except NotFoundError as exc:
        return str(exc), 404
    except ValidationError as exc:
        # Envelope branch refused -- e.g. settled target canonical,
        # template inactive in target period, or a corrupt multi-row
        # target state.  Rollback so no source row is left settled
        # and no target row is left bumped (batch atomicity per
        # docs/carry-forward-aftermath-implementation-plan.md).
        db.session.rollback()
        return str(exc), 400

    logger.info(
        "user_id=%d carried forward %d items from period %d", current_user.id, count, period_id
    )
    # Trigger a full grid refresh.
    return "", 200, {"HX-Trigger": "gridRefresh"}
