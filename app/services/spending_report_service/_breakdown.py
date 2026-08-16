"""Where It Went, and the By-change lens: one category total, read two ways.

Both the grouped breakdown and the flat change rows reduce the SAME
``{category_id: _CategoryTotal}`` pair -- the chosen window's and its prior
window's -- which is what makes a group's delta and its By-change row the same
number rather than two computations that agree.  The caller totals each window
once (:func:`_totals_by_category`) and hands both maps to both builders.

Boundary discipline: no Flask import, no query -- these reduce rows the window
module already loaded.  All money is ``Decimal``; the templates do no math.
"""

from collections import defaultdict
from decimal import Decimal

from app.models.transaction import Transaction
from app.services import spending_analysis
from app.services.row_valuation import owned_contribution
from app.utils.money import ZERO

from ._types import (
    ChangeRow,
    SpendingGroupRow,
    SpendingItemRow,
    _CategoryTotal,
)


def _totals_by_category(txns: list[Transaction]) -> dict[int, _CategoryTotal]:
    """Sum settled spend per category id, carrying the display labels.

    Category id ``0`` is the Uncategorized bucket (rows with no category),
    so an uncategorized row never collides with a real category.  Labels
    come from the first row seen for the id: a real category id maps to
    exactly one ``(group, item)`` pair, and the Uncategorized bucket's
    labels are fixed by :func:`spending_analysis.category_names`.

    Args:
        txns: One window's settled expenses -- every row owns its figure.

    Returns:
        ``category_id -> _CategoryTotal`` (labels + summed spend).
    """
    amounts: dict[int, Decimal] = defaultdict(lambda: ZERO)
    labels: dict[int, tuple[str, str]] = {}
    for txn in txns:
        cat_id = txn.category_id if txn.category_id is not None else 0
        amounts[cat_id] += abs(owned_contribution(txn))
        if cat_id not in labels:
            labels[cat_id] = spending_analysis.category_names(txn)
    return {
        cat_id: _CategoryTotal(
            group_name=labels[cat_id][0],
            item_name=labels[cat_id][1],
            amount=amount,
        )
        for cat_id, amount in amounts.items()
    }


def _build_breakdown(
    current_by_cat: dict[int, _CategoryTotal],
    prior_by_cat: dict[int, _CategoryTotal],
) -> list[SpendingGroupRow]:
    """Build the amount-descending 'Where It Went' group rows.

    Groups the chosen window's per-category totals by group name, computes
    every row's share of the window total, and attaches the signed
    window-over-window delta per item and per group (the D7 change basis).
    A group's prior side sums EVERY prior-window category in that group --
    including categories with no current spend -- so a stopped bill still
    moves its group's delta.

    Args:
        current_by_cat: The chosen window's per-category totals.
        prior_by_cat: The prior window's per-category totals.

    Returns:
        The group rows, amount-descending, each with amount-descending items.
    """
    total = sum((cat.amount for cat in current_by_cat.values()), ZERO)

    items_by_group: dict[str, list[SpendingItemRow]] = defaultdict(list)
    for cat_id, cat in current_by_cat.items():
        prior = prior_by_cat.get(cat_id)
        prior_amount = prior.amount if prior is not None else ZERO
        items_by_group[cat.group_name].append(SpendingItemRow(
            category_id=cat_id,
            item_name=cat.item_name,
            amount=cat.amount,
            share=_share(cat.amount, total),
            delta=cat.amount - prior_amount,
            is_new=prior_amount == ZERO,
        ))

    prior_group_totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for cat in prior_by_cat.values():
        prior_group_totals[cat.group_name] += cat.amount

    rows = [
        _group_row(
            group_name, items, total, prior_group_totals[group_name],
        )
        for group_name, items in items_by_group.items()
    ]
    rows.sort(key=lambda row: row.amount, reverse=True)
    return rows


def _group_row(
    group_name: str,
    items: list[SpendingItemRow],
    total: Decimal,
    prior_group_amount: Decimal,
) -> SpendingGroupRow:
    """Assemble one group row from its (to-be-sorted) item rows.

    Args:
        group_name: The category group label.
        items: The group's item rows (sorted in place, amount-descending).
        total: The window total (the group share denominator).
        prior_group_amount: The group's prior-window spend across ALL its
            categories (zero when the group had none).

    Returns:
        The :class:`SpendingGroupRow`.
    """
    items.sort(key=lambda row: row.amount, reverse=True)
    group_amount = sum((row.amount for row in items), ZERO)
    return SpendingGroupRow(
        group_name=group_name,
        amount=group_amount,
        share=_share(group_amount, total),
        delta=group_amount - prior_group_amount,
        is_new=prior_group_amount == ZERO,
        items=items,
    )


def _share(amount: Decimal, total: Decimal) -> Decimal:
    """Return ``amount / total`` as a full-precision fraction, or zero.

    Args:
        amount: The row's spend.
        total: The window's total spend (the share denominator).

    Returns:
        ``amount / total`` when ``total`` is positive, else ``Decimal("0")``
        (an empty window has no shares to compute).
    """
    if total <= ZERO:
        return ZERO
    return amount / total


def _build_changes(
    current_by_cat: dict[int, _CategoryTotal],
    prior_by_cat: dict[int, _CategoryTotal],
) -> list[ChangeRow]:
    """Build the By-change rows over the union of both windows' categories.

    Every category with settled spend in either window gets a row, so a
    category that stopped (prior spend, zero current -- the D7 zero-month
    rider) is as visible as one that grew.  Labels prefer the chosen
    window's rows (a rename shows its current name); a zero-current row
    falls back to the prior window's labels.

    Args:
        current_by_cat: The chosen window's per-category totals.
        prior_by_cat: The prior window's per-category totals.

    Returns:
        The :class:`ChangeRow` list sorted by ``abs(delta)`` descending,
        ties broken by current spend descending, then item name.
    """
    rows: list[ChangeRow] = []
    for cat_id in current_by_cat.keys() | prior_by_cat.keys():
        current = current_by_cat.get(cat_id)
        prior = prior_by_cat.get(cat_id)
        labels = current if current is not None else prior
        current_amount = current.amount if current is not None else ZERO
        prior_amount = prior.amount if prior is not None else ZERO
        rows.append(ChangeRow(
            category_id=cat_id,
            group_name=labels.group_name,
            item_name=labels.item_name,
            current=current_amount,
            prior=prior_amount,
            delta=current_amount - prior_amount,
            is_new=prior_amount == ZERO and current_amount > ZERO,
        ))
    rows.sort(key=lambda r: (-abs(r.delta), -r.current, r.item_name.lower()))
    return rows
