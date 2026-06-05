"""
Shekel Budget App -- Savings Dashboard: shared bundle dataclasses.

The request-scoped and per-account value objects passed between the
savings-dashboard package's loader, projection, and orchestration
modules so each helper takes a small, cohesive argument list rather than
a long positional parameter list.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.transaction import Transaction


@dataclass(frozen=True)
class _DashboardCoreData:
    """Request-scoped data loaded once at the start of the dashboard build.

    Bundles the accounts, baseline scenario, pay periods, and the
    pre-loaded transaction sets so the orchestrator passes one object
    to the projection step instead of six positional parameters.
    """

    accounts: list[Account]
    scenario: Scenario | None
    all_periods: list[PayPeriod]
    current_period: PayPeriod | None
    all_transactions: list[Transaction]
    all_shadow_income: list[Transaction]


@dataclass(frozen=True)
class _ProjectionContext:
    """Loop-invariant inputs shared across the per-account projection loop.

    Every account in ``_compute_account_projections`` projects against
    the same transactions, periods, current period, and loaded parameter
    maps; bundling them keeps the per-account helpers to a small,
    cohesive argument list.
    """

    all_transactions: list[Transaction]
    all_shadow_income: list[Transaction]
    all_periods: list[PayPeriod]
    current_period: PayPeriod | None
    params: dict


@dataclass(frozen=True)
class _LoanAccountResult:
    """Resolver-derived projection outputs for one loan account.

    Carries the figures the per-account dict needs from the loan
    resolver so ``_compute_loan_account`` can return them as one cohesive
    value instead of a positional tuple.
    """

    current_balance: Decimal
    monthly_payment: Decimal
    payoff_date: date | None
    projected: dict
    is_paid_off: bool
