"""The loan fold's EVENT STREAM: what happened to a loan, in the order it happened.

A loan's balance is a fold over its event stream, and this module builds that
stream.  Two kinds of fact enter it, and nothing else:

* an **ASSERTION** -- the loan's opening (its origination, ALWAYS) plus every
  balance assertion made after it: a mid-life ``tracking_start`` and every user
  balance true-up, all loaded as
  :class:`~app.services.loan_loaders.LoanAnchorFact` and all RESETTING the running
  balance at their own date (a ``tracking_start`` is never the opening -- step C1);
* a **PAYMENT** -- a settled loan-side income shadow, the record that cash
  actually moved (:func:`~app.services.loan_loaders.settled_income_shadows`).

**Every fact enters the stream, whatever its date, and nothing here reads the
clock.**  A loan's anchors are FACTS -- the origination is a verbatim copy of the
immutable :class:`~app.models.loan_params.LoanParams`, a true-up is the
operator's dated assertion -- and this module RECORDS them; deciding which have
HAPPENED as of a given date is a READER's job.  Dropping a future-dated anchor --
what the walk did while it took an ``as_of`` -- made the persisted ledger a
function of the wall clock at the moment the sync happened to run, which is a
corruption generator, not a cache (step A3, ``4e46a0a8``).

The one exception is :func:`confirmed_shadows_through`, which IS a reader's
bound and lives here only because it is the same settled-payment set narrowed:
see its docstring.
"""

from datetime import date

from app.models.transaction import Transaction
from app.services import loan_loaders
from app.services.loan_loaders import LoanAnchorFact

from ._visible import payment_visible_on


def confirmed_shadows_through(
    loan_account_id: int,
    scenario_id: int,
    as_of: date,
) -> list[Transaction]:
    """Return the settled shadows whose CASH had moved by ``as_of``.

    The DISPLAY subset of
    :func:`~app.services.loan_loaders.settled_income_shadows`: the payments the
    balance readers count as confirmed history at ``as_of`` (their shared
    visible-on bound).  The posted ledger's payment-history table
    (:func:`app.services.loan_posting_service.confirmed_loan_payment_history`)
    consumes this so its rows match the balance readers' cut; the fold's own walk
    deliberately does NOT (it splits every settled payment -- see
    :func:`~app.services.loan_loaders.settled_income_shadows` for why).

    A payment's visible-on date is its SETTLED date (step C2, ruling R-A), read
    through the SAME :func:`._visible.payment_visible_on` the fold uses, so the
    history rows and the fold cannot key a payment on two different days.  The SQL
    reader that must agree with this (:func:`app.services.loan_posting_service`)
    bounds the same postings by their ``entry_date``, which the writer stamps with
    that identical settled date.

    Args:
        loan_account_id: The loan account whose shadows to load.
        scenario_id: The budget scenario to scope to.
        as_of: The display boundary; a payment whose settled date has not arrived
            by it is a forward projection, excluded.

    Returns:
        The settled income shadows through ``as_of``, ascending by pay-period
        start then ``id``.
    """
    return [
        shadow
        for shadow in loan_loaders.settled_income_shadows(
            loan_account_id, scenario_id,
        )
        if payment_visible_on(shadow) <= as_of
    ]


def merge_anchor_and_payment_events(
    anchor_facts: list[LoanAnchorFact],
    shadows: list[Transaction],
    payment_day: int,
) -> list[tuple[date, bool, object]]:
    """Merge a loan's anchors and payments into one chronological event stream.

    Returns ``(governing_date, is_anchor, item)`` tuples in the order the
    running-balance walk must process them so each anchor's RESET lands at the
    right point relative to the payments.  The ordering key is each item's
    governing date -- an anchor's ``anchor_date``, a payment's
    :func:`app.services.loan_loaders.loan_payment_due_date` -- with a PAYMENT
    sorted BEFORE an anchor on a tie, so a payment due exactly on an anchor's date
    is subsumed by (walked before, then overwritten by) that anchor's reset.  The
    governing date is RETURNED beside each item (not just used to sort and then
    discarded) so the walk threads a payment's due date onto its split without
    re-deriving it (plan step E1c); it costs the caller nothing to ignore.  That
    is the SAME strict ``anchor_date < due_date`` post-anchor boundary the
    resolver's replay uses (:func:`is_confirmed_payment_eligible`, fed the same
    derivation via :attr:`PaymentRecord.due_date`), applied at EVERY anchor rather
    than the latest only -- the two MUST stay on one derivation, or the posted
    ledger and the replayed balance drift on which payments a given anchor
    subsumes.

    **This is CONTRACT time, not cash time.**  A payment is ordered by the
    installment it satisfies (its DUE date), never by when its cash settled, so a
    late or out-of-order settlement can never reorder installments or re-split one
    (ruling R-A).

    **BOTH inputs arrive PRE-ORDERED by their own loader, and this function adds
    no TIE-BREAK WITHIN A TYPE** (plan step X-an-b, closing finding N-196; the
    same shape finding N-133 / R1 ruled on the cash side).  It still owns the two
    ordering rules above -- the contract-time re-key of payments onto their DUE
    dates, and the tag that sorts a payment before an anchor sharing a date --
    and those are its own.  What it no longer owns is which of two anchors comes
    first.  A stable sort of the payments-then-anchors concatenation on
    ``(governing_date, tag)`` preserves each loader's key within a shared date --
    anchors keep
    :func:`~app.services.loan_loaders.load_loan_anchor_facts`' ``(anchor_date,
    created_at, event_id)``, payments keep
    :func:`~app.services.loan_loaders.settled_income_shadows`' ``(pay_period.start_date,
    id)``.  This re-sorted the anchors on ``(anchor_date, created_at)`` until
    X-an-b, which was a SECOND statement of a rule the loader is now the one home
    of, and an incomplete one: ``created_at`` is evaluated at TRANSACTION START,
    so two anchors written together shared an instant, the re-sort left them in
    whatever order PostgreSQL returned, and the walk reset on the LAST of the tie
    while the resolver's ``max()`` seeded from the FIRST.

    Args:
        anchor_facts: The loan's :class:`~app.services.loan_loaders.LoanAnchorFact`
            list, PRE-ORDERED by ``(anchor_date, created_at, event_id)``
            (:func:`~app.services.loan_loaders.load_loan_anchor_facts`, which is
            where that order is decided).
        shadows: The settled income shadows, PRE-SORTED by
            ``(pay_period.start_date, id)``
            (:func:`~app.services.loan_loaders.settled_income_shadows`).
        payment_day: The loan's contractual due day (drives each payment's due date).

    Returns:
        ``(governing_date, is_anchor, item)`` tuples in walk order -- the
        ``governing_date`` is the anchor's ``anchor_date`` or the payment's due
        date the tuple sorted on, and ``item`` is a
        :class:`~app.services.loan_loaders.LoanAnchorFact` when ``is_anchor``,
        else a settled income :class:`~app.models.transaction.Transaction`.
    """
    # Payment tag 0 sorts before anchor tag 1 on an equal date, so a payment due
    # on an anchor's date is walked (and then overwritten) before the reset.  A
    # stable sort of [payments..., anchors...] keeps each type's pre-sorted order
    # for equal keys.
    events = [
        (loan_loaders.loan_payment_due_date(shadow, payment_day), 0, shadow)
        for shadow in shadows
    ] + [
        (anchor.anchor_date, 1, anchor) for anchor in anchor_facts
    ]
    events.sort(key=lambda event: (event[0], event[1]))
    return [(event_date, tag == 1, item) for event_date, tag, item in events]
