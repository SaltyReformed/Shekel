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

from datetime import date

from app import ref_cache
from app.enums import (
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.services import account_projection, ledger_account_service
from app.services._posting_reconcile import (
    CorrectionKey,
    LegMap,
    account_owner_id,
    delta_legs,
    emit_anchor_correction_entry,
    merge_target_legs,
    posted_correction_legs,
)
from app.services.posting_service import (
    PostingError,
    _ledger_account_for,
)

from app.services.loan_loaders import LoanAnchorFact

from ._walk import LoanAnchorCorrection, walk_loan_ledger


def _anchor_correction_kinds(
    anchor: LoanAnchorFact,
) -> tuple[PostingSourceEnum, PostingKindEnum]:
    """Return the (journal source kind, posting leg kind) for an anchor's correction.

    The origination anchor books the loan's OPENING (source ``loan_opening``, leg
    kind ``opening``); every other anchor is a user balance assertion and books a
    TRUE-UP (source ``loan_trueup``, leg kind ``trueup``).  Keyed off the fact's
    ``is_opening`` flag -- the synthesized origination fact is the only opening
    (:func:`app.services.loan_loaders.load_loan_anchor_facts`), so "not
    opening" is exactly "user-trueup".

    Args:
        anchor: The :class:`~app.services.loan_loaders.LoanAnchorFact` whose
            correction kinds to resolve.

    Returns:
        ``(PostingSourceEnum, PostingKindEnum)`` -- ``(LOAN_OPENING, OPENING)`` for
        the origination anchor, else ``(LOAN_TRUEUP, TRUEUP)``.
    """
    if anchor.is_opening:
        return PostingSourceEnum.LOAN_OPENING, PostingKindEnum.OPENING
    return PostingSourceEnum.LOAN_TRUEUP, PostingKindEnum.TRUEUP


def _loan_anchor_correction_target(
    correction: LoanAnchorCorrection, owner_id: int,
) -> LegMap:
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
    verified = anchor.anchor_balance
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
) -> dict[CorrectionKey, LegMap]:
    """Merge a loan's anchor corrections into per-(source, date) target legs.

    Groups every anchor correction by its ``(source_kind_id, anchor_date)`` key
    and sums the legs within each group
    (:func:`app.services._posting_reconcile.merge_target_legs`), so two same-day
    same-kind anchors (the unique index permits two true-ups on one day with
    different balances) net to a single balanced target that lands owed on the
    LATER value -- exactly the combined jump they express.  A correction that
    books nothing still creates its key with an empty leg map, so an entry it
    previously posted (now matching) is reversed to zero by the reconcile.

    Args:
        corrections: The loan's anchor corrections from :func:`walk_loan_ledger`.
        owner_id: The loan owner's user id.

    Returns:
        ``{(source_kind_id, entry_date): {ledger_account_id: (amount, kind_id)}}``.
    """
    target: dict[CorrectionKey, LegMap] = {}
    for correction in corrections:
        source_enum, _posting_kind = _anchor_correction_kinds(correction.anchor)
        key = (
            ref_cache.posting_source_id(source_enum),
            correction.anchor.anchor_date,
        )
        bucket = target.setdefault(key, {})
        merge_target_legs(
            bucket, _loan_anchor_correction_target(correction, owner_id),
        )
    return target


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
    reads back what is posted
    (:func:`app.services._posting_reconcile.posted_correction_legs`, scoped to
    the loan's linked ledger), and
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
            (its origination opening + every user-trueup).

    Raises:
        PostingError: If the loan has anchor corrections to post but its owner has
            no pay periods (a broken invariant -- a correction needs a period).
    """
    if not corrections:
        return
    owner_id = account_owner_id(loan_account_id)
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
    posted = posted_correction_legs(
        _ledger_account_for(loan_account_id).id,
        scenario_id,
        [
            ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING),
            ref_cache.posting_source_id(PostingSourceEnum.LOAN_TRUEUP),
        ],
    )
    for key in sorted(set(target) | set(posted)):
        legs = delta_legs(target.get(key, {}), posted.get(key, {}))
        if not legs:
            continue
        period = _resolve_anchor_pay_period(periods, key[1])
        emit_anchor_correction_entry(
            owner_id, scenario_id, key, period.id, legs,
        )


def sync_loan_anchor_corrections(
    loan_account_id: int, scenario_id: int,
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

    Posts EVERY anchor the loan carries, whatever its date -- the walk reads no
    clock, and which anchors have HAPPENED as of a date is the readers' decision
    (:func:`._walk.walk_loan_ledger`, which also records where that decision is
    currently made by the readers' CALLERS rather than the readers: N-10).  A loan
    with no anchors (unresolvable) is a no-op.  Flushes but does not commit (the
    caller owns the transaction).

    Args:
        loan_account_id: The loan whose anchor corrections to reconcile.
        scenario_id: The budget scenario to reconcile within.

    Raises:
        PostingError: If the loan has anchor corrections to post but its owner has
            no pay periods (a broken invariant -- a correction needs a period).
    """
    reconcile_loan_anchor_corrections(
        loan_account_id, scenario_id,
        walk_loan_ledger(loan_account_id, scenario_id).anchor_corrections,
    )
