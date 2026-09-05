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

**The CALENDAR left this module at plan step C2-d, and with it the package's
last query.**  Three names lived here -- ``owner_pay_periods`` (the owner's
period list), ``find_period_containing_date`` (which period a date falls in)
and ``resolve_anchor_pay_period`` (the three-branch chain the anchor-posting
writers filed against, built on the other two).  Ruling **D5**'s one clock had
already taken the FOLD off them -- an anchor counts from its own date and needs
no calendar -- so the two posting writers were the only consumers left, and a
chronology rule neither of them shares with anything in this package was being
reached by an import from the CASH posting package into the LOAN package
(finding **N-169**).  The 2026-08-10 pay-calendar ruling replaced the chain
with one clamp on
:meth:`app.services.pay_calendar.PayCalendar.filing_period`, both writers now
take it through :func:`app.services._posting_reconcile.filing_calendar_for`,
and all three names are deleted.

**Chronology only, and now PURE.**  The two names left here each return a
``date``; neither yields a balance-at-T, which is why both are ruled
non-producers of the balance fence.  No clock, no writes and -- since C2-d
removed the one calendar query -- no database session anywhere in this package.
That also retires a caveat this paragraph used to carry: a returned
``PayPeriod`` was an ORM row, so money was reachable from it by relationship,
and the fence ruling had to say that a period is not an account's balance
rather than that a figure was unreachable.  Nothing here returns an ORM row any
more, so the stronger claim is now true by construction -- which is the shape to
prefer, because claiming it before it was true is how
``LoanState.current_balance`` shipped.
"""

from datetime import date

from app.models.transaction import Transaction
from app.utils.balance_predicates import settled_day


def anchor_visible_on(anchor_date: date) -> date:
    """Return the date an anchor's balance correction becomes visible to a read.

    The anchor's OWN civil date (step C2): an assertion happens on the date it
    asserts, which is the ``entry_date`` its correction is posted at
    (:func:`app.services._posting_reconcile.emit_anchor_correction_entry`).  It no
    longer needs the owner's calendar -- the pre-C2 rule
    ``LEAST(anchor_date, containing period.start)`` did, only to reach the pay
    period the anchor had to be FILED under -- a requirement of the per-period
    readers, which ``pay_period_id``'s ``NOT NULL`` expresses rather than
    creates (ruling **pay_calendar:R-PC53**) -- and that
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
    (:func:`app.services.loan_ledger.loan_event_stream`,
    :mod:`app.services.loan_ledger._replay`) -- never on ``paid_at`` -- so a late or
    out-of-order settlement, and equally a re-zoned one, changes only WHEN the
    paid-down principal is shown, never HOW the payment splits, at what rate, or
    against which anchor.

    Args:
        shadow: The settled loan-side income shadow.  Only ``id`` and
            ``settled_on`` are read.  *This said its ``pay_period`` "must be
            loaded" until plan step C2-d read the body against the sentence* --
            true while the NULL fallback dated a settle by its period's start,
            and false since ruling R-EC made the day a stored column.  A
            precondition a caller can satisfy needlessly is still a false
            precondition.

    Returns:
        The date from which a balance read counts this payment's principal.
    """
    return settled_day(shadow.id, shadow.settled_on)
