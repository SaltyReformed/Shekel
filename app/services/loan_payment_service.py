"""
Shekel Budget App -- Loan Payment Service

Queries shadow income transactions on debt accounts and converts them
to PaymentRecord instances for the amortization engine.  Also provides
payment preparation utilities (escrow subtraction, biweekly
redistribution) shared by all consumers of amortization schedules.

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
  - app/services/year_end_summary_service.py (annual aggregation)
"""

import calendar
import logging
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.models.ref import Status
from app.models.transaction import Transaction
from app.services import amortization_engine
from app.services.amortization_engine import PaymentRecord

logger = logging.getLogger(__name__)


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
    txns = (
        db.session.query(Transaction)
        .join(Transaction.status)
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
            Status.excludes_from_balance.is_(False),
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


def compute_contractual_pi(params: LoanParams) -> Decimal:
    """Compute the standard monthly P&I payment from loan params.

    For ARM loans, the payment is re-amortized from current balance at
    the current rate.  For fixed-rate loans, uses original terms.

    Args:
        params: LoanParams model instance with original_principal,
            current_principal, interest_rate, term_months,
            origination_date, and is_arm attributes.

    Returns:
        Decimal monthly P&I payment.
    """
    remaining = amortization_engine.calculate_remaining_months(
        params.origination_date, params.term_months,
    )
    if params.is_arm:
        return amortization_engine.calculate_monthly_payment(
            Decimal(str(params.current_principal)),
            Decimal(str(params.interest_rate)),
            remaining,
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
