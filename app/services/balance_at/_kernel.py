"""
Shekel Budget App -- Net-Worth Kernel (shared per-account balance chain).

The single, Flask-free home for the per-account balance-map projection
chain the ``balance_at`` seam dispatches through.  Promoted out of
``year_end_summary_service._balances`` (Loop B Phase 1) when the year-end
summary and the savings cockpit computed net worth from two copies of the
same math that could drift; the year-end consumer has since been deleted
(plan step F2), so what remains here is ONE dispatch (interest calculator /
investment growth engine / appreciation growth curve / canonical
entries-aware resolver) plus the investment forward/reverse growth
sub-chain.  AMORTIZING loans are NOT dispatched here: the seam reads its own
``positions()``-based map for them (plan step C3b3).

The cockpit's forward net-worth trend PROJECTS investment and retirement
growth forward, so the investment growth sub-chain lives here too (the
SCOPE B move locked 2026-06-24), not just the plain balance dispatch.

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"):
this module imports no Flask symbol and performs no database writes.  It
reads through the same ORM session the callers already opened.  All money
is :class:`~decimal.Decimal`; ``float`` belongs only at a route's Chart.js
serialization boundary, never here.

The public producers take loose, per-account parameters (the single
account's :class:`~app.models.investment_params.InvestmentParams`, its
adapted deductions, and the engine gross-biweekly) rather than a caller's
bundle, so a consumer need not construct a value object to call the kernel.
:func:`account_balance_map_from_inputs` is the one entry that DOES take a
bundle, duck-typed, and it slices it into those loose parameters here --
beside the dispatcher it feeds.
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
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.loan_resolution import ResolvedLoan
from app.utils.balance_predicates import account_period_scope_clause

from ._context import BalanceContext
from . import _calculator, _cash_engine, _investment

# The anchor-balance fallback for an account whose ``current_anchor_balance``
# is NULL -- unquantized on purpose, so it imposes no exponent on the walk it
# seeds.  Money RETURNED to a caller is cent-quantized by the producer that
# computes it.  (It seeded the kernel's net-worth sum too, until that reducer
# was deleted as dead code -- the live net-worth reduction lives with its
# consumer in ``savings_dashboard_service._net_worth``.)
ZERO = Decimal("0")


def load_account_period_transactions(
    account_id: int,
    scenario_id: int,
    period_ids: list[int],
) -> list[Transaction]:
    """Return one account's non-deleted scenario transactions over periods.

    The account/scenario/period transaction query behind the
    interest-layered walk (:func:`_account_interest_projection`, its only
    caller today).  It selects EVERY non-deleted row for the account in the
    period span -- unlike
    :func:`~app.services.cash_ledger.load_balance_transactions`, which
    additionally drops Credit / Cancelled rows -- because the interest
    accrual downstream applies its own status logic and needs the full row
    set.  ``Transaction.status`` is ``lazy="joined"`` on the model, so a
    consumer reads ``txn.status.is_settled`` off these rows without an N+1
    and without an explicit eager-load.

    It was shared with the year-end summary's interest and settled-net
    helpers until plan step F2 deleted that package, which is why it is a
    named function rather than an inline query.

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


@dataclass(frozen=True)
class DebtSchedule:
    """Everything the FORWARD projection needs to value one loan at any date.

    The outputs of ONE resolution
    (:meth:`~app.services.balance_at.BalanceContext.resolved_loan`), bundled so
    the schedule, its seed, and the loan's origination cannot come from
    different places and drift.

    Attributes:
        schedule: The loan's :class:`AmortizationRow` list (today-forward:
            confirmed-history rows plus committed forward rows).  May be empty
            for a fully-resolved / paid-off loan.
        projection_seed: The balance the forward projection STARTS from -- the
            balance in effect before the first unconfirmed row.  See
            :func:`_projection_seed`.  It is NOT "what is owed now": for an
            upcoming mortgage the loan owes ``0.00`` today and the projection
            must still start from its opening balance once it closes.  The two
            coincide for every live loan, which is why one field served both jobs
            and why the old name was a lie.  Read a balance-at-T from the
            ``balance_at`` seam.
        owed_from: The loan's ``origination_date``.  A loan owes nothing before
            it exists, and the forward plan fold enforces that
            (:func:`app.services.balance_at._plan.fold_forward` returns ``0.00``
            for a date before ``owed_from``).
    """

    schedule: list
    projection_seed: Decimal
    owed_from: date


def generate_debt_schedules(
    debt_accounts: list,
    ctx: "BalanceContext",
) -> dict[int, "DebtSchedule"]:
    """Return each debt account's :class:`DebtSchedule` from the pass's resolutions.

    Projects the read pass's memoized loan resolutions
    (:meth:`~app.services.balance_at.BalanceContext.resolved_loan`) into the narrow
    ``(schedule, projection_seed, owed_from)`` bundle the balance dispatcher
    needs.
    Same resolver output the loan dashboard and the /savings debt card consume,
    so mortgage interest, debt progress, and net-worth liability all derive from
    ONE resolution per loan (E-18 / Commit 15).

    It no longer resolves anything itself, and that is the point.  It used to
    call the resolver per account against its own ``date.today()`` -- so the five
    surfaces that called it in a single ``/savings`` render each re-resolved
    every loan, against five independently-read clocks.  Now the context owns
    both the clock and the resolution, so calling this twice in one pass costs
    one dict comprehension, not two amortization walks
    (``docs/audits/balance_architecture/followup_redundant_loan_resolution.md``).

    Args:
        debt_accounts: The amortizing loan accounts to bundle.  An account the
            context cannot resolve (no ``LoanParams`` -- not a configured loan)
            is absent from the result, and the caller's per-kind dispatch then
            falls through to its non-loan path.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (it pins the scenario and the as-of, and memoizes each resolution).

    Returns:
        dict mapping account_id to :class:`DebtSchedule`.
    """
    schedules: dict[int, DebtSchedule] = {}
    for account in debt_accounts:
        resolved = ctx.resolved_loan(account)
        if resolved is None:
            continue
        origination = resolved.params.origination_date
        schedules[account.id] = DebtSchedule(
            schedule=resolved.state.schedule,
            projection_seed=_projection_seed(resolved, ctx.as_of),
            owed_from=origination,
        )
    return schedules


def _projection_seed(resolved: ResolvedLoan, as_of: date) -> Decimal:
    """Return the balance the loan's forward projection starts from.

    See :attr:`DebtSchedule.projection_seed` for the contract.  The fork is the
    loan's own existence:

    * **Originated by *as_of*** -- the resolver's ``current_balance``: the
      ledger-confirmed present, which is what the projection amortizes down.
    * **NOT originated yet** -- the resolver correctly reports ``0.00`` owed (the
      loan does not exist), but the projection still has to know what it will owe
      the day it closes.  That is the loan's OPENING ANCHOR balance -- the same
      fact the genesis walk posts as the ``loan_opening``
      (:func:`app.services.loan_loaders._opening_anchor_fact`).  ONE fact, two
      readers, split on the boundary the architecture turns on: the ledger owns the
      origination once it has HAPPENED, the projection until it does.

    Sourced from the opening anchor and NEVER from the raw
    ``params.original_principal`` column, deliberately.  The two are equal for a
    loan that has not originated (nothing can supersede an origination that has
    not happened), but keeping ONE definition of "the balance this loan opens at"
    keeps a not-yet-originated loan's OPENING from being confused with an EXISTING
    loan's balance: reporting an existing loan's balance AS its origination amount
    is a different, wrong statement (the F-21 / PR #44 defect), and this is the one
    controlled path the seed reaches the forward fold through, so that confusion
    has no call site to recur at.

    Args:
        resolved: The pass's :class:`~app.services.loan_resolution.ResolvedLoan`.
        as_of: The read pass's as-of (the resolver's NOW).

    Returns:
        The projection's seed as a ``Decimal``.
    """
    if resolved.params.origination_date <= as_of:
        return resolved.state.current_balance
    opening = next(
        fact for fact in resolved.anchor_facts if fact.is_opening
    )
    return opening.anchor_balance


def debt_schedule_rows(
    debt_accounts: list,
    ctx: "BalanceContext",
) -> dict[int, list]:
    """Return each debt account's amortization ROWS -- no balance attached.

    The accessor every out-of-cluster consumer of the loan schedules reads,
    instead of :func:`generate_debt_schedules`.  They want the
    :class:`AmortizationRow` list -- today, the net-worth trend's
    honest-history gate needs a first-payment date -- and none of them wants a
    balance.  (The year-end and Schedule A interest hybrids read it too until
    plan steps F2 / C3c folded them onto the balance seam.)

    Handing them rows rather than the :class:`DebtSchedule` bundle is what keeps
    a balance out of an out-of-cluster consumer's hands.  W9906 binds on function
    NAMES: it flags a consumer that CALLS a balance producer.  It cannot see an
    ATTRIBUTE read, and ``DebtSchedule.projection_seed`` is a loan balance.  So
    while any consumer could call :func:`generate_debt_schedules`, one line --
    ``schedules[account.id].projection_seed`` in a template context -- would put a
    balance on a screen without passing the seam, with every gate silent
    (``docs/audits/balance_architecture/followup_debt_schedule_attribute_fence.md``).
    The bundle exists precisely so the forward projection CAN seed from a balance;
    that made it a loaded gun whose safety was a docstring.  The rows carry no
    seed, so a consumer that wants a balance has no choice but
    ``balance_at.balance_at`` -- which
    is the point.  :func:`generate_debt_schedules` is fenced as a producer now,
    and its remaining callers are all inside the cluster.

    Args:
        debt_accounts: The amortizing loan accounts whose rows to return.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (each loan is resolved at most once for the pass).

    Returns:
        ``{account_id: [AmortizationRow, ...]}`` -- the loan's schedule
        (confirmed history plus committed forward rows).  A loan the context
        cannot resolve (no ``LoanParams``) is absent, matching
        :func:`generate_debt_schedules`.
    """
    return {
        account_id: schedule.schedule
        for account_id, schedule in generate_debt_schedules(
            debt_accounts, ctx,
        ).items()
    }


def _account_interest_projection(
    account: Account,
    scenario: Scenario,
    periods: list,
    interest_params: InterestParams,
    amount_overrides: "dict[int, Decimal] | None" = None,
) -> "tuple[OrderedDict[int, Decimal], dict[int, Decimal]]":
    """Run the interest-layered balance walk for one account.

    The single home for the "load this account's transactions and run
    :func:`~app.services.balance_at._calculator.calculate_balances_with_interest`
    over them" sequence shared by the interest BALANCE path
    (:func:`base_account_balance_map`, which keeps the balances and
    discards the interest) and the interest-EARNED accessor
    (:func:`interest_by_period_for_account`, which keeps the interest and
    discards the balances).  Folding the two into one helper keeps the
    transaction-scope query, the anchor-balance coalesce, and the
    calculator kwargs identical between the balance figure a screen renders
    and the interest figure the account-detail chip reports -- they cannot
    drift onto two copies of the same walk (R0801).

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
            forwarded verbatim to the calculator; ``None`` (the
            interest-earned path) preserves the stored-amount behavior.

    Returns:
        ``(balances, interest_by_period)`` -- the calculator's two outputs
        verbatim: the period_id -> Decimal end-balance map (interest
        layered in) and the period_id -> Decimal interest-earned map.
    """
    transactions = load_account_period_transactions(
        account.id, scenario.id, [p.id for p in periods],
    )
    anchor_balance = account.current_anchor_balance or ZERO
    return _calculator.calculate_balances_with_interest(
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
            (:func:`~app.services.balance_at._calculator.calculate_balances_with_interest`
            for the interest path,
            :func:`~app.services.balance_at._cash_engine.balances_for` for the
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
    # ``cash_ledger._amounts._entry_aware_amount`` was closed in Commit 5
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
    return _cash_engine.balances_for(
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

    The engine-cluster accessor the account-detail page's "Interest, next
    12 mo" chip (``app.routes.accounts.detail``, its only caller) reads
    instead of calling
    :func:`~app.services.balance_at._calculator.calculate_balances_with_interest`
    directly: interest EARNED is rich projection detail, not a
    balance-at-T figure, so it is not a ``balance_at`` seam concern, yet
    the producer that computes it is fenced to the engine cluster.  This
    accessor keeps that producer call inside the kernel (where it belongs
    with :func:`base_account_balance_map`, which shares the same
    :func:`_account_interest_projection` walk) while the consumer sees only
    the interest map it needs.  (Its original caller was the year-end
    savings-progress section, deleted at plan step F2.)

    A None-anchor account earns no projectable interest (the walk produces
    no balances to layer interest on), returned as the empty map so the
    caller's windowed sum (``_interest_next_year``, a rolling next-12-months
    window -- not a calendar year) is ``Decimal("0")`` -- matching the prior
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
    ctx: "BalanceContext",
    periods: list,
    *,
    investment_params: InvestmentParams | None,
    deductions: list,
    salary_gross_biweekly: Decimal,
    amount_overrides: "dict[int, Decimal] | None" = None,
) -> "OrderedDict[int, Decimal] | None":
    """Compute period_id -> balance for one NON-loan account, dispatching on type.

    The net-worth path for every kind EXCEPT amortizing loans.  Dispatches to
    the correct calculation engine:

    - Investment (401k, IRA, etc.): the growth engine, fed by this
      account's ``investment_params`` plus its ``deductions`` and the
      engine ``salary_gross_biweekly``.
    - Appreciating physical assets (Property): the appreciation growth curve.
    - Interest-bearing and everything else: the shared
      :func:`base_account_balance_map`.

    **AMORTIZING loans are dispatched by the seam, not here** (plan step C3b3):
    the seam's :func:`app.services.balance_at._account_balance_map` reads its own
    positions()-based per-period map for a loan, because that producer sits ABOVE
    this kernel and the kernel cannot import it back.  A loan therefore never
    reaches this dispatcher through the seam; every branch below is non-loan.

    Takes loose per-account parameters (this account's params, deductions, and
    the engine gross-biweekly) rather than the savings package's projection
    bundle, so a caller need not construct that value object.

    Pylint: ``too-many-arguments`` (7/5) -- the keyword-only group is this
    account's three independent projection inputs (its investment params, its
    deductions, the engine gross-biweekly) plus the cash-path
    ``amount_overrides`` passthrough.  They are not a cohesive named concept
    that would survive as a value object, and re-creating a kernel-specific
    bundle no other caller shares would be the stamp coupling the standards
    reject.  Keyword-only keeps the call sites self-documenting (and exempts the
    positional-count rule).

    Args:
        account: The account to project.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
            Only its ``scenario`` is read here -- every remaining branch is a
            leaf producer with no clock -- so the branches take ``ctx.scenario``.
            The whole context is threaded (rather than a bare ``scenario``)
            because the seam's :func:`account_balance_map_from_inputs` holds it
            and the loan arm that DID need the clock has moved to the seam.
        periods: All user pay periods.
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
            ``balances_for``, and the appreciation branch derives from a
            growth curve -- so forwarding this cash override to any non-cash
            branch would match no id and is intentionally omitted.  Default
            ``None`` preserves the prior behavior byte-identical.

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

    # AMORTIZING loans are NOT dispatched here: the seam
    # (:func:`app.services.balance_at._account_balance_map`) reads its own
    # positions()-based per-period map for them (plan step C3b3), because that
    # producer sits ABOVE this kernel and this module cannot import it back.  A
    # loan therefore never reaches this dispatcher through the seam; the branches
    # below are cash / interest / investment / appreciation only.

    # Investment accounts: use the growth engine.  The base balance
    # feeding the projection comes from the canonical entries-aware
    # producer (E-25 / CRIT-01 / R-1).  The investment growth sub-chain
    # (``_investment``, extracted at the module-size ceiling) composes this
    # kernel's ``investment_base_balance_map`` seed; it is a plain sibling
    # import now that both live in the seam package (it imports nothing back,
    # so there is no cycle -- the reason the old cross-module lazy import is
    # gone, plan step D1d).
    if kind is AccountProjectionKind.INVESTMENT and investment_params is not None:
        return _investment.build_investment_balance_map(
            account, investment_params, ctx.scenario, periods,
            deductions, salary_gross_biweekly,
        )

    # Appreciating physical assets (Property): the user-set market value
    # compounds forward at its annual rate.  The rate rides on the
    # account's eager ``asset_appreciation_params`` backref, so no new
    # dispatch kwarg is needed; the helper flat-carries when the params
    # row is absent.  Same ``_investment`` sibling as the investment branch.
    if kind is AccountProjectionKind.APPRECIATING:
        return _investment.build_appreciation_balance_map(
            account, ctx.scenario, periods,
        )

    # Interest-bearing and plain accounts share the base path, and it is the
    # only branch that forwards ``amount_overrides``: the override map only
    # carries cash-account transaction ids, so no non-cash branch above would
    # match any of them (the investment base builds its own live overrides
    # inside ``balances_for``).
    return base_account_balance_map(
        account, ctx.scenario, periods, amount_overrides=amount_overrides,
    )


def account_balance_map_from_inputs(
    account: Account,
    ctx: "BalanceContext",
    periods: list,
    inputs,
    *,
    amount_overrides: "dict[int, Decimal] | None" = None,
) -> "OrderedDict[int, Decimal] | None":
    """Unpack a per-set projection bundle for one account and dispatch.

    The ``balance_at`` seam's unpack-and-dispatch site for NON-loan accounts
    (:func:`app.services.balance_at._account_balance_map` calls it for both
    the single-account and batch paths, after routing amortizing loans to its
    own positions()-based map): it slices the three projection inputs
    :func:`build_account_balance_map` needs for *account* out of a pre-assembled
    bundle and calls it.  Kept here in the engine cluster, beside the dispatcher
    it feeds, so the bundle-field-to-kwarg slice rule lives with
    :func:`build_account_balance_map` rather than in the seam.  The seam is
    its sole caller.  (The year-end summary's adapter sliced an identical
    bundle here too -- the R0801 this shared site closed -- until that package
    was deleted at plan step F2.)

    ``inputs`` is duck-typed: any bundle exposing ``investment_params_map``,
    ``deductions_by_account``, and ``salary_gross_biweekly`` qualifies (the
    bundle's ``debt_schedules`` is read by the seam's loan arm, not here).  The
    seam's
    :class:`app.services.balance_at._AssembledInputs` satisfies this
    contract.  It is intentionally left unannotated: that concrete bundle
    type lives in a consumer package this engine module must not import
    (the dependency direction), so the structural contract is documented
    here rather than expressed by a shared type.

    Args:
        account: The account to project.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        periods: The pay periods to project over.
        inputs: The per-set projection bundle (see the duck-typed contract
            above).
        amount_overrides: Optional ``{transaction_id: Decimal}`` live map,
            forwarded to the kernel's cash path; ``None`` (the net-worth
            batch) never applies live overrides.

    Returns:
        OrderedDict mapping period_id to Decimal balance, or None when the
        account has no anchor period.
    """
    return build_account_balance_map(
        account, ctx, periods,
        investment_params=inputs.investment_params_map.get(account.id),
        deductions=inputs.deductions_by_account.get(account.id, []),
        salary_gross_biweekly=inputs.salary_gross_biweekly,
        amount_overrides=amount_overrides,
    )
