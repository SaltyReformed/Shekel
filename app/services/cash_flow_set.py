"""
Shekel Budget App -- the owner's CASH-FLOW SET: checking and its cards.

The value that answers "whose plan items are this paycheck's rows" (developer
ruling ``credit_card:R-CC16``, 2026-09-18; design
``docs/design/credit_card_from_scratch.md`` 3.3, plan step CC-4-1).  A plan
item's ``account_id`` is the ONE account its money is expected to move through
-- the phone bill that is always paid by card is a row ON the card -- so every
reader of a paycheck's plan items (the budget grid, the dashboard's upcoming
bills, the spending report, the calendar) reads the owner's cash-flow accounts
as ONE SET rather than one account.  The set is the owner's primary grid
account plus their active revolving accounts; loans, investments and savings
stay out.  ``account_resolver.is_cash_flow_account`` is only "not amortizing"
and admits the IRAs and the savings accounts, which is why the set is a
predicate of its own rather than that one.

**A balance line is still ONE account's.**  The grid's Projected End Balance,
the calendar's end-of-day line, the dashboard's hero: each is the balance of
:attr:`CashFlowSet.balance`, a member of the set (the primary by default; an
override within the set as the grid has always allowed).  The rows are the
set's.  An override naming an account OUTSIDE the set -- a savings account --
keeps that account's single-account view, exactly as before this step: the set
is then that one account.

**A transfer between two members shows ONCE, from the balance line's side**
(developer ruling ``credit_card:R-CC23``, 2026-09-18).  Every transfer is two
shadow rows, one per endpoint, and a single-account reader saw one of them by
construction.  Widening to the set would load BOTH shadows of a checking ->
card payment, and the card's INCOME shadow would render as a paycheck income
row with its figure in Total Income -- the owner's own transfer counted as
income.  So the far leg -- a shadow on a non-balance member whose transfer's
OTHER endpoint is also a member -- is not a paycheck row.  A transfer with only
one endpoint in the set (savings -> card) still shows from that endpoint, as a
checking -> savings transfer always has.  Ruled against: showing both sides
(Total Income carries the transfer); hiding EVERY transfer row on a non-balance
member (hides a real planned act rather than a second view of one).  Worked on
the ruling's example -- phone ``$45`` on the card, grocery ``$500`` on checking,
a ``$165`` checking -> card payment in the same paycheck: Total Income ``$0``,
Total Expenses ``$710``, Net Cash Flow ``-$710``, "On other accounts" ``+$45``,
checking's balance moves ``-$665``.

The rule has ONE spelling of its subject -- :func:`_intra_set_transfers`, the
transfers with both endpoints in the set -- and two readers of it: the row
loads (:func:`paycheck_rows_clause`, through :func:`far_leg_clause`) and the
balance seam's composed subtotal (:func:`far_legs_of`), so the cells a reader
draws and the subtotal the seam sums cannot disagree about which rows are the
paycheck's.

Services boundary (``CLAUDE.md``): no Flask symbol.  The clauses are built,
never executed, here; :func:`far_legs_of` holds the seam's two queries.
"""

from dataclasses import dataclass

from sqlalchemy import and_, not_, or_

from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.utils.balance_predicates import settled_status_ids


@dataclass(frozen=True)
class CashFlowSet:
    """The owner's cash-flow accounts, and which one's balance line renders.

    Built by :func:`app.services.account_resolver.resolve_cash_flow_set`; read
    by every plan-item reader and by the balance seam's grid view.  Frozen and
    total: :attr:`balance` is always a member of :attr:`members`, which is
    never empty.

    Attributes:
        balance: The account whose BALANCE the surface renders -- the primary
            grid account by default, or the override the request named.
        members: Every account whose plan items are the paycheck's rows, in
            picker order: the PRIMARY first, then the owner's active cards by
            ``sort_order`` then ``id``.  The balance account is a member and
            not necessarily the first -- an override naming a card puts the
            card on the line and leaves the order alone.  ONE account when
            the owner has no active card, or when the balance line is an
            account outside the set.
    """

    balance: Account
    members: tuple[Account, ...]

    def __post_init__(self):
        """Refuse a balance line that is not a member: the type's one invariant."""
        if not any(m.id == self.balance.id for m in self.members):
            raise ValueError(
                "the balance account must be a member of the set: "
                f"balance={self.balance.id} members="
                f"{[m.id for m in self.members]}"
            )

    @property
    def member_ids(self) -> tuple[int, ...]:
        """The members' ``budget.accounts.id`` values, in member order."""
        return tuple(m.id for m in self.members)

    @property
    def others(self) -> tuple[Account, ...]:
        """Every member but the balance account, in member order."""
        return tuple(m for m in self.members if m.id != self.balance.id)

    @classmethod
    def single(cls, account: Account) -> "CashFlowSet":
        """The set of ONE account: its own rows behind its own balance line.

        What every single-account reader of the grid view asks for -- the
        cross-page equality tests, the anchor surfaces, the balance baseline
        harness -- and the shape the resolver answers for an owner with no
        card or for a balance line outside the set.  A legitimate set, not a
        compatibility shim: ``elsewhere`` is ``0.00`` in every column and the
        subtotals are the account's own.
        """
        return cls(balance=account, members=(account,))


def _intra_set_transfers(cash_flow: CashFlowSet):
    """Return the query of transfer ids with BOTH endpoints in the set.

    The ONE spelling of the rule's subject.  Used as a subquery by
    :func:`far_leg_clause` and executed by :func:`far_legs_of`; a member's
    endpoints are the owner's accounts, so it can name no other owner's
    transfer.

    Args:
        cash_flow: The set.

    Returns:
        A query of ``budget.transfers.id`` -- built, not executed.
    """
    member_ids = cash_flow.member_ids
    return (
        db.session.query(Transfer.id)
        .filter(
            Transfer.from_account_id.in_(member_ids),
            Transfer.to_account_id.in_(member_ids),
        )
    )


def far_leg_clause(cash_flow: CashFlowSet):
    """Return the clause matching a transfer's FAR leg (ruling **R-CC23**).

    The ONE spelling of the rule stated in the module docstring: a row on a
    member other than the balance account whose transfer has both endpoints in
    the set.  Such a row is the second view of an act the balance line's side
    already shows, so it is not a paycheck row.

    **It is meaningful only under two conjuncts the callers supply**: the row
    is on a member (``Transaction.account_id IN members``) and it is a shadow
    (``Transaction.transfer_id IS NOT NULL``).  Under those, the clause is TRUE
    or FALSE and never SQL-NULL -- ``account_id`` is NOT NULL and an ``IN`` over
    a primary-key subquery with a non-null left operand is two-valued -- which
    is what lets :func:`paycheck_rows_clause` negate it safely.  A negation
    written without the ``IS NULL`` guard would evaluate to NULL for every
    ordinary row and drop it from the load: SQL's three-valued logic, stated
    here because it is the one way this rule fails silently.

    Args:
        cash_flow: The set.

    Returns:
        A SQLAlchemy boolean clause over :class:`Transaction`.
    """
    return and_(
        Transaction.account_id != cash_flow.balance.id,
        Transaction.transfer_id.in_(_intra_set_transfers(cash_flow)),
    )


def paycheck_rows_clause(cash_flow: CashFlowSet):
    """Return the clause selecting the paycheck's plan items across the set.

    ``Transaction.account_id IN members``, less the far leg of every
    intra-set transfer (:func:`far_leg_clause`).  Every reader of a paycheck's
    plan items appends THIS in place of the ``Transaction.account_id ==
    account_id`` filter it carried, so the rule the readers share is the
    clause rather than a sentence each keeps in step.

    A one-member set -- no cards, or a balance line outside the set -- reduces
    to the old single-account filter: the far-leg arm cannot match, because a
    transfer needs two distinct endpoints
    (``ck_transfers_different_accounts``) and the set holds one.  It is written
    without a branch on the member count so there is one clause, not two
    spellings that agree on the common case.

    Args:
        cash_flow: The set.

    Returns:
        A SQLAlchemy boolean clause over :class:`Transaction`.
    """
    return and_(
        Transaction.account_id.in_(cash_flow.member_ids),
        or_(
            # The ``IS NULL`` guard :func:`far_leg_clause` documents: an
            # ordinary row is not a shadow, and the negated arm must not be
            # asked about it.
            Transaction.transfer_id.is_(None),
            not_(far_leg_clause(cash_flow)),
        ),
    )


@dataclass(frozen=True)
class FarLegs:
    """The far legs of the set's intra-set transfers, by both identities.

    What the balance seam needs to keep its composed subtotal in step with the
    rows :func:`paycheck_rows_clause` loads.  A member's budget legs
    (:func:`~app.services.balance_at._cash_periods._budget_legs`) hold a
    transfer two ways, and each is excluded by its own key:

    * a STILL-PROJECTED transfer is a
      :class:`~app.services.transfer_legs.PlannedTransferLeg` derived from
      ``budget.transfers`` (plan step X-bi-6a), keyed here by its TRANSFER's
      id -- and for a leg on a NON-balance member, "far" is exactly "its
      transfer has both endpoints in the set", so :attr:`transfer_ids` is
      every intra-set transfer, read off ``budget.transfers`` alone;
    * a SETTLED transfer is a :class:`~app.services.cash_ledger.CashSourceFact`
      keyed by its SHADOW's ``transaction_id`` (the shadow's own fact and its
      covering movement's both carry it), so :attr:`transaction_ids` is read
      off the settled shadow rows -- the record half Transfer Invariant 5
      admits a balance reader to read until ``X-bi-4`` re-keys it.

    **No projected shadow row is read** (Transfer Invariant 5: "no balance
    reader reads a projected shadow row").  The planned half needs none, and
    the settled half filters on the settled statuses, so when ``X-bi-6``
    deletes the projected shadows nothing here empties.

    Attributes:
        transaction_ids: The settled far-leg shadows' ``budget.transactions.id``.
        transfer_ids: Every intra-set transfer's ``budget.transfers.id``.
    """

    transaction_ids: frozenset[int]
    transfer_ids: frozenset[int]

    @classmethod
    def none(cls) -> "FarLegs":
        """The empty answer, for a set with nothing to exclude."""
        return cls(transaction_ids=frozenset(), transfer_ids=frozenset())


def far_legs_of(cash_flow: CashFlowSet) -> FarLegs:
    """Return the set's far legs -- two queries, for the balance seam.

    Issues nothing for a one-member set: it has no other member to hold a far
    leg, so the answer is empty by construction rather than by a round trip.
    Neither query filters on scenario or soft-deletion, on purpose: the
    seam's walk and plan are scenario-scoped and read live rows only, so an
    id here that names a row they never hold excludes nothing, and a filter
    would be a second statement of "which rows are live" beside theirs.

    Args:
        cash_flow: The set.

    Returns:
        The :class:`FarLegs`.
    """
    other_ids = tuple(a.id for a in cash_flow.others)
    if not other_ids:
        return FarLegs.none()
    transfer_ids = frozenset(
        row.id for row in _intra_set_transfers(cash_flow).all()
    )
    if not transfer_ids:
        return FarLegs.none()
    settled_shadows = (
        db.session.query(Transaction.id)
        .filter(
            Transaction.account_id.in_(other_ids),
            Transaction.transfer_id.in_(transfer_ids),
            Transaction.status_id.in_(settled_status_ids()),
        )
        .all()
    )
    return FarLegs(
        transaction_ids=frozenset(row.id for row in settled_shadows),
        transfer_ids=transfer_ids,
    )
