"""
Shekel Budget App -- Savings Goal Service

Pure functions for savings goal calculations. No database writes, no
Flask imports -- called by the savings route to compute metrics.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP


def calculate_required_contribution(current_balance, target_amount, remaining_periods):
    """Calculate the required contribution per period to reach a savings goal.

    Args:
        current_balance:   Decimal -- current account balance.
        target_amount:     Decimal -- the goal target.
        remaining_periods: int -- number of pay periods until the target date.

    Returns:
        Decimal -- required contribution per period, or Decimal("0.00") if
        already met, or None if past due (no remaining periods).
    """
    if current_balance is None:
        current_balance = Decimal("0.00")
    else:
        current_balance = Decimal(str(current_balance))
    target_amount = Decimal(str(target_amount))

    gap = target_amount - current_balance
    if gap <= 0:
        return Decimal("0.00")

    if remaining_periods is None or remaining_periods <= 0:
        return None

    return (gap / remaining_periods).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def calculate_savings_metrics(savings_balance, average_monthly_expenses):
    """Calculate how long savings would cover expenses.

    Args:
        savings_balance:          Decimal -- total savings balance.
        average_monthly_expenses: Decimal -- average monthly expense total.

    Returns:
        Dict with months_covered, paychecks_covered, years_covered.
        All values are Decimal. Returns zeros if expenses are zero.
    """
    if savings_balance is None:
        savings_balance = Decimal("0.00")
    else:
        savings_balance = Decimal(str(savings_balance))

    if average_monthly_expenses is None or Decimal(str(average_monthly_expenses)) <= 0:
        return {
            "months_covered": Decimal("0"),
            "paychecks_covered": Decimal("0"),
            "years_covered": Decimal("0"),
        }

    avg_expenses = Decimal(str(average_monthly_expenses))
    months = (savings_balance / avg_expenses).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )

    return {
        "months_covered": months,
        "paychecks_covered": (months * Decimal("26") / Decimal("12")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        ),
        "years_covered": (months / Decimal("12")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        ),
    }


def count_periods_until(target_date, periods):
    """Count pay periods between today and the target date.

    Args:
        target_date: date -- the goal's target date.
        periods:     List of PayPeriod objects ordered by index.

    Returns:
        int -- count of periods from today to the target date (inclusive).
    """
    if target_date is None:
        return None

    today = date.today()
    count = 0
    for period in periods:
        if period.start_date >= today and period.start_date <= target_date:
            count += 1
    return count
