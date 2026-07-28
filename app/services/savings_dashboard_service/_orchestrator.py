"""
Shekel Budget App -- Savings Dashboard: orchestrator.

``compute_dashboard_data`` is the full-page entry point: it loads the
core data, runs the per-account projections, computes goal progress, the
emergency-fund metrics, and the debt summary / DTI, and assembles the
render-template context dict.  Beside it are the NARROW producers, each
running the same loaders and projection dispatch restricted to the
accounts one consumer reads -- ``compute_debt_summary`` and
``compute_debt_principal_progress`` behind the budget dashboard's debt
track (deep-hunt #82, Loop B B-1), ``compute_goal_progress`` behind its
savings tracks, and ``compute_account_balance_cell`` behind the cockpit's
inline-edit revert.  **Every one of them has a live caller**: a narrow
producer nothing calls is a second answer to a question with no question
behind it, which is why ``compute_net_worth_horizon`` was deleted at plan
step X-q2 (finding N-100) rather than kept for a consumer that never
arrived -- ``/savings`` reads the Horizon range out of the ONE
``compute_dashboard_data`` build.  No Flask imports.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from app.services import balance_at, savings_goal_service
from app.services.balance_at import BalanceContext
from app.services.net_worth_account_data import is_liability_account
from app.services.savings_dashboard_service._data import (
    _load_account_params,
    _load_archived_accounts,
    _load_dashboard_core_data,
)
from app.services.savings_dashboard_service._horizon import build_horizon
from app.services.savings_dashboard_service._net_worth import (
    build_account_net_worth_maps,
    build_trend_periods,
    compute_net_worth_series,
    compute_net_worth_today,
    compute_property_equity,
    compute_sparklines,
)
from app.services.savings_dashboard_service._display import (
    _compute_group_subtotals,
    _group_accounts_by_category,
    category_key_by_account_id,
)
from app.services.savings_dashboard_service._goals import (
    _compute_goal_progress,
    _load_active_goals,
)
from app.services.savings_dashboard_service._metrics import (
    DebtSummary,
    _compute_avg_monthly_expenses,
    _compute_debt_summary,
    _compute_principal_paid_fraction,
    _get_current_paycheck_breakdown,
    _sum_liquid_balances,
)
from app.services.savings_dashboard_service._projections import (
    _compute_account_projections,
)
from app.services.savings_dashboard_service._types import (
    AccountProjection,
    _ProjectionContext,
)

if TYPE_CHECKING:
    from app.services.paycheck_calculator import PaycheckBreakdown
    from app.services.savings_dashboard_service._types import (
        _AccountParams,
        _DashboardCoreData,
    )


def _build_projection_context(
    core: _DashboardCoreData, params: _AccountParams,
) -> _ProjectionContext:
    """Assemble the request-scoped projection context from loaded data.

    One definition of the core-data -> context mapping shared by every entry
    point that projects -- the full dashboard build and the three narrow
    producers (:func:`compute_debt_summary` /
    :func:`compute_debt_principal_progress` through
    :func:`_project_debt_accounts`, :func:`compute_goal_progress`, and
    :func:`compute_account_balance_cell`) -- so no two of them can project
    against different inputs.

    Args:
        core: The :class:`_DashboardCoreData` from
            :func:`_load_dashboard_core_data`.
        params: The :class:`_AccountParams` from
            :func:`_load_account_params`.

    Returns:
        The :class:`_ProjectionContext` the projection dispatch reads.
    """
    # The baseline scenario is request-scoped (not an account-type
    # parameter), so it rides on the context, not in params.  The Scenario
    # object itself is carried (not just its id) because the balance_at seam
    # each non-loan tile reads through takes the Scenario; the loan path
    # derives ``scenario.id`` for the resolver.
    return _ProjectionContext(
        all_periods=core.all_periods,
        current_period=core.current_period,
        params=params,
        balance_ctx=core.balance_ctx,
    )


def _debt_summary_with_dti(
    account_data: list[AccountProjection],
    escrow_map: dict[int, list],
    current_breakdown: PaycheckBreakdown | None,
) -> DebtSummary | None:
    """Resolve the engine gross and build the debt summary from it.

    The single home for the debt-card rule, shared by the full
    dashboard build and the narrow :func:`compute_debt_summary`
    producer so the /savings page and the budget dashboard's debt card
    cannot drift onto different figures.

    Its remaining job is the BREAKDOWN -> gross unwrapping (plan step X-s3):
    the DTI block is no longer applied to a finished summary but built with it
    inside :func:`~.._metrics._compute_debt_summary`, which is what makes the
    summary a value constructed in one place rather than a dict mutated across
    two.

    Args:
        account_data: Per-account projections from
            ``_compute_account_projections`` (any mix -- the debt
            summary reads only the entries carrying a ``loan`` detail).
        escrow_map: account_id -> list of EscrowLine with versions (PITI).
        current_breakdown: The engine ``PaycheckBreakdown`` for the
            current period, or ``None`` with no salary configured.

    Returns:
        The :class:`~.._metrics.DebtSummary`, or ``None`` when no loan
        accounts with params exist.
    """
    # MED-06 / F-032: ``gross_biweekly`` is the raise-aware engine output for
    # the current period (``calculate_paycheck`` ->
    # ``PaycheckBreakdown.earnings.gross_biweekly``), NOT the off-engine
    # ``annual_salary / pay_periods`` recompute the DTI block read
    # pre-Commit-26.  ``_metrics._dti_metrics`` performs the
    # biweekly -> monthly normalization on this engine-derived input.
    gross_biweekly = (
        current_breakdown.earnings.gross_biweekly if current_breakdown is not None
        else Decimal("0.00")
    )
    return _compute_debt_summary(account_data, escrow_map, gross_biweekly)


def _project_debt_accounts(
    user_id: int, balance_ctx: BalanceContext | None = None,
) -> tuple[
    _DashboardCoreData, _AccountParams, list[AccountProjection]
] | None:
    """Load + project the user's DEBT accounts for the debt producers.

    The single home for the load-core -> load-params -> filter-to-loans ->
    early-``None`` -> build-context -> project pipeline that both narrow
    debt producers (:func:`compute_debt_summary` and
    :func:`compute_debt_principal_progress`) run verbatim.  Pylint's
    cross-module ``duplicate-code`` cannot see same-module duplication, so
    sharing this here is what keeps the two producers from drifting onto
    different loan sets or projection inputs -- the docstrings' promise
    that the debt summary's current balance and the principal-paid marker
    "can never disagree on which loans count" is enforced by both reading
    one projection of one loan set, not by two copies staying in sync.

    Restricts the projection to the user's LIABILITY accounts -- the loans
    (those with a ``LoanParams`` row) plus every other liability, which since
    plan step X-q3 the debt summary also reports on (``revolving_debt``, the
    debt no payoff date can speak for).  Per-account projections are
    independent, so the restriction cannot change any projected figure versus
    the full build.

    **It was loans ONLY, and that quietly broke the "identical figures"
    promise below**: `debt_without_payoff_model` sums the liabilities that are
    NOT loans, so over a loans-only projection it is always ``$0.00`` while
    the full ``/savings`` build reports the real figure.  Nothing rendered the
    difference -- only the cockpit footer reads that key -- which is exactly
    the kind of silent divergence between two paths to one number this arc
    exists to remove.  Found by plan step X-r's adversarial review.

    Args:
        user_id: Integer ID of the current user.
        balance_ctx: An existing read pass's
            :class:`~app.services.balance_at.BalanceContext` to share, or
            ``None`` to start one.  The budget dashboard's tracks section runs
            two of these producers back to back, so it passes ONE context and
            each loan is resolved once for the pair, not once per producer.

    Returns:
        ``(core, params, account_data)`` -- the loaded core data, the
        account-parameter maps (carrying the ``escrow_map`` the debt
        summary needs), and the per-loan-account projections.
        ``None`` when the user has no loan accounts with params, mirroring
        ``_compute_debt_summary``'s no-loan ``None`` inside the full build.
    """
    core = _load_dashboard_core_data(user_id, balance_ctx)
    params = _load_account_params(core.accounts)
    if not any(acct.id in params.loan_params_map for acct in core.accounts):
        # No loans: both debt producers answer ``None`` (a user with only a
        # card has no payoff caption to qualify and no principal to progress),
        # so nothing is projected at all.
        return None
    debt_accounts = [
        acct for acct in core.accounts
        if acct.id in params.loan_params_map or is_liability_account(acct)
    ]

    ctx = _build_projection_context(core, params)
    account_data = _compute_account_projections(debt_accounts, ctx)
    return core, params, account_data


def compute_debt_summary(
    user_id: int, balance_ctx: BalanceContext | None = None,
) -> DebtSummary | None:
    """Compute only the debt summary + DTI for the budget dashboard card.

    The narrow producer behind the dashboard's debt track
    (``dashboard_pulse_service.compute_tracks_section``; deep-hunt #82's
    efficiency/SRP half).  Identical figures to
    ``compute_dashboard_data(user_id)["debt_summary"]`` by construction:
    it runs the same loaders and the same per-account projection
    dispatch -- restricted to the accounts the debt summary reads (its loans, plus
    the other liabilities its ``revolving_debt`` figure names; per-account
    projections are independent, so the restriction cannot change any
    projected figure), via the shared
    :func:`_project_debt_accounts` -- and routes through the shared
    :func:`_debt_summary_with_dti`.  What it skips is the dashboard-only
    work: every non-loan account's projection, goal progress, the
    emergency-fund metrics, account grouping, and the archived-account
    list.

    Args:
        user_id: Integer ID of the current user.
        balance_ctx: An optional shared read-pass context (see
            :func:`_project_debt_accounts`).

    Returns:
        The :class:`~.._metrics.DebtSummary`, or ``None``
        when the user has no loan accounts with params (the early
        return mirrors ``_compute_debt_summary``'s no-loan ``None``
        inside the full build, and additionally skips the per-account
        projections and the breakdown's paycheck-engine call -- the
        debt summary needs neither).
    """
    projected = _project_debt_accounts(user_id, balance_ctx)
    if projected is None:
        return None
    core, params, account_data = projected

    current_breakdown = _get_current_paycheck_breakdown(
        user_id, core.all_periods, core.current_period,
    )
    return _debt_summary_with_dti(
        account_data, params.escrow_map, current_breakdown,
    )


def compute_debt_principal_progress(
    user_id: int, balance_ctx: BalanceContext | None = None,
) -> Decimal | None:
    """Compute the aggregate fraction of original loan principal paid off.

    The narrow producer behind the budget dashboard's debt track marker
    (Loop B B-1): it runs the same loaders and per-account projection
    dispatch :func:`compute_debt_summary` uses -- the shared
    :func:`_project_debt_accounts` pipeline restricted to the loan
    accounts -- and routes through the shared
    :func:`_compute_principal_paid_fraction`.

    Unlike the debt summary's active-loans-only ``total_debt``, the
    fraction sums over ALL loans ever originated that the pipeline
    surfaces -- reachably, every non-archived loan account with a
    ``LoanParams`` row, INCLUDING paid-off ones (locked 2026-06-12 in
    ``docs/design/dashboard_card_audit.md``, Rebuild decisions item 4).  A
    paid-off loan keeps its original principal in both the numerator and
    the denominator, so the marker is monotonic: it can only rise, reaches
    exactly ``1`` when every loan is paid off, and never jumps backward as
    a single loan retires.  The two surfaces deliberately scope different
    loan sets -- the displayed balance is active-only, the progress marker
    is all-loans-ever.

    ``original_principal`` is a NOT NULL, ``> 0`` column on
    :class:`~app.models.loan_params.LoanParams`, so a real loan always
    supplies the denominator; the fraction is honest principal progress,
    never a time-elapsed proxy.

    Args:
        user_id: Integer ID of the current user.
        balance_ctx: An optional shared read-pass context (see
            :func:`_project_debt_accounts`).

    Returns:
        The principal-paid fraction as a ``Decimal`` in ``[0, 1]``, or
        ``None`` ONLY when the user has no loan accounts at all (the
        :func:`_project_debt_accounts` early return).  A fully paid-off
        loan set returns ``Decimal("1")``, not ``None``.  The UI renders
        no marker for ``None``.
    """
    projected = _project_debt_accounts(user_id, balance_ctx)
    if projected is None:
        return None
    _core, _params, account_data = projected
    return _compute_principal_paid_fraction(account_data)


def compute_goal_progress(
    user_id: int, balance_ctx: BalanceContext | None = None,
) -> list[dict]:
    """Compute only the savings-goal progress for the budget dashboard card.

    The narrow producer behind the dashboard's savings tracks
    (``dashboard_pulse_service.compute_tracks_section``), mirroring
    :func:`compute_debt_summary`'s pattern.  Identical figures
    to ``compute_dashboard_data(user_id)["goal_data"]`` by construction:
    it runs the same loaders, the same per-account projection dispatch
    (restricted to the accounts that back an active goal -- per-account
    projections are independent, so the restriction cannot change any
    projected figure), and the same canonical net-biweekly-pay producer,
    then routes through the shared :func:`_compute_goal_progress`.  What
    it skips is the dashboard-only work: every non-goal account's
    projection, the emergency-fund metrics, the debt summary, account
    grouping, and the archived-account list.

    Closes the budget dashboard's two goal defects (dashboard_card_audit
    Card 5): income-relative goals (``target_amount`` NULL by design) now
    resolve their target via ``resolve_goal_target`` instead of rendering
    ``$0.00 / 0%``, and the balance basis is the entries-aware resolver
    balance (each projection's ``current_balance``) rather than the
    raw stored ``current_anchor_balance``.  So this card and the /savings
    page report the same numbers for the same goal.

    Args:
        user_id: Integer ID of the current user.
        balance_ctx: An optional shared read-pass context (see
            :func:`_project_debt_accounts`).

    Returns:
        A list of per-goal progress dicts (see
        :func:`_compute_goal_progress`), one per active goal; empty when
        the user has no active goals.
    """
    core = _load_dashboard_core_data(user_id, balance_ctx)

    active_goals = _load_active_goals(user_id)
    if not active_goals:
        return []

    params = _load_account_params(core.accounts)
    goal_account_ids = {goal.account_id for goal in active_goals}
    goal_accounts = [
        acct for acct in core.accounts if acct.id in goal_account_ids
    ]

    ctx = _build_projection_context(core, params)
    account_data = _compute_account_projections(goal_accounts, ctx)

    current_breakdown = _get_current_paycheck_breakdown(
        user_id, core.all_periods, core.current_period,
    )
    net_biweekly_pay = (
        current_breakdown.earnings.net_pay if current_breakdown is not None
        else Decimal("0.00")
    )

    return _compute_goal_progress(
        user_id, account_data, core.all_periods, net_biweekly_pay,
        active_goals,
    )


def compute_account_balance_cell(
    user_id: int, account_id: int,
) -> AccountProjection | None:
    """Compute one active account's cockpit balance cell.

    The narrow producer behind ``savings.cockpit_balance`` -- the GET
    endpoint the cockpit's per-card inline anchor editor reverts to on
    Cancel / Escape (``accounts._anchor_revert_url`` maps ``revert=accounts``
    here, mirroring how ``revert=dashboard`` maps to
    ``dashboard.balance_section``).  It re-renders
    ``savings/_cockpit_balance.html`` for a single account.

    SSOT with the grid: it runs the SAME load -> param-load -> project
    pipeline ``compute_dashboard_data`` runs, through the shared
    :func:`_compute_account_projections`, restricted to the one account (the
    param load is scoped to ``[acct]``; per-account projections are
    independent, so the restriction cannot change the projected figure).
    A Cancel therefore restores the exact number the card grid showed,
    never a divergent recompute.

    **It returns the projection ITSELF** (plan step X-t1, finding N-111).  It
    used to copy three of its fields into a dict of its own, so the partial's
    contract was stated in three places -- here, the grid's ``{% with %}``
    include, and the partial's header comment -- and the SSOT promise above
    held because those copies agreed rather than because both paths render one
    value.  The partial now reads the same object the grid loop hands it, so
    the revert and the cell it replaces cannot diverge by construction.

    Args:
        user_id: Integer ID of the current user (the owner; the caller has
            already verified ownership of *account_id* via the route's
            ``get_or_404``).
        account_id: Integer ID of the account whose balance cell to render.

    Returns:
        The account's :class:`~.._types.AccountProjection`, or ``None`` when
        *account_id* is not among the user's active accounts (e.g. it was
        archived between page load and the revert), which the caller turns
        into a 404.
    """
    core = _load_dashboard_core_data(user_id)
    acct = next(
        (a for a in core.accounts if a.id == account_id), None,
    )
    if acct is None:
        return None

    params = _load_account_params([acct])
    ctx = _build_projection_context(core, params)
    # Route through the shared projection (which batch-builds the seam maps)
    # restricted to the one account, so the Cancel revert restores the exact
    # number the card grid showed.
    return _compute_account_projections([acct], ctx)[0]


def _build_trend_window(
    core: _DashboardCoreData, params: _AccountParams,
) -> tuple[list, int, int]:
    """Build the net-worth trend window and its honest-history gate.

    Generates the loan amortization schedules the honest-history gate reads
    -- each loan's first-payment date, the data the balance maps do NOT carry
    -- then delegates to :func:`build_trend_periods`.  The schedules feed
    ONLY the gate; the dense-map build assembles its own inside the
    :mod:`app.services.balance_at` seam.

    **It carries no no-baseline guard of its own** (plan step X-t2, finding
    N-107): its one caller owns that rule for the whole region, so this is
    reached with a baseline and calls the seam unconditionally.  The guard it
    used to hold silently answered the question a different way from the map
    builder beside it.

    Args:
        core: The loaded core data (accounts, scenario, periods).
        params: The batch-loaded params (its loan-params map selects the
            loan accounts).

    Returns:
        ``(trend_periods, current_index, honest_start)`` from
        :func:`build_trend_periods`.
    """
    loan_accounts = [
        acct for acct in core.accounts if acct.id in params.loan_params_map
    ]
    return build_trend_periods(
        core.accounts, core.all_periods, core.current_period,
        balance_at.debt_schedule_rows(loan_accounts, core.balance_ctx),
    )


def _compute_card_sparklines(
    core: _DashboardCoreData, account_maps: list[dict],
) -> dict:
    """Build the per-account forward card sparklines (slice 3c).

    Reuses the dense per-account balance maps already built for the net-worth
    trend, so the sparklines and the net-worth math read ONE projection.  The
    forward window is the current-period-onward run the trend projects.

    Args:
        core: The loaded core data (its periods define the forward window).
        account_maps: The dense maps from
            :func:`build_account_net_worth_maps`.

    Returns:
        ``{account_id: [Decimal, ...]}`` for each informative account.
    """
    forward_periods = [
        p for p in core.all_periods
        if core.current_period is not None
        and p.period_index >= core.current_period.period_index
    ]
    return compute_sparklines(account_maps, forward_periods)


def _compute_net_worth_section(
    core: _DashboardCoreData,
    params: _AccountParams,
    account_data: list[AccountProjection],
    user_id: int,
) -> dict:
    """Assemble the cockpit's net-worth region, sparklines, and Horizon range.

    One producer over a single build of the dense per-account balance maps:
    the today figures (from the already-projected ``account_data``), the
    ``2 years`` net-worth trend series with its per-category composition
    split, the per-account forward sparklines, AND the ``Horizon`` range
    (P-AC1 Loop B P1) all derive from that ONE dashboard load -- so the
    /savings request pays for one load, not two (no redundant standalone
    horizon-producer call), and every figure reads one projection.

    The maps are built once over ALL periods (so the entries-aware resolver
    always has its anchor seed) via :func:`build_account_net_worth_maps`,
    which routes through the :mod:`app.services.balance_at` seam.  The
    per-category composition split reads each account's band off the SAME
    id-based classifier the grid grouping uses, so a trend band and its grid
    group cannot disagree.  The Horizon range reuses the /retirement engine,
    so it re-projects the retirement / investment accounts -- the accepted
    cost of the single-engine invariant, the same the dense-map rebuild pays.

    Degrades gracefully with no current period: the today figures still come
    from ``account_data``, the series is empty (``current_index`` 0), the
    sparklines are empty; the horizon is still built (its axis is date-driven)
    unless there are no pay periods at all
    (:func:`~app.services.savings_dashboard_service._horizon.build_horizon`
    returns ``None`` then).

    **This is the ONE no-baseline door for the whole region** (plan step X-t2,
    finding N-107).  Every seam read below it -- the dense maps, the trend
    window's loan schedules, the sparklines and the Horizon's bands -- is
    reachable only with a baseline, so the rule is stated HERE and the three
    producers below simply call the seam.  It was stated in each of them
    instead, and two of those copies degraded DIFFERENTLY: the map builder
    returned an empty list (a $0 trend drawn over a real window) while the trend
    window still built its axis.  A user with no baseline has no balance the app
    can answer, so the honest region is the today figures over an empty series
    and no Horizon at all -- which is exactly the state the no-pay-periods path
    already renders, so the template and the client need no new branch.

    The empty series is BUILT by :func:`compute_net_worth_series` over an empty
    window rather than written out as a literal here, so the degraded shape
    cannot drift from the real one.

    Args:
        core: The loaded :class:`_DashboardCoreData`.
        params: The batch-loaded :class:`_AccountParams`.
        account_data: The per-account projections already computed for the
            page (the today figures + the asset / liability horizon bands).
        user_id: The current user's id (for the Horizon range's /retirement
            engine reuse).

    Returns:
        ``(net_worth, sparklines)``.  ``net_worth`` carries ``net_worth``,
        ``total_assets``, ``total_liabilities``, ``liquid``, ``series`` (the
        ``2 years`` trend + its ``composition`` split, carrying
        ``current_index``), and ``horizon`` (the annual composition +
        trajectory + milestones, or ``None`` with no periods).  ``sparklines``
        is ``{account_id: [Decimal, ...]}``.
    """
    today = compute_net_worth_today(account_data)
    category = category_key_by_account_id(account_data)

    if not core.balance_ctx.has_baseline:
        empty_series = compute_net_worth_series([], [], category)
        empty_series["current_index"] = 0
        return {**today, "series": empty_series, "horizon": None}, {}

    account_maps = build_account_net_worth_maps(
        core.accounts, core.balance_ctx, core.all_periods,
    )

    trend_periods, current_index, _ = _build_trend_window(core, params)
    series = compute_net_worth_series(account_maps, trend_periods, category)
    # The solid-history / dashed-projection boundary (and the "Today"
    # marker): the index of the current period within the trend window.
    series["current_index"] = current_index

    sparklines = _compute_card_sparklines(core, account_maps)
    horizon = build_horizon(user_id, core, account_data, category)

    return {
        **today,
        "series": series,
        "horizon": horizon,
    }, sparklines


def _compute_cockpit_grid_section(
    core: _DashboardCoreData,
    account_data: list[AccountProjection],
) -> dict:
    """Assemble the cockpit's account-grid context (Loop B Phase 2).

    Groups the projected accounts by category ONCE and reuses that single
    structure for the grid itself and its per-category balance subtotals (so
    the grouping is never recomputed), and resolves each Property's equity
    through the shared
    :func:`app.services.savings_dashboard_service._net_worth.compute_property_equity`
    producer.  All money math lives here, never in the template.

    Args:
        core: The loaded :class:`_DashboardCoreData` (its ``accounts`` feed
            the equity resolver; its ``scenario`` supplies the loan
            resolver's scenario id, or ``None`` with no baseline scenario).
        account_data: The per-account projections already computed for the
            page (the grouping and subtotal source).

    Returns:
        dict with ``grouped_accounts`` (category label -> projections),
        ``group_subtotals`` (category label -> ``Decimal`` balance
        subtotal), and ``property_equity`` (list of ``{account, equity}`` for
        each Property account).
    """
    grouped_accounts = _group_accounts_by_category(account_data)
    group_subtotals = _compute_group_subtotals(grouped_accounts)
    return {
        "grouped_accounts": grouped_accounts,
        "group_subtotals": group_subtotals,
        "property_equity": compute_property_equity(
            core.accounts, core.balance_ctx,
        ),
    }


def compute_dashboard_data(user_id):
    """Compute all data needed by the savings dashboard template.

    Loads accounts, projects balances per account type, computes
    savings goal progress and emergency fund metrics, and groups
    accounts by category for display.

    Args:
        user_id: Integer ID of the current user.

    Returns:
        dict with keys matching the render_template context:
            account_data, grouped_accounts, goal_data,
            emergency_metrics, total_savings,
            avg_monthly_expenses, savings_accounts.
    """
    core = _load_dashboard_core_data(user_id)

    # ── Load account-type-specific parameters ───────────────────
    params = _load_account_params(core.accounts)

    # ── Compute per-account projections ─────────────────────────
    ctx = _build_projection_context(core, params)
    account_data = _compute_account_projections(core.accounts, ctx)

    # ── Canonical paycheck breakdown (MED-06 / F-032) ──────────
    # One income producer feeds every income-derived figure on the
    # page: the income-relative-goal trajectory's net biweekly pay AND
    # the DTI denominator's gross monthly income.  Both route through
    # ``calculate_paycheck`` for the current period so the engine is the
    # single source of truth.  Pre-Commit-26 the DTI path used an
    # off-engine raw ``annual_salary / pay_periods`` recompute that
    # silently dropped any applicable ``SalaryRaise`` rows, so a user with
    # a 3% recurring raise saw a DTI denominator ~$260/mo too low (audit
    # worked example: $8,666.67 vs $8,926.67, 27.7% vs 26.9%).
    current_breakdown = _get_current_paycheck_breakdown(
        user_id, core.all_periods, core.current_period,
    )
    net_biweekly_pay = (
        current_breakdown.earnings.net_pay if current_breakdown is not None
        else Decimal("0.00")
    )

    # ── Savings goals ───────────────────────────────────────────
    goal_data = _compute_goal_progress(
        user_id, account_data, core.all_periods, net_biweekly_pay,
        _load_active_goals(user_id),
    )

    # ── Emergency fund metrics ──────────────────────────────────
    avg_monthly_expenses = _compute_avg_monthly_expenses(
        user_id, core.accounts, core.all_periods, core.current_period,
        core.balance_ctx.scenario,
    )
    total_savings = _sum_liquid_balances(account_data)
    emergency_metrics = savings_goal_service.calculate_savings_metrics(
        total_savings, avg_monthly_expenses,
    )

    # ── Template helpers ────────────────────────────────────────
    # Liquid accounts appear in the savings goal form dropdown.
    savings_accounts = [
        ad.account for ad in account_data
        if ad.account.account_type is not None
        and ad.account.account_type.is_liquid
    ]

    # ── Debt summary and DTI ───────────────────────────────────
    debt_summary = _debt_summary_with_dti(
        account_data, params.escrow_map, current_breakdown,
    )

    # ── Net-worth cockpit region + per-account sparklines ──────
    # One producer over the build-once dense maps: the net-worth region
    # (the 2-year trend + composition split), the per-account card sparklines
    # (slice 3c), AND the Horizon range (P-AC1 Loop B P1) -- all from this one
    # dashboard load, so the /savings request never re-loads for the horizon.
    net_worth, sparklines = _compute_net_worth_section(
        core, params, account_data, user_id,
    )

    return {
        "account_data": account_data,
        # Grid grouping, per-group subtotals, and Property equity (Loop B
        # Phase 2): one helper so the grouping happens once and the money
        # math stays out of the template.
        **_compute_cockpit_grid_section(core, account_data),
        "goal_data": goal_data,
        "emergency_metrics": emergency_metrics,
        "total_savings": total_savings,
        "avg_monthly_expenses": avg_monthly_expenses,
        "savings_accounts": savings_accounts,
        "archived_accounts": _load_archived_accounts(user_id),
        "debt_summary": debt_summary,
        "net_worth": net_worth,
        "sparklines": sparklines,
    }
