"""
Shekel Budget App -- The PURCHASE arm of the outstanding set

One of the package's three arms (see :mod:`app.services.reconcile_service` for
what an arm is and why there are three): the entries recorded against an
envelope that the bank has not been seen to have taken.  It owns the three
things an arm owns -- its SCOPE, its READ and its WRITE -- and they live in one
module because the scope being literally shared between the read and the write
is the security property, not a convenience.

**Its settle is the odd one of the three, and that is why it is first.**
Settling a purchase writes THREE columns on ONE row (``settled_on``, the basis
that says the day is an upper BOUND, and the statement it names) and moves no
status, so this arm's writer is a single bulk ``UPDATE`` narrowed by the same
clauses the reader selected on.  The transaction
and transfer arms settle through a status seam, so their writers dispatch to a
service verb per row (ruling **R-FA**).  Nothing here should be generalised
into a shape those two can share until they exist.

**All three now reconcile the LEDGER, and that is plan step X-f3b** (ruling
**R-FM**): a purchase whose posting day is recorded is a cash movement of its
own, so stamping one posts its cash leg.  The bulk ``UPDATE`` stays -- the
column write is still one statement -- and the postings are reconciled per
FAMILY afterwards, which keeps the security property (the SCOPE is what decides
which rows were stamped) and adds no second definition of "outstanding".

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, frozen dataclasses out.
  - All monetary arithmetic uses :class:`~decimal.Decimal`.
  - The writer mutates and does NOT commit -- the caller owns the session
    boundary.
"""

import logging
from datetime import date

from app import ref_cache
from app.enums import SettledDayBasisEnum
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import posting_service
from app.services.cash_ledger import AnchorPoint
from app.services.reconcile_service._offers import OutstandingPurchase
from app.utils.balance_predicates import balance_contributing_clause
from app.utils.log_events import (
    BUSINESS,
    EVT_ENTRIES_SETTLED_DAY_RECORDED,
    log_event,
)

logger = logging.getLogger(__name__)


def _outstanding_scope(owner_id: int, account_id: int, observed_on: date):
    """Return the filter clauses for "not yet seen on a statement".

    The ONE definition of the outstanding PURCHASE set, shared by this arm's
    reader (:func:`outstanding_purchases`) and its writer
    (:func:`record_settled_days`) so a purchase the panel does not OFFER can
    never be stamped by a forged id -- and so the two cannot drift about what
    "outstanding" means, which is the shape this whole step exists to end.

    **"Purchase" is load-bearing in that sentence.**  Plan step X-f2-c2 adds a
    TRANSACTION twin with its own bound (``attribution_date <= observed_on``,
    applied in Python over an SQL superset), so this becomes one of two scopes
    and the sharing property has to hold per scope rather than over the set as
    a whole.  Writing "the outstanding set" here would make that leaf falsify a
    security argument instead of extending it -- and the module split is what
    makes the per-arm reading the obvious one: this function is not visible
    outside its own arm.

    Five clauses, each load-bearing:

    * ``settled_on IS NULL`` -- the definition itself.  A purchase whose
      posting day is already recorded is not outstanding, whatever that day is.
    * ``is_credit IS FALSE`` -- a credit-card purchase never touches checking;
      it leaves through its own CC Payback sibling, so it is not on this
      account's statement and reconciling it would mean nothing.
    * ``purchased_on <= observed_on`` -- a purchase made AFTER the day the
      balance was read cannot be inside it.  Offering one would let the user
      record a posting day earlier than the purchase, which
      ``ck_transaction_entries_settled_not_before_purchase`` refuses at the
      database; filtering here means that constraint is a backstop rather than
      a reachable 500.
    * the parent is this OWNER's and on THIS account -- a balance assertion
      declares the real balance of one account, and a user may hold more than
      one checking account (there is no per-type uniqueness).  Reconciling
      across accounts would drop another account's reservation without ever
      raising its anchor.
    * the parent is CONTRIBUTING -- a Credit or Cancelled parent's purchases
      are not money this account owes, and a soft-deleted one's are not money
      at all (``settled_cash_leg`` and ``_events.settled_cash_facts`` both zero
      the whole family for such a row).  Routed through the shared
      ``balance_contributing_clause`` so this filter and the plan loader cannot
      come to disagree about which parent rows exist at all -- the soft-delete
      half was a hand-written ``is_deleted.is_(False)`` until plan step X-f2-c2.

    **The parent is NOT required to be PROJECTED, and dropping that clause is
    the developer's 2026-08-17 ruling** (plan step X-au-c3).  It was here on the
    premise that "the entry reservation prices only projected rows
    (``cash_ledger._amounts._entry_aware_amount``), so an entry on a settled
    parent is inert" -- and ruling **R-FM** had already falsified it one step
    earlier: ``settled_cash_leg`` subtracts every POSTED purchase from a settled
    row's close, so recording a posting day on such a purchase moves its cash
    out of the close's day and onto the bank's.  Nothing is created or destroyed
    by that -- the two terms always sum to the row's whole debit total -- but
    the DAY is what a statement reconciles against, which is this panel's entire
    subject.  Measured on the 2026-08-17 production dump: 28 closed envelopes
    hold 61 debit purchases with no posting day, ``$4,360.07``, none of which
    this panel would ever have offered.  ``entry_service._doors``'s field-aware
    refusal is the write side of the same ruling.

    Not scoped by scenario_id: transactions are scenario-scoped, but Phase 1 is
    baseline-only (every transaction lives in the single baseline scenario), so
    account_id fully isolates the set today.  When what-if scenarios land
    (Phase 3), the callers must thread an operating-scenario context in here
    too -- the same deferral ``clear_entries_for_anchor_true_up`` carried.

    Args:
        owner_id: The user_id whose purchases to scope to.
        account_id: The cash account the balance was asserted for.
        observed_on: The civil day that balance was true for.

    Returns:
        A list of SQLAlchemy filter clauses to apply to a
        :class:`~app.models.transaction_entry.TransactionEntry` query.
    """
    return [
        TransactionEntry.settled_on.is_(None),
        TransactionEntry.is_credit.is_(False),
        TransactionEntry.purchased_on <= observed_on,
        TransactionEntry.transaction_id.in_(
            db.session.query(Transaction.id)
            .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
            .filter(
                PayPeriod.user_id == owner_id,
                Transaction.account_id == account_id,
                balance_contributing_clause(),
            )
        ),
    ]


def outstanding_purchases(
    owner_id: int, account_id: int, observed_on: date,
) -> "dict[int, list[OutstandingPurchase]]":
    """Return this arm's offers, ``{parent transaction id: purchases}``.

    Debit purchases made on or before *observed_on* whose posting day has never
    been recorded, so the projection is still holding their whole envelope
    budget back.  Ticking one is what tells the app the bank has taken the
    money (:func:`record_settled_days`).

    **This is the question a stored ``is_cleared`` flag used to answer by
    guessing.**  The flag was written by a bulk UPDATE at every true-up over
    "every entry dated on or before the SERVER's today", so a purchase recorded
    after the true-up was never reconciled and one recorded before always was,
    whether or not the bank had taken either.  The list this returns is the
    same question asked of the user, who is holding the statement.

    **It returns a MAP rather than the assembled blocks, and the seam is where
    the arms meet.**  Labelling a parent, ordering the blocks and totalling the
    set are the same work whatever produced the offers, so they belong to
    :mod:`app.services.reconcile_service._assemble`; deciding what this account
    still owes in PURCHASES is this arm's alone.  Plan steps X-f2-c2 and
    X-f2-c3 add a sibling arm each, returning their own offers against the same
    parent key, and the assembler unions them -- which is the whole reason the
    key here is the PARENT's id and not the entry's.

    Reads only (no writes, no commit).

    Args:
        owner_id: The user_id whose purchases to list.
        account_id: The cash account whose balance was asserted.
        observed_on: The civil day that balance was true for -- purchases made
            after it cannot be inside it and are not listed.

    Returns:
        ``{transaction_id: [OutstandingPurchase, ...]}``, INSERTION-ORDERED by
        each parent's oldest outstanding purchase and with each parent's list
        oldest first, the entry id breaking a same-day tie deterministically.
        Both orderings are the caller's contract and both come from the single
        ``ORDER BY`` below rather than from a second sort.  Empty for an
        account with nothing outstanding, which is THIS ARM's steady state for
        a user who reconciles at every true-up -- and is no longer the PANEL's,
        because plan step X-f2-c2 added an arm that offers source rows; see
        :func:`app.services.reconcile_service._transactions.outstanding_transactions`.
        No list is ever empty: a parent appears only because a purchase put it
        there.
    """
    rows = (
        db.session.query(TransactionEntry)
        .filter(*_outstanding_scope(owner_id, account_id, observed_on))
        .order_by(TransactionEntry.purchased_on, TransactionEntry.id)
        .all()
    )

    # One pass, insertion-ordered: ``rows`` is already sorted oldest purchase
    # first, so the FIRST time a parent is seen is its oldest outstanding
    # purchase -- which is the block order the docstring promises, obtained
    # from the sort that is already there rather than from a second one.
    blocks: "dict[int, list[OutstandingPurchase]]" = {}
    for row in rows:
        blocks.setdefault(row.transaction_id, []).append(
            OutstandingPurchase(
                entry_id=row.id,
                purchased_on=row.purchased_on,
                description=row.description,
                amount=row.amount,
            )
        )
    return blocks


def _post_stamped_purchases(
    account_id: int, entry_ids: "set[int]", anchor_id: int,
) -> None:
    """Post the cash leg of every purchase the tick just stamped -- ruling **R-FM**.

    Plan step X-f3b's half of this writer.  A purchase carrying a recorded bank
    posting day is a cash movement of its own, so stamping one is a LEDGER act
    as well as a column write, exactly as the transaction arm's settle already
    is.  This arm's writer is a bulk ``UPDATE`` and stays one; the postings are
    reconciled per FAMILY afterwards, which is what
    ``posting_service.sync_transaction_postings`` is for -- see the comment at
    that call for why it is the family and not the purchase.

    **The set is re-read rather than assumed**, and both halves of that matter.
    The ``UPDATE`` ran with ``synchronize_session=False``, so any
    :class:`~app.models.transaction_entry.TransactionEntry` already in the
    identity map holds the pre-update values -- ``populate_existing()`` refreshes
    exactly the rows this touched, so the posting reconcile (and the TRANSACTION
    arm that runs after it, which reads the same purchases through their
    parent's ``entries``) both see the day that was just written.  And the
    filter re-derives WHICH rows landed from the database rather than from
    *entry_ids*: an id the scope rejected was never stamped and must not be
    posted.

    ``reconciled_by_id == anchor_id`` identifies them exactly.  An entry inside
    :func:`_outstanding_scope` carries no ``settled_on``, and
    ``ck_transaction_entries_cleared_needs_settle_day`` makes a link without one
    unwritable -- so no entry this call could have stamped already named this
    statement, and every one that names it now was stamped here.

    **The account clause is redundant and is written anyway.**  A purchase
    naming this assertion is already on this account by construction --
    ``fk_transaction_entries_reconciled_by`` is a COMPOSITE key over
    ``(account_id, reconciled_by_id)``, so a cross-account link is unwritable --
    but every other read in this arm re-scopes on principle rather than on an
    argument, and a scope that rests on an argument is one refactor away from
    resting on nothing.

    Args:
        account_id: The cash account the balance was asserted for.
        entry_ids: The ids the user ticked (the superset the ``UPDATE`` was
            scoped against).
        anchor_id: The governing assertion's id, just written onto the stamped
            rows.
    """
    stamped = (
        db.session.query(TransactionEntry)
        .populate_existing()
        .filter(
            TransactionEntry.id.in_(entry_ids),
            TransactionEntry.account_id == account_id,
            TransactionEntry.reconciled_by_id == anchor_id,
        )
        .all()
    )
    # **The FAMILY is reconciled, not the purchase**, and that is a defect fixed
    # rather than a tidier spelling (plan step X-au-c3, second pass).  This
    # loop called ``posting_service.sync_purchase_postings`` per entry, whose
    # own docstring states the precondition it was written under: it is "for
    # the write paths that change a purchase WITHOUT touching its parent's own
    # cash leg".
    #
    # Widening :func:`_outstanding_scope` to admit a SETTLED parent broke that
    # precondition, because a settled row's confirmed effect is
    # ``settled figure - Sigma(credit) - Sigma(POSTED purchases)``
    # (``cash_ledger.settled_cash_leg``): recording the day SHRINKS the
    # parent's own leg by exactly what the purchase's new leg books.  Posting
    # the purchase alone left the parent's full leg standing beside it and the
    # money was counted TWICE -- measured on a ``$30.00`` purchase under a
    # settled envelope on a ``$1,000.00`` anchor, ledger ``970 -> 940`` while
    # its own sources still said ``970``, breaking the Build-Order Step-5
    # per-account invariant through an ordinary ``POST /accounts/<id>/reconcile``.
    #
    # ``sync_transaction_postings`` is the door for a caller that changed the
    # parent, and it reconciles the parent's leg AND one leg per posted
    # purchase in a single idempotent pass -- so it replaces the per-entry call
    # outright rather than being added beside it.  It is correct for a
    # PROJECTED parent too: ``settled=False`` leaves the parent booking nothing
    # and still posts each purchase's own leg, which is ruling **R-FM**.
    #
    # Grouped so a statement that ticks four purchases of one envelope
    # reconciles that family ONCE; ``dict`` preserves insertion order, so the
    # pass stays deterministic.
    families = {entry.transaction_id: entry.transaction for entry in stamped}
    for txn in families.values():
        posting_service.sync_transaction_postings(
            txn, settled=txn.status.is_settled,
        )


def record_settled_days(
    owner_id: int,
    account_id: int,
    entry_ids: "set[int]",
    anchor: AnchorPoint,
) -> int:
    """Record that *anchor*'s statement showed *entry_ids*.

    This arm's writer: the user ticked these purchases off a statement, so each
    one records WHICH statement showed it (``reconciled_by_id``, ruling
    **R-FL**) and takes that statement's day as its ``settled_on``.

    **Three columns, three facts, and the second is why this step exists.**
    ``settled_on`` is an UPPER BOUND on the true posting day -- the purchase may
    have cleared a day or two earlier, and a user who wants the exact day edits
    the entry.  ``reconciled_by_id`` is not a bound at all: it is the
    observation, and it is what the clearing rule reads first.  The day alone
    could not carry it -- production holds three days on which Checking has more
    than one assertion, so no rule over ``settled_on`` can name which statement
    a tick was made against.

    **``settled_day_basis_id`` is the third, and it is plan step X-az** (finding
    **N-332**).  It records that this day is a BOUND, in the row itself, where
    the sentence above used to be the only statement of it and every reader had
    to re-derive the fact from ``reconciled_by_id`` being populated.  That
    inference was exact over the three writers of ``settled_on`` and blind to
    the third of them, and it is the shape **N-241** deleted one column over.

    **Every id is re-scoped through :func:`_outstanding_scope` rather than
    trusted.**  The ids arrive from a form, so an id belonging to another
    user, another account, a credit purchase, a NON-CONTRIBUTING parent
    (Credit, Cancelled, soft-deleted) or an already-reconciled entry simply
    does not match and is silently skipped.  A SETTLED parent's purchase is no
    longer in that list and that is the developer's 2026-08-17 ruling: it
    matches, and recording its day is the point.  Skipping is --
    the same "404 for both not-found and not-yours" posture the ownership
    helpers take, expressed as a filter because this is a set operation.
    The count returned is what actually changed, not what was asked for.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        owner_id: The user_id whose purchases these must be.
        account_id: The cash account the balance was asserted for.
        entry_ids: The entry ids the user ticked.  An empty set is a no-op.
        anchor: The governing assertion -- the STATEMENT being reconciled.  Its
            id is what each ticked purchase records, and its ``observed_on`` is
            the day each is recorded as having settled by.

    Returns:
        The number of entries actually stamped.
    """
    if not entry_ids:
        return 0
    observed_on = anchor.observed_on

    # ``synchronize_session=False``, which CLOSES finding **N-223**, and the
    # reasoning that briefly argued the other way is recorded because it was
    # wrong in an instructive way.
    #
    # The rationale this inherited from ``entry_service`` -- "later code in the
    # same request (the grid re-rendering its projection)" -- was FALSE: that
    # re-render is a SEPARATE request raised by ``HX-Trigger: balanceChanged``,
    # and the route's own panel re-render happens after a ``commit()`` that
    # expires the identity map anyway.
    #
    # Plan step X-f2-c2 then put a SECOND writer after this one in the same
    # session -- the transaction arm, which ``selectinload``s the same parents'
    # ``entries`` -- and it looked as though ``'fetch'`` had finally earned
    # itself.  MEASURED, it has not, twice over: that arm reads an entry's
    # ``amount`` and the relationship's truthiness and never its ``settled_on``;
    # and a probe of the identity map at this statement counted **ZERO**
    # ``TransactionEntry`` objects, because nothing in the POST loads one before
    # here.  ``'fetch'`` pre-SELECTs primary keys in order to synchronise an
    # empty set.
    # **THREE columns, and the third says what KIND of day the first is** (plan
    # step **X-az**, finding **N-332**).  ``asserted`` is not a nicety: this
    # writer's own docstring calls the day an UPPER BOUND, and until this step
    # the only way a reader could tell was to test whether ``reconciled_by_id``
    # was populated -- which is exactly the "infer a fact from a column being
    # populated" shape ``settled_basis_id`` exists one column over to delete
    # (finding **N-241**).  The statement matcher is that reader, and reading
    # the bound as an observation cost 50 duplicate purchases worth
    # ``$3,590.00`` before ``f633d46a``.
    #
    # The pair is written HERE rather than through
    # ``settle_day.record_settle_day`` because this arm's writer is a bulk
    # ``UPDATE`` by design (see the module docstring): there is no ORM instance
    # to hand that function.  ``ck_transaction_entries_settle_day_basis_pairing``
    # is what makes a statement that wrote one column and not the other fail
    # loudly rather than leave a day nobody can classify.
    asserted_basis_id = ref_cache.settled_day_basis_id(
        SettledDayBasisEnum.ASSERTED,
    )
    updated = (
        db.session.query(TransactionEntry)
        .filter(
            TransactionEntry.id.in_(entry_ids),
            *_outstanding_scope(owner_id, account_id, observed_on),
        )
        .update(
            {
                TransactionEntry.settled_on: observed_on,
                TransactionEntry.settled_day_basis_id: asserted_basis_id,
                TransactionEntry.reconciled_by_id: anchor.anchor_id,
            },
            synchronize_session=False,
        )
    )

    if updated:
        _post_stamped_purchases(account_id, entry_ids, anchor.anchor_id)
        log_event(
            logger, logging.INFO,
            EVT_ENTRIES_SETTLED_DAY_RECORDED, BUSINESS,
            "Outstanding purchases confirmed against a bank statement",
            user_id=owner_id,
            account_id=account_id,
            observed_on=observed_on.isoformat(),
            recorded_count=updated,
            requested_count=len(entry_ids),
        )

    return updated
