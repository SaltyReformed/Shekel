"""
Shekel Budget App -- Year-End Summary: transfers summary.

Section 4: transfers grouped by destination account for the year.
"""

from decimal import Decimal

from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models.transfer import Transfer
from app.utils.balance_predicates import (
    attribution_year,
    balance_excluded_status_ids,
)

ZERO = Decimal("0")


def _compute_transfers_summary(
    user_id: int,
    year: int,
    period_ids: list[int],
    scenario_id: int,
) -> list[dict]:
    """Group transfers by destination account for the year.

    Each transfer is attributed to a calendar year by the shared
    ``COALESCE(due_date, pay_period.start_date)`` rule
    (:func:`app.utils.balance_predicates.attribution_year`), the same
    rule the spending section applies to settled expenses -- so a
    boundary-period transfer (a December pay period carrying a January
    due_date) is counted in the same year as its sibling expense shadow
    rather than landing in a different year on the same report (#61).
    ``Transfer.due_date`` is the parent-canonical value the transfer
    service keeps mirrored onto both shadows, so this parent-side
    attribution equals the shadow-side rule the spending query uses.

    Args:
        user_id: User ID for ownership filtering.
        year: Target calendar year for attribution.
        period_ids: IDs of pay periods with start_date in the year.
        scenario_id: Baseline scenario ID.

    Returns:
        List of dicts sorted by total_amount descending:
        [{destination_account, destination_account_id, total_amount}]
    """
    if not period_ids:
        return []

    # Routed through the centralized ``balance_excluded_status_ids``
    # accessor (D6-09 / MED-02) so the Credit / Cancelled exclusion
    # set is defined exactly once across the codebase.
    excluded_ids = balance_excluded_status_ids()

    transfers = (
        db.session.query(Transfer)
        .options(
            joinedload(Transfer.to_account),
            joinedload(Transfer.pay_period),
        )
        .filter(
            Transfer.user_id == user_id,
            Transfer.scenario_id == scenario_id,
            Transfer.pay_period_id.in_(period_ids),
            Transfer.is_deleted.is_(False),
            ~Transfer.status_id.in_(excluded_ids),
        )
        .all()
    )

    by_dest: dict[int, dict] = {}
    for t in transfers:
        # Re-filter to the attribution year (the period_ids query above
        # selects periods whose start_date is in the year, but a
        # boundary period's transfer may carry a due_date in an adjacent
        # year) -- mirrors _compute_spending_by_category's Python re-check.
        if attribution_year(t.due_date, t.pay_period.start_date) != year:
            continue
        acct_id = t.to_account_id
        if acct_id not in by_dest:
            by_dest[acct_id] = {
                "destination_account": t.to_account.name,
                "destination_account_id": acct_id,
                "total_amount": ZERO,
            }
        by_dest[acct_id]["total_amount"] += t.amount

    result = list(by_dest.values())
    result.sort(key=lambda x: x["total_amount"], reverse=True)
    return result
