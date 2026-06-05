"""
Shekel Budget App -- Year-End Summary: period and balance lookups.

Pure helpers for locating pay periods by date and reading balances
out of a period-keyed balance map (with anchor-aware fallbacks).
"""

import calendar
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.pay_period import PayPeriod

ZERO = Decimal("0")


def _get_anchor_period_index(
    account: Account, all_periods: list,
) -> int | None:
    """Return the period_index of the account's anchor period.

    Args:
        account: Account with current_anchor_period_id set.
        all_periods: All user pay periods.

    Returns:
        int period_index, or None if the anchor period is not found.
    """
    anchor_pid = account.current_anchor_period_id
    if anchor_pid is None:
        return None
    for p in all_periods:
        if p.id == anchor_pid:
            return p.period_index
    return None


def _get_month_end_periods(
    year: int, all_periods: list,
) -> dict[int, PayPeriod]:
    """Map each month (1-12) to the last period ending on or before
    that month's last day.

    Args:
        year: Target calendar year.
        all_periods: All user pay periods sorted by period_index.

    Returns:
        dict mapping month number to PayPeriod.
    """
    month_periods: dict[int, PayPeriod] = {}

    for month in range(1, 13):
        last_day = date(
            year, month, calendar.monthrange(year, month)[1],
        )
        best = _find_period_on_or_before_date(last_day, all_periods)
        if best is not None:
            month_periods[month] = best

    return month_periods


def _find_period_before_date(
    target: date, all_periods: list,
) -> PayPeriod | None:
    """Find the last period whose end_date is strictly before target."""
    best = None
    for p in all_periods:
        if p.end_date < target:
            if best is None or p.period_index > best.period_index:
                best = p
    return best


def _find_period_on_or_before_date(
    target: date, all_periods: list,
) -> PayPeriod | None:
    """Find the last period whose end_date is on or before target."""
    best = None
    for p in all_periods:
        if p.end_date <= target:
            if best is None or p.period_index > best.period_index:
                best = p
    return best


def _lookup_period_balance(
    balances: dict | None,
    year: int,
    month: int,
    all_periods: list,
) -> Decimal:
    """Look up the balance at the end of a specific month.

    Finds the last pay period ending on or before the month's last day
    and returns its balance from the balance map.

    Args:
        balances: period_id -> Decimal balance map, or None.
        year: Calendar year.
        month: Month number (1-12).
        all_periods: All user pay periods.

    Returns:
        Decimal balance, or ZERO if no matching period.
    """
    if not balances:
        return ZERO

    last_day = date(
        year, month, calendar.monthrange(year, month)[1],
    )
    target_period = _find_period_on_or_before_date(
        last_day, all_periods,
    )
    if target_period is None:
        return ZERO
    return balances.get(target_period.id, ZERO)


def _lookup_balance_with_anchor_fallback(
    balances: dict | None,
    year: int,
    month: int,
    all_periods: list,
    account: Account,
) -> Decimal:
    """Look up balance at a period, falling back to anchor balance for pre-anchor periods.

    Unlike _lookup_period_balance which returns ZERO for pre-anchor
    periods (because calculate_balances skips them), this function
    returns the account's anchor balance when the target period is
    before the anchor -- a closer approximation than ZERO.

    A legitimate ZERO balance at a post-anchor period is returned
    as-is; the fallback only triggers when the target period is
    absent from the balance map AND precedes the anchor.

    Args:
        balances: period_id -> Decimal balance map from
            calculate_balances, or None.
        year: Calendar year.
        month: Month number (1-12).
        all_periods: All user pay periods.
        account: Account with current_anchor_balance and
            current_anchor_period_id.

    Returns:
        Decimal balance.
    """
    if not balances:
        # No balance map at all -- fall back to anchor if available.
        return account.current_anchor_balance or ZERO

    last_day = date(
        year, month, calendar.monthrange(year, month)[1],
    )
    target_period = _find_period_on_or_before_date(
        last_day, all_periods,
    )
    if target_period is None:
        return ZERO

    # Period exists in the balance map -- return its value (even if ZERO).
    if target_period.id in balances:
        return balances[target_period.id]

    # Period is NOT in the balance map.  If it precedes the anchor,
    # the anchor balance is the best available approximation.
    anchor_pid = account.current_anchor_period_id
    anchor_period = next(
        (p for p in all_periods if p.id == anchor_pid), None,
    )
    if anchor_period and target_period.period_index < anchor_period.period_index:
        return account.current_anchor_balance or ZERO

    return ZERO
