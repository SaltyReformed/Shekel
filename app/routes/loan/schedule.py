"""
Shekel Budget App -- Loan route package: standalone amortization schedule.

The full month-by-month amortization schedule, demoted off the loan detail page
(the Loop B rebuild: the schedule is a statement table the developer reaches
occasionally, not the page's centre of gravity) into its own route linked from
the detail page's footer.  Renders the same planned trajectory the loan card
carries -- confirmed actuals from the genesis ledger plus the plan-aware
projected payments (the committed trajectory reflecting recurring payments and
any standing extra since the step-8 seam fix) -- so the table cannot diverge
from the card.
"""

from datetime import date

from flask import render_template
from flask_login import login_required

from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _require_configured_loan,
    build_schedule_context,
    load_baseline_scenarios,
)
from app.services import loan_resolver
from app.utils.auth_helpers import require_owner


@loan_bp.route("/accounts/<int:account_id>/loan/schedule")
@login_required
@require_owner
def schedule(account_id):
    """Standalone month-by-month amortization schedule for a loan.

    A schedule TABLE, not a balance surface, so it does not read the balance
    seam: it composes its planned trajectory ONCE off the same load-and-compose
    the detail page's band chart shares (:func:`._helpers.load_baseline_scenarios`,
    ``history_rows + committed_forward``) with the loan's standing extra -- the
    identical committed trajectory the card carries -- and reads the current rate
    (the ARM rate-column fallback) via the cheap rate-period accessor
    :func:`loan_resolver.current_rate_baseline` rather than a full resolve, so the
    schedule is derived exactly once.  Guards via :func:`._require_configured_loan`:
    a cross-owner / non-loan account 404s, an un-configured loan redirects to its
    detail page (the setup surface).
    """
    account, params, account_type = _require_configured_loan(account_id)
    loan, scenarios = load_baseline_scenarios(account, params)
    planned_schedule = (
        list(scenarios.history_rows) + list(scenarios.committed_forward)
    )
    current_rate = loan_resolver.current_rate_baseline(
        params, loan.rate_changes, date.today(),
    )
    context = {
        "account": account,
        "account_type": account_type,
        "monthly_escrow": loan.monthly_escrow,
    }
    context.update(build_schedule_context(
        planned_schedule, loan.monthly_escrow, current_rate, params,
    ))
    return render_template("loan/schedule.html", **context)
