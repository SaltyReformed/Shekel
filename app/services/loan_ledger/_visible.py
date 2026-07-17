"""WHEN a loan event becomes visible to a balance read -- today's rule, reproduced.

The fold reads a loan's SOURCE events, but the balance every screen currently
shows is a sum of POSTINGS.  For the fold to be gradeable against what ships, it
must answer on the same days the posted ledger answers -- so this module
reproduces, from source data, the visibility rule the posting readers apply in
SQL (:func:`app.services.loan_posting_service._asof.effective_date`):

* a **PAYMENT** is visible from its pay period's ``start_date``.  A payment
  settled BEFORE its period begins must not appear in a displayed balance until
  it does: "posting early changes when the fact is RECORDED, never when it is
  SHOWN" (:func:`~app.services.loan_loaders.settled_income_shadows`).
* an **ANCHOR** is visible from ``LEAST(anchor_date, containing period.start)``.

**This rule is wrong, it is reproduced deliberately, and C2 deletes it.**  Two of
its three parts are a boundary predicate standing in for an instant -- this
codebase's signature defect (``docs/audits/balance_architecture/README.md``
Section 8).  An anchor asserts a CIVIL DATE and carries no budget dimension at
all; it is dated from a pay period only because ``journal_entries.pay_period_id``
is NOT NULL, and the ``LEAST`` is a repair for the lie that forces.  The repair is
incomplete: a period that CONTAINS an anchor starts BEFORE it, so the ``LEAST``
collapses to the period start and the anchor becomes visible days EARLY.  That is
finding N-10 -- a loan originating 2026-03-25 reads its full $200,000.00
principal on 2026-03-20 -- contained today only because four separate consumers
each ask ``origination_date`` first.

Reproducing it here is what makes step B2's parallel run an EQUALITY: the fold and
the shipping readers agree on every day, so step C3's cutover provably moves no
money.  Step C2 then replaces both this and the SQL rule with the one honest
clock (ruling R-A: an assertion on its own date, an ACTUAL payment on its settled
date), which MOVES history inside bounded windows -- and every number it moves is
signed off there, against B2's oracle, rather than silently.

Pure apart from the caller-supplied period list; no clock, no writes.
"""

from datetime import date

from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import account_projection


def owner_pay_periods(account_id: int) -> list[PayPeriod]:
    """Return the whole calendar of *account_id*'s owner, ascending.

    EVERY pay period the owner has, ordered by ``period_index`` -- the one query
    behind both consumers of :func:`resolve_anchor_pay_period`, so the fold and
    the posting writer physically cannot be looking at different calendars.

    **"All", not "a window", is the load-bearing word.**  An anchor's visible-on
    date is derived from the period CONTAINING it, so a partial list silently
    changes the answer: with the containing period absent,
    :func:`app.services.account_projection.find_period_containing_date` misses,
    the ``periods[0]`` fallback fires, and the anchor lands on the wrong date.
    Measured: folding a $100,000.00 true-up against the owner's full calendar
    versus a window that excludes its period moves the balance by $150,000.00 on
    the days between.  That is why neither consumer may pass its own list -- the
    grid, whose period argument IS a six-period window
    (:func:`app.routes.grid`), reaches the balance seam with exactly the shape
    that would break it, and step C3 points the seam's AMORTIZING branch here.

    Joins through the account rather than taking an owner id, so a caller cannot
    pair one loan with another user's calendar.

    Args:
        account_id: The account whose owner's pay periods to load.

    Returns:
        The owner's pay periods, ascending by ``period_index``.  Empty only for a
        missing account or an owner with no periods at all -- a broken invariant
        each caller reports in its own terms.
    """
    return (
        db.session.query(PayPeriod)
        .join(Account, Account.user_id == PayPeriod.user_id)
        .filter(Account.id == account_id)
        .order_by(PayPeriod.period_index)
        .all()
    )


def resolve_anchor_pay_period(
    periods: list[PayPeriod], target_date: date,
) -> PayPeriod:
    """Return the pay period an anchor correction dated *target_date* books in.

    ``journal_entries.pay_period_id`` is NOT NULL, so an anchor correction needs a
    period even though the anchor date can predate every period (an imported loan
    whose origination is years before the app's first period).  Uses the period
    CONTAINING *target_date*, falling back to the user's EARLIEST period when the
    date precedes all of them -- so an opening is attributed to a real period and
    the reader (which bounds by period start) counts it from the first period on.

    **Two consumers, one rule.**  The posting WRITER
    (:func:`app.services.loan_posting_service._anchors.reconcile_loan_anchor_corrections`)
    calls it to choose an entry's ``pay_period_id``; :func:`anchor_visible_on`
    calls it to reproduce that choice from SOURCE data, since an anchor's
    visible-on date is derived from the period the writer filed it under.  Two
    copies would let the fold and the posted ledger disagree about which day an
    anchor lands on -- which is the entire question B2's oracle asks.  It lived
    beside the writer until the fold became its second consumer.

    Args:
        periods: The owner's pay periods, ascending by ``period_index`` (non-empty;
            the caller guarantees it).
        target_date: The anchor's date.

    Returns:
        The containing :class:`~app.models.pay_period.PayPeriod`, or the earliest
        when *target_date* precedes all periods.
    """
    containing = account_projection.find_period_containing_date(
        periods, target_date,
    )
    return containing if containing is not None else periods[0]


def anchor_visible_on(anchor_date: date, periods: list[PayPeriod]) -> date:
    """Return the date an anchor's balance correction becomes visible to a read.

    ``LEAST(anchor_date, containing period.start)`` -- the Python twin of the
    anchor arm of :func:`app.services.loan_posting_service._asof.effective_date`.
    See the module docstring for why this rule is wrong and reproduced anyway.

    Args:
        anchor_date: The date the anchor asserts its balance on.
        periods: The owner's pay periods, ascending by ``period_index``
            (non-empty).

    Returns:
        The date from which a balance read counts this anchor.
    """
    period = resolve_anchor_pay_period(periods, anchor_date)
    return min(anchor_date, period.start_date)


def payment_visible_on(shadow: Transaction) -> date:
    """Return the date a settled payment's principal becomes visible to a read.

    Its pay period's ``start_date`` -- the Python twin of the cash arm of
    :func:`app.services.loan_posting_service._asof.effective_date`.  Note this is
    the period the payment's CASH is budgeted to, which need not contain its due
    date: a payment settled late sits in the following period, so its visibility
    can fall AFTER the installment it satisfies (the real Mortgage's July payment
    is due 07-01 and visible from 07-02).  Ruling R-A replaces this with the
    payment's settled date at C2.

    Args:
        shadow: The settled loan-side income shadow (its ``pay_period`` must be
            loaded; :func:`~app.services.loan_loaders.query_shadow_income`
            eager-loads it).

    Returns:
        The date from which a balance read counts this payment's principal.
    """
    return shadow.pay_period.start_date
