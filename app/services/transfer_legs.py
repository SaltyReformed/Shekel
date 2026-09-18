"""
Shekel Budget App -- A projected transfer's two LEGS, derived from its parent.

The PLAN half of ruling **R-BAL13** (plan step **X-bi-6a**): a transfer's two
legs are not rows in ``budget.transactions``.  A still-projected transfer is
ONE economic event, and what each account it touches will see is a
PROJECTION of the parent row -- the from-side an expense, the to-side an
income, each worth exactly what the parent resolves to.  This module states
that projection ONCE, as a value (:class:`PlannedTransferLeg`) and the loader
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
figure, no period and no date of its own: :attr:`~PlannedTransferLeg.due_date`
and :attr:`~PlannedTransferLeg.pay_period_id` read the parent's columns, and
what the leg is WORTH is
:func:`app.services.cash_ledger.resolve_transfer_amount` over the parent --
the ONE producer ruling **R-BAL10** put a transfer's amount on.  A leg that
held a copy of any of those would be a shadow row in memory, the very shape
this step exists to stop reading.

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

from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.utils.balance_predicates import is_projected_clause


@dataclass(frozen=True)
class PlannedTransferLeg:
    """One side of a still-projected transfer, as the account on that side sees it.

    The value every plan reader folds in place of the shadow row it used to
    load.  It is a PROJECTION of the parent, so the parent is the only thing
    it stores: the two derived fields say which account is looking and from
    which side, and everything else is read off the row.

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
    """

    transfer: Transfer
    account_id: int
    is_income: bool

    @property
    def is_expense(self) -> bool:
        """``True`` on the from-side: the reduction's other arm."""
        return not self.is_income

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


def leg_of(transfer: Transfer, account_id: int) -> PlannedTransferLeg:
    """Return *transfer*'s leg on *account_id*, refusing an account on neither side.

    The ONE construction of a leg, so which side is income is decided in one
    place.  ``ck_transfers_different_accounts`` makes the two sides distinct,
    so an account is on at most one of them and the answer is never ambiguous.

    Args:
        transfer: The parent transfer.
        account_id: The account asking for its leg.

    Returns:
        The :class:`PlannedTransferLeg` on that side.

    Raises:
        ValueError: When *account_id* is neither side of *transfer*.  A
            reader that asks for a leg an account is not on has paired a
            transfer with the wrong account, and a wrong answer here is money
            on the wrong balance.
    """
    if account_id == transfer.to_account_id:
        return PlannedTransferLeg(
            transfer=transfer, account_id=account_id, is_income=True,
        )
    if account_id == transfer.from_account_id:
        return PlannedTransferLeg(
            transfer=transfer, account_id=account_id, is_income=False,
        )
    raise ValueError(
        f"account {account_id} is on neither side of transfer {transfer.id} "
        f"(from {transfer.from_account_id} to {transfer.to_account_id}): "
        "there is no leg of it for that account to fold"
    )


def planned_transfer_legs(
    account_id: int, scenario_id: int, *, options: tuple,
) -> list[PlannedTransferLeg]:
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
        One :class:`PlannedTransferLeg` per matching transfer whose leg on
        this account is not yet a dated movement, unordered; ``[]`` for an
        account no still-projected transfer touches.
    """
    # The leg's RECORD, when it exists: a dated covering movement on this
    # account under one of the transfer's shadows (ruling **R-BAL79**).  A
    # correlated EXISTS rather than a join, so a transfer is one row here
    # whatever its shadows hold.
    dated_leg = (
        db.session.query(TransactionEntry.id)
        .join(Transaction, TransactionEntry.transaction_id == Transaction.id)
        .filter(
            Transaction.transfer_id == Transfer.id,
            TransactionEntry.account_id == account_id,
            TransactionEntry.covers_settlement.is_(True),
            TransactionEntry.settled_on.isnot(None),
        )
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
