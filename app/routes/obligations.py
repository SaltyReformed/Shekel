"""
Shekel Budget App -- Recurring Obligations Routes

Read-only summary page showing all recurring financial obligations
in one place: recurring expenses, recurring transfers, and recurring
income.  Computes monthly equivalents using the shared conversion
factors from savings_goal_service.amount_to_monthly() and displays
next occurrence dates for each obligation.
"""

import calendar
import logging
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from app.utils.auth_helpers import require_owner

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import RecurrencePatternEnum, TxnTypeEnum
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services.savings_goal_service import amount_to_monthly

logger = logging.getLogger(__name__)

obligations_bp = Blueprint("obligations", __name__)

TWO_PLACES = Decimal("0.01")

# Human-readable labels for recurrence patterns, keyed by pattern ID.
# Built lazily on first use since ref_cache is not available at import time.
_FREQUENCY_LABELS = None


def _get_frequency_labels():
    """Build the pattern-ID-to-label mapping from ref_cache.

    Lazily initialized because ref_cache is populated at app startup,
    not at module import time.

    Returns:
        Dict mapping int pattern_id to str label.
    """
    global _FREQUENCY_LABELS  # pylint: disable=global-statement
    if _FREQUENCY_LABELS is not None:
        return _FREQUENCY_LABELS

    _FREQUENCY_LABELS = {
        ref_cache.recurrence_pattern_id(RecurrencePatternEnum.EVERY_PERIOD):
            "Biweekly",
        ref_cache.recurrence_pattern_id(RecurrencePatternEnum.EVERY_N_PERIODS):
            "Every N Periods",
        ref_cache.recurrence_pattern_id(RecurrencePatternEnum.MONTHLY):
            "Monthly",
        ref_cache.recurrence_pattern_id(RecurrencePatternEnum.MONTHLY_FIRST):
            "Monthly (1st)",
        ref_cache.recurrence_pattern_id(RecurrencePatternEnum.QUARTERLY):
            "Quarterly",
        ref_cache.recurrence_pattern_id(RecurrencePatternEnum.SEMI_ANNUAL):
            "Semi-Annual",
        ref_cache.recurrence_pattern_id(RecurrencePatternEnum.ANNUAL):
            "Annual",
        ref_cache.recurrence_pattern_id(RecurrencePatternEnum.ONCE):
            "One-Time",
    }
    return _FREQUENCY_LABELS


def _frequency_label(rule):
    """Return a human-readable frequency label for a recurrence rule.

    For EVERY_N_PERIODS, includes the interval (e.g., "Every 2 Periods").

    Args:
        rule: RecurrenceRule model instance.

    Returns:
        str frequency label.
    """
    labels = _get_frequency_labels()
    every_n_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.EVERY_N_PERIODS
    )
    if rule.pattern_id == every_n_id:
        n = rule.interval_n or 1
        if n == 1:
            return "Biweekly"
        return f"Every {n} Periods"
    return labels.get(rule.pattern_id, "Unknown")


def _next_occurrence(rule):
    """Compute the next occurrence date for a recurrence rule.

    Simple heuristic for display purposes.  Not intended to replicate
    the full recurrence engine logic -- this is an informational
    approximation for the summary page.

    Args:
        rule: RecurrenceRule model instance.

    Returns:
        date of next occurrence, or None if indeterminate.
    """
    today = date.today()

    # If the rule has an end_date in the past, no future occurrences.
    if rule.end_date is not None and rule.end_date < today:
        return None

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

    pid = rule.pattern_id

    if pid in (every_period_id, every_n_id):
        # Next pay period on or after today.
        period = (
            db.session.query(PayPeriod)
            .filter(
                PayPeriod.user_id == rule.user_id,
                PayPeriod.start_date >= today,
            )
            .order_by(PayPeriod.start_date)
            .first()
        )
        return period.start_date if period else None

    if pid in (monthly_id, monthly_first_id):
        return _next_monthly(today, rule.day_of_month or 1)

    if pid == quarterly_id:
        start_month = rule.month_of_year or 1
        day = rule.day_of_month or 1
        return _next_periodic_month(today, start_month, day, 3)

    if pid == semi_annual_id:
        start_month = rule.month_of_year or 1
        day = rule.day_of_month or 1
        return _next_periodic_month(today, start_month, day, 6)

    if pid == annual_id:
        month = rule.month_of_year or 1
        day = rule.day_of_month or 1
        return _next_annual(today, month, day)

    return None


def _next_monthly(today, day_of_month):
    """Next occurrence of a specific day of month on or after today.

    Clamps to the last day of the month when the target day exceeds
    the month's length (e.g., day 31 in a 30-day month).

    Args:
        today: Current date.
        day_of_month: Target day (1-31).

    Returns:
        date of the next occurrence.
    """
    max_day = calendar.monthrange(today.year, today.month)[1]
    clamped = min(day_of_month, max_day)
    candidate = today.replace(day=clamped)
    if candidate >= today:
        return candidate
    # Move to next month.
    if today.month == 12:
        next_year, next_month = today.year + 1, 1
    else:
        next_year, next_month = today.year, today.month + 1
    max_day = calendar.monthrange(next_year, next_month)[1]
    return date(next_year, next_month, min(day_of_month, max_day))


def _next_annual(today, month, day):
    """Next occurrence of a specific month/day on or after today.

    Args:
        today: Current date.
        month: Target month (1-12).
        day: Target day (1-31).

    Returns:
        date of the next occurrence.
    """
    max_day = calendar.monthrange(today.year, month)[1]
    candidate = date(today.year, month, min(day, max_day))
    if candidate >= today:
        return candidate
    max_day = calendar.monthrange(today.year + 1, month)[1]
    return date(today.year + 1, month, min(day, max_day))


def _next_periodic_month(today, start_month, day, step):
    """Next occurrence for quarterly or semi-annual patterns.

    Finds the next month in the sequence [start, start+step, start+2*step, ...]
    that is on or after today.

    Args:
        today: Current date.
        start_month: First occurrence month (1-12).
        day: Target day of month (1-31).
        step: Months between occurrences (3 for quarterly, 6 for semi-annual).

    Returns:
        date of the next occurrence.
    """
    # Build the set of target months in a year.
    target_months = []
    m = start_month
    while m <= 12:
        target_months.append(m)
        m += step

    # Search this year and next year.
    for year in (today.year, today.year + 1):
        for month in target_months:
            max_day = calendar.monthrange(year, month)[1]
            candidate = date(year, month, min(day, max_day))
            if candidate >= today:
                return candidate

    return None


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@obligations_bp.route("/obligations")
@login_required
@require_owner
def summary():
    """Render the recurring obligations summary page.

    Loads all active recurring transaction and transfer templates,
    computes monthly equivalents and approximate next occurrence dates,
    and displays grouped totals for expenses, transfers, and income.
    """
    user_id = current_user.id

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

    # --- Load recurring expense templates ---
    expense_templates = (
        db.session.query(TransactionTemplate)
        .options(
            joinedload(TransactionTemplate.recurrence_rule),
            joinedload(TransactionTemplate.account),
            joinedload(TransactionTemplate.category),
        )
        .filter(
            TransactionTemplate.user_id == user_id,
            TransactionTemplate.is_active.is_(True),
            TransactionTemplate.recurrence_rule_id.isnot(None),
            TransactionTemplate.transaction_type_id == expense_type_id,
        )
        .order_by(TransactionTemplate.sort_order, TransactionTemplate.name)
        .all()
    )

    # --- Load recurring income templates ---
    income_templates = (
        db.session.query(TransactionTemplate)
        .options(
            joinedload(TransactionTemplate.recurrence_rule),
            joinedload(TransactionTemplate.account),
        )
        .filter(
            TransactionTemplate.user_id == user_id,
            TransactionTemplate.is_active.is_(True),
            TransactionTemplate.recurrence_rule_id.isnot(None),
            TransactionTemplate.transaction_type_id == income_type_id,
        )
        .order_by(TransactionTemplate.sort_order, TransactionTemplate.name)
        .all()
    )

    # --- Load recurring transfer templates ---
    transfer_templates = (
        db.session.query(TransferTemplate)
        .options(
            joinedload(TransferTemplate.recurrence_rule),
            joinedload(TransferTemplate.from_account),
            joinedload(TransferTemplate.to_account),
        )
        .filter(
            TransferTemplate.user_id == user_id,
            TransferTemplate.is_active.is_(True),
            TransferTemplate.recurrence_rule_id.isnot(None),
        )
        .order_by(TransferTemplate.sort_order, TransferTemplate.name)
        .all()
    )

    # --- Build obligation items with monthly equivalents ---
    once_id = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.ONCE)

    expense_items = []
    total_expense_monthly = Decimal("0")
    for tmpl in expense_templates:
        rule = tmpl.recurrence_rule
        if rule.pattern_id == once_id:
            continue
        if rule.end_date is not None and rule.end_date < date.today():
            continue
        amount = Decimal(str(tmpl.default_amount))
        monthly = amount_to_monthly(amount, rule.pattern_id, rule.interval_n)
        if monthly is None:
            continue
        total_expense_monthly += monthly
        expense_items.append({
            "name": tmpl.name,
            "account_name": tmpl.account.name if tmpl.account else "--",
            "category_name": tmpl.category.item_name if tmpl.category else "--",
            "amount": amount,
            "frequency": _frequency_label(rule),
            "monthly": monthly.quantize(TWO_PLACES, ROUND_HALF_UP),
            "next_date": _next_occurrence(rule),
        })

    income_items = []
    total_income_monthly = Decimal("0")
    for tmpl in income_templates:
        rule = tmpl.recurrence_rule
        if rule.pattern_id == once_id:
            continue
        if rule.end_date is not None and rule.end_date < date.today():
            continue
        amount = Decimal(str(tmpl.default_amount))
        monthly = amount_to_monthly(amount, rule.pattern_id, rule.interval_n)
        if monthly is None:
            continue
        total_income_monthly += monthly
        income_items.append({
            "name": tmpl.name,
            "account_name": tmpl.account.name if tmpl.account else "--",
            "amount": amount,
            "frequency": _frequency_label(rule),
            "monthly": monthly.quantize(TWO_PLACES, ROUND_HALF_UP),
            "next_date": _next_occurrence(rule),
        })

    transfer_items = []
    total_transfer_monthly = Decimal("0")
    for tmpl in transfer_templates:
        rule = tmpl.recurrence_rule
        if rule.pattern_id == once_id:
            continue
        if rule.end_date is not None and rule.end_date < date.today():
            continue
        amount = Decimal(str(tmpl.default_amount))
        monthly = amount_to_monthly(amount, rule.pattern_id, rule.interval_n)
        if monthly is None:
            continue
        total_transfer_monthly += monthly
        transfer_items.append({
            "name": tmpl.name,
            "from_account": tmpl.from_account.name if tmpl.from_account else "--",
            "to_account": tmpl.to_account.name if tmpl.to_account else "--",
            "amount": amount,
            "frequency": _frequency_label(rule),
            "monthly": monthly.quantize(TWO_PLACES, ROUND_HALF_UP),
            "next_date": _next_occurrence(rule),
        })

    # --- Compute summary metrics ---
    total_expense_monthly = total_expense_monthly.quantize(
        TWO_PLACES, ROUND_HALF_UP,
    )
    total_income_monthly = total_income_monthly.quantize(
        TWO_PLACES, ROUND_HALF_UP,
    )
    total_transfer_monthly = total_transfer_monthly.quantize(
        TWO_PLACES, ROUND_HALF_UP,
    )
    total_outflows = total_expense_monthly + total_transfer_monthly
    net_cash_flow = total_income_monthly - total_outflows

    has_any = bool(expense_items or income_items or transfer_items)

    return render_template(
        "obligations/summary.html",
        expense_items=expense_items,
        income_items=income_items,
        transfer_items=transfer_items,
        total_expense_monthly=total_expense_monthly,
        total_income_monthly=total_income_monthly,
        total_transfer_monthly=total_transfer_monthly,
        total_outflows=total_outflows,
        net_cash_flow=net_cash_flow,
        has_any=has_any,
    )
