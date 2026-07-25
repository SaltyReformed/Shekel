"""The loan fold's per-payment split: how one payment's CASH divides.

The pure per-payment step of the fold's running-balance walk
(:func:`.._fold.walk_loan_ledger`), and the ONE split the whole architecture
uses.  It divides the cash a payment moved into the four economic parts a loan
payment consists of -- interest, escrow, principal, and a payoff overpayment's
refund -- against the balance outstanding at that moment.

**One split, every payment KIND.**  The arithmetic core
(:func:`split_payment_cash`) takes plain data -- a cash amount, the balance
before, the annual rate, and the month's escrow -- so it divides an ACTUAL
settled payment (:func:`split_one_payment`, cash read from the settled shadow), a
PLANNED projected payment (its live D3 cash), and an ESTIMATED contractual
installment (its P&I + escrow) exactly alike.  The cash the grid shows leaving
checking is the cash the loan folds: because ``principal = cash - interest -
escrow``, an extra or short payment lands in principal automatically, where the
resolver's contractual replay discards the real cash and needs an anchor true-up
to recover.  Nothing here reads a schedule row.

Pure: plain data in, plain values out.  No I/O, no clock, no Flask.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.transaction import Transaction
from app.services.rate_period_engine import RatePeriod, period_for_date
from app.utils.money import accrue_monthly_interest

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class PaymentCashSplit:
    """The four economic parts one payment's cash divides into, plus the balance after.

    The pure result of :func:`split_payment_cash`, carrying NO source record --
    so the same arithmetic serves a settled shadow, a projected shadow, and a
    synthesized contractual installment.  :class:`LoanPaymentSplit` is the
    posting-oriented wrapper that additionally carries the ACTUAL settled shadow
    the correction books under.

    Attributes:
        interest: Accrued interest, ``round_money(balance_before * rate / 12)``
            on the real running balance -- an Expense leg (``>= 0``).
        escrow: The monthly escrow supplied for this payment, NO inflation -- an
            Expense leg (``>= 0``).
        principal: The real debt paid down, ``cash - interest - escrow``, capped
            at the outstanding balance.  May be NEGATIVE (an underpayment that
            grows the balance) -- surfaced, never clamped (plan D5).
        excess: A payoff overpayment routed to a Refund Receivable (Asset) leg
            (``>= 0``): cash beyond what closes the loan (plan D4).
        balance_after: The outstanding balance after this payment
            (``balance_before - principal``).
    """

    interest: Decimal
    escrow: Decimal
    principal: Decimal
    excess: Decimal
    balance_after: Decimal


def split_payment_cash(
    cash: Decimal,
    balance: Decimal,
    annual_rate: Decimal,
    monthly_escrow: Decimal,
) -> PaymentCashSplit:
    """Divide one payment's *cash* into interest / escrow / principal / excess.

    The pure arithmetic core of the fold's per-payment step, over plain data so
    every payment KIND splits identically (see the module docstring).  ``balance``
    is the outstanding balance BEFORE this payment.

    Two regimes (plan Section 6):

    * **Loan already closed** (``balance <= 0``): no interest accrues and no escrow
      is due, so the entire cash is an overpayment routed to ``excess`` (a Refund),
      and the balance is unchanged.  This keeps every post-payoff cash entry matched
      by a correction instead of a phantom paydown.
    * **Open loan**: ``interest = round_money(balance * annual_rate / 12)`` (the
      BYTE-IDENTICAL formula :func:`app.services.rate_period_engine._replay_payment_row`
      uses); ``principal = cash - interest - monthly_escrow``; a principal that would
      overrun the balance caps to it, the remainder going to ``excess``.

    Args:
        cash: The cash this payment moved (settled actual, live planned, or
            synthesized contractual).
        balance: The outstanding balance before this payment.
        annual_rate: The annual interest rate governing this payment's installment
            (the caller resolves it -- :func:`period_for_date` for a real payment,
            the installment's rate period for a synthesized one).
        monthly_escrow: The monthly escrow in effect for this payment (``0.00``
            when none applies).

    Returns:
        The :class:`PaymentCashSplit` for this payment.
    """
    if balance <= 0:
        # Already paid off: a further payment is pure overpayment (refund), with no
        # interest and no escrow due, and the balance does not move.
        return PaymentCashSplit(
            interest=_ZERO_MONEY,
            escrow=_ZERO_MONEY,
            principal=_ZERO_MONEY,
            excess=cash,
            balance_after=balance,
        )
    interest = accrue_monthly_interest(balance, annual_rate)
    principal = cash - interest - monthly_escrow
    if principal > balance:
        # Payoff overpayment: principal caps at the remaining balance; the surplus
        # is a refund the lender owes back (plan D4), never absorbed into principal
        # or escrow.
        excess = principal - balance
        principal = balance
    else:
        excess = _ZERO_MONEY
    return PaymentCashSplit(
        interest=interest,
        escrow=monthly_escrow,
        principal=principal,
        excess=excess,
        balance_after=balance - principal,
    )


@dataclass(frozen=True)
class LoanPaymentSplit:
    """The real principal / interest / escrow / refund split of one loan payment.

    The per-payment result of walking a loan's settled payments with the ACTUAL
    cash paid (not the scheduled payment) -- see :func:`.._fold.walk_loan_ledger`.
    Carries the loan-side income shadow it derives from (the posting writer books
    its correction under that shadow's ``transaction_id``, and reads the shadow's
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
        due_date: The contractual installment this payment satisfies
            (:func:`app.services.loan_loaders.loan_payment_due_date`, computed
            ONCE by the merge that orders the walk and carried through here -- plan
            step E1c).  It dates and numbers the CONFIRMED schedule row the seam's
            walk-based view builds (:func:`app.services.balance_at.confirmed_view`),
            where the split's own settled date governs only WHEN the row is visible.
        period: The governing rate period this payment's cash was split against
            (:func:`app.services.rate_period_engine.period_for_date` on the
            payment's pay-period start).  The interest above was accrued at its
            ``annual_rate``; carrying the resolved period (plan step E1c) lets the
            confirmed-view builder read that SAME rate for the row's ``interest_rate``
            and the period's ``period_pi`` for its ``extra_payment`` -- one
            resolution, so a row's displayed rate is provably the rate its interest
            accrued at.
    """

    income_shadow: Transaction
    interest: Decimal
    escrow: Decimal
    principal: Decimal
    excess: Decimal
    due_date: date
    period: RatePeriod


def split_one_payment(
    shadow: Transaction,
    balance: Decimal,
    periods: list,
    monthly_escrow: Decimal,
    due_date: date,
) -> tuple[LoanPaymentSplit, Decimal]:
    """Split one payment's cash and return ``(split, balance_after)``.

    The pure per-payment step of :func:`.._fold.walk_loan_ledger` (the body of its
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

    The two regimes and the arithmetic are :func:`split_payment_cash`; this reads
    the ACTUAL cash off the shadow, resolves the rate from the shadow's pay-period
    start, and wraps the result with the shadow the posting writer books under,
    the ``due_date`` the caller already derived, and the resolved rate period.

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
        due_date: The contractual installment this payment satisfies
            (:func:`app.services.loan_loaders.loan_payment_due_date`), computed by
            the caller (:func:`.._events.merge_anchor_and_payment_events` already
            derives it to ORDER the walk, and threads it here so it is not
            re-derived).  Stored on the split for the confirmed-view builder;
            this function does not use it in the cash math.

    Returns:
        ``(LoanPaymentSplit, balance_after)``.
    """
    period = period_for_date(periods, shadow.pay_period.start_date)
    parts = split_payment_cash(
        shadow.effective_amount, balance, period.annual_rate, monthly_escrow,
    )
    split = LoanPaymentSplit(
        income_shadow=shadow,
        interest=parts.interest,
        escrow=parts.escrow,
        principal=parts.principal,
        excess=parts.excess,
        due_date=due_date,
        period=period,
    )
    return split, parts.balance_after
