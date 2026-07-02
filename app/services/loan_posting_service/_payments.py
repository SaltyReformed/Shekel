"""Loan-payment correction posting: the per-payment split reconcile.

Posts the REAL principal / interest / escrow / refund split of a confirmed loan
payment into the append-only double-entry ledger, as a balanced CORRECTION
layered on top of the Build-Order Step 2 cash entry.

Step 2 (:mod:`app.services.posting_service`) already posted the whole cash as a
balanced entry (``Checking -cash / Loan +cash``) linked by ``transfer_id``.  But
that dumps the ENTIRE cash onto the loan, when only the PRINCIPAL portion pays
the debt down.  Because a posted entry is immutable, this module appends a second
balanced entry that moves the non-principal off the loan::

    Loan     -(interest + escrow + excess)   [principal]
    Interest +interest                        [interest -> Expense]
    Escrow   +escrow                          [escrow   -> Expense]
    Refund   +excess                          [refund   -> Asset]
             --------------------------------
             0

The loan's NET (Step-2 cash + this correction) is then exactly the real principal
paid.  The split (:mod:`._walk`) is computed from the ACTUAL cash
(``principal = cash - interest - escrow``), so an extra or short payment is
captured honestly.

**Linked by ``transaction_id``, not ``transfer_id``.**  The correction links to
the loan-side income shadow's ``transaction_id``, leaving ``transfer_id`` NULL.
That NULL is load-bearing: the Step-2 cash path reads the loan ledger via
``posting_service._posted_net(transfer_id, ...)``, so a ``transfer_id`` on the
correction would corrupt its cash reversals.  ``transaction_id`` is invisible to
both the cash transfer path (which keys by ``transfer_id``) and the Step-3
transaction path (which skips transfer shadows), so the correction is disjoint
from every existing reader by construction.

**Flask-isolated**: plain data in, ORM objects or plain values out; never imports
``request`` / ``session``.  Flushes but never commits -- the caller owns the
transaction boundary.
"""

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import (
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.extensions import db
from app.models.journal_entry import JournalEntry
from app.models.transaction import Transaction
from app.services import ledger_account_service
from app.services.posting_service import (
    _MAX_DESCRIPTION_LENGTH,
    _civil_settle_date,
    _emit_balanced_entry,
    _ledger_account_for,
)

from ._common import delta_legs, summed_posting_legs
from ._walk import LoanPaymentSplit, compute_loan_payment_splits

logger = logging.getLogger(__name__)


# The three per-loan correction components, each a tuple of (the per-loan ledger
# account KIND to resolve, the posting-leg KIND to tag the leg, the
# :class:`LoanPaymentSplit` attribute holding the leg's amount).  The loan-linked
# principal leg is handled separately -- it books onto the loan's existing
# Asset/Liability account mirror (the ``linked`` ledger), not a per-loan account.
# Driving the three components off one table keeps the target builder DRY and
# makes "add a component" a one-line change.
_LOAN_CORRECTION_COMPONENTS = (
    (LedgerAccountKindEnum.LOAN_INTEREST, PostingKindEnum.INTEREST, "interest"),
    (LedgerAccountKindEnum.LOAN_ESCROW, PostingKindEnum.ESCROW, "escrow"),
    (LedgerAccountKindEnum.LOAN_REFUND, PostingKindEnum.REFUND, "excess"),
)


def _loan_payment_description(shadow: Transaction) -> str:
    """Return the human label for a loan-payment correction entry.

    ``"Loan payment split: <shadow name>"`` truncated to the description column
    width.  Display only -- never read for logic.

    Args:
        shadow: The loan-side income shadow the correction books under.

    Returns:
        The truncated description string.
    """
    return (
        f"Loan payment split: {shadow.name}"
    )[:_MAX_DESCRIPTION_LENGTH]


def _posted_loan_payment_legs(
    transaction_id: int,
) -> dict[int, tuple[Decimal, int]]:
    """Return the net loan-payment legs already posted under a shadow's id.

    ``{ledger_account_id: (net_amount, posting_kind_id)}`` summed over every
    ``loan_payment``-sourced journal entry linked to *transaction_id* (the
    income shadow's id).  The loan analog of :func:`_posted_net_by_account`,
    additionally carrying each ledger's posting kind: within a loan correction a
    ledger account always carries ONE kind (the loan-linked principal leg, or a
    per-loan interest / escrow / refund leg), so grouping by
    ``(ledger_account_id, posting_kind_id)`` yields one row per ledger and the
    kind travels with the net.  The reconcile (:func:`_reconcile_loan_payment`)
    reads this back so a reversal leg negates EXACTLY what was posted and reuses
    the kind it was posted with -- load-bearing when a component zeroes out (a
    later true-up re-splits a payment to no escrow) and its target leg is no
    longer resolved.

    The ``source_kind = loan_payment`` filter makes this disjoint from the
    Step-2 cash path (which links the same shadow's TRANSFER by ``transfer_id``,
    never this ``transaction_id``) and the Step-3 transaction path (which skips
    transfer shadows): only the loan-payment correction is ever summed here.

    Args:
        transaction_id: The income shadow's id whose posted corrections to sum.

    Returns:
        ``{ledger_account_id: (net Decimal, posting_kind_id)}``; empty when no
        correction is posted yet.
    """
    rows = summed_posting_legs(
        [],
        [
            JournalEntry.transaction_id == transaction_id,
            JournalEntry.source_kind_id == ref_cache.posting_source_id(
                PostingSourceEnum.LOAN_PAYMENT
            ),
        ],
    ).all()
    return {
        ledger_id: (net, kind_id) for ledger_id, net, kind_id in rows
    }


def _loan_payment_target(
    split: LoanPaymentSplit,
) -> dict[int, tuple[Decimal, int]]:
    """Build the target ledger legs for one payment's real-split correction.

    Maps the split to ``{ledger_account_id: (signed amount, posting_kind_id)}``,
    dropping any zero component so no empty per-loan ledger account is minted and
    no zero leg is written:

    * the loan's LINKED ledger (the Asset/Liability mirror Step 2 dumped the
      whole cash onto) gets ``-(interest + escrow + excess)`` tagged
      ``principal`` -- so the loan's NET across the Step-2 cash leg and this
      correction is exactly ``principal`` (plan Section 1);
    * the per-loan ``loan_interest`` Expense ledger gets ``+interest``;
    * the per-loan ``loan_escrow`` Expense ledger gets ``+escrow``;
    * the per-loan ``loan_refund`` Asset ledger gets ``+excess``.

    The per-loan ledger accounts are lazily resolved (created on first use,
    reused after) via
    :func:`app.services.ledger_account_service.get_or_create_loan_ledger_account`,
    keyed only when their amount is non-zero.  The legs sum to zero by
    construction.  An all-principal payment (``interest == escrow == excess ==
    0``) yields an EMPTY target: the loan already nets to principal from the
    Step-2 cash leg, so no correction is owed.

    Args:
        split: The payment's :class:`LoanPaymentSplit`.

    Returns:
        ``{ledger_account_id: (amount, posting_kind_id)}`` for the non-zero
        legs (empty when no correction is owed).

    Raises:
        PostingError: If the loan account has no linked ledger account (a broken
            chart-of-accounts pairing).
    """
    shadow = split.income_shadow
    owner_id = shadow.pay_period.user_id
    loan_account_id = shadow.account_id
    target: dict[int, tuple[Decimal, int]] = {}

    # The loan-linked leg backs the non-principal cash out of the loan; its
    # magnitude mirrors the interest + escrow + refund legs, so the four sum to
    # zero and the loan nets to the real principal.
    loan_leg = -(split.interest + split.escrow + split.excess)
    if loan_leg != 0:
        loan_linked = _ledger_account_for(loan_account_id)
        target[loan_linked.id] = (
            loan_leg, ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
        )
    for ledger_kind, posting_kind, attr in _LOAN_CORRECTION_COMPONENTS:
        amount = getattr(split, attr)
        if amount != 0:
            ledger = ledger_account_service.get_or_create_loan_ledger_account(
                owner_id, loan_account_id, ledger_kind,
            )
            target[ledger.id] = (
                amount, ref_cache.posting_kind_id(posting_kind),
            )
    return target


def _reconcile_loan_payment(
    shadow: Transaction,
    target: dict[int, tuple[Decimal, int]],
) -> JournalEntry | None:
    """Reconcile one payment's posted correction to *target*, idempotently.

    The loan analog of the reconcile inside :func:`sync_transaction_postings`,
    keyed by the income shadow's ``transaction_id`` (NOT ``transfer_id`` -- that
    keeps the correction invisible to the Step-2 cash path; plan Section 5).
    Emits ONE balanced delta journal entry (via :func:`._common.delta_legs`)
    bringing the net posted ``loan_payment`` legs to *target*, or ``None`` when
    already at target (an idempotent no-op).

    *target* maps each ledger account the correction should land on to its
    ``(signed amount, posting_kind_id)``; an EMPTY *target* reverses the whole
    correction to zero (the reverse-before-delete / stale-shadow path).  The
    posted side (:func:`_posted_loan_payment_legs`) carries each ledger's kind,
    so a leg whose target dropped to zero is still reversed with the kind it was
    posted under.

    Flushes but does not commit (the caller owns the transaction).

    Args:
        shadow: The loan-side income shadow the correction books under.  Must be
            flushed (``id`` set) so the entry links by ``transaction_id`` and
            the posted legs read back.
        target: ``{ledger_account_id: (amount, posting_kind_id)}`` the ledger
            should net to (empty to reverse to zero).

    Returns:
        The new delta :class:`~app.models.journal_entry.JournalEntry`, or
        ``None`` when already at target.
    """
    legs = delta_legs(target, _posted_loan_payment_legs(shadow.id))
    if not legs:
        return None

    entry = JournalEntry(
        user_id=shadow.pay_period.user_id,
        scenario_id=shadow.scenario_id,
        pay_period_id=shadow.pay_period_id,
        entry_date=_civil_settle_date(shadow.paid_at, shadow.pay_period),
        source_kind_id=ref_cache.posting_source_id(
            PostingSourceEnum.LOAN_PAYMENT
        ),
        # Linked by transaction_id (the income shadow), leaving transfer_id
        # NULL.  That NULL is load-bearing: the Step-2 cash path reads the loan
        # ledger via _posted_net(transfer_id, ...), so a transfer_id here would
        # corrupt its cash reversals (plan Section 5 / the CRITICAL bug v1 had).
        transaction_id=shadow.id,
        description=_loan_payment_description(shadow),
    )
    _emit_balanced_entry(entry, legs)
    logger.info(
        "Posted loan-payment split correction for shadow %d (deltas %s) as "
        "journal entry %d",
        shadow.id,
        {leg.ledger_account_id: leg.amount for leg in legs},
        entry.id,
    )
    return entry


def _stale_loan_payment_shadows(
    loan_account_id: int,
    scenario_id: int,
    synced_shadow_ids: set[int],
) -> list[Transaction]:
    """Return loan-payment shadows with a posted correction that no longer applies.

    The income shadows of *loan_account_id* in *scenario_id* that carry at least
    one posted ``loan_payment`` correction (their ``transaction_id`` appears on
    such an entry) but are NOT in *synced_shadow_ids* -- the set the current
    :func:`walk_loan_ledger` just reconciled.  Under genesis the walk splits
    EVERY confirmed payment from origination, so a payment is stale ONLY when it
    genuinely left the confirmed set -- reverted or edited to un-settle -- never
    because a new anchor "pushed it behind" (genesis re-splits a pre-anchor
    payment from the anchor's reset, it does not drop it).  A stale correction is
    reversed to zero so the ledger stops reflecting a payment that no longer
    counts.

    A HARD-deleted payment is NOT here -- its row is gone and the entry's
    ``transaction_id`` was SET NULL (so it does not join), which is why the
    delete path reverses it BEFORE deletion via
    :func:`reverse_loan_payment_postings_for_shadow`.

    Args:
        loan_account_id: The loan whose stale corrections to find.
        scenario_id: The budget scenario to scope to.
        synced_shadow_ids: The shadow ids the current sync already reconciled.

    Returns:
        The still-present income shadows whose corrections are now stale
        (``pay_period`` eager-loaded for the reversal entry header).
    """
    loan_payment_source_id = ref_cache.posting_source_id(
        PostingSourceEnum.LOAN_PAYMENT
    )
    posted_shadow_ids = {
        row[0]
        for row in (
            db.session.query(JournalEntry.transaction_id)
            .join(Transaction, Transaction.id == JournalEntry.transaction_id)
            .filter(
                JournalEntry.source_kind_id == loan_payment_source_id,
                JournalEntry.scenario_id == scenario_id,
                Transaction.account_id == loan_account_id,
            )
            .distinct()
            .all()
        )
    }
    stale_ids = posted_shadow_ids - synced_shadow_ids
    if not stale_ids:
        return []
    return (
        db.session.query(Transaction)
        .options(joinedload(Transaction.pay_period))
        .filter(
            Transaction.id.in_(stale_ids),
            Transaction.account_id == loan_account_id,
        )
        .all()
    )


def sync_loan_payment_postings(
    loan_account_id: int, scenario_id: int, as_of: date,
) -> None:
    """Reconcile a loan's per-payment split corrections to reality, idempotently.

    Computes the real split of every confirmed payment from origination
    (:func:`compute_loan_payment_splits`), reconciles each payment's correction
    to its target legs (:func:`_reconcile_loan_payment` /
    :func:`_loan_payment_target`), then reverses any correction whose payment is
    no longer confirmed (:func:`_stale_loan_payment_shadows`).  WHOLE-loan because
    interest accrues on the running balance -- re-splitting one payment (a
    true-up, a rate change, an amount edit) re-splits every LATER one -- so a
    per-payment sync could leave the downstream corrections stale.

    Posts ONLY the payment-split corrections; the loan's opening / true-up
    corrections are reconciled separately by
    :func:`app.services.loan_posting_service.sync_loan_anchor_corrections` (they
    share the same :func:`walk_loan_ledger` running balance but book against the
    loan's opening-equity account, not a payment shadow).

    Idempotent and self-healing: a re-run with no change writes nothing
    (reconcile-to-target sees ``delta == 0`` everywhere), and a missed call
    repairs at the next sync.  Touches ONLY the loan's own ledgers (linked,
    interest, escrow, refund) -- never Checking (the Step-2 cash entry is
    immutable and correct), so a loan sync can never move a cash balance.

    Reads ``as_of`` as the upper bound on which payments are historical; the
    go-forward wiring passes ``date.today()``.  Flushes but does not commit (the
    caller owns the transaction).

    Args:
        loan_account_id: The loan whose corrections to reconcile.
        scenario_id: The budget scenario to reconcile within.
        as_of: The evaluation date (a payment whose pay period has not begun by
            it is a projection, excluded from the confirmed set).
    """
    splits = compute_loan_payment_splits(loan_account_id, scenario_id, as_of)
    synced_shadow_ids: set[int] = set()
    for split in splits:
        synced_shadow_ids.add(split.income_shadow.id)
        _reconcile_loan_payment(
            split.income_shadow, _loan_payment_target(split),
        )

    # A payment that was posted but has since left the confirmed set (reverted
    # or un-settled) keeps a stale correction; an empty target reverses it to
    # zero.  Hard deletes are handled before the row is gone, by
    # reverse_loan_payment_postings_for_shadow.
    for shadow in _stale_loan_payment_shadows(
        loan_account_id, scenario_id, synced_shadow_ids,
    ):
        _reconcile_loan_payment(shadow, {})


def reverse_loan_payment_postings_for_shadow(income_shadow: Transaction) -> None:
    """Reverse one loan payment's split correction before its shadow is deleted.

    The loan analog of :func:`reverse_postings_before_delete`: reconciles the
    income shadow's ``loan_payment`` correction to zero
    (:func:`_reconcile_loan_payment` with an empty target), emitting a balanced
    reversal for whatever is posted, so a HARD delete (which SET-NULLs the
    entry's ``transaction_id``) never strands the correction's legs.  Run FIRST,
    while ``income_shadow.id`` still exists, by the delete wiring; the whole-loan
    :func:`sync_loan_payment_postings` then re-splits the downstream payments
    whose running balance the deletion changed.

    Idempotent no-op for a never-posted (Projected) shadow.  Flushes but does
    not commit (the caller owns the transaction).

    Args:
        income_shadow: The loan-side income :class:`Transaction` about to be
            deleted.  Must still be flushed (``id`` set) so the reversal links
            by ``transaction_id`` and reads the posted legs back.
    """
    _reconcile_loan_payment(income_shadow, {})
