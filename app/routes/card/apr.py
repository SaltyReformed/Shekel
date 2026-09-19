"""
Shekel Budget App -- Card route package: the card's APR.

Two doors for the card's rows on ``budget.rate_history`` (plan step
**credit_card:CC-3**, developer ruling **R-CC27**, 2026-09-18): one SETS the
APR for an effective date -- creating the row or rewriting its rate, since the
form that posts here is the whole row and a same-date resubmit is the
correction -- and one REMOVES a row, the door a mistyped date needs.  Both are
redirect-style POST handlers that flash and return to the cash detail page
hosting the form (developer ruling **R-CC28**: the APR section of the "Card
terms" card until CC-11's cockpit), gated by
:func:`~app.routes.card._helpers.load_configured_card_or_404` because the
section renders only once the card's terms exist.

The writes are :mod:`app.services.card_apr`'s; the transaction is this
module's.
"""

import logging

from flask import Response, abort, flash

from app.extensions import db
from app.routes._form_errors import load_form_or_redirect
from app.routes.card._bp import card_bp
from app.routes.card._helpers import back_to_card, load_configured_card_or_404
from app.schemas.validation import CardAprSchema
from app.services import card_apr
from app.utils.auth_helpers import require_owner

logger = logging.getLogger(__name__)

_apr_schema = CardAprSchema()


@card_bp.route("/accounts/<int:account_id>/card/rate", methods=["POST"])
@require_owner
def save_apr(account_id):
    """Set the card's APR effective from a date: create the row, or rewrite it.

    The payload is loaded ONCE through
    :class:`~app.schemas.validation.CardAprSchema` (``load_form_or_redirect``:
    a refusal flashes and returns to the page); its ``@pre_load`` has already
    divided the percent by 100, so the loaded rate is the storage-domain
    fraction the CHECK pins and :func:`~app.services.card_apr.set_apr` stores
    it verbatim, by date, in one statement.
    """
    account = load_configured_card_or_404(account_id)
    back = back_to_card(account.id)

    data = load_form_or_redirect(_apr_schema, back)
    if isinstance(data, Response):
        return data

    card_apr.set_apr(account.id, data["effective_date"], data["interest_rate"])
    db.session.commit()
    logger.info(
        "Set card APR for account %d effective %s",
        account.id, data["effective_date"].isoformat(),
    )
    flash("APR saved.", "success")
    return back.to_response()


@card_bp.route(
    "/accounts/<int:account_id>/card/rate/<int:row_id>/delete",
    methods=["POST"],
)
@require_owner
def remove_apr(account_id, row_id):
    """Remove one of the card's APR rows.

    :func:`~app.services.card_apr.remove_apr` deletes the row only when it
    is THIS card's; a row of another account -- another owner's, or the
    owner's own loan -- answers 404, the disposition a missing id gets.
    """
    account = load_configured_card_or_404(account_id)
    if not card_apr.remove_apr(account.id, row_id):
        abort(404)
    db.session.commit()
    logger.info("Removed card APR row %d from account %d", row_id, account.id)
    flash("APR removed.", "success")
    return back_to_card(account.id).to_response()
