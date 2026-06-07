"""
Shekel Budget App -- Recurring Obligations Routes

Read-only summary page showing all recurring financial obligations
in one place: recurring expenses, recurring transfers, and recurring
income.  Per-row monthly equivalents and section subtotals route
through ``obligations_aggregator`` (E-24 / HIGH-05) -- the single
canonical filter+sum producer also used by the /savings emergency-
fund baseline and per-goal contribution floors, so the same
obligation is never two different numbers on two pages.
"""

import calendar
import functools
import logging
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import TypeVar

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import RecurrencePatternEnum, TxnTypeEnum
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services.obligations_aggregator import (
    committed_monthly,
    template_monthly_or_none,
)
from app.utils.auth_helpers import require_owner
from app.utils.money import round_money

logger = logging.getLogger(__name__)

obligations_bp = Blueprint("obligations", __name__)

@functools.cache
def _get_frequency_labels():
    """Build the pattern-ID-to-label mapping from ref_cache.

    Memoized for the process lifetime on the first call.  ref_cache is
    populated at app startup (not at module import time), so the mapping
    cannot be built at import; the pattern IDs are stable thereafter, so
    caching once is safe.  ``functools.cache`` replaces a hand-rolled
    module-global lazy-init (and its ``global`` statement).

    Returns:
        Dict mapping int pattern_id to str label.
    """
    return {
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

    # The recurrence-pattern-id resolution block below is paralleled by
    # the cadence classifier in ``savings_goal_service``; both resolve the
    # same seven pattern ids.  The substantive logic that consumes them
    # diverges entirely (this route computes the next occurrence date; the
    # service converts a per-occurrence amount to a monthly equivalent), so
    # a shared "bag of ids" helper would relocate the lookups without
    # dissolving the real per-domain logic (coding-standards rule 13).
    # One-sided ``duplicate-code`` disable (see plan.md Phase 2 notes).
    # pylint: disable=duplicate-code
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
    # pylint: enable=duplicate-code

    pid = rule.pattern_id
    day = rule.day_of_month or 1
    month = rule.month_of_year or 1

    # Single-return dispatch (one date-or-None per pattern); collapses what
    # would otherwise be one return per branch.  An unknown pattern falls
    # through to None.
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
        next_date = period.start_date if period else None
    elif pid in (monthly_id, monthly_first_id):
        next_date = _next_monthly(today, day)
    elif pid == quarterly_id:
        next_date = _next_periodic_month(today, month, day, 3)
    elif pid == semi_annual_id:
        next_date = _next_periodic_month(today, month, day, 6)
    elif pid == annual_id:
        next_date = _next_annual(today, month, day)
    else:
        next_date = None

    return next_date


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
# Per-row display dicts
# ---------------------------------------------------------------------------


def _render_expense_item(tmpl: TransactionTemplate, monthly: Decimal) -> dict:
    """Build the display dict for one recurring expense row.

    ``monthly`` is the full-precision monthly equivalent returned by
    ``template_monthly_or_none``; the per-row display value is rounded
    to cents at the boundary via ``round_money``.
    """
    rule = tmpl.recurrence_rule
    return {
        "name": tmpl.name,
        "account_name": tmpl.account.name if tmpl.account else "--",
        "category_name": tmpl.category.item_name if tmpl.category else "--",
        "amount": Decimal(str(tmpl.default_amount)),
        "frequency": _frequency_label(rule),
        "monthly": round_money(monthly),
        "next_date": _next_occurrence(rule),
    }


def _render_income_item(tmpl: TransactionTemplate, monthly: Decimal) -> dict:
    """Build the display dict for one recurring income row.

    ``monthly`` is the full-precision monthly equivalent returned by
    ``template_monthly_or_none``; rounded to cents for display.
    """
    rule = tmpl.recurrence_rule
    return {
        "name": tmpl.name,
        "account_name": tmpl.account.name if tmpl.account else "--",
        "amount": Decimal(str(tmpl.default_amount)),
        "frequency": _frequency_label(rule),
        "monthly": round_money(monthly),
        "next_date": _next_occurrence(rule),
    }


def _render_transfer_item(tmpl: TransferTemplate, monthly: Decimal) -> dict:
    """Build the display dict for one recurring transfer row.

    ``monthly`` is the full-precision monthly equivalent returned by
    ``template_monthly_or_none``; rounded to cents for display.
    """
    rule = tmpl.recurrence_rule
    return {
        "name": tmpl.name,
        "from_account": tmpl.from_account.name if tmpl.from_account else "--",
        "to_account": tmpl.to_account.name if tmpl.to_account else "--",
        "amount": Decimal(str(tmpl.default_amount)),
        "frequency": _frequency_label(rule),
        "monthly": round_money(monthly),
        "next_date": _next_occurrence(rule),
    }


_TemplateT = TypeVar("_TemplateT", TransactionTemplate, TransferTemplate)


def _build_items(
    templates: list[_TemplateT],
    renderer: Callable[[_TemplateT, Decimal], dict],
    as_of: date,
) -> list[dict]:
    """Build the per-row display dicts for one obligation section.

    ``template_monthly_or_none`` returns None for the rows the aggregator
    excludes (ONCE, expired, and missing/zero amount), so a row appears
    here iff it also contributes to the section subtotal (E-24 / HIGH-05).
    Each surviving (template, monthly) pair is shaped into its
    section-specific display dict by ``renderer``.
    """
    items = []
    for tmpl in templates:
        monthly = template_monthly_or_none(tmpl, as_of)
        if monthly is not None:
            items.append(renderer(tmpl, monthly))
    return items


def _load_recurring_expenses(user_id: int) -> list[TransactionTemplate]:
    """Load the user's active recurring expense templates, ordered for
    display (recurrence rule + account + category eager-loaded).
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    return (
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


def _load_recurring_income(user_id: int) -> list[TransactionTemplate]:
    """Load the user's active recurring income templates, ordered for
    display (recurrence rule + account eager-loaded).
    """
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    return (
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


def _load_recurring_transfers(user_id: int) -> list[TransferTemplate]:
    """Load the user's active recurring transfer templates, ordered for
    display (recurrence rule + both accounts eager-loaded).
    """
    return (
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
    as_of = date.today()

    expense_templates = _load_recurring_expenses(user_id)
    income_templates = _load_recurring_income(user_id)
    transfer_templates = _load_recurring_transfers(user_id)

    # Per-row inclusion (_build_items) and the section subtotals
    # (committed_monthly) both route through obligations_aggregator, so a
    # row is shown iff it contributes to its subtotal and the two cannot
    # drift apart (E-24 / HIGH-05).
    expense_items = _build_items(expense_templates, _render_expense_item, as_of)
    income_items = _build_items(income_templates, _render_income_item, as_of)
    transfer_items = _build_items(transfer_templates, _render_transfer_item, as_of)

    total_expense_monthly = committed_monthly(expense_templates, as_of)
    total_income_monthly = committed_monthly(income_templates, as_of)
    total_transfer_monthly = committed_monthly(transfer_templates, as_of)
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
