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
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload, selectinload

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import ledger_account_service, posting_reads
from app.services.cash_ledger import settled_cash_leg
from app.services.posting_reads import PostingError, _ledger_account_for
from app.services.user_write_lock import lock_every_user_writes
from app.services._posting_write import (
    _MAX_DESCRIPTION_LENGTH,
    _PostingLeg,
    emit_keyed_delta_entries,
)
from app.utils.balance_predicates import settled_day, settled_status_ids

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

    A stale-DATED posting (finding N-13: a settled ``settled_on`` edit moves the
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

    The transfer's INCOME shadow's stored ``settled_on`` (the ``Transfer`` model
    has no such column of its own), read through the shared
    :func:`app.utils.balance_predicates.settled_day` -- the ONE accessor for
    "which civil day did this cash move", which the read fold, the posting walk
    and the confirmed-statement reader all ask the same question through, so the
    STORED ``entry_date`` this writes and the day any fold counts the settle on
    cannot drift (balance step C2).

    **It DERIVED the day from ``paid_at`` until plan step X-f1** (ruling R-EC):
    a display-timezone conversion of the click instant, falling back to the pay
    period's ``start_date`` when the instant was NULL.  Both are gone -- the day
    is a stored fact, and a settled shadow carrying none is refused rather than
    dated by a fallback, because ``entry_date`` is NOT NULL and a fabricated
    value here would file real money on a day nothing recorded.

    **The query survives the conversion, and its reason narrowed.**  It used to
    exist partly to force a server-side ``db.func.now()`` to materialise; a
    stored ``date`` is never an unresolved SQL expression, so what remains is
    the real reason -- this reads a DIFFERENT row from the one it is given.  Its
    transaction twin dropped its query entirely for exactly that difference.

    Args:
        xfer: The transfer being posted.

    Returns:
        The civil day the transfer's cash moved.

    Raises:
        PostingError: When no active income shadow resolves (Transfer
            Invariant 1 broken).
        UndatedSettleError: When that shadow carries no ``settled_on``
            (propagated from :func:`~app.utils.balance_predicates.settled_day`).
    """
    # Read the to-account (income) shadow.  The from-account (expense) shadow
    # carries the same day -- the transfer service mirrors it onto both
    # (Transfer Invariant 3) -- so the entry date is identical either way.
    shadow = (
        db.session.query(Transaction.id, Transaction.settled_on)
        .filter(
            Transaction.transfer_id == xfer.id,
            Transaction.account_id == xfer.to_account_id,
            Transaction.is_deleted.is_(False),
        )
        .first()
    )
    if shadow is None:
        raise PostingError(
            f"Transfer {xfer.id} has no active income shadow, so the day its "
            "money moved cannot be resolved; Transfer Invariant 1 is broken."
        )
    return settled_day(shadow.id, shadow.settled_on)


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

    The row's stored ``settled_on``, read through the shared
    :func:`app.utils.balance_predicates.settled_day`.  The transaction analog of
    :func:`_entry_date`, and now a plain attribute read: it DERIVED the day from
    ``paid_at`` until plan step X-f1 (ruling R-EC), with the pay period's
    ``start_date`` as a NULL fallback.

    **It also issued a query, and that query is gone.**  ``paid_at`` was read
    back off the database rather than off the ORM attribute for one stated
    reason -- so a value the caller had set to a server-side ``db.func.now()``
    would autoflush and materialise instead of being read as an unresolved SQL
    expression.  The seam assigns a plain Python ``date`` now, so there is
    nothing to materialise and nothing to re-read.  :func:`_entry_date` keeps
    its query because it reads a DIFFERENT row -- the transfer's income shadow.

    Args:
        txn: The transaction being posted (must be flushed, ``txn.id`` set).

    Returns:
        The civil day the transaction's cash moved.

    Raises:
        UndatedSettleError: When the row carries no ``settled_on``
            (propagated from :func:`~app.utils.balance_predicates.settled_day`).
    """
    return settled_day(txn.id, txn.settled_on)


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

    The Build-Order Step 5 effect-time self-heal, shared by the tails of
    :func:`sync_transfer_postings` and :func:`sync_transaction_postings`
    (which every settle / revert / delete path routes through, including
    :func:`reverse_postings_before_delete`): when the emitted deltas touch a
    non-loan account whose latest anchor assertion sits at-or-after the
    earliest emitted ``entry_date``, that account's opening / true-up
    corrections are reconciled again in the same transaction -- see
    :func:`app.services.account_posting_service.self_heal_anchor_corrections`
    for the predicate's correctness argument.  A no-op when nothing was
    emitted, so the hot idempotent-resync paths pay nothing.

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
    per ``(period, entry date)`` (:func:`_posted_by_period`), so it negates
    exactly what was posted regardless of any later edit to *xfer* -- and
    lands in the PERIOD of the postings it reverses AT THEIR OWN DATE (the
    2026-07-02 adversarial review's R2 attribution rule, per-date since plan
    step E1a): a revert-and-move PATCH therefore reverses into the ORIGINAL
    period, so the net-zero pair never straddles periods and a later period
    truncate cannot strand half of it.  The per-date key also makes a settled
    ``settled_on`` edit reconcile (finding N-13): the entry at the old settle
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

    entries = emit_keyed_delta_entries(
        legs_by_key, _build_transfer_entry,
        f"transfer {xfer.id} (settled={settled})",
    )
    _self_heal_account_anchor_corrections(
        (xfer.from_account_id, xfer.to_account_id), xfer.scenario_id, entries,
    )
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

    entries = emit_keyed_delta_entries(
        legs_by_key, _build_transaction_entry,
        f"transaction {txn.id} (settled={settled})",
    )
    _self_heal_account_anchor_corrections(
        (txn.account_id,), txn.scenario_id, entries,
    )
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


def resync_all_cash_postings() -> tuple[int, int]:
    """Re-reconcile every settled cash source's postings (deploy resync).

    The transaction / transfer twin of
    :func:`app.services.loan_posting_service.backfill_all_loan_postings` and
    :func:`app.services.account_posting_service.backfill_all_account_anchor_postings`,
    and the third of the three deploy-time reconciles that between them cover
    every journal entry the app writes.  It exists because those two do NOT
    reach an ordinary transaction or a NON-loan transfer: the loan package's
    staleness detector is scoped to one loan's linked ledger
    (``loan_posting_service._sync._resync_stale_transfers``) and the anchor
    backfill reconciles only the corrections, so a checking-to-savings transfer
    and every ordinary settled row were maintained per-mutation and by nothing
    else.

    **What it is FOR, and why it is a permanent hook rather than a one-off**
    (ruling R-DH (b), ``docs/audits/balance_architecture/archive/anchor_settle_partition.md``).
    ``journal_entries.entry_date`` is derived by :func:`_transaction_entry_date`,
    which moved from the UTC civil day to the user's on 2026-07-31.  Every entry
    written before that carries the old day, so the STORED ledger and the two
    folds that now read the new one disagree for any settle recorded between
    midnight UTC and the user's midnight -- on production, one ``$1,910.95``
    mortgage payment stamped 2026-07-02 00:38:53 UTC that belongs to the evening
    of 2026-07-01.  This walks every settled source back through the SAME
    go-forward sync, so a re-dated entry is identical to a freshly posted one by
    construction; there is no second implementation of the rule and no SQL
    restatement of it, which is the property this whole arc exists to hold.

    It stays wired on every deploy rather than being deleted after one run, for
    the same reason its two siblings are: reconcile-to-target makes it a no-op
    at target, so it costs one pass and converts any future drift -- a rule
    change, a hand-edited row, a half-applied migration -- into a self-heal
    instead of a silent divergence.

    Idempotent and self-healing.  A settled row already at target posts nothing;
    a row whose target DATE moved gets its old-date legs reversed and its new
    -date legs posted in one balanced pair by
    :func:`sync_transaction_postings` / :func:`sync_transfer_postings`, which
    reconcile over the ``(period, entry_date)`` keys already in the ledger
    unioned with the target (plan step E1a's per-date attribution) -- so a
    moved date is an ordinary reconcile, not a special case this function has to
    know about.

    Loan payment transfers are re-synced here too and that is deliberate
    duplication of effort, not of RULE: the loan package would reach the same
    ones through its own detector, and both paths call this module's
    :func:`sync_transfer_postings`, so whichever runs first leaves the other at
    target.

    Flushes but does NOT commit -- the caller owns the transaction boundary
    (``scripts.init_database.resync_all_cash_postings_after_migration``, which
    initialises ``ref_cache`` first because the migration host does not).

    **The counts are sources CHANGED, not sources walked** (finding N-133 / F8).
    A hook that rewrites the whole production ledger on every deploy and reports
    the same number whether it moved every date or nothing at all tells the
    operator only that it ran.  Both sync functions return the journal entries
    they emitted -- empty when already at target -- so "changed" is observable
    without a second query, and a healthy deploy logs ``0, 0``.  The FIRST
    deploy after a dating rule moves is the one that logs a non-zero count, and
    that line is the only evidence the one-time re-date happened.

    **The re-date is ONE-WAY, and that is a stated risk rather than a
    discovered one.**  ``entrypoint.sh`` runs ``set -eEuo pipefail`` and calls
    ``scripts/init_database.py``, so a failure here aborts the container and the
    auto-rollback fires before anything commits.  But if the healthcheck fails
    AFTER this commits, the rolled-back image reads a display-dated ledger with
    the previous image's UTC rules, and only the entries whose two days differ
    are affected (on production at the cutover: one payment, one day).  Rolling
    back ACROSS a dating change therefore needs this hook re-run under the old
    image, not just a container swap.

    **It is the THIRD multi-owner transaction, and it takes every per-user
    write lock up front** (plan step X-f1c3c, finding N-193).  It iterates every
    owner's settled rows in ID order, and each one can reach the anchor
    self-heal and so ``lock_user_writes(owner)`` -- an unordered multi-key
    acquisition, which is exactly what two concurrent sweeps deadlock on.  A
    first version of the lock's docstring called the two backfill functions
    "the only multi-owner transactions" and missed this one, which is the FIRST
    of the three deploy hooks to run.

    Returns:
        ``(transactions_changed, transfers_changed)`` -- how many settled
        sources this pass actually re-posted, for the deploy log.
    """
    lock_every_user_writes()
    settled_ids = settled_status_ids()
    transactions = (
        db.session.query(Transaction)
        .options(
            selectinload(Transaction.entries),
            # ``pay_period`` is a plain lazy relationship, and BOTH
            # ``_transaction_entry_date`` (its ``start_date`` fallback) and
            # ``_settled_target`` (``pay_period.user_id``) dereference it once
            # per row -- 122 extra SELECTs on production's settled set with the
            # eager ``entries`` load right beside it doing the same job for the
            # other relationship (finding N-133 / F9).
            joinedload(Transaction.pay_period),
        )
        .filter(
            Transaction.is_deleted.is_(False),
            Transaction.transfer_id.is_(None),
            Transaction.status_id.in_(settled_ids),
        )
        .order_by(Transaction.id)
        .all()
    )
    transactions_changed = sum(
        1 for txn in transactions
        if sync_transaction_postings(txn, settled=True)
    )

    transfers = (
        db.session.query(Transfer)
        .options(joinedload(Transfer.pay_period))
        .filter(
            Transfer.is_deleted.is_(False),
            Transfer.status_id.in_(settled_ids),
        )
        .order_by(Transfer.id)
        .all()
    )
    transfers_changed = sum(
        1 for xfer in transfers
        if sync_transfer_postings(xfer, settled=True)
    )

    return transactions_changed, transfers_changed
