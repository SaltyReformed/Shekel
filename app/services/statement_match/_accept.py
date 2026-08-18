"""The ONE door that records a match, and the only place here that MOVES MONEY.

An accepted match asserts that a set of bank lines and a set of the app's own
rows are ONE movement.  Two things follow from that assertion and this door
does both:

* every member row takes the bank's posted day, which SETTLES a row still
  Projected and CORRECTS one whose recorded day was wrong;
* the correspondence itself is recorded, so a re-import does not re-propose it,
  an undo has something to delete, and plan steps ``balance:X-f3a-2`` and
  ``balance:X-f3c`` have the provenance ruling **R-FT** promised them.

**It does NOT write ``reconciled_by_id``, and that is ruling R-FV.**  That
column names an ``account_anchor_history`` row -- a balance the owner asserted
by hand -- and a bank line is not one.  What it records, *which declared
balance already contains this row*, is DERIVABLE from the match once a
statement carries the line, where the match is not derivable from it; so this
door stores the fact and leaves the derivation alone.  The settle doors it
calls RELEASE any prior link as they move the day, which is correct: that link
recorded a statement showing this money on a day the bank has just contradicted.

**Every refusal fires before anything is written.**  The ids are re-derived
under the owner's own scope, the group is checked for balance, and only then
does a settle verb run -- so a refused match leaves the database exactly as it
was without depending on the rollback, the same discipline
``statement_import.record_statement`` states for itself.

**The settle verbs are the app's own, never restated.**  An ordinary row goes
through ``transaction_service.apply_requested_status``, the route layer's one
status entry point; a transfer SHADOW through ``transfer_service``, because
``CLAUDE.md`` transfer invariant 4 admits no direct mutation of one and
``settle_transaction`` refuses it outright; a purchase through
``entry_service.update_entry``.  A matcher that stamped ``settled_on`` itself
would be a fourth settle door, which is exactly what ruling **R-FA** exists to
prevent.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no Flask import.  It MUTATES and does NOT commit -- the
route owns the unit of work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.exceptions import ValidationError
from app.extensions import db
from app.models.statement_import import BankStatementLine
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import (
    entry_service,
    transaction_service,
    transfer_service,
)
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_MATCHED,
    EVT_STATEMENT_MATCH_RELEASED,
    log_event,
)
from app.utils.money import round_money

from ._candidates import candidates_for
from ._offers import CandidateRow, MatchSubmission, RowKind

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AcceptedMatch:
    """What accepting one match did.

    Attributes:
        match_id: The ``budget.statement_matches`` row recording the act.
        posts_on: The day every member row now records the money as having
            moved.
        amount: The signed figure both sides agreed on.
        settled_count: How many member rows were still Projected and are now
            settled -- the bank's evidence that money moved, applied.
        corrected_count: How many were already settled on a DIFFERENT day and
            had it moved.  The two counts partition the rows that changed; a
            row already settled on the bank's own day is in neither, and
            reporting it as "corrected" would claim work that did not happen.
        line_count: How many bank lines the act explains.
    """

    match_id: int
    posts_on: date
    amount: Decimal
    settled_count: int
    corrected_count: int
    line_count: int


def _load_lines(
    account_id: int, line_ids: "frozenset[int]",
) -> "list[BankStatementLine]":
    """Return the submitted bank lines, refusing any this account cannot match.

    **A line ALREADY in a match is refused here, symmetrically with the row
    side**, and the asymmetry was a real defect rather than an omission.
    ``uq_statement_match_members_line`` refuses the second act either way, so
    nothing could be corrupted -- but it arrives as an ``IntegrityError`` AFTER
    ``_apply_day`` has moved a settle day, which reaches the user as
    "Something went wrong" and logs a full traceback at ERROR for an ordinary
    stale page.  The hand-build form makes it easy to reach: its checkboxes
    render ``review.unmatched``, so one tab submitting a line another tab has
    just matched is two clicks.  Found by adversarial security review
    2026-08-17.

    Args:
        account_id: The account the match is for.
        line_ids: The submitted ids.

    Returns:
        The lines, ascending by posted day then id.

    Raises:
        ValidationError: When an id names no line on this account, or names one
            another match already explains.  A REFUSAL rather than a silent
            skip, unlike the reconcile panel's bulk tick: that door narrows a
            set the user swept, and this one names specific rows on purpose, so
            dropping a member would change what the match MEANS while
            reporting success.
    """
    lines = (
        db.session.query(BankStatementLine)
        .filter(
            BankStatementLine.account_id == account_id,
            BankStatementLine.id.in_(line_ids),
        )
        .order_by(BankStatementLine.posted_on, BankStatementLine.id)
        .all()
    )
    if len(lines) != len(line_ids):
        raise ValidationError(
            "One of the statement lines in this match is no longer on this "
            "account.  Reload the page and try again -- nothing was changed."
        )
    already = (
        db.session.query(StatementMatchMember.bank_statement_line_id)
        .filter(
            StatementMatchMember.account_id == account_id,
            StatementMatchMember.bank_statement_line_id.in_(line_ids),
        )
        .first()
    )
    if already is not None:
        raise ValidationError(
            "One of these statement lines is already matched to something "
            "else.  Undo that match first if it is wrong.  Nothing was "
            "changed."
        )
    return lines


def _load_rows(
    submission: MatchSubmission,
) -> "list[CandidateRow]":
    """Return the submitted app rows as priced candidates, refusing the rest.

    **Re-derived through :func:`~._candidates.candidates_for` rather than
    queried directly**, so the set this door may act on is exactly the set the
    screen may offer.  One scope, shared by the reader and the writer, is the
    security property ``reconcile_service`` is built on: an id belonging to
    another user, another account, a non-contributing row, a card purchase or
    a row already spoken for by another match is not a candidate and cannot be
    matched by crafting a request.

    Args:
        submission: What the owner accepted.

    Returns:
        The candidates the submission names, transactions first.

    Raises:
        ValidationError: When an id names nothing the screen could have
            offered.
    """
    wanted = {
        (RowKind.TRANSACTION, row_id) for row_id in submission.transaction_ids
    } | {
        (RowKind.PURCHASE, row_id) for row_id in submission.entry_ids
    }
    found = [
        row
        for row in candidates_for(
            submission.owner_id, submission.account_id,
        ).rows
        if (row.kind, row.row_id) in wanted
    ]
    if len(found) != len(wanted):
        raise ValidationError(
            "One of the rows in this match is no longer available to match -- "
            "it may have been deleted, cancelled, or matched to another "
            "statement line.  Reload the page and try again; nothing was "
            "changed."
        )
    return found


def _reject_empty_side(
    lines: "list[BankStatementLine]", rows: "list[CandidateRow]",
) -> None:
    """Refuse a match missing either half.

    A match is a claim that two things are the SAME movement, so one thing is
    not a match: a bank line with no app row is what plan step
    ``bank_import:X-f6a-3`` turns into a purchase, and an app row with no bank
    line is a row the statement did not show.

    Args:
        lines: The submitted bank lines.
        rows: The submitted app rows.

    Raises:
        ValidationError: When either side is empty.
    """
    if not lines or not rows:
        raise ValidationError(
            "A match needs at least one statement line and at least one row "
            "from your budget.  Nothing was changed."
        )


def _reject_parent_and_its_own_purchase(
    rows: "list[CandidateRow]", account_id: int,
) -> None:
    """Refuse an envelope and a purchase under it -- in this match OR another.

    **It would count the same money twice**, and no schema can see it.  An
    envelope's cash leg SUBTRACTS the purchases that have posted and INCLUDES
    the ones that have not (ruling **R-FM**), so naming both sums that purchase
    in two terms.

    **Both directions are checked ACROSS matches, and the cross-match half is
    the one that actually moves money.**  Within one match the two sides are
    priced together and refuse together.  Across two, each balances on its own
    and the second one FALSIFIES the first: measured on a production clone,
    envelope 2280 prices at `-265.69` (its four unposted purchases included)
    and its purchase 78 at `-18.64`; matching 2280 first and 78 second stamps
    78's posting day, which drops 2280's leg to `-247.05` -- so two matched
    line-sets worth `-284.33` are backed by `-265.69` of ledger and the
    projected balance reads `$18.64` HIGH.  The screen's hand-build form lists
    an envelope and its purchases side by side, so it is two clicks.  Found by
    adversarial financial review 2026-08-17.

    Args:
        rows: The submitted app rows, already priced.
        account_id: The account being matched into, whose existing matches the
            cross-match half is read from.

    Raises:
        ValidationError: When a submitted purchase's parent is submitted or
            already matched, or a submitted envelope holds a purchase that is.
    """
    transaction_ids = {
        row.row_id for row in rows if row.kind is RowKind.TRANSACTION
    }
    entry_ids = {row.row_id for row in rows if row.kind is RowKind.PURCHASE}
    if not transaction_ids and not entry_ids:
        return
    matched_transactions, matched_entries = _matched_relatives(account_id)
    clash = db.session.query(TransactionEntry.id).filter(
        db.or_(
            db.and_(
                TransactionEntry.id.in_(entry_ids or {0}),
                TransactionEntry.transaction_id.in_(
                    (transaction_ids | matched_transactions) or {0},
                ),
            ),
            db.and_(
                TransactionEntry.id.in_(matched_entries or {0}),
                TransactionEntry.transaction_id.in_(transaction_ids or {0}),
            ),
        )
    ).first()
    if clash is not None:
        raise ValidationError(
            "This match would count the same money twice: it names an "
            "envelope and a purchase inside it -- here, or through a match "
            "you have already accepted.  The envelope's figure already covers "
            "its own purchases.  Match the envelope OR its purchases, not "
            "both.  Nothing was changed."
        )


def _matched_relatives(account_id: int) -> "tuple[set[int], set[int]]":
    """Return every transaction and purchase *account_id* has already matched.

    One statement over this account's members, because the clash test above has
    to see a parent/child relation that spans two separate acts.

    Args:
        account_id: The account being matched into.

    Returns:
        ``(matched transaction ids, matched purchase ids)``.
    """
    rows = db.session.query(
        StatementMatchMember.transaction_id,
        StatementMatchMember.transaction_entry_id,
    ).filter(StatementMatchMember.account_id == account_id).all()
    return (
        {row[0] for row in rows if row[0] is not None},
        {row[1] for row in rows if row[1] is not None},
    )


def _reject_unbalanced(
    lines: "list[BankStatementLine]", rows: "list[CandidateRow]",
) -> None:
    """Refuse a match whose two sides do not sum to the same figure.

    **The developer's ruling of 2026-08-17, and the alternative was measured.**
    On their own 2026-08-16 statement, 6 of 16 payroll deposits sit
    `$0.05`-`$0.06` above what the app's rows sum to, because the projected
    paycheck distributes an annual rounding residue (finding **N-299**).
    Absorbing that into a tolerance would silence the one instrument that can
    see it; apportioning it across the members would need a rule about which
    member is wrong, which is a decision about a paycheck and not a matcher's
    to take.  So the door refuses and NAMES the difference, and the owner
    corrects a member's amount and matches again.

    Args:
        lines: The submitted bank lines.
        rows: The submitted app rows, already priced.

    Raises:
        ValidationError: When the sums differ, with the figures in the message.
    """
    bank = round_money(sum((line.amount for line in lines), Decimal("0.00")))
    app_side = round_money(
        sum((row.cash_amount for row in rows), Decimal("0.00")),
    )
    if bank == app_side:
        return
    raise ValidationError(
        f"These do not add up.  Your bank shows {bank:+,.2f} and the "
        f"{len(rows)} row(s) you picked come to {app_side:+,.2f}, a difference "
        f"of {bank - app_side:+,.2f}.  Correct the amount on one of your rows "
        f"first, then match them -- the bank is the record of what moved.  "
        f"Nothing was changed."
    )


def _apply_day(
    row: CandidateRow, submission: MatchSubmission, posts_on: date,
) -> str:
    """Move one member row onto *posts_on* through its own settle door.

    The dispatch, and every arm is an existing verb rather than a column write:

    * a PURCHASE stamps its posting day through ``entry_service.update_entry``,
      which refuses a future day and releases the row's clearing link;
    * a transfer SHADOW goes through ``transfer_service`` -- ``settle_transfer``
      when it is still Projected, ``update_transfer`` when only the day moves,
      because a settled transfer is an idempotent no-op for the first;
    * every other transaction goes through
      ``transaction_service.apply_requested_status``, with the row's OWN status
      when it is already settled (an edit that changes only the day is an
      identity transition) and its type's settled status when it is not.

    Args:
        row: The member being moved.
        submission: The act, for its owner id.
        posts_on: The day the bank states.

    Returns:
        ``"settled"`` when the row entered the settled band, ``"corrected"``
        when it was already settled on a different day, ``"unchanged"`` when it
        already carried the bank's own day.

    Raises:
        ValidationError: From a settle door -- a future day, a posting day
            before its purchase, an illegal transition.  Surfaced to the owner.
        PostingError: From a ledger reconcile.  Fails loud.
    """
    # **"unchanged" requires the row to be SETTLED as well as correctly
    # dated.**  Deciding on the day alone would let a Projected row carrying
    # the bank's own day be recorded as matched and left Projected -- the bank
    # line would read explained while the money was never booked.  The status
    # seam should make that state unreachable (it refuses a day on a
    # non-settled status and clears the column on the way out), but no CHECK
    # pairs the two columns, so the arm does not rest on that discipline.
    outcome = (
        "unchanged" if row.is_settled and row.settled_on == posts_on
        else "corrected" if row.is_settled
        else "settled"
    )
    if outcome == "unchanged":
        return outcome

    if row.kind is RowKind.PURCHASE:
        entry_service.update_entry(
            row.row_id, submission.owner_id, settled_on=posts_on,
        )
        return outcome

    if row.transfer_id is not None:
        if row.is_settled:
            transfer_service.update_transfer(
                row.transfer_id, submission.owner_id, settled_on=posts_on,
            )
        else:
            transfer_service.settle_transfer(
                row.transfer_id, submission.owner_id, settled_on=posts_on,
            )
        return outcome

    txn = db.session.get(Transaction, row.row_id)
    target_status_id = (
        txn.status_id if row.is_settled
        else transaction_service.settled_status_id(txn)
    )
    transaction_service.apply_requested_status(
        txn, target_status_id, settled_on=posts_on,
    )
    return outcome


def _record(
    submission: MatchSubmission,
    lines: "list[BankStatementLine]",
    rows: "list[CandidateRow]",
) -> StatementMatch:
    """Stage the match act and one member per subject.

    Args:
        submission: The act, for its owner and account.
        lines: The bank lines it explains.
        rows: The app rows it names.

    Returns:
        The staged, flushed :class:`~app.models.statement_match.StatementMatch`.
    """
    match = StatementMatch(
        account_id=submission.account_id, user_id=submission.owner_id,
    )
    db.session.add(match)
    # The members carry the act's id in a composite key, so the act must exist
    # before they are staged.
    db.session.flush()
    for line in lines:
        db.session.add(StatementMatchMember(
            match_id=match.id, account_id=submission.account_id,
            bank_statement_line_id=line.id,
        ))
    for row in rows:
        db.session.add(StatementMatchMember(
            match_id=match.id,
            account_id=submission.account_id,
            transaction_id=(
                row.row_id if row.kind is RowKind.TRANSACTION else None
            ),
            transaction_entry_id=(
                row.row_id if row.kind is RowKind.PURCHASE else None
            ),
        ))
    db.session.flush()
    return match


def accept_match(submission: MatchSubmission) -> AcceptedMatch:
    """Record that these bank lines ARE these app rows, and move the day.

    The whole act, in the order its refusals have to happen: the ids are
    re-derived under the owner's scope, both sides are checked for presence and
    for balance, and only then does any settle door run.

    **The purchases move first**, for the reason
    ``reconcile_service.record_reconciliation`` states for its own order: a
    purchase's posting day changes what its parent envelope's cash leg is worth
    (ruling **R-FM**), so settling a parent first and stamping its purchase
    afterwards would book the parent at a figure the purchase then moves.
    :func:`_reject_parent_and_its_own_purchase` makes that pairing unreachable
    in ONE match and across matches alike, so no submission this door accepts
    can actually hit the interaction today.  **The order is kept anyway and the
    reason is stated rather than invented**: a first draft justified it by "the
    parent is in a different match accepted in the same request", which cannot
    happen -- one POST accepts exactly one match.  What the order really buys
    is that the rule survives the guard: if a later step widens what a match
    may name, the sequence is already the safe one rather than something that
    has to be rediscovered.

    Does NOT commit -- the route owns the session boundary.

    Args:
        submission: What the owner accepted, ids only.

    Returns:
        The :class:`AcceptedMatch`.

    Raises:
        ValidationError: On any of this door's refusals or a settle door's.
            A 400: every one of them is reachable by an ordinary owner working
            from a stale page.
        PostingError: From a ledger reconcile, on a broken invariant.  Fails
            loud rather than rendering as a designed refusal.
    """
    lines = _load_lines(submission.account_id, submission.line_ids)
    rows = _load_rows(submission)
    _reject_empty_side(lines, rows)
    _reject_parent_and_its_own_purchase(rows, submission.account_id)
    _reject_unbalanced(lines, rows)

    # THE LATEST bank day, not the earliest.  A row is not wholly moved until
    # its last line posts, so the earliest would let a balance asserted between
    # the two absorb money that had not all left -- the class of double count
    # the walk's day partition exists to make unspellable.  With one line,
    # which is every proposal the app offers automatically, the two agree.
    posts_on = max(line.posted_on for line in lines)

    outcomes = [
        _apply_day(row, submission, posts_on)
        for row in sorted(
            rows, key=lambda row: (row.kind is not RowKind.PURCHASE, row.row_id),
        )
    ]
    match = _record(submission, lines, rows)

    amount = round_money(sum((line.amount for line in lines), Decimal("0.00")))
    accepted = AcceptedMatch(
        match_id=match.id,
        posts_on=posts_on,
        amount=amount,
        settled_count=outcomes.count("settled"),
        corrected_count=outcomes.count("corrected"),
        line_count=len(lines),
    )
    log_event(
        _logger, logging.INFO, EVT_STATEMENT_MATCHED, BUSINESS,
        "A bank statement's lines were matched to the rows they explain.",
        user_id=submission.owner_id,
        account_id=submission.account_id,
        match_id=accepted.match_id,
        posts_on=posts_on.isoformat(),
        line_count=accepted.line_count,
        row_count=len(rows),
        settled_count=accepted.settled_count,
        corrected_count=accepted.corrected_count,
    )
    return accepted


def release_match(match_id: int, owner_id: int, account_id: int) -> int:
    """Undo one match, leaving the days it wrote alone.

    **Deleting the record does NOT put the days back, and that is the honest
    direction.**  A settle day is what the app knows about when money moved,
    and the bank is still the best evidence it has; reverting one because the
    owner unlinked a record would throw away a correction in order to tidy a
    relation.  What the release restores is the QUESTION -- the bank lines
    become unexplained again and the rows become matchable again -- which is
    the repair door finding **N-302** says a refusal owes.

    Does NOT commit -- the route owns the session boundary.

    Args:
        match_id: The act to release.
        owner_id: The user the route proved owns the account.
        account_id: The account it must belong to.

    Returns:
        How many member rows were deleted.

    Raises:
        ValidationError: When *match_id* names no act on this owner's account
            -- the set-operation form of the project's "404 for both not-found
            and not-yours" rule, raised rather than ignored because this door
            names ONE act on purpose.
    """
    match = (
        db.session.query(StatementMatch)
        .filter(
            StatementMatch.id == match_id,
            StatementMatch.account_id == account_id,
            StatementMatch.user_id == owner_id,
        )
        .one_or_none()
    )
    if match is None:
        raise ValidationError(
            "That match is no longer there.  Reload the page; nothing was "
            "changed."
        )
    released = len(match.members)
    db.session.delete(match)
    db.session.flush()
    log_event(
        _logger, logging.INFO, EVT_STATEMENT_MATCH_RELEASED, BUSINESS,
        "A statement match was released; its lines are unexplained again.",
        user_id=owner_id, account_id=account_id, match_id=match_id,
        released_count=released,
    )
    return released
