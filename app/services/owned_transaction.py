"""The one service-level load of a transaction its caller's owner must own.

Plan step ``balance:X-bn``.  Five doors each load a ``budget.transactions``
row by id and refuse it unless it is the owner's: Mark Credit and its undo
(:mod:`app.services.credit_workflow`), the purchase door and the purchase
list (:mod:`app.services.entry_service`), and the payback sync
(:mod:`app.services.entry_credit_workflow`).  Three of them did it through
``credit_workflow.lock_source_transaction_for_payback``, which also took the
row's write lock and re-read the row under it; two spelled the load and the
comparison out for themselves.  The lock existed so a door would see a racing
click's committed change, and since plan step ``balance:X-bn`` every writing
transaction a signed-in request opens takes its owner's write lock before it
reads any of that owner's data (:mod:`app.db_transaction`), so two of one
owner's writing transactions never overlap and a door's first read already
sees the other click's committed work.  Per TRANSACTION, not per click: what a
click does after its own commit or rollback -- drawing its answer, a refusal's
re-read -- runs in a second transaction, which takes the lock again only after
a Delete queued behind the first may have committed (finding BAL-565; plan
step ``balance:X-dc`` makes each save one transaction).  What was left of the
locking function was this load, so it has
one home, below every door that asks it.

Services take plain values and import no Flask (``CLAUDE.md`` Architecture).
"""

from app.exceptions import NotFoundError
from app.extensions import db
from app.models.transaction import Transaction


def load_owned_transaction(transaction_id: int, owner_id: int) -> Transaction:
    """Load transaction *transaction_id*, refusing it unless *owner_id* owns it.

    The owner is the row's own ``user_id`` column (plan step
    ``pay_calendar:C13-a``).  A row that does not exist and a row another
    user owns get the same :class:`~app.exceptions.NotFoundError` and the same
    message, so a caller probing ids cannot tell them apart (the security
    response rule: 404 for both).

    It loads and scopes only.  A deleted row is returned like any other:
    whether a hidden row may take the door's act is each door's own refusal
    (Mark Credit answers it "not found", the purchase door names the row),
    so it stays with the caller.

    Args:
        transaction_id: The ``budget.transactions`` id to load.
        owner_id: The id of the user whose data the caller acts on -- a
            companion's linked owner, already resolved by the caller.

    Returns:
        The owner's :class:`~app.models.transaction.Transaction`.

    Raises:
        NotFoundError: When no such row exists, or *owner_id* does not own it.
    """
    txn = db.session.get(Transaction, transaction_id)
    if txn is None or txn.user_id != owner_id:
        raise NotFoundError(f"Transaction {transaction_id} not found.")
    return txn
