"""The loan fold's per-payment split: how one payment's CASH divides.

The pure per-payment step of the fold's running-balance walk
(:func:`.._fold.walk_loan_ledger`), and the ONE split function the whole
architecture uses.  It divides the cash a payment ACTUALLY moved into the four
economic parts a loan payment consists of -- interest, escrow, principal, and a
payoff overpayment's refund -- against the balance outstanding at that moment.

**One split, every payment.**  The cash the grid shows leaving checking is the
cash the loan folds: because ``principal = cash - interest - escrow``, an extra
or short payment lands in principal automatically, where the resolver's
contractual replay discards the real cash and needs an anchor true-up to
recover.  Nothing here reads a schedule row.

Pure: plain data in, plain values out.  No I/O, no clock, no Flask.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.models.transaction import Transaction
from app.services.rate_period_engine import period_for_date
from app.utils.money import accrue_monthly_interest

_ZERO_MONEY = Decimal("0.00")


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
    """

    income_shadow: Transaction
    interest: Decimal
    escrow: Decimal
    principal: Decimal
    excess: Decimal


def split_one_payment(
    shadow: Transaction,
    balance: Decimal,
    periods: list,
    monthly_escrow: Decimal,
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
        # The loan is already paid off: a further settled payment is pure
        # overpayment (refund), with no interest and no escrow due.
        split = LoanPaymentSplit(
            income_shadow=shadow,
            interest=_ZERO_MONEY,
            escrow=_ZERO_MONEY,
            principal=_ZERO_MONEY,
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
        excess = _ZERO_MONEY
    split = LoanPaymentSplit(
        income_shadow=shadow,
        interest=interest,
        escrow=monthly_escrow,
        principal=principal,
        excess=excess,
    )
    return split, balance - principal
