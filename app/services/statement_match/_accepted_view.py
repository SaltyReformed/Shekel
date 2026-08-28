"""What an ACCEPTED match looks like now, and whether it still holds.

Plan step ``bank_import:X-f6a-2`` built this reader; plan step
``bank_import:X-f6a-3d`` moved it out of :mod:`._reads`, which had grown past
the 1,000-line module ceiling.  The boundary is a real one rather than a place
to cut: :mod:`._reads` answers *what is there to do*, and this answers *what
was already done and does it still say what it said*.

**They are read by two different SCREENS since plan step
``bank_import:X-gf-2``** (ruling **bank_import:R-GX**), which is the same
boundary made visible: the review screen is the exception queue, and every act
already accepted is on the register, where it is found and undone.  So this
module no longer runs inside the review pass at all -- it was valuing 221 acts
on the developer's own account on every render of a page whose panel for them
he was not reading -- and it needs no :class:`~._scope.ReviewScope`, no
calendar and no candidate derivation of its own.

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
from app.models.statement_import import BankStatementLine
from app.models.transaction import Transaction
from app.services import cash_ledger
from app.utils.balance_predicates import is_balance_contributing
from app.utils.log_events import (
    ERROR,
    EVT_STATEMENT_MATCH_LINELESS,
    log_event,
)

from ._release import (
    PlannedRemovals,
    acts_of,
    planned_removals,
)
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
class AcceptedGroup:  # pylint: disable=too-many-instance-attributes
    """One accepted match, as the screen lists it.

    Pylint: ``too-many-instance-attributes`` (8/7) -- **eight because the two
    surfaces that render an act read eight disjoint facts about it**, not
    because the value wants splitting.  The eighth is
    :attr:`applied_by_rule`, which arrived with plan step ``bank_import:X-ge``
    and is the whole reason ruling **R-GT** stores a column at all: WHICH rule
    fired is derivable from the matched line, and THAT one fired is not, so a
    reader that could not see it could not tell an act the owner pressed from
    one the app performed for them.  Dropping a field to meet a limit is what
    :class:`~._accept.AcceptedMatch`'s own disable records the cost of: the
    receipt said *"Nothing moved."* over a rewritten figure.  The obvious
    grouping -- :attr:`agrees` beside :attr:`removes` beside this -- is three
    facts a template reads separately and one condition each, so a nested value
    would be the speculative shape ``CLAUDE.md`` rule 13 forbids.
    :class:`~._creations.PurchaseDestination`, :class:`~._offers.CandidateRow`,
    :class:`~._creations.CreatedPurchase` and
    :class:`~._batch.BatchOutcome` carry the same disable for the same reason.

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
        applied_by_rule: Whether a STANDING RULE performed this act rather than
            a person ticking it (ruling **R-GT**, plan step
            ``bank_import:X-ge``).  **The column exists so that this can be
            SHOWN**: it is the one fact about an act that is not derivable from
            what the act names, which is R-GT's own argument for storing it and
            against a foreign key to the rule row -- and a fact written and
            never seen is the shape this arc keeps finding.  It is what lets
            the import receipt list exactly the acts nobody pressed, and what
            lets the register say which of the accepted matches the owner
            agreed to line by line.  **No default, which is the discipline every
            other link in this chain keeps**: ``ReviewedBatch.consent``,
            ``create_purchase_from_line``'s keyword-only flag and the column
            itself all refuse one, because the two values are *the owner agreed
            to this* and *the app did it on their behalf* -- and a default
            claims the first by omission.  It defaulted to ``False`` until an
            adversarial security review named it, 2026-08-26: the DISPLAY half
            of a consent fact may no more assume consent than the writing half.
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
    applied_by_rule: bool


def accepted_groups(
    owner_id: int, account_id: int, match_ids: "set[int] | None" = None,
) -> "list[AcceptedGroup]":
    """Return this account's accepted matches, newest first.

    Args:
        owner_id: The user whose matches to list.
        account_id: The account.
        match_ids: The acts to describe, or ``None`` for all of this
            account's.  **It is :func:`~._release.acts_of`'s own parameter,
            surfaced rather than reimplemented** (plan step
            ``bank_import:X-ge``): the import receipt names a SUBSET -- the
            acts a standing rule performed -- and a caller filtering this
            function's output instead would load and value every act on the
            account to render twenty, which is the unbounded fold an
            adversarial review measured at 475 queries one reader over.  An
            empty set means no act, and returns nothing rather than
            everything, which is that function's rule and the only reading
            that does not turn a filter into its own opposite.

    Returns:
        One :class:`AcceptedGroup` per act.  A group whose rows no longer carry
        the day it asserted is flagged rather than hidden: it is the shape a
        later hand edit produces, and the screen is where it can be re-reviewed.
    """
    # ONE loader, shared with the import page's delete preview
    # (:func:`~._release.acts_of`): an act is only readable with BOTH its
    # relations AND the row each of them names -- what it names decides
    # whether it still holds, and what it created decides what the Undo
    # control would take back.  It narrows by the owner as well as the
    # account, which is the pair the write door itself uses, and every subject
    # below arrives through a join carrying that account
    # (:data:`~._release._WHOLE_ACT`, finding **bank_import:N-358**).
    #
    # **That replaced three by-id reads and a warm** (plan step
    # ``bank_import:X-gf-2``).  This fold collected the member ids and selected
    # the lines, transactions and purchases back by primary key alone, and
    # separately warmed the creations' subjects into the identity map -- a warm
    # whose result had to be held, because that map's references are weak.
    # Nothing leaked through the by-id reads, every id having come from a
    # scoped act, but that is safety by DERIVATION over an open set of future
    # callers; and a discipline about holding a variable is one a reader can
    # drop without anything saying so.  Both are now properties of how the act
    # is loaded.
    matches = acts_of(owner_id, account_id, match_ids)
    if not matches:
        return []

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
            member.line for member in match.members
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
                member.transaction
                if member.transaction_id is not None else member.entry,
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
            applied_by_rule=match.applied_by_rule,
        ))
    return groups


#: How many SETTLED acts the register renders before it stops.
#:
#: **A bound on what is RENDERED, never on what is read** (ruling
#: **bank_import:R-GX**, developer 2026-08-27).  Whether an act still holds is
#: a VALUATION over its member rows (:func:`_still_holds`) and not a query, so
#: the fold has to reach every act to know which ones no longer do -- and an
#: act that no longer holds is the one thing on that page worth finding.  So
#: every such act is rendered whatever its age, and the bound falls only on the
#: remainder, which is a log.
#:
#: Measured on the developer's own data 2026-08-27: 221 acts, 216,637 bytes,
#: 0 of them out of agreement -- so this cuts the register's own card to about
#: a quarter of that, and cuts nothing an owner is looking for.
REGISTER_LIMIT = 50


@dataclass(frozen=True)
class AcceptedRegister:
    """The accepted acts a register renders, and how many it withheld.

    Plan step ``bank_import:X-gf-2``, ruling **bank_import:R-GX**.  **The
    count travels with the rows** because a truncated list that does not say
    it is truncated is a page claiming to be the whole record -- and this one
    is the only surface where an act can be found and undone.

    Attributes:
        shown: The acts to render: every one that NO LONGER HOLDS, newest
            first, then the newest :data:`REGISTER_LIMIT` of the rest.  The
            two orders are one sort, so an act that stops agreeing rises to
            the top of the page rather than staying where its date put it.
        withheld_count: How many acts the bound left out -- ``0`` when the
            whole record is on screen, which is what tells the surface whether
            to offer the link that shows everything.
    """

    shown: "tuple[AcceptedGroup, ...]"
    withheld_count: int


def accepted_register(
    owner_id: int, account_id: int, limit: "int | None" = REGISTER_LIMIT,
) -> AcceptedRegister:
    """Return this account's accepted acts as the register renders them.

    Args:
        owner_id: The user whose acts to list.
        account_id: The account.
        limit: How many SETTLED acts to render, or ``None`` for all of them --
            which is what the *show everything* link asks for.  An act that no
            longer holds is never subject to it.

    Returns:
        The :class:`AcceptedRegister`.
    """
    groups = accepted_groups(owner_id, account_id)
    # **Stable, so the second key is the order** :func:`~._release.acts_of`
    # **already returned** (newest first) rather than a second sort restating
    # it -- and so an act that stops agreeing moves to the top without
    # disturbing anything else's order.
    ordered = sorted(groups, key=lambda group: group.agrees)
    if limit is None:
        return AcceptedRegister(shown=tuple(ordered), withheld_count=0)
    asking = sum(1 for group in ordered if not group.agrees)
    keep = asking + limit
    return AcceptedRegister(
        shown=tuple(ordered[:keep]),
        withheld_count=max(len(ordered) - keep, 0),
    )


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
