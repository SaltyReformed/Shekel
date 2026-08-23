"""What the hand-build form's two sides come to, BEFORE anything is applied.

Plan step ``bank_import:X-f6d-4``, ruling **R-FN**.

**Ruling R-FN says a difference is a transaction the owner ACCEPTS, and you
cannot accept a figure you have not seen.**  The proposals on the review screen
each state their own correction because the server built them; a group the
owner assembles from two checkbox lists is theirs, so nothing has computed it
yet.  This is what computes it.

**It runs the ACCEPT door's own reads and refusals, minus the writes.**  The
body it takes is the body Apply would send -- the same ``apply=hand``, the same
line ids, the same reviewed row tokens -- so the panel is not a second opinion
about the act, it is that act asked what it would do.  A screen that summed
differently from the door is finding **N-336** one tier up, and the surest way
to have one number is to have one derivation.

**It is a READ and it never writes** (:func:`~._accept.record_match` is the
only thing that does).  What it can do is REFUSE early: a group naming a
transfer shadow, or a row worth whatever its purchases are, is one the door
will not record, and saying so beside the checkboxes is better than saying it
after the press.

**The browser computes nothing.**  A first version of this step summed the two
sides in JavaScript from ``data-cash`` attributes and posted the result back as
the consent.  Three things were wrong with that and all three go away here: the
project's own coding rule says *JS never computes monetary values*; the
submitted figure was quantized by the schema with ``ROUND_HALF_EVEN``, the mode
``app.utils.money`` forbids, so a sub-cent figure was silently repaired into
agreement; and the same total ended up spelled four ways on one card.  Now the
server renders the figure and the consent box carries the server's own string.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no Flask import.  It READS and never writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.exceptions import ValidationError

from ._candidates import matched_subjects
from ._resolve import load_lines, resolve_rows
from ._scope import ReviewScope
from ._submission import MatchSubmission
from ._sides import MatchSides
from ._variance import bank_cash_for, reject_unrecordable

#: What the panel is doing about the gap, as one word the template dispatches
#: on.  A STRING rather than a ref-table id because it names a state of this
#: screen and no table holds it; the template compares it, which is the
#: sanctioned pattern for a computed domain string (see ``_money_macros``).
NOTHING_TICKED: str = "nothing"
AGREES: str = "agrees"
CORRECTS_THE_ROW: str = "corrects"
RECORDS_A_DIFFERENCE: str = "records"
REFUSED: str = "refused"


@dataclass(frozen=True)
class HandTotals:
    """What the hand-build panel shows, decided in the service.

    **Every field is rendered and none is computed by the template.**  The
    remedy in particular is a decision -- correcting the one row a match names
    and recording a group's difference are different acts with different
    consequences -- and deciding it in Jinja would be a second statement of
    ``bank_cash_for``'s rule in a language nothing lints.

    Attributes:
        bank: What the ticked statement lines come to, signed.
        app: What the ticked rows come to, on the same convention.
        difference: What the bank moved that those rows do not account for.
        remedy: One of the module constants, saying what Apply would do.
        consent: The figure the consent box carries, as the plain decimal
            string the door will compare -- or ``None`` where no consent is
            wanted.  **A STRING and not a Decimal**, because it is a wire
            value: rendering a ``Decimal`` through Jinja would let the
            template's own repr decide what the owner submits.
        refusal: Why Apply would refuse, in the door's own sentence, or
            ``None``.
    """

    bank: Decimal
    app: Decimal
    difference: Decimal
    remedy: str
    consent: "str | None" = None
    refusal: "str | None" = None

    @classmethod
    def untouched(cls) -> "HandTotals":
        """Return the panel for a form with nothing ticked on one side."""
        return cls(
            bank=Decimal("0.00"), app=Decimal("0.00"),
            difference=Decimal("0.00"), remedy=NOTHING_TICKED,
        )

    @classmethod
    def refused(cls, sentence: str, sides: "MatchSides | None" = None):
        """Return the panel for a selection this door would not record.

        **One constructor for the three places that build one**, so a refused
        panel cannot end up describing its figures differently depending on
        which refusal produced it -- including the route's own, where the
        submission never reached a service at all and there are no sides to
        show.

        Args:
            sentence: The refusal, in the door's own words.
            sides: What the two halves came to, where they were derived
                before the refusal fired; ``None`` where nothing got that far.

        Returns:
            The :class:`HandTotals`.
        """
        zero = Decimal("0.00")
        return cls(
            bank=zero if sides is None else sides.bank,
            app=zero if sides is None else sides.app,
            difference=zero if sides is None else sides.difference,
            remedy=REFUSED,
            refusal=sentence,
        )


def preview_hand_build(
    submission: MatchSubmission, scope: ReviewScope,
) -> HandTotals:
    """Return what the ticked lines and rows come to, and what Apply would do.

    Args:
        submission: What the form currently holds -- the same value
            :func:`~._accept.accept_match` would be given if the owner pressed
            Apply now.  Its ``accepted_difference`` is IGNORED: this function
            computes the figure the owner is about to consent to, so reading
            one back would be the screen agreeing with itself.
        scope: The pass's derived offer set.

    Returns:
        The :class:`HandTotals`.
    """
    if not submission.line_ids and not submission.rows:
        return HandTotals.untouched()
    matched = matched_subjects(scope.account_id)
    try:
        lines = load_lines(scope.account_id, submission.line_ids, matched)
        rows = resolve_rows(submission, scope, matched)
    except ValidationError as exc:
        # A line another match has claimed, or a row that moved since the page
        # was drawn.  The door would refuse the same way, so the panel says so
        # now rather than letting the press discover it.
        return HandTotals.refused(str(exc))

    sides = MatchSides.of(lines, rows)
    if not lines or not rows:
        # **One side ticked shows its total and offers NOTHING**, which is the
        # honest answer rather than the empty one: the owner has picked
        # something and the panel says what it comes to, but a match needs both
        # halves (``_accept._reject_empty_side``) so there is no act to consent
        # to yet.  A first version returned the empty panel here and reported
        # `$0.00` for a `$2,573.43` line the owner had just ticked -- caught by
        # driving the real screen in a browser.
        return HandTotals(
            bank=sides.bank, app=sides.app, difference=sides.difference,
            remedy=NOTHING_TICKED,
        )
    try:
        # **Asked with the difference as the accepted figure**, which is the
        # question the panel is for: *if you consented to this, what would
        # happen?*  Every refusal that is about the ROWS or the PAIR fires
        # here, so the screen names it beside the checkboxes.
        reject_unrecordable(rows, sides, sides.difference or None)
    except ValidationError as exc:
        return HandTotals.refused(str(exc), sides)

    if not sides.difference:
        return HandTotals(
            bank=sides.bank, app=sides.app, difference=sides.difference,
            remedy=AGREES,
        )
    return HandTotals(
        bank=sides.bank,
        app=sides.app,
        difference=sides.difference,
        # ONE rule for which remedy applies, read off the function that owns
        # it: a figure means the difference names a single row, ``None`` means
        # nothing says which member it belongs to.
        remedy=(
            CORRECTS_THE_ROW if bank_cash_for(sides, rows) is not None
            else RECORDS_A_DIFFERENCE
        ),
        consent=f"{sides.difference:f}",
    )
