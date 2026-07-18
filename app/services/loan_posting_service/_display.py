"""Loan-detail display read producers: the Loop B rebuild's measured surfaces.

The display read side of the genesis loan sub-ledger, split from the core
readers (:mod:`._reader`) as the loan-detail rebuild's producers landed and the
reader approached the module-size limit.  Where :mod:`._reader` answers the
balance scalar / map, the Schedule-A interest, and the amortization history
rows, this module answers the loan DETAIL page's three measured surfaces:

* the principal-paid-YTD chip (:func:`confirmed_loan_principal_in_year`, the
  paid-date sibling of the reader's interest chip so the two describe ONE set of
  payments);
* the confirmed payment-history table (:func:`confirmed_loan_payment_history`,
  each payment's real cash / principal / interest / escrow split); and
* the balance-anchors drift scorecard (:func:`loan_balance_anchor_history`, each
  opening / true-up paired with what the ledger had computed just before it).

Every producer reuses the reader's shared per-shadow / linked helpers and the
walk the postings derive from, so no display surface can drift from the balance
the readers report.  Reads only -- no writes, no commit.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.enums import LedgerAccountKindEnum
from app.services.loan_ledger import walk_loan_ledger
from app.services.loan_loaders import load_loan_params, loan_payment_due_date
from app.services.posting_service import _ledger_account_for
from app.utils.money import round_money

from ._reader import (
    _attribute_net_by_shadow_to_year,
    _confirmed_history_inputs,
    _has_opening_posting,
    _interest_net_by_shadow,
    _net_by_shadow_for_kind,
    _principal_net_by_shadow,
)

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class LoanPaymentHistoryRow:
    """One confirmed loan payment, split into its real economic parts.

    The display row of :func:`confirmed_loan_payment_history`: a single confirmed
    payment read from the genesis ledger, carrying the ACTUAL cash paid and its
    real principal / interest / escrow split (the posted legs, not the schedule's
    contractual replay -- so an extra or short payment shows honestly).  Every
    row is confirmed by construction (the producer bounds to payments whose pay
    period has begun), so the table renders a Confirmed badge on each.

    ``cash`` is the payment's full cash (the loan-side income shadow's
    ``effective_amount``); ``principal + interest + escrow`` equals it for an
    ordinary payment.  The one case they diverge is a payoff OVERPAYMENT, whose
    surplus is a lender refund (a receivable) rather than principal -- there
    ``cash`` exceeds the split sum by that refund; see
    :func:`app.services.loan_ledger.split_one_payment`.

    Attributes:
        due_date: The monthly installment the payment satisfies
            (:func:`app.services.loan_loaders.loan_payment_due_date` -- the
            shadow's own stored ``due_date``) -- the same date the amortization
            schedule rows it.  NOT derived from the pay period, so a payment
            settled late still reports the installment it actually paid.
        cash: The full cash paid (the income shadow's ``effective_amount``),
            cent-quantized.
        principal: The real debt paid down (the payment's net on the loan's
            linked ledger), cent-quantized; may be negative for an underpayment.
        interest: The real interest the payment's split posted, cent-quantized.
        escrow: The real escrow the payment's split posted, cent-quantized.
    """

    due_date: date
    cash: Decimal
    principal: Decimal
    interest: Decimal
    escrow: Decimal


@dataclass(frozen=True)
class LoanAnchorDrift:
    """One anchor event with the ledger's pre-correction balance (the drift row).

    The display row of :func:`loan_balance_anchor_history`: a loan's origination
    OPENING or a user balance TRUE-UP, paired with what the ledger had computed
    the balance to be the instant BEFORE the anchor reset it -- the running
    scorecard of recorded-vs-reality.

    ``drift = recorded - computed`` is the append-only jump the anchor booked:
    for a true-up it is "the lender said ``recorded``; my ledger had amortized to
    ``computed``, off by ``drift``."  For the origination opening ``computed`` is
    ``0.00`` (the loan opens from nothing), so ``drift`` equals the original
    principal and is NOT a meaningful correction -- the display treats the
    opening row specially (``is_opening``).

    Attributes:
        anchor_date: The date the balance was asserted (origination date for the
            opening).
        recorded: The asserted balance (``anchor_balance``): the original
            principal for the opening, the operator's dated assertion for a
            true-up.
        computed: The ledger's running balance JUST BEFORE this anchor's reset
            (the walk's ``owed_before``); ``0.00`` for the opening.
        drift: ``recorded - computed`` -- the correction the anchor booked.
        is_opening: ``True`` for the loan's opening (its origination), ``False``
            for a tracking-start or a user true-up (both balance assertions).
        is_tracking_start: ``True`` for a ``tracking_start`` assertion (a mid-life
            import's balance-as-of-date), so the display badges that row "Tracking
            start"; ``False`` for the origination opening (badged "Origination")
            and every user true-up.
    """

    anchor_date: date
    recorded: Decimal
    computed: Decimal
    drift: Decimal
    is_opening: bool
    is_tracking_start: bool


def confirmed_loan_principal_in_year(
    loan_account_id: int, scenario_id: int, year: int,
) -> Decimal | None:
    """Return a loan's actual PRINCIPAL paid in a calendar year (genesis ledger).

    The principal-paid sibling of
    :func:`app.services.loan_posting_service.confirmed_loan_interest_in_year`,
    attributed on the SAME basis -- each confirmed payment's real principal (its
    net on the loan's linked ledger) placed in the civil year of its
    display-timezone paid date -- so the loan-detail page's "interest paid" and
    "principal paid" chips describe one set of payments and never disagree.
    Principal is the real debt paid down (extra principal included, a
    payoff-overpayment's refund excluded), read from the posted legs, so an extra
    or short payment counts honestly rather than at the contractual split.

    Returns ``None`` when the loan has no OPENING posting in the scenario (an
    unconfigured / un-backfilled loan), matching the interest reader's fallback
    contract; a configured loan with no principal paid in *year* returns
    ``Decimal("0.00")``.

    Reads only -- no writes, no commit.

    Args:
        loan_account_id: The loan account whose paid principal to sum.
        scenario_id: The budget scenario to scope to.
        year: The calendar year to sum principal paid within.

    Returns:
        The actual principal paid during *year* as a cent-quantized ``Decimal``,
        or ``None`` when the loan has no opening posting in the scenario.

    Raises:
        PostingError: If the loan account has no linked ledger account (from
            :func:`._ledger_account_for`).
    """
    linked = _ledger_account_for(loan_account_id)
    if not _has_opening_posting(linked.id, scenario_id):
        return None
    return _attribute_net_by_shadow_to_year(
        _principal_net_by_shadow(loan_account_id, scenario_id), year,
    )


def confirmed_loan_payment_history(
    loan_account_id: int, scenario_id: int, as_of: date,
) -> list[LoanPaymentHistoryRow] | None:
    """Return a loan's confirmed payments split into their real economic parts.

    One :class:`LoanPaymentHistoryRow` per confirmed payment whose pay period has
    begun by *as_of* -- the same confirmed cut as
    :func:`app.services.loan_posting_service.confirmed_loan_history_rows` and the
    balance readers, so the table agrees with the balance and schedule --
    chronological, each carrying the ACTUAL cash paid and its real principal /
    interest / escrow split read from the posted ledger legs, never the
    schedule's contractual replay.

    ``cash`` is the loan-side income shadow's ``effective_amount``; ``principal``
    is its net on the loan's linked ledger
    (:func:`._reader._principal_net_by_shadow`); ``interest`` and ``escrow`` are
    its net ``loan_interest`` / ``loan_escrow`` legs.  For an ordinary payment
    ``principal + interest + escrow == cash``; a payoff overpayment's surplus is
    a lender refund excluded from all three (see :class:`LoanPaymentHistoryRow`).

    Returns ``None`` when the loan has no :class:`LoanParams` or no OPENING
    posting in the scenario (unconfigured / un-backfilled), so the caller hides
    the section rather than showing a misleading empty table -- the same fallback
    contract as ``confirmed_loan_history_rows``.

    Reads only -- no writes, no commit.

    Args:
        loan_account_id: The loan account whose confirmed payments to read.
        scenario_id: The budget scenario to scope to.
        as_of: The display boundary; must be on or before ``date.today()``.  A
            payment whose pay period has not begun by it is a forward projection,
            excluded.

    Returns:
        The chronological confirmed payment rows (possibly empty for a configured
        loan with no confirmed payment yet), or ``None`` when the ledger cannot
        answer for this loan / scenario.

    Raises:
        ValueError: If *as_of* is after ``date.today()`` (out of the confirmed
            reader's domain -- a future date is a forward projection).
        PostingError: If the loan account has no linked ledger account (from
            :func:`._ledger_account_for`).
    """
    if as_of > date.today():
        raise ValueError(
            f"confirmed_loan_payment_history answers only as_of <= today; got "
            f"{as_of.isoformat()}.  A future date is a forward projection."
        )
    inputs = _confirmed_history_inputs(loan_account_id, scenario_id, as_of)
    if inputs is None:
        return None
    params, _linked, shadows = inputs

    # Per-shadow economics read from the posted legs, keyed by shadow id.  The
    # principal map covers every settled payment; indexing it by the
    # confirmed-through-as_of shadows is what keeps this table's split on the
    # same cut as the balance and schedule.
    principal_by_shadow = _principal_net_by_shadow(loan_account_id, scenario_id)
    interest_by_shadow = _interest_net_by_shadow(loan_account_id, scenario_id)
    escrow_by_shadow = _net_by_shadow_for_kind(
        loan_account_id, scenario_id, LedgerAccountKindEnum.LOAN_ESCROW,
    )
    # Sorted by the INSTALLMENT the payment satisfies, matching how the ledger
    # history reader (:func:`confirmed_loan_history_rows`) orders its rows and
    # how the amortization table reads.  The shadows arrive in PAY-PERIOD order,
    # which is a different sequence once settlement timing is a first-class case:
    # a payment pre-paid for a later installment sits in an earlier period than
    # one paid late for an earlier installment, so iterating the shadows verbatim
    # would render the due dates out of order and disagree with the schedule.
    # ``shadow.id`` breaks a tie (two payments against one installment) with the
    # stable recording order.
    by_installment = sorted(
        shadows,
        key=lambda shadow: (
            loan_payment_due_date(shadow, params.payment_day), shadow.id,
        ),
    )
    return [
        LoanPaymentHistoryRow(
            due_date=loan_payment_due_date(shadow, params.payment_day),
            cash=round_money(shadow.effective_amount),
            principal=round_money(
                principal_by_shadow.get(shadow.id, _ZERO_MONEY)
            ),
            interest=round_money(
                interest_by_shadow.get(shadow.id, _ZERO_MONEY)
            ),
            escrow=round_money(
                escrow_by_shadow.get(shadow.id, _ZERO_MONEY)
            ),
        )
        for shadow in by_installment
    ]


def loan_balance_anchor_history(
    loan_account_id: int, scenario_id: int, as_of: date,
) -> list[LoanAnchorDrift] | None:
    """Return a loan's anchor events with the ledger's pre-correction balance.

    One :class:`LoanAnchorDrift` per anchor the loan carries on or before *as_of*
    -- its origination OPENING and every user balance TRUE-UP -- chronological,
    each paired with what the genesis walk had computed the balance to be the
    instant BEFORE the anchor reset it (the walk's ``owed_before``).  The drift
    (``recorded - computed``) is the append-only jump the anchor booked: the
    running scorecard of the operator's asserted balance vs the ledger's replay
    that replaces the old use of the amortization schedule as a trust check.

    Derived from the SAME deterministic walk
    (:func:`app.services.loan_ledger.walk_loan_ledger`) the loan's
    opening / true-up postings are reconciled from, so a drift row and the posted
    correction it describes can never disagree.

    **The *as_of* bound is applied HERE, on the walk's output, not inside it.**
    The walk records every anchor the loan carries whatever its date (it reads no
    clock); deciding which have HAPPENED by a display date is this reader's job,
    and an anchor dated after *as_of* has not yet reset the balance, so it is not
    yet a drift row.  Filtering after the walk cannot change what the surviving
    rows say: an anchor's ``owed_before`` is the running balance of the events
    BEFORE it, which admitting a LATER anchor cannot move.

    Returns ``None`` when the loan has no :class:`LoanParams` (unconfigured -- not
    a loan yet), so the caller hides the card.  A configured loan always has at
    least the synthesized origination opening -- though a loan that has not
    originated by *as_of* correctly shows NO rows: nothing has happened to it yet.

    Reads only -- no writes, no commit.

    Args:
        loan_account_id: The loan account whose anchor history to read.
        scenario_id: The budget scenario the payments live in (drives the running
            balance the drift is measured against).
        as_of: The display boundary; an anchor dated after it has not yet reset
            the balance and is excluded.

    Returns:
        The chronological anchor drift rows (origination first), or ``None`` when
        the account is not a configured loan.
    """
    if load_loan_params(loan_account_id) is None:
        return None
    corrections = [
        correction
        for correction in walk_loan_ledger(
            loan_account_id, scenario_id,
        ).anchor_corrections
        if correction.anchor.anchor_date <= as_of
    ]
    return [
        LoanAnchorDrift(
            anchor_date=correction.anchor.anchor_date,
            recorded=round_money(correction.anchor.anchor_balance),
            computed=round_money(correction.owed_before),
            drift=round_money(
                correction.anchor.anchor_balance - correction.owed_before
            ),
            is_opening=correction.anchor.is_opening,
            is_tracking_start=correction.anchor.is_tracking_start,
        )
        for correction in corrections
    ]
