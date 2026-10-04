"""
Shekel Budget App -- What a page NAMED before the press

One translation, made identically by every door a full-edit popover posts to:
the transaction popover's Save, Paid / Received and Delete
(``routes/transactions/mutations.py``) and the transfer popover's Save and Paid
(``routes/transfers/mutations.py``).

**The act that takes a movement off the books asks what the owner was SHOWN**
(plan step ``credit_card:CC-5-4a-5``, rulings **R-CC81** / **R-CC127**): the
bank lines the page's withdrawal captions named, or the ruling that lets a
button stay silent.  The popover posts those lines back as ``shown_lines`` --
EMPTY when its captions name none, and that emptiness is itself a statement:
*"A button with no warning sends nothing"*.  A request WITHOUT the field came
from a surface that renders no caption at all, and what that means is the
DOOR's to say: Mark Paid's is silence under ruling **R-CC56** for the owner's
one-click (the grid's cell and its phone card) and, for a companion's, a press
that may free no line (ruling **R-CC130**,
``routes.transactions._press._mark_paid_press``); every other door's is that
it named nothing.

**Only a full-edit popover posts the field, so its presence also says WHERE a
refusal is answered**: a popover's out-of-date press redraws the popover
(ruling **R-CC128**), and any other surface's refusal is its ordinary error.

Pure: no Flask import, no session.  A route-tier helper because the facts it
reads are about the FORM, the shape of :mod:`app.routes._typed_figure`.
"""

from typing import NamedTuple

from app.services.match_withdrawal import Shown, Silent

#: The form field a full-edit popover posts its captions' bank lines under,
#: declared on every schema those presses load
#: (:class:`~app.schemas.validation._helpers.ShownLines`).
SHOWN_LINES_FIELD = "shown_lines"


class Press(NamedTuple):
    """What a press's request said about the page it was made from.

    Attributes:
        shown: What the removal act is told the page named
            (:class:`~app.services.match_withdrawal.Shown`), or what lets the
            door stay silent.
        from_popover: Whether the request posted the field -- which only a
            full-edit popover renders -- so a refusal as out of date redraws
            that popover (ruling **R-CC128**).
    """

    shown: Shown | Silent
    from_popover: bool


def read_press(data: dict, *, absent: Shown | Silent) -> Press:
    """Return what the press's page named, taking the field out of *data*.

    Args:
        data: The schema-loaded payload.  The field is REMOVED when present,
            because it names no column: the transaction PATCH ``setattr``s
            every key it does not recognise, and the transfer door hands its
            payload to the service as keywords.
        absent: What the door means when its request carries no field --
            :data:`~app.services.match_withdrawal.MARK_PAID` at Mark Paid,
            :data:`~app.services.match_withdrawal.NOTHING_SHOWN` elsewhere.

    Returns:
        The :class:`Press`: ``Shown`` over the posted ids -- none, for the
        empty value ``ShownLines`` loads as ``None`` -- from a popover, or
        *absent* from any other surface.
    """
    if SHOWN_LINES_FIELD not in data:
        return Press(shown=absent, from_popover=False)
    return Press(
        shown=Shown(data.pop(SHOWN_LINES_FIELD) or frozenset()),
        from_popover=True,
    )
