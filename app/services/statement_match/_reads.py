"""What the review screen shows: unmatched lines, proposals, and what agrees.

Read-only, and separate from :mod:`._accept` for the reason every package here
splits that way: the write door and the reader answer different questions, and
a reader living inside the door is a reader nobody can call without one.

**It reports three things a bound would otherwise hide**, because a screen that
lists what it could explain and says nothing about what it could not reads as
a clean sweep:

* lines that predate the owner's pay calendar, which nothing can ever match --
  130 of the developer's own 361 lines, and listing them beside genuine
  failures would bury the ones worth acting on;
* days too crowded to search for groups
  (:func:`~._propose.skipped_group_days`);
* matches whose rows no longer carry the day the bank stated, which is what a
  later hand edit produces and what makes a match re-reviewable rather than
  quietly stale.

Services-boundary discipline: reads only, plain data in, frozen dataclasses
out, no Flask import.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload, selectinload

from app import ref_cache
from app.enums import SettlementBasisEnum
from app.exceptions import AmountUnresolvable
from app.extensions import db
from app.models.statement_import import BankStatementLine
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import cash_ledger, pay_calendar
from app.utils.balance_predicates import (
    balance_contributing_clause,
    is_balance_contributing,
    not_archived_clause,
)
from app.utils.money import round_money

from ._candidates import candidates_for
from ._offers import BankLine, CandidateRow, MatchProposal, PurchaseDestination
from ._propose import propose, skipped_group_days


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


@dataclass(frozen=True)
class ReviewBounds:
    """What the review DID NOT look at, and why.

    **A screen that lists what it could explain and says nothing about what it
    could not reads as a clean sweep.**  These four facts are one subject --
    the limits of this pass -- and they travel together so a caller cannot
    render the proposals while forgetting the caveat.

    Attributes:
        calendar_opens: The first day the owner's pay calendar covers, or
            ``None`` for an owner with no periods at all.
        before_calendar_count: How many recorded lines fall before it, which
            nothing can ever match: there are no rows to match them to.  A
            COUNT and a last day rather than the rows themselves -- they are
            not work, they are the statement being older than the budget.
            Measured at 130 of 361 on the developer's own export.
        before_calendar_last_day: The latest of those days, or ``None``.
        crowded_days: Days :func:`~._propose.skipped_group_days` refused to
            search for GROUP matches.
        unpriceable_count: How many of the account's rows the amount model
            could not price, so they could not be offered
            (:class:`~._offers.Candidates`).
    """

    calendar_opens: "date | None"
    before_calendar_count: int
    before_calendar_last_day: "date | None"
    crowded_days: "tuple[date, ...]"
    unpriceable_count: int

    @property
    def any_limit(self) -> bool:
        """Return whether this pass left anything unexamined.

        The one question the template asks, answered here rather than as three
        ``or``-ed truth tests in a Jinja condition -- where a fourth limit
        added later would silently not appear.
        """
        return bool(
            self.before_calendar_count
            or self.crowded_days
            or self.unpriceable_count
        )


@dataclass(frozen=True)
class CreatableLine:
    """One bank OUTFLOW the app has no row for, and where it could go.

    Plan step ``bank_import:X-f6a-3b``, ruling **R-FS**'s third shape.  These
    are the lines the matcher can never explain, because the app records a
    period's groceries as one envelope and the bank records every swipe:
    measured on the developer's own statement **91** unmatched outflows survive
    every proposal, of which 74 are card swipes worth `$3,383.49` -- the case
    R-FS names -- and 17 are ACH debits the app may already hold in another
    shape, which the screen SAYS rather than filtering on the bank's prose.

    Attributes:
        line: The bank's own record of the movement.
        pay_period_id: The period covering the day the bank says it was MADE,
            or ``None`` when no saved period does -- which is what a line
            older than the owner's first payday looks like.  The MADE day and
            not the posting day, because a purchase's budget clock is
            ``purchased_on`` and a swipe made on a period's last day and posted
            on the next period's first belongs to the budget it was made under.
        destinations: The budget lines it could become a purchase against, in
            that period.  EMPTY is a real answer and the screen must say so
            rather than rendering a chooser with nothing in it: on the
            developer's own data the 2026-03-26 period holds three envelopes and
            all three closed at a fixed figure, so 8 lines worth `$662.13` have
            no existing destination and a NEW envelope is the only arm open to
            them.
    """

    line: BankLine
    pay_period_id: "int | None"
    destinations: "tuple[PurchaseDestination, ...]"


@dataclass(frozen=True)
class ReviewSet:
    """Everything the review screen needs, in one value.

    Attributes:
        proposals: What the app believes goes with what, best first.
        unmatched: Bank lines inside the pay calendar that no proposal
            explains, ascending by day.
        unmatched_rows: The app's OWN rows that no proposal explains, over the
            span the statement covers -- ruling **R-FP**'s other side, and the
            more valuable half for a budget: a row the bank never showed is a
            payment the records claim happened and the bank did not make.  They
            are :class:`~._offers.CandidateRow` values rather than a type of
            their own; a second record carrying the same five fields was
            reported by pylint's cross-file ``duplicate-code`` and was exactly
            rule 13's speculative shape.
        accepted: The matches already accepted, newest first.
        creatable: The unmatched OUTFLOW lines, each with the budget lines it
            could become a purchase against (:class:`CreatableLine`).  A SUBSET
            of ``unmatched`` rather than a partition of it, and deliberately:
            the same line is offered to the hand-build form as something to
            GROUP and to the create door as something to RECORD, because those
            are different acts on the same fact and the owner is the one who
            knows which it is.  Inflows are absent -- a purchase is an expense
            (``ck_transaction_entries_positive_amount``), so a deposit or a
            card refund can only ever be matched to a row.
        bounds: What this pass did NOT look at (:class:`ReviewBounds`).
    """

    proposals: "tuple[MatchProposal, ...]"
    unmatched: "tuple[BankLine, ...]"
    unmatched_rows: "tuple[CandidateRow, ...]"
    accepted: "tuple[AcceptedGroup, ...]"
    creatable: "tuple[CreatableLine, ...]"
    bounds: ReviewBounds


def _covered_span(account_id: int) -> "tuple[date, date] | None":
    """Return the first and last day this account has a recorded line for.

    Every RECORDED line, matched or not: the span a statement covers is a fact
    about what the bank sent, and it must not move as the owner works through
    the matches.

    Args:
        account_id: The account.

    Returns:
        ``(first, last)``, or ``None`` when nothing is recorded.
    """
    bounds = db.session.query(
        db.func.min(BankStatementLine.posted_on),
        db.func.max(BankStatementLine.posted_on),
    ).filter(BankStatementLine.account_id == account_id).one()
    return None if bounds[0] is None else (bounds[0], bounds[1])


def _could_have_been_shown(
    row: CandidateRow, covered: "tuple[date, date] | None",
) -> bool:
    """Return whether the statement could have shown *row*'s movement.

    **It asks the row's own WINDOW** -- the days the app believes that money
    moved between (:attr:`~._offers.CandidateRow.expected_window`) -- and the
    two overlap or they do not.  It used to test one day, ``settled_on or
    expected_on``, which is that accessor's own rule written a second time and
    one end short: a bill budgeted across the statement's opening day was
    dropped from the list because its period STARTS earlier, while every rule
    beside it had learned that a bill occupies a fortnight.  Found by
    adversarial design review 2026-08-19.

    Args:
        row: The candidate.
        covered: The recorded span, or ``None`` when nothing is recorded.

    Returns:
        Whether the two spans overlap.  A row the app can date no way at all is
        IN: there is no basis for excluding it, and saying so is better than
        dropping it silently -- the opposite of the proposer's answer for the
        same row, and deliberately, because this list is a REPORT and that one
        is a money door.
    """
    if covered is None:
        return False
    window = row.expected_window
    if window is None:
        return True
    return window[0] <= covered[1] and window[1] >= covered[0]


def _unmatched_lines(account_id: int) -> "list[BankStatementLine]":
    """Return the account's recorded lines that no match explains.

    Args:
        account_id: The account.

    Returns:
        The lines, ascending by posted day then id.
    """
    spoken_for = (
        db.session.query(StatementMatchMember.bank_statement_line_id)
        .filter(
            StatementMatchMember.account_id == account_id,
            StatementMatchMember.bank_statement_line_id.isnot(None),
        )
    )
    return (
        db.session.query(BankStatementLine)
        .filter(
            BankStatementLine.account_id == account_id,
            BankStatementLine.id.notin_(spoken_for),
        )
        .order_by(BankStatementLine.posted_on, BankStatementLine.id)
        .all()
    )


def _as_bank_line(row: BankStatementLine) -> BankLine:
    """Return *row* as the value the proposer and the screen share.

    Args:
        row: A recorded line.

    Returns:
        Its :class:`~._offers.BankLine`.
    """
    return BankLine(
        line_id=row.id,
        posted_on=row.posted_on,
        amount=Decimal(str(row.amount)),
        description=row.description,
        transaction_on=row.transaction_on,
    )


def _accepted_groups(
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
    matches = (
        db.session.query(StatementMatch)
        .options(selectinload(StatementMatch.members))
        .filter(
            StatementMatch.account_id == account_id,
            StatementMatch.user_id == owner_id,
        )
        .order_by(StatementMatch.created_at.desc(), StatementMatch.id.desc())
        .all()
    )
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
        match_lines = [
            lines[member.bank_statement_line_id] for member in match.members
            if member.bank_statement_line_id is not None
        ]
        if not match_lines:
            # Every line CASCADED away with its import or its account.  The act
            # asserts nothing about a bank any more, so it is not listed --
            # deleting it here would be a write inside a reader.
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
    # ``round_money`` on BOTH sides, because the accept door rounds before it
    # compares (``_accept._reject_unbalanced``) and two spellings of one
    # invariant is a match the door accepted that this reader calls broken
    # forever.
    bank = round_money(
        sum((Decimal(str(line.amount)) for line in lines), Decimal("0.00")),
    )
    app_side = round_money(
        sum((row.cash_amount for row in rows), Decimal("0.00")),
    )
    return bank == app_side


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


def destinations_for(
    owner_id: int, account_id: int,
) -> "list[PurchaseDestination]":
    """Return every budget line a bank line could become a purchase against.

    **ONE scope, shared by the screen that offers a destination and the door
    that writes into it** (:func:`~._create._existing_envelope`), which is the
    property :func:`~._accept._load_rows` rests on: a row this does not return
    cannot be reached by crafting a request, and a row it does return cannot be
    refused by the write door.  Every clause below is one of those doors'.

    Scope, and what each clause is:

    * on THIS account, and its pay period is this OWNER's -- a statement is one
      bank's record of one account, and ``Transaction`` carries no ``user_id``
      of its own;
    * it TRACKS PURCHASES -- ``entry_service.create_entry`` refuses a parent
      that does not, and a purchase needs a container that can hold more than
      one;
    * it is not a TRANSFER and not INCOME -- both are ``create_entry``
      refusals: a transfer's legs are the transfer service's, and money coming
      in is not a purchase;
    * it CONTRIBUTES to a balance and is not soft-deleted
      (:func:`~app.utils.balance_predicates.balance_contributing_clause`) -- a
      Credit or Cancelled row records no cash, so a purchase filed under one
      would post nothing (ruling **R-FM**);
    * it is not ARCHIVED -- finding **N-229**: an archived row's purchases are
      history, and ``_candidates._purchase_candidates`` already declines to
      offer one;
    * if it has SETTLED, its recorded figure IS its purchases.  **This is the
      money clause** (:func:`~app.services.entry_service._doors
      ._reject_settled_addition`): on a ``purchases`` basis a new purchase
      raises what the row cost by exactly its own amount and the row's cash leg
      does not move, so the movement is recorded; on a stored-figure basis the
      gross cannot rise, and ``settled_cash_leg`` then subtracts money the gross
      never held -- measured on a production clone, `-163.95` became `+203.67`
      while the anchor true-up moved `$0.00`;
    * it is NOT ITSELF MATCHED to a bank line.  ``accept_match``'s
      :func:`~._accept._reject_parent_and_its_own_purchase` refuses a purchase
      whose parent another match already names, so offering such an envelope
      would render a chooser whose submission always fails.

      **Finding N-317 said this clause was wider than the money needs, and the
      re-measurement REFUTED it** (plan step ``bank_import:X-f6a-3c``,
      developer ruling 2026-08-19).  Its argument was that a purchase BORN
      carrying its posting day moves its parent's leg by zero, so the earlier
      match still balances.  That is true of two envelope shapes and false of
      the third, and the third is offerable here: adding a `$30.00` posted
      purchase to a row on a production clone moved the cash leg by `$0.00`
      for an envelope settled on a purchases basis (`Gas` 2223, `-88.01`
      unchanged) and for a projected envelope already holding purchases, and by
      **`+111.02`** for a projected envelope holding NONE (`Gas` 2232,
      budgeted `$111.02`, leg `-111.02` to `0.00`).  The reason is
      ``settles_from_entries``: an envelope with no entries is valued at its
      own figure, and the first purchase flips it onto ``sum(entries)`` while
      that purchase's own posting day is subtracted again.  A match accepted
      against such a row would be left explaining money the app no longer holds
      anywhere.  So the clause stays whole, and the finding is CLOSED as a
      misdiagnosis rather than acted on.

    Args:
        owner_id: The user whose budget lines may be offered.
        account_id: The cash account the statement is for.

    Returns:
        One :class:`~._offers.PurchaseDestination` per offerable row, oldest
        pay period first and then by name -- a deterministic order, so the
        chooser a screen shows does not depend on what the planner returned.
    """
    matched = {
        row[0] for row in db.session.query(
            StatementMatchMember.transaction_id,
        ).filter(
            StatementMatchMember.account_id == account_id,
            StatementMatchMember.transaction_id.isnot(None),
        ).all()
    }
    purchases_basis = ref_cache.settlement_basis_id(
        SettlementBasisEnum.PURCHASES,
    )
    rows = (
        db.session.query(Transaction)
        .options(joinedload(Transaction.pay_period))
        .filter(
            Transaction.account_id == account_id,
            Transaction.transfer_id.is_(None),
            balance_contributing_clause(),
            not_archived_clause(Transaction),
            Transaction.pay_period.has(user_id=owner_id),
        )
        .all()
    )
    offered = [
        PurchaseDestination(
            transaction_id=txn.id,
            label=(
                f"{txn.name} "
                f"({txn.pay_period.start_date} - {txn.pay_period.end_date})"
            ),
            pay_period_id=txn.pay_period_id,
            is_settled=txn.status.is_settled,
        )
        for txn in rows
        if txn.id not in matched
        and txn.tracks_purchases
        and not txn.is_income
        and (
            not txn.status.is_settled
            or txn.settled_basis_id == purchases_basis
        )
    ]
    offered.sort(key=lambda d: (d.pay_period_id, d.label))
    return offered


def _creatable_lines(
    calendar, unmatched: "list[BankLine]",
    destinations: "list[PurchaseDestination]",
) -> "tuple[CreatableLine, ...]":
    """Return the unmatched OUTFLOWS with the destinations open to each.

    Args:
        calendar: The owner's
            :class:`~app.services.pay_calendar.PayCalendar`, which places each
            line.  Taken rather than loaded, so one request holds ONE calendar
            (:func:`review_set`).
        unmatched: The bank lines inside the calendar no proposal explains.
        destinations: Every offerable budget line
            (:func:`destinations_for`), read ONCE and grouped here rather than
            re-queried per line -- a redundant producer call inside one request
            is this project's DRY violation rather than a cost.

    Returns:
        One :class:`CreatableLine` per outflow, in the order the lines were
        given.  The per-period destination tuple is SHARED by every line in
        that period, so a statement with 91 outflows over 11 periods builds 11
        tuples rather than 91.
    """
    outflows = [line for line in unmatched if line.amount < 0]
    if not outflows:
        return ()
    by_period: "dict[int, tuple[PurchaseDestination, ...]]" = {}
    for destination in destinations:
        by_period.setdefault(destination.pay_period_id, ())
    for period_id in by_period:
        by_period[period_id] = tuple(
            d for d in destinations if d.pay_period_id == period_id
        )
    return tuple(
        CreatableLine(
            line=line,
            pay_period_id=_period_id_for(calendar, line.happened_on),
            destinations=by_period.get(
                _period_id_for(calendar, line.happened_on), (),
            ),
        )
        for line in outflows
    )


def _period_id_for(calendar, day: date) -> "int | None":
    """Return the SAVED pay period covering *day*, or ``None``.

    Args:
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
        day: The day the bank says the purchase was made.

    Returns:
        Its period id, or ``None`` when no saved period covers it -- a line
        older than the owner's first payday, or past the generated horizon.
    """
    period = calendar.period_containing(day)
    return None if period is None else period.period_id


def _split_at_calendar_open(
    recorded: "list[BankStatementLine]", opens: "date | None",
) -> "tuple[list[BankStatementLine], list[BankStatementLine]]":
    """Split the account's unmatched lines at the owner's first payday.

    Args:
        recorded: Every recorded line no match explains.
        opens: The first day the pay calendar covers, or ``None`` for an owner
            with no periods at all -- in which case nothing is BEFORE, because
            "before the calendar" is not a fact about a calendar that does not
            exist, and the lines are reported as unexplained rather than as
            out of reach.

    Returns:
        ``(before, inside)``.  A line before the first payday can never be
        matched -- there are no rows before that day for it to match -- so it
        is COUNTED by :class:`ReviewBounds` rather than listed as work.
    """
    before = [
        line for line in recorded
        if opens is not None and line.posted_on < opens
    ]
    before_ids = {line.id for line in before}
    return before, [line for line in recorded if line.id not in before_ids]


def review_set(owner_id: int, account_id: int) -> ReviewSet:
    """Return everything the review screen shows for one account.

    ONE assembly, so the proposals, the leftovers and the bounds are all
    derived from the same read of the same account inside one request -- a
    screen whose "unmatched" list came from a second pass could disagree with
    its own proposals.

    Args:
        owner_id: The user the route proved owns the account.
        account_id: The account whose statements to review.

    Returns:
        Its :class:`ReviewSet`.
    """
    # **ONE calendar for the whole pass.**  Three sites asked the same question
    # in one request until adversarial financial review 2026-08-19 -- this
    # reader's own ``MIN(start_date)``, ``candidates_for``'s window source and
    # ``_creatable_lines``' line placer -- and two of them could disagree under
    # READ COMMITTED.  ``opening_bound`` is the calendar's own answer to what
    # ``_calendar_opens`` used to query for, so the query went with it.
    calendar = pay_calendar.calendar_for(owner_id)
    opens = calendar.opening_bound()
    before, inside = _split_at_calendar_open(
        _unmatched_lines(account_id), opens,
    )

    candidates = candidates_for(account_id, calendar)
    bank_lines = [_as_bank_line(line) for line in inside]
    proposals = propose(bank_lines, candidates.rows)
    explained = {
        line.line_id for proposal in proposals for line in proposal.lines
    }
    spoken_for = {
        (row.kind, row.row_id)
        for proposal in proposals for row in proposal.rows
    }
    # The rows the STATEMENT could have shown and did not.
    #
    # **The span is every RECORDED line's, not the unmatched ones'.**  Taking
    # it from the leftovers made the window SHRINK as matches were accepted --
    # matching the earliest or latest line silently dropped app rows from the
    # list, and matching every line left no span at all -- while the card went
    # on claiming these "fall inside the span your statement covers".
    #
    # **A row is measured on the WINDOW the app expects it in**, which is its
    # settle day where it has one and its projection's span where it does not.
    # Using "undated is always in" put every forward projection on the account
    # into the list: 712 rows on the developer's own, most of them dated months
    # ahead.  A projection the bank could not yet have shown is not a payment
    # the bank failed to make.  Both found by adversarial review 2026-08-17.
    covered = _covered_span(account_id)
    unmatched_rows = tuple(
        row for row in candidates.rows
        if (row.kind, row.row_id) not in spoken_for
        and _could_have_been_shown(row, covered)
    )
    unmatched = [
        line for line in bank_lines if line.line_id not in explained
    ]
    return ReviewSet(
        proposals=tuple(proposals),
        unmatched=tuple(unmatched),
        unmatched_rows=unmatched_rows,
        accepted=tuple(_accepted_groups(owner_id, account_id)),
        creatable=_creatable_lines(
            calendar, unmatched, destinations_for(owner_id, account_id),
        ),
        bounds=ReviewBounds(
            calendar_opens=opens,
            before_calendar_count=len(before),
            before_calendar_last_day=(
                max(line.posted_on for line in before) if before else None
            ),
            crowded_days=tuple(skipped_group_days(candidates.rows)),
            unpriceable_count=len(candidates.unpriceable_ids),
        ),
    )
