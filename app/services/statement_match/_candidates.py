"""Which of the app's rows a bank line could be or become, priced as it sees them.

The OFFER-SET half of the matcher.  Two questions, and they are here together
because they are the same question about two acts:

* :func:`candidates_for` -- *what has this account recorded that a statement
  could be showing*, over the three subjects the app holds a cash movement
  as (:class:`~._subjects.RowKind`), each priced with its SIGNED effect on the
  account so a comparison against ``bank_statement_lines.amount`` is a
  subtraction rather than a sign negotiation;
* :func:`~._destinations.destinations_for` -- *what budget line could a
  statement line BECOME a purchase against*, which is ruling **R-FS**'s third
  shape.  **In its own module since plan step ``balance:X-bi-7b``**, when
  this one crossed the 1,000-line bound (ruling **balance:R-IR**); the seam is
  this paragraph's, and the pass still derives the two together.

**Both are ONE scope shared by the screen that offers and the door that
writes**, which is the security property ``reconcile_service`` is built on: a
row these do not return cannot be reached by crafting a request, and a row they
do return cannot be refused by the write door for being out of scope.

**What is ALREADY SPOKEN FOR is not part of that scope, and separating the two
is what makes a batch safe** (plan step ``bank_import:X-f6a-3c-2``).  These two
producers answer what an account COULD offer, which does not change while a
review pass runs; :func:`matched_subjects` answers what a match has already
claimed, which is exactly what the pass changes.  So the pass derives the offer
sets ONCE -- 3.6 s on the developer's own account -- and every act inside it
re-reads the claims for itself and narrows through :func:`unmatched_rows` /
:func:`unmatched_destinations`.  Stating the narrowing once, outside the
producers, is what stops a snapshot offering a row an earlier item in the same
pass has just matched.

**The MOVEMENT is the subject of every settled match, and a row is a
candidate only while it is Projected** (plan step ``credit_card:CC-5-4a-1``,
ruling **R-CC43**, developer 2026-09-21).  A settled row's money is its
covering movement (ruling **R-BAL80**: the fold reads movements and no row),
so the movement is what a statement shows and what a match names -- on
WHICHEVER account the movement is on, which since ``credit_card:CC-5-3`` may
be another account than the row's (a bill on checking paid FROM the card
holds its movement on the card).  Offering the ROW for it, as this module did
through ``CC-5-3``, left such a bill with no subject anywhere: the member key
held a row member to the row's account, and checking's feed never shows the
money.  So :func:`_settlement_candidates` offers every covering movement on
the screen's account, :func:`_transaction_candidates` offers only the
Projected rows, and the two arms PARTITION on one predicate
(:func:`~._valuation.row_is_offered_here`): a Projected row is offered as itself on its
own account, and its kept movement -- a reverted row's, un-dated (ruling
**R-CC42**) -- is offered where the row is not.  Every accepted act therefore
names movements: the acts recorded before this step named rows until plan
step ``credit_card:CC-5-4a-2`` re-keyed each onto its row's covering movement
and dropped the row column (ruling **R-CC45**, migration ``2eabfa596ee0``).

**What one candidate is WORTH is** :mod:`._valuation` **'s, in its own module
since plan step ``credit_card:CC-5-4a-1``** (this one crossed the 1,000-line
bound, ruling **balance:R-IR**; the seam is by subject, the one
:func:`~._valuation.repriced` had always stated -- the scope answers WHICH
rows an act may reach, the valuation what one is WORTH).  The three arms here
decide which rows exist and may be offered and build each through that
module's one constructor per kind; pricing is the cash ledger's and is not
restated in either.

Services-boundary discipline (``CLAUDE.md`` Architecture): reads only, plain
data in, frozen dataclasses out, no Flask import, no clock read.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.orm import aliased, contains_eager, joinedload, selectinload

from app.extensions import db
from app.models.account import Account
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transaction_entry import TransactionEntry
from app.services import cash_ledger, status_seam
from app.utils.balance_predicates import (
    balance_contributing_clause,
    is_projected_clause,
)

from ._creations import PurchaseDestination
from ._subjects import CandidateRow, Candidates
from ._valuation import (
    purchase_candidate,
    settlement_candidate,
    settlement_price,
    transaction_candidate,
    transaction_price,
)

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from app.services.pay_calendar import PayCalendar


@dataclass(frozen=True)
class MatchedSubjects:
    """What an account's accepted matches have already claimed.

    **The fact a review pass CHANGES, held apart from the facts it does not**
    (plan step ``bank_import:X-f6a-3c-2``).  The offer sets beside it are
    derived once for a whole pass because nothing in the pass can add a row to
    them; this one is re-read by every act, because every act adds to it.

    It is also ONE query where there were two: :func:`candidates_for` read the
    account's members to exclude what it had claimed, and the accept door read
    them again to see an envelope whose purchase another match names.  Those
    are the same three sets, so a caller reads them once and threads them.

    **A row is CLAIMED through its PAYMENT, by any act of the OWNER's** (plan
    steps ``credit_card:CC-5-4a-1`` / ``CC-5-4a-2``, rulings **R-CC43**,
    **R-CC45**): every act names the row's covering movement rather than the
    row, and that movement may sit on ANOTHER account than the row since
    ``credit_card:CC-5-3`` (a checking bill's payment on the card, matched on
    the card's screen).  :attr:`transactions` holds the parents of those
    movements across the owner's accounts, so a Projected row whose kept
    movement an act still names is not offered again on ANY screen (the act
    shows on that account's register as no longer holding, with its Undo);
    and :func:`~._accept._reject_parent_and_its_own_purchase` reads one set
    for "an envelope an act already names".  Read on one account alone (a
    first cut of ``CC-5-4a-1``), a reverted card-paid bill was offered on
    checking while the card's act still named its payment, and accepting it
    there re-pointed the payment and withdrew the card's act through a door
    that discloses nothing (that leaf's neutral review; ruling **R-CC46**
    says disclosed).  :attr:`lines` and :attr:`entries` stay the account's
    own: a line belongs to one account, and a movement is offered only where
    it is.

    Attributes:
        lines: The ``bank_statement_lines`` ids a match already explains.
        transactions: The ``transactions`` ids a match of the owner's already
            names through the row's covering movement, on any of the owner's
            accounts.
        entries: The ``transaction_entries`` ids a match already names, a
            purchase's or a covering movement's.
    """

    lines: "frozenset[int]"
    transactions: "frozenset[int]"
    entries: "frozenset[int]"


def act_still_names_a_row():
    """Return the EXISTS that makes a membership a live CLAIM.

    **A match asserts that these bank lines ARE these app rows, and the
    movement key CASCADES** (``fk_statement_match_members_entry_account``,
    ``ondelete="CASCADE"``).  So destroying the last movement an act names --
    a purchase, or a row's payment with the row or on its own -- leaves the
    act holding its LINE alone -- and the line went on
    reading as explained, permanently, because "explained" was membership and
    nothing else.  It could then never be offered or matched again, whatever
    the review screen showed.

    **This is the invariant, and the writer beside it is the cleanup.**  The
    one act that takes a movement off the books
    (:mod:`app.services.movement_removal`, plan step ``credit_card:CC-5-4a-3``)
    withdraws such an act at every door that removes a movement THROUGH it,
    so the false record goes and the press can say which lines it freed.
    Three doors remove movements without it: ``routes/templates/crud``'s
    permanent delete and the account delete remove rows in BULK SQL, and
    ``pay_period_write.retire_paydays`` through a database cascade (finding
    **CC-363**; adversarial review, 2026-08-25, measured the template
    hard-delete reaching the state from a shipped button).  A rule enforced
    by enumeration is a rule the next door forgets, so until plan step
    ``credit_card:CC-5-4a-4`` this predicate in the one query that decides is
    what holds; that step ends it at the root (ruling **R-CC54**: a row
    holding a movement is history those doors keep, and neither of a match's
    keys cascades) and deletes it.

    **Applying it to the WHOLE member scan is exact rather than convenient.**
    The EXISTS is true for every member of an act that holds an app row, so
    filtering the scan changes only the LINE set -- an act with no app-side
    member has no movement membership left to filter.

    Returns:
        A correlated ``EXISTS`` over the outer
        :class:`~app.models.statement_match.StatementMatchMember`.
    """
    sibling = aliased(StatementMatchMember)
    return (
        db.session.query(sibling)
        .filter(
            sibling.match_id == StatementMatchMember.match_id,
            sibling.transaction_entry_id.isnot(None),
        )
        .exists()
    )


def matched_subjects(account_id: int) -> MatchedSubjects:
    """Return every subject *account_id* has already matched, by kind.

    One statement over ``statement_match_members`` rather than three: the
    table's rows are an exclusive arc, so a single scan of the account's
    members partitions itself.

    **A member of an act that no longer names any app row is NOT a claim** --
    see :func:`act_still_names_a_row` for the whole argument and what it costs
    without.

    Args:
        account_id: The account whose matches to read.

    Returns:
        Its :class:`MatchedSubjects`.
    """
    rows = (
        db.session.query(
            StatementMatchMember.bank_statement_line_id,
            StatementMatchMember.transaction_entry_id,
        )
        .filter(
            StatementMatchMember.account_id == account_id,
            act_still_names_a_row(),
        )
        .all()
    )
    return MatchedSubjects(
        lines=frozenset(row[0] for row in rows if row[0] is not None),
        transactions=_claimed_rows_of_the_owner(account_id),
        entries=frozenset(row[1] for row in rows if row[1] is not None),
    )


def _claimed_rows_of_the_owner(account_id: int) -> "frozenset[int]":
    """Return every row an act of *account_id*'s OWNER names, through its payment.

    One scan of the owner's members naming a row's covering movement (ruling
    **R-CC43**; every member since migration ``2eabfa596ee0``, ruling
    **R-CC45**), whose parent is read through a join onto the entry and only
    where that entry is a payment record -- a purchase member's parent is NOT
    a claim on the envelope (the envelope's figure is its purchases;
    ``_accept`` refuses the pairing itself).  The OWNER's acts rather than
    the account's, for the reason :class:`MatchedSubjects` states: a payment
    matched on the card claims its checking row.  The owner is the account's,
    read in the query rather than taken as a second parameter that could name
    someone else.

    Args:
        account_id: The account whose owner's claims to read.

    Returns:
        The claimed ``transactions`` ids.
    """
    owner = (
        db.session.query(Account.user_id)
        .filter(Account.id == account_id)
        .scalar_subquery()
    )
    rows = (
        db.session.query(TransactionEntry.transaction_id)
        .join(
            StatementMatchMember,
            StatementMatchMember.transaction_entry_id == TransactionEntry.id,
        )
        .join(
            StatementMatch,
            StatementMatch.id == StatementMatchMember.match_id,
        )
        .filter(
            StatementMatch.user_id == owner,
            status_seam.covering_clause(),
        )
        .all()
    )
    return frozenset(row[0] for row in rows)


def unmatched_rows(
    candidates: Candidates, matched: MatchedSubjects,
) -> "list[CandidateRow]":
    """Return the candidate rows no accepted match has claimed.

    **The ONE statement of "an already-matched row is not offerable"**, applied
    by the screen against the claims it read and by each write door against the
    claims IT read.  ``uq_statement_match_members_*`` would refuse a second act
    on one anyway; narrowing here is what stops the screen offering a row whose
    acceptance is guaranteed to fail, and what stops a shared offer set handing
    a second act a row the first act in the same pass has just claimed.

    It is a filter over an already-derived set rather than a clause inside the
    query for exactly that reason: the query is run once per pass and the claims
    move within it.

    **A row is claimed through its PAYMENT, on any of the owner's accounts**
    (plan step ``credit_card:CC-5-4a-1``, ruling **R-CC43**): a TRANSACTION
    candidate -- a Projected row, perhaps a reverted one whose kept movement
    an act still names -- is claimed when its row is in
    :attr:`MatchedSubjects.transactions`, and so is a SETTLEMENT, whose row
    that set carries whichever account the act naming its movement is on.
    A PURCHASE is claimed by its own id alone: its envelope is a container,
    never named by it.

    Args:
        candidates: The pass's derived offer set.
        matched: The claims as of NOW.

    Returns:
        The rows still offerable, in *candidates*' own order.
    """
    return [
        row for row in candidates.rows
        if not _is_claimed(row, matched)
    ]


def _is_claimed(row: CandidateRow, matched: MatchedSubjects) -> bool:
    """Return whether an accepted act already names *row*, or its row's payment."""
    if row.kind.names_an_entry and row.row_id in matched.entries:
        return True
    return (
        row.transaction_id is not None
        and row.transaction_id in matched.transactions
    )


def unmatched_destinations(
    destinations: "Sequence[PurchaseDestination]", matched: MatchedSubjects,
) -> "list[PurchaseDestination]":
    """Return the purchase destinations no accepted match has claimed.

    :func:`unmatched_rows`' twin, and the same rule for the same reason: an
    envelope a match already names may not also take a new purchase, because
    ``_accept._reject_parent_and_its_own_purchase`` refuses that pairing --
    the envelope's figure already covers its own purchases -- so offering it
    would render a chooser whose submission always fails.

    Args:
        destinations: The pass's derived destination set.  A SEQUENCE, because
            :class:`~._scope.ReviewScope` holds a tuple and a ``list``
            annotation made both callers copy 220 rows -- one of them once per
            created purchase.
        matched: The claims as of NOW.

    Returns:
        The destinations still offerable, in *destinations*' own order.
    """
    return [
        destination for destination in destinations
        if destination.transaction_id not in matched.transactions
    ]


def _transaction_candidates(
    account_id: int, calendar: "PayCalendar",
    period_ids: "Collection[int]",
    basis: "cash_ledger.AmountBasis",
) -> "tuple[list[CandidateRow], list[int]]":
    """Return the PROJECTED transactions on *account_id* a statement could be showing.

    Scope, and every clause is load-bearing:

    * the row is on THIS account -- a statement is one bank's record of one
      account, and matching across accounts would book money against a
      statement that never showed it;
    * it is PROJECTED (plan step ``credit_card:CC-5-4a-1``, ruling
      **R-CC43**): a settled row's money is its covering movement, offered by
      :func:`_settlement_candidates` on whichever account the money moved
      through, so the row itself is a candidate only while nothing has moved
      yet.  This clause and the account clause above it are the two halves
      of the partition :func:`~._valuation.row_is_offered_here` states in
      Python;
    * it CONTRIBUTES to a balance and is not soft-deleted
      (:func:`~app.utils.balance_predicates.balance_contributing_clause`) -- a
      Credit or Cancelled row is not money this account moved, and it is the
      shared gate every cash reader here narrows with rather than a filter
      written again;
    * its pay period is one of the OWNER'S -- ownership, reached through the
      paycheck; ``C13-b`` REFUSED ``Transaction.user_id`` here.  **The
      ids come from the CALENDAR rather than from a correlated subquery on
      ``pay_periods.user_id``, and that is what makes the window lookup below
      total**: a row this query returns names a period the calendar was built
      from, so :meth:`~app.services.pay_calendar.PayCalendar.period_by_id`
      cannot answer ``None`` for it.  Inside a COMMAND -- the three POST doors
      that build a scope -- the two reads are separate snapshots under READ
      COMMITTED, so a concurrent period INSERT between them is expressible,
      and scoping by the calendar's own ids means the query simply does not
      ask about a period the calendar has not got rather than returning a row
      nothing here can date.  **Plan step balance:X-i3 makes that
      inexpressible inside a QUERY and takes nothing away from this clause**,
      because the clause is ALSO the OWNERSHIP scope this bullet opens with:
      it is what keeps another owner's rows out of the answer, which holds for
      every request kind and for every CLI caller;
    * a SHADOW's parent transfer still exists and is not soft-deleted -- the
      clause ``reconcile_service._transfers.arm`` carries for the same reason:
      a shadow whose parent has gone is not money this account owes, and
      pricing one sends ``transfer_service.settle_amount`` at a row it treats
      as absent.  **It is unreachable through today's doors and stated
      anyway**: ``delete_transfer(soft=True)`` marks the transfer AND both
      shadows, and production carries 0 live shadows with a missing or
      soft-deleted parent (measured 2026-08-17) -- so the clause changes no
      answer today and the scope stops depending on a writer keeping a
      convention.  That is the same argument
      ``cash_ledger.movement_cash_leg`` makes for its own total guard.

    **What is ALREADY MATCHED is NOT a clause here** (plan step
    ``bank_import:X-f6a-3c-2``); it is :func:`unmatched_rows`, applied by each
    caller against the claims that caller read.  It used to be a filter in this
    loop, which is correct for a producer called once per act and wrong for one
    called once per PASS: a row matched by the pass's third item would still
    have been offered to its fourth.

    Not scoped by ``scenario_id``, for the same reason
    ``reconcile_service._rows.outstanding_scope`` is not: Phase 1 is
    baseline-only, so ``account_id`` fully isolates the set today, and when
    what-if scenarios land every arm must thread an operating scenario.

    Args:
        account_id: The cash account the statement is for.
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`,
            which each unsettled row's window is read from
            (:attr:`~._subjects.CandidateRow.expected_window`).  The DERIVED
            span, never ``pay_periods.end_date``: that column is a stored copy
            of a derivable fact and plan step ``pay_calendar:C4-c`` dropped it.
        period_ids: The saved period ids of that same calendar
            (:meth:`~app.services.pay_calendar.PayCalendar.saved_by_id`),
            resolved ONCE by :func:`candidates_for` and threaded rather than
            re-derived per arm.
        basis: The pass's :class:`~app.services.cash_ledger.AmountBasis`,
            threaded for exactly the reason ``period_ids`` above it is (plan
            step X-au-j): one derivation the whole pass shares, resolved once
            and never rebuilt under it.

    Returns:
        ``(candidates, unpriceable)`` -- one
        :class:`~._subjects.CandidateRow` per offerable row, by id (every row
        here is Projected and carries no day, so the id is the whole of the
        deterministic order the settled arm sorts its days ahead of; the
        proposals a screen shows must not depend on what the planner happened
        to return), and the ids of the rows the amount model could not price.
    """
    rows = (
        db.session.query(Transaction)
        .options(
            selectinload(Transaction.entries),
            joinedload(Transaction.template),
        )
        .filter(
            Transaction.account_id == account_id,
            is_projected_clause(Transaction),
            balance_contributing_clause(),
            Transaction.pay_period_id.in_(period_ids),
            _parent_transfer_stands(calendar),
        )
        .all()
    )
    candidates = []
    unpriceable = []
    for txn in rows:
        amount = transaction_price(txn, basis)
        if amount is None:
            unpriceable.append(txn.id)
            continue
        candidate = transaction_candidate(txn, calendar, amount)
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=lambda row: row.row_id)
    return candidates, unpriceable


def _parent_transfer_stands(calendar: "PayCalendar"):
    """Return the clause admitting a plain row, or a shadow whose transfer stands.

    The shadow-parent clause :func:`_transaction_candidates` states in its
    fourth bullet, spelled once for both arms that read a row: the row arm
    applies it to the row and :func:`_settlement_candidates` to a covering
    movement's parent, so neither can price a leg whose transfer has gone.

    Args:
        calendar: The pass's calendar, whose owner the transfer must belong to.

    Returns:
        A SQLAlchemy boolean expression over ``Transaction``.
    """
    return db.or_(
        Transaction.transfer_id.is_(None),
        Transaction.transfer_id.in_(
            db.session.query(Transfer.id).filter(
                Transfer.user_id == calendar.user_id,
                Transfer.is_deleted.is_(False),
            )
        ),
    )


def _settlement_candidates(
    account_id: int, calendar: "PayCalendar",
    period_ids: "Collection[int]",
    basis: "cash_ledger.AmountBasis",
) -> "tuple[list[CandidateRow], list[int]]":
    """Return the covering movements on *account_id* a statement could be showing.

    **The settled subject's arm** (plan step ``credit_card:CC-5-4a-1``,
    ruling **R-CC43**): a settled row's money IS its covering movement
    (ruling **R-BAL80**), on the account the money moved through -- the
    row's own for every settle through ``credit_card:CC-5-2``, and since
    ``CC-5-3`` the TENDER the door named, which for a bill charged to the
    card is the card.  Offering the row for it (the shape through
    ``CC-5-3``) put the subject on the row's account, where a card-paid
    bill's money never was and a member naming the row could not be written
    for the card's statement.

    Scope, each clause load-bearing:

    * the MOVEMENT is on THIS account (``TransactionEntry.account_id``,
      ruling **R-BAL75**) -- the one clause that differs from the row arm's,
      and the whole point;
    * it is a covering movement (:func:`status_seam.covering_clause`) --
      the seam's mirror of the row's record, never a person's purchase, which
      :func:`_purchase_candidates` offers on its own terms;
    * its ROW is NOT this screen's candidate as a row: the complement of
      :func:`_transaction_candidates`' first two clauses, so a Projected row
      on this account is offered once, as itself, and its kept movement (a
      reverted row's, ruling **R-CC42**) is offered only where the row is
      not -- :func:`~._valuation.row_is_offered_here` is the same partition
      in Python, re-asked by :func:`~._valuation.repriced`;
    * its row CONTRIBUTES and is not soft-deleted, sits in one of the
      OWNER's saved periods, and if a shadow leg has a transfer that stands
      -- the row arm's clauses, applied to the parent for the same reasons.

    **What is ALREADY MATCHED is NOT a clause here**; see
    :func:`_transaction_candidates` for why it moved to :func:`unmatched_rows`.

    Args:
        account_id: The cash account the statement is for.
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
        period_ids: The owner's saved pay-period ids, threaded as the other
            two arms take them.
        basis: The pass's :class:`~app.services.cash_ledger.AmountBasis`, for
            the un-dated arm of :func:`~._valuation.settlement_price`.

    Returns:
        ``(candidates, unpriceable)`` -- one :class:`~._subjects.CandidateRow`
        per offerable movement, oldest recorded day first and unrecorded days
        last with the movement id breaking ties, and the ids of the ROWS the
        amount model could not price a re-settle for.
    """
    rows = (
        db.session.query(TransactionEntry)
        .join(TransactionEntry.transaction)
        .options(
            contains_eager(TransactionEntry.transaction)
            .selectinload(Transaction.entries),
            contains_eager(TransactionEntry.transaction)
            .joinedload(Transaction.template),
            contains_eager(TransactionEntry.transaction)
            .joinedload(Transaction.account),
        )
        .filter(
            TransactionEntry.account_id == account_id,
            status_seam.covering_clause(),
            ~db.and_(
                is_projected_clause(Transaction),
                Transaction.account_id == account_id,
            ),
            balance_contributing_clause(),
            Transaction.pay_period_id.in_(period_ids),
            _parent_transfer_stands(calendar),
        )
        .all()
    )
    candidates = []
    unpriceable = []
    for entry in rows:
        amount = settlement_price(entry, basis)
        if amount is None:
            unpriceable.append(entry.transaction_id)
            continue
        candidate = settlement_candidate(entry, calendar, amount, account_id)
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(
        key=lambda row: (row.settled_on is None, row.settled_on, row.row_id),
    )
    return candidates, unpriceable


def _purchase_candidates(
    account_id: int, calendar: "PayCalendar", period_ids: "Collection[int]",
) -> "list[CandidateRow]":
    """Return the purchases on *account_id* a statement could be showing.

    A purchase is a cash movement of its OWN since plan step
    ``balance:X-f3b``, so the bank shows it as a line in its own right -- which
    is what makes the 267 card-swipe lines on the developer's own statement
    matchable at all.

    Two clauses beyond the account and the owner:

    * NOT a card purchase.  A card purchase never touches this account -- it
      leaves later through its own CC Payback sibling -- so a checking
      statement cannot be showing one.  It is the same clause
      ``ck_transaction_entries_card_purchase_clears_nowhere`` makes structural
      for the clearing link;
    * its PARENT contributes.  A purchase under a soft-deleted, Credit or
      Cancelled row posts nothing (ruling **R-FM**), so offering one would
      propose to record a movement the ledger books at zero;
    **A THIRD clause stood here until plan step balance:X-am** (ruling
    **balance:R-HA**): the parent must not be ARCHIVED.  It was added by
    adversarial financial review 2026-08-17 because the terminal ``Settled``
    status was not excluded by ``balance_contributing_clause``, so the screen
    could offer a row whose acceptance raised MID-LOOP -- falsifying this
    package's claim that every refusal fires before anything is written.  The
    status is deleted, so no row can be in it and the clause can match nothing;
    it goes with its subject rather than standing as a filter over an id that
    no longer exists.

    **What is ALREADY MATCHED is NOT a clause here**; see
    :func:`_transaction_candidates` for why it moved to :func:`unmatched_rows`.

    Args:
        account_id: The cash account the statement is for.
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`,
            which each purchase's envelope period is read from (plan step
            ``bank_import:X-gz``) -- threaded for the reason *period_ids* is.
        period_ids: The owner's saved pay-period ids -- the SAME scope
            :func:`_transaction_candidates` applies, written once and threaded
            so the two arms cannot drift about whose rows may be offered.

    Returns:
        One :class:`~._subjects.CandidateRow` per offerable purchase, ordered as
        :func:`_transaction_candidates` orders its own.
    """
    rows = (
        db.session.query(TransactionEntry)
        .join(TransactionEntry.transaction)
        .options(contains_eager(TransactionEntry.transaction))
        .filter(
            TransactionEntry.account_id == account_id,
            TransactionEntry.is_credit.is_(False),
            balance_contributing_clause(),
            Transaction.pay_period_id.in_(period_ids),
            # NOT the row's own payment record (plan step **X-bi-3a**): a
            # covering movement is the settle's mirror of its parent's figure
            # and is :func:`_settlement_candidates`' subject, on the ROW's
            # terms (plan step ``credit_card:CC-5-4a-1``); this arm offers a
            # person's purchases on their own.  Offering it here too would put
            # one bank line against two candidates.
            ~status_seam.covering_clause(),
        )
        .all()
    )
    return sorted(
        (
            purchase_candidate(entry, calendar)
            for entry in rows if entry.amount
        ),
        key=lambda row: (row.settled_on is None, row.settled_on, row.row_id),
    )


def candidates_for(
    account_id: int, calendar: "PayCalendar",
    basis: "cash_ledger.AmountBasis",
) -> Candidates:
    """Return every row on *account_id* a statement could be showing.

    **The ONE entry point, and the reason it exists is that the two arms share
    a read.**  Both scope by the owner's saved period ids, and asking twice in
    one request is a redundant producer call -- the shape this project treats
    as a DRY violation rather than as a cost.  It is resolved once here and
    threaded.

    **The CALENDAR is a parameter for the same reason and one tier up.**  A
    read pass holds one calendar and every producer under it takes it, exactly
    as a balance pass threads its ``BalanceContext``:
    :class:`~._scope.ReviewScope` builds one and hands it to this and to its own
    line placer, where a first version of this step had each of them ask
    ``calendar_for`` separately and a third site answer the same question with
    its own ``MIN(start_date)``.  Three reads of one fact in one request is the
    defect the paragraph above describes, and two of them can disagree: under
    READ COMMITTED a concurrent payday write between the two loads would place
    a line by one calendar and bound its candidates by another.  Found by
    adversarial financial review 2026-08-19.  **Since plan step balance:X-i3
    the disagreement is a COMMAND's alone** -- the three POST doors that build
    a scope, which are also the three that move money -- because a render's
    whole request is one snapshot.  The DRY half of the argument was never
    conditional on the isolation level and is what still makes the parameter
    required on every path.

    **It answers what the account COULD offer, and says nothing about what is
    already spoken for** (plan step ``bank_import:X-f6a-3c-2``) -- that is
    :func:`matched_subjects`, narrowed in by :func:`unmatched_rows`.  The split
    is what lets one derivation serve a whole review pass: this answer does not
    move while the pass runs, and the claims do.

    Args:
        account_id: The cash account the statement is for.
        calendar: The owner's
            :class:`~app.services.pay_calendar.PayCalendar`, built by the read
            pass.  **It IS the ownership scope**, which is why no ``owner_id``
            sits beside it: the periods it carries are exactly that owner's, so
            a second parameter naming the owner would be a second statement of
            whose rows may be offered and the two could disagree.  Nothing here
            re-derives it -- a producer that rebuilt its caller's pass would be
            the copy this parameter exists to remove.
        basis: The pass's
            :class:`~app.services.cash_ledger.AmountBasis`, built by
            :meth:`~._scope.ReviewScope.build` (plan step X-au-j, finding
            **N-309**).  It is a parameter for exactly the reason stated one
            column up and it is REQUIRED for exactly that reason too: a
            producer that built its own would be the copy the parameter exists
            to remove, and defaulting it would leave the expensive shape as
            what a caller gets by saying nothing.

    Returns:
        A :class:`~._subjects.Candidates`.  Its ``rows`` are the settlements,
        the transactions and the purchases TOGETHER, each arm's own order
        preserved and in that order -- the settled records by their day, then
        the Projected rows, then the purchases, which is the order the row arm
        alone produced while it carried the settled rows too -- a union rather
        than a triple, because every consumer asks the same question of every
        kind: a bank line does not know which table its counterpart lives in.
    """
    # The owner's SAVED periods, which are both arms' ownership scope.  Asked
    # of the calendar ONCE here rather than in each arm, for the reason the
    # calendar itself is threaded: two asks in one request is this project's
    # DRY violation rather than a cost.  The ``period_id is not None`` filter
    # is :meth:`~app.services.pay_calendar.PayCalendar.saved_by_id`'s since
    # pay-calendar plan step C4-a-4 -- one spelling of "is this period SAVED",
    # which this module used to write for itself.
    period_ids = calendar.saved_by_id().keys()
    settlements, unpriceable_settlements = _settlement_candidates(
        account_id, calendar, period_ids, basis,
    )
    transactions, unpriceable_transactions = _transaction_candidates(
        account_id, calendar, period_ids, basis,
    )
    return Candidates(
        rows=settlements + transactions + _purchase_candidates(
            account_id, calendar, period_ids,
        ),
        unpriceable_ids=(
            *unpriceable_settlements, *unpriceable_transactions,
        ),
    )
