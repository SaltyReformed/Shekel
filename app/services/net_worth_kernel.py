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
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
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


@dataclass(frozen=True)
class DebtSchedule:
    """A debt account's resolved amortization schedule and current balance.

    Bundles the two outputs of one
    :func:`app.services.loan_payment_service.resolve_account_loan` call: the
    amortization ``schedule`` and the resolver-derived ``current_balance``.
    Carrying them together lets the period-balance map report today's balance
    -- not the loan's original principal -- for periods before the first
    upcoming payment, while guaranteeing the schedule and the balance come
    from the SAME resolution and so cannot drift.

    Attributes:
        schedule: The loan's :class:`AmortizationRow` list (today-forward:
            confirmed-history rows plus committed forward rows).  May be empty
            for a fully-resolved / paid-off loan.
        current_balance: The resolver's
            :attr:`~app.services.loan_resolver.LoanState.current_balance` as
            of today -- the pre-first-payment and empty-schedule fallback.
    """

    schedule: list
    current_balance: Decimal


def generate_debt_schedules(
    debt_accounts: list,
    scenario_id: int,
) -> dict[int, "DebtSchedule"]:
    """Generate amortization schedules and current balances for debt accounts.

    Runs the loan resolver (E-18 / Commit 13) for each debt account and
    returns its :class:`DebtSchedule` -- the :class:`AmortizationRow` schedule
    plus the resolver-derived current balance.  Same resolver output the loan
    dashboard and /savings debt card consume, so mortgage interest, debt
    progress, and net-worth liability all derive from the single resolver call
    (E-18 / Commit 15).

    Args:
        debt_accounts: Accounts with has_amortization=True.
        scenario_id: Baseline scenario ID for payment history.

    Returns:
        dict mapping account_id to :class:`DebtSchedule`.
    """
    schedules: dict[int, DebtSchedule] = {}
    today = date.today()

    for account in debt_accounts:
        resolved = resolve_account_loan(account.id, scenario_id, today)
        if resolved is None:
            continue
        _, state = resolved
        schedules[account.id] = DebtSchedule(
            schedule=state.schedule,
            current_balance=state.current_balance,
        )

    return schedules


def _account_interest_projection(
    account: Account,
    scenario: Scenario,
    periods: list,
    interest_params: InterestParams,
    amount_overrides: "dict[int, Decimal] | None" = None,
) -> "tuple[OrderedDict[int, Decimal], dict[int, Decimal]]":
    """Run the interest-layered balance walk for one account.

    The single home for the "load this account's transactions and run
    :func:`~app.services.balance_calculator.calculate_balances_with_interest`
    over them" sequence shared by the interest BALANCE path
    (:func:`base_account_balance_map`, which keeps the balances and
    discards the interest) and the interest-EARNED accessor
    (:func:`interest_by_period_for_account`, which keeps the interest and
    discards the balances).  Folding the two into one helper keeps the
    transaction-scope query, the anchor-balance coalesce, and the
    calculator kwargs identical between the balance figure a screen
    renders and the interest figure the year-end savings-progress section
    reports -- they cannot drift onto two copies of the same walk (R0801).

    Args:
        account: The interest-bearing account.  Its ``current_anchor_*``
            columns seed the walk; the caller is responsible for the
            no-anchor guard.
        scenario: The baseline scenario (its id scopes the transaction
            query).
        periods: The pay periods to walk (ordered by ``period_index``).
        interest_params: The account's
            :class:`~app.models.interest_params.InterestParams` (APY +
            compounding frequency) the calculator layers interest from.
        amount_overrides: Optional ``{transaction_id: Decimal}`` live map,
            forwarded verbatim to the calculator; ``None`` (the year-end
            interest path) preserves the stored-amount behavior.

    Returns:
        ``(balances, interest_by_period)`` -- the calculator's two outputs
        verbatim: the period_id -> Decimal end-balance map (interest
        layered in) and the period_id -> Decimal interest-earned map.
    """
    transactions = load_account_period_transactions(
        account.id, scenario.id, [p.id for p in periods],
    )
    anchor_balance = account.current_anchor_balance or ZERO
    return balance_calculator.calculate_balances_with_interest(
        anchor_balance=anchor_balance,
        anchor_period_id=account.current_anchor_period_id,
        periods=periods,
        transactions=transactions,
        interest_params=interest_params,
        amount_overrides=amount_overrides,
    )


def base_account_balance_map(
    account: Account,
    scenario: Scenario,
    periods: list,
    *,
    amount_overrides: "dict[int, Decimal] | None" = None,
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
        amount_overrides: Optional ``{transaction_id: Decimal}`` live
            projected-net / loan-derive map (Workstream B), forwarded
            verbatim to whichever cash producer this account routes to
            (:func:`~app.services.balance_calculator.calculate_balances_with_interest`
            for the interest path,
            :func:`~app.services.balance_resolver.balances_for` for the
            plain path).  Default ``None`` lets each producer build its own
            live override map internally, byte-identical to the prior
            behavior; the ``balance_at`` seam threads the grid's pre-built
            map through here for grid parity.

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
        balances, _ = _account_interest_projection(
            account, scenario, periods, account.interest_params,
            amount_overrides,
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
        amount_overrides=amount_overrides,
    ).balances


def interest_by_period_for_account(
    account: Account,
    scenario: Scenario,
    periods: list,
    interest_params: InterestParams,
) -> dict[int, Decimal]:
    """Return period_id -> interest earned for an interest-bearing account.

    The engine-cluster accessor the year-end savings-progress section
    (:func:`app.services.year_end_summary_service._balances._compute_interest_for_year`)
    reads instead of calling
    :func:`~app.services.balance_calculator.calculate_balances_with_interest`
    directly: interest EARNED is rich projection detail, not a
    balance-at-T figure, so it is not a ``balance_at`` seam concern, yet
    the producer that computes it is fenced to the engine cluster.  This
    accessor keeps that producer call inside the kernel (where it belongs
    with :func:`base_account_balance_map`, which shares the same
    :func:`_account_interest_projection` walk) while the year-end consumer
    sees only the interest map it needs.

    A None-anchor account earns no projectable interest (the walk produces
    no balances to layer interest on), returned as the empty map so the
    caller's year-filtered sum is ``Decimal("0")`` -- matching the prior
    inline ``current_anchor_period_id is None -> ZERO`` early-out.

    Args:
        account: The interest-bearing account.
        scenario: The baseline scenario (its id scopes the transaction
            query).
        periods: All user pay periods (the walk domain; the caller
            filters to the periods whose interest it wants).
        interest_params: The account's
            :class:`~app.models.interest_params.InterestParams`.

    Returns:
        ``dict`` mapping period_id to the ``Decimal`` interest earned in
        that period; ``{}`` when the account has no anchor period.
    """
    if account.current_anchor_period_id is None:
        return {}
    _, interest_by_period = _account_interest_projection(
        account, scenario, periods, interest_params,
    )
    return interest_by_period


def build_account_balance_map(  # pylint: disable=too-many-arguments
    account: Account,
    scenario: Scenario,
    periods: list,
    *,
    debt_schedule: "DebtSchedule | None",
    investment_params: InvestmentParams | None,
    deductions: list,
    salary_gross_biweekly: Decimal,
    amount_overrides: "dict[int, Decimal] | None" = None,
) -> "OrderedDict[int, Decimal] | None":
    """Compute period_id -> balance for one account, dispatching on type.

    The net-worth path.  Dispatches to the correct calculation engine:

    - Amortizing loans: the pre-generated ``debt_schedule`` (its schedule
      plus the resolver's current balance).
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

    Pylint: ``too-many-arguments`` (8/5) -- the keyword-only group is
    this account's four independent projection inputs (its schedule, its
    investment params, its deductions, the engine gross-biweekly) plus the
    cash-path ``amount_overrides`` passthrough.  They are not a cohesive
    named concept that would survive as a value object; the year-end
    ``_ProjectionInputs`` bundle that previously carried the first four is
    the year-end package's own, and re-creating a kernel-specific bundle no
    other caller shares would be the stamp coupling the standards reject.
    Keyword-only keeps the call sites self-documenting (and exempts the
    positional-count rule).

    Args:
        account: The account to project.
        scenario: The baseline scenario.
        periods: All user pay periods.
        debt_schedule: This account's :class:`DebtSchedule` (the
            :func:`generate_debt_schedules` entry for it -- its amortization
            schedule plus the resolver's current balance), or ``None`` when
            the account is not an amortizing loan or has no resolvable
            schedule.
        investment_params: This account's
            :class:`~app.models.investment_params.InvestmentParams`, or
            ``None`` when it is not a parameterized investment account.
        deductions: This account's active paycheck deductions (the
            growth engine's contribution feed; adapted internally).
        salary_gross_biweekly: Raise-aware engine gross per pay period
            (the employer-match cap basis).
        amount_overrides: Optional ``{transaction_id: Decimal}`` live
            projected-net / loan-derive map (Workstream B).  Threaded ONLY
            through the base / cash fall-through
            (:func:`base_account_balance_map`).  The map only ever contains
            cash-account transaction ids (salary income + loan-transfer
            shadows); the investment branch's base IS a transaction sum but
            it independently builds its own live overrides inside
            ``balances_for``, and the loan / appreciation branches derive
            from schedules and growth curves -- so forwarding this cash
            override to any non-cash branch would match no id and is
            intentionally omitted.  Default ``None`` preserves the prior
            behavior byte-identical.

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
    # is membership (``is not None``), NOT truthiness: a :class:`DebtSchedule`
    # whose ``schedule`` is EMPTY (``[]`` -- a paid-off or fully-resolved loan
    # with no remaining rows) still routes to the loan path -- ``compute_loan_
    # period_balance_map`` returns its current balance for every period -- not
    # falling through to the entries-aware resolver (which would report the
    # anchor balance).  ``None`` means "not a resolved amortizing schedule for
    # this account," which correctly falls through.  Both callers pass
    # ``debt_schedules.get(account.id)``, so absent -> ``None`` -> base path,
    # present -> a :class:`DebtSchedule` -> loan path.
    if (kind is AccountProjectionKind.AMORTIZING
            and debt_schedule is not None):
        # F-21 / Commit 19: route through the shared
        # ``compute_loan_period_balance_map`` so the year-end liability
        # column and the savings-dashboard loan card consume the same
        # period-end-keyed balance derivation.  The schedule's
        # resolver-derived ``current_balance`` is the pre-first-payment
        # fallback -- NOT the original principal, which made the loan leap
        # down to its real balance the moment the first upcoming payment
        # landed (a phantom liability drop / net-worth jump).
        return compute_loan_period_balance_map(
            debt_schedule.schedule, periods, debt_schedule.current_balance,
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

    # Interest-bearing and plain accounts share the base path, and it is the
    # only branch that forwards ``amount_overrides``: the override map only
    # carries cash-account transaction ids, so no non-cash branch above would
    # match any of them (the investment base builds its own live overrides
    # inside ``balances_for``).
    return base_account_balance_map(
        account, scenario, periods, amount_overrides=amount_overrides,
    )


def account_balance_map_from_inputs(
    account: Account,
    scenario: Scenario,
    periods: list,
    inputs,
    *,
    amount_overrides: "dict[int, Decimal] | None" = None,
) -> "OrderedDict[int, Decimal] | None":
    """Unpack a per-set projection bundle for one account and dispatch.

    The ``balance_at`` seam's unpack-and-dispatch site
    (:func:`app.services.balance_at._account_balance_map` calls it for both
    the single-account and batch paths): it slices the four projection
    inputs :func:`build_account_balance_map` needs for *account* out of a
    pre-assembled bundle and calls it.  Kept here in the engine cluster,
    beside the dispatcher it feeds, so the bundle-field-to-kwarg slice rule
    lives with :func:`build_account_balance_map` rather than in the seam.
    (Until the year-end summary was rerouted through the seam its adapter
    sliced an identical bundle here too -- the R0801 the shared site
    closed; the seam is now the sole caller.)

    ``inputs`` is duck-typed: any bundle exposing ``debt_schedules``,
    ``investment_params_map``, ``deductions_by_account``, and
    ``salary_gross_biweekly`` qualifies.  The seam's
    :class:`app.services.balance_at._AssembledInputs` satisfies this
    contract.  It is intentionally left unannotated: that concrete bundle
    type lives in a consumer package this engine module must not import
    (the dependency direction), so the structural contract is documented
    here rather than expressed by a shared type.

    Args:
        account: The account to project.
        scenario: The baseline scenario.
        periods: The pay periods to project over.
        inputs: The per-set projection bundle (see the duck-typed contract
            above).
        amount_overrides: Optional ``{transaction_id: Decimal}`` live map,
            forwarded to the kernel's cash path; ``None`` (year-end and the
            net-worth batch) never applies live overrides.

    Returns:
        OrderedDict mapping period_id to Decimal balance, or None when the
        account has no anchor period.
    """
    return build_account_balance_map(
        account, scenario, periods,
        debt_schedule=inputs.debt_schedules.get(account.id),
        investment_params=inputs.investment_params_map.get(account.id),
        deductions=inputs.deductions_by_account.get(account.id, []),
        salary_gross_biweekly=inputs.salary_gross_biweekly,
        amount_overrides=amount_overrides,
    )


def investment_base_balance_map(
    account: Account,
    scenario: Scenario,
    periods: list,
) -> "OrderedDict[int, Decimal]":
    """Return an investment account's cash-basis (pre-growth) balance map.

    The transaction-sum balance an investment account holds from its
    anchor plus contributions, with NO modeled growth layered on -- the
    seed a forward growth projection compounds from.  It is the canonical
    entries-aware producer's map verbatim
    (:func:`~app.services.balance_resolver.balances_for`), so it agrees
    penny-for-penny with the figure the grid and every cash surface render
    for the same rows.

    Shared by every investment growth projection so none re-derives the
    seed: the net-worth investment sub-chain
    (:func:`_build_investment_balance_map`, which forward/reverse-projects
    growth off it), the year-end savings-progress projection
    (:func:`app.services.year_end_summary_service._savings._project_investment_for_year`,
    which re-projects each calendar year from this cash basis), and the
    investment / retirement dashboard forward projections
    (``investment_dashboard_service._resolve_seed_balance`` and
    ``retirement_dashboard_service._resolve_balance_maps``, whose growth
    chart seeds from this cash basis while the DISPLAYED headline reads the
    modeled :func:`balance_map`).  Each must seed from THIS pre-growth map,
    not the growth-modeled :func:`balance_map` the ``balance_at`` seam
    returns for an investment account -- seeding from the modeled balance
    would compound growth on top of growth (re-grow the current period).
    Exposed from the engine cluster precisely so those consumers can read
    the seed without calling the fenced cash producer directly.

    Args:
        account: The investment account.
        scenario: The baseline scenario (its id scopes the resolver).
        periods: The pay periods to span (ordered by ``period_index``;
            must include the anchor so the resolver has its running seed).

    Returns:
        The ``OrderedDict`` period_id -> Decimal cash-basis balance.
    """
    return balance_resolver.balances_for(account, scenario.id, periods).balances


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
    # the figure rendered on the grid and other surfaces.  Via the
    # shared seed accessor so the year-end savings-progress projection
    # compounds from the SAME cash basis (one definition of the seed).
    base_balances = investment_base_balance_map(account, scenario, periods)

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
