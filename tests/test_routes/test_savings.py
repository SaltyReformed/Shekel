"""
Shekel Budget App — Savings Route Tests

Tests for the savings dashboard and goal CRUD endpoints:
  - Dashboard rendering (with/without accounts, goals)
  - Goal creation (happy path, validation, IDOR)
  - Goal editing (happy path, IDOR)
  - Goal deletion (soft-deactivate, IDOR)
  - Double-submit (unique constraint on user+account+name)
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.ref import AccountType
from app.models.savings_goal import SavingsGoal
from app.models.scenario import Scenario
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password


# ── Helpers ──────────────────────────────────────────────────────────


def _create_savings_account(seed_user):
    """Create a savings account for the test user.

    Returns:
        Account: the new savings account.
    """
    savings_type = db.session.query(AccountType).filter_by(name="savings").one()
    acct = Account(
        user_id=seed_user["user"].id,
        account_type_id=savings_type.id,
        name="Savings",
        current_anchor_balance=Decimal("5000.00"),
    )
    db.session.add(acct)
    db.session.flush()
    return acct


def _create_goal(seed_user, account, name="Vacation Fund",
                 target_amount=Decimal("10000.00"), target_date=None):
    """Create a savings goal for the test user.

    Returns:
        SavingsGoal: the new goal.
    """
    goal = SavingsGoal(
        user_id=seed_user["user"].id,
        account_id=account.id,
        name=name,
        target_amount=target_amount,
        target_date=target_date or date(2027, 6, 1),
    )
    db.session.add(goal)
    db.session.commit()
    return goal


def _create_other_user_with_goal():
    """Create a second user with a savings account and goal.

    Returns:
        dict with keys: user, account, goal.
    """
    other_user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db.session.add(other_user)
    db.session.flush()

    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    savings_type = db.session.query(AccountType).filter_by(name="savings").one()
    account = Account(
        user_id=other_user.id,
        account_type_id=savings_type.id,
        name="Other Savings",
        current_anchor_balance=Decimal("2000.00"),
    )
    db.session.add(account)

    scenario = Scenario(
        user_id=other_user.id, name="Baseline", is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    goal = SavingsGoal(
        user_id=other_user.id,
        account_id=account.id,
        name="Other Goal",
        target_amount=Decimal("5000.00"),
        target_date=date(2027, 1, 1),
    )
    db.session.add(goal)
    db.session.commit()

    return {"user": other_user, "account": account, "goal": goal}


def _create_investment_account_with_params(seed_user, seed_periods):
    """Create a 401k account with investment params and anchor period.

    Returns:
        (Account, InvestmentParams)
    """
    acct_type = db.session.query(AccountType).filter_by(name="401k").one()
    acct = Account(
        user_id=seed_user["user"].id,
        account_type_id=acct_type.id,
        name="Test 401k",
        current_anchor_balance=Decimal("50000.00"),
        current_anchor_period_id=seed_periods[0].id,
    )
    db.session.add(acct)
    db.session.flush()

    params = InvestmentParams(
        account_id=acct.id,
        assumed_annual_return=Decimal("0.07000"),
        annual_contribution_limit=Decimal("23500.00"),
        contribution_limit_year=2026,
        employer_contribution_type="none",
    )
    db.session.add(params)
    db.session.commit()
    return acct, params


# ── Dashboard Tests ──────────────────────────────────────────────────


class TestDashboard:
    """Tests for GET /savings — the savings dashboard."""

    def test_dashboard_renders(self, app, auth_client, seed_user, seed_periods):
        """Dashboard renders successfully with accounts and periods."""
        with app.app_context():
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"savings" in resp.data.lower() or b"Savings" in resp.data

    def test_dashboard_no_savings_accounts(self, app, auth_client, seed_user, seed_periods):
        """Dashboard renders even when user has no savings-type accounts."""
        with app.app_context():
            # seed_user only has a checking account — no savings accounts.
            resp = auth_client.get("/savings")
            assert resp.status_code == 200

    def test_dashboard_with_goals(self, app, auth_client, seed_user, seed_periods):
        """Dashboard displays savings goals when they exist."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            _create_goal(seed_user, acct)

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Vacation Fund" in resp.data

    def test_dashboard_no_goals(self, app, auth_client, seed_user, seed_periods):
        """Dashboard renders account projections even with no goals."""
        with app.app_context():
            _create_savings_account(seed_user)

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            # Should show savings account even without goals.
            assert b"Savings" in resp.data

    def test_dashboard_investment_account_shows_growth_projections(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Investment account cards show projected balances with compound growth."""
        import re

        with app.app_context():
            acct, params = _create_investment_account_with_params(
                seed_user, seed_periods,
            )

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()

            # With 7% annual return on $50k, the 1-year projection should
            # be notably higher than $50,000. If growth is NOT applied,
            # the balance stays flat at $50,000 (the bug).
            amounts = re.findall(r'\$([0-9,]+)', html)
            amounts_int = [
                int(a.replace(',', ''))
                for a in amounts
                if int(a.replace(',', '')) > 50000
            ]

            # With 7% growth, at least one projected amount should exceed $50,000.
            assert len(amounts_int) > 0, (
                "Expected at least one projected amount > $50,000 with 7% growth, "
                "but all amounts were <= $50,000. Growth is not being applied."
            )

    def test_dashboard_requires_login(self, app, client, seed_user):
        """Unauthenticated request redirects to login."""
        with app.app_context():
            resp = client.get("/savings", follow_redirects=False)
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]


# ── Goal Create Tests ────────────────────────────────────────────────


class TestGoalCreate:
    """Tests for GET /savings/goals/new and POST /savings/goals."""

    def test_new_goal_form(self, app, auth_client, seed_user):
        """GET /savings/goals/new renders the goal creation form."""
        with app.app_context():
            resp = auth_client.get("/savings/goals/new")
            assert resp.status_code == 200

    def test_create_goal_success(self, app, auth_client, seed_user, seed_periods):
        """POST /savings/goals creates a goal and redirects to dashboard."""
        with app.app_context():
            acct = _create_savings_account(seed_user)

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "New Car",
                "target_amount": "15000.00",
                "target_date": "2027-12-31",
                "contribution_per_period": "250.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"New Car" in resp.data
            assert b"created" in resp.data

            # Verify in database.
            goal = db.session.query(SavingsGoal).filter_by(name="New Car").one()
            assert goal.target_amount == Decimal("15000.00")
            assert goal.account_id == acct.id

    def test_create_goal_validation_error(self, app, auth_client, seed_user):
        """POST /savings/goals with missing required fields shows error."""
        with app.app_context():
            resp = auth_client.post("/savings/goals", data={
                # Missing name, target_amount, account_id.
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Validation error" in resp.data

    def test_create_goal_invalid_account(self, app, auth_client, seed_user):
        """POST /savings/goals with another user's account is rejected."""
        with app.app_context():
            other = _create_other_user_with_goal()

            resp = auth_client.post("/savings/goals", data={
                "account_id": other["account"].id,
                "name": "Sneaky Goal",
                "target_amount": "5000.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid account" in resp.data

            # Verify goal was NOT created.
            goal = db.session.query(SavingsGoal).filter_by(name="Sneaky Goal").first()
            assert goal is None

    def test_create_goal_without_optional_fields(self, app, auth_client, seed_user):
        """POST /savings/goals succeeds without target_date and contribution."""
        with app.app_context():
            acct = _create_savings_account(seed_user)

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "Rainy Day",
                "target_amount": "1000.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data

            goal = db.session.query(SavingsGoal).filter_by(name="Rainy Day").one()
            assert goal.target_date is None
            assert goal.contribution_per_period is None


# ── Goal Update Tests ────────────────────────────────────────────────


class TestGoalUpdate:
    """Tests for GET /savings/goals/<id>/edit and POST /savings/goals/<id>."""

    def test_edit_goal_form(self, app, auth_client, seed_user):
        """GET /savings/goals/<id>/edit renders the edit form."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            goal = _create_goal(seed_user, acct)

            resp = auth_client.get(f"/savings/goals/{goal.id}/edit")
            assert resp.status_code == 200
            assert b"Vacation Fund" in resp.data

    def test_update_goal_success(self, app, auth_client, seed_user, seed_periods):
        """POST /savings/goals/<id> updates goal fields."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            goal = _create_goal(seed_user, acct)

            resp = auth_client.post(f"/savings/goals/{goal.id}", data={
                "name": "Updated Fund",
                "target_amount": "20000.00",
                "target_date": "2028-01-01",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(goal)
            assert goal.name == "Updated Fund"
            assert goal.target_amount == Decimal("20000.00")

    def test_update_goal_validation_error(self, app, auth_client, seed_user):
        """POST /savings/goals/<id> with invalid data shows error."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            goal = _create_goal(seed_user, acct)

            resp = auth_client.post(f"/savings/goals/{goal.id}", data={
                "target_amount": "-100.00",  # Negative — fails Range validator.
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Validation error" in resp.data

    def test_edit_goal_idor(self, app, auth_client, seed_user):
        """GET /savings/goals/<id>/edit for another user's goal redirects."""
        with app.app_context():
            other = _create_other_user_with_goal()

            resp = auth_client.get(
                f"/savings/goals/{other['goal'].id}/edit",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Goal not found" in resp.data

    def test_update_goal_idor(self, app, auth_client, seed_user):
        """POST /savings/goals/<id> for another user's goal redirects."""
        with app.app_context():
            other = _create_other_user_with_goal()

            resp = auth_client.post(
                f"/savings/goals/{other['goal'].id}",
                data={"name": "Hijacked"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Goal not found" in resp.data

            # Verify original goal unchanged.
            db.session.refresh(other["goal"])
            assert other["goal"].name == "Other Goal"


# ── Goal Delete Tests ────────────────────────────────────────────────


class TestGoalDelete:
    """Tests for POST /savings/goals/<id>/delete."""

    def test_delete_goal_success(self, app, auth_client, seed_user, seed_periods):
        """POST /savings/goals/<id>/delete soft-deactivates the goal."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            goal = _create_goal(seed_user, acct)

            resp = auth_client.post(
                f"/savings/goals/{goal.id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"deactivated" in resp.data

            db.session.refresh(goal)
            assert goal.is_active is False

    def test_delete_goal_idor(self, app, auth_client, seed_user):
        """POST /savings/goals/<id>/delete for another user's goal redirects."""
        with app.app_context():
            other = _create_other_user_with_goal()

            resp = auth_client.post(
                f"/savings/goals/{other['goal'].id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Goal not found" in resp.data

            # Verify goal still active.
            db.session.refresh(other["goal"])
            assert other["goal"].is_active is True

    def test_delete_nonexistent_goal(self, app, auth_client, seed_user):
        """POST /savings/goals/999999/delete for missing goal redirects."""
        with app.app_context():
            resp = auth_client.post(
                "/savings/goals/999999/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Goal not found" in resp.data


# ── Double Submit / Unique Constraint ────────────────────────────────


class TestGoalIdempotency:
    """Tests for unique constraint on savings goals."""

    def test_duplicate_goal_name_same_account(self, app, auth_client, seed_user):
        """Creating two goals with the same name+account raises IntegrityError."""
        from sqlalchemy.exc import IntegrityError
        import pytest

        with app.app_context():
            acct = _create_savings_account(seed_user)
            _create_goal(seed_user, acct, name="Emergency Fund")

            # Second goal with same name+account should violate unique constraint.
            # The route doesn't catch IntegrityError, so it bubbles up.
            with pytest.raises(IntegrityError, match="uq_savings_goals_user_acct_name"):
                auth_client.post("/savings/goals", data={
                    "account_id": acct.id,
                    "name": "Emergency Fund",
                    "target_amount": "5000.00",
                })
            db.session.rollback()
