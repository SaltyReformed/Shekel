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
(``dashboard_service._pulse``, which is Flask-free and money-precise):
``float`` exists only at this Chart.js boundary -- the projected
end-balance series and threshold are serialized to a JSON string for the
``data-chart`` attribute, and the debt track's principal-paid fraction is
scaled to a 0-100 percent float for the rail marker.
"""

import json
from dataclasses import dataclass

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.db_transaction import write_transaction
from app.services import dashboard_service, pay_period_rolling
from app.services.balance_at import BalanceContext
from app.services.savings_dashboard_service import DebtSummary
from app.utils.auth_helpers import require_owner

dashboard_bp = Blueprint("dashboard", __name__)

# Chart.js x-axis label format: month abbreviation + un-padded day (e.g.
# "Jun 5").  The labels are the charted periods' end dates.
_CHART_LABEL_FORMAT = "%b %-d"
# Scale a 0-1 principal-paid fraction to a 0-100 percent for the rail.
_PERCENT_SCALE = 100


@dataclass(frozen=True)
class _DebtTrackView:
    """The debt track as the template renders it: the summary plus a percent.

    The presentation half of the producer's ``DebtSummary`` (plan step X-s3,
    ruling R-BD).  The producer is Flask-free and money-precise, so it hands up
    a ``Decimal`` FRACTION in ``[0, 1]``; the rail marker positions from a
    0-100 percent float, and that scaling plus the cast is presentation.  This
    route used to MUTATE a percent key into the producer's dict, which is the
    fourth and last layer of the assemble-a-dict-across-four-modules shape
    finding N-106 records -- a value object cannot be extended after the fact,
    so the transformation has to say what it produces.

    **The fraction it scales lives on the summary itself** as of plan step X-u
    (finding N-109): the producer used to pair the summary with a fraction a
    SECOND full debt projection produced, and this view is what that pairing
    was for at the boundary.  Both values reach the template, so a caption
    reading ``summary.principal_paid_fraction`` would render ``0.1768`` where
    the rail reads ``17.7`` -- the rail attribute below is the rendered one, and
    it is the only one ``dashboard/_tracks.html`` may position from.

    Attributes:
        summary: The producer's
            :class:`~app.services.savings_dashboard_service.DebtSummary`,
            passed through untouched -- every money figure stays ``Decimal``
            for the ``money`` macro to render.
        principal_paid_pct: The summary's principal-paid fraction scaled to
            0-100 as a ``float``, or ``None`` when no loan has originated (the
            rail then renders bare and the hero column still carries the
            figure).
    """

    summary: DebtSummary
    principal_paid_pct: float | None


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
    """Map the debt track to its view: the summary plus a percent float.

    The producer's summary carries an honest principal-paid FRACTION
    (``Decimal`` in [0, 1], or ``None`` when no loan has ORIGINATED -- not
    "when the user has no loans", which is the state where there is no summary
    to read at all and this function returns at the guard below); the
    rail marker positions from a 0-100 PERCENT.  Scaling and the
    ``Decimal -> float`` cast are presentation, so they live here at the
    serialization boundary, not in the Flask-free producer.  ``None``
    flows through unchanged (the rail then renders without a marker).

    It MUTATES NOTHING (plan step X-s3): the summary is a frozen value object,
    and a serialization step that reaches back into its input to add a
    field is how the debt track came to be assembled across four modules with
    its shape written down in none of them (finding N-106).  With a summary it
    returns a new dict carrying the view; with none there is nothing to
    map and the input is passed straight back, which is the same object -- said
    here because the first draft of this docstring claimed "returns a new dict"
    unconditionally and that was false on the ``None`` branch.

    Args:
        tracks: The ``compute_tracks_section`` dict (``goals`` list +
            ``debt``, a
            :class:`~app.services.savings_dashboard_service.DebtSummary` or
            ``None``).  It was a ``DebtTrack`` wrapper around that summary
            until plan step X-u deleted the second debt producer the wrapper
            existed to pair it with (finding N-109).

    Returns:
        A dict whose ``goals`` is unchanged and whose ``debt`` is the
        corresponding :class:`_DebtTrackView`; the input dict itself when
        ``debt`` is ``None``.
    """
    summary = tracks["debt"]
    if summary is None:
        return tracks
    fraction = summary.principal_paid_fraction
    return {
        **tracks,
        "debt": _DebtTrackView(
            summary=summary,
            principal_paid_pct=(
                float(fraction) * _PERCENT_SCALE
                if fraction is not None else None
            ),
        ),
    }


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

    **THIS ROUTE OPENS THE RENDER'S ONE READ PASS** (pay-calendar plan step
    C2-f2e, ledger rows **P56** and **P61**).  Both producers used to open one
    of their own, so ``/`` held two passes and derived the owner's pay calendar
    TWICE per render where ``/grid``, ``/savings`` and ``/retirement`` each
    derive it once.  Two figures published on one screen out of two passes are
    two figures computed against two clocks; there is one pass, one clock and
    one calendar here, and nothing under
    ``app/services/dashboard_service/`` can start a second.

    **The pass is built AFTER the rolling-window top-up, and the order is
    load-bearing**: ``top_up_rolling_window`` can CREATE pay periods and
    commit them, and the pass memoizes the calendar it derives from those very
    rows.  Building it first would serve this render a schedule one paycheck
    short of the one the database now holds.  **Plan step X-i3 made that
    ordering structural rather than stated**: the top-up runs inside
    :func:`app.db_transaction.write_transaction`, so this render's own
    transaction is read-only and could not hold the append even if the call
    moved.

    ``has_account`` and the pulse region read ONE account resolution
    (:func:`~app.services.dashboard_service.resolve_section`); the route
    resolved its own and the producer resolved another before this step.

    Route-layer serialization (the Chart.js / rail boundary) is applied
    here: the pulse chart series to a JSON string and the debt track's
    principal-paid fraction to a percent.
    """
    # Continuous rolling window: top up on dashboard entry (a future-period
    # consumer).  A no-op (one count, no lock) when rolling is disabled.
    #
    # **In its own COMMAND transaction** (plan step X-i3), for the reason
    # ``grid.index`` states at its own call: this render is a query and its
    # transaction is one read-only snapshot, so the append is committed here
    # and the pass below snapshots a database that already holds it.
    with write_transaction():
        pay_period_rolling.top_up_rolling_window(current_user.id)

    balance_ctx = BalanceContext.build(current_user.id)
    section = dashboard_service.resolve_section(balance_ctx)

    pulse = _serialize_pulse(
        dashboard_service.compute_pulse_section(section)
    )
    tracks = _serialize_tracks(
        dashboard_service.compute_tracks_section(balance_ctx)
    )

    return render_template(
        "dashboard/dashboard.html",
        has_account=section is not None,
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
        dashboard_service.compute_pulse_section(
            dashboard_service.resolve_section(
                BalanceContext.build(current_user.id),
            ),
        )
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

    Uses the narrow ``compute_balance_section`` producer (one folded
    balance, NOT the full pulse projection walk): the figure is the current
    period's projected END balance -- the same date the hero reads off its
    period map, and the one the fragment's own label promises -- so the
    reverted control agrees with the main pulse region.  Non-HTMX requests redirect to the
    dashboard page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("dashboard.page"))

    data = dashboard_service.compute_balance_section(
        dashboard_service.resolve_section(
            BalanceContext.build(current_user.id),
        ),
    )
    return render_template("dashboard/_pulse_balance.html", pulse=data)
