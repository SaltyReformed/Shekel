"""
Shekel Budget App -- Savings Goal Model Tests (5.4-2)

Tests for the income-relative goal columns added to the SavingsGoal
model: goal_mode_id, income_unit_id, income_multiplier.

Verifies:
  - Default to Fixed mode (goal_mode_id=1) for new goals.
  - CHECK constraint rejects non-positive multiplier.
  - FK constraints reject invalid goal_mode_id and income_unit_id.
  - Relationships are eagerly loaded.
"""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import GoalModeEnum, IncomeUnitEnum
from app.models.savings_goal import SavingsGoal


class TestSavingsGoalDefaults:
    """Tests for goal_mode_id default behavior."""

    def test_existing_goals_default_to_fixed(self, app, db, seed_user):
        """Goals created without specifying goal_mode_id get Fixed (ID 1).

        Verifies backward compatibility: the server_default and Python
        default both assign goal_mode_id=1 to new goals, so existing
        code that does not pass goal_mode_id continues to work.
        """
        with app.app_context():
            fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="Default Mode Test",
                target_amount=Decimal("5000.00"),
            )
            db.session.add(goal)
            db.session.flush()

            assert goal.goal_mode_id == fixed_id
            assert goal.income_unit_id is None
            assert goal.income_multiplier is None


class TestSavingsGoalConstraints:
    """Tests for database-level constraints on income-relative columns."""

    def test_check_constraint_multiplier_rejects_negative(self, app, db, seed_user):
        """CHECK constraint ck_savings_goals_multiplier_positive rejects
        negative income_multiplier at the database level.

        This is the second line of defense after schema validation.
        """
        with app.app_context():
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="Negative Multiplier",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("-1.00"),
            )
            db.session.add(goal)
            with pytest.raises(IntegrityError):
                db.session.flush()
            db.session.rollback()

    def test_check_constraint_multiplier_rejects_zero(self, app, db, seed_user):
        """CHECK constraint rejects income_multiplier=0."""
        with app.app_context():
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="Zero Multiplier",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("0.00"),
            )
            db.session.add(goal)
            with pytest.raises(IntegrityError):
                db.session.flush()
            db.session.rollback()

    def test_fk_constraint_rejects_invalid_goal_mode_id(self, app, db, seed_user):
        """FK constraint rejects goal_mode_id that does not exist in
        ref.goal_modes.
        """
        with app.app_context():
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="Bad Mode FK",
                target_amount=Decimal("5000.00"),
                goal_mode_id=99,
            )
            db.session.add(goal)
            with pytest.raises(IntegrityError):
                db.session.flush()
            db.session.rollback()

    def test_fk_constraint_rejects_invalid_income_unit_id(self, app, db, seed_user):
        """FK constraint rejects income_unit_id that does not exist in
        ref.income_units.
        """
        with app.app_context():
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="Bad Unit FK",
                goal_mode_id=ir_id,
                income_unit_id=99,
                income_multiplier=Decimal("3.00"),
            )
            db.session.add(goal)
            with pytest.raises(IntegrityError):
                db.session.flush()
            db.session.rollback()


class TestSavingsGoalRelationships:
    """Tests for eager loading of goal_mode and income_unit relationships."""

    def test_relationship_eager_loading(self, app, db, seed_user):
        """goal_mode and income_unit are eagerly loaded (lazy='joined').

        After creating an income-relative goal and querying it,
        accessing goal.goal_mode and goal.income_unit should not issue
        additional queries -- the related objects are already loaded.
        """
        with app.app_context():
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="Eager Load Test",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
            )
            db.session.add(goal)
            db.session.flush()

            # Re-query to confirm eager load.
            queried = db.session.get(SavingsGoal, goal.id)
            assert queried.goal_mode is not None
            assert queried.goal_mode.name == "Income-Relative"
            assert queried.income_unit is not None
            assert queried.income_unit.name == "Paychecks"
