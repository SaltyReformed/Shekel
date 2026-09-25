"""
Shekel Budget App -- The per-user write lock

ONE transaction-scoped PostgreSQL advisory lock, keyed on the owning user,
serialising every write of that user's data.  **Since plan step
``balance:X-bn`` it is taken in exactly two places** (rulings **R-CC106**,
**R-CC114**, **R-CC115**): at the start of EVERY command transaction a
signed-in request opens (:mod:`app.db_transaction`), before that transaction
reads anything, and at the start of each deploy reconcile, which takes every
owner's (:func:`lock_every_user_writes`).  No service takes it for itself any
more, and ``tests/test_arch/test_the_owner_lock_has_one_home.py`` refuses one
that tries.  *It was taken by sixteen calls inside the write paths that needed
it most, each taking it at its own point in the transaction, which is what made
the deadlock below reachable.*

Two families of write are why the lock exists at all, and they need the SAME
lock because the second reads the first's output:

* **The structural pay-period mutations** -- top-up / extend / truncate, and so
  regenerate and reset, which are compositions of those.  Each counts or
  classifies the user's periods and then appends or deletes against that count.
* **Every posting-ledger RECONCILE** -- the cash anchor reconcile
  (:mod:`app.services.account_posting_service`) and the loan reconcile
  (:mod:`app.services.loan_posting_service`).  Each reads what the ledger has
  posted, subtracts it from what the account's facts say it should hold, and
  writes the difference.

**Why one lock and not two.**  A reconcile does not only read the account's
ledger: it derives each correction's pay period from the OWNER'S CALENDAR
(:meth:`app.services.pay_calendar.PayCalendar.filing_period`, loaded through
:func:`app.services._posting_reconcile.filing_calendar_for`), and
``journal_entries.pay_period_id`` is an ``ON DELETE CASCADE`` FK.  So a
concurrent truncate can delete the very period a reconcile is filing under, and
the correction it just wrote goes with it.  The consistency boundary of a
reconcile is therefore the user's ledger AND the calendar it keys on -- the
union, which is exactly this lock's subject.  It is also one KEY per user, so
no transaction has to order two of them against each other.

**Deadlock: what one key does and does not buy, corrected on evidence.**  An
earlier version of this docstring said deadlock was "structurally impossible on
every request path".  **That was FALSE, and a neutral adversarial review
reproduced the cycle**, because the argument considered only
advisory-vs-advisory ordering while this lock was taken in transactions that
also held ROW locks.  The cycle, as it stood until plan step ``balance:X-bn``:

* A settle took row locks FIRST.  ``update_transfer`` UPDATEd the transfer and
  both shadow transactions, those flushed, and only then did the posting sync
  reach this lock -- measured at statements 2-4 and 19 of one loan-payment
  settle.
* A truncate or reset took this lock first and then bulk-DELETEd pay periods,
  which CASCADE to ``budget.transactions`` and so took row locks on exactly
  the rows a concurrent settle might hold.

Two such transactions, same user, opposite orders: PostgreSQL detected it and
aborted one with ``DeadlockDetected``.  **No money was corrupted** -- the loser
rolled back atomically -- but the victim was an unhandled 500 on a money route.
It needed a settle and a schedule rebuild for one user to overlap, which is two
browser tabs, and it did not exist before this lock did.

**The real invariant, stated so the next author can hold it: this lock must be
the FIRST lock a transaction takes.**  *Until plan step ``balance:X-bn`` it was
a property each write door had to hold for itself, and most did not: the
settle paths took row locks first (finding **N-193**), plan step
``credit_card:CC-5-4a-4`` (ruling **R-CC100**) added the lock ahead of the row
locks it introduced, and a census over the whole suite (2026-09-24) still
found 27 endpoints that locked a row and then asked for this lock in the same
request -- the popover's Save, a purchase's edit and delete, carry-forward and
the reconcile tick among them.*  **It is now structural**: the lock is taken
where each command transaction begins (:mod:`app.db_transaction`), so there is
no earlier statement for a row lock to ride on -- and the deploy reconciles,
which hold no request, take every owner's at their own start, in ascending
order.  Shipping the lock with a detected-and-rolled-back deadlock was strictly
better than shipping the silent ledger divergence it replaced; shipping it
with a docstring claiming the deadlock impossible was not, which is why this
paragraph said so until the step that made it so.

**Why a lock at all, rather than a constraint.**  A reconcile emits the
DIFFERENCE between target and posted, and repeated deltas under one key are the
design (a correction whose basis moves is adjusted by a further entry, never
edited -- the ledger is append-only).  No unique index can distinguish a
legitimate second delta from a racing duplicate.  Nor can the read take a row
lock: when nothing is posted yet there are no rows to lock, which is the
classic phantom.  A predicate lock is what is wanted, and in PostgreSQL that is
either SERIALIZABLE isolation with a retry loop in every route, or an advisory
lock.  This is the advisory lock.

**Why the reconcile ever appeared to be safe.**  Until plan step X-f1c3c a cash
true-up also UPDATEd ``accounts``, and that UPDATE autoflushed and took a row
lock BEFORE the walk -- serialising the reconcile by accident, through a column
that had nothing to do with it.  Ruling R-EN deleted the column and with it the
accident.  Measured, with the interleave forced at the reconcile's read: two
concurrent true-ups on an account reconciled at ``$4,000.00`` both answer 200,
and the account's linked ledger settles at ``$1,000.00`` while its resolved
assertion reads ``$2,000.00`` -- both sides wrong, trial balance still
``$0.00`` because the anchor-equity leg mirrors the error, so nothing fails
loudly.  The LOAN reconcile never had even the accident: it is fed by an
append-only event table, so it has carried the same race since Commit 16, and
ruling R-EN cited it as the precedent to copy.

Transaction-scoped: PostgreSQL releases the lock at COMMIT or ROLLBACK, so it
cannot leak -- and a request that commits and goes on writing takes it again
in its next transaction (:mod:`app.db_transaction`).  Re-entrant, which no
caller relies on any more: each transaction takes it once, at its start.

Flask-isolated -- takes and returns plain data, never imports ``request`` /
``session``.  Takes no transaction of its own: the caller owns the boundary,
and the lock lives exactly as long as that transaction.
"""

from sqlalchemy import Connection, func, select

from app.extensions import db
from app.models.user import User

# Advisory-lock namespace for the per-user write lock.  The two-argument
# ``pg_advisory_xact_lock(namespace, user_id)`` form keys the lock on
# ``(this constant, user_id)``, so it can never collide with some other
# advisory lock that happens to use the same ``user_id`` as a single key.  The
# value is arbitrary but FIXED -- "SHKL" in ASCII -- and fits a signed int4
# (< 2**31 - 1).
#
# It is deliberately the SAME VALUE this constant held while it lived in
# ``pay_schedule_service`` as ``_PAY_SCHEDULE_LOCK_NAMESPACE``.  Changing it
# during the move would mean old and new code taking DIFFERENT keys for the
# same pay-period mutation, so a rolling deploy would run a window with no
# schedule lock at all.
_USER_WRITE_LOCK_NAMESPACE = 0x53484B4C  # 1397246796
# The decimal above read 1397705036 in this constant's OLD home and was
# carried over with it; the hex is the value PostgreSQL actually keys on
# (confirmed in a real `deadlock detected` DETAIL line: advisory lock
# [.., 1397246796, ..]), so only the comment was ever wrong.


def take_owner_write_lock(connection: Connection, owner_id: int) -> None:
    """Take *owner_id*'s write lock on *connection* for the rest of its transaction.

    The one statement of the lock, taken by exactly two callers:
    :mod:`app.db_transaction` at the start of every command transaction a
    signed-in request opens, and :func:`lock_every_user_writes` for the deploy
    reconciles (the module docstring).  Blocks until any other transaction
    holding the same key commits or rolls back; PostgreSQL releases it
    automatically at this transaction's end.

    **On a CONNECTION, never through the session**: a session statement
    autoflushes, so a transaction begun with rows already staged would write
    them -- and take their row locks -- before this one, which is the order
    the module docstring's invariant forbids.  *It was
    ``lock_user_writes(user_id)``, a session statement, until plan step
    ``balance:X-bn``; that step deleted every caller it had.*

    The lock is not a substitute for the constraints underneath it: a duplicate
    PAYDAY is still forbidden by ``uq_pay_periods_user_start``.  The lock is
    what a writer has instead of such a constraint when the quantity it must
    protect is something it READ rather than a row it is about to write -- a
    posted SUM for the reconciles, and since ruling **R-EQ** (plan step
    X-f1c4b) the governing assertion for the two anchor doors, whose duplicate
    rule moved out of a unique index for exactly that reason.

    Args:
        connection: The connection whose transaction takes the lock.
        owner_id: The id of the user whose data the transaction writes, used
            as the lock's second key.
    """
    connection.execute(
        select(func.pg_advisory_xact_lock(_USER_WRITE_LOCK_NAMESPACE, owner_id))
    )


def lock_every_user_writes() -> list[int]:
    """Take EVERY user's write lock, ascending by user id, for this transaction.

    The all-owners form, for the THREE deploy-time reconciles that between them
    touch every owner:
    :func:`app.services.account_posting_service.backfill_all_account_anchor_postings`,
    its loan twin, and
    :func:`app.services.posting_service.resync_all_cash_postings`.  Those are
    the only transactions in the app that reconcile more than one owner's
    ledger.  Without it they would take their per-user locks in whatever order
    their id-ordered enumeration happens to visit owners, and two such
    transactions running at once could deadlock on that alone.

    *An earlier version of this docstring said "the two backfills" and missed
    ``resync_all_cash_postings``, which is the FIRST of the three to run at
    deploy.  A neutral adversarial review found it.*

    Acquiring ascending by user id in PYTHON, one statement per user, is
    deliberate: ``SELECT pg_advisory_xact_lock(ns, id) FROM users ORDER BY id``
    would order the RESULT, not the evaluation, so the locks could still be
    taken in scan order.  The user count is small and this runs once per
    deploy.

    Returns:
        The user ids locked, ascending -- for the deploy log and for tests to
        assert the acquisition order without re-deriving it.
    """
    user_ids = [
        user_id
        for (user_id,) in db.session.query(User.id).order_by(User.id).all()
    ]
    connection = db.session.connection()
    for user_id in user_ids:
        take_owner_write_lock(connection, user_id)
    return user_ids
