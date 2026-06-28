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
    """Pre-loaded parameter maps the savings-progress helpers project against.

    Bundles the parameter maps loaded once in :func:`_load_common_data`
    so the savings-progress helpers forward a single cohesive object down
    the projection call chain instead of the parallel keyword arguments
    they previously threaded by hand (MED-01 / S6-06).

    The savings-progress chain reads ``interest_params_map`` /
    ``investment_params_map`` (the per-account dispatch) and the
    investment growth inputs ``deductions_by_account`` /
    ``salary_gross_biweekly``.  The net-worth section no longer reads this
    bundle: it routes through the :mod:`app.services.balance_at` seam,
    which owns its own input assembly -- including the debt schedules this
    bundle used to carry for the now-removed amortization-dispatch path.
    """

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
