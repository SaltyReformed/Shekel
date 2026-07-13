"""Balance-at-T seam -- the CASH-FLOW view (no per-kind dispatch).

The single-account cash-flow surfaces -- the budget grid, the obligations
panel, the calendar, the checking detail page -- read an account's pure
transaction running-balance, NOT its kind-correct balance (see the package
docstring's "Two views, one seam").  These three entries are the seam's only
fence-compliant way to obtain that view: thin pass-throughs to the canonical
entries-aware producers, so a cash-flow surface never reaches a balance
producer directly.
"""

from collections import OrderedDict
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.scenario import Scenario
from app.services import (
    balance_resolver,
    daily_balance_series,
)

from ._inputs import _require_scenario


def cash_balance_map(
    account: Account,
    scenario: Scenario,
    periods: list,
    *,
    amount_overrides: "dict[int, Decimal] | None" = None,
) -> balance_resolver.BalanceResult:
    """Return one account's cash-flow running balance across *periods*.

    The cash-flow view: the account's projected end balance per period as a
    pure transaction running-balance (the anchor carried forward by each
    period's Projected, entry-aware net), with NO per-kind dispatch.  This
    is what the single-account cash-flow surfaces show -- the budget grid,
    the obligations cash-flow panel, and the checking detail page -- where
    the balance row must reconcile with the account's own transaction rows
    and subtotal row on the same screen.

    Contrast with :func:`~app.services.balance_at.balance_map`, the
    KIND-CORRECT view: for an interest-bearing (HYSA), loan, investment, or
    property account ``balance_map`` dispatches to that kind's engine (accruing
    interest, walking an amortization schedule, compounding growth /
    appreciation) -- which is what the net-worth surfaces want, but would
    break a cash-flow surface.  Accruing interest into the grid's balance
    row while its subtotal row stays transaction-based would violate the
    grid's ``balances[p] - balances[p-1] == subtotals[p].net`` invariant,
    and the grid account is not always cash (``resolve_grid_account`` can
    return any kind).  So these surfaces ask for the cash-flow balance of
    whatever account they are pointed at, regardless of its kind.

    Delegates to :func:`~app.services.balance_resolver.balances_for` -- the
    canonical entries-aware producer -- and returns its
    :class:`~app.services.balance_resolver.BalanceResult` verbatim, so the
    caller also gets the ``stale_anchor_warning`` flag the grid surfaces in
    its banner (a data-quality signal ABOUT the projection, not a balance,
    so it rides on the result rather than becoming a separate seam concern).

    ``amount_overrides`` passes straight through to ``balances_for`` (the
    grid threads its pre-built live projected-income map here); ``None``
    (the default) lets the producer build its own, byte-identical to the
    prior direct call.

    Args:
        account: The account whose cash-flow balance to project.  Its
            ``user_id`` scopes the producer; its kind is NOT consulted.
        scenario: The baseline scenario.
        periods: The pay periods to project over, ordered by
            ``period_index`` (must include the anchor period; pre-anchor
            periods are omitted from the result by the producer).
        amount_overrides: Optional ``{transaction_id: Decimal}`` live
            projected-net map (the grid threads its pre-built map here).

    Returns:
        The :class:`~app.services.balance_resolver.BalanceResult`: the
        period_id -> Decimal balance map plus the ``stale_anchor_warning``
        flag.

    Raises:
        ValueError: When ``scenario`` is None -- callers that resolve a
            nullable baseline must guard first.
    """
    _require_scenario(scenario)
    return balance_resolver.balances_for(
        account, scenario.id, periods, amount_overrides=amount_overrides,
    )


def cash_balance_at(
    account: Account, scenario: Scenario, as_of: date,
) -> Decimal:
    """Return one account's cash-flow balance as of a calendar date *as_of*.

    The scalar cash-flow view -- the date-precise counterpart of
    :func:`cash_balance_map`.  Delegates to
    :func:`~app.services.balance_resolver.balance_as_of_date`, which sums
    the account's Projected, entry-aware transaction rows up to *as_of*
    (intra-period precise: entries dated after *as_of* are excluded).  Used
    by the calendar's month-end balance, which must reconcile with the day
    cells it renders for the same month.

    Like :func:`cash_balance_map`, this does NOT dispatch by kind: it is
    the cash-flow balance of whatever account the surface points at (the
    calendar's account can be any kind via an explicit ``account_id``).
    The KIND-CORRECT scalar is :func:`~app.services.balance_at.balance_at`.

    Args:
        account: The account to value.  Its kind is NOT consulted.
        scenario: The baseline scenario (its id scopes the producer).
        as_of: The calendar date to value the account at.

    Returns:
        The ``Decimal`` cash-flow balance at *as_of*, quantized to cents by
        the producer.

    Raises:
        ValueError: When ``scenario`` is None -- callers that resolve a
            nullable baseline must guard first.
        TypeError: When ``as_of`` is not a :class:`datetime.date` (raised by
            the underlying producer).
    """
    _require_scenario(scenario)
    return balance_resolver.balance_as_of_date(account, scenario.id, as_of)


def cash_daily_balance_series(
    account: Account,
    scenario: Scenario,
    first_day: date,
    last_day: date,
    *,
    amount_overrides: "dict[int, Decimal] | None" = None,
) -> "OrderedDict[date, Decimal]":
    """Return one account's projected end-of-day cash-flow balance per day.

    The daily-granularity cash-flow view -- the running-balance counterpart
    of the period-flat :func:`cash_balance_at`.  Delegates to
    :func:`app.services.daily_balance_series.build_daily_series`, which walks
    each calendar day in ``[first_day, last_day]`` as a true checkbook
    balance that steps on that day's projected, period-clamped, entry-aware
    flows and reconciles with the grid at every period end
    (``series[P.end_date] == cash_balance_at(account, scenario, P.end_date)``).

    Like :func:`cash_balance_at` this does NOT dispatch by kind: it is the
    cash-flow balance of whatever account the surface points at (the
    calendar's account can be any kind via an explicit ``account_id``).  Used
    by the analytics calendar's flow strip and day-cell end-of-day balances.

    Args:
        account: The account to project.  Its kind is NOT consulted; must be
            session-attached.
        scenario: The baseline scenario (its id scopes the producer).
        first_day: Inclusive first calendar day of the range.
        last_day: Inclusive last calendar day of the range.
        amount_overrides: Optional ``{transaction_id: Decimal}`` live
            projected-net map, forwarded to the producer (built there when
            None, so income is live by default).

    Returns:
        An ``OrderedDict`` mapping each calendar ``date`` in the inclusive
        range (ascending) to its projected end-of-day cash-flow balance,
        quantized to cents.  An inverted range yields an empty map.

    Raises:
        ValueError: When ``scenario`` is None -- callers that resolve a
            nullable baseline must guard first.
        TypeError: When ``first_day`` / ``last_day`` are not
            :class:`datetime.date`.
    """
    _require_scenario(scenario)
    return daily_balance_series.build_daily_series(
        account, scenario.id, first_day, last_day,
        amount_overrides=amount_overrides,
    )
