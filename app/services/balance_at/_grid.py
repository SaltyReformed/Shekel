"""Balance-at-T seam -- the GRID / OBLIGATIONS kind-aware cash-flow view.

The budget grid and the dashboard obligations panel are single-account
cash-flow surfaces (they read the CASH-FLOW view, :mod:`._cash_flow`), but they
are NOT always pointed at a cash account (``resolve_grid_account`` falls back to
any active account).  For an INTEREST-bearing grid account (HYSA / Money Market
/ CD / HSA) the pure transaction running-balance understates the real balance,
because it ignores the interest the net-worth surfaces already accrue.  This
view gives such an account the interest-accrued balance AND a per-period
interest figure that explains the part of the balance change the transactions
do not -- so the grid's balance row still reconciles with its transaction
subtotal row.

ONLY INTEREST accrues here.  INTEREST is the one non-cash kind whose balance is
a transaction SUM (anchor + the account's rows) plus a layered accrual, so a row
the user types on the editable grid flows into the projected balance and the
accrual row reconciles.  The other non-cash kinds are deliberately left on the
cash-flow view because their balance is NOT a transaction sum -- a typed grid
row would not move it:

* AMORTIZING (loan) -- the amortization schedule drives the balance (principal
  paydown), while its grid "transactions" are payment transfers recorded as
  income (opposite sign, different magnitude); no single accrual row reconciles
  them.
* INVESTMENT / APPRECIATING -- the growth / appreciation projection drives the
  balance (anchor + modeled contributions, compounded), so an ad-hoc grid row
  lands in the cash basis but not the projected balance.  Their modeled value is
  shown on the /savings cockpit and detail pages, where the projection is
  read-only.

PLAIN likewise carries no accrual (its kind-correct balance IS its cash basis).
The implementation plan's known-limitation note records the full rationale and
the deferred options for the projection-driven kinds.
"""

from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal

from app.models.account import Account
from app.models.scenario import Scenario
from app.services import balance_resolver
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.utils.money import round_money

from . import _cash_flow, _kind_correct
from ._inputs import ZERO, _require_scenario


@dataclass(frozen=True)
class GridBalanceView:
    """Kind-aware cash-flow-surface projection for the grid + obligations panel.

    The single view the budget grid (and the dashboard obligations panel,
    the grid's mirrored cash-flow projection) reads, regardless of the grid
    account's kind.  For every kind EXCEPT interest-bearing it is
    byte-identical to the cash-flow view
    (:func:`~app.services.balance_at.cash_balance_map`); for an INTEREST account
    it carries the interest-accrued balance plus a per-period interest accrual
    that keeps the grid's rows reconciling.

    Attributes:
        balances: ``OrderedDict`` period_id -> the projected end balance the
            surface displays, cent-quantized.  For an INTEREST account this is
            the interest-accrued balance; for every other kind it is the
            cash-flow running-balance.  Pre-anchor periods are absent (matching
            the cash producer), so the grid's pre-anchor "--" display is
            consistent across kinds.
        stale_anchor_warning: The cash producer's stale-anchor flag (a
            data-quality signal about settled post-anchor activity).  Always
            taken from the cash walk -- it is interest-independent, so the
            interest path never has to recompute it.
        increments: ``OrderedDict`` period_id -> the per-period interest
            accrual (the "Interest" row value), cent-quantized.  EMPTY for
            every non-interest kind (no accrual row).  By construction
            ``balances[p] - balances[p-1] == period_subtotal[p].net +
            increments[p]`` to the cent (see
            :func:`~app.services.balance_at.grid_balance_view`).
    """

    balances: "OrderedDict[int, Decimal]"
    stale_anchor_warning: bool
    increments: "OrderedDict[int, Decimal]"


def _accruing_grid_view(
    kc_balances: "OrderedDict[int, Decimal]",
    cash_result: balance_resolver.BalanceResult,
) -> GridBalanceView:
    """Assemble a :class:`GridBalanceView` for an INTEREST account.

    Displays the rounded interest-accrued balance per period and a per-period
    accrual (interest) equal to the PREMIUM over the no-interest cash basis,
    deltaed period to period:

        premium[p]   = round_money(interest_accrued[p]) - cash_basis[p]
        increment[p] = premium[p] - premium[previous present period]

    with the premium taken as ``0`` before the first present period (no
    accrual before the anchor).  Because the cash basis equals the grid's
    transaction subtotal roll-forward to the cent (the E-25 invariant
    ``cash[p] - cash[q] == period_subtotal[p].net``), this makes the three
    displayed rows reconcile exactly:

        balances[p] - balances[q]
          == (round_money(kc[p]) - round_money(kc[q]))
          == (cash[p] - cash[q]) + increment[p]
          == period_subtotal[p].net + increment[p].

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
        cash_result: The cash-flow :class:`~app.services.balance_resolver.BalanceResult`
            (its cent-quantized ``balances`` are the premium baseline, and
            its ``stale_anchor_warning`` rides through unchanged).

    Returns:
        The assembled :class:`GridBalanceView`.
    """
    balances: "OrderedDict[int, Decimal]" = OrderedDict()
    increments: "OrderedDict[int, Decimal]" = OrderedDict()
    prev_premium = ZERO
    # ``cash_result.balances`` is already cent-quantized by ``balances_for``
    # and ordered anchor-forward; iterating it (not ``kc_balances``) is what
    # drops an investment's pre-anchor reverse-projection.
    for period_id, cash_balance in cash_result.balances.items():
        kc_balance = kc_balances.get(period_id)
        if kc_balance is None:
            # Reachable only under anchor-cache divergence: resolve_anchor's
            # dated-SoT anchor is earlier than the cache anchor the kernel
            # interest path seeds from (the logged EVT_ANCHOR_CACHE_RECONCILED
            # state), so the cash map carries a leading PREFIX of periods the
            # kind-correct map lacks.  Show the cash balance there with no
            # accrual, and hold the premium baseline at ZERO so the first
            # real kind-correct period's increment is measured from a clean
            # baseline regardless of where the gap falls (never an
            # interest-inflated wrong number).
            balances[period_id] = cash_balance
            increments[period_id] = ZERO
            prev_premium = ZERO
            continue
        rounded_kc = round_money(kc_balance)
        balances[period_id] = rounded_kc
        premium = rounded_kc - cash_balance
        increments[period_id] = premium - prev_premium
        prev_premium = premium
    return GridBalanceView(
        balances=balances,
        stale_anchor_warning=cash_result.stale_anchor_warning,
        increments=increments,
    )


def grid_balance_view(
    account: Account,
    scenario: Scenario,
    periods: list,
    *,
    amount_overrides: "dict[int, Decimal] | None" = None,
) -> GridBalanceView:
    """Return the kind-aware cash-flow-surface view for *account*.

    The single entry the budget grid and the obligations panel read to
    project one account's balance, dispatching on the account's kind:

    * **INTEREST** -- the interest-accrued balance
      (:func:`~app.services.balance_at.balance_map`) plus a per-period interest
      accrual that explains the part of each period's balance change the
      transactions do not.  The accrual reconciles the grid's rows to the cent
      (:func:`_accruing_grid_view`).
    * **Every other kind (PLAIN / AMORTIZING / INVESTMENT / APPRECIATING)** --
      the cash-flow running-balance, byte-identical to
      :func:`~app.services.balance_at.cash_balance_map` (no accrual row).  Only
      INTEREST accrues on the grid because only its balance is a transaction sum
      (so a typed row flows into it); the others are cash- or projection-driven
      (see the module docstring and the implementation plan's known-limitation
      note).

    The cash walk runs for every kind -- it supplies the stale-anchor flag
    and, for INTEREST, the premium baseline.  Only an INTEREST grid account
    additionally runs the kind-correct walk, so every other kind is exactly
    one walk (unchanged from before this view existed).

    Income basis.  Both walks must apply ONE income basis or the premium would
    absorb an income mismatch instead of being pure interest.  When a caller
    threads ``amount_overrides`` (the grid does) both walks apply it -- live
    income, and the balance row reconciles with the grid's live-income
    subtotal row.  For the ``None`` default the cash walk (``balances_for``)
    auto-builds a live map but the kc walk
    (``calculate_balances_with_interest``) does NOT, so ``None`` is normalized
    to an empty map and BOTH walks use stored income (pure premium).  An
    interest account viewed via ``None`` therefore reports its STORED-income
    balance; thread a live map for live income.

    Args:
        account: The account to project (the grid / obligations account; any
            kind).  ``classify_account`` drives the dispatch.
        scenario: The baseline scenario.
        periods: The pay periods to project over, ordered by
            ``period_index`` (pass the full anchor-forward set so the
            previous-period premium baseline is available at the window's
            left edge).
        amount_overrides: Optional ``{transaction_id: Decimal}`` live
            projected-net map (the grid threads its pre-built map here).  For
            an INTEREST account ``None`` is treated as an empty map (stored
            income); thread a live map to put it on live income.  Ignored for
            non-interest kinds (they take the cash-flow walk, which builds its
            own from ``None``).

    Returns:
        A :class:`GridBalanceView`.

    Raises:
        ValueError: When ``scenario`` is None -- callers that resolve a
            nullable baseline must guard first.
    """
    _require_scenario(scenario)
    if classify_account(account) is AccountProjectionKind.INTEREST:
        # Thread ONE income basis to both walks so the premium is pure
        # interest, not an income mismatch.  The cash walk (balances_for)
        # auto-builds a live income map from None but the kc walk
        # (calculate_balances_with_interest) does NOT -- so an INTEREST account
        # left on None reads cash=live vs kc=stored and pollutes the premium.
        # Normalizing None to an empty map puts BOTH walks on stored income
        # (pure premium).  The grid threads its pre-built live map, putting
        # both walks (and its subtotal row) on live income.
        overrides = {} if amount_overrides is None else amount_overrides
        cash_result = _cash_flow.cash_balance_map(
            account, scenario, periods, amount_overrides=overrides,
        )
        kc_balances = _kind_correct.balance_map(
            account, scenario, periods, amount_overrides=overrides,
        )
        if kc_balances is not None:
            return _accruing_grid_view(kc_balances, cash_result)
    else:
        cash_result = _cash_flow.cash_balance_map(
            account, scenario, periods, amount_overrides=amount_overrides,
        )
    return GridBalanceView(
        balances=cash_result.balances,
        stale_anchor_warning=cash_result.stale_anchor_warning,
        increments=OrderedDict(),
    )
