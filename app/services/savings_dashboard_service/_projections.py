"""
Shekel Budget App -- Savings Dashboard: per-account balance projections.

Assembles the per-account dict the dashboard template renders.  Every
non-loan account's balance over time comes from the single
:mod:`app.services.balance_at` seam (cash, interest-bearing, investment, and
appreciating-property accounts each dispatch per kind inside it); a loan tile
reads its rich figures -- current balance, monthly payment, rate, payoff --
off the read pass's ONE
:class:`~app.services.resolution_context.ResolvedLoan`, and shows no projected
horizons.

This module no longer imports the resolver or the clock.  It used to load a
loan's context and anchor facts and run ``resolve_loan`` twice per loan (once
for the tile, once for a ``date.max`` "ever paid off" probe); both reads now
come from the context, which resolves each loan exactly once for the whole
render.  No Flask imports.
"""

from collections import OrderedDict
from decimal import Decimal

from app.services import balance_at
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.net_worth_account_data import is_liability_account
from app.services.savings_dashboard_service._types import _LoanAccountResult
from app.utils.period_projections import project_balance_horizons


def _account_balance_maps(accounts, ctx):
    """Build the balance_at seam maps for the NON-LOAN accounts in ONE batch.

    The per-account tile loop reads each non-loan balance out of this single
    dict instead of calling the seam once per account.  ``build_maps``
    assembles the seam's inputs (debt schedules, investment params,
    deductions, and the engine gross-biweekly) ONCE over the whole set, so
    the paycheck-engine gross fetch and the input queries do NOT scale with
    the account count -- the N+1 avoidance ``build_maps`` exists for.  Loans
    are excluded: the loan tile reads the resolver directly
    (:func:`_compute_loan_account`), never the seam.

    Returns an empty dict when there is no baseline scenario (the seam raises
    on a ``None`` scenario by contract, so this caller owns the legitimate
    empty state) -- every non-loan tile then degrades to its anchor balance.

    Args:
        accounts: The accounts being projected (loans are filtered out).
        ctx: The shared :class:`_ProjectionContext` (its ``scenario`` and
            ``all_periods`` feed the seam).

    Returns:
        ``{account_id: OrderedDict period_id -> Decimal}`` for the non-loan
        accounts that have a map; an account the seam omits (no anchor
        period) is simply absent.
    """
    if ctx.balance_ctx.scenario is None:
        return {}
    non_loan_accounts = [
        acct for acct in accounts
        if acct.id not in ctx.params.loan_params_map
    ]
    return balance_at.build_maps(
        non_loan_accounts, ctx.balance_ctx, ctx.all_periods,
    )


def _current_balance_from_map(balances, acct, ctx):
    """Read the current-period balance from a seam map, anchor as fallback.

    Preserves the pre-seam ``_compute_base_balances`` contract exactly: with
    a current period, the tile shows that period's balance from the map --
    which is ``None`` when the map omits it (a cash account whose anchor is
    after the current period: cash balances are not carried backward
    pre-anchor), and that ``None`` is the deliberate "no balance here yet"
    state the hero and goal reducers already treat as zero.  With no current
    period at all, it falls back to the account's stored anchor balance.

    Args:
        balances: The seam's period_id -> balance map (possibly empty).
        acct: The account whose ``current_anchor_balance`` is the
            no-current-period fallback.
        ctx: The shared :class:`_ProjectionContext`.

    Returns:
        The current-period ``Decimal`` balance, or ``None`` when a current
        period exists but the map omits it.
    """
    if ctx.current_period is None:
        return acct.current_anchor_balance or Decimal("0.00")
    return balances.get(ctx.current_period.id)


def _loan_is_paid_off(resolved):
    """Return whether the loan owes nothing -- read from the genesis ledger.

    ``resolved.state.current_balance`` is the ledger-confirmed balance (the
    read switch seeds it from :func:`confirmed_loan_balance_at`), so this asks
    the ONE producer that books what each payment actually paid.  A loan owes
    nothing when that balance is ``<= 0`` AND it has at least one confirmed
    payment -- the confirmed-payment guard is preserved from the original so a
    brand-new loan with a degenerate zero anchor does not render as "paid off".

    **Why this replaced a ``date.max`` resolver probe.**  The flag used to be
    answered by ``resolve_loan(inputs, date.max)`` with NO ``confirmed_view``,
    and that call could not have consulted the ledger even if it wanted to:
    :func:`~app.services.loan_payment_service.confirmed_loan_view` returns
    ``None`` for any ``as_of`` after today, by contract.  So the probe was
    structurally pinned to the pre-read-switch anchor replay -- and that replay
    is BLIND TO MONEY.  It feeds the engine ``ConfirmedPayment(period_start,
    due_date)`` with no amount, advancing one SCHEDULED step per confirmed
    payment (``principal = period P&I - interest``) and discarding the cash
    actually moved.

    The failure was real and bidirectional (``followup_redundant_loan_resolution.md``):

    * Pay a $15,663.59 van loan off with ONE payment.  The ledger says $0.00
      and the hero renders $0 -- but the replay, taking a single ~$450 scheduled
      step, still owed ~$15,213, so ``is_paid_off`` stayed False.  The loan kept
      its "Paid off" badge hidden, stayed in the Horizon's domain as active debt,
      and pushed the "Debt-free" milestone years out.
    * Inversely, a loan paid SHORT amortizes faster in the replay than in the
      ledger, so the replay could reach zero while the ledger still owed.  Then
      ``is_paid_off`` was True, ``_metrics._loan_current_balance`` dropped the
      loan from ``total_debt`` entirely, and its full original principal counted
      as paid.  Real debt silently vanished from the debt card.

    Reading the ledger makes the badge, the debt card, the principal-paid
    fraction, and the Horizon agree with the balance the hero renders BY
    CONSTRUCTION -- and costs zero extra resolutions, where the probe cost one
    full amortization walk per loan per render.

    Args:
        resolved: The loan's :class:`~app.services.loan_resolution.ResolvedLoan`
            from the read pass's context (its ``state.current_balance`` is the
            ledger balance; its ``context.payments`` is the payment feed).

    Returns:
        ``True`` when the loan owes nothing and has at least one confirmed
        payment.
    """
    if not any(p.is_confirmed for p in resolved.context.payments):
        return False
    return resolved.state.current_balance <= Decimal("0.00")


def _compute_loan_account(acct, ctx):
    """Resolve current balance, payment, rate, and payoff for a loan.

    Reads the loan out of the read pass's
    :class:`~app.services.resolution_context.BalanceContext`, which resolves it
    at most ONCE for the whole render.  The resolver is the source of truth for
    current_balance, monthly_payment, current_rate, and payoff_date -- the same
    dollar figures rendered on the loan card and the year-end net-worth
    liability.  The resolver-derived ``current_balance`` replaces the stored
    ``LoanParams.current_principal`` read that pre-E-18 produced the F-008
    stored-vs-engine divergence on this tile.

    It used to load the loan's context and anchor facts and run the resolver
    ITSELF (``load_loan_context`` + ``load_loan_anchor_facts`` +
    ``resolve_loan_seeded``), which is why the same loan the net-worth section
    was already resolving got resolved again for its tile.  Both now read one
    :class:`~app.services.loan_resolution.ResolvedLoan`, so the tile's balance
    and the loan's net-worth contribution are the same number BY CONSTRUCTION
    rather than because two independent resolutions happened to agree.

    Args:
        acct: The loan Account instance.
        ctx: The shared :class:`_ProjectionContext` (its ``balance_ctx`` owns
            the resolution).

    Returns:
        A :class:`_LoanAccountResult` with the resolver-derived figures, or
        ``None`` when the context cannot resolve the account as a loan (no
        ``LoanParams``).
    """
    resolved = ctx.balance_ctx.loan(acct)
    if resolved is None:
        return None
    state = resolved.state
    return _LoanAccountResult(
        current_balance=state.current_balance,
        monthly_payment=state.monthly_payment,
        current_rate=state.current_rate,
        payoff_date=state.payoff_date,
        is_paid_off=_loan_is_paid_off(resolved),
    )


def _compute_needs_setup(
    acct, kind, acct_interest_params, acct_loan_params, acct_investment_params,
):
    """Return whether a parameterized account still needs its params row.

    MED-01 / S6-03: consults the same flag-driven classifier the
    projection dispatcher uses, so the "needs setup" predicate and the
    projection path agree on one account-type taxonomy.

    Args:
        acct: The Account instance.
        kind: The account's :class:`AccountProjectionKind`.
        acct_interest_params: The InterestParams row, or None.
        acct_loan_params: The LoanParams row, or None.
        acct_investment_params: The InvestmentParams row, or None.

    Returns:
        True when the account flags ``has_parameters`` but its
        type-specific params row is missing.
    """
    if not (acct.account_type and acct.account_type.has_parameters):
        return False
    if kind is AccountProjectionKind.INTEREST:
        return acct_interest_params is None
    if kind is AccountProjectionKind.AMORTIZING:
        return acct_loan_params is None
    if kind is AccountProjectionKind.INVESTMENT:
        return acct_investment_params is None
    if kind is AccountProjectionKind.APPRECIATING:
        return acct.asset_appreciation_params is None
    return False


def _project_one_account(acct, ctx, balance_maps):
    """Compute the projection dict for a single account.

    Every non-loan account reads its balance from the one
    :mod:`app.services.balance_at` seam, via the pre-built *balance_maps*
    batch: the current-period balance and the 3 / 6 / 12-month horizons both
    come from that single per-kind map (cash and interest unchanged from the
    prior entries-aware producer; an investment and an appreciating property
    now report the model-from-anchor value the net-worth trend and year-end
    summary already use).  A loan tile instead reads the loan resolver
    directly -- a rich-primitive consumer for its current balance, payment,
    rate, and payoff -- and shows no projected horizons, so it is absent from
    *balance_maps* (the seam is never consulted for a loan, avoiding a second
    resolution of the same loan).

    Args:
        acct: The Account instance.
        ctx: The shared :class:`_ProjectionContext`.
        balance_maps: ``{account_id: balance map}`` from
            :func:`_account_balance_maps` -- the batch-built seam maps for the
            non-loan accounts.  A loan, and a non-loan account the seam omits
            (no anchor period), are absent and read as an empty map.

    Returns:
        A dict with keys: account, current_balance, projected,
        needs_setup, is_paid_off, is_liability, plus optional type-specific
        params (interest_params / investment_params / loan_params +
        monthly_payment + current_rate + payoff_date).
    """
    kind = classify_account(acct)
    acct_interest_params = ctx.params.interest_params_map.get(acct.id)
    acct_loan_params = ctx.params.loan_params_map.get(acct.id)
    acct_investment_params = ctx.params.investment_params_map.get(acct.id)

    loan_result = (
        _compute_loan_account(acct, ctx) if acct_loan_params else None
    )

    if loan_result is not None:
        # Loan tile: a loan-resolver (rich-primitive) consumer.  The seam is
        # not consulted -- current_balance is the as-of-today LoanState
        # balance, and the tile renders payment + payoff, not horizons.
        current_bal = loan_result.current_balance
        projected = {}
    else:
        # Every non-loan kind reads its per-period balance map out of the one
        # batch the seam already built, then picks the current balance and the
        # horizons out of that single map.
        balances = balance_maps.get(acct.id) or OrderedDict()
        current_bal = _current_balance_from_map(balances, acct, ctx)
        projected = project_balance_horizons(
            ctx.current_period, ctx.all_periods, balances,
        )

    needs_setup = _compute_needs_setup(
        acct, kind, acct_interest_params, acct_loan_params,
        acct_investment_params,
    )

    ad = {
        "account": acct,
        "current_balance": current_bal,
        "projected": projected,
        "needs_setup": needs_setup,
        "is_paid_off": loan_result.is_paid_off if loan_result else False,
        # Category-keyed liability flag (the id-based canonical classifier),
        # so the cockpit cell balance can take the danger token the group
        # subtotal, chip, and bar segment already do -- one quantity, one
        # treatment (polish audit P-AC4).
        "is_liability": is_liability_account(acct),
    }
    if acct_interest_params:
        ad["interest_params"] = acct_interest_params
    if acct_investment_params:
        ad["investment_params"] = acct_investment_params
    if acct_loan_params:
        ad["loan_params"] = acct_loan_params
        ad["monthly_payment"] = loan_result.monthly_payment
        # DH-#56: the loan's current rate (resolver-derived) replaces the
        # retired ``LoanParams.interest_rate`` column read on the /savings
        # debt card and in the weighted-average-rate metric.
        ad["current_rate"] = loan_result.current_rate
        ad["payoff_date"] = loan_result.payoff_date
    return ad


def _compute_account_projections(accounts, ctx):
    """Compute balance projections for each account.

    Builds the non-loan balance maps ONCE via :func:`_account_balance_maps`
    (so the seam's input assembly -- including the paycheck-engine gross
    fetch -- runs a single time for the whole set, not once per account),
    then projects each account against that shared batch.

    Args:
        accounts: List of Account model instances.
        ctx: The shared :class:`_ProjectionContext` bundling the periods,
            current period, baseline scenario, and type-specific parameter
            maps.

    Returns:
        A list of per-account dicts (see :func:`_project_one_account`).
    """
    balance_maps = _account_balance_maps(accounts, ctx)
    return [
        _project_one_account(acct, ctx, balance_maps) for acct in accounts
    ]
