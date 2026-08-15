"""Shared balanced-write primitives for the posting ledger (leaf module).

The pieces every ledger WRITER composes -- the leg record
(:class:`_PostingLeg`), the single balanced-write path
(:func:`_emit_balanced_entry`) and the description width -- in a LEAF module
below every writer, so the correction packages can import them without
importing :mod:`app.services.posting_service` itself.  It also held the entry's
civil-date rule as ``_utc_civil_date`` until ruling R-DH (2026-07-31) moved that
day to the USER's timezone; the derivation now lives once in
:func:`app.utils.balance_predicates.settled_day`, which every writer and both folds
share.

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


def posted_by_period(source_filter) -> "dict[tuple[int, date], dict[int, Decimal]]":
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
            journal entries (by ``transfer_id``, ``transaction_id`` or
            ``transaction_entry_id``).  The three are disjoint by construction
            -- every entry sets exactly one -- so a source's reconcile never
            reads back a sibling's legs.

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


def reconcile_periods(
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
        posted: The :func:`posted_by_period` net map.
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


def emit_source_deltas(
    *,
    targets: "dict[tuple[int, date], dict[int, Decimal]]",
    source_filter,
    kind_id: int,
    build_entry: "Callable[[int, date], JournalEntry]",
    log_label: str,
) -> "list[JournalEntry]":
    """Reconcile ONE source's posted legs to *targets*, emitting the deltas.

    **The reconcile-to-target algorithm itself, stated once.**  Read back what
    the source has already posted per ``(pay period, entry date)``, take the
    per-ledger-account difference against what it should hold, and emit one
    balanced entry per key that differs -- writing nothing at all when it is
    already at target.  Every writer in the ledger is that sentence over a
    different target: the transfer sync, the transaction sync and the purchase
    sync (plan step X-f3b, ruling **R-FM**), which is when three copies of the
    five-line body became a measured ``duplicate-code`` finding.

    What a caller still owns is the only thing that differs -- WHAT the target
    is.  Everything downstream of that is here.

    Args:
        targets: ``{(pay_period_id, entry_date): {ledger_account_id: amount}}``
            the ledger should net to; EMPTY to reverse the source to zero.
        source_filter: The SQLAlchemy filter selecting this source's journal
            entries (see :func:`posted_by_period`).
        kind_id: The posting kind stamped on every emitted leg.
        build_entry: The entry-header closure
            (:func:`source_entry_builder`).
        log_label: Names the source in the per-entry INFO line.

    Returns:
        The emitted delta entries, or ``[]`` when the ledger is already at
        target (the idempotent no-op every caller's contract promises).
    """
    legs_by_key = reconcile_periods(
        targets, posted_by_period(source_filter), kind_id,
    )
    if not legs_by_key:
        return []
    return emit_keyed_delta_entries(legs_by_key, build_entry, log_label)


def source_entry_builder(
    *,
    user_id: int,
    scenario_id: int,
    source_kind_id: int,
    description: str,
    **linkage: int,
) -> "Callable[[int, date], JournalEntry]":
    """Return the ``build_entry`` closure for ONE source's delta entries.

    The ONE statement of what a source-linked journal entry header IS, for the
    three writers that emit one: the transfer sync, the transaction sync and the
    purchase sync (plan step X-f3b, ruling **R-FM**).  They differed in exactly
    three things -- the source KIND, which concrete FK carries the linkage, and
    the human description -- and stated the other five fields three times, which
    pylint's ``duplicate-code`` measured the moment the third arrived.

    **The per-key fields are this closure's and the rest are the caller's**:
    ``pay_period_id`` and ``entry_date`` ARE the reconcile key (the R2
    attribution rule, per-date since plan step E1a), so they arrive per call and
    nothing else does.

    Args:
        user_id: The owning user (tenancy).
        scenario_id: The scenario the source lives in.
        source_kind_id: ``ref.posting_sources.id`` for this source's kind.
        description: The human label, already truncated by the caller to
            :data:`_MAX_DESCRIPTION_LENGTH` -- the column's own width, applied
            where the label is composed rather than here, so a caller cannot be
            handed a silently-clipped string it thinks it chose.
        **linkage: The ONE concrete source FK this kind sets, by keyword --
            ``transfer_id``, ``transaction_id`` or ``transaction_entry_id``.
            Passed through verbatim, so adding a source kind adds a keyword
            rather than a branch here.

    Returns:
        A callable taking ``(pay_period_id, entry_date)`` and returning the
        UNSAVED :class:`~app.models.journal_entry.JournalEntry` header.
    """
    def build(period_id: int, entry_date: date) -> JournalEntry:
        """Build one delta entry header for its ``(period, date)`` key."""
        return JournalEntry(
            user_id=user_id,
            scenario_id=scenario_id,
            pay_period_id=period_id,
            entry_date=entry_date,
            source_kind_id=source_kind_id,
            description=description,
            **linkage,
        )

    return build


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
