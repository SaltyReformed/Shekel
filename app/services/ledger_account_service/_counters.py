"""The per-ACCOUNT chart row that holds an anchor correction's counter leg.

Build-Order Step 5's half of the chart of accounts.  A non-loan account's
``account_opening`` / ``account_trueup`` corrections are balanced two-leg
entries: one leg moves the account's own linked row, and the other -- the
COUNTER leg -- lands here, which is what makes every non-loan linked ledger sum
to an ABSOLUTE balance and closes the app-wide trial balance.

One kind holds it today, ``anchor_equity``: the account's opening/true-up
Equity account.  It shares the ``account_id`` column with the ``linked`` row
(the two coexist under the ``(account_id, kind_id)`` key of
``uq_ledger_accounts_account_kind``) and, unlike a linked row, always snapshots
a display ``name`` -- the COALESCE display rule is the LINKED-row rule, so
readers render this row's snapshot.

The loan analogue is :mod:`._loans`' ``equity_opening`` kind; the two families
never overlap, because :func:`_load_non_loan_account` rejects amortizing loans
here and ``_load_amortizing_loan_account`` rejects everything else there.

Flask-isolated and commit-free: plain data in, ORM objects out; the caller owns
the transaction boundary.
"""

import logging

from app import ref_cache
from app.enums import LedgerAccountClassEnum, LedgerAccountKindEnum
from app.extensions import db
from app.models.account import Account
from app.models.ledger_account import LedgerAccount
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)

from ._common import (
    LEDGER_ACCOUNT_NAME_MAX_LEN,
    add_or_reuse,
    load_owned_account,
)

logger = logging.getLogger(__name__)


def _load_non_loan_account(user_id: int, account_id: int) -> Account:
    """Load and validate the NON-loan account an anchor-equity row will link.

    The inverse companion of :func:`._loans._load_amortizing_loan_account`:
    resolves the ``budget.accounts`` row through the shared tenancy-filtered
    loader (:func:`._common.load_owned_account`) and guards that the account is
    NOT an amortizing loan.  Loans post their anchor corrections through the
    ``LoanAnchorEvent``-driven loan path onto their per-loan
    ``equity_opening`` account; minting an ``anchor_equity`` twin for a loan
    would double-book its opening across two equity accounts.

    This guard is also what keeps the loan reconciliation oracle's
    bare-``account_id`` ledger helpers honest by construction: the account
    walk can never touch a loan's ``account_id``, so a loan's linked ledger
    never gains a twin (recorded in the Step-5 plan's C6 checklist).

    Args:
        user_id: The owning user's id (the account must belong to them).
        account_id: The non-loan ``budget.accounts`` id (non-NULL).

    Returns:
        The validated :class:`~app.models.account.Account` (a non-amortizing
        account owned by ``user_id``), with ``account_type`` eager-loaded.

    Raises:
        ValueError: If no account with that id is owned by ``user_id`` (from
            the shared loader), or if the account IS an amortizing loan.  Fail
            loud with the offending id rather than minting a malformed chart
            entry -- no database CHECK pins an ``anchor_equity`` row's target,
            so this guard is the sole defense (the same trust contract the
            loan resolver carries).
    """
    account = load_owned_account(user_id, account_id, "an anchor-equity")
    projection_kind = classify_account(account)
    if projection_kind is AccountProjectionKind.AMORTIZING:
        raise ValueError(
            f"cannot create an anchor-equity ledger account: account "
            f"id={account_id} is an amortizing loan (loans book their "
            f"anchor corrections onto their per-loan equity_opening "
            f"account, never an anchor_equity twin)"
        )
    return account


def get_or_create_anchor_equity_account(
    user_id: int, account_id: int,
) -> LedgerAccount:
    """Ensure a non-loan account's ``anchor_equity`` Equity account exists.

    The Build-Order Step 5 chart resolver: a non-loan account's
    ``account_opening`` / ``account_trueup`` corrections book their equity
    counter-leg into this per-account Equity account, and this lazily
    materialises (and thereafter reuses) it.  The loan analogue is the
    ``equity_opening`` kind resolved by
    :func:`._loans.get_or_create_loan_ledger_account`; the two kinds never
    overlap because :func:`_load_non_loan_account` rejects amortizing loans
    here and ``_load_amortizing_loan_account`` rejects everything else there.

    Idempotent: an existing row for the ``(account, kind)`` natural key is
    returned unchanged (the ``uq_ledger_accounts_account_kind`` partial
    unique would otherwise reject a duplicate).  The created row sets
    ``account_id`` (sharing the column with the account's ``linked`` row --
    the two coexist under the re-keyed unique), leaves ``category_id`` /
    ``loan_account_id`` NULL and ``is_fallback`` False, carries the Equity
    class, and ALWAYS snapshots a display ``name``
    (``"<account name> -- Opening"``, clipped to the column width): the
    COALESCE display rule is the LINKED-row rule, so readers branch on
    ``kind_id`` and render this snapshot (see
    :class:`app.models.ledger_account.LedgerAccount`).  Like every snapshot,
    it is frozen at creation so renaming the account never rewrites posted
    history.

    Flushes so the new row's ``id`` is assigned, but does NOT commit -- the
    caller (the Step-5 ``account_posting_service``) owns the transaction
    boundary.

    Args:
        user_id: The owning user's id.
        account_id: The non-loan ``budget.accounts`` id whose anchor
            corrections this account books.  Must be a non-amortizing
            account owned by ``user_id`` (validated when the row is first
            created).

    Returns:
        The :class:`~app.models.ledger_account.LedgerAccount` for the
        ``(account, anchor_equity)`` key (existing, or newly created and
        flushed).

    Raises:
        ValueError: If (on first creation) ``account_id`` names no account
            owned by ``user_id``, or names an amortizing loan (see
            :func:`_load_non_loan_account`).  No database CHECK enforces
            either, so the guard is the sole defense against a malformed
            chart entry.
    """
    kind_id = ref_cache.ledger_account_kind_id(
        LedgerAccountKindEnum.ANCHOR_EQUITY,
    )

    def _find_existing():
        return (
            db.session.query(LedgerAccount)
            .filter_by(user_id=user_id, account_id=account_id, kind_id=kind_id)
            .first()
        )

    existing = _find_existing()
    if existing is not None:
        return existing

    account = _load_non_loan_account(user_id, account_id)
    name = f"{account.name} -- Opening"[:LEDGER_ACCOUNT_NAME_MAX_LEN]
    ledger_account = add_or_reuse(
        LedgerAccount(
            user_id=user_id,
            class_id=ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.EQUITY,
            ),
            kind_id=kind_id,
            account_id=account_id,
            name=name,
        ),
        _find_existing,
    )
    logger.info(
        "Resolved anchor-equity ledger account id=%d (user_id=%d, "
        "account_id=%d)",
        ledger_account.id, user_id, account_id,
    )
    return ledger_account
