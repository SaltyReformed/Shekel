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
from app.services.loan_loaders import load_loan_anchor_facts
from app.services.loan_payment_service import (
    LoanContext,
    confirmed_loan_view,
    load_loan_context,
    resolve_loan_seeded,
)
from app.services.loan_resolver import LoanState
from app.services.rate_period_engine import payment_number
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.auth_helpers import get_or_404
from app.utils.money import round_money


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
    # Read switch (plan Section 8): resolve through ``resolve_loan_seeded`` so
    # the loan card's ``current_balance`` is the genesis-ledger confirmed
    # balance (falling back to the anchor replay when the ledger has not
    # opened this loan).  The anchor facts are synthesized from the immutable
    # params + the loan's true-up events.  Ownership was already verified by
    # ``_load_loan_account -> get_or_404`` before this runs, satisfying the
    # reader's trust-the-caller contract.
    state = resolve_loan_seeded(
        loan_resolver.LoanInputs(
            params, load_loan_anchor_facts(params),
            ctx.payments, ctx.rate_changes,
        ),
        account.id, scenario_id, date.today(),
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
        escrow_components: Today's active escrow lines, resolved
            (:class:`~app.services.escrow_calculator.ResolvedEscrowLine`).
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
            the length of the longest series (:func:`_build_chart_series`).

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

    Every series shares one x-axis: labels come from the LONGEST series,
    and every series' balances are padded to that length with $0.00 via
    :func:`_balances_for_chart` so Chart.js plots equal-length arrays
    against the shared labels.  The longest series is the correct label
    baseline because a payment plan slower than the contractual P&I (a
    sub-P&I recurring transfer against a balance the contractual payment
    would clear early) makes ``committed`` -- or the lever's
    ``accelerated`` -- run more months than the contractual ``original``;
    keying the labels off ``original`` alone would leave those extra (and
    correct) tail points plotting past the last labelled tick.  A series
    shorter than the longest pads with $0.00, the literal post-payoff
    balance, so the padding never invents a value.  Shared by the band
    chart (:func:`build_band_chart`) and the payoff lever's overlay
    (:func:`accelerated_overlay`).

    Args:
        series_rows: Mapping of series name -> the full
            :class:`AmortizationRow` list (history + forward, already
            concatenated by the caller).  Every series shares the same
            monthly payment-date sequence from the same starting month, so
            the longest series' dates label every shorter (padded) one; on
            a length tie the first-inserted series wins (Python ``max``),
            which keeps ``original`` the label baseline in the common case.

    Returns:
        Tuple of (chart_labels, balances) where ``balances`` is a dict
        mapping each series name to its padded float list.
    """
    baseline_rows = max(series_rows.values(), key=len)
    target_len = len(baseline_rows)
    chart_labels = [
        row.payment_date.strftime("%b %Y") for row in baseline_rows
    ]
    balances = {
        name: _balances_for_chart(rows, target_len)
        for name, rows in series_rows.items()
    }
    return chart_labels, balances


def build_band_chart(scenarios, has_payments):
    """Serialize the loan-detail band chart: one balance line on the contractual axis.

    The Fable 5 loan-detail band chart (docs/design/loan_audit.md, locked
    anatomy) draws a SINGLE balance trajectory -- the committed plan (confirmed
    history solid, projected forward dashed) when the loan has a recurring
    payment plan, otherwise the pure contractual schedule -- which the client
    splits at the confirmed / projected boundary via
    :func:`ShekelChart.splitSegment` (``current_index``).  The line is padded to
    the LONGEST-series x-axis by :func:`_build_chart_series` (``original`` vs
    ``committed``), the same baseline :func:`accelerated_overlay` reproduces, so
    a shorter (paid-sooner) trajectory and the lever's preview align to identical
    labels and cannot drift -- and a slower-than-contractual ``committed`` line
    never runs past the last labelled tick.

    Args:
        scenarios: The baseline :class:`PayoffScenarios` (``extra_monthly`` 0).
        has_payments: ``True`` when the loan has a recurring payment plan;
            selects the committed line, else the contractual original.

    Returns:
        dict with ``labels`` (list[str]), ``balance`` (list[float] padded to the
        contractual length), and ``current_index`` (int -- the count of
        confirmed history rows, i.e. the solid / dashed boundary).
    """
    chart_labels, balances = _build_chart_series({
        "original": scenarios.history_rows + scenarios.original_forward,
        "committed": scenarios.history_rows + scenarios.committed_forward,
    })
    return {
        "labels": chart_labels,
        "balance": (
            balances["committed"] if has_payments else balances["original"]
        ),
        "current_index": len(scenarios.history_rows),
    }


def accelerated_overlay(scenarios):
    """Forward-only accelerated balances for the band chart's payoff-lever preview.

    The green dashed "pay off sooner" preview (docs/design/loan_audit.md, locked
    anatomy) the band chart overlays when the extra-payment lever runs: the
    accelerated trajectory's FORWARD slice only, with the confirmed-history
    positions left ``None`` so the green line begins at Today and diverges from
    the committed dashed line rather than redrawing the shared solid history.
    Padded to the SAME x-axis as :func:`build_band_chart` by passing the same
    ``original`` and ``committed`` series into :func:`_build_chart_series`: the
    band's labels span ``max(len(original), len(committed))``, and since
    ``accelerated`` (committed plus extra) can never run longer than
    ``committed``, including ``committed`` here makes the overlay's padded length
    equal the band's label count exactly, so the overlay aligns to the band
    chart's labels one-to-one even when a slower-than-contractual committed line
    is the longest series.

    Args:
        scenarios: The lever's :class:`PayoffScenarios` (``extra_monthly`` the
            requested extra).

    Returns:
        list of ``float | None`` whose length equals the band chart's balance
        array: the first ``len(history_rows)`` entries are ``None`` (no overlay
        over confirmed history), the rest are the accelerated forward balances
        padded with post-payoff zeros.
    """
    _chart_labels, balances = _build_chart_series({
        "original": scenarios.history_rows + scenarios.original_forward,
        "committed": scenarios.history_rows + scenarios.committed_forward,
        "accelerated": scenarios.history_rows + scenarios.accelerated_forward,
    })
    n_history = len(scenarios.history_rows)
    return [None] * n_history + balances["accelerated"][n_history:]


def build_baseline_scenarios(loan_inputs, scenario_id, as_of):
    """Run the baseline payoff-scenario composer call for the loan detail page.

    One ``compute_payoff_scenarios`` call (``extra_monthly=0``) whose band
    chart, payment breakdown, and life-of-loan summary all derive from the same
    return value so they cannot diverge (the structural fix documented at
    ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``).
    The returned scenario consumes ALL payments (confirmed + projected): its
    ``history_rows + committed_forward`` slice IS the planned trajectory the band
    chart, payment breakdown, and summary read, while ``original_forward``
    supplies the contractual x-axis baseline.

    Read switch: reads the genesis-ledger confirmed view ONCE via
    :func:`loan_payment_service.confirmed_loan_view` and threads it into the
    composer as ``confirmed_view``, so the chart / summary derive from the same
    real owed balance AND ledger-derived confirmed history the loan card
    (:func:`_resolve`) shows -- they cannot desync off-schedule.

    Shared by the dashboard GET (which also reads the full scenario for the
    summary / breakdown) and the ARM rate-change band producer
    (:func:`build_loan_band_chart`), so the single composer call lives in exactly
    one place.

    Args:
        loan_inputs: The loan's :class:`loan_resolver.LoanInputs` bundle with
            ALL payments.
        scenario_id: The baseline scenario id (or ``None``) for the ledger
            seed scope.
        as_of: The replay/projection boundary (typically ``date.today()``).

    Returns:
        The baseline :class:`loan_resolver.PayoffScenarios`.
    """
    view = confirmed_loan_view(
        loan_inputs.loan_params.account_id, scenario_id, as_of,
    )
    return loan_resolver.compute_payoff_scenarios(
        loan_inputs=loan_inputs,
        extra_monthly=Decimal("0.00"),
        as_of=as_of,
        confirmed_view=view,
    )


def _loan_inputs(params, route_ctx) -> loan_resolver.LoanInputs:
    """Bundle a loan's resolver inputs from its params + loaded route context.

    The single :class:`loan_resolver.LoanInputs` constructor for the loan ROUTE
    surfaces (the dashboard GET and the band-chart producer), so the
    (params, anchor facts, payments, rate changes) assembly lives in one place.

    Args:
        params: ORM :class:`LoanParams` instance (also the anchor-fact
            synthesis source).
        route_ctx: The :class:`_RouteLoanContext` from
            :func:`_load_loan_context`; its ``loan`` carries the prepared
            payments and rate changes.

    Returns:
        The :class:`loan_resolver.LoanInputs` bundle with ALL payments.
    """
    return loan_resolver.LoanInputs(
        loan_params=params,
        anchor_events=load_loan_anchor_facts(params),
        payments=route_ctx.loan.payments,
        rate_changes=route_ctx.loan.rate_changes,
    )


def build_loan_band_chart(account, params):
    """Recompute the loan-detail band chart dict from the current loan state.

    The band's balance-over-time chart is a function of the loan's committed
    trajectory, so a mutation that RE-AMORTIZES the loan (an ARM rate change --
    :func:`app.routes.loan.escrow_rates.add_rate_change`) leaves the band stale
    until the chart is rebuilt.  This is the single producer both the dashboard
    GET path and that HTMX rate route share (via :func:`build_baseline_scenarios`
    + :func:`build_band_chart`), so the refreshed chart cannot diverge from the
    initially-rendered one.  Ownership is verified by the caller before this runs
    (``add_rate_change`` is ``require_owner``-gated), satisfying the resolver's
    trust-the-caller contract.

    Args:
        account: ORM :class:`Account` instance for the loan.
        params: ORM :class:`LoanParams` instance.

    Returns:
        The serializable band-chart dict (``labels`` / ``balance`` /
        ``current_index``) -- identical in shape to the dashboard's initial
        ``band_chart`` -- for the rate route to hand to ``loan_detail.js``.
    """
    ctx = _load_loan_context(account, params)
    scenario = get_baseline_scenario(current_user.id)
    scenario_id = scenario.id if scenario else None
    scenarios = build_baseline_scenarios(
        _loan_inputs(params, ctx), scenario_id, date.today(),
    )
    return build_band_chart(scenarios, len(ctx.loan.payments) > 0)


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


def build_schedule_context(planned_schedule, monthly_escrow, current_rate, params):
    """Build the amortization-schedule template context.

    Shared by the loan detail page's transitional schedule tab and the
    standalone schedule route (:mod:`app.routes.loan.schedule`).  The planned
    schedule shows the user's trajectory with confirmed actuals + projected
    payments.  Three index-parallel lists are computed server-side (consumed via
    ``loop.index0``) so the schedule template renders without inline Jinja
    arithmetic (MED-04 / E-16): per-row total monthly outflow (P&I + escrow +
    extra), the ARM display rate (storage-domain fraction times 100), and a
    continuous payment number from origination so a mid-life loan's "#" column
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
