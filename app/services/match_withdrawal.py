"""What a bank line's match means once the app row it named has gone.

**A match ASSERTS an identity** -- *these bank lines and these app rows are one
movement* (:mod:`app.models.statement_match`).  Destroying one of those rows
makes the assertion false, so the assertion is withdrawn and the bank lines it
explained become unexplained again.  That is the whole of this module, and the
one act that takes a movement off the books asks it first
(:mod:`app.services.movement_removal`).

**An act is withdrawn only when it would be left naming NO APP ROW AT ALL**,
which is the narrowest condition that reaches the goal, and two adversarial
reviews measured what a wider one costs (2026-08-25).  Withdrawing on the loss
of ANY member destroyed a group act that was still two-thirds true: one line
against three rows, one row deleted, and the other two were silently
un-matched while keeping the settle days the act had given them.  A partial
loss is exactly what
:attr:`~app.services.statement_match.AcceptedGroup.agrees` is for -- it fails
the SUM, tints the act amber and offers the Undo -- so this writer fires on the
one case that flag cannot repair by itself: an act with nothing left to
re-review.  The two mechanisms split on one predicate rather than shadowing
each other -- :func:`_loses_every_row` -- which was also ``_still_holds``' own
first branch until plan step ``credit_card:CC-5-4a-4`` made an act naming no
app row unrepresentable and deleted that branch.

**Whether a SOFT delete withdraws is the CALLER's to say, and the two callers
now say different things.**  A TRANSACTION's soft delete -- one occurrence of a
recurring definition, kept as a tombstone -- takes the row's payments and
purchases off through the one act exactly as its hard delete does (ruling
**R-CC75**, developer 2026-09-23: *"Deleting the occurrence takes its payments
and purchases off the books through the one removal act, exactly as deleting
a one-off does"*), so an act the tombstone empties is withdrawn here and its
line is unexplained again (``transaction_service._delete._leaves_the_books``;
the tombstone counts as leaving, ruling **R-CC84**).  Until R-CC75 it withdrew
nothing, on the argument that a shipped button reverses a soft delete; what
that left was a hidden row holding money the balance does not count, under a
match that read explained.  A
TRANSFER's soft delete still withdraws nothing
(``transfer_service.delete_transfer``'s ``if not soft``): its restore paths
(``transfers.templates``' un-archive through ``restore_transfer``, and
``transfer_recurrence``'s maintain pass) put the shadows back, and the kept
payment a reverted leg holds under a soft-deleted shadow is ledger row
**BAL-532**'s, owned by plan step ``balance:X-bi-6-4``.

**What it does NOT do is remove rows the withdrawn act CREATED**, and the
asymmetry is deliberate.  ``release_match`` is the owner's UNDO -- *withdraw
this act, and take back what it made* -- and it refuses where the owner has
edited a created row since.  This is a different act: the owner asked to delete
ONE row, not to withdraw a decision.  What survives is COUNTED
(:attr:`MatchWithdrawal.kept_rows`), and counted over the rows the owner still
has: a subject in the going set -- deleted by the same press, or a recurring
occurrence the press empties and hides (ruling **R-CC84**) -- is not one, and
reporting it as kept is the *"Nothing moved."* shape this arc has
shipped once already (finding **N-336**).  A first build counted every creation
of every withdrawn act, and both reviews measured it promising a `-$21.68`
residual would stay while the press destroyed it.

**A MOVEMENT that LEAVES ITS ACCOUNT leaves the acts naming it too** (plan
step ``credit_card:CC-5-4a-1``, ruling **R-CC46**, developer 2026-09-21).
Every accepted act names movements since ruling **R-CC43**, and a member is
held to its movement's account by ``fk_statement_match_members_entry_account``
-- a key the database will not let the movement's ``account_id`` walk out
from under (``NO ACTION`` on update).  A match asserts that THIS account's
bank line IS this movement; money that moved on another account was shown by
no such line, so the assertion is false the moment the movement is re-pointed
(a "Paid from" correction, ``status_seam._covering._re_point``), exactly as
it is false the moment the movement is destroyed.  So the member goes as a
DELETE would have taken it, and an act left naming no app row is withdrawn on
the same narrowest condition -- :func:`withdraw_for_moved_movement`, with
:func:`pending_for_movements` for the screen that offers the move.  The
refused alternative kept the owner's correction from happening while a match
stood, naming the register's Undo as the remedy.

**A movement that LEAVES THE BOOKS leaves through ONE act, and this is that
act's match step** (plan step ``credit_card:CC-5-4a-3``, ruling **R-CC54**,
developer 2026-09-22).  :func:`take_out_of_matches` is the match step of
:func:`app.services.movement_removal.remove_movements` -- take the going
movements out of every act naming them, withdraw an act that leaves naming no
app row -- after that act has reversed their ledger and before it deletes
them.
The doors that remove a movement call THAT act rather than this module: the
transaction delete verb, the purchase delete, both CC-payback teardowns, the
transfer delete, and the status seam's ``$0.00`` / ``purchases`` record
(finding **CC-358**: until this step the seam deleted the payment itself and
withdrew nothing, so the act kept its line alone and a re-match raised on
``uq_statement_match_members_line``).  The member of an act that KEEPS
another app row is taken out explicitly, as the move always did: the act is
the one path a movement leaves by, and the member key does not cascade.

**"Every door" is structural since plan step ``credit_card:CC-5-4a-4``**
(rulings **R-CC54**, **R-CC63**..**R-CC65**, closing finding **CC-363**).
Three BULK doors destroyed movements without the act -- the template and
account permanent deletes and the pay-period retire -- because they judged
what they may destroy by a row's STATUS rather than by what it held
(measured: a permitted template delete erased a `$25.00` purchase recorded
from a bank line, and the act kept its line alone).  Now a row holding a
movement is history those doors keep, and neither the row's key to its
movements nor the member's key to its movement cascades, so no statement can
empty an act behind this module's back.  The read-time predicate that
stopped counting such an act's line as explained
(``statement_match._candidates.act_still_names_a_row``) is DELETED with it:
an act naming no app row is unrepresentable rather than filtered.  What this
module adds is the CLEANUP and the DISCLOSURE: the act the press empties goes,
and a door that discloses names the lines it frees before the press -- the row
delete (ruling **R-CC75**) and the two popovers (**R-CC56**, **R-CC59**).

**And the act ASKS what the owner was shown** (plan step
``credit_card:CC-5-4a-5``, rulings **R-CC81** and **R-CC127**).  Every
caller of :func:`take_out_of_matches` passes the bank lines its page named
(:class:`Shown`) or what lets it stay silent (:class:`Silent`), and a press
whose freed lines differ from the named ones is refused before anything is
written: a page drawn before a match existed, or a door that forgot its
caption.  The owner's one-click Mark Paid is silent by ruling (**R-CC56**),
and a companion's is refused whenever it would free a line, because no page
may show a companion the owner's statement (:class:`OwnerOnly`, ruling
**R-CC130**) -- as is its purchase X and CC un-tick (ruling **R-CC132**).
The purchase X, the CC un-tick, Undo CC and Status leaving Credit name their
lines first since plan step ``credit_card:CC-5-4a-5b`` (ruling **R-CC80**,
finding **CC-367** closed), and the reconcile panel since ``CC-5-4a-5c-1``:
the tick it captions -- a row settling from its purchases over a kept
payment -- names its lines under the row, a typed ``$0.00`` box is refused
(ruling **R-CC125**), and neither list offers a tick that would move a
payment between accounts (ruling **R-CC126**; finding **CC-378** closes at
that leaf's tick).  Every other panel tick names nothing, so one that would
free a line is refused; two can still take a payment off uncaptioned -- a
``$0.00``-figure row ticked with its box cleared, saved when its payment is
unmatched (ledger row **BAL-596**), and two rows matched to one line ticked
together, refused (finding **CC-384**).  Carry-forward names the open finding that owns its caption
until ``CC-5-4a-5c-2`` (**R-CC76**; finding **CC-364**).

**Why it is a leaf module and not part of** :mod:`app.services.statement_match`.
That package imports ``entry_service``, ``credit_workflow`` and
``transaction_service`` -- three of the doors whose act calls this -- so a rule
living there could not be reached from any of them.  It imports the two match
MODELS and, of ``app.services``, only :mod:`app.services.transfer_legs` -- the
leaf below every service, which names a movement's parent for the event
(plan step ``balance:X-bi-6-4c-4``) -- which is what lets the act and the
seam's move above it call one rule instead of a spelling each.

Services-boundary discipline (``CLAUDE.md`` Architecture): ORM rows in, a
frozen dataclass out, no Flask import.  It MUTATES and does NOT commit -- the
caller owns the unit of work.
"""

from __future__ import annotations

import logging
from collections.abc import Hashable, Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import selectinload

from app.exceptions import PageOutOfDate, ValidationError
from app.extensions import db
from app.models.statement_import import BankStatementLine
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.services import transfer_legs
from app.services.transfer_legs import TransferLeg
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_MATCH_WITHDRAWN,
    log_event,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FreedLine:
    """One bank line a withdrawal puts back among the unexplained.

    Attributes:
        line_id: The line's primary key.
        posted_on: The day the bank posted it.
        amount: Its signed figure, as the bank states it -- negative for money
            leaving the account.
        description: What the bank called it.

    Carried as FACTS rather than as a count because the control that triggers
    the withdrawal is a destructive one, and *"1 bank line"* over a `$793.23`
    ACH payment is the *"Nothing moved."* sentence this arc has already shipped
    once (finding **N-336**).  A dialog that names the day and the figure lets
    the owner recognise the line without leaving the screen.
    """

    line_id: int
    posted_on: date
    amount: Decimal
    description: str


@dataclass(frozen=True)
class MatchWithdrawal:
    """What withdrawing a subject's matches would take back, or did.

    **ONE dataclass for the read and the write**, which is the shape
    :func:`~app.services.statement_match.planned_removals` already has one door
    over: the confirm dialog prints what the press will do, the press does
    exactly that, and two derivations would let the two disagree.

    Attributes:
        matches: How many accepted acts are withdrawn.
        lines: The bank lines that become unexplained again.
        kept_rows: How many rows those acts had CREATED that the owner still
            has after the press (a deleted occurrence's hidden tombstone is not
            one, ruling **R-CC84**) -- reported rather than silent, because a row the owner
            did not ask for and was not told about is exactly what a receipt is
            for.  **A creation whose subject is in the going set is NOT counted
            here**, and a first build counted it: both 2026-08-25 reviews
            measured the dialog promising a `-$21.68` residual would stay while
            the press destroyed it, and a minted envelope reported as two rows
            kept while both it and its purchase went.  ``0`` for every act on
            the developer's database, which carry no creation records at all
            (the relation postdates them).
    """

    matches: int
    lines: "tuple[FreedLine, ...]"
    kept_rows: int

    @property
    def frees_a_line(self) -> bool:
        """Return whether this withdrawal changes what the review screen shows.

        The one question a template asks, answered here rather than as a
        ``length`` test in a Jinja condition.
        """
        return bool(self.lines)

    @property
    def line_ids(self) -> "frozenset[int]":
        """Return the ids of the lines this frees -- what a caption naming them posts back.

        The one spelling the page's posted field and the act's comparison
        (:func:`_refuse_unshown`) share, so what a caption sends and what the
        press is graded against cannot be two readings of one withdrawal
        (plan step ``credit_card:CC-5-4a-5``, ruling **R-CC127**).
        """
        return frozenset(line.line_id for line in self.lines)


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
            Owner input, so never trusted as a scope: :func:`_refuse_unshown`
            reads only those on the accounts the press touches.
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


def _refuse_unshown(
    shown: "Shown | Silent", entries, planned: MatchWithdrawal,
) -> None:
    """Refuse a press whose freed lines differ from the lines its page named.

    **Compared PER ACCOUNT**: the lines this call frees against the named
    lines on the accounts of the movements it takes.  A member is held to its
    act's account and to its line's or movement's (the composite keys on
    :class:`~app.models.statement_match.StatementMatchMember`), so a call can
    free a line only on its own movements' accounts -- and a transfer's two
    sides, on two accounts, are two calls of one press that one posted set
    grades exactly, each against its own side's lines.  A named line on no
    account this call touches is another call's, or (a payment re-pointed
    in another tab since the page was drawn) a line already unexplained.

    **Equality, both ways** (*"At 10:10 they differ, so nothing is saved"*):
    a line freed and not named is the silent withdrawal the ruling forbids,
    and a line named and not freed is a page out of date -- except for a
    press whose page could name none (:class:`OwnerOnly`), whose refusal is
    its own sentence (ruling **R-CC130**).

    Args:
        shown: What the door declared.
        entries: The movements this call takes out of their matches.
        planned: What it would withdraw (:func:`_summarise`).

    Raises:
        PageOutOfDate: When the two sets differ -- a ``ValidationError``, so
            the door's existing refusal path renders it and its rollback
            undoes the press; a full-edit popover redraws itself instead
            (ruling **R-CC128**).
        ValidationError: An :class:`OwnerOnly` press's own refusal, when it
            would free a line.
    """
    if isinstance(shown, Silent):
        return
    named = set()
    if shown.line_ids:
        named = {
            row[0]
            for row in db.session.query(BankStatementLine.id)
            .filter(
                BankStatementLine.id.in_(shown.line_ids),
                BankStatementLine.account_id.in_(
                    {entry.account_id for entry in entries},
                ),
            )
            .all()
        }
    freed = planned.line_ids
    if freed != named:
        if isinstance(shown, OwnerOnly):
            raise ValidationError(shown.refusal)
        raise PageOutOfDate.over_lines(len(freed), len(named))


def _acts_emptied_by(entry_ids: "set[int]") -> "list[StatementMatch]":
    """Return the acts these movements are the LAST app rows of -- the READ.

    The first half of :func:`_partition` over :func:`_acts_naming`, the same
    call :func:`take_out_of_matches` makes, so what a dialog promises and
    what the press withdraws are one derivation.

    Args:
        entry_ids: Entry ids about to leave -- a purchase's, or a row's
            payment record, which goes with its row.

    Returns:
        The acts to withdraw, each with ``members`` and ``creations`` loaded.
        Empty for an ordinary delete, which is nearly every delete.
    """
    emptied, _surviving = _partition(_acts_naming(entry_ids), entry_ids)
    return emptied


def _partition(
    acts: "list[StatementMatch]", entry_ids: "set[int]",
) -> "tuple[list[StatementMatch], list[StatementMatch]]":
    """Split *acts* into those these movements EMPTY and those that survive.

    An act is emptied when every app-side member it holds is in the going set
    (:func:`_loses_every_row`) -- so a group that keeps a row keeps its act,
    and the ``agrees`` flag is what re-reviews it.  ONE spelling of the split
    for the read (:func:`_acts_emptied_by`) and the write
    (:func:`take_out_of_matches`).

    Args:
        acts: The acts naming any of the movements, ``members`` loaded.
        entry_ids: The movements leaving their matches.

    Returns:
        ``(emptied, surviving)``, each in *acts*' order.
    """
    emptied = [act for act in acts if _loses_every_row(act, entry_ids)]
    surviving = [act for act in acts if not _loses_every_row(act, entry_ids)]
    return emptied, surviving


def _acts_naming(entry_ids: "set[int]") -> "list[StatementMatch]":
    """Return every act naming any of these movements, loaded WHOLE.

    Two statements.  The first finds every act naming any of the going
    movements; the second loads those acts with both relations, which is what
    both the emptied acts (withdrawn whole) and the surviving ones (a member
    taken out of the loaded collection) are read off.

    **A member names a MOVEMENT, so the going MOVEMENTS are the whole
    question** (plan step ``credit_card:CC-5-4a-2``, ruling **R-CC45**): a
    row leaving the table takes its purchases and its payment with it
    (:func:`_subject_ids`), and those are what an act names.  The going ROW
    ids still matter to :func:`_summarise`, whose creations may name a row.

    **Scoped by the SUBJECTS, and by nothing else** (plan step
    ``credit_card:CC-5-4a-1``).  A member is held to its movement's account
    by a composite key (``fk_statement_match_members_entry_account``) and to
    its act's by another, so an act naming one of these ids is on that
    movement's account and its owner's by construction;
    a second clause on ONE account -- this took the going rows' account
    through ``CC-5-3`` -- is then either redundant or wrong, and since the
    tender (``CC-5-3``) it is wrong: a bill's payment can sit on ANOTHER
    account than the bill (a checking bill paid from the card), and the act
    that names that payment (ruling **R-CC43**) is on the CARD's account.
    Deleting the bill takes the payment with it, and an act lookup scoped to
    checking would have left that act standing while its member cascaded
    away -- an act naming a line and no movement, and the freed card line
    undisclosed by the dialog.  (Since plan step ``credit_card:CC-5-4a-4``
    the member key refuses rather than cascades, so a wrong scope here would
    fail loud at the flush instead.)

    Args:
        entry_ids: Entry ids about to leave -- a purchase's, or a row's
            payment record, which goes with its row.

    Returns:
        Every act naming one of them, each with ``members`` and ``creations``
        loaded.  Empty for an ordinary delete, which is nearly every delete.
    """
    if not entry_ids:
        return []
    match_ids = {
        row[0]
        for row in db.session.query(StatementMatchMember.match_id)
        .filter(StatementMatchMember.transaction_entry_id.in_(entry_ids))
        .all()
    }
    if not match_ids:
        return []
    return (
        db.session.query(StatementMatch)
        .filter(StatementMatch.id.in_(match_ids))
        .options(
            selectinload(StatementMatch.members),
            selectinload(StatementMatch.creations),
        )
        .all()
    )


def _loses_every_row(act: StatementMatch, entry_ids: "set[int]") -> bool:
    """Return whether *act* would name no app row once these movements go.

    Args:
        act: The act, with ``members`` loaded.
        entry_ids: Movement ids about to leave the table.

    Returns:
        ``True`` when every app-side member is in the going set.  Every act
        reaching here names at least one of them (:func:`_acts_naming`), and
        since plan step ``credit_card:CC-5-4a-4`` no act can name no movement
        at all (ruling **R-CC54**), so the empty case this once answered
        ``True`` for has no subject.
    """
    return all(
        member.transaction_entry_id in entry_ids
        for member in act.members
        if member.bank_statement_line_id is None
    )


def _line_ids_of(acts: "list[StatementMatch]") -> "set[int]":
    """Return the ids of the bank lines *acts* name.

    Args:
        acts: The acts, with ``members`` loaded.

    Returns:
        Every line id a member of one of them names.
    """
    return {
        member.bank_statement_line_id
        for act in acts
        for member in act.members
        if member.bank_statement_line_id is not None
    }


def _freed_lines(acts: "list[StatementMatch]") -> "dict[int, FreedLine]":
    """Return the bank lines *acts* name, as facts, keyed by id -- ONE query.

    Read once for however many withdrawals *acts* make up between them
    (:func:`pending_for_each`), and for the one a press makes.  No query
    at all when no act names a line.

    Args:
        acts: The acts, with ``members`` loaded.

    Returns:
        ``{line id: FreedLine}``.
    """
    line_ids = _line_ids_of(acts)
    if not line_ids:
        return {}
    return {
        line.id: FreedLine(
            line_id=line.id,
            posted_on=line.posted_on,
            amount=line.amount,
            description=line.description,
        )
        for line in db.session.query(BankStatementLine)
        .filter(BankStatementLine.id.in_(line_ids))
        .all()
    }


def _summarise(
    acts: "list[StatementMatch]",
    transaction_ids: "set[int]",
    entry_ids: "set[int]",
    lines: "dict[int, FreedLine]",
) -> MatchWithdrawal:
    """Return what withdrawing *acts* comes to, WITHOUT withdrawing them.

    Args:
        acts: The acts, with ``members`` and ``creations`` loaded.
        transaction_ids: Row ids the press takes from the owner -- deleted,
            or a recurring occurrence it empties and hides (ruling
            **R-CC84**) -- so a creation that names one is not reported as
            staying.
        entry_ids: Purchase ids about to leave the table, likewise.
        lines: :func:`_freed_lines` over *acts* or over a superset of them,
            so many withdrawals read their lines in one query.

    Returns:
        Their :class:`MatchWithdrawal`, its lines ordered by the day the
        bank posted them.
    """
    return MatchWithdrawal(
        matches=len(acts),
        lines=tuple(
            sorted(
                (lines[line_id] for line_id in _line_ids_of(acts)),
                key=lambda line: (line.posted_on, line.line_id),
            )
        ),
        kept_rows=sum(
            1
            for act in acts
            for creation in act.creations
            if creation.transaction_id not in transaction_ids
            and creation.transaction_entry_id not in entry_ids
        ),
    )


def _subject_ids(rows) -> "tuple[set[int], set[int]]":
    """Return the ids of *rows* and of every movement they hold.

    **Its purchases leave the books, and so does its PAYMENT**: a delete
    takes every entry under the row, on either arm (ruling **R-CC75**) --
    through the act that takes a movement off the books
    (:mod:`app.services.movement_removal`, plan step
    ``credit_card:CC-5-4a-3``) before the row itself goes -- and a match
    naming one loses that member -- a purchase's member, or the covering
    movement's that every act names for a settled row (ruling **R-CC43**:
    every act since plan step ``credit_card:CC-5-4a-1``, and every older one
    since migration ``2eabfa596ee0`` re-keyed it, ruling **R-CC45**);
    ``row.entries`` holds both.  The ROW ids are returned too, for
    :func:`_summarise`: an act's creations may name a row.

    Args:
        rows: The transactions whose movements the press takes off, each with
            ``entries`` accessible -- the row AND its live CC-payback chain,
            because those go down in the same commit and a dialog that named
            only the first would understate the press.  A recurring row is
            among them though it stays in the table as an emptied tombstone
            (ruling **R-CC75**): the owner deleted it, so it counts as
            leaving (ruling **R-CC84**).

    Returns:
        ``(transaction_ids, entry_ids)``.
    """
    return (
        {row.id for row in rows},
        {entry.id for row in rows for entry in row.entries},
    )


#: What the event says when rows left the books with their movements -- the
#: row, purchase, payback and transfer deletes (the act's ``because``).
LEFT_THE_BOOKS = (
    "Rows left the books and took the last app row of the matches naming "
    "them; those bank lines are unexplained again."
)

#: What the re-point door's event says happened (ruling **R-CC46**).  Its own
#: sentence because the rows STAY on the books, and production is observed
#: through these lines.
MOVED_ACCOUNTS = (
    "A payment moved to another account and took the last app row of the "
    "matches naming it; those bank lines are unexplained again."
)

#: What the status seam's event says when a settle's own record withdraws its
#: payment -- a ``$0.00`` figure, or the row's purchases becoming its record
#: (plan step ``credit_card:CC-5-4a-3``, finding **CC-358**).  Its own
#: sentence because the ROW stays and only its payment went.
RE_RECORDED = (
    "A payment was re-recorded as nothing, or as its row's purchases, and "
    "took the last app row of the matches naming it; those bank lines are "
    "unexplained again."
)


def _withdraw(
    acts, planned: MatchWithdrawal, owner_id: int, *, because: str, **fields,
) -> None:
    """Delete the acts and record what that freed.

    The members go with each act through the ORM cascade and the composite
    foreign key alike, which is what puts the lines back among the unexplained:
    no member names them any more.

    Args:
        acts: The acts to withdraw.
        planned: What :func:`_summarise` said they come to, so the event
            records the same figures the dialog printed.
        owner_id: The user the caller proved owns the account.
        because: The event's sentence -- the DOOR's, since a delete, a
            re-record and a re-point withdraw for different reasons and the
            log is read.
        **fields: Subject coordinates for the event (``transaction_ids``,
            ``transfer_ids``, ``transaction_entry_ids`` and
            ``freed_line_ids``), and ``silent_by``: the ruling or finding a
            no-caption door named (:class:`Silent`), else ``None``.
    """
    for act in acts:
        db.session.delete(act)
    db.session.flush()
    log_event(
        logger, logging.INFO, EVT_STATEMENT_MATCH_WITHDRAWN, BUSINESS,
        because,
        user_id=owner_id,
        match_count=planned.matches,
        freed_line_count=len(planned.lines),
        kept_row_count=planned.kept_rows,
        **fields,
    )


def pending_for_rows(rows) -> MatchWithdrawal:
    """Return what deleting *rows* would withdraw, WITHOUT withdrawing it.

    The read half, for the confirm dialog on a delete control: what
    :func:`app.services.movement_removal.remove_movements` withdraws when
    the delete verb hands it every movement of *rows* with *rows* leaving.
    A recurring row's tombstone is among them and counts as leaving (ruling
    **R-CC84**: the owner deleted it, so no creation naming it is reported
    as kept).  Runs on a popover render: one member query always, and the act
    and line queries only where an act actually names one of these subjects.

    Args:
        rows: The transactions a screen is offering to delete -- the row the
            owner pressed AND everything that goes down with it.

    Returns:
        Its :class:`MatchWithdrawal`.  All zeroes when no act would be emptied,
        which is every row on a book nobody has matched.
    """
    transaction_ids, entry_ids = _subject_ids(rows)
    emptied = _acts_emptied_by(entry_ids)
    return _summarise(
        emptied, transaction_ids, entry_ids, _freed_lines(emptied),
    )


def pending_for_movements(entries) -> MatchWithdrawal:
    """Return what taking *entries* out of their matches would withdraw, without doing it.

    **ONE read for every door that takes a single movement out** (plan step
    ``credit_card:CC-5-4a-3``): a purchase the owner deletes, a payment a
    settle's record withdraws (``$0.00``, or the row's purchases), and a
    payment re-pointed onto another account (ruling **R-CC46**) all leave
    the same acts, because an act names the MOVEMENT and none of the three
    keeps it named.  It was two functions until then --
    ``pending_for_purchase`` and ``pending_for_moved_movement``, the second
    returning the first -- which is one read with two names.  The screens
    that offer those acts read it: the bill popover's three captions (the
    "Paid from" pick, the Actual box's ``$0.00``, and Paid on a row whose
    purchases would replace its payment, ruling **R-CC56**), and the
    transfer popover's ``$0.00``, which takes BOTH legs' payments off at once
    -- the reason it takes a set.

    Like the delete dialog it names the ACTS the removal empties and the
    lines those free; a GROUP act that keeps another row is not named here,
    and the act still takes this member out of it and turns its ``agrees``
    flag amber on the register, stated so it reads as a choice and not a fact.
    The purchase list reads the same derivation for many purchases at once
    (:func:`pending_for_each`, plan step ``credit_card:CC-5-4a-5``, ruling
    **R-CC80**).

    Args:
        entries: The movements a screen is offering to remove or re-point.

    Returns:
        Their :class:`MatchWithdrawal`.
    """
    return pending_for_each({None: entries})[None]


def pending_for_each(
    removals: "dict[Hashable, Iterable]",
    rows_leaving: "dict[Hashable, Iterable] | None" = None,
) -> "dict[Hashable, MatchWithdrawal]":
    """Return what each of several removals would withdraw, in ONE read.

    :func:`pending_for_movements` for a screen offering MANY removals at once
    -- the purchase list's X on each of an envelope's purchases, drawn for
    every envelope on the grid (plan step ``credit_card:CC-5-4a-5``, ruling
    **R-CC80**) -- so the page asks one member query, one act load and one
    line query however many it offers, rather than three per purchase.
    Each removal is answered exactly as :func:`take_out_of_matches` would
    answer it alone: the acts naming its movements, split by
    :func:`_partition`, the emptied ones summarised.

    Args:
        removals: ``{key: movements}`` -- each value one press's movements,
            under whatever key the caller reads its answer back by.
        rows_leaving: ``{key: rows}`` -- the rows a removal's press deletes
            with its movements (a CC payback the last card purchase's X takes
            down), as :func:`take_out_of_matches` is handed them, so a
            creation naming one is not counted as kept.  A key absent here
            deletes no row.

    Returns:
        ``{key: MatchWithdrawal}``, one per key given.  All zeroes for a
        removal no act names, which is nearly every one.
    """
    leaving = rows_leaving or {}
    groups = {
        key: {entry.id for entry in entries}
        for key, entries in removals.items()
    }
    acts = _acts_naming(set().union(*groups.values()))
    emptied = {
        key: _partition(
            [
                act for act in acts
                if any(member.transaction_entry_id in ids for member in act.members)
            ],
            ids,
        )[0]
        for key, ids in groups.items()
    }
    lines = _freed_lines([act for each in emptied.values() for act in each])
    return {
        key: _summarise(
            emptied[key], {row.id for row in leaving.get(key, ())},
            groups[key], lines,
        )
        for key in groups
    }


def _parent_ids(entries, leaving_ids: "set[int]") -> "dict[str, list[int]]":
    """Return the withdrawal event's parent coordinates for *entries*.

    What each movement was recorded FOR, asked of the one resolution of a
    movement's parent (:func:`app.services.transfer_legs.movement_parent`,
    plan step ``balance:X-bi-6-4c-4``, ruling **R-BAL160**) and split in one
    pass: a plan row under ``transaction_ids``, with the rows the press
    deletes beside it, and a transfer's payment under ``transfer_ids``.  It
    read the shadow row's id off a transfer's payment until that step -- an
    id ``X-bi-6-4d`` leaves unset, and one ``sorted`` could not have ordered
    beside the others.  The rows a transfer delete hands over as leaving are
    its shadows, so that delete still lists them until the shadows go.

    Args:
        entries: The movements leaving their matches.
        leaving_ids: The ids of the rows the press deletes, soft or hard.

    Returns:
        ``{"transaction_ids": [...], "transfer_ids": [...]}``, each sorted.
    """
    row_ids, transfer_ids = set(leaving_ids), set()
    for entry in entries:
        parent = transfer_legs.movement_parent(entry)
        if isinstance(parent, TransferLeg):
            transfer_ids.add(parent.transfer.id)
        else:
            row_ids.add(parent.id)
    return {
        "transaction_ids": sorted(row_ids),
        "transfer_ids": sorted(transfer_ids),
    }


def take_out_of_matches(
    entries, owner_id: int, *, because: str, shown: "Shown | Silent",
    rows_leaving=(),
) -> MatchWithdrawal:
    """Take *entries* out of every act naming them; withdraw the acts that empties.

    The MATCH STEP of the one act that takes a movement off the books
    (:func:`app.services.movement_removal.remove_movements`, plan step
    ``credit_card:CC-5-4a-3``, ruling **R-CC54**), and the whole of the
    seam's re-point (:func:`withdraw_for_moved_movement`).  Two things,
    over ONE loaded set (:func:`_acts_naming`):

    * **an act left naming no app row is WITHDRAWN** -- deleted with its
      members and its creation records, its freed lines logged -- on the
      narrowest condition the module docstring states, read by the same
      predicate the dialogs read (:func:`_loses_every_row`);
    * **an act that KEEPS another app row loses this member**, taken out of
      its loaded ``members`` collection so the session and the table agree
      (``delete-orphan`` issues the DELETE).  The re-point spelled this as a
      bulk ``DELETE`` with ``synchronize_session=False`` until this step,
      which left the surviving act's already-loaded collection holding a
      member the table no longer had.

    FLUSHED before returning WHEN IT CHANGED SOMETHING (the withdrawal's own
    flush, or a member taken out), for the re-point's reason (the unit of
    work orders a DELETE after an UPDATE, so a member left to the same flush
    as a movement's account assignment would be checked against it and refuse
    it) and for the act's: the members are gone before the movement is.  An
    unmatched movement -- nearly every one -- flushes nothing here, so the
    status seam run inside a caller's ``no_autoflush`` block (the
    carry-forward batch) writes no earlier than it did before this step.

    **It asks what the owner was SHOWN** (plan step
    ``credit_card:CC-5-4a-5``, rulings **R-CC81** / **R-CC127**): *shown* is
    REQUIRED, and a press whose freed lines differ from what its page named
    is refused before anything is written (:func:`_refuse_unshown`).  The
    doors above the seam pass it down from the settle verbs, whose default
    is :data:`NOTHING_SHOWN`.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        entries: The movements leaving their matches -- the whole set one
            press removes, so a group act naming two of them is withdrawn
            once and a creation naming one is not reported as kept.
        owner_id: The owner under whose books the acts are filed.
        because: The event's sentence (:data:`LEFT_THE_BOOKS`,
            :data:`RE_RECORDED`, :data:`MOVED_ACCOUNTS`).
        shown: The lines the door's page named (:class:`Shown`), or what
            lets it stay silent (:class:`Silent`).
        rows_leaving: The rows going in the same press, soft or hard (a
            recurring occurrence's tombstone counts as gone, ruling
            **R-CC84**), when the caller is a row delete -- so a creation that
            names one is not reported as kept (:func:`_summarise`).  Empty
            when only movements go.

    Returns:
        What was withdrawn, as the dialog's read would have printed it.

    Raises:
        PageOutOfDate: When what this frees differs from *shown*.
        ValidationError: An :class:`OwnerOnly` press's own refusal, when it
            would free a line (ruling **R-CC130**).
    """
    entry_ids = {entry.id for entry in entries}
    leaving_ids = {row.id for row in rows_leaving}
    emptied, surviving = _partition(_acts_naming(entry_ids), entry_ids)
    planned = _summarise(
        emptied, leaving_ids, entry_ids, _freed_lines(emptied),
    )
    _refuse_unshown(shown, entries, planned)
    if emptied:
        _withdraw(
            emptied, planned, owner_id, because=because,
            # The PARENTS the movements were under, whether or not they leave
            # too -- a re-record's row stays, and the event is the only record
            # of which row a no-caption door (the grid's Mark Paid) touched.
            **_parent_ids(entries, leaving_ids),
            transaction_entry_ids=sorted(entry_ids),
            freed_line_ids=[line.line_id for line in planned.lines],
            # What let a no-caption door withdraw (ruling **R-CC81**).
            silent_by=shown.because if isinstance(shown, Silent) else None,
        )
    taken = [
        (act, member) for act in surviving for member in act.members
        if member.transaction_entry_id in entry_ids
    ]
    for act, member in taken:
        act.members.remove(member)
    if taken:
        db.session.flush()
    return planned


def withdraw_for_moved_movement(
    entry, owner_id: int, *, shown: "Shown | Silent",
) -> MatchWithdrawal:
    """Take *entry* out of every act naming it; withdraw the acts that empties.

    The door a movement's ACCOUNT change calls BEFORE the account is
    assigned (``status_seam._covering._re_point``; module docstring, ruling
    **R-CC46**).  A moved movement stays on the books, so it does not go
    through the act that takes one off them -- only through that act's match
    step (:func:`take_out_of_matches`): the key would refuse the move while
    the member stands, so the member is taken out and FLUSHED first.  A
    group act that keeps another row keeps standing, its ``agrees`` flag the
    re-review, exactly as after a purchase's delete.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        entry: The covering movement about to be re-pointed.
        owner_id: The row's owner, under whose books the act is filed.
        shown: What the door's page named, or what lets it stay silent
            (:func:`take_out_of_matches`).

    Returns:
        What was withdrawn.

    Raises:
        PageOutOfDate: When what this frees differs from *shown*.
        ValidationError: An :class:`OwnerOnly` press's own refusal, when it
            would free a line (ruling **R-CC130**).
    """
    return take_out_of_matches(
        [entry], owner_id, because=MOVED_ACCOUNTS, shown=shown,
    )
