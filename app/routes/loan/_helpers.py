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
    EscrowLineMergeSchema,
    EscrowLineRenameSchema,
    EscrowVersionSchema,
    LoanAnchorTrueupSchema,
    LoanParamsCreateSchema,
    LoanParamsUpdateSchema,
    LoanPaymentExtraSchema,
    LoanPaymentTransferSchema,
    PayoffCalculatorSchema,
    RateChangeSchema,
    RefinanceSchema,
)
from app.services import balance_at, escrow_calculator, loan_resolver
from app.services.balance_at import LoanFigures
from app.services.loan_loaders import (
    latest_settled_payment_period_start,
    load_loan_anchor_facts,
)
from app.services.recurring_transfer_query import loan_standing_extra
from app.services.loan_payment_service import (
    LoanContext,
    confirmed_loan_view,
    load_loan_context,
)
from app.services.rate_period_engine import payment_number
from app.services.resolution_context import BalanceContext
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
_escrow_version_schema = EscrowVersionSchema()
_escrow_rename_schema = EscrowLineRenameSchema()
_escrow_merge_schema = EscrowLineMergeSchema()
_payoff_schema = PayoffCalculatorSchema()
_refinance_schema = RefinanceSchema()
_transfer_schema = LoanPaymentTransferSchema()
_payment_extra_schema = LoanPaymentExtraSchema()


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
    """One read pass's loan state for the loan ROUTE surfaces, sourced from the seam.

    The loan detail page reads its displayed balance and rich figures from the
    :mod:`app.services.balance_at` seam like every other loan surface (plan step
    C4), instead of resolving a private :class:`LoanState` and rendering
    ``state.current_balance`` -- a balance-at-T produced outside the seam that,
    for a loan whose genesis ledger is missing, answered from the money-blind
    anchor replay while the seam folds the source facts (finding B-13).

    Composes three inputs from ONE ``BalanceContext``:

      * ``balance_ctx`` -- the read pass's context (memoizes the loan's single
        resolution + ledger walk); its ``as_of`` is today.
      * ``loan`` -- the service-loaded :class:`LoanContext` (prepared payments,
        rate changes, escrow lines / components, monthly escrow, rate history)
        the route's own schedule composer and escrow card need, which the seam
        does not publish.
      * ``figures`` -- the seam's :class:`LoanFigures` (payment, rate, payoff,
        arm), carrying deliberately NO balance.

    The balance is a property that reads the seam on demand; the figures fields
    are exposed as properties so the dashboard / calculators read typed
    attributes (``ctx.current_balance`` / ``ctx.monthly_payment`` /
    ``ctx.current_rate`` / ``ctx.payoff_date``) exactly as they read the old
    ``ctx.state.*``.
    """

    account: Account
    balance_ctx: BalanceContext
    loan: LoanContext
    figures: LoanFigures

    @property
    def current_balance(self) -> Decimal:
        """The loan's balance-at-today from the seam (the fold, plan C4).

        Reads :func:`app.services.balance_at.balance_at` at the pass's ``as_of``
        (today) -- the same seam entry the /savings tile, the net-worth hero,
        and /debt-strategy read, so the loan card's balance is now produced in
        the one tested place instead of off a private resolver.
        """
        return balance_at.balance_at(
            self.account, self.balance_ctx, self.balance_ctx.as_of,
        )

    @property
    def monthly_payment(self) -> Decimal:
        """The loan's P&I payment as of today (the seam's resolved figure)."""
        return self.figures.monthly_payment

    @property
    def current_rate(self) -> Decimal:
        """The annual interest rate in effect today (the seam's resolved figure)."""
        return self.figures.current_rate

    @property
    def payoff_date(self) -> date | None:
        """The committed schedule's last payment date (the seam's resolved figure)."""
        return self.figures.payoff_date


def _require_figures(account, balance_ctx: BalanceContext) -> LoanFigures:
    """Return a configured loan's seam :class:`LoanFigures`, or fail loud.

    The loan route only reaches the seam for a loan the caller has already
    confirmed is configured (``_load_loan_account`` / ``_require_configured_loan``
    loaded its :class:`LoanParams`), so ``None`` here -- the seam's not-a-loan
    signal -- is a caller bug, not a display case.  Raising keeps the callers'
    ``figures`` non-nullable and matches the seam's fail-loud contract, rather
    than letting a ``None`` render an empty payment silently.

    Args:
        account: The configured loan account (ownership already verified by the
            caller -- the seam's trust-the-caller contract).
        balance_ctx: The read pass's :class:`BalanceContext`.

    Returns:
        The loan's :class:`LoanFigures`.

    Raises:
        ValueError: When *account* has no ``LoanParams`` (a caller error).
    """
    figures = balance_at.loan_figures(account, balance_ctx)
    if figures is None:
        raise ValueError(
            f"loan figures unavailable for account {account.id}: it has no "
            f"LoanParams. Load a configured loan before reaching the seam."
        )
    return figures


def _loan_figures_now(account) -> LoanFigures:
    """Return a configured loan's seam figures as of today, resolving it once.

    The figure accessor the loan route's non-balance WRITE surfaces read the
    monthly payment / current rate off -- the escrow total-payment recompute
    (:func:`_compute_total_payment`), the rate-history OOB swap
    (:func:`app.routes.loan.escrow_rates._render_rate_history`), and the
    payment-transfer default (:func:`app.routes.loan.payment_transfer._resolve_transfer_amount`).
    They build a FRESH :class:`BalanceContext` (an as-of-today read pass) so the
    figure reflects any just-committed rate / escrow change; the values are the
    same the loan card shows, produced by the one seam resolution rather than a
    private resolve.

    Args:
        account: The configured loan account (ownership already verified by the
            caller).

    Returns:
        The loan's :class:`LoanFigures` as of today.
    """
    return _require_figures(account, BalanceContext.build(current_user.id))


def _load_route_context(account, params) -> _RouteLoanContext:
    """Build the loan ROUTE's read pass: one BalanceContext + the loaded context.

    The single loader the loan detail READ surfaces (the dashboard GET, the
    calculators, the standalone schedule) resolve a loan through.  It builds ONE
    :class:`BalanceContext` for the pass -- so the balance and the rich figures
    come from the same memoized resolution -- and loads the service
    :class:`LoanContext` (payments / escrow / rate) the route's own schedule
    composer and escrow card need.  It no longer runs the private
    ``resolve_loan_seeded`` the pre-C4 route did: the balance is the seam's fold
    (:attr:`_RouteLoanContext.current_balance`), and the payment / rate / payoff
    are the seam's figures, so the loan tile is no longer the one surface whose
    balance was produced outside the seam.

    Ownership was already verified by ``_load_loan_account -> get_or_404`` before
    this runs, satisfying the seam's trust-the-caller contract.

    Args:
        account: ORM :class:`Account` instance for a configured loan.
        params: ORM :class:`LoanParams` instance (the escrow / payment loader's
            input; also the seam resolution's, loaded once inside the seam).

    Returns:
        The :class:`_RouteLoanContext` for this read pass.
    """
    balance_ctx = BalanceContext.build(current_user.id)
    loan = load_loan_context(account.id, balance_ctx.scenario_id, params)
    return _RouteLoanContext(
        account=account,
        balance_ctx=balance_ctx,
        loan=loan,
        figures=_require_figures(account, balance_ctx),
    )


def _total_payment_from_seam(account, escrow_components) -> Decimal:
    """Return P&I (the seam figure) + *escrow_components* -- the one total-payment sum.

    The single "total monthly payment" assembly: the loan's resolved P&I
    (:func:`_loan_figures_now`, which owns the payment for both ARM -- re-amortized
    from the latest anchor over the remaining term -- and fixed-rate loans) plus
    the supplied escrow set, quantized by
    :func:`~app.services.escrow_calculator.calculate_total_payment`.  Every
    "P&I + escrow" figure funnels through here -- the escrow / rate OOB partials
    (:func:`_compute_total_payment`) and the loan-payment default / auto-track
    switch (:func:`app.routes.loan.payment_transfer._contractual_monthly_payment`)
    -- so the number the loan card shows, the recurring-payment default, and the
    drift / track-payment comparison are ONE computation and cannot silently
    diverge.

    The caller supplies the escrow set (today's active lines for the loan card and
    the payment default; the drawer's set for an escrow OOB swap), so this stays a
    pure sum with no load.

    Args:
        account: The configured loan account (ownership verified by the caller;
            the seam resolves the P&I).
        escrow_components: The resolved escrow lines to add to P&I
            (:class:`~app.services.escrow_calculator.ResolvedEscrowLine`).

    Returns:
        The total monthly payment (P&I + escrow) as a ``Decimal``.
    """
    return escrow_calculator.calculate_total_payment(
        _loan_figures_now(account).monthly_payment, escrow_components,
    )


def _compute_total_payment(account, params, escrow_components):
    """Compute total monthly payment (P&I + escrow) for OOB updates.

    Reads the seam figure's ``monthly_payment`` (via the shared
    :func:`_total_payment_from_seam`) so the escrow / delete-escrow HTMX partials
    display the same P&I as the loan card.  Returns None when params are absent
    (no loan configured yet).

    Args:
        account: ORM :class:`Account` instance for the loan account.
            The seam resolves it (ownership already verified by the caller).
        params: ORM :class:`LoanParams` instance, or None.
        escrow_components: Today's active escrow lines, resolved
            (:class:`~app.services.escrow_calculator.ResolvedEscrowLine`).
    """
    if params is None:
        return None
    return _total_payment_from_seam(account, escrow_components)


def _forward_boundary(account_id, scenario_id):
    """Return the escrow forward-only guard boundary for a loan, or ``None``.

    The latest settled payment's pay-period start
    (:func:`~app.services.loan_loaders.latest_settled_payment_period_start`) -- the
    exact date the genesis split resolves each payment's escrow at, so a new or
    edited escrow version strictly after it cannot move any settled payment's split.
    ``None`` (nothing is frozen) when the user has no baseline scenario or the loan
    has no settled payment.  Shared by the escrow HTMX routes (which apply the guard
    and mark each drawer row editable / deletable) and the loan dashboard GET (which
    builds the same drawer inline), so both derive the boundary one way.

    Args:
        account_id: The loan account whose settled payments bound the guard.
        scenario_id: The baseline scenario id, or ``None``.

    Returns:
        The boundary date, or ``None`` when nothing is settled.
    """
    if scenario_id is None:
        return None
    return latest_settled_payment_period_start(account_id, scenario_id)


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


def build_baseline_scenarios(
    loan_inputs, scenario_id, as_of, extra_principal=Decimal("0.00"),
):
    """Run the baseline payoff-scenario composer call for the loan detail page.

    One ``compute_payoff_scenarios`` call (no what-if lever, ``extra_monthly=0``)
    whose band chart, payment breakdown, and life-of-loan summary all derive
    from the same return value so they cannot diverge (the structural fix
    documented at
    ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``).
    The returned scenario consumes ALL payments (confirmed + projected): its
    ``history_rows + committed_forward`` slice IS the planned trajectory the band
    chart, payment breakdown, and summary read, while ``original_forward``
    supplies the contractual x-axis baseline.  The loan's STANDING
    ``extra_principal`` (step 5) is threaded so ``committed_forward`` -- the
    planned trajectory -- reflects the overpayment, accelerating the band chart
    and the projected payoff exactly as the cash debit does.

    Read switch: reads the genesis-ledger confirmed view ONCE via
    :func:`loan_payment_service.confirmed_loan_view` and threads it into the
    composer as ``confirmed_view``, so the chart / summary derive from the same
    real owed balance AND ledger-derived confirmed history the loan card's seam
    balance (:attr:`_RouteLoanContext.current_balance`) shows -- they cannot
    desync off-schedule.

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
        extra_principal: The loan's standing monthly overpayment (``0.00`` when
            none), threaded into the committed trajectory.

    Returns:
        The baseline :class:`loan_resolver.PayoffScenarios`.
    """
    view = confirmed_loan_view(loan_inputs.loan_params, scenario_id, as_of)
    return loan_resolver.compute_payoff_scenarios(
        loan_inputs=loan_inputs,
        extra_monthly=Decimal("0.00"),
        as_of=as_of,
        confirmed_view=view,
        extra_principal=extra_principal,
    )


def _loan_inputs(params, loan_context: LoanContext) -> loan_resolver.LoanInputs:
    """Bundle a loan's resolver inputs from its params + loaded :class:`LoanContext`.

    The single :class:`loan_resolver.LoanInputs` constructor for the loan ROUTE
    surfaces (the dashboard GET and the band-chart producer), so the
    (params, anchor facts, payments, rate changes) assembly lives in one place.

    Args:
        params: ORM :class:`LoanParams` instance (also the anchor-fact
            synthesis source).
        loan_context: The service-loaded :class:`LoanContext` (``ctx.loan`` for a
            route surface, or a directly-loaded one) carrying the prepared
            payments and rate changes.

    Returns:
        The :class:`loan_resolver.LoanInputs` bundle with ALL payments.
    """
    return loan_resolver.LoanInputs(
        loan_params=params,
        anchor_events=load_loan_anchor_facts(params),
        payments=loan_context.payments,
        rate_changes=loan_context.rate_changes,
    )


def load_baseline_scenarios(account, params):
    """Load a loan's context and compose its baseline payoff scenarios.

    The shared load-and-compose the two SCHEDULE-projection surfaces run -- the
    band-chart producer (:func:`build_loan_band_chart`) and the standalone
    schedule route (:mod:`app.routes.loan.schedule`).  Neither is a balance-at-T
    surface, so this reads no seam: it loads the service :class:`LoanContext`,
    resolves the baseline scenario id, and composes the baseline
    :class:`~app.services.loan_resolver.PayoffScenarios` (no what-if lever)
    threaded with the loan's standing extra -- the committed trajectory the loan
    card carries.  Returns both so the caller can read the ``LoanContext``
    (escrow / rate feeds) alongside the composed scenarios.

    Ownership is verified by the caller (both call sites are ``require_owner`` /
    ``_require_configured_loan``-gated), satisfying the composer's
    trust-the-caller contract.

    Args:
        account: ORM :class:`Account` instance for the loan.
        params: ORM :class:`LoanParams` instance.

    Returns:
        ``(LoanContext, PayoffScenarios)`` for this read.
    """
    scenario = get_baseline_scenario(current_user.id)
    scenario_id = scenario.id if scenario else None
    loan = load_loan_context(account.id, scenario_id, params)
    scenarios = build_baseline_scenarios(
        _loan_inputs(params, loan), scenario_id, date.today(),
        loan_standing_extra(account.id, current_user.id),
    )
    return loan, scenarios


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

    The band is a schedule PROJECTION, not a balance-at-T, so it loads only the
    service :class:`LoanContext` and runs the composer via
    :func:`load_baseline_scenarios` -- it does not build a :class:`BalanceContext`
    or read the seam (the schedule the client splits at the confirmed / projected
    boundary carries its own confirmed history).

    Args:
        account: ORM :class:`Account` instance for the loan.
        params: ORM :class:`LoanParams` instance.

    Returns:
        The serializable band-chart dict (``labels`` / ``balance`` /
        ``current_index``) -- identical in shape to the dashboard's initial
        ``band_chart`` -- for the rate route to hand to ``loan_detail.js``.
    """
    loan, scenarios = load_baseline_scenarios(account, params)
    return build_band_chart(scenarios, len(loan.payments) > 0)


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
