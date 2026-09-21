"""WHICH rows are an account's payments, and which of them have HAPPENED.

The half of :mod:`app.services.loan_loaders` that answers *which rows*, against
:mod:`._terms`' *what are this loan's contractual facts*.  It owns the
shadow-income predicate, the two producers -- :func:`settled_income_shadows`
and :func:`projected_income_legs`, one per relation since plan step
balance:X-bi-6a -- and the partition value that composes them
(:func:`income_shadows`).

Shadow income is the income-leg shadow of a transfer INTO an account: a
payment received by a loan, or a contribution into an investment account.

**Its two jobs are both rule-14 answers, and plan step balance:X-bl-2a is where
they became one each.**  Settled-ness had two derivations -- this module's
status-id filter and ``loan_payment_service.get_payment_history``'s read of the
``is_settled`` column -- agreeing under a pinned parity; and the amount model's
eager-load set was baked into the query, so a ROW loader decided what its
callers would traverse.  The partition is single now, and the load is the
caller's statement.

**The two halves come from two RELATIONS since plan step balance:X-bi-6a**
(ruling **R-BAL13**, developer ruling **R-BAL38**).  A SETTLED payment is the
shadow row that recorded it, read from ``budget.transactions``; a PROJECTED
payment is a leg DERIVED from its parent row in ``budget.transfers``
(:mod:`app.services.transfer_legs`), because a still-projected transfer's
legs are that parent's projection and not rows of their own.  Settled-ness is
therefore decided by WHICH RELATION a payment came from, and the Python
branch that used to sort one row set into two buckets has one arm left: the
refusal of a shadow in a status that is neither settled nor Projected.

A LEAF: models, the ref cache and the shared balance predicates.
Flask-isolated, reads only, no commits.  Its settled half queries
``budget.transactions`` and its projected half ``budget.transfers`` -- the
RECORD and PLAN halves of Transfer Invariant 5 as restated at X-bi-6a; no
amount is read off a transfer for a payment that has settled.
"""

from dataclasses import dataclass

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services.transfer_legs import (
    TransferLeg,
    planned_transfer_legs,
)
from app.utils.amount_relationships import (
    period_load_option,
    transfer_period_load_option,
)
from app.utils.balance_predicates import (
    balance_excluded_status_ids,
    is_projected_clause,
    settled_status_ids,
)

@dataclass(frozen=True)
class ShadowSets:
    """A loan's income payments, PARTITIONED by whether their cash has moved.

    The project's single answer to "which of this loan's payments have
    happened?", and the reason it is one value rather than two functions is
    :class:`CLAUDE.md` rule 14: settled-ness is one fact, so it is decided in
    one place (:func:`income_shadows`) and read from here.

    **Its two halves are two RELATIONS since plan step balance:X-bi-6a.**
    They were one table partitioned by ``status_id``, which is
    ``docs/design/from_scratch_architecture.md``'s named root cause -- a PLAN
    and a RECORD OF WHAT HAPPENED in one row, with a status column pretending
    the first turns into the second.  The projected half is a select from the
    plan rows now (``budget.transfers``, as derived legs); the settled half
    is still the shadow rows until ``balance:X-bi-4`` makes it a select from
    the movement relation.

    Attributes:
        settled: The payments whose cash has moved -- the settled income
            shadow rows -- ascending by ``(pay_period.start_date, id)``.
        projected: The payments still planned, as one
            :class:`~app.services.transfer_legs.TransferLeg` per
            still-projected transfer INTO the account, ascending by
            ``(pay_period.start_date, transfer id)``.  Disjoint from
            ``settled`` wherever Transfer Invariant 3 holds; under a STATUS
            drift (a settled shadow beneath a still-Projected parent, which
            no door writes) one payment appears in BOTH halves, since each
            half keys on its own relation's status -- pinned in
            ``tests/test_services/test_transfer_legs.py``, re-keyed by
            ``balance:X-bi-4``.
    """

    settled: list[Transaction]
    projected: list[TransferLeg]


def income_shadows(
    account_id: int, scenario_id: int, *, options: tuple, leg_options: tuple,
) -> ShadowSets:
    """Return an account's income payments split into SETTLED and PROJECTED.

    **The single derivation of "which payments are settled, and in what
    order"** (plan step **balance:X-bl-2a**).  Every settled-payment consumer
    reaches this one -- the fold's event stream
    (:func:`app.services.loan_ledger.loan_event_stream`), the ledger's
    per-payment principal reader, the escrow forward-only guard, the resolver's
    payment feed -- so no two of them can classify a payment differently.  It was
    two narrowed queries and a third rule in
    ``loan_payment_service.get_payment_history``, which read ``txn.status.is_settled``
    where these filtered on ``settled_status_ids()``; the two agreed under a
    pinned parity, which is rule 14's tell rather than its answer.

    **Since plan step balance:X-bi-6a it is TWO statements, one per relation,
    and settled-ness is decided by which one a payment came from.**  The
    settled half is :func:`settled_income_shadows`: the shadow-income query
    narrowed in SQL to every row that is NOT still Projected, with the
    refusal below over what comes back.  The projected half is
    :func:`projected_income_legs`: one derived leg per still-projected
    transfer INTO the account, from ``budget.transfers``.  A consumer wanting
    one half pays for that half alone -- the docstring here used to say the
    other half was HYDRATED for free, and that cost is gone with the
    partition, exactly as it said it would be.

    **The classification is TOTAL and refuses what it cannot place.**  A row is
    settled, or it is ``Projected``; :func:`query_shadow_income` has already
    dropped the balance-excluded statuses, so nothing else can arrive today.
    That is a claim about the ``ref.statuses`` seed, not about this code, and a
    set defined by everything-except is exactly the shape that claims members
    nobody censused -- so a sixth status RAISES here rather than falling
    silently out of every loan's schedule and balance.  The refusal reads the
    SHADOW side: a transfer in such a status carries a shadow in it (Transfer
    Invariant 3), the shadow arrives in the settled half's not-Projected band,
    and the refusal names it.

    Args:
        account_id: The account receiving the transfers.
        scenario_id: The budget scenario to scope to.
        options: The loader options for every relationship the CALLER will
            traverse on the SETTLED rows (see :func:`query_shadow_income`).
            ``Transaction.pay_period`` is added here regardless, because THIS
            function reads it -- it is the sort key -- which is the same rule
            applied one level up.  It is taken from
            :func:`~app.utils.amount_relationships.period_load_option`, the one
            spelling ``pricing_load_options`` also uses, so a priced caller
            naming that path twice cannot name it with two STRATEGIES.
        leg_options: The loader options for every relationship the caller
            will traverse on the PROJECTED legs' parents, rooted at
            :class:`~app.models.transfer.Transfer`
            (:func:`~app.utils.amount_relationships.transfer_pricing_load_options`
            for a caller that prices them, ``()`` for one that reads dates);
            ``Transfer.pay_period`` is added regardless, for the same reason.

    Returns:
        The :class:`ShadowSets` for the account; both halves ``[]`` when it has
        no income payments.

    Raises:
        ValueError: When a shadow carries a status that is neither settled nor
            ``Projected``.  The message names the row and the status, because a
            silently dropped payment is a balance that is quietly wrong.
    """
    return ShadowSets(
        settled=settled_income_shadows(
            account_id, scenario_id, options=options,
        ),
        projected=projected_income_legs(
            account_id, scenario_id, options=leg_options,
        ),
    )


def settled_income_shadows(
    account_id: int, scenario_id: int, *, options: tuple,
) -> list[Transaction]:
    """Return a loan's SETTLED income shadows, in payment order, NO period bound.

    The SETTLED half of :func:`income_shadows`, which is where the derivation
    itself lives since plan step **balance:X-bl-2a** -- this is the name that
    reads as the question a caller is asking, kept because a dozen docstrings
    cite it as the one settled-payment producer and that is still true of what
    it returns.  What follows describes the set:
    the shared :func:`query_shadow_income` predicate (transfer-linked, Income type,
    non-deleted, non-excluded) narrowed to the settled statuses -- and NOTHING ELSE.
    Every settled-payment consumer reads this ONE set, so no two can disagree on
    which payments are settled: the fold's event stream
    (:func:`app.services.loan_ledger.walk_loan_ledger`), the fold's display bound
    (:func:`app.services.loan_ledger.confirmed_shadows_through`), the ledger's
    per-payment principal reader, and :func:`_settled_payment_due_dates` (the
    escrow forward-only guard's boundary
    :func:`latest_settled_payment_due_date`, since finding N-34; the
    tracking-start ordering guard that also read it was deleted at plan step
    ``recurrence:R20``).

    **The narrowing is ``NOT Projected`` in SQL and ``settled`` in Python, and
    the gap between the two is the refusal** (plan step balance:X-bi-6a).
    Narrowing to ``status_id IN settled`` in SQL would be a set defined by
    what it admits and would let a sixth status vanish; loading everything
    non-excluded and dropping the Projected rows in Python would hydrate the
    whole projected tail for nothing, now that the projected half is a
    different relation.  So the query excludes exactly the one status the
    other half owns, and every row that comes back must be settled or it is
    refused by name.

    It was TWO functions of this name until the fold moved to its own leaf -- this
    one (unordered) and the genesis walk's private copy (sorted) -- each claiming in
    its docstring to be the single derivation the other could not disagree with.
    They issued the identical query, so they never did disagree; two copies of a
    predicate that answers one question is nonetheless exactly the shape the arc's
    process lessons name (``docs/audits/balance_architecture/README.md`` Section 8).

    Two bounds the resolver's
    :func:`app.services.rate_period_engine.is_confirmed_payment_eligible` filter
    applies are deliberately ABSENT:

    * **No post-anchor LOWER bound.**  The fold walks EVERY settled payment from
      origination, because an anchor is a running-balance RESET
      (:func:`app.services.loan_ledger.walk_loan_ledger`), not a payment exclusion.
      A pre-anchor payment is split and posted (its principal effect is later
      subsumed by the anchor correction), never silently dropped.
    * **No has-happened-yet UPPER bound.**  Settlement is the confirming event: the
      Step-2 cash entry posts the moment a payment settles, so the split correction
      must post in the SAME moment or the loan-linked ledger holds raw cash with no
      interest / escrow backout from the payment's period start until the next loan
      write (the 2026-07-02 adversarial review's H2 -- demonstrated as a ~$1,636
      understatement on the real Mortgage).  The READERS apply their own bound, so
      posting early changes when the fact is RECORDED, never when it is SHOWN.

      **That bound is the payment's SETTLED day, not its pay period, on every
      reader** -- the fold's since step C2 (:func:`app.services.loan_ledger.payment_visible_on`),
      the resolver's replay since plan step **X-an**.  This paragraph used to say
      the readers' PERIOD bound kept an early-settled payment out of every
      displayed balance until its period began, and finding **N-187** is that
      sentence being false in both directions at once: an early-settled payment
      IS shown from the day its cash moved, and the resolver was the one reader
      still waiting for the period -- so it planned an installment the ledger had
      already paid down.

    Sorted by pay-period start -- the app's canonical payment chronology
    (``get_payment_history`` orders identically) and the order the fold's running
    balance is walked in; ``id`` is the deterministic tie-breaker.  The order is
    immaterial to the guards (they take a ``min`` / ``max`` / set), and load-bearing
    for the walk, so it is applied ONCE here rather than by each caller.  These are
    the RAW shadows; the resolver's biweekly-collision redistribution (a display
    fix) is NOT applied, and is immaterial to a sequentially walked running balance.
    ``pay_period`` is eager-loaded HERE, which reads it itself as the sort key --
    NOT by :func:`query_shadow_income`, which since plan step **balance:X-bl-2a**
    loads only what its caller asks for.

    Args:
        account_id: The loan account whose settled payments to load.
        scenario_id: The budget scenario to scope to.
        options: The loader options for every relationship the CALLER will
            traverse (see :func:`query_shadow_income`) -- ``()`` for a consumer
            reading columns and dates, ``settlement_load_options()`` for one
            that values the rows through their settlement record (the row's
            ENTRIES since plan step ``balance:X-bi-4b-1``: the fold's event
            stream, its confirmed history, the asset contribution pass),
            ``pricing_load_options()`` for one that prices the rows.

    Returns:
        Every settled income shadow, ascending by ``(pay_period.start_date, id)``;
        ``[]`` when the loan has no settled payment.

    Raises:
        ValueError: When a shadow carries a status that is neither settled nor
            ``Projected``.  Named here because this view is the door the fold's
            walk, the posting reader and the escrow forward-only guard reach it
            through, so a broken status seed surfaces on every loan surface at
            once rather than on one.
    """
    rows = (
        query_shadow_income(
            account_id, scenario_id,
            options=(period_load_option(), *options),
        )
        .filter(~is_projected_clause(Transaction))
        .all()
    )
    settled_ids = settled_status_ids()
    for shadow in rows:
        # The SHARED predicate -- ``settled_status_ids`` is the one "which
        # statuses are settled" answer (D6-09).  An inline ``status_id ==``
        # here would be a second status rule in a module whose whole subject
        # is having one, and ``TestNoInlineStatusBusinessLogic`` refuses it.
        if shadow.status_id not in settled_ids:
            raise ValueError(
                f"shadow income transaction {shadow.id} carries status_id "
                f"{shadow.status_id}, which is neither settled nor Projected: "
                f"this partition is the app's one settled-payment derivation "
                f"and cannot place the row"
            )
    rows.sort(key=lambda shadow: (shadow.pay_period.start_date, shadow.id))
    return rows


def projected_income_legs(
    account_id: int, scenario_id: int, *, options: tuple,
) -> list[TransferLeg]:
    """Return a loan's PROJECTED payments as legs of their parents, in payment order.

    The forward analogue of :func:`settled_income_shadows`, and since plan
    step **balance:X-bi-6a** a different RELATION: one
    :class:`~app.services.transfer_legs.TransferLeg` per live
    still-projected transfer INTO the account, derived from the parent row in
    ``budget.transfers`` (ruling **R-BAL13**).  It was
    ``projected_income_shadows``, the shadow-income query narrowed to the
    PROJECTED status, until that step; the payment RECORDS a loan's forward
    projection folds (plan step C6, the PLANNED tier) are the parents now,
    and the shadow rows have no projected reader left.

    **Complementary with the settled set, so no payment is counted twice --
    STRUCTURALLY.**  A settled payment is a shadow row in a settled status; a
    projected one is a parent row in the Projected status.  A row cannot be in
    both relations under both narrowings, which is what lets the C6c
    settled-slot de-dup stay deleted.

    **The income side is the TO-side.**  The leaf loader returns every leg the
    account is on; a payment INTO the account is the leg whose parent names
    it as ``to_account_id``, which is the same rule the transfer service
    writes as the income shadow's TYPE.  A transfer OUT of a loan account is
    not a payment and is not returned.

    Carries no period bound and NO cash: the plan builder resolves each leg's
    cash (:func:`app.services.cash_ledger.planned_leg_contribution` over the
    parent), its due date (:func:`loan_payment_due_date`, which takes a leg)
    and its escrow as the plan is assembled.

    Args:
        account_id: The loan account whose projected payments to load.
        scenario_id: The budget scenario to scope to.
        options: The loader options for every relationship the CALLER will
            traverse on the parents, rooted at
            :class:`~app.models.transfer.Transfer`
            (:func:`~app.utils.amount_relationships.transfer_pricing_load_options`
            for a caller that prices them, ``()`` for one that reads dates).
            ``Transfer.pay_period`` is added here regardless, because THIS
            function reads it -- it is the sort key.

    Returns:
        Every projected leg into the account, ascending by
        ``(pay_period.start_date, transfer id)``; ``[]`` when the loan has no
        projected payment.
    """
    legs = [
        leg for leg in planned_transfer_legs(
            account_id, scenario_id,
            options=(transfer_period_load_option(), *options),
        )
        if leg.is_income
    ]
    legs.sort(key=lambda leg: (leg.pay_period.start_date, leg.transfer.id))
    return legs


def query_shadow_income(account_id: int, scenario_id: int, *, options: tuple):
    """Return the base query for shadow-income transactions on an account.

    Shadow income is the income-leg shadow of a transfer INTO the account:
    a payment received by a loan, or a contribution into an investment
    account.  It is identified by ``transfer_id IS NOT NULL`` plus the
    Income transaction type, excluding soft-deleted rows and the
    balance-excluded statuses (Credit, Cancelled, via the centralized
    ``balance_excluded_status_ids`` accessor).  Centralizing that predicate
    keeps the loan-payment history and the year-end contribution feeds from
    drifting on what counts as shadow income (MED-02): a one-sided change
    to the rule would otherwise desynchronize the two surfaces.

    **THE CALLER STATES EVERY LOAD THAT COSTS A ROUND TRIP** (plan step
    **balance:X-bl-2a**).  This loader baked ``pricing_load_options()`` -- the
    AMOUNT MODEL's five-chain eager set -- into itself, which made a ROW loader
    the authority on what its callers would traverse, and a producer cannot know
    that.  Both directions of the mistake are live in this codebase: FOUR of this
    query's SEVEN consumers never touch the pricing chain they paid five
    statements for -- ``loan_posting_service._reader`` (two columns),
    ``loan_ledger.confirmed_shadows_through`` (a settle day),
    ``loan_ledger.walk_loan_ledger`` (dates plus the settlement columns) and
    ``_terms._settled_payment_due_dates`` (a due date) -- while finding **N-296**
    is the same defect inverted: seven batch callers that DO price and reach a
    loader without the chain, paying a query per definition.  *A first draft of
    this paragraph said "three of six" and named the installment feed as one that
    never prices, which is wrong in both halves -- that feed's only production
    caller passed ``pricing_load_options()`` until plan step balance:X-bi-6a,
    and the two genuine non-pricers it omitted are the fold's own.*  *Since
    X-bi-6a every row this query returns is SETTLED and valued from its record,
    so no production caller states a pricing load any more; the parameter
    stays because the rule is the caller's statement, whatever it states.*
    ``options`` is required rather than defaulted, so a new caller
    must decide instead of inheriting a guess.

    ``status`` stays here because it is a ``joinedload`` of a small reference
    row: it rides on this query's own SELECT and costs no round trip, so it is
    not the caller's to decide.  The line is exactly that -- what costs a trip
    is stated by whoever reads it.  Period scoping and ordering stay with the
    caller because they differ: the payment feeds cover every period and order by
    period start.  *The contribution reader (``balance_at._asset_contributions``)
    took the rows unordered and unwindowed until plan step balance:X-bi-6a; it
    reads the two producers above now and this query has ONE caller,
    :func:`settled_income_shadows`.*

    **Two paragraphs stood here saying the opposite of the rule above, and they
    are DELETED rather than amended.**  They recorded plan step X-au-g-2c-2
    putting ``pay_period`` and the whole of
    :func:`~app.utils.amount_relationships.pricing_load_options` INTO this
    query, "so a rule that starts reading a new relationship does not leave this
    loader behind" -- true when written and false the moment ``options`` became
    the caller's.  A contract paragraph that survives the change it describes is
    worse than none: this docstring is what a new caller reads to decide what to
    pass.

    What survives from them is the reason a caller reaching for the pricing set
    imports it from the ``utils`` leaf rather than from ``cash_ledger``, which
    re-exports it: THIS module is one of the loan term primitives that package
    imports -- the arrow plan step X-au-g-2a made run one way -- and
    ``cyclic-import`` traces a call-time import too.  Every row this query
    returns is a transfer SHADOW by its own predicate, and a shadow is DERIVED,
    priced through ``transfer -> template -> settings`` -- which, since plan
    step balance:X-bi-6a narrowed its one caller to SETTLED rows, no row this
    query returns is priced through any more: a settled shadow is valued from
    its record.  The paragraph is kept for the day a caller asks this query for
    a row that has not settled.

    Args:
        account_id: The account receiving the transfers.
        scenario_id: The active budget scenario.
        options: The loader options for every relationship the CALLER will
            traverse -- ``pricing_load_options()`` for a caller that prices the
            rows, ``()`` for one that reads columns only.  Required: a default
            here would be this loader guessing again.

    Returns:
        A SQLAlchemy ``Query`` over ``Transaction`` filtered to the account's
        shadow income (``status`` eager-loaded, plus whatever *options* names),
        NOT yet executed -- callers chain ``.filter`` / ``.join`` /
        ``.order_by`` / ``.all`` as their surface requires.
    """
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    return (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.status),
            *options,
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
            Transaction.is_deleted.is_(False),
            ~Transaction.status_id.in_(balance_excluded_status_ids()),
        )
    )
