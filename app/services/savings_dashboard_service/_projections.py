"""
Shekel Budget App -- Savings Dashboard: per-account balance projections.

Assembles the per-account dict the dashboard template renders.  Every
non-loan account's balance over time comes from the single
:mod:`app.services.balance_at` seam (cash, interest-bearing, investment, and
appreciating-property accounts each dispatch per kind inside it); a loan tile
reads its rich figures -- current balance, monthly payment, rate, payoff --
off the read pass's ONE
:class:`~app.services.balance_at._resolution.ResolvedLoan`, and shows no projected
horizons.

This module no longer imports the resolver or the clock.  It used to load a
loan's context and anchor facts and run ``resolve_loan`` twice per loan (once
for the tile, once for a ``date.max`` "ever paid off" probe); both reads now
come from the context, which resolves each loan exactly once for the whole
render.  No Flask imports.

**Every seam read this module makes happens in :func:`_seam_batches`** (plan
step X-s2), so the per-account assembly below touches the seam nowhere: the two
doors -- the batched balance maps and the per-loan resolution -- are opened in
one place, behind one no-baseline predicate, in one shape.
"""

from collections import OrderedDict
from decimal import Decimal

from app.services import balance_at
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.net_worth_account_data import is_liability_account
from app.services.savings_dashboard_service._types import (
    _LoanAccountResult,
    _SeamBatches,
)
from app.utils.period_projections import project_balance_horizons


def _seam_batches(accounts, ctx):
    """Build every balance-seam read the projection loop consumes, in ONE pass.

    This build has exactly TWO doors into the :mod:`app.services.balance_at`
    seam -- the per-kind balance maps for the non-loan accounts, and the
    per-loan resolution -- and this is where both are opened, so the loop below
    reaches the seam nowhere itself.  ``build_maps`` assembles the seam's inputs
    (debt schedules, investment params, deductions, and the engine
    gross-biweekly) ONCE over the whole set, so the paycheck-engine gross fetch
    and the input queries do NOT scale with the account count -- the N+1
    avoidance ``build_maps`` exists for.  The loans are batched beside it rather
    than resolved inside the loop for the same reason: two doors of one shape,
    opened in one place.

    **The no-baseline rule is stated once for THIS projection's two doors**
    (plan step X-s2, ruling R-BF, finding N-105).  The seam raises on a ``None``
    scenario by contract, and its own guard says a caller that legitimately
    handles that state must guard BEFORE calling; this build guarded at the map
    door only, so a user with no baseline got four kinds of blank balance and a
    ``ValueError`` from the fifth -- the loan arm reaching ``require_scenario``
    through ``loan_figures`` -> ``memoized_payoff``.

    **What that does NOT mean, stated because the first draft of this docstring
    claimed it and was wrong** (caught by X-s2's adversarial review): the rule
    is not stated once in the PACKAGE.  Three more copies carry it, each in a
    producer this step does not touch --
    :func:`~.._net_worth.build_account_net_worth_maps` (the guard at
    ``_net_worth.py:150``), :func:`~.._orchestrator._build_trend_window`
    (``_orchestrator.py:454``), and
    :func:`app.services.dashboard_pulse_service.compute_pulse_section`
    (``dashboard_pulse_service.py:157``).  Hoisting all four to the build entry
    is the fix and it is finding **N-107**, owned by plan step **X-t**.  A new
    seam call added inside this module's per-account loop would likewise escape
    -- nothing mechanical prevents it, since W9909 / W9910 gate imports and
    module identity, never call sites.

    Args:
        accounts: The accounts being projected (any mix of kinds).
        ctx: The shared :class:`_ProjectionContext` (its ``balance_ctx``,
            ``all_periods`` and ``params.loan_params_map`` feed the seam).

    Returns:
        The :class:`_SeamBatches` for this projection -- empty on both maps
        when there is no baseline scenario, which is the legitimate empty state
        every tile then degrades through.
    """
    if ctx.balance_ctx.scenario is None:
        return _SeamBatches(balance_maps={}, loan_results={})

    loan_results = {}
    non_loan_accounts = []
    for acct in accounts:
        acct_loan_params = ctx.params.loan_params_map.get(acct.id)
        if acct_loan_params is None:
            non_loan_accounts.append(acct)
            continue
        loan_result = _compute_loan_account(acct, acct_loan_params, ctx)
        if loan_result is not None:
            loan_results[acct.id] = loan_result

    return _SeamBatches(
        balance_maps=balance_at.build_maps(
            non_loan_accounts, ctx.balance_ctx, ctx.all_periods,
        ),
        loan_results=loan_results,
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


def _compute_loan_account(acct, acct_loan_params, ctx):
    """Resolve current balance, payment, rate, and payoff for a loan.

    BOTH reads go through the :mod:`app.services.balance_at` seam, and both hit
    the read pass's ONE memoized resolution:

    * the BALANCE from :func:`~app.services.balance_at.balance_at` -- the same
      seam entry every other account kind on this page reads, so the loan tile
      is no longer the one kind whose displayed balance was produced outside the
      seam.  It used to be ``LoanState.current_balance``, read straight off the
      resolver; that value IS a balance-at-T, and because the name-keyed fence
      of the day bound on function names and could not see an attribute read,
      the loan's balance -- the hero's biggest number -- reached the screen with
      every gate silent.  Plan step D2a deleted the attribute at the root.
    * the rich FIGURES from :func:`~app.services.balance_at.loan_figures`, a
      value object that deliberately carries no balance, so this module cannot
      render one by accident.

    Both come from one resolution, so the tile's balance and the loan's
    net-worth contribution are the same number BY CONSTRUCTION rather than
    because two producers happened to agree.

    **The caller's already-loaded ``LoanParams`` rides into the result** (plan
    step X-s2, finding N-105) so the projection dict writes ``loan_params`` and
    ``loan_figures`` from ONE value under ONE condition, instead of writing one
    of them from a second lookup that a different module's filter decides.

    Args:
        acct: The loan Account instance.
        acct_loan_params: The account's already-loaded
            :class:`~app.models.loan_params.LoanParams` row, carried through to
            the result.
        ctx: The shared :class:`_ProjectionContext` (its ``balance_ctx`` owns
            the resolution).

    Returns:
        A :class:`_LoanAccountResult`, or ``None`` when the seam does not
        resolve the account as a configured loan.
    """
    figures = balance_at.loan_figures(acct, ctx.balance_ctx)
    if figures is None:
        return None
    return _LoanAccountResult(
        current_balance=balance_at.balance_at(
            acct, ctx.balance_ctx, ctx.balance_ctx.as_of,
        ),
        figures=figures,
        params=acct_loan_params,
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


def _project_one_account(acct, ctx, batches):
    """Compute the projection dict for a single account.

    Pure assembly over the prebuilt *batches*: this function reaches the
    :mod:`app.services.balance_at` seam nowhere itself, which is what lets
    :func:`_seam_batches` own the no-baseline rule for every kind at once.
    Every non-loan account reads its balance out of the batch's per-kind map --
    the current-period balance and the 3 / 6 / 12-month horizons both come from
    that single map (cash and interest unchanged from the prior entries-aware
    producer; an investment and an appreciating property now report the
    model-from-anchor value the net-worth trend and year-end summary already
    use).  A loan tile instead reads the batch's resolved
    :class:`~.._types._LoanAccountResult` -- a rich-primitive consumer for its
    current balance, payment, rate, and payoff -- and shows no projected
    horizons, so it is absent from the balance maps (the seam is never
    consulted twice for one loan).

    **The account being a loan is asked ONCE** (plan step X-s2, finding
    N-105).  This function used to branch on ``loan_result is not None`` and
    then re-test the same fact as ``if acct_loan_params:`` before dereferencing
    ``loan_result.figures`` -- two predicates for one condition, agreeing only
    because ``_data._load_loan_params_and_escrow`` filters a SUBSET of what
    ``loan_loaders.load_loan_params`` returns, an invariant held in two other
    modules neither of which knows this dereference depends on it.  The result
    now carries the params, so both loan keys are written from one value under
    one condition.

    Args:
        acct: The Account instance.
        ctx: The shared :class:`_ProjectionContext`.
        batches: The :class:`~.._types._SeamBatches` from
            :func:`_seam_batches` -- every seam read this projection makes,
            built once for the whole set.  A loan, and a non-loan account the
            seam omits (no anchor period), are absent from ``balance_maps`` and
            read as an empty map.

    Returns:
        A dict with keys: account, current_balance, projected,
        needs_setup, is_liability, plus optional type-specific params
        (interest_params / investment_params / loan_params) and, for a
        configured loan, ``loan_figures`` -- the seam's own
        :class:`~app.services.balance_at.LoanFigures`, carried WHOLE.
    """
    kind = classify_account(acct)
    acct_interest_params = ctx.params.interest_params_map.get(acct.id)
    acct_investment_params = ctx.params.investment_params_map.get(acct.id)

    loan_result = batches.loan_results.get(acct.id)

    if loan_result is not None:
        # Loan tile: both figures come from the seam (see
        # :func:`_compute_loan_account`) -- the balance from ``balance_at``, the
        # payment / rate / payoff from ``loan_figures``.  It renders no horizons,
        # so it takes no balance map.
        current_bal = loan_result.current_balance
        projected = {}
    else:
        # Every non-loan kind reads its per-period balance map out of the one
        # batch the seam already built, then picks the current balance and the
        # horizons out of that single map.
        balances = batches.balance_maps.get(acct.id) or OrderedDict()
        current_bal = _current_balance_from_map(balances, acct, ctx)
        projected = project_balance_horizons(
            ctx.current_period, ctx.all_periods, balances,
        )

    # "Does this account still need its params row" is a DIFFERENT question
    # from "did it resolve as a loan", and it must stay answerable for an
    # AMORTIZING account that has no ``LoanParams`` at all -- which is exactly
    # the state it reports.  So it reads the map, where the resolution above
    # reads the batch.
    needs_setup = _compute_needs_setup(
        acct, kind, acct_interest_params,
        ctx.params.loan_params_map.get(acct.id), acct_investment_params,
    )

    ad = {
        "account": acct,
        "current_balance": current_bal,
        "projected": projected,
        "needs_setup": needs_setup,
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
    if loan_result is not None:
        ad["loan_params"] = loan_result.params
        # The seam's value object, carried WHOLE (plan step X-r).  This dict
        # used to FLATTEN it, field by field: five copies -- ``is_paid_off``,
        # ``is_originated``, ``monthly_payment``, ``current_rate``,
        # ``payoff_date`` -- and six as of plan step X-o, which added
        # ``is_retired`` as a sixth deliberately so the live defect did not
        # wait on this refactor.  Until X-o that missing sixth field WAS
        # finding B-16: nothing failed, because a consumer cannot miss a key
        # that was never there, so the Horizon asked the nearest question the
        # dict could answer and reported a retired loan as debt the user still
        # carried.
        #
        # ``_types._LoanAccountResult`` composes ``LoanFigures`` for exactly
        # this reason one layer down -- "the copy silently went stale the
        # moment the seam grew ``is_originated``", and "a bundle that must be
        # hand-synchronised with the seam it mirrors is the seam's fence with
        # a hole in it".  This is that ruling applied where the copy actually
        # happened.  A field the seam grows now arrives here by construction.
        ad["loan_figures"] = loan_result.figures
    return ad


def _compute_account_projections(accounts, ctx):
    """Compute balance projections for each account.

    Makes every balance-seam read ONCE via :func:`_seam_batches` (so the seam's
    input assembly -- including the paycheck-engine gross fetch -- runs a single
    time for the whole set, not once per account, and each loan resolves once),
    then assembles each account's dict against that shared batch.

    Args:
        accounts: List of Account model instances.
        ctx: The shared :class:`_ProjectionContext` bundling the periods,
            current period, baseline scenario, and type-specific parameter
            maps.

    Returns:
        A list of per-account dicts (see :func:`_project_one_account`).
    """
    batches = _seam_batches(accounts, ctx)
    return [_project_one_account(acct, ctx, batches) for acct in accounts]
