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
  - app/routes/loan.py (dashboard and payoff calculator)
  - app/services/savings_dashboard_service.py (savings projections)
  - app/services/year_end_summary_service.py (annual aggregation)
  - app/routes/debt_strategy.py (debt payoff strategies)
"""

import calendar
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_features import EscrowComponent, RateHistory
from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import amortization_engine, escrow_calculator
from app.services.amortization_engine import PaymentRecord, RateChangeRecord
from app.utils.balance_predicates import (
    balance_excluded_status_ids,
    is_projected,
)

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
        rate_changes: List of RateChangeRecord for ARM loans, or None.
        escrow_components: Active EscrowComponent ORM objects for
            display and escrow calculation.
        monthly_escrow: Aggregated monthly escrow Decimal.
        contractual_pi: Standard monthly P&I payment (no escrow).
        rate_history: RateHistory ORM objects for ARM rate display.
            Empty list for fixed-rate loans.
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
    escrow_components = (
        db.session.query(EscrowComponent)
        .filter_by(account_id=account_id, is_active=True)
        .order_by(EscrowComponent.name)
        .all()
    )
    monthly_escrow = escrow_calculator.calculate_monthly_escrow(
        escrow_components,
    )

    # Rate history for ARM loans -- needed BEFORE contractual_pi so
    # the ARM rate adjustments factor into the SSOT monthly_payment.
    rate_history_records: list = []
    rate_changes: list[RateChangeRecord] | None = None
    if loan_params.is_arm:
        rate_history_records = (
            db.session.query(RateHistory)
            .filter_by(account_id=account_id)
            .order_by(RateHistory.effective_date.desc())
            .all()
        )
        if rate_history_records:
            rate_changes = [
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

    # Anchor events for the SSOT monthly_payment calculation.  Commit
    # 12's origination backfill guarantees at least one anchor per
    # loan; the empty-list case below is for direct unit-test
    # invocations that bypass the backfill (compute_contractual_pi's
    # fallback path covers it).
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
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

    # Query shadow income transactions with eager-loaded status and
    # pay_period to avoid N+1 queries when iterating results.
    # Status filter routes through the centralized
    # ``balance_excluded_status_ids`` accessor (D6-09 / MED-02): the
    # exclusion-set definition lives in one place.  The
    # ``join(Transaction.pay_period)`` is retained because the
    # subsequent ``order_by(PayPeriod.start_date)`` requires the
    # PayPeriod alias to be in scope; ``join(Transaction.status)``
    # is dropped because the ``Transaction.status_id`` filter no
    # longer needs it.  ``joinedload(Transaction.status)`` still
    # eager-loads the status row for the downstream ``is_settled``
    # consumer.
    txns = (
        db.session.query(Transaction)
        .join(Transaction.pay_period)
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
            # Exclude statuses that do not represent real payments
            # (Cancelled, Credit).  These have excludes_from_balance=True.
            ~Transaction.status_id.in_(balance_excluded_status_ids()),
        )
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
    when ``anchor_events`` is provided so the returned value is
    byte-identical to ``LoanState.monthly_payment`` -- the loan card,
    the schedule's projected rows, and the escrow-subtraction
    threshold in :func:`prepare_payments_for_engine` all converge on
    one number.

    Without the ARM-aware routing, an ARM loan whose rate has
    adjusted since origination produced a stale original-terms value
    here, which under-subtracted escrow from the prepared payments
    and made the schedule's "Payment" column disagree with the loan
    card by the rate-adjustment delta.  ``anchor_events`` is therefore
    REQUIRED in production; the legacy pure-LoanParams call shape is
    retained only as a degenerate fallback (origination-anchor +
    no-rate-changes) for unit tests that exercise the function
    without a resolver context.

    Args:
        params: LoanParams model instance with ``original_principal``,
            ``interest_rate``, and ``term_months``.
        anchor_events: Optional non-empty list of LoanAnchorEvent-
            shaped objects.  When provided (the production path),
            routes through the resolver baseline.  ``None`` (or
            empty) falls back to the original-terms amortization for
            backward compatibility with unit tests.
        rate_changes: Optional ARM rate-history, passed through to
            the resolver baseline.  ``None`` or empty for fixed-rate.
        as_of: Optional evaluation date.  Defaults to
            ``date.today()`` when ``anchor_events`` is provided.
            Ignored on the legacy fallback path.
        payments: Optional list of :class:`PaymentRecord` -- the RAW
            shadow-income amounts BEFORE escrow subtraction.  When
            provided, drives the conservative current-balance
            approximation in :func:`loan_resolver.compute_monthly_payment_baseline`
            so the threshold is guaranteed to be at-or-below the
            true ``state.monthly_payment``.  Without it (legacy
            callers), the baseline uses ``anchor_balance``, which
            slightly overestimates the threshold for an ARM whose
            principal has paid down since the latest anchor.

    Returns:
        Decimal monthly P&I payment, or ``Decimal("0")`` when either
        seed input is NULL (the E-18 / Commit 15 demotion permits
        ``interest_rate`` to be NULL at the storage tier).

    Raises:
        ValueError: When ``anchor_events`` is provided but empty
            (via :func:`loan_resolver._select_latest_anchor`).
    """
    if params.original_principal is None or params.interest_rate is None:
        return Decimal("0")
    if anchor_events:
        # Local import: avoids a top-level circular import (loan_resolver
        # imports nothing from this module today, but the policy
        # boundary is one-way and the local import documents that).
        from app.services import loan_resolver  # pylint: disable=import-outside-toplevel
        return loan_resolver.compute_monthly_payment_baseline(
            params,
            anchor_events,
            rate_changes,
            as_of or date.today(),
            payments=payments,
        )
    return amortization_engine.calculate_monthly_payment(
        Decimal(str(params.original_principal)),
        Decimal(str(params.interest_rate)),
        params.term_months,
    )


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

    # Step 2: Redistribute same-month payments to consecutive months.
    # Biweekly pay periods produce at most one extra payment per month
    # (~2 times per year), so cascading collisions are not expected,
    # but the while-loop handles them defensively.
    result = []
    allocated_months: set[tuple[int, int]] = set()

    for p in sorted_payments:
        ym = (p.payment_date.year, p.payment_date.month)
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
    # pylint: disable=import-outside-toplevel  -- one-way policy boundary;
    # the local import documents that loan_resolver imports nothing here.
    from app.models.transfer import Transfer
    from app.services import loan_resolver

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
        params = (
            db.session.query(LoanParams)
            .filter_by(account_id=loan_account_id)
            .first()
        )
        if params is None:
            continue
        context = load_loan_context(loan_account_id, scenario_id, params)
        anchor_events = (
            db.session.query(LoanAnchorEvent)
            .filter_by(account_id=loan_account_id)
            .all()
        )
        if not anchor_events:
            continue
        state = loan_resolver.resolve_loan(
            params, anchor_events, context.payments,
            context.rate_changes, today,
        )
        piti_by_loan[loan_account_id] = (
            state.monthly_payment + context.monthly_escrow
        )

    overrides: dict[int, Decimal] = {}
    for txn in candidates:
        loan_account_id = loan_by_transfer.get(txn.transfer_id)
        if loan_account_id is None:
            continue
        piti = piti_by_loan.get(loan_account_id)
        if piti is not None:
            overrides[txn.id] = piti
    return overrides
