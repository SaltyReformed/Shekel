"""
Shekel Budget App -- Assembling the arms' offers into the panel's blocks

The work that is the SAME whatever produced an offer: labelling the parent a
block hangs under, ordering the blocks, and totalling the set.  The arms decide
WHAT this account still owes; this module decides how the answer READS.

**Why the split is here and not somewhere else.**  Labelling a parent, ordering
what the panel shows and totalling it are the same work whatever produced an
offer, so writing them once per arm would be this arc's own root cause 1
applied to the panel.

**Whether a BILL is a block or a flat row is RULED, and it is one collection**
(**R-FC**, 2026-08-10).  Ruling R-EW settled only the purchase case, and a
first draft of this package answered the rest two contradictory ways in one
commit -- this docstring assumed every arm keys into a block by parent id while
``_offers.OutstandingSet`` assumed bills arrive as a sibling LIST.  The
developer picked the flat-bills panel on sight; measuring why dissolved the
fork, because the whole difference was RENDERING.  So: **one collection here,
and three presentational rules downstream** -- a block with no children prints
its name inline rather than above a one-item list, the ordering key gains a
kind term so like sits with like, and a section label is emitted when the kind
changes.  Those reproduce the chosen panel byte-for-byte while X-f2-c2 and
X-f2-c3 each ADD an arm, so nothing below is rewritten inside a money commit
(**R-EY**).

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, frozen dataclasses out.
  - Reads only.  No writer lives here: what a tick MEANS differs per arm
    (ruling **R-FA**), so the write side has no shared body to hoist yet.
    **That is the same rule-13 test this module's own existence has to pass**,
    and a reviewer was right to say the first draft applied it to the write
    side while exempting the read side.  What earns the read seam today is not
    a second arm -- there isn't one -- but that ``_block_headings`` and the
    reduction below name no entry, no purchase and no envelope: they are about
    a BLOCK.  The write side has no equivalent; a tick's meaning is entirely
    per-arm.  When X-f2-c2 lands, the POST settles more than one arm in one
    transaction and the write union gets a home -- decided then, with two arms
    to look at, rather than guessed now.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction

from . import _purchases
from ._offers import OutstandingGroup, OutstandingSet


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
