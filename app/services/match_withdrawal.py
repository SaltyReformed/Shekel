"""What a bank line's match means once the app row it named has gone.

**A match ASSERTS an identity** -- *these bank lines and these app rows are one
movement* (:mod:`app.models.statement_match`).  Destroying one of those rows
makes the assertion false, so the assertion is withdrawn and the bank lines it
explained become unexplained again.  That is the whole of this module, and
every door that removes a matchable subject calls it.

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

**The claim "every door" is NOT made here, because it was measured false.**
Five doors call this rule: the transaction delete verb, the purchase door, both
CC-payback teardowns and the transfer delete.  At least four more can leave an
act with no app row and do NOT call it -- ``routes/templates/crud``'s
hard-delete and archive bulk statements, ``pay_period_write.retire_paydays``'
cascade, and ``recurrence_engine``'s retire sweep (held today only by an
implication about ``settled_basis_id``).  A rule enforced by ENUMERATION is a
rule the next door forgets, and two of those doors are bulk SQL where a
per-row call does not fit.  **So the INVARIANT does not rest here**: it rests
on :func:`~app.services.statement_match.matched_subjects`' own predicate, which
stopped counting a bank line as explained while its act names no app row --
one clause, every door, present and future.  What this module adds on top is
the CLEANUP and the DISCLOSURE at the doors the owner actually presses: the
false record goes rather than lingering, and the dialog names the lines the
press frees.

**Why it is a leaf module and not part of** :mod:`app.services.statement_match`.
That package imports ``entry_service``, ``credit_workflow`` and
``transaction_service`` -- three of the five doors that call this -- so a rule
living there could not be reached from any of them.  It imports the two match
MODELS and nothing else in ``app.services``, which is what lets every door
above it call one rule instead of five spellings of it.

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


def _acts_emptied_by(
    account_id: int,
    transaction_ids: "set[int]",
    entry_ids: "set[int]",
) -> "list[StatementMatch]":
    """Return the acts these subjects are the LAST app rows of.

    Two statements.  The first finds every act naming any of the going
    subjects; the second loads those acts whole, and an act is kept only when
    every app-side member it holds is in the going set -- so a group that keeps
    a row keeps its act, and the ``agrees`` flag is what re-reviews it.

    Args:
        account_id: The account the subjects belong to.  A member is held to
            its subject's account by a composite key
            (``fk_statement_match_members_transaction_account``) and to its
            act's by another, so this scope is exact rather than conventional
            and no act of another owner is reachable.
        transaction_ids: Row ids about to leave the table.
        entry_ids: Purchase ids about to leave the table.

    Returns:
        The acts to withdraw, each with ``members`` and ``creations`` loaded.
        Empty for an ordinary delete, which is nearly every delete.
    """
    if not transaction_ids and not entry_ids:
        return []
    match_ids = {
        row[0]
        for row in db.session.query(StatementMatchMember.match_id)
        .filter(
            StatementMatchMember.account_id == account_id,
            db.or_(
                StatementMatchMember.transaction_id.in_(transaction_ids)
                if transaction_ids else db.false(),
                StatementMatchMember.transaction_entry_id.in_(entry_ids)
                if entry_ids else db.false(),
            ),
        )
        .all()
    }
    if not match_ids:
        return []
    acts = (
        db.session.query(StatementMatch)
        .filter(StatementMatch.id.in_(match_ids))
        .options(
            selectinload(StatementMatch.members),
            selectinload(StatementMatch.creations),
        )
        .all()
    )
    return [act for act in acts if _loses_every_row(act, transaction_ids, entry_ids)]


def _loses_every_row(
    act: StatementMatch,
    transaction_ids: "set[int]",
    entry_ids: "set[int]",
) -> bool:
    """Return whether *act* would name no app row once these subjects go.

    Args:
        act: The act, with ``members`` loaded.
        transaction_ids: Row ids about to leave the table.
        entry_ids: Purchase ids about to leave the table.

    Returns:
        ``True`` when every app-side member is in the going set.  An act
        holding no app-side member at all answers ``True`` -- it already
        asserts nothing, and taking it is the repair rather than a surprise.
    """
    return all(
        member.transaction_id in transaction_ids
        if member.transaction_id is not None
        else member.transaction_entry_id in entry_ids
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

    **Its purchases go with it**: ``transaction_entries.transaction_id`` is
    ``ON DELETE CASCADE``, so a hard delete takes them and a match naming one
    loses that member with the parent.

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


def _withdraw(acts, planned: MatchWithdrawal, owner_id: int, **fields) -> None:
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
        **fields: Subject coordinates for the event (``transaction_ids`` or
            ``transaction_entry_id``).
    """
    for act in acts:
        db.session.delete(act)
    db.session.flush()
    log_event(
        logger, logging.INFO, EVT_STATEMENT_MATCH_WITHDRAWN, BUSINESS,
        "Rows left the books and took the last app row of the matches naming "
        "them; those bank lines are unexplained again.",
        user_id=owner_id,
        match_count=planned.matches,
        freed_line_count=len(planned.lines),
        kept_row_count=planned.kept_rows,
        **fields,
    )


def pending_for_rows(rows) -> MatchWithdrawal:
    """Return what deleting *rows* would withdraw, WITHOUT withdrawing it.

    The read half, for the confirm dialog on a delete control.  Runs on a
    popover render: one member query always, and the act and line queries only
    where an act actually names one of these subjects.

    Args:
        rows: The transactions a screen is offering to delete -- the row the
            owner pressed AND everything that goes down with it.

    Returns:
        Its :class:`MatchWithdrawal`.  All zeroes when no act would be emptied,
        which is every row on a book nobody has matched.
    """
    transaction_ids, entry_ids = _subject_ids(rows)
    return _summarise(
        _acts_emptied_by(_account_of(rows), transaction_ids, entry_ids),
        transaction_ids, entry_ids,
    )


def _account_of(rows) -> "int | None":
    """Return the one account these rows belong to.

    Args:
        rows: The transactions going.  Every row that goes down with a source
            is on its account -- a CC payback is created on the source's
            (``credit_workflow.create_cc_payback_transaction``) -- so one scope
            covers the set.

    Returns:
        The account id, or ``None`` for an empty set.
    """
    return rows[0].account_id if rows else None


def withdraw_for_rows(rows, owner_id: int) -> MatchWithdrawal:
    """Withdraw every act *rows* would leave naming no app row.

    Called by the transaction delete verb for the row AND its live CC-payback
    chain in one act, and by the transfer delete for both shadows -- so what a
    press takes and what its receipt reports are one derivation.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        rows: The transactions leaving the books, each with ``entries``
            accessible.
        owner_id: The user the caller proved owns them.

    Returns:
        What was withdrawn, as the dialog would have printed it.
    """
    transaction_ids, entry_ids = _subject_ids(rows)
    acts = _acts_emptied_by(_account_of(rows), transaction_ids, entry_ids)
    if not acts:
        return MatchWithdrawal(matches=0, lines=(), kept_rows=0)
    planned = _summarise(acts, transaction_ids, entry_ids)
    _withdraw(
        acts, planned, owner_id,
        transaction_ids=sorted(transaction_ids),
    )
    return planned


def pending_for_purchase(entry) -> MatchWithdrawal:
    """Return what removing the purchase *entry* would withdraw, without doing it.

    The read twin of :func:`withdraw_for_purchase`, so a screen offering to
    remove a purchase can say what that frees before the press.

    Args:
        entry: The purchase a screen is offering to remove.

    Returns:
        Its :class:`MatchWithdrawal`.
    """
    entry_ids = {entry.id}
    return _summarise(
        _acts_emptied_by(entry.account_id, set(), entry_ids),
        set(), entry_ids,
    )


def withdraw_for_purchase(entry, owner_id: int) -> MatchWithdrawal:
    """Withdraw every act the purchase *entry* is the last app row of.

    Its parent is untouched, which is the difference from
    :func:`withdraw_for_rows`: removing one purchase from an envelope leaves
    the envelope and every other purchase in it asserting exactly what they
    did.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        entry: The purchase leaving the books.
        owner_id: The user the caller proved owns it -- the OWNER, not
            necessarily the requester, so a companion's delete is filed under
            the books it changed.

    Returns:
        What was withdrawn.
    """
    entry_ids = {entry.id}
    acts = _acts_emptied_by(entry.account_id, set(), entry_ids)
    if not acts:
        return MatchWithdrawal(matches=0, lines=(), kept_rows=0)
    planned = _summarise(acts, set(), entry_ids)
    _withdraw(acts, planned, owner_id, transaction_entry_id=entry.id)
    return planned
