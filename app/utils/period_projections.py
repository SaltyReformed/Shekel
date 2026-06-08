"""
Shekel Budget App -- Pay-Period Balance Projection Helpers

Pure helpers for picking projected account balances at fixed future
horizons.  No Flask, no SQLAlchemy: they operate on already-loaded
``PayPeriod`` objects and a ``{period_id: balance}`` map, so they import
cleanly into any route or service.
"""

# The 3 / 6 / 12-month balance horizons expressed as biweekly pay-period
# offsets from the current period (6 / 13 / 26 periods approximate
# 3 / 6 / 12 months at 26 pay periods per year).
HORIZON_OFFSETS: tuple[tuple[str, int], ...] = (
    ("3 months", 6),
    ("6 months", 13),
    ("1 year", 26),
)


def project_balance_horizons(current_period, all_periods, balance_map):
    """Pick the projected balance at each 3 / 6 / 12-month horizon.

    For each horizon offset, finds the pay period whose ``period_index``
    is ``current_period.period_index + offset`` and, when a balance
    exists for it in ``balance_map``, records it under the horizon label.

    Shared by the interest/checking account-detail pages
    (:mod:`app.routes.accounts.detail`) and the savings dashboard's
    plain-account projection branch
    (:mod:`app.services.savings_dashboard_service`).

    Args:
        current_period: The user's current ``PayPeriod``, or ``None``
            (no current period yields an empty result).
        all_periods: Iterable of ``PayPeriod`` objects to search by
            ``period_index``.
        balance_map: Mapping of ``period_id`` to the projected balance
            at that period.

    Returns:
        Dict of horizon label ("3 months" / "6 months" / "1 year") to
        the projected balance at that horizon.  Labels with no matching
        period (or no balance for it) are omitted.
    """
    projected = {}
    if current_period is None:
        return projected
    for label, offset in HORIZON_OFFSETS:
        target_idx = current_period.period_index + offset
        for period in all_periods:
            if period.period_index == target_idx and period.id in balance_map:
                projected[label] = balance_map[period.id]
                break
    return projected
