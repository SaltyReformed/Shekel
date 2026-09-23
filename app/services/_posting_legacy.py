"""The LEGACY one-entry ``transfer`` posting source (leaf; deleted at ``X-bi-6-5``).

Through plan step ``balance:X-bi-6-3`` the ledger booked a settled transfer as
ONE journal entry ``{from -figure, to +figure}`` under the ``transfer`` source,
keyed ``journal_entries.transfer_id``.  Rulings **R-BAL45** and **R-BAL101**
replaced it with two per-movement entries against the owner's
Transfers-in-transit account, posted by the movement writer
(:mod:`app.services._posting_purchases`); what remains of the one-entry shape
is the arm that reverses it to zero -- once, on the deploy resync -- and the
clause that finds every transfer it ever posted for.

**Why a module of its own.**  The arm has ONE lifetime: plan step
``X-bi-6-5`` drops ``journal_entries.transfer_id`` and deletes this module
whole, rather than picking the legacy arm back out of the writer.  Leaf 3b of
``balance:X-bi-6-3`` split it out of :mod:`app.services.posting_service` when
that module reached the 1000-line gate, as a pure move.

:mod:`app.services.posting_service` stays the ledger's one public surface: the
pair's door (``sync_transfer_postings``) and the deploy resync
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
        .filter(
            JournalEntry.transfer_id == Transfer.id,
            JournalEntry.source_kind_id
            == ref_cache.posting_source_id(PostingSourceEnum.TRANSFER),
        )
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
    legacy_source_id = ref_cache.posting_source_id(PostingSourceEnum.TRANSFER)
    return emit_source_deltas(
        targets={},
        source_filter=db.and_(
            JournalEntry.transfer_id == xfer.id,
            JournalEntry.source_kind_id == legacy_source_id,
        ),
        kind_id=ref_cache.posting_kind_id(PostingKindEnum.TRANSFER),
        build_entry=source_entry_builder(
            user_id=xfer.user_id,
            scenario_id=xfer.scenario_id,
            source_kind_id=legacy_source_id,
            description=_transfer_description(xfer),
            transfer_id=xfer.id,
        ),
        log_label=f"transfer {xfer.id} (legacy one-entry source: none)",
    )
