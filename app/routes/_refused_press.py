"""
Shekel Budget App -- How a refused press is answered

ONE decision for every door a full-edit popover posts to -- the transaction
popover's Save, Paid / Received and Delete (``routes/transactions``) and the
transfer popover's Save and Paid (``routes/transfers``): whether a refusal
REDRAWS the popover or is the surface's ordinary error.

**The redraw is ruling R-CC128's** (developer 2026-10-04, "Redraw all"): a
press refused because its page named other things than the press would take
(:class:`~app.exceptions.PageOutOfDate`: the removal act over the bank lines,
ruling **R-CC127**, or the row delete over its dialog's purchases, ruling
**R-CC131**) is answered with the WHOLE popover drawn from what is true now,
the refusal above it, so every box and every caption on it -- and what it
posts back -- come from one moment, and pressing again goes ahead.  It is swapped into the card
the press came from (:func:`~app.utils.error_fragments.designed_error`'s
retarget, the shape ruling **R-SAL33** gave the readiness what-if), replacing
the card whole, so the popover stays open: the grid's ``afterSwap`` closes it
only when a card-issued request lands OUTSIDE it.

Every other refusal -- a refusal of any other kind, or an out-of-date press
from a surface with no popover to redraw -- is the door's own designed error,
unchanged.  What differs per package is only what the caller passes in: which
object is re-read and which template draws it, and which error fragment the
surface answers with.
"""

from typing import Callable, NamedTuple

from flask.typing import ResponseReturnValue

from app.exceptions import PageOutOfDate, ShekelError
from app.extensions import db
from app.routes._shown_lines import Press
from app.utils.error_fragments import designed_error


class RedrawnCard(NamedTuple):
    """A full-edit popover drawn again, and the id of its root element.

    Attributes:
        dom_id: The ``id`` the card's root carries -- the element the redraw
            replaces.
        body: The rendered card.
    """

    dom_id: str
    body: str


def answer_refused_press(
    exc: ShekelError,
    press: Press,
    *,
    redraw: "Callable[[str], RedrawnCard | None]",
    refuse: "Callable[[], ResponseReturnValue]",
) -> ResponseReturnValue:
    """Answer a press a service refused: the popover's redraw, or the door's error.

    Args:
        exc: What the service raised.
        press: What the request said about its page (``read_press``).
        redraw: Re-reads the press's object and draws its popover with the
            refusal's facts above it, AFTER this has rolled the press back;
            ``None`` when there is no card to draw.
        refuse: The door's ordinary designed error for *exc*; it rolls back
            itself.

    Returns:
        A designed 400 swapping the redrawn card into the card the press came
        from, or *refuse*'s answer -- for every other refusal, and for a press
        whose card cannot be drawn.  **Not a bare 404 there** (the second
        review's L3, on review finding L8's one rule for whether a transfer
        card is drawn): a bare body carries no designed-fragment header, so
        htmx drops it and the press reads as doing nothing, where the door's
        own refusal says why -- and answers a gone row "not found" itself
        (the transaction door's ``_RowGone``).
    """
    if not (isinstance(exc, PageOutOfDate) and press.from_popover):
        return refuse()
    db.session.rollback()
    db.session.expire_all()
    card = redraw(exc.facts)
    if card is None:
        return refuse()
    return designed_error(
        card.body, 400, retarget=f"#{card.dom_id}", reswap="outerHTML",
    )
