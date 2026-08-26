"""
Shekel Budget App -- Archive and Delete History Helpers

Provides history-detection functions used by the unified delete/archive
pattern across transaction templates, transfer templates, accounts,
and categories.  Each function answers: "Does this entity have settled
history that prevents permanent deletion?"

These functions are pure queries -- they do not perform mutations.

The transaction/transfer template predicates filter on the semantic
``Status.is_settled`` boolean column (audit finding CRIT-05 / E-22):
enumerating ``[Paid, Settled]`` by name or ID silently missed Received
-- the status assigned to every income paycheck on mark-done -- and
let a normal user permanently destroy real RECEIVED income history.
``is_settled`` is the single source of truth for "this transaction is
real money already exchanged" (Paid, Received, Settled all carry
``is_settled=True`` in ``ref_seeds.py``), so a boolean predicate
covers every current and future settled status without enumeration.
"""

from app.extensions import db
from app.models.journal_entry import Posting
from app.models.ledger_account import LedgerAccount
from app.models.merchant_rule import MerchantRule
from app.models.pay_period import PayPeriod
from app.models.ref import Status
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer


def template_has_paid_history(template_id: int) -> bool:
    """Check if a transaction template has any settled transactions.

    "Settled" is determined by the semantic ``Status.is_settled``
    boolean (Paid, Received, Settled in the current seed -- see
    ``ref_seeds.py``).  Enumerating status names or IDs here would
    silently miss any status added to the settled set in the
    future; the boolean column is the single source of truth.
    Audit reference: CRIT-05 / E-22 (the prior ``[DONE, SETTLED]``
    enumeration omitted RECEIVED and enabled irreversible RECEIVED
    income-history deletion).

    Args:
        template_id: The TransactionTemplate.id to check.

    Returns:
        True if at least one linked transaction has a settled status
        and is not soft-deleted.
    """

    return db.session.query(
        db.session.query(Transaction)
        .join(Status, Transaction.status_id == Status.id)
        .filter(
            Transaction.template_id == template_id,
            Status.is_settled.is_(True),
            Transaction.is_deleted.is_(False),
        ).exists()
    ).scalar()


def transfer_template_has_paid_history(template_id: int) -> bool:
    """Check if a transfer template has any settled transfers.

    Mirrors :func:`template_has_paid_history`: filters on the
    semantic ``Status.is_settled`` boolean so Received and any
    future settled status are covered without enumeration.  Audit
    reference: CRIT-05 / E-22.

    Args:
        template_id: The TransferTemplate.id to check.

    Returns:
        True if at least one linked transfer has a settled status
        and is not soft-deleted.
    """

    return db.session.query(
        db.session.query(Transfer)
        .join(Status, Transfer.status_id == Status.id)
        .filter(
            Transfer.transfer_template_id == template_id,
            Status.is_settled.is_(True),
            Transfer.is_deleted.is_(False),
        ).exists()
    ).scalar()


def account_has_history(account_id: int) -> bool:
    """Check if an account has any non-deleted transactions.

    Unlike the template history checks, this does NOT filter by
    status.  Any non-deleted transaction means the account has
    history.  This is intentionally stricter than the template
    functions because account deletion would cascade to all related
    financial records -- even Projected transactions represent
    user-entered data worth preserving.

    Args:
        account_id: The Account.id to check.

    Returns:
        True if the account has any non-deleted transaction history.
    """

    return db.session.query(
        db.session.query(Transaction).filter(
            Transaction.account_id == account_id,
            Transaction.is_deleted.is_(False),
        ).exists()
    ).scalar()


def account_has_ledger_postings(account_id: int) -> bool:
    """Check if any of an account's ledger accounts has postings.

    A settled transfer writes balanced journal entries onto the account's
    linked ledger account (Build-Order Step 2).  Those entries are immutable
    and SURVIVE a transfer delete (``journal_entries.transfer_id`` SET NULL),
    so an account can still hold posting legs after every transaction
    referencing it is gone (e.g. its ad-hoc transfer was hard-deleted).
    Hard-deleting such an account would CASCADE-delete only its own legs and
    strand the paired legs as unbalanced single-leg entries (the balanced
    trigger fires on INSERT/UPDATE, not DELETE), so the hard-delete guard
    archives it instead.  The posting-ledger counterpart of
    :func:`account_has_history`.

    **It joins by ``account_id`` across ALL kinds, and that is load-bearing
    rather than incidental.**  An account carries its ``linked`` row plus its
    per-account COUNTER rows, and since ruling **R-FO** a correction re-pointed
    from one counter row to another emits an entry whose ONLY legs are counter
    legs -- so a linked-row-scoped check would answer False for an account that
    really does hold immutable posted history, and the hard delete would
    proceed.  The kind-agnostic join is what makes the model's
    cascade-imbalance impossibility argument true by construction rather than
    by a premise about which rows corrections touch.

    Args:
        account_id: The Account.id to check.

    Returns:
        True if any ledger account linked to *account_id* has at least one
        posting.
    """

    return db.session.query(
        db.session.query(Posting)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(LedgerAccount.account_id == account_id)
        .exists()
    ).scalar()


def category_has_usage(category_id: int, user_id: int) -> bool:
    """Check if a category is in use by templates, transactions or rules.

    Performs a three-part check: (1) any TransactionTemplate with matching
    category_id and user_id, (2) any Transaction with matching category_id
    joined to PayPeriod filtered by user_id, and (3) any standing merchant
    rule whose *new envelope* answer files under it.  Short-circuits in that
    order, cheapest first, so the transaction join is only paid for when the
    two indexed reads either side of it find nothing.

    The user_id scoping is critical -- categories are user-scoped, and
    the check must not cross user boundaries.

    **The third part was added at plan step ``bank_import:X-gd-2``, and it is
    about what ``delete_category`` does with the answer.**  A "no" here is what
    permits a PERMANENT delete, and ``fk_merchant_rules_category_owner``
    cascades -- so a category no template and no transaction used, but that one
    merchant rule filed under, was destroyed together with the rule, under a
    flash reading "permanently deleted" that said nothing about the rule.  The
    cascade itself is right (an answer naming a category that no longer exists
    is not an answer); what was wrong was a door calling the category unused
    while a stored decision used it.  Ruling **R-GS** makes that worse rather
    than better: a rule row is never un-stated by the owner, so a silent
    cascade would be the only way one could vanish.  Measured on the
    developer's dev database, 2026-08-26: 12 new-envelope rules naming 6
    distinct categories, every one of them also used by a template or a
    transaction -- so the path is reachable and has not yet fired.

    **A rule carries a ``user_id`` of its own**, held equal to its account's
    owner by ``fk_merchant_rules_owner``, so this scopes on the same column the
    two clauses above do rather than joining through the account.

    Args:
        category_id: The Category.id to check.
        user_id: The user who owns the category (for ownership scoping).

    Returns:
        True if any templates, transactions or standing merchant rules
        reference this category for the given user.
    """

    # Check templates first -- cheap query with direct user_id column.
    has_templates = db.session.query(
        db.session.query(TransactionTemplate).filter_by(
            category_id=category_id, user_id=user_id,
        ).exists()
    ).scalar()

    if has_templates:
        return True

    # ...then the merchant rules, for the same reason and at the same cost:
    # a direct user_id column, before the join below is paid for.
    has_rules = db.session.query(
        db.session.query(MerchantRule).filter_by(
            category_id=category_id, user_id=user_id,
        ).exists()
    ).scalar()

    if has_rules:
        return True

    # Check transactions -- requires join through PayPeriod for user scoping.
    return db.session.query(
        db.session.query(Transaction)
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(
            PayPeriod.user_id == user_id,
            Transaction.category_id == category_id,
        ).exists()
    ).scalar()
