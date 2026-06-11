"""
Shekel Budget App -- Loan route package: detail dashboard.

The loan detail page (GET): summary card, payment breakdown, multi-scenario
balance chart, escrow / rate-history panels, amortization-schedule tab, and the
recurring-transfer prompt.  The route assembles its template context by merging
the per-section dicts the private helpers below return; the recurrence
end_date sync (a deliberate write on a GET, R-4) also lives here because the
dashboard is where the payoff date is computed with full payment context.
"""

import logging
from datetime import date
from decimal import Decimal, ROUND_DOWN

from flask import abort, render_template
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app import ref_cache
from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.models.transfer_template import TransferTemplate
from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _build_chart_series,
    _load_anchor_events,
    _load_loan_account,
    _load_loan_context,
)
from app.services import escrow_calculator, loan_resolver
from app.services.amortization_engine import AmortizationRow, AmortizationSummary
from app.services.rate_period_engine import payment_number
from app.utils.auth_helpers import require_owner
from app.utils.log_events import (
    BUSINESS,
    EVT_LOAN_RECURRENCE_END_DATE_UPDATED,
    log_event,
)
from app.utils.money import round_money

logger = logging.getLogger(__name__)


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


def _compute_schedule_totals(schedule, monthly_escrow=Decimal("0.00")):
    """Sum payment, principal, interest, escrow, and extra from a schedule.

    The Payment column in the schedule shows P&I + escrow for each month.
    Totals are computed from the actual schedule rows so the footer row
    matches the individual data rows exactly.

    Args:
        schedule: List of AmortizationRow objects.
        monthly_escrow: Monthly escrow amount added to each row's
            payment for display.

    Returns:
        dict with keys: total_payment, total_principal, total_interest,
        total_escrow, total_extra, has_extra.  Empty dict if schedule
        is empty.
    """
    if not schedule:
        return {}
    num_months = len(schedule)
    total_pi = sum((row.payment for row in schedule), Decimal("0.00"))
    total_principal = sum((row.principal for row in schedule), Decimal("0.00"))
    total_interest = sum((row.interest for row in schedule), Decimal("0.00"))
    total_extra = sum((row.extra_payment for row in schedule), Decimal("0.00"))
    total_escrow = monthly_escrow * num_months
    return {
        "total_payment": total_pi + total_escrow + total_extra,
        "total_principal": total_principal,
        "total_interest": total_interest,
        "total_escrow": total_escrow,
        "total_extra": total_extra,
        "has_extra": total_extra > Decimal("0.00"),
    }


def _update_transfer_end_date(
    template: TransferTemplate,
    summary: AmortizationSummary,
    schedule: list[AmortizationRow],
    account_id: int,
) -> None:
    """Update the recurring transfer's end date to match the projected payoff.

    Sets the recurrence rule end_date to the committed schedule's payoff
    date so the recurrence engine stops generating transfers beyond
    payoff.  The update is idempotent -- if the end_date already matches,
    no write occurs.

    Three cases:
      - Normal payoff: end_date = last scheduled payment date.
      - Already paid off (empty schedule): end_date = summary fallback
        date (first of current month), stopping future generation.
      - No payoff within term (negative amortization, remaining balance
        > 0 at schedule end): end_date = None (indefinite recurrence).

    This is a write on a GET request (Risk R-4). Acknowledged as a
    pragmatic trade-off: the dashboard is the natural place where the
    payoff date is computed with full payment context, the write is
    idempotent, and the alternative (hooks in the transfer service)
    was rejected for coupling complexity.

    Args:
        template: The active recurring transfer template targeting
            this debt account.  Only the first matching template is
            updated -- multiple recurring transfers to the same debt
            account is unusual and likely a user configuration issue.
        summary: The committed schedule summary.  Used as a fallback
            payoff date when the schedule is empty (already paid off).
        schedule: The committed amortization schedule.  Used to
            determine payoff status and exact payoff date.
        account_id: The debt account ID, for logging.
    """
    rule = template.recurrence_rule

    # Determine the projected payoff date from the committed schedule.
    if not schedule:
        # Loan already paid off (zero principal).  Use the summary's
        # fallback payoff date (first of current month) to prevent
        # the recurrence engine from generating future transfers.
        projected_payoff = summary.payoff_date
    elif schedule[-1].remaining_balance > Decimal("0.00"):
        # Schedule ends with outstanding balance -- the loan does not
        # pay off within the projected term (e.g., payments less than
        # monthly interest).  Leave recurrence indefinite so transfers
        # continue until the user adjusts payments.
        projected_payoff = None
    else:
        # Normal payoff at the last scheduled payment date.
        projected_payoff = schedule[-1].payment_date

    current_end_date = rule.end_date

    if projected_payoff == current_end_date:
        return

    rule.end_date = projected_payoff
    try:
        db.session.commit()
    except SQLAlchemyError:
        logger.exception(
            "Failed to update recurrence rule end_date for template %d",
            template.id,
        )
        db.session.rollback()
        return

    log_event(
        logger, logging.INFO,
        EVT_LOAN_RECURRENCE_END_DATE_UPDATED, BUSINESS,
        "Updated recurrence rule end date to projected payoff",
        account_id=account_id,
        template_id=template.id,
        old_end_date=str(current_end_date),
        new_end_date=str(projected_payoff),
    )


def _build_dashboard_scenarios(params, anchor_events, payments, rate_changes, as_of):
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

    Returns:
        Tuple of (scenarios_main, scenarios_floor) PayoffScenarios.
    """
    scenarios_main = loan_resolver.compute_payoff_scenarios(
        loan_inputs=loan_resolver.LoanInputs(
            loan_params=params,
            anchor_events=anchor_events,
            payments=payments,
            rate_changes=rate_changes,
        ),
        extra_monthly=Decimal("0.00"),
        as_of=as_of,
    )
    confirmed_payments = [p for p in payments if p.is_confirmed]
    scenarios_floor = loan_resolver.compute_payoff_scenarios(
        loan_inputs=loan_resolver.LoanInputs(
            loan_params=params,
            anchor_events=anchor_events,
            payments=confirmed_payments,
            rate_changes=rate_changes,
        ),
        extra_monthly=Decimal("0.00"),
        as_of=as_of,
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
        Tuple of (existing_template, prompt_context) where
        ``existing_template`` is the active transfer template (or None)
        -- the caller also needs it for the end_date sync -- and
        ``prompt_context`` is a dict of template vars:
        show_transfer_prompt, source_accounts, default_source_id.
    """
    existing_template = (
        db.session.query(TransferTemplate)
        .filter(
            TransferTemplate.user_id == current_user.id,
            TransferTemplate.to_account_id == account.id,
            TransferTemplate.is_active.is_(True),
            TransferTemplate.recurrence_rule_id.isnot(None),
        )
        .first()
    )
    if existing_template is not None:
        return existing_template, {
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
    return None, {
        "show_transfer_prompt": True,
        "source_accounts": source_accounts,
        "default_source_id": default_source_id,
    }


def _build_schedule_tab(planned_schedule, monthly_escrow, current_rate, params):
    """Build the amortization-schedule tab's template context.

    The planned schedule shows the user's trajectory with confirmed
    actuals + projected payments.  Three index-parallel lists are
    computed server-side (consumed via ``loop.index0``) so the schedule
    template renders without inline Jinja arithmetic (MED-04 / E-16):
    per-row total monthly outflow (P&I + escrow + extra), the ARM
    display rate (storage-domain fraction times 100), and a continuous
    payment number from origination so a mid-life loan's "#" column
    keeps counting up instead of restarting at 1.

    Returns:
        dict of template vars: amortization_schedule, show_rate_column,
        schedule_totals, schedule_row_totals, schedule_row_rates_pct,
        schedule_row_numbers.
    """
    show_rate_column = bool(params.is_arm)
    schedule_row_totals = [
        round_money(row.payment + monthly_escrow + row.extra_payment)
        for row in planned_schedule
    ]
    schedule_row_rates_pct = [
        (row.interest_rate if row.interest_rate is not None else current_rate)
        * Decimal("100")
        for row in planned_schedule
    ] if show_rate_column else None
    schedule_row_numbers = [
        payment_number(params.origination_date, row.payment_date)
        for row in planned_schedule
    ]
    return {
        "amortization_schedule": planned_schedule,
        "show_rate_column": show_rate_column,
        "schedule_totals": _compute_schedule_totals(
            planned_schedule, monthly_escrow,
        ),
        "schedule_row_totals": schedule_row_totals,
        "schedule_row_rates_pct": schedule_row_rates_pct,
        "schedule_row_numbers": schedule_row_numbers,
    }


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
    scenarios_main, scenarios_floor = _build_dashboard_scenarios(
        params, _load_anchor_events(account.id),
        ctx.loan.payments, ctx.loan.rate_changes, date.today(),
    )
    # PLANNED-trajectory schedule: real confirmed history + projected /
    # contractual forward.  Resolver still owns current_balance and
    # monthly_payment so the loan card / debt card / net-worth liability
    # cannot diverge (the E-18 invariant).
    planned_schedule = (
        scenarios_main.history_rows + scenarios_main.committed_forward
    )
    summary = _build_planned_summary(ctx.state, planned_schedule, params)

    # Sync the recurring transfer's recurrence end_date to the projected
    # payoff (5.9-1) so shadow transactions are not generated beyond
    # payoff.  Uses ``planned_schedule`` (the user's planned trajectory)
    # so a neg-am user paying under the monthly interest keeps an open
    # end_date even though the contractual forecast would say "paid off."
    existing_template, prompt_context = _resolve_transfer_prompt(account)
    if existing_template is not None and existing_template.recurrence_rule is not None:
        _update_transfer_end_date(
            existing_template, summary, planned_schedule, account.id,
        )

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
    }
    context.update(_build_payment_summary(
        ctx.state, summary, planned_schedule, ctx.loan.escrow_components,
    ))
    context.update(_build_dashboard_chart_context(
        scenarios_main, scenarios_floor, len(ctx.loan.payments) > 0,
    ))
    context.update(prompt_context)
    context.update(_build_schedule_tab(
        planned_schedule, ctx.loan.monthly_escrow, ctx.current_rate, params,
    ))
    return render_template("loan/dashboard.html", **context)
