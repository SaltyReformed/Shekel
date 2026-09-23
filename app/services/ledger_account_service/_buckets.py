"""The OWNER-BUCKET chart rows: the Uncategorized fallbacks and Transfers in transit.

Ruling **R-BAL99** (plan step ``balance:X-bi-6-3``): a row that is the owner's
ONE bucket for its ``(class, kind)`` with no real account, budget category or
loan behind it is one family, keyed ``(user_id, class_id, kind_id)`` over rows
flagged ``is_owner_bucket`` (``uq_ledger_accounts_owner_bucket``).  Two kinds
are members today:

* **fallback** -- the per-(owner, class) ``Uncategorized Income`` /
  ``Uncategorized Expense`` bucket an ordinary settled transaction's counter
  leg books into when the row has no category (Build-Order Step 3; it was the
  whole family, under the flag's old name ``is_fallback``);
* **transit** -- the owner's ``Transfers in transit`` clearing account, Asset
  class, the counter leg of every settled transfer's TWO per-movement entries
  (ruling **R-BAL45**'s shape C): each side posts on its own bank day against
  it, so it nets to zero once both sides have cleared.

**Why one resolver.**  The two differ in nothing but the ``(kind, class)`` pair
and the label snapshotted for it; the get-or-create -- the lookup keyed
exactly as the partial unique is, the lost-race reuse through
:func:`._common.add_or_reuse`, the stamping of ``kind_id`` / ``class_id`` /
``is_owner_bucket`` -- is one body.  A future bucket kind is one more entry in
:data:`_OWNER_BUCKET_NAMES`, an enum member and a ref row, and no schema:
that is what R-BAL99 bought by keying the family on the kind.

The resolver NEVER creates or reuses an **orphan** (``is_owner_bucket`` False,
``category_id`` NULL): the lookup keys on the flag, never on ``category_id IS
NULL`` (see :class:`app.models.ledger_account.LedgerAccount`, "Why
``is_owner_bucket`` exists").

Flask-isolated and commit-free like the rest of the package: plain data in,
ORM objects out; the caller owns the transaction boundary.
"""

import logging

from app import ref_cache
from app.enums import LedgerAccountClassEnum, LedgerAccountKindEnum
from app.extensions import db
from app.models.ledger_account import LedgerAccount

from ._common import resolve_chart_row

logger = logging.getLogger(__name__)

# Canonical display label for each owner bucket, keyed by the ``(kind, class)``
# pair that IS the bucket's identity, snapshotted into ``name`` when the
# resolver first creates one.  Spelled out here rather than derived from the
# enums' display values so that renaming an enum value can never silently
# rewrite the label on already-posted rows.  The map is also the resolver's
# only guard against minting a bucket of a shape no ruling named: a pair
# outside it is refused, not created.
_OWNER_BUCKET_NAMES = {
    (LedgerAccountKindEnum.FALLBACK, LedgerAccountClassEnum.INCOME):
        "Uncategorized Income",
    (LedgerAccountKindEnum.FALLBACK, LedgerAccountClassEnum.EXPENSE):
        "Uncategorized Expense",
    (LedgerAccountKindEnum.TRANSIT, LedgerAccountClassEnum.ASSET):
        "Transfers in transit",
}


def fallback_ledger_classes() -> frozenset:
    """Return the accounting classes a ``fallback`` bucket may carry.

    Income and Expense -- derived from :data:`_OWNER_BUCKET_NAMES` so the
    category resolver's class guard and this module's label map can never
    drift (the guard lived on the name map itself while the map was the
    category module's).

    Returns:
        The :class:`~app.enums.LedgerAccountClassEnum` members, as a frozenset.
    """
    return frozenset(
        ledger_class
        for kind, ledger_class in _OWNER_BUCKET_NAMES
        if kind is LedgerAccountKindEnum.FALLBACK
    )


def _find_existing_owner_bucket(
    user_id: int, class_id: int, kind_id: int,
) -> LedgerAccount | None:
    """Return the owner's existing bucket for ``(class, kind)``, or None.

    The idempotency lookup for :func:`get_or_create_owner_bucket`, keyed to
    match ``uq_ledger_accounts_owner_bucket`` exactly: ``WHERE is_owner_bucket``
    over ``(user_id, class_id, kind_id)``.  It deliberately does NOT key on
    ``category_id IS NULL``: a deleted-category **orphan** is also
    ``category_id``-NULL but carries ``is_owner_bucket`` False, so that lookup
    would match an orphan and return it as the bucket, commingling unrelated
    postings.  Keying on the flag confines the match to the true bucket (the
    H1 design fix -- see :class:`app.models.ledger_account.LedgerAccount`).

    Args:
        user_id: The owning user's id.
        class_id: The ledger-account-class PK.
        kind_id: The ledger-account-kind PK.

    Returns:
        The matching :class:`~app.models.ledger_account.LedgerAccount`, or
        None when none exists yet.
    """
    return (
        db.session.query(LedgerAccount)
        .filter_by(
            user_id=user_id, class_id=class_id, kind_id=kind_id,
            is_owner_bucket=True,
        )
        .first()
    )


def get_or_create_owner_bucket(
    user_id: int,
    kind: LedgerAccountKindEnum,
    ledger_class: LedgerAccountClassEnum,
) -> LedgerAccount:
    """Ensure the owner's ``(class, kind)`` bucket exists and return it.

    Idempotent against ``uq_ledger_accounts_owner_bucket``: an existing row for
    the natural key is returned unchanged; a lost creation race returns the
    winner's row (:func:`._common.resolve_chart_row`).  The created row leaves
    ``account_id``, ``category_id`` and ``loan_account_id`` NULL, sets
    ``is_owner_bucket`` True, stamps the kind and class, and snapshots the
    canonical label from :data:`_OWNER_BUCKET_NAMES`.

    Flushes so the new row's ``id`` is assigned, but does NOT commit -- the
    caller (the posting writer) owns the transaction boundary.

    Args:
        user_id: The owning user's id.
        kind: The bucket kind, a :class:`~app.enums.LedgerAccountKindEnum`
            member named in :data:`_OWNER_BUCKET_NAMES`.
        ledger_class: The accounting class the bucket carries, a
            :class:`~app.enums.LedgerAccountClassEnum` member; together with
            *kind* it must be a pair the map names.

    Returns:
        The :class:`~app.models.ledger_account.LedgerAccount` for the
        ``(user, class, kind)`` key (existing, or newly created and flushed).

    Raises:
        ValueError: If ``(kind, ledger_class)`` is not a bucket the map names
            -- no database CHECK pins a bucket's class to its kind, so this
            guard is the sole defense against minting a malformed chart entry
            (an Asset-class fallback, a Liability-class transit).
    """
    name = _OWNER_BUCKET_NAMES.get((kind, ledger_class))
    if name is None:
        raise ValueError(
            f"no owner bucket is defined for kind {kind!r} in class "
            f"{ledger_class!r}; the defined buckets are "
            f"{sorted((k.value, c.value) for k, c in _OWNER_BUCKET_NAMES)}"
        )
    class_id = ref_cache.ledger_account_class_id(ledger_class)
    kind_id = ref_cache.ledger_account_kind_id(kind)

    ledger_account = resolve_chart_row(
        lambda: _find_existing_owner_bucket(user_id, class_id, kind_id),
        lambda: LedgerAccount(
            user_id=user_id,
            class_id=class_id,
            kind_id=kind_id,
            account_id=None,
            category_id=None,
            is_owner_bucket=True,
            name=name,
        ),
    )
    # "Resolved", not "Created": :func:`._common.resolve_chart_row` returns
    # the row a concurrent request won when this one lost the natural-key
    # race, and that row is not one this call created.  ``add_or_reuse`` logs
    # the reuse itself, so the two lines together read correctly either way.
    logger.info(
        "Resolved %s owner bucket id=%d (user_id=%d, class_id=%d, kind_id=%d)",
        name, ledger_account.id, user_id, class_id, kind_id,
    )
    return ledger_account


def get_or_create_transit_ledger_account(user_id: int) -> LedgerAccount:
    """Ensure the owner's Transfers-in-transit clearing account exists.

    The counter-leg resolver of the transfer-movement posting source (plan step
    ``balance:X-bi-6-3``, rulings **R-BAL45** and **R-BAL101**): the
    ``transit`` bucket in the Asset class, one per owner.  Stated as its own
    door so the posting writer names what it books against rather than a
    ``(kind, class)`` pair.

    Args:
        user_id: The owning user's id (the transfer's, read off the shadow --
            the one home ``pay_calendar:C13-b`` gave a row's owner).

    Returns:
        The owner's ``transit`` :class:`~app.models.ledger_account.LedgerAccount`.
    """
    return get_or_create_owner_bucket(
        user_id, LedgerAccountKindEnum.TRANSIT, LedgerAccountClassEnum.ASSET,
    )
