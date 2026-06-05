"""
Shekel Budget App -- Loan route package: payoff + refinance calculators.

The HTMX what-if calculators: the payoff calculator (extra-payment and
target-date modes) and the refinance comparison.  Both load the shared loan
context so their "current" baseline matches the dashboard's loan card, and
both render result partials.  The payoff chart series reuses the shared
:func:`~app.routes.loan._helpers._build_chart_series` so it cannot diverge
from the dashboard's chart.
"""

from datetime import date
from decimal import Decimal, ROUND_CEILING

from flask import render_template, request
from flask_login import login_required

from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _build_chart_series,
    _load_anchor_events,
    _load_loan_account,
    _load_loan_context,
    _payoff_schema,
    _refinance_schema,
)
from app.services import amortization_engine, loan_resolver
from app.services.amortization_engine import AmortizationSummary
from app.utils.auth_helpers import require_owner
from app.utils.money import round_money


def _payoff_committed_savings(scenarios):
    """Months and interest the committed plan saves vs pure contractual.

    Committed-forward vs original-forward: both slices share the same
    replay starting state, so the difference quantifies what the user's
    planned outlays save vs paying pure contractual from today onward.
    This is the load-bearing single-source-of-truth invariant -- the
    chart's months_saved and the displayed label derive from the same
    forward-row lists.

    Returns:
        Tuple of (committed_months_saved int, committed_interest_saved
        Decimal; the latter routed through ``round_money`` so the
        half-cent boundary follows the project default ROUND_HALF_UP,
        E-26).
    """
    committed_months_saved = (
        len(scenarios.original_forward) - len(scenarios.committed_forward)
    )
    original_forward_interest = sum(
        (r.interest for r in scenarios.original_forward), Decimal("0.00"),
    )
    committed_forward_interest = sum(
        (r.interest for r in scenarios.committed_forward), Decimal("0.00"),
    )
    committed_interest_saved = round_money(
        original_forward_interest - committed_forward_interest,
    )
    return committed_months_saved, committed_interest_saved


def _build_payoff_summary(scenarios, state):
    """Assemble the AmortizationSummary for the extra-payment partial.

    monthly_payment from the resolver (single source of truth);
    committed/accelerated totals and payoff dates from the composer.
    """
    return AmortizationSummary(
        monthly_payment=state.monthly_payment,
        total_interest=scenarios.total_interest_committed,
        payoff_date=scenarios.payoff_date_committed,
        total_interest_with_extra=scenarios.total_interest_accelerated,
        payoff_date_with_extra=scenarios.payoff_date_accelerated,
        months_saved=scenarios.months_saved,
        interest_saved=scenarios.interest_saved,
    )


def _payoff_extra_payment_result(params, account_id, ctx, data):
    """Render the extra-payment payoff scenario partial.

    One ``compute_payoff_scenarios`` call drives both the chart series
    and the summary metrics so they cannot diverge (the structural fix
    for the "extra applied to ghost historical months" defect): replay
    routes confirmed payments through history with no acceleration,
    projection routes projected payments through ``monthly_override``,
    and ``extra_monthly`` is applied only to forward non-override
    months.  The three chart series (original / committed / accelerated)
    share the x-axis via :func:`_build_chart_series`; committed renders
    empty when the loan has no payments.

    Args:
        params: ORM :class:`LoanParams` instance.
        account_id: Debt account id (anchor-event load).
        ctx: Loan context dict from :func:`_load_loan_context`.
        data: Validated :class:`PayoffCalculatorSchema` form data.

    Returns:
        Rendered ``loan/_payoff_results.html`` response.
    """
    extra = Decimal(str(data.get("extra_monthly", "0")))
    scenarios = loan_resolver.compute_payoff_scenarios(
        loan_params=params,
        anchor_events=_load_anchor_events(account_id),
        payments=ctx["payments"],
        rate_changes=ctx["rate_changes"],
        extra_monthly=extra,
        as_of=date.today(),
    )

    chart_labels, balances = _build_chart_series({
        "original": scenarios.history_rows + scenarios.original_forward,
        "committed": scenarios.history_rows + scenarios.committed_forward,
        "accelerated": (
            scenarios.history_rows + scenarios.accelerated_forward
        ),
    })
    has_payments = len(ctx["payments"]) > 0
    committed_months_saved, committed_interest_saved = (
        _payoff_committed_savings(scenarios)
    )

    return render_template(
        "loan/_payoff_results.html",
        mode="extra_payment",
        payoff_summary=_build_payoff_summary(scenarios, ctx["state"]),
        chart_labels=chart_labels,
        chart_original=balances["original"],
        chart_committed=balances["committed"] if has_payments else [],
        chart_accelerated=balances["accelerated"],
        has_payments=has_payments,
        committed_months_saved=committed_months_saved,
        committed_interest_saved=committed_interest_saved,
    )


def _payoff_target_date_result(params, ctx, data):
    """Render the target-date payoff scenario partial.

    Computes the extra monthly payment required to retire the loan by
    the user's target date via the engine's binary search.  The search
    anchors at the resolver-derived current balance and the contractual
    P&I the loan card displays (so the rendered
    ``total_monthly = monthly_payment + required_extra`` is internally
    consistent, D-2 closure).  ``calculate_remaining_months`` supplies
    the raw origination-to-today month count the binary search needs and
    that the resolver does not expose on :class:`LoanState`.

    Args:
        params: ORM :class:`LoanParams` instance.
        ctx: Loan context dict from :func:`_load_loan_context`.
        data: Validated :class:`PayoffCalculatorSchema` form data.

    Returns:
        Rendered ``loan/_payoff_results.html`` response.
    """
    target_date = data.get("target_date")
    if not target_date:
        return render_template(
            "loan/_payoff_results.html",
            error="Target date is required.",
        )

    state = ctx["state"]
    monthly_payment = state.monthly_payment
    remaining_months = amortization_engine.calculate_remaining_months(
        params.origination_date, params.term_months,
    )
    required_extra = amortization_engine.calculate_payoff_by_date(
        amortization_engine.PayoffRequest(
            current_principal=state.current_balance,
            annual_rate=ctx["base_rate"],
            remaining_months=remaining_months,
            target_date=target_date,
            origination_date=date.today().replace(day=1),
            payment_day=params.payment_day,
            original_principal=ctx["original_for_engine"],
            term_months=params.term_months,
            rate_changes=ctx["rate_changes"],
            contractual_payment=monthly_payment,
        )
    )

    total_monthly = (
        round_money(monthly_payment + required_extra)
        if required_extra is not None and required_extra > 0
        else None
    )
    return render_template(
        "loan/_payoff_results.html",
        mode="target_date",
        required_extra=required_extra,
        monthly_payment=monthly_payment,
        total_monthly=total_monthly,
    )


@loan_bp.route("/accounts/<int:account_id>/loan/payoff", methods=["POST"])
@login_required
@require_owner
def payoff_calculate(account_id):
    """Calculate payoff scenario (HTMX)."""
    account, params, _account_type = _load_loan_account(account_id)
    if account is None or params is None:
        return "Account not found", 404

    errors = _payoff_schema.validate(request.form)
    if errors:
        return render_template(
            "loan/_payoff_results.html",
            error="Please correct the highlighted errors and try again.",
        )

    data = _payoff_schema.load(request.form)
    mode = data["mode"]

    # Shared loan context: payments, rate changes, resolver state.
    # Identical to the dashboard's data loading so calculations are
    # consistent.  ``state.current_balance`` is the same dollar figure
    # rendered on the loan card (E-18 / Commit 15).
    ctx = _load_loan_context(account, params)

    if mode == "extra_payment":
        return _payoff_extra_payment_result(params, account_id, ctx, data)
    if mode == "target_date":
        return _payoff_target_date_result(params, ctx, data)
    return render_template(
        "loan/_payoff_results.html",
        error="Invalid mode.",
    )


def _project_refinance(refi_principal, refi_rate, refi_term, payment_day):
    """Project a hypothetical refinance schedule and summarize it.

    Commit 7 of the amortization-engine split: a pure forward projection
    from a known starting state (``refi_principal`` at next month's pay
    date) that maps directly onto
    :func:`amortization_engine.project_forward` -- no replay, no
    projections-as-overrides, no extra; the contractual P&I drives every
    row.

    Args:
        refi_principal: Decimal starting balance for the refinance.
        refi_rate: Decimal annual rate (storage-domain fraction).
        refi_term: New term in months.
        payment_day: Day-of-month the payment falls on.

    Returns:
        Tuple of (refi_monthly P&I, refi_total_interest, refi_payoff
        date).
    """
    refi_monthly = amortization_engine.calculate_monthly_payment(
        refi_principal, refi_rate, refi_term,
    )
    schedule_start = date.today().replace(day=1)
    starting_date = amortization_engine.advance_to_next_payment_date(
        schedule_start, payment_day,
    )
    refi_schedule = amortization_engine.project_forward(
        amortization_engine.ProjectionInputs(
            starting_balance=refi_principal,
            starting_date=starting_date,
            annual_rate=refi_rate,
            remaining_months=refi_term,
            payment_day=payment_day,
            contractual_payment=refi_monthly,
            rate_changes_remaining=None,
        ),
        monthly_override=None,
        extra_monthly=Decimal("0.00"),
    )
    refi_total_interest = sum(
        (row.interest for row in refi_schedule), Decimal("0.00"),
    )
    refi_payoff = (
        refi_schedule[-1].payment_date if refi_schedule
        else schedule_start
    )
    return refi_monthly, refi_total_interest, refi_payoff


def _refinance_break_even(closing_costs, monthly_savings):
    """Months to recoup closing costs from monthly savings, or None.

    Standard consumer-facing approximation assuming constant monthly
    savings: ceil(closing_costs / monthly_savings) when both are
    positive; None when there are no costs to recoup or no monthly
    savings (refinancing to a higher payment).

    Returns:
        int months, or None.
    """
    if (
        closing_costs <= Decimal("0.00")
        or monthly_savings <= Decimal("0.00")
    ):
        return None
    return int(
        (closing_costs / monthly_savings).to_integral_value(
            rounding=ROUND_CEILING,
        )
    )


def _build_refinance_comparison(state, data, params):
    """Build the refinance side-by-side comparison from validated form data.

    Compares the current committed trajectory (resolver ``state`` --
    same dollar figures as the loan card, E-18 / Commit 15) against a
    hypothetical refinance.  The refinance principal defaults to the
    current real balance + closing costs; the user may override for
    cash-out refinances.  The principal delta and its absolute magnitude
    are pre-computed server-side (MED-04 / E-16) so the template renders
    without inline arithmetic.

    Args:
        state: Resolver :class:`LoanState` for the current loan.
        data: Validated :class:`RefinanceSchema` form data.  ``new_rate``
            is already the storage-domain fraction (schema ``@pre_load``).
        params: ORM :class:`LoanParams` instance (payment_day source).

    Returns:
        dict of comparison fields consumed by
        ``loan/_refinance_results.html``.
    """
    current_real_principal = state.current_balance
    closing_costs = data["closing_costs"]
    if data["new_principal"] is not None:
        refi_principal = data["new_principal"]
    else:
        refi_principal = current_real_principal + closing_costs
    refi_term = data["new_term_months"]

    refi_monthly, refi_total_interest, refi_payoff = _project_refinance(
        refi_principal, data["new_rate"], refi_term, params.payment_day,
    )

    monthly_savings = state.monthly_payment - refi_monthly
    break_even_months = _refinance_break_even(closing_costs, monthly_savings)
    principal_diff = refi_principal - current_real_principal

    return {
        "current_monthly": state.monthly_payment,
        "current_total_interest": state.total_interest,
        "current_payoff": state.payoff_date,
        "current_remaining_months": len(state.schedule),
        "current_principal": current_real_principal,
        "refi_monthly": refi_monthly,
        "refi_total_interest": refi_total_interest,
        "refi_payoff": refi_payoff,
        "refi_term": refi_term,
        "refi_principal": refi_principal,
        "monthly_savings": monthly_savings,
        "interest_savings": state.total_interest - refi_total_interest,
        "break_even_months": break_even_months,
        "closing_costs": closing_costs,
        "principal_diff": principal_diff,
        "principal_diff_abs": abs(principal_diff),
    }


@loan_bp.route("/accounts/<int:account_id>/loan/refinance", methods=["POST"])
@login_required
@require_owner
def refinance_calculate(account_id):
    """Compute refinance what-if comparison scenario (HTMX).

    Compares the current committed loan trajectory against a
    hypothetical refinance with user-specified rate, term, closing
    costs, and optional principal override.  Returns a side-by-side
    comparison partial with monthly savings, interest savings, and
    break-even calculation.

    The "current" baseline uses the committed schedule (with actual
    payments) if a recurring transfer exists, otherwise the
    contractual schedule.  This ensures the comparison reflects the
    user's real trajectory, not just the original loan terms.

    The refinance principal defaults to current_real_principal +
    closing_costs.  The user may override for cash-out refinances.
    """
    account, params, _account_type = _load_loan_account(account_id)
    if account is None or params is None:
        return "Account not found", 404

    errors = _refinance_schema.validate(request.form)
    if errors:
        return render_template(
            "loan/_refinance_results.html",
            error="Please correct the highlighted errors and try again.",
        )

    data = _refinance_schema.load(request.form)

    # Shared loan context: resolver state.  Identical to the dashboard's
    # data loading so the "current" refinance baseline matches the card.
    ctx = _load_loan_context(account, params)
    state = ctx["state"]

    # Paid-off loan: no refinance comparison is meaningful.  Use the
    # resolver-derived current_balance for the gate so editing the
    # stored ``current_principal`` column (still legal until Commit 16)
    # cannot trick the route into rendering a refinance form against an
    # already-paid-off loan.
    if not state.schedule or state.current_balance <= Decimal("0.00"):
        return render_template(
            "loan/_refinance_results.html",
            error=(
                "This loan is paid off. "
                "No refinance comparison available."
            ),
        )

    comparison = _build_refinance_comparison(state, data, params)
    return render_template(
        "loan/_refinance_results.html",
        comparison=comparison,
    )
