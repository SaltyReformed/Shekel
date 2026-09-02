"""``fk_pay_periods_schedule``: an owner with paydays HAS a recorded cadence.

Plan step **pay_calendar:C4-b-2**, migration ``f1c8b3d5e920``, closing ledger
rows **P8** and **P35**.

**What the key replaced.**  ``budget.pay_periods`` and ``budget.pay_schedule``
are two halves of one fact and nothing in the schema held them together, so
"paydays on file, no cadence row beside them" was storable.
``pay_schedule_service.resolve_schedule`` therefore carried an arm that
inferred the cadence from the last period's stored length, and that inference
was wrong twice over: CIRCULAR, because since plan step C3-b
``pay_period_write.record_paydays`` derives that same end FROM the cadence, so
inverting it read back the value that produced it; and unbounded ABOVE, where
``ck_pay_schedule_cadence_range`` bounds a stored cadence to 1..365 -- a
period spanning more than a year inferred a cadence the calendar refuses, and
since plan step C2-c that refusal reached every balance page as a bare 500.

**Why the constraint is graded HERE and in one place.**  Every reader that
used to cope with the forbidden owner had a case describing it, and the arc's
own history is what argues for consolidating: those cases each restated the
same premise in their own words, and when the premise changed they went stale
independently.  One file states the rule; the readers state what they read.

**The user delete is DRIVEN, not argued from the DDL**, which is what the
step's specification asked for.  ``budget.pay_periods.user_id`` and
``budget.pay_schedule.user_id`` both cascade from ``auth.users``, and this key
sits between them, so the question "can an owner still be deleted" is about
referential-trigger ORDER -- something no reading of the schema settles.
"""
from __future__ import annotations

import importlib.util
import pathlib
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from alembic import op
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, InternalError

from app.models.pay_period import PayPeriod
from app.models.pay_schedule import PaySchedule
from app.models.user import User, UserSettings
from app.services import pay_schedule_service
from app.services.auth_service import hash_password
from tests._test_helpers import (
    open_owner_calendar,
    restore_pay_period_derived_columns,
)

_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)


def _load_migration(filename: str):
    """Load an Alembic migration file as a module (no ``__init__.py`` there).

    The same loader ``test_rf16_paycheck_count_migration`` uses, which is this
    repository's established pattern for driving a migration's own callables
    from a test.
    """
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: The key's own revision, loaded so its shipped callables are what this file
#: drives.  Hand-written DDL standing in for them would be a second statement
#: of the migration that could drift from it without failing anything.
_M_C4B2 = _load_migration(
    "f1c8b3d5e920_an_owner_with_paydays_has_a_cadence.py"
)

#: The revision whose ``downgrade()`` meets this key: it drops
#: ``budget.pay_schedule`` outright while ``budget.pay_periods`` survives.
_M_PHASE1 = _load_migration(
    "af8254074bef_phase1_create_budget_pay_schedule_.py"
)

#: The key's own name, spelled once.  Every assertion below reads the DATABASE
#: for it rather than trusting the model, because the model and the migration
#: are two statements of one constraint and the point is that they agree.
_KEY = "fk_pay_periods_schedule"


def _run(callable_, session):
    """Run a migration callable against the test connection, then commit.

    The pattern ``test_rf16_paycheck_count_migration`` established for driving
    a migration's own functions from a test.  No restore is needed after it:
    the ``db`` fixture drops and re-clones the per-worker database for every
    test, so a schema this leaves short of head cannot reach the next one.
    """
    ctx = MigrationContext.configure(connection=session.connection())
    with Operations.context(ctx):
        with patch.object(op, "get_bind", return_value=session.connection()):
            callable_()
    session.commit()


def _key_row(session):
    """Return ``(name, confdeltype)`` for the key, or ``None`` when absent.

    ``confdeltype`` is PostgreSQL's own single-letter ``ON DELETE`` code, read
    out of ``pg_constraint`` rather than parsed out of a definition string:
    ``'r'`` is RESTRICT, ``'c'`` CASCADE, ``'a'`` NO ACTION, ``'n'`` SET NULL.
    """
    return session.execute(text(
        "SELECT conname, confdeltype::text FROM pg_constraint "
        "WHERE conname = :name"
    ), {"name": _KEY}).fetchone()


def _owner_with_paydays(db, email, cadence_days=14, num_periods=3):
    """A committed owner holding paydays, built through the writing door.

    ``open_owner_calendar`` reaches ``pay_period_write.record_paydays``, which
    upserts the ``budget.pay_schedule`` row in the same call (the cadence rule,
    plan step C3-b).  Building the owner by hand is what plan step C4-b-1
    removed from this suite, and it is exactly the shape this key forbids.
    """
    user = User(
        email=email,
        password_hash=hash_password("keypass-123456"),
        display_name="Key Owner",
    )
    db.session.add(user)
    db.session.flush()
    db.session.add(UserSettings(user_id=user.id))
    open_owner_calendar(
        user.id, date(2026, 3, 2),
        num_periods=num_periods, cadence_days=cadence_days,
    )
    db.session.commit()
    return user


class TestTheKeyIsShapedTheWayTheRulingSays:
    """The DDL itself, read from the catalog rather than from the model."""

    def test_the_key_exists_and_is_ON_DELETE_RESTRICT(self, app, db):
        """Ruling **R-PC41**, asserted against ``pg_constraint``.

        The action is the whole of what the developer ruled on 2026-09-01, and
        it is one character in the catalog.  A later migration that recreated
        this key as CASCADE would leave every other case in this file passing
        -- an insert is refused either way, and the delete cases below would
        report a cascade as a success -- so the code is read directly.

        ``'r'`` is RESTRICT.  CASCADE (``'c'``) was the plan's draft and was
        measured to destroy 63 pay periods, 1,057 transactions and every
        journal entry on a clone of the developer's database.
        """
        with app.app_context():
            row = _key_row(db.session)

            assert row is not None, (
                f"{_KEY} is missing: migration f1c8b3d5e920 did not run, or a "
                f"later revision dropped it."
            )
            assert row[1] == "r", (
                f"{_KEY} is ON DELETE {row[1]!r}, not 'r' (RESTRICT).  That is "
                f"ruling pay_calendar:R-PC41 and it is not an inherited "
                f"default: 'c' would make deleting one settings row destroy "
                f"every pay period, transaction and journal entry the owner "
                f"has."
            )

    def test_the_key_targets_the_schedules_own_user_id(self, app, db):
        """It is a CO-LOCATED key, not a new pointer column.

        ``uq_pay_schedule_user`` is what makes ``budget.pay_schedule.user_id``
        a legal foreign-key target, and targeting it is why this constraint
        needs no ``schedule_id`` column and stores nothing that could drift
        from the owner it already records.  The same construction
        ``fk_statement_matches_owner`` uses.
        """
        with app.app_context():
            definition = db.session.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = :name"
            ), {"name": _KEY}).scalar()

            assert definition == (
                "FOREIGN KEY (user_id) REFERENCES "
                "budget.pay_schedule(user_id) ON DELETE RESTRICT"
            )

    def test_the_column_still_carries_its_auth_users_key(self, app, db):
        """Two keys on one column, and the second is not redundancy.

        ``pay_periods_user_id_fkey`` is ``ON DELETE CASCADE`` from
        ``auth.users``; it is what lets a user delete clear this table at all.
        Without it the RESTRICT key above would REFUSE that delete, because
        the periods would still reference a schedule row the user's own
        cascade was removing.  Deleting it as "implied by the transitive
        chain" is the specific mistake this case exists to fail on.
        """
        with app.app_context():
            rows = db.session.execute(text(
                "SELECT c.conname, c.confdeltype::text "
                "  FROM pg_constraint c "
                "  JOIN pg_class t ON t.oid = c.conrelid "
                "  JOIN pg_namespace n ON n.oid = t.relnamespace "
                " WHERE c.contype = 'f' AND n.nspname = 'budget' "
                "   AND t.relname = 'pay_periods' "
                " ORDER BY c.conname"
            )).fetchall()

            by_name = {name: action for name, action in rows}

            assert by_name["pay_periods_user_id_fkey"] == "c"
            assert by_name[_KEY] == "r"


class TestTheForbiddenOwnerIsUnstorable:
    """P8's state -- paydays with no cadence row -- cannot be written."""

    def test_a_pay_period_needs_its_owners_schedule_row(self, app, db):
        """The INSERT direction: no schedule row, no payday.

        This is the state 23 hand-built ``PayPeriod(...)`` sites across 18
        files used to construct before plan step C4-b-1 converted them, and
        that no application door has ever produced -- ``record_paydays``
        upserts the cadence in the same call.  It is now the database's rule
        rather than the writer's.
        """
        with app.app_context():
            user = User(
                email="noschedule@shekel.local",
                password_hash=hash_password("keypass-123456"),
                display_name="No Schedule",
            )
            db.session.add(user)
            db.session.flush()

            db.session.add(PayPeriod(
                user_id=user.id,
                start_date=date(2026, 3, 2),
            ))

            with pytest.raises(IntegrityError) as excinfo:
                db.session.flush()

            assert _KEY in str(excinfo.value)

    def test_the_schedule_row_cannot_be_deleted_under_live_paydays(
        self, app, db,
    ):
        """The DELETE direction, which is what RESTRICT decides.

        Nothing in ``app/`` performs this delete -- the three
        ``query(PaySchedule)`` sites are all SELECTs -- so what is graded is
        the refusal a bug, a hand-run statement or a future door would meet.
        """
        user = _owner_with_paydays(db, "restrict@shekel.local")

        with app.app_context():
            with pytest.raises(IntegrityError) as excinfo:
                db.session.query(PaySchedule).filter_by(
                    user_id=user.id,
                ).delete(synchronize_session=False)
                db.session.flush()

            assert _KEY in str(excinfo.value)
            db.session.rollback()

            # The refusal changed nothing, which is the half a raised
            # exception does not by itself establish.
            assert pay_schedule_service.get_schedule(user.id) is not None
            assert db.session.query(PayPeriod).filter_by(
                user_id=user.id,
            ).count() == 3

    def test_the_ORDER_is_the_whole_difference(self, app, db):
        """The same two statements, two orders, two outcomes.

        Children-then-parent succeeds; parent-then-children is refused.  Both
        halves run against ONE owner in one case on purpose: a case that only
        showed the legal order succeeding would pass under ``CASCADE`` and pass
        with the key dropped entirely -- an adversarial review measured exactly
        that of the version this replaces -- because "the delete worked" is not
        a fact the key decides.  What it decides is which ORDER works.

        This is also the order every helper in the suite now uses, and the one
        ``_strip_every_payday`` was reversed into.
        """
        user = _owner_with_paydays(db, "bothgo@shekel.local")

        with app.app_context():
            # Parent first: refused, and nothing moves.
            with pytest.raises(IntegrityError) as excinfo:
                db.session.query(PaySchedule).filter_by(
                    user_id=user.id,
                ).delete(synchronize_session=False)
                db.session.flush()
            assert _KEY in str(excinfo.value)
            db.session.rollback()
            assert db.session.query(PayPeriod).filter_by(
                user_id=user.id,
            ).count() == 3

            # Children first, then the same parent statement: allowed.
            db.session.query(PayPeriod).filter_by(
                user_id=user.id,
            ).delete(synchronize_session=False)
            db.session.query(PaySchedule).filter_by(
                user_id=user.id,
            ).delete(synchronize_session=False)
            db.session.commit()

            assert pay_schedule_service.resolve_cadence(user.id) is None

    def test_a_schedule_row_without_paydays_stays_legal(self, app, db):
        """The key binds one direction only, and this is the other one.

        An owner may hold a cadence and no periods: ``reset_pay_periods``
        deletes every period and keeps the row, and that is the state it
        passes through.  A key that forbade it would break the repair door.

        **The surviving row is then shown to ADMIT a fresh period**, so the
        case says something about the key rather than only about a delete and
        a read.

        *What it does NOT do is die under the two mutations the rest of this
        file is held to* -- recreating the key as ``CASCADE``, or dropping it
        -- and that is inherent rather than a weakness to fix: a case whose
        subject is a state being PERMITTED cannot fail when the permitting
        constraint is absent.  The mutation it does catch is the one worth
        catching here: any later constraint or trigger that made the key a
        BICONDITIONAL, requiring a schedule row to have periods, breaks both
        halves of this case and breaks ``reset_pay_periods`` with them.
        """
        user = _owner_with_paydays(db, "scheduleonly@shekel.local")

        with app.app_context():
            db.session.query(PayPeriod).filter_by(
                user_id=user.id,
            ).delete(synchronize_session=False)
            db.session.commit()

            assert pay_schedule_service.resolve_cadence(user.id) == 14

            db.session.add(PayPeriod(
                user_id=user.id,
                start_date=date(2027, 1, 1),
            ))
            db.session.flush()

            assert db.session.query(PayPeriod).filter_by(
                user_id=user.id,
            ).count() == 1


class TestTheDoubleCascadeIsDrivenRatherThanArgued:
    """Deleting an owner still works, with the key sitting between two keys."""

    def test_deleting_the_user_removes_both_tables_rows(self, app, db):
        """The specification's own requirement: drive a real user delete.

        ``pay_periods.user_id`` and ``pay_schedule.user_id`` each cascade from
        ``auth.users``, and this key joins them, so whether the delete
        succeeds depends on the order PostgreSQL fires those referential
        triggers in -- which no reading of the DDL answers.  Before the ruling
        the same delete was driven under all four candidate actions and in
        both trigger orderings (``pay_periods_user_id_fkey`` recreated to flip
        which fires last); all eight combinations succeeded, which is what
        showed the plan's stated reason for CASCADE was never a reason for it.

        This case pins the arm that shipped.
        """
        user = _owner_with_paydays(db, "cascade@shekel.local")

        with app.app_context():
            user_id = user.id
            db.session.execute(
                text("DELETE FROM auth.users WHERE id = :uid"),
                {"uid": user_id},
            )
            db.session.commit()

            assert db.session.query(PayPeriod).filter_by(
                user_id=user_id,
            ).count() == 0
            assert db.session.query(PaySchedule).filter_by(
                user_id=user_id,
            ).count() == 0


class TestTheRevisionRoundTripsAndTheChainOrderHolds:
    """``downgrade`` then ``upgrade``, and what the downgrade unblocks.

    **Every case here rewinds past plan step ``pay_calendar:C4-c`` first**, and
    that is not setup noise -- it is this revision's own backfill needing the
    schema it was written against.  ``_BACKFILL_CADENCE_SQL`` reads
    ``budget.pay_periods.end_date`` for the single-payday fallback, and C4-c
    dropped that column, so driving ``upgrade()`` at HEAD meets
    ``UndefinedColumn`` rather than the state under test.
    :func:`~tests._test_helpers.restore_pay_period_derived_columns` runs C4-c's
    own ``downgrade()`` to put the two columns back, with the values that
    statement rebuilds; it does not claim to place the database at this
    revision, and its docstring says which claim it does not make (ledger row
    **P79**).
    """

    def test_down_then_up_restores_the_key_unchanged(self, app, db):
        """The pair is re-runnable, and the action survives the round trip.

        The ``upgrade`` half re-runs the backfill against a database that
        still holds every schedule row, so ``ON CONFLICT DO NOTHING`` has to
        make it inert -- a backfill that inserted here would be writing a
        second row per owner into a table ``uq_pay_schedule_user`` allows one
        of.
        """
        user = _owner_with_paydays(db, "roundtrip@shekel.local")

        with app.app_context():
            restore_pay_period_derived_columns(db.session)
            _run(_M_C4B2.downgrade, db.session)
            assert _key_row(db.session) is None

            _run(_M_C4B2.upgrade, db.session)

            assert _key_row(db.session) == (_KEY, "r")
            assert db.session.query(PaySchedule).filter_by(
                user_id=user.id,
            ).count() == 1

    def test_the_backfill_reads_the_PAYDAYS_not_the_stored_span(
        self, app, db,
    ):
        """The two derivations are made to DISAGREE, and the paydays win.

        This is the case the migration's own argument turns on, and no case
        constructed it until an adversarial design review asked for one.  The
        owner's three paydays are nine days apart -- so the record says nine --
        while the last row's stored ``end_date`` is edited to span 29 days, the
        shape ledger row **P28** describes: a stored end written at a cadence
        the paydays were not spaced at.

        ``(end_date - start_date) + 1`` answers **29**, which is what the
        DELETED arm answered and what an earlier draft of this migration froze
        into the column.  At 29, ``round(365.2425 / 29)`` is 13 paychecks a
        year against a true 26 -- every monthly equivalent wrong by 2x, in a
        column nothing would ever recompute.  The backfill must answer **9**.
        """
        user = _owner_with_paydays(db, "disagree@shekel.local", cadence_days=9)

        with app.app_context():
            restore_pay_period_derived_columns(db.session)
            _run(_M_C4B2.downgrade, db.session)
            last = (
                db.session.query(PayPeriod).filter_by(user_id=user.id)
                .order_by(PayPeriod.start_date.desc()).first()
            )
            # Written as SQL rather than through the ORM: the column exists in
            # the rewound SCHEMA but not on the model, which plan step
            # ``pay_calendar:C4-c`` deleted it from.  That is the honest
            # spelling of a legacy value -- a stored end no live writer can
            # author -- and it is what ledger row **P28** describes.
            db.session.execute(
                text("UPDATE budget.pay_periods SET end_date = :end "
                     "WHERE id = :id"),
                {"end": last.start_date + timedelta(days=28), "id": last.id},
            )
            db.session.query(PaySchedule).filter_by(
                user_id=user.id,
            ).delete(synchronize_session=False)
            db.session.commit()

            # The premise, asserted rather than assumed: the two derivations
            # really do disagree here, so the assertion below distinguishes
            # them rather than agreeing with both.
            stored_end = db.session.execute(
                text("SELECT end_date FROM budget.pay_periods WHERE id = :id"),
                {"id": last.id},
            ).scalar()
            assert (stored_end - last.start_date).days + 1 == 29
            assert pay_schedule_service.get_schedule(user.id) is None

            _run(_M_C4B2.upgrade, db.session)

            assert pay_schedule_service.get_schedule(
                user.id,
            ).cadence_days == 9

    def test_a_single_payday_owner_falls_back_to_the_stored_span(
        self, app, db,
    ):
        """One payday has no predecessor, so the stored span is the evidence.

        The registration bootstrap's own shape, and the reason the backfill
        keeps that arm at all: ``last.start_date - previous.start_date`` is
        undefined here, and the writer wrote this end as
        ``start + (cadence - 1)``, so inverting it recovers the cadence the
        owner was actually set up with.
        """
        user = _owner_with_paydays(
            db, "onepayday@shekel.local", cadence_days=9, num_periods=1,
        )

        with app.app_context():
            restore_pay_period_derived_columns(db.session)
            _run(_M_C4B2.downgrade, db.session)
            db.session.query(PaySchedule).filter_by(
                user_id=user.id,
            ).delete(synchronize_session=False)
            db.session.commit()
            assert db.session.query(PayPeriod).filter_by(
                user_id=user.id,
            ).count() == 1

            _run(_M_C4B2.upgrade, db.session)

            assert pay_schedule_service.get_schedule(
                user.id,
            ).cadence_days == 9

    def test_an_uninferable_cadence_ABORTS_rather_than_inventing_one(
        self, app, db,
    ):
        """The migration's stated failure mode, graded rather than described.

        Its docstring says an owner whose inferred cadence falls outside
        1..365 aborts the upgrade on ``ck_pay_schedule_cadence_range``, and
        that this is correct rather than a gap: such an owner's calendar
        already raises on every balance page (ledger row **P35**), and a
        clamped or invented number would make a broken schedule look repaired.

        **The sentence a fix writes about itself is the one its own tests
        usually cannot grade**, so this grades it: two paydays 400 days apart,
        no schedule row, and the upgrade must refuse.
        """
        with app.app_context():
            restore_pay_period_derived_columns(db.session)
            _run(_M_C4B2.downgrade, db.session)
            user = User(
                email="uninferable@shekel.local",
                password_hash=hash_password("keypass-123456"),
                display_name="Uninferable",
            )
            db.session.add(user)
            db.session.flush()
            # Inserted as SQL because the rewound schema still requires the
            # two derived columns the MODEL no longer declares: an ORM insert
            # here would fail on ``end_date``'s NOT NULL and say nothing about
            # this revision.  The ends are the derivation's own -- the day
            # before the next payday, then the last one's projection at the
            # 400-day spacing under test -- so the row set is the one an owner
            # in this state really held.
            for period_index, (payday, end) in enumerate([
                (date(2026, 1, 1), date(2027, 2, 4)),
                (date(2027, 2, 5), date(2028, 3, 10)),
            ]):
                db.session.execute(
                    text("INSERT INTO budget.pay_periods "
                         "(user_id, start_date, end_date, period_index) "
                         "VALUES (:uid, :start, :end, :idx)"),
                    {"uid": user.id, "start": payday, "end": end,
                     "idx": period_index},
                )
            db.session.commit()

            with pytest.raises(IntegrityError) as excinfo:
                _run(_M_C4B2.upgrade, db.session)

            assert "ck_pay_schedule_cadence_range" in str(excinfo.value)
            db.session.rollback()

    def test_the_key_is_what_blocks_dropping_the_schedule_table(self, app, db):
        """``af8254074bef``'s downgrade needs this one to have run first.

        That revision's ``downgrade()`` is ``DROP TABLE budget.pay_schedule``
        while ``budget.pay_periods`` survives -- which both produces the state
        this key forbids and meets a dependent constraint.  Alembic runs
        downgrades newest-first, so the chain resolves it; this case measures
        both halves of that claim rather than trusting the ordering.

        The DROP is REAL and the schema is left without the table.  Nothing is
        restored, and nothing needs to be: the ``db`` fixture re-clones the
        per-worker database for every test.
        """
        _owner_with_paydays(db, "droptable@shekel.local")

        with app.app_context():
            restore_pay_period_derived_columns(db.session)
            # Half one: with the key in place, the drop is refused, and the
            # message names this constraint rather than some other dependency.
            # ``InternalError`` is SQLAlchemy's wrapper for PostgreSQL's
            # ``DependentObjectsStillExist``; it is named precisely rather than
            # caught as ``DBAPIError`` so a future remapping fails here loudly
            # instead of letting a broader arm swallow a different refusal.
            with pytest.raises(InternalError) as excinfo:
                _run(_M_PHASE1.downgrade, db.session)

            assert _KEY in str(excinfo.value)
            db.session.rollback()

            # Half two: the chain's own order clears it.
            _run(_M_C4B2.downgrade, db.session)
            _run(_M_PHASE1.downgrade, db.session)

            assert db.session.execute(text(
                "SELECT to_regclass('budget.pay_schedule')"
            )).scalar() is None
