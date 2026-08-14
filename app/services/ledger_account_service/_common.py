"""The pieces every chart-of-accounts resolver in this package shares.

Four resolvers materialise ``budget.ledger_accounts`` rows -- the linked
Asset/Liability row (:mod:`._linked`), the per-category Income/Expense rows and
their Uncategorized fallback (:mod:`._categories`), the per-loan rows
(:mod:`._loans`), and the per-account counter-leg rows (:mod:`._counters`).
What they genuinely share lives here so no resolver re-spells it (a
``duplicate-code`` finding) and all four agree exactly:

* :data:`LEDGER_ACCOUNT_NAME_MAX_LEN` -- the display-``name`` column width,
  read off the column so a snapshot clip can never drift from the schema.
* :func:`add_or_reuse` -- the natural-key race handler every RACEABLE
  get-or-create defers to.
* :func:`load_owned_account` -- the tenancy-filtered account load the two
  account-linked resolvers guard their targets with.

Flask-isolated and commit-free like its consumers: plain data in, ORM objects
out; flushes only inside its own SAVEPOINT; the caller owns the transaction.
"""

import logging

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.account import Account
from app.models.ledger_account import LedgerAccount
from app.utils.db_errors import is_unique_violation

logger = logging.getLogger(__name__)

# The maximum length of a snapshotted display ``name``, read straight from the
# column so it can never drift from the schema.  A category's ``display_name``
# ("Group: Item") concatenates two ``String(100)`` halves and so can reach
# ~202 characters -- wider than this ``String(100)`` column, which PostgreSQL
# rejects (it does not silently truncate) on insert.  Because ``name`` is
# display-only (the natural key is the (user, category, class) IDs, never the
# label), truncating the snapshot to fit is lossless for logic; this mirrors
# the ``description = txn.name[:200]`` snapshot pattern the posting ledger
# already uses.
LEDGER_ACCOUNT_NAME_MAX_LEN = LedgerAccount.__table__.columns["name"].type.length

# Every partial-unique index that makes a ledger account's NATURAL key unique,
# read off the live catalog rather than recalled: ``\di budget.ledger_accounts``
# on a production clone 2026-08-04.  They are the arbiters
# :func:`add_or_reuse` defers to when two concurrent requests both find no row
# and both try to create one; naming them means an unrelated IntegrityError
# still propagates rather than being read as "someone else won the race".
# Renaming any of them requires a coordinated edit here and in its migration.
_LEDGER_ACCOUNT_NATURAL_KEYS = (
    "uq_ledger_accounts_account_kind",
    "uq_ledger_accounts_category",
    "uq_ledger_accounts_loan",
    "uq_ledger_accounts_uncategorized",
)


def add_or_reuse(ledger_account: LedgerAccount, find_existing) -> LedgerAccount:
    """Insert *ledger_account*, or return the row a concurrent request won with.

    **Every get-or-create in this package that two requests can RACE FOR is a
    check-then-INSERT, and that is a race.**  Two requests can both find no row
    for a natural key and both try to create one; the loser's flush hits
    ``uq_ledger_accounts_account_kind`` (or its category twin) and, uncaught,
    surfaces as a 500 on an operation that in fact succeeded -- the winner's
    row is exactly the row the loser wanted.  Three resolvers consume this:
    the per-account counter, category and loan ones.

    **``create_ledger_account_for_account`` is deliberately NOT converted, and
    an earlier version of this docstring said "every get-or-create in this
    module" without the qualifier.**  Its only caller is
    ``account_service.create_account``, immediately after INSERTing a brand-new
    ``accounts`` row, so its natural key ``(account_id, linked kind)`` carries
    an id no other transaction can hold: two concurrent creates make two
    different accounts and cannot collide.  Wrapping it would be a SAVEPOINT
    for a state the database cannot reach.

    It was unreachable on the anchor-equity path until plan step X-f1c3c,
    because the C-17 optimistic lock made one of two concurrent true-ups 409
    before either reached the chart resolver.  Ruling R-EN deleted that lock
    (an assertion history is append-only, so there is nothing to contend for),
    which is what made the race REACHABLE and is how it was found -- by the
    concurrency test, not by a review.  The lock was never what made this
    correct, it was only what hid it: the same race is reachable on the
    category and loan resolvers through any two concurrent settles.

    The INSERT runs in a SAVEPOINT so a lost race does not poison the caller's
    transaction, and the re-read is the caller's own lookup rather than a
    second query written here -- so "which row is THE row for this key" has one
    definition per resolver, not two.

    Args:
        ledger_account: The unflushed row to insert.
        find_existing: The caller's zero-argument lookup for the same natural
            key, re-run after a lost race.

    Returns:
        The flushed new row, or the equal row a concurrent request committed.

    Raises:
        IntegrityError: When the violation is NOT one of this table's natural
            keys -- a different constraint failed and must not be swallowed.
        RuntimeError: When the natural key rejected the insert and the re-read
            then finds nothing.  Not reachable through the database's own
            semantics (the row that rejected us is visible once our savepoint
            is gone), so it is a fail-loud trap for a resolver whose lookup and
            whose INSERT disagree about the key.
    """
    try:
        with db.session.begin_nested():
            db.session.add(ledger_account)
            db.session.flush()
    except IntegrityError as exc:
        if not any(
            is_unique_violation(exc, name)
            for name in _LEDGER_ACCOUNT_NATURAL_KEYS
        ):
            raise
        existing = find_existing()
        if existing is None:
            raise RuntimeError(
                "ledger_account_service: a natural-key unique violation "
                f"rejected the insert for user_id={ledger_account.user_id}, "
                f"kind_id={ledger_account.kind_id}, but the re-read found no "
                "row -- the resolver's lookup and its INSERT disagree about "
                "the key."
            ) from exc
        logger.info(
            "Reused ledger account id=%d after a concurrent create "
            "(user_id=%d, kind_id=%d)",
            existing.id, existing.user_id, existing.kind_id,
        )
        return existing
    return ledger_account


def load_owned_account(
    user_id: int, account_id: int, chart_entry: str,
) -> Account:
    """Load the account a chart row will link, filtered by its owner.

    The tenancy-filtered load both account-linked resolvers guard their target
    with (:func:`._loans.get_or_create_loan_ledger_account` and
    :func:`._counters.get_or_create_account_counter_account`): the
    ``budget.accounts`` row is resolved by ``(id, user_id)``, matching
    ``_category_display_name``'s scoping, so an ``account_id`` belonging to
    another user is treated as "not found" rather than minting an owner-A row
    keyed to user B's account.

    Deliberately NOT filtered by ``is_active``: an archived account that still
    carries settled history must keep resolving its chart rows so the immutable
    postings reconcile (archiving disables new activity, it does not erase
    posted facts).

    It loads and scopes only.  **The projection-kind guard stays with each
    caller**, because the two want opposite answers -- the loan resolver
    requires ``AMORTIZING`` and the counter resolver refuses it -- and folding
    that into a boolean parameter here would put one flag in front of two
    different invariants.

    Args:
        user_id: The owning user's id (the account must belong to them).
        account_id: The ``budget.accounts`` id to load (non-NULL).
        chart_entry: The chart row being created, as a noun phrase for the
            failure message (e.g. ``"a loan"``, ``"a per-account counter"``).

    Returns:
        The :class:`~app.models.account.Account`, with ``account_type``
        eager-loaded (the relationship's ``lazy="joined"``) so the caller's
        classification guard issues no second query.

    Raises:
        ValueError: If no account with that id is owned by ``user_id``.  A live
            caller only ever resolves an account it has already read, so a miss
            signals a caller bug; fail loud with the offending ids rather than
            minting a malformed chart entry.
    """
    account = (
        db.session.query(Account)
        .filter_by(id=account_id, user_id=user_id)
        .first()
    )
    if account is None:
        raise ValueError(
            f"cannot create {chart_entry} ledger account: no account with "
            f"id={account_id} owned by user_id={user_id}"
        )
    return account
