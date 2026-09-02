"""
Shekel Budget App -- The per-user write lock

ONE transaction-scoped PostgreSQL advisory lock, keyed on the owning user,
serialising every write whose correctness depends on reading state it is about
to change.  Two families of write need it, and they need the SAME lock because
the second reads the first's output:

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
every request path".  **That is FALSE, and a neutral adversarial review
reproduced the cycle**, because the argument considered only
advisory-vs-advisory ordering while this lock is taken in transactions that also
hold ROW locks:

* A settle takes row locks FIRST.  ``update_transfer`` UPDATEs the transfer and
  both shadow transactions, those flush, and only then does the posting sync
  reach ``lock_user_writes`` -- measured at statements 2-4 and 19 of one
  loan-payment settle.
* A truncate or reset takes this lock first and then bulk-DELETEs pay periods,
  which CASCADEs to ``budget.transactions`` and so takes row locks on exactly
  the rows a concurrent settle may hold.

Two such transactions, same user, opposite orders: PostgreSQL detects it and
aborts one with ``DeadlockDetected``.  **No money is corrupted** -- the loser
rolls back atomically -- but the victim is an unhandled 500 on a money route.
It needs a settle and a schedule rebuild for one user to overlap, which is two
browser tabs, and it did not exist before this lock did.

**The real invariant, stated so the next author can hold it: this lock must be
the FIRST lock a transaction takes.**  The pay-period paths already satisfy it.
The settle paths do not, and closing that means acquiring at the write-service
entry rather than inside the reconcile -- a change with its own blast radius,
recorded as finding **N-193** rather than smuggled in here.  Shipping the lock
with a detected-and-rolled-back deadlock is strictly better than shipping the
silent ledger divergence it replaces; shipping it with a docstring claiming the
deadlock is impossible is not.

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
cannot leak.  Re-entrant, so a nested caller (a reset that resyncs, an
all-scenarios sync that loops the per-scenario one) takes it harmlessly more
than once.

Flask-isolated -- takes and returns plain data, never imports ``request`` /
``session``.  Takes no transaction of its own: the caller owns the boundary,
and the lock lives exactly as long as that transaction.
"""

from sqlalchemy import func, select

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


def lock_user_writes(user_id: int) -> None:
    """Take the user's write lock for the remainder of this transaction.

    See the module docstring for what it serialises and why it is one lock.
    Blocks until any other transaction holding the same key commits or rolls
    back; PostgreSQL releases it automatically at this transaction's end.

    The lock is not a substitute for the constraints underneath it: a duplicate
    PAYDAY is still forbidden by ``uq_pay_periods_user_start``.  *This sentence
    named ``UNIQUE(user_id, period_index)`` until plan step
    ``pay_calendar:C4-c`` dropped that constraint with the ordinal column it
    bounded; the point it illustrates is unchanged, and the remaining key is
    the one that makes it.*
    The lock is what a caller has instead of such a constraint when the quantity
    it must protect is something it READ rather than a row it is about to write
    -- a posted SUM for the reconciles, and since ruling **R-EQ** (plan step
    X-f1c4b) the governing assertion for the two anchor doors, whose duplicate
    rule moved out of a unique index for exactly that reason.  Those doors take
    it BEFORE their read, which is the ordering invariant this module's docstring
    states.

    Args:
        user_id: The owning user's id, used as the lock's second key.
    """
    db.session.execute(
        select(func.pg_advisory_xact_lock(_USER_WRITE_LOCK_NAMESPACE, user_id))
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
    for user_id in user_ids:
        lock_user_writes(user_id)
    return user_ids
