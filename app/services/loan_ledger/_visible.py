"""WHEN a loan event becomes visible to a balance read -- the ONE honest clock.

The fold reads a loan's SOURCE events, but the balance every screen shows is a
sum of POSTINGS, so the fold must count each event on the SAME day the posted
ledger does or the two would diverge (step B2's parallel run is an EQUALITY).
That day is the day the event HAPPENED, and it is already the day the posting
carries in ``journal_entries.entry_date``:

* a **PAYMENT** is visible from its **settled date** -- the shadow's STORED
  ``transactions.settled_on``, read through the SAME
  :func:`app.utils.balance_predicates.settled_day` accessor the posting writer
  stamps the payment's ``entry_date`` with
  (:func:`app.services.posting_service._transaction_entry_date`) and the cash
  walk dates its own settles with, and the SAME date the checking outflow moves
  on, so the loan and checking move together (ruling R-A).

  **There is no derivation and no fallback left here, and that is plan step
  X-f1** (ruling R-EC).  It WAS the display-timezone civil date of the shadow's
  ``paid_at``, falling back to its pay period's ``start_date`` when the instant
  was NULL (the developer ruling of 2026-07-17); the day is a stored fact now,
  and a settled shadow carrying none is REFUSED rather than dated by a fallback.

  **The zone moved from UTC to ``America/New_York`` at ruling R-DH (b)**
  (2026-07-31), together with the cash half, because a split zone is what pulls a
  transfer's two legs onto different days: a payment recorded at 20:38 Eastern is
  00:38 the NEXT day in UTC, so the checking outflow moved on the user's Monday
  while the loan principal fell on Tuesday.  That ruling is what the stored
  column now records directly.  Measured on production 2026-07-31, when the day
  was still derived: of 9 settled payment shadows exactly ONE was affected -- a
  ``$1,910.95`` mortgage payment stamped 2026-07-02 00:38:53 UTC, the evening of
  2026-07-01 Eastern and the last day of that pay period.
* an **ANCHOR** is visible from its **own civil date** (``anchor_date``) -- the one
  date it ever asserts, and the ``entry_date`` the anchor correction is posted at
  (:func:`app.services._posting_reconcile.emit_anchor_correction_entry`).

**This is step C2 -- the one clock that replaced the two boundary predicates the
old rule used.**  Before it, a payment counted from its pay period's ``start_date``
and an anchor from ``LEAST(anchor_date, containing period.start)`` -- both a
boundary predicate standing in for an instant, this codebase's signature defect
(``docs/audits/balance_architecture/README.md`` Section 8).  The anchor ``LEAST``
in particular made an anchor visible days EARLY, so a loan originating 2026-03-25
read its full $200,000.00 principal on 2026-03-20 (finding N-10).  Counting each
event on its own date closes that at the source: an anchor dated in the future is
simply not yet visible, and the four ``origination_date`` guards that contained
the leak are retired (N-10).

Also here, and for the same reason, the CALENDAR those rules resolve against:
:func:`owner_pay_periods` (the owner's period list) and
:func:`find_period_containing_date` (which period a date falls in), the primitive
:func:`resolve_anchor_pay_period` is built on.  The locator arrived at plan step
D1b from ``account_projection``, an account-KIND classifier that had no business
owning a chronology rule -- a COHESION correction (the primitive belongs with the
rules built on it, and this module had been importing the classifier to reach its
own), not the private-import smell Section 8 names; that import was public and
ordinary.

**Chronology only.**  Every name here returns a ``date``, a ``PayPeriod``, or a
list of them; none yields a balance-at-T, which is why all five are ruled
non-producers of the balance fence.  (A returned ``PayPeriod`` is an ORM row, so
money is reachable from it by relationship -- the ruling is that a period is not
an account's balance, not that a figure is unreachable.  Claiming the stronger
thing is how ``LoanState.current_balance`` shipped.)  No clock and no writes
anywhere; :func:`owner_pay_periods` is the one function that queries (it loads
the calendar), and the rest are pure.  An anchor's visible-on date no longer needs
the owner's calendar at all -- it is the anchor's own date -- so the fold is
total over the loan's facts without a period list.
"""

from datetime import date

from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.utils.balance_predicates import settled_day


def owner_pay_periods(account_id: int) -> list[PayPeriod]:
    """Return the whole calendar of *account_id*'s owner, ascending.

    EVERY pay period the owner has, ordered by ``period_index`` -- the one query
    behind both consumers of :func:`resolve_anchor_pay_period`, so the fold and
    the posting writer physically cannot be looking at different calendars.

    **"All", not "a window", is the load-bearing word.**  An anchor's visible-on
    date is derived from the period CONTAINING it, so a partial list silently
    changes the answer: with the containing period absent,
    :func:`find_period_containing_date` misses, the ``periods[0]`` fallback
    fires, and the anchor lands on the wrong date.
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


def find_period_containing_date(
    periods: list[PayPeriod], target: date,
) -> PayPeriod | None:
    """Return the pay period whose interval contains *target*.

    A period "contains" *target* when
    ``period.start_date <= target <= period.end_date``.  When no period contains
    *target* (the date falls in a gap or beyond the user's generated horizon),
    falls back to the latest period whose ``end_date`` is on or before *target*;
    if none exists either, returns ``None``.

    The fallback preserves the period-END-keyed semantic when a target date sits
    just past the last generated period: the user's last known balance at the
    horizon is the natural answer, rather than nothing at all.

    **Chronology, not balance.**  It answers WHICH PERIOD a date falls in and
    knows nothing of accounts or money -- the primitive
    :func:`resolve_anchor_pay_period` is built on, and the reason both live here.
    It moved from ``account_projection`` at plan step D1b: its only two callers
    are this module and the balance seam, so a kind CLASSIFIER was holding a
    chronology primitive that the chronology module then had to import back.  A
    cohesion correction, decided on where the rule BELONGS -- not on the
    private-import smell of Section 8, which this ordinary public import was not.

    Args:
        periods: The owner's pay periods.  Order-independent -- the scan keys on
            ``period_index``, not on list position.
        target: The date to locate.

    Returns:
        The matching :class:`~app.models.pay_period.PayPeriod`, or ``None`` when
        no period contains or precedes *target*.
    """
    containing = None
    fallback = None
    for period in periods:
        if period.start_date <= target <= period.end_date:
            if containing is None or period.period_index > containing.period_index:
                containing = period
        elif period.end_date < target:
            if fallback is None or period.period_index > fallback.period_index:
                fallback = period
    return containing if containing is not None else fallback


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
    containing = find_period_containing_date(periods, target_date)
    return containing if containing is not None else periods[0]


def anchor_visible_on(anchor_date: date) -> date:
    """Return the date an anchor's balance correction becomes visible to a read.

    The anchor's OWN civil date (step C2): an assertion happens on the date it
    asserts, which is the ``entry_date`` its correction is posted at
    (:func:`app.services._posting_reconcile.emit_anchor_correction_entry`).  It no
    longer needs the owner's calendar -- the pre-C2 rule
    ``LEAST(anchor_date, containing period.start)`` did, only to reach the pay
    period the NOT NULL ``pay_period_id`` forced the anchor under, and that
    ``LEAST`` is exactly what made a future-dated anchor visible early (N-10).

    Kept as a named one-liner rather than inlined so the fold reads with the same
    vocabulary as :func:`payment_visible_on` (the two halves of the one clock).

    Args:
        anchor_date: The date the anchor asserts its balance on.

    Returns:
        The date from which a balance read counts this anchor -- ``anchor_date``.
    """
    return anchor_date


def payment_visible_on(shadow: Transaction) -> date:
    """Return the date a settled payment's principal becomes visible to a read.

    Its **settled date** (step C2, ruling R-A): the shadow's STORED
    ``settled_on``, read through the shared
    :func:`app.utils.balance_predicates.settled_day`.  That is the same accessor
    the posting writer stamps the payment's ``entry_date`` through
    (:func:`app.services.posting_service._transaction_entry_date`), so the day
    the fold counts this payment and the day the sum-of-postings reader counts
    it cannot drift; and it is the day the checking outflow moves, so the loan
    and checking move together.

    **It DERIVED that day from ``paid_at`` until plan step X-f1** (ruling R-EC)
    -- a display-timezone conversion of the click instant with the pay period's
    ``start_date`` as a NULL fallback.  The column stores the day now, so this
    reads a fact; a settled shadow carrying none is refused rather than dated by
    a fallback.

    **The split MATH is untouched by the zone, and that is what bounds this
    rule's blast radius to one day of VISIBILITY.**  The interest / principal /
    escrow split, the governing rate version, and the anchor-versus-payment
    ordering all key on the DUE date
    (:func:`app.services.loan_ledger.merge_anchor_and_payment_events`,
    :mod:`app.services.loan_ledger._split`) -- never on ``paid_at`` -- so a late or
    out-of-order settlement, and equally a re-zoned one, changes only WHEN the
    paid-down principal is shown, never HOW the payment splits, at what rate, or
    against which anchor.

    Args:
        shadow: The settled loan-side income shadow (its ``pay_period`` must be
            loaded; :func:`~app.services.loan_loaders.query_shadow_income`
            eager-loads it).

    Returns:
        The date from which a balance read counts this payment's principal.
    """
    return settled_day(shadow.id, shadow.settled_on)
