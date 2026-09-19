"""
Shekel Budget App -- The RECORDED contributions into investment accounts.

The transfer-linked contribution feed the investment, retirement and savings
projections read, PRICED and DATED at this boundary (plan step X-au-c2, a
developer ruling of 2026-08-12; pay-calendar plan step C2-f2c).  It lived in
:mod:`app.services.projection_inputs` until plan step **balance:X-bi-6a**
pushed that module past the 1000-line ceiling, and it moved rather than that
module shaving: a feed with a valuation rule of its own lives beside the
orchestration that assembles the engine's inputs, not inside it -- the same
argument ``transfer_service._posting_sync`` was split out under.

**Two relations since X-bi-6a** (ruling **R-BAL13**, developer ruling
**R-BAL38**).  A contribution that has SETTLED is its income-shadow row in
``budget.transactions``, worth what it recorded; one still PROJECTED is a leg
of its parent transfer in ``budget.transfers``, worth what the parent resolves
to (:func:`app.services.cash_ledger.planned_leg_contribution`).  Which
relation a record came from is what ``is_confirmed`` says, so no second
reading of the status column decides it.

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"): no
Flask symbol; plain data in, the
:class:`~app.services.investment_projection.ShadowContributions` DTO out.  It
is here and not in :mod:`app.services.investment_projection` because that
module promises no database access, and this one is a session read.
"""

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.cash_ledger import (
    AmountBasis,
    contributions_by_id,
    planned_leg_contribution,
    settlement_load_options,
    transfer_pricing_load_options,
)
from app.services.investment_projection import (
    PricedContribution,
    ShadowContributions,
)
from app.services.transfer_legs import leg_of
from app.utils.balance_predicates import is_projected, settled_status_ids


def load_shadow_income_contributions_for_accounts(
    basis: AmountBasis,
    account_ids: list[int], period_ids: list[int],
) -> ShadowContributions:
    """Return PRICED shadow-income contributions across many accounts.

    Batch variant used by services that classify many accounts in one
    pass.  Returned records carry their original ``account_id`` so callers
    can group / partition downstream.  Returns an empty list when
    either ``account_ids`` or ``period_ids`` is empty so callers do
    not issue ``IN ()`` queries against PostgreSQL.

    **This is the BOUNDARY where a contribution is valued** (plan step
    X-au-c2, a developer ruling of 2026-08-12).  It used to return ORM rows and
    four readers in :mod:`app.services.investment_projection` each asked them
    for ``effective_amount`` behind its own copy of the
    ``status_contributes_to_balance`` screen.  That property cannot answer for
    a row whose amount is DERIVED -- such a row stores no figure -- and a module
    whose docstring promises no database access can never resolve one.  So the
    resolution happens HERE, where the session is: ONE
    :func:`~app.services.cash_ledger.contributions_by_id` call over the whole
    cross-account row set, which is also one paycheck-engine run rather than
    one per account (finding **N-228**, and what re-keying the basis on the
    OWNER rather than an ``Account`` bought).

    **It is also where a contribution is DATED, since plan step C2-f2c**, and
    that is the same argument applied to the same record's other derived fact.
    Every reader downstream buckets contributions by pay period and then needs
    that period's PAYDAY -- the YTD windows to compare it against the current
    period's, the timeline to stamp it on a
    :class:`~app.services.growth_engine.ContributionRecord`.  Carrying the id
    alone made each of them take the owner's whole period list as a lookup
    table, so three public signatures held a join this query can do in one
    ``JOIN``.  ``PayPeriod.start_date`` is the paydays' own column and the one
    plan step **C4** keeps, so this reads a fact rather than a derivation; the
    join is INNER, which drops nothing, because the filter below already
    excludes a ``NULL`` ``pay_period_id``.

    **Rows that contribute nothing are DROPPED rather than priced at zero.**
    :func:`~app.services.investment_projection._inputs._average_transfer_contribution`
    divides by the number of distinct pay periods it sees, so a Cancelled
    contribution carried through as ``$0.00`` would enlarge that denominator and
    silently lower the average.  The screen is applied before the pricing for
    the same reason the valuation gates before it resolves: an excluded row has
    no derived answer to give.

    **It reads TWO relations since plan step balance:X-bi-6a** (ruling
    **R-BAL13**), and the screen above is now the SQL narrowing of each.  A
    contribution that has SETTLED is its income-shadow row, loaded in the
    settled statuses and worth what it recorded; one still PROJECTED is a leg
    of its parent transfer INTO the account, loaded from ``budget.transfers``
    and worth what the parent resolves to
    (:func:`~app.services.cash_ledger.planned_leg_contribution`).  Neither
    load admits a Credit or Cancelled row, so the Python screen this used to
    apply has nothing left to drop; ``is_confirmed`` is decided by WHICH
    relation a record came from rather than by a second reading of the status
    column.  The transfers are loaded in EVERY status, because the link set
    below needs them; only the still-Projected ones become records.

    The ``eager_status`` switch is gone with them.  It defaulted to ``False``
    while every consumer needed the status, so the retirement chain lazy-loaded
    it per row; the status is now read exactly once here, under a ``joinedload``
    that is no longer optional.

    Args:
        basis: The read pass's
            :class:`~app.services.cash_ledger.AmountBasis` -- the owner and the
            scenario these amounts resolve under, and the derivations they
            resolve through.  Taken rather than built here since plan step
            X-au-c2b, so a caller that also prices its own rows pays for the
            paycheck engine once (findings **N-268**, **N-269**).
        account_ids: Investment / retirement account ids to scope to.
        period_ids: Pay-period ids to scope the contribution window
            against.

    Returns:
        A :class:`~app.services.investment_projection.ShadowContributions` --
        the priced ``records`` (callers partition by ``account_id``
        themselves, typically a comprehension inside a per-account loop) and
        the ``linked_account_ids`` of every account that has a transfer INTO
        it in the window WHATEVER its status.  The second field is not
        decoration: an adversarial review found that screening the records
        alone flipped ``retirement_projection``'s ``none_linked`` for an
        account whose contributions were all Cancelled, telling the owner to
        link a contribution that already exists.  It is read off the PARENT
        transfers since plan step balance:X-bi-6a -- every transfer has its
        income shadow and every income shadow its transfer (Transfer
        Invariants 1 and 2), so the two readings are one set, and the parent
        is the one that survives ``X-bi-6``.

    Raises:
        AmountUnresolvable: From the amount model, for a contribution whose
            rule cannot price it.  A refusal is never a fallback.
    """
    if not account_ids or not period_ids:
        return ShadowContributions(records=[], linked_account_ids=frozenset())
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    settled = (
        db.session.query(Transaction, PayPeriod.start_date)
        .join(PayPeriod, PayPeriod.id == Transaction.pay_period_id)
        .options(
            joinedload(Transaction.status),
            # A settled shadow is valued from its record -- its ENTRIES since
            # plan step balance:X-bi-4b-1 (``contributions_by_id`` reaches
            # ``row_valuation.settled_figure``).
            *settlement_load_options(),
        )
        .filter(
            Transaction.account_id.in_(account_ids),
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
            Transaction.pay_period_id.in_(period_ids),
            # The BASIS's scenario, and closing finding **N-271** is what this
            # line is (plan step X-au-c2b, after an adversarial review).  The
            # query scoped by account, transfer, income type, period and soft
            # delete only, so one batch could straddle scenarios while every
            # row in it was priced against a single baseline basis.  That was
            # `$0.00` while every row is OWN and becomes a wrong figure at the
            # first cutover that makes a contribution shadow derived.  Scoping
            # the query is the remedy the row named, and it is the one that
            # keeps this batch's rows and its pricing in agreement by
            # construction rather than by the caller's care.
            Transaction.scenario_id == basis.scenario_id,
            Transaction.is_deleted.is_(False),
            # The RECORD half: the settled statuses, stated positively through
            # the one shared accessor, so a row here is worth what it recorded.
            Transaction.status_id.in_(settled_status_ids()),
        )
        .all()
    )
    # The PLAN half and the LINK set, off ONE load of the parents (plan step
    # balance:X-bi-6a): every live transfer into these accounts in the window,
    # in any status.  A projected one becomes a record; every one is a link.
    transfers = (
        db.session.query(Transfer)
        .options(*transfer_pricing_load_options())
        .filter(
            Transfer.to_account_id.in_(account_ids),
            Transfer.pay_period_id.in_(period_ids),
            Transfer.scenario_id == basis.scenario_id,
            Transfer.is_deleted.is_(False),
        )
        .all()
    )
    amounts = contributions_by_id([row for row, _ in settled], basis)
    records = [
        PricedContribution(
            account_id=row.account_id,
            payday=payday,
            amount=amounts[row.id],
            is_confirmed=True,
        )
        for row, payday in settled
    ]
    for transfer in transfers:
        if not is_projected(transfer):
            continue
        leg = leg_of(transfer, transfer.to_account_id)
        records.append(PricedContribution(
            account_id=leg.account_id,
            payday=leg.pay_period.start_date,
            amount=planned_leg_contribution(leg, basis),
            is_confirmed=False,
        ))
    return ShadowContributions(
        records=records,
        # Taken from EVERY parent loaded, screened by nothing: a Cancelled
        # contribution counts nothing but is still a LINK, and the consumer
        # asking whether an account has one is asking a different question
        # from the consumers that sum amounts.
        linked_account_ids=frozenset(
            transfer.to_account_id for transfer in transfers
        ),
    )


def load_shadow_income_contributions_for_account(
    basis: AmountBasis,
    account_id: int, period_ids: list[int],
) -> ShadowContributions:
    """Return PRICED shadow-income contributions into a single account.

    Used by the investment-detail dashboard.  Filters to
    transfer-shadow income rows in the supplied period window so
    :func:`calculate_investment_inputs` can derive the YTD contribution
    total and the contribution timeline can layer historical receipts.
    Returns an empty list when ``period_ids`` is empty so callers do
    not issue an ``IN ()`` query against PostgreSQL.

    Args:
        basis: The read pass's amount basis (see the batch variant).
        account_id: ID of the investment / retirement account.
        period_ids: Pay-period ids to scope the contribution window
            against.

    Returns:
        A list of :class:`~app.services.investment_projection.PricedContribution`
        records (see the batch variant for what pricing at this boundary buys).
    """
    return load_shadow_income_contributions_for_accounts(
        basis, [account_id], period_ids,
    )
