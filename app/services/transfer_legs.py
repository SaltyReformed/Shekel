"""
Shekel Budget App -- A transfer's two LEGS, derived from its parent.

The PLAN half of ruling **R-BAL13** (plan step **X-bi-6a**), and since leaf
``X-bi-6-1`` (ruling **R-BAL87**) the GRID's view of a transfer as well: a
transfer's two legs are not rows in ``budget.transactions``.  A still-projected transfer is
ONE economic event, and what each account it touches will see is a
PROJECTION of the parent row -- the from-side an expense, the to-side an
income, each worth exactly what the parent resolves to.  This module states
that projection ONCE, as a value (:class:`TransferLeg`) and the loader
that produces it (:func:`planned_transfer_legs`), so every reader of an
account's plan derives the same pair from the same parent.

**What it replaces.**  Until this step every balance reader loaded the two
SHADOW rows the transfer service writes beside each parent -- an expense
``Transaction`` on the from-account and an income ``Transaction`` on the
to-account -- and priced each through amount rule 5 (*a shadow is worth its
parent*).  The shadows still exist and the transfer service still maintains
them, for the settle doors and the screens that render them; what changed is
that no reader FOLDING a projection reads one.  The settled half is each
shadow's covering MOVEMENT since plan step ``balance:X-bi-4a``, and plan
step ``X-bi-6`` deletes the rows, at which point the maintenance contract
that kept a shadow equal to its parent (Transfer Invariant 3) has nothing
left to keep in step.

**A leg is planned exactly while its own DATED movement does not exist**
(ruling **R-BAL79**, plan step ``balance:X-bi-4a``; ledger row
**BAL-500**).  The settled half reads a leg as the dated covering movement
under the transfer's shadow on that account, so the MOVEMENT -- not the
parent's status -- is what decides which relation a leg is in, per side:
a transfer whose parent is still Projected while one side's movement has
been dated (a state no door writes today and Transfer Invariant 3 forbids;
``X-bi-6``'s per-leg settle days make it the ordinary transitional state)
is emitted here for the OTHER side alone, so no leg is counted by both
halves.  Through ``X-bi-3e`` both legs were emitted off the parent's status
and that state read ``-$500.00`` on a `$250.00` transfer.

**A leg is DERIVED and carries its parent, deliberately.**  It stores no
figure, no period and no date of its own: :attr:`~TransferLeg.due_date`
and :attr:`~TransferLeg.pay_period_id` read the parent's columns, and
what the leg is WORTH is
:func:`app.services.cash_ledger.resolve_transfer_amount` over the parent --
the ONE producer ruling **R-BAL10** put a transfer's amount on.  A leg that
held a copy of any of those would be a shadow row in memory, the very shape
this step exists to stop reading.

**The same value is what the GRID draws in a transfer's cell** (leaf
``X-bi-6-1``, ruling **R-BAL87**): a leg's identity on a grid is the pair
``(transfer id, the account it is on)`` -- :attr:`~TransferLeg.cell_key` --
and its cell's doors are the transfer's own routes.  The grid's leg carries
one more thing the fold's never does: its RECORD, the covering movement the
status seam wrote when the transfer settled (:attr:`~TransferLeg.record`).
**R-BAL87's text says "its dated covering movement"; the record here is
that movement dated OR kept un-dated across a revert** (ruling **R-BAL61**),
one word wider than the ruling's, because the grid draws a reverted leg's
"marking paid records $X" caption off the kept movement exactly as it draws
a reverted row's (``retained_settle_amounts_by_id``'s rule); a settled leg's
record is dated, so the ruling's case is unchanged.  Through the interval
before ``X-bi-6``'s last leaf that movement hangs off the transfer's shadow
row on that account, so :func:`covering_movements_by_leg` reaches it through
ONE join -- the join :func:`planned_transfer_legs` already uses to decide
which relation a leg is in -- and the last leaf moves that join once when
the movement re-parents onto ``budget.transfers``.  The fold's loader still
emits only legs whose record is ``None``.

**One fold PREDICATE did change at 6-1, and it is pinned rather than
denied**: sharing the join gave :func:`planned_transfer_legs`' ``dated_leg``
test the term ``the shadow is live`` (``Transaction.is_deleted IS FALSE``)
that it did not carry before -- the term the settled half's
``balance_contributing_clause`` has always applied to the same movement.
On every door-written state the two predicates agree (no door soft-deletes
one shadow alone); on the double drift -- a dated movement under a shadow
deleted around the service, the parent still Projected -- the leg used to
vanish from both halves and is now counted once, by the plan (R-JA: the
parent decides).  ``tests/test_services/test_transfer_legs.py``'s drift
class pins it, red under the old predicate.

**A leg's LABEL is composed here** (:func:`leg_label`), from the endpoints'
CURRENT names: "Transfer to <to-account>" on the from-side, "Transfer from
<from-account>" on the to-side.  It is the one composition the shadow
constructor (``transfer_service._create.shadow_names``) and the grid's row
label read, so a renamed account re-labels every leg it touches where a
shadow's stored ``name`` went stale -- one of the two visible changes 6-1
makes.  The other: a leg's ``notes`` are its PARENT's (a shadow was written
with none and no writer mirrored them), so a transfer's notes show on its
grid cell's title where the shadow's showed nothing.

**Why a leaf module, below both readers.**  The cash ledger needs every leg an
account is on (both sides, for its cash fold); the loan loaders need the legs
INTO a loan (its projected payments); and ``cash_ledger`` imports
``loan_loaders`` for its loan term primitives, so the loader cannot live in
the cash ledger without closing that cycle -- ``cyclic-import`` traces a
call-time import too.  A leaf both tiers reach is the shape
:mod:`app.services.row_valuation` and :mod:`app.utils.amount_relationships`
already take, for the same reason.  It names four models, the shared status
predicates and the amount model's eager-load leaf, and no service.

**A database VIEW for this pair was refuted at the ruling**: a derive-mode loan
payment's leg cannot be priced without the amortization engine, so the pair
is Python.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01).  Plain data
in, frozen dataclasses out; no Flask symbol, no writes, no clock.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.ref import Status
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.utils.balance_predicates import is_projected_clause
from app.utils.dates import days_paid_before_due

#: The two label prefixes :func:`leg_label` composes with.  Published so the
#: one reader that PARSES a label back out (``grid_view_service
#: ._short_display_name``, the grid's row label) strips exactly what was
#: composed rather than a literal of its own -- the coupling
#: ``transfer_service._create.shadow_names`` reported and left for this leaf.
TRANSFER_TO_PREFIX = "Transfer to "
TRANSFER_FROM_PREFIX = "Transfer from "


def leg_label(from_account: Account, to_account: Account) -> tuple[str, str]:
    """Return the ``(expense, income)`` leg labels for two endpoints.

    ONE composition: "Transfer to <to-account>" is what the from-side shows
    (its money leaves for that account), "Transfer from <from-account>" what
    the to-side shows.  :attr:`TransferLeg.name` reads it off the parent's
    endpoints at render time; ``transfer_service._create.shadow_names`` writes
    the same pair onto the two shadow rows for as long as those rows exist.

    Args:
        from_account: The source account, the expense leg's.
        to_account: The destination account, the income leg's.

    Returns:
        ``(expense_label, income_label)``.
    """
    return (
        f"{TRANSFER_TO_PREFIX}{to_account.name}",
        f"{TRANSFER_FROM_PREFIX}{from_account.name}",
    )


@dataclass(frozen=True)
class TransferLeg:
    """One side of a transfer, as the account on that side sees it.

    The value every plan reader folds in place of the shadow row it used to
    load, and since leaf ``X-bi-6-1`` the value the grid draws in a
    transfer's cell.  It is a PROJECTION of the parent, so the parent is the
    only thing it stores: the two derived fields say which account is looking
    and from which side, and everything else is read off the row.  The
    display-facing properties below (``name``, ``status``, ``category`` and
    their kin) are the parent's columns read through the leg, so a template
    that draws a row and a leg asks each the same question; a leg that CARRIED
    any of them would be a shadow row in memory.

    Attributes:
        transfer: The parent :class:`~app.models.transfer.Transfer`.  Its
            ``status_id``, ``pay_period_id``, ``due_date``, ``scenario_id`` and
            ``is_deleted`` are the leg's; its amount is what
            :func:`app.services.cash_ledger.resolve_transfer_amount` answers.
        account_id: The account this leg is on -- the parent's
            ``from_account_id`` or ``to_account_id``.
        is_income: ``True`` on the to-side (money arrives), ``False`` on the
            from-side (money leaves).  The same split the transfer service
            writes as the two shadows' transaction TYPES, stated here from
            the columns that decide it rather than copied.
        record: The leg's COVERING MOVEMENT
            (:class:`~app.models.transaction_entry.TransactionEntry`,
            ``covers_settlement``) when one exists: what this side's money DID,
            written by the status seam at the settle, dated when the money
            moved and kept un-dated across a revert (ruling **R-BAL61**).
            ``None`` when the leg has none -- or when the loader did not ask:
            the fold's :func:`planned_transfer_legs` emits legs whose DATED
            movement does not exist and never loads one (ruling **R-BAL79**
            decides the relation by the movement in SQL), while the grid's
            :func:`grid_transfer_legs` loads every leg's through
            :func:`covering_movements_by_leg`.
    """

    transfer: Transfer
    account_id: int
    is_income: bool
    record: TransactionEntry | None = None

    @property
    def is_expense(self) -> bool:
        """``True`` on the from-side: the reduction's other arm."""
        return not self.is_income

    @property
    def cell_key(self) -> tuple[int, int]:
        """The leg's identity on a grid: ``(transfer id, account id)``.

        The key every per-cell map the grid publishes (``budgets``,
        ``settled``, ``retained``, ``due_captions``) holds a leg under, beside
        the plain rows' own ``id`` -- a tuple cannot collide with an ``int``,
        so one map serves both shapes.  Ruling **R-BAL87**.
        """
        return (self.transfer.id, self.account_id)

    @property
    def account(self) -> Account:
        """The account this leg is on, as a row: the parent's endpoint."""
        return (
            self.transfer.to_account if self.is_income
            else self.transfer.from_account
        )

    @property
    def name(self) -> str:
        """The leg's label, composed from the endpoints' CURRENT names."""
        expense, income = leg_label(
            self.transfer.from_account, self.transfer.to_account,
        )
        return income if self.is_income else expense

    @property
    def status_id(self) -> int:
        """The parent's status: a transfer's status lives in ONE row."""
        return self.transfer.status_id

    @property
    def status(self) -> Status:
        """The parent's :class:`~app.models.ref.Status` row."""
        return self.transfer.status

    @property
    def category_id(self) -> int | None:
        """The parent's category, which is where the grid files the leg."""
        return self.transfer.category_id

    @property
    def category(self) -> Category | None:
        """The parent's :class:`~app.models.category.Category` row."""
        return self.transfer.category

    @property
    def scenario_id(self) -> int:
        """The parent's scenario."""
        return self.transfer.scenario_id

    @property
    def is_deleted(self) -> bool:
        """The parent's soft-delete flag: a leg cannot be deleted alone."""
        return self.transfer.is_deleted

    @property
    def is_override(self) -> bool:
        """The parent's override flag (the pencil marker on a grid cell)."""
        return self.transfer.is_override

    @property
    def notes(self) -> str | None:
        """The parent's notes: a leg has none of its own."""
        return self.transfer.notes

    @property
    def template_id(self) -> None:
        """``None``: a leg names no transaction definition.

        The grid's row-key contract (``grid_view_service.build_row_keys``)
        asks every item it files whether a recurring definition generated it;
        a transfer's rule is its ``transfer_template_id``, which is not a row
        key on the transaction grid, so a leg answers as a one-off does.
        """
        return None

    @property
    def recurs(self) -> bool:
        """``False``: see :attr:`template_id`."""
        return False


    @property
    def pay_period_id(self) -> int:
        """The period the parent is filed in -- the leg's budget column."""
        return self.transfer.pay_period_id

    @property
    def pay_period(self) -> PayPeriod:
        """The parent's :class:`~app.models.pay_period.PayPeriod` row."""
        return self.transfer.pay_period

    @property
    def due_date(self) -> date | None:
        """The parent's due date, or ``None`` for an undated transfer."""
        return self.transfer.due_date

    @property
    def tracks_purchases(self) -> bool:
        """``False``: a transfer's leg is not an envelope.

        The question the dashboard's bill surfaces ask of every item they
        price (``dashboard_service._bills``, ``_pulse._row_still_due``), stated
        here from what a leg IS rather than answered by a branch at each of
        those sites (leaf ``X-bi-6-1b``).  A shadow row answered the same
        ``False`` through ``Transaction.tracks_purchases``' link-less arm
        (ruling **R-BAL73**).  Every reader of a row's ``purchases`` asks
        this first, so a leg needs no ``purchases`` of its own.
        """
        return False

    @property
    def settled_on(self) -> date | None:
        """The civil day this leg's money moved, or ``None``.

        Read off the leg's RECORD -- its covering movement's ``settled_on``
        (ruling **R-BAL80**: a settled leg's money is its dated movement) --
        and never off the parent or a shadow row: a settled leg carries a
        dated movement (ruling **R-BAL79**), a reverted one a movement kept
        un-dated, a planned one none, so this reads ``None`` exactly when the
        money has not moved.  What ``Transaction.settled_on`` states as a
        column for a row, for the readers that ask both shapes when a bill
        was paid (``spending_analysis.payment_timeliness_from_txns``).
        """
        if self.record is None:
            return None
        return self.record.settled_on

    @property
    def days_paid_before_due(self) -> int | None:
        """Days between the due date and the day the money moved, or ``None``.

        :func:`app.utils.dates.days_paid_before_due` over this leg's two
        days -- the ONE arithmetic ``Transaction.days_paid_before_due`` also
        calls.
        """
        return days_paid_before_due(self.due_date, self.settled_on)


#: A plan item as the display readers hold one: a plan row, or one side of a
#: transfer read off its parent.  The grid's window, the dashboard's bills,
#: the calendar's day cells and the Spending report's settled spend are each a
#: list of these since leaves ``X-bi-6-1`` and ``X-bi-6-1b``.
PlanItem = Transaction | TransferLeg


def cell_key(item: PlanItem):
    """Return the key a surface publishes *item*'s per-item facts under.

    **The ONE place a row and a leg are told apart for identity** (leaf
    ``X-bi-6-1``, ruling **R-BAL87**): a plan row is keyed by its ``id`` and
    a transfer leg by :attr:`TransferLeg.cell_key`, the ``(transfer id,
    account id)`` pair.  An ``int`` and a tuple cannot collide, so a page's
    ``budgets`` / ``settled`` / ``retained`` / ``due_captions`` maps, the
    dashboard's ``contributions`` and the calendar's hold both shapes in one
    dict and every reader subscripts them with the same expression -- the
    Jinja global of the same name (``app.jinja_filters``) is this function.
    It lived in ``grid_view_service`` until leaf ``X-bi-6-1b`` gave it three
    readers below the grid; the leaf both shapes are defined against is its
    home.

    Args:
        item: A row or a leg.

    Returns:
        ``item.id`` for a row, ``item.cell_key`` for a leg.
    """
    if isinstance(item, TransferLeg):
        return item.cell_key
    return item.id


def key_order(key) -> tuple[int, int, int]:
    """Return a TOTAL order over :func:`cell_key` values, for a mixed sort.

    A row's key is an ``int`` and a leg's a pair, and Python refuses to
    compare the two, so a reader that ranks rows and legs together on their
    identity -- the Spending report's surprises, whose cap follows a rank
    that must be a function of the data (finding **P74**) -- orders by this:
    every row before every leg, rows by id, legs by ``(transfer id, account
    id)``.  The ONE spelling of that order (leaf ``X-bi-6-1b``).

    Args:
        key: A :func:`cell_key` value.

    Returns:
        A three-int tuple that sorts as stated.
    """
    if isinstance(key, tuple):
        return (1, key[0], key[1])
    return (0, key, 0)


def leg_of(
    transfer: Transfer, account_id: int, *,
    record: TransactionEntry | None = None,
) -> TransferLeg:
    """Return *transfer*'s leg on *account_id*, refusing an account on neither side.

    The ONE construction of a leg, so which side is income is decided in one
    place.  ``ck_transfers_different_accounts`` makes the two sides distinct,
    so an account is on at most one of them and the answer is never ambiguous.

    Args:
        transfer: The parent transfer.
        account_id: The account asking for its leg.
        record: The leg's covering movement, when the caller loaded one (the
            grid); the fold's callers pass none.  See
            :attr:`TransferLeg.record`.

    Returns:
        The :class:`TransferLeg` on that side.

    Raises:
        ValueError: When *account_id* is neither side of *transfer*.  A
            reader that asks for a leg an account is not on has paired a
            transfer with the wrong account, and a wrong answer here is money
            on the wrong balance.
    """
    if account_id == transfer.to_account_id:
        return TransferLeg(
            transfer=transfer, account_id=account_id, is_income=True,
            record=record,
        )
    if account_id == transfer.from_account_id:
        return TransferLeg(
            transfer=transfer, account_id=account_id, is_income=False,
            record=record,
        )
    raise ValueError(
        f"account {account_id} is on neither side of transfer {transfer.id} "
        f"(from {transfer.from_account_id} to {transfer.to_account_id}): "
        "there is no leg of it for that account to fold"
    )


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
    # account under one of the transfer's shadows (ruling **R-BAL79**).  A
    # correlated EXISTS rather than a join, so a transfer is one row here
    # whatever its shadows hold.
    dated_leg = (
        _covering_movements_query()
        .filter(
            Transaction.transfer_id == Transfer.id,
            TransactionEntry.account_id == account_id,
            TransactionEntry.settled_on.isnot(None),
        )
        .with_entities(TransactionEntry.id)
        .correlate(Transfer)
        .exists()
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
    it from the parent walks ``transactions.transfer_id``.  Both readers of a
    leg's record build on this -- the fold's ``dated_leg`` predicate above and
    the grid's :func:`covering_movements_by_leg` -- so when the movement
    re-parents onto ``budget.transfers`` the join moves HERE, once.

    A deleted shadow's movement is not a leg's record: a transfer whose pair
    was soft-deleted and rebuilt holds the live pair's, and the query says so
    rather than leaving it to the caller.

    Returns:
        A query rooted at :class:`~app.models.transaction_entry.TransactionEntry`
        with the shadow joined -- built, not executed, and not yet narrowed
        to any transfer.
    """
    return (
        db.session.query(TransactionEntry)
        .join(Transaction, TransactionEntry.transaction_id == Transaction.id)
        .filter(
            Transaction.is_deleted.is_(False),
            TransactionEntry.covers_settlement.is_(True),
        )
    )


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
    movements = (
        _covering_movements_query()
        .filter(Transaction.transfer_id.in_(ids))
        .options(selectinload(TransactionEntry.transaction))
        .all()
    )
    return {
        (movement.transaction.transfer_id, movement.account_id): movement
        for movement in movements
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
