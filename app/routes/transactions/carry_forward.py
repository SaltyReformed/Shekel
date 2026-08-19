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
from app.services import carry_forward_service
from app.services.pay_calendar import calendar_for
from app.services.scenario_resolver import get_baseline_scenario
from app.exceptions import NotFoundError, ValidationError
from app.utils.auth_helpers import require_owner
from app.utils.dates import display_today
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

    **The TARGET period is derived and the SOURCE stays an ORM row**, and the
    asymmetry is the two questions (plan step C2-f3a).  The source is a
    user-supplied id that has to be resolved and OWNERSHIP-CHECKED against the
    row -- a 404 for both "no such period" and "not yours", which is the
    project's security response rule -- while the target is "which paycheck is
    this owner in now", a calendar question that
    ``pay_period_service.get_current_period`` answered in SQL with no
    ``ORDER BY`` against the process clock (ledger rows **P19**, **P49**).
    The day is ``display_today()``, the owner's own civil day.

    ``None`` from :meth:`~app.services.pay_calendar.PayCalendar
    .period_containing` keeps its meaning and its 400: no SAVED period covers
    today, so there is nowhere to carry TO.  ``span_containing`` would answer a
    projected period past the horizon whose ``period_id`` is ``None``, and
    every row this operation writes needs one.

    **This render now derives the pay calendar TWICE, and that is a MEASURED
    +1 this step introduced** (ledger row **P68**, owned by plan step
    **C2-f3c**).  The retired reader was SQL and derived nothing, so the one
    derivation on this render belonged to ``carry_forward_service``, which
    builds a ``GenerationSchedule`` for the target period; the resolve above is
    a second.  Measured on the arch fixture at 1 -> 2 derivations and 12 -> 13
    queries.  It is not left for someone to find: C2-f3c reshapes
    ``GenerationSchedule`` to hold ONE read and to take a calendar rather than
    load one, at which point this route threads the calendar it already has and
    the render is back to one.  Deriving HERE is the direction that fix goes --
    the route is the door -- so the duplicate is a transient of the sequence
    rather than a producer being added below the route.

    Returns:
        Tuple of ``((source_period, current_period, scenario), None)``
        on success, or ``(None, error_response)`` on failure.  The
        error response is a Flask-compatible ``(body, status_code)``
        tuple that the caller returns directly to HTMX.  *current_period* is
        a :class:`~app.services.pay_calendar.DerivedPeriod`; *source_period*
        is the ORM row.
    """
    source_period = db.session.get(PayPeriod, period_id)
    if source_period is None or source_period.user_id != current_user.id:
        return None, ("Not found", 404)

    current_period = calendar_for(current_user.id).period_containing(
        display_today(),
    )
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
            period_id, current_period.period_id, current_user.id, scenario.id,
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
            period_id, current_period.period_id, current_user.id, scenario.id
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
