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

**The two subtracted terms are why the family exists at all.**  A card purchase
leaves later through its own CC Payback sibling, and a purchase carrying a
recorded bank posting day is already a cash movement of its own on its own day
(ruling **R-FM**, plan step ``balance:X-f3b``) -- so an envelope's close books
only the remainder, or the same dollars leave the account twice.  That was
measured: entry 89 (`$12.79`, taken by the bank on 2026-08-12) was being taken a
second time by its envelope's 08-13 close, reading the whole of that day
`$12.79` low (finding **N-274**).

**Where ``gross`` comes from is the CALLER'S, and it must be.**  A settled row
owns its figure; a projected one is worth what settling it would book, which is
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
from app.services.row_valuation import owned_contribution
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
    ``+/-effective_amount``.  For an ENVELOPE at settle ``effective_amount``
    equals the sum of ALL its entries (``compute_actual_from_entries`` sets
    ``actual_amount`` so), and subtracting the two collapses the result to the
    UNPOSTED debit outflow -- with no branch on "is this an envelope".

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
    cash WALK (:func:`app.services.cash_ledger.walk_cash_ledger`), which folds
    it.  A second copy would let the projection and the posted ledger disagree
    about what a settled row was worth -- measured on production 2026-07-25
    before this move, a ``effective_amount``-only walk diverged from the posted
    ledger on 10 of 130 Checking rows and by up to ``$181.58`` on one, because
    every one of them was an envelope carrying credit-card entries.

    The bulk oracle reader ``posting_reads.settled_transaction_effect`` computes
    the same sum in SQL and deliberately stays independent: it is the Step-3
    reconciliation oracle's own window onto the ledger, and an oracle that
    shared this implementation could not grade it.

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
    can see; this one is total instead.  **The same gate governs the row's
    PURCHASES** (ruling R-FM): a non-contributing row's purchases post nothing
    either, in the walk (:func:`~._events.settled_cash_facts`) and in the ledger
    (``posting_service``), so the zero here is the whole family's zero rather
    than the parent leg's alone.

    Args:
        txn: The transaction whose confirmed cash effect to value.  A
            non-contributing row (soft-deleted, Credit, or Cancelled) returns
            ``0.00`` whatever entries it carries.

    Returns:
        The signed confirmed cash effect as a ``Decimal``.
    """
    if not is_balance_contributing(txn):
        return Decimal("0.00")
    return cash_leg_of(txn, owned_contribution(txn))


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
    settled row OWNS its figure (:func:`~app.services.row_valuation.owned_contribution`,
    which REFUSES a derived row rather than answering ``None`` into a money
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
    net = gross - off_statement_sum(txn)
    return net if txn.is_income else -net
