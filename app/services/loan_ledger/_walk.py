"""The loan walk: ONE running-balance replay over ONE event stream -- FACTS.

The single chronological walk a loan's whole architecture derives from.  It
seeds the balance at zero and, in event order (:mod:`._events`), applies each
anchor as a RESET and splits each settled payment on the reset-aware balance
(:mod:`._split`) -- so the loan's opening, every true-up, and every payment split
come from ONE running balance and can never disagree on the balance interest
accrued on, the way three independent walks could.

**The walk yields FACTS, not a balance-at-T.**  Its output is a
:class:`LoanLedgerWalk` -- one :class:`~._split.LoanPaymentSplit` per settled
payment and one :class:`LoanAnchorCorrection` per anchor, in CONTRACT-time order.
Turning those facts into "what is owed on date D" is the FOLD -- re-key each event
by the date it becomes VISIBLE, prefix-sum the paydowns -- which lives in the
balance seam (:mod:`app.services.balance_at._fold`) as of plan step **D-fold**, not
here.  A consumer holding a walk therefore cannot reach a balance from a public leaf
name, which is why the walk needs no fence (the fold that would turn it into money is
seam-private).

**Two consumers, one walk.**  The posting writer
(:mod:`app.services.loan_posting_service`) projects this walk into the balanced
corrections it reconciles onto the general ledger; the seam's read pass folds it
into a balance at a date.  The walk is the leaf both depend on, which is what
makes the posted ledger a re-derivable projection of the loan's facts rather
than a second opinion about them.

**Takes no as-of, and reads no clock**: it walks the loan's FACTS and records
every one of them, whatever their date.  Its output is therefore a function of
the loan's data ALONE -- which is what makes it re-derivable -- and deciding
which facts have HAPPENED as of a date belongs to a reader (the seam's fold).  The
cash ledger's walk
(:func:`app.services.account_posting_service.walk_account_ledger`) has taken no
as-of since Step 3; the loan half caught up at step A3 (``4e46a0a8``).

Reads the loan's rows; no writes, no commit.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.services import (
    escrow_calculator,
    loan_loaders,
    loan_resolver,
)
from app.services.loan_loaders import LoanAnchorFact

from ._events import merge_anchor_and_payment_events
from ._split import LoanPaymentSplit, split_one_payment

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class LoanAnchorCorrection:
    """One anchor's genesis balance correction: an opening or a true-up.

    The per-anchor result of the fold's walk (:func:`walk_loan_ledger`).  Every
    anchor a loan carries -- the one opening and any ``user_trueup`` events --
    drives the running balance to the anchor's verified value at the anchor's
    date, and the walk amortizes forward from there.  The posting writer turns
    each of these into a balanced correction: the opening anchor's is the loan's
    OPENING (its ``owed_before`` is zero, so it books ``-original_principal``
    onto the loan and ``+original_principal`` onto the loan's opening-equity
    account); a ``user_trueup`` anchor's is the append-only TRUE-UP that
    reproduces the resolver's balance jump without editing any prior posting.

    The correction's loan-linked leg is ``owed_before - anchor_balance`` (its
    equity leg the negative), so the two sum to zero and the ledger's implied
    ``owed`` moves from ``owed_before`` to ``anchor_balance``.  A correction whose
    ``owed_before`` already equals the anchor balance (a true-up that matches the
    walked balance) books nothing.

    Attributes:
        anchor: The :class:`~app.services.loan_loaders.LoanAnchorFact` this
            correction books for -- supplies ``anchor_balance`` (the verified
            value the balance resets to), ``anchor_date`` (the correction's civil
            entry date), ``is_opening`` (origination vs. user-trueup, which tags
            the leg and journal-entry kinds), and ``account_id``.
        owed_before: The walk's running balance JUST BEFORE this anchor resets it
            -- ``Decimal("0.00")`` for the opening anchor (always the first
            event), the amortized balance carried down from the prior anchor for a
            user-trueup.  The linked-ledger correction is
            ``owed_before - anchor_balance``.
    """

    anchor: LoanAnchorFact
    owed_before: Decimal


@dataclass(frozen=True)
class LoanLedgerWalk:
    """A loan's full walk output for one scenario: every split, every correction.

    The complete output of the single chronological running-balance walk
    (:func:`walk_loan_ledger`): the per-payment splits AND the per-anchor
    corrections, both in chronological order, sharing ONE running balance so the
    opening, every true-up, and every payment split are guaranteed mutually
    consistent.

    Carries no as-of, because the walk takes none: it is the loan's FACTS
    replayed, whole, and a reader bounds them to a date (see
    :func:`walk_loan_ledger`).

    Attributes:
        payment_splits: One :class:`~._split.LoanPaymentSplit` per settled payment
            -- including one whose pay period has not yet begun (settlement is the
            confirming event; the readers' period bound governs display) --
            chronological.
        anchor_corrections: One :class:`LoanAnchorCorrection` per anchor the loan
            carries (its opening + every user-trueup), whatever its date,
            chronological.  A reader that shows them applies its own display
            bound.
    """

    payment_splits: list[LoanPaymentSplit]
    anchor_corrections: list[LoanAnchorCorrection]


def _replay_events(
    events: list[tuple[bool, object]],
    periods: list,
    escrow_lines: list,
) -> tuple[list[LoanPaymentSplit], list[LoanAnchorCorrection]]:
    """Walk a merged event stream into its splits and anchor corrections.

    The running-balance heart of :func:`walk_loan_ledger`, factored out so the
    loader stays small.  Seeds the balance at zero and, per
    :func:`._events.merge_anchor_and_payment_events`'s order, records each
    anchor's correction (with the balance JUST BEFORE its reset) and resets the
    balance, or splits each payment on the current balance
    (:func:`._split.split_one_payment`) and advances it.

    Args:
        events: The merged ``(is_anchor, item)`` stream in walk order.
        periods: The loan's rate periods (governs each payment's interest rate).
        escrow_lines: The loan's escrow lines with their full version history;
            each payment resolves the escrow in effect on its pay-period start
            via :func:`~app.services.escrow_calculator.escrow_monthly_as_of`.

    Returns:
        ``(payment_splits, anchor_corrections)``, both chronological.
    """
    balance = _ZERO_MONEY
    payment_splits: list[LoanPaymentSplit] = []
    anchor_corrections: list[LoanAnchorCorrection] = []
    for is_anchor, item in events:
        if is_anchor:
            anchor_corrections.append(
                LoanAnchorCorrection(anchor=item, owed_before=balance)
            )
            balance = item.anchor_balance
            continue
        payment_escrow = escrow_calculator.escrow_monthly_as_of(
            escrow_lines, item.pay_period.start_date,
        )
        split, balance = split_one_payment(
            item, balance, periods, payment_escrow,
        )
        payment_splits.append(split)
    return payment_splits, anchor_corrections


def walk_loan_ledger(
    loan_account_id: int, scenario_id: int,
) -> LoanLedgerWalk:
    """Replay a loan's anchors and settled payments into one running balance.

    The SINGLE chronological running-balance walk the whole loan architecture
    derives from.  Seeds the running balance at zero and, in event order
    (:func:`._events.merge_anchor_and_payment_events`), applies each anchor as a
    RESET and splits each settled payment on the reset-aware balance:

    * At an anchor (opening or user-trueup): record a
      :class:`LoanAnchorCorrection` carrying the balance JUST BEFORE the reset,
      then reset the running balance to the anchor's verified value.  The opening
      anchor is always the first event, so its ``owed_before`` is zero.
    * At a settled payment -- INCLUDING one whose pay period has not yet begun
      (settlement is the confirming event; see
      :func:`~app.services.loan_loaders.settled_income_shadows`) -- divide its
      ACTUAL cash into interest / escrow / principal / excess on the current
      running balance (:func:`._split.split_one_payment`), then advance the
      balance.

    Resetting at EVERY anchor -- rather than seeding from the latest anchor only,
    as the resolver does -- is what lets a from-origination sum-of-postings
    reproduce the resolver penny-for-penny on a TRUED-UP loan: the pre-anchor
    payment corrections cancel against the anchor correction, leaving
    ``verified - sum(post-anchor principal)`` (the resolver's own value), while
    the pre-anchor payments' interest still lands in the interest ledger.  On a
    single-origination-anchor loan the reset is a no-op and the walk equals the
    from-origination replay.

    **Takes no as-of, and reads no clock** (see the module docstring).  Each
    payment's escrow is the amount IN EFFECT ON that payment's date
    (effective-dated, NO inflation), so a later escrow change never re-splits a
    past payment.  Reads only (no writes, no commit).

    Args:
        loan_account_id: The loan account whose ledger to walk.
        scenario_id: The budget scenario the payments live in.

    Returns:
        A :class:`LoanLedgerWalk` (payment splits + anchor corrections, both
        chronological).  Both lists are empty when the loan has no
        :class:`~app.models.loan_params.LoanParams` (not yet resolvable -- the N1
        guard); a configured loan always walks, since its origination anchor is
        synthesized.
    """
    params = loan_loaders.load_loan_params(loan_account_id)
    if params is None:
        # Not a configured loan yet (e.g. a payment settled before its
        # LoanParams was created); nothing to walk until it is resolvable.
        return LoanLedgerWalk([], [])
    # The origination anchor is SYNTHESIZED from the immutable params, so a
    # configured loan ALWAYS has at least one fact -- the old "no anchor
    # events" degenerate-fixture guard is structurally unreachable now.
    anchor_facts = loan_loaders.load_loan_anchor_facts(params)

    periods = loan_resolver.resolve_periods(
        params, loan_loaders.load_rate_changes(loan_account_id),
    )
    # Every escrow LINE with its full version history, loaded once; each
    # payment's escrow is resolved (greatest effective_date <= that payment's
    # date, per line) and summed via the shared ``escrow_monthly_as_of``, so a
    # since-removed version still applies to a historical payment and a later
    # escrow change never re-splits a past payment (plan Section 2 / D3).
    escrow_lines = loan_loaders.load_escrow_lines(loan_account_id)
    shadows = loan_loaders.settled_income_shadows(loan_account_id, scenario_id)
    events = merge_anchor_and_payment_events(
        anchor_facts, shadows, params.payment_day,
    )
    payment_splits, anchor_corrections = _replay_events(
        events, periods, escrow_lines,
    )
    return LoanLedgerWalk(payment_splits, anchor_corrections)


def compute_loan_payment_splits(
    loan_account_id: int, scenario_id: int,
) -> list[LoanPaymentSplit]:
    """Return the real split of a loan's settled payments from origination.

    The payment-split view of :func:`walk_loan_ledger`: one
    :class:`~._split.LoanPaymentSplit` per settled payment (whatever its pay
    period), in chronological order, each dividing its ACTUAL cash into interest /
    escrow / principal / excess on the reset-aware running balance (see
    :func:`._split.split_one_payment` for the per-payment math).  Because
    principal is ``cash - interest - escrow``, an extra or short payment lands in
    principal automatically -- the cash is the authority, where the resolver's
    contractual replay discards it and needs an anchor true-up.

    Unlike the resolver it does NOT stop at payoff: every Step-2 cash entry gets a
    matching correction, with post-payoff cash routed to Refund, so the ledger
    stays complete.  Reads only (no writes, no commit).

    Args:
        loan_account_id: The loan account whose settled payments to split.
        scenario_id: The budget scenario the payments live in.

    Returns:
        One :class:`~._split.LoanPaymentSplit` per settled payment, in
        chronological (pay-period-start) order.  Empty (``[]``) when the loan has
        no :class:`~app.models.loan_params.LoanParams` (not yet resolvable -- the
        N1 guard) or no settled payment.
    """
    return walk_loan_ledger(loan_account_id, scenario_id).payment_splits
