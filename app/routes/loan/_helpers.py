"""
Shekel Budget App -- Loan route package: shared helpers.

The Marshmallow schema singletons, the loan-account loader / ownership check,
the resolver-state and full-context loaders, and the chart-balance utilities
shared across the loan route sub-modules.  Schema instances are constructed
once at import time so every handler reuses the same instance (Marshmallow
contract), preserving the pre-split monolith's behaviour.
"""

from dataclasses import dataclass
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
from app.services.loan_payment_service import LoanContext, load_loan_context
from app.services.loan_resolver import LoanState
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.auth_helpers import get_or_404


# Field allowlist for the loan-params update route -- the LoanParams
# columns the update form may set directly.  ``current_principal`` is
# excluded (E-18 / D-C): it is non-authoritative seed and the resolver
# derives the displayed balance from :class:`LoanAnchorEvent`.
# ``interest_rate`` is excluded (DH-#56): the column was retired, and the
# form's rate field edits the loan's origination RateHistory row through
# ``update_params``'s ``_upsert_origination_rate`` instead of a column set.
_PARAM_FIELDS = {
    "payment_day", "term_months",
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
    account = get_or_404(Account, account_id)
    if account is None:
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


@dataclass(frozen=True)
class _RouteLoanContext:
    """Resolver state plus the loaded loan context for the loan ROUTE surfaces.

    Composes rather than copies: ``loan`` is the service-loaded
    :class:`LoanContext` (the prepared payment / rate-change feeds, escrow,
    and rate history); ``state`` is the resolver output; and
    ``current_rate`` is the route-derived rate the refinance / payoff
    calculators read.  Replaces the former untyped dict so the dashboard
    and calculator consumers read typed attributes (``ctx.state`` /
    ``ctx.loan.payments`` / ``ctx.current_rate``) instead of string keys.
    """

    state: LoanState
    loan: LoanContext
    current_rate: Decimal


def _resolve(account, params) -> tuple[LoanState, LoanContext]:
    """Run the loan resolver once; return ``(state, loaded context)``.

    The single 4-step resolve sequence -- baseline scenario lookup ->
    service context load -> anchor events -> resolver -- shared by
    :func:`_resolve_loan_state` and :func:`_load_loan_context` so the
    sequence lives in exactly one place.

    Args:
        account: ORM :class:`Account` instance.
        params: ORM :class:`LoanParams` instance.

    Returns:
        ``(LoanState, LoanContext)`` -- the resolver output and the
        service-loaded context it was built from.
    """
    scenario = get_baseline_scenario(current_user.id)
    scenario_id = scenario.id if scenario else None
    ctx = load_loan_context(account.id, scenario_id, params)
    anchor_events = _load_anchor_events(account.id)
    state = loan_resolver.resolve_loan(
        loan_resolver.LoanInputs(
            params, anchor_events, ctx.payments, ctx.rate_changes,
        ),
        date.today(),
    )
    return state, ctx


def _resolve_loan_state(account, params) -> LoanState:
    """Return the resolver :class:`LoanState` for a loan.

    Thin accessor over :func:`_resolve` for the callers that need only
    the resolver state (the escrow total-payment and payment-transfer
    paths), not the loaded payment / rate-change feeds.

    Args:
        account: ORM :class:`Account` instance.
        params: ORM :class:`LoanParams` instance.

    Returns:
        :class:`LoanState` -- resolver source of truth for
        current_balance / monthly_payment / schedule / payoff_date /
        total_interest.
    """
    state, _ = _resolve(account, params)
    return state


def _load_loan_context(account, params) -> _RouteLoanContext:
    """Load payment history, escrow, rate changes, and resolver state.

    Delegates payment / escrow / rate-change loading to
    :func:`loan_payment_service.load_loan_context`, then runs the
    loan resolver (E-18 / Commit 13) to derive the authoritative
    current balance, monthly payment, and current rate.  Display
    surfaces read ``ctx.state`` (``state.current_balance`` /
    ``state.current_rate``) instead of the stored
    ``LoanParams.current_principal`` column and the retired
    ``LoanParams.interest_rate`` column (E-18 / Commit 15, decision D-A;
    DH-#56 dropped ``interest_rate`` entirely in favour of the
    origination :class:`RateHistory` row).

    Returns a :class:`_RouteLoanContext` with:
        state: :class:`LoanState` from the resolver.
        loan: the service-loaded :class:`LoanContext` -- ``loan.payments``
            (prepared, escrow-subtracted, month-aligned), ``loan.rate_changes``
            (or None), ``loan.rate_history`` (RateHistory for display),
            ``loan.escrow_components`` (active), ``loan.monthly_escrow``.
        current_rate: Decimal annual interest rate in effect today --
            ``state.current_rate`` (DH-#56), the loan's current rate used
            by the refinance / payoff calculators as the existing loan's
            rate.  Replaces the read of the retired
            ``LoanParams.interest_rate`` column; the resolver derives it
            from the rate-period containing today.

    Args:
        account: Account model instance.
        params: LoanParams model instance.
    """
    state, ctx = _resolve(account, params)

    return _RouteLoanContext(
        state=state,
        loan=ctx,
        current_rate=state.current_rate,
    )


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
    state = _resolve_loan_state(account, params)
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
