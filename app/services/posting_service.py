"""
Shekel Budget App -- Posting Service

The sole writer of the append-only double-entry posting ledger
(``budget.journal_entries`` + ``budget.account_postings``, Build-Order
Step 2; see :mod:`app.models.journal_entry`).  Step 2 pilots the mechanism
on settled transfers; later Build-Order steps add cash, loan, and paycheck
sources by calling the same private balanced-write path
(:func:`_emit_balanced_entry`), so an unbalanced entry can never be written
from any source.

**Flask-isolated** (``CLAUDE.md`` Architecture rule): this service takes
plain data / ORM objects, returns ORM objects or plain values, and never
imports ``request`` / ``session``.  It **flushes but never commits** -- the
caller (the transfer service in Commit 5, a test, or a future source
writer) owns the transaction boundary.

**Reconcile-to-target, not append-blindly.**  :func:`sync_transfer_postings`
makes the ledger's NET posted effect for a transfer equal a single target
(the transfer's settled effect, or zero), by emitting ONE balanced delta
entry for the difference between the target and what is already posted.  That
one design is idempotent and covers every transfer lifecycle path -- settle,
revert, archive, cancel, delete, restore -- through a single call:

* a repeat sync computes ``delta = 0`` and writes nothing (no double-post);
* a revert / delete reverses *exactly what was posted* (read back from the
  ledger), so an amount edited while Projected and re-settled posts the new
  amount and nothing stale survives.

**The signed amount is debit-positive and class-independent.**  The *from*
account's leg is ``-amount`` (a credit: money leaving) and the *to*
account's leg is ``+amount`` (a debit: money entering), so the entry sums to
zero whether a leg lands on an asset or a liability ledger account.  The
builder never branches on account class (see the
:mod:`app.models.journal_entry` module docstring).

**The amount is the SHADOW's effective amount, not ``transfers.amount``.**
A settled transfer's effect is read as the income shadow's
``COALESCE(actual_amount, estimated_amount)`` -- the exact value the balance
calculator and the Commit-3 historical backfill use, and the value the
Commit-6 reconciliation oracle reconciles against.  The two differ when a
shadow carries an ``actual_amount`` (the grid shadow-edit path forwards one
through ``transfer_service.update_transfer``); posting ``transfers.amount``
instead would silently desynchronise the go-forward postings from both the
backfill and the oracle.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import case

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    PostingKindEnum,
    PostingSourceEnum,
    TxnTypeEnum,
)
from app.exceptions import ShekelError
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import ledger_account_service
from app.utils.balance_predicates import settled_status_ids

logger = logging.getLogger(__name__)

# A double-entry journal entry has at least two legs (one debit, one credit).
# Mirrors the ``COUNT(*) >= 2`` half of the deferred balanced-journal trigger
# (``app.posting_infrastructure``); named so the service-side backstop and the
# DB backstop read as the same rule.
_MIN_POSTING_LEGS = 2

# ``budget.journal_entries.description`` is ``VARCHAR(200)``.  The human label
# is truncated to fit, mirroring the historical backfill's ``LEFT(..., 200)``
# so the go-forward and backfilled entries carry identically-shaped
# descriptions.
_MAX_DESCRIPTION_LENGTH = 200


class PostingError(ShekelError):
    """A posting-ledger invariant was violated and the write was refused.

    Raised for the should-never-happen data-integrity failures this service
    guards (a real account with no paired ledger account; a settled transfer
    with no active income shadow; a caller-supplied set of legs that does not
    balance; a ``None`` scenario in a reconciliation helper).  These are not
    user-input errors -- the chart-of-accounts pairing and the two-shadow
    transfer invariant are guaranteed upstream -- so a violation here means a
    broken invariant that must fail loudly rather than post a wrong or
    unbalanced entry.
    """


@dataclass(frozen=True)
class _PostingLeg:
    """One signed leg to write into a balanced journal entry.

    The unit the shared balanced-write path (:func:`_emit_balanced_entry`)
    consumes, so the transfer lifecycle here and every future source type
    (cash, loan, paycheck in later Build-Order steps) describe their legs the
    same way.  ``amount`` is debit-positive / credit-negative; see the module
    docstring for the sign convention.

    Attributes:
        ledger_account_id: ``budget.ledger_accounts.id`` the leg lands in.
        amount: The signed leg amount (``Decimal``); non-zero (a zero leg is
            refused by ``ck_account_postings_amount_nonzero``).
        posting_kind_id: ``ref.posting_kinds.id`` for the leg's economic
            nature (``transfer`` in Step 2).
    """

    ledger_account_id: int
    amount: Decimal
    posting_kind_id: int


# ── Private helpers ────────────────────────────────────────────────


def _utc_civil_date(instant: datetime) -> date:
    """Return the UTC calendar date of a stored instant.

    The Python counterpart of the historical backfill's
    ``(paid_at AT TIME ZONE 'UTC')::date``: a transfer's settle date is the
    civil date of its ``paid_at`` in UTC, the app's storage convention, NOT
    the display timezone (``app.utils.dates.to_display_date`` would shift a
    late-evening Eastern settle onto the wrong day and diverge from the
    backfill).

    Args:
        instant: A stored ``paid_at`` instant.  Timezone-aware values are
            converted to UTC; a naive value is assumed UTC (every
            ``timestamptz`` in this app is stored UTC).

    Returns:
        The UTC calendar date of *instant*.
    """
    if instant.tzinfo is None:
        return instant.date()
    return instant.astimezone(timezone.utc).date()


def _civil_settle_date(paid_at: datetime | None, pay_period: PayPeriod) -> date:
    """Return the UTC civil date of a settle ``paid_at``, or the period start.

    The shared tail of the transfer and transaction entry-date helpers
    (:func:`_entry_date`, :func:`_transaction_entry_date`): a recorded
    ``paid_at`` maps to its UTC civil date (the storage-timezone date, NOT the
    display timezone -- see :func:`_utc_civil_date`); a NULL ``paid_at`` (a
    historical settle recorded before the ``paid_at`` sync, or a reverted row
    whose timestamp was cleared) falls back to the pay period's ``start_date``.
    ``journal_entries.entry_date`` is NOT NULL, so the fallback is load-bearing.
    Mirrors the historical backfill's
    ``COALESCE((paid_at AT TIME ZONE 'UTC')::date, start_date)``.

    Args:
        paid_at: The settle instant read back from the source row, or None.
        pay_period: The source row's pay period (supplies ``start_date``).

    Returns:
        The UTC civil settle date, or the pay period's ``start_date``.
    """
    if paid_at is not None:
        return _utc_civil_date(paid_at)
    return pay_period.start_date


def _ledger_account_for(account_id: int) -> LedgerAccount:
    """Return the ledger account paired with a real account, or fail loudly.

    Every ``budget.accounts`` row has exactly one linked ledger account (the
    Commit-2 create hook pairs new accounts; the Commit-2 backfill paired
    historical ones; ``uq_ledger_accounts_account`` permits only one).  A
    missing pairing is a broken chart-of-accounts invariant, not a benign
    lookup miss, so this raises rather than returning ``None``.

    Args:
        account_id: The real account whose linked ledger account to load.

    Returns:
        The linked :class:`~app.models.ledger_account.LedgerAccount`.

    Raises:
        PostingError: If no ledger account is linked to *account_id*.
    """
    ledger = (
        db.session.query(LedgerAccount)
        .filter_by(account_id=account_id)
        .one_or_none()
    )
    if ledger is None:
        raise PostingError(
            f"No ledger account is linked to account {account_id}; the "
            f"chart-of-accounts pairing is missing (every account is paired "
            f"by the account-create hook or the Step-2 backfill)."
        )
    return ledger


def _posted_net(transfer_id: int, ledger_account_id: int) -> Decimal:
    """Return the net of a transfer's posted legs on one ledger account.

    Sums ``account_postings.amount`` across every journal entry linked to
    *transfer_id* whose leg lands in *ledger_account_id*.  This is the
    "current" value the reconcile-to-target math compares the target against:
    for the to-account ledger it is ``+effect`` after a settle, ``0`` after a
    matching reversal.  Reads the posted amount back from the ledger so a
    reversal negates exactly what was written, independent of any later edit
    to the source transfer.

    Args:
        transfer_id: The source transfer whose entries to sum.
        ledger_account_id: The ledger account whose legs to sum.

    Returns:
        The signed net as a ``Decimal`` (``Decimal("0")`` when nothing is
        posted yet).
    """
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            JournalEntry.transfer_id == transfer_id,
            Posting.ledger_account_id == ledger_account_id,
        )
        .scalar()
    )


def _settle_effective(xfer: Transfer) -> Decimal:
    """Return the effective amount entering the transfer's to-account.

    The income shadow lives on the to-account (``_build_shadow`` in
    ``transfer_service``); its effective amount is
    ``COALESCE(actual_amount, estimated_amount)`` -- the money that actually
    moved, the value the balance calculator and the reconciliation oracle
    use.  ``settled`` callers pass a settled status, so the shadow is
    non-excluded and ``COALESCE`` is its effective contribution (matching the
    Commit-3 backfill's ``COALESCE`` on the same shadow).

    Args:
        xfer: The transfer being posted.

    Returns:
        The income shadow's effective amount as a ``Decimal``.

    Raises:
        PostingError: If the transfer has no active income shadow on its
            to-account (a Transfer-Invariant-1 violation -- a settled
            transfer must have its two shadows).
    """
    effective = (
        db.session.query(
            db.func.coalesce(
                Transaction.actual_amount, Transaction.estimated_amount
            )
        )
        .filter(
            Transaction.transfer_id == xfer.id,
            Transaction.account_id == xfer.to_account_id,
            Transaction.is_deleted.is_(False),
        )
        .scalar()
    )
    if effective is None:
        raise PostingError(
            f"Transfer {xfer.id} has no active income shadow on account "
            f"{xfer.to_account_id}; cannot post its settled effect."
        )
    return effective


def _entry_date(xfer: Transfer) -> date:
    """Return the civil date to stamp on a transfer's journal entry.

    The UTC civil date of the transfer's ``paid_at`` (which lives on the
    shadows -- the ``Transfer`` model has none), falling back to the pay
    period's ``start_date`` when ``paid_at`` is NULL (a historical settle
    recorded before the ``paid_at`` sync, or a reverted transfer whose
    ``paid_at`` was cleared).  ``entry_date`` is NOT NULL, so the fallback is
    load-bearing.  Mirrors the Commit-3 backfill's
    ``COALESCE((paid_at AT TIME ZONE 'UTC')::date, start_date)``.

    The query auto-flushes before reading, so a ``paid_at`` the caller set to
    a server-side ``db.func.now()`` (the ``mark_done`` path) is materialised
    and read back as a concrete timestamp rather than an unresolved SQL
    expression.

    Args:
        xfer: The transfer being posted.

    Returns:
        The UTC civil settle date, or the pay period's ``start_date`` when no
        ``paid_at`` is recorded.
    """
    # Read the to-account (income) shadow's paid_at.  The Commit-3 backfill
    # reads the from-account (expense) shadow's paid_at instead; the two are
    # always equal because the transfer service mirrors paid_at to both
    # shadows (Transfer Invariant 3), so the entry date is identical either
    # way.
    paid_at = (
        db.session.query(Transaction.paid_at)
        .filter(
            Transaction.transfer_id == xfer.id,
            Transaction.account_id == xfer.to_account_id,
            Transaction.is_deleted.is_(False),
        )
        .scalar()
    )
    return _civil_settle_date(paid_at, xfer.pay_period)


def _transfer_description(xfer: Transfer) -> str:
    """Return the human label for a transfer's journal entry.

    ``"Transfer: <from> to <to>"``, truncated to the description column
    width, matching the Commit-3 backfill byte-for-byte.  Display only --
    never read for logic.

    Args:
        xfer: The transfer being posted (its ``from_account`` / ``to_account``
            relationships supply the names).

    Returns:
        The truncated description string.
    """
    return (
        f"Transfer: {xfer.from_account.name} to {xfer.to_account.name}"
    )[:_MAX_DESCRIPTION_LENGTH]


def _emit_balanced_entry(
    entry: JournalEntry, legs: list[_PostingLeg]
) -> JournalEntry:
    """Persist a journal entry and its legs, enforcing the balanced invariant.

    The single balanced-write path every posting source shares (Step 2's
    transfers; cash / loan / paycheck in later steps).  Validates the two
    cross-row invariants the deferred ``ck_account_postings_balanced`` trigger
    enforces -- at least two legs, and legs summing to zero -- BEFORE the
    write, so an unbalanced entry fails loudly at the call site with a clear
    message instead of as an opaque deferred error at COMMIT.  The service is
    the first backstop; the DB trigger is the second (the house "service + DB
    backstop" pattern).

    Adds the entry with its legs via the ``postings`` relationship cascade
    (one flush assigns the entry id and inserts the legs with their FK) and
    flushes so the caller sees assigned ids.  Does NOT commit.

    Args:
        entry: The unsaved :class:`~app.models.journal_entry.JournalEntry`
            header, with every column already set by the caller.
        legs: The :class:`_PostingLeg` list to attach; balanced by
            construction for transfers.

    Returns:
        The persisted *entry* (flushed, with ``id`` and ``postings`` set).

    Raises:
        PostingError: If *legs* has fewer than two entries or does not sum
            to zero.
    """
    if len(legs) < _MIN_POSTING_LEGS:
        raise PostingError(
            f"A journal entry needs at least {_MIN_POSTING_LEGS} legs; "
            f"got {len(legs)}."
        )
    total = sum((leg.amount for leg in legs), Decimal("0"))
    if total != 0:
        raise PostingError(
            f"Journal entry legs must sum to 0 (debit-positive double "
            f"entry); got {total}."
        )

    db.session.add(entry)
    for leg in legs:
        entry.postings.append(
            Posting(
                ledger_account_id=leg.ledger_account_id,
                amount=leg.amount,
                posting_kind_id=leg.posting_kind_id,
            )
        )
    db.session.flush()
    return entry


# ── Transaction (cash) posting helpers (Build-Order Step 3) ────────


def _transaction_entry_date(txn: Transaction) -> date:
    """Return the civil date to stamp on a transaction's journal entry.

    The UTC civil date of the transaction's ``paid_at``, falling back to the
    pay period's ``start_date`` when ``paid_at`` is NULL (a historical settle,
    or a reverted transaction whose ``paid_at`` was cleared).  The transaction
    analog of :func:`_entry_date`, sharing :func:`_civil_settle_date`.

    ``paid_at`` is read back via a query (not off the ORM attribute) so a value
    the caller set to a server-side ``db.func.now()`` (the ``mark_done`` path)
    is auto-flushed and materialised to a concrete timestamp rather than read
    as an unresolved SQL expression -- the same reason :func:`_entry_date`
    queries the shadow's ``paid_at``.

    Args:
        txn: The transaction being posted (must be flushed, ``txn.id`` set).

    Returns:
        The UTC civil settle date, or the pay period's ``start_date`` when no
        ``paid_at`` is recorded.
    """
    paid_at = (
        db.session.query(Transaction.paid_at)
        .filter(Transaction.id == txn.id)
        .scalar()
    )
    return _civil_settle_date(paid_at, txn.pay_period)


def _credit_entry_sum(txn: Transaction) -> Decimal:
    """Return the sum of a transaction's credit (credit-card) entry amounts.

    The ``Sigma(credit entry amounts)`` term of the confirmed-cash-effect
    formula (plan Section 1): an envelope's credit purchases are excluded from
    the checking outflow because each posts its own CC Payback when that
    payback settles (``credit_workflow``), so counting them here would
    double-count against the payback.  A plain transaction has no entries, so
    this is ``Decimal("0")`` and the effect collapses to ``effective_amount``.

    Summed over the loaded ``entries`` relationship (the go-forward poster
    already holds the transaction); the bulk oracle reader
    :func:`settled_transaction_effect` computes the same sum in SQL.

    Args:
        txn: The transaction whose credit entries to sum.

    Returns:
        The sum of ``amount`` over the transaction's ``is_credit`` entries, as
        a ``Decimal`` (``Decimal("0")`` when there are none).
    """
    return sum(
        (entry.amount for entry in txn.entries if entry.is_credit),
        Decimal("0"),
    )


def _signed_cash_leg(txn: Transaction) -> Decimal:
    """Return the debit-positive cash-account leg for a settled transaction.

    The plan's one formula (Section 1): the confirmed cash effect is
    ``effective_amount - Sigma(credit entry amounts)``, signed ``+`` for income
    (a debit: money entering the cash account) and ``-`` for an expense (a
    credit: money leaving).  The sign follows the transaction *type*, never the
    account class, so the leg is correct whether the cash account is an asset
    (Checking) or a liability (a direct charge on a Credit Card account) --
    identical to the transfer sign convention (see the module docstring).

    For a plain transaction the credit-entry sum is zero, so the leg is
    ``+/-effective_amount``.  For an envelope at settle ``effective_amount``
    equals the sum of ALL entries (``compute_actual_from_entries`` set
    ``actual_amount`` so), so ``effective - Sigma(credit)`` collapses to the sum
    of the DEBIT entries -- the debit-only checking outflow (plan Decision D2),
    with no branch on "is this an envelope".

    Args:
        txn: The settled transaction whose cash leg to compute.  The caller
            posts only a settled, non-excluded row, so ``effective_amount`` is
            its confirmed amount (not the zero an excluded/deleted row returns).

    Returns:
        The signed, debit-positive cash-account leg amount as a ``Decimal``.
    """
    net = txn.effective_amount - _credit_entry_sum(txn)
    return net if txn.is_income else -net


def _posted_net_by_account(transaction_id: int) -> dict[int, Decimal]:
    """Return the net posted amount per ledger account for a transaction.

    Sums ``account_postings.amount`` across every journal entry linked to
    *transaction_id*, grouped by ledger account: ``{ledger_account_id: net}``.
    This is the "already posted" side of the reconcile-to-target math in
    :func:`sync_transaction_postings`, read straight from the ledger so a
    reversal negates EXACTLY what was posted and -- crucially -- reverses the
    accounts the transaction ACTUALLY posted to (e.g. the old category before a
    recategorize), never accounts recomputed from the current
    ``txn.category_id`` (the plan Section 2.8 CRITICAL).  The multi-account
    analog of the single-account :func:`_posted_net` the transfer path uses (a
    transfer always reconciles one to-account ledger; a transaction's
    counter-leg can move between category accounts).

    Args:
        transaction_id: The source transaction whose posted legs to sum.

    Returns:
        ``{ledger_account_id: net Decimal}`` over the transaction's posted
        legs; empty when nothing is posted yet.  A fully-reversed account
        appears with a ``Decimal("0")`` net (its delta then drops out).
    """
    rows = (
        db.session.query(
            Posting.ledger_account_id,
            db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(JournalEntry.transaction_id == transaction_id)
        .group_by(Posting.ledger_account_id)
        .all()
    )
    return dict(rows)


def _settled_target(txn: Transaction, owner_id: int) -> dict[int, Decimal]:
    """Return the debit-positive ledger target for a SETTLED transaction.

    The two-account map the ledger should net to once *txn* is settled:
    ``{cash_ledger_id: cash_leg, category_ledger_id: -cash_leg}``, summing to
    zero by construction.  ``cash_leg`` is :func:`_signed_cash_leg`; the cash
    account is the transaction's linked ledger account
    (:func:`_ledger_account_for`); the counter account is the per-category
    Income/Expense ledger account (or the per-(owner, class) Uncategorized
    fallback when ``category_id`` is NULL), lazily resolved by
    ``ledger_account_service``.  The accounting class is derived from the
    transaction *type* (Income vs Expense).

    Resolved only on the settle side; a revert / delete passes an empty target
    and reverses whatever :func:`_posted_net_by_account` reports, so this never
    creates a category ledger account for a transaction being unwound.

    Args:
        txn: The settled transaction.  ``account_id`` and
            ``transaction_type_id`` are immutable, so the cash account and the
            class are stable across the transaction's life.
        owner_id: The owning user's id (``txn.pay_period.user_id`` -- a
            ``Transaction`` has no ``user_id``), the category account's owner.

    Returns:
        ``{cash_ledger_id: cash_leg, category_ledger_id: -cash_leg}``.

    Raises:
        PostingError: If the transaction's account has no linked ledger
            account.
        ValueError: Propagated from the resolver if a non-NULL ``category_id``
            names no category owned by ``owner_id``.
    """
    cash_ledger = _ledger_account_for(txn.account_id)
    ledger_class = (
        LedgerAccountClassEnum.INCOME if txn.is_income
        else LedgerAccountClassEnum.EXPENSE
    )
    category_ledger = ledger_account_service.get_or_create_category_ledger_account(
        owner_id, txn.category_id, ledger_class,
    )
    cash_leg = _signed_cash_leg(txn)
    return {cash_ledger.id: cash_leg, category_ledger.id: -cash_leg}


# ── Public API ─────────────────────────────────────────────────────


def sync_transfer_postings(
    xfer: Transfer, *, settled: bool
) -> JournalEntry | None:
    """Reconcile a transfer's posted ledger effect to its target, idempotently.

    Ensures the NET amount posted for *xfer* on its to-account ledger equals
    the target (the transfer's settled effective amount when *settled*, else
    zero) by emitting ONE balanced delta journal entry for the difference
    between the target and what is already posted.  A no-op (returns ``None``)
    when the ledger is already at target.  See the module docstring for the
    reconcile-to-target rationale and the debit-positive sign convention.

    Every transfer lifecycle path is one call to this function:

    ========================================  ========  ====================
    Transition / action                       settled   Net effect
    ========================================  ========  ====================
    projected -> done (mark done)             True      post +effective
    done -> projected (revert)                False     reverse to zero
    done -> settled (archive)                 True      no-op (at target)
    projected -> cancelled                    False     no-op (target 0)
    delete of a settled transfer              False     reverse to zero
    restore of a settled, soft-deleted xfer   True      re-post +effective
    ========================================  ========  ====================

    The target's magnitude is the income shadow's effective amount (read
    fresh each call), so a revert -> edit-amount -> re-settle sequence posts
    the new amount.  The reversal's magnitude is read back from the ledger
    (``_posted_net``), so it negates exactly what was posted regardless of any
    later edit to *xfer*.  Idempotency rests on this delta math plus the
    transfer's ``version_id`` optimistic lock (a concurrent double mark-done
    collides on the version and surfaces as a 409); a repeat sync sees
    ``delta == 0`` and writes nothing.

    Flushes but does not commit (the caller owns the transaction).

    Args:
        xfer: The transfer to reconcile.  Must be flushed (``xfer.id`` set)
            with its two shadows present.
        settled: Whether the transfer's confirmed effect should be posted
            (its ``is_settled`` truth for the lifecycle action).  The caller
            passes ``False`` for revert / cancel / delete even when the row's
            status is still settled, so the effect is reversed.

    Returns:
        The new delta :class:`~app.models.journal_entry.JournalEntry`, or
        ``None`` when the ledger is already at target (an idempotent no-op).

    Raises:
        PostingError: If a from/to ledger-account pairing is missing, or
            (when *settled*) the income shadow is absent.
    """
    from_ledger = _ledger_account_for(xfer.from_account_id)
    to_ledger = _ledger_account_for(xfer.to_account_id)

    # The to-account ledger should net to the money entering it (when
    # settled) or to zero (when not).  The from-account ledger mirrors it by
    # construction, so one scalar describes the whole entry.
    target = _settle_effective(xfer) if settled else Decimal("0")
    current = _posted_net(xfer.id, to_ledger.id)
    delta = target - current
    if delta == 0:
        # Already at target: settling an already-posted transfer, reverting an
        # already-reversed one, cancelling a never-posted one.  No entry.
        return None

    transfer_kind_id = ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
    # from leg: money leaving the from-account -> a credit -> negative.
    # to leg:   money entering the to-account  -> a debit  -> positive.
    # Sum is (-delta) + (+delta) = 0, balanced by construction.
    legs = [
        _PostingLeg(from_ledger.id, -delta, transfer_kind_id),
        _PostingLeg(to_ledger.id, delta, transfer_kind_id),
    ]
    entry = JournalEntry(
        user_id=xfer.user_id,
        scenario_id=xfer.scenario_id,
        pay_period_id=xfer.pay_period_id,
        entry_date=_entry_date(xfer),
        source_kind_id=ref_cache.posting_source_id(PostingSourceEnum.TRANSFER),
        transfer_id=xfer.id,
        description=_transfer_description(xfer),
    )
    _emit_balanced_entry(entry, legs)
    logger.info(
        "Posted transfer %d ledger delta %s (settled=%s) as journal entry %d",
        xfer.id, delta, settled, entry.id,
    )
    return entry


def sync_transaction_postings(
    txn: Transaction, *, settled: bool
) -> JournalEntry | None:
    """Reconcile a transaction's posted ledger effect to its target, idempotently.

    The ordinary-transaction analog of :func:`sync_transfer_postings`: ensures
    the NET amount posted for *txn* equals its target -- the settled
    debit-positive split ``{cash_ledger: cash_leg, category_ledger: -cash_leg}``
    when *settled*, or nothing when not -- by emitting ONE balanced delta
    journal entry, then a no-op (returns ``None``) on any repeat.  See the
    module docstring for the reconcile-to-target rationale and the
    debit-positive sign convention; see :func:`_signed_cash_leg` for the
    ``effective - Sigma(credit)`` cash-effect formula.

    **Reconciles over the accounts the transaction has ALREADY posted to**, read
    from the ledger by ``transaction_id`` (:func:`_posted_net_by_account`),
    unioned with the target accounts -- NOT a single fixed pair.  This is what
    makes a revert-and-recategorize correct: the reversal lands on the OLD
    category (the one in the ledger), not the new ``txn.category_id`` (plan
    Section 2.8 CRITICAL).  For every account whose ``target - posted`` net is
    non-zero it writes one delta leg; those non-zero deltas always sum to zero
    (the target sums to zero, and the posted side sums to zero because every
    prior entry balanced), so the emitted entry is balanced and has >= 2 legs
    by construction -- :func:`_emit_balanced_entry` never sees a single leg.

    Every ordinary-transaction lifecycle action is one call to this function:

    =========================================  ========  ====================
    Action                                     settled   Net effect
    =========================================  ========  ====================
    projected -> paid/received (mark done)     True      post the cash split
    paid/received -> projected (revert)        False     reverse to zero
    edit amount / category while settled       True      post the delta
    cancel / delete of a settled transaction   False     reverse to zero
    repeat sync at the same target             either    no-op
    =========================================  ========  ====================

    A transfer shadow (``transfer_id`` set) is a no-op: Step 2 owns transfer
    postings, which link by ``transfer_id`` (this reads / writes the
    ``transaction_id`` linkage), so the guard keeps a shadow from being
    double-posted as an ordinary transaction.  Idempotency rests on the delta
    math plus the transaction's ``version_id`` optimistic lock (a concurrent
    double mark-done collides on the version, surfacing as a 409).

    Flushes but does not commit (the caller owns the transaction).

    Args:
        txn: The transaction to reconcile.  Must be flushed (``txn.id`` set).
            Its ``account_id`` and ``transaction_type_id`` are immutable, so
            the cash account and the income/expense sign are stable; its
            ``category_id`` may have changed, which the over-posted-accounts
            reconcile handles.
        settled: Whether the transaction's confirmed effect should be posted
            (its ``is_settled`` truth for the action).  The caller passes
            ``False`` for revert / cancel / delete even when the row's status
            is still settled, so the effect is reversed.

    Returns:
        The new delta :class:`~app.models.journal_entry.JournalEntry`, or
        ``None`` when the ledger is already at target (an idempotent no-op).

    Raises:
        PostingError: If the transaction's account (or, when *settled*, its
            resolved category account) has no ledger account.
    """
    # A transfer shadow is Step 2's responsibility and links by transfer_id, not
    # transaction_id.  No production path hands a shadow here (transaction
    # handlers act on the primary row; transfers go through transfer_service), so
    # this is defense-in-depth -- but a settled shadow that slipped through would
    # otherwise be given a second, transaction-sourced entry and double-counted
    # against the transfer posting, so the guard stays.
    if txn.transfer_id is not None:
        return None

    owner_id = txn.pay_period.user_id
    posted = _posted_net_by_account(txn.id)
    target = _settled_target(txn, owner_id) if settled else {}

    deltas = {
        ledger_id: target.get(ledger_id, Decimal("0"))
        - posted.get(ledger_id, Decimal("0"))
        for ledger_id in set(target) | set(posted)
    }
    deltas = {
        ledger_id: amount
        for ledger_id, amount in deltas.items()
        if amount != 0
    }
    if not deltas:
        # Already at target: a repeat settle, an already-reversed revert, a
        # cancel of a never-posted row, or an all-credit envelope (cash_leg 0).
        return None

    # Both legs of an ordinary-transaction entry carry the same kind, by the
    # transaction type (mirrors Step 2, where both transfer legs are
    # ``transfer``); no Step-3 reader differentiates per-leg kind.
    kind_id = ref_cache.posting_kind_id(
        PostingKindEnum.INCOME if txn.is_income else PostingKindEnum.EXPENSE
    )
    # Sorted for a deterministic, stable leg order (the sum is order-
    # independent, but a stable order keeps logs and stored rows predictable).
    legs = [
        _PostingLeg(ledger_id, amount, kind_id)
        for ledger_id, amount in sorted(deltas.items())
    ]
    entry = JournalEntry(
        user_id=owner_id,
        scenario_id=txn.scenario_id,
        pay_period_id=txn.pay_period_id,
        entry_date=_transaction_entry_date(txn),
        source_kind_id=ref_cache.posting_source_id(
            PostingSourceEnum.TRANSACTION
        ),
        transaction_id=txn.id,
        description=txn.name[:_MAX_DESCRIPTION_LENGTH],
    )
    _emit_balanced_entry(entry, legs)
    logger.info(
        "Posted transaction %d ledger deltas %s (settled=%s) as journal "
        "entry %d",
        txn.id, dict(sorted(deltas.items())), settled, entry.id,
    )
    return entry


def account_posting_total(account_id: int, scenario_id: int) -> Decimal:
    """Return the net of all posting legs on an account's ledger in a scenario.

    Sums ``account_postings.amount`` over the account's linked ledger account
    for journal entries in *scenario_id* (the ``scenario_id`` denorm on the
    entry keeps scenarios isolated).  This is the ledger side of the Commit-6
    reconciliation oracle; it equals :func:`settled_transfer_effect` for the
    same account and scenario when the ledger is in sync.

    Args:
        account_id: The real account whose ledger postings to sum.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net of the account's posting legs as a ``Decimal``.

    Raises:
        PostingError: If *scenario_id* is ``None`` (a scenario is required to
            isolate the sum), or the account has no linked ledger account.
    """
    if scenario_id is None:
        raise PostingError(
            "account_posting_total requires a scenario_id (postings are "
            "scenario-scoped); got None."
        )
    ledger = _ledger_account_for(account_id)
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == ledger.id,
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def settled_transfer_effect(account_id: int, scenario_id: int) -> Decimal:
    """Return an account's net effect from its settled transfer shadows.

    The balance-side expectation the Commit-6 oracle reconciles the ledger
    against: over the account's settled (``status.is_settled``), non-deleted
    transfer shadows in *scenario_id*, sum ``+effective_amount`` for an income
    shadow (money in) and ``-effective_amount`` for an expense shadow (money
    out) -- exactly the debit-positive net :func:`account_posting_total`
    accumulates.  ``effective_amount`` is ``COALESCE(actual, estimated)``;
    settled statuses are non-excluded by construction (``settled_status_ids``
    is disjoint from the balance-excluded set), so no excluded-status guard is
    needed.

    Args:
        account_id: The real account whose settled transfer shadows to sum.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net effect of the account's settled transfer shadows as a
        ``Decimal``.

    Raises:
        PostingError: If *scenario_id* is ``None``.
    """
    if scenario_id is None:
        raise PostingError(
            "settled_transfer_effect requires a scenario_id (transactions "
            "are scenario-scoped); got None."
        )
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    effective = db.func.coalesce(
        Transaction.actual_amount, Transaction.estimated_amount
    )
    signed_effect = case(
        (Transaction.transaction_type_id == income_type_id, effective),
        else_=-effective,
    )
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(signed_effect), Decimal("0"))
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.transfer_id.isnot(None),
            Transaction.is_deleted.is_(False),
            Transaction.status_id.in_(settled_status_ids()),
        )
        .scalar()
    )


def settled_transaction_effect(account_id: int, scenario_id: int) -> Decimal:
    """Return an account's net effect from its settled ordinary transactions.

    The transaction analog of :func:`settled_transfer_effect`, and the
    balance-side expectation the Build-Order Step 3 reconciliation oracle
    reconciles the ledger against: over the account's settled
    (``status.is_settled``), non-deleted, NON-transfer (``transfer_id IS
    NULL``) transactions in *scenario_id*, sum the signed confirmed cash effect
    ``effective - Sigma(credit entries)`` -- ``+`` for income (money in), ``-``
    for an expense (money out) -- exactly the debit-positive net the cash legs
    accumulate via :func:`account_posting_total`.  ``effective`` is
    ``COALESCE(actual, estimated)``; the per-transaction credit-entry sum is a
    correlated subquery (the SQL counterpart of the go-forward
    :func:`_credit_entry_sum`).  Settled statuses are non-excluded by
    construction (``settled_status_ids`` is disjoint from the balance-excluded
    set), so no excluded-status guard is needed.

    For a real account A, ``account_posting_total(A) ==
    settled_transfer_effect(A) + settled_transaction_effect(A)`` once the
    ledger is in sync (the oracle's per-account invariant).

    Args:
        account_id: The real account whose settled transactions to sum.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net effect of the account's settled ordinary transactions
        as a ``Decimal``.

    Raises:
        PostingError: If *scenario_id* is ``None``.
    """
    if scenario_id is None:
        raise PostingError(
            "settled_transaction_effect requires a scenario_id (transactions "
            "are scenario-scoped); got None."
        )
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    effective = db.func.coalesce(
        Transaction.actual_amount, Transaction.estimated_amount
    )
    # Per-transaction sum of credit-card entry amounts, correlated to the outer
    # transaction so it excludes the credit portion exactly as the go-forward
    # ``_credit_entry_sum`` does (the CC Payback posts that portion separately).
    credit_sum = (
        db.session.query(
            db.func.coalesce(db.func.sum(TransactionEntry.amount), Decimal("0"))
        )
        .filter(
            TransactionEntry.transaction_id == Transaction.id,
            TransactionEntry.is_credit.is_(True),
        )
        .correlate(Transaction)
        .scalar_subquery()
    )
    cash_effect = effective - credit_sum
    signed_effect = case(
        (Transaction.transaction_type_id == income_type_id, cash_effect),
        else_=-cash_effect,
    )
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(signed_effect), Decimal("0"))
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.transfer_id.is_(None),
            Transaction.is_deleted.is_(False),
            Transaction.status_id.in_(settled_status_ids()),
        )
        .scalar()
    )
