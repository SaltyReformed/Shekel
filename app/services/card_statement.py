"""
Shekel Budget App -- The Card Statement (pure)

A credit card's STATEMENT, derived from its terms and the balance seam's
figure -- never stored (ruling **R-CC2**; plan step **credit_card:CC-3**,
design ``docs/design/credit_card_from_scratch.md`` 3.4).  No Flask, no ``db``:
the caller loads the card's terms
(:class:`~app.models.credit_card_params.CreditCardParams`) and reads the fold
through the seam, and every function here takes plain values and returns
plain data -- the :mod:`app.services.rate_period_engine` discipline.

**The cycle is CLOSED-OPEN.**  A statement that closes on the 20th holds every
movement dated from the previous 20th up to but NOT including this one; a row
dated AT the close belongs to the next cycle.  So "the fold at the close
instant" is the balance at the END of the day before the close -- the seam's
:func:`~app.services.balance_at.cash_balance_at` samples the end of a day
(:class:`~app.services.balance_at.CashDayFacts`) -- and that is the figure
:attr:`CycleWindow.valuation_date` names, the same instant ruling **R-CC22**
prices a payday row at ("the card's balance at the end of the day before its
day").  The finance charge (design 3.6) will be a row dated at the close and
so prices the cycle before it, by this same rule.

**Every day-of-month term is NOMINAL and clamps through the ONE clamp**
(:func:`app.utils.dates.clamped_day`, ruling `pay_calendar:R-PC79` /
`recurrence:R-R3`): a close day of 31 is the 31st in January and the 28th in
February, and never decays to the 30th for good.

**Every statement figure is an OWED amount** (developer ruling **R-CC29**,
2026-09-18): positive when the owner owes, negative when the card holds a
credit.  The seam reports a card's cash balance NEGATIVE when money is owed
(the card is a plain liability riding the cash fold, **R-CC14**), and the ONE
flip from that held balance to an owed figure is the seam's
:func:`app.services.balance_at.owed` -- it lived here as ``owed`` until plan
step credit_card:CC-5-5a moved it into the balance seam (ruling **R-CC47**),
so the net-worth surfaces and the statement read ONE flip.  The statement
balance is ``owed(cash_balance_at(account, ctx, window.valuation_date))`` for
the producer that will state it (no ``app/`` module calls this one yet); a
payday row's base under **R-CC22** is the same flip of the fold at the end of
the day before its day.  A consumer that flipped the sign itself would be a
second home for it.

**The due date** (developer ruling **R-CC26**, 2026-09-18) is the first
occurrence of the due day strictly AFTER the close date -- the same month when
its clamped date falls after the close (closes Sep 5, due Sep 28), the next
month otherwise (closes Sep 20, due Oct 15).  Issuers state both days and this
reads them as stated.

All arithmetic is :class:`~decimal.Decimal`; :func:`~app.utils.money.round_money`
is applied at the one boundary where a fraction of a cent can arise (the
percentage of the balance), never in between (**R-CC8**'s convention).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.utils.dates import clamped_day, month_ordinal
from app.utils.money import ZERO, round_money

# The close instant is the end of the day BEFORE the close date.
_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class CycleWindow:
    """One statement cycle: the closed-open span ``[opens, closes)``.

    Attributes:
        opens: The first day IN the cycle -- the previous statement's close
            date.
        closes: This statement's close date -- the first day NOT in the
            cycle.  A movement dated here belongs to the NEXT cycle.
    """

    opens: date
    closes: date

    def contains(self, day: date) -> bool:
        """Return True iff *day* falls inside this cycle (closed-open).

        Args:
            day: Any date.

        Returns:
            ``opens <= day < closes``.
        """
        return self.opens <= day < self.closes

    @property
    def valuation_date(self) -> date:
        """The day whose END-of-day balance is the statement balance.

        The day before :attr:`closes`: the seam values an account at the end
        of a day, and the cycle excludes its close date, so the balance at the
        close instant is the fold at ``closes - 1 day``.
        """
        return self.closes - _ONE_DAY


def cycle_window(close_day: int, closing_month: int) -> CycleWindow:
    """Return the cycle that CLOSES in the month *closing_month* numbers.

    Args:
        close_day: The nominal day of the month the issuer closes a statement
            (1-31, :attr:`~app.models.credit_card_params.CreditCardParams.statement_close_day`).
        closing_month: The absolute month ordinal
            (:func:`app.utils.dates.month_ordinal`) of the month the cycle
            closes in.

    Returns:
        The :class:`CycleWindow` opening at the previous month's close date
        and closing at this month's, each clamped to its month's length.
    """
    return CycleWindow(
        opens=clamped_day(closing_month - 1, close_day),
        closes=clamped_day(closing_month, close_day),
    )


def cycle_containing(close_day: int, day: date) -> CycleWindow:
    """Return the cycle *day* belongs to.

    The cycle whose close is the first close date strictly after *day*: a
    day before this month's close date is in the cycle closing this month; a
    day on or after it is in the one closing next month (closed-open).  The
    placement the payment's "fold at the LAST close" (design 3.5, CC-6) and
    the finance charge's "the cycle before the close it posts at" (design
    3.6, CC-3i) both start from.

    Args:
        close_day: The nominal close day (1-31).
        day: The date to place.

    Returns:
        The :class:`CycleWindow` with ``window.contains(day)``.
    """
    month = month_ordinal(day)
    if day >= clamped_day(month, close_day):
        month += 1
    return cycle_window(close_day, month)


def statement_sequence(
    close_day: int, first: date, last: date,
) -> list[CycleWindow]:
    """Return every cycle whose CLOSE date falls within ``[first, last]``.

    Ascending, one per month whose clamped close date lies in the inclusive
    span; empty when no close date does (a span shorter than a month can hold
    zero or one).

    Args:
        close_day: The nominal close day (1-31).
        first: The earliest close date to include.
        last: The latest close date to include.

    Returns:
        The :class:`CycleWindow` list, oldest first.
    """
    first_month = month_ordinal(first)
    if clamped_day(first_month, close_day) < first:
        first_month += 1
    last_month = month_ordinal(last)
    if clamped_day(last_month, close_day) > last:
        last_month -= 1
    return [
        cycle_window(close_day, month)
        for month in range(first_month, last_month + 1)
    ]


def due_date_for(closes: date, due_day: int) -> date:
    """Return the payment due date of the statement that closed on *closes*.

    The first occurrence of *due_day* strictly after *closes* (**R-CC26**):
    this month's when that date is still ahead of the close, next month's
    otherwise.  A due day equal to the close day is therefore next month's.

    The clamp is the ONE clamp's; the two-line "which month's occurrence"
    comparison over it is also spelled by
    :func:`app.services.rate_period_engine.monthly_due_date` (the first
    occurrence ON OR AFTER a date, over PC-516's re-spelt clamp), and the
    fold of both onto one ``first_day_on_or_after`` beside
    :func:`~app.utils.dates.clamped_day` is that ledger row's, owned by plan
    step ``pay_calendar:C20-b``, which this function is reported to.

    Args:
        closes: The statement's close date (:attr:`CycleWindow.closes`).
        due_day: The nominal due day (1-31,
            :attr:`~app.models.credit_card_params.CreditCardParams.payment_due_day`).

    Returns:
        The due date, clamped to its month's length.
    """
    month = month_ordinal(closes)
    due = clamped_day(month, due_day)
    if due <= closes:
        due = clamped_day(month + 1, due_day)
    return due


def minimum_payment(
    balance: Decimal, percent: Decimal, floor: Decimal,
) -> Decimal:
    """Return the issuer's minimum payment on a statement *balance*.

    ``max(floor, round_money(percent x balance))``, clamped to the balance
    and never below zero (design 3.4; **R-CC29**): a balance smaller than
    the floor is due whole, and a zero or credit balance has nothing due.

    Args:
        balance: The statement balance as an OWED figure
            (:func:`app.services.balance_at.owed`).
        percent: The minimum's fraction of the balance
            (:attr:`~app.models.credit_card_params.CreditCardParams.min_payment_percent`,
            ``Decimal("0.0250")`` for 2.5%).
        floor: The dollar floor of the minimum
            (:attr:`~app.models.credit_card_params.CreditCardParams.min_payment_floor`).

    Returns:
        The minimum, cent-quantized on every arm: ``0.00`` for a zero or
        credit balance, else ``min(max(floor, round_money(percent x
        balance)), balance)``.
    """
    if balance <= ZERO:
        return round_money(ZERO)
    return min(max(floor, round_money(percent * balance)), balance)


def grace_kept(prior_balance: Decimal, credited_by_due: Decimal) -> bool:
    """Return True iff the PRIOR statement was paid in full by its due date.

    Grace (ruling **R-CC2**): no interest accrues on a cycle when the previous
    statement's balance was paid in full by its due date.  The caller sums
    the credits into the card -- payments, refunds, redemptions -- dated from
    the prior close date through its due date inclusive; a credit dated AT the
    close is outside that statement's balance (closed-open) and so counts
    toward paying it.

    Args:
        prior_balance: The prior statement's balance as an OWED figure.
        credited_by_due: The credits into the card dated in
            ``[prior.closes, due_date_for(prior.closes, due_day)]``, as a
            positive amount.

    Returns:
        ``True`` when nothing was owed, or the credits cover the balance.
    """
    if prior_balance <= ZERO:
        return True
    return credited_by_due >= prior_balance
