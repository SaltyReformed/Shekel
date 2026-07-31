"""Shared balanced-write primitives for the posting ledger (leaf module).

The pieces every ledger WRITER composes -- the leg record
(:class:`_PostingLeg`), the single balanced-write path
(:func:`_emit_balanced_entry`), the description width, and the UTC
civil-date rule (:func:`_utc_civil_date`) -- in a LEAF module below every
writer, so the correction packages can import them without importing
:mod:`app.services.posting_service` itself.

Why a leaf and not the writer module: Build-Order Step 5's effect-time
self-heal makes ``posting_service`` call into
:mod:`app.services.account_posting_service` at its sync tails (a
function-local import; the account package is the higher layer there),
while the account package needs exactly these primitives.  Importing them
FROM ``posting_service`` would close an import cycle
(``posting_service -> account_posting_service -> posting_service``);
holding them in a leaf breaks it structurally -- the same resolution the
accounts blueprint used (``app/routes/accounts/_bp.py``).
``posting_service`` remains the ledger's one PUBLIC surface and re-exports
everything here; only the correction packages
(:mod:`app.services._posting_reconcile`,
:mod:`app.services.account_posting_service`) import this module directly.

Flask-isolated and commit-free like its consumers: flushes so the caller
sees assigned ids; the caller owns the transaction boundary.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.services.posting_reads import PostingError

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


@dataclass(frozen=True)
class _PostingLeg:
    """One signed leg to write into a balanced journal entry.

    The unit the shared balanced-write path (:func:`_emit_balanced_entry`)
    consumes, so the transfer lifecycle, the cash lifecycle, and the loan /
    account correction packages describe their legs the same way.
    ``amount`` is debit-positive / credit-negative; see the
    :mod:`app.services.posting_service` module docstring for the sign
    convention.

    Attributes:
        ledger_account_id: ``budget.ledger_accounts.id`` the leg lands in.
        amount: The signed leg amount (``Decimal``); non-zero (a zero leg is
            refused by ``ck_account_postings_amount_nonzero``).
        posting_kind_id: ``ref.posting_kinds.id`` for the leg's economic
            nature.
    """

    ledger_account_id: int
    amount: Decimal
    posting_kind_id: int


def _emit_balanced_entry(
    entry: JournalEntry, legs: "list[_PostingLeg]"
) -> JournalEntry:
    """Persist a journal entry and its legs, enforcing the balanced invariant.

    The single balanced-write path every posting source shares (Step 2's
    transfers; Step 3's cash; the Step 4 / Step 5 correction packages).
    Validates the two cross-row invariants the deferred
    ``ck_account_postings_balanced`` trigger enforces -- at least two legs,
    and legs summing to zero -- BEFORE the write, so an unbalanced entry
    fails loudly at the call site with a clear message instead of as an
    opaque deferred error at COMMIT.  The service is the first backstop;
    the DB trigger is the second (the house "service + DB backstop"
    pattern).

    Adds the entry with its legs via the ``postings`` relationship cascade
    (one flush assigns the entry id and inserts the legs with their FK) and
    flushes so the caller sees assigned ids.  Does NOT commit.

    Args:
        entry: The unsaved :class:`~app.models.journal_entry.JournalEntry`
            header, with every column already set by the caller.
        legs: The :class:`_PostingLeg` list to attach; balanced by
            construction for every current source.

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


def emit_keyed_delta_entries(
    legs_by_key: "dict[tuple[int, date], list[_PostingLeg]]",
    build_entry: "Callable[[int, date], JournalEntry]",
    log_label: str,
) -> "list[JournalEntry]":
    """Emit one balanced delta entry per ``(pay period, entry date)`` key.

    The ONE emission loop behind every per-key reconcile-to-target writer --
    the transfer sync, the transaction sync, and the loan-payment correction
    reconcile (steps E1a / N-13 re-keyed all three from per-period to
    per-``(pay_period_id, entry_date)``, and three copies of the loop was a
    measured ``duplicate-code`` finding).  Keys are emitted in sorted order
    so a run's entries are deterministic; each entry carries its key's period
    and date (the R2 attribution rule, per-date since E1a: a reversal lands
    at the exact date of the postings it reverses, the target entry at the
    source's settle date -- both of which ARE the key).

    Args:
        legs_by_key: The non-empty delta legs per key (the caller returns
            early on an empty map, keeping its own no-op contract explicit).
        build_entry: Builds the UNSAVED entry header for one key -- the
            caller's closure over its source row (user / scenario / source
            kind / linkage / description).  Called once per key with
            ``(pay_period_id, entry_date)``; this loop owns setting nothing
            on it, so the header stays entirely the caller's.
        log_label: Names the source in the per-entry INFO line (e.g.
            ``"transfer 42"``), so the shared loop logs as informatively as
            the three loops it replaced.

    Returns:
        The persisted delta entries, in key order.

    Raises:
        PostingError: From :func:`_emit_balanced_entry`, if a key's legs do
            not balance (impossible for a reconcile's per-key deltas -- both
            sides of a key sum to zero -- so a raise here means the caller's
            targets are broken).
    """
    entries = []
    for (period_id, entry_date), legs in sorted(legs_by_key.items()):
        entry = build_entry(period_id, entry_date)
        _emit_balanced_entry(entry, legs)
        logger.info(
            "Posted %s ledger deltas %s in period %d dated %s as journal "
            "entry %d",
            log_label,
            {leg.ledger_account_id: leg.amount for leg in legs},
            period_id,
            entry_date.isoformat(),
            entry.id,
        )
        entries.append(entry)
    return entries
