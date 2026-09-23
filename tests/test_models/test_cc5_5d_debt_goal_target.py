"""Tests for migration ``764461215480`` (plan step credit_card:CC-5-5d).

Ruling R-CC72: a DEBT goal may target ``$0.00``, so ``budget.savings_goals``'
target CHECK is ``target_amount >= 0`` (``ck_savings_goals_nonnegative_target``)
where it was ``> 0``; the "above zero for a SAVINGS goal" half is the goal
door's.  Ruling R-CC91: a card goal's recorded start, ``start_owed``, nullable
and ``> 0`` when set.  The upgrade REFUSES while an active goal sits on a
non-loan debt with no start it could record; the downgrade REFUSES while a
``$0.00`` goal exists; both name the rows rather than rewrite them.
"""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.savings_goal import SavingsGoal
from tests._test_helpers import (
    constraint_name_from,
    load_migration_module,
    run_migration_callable,
)

_MIGRATION = "764461215480_debt_goals.py"


def _start_owed_column_exists():
    """Whether ``budget.savings_goals.start_owed`` exists."""
    return db.session.execute(text(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = 'budget' AND table_name = 'savings_goals' "
        "AND column_name = 'start_owed'"
    )).scalar() == 1


def _target_checks():
    """Return ``{name: definition}`` of the table's target CHECKs."""
    rows = db.session.execute(text(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'budget.savings_goals'::regclass AND contype = 'c' "
        "AND conname LIKE 'ck_savings_goals_%_target'"
    )).fetchall()
    return {name: definition for name, definition in rows}


def _goal(seed_user, target, name="Goal"):
    """Insert a goal on the seed Checking with *target*, bypassing the door."""
    goal = SavingsGoal(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        name=name,
        target_amount=target,
    )
    db.session.add(goal)
    db.session.commit()
    return goal


class TestTheTargetCheck:
    """The table holds the bound every goal shares: never negative."""

    def test_zero_is_stored_and_a_negative_target_is_refused(
        self, app, db, seed_user,
    ):
        """``$0.00`` stores; ``-$0.01`` raises on the renamed CHECK."""
        with app.app_context():
            assert _goal(seed_user, Decimal("0.00")).target_amount == Decimal("0.00")

            db.session.add(SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="Below zero",
                target_amount=Decimal("-0.01"),
            ))
            with pytest.raises(IntegrityError) as excinfo:
                db.session.commit()
            db.session.rollback()
            assert constraint_name_from(excinfo.value) == (
                "ck_savings_goals_nonnegative_target"
            )


class TestTheDowngrade:
    """Restores ``> 0`` exactly, and only when no goal targets ``$0.00``."""

    def test_it_refuses_while_a_zero_goal_exists_and_restores_without(
        self, app, db, seed_user,
    ):
        """Refused naming the goal; after it goes, down and up round-trip."""
        with app.app_context():
            migration = load_migration_module(_MIGRATION)
            zero_goal = _goal(seed_user, Decimal("0.00"), name="Paid off")

            with pytest.raises(RuntimeError, match="Paid off"):
                run_migration_callable(migration.downgrade, db.session)
            db.session.rollback()
            assert set(_target_checks()) == {"ck_savings_goals_nonnegative_target"}

            db.session.delete(zero_goal)
            db.session.commit()
            run_migration_callable(migration.downgrade, db.session)
            assert _target_checks() == {
                "ck_savings_goals_positive_target": "CHECK ((target_amount > (0)::numeric))",
            }
            assert not _start_owed_column_exists()

            run_migration_callable(migration.upgrade, db.session)
            assert _target_checks() == {
                "ck_savings_goals_nonnegative_target": "CHECK ((target_amount >= (0)::numeric))",
            }
            assert _start_owed_column_exists()


class TestTheUpgrade:
    """Refuses an active card goal whose start it cannot know."""

    @pytest.mark.parametrize("type_name", ["Credit Card", "Auto Loan"])
    def test_an_active_card_goal_without_a_start_refuses_the_upgrade(
        self, app, db, seed_user, type_name,
    ):
        """Downgraded, a goal is put on a card (or a loan type with no terms,
        R-CC93); the upgrade names it and writes nothing."""
        with app.app_context():
            migration = load_migration_module(_MIGRATION)
            run_migration_callable(migration.downgrade, db.session)
            card_type_id = db.session.execute(text(
                "SELECT id FROM ref.account_types "
                "WHERE name = :name AND user_id IS NULL"
            ), {"name": type_name}).scalar()
            card_id = db.session.execute(text(
                "INSERT INTO budget.accounts (user_id, account_type_id, name) "
                "VALUES (:user_id, :type_id, 'Old Card') RETURNING id"
            ), {"user_id": seed_user["user"].id, "type_id": card_type_id}).scalar()
            db.session.execute(text(
                "INSERT INTO budget.savings_goals "
                "(user_id, account_id, name, target_amount) "
                "VALUES (:user_id, :account_id, 'Card down', 100)"
            ), {"user_id": seed_user["user"].id, "account_id": card_id})
            db.session.commit()

            with pytest.raises(RuntimeError, match="Card down"):
                run_migration_callable(migration.upgrade, db.session)
            db.session.rollback()
            assert not _start_owed_column_exists()
            assert set(_target_checks()) == {"ck_savings_goals_positive_target"}

    @pytest.mark.parametrize("mode_name, contribution, refused", [
        ("Income-Relative", None, True),
        ("Fixed", 50, True),
        ("Fixed", None, False),
    ])
    def test_a_configured_loans_goal_is_judged_by_its_shape(
        self, app, db, seed_user, mode_name, contribution, refused,
    ):
        """On a CONFIGURED loan the upgrade refuses only a goal no save could clear.

        An income-relative mode or a per-period contribution (R-CC69's premise,
        R-CC90) is refused; a Fixed goal with no contribution is admitted.
        """
        # pylint: disable=import-outside-toplevel
        from datetime import date
        from tests._test_helpers import create_loan_account
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Car Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.00000"),
                term=24, origination_date=date(2026, 1, 1),
            )
            db.session.commit()
            migration = load_migration_module(_MIGRATION)
            run_migration_callable(migration.downgrade, db.session)
            _insert_goal(seed_user, loan.id, "Loan down", mode_name, contribution)

            if refused:
                with pytest.raises(RuntimeError, match="Loan down"):
                    run_migration_callable(migration.upgrade, db.session)
                db.session.rollback()
                assert not _start_owed_column_exists()
            else:
                run_migration_callable(migration.upgrade, db.session)
                assert _start_owed_column_exists()

    def test_a_savings_goal_of_any_shape_is_admitted(self, app, db, seed_user):
        """The shape clauses bind to DEBT goals only (the parentheses)."""
        with app.app_context():
            migration = load_migration_module(_MIGRATION)
            run_migration_callable(migration.downgrade, db.session)
            _insert_goal(
                seed_user, seed_user["account"].id, "Save", "Income-Relative", 50,
            )

            run_migration_callable(migration.upgrade, db.session)
            assert _start_owed_column_exists()


def _insert_goal(seed_user, account_id, name, mode_name, contribution):
    """Insert a goal by SQL on the downgraded table (no ORM ``start_owed``)."""
    db.session.execute(text(
        "INSERT INTO budget.savings_goals "
        "(user_id, account_id, name, target_amount, goal_mode_id, "
        "income_unit_id, income_multiplier, contribution_per_period) "
        "SELECT :user_id, :account_id, :name, "
        "CASE WHEN m.name = 'Fixed' THEN 100 END, m.id, "
        "CASE WHEN m.name = 'Fixed' THEN NULL ELSE "
        "(SELECT id FROM ref.income_units ORDER BY id LIMIT 1) END, "
        "CASE WHEN m.name = 'Fixed' THEN NULL ELSE 3 END, :contribution "
        "FROM ref.goal_modes m WHERE m.name = :mode"
    ), {
        "user_id": seed_user["user"].id, "account_id": account_id,
        "name": name, "mode": mode_name, "contribution": contribution,
    })
    db.session.commit()
