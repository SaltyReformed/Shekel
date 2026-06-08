"""
Shekel Budget App -- Transfer route package: grid-cell GET partials.

Read-only HTMX partials that return a transfer's grid cell in display,
quick-edit, or full-edit mode.  Every URL and endpoint name is preserved
verbatim from the pre-split ``app/routes/transfers.py``.
"""

from flask import render_template
from flask_login import current_user, login_required

from app.extensions import db
from app.models.ref import Status
from app.services import category_service, pay_period_service
from app.services.account_resolver import resolve_grid_account
from app.utils.auth_helpers import require_owner
from app.routes.transfers._bp import transfers_bp
from app.routes.transfers._helpers import _get_owned_transfer


@transfers_bp.route("/transfers/cell/<int:xfer_id>", methods=["GET"])
@login_required
@require_owner
def get_cell(xfer_id):
    """HTMX partial: return the display-mode cell for a transfer."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404
    account = resolve_grid_account(current_user.id, current_user.settings)
    return render_template(
        "transfers/_transfer_cell.html", xfer=xfer, account=account,
    )


@transfers_bp.route("/transfers/quick-edit/<int:xfer_id>", methods=["GET"])
@login_required
@require_owner
def get_quick_edit(xfer_id):
    """HTMX partial: return the inline amount edit form for a transfer."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404
    return render_template("transfers/_transfer_quick_edit.html", xfer=xfer)


@transfers_bp.route("/transfers/<int:xfer_id>/full-edit", methods=["GET"])
@login_required
@require_owner
def get_full_edit(xfer_id):
    """HTMX partial: return the full edit popover form for a transfer."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404
    statuses = db.session.query(Status).all()
    categories = category_service.list_active_categories(current_user.id)
    # Current + future periods power the in-popover period-move selector,
    # always including the transfer's own period so a transfer sitting in
    # a past period stays selected.  The service re-validates ownership of
    # the submitted id and moves the transfer plus both shadows together.
    periods = pay_period_service.get_current_and_future_periods(
        current_user.id, include_period_id=xfer.pay_period_id,
    )
    return render_template(
        "transfers/_transfer_full_edit.html",
        xfer=xfer, statuses=statuses, categories=categories, periods=periods,
    )
