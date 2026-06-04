"""
Shekel Budget App -- Loan Routes

Unified dashboard, parameter updates, escrow management, rate history,
and payoff calculator for all installment loan account types.
"""

import logging
from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.utils.auth_helpers import get_or_404, require_owner
from app import ref_cache
from app.enums import AcctTypeEnum, LoanAnchorSourceEnum, RecurrencePatternEnum
from app.extensions import db
from app.models.account import Account
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_params import LoanParams
from app.models.loan_features import RateHistory, EscrowComponent
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import AccountType
from app.models.transfer_template import TransferTemplate
from app.routes._transfer_creation_helpers import (
    build_recurring_transfer_template,
    flush_template_or_namedup_redirect,
    generate_transfers_for_all_periods,
    validate_and_resolve_source_account,
)
from app.schemas.validation import (
    EscrowComponentSchema,
    LoanAnchorTrueupSchema,
    LoanParamsCreateSchema,
    LoanParamsUpdateSchema,
    LoanPaymentTransferSchema,
    PayoffCalculatorSchema,
    RateChangeSchema,
    RefinanceSchema,
)
from app.services import (
    amortization_engine,
    anchor_service,
    escrow_calculator,
    loan_resolver,
)
from app.services.anchor_service import AnchorTrueUpOutcome
from app.services.amortization_engine import (
    AmortizationRow,
    AmortizationSummary,
)
from app.services.loan_payment_service import (
    load_loan_context,
)
from app.services.loan_resolver import LoanState
from app.services.rate_period_engine import payment_number
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.db_errors import is_unique_violation
from app.utils.log_events import BUSINESS, EVT_LOAN_RECURRENCE_END_DATE_UPDATED, log_event
from app.utils.money import round_money

logger = logging.getLogger(__name__)

# Field allowlist for the loan-params update route.  E-18 / D-C:
# ``current_principal`` is intentionally excluded -- it is non-authoritative
# seed and the resolver derives the displayed balance from
# :class:`LoanAnchorEvent`; ``LoanParamsUpdateSchema`` no longer declares
# the field, so a stale client submitting it via this form is a silent no-op.
_PARAM_FIELDS = {
    "interest_rate", "payment_day", "term_months",
    "is_arm", "arm_first_adjustment_months", "arm_adjustment_interval_months",
}

# Name of the composite unique constraint that backstops the
# loan rate-history double-submit fix (F-104 / C-22).  Mirrors the
# literal in ``app/models/loan_features.py:RateHistory.__table_args__``
# and ``migrations/versions/<C-22 revision>.py``; renaming the
# constraint requires a coordinated edit across all three sites.
_RATE_HISTORY_UNIQUE_CONSTRAINT = "uq_rate_history_account_effective_date"

loan_bp = Blueprint("loan", __name__)

_create_schema = LoanParamsCreateSchema()
_update_schema = LoanParamsUpdateSchema()
_trueup_schema = LoanAnchorTrueupSchema()
_rate_schema = RateChangeSchema()
_escrow_schema = EscrowComponentSchema()
_payoff_schema = PayoffCalculatorSchema()
_refinance_schema = RefinanceSchema()
_transfer_schema = LoanPaymentTransferSchema()


def _load_loan_account(account_id):
    """Load and validate a loan account for the current user.

    Verifies ownership and that the account type has has_amortization=True.

    Returns:
        (account, params, account_type) or (None, None, None) if invalid.
    """
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return None, None, None

    account_type = db.session.get(AccountType, account.account_type_id)
    if account_type is None or not account_type.has_amortization:
        return None, None, None

    params = (
        db.session.query(LoanParams)
        .filter_by(account_id=account.id)
        .first()
    )
    return account, params, account_type


def _build_chart_data(schedule):
    """Build chart data from an amortization schedule."""
    labels = []
    balances = []
    for row in schedule:
        labels.append(row.payment_date.strftime("%b %Y"))
        # Presentation boundary: float() for Chart.js JSON serialization.
        balances.append(float(row.remaining_balance))
    return labels, balances


def _balances_for_chart(rows, target_len):
    """Build a chart balance list, padded to ``target_len`` with $0.00.

    When a payoff scenario reaches zero before the longest baseline,
    its trailing months are padded with 0.0 so Chart.js plots all
    datasets against the same x-axis.  The post-payoff balance IS
    zero (the loan is gone), so the padding is the literal financial
    truth, not a visual placeholder.

    Args:
        rows: Iterable of :class:`AmortizationRow`.  May be shorter
            than ``target_len``.
        target_len: Total number of data points the chart expects --
            typically the length of the original (no-acceleration)
            baseline.

    Returns:
        List of floats, length exactly ``target_len``.  Presentation
        boundary: float() for Chart.js JSON serialization.
    """
    balances = [float(row.remaining_balance) for row in rows]
    if len(balances) < target_len:
        balances.extend([0.0] * (target_len - len(balances)))
    return balances


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

    # Truncate-then-distribute: guarantees percentages sum to 100.0%.
    one_decimal = Decimal("0.1")
    parts = [
        ("principal", principal_portion),
        ("interest", interest_portion),
        ("escrow", escrow_portion),
    ]
    truncated = {}
    for name, amount in parts:
        raw_pct = amount / total_payment * 100
        truncated[name] = raw_pct.quantize(one_decimal, rounding=ROUND_DOWN)

    residual = Decimal("100.0") - sum(truncated.values())
    # Assign residual to the largest portion.
    largest = max(truncated, key=truncated.get)
    truncated[largest] += residual

    # O-3: Escrow inflation projection.  If any component has a
    # non-null inflation_rate, compute next year's monthly escrow
    # to show the user projected changes.
    next_year_escrow = None
    has_inflation = any(
        getattr(c, "inflation_rate", None)
        for c in escrow_components
    )
    if has_inflation and escrow_portion > Decimal("0.00"):
        next_year_date = date(date.today().year + 1, 1, 1)
        next_year_escrow = escrow_calculator.calculate_monthly_escrow(
            escrow_components, as_of_date=next_year_date,
        )
        # Only show the note if next year differs from current.
        if next_year_escrow == escrow_portion:
            next_year_escrow = None

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





def _load_anchor_events(account_id: int) -> list[LoanAnchorEvent]:
    """Load every :class:`LoanAnchorEvent` for a loan account.

    The resolver selects the latest event by ``(anchor_date,
    created_at) DESC`` -- ordering is its responsibility, not the
    loader's.  Returning an unsorted list keeps the call site noise-
    free and matches the integration-test pattern from
    :mod:`tests.test_integration.test_loan_principal_settles`.

    Args:
        account_id: The debt account ID.

    Returns:
        List of :class:`LoanAnchorEvent` rows (possibly empty for
        loans created before the Commit-12 backfill OR before F-9
        was closed by Commit 15).  An empty list is a data invariant
        violation; ``loan_resolver.resolve_loan`` raises ValueError
        loudly so the operator sees the gap rather than a silently
        wrong number.
    """
    return (
        db.session.query(LoanAnchorEvent)
        .filter_by(account_id=account_id)
        .all()
    )


def _resolve_loan_state(account, params) -> tuple[LoanState, list, list | None]:
    """Run the resolver for a loan; return (state, payments, rate_changes).

    Single seam every loan-route resolver consumer reads through, so
    Commit 17's "unify per-period figures" follow-up has exactly one
    place to swap.  Returns the prepared payment + rate-change feeds
    alongside the resolver state because every caller that wants
    the state also wants the same feed for downstream
    schedule-generation (chart paths in
    :func:`dashboard` / :func:`payoff_calculate`).

    Args:
        account: ORM :class:`Account` instance.
        params: ORM :class:`LoanParams` instance.

    Returns:
        Three-tuple of:
            - :class:`LoanState` (resolver output, source of truth
              for current_balance / monthly_payment / schedule /
              payoff_date / total_interest).
            - Prepared payment list (escrow-subtracted, biweekly-
              redistributed) from
              :func:`loan_payment_service.load_loan_context`.
            - Optional rate-change list (None for fixed-rate loans).
    """
    scenario = get_baseline_scenario(current_user.id)
    scenario_id = scenario.id if scenario else None
    ctx = load_loan_context(account.id, scenario_id, params)
    anchor_events = _load_anchor_events(account.id)
    state = loan_resolver.resolve_loan(
        params, anchor_events, ctx.payments, ctx.rate_changes, date.today(),
    )
    return state, ctx.payments, ctx.rate_changes


def _load_loan_context(account, params):
    """Load payment history, escrow, rate changes, and resolver state.

    Delegates payment / escrow / rate-change loading to
    :func:`loan_payment_service.load_loan_context`, then runs the
    loan resolver (E-18 / Commit 13) to derive the authoritative
    current balance and monthly payment.  Display surfaces read
    ``ctx["state"]`` instead of the stored
    ``LoanParams.current_principal`` / ``LoanParams.interest_rate``
    columns (E-18 / Commit 15, decision D-A); the stored columns
    remain only as non-authoritative seed.

    Returns a dict with:
        payments: Prepared PaymentRecord list (escrow-subtracted,
            month-aligned).
        rate_changes: List of RateChangeRecord or None.
        rate_history: List of RateHistory ORM objects (for display).
        escrow_components: List of active EscrowComponent objects.
        monthly_escrow: Decimal monthly escrow amount.
        state: :class:`LoanState` from the resolver.
        original_for_engine: Decimal original principal, or None for
            ARM.  Historically consumed by chart-generation paths that
            called the amortization engine directly; those paths now
            route through :func:`loan_resolver.compute_payoff_scenarios`
            (Phase 4-6 of the amortization-engine split documented in
            ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``),
            so the field is retained only for the payoff calculator's
            ``mode == "target_date"`` branch (a thin
            :func:`amortization_engine.calculate_payoff_by_date`
            wrapper internally on :func:`project_forward`).
        base_rate: Decimal annual interest rate -- the resolver's
            ``base_rate`` input, used by the same direct-engine
            chart paths and by the refinance / payoff calculators.
            ``params.interest_rate`` remains the system-of-record
            for the base rate; the resolver layers
            :class:`RateHistory` over it for ARM display.

    Args:
        account: Account model instance.
        params: LoanParams model instance.
    """
    scenario = get_baseline_scenario(current_user.id)
    scenario_id = scenario.id if scenario else None

    ctx = load_loan_context(account.id, scenario_id, params)
    anchor_events = _load_anchor_events(account.id)
    state = loan_resolver.resolve_loan(
        params, anchor_events, ctx.payments, ctx.rate_changes, date.today(),
    )

    original_for_engine = (
        None if params.is_arm
        else Decimal(str(params.original_principal))
    )
    base_rate = Decimal(str(params.interest_rate))

    return {
        "payments": ctx.payments,
        "rate_changes": ctx.rate_changes,
        "rate_history": ctx.rate_history,
        "escrow_components": ctx.escrow_components,
        "monthly_escrow": ctx.monthly_escrow,
        "state": state,
        "original_for_engine": original_for_engine,
        "base_rate": base_rate,
    }


def _compute_total_payment(account, params, escrow_components):
    """Compute total monthly payment (P&I + escrow) for OOB updates.

    Reads the resolver's ``monthly_payment`` so the escrow / delete-
    escrow HTMX partials display the same P&I as the loan card.
    Returns None when params are absent (no loan configured yet).

    Args:
        account: ORM :class:`Account` instance for the loan account.
            Required to load anchor events for the resolver.
        params: ORM :class:`LoanParams` instance, or None.
        escrow_components: Iterable of :class:`EscrowComponent`.
    """
    if params is None:
        return None
    state, _, _ = _resolve_loan_state(account, params)
    return escrow_calculator.calculate_total_payment(
        state.monthly_payment, escrow_components,
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
    payments = ctx["payments"]
    rate_changes = ctx["rate_changes"]
    rate_history = ctx["rate_history"]
    escrow_components = ctx["escrow_components"]
    monthly_escrow = ctx["monthly_escrow"]
    state = ctx["state"]
    base_rate = ctx["base_rate"]

    # Resolver-derived "current state" fields drive the loan card
    # (E-18 / Commit 15).  current_principal_display == /savings debt
    # card balance == net-worth liability.
    current_principal_display = state.current_balance

    # Anchor events feed both composer calls below.  Loaded once so
    # the two scenarios share the same anchor (and any future trueup
    # cannot drift between the main and floor invocations).
    anchor_events = _load_anchor_events(account.id)
    as_of = date.today()

    # --- Single composer call drives chart, schedule, summary -----
    # Commit 5 of the amortization-engine split: replaces the
    # previous three direct ``generate_schedule`` calls (planned,
    # original, floor) with two ``compute_payoff_scenarios`` calls.
    # Chart series and summary derive from the same return value so
    # they cannot diverge -- the structural fix documented at
    # ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``.
    #
    # ``scenarios_main`` consumes ALL payments (confirmed +
    # projected) with ``extra_monthly=0``.  Its ``history_rows +
    # committed_forward`` slice IS the planned trajectory the
    # amortization tab, payment breakdown, schedule totals,
    # recurrence end_date update, and summary all read.
    scenarios_main = loan_resolver.compute_payoff_scenarios(
        loan_params=params,
        anchor_events=anchor_events,
        payments=payments,
        rate_changes=rate_changes,
        extra_monthly=Decimal("0.00"),
        as_of=as_of,
    )

    # ``scenarios_floor`` re-uses the composer with the projected
    # portion of ``payments`` filtered out.  Its
    # ``committed_forward`` is therefore "pure contractual from
    # balance_as_of" -- the floor's semantic of "where I stand if
    # I cancel all extras today."  When no projected payments
    # exist, floor and committed collapse to the same series.
    confirmed_payments = [p for p in payments if p.is_confirmed]
    scenarios_floor = loan_resolver.compute_payoff_scenarios(
        loan_params=params,
        anchor_events=anchor_events,
        payments=confirmed_payments,
        rate_changes=rate_changes,
        extra_monthly=Decimal("0.00"),
        as_of=as_of,
    )

    # PLANNED-trajectory schedule for display (Amortization Schedule
    # tab) and "life of loan" forecasts (total interest, payoff
    # date, recurrence end_date update).  Composed from
    # ``scenarios_main``: real confirmed history + projected /
    # contractual forward.  Resolver still owns current_balance and
    # monthly_payment so the loan card / debt card / net-worth
    # liability cannot diverge (the E-18 invariant).
    planned_schedule = (
        scenarios_main.history_rows + scenarios_main.committed_forward
    )

    # Build AmortizationSummary for the template / end_date update:
    # monthly_payment from the resolver (single source of truth);
    # total_interest / payoff_date from the planned schedule so the
    # "Total Interest (life of loan)" and "Projected Payoff" cards
    # reflect the user's full trajectory (history + forward).  The
    # composer's ``total_interest_committed`` covers the forward
    # slice only; summing over ``planned_schedule`` adds back the
    # history-row interest the dashboard has always displayed.
    planned_total_interest = sum(
        (row.interest for row in planned_schedule), Decimal("0.00"),
    )
    planned_payoff_date = (
        planned_schedule[-1].payment_date if planned_schedule
        else params.origination_date
    )
    summary = AmortizationSummary(
        monthly_payment=state.monthly_payment,
        total_interest=planned_total_interest,
        payoff_date=planned_payoff_date,
        total_interest_with_extra=planned_total_interest,
        payoff_date_with_extra=planned_payoff_date,
        months_saved=0,
        interest_saved=Decimal("0.00"),
    )
    total_payment = escrow_calculator.calculate_total_payment(
        summary.monthly_payment, escrow_components,
    )

    # Payment allocation breakdown for the current period.  Uses the
    # planned schedule so the breakdown reflects the next planned
    # payment, not the next contractual payment when the user is
    # under-/over-paying.
    payment_breakdown = _compute_payment_breakdown(
        planned_schedule, escrow_components,
    )

    # --- Multi-scenario chart data ---
    # All three series share ``history_rows`` and then diverge:
    #   Original   = history + original_forward
    #                (pure contractual from balance_as_of forward)
    #   Committed  = history + committed_forward
    #                (planned outlays via monthly_override)
    #   Floor      = floor_history + floor_committed_forward
    #                (committed with projections cancelled)
    # Chart labels are taken from the Original series because it is
    # the longest baseline (no override, no extra means contractual
    # all the way to term end); Committed and Floor may pay off
    # earlier but render against the same x-axis with shorter
    # arrays.  ``_balances_for_chart`` pads with $0.00 so Chart.js
    # plots equal-length arrays.
    original_rows = scenarios_main.history_rows + scenarios_main.original_forward
    committed_rows = scenarios_main.history_rows + scenarios_main.committed_forward
    floor_rows = scenarios_floor.history_rows + scenarios_floor.committed_forward
    target_len = len(original_rows)

    chart_labels = [
        row.payment_date.strftime("%b %Y") for row in original_rows
    ]
    chart_original = _balances_for_chart(original_rows, target_len)

    # Committed and Floor render empty when the loan has no payments
    # (the JS overlays just Original).  Preserves the pre-Commit-5
    # dashboard behavior where chart_committed / chart_floor were
    # set conditionally on ``has_payments``.
    has_payments = len(payments) > 0
    if has_payments:
        chart_committed = _balances_for_chart(committed_rows, target_len)
        chart_floor = _balances_for_chart(floor_rows, target_len)
    else:
        chart_committed = []
        chart_floor = []

    # Recurring payment transfer prompt: show when LoanParams exist
    # but no active recurring transfer template targets this account.
    # The template object is also used by the 5.9-1 end_date update.
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

    show_transfer_prompt = existing_template is None

    # --- Auto-update recurrence rule end date (5.9-1) ---
    # When a recurring transfer exists, sync its recurrence rule
    # end_date to the projected payoff date so shadow transactions
    # are not generated beyond payoff.  Uses ``planned_schedule``
    # (confirmed + projected payments) because the question is "when
    # will my recurring transfers stop being needed" -- the user's
    # planned trajectory, not the bank's contractual forecast.  A
    # neg-am user paying $100/mo on a $5K-monthly-interest loan
    # MUST see end_date stay open (the planned schedule ends with
    # positive balance), even though the resolver's contractual
    # forecast would otherwise say "paid off next month."
    if existing_template is not None and existing_template.recurrence_rule is not None:
        _update_transfer_end_date(
            existing_template, summary, planned_schedule, account.id,
        )

    # Source accounts for the transfer prompt dropdown: active accounts
    # excluding the current debt account and other amortizing accounts.
    source_accounts = []
    if show_transfer_prompt:
        all_accounts = (
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
        source_accounts = all_accounts

    # Default to the checking account if one exists.
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    default_source_id = None
    for acct in source_accounts:
        if acct.account_type_id == checking_type_id:
            default_source_id = acct.id
            break

    # Amortization schedule tab: the planned schedule shows the
    # user's trajectory with confirmed actuals + projected payments.
    # The loan card's current_principal stays resolver-derived so
    # the card-vs-schedule split is "what's true now" vs "what's
    # the plan."
    amortization_schedule = planned_schedule
    show_rate_column = bool(params.is_arm)
    schedule_totals = _compute_schedule_totals(
        amortization_schedule, monthly_escrow,
    )
    # MED-04 / E-16: per-row "total monthly outflow" (P&I + escrow +
    # extra) and display rate (storage-domain decimal fraction times
    # 100 for percent display) computed server-side so the schedule
    # template renders without inline Jinja arithmetic.  Parallel to
    # ``amortization_schedule``; consumed via ``loop.index0``.
    schedule_row_totals = [
        round_money(row.payment + monthly_escrow + row.extra_payment)
        for row in amortization_schedule
    ]
    schedule_row_rates_pct = [
        (row.interest_rate if row.interest_rate is not None else base_rate)
        * Decimal("100")
        for row in amortization_schedule
    ] if show_rate_column else None
    # Continuous payment number from origination for the "#" column, so a
    # mid-life loan's schedule reflects total payments made (e.g. 90) and
    # the projected slice keeps counting up instead of restarting at 1.
    # The replay already stamps this on confirmed rows' ``month``; the
    # projected rows carry ``project_forward``'s local 1..N count, so this
    # parallel list renumbers the whole schedule on one scale.  Parallel to
    # ``amortization_schedule``; consumed via ``loop.index0``.
    schedule_row_numbers = [
        payment_number(params.origination_date, row.payment_date)
        for row in amortization_schedule
    ]

    return render_template(
        "loan/dashboard.html",
        account=account,
        account_type=account_type,
        params=params,
        summary=summary,
        current_principal_display=current_principal_display,
        escrow_components=escrow_calculator.build_escrow_display(
            escrow_components,
        ),
        monthly_escrow=monthly_escrow,
        total_payment=total_payment,
        payment_breakdown=payment_breakdown,
        rate_history=rate_history,
        chart_labels=chart_labels,
        chart_original=chart_original,
        chart_committed=chart_committed,
        chart_floor=chart_floor,
        has_payments=has_payments,
        show_transfer_prompt=show_transfer_prompt,
        source_accounts=source_accounts,
        default_source_id=default_source_id,
        amortization_schedule=amortization_schedule,
        schedule_row_totals=schedule_row_totals,
        schedule_row_rates_pct=schedule_row_rates_pct,
        schedule_row_numbers=schedule_row_numbers,
        show_rate_column=show_rate_column,
        schedule_totals=schedule_totals,
        # E-18 / Commit 16: pass today's ISO date as a string so the
        # "Record Loan Balance" form can pre-fill the as-of date and
        # cap the input element's ``max`` attribute.  Computed here
        # rather than via a Jinja global so a future test that
        # freezes ``date.today()`` (see :func:`tests._test_helpers.freeze_today`)
        # sees the frozen value on the page.
        today_iso=date.today().isoformat(),
    )


@loan_bp.route("/accounts/<int:account_id>/loan/setup", methods=["POST"])
@login_required
@require_owner
def create_params(account_id):
    """Create initial loan parameters."""
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    account_type = db.session.get(AccountType, account.account_type_id)
    if account_type is None or not account_type.has_amortization:
        flash("This account type does not support loan parameters.", "warning")
        return redirect(url_for("savings.dashboard"))

    # Check if params already exist.
    existing = db.session.query(LoanParams).filter_by(account_id=account.id).first()
    if existing:
        flash("Loan parameters already configured.", "info")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    errors = _create_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return render_template(
            "loan/setup.html", account=account, account_type=account_type,
        )

    data = _create_schema.load(request.form)

    # Type-specific term validation.
    max_term = account_type.max_term_months
    if max_term and data.get("term_months", 0) > max_term:
        flash(
            f"Term cannot exceed {max_term} months for {account_type.name}.",
            "danger",
        )
        return render_template(
            "loan/setup.html", account=account, account_type=account_type,
        )

    # E-28 / HIGH-06 (Commit 24): the schema's ``@pre_load`` already
    # divides the form percent by 100, so ``data["interest_rate"]``
    # is already the storage-domain fraction.  The DB CHECK
    # ``interest_rate >= 0`` (combined with the schema's
    # ``Range(0, 1)``) enforces the same bounds.

    params = LoanParams(account_id=account.id, **data)
    db.session.add(params)
    db.session.flush()

    # Origination LoanAnchorEvent (E-18 / Commit 15; closes F-9).
    # The loan resolver requires at least one event per loan; Commit
    # 12's migration backfilled events for every pre-existing loan,
    # but new loans created post-migration need an explicit
    # origination event written here so the dashboard's resolver
    # call does not raise ValueError on first render.  Mirrors
    # ``account_service.create_account``'s paired-row insert pattern
    # for :class:`AccountAnchorHistory`.
    db.session.add(LoanAnchorEvent(
        account_id=account.id,
        anchor_date=params.origination_date,
        anchor_balance=params.original_principal,
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.ORIGINATION,
        ),
    ))
    db.session.commit()

    logger.info("Created loan params for account %d", account.id)
    flash("Loan parameters configured.", "success")
    return redirect(url_for("loan.dashboard", account_id=account_id))


@loan_bp.route("/accounts/<int:account_id>/loan/params", methods=["POST"])
@login_required
@require_owner
def update_params(account_id):
    """Update loan parameters."""
    account, params, account_type = _load_loan_account(account_id)
    if account is None:
        abort(404)
    if params is None:
        # Owner reached the params endpoint without configured params
        # (e.g. a stale form, hand-crafted URL, or back-button reload
        # after a deletion).  Redirect to the dashboard so the setup
        # flow takes over instead of conflating this with the IDOR
        # response above.
        flash("Loan parameters are not configured.", "warning")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    errors = _update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    data = _update_schema.load(request.form)

    # Type-specific term validation.
    max_term = account_type.max_term_months
    if max_term and data.get("term_months", 0) > max_term:
        flash(
            f"Term cannot exceed {max_term} months for {account_type.name}.",
            "danger",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    # E-28 / HIGH-06 (Commit 24): the schema's ``@pre_load`` already
    # converted the form percent to the storage-domain fraction, so
    # ``data["interest_rate"]`` is stored verbatim.

    for field, value in data.items():
        if field in _PARAM_FIELDS:
            setattr(params, field, value)

    db.session.commit()
    logger.info("Updated loan params for account %d", account.id)
    flash("Loan parameters updated.", "success")
    return redirect(url_for("loan.dashboard", account_id=account_id))


@loan_bp.route("/accounts/<int:account_id>/loan/trueup", methods=["POST"])
@login_required
@require_owner
def true_up_balance(account_id):
    """Append a dated balance true-up :class:`LoanAnchorEvent` (E-18 D-C / Commit 16).

    Mirrors the checking-account anchor true-up UX (see
    :func:`app.routes.accounts.inline_anchor_update` and
    :func:`app.routes.accounts.true_up`) for loan accounts.  The user
    asserts "the lender reports my balance is $X as of date D"; the
    handler appends a single ``user_trueup`` event and the resolver
    (:func:`app.services.loan_resolver.resolve_loan`) replays
    confirmed payments forward from that event to derive every loan-
    touching display surface.  The table is structurally
    append-only -- a correction is expressed as another append, never
    an edit -- so the new event becomes the active anchor without
    mutating any prior row.

    Validation chain:

      1. ``_load_loan_account`` rejects cross-owner / non-loan
         accounts with the project's "404 for not-found and not-yours"
         response.
      2. :class:`LoanAnchorTrueupSchema` enforces ``anchor_balance >= 0``
         and ``anchor_date <= today`` -- a future trueup is not a
         historical assertion and is rejected before any DB work.
      3. The route enforces ``anchor_date >= params.origination_date``
         here rather than in the schema because the schema does not
         have access to the loan's origination date; folding the
         check into the schema would require coupling
         :class:`LoanParams` into the schemas module.  A
         pre-origination trueup is rejected with a flash and a
         redirect; no event is written.

    Outcomes (mirroring the checking semantics):

      * COMMITTED: a new ``LoanAnchorEvent`` row is written and
        committed; the user is redirected back to the dashboard with
        a success flash.
      * DUPLICATE_SAME_DAY: the partial unique expression index
        ``uq_loan_anchor_events_acct_date_bal_day`` rejected the
        INSERT (the user double-clicked or a network retry replayed
        the same submission on the same UTC calendar day); the route
        treats this as idempotent success -- the prior request
        committed the same value this one was trying to submit -- and
        redirects with an informational flash.

    The function does NOT mutate :class:`LoanParams.current_principal`.
    The column is non-authoritative seed (E-18 / Commit 15) and the
    resolver reads the event log, not the column.
    """
    account, params, _ = _load_loan_account(account_id)
    if account is None:
        abort(404)
    if params is None:
        # Owner reached the trueup endpoint without configured params
        # (e.g. a stale form, hand-crafted URL, or back-button reload
        # after a deletion).  Redirect to the dashboard so the setup
        # flow takes over rather than confusing this with the IDOR
        # 404 above.
        flash("Loan parameters are not configured.", "warning")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    errors = _trueup_schema.validate(request.form)
    if errors:
        flash(
            "Please correct the highlighted errors and try again.",
            "danger",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    data = _trueup_schema.load(request.form)
    anchor_date = data["anchor_date"]
    # Schema returns ``anchor_balance`` as Decimal because the field
    # is declared with ``places=2`` (marshmallow's Decimal field
    # constructs from a string internally); explicit reconstruction
    # via ``Decimal(str(...))`` is defensive against future schema
    # tweaks that might return a different numeric type.
    anchor_balance = Decimal(str(data["anchor_balance"]))

    if anchor_date < params.origination_date:
        flash(
            "Anchor date cannot be before the loan's origination "
            f"date ({params.origination_date.isoformat()}).",
            "danger",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    outcome = anchor_service.apply_loan_anchor_true_up(
        account=account,
        anchor_balance=anchor_balance,
        anchor_date=anchor_date,
    )

    if outcome is AnchorTrueUpOutcome.DUPLICATE_SAME_DAY:
        # F-103 idempotent success path: the prior request committed
        # the same (date, balance) tuple this request was trying to
        # submit.  No new row, but the on-display value is already
        # correct; flash an informational message and redirect.
        flash(
            "Loan balance already recorded for that date.",
            "info",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    logger.info(
        "Loan trueup: account %d set to $%s as of %s",
        account.id, anchor_balance, anchor_date,
    )
    flash(
        f"Recorded loan balance of ${anchor_balance:,.2f} "
        f"as of {anchor_date.strftime('%b %-d, %Y')}.",
        "success",
    )
    return redirect(url_for("loan.dashboard", account_id=account_id))


@loan_bp.route("/accounts/<int:account_id>/loan/rate", methods=["POST"])
@login_required
@require_owner
def add_rate_change(account_id):
    """Record a variable-rate change (HTMX)."""
    account, params, _account_type = _load_loan_account(account_id)
    if account is None or params is None:
        return "Account not found", 404

    errors = _rate_schema.validate(request.form)
    if errors:
        return "Please correct the highlighted errors and try again.", 400

    data = _rate_schema.load(request.form)

    # E-28 / HIGH-06 (Commit 24): the schema's ``@pre_load`` already
    # converted the form percent to the storage-domain fraction.

    entry = RateHistory(
        account_id=account.id,
        effective_date=data["effective_date"],
        interest_rate=data["interest_rate"],
        monthly_pi=data.get("monthly_pi"),
        notes=data.get("notes"),
    )
    db.session.add(entry)

    # Also update the current rate on params.
    params.interest_rate = data["interest_rate"]
    try:
        db.session.commit()
    except IntegrityError as exc:
        # Same-effective-date double-submit (F-104 / C-22): the
        # composite unique ``uq_rate_history_account_effective_date``
        # rejects the second INSERT when the user clicks Save twice
        # in a row.  Roll back, flash a clear message, and re-render
        # the rate history without the proposed duplicate.  A
        # legitimate same-day correction is expressed by editing the
        # existing row, not by appending another.
        db.session.rollback()
        if not is_unique_violation(exc, _RATE_HISTORY_UNIQUE_CONSTRAINT):
            raise
        logger.info(
            "Duplicate rate-history entry prevented for account %d on %s",
            account.id, data["effective_date"],
        )
        flash(
            "A rate change with that effective date already exists. "
            "Edit the existing entry to correct it.",
            "warning",
        )
        rate_history = (
            db.session.query(RateHistory)
            .filter_by(account_id=account.id)
            .order_by(RateHistory.effective_date.desc())
            .all()
        )
        return render_template(
            "loan/_rate_history.html",
            account=account,
            params=params,
            rate_history=rate_history,
        )

    logger.info("Recorded rate change for loan %d: %s", account.id, data["interest_rate"])

    rate_history = (
        db.session.query(RateHistory)
        .filter_by(account_id=account.id)
        .order_by(RateHistory.effective_date.desc())
        .all()
    )
    return render_template(
        "loan/_rate_history.html",
        account=account,
        params=params,
        rate_history=rate_history,
    )


@loan_bp.route("/accounts/<int:account_id>/loan/escrow", methods=["POST"])
@login_required
@require_owner
def add_escrow(account_id):
    """Add an escrow component (HTMX)."""
    account, params, _account_type = _load_loan_account(account_id)
    if account is None:
        return "Account not found", 404

    errors = _escrow_schema.validate(request.form)
    if errors:
        return "Please correct the highlighted errors and try again.", 400

    data = _escrow_schema.load(request.form)

    # E-28 / HIGH-06 (Commit 24): the schema's ``@pre_load``
    # converted the form percent to the storage-domain fraction
    # before validation, so ``data["inflation_rate"]`` is stored
    # verbatim.

    # Check for duplicate name.
    existing = (
        db.session.query(EscrowComponent)
        .filter_by(account_id=account.id, name=data["name"])
        .first()
    )
    if existing:
        return "An escrow component with that name already exists.", 400

    comp = EscrowComponent(account_id=account.id, **data)
    db.session.add(comp)
    db.session.commit()

    logger.info("Added escrow component '%s' to loan %d", data["name"], account.id)

    escrow_components = (
        db.session.query(EscrowComponent)
        .filter_by(account_id=account.id, is_active=True)
        .order_by(EscrowComponent.name)
        .all()
    )

    # Compute updated payment summary for OOB swap.
    monthly_escrow = escrow_calculator.calculate_monthly_escrow(escrow_components)
    total_payment = _compute_total_payment(account, params, escrow_components)

    return render_template(
        "loan/_escrow_list.html",
        account=account,
        escrow_components=escrow_calculator.build_escrow_display(
            escrow_components,
        ),
        monthly_escrow=monthly_escrow,
        total_payment=total_payment,
    )


@loan_bp.route(
    "/accounts/<int:account_id>/loan/escrow/<int:component_id>/delete",
    methods=["POST"],
)
@login_required
@require_owner
def delete_escrow(account_id, component_id):
    """Remove an escrow component (HTMX)."""
    account, _, _account_type = _load_loan_account(account_id)
    if account is None:
        return "Account not found", 404

    comp = db.session.get(EscrowComponent, component_id)
    if comp is None or comp.account_id != account.id:
        return "Component not found", 404

    comp.is_active = False
    db.session.commit()
    logger.info("Deactivated escrow component %d from loan %d", component_id, account.id)

    escrow_components = (
        db.session.query(EscrowComponent)
        .filter_by(account_id=account.id, is_active=True)
        .order_by(EscrowComponent.name)
        .all()
    )

    # Compute updated payment summary for OOB swap.
    params = (
        db.session.query(LoanParams)
        .filter_by(account_id=account.id)
        .first()
    )
    monthly_escrow = escrow_calculator.calculate_monthly_escrow(escrow_components)
    total_payment = _compute_total_payment(account, params, escrow_components)

    return render_template(
        "loan/_escrow_list.html",
        account=account,
        escrow_components=escrow_calculator.build_escrow_display(
            escrow_components,
        ),
        monthly_escrow=monthly_escrow,
        total_payment=total_payment,
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
    # consistent.  ``state.current_balance`` is the same dollar
    # figure rendered on the loan card (E-18 / Commit 15).
    ctx = _load_loan_context(account, params)
    payments = ctx["payments"]
    rate_changes = ctx["rate_changes"]
    state = ctx["state"]
    base_rate = ctx["base_rate"]
    original = ctx["original_for_engine"]

    if mode == "extra_payment":
        extra = Decimal(str(data.get("extra_monthly", "0")))

        # One composer call replaces the previous three direct
        # ``generate_schedule`` calls plus ``calculate_summary``.
        # Chart series and summary metrics derive from the same
        # :class:`PayoffScenarios` return value so they cannot
        # diverge -- the structural fix for the "extra applied to
        # ghost historical months" defect documented at
        # ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``.
        # Replay routes confirmed payments through history (no
        # acceleration), projection routes projected payments
        # through ``monthly_override``, and ``extra_monthly`` is
        # applied only to forward non-override months.
        scenarios = loan_resolver.compute_payoff_scenarios(
            loan_params=params,
            anchor_events=_load_anchor_events(account.id),
            payments=payments,
            rate_changes=rate_changes,
            extra_monthly=extra,
            as_of=date.today(),
        )

        # Chart series: every scenario shares ``history_rows`` and
        # then diverges.  ``original_forward`` is the longest
        # baseline (pure contractual, no override, no extra);
        # committed/accelerated may pay off earlier, in which case
        # their series are padded with $0.00 (the post-payoff
        # balance) so all three datasets line up against the same
        # x-axis of months.  Chart.js plots equal-length arrays
        # against the shared ``chart_labels``.
        original_rows = scenarios.history_rows + scenarios.original_forward
        committed_rows = scenarios.history_rows + scenarios.committed_forward
        accelerated_rows = (
            scenarios.history_rows + scenarios.accelerated_forward
        )
        target_len = len(original_rows)

        chart_labels = [
            row.payment_date.strftime("%b %Y") for row in original_rows
        ]
        chart_original = _balances_for_chart(original_rows, target_len)
        chart_committed = _balances_for_chart(committed_rows, target_len)
        chart_accelerated = _balances_for_chart(accelerated_rows, target_len)

        has_payments = len(payments) > 0

        # Comparison metrics: committed forward vs. original forward.
        # Both slices share the same starting state from replay, so
        # the difference quantifies what the user's planned outlays
        # save vs. paying pure contractual from today onward.  This
        # is the load-bearing single-source-of-truth invariant --
        # months_saved on the chart and the displayed label derive
        # from the same forward-row lists.
        committed_months_saved = (
            len(scenarios.original_forward)
            - len(scenarios.committed_forward)
        )
        original_forward_interest = sum(
            (r.interest for r in scenarios.original_forward),
            Decimal("0.00"),
        )
        committed_forward_interest = sum(
            (r.interest for r in scenarios.committed_forward),
            Decimal("0.00"),
        )
        # Route through round_money so the half-cent boundary follows
        # the project default ROUND_HALF_UP (E-26).
        committed_interest_saved = round_money(
            original_forward_interest - committed_forward_interest,
        )

        payoff_summary = AmortizationSummary(
            monthly_payment=state.monthly_payment,
            total_interest=scenarios.total_interest_committed,
            payoff_date=scenarios.payoff_date_committed,
            total_interest_with_extra=scenarios.total_interest_accelerated,
            payoff_date_with_extra=scenarios.payoff_date_accelerated,
            months_saved=scenarios.months_saved,
            interest_saved=scenarios.interest_saved,
        )

        return render_template(
            "loan/_payoff_results.html",
            mode=mode,
            payoff_summary=payoff_summary,
            chart_labels=chart_labels,
            chart_original=chart_original,
            chart_committed=chart_committed if has_payments else [],
            chart_accelerated=chart_accelerated,
            has_payments=has_payments,
            committed_months_saved=committed_months_saved,
            committed_interest_saved=committed_interest_saved,
        )

    if mode == "target_date":
        target_date = data.get("target_date")
        if not target_date:
            return render_template(
                "loan/_payoff_results.html",
                error="Target date is required.",
            )

        # Resolver-derived current balance and monthly payment
        # (E-18 / Commit 15).  Same dollar figures the loan card
        # displays.  ``calculate_remaining_months`` is the engine's
        # calendar-month delta from origination to today; the
        # resolver does not expose it on :class:`LoanState` because
        # the resolver's per-loan amortization derives months
        # internally, but the payoff calculator's ``target_date``
        # branch still needs the raw count for the binary search.
        real_principal = state.current_balance
        monthly_payment = state.monthly_payment
        remaining_months = amortization_engine.calculate_remaining_months(
            params.origination_date, params.term_months,
        )

        required_extra = amortization_engine.calculate_payoff_by_date(
            current_principal=real_principal,
            annual_rate=base_rate,
            remaining_months=remaining_months,
            target_date=target_date,
            origination_date=date.today().replace(day=1),
            payment_day=params.payment_day,
            original_principal=original,
            term_months=params.term_months,
            rate_changes=rate_changes,
            # SSOT contractual: the binary search must use the same
            # P&I baseline the loan card displays, so the rendered
            # ``total_monthly = monthly_payment + required_extra``
            # is internally consistent (D-2 closure).
            contractual_payment=monthly_payment,
        )

        total_monthly = (
            round_money(monthly_payment + required_extra)
            if required_extra is not None and required_extra > 0
            else None
        )
        return render_template(
            "loan/_payoff_results.html",
            mode=mode,
            required_extra=required_extra,
            monthly_payment=monthly_payment,
            total_monthly=total_monthly,
        )

    return render_template(
        "loan/_payoff_results.html",
        error="Invalid mode.",
    )


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

    # Shared loan context: payments, rate changes, and resolver
    # state.  Identical to the dashboard's data loading so the
    # "current" refinance baseline matches the loan card.
    ctx = _load_loan_context(account, params)
    state = ctx["state"]

    current_schedule = state.schedule
    current_real_principal = state.current_balance

    # Paid-off loan: no refinance comparison is meaningful.  Use the
    # resolver-derived current_balance for the gate so editing the
    # stored ``current_principal`` column (still legal until Commit
    # 16) cannot trick the route into rendering a refinance form
    # against an already-paid-off loan.
    if not current_schedule or current_real_principal <= Decimal("0.00"):
        return render_template(
            "loan/_refinance_results.html",
            error=(
                "This loan is paid off. "
                "No refinance comparison available."
            ),
        )

    # Determine refinance principal: user override or auto-calculated
    # from current real balance + closing costs.
    closing_costs = data["closing_costs"]
    if data["new_principal"] is not None:
        refi_principal = data["new_principal"]
    else:
        refi_principal = current_real_principal + closing_costs

    # E-28 / HIGH-06 (Commit 24): the schema's ``@pre_load``
    # already divided the form percent by 100, so ``data["new_rate"]``
    # is the storage-domain decimal fraction the engine consumes.
    refi_rate = data["new_rate"]
    refi_term = data["new_term_months"]

    # Compute refinance monthly P&I.
    refi_monthly = amortization_engine.calculate_monthly_payment(
        refi_principal, refi_rate, refi_term,
    )

    # Compute refinance schedule for total interest and payoff date.
    # Commit 7 of the amortization-engine split (see
    # ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``):
    # the refi schedule is a pure forward projection from a known
    # starting state (``refi_principal`` at next month's pay date) so
    # it maps directly onto :func:`amortization_engine.project_forward`.
    # No replay, no projections-as-overrides, no extra -- the
    # contractual P&I drives every row.
    schedule_start = date.today().replace(day=1)
    starting_date = amortization_engine.advance_to_next_payment_date(
        schedule_start, params.payment_day,
    )
    refi_schedule = amortization_engine.project_forward(
        starting_balance=refi_principal,
        starting_date=starting_date,
        annual_rate=refi_rate,
        remaining_months=refi_term,
        payment_day=params.payment_day,
        contractual_payment=refi_monthly,
        monthly_override=None,
        extra_monthly=Decimal("0.00"),
        rate_changes_remaining=None,
    )
    refi_total_interest = sum(
        (row.interest for row in refi_schedule), Decimal("0.00"),
    )
    refi_payoff = (
        refi_schedule[-1].payment_date if refi_schedule
        else schedule_start
    )

    # Current metrics from the resolver state -- same dollar figures
    # as the loan card (E-18 / Commit 15).
    current_monthly = state.monthly_payment
    current_total_interest = state.total_interest
    current_payoff = state.payoff_date
    current_remaining_months = len(current_schedule)

    # Comparison metrics.
    monthly_savings = current_monthly - refi_monthly
    interest_savings = current_total_interest - refi_total_interest

    # Break-even: ceil(closing_costs / monthly_savings) when both > 0.
    # This is the standard consumer-facing approximation assuming
    # constant monthly savings.  A future enhancement could compute
    # the crossover point month-by-month for greater precision.
    break_even_months = None
    if (
        closing_costs > Decimal("0.00")
        and monthly_savings > Decimal("0.00")
    ):
        break_even_months = int(
            (closing_costs / monthly_savings).to_integral_value(
                rounding=ROUND_CEILING,
            )
        )

    # MED-04 / E-16: pre-compute the principal delta and its absolute
    # magnitude server-side so the refinance template renders without
    # inline arithmetic.  Previously the template subtracted
    # ``refi_principal - current_principal`` (TA-07) and applied a
    # unary negation ``-princ_diff`` (TA-08) on Decimal values.
    principal_diff = refi_principal - current_real_principal
    principal_diff_abs = abs(principal_diff)

    comparison = {
        "current_monthly": current_monthly,
        "current_total_interest": current_total_interest,
        "current_payoff": current_payoff,
        "current_remaining_months": current_remaining_months,
        "current_principal": current_real_principal,
        "refi_monthly": refi_monthly,
        "refi_total_interest": refi_total_interest,
        "refi_payoff": refi_payoff,
        "refi_term": refi_term,
        "refi_principal": refi_principal,
        "monthly_savings": monthly_savings,
        "interest_savings": interest_savings,
        "break_even_months": break_even_months,
        "closing_costs": closing_costs,
        "principal_diff": principal_diff,
        "principal_diff_abs": principal_diff_abs,
    }

    return render_template(
        "loan/_refinance_results.html",
        comparison=comparison,
    )


@loan_bp.route("/accounts/<int:account_id>/loan/create-transfer", methods=["POST"])
@login_required
@require_owner
def create_payment_transfer(account_id):
    """Create a recurring monthly transfer to a debt account.

    Creates a RecurrenceRule (monthly pattern), a TransferTemplate
    (from the selected source account to the debt account), and
    generates Transfer records (with shadow transactions) for
    existing pay periods.

    The amount defaults to the computed monthly payment (P&I + escrow).
    The user may override with a custom amount.
    """
    account, params, _ = _load_loan_account(account_id)
    if account is None:
        abort(404)
    if params is None:
        flash("Loan parameters are not configured.", "warning")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    # Validate the form and resolve + ownership-check the source account
    # (shared with investment.create_contribution_transfer).
    result = validate_and_resolve_source_account(
        _transfer_schema,
        dest_account_id=account_id,
        redirect_endpoint="loan.dashboard",
        redirect_kwargs={"account_id": account_id},
    )
    if isinstance(result, Response):
        return result
    source_account, data = result

    # Determine the transfer amount and whether it auto-derives.  A
    # user-supplied amount is respected verbatim (no live derivation);
    # the computed default opts into live derivation so the projected
    # cash debit tracks the loan's monthly payment after an escrow or
    # rate change instead of staying frozen at default_amount.
    if "amount" in data and data["amount"] is not None:
        transfer_amount = data["amount"]
        derive_from_loan = False
    else:
        # P&I + escrow as the full monthly payment.  Resolver state
        # owns the P&I figure for both ARM (re-amortized from the
        # latest anchor's balance over the remaining term) and
        # fixed-rate (contractual payment from origination), so the
        # transfer default matches the dashboard's displayed
        # "Total Monthly (with escrow)" exactly (E-18 / Commit 15).
        state, _, _ = _resolve_loan_state(account, params)
        monthly_pi = state.monthly_payment
        escrow_components = (
            db.session.query(EscrowComponent)
            .filter_by(account_id=account.id, is_active=True)
            .all()
        )
        transfer_amount = escrow_calculator.calculate_total_payment(
            monthly_pi, escrow_components,
        )
        derive_from_loan = True

    # Create monthly recurrence rule.
    monthly_pattern_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.MONTHLY,
    )
    rule = RecurrenceRule(
        user_id=current_user.id,
        pattern_id=monthly_pattern_id,
        day_of_month=params.payment_day,
    )
    db.session.add(rule)
    db.session.flush()

    # Create transfer template via the shared builder.  Loan-payment
    # transfers set derive_from_loan so the projected cash debit tracks
    # the live monthly payment after an escrow or rate change.
    template_name = f"{source_account.name} -> {account.name} Payment"
    template = build_recurring_transfer_template(
        source_account=source_account,
        dest_account=account,
        rule=rule,
        name=template_name,
        default_amount=transfer_amount,
        derive_from_loan=derive_from_loan,
    )

    namedup_redirect = flush_template_or_namedup_redirect(
        redirect_endpoint="loan.dashboard",
        redirect_kwargs={"account_id": account_id},
    )
    if namedup_redirect is not None:
        return namedup_redirect

    # Generate transfers for existing pay periods.
    generate_transfers_for_all_periods(template)

    db.session.commit()

    logger.info(
        "Created recurring payment transfer for loan %d: $%s from account %d",
        account.id, transfer_amount, source_account.id,
    )
    flash(
        f"Recurring monthly transfer of ${transfer_amount:,.2f} created "
        f"from {source_account.name} to {account.name}.",
        "success",
    )
    return redirect(url_for("loan.dashboard", account_id=account_id))
