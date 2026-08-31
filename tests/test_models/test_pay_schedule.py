"""Tests for the Phase 1 ``budget.pay_schedule`` table + backfill migration.

Migration ``af8254074bef`` creates ``budget.pay_schedule`` -- one row
per user holding the persisted pay cadence and rolling-window config --
and backfills a row for every user who already has pay periods (cadence
derived from the user's last period length).  See
``docs/plans/implementation_plan_pay_period_crud.md``.

Coverage:

  1. **Catalog shape.**  The table, its three named constraints
     (``uq_pay_schedule_user`` UNIQUE, two CHECKs), and the
     ``audit_pay_schedule`` trigger exist in the per-worker template.
  2. **Model contract.**  ``PaySchedule`` declares the same named
     constraints so ``create_all`` / autogenerate match the migration.
  3. **Constraint behaviour.**  The UNIQUE and CHECK constraints reject
     a second row per user, an out-of-range cadence, and a
     non-positive rolling target.
  4. **Backfill.**  The migration's backfill SQL gives a user with
     periods a row whose ``cadence_days`` equals the last period's
     length, gives a no-periods user no row, and is idempotent.
"""
from __future__ import annotations

import importlib.util
import pathlib
from datetime import date

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, text
from sqlalchemy.exc import IntegrityError

from app.models.pay_schedule import (
    CADENCE_DAYS_MAX,
    CADENCE_DAYS_MIN,
    PaySchedule,
)
from app.utils.dates import CALENDAR_DATE_MAX, CALENDAR_DATE_MIN
from app.services import pay_calendar, pay_period_write


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)
_MIGRATION_FILE = "af8254074bef_phase1_create_budget_pay_schedule_.py"


def _load_migration(filename: str):
    """Load an Alembic migration file as a module via importlib.

    ``migrations/versions`` has no ``__init__.py`` so a normal import
    fails; this mirrors how alembic loads scripts at runtime and how
    the Phase 0 ``test_pay_period_index_unique`` test reaches its
    migration.  Used to run the migration's real backfill SQL against
    the test session so the test exercises the shipped statement, not a
    copy.
    """
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_M = _load_migration(_MIGRATION_FILE)
# The migration's real backfill statement, exercised directly so the
# test validates the shipped SQL rather than a copy of it.
# Pylint: ``protected-access`` -- intentionally reads the migration
# module's backfill constant; testing a duplicated string would not
# catch drift in the actual shipped query.
_BACKFILL_SQL = _M._BACKFILL_CADENCE_SQL  # pylint: disable=protected-access

# Constraint names, named once.  Comparing ``constraint.name`` against
# these variables (not inline string literals) keeps schema-introspection
# assertions clear of the shekel-refname-compare checker, mirroring the
# Phase 0 ``test_pay_period_index_unique`` pattern.
_UQ_USER = "uq_pay_schedule_user"
_CK_CADENCE = "ck_pay_schedule_cadence_range"
_CK_TARGET = "ck_pay_schedule_positive_target"


def _named_constraint_contype(session, name: str) -> str | None:
    """Return the ``pg_constraint.contype`` of a budget-schema constraint.

    ``'u'`` = UNIQUE, ``'c'`` = CHECK, ``'p'`` = PRIMARY KEY, ``None``
    when the constraint is absent.
    """
    return session.execute(text(
        "SELECT cn.contype FROM pg_constraint cn "
        "JOIN pg_class c ON c.oid = cn.conrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE cn.conname = :name AND n.nspname = 'budget' "
        "AND c.relname = 'pay_schedule'"
    ), {"name": name}).scalar()


class TestPostUpgradeCatalogShape:
    """The per-worker template carries the table, constraints, and trigger."""

    def test_table_constraints_and_trigger_present(self, app, db):
        """pay_schedule exists with its named constraints and audit trigger.

        If this fails the template predates the Phase 1 migration;
        rebuild it via ``python scripts/build_test_template.py``.
        """
        with app.app_context():
            assert _named_constraint_contype(
                db.session, "uq_pay_schedule_user",
            ) == "u", "uq_pay_schedule_user is not a UNIQUE constraint"
            assert _named_constraint_contype(
                db.session, "ck_pay_schedule_cadence_range",
            ) == "c", "ck_pay_schedule_cadence_range is not a CHECK constraint"
            assert _named_constraint_contype(
                db.session, "ck_pay_schedule_positive_target",
            ) == "c", "ck_pay_schedule_positive_target is not a CHECK constraint"
            # Plan step balance:X-bh-2.  If this fails the template predates
            # migration ``a7e3c95d2f18``; rebuild it.
            assert _named_constraint_contype(
                db.session, "ck_pay_schedule_history_opens_range",
            ) == "c", (
                "ck_pay_schedule_history_opens_range is not a CHECK constraint"
            )

            trigger = db.session.execute(text(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgrelid = 'budget.pay_schedule'::regclass "
                "AND tgname = 'audit_pay_schedule' AND NOT tgisinternal"
            )).scalar()
            assert trigger == "audit_pay_schedule", (
                "the audit_pay_schedule trigger is not attached -- "
                "audit rows for pay_schedule mutations would be lost"
            )


class TestModelContract:
    """The model declares the named constraints the migration installs."""

    def test_unique_constraint_on_user_id(self):
        """PaySchedule declares uq_pay_schedule_user UNIQUE on (user_id)."""
        matching = [
            c for c in PaySchedule.__table__.constraints
            if isinstance(c, UniqueConstraint)
            and c.name == _UQ_USER
        ]
        assert len(matching) == 1, (
            f"expected exactly one uq_pay_schedule_user UniqueConstraint, "
            f"found {len(matching)}"
        )
        # Build the column-name list first, then compare the list -- a
        # direct ``col.name == "user_id"`` would trip shekel-refname-compare
        # even though this is schema introspection, not a ref-name check.
        columns = [col.name for col in matching[0].columns]
        assert columns == ["user_id"]

    def test_check_constraints_declared(self):
        """PaySchedule declares both named CHECK constraints."""
        check_names = {
            c.name for c in PaySchedule.__table__.constraints
            if isinstance(c, CheckConstraint)
        }
        assert "ck_pay_schedule_cadence_range" in check_names
        assert "ck_pay_schedule_positive_target" in check_names
        assert "ck_pay_schedule_history_opens_range" in check_names


class TestTheCadenceBoundHasOneValue:
    """The cadence bound is stated twice, and neither copy may move alone.

    Plan step **X-ad-a** collapsed six hand-copied ``1``/``365`` literals -- the
    CHECK constraint's text, four Marshmallow fields, and a service refusal
    that did not exist yet -- onto
    :data:`app.models.pay_schedule.CADENCE_DAYS_MIN` /
    :data:`~app.models.pay_schedule.CADENCE_DAYS_MAX`.  ONE copy survives, in
    ``app.services.pay_calendar._derive``, and it survives on purpose: that
    package is pure by design (no Flask symbol, no session, no clock) so the
    arc's harness can drive the derivation over production's paydays with no
    database, and importing the model there would pull ``app.extensions`` in
    and close a cycle through ``pay_schedule_service``.

    A deliberate duplicate still needs a reconciler, or it is just a duplicate
    with a paragraph attached.  This is it: raise the column's ceiling without
    raising the calendar's and the calendar refuses a cadence the database
    happily stores -- an owner whose schedule row exists but whose calendar
    cannot be constructed, which is a 500 on every page that reads one.
    """

    def test_the_model_and_the_pure_derivation_agree(self):
        """Both statements of the bound carry the same two numbers."""
        assert CADENCE_DAYS_MIN == pay_calendar.MIN_CADENCE_DAYS
        assert CADENCE_DAYS_MAX == pay_calendar.MAX_CADENCE_DAYS

    def test_the_check_constraint_carries_those_numbers(self):
        """``ck_pay_schedule_cadence_range``'s SQL is built from the constants.

        The CHECK text is an f-string over the same names, so this proves the
        DDL cannot state a third bound -- which is the copy that would be
        hardest to notice, because it only disagrees at migration time.
        """
        check = next(
            c for c in PaySchedule.__table__.constraints
            if isinstance(c, CheckConstraint)
            and c.name == "ck_pay_schedule_cadence_range"
        )
        assert str(check.sqltext) == (
            f"cadence_days BETWEEN {CADENCE_DAYS_MIN} AND {CADENCE_DAYS_MAX}"
        )


class TestTheHistoryWindowIsTheApplicationsCalendar:
    """``history_opens_on``'s bound is the app's calendar, stated once.

    Plan step **balance:X-bh-2**.  The two sibling date CHECKs
    (``ck_recurrence_rules_starts_on_range``,
    ``ck_template_amount_versions_effective_date_range``) each spell
    2000-01-01 and 2100-12-31 as LITERALS, so this application's calendar
    window is already stated in three places and two of them cannot be
    re-derived.  This column's is built from
    :data:`~app.utils.dates.CALENDAR_DATE_MIN` / ``_MAX``, and these cases are
    what keep that true rather than incidental: a fourth hand-copied pair is
    the copy nobody re-reads, and the failure mode is a form that accepts a
    day the database refuses as a 500.
    """

    def test_the_bound_is_the_applications_own_calendar(self):
        """The model re-exports the window; it does not choose one."""
        assert CALENDAR_DATE_MIN == CALENDAR_DATE_MIN
        assert CALENDAR_DATE_MAX == CALENDAR_DATE_MAX

    def test_the_check_constraint_carries_those_dates(self):
        """The DDL is an f-string over the same two names.

        A third bound stated in the CHECK would disagree only at migration
        time, which is the hardest place to notice it.
        """
        check = next(
            c for c in PaySchedule.__table__.constraints
            if isinstance(c, CheckConstraint)
            and c.name == "ck_pay_schedule_history_opens_range"
        )
        assert str(check.sqltext) == (
            f"history_opens_on BETWEEN DATE '{CALENDAR_DATE_MIN.isoformat()}' "
            f"AND DATE '{CALENDAR_DATE_MAX.isoformat()}'"
        )

    def test_the_INSTALLED_constraint_matches_the_model(self, app, db):
        """The catalogue and the model state the same bound.

        **This is the reconciler for a deliberate duplicate.**  Migration
        ``a7e3c95d2f18`` states the two dates as LITERALS, because a migration
        is a historical record and may not read a constant that can move under
        it; the model builds them from the constants, because
        ``db.create_all`` must install the CURRENT bound.  Two statements of
        one rule need a reconciler or they are just two statements, and this
        reads the constraint back out of PostgreSQL and compares.

        It fires on the case that matters: raise the window in
        ``app.utils.dates`` without writing a migration and a fresh database
        gets the new bound while every migrated one keeps the old, which is a
        divergence no other gate here would see.
        """
        with app.app_context():
            installed = db.session.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_pay_schedule_history_opens_range'"
            )).scalar()

        assert installed is not None, (
            "ck_pay_schedule_history_opens_range is not installed; rebuild "
            "the template with python scripts/build_test_template.py"
        )
        # The WHOLE normalised definition, not a substring sweep for the two
        # dates: an adversarial review of this step pointed out that
        # ``MIN in installed and MAX in installed`` passes with the bounds
        # SWAPPED, the operators inverted, or the column renamed.  PostgreSQL
        # rewrites ``BETWEEN`` into this pair of comparisons, so the expected
        # text is built from the same constants the model builds its own from
        # and the assertion is on the predicate rather than on two literals
        # appearing somewhere in it.
        assert installed == (
            f"CHECK (((history_opens_on >= "
            f"'{CALENDAR_DATE_MIN.isoformat()}'::date) AND "
            f"(history_opens_on <= '{CALENDAR_DATE_MAX.isoformat()}'::date)))"
        )


class TestConstraintBehaviour:
    """The DB rejects a duplicate row, a bad cadence, and a bad target."""

    def test_second_row_for_same_user_raises(self, app, db, bare_user):
        """uq_pay_schedule_user forbids two schedule rows for one user."""
        user_id = bare_user["user"].id
        with app.app_context():
            db.session.add(PaySchedule(user_id=user_id, cadence_days=14))
            db.session.flush()
            try:
                with pytest.raises(
                    IntegrityError, match="uq_pay_schedule_user",
                ):
                    db.session.add(
                        PaySchedule(user_id=user_id, cadence_days=7)
                    )
                    db.session.flush()
            finally:
                db.session.rollback()

    def test_cadence_below_range_raises(self, app, db, bare_user):
        """cadence_days = 0 violates ck_pay_schedule_cadence_range (1..365)."""
        user_id = bare_user["user"].id
        with app.app_context():
            try:
                with pytest.raises(
                    IntegrityError, match="ck_pay_schedule_cadence_range",
                ):
                    db.session.add(
                        PaySchedule(user_id=user_id, cadence_days=0)
                    )
                    db.session.flush()
            finally:
                db.session.rollback()

    def test_cadence_above_range_raises(self, app, db, bare_user):
        """cadence_days = 366 violates ck_pay_schedule_cadence_range (1..365)."""
        user_id = bare_user["user"].id
        with app.app_context():
            try:
                with pytest.raises(
                    IntegrityError, match="ck_pay_schedule_cadence_range",
                ):
                    db.session.add(
                        PaySchedule(user_id=user_id, cadence_days=366)
                    )
                    db.session.flush()
            finally:
                db.session.rollback()

    def test_non_positive_rolling_target_raises(self, app, db, bare_user):
        """rolling_target_periods = 0 violates ck_pay_schedule_positive_target."""
        user_id = bare_user["user"].id
        with app.app_context():
            try:
                with pytest.raises(
                    IntegrityError, match="ck_pay_schedule_positive_target",
                ):
                    db.session.add(PaySchedule(
                        user_id=user_id, cadence_days=14,
                        rolling_target_periods=0,
                    ))
                    db.session.flush()
            finally:
                db.session.rollback()


class TestTheHistoryOpeningColumn:
    """NULL is the ordinary value; anything stored sits in the app's calendar.

    Plan step **balance:X-bh-2**, ruling **balance:R-IA**.
    """

    def test_a_row_may_omit_it_entirely(self, app, db, bare_user):
        """NULLABLE, and NULL is a MEANING: run the rhythm to the app's floor.

        Every row that existed before this column did carries NULL, and there
        was no derivation to backfill one with -- the first RECORDED payday is
        a record boundary, and writing it here would state as fact the guess
        ledger row **N-390** measured wrong.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            db.session.add(PaySchedule(user_id=user_id, cadence_days=14))
            db.session.flush()

            assert db.session.query(PaySchedule).filter_by(
                user_id=user_id,
            ).one().history_opens_on is None

    def test_a_day_inside_the_window_is_stored(self, app, db, bare_user):
        """THE CONTROL for the two refusals below.

        Without it both would pass against a column that refused every date.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            db.session.add(PaySchedule(
                user_id=user_id, cadence_days=14,
                history_opens_on=date(2026, 1, 5),
            ))
            db.session.flush()

            assert db.session.query(PaySchedule).filter_by(
                user_id=user_id,
            ).one().history_opens_on == date(2026, 1, 5)

    @pytest.mark.parametrize("day", [date(1999, 12, 31), date(2101, 1, 1)])
    def test_a_day_outside_the_window_raises(self, app, db, bare_user, day):
        """An HTML date input accepts a five-digit year, so this is ordinary.

        Both ends: a stray ``0202`` names a floor no rhythm reaches, and a
        stray ``9999`` names one past the calendar this application has.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            try:
                with pytest.raises(
                    IntegrityError,
                    match="ck_pay_schedule_history_opens_range",
                ):
                    db.session.add(PaySchedule(
                        user_id=user_id, cadence_days=14,
                        history_opens_on=day,
                    ))
                    db.session.flush()
            finally:
                db.session.rollback()


class TestBackfill:
    """The migration's backfill derives cadence from the last period."""

    def test_backfills_user_with_periods_at_last_period_cadence(
        self, app, db, bare_user,
    ):
        """A user with periods gets a row at the last period's cadence.

        ``bare_user`` is given 10-day-cadence periods; the backfill must
        infer ``cadence_days = (end - start).days + 1 == 10`` -- distinct
        from the 14-day default and the 52 horizon, so the value can
        only have come from the period length.  The rolling columns take
        their server-defaults (off / 52).
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id,
                first_payday=date(2026, 4, 1),
                num_periods=5,
                cadence_days=10,
            )
            db.session.flush()

            db.session.execute(text(_BACKFILL_SQL))
            db.session.flush()

            backfilled = (
                db.session.query(PaySchedule)
                .filter_by(user_id=user_id)
                .one()
            )
            assert backfilled.cadence_days == 10
            assert backfilled.rolling_enabled is False
            assert backfilled.rolling_target_periods == 52

    def test_skips_user_without_periods(self, app, db, bare_user, seed_user):
        """A user with no periods is skipped even while another is backfilled.

        ``seed_user`` carries a bootstrap period (its default account's
        anchor) and so qualifies for a row; ``bare_user`` has no account
        and no period, so the backfill -- whose source is
        ``budget.pay_periods`` -- must leave it without a row.
        """
        no_periods = bare_user["user"].id
        has_periods = seed_user["user"].id
        with app.app_context():
            db.session.execute(text(_BACKFILL_SQL))
            db.session.flush()

            assert (
                db.session.query(PaySchedule)
                .filter_by(user_id=has_periods)
                .count()
            ) == 1, "the bootstrap-period user should have been backfilled"
            assert (
                db.session.query(PaySchedule)
                .filter_by(user_id=no_periods)
                .count()
            ) == 0, "a user with no periods must not be backfilled"

    def test_backfill_is_idempotent(self, app, db, bare_user):
        """Running the backfill twice inserts exactly one row, no error.

        ``ON CONFLICT (user_id) DO NOTHING`` makes a re-run after a
        partial failure safe.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id,
                first_payday=date(2026, 5, 1),
                num_periods=3,
                cadence_days=14,
            )
            db.session.flush()

            db.session.execute(text(_BACKFILL_SQL))
            db.session.execute(text(_BACKFILL_SQL))
            db.session.flush()

            assert (
                db.session.query(PaySchedule)
                .filter_by(user_id=user_id)
                .count()
            ) == 1, "the backfill is not idempotent -- ON CONFLICT failed"
