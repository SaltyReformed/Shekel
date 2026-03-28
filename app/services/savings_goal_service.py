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


def compute_committed_monthly(expense_templates, transfer_templates):
    """Calculate total committed monthly expenses from active templates.

    Sums the monthly-equivalent cost from each template based on its
    recurrence pattern.  Both expense templates (direct debits from
    checking) and transfer templates (money leaving checking to other
    accounts) count toward the committed baseline.

    Conversion factors (biweekly-to-monthly: 26 pay periods / 12 months):

      - every_period:    amount * 26 / 12
      - every_n_periods: amount * (26 / n) / 12
      - monthly:         amount  (already monthly)
      - monthly_first:   amount  (already monthly)
      - quarterly:       amount / 3
      - semi_annual:     amount / 6
      - annual:          amount / 12
      - once:            excluded (not a recurring commitment)

    Args:
        expense_templates: List of TransactionTemplate objects (expenses
            on checking).  Must already be filtered to is_active=True.
        transfer_templates: List of TransferTemplate objects (debits from
            checking).  Must already be filtered to is_active=True.

    Returns:
        Decimal -- total committed monthly expense, rounded to 2 decimal
        places with ROUND_HALF_UP.  Returns Decimal("0.00") if both
        lists are empty or all templates are skipped.
    """
    from app import ref_cache  # pylint: disable=import-outside-toplevel
    from app.enums import RecurrencePatternEnum  # pylint: disable=import-outside-toplevel

    # Resolve pattern IDs from the startup cache (no DB hit).
    every_period_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.EVERY_PERIOD
    )
    every_n_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.EVERY_N_PERIODS
    )
    monthly_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.MONTHLY
    )
    monthly_first_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.MONTHLY_FIRST
    )
    quarterly_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.QUARTERLY
    )
    semi_annual_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.SEMI_ANNUAL
    )
    annual_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.ANNUAL
    )
    once_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.ONCE
    )

    total = Decimal("0")

    for template in list(expense_templates) + list(transfer_templates):
        amount = template.default_amount
        # Skip templates with no amount or zero amount.
        if amount is None or Decimal(str(amount)) == 0:
            continue
        amount = Decimal(str(amount))

        rule = template.recurrence_rule
        if rule is None:
            # No recurrence rule -- cannot determine frequency.
            continue

        pattern_id = rule.pattern_id

        if pattern_id == once_id:
            # One-time templates are not recurring commitments.
            continue

        if pattern_id == every_period_id:
            # Every biweekly period: 26 occurrences/year.
            monthly = amount * Decimal("26") / Decimal("12")
        elif pattern_id == every_n_id:
            # Every N biweekly periods: 26/N occurrences/year.
            n = Decimal(str(rule.interval_n or 1))
            monthly = amount * Decimal("26") / n / Decimal("12")
        elif pattern_id in (monthly_id, monthly_first_id):
            # Already monthly -- 12 occurrences/year.
            monthly = amount
        elif pattern_id == quarterly_id:
            # 4 occurrences/year: amount / 3 for monthly.
            monthly = amount / Decimal("3")
        elif pattern_id == semi_annual_id:
            # 2 occurrences/year: amount / 6 for monthly.
            monthly = amount / Decimal("6")
        elif pattern_id == annual_id:
            # 1 occurrence/year: amount / 12 for monthly.
            monthly = amount / Decimal("12")
        else:
            # Unknown pattern -- skip to avoid incorrect calculation.
            continue

        total += monthly

    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
