"""``budget.pay_eras`` -- a pay schedule is a SEQUENCE OF ERAS.

Plan step ``pay_calendar:C17-a`` (ruling **R-PC58**; the relation's shape is
the developer's ruling of 2026-09-11).  Migration ``6fc77e86d76f`` creates the
table, seeds ``ref.pay_cadence_kinds``, backfills one era per owner holding a
payday from the rhythm ``budget.pay_schedule`` held, and drops that rhythm off
the schedule row.  Plan step ``pay_calendar:C17-d-2`` (rulings **R-PC79**,
**R-PC80**): migration ``3ec5291ca4e2`` adds the day-of-month columns
``nominal_day`` and ``other_day`` under three CHECKs, makes ``cadence_days``
nullable, and DROPS ``kind_id`` with ``ref.pay_cadence_kinds`` -- the kind is
which parameter columns a row carries.

What each class is here to catch:

  1. **Catalog shape.**  The table, its named constraints, its key onto the
     owner's schedule row, and the ``audit_pay_eras`` trigger exist in the
     per-worker template; the three rhythm columns are GONE from
     ``budget.pay_schedule`` and the kind column and vocabulary are GONE
     from the era table.
  2. **Model contract.**  ``PayEra`` declares the same named constraints and
     the same CHECK texts the migration installed, so ``create_all`` /
     autogenerate match the migration and the two frozen copies of each
     text cannot drift.
  3. **Constraint behaviour.**  Two eras cannot take effect on one day, the
     cadence bound holds on the row, an era cannot exist without its
     owner's schedule row, and each of the three kind CHECKs refuses the
     row it exists for and admits the row it does not.
  4. **The migrations, both ways.**  ``C17-d-2``'s downgrade restores the
     kind column naming ``fixed_days`` on every row and REFUSES a
     day-of-month era; its upgrade puts the columns back.  ``C17-a``'s
     downgrade restores the LATEST era's rhythm onto the row and refuses a
     row holding no era; its upgrade's backfill states one era per owner
     from the row's own rhythm -- for a piecewise owner that is the row's
     own wrong claim, stated rather than guessed at -- and admits a recorded
     payday off that grid, which ruling R-PC47 says is a fact and not an
     error.  Driven through each revision's own callables in Alembic's
     newest-first order, never a hand-written copy.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, text
from sqlalchemy.exc import IntegrityError

from app.models.pay_era import (
    CADENCE_DAYS_MAX,
    CADENCE_DAYS_MIN,
    NOMINAL_DAY_CHECK,
    ONE_KIND_CHECK,
    OTHER_DAY_CHECK,
    PayEra,
)
from app.models.pay_schedule import PaySchedule
from app.models.user import User, UserSettings
from app.services import pay_era_write, pay_period_write, pay_schedule_service
from app.services.auth_service import hash_password
from app.services.pay_rhythm import FixedDays, Monthly, Rhythm, SemiMonthly
from app.enums import BusinessDayShiftEnum
from tests._test_helpers import (
    record_paydays_across_a_hole,
    era_of,
    load_migration_module,
    rhythm_of,
    run_migration_callable as _run,
    shift_id_of,
)

#: The two revisions, loaded so their own shipped callables are what this file
#: drives.  Hand-written DDL standing in for them would be a second statement
#: of the migration that could drift from it without failing anything.
_M_C17A = load_migration_module(
    "6fc77e86d76f_a_pay_schedule_is_a_sequence_of_eras.py",
)
_M_C17D2 = load_migration_module(
    "3ec5291ca4e2_a_pay_eras_kind_is_which_columns_it_carries.py",
)

#: Constraint names, named once so the introspection assertions below compare
#: against variables rather than inline literals.
_UQ_EFFECTIVE_FROM = "uq_pay_eras_user_effective_from"
_CK_CADENCE = "ck_pay_eras_cadence_range"
_CK_ONE_KIND = "ck_pay_eras_one_kind"
_CK_NOMINAL_DAY = "ck_pay_eras_nominal_day"
_CK_OTHER_DAY = "ck_pay_eras_other_day"
_FK_SCHEDULE = "fk_pay_eras_schedule"
_FK_KIND = "fk_pay_eras_kind_id"
_FK_SHIFT = "fk_pay_eras_shift_id"
_OLD_CK_CADENCE = "ck_pay_schedule_cadence_range"
_OLD_FK_SHIFT = "fk_pay_schedule_shift_id"


def _constraints(session, table):
    """Return the constraint names on ``budget.<table>`` as a set."""
    return {
        row[0] for row in session.execute(text(
            "SELECT conname FROM pg_constraint c "
            "  JOIN pg_class t ON t.oid = c.conrelid "
            "  JOIN pg_namespace n ON n.oid = t.relnamespace "
            " WHERE n.nspname = 'budget' AND t.relname = :table"
        ), {"table": table})
    }


def _columns(session, table):
    """Return the column names of ``budget.<table>``, in ordinal order."""
    return [
        row[0] for row in session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'budget' AND table_name = :table "
            "ORDER BY ordinal_position"
        ), {"table": table})
    ]


def _table_exists(session, qualified):
    """Return whether *qualified* (``schema.table``) exists."""
    return session.execute(
        text("SELECT to_regclass(:name)"), {"name": qualified},
    ).scalar() is not None


def _owner(db, email):
    """Create and commit a bare owner with no calendar yet."""
    user = User(
        email=email,
        password_hash=hash_password("c17apass-123456"),
        display_name="C17-a Owner",
    )
    db.session.add(user)
    db.session.flush()
    db.session.add(UserSettings(user_id=user.id))
    db.session.commit()
    return user


def _era_rows(session, user_id):
    """Return ``[(effective_from, cadence_days)]`` for *user_id*, ascending, by SQL."""
    return [
        (row[0], row[1]) for row in session.execute(text(
            "SELECT effective_from, cadence_days FROM budget.pay_eras "
            " WHERE user_id = :uid ORDER BY effective_from"
        ), {"uid": user_id})
    ]


def _era_kind_rows(session, user_id):
    """Return ``[(effective_from, cadence_days, nominal_day, other_day)]``, by SQL."""
    return [
        tuple(row) for row in session.execute(text(
            "SELECT effective_from, cadence_days, nominal_day, other_day "
            "  FROM budget.pay_eras WHERE user_id = :uid "
            " ORDER BY effective_from"
        ), {"uid": user_id})
    ]


def _rewind_to_c17a(session):
    """Run ``C17-d-2``'s ``downgrade()``: the schema ``C17-a``'s callables read.

    Alembic's newest-first order, spelled once: the head has no ``kind_id``
    and no ``ref.pay_cadence_kinds`` for ``C17-a``'s downgrade to drop, so a
    case whose subject is that revision reaches its schema through this
    one's own downgrade rather than through hand-written DDL.
    """
    _run(_M_C17D2.downgrade, session)


def _rewound_row(session, user_id):
    """Return the rewound row's ``(cadence_days, nominal_anchor)``, by SQL."""
    return session.execute(text(
        "SELECT cadence_days, nominal_anchor FROM budget.pay_schedule "
        " WHERE user_id = :uid"
    ), {"uid": user_id}).one()


class TestPostUpgradeCatalogShape:
    """The template carries the era table and the row no longer carries a rhythm."""

    def test_the_era_table_carries_its_constraints_and_trigger(self, app, db):
        """Every named constraint and the audit trigger are installed."""
        with app.app_context():
            live = _constraints(db.session, "pay_eras")
            for name in (
                _UQ_EFFECTIVE_FROM, _CK_CADENCE, _CK_ONE_KIND, _CK_NOMINAL_DAY,
                _CK_OTHER_DAY, _FK_SCHEDULE, _FK_SHIFT,
            ):
                assert name in live, f"{name} is not installed"
            assert _FK_KIND not in live, (
                "fk_pay_eras_kind_id is still installed: revision 3ec5291ca4e2 "
                "did not run"
            )
            trigger = db.session.execute(text(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgrelid = 'budget.pay_eras'::regclass "
                "AND tgname = 'audit_pay_eras' AND NOT tgisinternal"
            )).scalar()
            assert trigger == "audit_pay_eras", (
                "the audit_pay_eras trigger is not attached -- audit rows for "
                "era mutations would be lost"
            )
            assert _columns(db.session, "pay_eras") == [
                "id", "effective_from", "cadence_days", "shift_id",
                "user_id", "created_at", "nominal_day", "other_day",
            ]

    def test_the_schedule_row_no_longer_carries_the_rhythm(self, app, db):
        """The three columns and their two constraints left with the era."""
        with app.app_context():
            columns = _columns(db.session, "pay_schedule")
            for name in ("cadence_days", "shift_id", "nominal_anchor"):
                assert name not in columns, (
                    f"budget.pay_schedule still has {name!r}: revision "
                    f"6fc77e86d76f did not run, or a later one re-added it"
                )
            live = _constraints(db.session, "pay_schedule")
            assert _OLD_CK_CADENCE not in live
            assert _OLD_FK_SHIFT not in live

    def test_the_kind_vocabulary_is_gone(self, app, db):
        """``ref.pay_cadence_kinds`` no longer exists: the kind is the row's shape."""
        with app.app_context():
            assert not _table_exists(db.session, "ref.pay_cadence_kinds")


class TestModelContract:
    """The model declares the named constraints the migration installs."""

    def test_the_unique_key_is_owner_and_day(self):
        """``uq_pay_eras_user_effective_from`` is UNIQUE (user_id, effective_from)."""
        matching = [
            c for c in PayEra.__table__.constraints
            if isinstance(c, UniqueConstraint) and c.name == _UQ_EFFECTIVE_FROM
        ]
        assert len(matching) == 1
        columns = [col.name for col in matching[0].columns]
        assert columns == ["user_id", "effective_from"]

    def test_the_cadence_check_carries_the_one_bound(self):
        """The CHECK is built from the constants, so no third bound exists."""
        check = next(
            c for c in PayEra.__table__.constraints
            if isinstance(c, CheckConstraint) and c.name == _CK_CADENCE
        )
        assert str(check.sqltext) == (
            f"cadence_days BETWEEN {CADENCE_DAYS_MIN} AND {CADENCE_DAYS_MAX}"
        )

    @pytest.mark.parametrize("name, expected", [
        (_CK_ONE_KIND, ONE_KIND_CHECK),
        (_CK_NOMINAL_DAY, NOMINAL_DAY_CHECK),
        (_CK_OTHER_DAY, OTHER_DAY_CHECK),
    ])
    def test_the_model_and_the_migration_state_each_kind_check_once(
        self, name, expected,
    ):
        """The mapper's CHECK text and the revision's installed text are one string.

        A revision records what it installed and the model is what
        ``create_all`` reads, so the text is deliberately stated twice; this
        is the reconciler that keeps a deliberate duplicate from becoming a
        drift.
        """
        check = next(
            c for c in PayEra.__table__.constraints
            if isinstance(c, CheckConstraint) and c.name == name
        )
        assert str(check.sqltext) == expected
        # pylint: disable=protected-access
        assert dict(_M_C17D2._CHECKS)[name] == expected


class TestConstraintBehaviour:
    """The DB refuses two eras on one day, a bad cadence, and an orphan era."""

    @staticmethod
    def _row(user_id, **overrides):
        """A legal era row for *user_id*, with *overrides* applied."""
        fields = {
            "user_id": user_id, "effective_from": date(2026, 1, 2),
            "cadence_days": 14, "shift_id": shift_id_of(),
        }
        fields.update(overrides)
        return PayEra(**fields)

    def _refused(self, db, user_id, constraint, **overrides):
        """Assert the row *overrides* describe is refused by *constraint*."""
        try:
            with pytest.raises(IntegrityError, match=constraint):
                db.session.add(self._row(user_id, **overrides))
                db.session.flush()
        finally:
            db.session.rollback()

    def _admitted(self, db, user_id, **overrides):
        """Assert the row *overrides* describe is stored, then roll it back."""
        try:
            db.session.add(self._row(user_id, **overrides))
            db.session.flush()
        finally:
            db.session.rollback()

    def test_two_eras_cannot_take_effect_on_one_day(self, app, db, bare_user):
        """``uq_pay_eras_user_effective_from``: one answer to "the era covering this day"."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(user_id)
            db.session.add(self._row(user_id))
            db.session.flush()
            try:
                with pytest.raises(IntegrityError, match=_UQ_EFFECTIVE_FROM):
                    db.session.add(self._row(user_id, cadence_days=7))
                    db.session.flush()
            finally:
                db.session.rollback()

    @pytest.mark.parametrize("cadence", [0, 366])
    def test_a_cadence_outside_the_bound_is_refused(
        self, app, db, bare_user, cadence,
    ):
        """``ck_pay_eras_cadence_range`` holds 1..365 on the row itself."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(user_id)
            try:
                with pytest.raises(IntegrityError, match=_CK_CADENCE):
                    db.session.add(self._row(user_id, cadence_days=cadence))
                    db.session.flush()
            finally:
                db.session.rollback()

    def test_an_era_needs_its_owners_schedule_row(self, app, db, bare_user):
        """``fk_pay_eras_schedule``: the owner-level row is the era's parent."""
        user_id = bare_user["user"].id
        with app.app_context():
            assert pay_schedule_service.get_schedule(user_id) is None
            try:
                with pytest.raises(IntegrityError, match=_FK_SCHEDULE):
                    db.session.add(self._row(user_id))
                    db.session.flush()
            finally:
                db.session.rollback()

    @pytest.mark.parametrize("overrides", [
        {"cadence_days": 14, "nominal_day": 31, "effective_from": date(2026, 2, 28)},
        {"cadence_days": 14, "other_day": 15},
    ])
    def test_a_fixed_days_era_cannot_carry_a_month_column(
        self, app, db, bare_user, overrides,
    ):
        """``ck_pay_eras_one_kind``: the three kinds' shapes are disjoint."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(user_id)
            self._refused(db, user_id, _CK_ONE_KIND, **overrides)

    @pytest.mark.parametrize("overrides", [
        # Outside the domain and NOTHING else wrong: 32 exceeds 31's day and
        # is what clamping 32 into January gives, so only conjunct 1 fires.
        {"nominal_day": 32, "effective_from": date(2026, 1, 31)},
        # Below the domain: a day every month holds is never lost.
        {"nominal_day": 28, "effective_from": date(2026, 2, 27)},
        # At or below the date's own day: restates a day the date carries.
        {"nominal_day": 30, "effective_from": date(2026, 4, 30)},
        # Above the day, but the month COULD carry it: no clamp happened.
        {"nominal_day": 31, "effective_from": date(2026, 3, 15)},
        {"nominal_day": 31, "effective_from": date(2026, 4, 15)},
    ])
    def test_a_nominal_day_that_records_no_clamp_is_refused(
        self, app, db, bare_user, overrides,
    ):
        """``ck_pay_eras_nominal_day``'s three conjuncts, each isolated by one case."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(user_id)
            self._refused(
                db, user_id, _CK_NOMINAL_DAY, cadence_days=None, **overrides,
            )

    @pytest.mark.parametrize("overrides", [
        {"nominal_day": 31, "effective_from": date(2026, 2, 28)},
        {"nominal_day": 31, "effective_from": date(2026, 4, 30)},
        {"nominal_day": 29, "effective_from": date(2027, 2, 28)},
        {"nominal_day": 30, "effective_from": date(2028, 2, 29)},
        {"nominal_day": None, "effective_from": date(2026, 1, 31)},
    ])
    def test_a_nominal_day_that_records_a_clamp_is_admitted(
        self, app, db, bare_user, overrides,
    ):
        """The direction the CHECK must not refuse: a genuinely clamped anchor."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(user_id)
            self._admitted(db, user_id, cadence_days=None, **overrides)

    @pytest.mark.parametrize("overrides", [
        {"other_day": 0},
        {"other_day": 32},
        # The same day the anchor means, read off the date ...
        {"other_day": 2},
        # ... and read off nominal_day when the first month clamped it.
        {"other_day": 31, "nominal_day": 31, "effective_from": date(2026, 2, 28)},
        # Both days at 28 or above: one payday in February.
        {"other_day": 30, "effective_from": date(2026, 1, 28)},
        {"other_day": 28, "nominal_day": 31, "effective_from": date(2026, 4, 30)},
    ])
    def test_an_other_day_that_is_not_a_second_payday_is_refused(
        self, app, db, bare_user, overrides,
    ):
        """``ck_pay_eras_other_day``: a day of the month, distinct, lower <= 27."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(user_id)
            self._refused(
                db, user_id, _CK_OTHER_DAY, cadence_days=None, **overrides,
            )

    @pytest.mark.parametrize("overrides", [
        {"other_day": 15},
        {"other_day": 31, "effective_from": date(2026, 1, 15)},
        {"other_day": 15, "nominal_day": 31, "effective_from": date(2026, 2, 28)},
        {"other_day": 27, "effective_from": date(2026, 1, 28)},
    ])
    def test_an_other_day_that_is_a_second_payday_is_admitted(
        self, app, db, bare_user, overrides,
    ):
        """The direction the CHECK must not refuse: every legal pair shape."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(user_id)
            self._admitted(db, user_id, cadence_days=None, **overrides)

    def test_a_monthly_era_is_the_absence_of_every_parameter(
        self, app, db, bare_user,
    ):
        """``cadence_days``, ``nominal_day`` and ``other_day`` all NULL is a legal era."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(user_id)
            self._admitted(db, user_id, cadence_days=None)

    def test_the_schedule_row_cannot_go_under_a_live_era(
        self, app, db, bare_user,
    ):
        """RESTRICT: the parent stays while an era stands on it."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(user_id)
            pay_era_write.mint_era(user_id, era_of(date(2026, 1, 2), 14))
            db.session.commit()
            try:
                with pytest.raises(IntegrityError, match=_FK_SCHEDULE):
                    db.session.query(PaySchedule).filter_by(
                        user_id=user_id,
                    ).delete(synchronize_session=False)
                    db.session.flush()
            finally:
                db.session.rollback()


class TestTheKindMigrationBothWays:
    """``3ec5291ca4e2``'s own ``downgrade`` and ``upgrade``, driven with owners in place.

    The per-worker database starts at head, so every case runs the
    ``downgrade`` first and reads the rewound table by SQL, because the head
    mapper selects the two columns the downgrade drops.  Everything else
    stays at head (ledger row **balance:P79** owns the general shape).
    """

    def test_the_downgrade_restores_the_kind_column_naming_fixed_days(
        self, app, db,
    ):
        """Every fixed-days era comes back with ``kind_id`` naming the one member."""
        user = _owner(db, "d2-downgrade@shekel.local")
        pay_period_write.record_paydays(
            user_id=user.id, first_payday=date(2026, 1, 2), num_periods=2,
            rhythm=rhythm_of(14),
        )
        db.session.commit()

        with app.app_context():
            _run(_M_C17D2.downgrade, db.session)

            assert _table_exists(db.session, "ref.pay_cadence_kinds")
            assert _columns(db.session, "pay_eras") == [
                "id", "effective_from", "cadence_days", "shift_id",
                "user_id", "created_at", "kind_id",
            ]
            assert db.session.execute(text(
                "SELECT k.name FROM budget.pay_eras e "
                "  JOIN ref.pay_cadence_kinds k ON k.id = e.kind_id "
                " WHERE e.user_id = :uid"
            ), {"uid": user.id}).scalars().all() == ["fixed_days"]
            live = _constraints(db.session, "pay_eras")
            assert _FK_KIND in live
            for name in (_CK_ONE_KIND, _CK_NOMINAL_DAY, _CK_OTHER_DAY):
                assert name not in live

    @pytest.mark.parametrize("cadence", [Monthly(15), SemiMonthly((1, 15))])
    def test_the_downgrade_REFUSES_a_day_of_month_era(self, app, db, cadence):
        """The old schema has no home for a month rhythm, and none is invented."""
        user = _owner(db, "d2-refuses@shekel.local")
        pay_period_write.record_paydays(
            user_id=user.id, first_payday=date(2026, 1, 15), num_periods=2,
            rhythm=Rhythm(cadence, BusinessDayShiftEnum.NONE),
        )
        db.session.commit()

        with app.app_context():
            with pytest.raises(RuntimeError, match="day-of-month era"):
                _run(_M_C17D2.downgrade, db.session)
            db.session.rollback()

            assert not _table_exists(db.session, "ref.pay_cadence_kinds")
            assert "nominal_day" in _columns(db.session, "pay_eras")

    def test_the_round_trip_leaves_a_fixed_days_era_as_it_was(self, app, db):
        """Down, then up: the era reads back the same value through the head mapper."""
        user = _owner(db, "d2-roundtrip@shekel.local")
        pay_period_write.record_paydays(
            user_id=user.id, first_payday=date(2026, 1, 2), num_periods=2,
            rhythm=rhythm_of(14),
        )
        db.session.commit()

        with app.app_context():
            _run(_M_C17D2.downgrade, db.session)
            _run(_M_C17D2.upgrade, db.session)

            assert _era_kind_rows(db.session, user.id) == [
                (date(2026, 1, 2), 14, None, None),
            ]
            assert not _table_exists(db.session, "ref.pay_cadence_kinds")
            assert pay_schedule_service.resolve_cadence(user.id) == FixedDays(14)


class TestTheMigrationBothWays:
    """``6fc77e86d76f``'s own ``downgrade`` and ``upgrade``, driven with owners in place.

    The per-worker database starts at head, so every case runs
    ``3ec5291ca4e2``'s ``downgrade`` and then this revision's -- Alembic's
    newest-first order, which is the only way to reach the schema the
    ``upgrade`` reads -- and reads the rewound row by SQL, because the head
    mapper no longer selects its rhythm columns and does select the table
    the downgrade drops.  A case that upgrades again runs both upgrades, so
    the head mapper can read the result.  What it does NOT do is put the
    database at this revision's parent: everything else stays at head,
    which is enough for a statement whose subject is these two tables
    (ledger row **balance:P79** owns the general shape).
    """

    def test_the_downgrade_restores_the_LATEST_eras_rhythm_onto_the_row(
        self, app, db,
    ):
        """A piecewise owner's row reads back the era their last batch stated.

        Two eras -- 14 days from 2026-01-02, then 7 from 2026-02-20 -- and
        the old schema can hold one rhythm, which is the one every batch
        overwrote it with: the latest.  ``nominal_anchor`` comes back as that
        era's ``effective_from``, a day on its grid.
        """
        user = _owner(db, "downgrade-latest@shekel.local")
        pay_period_write.record_paydays(
            user_id=user.id, first_payday=date(2026, 1, 2), num_periods=2,
            rhythm=rhythm_of(14),
        )
        record_paydays_across_a_hole(
            user_id=user.id, first_payday=date(2026, 2, 20), num_periods=1,
            rhythm=rhythm_of(7),
        )
        db.session.commit()
        assert _era_rows(db.session, user.id) == [
            (date(2026, 1, 2), 14), (date(2026, 2, 20), 7),
        ]
        # The read above opened a transaction holding a share lock on the
        # era table; the DDL below takes an exclusive one on a second
        # connection, so the outer session must not sit idle in it.
        db.session.commit()

        with app.app_context():
            _rewind_to_c17a(db.session)
            _run(_M_C17A.downgrade, db.session)

            assert not _table_exists(db.session, "budget.pay_eras")
            assert not _table_exists(db.session, "ref.pay_cadence_kinds")
            assert _rewound_row(db.session, user.id) == (7, date(2026, 2, 20))
            live = _constraints(db.session, "pay_schedule")
            assert _OLD_CK_CADENCE in live
            assert _OLD_FK_SHIFT in live

    def test_the_downgrade_REFUSES_a_row_holding_no_era(self, app, db, bare_user):
        """The old schema needs a cadence there and this revision has none to write."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(user_id)
            db.session.commit()

            _rewind_to_c17a(db.session)
            with pytest.raises(RuntimeError, match="hold no era"):
                _run(_M_C17A.downgrade, db.session)
            db.session.rollback()

            assert _table_exists(db.session, "budget.pay_eras")
            assert "cadence_days" not in _columns(db.session, "pay_schedule")
            _run(_M_C17D2.upgrade, db.session)

    def test_the_round_trip_backfills_ONE_era_from_the_rows_opening(
        self, app, db,
    ):
        """Down, then up: the era comes back phased on the record's opening.

        The rewound row holds ``nominal_anchor = 2026-01-30`` (the extend's
        phase, MAX-shaped) and paydays from 2026-01-02; the backfill walks the
        anchor back by whole cadences to the opening, so the era takes effect
        2026-01-02 and not on the anchor.
        """
        user = _owner(db, "roundtrip@shekel.local")
        pay_period_write.record_paydays(
            user_id=user.id, first_payday=date(2026, 1, 2), num_periods=3,
            rhythm=rhythm_of(14),
        )
        db.session.commit()

        with app.app_context():
            _rewind_to_c17a(db.session)
            _run(_M_C17A.downgrade, db.session)
            db.session.execute(text(
                "UPDATE budget.pay_schedule SET nominal_anchor = :anchor "
                " WHERE user_id = :uid"
            ), {"anchor": date(2026, 1, 30), "uid": user.id})
            db.session.commit()

            _run(_M_C17A.upgrade, db.session)
            _run(_M_C17D2.upgrade, db.session)

            assert _era_rows(db.session, user.id) == [(date(2026, 1, 2), 14)]
            assert "cadence_days" not in _columns(db.session, "pay_schedule")
            assert pay_schedule_service.resolve_cadence(user.id) == FixedDays(14)

    def test_the_upgrade_states_the_ROWS_rhythm_for_a_piecewise_owner(
        self, app, db,
    ):
        """One era at the row's cadence from the opening -- the row's own claim.

        Eras 14 days from 01-02 then 7 from 02-20.  The rewound row holds
        cadence 7 (the last batch's, which is what the old schema always
        held), and the upgrade cannot know the earlier cadence: it states 7
        from 2026-01-02, the claim the row made, rather than inventing the
        14-day era.  Ledger row **N-492**'s defect survives the round trip
        for such an owner, in a relation that can now hold the correction.
        *A first draft REFUSED this owner; an adversarial review measured
        that the refusal could not see them -- every 14-day gap is a multiple
        of 7 -- so the honest form is to state what is stated and say so.*
        """
        user = _owner(db, "piecewise@shekel.local")
        pay_period_write.record_paydays(
            user_id=user.id, first_payday=date(2026, 1, 2), num_periods=2,
            rhythm=rhythm_of(14),
        )
        record_paydays_across_a_hole(
            user_id=user.id, first_payday=date(2026, 2, 20), num_periods=1,
            rhythm=rhythm_of(7),
        )
        db.session.commit()

        with app.app_context():
            _rewind_to_c17a(db.session)
            _run(_M_C17A.downgrade, db.session)
            assert _rewound_row(db.session, user.id) == (7, date(2026, 2, 20))

            _run(_M_C17A.upgrade, db.session)
            _run(_M_C17D2.upgrade, db.session)

            assert _era_rows(db.session, user.id) == [(date(2026, 1, 2), 7)]

    def test_a_recorded_payday_OFF_the_grid_does_not_stop_the_upgrade(
        self, app, db,
    ):
        """Ruling **R-PC47**: an off-cadence payday is a fact, warned about and kept.

        Paydays 01-02 and 01-16 at 14 days, and 01-30 moved by hand to 01-31
        -- the shape payroll produces when it pays a day late.  The upgrade
        phases the era on the grid through the opening and leaves the
        departure where the record put it; refusing would have told the
        operator to rewrite a payday.
        """
        user = _owner(db, "departure@shekel.local")
        pay_period_write.record_paydays(
            user_id=user.id, first_payday=date(2026, 1, 2), num_periods=3,
            rhythm=rhythm_of(14),
        )
        db.session.commit()

        with app.app_context():
            _rewind_to_c17a(db.session)
            _run(_M_C17A.downgrade, db.session)
            db.session.execute(text(
                "UPDATE budget.pay_periods SET start_date = :moved "
                " WHERE user_id = :uid AND start_date = :was"
            ), {"moved": date(2026, 1, 31), "uid": user.id,
                "was": date(2026, 1, 30)})
            db.session.commit()

            _run(_M_C17A.upgrade, db.session)
            _run(_M_C17D2.upgrade, db.session)

            assert _era_rows(db.session, user.id) == [(date(2026, 1, 2), 14)]
            assert db.session.execute(text(
                "SELECT start_date FROM budget.pay_periods "
                " WHERE user_id = :uid ORDER BY start_date"
            ), {"uid": user.id}).scalars().all() == [
                date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 31),
            ]

    def test_the_upgrade_REFUSES_an_owner_with_paydays_and_no_anchor(
        self, app, db,
    ):
        """No anchor, no grid to phase on: the one refusal the upgrade keeps.

        Built by clearing the rewound row's anchor by SQL -- the state paydays
        inserted outside the application would leave -- and the refusal
        happens before the era table exists.
        """
        user = _owner(db, "anchorless@shekel.local")
        pay_period_write.record_paydays(
            user_id=user.id, first_payday=date(2026, 1, 2), num_periods=2,
            rhythm=rhythm_of(14),
        )
        db.session.commit()

        with app.app_context():
            _rewind_to_c17a(db.session)
            _run(_M_C17A.downgrade, db.session)
            db.session.execute(text(
                "UPDATE budget.pay_schedule SET nominal_anchor = NULL "
                " WHERE user_id = :uid"
            ), {"uid": user.id})
            db.session.commit()

            with pytest.raises(RuntimeError, match="no nominal_anchor"):
                _run(_M_C17A.upgrade, db.session)
            db.session.rollback()

            assert not _table_exists(db.session, "budget.pay_eras")
            assert "cadence_days" in _columns(db.session, "pay_schedule")
