"""
Shekel Budget App -- Income Service (F-20 / MED-06 / F-032).

Single source of truth for the raise-aware per-period gross income
quantity that every income-derived dashboard surface needs.  Wraps
:func:`paycheck_calculator.calculate_paycheck` so the engine's
:attr:`~app.services.paycheck_calculator.Earnings.gross_biweekly`
(``breakdown.earnings.gross_biweekly``) is the canonical value -- never the off-engine
``Decimal(str(profile.annual_salary)) / pay_periods_per_year``
recompute that silently dropped any applicable
:class:`~app.models.salary_raise.SalaryRaise` row pre-Commit-17.

Pre-fix, six call sites read the off-engine quantity:

- ``savings_dashboard_service._data._load_account_params``
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
from app.services.tax_config_service import (
    load_tax_configs,
    load_tax_configs_for_periods,
)
from app.utils.balance_predicates import is_projected

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
    should read ``breakdown.earnings.gross_biweekly`` directly rather than
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
        :attr:`~app.services.paycheck_calculator.Earnings.gross_biweekly`
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
    return breakdown.earnings.gross_biweekly


def live_projected_net(
    user_id: int,
    scenario_id: int,
    transactions: list,
) -> dict[int, Decimal]:
    """Return ``{transaction_id: live_net_pay}`` for salary-linked Projected income.

    The read-time analogue of the recurrence engine's generation-time
    amount: for every Projected, non-overridden income transaction in
    ``transactions`` whose template is linked to an active
    :class:`SalaryProfile` in ``scenario_id``, recompute the net
    paycheck LIVE from that profile -- the same path the salary
    projection page uses (:func:`paycheck_calculator.project_salary`
    over the full pay-period set, tax configs resolved PER period year --
    the same per-year resolution the recurrence engine uses to GENERATE
    the stored amount (DH-#30) -- the profile's calibration).  The result
    lets a balance/display consumer
    treat the stored ``Transaction.estimated_amount`` as a cache that
    cannot silently disagree with the salary page after a profile,
    calibration, or financial-calc CODE change that did not fire a
    regeneration (the staleness gap that shipped the SS regression).

    Only transactions that are ALL of:

      * income (:attr:`Transaction.is_income`),
      * Projected (:func:`~app.utils.balance_predicates.is_projected` --
        Received / Settled income carries a realized ``actual_amount``
        that is a historical fact, never a recomputable projection),
      * NOT user-overridden (``is_override`` -- a manual amount the user
        deliberately set is respected, mirroring the recurrence engine),
      * linked to a template that maps to an active ``SalaryProfile`` in
        ``scenario_id``,

    appear in the result.  Every other transaction is omitted, so a
    caller's ``overrides.get(txn.id, txn.effective_amount)`` falls back
    to the stored value for non-salary income, overridden rows, and
    expenses.

    Boundary discipline: no Flask import; inputs are plain data
    (ids + an already-loaded transaction list), output is a plain dict.
    The full-period ``project_salary`` call is what makes the per-period
    gross reconcile exactly (:func:`paycheck_calculator._gross_biweekly_for_period`),
    so callers pass the transactions they have loaded -- this helper
    sources the canonical full pay-period set itself.

    Args:
        user_id: Owning user; scopes the SalaryProfile and pay-period
            queries.
        scenario_id: Scenario to resolve salary profiles against; the
            grid and every balance surface are scenario-scoped, and a
            profile drives income only within its own scenario.
        transactions: Already-loaded :class:`Transaction` rows (the
            caller's balance or display set).  Each must expose
            ``is_income``, ``status`` (for ``is_projected``),
            ``is_override``, ``template_id``, ``pay_period_id``, ``id``.

    Returns:
        ``dict`` mapping transaction id to the live net pay
        (:class:`~decimal.Decimal`) for the transaction's period.
        Empty when there are no salary-linked Projected income rows --
        the common case for non-salary accounts and expense-only sets,
        and a fast no-op (no query) when no candidate rows exist.
    """
    candidates = [
        txn for txn in transactions
        if txn.is_income
        and is_projected(txn)
        and not txn.is_override
        and txn.template_id is not None
    ]
    if not candidates:
        return {}

    template_ids = {txn.template_id for txn in candidates}
    profiles = (
        db.session.query(SalaryProfile)
        .filter(
            SalaryProfile.user_id == user_id,
            SalaryProfile.scenario_id == scenario_id,
            SalaryProfile.is_active.is_(True),
            SalaryProfile.template_id.in_(template_ids),
        )
        .all()
    )
    profile_by_template = {p.template_id: p for p in profiles}
    if not profile_by_template:
        return {}

    # Compute each profile's live per-period net ONCE.  The full
    # pay-period set is required so the biweekly residue reconciliation
    # anchors against the complete annual figure, exactly as the salary
    # projection page does.
    all_periods = pay_period_service.get_all_periods(user_id)
    net_by_period_per_profile: dict[int, dict[int, Decimal]] = {}
    for profile in profile_by_template.values():
        # Resolve tax configs PER period year (DH-#30) so this live
        # recompute uses each period's own year's brackets/FICA -- exactly
        # as the recurrence engine does when it GENERATES the stored grid
        # amount.  A single current-year dict (the pre-fix behaviour) made
        # the two silently disagree once future-year configs existed,
        # breaking the reconciliation contract this helper advertises.
        configs_by_year = load_tax_configs_for_periods(
            user_id, profile, all_periods,
        )
        breakdowns = paycheck_calculator.project_salary(
            profile, all_periods, configs_by_year=configs_by_year,
            calibration=profile.calibration,
        )
        net_by_period_per_profile[profile.id] = {
            bd.period.period_id: bd.earnings.net_pay for bd in breakdowns
        }

    overrides: dict[int, Decimal] = {}
    for txn in candidates:
        profile = profile_by_template.get(txn.template_id)
        if profile is None:
            continue
        net = net_by_period_per_profile[profile.id].get(txn.pay_period_id)
        if net is not None:
            overrides[txn.id] = net
    return overrides
