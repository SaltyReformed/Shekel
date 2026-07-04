"""Shared reconcile primitives for the correction-posting packages.

The pieces :mod:`app.services.loan_posting_service` (Build-Order Step 4 +
the read switch) and :mod:`app.services.account_posting_service` (Build-Order
Step 5) genuinely share, extracted here -- from the loan package's former
``_common`` module -- so neither package re-spells them (a ``duplicate-code``
finding) and both agree exactly:

* :func:`account_owner_id` -- resolve any account's owner id (both packages
  need it for the per-account equity ledger and the entry header).
* :func:`summed_posting_legs` -- the grouped "what is already posted" query
  shape every posted-leg reader shares.
* :func:`posted_correction_legs` -- the posted anchor-correction reader,
  keyed by ``(source_kind_id, entry_date)`` and scoped to entries touching
  one linked ledger.
* :func:`delta_legs` -- turn a ``target`` ledger-leg map and the ``posted``
  ledger-leg map into the balanced DELTA legs that move posted to target.
* :func:`merge_target_legs` -- sum a correction's legs into a target bucket
  (the same-key merge both anchor reconciles apply).
* :func:`emit_anchor_correction_entry` -- the one definition of what a
  sourceless anchor-correction journal entry looks like (both concrete FKs
  NULL, dated at the anchor's civil date, described from its source kind).

Flask-isolated and commit-free like its consumers: plain data in, ORM
objects or plain values out; flushes only through the shared balanced-write
path; the caller owns the transaction boundary.
"""

import logging
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PostingSourceEnum
from app.extensions import db
from app.models.account import Account
from app.models.journal_entry import JournalEntry, Posting
from app.services.posting_reads import PostingError
from app.services._posting_write import (
    _MAX_DESCRIPTION_LENGTH,
    _PostingLeg,
    _emit_balanced_entry,
)

logger = logging.getLogger(__name__)

_ZERO_MONEY = Decimal("0.00")

# The anchor-correction reconcile key: (journal ``source_kind_id``, civil
# ``entry_date``).  Shared by the loan and account anchor reconciles -- an
# anchor correction has no concrete source FK to key on, so both key each
# correction's entry by its source kind (opening vs. true-up) and its date.
CorrectionKey = tuple[int, date]
# The target/posted leg map: {ledger_account_id: (signed amount, posting_kind_id)}.
LegMap = dict[int, tuple[Decimal, int]]


def account_owner_id(account_id: int) -> int | None:
    """Return an account's owner (``auth.users.id``), or ``None`` if absent.

    The shared owner resolver for the correction-posting packages: the loan
    anchor reconcile and all-scenarios sync need the loan account's owner
    (its per-loan equity account, its pay periods, its baseline scenario),
    and the account anchor reconcile needs the non-loan account's owner (its
    ``anchor_equity`` twin and the entry header) -- the identical query, an
    amortizing loan being an :class:`~app.models.account.Account` row like
    any other.  ``None`` only for a missing / deleted account, which every
    caller treats as "nothing to reconcile".

    Args:
        account_id: The account whose owner to resolve.

    Returns:
        The owner's user id, or ``None`` when the account row is absent.
    """
    return (
        db.session.query(Account.user_id)
        .filter(Account.id == account_id)
        .scalar()
    )


def summed_posting_legs(extra_columns: list, filters: list):
    """Return a grouped query summing each ledger's posted amount and kind.

    The shared shape of the "what is already posted" readers -- the loan
    payment-correction reader
    (:func:`app.services.loan_posting_service._payments._posted_loan_payment_legs`)
    and, via :func:`posted_correction_legs`, both anchor-correction readers --
    so no consumer re-spells the ``SUM(amount) GROUP BY ledger, kind`` join (a
    ``duplicate-code`` finding).  Sums ``Posting.amount`` per ledger account
    (carrying its single posting kind, since a correction ledger always holds
    one kind), joined to the owning :class:`JournalEntry`.

    Args:
        extra_columns: Extra group-key columns prepended to the SELECT and
            GROUP BY (empty for the payment reader; the anchor readers add
            ``source_kind_id`` and ``entry_date`` to sub-group by correction).
        filters: The entry-scoping filter expressions.

    Returns:
        A SQLAlchemy ``Query``, NOT executed.  Each row unpacks as
        ``(*extra_columns, ledger_account_id, net_amount, posting_kind_id)``.
    """
    return (
        db.session.query(
            *extra_columns,
            Posting.ledger_account_id,
            db.func.sum(Posting.amount),
            Posting.posting_kind_id,
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(*filters)
        .group_by(
            *extra_columns,
            Posting.ledger_account_id,
            Posting.posting_kind_id,
        )
    )


def posted_correction_legs(
    linked_ledger_id: int, scenario_id: int, source_kind_ids: list[int],
) -> dict[CorrectionKey, LegMap]:
    """Return the posted anchor-correction legs on one linked ledger, by key.

    Sums ``account_postings.amount`` over every journal entry in
    *scenario_id* whose source kind is one of *source_kind_ids* and that
    touches the given LINKED ledger (which scopes the query to that ledger's
    one account, the linked ledger being per-account -- and every anchor
    correction carries a linked leg, so no correction escapes the scope),
    grouped by ``(source_kind_id, entry_date, ledger_account_id,
    posting_kind_id)``.  This is the "already posted" side the anchor
    reconciles compare their targets against, read straight from the ledger
    so a reversal negates exactly what was posted and reuses the kind it was
    posted with.

    Args:
        linked_ledger_id: The account's LINKED ledger account id whose
            corrections to sum.
        scenario_id: The budget scenario to scope to.
        source_kind_ids: The correction source-kind ids to select (the loan
            or account opening / true-up pair).

    Returns:
        ``{(source_kind_id, entry_date): {ledger_account_id: (net, kind_id)}}``;
        empty when no correction is posted yet.
    """
    entry_ids = (
        db.session.query(Posting.journal_entry_id)
        .filter(Posting.ledger_account_id == linked_ledger_id)
    )
    rows = summed_posting_legs(
        [JournalEntry.source_kind_id, JournalEntry.entry_date],
        [
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.source_kind_id.in_(source_kind_ids),
            JournalEntry.id.in_(entry_ids),
        ],
    ).all()
    posted: dict[CorrectionKey, LegMap] = {}
    for source_kind_id, entry_date, ledger_id, net, kind_id in rows:
        posted.setdefault((source_kind_id, entry_date), {})[ledger_id] = (
            net, kind_id,
        )
    return posted


def delta_legs(target: LegMap, posted: LegMap) -> list[_PostingLeg]:
    """Return the balanced delta legs bringing *posted* to *target*.

    For each ledger account touched by either side, the leg amount is
    ``target_amount - posted_amount``; a zero delta is dropped (no leg written).
    Because *target* sums to zero by construction and every *posted* entry is
    balanced, the non-zero deltas also sum to zero -- so the result is either
    empty (already at target, an idempotent no-op) or has ``>= 2`` legs that
    :func:`app.services.posting_service._emit_balanced_entry` accepts.

    The kind labels each leg's economic nature: a leg present on the target side
    takes its target kind; a leg present ONLY on the posted side (a component the
    new target dropped to zero) is reversed with the kind it was posted under, so
    a reversal never loses the kind it is undoing.

    Args:
        target: ``{ledger_account_id: (amount, posting_kind_id)}`` the ledger
            should net to (empty to reverse everything to zero).
        posted: ``{ledger_account_id: (net_amount, posting_kind_id)}`` currently
            posted (empty when nothing is posted yet).

    Returns:
        The balanced :class:`~app.services.posting_service._PostingLeg` deltas
        (empty when *posted* already equals *target*).
    """
    legs: list[_PostingLeg] = []
    for ledger_id in sorted(set(target) | set(posted)):
        target_amount, target_kind = target.get(ledger_id, (_ZERO_MONEY, None))
        posted_amount, posted_kind = posted.get(ledger_id, (_ZERO_MONEY, None))
        delta = target_amount - posted_amount
        if delta == 0:
            continue
        kind_id = target_kind if target_kind is not None else posted_kind
        legs.append(_PostingLeg(ledger_id, delta, kind_id))
    return legs


def merge_target_legs(bucket: LegMap, legs: LegMap) -> None:
    """Sum a correction's legs into a per-key target *bucket*, in place.

    The same-key merge both anchor reconciles apply when two same-day
    same-kind anchors share one reconcile key: each ledger's amounts add, so
    the merged target expresses the anchors' combined jump (landing on the
    later anchor's value, since each correction's delta already accounts for
    the prior one).  The posting kind is shared within a key by construction
    (one key holds one source kind, whose legs all carry that kind), so the
    incoming kind simply wins.

    Args:
        bucket: The key's accumulated ``{ledger_account_id: (amount,
            posting_kind_id)}`` target, mutated in place.
        legs: One correction's balanced legs to add (may be empty for a
            correction that books nothing).
    """
    for ledger_id, (amount, kind_id) in legs.items():
        prev_amount, _prev_kind = bucket.get(ledger_id, (_ZERO_MONEY, kind_id))
        bucket[ledger_id] = (prev_amount + amount, kind_id)


def _correction_description(source_kind_id: int, entry_date: date) -> str:
    """Return the human label for an anchor-correction entry (display only).

    ``"<Loan|Account> <opening balance|balance true-up> as of <date>"``,
    resolved from the entry's source kind and truncated to the description
    column width.  Never read for logic.

    Args:
        source_kind_id: The entry's journal source kind id (one of the four
            anchor-correction kinds).
        entry_date: The correction's civil date.

    Returns:
        The truncated description string.

    Raises:
        PostingError: If *source_kind_id* is not one of the four
            anchor-correction source kinds -- a caller emitting a correction
            entry under a non-correction source is a broken invariant, so it
            fails loudly rather than fabricating a label.
    """
    labels = {
        ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING):
            "Loan opening balance",
        ref_cache.posting_source_id(PostingSourceEnum.LOAN_TRUEUP):
            "Loan balance true-up",
        ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING):
            "Account opening balance",
        ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_TRUEUP):
            "Account balance true-up",
    }
    label = labels.get(source_kind_id)
    if label is None:
        raise PostingError(
            f"Source kind {source_kind_id} is not an anchor-correction "
            f"source; no correction entry may be emitted under it."
        )
    return (
        f"{label} as of {entry_date.isoformat()}"
    )[:_MAX_DESCRIPTION_LENGTH]


def emit_anchor_correction_entry(
    owner_id: int,
    scenario_id: int,
    key: CorrectionKey,
    pay_period_id: int,
    legs: list,
) -> JournalEntry:
    """Emit one balanced anchor-correction delta entry (opening or true-up).

    The one definition of a sourceless correction's journal header, shared by
    the loan and account anchor reconciles: ``transfer_id`` /
    ``transaction_id`` both NULL (an anchor correction links to neither;
    ``source_kind_id`` disambiguates it), dated at the anchor's civil
    ``entry_date``, attributed to *pay_period_id*, and described from its
    source kind (:func:`_correction_description`).  Writes the balanced
    *legs* through the shared balanced-write path.  Flushes; does not commit.

    Args:
        owner_id: The account owner's user id.
        scenario_id: The budget scenario the correction lives in.
        key: The ``(source_kind_id, entry_date)`` the delta reconciles.
        pay_period_id: The resolved pay period for the NOT NULL
            ``pay_period_id`` (the loan reconcile resolves the period
            containing the anchor date; the account reconcile uses the
            anchor history row's own period).
        legs: The balanced delta legs from :func:`delta_legs`.

    Returns:
        The persisted delta :class:`~app.models.journal_entry.JournalEntry`.

    Raises:
        PostingError: If *key* names a non-correction source kind, or the
            legs do not balance (via the shared balanced-write path).
    """
    source_kind_id, entry_date = key
    entry = JournalEntry(
        user_id=owner_id,
        scenario_id=scenario_id,
        pay_period_id=pay_period_id,
        entry_date=entry_date,
        source_kind_id=source_kind_id,
        transfer_id=None,
        transaction_id=None,
        description=_correction_description(source_kind_id, entry_date),
    )
    _emit_balanced_entry(entry, legs)
    logger.info(
        "Posted anchor correction (source %d as of %s) as journal entry %d",
        source_kind_id, entry_date, entry.id,
    )
    return entry
