"""
Shekel Budget App -- Pay-Period Balance Projection Helpers

Pure helpers for picking projected account balances at fixed future
horizons.  No Flask, no SQLAlchemy: they operate on the DERIVED pay calendar
(:class:`~app.services.pay_calendar.DerivedPeriod` values) and a
``{period_id: balance}`` map, so they import cleanly into any route or service.

**They took ORM ``PayPeriod`` rows until pay-calendar plan step C2-f2d-3.**
Both of the two things read off a period here -- its ordinal and its id -- are
answered by the derived value, and the ordinal is one of the two columns plan
step **C4** drops, so a helper keyed on the stored one would have had to move
anyway.
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
        current_period: The
            :class:`~app.services.pay_calendar.DerivedPeriod` covering the read
            pass's clock, or ``None`` (no current period yields an empty
            result).
        all_periods: The owner's saved schedule -- a
            :class:`~app.services.pay_calendar.PeriodWindow` -- searched by
            ``period_index``.
        balance_map: Mapping of ``budget.pay_periods.id`` to the projected
            balance at that period.  Both callers get it from the
            :mod:`app.services.balance_at` seam, which reports over the same
            pass's ``reported_periods()``, so a period taken from that
            window and this map name one calendar.

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
            if (period.period_index == target_idx
                    and period.period_id in balance_map):
                projected[label] = balance_map[period.period_id]
                break
    return projected
