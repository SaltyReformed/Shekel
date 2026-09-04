"""
Shekel Budget App -- Transaction route package: the transfer-shadow branches.

The three places a transaction-route request lands on a SHADOW
(``transfer_id IS NOT NULL``) and is re-expressed as a transfer update: the
PATCH edit save, the mark-done settle, and the cancel.  A shadow may never be
mutated directly -- the parent transfer and both shadows move together (design
doc invariants 3-5) -- so each of these translates the submitted transaction
fields into ``transfer_service.update_transfer`` kwargs, commits, and renders
the refreshed transaction cell the grid asked for.

**Extracted from ``mutations.py`` at plan step X-f1c**, on the same ground
every earlier split in this arc used: that module was at the 1000-line ceiling
and the settle-day edit door had to go in it.  The split is by RESPONSIBILITY
rather than by line count -- these three functions are the only shadow-to-
transfer translation in the route layer, and "a transaction request that lands
on a shadow becomes a transfer update" is one rule with three entry points.

**It does not contradict ``mutations.py``'s co-location argument, and moving
them TOGETHER is why.**  That docstring keeps the edit and status concerns in
one module because these three are near-identical parallel code
(``update_transfer`` + commit + ``StaleDataError`` preamble, refresh/render
tail, the ``_RenderTarget`` stale + ``IntegrityError`` response handling), and
splitting that parallelism across modules would re-surface as cross-file
``duplicate-code`` (R0801 is cross-file only).  Their parallelism is with EACH
OTHER, so it stays intra-file here; what crosses the new boundary is the
shadow-vs-regular pairing, which shares only its two-line except tails.
"""

import logging

from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.routes._authored_figure import figure_was_authored
from app.routes._render_helpers import render_transaction_cell
from app.routes.transactions._helpers import (
    _error_transaction_response,
    _finalised_edit_response,
    _INVALID_REFERENCE_MSG,
    _stale_transaction_response,
)
from app.services import (
    status_seam,
    transfer_service,
)
from app.services.settle_day import recorded_settle_day

logger = logging.getLogger(__name__)


def _apply_shadow_update(txn, txn_id, data):
    """Apply a PATCH update to a transfer shadow via the transfer service.

    Shadow transactions (``transfer_id IS NOT NULL``) cannot be mutated
    directly -- the parent transfer and both shadows must move together
    (design doc invariants 3-5).  Maps the submitted transaction fields
    onto :func:`transfer_service.update_transfer` kwargs, commits, and
    renders the refreshed cell.

    Args:
        txn: The shadow Transaction being edited.
        txn_id: The shadow's id, used for stale-conflict logging and the
            conflict re-fetch.
        data: The schema-loaded PATCH payload (``version_id`` already
            popped by the caller).

    Returns:
        A Flask response tuple: the updated cell + ``balanceChanged`` on
        success, a 409 conflict cell on a concurrent commit, or a 400
        when the transfer service rejects the change or the shadow's
        parent transfer is finalised (#26).
    """
    finalised_error = _finalised_edit_response(txn, data)
    if finalised_error is not None:
        return finalised_error

    # Map transaction field names to transfer service kwargs.
    #
    # **The THIRD door onto a transfer's amount, and the one that is easy to
    # miss** -- a PATCH addressed to a transfer SHADOW is answered by updating
    # its PARENT, so a figure submitted here is a figure submitted for the
    # transfer.  It therefore owes the same authorship fact the transfer
    # popover owes (ruling **R-JR**, plan step X-au-h); a door that mapped the
    # figure across without it would re-open findings **N-436** and **N-448**
    # through a route neither is written about.
    #
    # An UNAUTHORED figure is not forwarded at all, matching
    # ``transfers.mutations``: the transfer service's amount arm CLEARS the
    # relation that prices the row, so passing an echo would un-derive a
    # generated transfer on a save that touched only its status.
    svc_kwargs = {}
    amount_authored = figure_was_authored(data, "estimated_amount")
    svc_kwargs["amount_authored"] = amount_authored
    if amount_authored:
        svc_kwargs["amount"] = data["estimated_amount"]
    if "settled_amount" in data:
        svc_kwargs["settled_amount"] = data["settled_amount"]
    if "status_id" in data:
        # No ``settled_on`` companion: the seam CLEARS the day on entering a
        # non-settled status, so the explicit ``None`` this used to add was a
        # second statement of the seam's own rule (finding N-178's other half).
        # Dropping it changes no reconcile -- ``status_id`` is already in
        # ``transfer_service._POSTING_RELEVANT_FIELDS``, and the anchor-resync
        # arm it also gated requires the NEW status to be settled, which a
        # revert's never is.
        svc_kwargs["status_id"] = data["status_id"]
    if "notes" in data:
        svc_kwargs["notes"] = data["notes"]
    if "category_id" in data:
        svc_kwargs["category_id"] = data["category_id"]
    if "due_date" in data:
        svc_kwargs["due_date"] = data["due_date"]
    # The settle-day correction (ruling R-ED), graded against the status the
    # PATCH leaves the row in -- a day submitted alongside a revert is dropped
    # (ruling R-EG, :func:`status_seam.settle_day_for_status`).  No UI submits
    # one HERE today: a shadow's full-edit popover is the TRANSFER form, which
    # PATCHes ``transfers.update_transfer`` and carries its own copy of this
    # door, and the quick-edit renders an amount only.  It is mapped rather
    # than dropped because dropping it would make a crafted request LOOK like
    # it took -- the response re-renders the cell either way -- and because the
    # two doors onto one rule must not answer differently.
    #
    # **Graded against the PARENT transfer's status, not the shadow's**, which a
    # neutral review corrected: ``transfer_service`` hands the day to
    # ``apply_settle_day_correction``, which grades it against ``xfer.status_id``
    # -- so reading ``txn.status_id`` here would be a SECOND spelling of "which
    # status does this day belong to", the exact duplication
    # ``settle_day_for_status`` exists to remove.  Transfer Invariant 4 makes the
    # two equal today, so both drift directions land safely either way; the point
    # is that the answer comes from one place rather than two that agree by luck.
    #
    # It can also REFUSE (ruling **R-EL**'s floor), which is ordinary input from
    # the correction box, so it renders as the same designed 400 the service's
    # own rejections below do.
    try:
        settle_day = status_seam.settle_day_for_status(
            current_user.id,
            data.get("status_id", txn.transfer.status_id),
            data.get("settled_on"),
            # The SHADOW's own recorded pair, which is the pair for both legs
            # (Transfer Invariant 3).  It makes the reading echo-aware, so a
            # re-submitted day does not restamp its basis (plan step X-az).
            recorded_settle_day(txn),
        )
    except ValidationError as exc:
        return _error_transaction_response(txn_id, str(exc))
    if settle_day is not None:
        # ``settle_day``, not ``settled_on``: the value is the day AND the basis
        # that says how it is known (plan step **X-az**), and the transfer
        # service's kwargs key is named for the pair rather than for a column
        # ``Transfer`` does not have.  ``settle_day_for_status`` stamped the
        # ``entered`` basis, which is what a day out of a date box is.
        svc_kwargs["settle_day"] = settle_day

    try:
        transfer_service.update_transfer(
            txn.transfer_id, current_user.id, **svc_kwargs
        )
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on update_transaction shadow id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    except (NotFoundError, ValidationError) as exc:
        # transfer_service.update_transfer mutates xfer.amount and both
        # shadows' estimated_amount in-memory BEFORE running the status
        # transition through the state machine, so a rejected
        # amount+illegal-status PATCH leaves dirty mutations staged on
        # the session.  The error helper rolls back so they cannot
        # reach the DB, matching the sibling shadow handlers
        # (_mark_done_shadow, _cancel_shadow).
        return _error_transaction_response(txn_id, str(exc))

    db.session.refresh(txn)
    logger.info(
        "user_id=%d updated shadow transaction %d (transfer %d)",
        current_user.id, txn_id, txn.transfer_id,
    )
    response = render_transaction_cell(txn)
    return response, 200, {"HX-Trigger": "balanceChanged"}


def _mark_done_shadow(txn, txn_id, submitted, target):
    """Settle a transfer shadow through the transfer service.

    Marks both shadows and the parent transfer done atomically (the
    transfer service owns the shadow invariants).  Uses the DONE status
    for the service -- the 'done'/'received' split is a display
    convention for regular transactions only -- and forwards an optional
    manual ``actual_amount``.

    Args:
        txn: The shadow Transaction being settled.
        txn_id: The shadow's id, for stale-conflict logging / re-fetch.
        submitted: The figure a human typed for what moved, or ``None``
            when nobody typed one.
        target: The :class:`_RenderTarget` describing the response
            surface (mobile card vs desktop cell).

    Returns:
        A Flask response tuple: the refreshed cell + ``gridRefresh`` on
        success, a 409 conflict surface on a concurrent commit, or a 400
        on a bad FK or a state-machine rejection.
    """
    # **The capture-on-settle FREEZE left this route at plan step X-f2-c3**, and
    # its absence here is the fix rather than an omission.  This branch called
    # ``loan_payment_service.live_loan_payment_amount`` (deleted; the rule is
    # the amount model since plan step X-au-g-2c-2) and handed the answer
    # down as an ``actual_amount``, so an auto-derived loan payment recorded its
    # live payment-date cash through THIS door and the stale creation-time
    # escrow through the other three that can settle a transfer (the transfers
    # page's Mark Done, the transfer full-edit Status dropdown, and a
    # transaction PATCH landing on a shadow).  A ROUTE holding a money rule is
    # this arc's own root cause 1, and finding **N-219** is the same defect on
    # the transaction table.  ``transfer_service.settle_transfer`` owns it now,
    # for every door at once -- what this route states is the INTENT, and the
    # optional figure a human typed.
    #
    # The status is the verb's: it sets one for all three rows, because the
    # done/received split is a display convention for regular transactions and
    # is meaningless for a pair whose whole point is that one leg is each.
    #
    # NO explicit settle day: finding N-178's fix, rationale at the matching
    # comment in ``routes/transfers/mutations.py:mark_done``.  The seam stamps
    # the day on first entry and preserves it after; passing one here
    # overrode that and re-dated a replayed settle.
    try:
        transfer_service.settle_transfer(
            txn.transfer_id, current_user.id, submitted=submitted,
        )
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on mark_done shadow id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id, target)
    except IntegrityError:
        return _error_transaction_response(
            txn_id, _INVALID_REFERENCE_MSG, target,
        )
    except ValidationError as exc:
        # transfer_service.update_transfer runs the transition through
        # the state machine (commit C-21).  A mark-done request against
        # a Cancelled transfer shadow surfaces here as a
        # designed 400 fragment instead of crashing the request.
        return _error_transaction_response(txn_id, str(exc), target)
    db.session.refresh(txn)
    response = render_transaction_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


def _cancel_shadow(txn, txn_id, cancelled_id):
    """Cancel a transfer shadow through the transfer service.

    Cancels the parent transfer and both shadows atomically (the
    transfer service owns the shadow invariants); the route never
    mutates a shadow directly.

    Args:
        txn: The shadow Transaction being cancelled.
        txn_id: The shadow's id, for stale-conflict logging.
        cancelled_id: The Cancelled status id.

    Returns:
        A Flask response tuple: the refreshed cell + ``gridRefresh`` on
        success, a 409 conflict cell on a concurrent commit, or a 400
        when the state machine rejects cancelling a settled transfer.
    """
    try:
        transfer_service.update_transfer(
            txn.transfer_id, current_user.id,
            status_id=cancelled_id,
        )
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on cancel_transaction shadow id=%d",
            txn_id,
        )
        return _stale_transaction_response(txn_id)
    except ValidationError as exc:
        # transfer_service runs the transition through the state
        # machine.  An attempt to cancel a Paid/Received
        # transfer surfaces here as a designed 400 fragment instead of
        # crashing the request -- the transfer-service path was wired
        # by commit C-21; this clause is the route's translation.
        return _error_transaction_response(txn_id, str(exc))
    db.session.refresh(txn)
    response = render_transaction_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}
