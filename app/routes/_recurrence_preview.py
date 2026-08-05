"""
Shekel Budget App -- Recurrence occurrence-preview helpers

The three helpers behind ``templates.preview_recurrence``, the read-only HTMX
fragment that shows the next five pay periods a recurrence would land in.  The
endpoint is deliberately kind-agnostic -- both the transaction-template form
and the transfer-template form point their live preview at it
(``_recurrence_fields.html``) -- so its helpers do not belong inside the
transaction-template CRUD module they used to live in.

The preview reads request args and never writes: it resolves a TRANSIENT rule
through the same authoring seam a save goes through
(:func:`app.services.recurrence.build_transient_rule`), so what the user is
shown is what saving would produce.  Before plan step R2c it built the rule by
hand and derived the ``Every N Periods`` phase inline, which is exactly the
kind of second copy of a derivation the seam exists to remove.

Route-layer module rather than service because these read ``request`` and
``current_user``; the leading underscore marks it route-internal.
"""
from datetime import date

from flask import request
from flask_login import current_user
from markupsafe import Markup

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern
from app.services.recurrence import (
    PeriodCalendar,
    RecurrenceSpec,
    build_transient_rule,
)

#: How many upcoming occurrences the fragment lists.
PREVIEW_OCCURRENCE_LIMIT = 5


def owned_preview_start_period() -> PayPeriod | None:
    """Return the submitted start period when this user owns it, else ``None``.

    Ownership check: another user's period is rejected rather than used, so
    the preview cannot disclose someone else's pay-period structure (audit
    finding H3).  Falling through to ``None`` leaves the preview on this
    user's own schedule.

    Returns:
        The owned :class:`~app.models.pay_period.PayPeriod`, or ``None`` when
        none was submitted or it is not this user's.
    """
    start_period_id = request.args.get("start_period_id", type=int)
    if start_period_id is None:
        return None
    start_period = db.session.get(PayPeriod, start_period_id)
    if start_period is None or start_period.user_id != current_user.id:
        return None
    return start_period


def build_preview_rule(
    pattern: RecurrencePattern,
    start_period: PayPeriod | None,
    calendar: PeriodCalendar,
) -> RecurrenceRule:
    """Build an unsaved, fully resolved rule from the preview request args.

    Goes through the authoring seam like every other writer, but via
    :func:`~app.services.recurrence.build_transient_rule`, which resolves
    without touching the session -- so the previewed rule carries the columns
    the saved one would, including the ``Every N Periods`` phase this route
    used to derive for itself.

    Args:
        pattern: The ``RecurrencePattern`` row being previewed.
        start_period: The owner-checked start period, or ``None``.
        calendar: The user's pay-period schedule.

    Returns:
        The transient :class:`~app.models.recurrence_rule.RecurrenceRule`,
        with ``pattern`` attached for the matcher.
    """
    end_date_str = request.args.get("end_date")
    rule = build_transient_rule(
        RecurrenceSpec(
            user_id=current_user.id,
            pattern_id=pattern.id,
            interval_n=request.args.get("interval_n", type=int, default=1),
            day_of_month=request.args.get("day_of_month", type=int),
            month_of_year=request.args.get("month_of_year", type=int),
            start_period_id=start_period.id if start_period else None,
            end_date=date.fromisoformat(end_date_str) if end_date_str else None,
        ),
        calendar,
    )
    # Attach the pattern relationship manually: the rule is never added to
    # the session, so the relationship would not load.
    rule.pattern = pattern
    return rule


def render_preview_html(preview_periods: list[PayPeriod]) -> Markup:
    """Render the occurrence-preview HTML fragment for *preview_periods*.

    Args:
        preview_periods: The matched
            :class:`~app.models.pay_period.PayPeriod` rows to list.

    Returns:
        The fragment markup.
    """
    items = "".join(
        f"<li>{p.start_date.strftime('%b %d, %Y')} - {p.end_date.strftime('%b %d, %Y')}</li>"
        for p in preview_periods
    )
    html = (
        f"<small class='text-muted'>Next {len(preview_periods)} occurrences:</small>"
        f"<ul class='list-unstyled mb-0 ms-2'><small>{items}</small></ul>"
    )
    return Markup(html)


__all__ = [
    "PREVIEW_OCCURRENCE_LIMIT",
    "build_preview_rule",
    "owned_preview_start_period",
    "render_preview_html",
]
