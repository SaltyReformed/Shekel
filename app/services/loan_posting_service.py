"""
Shekel Budget App -- Loan-Payment Posting Service (Build-Order Step 4)

Posts the REAL principal / interest / escrow / refund split of a confirmed
loan payment into the append-only double-entry ledger, as a balanced
CORRECTION layered on top of the Build-Order Step 2 cash entry.

A confirmed loan payment is one Transfer with two shadows; Step 2
(:mod:`app.services.posting_service`) already posted the whole cash as a
balanced entry (``Checking -cash / Loan +cash``) linked by ``transfer_id``.
But that dumps the ENTIRE cash onto the loan, when only the PRINCIPAL portion
pays the debt down.  Because a posted entry is immutable, Step 4 appends a
second balanced entry that moves the non-principal off the loan::

    Loan     -(interest + escrow + excess)   [principal]
    Interest +interest                        [interest -> Expense]
    Escrow   +escrow                          [escrow   -> Expense]
    Refund   +excess                          [refund   -> Asset]
             --------------------------------
             0

The loan's NET (Step-2 cash + this correction) is then exactly the real
principal paid.  The split is computed from the ACTUAL cash
(``principal = cash - interest - escrow``), so an extra or short payment is
captured honestly -- where the loan resolver discards the cash and needs an
anchor true-up.  Each payment's escrow is the configured monthly amount IN EFFECT
ON that payment's date (the effective-dated components active on its pay-period
start, NO inflation), so on-schedule the principal is byte-identical to the
resolver's, and a later escrow change never re-splits a past payment.

**Linked by ``transaction_id``, not ``transfer_id``.**  The correction links to
the loan-side income shadow's ``transaction_id``, leaving ``transfer_id`` NULL.
That NULL is load-bearing: the Step-2 cash path reads the loan ledger via
``posting_service._posted_net(transfer_id, ...)``, so a ``transfer_id`` on the
correction would corrupt its cash reversals.  ``transaction_id`` is invisible to
both the cash transfer path (which keys by ``transfer_id``) and the Step-3
transaction path (which skips transfer shadows), so the correction is disjoint
from every existing reader by construction.

**Reuses the shared posting infrastructure.**  This module is a sibling
*posting source* alongside the transfer and transaction posting in
:mod:`app.services.posting_service`; it books through that module's shared
balanced-write path (``_emit_balanced_entry``), leg DTO (``_PostingLeg``),
linked-ledger resolver (``_ledger_account_for``), and settle-date helper
(``_civil_settle_date``), so an unbalanced entry can never be written from any
source and every source shares one entry-date and leg convention.  It lives in
its own module -- rather than growing ``posting_service`` past the project's
module-size limit -- because the loan split, with its anchor walk, rate-period
sampling, and running-balance coupling, is a cohesive concern of its own.

**Reuses the resolver's pure primitives.**  The split walks the IDENTICAL
payment set, anchor, and rate path the loan resolver's balance replay does --
:func:`app.services.loan_resolver.select_latest_anchor` /
:func:`app.services.loan_resolver.resolve_periods` and the
:func:`app.services.rate_period_engine.is_confirmed_payment_eligible`
predicate -- so the posted ledger and the resolver can never drift on which
payments, which anchor, or which rate they consider.  No engine change: the
resolver's contractual replay is untouched, and this module only READS its
primitives.

**Flask-isolated** (``CLAUDE.md`` Architecture rule): plain data in, ORM objects
or plain values out; never imports ``request`` / ``session``.  It flushes but
never commits -- the caller (the transfer-service wiring in a later commit, a
test, or a backfill) owns the transaction boundary.

**WRITE-ONLY (this Build-Order step).**  Nothing READS these postings yet:
displayed loan balances still flow through the resolver / ``balance_at`` seam.
Switching confirmed loan reads onto the ledger -- and retiring the resolver's
confirmed replay -- is the next Build-Order step; this step builds and proves
the reality-authoritative record in parallel, observably a no-op on today's
on-schedule data while establishing the correct foundation.
"""

import logging
from dataclasses import dataclass
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
from app.models.journal_entry import JournalEntry, Posting
from app.models.transaction import Transaction
from app.services import (
    escrow_calculator,
    ledger_account_service,
    loan_payment_service,
    loan_resolver,
)
from app.services.posting_service import (
    _MAX_DESCRIPTION_LENGTH,
    _PostingLeg,
    _civil_settle_date,
    _emit_balanced_entry,
    _ledger_account_for,
)
from app.services.rate_period_engine import (
    is_confirmed_payment_eligible,
    period_for_date,
)
from app.utils.balance_predicates import settled_status_ids
from app.utils.money import accrue_monthly_interest

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


@dataclass(frozen=True)
class LoanPaymentSplit:
    """The real principal / interest / escrow / refund split of one loan payment.

    The per-payment result of walking a loan's confirmed post-anchor payments
    with the ACTUAL cash paid (not the scheduled payment) -- see
    :func:`compute_loan_payment_splits`.  Carries the loan-side income shadow it
    derives from (the Step-4 correction books under that shadow's
    ``transaction_id``, and the sync reads the shadow's period / scenario /
    owner / ``paid_at`` for the entry header) plus the four economic parts the
    cash divides into, all signed for a debit-positive ledger.

    Attributes:
        income_shadow: The settled loan-side income :class:`Transaction` (the
            ``to``-account leg of the payment transfer).  Its
            ``effective_amount`` is the cash ``principal`` falls out of; its
            ``transaction_id`` keys the correction.
        interest: Accrued interest, ``round_money(balance_before * rate / 12)``
            on the REAL running balance -- an Expense leg (``>= 0``).
        escrow: The configured monthly escrow at payment time, NO inflation (the
            exact figure the cash was built from) -- an Expense leg (``>= 0``).
        principal: The real debt paid down, ``cash - interest - escrow``, capped
            at the outstanding balance.  May be NEGATIVE (an underpayment that
            grows the balance) -- surfaced, never clamped (plan D5).
        excess: A payoff overpayment routed to a Refund Receivable (Asset) leg
            (``>= 0``): cash beyond what closes the loan, never mislabeled as
            escrow or principal (plan D4).
    """

    income_shadow: Transaction
    interest: Decimal
    escrow: Decimal
    principal: Decimal
    excess: Decimal


def _split_one_payment(
    shadow: Transaction,
    balance: Decimal,
    periods: list,
    monthly_escrow: Decimal,
) -> tuple[LoanPaymentSplit, Decimal]:
    """Split one payment's cash and return ``(split, balance_after)``.

    The pure per-payment step of :func:`compute_loan_payment_splits` (the body
    of its running-balance walk), factored out so the recurrence reads as one
    expression and the post-payoff branch is explicit.  ``balance`` is the
    outstanding balance BEFORE this payment; the returned balance is AFTER it
    (``balance - principal``).

    Two regimes (plan Section 6):

    * **Loan already closed** (``balance <= 0``): no interest accrues and no
      escrow is due, so the entire cash is an overpayment routed to ``excess``
      (a Refund).  This keeps every post-payoff Step-2 cash entry matched by a
      correction instead of a phantom paydown.
    * **Open loan**: ``interest = round_money(balance * rate / 12)`` at the rate
      in effect for the payment's pay-period start (the BYTE-IDENTICAL formula
      :func:`app.services.rate_period_engine._replay_payment_row` uses);
      ``principal = cash - interest - escrow``; a principal that would overrun
      the balance caps to it, the remainder going to ``excess``.

    Args:
        shadow: The settled loan-side income shadow (supplies ``effective_amount``
            and ``pay_period.start_date``).
        balance: The outstanding balance before this payment.
        periods: The loan's rate periods (from
            :func:`app.services.loan_resolver.resolve_periods`); the governing
            period's ``annual_rate`` drives the interest accrual.
        monthly_escrow: The configured monthly escrow in effect on THIS payment's
            date (summed over the effective-dated components active on its
            pay-period start; no inflation).

    Returns:
        ``(LoanPaymentSplit, balance_after)``.
    """
    cash = shadow.effective_amount
    if balance <= 0:
        # The loan is already paid off: a further confirmed payment is pure
        # overpayment (refund), with no interest and no escrow due.
        split = LoanPaymentSplit(
            income_shadow=shadow,
            interest=Decimal("0.00"),
            escrow=Decimal("0.00"),
            principal=Decimal("0.00"),
            excess=cash,
        )
        return split, balance

    period = period_for_date(periods, shadow.pay_period.start_date)
    interest = accrue_monthly_interest(balance, period.annual_rate)
    principal = cash - interest - monthly_escrow
    if principal > balance:
        # Payoff overpayment: principal caps at the remaining balance; the
        # surplus is a refund the lender owes back (plan D4), never absorbed
        # into principal or escrow.
        excess = principal - balance
        principal = balance
    else:
        excess = Decimal("0.00")
    split = LoanPaymentSplit(
        income_shadow=shadow,
        interest=interest,
        escrow=monthly_escrow,
        principal=principal,
        excess=excess,
    )
    return split, balance - principal


def _eligible_confirmed_shadows(
    loan_account_id: int,
    scenario_id: int,
    anchor_date: date,
    payment_day: int,
    as_of: date,
) -> list[Transaction]:
    """Return a loan's settled post-anchor income shadows, in payment order.

    The settled loan-side income shadows
    (:func:`app.services.loan_payment_service.query_shadow_income` supplies the
    shared "what counts as shadow income" predicate -- transfer-linked, Income
    type, non-deleted, non-excluded), narrowed to the settled statuses and the
    post-anchor window by the SAME predicate the resolver replays on
    (:func:`app.services.rate_period_engine.is_confirmed_payment_eligible`), so
    the split walks exactly the payment set the resolver's balance does.  Sorted
    by pay-period start -- the app's canonical payment chronology
    (``get_payment_history`` orders identically) and the order the running
    balance must be walked in; ``id`` is the deterministic tie-breaker.

    This walks the RAW shadows; it does NOT apply the resolver's
    biweekly-collision redistribution
    (``loan_payment_service._redistribute_to_distinct_months``, a display fix
    that shifts a second payment sharing a month onto the next month).  The two
    agree whenever payments fall in distinct months (the universal case for a
    monthly loan); they could differ only if two confirmed payments share a
    calendar month AND a rate-period boundary falls between that month and the
    next -- the running balance is walked sequentially either way, so the split
    is unchanged unless the shifted payment also crosses a rate step.  Each
    correction is dated by its own shadow's settle date regardless, so the
    posted entry date is always the real one.

    Args:
        loan_account_id: The loan account whose shadows to load.
        scenario_id: The budget scenario to scope to.
        anchor_date: The latest anchor's ``anchor_date`` (the post-anchor
            lower boundary).
        payment_day: The loan's contractual due day (drives the due-date
            boundary test).
        as_of: The evaluation date (the upper boundary).

    Returns:
        The eligible settled income shadows, ascending by pay-period start.
    """
    settled_shadows = (
        loan_payment_service.query_shadow_income(loan_account_id, scenario_id)
        .filter(Transaction.status_id.in_(settled_status_ids()))
        .all()
    )
    eligible = [
        shadow for shadow in settled_shadows
        if is_confirmed_payment_eligible(
            shadow.pay_period.start_date,
            anchor_date=anchor_date,
            payment_day=payment_day,
            as_of=as_of,
        )
    ]
    eligible.sort(key=lambda shadow: (shadow.pay_period.start_date, shadow.id))
    return eligible


def compute_loan_payment_splits(
    loan_account_id: int, scenario_id: int, as_of: date,
) -> list[LoanPaymentSplit]:
    """Return the real split of a loan's confirmed post-anchor payments.

    Walks the loan's settled, post-anchor income shadows in chronological order
    from the latest balance anchor, dividing each payment's ACTUAL cash into
    interest / escrow / principal / excess (the plan's one formula, Section 6;
    see :func:`_split_one_payment` for the per-payment math).  Because principal
    is ``cash - interest - escrow``, an extra or short payment lands in
    principal automatically -- the cash is the authority, where the resolver's
    contractual replay discards it and needs an anchor true-up.

    Reuses the resolver's OWN pure primitives -- the latest-anchor selection
    (:func:`app.services.loan_resolver.select_latest_anchor`), the rate-period
    set (:func:`app.services.loan_resolver.resolve_periods`), and the
    post-anchor eligibility predicate
    (:func:`app.services.rate_period_engine.is_confirmed_payment_eligible`) -- so
    the posted split walks the IDENTICAL payment set, anchor, and rate path the
    resolver's balance does, and diverges only where the user pays off-schedule
    (where the ledger is the more-correct record).  Unlike the resolver it does
    NOT stop at payoff: every eligible Step-2 cash entry gets a matching
    correction, with post-payoff cash routed to Refund, so the ledger stays
    complete (plan Section 6 / 8.3).

    Reads only (no writes, no commit).  Each payment's escrow is the configured
    monthly amount IN EFFECT ON that payment's date -- the effective-dated
    components active on its pay-period start, with NO inflation -- so on-schedule
    the principal is byte-identical to the resolver's, AND a later escrow change
    never moves an already-posted split (immutable for a past date; plan
    Section 2 / D3).

    Args:
        loan_account_id: The loan account whose confirmed payments to split.
        scenario_id: The budget scenario the payments live in.
        as_of: The evaluation date; a payment whose pay period has not begun by
            it is a forward projection and is excluded.

    Returns:
        One :class:`LoanPaymentSplit` per eligible confirmed payment, in
        chronological (pay-period-start) order.  Empty (``[]``) when the loan
        has no :class:`LoanParams` (not yet resolvable -- the N1 guard), no
        anchor event, or no eligible confirmed payment.
    """
    params = loan_payment_service.load_loan_params(loan_account_id)
    if params is None:
        # Not a configured loan yet (e.g. a payment settled before its
        # LoanParams was created); nothing to split until it is resolvable.
        return []
    anchor_events = loan_payment_service.load_anchor_events(loan_account_id)
    if not anchor_events:
        # The origination backfill guarantees at least one anchor per loan; an
        # empty list only arises in a degenerate direct-insert test fixture.
        return []

    anchor = loan_resolver.select_latest_anchor(anchor_events)
    periods = loan_resolver.resolve_periods(
        params, loan_payment_service.load_rate_changes(loan_account_id),
    )
    # EVERY escrow version (active + removed), loaded once; each payment's
    # escrow is summed over the versions in effect ON that payment's date, so a
    # since-removed version still applies to a historical payment and a later
    # escrow change never re-splits a past payment (plan Section 2 / D3).
    escrow_components = loan_payment_service.load_all_escrow_components(
        loan_account_id,
    )

    shadows = _eligible_confirmed_shadows(
        loan_account_id, scenario_id, anchor.anchor_date,
        params.payment_day, as_of,
    )
    balance = Decimal(str(anchor.anchor_balance))
    splits: list[LoanPaymentSplit] = []
    for shadow in shadows:
        payment_escrow = escrow_calculator.calculate_monthly_escrow([
            comp for comp in escrow_components
            if comp.is_active_on(shadow.pay_period.start_date)
        ])
        split, balance = _split_one_payment(
            shadow, balance, periods, payment_escrow,
        )
        splits.append(split)
    return splits


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
    transfer shadows): only the Step-4 correction is ever summed here.

    Args:
        transaction_id: The income shadow's id whose posted corrections to sum.

    Returns:
        ``{ledger_account_id: (net Decimal, posting_kind_id)}``; empty when no
        correction is posted yet.
    """
    rows = (
        db.session.query(
            Posting.ledger_account_id,
            db.func.sum(Posting.amount),
            Posting.posting_kind_id,
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            JournalEntry.transaction_id == transaction_id,
            JournalEntry.source_kind_id == ref_cache.posting_source_id(
                PostingSourceEnum.LOAN_PAYMENT
            ),
        )
        .group_by(Posting.ledger_account_id, Posting.posting_kind_id)
        .all()
    )
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
    Emits ONE balanced delta journal entry bringing the net posted
    ``loan_payment`` legs to *target*, or ``None`` when already at target (an
    idempotent no-op).

    *target* maps each ledger account the correction should land on to its
    ``(signed amount, posting_kind_id)``; an EMPTY *target* reverses the whole
    correction to zero (the reverse-before-delete / stale-shadow path).  The
    posted side (:func:`_posted_loan_payment_legs`) carries each ledger's kind,
    so a leg whose target dropped to zero is still reversed with the kind it was
    posted under.  Deltas always sum to zero (the target sums to zero by
    construction, every posted entry balanced), so a non-empty delta set has
    ``>= 2`` legs and :func:`_emit_balanced_entry` never sees a single leg.

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
    posted = _posted_loan_payment_legs(shadow.id)
    legs: list[_PostingLeg] = []
    for ledger_id in sorted(set(target) | set(posted)):
        target_amount, target_kind = target.get(ledger_id, (Decimal("0.00"), None))
        posted_amount, posted_kind = posted.get(ledger_id, (Decimal("0.00"), None))
        delta = target_amount - posted_amount
        if delta == 0:
            continue
        # The kind labels the leg's economic nature.  A target leg knows its own
        # kind; a leg present only on the posted side (a component reversed to
        # zero) reuses the kind it was posted with.
        kind_id = target_kind if target_kind is not None else posted_kind
        legs.append(_PostingLeg(ledger_id, delta, kind_id))
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
    :func:`compute_loan_payment_splits` just reconciled.  These are payments that
    were settled-and-posted but have since been reverted, edited to un-settle, or
    pushed behind a new balance anchor: their corrections must be reversed to
    zero so the ledger stops reflecting a payment that no longer counts.

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

    Computes the real split of every confirmed post-anchor payment
    (:func:`compute_loan_payment_splits`), reconciles each payment's correction
    to its target legs (:func:`_reconcile_loan_payment` /
    :func:`_loan_payment_target`), then reverses any correction whose payment is
    no longer eligible (:func:`_stale_loan_payment_shadows`).  WHOLE-loan because
    interest accrues on the running balance -- re-splitting one payment (a
    true-up, a rate change, an amount edit) re-splits every LATER one -- so a
    per-payment sync could leave the downstream corrections stale.

    Idempotent and self-healing: a re-run with no change writes nothing
    (reconcile-to-target sees ``delta == 0`` everywhere), and a missed call
    repairs at the next sync.  Touches ONLY the loan's own ledgers (linked,
    interest, escrow, refund) -- never Checking (the Step-2 cash entry is
    immutable and correct), so a loan sync can never move a cash balance.

    Reads ``as_of`` as the upper bound on which payments are historical; the
    Commit-5 wiring passes ``date.today()``.  Flushes but does not commit (the
    caller owns the transaction).

    Args:
        loan_account_id: The loan whose corrections to reconcile.
        scenario_id: The budget scenario to reconcile within.
        as_of: The evaluation date (a payment whose pay period has not begun by
            it is a projection, excluded from the eligible set).
    """
    splits = compute_loan_payment_splits(loan_account_id, scenario_id, as_of)
    synced_shadow_ids: set[int] = set()
    for split in splits:
        synced_shadow_ids.add(split.income_shadow.id)
        _reconcile_loan_payment(
            split.income_shadow, _loan_payment_target(split),
        )

    # A payment that was posted but has since left the eligible set (reverted,
    # un-settled, or re-based behind a new anchor) keeps a stale correction; an
    # empty target reverses it to zero.  Hard deletes are handled before the row
    # is gone, by reverse_loan_payment_postings_for_shadow.
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
    while ``income_shadow.id`` still exists, by the Commit-5 delete wiring; the
    whole-loan :func:`sync_loan_payment_postings` then re-splits the downstream
    payments whose running balance the deletion changed.

    Idempotent no-op for a never-posted (Projected) shadow.  Flushes but does
    not commit (the caller owns the transaction).

    Args:
        income_shadow: The loan-side income :class:`Transaction` about to be
            deleted.  Must still be flushed (``id`` set) so the reversal links
            by ``transaction_id`` and reads the posted legs back.
    """
    _reconcile_loan_payment(income_shadow, {})
