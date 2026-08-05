"""
Shekel Budget App -- Savings Dashboard: orchestrator.

``compute_dashboard_data`` is the full-page entry point: it loads the
core data, runs the per-account projections, computes goal progress, the
emergency-fund metrics, and the debt summary / DTI, and assembles the
render-template context dict.  Beside it are the NARROW producers, each
running the same loaders and projection dispatch restricted to the
accounts one consumer reads -- ``compute_debt_summary`` behind the budget
dashboard's debt track (deep-hunt #82, Loop B B-1),
``compute_goal_progress`` behind its
savings tracks, and ``compute_account_balance_cell`` behind the cockpit's
inline-edit revert.  **Every one of them has a live caller**: a narrow
producer nothing calls is a second answer to a question with no question
behind it, which is why ``compute_net_worth_horizon`` was deleted at plan
step X-q2 (finding N-100) rather than kept for a consumer that never
arrived -- ``/savings`` reads the Horizon range out of the ONE
``compute_dashboard_data`` build.

**And each narrow producer answers ONE question for ONE consumer**, which is
what plan step X-u restored (ruling R-BS, finding N-109): there were two debt
producers, both for the same debt track, so the dashboard ran this module's
load -> params -> project pipeline twice per render and two docstrings promised
they would keep agreeing on which loans count.  The second one's figure is a
field of the first one's value object now.  No Flask imports.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from app.services import balance_at, savings_goal_service
from app.services.balance_at import BalanceContext
from app.services.account_category import is_liability_account
from app.services.savings_dashboard_service._data import (
    _load_account_params,
    _load_archived_accounts,
    _load_dashboard_core_data,
)
from app.services.savings_dashboard_service._horizon import build_horizon
from app.services.savings_dashboard_service._net_worth import (
    NetWorthRegion,
    build_trend_periods,
    compute_net_worth_series,
    compute_net_worth_today,
    compute_property_equity,
    compute_sparklines,
)
from app.services.savings_dashboard_service._display import (
    _compute_group_subtotals,
    _group_accounts_by_category,
)
from app.services.savings_dashboard_service._goals import (
    GoalProgress,
    _compute_goal_progress,
    _load_active_goals,
)
from app.services.savings_dashboard_service._metrics import (
    DebtSummary,
    _compute_avg_monthly_expenses,
    _compute_debt_summary,
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
    producers (:func:`compute_debt_summary`, :func:`compute_goal_progress`, and
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
    projected figure) -- and routes through the shared
    :func:`_debt_summary_with_dti`.  What it skips is the dashboard-only
    work: every non-loan account's projection, goal progress, the
    emergency-fund metrics, account grouping, and the archived-account
    list.

    **It is the ONLY debt producer, as of plan step X-u** (ruling R-BS, finding
    N-109).  There were two, and the tracks section called both, so ONE
    dashboard render ran this load -> params -> project pipeline TWICE over the
    same loans -- measured at 2 projections and 3 seam batches per render on the
    developer's own data.  The second one existed to carry the principal-paid
    fraction, which is now a field of the summary this one already builds.  The
    pipeline they shared lived in a ``_project_debt_accounts`` helper whose whole
    rationale was that two producers must not drift onto different loan sets;
    with one producer that rationale is gone, so it is inlined here and the
    three narrow producers left in this module read alike again -- the helper
    was the asymmetry, not the DRY.

    **The restriction to LIABILITY accounts is not an optimization detail.**
    It was loans ONLY until plan step X-r's adversarial review:
    ``debt_without_payoff_model`` sums the liabilities that are NOT loans, so
    over a loans-only projection it always read ``$0.00`` here while the full
    ``/savings`` build reported the real figure -- the silent divergence between
    two paths to one number this arc exists to remove.

    Args:
        user_id: Integer ID of the current user.
        balance_ctx: An existing read pass's
            :class:`~app.services.balance_at.BalanceContext` to share, or
            ``None`` to start one.  The budget dashboard's tracks section runs
            this beside :func:`compute_goal_progress`, so it passes ONE context
            and each loan is resolved once for the pair.

    Returns:
        The :class:`~.._metrics.DebtSummary`, or ``None``
        when the user has no loan accounts with params (the early
        return mirrors ``_compute_debt_summary``'s no-loan ``None``
        inside the full build, and additionally skips the per-account
        projections and the breakdown's paycheck-engine call -- the
        debt summary needs neither).
    """
    core = _load_dashboard_core_data(user_id, balance_ctx)
    params = _load_account_params(core.accounts)
    if not any(acct.id in params.loan_params_map for acct in core.accounts):
        # No loans: the summary is ``None`` (a user whose only liability is a
        # card has no payoff caption to qualify and no principal to progress),
        # so nothing is projected at all.  This is a CHEAP SUFFICIENT condition
        # for ``_compute_debt_summary``'s own no-loan ``None`` -- an account
        # with a ``LoanParams`` row that the seam does not resolve as a loan
        # gets past here and is answered ``None`` there -- not a second
        # statement of it.
        return None
    debt_accounts = [
        acct for acct in core.accounts
        if acct.id in params.loan_params_map or is_liability_account(acct)
    ]

    ctx = _build_projection_context(core, params)
    account_data = _compute_account_projections(debt_accounts, ctx)

    current_breakdown = _get_current_paycheck_breakdown(
        user_id, core.all_periods, core.current_period,
    )
    return _debt_summary_with_dti(
        account_data, params.escrow_map, current_breakdown,
    )


def compute_goal_progress(
    user_id: int, balance_ctx: BalanceContext | None = None,
) -> list[GoalProgress]:
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
    raw asserted balance.  So this card and the /savings
    page report the same numbers for the same goal.

    Args:
        user_id: Integer ID of the current user.
        balance_ctx: An optional shared read-pass context (see
            :func:`compute_debt_summary`).

    Returns:
        One :class:`~.._goals.GoalProgress` per active goal (see
        :func:`_compute_goal_progress`); empty when the user has no active
        goals.
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
    core: _DashboardCoreData, account_data: list[AccountProjection],
) -> dict:
    """Build the per-account forward card sparklines (slice 3c).

    Reads the dense per-account balance maps the projections already carry, so
    the sparklines, the tiles and the net-worth math read ONE projection.  The
    forward window is the current-period-onward run the trend projects.

    Args:
        core: The loaded core data (its periods define the forward window).
        account_data: The per-account projections, each carrying its dense
            :attr:`~.._types.AccountProjection.balances` map (plan step X-w).

    Returns:
        ``{account_id: [Decimal, ...]}`` for each informative account.
    """
    forward_periods = [
        p for p in core.all_periods
        if core.current_period is not None
        and p.period_index >= core.current_period.period_index
    ]
    return compute_sparklines(account_data, forward_periods)


def _compute_net_worth_section(
    core: _DashboardCoreData,
    params: _AccountParams,
    account_data: list[AccountProjection],
    user_id: int,
) -> tuple[NetWorthRegion, dict[int, list[Decimal]]]:
    """Assemble the cockpit's net-worth region, sparklines, and Horizon range.

    One producer over ONE per-account projection: the today figures, the
    ``2 years`` net-worth trend series with its per-category composition
    split, the per-account forward sparklines, AND the ``Horizon`` range
    (P-AC1 Loop B P1) all reduce over the same ``account_data`` this page
    already built -- so the /savings request pays for one load, not two (no
    redundant standalone horizon-producer call), and every figure reads one
    projection.

    **It built a SECOND per-account container to do that, and plan step X-w
    deleted it** (ruling R-CG, finding N-114).  ``build_account_net_worth_maps``
    re-asked the seam for every account's dense period map and paired each with
    a stored liability flag; the projections beside it already carried the same
    balances and derived the same flag.  The dense map is
    :attr:`~.._types.AccountProjection.balances` now, so the trend, the
    sparklines and the hero cannot be given different accounts, different
    balances or different classifications.

    The maps are still built over ALL periods (so every consumer reads whichever
    ones it wants back out by id); the per-category composition split reads each
    account's band off the category the projection itself carries, the same
    field the grid grouping buckets by (plan step X-z7, ruling R-CT), so a
    trend band and its grid group cannot disagree.  The Horizon range reuses
    the /retirement engine, so it re-projects the retirement / investment
    accounts -- the accepted cost of the single-engine invariant.

    Degrades gracefully with no current period: the today figures still come
    from ``account_data``, the series is empty (``current_index`` 0), the
    sparklines are empty; the horizon is still built (its axis is date-driven)
    unless there are no pay periods at all
    (:func:`~app.services.savings_dashboard_service._horizon.build_horizon`
    returns ``None`` then).

    **No producer here decides the no-baseline state** (plan step X-v2, ruling
    R-BW).  This function was the ONE door for the whole region at plan step
    X-t2 (finding N-107), because two producers under it degraded DIFFERENTLY --
    the map builder returned an empty list, a ``$0`` trend drawn over a real
    window, while the trend window built its axis anyway.  X-v2 then deleted the
    predicate itself: the seam raises
    :class:`~app.exceptions.BaselineMissingError` and one application-level
    handler answers it.  Since plan step X-w the region's dense maps are not
    even read here -- the projection opened that door before this function ran.

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
        ``(region, sparklines)`` -- the :class:`~.._net_worth.NetWorthRegion` and
        ``{account_id: [Decimal, ...]}``.
    """
    today = compute_net_worth_today(account_data)

    # The no-baseline early return that stood here went at plan step X-v2
    # (rulings R-BZ and R-CA).  It returned the today figures over an empty
    # series -- and those "today figures" were ``current_balance or ZERO``
    # reduced over balances that were ALL ``None``, so the region reported a
    # net worth, a total-assets and a total-liabilities figure for a user whose
    # every balance the app cannot answer.  The seam raises now and one
    # application-level handler renders the repair, so there is no hero left to
    # fabricate.  X-t2's ruling that this region should degrade here is
    # REVERSED, on the developer's confirmation (CLAUDE.md rule 5).
    # The window and its current index come from ONE producer and are handed to
    # the series builder together, so the solid-history / dashed-projection
    # boundary is a field of a series built once rather than a key mutated onto
    # a dict after it was returned (plan step X-w3, ruling R-CI).
    trend_periods, current_index, _ = _build_trend_window(core, params)
    series = compute_net_worth_series(
        account_data, trend_periods, current_index,
    )

    sparklines = _compute_card_sparklines(core, account_data)
    horizon = build_horizon(user_id, core, account_data)

    return NetWorthRegion(
        today=today, series=series, horizon=horizon,
    ), sparklines


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

    **The grouping no longer CLASSIFIES** (plan steps X-z2 and X-z7).  It
    bucketed each account by calling the classifier once per category label --
    5N calls, 40 for 8 accounts, on top of the N-call map the net-worth region
    built, for 48 on the render -- then briefly read a map this function was
    handed, and now reads the category each projection already carries.

    Args:
        core: The loaded :class:`_DashboardCoreData` (its ``accounts`` feed
            the equity resolver; its ``scenario`` supplies the loan
            resolver's scenario id, or ``None`` with no baseline scenario).
        account_data: The per-account projections already computed for the
            page (the grouping and subtotal source).

    Returns:
        dict with ``grouped_accounts`` (category label -> projections),
        ``group_subtotals`` (category label -> ``Decimal`` balance
        subtotal), and ``property_equity`` (a
        :class:`~.._net_worth.PropertyEquity` per Property account).
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


def _compute_emergency_fund_section(
    user_id: int,
    core: _DashboardCoreData,
    account_data: list[AccountProjection],
) -> dict:
    """Assemble the cockpit's emergency-fund figures and their basis.

    A section helper beside :func:`_compute_net_worth_section` and
    :func:`_compute_cockpit_grid_section`, so the three cohesive blocks of
    :func:`compute_dashboard_data` read alike and it assembles sections rather
    than computing some of them inline (plan step X-z2; the three locals it
    held were what pushed that function past the locals ceiling).

    All three keys are published because the footer renders all three: the
    coverage itself, and the two figures its caption names as the basis
    ("Based on $X savings and $Y/mo avg expenses").  The money math is here,
    never in the template.

    **A known duplication is named rather than hidden**: ``total_savings`` is
    :func:`~.._metrics._sum_liquid_balances` over the same ``account_data``
    :attr:`~.._net_worth.NetWorthToday.liquid` reduces on the same render, so
    one fact is computed twice and published under two keys.  Out of plan step
    X-z's scope (``CLAUDE.md`` rule 6) and recorded as finding N-121 with an
    owner, because collapsing it changes what the page publishes and wants its
    own commit.

    Args:
        user_id: Integer ID of the current user.
        core: The loaded :class:`_DashboardCoreData` (its accounts, periods and
            scenario scope the expense baseline).
        account_data: The per-account projections (the liquid balances).

    Returns:
        dict with ``emergency_metrics``
        (:class:`~app.services.savings_goal_service.SavingsCoverage`),
        ``total_savings`` and ``avg_monthly_expenses``.
    """
    avg_monthly_expenses = _compute_avg_monthly_expenses(
        user_id, core.accounts, core.all_periods, core.current_period,
        core.balance_ctx.scenario_id,
    )
    total_savings = _sum_liquid_balances(account_data)
    return {
        "emergency_metrics": savings_goal_service.calculate_savings_metrics(
            total_savings, avg_monthly_expenses,
        ),
        "total_savings": total_savings,
        "avg_monthly_expenses": avg_monthly_expenses,
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
        # The coverage figures and the two figures its caption names as their
        # basis, from one helper (plan step X-z2).
        **_compute_emergency_fund_section(user_id, core, account_data),
        "savings_accounts": savings_accounts,
        "archived_accounts": _load_archived_accounts(user_id),
        "debt_summary": debt_summary,
        "net_worth": net_worth,
        "sparklines": sparklines,
    }
