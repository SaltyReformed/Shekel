"""The loan fold's EVENT STREAM: what happened to a loan, in the order it happened.

A loan's balance is a fold over its event stream, and this module builds that
stream.  THREE kinds of fact enter it, and nothing else:

* an **ASSERTION** -- the loan's opening (its origination, ALWAYS) plus every
  balance assertion made after it: a mid-life ``tracking_start`` and every user
  balance true-up, all loaded as
  :class:`~app.services.loan_loaders.LoanAnchorFact` and all RESETTING the running
  balance at their own date (a ``tracking_start`` is never the opening -- step C1);
* a **PAYMENT** -- a settled loan-side income shadow, the record that cash
  actually moved (:func:`~app.services.loan_loaders.settled_income_shadows`);
* a **CHARGE** -- what an accrual period cost the loan
  (:func:`.._charges.charges_for_due_dates`), one per period the payments occupy.
  It joined the stream at plan step **X-au-g-2c-3b-2**, and it is the fact that
  stops the payment COUNT being the clock: while a month's interest and escrow
  rode on the payment RECORD, N payments inside one month charged N months.

**Every fact enters the stream, whatever its date, and nothing here reads the
clock.**  A loan's anchors are FACTS -- the origination is a verbatim copy of the
immutable :class:`~app.models.loan_params.LoanParams`, a true-up is the
operator's dated assertion -- and this module RECORDS them; deciding which have
HAPPENED as of a given date is a READER's job.  Dropping a future-dated anchor --
what the walk did while it took an ``as_of`` -- made the persisted ledger a
function of the wall clock at the moment the sync happened to run, which is a
corruption generator, not a cache (step A3, ``4e46a0a8``).

**What this module does NOT decide is the ORDER**, and that is plan step
X-au-g-2c-3b-2's division.  ``merge_anchor_and_payment_events`` lived here and
sorted the stream itself; the forward fold sorted its own, and the two were one
rule stated twice.  The order between KINDS is now
:func:`.._replay.replay_loan_events`'s alone, and the order WITHIN a kind is each
loader's, preserved by a stable sort.  What survives here is the mapping: which
rows are facts, which date governs each, and what figure each carries.

The one exception is :func:`confirmed_shadows_through`, which IS a reader's
bound and lives here only because it is the same settled-payment set narrowed:
see its docstring.
"""

from datetime import date

from app.models.transaction import Transaction
from app.services import loan_loaders
from app.services.loan_loaders import LoanAnchorFact
from app.services.row_valuation import owned_contribution

from ._charges import charges_for_due_dates
from ._replay import LoanCashEvent, LoanEventStream, LoanResetEvent
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


def loan_event_stream(
    anchor_facts: list[LoanAnchorFact],
    shadows: list[Transaction],
    payment_day: int,
    periods: list,
    escrow_lines: list,
) -> LoanEventStream:
    """Map a loan's anchors, settled payments and charges into ONE event stream.

    The settled walk's half of the replay: it says WHICH rows are facts, WHICH
    date governs each, and WHAT figure each carries.  The replay
    (:func:`.._replay.replay_loan_events`) then decides the order between kinds
    and folds them.

    **This is CONTRACT time, not cash time.**  A payment is dated by the
    installment it satisfies (its DUE date,
    :func:`app.services.loan_loaders.loan_payment_due_date`), never by when its
    cash settled, so a late or out-of-order settlement can never reorder
    installments or re-split one (ruling R-A).  That derivation is threaded onto
    the event rather than recomputed downstream (plan step E1c), and it is the
    SAME strict ``anchor_date < due_date`` post-anchor boundary the resolver's
    replay uses (:func:`is_confirmed_payment_eligible`, fed the same derivation
    via :attr:`PaymentRecord.due_date`) -- the two MUST stay on one derivation,
    or the posted ledger and the replayed balance drift on which payments a given
    anchor subsumes.

    **The CHARGES are derived from the installments the payments SATISFY**, one
    per accrual period they occupy (:func:`.._charges.charges_for_due_dates`) --
    which is the whole of plan step R16-a: the count of charges cannot depend on
    the count of payments.  A period holding two payments is charged once, and
    the second payment clears nothing fresh and pays pure principal.

    **EVERY input arrives PRE-ORDERED by its own loader, and nothing here or in
    the replay adds a TIE-BREAK WITHIN A KIND** (plan step X-an-b, closing finding
    N-196; the same shape finding N-133 / R1 ruled on the cash side).  A stable
    sort in the replay preserves each loader's key within a shared date -- anchors
    keep :func:`~app.services.loan_loaders.load_loan_anchor_facts`'
    ``(anchor_date, created_at, event_id)``, payments keep
    :func:`~app.services.loan_loaders.settled_income_shadows`'
    ``(pay_period.start_date, id)``.  The stream was re-sorted on ``(anchor_date,
    created_at)`` until X-an-b, which was a SECOND statement of a rule the loader
    is now the one home of, and an incomplete one: ``created_at`` is evaluated at
    TRANSACTION START, so two anchors written together shared an instant, the
    re-sort left them in whatever order PostgreSQL returned, and the walk reset on
    the LAST of the tie while the resolver's ``max()`` seeded from the FIRST.

    Args:
        anchor_facts: The loan's :class:`~app.services.loan_loaders.LoanAnchorFact`
            list, PRE-ORDERED by ``(anchor_date, created_at, event_id)``
            (:func:`~app.services.loan_loaders.load_loan_anchor_facts`, which is
            where that order is decided).
        shadows: The settled income shadows, PRE-SORTED by
            ``(pay_period.start_date, id)``
            (:func:`~app.services.loan_loaders.settled_income_shadows`).  Each
            row's cash is read through
            :func:`~app.services.row_valuation.owned_contribution` -- the accessor
            whose NAME asserts the row owns its figure -- rather than a resolver,
            because every row here has SETTLED, so it answers from the settlement
            it RECORDED (plan step X-au-c3) and never reaches the plan; a row that
            recorded nothing REFUSES rather than falling back to a forecast.
        payment_day: The loan's contractual due day (the fallback coordinate for a
            shadow carrying no stored ``due_date``).
        periods: The loan's rate periods
            (:func:`app.services.loan_resolver.resolve_periods`); each charge
            carries the one governing its own date.
        escrow_lines: The loan's escrow lines with their full version history
            (:func:`app.services.loan_loaders.load_escrow_lines`); each charge
            carries the escrow in force on its own date.

    Returns:
        The loan's :class:`~._replay.LoanEventStream`.
    """
    payments = [
        LoanCashEvent(
            on_date=loan_loaders.loan_payment_due_date(shadow, payment_day),
            cash=owned_contribution(shadow),
            source=shadow,
        )
        for shadow in shadows
    ]
    return LoanEventStream(
        charges=charges_for_due_dates(
            [payment.on_date for payment in payments], periods, escrow_lines,
        ),
        payments=payments,
        resets=[
            LoanResetEvent(
                on_date=anchor.anchor_date,
                balance=anchor.anchor_balance,
                source=anchor,
            )
            for anchor in anchor_facts
        ],
    )
