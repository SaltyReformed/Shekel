"""
Shekel Budget App -- Savings Dashboard: template display grouping.

Groups the per-account :class:`~.._types.AccountProjection` values into the
category-ordered structure the savings dashboard template renders.  No Flask
imports.
"""

from collections import OrderedDict
from decimal import Decimal

from app.enums import AcctCategoryEnum
from app.services.savings_dashboard_service._types import AccountProjection

ZERO = Decimal("0.00")

# The DISPLAY vocabulary: one key per modelled account category.  This module
# owns the mapping and the order because both are display decisions; what an
# account's category IS belongs to
# :func:`app.services.account_category.account_category`, the one classifier
# every cockpit and net-worth surface reads (plan step X-z, ruling R-CP,
# finding N-118).  It is NOT the only place in the application that compares a
# ``category_id`` against a cached id -- see that module for the write-path
# survivor finding N-122 owns.
_CATEGORY_KEYS = {
    AcctCategoryEnum.ASSET: "asset",
    AcctCategoryEnum.LIABILITY: "liability",
    AcctCategoryEnum.RETIREMENT: "retirement",
    AcctCategoryEnum.INVESTMENT: "investment",
}

# The fall-through key for an account whose category this application does not
# model -- the two states :func:`~app.services.account_category.account_category`
# returns ``None`` for, neither of which a healthy persisted row reaches.  It is
# a real band with real microcopy, a chart colour and a grid card, so such an
# account's balance is rendered somewhere rather than dropped.
_OTHER_KEY = "other"

# The liability band's key, and the ONE spelling of it in this application.
# ``_net_worth`` and ``_horizon`` import this rather than repeating the string
# (plan step X-z, ruling R-CP; it was ``_net_worth._LIABILITY_BAND``), so the
# band a chart stacks IS the key this module assigns -- which is what makes
# ``category_key(ad.category) == LIABILITY_KEY`` and
# :attr:`~.._types.AccountProjection.is_liability` one answer rather than two
# that agree.
LIABILITY_KEY = _CATEGORY_KEYS[AcctCategoryEnum.LIABILITY]

# The cockpit's category display ORDER -- the grid's card order, and the order
# the composition's bands are derived in.  Written out entry by entry (never
# derived from :class:`~app.enums.AcctCategoryEnum`'s declaration order) because
# the order is a display decision: reordering a reference enum must not silently
# reorder the cockpit's cards.  The KEYS come from :data:`_CATEGORY_KEYS`, so
# this module holds exactly one spelling of each.
#
# **It is THE band vocabulary, and the net-worth producer derives from it**
# (plan step X-t3, finding N-108): ``_net_worth._ASSET_BANDS`` is this tuple
# minus :data:`LIABILITY_KEY`.  The two are the same set by construction rather
# than by two lists agreeing, so a category added here cannot ship a chart band
# with no grid group behind it.  The ORDER differs from the chart's stack order
# on purpose, and only the SET is shared.  The presentation homes that cannot
# import it (the chart script, the cockpit template's microcopy and its two
# ``category_name == 'liability'`` tests, the CSS band tokens) are held to this
# vocabulary by ``test_net_worth_band_vocabulary.py``.
_CATEGORY_ORDER = (
    _CATEGORY_KEYS[AcctCategoryEnum.ASSET],
    LIABILITY_KEY,
    _CATEGORY_KEYS[AcctCategoryEnum.RETIREMENT],
    _CATEGORY_KEYS[AcctCategoryEnum.INVESTMENT],
    _OTHER_KEY,
)


def category_key(category: AcctCategoryEnum | None) -> str:
    """Return the cockpit band key for one account's resolved category.

    The display half of the classification: it NAMES a category, it does not
    decide one.  The deciding is
    :func:`app.services.account_category.account_category`, resolved once per
    account per render onto :attr:`~.._types.AccountProjection.category` (plan
    step X-z7, ruling R-CT).

    Both of this page's category questions read that one answer, so they cannot
    disagree: the asset-vs-liability sign through
    :attr:`~.._types.AccountProjection.is_liability` (the same member compared
    against one enum value), and the chart band / grid group through this
    lookup.  ``account_category_key(account)`` and the
    ``{account_id: category_key}`` map built from it are both DELETED -- the map
    was a second per-account container keyed by account id, which is ruling
    R-CG's own defect re-created one commit after the step that removed it, and
    both of plan step X-z's adversarial reviews said so.

    The equivalence finding N-118 exists for still holds and is now trivial:
    ``category_key(ad.category) == LIABILITY_KEY`` iff ``ad.is_liability``,
    because both read ONE member, the mapping is injective, and
    :data:`_OTHER_KEY` is not one of its values.

    Args:
        category: The account's :class:`~app.enums.AcctCategoryEnum` member, or
            ``None`` when this application models no category for it.

    Returns:
        The band key from :data:`_CATEGORY_KEYS`, or :data:`_OTHER_KEY`.
    """
    return _CATEGORY_KEYS.get(category, _OTHER_KEY)


def _group_accounts_by_category(
    account_data: list[AccountProjection],
) -> "OrderedDict[str, list[AccountProjection]]":
    """Group the per-account projections by account type category.

    Returns an OrderedDict keyed by band, preserving the display order
    :data:`_CATEGORY_ORDER` fixes (Asset, Liability, Retirement, Investment,
    Other) and keeping only the non-empty groups.

    **It reads each projection's own resolved category** (plan step X-z7,
    ruling R-CT).  It classified every account once per category label -- 5N
    calls, 40 for 8 accounts -- until plan step X-z2, which cut that by handing
    it a prebuilt map; that map was itself a second per-account container, so
    the answer now rides on the record this loop is already iterating.  No
    classifier call, no map lookup, and no argument a caller can get wrong
    (Section 8): there is nothing left to pass.

    Args:
        account_data: The per-account projections to group, each carrying its
            own :attr:`~.._types.AccountProjection.category`.

    Returns:
        ``OrderedDict[band_key, list[AccountProjection]]`` -- the non-empty
        groups in display order.
    """
    grouped: "OrderedDict[str, list[AccountProjection]]" = OrderedDict(
        (band, []) for band in _CATEGORY_ORDER
    )
    for ad in account_data:
        grouped[category_key(ad.category)].append(ad)
    return OrderedDict(
        (band, members) for band, members in grouped.items() if members
    )


def _compute_group_subtotals(grouped_accounts):
    """Sum each category group's current balance for its group header.

    The cockpit's grid shows a subtotal beside each category header; the
    figure is computed here, never in the template (money math stays in the
    service).  Returns an ``OrderedDict`` keyed exactly like
    *grouped_accounts* -- same category labels, same display order -- so the
    template reads ``group_subtotals[label]`` alongside its
    ``grouped_accounts.items()`` loop.

    Each subtotal is the ``Decimal`` sum of the group's per-account
    ``current_balance``, which is never ``None`` since plan step X-v2
    (ruling R-CA): the ``is not None`` test this loop used to carry was the
    EIGHTH reducer treating "the app cannot answer this balance" as ``$0.00``,
    and the field stopped being nullable when the one state that produced a
    ``None`` stopped rendering.

    Liability groups sum the loan resolver's positive owed balances, so a
    liability subtotal is the positive total owed; the template colors it
    with the danger token (color is a display decision keyed on the
    category, not encoded in the figure's sign).

    Args:
        grouped_accounts: The ``OrderedDict`` from
            :func:`_group_accounts_by_category` (category label ->
            list of per-account projections).

    Returns:
        ``OrderedDict[str, Decimal]`` mapping each category label to its
        balance subtotal, in the same order as *grouped_accounts*.
    """
    subtotals = OrderedDict()
    for cat_label, cat_accounts in grouped_accounts.items():
        subtotals[cat_label] = sum(
            (ad.current_balance for ad in cat_accounts), ZERO,
        )
    return subtotals
