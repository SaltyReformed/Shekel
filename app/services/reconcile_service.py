"""
Shekel Budget App -- The outstanding set: what a bank statement can still settle

The reconcile step's reader and its writer, and the ONE definition of
"outstanding" they share (plan step X-f2-c1, ruling **R-EW**).  A balance
assertion says what the account really held on a civil day; this module answers
the question that follows it -- *which of the things you have recorded had the
bank not yet taken by then* -- and records the answer the user gives.

**Why it is not in :mod:`app.services.entry_service`, where it was born.**  Two
reasons, and only the second is structural.

* The subject moved.  As shipped at plan step S1-c the set was purchases and
  nothing else, so it sat naturally beside the CRUD for a purchase.  Ruling
  **R-EW** widens it to everything a statement can settle -- purchases nested
  under their own envelope, the envelope's own close, bills, transfer shadows --
  and three of those four are not entries at all.  A module named for one row
  type would then own the rule about four.
* ``entry_service`` stood at **991 of pylint's 1000-line ceiling** when this
  step opened, so the transaction half of the scope could not have been added
  there in any case.  That is the fourth module in this arc to reach the
  ceiling (findings **N-152**, **N-156**, **N-201**), and the answer each time
  is the cut the subject already wanted rather than another round of shaving
  prose off a measured claim.

**It is a MODULE and not yet a package**, deliberately.  ``cash_ledger`` /
``balance_at`` / ``loan_ledger`` are packages because each exports twenty or
more symbols over several independent verbs; this holds one question and one
answer, and a package for two functions is the speculative structure rule 13
forbids.

**The split is an OWED step, not a trigger left in a docstring.**  Saying "it
becomes a package when the ceiling forces it" would schedule a structural
refactor into whichever leaf happens to breach 1000 lines -- and both leaves
that widen this module (X-f2-c2, X-f2-c3) MOVE MONEY, which is exactly the
combination ruling **R-EY** refused for X-ad and exactly how findings
**N-152** / **N-156** / **N-201** were each created.  So X-f2-c2's own
specification carries the obligation instead: measure this module against the
ceiling FIRST, and if the projection binds, split it in its own zero-money
commit before the money commit.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, frozen dataclasses out.
  - All monetary arithmetic uses :class:`~decimal.Decimal`.
  - The writer mutates and does NOT commit -- the caller owns the session
    boundary.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.utils.balance_predicates import is_projected_clause
from app.utils.log_events import (
    BUSINESS,
    EVT_ENTRIES_SETTLED_DAY_RECORDED,
    log_event,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutstandingPurchase:
    """One purchase the account has not been seen to have paid for.

    A VALUE, not the ORM row: the panel renders four fields and the form posts
    one id, and publishing the row itself is what let the template reach
    ``entry.transaction.name`` per line -- one lazy SELECT per purchase, on a
    relationship the grouping below has already loaded (see
    :func:`outstanding_set`).

    Attributes:
        entry_id: The ``budget.transaction_entries`` id, and the value the
            tick posts back.  Re-scoped by :func:`record_settled_days` rather
            than trusted, so publishing it grants nothing.
        purchased_on: The day the purchase was MADE -- the budget clock, and
            what the row is captioned with.  Never the day it settled: an
            outstanding purchase has no settle day, which is the definition.
        description: The purchase's own description, as typed.
        amount: The purchase's amount, positive.
    """

    entry_id: int
    purchased_on: date
    description: str
    amount: Decimal


@dataclass(frozen=True)
class OutstandingGroup:
    """One source row, with the purchases against it that are still outstanding.

    **The grouping is ruling R-EW's shape**: a purchase nests under the thing it
    belongs to, so a grocery purchase and the grocery envelope read as one
    block.  Rejected there: grouping by act-type, which separates exactly those
    two, and a flat undifferentiated list, which is what shipped at S1-c and
    named each purchase's parent in a trailing fragment per line instead.

    **It is a GROUP and not an ``OutstandingEnvelope``, on two grounds.**
    Nothing in :func:`_outstanding_scope` asserts the parent is envelope-tracked
    -- the clauses ask for a projected, non-deleted parent on this account, and
    "it has purchases" is what makes it one in practice -- so the narrower name
    would claim a classification no clause checks.  And plan step X-f2-c2 puts
    BILLS and transfer shadows in this same set, neither of which is an
    envelope, so the narrow name would have to be renamed inside a leaf that
    moves money.

    Attributes:
        transaction_id: The parent ``budget.transactions`` id, and the key the
            grouping is built on.  Published because plan step X-f2-c2 adds the
            parent's OWN close tick to this block and posts it; nothing in this
            leaf posts it.
        name: The parent's name, for the block's heading.
        period_start: The first day of the pay period the parent is budgeted
            in, so the heading names WHICH one.  **Without it two blocks can
            carry the identical heading**: the recurrence engine materialises
            one row per template per period, so one envelope in two periods is
            two parents with one name, and both can hold outstanding purchases
            at one assertion.  The flat list was equally ambiguous per line;
            grouping PROMOTES that ambiguity to the heading, so the leaf that
            creates the heading is the leaf that has to resolve it.
        period_end: Its last day, so the caption is a span rather than a date
            the user has to look up.
        purchases: Its outstanding purchases, oldest first.  Never empty -- a
            parent with nothing outstanding is not a block, and
            :func:`outstanding_set` does not build one.
        total: The sum of :attr:`purchases`, quantised by the source amounts
            rather than by any arithmetic here.
    """

    transaction_id: int
    name: str
    period_start: date
    period_end: date
    purchases: "tuple[OutstandingPurchase, ...]"
    total: Decimal


@dataclass(frozen=True)
class OutstandingSet:
    """The PURCHASES a statement of one civil day could still settle, grouped.

    **Its scope is purchases, and every field says so** (ruling R-EW widens the
    OFFER to bills, the parent's own close and transfer shadows at plan steps
    X-f2-c2 and X-f2-c3; this leaf ships the first of the four).  A field called
    ``total`` here would be read by the next leaf as "the sum of every tickable
    row", and that figure DOUBLE-COUNTS the moment the close tick joins: an
    envelope settles at ``sum(entries)`` (ruling **R-FA**), so counting its two
    `$40` / `$60` purchases AND its `$100` close reports `$200` against `$100`
    of money.  Naming the fields for what they hold is what stops the next leaf
    implementing a wrong definition literally.

    Attributes:
        groups: One block per parent carrying outstanding purchases, ordered by
            that parent's oldest outstanding purchase so the block a user is
            most likely looking for is first.
        purchase_count: How many purchases the set offers.  Computed here and
            not in the template because it is the figure the panel's copy
            pluralises on, and money-adjacent counting belongs on the services
            side of the boundary.
        purchase_total: The sum of those purchases.
    """

    groups: "tuple[OutstandingGroup, ...]"
    purchase_count: int
    purchase_total: Decimal

    @classmethod
    def empty(cls) -> "OutstandingSet":
        """Return the set for an account with nothing to reconcile.

        The ROUTE's shape for an account carrying no assertion at all: there is
        no day for a purchase to be inside of, so the producer is never asked.
        It is a constructor here rather than a literal there because the zero
        is MONEY, and the services boundary is where money is built (a route
        composing ``Decimal("0.00")`` is the shape ``outstanding_total`` had
        before this leaf, and it is how a caller ends up quantising).
        """
        return cls(groups=(), purchase_count=0, purchase_total=Decimal("0.00"))

    @property
    def is_empty(self) -> bool:
        """Return True when the account has nothing outstanding.

        The steady state for a user who reconciles as they go, and the state
        the panel answers with its "nothing is being held back twice" copy
        rather than an empty form.

        **Read off the COUNT, not off ``groups``**, and the difference is a
        wrong empty state rather than a style point: when X-f2-c2 adds bills as
        a sibling list, ``not self.groups`` would answer True for a panel with
        bills to offer and suppress them behind the "nothing is being held back
        twice" copy.  One definition of empty, and it is "nothing to tick".
        """
        return self.purchase_count == 0


def _outstanding_scope(owner_id: int, account_id: int, observed_on: date):
    """Return the filter clauses for "not yet seen on a statement".

    The ONE definition of the outstanding PURCHASE set, shared by the reader
    (:func:`outstanding_set`) and the writer (:func:`record_settled_days`) so a
    purchase the panel does not OFFER can never be stamped by a forged id -- and
    so the two cannot drift about what "outstanding" means, which is the shape
    this whole step exists to end.

    **"Purchase" is load-bearing in that sentence.**  Plan step X-f2-c2 adds a
    TRANSACTION twin with its own bound (``attribution_date <= observed_on``,
    applied in Python over an SQL superset), so this becomes one of two scopes
    and the sharing property has to hold per scope rather than over the set as
    a whole.  Writing "the outstanding set" here would make that leaf falsify a
    security argument instead of extending it.

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
    * the parent is PROJECTED and not soft-deleted -- the entry reservation
      prices only projected rows
      (:func:`app.services.cash_ledger._amounts._entry_aware_amount`), so an
      entry on a settled parent is inert and listing it would be asking the
      user to reconcile something that cannot move a figure.  Routed through
      the centralized ``is_projected_clause`` (D6-09 / MED-02) so this filter
      shares one definition with every other Projected filter.

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
                Transaction.is_deleted.is_(False),
                is_projected_clause(Transaction),
            )
        ),
    ]


def _block_headings(
    transaction_ids: "set[int]",
) -> "dict[int, tuple[str, date, date]]":
    """Return ``{transaction_id: (name, period_start, period_end)}``.

    The three scalars a block's heading needs, in ONE statement over the ids
    the grouping has already established -- not a relationship walk.  See
    :func:`outstanding_set` for why the ``joinedload`` alternative costs 13
    joins to fetch one name.

    It is keyed on the ids the caller HOLDS rather than re-deriving the
    outstanding set, so it cannot answer about a different set than the one
    being grouped.

    Args:
        transaction_ids: The parents to label.  Empty is answered with an empty
            map and issues no query -- ``IN ()`` is a statement with no rows to
            find.

    Returns:
        One entry per id.  Every id comes from a row this request just read
        inside one transaction, so a missing parent is not reachable; a caller
        that indexed a missing id would raise ``KeyError`` rather than render a
        block with no heading, which is the honest failure.
    """
    if not transaction_ids:
        return {}
    rows = (
        db.session.query(
            Transaction.id,
            Transaction.name,
            PayPeriod.start_date,
            PayPeriod.end_date,
        )
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(Transaction.id.in_(transaction_ids))
        .all()
    )
    return {row[0]: (row[1], row[2], row[3]) for row in rows}


def outstanding_set(
    owner_id: int, account_id: int, observed_on: date,
) -> OutstandingSet:
    """Return what this account has not been seen to have paid for, grouped.

    The reconcile panel's list: debit purchases made on or before *observed_on*
    whose posting day has never been recorded, so the projection is still
    holding their whole envelope budget back.  Ticking one is what tells the
    app the bank has taken the money (:func:`record_settled_days`).

    **This is the question a stored ``is_cleared`` flag used to answer by
    guessing.**  The flag was written by a bulk UPDATE at every true-up over
    "every entry dated on or before the SERVER's today", so a purchase recorded
    after the true-up was never reconciled and one recorded before always was,
    whether or not the bank had taken either.  The list this returns is the
    same question asked of the user, who is holding the statement.

    **The parents are read in ONE narrow statement, and that is a fix rather
    than a tidy-up.**  The flat reader this replaced returned bare
    :class:`~app.models.transaction_entry.TransactionEntry` rows and the
    template reached ``entry.transaction.name`` per line -- a SELECT per
    distinct parent on a ``lazy="select"`` relationship.  A ``joinedload`` of
    that relationship would fix the count and cost a statement carrying **13
    LEFT OUTER JOINs and around a hundred columns**, because ``Transaction``
    eager-joins its account, status, category and type and ``Account`` eager-
    joins four parameter tables -- all to fetch one name.  The grouping needs
    three scalars per parent, so it asks for three.

    Reads only (no writes, no commit).

    Args:
        owner_id: The user_id whose purchases to list.
        account_id: The cash account whose balance was asserted.
        observed_on: The civil day that balance was true for -- purchases made
            after it cannot be inside it and are not listed.

    Returns:
        The :class:`OutstandingSet`.  Its ``groups`` are ordered by each
        block's oldest outstanding purchase and each block's purchases are
        oldest first, with the entry id breaking a same-day tie
        deterministically.  Empty for an account with nothing outstanding,
        which is the steady state for a user who reconciles at every true-up.
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

    headings = _block_headings(set(blocks))
    groups = tuple(
        OutstandingGroup(
            transaction_id=transaction_id,
            name=headings[transaction_id][0],
            period_start=headings[transaction_id][1],
            period_end=headings[transaction_id][2],
            purchases=tuple(purchases),
            total=sum(
                (purchase.amount for purchase in purchases), Decimal("0.00"),
            ),
        )
        for transaction_id, purchases in blocks.items()
    )
    return OutstandingSet(
        groups=groups,
        purchase_count=len(rows),
        purchase_total=sum(
            (group.total for group in groups), Decimal("0.00"),
        ),
    )


def record_settled_days(
    owner_id: int,
    account_id: int,
    entry_ids: "set[int]",
    observed_on: date,
) -> int:
    """Record that the bank had taken *entry_ids* by *observed_on*.

    The reconcile step's writer: the user ticked these purchases off a
    statement, so each one's ``settled_on`` becomes the day that statement's
    balance was true for.  The stored date is an UPPER BOUND on the true
    posting day -- the purchase may have cleared a day or two earlier -- and it
    is the only bound the reconciliation predicate consumes
    (``settled_on <= observed_on``), so no answer changes by sharpening it.
    A user who wants the exact day off their statement edits the entry.

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

    # ``synchronize_session='fetch'`` so anything already holding these rows
    # sees the new posting day WITHOUT waiting for the session boundary -- which
    # this function does not own.  The rationale carried here from
    # ``entry_service`` said "later code in the same request (the grid
    # re-rendering its projection)", and that is FALSE at the one live caller:
    # the grid re-render is a SEPARATE request raised by ``HX-Trigger:
    # balanceChanged``, and the route's own panel re-render happens after a
    # ``commit()`` that expires the identity map anyway (nothing in ``app/``
    # overrides ``expire_on_commit``).  So the flag costs a pre-SELECT and buys
    # nothing for today's caller; it is kept rather than flipped because
    # changing it is a behaviour change on a write path and belongs to the leaf
    # that revisits this writer (X-f2-c2), not to a docstring correction.
    updated = (
        db.session.query(TransactionEntry)
        .filter(
            TransactionEntry.id.in_(entry_ids),
            *_outstanding_scope(owner_id, account_id, observed_on),
        )
        .update(
            {TransactionEntry.settled_on: observed_on},
            synchronize_session="fetch",
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
