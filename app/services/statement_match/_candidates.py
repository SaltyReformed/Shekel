"""Which of the app's rows a bank line could be, priced as the bank sees them.

The READ half of the matcher.  It answers one question -- *what has this
account recorded that a statement could be showing* -- over the two row kinds
the app holds a cash movement as, and prices each with its SIGNED effect on the
account so a comparison against ``bank_statement_lines.amount`` is a
subtraction rather than a sign negotiation.

**It admits SETTLED and PROJECTED rows alike, and that is the developer's
ruling of 2026-08-17.**  A statement is evidence that money moved: for a row
the app already settled the match CORRECTS its day, and for one still Projected
it SETTLES it.  Measured on the developer's own 2026-08-16 export against a
production clone, both arms are live -- of 58 lines an exact-amount predicate
pairs uniquely with a row, 35 carry a day the app got wrong, and 11 rows inside
the statement's own span had never been marked as having happened at all.

**Pricing is the cash ledger's, never restated here.**  A settled row is worth
``cash_ledger.settled_cash_leg``; a projected one is worth
``cash_ledger.cash_leg_of`` over what its own settle verb says it would book --
``transaction_service.settle_amount`` for an ordinary row and
``transfer_service.settle_amount`` for a shadow leg, which is the same
partition ``reconcile_service``'s two arms are built on.  A matcher that
computed its own figure could offer a line against a number no door would book.

**An already-matched row is not a candidate.**  ``uq_statement_match_members_*``
would refuse the second act anyway; excluding it here is what stops the screen
offering a row whose acceptance is guaranteed to fail.

Services-boundary discipline (``CLAUDE.md`` Architecture): reads only, plain
data in, frozen dataclasses out, no Flask import, no clock read.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import contains_eager, joinedload, selectinload

from app.exceptions import AmountUnresolvable
from app.extensions import db
from app.models.statement_match import StatementMatchMember
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transaction_entry import TransactionEntry
from app.services import cash_ledger, transaction_service, transfer_service
from app.utils.balance_predicates import (
    balance_contributing_clause,
    not_archived_clause,
)

from ._offers import CandidateRow, Candidates, RowKind


def _matched_subject_ids(
    account_id: int,
) -> "tuple[set[int], set[int], set[int]]":
    """Return what this account has already matched, by subject kind.

    One statement over ``statement_match_members`` rather than three: the
    table's rows are an exclusive arc, so a single scan of the account's
    members partitions itself.

    Args:
        account_id: The account whose matches to read.

    Returns:
        ``(line_ids, transaction_ids, entry_ids)`` -- the subjects already
        spoken for.
    """
    rows = (
        db.session.query(
            StatementMatchMember.bank_statement_line_id,
            StatementMatchMember.transaction_id,
            StatementMatchMember.transaction_entry_id,
        )
        .filter(StatementMatchMember.account_id == account_id)
        .all()
    )
    lines = {row[0] for row in rows if row[0] is not None}
    transactions = {row[1] for row in rows if row[1] is not None}
    entries = {row[2] for row in rows if row[2] is not None}
    return lines, transactions, entries


def _price(txn: Transaction) -> "Decimal | None":
    """Return *txn*'s signed cash effect, or ``None`` when no rule prices it.

    The one branch in this module, and it is the settled / projected split
    rather than a money rule of its own:

    * a SETTLED row owns its figure, which
      :func:`~app.services.cash_ledger.settled_cash_leg` reads;
    * a PROJECTED row is worth what settling it would book, which is its own
      arm's ``settle_amount`` -- the transfer service's for a shadow leg and
      the transaction service's for its complement.

    **Neither ``settle_amount`` can refuse a row this module's scope admits**,
    and that is why there is no guard against one here.  Both refuse exactly a
    soft-deleted row and a row on the wrong side of the shadow partition;
    :func:`_transaction_candidates` excludes the first through
    ``balance_contributing_clause`` and the dispatch above IS the second.  A
    ``try`` around them would be a guard nothing could ever observe, which this
    project has twice measured as worse than none.

    **``AmountUnresolvable`` is a different thing and is REPORTED rather than
    swallowed or raised, and BOTH branches are inside the guard.**  A first
    draft put the settled branch outside it, which is the defect the paragraph
    below describes happening anyway: ``settled_cash_leg`` reaches
    ``owned_contribution``, which RAISES for a derived row, so the first
    per-kind cutover would have taken the whole review screen down for one
    settled row.  Found by adversarial security review 2026-08-17.

    It means the amount model had no rule for the row
    -- latent today, because every production row still owns its figure, and
    live from the first per-kind cutover (plan steps ``balance:X-au-d`` on).  A
    matcher cannot offer a row it cannot price without guessing, so the row
    leaves the candidate set; but a reader that dropped it silently would hide
    a broken row, and one that raised would make the whole review screen
    unreachable for one bad row with no in-app repair -- which is finding
    **N-302**'s shape.  :class:`~._offers.Candidates` carries the count instead
    and the screen says so.

    Args:
        txn: The row to price, with ``entries`` loaded.

    Returns:
        Its signed cash effect on this account, or ``None`` when the amount
        model cannot answer for it.
    """
    settle_amount = (
        transfer_service.settle_amount if txn.transfer_id is not None
        else transaction_service.settle_amount
    )
    try:
        if txn.status.is_settled:
            return cash_ledger.settled_cash_leg(txn)
        return cash_ledger.cash_leg_of(txn, settle_amount(txn))
    except AmountUnresolvable:
        return None


def _label(txn: Transaction) -> str:
    """Return what to call *txn* on the review screen.

    The row's own name, with the parent transfer named where the row is a
    SHADOW: two accounts hold a leg each and both are called "Transfer to
    Mortgage", so a reviewer reading a checking statement has to be told which
    side they are being offered.

    Args:
        txn: The row being offered.

    Returns:
        Its display label.
    """
    if txn.transfer_id is None:
        return txn.name
    return f"{txn.name} (transfer leg)"


def _transaction_candidates(
    owner_id: int, account_id: int, matched: "set[int]",
) -> "tuple[list[CandidateRow], list[int]]":
    """Return the transactions on *account_id* a statement could be showing.

    Scope, and every clause is load-bearing:

    * the row is on THIS account -- a statement is one bank's record of one
      account, and matching across accounts would book money against a
      statement that never showed it;
    * it CONTRIBUTES to a balance and is not soft-deleted
      (:func:`~app.utils.balance_predicates.balance_contributing_clause`) -- a
      Credit or Cancelled row is not money this account moved, and it is the
      shared gate every cash reader here narrows with rather than a filter
      written again;
    * its pay period is this OWNER'S -- ownership, reached the way
      ``Transaction`` is scoped (it carries no ``user_id`` of its own);
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
      ``cash_ledger.settled_cash_leg`` makes for its own total guard;
    * it is not already matched.

    Not scoped by ``scenario_id``, for the same reason
    ``reconcile_service._rows.outstanding_scope`` is not: Phase 1 is
    baseline-only, so ``account_id`` fully isolates the set today, and when
    what-if scenarios land every arm must thread an operating scenario.

    Args:
        owner_id: The user whose rows may be offered.
        account_id: The cash account the statement is for.
        matched: The transaction ids this account has already matched, read
            ONCE by :func:`candidates_for` and threaded rather than re-queried
            per arm.

    Returns:
        ``(candidates, unpriceable)`` -- one
        :class:`~._offers.CandidateRow` per offerable row, oldest recorded day
        first and unrecorded days last with the id breaking ties (a
        deterministic order, so the proposals a screen shows do not depend on
        what the planner happened to return), and the ids of the rows the
        amount model could not price.
    """
    rows = (
        db.session.query(Transaction)
        .options(
            selectinload(Transaction.entries),
            joinedload(Transaction.pay_period),
            joinedload(Transaction.template),
        )
        .filter(
            Transaction.account_id == account_id,
            balance_contributing_clause(),
            Transaction.pay_period.has(user_id=owner_id),
            db.or_(
                Transaction.transfer_id.is_(None),
                Transaction.transfer_id.in_(
                    db.session.query(Transfer.id).filter(
                        Transfer.user_id == owner_id,
                        Transfer.is_deleted.is_(False),
                    )
                ),
            ),
        )
        .all()
    )
    candidates = []
    unpriceable = []
    for txn in rows:
        if txn.id in matched:
            continue
        amount = _price(txn)
        if amount is None:
            unpriceable.append(txn.id)
            continue
        if not amount:
            continue
        candidates.append(CandidateRow(
            kind=RowKind.TRANSACTION,
            row_id=txn.id,
            label=_label(txn),
            cash_amount=amount,
            settled_on=txn.settled_on,
            is_settled=txn.status.is_settled,
            transfer_id=txn.transfer_id,
            expected_on=txn.pay_period.start_date,
        ))
    candidates.sort(
        key=lambda row: (row.settled_on is None, row.settled_on, row.row_id),
    )
    return candidates, unpriceable


def _purchase_candidates(
    owner_id: int, account_id: int, matched: "set[int]",
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
    * its parent is NOT ARCHIVED.  ``entry_service.update_entry`` refuses every
      write against a `Settled` parent (finding **N-229**: an archived row's
      purchases are history), and `balance_contributing_clause` does not
      exclude that status -- so without this clause the screen offered a row
      whose acceptance raises MID-LOOP, after other members had already been
      written.  That would falsify this package's own claim that every refusal
      fires before anything is written.  0 archived rows on production today;
      the full-edit Status dropdown reaches it.  Found by adversarial financial
      review 2026-08-17.

    Args:
        owner_id: The user whose purchases may be offered.
        account_id: The cash account the statement is for.
        matched: The purchase ids already matched, threaded for the reason
            :func:`_transaction_candidates` gives.

    Returns:
        One :class:`~._offers.CandidateRow` per offerable purchase, ordered as
        :func:`transaction_candidates` orders its own.  A purchase's cash is
        always money LEAVING, so its amount is the negated stored figure -- the
        sign convention stated once in
        :mod:`app.models.statement_import`.
    """
    rows = (
        db.session.query(TransactionEntry)
        .join(TransactionEntry.transaction)
        .options(contains_eager(TransactionEntry.transaction))
        .filter(
            TransactionEntry.account_id == account_id,
            TransactionEntry.is_credit.is_(False),
            balance_contributing_clause(),
            not_archived_clause(Transaction),
            Transaction.pay_period.has(user_id=owner_id),
        )
        .all()
    )
    return sorted(
        (
            CandidateRow(
                kind=RowKind.PURCHASE,
                row_id=entry.id,
                label=f"{entry.transaction.name}: {entry.description}",
                cash_amount=-Decimal(str(entry.amount)),
                settled_on=entry.settled_on,
                is_settled=entry.settled_on is not None,
                parent_id=entry.transaction_id,
                expected_on=entry.purchased_on,
            )
            for entry in rows
            if entry.id not in matched and entry.amount
        ),
        key=lambda row: (row.settled_on is None, row.settled_on, row.row_id),
    )


def candidates_for(owner_id: int, account_id: int) -> Candidates:
    """Return every row on *account_id* a statement could be showing.

    **The ONE entry point, and the reason it exists is that the two arms share
    a read.**  Both need to know what is already matched, and asking twice in
    one request is a redundant producer call -- the shape this project treats
    as a DRY violation rather than as a cost.  It is resolved once here and
    threaded.

    Args:
        owner_id: The user whose rows may be offered.
        account_id: The cash account the statement is for.

    Returns:
        A :class:`~._offers.Candidates`.  Its ``rows`` are the transactions and
        the purchases TOGETHER, each arm's own order preserved and transactions
        first -- a union rather than a pair, because every consumer asks the
        same question of both kinds: a bank line does not know which table its
        counterpart lives in.
    """
    _, matched_transactions, matched_entries = _matched_subject_ids(account_id)
    transactions, unpriceable = _transaction_candidates(
        owner_id, account_id, matched_transactions,
    )
    return Candidates(
        rows=transactions
        + _purchase_candidates(owner_id, account_id, matched_entries),
        unpriceable_ids=tuple(unpriceable),
    )
