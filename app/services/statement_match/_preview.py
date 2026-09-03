"""What the hand-build form's two sides come to, BEFORE anything is applied.

Plan step ``bank_import:X-f6d-4``, ruling **R-FN**.

**Ruling R-FN says a difference is a transaction the owner ACCEPTS, and you
cannot accept a figure you have not seen.**  The proposals on the review screen
each state their own correction because the server built them; a group the
owner assembles from two checkbox lists is theirs, so nothing has computed it
yet.  This is what computes it.

**It runs the ACCEPT door's own reads and refusals, minus the writes.**  The
body it takes is the body Apply would send -- the same line ids and the same
reviewed row tokens, read through the same
:func:`~app.schemas.validation.statements.hand_match_payload` -- so the panel
is not a second opinion about the act, it is that act asked what it would do.
A screen that summed differently from the door is finding **N-336** one tier
up, and the surest way to have one number is to have one derivation.

**There is no longer an ordering token in that body**, and its absence is plan
step ``bank_import:X-gf-3b`` (ruling **bank_import:R-HC**).  It carried
``apply=hand`` while this form shared a page with the reviewed pass, where a
non-numeric index was the only thing keeping its ticks out of proposal ``0``'s
submission -- a money-correctness hazard held off by the two controls being
separate ``<form>`` elements, which is a property of the DOCUMENT.  The form is
a surface of its own now and posts a group with no index at all, so there is no
shared namespace left for two acts to collide in.

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
from ._offers import CandidateRow
from ._resolve import load_lines, resolve_rows
from ._scope import ReviewScope
from ._submission import MatchSubmission, spell_figure
from ._sides import MatchSides
from ._variance import DifferenceLanding, reject_unrecordable

#: What the panel is doing about the gap, as one word the template dispatches
#: on.  A STRING rather than a ref-table id because it names a state of this
#: screen and no table holds it; the template compares it, which is the
#: sanctioned pattern for a computed domain string (see ``_money_macros``).
NOTHING_TICKED: str = "nothing"
AGREES: str = "agrees"
CORRECTS_THE_ROW: str = "corrects"
RECORDS_A_DIFFERENCE: str = "records"
REFUSED: str = "refused"

#: What a panel describing NO act reports for its two sides.  One value rather
#: than two zeros written at each of the three sites that need them, so an
#: empty panel and a refused one cannot come to report different nothings.
_NO_SIDES: MatchSides = MatchSides(bank=Decimal("0.00"), app=Decimal("0.00"))


@dataclass(frozen=True)
class HandTotals:
    """What the hand-build panel shows, decided in the service.

    **Every field is rendered and none is computed by the template.**  The
    remedy in particular is a decision -- correcting the one row a match names
    and recording a group's difference are different acts with different
    consequences -- and deciding it in Jinja would be a second statement of
    :class:`~._variance.DifferenceLanding`'s rule in a language nothing
    lints.

    **It stores the DOOR's own two answers rather than copies of their
    parts** (plan step ``bank_import:X-gj-3a``).  It carried ``bank``, ``app``
    and ``difference`` as three fields, which is :class:`~._sides.MatchSides`
    written out twice -- the third of them being that class's own subtraction
    -- and it grew a second pair, the row a correction names and the figure it
    would write, which is :class:`~._variance.DifferenceLanding` written out
    twice.  Both are now HELD and read through properties, so the panel's
    figures and the door's cannot come apart, and every reader of
    ``totals.bank`` or ``totals.difference`` is unchanged.

    Attributes:
        sides: What the two ticked halves come to
            (:class:`~._sides.MatchSides`), which is where :attr:`bank`,
            :attr:`app` and :attr:`difference` are read from.
        remedy: One of the module constants, saying what Apply would do.
        consent: The figure this panel submits as the difference it was
            reviewed against, as the plain decimal string the door will
            compare -- or ``None`` where there is no act to review at all
            (nothing ticked, or a selection this door has already refused).
            **A STRING and not a Decimal**, because it is a wire value:
            rendering a ``Decimal`` through Jinja would let the template's own
            repr decide what the owner submits.

            **It is set for an AGREEING match too**, as ``"0.00"``, since plan
            step ``bank_import:X-gj-1b`` deleted the consent gate's exempt
            shape: the field is what says *this is the figure I was shown*,
            and a match that skipped it would be one the door cannot check.
            :attr:`remedy` is what decides whether the surface renders it as a
            hidden field or as the tick box -- the figure is the same fact
            either way.
        refusal: Why Apply would refuse, in the door's own sentence, or
            ``None``.
        landing: Where Apply would put the difference
            (:class:`~._variance.DifferenceLanding`), or ``None`` where there
            is no act to describe at all -- nothing ticked, one side ticked,
            or a selection this door has already refused.  :attr:`corrects`
            and :attr:`corrects_to` are what the pane reads off it.
        choices: The members this panel may attribute the difference TO --
            empty where there is no choice to make.  **A match naming ONE row
            offers none**, because ruling **R-GD**'s group clause (ii) already
            answers it, and a match whose sides AGREE offers none either,
            because nothing would be written wherever it landed.  Asked of
            :meth:`~._variance.DifferenceLanding.offers_a_choice`, which is the
            value that owns the question, so the control the panel draws and
            the attribution the door will honour cannot disagree.

            Rendered as the select the owner picks from, with *a new row with
            no category* as its unselected first option: the developer ruled on
            2026-09-01 (**R-IU**) that NO member is pre-selected, on **R-HX**'s
            reading of what justified means -- the app has a candidate here
            (the member whose figure is computed rather than stored) and not a
            justification.  **No later step supplies one**: ``X-gj-3b`` was
            withdrawn 2026-09-02 (**R-JJ**), and ``recurrence:R18`` removes the
            CHOICE instead of justifying it, by making one payroll deposit name
            one row.  The control is NOT retired with it -- a genuine multi-row
            group has no computed member, so it has no candidate either.
    """

    sides: MatchSides
    remedy: str
    consent: "str | None" = None
    refusal: "str | None" = None
    landing: "DifferenceLanding | None" = None
    choices: "tuple[CandidateRow, ...]" = ()

    @property
    def bank(self) -> Decimal:
        """Return what the ticked statement lines come to, signed."""
        return self.sides.bank

    @property
    def app(self) -> Decimal:
        """Return what the ticked rows come to, on the same convention."""
        return self.sides.app

    @property
    def difference(self) -> Decimal:
        """Return what the bank moved that those rows do not account for."""
        return self.sides.difference

    @property
    def corrects(self) -> "CandidateRow | None":
        """Return the row Apply would write the bank's figure to, or ``None``.

        **The ROW and not its name**, because the pane needs its label for the
        consent sentence AND its reviewed token for the control's selected
        option, and deriving either in Jinja would be a second spelling of the
        wire format (:attr:`~._submission.ReviewedRow.token`).

        Returns:
            The member the difference lands on, or ``None`` where it lands on
            none -- which is every remedy but :data:`CORRECTS_THE_ROW`.
        """
        return None if self.landing is None else self.landing.on_row

    @property
    def corrects_to(self) -> "Decimal | None":
        """Return what the bank says :attr:`corrects` is worth.

        On :attr:`~._offers.CandidateRow.cash_amount`'s own convention, so the
        sentence quotes the ROW's two figures rather than the match's two
        sums -- which for a lone row are the same two numbers and for a group
        are not.

        Returns:
            The figure, or ``None`` beside a ``None`` row.
        """
        return None if self.landing is None else self.landing.bank_cash

    @property
    def needs_consent(self) -> bool:
        """Return whether the owner has something here to AGREE to.

        Plan step ``bank_import:X-gj-1b``.  **The control's question, answered
        here rather than by a template comparing** :attr:`remedy`, because the
        two are not the same question and reading one for the other is how a
        money control comes to render in a state nobody designed: every panel
        that states an act submits :attr:`consent`, and only some of them are
        asking permission for it.

        Returns:
            ``True`` where this panel states an act whose difference is
            non-zero -- the tick box, carrying the sentence that says what
            would be written.  ``False`` both where the sides agree, which is
            the same figure as a hidden field because there is nothing to
            permit, and where there is no act at all (nothing ticked, or a
            selection already refused), which submits no figure and renders
            none.
        """
        return self.consent is not None and bool(self.difference)

    @classmethod
    def untouched(cls) -> "HandTotals":
        """Return the panel for a form with nothing ticked on one side."""
        return cls(sides=_NO_SIDES, remedy=NOTHING_TICKED)

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
        return cls(
            sides=_NO_SIDES if sides is None else sides,
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

            **Its ``attributed_to`` is READ, and the asymmetry is the point.**
            The difference is arithmetic this function performs, so agreeing
            with a submitted copy of it would be agreeing with itself; WHICH
            member carries it is a decision the owner made, which this
            function has no way to derive and must be told.  It reaches
            :func:`~._variance.reject_unrecordable` and
            :class:`~._variance.DifferenceLanding` exactly as it will at the
            press, so the remedy on screen and the remedy the door performs
            are one derivation.
        scope: The pass's derived offer set.

    Returns:
        The :class:`HandTotals`.
    """
    if not submission.line_ids and not submission.rows:
        return HandTotals.untouched()
    matched = matched_subjects(scope.account_id)
    try:
        # **NOT locked**: this is the preview, and a query request runs
        # in a READ ONLY transaction where PostgreSQL refuses every row
        # lock.  See :func:`~._resolve.load_lines`' ``for_write``.
        lines = load_lines(
            scope.account_id, submission.line_ids, matched,
            for_write=False,
        )
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
        return HandTotals(sides=sides, remedy=NOTHING_TICKED)
    # **DERIVED BEFORE THE REFUSALS, exactly as :func:`~._accept.record_match`
    # derives it** (plan step ``bank_import:X-gj-3a``): one of them is about
    # the landing itself, and the panel and the door must reach the same
    # sentence for the same body or this function is a second opinion rather
    # than the act asked what it would do.
    landing = DifferenceLanding.of(sides, rows, submission.attributed_subject)
    try:
        # **Asked with the difference as the reviewed figure**, which is the
        # question the panel is for: *if you consented to this, what would
        # happen?*  Every refusal that is about the ROWS or the PAIR fires
        # here, so the screen names it beside the checkboxes.
        #
        # It read ``sides.difference or None`` until plan step
        # ``bank_import:X-gj-1b``, and the two spell the same behaviour: the
        # gate returns early on a zero difference whether it is told ``None``
        # or ``Decimal("0.00")``.  The straight value is passed because it
        # states the question this preview is asking -- *against THIS figure*
        # -- where the ``or None`` spelled a zero difference as *nothing was
        # reviewed*, which is not what the panel means and is not what it
        # renders.  (An earlier draft of this comment claimed the ``or None``
        # would now make the preview refuse every agreeing match.  It would
        # not; the claim was written against a version of the gate that did
        # require a figure at zero, and was not revisited when that changed.)
        reject_unrecordable(rows, sides, sides.difference, landing)
    except ValidationError as exc:
        return HandTotals.refused(str(exc), sides)

    # **The figure travels whether or not there is anything to agree to**, and
    # that is the developer's ruling of 2026-08-30: every match states the
    # difference it was reviewed against, and a match whose sides agree was
    # reviewed against a difference of nothing.  What differs between the two
    # arms below is only the CONTROL -- a zero difference is a hidden field,
    # because there is nothing for an owner to consent to, and a non-zero one
    # is the tick box that says what would be written.
    if not sides.difference:
        return HandTotals(
            sides=sides, remedy=AGREES,
            consent=spell_figure(sides.difference),
        )
    # ONE rule for which remedy applies, read off the value that owns it: a
    # named member means the bank's figure is written to that row, and none
    # means nothing says which member the difference belongs to.
    return HandTotals(
        sides=sides,
        remedy=(
            RECORDS_A_DIFFERENCE if landing.mints_a_row else CORRECTS_THE_ROW
        ),
        consent=spell_figure(sides.difference),
        landing=landing,
        # **Offered only where there is a choice to make, asked of the value
        # that owns the question.**  One row is answered by ruling **R-GD**'s
        # group clause (ii), so the pane renders no control and the owner is
        # shown what will happen instead; several is the case this step exists
        # for.  A first version spelled ``len(rows) > 1`` here and the door
        # spelled it again, which is one predicate in two modules.
        choices=tuple(rows) if DifferenceLanding.offers_a_choice(rows) else (),
    )
