"""
Shekel Budget App -- Card route package: the card's terms.

ONE door for the ``budget.credit_card_params`` row (plan step
**credit_card:CC-2**, developer ruling **R-CC24**, 2026-09-18): it creates the
row when the card has none and rewrites it when it has one, because the form
that posts here renders every control every time, so a submit IS the whole
row and there is nothing for a second door to do differently.  A
redirect-style POST handler that flashes and returns to the cash detail page
hosting the form (**R-CC25**).
"""

import logging

from flask import Response, flash

from app.extensions import db
from app.models.credit_card_params import CreditCardParams
from app.routes._form_errors import load_form_or_redirect
from app.routes._redirect_target import RedirectTarget
from app.routes.card._bp import card_bp
from app.routes.card._helpers import load_card_or_404
from app.schemas.validation import CreditCardTermsSchema
from app.utils.auth_helpers import require_owner

logger = logging.getLogger(__name__)

_terms_schema = CreditCardTermsSchema()


@card_bp.route("/accounts/<int:account_id>/card/terms", methods=["POST"])
@require_owner
def save_terms(account_id):
    """Record the card's terms: create its params row, or rewrite it whole.

    The ownership and kind gate is :func:`~app.routes.card._helpers.load_card_or_404`
    (404 for not-found, not-yours and not-a-card alike).  The payload is
    loaded ONCE through :class:`~app.schemas.validation.CreditCardTermsSchema`
    (``load_form_or_redirect``: a refusal flashes and returns to the page, and
    a refusal raised at any stage of the load is heard); its ``@pre_load`` has
    already divided the two percent fields by 100, so the loaded values are
    the storage-domain fractions the model's CHECKs pin -- the route stores
    them verbatim, with no second divide.

    ``user_id`` is written from the ACCOUNT's owner, not the session's: the
    gate has already proved the two equal, and ``fk_credit_card_params_owner``
    would refuse the row otherwise, so the column is the account's fact
    restated where the database can hold it true.

    Before the row exists the card is a dormant plain liability (design 3.4);
    this door is the one act that ends that dormancy, and nothing in the
    application creates the row on the owner's behalf.
    """
    account = load_card_or_404(account_id)
    back = RedirectTarget("accounts.cash_detail", {"account_id": account.id})

    data = load_form_or_redirect(_terms_schema, back)
    if isinstance(data, Response):
        return data

    params = (
        db.session.query(CreditCardParams)
        .filter_by(account_id=account.id)
        .first()
    )
    if params is None:
        db.session.add(CreditCardParams(
            account_id=account.id, user_id=account.user_id, **data,
        ))
    else:
        for field, value in data.items():
            setattr(params, field, value)

    db.session.commit()
    logger.info("Saved card terms for account %d", account.id)
    flash("Card terms saved.", "success")
    return back.to_response()
