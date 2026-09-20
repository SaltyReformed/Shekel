"""
Shekel Budget App -- The Card's Terms (the one read)

The ONE query behind every reader of ``budget.credit_card_params``
(:class:`~app.models.credit_card_params.CreditCardParams`, plan step
credit_card:CC-2): the terms door that creates or rewrites the row, the cash
detail page that renders it, and the card gate a configured-card feature
takes (plan step CC-3).  Each spelled the query itself until CC-3's review
counted three; the loan side's :func:`~app.services.loan_loaders.load_loan_params`
exists for the same reason.

``None`` is the DORMANT state, not an error (design 3.4: no auto-create): a
card whose owner has not stated its terms is a plain liability and every
card feature stays off.  A reader gates on
:func:`~app.services.account_projection.is_revolving` FIRST -- the row
outlives a re-type, so its existence alone never means "this is a card".

Flask-isolated; reads only.
"""

from app.extensions import db
from app.models.credit_card_params import CreditCardParams


def load_card_terms(account_id: int) -> CreditCardParams | None:
    """Return the account's terms row, or ``None`` while the card is dormant.

    Args:
        account_id: The revolving account (the caller has decided the kind).

    Returns:
        Its :class:`~app.models.credit_card_params.CreditCardParams`, or
        ``None``.
    """
    return (
        db.session.query(CreditCardParams)
        .filter_by(account_id=account_id)
        .first()
    )
