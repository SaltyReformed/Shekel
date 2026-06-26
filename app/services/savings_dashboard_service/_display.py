"""
Shekel Budget App -- Savings Dashboard: template display grouping.

Groups the per-account projection dicts into the category-ordered
structure the savings dashboard template renders.  No Flask imports.
"""

from collections import OrderedDict
from decimal import Decimal

from app import ref_cache
from app.enums import AcctCategoryEnum

ZERO = Decimal("0.00")


def _group_accounts_by_category(account_data):
    """Group account data dicts by account type category.

    Returns an OrderedDict with category labels as keys, preserving
    the display order: Asset, Liability, Retirement, Investment, Other.
    """
    category_order = [
        ("asset", ref_cache.acct_category_id(AcctCategoryEnum.ASSET)),
        ("liability", ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)),
        ("retirement", ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT)),
        ("investment", ref_cache.acct_category_id(AcctCategoryEnum.INVESTMENT)),
    ]
    grouped = OrderedDict()
    for cat_label, cat_id in category_order:
        cat_accounts = [
            ad for ad in account_data
            if ad["account"].account_type
            and ad["account"].account_type.category_id == cat_id
        ]
        if cat_accounts:
            grouped[cat_label] = cat_accounts

    uncategorized = [
        ad for ad in account_data
        if not ad["account"].account_type
        or not ad["account"].account_type.category_id
    ]
    if uncategorized:
        grouped["other"] = uncategorized

    return grouped


def _compute_group_subtotals(grouped_accounts):
    """Sum each category group's current balance for its group header.

    The cockpit's grid shows a subtotal beside each category header; the
    figure is computed here, never in the template (money math stays in the
    service).  Returns an ``OrderedDict`` keyed exactly like
    *grouped_accounts* -- same category labels, same display order -- so the
    template reads ``group_subtotals[label]`` alongside its
    ``grouped_accounts.items()`` loop.

    Each subtotal is the ``Decimal`` sum of the group's per-account
    ``current_balance``.  A ``None`` balance (an account with no resolvable
    current-period figure) contributes ``0.00`` rather than being skipped,
    matching how the cards render it as a zero rather than dropping the row.
    Liability groups sum the loan resolver's positive owed balances, so a
    liability subtotal is the positive total owed; the template colors it
    with the danger token (color is a display decision keyed on the
    category, not encoded in the figure's sign).

    Args:
        grouped_accounts: The ``OrderedDict`` from
            :func:`_group_accounts_by_category` (category label ->
            list of per-account projection dicts).

    Returns:
        ``OrderedDict[str, Decimal]`` mapping each category label to its
        balance subtotal, in the same order as *grouped_accounts*.
    """
    subtotals = OrderedDict()
    for cat_label, cat_accounts in grouped_accounts.items():
        total = ZERO
        for ad in cat_accounts:
            balance = ad["current_balance"]
            if balance is not None:
                total += balance
        subtotals[cat_label] = total
    return subtotals
