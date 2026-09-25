"""
Shekel Budget App -- A leg's RECORD: the one join to its covering movement.

The half of :mod:`app.services.transfer_legs` that reaches a leg's covering
MOVEMENT.  :func:`_movements_under_shadows` is the ONE join through the
interval (a movement still hangs off the transfer's shadow row),
:func:`_covering_movements_query` that join narrowed to leg RECORDS by
:func:`_leg_is_record`, and :func:`_leg_transfer_id` and :func:`_leg_is_income`
say which transfer and which side over them.  Every loader in this package that
asks the join lives here -- the plan half's :func:`planned_transfer_legs`
(through :func:`dated_leg_exists_clause`), the settled half's
:func:`transfer_movement_rows` / :func:`recorded_transfer_legs`, the posting
writer's :func:`transfer_family_movements`, and the grid's
:func:`covering_movements_by_leg` / :func:`grid_transfer_leg` /
:func:`grid_transfer_legs` -- beside :func:`movement_parent`, the join's Python
twin over one loaded movement.  Plan step ``balance:X-bi-6-4d`` moves the join
off the shadows HERE, once, for every reader built on it.

**"The module docstring" in the definitions below means the PACKAGE's**
(:mod:`app.services.transfer_legs`): they were written when this was one
module.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import or_
from sqlalchemy.orm import contains_eager

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services.transfer_legs._leg import (
    PlanItem,
    TransferLeg,
    _leg_on_side,
    leg_of,
)
from app.utils.balance_predicates import is_projected_clause


def planned_transfer_legs(
    account_id: int, scenario_id: int, *, options: tuple,
) -> list[TransferLeg]:
    """Return every still-planned leg of a projected transfer *account_id* is on.

    The plan-half loader behind the cash fold's plan
    (:func:`app.services.cash_ledger.planned_cash_rows`) and the loan
    partition's projected half
    (:func:`app.services.loan_loaders.projected_income_legs`): one statement
    against ``budget.transfers``, scoped by account and scenario, narrowed to
    live still-Projected parents through the SAME shared builder the shadow
    loaders narrow with (:func:`~app.utils.balance_predicates.is_projected_clause`),
    so the plan half and the record half cannot disagree about which status is
    "still planned".

    **It takes no period window, for the reason the cash plan loader does not**
    (``cash_ledger._facts.planned_cash_rows``): a fold over a windowed plan is a
    fold over a different account, and an argument a caller can get wrong is a
    defect rather than a contract.

    **The caller states the loads that cost a round trip** (the rule plan step
    ``balance:X-bl-2a`` gave the shadow loaders): a reader that prices the legs
    passes :func:`~app.utils.amount_relationships.transfer_pricing_load_options`,
    one that reads dates alone passes ``()``.  Required rather than defaulted,
    so a new caller decides instead of inheriting a guess.

    Args:
        account_id: The account whose legs to load -- either side.
        scenario_id: The budget scenario the transfers live in.
        options: The loader options for every relationship the caller will
            traverse on the parents, rooted at
            :class:`~app.models.transfer.Transfer`.

    Returns:
        One :class:`TransferLeg` per matching transfer whose leg on this
        account is not yet a dated movement, unordered and with no
        :attr:`~TransferLeg.record`; ``[]`` for an account no still-projected
        transfer touches.
    """
    # The leg's RECORD, when it exists: a dated covering movement on this
    # account (ruling **R-BAL79**).  A correlated EXISTS rather than a join,
    # so a transfer is one row here whatever its shadows hold.
    dated_leg = dated_leg_exists_clause(
        TransactionEntry.account_id == account_id,
    )
    transfers = (
        db.session.query(Transfer)
        .options(*options)
        .filter(
            Transfer.scenario_id == scenario_id,
            Transfer.is_deleted.is_(False),
            is_projected_clause(Transfer),
            or_(
                Transfer.from_account_id == account_id,
                Transfer.to_account_id == account_id,
            ),
            ~dated_leg,
        )
        .all()
    )
    return [leg_of(transfer, account_id) for transfer in transfers]


def _covering_movements_query():
    """Return the query of covering movements joined to their transfer's shadow.

    **The ONE join from a transfer to a leg's record**, through the interval
    before ``X-bi-6``'s last leaf: a movement hangs off the transfer's shadow
    row on that account (``transaction_entries.transaction_id``), so reaching
    it from the parent walks ``transactions.transfer_id``.  Every reader of a
    leg's record builds on this with :func:`_leg_transfer_id` and
    :func:`_leg_is_income` -- the plan half's and the resync's
    :func:`dated_leg_exists_clause`, the grid's
    :func:`covering_movements_by_leg`, and since leaf ``X-bi-6-4a`` (ruling
    **R-BAL106**) every settled-half reader through
    :func:`transfer_movement_rows` and the posting writer through
    :func:`transfer_family_movements` -- so when the movement re-parents onto
    ``budget.transfers`` (``X-bi-6-4d``, ruling **R-BAL88**) the join and
    those expressions move HERE for every reader built on them; the readers
    not yet on them are named in the module docstring.

    A deleted shadow's movement is not a leg's record (:func:`_leg_is_record`),
    and the query says so rather than leaving it to the caller.

    Returns:
        A query rooted at :class:`~app.models.transaction_entry.TransactionEntry`
        with the shadow joined -- built, not executed, and not yet narrowed
        to any transfer.
    """
    return _movements_under_shadows().filter(_leg_is_record())


def _movements_under_shadows():
    """Return the ONE join WITHOUT its record test: covering movements under any row, live or dead.

    :func:`_covering_movements_query` is this narrowed to leg RECORDS; the
    ledger writer's family (:func:`transfer_family_movements`) needs the
    rest too, because a movement that is no leg's record may still hold
    postings the writer must reverse.  The caller narrows it to a transfer
    (:func:`_leg_transfer_id`), which is what makes the row a shadow.
    Interval-only: from ``X-bi-6-4d`` every movement a side links is that
    side's record and the two queries are one.

    Returns:
        The unexecuted query of covering movements with their parent row
        joined, not yet narrowed to any transfer.
    """
    return (
        db.session.query(TransactionEntry)
        .join(Transaction, TransactionEntry.transaction_id == Transaction.id)
        .filter(TransactionEntry.covers_settlement.is_(True))
    )


def _leg_is_record():
    """Return the SQL truth of "this covering movement IS its leg's record".

    The third thing the join is for.  Through the interval a movement is its
    leg's record while the shadow it hangs off is LIVE: a shadow soft-deleted
    around the service (Transfer Invariant 4 drift; no door writes it) leaves
    its movement no leg's, which the fold and the writer both read as worth
    nothing (``tests/test_services/test_transfer_legs.py``'s drift class).
    At ``X-bi-6-4d`` every linked movement is its side's record and this is
    deleted.  :func:`movement_parent` states the same test over a loaded
    movement.
    """
    return Transaction.is_deleted.is_(False)


def _leg_transfer_id():
    """Return the column naming a covering movement's TRANSFER, over the join.

    The first of the two things :func:`_covering_movements_query`'s join is
    for: which transfer a movement is one leg of.  Through the interval it is
    the shadow's ``transfer_id``; at ``X-bi-6-4d`` it is whichever of the
    movement's two side links is set (ruling **R-BAL88**).
    """
    return Transaction.transfer_id


def _leg_is_income():
    """Return the SQL truth of "this covering movement is its transfer's to-side".

    The second thing the join is for: which SIDE a movement is, stated by its
    link rather than inferred from its account.  Through the interval the link
    is the shadow, and the shadow's TYPE is its side -- the transfer service
    writes the income shadow on the to-account and the expense shadow on the
    from-account (``transfer_service._create``), and it is the type the fold
    and the ledger signed a transfer movement by before leaf ``X-bi-6-4a``, so
    reading it here moves no figure in any state.  At ``X-bi-6-4d`` the side
    is which link column is set (ruling **R-BAL88**: ``income_transfer_id``
    keyed to the to-account, ``expense_transfer_id`` to the from-account).
    """
    return Transaction.transaction_type_id == ref_cache.txn_type_id(
        TxnTypeEnum.INCOME,
    )


def transfer_movement_rows(*filters):
    """Return every transfer leg's covering movement WITH its transfer and side.

    **The settled half's ONE loader** (leaf ``X-bi-6-4a``, ruling
    **R-BAL106**): each row is ``(movement, transfer, is_income)`` --
    the :class:`~app.models.transaction_entry.TransactionEntry`, its parent
    :class:`~app.models.transfer.Transfer` and its side -- over
    :func:`_covering_movements_query`'s join, narrowed by the caller's
    *filters*.  A reader takes a movement's period, scenario, status,
    soft-delete and direction from the TRANSFER and the SIDE, never from the
    shadow the movement still hangs off, so the fold, the ledger oracle, the
    savings metric and every later reader of a paid transfer's money move off
    the shadows in one place when ``X-bi-6-4d`` moves the join.  A LOADER, not
    a producer: it selects and returns, and every figure is the caller's
    (``cash_ledger.movement_cash_leg`` for the fold, the oracle's own sign).

    Args:
        *filters: The caller's clauses over ``TransactionEntry`` and the joined
            ``Transfer`` -- the movement's account and day, the parent's
            scenario, status and soft-delete.  Never the shadow's columns:
            this function is where the shadow is named, and a caller that
            filtered on it would be a second place ``X-bi-6-4d`` must find.

    Returns:
        An unexecuted ``Query`` of ``(TransactionEntry, Transfer, bool)``
        rows, ordered by the movement's id so a caller that emits in load
        order is deterministic.
    """
    return (
        _covering_movements_query()
        .join(Transfer, _leg_transfer_id() == Transfer.id)
        .add_entity(Transfer)
        .add_columns(_leg_is_income())
        .filter(*filters)
        .order_by(TransactionEntry.id)
    )


def recorded_transfer_legs(*filters) -> list[TransferLeg]:
    """Return a :class:`TransferLeg` per matching covering movement, record attached.

    :func:`transfer_movement_rows` as legs: the value the grid and the plan
    half already hold a transfer's side as, carrying its movement as
    :attr:`~TransferLeg.record` (leaf ``X-bi-6-4a``).  A leg is its parent,
    so everything :func:`app.services.cash_ledger.movement_cash_leg` asks of a
    movement's parent -- its type, soft-delete and status -- the leg answers
    off the transfer.

    Args:
        *filters: As :func:`transfer_movement_rows`.

    Returns:
        The legs in movement-id order; ``[]`` when none match.
    """
    return [
        _leg_on_side(transfer, is_income=is_income, record=movement)
        for movement, transfer, is_income in transfer_movement_rows(*filters)
    ]


def dated_leg_exists_clause(*filters):
    """Return the SQL form of "this transfer has a leg whose record is DATED".

    A correlated ``EXISTS`` over :func:`_covering_movements_query`, rooted at
    ``Transfer``: one of the transfer's legs has a covering movement carrying
    a ``settled_on``.  Its two readers ask one question two ways -- the plan
    half's :func:`planned_transfer_legs` narrows it to ONE account (that
    side's money moved, so its leg is no longer planned, ruling
    **R-BAL79**), and the posting writer's deploy resync asks it bare (the
    transfer may hold a posted leg whatever its status, the totality
    argument of ``posting_service.resync_all_cash_postings``).  Stated once
    since leaf ``X-bi-6-4a``: the resync's copy lived in
    ``_posting_purchases`` and walked the shadow itself, and it read every
    entry under a live shadow where this reads the covering movements the
    writer's family holds (0 of 38 entries under a shadow were anything else
    on the 2026-09-22 17:06 production dump).

    Args:
        *filters: Further clauses over ``TransactionEntry``, e.g. the
            account.

    Returns:
        A SQLAlchemy ``EXISTS`` clause, correlated to ``Transfer``.
    """
    return (
        _covering_movements_query()
        .filter(
            _leg_transfer_id() == Transfer.id,
            TransactionEntry.settled_on.isnot(None),
            *filters,
        )
        .with_entities(TransactionEntry.id)
        .correlate(Transfer)
        .exists()
    )


def movement_parent(movement: TransactionEntry) -> PlanItem:
    """Return what *movement* records money FOR: its plan row, or its transfer's LEG.

    **The ledger writer's ONE resolution of a loaded movement's parent**
    (leaf ``X-bi-6-4a``, its second half, ruling **R-BAL106**).  Every door
    that books a movement -- the pair door, a row's family reconcile, the
    teardowns and the one removal act -- asks this, and the writer takes
    what it answers as the parent that types the movement's legs: a plan
    row's category and owner, or for a transfer movement the LEG, whose
    period, owner, scenario, contributing gate and side are its TRANSFER's
    (:class:`TransferLeg`), never the shadow row's.  So the ledger and the
    cash fold read a transfer's money off the same parent.

    **Through the interval the answer is read off the shadow the movement
    hangs off**, which is the Python twin of this module's join expressions
    over one loaded movement: :func:`_leg_transfer_id` (the shadow's
    ``transfer_id``), :func:`_leg_is_income` (its type) and
    :func:`_leg_is_record` with the join's ``covers_settlement`` term (the
    leg carries the movement as its :attr:`~TransferLeg.record` only when it
    is one).  A movement under a DEAD shadow of its transfer is no leg's
    record, so its leg carries none and the writer posts nothing for it
    (``_posting_purchases.purchase_posts``) -- what the fold answers too.
    ``tests/test_services/test_transfer_legs.py`` pins the twin against
    :func:`transfer_movement_rows`.  At ``X-bi-6-4d`` both read the
    movement's side links and this stops naming the shadow.

    Args:
        movement: A ``budget.transaction_entries`` row with its parent
            reachable (``movement.transaction``); a transfer movement's
            ``transaction.transfer`` is read too.

    Returns:
        The movement's plan row, or the :class:`TransferLeg` it is booked
        under.
    """
    row = movement.transaction
    if row.transfer_id is None:
        return row
    is_record = movement.covers_settlement and not row.is_deleted
    return _leg_on_side(
        row.transfer, is_income=row.is_income,
        record=movement if is_record else None,
    )


def transfer_family_movements(
    transfer: Transfer,
) -> list[tuple[TransactionEntry, TransferLeg]]:
    """Return every covering movement *transfer*'s family holds, each with its leg.

    The posting writer's pair door walks this
    (``posting_service.sync_transfer_postings`` and its teardown twin): every
    movement ANY shadow of the transfer holds, dead shadows included, each
    paired with the leg :func:`movement_parent` books it under.  Not
    :func:`recorded_transfer_legs`, deliberately: the ledger must reverse
    what a movement that is no leg's record still holds (an idempotent hard
    delete of an already soft-deleted pair must find nothing left, and a
    shadow deleted around the service must not strand its legs when the hard
    delete SET-NULLs its movement's link).  Each movement's shadow rides the
    same statement (``contains_eager``) and its transfer is *transfer*, so
    the walk reads no relationship lazily.  Moved here from
    ``posting_service._transfer_family_movements`` at leaf ``X-bi-6-4a``,
    whose join it now shares; it goes with the shadows at ``X-bi-6-4d``,
    where a transfer's family is its sides' movements.

    Args:
        transfer: The transfer, loaded.

    Returns:
        ``(movement, leg)`` pairs, ascending by movement id so a run's
        entries are deterministic.
    """
    movements = (
        _movements_under_shadows()
        .options(contains_eager(TransactionEntry.transaction))
        .filter(_leg_transfer_id() == transfer.id)
        .order_by(TransactionEntry.id)
        .all()
    )
    return [(movement, movement_parent(movement)) for movement in movements]


def covering_movements_by_leg(
    transfer_ids: Iterable[int],
) -> dict[tuple[int, int], TransactionEntry]:
    """Return ``{(transfer id, account id): the leg's covering movement}``.

    The grid's one load of every leg's record for a window of transfers
    (leaf ``X-bi-6-1``): one statement over :func:`_covering_movements_query`
    narrowed to the transfers named, dated or not -- the grid draws a
    settled leg's recorded figure and day off a dated one and a reverted
    leg's retained figure off an un-dated one (``retained_settle_amounts``'
    rule), so it asks for both.  At most one per key: a shadow holds at most
    one covering movement (``uq_transaction_entries_one_settlement_record``)
    and a transfer at most one live shadow per account
    (``uq_transactions_transfer_type_active``).

    Args:
        transfer_ids: The parents whose legs are being drawn.  Empty answers
            ``{}`` without a query.

    Returns:
        The map, holding a key only for a leg that has a record.
    """
    ids = list(transfer_ids)
    if not ids:
        return {}
    # The transfer rides as a column of the ONE join rather than through the
    # shadow's relationship, so nothing here reads the shadow row.
    movements = (
        _covering_movements_query()
        .add_columns(_leg_transfer_id())
        .filter(_leg_transfer_id().in_(ids))
        .all()
    )
    return {
        (transfer_id, movement.account_id): movement
        for movement, transfer_id in movements
    }


def grid_transfer_leg(transfer: Transfer, account_id: int) -> TransferLeg:
    """Return ONE leg for a grid fragment, its record loaded.

    What a transfer door renders back into a leg's cell (leaf
    ``X-bi-6-1``): :func:`grid_transfer_legs` over one transfer and one
    side, stated separately so the fragment renderers name what they load.

    Args:
        transfer: The parent.
        account_id: The account the leg is on.

    Returns:
        The leg, with its covering movement when one exists.

    Raises:
        ValueError: When *account_id* is neither endpoint (from
            :func:`leg_of`).
    """
    return leg_of(
        transfer, account_id,
        record=covering_movements_by_leg([transfer.id]).get(
            (transfer.id, account_id),
        ),
    )


def grid_transfer_legs(
    transfers: Iterable[Transfer], shown_accounts,
) -> list[TransferLeg]:
    """Return the legs a grid draws for *transfers*, records attached.

    The grid-window loader (leaf ``X-bi-6-1``, ruling **R-BAL87**): for each
    transfer, one :class:`TransferLeg` per account *shown_accounts* names for
    it, each carrying its covering movement from ONE
    :func:`covering_movements_by_leg` load.  Which accounts are shown is the
    caller's rule -- :func:`app.services.cash_flow_set.leg_accounts_shown`
    for the paycheck grid, where a transfer between two members shows once
    from the balance line's side (ruling **R-CC23**) -- taken as a function
    so this loader states no set of its own.

    Args:
        transfers: The parents in the window, loaded by the caller with the
            relationships its render will read (the pricing chain, the
            endpoints, the category).
        shown_accounts: ``transfer -> the account ids whose leg to draw``.

    Returns:
        The legs, in transfer order then side order; ``[]`` for no transfers.
    """
    transfers = list(transfers)
    records = covering_movements_by_leg(t.id for t in transfers)
    legs: list[TransferLeg] = []
    for transfer in transfers:
        for account_id in shown_accounts(transfer):
            legs.append(leg_of(
                transfer, account_id,
                record=records.get((transfer.id, account_id)),
            ))
    return legs
