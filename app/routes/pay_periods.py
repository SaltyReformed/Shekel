"""
Shekel Budget App -- Pay Period Routes

Generates the biweekly schedule and manages its lifecycle: extend the
schedule forward, truncate the tail, and regenerate a wrong future tail.
All management actions are full-page POST + redirect (or a 422 re-render
of the settings dashboard when a discard needs confirming); they live on
the settings "pay-periods" section.
"""

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.utils.auth_helpers import require_owner

from app.extensions import db
from app.exceptions import (
    PayPeriodDiscardRequired,
    PayPeriodLocked,
    PayPeriodResetBlocked,
    PayPeriodUnresolved,
    ValidationError,
)
from app.routes.settings import render_settings_dashboard
from app.schemas.validation import (
    PayPeriodExtendSchema,
    PayPeriodGenerateSchema,
    PayPeriodRegenerateSchema,
    PayPeriodResetSchema,
    PayPeriodTruncateSchema,
    PayScheduleSchema,
)
from app.services import pay_period_admin, pay_period_write, pay_schedule_service

logger = logging.getLogger(__name__)

pay_periods_bp = Blueprint("pay_periods", __name__)

_generate_schema = PayPeriodGenerateSchema()
_extend_schema = PayPeriodExtendSchema()
_truncate_schema = PayPeriodTruncateSchema()
_regenerate_schema = PayPeriodRegenerateSchema()
_reset_schema = PayPeriodResetSchema()
_schedule_schema = PayScheduleSchema()


def _pay_periods_redirect():
    """Redirect back to the settings pay-periods section."""
    return redirect(url_for("settings.show", section="pay-periods"))


def _summarize_errors(errors):
    """Flatten a Marshmallow error dict into one flash-able sentence."""
    parts = [
        f"{field}: {'; '.join(str(m) for m in messages)}"
        for field, messages in errors.items()
    ]
    return "Please correct the form: " + " | ".join(parts)


@pay_periods_bp.route("/pay-periods/generate", methods=["GET"])
@login_required
@require_owner
def generate_form():
    """Redirect to settings dashboard pay periods section."""
    return redirect(url_for("settings.show", section="pay-periods"))


@pay_periods_bp.route("/pay-periods/generate", methods=["POST"])
@login_required
@require_owner
def generate():
    """Generate pay periods from the submitted form data."""
    errors = _generate_schema.validate(request.form)
    if errors:
        return render_template("pay_periods/generate.html", errors=errors), 422

    data = _generate_schema.load(request.form)

    try:
        # One call, because recording the paydays and capturing the cadence
        # extend / the rolling top-up continue from is ONE operation -- the
        # pair was two lines here and would have been two more in
        # ``auth_service.register_user`` at plan step X-ad-a.  Plan step C3-b
        # folded the pair INTO the writer as the cadence rule, so
        # ``establish_schedule`` had nothing left to compose.
        periods = pay_period_write.record_paydays(
            user_id=current_user.id,
            first_payday=data["start_date"],
            num_periods=data["num_periods"],
            cadence_days=data["cadence_days"],
        )
    except ValidationError as exc:
        # Forward-only rule (ruling R-PC1, plan step C3-b): a payday that would
        # land BETWEEN two existing ones is rejected.  Surfaced on the
        # start_date field, mirroring the schema 422 -- and that attribution
        # is PROVABLE rather than assumed.  ``record_paydays`` refuses for
        # three reasons, and ``PayPeriodGenerateSchema`` bounds the cadence and
        # the batch size to exactly the ranges the writer and the schedule
        # column accept (``_CADENCE_DAYS_RANGE`` takes the TIGHTER of the two
        # floors, which is the writer's), so those two cannot reach here and
        # the date one is what is left.  Widen either field and this line
        # starts rendering a cadence message under the date box.
        #
        # The rollback is what makes the 422 clean.  ``record_paydays`` now
        # runs every refusal BEFORE its first durable statement, so there is
        # nothing staged to discard on this path -- but the response below
        # re-renders a form, and a rendered response should not sit on a unit
        # of work whose emptiness depends on reading the writer.
        db.session.rollback()
        return render_template(
            "pay_periods/generate.html",
            errors={"start_date": [str(exc)]},
        ), 422
    db.session.commit()

    flash(f"Generated {len(periods)} pay periods.", "success")
    return redirect(url_for("grid.index"))


@pay_periods_bp.route("/pay-periods/extend", methods=["POST"])
@login_required
@require_owner
def extend():
    """Append pay periods to the end of the schedule."""
    errors = _extend_schema.validate(request.form)
    if errors:
        flash(_summarize_errors(errors), "danger")
        return _pay_periods_redirect()

    data = _extend_schema.load(request.form)
    try:
        new_periods = pay_period_admin.extend_pay_periods(
            current_user.id, data["num_periods"],
        )
    except ValidationError as exc:
        # Rolled back before the redirect: ``extend_pay_periods`` takes the
        # per-user advisory lock and may have flushed the repopulation pass
        # before a later statement refused, and the page this redirects to
        # reads the owner's schedule back.
        db.session.rollback()
        flash(str(exc), "danger")
        return _pay_periods_redirect()

    db.session.commit()
    flash(f"Added {len(new_periods)} pay periods.", "success")
    return _pay_periods_redirect()


@pay_periods_bp.route("/pay-periods/truncate", methods=["POST"])
@login_required
@require_owner
def truncate():
    """Delete the schedule tail beyond the chosen period."""
    errors = _truncate_schema.validate(request.form)
    if errors:
        flash(_summarize_errors(errors), "danger")
        return _pay_periods_redirect()

    data = _truncate_schema.load(request.form)
    try:
        deleted = pay_period_admin.truncate_pay_periods(
            current_user.id, data["keep_through_period_id"],
            confirm_discard=data["confirm_discard"],
        )
    except (
        PayPeriodLocked, PayPeriodUnresolved, ValidationError,
    ) as exc:
        # ``PayPeriodUnresolved`` is the service refusing an id that names no
        # pay period of this user's (plan step C3-a): a forged one, another
        # owner's, or a STALE one -- the confirm panel below re-submits the id
        # the user reviewed, and a concurrent truncate can delete that period
        # between the two posts.  **Its own class rather than the generic
        # ``ValidationError``**, which an adversarial review of this step
        # asked for: a catch on the base would flash "choose the period again"
        # for any future business-rule refusal raised anywhere below this
        # call, reporting a real defect as advice about a dropdown.
        #
        # It flashes rather than 404ing because every sibling action on this
        # settings form does, and the security property that matters is
        # intact: "not yours" and "does not exist" carry the same message, so
        # this door is not an existence oracle.  Which case it was is recorded
        # in the ACCESS log instead (``_log_unresolved_period``).
        #
        # ``ValidationError`` is new to this door at plan step C3-b and was
        # an unhandled 500 until an adversarial review found it: the writer now
        # reads the stored cadence to re-project the surviving last period, and
        # ``budget.pay_schedule.cadence_days`` accepts 1 while no stored
        # ``end_date`` can express a one-day period.  Only legacy data holds
        # such a value, and a schedule button is the right place for it to be a
        # message.
        #
        # All three refuse BEFORE the ``DELETE``, so nothing durable is staged;
        # the rollback is for the page this redirects to, which reads the
        # owner's schedule back and should read committed state.
        db.session.rollback()
        flash(str(exc), "danger")
        return _pay_periods_redirect()
    except PayPeriodDiscardRequired as exc:
        db.session.rollback()
        return render_settings_dashboard("pay-periods", extra={"pp_confirm": {
            "op": "truncate",
            "count": exc.count,
            "params": {
                "keep_through_period_id": data["keep_through_period_id"],
            },
        }}, status=422)

    db.session.commit()
    flash(f"Removed {deleted} pay period(s).", "success")
    return _pay_periods_redirect()


@pay_periods_bp.route("/pay-periods/regenerate", methods=["POST"])
@login_required
@require_owner
def regenerate():
    """Rebuild the not-yet-started future tail from a corrected start."""
    errors = _regenerate_schema.validate(request.form)
    if errors:
        flash(_summarize_errors(errors), "danger")
        return _pay_periods_redirect()

    data = _regenerate_schema.load(request.form)
    try:
        new_periods = pay_period_admin.regenerate_pay_periods(
            current_user.id, data["new_start_date"], data["num_periods"],
            data["cadence_days"], confirm_discard=data["confirm_discard"],
        )
    except (PayPeriodLocked, ValidationError) as exc:
        # Rolled back for the reason the generate route states, and here it is
        # not a nicety: ``regenerate_pay_periods`` DELETES the rebuildable tail
        # before the writer validates the new start, so a refusal raised after
        # that leaves the delete in the session.  Its own docstring promised
        # "the route's rollback undoes the truncate too" and no route made the
        # call -- an adversarial review of plan step C3-b found the gap.
        db.session.rollback()
        flash(str(exc), "danger")
        return _pay_periods_redirect()
    except PayPeriodDiscardRequired as exc:
        # The discard gate raises BEFORE the delete, so nothing is staged --
        # but this response re-renders the settings dashboard, which reads the
        # owner's periods.  Rolling back first means it reads committed state
        # rather than a session the service may have flushed into.
        db.session.rollback()
        return render_settings_dashboard("pay-periods", extra={"pp_confirm": {
            "op": "regenerate",
            "count": exc.count,
            "params": {
                "new_start_date": data["new_start_date"].isoformat(),
                "num_periods": data["num_periods"],
                "cadence_days": data["cadence_days"],
            },
        }}, status=422)

    db.session.commit()
    flash(f"Rebuilt the schedule: {len(new_periods)} new period(s).", "success")
    return _pay_periods_redirect()


@pay_periods_bp.route("/pay-periods/reset", methods=["POST"])
@login_required
@require_owner
def reset():
    """Wipe and rebuild the entire schedule (first-time-setup correction).

    Refuses unless the user explicitly confirmed and -- enforced by the
    service -- has no settled transactions.  The whole rebuild runs in one
    transaction this route commits; a service-side failure (the settled
    refusal, or an invalid start/cadence after the wipe) rolls back so
    nothing partial ships.
    """
    errors = _reset_schema.validate(request.form)
    if errors:
        flash(_summarize_errors(errors), "danger")
        return _pay_periods_redirect()

    data = _reset_schema.load(request.form)
    if not data["confirm"]:
        flash(
            "Confirm the reset to rebuild your entire schedule.", "danger",
        )
        return _pay_periods_redirect()

    try:
        new_periods = pay_period_admin.reset_pay_periods(
            current_user.id, data["new_start_date"], data["num_periods"],
            data["cadence_days"],
        )
    except (PayPeriodResetBlocked, ValidationError) as exc:
        # ``reset_pay_periods`` wipes every period before it generates the new
        # schedule, so a refusal raised at the second half leaves the wipe in
        # the session.  The settled-transaction refusal happens before any of
        # it; the rollback is for the other one.
        db.session.rollback()
        flash(str(exc), "danger")
        return _pay_periods_redirect()

    db.session.commit()
    flash(f"Reset your schedule: {len(new_periods)} new period(s).", "success")
    return _pay_periods_redirect()


@pay_periods_bp.route("/pay-periods/schedule", methods=["POST"])
@login_required
@require_owner
def schedule():
    """Save the continuous-rolling-window configuration."""
    errors = _schedule_schema.validate(request.form)
    if errors:
        flash(_summarize_errors(errors), "danger")
        return _pay_periods_redirect()

    data = _schedule_schema.load(request.form)
    try:
        pay_schedule_service.set_rolling(
            current_user.id,
            enabled=data["rolling_enabled"],
            target_periods=data["rolling_target_periods"],
        )
    except ValidationError as exc:
        flash(str(exc), "danger")
        return _pay_periods_redirect()

    db.session.commit()
    flash("Rolling-window settings saved.", "success")
    return _pay_periods_redirect()
