"""
Shekel Budget App -- Transfer route package: grid-cell GET partials.

Read-only HTMX partials that return a transfer's grid cell in display,
quick-edit, or full-edit mode.  Every URL and endpoint name is preserved
verbatim from the pre-split ``app/routes/transfers.py``.
"""

from flask import render_template, request
from flask_login import current_user

from app.extensions import db
from app.models.ref import Status
from app.services import (
    category_service,
    match_withdrawal,
    pay_period_service,
    transfer_legs,
)
from app.services.pay_calendar import calendar_for
from app.services.state_machine import allowed_transitions
from app.utils.auth_helpers import require_owner
from app.utils.dates import display_today
from app.routes._period_options import period_move_options
from app.routes._render_helpers import (
    render_transfer_cell,
    transfer_budgets,
    transfer_settlement_amounts,
)
from app.routes.transfers._bp import transfers_bp
from app.routes.transfers._helpers import _get_owned_transfer
from app.utils.digit_strings import parse_row_id


@transfers_bp.route("/transfers/cell/<int:xfer_id>", methods=["GET"])
@require_owner
def get_cell(xfer_id):
    """HTMX partial: return the display-mode cell for a transfer."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404
    return render_transfer_cell(xfer)


@transfers_bp.route("/transfers/quick-edit/<int:xfer_id>", methods=["GET"])
@require_owner
def get_quick_edit(xfer_id):
    """HTMX partial: return the inline amount edit form for a transfer."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404
    return render_template(
        "transfers/_transfer_quick_edit.html",
        xfer=xfer, budgets=transfer_budgets(xfer),
    )


@transfers_bp.route("/transfers/<int:xfer_id>/full-edit", methods=["GET"])
@require_owner
def get_full_edit(xfer_id):
    """HTMX partial: return the full edit popover form for a transfer.

    **Reached from two surfaces, and the form says which** (leaf
    ``X-bi-6-1``, ruling **R-BAL87**): the transfers page, and a transfer
    LEG's grid cell, which asks with ``?leg_account_id=<the account the leg
    is on>`` so the popover's form and quick buttons target that cell and
    post the id back for the leg's re-render.  Until this leaf the grid
    reached the same popover through ``transactions.get_full_edit`` on the
    SHADOW row, which returned this template with ``source_txn_id``; the leg
    has no row, so the cell asks the transfer's own door.  A ``leg_account_id``
    that is neither endpoint names a leg that does not exist: 404, the
    "not found" every missing surface answers.
    """
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404
    leg_account_id = parse_row_id(request.args.get("leg_account_id"))
    if leg_account_id is not None and leg_account_id not in (
        xfer.from_account_id, xfer.to_account_id,
    ):
        return "Not found", 404
    # A soft-deleted transfer has no edit surface, and since plan step X-au-c3
    # it has no popover either: this form now resolves the pair's recorded and
    # retained figures, and ``load_transfer_rows`` REFUSES a deleted parent --
    # so without this the request 500'd where it used to render an edit form
    # over a deleted row.  404 rather than a message, per the project security
    # response rule: "not found" and "not yours" are indistinguishable, and a
    # deleted row is invisible to normal operations
    # (``transfer_service._validation._get_transfer_or_raise``).  The OTHER
    # transfer routes still admit a deleted parent -- notably the idempotent
    # DELETE, which needs to -- so the refusal is scoped to the edit doors
    # rather than pushed into ``_get_owned_transfer``.
    if xfer.is_deleted:
        return "Not found", 404
    statuses = db.session.query(Status).all()
    categories = category_service.list_active_categories(current_user.id)
    # Current + future periods power the in-popover period-move selector,
    # always including the transfer's own period so a transfer sitting in
    # a past period stays selected.  The service re-validates ownership of
    # the submitted id and moves the transfer plus both shadows together.
    periods = period_move_options(
        calendar_for(current_user.id), xfer.pay_period_id,
    )
    # What the pair RECORDED and what a re-settle would RE-BOOK (plan step
    # X-au-c3), through the one helper, so the popover opened from the
    # transfers page and the one opened from a grid leg's cell cannot show
    # different figures for the same transfer.
    amounts = transfer_settlement_amounts(xfer, current_user.id)
    return render_template(
        "transfers/_transfer_full_edit.html",
        xfer=xfer, statuses=statuses, categories=categories, periods=periods,
        leg_account_id=leg_account_id,
        budgets=transfer_budgets(xfer),
        settled=amounts.settled, retained=amounts.retained,
        # The settle-day correction's bounds -- ``max`` from ruling R-EJ,
        # ``min`` from ruling R-EL.  The USER's today via ``display_today()``,
        # never the process's UTC day: the input must not refuse a day
        # ``status_seam.reject_future_settle_day`` accepts.  The floor calls the
        # SAME function ``reject_settle_day_before_the_schedule`` refuses below,
        # so the browser bound and the server bound cannot drift.
        today=display_today(),
        settle_day_min=pay_period_service.earliest_recordable_day(
            current_user.id,
        ),
        # Pre-hint (grid audit D2): the status dropdown disables
        # transitions the state machine would reject.
        allowed_status_ids=allowed_transitions(xfer),
        # **What a $0.00 Actual would WITHDRAW** (plan step
        # ``credit_card:CC-5-4a-3``, ruling **R-CC59**): the caption under
        # the Actual box, read through the same twin the seam's write uses.
        payment_withdraws=_payment_withdrawal(xfer),
    )


def _payment_withdrawal(xfer):
    """Return what taking BOTH legs' payments out of their matches would withdraw.

    The read twin of the seam's write on a transfer's ``$0.00`` record: the
    settle hands each leg the one record (``transfer_service._status``), and
    a ``$0.00`` figure takes each leg's covering movement off the books
    through ``movement_removal.remove_movements`` (ruling **R-CC54**).  An
    act names ONE leg's movement -- a member is held to its movement's
    account, and the legs are on two -- so the two legs' acts are disjoint
    and one read over both movements is what the two per-leg writes
    withdraw.  The movements come through
    ``transfer_legs.covering_movements_by_leg``, the ONE join from a
    transfer to its legs' records that this popover's figures read too
    (``transfer_settlement_amounts`` through ``grid_transfer_leg``) -- a
    second CALL of that join in the same render, one indexed query, where
    threading one load through both reads would change the leg producers'
    signatures (reported, not done here).

    Args:
        xfer: The owned, live transfer the popover is drawn for.

    Returns:
        A :class:`~app.services.match_withdrawal.MatchWithdrawal`, or
        ``None`` when neither leg holds a payment.
    """
    movements = list(transfer_legs.covering_movements_by_leg([xfer.id]).values())
    if not movements:
        return None
    return match_withdrawal.pending_for_movements(movements)
