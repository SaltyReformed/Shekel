"""
Shekel Budget App -- Dashboard Service

Aggregates data from multiple existing services into a single
template-ready structure for the summary dashboard.  Calls existing
services for balance, paycheck, savings, and debt computations --
does NOT duplicate their logic.

Pure aggregation service -- no Flask imports, no database writes.
"""

from dataclasses import dataclass
from datetime import date, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import joinedload, selectinload

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.models.salary_profile import SalaryProfile
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.user import UserSettings
from app.services import balance_resolver, pay_period_service, paycheck_calculator
from app.services.account_resolver import resolve_grid_account
from app.services.entry_service import compute_entry_sums, compute_remaining
from app.services.scenario_resolver import get_baseline_scenario
from app.services.tax_config_service import load_tax_configs_for_year
from app.utils.balance_predicates import is_projected_clause, settled_status_ids

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
        savings_goals, debt_summary.
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

    # Compute balances once for the alerts' negative-projection scan.
    balance_results = _compute_balances(account, all_periods, scenario)
    projection = _PeriodProjection(
        balance_results=balance_results,
        current_period=current_period,
        all_periods=all_periods,
    )
    balance_info = _get_balance_info(
        account, scenario.id, current_period,
    )

    return {
        "has_default_account": True,
        "current_period": current_period,
        "upcoming_bills": _get_upcoming_bills(
            account.id, scenario.id, current_period, next_period,
        ),
        "alerts": _compute_alerts(
            account, settings, projection, balance_info["current_balance"],
        ),
        "balance_info": balance_info,
        "payday_info": _get_payday_info(user_id, all_periods),
        "savings_goals": _get_savings_goals(user_id),
        "debt_summary": _get_debt_summary(user_id),
    }


def compute_bills_section(user_id: int) -> dict:
    """Compute only the data the upcoming-bills partial renders.

    The narrow producer behind ``dashboard.bills_section`` (fix H): it
    resolves the account, baseline scenario, and current/next period, and
    returns the grouped bill list.  It skips the full
    ``compute_dashboard_data`` build -- the balance projection, the
    alerts, the deferred heavy debt-import chain, savings, and payday --
    none of which the bills partial reads, and which the rewired
    ``balanceChanged`` refresh (fix G) would otherwise recompute on every
    transaction change while the dashboard is open.

    Returns a dict with the single key ``upcoming_bills`` -- an empty list
    when the user has no account, scenario, or current period (the partial
    renders the empty state cleanly).  Each bill group already carries its
    own period date-range header, so the partial no longer needs a
    separate ``current_period`` (the audit's Card 1 grouping fix).
    """
    account, scenario, current_period = _resolve_section_context(user_id)
    if account is None or scenario is None or current_period is None:
        return {"upcoming_bills": []}

    next_period = pay_period_service.get_next_period(current_period)
    return {
        "upcoming_bills": _get_upcoming_bills(
            account.id, scenario.id, current_period, next_period,
        ),
    }


def compute_balance_section(user_id: int) -> dict:
    """Compute only the data the balance/runway partial renders.

    The narrow producer behind ``dashboard.balance_section`` (fix H).
    ``balance_section`` is on the live ``balanceChanged`` refresh path, so
    running the full ``compute_dashboard_data`` (including the deferred
    heavy debt-import chain) on every balance change just to render this
    one card was wasteful.  This computes only ``balance_info``.

    Returns a dict with key ``balance_info`` (``None`` when the user has
    no account, scenario, or current period).
    """
    account, scenario, current_period = _resolve_section_context(user_id)
    if account is None or scenario is None:
        return {"balance_info": None}

    return {
        "balance_info": _get_balance_info(
            account, scenario.id, current_period,
        ),
    }


def _resolve_section_context(
    user_id: int,
) -> tuple[Account | None, Scenario | None, PayPeriod | None]:
    """Resolve the account, baseline scenario, and current period.

    The shared head-of-function resolution the narrow partial producers
    (:func:`compute_bills_section`, :func:`compute_balance_section`)
    both need, so the resolution is defined once rather than copied.

    Returns:
        ``(account, scenario, current_period)``.  ``account`` is ``None``
        when the user has no resolvable grid account; ``scenario`` is
        ``None`` when there is no baseline scenario; ``current_period``
        is ``None`` when no period contains today.
    """
    settings = _get_user_settings(user_id)
    account = resolve_grid_account(user_id, settings)
    if account is None:
        return None, None, None

    scenario = get_baseline_scenario(user_id)
    current_period = pay_period_service.get_current_period(user_id)
    return account, scenario, current_period


# ── Section 1: Upcoming Bills ──────────────────────────────────────


def _get_upcoming_bills(
    account_id: int,
    scenario_id: int,
    current_period: PayPeriod | None,
    next_period: PayPeriod | None,
) -> list[dict]:
    """Get unpaid expense transactions grouped by pay period.

    Returns one group dict per period that has at least one bill, in
    chronological order (current period first, then next).  Each group
    carries the period's identity (``period_id``, ``period_start_date``,
    ``period_end_date``) so the template can render a date-range header
    that actually describes the rows beneath it -- the audit's Card 1
    fix, where a flat list under a current-period-only header silently
    mislabeled next-period bills.

    Within a group, bills are sorted by due_date ascending, then name;
    bills without a due_date sort by their pay period's start_date.

    Each bill dict includes entry progress fields (is_tracked,
    entry_total, entry_count, entry_remaining, entry_over_budget,
    entry_over_budget_amount) that the template uses to show "spent /
    budget" progress for entry-capable transactions with recorded
    entries.  Non-tracked bills and tracked bills without entries get
    None/0/False values and the template falls back to the standard
    amount display.

    Returns an empty list if no current period exists.
    """
    if current_period is None:
        return []

    # Ordered so the rendered groups read current-period-first.
    ordered_periods = [current_period]
    if next_period is not None:
        ordered_periods.append(next_period)
    period_ids = [p.id for p in ordered_periods]

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

    return _group_bills_by_period(txns, ordered_periods)


def _group_bills_by_period(
    txns: list[Transaction],
    ordered_periods: list[PayPeriod],
) -> list[dict]:
    """Group bill dicts under their pay period, in the given period order.

    Builds one render-ready bill dict per transaction, buckets them by
    ``pay_period_id``, and emits one group dict per period (in
    ``ordered_periods`` order) that has at least one bill.  Within a
    group, bills are sorted by due_date ascending (falling back to the
    period start_date for null due dates), then name.

    Args:
        txns: The upcoming expense transactions, with ``pay_period``,
            ``template``, and ``entries`` eager-loaded.
        ordered_periods: The periods to emit groups for, in display
            order (current period first, then next).

    Returns:
        A list of group dicts with keys ``period_id``,
        ``period_start_date``, ``period_end_date``, and ``bills``.
    """
    today = date.today()
    bills_by_period: dict[int, list[dict]] = {}
    for txn in txns:
        sort_date = txn.due_date if txn.due_date else txn.pay_period.start_date
        bill = txn_to_bill_dict(txn, today)
        bill["_sort_date"] = sort_date
        bills_by_period.setdefault(txn.pay_period_id, []).append(bill)

    groups: list[dict] = []
    for period in ordered_periods:
        period_bills = bills_by_period.get(period.id)
        if not period_bills:
            continue
        period_bills.sort(key=lambda b: (b["_sort_date"], b["name"]))
        for bill in period_bills:
            del bill["_sort_date"]
        groups.append({
            "period_id": period.id,
            "period_start_date": period.start_date,
            "period_end_date": period.end_date,
            "bills": period_bills,
        })

    return groups


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

    Used by the service's ``_get_upcoming_bills`` loop to produce one
    render-ready dict per upcoming bill.

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
            "entry_over_budget_amount": None,
        }

    debit, credit = compute_entry_sums(txn.entries)
    total = debit + credit
    remaining = compute_remaining(txn.estimated_amount, txn.entries)
    over_budget = total > txn.estimated_amount
    # Templates display, never compute (coding-standards): the
    # over-budget overage is the positive dollar amount by which the
    # entries exceed the declared budget base.  Computing it here keeps
    # the ``|abs`` arithmetic out of the bill-row template, where it
    # previously lived.  ``None`` when the row is not over budget so the
    # template renders the "remaining" branch instead.
    over_budget_amount = (
        total - txn.estimated_amount if over_budget else None
    )
    return {
        "is_tracked": True,
        "entry_total": total,
        "entry_count": len(txn.entries),
        "entry_remaining": remaining,
        "entry_over_budget": over_budget,
        "entry_over_budget_amount": over_budget_amount,
    }


# ── Section 2: Alerts ──────────────────────────────────────────────


_DEFAULT_STALENESS_DAYS = 14
_DEFAULT_LOW_BALANCE_THRESHOLD = 500


@dataclass(frozen=True)
class _PeriodProjection:
    """The period-keyed balance projection the alert computation reads.

    A cohesive value object bundling the three projection inputs that are
    always produced together in ``compute_dashboard_data``: the
    period_id -> end-balance map, the current period, and the ordered
    period list.  Threaded into the alert helpers so they take a small,
    cohesive argument list rather than three loose positional parameters.
    """

    balance_results: dict[int, Decimal] | None
    current_period: PayPeriod | None
    all_periods: list[PayPeriod]


def _compute_alerts(
    account: Account,
    settings: UserSettings | None,
    projection: _PeriodProjection,
    current_balance: Decimal | None,
) -> list[dict]:
    """Compute actionable alerts for the dashboard.

    Alert types:
    1. Stale anchor -- checking balance not updated recently.
    2. Negative projected balance -- a future period goes negative.
    3. Low / negative current balance -- the as-of-today balance is
       strictly below the low-balance threshold (a balance exactly at the
       threshold raises no alert; a danger alert when the as-of-today
       balance is already negative, a warning otherwise).

    ``current_balance`` is the as-of-today checking balance the balance
    card now displays (fix B).  Reusing the same figure here keeps the
    low-balance alert from disagreeing with the headline (the card and
    the alert can no longer quote two different numbers), and lets an
    already-negative balance raise the danger alert it previously never
    reached (the audit's severity inversion).

    Each alert carries a structured ``link`` describing the destination
    in service terms -- a ``kind`` plus whatever context the route layer
    needs to build a URL -- because this service is Flask-free and cannot
    call ``url_for``.  ``dashboard.page`` is the only route that resolves
    these links today (no alert-rendering partial route exists); it maps
    ``link.kind`` to a concrete URL.

    Returns alerts sorted by severity (danger first, then warning).
    """
    candidates = [
        _stale_anchor_alert(account, settings),
        _negative_balance_alert(projection),
        _low_balance_alert(settings, current_balance),
    ]
    alerts = [alert for alert in candidates if alert is not None]

    # Sort: danger first, then warning.
    severity_order = {"danger": 0, "warning": 1}
    alerts.sort(key=lambda a: severity_order.get(a["severity"], 2))
    return alerts


def _stale_anchor_alert(
    account: Account,
    settings: UserSettings | None,
) -> dict | None:
    """Build the stale / never-set anchor alert, or None when the anchor is fresh.

    Both the never-set and the stale case link to the anchor-update flow
    for this account.  The anchor timestamp is UTC-normalized before
    truncating to a date so the day count matches balance_resolver.py:241
    and cannot shift by the server's local timezone.
    """
    anchor_link = {"kind": "anchor_update", "account_id": account.id}
    last_anchor = _get_last_anchor_date(account.id)
    if last_anchor is None:
        return {
            "type": "stale_anchor",
            "message": "Your checking balance has never been set.",
            "severity": "warning",
            "link": anchor_link,
        }

    staleness_days = (
        settings.anchor_staleness_days if settings
        else _DEFAULT_STALENESS_DAYS
    )
    last_anchor_date = last_anchor.astimezone(timezone.utc).date()
    days_since = (date.today() - last_anchor_date).days
    if days_since > staleness_days:
        return {
            "type": "stale_anchor",
            "message": (
                f"Your checking balance hasn't been updated in {days_since} days."
            ),
            "severity": "warning",
            "link": anchor_link,
        }
    return None


def _negative_balance_alert(
    projection: _PeriodProjection,
) -> dict | None:
    """Build the negative-projection alert for the first future period < 0.

    The link names the offending period by its offset from the current
    period so the route can deep-link the grid to it (/grid?offset=N).
    Returns None when no future period is projected negative.
    """
    balance_results = projection.balance_results
    current_period = projection.current_period
    if not (balance_results and current_period):
        return None

    for period in projection.all_periods:
        if period.start_date <= date.today():
            continue
        bal = balance_results.get(period.id)
        if bal is not None and bal < _ZERO:
            offset = period.period_index - current_period.period_index
            return {
                "type": "negative_balance",
                "message": (
                    f"Projected balance goes negative on "
                    f"{period.start_date.strftime('%b %d, %Y')}."
                ),
                "severity": "danger",
                "link": {"kind": "negative_projection", "offset": offset},
            }
    return None


def _low_balance_alert(
    settings: UserSettings | None,
    current_balance: Decimal | None,
) -> dict | None:
    """Build the low / negative current-balance alert, measured as of today.

    Uses the as-of-today balance the card displays (fix B ripple) so the
    card and the alert cannot quote two different numbers.  An already-
    negative balance is a danger, not merely a low-balance warning (the
    audit's severity inversion); a positive-but-low balance stays a
    warning.  Returns None when the balance is at or above the threshold.
    """
    low_threshold = (
        settings.low_balance_threshold if settings
        else _DEFAULT_LOW_BALANCE_THRESHOLD
    )
    if current_balance is None or current_balance >= Decimal(str(low_threshold)):
        return None

    if current_balance < _ZERO:
        severity = "danger"
        message = (
            f"Your checking balance is negative (${current_balance:,.2f})."
        )
    else:
        severity = "warning"
        message = (
            f"Your current balance "
            f"(${current_balance:,.2f}) is below ${low_threshold:,}."
        )
    return {
        "type": "low_balance",
        "message": message,
        "severity": severity,
        "link": {"kind": "low_balance"},
    }


# ── Section 3: Balance and Cash Runway ─────────────────────────────


def _get_balance_info(
    account: Account,
    scenario_id: int,
    current_period: PayPeriod | None,
) -> dict:
    """Get the as-of-today balance and compute cash runway.

    The headline ``current_balance`` is the projected checking balance
    as of today, from the canonical ``balance_resolver.balance_as_of_date``
    producer (fix B) -- the same producer the calendar uses -- so the
    figure agrees with the "as of <anchor date>" caption: both now
    describe today's position rather than the end-of-current-period
    projection the card used to show.  When there is no current period
    (no period contains today) the resolver cannot project to today, so
    the raw anchor balance is used.

    Cash runway uses settled expenses from the last 30 calendar days
    (by due_date, consistent with calendar service attribution),
    scoped to ``scenario_id``, divided by 30.  Runway =
    current_balance / daily_average_spending.

    Returns None for runway when there is zero spending (avoids
    infinity) and clamps negative balance to 0 runway days.
    """
    if current_period is not None:
        current_balance = balance_resolver.balance_as_of_date(
            account, scenario_id, date.today(),
        )
    else:
        current_balance = account.current_anchor_balance or _ZERO

    last_anchor_dt = _get_last_anchor_date(account.id)
    # UTC-normalize the anchor timestamp before truncating to a date so
    # the "as of" day matches balance_resolver.py:241 and cannot shift
    # by the server's local timezone.
    last_true_up_date = (
        last_anchor_dt.astimezone(timezone.utc).date()
        if last_anchor_dt is not None else None
    )

    runway = _compute_cash_runway(account.id, scenario_id, current_balance)

    return {
        "current_balance": current_balance,
        "cash_runway_days": runway,
        "account_id": account.id,
        "account_name": account.name,
        "last_true_up_date": last_true_up_date,
    }


def _compute_cash_runway(
    account_id: int,
    scenario_id: int,
    current_balance: Decimal,
) -> int | None:
    """Compute cash runway in days from recent spending rate.

    Queries settled expenses from the last 30 days by due_date,
    consistent with calendar service date attribution, and scoped to
    ``scenario_id`` so what-if scenarios never spill into the baseline
    spending average -- matching the sibling dashboard expense query
    (_get_upcoming_bills).  The window is a true 30 calendar days (today
    and the 29 days before it, inclusive) so dividing by 30 yields a
    genuine daily average.

    Per the locked transfer decision, settled transfer-out shadows ARE
    included in the runway outflow: runway measures checking depletion,
    and a sweep to savings genuinely drains checking.  The summation is
    delegated to ``_sum_runway_outflow``.

    Returns None if no spending (avoids infinity), 0 if balance
    is negative.
    """
    if current_balance <= _ZERO:
        return 0

    # Today and the preceding 29 days, inclusive, is exactly 30 days.
    window_start = date.today() - timedelta(days=_THIRTY_DAYS - 1)
    total_spending = _sum_runway_outflow(
        account_id, scenario_id, window_start, date.today(),
    )
    if total_spending == _ZERO:
        return None

    daily_avg = total_spending / Decimal(str(_THIRTY_DAYS))
    runway = current_balance / daily_avg
    return int(runway.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _sum_runway_outflow(
    account_id: int,
    scenario_id: int,
    window_start: date,
    window_end: date,
) -> Decimal:
    """Sum settled expense outflow over a due-date window, for cash runway.

    The single outflow producer behind the cash-runway daily average.
    Sums ``abs(effective_amount)`` over settled expense transactions on
    the account (in ``scenario_id``) whose ``due_date`` falls within
    ``[window_start, window_end]`` inclusive.

    Transfer decision (locked 2026-06-12): settled transfer-out shadows
    are INCLUDED here.  Runway measures how fast checking depletes, and a
    settled sweep from checking to savings genuinely drains checking, so
    it counts as outflow even though it is excluded from any "spending"
    figure.  This producer therefore applies no ``transfer_id`` filter.

    Returns ``_ZERO`` (a ``Decimal``) when the window contains no settled
    expenses, honoring the ``-> Decimal`` annotation rather than the bare
    ``int`` ``0`` an empty ``sum()`` would yield.
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

    txns = (
        db.session.query(Transaction)
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.is_deleted.is_(False),
            Transaction.status_id.in_(settled_status_ids()),
            Transaction.transaction_type_id == expense_type_id,
            Transaction.due_date >= window_start,
            Transaction.due_date <= window_end,
        )
        .all()
    )

    return sum((abs(txn.effective_amount) for txn in txns), _ZERO)


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

    # Resolve the period's OWN tax year (DH-#30): the "next paycheck" can
    # fall in next calendar year, which may have its own brackets/FICA.
    # Falls back to the current year when that year has no configs.
    tax_configs = load_tax_configs_for_year(
        user_id, profile, period.start_date.year,
    )
    # Pass the salary calibration override exactly as the recurrence
    # engine does (recurrence_engine.py:767-772) so this dashboard figure
    # matches the grid's stored paycheck for a calibrated profile (fix E /
    # audit Card 4); without it a calibrated user's dashboard disagreed
    # with the grid.
    calibration = getattr(profile, "calibration", None)
    breakdown = paycheck_calculator.calculate_paycheck(
        profile, period, all_periods, tax_configs,
        calibration=calibration,
    )
    return breakdown.earnings.net_pay


# ── Section 5: Savings Goals ──────────────────────────────────────


def _get_savings_goals(user_id: int) -> list[dict]:
    """Get active savings goal progress.

    Delegates to ``savings_dashboard_service.compute_goal_progress`` --
    the narrow producer built from the SAME internals /savings uses (the
    resolved target via ``resolve_goal_target`` and the entries-aware
    resolver balance) -- and reshapes its output into this card's
    template contract.  So both screens report the same numbers for the
    same goal (fix F / audit Card 5): income-relative goals (whose
    ``target_amount`` is NULL by design) resolve a real target instead of
    rendering ``$0.00 / 0%``, and the balance basis is the resolver
    balance rather than the raw stored ``current_anchor_balance``.

    No exception is caught here, for the same reasons documented on
    :func:`_get_debt_summary`: a computation error is a programming bug
    that must fail loud, not be masked as an empty goal list.
    """
    # Pylint: ``import-outside-toplevel`` -- Deferred: savings_dashboard_service
    # pulls the heaviest service import chain (+27 modules, measured); loaded only
    # when this path runs, not on every dashboard_service import.  Same rationale
    # as ``_get_debt_summary``.
    from app.services import savings_dashboard_service  # pylint: disable=import-outside-toplevel

    goal_data = savings_dashboard_service.compute_goal_progress(user_id)

    result: list[dict] = []
    for gd in goal_data:
        goal = gd["goal"]
        result.append({
            "name": goal.name,
            "current_balance": gd["current_balance"],
            "target_amount": gd["resolved_target"],
            "pct_complete": gd["progress_pct"],
            "account_name": goal.account.name,
            "account_id": goal.account_id,
        })

    return result


# ── Section 6: Debt Summary ───────────────────────────────────────


def _get_debt_summary(user_id: int) -> dict | None:
    """Get debt summary by calling the savings dashboard service.

    Routes through ``compute_debt_summary`` -- the narrow producer that
    shares the full savings-dashboard build's loaders, projection
    dispatch, and debt/DTI rule (so this card and the /savings page
    cannot disagree) while skipping the dashboard-only sections (goal
    progress, emergency-fund metrics, non-loan projections, grouping;
    deep-hunt #82).  Returns ``None`` when the user has no loan
    accounts with params.

    No exception is caught here.  The producer is the same code the
    savings route (``app/routes/savings.py``) runs without a guard, and
    every sibling dashboard section here (``_get_savings_goals``,
    ``_compute_cash_runway``) is likewise unguarded.  A ``ValueError`` /
    ``KeyError`` / ``AttributeError``
    from that computation is a programming bug, not the no-debt signal;
    swallowing it would silently blank the debt panel and hide real
    debt (CLAUDE.md rule 4).  Letting it propagate fails loud and
    identically on the dashboard and savings pages.
    """
    # Pylint: ``import-outside-toplevel`` -- Deferred: savings_dashboard_service
    # pulls the heaviest service import chain (+27 modules, measured); loaded only
    # when the debt-summary path runs, not on every dashboard_service import.
    from app.services import savings_dashboard_service  # pylint: disable=import-outside-toplevel

    return savings_dashboard_service.compute_debt_summary(user_id)


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
    }
