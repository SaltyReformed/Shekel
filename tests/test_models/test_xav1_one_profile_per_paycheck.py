"""Tests for migration ``9b2c5656eed9`` (plan step salary:X-av-1).

Ruling R-SAL63, "One profile per paycheck", as scoped by R-SAL69, "Per
scenario": within a scenario a paycheck definition belongs to at most one
salary profile, active or not (``uq_salary_profiles_scenario_template``),
closing finding N-294 -- two profiles on one template in a scenario gave the
amount model's ``{template_id: profile}`` map two candidates for one key.
Another scenario may name the same template (a what-if salary).  NULLs stay
distinct, because a template's hard delete sets ``template_id`` NULL.  The
upgrade REFUSES while data holds the state, naming it, and writes nothing; the
downgrade drops the rule.
"""

import pytest
from sqlalchemy import UniqueConstraint, text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.scenario import Scenario
from tests._test_helpers import (
    constraint_name_from,
    load_migration_module,
    make_income_template,
    make_salary_profile,
    run_migration_callable,
)

_MIGRATION = "9b2c5656eed9_one_salary_profile_per_paycheck.py"
_CONSTRAINT = "uq_salary_profiles_scenario_template"
_DEFINITION = "UNIQUE (scenario_id, template_id)"


def _template_rule():
    """Return the rule's definition as PostgreSQL states it, or ``None``."""
    return db.session.execute(text(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'salary.salary_profiles'::regclass "
        "AND conname = :name"
    ), {"name": _CONSTRAINT}).scalar()


def _profile_on(seed_user, template, name, *, is_active=True, scenario=None):
    """Add (uncommitted) a profile named *name* on *template*, or on none.

    It lands in the seed user's baseline scenario unless *scenario* is given.
    """
    profile = make_salary_profile(seed_user, db.session, name=name)
    profile.template_id = None if template is None else template.id
    profile.is_active = is_active
    if scenario is not None:
        profile.scenario_id = scenario.id
    return profile


def _what_if_scenario(seed_user):
    """Add and flush a non-baseline scenario for the seed user."""
    scenario = Scenario(
        user_id=seed_user["user"].id, name="What-if", is_baseline=False,
    )
    db.session.add(scenario)
    db.session.flush()
    return scenario


class TestTheRule:
    """The table refuses a second profile on one definition in one scenario."""

    def test_the_model_declares_what_the_migration_adds(self):
        """The model states the rule over the same two columns, NULLs distinct.

        A fresh database is built from the MODELS
        (``scripts/init_database.init_fresh_database``'s ``create_all``, then
        a stamp) while an existing one is upgraded by the migration, so both
        must state it identically: finding F-069 was exactly such a
        model/migration drift.  The NULLs clause is part of the rule, and the
        test database is built by the migration, so only this assertion would
        catch the model alone gaining ``NULLS NOT DISTINCT``.
        """
        declared = {
            constraint.name: constraint
            for constraint in SalaryProfile.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        rule = declared[_CONSTRAINT]
        assert [column.name for column in rule.columns] == [
            "scenario_id", "template_id",
        ]
        assert not rule.dialect_options["postgresql"]["nulls_not_distinct"]

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

    def test_another_scenario_may_name_the_same_template(
        self, app, db, seed_user, seed_periods,
    ):
        """A what-if scenario's profile on the baseline's template stores (R-SAL69)."""
        with app.app_context():
            template = make_income_template(db.session, seed_user)
            _profile_on(seed_user, template, "Day job")
            _profile_on(
                seed_user, template, "Day job, what-if",
                scenario=_what_if_scenario(seed_user),
            )
            db.session.commit()

            names = sorted(
                row.name for row in db.session.query(SalaryProfile.name)
                .filter(SalaryProfile.template_id == template.id)
            )
            assert names == ["Day job", "Day job, what-if"]

    def test_profiles_that_lost_their_template_all_store(
        self, app, db, seed_user,
    ):
        """Two profiles with no template in one scenario both store: NULLs stay distinct."""
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
    """Refuses while a scenario's profiles share a definition; adds the rule once none do."""

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
                f"scenario {seed_user['scenario'].id}, template {template.id}: "
                f"profiles {first.id} (Day job), {second.id} (Second job)"
            )

            with pytest.raises(RuntimeError) as excinfo:
                run_migration_callable(migration.upgrade, db.session)
            db.session.rollback()
            assert expected in str(excinfo.value)
            assert _template_rule() is None

            second.template_id = None
            db.session.commit()
            run_migration_callable(migration.upgrade, db.session)
            assert _template_rule() == _DEFINITION

    def test_the_refusal_names_every_shared_template(
        self, app, db, seed_user, seed_periods,
    ):
        """Two shared templates, one under three profiles: each named, in order."""
        with app.app_context():
            migration = load_migration_module(_MIGRATION)
            day = make_income_template(db.session, seed_user, name="Day pay")
            side = make_income_template(db.session, seed_user, name="Side pay")
            db.session.commit()
            run_migration_callable(migration.downgrade, db.session)

            a = _profile_on(seed_user, day, "A")
            b = _profile_on(seed_user, day, "B")
            c = _profile_on(seed_user, day, "C")
            d = _profile_on(seed_user, side, "D")
            e = _profile_on(seed_user, side, "E")
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            expected = (
                f"scenario {scenario_id}, template {day.id}: profiles "
                f"{a.id} (A), {b.id} (B), {c.id} (C); "
                f"scenario {scenario_id}, template {side.id}: profiles "
                f"{d.id} (D), {e.id} (E)."
            )

            with pytest.raises(RuntimeError) as excinfo:
                run_migration_callable(migration.upgrade, db.session)
            db.session.rollback()
            assert expected in str(excinfo.value)

    def test_one_template_across_two_scenarios_does_not_refuse(
        self, app, db, seed_user, seed_periods,
    ):
        """A template named once in each of two scenarios is not shared (R-SAL69)."""
        with app.app_context():
            migration = load_migration_module(_MIGRATION)
            template = make_income_template(db.session, seed_user)
            db.session.commit()
            run_migration_callable(migration.downgrade, db.session)

            _profile_on(seed_user, template, "Day job")
            _profile_on(
                seed_user, template, "Day job, what-if",
                scenario=_what_if_scenario(seed_user),
            )
            db.session.commit()

            run_migration_callable(migration.upgrade, db.session)
            assert _template_rule() == _DEFINITION

    def test_profiles_with_no_template_do_not_refuse_the_upgrade(
        self, app, db, seed_user,
    ):
        """Two template-less profiles in one scenario share nothing: the rule is added.

        A template's hard delete sets ``template_id`` NULL, so the state is
        reachable, and the refusal's ``template_id IS NOT NULL`` clause is
        what keeps it from failing a deploy.
        """
        with app.app_context():
            migration = load_migration_module(_MIGRATION)
            run_migration_callable(migration.downgrade, db.session)

            _profile_on(seed_user, None, "Old job")
            _profile_on(seed_user, None, "Older job")
            db.session.commit()

            run_migration_callable(migration.upgrade, db.session)
            assert _template_rule() == _DEFINITION


class TestTheDowngrade:
    """Drops the rule; the upgrade restores it exactly."""

    def test_the_round_trip_drops_and_restores_the_rule(self, app, db):
        """Head holds the rule; down removes it; up puts it back."""
        with app.app_context():
            migration = load_migration_module(_MIGRATION)
            assert _template_rule() == _DEFINITION

            run_migration_callable(migration.downgrade, db.session)
            assert _template_rule() is None

            run_migration_callable(migration.upgrade, db.session)
            assert _template_rule() == _DEFINITION
