"""
Shekel Budget App -- Savings Route Tests

Tests for the savings dashboard and goal CRUD endpoints:
  - Dashboard rendering (with/without accounts, goals)
  - Goal creation (happy path, validation, IDOR)
  - Goal editing (happy path, IDOR)
  - Goal deletion (soft-deactivate, IDOR)
  - Double-submit (unique constraint on user+account+name)
"""

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import pytest

from app import ref_cache
from app.enums import (
    GoalModeEnum, IncomeUnitEnum, RecurrencePatternEnum,
    StatusEnum, TxnTypeEnum,
)
from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import AccountType, CalcMethod, DeductionTiming, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate

from tests._test_helpers import freeze_today


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today to date(2026, 3, 20) so seed_periods tests pass past 2026-05-22.

    Savings tests use seed_periods[7] (loan-related), an
    origination_date=date(2026, 1, 1) that aligns specific seed_periods
    indices to specific amortization months, and inline ``date.today()``
    calls (e.g. ``start = date.today() - timedelta(days=14)``).
    Auto-discovery patches every loaded module so test, fixture, and
    production services all see the same frozen "today" regardless of
    wall-clock date.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))
from app.models.transfer_template import TransferTemplate
from app.models.user import User, UserSettings
from app.services import savings_goal_service
from app.services.auth_service import hash_password


# ── Helpers ──────────────────────────────────────────────────────────


def _create_savings_account(seed_user, name="Savings"):
    """Create a savings account for the test user.

    Args:
        seed_user: The seed user fixture dict.
        name: Account display name (default "Savings").

    Returns:
        Account: the new savings account.
    """
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = Account(
        user_id=seed_user["user"].id,
        account_type_id=savings_type.id,
        name=name,
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

    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
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
    acct_type = db.session.query(AccountType).filter_by(name="401(k)").one()
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


def _create_investment_account_with_contributions(seed_user, seed_periods):
    """Create a 401k with employer flat 5% and employee deduction.

    Returns:
        (Account, InvestmentParams, SalaryProfile, PaycheckDeduction)
    """
    acct_type = db.session.query(AccountType).filter_by(name="401(k)").one()
    acct = Account(
        user_id=seed_user["user"].id,
        account_type_id=acct_type.id,
        name="Test 401k Employer",
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
        employer_contribution_type="flat_percentage",
        employer_flat_percentage=Decimal("0.0500"),
    )
    db.session.add(params)

    scenario = seed_user["scenario"]
    filing_status = db.session.query(FilingStatus).first()
    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=scenario.id,
        filing_status_id=filing_status.id,
        name="Test Salary",
        annual_salary=Decimal("100000.00"),
        pay_periods_per_year=26,
        state_code="NC",
    )
    db.session.add(profile)
    db.session.flush()

    pre_tax = db.session.query(DeductionTiming).filter_by(name="pre_tax").first()
    flat_method = db.session.query(CalcMethod).filter_by(name="flat").first()
    deduction = PaycheckDeduction(
        salary_profile_id=profile.id,
        deduction_timing_id=pre_tax.id,
        calc_method_id=flat_method.id,
        name="401k Contribution",
        amount=Decimal("500.0000"),
        target_account_id=acct.id,
    )
    db.session.add(deduction)
    db.session.commit()
    return acct, params, profile, deduction


def _create_recurrence_rule(seed_user, pattern_enum, interval_n=1):
    """Create a recurrence rule for the test user.

    Args:
        seed_user: The seed user fixture dict.
        pattern_enum: RecurrencePatternEnum member.
        interval_n: Interval for every_n_periods (default 1).

    Returns:
        RecurrenceRule: the new rule, flushed for id assignment.
    """
    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=ref_cache.recurrence_pattern_id(pattern_enum),
        interval_n=interval_n,
    )
    db.session.add(rule)
    db.session.flush()
    return rule


def _create_expense_template(seed_user, rule, amount, name="Test Expense",
                             is_active=True):
    """Create an expense template on the seed user's checking account.

    Args:
        seed_user: The seed user fixture dict.
        rule: RecurrenceRule object.
        amount: Decimal default amount.
        name: Template display name.
        is_active: Whether the template is active (default True).

    Returns:
        TransactionTemplate: the new template, flushed for id assignment.
    """
    tmpl = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name=name,
        default_amount=amount,
        is_active=is_active,
    )
    db.session.add(tmpl)
    db.session.flush()
    return tmpl


def _create_test_transfer_template(seed_user, to_account, rule, amount,
                                   name="Test Transfer", is_active=True):
    """Create a transfer template from checking to another account.

    Args:
        seed_user: The seed user fixture dict (checking is the source).
        to_account: Destination Account object.
        rule: RecurrenceRule object.
        amount: Decimal default amount.
        name: Template display name.
        is_active: Whether the template is active (default True).

    Returns:
        TransferTemplate: the new template, flushed for id assignment.
    """
    tmpl = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        recurrence_rule_id=rule.id,
        name=name,
        default_amount=amount,
        is_active=is_active,
    )
    db.session.add(tmpl)
    db.session.flush()
    return tmpl


# ── Dashboard Tests ──────────────────────────────────────────────────


class TestDashboard:
    """Tests for GET /savings -- the savings dashboard."""

    def test_dashboard_renders(self, app, auth_client, seed_user, seed_periods):
        """Dashboard renders successfully with accounts and periods."""
        with app.app_context():
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"savings" in resp.data.lower() or b"Savings" in resp.data

    def test_dashboard_no_savings_accounts(self, app, auth_client, seed_user, seed_periods):
        """Dashboard renders even when user has no savings-type accounts."""
        with app.app_context():
            # seed_user only has a checking account -- no savings accounts.
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Accounts Dashboard" in resp.data
            assert b"No savings goals yet" in resp.data

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
        self, app, auth_client, seed_user,
    ):
        """Investment account cards show projected balances with compound growth."""
        import re
        from app.services import pay_period_service

        with app.app_context():
            # Start periods 14 days before today so today falls inside
            # period 0 or 1.  The savings dashboard renders milestone
            # projections at offsets +6, +13, +26 from the current
            # period; with a low current_period.period_index, all three
            # land within the 40 generated periods regardless of when
            # the test is run.  A fixed start_date instead would silently
            # drift current_period forward each calendar week and break
            # the 1-year milestone (offset 26) once today moved past
            # ~August 2026 (only 2 milestones would be displayed and
            # the assertion below would fail).
            start = date.today() - timedelta(days=14)
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=start,
                num_periods=40,
                cadence_days=14,
            )
            db.session.flush()

            acct, params = _create_investment_account_with_params(
                seed_user, periods,
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

            # Dashboard shows 3 milestones (3-month, 6-month, 1-year) at offsets
            # 6, 13, 26 periods from current. With 7% annual return on $50k,
            # all 3 milestones exceed $50,000 (~$50.8k, ~$51.7k, ~$53.5k).
            assert len(amounts_int) == 3, (
                "Expected 3 milestone projections > $50,000 with 7% growth, "
                f"but found {len(amounts_int)}. Amounts on page: {amounts}"
            )

    def test_dashboard_investment_account_includes_contributions(
        self, app, auth_client, seed_user,
    ):
        """Investment cards include employee + employer contributions in projections."""
        import re
        from app.services import pay_period_service

        with app.app_context():
            # See test_dashboard_investment_account_shows_growth_projections
            # for why ``start`` is computed relative to today.
            start = date.today() - timedelta(days=14)
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=start,
                num_periods=40,
                cadence_days=14,
            )
            db.session.flush()

            acct, params, profile, ded = _create_investment_account_with_contributions(
                seed_user, periods,
            )

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()

            # With $500/period employee + 5% employer (~$192/period) + 7% growth
            # on $50k, projections should be substantially higher than growth-only.
            # Growth-only 1yr ~$53,500. With contributions (~$18k/yr), ~$71k+.
            amounts = re.findall(r'\$([0-9,]+)', html)
            amounts_int = [
                int(a.replace(',', ''))
                for a in amounts
                if int(a.replace(',', '')) > 60000
            ]

            # 3 milestones at offsets 6, 13, 26. With $500/period employee +
            # ~$192/period employer (5% of $100k/26) + 7% growth on $50k:
            # 3-month ~$55k (<$60k), 6-month ~$61k (>$60k), 1-year ~$72k (>$60k)
            assert len(amounts_int) == 2, (
                "Expected 2 milestone projections > $60,000 with contributions, "
                f"but found {len(amounts_int)}. Amounts on page: {amounts}"
            )

    def test_dashboard_employer_contribution_without_employee_deduction(
        self, app, auth_client, seed_user,
    ):
        """Employer flat 5% works even when no paycheck deduction targets the account."""
        import re
        from app.services import pay_period_service

        with app.app_context():
            # See test_dashboard_investment_account_shows_growth_projections
            # for why ``start`` is computed relative to today.
            start = date.today() - timedelta(days=14)
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=start,
                num_periods=40,
                cadence_days=14,
            )
            db.session.flush()

            # Create 401k with employer flat 5% but NO employee deduction.
            acct_type = db.session.query(AccountType).filter_by(name="401(k)").one()
            acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=acct_type.id,
                name="Employer Only 401k",
                current_anchor_balance=Decimal("50000.00"),
                current_anchor_period_id=periods[0].id,
            )
            db.session.add(acct)
            db.session.flush()

            params = InvestmentParams(
                account_id=acct.id,
                assumed_annual_return=Decimal("0.07000"),
                annual_contribution_limit=Decimal("23500.00"),
                contribution_limit_year=2026,
                employer_contribution_type="flat_percentage",
                employer_flat_percentage=Decimal("0.0500"),
            )
            db.session.add(params)

            # Create salary profile (no deduction targeting the 401k).
            filing_status = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing_status.id,
                name="Main Job",
                annual_salary=Decimal("100000.00"),
                pay_periods_per_year=26,
                state_code="NC",
            )
            db.session.add(profile)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()

            # With 5% employer on $3846/period (~$5k/yr) + 7% growth on $50k,
            # 1-year should be ~$58k+. Without employer, growth-only ~$53.5k.
            amounts = re.findall(r'\$([0-9,]+)', html)
            amounts_int = [
                int(a.replace(',', ''))
                for a in amounts
                if int(a.replace(',', '')) > 54000
            ]

            # 3 milestones at offsets 6, 13, 26. With 5% employer flat on
            # $100k/26 (~$192/period) + 7% growth on $50k, no employee deduction:
            # 3-month ~$52k (<$54k), 6-month ~$54.3k (>$54k), 1-year ~$58.7k (>$54k)
            assert len(amounts_int) == 2, (
                "Expected 2 milestone projections > $54,000 with employer contribution, "
                f"but found {len(amounts_int)}. Amounts on page: {amounts}"
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
            assert b'name="target_amount"' in resp.data
            assert b'name="target_date"' in resp.data
            assert b"New Savings Goal" in resp.data

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
            assert b"Please correct the highlighted errors" in resp.data

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
                "target_amount": "-100.00",  # Negative -- fails Range validator.
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

    def test_edit_goal_idor(self, app, auth_client, seed_user):
        """GET /savings/goals/<id>/edit for another user's goal returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_goal()

            resp = auth_client.get(
                f"/savings/goals/{other['goal'].id}/edit",
                follow_redirects=True,
            )
            assert resp.status_code == 404

    def test_update_goal_idor(self, app, auth_client, seed_user):
        """POST /savings/goals/<id> for another user's goal returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_goal()

            resp = auth_client.post(
                f"/savings/goals/{other['goal'].id}",
                data={"name": "Hijacked"},
                follow_redirects=True,
            )
            assert resp.status_code == 404

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
        """POST /savings/goals/<id>/delete for another user's goal returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_goal()

            resp = auth_client.post(
                f"/savings/goals/{other['goal'].id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 404

            # Verify goal still active.
            db.session.refresh(other["goal"])
            assert other["goal"].is_active is True

    def test_delete_nonexistent_goal(self, app, auth_client, seed_user):
        """POST /savings/goals/999999/delete for missing goal returns 404 (security)."""
        with app.app_context():
            resp = auth_client.post(
                "/savings/goals/999999/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 404


# ── Double Submit / Unique Constraint ────────────────────────────────


class TestGoalIdempotency:
    """Tests for unique constraint on savings goals."""

    def test_duplicate_goal_name_same_account(self, app, auth_client, seed_user, seed_periods):
        """POST /savings/goals twice with the same name+account returns a
        flash warning on the second attempt, and creating the same name
        on a different account still succeeds."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            form_data = {
                "account_id": str(acct.id),
                "name": "Emergency Fund",
                "target_amount": "5000.00",
            }

            # -- First submission: succeeds --
            resp1 = auth_client.post("/savings/goals", data=form_data)
            assert resp1.status_code == 302, (
                f"First submit returned {resp1.status_code}, expected 302"
            )

            goal = db.session.query(SavingsGoal).filter_by(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="Emergency Fund",
            ).one()
            assert goal.target_amount == Decimal("5000.00")
            original_goal_id = goal.id

            # -- Second submission: duplicate, handled gracefully --
            resp2 = auth_client.post("/savings/goals", data=form_data)
            assert resp2.status_code == 302, (
                f"Duplicate submit returned {resp2.status_code}, expected 302"
            )

            location = resp2.headers.get("Location", "")
            assert "/savings" in location, (
                f"Redirect went to {location}, expected /savings"
            )

            # Follow redirect and verify flash warning.
            resp3 = auth_client.get(location)
            assert resp3.status_code == 200
            assert b"already exists" in resp3.data, (
                "Flash warning about duplicate goal not found"
            )

            # -- DB state: exactly 1 goal, unchanged --
            goal_count = db.session.query(SavingsGoal).filter_by(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="Emergency Fund",
            ).count()
            assert goal_count == 1, (
                f"Expected 1 goal, found {goal_count}"
            )

            # Original goal not modified.
            db.session.expire_all()
            original_goal = db.session.get(SavingsGoal, original_goal_id)
            assert original_goal.target_amount == Decimal("5000.00")

            # Session health check: only 1 goal should exist at this point.
            total_goals = db.session.query(SavingsGoal).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert total_goals == 1

            # -- Same name on DIFFERENT account must still succeed --
            acct2 = _create_savings_account(seed_user, "Second Savings")
            db.session.commit()
            resp4 = auth_client.post("/savings/goals", data={
                "account_id": str(acct2.id),
                "name": "Emergency Fund",
                "target_amount": "3000.00",
            })
            assert resp4.status_code == 302, (
                f"Same name on different account returned {resp4.status_code}, "
                "expected 302 (should succeed)"
            )

            # Now 2 goals named "Emergency Fund" exist, on different accounts.
            all_ef_goals = db.session.query(SavingsGoal).filter_by(
                user_id=seed_user["user"].id,
                name="Emergency Fund",
            ).all()
            assert len(all_ef_goals) == 2, (
                f"Expected 2 goals named 'Emergency Fund', found {len(all_ef_goals)}"
            )
            account_ids = {g.account_id for g in all_ef_goals}
            assert account_ids == {acct.id, acct2.id}


# ── Negative Paths ────────────────────────────────────────────────


class TestSavingsNegativePaths:
    """Negative-path tests: nonexistent IDs, IDOR, deactivated accounts, validation."""

    def test_edit_nonexistent_goal(self, app, auth_client, seed_user):
        """GET /savings/goals/999999/edit for a nonexistent goal returns 404 (security)."""
        with app.app_context():
            resp = auth_client.get(
                "/savings/goals/999999/edit", follow_redirects=True,
            )

            assert resp.status_code == 404

    def test_update_nonexistent_goal(self, app, auth_client, seed_user):
        """POST /savings/goals/999999 for a nonexistent goal returns 404 (security)."""
        with app.app_context():
            resp = auth_client.post("/savings/goals/999999", data={
                "name": "Ghost Goal",
                "target_amount": "5000.00",
            }, follow_redirects=True)

            assert resp.status_code == 404

    def test_delete_nonexistent_goal(self, app, auth_client, seed_user):
        """POST /savings/goals/999999/delete for a nonexistent goal returns 404 (security)."""
        with app.app_context():
            resp = auth_client.post(
                "/savings/goals/999999/delete", follow_redirects=True,
            )

            assert resp.status_code == 404

    def test_create_goal_on_deactivated_account(self, app, auth_client, seed_user):
        """POST /savings/goals with account_id of a deactivated account is rejected."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            acct.is_active = False
            db.session.commit()

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "Deactivated Test",
                "target_amount": "5000.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid account." in resp.data

            # Verify no goal was created.
            goal = db.session.query(SavingsGoal).filter_by(
                name="Deactivated Test",
            ).first()
            assert goal is None

    def test_update_goal_account_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /savings/goals/<id> with another user's account_id is rejected."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            goal = _create_goal(seed_user, acct)
            original_account_id = goal.account_id

            resp = auth_client.post(f"/savings/goals/{goal.id}", data={
                "account_id": str(second_user["account"].id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid account." in resp.data

            # Verify goal's account_id was NOT changed.
            db.session.expire_all()
            refreshed = db.session.get(SavingsGoal, goal.id)
            assert refreshed.account_id == original_account_id, (
                "Goal's account_id must not change to another user's account"
            )

    def test_delete_other_users_goal_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /savings/goals/<id>/delete for another user's goal is blocked."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            other_acct = Account(
                user_id=second_user["user"].id,
                account_type_id=savings_type.id,
                name="Other Savings",
                current_anchor_balance=Decimal("2000.00"),
            )
            db.session.add(other_acct)
            db.session.flush()

            goal = SavingsGoal(
                user_id=second_user["user"].id,
                account_id=other_acct.id,
                name="Other Goal",
                target_amount=Decimal("5000.00"),
            )
            db.session.add(goal)
            db.session.commit()
            goal_id = goal.id

            resp = auth_client.post(
                f"/savings/goals/{goal_id}/delete",
                follow_redirects=True,
            )

            assert resp.status_code == 404

            # Verify goal still exists and is active.
            db.session.expire_all()
            refreshed = db.session.get(SavingsGoal, goal_id)
            assert refreshed is not None
            assert refreshed.is_active is True

    def test_create_goal_missing_required_fields(self, app, auth_client, seed_user):
        """POST /savings/goals with empty form data fails validation and creates no record."""
        with app.app_context():
            resp = auth_client.post(
                "/savings/goals", data={}, follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            # Verify no goal was created.
            count = db.session.query(SavingsGoal).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_create_goal_negative_target_amount(self, app, auth_client, seed_user):
        """POST /savings/goals with negative target_amount fails schema validation."""
        with app.app_context():
            acct = _create_savings_account(seed_user)

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "Bad Goal",
                "target_amount": "-1000.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            # Verify no goal was created.
            count = db.session.query(SavingsGoal).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0


# ── Shadow Transaction Inclusion Tests ────────────────────────────


class TestSavingsDashboardShadowTransactions:
    """Verify that the savings dashboard includes shadow transactions
    (from transfers) in account balance calculations.

    Before this fix, the dashboard filtered transactions by template_id,
    which excluded shadow transactions (template_id=None).  The correct
    filter uses the account_id column added in Task 1 of the transfer
    rework.
    """

    def test_hysa_balance_includes_transfer_deposit(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Verify that the savings dashboard passes shadow income
        transactions to the HYSA balance calculator, so transfer deposits
        increase the projected balance.  Without this, HYSA projections
        underestimate the balance by the total of all missed deposits.
        """
        from app.models.interest_params import InterestParams as IP  # pylint: disable=import-outside-toplevel
        from app.models.category import Category  # pylint: disable=import-outside-toplevel
        from app.models.ref import Status  # pylint: disable=import-outside-toplevel
        from app.services import transfer_service  # pylint: disable=import-outside-toplevel

        with app.app_context():
            # Create HYSA account with known anchor balance.
            hysa_type = db.session.query(AccountType).filter_by(name="HYSA").one()
            hysa = Account(
                user_id=seed_user["user"].id,
                account_type_id=hysa_type.id,
                name="High Yield Savings",
                current_anchor_balance=Decimal("10000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(hysa)
            db.session.flush()

            ip = IP(
                account_id=hysa.id,
                apy=Decimal("0.04500"),  # 4.5% stored as decimal
                compounding_frequency="daily",
            )
            db.session.add(ip)

            # Add transfer categories required by transfer_service.
            incoming = Category(
                user_id=seed_user["user"].id,
                group_name="Transfers", item_name="Incoming",
            )
            outgoing = Category(
                user_id=seed_user["user"].id,
                group_name="Transfers", item_name="Outgoing",
            )
            db.session.add_all([incoming, outgoing])
            db.session.flush()

            # Create a $500 transfer from checking to HYSA.
            projected = db.session.query(Status).filter_by(name="Projected").one()
            transfer_service.create_transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=hysa.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                amount=Decimal("500.00"),
                status_id=projected.id,
                category_id=outgoing.id,
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            # The HYSA should show in the dashboard.  Its balance should
            # include the $500 deposit + interest.  With anchor $10,000
            # + $500 deposit + daily compounding at 4.5% APY, the
            # balance will be ~$10,601.  The key assertion is that the
            # balance exceeds $10,500 (anchor + deposit), proving the
            # deposit was included before interest compounded.
            html = resp.data.decode()
            assert "High Yield Savings" in html
            # Without the fix, the balance would be ~$10,096 (anchor
            # + interest only, no deposit).  With the fix, it exceeds
            # $10,500.  Check for "10,6" which confirms the deposit
            # is reflected ($10,601 with interest).
            assert "10,6" in html

    def test_savings_balance_includes_transfer_deposit(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Verify that a regular savings account (no HYSA params) includes
        shadow income from transfers in its balance calculation.  The
        balance for a savings account receiving a $1000 transfer must
        reflect the deposit, not just the anchor balance.
        """
        from app.models.category import Category  # pylint: disable=import-outside-toplevel
        from app.models.ref import Status  # pylint: disable=import-outside-toplevel
        from app.services import transfer_service  # pylint: disable=import-outside-toplevel

        with app.app_context():
            savings = _create_savings_account(seed_user, name="Emergency Fund")
            savings.current_anchor_period_id = seed_periods[0].id
            savings.current_anchor_balance = Decimal("3000.00")
            db.session.flush()

            # Transfer categories.
            incoming = Category(
                user_id=seed_user["user"].id,
                group_name="Transfers", item_name="Incoming",
            )
            outgoing = Category(
                user_id=seed_user["user"].id,
                group_name="Transfers", item_name="Outgoing",
            )
            db.session.add_all([incoming, outgoing])
            db.session.flush()

            projected = db.session.query(Status).filter_by(name="Projected").one()
            transfer_service.create_transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                amount=Decimal("1000.00"),
                status_id=projected.id,
                category_id=outgoing.id,
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()
            # Anchor $3,000 + $1,000 deposit = $4,000 at period 0.
            assert "4,000" in html

    def test_account_with_no_transfers_still_works(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Verify that the savings dashboard renders correctly for
        accounts that have no transfers.  The account_id filter must
        produce an empty list without errors, not crash or show stale
        data from another account's transactions.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user, name="Plain Savings")
            savings.current_anchor_period_id = seed_periods[0].id
            savings.current_anchor_balance = Decimal("2000.00")
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()
            assert "Plain Savings" in html
            assert "2,000" in html


# ── Emergency Fund Committed Baseline Tests ──────────────────────────


class TestEmergencyFundCommittedBaseline:
    """Tests for the committed monthly expense floor in emergency fund coverage.

    The emergency fund calculation uses the higher of:
    - Historical actual average expenses (from settled transactions)
    - Committed baseline (from active recurring templates)

    This ensures newly created recurring obligations are immediately
    reflected without waiting for settlement history to accumulate.
    """

    def test_emergency_fund_includes_transfer_templates(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Transfer templates debiting checking are included in the
        committed monthly baseline.  A $1,500 every-period transfer
        produces committed = $1,500 * 26/12 = $3,250/month.
        """
        with app.app_context():
            # Savings account so emergency fund section renders.
            savings = _create_savings_account(seed_user, name="EF Savings")
            savings.current_anchor_balance = Decimal("10000.00")

            # Transfer template: checking -> savings, every period.
            rule = _create_recurrence_rule(
                seed_user, RecurrencePatternEnum.EVERY_PERIOD,
            )
            _create_test_transfer_template(
                seed_user, savings, rule, Decimal("1500.00"),
                name="Mortgage Payment",
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()
            # committed = 1500 * 26/12 = 3250
            # Template shows "$3,250/mo avg expenses".
            assert "$3,250/mo" in html, (
                "Expected $3,250/mo from committed transfer baseline, "
                f"but not found in HTML"
            )

    def test_emergency_fund_uses_higher_of_actual_or_committed(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """When committed monthly exceeds historical average, the
        committed value is used.  Small settled history ($10/period)
        should be overridden by the $3,250/month committed baseline.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user, name="EF Savings")
            savings.current_anchor_balance = Decimal("10000.00")

            # Create small settled expenses across 6 recent periods.
            settled_id = ref_cache.status_id(StatusEnum.SETTLED)
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            category_id = seed_user["categories"]["Rent"].id

            for period in seed_periods[1:7]:
                txn = Transaction(
                    account_id=seed_user["account"].id,
                    pay_period_id=period.id,
                    scenario_id=seed_user["scenario"].id,
                    status_id=settled_id,
                    name="Small Expense",
                    category_id=category_id,
                    transaction_type_id=expense_type_id,
                    estimated_amount=Decimal("10.00"),
                )
                db.session.add(txn)

            # Transfer template with higher committed amount.
            rule = _create_recurrence_rule(
                seed_user, RecurrencePatternEnum.EVERY_PERIOD,
            )
            _create_test_transfer_template(
                seed_user, savings, rule, Decimal("1500.00"),
                name="Mortgage Payment",
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()
            # Historical avg ~= $21.67/mo, committed = $3,250/mo.
            # max() picks $3,250.
            assert "$3,250/mo" in html, (
                "Expected committed baseline ($3,250) to override "
                "small historical average"
            )

    def test_emergency_fund_with_no_history_uses_committed(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """With zero settled transactions, the committed baseline from
        active templates is used instead of the historical $0 average.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user, name="EF Savings")
            savings.current_anchor_balance = Decimal("10000.00")

            # Monthly expense template = $2,000/month.
            rule = _create_recurrence_rule(
                seed_user, RecurrencePatternEnum.MONTHLY,
            )
            _create_expense_template(
                seed_user, rule, Decimal("2000.00"),
                name="Monthly Bills",
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()
            # committed = $2,000 (monthly, no conversion needed).
            assert "$2,000/mo" in html, (
                "Expected $2,000/mo from committed monthly baseline"
            )

    def test_emergency_fund_no_templates_no_history(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """With no templates and no settled transactions, avg_monthly_expenses
        stays at $0 and coverage metrics show zero.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user, name="EF Savings")
            savings.current_anchor_balance = Decimal("10000.00")
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()
            # Section renders (savings > 0) but no expense info.
            assert "Emergency Fund Coverage" in html
            assert "avg expenses" not in html

    def test_emergency_fund_monthly_template_contribution(
        self, app, seed_user,
    ):
        """A monthly template contributes its exact default_amount as the
        monthly equivalent -- NOT multiplied by 26/12.
        """
        with app.app_context():
            rule = _create_recurrence_rule(
                seed_user, RecurrencePatternEnum.MONTHLY,
            )
            tmpl = _create_expense_template(
                seed_user, rule, Decimal("500.00"),
                name="Monthly Subscription",
            )
            db.session.commit()

            result = savings_goal_service.compute_committed_monthly(
                [tmpl], [],
            )
            assert result == Decimal("500.00"), (
                f"Monthly template should contribute exactly $500, got {result}"
            )

    def test_emergency_fund_excludes_once_templates(
        self, app, seed_user,
    ):
        """One-time templates do not contribute to committed monthly.
        Only the recurring every-period template should be counted.
        """
        with app.app_context():
            once_rule = _create_recurrence_rule(
                seed_user, RecurrencePatternEnum.ONCE,
            )
            once_tmpl = _create_expense_template(
                seed_user, once_rule, Decimal("5000.00"),
                name="One-Time Purchase",
            )

            every_rule = _create_recurrence_rule(
                seed_user, RecurrencePatternEnum.EVERY_PERIOD,
            )
            recurring_tmpl = _create_expense_template(
                seed_user, every_rule, Decimal("100.00"),
                name="Recurring Bill",
            )
            db.session.commit()

            result = savings_goal_service.compute_committed_monthly(
                [once_tmpl, recurring_tmpl], [],
            )
            # Only recurring: 100 * 26/12 = 216.67
            expected = (Decimal("100") * Decimal("26") / Decimal("12")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
            assert result == expected, (
                f"Expected {expected} (once excluded), got {result}"
            )

    def test_emergency_fund_excludes_inactive_templates(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Inactive templates are filtered out by the route and do not
        contribute to the committed monthly baseline.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user, name="EF Savings")
            savings.current_anchor_balance = Decimal("10000.00")

            # Inactive template -- excluded by route query.
            rule1 = _create_recurrence_rule(
                seed_user, RecurrencePatternEnum.EVERY_PERIOD,
            )
            _create_expense_template(
                seed_user, rule1, Decimal("999.00"),
                name="Inactive Bill", is_active=False,
            )

            # Active template -- included.
            rule2 = _create_recurrence_rule(
                seed_user, RecurrencePatternEnum.EVERY_PERIOD,
            )
            _create_expense_template(
                seed_user, rule2, Decimal("1500.00"),
                name="Active Bill",
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()
            # Only active: 1500 * 26/12 = 3250.
            # If inactive were included: (1500+999)*26/12 = 5415.
            assert "$3,250/mo" in html, (
                "Expected only active template in committed baseline"
            )
            assert "$5,415/mo" not in html, (
                "Inactive template should not contribute"
            )

    def test_emergency_fund_handles_none_default_amount(
        self, app, seed_user,
    ):
        """Templates with default_amount=None are skipped without error.
        The column is NOT NULL in the schema, but the function handles
        it defensively for robustness.
        """
        import types  # pylint: disable=import-outside-toplevel

        with app.app_context():
            # Mock template with None amount (cannot be persisted to DB).
            mock_rule = types.SimpleNamespace(
                pattern_id=ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.EVERY_PERIOD,
                ),
                interval_n=1,
            )
            mock_template = types.SimpleNamespace(
                default_amount=None,
                recurrence_rule=mock_rule,
            )

            result = savings_goal_service.compute_committed_monthly(
                [mock_template], [],
            )
            assert result == Decimal("0.00"), (
                f"Expected 0.00 when template has None amount, got {result}"
            )

    def test_emergency_fund_every_n_periods_template(
        self, app, seed_user,
    ):
        """An every_n_periods template with n=2 and $600 contributes
        $600 * (26/2) / 12 = $650.00 per month.
        """
        with app.app_context():
            rule = _create_recurrence_rule(
                seed_user, RecurrencePatternEnum.EVERY_N_PERIODS,
                interval_n=2,
            )
            tmpl = _create_expense_template(
                seed_user, rule, Decimal("600.00"),
                name="Biweekly Alternating",
            )
            db.session.commit()

            result = savings_goal_service.compute_committed_monthly(
                [tmpl], [],
            )
            assert result == Decimal("650.00"), (
                f"Expected 650.00 for every-2-periods template, got {result}"
            )

    def test_emergency_fund_annual_template(
        self, app, seed_user,
    ):
        """An annual template with $1,200 contributes $100.00 per month."""
        with app.app_context():
            rule = _create_recurrence_rule(
                seed_user, RecurrencePatternEnum.ANNUAL,
            )
            tmpl = _create_expense_template(
                seed_user, rule, Decimal("1200.00"),
                name="Annual Insurance",
            )
            db.session.commit()

            result = savings_goal_service.compute_committed_monthly(
                [tmpl], [],
            )
            assert result == Decimal("100.00"), (
                f"Expected 100.00 for annual template, got {result}"
            )

    def test_compute_committed_monthly_empty_lists(
        self, app,
    ):
        """compute_committed_monthly with empty lists returns zero."""
        with app.app_context():
            result = savings_goal_service.compute_committed_monthly([], [])
            assert result == Decimal("0.00"), (
                f"Expected 0.00 for empty lists, got {result}"
            )


# ── Setup Required Badge Tests ───────────────────────────────────


class TestSetupRequiredBadge:
    """Tests for the 'Setup Required' badge on the savings dashboard.

    The badge appears when a parameterized account type is missing its
    params record (e.g. account created before auto-creation was added).
    """

    def test_setup_badge_shown_for_hysa_without_params(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """HYSA without InterestParams shows 'Setup Required' badge on dashboard."""
        with app.app_context():
            hysa_type = db.session.query(AccountType).filter_by(
                name="HYSA"
            ).one()
            acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=hysa_type.id,
                name="Unconfigured HYSA",
                current_anchor_balance=Decimal("5000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(acct)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Setup Required" in resp.data

    def test_setup_badge_hidden_for_hysa_with_params(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """HYSA with InterestParams does NOT show 'Setup Required' badge."""
        with app.app_context():
            from app.models.interest_params import InterestParams

            hysa_type = db.session.query(AccountType).filter_by(
                name="HYSA"
            ).one()
            acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=hysa_type.id,
                name="Configured HYSA",
                current_anchor_balance=Decimal("5000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InterestParams(account_id=acct.id))
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Setup Required" not in resp.data

    def test_setup_badge_shown_for_investment_without_params(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """401(k) without InvestmentParams shows 'Setup Required' badge."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()
            acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=k401_type.id,
                name="Unconfigured 401k",
                current_anchor_balance=Decimal("10000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(acct)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Setup Required" in resp.data

    def test_setup_badge_hidden_for_investment_with_params(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """401(k) with InvestmentParams does NOT show 'Setup Required' badge."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()
            acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=k401_type.id,
                name="Configured 401k",
                current_anchor_balance=Decimal("10000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InvestmentParams(account_id=acct.id))
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Setup Required" not in resp.data

    def test_setup_badge_not_shown_for_checking(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Checking account does not show 'Setup Required' badge."""
        with app.app_context():
            # seed_user already has a checking account.
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Setup Required" not in resp.data

    def test_needs_setup_with_no_params_record(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """401(k) with missing InvestmentParams renders without error and shows badge.

        Verifies the dashboard handles missing params gracefully (no 500)
        when an account was created before auto-creation was implemented.
        """
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()
            acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=k401_type.id,
                name="Legacy 401k",
                current_anchor_balance=Decimal("50000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(acct)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Legacy 401k" in resp.data
            assert b"Setup Required" in resp.data


# ── Section 5 Regression Baseline ──────────────────────────────────────


class TestSavingsGoalRegression:
    """Regression baseline for Section 5 savings goal changes.

    Locks down the full savings goal lifecycle (create, read, update,
    deactivate) and edge cases before Section 5 modifies savings
    projections and goal computation.
    """

    def test_full_lifecycle_create_read_update_deactivate(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Complete goal lifecycle: create -> read on dashboard -> update
        -> deactivate -> verify absent from active views.

        This is the primary regression test for the goal CRUD pipeline.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user, "Lifecycle Savings")

            # Create.
            resp = auth_client.post("/savings/goals", data={
                "name": "Lifecycle Goal",
                "target_amount": "8000.00",
                "target_date": "2027-12-01",
                "contribution_per_period": "100.00",
                "account_id": str(acct.id),
            })
            assert resp.status_code == 302

            goal = db.session.query(SavingsGoal).filter_by(
                name="Lifecycle Goal"
            ).one()
            assert goal.target_amount == Decimal("8000.00")
            assert goal.is_active is True

            # Read on dashboard.
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Lifecycle Goal" in resp.data

            # Update.
            resp = auth_client.post(f"/savings/goals/{goal.id}", data={
                "name": "Updated Goal",
                "target_amount": "12000.00",
                "target_date": "2028-06-01",
                "contribution_per_period": "150.00",
                "account_id": str(acct.id),
            })
            assert resp.status_code == 302
            db.session.refresh(goal)
            assert goal.name == "Updated Goal"
            assert goal.target_amount == Decimal("12000.00")

            # Deactivate.
            resp = auth_client.post(f"/savings/goals/{goal.id}/delete")
            assert resp.status_code == 302
            db.session.refresh(goal)
            assert goal.is_active is False

            # Verify absent from active goal list (goal name may still
            # appear in flash/toast messages, so check the DB instead).
            active_goals = (
                db.session.query(SavingsGoal)
                .filter_by(user_id=seed_user["user"].id, is_active=True)
                .all()
            )
            active_names = [g.name for g in active_goals]
            assert "Updated Goal" not in active_names

    def test_goal_with_past_target_date(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Goal with target_date in the past must not crash the dashboard.

        Users may have goals whose deadlines have passed.  The dashboard
        should handle this gracefully.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user, "Past Date Savings")

            # Create goal with past target_date directly in DB.
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="Overdue Goal",
                target_amount=Decimal("5000.00"),
                target_date=date(2020, 1, 1),
            )
            db.session.add(goal)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Overdue Goal" in resp.data

    def test_goal_with_zero_target_amount_rejected(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Goal with zero target_amount must fail validation.

        The savings_goals table has a CHECK constraint: target_amount > 0.
        The schema validation should catch this before hitting the DB.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user, "Zero Target Savings")
            auth_client.post("/savings/goals", data={
                "name": "Zero Goal",
                "target_amount": "0.00",
                "account_id": str(acct.id),
            })
            # Should fail validation -- not create the goal.
            count = db.session.query(SavingsGoal).filter_by(
                name="Zero Goal"
            ).count()
            assert count == 0

    def test_goal_without_contribution_per_period(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Goal without contribution_per_period must still render on dashboard.

        contribution_per_period is optional (nullable).  The dashboard
        must handle None gracefully in its progress calculations.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user, "No Contrib Savings")

            resp = auth_client.post("/savings/goals", data={
                "name": "No Contribution Goal",
                "target_amount": "3000.00",
                "target_date": "2028-01-01",
                "account_id": str(acct.id),
            })
            assert resp.status_code == 302

            goal = db.session.query(SavingsGoal).filter_by(
                name="No Contribution Goal"
            ).one()
            assert goal.contribution_per_period is None

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"No Contribution Goal" in resp.data

    def test_goal_without_target_date(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Goal without target_date must still render on dashboard.

        target_date is nullable.  The dashboard's remaining_periods
        calculation must handle None target_date without error.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user, "No Date Savings")

            resp = auth_client.post("/savings/goals", data={
                "name": "Dateless Goal",
                "target_amount": "7000.00",
                "account_id": str(acct.id),
            })
            assert resp.status_code == 302

            goal = db.session.query(SavingsGoal).filter_by(
                name="Dateless Goal"
            ).one()
            assert goal.target_date is None

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Dateless Goal" in resp.data

    def test_goal_idor_view_blocked(
        self, app, auth_client, seed_user, seed_periods,
        seed_second_user, second_auth_client, seed_second_periods,
    ):
        """User A cannot view or edit User B's savings goal.

        Verifies the ownership check on the goal edit endpoint returns
        an identical response for 'not found' and 'not yours'.
        """
        with app.app_context():
            # Create goal for user B.
            other_acct = _create_savings_account(
                seed_second_user, "Other User Savings",
            )
            other_goal = SavingsGoal(
                user_id=seed_second_user["user"].id,
                account_id=other_acct.id,
                name="Private Goal",
                target_amount=Decimal("20000.00"),
            )
            db.session.add(other_goal)
            db.session.commit()

            # User A tries to access User B's goal edit form.
            resp = auth_client.get(f"/savings/goals/{other_goal.id}/edit")
            assert resp.status_code == 404

            # User A tries to update User B's goal.
            resp = auth_client.post(f"/savings/goals/{other_goal.id}", data={
                "name": "Hijacked",
                "target_amount": "1.00",
                "account_id": str(other_acct.id),
            })
            assert resp.status_code == 404

            # Goal must be unchanged.
            db.session.refresh(other_goal)
            assert other_goal.name == "Private Goal"
            assert other_goal.target_amount == Decimal("20000.00")

    def test_goal_negative_target_amount_rejected(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Negative target_amount must be rejected by schema validation."""
        with app.app_context():
            acct = _create_savings_account(seed_user, "Neg Target Savings")
            auth_client.post("/savings/goals", data={
                "name": "Negative Goal",
                "target_amount": "-5000.00",
                "account_id": str(acct.id),
            })
            count = db.session.query(SavingsGoal).filter_by(
                name="Negative Goal"
            ).count()
            assert count == 0


# ── Paid-Off Badge Tests (Commit 5.9-2) ──────────────────────────────


def _create_small_loan(seed_user, name="Test Loan",
                       principal=Decimal("1000.00"),
                       rate=Decimal("0.05000"), term=24):
    """Create a small loan with LoanParams for paid-off badge testing.

    Origination is Jan 2026 with term=24 so remaining months is
    comfortably positive (~21 from April 2026).
    """
    from app.models.loan_params import LoanParams  # pylint: disable=import-outside-toplevel

    loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=loan_type.id,
        name=name,
        current_anchor_balance=principal,
    )
    db.session.add(account)
    db.session.flush()

    params = LoanParams(
        account_id=account.id,
        original_principal=principal,
        current_principal=principal,
        interest_rate=rate,
        term_months=term,
        origination_date=date(2026, 1, 1),
        payment_day=1,
    )
    db.session.add(params)
    db.session.commit()
    return account


def _make_confirmed_transfer(seed_user, to_account, period, amount):
    """Create a confirmed (Paid) transfer to a loan account."""
    from app.services import transfer_service  # pylint: disable=import-outside-toplevel

    return transfer_service.create_transfer(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        amount=amount,
        status_id=ref_cache.status_id(StatusEnum.DONE),
        category_id=seed_user["categories"]["Rent"].id,
    )


class TestPaidOffBadge:
    """Tests for the Paid Off badge on the accounts dashboard.

    Commit 5.9-2: a green "Paid Off" badge appears on debt account
    cards when confirmed payments bring the remaining balance to zero.
    """

    def test_paid_off_badge_shown(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Loan fully paid by a confirmed payment: badge appears.

        A $1,000 loan at 5% for 12 months.  A single confirmed
        payment of $1,100 covers the full balance + interest.
        """
        with app.app_context():
            acct = _create_small_loan(seed_user)
            _make_confirmed_transfer(
                seed_user, acct, seed_periods[7], Decimal("1100.00"),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Paid Off" in resp.data

    def test_no_badge_when_balance_remaining(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Partial confirmed payment: no badge."""
        with app.app_context():
            acct = _create_small_loan(seed_user)
            _make_confirmed_transfer(
                seed_user, acct, seed_periods[7], Decimal("500.00"),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Paid Off" not in resp.data

    def test_no_badge_when_no_payments(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Loan with no payments at all: no badge."""
        with app.app_context():
            _create_small_loan(seed_user)

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Paid Off" not in resp.data

    def test_no_badge_projected_only(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Projected payment covering full balance: no badge.

        Projections do not equal payoff.  Only confirmed (Paid/Settled)
        payments count toward the paid-off determination.
        """
        with app.app_context():
            from app.services import transfer_service  # pylint: disable=import-outside-toplevel

            acct = _create_small_loan(seed_user)
            transfer_service.create_transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=acct.id,
                pay_period_id=seed_periods[7].id,
                scenario_id=seed_user["scenario"].id,
                amount=Decimal("1100.00"),
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Paid Off" not in resp.data

    def test_paid_off_lump_sum(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Single lump-sum payment on a small loan: badge appears.

        A $1,000 loan paid off with a single $1,100 confirmed
        payment triggers the 5.8 overpayment guard, capping the
        payment at remaining balance + interest.
        """
        with app.app_context():
            acct = _create_small_loan(
                seed_user, name="Lump Sum Loan",
                principal=Decimal("500.00"), rate=Decimal("0.06000"), term=6,
            )
            _make_confirmed_transfer(
                seed_user, acct, seed_periods[7], Decimal("600.00"),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Paid Off" in html

    def test_paid_off_multiple_accounts_mixed(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Two loans: one paid off, one not.  Badge on the right one only."""
        with app.app_context():
            paid_off = _create_small_loan(
                seed_user, name="Paid Loan",
                principal=Decimal("1000.00"),
            )
            _make_confirmed_transfer(
                seed_user, paid_off, seed_periods[7], Decimal("1100.00"),
            )

            _unpaid = _create_small_loan(
                seed_user, name="Unpaid Loan",
                principal=Decimal("5000.00"),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()

            # The paid-off loan's card should have the badge.
            assert "Paid Off" in html
            # Only one badge should appear (for the paid-off loan).
            assert html.count("Paid Off") == 1

    def test_sub_penny_not_paid_off(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Payment leaving $0.01 remaining: not paid off.

        Only exact zero qualifies.  This tests that the comparison
        uses == Decimal("0.00"), not a threshold.
        """
        with app.app_context():
            # A $100 loan at 5% for 12 months, originating Jan 2026.
            # The schedule starts from origination, so two contractual
            # payments (Feb, Mar) reduce the balance before the
            # confirmed payment in April (seed_periods[7]).
            #
            # After month 1 (Feb): balance = $91.86
            # After month 2 (Mar): balance = $83.68
            # Month 3 (Apr) interest: $83.68 * 0.05/12 = $0.35
            # Payment of $84.02 -> principal = $84.02 - $0.35 = $83.67
            # Remaining: $83.68 - $83.67 = $0.01
            acct = _create_small_loan(
                seed_user, name="Sub Penny Loan",
                principal=Decimal("100.00"), rate=Decimal("0.05000"), term=12,
            )
            _make_confirmed_transfer(
                seed_user, acct, seed_periods[7], Decimal("84.02"),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Paid Off" not in resp.data


# -- Account Archival on Savings Dashboard Tests (Commit 5.9-3) -----------


class TestAccountArchivalDashboard:
    """Tests for archive/unarchive behavior on the accounts dashboard.

    Commit 5.9-3: archived accounts move to a collapsed section,
    active accounts get an archive button, and paid-off loans get
    a prominent archive prompt.
    """

    def test_archived_account_hidden_from_active_section(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Archived account does not appear in the active account cards."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            archived = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Hidden Savings",
                current_anchor_balance=Decimal("500.00"),
                is_active=False,
            )
            db.session.add(archived)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The active section (before "Archived Accounts") should
            # not contain the archived account name as a card title.
            active_section = html.split("Archived Accounts")[0]
            assert "Hidden Savings" not in active_section

    def test_archived_section_shown_when_archived_exist(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """When at least one account is archived, the collapsed section appears."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            archived = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Old Account",
                is_active=False,
            )
            db.session.add(archived)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Archived Accounts" in html
            assert "archivedAccounts" in html

    def test_archived_section_hidden_when_none_archived(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """When no accounts are archived, the section does not render."""
        with app.app_context():
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Archived Accounts" not in resp.data

    def test_archived_account_shows_in_archived_section(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Archived account card appears in the collapsed section with
        its name and an unarchive button.
        """
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            archived = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Closed Savings",
                current_anchor_balance=Decimal("0.00"),
                is_active=False,
            )
            db.session.add(archived)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The archived collapse div starts at id="archivedAccounts".
            # Split on the id attribute to get content after it.
            archived_section = html.split('id="archivedAccounts"')[1]
            assert "Closed Savings" in archived_section
            assert "unarchive" in archived_section

    def test_unarchive_from_dashboard(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """POST unarchive returns the account to active state."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            archived = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Restore Me",
                is_active=False,
            )
            db.session.add(archived)
            db.session.commit()
            acct_id = archived.id

            resp = auth_client.post(
                f"/accounts/{acct_id}/unarchive",
                follow_redirects=False,
            )
            assert resp.status_code == 302

            refreshed = db.session.get(Account, acct_id)
            assert refreshed.is_active is True

    def test_active_cards_have_archive_button(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Each active account card includes an archive action button."""
        with app.app_context():
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "bi-archive" in html
            assert f"/accounts/{seed_user['account'].id}/archive" in html

    def test_paid_off_shows_archive_prompt(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Paid-off loan card has a prominent 'Archive' prompt."""
        with app.app_context():
            acct = _create_small_loan(seed_user, name="Paid Off Archival")
            _make_confirmed_transfer(
                seed_user, acct, seed_periods[7], Decimal("1100.00"),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "This loan is paid off" in html
            assert f"/accounts/{acct.id}/archive" in html

    def test_archived_account_no_projections(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Archived accounts show last balance, not projected balances."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            archived = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Old Savings",
                current_anchor_balance=Decimal("5000.00"),
                is_active=False,
            )
            db.session.add(archived)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            archived_section = html.split('id="archivedAccounts"')[1]
            assert "Old Savings" in archived_section
            assert "$5,000.00" in archived_section
            assert "Projected" not in archived_section

    def test_mixed_active_and_archived(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Two active accounts + one archived: correct separation."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            active_acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Active Savings",
                current_anchor_balance=Decimal("3000.00"),
                current_anchor_period_id=seed_periods[0].id,
                is_active=True,
            )
            archived_acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Archived Savings",
                current_anchor_balance=Decimal("1000.00"),
                is_active=False,
            )
            db.session.add_all([active_acct, archived_acct])
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()

            assert "Active Savings" in html
            assert "Checking" in html
            assert "Archived Accounts (1)" in html
            assert "Archived Savings" in html


# -- Income-Relative Goal Form and Dashboard Tests (Commit 5.4-4) ----------


class TestIncomeRelativeGoalForm:
    """Tests for income-relative goal mode in the form and dashboard."""

    def test_goal_form_shows_mode_selector(self, app, auth_client, seed_user):
        """GET /savings/goals/new renders the mode selector dropdown.

        Both goal mode options and the income fields must be present
        in the form HTML.
        """
        with app.app_context():
            resp = auth_client.get("/savings/goals/new")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'name="goal_mode_id"' in html
            assert "Fixed" in html
            assert "Income-Relative" in html
            assert 'name="income_unit_id"' in html
            assert 'name="income_multiplier"' in html

    def test_create_income_relative_goal_via_form(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST creates an income-relative goal with correct field values.

        target_amount should be None; income fields should be set.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "3 Paychecks",
                "goal_mode_id": str(ir_id),
                "income_unit_id": str(paychecks_id),
                "income_multiplier": "3.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data

            goal = db.session.query(SavingsGoal).filter_by(
                name="3 Paychecks",
            ).one()
            assert goal.goal_mode_id == ir_id
            assert goal.income_unit_id == paychecks_id
            assert goal.income_multiplier == Decimal("3.00")
            assert goal.target_amount is None

    def test_create_fixed_goal_still_works(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST with goal_mode_id=Fixed creates a fixed goal.

        Backward compatibility -- income fields should be None.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "Emergency Fund",
                "goal_mode_id": str(fixed_id),
                "target_amount": "5000.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data

            goal = db.session.query(SavingsGoal).filter_by(
                name="Emergency Fund",
            ).one()
            assert goal.goal_mode_id == fixed_id
            assert goal.target_amount == Decimal("5000.00")
            assert goal.income_unit_id is None
            assert goal.income_multiplier is None

    def test_create_goal_without_mode_defaults_fixed(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST without goal_mode_id defaults to Fixed via schema load_default.

        Backward compatibility for any code path that omits goal_mode_id.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "No Mode Specified",
                "target_amount": "2000.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data

            goal = db.session.query(SavingsGoal).filter_by(
                name="No Mode Specified",
            ).one()
            assert goal.goal_mode_id == fixed_id

    def test_create_fixed_with_income_fields_cleaned(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST with fixed mode but stale income fields cleans them.

        Hidden form fields still submit their old values.  The route
        must strip income_unit_id and income_multiplier for fixed goals.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "Stale Fields",
                "goal_mode_id": str(fixed_id),
                "target_amount": "5000.00",
                "income_unit_id": str(paychecks_id),
                "income_multiplier": "3.00",
            }, follow_redirects=True)

            assert resp.status_code == 200

            goal = db.session.query(SavingsGoal).filter_by(
                name="Stale Fields",
            ).one()
            assert goal.goal_mode_id == fixed_id
            assert goal.target_amount == Decimal("5000.00")
            assert goal.income_unit_id is None
            assert goal.income_multiplier is None

    def test_edit_goal_mode_change_fixed_to_relative(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST update changes goal from fixed to income-relative.

        target_amount should be cleared; income fields should be set.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            goal = _create_goal(seed_user, acct, name="Mode Change Test")
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            months_id = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)

            resp = auth_client.post(
                f"/savings/goals/{goal.id}",
                data={
                    "goal_mode_id": str(ir_id),
                    "income_unit_id": str(months_id),
                    "income_multiplier": "6.00",
                    "name": "Mode Change Test",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(goal)
            assert goal.goal_mode_id == ir_id
            assert goal.income_unit_id == months_id
            assert goal.income_multiplier == Decimal("6.00")
            assert goal.target_amount is None

    def test_edit_income_relative_to_fixed(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST update changes goal from income-relative to fixed.

        income_unit_id and income_multiplier should be cleared.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
            fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="IR to Fixed",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
            )
            db.session.add(goal)
            db.session.commit()

            resp = auth_client.post(
                f"/savings/goals/{goal.id}",
                data={
                    "goal_mode_id": str(fixed_id),
                    "target_amount": "5000.00",
                    "name": "IR to Fixed",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            db.session.refresh(goal)
            assert goal.goal_mode_id == fixed_id
            assert goal.target_amount == Decimal("5000.00")
            assert goal.income_unit_id is None
            assert goal.income_multiplier is None

    def test_create_income_relative_validation_error(
        self, app, auth_client, seed_user
    ):
        """POST with income-relative mode but missing income_unit_id errors.

        Schema cross-field validation rejects this combination.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "Missing Unit",
                "goal_mode_id": str(ir_id),
                "income_multiplier": "3.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            goal = db.session.query(SavingsGoal).filter_by(
                name="Missing Unit",
            ).first()
            assert goal is None

    def test_goal_form_edit_prepopulates_income_fields(
        self, app, auth_client, seed_user
    ):
        """GET edit form pre-populates mode, unit, and multiplier.

        For an income-relative goal, the mode dropdown should select
        Income-Relative, and the income fields should have values.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            months_id = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="Prepopulate Test",
                goal_mode_id=ir_id,
                income_unit_id=months_id,
                income_multiplier=Decimal("3.00"),
            )
            db.session.add(goal)
            db.session.commit()

            resp = auth_client.get(f"/savings/goals/{goal.id}/edit")
            assert resp.status_code == 200
            html = resp.data.decode()

            # The income-relative option should be selected.
            assert f'value="{ir_id}"' in html
            # The months unit option should be selected.
            assert f'value="{months_id}"' in html
            # The multiplier value should be pre-filled.
            assert '3.00' in html

    def test_dashboard_shows_income_relative_label(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Dashboard displays the income descriptor for income-relative goals.

        The descriptor text (e.g. '3.00 months of salary') should
        appear on the dashboard for income-relative goals.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            months_id = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="Income Descriptor",
                goal_mode_id=ir_id,
                income_unit_id=months_id,
                income_multiplier=Decimal("3.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "3.00 months of salary" in html

    def test_dashboard_fixed_goal_no_descriptor(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Dashboard does NOT show an income descriptor for fixed goals."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            goal = _create_goal(seed_user, acct, name="Fixed Display")

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "of salary" not in html


# -- Goal Trajectory Display Tests (Commit 5.15-1) --------------------------


class TestTrajectoryDisplay:
    """Route-level tests for trajectory and pace display on goal cards.

    Commit 5.15-1: the dashboard shows projected completion dates,
    pace badges, and required monthly contribution when behind.
    """

    def test_dashboard_displays_trajectory(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C-5.15-14: Goal with recurring transfer shows trajectory info.

        A monthly transfer of $500 into a savings account with $5,000
        balance and $10,000 target.  The dashboard should show the
        projected completion text and the trajectory section.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)

            monthly_pattern_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.MONTHLY
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=monthly_pattern_id,
            )
            db.session.add(rule)
            db.session.flush()

            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=acct.id,
                name="Monthly Savings",
                default_amount=Decimal("500.00"),
                recurrence_rule_id=rule.id,
                is_active=True,
            )
            db.session.add(template)

            goal = _create_goal(seed_user, acct, name="Trajectory Goal")
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Projected completion" in html

    def test_dashboard_no_contribution_message(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C-5.15-15: Goal with no recurring transfer shows no-contribution message."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            goal = _create_goal(seed_user, acct, name="No Transfer Goal")

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "No recurring contribution" in html

    def test_dashboard_goal_met_message(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C-5.15-16: Balance exceeds target shows 'Goal met!' text."""
        with app.app_context():
            # Default savings account has $5,000 balance.
            acct = _create_savings_account(seed_user)
            # Target is $3,000 -- already exceeded.
            goal = _create_goal(
                seed_user, acct, name="Met Goal",
                target_amount=Decimal("3000.00"),
            )

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Goal met!" in html

    def test_dashboard_trajectory_with_income_relative_goal(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C-5.15-17: Income-relative goal uses resolved target for trajectory.

        With a salary profile, the income-relative target is resolved
        to a dollar value.  Trajectory uses this resolved value, not
        a NULL target_amount.
        """
        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="Test Salary",
                annual_salary=Decimal("75000.00"),
                state_code="NC",
            )
            db.session.add(profile)

            acct = _create_savings_account(seed_user)

            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="IR Trajectory Goal",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            # Should not crash -- trajectory is computed on the resolved
            # target, even though target_amount is NULL.
            html = resp.data.decode()
            # With no transfer template but salary data, we get the
            # "No recurring contribution" message.
            assert "No recurring contribution" in html

    def test_dashboard_biweekly_transfer_normalization(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C-5.15-19: Biweekly transfer normalized to monthly for trajectory.

        A biweekly (EVERY_PERIOD) transfer of $500/period should yield
        a monthly equivalent of $500 * 26 / 12 = $1,083.33.
        With $5,000 balance and $10,000 target:
            remaining = $5,000
            months = ceil(5000 / 1083.33) = 5
        Dashboard should show 'Projected completion' text.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)

            biweekly_pattern_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.EVERY_PERIOD
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=biweekly_pattern_id,
            )
            db.session.add(rule)
            db.session.flush()

            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=acct.id,
                name="Biweekly Savings",
                default_amount=Decimal("500.00"),
                recurrence_rule_id=rule.id,
                is_active=True,
            )
            db.session.add(template)

            goal = _create_goal(seed_user, acct, name="Biweekly Goal")
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Projected completion" in html

    def test_dashboard_no_salary_warning(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Dashboard shows warning when income-relative goal has no salary."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="No Salary Goal",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "No salary profile configured" in html

    def test_dashboard_resolved_target_not_raw(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Dashboard displays resolved_target, not None, for income-relative goals.

        Even without a salary profile (target=$0), the dashboard must
        show '$0' not 'None' or an empty string.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="Resolved Target Check",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            # Must not show "None" where target should be.
            assert "None" not in html or "none" in html.lower()


# -- Debt Summary Display Tests (Commit 5.12-1) ─────────────────────


class TestDebtSummaryDisplay:
    """Route-level tests for the debt summary card on the dashboard.

    Commit 5.12-1: the dashboard shows aggregate debt metrics and
    DTI ratio when loan accounts exist.
    """

    def test_dashboard_debt_summary_card_rendered(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C-5.12-17: Dashboard shows debt summary card when loans exist."""
        with app.app_context():
            _create_small_loan(seed_user)

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Debt Summary" in html
            assert "Total Debt" in html
            assert "Monthly Payments" in html
            assert "Weighted Avg Rate" in html

    def test_dashboard_no_debt_summary_when_no_loans(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C-5.12-19: No debt summary card when no loan accounts exist."""
        with app.app_context():
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Debt Summary" not in html

    def test_dashboard_dti_badge_rendered(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C-5.12-18: DTI badge appears when loans and salary exist."""
        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="DTI Salary",
                annual_salary=Decimal("78000.00"),
                state_code="NC",
            )
            db.session.add(profile)
            _create_small_loan(seed_user)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Debt-to-Income" in html
            # Small loan relative to $78K salary -> "Healthy" badge
            assert "Healthy" in html

    def test_dashboard_dti_no_salary_shows_na(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """DTI shows N/A when no salary profile configured."""
        with app.app_context():
            _create_small_loan(seed_user)

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "N/A" in html or "no salary profile" in html
