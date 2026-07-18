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
)
from app.services.account_projection import (
    ZERO_MONEY,
    AccountProjectionKind,
    classify_account,
    compute_forward_loan_period_balance_map,
    splice_confirmed_and_projected_loan_balances,
)
from app.services.loan_resolution import ResolvedLoan
from app.services.posting_reads import LoanLedgerNotOpenedError
from app.services.resolution_context import BalanceContext
from app.utils.balance_predicates import account_period_scope_clause

# The identity for the net-worth SUMS below -- unquantized on purpose, so it
# imposes no exponent on what it accumulates.  Money RETURNED to a caller is
# cent-quantized: that is ``ZERO_MONEY`` (imported), what the balance maps use.
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
    :func:`~app.services.balance_resolver.load_balance_transactions`,
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
    """Everything the FORWARD projection needs to value one loan at any date.

    The outputs of ONE resolution
    (:meth:`~app.services.resolution_context.BalanceContext.loan`), bundled so
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
            it exists, and both forward producers enforce that from here
            (:func:`app.services.account_projection._projected_owed_at`).
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
    (:meth:`~app.services.resolution_context.BalanceContext.loan`) into the narrow
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
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`
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
    is what stops the W9905 ``shekel-original-principal-as-balance`` rule from
    being quietly re-litigated at a call site: that checker exists because an
    EXISTING loan's balance was reported as its origination amount, which is a
    different statement from this one and must stay obviously different.

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
    instead of :func:`generate_debt_schedules`.  They all want the same thing --
    the :class:`AmortizationRow` list, for a first-payment date (the net-worth
    trend's honest-history gate) or a yearly interest sum (the year-end and
    Schedule A mortgage-interest hybrids) -- and none of them wants a balance.

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
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`
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



def _loan_ledger_not_opened(
    account: Account, ctx: "BalanceContext",
) -> "LoanLedgerNotOpenedError":
    """Build the fail-loud error for an originated loan with no OPENING posting.

    Used by the per-period map (:func:`_build_amortizing_balance_map`), the last
    balance producer still reading the POSTING ledger for a loan's past.  The
    scalar cut over to the fold (:func:`app.services.balance_at.positions`, step
    C3b), which answers such a loan from its SOURCE facts and so does not raise;
    the map raises here until its own cutover, keeping the corruption named in one
    place while it still reads the postings.

    Args:
        account: The broken loan account.
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`
            (names the scenario the opening is missing from).

    Returns:
        The :class:`~app.services.posting_reads.LoanLedgerNotOpenedError` to raise.
    """
    return LoanLedgerNotOpenedError(
        f"loan account {account.id} ({account.name!r}) has originated but has no "
        f"OPENING posting in scenario {ctx.scenario.id}: its genesis ledger was "
        f"never written, so its balance history cannot be read.  Refusing to "
        f"answer from the amortization schedule, whose rows are payments the "
        f"borrower was SUPPOSED to make, not ones they did.  Repair with "
        f"loan_posting_service.sync_loan_postings_all_scenarios({account.id})."
    )



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
    ctx: "BalanceContext",
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
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`.
            Its ``scenario`` scopes every branch's producer; its ``as_of`` is the
            confirmed/projected boundary the AMORTIZING branch splices on.  Only
            the loan branch needs the whole context -- the cash / investment /
            appreciation producers are leaves with no clock and no loan of their
            own, so they take ``ctx.scenario`` and keep their existing contract.
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
        # Genesis per-period read switch (plan Section 9): confirmed ledger
        # for begun periods, re-seeded projection after (see the helper).
        return _build_amortizing_balance_map(
            account, ctx, periods, debt_schedule,
        )

    # Investment accounts: use the growth engine.  The base balance
    # feeding the projection comes from the canonical entries-aware
    # producer (E-25 / CRIT-01 / R-1).  The investment growth sub-chain was
    # extracted to ``net_worth_investment`` (module-size ceiling); it composes
    # this kernel's ``investment_base_balance_map`` seed, so the dispatch is a
    # call-time import (the sub-chain imports back from here).
    if kind is AccountProjectionKind.INVESTMENT and investment_params is not None:
        # Pylint: ``import-outside-toplevel`` -- lazy import so the static
        # import graph carries no ``net_worth_kernel -> net_worth_investment``
        # cycle at module load, the same seam pattern the loan-reader read
        # above uses.
        from app.services.net_worth_investment import (  # pylint: disable=import-outside-toplevel
            build_investment_balance_map,
        )
        return build_investment_balance_map(
            account, investment_params, ctx.scenario, periods,
            deductions, salary_gross_biweekly,
        )

    # Appreciating physical assets (Property): the user-set market value
    # compounds forward at its annual rate.  The rate rides on the
    # account's eager ``asset_appreciation_params`` backref, so no new
    # dispatch kwarg is needed; the helper flat-carries when the params
    # row is absent.  Same call-time import as the investment branch: the
    # growth builders live in ``net_worth_investment`` (which imports back).
    if kind is AccountProjectionKind.APPRECIATING:
        # Pylint: ``import-outside-toplevel`` -- lazy import breaks the
        # ``net_worth_kernel -> net_worth_investment`` cycle, the same seam
        # pattern the loan-reader read below uses.
        from app.services.net_worth_investment import (  # pylint: disable=import-outside-toplevel
            build_appreciation_balance_map,
        )
        return build_appreciation_balance_map(account, ctx.scenario, periods)

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
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`.
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
        account, ctx, periods,
        debt_schedule=inputs.debt_schedules.get(account.id),
        investment_params=inputs.investment_params_map.get(account.id),
        deductions=inputs.deductions_by_account.get(account.id, []),
        salary_gross_biweekly=inputs.salary_gross_biweekly,
        amount_overrides=amount_overrides,
    )


def _build_amortizing_balance_map(
    account: Account,
    ctx: "BalanceContext",
    periods: list,
    debt_schedule: "DebtSchedule",
) -> "OrderedDict[int, Decimal]":
    """Build an amortizing loan's per-period map: ledger past, projection future.

    The AMORTIZING branch of :func:`build_account_balance_map` (the genesis
    per-period read switch, plan Section 9): the confirmed ledger balance
    (:func:`app.services.loan_posting_service.confirmed_loan_balance_map`) on every
    BEGUN period, and the re-seeded projection
    (:func:`app.services.account_projection.compute_forward_loan_period_balance_map`)
    for the future, spliced
    (:func:`app.services.account_projection.splice_confirmed_and_projected_loan_balances`).
    The ledger books the REAL principal each settled payment paid, so an
    off-schedule payment moves the PAST balances exactly, where the schedule
    replay shows only scheduled principal.  An ORIGINATED loan the ledger has not
    opened is BROKEN and RAISES (:func:`_loan_ledger_not_opened`) -- it does NOT
    fall back to the schedule, whose rows are payments the borrower was supposed to
    make, not ones they did.  The direct reader call is fence-clean: this module is
    in the W9906 seam cluster the reader joins at plan Section 11.

    **A loan that has not ORIGINATED by ``ctx.as_of`` reads a map of TRUE zeros for
    its begun periods, and the splice runs unchanged** -- every begun period
    precedes origination, so it owed nothing in any of them.  Asked of the FACT
    (``owed_from``) BEFORE the ledger is read, exactly as the seam's total producer
    :func:`app.services.balance_at.positions` does.  Reading the ledger's ``None``
    as "not originated" worked only while the walk's clock forced it.

    **The two zeros, which must never be confused.**  The zeros above mean *"the
    loan does not exist yet"* and are TRUE.  The ledger's own pre-opening zero
    (``confirmed_loan_balance_map`` answers ``0.00`` for a period preceding the
    OPENING) means *"I have no record"* and is FALSE for a mid-life import, whose
    loan was owed long before tracking began; spending THAT as money is what made
    the year-end report the borrower ADDING debt they had paid down.  ``owed_from``
    is the fact that tells them apart.

    Args:
        account: The loan account (its id and the scenario scope the ledger
            read; the caller owns the ownership check).
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`.
            Its ``scenario`` scopes the ledger read, and its ``as_of`` is the
            begun/future splice boundary -- the SAME date the loan behind
            *debt_schedule* was resolved at, which a local ``date.today()`` read
            could not guarantee.
        periods: All user pay periods (output domain, keyed by ``period.id``).
        debt_schedule: This account's :class:`DebtSchedule` (resolver schedule,
            projection seed, and origination date).

    Returns:
        The period_id -> Decimal map: confirmed ledger for begun periods,
        re-seeded projection after.

    Raises:
        LoanLedgerNotOpenedError: When an ORIGINATED loan has no OPENING posting in
            the scenario -- its history cannot be read, and the schedule is not a
            substitute for it.
    """
    # Pylint: ``import-outside-toplevel`` -- imported from the defining
    # ``_reader`` submodule (which imports nothing back from here) rather than
    # the package, so the static import graph carries no
    # ``net_worth_kernel -> loan_posting_service`` cycle at module load, the
    # same lazy-seam pattern the loan_payment_service genesis reads use.
    from app.services.loan_posting_service._reader import (  # pylint: disable=import-outside-toplevel
        confirmed_loan_balance_map,
    )
    if debt_schedule.owed_from > ctx.as_of:
        # Not originated yet: nothing has happened to it, so every BEGUN period's
        # confirmed balance is a TRUE 0.00 and the ledger is not asked (see the
        # docstring).  The splice then hands the future to the projection exactly
        # as it does for every other loan.
        #
        # It must be the SPLICE and not the forward map alone: the forward map is
        # period-END keyed, so for a loan originating INSIDE the current period it
        # would report the full opening balance for a period the ledger-keyed hero
        # reports as 0.00 -- one page contradicting itself about one loan on one
        # day, which is the very failure this arc opened with.
        confirmed_map = OrderedDict(
            (period.id, ZERO_MONEY) for period in periods
        )
    else:
        confirmed_map = confirmed_loan_balance_map(
            account.id, ctx.scenario.id, periods,
        )
        if confirmed_map is None:
            # An ORIGINATED loan whose ledger cannot answer is BROKEN.  Fail loud.
            raise _loan_ledger_not_opened(account, ctx)
    return splice_confirmed_and_projected_loan_balances(
        periods,
        confirmed_map,
        compute_forward_loan_period_balance_map(
            debt_schedule.schedule, periods,
            debt_schedule.projection_seed, debt_schedule.owed_from,
        ),
        ctx.as_of,
    )
