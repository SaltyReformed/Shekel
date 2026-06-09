"""
Shekel Budget App -- Recurrence Engine

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
  - once:              Single occurrence (no auto-generation -- user assigns manually).
"""

import calendar as cal
import logging
from collections import defaultdict
from datetime import date
from decimal import InvalidOperation
from typing import NamedTuple

from app.extensions import db
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app import ref_cache
from app.enums import RecurrencePatternEnum, StatusEnum
from app.exceptions import RecurrenceConflict, ValidationError
from app.models.salary_profile import SalaryProfile
from app.services._recurrence_common import (
    check_scenario_ownership,
    log_resource_access_denied,
    partition_regeneration_rows,
    query_rows_from_effective_date,
    should_skip_period,
)
from app.utils.log_events import (
    BUSINESS,
    EVT_RECURRENCE_CONFLICTS_RESOLVED,
    EVT_RECURRENCE_GENERATED,
    EVT_RECURRENCE_REGENERATED,
    EVT_RESOLVE_CONFLICTS_SHADOW_REFUSED,
    log_event,
)

logger = logging.getLogger(__name__)


class _GenerationPlan(NamedTuple):
    """Resolved inputs a recurrence generate pass needs after gating.

    Returned by :func:`_resolve_generation_plan` once the cross-user
    ownership check and the rule/ONCE gating have passed, so the caller
    can proceed straight to model-specific row creation.
    """

    rule: RecurrenceRule
    matching_periods: list[PayPeriod]
    projected_id: int


def _resolve_generation_plan(
    template, periods, scenario_id, effective_from, *, block_message,
):
    """Run the shared gating + period-matching preamble for a generate pass.

    Both this module's ``generate_for_template`` and the transfer
    engine's identical preamble (``app/services/transfer_recurrence.py``)
    perform the same steps before their model-specific row creation: the
    cross-user ownership check, the rule-present / not-ONCE gating, the
    ``effective_from`` defaulting, and the pattern match.  Centralising
    them guarantees the two engines cannot drift on which periods a rule
    applies to.

    Args:
        template: The (Transaction|Transfer)Template to generate from.
        periods: Candidate PayPeriod objects, ordered by index.
        scenario_id: The scenario to generate into.
        effective_from: Optional boundary date; when None it defaults to
            the rule's start period, then the first candidate period.
        block_message: Cross-user-block log message distinguishing the
            calling engine.

    Returns:
        A :class:`_GenerationPlan` when generation should proceed, or
        ``None`` when ownership fails or the rule is absent / ONCE (every
        caller returns an empty list in the None case).
    """
    if not check_scenario_ownership(
        logger, template, scenario_id, block_message=block_message,
    ):
        return None

    rule = template.recurrence_rule
    if rule is None:
        # No recurrence rule -- nothing to generate (one-time / manual).
        return None

    pattern_id = rule.pattern_id
    if pattern_id == ref_cache.recurrence_pattern_id(RecurrencePatternEnum.ONCE):
        # 'once' items are manually placed; no auto-generation.
        return None

    # If the rule has a start_period_id and no explicit effective_from
    # was passed, use the start period's start_date as the boundary.
    if effective_from is None and rule.start_period_id and rule.start_period:
        effective_from = rule.start_period.start_date
    if effective_from is None and periods:
        effective_from = periods[0].start_date

    matching_periods = match_periods(rule, pattern_id, periods, effective_from)
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    return _GenerationPlan(rule, matching_periods, projected_id)


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
        effective_from: Optional date -- only generate for periods starting on or
                        after this date.  Defaults to the first period's start_date.

    Returns:
        List of newly created Transaction objects.
    """
    # Resolve the shared gating + period-matching preamble: cross-user
    # defense, rule/ONCE gating, effective_from defaulting, and the
    # pattern match.  A None result means generate nothing (ownership
    # failed, or no rule / ONCE).  See _resolve_generation_plan.
    plan = _resolve_generation_plan(
        template, periods, scenario_id, effective_from,
        block_message="Blocked cross-user recurrence generation",
    )
    if plan is None:
        return []

    # Check for existing transactions to avoid duplicates and respect overrides.
    existing = _get_existing_map(template.id, scenario_id, plan.matching_periods)

    # Check if this template has a linked salary profile for paycheck calculation.
    salary_profile = _get_salary_profile(template.id)

    created = []
    for period in plan.matching_periods:
        existing_txns = existing.get(period.id, [])

        # Skip periods that already hold a template-linked row (immutable,
        # override, soft-deleted, or simply already auto-generated).
        if should_skip_period(existing_txns):
            continue

        # Determine the amount -- use paycheck calculator if salary-linked.
        amount = _get_transaction_amount(
            template, salary_profile, period, periods
        )

        # Compute the due date from the rule and period context.
        due = _compute_due_date(plan.rule, period)

        # No existing entry -- create a new one.
        txn = Transaction(
            account_id=template.account_id,
            template_id=template.id,
            pay_period_id=period.id,
            scenario_id=scenario_id,
            status_id=plan.projected_id,
            name=template.name,
            category_id=template.category_id,
            transaction_type_id=template.transaction_type_id,
            estimated_amount=amount,
            is_override=False,
            is_deleted=False,
            due_date=due,
        )
        db.session.add(txn)
        created.append(txn)

    db.session.flush()
    log_event(logger, logging.INFO, EVT_RECURRENCE_GENERATED, BUSINESS,
              "Transactions generated from template",
              user_id=template.user_id,
              template_id=template.id,
              scenario_id=scenario_id,
              count=len(created))
    return created


def can_generate_in_period(template, period, scenario_id):
    """Return True iff ``generate_for_template`` would create a row in *period*.

    Read-only mirror of ``generate_for_template``'s gating logic.
    Useful to callers that need to predict the engine's behaviour
    without mutating -- e.g. the carry-forward preview endpoint
    (``carry_forward_service.preview_carry_forward``) shows the user
    whether a missing target canonical would be auto-generated or
    whether the carry-forward will refuse.

    The decision uses exactly the same predicates as
    ``generate_for_template``:

      1. Cross-user defense: scenario must belong to the template's
         user.
      2. Template must have a recurrence rule.
      3. Rule pattern must not be ``Once`` (manual placement only).
      4. The period must match the rule's pattern via
         ``match_periods`` (effective_from / end_date / pattern
         filters all apply).
      5. The (template, period, scenario) tuple must have NO existing
         rows -- not even soft-deleted ones.  The engine's per-row
         skip logic treats any existing row as a "do not generate"
         signal, so a soft-deleted carry-over also blocks generation.

    Args:
        template: The TransactionTemplate to check.  Must have its
            ``recurrence_rule`` relationship loaded (the same
            assumption ``generate_for_template`` makes).
        period: The PayPeriod object the canonical would land in.
        scenario_id: The scenario that would receive the canonical.

    Returns:
        bool -- True when the engine would create a row, False when
        any of the gating conditions would skip it.
    """
    # Defense-in-depth (mirrors generate_for_template's first guard).
    scenario = db.session.get(Scenario, scenario_id)
    if scenario is None or scenario.user_id != template.user_id:
        return False

    rule = template.recurrence_rule
    if rule is None:
        return False

    pattern_id = rule.pattern_id
    if pattern_id == ref_cache.recurrence_pattern_id(RecurrencePatternEnum.ONCE):
        return False

    # Mirror generate_for_template's effective_from default.  Without
    # an explicit value, fall back to the rule's start_period.start_date,
    # then to the supplied period's start_date -- so a single-period
    # check always has a concrete boundary to compare against.
    if rule.start_period_id and rule.start_period:
        effective_from = rule.start_period.start_date
    else:
        effective_from = period.start_date

    matching = match_periods(rule, pattern_id, [period], effective_from)
    if not matching:
        return False

    # Engine refuses to overwrite ANY existing row -- skip if even one
    # row (including soft-deleted) sits in (template, period, scenario).
    existing = _get_existing_map(template.id, scenario_id, [period])
    if existing.get(period.id):
        return False

    return True


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
    # Defense-in-depth: verify ownership before deleting and regenerating.
    if not check_scenario_ownership(
        logger, template, scenario_id,
        block_message="Blocked cross-user recurrence regeneration",
    ):
        return []

    if effective_from is None and periods:
        effective_from = periods[0].start_date

    # Find all existing template-linked transactions on or after effective_from,
    # then partition them into conflicts vs rows safe to delete and regenerate.
    existing = query_rows_from_effective_date(
        Transaction, Transaction.template_id,
        template.id, scenario_id, effective_from,
    )
    overridden_ids, deleted_ids, to_delete = partition_regeneration_rows(existing)

    # Delete the safe-to-remove entries.
    for txn in to_delete:
        db.session.delete(txn)
    db.session.flush()

    # Regenerate new entries.
    created = generate_for_template(template, periods, scenario_id, effective_from)

    log_event(
        logger, logging.INFO, EVT_RECURRENCE_REGENERATED, BUSINESS,
        "Recurrence regenerated for template",
        user_id=template.user_id,
        template_id=template.id,
        scenario_id=scenario_id,
        deleted_count=len(to_delete),
        created_count=len(created),
        overridden_conflict_count=len(overridden_ids),
        deleted_conflict_count=len(deleted_ids),
    )

    # If there are conflicts, raise so the caller can prompt the user.
    if overridden_ids or deleted_ids:
        raise RecurrenceConflict(overridden=overridden_ids, deleted=deleted_ids)

    return created


def resolve_conflicts(transaction_ids, action, user_id, new_amount=None):
    """Resolve override/delete conflicts after a regeneration.

    Called by the route layer after the user responds to the conflict prompt.
    Each transaction is ownership-checked via its pay_period.user_id before
    any modification -- transactions not owned by ``user_id`` are silently
    skipped (defense-in-depth against IDOR).

    Args:
        transaction_ids: List of Transaction IDs to resolve.
        action:          'update' -- clear override/delete, apply new amount.
                         'keep' -- leave the transaction unchanged.
        user_id:         The requesting user's ID.  Transactions not owned
                         by this user are skipped.
        new_amount:      The new default amount (required if action='update').
    """
    if action == "keep":
        # Nothing to do -- the user wants to keep their overrides.
        log_event(
            logger, logging.INFO, EVT_RECURRENCE_CONFLICTS_RESOLVED, BUSINESS,
            "Recurrence conflicts kept (no mutation)",
            user_id=user_id, action=action,
            transaction_id_count=len(transaction_ids),
        )
        return

    if action == "update":
        resolved_count = 0
        skipped_count = 0
        for txn_id in transaction_ids:
            txn = db.session.get(Transaction, txn_id)
            if txn is None:
                skipped_count += 1
                continue

            # Ownership check: Transaction -> PayPeriod -> user_id.
            if txn.pay_period.user_id != user_id:
                # Cross-user request: emit the IDOR-detection event so
                # SOC tooling sees the probe.  ACCESS-category is the
                # right home for this -- the requester does not own
                # the row even though we silently skip it.
                log_resource_access_denied(
                    logger,
                    user_id=user_id,
                    model="Transaction",
                    pk=txn_id,
                    owner_id=txn.pay_period.user_id,
                )
                skipped_count += 1
                continue

            # Transfer shadow guard (CLAUDE.md Transfer invariant 4 / F-007).
            # Shadow rows (transfer_id IS NOT NULL) are owned by the transfer
            # service.  resolve_conflicts is reachable only from the
            # transaction-template regeneration flow, which never produces
            # shadow IDs in its conflict set; a shadow ID arriving here is
            # therefore an internal logic error or an attacker probe.
            # Mutating a shadow directly would desynchronise the parent
            # transfer's amount/status/period from its sibling shadow and
            # silently corrupt the user's balance projections.  Refuse.
            if txn.transfer_id is not None:
                log_event(
                    logger, logging.WARNING,
                    EVT_RESOLVE_CONFLICTS_SHADOW_REFUSED, BUSINESS,
                    "Refused to mutate transfer shadow via resolve_conflicts",
                    user_id=user_id,
                    transaction_id=txn_id,
                    transfer_id=txn.transfer_id,
                    action=action,
                )
                raise ValidationError(
                    "Cannot modify transfer shadow transactions via "
                    "resolve_conflicts.  Route transfer mutations through "
                    "transfer_service."
                )

            txn.is_override = False
            txn.is_deleted = False
            if new_amount is not None:
                txn.estimated_amount = new_amount
            resolved_count += 1
        db.session.flush()
        log_event(
            logger, logging.INFO, EVT_RECURRENCE_CONFLICTS_RESOLVED, BUSINESS,
            "Recurrence conflicts resolved (update)",
            user_id=user_id, action=action,
            resolved_count=resolved_count,
            skipped_count=skipped_count,
            new_amount=str(new_amount) if new_amount is not None else None,
        )


# --- Pattern Matching Helpers -------------------------------------------


def _rp_id(member):
    """Shorthand for recurrence_pattern_id to keep dispatch lines readable."""
    return ref_cache.recurrence_pattern_id(member)


def match_periods(rule, pattern_id, periods, effective_from):
    """Return the subset of periods that match the recurrence pattern.

    Public, pure period-matcher: the recurrence engine generates against it
    internally, and the templates preview route renders against it, so it is
    deliberately part of this module's public surface rather than a
    leading-underscore helper.

    Args:
        rule:           The RecurrenceRule object.
        pattern_id:     The recurrence pattern integer ID.
        periods:        All candidate PayPeriod objects.
        effective_from: Only include periods starting on or after this date.

    Returns:
        Filtered list of PayPeriod objects.
    """
    # Filter by effective date first.  Use end_date so that the current
    # pay period is included when effective_from falls mid-period.
    candidates = [p for p in periods if p.end_date >= effective_from]

    # Filter by rule end_date -- stop generating after this date.
    if rule.end_date is not None:
        candidates = [p for p in candidates if p.start_date <= rule.end_date]

    # Dispatch on pattern through a single exit.  Each branch resolves the
    # matching subset from the pre-filtered candidates; ``Once`` is absent by
    # design (manual placement only) and falls through to the empty default.
    if pattern_id == _rp_id(RecurrencePatternEnum.EVERY_PERIOD):
        matches = candidates
    elif pattern_id == _rp_id(RecurrencePatternEnum.EVERY_N_PERIODS):
        n = rule.interval_n
        offset = rule.offset_periods
        matches = [p for p in candidates if (p.period_index - offset) % n == 0]
    elif pattern_id == _rp_id(RecurrencePatternEnum.MONTHLY):
        matches = _match_monthly(candidates, rule.day_of_month or 1)
    elif pattern_id == _rp_id(RecurrencePatternEnum.MONTHLY_FIRST):
        matches = _match_monthly_first(candidates)
    elif pattern_id == _rp_id(RecurrencePatternEnum.QUARTERLY):
        matches = _match_quarterly(
            candidates, rule.month_of_year or 1, rule.day_of_month or 1,
        )
    elif pattern_id == _rp_id(RecurrencePatternEnum.SEMI_ANNUAL):
        matches = _match_semi_annual(
            candidates, rule.month_of_year or 1, rule.day_of_month or 1,
        )
    elif pattern_id == _rp_id(RecurrencePatternEnum.ANNUAL):
        matches = _match_annual(
            candidates, rule.month_of_year or 1, rule.day_of_month or 1,
        )
    else:
        # Unknown pattern -- match nothing.
        logger.warning("Unknown recurrence pattern ID: %s", pattern_id)
        matches = []

    return matches


def _match_monthly(periods, day_of_month):
    """Find the pay period that contains a given day_of_month each month.

    For each unique (year, month) in the periods, find the period whose
    date range includes that month's target day.
    """

    matched = []
    seen_months = set()

    for period in periods:
        # Check each month the period might span.
        for dt in (period.start_date, period.end_date):
            year_month = (dt.year, dt.month)
            if year_month in seen_months:
                continue

            # Clamp day_of_month to the actual last day of the month.
            last_day = cal.monthrange(dt.year, dt.month)[1]
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
    matched = []
    seen = set()

    for period in periods:
        for dt in (period.start_date, period.end_date):
            key = (dt.year, dt.month)
            if key in seen or dt.month not in target_months:
                continue

            last_day = cal.monthrange(dt.year, dt.month)[1]
            target_day = min(day_of_month, last_day)
            target_date = date(dt.year, dt.month, target_day)

            if period.start_date <= target_date <= period.end_date:
                matched.append(period)
                seen.add(key)

    return matched


def _match_annual(periods, month, day):
    """Find the pay period that contains a specific month/day each year."""
    matched = []
    seen_years = set()

    for period in periods:
        for dt in (period.start_date, period.end_date):
            if dt.year in seen_years:
                continue

            last_day = cal.monthrange(dt.year, month)[1]
            target_day = min(day, last_day)
            target_date = date(dt.year, month, target_day)

            if period.start_date <= target_date <= period.end_date:
                matched.append(period)
                seen_years.add(dt.year)

    return matched


def _compute_due_date(rule, period):
    """Compute the due_date for a generated transaction.

    Derives the calendar date the bill is actually due, using the
    recurrence rule's scheduling day and optional due-day override.

    Source priority:
      1. rule.due_day_of_month (if set and differs from day_of_month)
      2. rule.day_of_month (placed within the period's month context)
      3. period.start_date (for every-paycheck patterns with no day)

    Next-month convention: if due_day_of_month < day_of_month, the due
    date falls in the following calendar month.  Example: day_of_month=22
    with due_day_of_month=1 means the bill is due on the 1st of the
    next month after the scheduling month.

    Month-end clamping: day values exceeding the month's last day are
    clamped (e.g. day 31 in April becomes 30, day 30 in Feb becomes 28).

    Args:
        rule: The RecurrenceRule with day_of_month and due_day_of_month.
        period: The PayPeriod the transaction was assigned to.

    Returns:
        A date object representing the due date.
    """
    dom = rule.day_of_month
    due_dom = rule.due_day_of_month

    # Patterns without day_of_month (every-paycheck, every-N): use period start.
    if dom is None:
        return period.start_date

    # Determine the base month by finding which month within the period
    # contains the day_of_month target.  Mirrors the logic in
    # _match_monthly() which checks both start_date and end_date months.
    base_year = period.start_date.year
    base_month = period.start_date.month

    for dt in (period.start_date, period.end_date):
        last_day = cal.monthrange(dt.year, dt.month)[1]
        target_day = min(dom, last_day)
        target = date(dt.year, dt.month, target_day)
        if period.start_date <= target <= period.end_date:
            base_year = dt.year
            base_month = dt.month
            break

    if due_dom is None or due_dom == dom:
        # No separate due date -- use day_of_month in the base month.
        last_day = cal.monthrange(base_year, base_month)[1]
        return date(base_year, base_month, min(dom, last_day))

    # Next-month convention: due_day_of_month < day_of_month means the
    # due date falls in the month after the scheduling month.
    if due_dom < dom:
        if base_month == 12:
            due_year = base_year + 1
            due_month = 1
        else:
            due_year = base_year
            due_month = base_month + 1
    else:
        due_year = base_year
        due_month = base_month

    last_day = cal.monthrange(due_year, due_month)[1]
    return date(due_year, due_month, min(due_dom, last_day))


def _get_existing_map(template_id, scenario_id, periods):
    """Build a dict of period_id → [Transaction, ...] for existing template entries.

    Uses a list per period to avoid silent dict overwrites when a deleted and
    non-deleted transaction share the same period_id.  Fetches all entries
    (including deleted) to check for duplicates and respect override/delete flags.
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
    result = defaultdict(list)
    for txn in existing:
        result[txn.pay_period_id].append(txn)
    return result


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
    """Determine the transaction amount, using paycheck calculator if salary-linked.

    Resolves tax configs for the period's OWN tax year via the shared
    ``load_tax_configs_for_year`` SSOT (current-year fallback when a future
    year has no configs at all).  The salary projection page and the
    live net-pay recompute (``income_service.live_projected_net``) resolve
    the SAME way (DH-#30), so the grid's stored income amount and the
    salary page's live-calculated net pay agree on which year's brackets
    and FICA wage base/cap apply -- they cannot silently diverge.
    """
    if salary_profile is None:
        return template.default_amount

    try:
        # Local imports: the tax-config / paycheck fallback tests patch the
        # SOURCE modules (app.services.tax_config_service.load_tax_configs and
        # app.services.paycheck_calculator.calculate_paycheck -- the
        # testing-standards-preferred patch target).  A module-level
        # ``from ... import`` would bind the name once at import and not see
        # the patch, so these imports stay local.
        # Pylint: ``import-outside-toplevel`` -- kept local so the fallback
        # tests' patches of app.services.paycheck_calculator take effect.
        from app.services import paycheck_calculator  # pylint: disable=import-outside-toplevel
        # Pylint: ``import-outside-toplevel`` -- kept local so the fallback
        # tests' patches of app.services.tax_config_service.load_tax_configs
        # take effect (load_tax_configs_for_year calls it internally).
        from app.services.tax_config_service import load_tax_configs_for_year  # pylint: disable=import-outside-toplevel

        # Resolve the period's own tax year, falling back to the current
        # year when that year has no configs at all (else future-year
        # periods would produce zero federal tax and the grid would
        # disagree with the salary page).  The fallback rule is owned ONCE
        # by load_tax_configs_for_year, the SSOT shared with the salary
        # projection and the year-end summary (DH-#30).
        tax_configs = load_tax_configs_for_year(
            salary_profile.user_id, salary_profile, period.start_date.year,
        )

        # Load calibration override if the profile has one.
        calibration = getattr(salary_profile, "calibration", None)

        breakdown = paycheck_calculator.calculate_paycheck(
            salary_profile, period, all_periods, tax_configs,
            calibration=calibration,
        )
        return breakdown.earnings.net_pay

    except (InvalidOperation, ZeroDivisionError, TypeError, KeyError) as exc:
        logger.error(
            "Paycheck calculation failed for salary profile %d in "
            "period %s: %s. Using template default_amount.",
            salary_profile.id,
            period.start_date,
            exc,
        )
        return template.default_amount
