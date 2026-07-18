"""The loan fold: ONE running-balance walk over ONE event stream.

The single chronological walk a loan's whole architecture derives from.  It
seeds the balance at zero and, in event order (:mod:`._events`), applies each
anchor as a RESET and splits each settled payment on the reset-aware balance
(:mod:`._split`) -- so the loan's opening, every true-up, and every payment split
come from ONE running balance and can never disagree on the balance interest
accrued on, the way three independent walks could.

**Two consumers, one walk.**  The posting writer
(:mod:`app.services.loan_posting_service`) projects this walk into the balanced
corrections it reconciles onto the general ledger; the fold's readers project it
into a balance at a date.  The walk is the leaf both depend on, which is what
makes the posted ledger a re-derivable projection of the loan's facts rather
than a second opinion about them.

**Takes no as-of, and reads no clock**: it walks the loan's FACTS and records
every one of them, whatever their date.  Its output is therefore a function of
the loan's data ALONE -- which is what makes it re-derivable -- and deciding
which facts have HAPPENED as of a date belongs to a reader.  The cash ledger's
walk (:func:`app.services.account_posting_service.walk_account_ledger`) has taken
no as-of since Step 3; the loan half caught up at step A3 (``4e46a0a8``).

Reads the loan's rows; no writes, no commit.
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services import (
    escrow_calculator,
    loan_loaders,
    loan_resolver,
)
from app.services.loan_loaders import LoanAnchorFact
from app.utils.money import round_money

from ._events import merge_anchor_and_payment_events
from ._split import LoanPaymentSplit, split_one_payment
from ._visible import anchor_visible_on, owner_pay_periods, payment_visible_on

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


def _dated_deltas(
    walk: LoanLedgerWalk, loan_account_id: int,
) -> list[tuple[date, Decimal]]:
    """Return the walk's ``(visible_on, delta)`` steps, ascending by visible date.

    The bridge from the walk (which orders events by when they HAPPENED) to a
    balance read (which counts them from when they became VISIBLE).  Each event
    contributes the amount it moved the running balance by:

    * an anchor: ``anchor_balance - owed_before`` -- the jump its reset booked;
    * a payment: ``-principal`` -- the debt its cash actually paid down.

    Those are exactly the amounts the posting writer books onto the loan's linked
    ledger (negated for the debit-positive convention), which is why prefix-summing
    them reproduces the sum-of-postings readers.

    **The deltas are computed in EVENT order and then re-keyed by VISIBLE date,
    and that is deliberate -- it is not the same function as re-folding the
    visible prefix.**  A payment's split depends on the balance at its
    installment, so the walk must run in contract order; the ledger then stores
    those amounts and a reader sums whichever are visible.  The two orders can
    disagree (a payment's visibility is its budgeted period's start, its event
    date is its due date -- the real Mortgage's July payment is due 07-01 and
    visible 07-02), and where they do, this reproduces what the posted ledger
    actually does rather than an idealisation of it.  Step C2's one clock
    collapses the distinction: visible date IS event date, and the two functions
    become one.

    Args:
        walk: The loan's :class:`LoanLedgerWalk` (:func:`walk_loan_ledger`).
        loan_account_id: The loan, to load its owner's calendar from (the anchor
            visibility rule needs it).

    Returns:
        ``[(visible_on, delta), ...]`` ascending by ``(visible_on, tag)``, where a
        PAYMENT tags before an ANCHOR on a shared date -- mirroring the walk's own
        tie-break (:func:`._events.merge_anchor_and_payment_events`), so reading
        the list shows the same chronology the walk applied.  The order within a
        date is immaterial to the prefix sum (addition commutes); it is mirrored
        so that step C2, which collapses visible-date onto event-date, finds the
        two already agreeing rather than silently reconciling them.

    Raises:
        ValueError: When the loan has facts to date but its owner has no pay
            periods -- there is no calendar to date an assertion against.  The
            posting writer refuses the same state
            (:func:`app.services.loan_posting_service._anchors.reconcile_loan_anchor_corrections`).
    """
    if not walk.anchor_corrections:
        # No LoanParams -> no facts at all (walk_loan_ledger's N1 guard; a
        # configured loan ALWAYS has its opening fact, per
        # ``load_loan_anchor_facts``).  Nothing to date, so no calendar needed --
        # which is what keeps the fold total for a non-loan account.
        return []
    periods = owner_pay_periods(loan_account_id)
    if not periods:
        raise ValueError(
            f"loan account {loan_account_id} has balance facts to date but its "
            f"owner has no pay periods; an assertion's visible-on date is "
            f"derived from the period containing it, so there is no calendar to "
            f"date it against.  Generate the owner's pay periods "
            f"(pay_period_service.generate_pay_periods) and re-read."
        )
    # Tag 0 = payment, 1 = anchor: the same tie-break the walk applies, so a
    # payment sharing an anchor's date reads before it here too.
    tagged: list[tuple[date, int, Decimal]] = [
        (
            anchor_visible_on(correction.anchor.anchor_date, periods),
            1,
            correction.anchor.anchor_balance - correction.owed_before,
        )
        for correction in walk.anchor_corrections
    ] + [
        (payment_visible_on(split.income_shadow), 0, -split.principal)
        for split in walk.payment_splits
    ]
    tagged.sort(key=lambda step: (step[0], step[1]))
    return [(visible_on, delta) for visible_on, _tag, delta in tagged]


def fold_loan_balances(
    loan_account_id: int,
    scenario_id: int,
    dates: list[date],
) -> dict[date, Decimal]:
    """Return the loan's folded balance at each of *dates* -- from SOURCE events.

    The fold's read side, and the reference the optimized readers are graded
    against (plan step B2).  ONE walk of the loan's facts
    (:func:`walk_loan_ledger`), re-keyed by each event's visible-on date
    (:func:`_dated_deltas`), prefix-summed, and sampled at every requested date --
    so N dates cost one walk, not N.

    **It reads the loan's SOURCE rows and never the postings table.**  That is the
    whole point: the posted ledger is a PROJECTION of this fold, so an answer
    derived independently of it is what can prove the projection faithful.  Where
    the two disagree, the ledger is a stale cache -- a detectable, repairable
    inconsistency -- rather than the outage a missing posting is today.

    **TOTAL over every DATE and every ACCOUNT.**  There is no date it refuses and
    no account it refuses: asked about a date before any event -- or about an
    account with no :class:`~app.models.loan_params.LoanParams` at all -- it
    returns ``0.00``, the correct fold of an empty prefix; asked about a future
    date it answers rather than raising.  This is the property the whole arc turns
    on (plan Section 3): the posting readers' partiality (``None`` for an unopened
    ledger, a RAISE for a future date) is what forces every caller to compose them
    with a projection, a seed, a flag or a fallback, and every composition is a new
    producer that can disagree with the others.  A total function has nothing to
    compose with.  A caller that must distinguish "owed nothing" from "no loan"
    asks the FACT (``origination_date``), never this function's zero.

    That totality is over its QUESTION, not over a corrupt database.  One state
    still raises, loudly and on purpose: a loan with balance facts whose owner has
    NO pay periods has no calendar to date an assertion against, and the posting
    writer already refuses it (see :func:`_dated_deltas`).  Papering that over
    with a zero would be the "no record" / "no debt" confusion this arc exists to
    end.

    **ACTUAL events only.**  It folds what is RECORDED -- the loan's anchors and
    its settled payments -- so it answers the past.  PLANNED payments (the future)
    arrive with the plan at step C3; asked about a future date today it holds the
    last recorded balance flat, which is honest for what it knows but is NOT the
    projection the seam shows.  Grade it on the past.

    **N-11 (a raw transaction typed onto a loan) is closed by construction (BG,
    ruling R-E), not a live gap.**  A raw settled transaction typed directly onto a
    loan account would post onto the loan's linked ledger where the sum-of-postings
    readers count it, while the fold -- whose payment set is transfer-linked shadows
    only (:func:`~app.services.loan_loaders.settled_income_shadows`) -- would not
    see it, so the two would disagree by that transaction's amount ($300.00
    measured).  Ruling R-E FORBIDS that write at every source (the two
    transaction-create routes, the recurrence-template form, and the salary-profile
    picker each refuse an amortizing account -- BG, ``dba91dc0``), so no such row
    can enter the fold's domain; B2 both demonstrates the divergence on a forced
    row and asserts the sources refuse it.  Do NOT read a disagreement in that
    shape as a stale cache to be repaired.

    Reads only -- no writes, no commit.  Not wired into any production path (plan
    step B1): its consumer is B2's parallel-run oracle until C3 makes the seam's
    AMORTIZING dispatch read it.

    Args:
        loan_account_id: The loan account to fold.
        scenario_id: The budget scenario the payments live in.
        dates: The dates to value the loan at, in any order.  Duplicates collapse.

    Returns:
        ``{date: balance owed}`` -- one cent-quantized ``Decimal`` per requested
        date.  ``{}`` for an empty *dates*.

    Raises:
        ValueError: When the loan has facts to date but its owner has no pay
            periods (see :func:`_dated_deltas`).
    """
    steps = _dated_deltas(
        walk_loan_ledger(loan_account_id, scenario_id), loan_account_id,
    )
    # The prefix cumulative at each distinct visible date, ascending, so each
    # requested date is one bisect rather than a re-sum.
    boundaries: list[date] = []
    cumulative_at_boundary: list[Decimal] = []
    running = _ZERO_MONEY
    for visible_on, delta in steps:
        running += delta
        if boundaries and boundaries[-1] == visible_on:
            cumulative_at_boundary[-1] = running
            continue
        boundaries.append(visible_on)
        cumulative_at_boundary.append(running)

    folded: dict[date, Decimal] = {}
    for on_date in dates:
        # The count of boundaries at or before this date; the last one's prefix is
        # the answer (0.00 when none precede -- the empty prefix's honest fold).
        count = bisect_right(boundaries, on_date)
        folded[on_date] = round_money(
            cumulative_at_boundary[count - 1] if count > 0 else _ZERO_MONEY
        )
    return folded
