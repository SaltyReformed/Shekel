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
real money already exchanged" (Paid and Received both carry
``is_settled=True`` in ``ref_seeds.py``), so a boolean predicate
covers every current and future settled status without enumeration.

That last clause has since been paid out twice.  ``Settled`` -- the terminal
ARCHIVE -- joined the band and then LEFT it at plan step **balance:X-am**, and
neither change touched a line in this module, because none of these predicates
ever named a status.
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
    boolean (Paid and Received in the current seed -- see
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


def template_has_standing_rule(template_id: int) -> bool:
    """Check if a standing merchant rule files a merchant's spending here.

    **The template twin of :func:`category_has_usage`'s merchant-rule clause**,
    and it exists for the same reason and closes the same defect on the other
    of the two cascading subject keys (plan step ``bank_import:X-gd-2``).
    ``fk_merchant_rules_template_account`` is ``ON DELETE CASCADE``, so
    permanently deleting a template destroys every rule that names it -- and
    ``hard_delete_template`` gated only on settled TRANSACTIONS, which knows
    nothing about rules.

    Measured on the developer's own dev database, 2026-08-26: 16 of 29 rules
    name a template, and template 19 (`Clothes`) carries a rule and ZERO
    settled transactions -- so the permanent-delete arm was live on it and one
    press would have destroyed a stated answer under a flash that said only
    that the template was deleted.  Ruling **R-GS** is what makes it matter
    rather than merely untidy: a rule row is never un-stated by the owner, so
    a silent cascade would be the only way one could vanish.

    **It is NOT folded into :func:`template_has_paid_history`**, which is cited
    by name from the transfer-template route's own guard and means exactly what
    it says.  Two predicates with two reasons give the door two SENTENCES, and
    telling an owner their template "has payment history" when what it has is a
    merchant rule is the screens-stating-what-is-false defect this arc has
    closed three times.

    Args:
        template_id: The TransactionTemplate.id to check.

    Returns:
        True if any :class:`~app.models.merchant_rule.MerchantRule` names it.
        **Not scoped**, for the reason ``category_has_usage``'s own clause is
        not: ``fk_merchant_rules_template_account`` is composite over
        ``(template_id, account_id)``, and ``transaction_templates.id`` is a
        primary key -- so a rule naming this template can only be on this
        template's account, and its owner is that account's.
    """

    return db.session.query(
        db.session.query(MerchantRule).filter(
            MerchantRule.template_id == template_id,
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

    Performs a three-part check, short-circuiting in the order it runs them:
    (1) any TransactionTemplate with matching category_id and user_id, (2) any
    standing merchant rule that names it -- as the category a *new envelope*
    answer creates under, OR as the income category a deposit from that
    merchant is filed under (plan step ``bank_import:X-gj-2a``) -- and (3) any
    Transaction with matching category_id joined to PayPeriod filtered by
    user_id.  The join is last because it is the only one of the three that
    needs one; none of the three has an index on ``category_id``, so all three
    are sequential scans over small tables and the ordering buys the JOIN
    rather than a lookup.

    The user_id scoping is critical for (1) and (3) -- categories are
    user-scoped, and the check must not cross user boundaries.

    **The merchant-rule part was added at plan step ``bank_import:X-gd-2``, and
    it is about what ``delete_category`` does with the answer.**  A "no" here is what
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

    **It is NOT scoped, and that is structural rather than an omission.**  The
    two clauses beside it filter on ``user_id`` because they must:
    ``transaction_templates.category_id`` and ``transactions.category_id`` are
    plain single-column keys, so either can name a category belonging to
    somebody else and the reader is what stops it.  A rule cannot:
    ``fk_merchant_rules_category_owner`` is composite over
    ``(category_id, user_id)`` against ``categories(id, user_id)``, and
    ``categories.id`` is a primary key -- so a ``category_id`` DETERMINES its
    owner and a rule naming one necessarily carries that owner's id.  A
    ``user_id`` term here would restate what the constraint already holds, and
    no test could make it fire; an adversarial review 2026-08-26 found the case
    written for it grading a different scenario for exactly that reason.

    **What it deliberately does NOT filter is the ACCOUNT.**  A rule is
    account-scoped and a category is not, so one owner's rules on two accounts
    may both file under one category and BOTH are usage.

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

    # ...then the merchant rules, before the join below is paid for.  No
    # ``user_id`` term: ``fk_merchant_rules_category_owner`` is composite over
    # ``(category_id, user_id)`` and ``categories.id`` is a primary key, so a
    # rule naming this category can only be this owner's.  No ``account_id``
    # term either, and that one IS load-bearing: a category is owner-scoped
    # while a rule is account-scoped, so two accounts' rules may file under one
    # category and both are usage.
    #
    # **BOTH answer columns, since plan step ``bank_import:X-gj-2a``.**  A rule
    # names a category in two different ways now -- ``category_id`` is the
    # category a *new envelope* answer creates its envelope under, and
    # ``income_category_id`` is what a DEPOSIT from that merchant is -- and
    # ``fk_merchant_rules_income_category_owner`` cascades exactly as its twin
    # does.  Asking about only the first would have re-created, on the new
    # column, the precise defect this clause was added for: a category no
    # template and no transaction used but that one income rule filed under
    # would be reported UNUSED, permanently deleted, and take the rule with it
    # under a flash saying nothing about it.  A rule is never un-stated by the
    # owner (ruling **R-GS**), so that cascade is the only way one can vanish.
    has_rules = db.session.query(
        db.session.query(MerchantRule).filter(
            db.or_(
                MerchantRule.category_id == category_id,
                MerchantRule.income_category_id == category_id,
            ),
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
