"""
Shekel Budget App -- Assembling the arms' offers into the panel's blocks

The work that is the SAME whatever produced an offer: labelling the parent a
block hangs under, ordering the blocks, sectioning them, and totalling the set.
The arms decide WHAT this account still owes; this module decides how the answer
READS.

**Why the split is here and not somewhere else.**  Labelling a parent, ordering
what the panel shows and totalling it are the same work whatever produced an
offer, so writing them once per arm would be this arc's own root cause 1
applied to the panel.

**Whether a BILL is a block or a flat row is RULED, and it is one collection**
(**R-FC**, 2026-08-10).  The developer picked the flat-bills panel on sight;
measuring why dissolved the fork, because the whole difference was RENDERING.
So: **one collection here, and three presentational rules** -- a block with no
children prints its name inline rather than above a one-item list (the
template's, off an empty ``purchases``), the ordering key gains a kind term so
like sits with like, and a section label is emitted where the kind changes.
Those reproduce the chosen panel byte-for-byte while X-f2-c2 and X-f2-c3 each
ADD an arm, so nothing here is rewritten inside a money commit (**R-EY**).

**The ORDER is this module's since X-f2-c2, and it had to become so.**  Until
then the block order WAS the purchase arm's insertion order, consumed rather
than re-derived (ruling R1's cash rule: the loader owns the order).  Two arms
return two maps keyed on the same parent, and the union of two dicts has no
meaningful insertion order -- so the seam where the arms MEET is the only place
that can own it.  :func:`_block_order` is written so the one-arm case is
unchanged: with purchases alone the key reduces to each block's oldest
purchase, entry id breaking a same-day tie, which is exactly the sequence the
arm's own ``ORDER BY`` produced.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, frozen dataclasses out.
  - Reads only.  **The write side is deliberately still NOT here.**  What a
    tick MEANS differs per arm (ruling **R-FA**) -- a purchase stamps one
    column, a transaction runs a service verb -- and X-f2-c2, which was to
    "decide then, with two arms to look at", looked and found no shared body:
    the two writers share their ORDER (the route's, and it is load-bearing) and
    nothing else.  A union verb here would be a passthrough that took the
    arms' arguments and added a name.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction

from . import _purchases, _transactions
from ._offers import OutstandingGroup, OutstandingSet, OutstandingTransaction


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


def _block_order(group: OutstandingGroup) -> "tuple[int, date, int, int]":
    """Return a block's sort key: its section, then its OLDEST offer.

    One rule over both arms, and total: every offer contributes
    ``(day, arm rank, row id)`` and the block takes the minimum.  A purchase
    offers its purchase day, a settle its attribution day; the arm rank breaks
    a same-day tie between them so a block is never ordered by whichever arm
    happened to be read first, and the row id breaks the rest.

    **The one-arm case is unchanged, which is what keeps this out of R-EY's
    way.**  With purchases alone the key reduces to (oldest purchase day, 0,
    that purchase's entry id) -- the first appearance of each parent in a list
    the purchase arm already sorted ``(purchased_on, id)``, i.e. exactly the
    insertion order this module consumed before there was a second arm.

    Args:
        group: The block, before its section label is resolved.

    Returns:
        ``(section rank, day, arm rank, row id)``.  A block always carries at
        least one offer -- :func:`outstanding_set` builds none otherwise -- so
        the minimum is always defined.
    """
    offers = [
        (purchase.purchased_on, 0, purchase.entry_id)
        for purchase in group.purchases
    ]
    if group.settle is not None:
        offers.append(
            (group.settle.attributed_on, 1, group.settle.transaction_id),
        )
    day, arm, row_id = min(offers)
    return (group.kind.rank, day, arm, row_id)


def _sectioned(
    groups: "list[OutstandingGroup]",
) -> "tuple[OutstandingGroup, ...]":
    """Return *groups* with a section label on the first block of each kind.

    Ruling **R-FC**'s third presentational rule, applied where the ORDER is
    known.  A template deriving it would compare against the previous element
    by index, and index arithmetic over a sorted list is how a heading silently
    stops appearing -- on a screen whose sections are the only thing
    distinguishing a `$412.33` envelope from a `$412.33` bill.

    Args:
        groups: The blocks, already ordered by :func:`_block_order`.

    Returns:
        The same blocks, each carrying ``section_label`` where its kind first
        appears and ``None`` elsewhere.
    """
    labelled = []
    previous = None
    for group in groups:
        labelled.append(replace(
            group,
            section_label=(
                group.kind.section_label if group.kind is not previous
                else None
            ),
        ))
        previous = group.kind
    return tuple(labelled)


def _tally(
    offers: "list[OutstandingTransaction]",
) -> "tuple[int, Decimal]":
    """Return ``(count, total)`` for one kind of transaction offer.

    Stated once because the set publishes the same pair twice -- payments and
    deposits (ruling **R-FD**) -- and a second hand-written sum is where one of
    them would end up counting the other's rows.

    Args:
        offers: The offers of one kind.

    Returns:
        How many, and what they would book.
    """
    return (
        len(offers),
        sum((offer.amount for offer in offers), Decimal("0.00")),
    )


def outstanding_set(
    owner_id: int, account_id: int, observed_on: date,
) -> OutstandingSet:
    """Return what this account has not been seen to have paid for, grouped.

    The reconcile panel's list.  It asks each arm what it still owes against
    *observed_on*, labels every parent that came back, and reduces the result
    into the :class:`~app.services.reconcile_service.OutstandingSet` the
    boundary publishes.  Two arms answer today -- purchases, and the source
    rows themselves (plan step X-f2-c2) -- and plan step X-f2-c3 adds transfer
    shadows.

    **The two arms are unioned on the PARENT's id**, which is why both key
    their offers on it: an envelope with outstanding purchases AND an overdue
    close is ONE block carrying both, which is ruling **R-EW**'s shape, while a
    bill is a block with a close and no children.

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
        owner_id: The user_id whose offers to list.
        account_id: The cash account whose balance was asserted.
        observed_on: The civil day that balance was true for -- nothing dated
            after it can be inside it, and neither arm offers one.

    Returns:
        The :class:`~app.services.reconcile_service.OutstandingSet`, its
        ``groups`` ordered by section and then by each block's oldest offer
        (:func:`_block_order`), each block's purchases oldest first.  Empty for
        an account with nothing outstanding, which is the steady state for a
        user who reconciles at every true-up.
    """
    blocks = _purchases.outstanding_purchases(
        owner_id, account_id, observed_on,
    )
    settles = _transactions.outstanding_transactions(
        owner_id, account_id, observed_on,
    )
    headings = _block_headings(set(blocks) | set(settles))

    groups = [
        OutstandingGroup(
            transaction_id=transaction_id,
            name=headings[transaction_id][0],
            period_start=headings[transaction_id][1],
            period_end=headings[transaction_id][2],
            purchases=tuple(blocks.get(transaction_id, ())),
            settle=settles.get(transaction_id),
            # Resolved by ``_sectioned`` once the order is known: a block
            # cannot know whether it starts a section before it knows what
            # precedes it.
            section_label=None,
        )
        for transaction_id in set(blocks) | set(settles)
    ]
    groups.sort(key=_block_order)

    payment_count, payment_total = _tally(
        [offer for offer in settles.values() if not offer.is_income],
    )
    deposit_count, deposit_total = _tally(
        [offer for offer in settles.values() if offer.is_income],
    )
    return OutstandingSet(
        groups=_sectioned(groups),
        purchase_count=sum(len(purchases) for purchases in blocks.values()),
        purchase_total=sum(
            (group.total for group in groups), Decimal("0.00"),
        ),
        payment_count=payment_count,
        payment_total=payment_total,
        deposit_count=deposit_count,
        deposit_total=deposit_total,
    )
