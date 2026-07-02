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
(the transfer's settled effect, or zero), by emitting one balanced delta
entry PER PAY PERIOD for the difference between the target and what is
already posted there.  That one design is idempotent and covers every
transfer lifecycle path -- settle, revert, archive, cancel, delete, restore
-- through a single call:

* a repeat sync computes zero deltas and writes nothing (no double-post);
* a revert / delete reverses *exactly what was posted* (read back from the
  ledger), so an amount edited while Projected and re-settled posts the new
  amount and nothing stale survives.

**Corrections are attributed to what they correct** (the 2026-07-02
adversarial review's R2 rule): a reversal entry carries the PAY PERIOD of
the postings it reverses -- read back from the ledger per period, never the
source row's current period -- and inherits the latest ``entry_date`` it
reverses.  A revert-and-move PATCH therefore nets the ORIGINAL period to
zero instead of stamping the reversal into the new period, so a net-zero
pair never straddles periods (a later truncate of the new period cannot
strand half of it) and date-grouped reporting nets a reversal against the
entry it undoes.

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

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import ledger_account_service, posting_reads
from app.services.posting_reads import PostingError, _ledger_account_for

logger = logging.getLogger(__name__)

# Re-exported read-side API.  The reconciliation readers moved to
# :mod:`app.services.posting_reads` when this module crossed the size gate
# (the sibling-split convention); the ledger's one public surface stays HERE,
# so the oracles and the loan posting package keep reading them off the
# writer module.  ``PostingError`` / ``_ledger_account_for`` above are
# re-exports of the same kind (this module also uses them itself).
account_posting_total = posting_reads.account_posting_total
settled_transfer_effect = posting_reads.settled_transfer_effect
settled_transaction_effect = posting_reads.settled_transaction_effect

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


def _posted_by_period(source_filter) -> tuple[
    "dict[int, dict[int, Decimal]]", "dict[int, date]"
]:
    """Return a source's posted legs grouped by pay period, plus each period's date.

    The "already posted" side of the per-period reconcile both sync functions
    share: sums ``account_postings.amount`` across every journal entry matching
    *source_filter* (``JournalEntry.transfer_id == x`` for a transfer,
    ``JournalEntry.transaction_id == x`` for a transaction), grouped by the
    entry's ``pay_period_id`` and the leg's ledger account.  Reading the posted
    side back from the ledger -- per PERIOD, not just per account -- is what
    lets a reversal land in the period of the postings it reverses (the
    2026-07-02 adversarial review's R2 attribution rule): a source row whose
    ``pay_period_id`` later moved (the revert-and-move PATCH) reverses into its
    ORIGINAL period, so the net-zero pair never straddles periods and a later
    period truncate cannot strand half of it.

    Also returns each period's LATEST posted ``entry_date`` -- the date a
    reversal-only delta entry inherits, so a reversal nets against what it
    reverses in date-grouped reporting instead of taking the reversal-time
    fallback (the cross-year mis-statement class the loan tax reader had to
    work around per-reader).

    Args:
        source_filter: The SQLAlchemy filter expression selecting the source's
            journal entries (by ``transfer_id`` or ``transaction_id``).

    Returns:
        ``(posted, last_dates)`` -- ``{pay_period_id: {ledger_account_id: net
        Decimal}}`` over the source's posted legs (empty when nothing is posted
        yet; a fully-reversed account appears with a ``Decimal("0")`` net, its
        delta then dropping out), and ``{pay_period_id: latest entry_date}``.
    """
    rows = (
        db.session.query(
            JournalEntry.pay_period_id,
            Posting.ledger_account_id,
            db.func.sum(Posting.amount),
            db.func.max(JournalEntry.entry_date),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(source_filter)
        .group_by(JournalEntry.pay_period_id, Posting.ledger_account_id)
        .all()
    )
    posted: "dict[int, dict[int, Decimal]]" = {}
    last_dates: "dict[int, date]" = {}
    for period_id, ledger_id, net, last_date in rows:
        posted.setdefault(period_id, {})[ledger_id] = net
        if period_id not in last_dates or last_date > last_dates[period_id]:
            last_dates[period_id] = last_date
    return posted, last_dates


def _reconcile_periods(
    targets: "dict[int, dict[int, Decimal]]",
    posted: "dict[int, dict[int, Decimal]]",
    kind_id: int,
) -> "dict[int, list[_PostingLeg]]":
    """Return the balanced delta legs per pay period bringing *posted* to *targets*.

    The per-period core of the reconcile both sync functions share: for every
    period either side touches, the delta per ledger account is
    ``target - posted``; zero deltas drop.  Within one period the non-zero
    deltas always sum to zero -- a period's target sums to zero by construction,
    and its posted side sums to zero because every prior entry balanced and an
    entry lives in exactly one period -- so each period's legs form one balanced
    entry (never a single leg).

    Args:
        targets: ``{pay_period_id: {ledger_account_id: amount}}`` the ledger
            should net to (at most the source's current period; empty to
            reverse everything to zero).
        posted: The :func:`_posted_by_period` net map.
        kind_id: The posting kind stamped on every delta leg (both legs of a
            transfer / transaction entry carry the source's one kind).

    Returns:
        ``{pay_period_id: [_PostingLeg, ...]}`` for every period with a
        non-zero delta; empty when the ledger is already at target.
    """
    legs_by_period: "dict[int, list[_PostingLeg]]" = {}
    for period_id in sorted(set(targets) | set(posted)):
        period_target = targets.get(period_id, {})
        period_posted = posted.get(period_id, {})
        legs = [
            _PostingLeg(ledger_id, delta, kind_id)
            for ledger_id in sorted(set(period_target) | set(period_posted))
            if (
                delta := period_target.get(ledger_id, Decimal("0"))
                - period_posted.get(ledger_id, Decimal("0"))
            ) != 0
        ]
        if legs:
            legs_by_period[period_id] = legs
    return legs_by_period


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
) -> list[JournalEntry]:
    """Reconcile a transfer's posted ledger effect to its target, idempotently.

    Ensures the NET amount posted for *xfer* equals the target -- the
    transfer's settled effective amount in its CURRENT pay period when
    *settled*, else zero everywhere -- by emitting one balanced delta journal
    entry PER PAY PERIOD whose posted legs differ from the target
    (:func:`_reconcile_periods`).  A no-op (returns ``[]``) when the ledger is
    already at target.  See the module docstring for the reconcile-to-target
    rationale and the debit-positive sign convention.

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
    per period (:func:`_posted_by_period`), so it negates exactly what was
    posted regardless of any later edit to *xfer* -- and lands in the PERIOD
    of the postings it reverses, dated by the latest entry it reverses (the
    2026-07-02 adversarial review's R2 attribution rule): a revert-and-move
    PATCH therefore reverses into the ORIGINAL period, so the net-zero pair
    never straddles periods and a later period truncate cannot strand half of
    it.  Idempotency rests on this delta math plus the transfer's
    ``version_id`` optimistic lock (a concurrent double mark-done collides on
    the version and surfaces as a 409); a repeat sync sees zero deltas and
    writes nothing.

    Flushes but does not commit (the caller owns the transaction).

    Args:
        xfer: The transfer to reconcile.  Must be flushed (``xfer.id`` set)
            with its two shadows present.
        settled: Whether the transfer's confirmed effect should be posted
            (its ``is_settled`` truth for the lifecycle action).  The caller
            passes ``False`` for revert / cancel / delete even when the row's
            status is still settled, so the effect is reversed.

    Returns:
        The new delta :class:`~app.models.journal_entry.JournalEntry` list,
        one per period reconciled -- in practice a single entry, since the R2
        attribution rule keeps every prior period netted to zero -- or ``[]``
        when the ledger is already at target (an idempotent no-op).

    Raises:
        PostingError: If a from/to ledger-account pairing is missing, or
            (when *settled*) the income shadow is absent.
    """
    targets: "dict[int, dict[int, Decimal]]" = {}
    if settled:
        from_ledger = _ledger_account_for(xfer.from_account_id)
        to_ledger = _ledger_account_for(xfer.to_account_id)
        # from leg: money leaving the from-account -> a credit -> negative.
        # to leg:   money entering the to-account  -> a debit  -> positive.
        effective = _settle_effective(xfer)
        targets[xfer.pay_period_id] = {
            from_ledger.id: -effective,
            to_ledger.id: effective,
        }
    posted, last_dates = _posted_by_period(JournalEntry.transfer_id == xfer.id)
    legs_by_period = _reconcile_periods(
        targets, posted, ref_cache.posting_kind_id(PostingKindEnum.TRANSFER),
    )
    if not legs_by_period:
        # Already at target: settling an already-posted transfer, reverting an
        # already-reversed one, cancelling a never-posted one.  No entry.
        return []

    entries = []
    for period_id, legs in sorted(legs_by_period.items()):
        entry = JournalEntry(
            user_id=xfer.user_id,
            scenario_id=xfer.scenario_id,
            pay_period_id=period_id,
            # The settle-side entry (the transfer's current period, when
            # settled) is dated by the settle instant; a reversal-only entry
            # inherits the latest date it reverses (the R2 attribution rule).
            entry_date=(
                _entry_date(xfer)
                if settled and period_id == xfer.pay_period_id
                else last_dates[period_id]
            ),
            source_kind_id=ref_cache.posting_source_id(
                PostingSourceEnum.TRANSFER
            ),
            transfer_id=xfer.id,
            description=_transfer_description(xfer),
        )
        _emit_balanced_entry(entry, legs)
        logger.info(
            "Posted transfer %d ledger deltas %s in period %d (settled=%s) "
            "as journal entry %d",
            xfer.id, {leg.ledger_account_id: leg.amount for leg in legs},
            period_id, settled, entry.id,
        )
        entries.append(entry)
    return entries


def sync_transaction_postings(
    txn: Transaction, *, settled: bool
) -> list[JournalEntry]:
    """Reconcile a transaction's posted ledger effect to its target, idempotently.

    The ordinary-transaction analog of :func:`sync_transfer_postings`: ensures
    the NET amount posted for *txn* equals its target -- the settled
    debit-positive split ``{cash_ledger: cash_leg, category_ledger: -cash_leg}``
    in its CURRENT pay period when *settled*, or nothing when not -- by
    emitting one balanced delta journal entry PER PAY PERIOD whose posted legs
    differ from the target (:func:`_reconcile_periods`), then a no-op
    (returns ``[]``) on any repeat.  See the module docstring for the
    reconcile-to-target rationale and the debit-positive sign convention; see
    :func:`_signed_cash_leg` for the ``effective - Sigma(credit)`` cash-effect
    formula.

    **Reconciles over the accounts AND periods the transaction has ALREADY
    posted to**, read from the ledger by ``transaction_id``
    (:func:`_posted_by_period`), unioned with the target -- NOT a single fixed
    pair.  This is what makes a revert-and-recategorize correct (the reversal
    lands on the OLD category -- the one in the ledger -- not the new
    ``txn.category_id``; plan Section 2.8 CRITICAL), and what makes a
    revert-and-MOVE correct (the reversal lands in the OLD period, dated by
    the latest entry it reverses -- the 2026-07-02 adversarial review's R2
    attribution rule -- so the net-zero pair never straddles periods and a
    later period truncate cannot strand half of it).  Within each period the
    non-zero deltas always sum to zero (a period's target sums to zero, and
    its posted side sums to zero because every prior entry balanced and lives
    in exactly one period), so each emitted entry is balanced and has >= 2
    legs by construction -- :func:`_emit_balanced_entry` never sees a single
    leg.

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
        The new delta :class:`~app.models.journal_entry.JournalEntry` list,
        one per period reconciled -- in practice a single entry, since the R2
        attribution rule keeps every prior period netted to zero -- or ``[]``
        when the ledger is already at target (an idempotent no-op).

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
        return []

    owner_id = txn.pay_period.user_id
    targets: "dict[int, dict[int, Decimal]]" = {}
    if settled:
        targets[txn.pay_period_id] = _settled_target(txn, owner_id)
    posted, last_dates = _posted_by_period(
        JournalEntry.transaction_id == txn.id
    )
    # Both legs of an ordinary-transaction entry carry the same kind, by the
    # transaction type (mirrors Step 2, where both transfer legs are
    # ``transfer``); no Step-3 reader differentiates per-leg kind.
    kind_id = ref_cache.posting_kind_id(
        PostingKindEnum.INCOME if txn.is_income else PostingKindEnum.EXPENSE
    )
    legs_by_period = _reconcile_periods(targets, posted, kind_id)
    if not legs_by_period:
        # Already at target: a repeat settle, an already-reversed revert, a
        # cancel of a never-posted row, or an all-credit envelope (cash_leg 0).
        return []

    entries = []
    for period_id, legs in sorted(legs_by_period.items()):
        entry = JournalEntry(
            user_id=owner_id,
            scenario_id=txn.scenario_id,
            pay_period_id=period_id,
            # The settle-side entry (the transaction's current period, when
            # settled) is dated by the settle instant; a reversal-only entry
            # inherits the latest date it reverses (the R2 attribution rule).
            entry_date=(
                _transaction_entry_date(txn)
                if settled and period_id == txn.pay_period_id
                else last_dates[period_id]
            ),
            source_kind_id=ref_cache.posting_source_id(
                PostingSourceEnum.TRANSACTION
            ),
            transaction_id=txn.id,
            description=txn.name[:_MAX_DESCRIPTION_LENGTH],
        )
        _emit_balanced_entry(entry, legs)
        logger.info(
            "Posted transaction %d ledger deltas %s in period %d (settled=%s)"
            " as journal entry %d",
            txn.id, {leg.ledger_account_id: leg.amount for leg in legs},
            period_id, settled, entry.id,
        )
        entries.append(entry)
    return entries


def reverse_postings_before_delete(txn: Transaction) -> None:
    """Reverse a transaction's ledger postings before the row is deleted.

    The delete-side reconcile every transaction-delete path runs FIRST, while
    ``txn.id`` still exists: it brings the transaction's net posted effect to
    zero by reconciling to an empty (``settled=False``) target via
    :func:`sync_transaction_postings`, emitting a balanced reversal entry for
    whatever the ledger currently holds.  Running it before the delete is
    load-bearing for a HARD delete: ``journal_entries.transaction_id`` is
    ``ON DELETE SET NULL``, so once the row is gone the link is severed and the
    original legs would be stranded on their ledger accounts with no offsetting
    reversal -- breaking per-account reconciliation.  Reversing first leaves the
    original entry and its reversal as an immutable net-zero pair (their
    ``transaction_id`` SET-NULLs together on the delete), so every ledger
    account still nets correctly.  The transaction analog of
    ``transfer_service.delete_transfer``'s ``sync_transfer_postings(xfer,
    settled=False)`` reverse-before-delete.

    Idempotent no-op for a never-settled or already-reversed transaction (a
    Projected row has no postings).  Shared by the delete route
    (``delete_transaction``) and the three payback-delete paths
    (``credit_workflow.delete_payback_on_credit_revert`` /
    ``delete_payback_on_source_delete`` / ``entry_credit_workflow
    .sync_entry_payback``'s DELETE branch) so no delete path can strand a
    posting.  Flushes but does not commit (the caller owns the transaction).

    Args:
        txn: The transaction about to be deleted (soft or hard).  Must still be
            flushed (``txn.id`` set) so the reversal entry can link by
            ``transaction_id`` and read the already-posted legs back.
    """
    sync_transaction_postings(txn, settled=False)
