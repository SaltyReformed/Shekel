"""
Shekel Budget App -- Obligations cash-flow projection producer.

The ``/obligations`` summary used to show three flat "monthly" figures
(Monthly Income, Monthly Outflows, Net Cash Flow) built from recurring
templates via ``obligations_aggregator``.  That presentation was
misleading for two independent reasons, both confirmed against real
data during the DH cash-flow investigation:

  1. The income figure was a flat ``template.default_amount * 26/12``
     with no time dimension, so it could not reflect a salary profile's
     scheduled raises (COLA / merit).  The grid recomputes net pay PER
     period (``income_service.live_projected_net`` ->
     ``paycheck_calculator.project_salary`` -> ``apply_raises``), so a
     user with raises saw a growing grid balance contradicted by a flat,
     understated obligations income.
  2. Net Cash Flow subtracted internal transfers (savings contributions,
     loan payments) as outflows without their offsetting inflows, while
     the grid is per-account and transfer-symmetric (each transfer's two
     shadow legs are real transactions).

A single flat "monthly" scalar also cannot honestly represent a cash
flow that is lumpy period-to-period (annual bills land in one period)
and trends upward across years (raises compound): its sign depends on
the averaging window.

This producer replaces that scalar with a projection that reuses the
grid's exact resolution + balance engine, so the obligations panel
reconciles with the grid by construction: the same Checking-by-default
account, the same baseline scenario, the same balance-at seam cash-flow
walk (``balance_at.cash_balance_map``, the grid's producer too -- raise-aware
income, entry-aware expenses, transfer-symmetric).  It surfaces the
projected end balance now, in ~12 months, and at the end of the
projection, plus how many periods dip below zero.

Boundary discipline (``CLAUDE.md`` Architecture): no Flask imports;
inputs are plain data (user id + an optional ``UserSettings`` row),
output is a frozen dataclass of ``Decimal`` / ``date`` / ``int``.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models.user import UserSettings
from app.services import balance_at, balance_resolver, pay_period_service
from app.services.account_resolver import resolve_grid_account
from app.services.scenario_resolver import get_baseline_scenario

logger = logging.getLogger(__name__)

# A "12-month" horizon marker is the projected balance roughly one year
# out.  Measured in calendar days (not 26 pay periods) so the marker
# tracks the calendar year the user thinks in, independent of where the
# anchor falls within its period.
DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class BalanceMarker:
    """A projected end balance at the date it is reached.

    The cohesive ``(balance, date)`` value the projection reports at its
    forward markers (~12 months out, end of projection).  ``as_of_date``
    is the owning period's ``end_date`` -- the day that projected balance
    is reached.
    """

    balance: Decimal
    as_of_date: date


@dataclass(frozen=True)
class CashFlowProjection:
    """Immutable grid-reconciled cash-flow projection for one account.

    Returned by :func:`project_cash_flow`.  Every balance is a projected
    END balance produced by the balance-at seam's cash-flow entry
    :func:`app.services.balance_at.cash_balance_map`, so the figures match
    the ``/grid`` Projected End Balance footer for the same account and
    scenario.

    Attributes:
        account_name: Display name of the projected account (the grid's
            default account, Checking unless the user overrode it).
        now_balance: The account's real anchor balance -- the starting
            point every projection flows forward from.
        twelve_month: :class:`BalanceMarker` for the period roughly one
            calendar year out, or ``None`` when the projection has no
            forward period (a user with no periods past the anchor).
        end: :class:`BalanceMarker` for the last period in the
            projection, or ``None`` when there is no forward period.
        negative_period_count: How many forward periods have a projected
            end balance below zero -- the count the panel flags so a user
            sees the near-term dips an average would hide.
        direction: ``"growing"`` / ``"declining"`` / ``"flat"`` from
            comparing the end balance to ``now_balance``; ``"flat"`` when
            they are equal or there is no forward period.
    """

    account_name: str
    now_balance: Decimal
    twelve_month: BalanceMarker | None
    end: BalanceMarker | None
    negative_period_count: int
    direction: str


def _direction(now_balance: Decimal, end_balance: Decimal | None) -> str:
    """Classify the projection trend from start vs end balance.

    Returns ``"flat"`` when ``end_balance`` is ``None`` (no forward
    period to compare) or exactly equals ``now_balance``; otherwise
    ``"growing"`` or ``"declining"``.
    """
    if end_balance is None or end_balance == now_balance:
        return "flat"
    return "growing" if end_balance > now_balance else "declining"


def _summarize_forward(forward, balances, twelve_month_cutoff):
    """Build the ~12-month and end markers plus the negative-period count.

    ``forward`` is the current-and-later period list (each present in
    ``balances``).  The ~12-month marker is the projected balance AS OF
    one year out: the last forward period whose ``end_date`` is on or
    before ``twelve_month_cutoff`` (a period still in progress at the
    cutoff has not posted its end balance yet).  Selecting by ``end_date``
    rather than ``start_date`` keeps the figure the balance reached by the
    one-year mark, not a later period's recovery from a near-term dip --
    the account balance swings widely within each cycle as lumpy expenses
    land.  Falls back to the first forward period when the projection is
    shorter than a year.

    Returns ``(twelve_month_marker, end_marker, negative_period_count)``.
    """
    twelve_month_period = forward[0]
    for period in forward:
        if period.end_date <= twelve_month_cutoff:
            twelve_month_period = period
        else:
            break
    end_period = forward[-1]
    negative_period_count = sum(
        1 for period in forward if balances[period.id] < 0
    )
    return (
        BalanceMarker(balances[twelve_month_period.id], twelve_month_period.end_date),
        BalanceMarker(balances[end_period.id], end_period.end_date),
        negative_period_count,
    )


def project_cash_flow(
    user_id: int,
    settings: UserSettings | None,
) -> CashFlowProjection | None:
    """Project the grid-default account's balance for the obligations panel.

    Resolves the same baseline scenario and default account the ``/grid``
    page uses, then walks the balance-at seam's cash-flow entry
    :func:`balance_at.cash_balance_map` over the user's full pay-period
    set so the returned balances are byte-identical to the grid's
    Projected End Balance footer (raise-aware income via the live
    paycheck recompute, entry-aware expenses, transfer-symmetric).

    The full period set is passed to ``cash_balance_map`` (not just the
    current-and-forward slice) because the engine seeds its running
    balance at the anchor period and skips any period before it; a
    forward-only slice that omitted the anchor would yield an empty map.
    Markers are then read from the current period forward.

    Args:
        user_id: The user whose projection to build.
        settings: The user's :class:`UserSettings` row (or ``None``);
            forwarded to :func:`resolve_grid_account` so the panel honors
            a configured default grid account, matching the grid.

    Returns:
        A :class:`CashFlowProjection`, or ``None`` when the user has no
        baseline scenario, no resolvable account, or no current pay
        period (setup-incomplete) -- the same empty-state signals the
        grid treats as "not set up yet".  The caller hides the panel on
        ``None``.
    """
    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        return None

    account = resolve_grid_account(user_id, settings, None)
    if account is None:
        return None

    current_period = pay_period_service.get_current_period(user_id)
    if current_period is None:
        return None

    all_periods = pay_period_service.get_all_periods(user_id)
    balances = balance_at.cash_balance_map(
        account, scenario, all_periods,
    ).balances
    now_balance = balance_resolver.resolve_anchor(account, scenario.id).balance

    # Periods from the current period forward that the engine actually
    # projected (pre-anchor periods are absent from ``balances``).
    forward = [
        period
        for period in all_periods
        if period.period_index >= current_period.period_index
        and period.id in balances
    ]
    if not forward:
        return CashFlowProjection(
            account_name=account.name,
            now_balance=now_balance,
            twelve_month=None,
            end=None,
            negative_period_count=0,
            direction="flat",
        )

    twelve_month_cutoff = current_period.start_date + timedelta(days=DAYS_PER_YEAR)
    twelve_month, end, negative_period_count = _summarize_forward(
        forward, balances, twelve_month_cutoff,
    )
    return CashFlowProjection(
        account_name=account.name,
        now_balance=now_balance,
        twelve_month=twelve_month,
        end=end,
        negative_period_count=negative_period_count,
        direction=_direction(now_balance, end.balance),
    )
