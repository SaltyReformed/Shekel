"""
Shekel Budget App -- Dashboard Service

Aggregates data from multiple existing services into a single
template-ready structure for the summary dashboard.  Calls existing
services for balance, paycheck, savings, and debt computations --
does NOT duplicate their logic.

Pure aggregation service -- no Flask imports, no database writes.
"""

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import joinedload, selectinload

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.user import UserSettings
from app.services import balance_resolver, pay_period_service, paycheck_calculator
from app.services.account_resolver import resolve_grid_account
from app.services.entry_service import compute_entry_sums, compute_remaining
from app.services.scenario_resolver import get_baseline_scenario
from app.services.tax_config_service import load_tax_configs
from app.utils.balance_predicates import is_projected_clause, settled_status_ids
from app.utils.money import percent_complete

_TWO_PLACES = Decimal("0.01")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_THIRTY_DAYS = 30


def compute_dashboard_data(user_id: int) -> dict:
    """Assemble all dashboard sections into a single dict.

    Returns a dict with keys for each section plus metadata.  Every
    section handles missing data gracefully -- the dashboard always
    renders, even with incomplete setup.

    Args:
        user_id: The user's ID.

    Returns:
        Dict with keys: has_default_account, current_period,
        upcoming_bills, alerts, balance_info, payday_info,
        savings_goals, debt_summary, spending_comparison.
    """
    settings = _get_user_settings(user_id)
    account = resolve_grid_account(user_id, settings)

    if account is None:
        return _empty_dashboard(has_default_account=False)

    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        return _empty_dashboard(has_default_account=True)

    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)
    next_period = (
        pay_period_service.get_next_period(current_period)
        if current_period else None
    )

    # Compute balances once for alerts and balance_info.
    balance_results = _compute_balances(account, all_periods, scenario)

    return {
        "has_default_account": True,
        "current_period": current_period,
        "upcoming_bills": _get_upcoming_bills(
            account.id, scenario.id, current_period, next_period,
        ),
        "alerts": _compute_alerts(
            account, settings, balance_results, current_period, all_periods,
        ),
        "balance_info": _get_balance_info(
            account, scenario.id, current_period, balance_results,
        ),
        "payday_info": _get_payday_info(user_id, all_periods),
        "savings_goals": _get_savings_goals(user_id),
        "debt_summary": _get_debt_summary(user_id),
        "spending_comparison": _get_spending_comparison(
            account.id, scenario.id, current_period, all_periods,
        ),
    }


# ── Section 1: Upcoming Bills ──────────────────────────────────────


def _get_upcoming_bills(
    account_id: int,
    scenario_id: int,
    current_period: PayPeriod | None,
    next_period: PayPeriod | None,
) -> list[dict]:
    """Get unpaid expense transactions for current and next periods.

    Returns bills sorted by due_date ascending, then name.  Bills
    without a due_date sort by their pay period's start_date.

    Each bill dict includes entry progress fields (is_tracked,
    entry_total, entry_count, entry_remaining, entry_over_budget)
    that the template uses to show "spent / budget" progress for
    entry-capable transactions with recorded entries.  Non-tracked
    bills and tracked bills without entries get None/0/False values
    and the template falls back to the standard amount display.

    Returns an empty list if no current period exists.
    """
    if current_period is None:
        return []

    period_ids = [current_period.id]
    if next_period is not None:
        period_ids.append(next_period.id)

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

    # selectinload(entries) + joinedload(template) avoid N+1 lookups
    # when the template checks is_envelope and the helper below
    # iterates entries for progress computation.
    # The Projected filter routes through the centralized
    # ``is_projected_clause`` (D6-09 / MED-02) so every SQL filter
    # over Projected shares one definition with the Python
    # ``is_projected`` predicate.
    txns = (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.category),
            joinedload(Transaction.pay_period),
            joinedload(Transaction.template),
            selectinload(Transaction.entries),
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
            is_projected_clause(Transaction),
            Transaction.transaction_type_id == expense_type_id,
        )
        .all()
    )

    today = date.today()
    bills = []
    for txn in txns:
        sort_date = txn.due_date if txn.due_date else txn.pay_period.start_date
        bill = txn_to_bill_dict(txn, today)
        bill["_sort_date"] = sort_date
        bills.append(bill)

    bills.sort(key=lambda b: (b["_sort_date"], b["name"]))
    # Remove internal sort key before returning.
    for bill in bills:
        del bill["_sort_date"]

    return bills


def _is_entry_tracked(txn: Transaction) -> bool:
    """Return True if the transaction's template enables envelope tracking.

    Per E-21 (MED-03 / F-028 / F-056) entry-tracked bill rows anchor
    every visible figure (amount cell, remaining, over-budget flag) on
    ``estimated_amount`` -- the declared budget base -- so the row's
    three numbers always answer the same question.  Centralising the
    "is this row entry-tracked" check here keeps :func:`txn_to_bill_dict`
    and :func:`_entry_progress_fields` from re-deriving it inline (and
    so cannot drift apart): both call this helper.

    Args:
        txn: The Transaction to inspect.  ``txn.template`` must be
            accessible (eager-loaded by the caller for collections).

    Returns:
        True when the transaction is purchase-tracked -- either its
        template has ``is_envelope = True`` or, for an ad-hoc row, its
        own ``is_envelope`` flag is set; otherwise False.
    """
    return txn.tracks_purchases


def txn_to_bill_dict(txn: Transaction, today: date) -> dict:
    """Build a bill dict for the dashboard bills template from a Transaction.

    Shared between the service's _get_upcoming_bills loop and the
    dashboard route's mark-paid response helper so both produce
    dicts with the same shape.  The caller is responsible for any
    additional fields (e.g. is_paid for the mark-paid partial).

    Expects txn.template and txn.entries to be accessible -- callers
    dealing with collections should eager-load them via selectinload
    /joinedload to avoid N+1 queries.

    E-21 / MED-03 / F-028 / F-056: for entry-tracked (envelope) bills
    the ``amount`` field is set from ``estimated_amount`` so it shares
    the same declared base as ``entry_remaining`` and
    ``entry_over_budget`` (also derived from ``estimated_amount`` in
    :func:`_entry_progress_fields`).  ``amount_base`` carries the
    label the template surfaces to the user ("budget") so the base is
    disclosed in the UI, not implicit.  Non-entry-tracked rows keep
    ``effective_amount`` (tier-3 actual when populated, otherwise
    estimated) because the row has no progress fields to be
    inconsistent with; ``amount_base`` is None there so the template
    skips the label.

    Args:
        txn: The Transaction to convert.
        today: The reference date used to compute days_until_due.

    Returns:
        Dict matching the bills template contract, including the
        entry progress fields from _entry_progress_fields and the
        ``amount_base`` label that discloses which base the amount
        cell uses.
    """
    days_until = (txn.due_date - today).days if txn.due_date else None
    is_entry_tracked = _is_entry_tracked(txn)
    if is_entry_tracked:
        amount = txn.estimated_amount
        amount_base = "budget"
    else:
        amount = txn.effective_amount
        amount_base = None
    bill = {
        "id": txn.id,
        "name": txn.name,
        "amount": amount,
        "amount_base": amount_base,
        "due_date": txn.due_date,
        "period_start_date": txn.pay_period.start_date,
        "category_group": txn.category.group_name if txn.category else None,
        "category_item": txn.category.item_name if txn.category else None,
        "is_transfer": txn.transfer_id is not None,
        "days_until_due": days_until,
    }
    bill.update(_entry_progress_fields(txn))
    return bill


def _entry_progress_fields(txn: Transaction) -> dict:
    """Build entry progress fields for a bill dict from a Transaction.

    Returns a dict with keys is_tracked, entry_total, entry_count,
    entry_remaining, and entry_over_budget.  When the transaction is
    not entry-capable (no template with is_envelope) or has no
    recorded entries, the progress fields are None/0/False and the
    dashboard template falls back to the standard amount display.
    Otherwise returns the debit+credit sum, the remaining budget,
    and a flag indicating whether the sum exceeds the estimated
    amount.

    Per E-21 / MED-03 / F-028 / F-056 the remaining and over-budget
    figures are computed against ``txn.estimated_amount`` -- the
    declared E-21 budget base -- so the row's three numbers (amount,
    remaining, over-budget) all share one base.  ``txn_to_bill_dict``
    anchors the amount cell on the same base; the template surfaces
    ``bill.amount_base`` to disclose it.

    Expects txn.template and txn.entries to already be loaded on the
    transaction object (eager-loaded by the caller).

    Args:
        txn: The Transaction to inspect.

    Returns:
        Dict with the five entry progress fields.
    """
    is_tracked = _is_entry_tracked(txn)
    if not is_tracked or not txn.entries:
        return {
            "is_tracked": is_tracked,
            "entry_total": None,
            "entry_count": 0,
            "entry_remaining": None,
            "entry_over_budget": False,
        }

    debit, credit = compute_entry_sums(txn.entries)
    total = debit + credit
    remaining = compute_remaining(txn.estimated_amount, txn.entries)
    return {
        "is_tracked": True,
        "entry_total": total,
        "entry_count": len(txn.entries),
        "entry_remaining": remaining,
        "entry_over_budget": total > txn.estimated_amount,
    }


# ── Section 2: Alerts ──────────────────────────────────────────────


def _compute_alerts(
    account: Account,
    settings: UserSettings | None,
    balance_results: dict[int, Decimal] | None,
    current_period: PayPeriod | None,
    all_periods: list[PayPeriod],
) -> list[dict]:
    """Compute actionable alerts for the dashboard.

    Alert types:
    1. Stale anchor -- checking balance not updated recently.
    2. Negative projected balance -- future period goes negative.
    3. Low balance -- current balance below threshold.

    Returns alerts sorted by severity (danger first, then warning).
    """
    alerts: list[dict] = []
    staleness_days = settings.anchor_staleness_days if settings else 14

    # Alert 1: Stale anchor.
    last_anchor = _get_last_anchor_date(account.id)
    if last_anchor is None:
        alerts.append({
            "type": "stale_anchor",
            "message": "Your checking balance has never been set.",
            "severity": "warning",
            "link": "/",
        })
    else:
        days_since = (date.today() - last_anchor.date()).days
        if days_since > staleness_days:
            alerts.append({
                "type": "stale_anchor",
                "message": (
                    f"Your checking balance hasn't been updated in {days_since} days."
                ),
                "severity": "warning",
                "link": "/",
            })

    # Alert 2: Negative projected balance.
    if balance_results and current_period:
        for period in all_periods:
            if period.start_date <= date.today():
                continue
            bal = balance_results.get(period.id)
            if bal is not None and bal < _ZERO:
                alerts.append({
                    "type": "negative_balance",
                    "message": (
                        f"Projected balance goes negative on "
                        f"{period.start_date.strftime('%b %d, %Y')}."
                    ),
                    "severity": "danger",
                    "link": "/",
                })
                break  # Only the first negative period.

    # Alert 3: Low balance.
    low_threshold = settings.low_balance_threshold if settings else 500
    if balance_results and current_period:
        current_bal = balance_results.get(current_period.id)
        if current_bal is not None and current_bal < Decimal(str(low_threshold)):
            alerts.append({
                "type": "low_balance",
                "message": (
                    f"Current projected balance "
                    f"(${current_bal:,.2f}) is below ${low_threshold:,}."
                ),
                "severity": "warning",
                "link": "/",
            })

    # Sort: danger first, then warning.
    severity_order = {"danger": 0, "warning": 1}
    alerts.sort(key=lambda a: severity_order.get(a["severity"], 2))
    return alerts


# ── Section 3: Balance and Cash Runway ─────────────────────────────


def _get_balance_info(
    account: Account,
    scenario_id: int,
    current_period: PayPeriod | None,
    balance_results: dict[int, Decimal] | None,
) -> dict:
    """Get current balance and compute cash runway.

    Cash runway uses paid expenses from the last 30 calendar days
    (by due_date, consistent with calendar service attribution),
    scoped to ``scenario_id``.  Runway = current_balance /
    daily_average_spending.

    Returns None for runway when there is zero spending (avoids
    infinity) and clamps negative balance to 0 runway days.
    """
    if current_period is not None and balance_results:
        current_balance = balance_results.get(
            current_period.id, account.current_anchor_balance or _ZERO,
        )
    else:
        current_balance = account.current_anchor_balance or _ZERO

    last_anchor_dt = _get_last_anchor_date(account.id)
    staleness_days = 14  # Default; caller can override via alerts.
    anchor_is_stale = False
    if last_anchor_dt is None:
        anchor_is_stale = True
    else:
        anchor_is_stale = (date.today() - last_anchor_dt.date()).days > staleness_days

    runway = _compute_cash_runway(account.id, scenario_id, current_balance)

    return {
        "current_balance": current_balance,
        "cash_runway_days": runway,
        "account_id": account.id,
        "account_name": account.name,
        "last_true_up_date": last_anchor_dt,
        "anchor_is_stale": anchor_is_stale,
    }


def _compute_cash_runway(
    account_id: int,
    scenario_id: int,
    current_balance: Decimal,
) -> int | None:
    """Compute cash runway in days from recent spending rate.

    Queries paid expenses from the last 30 days by due_date,
    consistent with calendar service date attribution, and scoped to
    ``scenario_id`` so what-if scenarios never spill into the baseline
    spending average -- matching the sibling dashboard expense queries
    (_get_upcoming_bills, _sum_settled_expenses).

    Returns None if no spending (avoids infinity), 0 if balance
    is negative.
    """
    if current_balance <= _ZERO:
        return 0

    thirty_days_ago = date.today() - timedelta(days=_THIRTY_DAYS)
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

    txns = (
        db.session.query(Transaction)
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.is_deleted.is_(False),
            Transaction.status_id.in_(settled_status_ids()),
            Transaction.transaction_type_id == expense_type_id,
            Transaction.due_date >= thirty_days_ago,
            Transaction.due_date <= date.today(),
        )
        .all()
    )

    total_spending = sum(abs(txn.effective_amount) for txn in txns)
    if total_spending == _ZERO:
        return None

    daily_avg = total_spending / Decimal(str(_THIRTY_DAYS))
    runway = current_balance / daily_avg
    return int(runway.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# ── Section 4: Payday Info ─────────────────────────────────────────


def _get_payday_info(
    user_id: int,
    all_periods: list[PayPeriod],
) -> dict:
    """Compute days until next paycheck and projected net amount.

    Returns None values gracefully if no salary profile or no
    future period exists.
    """
    today = date.today()
    next_period = None
    for p in all_periods:
        if p.start_date > today:
            next_period = p
            break

    if next_period is None:
        return {"days_until": None, "next_amount": None, "next_date": None}

    days_until = (next_period.start_date - today).days
    net_pay = _get_net_pay_for_period(user_id, next_period, all_periods)

    return {
        "days_until": days_until,
        "next_amount": net_pay,
        "next_date": next_period.start_date,
    }


def _get_net_pay_for_period(
    user_id: int,
    period: PayPeriod,
    all_periods: list[PayPeriod],
) -> Decimal | None:
    """Compute net pay for a specific period using the paycheck calculator.

    Returns None if no active salary profile exists.
    """

    profile = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=user_id, is_active=True)
        .first()
    )
    if profile is None:
        return None

    tax_configs = load_tax_configs(user_id, profile)
    breakdown = paycheck_calculator.calculate_paycheck(
        profile, period, all_periods, tax_configs,
    )
    return breakdown.earnings.net_pay


# ── Section 5: Savings Goals ──────────────────────────────────────


def _get_savings_goals(user_id: int) -> list[dict]:
    """Get active savings goal progress.

    For each goal, computes percentage complete from the account's
    current anchor balance relative to the target amount.
    """
    goals = (
        db.session.query(SavingsGoal)
        .options(joinedload(SavingsGoal.account))
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )

    result: list[dict] = []
    for goal in goals:
        current = goal.account.current_anchor_balance or _ZERO
        target = goal.target_amount or _ZERO
        pct = percent_complete(current, target)

        result.append({
            "name": goal.name,
            "current_balance": current,
            "target_amount": target,
            "pct_complete": pct,
            "account_name": goal.account.name,
            "account_id": goal.account.id,
        })

    return result


# ── Section 6: Debt Summary ───────────────────────────────────────


def _get_debt_summary(user_id: int) -> dict | None:
    """Get debt summary by calling the savings dashboard service.

    Reuses existing logic from ``savings_dashboard_service`` to avoid
    duplicating debt computation.  Returns ``None`` when the user has no
    loan accounts: ``compute_dashboard_data`` surfaces that as a
    ``debt_summary`` of ``None`` (``_compute_debt_summary`` returns
    ``None`` with no loan accounts), which the ``.get`` below yields
    directly.

    No exception is caught here.  ``compute_dashboard_data`` is the same
    producer the savings route (``app/routes/savings.py``) calls without
    a guard, and every sibling dashboard section here
    (``_get_savings_goals``, ``_get_spending_comparison``,
    ``_compute_cash_runway``) is likewise unguarded.  A ``ValueError`` /
    ``KeyError`` / ``AttributeError`` from that computation is a
    programming bug, not the no-debt signal; swallowing it would silently
    blank the debt panel and hide real debt (CLAUDE.md rule 4).  Letting
    it propagate fails loud and identically on the dashboard and savings
    pages.
    """
    # Pylint: ``import-outside-toplevel`` -- Deferred: savings_dashboard_service
    # pulls the heaviest service import chain (+27 modules, measured); loaded only
    # when the debt-summary path runs, not on every dashboard_service import.
    from app.services import savings_dashboard_service  # pylint: disable=import-outside-toplevel

    return savings_dashboard_service.compute_dashboard_data(user_id).get("debt_summary")


# ── Section 7: Spending Comparison ─────────────────────────────────


def _get_spending_comparison(
    account_id: int,
    scenario_id: int,
    current_period: PayPeriod | None,
    all_periods: list[PayPeriod],
) -> dict:
    """Compare paid expense spending between current and prior periods.

    Returns delta, percentage change, and direction.  Handles missing
    prior period, zero spending, and same-amount cases gracefully.
    """
    if current_period is None:
        return _empty_comparison()

    prior_period = _find_prior_period(current_period, all_periods)

    current_total = _sum_settled_expenses(account_id, scenario_id, current_period.id)

    if prior_period is None:
        return {
            "current_total": current_total,
            "prior_total": None,
            "delta": None,
            "delta_pct": None,
            "direction": None,
        }

    prior_total = _sum_settled_expenses(account_id, scenario_id, prior_period.id)
    delta = current_total - prior_total

    if prior_total == _ZERO:
        delta_pct = None
    else:
        delta_pct = (delta / prior_total * _HUNDRED).quantize(
            _TWO_PLACES, rounding=ROUND_HALF_UP,
        )

    if delta > _ZERO:
        direction = "higher"
    elif delta < _ZERO:
        direction = "lower"
    else:
        direction = "same"

    return {
        "current_total": current_total,
        "prior_total": prior_total,
        "delta": delta,
        "delta_pct": delta_pct,
        "direction": direction,
    }


def _sum_settled_expenses(
    account_id: int,
    scenario_id: int,
    period_id: int,
) -> Decimal:
    """Sum effective_amount of settled expense transactions in a period."""
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

    txns = (
        db.session.query(Transaction)
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id == period_id,
            Transaction.is_deleted.is_(False),
            Transaction.status_id.in_(settled_status_ids()),
            Transaction.transaction_type_id == expense_type_id,
        )
        .all()
    )

    return sum(abs(txn.effective_amount) for txn in txns)


def _find_prior_period(
    current: PayPeriod,
    all_periods: list[PayPeriod],
) -> PayPeriod | None:
    """Find the period immediately before the current one by index."""
    for p in reversed(all_periods):
        if p.period_index < current.period_index:
            return p
    return None


# ── Shared helpers ─────────────────────────────────────────────────


def _get_user_settings(user_id: int) -> UserSettings | None:
    """Load user settings."""
    return (
        db.session.query(UserSettings)
        .filter_by(user_id=user_id)
        .first()
    )


def _get_last_anchor_date(account_id: int):
    """Return the created_at of the most recent anchor history entry.

    Returns None if no anchor history exists.
    """
    entry = (
        db.session.query(AccountAnchorHistory.created_at)
        .filter_by(account_id=account_id)
        .order_by(AccountAnchorHistory.created_at.desc())
        .first()
    )
    return entry[0] if entry else None


def _compute_balances(
    account: Account,
    periods: list[PayPeriod],
    scenario: Scenario,
) -> dict[int, Decimal] | None:
    """Run the canonical balance producer for the default account.

    Routes through :func:`app.services.balance_resolver.balances_for`
    (E-25 / Commit 5).  The producer owns its own
    ``selectinload(Transaction.entries)`` query, so this helper no
    longer assembles one of its own: the entries-aware reduction is
    applied unconditionally regardless of how this function is
    invoked, which is the structural fix for CRIT-01 / F-009 /
    symptom #1.  The pre-Commit-5 dashboard already eager-loaded
    entries, so the returned values are byte-identical to the
    pre-routing computation -- this routing change is regression-safe
    for the dashboard's pinned tests.

    Returns the period_id -> Decimal balance mapping, or None if no
    periods were supplied.  Post-Commit-3 every account has a
    resolvable anchor, so the historical ``current_anchor_period_id
    is None`` guard is no longer needed; the producer raises
    ``RuntimeError`` if the invariant ever regresses.
    """
    if not periods:
        return None

    balance_result = balance_resolver.balances_for(
        account, scenario.id, periods,
    )
    return dict(balance_result.balances)


def _empty_dashboard(has_default_account: bool = True) -> dict:
    """Return a dashboard dict with all sections empty."""
    return {
        "has_default_account": has_default_account,
        "current_period": None,
        "upcoming_bills": [],
        "alerts": [],
        "balance_info": None,
        "payday_info": {"days_until": None, "next_amount": None, "next_date": None},
        "savings_goals": [],
        "debt_summary": None,
        "spending_comparison": _empty_comparison(),
    }


def _empty_comparison() -> dict:
    """Return an empty spending comparison dict."""
    return {
        "current_total": _ZERO,
        "prior_total": None,
        "delta": None,
        "delta_pct": None,
        "direction": None,
    }
