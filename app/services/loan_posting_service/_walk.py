"""Loan-ledger genesis walk: the single chronological correction producer.

The shared foundation of the genesis loan sub-ledger (the loan half of the
Build-Order posting architecture).  ONE running-balance walk
(:func:`walk_loan_ledger`) replays a loan's anchors and confirmed payments in
date order and produces every correction the ledger needs -- the opening, each
true-up, and each payment split -- from a single running balance, so they can
never disagree on the balance interest accrued on.  The reconcile / posting of
those corrections lives in the sibling modules (:mod:`._payments`,
:mod:`._anchors`); this module only READS and COMPUTES (no writes, no commit).

Reuses the resolver's OWN pure primitives -- the rate-period set
(:func:`app.services.loan_resolver.resolve_periods`) -- and the project's single
installment-date derivation
(:func:`app.services.loan_loaders.loan_payment_due_date`) -- so the posted ledger,
the ledger history reader, and the resolver's replayed balance can never drift on
the rate path or the anchor boundary they consider.

**Genesis, not post-anchor (the read-switch change).**  The Step-4 walk seeded
from the latest anchor and split only post-anchor payments.  Genesis seeds at
zero, applies EVERY anchor as a running-balance RESET, and splits EVERY confirmed
payment from origination -- so a from-origination sum-of-postings reproduces the
resolver on a trued-up loan (the pre-anchor payment corrections cancel against
the anchor correction) while the pre-anchor payments' interest still lands in the
ledger.  On a single-origination-anchor loan the reset is a no-op and the walk
equals the from-origination replay.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.transaction import Transaction
from app.services import (
    escrow_calculator,
    loan_loaders,
    loan_resolver,
)
from app.services.loan_loaders import LoanAnchorFact
from app.services.rate_period_engine import period_for_date
from app.utils.balance_predicates import settled_status_ids
from app.utils.money import accrue_monthly_interest

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class LoanPaymentSplit:
    """The real principal / interest / escrow / refund split of one loan payment.

    The per-payment result of walking a loan's confirmed payments with the ACTUAL
    cash paid (not the scheduled payment) -- see :func:`walk_loan_ledger`.
    Carries the loan-side income shadow it derives from (the payment correction
    books under that shadow's ``transaction_id``, and the sync reads the shadow's
    period / scenario / owner / ``paid_at`` for the entry header) plus the four
    economic parts the cash divides into, all signed for a debit-positive ledger.

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


@dataclass(frozen=True)
class LoanAnchorCorrection:
    """One anchor's genesis balance correction: an opening or a true-up.

    The per-anchor result of the genesis ledger walk
    (:func:`walk_loan_ledger`).  Every anchor a loan carries -- the one
    ``origination`` event and any ``user_trueup`` events -- posts a balanced
    correction that drives the ledger's running balance to the anchor's verified
    value at the anchor's date, then the walk amortizes forward from there.  The
    origination anchor's correction is the loan's OPENING (its ``owed_before`` is
    zero, so it books ``-original_principal`` onto the loan and
    ``+original_principal`` onto the loan's opening-equity account); a
    ``user_trueup`` anchor's correction is the append-only TRUE-UP that reproduces
    the resolver's balance jump without editing any prior posting (plan Section
    3.1 / 3.3, unified into one mechanism per the developer-chosen design).

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
            the leg and journal-entry kinds), and ``account_id``.  The
            origination fact is synthesized from the immutable
            :class:`LoanParams`; a user-trueup fact wraps its stored
            ``user_trueup`` event (the read switch retired the origination
            event write).
        owed_before: The walk's running balance JUST BEFORE this anchor resets it
            -- ``Decimal("0.00")`` for the origination anchor (always the first
            event), the amortized balance carried down from the prior anchor for a
            user-trueup.  The linked-ledger correction is
            ``owed_before - anchor_balance``.
    """

    anchor: LoanAnchorFact
    owed_before: Decimal


@dataclass(frozen=True)
class LoanLedgerWalk:
    """A loan's full genesis correction set for one scenario, as of a date.

    The complete output of the single chronological running-balance walk
    (:func:`walk_loan_ledger`) the whole genesis loan ledger derives from: the
    per-payment splits AND the per-anchor corrections, both in chronological
    order, sharing ONE running balance so the opening, every true-up, and every
    payment split are guaranteed mutually consistent (they can never disagree on
    the balance interest accrued on, the way three independent walks could).

    Attributes:
        payment_splits: One :class:`LoanPaymentSplit` per settled payment --
            including one whose pay period has not yet begun (settlement is the
            confirming event; the readers' period bound governs display) --
            chronological.
        anchor_corrections: One :class:`LoanAnchorCorrection` per anchor
            (origination + user-trueups) dated on or before ``as_of``,
            chronological.
    """

    payment_splits: list[LoanPaymentSplit]
    anchor_corrections: list[LoanAnchorCorrection]


def _split_one_payment(
    shadow: Transaction,
    balance: Decimal,
    periods: list,
    monthly_escrow: Decimal,
) -> tuple[LoanPaymentSplit, Decimal]:
    """Split one payment's cash and return ``(split, balance_after)``.

    The pure per-payment step of :func:`walk_loan_ledger` (the body of its
    running-balance walk), factored out so the recurrence reads as one expression
    and the post-payoff branch is explicit.  ``balance`` is the outstanding
    balance BEFORE this payment; the returned balance is AFTER it
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


def _settled_income_shadows(
    loan_account_id: int,
    scenario_id: int,
) -> list[Transaction]:
    """Return a loan's settled income shadows, in payment order, NO period bound.

    The genesis walk's payment set: the settled loan-side income shadows
    (:func:`app.services.loan_loaders.query_shadow_income` supplies the
    shared "what counts as shadow income" predicate -- transfer-linked, Income
    type, non-deleted, non-excluded), narrowed to the settled statuses -- and
    NOTHING ELSE.  Two bounds the resolver's
    :func:`app.services.rate_period_engine.is_confirmed_payment_eligible` filter
    applies are deliberately ABSENT here:

    * **No post-anchor LOWER bound.**  Genesis walks EVERY confirmed payment
      from origination, because the anchor boundary is a running-balance RESET
      in :func:`walk_loan_ledger`, not a payment exclusion.  A pre-anchor
      payment is split and posted (its principal effect is later subsumed by
      the anchor correction), never silently dropped.
    * **No period-begun UPPER bound.**  Settlement is the confirming event: the
      Step-2 cash entry posts the moment a payment settles, so the split
      correction must post in the SAME moment or the loan-linked ledger holds
      raw cash with no interest / escrow backout from the payment's period
      start until the next loan write (the 2026-07-02 adversarial review's H2 --
      demonstrated as a ~$1,636 understatement on the real Mortgage).  Both
      entries carry the payment's ``pay_period_id``, so the READERS' period
      bound still keeps an early-settled payment out of every displayed balance
      until its period begins -- posting early changes when the fact is
      RECORDED, never when it is SHOWN.

    Sorted by pay-period start -- the app's canonical payment chronology
    (``get_payment_history`` orders identically) and the order the running balance
    is walked in; ``id`` is the deterministic tie-breaker.  This walks the RAW
    shadows; it does NOT apply the resolver's biweekly-collision redistribution (a
    display fix), which is immaterial to a sequentially walked running balance.

    Args:
        loan_account_id: The loan account whose shadows to load.
        scenario_id: The budget scenario to scope to.

    Returns:
        Every settled income shadow, ascending by pay-period start then ``id``.
    """
    settled_shadows = (
        loan_loaders.query_shadow_income(loan_account_id, scenario_id)
        .filter(Transaction.status_id.in_(settled_status_ids()))
        .all()
    )
    settled_shadows.sort(
        key=lambda shadow: (shadow.pay_period.start_date, shadow.id)
    )
    return settled_shadows


def _confirmed_shadows_through(
    loan_account_id: int,
    scenario_id: int,
    as_of: date,
) -> list[Transaction]:
    """Return the settled shadows whose pay period has begun by ``as_of``.

    The DISPLAY subset of :func:`_settled_income_shadows`: the payments the
    balance readers count as confirmed history at ``as_of`` (their shared
    "period has begun" bound).  The history reader
    (:func:`.._reader.confirmed_loan_history_rows`) consumes this so its rows
    match the balance readers' cut; the WRITE-side walk deliberately does NOT
    (it posts a split for every settled payment -- see
    :func:`_settled_income_shadows` for why).

    Args:
        loan_account_id: The loan account whose shadows to load.
        scenario_id: The budget scenario to scope to.
        as_of: The display boundary; a payment whose pay period has not begun
            by it is a forward projection, excluded.

    Returns:
        The settled income shadows through ``as_of``, ascending by pay-period
        start then ``id``.
    """
    return [
        shadow
        for shadow in _settled_income_shadows(loan_account_id, scenario_id)
        if shadow.pay_period.start_date <= as_of
    ]


def _merge_anchor_and_payment_events(
    anchor_facts: list[LoanAnchorFact],
    shadows: list[Transaction],
    payment_day: int,
    as_of: date,
) -> list[tuple[bool, object]]:
    """Merge a loan's anchors and payments into one chronological event stream.

    Returns ``(is_anchor, item)`` tuples in the order the running-balance walk
    must process them so each anchor's RESET lands at the right point relative to
    the payments.  The ordering key is each item's governing date -- an anchor's
    ``anchor_date``, a payment's
    :func:`app.services.loan_loaders.loan_payment_due_date` -- with a PAYMENT
    sorted BEFORE an anchor on a tie, so a payment due exactly on an anchor's date
    is subsumed by (walked before, then overwritten by) that anchor's reset.  That
    is the SAME strict ``anchor_date < due_date`` post-anchor boundary the
    resolver's replay uses (:func:`is_confirmed_payment_eligible`, fed the same
    derivation via :attr:`PaymentRecord.due_date`), applied at EVERY anchor rather
    than the latest only -- the two MUST stay on one derivation, or the posted
    ledger and the replayed balance drift on which payments a given anchor
    subsumes.

    Anchors dated after ``as_of`` are dropped (an anchor cannot reset the balance
    as of a date before it); the shadows are NOT capped -- every settled payment
    walks (see :func:`_settled_income_shadows`), and a not-yet-begun payment's
    future due date simply sorts after every in-window anchor, so it can never
    perturb an anchor's ``owed_before``.  Within a type, ties break
    deterministically -- anchors by
    ``created_at`` (mirroring :func:`select_latest_anchor`; the synthesized
    origination fact carries the earliest possible instant), payments by their
    caller-supplied ``(pay_period.start_date, id)`` order -- preserved by a stable
    sort of the payments-then-anchors concatenation.

    Args:
        anchor_facts: The loan's :class:`~app.services.loan_loaders.LoanAnchorFact`
            list (any order; filtered to ``anchor_date <= as_of`` and sorted here).
        shadows: The confirmed income shadows, PRE-SORTED by
            ``(pay_period.start_date, id)`` (:func:`_confirmed_shadows_through`).
        payment_day: The loan's contractual due day (drives each payment's due date).
        as_of: The evaluation date; anchors after it are excluded.

    Returns:
        ``(is_anchor, item)`` tuples in walk order (``item`` is a
        :class:`~app.services.loan_loaders.LoanAnchorFact` when ``is_anchor``,
        else a settled income :class:`~app.models.transaction.Transaction`).
    """
    anchors_in_window = sorted(
        (anchor for anchor in anchor_facts if anchor.anchor_date <= as_of),
        key=lambda anchor: (anchor.anchor_date, anchor.created_at),
    )
    # Payment tag 0 sorts before anchor tag 1 on an equal date, so a payment due
    # on an anchor's date is walked (and then overwritten) before the reset.  A
    # stable sort of [payments..., anchors...] keeps each type's pre-sorted order
    # for equal keys.
    events = [
        (loan_loaders.loan_payment_due_date(shadow, payment_day), 0, shadow)
        for shadow in shadows
    ] + [
        (anchor.anchor_date, 1, anchor) for anchor in anchors_in_window
    ]
    events.sort(key=lambda event: (event[0], event[1]))
    return [(tag == 1, item) for _date, tag, item in events]


def _replay_events(
    events: list[tuple[bool, object]],
    periods: list,
    escrow_lines: list,
) -> tuple[list[LoanPaymentSplit], list[LoanAnchorCorrection]]:
    """Walk a merged event stream into its splits and anchor corrections.

    The running-balance heart of :func:`walk_loan_ledger`, factored out so the
    loader stays small.  Seeds the balance at zero and, per
    :func:`_merge_anchor_and_payment_events`'s order, records each anchor's
    correction (with the balance JUST BEFORE its reset) and resets the balance,
    or splits each payment on the current balance (:func:`_split_one_payment`)
    and advances it.

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
        split, balance = _split_one_payment(
            item, balance, periods, payment_escrow,
        )
        payment_splits.append(split)
    return payment_splits, anchor_corrections


def walk_loan_ledger(
    loan_account_id: int, scenario_id: int, as_of: date,
) -> LoanLedgerWalk:
    """Replay a loan's anchors and confirmed payments into genesis corrections.

    The SINGLE chronological running-balance walk the whole genesis loan ledger
    derives from (the developer-chosen unified design, superseding the plan's
    separate opening / true-up / split mechanisms).  Seeds the running balance at
    zero and, in date order (:func:`_merge_anchor_and_payment_events`), applies
    each anchor as a RESET and splits each confirmed payment on the reset-aware
    balance:

    * At an anchor (origination or user-trueup) dated on or before ``as_of``:
      record a :class:`LoanAnchorCorrection` carrying the balance JUST BEFORE the
      reset, then reset the running balance to the anchor's verified value.  The
      origination anchor is always the first event, so its ``owed_before`` is
      zero and its correction is the OPENING; a user-trueup's correction is the
      append-only TRUE-UP that reproduces the resolver's balance jump.
    * At a settled payment -- INCLUDING one whose pay period has not yet begun
      (settlement is the confirming event; see :func:`_settled_income_shadows`)
      -- divide its ACTUAL cash into interest / escrow / principal / excess on
      the current running balance (:func:`_split_one_payment`), then advance
      the balance.

    Resetting at EVERY anchor -- rather than seeding from the latest anchor only,
    as the Step-4 walk and the resolver do -- is what lets a from-origination
    sum-of-postings reproduce the resolver penny-for-penny on a TRUED-UP loan:
    the pre-anchor payment corrections cancel against the anchor correction,
    leaving ``verified - sum(post-anchor principal)`` (the resolver's own value),
    while the pre-anchor payments' interest still lands in the interest ledger
    (the genesis record the read switch and Step-5 reporting move onto).  On a
    single-origination-anchor loan the reset is a no-op and the walk equals the
    from-origination replay.

    Reads only (no writes, no commit).  Each payment's escrow is the amount IN
    EFFECT ON that payment's date (effective-dated, NO inflation), so a later
    escrow change never re-splits a past payment.

    Args:
        loan_account_id: The loan account whose ledger to walk.
        scenario_id: The budget scenario the payments live in.
        as_of: The anchor boundary; anchors after it do not reset.  Payments are
            NOT bounded by it -- every settled payment splits, whatever its pay
            period (see :func:`_settled_income_shadows`).

    Returns:
        A :class:`LoanLedgerWalk` (payment splits + anchor corrections, both
        chronological).  Both lists are empty when the loan has no
        :class:`LoanParams` (not yet resolvable -- the N1 guard); a configured
        loan always walks, since its origination anchor is synthesized.
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
    shadows = _settled_income_shadows(loan_account_id, scenario_id)
    events = _merge_anchor_and_payment_events(
        anchor_facts, shadows, params.payment_day, as_of,
    )
    payment_splits, anchor_corrections = _replay_events(
        events, periods, escrow_lines,
    )
    return LoanLedgerWalk(payment_splits, anchor_corrections)


def compute_loan_payment_splits(
    loan_account_id: int, scenario_id: int, as_of: date,
) -> list[LoanPaymentSplit]:
    """Return the real split of a loan's confirmed payments from origination.

    The payment-split view of the genesis ledger walk
    (:func:`walk_loan_ledger`): one :class:`LoanPaymentSplit` per settled
    payment (whatever its pay period), in chronological order, each
    dividing its ACTUAL cash into interest / escrow / principal / excess on the
    reset-aware running balance (see :func:`_split_one_payment` for the
    per-payment math).  Because principal is ``cash - interest - escrow``, an
    extra or short payment lands in principal automatically -- the cash is the
    authority, where the resolver's contractual replay discards it and needs an
    anchor true-up.

    Unlike the Step-4 version this walks EVERY confirmed payment from origination
    (no post-anchor lower bound; the anchor boundary is now a running-balance
    reset inside the walk), and unlike the resolver it does NOT stop at payoff:
    every Step-2 cash entry gets a matching correction, with post-payoff cash
    routed to Refund, so the ledger stays complete.  Reads only (no writes, no
    commit).

    Args:
        loan_account_id: The loan account whose confirmed payments to split.
        scenario_id: The budget scenario the payments live in.
        as_of: The anchor boundary (anchors after it do not reset).  Payments
            are NOT bounded by it (see :func:`_settled_income_shadows`).

    Returns:
        One :class:`LoanPaymentSplit` per settled payment, in chronological
        (pay-period-start) order.  Empty (``[]``) when the loan has no
        :class:`LoanParams` (not yet resolvable -- the N1 guard) or no settled
        payment.
    """
    return walk_loan_ledger(
        loan_account_id, scenario_id, as_of,
    ).payment_splits
