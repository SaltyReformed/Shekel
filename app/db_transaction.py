"""
Shekel Budget App -- what a request's transaction IS (plan step balance:X-i3)

**A request is a QUERY or a COMMAND, and its transaction says which.**  A query
runs at ``REPEATABLE READ, READ ONLY`` -- one snapshot of the database for every
statement it issues, and no write possible inside it.  A command runs at
PostgreSQL's default ``READ COMMITTED``, writable, exactly as every request did
before this module existed.

**Why a query needs one snapshot** (finding **N-353**).  Every figure a render
publishes is assembled from many statements, and under ``READ COMMITTED``
*each statement gets its own snapshot*.  So a pass can read the owner's pay
calendar in one statement and the rows filed against it forty statements later,
and the two can disagree: ``/grid``'s rolling top-up appends a pay period,
repopulates it from every active template and commits mid-render, which leaves a
concurrent render holding rows filed in a period its calendar never saw.  Nine
sites in ``app/`` reasoned about that class in their own words and four were
independent discoveries with four different local accommodations.  Measured
here, against this application's own session: two counts across one concurrent
committed append answer ``(1, 2)`` under ``READ COMMITTED`` and ``(1, 1)`` under
this module's query mode.

**Why a command may NOT have one snapshot, which is the harder half.**  The
posting-ledger reconciles are read-modify-write under an advisory lock
(:func:`app.services.user_write_lock.lock_user_writes`): take the lock, re-read
what is posted, write the difference.  The waiting transaction's re-read is
*required* to see the winner's just-committed postings, and only ``READ
COMMITTED`` shows them -- under one snapshot the waiter would re-read its own
pre-lock picture and reconcile to a stale target, which is the divergence ruling
**R-EN** measured at ``$4,000.00`` asserted against ``$1,000.00`` settled, with
the trial balance still ``$0.00`` because the anchor-equity leg mirrors the
error.  A snapshot is therefore not a strictly better setting to run everything
at; it is right for reads and wrong for writes, and the line between them is
what this module draws.

**Why ``READ ONLY`` and not merely ``REPEATABLE READ``.**  It costs nothing and
it converts "a render wrote" from a thing a reviewer must notice into a thing
PostgreSQL refuses, which is what makes the boundary structural rather than a
convention: a GET that writes fails loudly on its first write statement instead
of quietly costing its own render the snapshot.

**What ``READ ONLY`` does NOT refuse**, measured against this project's own
PostgreSQL rather than assumed: ``pg_advisory_xact_lock`` succeeds inside one
(and assigns no transaction id), and so does ``SELECT ... FOR UPDATE``.  So the
two primitives whose correctness this module's argument turns on are exactly
the two the guardrail cannot see.  Neither is reachable from a query today --
``lock_user_writes`` is GET-reachable only inside :func:`write_transaction`,
where the mode is already a command's, and ``with_for_update`` only from POST
doors -- but "a render cannot take a lock" is a census, not a guarantee, and
saying otherwise would be the claim rather than the code being wrong.

**Why the mode is bound at ``after_begin`` and not in a before-request hook.**
``SET TRANSACTION`` must precede the transaction's first statement, and
``before_request`` cannot guarantee that: ``_refresh_last_activity`` reads
``current_user``, which loads the user row, and Flask runs before-request hooks
in registration order.  Binding at the session's own ``after_begin`` fires
between ``BEGIN`` and the statement that caused it, whoever issued it, so the
rule cannot be defeated by an import order or a hook registered later.

**Why there is ALSO a request boundary** (:func:`register_transaction_boundary`),
which is a TEST-FIDELITY property rather than a production one and is stated
that way because measuring it is what found it.  In production a request gets
its own app context and so its own session, so nothing is open when it starts
and ``after_begin`` binds the mode on its first statement.  Under the test
client the outer ``app.app_context()`` is reused, so the test body and the
request share one session: measured over the whole suite, **2,302 of 2,978
GET requests arrived on a transaction that was already open**, which would have
left the guarantee bound for under a quarter of them and CI blind to a render
that writes.  The boundary ends that transaction so the request opens its own.

**What it refuses, and why that is a precondition rather than a fence.**  A
query request whose transaction has ALREADY WRITTEN cannot be given its own
snapshot without discarding work that is not this module's to discard.  In
production the state is unreachable -- a request's session is its own -- so the
refusal names a caller that built uncommitted state and then issued a request
against it, which is a test that is unfaithful to the thing it is testing.
Measured at **36 of 2,978** GET requests before those fixtures were made to
commit, and at **0** carrying unflushed ORM state.

**And it detects a FLUSHED write, not pending ORM state**, which is the honest
limit of the test it can afford: PostgreSQL assigns a transaction id at the
first statement that writes, so a caller holding an unflushed ``session.add()``
is not refused -- the rollback below expunges it, silently.  The zero measured
above is what makes that acceptable today rather than a property anything
enforces; a caller that stages a row and then issues a request is already
describing a state no request can see.

**What this module does NOT do**, said here because the boundary is worth
knowing rather than discovering: a COMMAND's own re-render still reads at
``READ COMMITTED``, because it rides the transaction its writes are in.  That is
finding **N-358** and it has its own owner; closing it means the mutation routes
adopting :func:`write_transaction` so their render falls outside the command,
which moves the transaction boundary of every write door in the application and
is not this step's to take.

Non-request callers -- the CLI scripts, the deploy reconciles, Alembic -- never
reach the hook that records a mode, so :func:`_is_query_request` answers
``False`` for them and nothing about their transactions changes.  So does a
request CONTEXT that was never dispatched, which is not a pedantic distinction:
``pytest-flask`` pushes one around every test in this suite.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from flask import g, has_app_context, request
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.audit_infrastructure import bind_audit_actor
from app.extensions import db

# HTTP's safe methods, as this application serves them.  ``OPTIONS`` is left
# out deliberately rather than overlooked: Flask answers it from the URL map
# without dispatching a view, so it issues no statement for a mode to bind.
_QUERY_METHODS = frozenset({"GET", "HEAD"})

# Where the DISPATCH records what it decided this request is.  The mode is
# decided ONCE, by the before-request hook, and read from here afterwards -- it
# is deliberately not re-derived from ``request.method`` at each transaction,
# for two reasons.  A derived value read in many places and stored in none is
# this arc's own root cause, and more concretely: a request CONTEXT is not a
# dispatched request.  ``pytest-flask`` pushes a synthetic
# ``app.test_request_context()`` around every test in this suite -- method
# ``GET``, no dispatch, no hooks -- so a rule that read the method would make
# every test BODY a query and refuse its fixtures.  A context nobody dispatched
# carries no mode, and no mode means command.
_MODE_KEY = "shekel_transaction_mode"
_QUERY = "query"
_COMMAND = "command"

# Where the dispatch records WHO is acting, for the same reason it records the
# mode: the audit triggers read a TRANSACTION-scoped setting
# (:func:`app.audit_infrastructure.bind_audit_actor`), and a request no longer
# runs in one transaction.
_ACTOR_KEY = "shekel_audit_actor"

# Issued between ``BEGIN`` and the statement that caused it.  One statement,
# both halves: the isolation level is what gives the pass one snapshot, and
# ``READ ONLY`` is what makes a write inside it the database's refusal.
_QUERY_TRANSACTION_SQL = (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
)

# PostgreSQL assigns a transaction id at a transaction's FIRST write and never
# before, so this answers "has this transaction written" exactly -- which is
# also "would ending it lose work".  The ``_if_assigned`` form is the one that
# does not assign one by asking.
_ASSIGNED_XID_SQL = "SELECT pg_current_xact_id_if_assigned()"


class UncommittedWriteAtRequestStart(RuntimeError):
    """A query request began on a transaction that had already written.

    Not a domain error and so not in :mod:`app.exceptions`: it reports a state
    the application cannot produce, because a request's session is its own.
    What it names in practice is a test that stages rows without committing
    them and then issues a request expecting to see them -- which no request can
    do, so the fixture is unfaithful before this module refuses it.

    Deliberately NOT handled in :mod:`app.error_handlers`.  A caught and
    rendered version of this would be the accommodation the step exists to
    remove; the honest disposition is the 500 an unreachable state deserves.
    """


def _is_query_request() -> bool:
    """Return whether the code running now belongs to a QUERY.

    It READS the mode the dispatch recorded rather than deciding one, so every
    transaction a request opens is bound the same way and the decision has a
    single site (:func:`register_transaction_boundary`).  Absent means command:
    a CLI script, a deploy reconcile, Alembic, and a request context nobody
    dispatched all take the transaction they have always had.

    Returns:
        ``True`` when the current transaction should be a one-snapshot,
        read-only one.
    """
    return has_app_context() and getattr(g, _MODE_KEY, None) == _QUERY


@event.listens_for(Session, "after_begin")
def _bind_transaction_mode(session, transaction, connection) -> None:
    """Bind the mode of every transaction this process opens.

    Registered on the ``Session`` CLASS at import, which is what makes it
    apply to every session in the process exactly once -- registering it inside
    ``create_app`` would add a listener per application built, and the test
    suite builds several.

    ``after_begin`` fires between ``BEGIN`` and the statement that caused it, so
    the ``SET`` lands where PostgreSQL requires it (before the transaction's
    first statement) no matter which module issued that statement.

    Args:
        session: The :class:`~sqlalchemy.orm.Session` beginning a transaction.
        transaction: Its ``SessionTransaction``.
        connection: The connection the transaction was begun on.
    """
    # Pylint: ``unused-argument`` -- ``session`` / ``transaction`` are part of
    # SQLAlchemy's ``after_begin`` signature; only the connection is needed.
    # pylint: disable=unused-argument
    if transaction.nested:
        # A SAVEPOINT, not a transaction: PostgreSQL refuses ``SET TRANSACTION``
        # once a statement has run (``25001``), and the actor is already bound
        # on the enclosing transaction.  ``after_begin`` fires for a nested
        # transaction too, which the first draft of this listener did not
        # allow for.
        return
    if not has_app_context():
        return
    # The isolation level goes FIRST and the ORDER is not stylistic:
    # ``SET TRANSACTION`` is refused once any statement has run in the
    # transaction (``25001``), and binding the actor IS a statement.
    if _is_query_request():
        connection.exec_driver_sql(_QUERY_TRANSACTION_SQL)
    actor = g.get(_ACTOR_KEY)
    if actor is not None:
        bind_audit_actor(connection, actor)


def register_transaction_boundary(app) -> None:
    """Give every query request a transaction of its OWN, opened AND closed.

    See the module docstring for why this exists and what it measured.  Two
    hooks rather than one, and the closing half is not symmetry for its own
    sake: a snapshot that outlives its request is a snapshot the NEXT caller
    inherits, and read-only is inherited with it.  Measured the first time the
    suite ran without it -- 7,009 errors, every one of them
    ``ReadOnlySqlTransaction`` raised in a test body that had merely issued a
    request earlier and then tried to write.  Under the test client the app
    context is shared, so the session survives the request; releasing the
    snapshot where the request ends is what makes the boundary a boundary
    rather than a starting gun.

    In production the opening hook is a no-op on every request -- nothing is
    open when one starts, so it returns before issuing a statement -- and the
    closing hook ends a transaction Flask-SQLAlchemy's own app-context teardown
    would otherwise end a moment later.  Neither is load-bearing there, and
    saying so is the point: what they buy is that the suite exercises in CI the
    guarantee production gets from its request lifecycle.

    Registered EARLY in ``create_app`` -- before the session-activity refresh,
    which reads ``current_user`` and so opens a transaction -- but the
    correctness does not rest on that order, only the cost does: a hook that ran
    first would find nothing open, and one that ran later finds an unwritten
    transaction and ends it.  Either way the request's first statement after
    this opens a transaction bound by :func:`_bind_transaction_mode`.

    Args:
        app: The Flask application to register the hooks on.
    """

    @app.before_request
    def _open_this_request_s_own_transaction() -> None:
        """Decide this request's mode, then give a query its own transaction.

        **The ONE site that reads ``request.method``**, which is why it records
        the answer on ``g`` rather than leaving every later reader to re-derive
        it -- a derived value read in many places and stored in none is this
        arc's own root cause.

        **The query mode is recorded LAST, after the refusal**, and the order
        is what makes the refusal's sentence true: a request that is turned
        away never gets a mode, so the teardown below ends nothing, and the
        uncommitted writes this hook declines to discard are still there when
        the caller looks.  Recording first and refusing second would have the
        boundary destroy them one hook later.

        Raises:
            UncommittedWriteAtRequestStart: A query request arrived on a
                transaction that has already written.
        """
        if request.method not in _QUERY_METHODS:
            setattr(g, _MODE_KEY, _COMMAND)
            return
        session = db.session()
        if session.in_transaction():
            # Asked through the CONNECTION rather than through
            # ``Session.execute``, which autoflushes: a probe that flushed the
            # session's pending state would perform the very write it is asking
            # about and then report it.  Measured at zero pending rows across
            # the suite, so it changes no answer today -- but a probe whose
            # correctness depends on that being true is the shape this step
            # exists to remove.  It also keeps the boundary off the ORM
            # entirely, which is the tier it operates below.
            connection = session.connection()
            if connection.exec_driver_sql(_ASSIGNED_XID_SQL).scalar() is not None:
                raise UncommittedWriteAtRequestStart(
                    f"{request.method} {request.path} began on a transaction "
                    f"that has already written, so it cannot be given the one "
                    f"snapshot a render is entitled to -- and the writes are "
                    f"not this boundary's to discard, so they are left where "
                    f"they are. No request can reach this state (a request's "
                    f"session is its own), so what it names is staged rows "
                    f"that were never committed; commit them, which is what "
                    f"the door under test does, and the request sees them."
                )
            session.rollback()
        setattr(g, _MODE_KEY, _QUERY)

    @app.teardown_request
    def _close_this_request_s_own_transaction(exc) -> None:
        """Release the query's snapshot and retire the request's mode.

        Rolls back rather than commits, and there is nothing to choose between
        them: the transaction is ``READ ONLY``, so it holds no work either way.

        Only a QUERY's transaction is ended here.  A command's belongs to its
        route -- which commits it, or does not and lets the app-context
        teardown roll it back -- and reaching into that from here would decide
        a mutation's outcome from a lifecycle hook.

        **The mode is retired either way**, and under the test client that is
        the load-bearing half: ``flask.g`` lives on the APP context, which the
        suite shares across a test and every request it issues, so a mode left
        behind would follow the request out and govern the test body's own
        transactions.

        Args:
            exc: The unhandled exception Flask is tearing down for, if any.
                Unused: the disposition is the same either way.
        """
        # Pylint: ``unused-argument`` -- ``exc`` is Flask's ``teardown_request``
        # signature; a read-only transaction is released identically whether
        # the request succeeded or raised.
        # pylint: disable=unused-argument
        if _is_query_request():
            db.session.rollback()
        if has_app_context():
            g.pop(_MODE_KEY, None)


@contextmanager
def write_transaction() -> Iterator[None]:
    """Run this block as a COMMAND inside a query request.

    The one way a render says it must write, and the reason it is a block
    rather than a flag is that the writes must be COMMITTED before the render
    resumes: the pass that follows then takes its snapshot of a database that
    already holds them.  That ordering is what ``/grid`` and ``/dashboard``
    already depended on informally -- their rolling top-up commits and the read
    pass is built after -- and what this makes structural.

    The block owns its transaction end to end: it ends the query's snapshot
    first (which has written nothing, so nothing is lost), commits on the way
    out, and rolls back if the body raises.  A caller that wants a failure to
    leave the render running must catch it inside the block; one that lets it
    escape gets the rollback and the exception.

    **Not available to a command request**, which is already writable end to
    end.  Making a command's own render fall outside its write is finding
    **N-358**'s work and wants its own trace; opening a nested transaction
    here would commit the route's staged writes early.

    Yields:
        ``None`` -- the block writes through ``db.session`` as any command does.

    Raises:
        RuntimeError: Used outside a query request, or nested inside another
            :func:`write_transaction` block.
    """
    mode = getattr(g, _MODE_KEY, None) if has_app_context() else None
    if mode is None:
        raise RuntimeError(
            "write_transaction() is for a QUERY request that must write, and "
            "no dispatched request is running here. Outside one there is "
            "nothing to leave and return to: a CLI script, a deploy reconcile "
            "and a migration all hold the writable transaction they always "
            "had, and had no snapshot taken from them."
        )
    if mode == _COMMAND:
        raise RuntimeError(
            "write_transaction() found a COMMAND transaction already running. "
            "Either this is a mutation route, which is writable end to end "
            "and needs no block (making a command's own re-render fall "
            "outside its write is finding N-358's work), or a block is "
            "already open on this request and nesting one would commit the "
            "outer block's staged writes at the inner block's end."
        )
    session = db.session
    # End the snapshot before the mode flips, so the transaction this opens is
    # bound as a command.  It has written nothing -- READ ONLY guaranteed that
    # -- so the rollback discards no work; what it does discard is the identity
    # map's loaded state, which reloads inside the command.
    session.rollback()
    setattr(g, _MODE_KEY, _COMMAND)
    committed = False
    try:
        yield
        session.commit()
        committed = True
    finally:
        # The mode is restored BEFORE the rollback, not after: a rollback can
        # itself raise (a dropped connection, a failover), and a mode left on
        # COMMAND would leave the rest of the render writable and
        # un-snapshotted, with the teardown declining to end its transaction.
        setattr(g, _MODE_KEY, _QUERY)
        if not committed:
            session.rollback()
