"""Architecture test: what KIND of transaction a request runs in.

Plan step **balance:X-i3**, finding **N-353**.  :mod:`app.db_transaction` makes
one rule structural: a GET or HEAD runs at ``REPEATABLE READ, READ ONLY`` -- one
snapshot of the database for every statement it issues, and no write possible
inside it -- and every other method runs at PostgreSQL's default ``READ
COMMITTED``, writable, exactly as before.

Why the read half matters
-------------------------

Under ``READ COMMITTED`` *each statement gets its own snapshot*.  A render
assembles one screen from dozens of statements, so it could read the owner's
pay calendar in one and the rows filed against it in another, and the two could
disagree -- which is exactly what ``/grid``'s rolling top-up produces for a
concurrent render: it appends a pay period, repopulates it from every active
template, and commits mid-render.

Why the WRITE half matters at least as much
-------------------------------------------

**A command may not be given one snapshot, and this file is where that is
pinned.**  The posting-ledger reconciles are read-modify-write under an
advisory lock (:func:`app.services.user_write_lock.lock_user_writes`): take the
lock, re-read what is posted, write the difference.  The waiter's re-read is
REQUIRED to see the winner's just-committed postings, and only ``READ
COMMITTED`` shows them.  Under one snapshot the waiter re-reads its own
pre-lock picture and reconciles to a stale target -- the divergence ruling
**R-EN** measured at ``$4,000.00`` asserted against ``$1,000.00`` settled, with
the trial balance still ``$0.00`` because the anchor-equity leg mirrors the
error, so nothing fails loudly.

So "make everything REPEATABLE READ, it is strictly safer" is a plausible,
wrong change, and :func:`test_a_command_stays_read_committed` is the control
that refuses it.

Every test here is BEHAVIOURAL
------------------------------

Nothing below asserts that a particular function was called or that a module
carries a particular line.  Each asks the database what mode it is actually in,
or drives a real request and asks what that request could see.  The snapshot
test carries its own planted defect (:func:`test_the_snapshot_control_fires`),
because a test that would pass with the guarantee removed proves nothing.
"""

from datetime import date, timedelta

import psycopg2
import pytest
from flask import g
from sqlalchemy import event, text
from sqlalchemy.exc import InternalError
from sqlalchemy.orm import Session

from app import db_transaction
from app.extensions import db
from app.models.pay_period import PayPeriod


def _record_len(calendar, sink):
    """Append *calendar*'s payday count to *sink* and return it unchanged."""
    sink.append(len(calendar.periods))
    return calendar


@pytest.fixture()
def observed_modes(app):
    """Record the isolation mode of every transaction opened while active.

    Registered AFTER :mod:`app.db_transaction`'s own ``after_begin`` listener,
    which is installed at module import -- SQLAlchemy fires listeners in
    registration order, so by the time this one runs the ``SET TRANSACTION`` has
    already been issued and what it reads is the mode as bound.

    Yields:
        The list of ``(isolation, read_only)`` pairs, appended to as the test
        runs.
    """
    seen = []

    def _record(session, transaction, connection):  # noqa: ARG001
        seen.append(
            connection.exec_driver_sql(
                "select current_setting('transaction_isolation'), "
                "current_setting('transaction_read_only')"
            ).one()
        )

    event.listen(Session, "after_begin", _record)
    try:
        yield seen
    finally:
        event.remove(Session, "after_begin", _record)


class TestTheModeIsBoundByTheDispatch:
    """A dispatched request's method decides its transaction, and nothing else."""

    def test_a_query_runs_read_only_at_repeatable_read(
        self, auth_client, seed_user, observed_modes,
    ):
        """A GET's transactions are one snapshot each, and cannot write."""
        assert auth_client.get("/settings").status_code == 200

        during = [pair for pair in observed_modes if pair[1] == "on"]
        assert during, "no read-only transaction was opened during the GET"
        assert all(
            isolation == "repeatable read" for isolation, _ in during
        ), observed_modes

    def test_a_command_stays_read_committed(
        self, auth_client, seed_user, observed_modes,
    ):
        """A POST keeps the default, and this is the money-critical half.

        ``user_write_lock``'s lock-then-reread is only correct at ``READ
        COMMITTED``: the waiting transaction must see the winner's committed
        postings.  If a future change binds one snapshot here, the reconciles
        silently double-count.  See this module's docstring for what that was
        measured at.
        """
        observed_modes.clear()
        auth_client.post("/settings", data={"grid_default_periods": "7"})

        assert observed_modes, "the POST opened no transaction"
        assert all(
            pair == ("read committed", "off") for pair in observed_modes
        ), observed_modes

    def test_a_caller_with_no_dispatched_request_is_untouched(
        self, app, db, seed_user, observed_modes,
    ):
        """A CLI script, a deploy reconcile and Alembic keep what they had.

        ``pytest-flask`` pushes a synthetic ``test_request_context`` around
        every test in this suite -- method ``GET``, never dispatched -- so this
        also pins that a request CONTEXT is not a request: were the mode
        derived from ``request.method`` rather than recorded by the dispatch,
        this test body itself would be read-only and could not write.
        """
        db.session.rollback()
        db.session.query(PayPeriod).filter_by(user_id=seed_user["user"].id).count()

        assert observed_modes == [("read committed", "off")]


class TestAQueryIsOneSnapshot:
    """The property finding N-353 records, and the defect that proves it fires."""

    @staticmethod
    def _append_a_payday_from_another_connection(app, user_id, start_date):
        """Commit one pay period on a SEPARATE connection, as a rival would.

        A second connection is the whole point: the row must be committed by
        someone else while the request is in flight, which is what the rolling
        top-up on a concurrent ``/grid`` render does.

        Args:
            app: The Flask application, for its database URI.
            user_id: The owner to append the payday for.
            start_date: The payday.
        """
        rival = psycopg2.connect(app.config["SQLALCHEMY_DATABASE_URI"])
        try:
            rival.autocommit = True
            with rival.cursor() as cur:
                cur.execute(
                    "insert into budget.pay_periods "
                    "(user_id, start_date, end_date, period_index) "
                    "values (%s, %s, %s, %s)",
                    (user_id, start_date, start_date + timedelta(days=13), 900),
                )
        finally:
            rival.close()

    @pytest.fixture()
    def calendars_the_render_saw(self, app, seed_user, monkeypatch):
        """Interleave a rival's committed payday, and record what the pass saw.

        The rival commits from ``before_cursor_execute`` on the FIRST statement
        that touches ``budget.pay_periods`` -- before that statement runs, so a
        ``READ COMMITTED`` reader sees the new row in that very statement and a
        snapshot reader cannot.  No statement counting, so nothing here breaks
        when a route's query plan changes.

        Yields:
            ``(counts, armed)`` -- the payday counts each ``calendar_for`` call
            returned, and the one-shot arming flag, which a test asserts is
            SPENT so a render that never read ``budget.pay_periods`` cannot
            pass vacuously.
        """
        counts = []
        armed = [True]

        # Patched where the read pass BINDS it, not where it is defined: the
        # seam imported the name at module load, so patching the package would
        # leave the pass calling the original.
        import app.services.balance_at._context as seam  # pylint: disable=import-outside-toplevel

        real_calendar_for = seam.calendar_for

        def _recording_calendar_for(user_id):
            calendar = real_calendar_for(user_id)
            counts.append(len(calendar.periods))
            return calendar

        monkeypatch.setattr(seam, "calendar_for", _recording_calendar_for)

        def _interleave(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
            if not armed[0] or "pay_periods" not in statement:
                return
            armed[0] = False
            self._append_a_payday_from_another_connection(
                app, seed_user["user"].id, date(2099, 1, 1),
            )

        event.listen(db.engine, "before_cursor_execute", _interleave)
        try:
            yield counts, armed
        finally:
            event.remove(db.engine, "before_cursor_execute", _interleave)

    def test_a_render_cannot_see_a_payday_committed_under_it(
        self, auth_client, seed_user, seed_periods, calendars_the_render_saw,
    ):
        """N-353's exact shape, made unconstructible.

        The rival's payday commits while the render is running.  Every calendar
        the pass derives is taken from ONE snapshot, so none of them holds it --
        which is what keeps a row from being valued against a calendar older
        than it is.
        """
        calendars, armed = calendars_the_render_saw
        before = len(seed_periods)

        assert auth_client.get("/grid").status_code == 200

        assert calendars, "the render derived no calendar"
        assert not armed[0], (
            "the rival never committed its payday -- no statement naming "
            "budget.pay_periods ran, so this test proves nothing about "
            "snapshots"
        )
        assert all(seen == before for seen in calendars), (
            f"the render saw {calendars} paydays against {before} at its "
            f"snapshot; a concurrent append reached it"
        )

    def test_the_snapshot_control_fires(
        self, auth_client, seed_user, seed_periods, calendars_the_render_saw,
        monkeypatch,
    ):
        """The SAME render, with the guarantee removed, DOES see the append.

        The planted defect is the smallest one that removes THIS rule and
        nothing else: the request is still classified as a query, so
        ``write_transaction`` still works and ``/grid`` still renders -- only
        the isolation the query binds is put back to the default every render
        had before this step.  A defect that also broke the classification
        would prove the test notices *something*, not that it notices this.

        Without this control the test above would pass just as happily against
        a boundary that had quietly stopped binding anything.
        """
        monkeypatch.setattr(
            db_transaction,
            "_QUERY_TRANSACTION_SQL",
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
        )
        calendars, armed = calendars_the_render_saw
        before = len(seed_periods)

        assert auth_client.get("/grid").status_code == 200

        assert calendars, "the render derived no calendar"
        assert not armed[0], "the rival never committed its payday"
        assert any(seen > before for seen in calendars), (
            "READ COMMITTED should have shown the rival's payday to at least "
            f"one calendar; saw {calendars} against {before}"
        )


class TestAQueryMayNotWrite:
    """``READ ONLY`` is what makes the rule the database's rather than a habit."""

    def test_a_write_inside_a_render_is_refused_by_postgresql(
        self, app, auth_client, seed_user,
    ):
        """Not a checker, not a review note: an error from the server.

        Driven through a real dispatched GET so the refusal is the one a
        render would actually meet.  ``/health`` is used because it is the
        cheapest GET in the application and its own body is irrelevant here --
        the write is attempted from a listener, standing in for any producer
        that decided to repair something mid-render.
        """
        attempted = []

        def _write_during_the_render(session, transaction, connection):  # noqa: ARG001
            if not db_transaction._is_query_request():  # pylint: disable=protected-access
                return
            # Inside a SAVEPOINT: PostgreSQL aborts a transaction on ANY error,
            # so an unguarded refusal here would poison the request rather than
            # let it finish, and the test would be measuring the poisoning.
            connection.exec_driver_sql("SAVEPOINT the_render_tried_to_write")
            try:
                connection.exec_driver_sql(
                    "insert into budget.pay_periods "
                    "(user_id, start_date, end_date, period_index) "
                    "values (1, '2099-06-01', '2099-06-14', 901)"
                )
                attempted.append("allowed")
            except InternalError as exc:
                attempted.append(type(exc.orig).__name__)
            connection.exec_driver_sql(
                "ROLLBACK TO SAVEPOINT the_render_tried_to_write"
            )

        event.listen(Session, "after_begin", _write_during_the_render)
        try:
            auth_client.get("/health")
        finally:
            event.remove(Session, "after_begin", _write_during_the_render)

        assert attempted, "no query transaction was opened during the GET"
        assert set(attempted) == {"ReadOnlySqlTransaction"}, attempted


class TestWriteTransaction:
    """The one door a render has when it must write, and its two refusals."""

    def test_the_rolling_top_up_still_lands_and_the_render_sees_it(
        self, app, auth_client, seed_user, seed_periods, db, monkeypatch,
    ):
        """``/grid``'s top-up commits, and the pass snapshots AFTER it.

        The behavioural proof that ``write_transaction`` does what the route
        needs: a schedule short of its rolling target is extended by the render
        that finds it, from INSIDE a read-only request, and the periods are
        still there once that request has gone.
        """
        from app.models.pay_schedule import PaySchedule  # pylint: disable=import-outside-toplevel

        target = len(seed_periods) + 4
        schedule = db.session.query(PaySchedule).filter_by(
            user_id=seed_user["user"].id,
        ).one_or_none()
        if schedule is None:
            schedule = PaySchedule(
                user_id=seed_user["user"].id, cadence_days=14,
            )
            db.session.add(schedule)
        schedule.rolling_enabled = True
        schedule.rolling_target_periods = target
        db.session.commit()

        seen = []
        import app.services.balance_at._context as seam  # pylint: disable=import-outside-toplevel

        real_calendar_for = seam.calendar_for
        monkeypatch.setattr(seam, "calendar_for", lambda uid: _record_len(
            real_calendar_for(uid), seen,
        ))

        assert auth_client.get("/grid").status_code == 200

        db.session.rollback()
        assert db.session.query(PayPeriod).filter_by(
            user_id=seed_user["user"].id,
        ).count() >= target, "the top-up did not commit inside the render"
        # And the RENDER saw them, which is the half the block exists for: the
        # pass snapshots AFTER the command commits, not before it.
        assert seen and min(seen) >= target, (
            f"the read pass derived {seen} paydays after a top-up that left "
            f"at least {target} committed; its snapshot predates the write"
        )

    def test_a_write_it_commits_still_names_the_ACTOR_in_the_audit_log(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The regression this block introduced, and the reason it is here.

        The audit triggers attribute a row through a TRANSACTION-scoped
        setting (:func:`app.audit_infrastructure.bind_audit_actor`).  A request
        used to run in ONE transaction, so one before-request hook could bind
        it once; this step gave a render its own snapshot and a render's
        declared write a command transaction of its own, and the first draft
        bound neither -- so every pay period the rolling top-up appended landed
        in ``system.audit_log`` with a NULL user, silently, because the
        trigger reads an unset setting as "no authenticated user".

        Found by adversarial review, twice, independently.
        """
        from app.models.pay_schedule import PaySchedule  # pylint: disable=import-outside-toplevel

        target = len(seed_periods) + 4
        schedule = db.session.query(PaySchedule).filter_by(
            user_id=seed_user["user"].id,
        ).one_or_none()
        if schedule is None:
            schedule = PaySchedule(user_id=seed_user["user"].id, cadence_days=14)
            db.session.add(schedule)
        schedule.rolling_enabled = True
        schedule.rolling_target_periods = target
        db.session.commit()
        db.session.execute(text("DELETE FROM system.audit_log"))
        db.session.commit()

        assert auth_client.get("/grid").status_code == 200

        db.session.rollback()
        actors = db.session.execute(text(
            "select distinct user_id from system.audit_log "
            "where table_name = 'pay_periods'"
        )).scalars().all()
        assert actors, "the top-up wrote no audited pay-period rows"
        assert actors == [seed_user["user"].id], (
            f"the rolling top-up's rows were attributed to {actors}; a NULL "
            f"here is the audit trail losing the acting user on every write a "
            f"render commits"
        )

    def test_it_refuses_a_caller_with_no_dispatched_request(self, app):
        """Outside a request there is no snapshot to leave and return to."""
        with app.app_context():
            with pytest.raises(RuntimeError, match="no dispatched request"):
                with db_transaction.write_transaction():
                    pass

    def test_it_refuses_a_COMMAND_transaction(self, app):
        """The second refusal: a mutation route is writable end to end.

        Covers both spellings of the same state -- a POST, and a block already
        open on a query request -- because the mode is what distinguishes them
        and by then it is the same value.
        """
        with app.test_request_context("/settings", method="POST"):
            setattr(g, db_transaction._MODE_KEY, db_transaction._COMMAND)  # pylint: disable=protected-access
            with pytest.raises(RuntimeError, match="COMMAND transaction"):
                with db_transaction.write_transaction():
                    pass

    def test_a_body_that_raises_leaves_nothing_behind(
        self, app, auth_client, seed_user, db,
    ):
        """The block rolls back on the way out, and hands the mode back.

        Both halves matter: a render whose declared write fails must commit
        none of it, and the render that continues must still be a query -- if
        the mode were left on COMMAND the rest of the page would run writable
        and un-snapshotted.
        """
        with app.test_request_context("/grid"):
            setattr(g, db_transaction._MODE_KEY, db_transaction._QUERY)  # pylint: disable=protected-access
            with pytest.raises(ZeroDivisionError):
                with db_transaction.write_transaction():
                    db.session.add(PayPeriod(
                        user_id=seed_user["user"].id,
                        start_date=date(2099, 9, 1),
                        end_date=date(2099, 9, 14),
                        period_index=903,
                    ))
                    db.session.flush()
                    raise ZeroDivisionError("the render's write failed")

            assert db_transaction._is_query_request()  # pylint: disable=protected-access

        db.session.rollback()
        assert db.session.query(PayPeriod).filter_by(
            user_id=seed_user["user"].id, period_index=903,
        ).one_or_none() is None


class TestARequestArrivingOnAWrittenTransaction:
    """The precondition, and why it is a precondition rather than a fence."""

    def test_it_refuses_rather_than_discarding_the_writes(
        self, app, auth_client, seed_user, db,
    ):
        """A query cannot be given a snapshot over someone's uncommitted work.

        Unreachable in production -- a request's session is its own -- so what
        it names is a fixture that staged rows and then issued a request
        expecting to see them, which no request can do.  Refusing beats
        discarding: the writes are not this boundary's to throw away.
        """
        db.session.add(PayPeriod(
            user_id=seed_user["user"].id,
            start_date=date(2099, 3, 1),
            end_date=date(2099, 3, 14),
            period_index=902,
        ))
        db.session.flush()

        with pytest.raises(db_transaction.UncommittedWriteAtRequestStart):
            auth_client.get("/settings")

        # And it left them alone.  The refusal says the writes are not this
        # boundary's to discard, so the teardown must not discard them one hook
        # later -- which is why the mode is recorded only AFTER the refusal.
        assert db.session.query(PayPeriod).filter_by(
            user_id=seed_user["user"].id, period_index=902,
        ).one_or_none() is not None

    def test_an_unwritten_inherited_transaction_is_simply_ended(
        self, app, auth_client, seed_user, db, observed_modes,
    ):
        """Reading before a request costs nothing; only WRITING is refused.

        The distinction is the whole reason the boundary asks PostgreSQL for
        an assigned transaction id rather than asking whether a transaction is
        open: a read-only transaction holds no work, so ending it loses none.

        **It asserts the MODE, not just a 200.**  An adversarial review caught
        the first version doing the latter: delete the boundary's rollback and
        the request runs inside the inherited transaction with no snapshot and
        no read-only guarantee, and a 200 is still a 200.
        """
        db.session.query(PayPeriod).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        observed_modes.clear()

        assert auth_client.get("/settings").status_code == 200

        assert ("repeatable read", "on") in observed_modes, (
            f"the request did not open a transaction of its own; it inherited "
            f"the one this test body opened. Modes seen: {observed_modes}"
        )
