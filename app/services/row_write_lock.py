"""The row's write lock: what a door that writes money under a row, or hides one, takes FIRST.

Plan step ``credit_card:CC-5-4a-4``, ruling **R-CC96** (developer 2026-09-23,
"Lock in both"): *"The app's two money writers (add purchase, Mark Paid) and
two hiders (Delete, Archive) also take that lock first, so whichever click
lands second gets a sentence."*  The database half is
:mod:`app.deleted_row_infrastructure`'s arrival arm, which reads the row under
:data:`~app.deleted_row_infrastructure.ROW_WRITE_LOCK` whoever writes the
movement; that is the guarantee.  This module is what gives the app's own
SERVICES words: a door that has taken the lock before it reads the row sees a
race's winner as committed, so it raises its own sentence, or takes the new
purchase off with the rest, where it met the database's raw error (the
purchase, the delete and the archive) or the version pin's
``StaleDataError`` (Mark Paid) -- each measured by removing that door's lock,
against ``tests/test_services/test_cc5_4a4_row_lock_races.py``.  Whether the
screen then SHOWS the sentence is each route's answer, not this module's
(ruling **R-CC101**).

**The owner's write lock FIRST, then the row's** (ruling **R-CC100**,
developer 2026-09-23, "Write lock first here": *"Every door this step locks
(add purchase, Mark Paid, the popover's Actual, Delete, Archive, Mark Credit)
takes the owner's write lock before the row's lock, from the one lock
module."*).  Each function here takes
:func:`app.services.user_write_lock.lock_user_writes` for the row's OWNER
before it locks the row, because a posted row's settle or delete goes on to
take that lock in its ledger reconcile, and a transaction that holds a row and
then asks for the owner's lock deadlocks against one that holds the owner's
lock and then asks for the row -- the reconcile tick settling two rows does
exactly that.  Measured on the step's merged tree (review 5, M1): a Delete that
locked the row first against a two-row settle ended ``DeadlockDetected``; a
Delete alone taking the owner's lock first moved the cycle to Mark Paid x
Delete (``DeadlockDetected`` again); the owner's lock first here, for every
door, ended both pairs cleanly.  It is the invariant
:mod:`app.services.user_write_lock` states -- *this lock must be the FIRST lock
a transaction takes* -- held for these doors; plan step ``balance:X-bn`` owns
the write paths that do not reach this module.  "First" holds only while the
door has flushed nothing before it: the lock's own statement flushes nothing
(a Core ``SELECT``), so a change the door has STAGED -- the archive's
``is_active`` -- reaches the database after it, but a write a caller had
already flushed would precede it.  **Three callers have, and this paragraph
said none did** (review 6, M1, measured 2026-09-23): the popover's Save
flushes a typed note -- or a revert its status -- before its status arm
reaches here, and carry-forward's bulk ``UPDATE`` precedes the owner's lock
its envelope's settle takes; each, racing the owner-first Delete, ended that
Delete in ``DeadlockDetected``.  The race module grades each door's order
through the SERVICE, which cannot see a route's earlier flush.  Ruling
**R-CC106** (developer 2026-09-24, "Both layers, one leaf") ends the class
rather than this list: plan step ``balance:X-bn`` takes the owner's lock at
the start of every request that can write, in one place, and the per-door
calls here go with it.  The owner is the ROW's, whoever clicks: a
companion's purchase queues behind the owner's Delete.

**One row strength, and the reason is a deadlock.**  Every door here takes
``FOR NO KEY UPDATE`` on the row -- the lock a soft delete's ``UPDATE``
takes, the lock the purchase door's payback sync takes, and the lock the
arrival arm takes -- so no door holds a weaker lock on the row and upgrades it
later, which is the shape that deadlocks two raw writers (measured in
:mod:`app.deleted_row_infrastructure`).  The one stronger lock is a delete's:
the row delete's hard arm is a ``DELETE``, which takes ``FOR UPDATE``, and a
door takes first the strongest lock it will take on the row (argued, not
graded: two app doors on one row already queue on the owner's lock).  The
``OF`` clause and the flag's spelling are the ones
:func:`app.services.credit_workflow.lock_source_transaction_for_payback`
argues (``key_share=True`` renders ``FOR NO KEY UPDATE``), and it reads them
from :data:`WRITE_LOCK` so the doors state the strength once.

**Two shapes of door, and which re-read each owes.**

* **The purchase door** (:func:`app.services.entry_service.create_entry`)
  writes no column of the row, so it takes the lock through
  ``lock_source_transaction_for_payback`` and :func:`lock_and_read`, which
  re-reads the WHOLE row after the lock: each of its refusals then asks the
  row as it stands locked.  The payback sync shares it, and so does Mark
  Credit -- which DOES go on to write the row's status through the seam, so
  for Mark Credit the whole-row re-read rebases the version pin: a stale
  Mark Credit absorbs another tab's committed edit instead of answering the
  409 (review 5, L2; the re-read predates ruling R-CC96).
* **The doors that go on to write the row** -- Mark Paid through the status
  seam, and the row delete -- write it under its optimistic version pin
  (``OptimisticLockMixin``), whose job is to answer a concurrent change with a
  409.  Re-reading the whole row would rebase that pin and silently absorb the
  change, so :func:`lock_row` re-reads ``is_deleted`` alone: the one column
  the deleted-row rule asks.

Services here take ORM rows and plain values and import no Flask (``CLAUDE.md``
Architecture); nothing here commits, and every lock is held until the caller's
transaction ends.
"""

from __future__ import annotations

from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models.transaction import Transaction
from app.services.user_write_lock import lock_user_writes

#: ``with_for_update`` keywords for the row's write lock: ``FOR NO KEY UPDATE
#: OF transactions``.  ``OF`` because ``Transaction``'s joined eager loads put
#: nullable outer joins in the default statement, which PostgreSQL refuses to
#: lock across (``lock_source_transaction_for_payback``'s docstring).
WRITE_LOCK = {"of": Transaction, "key_share": True}


def lock_row(row: Transaction, *, removing: bool = False) -> None:
    """Lock *row* for a door that goes on to write it; re-read ``is_deleted`` under the lock.

    The owner's write lock first, then the row's (the module docstring).
    Taken before the door's first read of the row's state, so a race's winner
    has committed by the time the door asks: a delete that committed while
    this waited is ``is_deleted`` here, and the door's refusal answers it.

    **Only ``is_deleted`` is re-read** (the module docstring says why), as the
    database holds it in this transaction: the row lock's statement flushes a
    staged change first, so a caller that staged an ``is_deleted`` change and
    lets autoflush run (the transfer restore) reads its own value back.  No
    caller stages one inside a ``no_autoflush`` block and then reaches here;
    one that did would have its staged value replaced by the committed one.

    Args:
        row: The session-attached row the door is about to write.
        removing: Whether the door may take the row out of the table (the row
            delete), which is a ``DELETE`` and so takes ``FOR UPDATE``; taking
            that first is what spares the door an upgrade.

    Raises:
        StaleDataError: When the row no longer exists -- a hard delete
            committed while this waited.  The ORM's own answer when a
            version-pinned write finds its row gone, so each caller answers it
            as it already answered that: the row routes' 409 and re-fetch.
    """
    lock_user_writes(row.user_id)
    locked = _lock(row.id, removing=removing)
    if locked is None:
        raise StaleDataError(
            f"Transaction {row.id} was removed while this change waited for it."
        )
    set_committed_value(row, "is_deleted", locked.is_deleted)


def lock_and_read(transaction_id: int) -> "Transaction | None":
    """Lock row *transaction_id*, THEN read the whole row as it stands locked.

    The owner's write lock first, then the row's (the module docstring); the
    owner is read by a plain statement that locks nothing (an ORM query, so it
    flushes whatever the caller staged: no caller stages anything first).  For
    the doors that reach it through
    :func:`app.services.credit_workflow.lock_source_transaction_for_payback`:
    the purchase door and the payback sync, which write none of the row's own
    columns, so re-reading every column rebases nothing they write -- and Mark
    Credit, which does (the module docstring's first bullet).

    **Two statements, and the split is the point** (ruling **R-CC99**,
    measured 2026-09-23).  One statement that both locked the row and loaded
    it -- ``Transaction``'s joined eager loads included -- handed back, after
    waiting on a Mark Paid, ``status_id`` of the Paid row it now held and a
    ``status`` of ``None``: PostgreSQL re-reads the LOCKED row at its newest
    version once the wait ends, and re-checks the join against the
    ``ref.statuses`` row it had read BEFORE the wait, which no longer matches.
    The purchase door's settled-row refusal reads ``txn.status``, so it
    admitted a purchase beside the payment Mark Paid had just recorded -- a
    $12.34 purchase under a $300.00 Groceries close, counted twice.  Locked
    first by a statement that joins nothing, the read after it is a new
    statement with a fresh snapshot, and every relationship it loads agrees
    with the row.

    Args:
        transaction_id: The row to lock and read.

    Returns:
        The locked row, refreshed; ``None`` when no such row exists.
    """
    owner_id = (
        db.session.query(Transaction.user_id)
        .filter(Transaction.id == transaction_id)
        .scalar()
    )
    if owner_id is None:
        return None
    lock_user_writes(owner_id)
    if _lock(transaction_id, removing=False) is None:
        return None
    return (
        db.session.query(Transaction)
        .filter(Transaction.id == transaction_id)
        .populate_existing()
        .one()
    )


def _lock(row_id: int, *, removing: bool):
    """Take the row's lock by a statement that joins nothing; return its ``is_deleted`` row.

    The ONE locking statement every door's lock goes through, so the strength
    is written once (:data:`WRITE_LOCK`) and no locking read carries a join
    for the wait to leave stale (:func:`lock_and_read` says what that cost).

    Args:
        row_id: The row to lock.
        removing: Take ``FOR UPDATE`` (a door that may ``DELETE`` the row)
            rather than the write lock.

    Returns:
        A row holding ``is_deleted`` as of the lock, or ``None`` when the row
        does not exist.
    """
    return (
        db.session.query(Transaction.is_deleted)
        .filter(Transaction.id == row_id)
        .with_for_update(**{**WRITE_LOCK, "key_share": not removing})
        .one_or_none()
    )


def lock_rows(owner_id: int, *criteria) -> None:
    """Lock every row of *owner_id* matching *criteria* for a bulk hide.

    The archive's form of :func:`lock_row`, the owner's write lock first: it
    hides rows by a bulk ``UPDATE`` whose subquery would otherwise read the
    database as it stood BEFORE the statement waited, so a purchase committed
    during the wait was invisible to it.  Locking first and then asking in NEW
    statements gives each a fresh ``READ COMMITTED`` snapshot taken after
    every row is held.  No row order is imposed: two app doors over one
    owner's rows already queue on the owner's lock.

    Args:
        owner_id: The id of the user who owns every row *criteria* can match.
        *criteria: SQLAlchemy filter clauses over :class:`Transaction`.
    """
    lock_user_writes(owner_id)
    (
        db.session.query(Transaction.id)
        .filter(Transaction.user_id == owner_id, *criteria)
        .with_for_update(**WRITE_LOCK)
        .all()
    )
