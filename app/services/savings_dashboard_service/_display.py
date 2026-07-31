"""
Shekel Budget App -- Savings Dashboard: template display grouping.

Groups the per-account :class:`~.._types.AccountProjection` values into the
category-ordered structure the savings dashboard template renders.  No Flask
imports.
"""

from collections import OrderedDict
from decimal import Decimal

from app.enums import AcctCategoryEnum
from app.services.account_category import account_category
from app.services.savings_dashboard_service._types import AccountProjection

ZERO = Decimal("0.00")

# The DISPLAY vocabulary: one key per modelled account category.  This module
# owns the mapping and the order because both are display decisions; what an
# account's category IS belongs to
# :func:`app.services.account_category.account_category`, which is the only
# place a ``category_id`` meets a cached reference id (plan step X-z, ruling
# R-CP, finding N-118).
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
# ``account_category_key(a) == LIABILITY_KEY`` and
# :func:`~app.services.account_category.is_liability_account` one answer rather
# than two that agree.
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


def account_category_key(account) -> str:
    """Return the cockpit category key for one account (id-based).

    The single per-account category classifier both the grid grouping
    (:func:`_group_accounts_by_category`) and the net-worth composition split
    (:func:`category_key_by_account_id`, consumed by
    :func:`~app.services.savings_dashboard_service._net_worth.compute_net_worth_series`)
    read, so a category band in the trend can never disagree with the group
    the same account sits in on the grid.

    **It is a LOOKUP of the shared classifier's answer, not a second
    comparison** (plan step X-z, ruling R-CP, finding N-118).  It re-derived the
    category from ``account_type.category_id`` itself, beside
    :func:`~app.services.account_category.is_liability_account` doing the same
    for its own question -- one rule written twice, equivalent by reading and by
    nothing else.  Both now read
    :func:`~app.services.account_category.account_category`, so
    ``account_category_key(a) == LIABILITY_KEY`` and ``is_liability_account(a)``
    are one answer: the display mapping is injective and :data:`_OTHER_KEY` is
    not one of its values, which the band gate asserts.

    Args:
        account: The :class:`~app.models.account.Account` to classify.

    Returns:
        The account's key from :data:`_CATEGORY_KEYS`, or :data:`_OTHER_KEY`
        when the app models no category for it (see
        :func:`~app.services.account_category.account_category` for the two
        states that produces).
    """
    return _CATEGORY_KEYS.get(account_category(account), _OTHER_KEY)


def category_key_by_account_id(
    account_data: list[AccountProjection],
) -> dict[int, str]:
    """Map each account's id to its cockpit category key.

    The composition-split adapter: the net-worth trend
    (:func:`~app.services.savings_dashboard_service._net_worth.compute_net_worth_series`)
    and the long-horizon producer both read each account's category band by id
    off this map, built from the SAME per-account classifier
    (:func:`account_category_key`) the grid grouping uses, so a band and the
    grid group cannot drift.

    Args:
        account_data: The per-account projections (each carrying an
            ``account``).

    Returns:
        ``{account_id: category_key}`` over every account in *account_data*.
    """
    return {
        ad.account.id: account_category_key(ad.account)
        for ad in account_data
    }


def _group_accounts_by_category(
    account_data: list[AccountProjection],
    category_by_account_id: dict[int, str],
) -> "OrderedDict[str, list[AccountProjection]]":
    """Group the per-account projections by account type category.

    Returns an OrderedDict keyed by category, preserving the display order
    :data:`_CATEGORY_ORDER` fixes (Asset, Liability, Retirement, Investment,
    Other) and keeping only the non-empty groups.

    **It TAKES the category map rather than re-deriving one** (plan step X-z2,
    ruling R-CR).  It classified every account once per category label, so one
    ``/savings`` render asked the classifier **48 times for 8 accounts**
    (measured, both databases) while :func:`category_key_by_account_id` had
    already built the same map for the net-worth trend one function away.  The
    render now builds it ONCE and hands the same object to both, which is what
    makes "the grid group and the chart band come from one classification"
    structural rather than a property of two callers using one classifier.

    The map is INDEXED, not defaulted -- ruling R-CJ's rule at a third reader.
    It is built from this same ``account_data``, so a missing key is a producer
    defect and raises here; answering it with :data:`_OTHER_KEY` would file a
    real account under the wrong card and its balance into the wrong subtotal,
    in silence.

    Args:
        account_data: The per-account projections to group.
        category_by_account_id: Each account's category key, from
            :func:`category_key_by_account_id` over this same *account_data*.

    Returns:
        ``OrderedDict[category_key, list[AccountProjection]]`` -- the non-empty
        groups in display order.

    Raises:
        KeyError: When *category_by_account_id* has no entry for an account in
            *account_data* -- a producer defect, never a display state.
    """
    grouped: "OrderedDict[str, list[AccountProjection]]" = OrderedDict(
        (category_key, []) for category_key in _CATEGORY_ORDER
    )
    for ad in account_data:
        grouped[category_by_account_id[ad.account.id]].append(ad)
    return OrderedDict(
        (category_key, members)
        for category_key, members in grouped.items() if members
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
