"""
Shekel Budget App — Recurrence Engine

The most complex service in the app.  Given a transaction template and
its recurrence rule, generates Transaction entries into the appropriate
future pay periods.

Implements the full state machine from §4.8:
  - Respects is_override and is_deleted flags.
  - Returns conflicts (overridden/deleted) for the route layer to present
    to the user as prompts.
  - Never touches done/received/credit transactions.

Supported patterns (§4.7):
  - every_period:      Every pay period.
  - every_n_periods:   Every Nth period with an offset.
  - monthly:           Assigned to the period containing day_of_month.
  - monthly_first:     First pay period whose start_date falls in each month.
  - quarterly:         Every 3 months starting from month_of_year.
  - semi_annual:       Every 6 months starting from month_of_year.
  - annual:            Once per year on month/day.
  - once:              Single occurrence (no auto-generation — user assigns manually).
"""

import logging
from datetime import date

from app.extensions import db
from app.models.transaction import Transaction
from app.models.pay_period import PayPeriod
from app.models.ref import Status
from app.exceptions import RecurrenceConflict
from app.models.salary_profile import SalaryProfile

logger = logging.getLogger(__name__)

# Statuses that are historical — never modified by the recurrence engine.
IMMUTABLE_STATUSES = frozenset({"done", "received", "credit", "cancelled"})


def generate_for_template(template, periods, scenario_id, effective_from=None):
    """Generate transactions for a template across the given pay periods.

    This is the main entry point.  It:
      1. Determines which periods the rule applies to.
      2. Skips periods that already have an overridden, deleted, or immutable entry.
      3. Creates new auto-generated transactions for applicable periods.

    Args:
        template:       A TransactionTemplate with a loaded recurrence_rule.
        periods:        List of PayPeriod objects to consider (ordered by index).
        scenario_id:    The scenario to generate into.
        effective_from: Optional date — only generate for periods starting on or
                        after this date.  Defaults to the first period's start_date.

    Returns:
        List of newly created Transaction objects.
    """
    rule = template.recurrence_rule
    if rule is None:
        # No recurrence rule — nothing to generate (one-time / manual).
        return []

    pattern_name = rule.pattern.name
    if pattern_name == "once":
        # 'once' items are manually placed; no auto-generation.
        return []

    # If the rule has a start_period_id and no explicit effective_from was
    # passed, use the start period's start_date as the boundary.
    if effective_from is None and rule.start_period_id and rule.start_period:
        effective_from = rule.start_period.start_date
    if effective_from is None and periods:
        effective_from = periods[0].start_date

    # Get the projected status for new transactions.
    projected_status = db.session.query(Status).filter_by(name="projected").one()

    # Determine which periods match the recurrence pattern.
    matching_periods = _match_periods(rule, pattern_name, periods, effective_from)

    # Check for existing transactions to avoid duplicates and respect overrides.
    existing = _get_existing_map(template.id, scenario_id, matching_periods)

    # Check if this template has a linked salary profile for paycheck calculation.
    salary_profile = _get_salary_profile(template.id)

    created = []
    for period in matching_periods:
        existing_txn = existing.get(period.id)

        if existing_txn is not None:
            status_name = existing_txn.status.name if existing_txn.status else "projected"

            # Never touch immutable (historical) transactions.
            if status_name in IMMUTABLE_STATUSES:
                continue

            # Skip overridden entries — the user made a deliberate change.
            if existing_txn.is_override:
                continue

            # Skip soft-deleted entries — the user intentionally removed it.
            if existing_txn.is_deleted:
                continue

            # Auto-generated and unmodified — it already exists, skip.
            continue

        # Determine the amount — use paycheck calculator if salary-linked.
        amount = _get_transaction_amount(
            template, salary_profile, period, periods
        )

        # No existing entry — create a new one.
        txn = Transaction(
            template_id=template.id,
            pay_period_id=period.id,
            scenario_id=scenario_id,
            status_id=projected_status.id,
            name=template.name,
            category_id=template.category_id,
            transaction_type_id=template.transaction_type_id,
            estimated_amount=amount,
            is_override=False,
            is_deleted=False,
        )
        db.session.add(txn)
        created.append(txn)

    db.session.flush()
    logger.info(
        "Generated %d transactions for template '%s' (id=%d)",
        len(created), template.name, template.id,
    )
    return created


def regenerate_for_template(template, periods, scenario_id, effective_from=None):
    """Delete non-overridden auto-generated entries and regenerate.

    Used when a template's amount or recurrence rule changes.  Implements
    the state machine rules from §4.8:
      1. Delete all auto_generated (non-overridden, non-deleted) transactions
         on or after the effective date.
      2. Regenerate from the rule.
      3. Return conflicts (overridden and deleted entries) for the caller
         to present to the user.

    Args:
        template:       The updated TransactionTemplate.
        periods:        List of PayPeriod objects.
        scenario_id:    The target scenario.
        effective_from: Date from which to regenerate (default: first period).

    Returns:
        List of newly created Transaction objects.

    Raises:
        RecurrenceConflict: If overridden or deleted entries exist that need
                            user confirmation.  The caller should catch this,
                            present the options, and call resolve_conflicts().
    """
    if effective_from is None and periods:
        effective_from = periods[0].start_date

    # Find all existing template-linked transactions on or after effective_from.
    existing = (
        db.session.query(Transaction)
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(
            Transaction.template_id == template.id,
            Transaction.scenario_id == scenario_id,
            PayPeriod.start_date >= effective_from,
        )
        .all()
    )

    overridden_ids = []
    deleted_ids = []
    to_delete = []

    for txn in existing:
        status_name = txn.status.name if txn.status else "projected"

        # Immutable — never touch.
        if status_name in IMMUTABLE_STATUSES:
            continue

        # Overridden — flag as conflict for user prompt.
        if txn.is_override:
            overridden_ids.append(txn.id)
            continue

        # Soft-deleted — flag as conflict for user prompt.
        if txn.is_deleted:
            deleted_ids.append(txn.id)
            continue

        # Auto-generated, unmodified — safe to delete and regenerate.
        to_delete.append(txn)

    # Delete the safe-to-remove entries.
    for txn in to_delete:
        db.session.delete(txn)
    db.session.flush()

    # Regenerate new entries.
    created = generate_for_template(template, periods, scenario_id, effective_from)

    # If there are conflicts, raise so the caller can prompt the user.
    if overridden_ids or deleted_ids:
        raise RecurrenceConflict(overridden=overridden_ids, deleted=deleted_ids)

    return created


def resolve_conflicts(transaction_ids, action, new_amount=None):
    """Resolve override/delete conflicts after a regeneration.

    Called by the route layer after the user responds to the conflict prompt.

    Args:
        transaction_ids: List of Transaction IDs to resolve.
        action:          'update' — clear override/delete, apply new amount.
                         'keep' — leave the transaction unchanged.
        new_amount:      The new default amount (required if action='update').
    """
    if action == "keep":
        # Nothing to do — the user wants to keep their overrides.
        return

    if action == "update":
        for txn_id in transaction_ids:
            txn = db.session.get(Transaction, txn_id)
            if txn is None:
                continue
            txn.is_override = False
            txn.is_deleted = False
            if new_amount is not None:
                txn.estimated_amount = new_amount
        db.session.flush()


# --- Pattern Matching Helpers -------------------------------------------


def _match_periods(rule, pattern_name, periods, effective_from):
    """Return the subset of periods that match the recurrence pattern.

    Args:
        rule:           The RecurrenceRule object.
        pattern_name:   The pattern name string.
        periods:        All candidate PayPeriod objects.
        effective_from: Only include periods starting on or after this date.

    Returns:
        Filtered list of PayPeriod objects.
    """
    # Filter by effective date first.
    candidates = [p for p in periods if p.start_date >= effective_from]

    if pattern_name == "every_period":
        return candidates

    if pattern_name == "every_n_periods":
        n = rule.interval_n or 1
        offset = rule.offset_periods or 0
        return [p for p in candidates if (p.period_index - offset) % n == 0]

    if pattern_name == "monthly":
        return _match_monthly(candidates, rule.day_of_month or 1)

    if pattern_name == "monthly_first":
        return _match_monthly_first(candidates)

    if pattern_name == "quarterly":
        start_month = rule.month_of_year or 1
        day = rule.day_of_month or 1
        return _match_quarterly(candidates, start_month, day)

    if pattern_name == "semi_annual":
        start_month = rule.month_of_year or 1
        day = rule.day_of_month or 1
        return _match_semi_annual(candidates, start_month, day)

    if pattern_name == "annual":
        month = rule.month_of_year or 1
        day = rule.day_of_month or 1
        return _match_annual(candidates, month, day)

    # Unknown pattern — return nothing.
    logger.warning("Unknown recurrence pattern: %s", pattern_name)
    return []


def _match_monthly(periods, day_of_month):
    """Find the pay period that contains a given day_of_month each month.

    For each unique (year, month) in the periods, find the period whose
    date range includes that month's target day.
    """
    import calendar  # pylint: disable=import-outside-toplevel

    matched = []
    seen_months = set()

    for period in periods:
        # Check each month the period might span.
        for dt in (period.start_date, period.end_date):
            year_month = (dt.year, dt.month)
            if year_month in seen_months:
                continue

            # Clamp day_of_month to the actual last day of the month.
            last_day = calendar.monthrange(dt.year, dt.month)[1]
            target_day = min(day_of_month, last_day)
            target_date = date(dt.year, dt.month, target_day)

            if period.start_date <= target_date <= period.end_date:
                matched.append(period)
                seen_months.add(year_month)

    return matched


def _match_monthly_first(periods):
    """Find the first pay period whose start_date falls in each calendar month."""
    matched = []
    seen_months = set()

    for period in periods:
        year_month = (period.start_date.year, period.start_date.month)
        if year_month not in seen_months:
            matched.append(period)
            seen_months.add(year_month)

    return matched


def _match_quarterly(periods, start_month, day_of_month):
    """Find pay periods containing specific day in quarterly months.

    Matches months: start_month, start_month+3, start_month+6, start_month+9.
    """
    target_months = set(((start_month - 1 + i * 3) % 12) + 1 for i in range(4))
    return _match_specific_months(periods, target_months, day_of_month)


def _match_semi_annual(periods, start_month, day_of_month):
    """Find pay periods containing specific day in semi-annual months.

    Matches months: start_month and start_month+6.
    """
    target_months = set(((start_month - 1 + i * 6) % 12) + 1 for i in range(2))
    return _match_specific_months(periods, target_months, day_of_month)


def _match_specific_months(periods, target_months, day_of_month):
    """Find pay periods that contain a target day in any of the specified months."""
    import calendar  # pylint: disable=import-outside-toplevel

    matched = []
    seen = set()

    for period in periods:
        for dt in (period.start_date, period.end_date):
            key = (dt.year, dt.month)
            if key in seen or dt.month not in target_months:
                continue

            last_day = calendar.monthrange(dt.year, dt.month)[1]
            target_day = min(day_of_month, last_day)
            target_date = date(dt.year, dt.month, target_day)

            if period.start_date <= target_date <= period.end_date:
                matched.append(period)
                seen.add(key)

    return matched


def _match_annual(periods, month, day):
    """Find the pay period that contains a specific month/day each year."""
    import calendar  # pylint: disable=import-outside-toplevel

    matched = []
    seen_years = set()

    for period in periods:
        for dt in (period.start_date, period.end_date):
            if dt.year in seen_years:
                continue

            last_day = calendar.monthrange(dt.year, month)[1]
            target_day = min(day, last_day)
            target_date = date(dt.year, month, target_day)

            if period.start_date <= target_date <= period.end_date:
                matched.append(period)
                seen_years.add(dt.year)

    return matched


def _get_existing_map(template_id, scenario_id, periods):
    """Build a dict of period_id → Transaction for existing template entries.

    Fetches all entries (including deleted) to check for duplicates and
    respect override/delete flags.  The caller checks is_deleted to skip
    re-creating those periods.
    """
    period_ids = [p.id for p in periods]
    if not period_ids:
        return {}

    existing = (
        db.session.query(Transaction)
        .filter(
            Transaction.template_id == template_id,
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id.in_(period_ids),
        )
        .all()
    )
    return {txn.pay_period_id: txn for txn in existing}


def _get_salary_profile(template_id):
    """Check if a template has a linked salary profile.

    Returns the SalaryProfile if found, None otherwise.
    """
    return (
        db.session.query(SalaryProfile)
        .filter_by(template_id=template_id, is_active=True)
        .first()
    )


def _get_transaction_amount(template, salary_profile, period, all_periods):
    """Determine the transaction amount, using paycheck calculator if salary-linked."""
    if salary_profile is None:
        return template.default_amount

    try:
        from app.services import paycheck_calculator  # pylint: disable=import-outside-toplevel
        from app.models.tax_config import (  # pylint: disable=import-outside-toplevel
            FicaConfig,
            StateTaxConfig,
            TaxBracketSet,
        )

        user_id = salary_profile.user_id
        tax_year = period.start_date.year

        bracket_set = (
            db.session.query(TaxBracketSet)
            .filter_by(
                user_id=user_id,
                filing_status_id=salary_profile.filing_status_id,
                tax_year=tax_year,
            )
            .first()
        )

        state_config = (
            db.session.query(StateTaxConfig)
            .filter_by(user_id=user_id, state_code=salary_profile.state_code)
            .first()
        )

        fica_config = (
            db.session.query(FicaConfig)
            .filter_by(user_id=user_id, tax_year=tax_year)
            .first()
        )

        tax_configs = {
            "bracket_set": bracket_set,
            "state_config": state_config,
            "fica_config": fica_config,
        }

        breakdown = paycheck_calculator.calculate_paycheck(
            salary_profile, period, all_periods, tax_configs
        )
        return breakdown.net_pay

    except Exception:
        logger.exception(
            "Failed to calculate paycheck for salary profile %d, "
            "falling back to template default_amount",
            salary_profile.id,
        )
        return template.default_amount
