"""What a page NAMED before a press, and the ONE check a save makes against it.

Split out of :mod:`app.services.match_withdrawal` at plan step
``credit_card:CC-5-4a-5`` (leaf 5c-2b), when ruling **R-CC135** (developer
2026-10-04, "One check per save") took that module past this project's
1,000-line bound (ruling **balance:R-IR**: the session that breaks a module
splits it).  **The seam is a SUBJECT**: :mod:`app.services.match_withdrawal`
answers *what withdrawing a match means* -- which acts a removal empties,
what that frees, what is deleted -- and this answers *what a door told the
owner, and whether its save did that and no more*: the declarations a page
makes (:class:`Shown`, :class:`Silent`, :class:`OwnerOnly`) and the
:class:`Press` every door opens around its unit of work.  Nothing changed on
the way across except the press itself, which replaced the per-call
comparison (``_refuse_unshown``).

Services-boundary discipline (``CLAUDE.md`` Architecture): plain values, no
Flask import, no query.  It runs the withdrawal events a save owes at the
save's close; each is the act's own log call, so the event keeps the act's
logger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.exceptions import PageOutOfDate, ValidationError

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from collections.abc import Callable

    from app.services.match_withdrawal import FreedLine, MatchWithdrawal


@dataclass(frozen=True)
class Shown:
    """The bank lines a page NAMED before the press: what the owner was shown.

    Plan step ``credit_card:CC-5-4a-5``, rulings **R-CC81** and **R-CC127**
    (developer 2026-09-23 / 2026-09-30): *"Each warning also sends back the
    bank lines it named, and the function compares them with what it would
    undo."*  The page posts back the ids its caption printed
    (``_withdrawal_macros.frees_lines``) and the door hands them here; a
    page that printed none posts none, which is :data:`NOTHING_SHOWN`.

    Attributes:
        line_ids: The ``bank_statement_lines.id`` values the caption named.
            Owner input, so never trusted as a scope: :class:`Press` only
            compares it, whole, with the lines a GRADED save frees (one that
            reached a match step, or whose page promised), so an id the save
            does not free -- another owner's, or one another tab has already
            freed -- refuses a graded save and never reaches a query.
    """

    line_ids: "frozenset[int]"


@dataclass(frozen=True)
class Silent:
    """A door that withdraws with NO caption, and what lets it.

    Ruling **R-CC81**: a button may undo a match unannounced only by naming
    the ruling that lets it stay silent, in its own code where a reviewer
    sees it -- the grid's one-click Mark Paid (:data:`MARK_PAID`, ruling
    **R-CC56**).  Until plan step ``credit_card:CC-5-4a-5c-2`` captions it,
    carry-forward names the OPEN FINDING that owns its caption instead
    (**CC-364**): today's behaviour, stated at its call site.  The purchase
    X and the three Credit doors did too until ``CC-5-4a-5b`` captioned them
    (finding **CC-367**), and the reconcile panel until ``CC-5-4a-5c-1``
    (findings **CC-364**, **CC-378**).

    Attributes:
        because: The ruling or finding id, written to the withdrawal event.
    """

    because: str


#: What a page that names no bank line shows, and what every settle verb
#: assumes when its door says nothing: *"A button with no warning sends
#: nothing"* (ruling **R-CC127**).  It refuses any press that would free a
#: line, so a door added later cannot undo a match without declaring.
NOTHING_SHOWN = Shown(frozenset())

@dataclass(frozen=True)
class OwnerOnly(Shown):
    """A press by someone no page may show the owner's bank lines to: a companion.

    Ruling **R-CC130** (developer 2026-10-04, "Companion refuses"): *"On the
    companion page, a Mark Paid that would undo a match is refused: 'Hotel is
    matched to a line on the bank statement, so only the account owner can
    mark it paid.' Nothing changes"*.  A companion has no statement screen, so
    its page names no line and never could: a press that would free one is
    refused with *refusal* rather than as a page out of date, which the page
    was not.  A :class:`Shown` naming nothing, so every signature that takes
    what a page showed takes this, and the act compares it as one.

    Attributes:
        line_ids: Empty: what a companion's page names.
        refusal: The sentence such a press is refused with -- the door's,
            because it names the row and the act it refuses (ruling
            **R-CC98**: the row's name, never an id).
    """

    line_ids: "frozenset[int]" = frozenset()
    refusal: str = field(kw_only=True)


#: The owner's one-click Mark Paid -- the grid's cell and its phone card --
#: silent by ruling **R-CC56**.
MARK_PAID = Silent("R-CC56")


@dataclass
class _PressState:
    """What one save has done so far: the lines it freed and the events it owes.

    Shared between a :class:`Press` and the twin :meth:`Press.reworded`
    returns, so a door that rewords its refusal mid-save still has ONE save.

    Attributes:
        opened: Whether the door has entered the press.
        closed: Whether the door has left it.
        abandoned: Whether the door returned without committing
            (:meth:`Press.abandon`).
        engaged: Whether any call of the save reached the match step.
        freed: Every line a call of this save has freed, by id.
        events: The withdrawal events the save owes, each the act's own log
            call, run at its close.
    """

    opened: bool = False
    closed: bool = False
    abandoned: bool = False
    engaged: bool = False
    freed: "dict[int, FreedLine]" = field(default_factory=dict)
    events: "list[Callable[[], None]]" = field(default_factory=list)


class Press:
    """ONE save's withdrawals, checked once: the press a door opens around its unit of work.

    Plan step ``credit_card:CC-5-4a-5`` (leaf 5c-2b), ruling **R-CC135**
    (developer 2026-10-04, "One check per save").  A door opens it over what
    its page declared and threads it to every call of
    :func:`~app.services.match_withdrawal.take_out_of_matches` its save
    makes::

        with match_press.Press(shown) as press:
            service_call(..., press=press)
        db.session.commit()

    * **Each call refuses AT ONCE a line the page did not name**
      (:class:`PageOutOfDate`, or an :class:`OwnerOnly` press's own
      sentence), before it writes anything, and records what it freed.
    * **The close compares the WHOLE save**: what every call freed must EQUAL
      what the page named, else :class:`PageOutOfDate` -- a line the page
      named that nothing freed is a page out of date, whether another tab
      freed it first or the page named it under a row whose save went another
      way.  So the door's rollback undoes the save, and its commit comes
      AFTER the ``with`` block.  **A save that reached NO match step is
      compared only when its page PROMISED what it named** (*promised*): a
      card's captions each name what one kind of submission would free -- a
      ``$0.00`` estimate, Paid, a "Paid from" change -- and the card posts
      them as one set, so a notes-only Save on it frees nothing and is not
      graded (ruling **R-CC135**: *"One-row buttons behave as now"*), while
      the reconcile panel names only what the rows TICKED will free, so a
      ticked row whose save went another way refuses it (ledger row
      **BAL-597**).
    * **The withdrawal EVENTS are logged at the close**, not per call: a save
      refused after one call withdrew would otherwise have logged a
      withdrawal its rollback undid.
    * A :class:`Silent` press compares nothing and logs at its close.

    It was a comparison PER CALL (``_refuse_unshown``) until this step, scoped
    to the accounts each call's movements were on so that a transfer's two
    sides graded against one posted set; a save making several calls --
    the reconcile panel's ticks, a transfer's two sides, carry-forward's
    envelopes -- then compared each call with a part of what its page said,
    and two rows matched to one bank line, ticked together, were refused as
    out of date on every try (finding **CC-384**).

    **A door's unit of work is one press**: a door making several calls opens
    one and threads it; a door making one opens one around it; a settle verb
    a door calls with none is told the page named nothing
    (:func:`~app.services.match_withdrawal.take_out_of_matches`, ruling
    **R-CC127**: *"A button with no warning sends nothing"*).  A press that
    records state must not span a savepoint its door rolls back alone -- the
    statement review's batch, the one door that does, names nothing, so its
    calls record nothing.

    Not thread-safe and not reusable: one press, one save.

    Args:
        shown: What the door's page named (:class:`Shown`, or
            :class:`OwnerOnly` for a companion's), or what lets it stay
            silent (:class:`Silent`).
        promised: Whether the page named exactly what this save will free
            -- the reconcile panel's captions under the rows ticked -- so
            the close compares a save that reached no match step too.
        state: The save a :meth:`reworded` twin shares; a door passes none.
    """

    def __init__(
        self, shown: "Shown | Silent", *, promised: bool = False,
        state: "_PressState | None" = None,
    ) -> None:
        """Hold the declaration; *state* is a reworded twin's, never a door's."""
        self.shown = shown
        self._promised = promised
        self._state = _PressState() if state is None else state

    def __enter__(self) -> "Press":
        """Open the save, once."""
        if self._state.opened:
            raise RuntimeError("A press is opened once, around one save.")
        self._state.opened = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        """Close the save: compare it whole and log what it withdrew, unless it raised.

        Returns:
            ``False``, so an exception raised inside the save propagates
            unchanged and the door's rollback undoes it.

        Raises:
            PageOutOfDate: When what the save freed differs from what its page
                named.
        """
        self._state.closed = True
        if exc_type is None and not self._state.abandoned:
            self._close()
        return False

    def abandon(self) -> None:
        """Leave the save ungraded and unlogged: its door returns WITHOUT committing.

        A door whose own refusal returns a response from inside its press --
        the transaction popover's field refusals -- commits nothing, and the
        request's teardown rolls the save back, so there is nothing to
        compare and no withdrawal to log.  Called just before that return.
        """
        self._state.abandoned = True

    def reworded(self, refusal: str) -> "Press":
        """Return this companion press refusing with *refusal*, the same save.

        A companion's purchase X carries its door's sentence for the PURCHASE,
        and the one removal act that takes the purchase also takes its
        envelope's payback; where the purchase's own matches free nothing, a
        refusal could only be over the payback's line (ruling **R-CC132**,
        ``entry_credit_workflow.x_press_for_payback``).

        Args:
            refusal: The sentence the twin refuses with.

        Returns:
            A :class:`Press` over :class:`OwnerOnly`, sharing this one's state.
        """
        return Press(
            OwnerOnly(refusal=refusal), promised=self._promised,
            state=self._state,
        )

    def take(self, planned: MatchWithdrawal) -> None:
        """Grade one call's withdrawal against the page, and record what it frees.

        Args:
            planned: What the call would withdraw
                (``match_withdrawal._summarise``).

        Raises:
            RuntimeError: When the press is not open -- a door that forgot
                the ``with``, or reused a closed press.
            PageOutOfDate: When the call frees a line the page did not name.
            ValidationError: An :class:`OwnerOnly` press's own refusal, when
                the call would free a line (ruling **R-CC130**).
        """
        if not self._state.opened or self._state.closed:
            raise RuntimeError(
                "A withdrawal ran outside an open press: a door opens one "
                "around its whole save."
            )
        self._state.engaged = True
        if not isinstance(self.shown, Silent):
            if planned.line_ids - self.shown.line_ids:
                if isinstance(self.shown, OwnerOnly):
                    raise ValidationError(self.shown.refusal)
                raise PageOutOfDate.over_lines(
                    len(self._state.freed.keys() | planned.line_ids),
                    len(self.shown.line_ids),
                )
        self._state.freed.update(
            (line.line_id, line) for line in planned.lines
        )

    def owe_event(self, log: "Callable[[], None]") -> None:
        """Record a withdrawal event the save logs at its close.

        Args:
            log: The act's own log call, taking nothing
                (``match_withdrawal._withdraw``), so the event keeps the
                act's logger and fields.
        """
        self._state.events.append(log)

    def _close(self) -> None:
        """Refuse a graded save that freed other lines than its page named, else log it.

        Raises:
            PageOutOfDate: When the two sets differ.
        """
        graded = self._state.engaged or self._promised
        if graded and not isinstance(self.shown, Silent):
            freed = frozenset(self._state.freed)
            if freed != self.shown.line_ids:
                raise PageOutOfDate.over_lines(
                    len(freed), len(self.shown.line_ids),
                )
        for log in self._state.events:
            log()
