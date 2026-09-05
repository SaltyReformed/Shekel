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
from app.services import carry_forward_service
from app.services.balance_at import BalanceContext
from app.exceptions import NotFoundError, ValidationError
from app.utils.auth_helpers import require_owner
from app.utils.dates import display_today
from app.routes.transactions._bp import transactions_bp

logger = logging.getLogger(__name__)


def _resolve_carry_forward_context(period_id):
    """Resolve the calendar, both periods, and the baseline scenario.

    Shared by both ``carry_forward`` (POST mutator) and
    ``carry_forward_preview`` (GET preview) so they apply identical
    ownership and configuration checks.

    Each return is a ``(payload, status, headers)`` tuple where
    *payload* is None when the lookups succeed.  Caller pattern:

        ctx, err = _resolve_carry_forward_context(period_id)
        if err is not None:
            return err
        balance_ctx, source_period, current_period = ctx

    **ONE READ PASS answers everything this render asks about the schedule**
    (pay-calendar plan step C2-f3c, closing ledger row **P68**; plan step
    R7d-c-1 made it a pass where it was a bare calendar).  Both periods, the
    baseline scenario and the recurrence engine's write window come off it,
    and it is handed to ``carry_forward_service`` so the service opens none of
    its own.  The render derived TWO calendars before C2-f3c -- one here and
    one inside the service's ``GenerationSchedule`` -- which was measured on
    the arch fixture at 1 -> 2 derivations and 12 -> 13 queries when plan step
    C2-f3a replaced a SQL reader here.  It also held a
    ``get_baseline_scenario`` beside the derivation, which is the second of
    the two facts a pass pins.

    **Both periods are ANSWERED BY THE CALENDAR, which is what makes the
    ownership check structural.**  The source is a user-supplied id and used to
    be a ``db.session.get`` plus a hand-written ``source.user_id !=
    current_user.id`` comparison; a calendar holds one owner's whole schedule
    and nothing else, so an id that is not in it is not this owner's and the
    404 covers "no such period" and "not yours" with one answer -- the
    project's security response rule expressed as a lookup rather than as a
    guard.  The target is "which paycheck is this owner in now", which
    ``pay_period_service.get_current_period`` answered in SQL with no
    ``ORDER BY`` against the process clock (ledger rows **P19**, **P49**).
    The day is ``display_today()``, the owner's own civil day.

    ``None`` from :meth:`~app.services.pay_calendar.PayCalendar
    .period_containing` keeps its meaning and its 400: no SAVED period covers
    today, so there is nowhere to carry TO.  ``span_containing`` would answer a
    projected period past the horizon whose ``period_id`` is ``None``, and
    every row this operation writes needs one.

    Returns:
        Tuple of ``((balance_ctx, source_period, current_period), None)`` on
        success, or ``(None, error_response)`` on failure.  The error response
        is a Flask-compatible ``(body, status_code)`` tuple that the caller
        returns directly to HTMX.  Both periods are
        :class:`~app.services.pay_calendar.DerivedPeriod` values, and the
        scenario is read off the pass rather than returned beside it.
    """
    balance_ctx = BalanceContext.build(current_user.id)
    calendar = balance_ctx.calendar()

    source_period = calendar.period_by_id(period_id)
    if source_period is None:
        return None, ("Not found", 404)

    current_period = calendar.period_containing(display_today())
    if current_period is None:
        return None, ("No current period found", 400)

    if balance_ctx.scenario is None:
        return None, ("No baseline scenario", 400)

    return (balance_ctx, source_period, current_period), None


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
    balance_ctx, source_period, current_period = ctx

    try:
        preview = carry_forward_service.preview_carry_forward(
            period_id, current_period.period_id, balance_ctx.scenario_id,
            balance_ctx=balance_ctx,
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
    balance_ctx, _source_period, current_period = ctx

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
            period_id, current_period.period_id, balance_ctx.scenario_id,
            balance_ctx=balance_ctx,
        )
        db.session.commit()
    except NotFoundError as exc:
        return str(exc), 404
    except ValidationError as exc:
        # Refused by the envelope branch (a corrupt multi-row target state), or
        # -- since plan step C9b -- by ruling R-C, when a carried loan payment's
        # destination period would place its installment at or before the loan's
        # origination, where the fold would erase it.  Rollback so no source row
        # is left settled and no target row is left bumped (batch atomicity per
        # docs/carry-forward-aftermath-implementation-plan.md).
        db.session.rollback()
        return str(exc), 400

    logger.info(
        "user_id=%d carried forward %d items from period %d", current_user.id, count, period_id
    )
    # Trigger a full grid refresh.
    return "", 200, {"HX-Trigger": "gridRefresh"}
