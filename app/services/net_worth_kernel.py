"""
Shekel Budget App -- Net-Worth Kernel (shared per-account balance chain).

The single, Flask-free home for the per-account balance-map projection
chain that BOTH the year-end summary's net-worth section and the savings
cockpit's net-worth producer build on.  Promoted out of
``year_end_summary_service._balances`` (Loop B Phase 1) so the two
surfaces compute net worth from one set of math instead of two copies
that could drift: the same dispatch (amortizing loan schedule / interest
calculator / investment growth engine / canonical entries-aware
resolver), the same investment forward/reverse growth sub-chain, and the
same asset-plus / liability-minus net-worth sum.

The cockpit's forward net-worth trend PROJECTS investment and retirement
growth forward, so the investment growth sub-chain lives here too (the
SCOPE B move locked 2026-06-24), not just the plain balance dispatch.

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"):
this module imports no Flask symbol and performs no database writes.  It
reads through the same ORM session the callers already opened.  All money
is :class:`~decimal.Decimal`; ``float`` belongs only at a route's Chart.js
serialization boundary, never here.

The public producers take loose, per-account parameters (the single
account's debt schedule, its :class:`~app.models.investment_params.InvestmentParams`,
its adapted deductions, and the engine gross-biweekly) rather than the
year-end package's ``_ProjectionInputs`` bundle, so neither consumer has
to construct that year-end-specific value object to call the kernel.  The
year-end adapter unpacks its bundle per account at the call site.
"""

from collections import OrderedDict
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.loan_params import LoanParams
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.services import (
    balance_calculator,
    balance_resolver,
    growth_engine,
)
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
    compute_loan_period_balance_map,
)
from app.services.investment_projection import adapt_deductions
from app.services.loan_payment_service import (
    query_shadow_income,
    resolve_account_loan,
)
from app.services.projection_inputs import build_investment_projection_inputs
from app.utils.balance_predicates import account_period_scope_clause

ZERO = Decimal("0")


def load_account_period_transactions(
    account_id: int,
    scenario_id: int,
    period_ids: list[int],
) -> list[Transaction]:
    """Return one account's non-deleted scenario transactions over periods.

    The single home for the account/scenario/period transaction query
    shared by the interest-bearing balance path
    (:func:`base_account_balance_map`) and the year-end interest helpers
    (``_compute_interest_for_year`` and ``_settled_net_by_period`` in
    :mod:`._balances`).  All three select EVERY non-deleted row for the
    account in the period span -- unlike
    :func:`~app.services.balance_resolver._load_balance_transactions`,
    which additionally drops Credit / Cancelled rows -- because their
    downstream consumers (interest accrual and the settled-net walk)
    apply their own status logic and need the full row set.
    ``Transaction.status`` is ``lazy="joined"`` on the model, so the
    settled-net walk reads ``txn.status.is_settled`` off these rows
    without an N+1 and without an explicit eager-load.

    Args:
        account_id: The account whose transactions to load.
        scenario_id: The scenario the balance is projected under.
        period_ids: Pay period ids the projection covers.  An empty list
            yields an empty result without issuing an ``IN ()`` query.

    Returns:
        ``list[Transaction]`` -- every non-deleted row for the account in
        the period span under the scenario.
    """
    if not period_ids:
        return []
    return (
        db.session.query(Transaction)
        .filter(
            account_period_scope_clause(account_id, scenario_id, period_ids),
        )
        .all()
    )


def sum_net_worth_at_period(
    period_id: int, account_data: list[dict],
) -> Decimal:
    """Sum net worth across all accounts at a given period.

    Assets add their balance; liabilities subtract their magnitude
    (``-abs(bal)``), so a liability stored as a positive owed amount and
    one stored as a negative both reduce net worth by the same amount.

    Args:
        period_id: The pay period ID to look up balances for.
        account_data: List of dicts with ``balances`` (period_id ->
            ``Decimal``) and ``is_liability`` (``bool``).

    Returns:
        Net worth at the period as a ``Decimal``.
    """
    total = ZERO
    for data in account_data:
        bal = data["balances"].get(period_id, ZERO)
        if data["is_liability"]:
            total -= abs(bal)
        else:
            total += bal
    return total


def generate_debt_schedules(
    debt_accounts: list,
    scenario_id: int,
) -> dict[int, list]:
    """Generate amortization schedules for all debt accounts.

    Runs the loan resolver (E-18 / Commit 13) for each debt account
    and returns its :class:`AmortizationRow` schedule.  Same schedule
    the loan dashboard and /savings debt card consume, so mortgage
    interest, debt progress, and net worth liability all derive
    from the single resolver output (E-18 / Commit 15).

    Args:
        debt_accounts: Accounts with has_amortization=True.
        scenario_id: Baseline scenario ID for payment history.

    Returns:
        dict mapping account_id to list[AmortizationRow].
    """
    schedules: dict[int, list] = {}
    today = date.today()

    for account in debt_accounts:
        resolved = resolve_account_loan(account.id, scenario_id, today)
        if resolved is None:
            continue
        _, state = resolved
        schedules[account.id] = state.schedule

    return schedules


def loan_original_principal(account_id: int) -> Decimal:
    """Return a loan account's original principal, or ZERO if unset.

    The original principal is the schedule's pre-payment balance, used as
    the balance-before-first-payment fallback by both the net-worth
    liability column (:func:`build_account_balance_map`) and the year-end
    debt-progress section (``_compute_debt_progress``).

    Args:
        account_id: The loan account's ID.

    Returns:
        Decimal original principal, or ZERO when the account has no
        :class:`LoanParams` row.
    """
    params = (
        db.session.query(LoanParams)
        .filter_by(account_id=account_id)
        .first()
    )
    return params.original_principal if params else ZERO


def base_account_balance_map(
    account: Account,
    scenario: Scenario,
    periods: list,
) -> "OrderedDict[int, Decimal] | None":
    """Compute period_id -> balance for one account WITHOUT dispatch inputs.

    The base path used by the savings-progress section and by
    :func:`build_account_balance_map`'s fall-through: interest-bearing
    accounts (HYSA, Money Market, CD, HSA) use the balance calculator with
    interest accrual; everything else routes through the canonical
    entries-aware resolver.  It deliberately takes no amortization-schedule
    or growth-engine inputs -- callers that drive those use
    :func:`build_account_balance_map`.

    Args:
        account: The account to project.
        scenario: The baseline scenario.
        periods: All user pay periods.

    Returns:
        OrderedDict mapping period_id to Decimal balance, or None if the
        account has no anchor period.
    """
    if account.current_anchor_period_id is None:
        return None

    kind = classify_account(account)

    # Interest-bearing accounts (HYSA, Money Market, CD, HSA).  The
    # math-layer silent-degrade seam in
    # ``balance_calculator._entry_aware_amount`` was closed in Commit 5
    # (entries lazy-load via the SQLAlchemy descriptor instead of
    # short-circuiting to ``effective_amount``), so the entries-aware
    # reduction applies here even without ``selectinload``.
    if (kind is AccountProjectionKind.INTEREST
            and hasattr(account, "interest_params")
            and account.interest_params):
        transactions = load_account_period_transactions(
            account.id, scenario.id, [p.id for p in periods],
        )
        anchor_balance = account.current_anchor_balance or ZERO
        balances, _ = balance_calculator.calculate_balances_with_interest(
            anchor_balance=anchor_balance,
            anchor_period_id=account.current_anchor_period_id,
            periods=periods,
            transactions=transactions,
            interest_params=account.interest_params,
        )
        return balances

    # Standard checking/savings (and any unmatched types) route through
    # the canonical entries-aware producer (E-25 / CRIT-01 / F-009 /
    # R-1: Commit 8).  ``balances_for`` owns the transaction query with
    # ``selectinload(Transaction.entries)`` and resolves the anchor via
    # the dated ``AccountAnchorHistory`` SoT, so the net-worth aggregate
    # cannot disagree with the grid for the same input.
    return balance_resolver.balances_for(
        account, scenario.id, periods,
    ).balances


def build_account_balance_map(  # pylint: disable=too-many-arguments
    account: Account,
    scenario: Scenario,
    periods: list,
    *,
    debt_schedule: list | None,
    investment_params: InvestmentParams | None,
    deductions: list,
    salary_gross_biweekly: Decimal,
) -> "OrderedDict[int, Decimal] | None":
    """Compute period_id -> balance for one account, dispatching on type.

    The net-worth path.  Dispatches to the correct calculation engine:

    - Amortizing loans: the pre-generated amortization ``debt_schedule``.
    - Investment (401k, IRA, etc.): the growth engine, fed by this
      account's ``investment_params`` plus its ``deductions`` and the
      engine ``salary_gross_biweekly``.
    - Interest-bearing and everything else: the shared
      :func:`base_account_balance_map`.

    Takes loose per-account parameters (this account's schedule, params,
    deductions, and the engine gross-biweekly) rather than the year-end
    package's ``_ProjectionInputs`` bundle, so the savings cockpit can
    call it without constructing that year-end-specific value object; the
    year-end adapter unpacks its bundle per account at the call site.

    Pylint: ``too-many-arguments`` (7/5) -- the keyword-only group is
    this account's four independent projection inputs (its schedule, its
    investment params, its deductions, the engine gross-biweekly).  They
    are not a cohesive named concept that would survive as a value
    object; the year-end ``_ProjectionInputs`` bundle that previously
    carried them is the year-end package's own, and re-creating a
    kernel-specific bundle no other caller shares would be the stamp
    coupling the standards reject.  Keyword-only keeps the call sites
    self-documenting (and exempts the positional-count rule).

    Args:
        account: The account to project.
        scenario: The baseline scenario.
        periods: All user pay periods.
        debt_schedule: This account's pre-generated amortization schedule
            (the :func:`generate_debt_schedules` entry for it), or
            ``None`` when the account is not an amortizing loan or has no
            resolvable schedule.
        investment_params: This account's
            :class:`~app.models.investment_params.InvestmentParams`, or
            ``None`` when it is not a parameterized investment account.
        deductions: This account's active paycheck deductions (the
            growth engine's contribution feed; adapted internally).
        salary_gross_biweekly: Raise-aware engine gross per pay period
            (the employer-match cap basis).

    Returns:
        OrderedDict mapping period_id to Decimal balance, or None if the
        account has no anchor period.
    """
    if account.current_anchor_period_id is None:
        return None

    # MED-01 / S6-03: single flag-driven classifier replaces the
    # divergent branch ladders that used to express the same taxonomy
    # two different ways here and in
    # ``savings_dashboard_service._compute_account_projections``.
    kind = classify_account(account)

    # Amortizing loan accounts: use the pre-generated schedule.  The gate
    # is membership (``is not None``), NOT truthiness: an EMPTY schedule
    # ``[]`` is a resolved-but-unpaid loan (LoanParams present, no payment
    # events) and must route to the loan path -- ``compute_loan_period_
    # balance_map`` returns the original principal for it -- not fall
    # through to the entries-aware resolver (which would report the anchor
    # balance).  ``None`` means "not a resolved amortizing schedule for
    # this account," which correctly falls through.  Both callers pass
    # ``debt_schedules.get(account.id)``, so absent -> ``None`` -> base
    # path, present-but-empty -> ``[]`` -> loan path (the pre-move
    # ``account.id in inputs.debt_schedules`` membership semantics).
    if (kind is AccountProjectionKind.AMORTIZING
            and debt_schedule is not None):
        original = loan_original_principal(account.id)
        # F-21 / Commit 19: route through the shared
        # ``compute_loan_period_balance_map`` so the year-end liability
        # column and the savings-dashboard loan card consume the same
        # period-end-keyed balance derivation.
        return compute_loan_period_balance_map(
            debt_schedule, periods, original,
        )

    # Investment accounts: use the growth engine.  The base balance
    # feeding the projection comes from the canonical entries-aware
    # producer (E-25 / CRIT-01 / R-1).
    if kind is AccountProjectionKind.INVESTMENT and investment_params is not None:
        return _build_investment_balance_map(
            account, investment_params, scenario, periods,
            deductions, salary_gross_biweekly,
        )

    # Appreciating physical assets (Property): the user-set market value
    # compounds forward at its annual rate.  The rate rides on the
    # account's eager ``asset_appreciation_params`` backref, so no new
    # dispatch kwarg is needed; the helper flat-carries when the params
    # row is absent.
    if kind is AccountProjectionKind.APPRECIATING:
        return _build_appreciation_balance_map(account, scenario, periods)

    # Interest-bearing and plain accounts share the base path.
    return base_account_balance_map(account, scenario, periods)


def _build_investment_balance_map(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    account: Account,
    investment_params: InvestmentParams,
    scenario: Scenario,
    periods: list,
    deductions: list,
    salary_gross_biweekly: Decimal,
) -> "OrderedDict[int, Decimal]":
    """Build period_id -> balance map using the growth engine.

    Produces balances for all periods by combining three sources:

    - **Pre-anchor periods**: reverse growth engine projection backward
      from the anchor balance.
    - **Anchor period**: canonical entries-aware producer (anchor +
      remaining transactions).
    - **Post-anchor periods**: forward growth engine projection from
      the anchor balance.

    Pylint: ``too-many-arguments`` (6/5) /
    ``too-many-positional-arguments`` (6/5) -- the six are this account's
    independent growth-engine inputs (the account, its params, the
    scenario, the period list, its deductions, and the engine
    gross-biweekly).  They were previously folded behind the year-end
    ``_ProjectionInputs`` bundle; unfolding the two the kernel needs onto
    the signature is the honesty-first decomposition the standards prefer
    over re-wrapping them in a kernel-specific bundle no other caller
    would share.

    Args:
        account: Investment account.
        investment_params: InvestmentParams for the account.
        scenario: Baseline scenario.
        periods: All user pay periods.
        deductions: This account's active paycheck deductions (the
            contribution feed; adapted internally).
        salary_gross_biweekly: Raise-aware engine gross per pay period
            (the employer-match cap basis).

    Returns:
        OrderedDict mapping period_id to Decimal balance.
    """
    # Base balances from the canonical entries-aware producer (E-25 /
    # CRIT-01 / F-009 / R-1: Commit 8).  ``balances_for`` owns the
    # transaction query with ``selectinload(Transaction.entries)``,
    # resolves the anchor via the dated ``AccountAnchorHistory`` SoT,
    # and routes through the same engine math as the grid -- so the
    # base balance feeding the growth projection here is identical to
    # the figure rendered on the grid and other surfaces.
    base_balances = balance_resolver.balances_for(
        account, scenario.id, periods,
    ).balances

    # Find the anchor period's index to split pre/post-anchor.
    anchor_idx = get_anchor_period_index(account, periods)
    if anchor_idx is None:
        return base_balances

    pre_anchor = [p for p in periods if p.period_index < anchor_idx]
    post_anchor = [p for p in periods if p.period_index > anchor_idx]
    if not pre_anchor and not post_anchor:
        return base_balances

    # Adapt paycheck deductions, load the post-anchor shadow-income
    # contribution feed, and compute the growth-engine projection inputs.
    # F-22 / Commit 18: shared kwargs-splat helper.  The contribution feed
    # is fed straight in (used once) rather than bound to a local.
    proj_inputs = build_investment_projection_inputs(
        investment_params,
        adapt_deductions(deductions),
        _load_shadow_contributions(
            account.id, scenario.id, [p.id for p in post_anchor],
        ),
        periods,
        post_anchor[0] if post_anchor else pre_anchor[-1],
        salary_gross_biweekly,
    )

    anchor_balance = base_balances.get(
        account.current_anchor_period_id, ZERO,
    )
    anchor_period = next(
        (p for p in periods if p.id == account.current_anchor_period_id),
        None,
    )

    proj_by_pid = _forward_project_periods(
        post_anchor, anchor_balance, investment_params, proj_inputs,
    )
    rev_by_pid = _reverse_project_periods(
        pre_anchor, anchor_period, anchor_balance,
        investment_params, proj_inputs,
    )
    return _merge_balance_sources(
        periods, proj_by_pid, base_balances, rev_by_pid,
    )


def _build_appreciation_balance_map(
    account: Account,
    scenario: Scenario,
    periods: list,
) -> "OrderedDict[int, Decimal]":
    """Build period_id -> balance for an appreciating physical asset.

    The user-set market value (the canonical entries-aware resolver's flat
    anchor carry) is the base; post-anchor periods compound forward at the
    annual appreciation rate via the growth engine with no contributions.

    Pre-anchor periods are NOT back-cast: a manually-asserted point-in-time
    market value has no historical basis to compound backward from (unlike
    an investment's contribution history), so they keep the flat base
    value.  This is the deliberate asymmetry with
    :func:`_build_investment_balance_map`, which reverse-projects.

    Degrades to the flat base map when the account has no
    :class:`~app.models.asset_appreciation_params.AssetAppreciationParams`
    row yet (Property created, rate not set) or has no post-anchor periods.

    Args:
        account: The Property account; its ``asset_appreciation_params``
            backref carries the annual rate.
        scenario: The baseline scenario.
        periods: All user pay periods.

    Returns:
        OrderedDict mapping period_id to Decimal balance.
    """
    base_balances = balance_resolver.balances_for(
        account, scenario.id, periods,
    ).balances

    anchor_idx = get_anchor_period_index(account, periods)
    if anchor_idx is None:
        return base_balances

    anchor_balance = base_balances.get(account.current_anchor_period_id, ZERO)

    # Compound the market value forward at the annual rate (no
    # contributions) when a rate is configured.  An absent params row
    # leaves ``proj_by_pid`` empty so the value simply flat-carries forward
    # via the resolver's base map instead.
    proj_by_pid: dict = {}
    params = account.asset_appreciation_params
    if params is not None:
        post_anchor = [p for p in periods if p.period_index > anchor_idx]
        if post_anchor:
            projection = growth_engine.project_balance(
                current_balance=anchor_balance,
                assumed_annual_return=params.annual_appreciation_rate,
                periods=post_anchor,
            )
            proj_by_pid = {pb.period_id: pb.end_balance for pb in projection}

    # Per period: the compounded value (post-anchor), else the resolver's
    # flat carry (the anchor and forward), else the anchor value
    # (pre-anchor).  The resolver does not produce pre-anchor balances, so
    # a manually-set market value is held FLAT backward (back-filled at the
    # anchor value) rather than reverse-compounded -- there is no
    # historical valuation to compound backward from -- yet the home still
    # contributes to net worth at every period.
    result: "OrderedDict[int, Decimal]" = OrderedDict()
    for period in periods:
        if period.id in proj_by_pid:
            result[period.id] = proj_by_pid[period.id]
        elif period.id in base_balances:
            result[period.id] = base_balances[period.id]
        else:
            result[period.id] = anchor_balance
    return result


def _forward_project_periods(
    post_anchor: list,
    anchor_balance: Decimal,
    investment_params: InvestmentParams,
    proj_inputs,
) -> dict:
    """Forward-project post-anchor period-end balances via the growth engine.

    Args:
        post_anchor: Periods after the anchor (chronological).
        anchor_balance: Balance at the end of the anchor period.
        investment_params: InvestmentParams (for the assumed return).
        proj_inputs: ``build_investment_projection_inputs`` result.

    Returns:
        dict mapping period_id to projected end balance, or ``{}`` when
        there are no post-anchor periods.
    """
    if not post_anchor:
        return {}

    projection = growth_engine.project_balance(
        current_balance=anchor_balance,
        assumed_annual_return=investment_params.assumed_annual_return,
        periods=post_anchor,
        periodic_contribution=proj_inputs.periodic_contribution,
        employer_params=proj_inputs.employer_params,
        annual_contribution_limit=proj_inputs.annual_contribution_limit,
        ytd_contributions_start=proj_inputs.ytd_contributions,
    )
    return {pb.period_id: pb.end_balance for pb in projection}


def _reverse_project_periods(
    pre_anchor: list,
    anchor_period,
    anchor_balance: Decimal,
    investment_params: InvestmentParams,
    proj_inputs,
) -> dict:
    """Reverse-project pre-anchor period-end balances via the growth engine.

    The anchor period is appended to the reverse list so
    ``reverse_project_balance`` has the correct endpoint (the anchor
    balance is the end-of-anchor-period value); the anchor's own entry is
    then dropped from the result so the base-balance map keeps ownership
    of it.

    Args:
        pre_anchor: Periods before the anchor (chronological).
        anchor_period: The anchor PayPeriod (the reverse endpoint), or
            None if it could not be resolved.
        anchor_balance: Balance at the end of the anchor period.
        investment_params: InvestmentParams (for the assumed return).
        proj_inputs: ``build_investment_projection_inputs`` result.

    Returns:
        dict mapping period_id to projected end balance, or ``{}`` when
        there are no pre-anchor periods.
    """
    if not pre_anchor or anchor_period is None:
        return {}

    # DH-#28: thread the annual contribution limit so the reverse caps each
    # period exactly as the forward path does (otherwise a maxed-out account's
    # pre-anchor balances are derived too low).  ytd_contributions_start=ZERO
    # because this window starts at the user's earliest period, before which
    # no contribution exists; each later calendar year inside the window resets
    # YTD on its own (the engine replays the year-boundary reset).
    reversed_proj = growth_engine.reverse_project_balance(
        anchor_balance=anchor_balance,
        assumed_annual_return=investment_params.assumed_annual_return,
        periods=pre_anchor + [anchor_period],
        periodic_contribution=proj_inputs.periodic_contribution,
        employer_params=proj_inputs.employer_params,
        annual_contribution_limit=proj_inputs.annual_contribution_limit,
        ytd_contributions_start=ZERO,
    )
    return {
        pb.period_id: pb.end_balance
        for pb in reversed_proj
        if pb.period_id != anchor_period.id
    }


def _merge_balance_sources(
    periods: list,
    proj_by_pid: dict,
    base_balances: dict,
    rev_by_pid: dict,
) -> "OrderedDict[int, Decimal]":
    """Merge the three balance sources into one period-ordered map.

    For each period, prefers the forward projection, then the canonical
    base balance, then the reverse projection.  Periods absent from all
    three sources are omitted.

    Args:
        periods: All user pay periods (defines output order).
        proj_by_pid: Forward post-anchor balances by period_id.
        base_balances: Canonical anchor/base balances by period_id.
        rev_by_pid: Reverse pre-anchor balances by period_id.

    Returns:
        OrderedDict mapping period_id to Decimal balance.
    """
    result: "OrderedDict[int, Decimal]" = OrderedDict()
    for period in periods:
        if period.id in proj_by_pid:
            result[period.id] = proj_by_pid[period.id]
        elif period.id in base_balances:
            result[period.id] = base_balances[period.id]
        elif period.id in rev_by_pid:
            result[period.id] = rev_by_pid[period.id]
    return result


def _load_shadow_contributions(
    account_id: int,
    scenario_id: int,
    period_ids: list[int],
) -> list:
    """Load settled shadow-income (transfer-in) transactions for an account.

    The contribution-history feed for the growth engine, shared by the
    year-end savings-progress projection
    (``_project_investment_for_year``) and the net-worth investment
    balance map (:func:`_build_investment_balance_map`).  ``status`` and
    ``pay_period`` are eager-loaded so the downstream consumer
    (``investment_projection.calculate_investment_inputs`` /
    ``build_contribution_timeline``) reads ``txn.status.*`` /
    ``txn.pay_period`` without an N+1.  The status filter routes through
    ``balance_excluded_status_ids`` (D6-09 / MED-02), which lets the
    query drop the ``Status`` INNER JOIN while the ``joinedload`` keeps
    the attribute available; the audit-trigger row count is unchanged.

    Args:
        account_id: Target account ID.
        scenario_id: Baseline scenario ID.
        period_ids: Pay period IDs whose shadow income forms the
            contribution history.

    Returns:
        List of shadow-income Transaction objects, or ``[]`` when
        ``period_ids`` is empty.
    """
    if not period_ids:
        return []

    # Shadow-income definition + status/pay_period eager-loads come from
    # the shared ``query_shadow_income`` builder (the R0801 sibling is
    # ``loan_payment_service.get_payment_history``, NOT ``budget_variance``
    # as a prior rationale wrongly claimed); the feed scopes it to the
    # supplied periods.
    return (
        query_shadow_income(account_id, scenario_id)
        .filter(Transaction.pay_period_id.in_(period_ids))
        .all()
    )


def get_anchor_period_index(
    account: Account, all_periods: list,
) -> int | None:
    """Return the period_index of the account's anchor period.

    Args:
        account: Account with current_anchor_period_id set.
        all_periods: All user pay periods.

    Returns:
        int period_index, or None if the anchor period is not found.
    """
    anchor_pid = account.current_anchor_period_id
    if anchor_pid is None:
        return None
    for p in all_periods:
        if p.id == anchor_pid:
            return p.period_index
    return None
