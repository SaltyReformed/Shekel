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
walks take it.  *The two closure figures were measured 2026-09-02 and are quoted
as the REASON the duplication was forced rather than chosen; re-measure them
before citing them again, since an undated measurement quoted as a reason decays
invisibly.*

**Since plan step recurrence:R16-c-2 the calendar is the CONTRACT's, in every
walk** (rulings **R-R72**, **R-R89** and **R-R100**).  Until that step the
settled walk charged one period per month its PAYMENTS occupied -- a month
nobody paid was never charged, so a skipped month's interest vanished from the
posted ledger (finding **D53**'s past half) -- while the forward plan charged
the contract's installments after the loan's latest assertion, a second
calendar kept apart from the first by a set of ``(year, month)`` slots.  Now a
loan is charged on EVERY contractual installment from origination
(:func:`contract_charges` over :func:`installment_dates`), and a payment
belongs to the installment whose interval it falls in -- the latest one due on
or before it (ruling R-R89, finding **D55**) -- which the replay answers by
handing each payment the charge standing over it.  An assertion clears every
charge standing before it (R-R72 part (2)), so a mid-life loan's months before
its tracking start are charged and cleared by that statement.

Pure: plain data in, plain values out.  No I/O, no clock, no Flask.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.services import escrow_calculator
from app.services.rate_period_engine import (
    RatePeriod,
    first_installment_date,
    monthly_due_date,
    period_for_date,
)

_ONE_DAY = timedelta(days=1)


def installment_dates(
    origination_date: date, payment_day: int, through: date,
) -> list[date]:
    """Return a loan's contractual installment dates, its first through *through*.

    **The loan's ONE calendar** (plan step recurrence:R16-c-2, rulings
    **R-R72**, **R-R89** and **R-R100**): the dates a loan is CHARGED on --
    every one of them from its first installment onward, whether or not a
    payment lands on it -- and the grid the loan page's band chart and the
    plan's post-contractual extension step along.  It adds no date arithmetic
    of its own: the first date is
    :func:`~app.services.rate_period_engine.first_installment_date`, and each
    next one is the first *payment_day* after the one before
    (:func:`~app.services.rate_period_engine.monthly_due_date` from the next
    day), which clamps *payment_day* to each month afresh -- so a due day of
    29-31 returns to it after a short month (Jan 31, Feb 28, Mar 31), where
    stepping a month count from a CLAMPED date keeps the clamp.

    Args:
        origination_date: The loan's immutable
            :attr:`~app.models.loan_params.LoanParams.origination_date`.
        payment_day: The loan's contractual day-of-month due day, 1-31.
        through: The last day the sequence may reach; a date before the first
            installment yields an empty list.

    Returns:
        The installment dates, ascending, one per calendar month.
    """
    dates: list[date] = []
    due = first_installment_date(origination_date, payment_day)
    while due <= through:
        dates.append(due)
        due = monthly_due_date(due + _ONE_DAY, payment_day)
    return dates


@dataclass(frozen=True)
class AccrualCharge:
    """What one ACCRUAL PERIOD charges a loan -- its interest rate and its escrow.

    The TIME half of a loan's walk, and the value that stops the payment count
    being the clock (plan step **R16-a**).  A loan charges interest because time
    passed and impounds escrow because a month began; neither is a fact about a
    payment, and while they rode ON one, N payments inside a month charged N
    months.

    One charge per CONTRACTUAL installment, dated at the installment (plan step
    recurrence:R16-c-2): a payment faces the charge of the installment whose
    interval it falls in, and a second payment inside one interval arrives with
    nothing standing and pays pure principal.

    **It carries a RATE and not an interest AMOUNT**, because interest accrues on
    the balance standing when the charge falls, and only the walk knows that --
    an anchor between two payments resets it.  The escrow is an amount: it is a
    function of the date alone.  *Since plan step X-au-g-2c-3b-2 the rate arrives
    as the whole governing ``RatePeriod`` rather than a bare fraction, so the
    value now also carries the CONTRACT facts that period holds -- its level P&I,
    its index, its remaining term.  The sentence above stays true of what a
    charge COSTS; what widened is what a charge KNOWS.*

    **The rate it carries is the whole governing :class:`RatePeriod`, and plan
    step X-au-g-2c-3b-2 is why.**  The accrual samples ``period.annual_rate``;
    the CONFIRMED schedule row a settled payment builds displays that same rate
    and sizes its extra-payment split against the same period's ``period_pi``
    (:func:`~app.services.rate_period_engine.confirmed_amortization_row`).  While
    the charge carried a bare rate, the row re-resolved the period on the
    PAYMENT's own due date -- identical for the one-payment-a-month shape, and
    divergent for the shape this step exists to serve: a rate change effective
    inside a period would tag a second payment's row with the NEW rate while its
    (zero) interest belonged to the old one.  Resolving ONCE, on the accrual
    period the charge IS, is what makes "the row's rate is the rate its interest
    accrued at" provable rather than true by coincidence.

    Attributes:
        on_date: The installment this charge falls on, in CONTRACT time -- the
            date its rate and its escrow are both resolved AS OF (ruling D5), and
            where it sorts against the payments in a walk.  The charge is applied
            BEFORE any payment sharing its date, since interest is charged on the
            balance a payment has not yet reduced.
        period: The :class:`~app.services.rate_period_engine.RatePeriod`
            governing this period's accrual
            (:func:`~app.services.rate_period_engine.period_for_date` on
            ``on_date``).  Its ``annual_rate`` drives the accrual; every payment
            in this accrual period carries the whole period onto its split, so
            the displayed rate and the accrued interest come from one resolution.
        escrow: The monthly escrow in force for this period
            (:func:`~app.services.escrow_calculator.escrow_monthly_as_of` on
            ``on_date``), ``0.00`` when the loan escrows nothing.
    """

    on_date: date
    period: RatePeriod
    escrow: Decimal


@dataclass(frozen=True)
class LoanCalendar:
    """A loan's CONTRACT terms -- everything its charge calendar is built from.

    Ruling **R-R100** (plan step recurrence:R16-c-2): ONE function builds a
    stream's charges from these terms (:func:`contract_charges`, through the
    stream's last event), for the posted ledger's facts and for a screen's
    facts plus plan alike, and the forward plan carries this value rather than
    a charge list of its own.  Nothing here is a balance or a payment: the
    note's day, its day-of-month, the rates it charges and the escrow it
    impounds.

    Attributes:
        origination_date: The loan's immutable
            :attr:`~app.models.loan_params.LoanParams.origination_date`; the
            first installment falls the month after it
            (:func:`installment_dates`).
        payment_day: The loan's contractual day-of-month due day, 1-31.
        periods: The loan's rate periods
            (:func:`app.services.loan_resolver.resolve_periods`).
        escrow_lines: The loan's escrow lines with their full version history
            (:func:`app.services.loan_loaders.load_escrow_lines`); empty for a
            loan that escrows nothing, which charges ``0.00`` escrow.
    """

    origination_date: date
    payment_day: int
    periods: Sequence[RatePeriod]
    escrow_lines: Sequence


def contract_charges(calendar: LoanCalendar, through: date) -> list[AccrualCharge]:
    """Return one :class:`AccrualCharge` per contractual installment through *through*.

    THE charge calendar (ruling **R-R100**), taken by every walk through the
    one composer that decides how far a stream reaches
    (:func:`.._replay.with_contract_charges`).  Every installment from the
    loan's first (:func:`installment_dates`) is charged, whether or not a
    payment lands on it: a skipped month owes its interest and its escrow, and
    the next payment clears those arrears before it reaches principal (ruling
    **R-R72**, finding **D53**).  Each charge
    resolves its rate period and its escrow on its own installment date -- the
    contract's day, ruling D5 -- so a later rate or escrow change never
    re-prices an earlier month.

    Args:
        calendar: The loan's :class:`LoanCalendar`.
        through: The last day a charge may fall on.

    Returns:
        The charges, ascending by ``on_date``; empty when *through* is before
        the loan's first installment.
    """
    return [
        AccrualCharge(
            on_date=on_date,
            period=period_for_date(calendar.periods, on_date),
            escrow=escrow_calculator.escrow_monthly_as_of(
                calendar.escrow_lines, on_date,
            ),
        )
        for on_date in installment_dates(
            calendar.origination_date, calendar.payment_day, through,
        )
    ]
