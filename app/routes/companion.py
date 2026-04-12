"""
Shekel Budget App -- Companion View Routes

Mobile-first companion interface for viewing budgeted transactions,
tracking spending progress via entries, and marking transactions
as Paid.  The companion sees only transactions from templates
flagged ``companion_visible=True`` belonging to their linked owner.

Period navigation allows browsing past and future pay periods.
Entry CRUD is handled by the entries blueprint (Commit 8), which
already supports companion access via ``_get_accessible_transaction``.
"""

import logging
from decimal import Decimal

from flask import Blueprint, redirect, render_template, url_for
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import RoleEnum
from app.services import companion_service, pay_period_service
from app.services.entry_service import compute_entry_sums, compute_remaining
from app.exceptions import NotFoundError

logger = logging.getLogger(__name__)

companion_bp = Blueprint("companion", __name__, url_prefix="/companion")


def _build_entry_data(transactions: list) -> dict[int, dict]:
    """Compute entry summary data for each transaction with entries.

    Uses ``entry_service`` functions to avoid inline Decimal math
    and ensure consistency with the balance calculator and grid.

    Args:
        transactions: List of Transaction objects with entries
            accessible (eager-loaded or lazy).

    Returns:
        Dict mapping transaction ID to a dict with keys:
        ``total`` (Decimal), ``remaining`` (Decimal), ``count`` (int),
        ``pct`` (float -- percentage of budget consumed, capped at 100
        for progress bar width).
    """
    entry_data: dict[int, dict] = {}
    for txn in transactions:
        if txn.entries:
            sum_debit, sum_credit = compute_entry_sums(txn.entries)
            total = sum_debit + sum_credit
            remaining = compute_remaining(txn.estimated_amount, txn.entries)
            pct = (
                float(total / txn.estimated_amount * Decimal("100"))
                if txn.estimated_amount > 0
                else 0.0
            )
            entry_data[txn.id] = {
                "total": total,
                "remaining": remaining,
                "count": len(txn.entries),
                "pct": pct,
            }
    return entry_data


def _companion_or_redirect():
    """Check if current user is a companion; redirect owners to grid.

    Returns:
        True if the current user is a companion, or a redirect
        Response if they are an owner.
    """
    companion_role_id = ref_cache.role_id(RoleEnum.COMPANION)
    if current_user.role_id != companion_role_id:
        return redirect(url_for("grid.index"))
    return None


@companion_bp.route("/")
@login_required
def index():
    """Companion landing page: current period's visible transactions.

    Redirects owner users to the grid.  For companions, loads the
    current pay period's transactions (filtered by companion
    visibility), computes entry progress data, and renders the
    companion view with period navigation.
    """
    redir = _companion_or_redirect()
    if redir is not None:
        return redir

    try:
        transactions, period = companion_service.get_visible_transactions(
            current_user.id,
        )
    except NotFoundError:
        # No current period or misconfigured companion -- show empty view.
        return render_template(
            "companion/index.html",
            transactions=[],
            period=None,
            prev_period=None,
            next_period=None,
            entry_data={},
        )

    entry_data = _build_entry_data(transactions)

    prev_period = companion_service.get_previous_period(period)
    next_period = pay_period_service.get_next_period(period)

    return render_template(
        "companion/index.html",
        transactions=transactions,
        period=period,
        prev_period=prev_period,
        next_period=next_period,
        entry_data=entry_data,
    )


@companion_bp.route("/period/<int:period_id>")
@login_required
def period_view(period_id):
    """Navigate to a specific pay period's visible transactions.

    Same logic as ``index`` but with an explicit period_id.
    Redirects owner users to the grid.  Returns 404 if the
    period does not exist or does not belong to the companion's
    linked owner.

    Args:
        period_id (int): The pay period ID to display.
    """
    redir = _companion_or_redirect()
    if redir is not None:
        return redir

    try:
        transactions, period = companion_service.get_visible_transactions(
            current_user.id,
            period_id=period_id,
        )
    except NotFoundError:
        return "Not found", 404

    entry_data = _build_entry_data(transactions)

    prev_period = companion_service.get_previous_period(period)
    next_period = pay_period_service.get_next_period(period)

    return render_template(
        "companion/index.html",
        transactions=transactions,
        period=period,
        prev_period=prev_period,
        next_period=next_period,
        entry_data=entry_data,
    )
