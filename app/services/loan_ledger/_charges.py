"""What a loan is CHARGED, per accrual period -- the TIME half of every walk.

Plan step **X-au-g-2c-3b-1**.  A loan payment is one CHARGE and one ALLOCATION,
and plan step **R16-a** is the proof that they must be separate values: while
both rode on the payment RECORD, N payments inside one month charged N months
and the payment COUNT was the clock.  Measured on a production clone, 30
payments of ``$531.94`` fourteen days apart charged the identical ``$1,096.34``
as 30 a month apart, split for split.

The ALLOCATION half moved to :mod:`app.utils.money` at plan step
**X-au-g-2c-3a**, because it sat ABOVE two of the four walks that needed it and
each had restated it inline.  **This module is the same move for the CHARGE
half, one tier up.**  ``balance_at._plan._charges_for`` built the charge
calendar and could not be reached from the settled walk: ``balance_at`` has an
import closure of 50 modules and reaches ``loan_ledger``, so the arrow runs the
wrong way for sharing.  ``loan_ledger`` (closure 23) is BELOW it and already
imports every input a charge needs -- the rate periods
(:mod:`app.services.rate_period_engine`) and the escrow lines
(:mod:`app.services.escrow_calculator`) -- so the calendar lives here and BOTH
walks take it.

Pure: plain data in, plain values out.  No I/O, no clock, no Flask.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services import escrow_calculator
from app.services.rate_period_engine import period_for_date


def installment_slot(due: date) -> tuple[int, int]:
    """Return the ``(year, month)`` installment a due date belongs to.

    The project's identity for "which contractual installment is this?", shared
    by the charge calendar below, the forward plan's PLANNED-vs-ESTIMATED de-dup
    (``balance_at._plan._month_slot``) and the tax reader's settled-slot merge
    (``balance_at._loan_interest._due_slot``).

    **It is the CALENDAR month, and for a loan whose ``payment_day`` is not the
    1st that is not the contract's own period** -- the Van Loan's runs the 22nd
    to the 22nd, so two payments 23 days apart inside one period key to two
    slots while two payments 2 days apart across a boundary key to one.  That is
    finding **D55**, owned by ``recurrence:R16-c``, and this function ADOPTS the
    existing key rather than introducing it: re-keying it would move the
    installment identity across the whole loan architecture at once, which is
    that step's job and not this one's.
    """
    return (due.year, due.month)


@dataclass(frozen=True)
class AccrualCharge:
    """What one ACCRUAL PERIOD charges a loan -- its interest rate and its escrow.

    The TIME half of a loan's walk, and the value that stops the payment count
    being the clock (plan step **R16-a**).  A loan charges interest because time
    passed and impounds escrow because a month began; neither is a fact about a
    payment, and while they rode ON one, N payments inside a month charged N
    months.

    One charge per accrual period the walk's payments occupy, dated at the
    EARLIEST payment due in that period -- so for the one-payment-per-month
    shape every live loan is in, the charge lands on exactly the date the payment
    used to resolve its own rate and escrow at, and the fold is byte-identical.
    A second payment inside the same period then clears no fresh charge and pays
    pure principal.

    **It carries a RATE and not an interest AMOUNT**, because interest accrues on
    the balance standing when the charge falls, and only the walk knows that --
    an anchor between two payments resets it.  The escrow is an amount: it is a
    function of the date alone.

    Attributes:
        on_date: The date this period's charge falls, in CONTRACT time -- the
            date its rate and its escrow are both resolved AS OF (ruling D5), and
            where it sorts against the payments in a walk.  The charge is applied
            BEFORE any payment sharing its date, since interest is charged on the
            balance a payment has not yet reduced.
        annual_rate: The annual rate governing this period's accrual
            (:func:`~app.services.rate_period_engine.period_for_date` on
            ``on_date``).
        escrow: The monthly escrow in force for this period
            (:func:`~app.services.escrow_calculator.escrow_monthly_as_of` on
            ``on_date``), ``0.00`` when the loan escrows nothing.
    """

    on_date: date
    annual_rate: Decimal
    escrow: Decimal


def charges_for_due_dates(
    due_dates: list[date], periods: list, escrow_lines: list,
) -> list[AccrualCharge]:
    """Return one :class:`AccrualCharge` per accrual period *due_dates* occupy.

    The ONE charge calendar, taken by the settled walk
    (:func:`.._walk.walk_loan_ledger`) and by the forward plan
    (``balance_at._plan._charges_for``).  Derived from the installments the
    payments SATISFY rather than from the payments themselves, which is the
    whole of R16-a: the count of charges cannot depend on the count of payments.

    **The charge is dated at the EARLIEST due date in its period, and that is
    what makes this byte-identical for a monthly loan.**  With one payment to a
    month that date IS the payment's own due date, so the rate and the escrow
    resolve exactly where a per-payment charge used to resolve them -- contract
    time, ruling D5.  Deriving the date from the CONTRACTUAL schedule instead
    would have been the more obvious rule and is not the safer one: a payment
    whose stored due date is not on the contractual day would then have its
    charge resolved on a different date from the one that priced it.

    **A period with no payment at all gets no charge**, which is today's rule
    kept deliberately rather than extended.  Charging every ELAPSED period would
    make a delinquent balance GROW, against the forward plan's own "an overdue
    slot with no record holds flat" (finding B-9).  That is finding **D53**, a
    ruling owned by ``recurrence:R16-b-2``, and it is deliberately NOT taken
    here: this step moves a rule between tiers and must not also change it.

    Args:
        due_dates: The installment dates the walk's payments satisfy, in any
            order.  Duplicates collapse onto one charge, which is the point.
        periods: The loan's rate periods
            (:func:`app.services.loan_resolver.resolve_periods`).
        escrow_lines: The loan's escrow lines with their full version history
            (:func:`app.services.loan_loaders.load_escrow_lines`).  Empty for a
            loan that escrows nothing, which yields ``0.00`` on every charge.

    Returns:
        One :class:`AccrualCharge` per occupied period, ascending by
        ``on_date``.  Empty for empty *due_dates*.
    """
    opens_on: dict[tuple[int, int], date] = {}
    for due in due_dates:
        slot = installment_slot(due)
        standing = opens_on.get(slot)
        if standing is None or due < standing:
            opens_on[slot] = due
    return [
        AccrualCharge(
            on_date=on_date,
            annual_rate=period_for_date(periods, on_date).annual_rate,
            escrow=escrow_calculator.escrow_monthly_as_of(
                escrow_lines, on_date,
            ),
        )
        for on_date in sorted(opens_on.values())
    ]
