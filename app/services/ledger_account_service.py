"""
Shekel Budget App -- Ledger Account Service

Sole writer of ``budget.ledger_accounts`` (the chart of accounts for the
double-entry posting ledger, Build-Order Step 2).  Pairs every real
``budget.accounts`` row with exactly one Asset or Liability ledger account
so that later steps' postings have an account to land in.

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
from app.enums import AcctCategoryEnum, LedgerAccountClassEnum
from app.extensions import db
from app.models.account import Account
from app.models.ledger_account import LedgerAccount


logger = logging.getLogger(__name__)


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
    with the derived class, ``name`` left NULL (the display label derives
    from ``account.name``), and the owning ``user_id`` copied from the
    account.

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
