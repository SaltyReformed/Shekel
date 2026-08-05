"""Loan-anchor correction posting: the opening and true-up reconcile.

Posts a loan's genesis balance corrections -- the once-per-loan OPENING and every
user balance TRUE-UP -- into the append-only double-entry ledger, so the loan's
confirmed balance is fully reconstructable as ``-(sum of its linked postings)``
with no external anchor read (the foundation the read switch and Step-5 reporting
move onto).

Every anchor a loan carries posts one balanced correction (:mod:`app.services.loan_ledger` computes
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

**Reconciled to target, keyed by (source kind, pay period, entry date).**
Unlike a payment correction there is no shadow ``transaction_id`` to key on, so
the reconcile keys each anchor's entry by its ``source_kind_id`` (opening vs.
true-up), the pay period the anchor books in, and its ``entry_date`` (the
anchor date), scoped to the loan's linked ledger.  A change to a pre-true-up
payment moves a true-up's ``owed_before``; re-running the sync re-derives the
target and posts the balancing delta, so a stale true-up self-heals.  Flushes
but never commits -- the caller owns the transaction.

**The period entered the key at plan step X-ai-r (finding N-161), and on this
side it closes a LATENT hole rather than a live one.**  The key was ``(source
kind, entry date)`` and the entry's period was resolved inside the emission
loop from the anchor date, so on a fixed calendar the target and the posted
entries always agreed and no loan figure moves (measured on a production
clone: zero deltas across every loan).  They stop agreeing when the calendar
GROWS A PERIOD around an anchor that had none.
:func:`app.services.loan_ledger.resolve_anchor_pay_period` falls back to the
nearest period when nothing contains the anchor date, so a loan asserted past
the end of the user's schedule files against that fallback; the next
``extend_pay_periods`` -- or the rolling-window top-up
(``pay_period_admin.top_up_rolling_window``, which appends through the same
function on an ordinary grid or dashboard load) -- then creates the period
that really contains it.  A period-blind key compared the two as equal, so the
delta was zero and the correction sat in the fallback period permanently, with
any later adjustment filed there too.  With the period in the key the stale
key reverses to zero and the real one posts fresh, which is the same lifecycle
every other reconcile in this ledger already gets.

*The truncating paths are NOT the mechanism, and two drafts of this note got
their reason wrong.*  Only a period APPEARING under an existing correction
reaches this.  A period being DELETED does not, but not for the reason first
written here (*"they dispose the correction with the period"*): a truncate is
REFUSED before it deletes anything, because
``pay_period_admin._period_ids_with_unbalanced_ledger`` hard-locks any
to-delete period whose per-ledger nets are non-zero -- which is what a posted
correction makes them.  ``reset_pay_periods`` is the one path that really does
wipe and re-derive, and it pairs itself with a full resync
(``resync_user_loan_postings`` / ``resync_user_account_anchor_postings``);
``truncate_pay_periods`` and ``regenerate_pay_periods`` run no resync at all,
which is only safe BECAUSE they refuse.

**Where the period comes from is the SAME on both halves, and this paragraph
used to say it was not.**  A loan anchor's period is DERIVED from its date
through :func:`app.services.loan_ledger.resolve_anchor_pay_period` -- the same
call the fold's visibility rule uses -- because ``budget.loan_anchor_events``
carries no ``pay_period_id`` column at all (and the origination anchor is
SYNTHESIZED from ``LoanParams``, so it has no row to carry one).  **The account
twin makes the identical call** (ruling R-EA): a cash assertion does store a
period, and the twin deliberately does not read it, because that column is a
cache of this same derivation and a clock split made it disagree with its own
day on real data.  An earlier draft of this paragraph read *"a cash assertion
stores its own period and the account twin reads it"*; that was true of the
first build of plan step X-ai-r and was left standing when the ruling changed,
which is exactly the stale-sentence-beside-a-rewritten-one class this arc keeps
paying for.
"""

from app import ref_cache
from app.enums import (
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.models.pay_period import PayPeriod
from app.services import ledger_account_service
from app.services._posting_reconcile import (
    CorrectionKey,
    LegMap,
    account_owner_id,
    emit_correction_deltas,
    merge_target_legs,
    posted_correction_legs,
)
from app.services.user_write_lock import lock_user_writes
from app.services.loan_ledger import (
    LoanAnchorCorrection,
    owner_pay_periods,
    resolve_anchor_pay_period,
    walk_loan_ledger,
)
from app.services.loan_loaders import LoanAnchorFact
from app.services.posting_service import (
    PostingError,
    _ledger_account_for,
)


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
    corrections: list[LoanAnchorCorrection],
    owner_id: int,
    periods: list[PayPeriod],
) -> dict[CorrectionKey, LegMap]:
    """Merge a loan's anchor corrections into per-(source, period, date) targets.

    Groups every anchor correction by its ``(source_kind_id, pay_period_id,
    anchor_date)`` key and sums the legs within each group
    (:func:`app.services._posting_reconcile.merge_target_legs`), so two same-day
    same-kind anchors (the unique index permits two true-ups on one day with
    different balances) net to a single balanced target that lands owed on the
    LATER value -- exactly the combined jump they express.  A correction that
    books nothing still creates its key with an empty leg map, so an entry it
    previously posted (now matching) is reversed to zero by the reconcile.

    **The period is RESOLVED here, from the calendar, rather than in the
    emission loop** (plan step X-ai-r).  It is part of the key, so it has to be
    known before the target and the posted side can be compared -- which is
    exactly the point: a boundary that moved under an already-posted correction
    now shows up as two keys (the old one reversing, the new one posting) instead
    of one target silently re-filed.  Two same-day anchors always resolve to the
    same period, so this narrows no group that was merged before.

    Args:
        corrections: The loan's anchor corrections from :func:`walk_loan_ledger`.
        owner_id: The loan owner's user id.
        periods: The owner's pay periods ascending, non-empty (the caller
            raises when the loan has corrections to post and the owner has
            none, so :func:`resolve_anchor_pay_period` always resolves).

    Returns:
        ``{(source_kind_id, pay_period_id, entry_date): {ledger_account_id:
        (amount, kind_id)}}``.
    """
    target: dict[CorrectionKey, LegMap] = {}
    for correction in corrections:
        source_enum, _posting_kind = _anchor_correction_kinds(correction.anchor)
        anchor_date = correction.anchor.anchor_date
        key = (
            ref_cache.posting_source_id(source_enum),
            resolve_anchor_pay_period(periods, anchor_date).id,
            anchor_date,
        )
        bucket = target.setdefault(key, {})
        merge_target_legs(
            bucket, _loan_anchor_correction_target(correction, owner_id),
        )
    return target


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
    per-``(source kind, pay period, date)`` target legs
    (:func:`_anchor_correction_targets`), reads back what is posted
    (:func:`app.services._posting_reconcile.posted_correction_legs`, scoped to
    the loan's linked ledger), and
    emits ONE balanced delta per key that differs
    (:func:`app.services._posting_reconcile.emit_correction_deltas`, the loop
    shared with the account twin) -- posting a new opening /
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
    # The SAME calendar load the fold's visibility rule uses
    # (:func:`app.services.loan_ledger.owner_pay_periods`), so the period this
    # writer FILES an anchor under and the period the fold derives its visible-on
    # date FROM can never come from two different lists.  That is what makes
    # "two consumers, one rule" true of the period set as well as the lookup.
    periods = owner_pay_periods(loan_account_id)
    if not periods:
        raise PostingError(
            f"Loan account {loan_account_id} has anchor corrections to post but "
            f"owner {owner_id} has no pay periods; a correction's NOT NULL "
            f"pay_period_id cannot be resolved."
        )

    emit_correction_deltas(
        owner_id,
        scenario_id,
        target=_anchor_correction_targets(corrections, owner_id, periods),
        posted=posted_correction_legs(
            _ledger_account_for(loan_account_id).id,
            scenario_id,
            [
                ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING),
                ref_cache.posting_source_id(PostingSourceEnum.LOAN_TRUEUP),
            ],
        ),
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
    (:func:`app.services.loan_ledger.walk_loan_ledger`, which also records where that decision is
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
    # Locked like every other reconcile door (plan step X-f1c3c): this one has
    # no ``app/`` caller today, only tests -- and it is in ``__all__``, so the
    # next caller would otherwise bypass the serialisation silently.  A neutral
    # review found it and its payment twin exactly that way.
    owner_id = account_owner_id(loan_account_id)
    if owner_id is not None:
        lock_user_writes(owner_id)
    reconcile_loan_anchor_corrections(
        loan_account_id, scenario_id,
        walk_loan_ledger(loan_account_id, scenario_id).anchor_corrections,
    )
