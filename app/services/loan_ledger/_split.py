"""The loan walk's per-payment FACT: what one payment's cash turned out to be.

The value one settled payment contributes to a loan's walk -- its interest,
escrow, principal and refund, on the real running balance -- and the one
construction of it.

**Nothing here divides anything any more, and the two steps that emptied it are
the point.**  A loan payment is one CHARGE and one ALLOCATION:

* the ALLOCATION moved to :mod:`app.utils.money` at plan step
  **X-au-g-2c-3a** (:func:`~app.utils.money.apply_payment_cash`), because it sat
  ABOVE two of the four walks that needed it -- ``amortization_engine`` and
  ``rate_period_engine`` could not reach up without the import cycle
  ``loan_ledger._split -> rate_period_engine -> amortization_engine`` -- so each
  had restated it inline;
* the CHARGE and the REPLAY that consumes it moved to :mod:`._charges` and
  :mod:`._replay` at plan steps **X-au-g-2c-3b-1** and **X-au-g-2c-3b-2**, for
  the same reason one tier up: the forward fold in ``balance_at`` could not hand
  its rule DOWN to this package, so it stated its own.

``split_payment_cash`` -- the fused "charge a month, then divide this payment's
cash" composition this module used to own -- is DELETED with the second of those.
Its own docstring conceded it was correct only while a loan takes ONE payment per
accrual period, and the settled walk was its last caller.  The retired
composition survives as an ORACLE in
``tests/oracles/loan_monthly_composition.py``, where the suite still folds a
one-payment-a-month plan through it as an INDEPENDENT second opinion on the
charge-per-period replay -- deleting it outright would have left the producer
grading itself.

What is left here is the FACT: the four economic parts, the record they came
from, the installment they satisfy and the rate period they accrued at.

Pure: plain data in, one value out.  No I/O, no clock, no Flask.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.transaction import Transaction
from app.services.rate_period_engine import RatePeriod

from ._replay import PaymentOutcome


@dataclass(frozen=True)
class LoanPaymentSplit:
    """The real principal / interest / escrow / refund split of one loan payment.

    The per-payment result of walking a loan's settled payments with the ACTUAL
    cash paid (not the scheduled payment) -- see :func:`.._walk.walk_loan_ledger`.
    Carries the loan-side income shadow it derives from (the posting writer books
    its correction under that shadow's ``transaction_id``, and reads the shadow's
    period / scenario / owner / ``settled_on`` for the entry header) plus the four
    economic parts the cash divides into, all signed for a debit-positive ledger.

    Attributes:
        income_shadow: The settled loan-side income :class:`Transaction` (the
            ``to``-account leg of the payment transfer).  Its
            :func:`~app.services.row_valuation.owned_contribution` is the cash
            ``principal`` falls out of; its ``transaction_id`` keys the
            correction.
        interest: The interest CHARGE this payment cleared -- an Expense leg
            (``>= 0``).  ``round_money(balance_before * rate / 12)`` on the REAL
            running balance for the payment that OPENS its accrual period, and
            ``0.00`` for one that follows another inside the same period, which
            clears no fresh charge (plan step X-au-g-2c-3b-2).
        escrow: The escrow CHARGE this payment cleared, NO inflation (the exact
            figure in force on the period's own date) -- an Expense leg
            (``>= 0``).  ``0.00`` for a payment that follows another inside one
            accrual period, on the same rule as ``interest``.
        principal: The real debt paid down, ``cash - interest - escrow``, capped
            at the outstanding balance.  May be NEGATIVE (an underpayment that
            grows the balance) -- surfaced, never clamped (plan D5).
        excess: A payoff overpayment routed to a Refund Receivable (Asset) leg
            (``>= 0``): cash beyond what closes the loan, never mislabeled as
            escrow or principal (plan D4).
        due_date: The contractual installment this payment satisfies
            (:func:`app.services.loan_loaders.loan_payment_due_date`, computed
            ONCE by the event stream that orders the walk and carried through here
            -- plan step E1c).  It dates and numbers the CONFIRMED schedule row the
            seam's walk-based view builds
            (:func:`app.services.balance_at.confirmed_view`), where the split's own
            settled date governs only WHEN the row is visible.
        period: The governing rate period of the ACCRUAL PERIOD this payment's
            installment falls in -- carried off that period's own
            :class:`~._charges.AccrualCharge`, which resolved it once
            (:func:`app.services.rate_period_engine.period_for_date` on the
            charge's date -- contract time, ruling D5).  The interest above
            accrued at its ``annual_rate``; the confirmed-view builder reads that
            SAME rate for the row's ``interest_rate`` and the period's
            ``period_pi`` for its ``extra_payment``.  **ONE resolution, so a
            row's displayed rate is provably the rate its interest accrued at**
            -- which re-resolving on each payment's own due date could not
            promise once a period may hold two payments (plan step
            X-au-g-2c-3b-2).
    """

    income_shadow: Transaction
    interest: Decimal
    escrow: Decimal
    principal: Decimal
    excess: Decimal
    due_date: date
    period: RatePeriod


def split_one_payment(outcome: PaymentOutcome) -> LoanPaymentSplit:
    """Build one settled payment's :class:`LoanPaymentSplit` from its replay outcome.

    The bridge from the shared replay (:func:`.._replay.replay_loan_events`, which
    knows only cash and dates) back to the loan-specific fact the posting writer
    and the confirmed-view builder consume.  It performs no arithmetic: the four
    parts are the allocation's verbatim, and this attaches the record they came
    from, the installment they satisfy, and the rate period they accrued at.

    **The split inputs key on CONTRACT time, the ledger on CASH time** (ruling
    D5 / R-A, corrected at finding N-34).  Ordering, the rate the charge resolved,
    and the escrow it carries all key on the DUE date -- the installment the
    payment satisfies -- so out-of-order or late settlement can never re-split an
    installment: a payment made a day late is still the payment for ITS month, at
    ITS month's rate.  A pay period starts up to ~2 weeks BEFORE the installment
    it pays, so keying on the period start would let a rate version effective
    inside that window govern the wrong side of the boundary.  Only VISIBILITY --
    which day the split's principal counts from -- keys on the settled date
    (:func:`app.services.loan_ledger.payment_visible_on`).

    Args:
        outcome: The payment's :class:`~._replay.PaymentOutcome` -- its ``event``
            carries the settled income shadow as ``source`` and the installment as
            ``on_date``, its ``charge`` the accrual period standing over it, and
            its ``split`` the four parts the ONE allocation produced.  **The
            charge comes off the outcome rather than being looked up here**, so
            the pairing is stated ONCE, by the replay that made it (plan step
            X-au-g-2c-3b-2 built it the other way first, and an adversarial
            review measured that as a second association rule agreeing with the
            first only by construction).

    Returns:
        The payment's :class:`LoanPaymentSplit`.
    """
    return LoanPaymentSplit(
        income_shadow=outcome.event.source,
        interest=outcome.split.interest,
        escrow=outcome.split.escrow,
        principal=outcome.split.principal,
        excess=outcome.split.excess,
        due_date=outcome.event.on_date,
        period=outcome.charge.period,
    )
