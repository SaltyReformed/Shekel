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
re-review.  The two mechanisms now split on a predicate rather than shadowing
each other, and the predicate is ``_still_holds``' own first branch.

**A SOFT delete withdraws nothing, and the CALLER is what says so** -- the
going set is the rows that really leave the table
(``transaction_service._delete._leaves_the_table``,
``transfer_service.delete_transfer``'s ``if not soft``).  A member's foreign
key CASCADES only on a real ``DELETE``, so a soft-deleted row keeps its
membership and the act still names it; withdrawing anyway would destroy an
accepted act for a change a shipped button reverses -- ``transfers.templates``
un-archives through ``restore_transfer`` and ``transfer_recurrence`` restores
soft-deleted shadows during a maintain pass.  A first build asserted this fell
out of the cascade and it did not: the going set was the row regardless of arm,
and a soft delete withdrew.  A soft-deleted row that records nothing is still
:attr:`~app.services.statement_match.AcceptedGroup.agrees`' case, and that flag
covers it.

**What it does NOT do is remove rows the withdrawn act CREATED**, and the
asymmetry is deliberate.  ``release_match`` is the owner's UNDO -- *withdraw
this act, and take back what it made* -- and it refuses where the owner has
edited a created row since.  This is a different act: the owner asked to delete
ONE row, not to withdraw a decision.  What survives is COUNTED
(:attr:`MatchWithdrawal.kept_rows`), and counted over the rows that ACTUALLY
survive: a creation whose subject is in the going set is destroyed by the same
press, and reporting it as kept is the *"Nothing moved."* shape this arc has
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
another app row is taken out explicitly, as the move always did, rather than
left to the member key's cascade: the act is the one path a movement leaves
by, and plan step ``credit_card:CC-5-4a-4`` stops that key cascading.

**The claim "every door" is still NOT made here, because it is still false.**
Three BULK doors destroy movements without the act -- ``routes/templates/
crud``'s permanent delete (``definition_delete``), the account delete's
ghost rows, and ``pay_period_write.retire_paydays``' cascade -- because they
judge what they may destroy by a row's STATUS rather than by what it holds
(finding **CC-363**, measured: a permitted template delete erased a `$25.00`
purchase recorded from a bank line, and the act kept its line alone).  Ruling
**R-CC54** ends that at the root in plan step ``credit_card:CC-5-4a-4``: a row
holding a movement is history those doors keep, and neither of a match's keys
cascades.  Until then the INVARIANT rests on
:func:`~app.services.statement_match.matched_subjects`' own predicate, which
stops counting a bank line as explained while its act names no app row; that
step deletes it.  What this module adds on top is the CLEANUP and the
DISCLOSURE at the doors the owner actually presses: the false record goes
rather than lingering, and the dialog names the lines the press frees.

**Why it is a leaf module and not part of** :mod:`app.services.statement_match`.
That package imports ``entry_service``, ``credit_workflow`` and
``transaction_service`` -- three of the doors whose act calls this -- so a rule
living there could not be reached from any of them.  It imports the two match
MODELS and nothing else in ``app.services``, which is what lets the act and the
seam's move above it call one rule instead of a spelling each.

Services-boundary discipline (``CLAUDE.md`` Architecture): ORM rows in, a
frozen dataclass out, no Flask import.  It MUTATES and does NOT commit -- the
caller owns the unit of work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.statement_import import BankStatementLine
from app.models.statement_match import StatementMatch, StatementMatchMember
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
        kept_rows: How many rows those acts had CREATED that ACTUALLY survive
            the press -- reported rather than silent, because a row the owner
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
    away -- the false record :func:`~app.services.statement_match
    .act_still_names_a_row` stops counting but this module exists to remove,
    and the freed card line undisclosed by the dialog.

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
        ``True`` when every app-side member is in the going set.  An act
        holding no app-side member at all answers ``True`` -- it already
        asserts nothing, and taking it is the repair rather than a surprise.
    """
    return all(
        member.transaction_entry_id in entry_ids
        for member in act.members
        if member.bank_statement_line_id is None
    )


def _summarise(
    acts: "list[StatementMatch]",
    transaction_ids: "set[int]",
    entry_ids: "set[int]",
) -> MatchWithdrawal:
    """Return what withdrawing *acts* comes to, WITHOUT withdrawing them.

    Args:
        acts: The acts, with ``members`` and ``creations`` loaded.
        transaction_ids: Row ids about to leave the table, so a creation that
            names one is not reported as staying.
        entry_ids: Purchase ids about to leave the table, likewise.

    Returns:
        Their :class:`MatchWithdrawal`.
    """
    line_ids = {
        member.bank_statement_line_id
        for act in acts
        for member in act.members
        if member.bank_statement_line_id is not None
    }
    lines = (
        db.session.query(BankStatementLine)
        .filter(BankStatementLine.id.in_(line_ids))
        .order_by(BankStatementLine.posted_on, BankStatementLine.id)
        .all()
        if line_ids else []
    )
    return MatchWithdrawal(
        matches=len(acts),
        lines=tuple(
            FreedLine(
                line_id=line.id,
                posted_on=line.posted_on,
                amount=line.amount,
                description=line.description,
            )
            for line in lines
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
    """Return every row and purchase id that leaves the table with *rows*.

    **Its purchases go with it, and so does its PAYMENT**: a hard delete
    takes every entry under the row -- through the act that takes a movement
    off the books (:mod:`app.services.movement_removal`, plan step
    ``credit_card:CC-5-4a-3``) before the row itself goes -- and a match
    naming one loses that member -- a purchase's member, or the covering
    movement's that every act names for a settled row (ruling **R-CC43**:
    every act since plan step ``credit_card:CC-5-4a-1``, and every older one
    since migration ``2eabfa596ee0`` re-keyed it, ruling **R-CC45**);
    ``row.entries`` holds both.  The ROW ids are returned too, for
    :func:`_summarise`: an act's creations may name a row.

    Args:
        rows: The transactions about to be deleted, each with ``entries``
            accessible.  The delete verb passes the row AND its live CC-payback
            chain, because those go down in the same commit and a dialog that
            named only the first would understate the press.

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
    ``statement_match.matched_subjects`` stops counting a line whose act names
    no app row.

    Args:
        acts: The acts to withdraw.
        planned: What :func:`_summarise` said they come to, so the event
            records the same figures the dialog printed.
        owner_id: The user the caller proved owns the account.
        because: The event's sentence -- the DOOR's, since a delete, a
            re-record and a re-point withdraw for different reasons and the
            log is read.
        **fields: Subject coordinates for the event (``transaction_ids``,
            ``transaction_entry_ids`` and ``freed_line_ids``).
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
    Runs on a popover render: one member query always, and the act and line
    queries only where an act actually names one of these subjects.

    Args:
        rows: The transactions a screen is offering to delete -- the row the
            owner pressed AND everything that goes down with it.

    Returns:
        Its :class:`MatchWithdrawal`.  All zeroes when no act would be emptied,
        which is every row on a book nobody has matched.
    """
    transaction_ids, entry_ids = _subject_ids(rows)
    return _summarise(
        _acts_emptied_by(entry_ids), transaction_ids, entry_ids,
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
    flag amber on the register -- the same silence the purchase-delete
    dialog keeps over a group, stated so it reads as a choice and not a fact.

    Args:
        entries: The movements a screen is offering to remove or re-point.

    Returns:
        Their :class:`MatchWithdrawal`.
    """
    entry_ids = {entry.id for entry in entries}
    return _summarise(_acts_emptied_by(entry_ids), set(), entry_ids)


def take_out_of_matches(
    entries, owner_id: int, *, because: str, rows_leaving=(),
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

    Does NOT commit -- the caller owns the session boundary.

    Args:
        entries: The movements leaving their matches -- the whole set one
            press removes, so a group act naming two of them is withdrawn
            once and a creation naming one is not reported as kept.
        owner_id: The owner under whose books the acts are filed.
        because: The event's sentence (:data:`LEFT_THE_BOOKS`,
            :data:`RE_RECORDED`, :data:`MOVED_ACCOUNTS`).
        rows_leaving: The rows going in the same press, when the caller is a
            row delete -- so a creation that names one is not reported as
            kept (:func:`_summarise`).  Empty when only movements go.

    Returns:
        What was withdrawn, as the dialog's read would have printed it.
    """
    entry_ids = {entry.id for entry in entries}
    leaving_ids = {row.id for row in rows_leaving}
    emptied, surviving = _partition(_acts_naming(entry_ids), entry_ids)
    planned = _summarise(emptied, leaving_ids, entry_ids)
    if emptied:
        _withdraw(
            emptied, planned, owner_id, because=because,
            # The ROWS the movements were under, whether or not they leave
            # too -- a re-record's row stays, and the event is the only record
            # of which row a no-caption door (the grid's Mark Paid) touched.
            transaction_ids=sorted(
                leaving_ids | {entry.transaction_id for entry in entries},
            ),
            transaction_entry_ids=sorted(entry_ids),
            freed_line_ids=[line.line_id for line in planned.lines],
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


def withdraw_for_moved_movement(entry, owner_id: int) -> MatchWithdrawal:
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

    Returns:
        What was withdrawn.
    """
    return take_out_of_matches([entry], owner_id, because=MOVED_ACCOUNTS)
