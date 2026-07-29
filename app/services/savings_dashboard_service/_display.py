"""
Shekel Budget App -- Savings Dashboard: template display grouping.

Groups the per-account :class:`~.._types.AccountProjection` values into the
category-ordered structure the savings dashboard template renders.  No Flask
imports.
"""

from collections import OrderedDict
from decimal import Decimal

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.services.savings_dashboard_service._types import AccountProjection

ZERO = Decimal("0.00")

# The cockpit's category display order.  ``other`` is the catch-all for an
# account with no ``account_type`` or no ``category_id`` (degenerate /
# partially loaded); the four real categories
# (:class:`~app.enums.AcctCategoryEnum`) exhaust every account that carries a
# ``category_id``, so this order also enumerates every band the net-worth
# composition can produce.
#
# **It is THE band vocabulary, and the net-worth producer now derives from it**
# (plan step X-t3, finding N-108): ``_net_worth._ASSET_BANDS`` is this tuple
# minus the liability key.  The two are the same set by construction rather
# than by two lists agreeing, so a category added here cannot ship a chart band
# with no grid group behind it.  The ORDER differs on purpose -- this one is the
# grid's card order, the composition's is the chart's stack order -- and only
# the SET is shared.  The presentation homes that cannot import it (the chart
# script, the cockpit template's microcopy, the CSS band tokens) are held to
# this set by ``test_net_worth_band_vocabulary.py``.
_CATEGORY_ORDER = ("asset", "liability", "retirement", "investment", "other")

# The four real categories paired with their display keys, in the order the
# key resolver checks them.  ``other`` is not here: it is the fall-through for
# an account with no category id.
_REAL_CATEGORIES = (
    ("asset", AcctCategoryEnum.ASSET),
    ("liability", AcctCategoryEnum.LIABILITY),
    ("retirement", AcctCategoryEnum.RETIREMENT),
    ("investment", AcctCategoryEnum.INVESTMENT),
)


def account_category_key(account) -> str:
    """Return the cockpit category key for one account (id-based).

    The single per-account category classifier both the grid grouping
    (:func:`_group_accounts_by_category`) and the net-worth composition split
    (:func:`category_key_by_account_id`, consumed by
    :func:`~app.services.savings_dashboard_service._net_worth.compute_net_worth_series`)
    read, so a category band in the trend can never disagree with the group
    the same account sits in on the grid.  Classifies by the account type's
    integer ``category_id`` against the cached category ids (IDs for logic,
    never a ``.name`` string).  An account with no ``account_type`` or no
    ``category_id`` -- degenerate / partially loaded -- classifies as
    ``"other"``; because :class:`~app.enums.AcctCategoryEnum` has exactly the
    four real categories, every account that DOES carry a ``category_id``
    matches one of them, so ``"other"`` is reached only for the degenerate
    case.

    Args:
        account: The :class:`~app.models.account.Account` to classify.

    Returns:
        One of ``"asset"``, ``"liability"``, ``"retirement"``,
        ``"investment"``, or ``"other"``.
    """
    acct_type = account.account_type
    if acct_type is None or acct_type.category_id is None:
        return "other"
    for key, enum in _REAL_CATEGORIES:
        if acct_type.category_id == ref_cache.acct_category_id(enum):
            return key
    return "other"


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
) -> "OrderedDict[str, list[AccountProjection]]":
    """Group the per-account projections by account type category.

    Returns an OrderedDict with category labels as keys, preserving
    the display order: Asset, Liability, Retirement, Investment, Other.
    Buckets each account through the shared :func:`account_category_key`
    classifier (so the grid groups and the net-worth composition bands read
    one taxonomy), keeping only the non-empty groups in display order.
    """
    grouped = OrderedDict()
    for cat_label in _CATEGORY_ORDER:
        cat_accounts = [
            ad for ad in account_data
            if account_category_key(ad.account) == cat_label
        ]
        if cat_accounts:
            grouped[cat_label] = cat_accounts
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
