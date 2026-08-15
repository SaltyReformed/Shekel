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
  - Reads AND the write union.  X-f2-c2 was to "decide then, with two arms to
    look at"; a first draft looked, said the writers "share their ORDER (the
    route's) and nothing else", and shipped that order as two statements in a
    route handler.  **That sentence concedes a shared body and files it under
    the wrong tier.**  The order is a rule ABOUT THE ARMS -- the purchase arm's
    scope requires a PROJECTED parent, which the transaction arm's writer
    destroys -- so writing the close first silently drops every purchase tick
    on that block, and nothing but statement order was stopping it.
    :func:`record_reconciliation` owns it, which is why it is not a
    passthrough: it carries an invariant no caller can see.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services.cash_ledger import AnchorPoint

from . import _purchases, _rows, _transactions, _transfers
from ._offers import (
    OutstandingGroup,
    ReconcileSubmission,
    OutstandingPurchase,
    OutstandingSet,
    OutstandingTransaction,
    Section,
)


def _block_headings(
    owner_id: int, transaction_ids: "set[int]",
) -> "dict[int, tuple[str, date, date]]":
    """Return ``{transaction_id: (name, period_start, period_end)}``.

    The three scalars a block's heading needs, in ONE statement over the ids
    the arms have already established -- not a relationship walk.  See
    :func:`outstanding_set` for why the ``joinedload`` alternative costs 13
    joins to fetch one name.

    It is keyed on the ids the caller HOLDS rather than re-deriving any arm's
    offers, so it cannot answer about a different set than the one being
    grouped.

    **It scopes to the OWNER anyway**, and the redundancy is deliberate.  Every
    id here comes from an arm that already scoped it, so the clause can never
    change an answer today -- which is exactly the argument that would let a
    future caller pass an unscoped set into the one query in a package whose
    stated security property is that scope is SHARED rather than remembered.
    The cost is one indexed predicate.

    Args:
        owner_id: The user_id the parents must belong to.
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
        .filter(
            Transaction.id.in_(transaction_ids),
            PayPeriod.user_id == owner_id,
        )
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
        The same blocks, each carrying a :class:`Section` where its kind
        first appears and ``None`` elsewhere.
    """
    labelled = []
    previous = None
    for group in groups:
        starts_a_section = group.kind is not previous
        labelled.append(replace(
            group,
            section=Section(
                label=group.kind.section_label,
                note=group.kind.section_note,
            ) if starts_a_section else None,
        ))
        previous = group.kind
    return tuple(labelled)


def _tally(
    offers: "list[OutstandingPurchase | OutstandingTransaction]",
) -> "tuple[int, Decimal]":
    """Return ``(count, total)`` for one kind of offer.

    Stated once because the set publishes the same pair THREE times -- purchases,
    payments and deposits (rulings **R-FA** / **R-FD**) -- and a hand-written
    second sum is where one of them ends up counting another's rows.  It takes
    anything carrying an ``amount``, which is both offer types, because the
    reduction is about a list of money and not about which arm produced it.

    Args:
        offers: The offers of one kind, each carrying an ``amount``.

    Returns:
        How many, and what they are worth.
    """
    return (
        len(offers),
        sum((offer.amount for offer in offers), Decimal("0.00")),
    )


def outstanding_set(
    owner_id: int, account_id: int, anchor: AnchorPoint,
) -> OutstandingSet:
    """Return what this account has not been seen to have paid for, grouped.

    The reconcile panel's list.  It asks each arm what it still owes against
    *observed_on*, labels every parent that came back, and reduces the result
    into the :class:`~app.services.reconcile_service.OutstandingSet` the
    boundary publishes.  THREE arms answer: purchases, the source rows
    themselves (plan step X-f2-c2) and transfer shadows (plan step X-f2-c3).

    **All three are unioned on the PARENT's id**, which is why each keys its
    offers on it: an envelope with outstanding purchases AND an overdue close
    is ONE block carrying both, which is ruling **R-EW**'s shape, while a bill
    and a transfer shadow are each a block with a close and no children.

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
        anchor: The governing assertion -- the STATEMENT being reconciled
            against.  Nothing dated after its ``observed_on`` can be inside it,
            and no arm offers one.  The READ takes the whole assertion rather
            than its day because the arms build a
            :class:`~app.services.reconcile_service._rows.Statement` from it and
            the WRITE half stamps its id (ruling **R-FL**); one value threaded
            through both halves is what stops the offer set and the tick
            describing different statements.

    Returns:
        The :class:`~app.services.reconcile_service.OutstandingSet`, its
        ``groups`` ordered by section and then by each block's oldest offer
        (:func:`_block_order`), each block's purchases oldest first.  Empty for
        an account with nothing outstanding -- **which stopped being the steady
        state at plan step X-f2-c2**, because an envelope's close is offerable
        for the whole of its own period and only closing it clears one.
        Replayed over production's 53 Checking assertion DAYS (57 assertion
        ROWS -- the two figures answer different questions and one word was
        doing both jobs), 46 carry at least one offer.  This docstring said 48
        and 44 until plan step X-f2-c3 re-took the measurement; its two
        siblings said 53 and 46, so one package held two answers to one count.
        Finding **N-227** owns that bound.
    """
    blocks = _purchases.outstanding_purchases(
        owner_id, account_id, anchor.observed_on,
    )
    # The two source-row arms union into ONE map, and they can: their scopes
    # are complements (``transfer_id IS NULL`` against ``IS NOT NULL``), so no
    # id is in both and the merge cannot silently drop one arm's offer.
    settles = {
        **_transactions.outstanding_transactions(
            owner_id, account_id, anchor,
        ),
        **_transfers.outstanding_transfers(
            owner_id, account_id, anchor,
        ),
    }
    parents = set(blocks) | set(settles)
    headings = _block_headings(owner_id, parents)

    groups = [
        OutstandingGroup(
            transaction_id=transaction_id,
            name=headings[transaction_id][0],
            period_start=headings[transaction_id][1],
            period_end=headings[transaction_id][2],
            purchases=tuple(blocks.get(transaction_id, ())),
            settle=settles.get(transaction_id),
            # Resolved by ``_sectioned`` once the order is known: a block
            # cannot know whether it STARTS a section before it knows what
            # precedes it.
            section=None,
        )
        for transaction_id in parents
    ]
    groups.sort(key=_block_order)

    # Every pair through ``_tally``, including the purchases -- which were
    # counted off ``blocks`` and totalled off ``groups`` until a review pointed
    # out that ``_tally``'s whole reason for existing is that two sums of one
    # set cannot be written two ways.
    purchase_count, purchase_total = _tally(
        [purchase for group in groups for purchase in group.purchases],
    )
    payment_count, payment_total = _tally(
        [offer for offer in settles.values() if not offer.is_income],
    )
    deposit_count, deposit_total = _tally(
        [offer for offer in settles.values() if offer.is_income],
    )
    return OutstandingSet(
        groups=_sectioned(groups),
        purchase_count=purchase_count,
        purchase_total=purchase_total,
        payment_count=payment_count,
        payment_total=payment_total,
        deposit_count=deposit_count,
        deposit_total=deposit_total,
    )


def record_reconciliation(submission: ReconcileSubmission) -> int:
    """Record everything a statement settled, in the ONE order that works.

    The write union.  Each arm still owns what a tick MEANS for its own rows
    (ruling **R-FA**); what lives here is the rule that spans them, and it is
    not an HTTP concern:

    **Purchases are stamped BEFORE the source rows settle, because the purchase
    arm's scope requires a PROJECTED parent** and settling an envelope's close
    is exactly what takes that parent out of it.  Reversed, every purchase
    ticked on a block whose close was also ticked is silently skipped -- the
    call reports success, the panel re-renders, and the entries still read
    outstanding.  Ticking a whole block at once is how a statement is walked,
    so this is the ordinary case rather than an exotic one.

    It was two statements in a route handler until an adversarial review named
    it: an invariant enforced by the order of two lines, in a tier that owns
    neither arm, with nothing able to fail if a later edit swapped them.

    **The transfer arm's position is FREE and is fixed anyway.**  Its scope is
    the complement of the transaction arm's and disjoint from the purchase
    arm's parents -- a shadow can hold no entries -- so no ordering between it
    and either of them can change an outcome.  It runs last because a sequence
    with one hard rule in it should not also have an unstated arbitrary part:
    the order is written down here so a reader learns which half is which.

    **The three arms are handed ONE set of ticked transaction ids**, and each
    re-scopes it.  The two source-row scopes partition ``budget.transactions``
    on ``transfer_id``, so an id settles through exactly one of them and can
    never settle twice; a second form field would be a second place for the
    panel and the writers to agree about which control posts what.

    **All three run in the caller's transaction and NONE commits.**  A
    statement is one act: four purchases, their envelope's close and the
    transfer beneath them mean all six or none, so a commit between the arms
    would leave the part that failed invisible behind a rendered success.

    Args:
        submission: The :class:`ReconcileSubmission` -- one statement's worth of
            ticks, already parsed and owner-scoped by the route.

    Returns:
        How many of the submitted ticks actually LANDED, across all three arms
        -- never what was asked for.  The caller compares it against what was
        submitted to tell a user their ticks landed on rows something else had
        already moved.  **One total rather than a tuple per arm**, because that
        is the only thing the caller does with them and a per-arm breakdown
        would be three numbers nobody adds up differently.

    Raises:
        ValidationError: Propagated from a settle verb -- an illegal transition
            a stale panel can still submit.
        PostingError: Propagated from a verb's ledger reconcile.  Fails loud.
    """
    purchases = _purchases.record_settled_days(
        submission.owner_id, submission.account_id,
        submission.entry_ids, submission.anchor,
    )
    statement = _rows.Statement(
        submission.owner_id, submission.account_id, submission.anchor,
    )
    source_rows = sum(
        _rows.record_settled(
            arm, statement,
            submission.transaction_ids, submission.corrections,
        )
        for arm in (
            _transactions.ARM,
            _transfers.arm(submission.owner_id),
        )
    )
    return purchases + source_rows
