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
from datetime import date, datetime
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
from app.services.cash_ledger import settled_cash_leg
from app.services.posting_reads import PostingError, _ledger_account_for
from app.services._posting_write import (
    _MAX_DESCRIPTION_LENGTH,
    _PostingLeg,
    emit_keyed_delta_entries,
)
from app.utils.dates import to_display_civil_date

logger = logging.getLogger(__name__)

# Re-exported read-side API.  The reconciliation readers moved to
# :mod:`app.services.posting_reads` when this module crossed the size gate
# (the sibling-split convention); the ledger's one public surface stays HERE,
# so the oracles and the loan posting package keep reading them off the
# writer module.  ``PostingError`` / ``_ledger_account_for`` above are
# re-exports of the same kind (this module also uses them itself), and so are
# the balanced-write primitives imported from the
# :mod:`app.services._posting_write` leaf (held below every writer so the
# correction packages can share them without importing this module -- see
# that module's docstring for the cycle this breaks).
account_posting_total = posting_reads.account_posting_total
settled_transfer_effect = posting_reads.settled_transfer_effect
settled_transaction_effect = posting_reads.settled_transaction_effect


# ── Private helpers ────────────────────────────────────────────────


def _civil_settle_date(paid_at: datetime | None, pay_period: PayPeriod) -> date:
    """Return the civil date of a settle ``paid_at``, or the period start.

    The shared tail of the transfer and transaction entry-date helpers
    (:func:`_entry_date`, :func:`_transaction_entry_date`): a recorded
    ``paid_at`` maps to its DISPLAY-timezone civil date; a NULL ``paid_at`` (a
    historical settle recorded before the ``paid_at`` sync, or a reverted row
    whose timestamp was cleared) falls back to the pay period's ``start_date``,
    UNCONVERTED -- that fallback is already a civil date and routing it through a
    zone would shift it a day.  ``journal_entries.entry_date`` is NOT NULL, so the
    fallback is load-bearing.

    Delegates to :func:`app.utils.dates.to_display_civil_date` -- the ONE
    derivation the loan fold's payment-visibility rule
    (:func:`app.services.loan_ledger._visible.payment_visible_on`) and the cash
    walk's settle dating (:func:`app.services.cash_ledger.settled_civil_day`) also
    call, so the STORED ``entry_date`` this writes and the day either fold counts
    a settle on cannot drift (balance step C2).

    **The zone is ruling R-DH (b)** (2026-07-31,
    ``docs/audits/balance_architecture/anchor_settle_partition.md``).  It was the
    UTC civil date, mirroring the historical backfill's
    ``COALESCE((paid_at AT TIME ZONE 'UTC')::date, start_date)``.  Storage is
    unchanged -- every instant is still stored UTC -- but the DAY an entry is
    filed under is now the user's, because it is compared against and bucketed by
    plain ``DATE`` columns that mean the user's civil days
    (``pay_periods.start_date`` / ``end_date``).  The three writers moved together
    with the two folds; moving any one alone is what would put a transfer's two
    legs on different days.

    Args:
        paid_at: The settle instant read back from the source row, or None.
        pay_period: The source row's pay period (supplies ``start_date``).

    Returns:
        The display-timezone civil settle date, or the pay period's
        ``start_date``.
    """
    return to_display_civil_date(paid_at, pay_period.start_date)


def _posted_by_period(source_filter) -> "dict[tuple[int, date], dict[int, Decimal]]":
    """Return a source's posted legs grouped by (pay period, entry date).

    The "already posted" side of the reconcile both sync functions share: sums
    ``account_postings.amount`` across every journal entry matching
    *source_filter* (``JournalEntry.transfer_id == x`` for a transfer,
    ``JournalEntry.transaction_id == x`` for a transaction), grouped by the
    entry's ``pay_period_id``, its ``entry_date``, and the leg's ledger
    account.  Reading the posted side back per PERIOD is what lets a reversal
    land in the period of the postings it reverses (the 2026-07-02 adversarial
    review's R2 attribution rule): a source row whose ``pay_period_id`` later
    moved (the revert-and-move PATCH) reverses into its ORIGINAL period, so the
    net-zero pair never straddles periods and a later period truncate cannot
    strand half of it.

    **Per ENTRY DATE as well as period since plan step E1a (finding N-13).**
    ``entry_date`` is the day the recorded event happened (step C2's one
    clock), and the balance fold counts each event from that day -- so a
    correct NET in the right period at the WRONG date is still a wrong ledger
    (the per-visible-date checked-projection assert catches exactly that).
    Keying the reconcile by ``(period, entry_date)`` makes a stale-dated
    posting a first-class delta: it is reversed AT ITS OWN DATE and re-posted
    at the source's current settle date, instead of surviving because its
    period's amount happened to match.  This also retires the old
    latest-``entry_date`` heuristic a reversal-only entry inherited -- each
    key carries the exact date its delta nets against.

    Args:
        source_filter: The SQLAlchemy filter expression selecting the source's
            journal entries (by ``transfer_id`` or ``transaction_id``).

    Returns:
        ``{(pay_period_id, entry_date): {ledger_account_id: net Decimal}}``
        over the source's posted legs (empty when nothing is posted yet; a
        fully-reversed account appears with a ``Decimal("0")`` net, its delta
        then dropping out).
    """
    rows = (
        db.session.query(
            JournalEntry.pay_period_id,
            JournalEntry.entry_date,
            Posting.ledger_account_id,
            db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(source_filter)
        .group_by(
            JournalEntry.pay_period_id,
            JournalEntry.entry_date,
            Posting.ledger_account_id,
        )
        .all()
    )
    posted: "dict[tuple[int, date], dict[int, Decimal]]" = {}
    for period_id, entry_date, ledger_id, net in rows:
        posted.setdefault((period_id, entry_date), {})[ledger_id] = net
    return posted


def _reconcile_periods(
    targets: "dict[tuple[int, date], dict[int, Decimal]]",
    posted: "dict[tuple[int, date], dict[int, Decimal]]",
    kind_id: int,
) -> "dict[tuple[int, date], list[_PostingLeg]]":
    """Return the balanced delta legs per (period, entry date) bringing *posted* to *targets*.

    The keyed core of the reconcile both sync functions share: for every
    ``(pay_period_id, entry_date)`` either side touches, the delta per ledger
    account is ``target - posted``; zero deltas drop.  Within one key the
    non-zero deltas always sum to zero -- a target sums to zero by
    construction, and the posted side sums to zero because every prior entry
    balanced and an entry lives in exactly one (period, date) -- so each key's
    legs form one balanced entry (never a single leg).

    A stale-DATED posting (finding N-13: a settled ``paid_at`` edit moves the
    event's day but no amount) therefore reconciles as two keys: its old date
    reverses to zero and the target's date posts fresh -- converging in ONE
    pass, so a repeat sync writes nothing.

    Args:
        targets: ``{(pay_period_id, entry_date): {ledger_account_id: amount}}``
            the ledger should net to (at most the source's current period at
            its settle date; empty to reverse everything to zero).
        posted: The :func:`_posted_by_period` net map.
        kind_id: The posting kind stamped on every delta leg (both legs of a
            transfer / transaction entry carry the source's one kind).

    Returns:
        ``{(pay_period_id, entry_date): [_PostingLeg, ...]}`` for every key
        with a non-zero delta; empty when the ledger is already at target.
    """
    legs_by_key: "dict[tuple[int, date], list[_PostingLeg]]" = {}
    for key in sorted(set(targets) | set(posted)):
        key_target = targets.get(key, {})
        key_posted = posted.get(key, {})
        legs = [
            _PostingLeg(ledger_id, delta, kind_id)
            for ledger_id in sorted(set(key_target) | set(key_posted))
            if (
                delta := key_target.get(ledger_id, Decimal("0"))
                - key_posted.get(ledger_id, Decimal("0"))
            ) != 0
        ]
        if legs:
            legs_by_key[key] = legs
    return legs_by_key


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

    The DISPLAY-timezone civil date of the transfer's ``paid_at`` (which lives
    on the shadows -- the ``Transfer`` model has none), falling back to the pay
    period's ``start_date`` when ``paid_at`` is NULL (a historical settle
    recorded before the ``paid_at`` sync, or a reverted transfer whose
    ``paid_at`` was cleared).  ``entry_date`` is NOT NULL, so the fallback is
    load-bearing.  It mirrored the Commit-3 backfill's
    ``COALESCE((paid_at AT TIME ZONE 'UTC')::date, start_date)`` until ruling
    R-DH (b) moved the stored day to the user's zone; the derivation is
    :func:`_civil_settle_date`, shared with the transaction path and both
    balance folds.

    The query auto-flushes before reading, so a ``paid_at`` the caller set to
    a server-side ``db.func.now()`` (the ``mark_done`` path) is materialised
    and read back as a concrete timestamp rather than an unresolved SQL
    expression.

    Args:
        xfer: The transfer being posted.

    Returns:
        The display-timezone civil settle date, or the pay period's
        ``start_date`` when no ``paid_at`` is recorded.
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


# ── Transaction (cash) posting helpers (Build-Order Step 3) ────────


def _transaction_entry_date(txn: Transaction) -> date:
    """Return the civil date to stamp on a transaction's journal entry.

    The DISPLAY-timezone civil date of the transaction's ``paid_at`` (ruling
    R-DH (b)), falling back to the pay period's ``start_date`` when ``paid_at``
    is NULL (a historical settle, or a reverted transaction whose ``paid_at``
    was cleared).  The transaction analog of :func:`_entry_date`, sharing
    :func:`_civil_settle_date`.

    ``paid_at`` is read back via a query (not off the ORM attribute) so a value
    the caller set to a server-side ``db.func.now()`` (the ``mark_done`` path)
    is auto-flushed and materialised to a concrete timestamp rather than read
    as an unresolved SQL expression -- the same reason :func:`_entry_date`
    queries the shadow's ``paid_at``.

    Args:
        txn: The transaction being posted (must be flushed, ``txn.id`` set).

    Returns:
        The display-timezone civil settle date, or the pay period's
        ``start_date`` when no ``paid_at`` is recorded.
    """
    paid_at = (
        db.session.query(Transaction.paid_at)
        .filter(Transaction.id == txn.id)
        .scalar()
    )
    return _civil_settle_date(paid_at, txn.pay_period)


def _settled_target(txn: Transaction, owner_id: int) -> dict[int, Decimal]:
    """Return the debit-positive ledger target for a SETTLED transaction.

    The two-account map the ledger should net to once *txn* is settled:
    ``{cash_ledger_id: cash_leg, category_ledger_id: -cash_leg}``, summing to
    zero by construction.  ``cash_leg`` is
    :func:`app.services.cash_ledger.settled_cash_leg`; the cash
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
    cash_leg = settled_cash_leg(txn)
    return {cash_ledger.id: cash_leg, category_ledger.id: -cash_leg}


def _self_heal_account_anchor_corrections(
    account_ids: tuple, scenario_id: int, entries: list[JournalEntry],
) -> None:
    """Re-derive anchor corrections the just-emitted source deltas staled.

    The Build-Order Step 5 effect-time self-heal, shared by the tails of the
    two CHECKED syncs, :func:`sync_transfer_postings` and
    :func:`sync_transaction_postings`: when either emits a source delta on a
    non-loan account, that account's opening / true-up corrections are
    reconciled again in the same transaction, because a source whose posted
    effect changed at-or-before the account's latest assertion moved that
    assertion's walked ``ledger_before``.  A no-op when nothing was emitted, so
    the hot idempotent-resync paths pay nothing.

    **It ran behind a SKIP predicate until plan step X-d, and ruling R-DK
    deleted it**: whether a particular walk would write nothing is not a
    question a cost guard should answer in a money path, and the
    checked-projection assert now rides behind this call, so a skip here was a
    skip of the CHECK too.
    :func:`app.services.account_posting_service.self_heal_anchor_corrections`
    carries the full argument.

    **The retirement paths do NOT route through here, and that is ruling
    R-DM.**  :func:`reverse_postings_before_delete` and
    :func:`reverse_transfer_postings_before_delete` call the reconcile CORE
    instead, because between the reversal and the removal the rows and the
    ledger deliberately disagree; :func:`retire_transaction` and
    ``transfer_service.delete_transfer`` re-derive once the rows are final.

    Args:
        account_ids: The real accounts the deltas' LINKED legs can touch
            (immutable on their source rows).
        scenario_id: The scenario the deltas were emitted in.
        entries: The just-emitted delta entries (empty -> no-op).
    """
    if not entries:
        return
    # Pylint: ``import-outside-toplevel`` -- reverse dependency: the account
    # posting package imports this module's balanced-write path, so the
    # top-level import would be circular.  Mirrors the loan package's
    # function-local imports of the same shape.
    # pylint: disable-next=import-outside-toplevel
    from app.services import account_posting_service

    account_posting_service.self_heal_anchor_corrections(
        account_ids, scenario_id, entries,
    )


# ── Public API ─────────────────────────────────────────────────────


def _reconcile_transfer_postings(
    xfer: Transfer, *, settled: bool
) -> list[JournalEntry]:
    """Reconcile a transfer's posted ledger effect to its target, idempotently.

    **The reconcile CORE: the ledger effect and nothing else.**  It emits the
    balanced deltas and stops -- no account-anchor re-derive on either endpoint,
    and therefore no checked-projection assert.  Its two callers are
    :func:`sync_transfer_postings`, the go-forward entry point that adds both,
    and :func:`reverse_transfer_postings_before_delete`, which runs while the
    shadows deliberately do not yet agree with the ledger (ruling R-DM).

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
    per ``(period, entry date)`` (:func:`_posted_by_period`), so it negates
    exactly what was posted regardless of any later edit to *xfer* -- and
    lands in the PERIOD of the postings it reverses AT THEIR OWN DATE (the
    2026-07-02 adversarial review's R2 attribution rule, per-date since plan
    step E1a): a revert-and-move PATCH therefore reverses into the ORIGINAL
    period, so the net-zero pair never straddles periods and a later period
    truncate cannot strand half of it.  The per-date key also makes a settled
    ``paid_at`` edit reconcile (finding N-13): the entry at the old settle
    date reverses and the effect re-posts at the new one, converging in one
    pass.  Idempotency rests on this delta math plus the transfer's
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
        one per (period, entry date) reconciled -- in practice a single entry,
        since the R2 attribution rule keeps every prior key netted to zero --
        or ``[]`` when the ledger is already at target (an idempotent no-op).

    Raises:
        PostingError: If a from/to ledger-account pairing is missing, or
            (when *settled*) the income shadow is absent.
    """
    targets: "dict[tuple[int, date], dict[int, Decimal]]" = {}
    if settled:
        from_ledger = _ledger_account_for(xfer.from_account_id)
        to_ledger = _ledger_account_for(xfer.to_account_id)
        # from leg: money leaving the from-account -> a credit -> negative.
        # to leg:   money entering the to-account  -> a debit  -> positive.
        effective = _settle_effective(xfer)
        # The target lives at the transfer's current period AND its settle
        # date (step C2's one clock): a posting at any other (period, date)
        # is stale and reconciles away at its own key.
        targets[(xfer.pay_period_id, _entry_date(xfer))] = {
            from_ledger.id: -effective,
            to_ledger.id: effective,
        }
    posted = _posted_by_period(JournalEntry.transfer_id == xfer.id)
    legs_by_key = _reconcile_periods(
        targets, posted, ref_cache.posting_kind_id(PostingKindEnum.TRANSFER),
    )
    if not legs_by_key:
        # Already at target: settling an already-posted transfer, reverting an
        # already-reversed one, cancelling a never-posted one.  No entry.
        return []

    def _build_transfer_entry(period_id: int, entry_date: date) -> JournalEntry:
        """Build one transfer delta entry header for its (period, date) key."""
        return JournalEntry(
            user_id=xfer.user_id,
            scenario_id=xfer.scenario_id,
            pay_period_id=period_id,
            # Each delta entry carries its key's date: the settle-side entry
            # the settle instant, a reversal the exact date of the postings it
            # reverses (the R2 attribution rule, per-date since step E1a).
            entry_date=entry_date,
            source_kind_id=ref_cache.posting_source_id(
                PostingSourceEnum.TRANSFER
            ),
            transfer_id=xfer.id,
            description=_transfer_description(xfer),
        )

    return emit_keyed_delta_entries(
        legs_by_key, _build_transfer_entry,
        f"transfer {xfer.id} (settled={settled})",
    )


def sync_transfer_postings(
    xfer: Transfer, *, settled: bool
) -> list[JournalEntry]:
    """Reconcile a transfer's postings, then re-derive both accounts' anchors.

    The go-forward entry point every transfer lifecycle path calls:
    :func:`_reconcile_transfer_postings` for the ledger effect, then the
    account-anchor re-derive on BOTH endpoints
    (:func:`_self_heal_account_anchor_corrections`), which also runs plan step
    X-d's checked-projection assert.

    **The two are split for the same reason the transaction pair is** (ruling
    R-DM): the assert reads the shadow ROWS, and a delete must reverse the
    postings while those rows still exist and still read settled, so asserting
    there would grade a half-finished operation.
    :func:`reverse_transfer_postings_before_delete` is the reversal half, and
    ``transfer_service.delete_transfer`` re-derives the anchors once the rows
    are final -- beside the loan resync already sitting at its end.

    Args:
        xfer: The transfer to reconcile.  See
            :func:`_reconcile_transfer_postings`.
        settled: Whether the transfer's confirmed effect should be posted.
            **Every production caller passes the row's OWN status**, verified
            across all five call sites at plan step X-d; the retire path is the
            one place the two differ, and it goes through the reversal above.
            Finding N-144 owns removing the parameter.

    Returns:
        The new delta entries, or ``[]`` when the ledger is already at target.

    Raises:
        PostingError: From the reconcile (a missing ledger pairing, or an
            absent income shadow when *settled*), or from the
            checked-projection assert on either endpoint.
    """
    entries = _reconcile_transfer_postings(xfer, settled=settled)
    _self_heal_account_anchor_corrections(
        (xfer.from_account_id, xfer.to_account_id), xfer.scenario_id, entries,
    )
    return entries


def reverse_transfer_postings_before_delete(xfer: Transfer) -> None:
    """Reverse a transfer's ledger postings before its rows are retired.

    The transfer twin of :func:`reverse_postings_before_delete`, and the
    delete-side reconcile ``transfer_service.delete_transfer`` runs FIRST,
    while ``xfer.id`` and both shadows still exist: it brings the transfer's net
    posted effect to zero, emitting a balanced reversal for whatever the ledger
    holds.  Running it before the retirement is load-bearing for a HARD delete
    (``journal_entries.transfer_id`` is ``ON DELETE SET NULL``, so afterwards
    the link is severed and the original legs would strand) and for the SOFT one
    (the shadow queries the reversal reads filter ``is_deleted``).

    **There is no transfer twin of :func:`retire_transaction`, and that is
    ruling R-DN rather than an omission.**  A ``posting_service.retire_transfer``
    would have to soft-delete both shadows, and ``CLAUDE.md`` Transfer Invariant
    4 reserves every shadow mutation to the transfer service.  So the retirement
    is two named halves -- this one, then
    :func:`app.services.account_posting_service.resync_anchor_postings` on both
    endpoints -- and ``delete_transfer`` is the single path that calls both.

    **It reconciles WITHOUT re-deriving either account's anchors, and that is
    ruling R-DM.**  Between this call and the retirement the shadows still read
    settled while the ledger reads zero -- a deliberate window, not a defect --
    and the checked-projection assert that rides on the anchor re-derive would
    grade it.  ``delete_transfer`` owns that re-derive and runs it once the rows
    are final.

    Idempotent no-op for a never-settled or already-reversed transfer.  Flushes
    but does not commit (the caller owns the transaction).

    Args:
        xfer: The transfer about to be soft- or hard-deleted.  Must still be
            flushed with both shadows present, so the reversal links by
            ``transfer_id`` and reads the posted legs back.
    """
    _reconcile_transfer_postings(xfer, settled=False)


def _reconcile_transaction_postings(
    txn: Transaction, *, settled: bool
) -> list[JournalEntry]:
    """Reconcile a transaction's posted ledger effect to its target, idempotently.

    **The reconcile CORE: the ledger effect and nothing else.**  It emits the
    balanced deltas and stops -- no account-anchor re-derive, and therefore no
    checked-projection assert.  Its two callers are
    :func:`sync_transaction_postings`, the go-forward entry point that adds
    both, and :func:`reverse_postings_before_delete`, which runs while the row
    deliberately does not yet agree with the ledger (ruling R-DM; see the
    wrapper's docstring for why that window exists and why it is not asserted).

    The ordinary-transaction analog of :func:`sync_transfer_postings`: ensures
    the NET amount posted for *txn* equals its target -- the settled
    debit-positive split ``{cash_ledger: cash_leg, category_ledger: -cash_leg}``
    in its CURRENT pay period when *settled*, or nothing when not -- by
    emitting one balanced delta journal entry PER PAY PERIOD whose posted legs
    differ from the target (:func:`_reconcile_periods`), then a no-op
    (returns ``[]``) on any repeat.  See the module docstring for the
    reconcile-to-target rationale and the debit-positive sign convention; see
    :func:`app.services.cash_ledger.settled_cash_leg` for the
    ``effective - Sigma(credit)`` cash-effect
    formula.

    **Reconciles over the accounts, periods, AND entry dates the transaction
    has ALREADY posted to**, read from the ledger by ``transaction_id``
    (:func:`_posted_by_period`), unioned with the target -- NOT a single fixed
    pair.  This is what makes a revert-and-recategorize correct (the reversal
    lands on the OLD category -- the one in the ledger -- not the new
    ``txn.category_id``; plan Section 2.8 CRITICAL), and what makes a
    revert-and-MOVE correct (the reversal lands in the OLD period at the exact
    date of the postings it reverses -- the 2026-07-02 adversarial review's R2
    attribution rule, per-date since plan step E1a -- so the net-zero pair
    never straddles periods and a later period truncate cannot strand half of
    it).  Within each (period, date) key the non-zero deltas always sum to
    zero (a target sums to zero, and the posted side sums to zero because
    every prior entry balanced and lives in exactly one key), so each emitted
    entry is balanced and has >= 2 legs by construction --
    :func:`_emit_balanced_entry` never sees a single leg.

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
        one per (period, entry date) reconciled -- in practice a single entry,
        since the R2 attribution rule keeps every prior key netted to zero --
        or ``[]`` when the ledger is already at target (an idempotent no-op).

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
    targets: "dict[tuple[int, date], dict[int, Decimal]]" = {}
    if settled:
        # The target lives at the transaction's current period AND its settle
        # date (step C2's one clock); see ``sync_transfer_postings``.
        targets[(txn.pay_period_id, _transaction_entry_date(txn))] = (
            _settled_target(txn, owner_id)
        )
    posted = _posted_by_period(JournalEntry.transaction_id == txn.id)
    # Both legs of an ordinary-transaction entry carry the same kind, by the
    # transaction type (mirrors Step 2, where both transfer legs are
    # ``transfer``); no Step-3 reader differentiates per-leg kind.
    kind_id = ref_cache.posting_kind_id(
        PostingKindEnum.INCOME if txn.is_income else PostingKindEnum.EXPENSE
    )
    legs_by_key = _reconcile_periods(targets, posted, kind_id)
    if not legs_by_key:
        # Already at target: a repeat settle, an already-reversed revert, a
        # cancel of a never-posted row, or an all-credit envelope (cash_leg 0).
        return []

    def _build_transaction_entry(
        period_id: int, entry_date: date,
    ) -> JournalEntry:
        """Build one transaction delta entry header for its (period, date) key."""
        return JournalEntry(
            user_id=owner_id,
            scenario_id=txn.scenario_id,
            pay_period_id=period_id,
            # Each delta entry carries its key's date: the settle-side entry
            # the settle instant, a reversal the exact date of the postings it
            # reverses (the R2 attribution rule, per-date since step E1a).
            entry_date=entry_date,
            source_kind_id=ref_cache.posting_source_id(
                PostingSourceEnum.TRANSACTION
            ),
            transaction_id=txn.id,
            description=txn.name[:_MAX_DESCRIPTION_LENGTH],
        )

    return emit_keyed_delta_entries(
        legs_by_key, _build_transaction_entry,
        f"transaction {txn.id} (settled={settled})",
    )


def sync_transaction_postings(
    txn: Transaction, *, settled: bool
) -> list[JournalEntry]:
    """Reconcile a transaction's postings, then re-derive its account's anchors.

    The go-forward entry point every ordinary-transaction lifecycle path calls:
    :func:`_reconcile_transaction_postings` for the ledger effect, then the
    account-anchor re-derive (:func:`_self_heal_account_anchor_corrections`),
    which ALSO runs the checked-projection assert (plan step X-d).

    **The two are split because the assert reads the transaction ROW, and one
    caller legitimately runs the reconcile while the row does not yet agree
    with it** (ruling R-DM).  A delete must reverse the postings while
    ``txn.id`` still exists -- the FK is ``ON DELETE SET NULL`` -- so between
    that reversal and the removal, the row still reads settled while the ledger
    reads zero.  Asserting there grades a half-finished operation.  So the
    reversal calls the reconcile CORE and :func:`retire_transaction` re-derives
    the anchors once the row is final, which is the same split
    ``loan_posting_service`` already ships (``_reconcile_loan_payment`` for the
    reverse-before-delete, ``sync_loan_postings`` for the checked one).  No
    boolean selects between them: they are two names.

    Args:
        txn: The transaction to reconcile.  See
            :func:`_reconcile_transaction_postings`.
        settled: Whether the transaction's confirmed effect should be posted.
            **Every production caller passes the row's OWN status**
            (``txn.status.is_settled``), verified across all seven call sites at
            plan step X-d; the retire path is the one place the two differ, and
            it goes through :func:`reverse_postings_before_delete` instead.
            Finding N-144 owns removing the parameter.

    Returns:
        The new delta entries, or ``[]`` when the ledger is already at target.

    Raises:
        PostingError: From the reconcile (a missing ledger pairing), or from
            the checked-projection assert when the reconciled ledger no longer
            equals the fold of the account's facts.
    """
    entries = _reconcile_transaction_postings(txn, settled=settled)
    _self_heal_account_anchor_corrections(
        (txn.account_id,), txn.scenario_id, entries,
    )
    return entries


def retire_transaction(txn: Transaction, *, hard: bool) -> None:
    """Reverse a transaction's postings, remove the row, re-derive the anchors.

    **The ONE transaction-retirement chokepoint (ruling R-DM).**  Retiring a
    posted row is three steps that must happen in one order, and the order is
    forced by the schema rather than chosen:

    1. **reverse the postings**, while ``txn.id`` still exists --
       ``journal_entries.transaction_id`` is ``ON DELETE SET NULL``, so once the
       row is gone the link is severed and the original legs would be stranded
       with no offsetting reversal;
    2. **remove the row**, hard (ad-hoc) or soft (template-linked, so the
       recurrence engine still sees it);
    3. **re-derive the account's anchor corrections**, now that the row is
       final, which is also where the checked-projection assert runs.

    **Step 3 is what this function exists for.**  Steps 1 and 2 were written out
    at each of the four delete sites, and step 3 did not exist: the anchor
    re-derive rode inside step 1, where the row still reads settled and the
    ledger already reads zero, so plan step X-d's assert graded a half-finished
    operation.  Leaving the three steps to each caller would make step 3 an
    obligation five places have to remember, and a forgotten one leaves a stale
    anchor correction that nothing detects until the next sync -- an unowned
    obligation in the general ledger.  One function makes the ordering
    structural.

    It lives HERE and not in ``transaction_service``, and that is forced:
    ``transaction_service`` imports ``entry_service``, so ``credit_workflow``
    -- one of this function's callers -- could not import from it without
    closing the ``transaction_service <- entry_service <- entry_credit_workflow
    <- credit_workflow`` cycle.  Same constraint that put
    :mod:`app.services.status_seam` below its callers, recorded in that module's
    docstring.

    Idempotent for a never-settled row (a Projected transaction has no postings,
    so the reversal writes nothing) and for an already-soft-deleted one.
    Flushes but does not commit -- the caller owns the transaction, and the
    whole retirement is meant to land in ONE of them.

    Args:
        txn: The transaction to retire.  Must be flushed (``txn.id`` set) so the
            reversal links by ``transaction_id`` and reads its posted legs back.
        hard: ``True`` to remove the row outright (an ad-hoc transaction),
            ``False`` to soft-delete it (a template-linked row, which the
            recurrence engine must keep seeing as deliberately removed).

    Raises:
        PostingError: From the reversal (a missing ledger pairing), or from the
            checked-projection assert when the account's ledger no longer equals
            the fold of its facts once the row is gone.
    """
    # Captured BEFORE the removal: a hard-deleted row cannot be asked for its
    # account or scenario afterwards, and both are immutable on the row anyway.
    account_id = txn.account_id
    scenario_id = txn.scenario_id
    reverse_postings_before_delete(txn)
    if hard:
        db.session.delete(txn)
    else:
        txn.is_deleted = True
    db.session.flush()
    # Pylint: ``import-outside-toplevel`` -- reverse dependency, the same one
    # ``_self_heal_account_anchor_corrections`` documents: the account posting
    # package imports this module's balanced-write path.
    # pylint: disable-next=import-outside-toplevel
    from app.services import account_posting_service

    account_posting_service.resync_anchor_postings(
        (account_id,), scenario_id,
    )


def reverse_postings_before_delete(txn: Transaction) -> None:
    """Reverse a transaction's ledger postings before the row is deleted.

    The delete-side reconcile every transaction-delete path runs FIRST, while
    ``txn.id`` still exists: it brings the transaction's net posted effect to
    zero by reconciling to an empty (``settled=False``) target through the
    reconcile CORE, :func:`_reconcile_transaction_postings`, emitting a
    balanced reversal entry for whatever the ledger currently holds.  It is the
    core and NOT :func:`sync_transaction_postings` deliberately, for the reason
    the last paragraph gives.  Running it before the delete is
    load-bearing for a HARD delete: ``journal_entries.transaction_id`` is
    ``ON DELETE SET NULL``, so once the row is gone the link is severed and the
    original legs would be stranded on their ledger accounts with no offsetting
    reversal -- breaking per-account reconciliation.  Reversing first leaves the
    original entry and its reversal as an immutable net-zero pair (their
    ``transaction_id`` SET-NULLs together on the delete), so every ledger
    account still nets correctly.  The transaction analog of
    :func:`reverse_transfer_postings_before_delete`, which
    ``transfer_service.delete_transfer`` runs in the same position.

    Idempotent no-op for a never-settled or already-reversed transaction (a
    Projected row has no postings).  Its ONE caller is
    :func:`retire_transaction`, the chokepoint the four delete sites route
    through since plan step X-d -- the delete route (``delete_transaction``)
    and the three payback-delete paths
    (``credit_workflow.delete_payback_on_credit_revert`` /
    ``delete_payback_on_source_delete`` / ``entry_credit_workflow
    .sync_entry_payback``'s DELETE branch), each of which used to call this
    directly and then delete by hand.  Flushes but does not commit (the caller
    owns the transaction).

    **It reconciles WITHOUT re-deriving the account's anchors, and that is
    ruling R-DM.**  It runs in the one window where the row and the ledger
    deliberately disagree -- the postings are at zero while the row still reads
    settled -- so the checked-projection assert that rides on the anchor
    re-derive would grade a half-finished operation.  :func:`retire_transaction`
    owns that re-derive and runs it once the row is final.  Calling this
    directly is therefore only correct as part of a retirement; every other
    lifecycle path wants :func:`sync_transaction_postings`.

    Args:
        txn: The transaction about to be deleted (soft or hard).  Must still be
            flushed (``txn.id`` set) so the reversal entry can link by
            ``transaction_id`` and read the already-posted legs back.
    """
    _reconcile_transaction_postings(txn, settled=False)
