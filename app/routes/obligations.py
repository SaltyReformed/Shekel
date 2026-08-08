"""
Shekel Budget App -- Obligations redirect (retired surface).

The standalone ``/obligations`` page is retired (Recurring cluster Loop B).
Its unique value -- the monthly-equivalent committed totals -- now lives in
the unified ``/templates`` (Recurring) surface's summary band and section
subtotals, computed by the same canonical ``obligations_aggregator``
(E-24 / HIGH-05) so the figure is unchanged.  Its cash-flow projection
card is dropped: the dashboard's end-balance chart and the grid footer own
that question, and the projection duplicated them.  Its approximate
next-occurrence dates are superseded by the engine-backed dates the unified
surface derives from ``app.services.recurrence.rule_occurrences``.

This module keeps the ``/obligations`` URL alive as a redirect so old
bookmarks and links land on the surface that replaced it.
"""

from flask import Blueprint, redirect, url_for
from flask_login import login_required

from app.utils.auth_helpers import require_owner

obligations_bp = Blueprint("obligations", __name__)


@obligations_bp.route("/obligations")
@login_required
@require_owner
def summary():
    """Redirect the retired /obligations page to the unified Recurring surface."""
    return redirect(url_for("templates.list_templates"))
