"""A loan's INSTALLMENT CALENDAR: one rule, and every date question derived from it.

**The rule.**  A loan's installment number ``k`` (``k >= 1``) falls on its due
day in the ``k``-th month after the month it originated, clamped to that
month's end -- due the 31st: Jan 31, Feb 28, Mar 31.  The clamp is the
project's ONE month-day clamp, :func:`app.utils.dates.clamped_day` over
:func:`app.utils.dates.month_ordinal` (recurrence ruling **R-R3**, pay-calendar
ruling **R-PC79**), which the recurrence walk, the pay grid and the card
statement already land a meant day with.  A clamp re-applied to each month
afresh is what returns a 31st-of-the-month loan to the 31st after a February,
where stepping a month from a CLAMPED date keeps the clamp.

**Every answer here is worked out from that rule when it is asked, and none is
stored** (plan step recurrence:R16-c-2; the developer, ruling on the design:
*"I don't want any second copy or anything stored that can go stale especially
if it can be derived"*).  The list of a loan's installments enumerates it; the
installment a date falls in inverts it with month arithmetic rather than a
search over a list; the first installment is its first term; and the due date
on or after a pay period's start reads the same clamp from that month.  Until
that step the rule was written out by hand in the loan rate engine (the month
step and the due-date fallback) beside the one clamp.  The installment lookup
ruling **R-R104** needs was first built, inside that step, as a search over a
list rebuilt from origination for every payment priced: measured 2026-09-24 on
a production copy with a mortgage's payments switched to derive mode (rolled
back), pricing its forward plan's payments took 103 ms of a 162 ms build,
against 1.4 ms before, and the lookup worked out from the rule takes 1.5 ms.

**It sits BELOW every loan layer** -- it imports :mod:`app.utils.dates` and
nothing else -- so the rate engine, the loan loaders, the charge calendar
(:func:`app.services.loan_ledger.contract_charges`), the cash price
(:func:`app.services.cash_ledger._loan_installment._installment_cash`), the
forward plan and the loan page all reach the one rule from wherever they are.

Pure: no I/O, no clock, no Flask.
"""

from datetime import date

from app.utils.dates import clamped_day, month_ordinal


def due_in_following_month(reference: date, payment_day: int) -> date:
    """Return the loan's due day in the month after *reference*'s month.

    The rule's one-month step: whatever day of its month *reference* is, the
    answer is *payment_day* in the NEXT calendar month, clamped to that
    month's end.  It seeds the loan's first installment from its origination
    (:func:`first_installment_date`) and the rate engine's contractual
    schedule from its latest row or anchor
    (:func:`app.services.rate_period_engine.replay_schedule`).

    Args:
        reference: Any date; only its month is read.
        payment_day: The loan's contractual day-of-month due day, 1-31.

    Returns:
        *payment_day* in the month after *reference*'s, day-clamped.
    """
    return clamped_day(month_ordinal(reference) + 1, payment_day)


def first_installment_date(origination_date: date, payment_day: int) -> date:
    """Return the date of a loan's FIRST contractual installment.

    The project's single derivation of "when does this loan's first payment
    come due?", and the loan's own convention rather than a calendar guess:
    the ``payment_day`` of the month AFTER origination -- never a
    ``payment_day`` falling later in the origination month itself.
    Concretely, a loan originating 2026-04-15 with ``payment_day`` 20 first
    bills 2026-05-20, NOT 2026-04-20.  It is installment number 1 of the
    rule (:func:`installment_dates`).

    Deliberately NOT :func:`monthly_due_date`, which answers a different
    question (the first ``payment_day`` ON OR AFTER a date -- the installment
    a pay period contains) and would return that wrong 2026-04-20.

    Exposed because the recurrence bound needs it
    (:func:`app.services.loan_recurrence_sync.loan_cadence_start` makes it the
    rule's ``starts_on`` -- its FIRST OCCURRENCE since ruling R-R16 -- so no
    payment generates before the loan exists, plan step C9a).  The
    alternative -- reading
    ``contractual_schedule_from_origination(...)[0].payment_date`` -- yields
    the identical date (pinned by test) but builds the loan's entire 360-row
    schedule and needs its rate feed to answer a question no rate can
    influence.

    It lived in :mod:`app.services.rate_period_engine` until plan step
    recurrence:R16-c-2 moved the rule here (the developer's ruling assigns
    the loan's copies of the clamp to the loan steps; pay-calendar step
    ``C20-b`` keeps the salary cockpit's and the recurrence row-dater's).

    Args:
        origination_date: The loan's immutable
            :attr:`~app.models.loan_params.LoanParams.origination_date`.
        payment_day: The loan's contractual day-of-month due day, 1-31.

    Returns:
        The first contractual installment's due date.
    """
    return due_in_following_month(origination_date, payment_day)


def monthly_due_date(period_start: date, payment_day: int) -> date:
    """Return the first *payment_day* on or after *period_start*.

    A loan payment is recorded against the pay period whose range contains
    its monthly due date, so for a payment carrying no stored due date the
    first ``payment_day`` at or after its period's start IS that due date
    (:func:`app.services.loan_loaders.installment_for`'s fallback).  The pay
    period start alone is too coarse for the anchor-boundary comparison ("did
    this payment come due after the balance was last verified?"): a true-up
    dated between a period's start and that period's due date would strand
    the payment in the gap.

    It reads the rule's clamp in *period_start*'s own month and steps to the
    next month only when that day has already passed, so a ``payment_day``
    of 31 resolves to Feb 28/29 in February.  It lived in
    :mod:`app.services.rate_period_engine` until plan step recurrence:R16-c-2
    (see :func:`first_installment_date`).

    Args:
        period_start: The date to resolve from -- a payment's pay-period
            start.
        payment_day: The loan's contractual day-of-month due day, 1-31.

    Returns:
        The first date on or after *period_start* whose day is
        *payment_day* (day-clamped to the month's length).
    """
    candidate = clamped_day(month_ordinal(period_start), payment_day)
    if candidate >= period_start:
        return candidate
    return due_in_following_month(period_start, payment_day)


def installment_dates(
    origination_date: date, payment_day: int, through: date,
) -> list[date]:
    """Return a loan's contractual installment dates, its first through *through*.

    The rule enumerated: installment ``k`` for ``k = 1, 2, ...`` -- number 1
    being :func:`first_installment_date` -- while it falls on or before
    *through*.  These are the dates a loan is CHARGED on
    -- every one of them from its first installment onward, whether or not a
    payment lands on it (:func:`app.services.loan_ledger.contract_charges`,
    rulings **R-R72**, **R-R89** and **R-R100**) -- and the grid the loan
    page's band chart and the plan's post-contractual extension step along.
    A caller that needs ONE installment asks :func:`installment_of` rather
    than searching this list.

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
    ordinal = month_ordinal(first_installment_date(origination_date, payment_day))
    due = clamped_day(ordinal, payment_day)
    while due <= through:
        dates.append(due)
        ordinal += 1
        due = clamped_day(ordinal, payment_day)
    return dates


def installment_of(
    origination_date: date, payment_day: int, day: date,
) -> date | None:
    """Return the installment whose INTERVAL *day* falls in, or ``None`` before the first.

    **Ruling R-R89's pairing** ("B: contract interval", plan step
    recurrence:R16-c-2): a loan payment belongs to the latest installment due
    ON or BEFORE it, so a payment due on the 10th of a loan due on the 22nd
    pays the previous month's installment, not the one twelve days later.
    Ruling **R-R104** ("Price on the interval") made it the ONE answer every
    tier asks: the payment's CASH is priced on this installment's rate period
    and escrow
    (:func:`app.services.cash_ledger._loan_installment._installment_cash`),
    and the forward plan's contract-only estimate counts an installment
    covered when a record falls in its interval
    (``app.services.balance_at._plan._estimated_from_contract``).

    **The rule inverted, not searched.**  The installment in *day*'s own
    month is the clamp there; when that has not yet fallen by *day*, the
    interval opened on the month before's.  Either way it is an installment
    only from number 1 on, :func:`first_installment_date`'s month.

    **The replay's pairing is the same answer by construction.**  The replay
    hands a payment whatever charge stands when it walks
    (:func:`app.services.loan_ledger.replay_loan_events`), and it charges
    every date :func:`installment_dates` lists -- the same rule -- with a
    charge sorting before a payment on its own date, so a payment walked on
    its own date faces exactly this installment's charge.  The one payment it
    differs for is an OVERDUE projection pushed past a later recorded fact,
    which R-R89 pairs with what stands at the push (the catch-up pays pure
    principal); its cash is priced on its own interval, as it would be had
    nothing pushed it.

    Args:
        origination_date: The loan's immutable
            :attr:`~app.models.loan_params.LoanParams.origination_date`.
        payment_day: The loan's contractual day-of-month due day, 1-31.
        day: The date to place -- a payment's due date in contract time
            (:func:`app.services.loan_loaders.installment_for`).

    Returns:
        The latest installment on or before *day*, or ``None`` when *day*
        precedes the first installment: ruling R-C's early extra, a payment
        after origination and before any installment falls, which no charge
        stands over.
    """
    ordinal = month_ordinal(day)
    installment = clamped_day(ordinal, payment_day)
    if installment > day:
        ordinal -= 1
        installment = clamped_day(ordinal, payment_day)
    if ordinal < month_ordinal(first_installment_date(origination_date, payment_day)):
        return None
    return installment
