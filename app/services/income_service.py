"""
Shekel Budget App -- Income Service (F-20 / MED-06 / F-032).

Single source of truth for the raise-aware per-period gross income
quantity that every income-derived dashboard surface needs.  Wraps
:func:`paycheck_calculator.calculate_paycheck` so the engine's
:class:`~app.services.paycheck_calculator.PaycheckBreakdown.gross_biweekly`
is the canonical value -- never the off-engine
``Decimal(str(profile.annual_salary)) / pay_periods_per_year``
recompute that silently dropped any applicable
:class:`~app.models.salary_raise.SalaryRaise` row pre-Commit-17.

Pre-fix, six call sites read the off-engine quantity:

- ``savings_dashboard_service._load_account_params``
- ``year_end_summary_service._load_salary_gross_biweekly``
- ``retirement_dashboard_service.compute_gap_data`` (projected-salary path)
- ``retirement_dashboard_service._project_retirement_accounts``
- ``investment_dashboard_service._salary_gross_biweekly``

For users with applicable raises, those quantities drifted from the
paycheck engine's per-period gross by the raise factor -- the audit's
F-032 worked example: a $104,000 base with a 3% recurring raise showed
``$4,000.00`` per period off-engine vs ``$4,120.00`` from the engine,
which then under-stated the employer-match cap basis, the retirement
gap denominator, and the year-end employer / investment-growth totals
by the same factor.  Routing every consumer through this one helper
means the corrected income figure shows up uniformly.

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"):
this module imports no Flask symbol.  All inputs are plain data
(user id, optional scenario id, optional ``as_of`` date); the return
value is a :class:`~decimal.Decimal`.
"""

import logging
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.services import pay_period_service, paycheck_calculator
from app.services.tax_config_service import load_tax_configs

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


def get_current_gross_biweekly(
    user_id: int,
    *,
    scenario_id: int | None = None,
    as_of: date | None = None,
) -> Decimal:
    """Return the raise-aware per-period gross for the user's active salary profile.

    The canonical raise-aware income producer (F-20 / MED-06 / F-032):
    every dashboard surface that needs the current gross per pay period
    routes through this helper so the value cannot disagree with the
    paycheck engine for the same period.  Internally loads the user's
    active :class:`SalaryProfile`, resolves the pay period containing
    ``as_of`` (default: today), and invokes
    :func:`paycheck_calculator.calculate_paycheck` so any applicable
    :class:`~app.models.salary_raise.SalaryRaise` row is folded into
    the post-raise annual salary -- which the engine then divides by
    ``pay_periods_per_year`` and reconciles per
    :func:`paycheck_calculator._gross_biweekly_for_period`.

    Returning a single Decimal (rather than the full
    :class:`~app.services.paycheck_calculator.PaycheckBreakdown`)
    matches the producer shape every consumer wants: a snapshot
    "current per-period gross" value that downstream code feeds into
    investment / retirement / employer-match math.  Callers that
    already hold a :class:`PaycheckBreakdown` for the same period
    should read ``breakdown.gross_biweekly`` directly rather than
    re-invoking this helper (avoids re-querying tax configs and
    re-running the engine for an identical result).

    Args:
        user_id: ID of the user whose active salary profile to load.
        scenario_id: Optional scenario filter.  When provided, only a
            ``SalaryProfile`` whose ``scenario_id`` matches is
            returned -- year-end consumers pass this to scope to the
            same scenario they aggregate against.  When ``None``, the
            filter is omitted and the user's first ``is_active=True``
            profile across all scenarios is used (the historical
            savings / retirement / investment dashboard behavior).
        as_of: Optional date for which to compute the gross.  Defaults
            to today.  Passed to
            :func:`pay_period_service.get_current_period` for period
            resolution.

    Returns:
        The paycheck engine's
        :attr:`~app.services.paycheck_calculator.PaycheckBreakdown.gross_biweekly`
        for the resolved profile + period.  Returns ``Decimal("0")``
        when the user has no active salary profile or no pay period
        covers ``as_of`` -- both pre-fix call sites returned
        ``Decimal("0")`` for the missing-profile branch, so the
        substitute preserves the contract.
    """
    query = (
        db.session.query(SalaryProfile)
        .filter(
            SalaryProfile.user_id == user_id,
            SalaryProfile.is_active.is_(True),
        )
    )
    if scenario_id is not None:
        query = query.filter(SalaryProfile.scenario_id == scenario_id)
    profile = query.first()
    if profile is None:
        return ZERO

    as_of_date = as_of or date.today()
    current_period = pay_period_service.get_current_period(
        user_id, as_of=as_of_date,
    )
    if current_period is None:
        return ZERO

    all_periods = pay_period_service.get_all_periods(user_id)
    tax_configs = load_tax_configs(user_id, profile)
    breakdown = paycheck_calculator.calculate_paycheck(
        profile, current_period, all_periods, tax_configs,
    )
    return breakdown.gross_biweekly
