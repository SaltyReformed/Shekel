"""Balance-at-T seam -- the GRID kind-aware cash-flow view.

The budget grid is a single-account cash-flow surface (it reads the CASH-FLOW
view, :mod:`._cash_flow`), but it is NOT always pointed at a cash account
(``resolve_grid_account`` falls back to any active account).  For an
INTEREST-bearing grid account (HYSA / Money Market / CD / HSA) the pure
transaction running-balance understates the real balance, because it ignores
the interest the net-worth surfaces already accrue.  This view gives such an
account the interest-accrued balance AND a per-period interest figure that
explains the part of the balance change the transactions do not -- so the
grid's balance row still reconciles with its transaction subtotal row.

**ONE per-period column, not four parallel maps** (plan step X-c2b1, ruling
R-K).  Every figure the grid renders for one pay period -- the projected end
balance, the income and expense subtotals, the "Timing & true-ups" remainder
and the interest accrual -- is one :class:`GridColumn`, because they are one
row set read three ways and the identity binding them is

    balance[p] - balance[p-1] == net[p] + reconciliation[p] + interest[p]

Carrying them as separate period-keyed dicts is what let the grid compute them
in three independent producer passes that a test then had to keep in step
(finding N-48: ``338.0 ms`` of producer work per render, and an invariant
asserted rather than constructed).  One column makes the identity a property of
the object the template reads.

ONLY INTEREST accrues here.  INTEREST is the one non-cash kind whose balance is
a transaction SUM (anchor + the account's rows) plus a layered accrual, so a row
the user types on the editable grid flows into the projected balance and the
accrual row reconciles.  The other non-cash kinds are deliberately left on the
cash-flow view because their balance is NOT a transaction sum -- a typed grid
row would not move it:

* AMORTIZING (loan) -- the amortization schedule drives the balance (principal
  paydown), while its grid "transactions" are payment transfers recorded as
  income (opposite sign, different magnitude); no single accrual row reconciles
  them.  Ruling D4 refuses one at the resolver anyway, so this branch is the
  degenerate safety, not a supported view.
* INVESTMENT / APPRECIATING -- the growth / appreciation projection drives the
  balance (anchor + modeled contributions, compounded), so an ad-hoc grid row
  lands in the cash basis but not the projected balance.  Their modeled value is
  shown on the /savings cockpit and detail pages, where the projection is
  read-only.

PLAIN likewise carries no accrual (its kind-correct balance IS its cash basis).
"""

from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal

from app.models.account import Account
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.cash_ledger import (
    live_amount_overrides,
    load_balance_transactions,
    period_subtotals,
)
from app.utils.money import round_money

from ._context import BalanceContext
from . import _cash_flow, _kind_correct
from ._inputs import ZERO, _require_scenario

# The remainder ruling R-K names, while the SHIPPING producers still answer
# (plan step X-c2b1).  It is ``0.00`` as a measured property of those
# producers, not as a placeholder: the balance row and the subtotal row both
# count exactly the still-UNPAID rows of one anchor-seeded walk, through the
# same ``sum_projected`` engine, so ``balances[p] - balances[p-1] ==
# subtotals[p].net`` holds to the cent (the E-25 invariant, pinned by
# ``test_balance_at.py::TestTheRemainderIsZeroUnderTheShippingProducers``).
# Nothing the two clocks disagree about can reach either side yet, because
# neither side can see a settled row at all.  Plan step X-c2b2 points this at
# ``_cash_fold.cash_period_view``'s independently-computed remainder, where the
# figure stops being zero (measured on the prod-shape clone: ``-$788.68`` in
# the real Checking account's current column).
_NO_REMAINDER = Decimal("0.00")


@dataclass(frozen=True)
class GridColumn:
    """Every figure the grid renders for ONE pay period.

    The per-period unit of :class:`GridBalanceView`, and ruling R-K's row set
    expressed as one record: the same valued rows grouped on the budget clock
    (:attr:`income` / :attr:`expense` / :attr:`net`), what the cash clock and
    the assertions add on top of that (:attr:`reconciliation`), the modelled
    accrual (:attr:`interest`), and the balance all three roll forward to
    (:attr:`balance`).

    Attributes:
        balance: The projected end balance the surface displays, cent-quantized
            -- the interest-accrued balance for an INTEREST account, the
            cash-flow running balance for every other kind.  ``None`` for a
            period the projection does not reach (pre-anchor), which the grid
            renders as ``--``.
        income: The period's projected income subtotal, cent-quantized.
        expense: The period's projected expense subtotal (a magnitude), so the
            column reads ``income`` minus ``expense``.
        net: ``round_money(income - expense)`` -- rounded ONCE at the boundary
            rather than as the difference of two separately-rounded legs,
            because it is the figure the balance roll-forward has to reconcile
            with.
        reconciliation: Ruling R-K's remainder, rendered as "Timing &
            true-ups": money budgeted to this period that moved in another (or
            has not moved yet), money that moved here but is budgeted
            elsewhere, and the balance ASSERTIONS made inside the period.
        interest: The period's modelled interest accrual (the read-only
            "Interest" row), or ``None`` for an account that models none and
            for a period the accrual map does not cover.
    """

    balance: "Decimal | None"
    income: Decimal
    expense: Decimal
    net: Decimal
    reconciliation: Decimal
    interest: "Decimal | None"


@dataclass(frozen=True)
class GridRowFlags:
    """Which CONDITIONAL rows a given visible window renders.

    Ruling R-O's visibility rule, stated ONCE for both rows it governs: a
    conditional row is present for the whole visible window when at least one
    visible column carries a non-zero value, and shows its own figure (``$0.00``
    included) in every column of that window.  Rejected at the ruling: always-on
    (a permanently-zero row on the forward-looking windows, which are the ones
    most used) and past/current-only (an all-zero PAST window then reads as "not
    measured" rather than "nothing to explain").

    The rule lives here rather than in each template because it is the same
    rule for both rows on four different windows (the visible grid, the Plan
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

    Attributes:
        interest: Whether the "Interest" accrual row renders.
        reconciliation: Whether ruling R-O's "Timing & true-ups" row renders.
    """

    interest: bool
    reconciliation: bool


@dataclass(frozen=True)
class GridBalanceView:
    """Kind-aware cash-flow-surface projection for the budget grid.

    The single view the budget grid reads, regardless of the grid account's
    kind.  For every kind EXCEPT interest-bearing its balances are identical to
    the cash-flow view (:func:`~app.services.balance_at.cash_balance_map`); for
    an INTEREST account they carry the interest-accrued balance plus a
    per-period accrual that keeps the grid's rows reconciling.

    Attributes:
        columns: ``OrderedDict`` period_id -> :class:`GridColumn`, in the order
            the caller's *periods* were given.  EVERY requested period is
            present; a period the projection cannot reach carries
            ``balance=None`` with its real subtotals beside it, which is why
            this is one map rather than a balance map that omits periods and a
            subtotal map that does not.
        stale_anchor_warning: The cash producer's stale-anchor flag (a
            data-quality signal about settled post-anchor activity).  Always
            taken from the cash walk -- it is interest-independent, so the
            interest path never has to recompute it.  Deletes at plan step
            X-c2b2, where the fold counts those rows and the warning has
            nothing left to warn about.
        amount_overrides: The live ``{transaction_id: Decimal}`` map this
            projection was computed with (recomputed salary income and derived
            loan debits).  Carried so the grid's CELLS render from the same map
            its balance row folded (ruling R-Q) instead of the route building a
            second one, which made them identical only by argument.
    """

    columns: "OrderedDict[int, GridColumn]"
    stale_anchor_warning: bool
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
        return GridRowFlags(
            interest=any(
                column.interest not in (None, ZERO) for column in columns
            ),
            reconciliation=any(
                column.reconciliation != ZERO for column in columns
            ),
        )


def _accruing_balances(
    kc_balances: "OrderedDict[int, Decimal]",
    cash_balances: "OrderedDict[int, Decimal]",
) -> "tuple[OrderedDict[int, Decimal], dict[int, Decimal]]":
    """Return an INTEREST account's displayed balances and per-period accrual.

    Displays the rounded interest-accrued balance per period and a per-period
    accrual (interest) equal to the PREMIUM over the no-interest cash basis,
    deltaed period to period::

        premium[p]   = round_money(interest_accrued[p]) - cash_basis[p]
        increment[p] = premium[p] - premium[previous present period]

    with the premium taken as ``0`` before the first present period (no accrual
    before the anchor).  Because the cash basis equals the grid's transaction
    subtotal roll-forward to the cent (the E-25 invariant ``cash[p] - cash[q]
    == subtotals[p].net``), this makes the displayed rows reconcile exactly::

        balance[p] - balance[q]
          == (round_money(kc[p]) - round_money(kc[q]))
          == (cash[p] - cash[q]) + increment[p]
          == subtotals[p].net + increment[p].

    **The subtraction is transitional, and it is what plan step X-c2b2
    deletes** (finding N-52).  The accrual is a DIFFERENCE of two
    independently-computed balance maps only because the cash basis and the
    interest-accrued balance come from two separate walks today; once both
    derive from ONE fold, the increment is the accrual map ``_layer_interest``
    already returns and this function goes with the divergence branch below.

    Iterates the cash producer's period set (anchor-forward).  For INTEREST
    the interest-accrued and cash maps cover the SAME anchor-forward periods
    (one transaction walk, with vs without layered interest), so the only case
    where a cash period is missing from ``kc_balances`` is the rare
    anchor-cache divergence handled in the loop.

    Args:
        kc_balances: The interest-accrued balance map from
            :func:`~app.services.balance_at.balance_map`.  Covers the same
            anchor-forward periods as the cash producer, except a possible
            leading anchor-cache-divergence prefix (cash periods the interest
            map lacks), which the loop handles.
        cash_balances: The cent-quantized cash-flow balances (the premium
            baseline).

    Returns:
        ``(balances, accrual)`` -- the displayed balance per period and the
        interest earned in it, both keyed by period id.
    """
    balances: "OrderedDict[int, Decimal]" = OrderedDict()
    accrual: "dict[int, Decimal]" = {}
    prev_premium = ZERO
    # ``cash_balances`` is already cent-quantized by ``balances_for`` and
    # ordered anchor-forward; iterating it (not ``kc_balances``) is what drops
    # an investment's pre-anchor reverse-projection.
    for period_id, cash_balance in cash_balances.items():
        kc_balance = kc_balances.get(period_id)
        if kc_balance is None:
            # Reachable only under anchor-cache divergence: resolve_anchor's
            # dated-SoT anchor is earlier than the cache anchor the kernel
            # interest path seeds from (the logged EVT_ANCHOR_CACHE_RECONCILED
            # state), so the cash map carries a leading PREFIX of periods the
            # kind-correct map lacks.  Show the cash balance there with no
            # accrual, and hold the premium baseline at ZERO so the first real
            # kind-correct period's increment is measured from a clean baseline
            # regardless of where the gap falls (never an interest-inflated
            # wrong number).
            balances[period_id] = cash_balance
            accrual[period_id] = ZERO
            prev_premium = ZERO
            continue
        rounded_kc = round_money(kc_balance)
        balances[period_id] = rounded_kc
        premium = rounded_kc - cash_balance
        accrual[period_id] = premium - prev_premium
        prev_premium = premium
    return balances, accrual


def _assemble_columns(
    periods: list,
    balances: "OrderedDict[int, Decimal]",
    subtotals: dict,
    accrual: "dict[int, Decimal]",
) -> "OrderedDict[int, GridColumn]":
    """Combine the per-period figures into one :class:`GridColumn` each.

    Args:
        periods: The pay periods to report, in display order.
        balances: The displayed balance per period; a period absent from it is
            one the projection does not reach (pre-anchor) and reports
            ``balance=None``.
        subtotals: The period-keyed subtotal records (``.income`` / ``.expense``
            / ``.net``).  Total over *periods*.
        accrual: The per-period interest, empty for an account that models
            none.

    Returns:
        ``OrderedDict`` period id -> :class:`GridColumn`, one per requested
        period.
    """
    columns: "OrderedDict[int, GridColumn]" = OrderedDict()
    for period in periods:
        subtotal = subtotals[period.id]
        columns[period.id] = GridColumn(
            balance=balances.get(period.id),
            income=subtotal.income,
            expense=subtotal.expense,
            net=subtotal.net,
            reconciliation=_NO_REMAINDER,
            interest=accrual.get(period.id),
        )
    return columns


def grid_balance_view(
    account: Account, ctx: BalanceContext, periods: list,
) -> GridBalanceView:
    """Return the kind-aware cash-flow-surface view for *account*.

    The single entry the budget grid reads to project one account's column set,
    dispatching on the account's kind:

    * **INTEREST** -- the interest-accrued balance
      (:func:`~app.services.balance_at.balance_map`) plus a per-period interest
      accrual that explains the part of each period's balance change the
      transactions do not (:func:`_accruing_balances`).
    * **Every other kind (PLAIN / AMORTIZING / INVESTMENT / APPRECIATING)** --
      the cash-flow running-balance, identical to
      :func:`~app.services.balance_at.cash_balance_map`, with no accrual.  Only
      INTEREST accrues on the grid because only its balance is a transaction sum
      (so a typed row flows into it); the others are cash- or projection-driven
      (see the module docstring).

    **ONE income basis, and the caller no longer chooses it** (ruling R-Q).
    The live override map -- recomputed salary income and derived loan debits --
    is built HERE, once, over the account's own contributing rows, and threaded
    into every walk this view runs AND returned on the result so the grid's
    cells render from the same map its balance row folded.  It used to be the
    caller's argument, which is how the two walks could land on two bases: the
    cash walk auto-builds a live map from ``None`` while the interest walk does
    not, so an INTEREST account left on the default read cash=live against
    kc=stored and the premium absorbed an income mismatch instead of being pure
    interest.  With one map built at the one place that runs both walks, that
    divergence has no argument to arrive through -- an argument a caller can get
    wrong is a defect, not a contract (plan Section 8).  Measured on the
    prod-shape clone 2026-07-26: the stored and live bases differ on ZERO of 60
    columns for every real account, so removing the choice moves nothing.

    The cash walk runs for every kind -- it supplies the stale-anchor flag and,
    for INTEREST, the premium baseline.  Only an INTEREST grid account
    additionally runs the kind-correct walk.

    Args:
        account: The account to project (the grid account; any kind).
            ``classify_account`` drives the dispatch.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        periods: The pay periods to project over, ordered by ``period_index``
            (pass the full anchor-forward set so the previous-period premium
            baseline is available at the window's left edge; every one of them
            is present in the result).

    Returns:
        A :class:`GridBalanceView`.

    Raises:
        ValueError: When ``scenario`` is None -- callers that resolve a
            nullable baseline must guard first.
    """
    _require_scenario(ctx)
    scenario_id = ctx.scenario.id
    overrides = live_amount_overrides(
        account,
        scenario_id,
        load_balance_transactions(
            account, scenario_id, [period.id for period in periods],
        ),
    )
    cash_result = _cash_flow.cash_balance_map(
        account, ctx, periods, amount_overrides=overrides,
    )
    balances = cash_result.balances
    accrual: "dict[int, Decimal]" = {}
    if classify_account(account) is AccountProjectionKind.INTEREST:
        kc_balances = _kind_correct.balance_map(
            account, ctx, periods, amount_overrides=overrides,
        )
        if kc_balances is not None:
            balances, accrual = _accruing_balances(
                kc_balances, cash_result.balances,
            )
    return GridBalanceView(
        columns=_assemble_columns(
            periods,
            balances,
            period_subtotals(
                account, scenario_id, periods, amount_overrides=overrides,
            ),
            accrual,
        ),
        stale_anchor_warning=cash_result.stale_anchor_warning,
        amount_overrides=overrides,
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
        A :class:`GridBalanceView` with no columns, no warning, and no
        overrides.
    """
    return GridBalanceView(
        columns=OrderedDict(), stale_anchor_warning=False, amount_overrides={},
    )
