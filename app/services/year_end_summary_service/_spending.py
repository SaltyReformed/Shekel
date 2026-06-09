"""
Shekel Budget App -- Year-End Summary: spending and payment timeliness.

Section 3 (settled expenses grouped by category hierarchy, with
per-purchase entry breakdowns) and the OP-2 payment-timeliness metrics,
which share the settled-expense query and year-attribution helpers.
"""

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import and_, case, or_
from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.utils.balance_predicates import settled_status_ids
from app.utils.money import round_money

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


def _compute_spending_by_category(
    user_id: int,
    year: int,
    period_ids: list[int],
    scenario_id: int,
) -> list[dict]:
    """Group paid expense transactions by category hierarchy.

    Queries settled expense transactions in the year's pay periods,
    attributes each to the year using COALESCE(due_date,
    pay_period.start_date), and groups by category group_name then
    item_name.  Purchase-tracked items (a parent template with
    is_envelope=True, or an ad-hoc row carrying its own is_envelope
    flag) receive an entry_breakdown sub-dict with per-purchase
    aggregates queried from TransactionEntry (OP-3).

    Args:
        user_id: User ID for ownership filtering.
        year: Target calendar year for attribution.
        period_ids: IDs of pay periods with start_date in the year.
        scenario_id: Baseline scenario ID.

    Returns:
        List of dicts sorted by group_total descending:
        [{group_name, group_total, items: [{item_name, item_total,
        entry_breakdown?}]}].  entry_breakdown is present only for
        items with at least one tracked, settled, year-attributed
        parent transaction in the period.
    """
    if not period_ids:
        return []

    transactions = _query_settled_expenses(
        user_id, period_ids, scenario_id,
    )

    # Group by category using COALESCE(due_date, pp.start_date).
    groups: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: ZERO),
    )
    for txn in transactions:
        if _attribution_year(txn) != year:
            continue
        group_name, item_name = _txn_category_names(txn)
        groups[group_name][item_name] += abs(txn.effective_amount)

    spending = _build_spending_hierarchy(groups)

    # OP-3: attach per-entry breakdowns for tracked categories.
    breakdowns = _compute_entry_breakdowns(
        user_id, year, period_ids, scenario_id,
    )
    if breakdowns:
        for group in spending:
            for item in group["items"]:
                key = (group["group_name"], item["item_name"])
                if key in breakdowns:
                    item["entry_breakdown"] = breakdowns[key]

    return spending


def _compute_entry_breakdowns(
    user_id: int,
    year: int,
    period_ids: list[int],
    scenario_id: int,
) -> dict[tuple[str, str], dict]:
    """Aggregate transaction entries for tracked categories in the year.

    Runs one SQL query that joins TransactionEntry through Transaction
    to its template (outer-joined), account, pay period, and category.
    Filters with the same predicates as _query_settled_expenses (settled
    expenses in the user's year period_ids on the baseline scenario) plus
    purchase tracking enabled -- is_envelope=True on the parent template,
    or on the transaction itself for an ad-hoc row.  Aggregates per parent
    transaction so the same _attribution_year filter used by
    _compute_spending_by_category can be applied in Python -- this
    handles transactions whose due_date crosses a calendar year
    boundary identically to the existing category aggregation.

    Args:
        user_id: User ID for ownership filtering.
        year: Target calendar year for attribution.
        period_ids: IDs of pay periods with start_date in the year.
        scenario_id: Baseline scenario ID.

    Returns:
        dict mapping (group_name, item_name) tuple to a breakdown dict
        with entry_count, entry_total, credit_total, debit_total,
        avg_entry, and transaction_count_with_entries.  Categories
        with no tracked entries in the attribution year are absent.
    """
    if not period_ids:
        return {}

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

    rows = (
        db.session.query(
            Category.group_name.label("group_name"),
            Category.item_name.label("item_name"),
            Transaction.id.label("transaction_id"),
            Transaction.due_date.label("due_date"),
            PayPeriod.start_date.label("pp_start_date"),
            db.func.count(TransactionEntry.id).label("entry_count"),
            db.func.sum(TransactionEntry.amount).label("entry_total"),
            db.func.sum(
                case(
                    (
                        TransactionEntry.is_credit.is_(True),
                        TransactionEntry.amount,
                    ),
                    else_=Decimal("0"),
                )
            ).label("credit_total"),
        )
        .join(
            Transaction,
            TransactionEntry.transaction_id == Transaction.id,
        )
        # OUTER join so ad-hoc (template_id IS NULL) rows survive; the
        # envelope predicate below accepts either a template with
        # is_envelope set or an ad-hoc row carrying its own is_envelope
        # flag, mirroring Transaction.tracks_purchases at the SQL tier.
        .outerjoin(
            TransactionTemplate,
            Transaction.template_id == TransactionTemplate.id,
        )
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .filter(
            Account.user_id == user_id,
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
            Transaction.transaction_type_id == expense_type_id,
            Transaction.status_id.in_(settled_status_ids()),
            or_(
                TransactionTemplate.is_envelope.is_(True),
                and_(
                    Transaction.template_id.is_(None),
                    Transaction.is_envelope.is_(True),
                ),
            ),
        )
        .group_by(
            Category.group_name,
            Category.item_name,
            Transaction.id,
            Transaction.due_date,
            PayPeriod.start_date,
        )
        .all()
    )

    breakdowns: dict[tuple[str, str], dict] = {}
    for row in rows:
        # Match _attribution_year(): COALESCE(due_date, pp.start_date).
        attr_year = (
            row.due_date.year if row.due_date is not None
            else row.pp_start_date.year
        )
        if attr_year != year:
            continue
        _accumulate_entry_row(breakdowns, row)

    for bd in breakdowns.values():
        _finalize_entry_breakdown(bd)

    return breakdowns


def _accumulate_entry_row(
    breakdowns: dict[tuple[str, str], dict], row,
) -> None:
    """Add one transaction's entry stats to the running breakdown.

    Looks up (or creates) the breakdown dict keyed by the parent
    transaction's category and increments the running totals.  Missing
    categories are mapped to "Uncategorized" so the key matches what
    _txn_category_names() produces in the existing aggregation.

    Args:
        breakdowns: Mutable mapping from (group_name, item_name) to
            running aggregate dict.  Mutated in place.
        row: One result row from the entry aggregation query.
    """
    key = (
        row.group_name or "Uncategorized",
        row.item_name or "Uncategorized",
    )
    bd = breakdowns.get(key)
    if bd is None:
        bd = {
            "entry_count": 0,
            "entry_total": ZERO,
            "credit_total": ZERO,
            "transaction_count_with_entries": 0,
        }
        breakdowns[key] = bd
    bd["entry_count"] += int(row.entry_count or 0)
    bd["entry_total"] += row.entry_total or ZERO
    bd["credit_total"] += row.credit_total or ZERO
    bd["transaction_count_with_entries"] += 1


def _finalize_entry_breakdown(bd: dict) -> None:
    """Compute derived debit_total and avg_entry on a breakdown dict.

    Mutates the dict in place.  Called after all entry rows have been
    accumulated.  avg_entry is a monetary average (dollars per entry)
    so it is rounded through :func:`app.utils.money.round_money`,
    the project's centralized ROUND_HALF_UP cent boundary (E-26 /
    HIGH-04).  ``debit_total`` is pre-rounded subtraction of two
    already-rounded sums, so no further rounding is needed.

    Args:
        bd: Running aggregate dict produced by _accumulate_entry_row.
    """
    entry_total = bd["entry_total"]
    bd["debit_total"] = entry_total - bd["credit_total"]
    count = bd["entry_count"]
    bd["avg_entry"] = (
        round_money(entry_total / Decimal(count))
        if count > 0 else ZERO
    )


def _compute_payment_timeliness(
    user_id: int,
    year: int,
    period_ids: list[int],
    scenario_id: int,
) -> dict | None:
    """Compute bill payment timeliness metrics for the year.

    Examines settled expense transactions that have both paid_at
    and due_date populated.  Counts how many were paid on time
    vs. late, and computes the average days paid before the due date.

    Args:
        user_id: User ID for ownership filtering.
        year: Target calendar year for attribution.
        period_ids: IDs of pay periods in the target year.
        scenario_id: Baseline scenario ID.

    Returns:
        dict with total_bills_paid, paid_on_time, paid_late,
        avg_days_before_due.  Returns None if no applicable
        transactions exist.
    """
    if not period_ids:
        return None

    transactions = _query_settled_expenses(
        user_id, period_ids, scenario_id,
    )

    # Filter to transactions with both paid_at and due_date,
    # attributed to the target year.
    applicable = [
        txn for txn in transactions
        if txn.paid_at is not None
        and txn.due_date is not None
        and _attribution_year(txn) == year
    ]

    if not applicable:
        return None

    paid_on_time = 0
    paid_late = 0
    total_days = 0

    for txn in applicable:
        days_before = (txn.due_date - txn.paid_at.date()).days
        total_days += days_before
        if txn.paid_at.date() <= txn.due_date:
            paid_on_time += 1
        else:
            paid_late += 1

    avg_days = (
        Decimal(str(total_days)) / Decimal(str(len(applicable)))
    ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    return {
        "total_bills_paid": len(applicable),
        "paid_on_time": paid_on_time,
        "paid_late": paid_late,
        "avg_days_before_due": avg_days,
    }


def _query_settled_expenses(
    user_id: int, period_ids: list[int], scenario_id: int,
) -> list:
    """Load settled expense transactions for the given periods.

    Args:
        user_id: User ID for ownership filtering.
        period_ids: Pay period IDs to query.
        scenario_id: Baseline scenario ID.

    Returns:
        List of Transaction objects with eager-loaded category
        and pay_period.
    """
    return (
        db.session.query(Transaction)
        .join(Transaction.pay_period)
        .join(Transaction.account)
        .options(
            joinedload(Transaction.category),
            joinedload(Transaction.pay_period),
        )
        .filter(
            Account.user_id == user_id,
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
            Transaction.transaction_type_id == ref_cache.txn_type_id(
                TxnTypeEnum.EXPENSE,
            ),
            Transaction.status_id.in_(settled_status_ids()),
        )
        .all()
    )


def _attribution_year(txn: Transaction) -> int:
    """Return the calendar year a transaction is attributed to.

    Uses COALESCE(due_date, pay_period.start_date), consistent with
    calendar and variance services.
    """
    attr_date = (
        txn.due_date if txn.due_date is not None
        else txn.pay_period.start_date
    )
    return attr_date.year


def _txn_category_names(txn: Transaction) -> tuple[str, str]:
    """Return (group_name, item_name) for a transaction's category."""
    if txn.category is None:
        return ("Uncategorized", "Uncategorized")
    return (txn.category.group_name, txn.category.item_name)


def _build_spending_hierarchy(
    groups: dict[str, dict[str, Decimal]],
) -> list[dict]:
    """Convert grouped spending data to a sorted hierarchical list.

    Args:
        groups: group_name -> {item_name -> total} mapping.

    Returns:
        List of dicts sorted by group_total descending.
    """
    result = []
    for group_name, items in groups.items():
        item_list = [
            {"item_name": k, "item_total": v}
            for k, v in items.items()
        ]
        item_list.sort(key=lambda x: x["item_total"], reverse=True)
        group_total = sum(
            (i["item_total"] for i in item_list), ZERO,
        )
        result.append({
            "group_name": group_name,
            "group_total": group_total,
            "items": item_list,
        })
    result.sort(key=lambda x: x["group_total"], reverse=True)
    return result
