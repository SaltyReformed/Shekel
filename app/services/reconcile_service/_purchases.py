"""
Shekel Budget App -- The PURCHASE arm of the outstanding set

One of the package's three arms (see :mod:`app.services.reconcile_service` for
what an arm is and why there are three): the entries recorded against an
envelope that the bank has not been seen to have taken.  It owns the three
things an arm owns -- its SCOPE, its READ and its WRITE -- and they live in one
module because the scope being literally shared between the read and the write
is the security property, not a convenience.

**Its settle is the odd one of the three, and that is why it is first.**
Settling a purchase writes ONE column on ONE row (``settled_on``) and moves no
status, so this arm's writer is a single bulk ``UPDATE`` narrowed by the same
clauses the reader selected on.  The transaction and transfer arms settle
through a status seam and a posting reconcile, so their writers dispatch to a
service verb per row (ruling **R-FA**).  Nothing here should be generalised
into a shape those two can share until they exist.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, frozen dataclasses out.
  - All monetary arithmetic uses :class:`~decimal.Decimal`.
  - The writer mutates and does NOT commit -- the caller owns the session
    boundary.
"""

import logging
from datetime import date

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services.reconcile_service._offers import OutstandingPurchase
from app.utils.balance_predicates import (
    balance_contributing_clause,
    is_projected_clause,
)
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
    * the parent is PROJECTED and CONTRIBUTING -- the entry reservation
      prices only projected rows
      (:func:`app.services.cash_ledger._amounts._entry_aware_amount`), so an
      entry on a settled parent is inert and listing it would be asking the
      user to reconcile something that cannot move a figure.  Routed through
      the centralized ``is_projected_clause`` (D6-09 / MED-02) so this filter
      shares one definition with every other Projected filter, composed with
      ``balance_contributing_clause`` -- the soft-delete half was a
      hand-written ``is_deleted.is_(False)`` until plan step X-f2-c2, which
      left one package stating "which parent rows exist at all" two ways while
      its new arm's own docstring argued for the shared builder.  The two are
      equivalent today (Projected is neither Credit nor Cancelled); the point
      is that they cannot come to disagree.

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
                is_projected_clause(Transaction),
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
        account with nothing outstanding, which is the steady state for a user
        who reconciles at every true-up.  No list is ever empty: a parent
        appears only because a purchase put it there.
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


def record_settled_days(
    owner_id: int,
    account_id: int,
    entry_ids: "set[int]",
    observed_on: date,
) -> int:
    """Record that the bank had taken *entry_ids* by *observed_on*.

    This arm's writer: the user ticked these purchases off a statement, so each
    one's ``settled_on`` becomes the day that statement's balance was true for.
    The stored date is an UPPER BOUND on the true posting day -- the purchase
    may have cleared a day or two earlier -- and it is the only bound the
    reconciliation predicate consumes (``settled_on <= observed_on``), so no
    answer changes by sharpening it.  A user who wants the exact day off their
    statement edits the entry.

    **Every id is re-scoped through :func:`_outstanding_scope` rather than
    trusted.**  The ids arrive from a form, so an id belonging to another
    user, another account, a credit purchase, a settled parent or an
    already-reconciled entry simply does not match and is silently skipped --
    the same "404 for both not-found and not-yours" posture the ownership
    helpers take, expressed as a filter because this is a set operation.
    The count returned is what actually changed, not what was asked for.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        owner_id: The user_id whose purchases these must be.
        account_id: The cash account the balance was asserted for.
        entry_ids: The entry ids the user ticked.  An empty set is a no-op.
        observed_on: The civil day the asserted balance was true for, and the
            day each ticked purchase is recorded as having settled by.

    Returns:
        The number of entries actually stamped.
    """
    if not entry_ids:
        return 0

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
    updated = (
        db.session.query(TransactionEntry)
        .filter(
            TransactionEntry.id.in_(entry_ids),
            *_outstanding_scope(owner_id, account_id, observed_on),
        )
        .update(
            {TransactionEntry.settled_on: observed_on},
            synchronize_session=False,
        )
    )

    if updated:
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
