"""
Shekel Budget App -- The TRANSACTION arm of the outstanding set

One of the package's arms (see :mod:`app.services.reconcile_service` for what
an arm is and how many there are): the SOURCE ROWS a statement can still
settle -- an envelope's own close, and a bill -- as opposed to the purchase
entries recorded against one, or a transfer's leg.  It owns the three things
an arm owns, its SCOPE, its READ and its WRITE.

**Its scope, bound and loader are :mod:`._rows`', and that is finding N-225.**
The shared half lives once, and the reader and the writer here share it
literally through :data:`ARM`'s loader, which is the security property.  What
stays here is what is genuinely this arm's: WHICH rows (``transfer_id IS
NULL``), what one is WORTH, and what a tick MEANS for it.

**It answers TWO scopes since plan step ``credit_card:CC-5-4b``, one per
account relation** (ruling **R-CC44**).  :data:`ARM` offers the rows ON this
account.  :data:`SETTLEMENT_ARM` offers a row planned on ANOTHER account whose
payment was recorded on this one and then reopened -- a bill planned on
Checking, marked paid from the card, reopened to edit, its payment KEPT on the
card with no date (rulings **R-CC42**, **R-BAL61**) -- under the panel's "Paid
from this account" section (ruling **R-CC111**).  They share this module
because the package cuts by SETTLE VERB and they have one: the same row kind,
ticked through the same :func:`_settle_one`, priced by the same
``settle_amount``, with the same amount box (ruling **R-CC110**) and the same
form fields (ruling **R-CC116**).  What differs is WHICH rows, how a row
READS (its label, :func:`_settlement_label`), and which statement a tick's
link reaches -- the payment and not the row, which ``status_seam.
record_clearing`` works out from the statement's own account.

**Its settle is a service verb, and that is the difference from the purchase
arm.**  A purchase settles by stamping one column and moves no status, so that
arm's writer is a bulk ``UPDATE``.  A transaction settles through the status
seam, an amount rule and a posting reconcile -- so this writer dispatches to
``transaction_service.settle_transaction`` per row, which is the verb the
grid's Mark Paid calls (ruling **R-FA**).  Two doors restating one money rule
is this arc's own root cause 1.

**Nothing here decides what a tick BOOKS, whether the panel may offer a box for
it, or whether a submitted figure is a CORRECTION.**  All three are the verb's,
published as ``transaction_service.settle_amount`` and
``transaction_service.settles_from_entries``, and ANSWERED BY THE ACT for the
third: ``settle_transaction`` returns whether it booked a human's figure, so the
count and the write cannot disagree.  A panel showing a
figure the verb would not book, an input for a value the verb would ignore, or
a telemetry count of corrections the verb never made, are the same defect one
tier up.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, frozen dataclasses out.
  - All monetary arithmetic uses :class:`~decimal.Decimal`.
  - The writer mutates and does NOT commit -- the caller owns the session
    boundary.
"""

from decimal import Decimal

from sqlalchemy import and_
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import cash_ledger, status_seam, transaction_service
from app.services.account_projection import is_revolving
from app.services.cash_ledger import AmountBasis
from app.services.reconcile_service import _rows
from app.services.reconcile_service._offers import (
    OfferKind,
    OutstandingGroup,
    OutstandingTransaction,
)
from app.services.stated_figure import StatedFigure
from app.utils.log_events import (
    EVT_SETTLEMENTS_RECONCILED,
    EVT_TRANSACTIONS_RECONCILED,
)


def _cash_amount(txn: Transaction, booked: Decimal) -> "Decimal | None":
    """Return what the STATEMENT shows for *txn*, or ``None`` when it is *booked*.

    Finding **N-226**, widened by ruling **R-FM**.  An envelope settles at
    ``sum(entries)`` over EVERY entry it holds, and THREE kinds of those never
    leave checking at the tick:

    * a CARD purchase marked by the flag, which leaves later through its own
      CC Payback sibling -- which is exactly why the purchase arm refuses to
      OFFER one;
    * a purchase whose money moved through ANOTHER account than the row's
      (plan step ``credit_card:CC-5-2``, ruling **R-CC15**) -- the card's
      statement shows it and the purchase arm offers it THERE; and
    * a purchase on this account that has ALREADY POSTED, whose cash left on
      its own recorded day and is already a movement of its own in the ledger
      (plan step X-f3b).

    So the figure a tick books and the figure the bank shows for it now are two
    different numbers for one row, and this screen is the one read beside a
    paper statement.

    **It prints both rather than changing what a tick books**, which is the
    only correct direction: ``actual_amount`` legitimately IS total spend, the
    posted ledger already leaves both terms out (a card purchase is a movement
    that moves nothing and a dated purchase posts on its own day,
    ``cash_ledger.movement_cash_leg``), and moving the booked figure would make
    the panel disagree with the grid and the analytics.

    Both terms are the cash ledger's own, taken as ONE published sum
    (``cash_ledger.off_statement_sum``) rather than as two additions restated
    here: one rule, one statement, so a change to what either means cannot
    leave the panel saying the old thing.  Its other caller is the statement
    matcher's corrected figure for a line (``_landing.corrected_figure``),
    which asks the same question of a bank line.

    Args:
        txn: The row being offered, with ``entries`` loaded.
        booked: What a tick would book (``transaction_service.settle_amount``).

    Returns:
        ``booked`` minus the card entries, the purchases whose money moved
        through another account (plan step ``credit_card:CC-5-2``: a card
        swipe recorded in a checking envelope) and the already-posted
        purchases on this account, when the row holds any, else ``None`` --
        which is every bill, every deposit and every envelope whose purchases
        are all debits on its own account and all outstanding.
        Production carries 18 card entries in history and ZERO on a Projected
        envelope, so the card term is latent; the posted term is LIVE, on 2 of
        the 9 posted purchases (`$45.85`) measured 2026-08-14.
    """
    not_on_this_statement = cash_ledger.off_statement_sum(txn)
    if not not_on_this_statement:
        return None
    return booked - not_on_this_statement


def _offer_kind(txn: Transaction) -> OfferKind:
    """Return the section tag this arm puts on *txn*'s offer.

    **The arm TAGS; nothing downstream derives** (see :class:`OfferKind` for
    the two defects deriving it caused, one of them a live mis-captioning of
    production's `$1,958.87` FSA reimbursement).

    Three arms of one rule, in this order:

    * INCOME is a ``DEPOSIT`` -- money arriving, which ruling **R-FD** counts
      apart from payments because a deposit and a bill do not sum to anything a
      reader wants.  Tested FIRST because an income row is never
      purchase-tracked anyway (both entry write doors are expense-only), so the
      order costs nothing and states the priority.
    * A purchase-tracked row is an ``ENVELOPE``, whether or not it currently
      holds anything.  Production's `Kayla's Spending Money` carries zero
      entries and is still an envelope; calling it a bill because it happens to
      be correctable was the renderer's proxy talking.
    * Everything else is a ``BILL``.

    Args:
        txn: The row being offered.

    Returns:
        Its :class:`OfferKind`.
    """
    if txn.is_income:
        return OfferKind.DEPOSIT
    if txn.tracks_purchases:
        return OfferKind.ENVELOPE
    return OfferKind.BILL


def _settle_one(
    txn: Transaction,
    submitted: StatedFigure | None,
    statement: _rows.Statement,
) -> bool:
    """Settle one row through the grid's own verb; say if a human's figure won.

    This arm's settle, named by :data:`ARM` and by :data:`SETTLEMENT_ARM`:
    the "Paid from this account" list ticks a row through the bill's OWN door
    (ruling **R-CC44**), so both scopes share this one function and nothing
    here asks which list the row came from.
    The submitted figure is handed STRAIGHT to the verb: **this function holds
    no money rule at all**, and a first draft's two -- "read it only where the
    panel offered a box" and "only when it differs from what the row would
    otherwise book" -- were both the verb's, restated.  A review measured the
    first: deleting it left every test green, because ``settle_transaction``
    routes an entries-derived row to a branch that ignores ``actual_amount``
    outright.  A guard nothing can observe is not a guard, and two doors
    deciding one column's meaning separately is the shape this whole arc exists
    to remove.

    Args:
        txn: The row to settle, still Projected.
        submitted: The figure the panel's amount box posted, or ``None``.
        statement: The statement being reconciled; its day is what the settle
            records the money as having moved on, rather than the seam's
            default of the user's today -- and on the ``asserted`` basis, because
            what the owner asserted is a BALANCE for that day and the money was
            inside it (plan step **X-az**).  **Its ACCOUNT is the tender**
            (plan step ``credit_card:CC-5-3``, ruling **R-CC15**: a
            statement-driven settle forces the statement's own account): the
            owner ticking a row on this account's panel says this account's
            statement showed the money, so the covering movement books here
            -- named rather than left to the seam's default, because a row
            reverted out of a card-tendered settle keeps that record and the
            default would keep it on the card (ruling **R-CC42**).  On
            :data:`ARM`'s scope the named tender is the row's own account and
            passes the verb's gate by its first member.  On
            :data:`SETTLEMENT_ARM`'s it is the account the row's kept payment
            is already on (that scope's own clause), which the verb reads as
            an ECHO of the recorded tender (``status_seam.tender_for_status``)
            and books where the payment is, asking the gate nothing: the
            money is not moving, only being dated.

    Returns:
        Whether the verb booked *submitted* as a correction -- **answered by the
        verb itself** (developer ruling, 2026-08-17), which is the shape
        ``transfer_service._settle.settle`` already had.  This asked
        ``transaction_service.is_correction`` separately (now private), and it
        re-resolved the row's amount to make its comparison, so one ticked row
        paid for the profile lookup, the loan resolve and the paycheck engine
        twice -- and the count and the write were two answers to one question
        (finding **N-231**), which is the shape they can no longer be.
    """
    corrected = transaction_service.settle_transaction(
        txn, submitted=submitted, settle_day=statement.settle_day,
        tender_account_id=statement.account_id,
    )
    # WHICH statement showed this row (ruling **R-FL**), recorded HERE rather
    # than inside ``settle_transaction`` -- and that placement is the rule.  The
    # verb is shared with the grid's Mark Paid, which settles a row without any
    # statement having shown it, so a link written there would record an
    # observation nobody made.  Ticking a row on this panel IS the observation.
    #
    # It follows the settle, which RELEASES any prior link as it stamps the day
    # (``status_seam``): the release is about the day that moved, and this is
    # the new day's own fact.  Through ``status_seam.record_clearing`` since
    # plan step **X-bi-3a**: the settle mirrored the row's money onto its
    # covering movement, and the link has to reach the fact that carries it.
    # That door links each fact ON THIS STATEMENT'S ACCOUNT (plan step
    # credit_card:CC-5-4b, ruling R-CC44): the row and its payment for a row
    # on this account, the payment alone for a row planned on another.
    status_seam.record_clearing(txn, statement.anchor.anchor_id)
    return corrected


def _load(
    statement: _rows.Statement, transaction_ids: "set[int] | None",
) -> "dict[int, Transaction]":
    """Return this arm's rows, ``{row id: row}``, for :data:`ARM`'s ``load``.

    :func:`~._rows.outstanding_rows` over this scope's clauses and eager load,
    keyed by the id a row's tick posts (its own).

    The row is on THIS account -- the account clause
    :func:`~._rows.outstanding_scope` carried as its own first clause until
    plan step ``credit_card:CC-5-4b``, which made it each scope's; its
    complement is :func:`_settlement_clauses`' first.

    ``transfer_id IS NULL`` -- a transfer settles through
    ``transfer_service.settle_transfer`` so both legs and the parent move
    together (``CLAUDE.md`` transfer invariant 3), and
    ``transaction_service.settle_transaction`` REFUSES a shadow, so admitting
    one here would turn a design boundary into a 400.  A transfer is the
    transfer arm's (:mod:`._transfers`), which offers its LEG off
    ``budget.transfers`` since leaf ``balance:X-bi-6-4c-2``; until then its
    clause was this one's complement over this table.

    ``template`` is loaded here and not in the shared loader because only this
    arm reads ``tracks_purchases``, which lazy-loads a template per row
    otherwise -- an N+1 on a list the user is about to read.

    Args:
        statement: The statement being reconciled.
        transaction_ids: The writer's narrowing, or ``None`` for the reader.

    Returns:
        The rows in landing-day order, keyed by id.
    """
    return {
        txn.id: txn
        for txn in _rows.outstanding_rows(
            statement,
            scope_clauses=(
                Transaction.account_id == statement.account_id,
                Transaction.transfer_id.is_(None),
            ),
            load_options=(selectinload(Transaction.template),),
            transaction_ids=transaction_ids,
        )
    }


#: What this arm IS (:class:`app.services.reconcile_service._rows.Arm`): what it
#: loads, how a row settles, and what it calls the act in the log.
#:
#: PUBLIC within the package: the reader below and
#: :func:`app.services.reconcile_service._assemble.record_reconciliation` both
#: name it, and it being ONE value is what stops them scoping differently.
ARM = _rows.Arm(
    load=_load,
    settle=_settle_one,
    event=EVT_TRANSACTIONS_RECONCILED,
)


def _settlement_clauses(statement: _rows.Statement) -> tuple:
    """Return the "Paid from this account" scope's membership clauses.

    Ruling **R-CC44**: an UN-DATED payment on this account whose row is
    planned on ANOTHER.  :func:`~._rows.outstanding_scope` adds what every
    row scope shares -- Projected, contributing, an offerable period -- and
    :func:`~._rows.outstanding_rows` the landing-day bound, which is the one
    Checking's own list applies to the same row (ruling **R-CC118**, "Once the
    bill is due").  Four clauses of its own, each load-bearing:

    * the row is on ANOTHER account -- the complement of :data:`ARM`'s first
      clause, so the two scopes partition every row and one posted id loads
      in at most one (ruling **R-CC116**).  The row's own account's list keeps
      offering it as a bill, and whichever tick lands first leaves the other
      nothing to settle.
    * ``transfer_id IS NULL`` -- a transfer settles through the transfer
      service and ``settle_transaction`` refuses a shadow.  A shadow's
      covering movements are kept on the shadow's own account
      (``transfer_service._endpoints._apply_endpoint_move`` re-points them
      with the endpoint), so the next clause admits none today; this one
      keeps the scope inside the verb's domain rather than relying on that.
    * it holds an UN-DATED covering movement ON THIS ACCOUNT -- the payment
      the statement may show.  Un-dated, because a dated one already posts
      and folds on its own day, so offering it would count the money twice;
      on this account, because a statement can show only this account's
      money (the tick then books where the payment already is,
      :func:`_settle_one`).
    * it holds NO purchase -- ruling **R-CC113** ("Hide it"): a row holding
      purchases settles FROM them (``transaction_service.
      settles_from_entries``), ignoring the tender and withdrawing the very
      payment this list would show; its purchases are its figure, and the
      purchase arm offers a card swipe on the card.  Stated as the verb's own
      predicate in SQL (:attr:`~app.models.transaction.Transaction.purchases`
      is the entries less the seam's mark, ``status_seam.covering_clause``).

    Args:
        statement: The statement being reconciled.

    Returns:
        The clauses, for :func:`~._rows.outstanding_rows`.
    """
    covering = status_seam.covering_clause()
    return (
        Transaction.account_id != statement.account_id,
        Transaction.transfer_id.is_(None),
        Transaction.entries.any(and_(
            covering,
            TransactionEntry.settled_on.is_(None),
            TransactionEntry.account_id == statement.account_id,
        )),
        ~Transaction.entries.any(~covering),
    )


def _load_settlements(
    statement: _rows.Statement, transaction_ids: "set[int] | None",
) -> "dict[int, Transaction]":
    """Return the "Paid from this account" rows, ``{row id: row}``.

    :data:`SETTLEMENT_ARM`'s ``load``: :func:`~._rows.outstanding_rows` over
    :func:`_settlement_clauses`, keyed by the id a row's tick posts -- its
    own, under the SAME field the row's own list posts it (ruling
    **R-CC116**).  ``template`` is loaded for the reason :func:`_load` loads
    it; the row's ``account``, whose name the label reads, is a joined load
    on the model.

    Args:
        statement: The statement being reconciled.
        transaction_ids: The writer's narrowing, or ``None`` for the reader.

    Returns:
        The rows in landing-day order, keyed by id.
    """
    return {
        txn.id: txn
        for txn in _rows.outstanding_rows(
            statement,
            scope_clauses=_settlement_clauses(statement),
            load_options=(selectinload(Transaction.template),),
            transaction_ids=transaction_ids,
        )
    }


#: The transaction arm's SECOND scope (plan step ``credit_card:CC-5-4b``): the
#: rows planned on another account whose kept payment is on this one.  Its own
#: :class:`~app.services.reconcile_service._rows.Arm` because it loads
#: differently and logs under its own event -- the row it settles is on a
#: SECOND account, which an analyst has to be able to find -- while its settle
#: is :data:`ARM`'s own.  PUBLIC within the package for :data:`ARM`'s reason.
SETTLEMENT_ARM = _rows.Arm(
    load=_load_settlements,
    settle=_settle_one,
    event=EVT_SETTLEMENTS_RECONCILED,
)


def outstanding_transactions(
    statement: _rows.Statement, basis: "AmountBasis",
) -> "dict[int, OutstandingTransaction]":
    """Return this arm's offers, ``{transaction id: offer}``.

    The source rows this account is still holding forward on the day the
    balance was asserted: an envelope whose own close has not been ticked, and
    a bill the projection is still carrying.  Ticking one records that the bank
    moved the money by that day
    (:func:`app.services.reconcile_service._rows.record_settled`).

    **It returns a MAP keyed on the PARENT, which is what lets the assembler
    union it with the purchase arm** -- that arm keys its purchases on the same
    id, so an envelope with outstanding purchases AND an offerable close is ONE
    block carrying both, which is ruling **R-EW**'s shape.

    Reads only (no writes, no commit).

    Args:
        statement: The :class:`~._rows.Statement` being reconciled -- whose
            calendar, which account, which assertion.  **Built ONCE by
            :func:`~._assemble.outstanding_set` and threaded**, rather than
            assembled here from three arguments: it gained the owner's calendar
            at pay-calendar plan step C4-a-2 (every offered row's span is
            derived from it), and three constructions of one value would be
            three chances to date one owner's rows against another's paydays.
            The same reason *basis* below is threaded, one tier down.
        basis: The PANEL's :class:`~app.services.cash_ledger.AmountBasis`,
            built ONCE by :func:`~._assemble.outstanding_set` and threaded
            (plan step X-au-j, finding **N-295**).  Every offer built its own until
            then, so K offered PAYCHECKS ran the paycheck engine K times over
            the owner's whole pay-period set -- which is finding **N-228** one
            tier up, named by ``amount_basis``'s own docstring.

    Returns:
        ``{transaction_id: OutstandingTransaction}``, insertion-ordered by
        landing day then id.  Empty for an account holding nothing overdue --
        **which is NOT the steady state, and the purchase arm's twin of this
        sentence is now wrong about the panel as a whole.**  Replayed over all
        53 Checking assertion days on production, 46 would have carried at
        least one offer, because an envelope's close is offerable for the whole
        of its own period and only closing it clears it.  Finding **N-227**
        owns whether that bound is right.
    """
    return {
        txn_id: _offer(statement, txn, basis, kind=_offer_kind(txn))
        for txn_id, txn in ARM.load(statement, None).items()
    }


def outstanding_settlements(
    statement: _rows.Statement, basis: "AmountBasis",
) -> "list[OutstandingGroup]":
    """Return the "Paid from this account" offers, one childless block per row.

    The second scope's reader (plan step ``credit_card:CC-5-4b``, ruling
    **R-CC44**): each row planned on another account whose payment was
    recorded here and reopened, headed by its own label and listed in its
    own paycheck block -- :func:`_settlement_label`, over
    :func:`~._rows.filed_period` -- and priced, boxed and settled exactly as
    the row's own list would price, box and settle it (:func:`_offer`).

    **It returns finished BLOCKS, as the transfer arm does**, because a row
    here is always childless (the scope admits no row holding a purchase,
    ruling **R-CC113**) and headed by its own label -- so it has nothing to
    union with the purchase arm and no parent to look up.  Its key is still
    the row's id, and the row-keyed map could have held it (its scope is
    disjoint from :data:`ARM`'s by the row's account); the label is what
    that map's heading query cannot compose.

    **Whether this account is a CARD is asked once, of the account's type**
    (``account_projection.is_revolving``, the one card predicate, which reads
    the type's ``has_revolving_credit`` flag -- never a name), and only when
    there is a row to label: a card's rows read "paid from this card", every
    other account's "paid from this account" (rulings **R-CC111** /
    **R-CC117**).

    Reads only (no writes, no commit).

    Args:
        statement: The :class:`~._rows.Statement` being reconciled, built once
            by :func:`~._assemble.outstanding_set` and threaded.
        basis: The PANEL's :class:`~app.services.cash_ledger.AmountBasis`,
            built once by :func:`~._assemble.outstanding_set` and threaded,
            for :func:`outstanding_transactions`' reason.

    Returns:
        One :class:`~._offers.OutstandingGroup` per row, keyed by the row's
        id, in landing-day order; empty for an account holding none.
    """
    rows = SETTLEMENT_ARM.load(statement, None)
    if not rows:
        return []
    on_card = is_revolving(db.session.get(Account, statement.account_id))
    return [
        OutstandingGroup(
            key=txn.id,
            name=_settlement_label(txn, on_card=on_card),
            period=_rows.filed_period(statement, txn),
            purchases=(),
            settle=_offer(statement, txn, basis, kind=OfferKind.SETTLEMENT),
            # Resolved by the assembler once the order is known.
            section=None,
        )
        for txn in rows.values()
    ]


def _settlement_label(txn: Transaction, *, on_card: bool) -> str:
    """Return a "Paid from this account" row's heading.

    The developer's wording, verbatim in shape: ruling **R-CC111** ("Groceries
    (Checking's plan, paid from this card)", and "(Checking's plan, received
    into this account)" for a deposit), and ruling **R-CC117** for an account
    that is not a card ("paid from this account", since "this card" would be
    false).  An envelope's tick is PREFIXED "Close " by the template, off
    :attr:`~._offers.OutstandingGroup.settle_closes_an_envelope` (ruling
    **R-CC119**), so this composes the row and never the act.

    The planned account's name is its CURRENT one, read through the row's
    joined ``account``, as a transfer leg's label reads its endpoints'.

    Args:
        txn: A row in the second scope, with ``account`` loaded.
        on_card: Whether the statement's account is a card.

    Returns:
        The label.
    """
    if txn.is_income:
        moved = "received into this account"
    elif on_card:
        moved = "paid from this card"
    else:
        moved = "paid from this account"
    return f"{txn.name} ({txn.account.name}'s plan, {moved})"


def _offer(
    statement: _rows.Statement, txn: Transaction, basis: AmountBasis,
    *, kind: OfferKind,
) -> OutstandingTransaction:
    """Return the offer this arm makes for one row, in either scope.

    Args:
        statement: The statement being reconciled; its calendar is what dates
            the offer (:func:`~._rows.attributed_on`).
        txn: A row in scope, with ``entries``, ``pay_period`` and ``template``
            loaded.
        basis: The panel's :class:`~app.services.cash_ledger.AmountBasis`,
            threaded from :func:`outstanding_transactions` (plan step X-au-j).
        kind: The SECTION the scope puts it in -- :func:`_offer_kind` on
            :data:`ARM`'s scope, ``SETTLEMENT`` on :data:`SETTLEMENT_ARM`'s
            (plan step ``credit_card:CC-5-4b``).  Everything else is the row's
            and is read the same way in both, so a row is worth, boxed and
            settled the same whichever list offers it.

    Returns:
        Its :class:`OutstandingTransaction`.  ``amount`` is resolved once and
        passed to :func:`_cash_amount` rather than resolved twice: the two
        figures are the same number seen two ways, and asking the verb again
        would be a second answer to one money question.  ``closes_envelope``
        is :func:`_offer_kind`'s own answer, so on :data:`ARM`'s scope it
        and ``kind`` are one classification read once each.
    """
    booked = transaction_service.settle_amount(txn, basis)
    return OutstandingTransaction(
        key=txn.id,
        attributed_on=_rows.attributed_on(statement, txn),
        amount=booked,
        cash_amount=_cash_amount(txn, booked),
        is_correctable=not transaction_service.settles_from_entries(txn),
        is_income=txn.is_income,
        kind=kind,
        closes_envelope=_offer_kind(txn) is OfferKind.ENVELOPE,
    )
