"""Balance-at-T seam -- the GRID kind-aware cash-flow view.

The budget grid is a single-account cash-flow surface (it reads the cash FOLD,
:mod:`._cash_fold`), but it is NOT always pointed at a cash account
(``resolve_grid_account`` falls back to any active account).  For a grid account
that MODELS a return -- an INTEREST-bearing HYSA / Money Market / CD / HSA
today, an INVESTMENT or an APPRECIATING asset at plan step X-g3b -- the pure
transaction running-balance understates the real balance, because it ignores the
return the net-worth surfaces already credit.  This view gives such an account
the modelled balance AND the per-period modelled figures that explain the part of
the balance change the transactions do not -- so the grid's balance row still
reconciles with the rows above it.

**ONE per-period column, from ONE producer pass** (plan steps X-c2b1 / X-c2b2,
ruling R-K).  Every figure the grid renders for one pay period -- the projected
end balance, the income and expense subtotals, the "Timing & true-ups"
remainder, the modelled contribution and the modelled accrual -- is one
:class:`GridColumn`, and all but the last two come from a single
:func:`~app.services.balance_at._cash_fold.cash_period_view`: one walk, one
plan load, one valuation, grouped on the two clocks the identity binds.

    balance[p] - balance[p-1]
        == net[p] + reconciliation[p] + contribution[p] + accrual[p]

The grid used to compute those figures in three independent producer passes a
test then had to keep in step (finding N-48), against a subtotal that counted
only still-UNPAID rows while the balance counted the anchor plus the same rows
-- an identity that held only because neither side could see a settled row at
all, and that broke on 8 of 59 real period pairs (worst ``$2,505.17``) the
moment the balance became a fold (finding N-41).  Reading one row set grouped
two ways makes the identity a property of the object the template reads.

**The identity carries FOUR terms, not three** (ruling R-W as corrected at
X-g2b, measured at ruling R-AH).  A modelled asset has TWO modelled tiers, and
on the real Empower 401(k) the CONTRIBUTION is the larger of them
(``$9,624.27`` against ``$8,152.58`` over the horizon): the three-term form
breaks on 53 of 59 period pairs, worst ``$181.59`` a column, and the four-term
form on none.  So the seam carries both tiers apart -- they answer different
questions, what the market did and what the user put in
(:func:`._asset_fold.asset_growth_at`) -- and the grid renders them as two rows
rather than one sum, which could otherwise report a gain on an account that lost
money.

ONLY INTEREST accrues here, and since plan step X-g2b that is a SEQUENCING fact
rather than a structural one.  Its accrual is the modelled replay
(:func:`app.services.balance_at._asset_fold.resolve`) resolved over the very
:class:`~app.services.balance_at._cash_fold.AssembledCashFold` this view
regroups into its cash columns -- ONE walk, one plan load, one valuation -- so
the grid's INTEREST balance and ``/savings``' are the same producer's answer
rather than two that a test keeps byte-identical.

The other non-cash kinds are still on the kind-blind cash-flow view here:

* AMORTIZING (loan) -- the amortization schedule drives the balance (principal
  paydown), while its grid "transactions" are payment transfers recorded as
  income (opposite sign, different magnitude); no single accrual row reconciles
  them.  Ruling D4 refuses one at the resolver anyway, so this branch is the
  degenerate safety, not a supported view.  An AMORTIZING account with no
  ``LoanParams`` reaches the cash view and belongs there.
* INVESTMENT / APPRECIATING -- **the reason this module used to give is no
  longer true, and finding N-76 is what remains of it.**  It said their balance
  is not a transaction sum, so "an ad-hoc grid row lands in the cash basis but
  not the projected balance".  Under one replay a typed grid row IS an event in
  the same stream, so it moves the modelled balance exactly as it moves a
  HYSA's -- which is why ruling **R-W** puts the modelled balance and the two
  modelled rows on this surface.  Plan step **X-g3a** shipped the SHAPE (the
  rows, the flags, the per-kind label) with the gate below still in place, so no
  figure moved; plan step **X-g3b** deletes the gate and the balance follows.
  Until it does, the grid answers those two kinds on the cash basis while
  ``/savings`` answers them modelled -- measured at `$17,776.85` on the Empower
  401(k) -- and N-76 stays open with that number on it.

PLAIN likewise carries no accrual (its kind-correct balance IS its cash basis,
and the replay says the same thing by having no ACCRUAL tier to resolve).
"""

from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal

from app.models.account import Account

from ._asset_contributions import ContributionInputs
from ._context import BalanceContext
from . import _asset_fold, _cash_fold, _interest
from ._inputs import _require_scenario

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class GridColumn:
    """Every figure the grid renders for ONE pay period.

    The per-period unit of :class:`GridBalanceView`, and ruling R-K's row set
    expressed as one record: the same valued rows grouped on the budget clock
    (:attr:`income` / :attr:`expense` / :attr:`net`), what the cash clock and
    the assertions add on top of that (:attr:`reconciliation`), the two modelled
    tiers (:attr:`contribution` and :attr:`accrual`), and the balance all four
    roll forward to (:attr:`balance`).

    Attributes:
        balance: The projected end balance the surface displays, cent-quantized
            -- the modelled balance for an account that models a return, the
            folded cash balance for every other kind.  Never ``None``: the fold
            is TOTAL, so every requested period has one.  (It was optional while
            the projection carried the anchor forward and omitted every
            pre-anchor period; on the real Checking account eight columns
            rendered ``--`` for periods it plainly held money in.)
        income: The period's income subtotal, cent-quantized -- every row
            ATTRIBUTED to the period, settled at its confirmed cash leg and
            still-projected at its live or entries-aware amount (ruling R-K).
        expense: The same, for expense rows (a magnitude), so the column reads
            ``income`` minus ``expense``.
        net: ``round_money(income - expense)`` -- rounded ONCE at the boundary
            rather than as the difference of two separately-rounded legs,
            because it is the figure the balance roll-forward has to reconcile
            with.
        reconciliation: Ruling R-K's remainder, rendered as "Timing &
            true-ups": money budgeted to this period that moved in another (or
            has not moved yet), money that moved here but is budgeted
            elsewhere, and the balance ASSERTIONS made inside the period.
        contribution: The period's modelled CONTRIBUTION -- what the account's
            payroll puts in, employee plus employer (the read-only
            "Contributions" row).  ``0.00`` for every kind but INVESTMENT, whose
            feed is the only one that exists
            (:func:`._asset_contributions.contribution_events`).
        accrual: The period's modelled RETURN -- interest, market growth or
            appreciation (the read-only accrual row, whose LABEL the route
            resolves per kind, ruling R-AI).  ``0.00`` for an account that
            models none.

    **Neither modelled field is optional, and that is ruling R-AJ (c).**  Under
    one replay every requested period carries a ``Decimal`` for both, so
    ``Decimal | None`` is a state the producer cannot be in -- and a template
    guarding against it is a guard against an impossible shape, which reads as
    coverage and is not.  ``interest: Decimal | None`` is what this field was
    until plan step X-g3a; it was optional because the accrual arrived as a map
    that covered only the periods an INTEREST account's layering pass produced.
    """

    # The four subtotal figures are the cash view's verbatim, so this record
    # and :class:`~app.services.balance_at._cash_fold.CashPeriodFigures` share
    # five field declarations.  Composing instead (``GridColumn.cash``) was
    # REJECTED: it would put TWO balances on the one object the templates read
    # -- the kind-blind cash balance beside the displayed modelled one
    # -- which is precisely the "two producers on one screen" shape this arc
    # exists to end, and a template reaching the wrong one would render a
    # silently wrong figure.  Inheriting was rejected for the same reason one
    # level up: a subclass whose ``balance`` means something the parent's does
    # not is a substitution defect, and the two carry DIFFERENT identities
    # (``net + reconciliation`` there, ``+ contribution + accrual`` here).
    # There is no shared BEHAVIOUR to extract -- only names -- and the two
    # contracts are free to diverge (this one is what the grid renders; that one
    # is what the fold produces).
    # Pylint: ``duplicate-code`` -- incidental field-name overlap with
    # ``_cash_fold.CashPeriodFigures``; one-sided disable so the producer's own
    # declaration stays un-disabled.
    # pylint: disable=duplicate-code
    balance: Decimal
    income: Decimal
    expense: Decimal
    net: Decimal
    reconciliation: Decimal
    # pylint: enable=duplicate-code
    contribution: Decimal
    accrual: Decimal


@dataclass(frozen=True)
class GridRowFlags:
    """Which CONDITIONAL rows a given visible window renders.

    Ruling R-O's visibility rule, stated ONCE for all three rows it governs: a
    conditional row is present for the whole visible window when at least one
    visible column carries a non-zero value, and shows its own figure (``$0.00``
    included) in every column of that window.  Rejected at the ruling: always-on
    (a permanently-zero row on the forward-looking windows, which are the ones
    most used) and past/current-only (an all-zero PAST window then reads as "not
    measured" rather than "nothing to explain").

    The rule lives here rather than in each template because it is the same
    rule for three rows on four different windows (the visible grid, the Plan
    tab, the mobile This Period card, and the two self-refresh partials), and a
    template that decided it per surface is how one form factor ends up
    rendering a balance its own figures cannot explain (ruling R-P).

    **It is a METHOD on the view rather than a free function in the grid's
    presentation service, and that is the deliberate half.**  The rule is a
    predicate over the view's OWN columns, so a free
    ``row_flags(columns, periods)`` would take as an argument the one thing a
    caller can get wrong -- hand it a different account's columns, or the
    window it is not about, and it answers confidently and wrongly.  That is
    the shape plan Section 8 rules a defect rather than a contract (the fold
    once TOOK the period list its visibility rule needed, and a caller passing
    a window moved a balance by ``$150,000.00``).  Asked of the view, it cannot
    be asked about anything else.  It carries no money and decides no figure --
    only whether a row the seam already computed appears -- so it is not a
    balance producer wearing a presentation hat.

    The fields are declared in the order the rows RENDER, which is the order
    the replay resolves them in (ruling R-AH): a contribution lands on its pay
    period's ``start_date`` and the day's accrual is then taken on the balance
    that day ENDS holding, so the money is contributed and then earns.

    Attributes:
        reconciliation: Whether ruling R-O's "Timing & true-ups" row renders.
        contribution: Whether the "Contributions" row renders.
        accrual: Whether the modelled-return row renders (labelled "Interest" /
            "Growth" / "Appreciation" by the route, ruling R-AI).
    """

    reconciliation: bool
    contribution: bool
    accrual: bool


@dataclass(frozen=True)
class GridBalanceView:
    """Kind-aware cash-flow-surface projection for the budget grid.

    The single view the budget grid reads, regardless of the grid account's
    kind.  For every kind EXCEPT interest-bearing its balances are identical to
    the cash-flow view (:func:`~app.services.balance_at.cash_balance_map`); for
    an INTEREST account they carry the modelled balance plus the per-period
    tiers that keep the grid's rows reconciling.

    Attributes:
        columns: ``OrderedDict`` period_id -> :class:`GridColumn`, in the order
            the caller's *periods* were given.  EVERY requested period is
            present, with a real balance beside its real subtotals -- which is
            why this is one map rather than a balance map that omitted periods
            and a subtotal map that did not.
        amount_overrides: The live ``{transaction_id: Decimal}`` map this
            projection was computed with (recomputed salary income and derived
            loan debits).  Carried so the grid's CELLS render from the same map
            its balance row folded (ruling R-Q) instead of the route building a
            second one, which made them identical only by argument.
    """

    columns: "OrderedDict[int, GridColumn]"
    amount_overrides: "dict[int, Decimal]"

    def row_flags(self, periods: list) -> GridRowFlags:
        """Return which conditional rows *periods* renders (ruling R-O).

        Args:
            periods: The visible pay periods, in display order.  Periods absent
                from :attr:`columns` contribute nothing.

        Returns:
            The window's :class:`GridRowFlags`.
        """
        columns = [
            self.columns[period.id] for period in periods
            if period.id in self.columns
        ]
        # ``!= ZERO`` on every arm, with no ``None`` member beside it: both
        # modelled fields are total ``Decimal``s since plan step X-g3a (ruling
        # R-AJ (c)).  The comparison itself IS ruling R-O's visibility rule and
        # stays load-bearing -- only the impossible-shape half went.
        return GridRowFlags(
            reconciliation=any(
                column.reconciliation != _ZERO_MONEY for column in columns
            ),
            contribution=any(
                column.contribution != _ZERO_MONEY for column in columns
            ),
            accrual=any(
                column.accrual != _ZERO_MONEY for column in columns
            ),
        )


def _cash_only_columns(
    figures: "OrderedDict[int, _cash_fold.CashPeriodFigures]",
) -> "OrderedDict[int, _asset_fold.AssetPeriodFigures]":
    """Return the modelled columns of an account this step does not model.

    The kind gate's OTHER arm, expressed as the modelled record rather than as a
    branch inside :func:`_assemble_columns`: an account the grid still answers
    on the cash basis has the cash balance and BOTH modelled tiers at zero,
    which is exactly what :func:`._asset_fold.resolve` itself returns for an
    account that models nothing.

    **It is spelled here rather than obtained from that producer, and only
    because the gate above it is still in place.**  Asking the replay would
    model an APPRECIATING account -- it reads the appreciation rate off the
    account row, where an INVESTMENT reads the CALLER's ``investment_params``
    (:func:`._asset_fold._modelled_return`) -- and moving a Property's grid
    balance is plan step X-g3b's cutover, not this refactor's.  Both this helper
    and the gate delete there, after which every kind reads the replay and this
    function has nothing left to say.

    Args:
        figures: The cash period view's columns, one per requested period.

    Returns:
        ``OrderedDict`` period id -> :class:`._asset_fold.AssetPeriodFigures`
        over the same keys, carrying the cash balance and two zero tiers.
    """
    return OrderedDict(
        (
            period_id,
            _asset_fold.AssetPeriodFigures(
                balance=column.balance,
                accrual=_ZERO_MONEY,
                contribution=_ZERO_MONEY,
            ),
        )
        for period_id, column in figures.items()
    )


def _assemble_columns(
    periods: list,
    figures: "OrderedDict[int, _cash_fold.CashPeriodFigures]",
    modelled: "OrderedDict[int, _asset_fold.AssetPeriodFigures]",
) -> "OrderedDict[int, GridColumn]":
    """Combine each period's cash and modelled figures into one :class:`GridColumn`.

    Args:
        periods: The pay periods to report, in display order.
        figures: The period view's :class:`~app.services.balance_at._cash_fold.CashPeriodFigures`
            per period (the budget-clock subtotals and ruling R-K's remainder).
            Total over *periods*.
        modelled: The :class:`._asset_fold.AssetPeriodFigures` per period -- the
            DISPLAYED balance and the two modelled tiers.  Total over *periods*,
            so a missing key is a defect rather than a blank column; it is
            indexed, not ``.get``.

    Returns:
        ``OrderedDict`` period id -> :class:`GridColumn`, one per requested
        period.
    """
    columns: "OrderedDict[int, GridColumn]" = OrderedDict()
    for period in periods:
        cash = figures[period.id]
        tiers = modelled[period.id]
        columns[period.id] = GridColumn(
            balance=tiers.balance,
            income=cash.income,
            expense=cash.expense,
            net=cash.net,
            reconciliation=cash.reconciliation,
            contribution=tiers.contribution,
            accrual=tiers.accrual,
        )
    return columns


def grid_balance_view(
    account: Account, ctx: BalanceContext, periods: list,
) -> GridBalanceView:
    """Return the kind-aware cash-flow-surface view for *account*.

    The single entry the budget grid reads to project one account's column set.
    ONE :func:`~app.services.balance_at._cash_fold.assemble` supplies every
    figure the surface renders: :func:`._cash_fold.period_view_of` regroups it
    into the income and expense subtotals and ruling R-K's remainder, and for an
    INTEREST account :func:`._asset_fold.resolve` resolves the modelled tiers
    over the SAME record for the balance, the accrual and the contribution.  So

        balance[p] - balance[p-1]
            == net[p] + reconciliation[p] + contribution[p] + accrual[p]

    is a property of the construction rather than an invariant a test polices
    across three independent producer passes (finding N-48).

    **The sharing is the point, and it is what plan step X-g2a built**
    (Section 4's constraint under ruling R-AA).  Reaching the replay through its
    own entry (:func:`._asset_fold.asset_period_view`) would have walked the
    account, loaded its plan and valued its rows a SECOND time -- undoing plan
    step X-c1's "one walk, one plan load, one valuation, whichever reader is
    asking" for the sake of one extra tier.

    **Only INTEREST accrues** here at this step; ruling R-W generalises it to
    the other modelled kinds at plan step X-g3b, and the module docstring
    carries what that costs in the meantime (finding N-76).  PLAIN carries no
    accrual -- the replay has no ACCRUAL tier to resolve for it, which is the
    same statement its old "its kind-correct balance IS its cash basis" made,
    now made by the producer instead of by a branch.

    **The contribution tier is ZERO in every column at this step, and that is a
    property of the gate rather than of the data.**  Only an INTEREST account
    reaches the replay, and :func:`._asset_contributions.contribution_events`
    returns ``[]`` for every kind but INVESTMENT -- an HYSA's payroll does not
    fund it.  The field is wired through the producer anyway rather than
    hard-coded, so plan step X-g3b moves the figure by deleting the gate and
    supplying the real inputs, not by re-plumbing the column.

    **The accrual is a producer's answer, not a residual** (plan step X-c2b2,
    finding N-52).  It used to be the period-to-period delta of the PREMIUM
    between two independently computed balance maps, which meant any
    disagreement between those maps rendered as interest EARNED: measured on the
    real Money Market, folding the cash map while the accrual still seeded off
    the ``current_anchor_balance`` cache would have shown ``$2,007.01`` of
    interest in the current column -- the ``$2,000.00`` of settled money the
    cache never saw, relabelled (finding N-49).  Both halves now come off ONE
    resolved step list, so the row is the accrual map itself.

    **ONE income basis, and nobody chooses it** (ruling R-Q).  The live override
    map -- recomputed salary income and derived loan debits -- is built inside
    the fold over the account's own plan and returned on the result, so the
    grid's CELLS render from the same map its balance row folded.  It used to be
    an argument threaded through two walks whose ``None``-handling differed by
    kind, which is how one account could be valued on two income bases.

    Args:
        account: The account to project (the grid account; any kind).
            ``classify_account`` drives the accrual dispatch.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        periods: The pay periods to project over, ordered by ``period_index``
            (pass the full set; every one of them is present in the result, and
            each is valued off its OWN span rather than re-based on the
            window's left edge).

    Returns:
        A :class:`GridBalanceView`.

    Raises:
        ValueError: When ``scenario`` is None -- callers that resolve a
            nullable baseline must guard first.
    """
    _require_scenario(ctx)
    if not periods:
        # A user with no pay periods has no columns to render and no rows to
        # price, so the override map describes nothing.  Early-out rather than
        # asking the modelled arm below for a horizon it cannot derive from an
        # empty list -- the same guard :func:`._asset_fold.asset_period_view`
        # and :func:`._asset_fold.period_columns` already carry.
        return empty_grid_view()
    folded = _cash_fold.assemble(account, ctx.scenario.id, ctx.as_of)
    view = _cash_fold.period_view_of(folded, periods)
    if _interest.accrual_params(account) is None:
        modelled = _cash_only_columns(view.columns)
    else:
        modelled = _asset_fold.period_columns(
            _asset_fold.resolve(
                account, folded,
                max(period.end_date for period in periods),
                ContributionInputs.absent(),
            ),
            periods,
        )
    return GridBalanceView(
        columns=_assemble_columns(periods, view.columns, modelled),
        amount_overrides=view.amount_overrides,
    )


def empty_grid_view() -> GridBalanceView:
    """Return the view for a user with no accounts at all.

    The grid renders for a user whose account set is empty (nothing to point
    ``resolve_grid_account`` at), and its templates then have no column to read.
    Returning the empty view from the SEAM rather than constructing one at each
    of the grid's four render entries keeps the no-account shape a property of
    this module -- a route that assembled its own would be a second definition
    of what an absent projection looks like, and the three self-refresh
    endpoints each had one.

    Returns:
        A :class:`GridBalanceView` with no columns and no overrides.
    """
    return GridBalanceView(columns=OrderedDict(), amount_overrides={})
