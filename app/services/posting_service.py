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

**The amount is what the SHADOW RECORDED, not ``transfers.amount``.**
A settled transfer's effect is read as the income shadow's
:func:`~app.services.posting_reads.settled_figure_clause` -- the same
expression the balance calculator and the reconciliation oracle read, and the
one the Python tier answers through ``row_valuation.settled_figure`` (plan step
X-au-c3; it was ``COALESCE(actual_amount, estimated_amount)``).  The two differ
whenever a settle books a figure the parent's own amount does not hold, which a
correction and a derive-mode loan payment both do; posting ``transfers.amount``
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
from app.models.journal_entry import JournalEntry
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import ledger_account_service, posting_reads
from app.services.cash_ledger import settled_cash_leg
from app.services.posting_reads import PostingError, _ledger_account_for
from app.services.user_write_lock import lock_every_user_writes
from app.services._posting_purchases import (
    emit_purchase_deltas,
    posted_purchase_exists_clause,
    purchase_posts,
)
from app.services._posting_write import (
    _MAX_DESCRIPTION_LENGTH,
    emit_source_deltas,
    source_entry_builder,
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
posted_purchase_effect = posting_reads.posted_purchase_effect


# ── Private helpers ────────────────────────────────────────────────


def _settle_effective(xfer: Transfer) -> Decimal:
    """Return what the transfer's income leg RECORDED as having moved.

    The income shadow lives on the to-account (``_build_shadow`` in
    ``transfer_service``), and what it recorded is
    :func:`~app.services.posting_reads.settled_figure_clause` -- the same
    expression the two reconciliation folds read and the Python tier answers
    through ``row_valuation.settled_figure``, so the ledger and the balance
    cannot come to price one leg two ways.

    **Its query states its own preconditions now, and that is findings N-242 and
    N-298** (plan step X-au-c3).  It filtered on ``transfer_id`` /
    ``account_id`` / ``is_deleted`` alone, and its settled-ness was a CALLER
    convention its own docstring named -- while the balance README carried the
    stronger claim that every SQL-tier reader of the settled figure was safe
    because it filtered to settled statuses, which was true of
    ``posting_reads`` and not of this one.  Two consequences it could not
    distinguish, and both are now impossible rather than merely unobserved:

    * **no status predicate.**  An unsettled shadow records nothing, so the
      expression above answered ``0`` for one -- a silent zero where a caller
      asked what moved.  The predicate makes the query answer only about a row
      that has settled, and the refusal below turns "nothing to answer" into an
      error rather than a zero.  Since that expression became a ``CASE`` on the
      basis it answers ``NULL`` rather than ``0`` for a row recording nothing,
      so the two ways this lookup comes back empty -- no such shadow, and a
      shadow with no record -- arrive as one ``None`` and the refusal names
      both;
    * **no ``.limit(1)``.**  A second active shadow on the to-account raises
      ``MultipleResultsFound`` from ``.scalar()``; the sibling reader at
      ``models/transfer.py`` added ``.limit(1)`` for exactly that.  Transfer
      Invariant 1 makes two shadows a broken state rather than a supported one,
      so this takes the first deterministically -- by ``id``, so a repeated read
      answers the same leg -- and leaves surfacing the breakage to the invariant's
      own repair path instead of failing here with an error about SQL.

    Args:
        xfer: The transfer being posted.

    Returns:
        The income shadow's recorded figure as a ``Decimal``.

    Raises:
        PostingError: If the transfer has no SETTLED, active income shadow on
            its to-account -- a Transfer-Invariant-1 violation, or a caller
            posting a settled effect for a pair that has not settled -- or if
            that shadow records no settlement, which
            ``ck_transactions_settle_day_needs_a_record`` makes unstorable.
    """
    effective = (
        db.session.query(posting_reads.settled_figure_clause())
        .filter(
            Transaction.transfer_id == xfer.id,
            Transaction.account_id == xfer.to_account_id,
            Transaction.is_deleted.is_(False),
            Transaction.status_id.in_(settled_status_ids()),
        )
        .order_by(Transaction.id)
        .limit(1)
        .scalar()
    )
    if effective is None:
        raise PostingError(
            f"Transfer {xfer.id} has no settled, active income shadow on "
            f"account {xfer.to_account_id} that records what moved; cannot "
            "post its settled effect. Either no such shadow exists (Transfer "
            "Invariant 1), or the one that does carries no settlement record "
            "-- a state ck_transactions_settle_day_needs_a_record refuses to "
            "store and status_seam.apply_status_change refuses to create."
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
    :func:`app.services.cash_ledger.settled_cash_leg` -- which since plan step
    X-f3b (ruling **R-FM**) is net of the row's already-posted purchases, so
    this books only the remainder and :func:`_purchase_target` books the rest;
    the cash
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
        owner_id: The owning user's id, the category account's owner.  The
            caller sources it from ``txn.pay_period.user_id``; ``txn.user_id``
            is the same value and one hydration cheaper since plan step
            ``pay_calendar:C13-a``.  This is a read that STAMPS rather than
            refuses, so it is NOT one of finding **P75**'s nineteen -- that
            row's own census excludes it by name -- and moving it is a
            performance question ``C13-b`` may take while it is there.

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
    (:func:`~app.services._posting_write.emit_source_deltas`).  A no-op
    (returns ``[]``) when the ledger is already at target.  See the module
    docstring for the reconcile-to-target rationale and the debit-positive sign
    convention.

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
    per ``(period, entry date)``
    (:func:`~app.services._posting_write.posted_by_period`), so it negates
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
    # Each delta entry carries its key's period and date: the settle-side entry
    # the settle instant, a reversal the exact date of the postings it reverses
    # (the R2 attribution rule, per-date since step E1a).  Everything else is
    # this source's, stated once for all three sources in
    # ``_posting_write.source_entry_builder``.  An empty result means the ledger
    # is already at target: settling an already-posted transfer, reverting an
    # already-reversed one, cancelling a never-posted one.
    entries = emit_source_deltas(
        targets=targets,
        source_filter=JournalEntry.transfer_id == xfer.id,
        kind_id=ref_cache.posting_kind_id(PostingKindEnum.TRANSFER),
        build_entry=source_entry_builder(
            user_id=xfer.user_id,
            scenario_id=xfer.scenario_id,
            source_kind_id=ref_cache.posting_source_id(
                PostingSourceEnum.TRANSFER
            ),
            description=_transfer_description(xfer),
            transfer_id=xfer.id,
        ),
        log_label=f"transfer {xfer.id} (settled={settled})",
    )
    _self_heal_account_anchor_corrections(
        (xfer.from_account_id, xfer.to_account_id), xfer.scenario_id, entries,
    )
    return entries


def sync_transaction_postings(
    txn: Transaction, *, settled: bool
) -> list[JournalEntry]:
    """Reconcile a transaction's whole posted FAMILY to its target, idempotently.

    The ordinary-transaction analog of :func:`sync_transfer_postings`: ensures
    the NET amount posted for *txn* equals its target -- the settled
    debit-positive split ``{cash_ledger: cash_leg, category_ledger: -cash_leg}``
    in its CURRENT pay period when *settled*, or nothing when not -- by
    emitting one balanced delta journal entry PER PAY PERIOD whose posted legs
    differ from the target
    (:func:`~app.services._posting_write.emit_source_deltas`), then a no-op
    (returns ``[]``) on any repeat.  See the module docstring for the
    reconcile-to-target rationale and the debit-positive sign convention; see
    :func:`app.services.cash_ledger.settled_cash_leg` for the
    ``effective - Sigma(credit) - Sigma(posted purchases)`` cash-effect formula.

    **A FAMILY since plan step X-f3b, and that is ruling R-FM** (finding
    **N-274**).  A purchase whose bank posting day the owner recorded is a cash
    movement of its own, on its own day, so this reconciles the parent's leg AND
    one entry per such purchase
    (:func:`~app.services._posting_purchases.emit_purchase_deltas`) in a single
    pass.  The two halves cannot double-count, structurally: what the parent's
    leg books is ``sum(entries) - credit - posted``, so the family always sums
    to the row's whole debit total whatever subset of its purchases has posted.
    **Owning both halves here is what makes the trigger set complete** -- every
    door that already reconciles a transaction (a settle, a revert, a
    re-category, an amount edit, a period move, a status change) now reconciles
    its purchases too, with no second list of call sites to keep in step.  A
    change to ONE purchase that leaves the parent alone has its own narrower
    door, :func:`sync_purchase_postings`.

    **Reconciles over the accounts, periods, AND entry dates the transaction
    has ALREADY posted to**, read from the ledger by ``transaction_id``
    (:func:`~app.services._posting_write.posted_by_period`), unioned with the
    target -- NOT a single fixed
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
    # against the transfer posting, so the guard stays.  A shadow also carries no
    # entries (``entry_service`` refuses them), so the purchase arm below has
    # nothing to do for one either.
    if txn.transfer_id is not None:
        return []

    owner_id = txn.pay_period.user_id
    entries = _emit_transaction_deltas(txn, settled=settled, owner_id=owner_id)
    for purchase in txn.entries:
        entries.extend(
            emit_purchase_deltas(
                purchase, txn,
                posted=purchase_posts(txn, purchase), owner_id=owner_id,
            )
        )
    _self_heal_account_anchor_corrections(
        (txn.account_id,), txn.scenario_id, entries,
    )
    return entries


def _emit_transaction_deltas(
    txn: Transaction, *, settled: bool, owner_id: int,
) -> "list[JournalEntry]":
    """Emit the delta entries for the transaction's OWN cash leg.

    :func:`sync_transaction_postings`' first half, split out so its purchase
    arm and the teardown door (:func:`reverse_postings_before_delete`) compose
    the same two halves without either running the anchor self-heal twice.

    Args:
        txn: The transaction (already known not to be a transfer shadow).
        settled: Whether its confirmed effect should be posted.
        owner_id: ``txn.pay_period.user_id``.

    Returns:
        The emitted delta entries; ``[]`` when the ledger is already at target.
    """
    targets: "dict[tuple[int, date], dict[int, Decimal]]" = {}
    if settled:
        # The target lives at the transaction's current period AND its settle
        # date (step C2's one clock); see ``sync_transfer_postings``.
        targets[(txn.pay_period_id, _transaction_entry_date(txn))] = (
            _settled_target(txn, owner_id)
        )
    # An empty result means the ledger is already at target: a repeat settle, an
    # already-reversed revert, a cancel of a never-posted row, or an envelope
    # whose whole debit total is already carried by its own purchases.
    return emit_source_deltas(
        targets=targets,
        source_filter=JournalEntry.transaction_id == txn.id,
        # Both legs of an ordinary-transaction entry carry the same kind, by the
        # transaction type (mirrors Step 2, where both transfer legs are
        # ``transfer``); no Step-3 reader differentiates per-leg kind.
        kind_id=ref_cache.posting_kind_id(
            PostingKindEnum.INCOME if txn.is_income else PostingKindEnum.EXPENSE
        ),
        build_entry=source_entry_builder(
            user_id=owner_id,
            scenario_id=txn.scenario_id,
            source_kind_id=ref_cache.posting_source_id(
                PostingSourceEnum.TRANSACTION
            ),
            description=txn.name[:_MAX_DESCRIPTION_LENGTH],
            transaction_id=txn.id,
        ),
        log_label=f"transaction {txn.id} (settled={settled})",
    )


def sync_purchase_postings(entry) -> "list[JournalEntry]":
    """Reconcile ONE purchase's posted ledger effect to its target.

    The per-purchase door, for the write paths that change a purchase without
    touching its parent's own cash leg: ``entry_service``'s create / update
    doors and ``reconcile_service``'s statement tick.  It resolves whether the
    purchase should be posted itself
    (:func:`~app.services._posting_purchases.purchase_posts`), so no caller
    restates that rule.

    A caller that has changed the PARENT -- a settle, a revert, a re-category, a
    delete -- calls :func:`sync_transaction_postings` instead, which reconciles
    the whole family in one pass.

    Flushes but does not commit (the caller owns the transaction).

    Args:
        entry: The purchase, flushed (``entry.id`` set), with its parent
            reachable through ``entry.transaction``.

    Returns:
        The new delta entries, or ``[]`` when the ledger is already at target.

    Raises:
        PostingError: If the account has no linked ledger account.
    """
    txn = entry.transaction
    # The same guard the two transaction doors take, for the same reason and
    # over the same row set: a shadow's postings are Step 2's and link by
    # ``transfer_id``.  ``entry_service.create_entry`` refuses a shadow outright,
    # so this is defense-in-depth -- but three doors refusing three different row
    # sets is how the fourth one is written wrongly.
    if txn.transfer_id is not None:
        return []
    owner_id = txn.pay_period.user_id
    entries = emit_purchase_deltas(
        entry, txn, posted=purchase_posts(txn, entry), owner_id=owner_id,
    )
    _self_heal_account_anchor_corrections(
        (entry.account_id,), txn.scenario_id, entries,
    )
    return entries


def reverse_purchase_postings_before_delete(entry) -> None:
    """Reverse one purchase's ledger postings before the row is deleted.

    The purchase twin of :func:`reverse_postings_before_delete`, and it exists
    for the identical reason: ``journal_entries.transaction_entry_id`` is ``ON
    DELETE SET NULL``, so once the purchase row is gone the link is severed and
    its legs would be stranded on their ledger accounts with no offsetting
    reversal -- breaking per-account reconciliation and leaving RESIDUE the
    posted walk can only absorb, never explain.  Reversing FIRST leaves the
    original entry and its reversal as an immutable net-zero pair.

    Idempotent no-op for a purchase that never posted (no recorded posting day,
    a card purchase, a non-contributing parent).

    Args:
        entry: The purchase about to be deleted.  Must still be flushed
            (``entry.id`` set) so the reversal can read its posted legs back.
    """
    txn = entry.transaction
    if txn.transfer_id is not None:
        return
    owner_id = txn.pay_period.user_id
    entries = emit_purchase_deltas(
        entry, txn, posted=False, owner_id=owner_id,
    )
    _self_heal_account_anchor_corrections(
        (entry.account_id,), txn.scenario_id, entries,
    )


def reverse_postings_before_delete(txn: Transaction) -> None:
    """Reverse a transaction's ledger postings before the row is deleted.

    The delete-side reconcile every transaction-delete path runs FIRST, while
    ``txn.id`` still exists: it brings the row's WHOLE posted family -- its own
    cash leg and every one of its purchases' -- to zero, emitting a balanced
    reversal entry for whatever the ledger currently holds.  Running it before
    the delete is load-bearing for a HARD delete: ``journal_entries``'
    ``transaction_id`` and ``transaction_entry_id`` are both ``ON DELETE SET
    NULL`` (and ``transaction_entries`` CASCADE from their parent), so once the
    rows are gone the links are severed and the original legs would be stranded
    on their ledger accounts with no offsetting reversal -- breaking per-account
    reconciliation.  Reversing first leaves each original entry and its reversal
    as an immutable net-zero pair (their links SET-NULL together on the delete),
    so every ledger account still nets correctly.  The transaction analog of
    ``transfer_service.delete_transfer``'s ``sync_transfer_postings(xfer,
    settled=False)`` reverse-before-delete.

    **It is NOT ``sync_transaction_postings(txn, settled=False)``, and since
    plan step X-f3b it cannot be.**  That call means "this row has not settled",
    which is a true and ORDINARY state for an envelope whose purchases have
    posted -- a revert must leave them exactly where they are, because the money
    really did leave the bank.  A teardown means something else entirely, so it
    says so rather than borrowing a flag whose meaning stops at the parent's own
    leg.

    Idempotent no-op for a transaction whose family has never posted (a
    Projected row with no posted purchases).  Shared by the delete route
    (``delete_transaction``), the regeneration sweep
    (``recurrence_engine.regenerate_for_template``) and the three payback-delete
    paths (``credit_workflow.delete_payback_on_credit_revert`` /
    ``delete_payback_on_source_delete`` / ``entry_credit_workflow
    .sync_entry_payback``'s DELETE branch) so no delete path can strand a
    posting.  Flushes but does not commit (the caller owns the transaction).

    Args:
        txn: The transaction about to be deleted (soft or hard).  Must still be
            flushed (``txn.id`` set) so the reversal entries can link by
            ``transaction_id`` / ``transaction_entry_id`` and read the
            already-posted legs back.
    """
    # A transfer shadow carries no transaction-sourced postings and no entries;
    # the guard mirrors ``sync_transaction_postings``' so both doors refuse the
    # same row rather than one of them reading ``pay_period`` for a row it will
    # do nothing with.
    if txn.transfer_id is not None:
        return
    owner_id = txn.pay_period.user_id
    entries = _emit_transaction_deltas(txn, settled=False, owner_id=owner_id)
    for purchase in txn.entries:
        entries.extend(
            emit_purchase_deltas(
                purchase, txn, posted=False, owner_id=owner_id,
            )
        )
    _self_heal_account_anchor_corrections(
        (txn.account_id,), txn.scenario_id, entries,
    )


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

    **Its transaction half is no longer settled-only** (ruling **R-FM**, plan
    step X-f3b): it walks every non-transfer row that is settled OR carries a
    purchase with a recorded bank posting day, and passes each row its own
    settled truth.  That is what makes the hook total over the family the ledger
    now holds -- a purchase against a still-Projected envelope is real cash that
    left the bank -- and it is what moves the existing rows onto the new split
    without a backfill: the first deploy after migration ``b7c3d9e1f204``
    reverses the part of each envelope's cash leg its posted purchases now own
    and posts those purchases at their own days, in one balanced pass per row.

    Returns:
        ``(transactions_changed, transfers_changed)`` -- how many sources this
        pass actually re-posted, for the deploy log.
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
            # SETTLED, or carrying a purchase that has posted a leg of its own
            # (ruling **R-FM**, plan step X-f3b).  The second arm is what makes
            # this hook TOTAL over the family the ledger now holds: a purchase
            # against a still-PROJECTED envelope is real cash that left the bank,
            # and without it that envelope's legs would be maintained by
            # per-mutation calls and by nothing else -- the exact gap this
            # function exists to close for transactions.  It is an EXISTS rather
            # than a join so a row with several posted purchases is walked once.
            db.or_(
                Transaction.status_id.in_(settled_ids),
                posted_purchase_exists_clause(),
            ),
        )
        .order_by(Transaction.id)
        .all()
    )
    transactions_changed = sum(
        1 for txn in transactions
        if sync_transaction_postings(
            txn, settled=txn.status_id in settled_ids,
        )
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
    # **A broken PAIR is skipped and reported, not allowed to abort the batch**
    # (developer ruling, 2026-08-17).  This selects transfers by the PARENT's
    # status and tells :func:`sync_transfer_postings` the pair is settled;
    # ``_settle_effective`` then refuses -- correctly -- when the income shadow
    # is not settled or records nothing, which is a Transfer-Invariant-4 drift
    # that ``restore_transfer`` exists to repair.
    #
    # That refusal is right for a SINGLE write path, where a caller asking about
    # one transfer must not get a fabricated figure.  It is wrong for a batch
    # self-heal that walks every row in the database and runs at container start
    # (``scripts/init_database.py``): one repairable row would make the app
    # unbootable for every user, and the operator could not even reach the
    # screen that shows which row it was.  Skipping keeps the failure loud in
    # the log and bounded to the pair that caused it.
    transfers_changed = 0
    skipped: list[int] = []
    for xfer in transfers:
        try:
            if sync_transfer_postings(xfer, settled=True):
                transfers_changed += 1
        except PostingError:
            skipped.append(xfer.id)
    if skipped:
        logger.warning(
            "Cash posting resync skipped %d transfer(s) whose shadow pair is "
            "broken: %s.  Each is a Transfer Invariant 3/4 drift -- repair it "
            "with transfer_service.restore_transfer, then re-run the resync.",
            len(skipped), skipped,
        )

    return transactions_changed, transfers_changed
