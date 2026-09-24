"""Tests for migration ``9b2c5656eed9`` (plan step salary:X-av-1).

Ruling R-SAL63, "One profile per paycheck": a paycheck definition belongs to
at most one salary profile, active or not (``uq_salary_profiles_template_id``),
closing finding N-294 -- two profiles on one template left the amount model's
``{template_id: profile}`` map with two answers for one paycheck row.  NULLs
stay distinct, because a template's hard delete sets ``template_id`` NULL.  The
upgrade REFUSES while data holds the state, naming it, and writes nothing; the
downgrade drops the rule.
"""

import pytest
from sqlalchemy import UniqueConstraint, text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.salary_profile import SalaryProfile
from tests._test_helpers import (
    constraint_name_from,
    load_migration_module,
    make_income_template,
    make_salary_profile,
    run_migration_callable,
)

_MIGRATION = "9b2c5656eed9_one_salary_profile_per_paycheck.py"
_CONSTRAINT = "uq_salary_profiles_template_id"


def _template_rule():
    """Return the rule's definition as PostgreSQL states it, or ``None``."""
    return db.session.execute(text(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'salary.salary_profiles'::regclass "
        "AND conname = :name"
    ), {"name": _CONSTRAINT}).scalar()


def _profile_on(seed_user, template, name, *, is_active=True):
    """Add (uncommitted) a profile named *name* on *template*, or on none."""
    profile = make_salary_profile(seed_user, db.session, name=name)
    profile.template_id = None if template is None else template.id
    profile.is_active = is_active
    return profile


class TestTheRule:
    """The table refuses a second profile on one definition, and only that."""

    def test_the_model_declares_what_the_migration_adds(self):
        """The model states the rule over ``template_id`` alone, by its name.

        A fresh database is built from the MODELS
        (``scripts/init_database.init_fresh_database``'s ``create_all``, then
        a stamp) while an existing one is upgraded by the migration, so both
        must state it: finding F-069 was exactly such a model/migration drift.
        """
        declared = {
            constraint.name: [column.name for column in constraint.columns]
            for constraint in SalaryProfile.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        assert declared[_CONSTRAINT] == ["template_id"]

    @pytest.mark.parametrize("second_is_active", [True, False])
    def test_a_second_profile_on_one_template_is_refused(
        self, app, db, seed_user, seed_periods, second_is_active,
    ):
        """Active or archived, the second profile is refused by the rule's name.

        R-SAL63 is "active or not": an archived profile keeps its template, so
        reactivating it must never meet a second profile on that definition.
        """
        with app.app_context():
            template = make_income_template(db.session, seed_user)
            _profile_on(seed_user, template, "Day job")
            db.session.commit()

            _profile_on(
                seed_user, template, "Second job", is_active=second_is_active,
            )
            with pytest.raises(IntegrityError) as excinfo:
                db.session.commit()
            db.session.rollback()
            assert constraint_name_from(excinfo.value) == _CONSTRAINT

    def test_profiles_that_lost_their_template_all_store(
        self, app, db, seed_user,
    ):
        """Two profiles with no template both store: NULLs stay distinct."""
        with app.app_context():
            _profile_on(seed_user, None, "Old job")
            _profile_on(seed_user, None, "Older job")
            db.session.commit()

            stored = (
                db.session.query(SalaryProfile.name)
                .filter(
                    SalaryProfile.user_id == seed_user["user"].id,
                    SalaryProfile.template_id.is_(None),
                )
                .order_by(SalaryProfile.name)
                .all()
            )
            assert [row.name for row in stored] == ["Old job", "Older job"]


class TestTheUpgrade:
    """Refuses while two profiles share a definition; adds the rule once none do."""

    def test_a_shared_template_refuses_the_upgrade_and_writes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """Downgraded, two profiles share a template; the upgrade names both.

        Nothing is written by the refusal (the rule is still absent after it);
        pointing one profile at no template lets the same upgrade add the rule.
        """
        with app.app_context():
            migration = load_migration_module(_MIGRATION)
            template = make_income_template(db.session, seed_user)
            db.session.commit()
            run_migration_callable(migration.downgrade, db.session)
            assert _template_rule() is None

            first = _profile_on(seed_user, template, "Day job")
            second = _profile_on(
                seed_user, template, "Second job", is_active=False,
            )
            db.session.commit()
            expected = (
                f"template {template.id}: profiles "
                f"{first.id} (Day job), {second.id} (Second job)"
            )

            with pytest.raises(RuntimeError) as excinfo:
                run_migration_callable(migration.upgrade, db.session)
            db.session.rollback()
            assert expected in str(excinfo.value)
            assert _template_rule() is None

            second.template_id = None
            db.session.commit()
            run_migration_callable(migration.upgrade, db.session)
            assert _template_rule() == "UNIQUE (template_id)"


class TestTheDowngrade:
    """Drops the rule; the upgrade restores it exactly."""

    def test_the_round_trip_drops_and_restores_the_rule(self, app, db):
        """Head holds the rule; down removes it; up puts it back."""
        with app.app_context():
            migration = load_migration_module(_MIGRATION)
            assert _template_rule() == "UNIQUE (template_id)"

            run_migration_callable(migration.downgrade, db.session)
            assert _template_rule() is None

            run_migration_callable(migration.upgrade, db.session)
            assert _template_rule() == "UNIQUE (template_id)"
