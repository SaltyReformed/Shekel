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
step X-s2), so the per-account assembly below touches the seam nowhere: this
module's two reads -- the batched balance maps and the per-loan resolution --
are opened in one place, behind one no-baseline predicate, in one shape.  That
place is one of the PACKAGE's three seam doors; see that function.
"""


from app.services import balance_at
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.account_category import account_category
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
    seam -- the per-kind balance maps and the per-loan resolution -- and this is
    where both are opened, so the loop below reaches the seam nowhere itself.
    ``build_maps`` assembles the seam's inputs (debt schedules, investment
    params, deductions, and the engine gross-biweekly) ONCE over the whole set,
    so the paycheck-engine gross fetch and the input queries do NOT scale with
    the account count -- the N+1 avoidance ``build_maps`` exists for.  The loans
    are batched beside it rather than resolved inside the loop for the same
    reason: two doors of one shape, opened in one place.

    **The map door now covers EVERY account, loans included** (plan step X-w,
    ruling R-CG, finding N-114).  It covered the non-loan accounts only, on the
    ground that a loan tile reads no map -- true of the TILE, and false of the
    page: the net-worth trend, its composition split and the card sparklines all
    read a loan's dense map, so a SECOND per-account container was built for
    them from the same accounts on the same render, and it stored the liability
    flag :attr:`~.._types.AccountProjection.is_liability` derives.  The loans are
    resolved first, in the loop below, so ``build_maps`` reaches each one's
    memoized resolution: measured at ``0.19-0.59 ms`` and ZERO SQL per loan on a
    warm context (best of five, both databases), against the ``20-95 ms`` the
    resolution itself costs cold and which every caller of this function already
    pays.

    **The no-baseline rule is stated once for THIS projection's two doors**
    (plan step X-s2, ruling R-BF, finding N-105).  The seam raises on a ``None``
    scenario by contract, and its own guard says a caller that legitimately
    handles that state must guard BEFORE calling; this build guarded at the map
    door only, so a user with no baseline got four kinds of blank balance and a
    ``ValueError`` from the fifth -- the loan arm reaching ``require_scenario``
    through ``loan_figures`` -> ``memoized_payoff``.

    **This is ONE of the package's THREE seam doors** (plan step X-t2, finding
    N-107; the count corrected at X-t5).  The rule was written out in three
    producers here; two of them -- the dense-map builder and the trend window --
    sat under one caller, so it moved up to
    :func:`~.._orchestrator._compute_net_worth_section` and they simply call the
    seam.  This door survives because it belongs to a DIFFERENT caller: the
    projection runs for the full page and for each narrow producer, and its
    degraded value is a blank tile rather than an empty region.  The third is
    :func:`~.._net_worth.compute_property_equity`, whose secured-loan read
    reaches the seam through ``home_equity_service`` -- X-t2's docstrings said
    there were only two, and both of its adversarial reviews found the third by
    walking the call graph instead of counting call sites.

    **All three doors are now UNGUARDED, deliberately** (plan step X-v2,
    ruling R-BW).  X-t2 gave the three one PREDICATE to share; they still
    answered with three different degraded values, and the fourth door nobody
    had found would have needed a fourth.  The seam raises a named exception
    and one application-level handler answers it, so "a new seam call added
    inside this loop escapes the guard" -- true when this paragraph was written,
    and unfixable by any import- or module-scoped gate -- stopped being a way
    to get a wrong number onto a screen.

    Args:
        accounts: The accounts being projected (any mix of kinds).
        ctx: The shared :class:`_ProjectionContext` (its ``balance_ctx``,
            ``all_periods`` and ``params.loan_params_map`` feed the seam).

    Returns:
        The :class:`_SeamBatches` for this projection.

        **The no-baseline early return went at plan step X-v2** (ruling R-BW).
        It returned empty maps so every tile could "degrade" -- which meant
        every ``current_balance`` came back ``None`` and seven reducers in this
        package turned that into ``$0.00``.  The seam raises now and one
        application-level handler answers, which is why
        :attr:`~.._types.AccountProjection.current_balance` could stop being
        nullable in the same commit.
    """
    loan_results = {}
    for acct in accounts:
        acct_loan_params = ctx.params.loan_params_map.get(acct.id)
        if acct_loan_params is None:
            continue
        loan_result = _compute_loan_account(acct, acct_loan_params, ctx)
        if loan_result is not None:
            loan_results[acct.id] = loan_result

    return _SeamBatches(
        balance_maps=balance_at.build_maps(
            accounts, ctx.balance_ctx, ctx.all_periods,
        ),
        loan_results=loan_results,
    )


def _current_balance_from_map(balances, ctx):
    """Read the current-period balance from a seam map.

    **The anchor-cache fallback is GONE** (plan step X-x2, ruling R-CY).  With
    no current period this returned :attr:`~app.models.account.Account.current_anchor_balance`
    -- a DERIVED CACHE that ``cash_ledger.resolve_anchor`` is already known to
    find diverged from the ledger (cash D4), and for an amortizing account not a
    balance at all (finding N-103) -- presented as the account's CURRENT
    balance.  Its docstring called that "a real figure and not a stand-in"
    because the column is ``NOT NULL``; being non-null is not being right.
    Measured on a prod-shape clone with a four-day hole in the pay calendar, the
    substitution rendered Checking at ``$2,932.41`` against ``$406.92`` and
    moved the page's net worth by ``$3,228.55``.  ``ctx.current_period`` is not
    nullable now, so there is no branch left to take.

    **The map is INDEXED, not ``.get``-defaulted** (plan step X-v2, ruling
    R-CA), which is the argument :func:`app.services.balance_at.build_maps`
    already makes about its own total feed map: the seam builds a column for
    EVERY period it is handed, so a missing key is a defect in the seam or in
    the period list, and answering it with ``None`` renders a real account as
    one the app has no figure for -- a wrong figure wearing a plausible shape.
    The ``None`` this used to return was reduced to ``$0.00`` by seven callers
    (finding N-113); a ``KeyError`` here fails loudly at the one place that can
    explain it.

    Args:
        balances: The seam's period_id -> balance map, built over
            ``ctx.all_periods``.
        ctx: The shared :class:`_ProjectionContext`.

    Returns:
        The current-period ``Decimal`` balance.

    Raises:
        KeyError: When the map has no column for the current period -- a seam
            or period-list defect, never a display state.
    """
    return balances[ctx.current_period.id]


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
    """Compute the projection for a single account.

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
    horizons.

    **Every account carries its dense map, and the loan's balance is still the
    SCALAR** (plan step X-w, ruling R-CG).  The two are the same figure -- the
    seam's current-period column is clamped to the read pass's ``as_of``, and
    the probe measured them equal to the cent for both loans on both databases
    -- but they are equal by the seam's construction, not by this module's, so
    the loan arm keeps reading the value the tile has always rendered.  The map
    rides alongside because the net-worth trend, the composition split and the
    sparklines need it for every kind.

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
            built once for the whole set.  ``balance_maps`` is TOTAL over the
            projected accounts and is INDEXED here for that reason: the seam
            omits only an account with no anchor period, and
            ``accounts.current_anchor_period_id`` is ``NOT NULL``.

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
    # EVERY kind's dense period map, from the one batch the seam already built
    # (plan step X-w).  A loan's tile reads none of it, but the net-worth trend,
    # the composition split and the sparklines read every kind's, which is why
    # it is not the loan arm's business whether it is here.
    balances = batches.balance_maps[acct.id]

    if loan_result is not None:
        # Loan tile: both figures come from the seam (see
        # :func:`_compute_loan_account`) -- the balance from ``balance_at``, the
        # payment / rate / payoff from ``loan_figures``.  It renders no horizons.
        current_bal = loan_result.current_balance
        projected = {}
    else:
        # Every non-loan kind picks the current balance and the horizons out of
        # that single map.
        current_bal = _current_balance_from_map(balances, ctx)
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
        balances=balances,
        projected=projected,
        needs_setup=needs_setup,
        # The account's category, resolved ONCE per account per render (plan
        # step X-z7, ruling R-CT).  Both questions this page asks of it read
        # this one answer: the asset-vs-liability sign through
        # :attr:`~.._types.AccountProjection.is_liability`, and the chart band /
        # grid group through ``_display``.  Plan step X-z2 threaded a parallel
        # ``{account_id: category_key}`` dict for the second of those, which was
        # a second per-account container keyed by account id -- ruling R-CG's
        # own defect, re-created one commit after the step that deleted it.
        category=account_category(acct),
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
