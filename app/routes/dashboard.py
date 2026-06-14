"""
Shekel Budget App -- Dashboard Routes

The Terminal Road summary dashboard (Loop B): one read-only pulse region
(canvas + street + due-soon list) refreshing on ``balanceChanged``, plus
a page-load-only position-tracks tier.  The dashboard is read-only for
transaction status; all settlement flows through ``transactions.mark_done``
(the canonical settlement endpoint).  The only mutation reachable here is
the anchor true-up (the click-to-edit balance), whose Cancel / Escape
revert target is :func:`balance_section`.

Route-layer serialization lives here, NOT in the producer
(``dashboard_pulse_service``, which is Flask-free and money-precise):
``float`` exists only at this Chart.js boundary -- the projected
end-balance series and threshold are serialized to a JSON string for the
``data-chart`` attribute, and the debt track's principal-paid fraction is
scaled to a 0-100 percent float for the rail marker.
"""

import json

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.services import (
    dashboard_pulse_service,
    dashboard_service,
    pay_period_admin,
)
from app.services.account_resolver import resolve_grid_account
from app.utils.auth_helpers import require_owner

dashboard_bp = Blueprint("dashboard", __name__)

# Chart.js x-axis label format: month abbreviation + un-padded day (e.g.
# "Jun 5").  The labels are the charted periods' end dates.
_CHART_LABEL_FORMAT = "%b %-d"
# Scale a 0-1 principal-paid fraction to a 0-100 percent for the rail.
_PERCENT_SCALE = 100


def _serialize_chart(chart: dict) -> str:
    """Serialize the pulse chart series + threshold to a JSON string.

    The single Chart.js serialization boundary (coding-standards: floats
    live only here, never in a calculation).  Maps the producer's
    ``points`` (``{end_date, balance}`` dicts in ``Decimal``) to parallel
    ``labels`` / ``values`` arrays and the ``low_balance_threshold``
    (``Decimal`` or ``None``) to a ``float`` or ``null``.  The first
    ``values`` entry coincides with the hero figure by construction (the
    producer's reservation-semantics identity), so the chart opens on the
    same number the hero shows.

    Args:
        chart: The producer's ``pulse["chart"]`` dict, with keys
            ``points`` and ``low_balance_threshold``.

    Returns:
        A JSON string ``{"labels": [str], "values": [float],
        "threshold": float | null}`` for the ``data-chart`` attribute.
    """
    threshold = chart["low_balance_threshold"]
    return json.dumps({
        "labels": [
            point["end_date"].strftime(_CHART_LABEL_FORMAT)
            for point in chart["points"]
        ],
        "values": [float(point["balance"]) for point in chart["points"]],
        "threshold": float(threshold) if threshold is not None else None,
    })


def _serialize_tracks(tracks: dict) -> dict:
    """Add the route-layer ``principal_paid_pct`` to the debt track.

    The producer hands the debt track an honest principal-paid FRACTION
    (``Decimal`` in [0, 1], or ``None`` when the user has no loans); the
    rail marker positions from a 0-100 PERCENT.  Scaling and the
    ``Decimal -> float`` cast are presentation, so they live here at the
    serialization boundary, not in the Flask-free producer.  ``None``
    flows through unchanged (the rail then renders without a marker).

    Args:
        tracks: The ``compute_tracks_section`` dict (``goals`` list +
            ``debt`` dict or ``None``).

    Returns:
        The same dict with ``debt.principal_paid_pct`` added when a debt
        track exists.  Returned for call-site readability; the ``debt``
        sub-dict is mutated in place (it is a fresh ``dict`` the producer
        copied, so no shared state is touched).
    """
    debt = tracks["debt"]
    if debt is not None:
        fraction = debt["principal_paid_fraction"]
        debt["principal_paid_pct"] = (
            float(fraction) * _PERCENT_SCALE if fraction is not None else None
        )
    return tracks


def _serialize_pulse(pulse: dict | None) -> dict | None:
    """Add the route-layer ``chart_json`` to the pulse region, or pass None.

    The pulse producer returns ``None`` for the degraded states (no
    account / scenario / current period); that ``None`` is propagated
    unchanged so each consumer renders its own no-period fallback (the
    page's ``{% if pulse %}`` else-branch and ``pulse_section``'s explicit
    ``_no_period.html`` render -- ``_pulse.html`` itself assumes a
    populated pulse).  Otherwise the chart series is serialized to
    ``pulse["chart_json"]`` (the ``data-chart`` attribute the template
    reads).

    Args:
        pulse: The ``compute_pulse_section`` dict, or ``None``.

    Returns:
        The pulse dict with ``chart_json`` added, or ``None`` unchanged.
    """
    if pulse is None:
        return None
    pulse["chart_json"] = _serialize_chart(pulse["chart"])
    return pulse


@dashboard_bp.route("/")
@dashboard_bp.route("/dashboard")
@login_required
@require_owner
def page():
    """Render the Terminal Road dashboard: pulse region plus position tracks.

    ``has_account`` carries the account-resolution truth (the old
    ``has_default_account`` flag): the dashboard projects the user's
    default account, so with no resolvable account the page renders the
    neutral "Set up an account" empty state instead.  When an account
    exists but no period contains today, the pulse producer returns
    ``None`` and the page renders the "No pay period covers today"
    generate-periods CTA; the position tracks still render.

    Route-layer serialization (the Chart.js / rail boundary) is applied
    here: the pulse chart series to a JSON string and the debt track's
    principal-paid fraction to a percent.
    """
    # Continuous rolling window: top up on dashboard entry (a future-
    # period consumer).  A no-op (one count, no lock) when rolling is
    # disabled; commits only when periods were actually created.
    if pay_period_admin.top_up_rolling_window(current_user.id):
        db.session.commit()

    has_account = resolve_grid_account(
        current_user.id, current_user.settings,
    ) is not None

    pulse = _serialize_pulse(
        dashboard_pulse_service.compute_pulse_section(current_user.id)
    )
    tracks = _serialize_tracks(
        dashboard_pulse_service.compute_tracks_section(current_user.id)
    )

    return render_template(
        "dashboard/dashboard.html",
        has_account=has_account,
        pulse=pulse,
        tracks=tracks,
    )


@dashboard_bp.route("/dashboard/pulse")
@login_required
@require_owner
def pulse_section():
    """HTMX partial: re-render the pulse region on ``balanceChanged``.

    The single ``balanceChanged from:body`` swap target for the canvas +
    street + due-soon list.  Computes only the pulse region (not the
    page-load-only tracks) and applies the same chart serialization the
    page does, so the swapped-in markup reads the identical
    ``data-chart`` contract.

    When the producer returns ``None`` (the schedule lapsed between page
    load and a ``balanceChanged`` refresh, so no period covers today), the
    swap target renders the same "No pay period covers today" CTA the page
    shows -- ``_pulse.html`` assumes a populated pulse and would raise on a
    missing hero, so the ``None`` branch routes to ``_no_period.html``
    instead.

    Non-HTMX requests redirect to the dashboard page (the section is a
    fragment, not a standalone page), matching the old section
    endpoints' behavior.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("dashboard.page"))

    pulse = _serialize_pulse(
        dashboard_pulse_service.compute_pulse_section(current_user.id)
    )
    if pulse is None:
        return render_template("dashboard/_no_period.html")
    return render_template("dashboard/_pulse.html", pulse=pulse)


@dashboard_bp.route("/dashboard/balance")
@login_required
@require_owner
def balance_section():
    """HTMX partial: re-render the hero balance (the anchor-edit revert target).

    The anchor editor opened from the dashboard balance control carries
    ``?revert=dashboard``; Cancel / Escape and the 409-conflict retry path
    revert through ``accounts._anchor_revert_url``, which maps
    ``dashboard`` to THIS endpoint.  So it must render ``_pulse_balance.html``
    -- the ``#balance-display`` fragment the editor replaced -- shaped on
    the pulse hero (``balance`` + ``account_id`` drive the control).

    Uses the narrow ``compute_balance_section`` producer (the as-of-today
    balance only, NOT the full pulse projection walk): the figure is the
    same ``balance_as_of_date`` the hero shows, so the reverted control
    agrees with the main pulse region.  Non-HTMX requests redirect to the
    dashboard page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("dashboard.page"))

    data = dashboard_service.compute_balance_section(current_user.id)
    return render_template("dashboard/_pulse_balance.html", pulse=data)
