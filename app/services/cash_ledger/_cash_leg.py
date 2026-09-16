"""What one row MOVES through its cash account, as against what it is worth.

The confirmed-cash-effect family, extracted from :mod:`._amounts` at plan step
``bank_import:X-f6a-2`` when a second consumer arrived and pushed that module
past its 1,000-line ceiling.  **The cut is a subject rather than a size**: its
neighbour answers *what is this row worth* -- a valuation composing an amount
with an entered actual, an excluded status, a soft delete and an envelope's
purchases -- and these five answer the narrower question *how much of it
actually crosses this bank account*, which is a different figure whenever a row
carries entries.

One rule, in one expression:

    ``gross - Sigma(card entries) - Sigma(already-posted purchases)``,
    signed ``+`` for income and ``-`` for an expense.

The sign follows the transaction TYPE, never the account class, so the leg is
correct whether the cash account is an asset (Checking) or a liability (a direct
charge on a Credit Card account).

**A MOVEMENT's leg is signed by the same rule, and since plan step
``balance:X-bi-3b`` it is stated here ONCE** (:func:`movement_cash_leg`).  A
purchase recorded against an envelope, and the covering movement a settle
writes for a bill or a paycheck, moves its whole figure through the parent's
account in the parent's direction -- ruling **R-BAL35**: a movement's
category, type and scenario are its plan row's, read through
``transaction_id`` and never copied, so its direction is read the same way.
Six readers spelled *a purchase is money leaving* for themselves before this
step (the walk's fact producer, the ledger writer's target, the seam's family
valuation, the statement matcher's offer, its accepted register and its undo
dialog), every one of them correct for a purchase and every one of them
``-figure`` for a covered paycheck, which is wrong by twice the figure.

**The two subtracted terms are why the family exists at all.**  A card purchase
leaves later through its own CC Payback sibling, and a purchase carrying a
recorded bank posting day is already a cash movement of its own on its own day
(ruling **R-FM**, plan step ``balance:X-f3b``) -- so an envelope's close books
only the remainder, or the same dollars leave the account twice.  That was
measured: entry 89 (`$12.79`, taken by the bank on 2026-08-12) was being taken a
second time by its envelope's 08-13 close, reading the whole of that day
`$12.79` low (finding **N-274**).

**Where ``gross`` comes from is the CALLER'S, and it must be.**  A settled row
RECORDED its figure; a projected one is worth what settling it would book, which is
``transaction_service.settle_amount`` for an ordinary row and
``transfer_service.settle_amount`` for a shadow leg.  Asking this module to
choose between them would put the settle verbs' own partition in a third place,
and it cannot import either without a cycle.

Services-boundary discipline: pure per-row reads.  No query, no clock, no Flask
import, no write.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.transaction import Transaction
from app.services.row_valuation import settled_contribution
from app.utils.balance_predicates import is_balance_contributing


def credit_entry_sum(txn: Transaction) -> Decimal:
    """Return the sum of a transaction's credit (credit-card) entry amounts.

    The ``Sigma(credit entry amounts)`` term of the confirmed-cash-effect
    formula: an envelope's credit purchases are excluded from the checking
    outflow because each posts its own CC Payback when that payback settles
    (``credit_workflow``), so counting them here would double-count against the
    payback.  A plain transaction has no entries, so this is ``Decimal("0")``
    and the effect collapses to ``effective_amount``.

    **PUBLIC since plan step X-f2-c3, for the reconcile panel** (finding
    **N-226**).  That panel offers an envelope at what a tick would BOOK, which
    is ``sum(entries)`` over every entry INCLUDING the card ones -- against a
    statement that shows only the debit half.  The panel therefore prints the
    cash figure beside the booked one, and it takes this term rather than
    writing ``entry.is_credit`` a second time: the two would then be one rule
    in two places, on the screen a user reads beside a paper statement.

    Args:
        txn: The transaction whose credit entries to sum.

    Returns:
        The sum of ``amount`` over the transaction's ``is_credit`` entries, as a
        ``Decimal`` (``Decimal("0")`` when there are none).
    """
    return sum(
        (entry.amount for entry in txn.entries if entry.is_credit),
        Decimal("0"),
    )


def posted_purchase_sum(txn: Transaction) -> Decimal:
    """Return the sum of a transaction's purchases that have ALREADY posted.

    The ``Sigma(posted debit purchases)`` term ruling **R-FM** adds to the
    confirmed cash effect (plan step X-f3b).  A purchase carrying a recorded
    bank posting day books its OWN cash leg on its OWN day
    (``posting_service.sync_purchase_postings``), so its envelope's close must
    book only the remainder or the same dollars leave the account twice.

    A DEBIT purchase only: a card purchase never touches checking at all, and
    :func:`credit_entry_sum` is the term that removes it.  The two are disjoint
    by construction (``is_credit`` partitions the entries), so subtracting both
    subtracts nothing twice.  A plain transaction has no entries, so this is
    ``Decimal("0")`` and the effect collapses to ``effective_amount``.

    **PUBLIC for the same reason** :func:`credit_entry_sum` **is**: the reconcile
    panel prints what the STATEMENT will show for a tick beside what the tick
    BOOKS, and those differ by exactly these two terms.  It takes this one rather
    than writing ``entry.settled_on is not None`` a second time, so a change to
    what "already posted" means cannot leave the panel saying the old thing.

    Args:
        txn: The transaction whose posted purchases to sum.

    Returns:
        The sum of ``amount`` over the transaction's debit entries carrying a
        ``settled_on``, as a ``Decimal`` (``Decimal("0")`` when there are none).
    """
    return sum(
        (
            entry.amount for entry in txn.entries
            if not entry.is_credit and entry.settled_on is not None
        ),
        Decimal("0"),
    )


def settled_cash_leg(txn: Transaction) -> Decimal:
    """Return the confirmed cash effect of a SETTLED row: what really moved.

    The settled counterpart of the projected valuations beside it, and the ONE
    statement of that rule: ``effective_amount - Sigma(credit entry amounts) -
    Sigma(posted debit purchases)``, signed ``+`` for income (money entering the
    account) and ``-`` for an expense (money leaving).  The sign follows the
    transaction TYPE, never the account class, so the leg is correct whether the
    cash account is an asset (Checking) or a liability (a direct charge on a
    Credit Card account).

    For a plain transaction both entry sums are zero and the effect collapses to
    ``+/-effective_amount``.  For an ENVELOPE at settle the first term equals the
    sum of ALL its entries -- since plan step **X-au-c3** because that is what
    the row's ``purchases``-basis settlement RECORDS
    (:func:`app.services.row_valuation.settled_figure`), where it used to be
    because a deleted hook wrote the sum into ``actual_amount`` -- and
    subtracting the two collapses the result to the UNPOSTED debit outflow, with
    no branch on "is this an envelope".

    **The third term is ruling R-FM** (plan step X-f3b), and it is what makes
    "an envelope's close books only what its purchases did not" one expression
    rather than a second rule.  A purchase whose bank posting day is recorded is
    a cash movement of its own, dated on its own day
    (:func:`~._events.settled_cash_facts`, ``posting_service``'s purchase
    sync); the close therefore books the rest.  The two always sum to the row's
    whole debit total, so nothing is lost and nothing is counted twice --
    measured on a production clone 2026-08-14: entry 89 (``$12.79``, taken by
    the bank on 08-12, inside the ``$2,193.69`` the owner asserted for that day)
    was being taken a SECOND time by its envelope's 08-13 close, which read the
    whole of 08-13 ``$12.79`` low (finding **N-274**).

    **This is why the rule lives HERE (plan step X-a), not in the posting
    writer.**  It was ``posting_service._signed_cash_leg``, private to the
    module that WRITES the ledger -- the same inversion plan step B0 corrected on
    the loan side, where the payment split lived inside the posting package and
    every other consumer had to reach through its privates for it.  Two
    consumers need this rule now: the writer, which posts the effect, and the
    cash WALK (:func:`app.services.cash_ledger.walk_cash_ledger`), whose facts fold
    it.  A second copy would let the projection and the posted ledger disagree
    about what a settled row was worth -- measured on production 2026-07-25
    before this move, a ``effective_amount``-only walk diverged from the posted
    ledger on 10 of 130 Checking rows and by up to ``$181.58`` on one, because
    every one of them was an envelope carrying credit-card entries.

    The bulk oracle reader ``posting_reads.settled_transaction_effect`` computes
    the same sum in SQL and deliberately stays independent: it is the Step-3
    reconciliation oracle's own window onto the ledger, and an oracle that
    shared this implementation could not grade it.
    **That independence narrowed at plan step X-au-c3** (adversarial review,
    2026-08-17): this rule is the transaction writer's in Python and the
    oracle's is in SQL, so those two still grade each other, but the transfer
    writer (``posting_service._settle_effective``) spelled its figure inline and
    now shares ``posting_reads.settled_figure_clause`` with its own oracle.
    What was lost is a transcription check between two copies of one expression;
    what was gained is one statement of a money rule.

    **TOTAL: a non-contributing row is worth exactly zero.**  A soft-deleted or
    Credit / Cancelled row has an ``effective_amount`` of zero, but its ENTRIES
    survive on the row -- so without the guard below,
    ``0 - Sigma(credit) - Sigma(posted)`` negated for an expense returns a
    FABRICATED INFLOW: a deleted grocery envelope carrying an $80.00 credit
    purchase valued at ``+$80.00``, money the account never received.
    Unreachable through today's
    two callers (the walk pre-filters with
    :func:`~app.utils.balance_predicates.balance_contributing_clause`, and the
    writer resolves a target only on the settle side), which is exactly why it
    would have waited to be discovered by a third.  A function whose answer is
    correct only because every caller happens to pre-filter is a contract nobody
    can see; this gate is stated here instead.  **The same gate governs the row's
    PURCHASES** (ruling R-FM): a non-contributing row's purchases post nothing
    either, in the walk (:func:`~._events.settled_cash_facts`) and in the ledger
    (``posting_service``), so the zero here is the whole family's zero rather
    than the parent leg's alone.

    **IT IS NOT TOTAL OVER STATUS, AND SINCE PLAN STEP X-bx IT SAYS SO.**  The
    paragraph above used to call this function total, and on the contributing
    gate it is; on the SETTLED one it never was.  A row that contributes and has
    not settled has no confirmed cash effect to report, and this used to answer
    one anyway -- :func:`~app.services.row_valuation.settled_contribution` fell
    through to the row's plan column, so a Projected bill was valued as a
    movement that had not happened (finding **BAL-465**).  It refuses now.  The
    guard below reads ``is_balance_contributing``, which does NOT test status,
    so what makes this correct is the refusal one call down rather than a
    pre-filter each caller remembers.  **Three of the six callers restrict the
    row set**: the walk (:func:`~._events.settled_cash_facts`) loads settled
    statuses in SQL; ``posting_service._settled_target`` is reached only when
    ``sync_transaction_postings`` was passed ``settled=True``, and all FOURTEEN
    of its call sites derive that flag from the row rather than assert it
    (thirteen as ``txn.status.is_settled``, one as ``txn.status_id in
    settled_ids``); and ``statement_match._candidates._price`` branches on
    ``txn.status.is_settled``.  **The other three CATCH the refusal instead**
    -- ``_accepted_view._accepted_row``, ``_release._subject_removal`` and
    ``._container_removal`` -- because they render the review page, where a
    raise would strand the account (finding **N-302**).  Catching is not
    pre-filtering: on those three the refusal changes an ANSWER, and each is
    graded by a case added at plan step X-bx.

    Args:
        txn: The transaction whose confirmed cash effect to value.  A
            non-contributing row (soft-deleted, Credit, or Cancelled) returns
            ``0.00`` whatever entries it carries; a CONTRIBUTING row must have
            settled, or this refuses.

    Returns:
        The signed confirmed cash effect as a ``Decimal``.

    Raises:
        AmountUnresolvable: From
            :func:`~app.services.row_valuation.settled_contribution`, when the
            row contributes and has not settled -- so it recorded nothing and
            there is no confirmed effect to report -- and when a settled row's
            record is incomplete.
    """
    if not is_balance_contributing(txn):
        return Decimal("0.00")
    return cash_leg_of(txn, settled_contribution(txn))


def off_statement_sum(txn) -> Decimal:
    """Return what *txn* BOOKS but does not move through its cash account.

    The two terms a row can carry that never reach this account's statement,
    stated once because three readers ask for them:

    * a CARD purchase, which leaves later through its own CC Payback sibling;
    * a purchase whose bank posting day is already recorded, whose cash left on
      its own day and is a movement of its own in the ledger (ruling **R-FM**,
      plan step ``balance:X-f3b``).

    Args:
        txn: The row, with ``entries`` loaded.

    Returns:
        Their sum, ``0.00`` for the ordinary row that carries neither.
    """
    return credit_entry_sum(txn) + posted_purchase_sum(txn)


def cash_leg_of(txn, gross: Decimal) -> Decimal:
    """Return the signed cash *txn* moves through its account booking *gross*.

    :func:`settled_cash_leg` with its first term supplied, so the rule --
    *gross, less what never reaches this account, signed by the transaction
    TYPE* -- is stated once for a settled row and a projected one alike.

    **The two differ only in where ``gross`` comes from, and they must.**  A
    settled row RECORDED its figure
    (:func:`~app.services.row_valuation.settled_contribution`, which REFUSES a
    row that has not settled rather than pricing its PLAN into a money
    path); a projected row's is what settling it would book, which is
    ``transaction_service.settle_amount`` for an ordinary row and
    ``transfer_service.settle_amount`` for a shadow leg.  Asking this function
    to choose between them would put the settle verbs' partition in a third
    place.

    Its second caller is the statement matcher (plan step
    ``bank_import:X-f6a-2``), which must compare a bank line against what the
    app would move if the row it names settled -- Projected or not.

    Args:
        txn: The row being valued, with ``entries`` loaded.
        gross: What the row books, before the off-statement terms.

    Returns:
        The signed cash effect: ``+`` for income (money entering the account),
        ``-`` for an expense.  ``0.00`` for a non-contributing row whatever
        *gross* says and whatever entries it carries -- the same TOTAL guard
        :func:`settled_cash_leg` documents, restated here because this is now
        the function that applies it.
    """
    if not is_balance_contributing(txn):
        return Decimal("0.00")
    return _signed_by_type(txn, gross - off_statement_sum(txn))


def movement_cash_leg(txn: Transaction, entry) -> Decimal:
    """Return the signed cash ONE movement moves through *txn*'s account.

    The movement's twin of :func:`settled_cash_leg` (plan step
    ``balance:X-bi-3b``, ruling **R-BAL35**): a purchase against an envelope
    or the covering movement a settle wrote moves its whole stored figure in
    its PARENT's direction -- ``+`` under an income row, ``-`` under an
    expense -- through the ONE sign rule the parent's own leg reads.  A
    purchase has no type of its own, exactly as it has no category of its
    own: both are the plan row's.

    **TOTAL over the two facts that make a movement move nothing here**, so
    a caller that forgets to pre-filter still reads the right figure:

    * a CARD purchase leaves through its own CC Payback sibling and never
      touches this account (:func:`credit_entry_sum` is the term that keeps
      it out of the parent's leg for the same reason);
    * a movement under a NON-CONTRIBUTING parent -- soft-deleted, Credit or
      Cancelled -- moves nothing, the family's zero :func:`settled_cash_leg`
      states for the parent (ruling **R-FM**).

    It does NOT read ``settled_on``: whether a movement has POSTED is a
    question about the event stream and the ledger (``_events.
    _posted_purchase_facts``, ``_posting_purchases.purchase_posts``), while
    the statement matcher prices an UNPOSTED purchase at what the bank would
    show for it.  The one figure both want is this.

    Args:
        txn: The movement's parent row, contributing or not.
        entry: One of its ``budget.transaction_entries`` rows.

    Returns:
        The signed ``Decimal``: positive INTO the account, ``0.00`` for a card
        purchase or a movement under a non-contributing parent.  A stored
        REFUND (a negative purchase, ruling **bank_import:R-II**) passes
        through as money coming back, because this is arithmetic and not a
        case analysis.
    """
    if entry.is_credit or not is_balance_contributing(txn):
        return Decimal("0.00")
    return _signed_by_type(txn, entry.amount)


def movement_figure_for(txn: Transaction, cash: Decimal) -> Decimal:
    """Return the figure a movement under *txn* must STORE to move *cash*.

    The inverse of :func:`movement_cash_leg`'s signed arm, for the two doors
    that write a movement's figure FROM a bank line's cash rather than read
    the cash from a figure: the statement matcher minting a purchase from a
    line (``_create._born_purchase``) and re-pricing one to a line
    (``_landing.corrected_figure``).  Both spelled ``-cash`` for themselves
    until plan step ``balance:X-bi-3b`` -- the expense arm of the rule, and
    a second spelling of it even while every purchase they could reach sat
    under an expense row.  The sign rule is an involution, so this IS
    :func:`_signed_by_type` and the inverse costs no second expression.

    Args:
        txn: The parent the movement records money for.
        cash: The signed cash the bank states, positive INTO the account.

    Returns:
        The stored figure: ``-cash`` under an expense (an outflow of
        ``-28.29`` is a purchase of ``28.29``, an inflow a REFUND of
        ``-28.29`` by the same expression, ruling **bank_import:R-II**),
        ``+cash`` under an income row.
    """
    return _signed_by_type(txn, cash)


def _signed_by_type(txn: Transaction, magnitude: Decimal) -> Decimal:
    """Return *magnitude* signed by *txn*'s TYPE: ``+`` income, ``-`` expense.

    The one expression of the direction rule, private so its three readers
    -- a row's leg (:func:`cash_leg_of`), a movement's
    (:func:`movement_cash_leg`) and the movement's figure from its cash
    (:func:`movement_figure_for`, the same involution read the other way)
    -- are the whole surface.
    """
    return magnitude if txn.is_income else -magnitude
