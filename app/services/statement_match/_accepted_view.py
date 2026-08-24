"""What an ACCEPTED match looks like now, and whether it still holds.

Plan step ``bank_import:X-f6a-2`` built this reader; plan step
``bank_import:X-f6a-3d`` moved it out of :mod:`._reads`, which had grown past
the 1,000-line module ceiling.  The boundary is a real one rather than a place
to cut: :mod:`._reads` answers *what is there to do*, and this answers *what
was already done and does it still say what it said*.  They are read in one
request and share nothing but the account.

**Agreement is DERIVED, never stored** (:mod:`app.models.statement_match`).
Nothing records the day a match asserted -- that is ``max(posted_on)`` over its
bank lines, and each member row carries it in its own ``settled_on``.  So an
owner who later moves a day by hand puts the group out of agreement with the
bank, and :attr:`AcceptedGroup.agrees` SHOWS it rather than a release nobody
can see: the repair door finding **N-302** says a refusal owes.

Services-boundary discipline: reads only, plain data in, frozen dataclasses
out, no Flask import.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.exceptions import AmountUnresolvable
from app.extensions import db
from app.models.statement_import import BankStatementLine
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import cash_ledger
from app.utils.balance_predicates import is_balance_contributing
from app.utils.log_events import (
    ERROR,
    EVT_STATEMENT_MATCH_LINELESS,
    log_event,
)

from ._release import PlannedRemovals, acts_of, planned_removals
from ._sides import MatchSides

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AcceptedRow:
    """One member of an accepted match, as the screen lists it.

    Attributes:
        label: What to call the app row.
        settled_on: The day it currently records, which is the bank's own day
            for a match still in agreement.
        cash_amount: Its signed cash effect NOW, or ``None`` when the amount
            model can no longer price it.  Carried because
            :func:`_still_holds` re-asks the balance the accept door checked,
            and a member that has since been soft-deleted contributes zero --
            which no test over days could see.
        agrees: Whether that day is still the one the match asserted.
    """

    label: str
    settled_on: "date | None"
    cash_amount: "Decimal | None"
    agrees: bool


@dataclass(frozen=True)
class AcceptedGroup:
    """One accepted match, as the screen lists it.

    Attributes:
        match_id: The act, so the screen can offer to release it.
        posts_on: The day the match asserted -- the latest of its bank days,
            derived here rather than stored (see
            :mod:`app.models.statement_match`).
        amount: The signed total its bank lines state.
        descriptions: What the bank called each line.
        rows: Its app rows.
        removes: What an Undo would take back
            (:class:`~._release.PlannedRemovals`) -- the rows this act CREATED
            and the money the account would stop recording.  **From the release
            door's OWN derivation** (:func:`~._release.planned_removals`),
            which is the shape :func:`~._preview.preview_hand_build` already
            has for the accept door: two spellings would let the screen promise
            one thing and the button do another, and this button destroys
            records.  It is empty for every match between rows that already
            existed, which is most of them.
        agrees: Whether the match still HOLDS -- which is three questions, not
            one, and a first draft asked only the first.  Every row still
            carries ``posts_on``; the act still names at least one row; and the
            rows still SUM to what the bank stated.  The second and third are
            what a CASCADE breaks: deleting a purchase or destroying a pay
            period removes that member silently, and a day-only test then
            reports a group that explains less than it claims -- or, when every
            row goes, nothing at all -- as still agreeing with the bank.  False
            is not a corruption; it means the match wants re-reviewing, and the
            screen offers it for exactly that.
    """

    match_id: int
    posts_on: date
    amount: Decimal
    descriptions: "tuple[str, ...]"
    rows: "tuple[AcceptedRow, ...]"
    agrees: bool
    removes: PlannedRemovals


def accepted_groups(
    owner_id: int, account_id: int,
) -> "list[AcceptedGroup]":
    """Return this account's accepted matches, newest first.

    Args:
        owner_id: The user whose matches to list.
        account_id: The account.

    Returns:
        One :class:`AcceptedGroup` per act.  A group whose rows no longer carry
        the day it asserted is flagged rather than hidden: it is the shape a
        later hand edit produces, and the screen is where it can be re-reviewed.
    """
    # ONE loader, shared with the import page's delete preview
    # (:func:`~._release.acts_of`): an act is only readable with BOTH its
    # relations -- what it names decides whether it still holds, and what it
    # created decides what the Undo control would take back.  It narrows by
    # the owner as well as the account, which is the pair the write door
    # itself uses.
    matches = acts_of(owner_id, account_id)
    if not matches:
        return []

    member_rows = [member for match in matches for member in match.members]
    lines = _by_id(BankStatementLine, {
        member.bank_statement_line_id for member in member_rows
        if member.bank_statement_line_id is not None
    })
    transactions = _by_id(Transaction, {
        member.transaction_id for member in member_rows
        if member.transaction_id is not None
    })
    entries = _by_id(TransactionEntry, {
        member.transaction_entry_id for member in member_rows
        if member.transaction_entry_id is not None
    })

    groups = []
    for match in matches:
        # **Every act has at least one line, and the guarantee is three parts
        # rather than one** (plan step ``bank_import:X-f6a-4``): ``record_match``
        # refuses an empty side at the ONE writer,
        # ``fk_statement_match_members_line_account`` no longer cascades so the
        # database refuses to remove a line a match names, and migration
        # ``e4a7c0f13b92`` DELETED the acts that already held none -- a foreign
        # key cannot see an absence, so the third part is not implied by the
        # second.
        #
        # There USED to be a guard here skipping a lineless act, and skipping it
        # was the defect: such an act is invisible on this screen and yet still
        # claims its transactions in ``matched_subjects``, so those rows could
        # never be matched again and no release button existed to free them.
        # The skip is BACK and it is no longer a silence: the act is logged at
        # ERROR naming the row to delete.  Two adversarial reviews 2026-08-20
        # measured why each of the three alternatives is worse.  Skipping
        # SILENTLY was the original defect.  RAISING takes the whole review
        # surface down for the account -- including the release control that
        # would repair it -- which is the rule ``_accepted_row`` below already
        # states for its own degraded case, in as many words: a raise here "would
        # make the screen permanently unreachable for the account, with no
        # in-app repair, which is finding N-302's shape".  Rendering a husk
        # would need a group with no day, no amount and no wording, which is
        # not a group.  So the screen stays up, the operator is told, and the
        # state itself is what the three guarantees above prevent.
        match_lines = [
            lines[member.bank_statement_line_id] for member in match.members
            if member.bank_statement_line_id is not None
        ]
        if not match_lines:
            log_event(
                _logger, logging.ERROR, EVT_STATEMENT_MATCH_LINELESS, ERROR,
                "An accepted match names no bank line.",
                account_id=account_id, match_id=match.id,
            )
            continue
        posts_on = max(line.posted_on for line in match_lines)
        rows = [
            _accepted_row(
                transactions[member.transaction_id]
                if member.transaction_id is not None
                else entries[member.transaction_entry_id],
                posts_on,
            )
            for member in match.members
            if member.transaction_id is not None
            or member.transaction_entry_id is not None
        ]
        groups.append(AcceptedGroup(
            match_id=match.id,
            posts_on=posts_on,
            amount=sum(
                (Decimal(str(line.amount)) for line in match_lines),
                Decimal("0.00"),
            ),
            descriptions=tuple(line.description for line in match_lines),
            rows=tuple(rows),
            agrees=_still_holds(rows, match_lines, posts_on),
            removes=planned_removals(match),
        ))
    return groups


def _accepted_row(row, posts_on: date) -> AcceptedRow:
    """Return one member of an accepted match, valued as it stands NOW.

    **The valuation is the cash ledger's, and it is what makes a soft-deleted
    member visible.**  ``settled_cash_leg`` answers ``0.00`` for a row that
    contributes nothing -- soft-deleted, Credit or Cancelled -- so a member
    that has quietly left the balance shows up in :func:`_still_holds`'s sum
    even though its recorded day is untouched.  A purchase takes the same gate
    through its PARENT, which is ruling **R-FM**: a non-contributing row's
    purchases post nothing either.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transaction_entry.TransactionEntry` the member
            names.
        posts_on: The day the match asserted.

    Returns:
        Its :class:`AcceptedRow`.
    """
    if isinstance(row, Transaction):
        try:
            amount = cash_ledger.settled_cash_leg(row)
        except AmountUnresolvable:
            # **A member the amount model cannot price stops the match holding
            # rather than stopping the page.**  This row is already a match
            # MEMBER, so it is read on every load and the owner cannot
            # un-select it -- a raise here would make the screen permanently
            # unreachable for the account, with no in-app repair, which is
            # finding N-302's shape.  ``None`` propagates into
            # :func:`_still_holds` as a sum that cannot be taken, so the group
            # is offered for re-review, which is the honest answer.
            amount = None
        return AcceptedRow(
            label=row.name, settled_on=row.settled_on,
            cash_amount=amount,
            agrees=row.settled_on == posts_on,
        )
    # **A CARD purchase moves no cash through THIS account at all** -- it
    # leaves later through its own CC Payback sibling, which is why
    # ``credit_entry_sum`` removes it from its parent's leg and the posted
    # walk filters it out.  ``update_entry`` supports that flip, and the
    # purchase keeps its ``settled_on`` through it, so a valuation reading the
    # magnitude alone reported a match as still explaining money that had
    # stopped being on this statement.  Found by adversarial financial review
    # 2026-08-17.
    explains_cash = (
        is_balance_contributing(row.transaction) and not row.is_credit
    )
    return AcceptedRow(
        label=row.description, settled_on=row.settled_on,
        cash_amount=(
            -Decimal(str(row.amount)) if explains_cash else Decimal("0.00")
        ),
        agrees=row.settled_on == posts_on,
    )


def _still_holds(
    rows: "list[AcceptedRow]",
    lines: "list[BankStatementLine]",
    posts_on: date,
) -> bool:
    """Return whether an accepted match still says what it said when accepted.

    Three questions, because a CASCADE can falsify a match without touching a
    single day:

    * it still names at least one app row (``all([])`` is True, so a match that
      lost every row would otherwise report agreement while explaining nothing,
      and its bank line would stay off the unexplained list permanently);
    * every row still carries the day the match asserted;
    * the rows still SUM to what the bank stated -- the invariant
      :func:`~._accept.accept_match` checks before it writes, asked again of
      what survives.

    Args:
        rows: The act's app-row members as the screen holds them.
        lines: Its bank lines.
        posts_on: The day it asserted.

    Returns:
        Whether the match still holds.  A soft-deleted member is caught by the
        SUM rather than by the day: it keeps its ``settled_on`` and contributes
        nothing to any balance, so only the total can see it has gone.
    """
    if not rows:
        return False
    if any(row.cash_amount is None for row in rows):
        return False
    if any(row.settled_on != posts_on for row in rows):
        return False
    # **Through the door's OWN derivation** (plan step ``bank_import:X-f6d-4``).
    # This reader summed and rounded the two sides itself until then, with a
    # comment saying it must match "``_accept._reject_unbalanced``" -- a
    # function that has not existed for two steps.  Two spellings of one
    # invariant is a match the door accepted that this reader calls broken
    # forever, and a citation that resolves to nothing is how the second
    # spelling survived.  ``MatchSides.of`` is STRUCTURALLY typed over anything
    # exposing ``amount`` / ``cash_amount``, which is exactly why these two
    # shapes can reach it.
    return not MatchSides.of(lines, rows).difference


def _by_id(model, ids: "set[int]") -> dict:
    """Return ``{id: row}`` for *ids*, in one statement or none at all.

    Args:
        model: The mapped class to load.
        ids: The primary keys wanted.  Empty issues no query -- ``IN ()`` is a
            statement with no rows to find.

    Returns:
        The rows by id.
    """
    if not ids:
        return {}
    return {
        row.id: row
        for row in db.session.query(model).filter(model.id.in_(ids)).all()
    }
