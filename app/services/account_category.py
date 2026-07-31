"""
Shekel Budget App -- the account-type CATEGORY classifier.

The one place an account's ``account_type.category_id`` is compared against a
cached reference-table id.  Every question the app asks about an account's
category derives from :func:`account_category`'s single answer:

* :func:`is_liability_account` -- the asset-vs-liability rule the net-worth
  reduction, the liability band, the danger ink and the revolving-debt figure
  all classify through, reached by every cockpit surface as
  :attr:`~app.services.savings_dashboard_service._types.AccountProjection.is_liability`;
* :func:`~app.services.savings_dashboard_service._display.account_category_key`
  -- the five-key DISPLAY vocabulary (the grid's group cards, the net-worth
  composition bands, the Horizon's bands), which looks this answer up in a
  mapping that lives with the display order it belongs to.

**Those two were independent id comparisons until plan step X-z** (ruling
R-CP, finding N-118), equivalent by READING and by nothing else.  The Horizon
is where that cost the most: its three band producers must partition the
account set exactly once and they selected with BOTH spellings, so an account
the two classified differently would be counted twice with opposite signs --
net worth wrong by double its balance -- or not at all.  Now
``account_category_key(a) == LIABILITY_KEY`` iff ``is_liability_account(a)``,
given only that the display mapping is injective, which the band gate asserts.

**The module was ``net_worth_account_data`` until the same step** (ruling
R-CQ).  It was named for a ``to_net_worth_account_data`` adapter that paired
each account's dense period map with a STORED copy of the liability flag; plan
step X-w put the dense map on the projection that derives the flag, the adapter
had nothing left to adapt, and the name outlived it by three steps.

It is PUBLIC and lives at the service layer rather than inside the savings
cockpit package, although that package holds every consumer today: the
classification RULE is account metadata, not a display decision, so the next
consumer reaches a public module instead of importing a private one (the
package-boundary lesson the W9910 checker enforces).  The rule does NOT belong
in the ``balance_at`` seam either: it is account metadata, not a balance, and
the seam's contract is balances only.

Boundary discipline (``CLAUDE.md``: services are isolated from Flask): no
Flask import, no database writes.  Classification uses the cached
reference-table ids (IDs for logic, never a ``.name`` string).
"""

from app import ref_cache
from app.enums import AcctCategoryEnum


def account_category(account) -> AcctCategoryEnum | None:
    """Return the :class:`~app.enums.AcctCategoryEnum` for *account*'s type.

    The canonical account-category classifier, and the ONLY place in the
    application where an ``account_type.category_id`` is compared against a
    cached reference-table id (IDs for logic, never a ``.name`` string).  Every
    other category question derives from this answer, so no two of them can
    disagree about one account.

    Returns ``None`` -- "this account has no category the app models" -- in
    exactly two states, neither of which a healthy persisted row reaches:

    * ``account.account_type`` is ``None``.  ``budget.accounts.account_type_id``
      is ``NOT NULL`` and the relationship is ``lazy="joined"``, so a PERSISTED
      account always has a type; this is the transient / partially-built object
      (the shape a unit test stands in with).
    * the type's ``category_id`` names a ``ref.account_type_categories`` row
      that is not one of the four :class:`~app.enums.AcctCategoryEnum` members.
      ``ref.account_types.category_id`` is ``NOT NULL``, so this needs a FIFTH
      category row inserted outside the application: ``ref_cache.init`` requires
      the four named rows to exist and does not forbid others.  Both databases
      carry exactly four (verified 2026-07-30).

    Callers decide what an uncategorised account means for them: the display
    vocabulary buckets it as ``"other"`` and :func:`is_liability_account`
    answers ``False``.  That is the safe direction -- an unmodelled category
    counted as an asset is a balance in the wrong chart band, where counting it
    as a liability would SUBTRACT it from net worth.

    Args:
        account: The :class:`~app.models.account.Account` to classify, with its
            ``account_type`` relationship loaded (this issues no query).

    Returns:
        The account type's :class:`~app.enums.AcctCategoryEnum` member, or
        ``None`` when it has no category this application models.
    """
    acct_type = account.account_type
    if acct_type is None:
        return None
    category_id = acct_type.category_id
    for member in AcctCategoryEnum:
        if category_id == ref_cache.acct_category_id(member):
            return member
    return None


def is_liability_account(account) -> bool:
    """Return whether an account's type is in the LIABILITY category.

    The asset-vs-liability rule the net-worth sum depends on: assets add their
    balance, liabilities accumulate their POSITIVE magnitude, and net worth is
    the difference.  Every net-worth surface classifies through this one home --
    the cockpit's today figures, its trend, its Horizon band, the revolving-debt
    figure and the per-cell danger ink -- so an account can never count as an
    asset on one surface and a liability on another.

    DERIVED from :func:`account_category` since plan step X-z (ruling R-CP,
    finding N-118), where it was a second, independent comparison of the same
    column against the same cached id.  An account with no modelled category
    (see :func:`account_category` for the two states that produces) is not a
    liability.

    Args:
        account: The :class:`~app.models.account.Account` to classify.

    Returns:
        ``True`` when the account's type's category is LIABILITY,
        ``False`` otherwise.
    """
    return account_category(account) is AcctCategoryEnum.LIABILITY
