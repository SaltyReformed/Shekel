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
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.models.loan_features import EscrowComponent
from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.scenario import Scenario


@dataclass(frozen=True)
class _DashboardCoreData:
    """Request-scoped data loaded once at the start of the dashboard build.

    Bundles the accounts, baseline scenario, and pay periods so the
    orchestrator passes one object to the projection step instead of a
    long positional parameter list.  Per-account balances come from the
    :mod:`app.services.balance_at` seam (which loads its own transactions),
    so no pre-loaded transaction set rides here.
    """

    accounts: list[Account]
    scenario: Scenario | None
    all_periods: list[PayPeriod]
    current_period: PayPeriod | None


@dataclass(frozen=True)
class _AccountParams:
    """Batch-loaded, account-type-specific parameter maps for the loop.

    Built once per request by :func:`_load_account_params` -- the single
    place all six maps are constructed -- and read per account inside the
    projection loop.  Each map is keyed by ``account_id``.  Request-scoped
    state that is not an account-type parameter (the baseline
    ``scenario``) lives on :class:`_ProjectionContext`, not here.
    """

    interest_params_map: dict[int, InterestParams]
    investment_params_map: dict[int, InvestmentParams]
    deductions_by_account: dict[int, list[PaycheckDeduction]]
    salary_gross_biweekly: Decimal
    loan_params_map: dict[int, LoanParams]
    escrow_map: dict[int, list[EscrowComponent]]


@dataclass(frozen=True)
class _ProjectionContext:
    """Loop-invariant inputs shared across the per-account projection loop.

    Every account in ``_compute_account_projections`` projects against
    the same periods, current period, loaded parameter maps, and baseline
    scenario; bundling them keeps the per-account helpers to a small,
    cohesive argument list.  The ``scenario`` object (not just its id) is
    held because the :mod:`app.services.balance_at` seam each non-loan tile
    reads through takes the :class:`~app.models.scenario.Scenario`; the loan
    path derives ``scenario.id`` for the resolver.
    """

    all_periods: list[PayPeriod]
    current_period: PayPeriod | None
    params: _AccountParams
    scenario: Scenario | None


@dataclass(frozen=True)
class _LoanAccountResult:
    """Resolver-derived figures for one loan account.

    Carries the figures the per-account dict needs from the loan
    resolver so ``_compute_loan_account`` can return them as one cohesive
    value instead of a positional tuple.  The loan tile renders the current
    balance, monthly payment, rate, and payoff date; it shows no
    projected-balance horizons (those are the :mod:`app.services.balance_at`
    seam's job for the non-loan kinds), so none are carried here.
    """

    current_balance: Decimal
    monthly_payment: Decimal
    current_rate: Decimal
    payoff_date: date | None
    is_paid_off: bool
