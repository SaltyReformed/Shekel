"""
Shekel Budget App -- Year-End Summary: shared bundle dataclasses.

The loop-invariant value objects threaded between the year-end
package's section helpers so each takes a small, cohesive argument
list rather than a long parameter list.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario


@dataclass(frozen=True)
class _ProjectionInputs:
    """Pre-loaded per-account parameter maps the section helpers project against.

    Bundles the maps loaded once in :func:`_load_common_data` (plus the
    amortization schedules built in :func:`_build_summary`) so the
    net-worth and savings-progress helpers forward a single cohesive
    object down the projection call chain instead of the four or five
    parallel keyword arguments they previously threaded by hand
    (MED-01 / S6-06).

    The net-worth chain reads ``debt_schedules`` and the investment trio
    (``investment_params_map`` / ``deductions_by_account`` /
    ``salary_gross_biweekly``); the savings-progress chain reads
    ``interest_params_map`` and the same investment trio.  Each chain
    leaves the one field it does not need untouched.
    """

    debt_schedules: dict[int, list]
    investment_params_map: dict[int, InvestmentParams]
    interest_params_map: dict[int, InterestParams]
    deductions_by_account: dict[int, list]
    salary_gross_biweekly: Decimal


@dataclass(frozen=True)
class _YearContext:
    """The calendar year under summary plus its period and scenario context.

    Loop-invariant inputs shared across the net-worth and
    savings-progress per-account computations: the target year, the
    baseline scenario, the full ordered period list (needed for
    anchor-based reverse projection), and the IDs of the pay periods that
    fall within the year.  Bundled so the section helpers and their
    per-account workers take one cohesive context object instead of four
    separate positional parameters.
    """

    year: int
    scenario: Scenario
    all_periods: list[PayPeriod]
    year_period_ids: list[int]
