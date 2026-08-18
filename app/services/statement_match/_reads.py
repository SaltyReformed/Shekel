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

from sqlalchemy.orm import selectinload

from app.exceptions import AmountUnresolvable
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.statement_import import BankStatementLine
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import cash_ledger
from app.utils.balance_predicates import is_balance_contributing
from app.utils.money import round_money

from ._candidates import candidates_for
from ._offers import BankLine, CandidateRow, MatchProposal
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
        bounds: What this pass did NOT look at (:class:`ReviewBounds`).
    """

    proposals: "tuple[MatchProposal, ...]"
    unmatched: "tuple[BankLine, ...]"
    unmatched_rows: "tuple[CandidateRow, ...]"
    accepted: "tuple[AcceptedGroup, ...]"
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


def _inside(day: "date | None", covered: "tuple[date, date] | None") -> bool:
    """Return whether *day* falls in *covered*.

    Args:
        day: The day to test, or ``None`` for a row the app can date no way at
            all.
        covered: The recorded span, or ``None`` when nothing is recorded.

    Returns:
        Whether the statement could have shown a movement on that day.  A row
        with no day of any kind is IN: the app has no basis for excluding it,
        and saying so is better than dropping it silently.
    """
    if covered is None:
        return False
    return day is None or covered[0] <= day <= covered[1]


def _calendar_opens(owner_id: int) -> "date | None":
    """Return the first day this owner's pay calendar covers.

    Args:
        owner_id: The user.

    Returns:
        The earliest pay-period start, or ``None`` for an owner with no
        periods.  Read from the periods themselves rather than from the
        schedule, because what bounds a match is where rows can EXIST.
    """
    return (
        db.session.query(db.func.min(PayPeriod.start_date))
        .filter(PayPeriod.user_id == owner_id)
        .scalar()
    )


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
    opens = _calendar_opens(owner_id)
    recorded = _unmatched_lines(account_id)
    before = [
        line for line in recorded
        if opens is not None and line.posted_on < opens
    ]
    before_ids = {line.id for line in before}
    inside = [line for line in recorded if line.id not in before_ids]

    candidates = candidates_for(owner_id, account_id)
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
    # **A row is measured on the day the app EXPECTS it**, which is its settle
    # day where it has one and its projection's day where it does not.  Using
    # "undated is always in" put every forward projection on the account into
    # the list: 712 rows on the developer's own, most of them dated months
    # ahead.  A projection the bank could not yet have shown is not a payment
    # the bank failed to make.  Both found by adversarial review 2026-08-17.
    covered = _covered_span(account_id)
    unmatched_rows = tuple(
        row for row in candidates.rows
        if (row.kind, row.row_id) not in spoken_for
        and _inside(row.settled_on or row.expected_on, covered)
    )
    return ReviewSet(
        proposals=tuple(proposals),
        unmatched=tuple(
            line for line in bank_lines if line.line_id not in explained
        ),
        unmatched_rows=unmatched_rows,
        accepted=tuple(_accepted_groups(owner_id, account_id)),
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
