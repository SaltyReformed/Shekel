"""The LEGACY one-entry ``transfer`` posting source (leaf; deleted at ``X-bi-6-5``).

Through plan step ``balance:X-bi-6-3`` the ledger booked a settled transfer as
ONE journal entry ``{from -figure, to +figure}`` under the ``transfer`` source,
keyed ``journal_entries.transfer_id``.  Rulings **R-BAL45** and **R-BAL101**
replaced it with two per-movement entries against the owner's
Transfers-in-transit account, posted by the movement writer
(:mod:`app.services._posting_purchases`); what remains of the one-entry shape
is the arm that reverses it to zero -- once, on the deploy resync -- the
clause that finds every transfer it ever posted for, and the reader the deploy
resync's refusal asks whether any of them still holds a net (ruling
**R-BAL104**): the account walk reads no ``transfer_id``-linked entry, so a
legacy net left standing is money a later true-up cannot see.

**Why a module of its own.**  The arm has ONE lifetime: plan step
``X-bi-6-5`` drops ``journal_entries.transfer_id`` and deletes this module
whole, rather than picking the legacy arm back out of the writer.  Leaf 3b of
``balance:X-bi-6-3`` split it out of :mod:`app.services.posting_service` when
that module reached the 1000-line gate, as a pure move.

:mod:`app.services.posting_service` stays the ledger's one public surface: the
pair's door (``sync_transfer_postings``), its teardown twin
(``reverse_transfer_postings_before_delete``) -- both through the re-book half
``_rebook_transfer_family`` -- and the deploy resync
(``resync_all_cash_postings``) call in here, and nothing else does.
Flask-isolated and commit-free like its consumers: flushes, never commits.
"""

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry
from app.models.transfer import Transfer
from app.services._posting_write import (
    _MAX_DESCRIPTION_LENGTH,
    emit_source_deltas,
    posted_by_period,
    source_entry_builder,
)


def _transfer_description(xfer: Transfer) -> str:
    """Return the human label of a transfer's LEGACY one-entry journal entry.

    ``"Transfer: <from> to <to>"``, truncated to the description column
    width, matching the Commit-3 backfill byte-for-byte.  Since plan step
    ``balance:X-bi-6-3`` only the legacy source's REVERSAL entries carry it
    (:func:`reverse_legacy_transfer_entry`); a per-movement entry carries its
    movement's own description, as every movement does.  Display only --
    never read for logic.

    Args:
        xfer: The transfer whose legacy entry is being reversed (its
            ``from_account`` / ``to_account`` relationships supply the names).

    Returns:
        The truncated description string.
    """
    return (
        f"Transfer: {xfer.from_account.name} to {xfer.to_account.name}"
    )[:_MAX_DESCRIPTION_LENGTH]


def _legacy_source_kind_id() -> int:
    """Return the ``ref.posting_sources`` id of the LEGACY ``transfer`` source.

    The one place the legacy source's KIND is named, so the kind the reversal
    STAMPS on its entries (:func:`reverse_legacy_transfer_entry`) and the kind
    every reader FILTERS on (:func:`_legacy_source_filter`) cannot drift
    apart: were they ever to differ, the reversal's own entries would not be
    read back, every sync would reverse again, and the deploy's refusal would
    fire on every boot.

    Returns:
        The stored id of :attr:`PostingSourceEnum.TRANSFER`.
    """
    return ref_cache.posting_source_id(PostingSourceEnum.TRANSFER)


def _legacy_source_filter(transfer_id):
    """Return the filter selecting one transfer's LEGACY one-entry postings.

    The ONE statement of "this transfer's legacy source": its
    ``journal_entries.transfer_id`` link AND the ``transfer`` source kind
    (the kind as well as the link, the rule
    :func:`~app.services._posting_write.posted_by_period` states for every
    source).  :func:`reverse_legacy_transfer_entry` reconciles what it selects
    to zero, :func:`transfers_holding_a_legacy_net` reads the same selection
    back for the deploy's refusal, and :func:`legacy_transfer_entry_exists_clause`
    asks whether it selects anything -- so the refusal cannot ask about a
    different set of entries than the reversal answered for.

    Args:
        transfer_id: The transfer's id, or the ``Transfer.id`` column for a
            clause correlated to ``Transfer``.

    Returns:
        A SQLAlchemy filter expression over ``JournalEntry``.
    """
    return db.and_(
        JournalEntry.transfer_id == transfer_id,
        JournalEntry.source_kind_id == _legacy_source_kind_id(),
    )


def legacy_transfer_entry_exists_clause():
    """Return the SQL form of "this transfer holds a legacy one-entry posting".

    An ``EXISTS`` over ``budget.journal_entries`` carrying the transfer's
    ``transfer_id`` under the legacy ``transfer`` source kind, correlated to
    ``Transfer`` -- reversed pairs included, so the deploy resync keeps
    walking a re-booked transfer (a no-op) rather than deciding from a net
    whether its residue is clean.  Goes with the column at ``X-bi-6-5``.

    Returns:
        A SQLAlchemy ``EXISTS`` clause, correlated to ``Transfer``.
    """
    return (
        db.session.query(JournalEntry.id)
        .filter(_legacy_source_filter(Transfer.id))
        .exists()
    )


def reverse_legacy_transfer_entry(xfer: Transfer) -> "list[JournalEntry]":
    """Bring the LEGACY one-entry ``transfer`` source for *xfer* to zero.

    The transfer analog of
    :func:`app.services.posting_service._emit_transaction_deltas` and the same
    shape: an EMPTY target over the source's own ``(period, entry date)``
    keys, read back from the ledger by ``transfer_id`` under the ``transfer``
    source kind, so whatever the one-entry shape posted before plan step
    ``balance:X-bi-6-3`` is reversed at its own date -- once, by the deploy
    resync -- and a transfer this source never touched, or one already at
    zero, emits nothing.  The reversal's header carries the legacy source kind
    and link so the pair nets to zero under the same key every reader groups
    by; ``journal_entries.transfer_id`` and this arm go at ``X-bi-6-5``.

    Args:
        xfer: The transfer whose legacy entry to reverse.

    Returns:
        The emitted reversal entries; ``[]`` when the ledger holds nothing
        for this source.
    """
    return emit_source_deltas(
        targets={},
        source_filter=_legacy_source_filter(xfer.id),
        kind_id=ref_cache.posting_kind_id(PostingKindEnum.TRANSFER),
        build_entry=source_entry_builder(
            user_id=xfer.user_id,
            scenario_id=xfer.scenario_id,
            source_kind_id=_legacy_source_kind_id(),
            description=_transfer_description(xfer),
            transfer_id=xfer.id,
        ),
        log_label=f"transfer {xfer.id} (legacy one-entry source: none)",
    )


def transfers_holding_a_legacy_net() -> "list[int]":
    """Return every transfer whose LEGACY source still nets nonzero on some key.

    The question ruling **R-BAL104** (developer 2026-09-22) makes the deploy
    resync ask after its loop: does any transfer still hold a NONZERO legacy
    one-entry posting on any ``(pay period, entry date)``?  The account walk
    reads no ``transfer_id``-linked entry (the fence until ``X-bi-6-5``), so
    such a net is money a later true-up books a correction for without seeing
    -- Checking opens ``$1,000.00``, a ``$100.00`` transfer the resync skipped
    keeps its old entry ``{Checking -100, Savings +100}``, a true-up to
    ``$900.00`` books ``-$100.00`` and the posted ledger reads ``$800.00``.

    It reads every transfer ROW the legacy source ever posted for, a
    soft-deleted one included (:func:`legacy_transfer_entry_exists_clause`;
    a HARD delete sets ``journal_entries.transfer_id`` NULL, and the walk's
    residue loader reads those entries, so they are not money it cannot
    see), back through
    :func:`~app.services._posting_write.posted_by_period` under
    :func:`_legacy_source_filter` -- the SAME producer and the same filter
    :func:`reverse_legacy_transfer_entry` reconciles to zero -- so a transfer
    is named here exactly when its reversal is not at target, per
    ``(period, date)`` and not in total: a pre-E1a settle / reversal pair
    straddling two days nets zero in total and is still a net on each day.
    It measures the ledger rather than trusting the loop that just ran, so
    it does not rest on the argument that only a skipped transfer can hold
    one.  Goes with this module at ``X-bi-6-5``.

    **What a refusal costs, and what gates it** (ruling **R-BAL105**, which
    amends R-BAL104's "the deploy auto-rolls back", developer 2026-09-22).
    It can fire only on a deploy that re-books the legacy shape: a re-booked
    legacy source nets zero on every key, and the one writer that posts a
    net under it again is a rollback's OLD image, after which the next deploy
    re-applies the migration.  That deploy has already COMMITTED migration
    ``c7d1e9a4b2f8`` when the resync runs, and the previous image cannot
    resolve it, so ``deploy/shekel-deploy.sh`` refuses to re-pin that image:
    the container crash-loops on this refusal and the site is down until an
    operator intervenes -- the ruled recovery is restoring the pre-deploy dump
    the script names.  The gate that meets it first is the release rehearsal
    on a same-day dump (a refusal there means do not deploy); plan step
    ``X-cv`` (a future step, unshipped) will make a deploy all-or-nothing, so
    a hook refusal leaves the stamp unmoved and the previous image re-pins on
    its own.  Measured 2026-09-22: zero holders
    after the re-book on both production dumps' rehearsals.

    Returns:
        The transfer ids, ascending; ``[]`` when every legacy entry is netted
        to zero on its own key.
    """
    holders = (
        db.session.query(Transfer.id)
        .filter(legacy_transfer_entry_exists_clause())
        .order_by(Transfer.id)
        .all()
    )
    return [
        transfer_id
        for (transfer_id,) in holders
        if any(
            net != 0
            for nets in posted_by_period(
                _legacy_source_filter(transfer_id),
            ).values()
            for net in nets.values()
        )
    ]
