"""
Shekel Budget App -- Year-End Summary: orchestrator.

The public entry point ``compute_year_end_summary`` plus the load-once
and section-assembly steps that drive every section helper.
"""

from app.models.scenario import Scenario
from app.services.scenario_resolver import get_baseline_scenario
from app.services.year_end_summary_service._balances import (
    _generate_debt_schedules,
)
from app.services.year_end_summary_service._data import _load_common_data
from app.services.year_end_summary_service._income_tax import (
    _compute_income_tax,
    _compute_mortgage_interest,
    _empty_income_tax,
)
from app.services.year_end_summary_service._net_worth import (
    _compute_debt_progress,
    _compute_net_worth,
    _empty_net_worth,
)
from app.services.year_end_summary_service._savings import (
    _compute_savings_progress,
)
from app.services.year_end_summary_service._spending import (
    _compute_payment_timeliness,
    _compute_spending_by_category,
)
from app.services.year_end_summary_service._transfers import (
    _compute_transfers_summary,
)
from app.services.year_end_summary_service._types import (
    _ProjectionInputs,
    _YearContext,
)


def compute_year_end_summary(user_id: int, year: int) -> dict:
    """Aggregate annual financial data for the specified calendar year.

    Loads common data once (baseline scenario, pay periods, accounts,
    salary profiles) then delegates to section helpers.  Each section
    degrades gracefully when its required data is missing.

    Args:
        user_id: The authenticated user's ID.
        year: The four-digit calendar year to summarize.

    Returns:
        dict with keys: income_tax, spending_by_category,
        transfers_summary, net_worth, debt_progress,
        savings_progress, payment_timeliness.
    """
    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        return _full_empty_summary()

    ctx = _load_common_data(user_id, year, scenario)
    return _build_summary(user_id, year, scenario, ctx)


def _build_summary(
    user_id: int, year: int, scenario: Scenario, ctx: dict,
) -> dict:
    """Compute each section and assemble the final summary dict.

    Generates amortization schedules once for all debt accounts and
    shares them across the mortgage-interest and debt-progress sections
    (the membership gate for the latter).  The net-worth section reads
    balances through the :mod:`app.services.balance_at` seam, which owns
    its own schedule assembly, so it is not fed these schedules.

    Args:
        user_id: The authenticated user's ID.
        year: The target calendar year.
        scenario: The user's baseline scenario.
        ctx: Common data from :func:`_load_common_data`.  This is the
            sanctioned W-052 load-once bag and stays whole at this
            top-level assembly site, where it is packed into the two
            cohesive bundles passed down the projection chains: the
            ``_ProjectionInputs`` parameter maps (the savings-progress
            section) and the ``_YearContext`` period/scenario context
            (MED-01 / S6-06 -- the section helpers below take those bundles
            instead of the opaque bag).

    Returns:
        Fully assembled year-end summary dict.
    """
    # Pre-compute amortization schedules with properly prepared payments
    # (escrow subtracted, biweekly overlaps redistributed).  Shared by the
    # mortgage-interest section and the debt-progress membership gate.
    debt_schedules = _generate_debt_schedules(
        ctx["debt_accounts"], scenario.id,
    )

    inputs = _ProjectionInputs(
        investment_params_map=ctx["investment_params_map"],
        interest_params_map=ctx["interest_params_map"],
        deductions_by_account=ctx["deductions_by_account"],
        salary_gross_biweekly=ctx["salary_gross_biweekly"],
    )
    year_ctx = _YearContext(
        year=year,
        scenario=scenario,
        all_periods=ctx["all_periods"],
        year_period_ids=ctx["year_period_ids"],
    )

    income_tax = _compute_income_tax(
        user_id, year, ctx["year_periods"], ctx["salary_profiles"],
    )
    mortgage_interest = _compute_mortgage_interest(year, debt_schedules)
    income_tax["mortgage_interest_total"] = mortgage_interest

    return {
        "income_tax": income_tax,
        "spending_by_category": _compute_spending_by_category(
            user_id, year, ctx["year_period_ids"], scenario.id,
        ),
        "transfers_summary": _compute_transfers_summary(
            user_id, year, ctx["year_period_ids"], scenario.id,
        ),
        "net_worth": _compute_net_worth(
            ctx["accounts"], year_ctx,
        ),
        "debt_progress": _compute_debt_progress(
            year, ctx["debt_accounts"], debt_schedules, scenario,
        ),
        "savings_progress": _compute_savings_progress(
            ctx["savings_accounts"], year_ctx, inputs,
        ),
        "payment_timeliness": _compute_payment_timeliness(
            user_id, year, ctx["year_period_ids"], scenario.id,
        ),
    }


def _full_empty_summary() -> dict:
    """Return a complete summary with all sections empty/zero."""
    income_tax = _empty_income_tax()
    return {
        "income_tax": income_tax,
        "spending_by_category": [],
        "transfers_summary": [],
        "net_worth": _empty_net_worth(),
        "debt_progress": [],
        "savings_progress": [],
        "payment_timeliness": None,
    }
