"""
Shekel Budget App -- Savings Dashboard: template display grouping.

Groups the per-account projection dicts into the category-ordered
structure the savings dashboard template renders.  No Flask imports.
"""

from collections import OrderedDict

from app import ref_cache
from app.enums import AcctCategoryEnum


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
