"""Which of the app's rows a bank line could be or become, priced as it sees them.

The OFFER-SET half of the matcher.  Two questions, and they are here together
because they are the same question about two acts:

* :func:`candidates_for` -- *what has this account recorded that a statement
  could be showing*, over the two row kinds the app holds a cash movement as,
  each priced with its SIGNED effect on the account so a comparison against
  ``bank_statement_lines.amount`` is a subtraction rather than a sign
  negotiation;
* :func:`destinations_for` -- *what budget line could a statement line BECOME a
  purchase against*, which is ruling **R-FS**'s third shape.

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

**Pricing is the cash ledger's, never restated here.**  A settled row is worth
``cash_ledger.settled_cash_leg``; a projected one is worth
``cash_ledger.cash_leg_of`` over what its own settle verb says it would book --
``transaction_service.settle_amount`` for an ordinary row and
``transfer_service.settle_amount`` for a shadow leg, which is the same
partition ``reconcile_service``'s two arms are built on.  A matcher that
computed its own figure could offer a line against a number no door would book.

Services-boundary discipline (``CLAUDE.md`` Architecture): reads only, plain
data in, frozen dataclasses out, no Flask import, no clock read.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import aliased, contains_eager, joinedload, selectinload

from app import ref_cache
from app.enums import SettledDayBasisEnum, SettlementBasisEnum
from app.exceptions import AmountUnresolvable
from app.extensions import db
from app.models.statement_match import StatementMatchMember
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transaction_entry import TransactionEntry
from app.services import cash_ledger, transaction_service, transfer_service
from app.services.settle_day import recorded_settle_day
from app.utils.balance_predicates import (
    balance_contributing_clause,
    not_archived_clause,
)

from ._creations import PurchaseDestination
from ._offers import CandidateRow, Candidates, RowKind


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

    Attributes:
        lines: The ``bank_statement_lines`` ids a match already explains.
        transactions: The ``transactions`` ids a match already names.
        entries: The ``transaction_entries`` ids a match already names.
    """

    lines: "frozenset[int]"
    transactions: "frozenset[int]"
    entries: "frozenset[int]"


def act_still_names_a_row():
    """Return the EXISTS that makes a membership a live CLAIM.

    **A match asserts that these bank lines ARE these app rows, and the app-row
    keys CASCADE** (``fk_statement_match_members_transaction_account`` /
    ``_entry_account``, ``ondelete="CASCADE"``).  So destroying the last app row
    an act names leaves the act holding its LINE alone -- and the line went on
    reading as explained, permanently, because "explained" was membership and
    nothing else.  It could then never be offered or matched again, whatever
    the review screen showed.

    **This is the invariant, and the writer beside it is the cleanup.**
    :mod:`app.services.match_withdrawal` deletes such an act at the five doors
    an owner presses, so the false record goes and the press can say which
    lines it freed.  It cannot cover them all: ``routes/templates/crud``
    hard-deletes and archives in BULK SQL, ``pay_period_write.retire_paydays``
    removes transactions through a database cascade, and a sixth door written
    next year will not know to call it.  A rule enforced by enumeration is a
    rule the next door forgets; a predicate in the one query that decides is
    not (adversarial review, 2026-08-25, which measured the template
    hard-delete reaching the state from a shipped button).

    **What those four doors still OWE, recorded here because ``ledger.md`` is
    AT its cap** (240 lines of the 260 the gate requires 20 lines of headroom
    in, which is the archival ruling ``bank_import:X-ga`` already named as
    owed): the LINE is correct through every one of them, but each leaves a
    DEAD act on the accepted-matches panel, and none of them says a word about
    destroying an accepted match.  The disclosure and a sweep for dead acts
    belong to ``bank_import:X-gf``, which rebuilds that screen.  The recurrence
    retire sweep is the one held by an IMPLICATION rather than a check --
    ``_maintain._rows_holding_owner_records`` reads ``settled_basis_id`` and an
    accepted match settles its row, so 0 of 71 matched rows were in scope when
    measured -- and that implication breaks the day anything matches a row with
    no figure basis.

    **Applying it to the WHOLE member scan is exact rather than convenient.**
    The EXISTS is true for every member of an act that holds an app row, so
    filtering the scan changes only the LINE set -- an act with no app-side
    member has no transaction or entry membership left to filter.

    Returns:
        A correlated ``EXISTS`` over the outer
        :class:`~app.models.statement_match.StatementMatchMember`.
    """
    sibling = aliased(StatementMatchMember)
    return (
        db.session.query(sibling)
        .filter(
            sibling.match_id == StatementMatchMember.match_id,
            db.or_(
                sibling.transaction_id.isnot(None),
                sibling.transaction_entry_id.isnot(None),
            ),
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
            StatementMatchMember.transaction_id,
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
        transactions=frozenset(row[1] for row in rows if row[1] is not None),
        entries=frozenset(row[2] for row in rows if row[2] is not None),
    )


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

    Args:
        candidates: The pass's derived offer set.
        matched: The claims as of NOW.

    Returns:
        The rows still offerable, in *candidates*' own order.
    """
    return [
        row for row in candidates.rows
        if row.row_id not in (
            matched.transactions if row.kind is RowKind.TRANSACTION
            else matched.entries
        )
    ]


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


def _price(txn: Transaction, basis: "cash_ledger.AmountBasis") -> "Decimal | None":
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
        basis: The PASS's
            :class:`~app.services.cash_ledger.AmountBasis`, built once by
            :meth:`~._scope.ReviewScope.build` and threaded (plan step
            X-au-j).  Every offered row built its own until then, which finding
            **N-309** measured at **609 salary-pricing and 609 loan-pricing
            constructions** over 825 candidates and `4.7 s` to render -- and
            ``amount_basis``'s own docstring had already named calling the
            derivations per row as finding **N-228**.  The same reason the
            calendar is a parameter one tier up, and the same shape a balance
            pass threads its ``BalanceContext`` for.

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
        return cash_ledger.cash_leg_of(txn, settle_amount(txn, basis))
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


def _day_basis(row) -> SettledDayBasisEnum | None:
    """Return WHICH KIND of settle day *row* records, or ``None`` for none.

    Plan step **X-az**.  ONE reading for both candidate constructors, because a
    transaction and a purchase carry the same pair of columns and answering the
    question twice is two chances to answer it differently -- which is exactly
    what the two ``reconciled_by_id`` tests this replaced were.

    **It reads the stored basis and derives nothing.**  The basis is what the
    row's own settle door recorded: ``observed`` for a day the bank posted,
    ``asserted`` for the day a balance was asserted FOR (an upper bound), and
    ``entered`` for the owner's own.  Nothing here re-classifies, because a
    re-classification is the defect finding **N-332** names.

    Its parameter is the two models' shared
    :class:`~app.models.mixins.SettleDatedMixin`, which is where the pair is
    declared once for both -- so this reads a column set the schema guarantees
    rather than one it happens to share.

    Args:
        row: A :class:`~app.models.transaction.Transaction` or a
            :class:`~app.models.transaction_entry.TransactionEntry`.

    Returns:
        Its :class:`~app.enums.SettledDayBasisEnum` member, or ``None`` when the
        row carries no settle day at all.

    Raises:
        ValueError: When the row carries a day and no basis, or a basis and no
            day (propagated from
            :func:`app.services.settle_day.recorded_settle_day`).  Each table's
            ``ck_*_settle_day_basis_pairing`` makes both unstorable, so reaching
            either means something wrote around every door.
    """
    recorded = recorded_settle_day(row)
    return None if recorded is None else recorded.basis


def purchase_candidate(entry: TransactionEntry) -> CandidateRow:
    """Return one purchase as the candidate value every consumer here shares.

    **ONE construction, because two callers build it and one of them writes
    money with it** (plan step ``bank_import:X-f6a-3c-2``):
    :func:`_purchase_candidates` builds it for every purchase this account
    holds, and :func:`~._create.create_purchase_from_line` builds it for the
    ONE purchase that door has just created -- a row no offer set derived
    before it can contain.  Two constructions would be two answers to what a
    purchase is worth and when the app believes it moved, on the two sides of a
    single match.

    A purchase's cash is always money LEAVING, so its amount is the negated
    stored figure -- the sign convention stated once in
    :mod:`app.models.statement_import`.

    Args:
        entry: The purchase, with its parent transaction loaded.

    Returns:
        Its :class:`~._offers.CandidateRow`.
    """
    return CandidateRow(
        kind=RowKind.PURCHASE,
        row_id=entry.id,
        label=f"{entry.transaction.name}: {entry.description}",
        cash_amount=-Decimal(str(entry.amount)),
        settled_on=entry.settled_on,
        is_settled=entry.settled_on is not None,
        # **A purchase always states its own figure.**  The two shapes whose
        # amount is a fact about another row are both TRANSACTIONS -- an
        # envelope worth its purchases and a payback worth the spend it repays
        # -- and a purchase is what those rows are made OF.  Ruling **R-GE** is
        # what lets a match correct one even under a settled parent, and it
        # bounds that permission by the DOOR rather than by the row, so nothing
        # here narrows it further.  See
        # :attr:`~._offers.CandidateRow.figure_is_correctable`.
        states_own_figure=True,
        parent_id=entry.transaction_id,
        # A purchase's budget clock is ONE day, so both ends of its window are
        # that day: it is not undated, it is dated on a clock the cash column
        # does not hold (ruling **R-FW**).
        expected_on=entry.purchased_on,
        expected_through=entry.purchased_on,
        # WHICH KIND of day ``settled_on`` is, READ rather than inferred (plan
        # step **X-az**, finding **N-332**).  It tested ``reconciled_by_id`` --
        # a different question, WHICH statement was seen to show this money --
        # and that inference was exact over the panel's bound and the bank's
        # observation and blind to the owner's own typed day, which carries no
        # link and so read as an observation.  ``CandidateRow.expected_window``
        # is the single reader and states the measurement.
        # WHICH REVISION the screen is about to show (plan step
        # ``bank_import:X-f6d-3``, finding **N-336**).  Read here rather than
        # by the reader that emits it, for the reason every fact beside it is:
        # the OFFER SET and the ACCEPT DOOR both build a candidate through this
        # one constructor, so the state a review is checked against and the
        # state it was taken against come from the same read.
        version_id=entry.version_id,
        settle_day_basis=_day_basis(entry),
    )


def transaction_candidate(
    txn: Transaction, calendar, amount: Decimal,
) -> "CandidateRow | None":
    """Return one transaction as the candidate value every consumer shares.

    :func:`purchase_candidate`'s twin, and it exists for the same reason plus
    one more: **an act RE-PRICES the rows it names**
    (:func:`~._resolve.resolve_rows`), so the construction the offer set uses
    and the construction a write door uses have to be one.  Two would be two
    answers to what a row is worth and when the app believes it moved, on the
    two sides of a money gate.

    Args:
        txn: The row, with ``entries`` loaded.
        calendar: The pass's
            :class:`~app.services.pay_calendar.PayCalendar`, which the row's
            window is read from.
        amount: Its signed cash effect, already resolved by :func:`_price` --
            taken rather than computed here because the caller has to tell an
            UNPRICEABLE row (reported) from a zero-valued one (not offerable),
            and a constructor returning ``None`` for both could not.

    Returns:
        Its :class:`~._offers.CandidateRow`, or ``None`` when the row is worth
        nothing or its pay period is not one this calendar carries -- neither
        is offerable, and neither is an error.
    """
    if not amount:
        return None
    # The row's PAY PERIOD is the whole of what the app asserts about when this
    # money moves, so both ends travel and the proposer bounds the row by the
    # span rather than by its opening day (finding **N-312**).
    period = calendar.period_by_id(txn.pay_period_id)
    if period is None:
        return None
    return CandidateRow(
        kind=RowKind.TRANSACTION,
        row_id=txn.id,
        label=_label(txn),
        cash_amount=amount,
        settled_on=txn.settled_on,
        is_settled=txn.status.is_settled,
        # **The not-its-own-figure census, stated ONCE and here, because both
        # members are load-bearing and one of them was missed** (plan step
        # ``bank_import:X-f6d-1``).  ``transaction_service`` publishes exactly
        # two predicates for *this figure is not this row's to state* and they
        # are siblings by that module's own docstring: an ENVELOPE derives its
        # figure from the purchases recorded against it, and a CC PAYBACK from
        # the card spend of the row it names.  Correcting either writes a
        # number the next sibling write silently reverts (finding **N-252**),
        # and the transaction door's own backstop (``_correction_for_status``)
        # refuses only the FIRST -- a payback is refused at the PATCH route
        # instead -- so a door reaching it from here would have written a
        # ``corrected`` record onto a figure that is a fact about another row.
        # Measured by the batch suite's own stale-price case, which booked
        # `-60.00` against a payback re-derived to `50.00`.
        #
        # A transfer SHADOW is the third member of that class and is NOT
        # folded in: ``transfer_id`` beside it already states it, and what the
        # owner must do about one is different (change the transfer, not a
        # purchase), which is why the accept door gives it its own sentence.
        # :attr:`~._offers.CandidateRow.figure_is_correctable` is where the two
        # facts are read together.
        #
        # Neither predicate costs a query: ``_transaction_candidates`` eager
        # loads both ``entries`` and ``template``, which are all
        # ``settles_from_entries`` reads, and ``repays_card_spend`` is a plain
        # column.
        states_own_figure=not (
            transaction_service.repays_card_spend(txn)
            or transaction_service.settles_from_entries(txn)
        ),
        transfer_id=txn.transfer_id,
        expected_on=period.start_date,
        expected_through=period.end_date,
        # The same fact its twin carries, from the same column and for the same
        # reason.  A transaction settled through the reconcile panel takes the
        # assertion's day (``reconcile_service._transactions`` for a bill,
        # ``transfer_service._settle`` for a shadow leg), so its window opens at
        # the period rather than closing on that day.
        # WHICH REVISION the screen is about to show (plan step
        # ``bank_import:X-f6d-3``, finding **N-336**).  Read here rather than
        # by the reader that emits it, for the reason every fact beside it is:
        # the OFFER SET and the ACCEPT DOOR both build a candidate through this
        # one constructor, so the state a review is checked against and the
        # state it was taken against come from the same read.
        version_id=txn.version_id,
        settle_day_basis=_day_basis(txn),
    )


def repriced(
    row: CandidateRow, calendar, basis: "cash_ledger.AmountBasis",
) -> "CandidateRow | None":
    """Return *row* as it stands NOW, re-read and re-valued.

    **The scope answers WHICH rows an act may reach; this answers what one of
    them is WORTH, and the two must be asked at different moments.**  Plan step
    ``bank_import:X-f6a-3c-2`` first shared both, on the argument that the only
    way one act can move another's figure is by adding a purchase to it or
    posting one under it -- which makes the two an envelope and its own child,
    and is refused.  **Adversarial financial review measured that argument
    false on 2026-08-19**, with a counterexample and a booked figure:

    ``entry_service.update_entry`` -- which every matched PURCHASE goes through
    -- calls ``entry_credit_workflow.sync_entry_payback``, and that WRITES the
    envelope's CC Payback ``estimated_amount``.  A payback is a transaction on
    the SAME account, so it is a candidate, and it is priced from that column.
    The purchase and the payback are SIBLINGS under one envelope rather than a
    parent and its own child, so no guard here can see the relation.  Measured:
    matching a `$25.00` purchase and then the payback drops the payback from
    `$60.00` to `$50.00`, and the second match is accepted against the stale
    `$60.00` -- the ledger books `$50.00` for a `-$60.00` bank line and the
    account reads **`$10.00` high**.  A fresh derivation refuses it by name.

    **So the figure is re-derived per act and the enumeration is abandoned.**
    Enumerating sibling writes is a guard the next unenumerated writer
    reopens; re-pricing is total.  It is also cheap in the only way that
    matters: the 3.593 s belongs to the 827-row SCAN, which is still derived
    once, and an act names one to four rows.

    Args:
        row: The candidate the scope offered.
        calendar: The pass's
            :class:`~app.services.pay_calendar.PayCalendar`.
        basis: The pass's
            :class:`~app.services.cash_ledger.AmountBasis` (plan step X-au-j).
            **Sharing it does NOT weaken the re-derivation this function
            exists for**, and the counterexample above is why it cannot: an
            :class:`~app.services.cash_ledger.AmountBasis` holds the owner's
            salary and loan DERIVATIONS, never a per-row answer, and the
            sibling write that defect turns on writes a ROW's own column.
            This re-reads the row and its entries from the database either
            way.  Nothing an accept act does -- settling rows, creating
            purchases -- writes a salary profile, a payday or a loan
            parameter, which is the same argument that lets the calendar
            beside it be shared across the pass.

    Returns:
        The row as it stands now, or ``None`` when it has gone, cannot be
        priced, or is no longer worth anything -- each of which means the act
        naming it must be refused rather than applied against a stale figure.
    """
    if row.kind is RowKind.PURCHASE:
        entry = db.session.get(TransactionEntry, row.row_id)
        if entry is None or not entry.amount:
            return None
        return purchase_candidate(entry)
    txn = db.session.get(Transaction, row.row_id)
    if txn is None:
        return None
    amount = _price(txn, basis)
    if amount is None:
        return None
    return transaction_candidate(txn, calendar, amount)


def _transaction_candidates(
    account_id: int, calendar, period_ids: "set[int]",
    basis: "cash_ledger.AmountBasis",
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
    * its pay period is one of the OWNER'S -- ownership, reached the way
      ``Transaction`` is scoped (it carries no ``user_id`` of its own).  **The
      ids come from the CALENDAR rather than from a correlated subquery on
      ``pay_periods.user_id``, and that is what makes the window lookup below
      total**: a row this query returns names a period the calendar was built
      from, so :meth:`~app.services.pay_calendar.PayCalendar.period_by_id`
      cannot answer ``None`` for it.  The two reads are separate snapshots
      under READ COMMITTED, so a concurrent period INSERT between them is
      expressible -- and scoping by the calendar's own ids means the query
      simply does not ask about a period the calendar has not got, rather than
      returning a row nothing here can date;
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
      ``cash_ledger.settled_cash_leg`` makes for its own total guard.

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
            (:attr:`~._offers.CandidateRow.expected_window`).  The DERIVED
            span, never ``pay_periods.end_date``: that column is a stored copy
            of a derivable fact and plan step ``pay_calendar:C4`` drops it.
        period_ids: The saved period ids of that same calendar, resolved ONCE
            by :func:`candidates_for` and threaded rather than re-derived per
            arm.
        basis: The pass's :class:`~app.services.cash_ledger.AmountBasis`,
            threaded for exactly the reason ``period_ids`` above it is (plan
            step X-au-j): one derivation the whole pass shares, resolved once
            and never rebuilt under it.

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
            joinedload(Transaction.template),
        )
        .filter(
            Transaction.account_id == account_id,
            balance_contributing_clause(),
            Transaction.pay_period_id.in_(period_ids),
            db.or_(
                Transaction.transfer_id.is_(None),
                Transaction.transfer_id.in_(
                    db.session.query(Transfer.id).filter(
                        Transfer.user_id == calendar.user_id,
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
        amount = _price(txn, basis)
        if amount is None:
            unpriceable.append(txn.id)
            continue
        candidate = transaction_candidate(txn, calendar, amount)
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(
        key=lambda row: (row.settled_on is None, row.settled_on, row.row_id),
    )
    return candidates, unpriceable


def _purchase_candidates(
    account_id: int, period_ids: "set[int]",
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

    **What is ALREADY MATCHED is NOT a clause here**; see
    :func:`_transaction_candidates` for why it moved to :func:`unmatched_rows`.

    Args:
        account_id: The cash account the statement is for.
        period_ids: The owner's saved pay-period ids -- the SAME scope
            :func:`_transaction_candidates` applies, written once and threaded
            so the two arms cannot drift about whose rows may be offered.

    Returns:
        One :class:`~._offers.CandidateRow` per offerable purchase, ordered as
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
            not_archived_clause(Transaction),
            Transaction.pay_period_id.in_(period_ids),
        )
        .all()
    )
    return sorted(
        (purchase_candidate(entry) for entry in rows if entry.amount),
        key=lambda row: (row.settled_on is None, row.settled_on, row.row_id),
    )


def candidates_for(
    account_id: int, calendar, basis: "cash_ledger.AmountBasis",
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
    adversarial financial review 2026-08-19.

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
        A :class:`~._offers.Candidates`.  Its ``rows`` are the transactions and
        the purchases TOGETHER, each arm's own order preserved and transactions
        first -- a union rather than a pair, because every consumer asks the
        same question of both kinds: a bank line does not know which table its
        counterpart lives in.
    """
    # The owner's SAVED periods, which are both arms' ownership scope and the
    # source of every unsettled transaction's window.  Derived here rather than
    # in each arm for the reason the calendar is threaded: two asks in one
    # request is this project's DRY violation rather than a cost.
    # ``period_id`` is nullable on a DerivedPeriod in general and never ``None``
    # on one ``calendar_for`` built, which reads saved rows only.
    period_ids = {
        period.period_id for period in calendar.periods
        if period.period_id is not None
    }
    transactions, unpriceable = _transaction_candidates(
        account_id, calendar, period_ids, basis,
    )
    return Candidates(
        rows=transactions + _purchase_candidates(account_id, period_ids),
        unpriceable_ids=tuple(unpriceable),
    )


def destinations_for(
    owner_id: int, account_id: int,
) -> "list[PurchaseDestination]":
    """Return every budget line a bank line could become a purchase against.

    **ONE scope, shared by the screen that offers a destination and the door
    that writes into it** (:func:`~._create._existing_envelope`), which is the
    property :func:`~._resolve.resolve_rows` rests on: a row this does not return
    cannot be reached by crafting a request, and a row it does return cannot be
    refused by the write door.  Every clause below is one of those doors'.

    **It lives beside :func:`candidates_for` because it is the same kind of
    answer about the other act** (plan step ``bank_import:X-f6a-3c-2``), and
    because a review pass now derives both together and threads them: it was in
    ``_reads`` while that module was the only caller, and the write doors
    reached across for it.

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
      history, and :func:`_purchase_candidates` already declines to offer one;
    * if it has SETTLED, its recorded figure IS its purchases.  **This is the
      money clause** (:func:`~app.services.entry_service._doors
      ._reject_settled_addition`): on a ``purchases`` basis a new purchase
      raises what the row cost by exactly its own amount and the row's cash leg
      does not move, so the movement is recorded; on a stored-figure basis the
      gross cannot rise, and ``settled_cash_leg`` then subtracts money the gross
      never held -- measured on a production clone, `-163.95` became `+203.67`
      while the anchor true-up moved `$0.00`.

    **Whether it is ITSELF MATCHED is NOT a clause here**, and that is this
    step's change rather than a relaxation: it is :func:`unmatched_destinations`,
    applied by the screen against the claims it read and by
    :func:`~._create._existing_envelope` against the claims that ACT read.  The
    rule is unchanged -- ``accept_match``'s
    :func:`~._accept._reject_parent_and_its_own_purchase` refuses a purchase
    whose parent another match already names, so offering such an envelope
    would render a chooser whose submission always fails.  What changed is
    WHEN it is asked, and it had to: measured on the developer's own statement,
    4 envelopes (2225, 2228, 2389, 2581) are both named by a proposal and
    offered as a destination, so **15 of the 91 creatable lines aim at an
    envelope an earlier item in the same pass claims**.  A snapshot carrying
    the clause baked in would have offered all 15 and refused them a tier
    deeper, with the sentence about counting money twice rather than the one
    about the envelope being gone.

    **Finding N-317 says this clause is wider than the money needs, and the
    developer's ruling of 2026-08-19 is that it STAYS WHOLE**: a money guard is
    not narrowed for a `$0.00` benefit.  The row is OPEN in ``ledger.md`` with
    its diagnosis corrected -- an earlier closure argued the clause protects a
    projected envelope holding no entries, whose leg moves `+111.02` when a
    purchase is added, and adversarial review measured that shape unreachable
    through this clause: a match SETTLES the envelope it names, and a
    zero-entry settle records a STORED FIGURE, which the money clause above
    already refuses.

    Args:
        owner_id: The user whose budget lines may be offered.
        account_id: The cash account the statement is for.

    Returns:
        One :class:`~._creations.PurchaseDestination` per offerable row, oldest
        pay period first and then by name -- a deterministic order, so the
        chooser a screen shows does not depend on what the planner returned.
    """
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
            name=txn.name,
            category_id=txn.category_id,
            period_start=txn.pay_period.start_date,
            period_end=txn.pay_period.end_date,
            pay_period_id=txn.pay_period_id,
            is_settled=txn.status.is_settled,
            # The row's identity ACROSS periods, which is what a merchant
            # destination policy names (plan step X-f6a-3d).
            template_id=txn.template_id,
        )
        for txn in rows
        if txn.tracks_purchases
        and not txn.is_income
        and (
            not txn.status.is_settled
            or txn.settled_basis_id == purchases_basis
        )
    ]
    offered.sort(key=lambda d: (d.pay_period_id, d.label))
    return offered
