"""
Shekel Budget App -- Loan route package: detail dashboard.

The loan detail page (GET): summary card, payment breakdown, multi-scenario
balance chart, escrow / rate-history panels, amortization-schedule tab, and the
recurring-transfer prompt.  The route assembles its template context by merging
the per-section dicts the private helpers below return; the recurrence
end_date sync (a deliberate write on a GET, R-4) also lives here because the
dashboard is where the payoff date is computed with full payment context.
"""

import dataclasses
from datetime import date
from decimal import Decimal, ROUND_DOWN

from flask import abort, render_template
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.services.recurring_transfer_query import (
    active_recurring_transfer_template,
)
from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _build_chart_series,
    _load_loan_account,
    _load_loan_context,
    build_schedule_context,
)
from app.services import escrow_calculator, loan_resolver
from app.services.amortization_engine import AmortizationSummary
from app.services.loan_loaders import load_loan_anchor_facts
from app.services.loan_payment_service import confirmed_loan_view
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.auth_helpers import require_owner


def _find_current_period_row(schedule):
    """Find the schedule row for the current or next upcoming payment.

    Returns the first projected (non-confirmed) row if one exists,
    otherwise the last confirmed row.  Returns None for an empty
    schedule.

    This approach is more robust than date-based lookup because
    shadow transaction dates (biweekly) and schedule payment dates
    (monthly) use different calendars.  The confirmed/projected
    boundary is the cleanest split.

    Args:
        schedule: List of AmortizationRow objects.

    Returns:
        AmortizationRow or None.
    """
    if not schedule:
        return None
    for row in schedule:
        if not row.is_confirmed:
            return row
    # All rows confirmed -- use the last one.
    return schedule[-1]


def _distribute_payment_percentages(parts, total_payment):
    """Compute portion percentages that sum to exactly 100.0%.

    Truncate-then-distribute: each part is truncated to one decimal
    place (ROUND_DOWN), then the residual needed to reach 100.0% is
    assigned to the largest part.  Guarantees the percentages sum to
    exactly 100.0% regardless of per-part rounding.

    Args:
        parts: List of ``(name, amount)`` tuples (Decimal amounts).
        total_payment: Decimal sum of the part amounts; must be > 0.

    Returns:
        dict mapping each part name to its Decimal percentage.
    """
    one_decimal = Decimal("0.1")
    truncated = {}
    for name, amount in parts:
        raw_pct = amount / total_payment * 100
        truncated[name] = raw_pct.quantize(one_decimal, rounding=ROUND_DOWN)

    residual = Decimal("100.0") - sum(truncated.values())
    # Assign residual to the largest portion.
    largest = max(truncated, key=truncated.get)
    truncated[largest] += residual
    return truncated


def _project_next_year_escrow(escrow_components, escrow_portion):
    """Project next year's monthly escrow when a component inflates.

    O-3: if any component carries a non-null ``inflation_rate`` and the
    current escrow portion is positive, compute the Jan-1-next-year
    monthly escrow so the dashboard can show the user the projected
    change.

    Args:
        escrow_components: List of active EscrowComponent objects.
        escrow_portion: Decimal current monthly escrow.

    Returns:
        Decimal projected escrow when it differs from the current
        portion, otherwise None (no note shown).
    """
    has_inflation = any(
        getattr(c, "inflation_rate", None)
        for c in escrow_components
    )
    if not has_inflation or escrow_portion <= Decimal("0.00"):
        return None

    next_year_date = date(date.today().year + 1, 1, 1)
    next_year_escrow = escrow_calculator.calculate_monthly_escrow(
        escrow_components, as_of_date=next_year_date,
    )
    # Only show the note if next year differs from current.
    if next_year_escrow == escrow_portion:
        return None
    return next_year_escrow


def _compute_payment_breakdown(schedule, escrow_components):
    """Build payment allocation breakdown for the current period.

    Combines the amortization engine's per-period principal/interest
    split with the escrow calculator's monthly total to show the user
    exactly how their payment is allocated.

    Percentages are computed with a truncate-then-distribute algorithm
    to guarantee they sum to exactly 100.0%.

    Args:
        schedule: List of AmortizationRow objects (committed schedule).
        escrow_components: List of active EscrowComponent objects.

    Returns:
        dict with breakdown data, or None if no schedule data.
    """
    current_row = _find_current_period_row(schedule)
    if current_row is None:
        return None

    principal_portion = current_row.principal + current_row.extra_payment
    interest_portion = current_row.interest
    escrow_portion = escrow_calculator.calculate_monthly_escrow(
        escrow_components,
    )
    total_payment = principal_portion + interest_portion + escrow_portion

    if total_payment <= Decimal("0.00"):
        return None

    truncated = _distribute_payment_percentages(
        [
            ("principal", principal_portion),
            ("interest", interest_portion),
            ("escrow", escrow_portion),
        ],
        total_payment,
    )
    next_year_escrow = _project_next_year_escrow(
        escrow_components, escrow_portion,
    )

    return {
        "principal": principal_portion,
        "interest": interest_portion,
        "escrow": escrow_portion,
        "total": total_payment,
        "principal_pct": truncated["principal"],
        "interest_pct": truncated["interest"],
        "escrow_pct": truncated["escrow"],
        "is_confirmed": current_row.is_confirmed,
        "payment_date": current_row.payment_date,
        "next_year_escrow": next_year_escrow,
    }


def _build_dashboard_scenarios(loan_inputs, scenario_id, as_of):
    """Run the main + floor payoff-scenario composer calls for the dashboard.

    Commit 5 of the amortization-engine split: two
    ``compute_payoff_scenarios`` calls (replacing three direct
    ``generate_schedule`` calls) whose chart series and summary derive
    from the same return value so they cannot diverge (the structural
    fix documented at
    ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``).

    ``scenarios_main`` consumes ALL payments (confirmed + projected)
    with ``extra_monthly=0``: its ``history_rows + committed_forward``
    slice IS the planned trajectory the amortization tab, payment
    breakdown, schedule totals, recurrence end_date update, and summary
    all read.  ``scenarios_floor`` re-runs with the projected portion of
    ``payments`` filtered out, so its ``committed_forward`` is "pure
    contractual from balance_as_of" -- the floor's semantic of "where I
    stand if I cancel all extras today."  Both share the same
    ``anchor_events`` so a future trueup cannot drift between them.

    Read switch: reads the genesis-ledger confirmed view ONCE via
    :func:`loan_payment_service.confirmed_loan_view` and threads it into BOTH
    composer calls as ``confirmed_view``, so the chart / schedule tab /
    summary all derive from the same real owed balance AND ledger-derived
    confirmed history the loan card (:func:`._helpers._resolve`) shows --
    they cannot desync off-schedule.

    Args:
        loan_inputs: The loan's :class:`loan_resolver.LoanInputs` bundle with
            ALL payments (the main scenario); the floor derives its
            confirmed-only variant from it.
        scenario_id: The baseline scenario id (or ``None``) for the ledger
            seed scope.
        as_of: The replay/projection boundary (typically ``date.today()``).

    Returns:
        Tuple of (scenarios_main, scenarios_floor) PayoffScenarios.
    """
    view = confirmed_loan_view(
        loan_inputs.loan_params.account_id, scenario_id, as_of,
    )
    scenarios_main = loan_resolver.compute_payoff_scenarios(
        loan_inputs=loan_inputs,
        extra_monthly=Decimal("0.00"),
        as_of=as_of,
        confirmed_view=view,
    )
    confirmed_payments = [
        p for p in (loan_inputs.payments or []) if p.is_confirmed
    ]
    scenarios_floor = loan_resolver.compute_payoff_scenarios(
        loan_inputs=dataclasses.replace(
            loan_inputs, payments=confirmed_payments,
        ),
        extra_monthly=Decimal("0.00"),
        as_of=as_of,
        confirmed_view=view,
    )
    return scenarios_main, scenarios_floor


def _build_planned_summary(state, planned_schedule, params):
    """Build the life-of-loan AmortizationSummary from the planned schedule.

    monthly_payment comes from the resolver (single source of truth);
    total_interest / payoff_date are summed/read over ``planned_schedule``
    (history + forward) so the "Total Interest (life of loan)" and
    "Projected Payoff" cards reflect the user's full trajectory.  The
    composer's ``total_interest_committed`` covers the forward slice
    only; summing over ``planned_schedule`` adds back the history-row
    interest the dashboard has always displayed.

    Args:
        state: Resolver :class:`LoanState` (monthly_payment source).
        planned_schedule: history + committed-forward AmortizationRows.
        params: ORM :class:`LoanParams` (origination fallback date).

    Returns:
        :class:`AmortizationSummary` (no acceleration: with-extra fields
        mirror the base fields, months/interest saved zero).
    """
    planned_total_interest = sum(
        (row.interest for row in planned_schedule), Decimal("0.00"),
    )
    planned_payoff_date = (
        planned_schedule[-1].payment_date if planned_schedule
        else params.origination_date
    )
    return AmortizationSummary(
        monthly_payment=state.monthly_payment,
        total_interest=planned_total_interest,
        payoff_date=planned_payoff_date,
        total_interest_with_extra=planned_total_interest,
        payoff_date_with_extra=planned_payoff_date,
        months_saved=0,
        interest_saved=Decimal("0.00"),
    )


def _build_payment_summary(state, summary, planned_schedule, escrow_components):
    """Build the loan-card payment-summary template context.

    Bundles the resolver-derived current balance, the total monthly
    payment (P&I + escrow), the current-period payment breakdown, and
    the escrow display list.  The life-of-loan ``summary`` is built by
    the caller (it is also needed for the recurrence end_date sync) and
    passed in for its ``monthly_payment``.  The payment breakdown uses
    the planned schedule so it reflects the next planned payment, not
    the contractual one when the user is under-/over-paying.

    Returns:
        dict of template vars: current_principal_display, total_payment,
        payment_breakdown, escrow_components (display list).
    """
    return {
        # E-18 / Commit 15: resolver-derived; equals the /savings debt
        # card balance and the net-worth liability.
        "current_principal_display": state.current_balance,
        "total_payment": escrow_calculator.calculate_total_payment(
            summary.monthly_payment, escrow_components,
        ),
        "payment_breakdown": _compute_payment_breakdown(
            planned_schedule, escrow_components,
        ),
        "escrow_components": escrow_calculator.build_escrow_display(
            escrow_components,
        ),
    }


def _build_dashboard_chart_context(scenarios_main, scenarios_floor, has_payments):
    """Build the dashboard's multi-scenario chart template context.

    Three series share the x-axis (see :func:`_build_chart_series`):
    Original (history + original_forward, pure contractual) and
    Committed (history + committed_forward, planned outlays) come from
    the main scenario; Floor (history + committed_forward) comes from
    the floor scenario (projections cancelled).  Committed and Floor
    render empty when the loan has no payments (the JS overlays just
    Original), preserving the pre-Commit-5 conditional behavior.

    Returns:
        dict of template vars: chart_labels, chart_original,
        chart_committed, chart_floor, has_payments.
    """
    chart_labels, balances = _build_chart_series({
        "original": (
            scenarios_main.history_rows + scenarios_main.original_forward
        ),
        "committed": (
            scenarios_main.history_rows + scenarios_main.committed_forward
        ),
        "floor": (
            scenarios_floor.history_rows + scenarios_floor.committed_forward
        ),
    })
    return {
        "chart_labels": chart_labels,
        "chart_original": balances["original"],
        "chart_committed": balances["committed"] if has_payments else [],
        "chart_floor": balances["floor"] if has_payments else [],
        "has_payments": has_payments,
    }


def _resolve_transfer_prompt(account):
    """Resolve the recurring-transfer prompt state for the dashboard.

    The prompt shows when LoanParams exist but no active recurring
    transfer template targets this account.  When shown, the eligible
    source accounts (active, non-amortizing, excluding this account) and
    the default source (the checking account, if any) are loaded.

    Returns:
        ``prompt_context`` -- a dict of template vars: show_transfer_prompt,
        source_accounts, default_source_id.
    """
    existing_template = active_recurring_transfer_template(
        account.id, current_user.id,
    )
    if existing_template is not None:
        return {
            "show_transfer_prompt": False,
            "source_accounts": [],
            "default_source_id": None,
        }

    source_accounts = (
        db.session.query(Account)
        .join(AccountType)
        .filter(
            Account.user_id == current_user.id,
            Account.is_active.is_(True),
            Account.id != account.id,
            AccountType.has_amortization.is_(False),
        )
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    # Default to the checking account if one exists.
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    default_source_id = next(
        (acct.id for acct in source_accounts
         if acct.account_type_id == checking_type_id),
        None,
    )
    return {
        "show_transfer_prompt": True,
        "source_accounts": source_accounts,
        "default_source_id": default_source_id,
    }


def _load_collateral_candidates(user_id):
    """Return the user's active Property accounts for the secured-by picker.

    The loan dashboard's "Secured by" picker offers the physical assets a
    loan can be secured by (``has_appreciation`` types, i.e. Property), so a
    mortgage / HELOC can be grouped with the home it is secured by and
    equity rendered.  Empty when the user has none -- the template then
    shows a "create a property first" prompt instead of an empty dropdown.

    Args:
        user_id: ``auth.users.id`` of the current owner.

    Returns:
        list[Account]: active Property accounts, ordered for display.
    """
    return (
        db.session.query(Account)
        .join(AccountType)
        .filter(
            Account.user_id == user_id,
            Account.is_active.is_(True),
            AccountType.has_appreciation.is_(True),
        )
        .order_by(Account.sort_order, Account.name)
        .all()
    )


@loan_bp.route("/accounts/<int:account_id>/loan")
@login_required
@require_owner
def dashboard(account_id):
    """Loan detail page with summary, escrow, rate history, and payoff calculator."""
    account, params, account_type = _load_loan_account(account_id)
    if account is None:
        abort(404)

    if params is None:
        return render_template(
            "loan/setup.html",
            account=account,
            account_type=account_type,
        )

    ctx = _load_loan_context(account, params)
    scenario = get_baseline_scenario(current_user.id)
    loan_inputs = loan_resolver.LoanInputs(
        loan_params=params,
        anchor_events=load_loan_anchor_facts(params),
        payments=ctx.loan.payments,
        rate_changes=ctx.loan.rate_changes,
    )
    scenarios_main, scenarios_floor = _build_dashboard_scenarios(
        loan_inputs, scenario.id if scenario else None, date.today(),
    )
    # PLANNED-trajectory schedule: real confirmed history + projected /
    # contractual forward.  The loan card's current_balance and the
    # forward projection here both seed from the SAME genesis-ledger
    # balance (plan Section 8), so the card / debt card / net-worth
    # liability and the chart cannot diverge (the E-18 invariant).
    planned_schedule = (
        scenarios_main.history_rows + scenarios_main.committed_forward
    )
    summary = _build_planned_summary(ctx.state, planned_schedule, params)

    # R-4: the recurring transfer's end_date is NO LONGER written here (a write
    # on a GET).  It is synced to the projected payoff at every payoff-affecting
    # mutation instead (:mod:`app.services.loan_recurrence_sync`), so this GET is
    # read-only.
    prompt_context = _resolve_transfer_prompt(account)

    context = {
        "account": account,
        "account_type": account_type,
        "params": params,
        "summary": summary,
        "rate_history": ctx.loan.rate_history,
        # DH-#56: the rate columns the dashboard displays/edits, derived
        # from the resolver / RateHistory (the retired
        # ``LoanParams.interest_rate`` is gone).  ``current_rate`` is the
        # rate in effect today (the card display); ``origination_rate`` is
        # the loan's earliest RateHistory row -- the period-0 rate the
        # "Loan Parameters" form edits (and ``update_params`` upserts).
        # ``rate_history`` is ordered effective_date DESC, so the last
        # element is the earliest (origination) row; it is guaranteed
        # non-empty here because ``_load_loan_context`` already resolved
        # the loan (raising if no origination row exists).
        "current_rate": ctx.state.current_rate,
        "origination_rate": ctx.loan.rate_history[-1].interest_rate,
        "monthly_escrow": ctx.loan.monthly_escrow,
        # E-18 / Commit 16: today's ISO date pre-fills the "Record Loan
        # Balance" form's as-of date and caps its ``max``.  Computed here
        # (not via a Jinja global) so a test that freezes ``date.today()``
        # sees the frozen value on the page.
        "today_iso": date.today().isoformat(),
        # Home-equity link: the Property accounts this loan can be secured
        # by drive the "Secured by" picker; the current selection is read
        # off ``account.collateral_account_id`` in the template.
        "collateral_candidates": _load_collateral_candidates(current_user.id),
    }
    context.update(_build_payment_summary(
        ctx.state, summary, planned_schedule, ctx.loan.escrow_components,
    ))
    context.update(_build_dashboard_chart_context(
        scenarios_main, scenarios_floor, len(ctx.loan.payments) > 0,
    ))
    context.update(prompt_context)
    context.update(build_schedule_context(
        planned_schedule, ctx.loan.monthly_escrow, ctx.current_rate, params,
    ))
    return render_template("loan/dashboard.html", **context)
