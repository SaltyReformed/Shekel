"""The per-LOAN chart rows: interest, escrow, refund, and the opening equity.

Build-Order Step 4's half of the chart of accounts, plus the ``equity_opening``
row the loan read switch added.  A confirmed loan payment's real-split
correction books its accrued interest, its configured escrow and any payoff
overpayment into three per-loan accounts, and the once-per-loan opening entry
books the origination balance into a fourth.  All four carry
``loan_account_id`` and are keyed ``(user, loan, kind)`` by
``uq_ledger_accounts_loan``.

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

# The four per-loan ledger-account kinds this resolver materialises, each
# mapped to (its accounting class, the display-name suffix snapshotted into
# ``name``).  Three are booked by the Step-4 loan-payment correction:
# ``loan_interest`` and ``loan_escrow`` are Expense (the accrued interest and
# the configured escrow both leave the borrower as an expense at payment time)
# and ``loan_refund`` is an Asset (a payoff overpayment is a receivable).  The
# fourth, ``equity_opening``, holds the credit counter-leg of the
# once-per-loan opening-equity entry the loan read switch (Step 4, second half)
# books at origination -- the loan's opening balance posted as Equity so the
# ledger is authoritative for its confirmed balance; it is Equity class.
# Spelled out here, like ``_FALLBACK_LEDGER_ACCOUNT_NAMES``, rather than
# derived from the enum value so renaming a ``LedgerAccountKindEnum`` member can
# never silently rewrite the class or the label on already-posted per-loan rows.
#
# This map is the resolver's -- and therefore the app's -- sole guarantee that
# a ``loan_account_id`` row carries one of the four loan kinds and the
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
    LedgerAccountKindEnum.EQUITY_OPENING: (LedgerAccountClassEnum.EQUITY, "Opening"),
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
    one ``loan_interest`` / ``loan_escrow`` / ``loan_refund`` /
    ``equity_opening`` account per (owner, loan).

    Args:
        user_id: The owning user's id.
        loan_account_id: The loan ``budget.accounts`` id whose per-loan
            account is sought.
        kind_id: The ``ref.ledger_account_kinds`` PK of the loan kind
            (``loan_interest`` / ``loan_escrow`` / ``loan_refund`` /
            ``equity_opening``).

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

    Resolves the ``budget.accounts`` row through the shared tenancy-filtered
    loader (:func:`._common.load_owned_account`), then guards that the account
    is an amortizing loan (``classify_account == AMORTIZING``, which reads the
    ``has_amortization`` boolean -- never a type name string).  This is the
    load-bearing companion to the kind guard in
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
        ValueError: If no account with that id is owned by ``user_id`` (from
            the shared loader), or if the account is not an amortizing loan.
            A live caller (the Step-4 poster) only ever resolves a settled
            loan payment's loan account, so a miss or a non-loan account
            signals a caller bug; fail loud with the offending id and the
            account's actual projection kind.
    """
    loan = load_owned_account(user_id, loan_account_id, "a loan")
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
    """Ensure a loan's per-kind interest / escrow / refund / opening account exists.

    The Build-Order Step 4 chart resolver: a confirmed loan payment's real-split
    correction books its accrued interest into the loan's ``loan_interest``
    Expense account, its configured escrow into the ``loan_escrow`` Expense
    account, and any payoff overpayment into the ``loan_refund`` Asset account;
    the loan read switch (Step 4, second half) additionally books the loan's
    origination balance into its ``equity_opening`` Equity account (the credit
    counter-leg of the once-per-loan opening entry).  This lazily materialises
    (and thereafter reuses) the requested one.

    The accounting class is derived from ``kind`` (``loan_interest`` /
    ``loan_escrow`` -> Expense; ``loan_refund`` -> Asset; ``equity_opening`` ->
    Equity) via ``_LOAN_LEDGER_KINDS`` -- the caller passes only the kind, so
    the class can never be set inconsistently with it.  ``kind`` MUST be one of
    the four loan kinds; any other (``linked`` / ``category`` / ``fallback`` /
    ``orphan``) is rejected before any write.  That guard, and the
    amortizing-loan guard in :func:`_load_amortizing_loan_account`, are
    load-bearing rather than belt-and-suspenders: the shipped
    ``ck_ledger_accounts_loan_shape`` CHECK is columns-only (it cannot pin
    ``kind_id`` without subquerying ``ref`` or hardcoding its IDs), so this
    resolver is the only thing keeping a per-loan row's kind a loan kind and
    its ``loan_account_id`` a real loan (the same un-CHECKed trust contract
    ``class_id`` already carries).

    Idempotent: an existing row for the ``(user, loan, kind)`` natural key is
    returned unchanged (the ``uq_ledger_accounts_loan`` partial unique would
    otherwise reject a duplicate).  The created row sets ``loan_account_id``,
    leaves ``account_id`` / ``category_id`` NULL and ``is_fallback`` False (the
    per-loan column shape ``ck_ledger_accounts_loan_shape`` requires), and
    snapshots a display ``name`` (``"<loan name> -- Interest|Escrow|Refund|Opening"``)
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
            ``LOAN_INTEREST``, ``LOAN_ESCROW``, ``LOAN_REFUND``, or
            ``EQUITY_OPENING``.

    Returns:
        The :class:`~app.models.ledger_account.LedgerAccount` for the
        ``(user, loan, kind)`` key (existing, or newly created and flushed).

    Raises:
        ValueError: If ``kind`` is not one of the four loan kinds, or (on
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
    name = f"{loan.name} -- {component}"[:LEDGER_ACCOUNT_NAME_MAX_LEN]
    ledger_account = add_or_reuse(
        LedgerAccount(
            user_id=user_id,
            class_id=class_id,
            kind_id=kind_id,
            loan_account_id=loan_account_id,
            name=name,
        ),
        lambda: _find_existing_loan_ledger_account(
            user_id, loan_account_id, kind_id,
        ),
    )
    # "Resolved", not "Created" -- see the note on the category resolver: a
    # lost natural-key race returns a row this call did not create.
    logger.info(
        "Resolved loan %s ledger account id=%d (user_id=%d, "
        "loan_account_id=%d, class_id=%d, kind_id=%d)",
        component, ledger_account.id, user_id, loan_account_id,
        class_id, kind_id,
    )
    return ledger_account
