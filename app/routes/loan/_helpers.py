"""
Shekel Budget App -- Loan route package: shared helpers.

The Marshmallow schema singletons, the loan-account loader / ownership check,
the resolver-state and full-context loaders, and the chart-balance utilities
shared across the loan route sub-modules.  Schema instances are constructed
once at import time so every handler reuses the same instance (Marshmallow
contract), preserving the pre-split monolith's behaviour.
"""

from datetime import date
from decimal import Decimal

from flask import abort, flash, redirect, url_for
from flask_login import current_user

from app.extensions import db
from app.models.account import Account
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_params import LoanParams
from app.models.ref import AccountType
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
from app.services import escrow_calculator, loan_resolver
from app.services.loan_payment_service import load_loan_context
from app.services.loan_resolver import LoanState
from app.services.scenario_resolver import get_baseline_scenario


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


def _require_configured_loan(account_id):
    """Load a loan account that the owner has fully configured, or reject.

    The shared precondition for the parameter-mutation and
    payment-transfer routes: the account must be owned by the current
    user, be an amortizing type, AND already have ``LoanParams``.  On
    failure it raises (never returns) the appropriate response:

      * 404 (``abort``) for a cross-owner / non-existent / non-loan
        account -- the project's "404 for not-found and not-yours" rule.
      * a redirect to the dashboard with a warning flash when the owner
        reached the endpoint without configured params (a stale form,
        hand-crafted URL, or back-button reload after a deletion);
        ``abort(redirect(...))`` raises this as a 302 so callers do not
        repeat the load/guard, and it is never conflated with the IDOR
        404 above.

    Args:
        account_id: The loan account id from the route.

    Returns:
        (account, params, account_type) -- only on success; failure
        paths raise.
    """
    account, params, account_type = _load_loan_account(account_id)
    if account is None:
        abort(404)
    if params is None:
        flash("Loan parameters are not configured.", "warning")
        abort(redirect(url_for("loan.dashboard", account_id=account_id)))
    return account, params, account_type


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


def _build_chart_series(series_rows):
    """Build aligned Chart.js label + balance arrays for loan scenarios.

    Every scenario shares the same x-axis: labels come from the FIRST
    series (the original / longest contractual baseline), and every
    series' balances are padded to that baseline's length with $0.00 via
    :func:`_balances_for_chart` so Chart.js plots equal-length arrays
    against the shared labels.  Shared by the dashboard's three-series
    chart (original / committed / floor) and the payoff calculator's
    (original / committed / accelerated).

    Args:
        series_rows: Insertion-ordered mapping of series name -> the
            full :class:`AmortizationRow` list (history + forward,
            already concatenated by the caller).  The FIRST entry is the
            baseline whose length and dates define the x-axis.

    Returns:
        Tuple of (chart_labels, balances) where ``balances`` is a dict
        mapping each series name to its padded float list.
    """
    baseline_rows = next(iter(series_rows.values()))
    target_len = len(baseline_rows)
    chart_labels = [
        row.payment_date.strftime("%b %Y") for row in baseline_rows
    ]
    balances = {
        name: _balances_for_chart(rows, target_len)
        for name, rows in series_rows.items()
    }
    return chart_labels, balances
