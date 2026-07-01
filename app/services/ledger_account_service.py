"""
Shekel Budget App -- Ledger Account Service

Sole writer of ``budget.ledger_accounts`` (the chart of accounts for the
double-entry posting ledger, Build-Order Step 2).  Pairs every real
``budget.accounts`` row with exactly one Asset or Liability ledger account
so that later steps' postings have an account to land in.

Build-Order Step 3 adds the counter-leg side:
:func:`get_or_create_category_ledger_account` lazily materialises the
per-category Income/Expense ledger accounts an ordinary settled
transaction's category leg books into, plus the per-(owner, class)
``Uncategorized`` fallback for a transaction whose ``category_id`` is NULL.
Like the linked-account pairing it is idempotent (it respects the partial
unique indexes) and snapshots a display ``name``; unlike it, the row carries
a ``category_id`` (or, for the fallback, ``is_fallback=True``) and a NULL
``account_id``.  This service stays the sole writer of every
``ledger_accounts`` row kind.

Build-Order Step 4 adds the per-loan side:
:func:`get_or_create_loan_ledger_account` lazily materialises the three
per-loan accounts a confirmed loan payment's real-split correction books
into -- the loan's ``loan_interest`` and ``loan_escrow`` Expense accounts and
its ``loan_refund`` Asset account -- keyed (and made idempotent) by the
``uq_ledger_accounts_loan`` partial unique on ``(user, loan, kind)``.

As the sole writer, it stamps every row's explicit ``kind_id`` discriminator
(``LedgerAccountKindEnum`` -> ``ref.ledger_account_kinds`` id):
``create_ledger_account_for_account`` writes ``linked``,
``get_or_create_category_ledger_account`` writes ``fallback`` (the
Uncategorized bucket) or ``category``, and
``get_or_create_loan_ledger_account`` writes one of the three per-loan kinds
(``loan_interest`` / ``loan_escrow`` / ``loan_refund``).  ``kind_id`` is the
authoritative discriminator readers branch on; no database CHECK pins it to
the row shape (see :class:`app.models.ledger_account.LedgerAccount`), so
stamping it correctly here -- exactly as this service already stamps
``class_id`` -- is the app's guarantee that the kind and the column shape
agree.  For the per-loan rows that guarantee is load-bearing, not
belt-and-suspenders: the shipped ``ck_ledger_accounts_loan_shape`` CHECK is
columns-only (a CHECK cannot subquery ``ref.ledger_account_kinds``), so the
loan resolver -- which rejects any non-loan kind and any non-amortizing loan
account before it writes -- is the only thing keeping a ``loan_account_id``
row's kind a loan kind and its target a real loan.

This service is Flask-isolated per the project architecture rule
(``CLAUDE.md`` Architecture section): it takes plain data, returns a plain
SQLAlchemy object, never imports ``request``/``session``.  The caller owns
the surrounding transaction (no commit inside this module).

The go-forward pairing entry point is :func:`create_ledger_account_for_account`,
called from ``account_service.create_account`` immediately after the
account is flushed.  Historical accounts (those created before Step 2) are
paired once by the Commit-2 backfill migration, which reproduces the same
mapping in raw SQL.  Both producers leave a linked row's ``name`` NULL --
its display label derives from the live ``account.name`` (see
:class:`app.models.ledger_account.LedgerAccount`).
"""

import logging

from app import ref_cache
from app.enums import (
    AcctCategoryEnum,
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
)
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.ledger_account import LedgerAccount
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)


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
# the linked real-account rows and Equity to a future opening-balance row,
# none of which this resolver creates.  Derived from the name map so the two
# can never drift.  No database CHECK constrains a category row's ``class_id``,
# so this set is the resolver's -- and the app's -- only guard against minting
# a malformed chart entry.
_CATEGORY_LEDGER_CLASSES = frozenset(_FALLBACK_LEDGER_ACCOUNT_NAMES)

# The maximum length of a snapshotted display ``name``, read straight from the
# column so it can never drift from the schema.  A category's ``display_name``
# ("Group: Item") concatenates two ``String(100)`` halves and so can reach
# ~202 characters -- wider than this ``String(100)`` column, which PostgreSQL
# rejects (it does not silently truncate) on insert.  Because ``name`` is
# display-only (the natural key is the (user, category, class) IDs, never the
# label), truncating the snapshot to fit is lossless for logic; this mirrors
# the ``description = txn.name[:200]`` snapshot pattern the posting ledger
# already uses.
_LEDGER_ACCOUNT_NAME_MAX_LEN = LedgerAccount.__table__.columns["name"].type.length


def _ledger_class_id_for_account(account: Account) -> int:
    """Return the ledger-account-class ID a real account maps to.

    Liability-category accounts (credit cards, loans) map to the
    **Liability** ledger class; every other category (Asset, Retirement,
    Investment) maps to the **Asset** ledger class -- a retirement or
    brokerage balance is an asset on the books, only borrowed money is a
    liability.

    The branch compares the account-type category INTEGER ID against the
    cached Liability category ID; it never reads the category's string
    ``name`` (the project-wide IDs-for-logic invariant).  The Step-2
    backfill migration reproduces this exact mapping in raw SQL, so the
    go-forward and historical ledger accounts agree.

    Args:
        account: The real :class:`~app.models.account.Account` being
            paired.  Its ``account_type`` relationship (eager-loaded)
            supplies the ``category_id``.

    Returns:
        int -- the ``ref.ledger_account_classes.id`` of the Asset or
        Liability class.
    """
    liability_category_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
    if account.account_type.category_id == liability_category_id:
        class_member = LedgerAccountClassEnum.LIABILITY
    else:
        class_member = LedgerAccountClassEnum.ASSET
    return ref_cache.ledger_account_class_id(class_member)


def create_ledger_account_for_account(account: Account) -> LedgerAccount:
    """Ensure a real account has its paired Asset/Liability ledger account.

    Idempotent: when a linked ledger account already exists for this
    account it is returned unchanged (the partial unique index
    ``uq_ledger_accounts_account`` permits only one per ``account_id``, so
    a second insert would raise); otherwise a new linked row is created
    with the derived class, the ``linked`` kind, ``name`` left NULL (the
    display label derives from ``account.name``), and the owning ``user_id``
    copied from the account.

    Flushes so the new row's ``id`` is assigned, but does NOT commit --
    the caller (``account_service.create_account``) owns the transaction
    boundary.

    Args:
        account: The real :class:`~app.models.account.Account` to pair.
            Must already be flushed (``account.id`` non-NULL) so the FK
            and the idempotency lookup resolve.

    Returns:
        The linked :class:`~app.models.ledger_account.LedgerAccount`
        (existing or newly created and flushed).
    """
    existing = (
        db.session.query(LedgerAccount)
        .filter_by(account_id=account.id)
        .first()
    )
    if existing is not None:
        return existing

    ledger_account = LedgerAccount(
        user_id=account.user_id,
        class_id=_ledger_class_id_for_account(account),
        kind_id=ref_cache.ledger_account_kind_id(LedgerAccountKindEnum.LINKED),
        account_id=account.id,
        name=None,
    )
    db.session.add(ledger_account)
    db.session.flush()
    logger.info(
        "Paired account %s (id=%d, user_id=%d) with ledger account id=%d "
        "(class_id=%d)",
        account.name, account.id, account.user_id,
        ledger_account.id, ledger_account.class_id,
    )
    return ledger_account


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
    Truncated to ``_LEDGER_ACCOUNT_NAME_MAX_LEN`` so a long "Group: Item"
    (up to ~202 chars) always fits the ``name`` column; the label is
    display-only, so the clip is lossless for logic, and the equivalent
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
    return category.display_name[:_LEDGER_ACCOUNT_NAME_MAX_LEN]


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
            ``txn.pay_period.user_id`` (a ``Transaction`` has no
            ``user_id``).
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
    ledger_account = LedgerAccount(
        user_id=user_id,
        class_id=class_id,
        kind_id=ref_cache.ledger_account_kind_id(kind_member),
        account_id=None,
        category_id=category_id,
        is_fallback=is_fallback,
        name=name,
    )
    db.session.add(ledger_account)
    db.session.flush()
    logger.info(
        "Created %s ledger account id=%d (user_id=%d, category_id=%s, "
        "class_id=%d, is_fallback=%s)",
        "Uncategorized fallback" if is_fallback else "category",
        ledger_account.id, user_id, category_id, class_id, is_fallback,
    )
    return ledger_account


# The three per-loan ledger-account kinds the Step-4 loan-payment correction
# books into, each mapped to (its accounting class, the display-name suffix
# snapshotted into ``name``).  ``loan_interest`` and ``loan_escrow`` are
# Expense (the accrued interest and the configured escrow both leave the
# borrower as an expense at payment time); ``loan_refund`` is an Asset (a
# payoff overpayment the lender owes back).  Spelled out here, like
# ``_FALLBACK_LEDGER_ACCOUNT_NAMES``, rather than derived from the enum value
# so renaming a ``LedgerAccountKindEnum`` member can never silently rewrite the
# class or the label on already-posted per-loan rows.
#
# This map is the resolver's -- and therefore the app's -- sole guarantee that
# a ``loan_account_id`` row carries one of the three loan kinds and the
# accounting class that kind implies: the shipped
# ``ck_ledger_accounts_loan_shape`` CHECK is columns-only (a CHECK cannot
# subquery ``ref.ledger_account_kinds`` and the project forbids hardcoding its
# IDs -- see the model docstring), so nothing at the storage tier pins a loan
# row's ``kind_id`` to a loan kind.  A kind absent from this map is rejected
# before any write -- the load-bearing guard, not belt-and-suspenders.
_LOAN_LEDGER_KINDS = {
    LedgerAccountKindEnum.LOAN_INTEREST: (LedgerAccountClassEnum.EXPENSE, "Interest"),
    LedgerAccountKindEnum.LOAN_ESCROW: (LedgerAccountClassEnum.EXPENSE, "Escrow"),
    LedgerAccountKindEnum.LOAN_REFUND: (LedgerAccountClassEnum.ASSET, "Refund"),
}


def _find_existing_loan_ledger_account(
    user_id: int, loan_account_id: int, kind_id: int,
) -> LedgerAccount | None:
    """Return the existing per-loan ledger account, or None.

    The idempotency lookup for :func:`get_or_create_loan_ledger_account`,
    keyed to match the ``uq_ledger_accounts_loan`` partial unique exactly:
    ``(user_id, loan_account_id, kind_id)`` among the rows
    ``WHERE loan_account_id IS NOT NULL``.  ``loan_account_id`` is non-NULL
    here (the caller only resolves a concrete loan), so the row is inside the
    index's predicate and the three-column key identifies at most one row --
    one ``loan_interest`` / ``loan_escrow`` / ``loan_refund`` account per
    (owner, loan).

    Args:
        user_id: The owning user's id.
        loan_account_id: The loan ``budget.accounts`` id whose per-loan
            account is sought.
        kind_id: The ``ref.ledger_account_kinds`` PK of the loan kind
            (``loan_interest`` / ``loan_escrow`` / ``loan_refund``).

    Returns:
        The matching :class:`~app.models.ledger_account.LedgerAccount`, or
        None when none exists yet.
    """
    return (
        db.session.query(LedgerAccount)
        .filter_by(
            user_id=user_id,
            loan_account_id=loan_account_id,
            kind_id=kind_id,
        )
        .first()
    )


def _load_amortizing_loan_account(user_id: int, loan_account_id: int) -> Account:
    """Load and validate the loan account a per-loan ledger row will link.

    Resolves the ``budget.accounts`` row by ``(id, user_id)`` -- the tenancy
    filter, matching :func:`_category_display_name`, so a ``loan_account_id``
    belonging to another user is treated as "not found" rather than minting an
    owner-A row keyed to user B's loan.  Deliberately NOT filtered by
    ``is_active``: an archived loan that still carries settled payment history
    must keep resolving its per-loan accounts so the immutable postings on them
    reconcile (archiving disables new activity, it does not erase posted facts).

    Then guards that the account is an amortizing loan (``classify_account ==
    AMORTIZING``, which reads the ``has_amortization`` boolean -- never a type
    name string).  This is the load-bearing companion to the kind guard in
    :func:`get_or_create_loan_ledger_account`: ``ck_ledger_accounts_loan_shape``
    polices only a per-loan row's column shape, so nothing at the storage tier
    stops a ``loan_account_id`` pointing at a Checking or Credit Card account.
    The resolver is the sole writer and therefore the only guarantee that a
    per-loan ledger row links a real loan.

    Args:
        user_id: The owning user's id (the loan must belong to them).
        loan_account_id: The loan ``budget.accounts`` id (non-NULL).

    Returns:
        The validated :class:`~app.models.account.Account` (an amortizing
        loan owned by ``user_id``), with ``account_type`` eager-loaded.

    Raises:
        ValueError: If no account with that id is owned by ``user_id``, or if
            the account is not an amortizing loan.  A live caller (the Step-4
            poster) only ever resolves a settled loan payment's loan account,
            so a miss or a non-loan account signals a caller bug; fail loud
            with the offending id and the account's actual projection kind.
    """
    loan = (
        db.session.query(Account)
        .filter_by(id=loan_account_id, user_id=user_id)
        .first()
    )
    if loan is None:
        raise ValueError(
            f"cannot create a loan ledger account: no account with "
            f"id={loan_account_id} owned by user_id={user_id}"
        )
    projection_kind = classify_account(loan)
    if projection_kind is not AccountProjectionKind.AMORTIZING:
        raise ValueError(
            f"cannot create a loan ledger account: account id={loan_account_id} "
            f"is not an amortizing loan (classifies as {projection_kind.value!r})"
        )
    return loan


def get_or_create_loan_ledger_account(
    user_id: int,
    loan_account_id: int,
    kind: LedgerAccountKindEnum,
) -> LedgerAccount:
    """Ensure a loan's per-kind interest / escrow / refund ledger account exists.

    The Build-Order Step 4 chart resolver: a confirmed loan payment's real-split
    correction books its accrued interest into the loan's ``loan_interest``
    Expense account, its configured escrow into the ``loan_escrow`` Expense
    account, and any payoff overpayment into the ``loan_refund`` Asset account.
    This lazily materialises (and thereafter reuses) the requested one.

    The accounting class is derived from ``kind`` (``loan_interest`` /
    ``loan_escrow`` -> Expense; ``loan_refund`` -> Asset) via
    ``_LOAN_LEDGER_KINDS`` -- the caller passes only the kind, so the class can
    never be set inconsistently with it.  ``kind`` MUST be one of the three
    loan kinds; any other (``linked`` / ``category`` / ``fallback`` / ``orphan``)
    is rejected before any write.  That guard, and the amortizing-loan guard in
    :func:`_load_amortizing_loan_account`, are load-bearing rather than
    belt-and-suspenders: the shipped ``ck_ledger_accounts_loan_shape`` CHECK is
    columns-only (it cannot pin ``kind_id`` without subquerying ``ref`` or
    hardcoding its IDs), so this resolver is the only thing keeping a per-loan
    row's kind a loan kind and its ``loan_account_id`` a real loan (the same
    un-CHECKed trust contract ``class_id`` already carries).

    Idempotent: an existing row for the ``(user, loan, kind)`` natural key is
    returned unchanged (the ``uq_ledger_accounts_loan`` partial unique would
    otherwise reject a duplicate).  The created row sets ``loan_account_id``,
    leaves ``account_id`` / ``category_id`` NULL and ``is_fallback`` False (the
    per-loan column shape ``ck_ledger_accounts_loan_shape`` requires), and
    snapshots a display ``name`` (``"<loan name> -- Interest|Escrow|Refund"``)
    clipped to the column width -- like a category row the snapshot is frozen at
    creation, so renaming the loan never rewrites posted history (and unlike a
    linked row, a per-loan row has ``account_id`` NULL, so
    ``ck_ledger_accounts_name_present`` requires the stored ``name``).

    Flushes so the new row's ``id`` is assigned, but does NOT commit -- the
    caller (the Step-4 ``posting_service``) owns the transaction boundary.

    Args:
        user_id: The owning user's id.
        loan_account_id: The loan ``budget.accounts`` id whose payment split
            this account books.  Must be an amortizing loan owned by
            ``user_id`` (validated when the row is first created).
        kind: The per-loan kind to resolve, a
            :class:`~app.enums.LedgerAccountKindEnum` member that MUST be
            ``LOAN_INTEREST``, ``LOAN_ESCROW``, or ``LOAN_REFUND``.

    Returns:
        The :class:`~app.models.ledger_account.LedgerAccount` for the
        ``(user, loan, kind)`` key (existing, or newly created and flushed).

    Raises:
        ValueError: If ``kind`` is not one of the three loan kinds, or (on
            first creation) if ``loan_account_id`` names no amortizing loan
            owned by ``user_id`` (see :func:`_load_amortizing_loan_account`).
            No database CHECK enforces either, so these guards are the sole
            defense against a malformed per-loan chart entry.
    """
    if kind not in _LOAN_LEDGER_KINDS:
        raise ValueError(
            f"loan ledger account kind must be one of "
            f"{sorted(member.value for member in _LOAN_LEDGER_KINDS)}, "
            f"got {kind!r}"
        )
    ledger_class, component = _LOAN_LEDGER_KINDS[kind]
    class_id = ref_cache.ledger_account_class_id(ledger_class)
    kind_id = ref_cache.ledger_account_kind_id(kind)

    existing = _find_existing_loan_ledger_account(
        user_id, loan_account_id, kind_id,
    )
    if existing is not None:
        return existing

    loan = _load_amortizing_loan_account(user_id, loan_account_id)
    name = f"{loan.name} -- {component}"[:_LEDGER_ACCOUNT_NAME_MAX_LEN]
    ledger_account = LedgerAccount(
        user_id=user_id,
        class_id=class_id,
        kind_id=kind_id,
        loan_account_id=loan_account_id,
        name=name,
    )
    db.session.add(ledger_account)
    db.session.flush()
    logger.info(
        "Created loan %s ledger account id=%d (user_id=%d, "
        "loan_account_id=%d, class_id=%d, kind_id=%d)",
        component, ledger_account.id, user_id, loan_account_id,
        class_id, kind_id,
    )
    return ledger_account
