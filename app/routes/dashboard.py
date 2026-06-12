"""
Shekel Budget App -- Dashboard Routes

Summary dashboard displaying upcoming bills, alerts, balance, payday
info, savings goals, and debt summary.  The dashboard is read-only for
transaction status; all settlement flows through ``transactions.mark_done``
(the canonical settlement endpoint).
"""

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.services import dashboard_service
from app.utils.auth_helpers import require_owner

dashboard_bp = Blueprint("dashboard", __name__)


def _alert_link_url(link: dict) -> str:
    """Map a service-emitted alert link to a concrete URL.

    The dashboard service is Flask-free and cannot call ``url_for``, so
    it emits a structured ``link`` (a ``kind`` plus the context the URL
    needs) and the route layer resolves it here.  Locked destinations
    (Gate A, 2026-06-12):

    * ``anchor_update`` -- the checking-account detail page, which puts
      the user in front of the checking balance that needs a true-up.
    * ``negative_projection`` -- the grid, deep-linked to the offending
      period via ``?offset=N``.
    * ``low_balance`` -- the grid.

    Args:
        link: The alert's structured link dict with a ``kind`` key.

    Returns:
        The resolved URL string.

    Raises:
        ValueError: When ``link.kind`` is unrecognized -- a programming
            error (a new alert kind was added without a mapping here)
            that must fail loud rather than silently linking nowhere.
    """
    kind = link["kind"]
    if kind == "anchor_update":
        return url_for("accounts.checking_detail", account_id=link["account_id"])
    if kind == "negative_projection":
        return url_for("grid.index", offset=link["offset"])
    if kind == "low_balance":
        return url_for("grid.index")
    raise ValueError(f"Unknown alert link kind: {kind!r}")


def _resolve_alert_links(alerts: list[dict]) -> list[dict]:
    """Resolve each alert's structured link to a concrete URL.

    Returns a new list of alert dicts with ``link`` replaced by the
    resolved URL string (or ``None`` when the alert carries no link), so
    the template renders ``alert.link`` directly as an ``href``.
    """
    resolved = []
    for alert in alerts:
        out = dict(alert)
        link = alert.get("link")
        out["link"] = _alert_link_url(link) if link else None
        resolved.append(out)
    return resolved


@dashboard_bp.route("/")
@dashboard_bp.route("/dashboard")
@login_required
@require_owner
def page():
    """Render the summary dashboard with all sections.

    Calls dashboard_service.compute_dashboard_data() and passes the
    result as template variables.  Alert links carry service-emitted
    ``kind`` + context; they are mapped to concrete URLs here (the
    service is Flask-free).
    """
    data = dashboard_service.compute_dashboard_data(current_user.id)
    data["alerts"] = _resolve_alert_links(data["alerts"])
    return render_template("dashboard/dashboard.html", **data)


@dashboard_bp.route("/dashboard/bills")
@login_required
@require_owner
def bills_section():
    """HTMX partial: refresh the upcoming bills section.

    Computes only the bills-section data (fix H) -- not the full
    dashboard -- so the ``balanceChanged`` refresh does not recompute
    the balance projection, alerts, savings, payday, and the deferred
    heavy debt-import chain on every transaction change.

    Non-HTMX requests redirect to the dashboard page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("dashboard.page"))

    data = dashboard_service.compute_bills_section(current_user.id)
    return render_template(
        "dashboard/_upcoming_bills.html",
        upcoming_bills=data["upcoming_bills"],
    )


@dashboard_bp.route("/dashboard/balance")
@login_required
@require_owner
def balance_section():
    """HTMX partial: refresh the balance and runway section.

    Computes only the balance-section data (fix H); this endpoint is on
    the live ``balanceChanged`` refresh path, so it must not run the full
    dashboard build to render one card.

    Non-HTMX requests redirect to the dashboard page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("dashboard.page"))

    data = dashboard_service.compute_balance_section(current_user.id)
    return render_template(
        "dashboard/_balance_runway.html",
        balance_info=data["balance_info"],
    )
