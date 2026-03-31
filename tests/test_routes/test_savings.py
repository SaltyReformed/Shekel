"""
Shekel Budget App -- Savings Route Tests

Tests for the savings dashboard and goal CRUD endpoints:
  - Dashboard rendering (with/without accounts, goals)
  - Goal creation (happy path, validation, IDOR)
  - Goal editing (happy path, IDOR)
  - Goal deletion (soft-deactivate, IDOR)
  - Double-submit (unique constraint on user+account+name)
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app import ref_cache
from app.enums import RecurrencePatternEnum, StatusEnum, TxnTypeEnum
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
            # Need enough periods so milestone offsets (6, 13, 26) are reachable
            # from the current period. Generate 40 periods starting well before today.
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 1, 2),
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
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 1, 2),
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
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 1, 2),
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
        """GET /savings/goals/999999/edit for a nonexistent goal redirects with flash."""
        with app.app_context():
            resp = auth_client.get(
                "/savings/goals/999999/edit", follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"Goal not found." in resp.data

    def test_update_nonexistent_goal(self, app, auth_client, seed_user):
        """POST /savings/goals/999999 for a nonexistent goal redirects with flash."""
        with app.app_context():
            resp = auth_client.post("/savings/goals/999999", data={
                "name": "Ghost Goal",
                "target_amount": "5000.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Goal not found." in resp.data

    def test_delete_nonexistent_goal(self, app, auth_client, seed_user):
        """POST /savings/goals/999999/delete for a nonexistent goal redirects with flash."""
        with app.app_context():
            resp = auth_client.post(
                "/savings/goals/999999/delete", follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"Goal not found." in resp.data

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

            assert resp.status_code == 200
            assert b"Goal not found." in resp.data

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
