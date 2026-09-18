"""
Shekel Budget App -- A figure out of a FORM is a person's statement

One translation, made identically by every route that collects a money figure
from a form and hands it to a service door: the transaction popover's Actual
box and Mark Paid's optional amount (``routes/transactions/mutations.py``),
the shadow popover (``routes/transactions/_shadow_mutations.py``), the
transfer popover (``routes/transfers/mutations.py``) and the add-purchase
form and entry PATCH (``routes/entries.py``).

**The service takes the figure and WHO WROTE it as one value** (plan step
**balance:X-bi-3e-1**, rulings **R-BAL61** and **R-BAL69**):
:class:`~app.services.stated_figure.StatedFigure`, so that a movement's
``figure_source_id`` records the writer's own statement and nothing infers it
from the day beside the figure.  A route is the layer that knows who its form
belongs to, and every form here belongs to a PERSON -- the statement matcher,
the one door that states the bank's figure, is a service and builds its own
value.  So the routes say ``typed``, once, here; a sixth route collecting a
figure says it by calling this rather than by spelling the enum again.

Pure: no Flask import, no session.  It is a route-tier helper because the
fact it states is about the DOOR, not about the row.
"""

from decimal import Decimal

from app.enums import MovementFigureSourceEnum
from app.services.stated_figure import StatedFigure


def typed_figure(amount: Decimal | None) -> StatedFigure | None:
    """Return *amount* as a PERSON's stated figure, or ``None`` for none.

    Args:
        amount: The loaded figure a form posted, or ``None`` when the form
            carried none (an empty box loads as ``None`` under every schema
            that declares one).

    Returns:
        The ``typed`` :class:`~app.services.stated_figure.StatedFigure`, or
        ``None`` -- which every service parameter typed ``StatedFigure | None``
        reads as *nobody stated a figure*.
    """
    if amount is None:
        return None
    return StatedFigure(amount=amount, source=MovementFigureSourceEnum.TYPED)
