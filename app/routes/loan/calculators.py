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
from flask_login import current_user, login_required

from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _load_loan_account,
    _load_route_context,
    _loan_inputs,
    _payoff_schema,
    _refinance_schema,
    accelerated_overlay,
)
from app.services import amortization_engine, balance_at, loan_resolver
from app.services.amortization_engine import AmortizationSummary
from app.services.recurring_transfer_query import loan_standing_extra
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


def _build_payoff_summary(scenarios, monthly_payment):
    """Assemble the AmortizationSummary for the extra-payment partial.

    monthly_payment from the seam figures (single source of truth);
    committed/accelerated totals and payoff dates from the composer.
    """
    return AmortizationSummary(
        monthly_payment=monthly_payment,
        total_interest=scenarios.total_interest_committed,
        payoff_date=scenarios.payoff_date_committed,
        total_interest_with_extra=scenarios.total_interest_accelerated,
        payoff_date_with_extra=scenarios.payoff_date_accelerated,
        months_saved=scenarios.months_saved,
        interest_saved=scenarios.interest_saved,
    )


def _payoff_extra_payment_result(params, ctx, data, confirmed_view, extra_principal):
    """Render the extra-payment payoff scenario partial.

    One ``compute_payoff_scenarios`` call drives both the band-chart
    overlay and the summary metrics so they cannot diverge (the
    structural fix for the "extra applied to ghost historical months"
    defect): replay routes confirmed payments through history, projection
    routes projected payments through ``monthly_override``, the loan's
    STANDING ``extra_principal`` accelerates the committed slice, and the
    lever's ``extra_monthly`` previews additional acceleration on top in the
    accelerated slice.  Both extras apply to every forward month (step 5).  The
    accelerated forward slice becomes the band chart's green dashed preview via
    :func:`._helpers.accelerated_overlay` -- forward-only, aligned to the
    band's contractual x-axis, so the client overlays it on the same
    chart the dashboard drew.

    Args:
        params: ORM :class:`LoanParams` instance (also the anchor-fact
            synthesis source).
        ctx: The route context from :func:`_load_route_context`.
        data: Validated :class:`PayoffCalculatorSchema` form data.
        confirmed_view: The genesis-ledger confirmed view, read once by the
            caller; threaded into the composer so the projected payoff
            amortizes the real owed balance -- and charts the ledger-derived
            confirmed history -- the loan card shows.  ``None`` falls back to
            the anchor replay.
        extra_principal: The loan's standing monthly overpayment (``0.00`` when
            none); the committed baseline the lever previews on top of.

    Returns:
        Rendered ``loan/_payoff_results.html`` response.
    """
    extra = Decimal(str(data.get("extra_monthly", "0")))
    scenarios = loan_resolver.compute_payoff_scenarios(
        loan_inputs=_loan_inputs(params, ctx.loan),
        extra_monthly=extra,
        # The read pass's pinned as-of, NOT a second ``date.today()``: the
        # confirmed view threaded in below was built at ``ctx.balance_ctx.as_of``,
        # and a midnight rollover between the two reads would splice a seed from
        # one day onto a projection from the next (plan step E1d-b).
        as_of=ctx.balance_ctx.as_of,
        confirmed_view=confirmed_view,
        extra_principal=extra_principal,
    )

    committed_months_saved, committed_interest_saved = (
        _payoff_committed_savings(scenarios)
    )
    return render_template(
        "loan/_payoff_results.html",
        mode="extra_payment",
        payoff_summary=_build_payoff_summary(scenarios, ctx.monthly_payment),
        overlay=accelerated_overlay(scenarios),
        has_payments=len(ctx.loan.payments) > 0,
        committed_months_saved=committed_months_saved,
        committed_interest_saved=committed_interest_saved,
    )


def _payoff_target_date_result(params, ctx, data):
    """Render the target-date payoff scenario partial.

    Computes two answers (F-27, developer-selected "fix + reframe,
    show both" 2026-06-11):

    * The RAW required extra -- the engine's binary search against the
      contractual schedule alone, anchored at the seam's current
      balance (the fold) with the loan's rate-period terms feed
      (``loan_resolver.engine_terms``), whose governing entry today IS
      the loan card's P&I -- so the rendered ``total_monthly =
      monthly_payment + required_extra`` is internally consistent (D-2
      closure, now structural).  For a user with no recurring payment
      plan this is the only number.
    * The PLAN-AWARE answer -- when the loan has payments,
      :func:`app.services.balance_at.loan_required_extra` folds the loan's
      forward PLAN (plan step C8f): what extra is needed ON TOP of the
      payments the user is already making.  Without it, a user
      already paying $500/mo over contractual was told they need the
      full extra again (the F-27 overstatement).

      It no longer reports "when the current plan pays off" beside that
      figure.  That is the payoff CHIP's question, answered on the same
      page by the same fold; rendering it twice from two producers is how
      the panel came to contradict the chip for a delinquent loan (the
      schedule walk retires debt nobody paid -- finding B-9).  One
      question, one producer, one place on the page.

    ``calculate_remaining_months`` supplies the raw origination-to-today
    month count the raw search needs and that the resolver does not
    expose on :class:`LoanState`.

    Args:
        params: ORM :class:`LoanParams` instance (the RAW search's
            origination / term source).
        ctx: The route context from :func:`_load_route_context`.
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

    monthly_payment = ctx.monthly_payment
    remaining_months = amortization_engine.calculate_remaining_months(
        params.origination_date, params.term_months,
    )
    required_extra = amortization_engine.calculate_payoff_by_date(
        amortization_engine.PayoffRequest(
            current_principal=ctx.current_balance,
            remaining_months=remaining_months,
            target_date=target_date,
            origination_date=date.today().replace(day=1),
            payment_day=params.payment_day,
            terms_schedule=loan_resolver.engine_terms(
                params, ctx.loan.rate_changes,
            ),
        )
    )

    has_payments = len(ctx.loan.payments) > 0
    plan_extra = None
    if has_payments:
        # The plan-aware answer folds the loan's forward PLAN through the seam,
        # off the SAME BalanceContext the page's payoff chip reads (step C8f), so
        # the two cannot rest on different forward models.
        plan_extra = balance_at.loan_required_extra(
            ctx.account, ctx.balance_ctx, target_date,
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
        has_payments=has_payments,
        plan_extra=plan_extra,
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

    # Shared loan context: payments, rate changes, seam balance + figures.
    # Identical to the dashboard's data loading so calculations are
    # consistent.  ``ctx.current_balance`` is the same dollar figure
    # rendered on the loan card (the seam's fold, plan C4).
    ctx = _load_route_context(account, params)

    if mode == "extra_payment":
        # Resolved INSIDE the branch that uses them (step C8f): the target-date
        # mode takes neither, since its plan-aware answer folds the seam's own
        # memoized walk and plan and the standing extra is already inside the
        # plan's payment cash.  Hoisting them would make every target-date
        # request pay for a genesis-ledger walk and a template query it discards.
        #
        # Read switch: read the genesis-ledger confirmed view ONCE and thread it
        # into the forward projection, so the payoff results project from the
        # same real owed balance -- and chart the same confirmed history -- the
        # loan card shows.  Since plan step E1d-b that view is the seam's FOLD of
        # the loan's recorded events, read off the pass's already-memoized walk,
        # which is also what the loan card's own resolution was seeded with.
        # ``require_owner`` already gated ownership above.
        view = balance_at.confirmed_view(account, ctx.balance_ctx)
        # The loan's standing overpayment: the committed baseline this mode
        # previews additional extra on top of (step 5).
        standing_extra = loan_standing_extra(account_id, current_user.id)
        return _payoff_extra_payment_result(
            params, ctx, data, view, standing_extra,
        )
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
            remaining_months=refi_term,
            payment_day=payment_day,
            # A refinance is one fixed-rate span: a single terms entry
            # carries the new rate and its derived level payment.
            terms_schedule=[amortization_engine.PeriodTerms(
                start_date=starting_date,
                annual_rate=refi_rate,
                monthly_pi=refi_monthly,
            )],
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


def _build_refinance_comparison(current_balance, ctx, scenarios, data, params):
    """Build the refinance side-by-side comparison from validated form data.

    Compares the current loan's CONTRACTUAL forward trajectory against a
    hypothetical refinance.  Since the resolver seam went plan-aware (step 8,
    ``docs/design/escrow_line_identity_refactor.md`` Sec. 16), the loan's
    committed schedule reflects its standing extra; a refinance comparison must
    instead be like-for-like -- minimum-payment current vs minimum-payment refi
    -- because a borrower could pay the same extra on either loan.  So the
    current side reads the pure-contractual ``scenarios.original_forward`` slice
    (override- and extra-free), while ``ctx`` supplies the current monthly P&I
    (the seam figure) and real balance (the seam's fold, both independent of
    committed-vs-contractual).  The refinance principal defaults to the current
    real balance + closing costs; the user may override for cash-out refinances.
    The principal delta and its absolute magnitude are pre-computed server-side
    (MED-04 / E-16).

    The current side is measured FORWARD-ONLY -- ``original_forward`` is already
    the from-today contractual remainder -- because the refinance side is
    inherently forward-only (a brand-new loan from today): "Remaining Term" is
    the count of payments still ahead, and "Total Interest" the interest still to
    be paid.  Counting sunk history rows would skew the comparison against a
    from-today refinance.

    Args:
        current_balance: The loan's balance-at-today (the seam's fold), read
            ONCE by the caller and threaded in so this and the paid-off gate do
            not each re-sample the seam.
        ctx: The :class:`~app.routes.loan._helpers._RouteLoanContext` for the
            current loan (its ``monthly_payment``, and its DERIVED
            ``payoff_date`` as the empty-slice fallback -- which may be ``None``
            for a retired loan or one that never clears, so the template renders
            the absence; the plan-aware schedule is NOT read).
        scenarios: The loan's :class:`loan_resolver.PayoffScenarios`; its
            ``original_forward`` slice is the contractual current-side baseline.
        data: Validated :class:`RefinanceSchema` form data.  ``new_rate``
            is already the storage-domain fraction (schema ``@pre_load``).
        params: ORM :class:`LoanParams` instance (payment_day source).

    Returns:
        dict of comparison fields consumed by
        ``loan/_refinance_results.html``.
    """
    closing_costs = data["closing_costs"]
    if data["new_principal"] is not None:
        refi_principal = data["new_principal"]
    else:
        refi_principal = current_balance + closing_costs
    refi_term = data["new_term_months"]

    refi_monthly, refi_total_interest, refi_payoff = _project_refinance(
        refi_principal, data["new_rate"], refi_term, params.payment_day,
    )

    # Forward-only CONTRACTUAL baseline: what is still ahead on the current loan
    # at the minimum payment (the like-for-like basis against a from-today
    # refinance; see the docstring).  ``original_forward`` is already forward-only.
    forward_rows = scenarios.original_forward
    current_remaining_interest = round_money(sum(
        (row.interest for row in forward_rows), Decimal("0.00"),
    ))

    monthly_savings = ctx.monthly_payment - refi_monthly
    principal_diff = refi_principal - current_balance

    return {
        "current_monthly": ctx.monthly_payment,
        "current_total_interest": current_remaining_interest,
        "current_payoff": (
            forward_rows[-1].payment_date if forward_rows
            else ctx.payoff_date
        ),
        "current_remaining_months": len(forward_rows),
        "current_principal": current_balance,
        "refi_monthly": refi_monthly,
        "refi_total_interest": refi_total_interest,
        "refi_payoff": refi_payoff,
        "refi_term": refi_term,
        # Term delta (new term minus current remaining), pre-computed
        # server-side so the template renders without inline arithmetic
        # (MED-04 / E-16, same rationale as principal_diff above).
        "term_diff": refi_term - len(forward_rows),
        "refi_principal": refi_principal,
        "monthly_savings": monthly_savings,
        "interest_savings": current_remaining_interest - refi_total_interest,
        "break_even_months": _refinance_break_even(closing_costs, monthly_savings),
        "closing_costs": closing_costs,
        "principal_diff": principal_diff,
        "principal_diff_abs": abs(principal_diff),
    }


@loan_bp.route("/accounts/<int:account_id>/loan/refinance", methods=["POST"])
@login_required
@require_owner
def refinance_calculate(account_id):
    """Compute refinance what-if comparison scenario (HTMX).

    Compares the current loan's CONTRACTUAL forward trajectory against a
    hypothetical refinance with user-specified rate, term, closing
    costs, and optional principal override.  Returns a side-by-side
    comparison partial with monthly savings, interest savings, and
    break-even calculation.

    The "current" baseline is deliberately contractual
    (``scenarios.original_forward``), NOT the plan-aware committed schedule the
    resolver seam now produces (step 8, Sec. 16): a refinance is a like-for-like
    minimum-vs-minimum comparison, since any standing extra could be paid on
    either loan.  See :func:`_build_refinance_comparison`.

    The refinance principal defaults to the current real balance +
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

    # Shared loan context: seam balance + figures.  Identical to the dashboard's
    # data loading so the "current" refinance baseline matches the card.
    ctx = _load_route_context(account, params)

    # Paid-off loan: no refinance comparison is meaningful.  Read the seam's
    # balance (the fold, plan C4) ONCE, and gate on it: a loan that owes nothing
    # needs no refinance.  This gate replaces the pre-C4 ``not state.schedule or
    # state.current_balance <= 0``; the schedule half was redundant for every
    # normal loan (an empty committed schedule implies a zero balance) and its
    # only divergent case -- a past-term balloon still owing a positive balance
    # with no forward rows -- is more honestly served BY a refinance comparison
    # than blocked as "paid off", so gating on the balance alone is correct.
    current_balance = ctx.current_balance
    if current_balance <= Decimal("0.00"):
        return render_template(
            "loan/_refinance_results.html",
            error=(
                "This loan is paid off. "
                "No refinance comparison available."
            ),
        )

    # Contractual current-side baseline (step 8 / Sec. 16): a like-for-like
    # comparison holds any standing extra constant on both sides, so the current
    # side reads the pure-contractual ``original_forward`` slice -- NOT the
    # committed schedule (plan-aware since the resolver seam) -- against a
    # from-today minimum-payment refi.  ``original_forward`` is override- and
    # extra-free regardless of inputs; the seam's confirmed view (the fold, plan
    # step E1d-b) seeds it from the real owed balance the loan card shows.
    scenarios = loan_resolver.compute_payoff_scenarios(
        loan_inputs=_loan_inputs(params, ctx.loan),
        extra_monthly=Decimal("0.00"),
        as_of=ctx.balance_ctx.as_of,
        confirmed_view=balance_at.confirmed_view(account, ctx.balance_ctx),
    )

    comparison = _build_refinance_comparison(
        current_balance, ctx, scenarios, data, params,
    )
    return render_template(
        "loan/_refinance_results.html",
        comparison=comparison,
    )
