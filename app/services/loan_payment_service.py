"""
Shekel Budget App -- Loan Payment Service

Queries shadow income transactions on debt accounts and converts them
to PaymentRecord instances for the amortization engine.  Also provides
payment preparation utilities (escrow subtraction, biweekly
redistribution) and a unified data-loading function (load_loan_context)
shared by all consumers of amortization schedules.

Shadow income transactions represent payments received by a debt
account via transfers.  When a user transfers money from checking to
a mortgage account, the transfer service creates two shadow
transactions: an expense on checking (money out) and an income on
the mortgage (money in).  This service queries the income side to
discover all payments into a loan account.

This service queries ONLY budget.transactions (transfer invariant #5).
It NEVER queries budget.transfers.  The balance calculator and all
related services must never depend on the transfers table directly.

Shared by:
  - app/routes/loan/ (dashboard and payoff calculator)
  - app/services/savings_dashboard_service.py (savings projections)
  - app/services/year_end_summary_service.py (annual aggregation)
  - app/routes/debt_strategy.py (debt payoff strategies)
"""

import calendar
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_features import EscrowComponent, RateHistory
from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import escrow_calculator
from app.services.amortization_engine import PaymentRecord, RateChangeRecord
from app.services.rate_period_engine import monthly_due_date
from app.utils.balance_predicates import (
    balance_excluded_status_ids,
    is_projected,
)

if TYPE_CHECKING:
    # Type-only import: ``loan_resolver`` imports from this module
    # (``load_loan_context``), so a runtime top-level import would be
    # circular.  ``resolve_account_loan`` returns its ``LoanState``; the
    # value is produced via the function-local import below.
    from app.services.loan_resolver import LoanState

logger = logging.getLogger(__name__)


@dataclass
class LoanContext:
    """All context data needed for loan projection.

    Loaded once per account via load_loan_context(), shared across all
    projection consumers (loan dashboard, savings dashboard, year-end
    service, debt strategy).  Eliminates duplicated data loading logic.

    Attributes:
        payments: Prepared PaymentRecord list (escrow-subtracted,
            biweekly month-aligned).  Ready for the amortization engine.
        rate_changes: List of RateChangeRecord for the loan -- its
            origination row plus any ARM adjustments (DH-#56: every loan
            carries an origination row).  ``None`` only for a loan with
            no RateHistory rows at all, which the origination-row
            invariant forbids in production.
        escrow_components: Active EscrowComponent ORM objects for
            display and escrow calculation.
        monthly_escrow: Aggregated monthly escrow Decimal.
        contractual_pi: Standard monthly P&I payment (no escrow).
        rate_history: RateHistory ORM objects for rate display.  Carries
            the origination row for every loan plus any ARM adjustments;
            the loan dashboard shows the table only for ARM loans.
    """

    payments: list[PaymentRecord]
    rate_changes: list[RateChangeRecord] | None
    escrow_components: list  # list[EscrowComponent]
    monthly_escrow: Decimal
    contractual_pi: Decimal
    rate_history: list = field(default_factory=list)  # list[RateHistory]


def load_loan_context(
    account_id: int,
    scenario_id: int | None,
    loan_params: LoanParams,
) -> LoanContext:
    """Load and prepare all context data for a loan account.

    Consolidates the data loading pattern repeated in loan routes,
    savings dashboard, year-end service, and debt strategy: payment
    history retrieval, escrow loading, payment preparation (escrow
    subtraction + biweekly redistribution), and rate change loading
    for ARM loans.

    This is a pure data-loading function -- no Flask request/session
    imports.  Callers pass the scenario_id explicitly.

    Args:
        account_id: The loan account ID.
        scenario_id: Baseline scenario ID for payment history lookup.
            None means no payments are loaded (empty list).
        loan_params: LoanParams model instance for the account.

    Returns:
        LoanContext with all data needed for amortization projection.
    """
    # Escrow -- loaded first because payment preparation needs it.
    escrow_components = load_active_escrow_components(account_id)
    monthly_escrow = escrow_calculator.calculate_monthly_escrow(
        escrow_components,
    )

    # Rate history for EVERY loan -- needed BEFORE contractual_pi so
    # the rate (origination plus any ARM adjustments) factors into the
    # SSOT monthly_payment.  DH-#56 retired LoanParams.interest_rate, so
    # the loan's base / period-0 rate now lives in its origination
    # RateHistory row; every loan carries one (create_params seeds it on
    # setup; the DH-#56 migration backfilled pre-existing loans).  The
    # load is therefore no longer ARM-gated -- a fixed-rate loan resolves
    # its single rate period from its one origination row.
    rate_history_records = (
        db.session.query(RateHistory)
        .filter_by(account_id=account_id)
        .order_by(RateHistory.effective_date.desc())
        .all()
    )
    rate_changes = _rate_change_records_from(rate_history_records)

    # Anchor events for the SSOT monthly_payment calculation.  Commit
    # 12's origination backfill guarantees at least one anchor per
    # loan; an empty list only arises in direct unit-test invocations
    # that bypass the backfill, and compute_contractual_pi tolerates it
    # (the period P&I is anchor-independent -- it reads the rate-change
    # feed, not the anchor).
    anchor_events = (
        db.session.query(LoanAnchorEvent)
        .filter_by(account_id=account_id)
        .all()
    )

    # Payment history from shadow income transactions.
    raw_payments = (
        get_payment_history(account_id, scenario_id)
        if scenario_id else []
    )

    # Prepare: subtract escrow and fix biweekly month overlaps.  The
    # ARM-aware contractual_pi makes the escrow-subtraction threshold
    # match LoanState.monthly_payment -- the SSOT property the user
    # called out (P&I, escrow, and monthly payment numbers must be the
    # same across the loan card, the schedule's projected rows, and
    # the prepared-payment net amount).  ``raw_payments`` is passed
    # so the baseline does a conservative anchor-walk over the raw
    # (gross-of-escrow) amounts -- guarantees the threshold is at-
    # or-below ``state.monthly_payment``, which guarantees the
    # escrow-subtraction min() in :func:`prepare_payments_for_engine`
    # picks the FULL escrow amount.  Without this, the threshold is
    # an anchor-based approximation that slightly overestimates the
    # true P&I, under-subtracts escrow, and leaks a few cents per
    # row into the schedule's "Payment" column.
    contractual_pi = compute_contractual_pi(
        loan_params,
        anchor_events=anchor_events,
        rate_changes=rate_changes,
        as_of=date.today(),
        payments=raw_payments,
    )
    payments = prepare_payments_for_engine(
        raw_payments, loan_params.payment_day,
        monthly_escrow, contractual_pi,
    )

    return LoanContext(
        payments=payments,
        rate_changes=rate_changes,
        escrow_components=escrow_components,
        monthly_escrow=monthly_escrow,
        contractual_pi=contractual_pi,
        rate_history=rate_history_records,
    )


def _rate_change_records_from(
    rate_history_records: list,
) -> list[RateChangeRecord] | None:
    """Convert loaded RateHistory rows to the engine's RateChangeRecord feed.

    The pure (no-DB) half of rate-change loading, shared by
    :func:`load_loan_context` (which also keeps the raw ORM rows for its
    ``rate_history`` display field) and :func:`load_rate_changes` (which needs
    only the feed), so the two cannot drift on how a :class:`RateHistory` row
    maps to a :class:`RateChangeRecord`.  Returns ``None`` -- not an empty
    list -- for no rows: the resolver treats ``None`` and an empty feed
    identically (an origination-row-less loan is unresolvable), and the explicit
    ``None`` keeps the established contract a loan with no RateHistory has no
    feed at all.

    Args:
        rate_history_records: The loan's :class:`RateHistory` ORM rows (any
            order; each exposes ``effective_date`` / ``interest_rate`` /
            optional ``monthly_pi``).

    Returns:
        The :class:`RateChangeRecord` list, or ``None`` when there are no rows.
    """
    if not rate_history_records:
        return None
    return [
        RateChangeRecord(
            effective_date=rh.effective_date,
            interest_rate=Decimal(str(rh.interest_rate)),
            monthly_pi=(
                Decimal(str(rh.monthly_pi))
                if rh.monthly_pi is not None else None
            ),
        )
        for rh in rate_history_records
    ]


def load_rate_changes(account_id: int) -> list[RateChangeRecord] | None:
    """Load a loan's rate-change feed (origination row plus any ARM adjustments).

    Queries the account's :class:`RateHistory` rows (newest first, the same
    order :func:`load_loan_context` uses) and maps them to the engine's
    :class:`RateChangeRecord` feed via :func:`_rate_change_records_from`.  The
    standalone loader for callers that need ONLY the feed -- the Build-Order
    Step 4 split walk
    (:func:`app.services.loan_posting_service.compute_loan_payment_splits`) builds the
    loan's rate periods from it via
    :func:`app.services.loan_resolver.resolve_periods` -- without paying for the
    rest of :func:`load_loan_context`'s payment-history / escrow /
    contractual-P&I work.

    Args:
        account_id: The loan account whose rate history to load.

    Returns:
        The :class:`RateChangeRecord` list (newest first), or ``None`` when the
        loan carries no :class:`RateHistory` row (an origination-row-less,
        unresolvable loan -- the resolver raises on such a feed).
    """
    rate_history_records = (
        db.session.query(RateHistory)
        .filter_by(account_id=account_id)
        .order_by(RateHistory.effective_date.desc())
        .all()
    )
    return _rate_change_records_from(rate_history_records)


def load_loan_params(account_id: int) -> LoanParams | None:
    """Load a loan account's :class:`LoanParams` row, or None.

    The one-line "is this a configured loan, and if so what are its terms"
    lookup shared by every loan consumer (:func:`resolve_account_loan`,
    :func:`_resolve_loan_piti`, and the Step-4
    :func:`app.services.loan_posting_service.compute_loan_payment_splits`), so
    none of them re-spells the same query and a future change to how a loan's
    params are loaded (eager-loads, soft-delete handling) touches one site.
    ``None`` means the account has no loan configuration yet -- not an
    amortizing loan, or a loan whose setup is incomplete -- and the caller
    short-circuits.

    Args:
        account_id: The account whose loan parameters to load.

    Returns:
        The :class:`LoanParams` row, or ``None`` when the account is not a
        configured loan.
    """
    return (
        db.session.query(LoanParams)
        .filter_by(account_id=account_id)
        .first()
    )


def load_anchor_events(account_id: int) -> list:
    """Load every :class:`LoanAnchorEvent` for a loan account (unordered).

    The shared anchor-history loader for the loan consumers
    (:func:`resolve_account_loan`, :func:`_resolve_loan_piti`, and the Step-4
    :func:`app.services.loan_posting_service.compute_loan_payment_splits`); the
    resolver and the split walk both select the latest event from the returned
    list via :func:`app.services.loan_resolver.select_latest_anchor` (so the
    ordering is irrelevant here and not imposed).  Centralising the query keeps
    the consumers from drifting on how a loan's anchor history is read.

    Args:
        account_id: The loan account whose anchor events to load.

    Returns:
        The account's :class:`LoanAnchorEvent` rows (possibly empty -- the
        origination backfill guarantees at least one in production, but a
        direct-insert test fixture may have none).
    """
    return (
        db.session.query(LoanAnchorEvent)
        .filter_by(account_id=account_id)
        .all()
    )


def load_active_escrow_components(account_id: int) -> list:
    """Load a loan account's active escrow components, ordered by name.

    The shared "what escrow does this loan carry" loader for the loan consumers
    (:func:`load_loan_context` and the Step-4
    :func:`app.services.loan_posting_service.compute_loan_payment_splits`), so the
    monthly-escrow figure each feeds to
    :func:`app.services.escrow_calculator.calculate_monthly_escrow` is summed
    over the IDENTICAL component set.  Removed components (``end_date`` set) are
    excluded -- "currently active" is exactly ``end_date IS NULL`` under the
    effective-dated model -- matching every other escrow surface in the app.
    For the escrow active on a PAST date (the loan-payment split walk), see
    :func:`escrow_components_as_of`.

    Args:
        account_id: The loan account whose escrow components to load.

    Returns:
        The currently-active (``end_date IS NULL``)
        :class:`~app.models.loan_features.EscrowComponent` rows, ascending by
        name (the order is irrelevant to the order-independent monthly sum, but
        kept stable for display callers).
    """
    return (
        db.session.query(EscrowComponent)
        .filter(
            EscrowComponent.account_id == account_id,
            EscrowComponent.end_date.is_(None),
        )
        .order_by(EscrowComponent.name)
        .all()
    )


def query_shadow_income(account_id: int, scenario_id: int):
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

    ``status`` and ``pay_period`` are eager-loaded because both current
    consumers read ``txn.status`` / ``txn.pay_period`` downstream without an
    N+1.  Period scoping and ordering stay with the caller because they
    differ: the payment history covers every period and orders by period
    start; the year-end feeds filter to a specific set of period IDs.

    Args:
        account_id: The account receiving the transfers.
        scenario_id: The active budget scenario.

    Returns:
        A SQLAlchemy ``Query`` over ``Transaction`` filtered to the
        account's shadow income (status + pay_period eager-loaded), NOT yet
        executed -- callers chain ``.filter`` / ``.join`` / ``.order_by`` /
        ``.all`` as their surface requires.
    """
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    return (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.status),
            joinedload(Transaction.pay_period),
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


def get_payment_history(
    account_id: int, scenario_id: int,
) -> list[PaymentRecord]:
    """Query shadow income transactions on a debt account.

    Returns PaymentRecord instances for all non-deleted, non-excluded
    shadow income transactions linked to the given account and scenario.
    Shadow income transactions represent payments received by a debt
    account via transfers.

    Filtering logic:
      - transfer_id IS NOT NULL (shadow transactions only)
      - transaction_type_id = Income (income side of the transfer)
      - is_deleted = False (excludes soft-deleted transactions)
      - status.excludes_from_balance = False (excludes Cancelled and
        Credit statuses, which do not represent actual payments)

    The is_confirmed flag is determined by the status.is_settled
    boolean:
      - True for Paid, Received, Settled (payment actually occurred)
      - False for Projected (payment is committed but not yet made)

    Uses effective_amount (not manual actual/estimated logic) to
    respect the 5A.1 fix: actual_amount when populated, else
    estimated_amount, with correct zero-vs-null handling.

    Args:
        account_id: The debt account receiving payments.
        scenario_id: The active budget scenario.

    Returns:
        List of PaymentRecord instances sorted by payment date
        (ascending).  Empty list if no qualifying transactions exist.
    """
    # Shadow-income transactions for this account across every period,
    # ordered by period start for the chronological payment timeline.
    # ``query_shadow_income`` owns the shared "what counts as shadow income"
    # predicate; the explicit ``join(Transaction.pay_period)`` brings the
    # PayPeriod alias into scope for the ``order_by`` (the builder's
    # ``joinedload`` is the separate N+1-avoiding eager-load).
    txns = (
        query_shadow_income(account_id, scenario_id)
        .join(Transaction.pay_period)
        .order_by(PayPeriod.start_date)
        .all()
    )

    payments = []
    for txn in txns:
        amount = txn.effective_amount
        # Defensive: ensure Decimal even if effective_amount somehow
        # returns a non-Decimal from a DB column.
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))

        payments.append(PaymentRecord(
            payment_date=txn.pay_period.start_date,
            amount=amount,
            is_confirmed=txn.status.is_settled,
        ))

    return payments


def compute_contractual_pi(
    params: LoanParams,
    anchor_events: list | None = None,
    rate_changes: list[RateChangeRecord] | None = None,
    as_of: date | None = None,
    payments: list[PaymentRecord] | None = None,
) -> Decimal:
    """Return the SSOT monthly P&I number for a loan.

    Routes through :func:`loan_resolver.compute_monthly_payment_baseline`
    so the returned value is byte-identical to
    ``LoanState.monthly_payment`` -- the loan card, the schedule's
    projected rows, and the escrow-subtraction threshold in
    :func:`prepare_payments_for_engine` all converge on one number.

    The monthly P&I is the level payment of the rate period containing
    ``as_of``, derived from the loan's rate-change feed (its origination
    :class:`RateHistory` row plus any ARM adjustments).  DH-#56 retired
    the ``LoanParams.interest_rate`` column, so the rate now comes
    exclusively from ``rate_changes``; the prior legacy pure-LoanParams
    fallback (which read the column) is gone.  The value is independent
    of the running balance, so ``anchor_events`` and ``payments`` are
    accepted for caller compatibility only and are not read.

    Args:
        params: LoanParams model instance with ``original_principal``,
            ``term_months``, and the ARM cadence fields.
        anchor_events: Accepted for caller compatibility; unused (the
            period P&I does not depend on the anchor balance).
        rate_changes: The loan's rate-change feed (origination row plus
            any ARM adjustments).  Required -- an empty/``None`` feed
            raises in the resolver, because every loan must carry an
            origination :class:`RateHistory` row.
        as_of: Optional evaluation date.  Defaults to ``date.today()``.
        payments: Accepted for caller compatibility; unused.

    Returns:
        Decimal monthly P&I payment, or ``Decimal("0")`` when
        ``original_principal`` is NULL (defensive: the column is NOT
        NULL, so this is unreachable in practice).

    Raises:
        ValueError: When ``rate_changes`` is empty/``None`` (the
            origination-rate invariant is violated) -- surfaced by
            :func:`loan_resolver._periods._origination_rate`.
    """
    if params.original_principal is None:
        return Decimal("0")
    # Pylint: ``import-outside-toplevel`` -- local import avoids a
    # top-level circular import (loan_resolver imports ``load_loan_context``
    # from this module, so a top-level import here would be circular).
    from app.services import loan_resolver  # pylint: disable=import-outside-toplevel
    # ``anchor_events`` and ``payments`` are unused by the baseline (the
    # period P&I is anchor-independent); passed through for signature
    # compatibility.  The rate comes from ``rate_changes`` (DH-#56 retired
    # ``LoanParams.interest_rate``), so an empty feed raises in the
    # resolver rather than silently defaulting to a wrong payment.
    return loan_resolver.compute_monthly_payment_baseline(
        params,
        anchor_events or [],
        rate_changes,
        as_of or date.today(),
        payments=payments,
    )


def _redistribute_to_distinct_months(
    payments: list[PaymentRecord], payment_day: int
) -> list[PaymentRecord]:
    """Shift payments sharing a monthly DUE month to consecutive months.

    Biweekly pay periods sometimes place two mortgage payments in the same
    calendar month; the monthly engine would sum them, double-counting one
    month and leaving the next empty.  At most one extra payment per month
    (~2x/year) is expected, so cascading collisions are not, but the
    while-loop handles them defensively.  The collision key is the true
    monthly DUE month (``monthly_due_date`` of the pay-period start), NOT
    the pay-period-start month: two pay periods that both fall before the
    same ``payment_day`` (e.g. Apr 10 and Apr 24, both due May 1) collide on
    the May schedule row, and the schedule/override key everything by due
    month -- a pay-period-start-month key would leave that collision
    unresolved and sum both into a single double payment.
    """
    result: list[PaymentRecord] = []
    allocated_months: set[tuple[int, int]] = set()
    for p in payments:
        due = monthly_due_date(p.payment_date, payment_day)
        ym = (due.year, due.month)
        if ym not in allocated_months:
            result.append(p)
            allocated_months.add(ym)
        else:
            y, m = ym
            m += 1
            if m > 12:
                m = 1
                y += 1
            while (y, m) in allocated_months:
                m += 1
                if m > 12:
                    m = 1
                    y += 1
            max_day = calendar.monthrange(y, m)[1]
            new_date = date(y, m, min(payment_day, max_day))
            result.append(PaymentRecord(
                payment_date=new_date,
                amount=p.amount,
                is_confirmed=p.is_confirmed,
            ))
            allocated_months.add((y, m))
    return result


def prepare_payments_for_engine(
    payments: list[PaymentRecord],
    payment_day: int,
    monthly_escrow: Decimal,
    contractual_pi: Decimal,
) -> list[PaymentRecord]:
    """Prepare payment records for the amortization engine.

    Corrects two mismatches between biweekly shadow transactions and
    the monthly amortization schedule:

    1. Escrow subtraction: Recurring transfers include escrow in their
       total amount, but the engine handles P&I only.  Without this
       correction, the engine treats escrow as extra principal, inflating
       paydown speed and showing escrow as spurious "Extra" entries.
       Only subtracts escrow from the portion that exceeds the standard
       P&I payment, so payments that do not include escrow are unaffected.

    2. Biweekly redistribution: Pay period start dates are biweekly and
       sometimes place two mortgage payments in the same calendar month
       (e.g., the Aug 1 payment falls in a Jul 29 pay period).  The
       engine sums same-month payments, double-counting one month and
       leaving the next empty.  This shifts extra same-month payments
       to subsequent months to restore one-payment-per-month alignment.

    Args:
        payments: List of PaymentRecord from get_payment_history().
        payment_day: Mortgage payment day of month (from LoanParams).
        monthly_escrow: Monthly escrow amount from escrow_calculator.
        contractual_pi: Standard monthly P&I payment (no escrow).

    Returns:
        Corrected list of PaymentRecord.
    """
    if not payments:
        return payments

    sorted_payments = sorted(payments, key=lambda p: p.payment_date)

    # Step 1: Subtract escrow from payments that include it.
    # Only subtract from the excess above contractual P&I so that
    # payments equal to or below P&I (no escrow included) are untouched.
    if monthly_escrow > Decimal("0.00"):
        adjusted = []
        for p in sorted_payments:
            if p.amount > contractual_pi:
                new_amount = p.amount - min(
                    monthly_escrow, p.amount - contractual_pi,
                )
            else:
                new_amount = p.amount
            adjusted.append(PaymentRecord(
                payment_date=p.payment_date,
                amount=new_amount,
                is_confirmed=p.is_confirmed,
            ))
        sorted_payments = adjusted

    # Step 2: Redistribute payments that share a monthly DUE month to
    # consecutive months so the monthly engine sees one per due month.
    return _redistribute_to_distinct_months(sorted_payments, payment_day)


def resolve_account_loan(
    account_id: int, scenario_id: int, today: date
) -> "tuple[LoanParams, LoanState] | None":
    """Load a debt account's ``LoanParams`` and run the resolver as of ``today``.

    The per-account "load LoanParams (skip if unconfigured), load anchor
    events + context, run the resolver" preamble shared by the debt-strategy
    route and the year-end schedule generation.  Centralizing it keeps the
    two consumers from drifting on HOW a loan account is resolved (which
    inputs feed :func:`loan_resolver.resolve_loan`, in what order).

    Returns ``None`` when the account has no ``LoanParams`` row (it is not a
    configured loan); the caller skips it.  Unlike
    :func:`_resolve_loan_piti`, an account WITH params but no anchor events
    is still resolved here -- both callers screen those out downstream
    (debt-strategy via its zero-balance/zero-payment guard, the year-end
    feed via the resulting empty schedule), so the no-anchor short-circuit
    that PITI needs is intentionally absent.

    Args:
        account_id: The debt account to resolve.
        scenario_id: The active budget scenario (for payment history).
        today: The as-of date passed through to the resolver.

    Returns:
        ``(params, state)`` -- the loaded :class:`LoanParams` and the
        resolved :class:`~app.services.loan_resolver.LoanState` -- or
        ``None`` if the account has no ``LoanParams``.
    """
    # Pylint: ``import-outside-toplevel`` -- loan_resolver imports from this
    # module (``load_loan_context``), so resolving it here rather than at
    # module top keeps the dependency one-directional.
    from app.services import loan_resolver  # pylint: disable=import-outside-toplevel
    params = load_loan_params(account_id)
    if params is None:
        return None
    anchor_events = load_anchor_events(account_id)
    ctx = load_loan_context(account_id, scenario_id, params)
    state = loan_resolver.resolve_loan(
        loan_resolver.LoanInputs(
            params, anchor_events, ctx.payments, ctx.rate_changes,
        ),
        today,
    )
    return params, state


def _resolve_loan_piti(
    loan_account_id: int, scenario_id: int, as_of: date
) -> Decimal | None:
    """Resolve a loan's full live monthly payment (P&I + escrow), or None.

    Returns None when the loan has no ``LoanParams`` row or no anchor
    events (it cannot be resolved, so its shadows keep their stored amount).
    ``resolve_loan(...).monthly_payment`` is the rate-period P&I;
    ``context.monthly_escrow`` adds the escrow component to reach PITI.
    """
    # Pylint: ``import-outside-toplevel`` -- loan_resolver imports nothing
    # from this module, so resolving it here rather than at module top
    # keeps the dependency one-directional.
    from app.services import loan_resolver  # pylint: disable=import-outside-toplevel
    params = load_loan_params(loan_account_id)
    if params is None:
        return None
    context = load_loan_context(loan_account_id, scenario_id, params)
    anchor_events = load_anchor_events(loan_account_id)
    if not anchor_events:
        return None
    state = loan_resolver.resolve_loan(
        loan_resolver.LoanInputs(
            params, anchor_events, context.payments, context.rate_changes,
        ),
        as_of,
    )
    return state.monthly_payment + context.monthly_escrow


def live_loan_transfer_amounts(
    scenario_id: int,
    transactions: list,
) -> dict[int, Decimal]:
    """Return ``{transaction_id: live PITI}`` for derive-from-loan transfer shadows.

    The read-time analogue of a recurring loan payment's stored
    ``TransferTemplate.default_amount``: for every Projected,
    non-overridden shadow transaction whose parent transfer's template
    has ``derive_from_loan=True``, recompute the full monthly payment
    LIVE from the destination loan -- ``resolve_loan(...).monthly_payment``
    (the rate-period P&I) plus the loan's monthly escrow/components.  A
    balance/display consumer can then treat the stored transfer amount as
    a cache that cannot silently disagree with the loan card after an
    escrow or rate change.  Directly mirrors the salary-income
    live-recompute, :func:`app.services.income_service.live_projected_net`.

    Both shadow legs of a transfer (the checking-side expense and the
    loan-side income) share the transfer id, so both receive the same
    PITI -- preserving Transfer Invariant 3 in the projection.  The
    checking expense leg moves the checking balance; the loan income leg
    does not affect the loan balance (that is resolver-derived), but
    keeping both equal avoids any surface showing mismatched shadows.

    Boundary discipline: no Flask import; inputs are plain data, output a
    plain dict.  Returns an empty dict when no candidate transfer targets
    a derive-from-loan template -- the common case for non-loan transfers
    and every pre-existing template (the flag defaults False) -- after at
    most one transfer/template lookup, so the balance render is unchanged
    for loans that have not opted in.

    Args:
        scenario_id: Scenario to resolve each loan against.
        transactions: Already-loaded (user-scoped) :class:`Transaction`
            rows.  Each must expose ``transfer_id``, ``status`` (for
            ``is_projected``), ``is_override``, and ``id``.

    Returns:
        ``dict`` mapping transaction id to the live PITI Decimal; empty
        when no derive-from-loan transfer is present.
    """
    candidates = [
        txn for txn in transactions
        if txn.transfer_id is not None
        and is_projected(txn)
        and not txn.is_override
    ]
    if not candidates:
        return {}

    transfer_ids = {txn.transfer_id for txn in candidates}
    transfers = (
        db.session.query(Transfer)
        .options(joinedload(Transfer.template))
        .filter(Transfer.id.in_(transfer_ids))
        .all()
    )
    loan_by_transfer = {
        xfer.id: xfer.to_account_id
        for xfer in transfers
        if xfer.template is not None and xfer.template.derive_from_loan
    }
    if not loan_by_transfer:
        return {}

    # Resolve each distinct loan's full monthly payment (P&I + escrow)
    # once, then map it onto every candidate shadow of that loan.
    today = date.today()
    piti_by_loan: dict[int, Decimal] = {}
    for loan_account_id in set(loan_by_transfer.values()):
        piti = _resolve_loan_piti(loan_account_id, scenario_id, today)
        if piti is not None:
            piti_by_loan[loan_account_id] = piti

    overrides: dict[int, Decimal] = {}
    for txn in candidates:
        loan_account_id = loan_by_transfer.get(txn.transfer_id)
        if loan_account_id is None:
            continue
        piti = piti_by_loan.get(loan_account_id)
        if piti is not None:
            overrides[txn.id] = piti
    return overrides
