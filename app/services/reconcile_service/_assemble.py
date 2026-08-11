"""
Shekel Budget App -- Assembling the arms' offers into the panel's blocks

The work that is the SAME whatever produced an offer: labelling the parent a
block hangs under, ordering the blocks, and totalling the set.  The arms decide
WHAT this account still owes; this module decides how the answer READS.

**Why the split is here and not somewhere else.**  Plan step X-f2-c2 adds a
transaction arm and X-f2-c3 a transfer arm, and each one returns its own offers
against the same key -- the parent ``budget.transactions`` id.  If each arm
assembled its own blocks, the heading rule, the ordering rule and the totals
would be written three times, which is this arc's own root cause 1 applied to
the panel.  So an arm returns offers and this unions them.  Today there is one
arm to union, and the seam still earns its place: it is what makes the second
arm an ADDITION rather than a rewrite of the first.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, frozen dataclasses out.
  - Reads only.  No writer lives here: what a tick MEANS differs per arm
    (ruling **R-FA**), so the write side has no shared body to hoist and
    inventing a dispatcher before the second arm exists would be the
    speculative structure rule 13 forbids.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services.reconcile_service import _purchases
from app.services.reconcile_service._offers import (
    OutstandingGroup,
    OutstandingSet,
)


def _block_headings(
    transaction_ids: "set[int]",
) -> "dict[int, tuple[str, date, date]]":
    """Return ``{transaction_id: (name, period_start, period_end)}``.

    The three scalars a block's heading needs, in ONE statement over the ids
    the arms have already established -- not a relationship walk.  See
    :func:`outstanding_set` for why the ``joinedload`` alternative costs 13
    joins to fetch one name.

    It is keyed on the ids the caller HOLDS rather than re-deriving any arm's
    offers, so it cannot answer about a different set than the one being
    grouped.

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

    The reconcile panel's list.  It asks each arm what it still owes against
    *observed_on*, labels every parent that came back, and reduces the result
    into the :class:`~app.services.reconcile_service.OutstandingSet` the
    boundary publishes.  One arm answers today (purchases); plan steps X-f2-c2
    and X-f2-c3 add the parent's own close tick with bills, and transfer
    shadows.

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
        The :class:`~app.services.reconcile_service.OutstandingSet`.  Its
        ``groups`` are ordered by each block's oldest outstanding purchase and
        each block's purchases are oldest first, with the entry id breaking a
        same-day tie deterministically -- both orderings are the purchase arm's
        insertion order, consumed rather than re-derived (ruling R1's cash
        rule: the loader owns the order).  Empty for an account with nothing
        outstanding, which is the steady state for a user who reconciles at
        every true-up.
    """
    blocks = _purchases.outstanding_purchases(
        owner_id, account_id, observed_on,
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
        purchase_count=sum(len(purchases) for purchases in blocks.values()),
        purchase_total=sum(
            (group.total for group in groups), Decimal("0.00"),
        ),
    )
