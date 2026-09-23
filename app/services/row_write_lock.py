"""The row's write lock: what a door that writes money under a row, or hides one, takes FIRST.

Plan step ``credit_card:CC-5-4a-4``, ruling **R-CC96** (developer 2026-09-23,
"Lock in both"): *"The app's two money writers (add purchase, Mark Paid) and
two hiders (Delete, Archive) also take that lock first, so whichever click
lands second gets a sentence."*  The database half is
:mod:`app.deleted_row_infrastructure`'s arrival arm, which reads the row under
:data:`~app.deleted_row_infrastructure.ROW_WRITE_LOCK` whoever writes the
movement; that is the guarantee.  This module is what gives the app's own doors
WORDS: a door that has taken the lock before it reads the row sees a race's
winner as committed, so it refuses with its own sentence, or takes the new
purchase off with the rest, where it met the database's raw error (the
purchase, the delete and the archive) or the version pin's 409 (Mark Paid) --
each measured by removing that door's lock, against
``tests/test_services/test_cc5_4a4_row_lock_races.py``.

**One strength, and the reason is a deadlock.**  Every door here takes
``FOR NO KEY UPDATE`` -- the lock a soft delete's ``UPDATE`` takes, the lock
the purchase door's payback sync takes, and the lock the arrival arm takes --
so no door holds a weaker lock on the row and upgrades it later, which is the
shape that deadlocks two writers (measured, and argued, in
:mod:`app.deleted_row_infrastructure`).  The one stronger lock is a delete's:
the row delete's hard arm is a ``DELETE``, which takes ``FOR UPDATE``, and a
door takes first the strongest lock it will take on the row.  The ``OF``
clause and the flag's spelling are the ones
:func:`app.services.credit_workflow.lock_source_transaction_for_payback`
argues (``key_share=True`` renders ``FOR NO KEY UPDATE``), and it reads them
from :data:`WRITE_LOCK` so the doors state the strength once.

**Two shapes of door, and which re-read each owes.**

* **The purchase door** (:func:`app.services.entry_service.create_entry`)
  writes no column of the row, so it takes the lock through
  ``lock_source_transaction_for_payback`` and :func:`lock_and_read`, which
  re-reads the WHOLE row after the lock: each of its refusals then asks the
  row as it stands locked.  Mark Credit and the payback sync share it.
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

from sqlalchemy import inspect
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models.transaction import Transaction

#: ``with_for_update`` keywords for the row's write lock: ``FOR NO KEY UPDATE
#: OF transactions``.  ``OF`` because ``Transaction``'s joined eager loads put
#: nullable outer joins in the default statement, which PostgreSQL refuses to
#: lock across (``lock_source_transaction_for_payback``'s docstring).
WRITE_LOCK = {"of": Transaction, "key_share": True}


def lock_row(row: Transaction, *, removing: bool = False) -> None:
    """Lock *row* for a door that goes on to write it; re-read ``is_deleted`` under the lock.

    Taken before the door's first read of the row's state, so a race's winner
    has committed by the time the door asks: a delete that committed while
    this waited is ``is_deleted`` here, and the door's refusal answers it.

    **Only ``is_deleted`` is re-read** (the module docstring says why), and
    only when this transaction is not itself changing it: the status seam
    runs inside callers' ``no_autoflush`` blocks, where an un-delete this
    transaction has staged is not yet in the database, and the staged value
    is the one the door must judge.

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
    locked = _lock(row.id, removing=removing)
    if locked is None:
        raise StaleDataError(
            f"Transaction {row.id} was removed while this change waited for it."
        )
    if not inspect(row).attrs.is_deleted.history.has_changes():
        set_committed_value(row, "is_deleted", locked.is_deleted)


def lock_and_read(transaction_id: int) -> "Transaction | None":
    """Lock row *transaction_id*, THEN read the whole row as it stands locked.

    For a door that writes none of the row's own columns (the purchase door,
    Mark Credit, the payback sync -- through
    :func:`app.services.credit_workflow.lock_source_transaction_for_payback`),
    so re-reading every column rebases nothing it will write.

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


def lock_rows(*criteria) -> None:
    """Lock every row matching *criteria* for a bulk hide, in ``id`` order.

    The archive's form of :func:`lock_row`: it hides rows by a bulk ``UPDATE``
    whose subquery would otherwise read the database as it stood BEFORE the
    statement waited, so a purchase committed during the wait was invisible
    to it.  Locking first and then asking in NEW statements gives each a fresh
    ``READ COMMITTED`` snapshot taken after every row is held.  In ``id``
    order, so two bulk lockers over overlapping rows queue rather than
    deadlock.

    Args:
        *criteria: SQLAlchemy filter clauses over :class:`Transaction`.
    """
    (
        db.session.query(Transaction.id)
        .filter(*criteria)
        .order_by(Transaction.id)
        .with_for_update(**WRITE_LOCK)
        .all()
    )
