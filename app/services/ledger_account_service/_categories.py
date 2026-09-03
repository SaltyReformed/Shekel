"""The per-CATEGORY chart rows and the per-owner Uncategorized fallback.

Build-Order Step 3's half of the chart of accounts: the counter-leg side an
ordinary settled transaction's category leg books into.  One Income or Expense
ledger account per budget category per accounting class, plus the
per-(owner, class) ``Uncategorized`` bucket that catches a transaction whose
``category_id`` is NULL.

Unlike a linked row these carry a ``category_id`` (or, for the fallback,
``is_fallback=True``) and a NULL ``account_id``, and both snapshot a display
``name`` frozen at creation so renaming a budgeting category never rewrites
posted history.

Flask-isolated and commit-free: plain data in, ORM objects out; the caller owns
the transaction boundary.
"""

import logging

from app import ref_cache
from app.enums import LedgerAccountClassEnum, LedgerAccountKindEnum
from app.extensions import db
from app.models.category import Category
from app.models.ledger_account import LedgerAccount

from ._common import LEDGER_ACCOUNT_NAME_MAX_LEN, add_or_reuse

logger = logging.getLogger(__name__)

# Canonical display label for each per-(owner, class) Uncategorized fallback
# bucket, snapshotted into ``name`` when the resolver first creates one.
# Spelled out here rather than derived from the enum's display value so that
# renaming a ``LedgerAccountClassEnum`` value can never silently rewrite the
# label on already-posted fallback rows.
_FALLBACK_LEDGER_ACCOUNT_NAMES = {
    LedgerAccountClassEnum.INCOME: "Uncategorized Income",
    LedgerAccountClassEnum.EXPENSE: "Uncategorized Expense",
}

# The accounting classes a category / fallback ledger account may carry are
# exactly the keys above (Income or Expense): an ordinary transaction's
# counter-leg is always income or expense, while Asset/Liability belong to
# the linked real-account rows and Equity to the per-loan ``equity_opening``
# and per-account ``anchor_equity`` opening rows, none of which THIS resolver
# creates.  Derived from the name map so the two can never drift.  No
# database CHECK constrains a category row's ``class_id``, so this set is the
# resolver's -- and the app's -- only guard against minting a malformed chart
# entry.
_CATEGORY_LEDGER_CLASSES = frozenset(_FALLBACK_LEDGER_ACCOUNT_NAMES)


def _find_existing_category_ledger_account(
    user_id: int, class_id: int, category_id: int | None,
) -> LedgerAccount | None:
    """Return the existing category / fallback ledger account, or None.

    The idempotency lookup for
    :func:`get_or_create_category_ledger_account`, keyed to match exactly
    one partial unique index:

    * ``category_id`` is None -> the per-(owner, class) **fallback**, found
      ``WHERE is_fallback`` (the ``uq_ledger_accounts_uncategorized`` key).
      It deliberately does NOT key on ``category_id IS NULL``: a
      deleted-category **orphan** is also ``category_id``-NULL but carries
      ``is_fallback`` False, so a ``category_id IS NULL`` lookup would match
      an orphan and return it as the fallback, commingling unrelated
      postings.  Keying on ``is_fallback`` confines the match to the true
      fallback (the H1 design fix -- see
      :class:`app.models.ledger_account.LedgerAccount`).
    * ``category_id`` set -> the **category** row for ``(owner, category,
      class)``, found among ``account_id``-NULL rows (the
      ``uq_ledger_accounts_category`` key).

    Args:
        user_id: The owning user's id.
        class_id: The Income or Expense ledger-account-class PK.
        category_id: The budget category's id, or None for the fallback.

    Returns:
        The matching :class:`~app.models.ledger_account.LedgerAccount`, or
        None when none exists yet.
    """
    query = (
        db.session.query(LedgerAccount)
        .filter_by(user_id=user_id, class_id=class_id)
    )
    if category_id is None:
        return query.filter_by(is_fallback=True).first()
    return query.filter_by(category_id=category_id, account_id=None).first()


def _category_display_name(user_id: int, category_id: int) -> str:
    """Return a budget category's ``display_name`` to snapshot into ``name``.

    Loaded fresh (not navigated through a relationship) because the snapshot
    is taken once, at ledger-account creation, and must not track later
    renames of the budgeting category -- posted history stays stable.
    Truncated to :data:`._common.LEDGER_ACCOUNT_NAME_MAX_LEN` so a long
    "Group: Item" (up to ~202 chars) always fits the ``name`` column; the label
    is display-only, so the clip is lossless for logic, and the equivalent
    ``LEFT(group || ': ' || item, <len>)`` in the Step-7 backfill yields the
    identical string (the backfill==go-forward invariant).

    Filtered by the owning ``user_id``, not loaded by bare primary key: a
    ``Category`` is user-scoped data, so this honours the project rule that
    every query touching user data filters by ``user_id`` and matches the
    sibling :func:`_find_existing_category_ledger_account` lookup.  A
    ``category_id`` belonging to another user is therefore treated as "not
    found" rather than silently snapshotting a foreign label into this
    owner's ledger account.

    Args:
        user_id: The owning user's id (the category must belong to them).
        category_id: The budget category's id (non-NULL).

    Returns:
        str -- the category's ``"Group: Item"`` display label, clipped to the
        ``name`` column width.

    Raises:
        ValueError: If no category with that id is owned by ``user_id``.  A
            live transaction's ``category_id`` always references the owner's
            existing category (the FK SET-NULLs it on delete), so a miss
            signals a caller passing a stale, wrong, or foreign id -- fail
            loud with the offending values rather than raising an opaque
            ``AttributeError`` on ``None.display_name``.
    """
    category = (
        db.session.query(Category)
        .filter_by(id=category_id, user_id=user_id)
        .first()
    )
    if category is None:
        raise ValueError(
            f"cannot create a category ledger account: no budget category "
            f"with id={category_id} owned by user_id={user_id}"
        )
    return category.display_name[:LEDGER_ACCOUNT_NAME_MAX_LEN]


def get_or_create_category_ledger_account(
    user_id: int,
    category_id: int | None,
    ledger_class: LedgerAccountClassEnum,
) -> LedgerAccount:
    """Ensure the Income/Expense ledger account for a category exists.

    The Build-Order Step 3 counter-leg resolver: an ordinary settled
    transaction's category leg books into a per-category Income or Expense
    ledger account, and this lazily materialises (and thereafter reuses)
    that account.  A transaction with no category books into the
    per-(owner, class) ``Uncategorized`` fallback instead.

    Idempotent: an existing row for the natural key is returned unchanged
    (the matching partial unique index -- ``uq_ledger_accounts_category``
    for a category row, ``uq_ledger_accounts_uncategorized`` for a fallback
    -- would otherwise reject a duplicate).  A category used for both an
    income and an expense transaction correctly yields TWO rows, one per
    class, because the natural key includes ``class_id`` (a ``Category`` is
    type-agnostic).

    The created row leaves ``account_id`` NULL (it is a counter account, not
    a real-account mirror), snapshots its display ``name`` (the category's
    ``"Group: Item"`` or the canonical ``"Uncategorized {Income|Expense}"``),
    and sets ``is_fallback`` True for the fallback / False for a category
    row, stamping the matching ``kind_id`` (``fallback`` or ``category``).
    The resolver NEVER creates or reuses an **orphan** (``is_fallback``
    False, ``category_id`` NULL): orphans arise only from a category delete's
    SET NULL and are left untouched (see
    :func:`_find_existing_category_ledger_account` for why the fallback
    lookup keys on ``is_fallback``, not ``category_id IS NULL``).

    Flushes so the new row's ``id`` is assigned, but does NOT commit -- the
    caller (``posting_service``, Step 3 Commit 4) owns the transaction
    boundary.

    Args:
        user_id: The owning user's id.  Sourced by the caller from
            ``txn.pay_period.user_id``; ``txn.user_id`` is the same value and
            one hydration cheaper since plan step ``pay_calendar:C13-a``.  A
            read that STAMPS rather than refuses, so NOT one of finding
            **P75**'s nineteen -- that census excludes it by name.
        category_id: The budget category's id, or None to resolve the
            per-(owner, class) Uncategorized fallback.
        ledger_class: The accounting class, a
            :class:`~app.enums.LedgerAccountClassEnum` member that MUST be
            ``INCOME`` or ``EXPENSE`` (the caller derives it from the
            transaction type).

    Returns:
        The :class:`~app.models.ledger_account.LedgerAccount` for the
        (user, category, class) key (existing, or newly created and
        flushed).

    Raises:
        ValueError: If ``ledger_class`` is not Income or Expense (no database
            CHECK enforces this, so the guard is the sole defense against a
            malformed chart entry), or if a non-NULL ``category_id`` names
            no category owned by ``user_id``.
    """
    if ledger_class not in _CATEGORY_LEDGER_CLASSES:
        raise ValueError(
            f"category ledger account must be Income or Expense class, "
            f"got {ledger_class!r}"
        )
    class_id = ref_cache.ledger_account_class_id(ledger_class)

    existing = _find_existing_category_ledger_account(
        user_id, class_id, category_id,
    )
    if existing is not None:
        return existing

    is_fallback = category_id is None
    kind_member = (
        LedgerAccountKindEnum.FALLBACK if is_fallback
        else LedgerAccountKindEnum.CATEGORY
    )
    name = (
        _FALLBACK_LEDGER_ACCOUNT_NAMES[ledger_class] if is_fallback
        else _category_display_name(user_id, category_id)
    )
    ledger_account = add_or_reuse(
        LedgerAccount(
            user_id=user_id,
            class_id=class_id,
            kind_id=ref_cache.ledger_account_kind_id(kind_member),
            account_id=None,
            category_id=category_id,
            is_fallback=is_fallback,
            name=name,
        ),
        lambda: _find_existing_category_ledger_account(
            user_id, class_id, category_id,
        ),
    )
    # "Resolved", not "Created": :func:`._common.add_or_reuse` returns the row
    # a concurrent request won when this one lost the natural-key race, and
    # that row is not one this call created.  ``add_or_reuse`` logs the reuse
    # itself, so the two lines together read correctly either way.
    logger.info(
        "Resolved %s ledger account id=%d (user_id=%d, category_id=%s, "
        "class_id=%d, is_fallback=%s)",
        "Uncategorized fallback" if is_fallback else "category",
        ledger_account.id, user_id, category_id, class_id, is_fallback,
    )
    return ledger_account
