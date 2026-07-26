"""
Shekel Budget App -- Investment growth balance sub-chain.

The investment / retirement growth projection, extracted from the kernel
(:mod:`app.services.balance_at._kernel`, which reached its module-size
ceiling).  Two views built on ONE assembly and ONE forward projection so
they cannot drift:

* :func:`build_investment_balance_map` -- the modeled balance an
  investment account DISPLAYS: the anchor compounded forward at the assumed
  return (plus contributions), reverse-projected before the anchor, spliced
  over the anchor-forward cash basis.  The kernel's
  :func:`~app.services.balance_at._kernel.build_account_balance_map` dispatches
  here for INVESTMENT accounts.
* :func:`investment_growth_since_anchor` -- the growth-vs-contributed
  decomposition the detail page's Growth chip reads.  It sums the SAME
  forward projection's per-period ``growth`` and ``contribution`` /
  ``employer_contribution`` rows, so ``growth + contributed`` reconciles to
  the cent with ``balance_map[current] - anchor_balance`` (the hero balance
  minus the anchor balance).

Both flow through :func:`_assemble_investment_projection_inputs` (one set of
growth-engine inputs) and :func:`_forward_project_rows` (one forward call),
which is what makes the reconciliation exact.

This module OWNS the investment-growth primitives extracted alongside the
builders -- :func:`investment_base_balance_map` (the cash-basis seed),
:func:`get_anchor_period_index`, and :func:`_load_shadow_contributions` (the
contribution feed) -- and the appreciating-asset builder
:func:`build_appreciation_balance_map` (Property market value compounded
forward), the other kind the kernel dispatches here.  Other ``balance_at``
seam modules import those primitives FROM here.  It imports NOTHING back from
the kernel (:mod:`~app.services.balance_at._kernel`), so the static import
graph carries no cycle; now that both are siblings in the seam package the
kernel's :func:`~app.services.balance_at._kernel.build_account_balance_map`
reaches the two builders through a plain sibling import (plan step D1d retired
the cross-module lazy import its rationale no longer justified).

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"):
this module imports no Flask symbol and performs no writes.  All money is
:class:`~decimal.Decimal`.
"""

from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal

from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.services import growth_engine
from app.services.investment_projection import InvestmentInputs, adapt_deductions
from app.services.loan_loaders import query_shadow_income
from app.services.projection_inputs import build_investment_projection_inputs

from . import _cash_engine

ZERO = Decimal("0")


def investment_base_balance_map(
    account: Account,
    scenario: Scenario,
    periods: list,
) -> "OrderedDict[int, Decimal]":
    """Return an investment account's cash-basis (pre-growth) balance map.

    The transaction-sum balance an investment account holds from its anchor
    plus contributions, with NO modeled growth layered on -- the seed a forward
    growth projection compounds from.  It is the anchor-forward entries-aware
    producer's map verbatim
    (:func:`~app.services.balance_at._cash_engine.balances_for`).

    **That producer is no longer what the cash surfaces read** (plan step
    X-c2b2): they read the FOLD, which replays every assertion and counts
    settled money from the day it moved, so this seed and a rendered cash
    balance for the same rows can differ by whatever has settled since the
    account was last asserted.  The gap is deliberate: the growth projection
    compounds BACKWARD through pre-anchor periods
    (:func:`build_investment_balance_map`), a ruled model the fold's own
    pre-anchor answer must not silently replace (finding N-43).  **It closes at
    plan step X-g, not by WINDOWING this base onto the fold but by deleting the
    merge that makes the clash possible** -- the window was plan step X-c2c3 and
    ruling R-V cancelled it as a compensator (finding N-72).  What IS measured today is the
    other direction: plan step X-c2b2 left the three real IRAs and the Home
    unmoved on every column precisely BECAUSE they still seed here, which is
    what makes this the last cash producer rather than a second opinion.

    Shared by every investment growth projection so none re-derives the seed:
    :func:`build_investment_balance_map` (which forward/reverse-projects growth
    off it), the year-end savings-progress projection, and the investment /
    retirement dashboard forward projections (which seed from this cash basis
    while the DISPLAYED headline reads the modeled
    :func:`build_investment_balance_map`).  Each must seed from THIS pre-growth
    map, not the growth-modeled map the ``balance_at`` seam returns -- seeding
    from the modeled balance would compound growth on growth (re-grow the
    current period).  Exposed so those consumers read the seed without calling
    the fenced cash producer directly (the seam re-exposes it as
    :func:`~app.services.balance_at.investment_seed_map`).

    Args:
        account: The investment account.
        scenario: The baseline scenario (its id scopes the resolver).
        periods: The pay periods to span (ordered by ``period_index``; must
            include the anchor so the resolver has its running seed).

    Returns:
        The ``OrderedDict`` period_id -> Decimal cash-basis balance.
    """
    return _cash_engine.balances_for(account, scenario.id, periods)


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


def _load_shadow_contributions(
    account_id: int,
    scenario_id: int,
    period_ids: list[int],
) -> list:
    """Load settled shadow-income (transfer-in) transactions for an account.

    The contribution-history feed for the growth engine, shared by the
    year-end savings-progress projection and the net-worth investment balance
    map (:func:`build_investment_balance_map`).  ``status`` and ``pay_period``
    are eager-loaded (via the shared :func:`query_shadow_income` builder) so
    the downstream consumer
    (``investment_projection.calculate_investment_inputs`` /
    ``build_contribution_timeline``) reads ``txn.status.*`` / ``txn.pay_period``
    without an N+1; the feed scopes that builder to the supplied periods.

    Args:
        account_id: Target account ID.
        scenario_id: Baseline scenario ID.
        period_ids: Pay period IDs whose shadow income forms the contribution
            history.

    Returns:
        List of shadow-income Transaction objects, or ``[]`` when ``period_ids``
        is empty.
    """
    if not period_ids:
        return []

    return (
        query_shadow_income(account_id, scenario_id)
        .filter(Transaction.pay_period_id.in_(period_ids))
        .all()
    )


@dataclass(frozen=True)
class _InvestmentProjectionInputs:
    """The assembled inputs an investment's growth projection walks from.

    A cohesive assembly record: the base cash-basis map, the pre/post-anchor
    period split, the growth-engine inputs, and the anchor balance/period the
    forward and reverse projections pivot on.  Assembled ONCE by
    :func:`_assemble_investment_projection_inputs` so
    :func:`build_investment_balance_map` and
    :func:`investment_growth_since_anchor` read ONE set of projection inputs
    and cannot drift on the periodic contribution, the employer match, the
    YTD seed, or the anchor balance.

    ``pre_anchor`` and ``post_anchor`` are both empty (and ``proj_inputs`` /
    ``anchor_period`` ``None``) in the degenerate no-projection case (no
    anchor period, or the anchor is the user's only period): the map then
    flat-carries ``base_balances`` and the decomposition hides.

    Attributes:
        base_balances: Cash-basis (pre-growth) period_id -> balance map from
            :func:`~app.services.balance_at._kernel.investment_base_balance_map`;
            the anchor period's entry is the seed both projections pivot on.
        pre_anchor: Periods before the anchor (chronological), reverse-projected.
        post_anchor: Periods after the anchor (chronological), forward-projected.
        proj_inputs: The :class:`~app.services.investment_projection.InvestmentInputs`
            (periodic contribution, employer params, annual limit, YTD seed),
            or ``None`` in the no-projection case.
        anchor_balance: The end-of-anchor-period balance both projections
            pivot on (``ZERO`` in the no-projection case).
        anchor_period: The anchor :class:`~app.models.pay_period.PayPeriod`
            (the reverse endpoint), or ``None``.
    """

    base_balances: "OrderedDict[int, Decimal]"
    pre_anchor: list
    post_anchor: list
    proj_inputs: InvestmentInputs | None
    anchor_balance: Decimal
    anchor_period: object | None


def _assemble_investment_projection_inputs(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    account: Account,
    investment_params: InvestmentParams,
    scenario: Scenario,
    periods: list,
    deductions: list,
    salary_gross_biweekly: Decimal,
) -> _InvestmentProjectionInputs:
    """Assemble the growth-engine inputs for an investment account.

    The single assembly point shared by :func:`build_investment_balance_map`
    (which forward + reverse-projects to build the displayed map) and
    :func:`investment_growth_since_anchor` (which reads the forward rows to
    decompose growth vs contributions), so the two read ONE set of inputs and
    cannot drift.  ``base_balances`` comes from the anchor-forward entries-aware
    producer (E-25 / CRIT-01 / F-009 / R-1: Commit 8) via the shared seed
    accessor :func:`investment_base_balance_map`, so the growth projection and
    the year-end savings-progress projection compound from ONE cash basis.  That
    basis is NOT the one the grid renders any more -- see the seed accessor for
    why the gap is deliberate until plan step X-g.

    Returns an :class:`_InvestmentProjectionInputs` whose ``pre_anchor`` and
    ``post_anchor`` are both empty in the no-projection case; ``base_balances``
    is always populated so the map caller flat-carries it without a re-query.

    Pylint: ``too-many-arguments`` (6/5) / ``too-many-positional-arguments``
    (6/5) -- the six are this account's independent growth-engine inputs
    (the account, its params, the scenario, the period list, its deductions,
    and the engine gross-biweekly); they mirror the kernel dispatch's
    per-account inputs, passed positionally at the two call sites.
    """
    base_balances = investment_base_balance_map(account, scenario, periods)
    anchor_idx = get_anchor_period_index(account, periods)
    if anchor_idx is None:
        return _InvestmentProjectionInputs(
            base_balances, [], [], None, ZERO, None,
        )

    pre_anchor = [p for p in periods if p.period_index < anchor_idx]
    post_anchor = [p for p in periods if p.period_index > anchor_idx]
    if not pre_anchor and not post_anchor:
        return _InvestmentProjectionInputs(
            base_balances, [], [], None, ZERO, None,
        )

    # F-22 / Commit 18: shared kwargs-splat helper.  The reference period is
    # the first post-anchor period (falling back to the last pre-anchor for an
    # anchor at the far edge), matching the YTD seed the forward walk uses.
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
    anchor_balance = base_balances.get(account.current_anchor_period_id, ZERO)
    anchor_period = next(
        (p for p in periods if p.id == account.current_anchor_period_id),
        None,
    )
    return _InvestmentProjectionInputs(
        base_balances, pre_anchor, post_anchor,
        proj_inputs, anchor_balance, anchor_period,
    )


def _forward_project_rows(
    post_anchor: list,
    anchor_balance: Decimal,
    investment_params: InvestmentParams,
    proj_inputs,
) -> "list[growth_engine.ProjectedBalance]":
    """Forward-project post-anchor periods, returning the full engine rows.

    The single growth-engine forward call, shared by
    :func:`_forward_project_periods` (which keeps only ``end_balance`` for the
    displayed map) and :func:`investment_growth_since_anchor` (which reads the
    per-period ``growth`` / ``contribution`` / ``employer_contribution``
    decomposition), so the map's post-anchor balances and the
    growth-since-anchor split come from ONE projection and cannot drift.

    Args:
        post_anchor: Periods after the anchor (chronological).
        anchor_balance: Balance at the end of the anchor period.
        investment_params: InvestmentParams (for the assumed return).
        proj_inputs: ``build_investment_projection_inputs`` result.

    Returns:
        The list of :class:`~app.services.growth_engine.ProjectedBalance`
        rows (one per period), or ``[]`` when there are no post-anchor periods.
    """
    if not post_anchor:
        return []

    return growth_engine.project_balance(
        current_balance=anchor_balance,
        assumed_annual_return=investment_params.assumed_annual_return,
        periods=post_anchor,
        periodic_contribution=proj_inputs.periodic_contribution,
        employer_params=proj_inputs.employer_params,
        annual_contribution_limit=proj_inputs.annual_contribution_limit,
        ytd_contributions_start=proj_inputs.ytd_contributions,
    )


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
        dict mapping period_id to projected end balance, or ``{}`` when there
        are no post-anchor periods.
    """
    return {
        pb.period_id: pb.end_balance
        for pb in _forward_project_rows(
            post_anchor, anchor_balance, investment_params, proj_inputs,
        )
    }


def _reverse_project_periods(
    pre_anchor: list,
    anchor_period,
    anchor_balance: Decimal,
    investment_params: InvestmentParams,
    proj_inputs,
) -> dict:
    """Reverse-project pre-anchor period-end balances via the growth engine.

    The anchor period is appended to the reverse list so
    ``reverse_project_balance`` has the correct endpoint (the anchor balance
    is the end-of-anchor-period value); the anchor's own entry is then dropped
    from the result so the base-balance map keeps ownership of it.

    Args:
        pre_anchor: Periods before the anchor (chronological).
        anchor_period: The anchor PayPeriod (the reverse endpoint), or None if
            it could not be resolved.
        anchor_balance: Balance at the end of the anchor period.
        investment_params: InvestmentParams (for the assumed return).
        proj_inputs: ``build_investment_projection_inputs`` result.

    Returns:
        dict mapping period_id to projected end balance, or ``{}`` when there
        are no pre-anchor periods.
    """
    if not pre_anchor or anchor_period is None:
        return {}

    # DH-#28: thread the annual contribution limit so the reverse caps each
    # period exactly as the forward path does (otherwise a maxed-out account's
    # pre-anchor balances are derived too low).  ytd_contributions_start=ZERO
    # because this window starts at the user's earliest period, before which no
    # contribution exists; each later calendar year inside the window resets
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

    For each period, prefers the forward projection, then the base balance,
    then the reverse projection.  Periods absent from all three sources are
    omitted.

    Args:
        periods: All user pay periods (defines output order).
        proj_by_pid: Forward post-anchor balances by period_id.
        base_balances: Anchor-forward base balances by period_id.
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


def build_appreciation_balance_map(
    account: Account,
    scenario: Scenario,
    periods: list,
) -> "OrderedDict[int, Decimal]":
    """Build period_id -> balance for an appreciating physical asset.

    The user-set market value (the anchor-forward producer's flat anchor
    carry) is the base; post-anchor periods compound forward at the
    annual appreciation rate via the growth engine with no contributions.
    Pre-anchor periods are NOT back-cast: a manually-asserted point-in-time
    market value has no historical basis to compound backward from (unlike an
    investment's contribution history), so they flat-carry the anchor value --
    the deliberate asymmetry with :func:`build_investment_balance_map`, which
    reverse-projects.

    Degrades to the flat base map when the account has no
    :class:`~app.models.asset_appreciation_params.AssetAppreciationParams` row
    yet (Property created, rate not set) or has no post-anchor periods.  The
    kernel's :func:`~app.services.balance_at._kernel.build_account_balance_map`
    dispatches here for APPRECIATING accounts.

    Args:
        account: The Property account; its ``asset_appreciation_params``
            backref carries the annual rate.
        scenario: The baseline scenario.
        periods: All user pay periods.

    Returns:
        OrderedDict mapping period_id to Decimal balance.
    """
    base_balances = _cash_engine.balances_for(
        account, scenario.id, periods,
    )

    anchor_idx = get_anchor_period_index(account, periods)
    if anchor_idx is None:
        return base_balances

    anchor_balance = base_balances.get(account.current_anchor_period_id, ZERO)

    # Compound the market value forward at the annual rate (no contributions)
    # when a rate is configured.  An absent params row leaves the forward map
    # empty so the value simply flat-carries via the resolver's base map.
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

    # A manually-set market value has no historical valuation to reverse-
    # compound, so pre-anchor periods (and any gap) flat-carry the anchor
    # value: expressed as the merge's third source so the per-period pick is
    # the SAME 3-source merge the investment map uses (DRY), while the home
    # still contributes to net worth at every period.
    anchor_carry = {p.id: anchor_balance for p in periods}
    return _merge_balance_sources(
        periods, proj_by_pid, base_balances, anchor_carry,
    )


def build_investment_balance_map(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    account: Account,
    investment_params: InvestmentParams,
    scenario: Scenario,
    periods: list,
    deductions: list,
    salary_gross_biweekly: Decimal,
) -> "OrderedDict[int, Decimal]":
    """Build period_id -> balance map using the growth engine.

    Produces balances for all periods by combining three sources:

    - **Pre-anchor periods**: reverse growth engine projection backward from
      the anchor balance.
    - **Anchor period**: the anchor-forward entries-aware producer (anchor +
      remaining transactions).
    - **Post-anchor periods**: forward growth engine projection from the
      anchor balance.

    Assembles its inputs via :func:`_assemble_investment_projection_inputs`
    (shared with :func:`investment_growth_since_anchor` so the displayed map
    and the growth decomposition cannot drift), then forward/reverse-projects
    and merges.  When the account has no anchor period, or the anchor is the
    user's only period, the flat entries-aware base carries the whole map.

    Pylint: ``too-many-arguments`` (6/5) / ``too-many-positional-arguments``
    (6/5) -- the six are this account's independent growth-engine inputs (the
    account, its params, the scenario, the period list, its deductions, and
    the engine gross-biweekly).  They were previously folded behind the
    year-end ``_ProjectionInputs`` bundle; unfolding the two the kernel needs
    onto the signature is the honesty-first decomposition the standards prefer
    over re-wrapping them in a kernel-specific bundle no other caller shares.

    Args:
        account: Investment account.
        investment_params: InvestmentParams for the account.
        scenario: Baseline scenario.
        periods: All user pay periods.
        deductions: This account's active paycheck deductions (the
            contribution feed; adapted internally).
        salary_gross_biweekly: Raise-aware engine gross per pay period (the
            employer-match cap basis).

    Returns:
        OrderedDict mapping period_id to Decimal balance.
    """
    parts = _assemble_investment_projection_inputs(
        account, investment_params, scenario, periods,
        deductions, salary_gross_biweekly,
    )
    if not parts.pre_anchor and not parts.post_anchor:
        return parts.base_balances

    proj_by_pid = _forward_project_periods(
        parts.post_anchor, parts.anchor_balance,
        investment_params, parts.proj_inputs,
    )
    rev_by_pid = _reverse_project_periods(
        parts.pre_anchor, parts.anchor_period, parts.anchor_balance,
        investment_params, parts.proj_inputs,
    )
    return _merge_balance_sources(
        periods, proj_by_pid, parts.base_balances, rev_by_pid,
    )


def investment_growth_since_anchor(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    account: Account,
    investment_params: "InvestmentParams | None",
    scenario: Scenario,
    periods: list,
    deductions: list,
    salary_gross_biweekly: Decimal,
    current_period,
) -> "tuple[Decimal, Decimal] | None":
    """Decompose growth since the anchor into (growth, contributed).

    Splits the investment's modeled balance change over the window
    ``(anchor, current_period]`` into compound GROWTH and applied
    CONTRIBUTIONS (the annual-limit-capped employee amount plus the employer
    match).  Both are read from the SAME forward projection whose end balances
    :func:`build_investment_balance_map` uses for its post-anchor values, so
    the two reconcile to the cent::

        growth + contributed
          == rows[-1].end_balance - rows[0].start_balance
          == balance_map[current_period] - anchor_balance

    (the hero balance minus the anchor balance).  The window is a PREFIX of the
    post-anchor periods, and the growth engine's walk is causal (each period
    depends only on the seed and prior periods), so projecting the prefix
    yields byte-identical rows to the full map's projection for those periods.
    The telescoping is exact because each row satisfies
    ``end = start + growth + contribution + employer`` on cent-rounded
    components; the one non-invertible forward step, the M-06
    ``max(balance, 0)`` clamp, cannot fire while the balance stays non-negative
    (growth >= 0, contributions >= 0), which holds for every standard
    investment.

    Returns ``None`` (the caller hides the chip) when there is no
    ``current_period``, no ``investment_params``, or no post-anchor period at
    or before the current period (the account was anchored this period or
    later, so no growth has accrued since the anchor yet).

    Args:
        account: Investment account.
        investment_params: InvestmentParams for the account, or ``None``.
        scenario: Baseline scenario.
        periods: All user pay periods.
        deductions: This account's active paycheck deductions.
        salary_gross_biweekly: Raise-aware engine gross per pay period.
        current_period: The current :class:`~app.models.pay_period.PayPeriod`,
            or ``None``.

    Returns:
        ``(growth, contributed)`` Decimals (cent-precise), or ``None``.

    Pylint: ``too-many-arguments`` (7/5) / ``too-many-positional-arguments``
    (7/5) -- the seven mirror :func:`build_investment_balance_map`'s inputs
    plus the current-period window bound; the shared-assembly split keeps the
    body thin.
    """
    if investment_params is None or current_period is None:
        return None
    parts = _assemble_investment_projection_inputs(
        account, investment_params, scenario, periods,
        deductions, salary_gross_biweekly,
    )
    window = [
        p for p in parts.post_anchor
        if p.period_index <= current_period.period_index
    ]
    if not window:
        return None
    rows = _forward_project_rows(
        window, parts.anchor_balance, investment_params, parts.proj_inputs,
    )
    growth = sum((pb.growth for pb in rows), ZERO)
    contributed = sum(
        (pb.contribution + pb.employer_contribution for pb in rows), ZERO,
    )
    return growth, contributed
