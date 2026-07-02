"""Loan-anchor correction posting: the opening and true-up reconcile.

Posts a loan's genesis balance corrections -- the once-per-loan OPENING and every
user balance TRUE-UP -- into the append-only double-entry ledger, so the loan's
confirmed balance is fully reconstructable as ``-(sum of its linked postings)``
with no external anchor read (the foundation the read switch and Step-5 reporting
move onto).

Every anchor a loan carries posts one balanced correction (:mod:`._walk` computes
its ``owed_before``)::

    loan-linked ledger   (owed_before - anchor_balance)   [opening | trueup]
    opening-equity ledger (anchor_balance - owed_before)  [opening | trueup]
                          -------------------------------
                          0

The origination anchor's ``owed_before`` is zero, so its correction is the
opening ``-original_principal`` onto the loan and ``+original_principal`` onto its
per-loan opening-equity (Equity) account.  A user-trueup's correction is
``owed_before - verified`` -- the append-only jump that reproduces the resolver's
true-up without editing any prior posting.

**Reconciled to target, keyed by (source kind, entry date).**  Unlike a payment
correction there is no shadow ``transaction_id`` to key on, so the reconcile keys
each anchor's entry by its ``source_kind_id`` (opening vs. true-up) and its
``entry_date`` (the anchor date), scoped to the loan's linked ledger.  A change
to a pre-true-up payment moves a true-up's ``owed_before``; re-running the sync
re-derives the target and posts the balancing delta, so a stale true-up
self-heals.  Flushes but never commits -- the caller owns the transaction.
"""

import logging
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import (
    LedgerAccountKindEnum,
    LoanAnchorSourceEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.pay_period import PayPeriod
from app.services import account_projection, ledger_account_service
from app.services.posting_service import (
    _MAX_DESCRIPTION_LENGTH,
    PostingError,
    _emit_balanced_entry,
    _ledger_account_for,
)

from ._common import delta_legs, loan_owner_id, summed_posting_legs
from ._walk import LoanAnchorCorrection, walk_loan_ledger

logger = logging.getLogger(__name__)

# The correction key: (journal ``source_kind_id``, civil ``entry_date``).
_CorrectionKey = tuple[int, date]
# The target/posted leg map: {ledger_account_id: (signed amount, posting_kind_id)}.
_LegMap = dict[int, tuple[Decimal, int]]


def _anchor_correction_kinds(
    anchor: LoanAnchorEvent,
) -> tuple[PostingSourceEnum, PostingKindEnum]:
    """Return the (journal source kind, posting leg kind) for an anchor's correction.

    The origination anchor books the loan's OPENING (source ``loan_opening``, leg
    kind ``opening``); every other anchor is a user balance assertion and books a
    TRUE-UP (source ``loan_trueup``, leg kind ``trueup``).  Keyed off the anchor's
    ``source_id`` compared to the ``origination`` ref id -- the only two seeded
    anchor sources are ``origination`` and ``user_trueup``, so "not origination"
    is exactly "user-trueup".

    Args:
        anchor: The anchor event whose correction kinds to resolve.

    Returns:
        ``(PostingSourceEnum, PostingKindEnum)`` -- ``(LOAN_OPENING, OPENING)`` for
        the origination anchor, else ``(LOAN_TRUEUP, TRUEUP)``.
    """
    origination_source_id = ref_cache.loan_anchor_source_id(
        LoanAnchorSourceEnum.ORIGINATION,
    )
    if anchor.source_id == origination_source_id:
        return PostingSourceEnum.LOAN_OPENING, PostingKindEnum.OPENING
    return PostingSourceEnum.LOAN_TRUEUP, PostingKindEnum.TRUEUP


def _loan_anchor_correction_target(
    correction: LoanAnchorCorrection, owner_id: int,
) -> _LegMap:
    """Build the two-leg target for one anchor correction, or empty when it books nothing.

    The loan-linked leg is ``owed_before - anchor_balance`` (tagged ``opening`` or
    ``trueup``); the per-loan opening-equity leg is its negative, so the two sum
    to zero and the ledger's implied ``owed`` moves from ``owed_before`` to the
    verified value.  A correction whose ``owed_before`` already equals the anchor
    balance (a true-up that matches the walked balance) books NOTHING -- an empty
    target, so no zero leg is written and no opening-equity account is minted for
    it.

    The per-loan opening-equity account is resolved lazily (created on first use)
    only when the correction is non-zero, via
    :func:`app.services.ledger_account_service.get_or_create_loan_ledger_account`.

    Args:
        correction: The anchor correction from :func:`walk_loan_ledger`.
        owner_id: The loan owner's user id (for the per-loan equity account).

    Returns:
        ``{ledger_account_id: (amount, posting_kind_id)}`` (the two balanced legs,
        or empty when the correction books nothing).

    Raises:
        PostingError: If the loan account has no linked ledger account (a broken
            chart-of-accounts pairing).
    """
    anchor = correction.anchor
    loan_account_id = anchor.account_id
    verified = Decimal(str(anchor.anchor_balance))
    linked_amount = correction.owed_before - verified
    if linked_amount == 0:
        return {}
    _source_enum, posting_kind_enum = _anchor_correction_kinds(anchor)
    posting_kind_id = ref_cache.posting_kind_id(posting_kind_enum)
    linked = _ledger_account_for(loan_account_id)
    equity = ledger_account_service.get_or_create_loan_ledger_account(
        owner_id, loan_account_id, LedgerAccountKindEnum.EQUITY_OPENING,
    )
    return {
        linked.id: (linked_amount, posting_kind_id),
        equity.id: (-linked_amount, posting_kind_id),
    }


def _anchor_correction_targets(
    corrections: list[LoanAnchorCorrection], owner_id: int,
) -> dict[_CorrectionKey, _LegMap]:
    """Merge a loan's anchor corrections into per-(source, date) target legs.

    Groups every anchor correction by its ``(source_kind_id, anchor_date)`` key
    and sums the legs within each group, so two same-day same-kind anchors (the
    unique index permits two true-ups on one day with different balances) net to a
    single balanced target that lands owed on the LATER value -- exactly the
    combined jump they express.  A correction that books nothing still creates its
    key with an empty leg map, so an entry it previously posted (now matching) is
    reversed to zero by the reconcile.

    Args:
        corrections: The loan's anchor corrections from :func:`walk_loan_ledger`.
        owner_id: The loan owner's user id.

    Returns:
        ``{(source_kind_id, entry_date): {ledger_account_id: (amount, kind_id)}}``.
    """
    target: dict[_CorrectionKey, _LegMap] = {}
    for correction in corrections:
        source_enum, _posting_kind = _anchor_correction_kinds(correction.anchor)
        key = (
            ref_cache.posting_source_id(source_enum),
            correction.anchor.anchor_date,
        )
        bucket = target.setdefault(key, {})
        legs = _loan_anchor_correction_target(correction, owner_id)
        for ledger_id, (amount, kind_id) in legs.items():
            prev_amount, _prev_kind = bucket.get(
                ledger_id, (Decimal("0.00"), kind_id),
            )
            bucket[ledger_id] = (prev_amount + amount, kind_id)
    return target


def _posted_loan_anchor_correction_legs(
    loan_account_id: int, scenario_id: int,
) -> dict[_CorrectionKey, _LegMap]:
    """Return the loan's posted anchor-correction legs, keyed by (source, date).

    Sums ``account_postings.amount`` over every ``loan_opening`` / ``loan_trueup``
    journal entry in *scenario_id* that touches the loan's LINKED ledger (which
    scopes the query to THIS loan, the linked ledger being per-account), grouped
    by ``(source_kind_id, entry_date, ledger_account_id, posting_kind_id)``.  This
    is the "already posted" side the reconcile (:func:`sync_loan_anchor_corrections`)
    compares the target against, read straight from the ledger so a reversal
    negates exactly what was posted and reuses the kind it was posted with.

    Args:
        loan_account_id: The loan whose posted anchor corrections to sum.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``{(source_kind_id, entry_date): {ledger_account_id: (net, kind_id)}}``;
        empty when no opening / true-up is posted yet.
    """
    opening_source_id = ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING)
    trueup_source_id = ref_cache.posting_source_id(PostingSourceEnum.LOAN_TRUEUP)
    linked = _ledger_account_for(loan_account_id)
    loan_entry_ids = (
        db.session.query(Posting.journal_entry_id)
        .filter(Posting.ledger_account_id == linked.id)
    )
    rows = summed_posting_legs(
        [JournalEntry.source_kind_id, JournalEntry.entry_date],
        [
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.source_kind_id.in_(
                [opening_source_id, trueup_source_id],
            ),
            JournalEntry.id.in_(loan_entry_ids),
        ],
    ).all()
    posted: dict[_CorrectionKey, _LegMap] = {}
    for source_kind_id, entry_date, ledger_id, net, kind_id in rows:
        posted.setdefault((source_kind_id, entry_date), {})[ledger_id] = (
            net, kind_id,
        )
    return posted


def _resolve_anchor_pay_period(
    periods: list[PayPeriod], target_date: date,
) -> PayPeriod:
    """Return the pay period an anchor correction dated *target_date* books in.

    ``journal_entries.pay_period_id`` is NOT NULL, so an anchor correction needs a
    period even though the anchor date can predate every period (an imported loan
    whose origination is years before the app's first period).  Uses the period
    CONTAINING *target_date*, falling back to the user's EARLIEST period when the
    date precedes all of them -- so an opening is attributed to a real period and
    the reader (which bounds by period start) counts it from the first period on.

    Args:
        periods: The owner's pay periods, ascending by ``period_index`` (non-empty;
            the caller guarantees it).
        target_date: The anchor's date.

    Returns:
        The containing :class:`~app.models.pay_period.PayPeriod`, or the earliest
        when *target_date* precedes all periods.
    """
    containing = account_projection.find_period_containing_date(
        periods, target_date,
    )
    return containing if containing is not None else periods[0]


def _anchor_correction_description(
    source_kind_id: int, opening_source_id: int, entry_date: date,
) -> str:
    """Return the human label for an anchor-correction entry (display only).

    ``"Loan opening balance as of <date>"`` or ``"Loan balance true-up as of
    <date>"``, truncated to the description column width.  Never read for logic.

    Args:
        source_kind_id: The entry's journal source kind id.
        opening_source_id: The ``loan_opening`` ref id (opening vs. true-up).
        entry_date: The correction's civil date.

    Returns:
        The truncated description string.
    """
    label = (
        "opening balance" if source_kind_id == opening_source_id
        else "balance true-up"
    )
    return (
        f"Loan {label} as of {entry_date.isoformat()}"
    )[:_MAX_DESCRIPTION_LENGTH]


def _emit_anchor_correction_entry(
    owner_id: int,
    scenario_id: int,
    key: _CorrectionKey,
    period: PayPeriod,
    legs: list,
) -> JournalEntry:
    """Emit one balanced anchor-correction delta entry (opening or true-up).

    Builds the journal header -- ``transfer_id`` / ``transaction_id`` both NULL
    (an anchor correction links to neither; ``source_kind_id`` disambiguates it),
    dated at the anchor's ``entry_date``, attributed to *period* -- and writes the
    balanced *legs* through the shared balanced-write path.  Flushes; does not
    commit.

    Args:
        owner_id: The loan owner's user id.
        scenario_id: The budget scenario the correction lives in.
        key: The ``(source_kind_id, entry_date)`` the delta reconciles.
        period: The resolved pay period for the NOT NULL ``pay_period_id``.
        legs: The balanced delta legs from :func:`._common.delta_legs`.

    Returns:
        The persisted delta :class:`~app.models.journal_entry.JournalEntry`.
    """
    source_kind_id, entry_date = key
    opening_source_id = ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING)
    entry = JournalEntry(
        user_id=owner_id,
        scenario_id=scenario_id,
        pay_period_id=period.id,
        entry_date=entry_date,
        source_kind_id=source_kind_id,
        transfer_id=None,
        transaction_id=None,
        description=_anchor_correction_description(
            source_kind_id, opening_source_id, entry_date,
        ),
    )
    _emit_balanced_entry(entry, legs)
    logger.info(
        "Posted loan anchor correction (source %d as of %s) as journal entry %d",
        source_kind_id, entry_date, entry.id,
    )
    return entry


def reconcile_loan_anchor_corrections(
    loan_account_id: int,
    scenario_id: int,
    corrections: list[LoanAnchorCorrection],
) -> None:
    """Reconcile a loan's opening + true-up corrections to a PRE-WALKED list.

    The reconcile half of the anchor sync, taking the corrections already
    produced by :func:`walk_loan_ledger` rather than re-walking, so the unified
    :func:`app.services.loan_posting_service.sync_loan_postings` can drive BOTH
    the payment and the anchor reconcile off ONE walk (the payment half is
    :func:`._payments.reconcile_loan_payment_splits`).  Builds the
    per-``(source kind, date)`` target legs (:func:`_anchor_correction_targets`),
    reads back what is posted (:func:`_posted_loan_anchor_correction_legs`), and
    emits ONE balanced delta per key that differs -- posting a new opening /
    true-up, adjusting a true-up whose ``owed_before`` moved (a pre-true-up
    payment changed), or reversing one a matching balance retired.

    Idempotent and self-healing: a re-run at the same state writes nothing
    (every delta is zero).  Touches ONLY the loan's own linked and opening-equity
    ledgers.  An empty *corrections* list (an unresolvable loan) or an owner that
    cannot be resolved is a no-op.  Flushes but does not commit (the caller owns
    the transaction).

    Args:
        loan_account_id: The loan whose anchor corrections to reconcile.
        scenario_id: The budget scenario to reconcile within.
        corrections: The loan's anchor corrections from :func:`walk_loan_ledger`
            (origination opening + user-trueups on or before the walk's as-of).

    Raises:
        PostingError: If the loan has anchor corrections to post but its owner has
            no pay periods (a broken invariant -- a correction needs a period).
    """
    if not corrections:
        return
    owner_id = loan_owner_id(loan_account_id)
    if owner_id is None:
        return
    periods = (
        db.session.query(PayPeriod)
        .filter(PayPeriod.user_id == owner_id)
        .order_by(PayPeriod.period_index)
        .all()
    )
    if not periods:
        raise PostingError(
            f"Loan account {loan_account_id} has anchor corrections to post but "
            f"owner {owner_id} has no pay periods; a correction's NOT NULL "
            f"pay_period_id cannot be resolved."
        )

    target = _anchor_correction_targets(corrections, owner_id)
    posted = _posted_loan_anchor_correction_legs(loan_account_id, scenario_id)
    for key in sorted(set(target) | set(posted)):
        legs = delta_legs(target.get(key, {}), posted.get(key, {}))
        if not legs:
            continue
        period = _resolve_anchor_pay_period(periods, key[1])
        _emit_anchor_correction_entry(
            owner_id, scenario_id, key, period, legs,
        )


def sync_loan_anchor_corrections(
    loan_account_id: int, scenario_id: int, as_of: date,
) -> None:
    """Walk a loan's anchors and reconcile ONLY their opening / true-up corrections.

    The anchor-only sync: walks the loan's anchors into their corrections
    (:func:`walk_loan_ledger`) and reconciles them
    (:func:`reconcile_loan_anchor_corrections`).  Posts ONLY the opening /
    true-up corrections; the go-forward chokepoints reconcile BOTH the payment
    and anchor halves in one walk via
    :func:`app.services.loan_posting_service.sync_loan_postings`.  This
    single-half entry point remains for reconciling anchors in isolation (the
    opening / true-up unit tests).

    A loan with no anchors (unresolvable) is a no-op.  Flushes but does not
    commit (the caller owns the transaction).

    Args:
        loan_account_id: The loan whose anchor corrections to reconcile.
        scenario_id: The budget scenario to reconcile within.
        as_of: The evaluation date; anchors after it are not yet corrections.

    Raises:
        PostingError: If the loan has anchor corrections to post but its owner has
            no pay periods (a broken invariant -- a correction needs a period).
    """
    reconcile_loan_anchor_corrections(
        loan_account_id, scenario_id,
        walk_loan_ledger(loan_account_id, scenario_id, as_of).anchor_corrections,
    )
