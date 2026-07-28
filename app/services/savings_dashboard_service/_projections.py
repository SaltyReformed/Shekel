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
from app.services.savings_dashboard_service._types import (
    AccountProjection,
    LoanDetail,
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

    **This is now ONE of the package's TWO doors, and they are the only two**
    (plan step X-t2, finding N-107).  The rule was written out in three
    producers here; the other two -- the dense-map builder and the trend window
    -- sat under one caller, so it moved up to
    :func:`~.._orchestrator._compute_net_worth_section` and they simply call the
    seam.  This door survives because it belongs to a DIFFERENT caller: the
    projection runs for the full page and for each narrow producer, and its
    degraded value is a blank tile rather than an empty region.

    The PREDICATE itself is the seam's own
    :attr:`~app.services.balance_at.BalanceContext.has_baseline`, which
    :func:`~app.services.balance_at.require_scenario` raises on -- so the guard
    and the precondition it guards are one property, read from both ends,
    rather than an independent spelling of ``ctx.scenario is None`` at each call
    site (18 in the tree when X-t2 measured it; the 12 this step does not reach
    are finding N-112).  A new seam call added inside this module's per-account
    loop would still escape -- nothing mechanical prevents it, since W9909 /
    W9910 gate imports and module identity, never call sites.

    Args:
        accounts: The accounts being projected (any mix of kinds).
        ctx: The shared :class:`_ProjectionContext` (its ``balance_ctx``,
            ``all_periods`` and ``params.loan_params_map`` feed the seam).

    Returns:
        The :class:`_SeamBatches` for this projection -- empty on both maps
        when there is no baseline scenario, which is the legitimate empty state
        every tile then degrades through.
    """
    if not ctx.balance_ctx.has_baseline:
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
    step X-s2, finding N-105) so the projection carries the figures and the
    terms row from ONE value under ONE condition, instead of taking one of them
    from a second lookup that a different module's filter decides.  Since plan
    step X-t1 the two are a single :class:`~.._types.LoanDetail` field, so the
    condition is the field's existence and there is nothing left to disagree.

    Args:
        acct: The loan Account instance.
        acct_loan_params: The account's already-loaded
            :class:`~app.models.loan_params.LoanParams` row, carried through to
            the result.
        ctx: The shared :class:`_ProjectionContext` (its ``balance_ctx`` owns
            the resolution).

    Returns:
        A :class:`~.._types._LoanAccountResult` -- the seam balance plus the
        :class:`~.._types.LoanDetail` -- or ``None`` when the seam does not
        resolve the account as a configured loan.
    """
    figures = balance_at.loan_figures(acct, ctx.balance_ctx)
    if figures is None:
        return None
    return _LoanAccountResult(
        current_balance=balance_at.balance_at(
            acct, ctx.balance_ctx, ctx.balance_ctx.as_of,
        ),
        detail=LoanDetail(figures=figures, params=acct_loan_params),
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
    now carries the params, so both loan facts are written from one value under
    one condition -- and since plan step X-t1 they are ONE FIELD, so a consumer
    cannot re-open the question either.

    Args:
        acct: The Account instance.
        ctx: The shared :class:`_ProjectionContext`.
        batches: The :class:`~.._types._SeamBatches` from
            :func:`_seam_batches` -- every seam read this projection makes,
            built once for the whole set.  A loan, and a non-loan account the
            seam omits (no anchor period), are absent from ``balance_maps`` and
            read as an empty map.

    Returns:
        The account's :class:`~.._types.AccountProjection` -- THE shape every
        consumer of this package reads.  It was an untyped dict whose optional
        KEYS were its type discriminator until plan step X-t1 (finding N-111);
        that class's docstring carries the two defects the container cost.
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

    return AccountProjection(
        account=acct,
        current_balance=current_bal,
        projected=projected,
        needs_setup=needs_setup,
        # An absent parameter row is ``None``, never a missing attribute: the
        # dict this replaced omitted the KEY, so "does this account have an APY"
        # and "is this account a loan" were both spelled as key membership, and
        # a consumer that mistyped one got a silent Jinja ``Undefined`` rather
        # than a failure (plan step X-t1, finding N-111).  The maps already
        # answer ``None`` for an account with no row, so the value passes
        # straight through -- the dict's truthiness test is gone with it, per the
        # coding standard's "a zero balance is not a missing balance".
        interest_params=acct_interest_params,
        investment_params=acct_investment_params,
        # The loan half as ONE value under ONE condition.  The dict wrote two
        # keys here, and the seam's ``LoanFigures`` was FLATTENED into it field
        # by field before plan step X-r: five copies -- ``is_paid_off``,
        # ``is_originated``, ``monthly_payment``, ``current_rate``,
        # ``payoff_date`` -- and six as of plan step X-o, which added
        # ``is_retired`` as a sixth deliberately so the live defect did not wait
        # on this refactor.  Until X-o that missing sixth field WAS finding
        # B-16: nothing failed, because a consumer cannot miss a key that was
        # never there, so the Horizon asked the nearest question the dict could
        # answer and reported a retired loan as debt the user still carried.
        # ``LoanDetail`` composes the seam's value for that reason, so a field
        # the seam grows arrives at every consumer by construction.
        loan=loan_result.detail if loan_result is not None else None,
    )


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
        A list of :class:`~.._types.AccountProjection` values, one per account
        in *accounts* order (see :func:`_project_one_account`).
    """
    batches = _seam_batches(accounts, ctx)
    return [_project_one_account(acct, ctx, batches) for acct in accounts]
