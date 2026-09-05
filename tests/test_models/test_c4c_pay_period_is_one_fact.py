"""``b7a41e2c9d63``: a pay period is ONE fact, graded in both directions.

Plan step **pay_calendar:C4-c**, closing ledger rows **P1**, **P4**, **P5** and
**P9**.  The revision drops ``budget.pay_periods.end_date`` and
``budget.pay_periods.period_index`` together with the three constraints that
exist only to bound them -- ``uq_pay_periods_user_index``,
``ck_pay_periods_positive_index`` and ``ck_pay_periods_date_order`` -- because
both columns are derived from the owner's payday set and neither was reconciled
to it.

**This file exists because the revision was written claiming a grader that did
not exist.**  Its docstring said "Tested rather than argued
(``tests/test_models/test_c4c_pay_period_is_one_fact.py``)" while that path had
never been added -- ``git diff --name-status`` over the branch reported no new
test file at all until this one.  A destructive ``DROP COLUMN`` of two columns
and three constraints, with a ``downgrade`` nothing had executed, is exactly
what Definition of Done #7 is about.

**What each class is here to catch, and why prose could not hold it.**

  1. **The upgrade really removed five objects and no more.**  Read from
     ``pg_constraint`` and ``information_schema``, not from the model: the
     model and the migration are two statements of one schema and the point is
     that they agree.  ``uq_pay_periods_user_start`` and
     ``fk_pay_periods_schedule`` must SURVIVE -- a revision that took the
     table's remaining key with them would leave every other case here green.
  2. **The downgrade rebuilds the DERIVATION, not a projection.**  Every end
     but the last is ``lead(start_date) - 1``; the last is
     ``start_date + (cadence_days - 1)``.  On production those two agree on
     every row -- 63 paydays all fourteen days apart -- so a rebuild that took
     the projection branch everywhere reproduces that database byte for byte.
     That is the pre-normalization defect this arc exists to remove, so the
     fixture here is deliberately OFF-CADENCE and the two branches give
     different answers on every row BUT THE LAST -- where they coincide by
     construction, the last period having no successor, so the projection IS
     the derivation there.  An on-cadence fixture, where they coincide on
     every row, is what made plan step C2-a's first P14 test vacuous.
  3. **The ordinal is a WINDOW, partitioned by owner.**  Two owners, and each
     one's ordinals restart at zero -- a rebuild that numbered the table
     globally passes every single-owner case.
  4. **The downgrade is NOT unconditionally lossless, and refuses rather than
     inventing.**  A one-day pay period is legal after this revision and
     ``start_date < end_date`` forbids it, so the downgrade aborts.  The abort
     is driven, and so is the fact that it rolls back WHOLE -- PostgreSQL's DDL
     is transactional, which is what makes it a refusal rather than a
     half-applied schema.
  5. **The chain order below this revision holds.**  ``f75485db6757`` and
     ``e5f6a7b8c9d0`` both drop objects this revision drops, and both are
     EARLIER, so Alembic reaches them only after this ``downgrade()`` has put
     those objects back.  Driven through their own shipped callables rather
     than argued from the ordering.

**What is NOT graded here, said rather than left as a hole.**  The module
docstring's third loss -- that ``ADD COLUMN`` appends, so the rebuilt pair sits
after ``created_at`` rather than in its original positions -- is asserted as an
observation in class 2 rather than as a requirement, because nothing in ``app/``
reads a column position.  And the backfill's ``INNER JOIN budget.pay_schedule``
cannot be shown losing a row: ``fk_pay_periods_schedule`` is ``ON DELETE
RESTRICT`` (plan step C4-b-2, ruling **R-PC41**), so an owner holding paydays
without a cadence row is unstorable and the ``SET NOT NULL`` guard behind that
join has no reachable input.

The bootstrap that drives a migration's own callables is
``tests._test_helpers.run_migration_callable``, imported here as ``_run``.  It
is shared with :func:`~tests._test_helpers.restore_pay_period_derived_columns`
rather than copied, which an adversarial review of this step asked for: the two
had already drifted on whether the caller or the helper commits first, and
that difference is a ten-second ``lock_timeout`` rather than a style point.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.pay_period import PayPeriod
from app.models.user import User, UserSettings
from app.services import pay_period_write
from app.services.pay_calendar import calendar_for
from app.services.auth_service import hash_password
from tests._test_helpers import (
    rhythm_of,
    load_migration_module,
    run_migration_callable as _run,
)

#: This revision, loaded so its own shipped callables are what this file drives.
#: Hand-written DDL standing in for them would be a second statement of the
#: migration that could drift from it without failing anything.
_M_C4C = load_migration_module("b7a41e2c9d63_a_pay_period_is_one_fact.py")

#: The two EARLIER revisions whose downgrades meet the objects this one drops.
_M_PHASE0 = load_migration_module(
    "f75485db6757_phase0_unique_user_period_index_on_pay_.py"
)
_M_CHECKS = load_migration_module(
    "e5f6a7b8c9d0_add_check_and_unique_constraints.py"
)

#: The five objects this revision removes, spelled once.  Every assertion below
#: reads the DATABASE for them.
_DROPPED_COLUMNS = ("end_date", "period_index")
_DROPPED_CONSTRAINTS = (
    "uq_pay_periods_user_index",
    "ck_pay_periods_positive_index",
    "ck_pay_periods_date_order",
)

#: What must SURVIVE.  A revision that took the table's remaining key with the
#: three above would leave every other case in this file green.
_SURVIVING_CONSTRAINTS = (
    "uq_pay_periods_user_start",
    "fk_pay_periods_schedule",
)


def _columns(session):
    """Return the column names of ``budget.pay_periods``, in ordinal order."""
    return [
        row[0] for row in session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'budget' AND table_name = 'pay_periods' "
            "ORDER BY ordinal_position"
        ))
    ]


def _constraints(session):
    """Return the constraint names on ``budget.pay_periods`` as a set.

    Read from ``pg_constraint`` rather than from the model, which is the whole
    point: the two are separate statements of one schema.
    """
    return {
        row[0] for row in session.execute(text(
            "SELECT conname FROM pg_constraint c "
            "  JOIN pg_class t ON t.oid = c.conrelid "
            "  JOIN pg_namespace n ON n.oid = t.relnamespace "
            " WHERE n.nspname = 'budget' AND t.relname = 'pay_periods'"
        ))
    }


def _rebuilt_rows(session, user_id):
    """Return ``[(start_date, end_date, period_index)]`` for *user_id*, ascending.

    Read as SQL because the two columns exist in the downgraded SCHEMA and not
    on the model, which plan step ``pay_calendar:C4-c`` deleted them from.
    """
    return [
        (row[0], row[1], row[2]) for row in session.execute(text(
            "SELECT start_date, end_date, period_index "
            "  FROM budget.pay_periods WHERE user_id = :uid "
            " ORDER BY start_date"
        ), {"uid": user_id})
    ]


def _owner(db, email):
    """Create and commit a bare owner with no calendar yet.

    Args:
        db: The Flask-SQLAlchemy extension.
        email: The owner's email, unique per case.

    Returns:
        The committed :class:`~app.models.user.User`.
    """
    user = User(
        email=email,
        password_hash=hash_password("c4cpass-123456"),
        display_name="C4-c Owner",
    )
    db.session.add(user)
    db.session.flush()
    db.session.add(UserSettings(user_id=user.id))
    db.session.commit()
    return user


def _off_cadence_calendar(db, user_id):
    """Record three paydays whose spacing DISAGREES with the stored cadence.

    **The fixture the whole downgrade argument turns on.**  Production's
    schedule is perfectly regular, so ``lead(start_date) - 1`` and
    ``start_date + (cadence_days - 1)`` answer the same day on every row and a
    rebuild that took the projection branch everywhere reproduces it exactly.
    Here they differ on every row, so the assertion distinguishes the two rather
    than agreeing with both.

    Built through ``record_paydays`` twice rather than by hand: the second batch
    changes the owner's stored cadence to 7 while the paydays it leaves behind
    are 14 and 35 days apart.  That is a schedule the app can really write --
    "correct my cadence going forward" -- so the state under test is reachable
    rather than invented.

    Args:
        db: The Flask-SQLAlchemy extension.
        user_id: The owner recording them.

    Returns:
        ``(paydays, cadence_days)`` -- the three paydays ascending and the
        cadence the owner ends up storing.
    """
    pay_period_write.record_paydays(
        user_id=user_id, first_payday=date(2026, 1, 2),
        num_periods=2, rhythm=rhythm_of(14),
    )
    db.session.commit()
    # The forward-only floor is the latest payday plus the STORED cadence
    # (2026-01-16 + 14 = 2026-01-30), so this batch is accepted and the stored
    # cadence becomes 7 while the gap it leaves behind is 35 days.
    pay_period_write.record_paydays(
        user_id=user_id, first_payday=date(2026, 2, 20),
        num_periods=1, rhythm=rhythm_of(7),
    )
    db.session.commit()
    return [date(2026, 1, 2), date(2026, 1, 16), date(2026, 2, 20)], 7


class TestTheUpgradeLeftTheTableOneFact:
    """Head's catalog: the two columns and the three constraints are gone."""

    def test_neither_derived_column_exists(self, app, db):
        """``end_date`` and ``period_index`` are not columns of this table.

        The template is built by running the migrations, so this is the
        upgrade's own effect read back rather than a restatement of the model.
        """
        with app.app_context():
            columns = _columns(db.session)

            for name in _DROPPED_COLUMNS:
                assert name not in columns, (
                    f"budget.pay_periods still has {name!r}: revision "
                    f"b7a41e2c9d63 did not run, or a later one re-added it"
                )
            assert columns == ["id", "user_id", "start_date", "created_at"], (
                f"the table is {columns}; a pay period is ONE fact plus its "
                f"owner, its identity and when the row was written"
            )

    def test_the_three_bounding_constraints_are_gone(self, app, db):
        """The unique ordinal and the two CHECKs went with their subject."""
        with app.app_context():
            live = _constraints(db.session)

            for name in _DROPPED_CONSTRAINTS:
                assert name not in live, (
                    f"{name} survives, so the state it policed is still "
                    f"expressible and this step deleted a fence without "
                    f"deleting its subject"
                )

    def test_the_tables_remaining_key_and_its_schedule_key_SURVIVE(
        self, app, db,
    ):
        """The drop is narrow, and this is the case that says so.

        ``uq_pay_periods_user_start`` is the payday model's exact key -- one
        period per owner per opening day -- and ``fk_pay_periods_schedule`` is
        plan step C4-b-2's.  A revision that dropped either alongside the three
        above would leave every other case in this file green while making a
        duplicate payday storable.
        """
        with app.app_context():
            live = _constraints(db.session)

            for name in _SURVIVING_CONSTRAINTS:
                assert name in live, (
                    f"{name} is missing: this revision drops five objects and "
                    f"this is not one of them"
                )


class TestTheDowngradeRebuildsTheDerivation:
    """The rebuilt values, on a schedule whose two branches disagree."""

    def test_every_end_but_the_last_is_the_day_before_the_next_payday(
        self, app, db,
    ):
        """Hand-computed on the off-cadence fixture, where the branches differ.

        Paydays 2026-01-02, 2026-01-16 and 2026-02-20 with a stored cadence of
        7.  The derivation gives:

          * 2026-01-02 -> 2026-01-15, the day before the next payday.  The
            projection would say 2026-01-08.
          * 2026-01-16 -> 2026-02-19.  The projection would say 2026-01-22.
          * 2026-02-20 -> 2026-02-26, ``start + (7 - 1)``, because this one has
            no successor.

        **Every row distinguishes the two branches**, which is what an
        on-cadence fixture cannot do: there the two spellings coincide and a
        rebuild that projected everywhere would pass.
        """
        user = _owner(db, "offcadence@shekel.local")
        paydays, cadence = _off_cadence_calendar(db, user.id)

        with app.app_context():
            _run(_M_C4C.downgrade, db.session)

            rebuilt = _rebuilt_rows(db.session, user.id)

            assert [row[0] for row in rebuilt] == paydays
            assert [row[1] for row in rebuilt] == [
                date(2026, 1, 15), date(2026, 2, 19), date(2026, 2, 26),
            ]
            # **The control, and it compares the two ANSWERS rather than
            # re-deriving one of them.**  A first cut computed the projection
            # in Python and checked it against hand-written constants, which
            # reads nothing from the database and cannot fail unless the hand
            # arithmetic is wrong (adversarial review, 2026-09-01).  What makes
            # the assertion above a CHOICE is that the two branches disagree on
            # the rows that have a successor -- so that is what is asserted.
            projection = [
                payday + timedelta(days=cadence - 1) for payday in paydays
            ]
            assert [row[1] for row in rebuilt][:-1] != projection[:-1], (
                "the two branches agree on every row with a successor, so this "
                "fixture cannot tell a derived end from a projected one"
            )
            # And they DO coincide on the last row, which has no successor --
            # stated so the inequality above is not read as covering it.
            assert rebuilt[-1][1] == projection[-1]

    def test_a_SINGLE_payday_owner_is_ordinal_zero_and_a_projected_end(
        self, app, db,
    ):
        """One row is both the first and the last, and the branches meet on it.

        The registration bootstrap's own shape: ``auth_service.register_user``
        records a batch, and an owner can sit on one payday until they generate
        more.  ``lead(start_date)`` is NULL there, so the end is the projection
        and the ordinal is 0 -- the two arms of the rebuild landing on a single
        row rather than on different ones, which the three-period fixture above
        cannot show.

        Hand-computed: payday 2026-04-03 at a cadence of 9 ends 2026-04-11.
        """
        user = _owner(db, "single@shekel.local")
        pay_period_write.record_paydays(
            user_id=user.id, first_payday=date(2026, 4, 3),
            num_periods=1, rhythm=rhythm_of(9),
        )
        db.session.commit()

        with app.app_context():
            _run(_M_C4C.downgrade, db.session)

            assert _rebuilt_rows(db.session, user.id) == [
                (date(2026, 4, 3), date(2026, 4, 11), 0),
            ]

    def test_the_ordinal_is_dense_from_zero_in_PAYDAY_order(self, app, db):
        """``row_number() over (order by start_date) - 1``, read back."""
        user = _owner(db, "ordinal@shekel.local")
        _off_cadence_calendar(db, user.id)

        with app.app_context():
            _run(_M_C4C.downgrade, db.session)

            assert [row[2] for row in _rebuilt_rows(db.session, user.id)] == [
                0, 1, 2,
            ]

    def test_every_REBUILT_COLUMN_is_partitioned_by_owner(self, app, db):
        """Two owners at DIFFERENT cadences, and neither leaks into the other.

        **This case is the file's only grade of the schedule JOIN and of the
        ``lead`` window's partition, and an adversarial review is why it
        asserts spans rather than ordinals** (2026-09-01).  It used to check
        the ordinals alone, and two mutations of the rebuild statement passed
        all fifteen tests while corrupting money-bearing spans:

        * deleting the owner predicate from the schedule join wrote owner A's
          forecast cadence onto owner B's row -- a 7-day span where B's own
          cadence says 14;
        * deleting ``PARTITION BY`` from the ``lead`` window alone gave owner
          A's last period an end on the day before owner B's FIRST payday, a
          105-day paycheck.

        Ordinals are unchanged under both, which is exactly why an ordinal-only
        assertion could not see either.

        The cadences differ (7 and 14) so a cadence read from the wrong owner
        lands on a different day, and owner B's paydays are LATER than owner
        A's so an unpartitioned ``lead`` reaches across the boundary.
        """
        first = _owner(db, "partition-a@shekel.local")
        second = _owner(db, "partition-b@shekel.local")
        _off_cadence_calendar(db, first.id)
        pay_period_write.record_paydays(
            user_id=second.id, first_payday=date(2026, 6, 5),
            num_periods=3, rhythm=rhythm_of(14),
        )
        db.session.commit()

        with app.app_context():
            _run(_M_C4C.downgrade, db.session)

            # Owner A: the off-cadence schedule, ends unchanged by B existing.
            assert _rebuilt_rows(db.session, first.id) == [
                (date(2026, 1, 2), date(2026, 1, 15), 0),
                (date(2026, 1, 16), date(2026, 2, 19), 1),
                (date(2026, 2, 20), date(2026, 2, 26), 2),
            ]
            # Owner B: fortnightly from 2026-06-05, ordinals restarting at 0
            # and the last end projected at B's OWN cadence (05 + 14 - 1).
            assert _rebuilt_rows(db.session, second.id) == [
                (date(2026, 6, 5), date(2026, 6, 18), 0),
                (date(2026, 6, 19), date(2026, 7, 2), 1),
                (date(2026, 7, 3), date(2026, 7, 16), 2),
            ]

    def test_the_rebuild_agrees_with_the_APPLICATION_derivation(self, app, db):
        """The rebuilt triples equal what every screen answers for the same day.

        **Two independently written implementations of one specification.**
        The migration states the derivation as SQL windows; ``_derive.py``
        states it as Python over a payday list, and it is what
        ``calendar_for`` -- and therefore every producer in ``app/`` -- reads.
        Comparing them is a real cross-check rather than one producer agreeing
        with itself, and it is the migration docstring's own claim that "the
        rebuilt column agrees with what every screen shows" said as an
        assertion instead of a sentence.

        Driven on TWO owners at different cadences, so a rebuild that crossed
        the owner boundary in either window disagrees here as well.  The
        calendar is resolved BEFORE the downgrade, because the derivation reads
        only paydays and the schedule row -- neither of which the downgrade
        touches -- and reading it first is what keeps the comparison from being
        taken against a value the downgrade could have moved.
        """
        first = _owner(db, "crosscheck-a@shekel.local")
        second = _owner(db, "crosscheck-b@shekel.local")
        _off_cadence_calendar(db, first.id)
        pay_period_write.record_paydays(
            user_id=second.id, first_payday=date(2026, 6, 5),
            num_periods=3, rhythm=rhythm_of(14),
        )
        db.session.commit()

        with app.app_context():
            expected = {
                owner.id: [
                    (period.start_date, period.end_date, period.period_index)
                    for period in calendar_for(owner.id).saved()
                ]
                for owner in (first, second)
            }
            # The premise: the application really does answer something for
            # both owners, so an empty calendar cannot make this vacuous.
            assert len(expected[first.id]) == 3
            assert len(expected[second.id]) == 3

            _run(_M_C4C.downgrade, db.session)

            for owner in (first, second):
                assert _rebuilt_rows(db.session, owner.id) == expected[owner.id], (
                    f"owner {owner.id}: the migration's SQL and "
                    f"pay_calendar.derive_periods disagree about the span or "
                    f"the ordinal, so a downgraded database would not render "
                    f"what the app renders"
                )

    def test_the_rebuilt_columns_are_APPENDED_not_restored_in_place(
        self, app, db,
    ):
        """The module docstring's third stated loss, observed rather than hoped.

        ``ADD COLUMN`` appends, so the pair comes back after ``created_at``
        rather than in its original positions 4 and 5.  Nothing in ``app/``
        reads a column position and ``pg_dump`` writes an explicit column list,
        so this is recorded rather than required -- but a ``SELECT *`` in a
        hand-run script sees a different tuple shape, and the docstring says so
        because of this observation.
        """
        user = _owner(db, "colorder@shekel.local")
        _off_cadence_calendar(db, user.id)

        with app.app_context():
            _run(_M_C4C.downgrade, db.session)

            assert _columns(db.session) == [
                "id", "user_id", "start_date", "created_at",
                "end_date", "period_index",
            ]

    def test_the_downgrade_re_adds_the_three_constraints_and_both_NOT_NULLs(
        self, app, db,
    ):
        """Exactly five constraint objects come back, and no sixth.

        Three are the ones the upgrade named.  The other two are the columns'
        ``NOT NULL``s, which PostgreSQL materialises as ``pg_constraint`` rows
        of type ``n`` -- so this reads them for free, and they are worth
        reading: the downgrade adds both columns NULLABLE, fills them, and only
        then runs ``SET NOT NULL``, which is what makes a row the rebuild could
        not reach abort the downgrade by name instead of being written an
        invented span.  Their presence is that promise kept.

        The second assertion is the narrow half: the downgrade must REMOVE
        nothing.
        """
        user = _owner(db, "constraints@shekel.local")
        _off_cadence_calendar(db, user.id)

        with app.app_context():
            before = _constraints(db.session)
            _run(_M_C4C.downgrade, db.session)

            after = _constraints(db.session)

            assert after - before == set(_DROPPED_CONSTRAINTS) | {
                "pay_periods_end_date_not_null",
                "pay_periods_period_index_not_null",
            }, (
                f"the downgrade added {sorted(after - before)}; it must add "
                f"the three constraints the upgrade dropped and the two "
                f"columns' NOT NULLs, and nothing else"
            )
            assert not before - after, (
                f"the downgrade REMOVED {sorted(before - after)}"
            )

    def test_the_re_added_constraints_have_the_right_DEFINITIONS(
        self, app, db,
    ):
        """Names are not definitions, and an adversarial review measured that.

        The case above compares ``pg_constraint.conname`` sets, and PostgreSQL
        accepts a second UNIQUE over columns another already covers without
        complaint -- so re-adding ``uq_pay_periods_user_index`` over
        ``(user_id, start_date)`` instead of ``(user_id, period_index)``, a
        plausible copy-paste from the sibling three lines away, left every test
        green.  Weakening ``period_index >= 0`` to ``>= -1`` did too.

        Read from ``pg_get_constraintdef`` so what is asserted is what
        PostgreSQL will actually enforce for a developer who steps back.
        """
        user = _owner(db, "constraintdefs@shekel.local")
        _off_cadence_calendar(db, user.id)

        with app.app_context():
            _run(_M_C4C.downgrade, db.session)

            defs = {
                name: db.session.execute(text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    " WHERE conname = :name"
                ), {"name": name}).scalar()
                for name in _DROPPED_CONSTRAINTS
            }

            assert defs["uq_pay_periods_user_index"] == (
                "UNIQUE (user_id, period_index)"
            )
            assert defs["ck_pay_periods_positive_index"] == (
                "CHECK ((period_index >= 0))"
            )
            assert defs["ck_pay_periods_date_order"] == (
                "CHECK ((start_date < end_date))"
            )


class TestTheUpgradeWritesDownWhatItDISCARDS:
    """The "provably free" claim, asked of the database rather than quoted.

    Production was measured clean on 2026-09-01 -- 63 rows, zero mismatches --
    and that is one measurement of one database on one day.  The upgrade is a
    one-way ``DROP COLUMN``, so a developer restoring a pre-C3-b dump runs it
    over data nobody censused, and a stored end that disagreed with the paydays
    would be normalised away in silence with no way back.
    ``_report_stored_versus_derived`` writes each one down first.

    **Reachable only from BELOW the drop**, which is why these cases downgrade
    before they plant: the columns exist nowhere else, and the downgrade writes
    correct values by construction, so a disagreement has to be hand-written
    after it.
    """

    def test_a_disagreeing_row_is_REPORTED_and_dropped_anyway(
        self, app, db, caplog,
    ):
        """The warning names both values, and the drop still happens.

        Refusing would leave an owner whose schedule was already wrong stuck
        below this revision -- and this revision is what makes that schedule
        unrepresentable.  ``f2b7c40d918e`` ruled the same shape the same way
        for the same reason.
        """
        user = _owner(db, "disagree-report@shekel.local")
        _off_cadence_calendar(db, user.id)

        with app.app_context():
            _run(_M_C4C.downgrade, db.session)
            # A stored end the paydays do not justify: period 0 runs to
            # 2026-01-15 by derivation and is written 2026-01-31.
            db.session.execute(text(
                "UPDATE budget.pay_periods SET end_date = :end "
                " WHERE user_id = :uid AND start_date = :start"
            ), {"end": date(2026, 1, 31), "uid": user.id,
                "start": date(2026, 1, 2)})
            db.session.commit()

            with caplog.at_level(
                logging.WARNING, logger="alembic.runtime.migration",
            ):
                _run(_M_C4C.upgrade, db.session)

            warnings = [
                r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING
            ]
            assert len(warnings) == 1, warnings
            assert "2026-01-31" in warnings[0], warnings[0]
            assert "2026-01-15" in warnings[0], warnings[0]
            # And it proceeded: the columns really are gone.
            assert _columns(db.session) == [
                "id", "user_id", "start_date", "created_at",
            ]

    def test_an_agreeing_schedule_is_NOT_reported(self, app, db, caplog):
        """THE CONTROL: a consistent owner produces no warning.

        Without it the case above passes for a report that fires on every row,
        which on production would be 63 warnings about a database the same
        docstring calls provably clean.
        """
        user = _owner(db, "agree-report@shekel.local")
        _off_cadence_calendar(db, user.id)

        with app.app_context():
            _run(_M_C4C.downgrade, db.session)

            with caplog.at_level(
                logging.WARNING, logger="alembic.runtime.migration",
            ):
                _run(_M_C4C.upgrade, db.session)

            assert [
                r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING
            ] == []


class TestTheDowngradeIsNotUnconditionallyLossless:
    """The one-day period, which this revision legalises and the CHECK forbids."""

    def test_a_ONE_DAY_period_makes_the_downgrade_REFUSE(self, app, db):
        """``ck_pay_periods_date_order`` cannot hold what C4-c legalises.

        Two paydays a day apart define a one-day paycheck, which is an ordinary
        schedule now: ``budget.pay_schedule.cadence_days`` has always accepted
        1 and the derivation has always handled it -- what could not hold one
        was a stored end (ledger rows **P9** and **P33**).  Re-adding
        ``start_date < end_date`` on such an owner is therefore impossible, and
        the migration's docstring says so rather than claiming a reversibility
        it does not have.

        **Refusing is the correct outcome**, and the alternative is the reason:
        any end the downgrade could invent here is a day of coverage the owner
        never had.
        """
        user = _owner(db, "oneday@shekel.local")
        pay_period_write.record_paydays(
            user_id=user.id, first_payday=date(2026, 3, 2),
            num_periods=3, rhythm=rhythm_of(1),
        )
        db.session.commit()

        with app.app_context():
            with pytest.raises(IntegrityError) as excinfo:
                _run(_M_C4C.downgrade, db.session)

            assert "ck_pay_periods_date_order" in str(excinfo.value)
            db.session.rollback()

    def test_a_TWO_payday_one_day_schedule_refuses_on_BOTH_branches(
        self, app, db,
    ):
        """Both arms of the rebuild produce the violating row, not just one.

        The case above records three paydays at cadence 1, so the violation
        could come from the ``lead(start) - 1`` arm alone and a mutation that
        broke only the projection arm's refusal would hide there.  Two paydays
        is the smallest schedule where BOTH fire: the first period's end is its
        successor's payday minus a day (its own start), and the second's is
        ``start + 1 - 1`` (its own start too).

        Driven rather than reasoned: the refusal is asserted, and the rows are
        counted afterwards to show the abort was whole.
        """
        user = _owner(db, "oneday-both@shekel.local")
        pay_period_write.record_paydays(
            user_id=user.id, first_payday=date(2026, 3, 2),
            num_periods=2, rhythm=rhythm_of(1),
        )
        db.session.commit()

        with app.app_context():
            with pytest.raises(IntegrityError) as excinfo:
                _run(_M_C4C.downgrade, db.session)

            assert "ck_pay_periods_date_order" in str(excinfo.value)
            db.session.rollback()
            assert db.session.query(PayPeriod).filter_by(
                user_id=user.id,
            ).count() == 2

    def test_the_refused_downgrade_rolls_back_WHOLE(self, app, db):
        """PostgreSQL's DDL is transactional, so the abort leaves head standing.

        The half that makes the refusal above a refusal rather than a
        half-applied schema: the two columns are not left behind, the three
        constraints are not left half-added, and the owner's rows are untouched.
        Asserted after the raise rather than argued from "DDL is transactional".
        """
        user = _owner(db, "oneday-rollback@shekel.local")
        pay_period_write.record_paydays(
            user_id=user.id, first_payday=date(2026, 3, 2),
            num_periods=3, rhythm=rhythm_of(1),
        )
        db.session.commit()

        with app.app_context():
            with pytest.raises(IntegrityError):
                _run(_M_C4C.downgrade, db.session)
            db.session.rollback()

            assert _columns(db.session) == [
                "id", "user_id", "start_date", "created_at",
            ]
            live = _constraints(db.session)
            for name in _DROPPED_CONSTRAINTS:
                assert name not in live
            assert db.session.query(PayPeriod).filter_by(
                user_id=user.id,
            ).count() == 3


class TestTheRoundTripIsAFixedPoint:
    """Down then up leaves the schema where it started."""

    def test_down_then_up_drops_the_pair_and_the_three_constraints_again(
        self, app, db,
    ):
        """The pair is re-runnable rather than one-shot.

        The ``upgrade`` half runs against a table that now holds both columns
        again, so every ``drop_constraint`` and ``drop_column`` in it has to
        find its object exactly as it did the first time.
        """
        user = _owner(db, "roundtrip@shekel.local")
        _off_cadence_calendar(db, user.id)

        with app.app_context():
            before_columns = _columns(db.session)
            before_constraints = _constraints(db.session)

            _run(_M_C4C.downgrade, db.session)
            _run(_M_C4C.upgrade, db.session)

            assert _columns(db.session) == before_columns
            assert _constraints(db.session) == before_constraints

    def test_the_rebuilt_values_survive_a_second_round_trip(self, app, db):
        """Two full cycles answer what one answered, which one cannot say.

        A rebuild that read its own previous output rather than the paydays --
        ordering by ``period_index`` instead of by ``start_date``, say -- gives
        the same answer once and can drift on the repeat.  The producer is
        asked the SECOND time.
        """
        user = _owner(db, "twice@shekel.local")
        _off_cadence_calendar(db, user.id)

        with app.app_context():
            _run(_M_C4C.downgrade, db.session)
            once = _rebuilt_rows(db.session, user.id)
            _run(_M_C4C.upgrade, db.session)
            _run(_M_C4C.downgrade, db.session)

            assert _rebuilt_rows(db.session, user.id) == once


class TestTheChainOrderBelowThisRevisionHolds:
    """The two earlier revisions whose downgrades meet the objects this drops."""

    def test_the_phase0_downgrade_finds_its_unique_constraint(self, app, db):
        """``f75485db6757.downgrade()`` drops the ordinal key and indexes it.

        It drops ``uq_pay_periods_user_index`` and creates
        ``idx_pay_periods_user_index (user_id, period_index)`` -- both of which
        need this revision's ``downgrade()`` to have run first.  Alembic goes
        newest-first, so the chain resolves it; this case measures that rather
        than trusting the ordering.
        """
        user = _owner(db, "phase0@shekel.local")
        _off_cadence_calendar(db, user.id)

        with app.app_context():
            _run(_M_C4C.downgrade, db.session)
            assert "uq_pay_periods_user_index" in _constraints(db.session)

            _run(_M_PHASE0.downgrade, db.session)

            assert "uq_pay_periods_user_index" not in _constraints(db.session)
            assert db.session.execute(text(
                "SELECT 1 FROM pg_indexes WHERE schemaname = 'budget' "
                "  AND tablename = 'pay_periods' "
                "  AND indexname = 'idx_pay_periods_user_index'"
            )).scalar() == 1, (
                "the legacy non-unique index was not restored, so the column "
                "it names must have been missing when it ran"
            )

    def test_the_checks_revision_finds_both_of_its_pay_period_CHECKs(
        self, app, db,
    ):
        """``e5f6a7b8c9d0.downgrade()`` names two CHECKs this one re-adds.

        Its ``downgrade`` calls ``drop_constraint`` on
        ``ck_pay_periods_positive_index`` and ``ck_pay_periods_date_order`` by
        name, so it fails outright if this revision has not put them back.

        **Its callable is NOT driven here, and the reason is the honest form of
        this claim.**  That revision's ``downgrade`` drops constraints across
        eleven tables, and one of them --
        ``ck_salary_profiles_positive_periods`` -- was itself dropped by the
        LATER revision ``f2b7c40d918e`` (R-F16).  In a real chain Alembic
        reaches ``e5f6a7b8c9d0`` only after that revision's own ``downgrade``
        has re-added it; at HEAD-minus-this-revision it has not, so running the
        whole callable here dies on an object that has nothing to do with pay
        periods.  Driving it anyway and catching the error would grade the
        wrong failure.  What IS this revision's claim -- that the two objects
        it names are present when it runs -- is asserted directly, and the
        source is read for the names so a rename cannot leave this passing
        against a constraint nobody drops.

        Run in ISOLATION from the phase-0 case above because the two are
        independent claims about the same ordering, and one case running both
        would report either failure as the other's.
        """
        user = _owner(db, "checks@shekel.local")
        _off_cadence_calendar(db, user.id)
        with open(_M_CHECKS.__file__, encoding="utf-8") as handle:
            source = handle.read()

        with app.app_context():
            _run(_M_C4C.downgrade, db.session)

            live = _constraints(db.session)
            for name in ("ck_pay_periods_positive_index",
                         "ck_pay_periods_date_order"):
                assert (
                    f'op.drop_constraint("{name}", "pay_periods"' in source
                ), (
                    f"e5f6a7b8c9d0's downgrade no longer drops {name}, so "
                    f"this case is asserting the presence of an object "
                    f"nothing needs"
                )
                assert name in live, (
                    f"{name} is absent after this revision's downgrade, so "
                    f"e5f6a7b8c9d0's would die on it"
                )
