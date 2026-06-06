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
from decimal import Decimal

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required
from marshmallow import ValidationError

from app.utils.auth_helpers import require_owner

from app.extensions import db
from app.models.account import Account
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_params import LoanParams
from app.models.ref import AccountType
from app.schemas.validation import DebtStrategyCalculateSchema
from app.services import loan_resolver
from app.services.debt_strategy_service import (
    DebtAccount,
    STRATEGY_AVALANCHE,
    STRATEGY_CUSTOM,
    STRATEGY_SNOWBALL,
    StrategyRequest,
    calculate_strategy,
)
from app.services.loan_payment_service import load_loan_context
from app.services.scenario_resolver import get_baseline_scenario

logger = logging.getLogger(__name__)

debt_strategy_bp = Blueprint("debt_strategy", __name__)

# Single Marshmallow schema instance per process -- safe for concurrent
# use by Marshmallow contract.  Replaces the per-request hand-parsing
# at lines 234, 251, and 261 (pre-C-27) so ``extra_monthly``,
# ``strategy``, and ``custom_order`` get the same field-level Range,
# OneOf, and Length validators a JSON caller would.
_calculate_schema = DebtStrategyCalculateSchema()


# ---------------------------------------------------------------------------
# Shared data loading
# ---------------------------------------------------------------------------

def _load_debt_accounts(user_id):
    """Load all active debt accounts with loan parameters for a user.

    Queries accounts where the account type has ``has_amortization=True``,
    runs the loan resolver (E-18 / Commit 13) for each, and builds
    :class:`DebtAccount` instances from the resolver state.  The same
    ``current_balance`` / ``monthly_payment`` figures appear on the
    loan card, /savings debt card, and net-worth liability, so the
    strategy comparison cannot diverge from the per-loan displays
    (E-18 / Commit 15).

    Accounts without saved :class:`LoanParams` are silently skipped --
    a user created a debt account but has not yet filled in the loan
    details.  Accounts with zero resolver-derived balance OR zero
    resolver-derived monthly payment are also skipped (paid-off or
    degenerate).

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

    scenario = get_baseline_scenario(user_id)
    scenario_id = scenario.id if scenario else None
    today = date.today()

    debt_accounts = []
    has_arm = False

    # The per-account "load LoanParams, skip if unconfigured" preamble
    # below coincides with the same generic SQLAlchemy idiom in
    # ``year_end_summary_service``; the extracted body would be identical
    # to the inline form, and the two consumers are unrelated domains
    # (debt-strategy view vs year-end summary).  One-sided ``duplicate-code``
    # disable (coding-standards rule 13; see plan.md Phase 2 notes).
    # pylint: disable=duplicate-code
    for account in accounts:
        params = (
            db.session.query(LoanParams)
            .filter_by(account_id=account.id)
            .first()
        )
        if params is None:
            # Account exists but loan details not yet configured.
            continue
        # pylint: enable=duplicate-code

        if params.is_arm:
            has_arm = True

        anchor_events = (
            db.session.query(LoanAnchorEvent)
            .filter_by(account_id=account.id)
            .all()
        )
        ctx = load_loan_context(account.id, scenario_id, params)
        state = loan_resolver.resolve_loan(
            loan_resolver.LoanInputs(
                params, anchor_events, ctx.payments, ctx.rate_changes,
            ),
            today,
        )

        # Skip debts with zero principal or zero payment (fully paid
        # off or degenerate loan parameters).  Resolver-derived
        # values, so a settled-to-zero loan disappears here even if
        # the legacy ``LoanParams.current_principal`` column still
        # carries a non-zero seed.
        if (
            state.current_balance <= Decimal("0")
            or state.monthly_payment <= Decimal("0")
        ):
            continue

        # DebtAccount.interest_rate carries the BASE rate from
        # :class:`LoanParams`; the rate-history layered current rate
        # (resolver-aware) does not flow into the strategy service
        # because :mod:`debt_strategy_service` assumes a fixed rate
        # per debt (R-5 limitation documented in that module).
        # Promoting strategy ARM-awareness is out of scope here.
        debt_accounts.append(DebtAccount(
            account_id=account.id,
            name=account.name,
            current_principal=state.current_balance,
            interest_rate=Decimal(str(params.interest_rate)),
            minimum_payment=state.monthly_payment,
        ))

    return debt_accounts, has_arm


def _first_validation_message(exc):
    """Return the first user-facing message from a ``ValidationError``.

    ``DebtStrategyCalculateSchema`` raises Marshmallow's standard
    ``ValidationError`` which carries a nested ``messages`` dict.
    The HTMX UX renders a single-line error banner inside the
    ``_results.html`` partial, so this helper picks the first
    available message in deterministic order: the cross-field
    ``_schema`` key first (for the ``custom`` + missing
    ``custom_order`` case which is the most common user mistake),
    then the per-field messages in alphabetical order so two
    test runs see the same response.

    The fallback "Invalid input" handles a future schema change
    that produces an empty ``messages`` dict (Marshmallow itself
    has never done this in practice; the fallback is purely
    defensive against regressions).

    Args:
        exc: The ``ValidationError`` raised by ``schema.load``.

    Returns:
        A single user-facing string suitable for the
        ``_results.html`` ``error`` slot.
    """
    messages = exc.messages
    if isinstance(messages, dict):
        # ``_schema`` carries cross-field errors; surface those first
        # because they almost always describe the user-input problem
        # in domain terms ("Custom strategy requires a priority
        # order") rather than a field-coercion error.
        schema_msgs = messages.get("_schema")
        if schema_msgs:
            return _flatten_message(schema_msgs)
        # Otherwise pick the first field's first message in
        # alphabetical order so HTML form testing is deterministic.
        for field_name in sorted(messages):
            field_msgs = messages[field_name]
            flat = _flatten_message(field_msgs)
            if flat:
                return flat
    elif isinstance(messages, list) and messages:
        return _flatten_message(messages)
    return "Invalid input."


def _flatten_message(value):
    """Return the first scalar string from a Marshmallow error value.

    Marshmallow nests messages as ``list[str]`` for simple validators
    and ``dict[str, list[str]]`` for nested schemas.  This helper
    walks one level of nesting so a single-string result reaches the
    template regardless of the validator that produced it.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            flat = _flatten_message(item)
            if flat:
                return flat
        return ""
    if isinstance(value, dict):
        for key in sorted(value):
            flat = _flatten_message(value[key])
            if flat:
                return flat
        return ""
    return ""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@debt_strategy_bp.route("/debt-strategy")
@login_required
@require_owner
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
@require_owner
def calculate():
    """Compute debt payoff strategies and return comparison results.

    HTMX endpoint.  Returns a partial template with the comparison
    table and per-account payoff timeline.  Always computes three
    scenarios (no-extra baseline, avalanche, snowball).  When the
    user selects custom, a fourth scenario is added.

    Input validation runs through ``DebtStrategyCalculateSchema``
    (commit C-27 / F-040): ``extra_monthly`` is range-bounded,
    ``strategy`` is restricted to the three known constants, and
    the cross-field rule rejects ``custom`` without a
    ``custom_order``.  Schema errors render the legacy
    ``_results.html`` partial with the first per-field message so
    the existing HTMX UX -- inline error banner inside the form's
    results target -- is preserved.
    """
    # --- Parse and validate the form payload ---
    try:
        data = _calculate_schema.load(request.form)
    except ValidationError as exc:
        return render_template(
            "debt_strategy/_results.html",
            error=_first_validation_message(exc),
        )

    extra_monthly: Decimal = data["extra_monthly"]
    strategy: str = data["strategy"]
    custom_raw: str | None = data.get("custom_order")

    # --- Parse custom_order: schema validated presence/length only;
    # the per-element integer coercion lives here so a malformed
    # entry is reported as a user-friendly error rather than a
    # generic Marshmallow message. ---
    custom_order = None
    if strategy == STRATEGY_CUSTOM:
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
        baseline = calculate_strategy(StrategyRequest(
            debt_accounts, Decimal("0"), STRATEGY_AVALANCHE,
            start_date=today,
        ))
        avalanche = calculate_strategy(StrategyRequest(
            debt_accounts, extra_monthly, STRATEGY_AVALANCHE,
            start_date=today,
        ))
        snowball = calculate_strategy(StrategyRequest(
            debt_accounts, extra_monthly, STRATEGY_SNOWBALL,
            start_date=today,
        ))
    except ValueError as exc:
        logger.warning("Strategy calculation failed: %s", exc)
        return render_template(
            "debt_strategy/_results.html",
            error=str(exc),
        )

    custom_result = None
    if strategy == STRATEGY_CUSTOM and custom_order is not None:
        try:
            custom_result = calculate_strategy(StrategyRequest(
                debt_accounts, extra_monthly, STRATEGY_CUSTOM,
                custom_order=custom_order, start_date=today,
            ))
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
