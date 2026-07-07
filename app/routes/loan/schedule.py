"""
Shekel Budget App -- Loan route package: standalone amortization schedule.

The full month-by-month amortization schedule, demoted off the loan detail page
(the Loop B rebuild: the schedule is a statement table the developer reaches
occasionally, not the page's centre of gravity) into its own route linked from
the detail page's footer.  Renders the same planned trajectory the loan card
carries -- confirmed actuals from the genesis ledger plus the plan-aware
projected payments (``LoanState.schedule`` -- the committed trajectory
reflecting recurring payments and any standing extra since the step-8 seam fix)
-- so the table cannot diverge from the card.
"""

from flask import render_template
from flask_login import login_required

from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _load_loan_context,
    _require_configured_loan,
    build_schedule_context,
)
from app.utils.auth_helpers import require_owner


@loan_bp.route("/accounts/<int:account_id>/loan/schedule")
@login_required
@require_owner
def schedule(account_id):
    """Standalone month-by-month amortization schedule for a loan.

    Resolves the loan once (the same resolver state the detail page reads) and
    renders its planned schedule as the full statement table.  Guards via
    :func:`._require_configured_loan`: a cross-owner / non-loan account 404s, an
    un-configured loan redirects to its detail page (the setup surface).
    """
    account, params, account_type = _require_configured_loan(account_id)
    ctx = _load_loan_context(account, params)
    context = {
        "account": account,
        "account_type": account_type,
        "monthly_escrow": ctx.loan.monthly_escrow,
    }
    context.update(build_schedule_context(
        ctx.state.schedule, ctx.loan.monthly_escrow, ctx.current_rate, params,
    ))
    return render_template("loan/schedule.html", **context)
