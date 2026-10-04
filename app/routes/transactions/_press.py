"""How the transaction doors answer a refused press.

**Split out of :mod:`.mutations` at plan step ``credit_card:CC-5-4a-5``**,
whose popover wiring pushed that module past ``max-module-lines`` -- the
shape :mod:`._gates` took at X-au-j.  What it holds is the transaction
package's call of the ONE decision both route packages share
(:func:`app.routes._refused_press.answer_refused_press`): only what differs
here is spelled here -- the transaction popover's redraw
(:func:`~app.routes.transactions.forms.redraw_full_edit`) and the designed
error fragment of the cell or card the press targeted
(:func:`~app.routes.transactions._helpers._error_transaction_response`).
"""

from functools import partial

from app.exceptions import NotFoundError
from app.routes._refused_press import answer_refused_press
from app.routes.transactions._helpers import _error_transaction_response
from app.routes.transactions.forms import redraw_full_edit


def _refused(txn_id, exc, press, target=None):
    """Answer a press a service refused, on the surface the press came from.

    The transaction doors' call of the one decision
    (:func:`app.routes._refused_press.answer_refused_press`): a popover out
    of date is redrawn (ruling **R-CC128**), and every other refusal is the
    designed error fragment it always was -- the 404 for a "Paid from"
    account that is not the row owner's (plan step ``credit_card:CC-5-3``),
    else a 400.

    Args:
        txn_id: The row the press named.
        exc: What the service raised.
        press: What the request said about its page.
        target: The
            :class:`~app.routes.transactions._helpers._RenderTarget`, or
            ``None`` for the desktop cell.

    Returns:
        A Flask response tuple.
    """
    return answer_refused_press(
        exc, press,
        redraw=partial(redraw_full_edit, txn_id),
        refuse=lambda: _error_transaction_response(
            txn_id, str(exc), target,
            status=404 if isinstance(exc, NotFoundError) else 400,
        ),
    )
