"""
Shekel Budget App -- The TRANSACTION arm of the outstanding set

One of the package's three arms (see :mod:`app.services.reconcile_service` for
what an arm is and why there are three): the SOURCE ROWS a statement can still
settle -- an envelope's own close, and a bill -- as opposed to the purchase
entries recorded against one.  It owns the three things an arm owns, its SCOPE,
its READ and its WRITE, and they live in one module because the scope being
literally shared between the read and the write is the security property.

**Its settle is a service verb, and that is the difference from the purchase
arm.**  A purchase settles by stamping one column and moves no status, so that
arm's writer is a bulk ``UPDATE``.  A transaction settles through the status
seam, an amount rule and a posting reconcile -- so this writer dispatches to
``transaction_service.settle_transaction`` per row, which is the verb the
grid's Mark Paid calls (ruling **R-FA**).  Two doors restating one money rule
is this arc's own root cause 1.

**Nothing here decides what a tick BOOKS or whether the panel may offer a box
for it.**  Both are the verb's, published as
``transaction_service.settle_amount`` and
``transaction_service.settles_from_entries``, and read from here.  A panel
showing a figure the verb would not book, or an input for a value the verb
would ignore, is the same defect one tier up.

**The date bound is in TWO halves and only one of them is SQL.**  The offer set
is the OVERDUE set: ruling **R-G** clamps a projected row's landing day up to
``as_of + 1`` (``balance_at/_cash_fold.py``), so a row whose attribution day has
already passed is precisely one the projection is still holding forward.  That
day is :func:`~app.utils.dates.attribution_date` -- a clamp, not a column -- so
the bound is applied in Python over an SQL SUPERSET (``period.start_date <=
observed_on``), valid because the clamp guarantees ``attribution_date >=
period.start_date``.  Restating the clamp in SQL would be a second
implementation of the rule the calendar and the balance line already share.
Measured on production 2026-08-11, Checking at its latest assertion: the SQL
superset admits 5 rows and the Python bound narrows them to 3.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, frozen dataclasses out.
  - All monetary arithmetic uses :class:`~decimal.Decimal`.
  - The writer mutates and does NOT commit -- the caller owns the session
    boundary.
"""

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import transaction_service
from app.services.reconcile_service._offers import (
    OfferKind,
    OutstandingTransaction,
)
from app.utils.balance_predicates import (
    balance_contributing_clause,
    is_projected_clause,
)
from app.utils.dates import attribution_date
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSACTIONS_RECONCILED,
    log_event,
)

logger = logging.getLogger(__name__)


def _outstanding_scope(owner_id: int, account_id: int, observed_on: date):
    """Return the SQL half of "this row is still waiting on the bank".

    A SUPERSET, by construction: the day bound below is on the pay period's
    start rather than on the row's own landing day, and
    :func:`_lands_on_or_before` narrows what comes back.  The two halves are
    always applied together by :func:`_outstanding_rows`, which is the only
    caller, so there is no shape in which a reader or a writer gets one of
    them.

    **The offer set is a SUBSET of the account's PLAN**, and the clauses say so
    by sharing the plan loader's own builders rather than re-deriving them:
    ``is_projected_clause`` composed with ``balance_contributing_clause`` is
    exactly what
    :func:`app.services.cash_ledger._facts.planned_cash_rows` narrows with.
    The status pair inside the second is redundant by construction (Projected
    is neither Credit nor Cancelled) and composed anyway, for the reason that
    loader gives: one shared statement of "which rows exist at all" beats two
    hand-written filters that agree today.

    Five clauses, each load-bearing:

    * the row is on THIS account -- a balance assertion declares the real
      balance of one account, and a user may hold more than one checking
      account.  Settling across accounts would book money against a statement
      that never showed it.
    * ``transfer_id IS NULL`` -- a transfer shadow settles through
      ``transfer_service.update_transfer`` so both legs and the parent move
      together (``CLAUDE.md`` transfer invariant 3).  It is plan step X-f2-c3's
      arm, and ``transaction_service.settle_transaction`` REFUSES one, so
      admitting it here would turn a design boundary into a 400.
    * PROJECTED -- a settled row has already been recorded, and a Credit or
      Cancelled row is not money this account owes.
    * contributing and not soft-deleted -- the shared gate above.
    * the parent period is this OWNER's and starts on or before *observed_on*
      -- ownership, and the SQL superset of the landing-day bound.

    Not scoped by ``scenario_id``, for the same reason
    :func:`app.services.reconcile_service._purchases._outstanding_scope` is
    not: Phase 1 is baseline-only, so ``account_id`` fully isolates the set
    today, and when what-if scenarios land the callers must thread an
    operating-scenario context into BOTH arms.  One deferral, stated once per
    arm and identically, rather than two arms of one package scoping
    differently.

    Args:
        owner_id: The user_id whose rows to scope to.
        account_id: The cash account the balance was asserted for.
        observed_on: The civil day that balance was true for.

    Returns:
        A list of SQLAlchemy filter clauses to apply to a
        :class:`~app.models.transaction.Transaction` query.
    """
    return [
        Transaction.account_id == account_id,
        Transaction.transfer_id.is_(None),
        is_projected_clause(Transaction),
        balance_contributing_clause(),
        Transaction.pay_period_id.in_(
            db.session.query(PayPeriod.id).filter(
                PayPeriod.user_id == owner_id,
                PayPeriod.start_date <= observed_on,
            )
        ),
    ]


def _lands_on_or_before(txn: Transaction, observed_on: date) -> bool:
    """Return whether the projection lands *txn* on or before *observed_on*.

    The Python half of the bound, and the reason it is not SQL: the landing day
    is :func:`~app.utils.dates.attribution_date`, the clamp the calendar's day
    cells and the balance line's daily ramp already share, so writing it as a
    ``LEAST(GREATEST(...))`` here would be a second implementation of one rule.

    Args:
        txn: A row from the SQL superset, with ``pay_period`` loaded.
        observed_on: The civil day the balance was asserted for.

    Returns:
        True when the row is OVERDUE against that day.
    """
    return _attributed_on(txn) <= observed_on


def _attributed_on(txn: Transaction) -> date:
    """Return the day the projection lands *txn* on.

    Stated once because two things read it: the bound
    (:func:`_lands_on_or_before`) and the caption the panel prints
    (:attr:`~app.services.reconcile_service.OutstandingTransaction.attributed_on`).
    A row offered under a caption that disagrees with why it was offered is the
    "a figure and its caption never disagree" rule broken on the one screen a
    user reads against a paper statement.

    Args:
        txn: The row, with ``pay_period`` loaded.

    Returns:
        Its clamped attribution date.
    """
    period = txn.pay_period
    return attribution_date(
        txn.due_date, period.start_date, period.end_date,
    )


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


def _outstanding_rows(
    owner_id: int,
    account_id: int,
    observed_on: date,
    *,
    transaction_ids: "set[int] | None" = None,
) -> "list[Transaction]":
    """Return the rows this arm offers, both halves of the scope applied.

    **The ONE place this arm's scope is expressed**, so its reader and its
    writer cannot come to disagree about what "outstanding" means -- the
    property the purchase arm gets by sharing a clause list, expressed as a
    shared loader here because this arm's writer needs the ROWS (its settle is
    a per-row service verb, not a bulk ``UPDATE``).

    Both eager loads are load-bearing and are the plan loader's own: ``entries``
    decides the settle branch and its amount, ``pay_period`` feeds the
    attribution clamp.  ``template`` is loaded here and not there because this
    arm reads ``tracks_purchases``, which lazy-loads a template per row
    otherwise -- an N+1 on a list the user is about to read.

    Args:
        owner_id: The user_id whose rows to scope to.
        account_id: The cash account whose balance was asserted.
        observed_on: The civil day that balance was true for.
        transaction_ids: The writer's narrowing -- the ids a form submitted.
            ``None`` (the reader) means "everything in scope".  An id outside
            the scope simply does not come back, which is the set-operation
            form of the project's "404 for both not-found and not-yours" rule.

    Returns:
        The matching rows, ordered by their landing day and then by id so the
        panel's blocks and the writer's loop are both deterministic.
    """
    query = (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.pay_period),
            selectinload(Transaction.entries),
            selectinload(Transaction.template),
        )
        .filter(*_outstanding_scope(owner_id, account_id, observed_on))
    )
    if transaction_ids is not None:
        query = query.filter(Transaction.id.in_(transaction_ids))
    rows = [
        txn for txn in query.all()
        if _lands_on_or_before(txn, observed_on)
    ]
    rows.sort(key=lambda txn: (_attributed_on(txn), txn.id))
    return rows


def outstanding_transactions(
    owner_id: int, account_id: int, observed_on: date,
) -> "dict[int, OutstandingTransaction]":
    """Return this arm's offers, ``{transaction id: offer}``.

    The source rows this account is still holding forward on the day the
    balance was asserted: an envelope whose own close has not been ticked, and
    a bill the projection is still carrying.  Ticking one records that the bank
    moved the money by that day (:func:`record_settled_transactions`).

    **It returns a MAP keyed on the PARENT, which is what lets the assembler
    union it with the purchase arm** -- that arm keys its purchases on the same
    id, so an envelope with outstanding purchases AND an offerable close is ONE
    block carrying both, which is ruling **R-EW**'s shape.

    Reads only (no writes, no commit).

    Args:
        owner_id: The user_id whose rows to list.
        account_id: The cash account whose balance was asserted.
        observed_on: The civil day that balance was true for.

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
        txn.id: OutstandingTransaction(
            transaction_id=txn.id,
            attributed_on=_attributed_on(txn),
            amount=transaction_service.settle_amount(txn),
            is_correctable=not transaction_service.settles_from_entries(txn),
            is_income=txn.is_income,
            kind=_offer_kind(txn),
        )
        for txn in _outstanding_rows(owner_id, account_id, observed_on)
    }


def record_settled_transactions(
    owner_id: int,
    account_id: int,
    transaction_ids: "set[int]",
    corrections: "dict[int, Decimal]",
    observed_on: date,
) -> int:
    """Settle *transaction_ids* as having moved by *observed_on*.

    This arm's writer: the user ticked these rows off a statement, so each
    settles through ``transaction_service.settle_transaction`` -- the grid's
    own verb -- stamping the statement's civil day rather than the seam's
    default of the user's today.

    **Every id is re-derived through the arm's scope rather than trusted.**
    An id belonging to another user, another account, a settled row, a
    soft-deleted row, a transfer shadow or a row that is not yet overdue simply
    does not come back from :func:`_outstanding_rows` and is silently skipped.
    The count returned is what actually settled, never what was asked for.

    **A correction is applied only where the panel offered a box for one**
    (rulings **R-FB** / **R-FF**), and only when it DIFFERS from what the row
    would otherwise book.  Both halves matter.  Reading a submitted amount for
    a row whose settle derives its own figure would silently discard the user's
    input; writing an equal figure into ``actual_amount`` would populate a
    column that is NULL on every uncorrected row, destroying the only signal
    that says a human typed one.

    **The loop reconciles the posted ledger once PER ROW, and that is finding
    N-221 ANSWERED rather than accepted by default.**  The verb ends in
    ``posting_service.sync_transaction_postings``, which per call resolves two
    ledger accounts, reads the period's posted set and runs the anchor self-heal
    -- ruling **R-DL**'s shape one tier up.  A batch sibling is NOT built here,
    for two measured reasons.  It already exists as somebody's job: plan step
    **X-ai-a**'s stated mandate is a BATCHED cash reconcile, measured at 8 SQL
    statements against 696 assembled, and a second batch implementation written
    here is the duplication that step exists to remove.  And the cost is bounded
    by the data rather than by hope: replayed over all 53 Checking assertion
    days on production, the WORST day offers 9 transaction rows and the mean is
    **4.02** over the 46 days that carry any (a first draft said 4.2, which was
    the same replay with transfer shadows left in -- they are X-f2-c3's arm and
    not this one's), and ``carry_forward_service`` already loops the same reconcile per
    envelope on a path with no such bound.  N-221 is therefore re-pointed to
    X-ai-a rather than closed.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        owner_id: The user_id whose rows these must be.
        account_id: The cash account the balance was asserted for.
        transaction_ids: The ids the user ticked.  An empty set is a no-op that
            issues no query.
        corrections: ``{transaction id: amount}`` for the rows whose amount box
            was submitted.  An id with no entry, and an id whose row is not
            correctable, settle at the row's own figure.
        observed_on: The civil day the asserted balance was true for, and the
            day each settled row records its money as having moved.

    Returns:
        The number of rows actually settled.

    Raises:
        ValidationError: Propagated from the settle verb -- an illegal
            transition a stale panel can still submit.  A 400 at the route.
        PostingError: Propagated from the verb's ledger reconcile.  Fails loud.
    """
    if not transaction_ids:
        return 0

    rows = _outstanding_rows(
        owner_id, account_id, observed_on, transaction_ids=transaction_ids,
    )
    corrected = 0
    for txn in rows:
        # The submitted figure is handed straight to the verb.  **This loop
        # holds no money rule at all**, and a first draft's two -- "read it only
        # where the panel offered a box" and "only when it differs from what the
        # row would otherwise book" -- were both the verb's, restated.  A review
        # measured the first: deleting it left every test green, because
        # ``settle_transaction`` routes an entries-derived row to a branch that
        # ignores ``actual_amount`` outright.  A guard nothing can observe is
        # not a guard, and two doors deciding one column's meaning separately is
        # the shape this whole arc exists to remove.
        before = txn.actual_amount
        transaction_service.settle_transaction(
            txn, actual_amount=corrections.get(txn.id),
            settled_on=observed_on,
        )
        # Counted from what CHANGED, not from what was submitted.  An HTML form
        # posts every rendered input, so ``corrections`` holds the prefilled box
        # of every correctable row on the panel whether it was ticked or not --
        # counting its keys measures how many boxes the panel drew.
        if txn.actual_amount != before:
            corrected += 1

    if rows:
        log_event(
            logger, logging.INFO,
            EVT_TRANSACTIONS_RECONCILED, BUSINESS,
            "Outstanding transactions settled against a bank statement",
            user_id=owner_id,
            account_id=account_id,
            observed_on=observed_on.isoformat(),
            settled_count=len(rows),
            requested_count=len(transaction_ids),
            corrected_count=corrected,
        )

    return len(rows)
