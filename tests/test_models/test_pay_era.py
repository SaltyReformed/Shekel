"""``budget.pay_eras`` -- a pay schedule is a SEQUENCE OF ERAS.

Plan step ``pay_calendar:C17-a`` (ruling **R-PC58**; the relation's shape is
the developer's ruling of 2026-09-11).  Migration ``6fc77e86d76f`` creates the
table, seeds ``ref.pay_cadence_kinds``, backfills one era per owner holding a
payday from the rhythm ``budget.pay_schedule`` held, and drops that rhythm off
the schedule row.

What each class is here to catch:

  1. **Catalog shape.**  The table, its named constraints, its two keys onto
     the owner's schedule row and the vocabulary, and the ``audit_pay_eras``
     trigger exist in the per-worker template -- and the three columns are
     GONE from ``budget.pay_schedule``.
  2. **Model contract.**  ``PayEra`` declares the same named constraints, so
     ``create_all`` / autogenerate match the migration.
  3. **Constraint behaviour.**  Two eras cannot take effect on one day, the
     cadence bound holds on the row, and an era cannot exist without its
     owner's schedule row.
  4. **The migration, both ways.**  The downgrade restores the LATEST era's
     rhythm onto the row and refuses a row holding no era; the upgrade's
     backfill states one era per owner from the row's own rhythm -- for a
     piecewise owner that is the row's own wrong claim, stated rather than
     guessed at -- and admits a recorded payday off that grid, which ruling
     R-PC47 says is a fact and not an error.  Driven through the revision's
     own callables, never a hand-written copy.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, text
from sqlalchemy.exc import IntegrityError

from app.models.pay_era import CADENCE_DAYS_MAX, CADENCE_DAYS_MIN, PayEra
from app.models.pay_schedule import PaySchedule
from app.models.user import User, UserSettings
from app.services import pay_era_write, pay_period_write, pay_schedule_service
from app.services.auth_service import hash_password
from tests._test_helpers import (
    era_of,
    load_migration_module,
    rhythm_of,
    run_migration_callable as _run,
    shift_id_of,
)

#: This revision, loaded so its own shipped callables are what this file
#: drives.  Hand-written DDL standing in for them would be a second statement
#: of the migration that could drift from it without failing anything.
_M_C17A = load_migration_module(
    "6fc77e86d76f_a_pay_schedule_is_a_sequence_of_eras.py",
)

#: Constraint names, named once so the introspection assertions below compare
#: against variables rather than inline literals.
_UQ_EFFECTIVE_FROM = "uq_pay_eras_user_effective_from"
_CK_CADENCE = "ck_pay_eras_cadence_range"
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
                _UQ_EFFECTIVE_FROM, _CK_CADENCE, _FK_SCHEDULE, _FK_KIND,
                _FK_SHIFT,
            ):
                assert name in live, f"{name} is not installed"
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
                "id", "effective_from", "kind_id", "cadence_days", "shift_id",
                "user_id", "created_at",
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

    def test_the_kind_vocabulary_is_seeded(self, app, db):
        """``fixed_days`` exists, seeded by the migration and by the reseed."""
        with app.app_context():
            assert db.session.execute(text(
                "SELECT count(*) FROM ref.pay_cadence_kinds"
            )).scalar() == 1


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


class TestConstraintBehaviour:
    """The DB refuses two eras on one day, a bad cadence, and an orphan era."""

    @staticmethod
    def _row(user_id, **overrides):
        """A legal era row for *user_id*, with *overrides* applied."""
        fields = {
            "user_id": user_id, "effective_from": date(2026, 1, 2),
            "kind_id": _fixed_days_id(), "cadence_days": 14,
            "shift_id": shift_id_of(),
        }
        fields.update(overrides)
        return PayEra(**fields)

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


def _fixed_days_id():
    """Return ``ref.pay_cadence_kinds``' id for the one seeded member."""
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import PayCadenceKindEnum

    return ref_cache.pay_cadence_kind_id(PayCadenceKindEnum.FIXED_DAYS)


class TestTheMigrationBothWays:
    """The revision's own ``downgrade`` and ``upgrade``, driven with owners in place.

    The per-worker database starts at head, so every case runs the
    ``downgrade`` first -- which is the only way to reach the schema the
    ``upgrade`` reads -- and reads the rewound row by SQL, because the head
    mapper no longer selects its rhythm columns and does select the table the
    downgrade drops.  What it does NOT do is put the database at this
    revision's parent: everything else stays at head, which is enough for a
    statement whose subject is these two tables (ledger row **balance:P79**
    owns the general shape).
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
        pay_period_write.record_paydays(
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

            with pytest.raises(RuntimeError, match="hold no era"):
                _run(_M_C17A.downgrade, db.session)
            db.session.rollback()

            assert _table_exists(db.session, "budget.pay_eras")
            assert "cadence_days" not in _columns(db.session, "pay_schedule")

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
            _run(_M_C17A.downgrade, db.session)
            db.session.execute(text(
                "UPDATE budget.pay_schedule SET nominal_anchor = :anchor "
                " WHERE user_id = :uid"
            ), {"anchor": date(2026, 1, 30), "uid": user.id})
            db.session.commit()

            _run(_M_C17A.upgrade, db.session)

            assert _era_rows(db.session, user.id) == [(date(2026, 1, 2), 14)]
            assert "cadence_days" not in _columns(db.session, "pay_schedule")
            assert pay_schedule_service.resolve_cadence(user.id) == 14

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
        pay_period_write.record_paydays(
            user_id=user.id, first_payday=date(2026, 2, 20), num_periods=1,
            rhythm=rhythm_of(7),
        )
        db.session.commit()

        with app.app_context():
            _run(_M_C17A.downgrade, db.session)
            assert _rewound_row(db.session, user.id) == (7, date(2026, 2, 20))

            _run(_M_C17A.upgrade, db.session)

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
            _run(_M_C17A.downgrade, db.session)
            db.session.execute(text(
                "UPDATE budget.pay_periods SET start_date = :moved "
                " WHERE user_id = :uid AND start_date = :was"
            ), {"moved": date(2026, 1, 31), "uid": user.id,
                "was": date(2026, 1, 30)})
            db.session.commit()

            _run(_M_C17A.upgrade, db.session)

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
