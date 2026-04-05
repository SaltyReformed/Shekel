"""
Shekel Budget App -- Debt Strategy Routes

Cross-account debt payoff strategy calculator.  Loads all active debt
accounts with loan parameters, computes current real principal and
minimum payments, then calls the debt_strategy_service to simulate
snowball, avalanche, and custom payoff strategies.

The GET endpoint renders the strategy form.  The POST endpoint
(HTMX) returns a comparison partial with aggregate metrics and
per-account payoff timelines.
"""

import json
import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from app.extensions import db
from app.models.account import Account
from app.models.loan_params import LoanParams
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.services import amortization_engine
from app.services.debt_strategy_service import (
    DebtAccount,
    STRATEGY_AVALANCHE,
    STRATEGY_CUSTOM,
    STRATEGY_SNOWBALL,
    calculate_strategy,
)
from app.services.loan_payment_service import get_payment_history

logger = logging.getLogger(__name__)

debt_strategy_bp = Blueprint("debt_strategy", __name__)

# Strategies accepted by the form.  Matches debt_strategy_service constants.
_VALID_STRATEGIES = frozenset({STRATEGY_AVALANCHE, STRATEGY_SNOWBALL, STRATEGY_CUSTOM})


# ---------------------------------------------------------------------------
# Shared data loading
# ---------------------------------------------------------------------------

def _load_debt_accounts(user_id):
    """Load all active debt accounts with loan parameters for a user.

    Queries accounts where the account type has ``has_amortization=True``,
    loads LoanParams and payment history, computes current real principal
    (from confirmed payment replay), and builds DebtAccount instances.

    Accounts without saved LoanParams are silently skipped -- this
    happens when a user creates a debt account but has not yet filled
    in the loan details.

    The current real principal is derived by replaying confirmed payments
    through the amortization engine schedule.  The remaining_balance of
    the last confirmed row reflects what the borrower actually owes.  If
    no confirmed payments exist, the stored current_principal from
    LoanParams is used as the fallback.

    Args:
        user_id: The authenticated user's ID.

    Returns:
        Tuple of (debt_accounts, has_arm) where:
            debt_accounts: list[DebtAccount] ready for calculate_strategy().
            has_arm: bool indicating whether any loaded account is ARM.
    """
    accounts = (
        db.session.query(Account)
        .join(Account.account_type)
        .filter(
            Account.user_id == user_id,
            Account.is_active.is_(True),
            AccountType.has_amortization.is_(True),
        )
        .order_by(Account.sort_order, Account.name)
        .all()
    )

    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=user_id, is_baseline=True)
        .first()
    )

    debt_accounts = []
    has_arm = False

    for account in accounts:
        params = (
            db.session.query(LoanParams)
            .filter_by(account_id=account.id)
            .first()
        )
        if params is None:
            # Account exists but loan details not yet configured.
            continue

        principal = Decimal(str(params.current_principal))
        rate = Decimal(str(params.interest_rate))
        remaining = amortization_engine.calculate_remaining_months(
            params.origination_date, params.term_months,
        )

        if params.is_arm:
            has_arm = True

        # Derive current real principal from confirmed payment replay.
        if scenario is not None and principal > Decimal("0") and remaining > 0:
            real_principal = _compute_real_principal(
                params, scenario.id, principal, rate, remaining,
            )
        else:
            real_principal = principal

        # Compute minimum monthly P&I payment.
        minimum_payment = amortization_engine.calculate_monthly_payment(
            real_principal, rate, remaining,
        )

        # Skip debts with zero principal or zero payment (fully paid off
        # or degenerate loan parameters).
        if real_principal <= Decimal("0") or minimum_payment <= Decimal("0"):
            continue

        debt_accounts.append(DebtAccount(
            account_id=account.id,
            name=account.name,
            current_principal=real_principal,
            interest_rate=rate,
            minimum_payment=minimum_payment,
        ))

    return debt_accounts, has_arm


def _compute_real_principal(params, scenario_id, principal, rate, remaining):
    """Derive real principal by replaying confirmed payments.

    Generates an amortization schedule with actual payment history and
    returns the remaining_balance of the last confirmed row.  Falls
    back to the stored current_principal if no confirmed payments exist.

    This matches the pattern in loan.py refinance_calculate (lines
    1138-1146).

    Args:
        params: LoanParams model instance.
        scenario_id: Baseline scenario ID for payment lookup.
        principal: Decimal current_principal from LoanParams.
        rate: Decimal interest_rate from LoanParams.
        remaining: int remaining months on the loan.

    Returns:
        Decimal real principal reflecting confirmed payments.
    """
    payments = get_payment_history(params.account_id, scenario_id)
    if not payments:
        return principal

    # For ARM loans, force re-amortization from current principal.
    original_for_engine = (
        None if params.is_arm
        else Decimal(str(params.original_principal))
    )

    schedule = amortization_engine.generate_schedule(
        current_principal=principal,
        annual_rate=rate,
        remaining_months=remaining,
        origination_date=params.origination_date,
        payment_day=params.payment_day,
        original_principal=original_for_engine,
        term_months=params.term_months,
        payments=payments,
    )

    # Walk backward to find the last confirmed row.
    for row in reversed(schedule):
        if row.is_confirmed:
            return row.remaining_balance

    return principal


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@debt_strategy_bp.route("/debt-strategy")
@login_required
def dashboard():
    """Render the debt strategy comparison page.

    Loads all active debt accounts with loan parameters, computes
    current real principal and minimum payment for each, and renders
    the strategy form.  An empty-state message is shown when no debt
    accounts exist.
    """
    debt_accounts, has_arm = _load_debt_accounts(current_user.id)

    return render_template(
        "debt_strategy/dashboard.html",
        debt_accounts=debt_accounts,
        has_arm=has_arm,
    )


@debt_strategy_bp.route("/debt-strategy/calculate", methods=["POST"])
@login_required
def calculate():
    """Compute debt payoff strategies and return comparison results.

    HTMX endpoint.  Returns a partial template with the comparison
    table and per-account payoff timeline.  Always computes three
    scenarios (no-extra baseline, avalanche, snowball).  When the
    user selects custom, a fourth scenario is added.
    """
    # --- Parse extra_monthly ---
    extra_raw = request.form.get("extra_monthly", "0").strip()
    try:
        extra_monthly = Decimal(extra_raw)
    except InvalidOperation:
        return render_template(
            "debt_strategy/_results.html",
            error=f"Invalid extra monthly amount: {extra_raw!r}. "
                  "Enter a number like 200 or 200.00.",
        )

    if extra_monthly < Decimal("0"):
        return render_template(
            "debt_strategy/_results.html",
            error="Extra monthly amount cannot be negative.",
        )

    # --- Parse strategy ---
    strategy = request.form.get("strategy", STRATEGY_AVALANCHE).strip()
    if strategy not in _VALID_STRATEGIES:
        return render_template(
            "debt_strategy/_results.html",
            error=f"Invalid strategy: {strategy!r}.",
        )

    # --- Parse custom order ---
    custom_order = None
    if strategy == STRATEGY_CUSTOM:
        custom_raw = request.form.get("custom_order", "").strip()
        if not custom_raw:
            return render_template(
                "debt_strategy/_results.html",
                error="Custom strategy requires a priority order.",
            )
        try:
            custom_order = [int(x.strip()) for x in custom_raw.split(",")]
        except ValueError:
            return render_template(
                "debt_strategy/_results.html",
                error="Invalid custom order format.",
            )

    # --- Re-load debt accounts (safe, no stale data) ---
    debt_accounts, has_arm = _load_debt_accounts(current_user.id)

    if not debt_accounts:
        return render_template(
            "debt_strategy/_results.html",
            error="No active debt accounts found.",
        )

    # --- IDOR check for custom order ---
    if custom_order is not None:
        valid_ids = {d.account_id for d in debt_accounts}
        for aid in custom_order:
            if aid not in valid_ids:
                return "Not found", 404

    # --- Compute strategies ---
    today = date.today()

    try:
        baseline = calculate_strategy(
            debt_accounts, Decimal("0"), STRATEGY_AVALANCHE,
            start_date=today,
        )
        avalanche = calculate_strategy(
            debt_accounts, extra_monthly, STRATEGY_AVALANCHE,
            start_date=today,
        )
        snowball = calculate_strategy(
            debt_accounts, extra_monthly, STRATEGY_SNOWBALL,
            start_date=today,
        )
    except ValueError as exc:
        logger.warning("Strategy calculation failed: %s", exc)
        return render_template(
            "debt_strategy/_results.html",
            error=str(exc),
        )

    custom_result = None
    if strategy == STRATEGY_CUSTOM and custom_order is not None:
        try:
            custom_result = calculate_strategy(
                debt_accounts, extra_monthly, STRATEGY_CUSTOM,
                custom_order=custom_order, start_date=today,
            )
        except ValueError as exc:
            logger.warning("Custom strategy calculation failed: %s", exc)
            return render_template(
                "debt_strategy/_results.html",
                error=str(exc),
            )

    # --- Derive comparison metrics ---
    comparison = _build_comparison(baseline, avalanche, snowball, custom_result)

    # --- Determine which result to show in the per-account timeline ---
    if strategy == STRATEGY_CUSTOM and custom_result is not None:
        selected_result = custom_result
    elif strategy == STRATEGY_SNOWBALL:
        selected_result = snowball
    else:
        selected_result = avalanche

    # --- Prepare chart data for the selected strategy ---
    chart_data_json = _prepare_chart_data(selected_result, today)

    return render_template(
        "debt_strategy/_results.html",
        comparison=comparison,
        baseline=baseline,
        avalanche=avalanche,
        snowball=snowball,
        custom_result=custom_result,
        selected_result=selected_result,
        selected_strategy=strategy,
        extra_monthly=extra_monthly,
        has_arm=has_arm,
        debt_accounts=debt_accounts,
        chart_data_json=chart_data_json,
    )


def _build_comparison(baseline, avalanche, snowball, custom_result):
    """Build the comparison metrics dict for the template.

    Computes interest saved and months saved relative to the no-extra
    baseline for each strategy.

    Args:
        baseline: StrategyResult with extra_monthly=0.
        avalanche: StrategyResult with user's extra, avalanche strategy.
        snowball: StrategyResult with user's extra, snowball strategy.
        custom_result: StrategyResult for custom strategy, or None.

    Returns:
        Dict with keys for each column in the comparison table.
    """
    return {
        "baseline": {
            "debt_free_date": baseline.debt_free_date,
            "total_interest": baseline.total_interest,
            "total_paid": baseline.total_paid,
            "total_months": baseline.total_months,
            "interest_saved": Decimal("0.00"),
            "months_saved": 0,
        },
        "avalanche": {
            "debt_free_date": avalanche.debt_free_date,
            "total_interest": avalanche.total_interest,
            "total_paid": avalanche.total_paid,
            "total_months": avalanche.total_months,
            "interest_saved": baseline.total_interest - avalanche.total_interest,
            "months_saved": baseline.total_months - avalanche.total_months,
        },
        "snowball": {
            "debt_free_date": snowball.debt_free_date,
            "total_interest": snowball.total_interest,
            "total_paid": snowball.total_paid,
            "total_months": snowball.total_months,
            "interest_saved": baseline.total_interest - snowball.total_interest,
            "months_saved": baseline.total_months - snowball.total_months,
        },
        "custom": {
            "debt_free_date": custom_result.debt_free_date,
            "total_interest": custom_result.total_interest,
            "total_paid": custom_result.total_paid,
            "total_months": custom_result.total_months,
            "interest_saved": baseline.total_interest - custom_result.total_interest,
            "months_saved": baseline.total_months - custom_result.total_months,
        } if custom_result is not None else None,
    }


def _prepare_chart_data(result, start_date):
    """Serialize strategy balance timelines to JSON for Chart.js.

    Converts Decimal balance_timeline values to floats (presentation
    boundary -- matching loan.py _build_chart_data pattern) and
    generates month labels from start_date.

    Returns None if there are no accounts or no months to chart.

    Args:
        result: The StrategyResult for the selected strategy.
        start_date: The first month of the projection.

    Returns:
        JSON string for embedding in a data-* attribute, or None.
    """
    if not result.per_account or result.total_months == 0:
        return None

    # Generate "Mon YYYY" labels for each month in the timeline.
    # Index 0 = start_date (starting balance), index N = month N.
    labels = []
    for i in range(result.total_months + 1):
        total = start_date.month - 1 + i
        lbl_year = start_date.year + total // 12
        lbl_month = total % 12 + 1
        labels.append(date(lbl_year, lbl_month, 1).strftime("%b %Y"))

    # Build one dataset per account with floats (not Decimals).
    datasets = []
    for idx, acct in enumerate(result.per_account):
        # Presentation boundary: float() for Chart.js JSON serialization.
        data = [float(b) for b in acct.balance_timeline]
        datasets.append({
            "label": acct.name,
            "data": data,
            "colorIndex": idx,
        })

    return json.dumps({"labels": labels, "datasets": datasets})
