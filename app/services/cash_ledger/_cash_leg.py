"""What one row MOVES through its cash account, as against what it is worth.

The confirmed-cash-effect family, extracted from :mod:`._amounts` at plan step
``bank_import:X-f6a-2`` when a second consumer arrived and pushed that module
past its 1,000-line ceiling.  **The cut is a subject rather than a size**: its
neighbour answers *what is this row worth* -- a valuation composing an amount
with an entered actual, an excluded status, a soft delete and an envelope's
purchases -- and these answer the narrower question *how much of it actually
crosses this bank account*.

**A MOVEMENT's leg is the one the balance and the ledger read, and since plan
step ``balance:X-bi-3b`` it is stated here ONCE** (:func:`movement_cash_leg`).
A purchase recorded against an envelope, and the covering movement a settle
writes for a bill, a paycheck or a transfer leg, moves its whole figure
through its account in the parent's direction -- ruling **R-BAL35**: a
movement's category, type and scenario are its plan row's, read through
``transaction_id`` and never copied, so its direction is read the same way.
Six readers spelled *a purchase is money leaving* for themselves before that
step (the walk's fact producer, the ledger writer's target, the seam's family
valuation, the statement matcher's offer, its accepted register and its undo
dialog), every one of them correct for a purchase and every one of them
``-figure`` for a covered paycheck, which is wrong by twice the figure.  The
sign follows the transaction TYPE, never the account class, so the leg is
correct whether the cash account is an asset (Checking) or a liability (a
direct charge on a Credit Card account).

**A SETTLED ROW's own leg has NO reader since plan step ``balance:X-bi-4a``**
(rulings **R-BAL77**, **R-BAL80**, **R-BAL81**).  The cash walk and the
posting writer read a settled row's money as its MOVEMENTS and nothing else,
and the statement matcher prices a settled row at what its covering movement
moves (``status_seam.covered_cash_leg``, ruling **R-BAL81**).  Through
``X-bi-3e`` all of them read the row's leg, one rule in one expression --
``gross - Sigma(card entries) - Sigma(already-dated purchases)``, signed by
type -- which was zero for every covered bill and paycheck by ruling
**R-FM**'s identity and, for a ``purchases``-basis envelope, its un-dated
purchases booked on the close day; the matcher kept that spelling
(``settled_cash_leg``) for one step longer, offering an envelope ROW priced
at its un-dated purchases beside the purchases themselves, and accepting the
row dated a day the fold no longer read.  Those purchases are movements in
flight now, the row books nothing, and the function is deleted.  What
remains of the row-leg family here is :func:`cash_leg_of` -- the matcher's
pricing of a PROJECTED row that holds no purchases, what the bank would see
if the row it names settled -- and :func:`off_statement_sum` with its two
terms, the reconcile panel's cash figure beside the booked one and the
matcher's corrected figure for a line, all ``bank_import``'s questions.

**The two subtracted terms are why the row-leg family existed at all.**  A
card purchase leaves later through its own CC Payback sibling, and a purchase
carrying a recorded bank posting day is already a cash movement of its own on
its own day (ruling **R-FM**, plan step ``balance:X-f3b``) -- so an
envelope's close booked only the remainder, or the same dollars left the
account twice.  That was measured: entry 89 (`$12.79`, taken by the bank on
2026-08-12) was being taken a second time by its envelope's 08-13 close,
reading the whole of that day `$12.79` low (finding **N-274**).

**Where ``gross`` comes from is the CALLER'S, and it must be.**  A projected
row is worth what settling it would book, which is
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
    (``_posting_purchases.emit_purchase_deltas``), so a row's leg priced
    beside it must leave it out or the same dollars are counted twice.

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
    """Return the signed cash a PROJECTED *txn* would move settling at *gross*.

    The one sign rule for a ROW -- *gross, signed by the transaction TYPE*
    (``+`` income, ``-`` expense) -- behind the same TOTAL contributing gate
    :func:`movement_cash_leg` applies to a movement: a non-contributing row is
    worth ``0.00`` whatever *gross* says.  Its one caller is the statement
    matcher (``_candidates._price``, plan step ``bank_import:X-f6a-2``),
    which must compare a bank line against what the app would move if the
    Projected row it names settled -- and which asks this ONLY of a row that
    does not settle from its purchases (``transaction_service.
    settles_from_entries``, the verb's own predicate): such a row's purchases
    are the candidates and the row is worth ``0`` to the offer (ruling
    **R-BAL81**).  A row that reaches here therefore holds no purchase, so
    the two off-statement terms :func:`off_statement_sum` states are ``0``
    for it by construction; this subtracted them anyway through ``X-bi-4a``'s
    first cut, a term no caller could observe, and was
    ``settled_cash_leg`` with its first term supplied until that function
    lost its last reader.

    Args:
        txn: The row being valued.
        gross: What the row would book.

    Returns:
        The signed cash effect, ``0.00`` for a non-contributing row.
    """
    if not is_balance_contributing(txn):
        return Decimal("0.00")
    return _signed_by_type(txn, gross)


def movement_cash_leg(txn: Transaction, entry) -> Decimal:
    """Return the signed cash ONE movement moves through *txn*'s account.

    The movement's twin of :func:`cash_leg_of` (plan step
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
      Cancelled -- moves nothing, the family's zero :func:`cash_leg_of`
      states for the parent (ruling **R-FM**).

    It does NOT read ``settled_on``: whether a movement has POSTED is a
    question about the event stream and the ledger (``_events.
    settled_cash_facts``, ``_posting_purchases.purchase_posts``), while the
    plan holds an UNPOSTED one in flight at the same figure
    (``_events.in_flight_movements``) and the statement matcher prices it at
    what the bank would show for it.  The one figure all three want is this.

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
