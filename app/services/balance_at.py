"""
Shekel Budget App -- Balance-at-T seam (Level 1, Option D Build-Order Step 1).

The single public way any screen obtains an account's balance over time.
Six producers historically answered "what is account A's balance at time
T?", and the three recompute-at-read kinds (loan, investment, property)
each bolted on their own rule for periods before an account's first known
data point; every new surface re-invented that boundary and shipped a bug
at least once.  This module owns all four per-kind boundary rules in ONE
place, documented and tested together (the documented-once contract):

* **PLAIN / INTEREST (cash)** -- pre-anchor periods are OMITTED.  Cash
  balances are materialized transaction sums carried forward from the
  anchor period; flat-carrying them backward would fabricate balances the
  account never had.
* **AMORTIZING (loan)** -- periods before the first scheduled payment, and
  every period of an empty / paid-off schedule, return the resolver's
  current_balance held flat -- NEVER the loan's original principal (which
  made the liability leap down the moment the first payment landed).
* **INVESTMENT** -- model-from-anchor: the anchor compounded forward at the
  assumed return (plus contributions) for post-anchor periods, and
  reverse-projected backward for pre-anchor periods.
* **APPRECIATING (property)** -- the user-set market value compounds
  forward at its annual rate; a manually-asserted valuation has no
  historical basis to compound backward from, so pre-anchor periods
  flat-carry the anchor value.

The seam does NOT reimplement any of that math.  It assembles each
account's inputs (its debt schedule, investment params, deductions, and the
engine gross-biweekly) from the shared loaders and DELEGATES the per-kind
dispatch to :func:`app.services.net_worth_kernel.build_account_balance_map`
-- the one dispatcher both the savings cockpit and the year-end summary
already build on.  Centralising the dispatch the two existing dispatchers
duplicate is the whole point: a third copy is exactly the duplication this
work exists to kill.

Dependency direction (SOLID).  Consumers (routes, savings, year-end,
dashboards) depend on this seam; the seam depends only on the engine
cluster (:mod:`~app.services.net_worth_kernel`,
:mod:`~app.services.account_projection`,
:mod:`~app.services.balance_resolver`,
:mod:`~app.services.projection_inputs`,
:mod:`~app.services.income_service`,
:mod:`~app.services.pay_period_service`) and the models.  It MUST NOT import
a consumer package (savings_dashboard_service, year_end_summary_service,
dashboards, routes).

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"):
this module imports no Flask symbol and performs no database writes.  All
money is :class:`~decimal.Decimal`; ``float`` belongs only at a route's
serialization boundary, never here.

Liability classification is NOT a balance concern: the maps this module
returns are balances only.  Consumers that need the asset-plus /
liability-minus net-worth sum add ``is_liability`` themselves (the kernel's
:func:`~app.services.net_worth_kernel.sum_net_worth_at_period` already
owns that rule).
"""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.scenario import Scenario
from app.services import (
    balance_resolver,
    income_service,
    net_worth_kernel,
    pay_period_service,
)
from app.services.account_projection import (
    AccountProjectionKind,
    balance_from_schedule_at_date,
    classify_account,
    find_period_containing_date,
)
from app.services.projection_inputs import (
    load_active_deductions_for_accounts,
    load_investment_params_for_accounts,
)
from app.utils.money import round_money

ZERO = Decimal("0")

# The two kinds whose balances are materialized transaction sums (cash):
# PLAIN (checking / savings) and INTEREST (HYSA / Money Market / CD / HSA).
# :func:`balance_at` routes them to the date-precise cash producer
# (:func:`~app.services.balance_resolver.balance_as_of_date`); the loan and
# investment / property kinds are period-granular (their model is
# period-keyed) and route through :func:`balance_map` instead.  Named here
# (not a bare two-branch ``or``) so the "these are the cash kinds" decision
# is explicit and single-sourced within the seam.
_CASH_KINDS = frozenset({
    AccountProjectionKind.PLAIN,
    AccountProjectionKind.INTEREST,
})


@dataclass(frozen=True)
class _AssembledInputs:
    """The batch-loaded per-account projection inputs for a set of accounts.

    Bundles the four shared-loader outputs that
    :func:`app.services.net_worth_kernel.build_account_balance_map`
    dispatches on -- the amortization schedules, the investment-params map,
    the per-account deductions, and the engine gross-biweekly -- so the
    single-account (:func:`balance_map`) and batch (:func:`build_maps`)
    entry points assemble them through ONE helper (:func:`_assemble_inputs`)
    and dispatch through ONE helper (:func:`_account_balance_map`).  Mirrors
    the year-end package's ``_ProjectionInputs`` and the savings package's
    ``_AccountParams``; kept seam-local (not shared with either consumer) so
    each surface still owns its own assembly contract.

    Attributes:
        debt_schedules: account_id ->
            :class:`~app.services.net_worth_kernel.DebtSchedule` for the
            amortizing-loan subset (its schedule plus the resolver's current
            balance).  Non-loan accounts are absent.
        investment_params_map: account_id ->
            :class:`~app.models.investment_params.InvestmentParams` for the
            accounts the canonical classifier marks INVESTMENT.  A
            params-less investment, and every non-investment account, is
            absent (callers use ``dict.get``).
        deductions_by_account: account_id -> list of active paycheck
            deductions, loaded ONLY for accounts in
            ``investment_params_map`` (see :func:`_assemble_inputs`).
        salary_gross_biweekly: The raise-aware engine gross per pay period
            (the employer-match cap basis), shared by every investment in
            the set.
    """

    debt_schedules: dict[int, net_worth_kernel.DebtSchedule]
    investment_params_map: dict[int, InvestmentParams]
    deductions_by_account: dict[int, list]
    salary_gross_biweekly: Decimal


def _assemble_inputs(
    accounts: list[Account], scenario: Scenario,
) -> _AssembledInputs:
    """Batch-load the per-account projection inputs ONCE for *accounts*.

    The single assembly point shared by :func:`balance_map` (called with a
    one-element list) and :func:`build_maps` (called with the whole set), so
    single- and batch-assembly run identical loader logic and preserve the
    N+1 avoidance: one :func:`~app.services.net_worth_kernel.generate_debt_schedules`
    over the amortizing-loan subset, one investment-params query, one
    deductions query, and one raise-aware gross fetch for the whole set.

    The four loaders are the shared building blocks the savings cockpit's
    ``_load_account_params`` and the year-end summary already use -- this
    seam reuses them rather than writing new inline param queries.

    Args:
        accounts: The accounts to assemble inputs for, each with its
            ``account_type`` relationship available for the classifier.  An
            empty list returns an empty bundle without issuing any query.
        scenario: The baseline scenario (its id scopes the loan resolver's
            payment history).

    Returns:
        The :class:`_AssembledInputs` bundle.
    """
    if not accounts:
        return _AssembledInputs(
            debt_schedules={},
            investment_params_map={},
            deductions_by_account={},
            salary_gross_biweekly=ZERO,
        )

    # Every account in the set is owned by one user (the caller's), so the
    # user id for the deductions / gross loaders comes off any of them.
    user_id = accounts[0].user_id

    # Amortizing loans drive the schedule path; resolve their schedules
    # once.  ``generate_debt_schedules`` returns an empty map for an empty
    # subset, so a no-loan set issues no resolver work.
    loan_accounts = [
        account for account in accounts
        if classify_account(account) is AccountProjectionKind.AMORTIZING
    ]
    debt_schedules = net_worth_kernel.generate_debt_schedules(
        loan_accounts, scenario.id,
    )

    # The shared loader owns the canonical-classifier filter, so a
    # parameterised physical asset (Property -> APPRECIATING) is correctly
    # excluded here rather than re-derived by elimination.
    investment_params_map = load_investment_params_for_accounts(accounts)

    # Deduction-scoping rule (mirrors savings ``_load_account_params``):
    # load deductions ONLY for the investment accounts that HAVE an
    # InvestmentParams row.  ``build_account_balance_map`` feeds deductions
    # to the growth engine ONLY for an INVESTMENT account whose
    # ``investment_params`` is not None, so deductions for a params-less
    # account are never consumed -- scoping to the params map's keys is the
    # canonical rule that keeps this seam, savings, and year-end in
    # agreement on which accounts get a deduction feed.
    deductions_by_account = (
        load_active_deductions_for_accounts(
            user_id, list(investment_params_map.keys()),
        ) if investment_params_map else {}
    )

    salary_gross_biweekly = income_service.get_current_gross_biweekly(user_id)

    return _AssembledInputs(
        debt_schedules=debt_schedules,
        investment_params_map=investment_params_map,
        deductions_by_account=deductions_by_account,
        salary_gross_biweekly=salary_gross_biweekly,
    )


def _account_balance_map(
    account: Account,
    scenario: Scenario,
    periods: list,
    inputs: _AssembledInputs,
    amount_overrides: "dict[int, Decimal] | None",
) -> "OrderedDict[int, Decimal] | None":
    """Dispatch ONE account's per-period balance map from *inputs*.

    The seam's single dispatch site, shared by :func:`balance_map` and
    :func:`build_maps`.  Delegates to the shared
    :func:`app.services.net_worth_kernel.account_balance_map_from_inputs`,
    which unpacks the bundle for *account* and calls the kernel's per-kind
    dispatcher -- the same unpack the year-end adapter's
    ``_dispatch_account_balance_map`` runs, hoisted into the engine cluster
    so the two cannot drift (R0801).  The seam never re-implements the
    classify ladder; it supplies this account's assembled inputs.

    Args:
        account: The account to project.
        scenario: The baseline scenario.
        periods: The pay periods to project over (the output domain).
        inputs: The :class:`_AssembledInputs` bundle for the account's set.
        amount_overrides: Optional ``{transaction_id: Decimal}`` live map,
            forwarded to the kernel's cash path; ``None`` for the net-worth
            batch path, which never applies live overrides.

    Returns:
        The OrderedDict period_id -> Decimal balance, or ``None`` when the
        account has no anchor period (the kernel's own no-anchor contract).
    """
    return net_worth_kernel.account_balance_map_from_inputs(
        account, scenario, periods, inputs, amount_overrides=amount_overrides,
    )


def balance_map(
    account: Account,
    scenario: Scenario,
    periods: list,
    *,
    amount_overrides: "dict[int, Decimal] | None" = None,
) -> "OrderedDict[int, Decimal] | None":
    """Return one account's period_id -> balance map across *periods*.

    The single-account per-period producer.  Assembles THIS account's
    inputs via the shared :func:`_assemble_inputs` (its debt schedule when
    it is an amortizing loan, its investment params, its deductions when it
    has params, and the engine gross-biweekly) and delegates the per-kind
    dispatch to the kernel via :func:`_account_balance_map` -- the same code
    path :func:`build_maps` runs per account, so single- and batch-assembly
    cannot drift.

    ``amount_overrides`` passes straight through to the kernel's cash path
    (and there to :func:`~app.services.balance_resolver.balances_for`) for
    grid parity; it is NOT auto-applied, so a non-grid caller's behavior is
    unchanged.  The loan / investment / appreciation kinds ignore it -- the
    override map only carries cash-account transaction ids, so it matches
    nothing off the cash path.

    Args:
        account: The account to project.  Its ``user_id`` scopes the
            deduction / gross loaders; its ``account_type`` drives the
            classifier.
        scenario: The baseline scenario.
        periods: The pay periods to project over, ordered by
            ``period_index``.
        amount_overrides: Optional ``{transaction_id: Decimal}`` live
            projected-net / loan-derive map (the grid threads its pre-built
            map here).  Default ``None`` lets the cash producer build its
            own live overrides, byte-identical to the prior behavior.

    Returns:
        The OrderedDict period_id -> Decimal balance, or ``None`` when the
        account has no anchor period.

    Raises:
        ValueError: When ``scenario`` is None -- callers that resolve a
            nullable baseline must guard first.
    """
    # Rerouted callers (e.g. ``build_account_net_worth_maps``) keep their own
    # ``if scenario is None: return []`` guard, so the legitimate empty state
    # is preserved; the seam raising here is the defensive contract that turns
    # a deep AttributeError (or a silent $0 net worth) into a clear failure.
    if scenario is None:
        raise ValueError(
            "balance_at requires a baseline scenario; resolve via "
            "get_baseline_scenario and guard None before calling"
        )
    inputs = _assemble_inputs([account], scenario)
    return _account_balance_map(
        account, scenario, periods, inputs, amount_overrides,
    )


def build_maps(
    accounts: list[Account],
    scenario: Scenario,
    periods: list,
) -> "dict[int, OrderedDict[int, Decimal]]":
    """Return account_id -> period balance map for many accounts (batch).

    The batch producer that preserves the existing N+1 avoidance: it
    assembles ALL inputs ONCE via :func:`_assemble_inputs` (one debt-schedule
    generation over the loan subset, one investment-params query, one
    deductions query, one gross fetch for the whole set), then loops the
    shared :func:`_account_balance_map` per account.  This is the per-account
    dense-map build the savings cockpit's ``build_account_net_worth_maps``
    performs today, internalised behind the seam so the assembly lives in
    one place.

    The net-worth batch path never applies live amount overrides, so each
    per-account dispatch passes ``amount_overrides=None``.

    Accounts whose map is ``None`` (no anchor period) are omitted from the
    result, matching the net-worth section's ``balances is None`` skip.

    Args:
        accounts: The accounts to project (the same user's active set).
        scenario: The baseline scenario.
        periods: The pay periods to project over (the dense domain -- pass
            ALL of the user's periods so the cash / investment paths have
            their anchor seed).

    Returns:
        A dict mapping ``account.id`` to its OrderedDict period_id ->
        Decimal balance map, for every account that has a map.

    Raises:
        ValueError: When ``scenario`` is None -- callers that resolve a
            nullable baseline must guard first.
    """
    if scenario is None:
        raise ValueError(
            "balance_at requires a baseline scenario; resolve via "
            "get_baseline_scenario and guard None before calling"
        )
    inputs = _assemble_inputs(accounts, scenario)
    result: "dict[int, OrderedDict[int, Decimal]]" = {}
    for account in accounts:
        balances = _account_balance_map(
            account, scenario, periods, inputs, None,
        )
        if balances is None:
            continue
        result[account.id] = balances
    return result


def balance_at(
    account: Account, scenario: Scenario, as_of: date,
) -> Decimal:
    """Return one account's balance as of a calendar date *as_of*.

    The scalar-at-a-date producer, dispatched by
    :func:`~app.services.account_projection.classify_account`:

    * **PLAIN / INTEREST (cash)** -> the date-precise
      :func:`~app.services.balance_resolver.balance_as_of_date`, which owns
      its own period loading and intra-period entry-date precision.
    * **AMORTIZING (loan)** -> the loan's resolver schedule via
      :func:`~app.services.account_projection.balance_from_schedule_at_date`
      (the remaining balance after the last payment on or before *as_of*,
      falling back to the resolver current_balance before the first
      payment).  When the loan has no resolvable schedule (the resolver
      returned nothing -- e.g. a loan with no anchor event yet), it falls
      back to :func:`~app.services.balance_resolver.balance_as_of_date` over
      the loan's own rows, the same generic degrade any account would get.
    * **INVESTMENT / APPRECIATING** -> the value of :func:`balance_map` at
      the period containing *as_of* (these kinds are period-granular: their
      model is period-keyed, so a date resolves to its period).

    Granularity note: cash and loan are DATE-precise -- cash sums dated rows
    up to *as_of*, and the loan walks its amortization schedule to the exact
    *as_of* date.  INVESTMENT / APPRECIATING are period-granular: they answer
    "what is the balance at the end of the period containing *as_of*?"  This
    matches how each kind is actually stored.

    Out-of-range / no-map behavior (INVESTMENT / APPRECIATING): when *as_of*
    falls before the user's entire pay-period horizon (no period contains or
    precedes it) or the account has no projectable map, the seam returns the
    canonical anchor balance from
    :func:`~app.services.balance_resolver.resolve_anchor`, rounded to cents.
    This mirrors :func:`~app.services.balance_resolver.balance_as_of_date`'s
    pre-anchor convention (a date the projection cannot reach returns the
    anchor balance), so every kind answers an unreachable date the same way.
    A genuinely corrupt account with no anchor history makes
    ``resolve_anchor`` raise, which is the correct loud failure rather than
    a silently wrong number.

    Args:
        account: The account to value.
        scenario: The baseline scenario (its id scopes the resolver / loan
            schedule).
        as_of: The calendar date to value the account at.

    Returns:
        The ``Decimal`` balance at *as_of*.

    Raises:
        ValueError: When ``scenario`` is None -- callers that resolve a
            nullable baseline must guard first.
    """
    if scenario is None:
        raise ValueError(
            "balance_at requires a baseline scenario; resolve via "
            "get_baseline_scenario and guard None before calling"
        )
    kind = classify_account(account)

    if kind in _CASH_KINDS:
        return balance_resolver.balance_as_of_date(account, scenario.id, as_of)

    if kind is AccountProjectionKind.AMORTIZING:
        debt_schedule = net_worth_kernel.generate_debt_schedules(
            [account], scenario.id,
        ).get(account.id)
        if debt_schedule is not None:
            # Returned verbatim (no re-round): the schedule rows and
            # current_balance are already cent-quantized by the resolver, so
            # balance_at agrees penny-exact with balance_map for the period
            # containing as_of.
            return balance_from_schedule_at_date(
                debt_schedule.schedule, as_of, debt_schedule.current_balance,
            )
        # No resolvable schedule: degrade to the cash producer over the
        # loan's own transaction rows (documented above).
        return balance_resolver.balance_as_of_date(account, scenario.id, as_of)

    # INVESTMENT / APPRECIATING: locate the period containing as_of and read
    # the period-keyed map's value there.
    periods = pay_period_service.get_all_periods(account.user_id)
    balances = balance_map(account, scenario, periods)
    target_period = find_period_containing_date(periods, as_of)
    if balances is not None and target_period is not None:
        located = balances.get(target_period.id)
        if located is not None:
            # Returned verbatim (no re-round): the growth / appreciation end
            # balances are already cent-quantized from round_money'd
            # components, so balance_at == balance_map[period] penny-exact.
            return located

    # as_of precedes the user's pay-period horizon, or the account has no
    # projectable map: fall back to the canonical anchor balance.
    return round_money(
        balance_resolver.resolve_anchor(account, scenario.id).balance,
    )
