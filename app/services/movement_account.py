"""
Shekel Budget App -- the ONE gate on which account a movement under a row may name.

A movement's ``account_id`` is the account its money moved through (design
``docs/design/credit_card_from_scratch.md`` 3.2, rulings **R-CC15** and
**R-BAL75**), and three writers put one onto an account other than its
row's: the purchase door (``entry_service._doors.create_entry``, plan step
``credit_card:CC-5-2``), the settle verb
(``transaction_service._settle.settle_transaction``, plan step ``CC-5-3``,
where the covering movement takes the TENDER the owner names) and the
settled row's "Paid from" correction
(``transaction_service._door._correction_for_status``, the identity arm
that re-points the kept movement).  All three admit exactly one set of
accounts, and it is spelled ONCE here so they cannot part: a door written
against another door's body inherits its write and none of its refusals
([[feedback_a_copied_write_inherits_no_refusal]]).
The gate lived inside the purchase door through ``CC-5-2``; ``CC-5-3`` moved
the leaf rather than copying it (rule 14: where a layer puts the shared leaf
out of reach, move the leaf).

**What a movement may name is the picker's predicate** (rulings **R-CC37**
and **R-CC39**): the row's own account, or a member of the ROW OWNER's
cash-flow set -- the primary grid account plus the active cards
(:func:`~app.services.account_resolver.resolve_owner_cash_flow_set`, no
override), the tuple :func:`~app.services.cash_flow_set.purchase_accounts`
renders every picker from.  So a swipe or a payment on a 401(k), an IRA, a
house, a loan or a savings account is UNWRITABLE rather than merely
unoffered, because a movement filed on an account whose balance never folds
movements would drop the row's hold on checking by its figure and land where
no screen reads it; a row that LIVES on such an account keeps its movements
there, its own account being the tuple's first member.

**The gate is against the ROW's owner, never the caller** (design 3.2,
ruling **R-CC11**): a companion reaches the row through the owner's
accessible-transaction path and owns no account at all, so a gate on the
caller's ``user_id`` would refuse every companion swipe and every companion
Mark Paid.  ``txn.user_id`` is the owner, and
``fk_transaction_entries_owner_account`` holds the written movement to the
same fact -- this gate is what turns the key's ``IntegrityError`` into the
security response rule's 404, one answer for "no such account" and "not the
owner's" alike, raised before the account's name is read.

The two refusals below the 404 are the predicate's consequences, each given
the sentence that names its remedy: an ARCHIVED card is not an active member
(the row doors admit an archived account, a pre-existing admission ruling
**R-CC31**'s review named and a new door does not copy); an account that is
neither the row's nor a member -- a loan, a 401(k), savings -- is not one
money can be recorded as moving through.

The books boundary on the card's OPENING is not asked here, and by
construction: the day a movement takes is written by
``settle_day.record_settle_day``, which reads the MOVEMENT's ``account_id``
-- so a movement dated on or before its account's opening is refused there,
whatever account its row names.

Services-boundary discipline (``CLAUDE.md`` Architecture): no Flask imports;
one read of the named account, one resolve of the owner's set; no mutation.
"""

from app.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.services.account_resolver import resolve_owner_cash_flow_set
from app.services.cash_flow_set import purchase_accounts


def admitted_movement_account_id(
    txn: Transaction, account_id: int, *, movement: str,
) -> int:
    """Return *account_id* once it is one a movement under *txn* may name.

    Args:
        txn: The parent row, already proven the caller's.
        account_id: The account the door was asked to write the movement on.
        movement: What the door calls the movement in its refusal --
            ``"purchase"`` at the purchase door, ``"payment"`` at the settle
            verb -- so the sentence names the act the owner performed.

    Returns:
        The ``budget.accounts.id`` to write onto the movement: *account_id*.

    Raises:
        NotFoundError: The account does not exist or is not the ROW's owner's.
        ValidationError: The account is archived, or is neither the row's own
            nor a member of the owner's cash-flow set.
    """
    if account_id == txn.account_id:
        return txn.account_id
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != txn.user_id:
        raise NotFoundError("Account not found.")
    if not account.is_active:
        raise ValidationError(
            f"'{account.name}' is archived, so a {movement} cannot be filed "
            "on it. Unarchive the account first, or pick another."
        )
    cash_flow = resolve_owner_cash_flow_set(txn.user_id)
    if account.id not in {a.id for a in purchase_accounts(cash_flow, txn)}:
        raise ValidationError(
            f"'{account.name}' is not an account a {movement} can be paid "
            "from: pick this row's own account, your checking account, or "
            "one of your credit cards."
        )
    return account.id
