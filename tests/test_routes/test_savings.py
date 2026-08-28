"""
Shekel Budget App -- Savings Route Tests

Tests for the savings dashboard and goal CRUD endpoints:
  - Dashboard rendering (with/without accounts, goals)
  - Goal creation (happy path, validation, IDOR)
  - Goal editing (happy path, IDOR)
  - Goal deletion (soft-deactivate, IDOR)
  - Double-submit (unique constraint on user+account+name)
"""

import re
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import pytest

from app import ref_cache
from app.enums import (
    CompoundingFrequencyEnum, EmployerContributionTypeEnum,
    GoalModeEnum, IncomeUnitEnum,
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
from app.services import balance_at, pay_period_write, savings_dashboard_service
from app.services.balance_at import BalanceContext

from tests._test_helpers import (
    create_hysa_account,
    create_loan_account,
    freeze_today,
    make_cadence_rule,
    settle_day_columns,
    settlement_columns,
    transient_cadence_rule,
)
from tests.oracles.recurrence_baseline import (
    EVERY_PERIOD,
    EVERY_N_PERIODS,
    MONTHLY,
    ANNUAL,
)


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
from app.services import account_service, obligations_aggregator
from app.services.pay_calendar import calendar_for
from app.services.auth_service import hash_password


# ── Helpers ──────────────────────────────────────────────────────────


def _create_savings_account(
    seed_user, name="Savings",
    anchor_balance=Decimal("5000.00"),
):
    """Create a savings account for the test user.

    Args:
        seed_user: The seed user fixture dict.
        name: Account display name (default "Savings").
        anchor_balance: Origination anchor balance (default
            $5,000.00).  Pass an explicit value when the test
            assertion depends on a non-default anchor; this routes
            through ``account_service.create_account`` so the dated
            ``AccountAnchorHistory`` SoT (E-19, Commit 4) and the
            cache columns agree from t0.  Required by Commit 6:
            ``cash_ledger.resolve_anchor`` reads history, so
            mutating only the cache columns after creation no longer
            propagates to ``/savings``.
        anchor_period_id: Pay period to anchor the new account
            against.  ``None`` falls back to
            ``account_service.resolve_anchor_period_id`` (the
            user's earliest pay period at create time).

    Returns:
        Account: the new savings account, COMMITTED.  Committed rather than
        flushed (plan step balance:X-i3): the callers that go on to issue a
        request need the account to exist as far as that request is concerned,
        and a request holds a transaction of its own in which an uncommitted
        row does not.
    """
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name=name,
            anchor_balance=anchor_balance,
        ),
    )
    db.session.add(acct)
    db.session.commit()
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


    # Bootstrap pay period (E-19, Commit 3): the
    # account_service factory requires the user to have at
    # least one pay period to anchor against.
    from datetime import date as _date, timedelta as _td
    from app.models.pay_period import PayPeriod as _PayPeriod
    _bootstrap = _PayPeriod(
        user_id=other_user.id,
        start_date=_date(2024, 1, 5),
        end_date=_date(2024, 1, 5) + _td(days=13),
        period_index=0,
    )
    db.session.add(_bootstrap)
    db.session.flush()
    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=other_user.id,
            account_type_id=savings_type.id,
            name="Other Savings",
            anchor_balance=Decimal("2000.00"),
        ),
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
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=acct_type.id,
            name="Test 401k",
            anchor_balance=Decimal("50000.00"),
        ),
    )
    db.session.add(acct)
    db.session.flush()

    params = InvestmentParams(
        account_id=acct.id,
        assumed_annual_return=Decimal("0.07000"),
        annual_contribution_limit=Decimal("23500.00"),
        contribution_limit_year=2026,
        employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
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
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=acct_type.id,
            name="Test 401k Employer",
            anchor_balance=Decimal("50000.00"),
        ),
    )
    db.session.add(acct)
    db.session.flush()

    params = InvestmentParams(
        account_id=acct.id,
        assumed_annual_return=Decimal("0.07000"),
        annual_contribution_limit=Decimal("23500.00"),
        contribution_limit_year=2026,
        employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.FLAT_PERCENTAGE),
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


def _create_expense_template(seed_user, cadence, amount, name="Test Expense",
                             is_active=True, interval_n=1):
    """Create an expense template on the seed user's checking account.

    **It takes the CADENCE rather than a pre-built rule, since plan step
    R-F6**, and authors it here: a rule carries its owning template's FK, so it
    cannot exist before the template does.  The two calls a caller used to make
    -- build the rule, then pass it in -- collapse into this one, and the
    separate ``_create_recurrence_rule`` helper is deleted.

    Args:
        seed_user: The seed user fixture dict.
        cadence: A ``tests.oracles.recurrence_baseline`` cadence constant, or
            ``None`` for a template that does not repeat (plan step R2e-3's
            shape).
        amount: Decimal default amount.
        name: Template display name.
        is_active: Whether the template is active (default True).
        interval_n: Interval for the every-N-paychecks cadence (default 1).

    Returns:
        TransactionTemplate: the new template, flushed for id assignment.
    """
    tmpl = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name=name,
        default_amount=amount,
        is_active=is_active,
    )
    db.session.add(tmpl)
    db.session.flush()
    if cadence is not None:
        make_cadence_rule(tmpl, cadence, interval_n=interval_n)
    return tmpl


def _create_test_transfer_template(seed_user, to_account, cadence, amount,
                                   name="Test Transfer", is_active=True):
    """Create a transfer template from checking to another account.

    Takes the CADENCE rather than a pre-built rule, for the reason
    :func:`_create_expense_template` gives.

    Args:
        seed_user: The seed user fixture dict (checking is the source).
        to_account: Destination Account object.
        cadence: A ``tests.oracles.recurrence_baseline`` cadence constant.
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
        name=name,
        default_amount=amount,
        is_active=is_active,
    )
    db.session.add(tmpl)
    db.session.flush()
    make_cadence_rule(tmpl, cadence)
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
            assert b"Accounts" in resp.data
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
            periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=start,
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
            amounts_int = [int(a.replace(',', '')) for a in amounts]

            # The cockpit card shows ONE consolidated projection line (the
            # furthest horizon, 1 year). With 7% annual return on $50k the
            # 1-year projection (~$53.5k) is the largest dollar figure on the
            # page -- above the ~$51k net-worth band (checking $1k + the $50k
            # account summed at today's balances, NOT the projection) and the
            # $50k anchor -- so max > $52k proves growth was applied (a flat
            # balance would leave the max at the $51k band total).
            assert amounts_int, f"no dollar amounts rendered; page: {html[:300]}"
            assert max(amounts_int) > 52000, (
                "Expected the 1-year projection to exceed $52,000 with 7% "
                f"growth; largest amount was ${max(amounts_int):,}. "
                f"Amounts on page: {amounts}"
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
            periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=start,
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
            amounts_int = [int(a.replace(',', '')) for a in amounts]

            # The cockpit card shows ONE consolidated projection line (1 year).
            # With $500/period employee + ~$192/period employer (5% of $100k/26)
            # + 7% growth on $50k, the 1-year projection is ~$72k -- well above
            # both the growth-only baseline (~$53.5k) and the ~$51k net-worth
            # band -- so max > $60k proves the contributions are included.
            assert amounts_int, f"no dollar amounts rendered; page: {html[:300]}"
            assert max(amounts_int) > 60000, (
                "Expected the 1-year projection to exceed $60,000 with "
                f"contributions; largest amount was ${max(amounts_int):,}. "
                f"Amounts on page: {amounts}"
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
            # The BINDING went with the ``current_anchor_period_id`` line it
            # fed (ruling R-EH); the CALL is fixture setup and stays -- it is
            # what creates the periods this test projects over.
            pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=start,
                num_periods=40,
                cadence_days=14,
            )
            db.session.flush()

            # Create 401k with employer flat 5% but NO employee deduction.
            acct_type = db.session.query(AccountType).filter_by(name="401(k)").one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=acct_type.id,
                    name="Employer Only 401k",
                    anchor_balance=Decimal("50000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()

            params = InvestmentParams(
                account_id=acct.id,
                assumed_annual_return=Decimal("0.07000"),
                annual_contribution_limit=Decimal("23500.00"),
                contribution_limit_year=2026,
                employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.FLAT_PERCENTAGE),
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
            amounts_int = [int(a.replace(',', '')) for a in amounts]

            # The cockpit card shows ONE consolidated projection line (1 year).
            # With 5% employer flat on $100k/26 (~$192/period) + 7% growth on
            # $50k and no employee deduction, the 1-year projection is ~$58.7k --
            # above the growth-only baseline (~$53.5k) and the ~$51k net-worth
            # band -- so max > $55k proves the employer contribution is included.
            assert amounts_int, f"no dollar amounts rendered; page: {html[:300]}"
            assert max(amounts_int) > 55000, (
                "Expected the 1-year projection to exceed $55,000 with the "
                f"employer contribution; largest amount was ${max(amounts_int):,}. "
                f"Amounts on page: {amounts}"
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

    def test_update_goal_clears_target_date(
        self, app, auth_client, seed_user, seed_periods
    ):
        """An emptied target_date input clears the stored date.

        The nullable-field clear rule: ``target_date`` is allow_none
        on the update schema, so the empty submit loads as an explicit
        None (it used to be DROPPED, making the date unclearable from
        the UI) and the route's setattr loop nulls the column -- the
        goal reverts to no-deadline pacing.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            goal = _create_goal(seed_user, acct)
            assert goal.target_date == date(2027, 6, 1)

            resp = auth_client.post(f"/savings/goals/{goal.id}", data={
                "target_date": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.refresh(goal)
            assert goal.target_date is None

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
            other_acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=second_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Other Savings",
                    anchor_balance=Decimal("2000.00"),
                ),
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
        from app.models.category import Category  # pylint: disable=import-outside-toplevel
        from app.models.ref import Status  # pylint: disable=import-outside-toplevel
        from app.services import transfer_service  # pylint: disable=import-outside-toplevel

        with app.app_context():
            # Create HYSA account with known anchor balance, through the
            # shared factory rather than an inline account + InterestParams
            # pair: since plan step X-c2a modelled interest accrues only
            # forward of the account's latest ASSERTION, and the factory is
            # where that assertion's instant is pinned to the anchor period
            # (``account_service.create_account`` stamps it with the wall
            # clock, which for this suite's 2026-01-02 periods lands months
            # after the whole projection and would accrue nothing anywhere).
            hysa = create_hysa_account(
                seed_user, db.session, seed_periods[0], Decimal("10000.00"),
                apy=Decimal("0.04500"), name="High Yield Savings",
            )

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
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=hysa.id,
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("500.00"),
                    status_id=projected.id,
                    category_id=outgoing.id,
                ),
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
            # Origination anchor written at creation time so the
            # dated AccountAnchorHistory SoT (E-19 / Commit 4)
            # matches the test's intended anchor.  Pre-Commit-6
            # ``savings_dashboard_service`` read ``current_anchor_*``
            # columns directly; mutating those columns after
            # creation was enough to override the displayed balance.
            # Post-Commit-6 the resolver reads from history; the
            # cache mutation alone is reconciled-against, not honored
            # (the resolver logs EVT_ANCHOR_CACHE_RECONCILED and
            # returns the history value).  This is the documented
            # E-19 SoT shift, not a behavioral regression.
            savings = _create_savings_account(
                seed_user, name="Emergency Fund",
                anchor_balance=Decimal("3000.00"),
            )
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
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("1000.00"),
                    status_id=projected.id,
                    category_id=outgoing.id,
                ),
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
            # Origination anchor written at creation time -- see the
            # explanatory comment in
            # test_savings_balance_includes_transfer_deposit above
            # for the E-19 / Commit 6 SoT-shift rationale.
            savings = _create_savings_account(
                seed_user, name="Plain Savings",
                anchor_balance=Decimal("2000.00"),
            )
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
            # Origination anchor written at create time so the dated
            # AccountAnchorHistory SoT (E-19 / Commit 4) carries the
            # test's intended $10,000.  Mutating ``current_anchor_balance``
            # after the fact is reconciled-against by the canonical
            # producer (it logs EVT_ANCHOR_CACHE_RECONCILED and uses
            # history); the proper anchor-update path appends a fresh
            # history row, which is what the factory does at creation.
            savings = _create_savings_account(
                seed_user, name="EF Savings",
                anchor_balance=Decimal("10000.00"),
            )

            # Transfer template: checking -> savings, every period.
            _create_test_transfer_template(
                seed_user, savings, EVERY_PERIOD, Decimal("1500.00"),
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
            # Origination anchor written at create time so the dated
            # AccountAnchorHistory SoT (E-19 / Commit 4) carries the
            # test's intended $10,000.  Mutating ``current_anchor_balance``
            # after the fact is reconciled-against by the canonical
            # producer (it logs EVT_ANCHOR_CACHE_RECONCILED and uses
            # history); the proper anchor-update path appends a fresh
            # history row, which is what the factory does at creation.
            savings = _create_savings_account(
                seed_user, name="EF Savings",
                anchor_balance=Decimal("10000.00"),
            )

            # Create small settled expenses across 6 recent periods.
            settled_id = ref_cache.status_id(StatusEnum.DONE)
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
                    # A settled row carries the day its money moved AND the
                    # record of what moved -- one fact in three columns (plan
                    # steps X-f1 / X-au-c3), resolved by the one door a
                    # bare-built fixture uses.
                    **settle_day_columns(period.start_date),
                    **settlement_columns(
                        period.start_date, Decimal("10.00"),
                    ),
                )
                db.session.add(txn)

            # Transfer template with higher committed amount.
            _create_test_transfer_template(
                seed_user, savings, EVERY_PERIOD, Decimal("1500.00"),
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

    def test_emergency_fund_historical_excludes_non_checking_expenses(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The historical operand counts only checking-account expenses.

        DH-#29: ``_recent_settled_expenses_monthly`` is scoped to the
        same checking accounts as the committed floor, so a settled
        expense on a NON-checking account (here a Savings account) no
        longer inflates the emergency-fund denominator.  With no
        templates the floor is $0, so the displayed average is driven
        entirely by the historical operand -- which must reflect only
        the $120/period checking expenses, not the $300/period Savings
        ones.  Today is frozen to 2026-03-20 (autouse fixture), so the
        current period is seed_periods[5] and the recent-6 window is
        seed_periods[0:6]; seeding every window period the same amount
        makes the monthly average independent of the exact window.
        """
        with app.app_context():
            # Savings account so the emergency fund section renders and
            # supplies a non-checking account to spend from.
            savings = _create_savings_account(
                seed_user, name="EF Savings",
                anchor_balance=Decimal("10000.00"),
            )

            settled_id = ref_cache.status_id(StatusEnum.DONE)
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            category_id = seed_user["categories"]["Rent"].id

            # $120 on CHECKING and $300 on the non-checking SAVINGS
            # account in each of the 6 recent (window) periods; no
            # templates, so the floor is $0 and the historical operand
            # alone drives the displayed average.
            for period in seed_periods[0:6]:
                db.session.add(Transaction(
                    account_id=seed_user["account"].id,
                    pay_period_id=period.id,
                    scenario_id=seed_user["scenario"].id,
                    status_id=settled_id,
                    name="Checking Expense",
                    category_id=category_id,
                    transaction_type_id=expense_type_id,
                    estimated_amount=Decimal("120.00"),
                    **settle_day_columns(period.start_date),
                    **settlement_columns(
                        period.start_date, Decimal("120.00"),
                    ),
                ))
                db.session.add(Transaction(
                    account_id=savings.id,
                    pay_period_id=period.id,
                    scenario_id=seed_user["scenario"].id,
                    status_id=settled_id,
                    name="Savings Expense",
                    category_id=category_id,
                    transaction_type_id=expense_type_id,
                    estimated_amount=Decimal("300.00"),
                    **settle_day_columns(period.start_date),
                    **settlement_columns(
                        period.start_date, Decimal("300.00"),
                    ),
                ))
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()
            # Checking-only: $120/period * 26/12 = $260/mo.
            assert "$260/mo avg expenses" in html, (
                "Expected the checking-only historical average "
                "($260/mo); non-checking (Savings) expenses must be "
                "excluded from the emergency-fund denominator"
            )
            # Pre-fix (all accounts): ($120 + $300)/period * 26/12 =
            # $910/mo -- must NOT appear now that Savings is excluded.
            assert "$910/mo" not in html, (
                "Non-checking Savings expenses must not inflate the "
                "emergency-fund average (would read $910/mo if counted)"
            )

    def test_emergency_fund_with_no_history_uses_committed(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """With zero settled transactions, the committed baseline from
        active templates is used instead of the historical $0 average.
        """
        with app.app_context():
            # Origination anchor written at create time so the dated
            # AccountAnchorHistory SoT (E-19 / Commit 4) carries the
            # test's intended $10,000.  Mutating ``current_anchor_balance``
            # after the fact is reconciled-against by the canonical
            # producer (it logs EVT_ANCHOR_CACHE_RECONCILED and uses
            # history); the proper anchor-update path appends a fresh
            # history row, which is what the factory does at creation.
            savings = _create_savings_account(
                seed_user, name="EF Savings",
                anchor_balance=Decimal("10000.00"),
            )

            # Monthly expense template = $2,000/month.
            _create_expense_template(
                seed_user, MONTHLY, Decimal("2000.00"),
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
            # Origination anchor written at create time so the dated
            # AccountAnchorHistory SoT (E-19 / Commit 4) carries the
            # test's intended $10,000.  Mutating ``current_anchor_balance``
            # after the fact is reconciled-against by the canonical
            # producer (it logs EVT_ANCHOR_CACHE_RECONCILED and uses
            # history); the proper anchor-update path appends a fresh
            # history row, which is what the factory does at creation.
            savings = _create_savings_account(
                seed_user, name="EF Savings",
                anchor_balance=Decimal("10000.00"),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()
            # Section renders (savings > 0) but no expense info.
            assert "Emergency fund coverage" in html
            assert "avg expenses" not in html

    def test_emergency_fund_monthly_template_contribution(
        self, app, seed_user,
    ):
        """A monthly template contributes its exact default_amount as the
        monthly equivalent -- NOT multiplied by 26/12.
        """
        with app.app_context():
            tmpl = _create_expense_template(
                seed_user, MONTHLY, Decimal("500.00"),
                name="Monthly Subscription",
            )
            db.session.commit()

            result = obligations_aggregator.committed_monthly(
                [tmpl], date.today(), calendar_for(seed_user["user"].id),
            )
            assert result == Decimal("500.00"), (
                f"Monthly template should contribute exactly $500, got {result}"
            )

    def test_emergency_fund_excludes_non_repeating_templates(
        self, app, seed_user,
    ):
        """Non-repeating templates do not contribute to committed monthly.

        Only the recurring every-period template should be counted.  The
        non-repeating one carried a ``Once`` PATTERN until plan step R2e-3;
        it is now rule-less, which is the shape the aggregator filters on.
        """
        with app.app_context():
            once_tmpl = _create_expense_template(
                seed_user, None, Decimal("5000.00"),
                name="One-Time Purchase",
            )

            recurring_tmpl = _create_expense_template(
                seed_user, EVERY_PERIOD, Decimal("100.00"),
                name="Recurring Bill",
            )
            db.session.commit()

            result = obligations_aggregator.committed_monthly(
                [once_tmpl, recurring_tmpl], date.today(),
                calendar_for(seed_user["user"].id),
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
            # Origination anchor written at create time so the dated
            # AccountAnchorHistory SoT (E-19 / Commit 4) carries the
            # test's intended $10,000.  Mutating ``current_anchor_balance``
            # after the fact is reconciled-against by the canonical
            # producer (it logs EVT_ANCHOR_CACHE_RECONCILED and uses
            # history); the proper anchor-update path appends a fresh
            # history row, which is what the factory does at creation.
            savings = _create_savings_account(
                seed_user, name="EF Savings",
                anchor_balance=Decimal("10000.00"),
            )

            # Inactive template -- excluded by route query.
            _create_expense_template(
                seed_user, EVERY_PERIOD, Decimal("999.00"),
                name="Inactive Bill", is_active=False,
            )

            # Active template -- included.
            _create_expense_template(
                seed_user, EVERY_PERIOD, Decimal("1500.00"),
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

        The column is NOT NULL in the schema, but the function handles it
        defensively for robustness, so the row is built UNSAVED rather than
        flushed.

        **Both halves are real model instances from plan step R9**, where the
        rule half was a ``SimpleNamespace`` carrying four hand-listed
        attributes -- one of them ``pattern_id``, a column dropped at R7c-c
        that nothing had read since.  A stand-in that lists what the reader
        happens to touch stops being a stand-in the moment the reader touches
        something else, and it cannot fail loud when it does: the shape it
        mirrors has no constraint to violate.  An unsaved
        ``TransactionTemplate`` holding a rule authored through the real door
        costs no flush and cannot drift from either model.
        """
        with app.app_context():
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                name="No amount",
                default_amount=None,
                recurrence_rule=transient_cadence_rule(
                    seed_user["user"].id, EVERY_PERIOD,
                ),
            )

            result = obligations_aggregator.committed_monthly(
                [template], date.today(),
                calendar_for(seed_user["user"].id),
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
            tmpl = _create_expense_template(
                seed_user, EVERY_N_PERIODS, Decimal("600.00"),
                name="Biweekly Alternating", interval_n=2,
            )
            db.session.commit()

            result = obligations_aggregator.committed_monthly(
                [tmpl], date.today(), calendar_for(seed_user["user"].id),
            )
            assert result == Decimal("650.00"), (
                f"Expected 650.00 for every-2-periods template, got {result}"
            )

    def test_emergency_fund_annual_template(
        self, app, seed_user,
    ):
        """An annual template with $1,200 contributes $100.00 per month."""
        with app.app_context():
            tmpl = _create_expense_template(
                seed_user, ANNUAL, Decimal("1200.00"),
                name="Annual Insurance",
            )
            db.session.commit()

            result = obligations_aggregator.committed_monthly(
                [tmpl], date.today(), calendar_for(seed_user["user"].id),
            )
            assert result == Decimal("100.00"), (
                f"Expected 100.00 for annual template, got {result}"
            )

    def test_committed_monthly_empty_iterable(
        self, app, seed_user,
    ):
        """obligations_aggregator.committed_monthly([], today) returns zero."""
        with app.app_context():
            result = obligations_aggregator.committed_monthly(
                [], date.today(), calendar_for(seed_user["user"].id),
            )
            assert result == Decimal("0.00"), (
                f"Expected 0.00 for empty iterable, got {result}"
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
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="Unconfigured HYSA",
                    anchor_balance=Decimal("5000.00"),
                ),
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
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="Configured HYSA",
                    anchor_balance=Decimal("5000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            # HIGH-06 / Commit 24: ``apy`` is NOT NULL with no
            # server_default; supply an explicit value.
            db.session.add(InterestParams(
                account_id=acct.id, apy=Decimal("0.04500"),
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
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
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=k401_type.id,
                    name="Unconfigured 401k",
                    anchor_balance=Decimal("10000.00"),
                ),
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
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=k401_type.id,
                    name="Configured 401k",
                    anchor_balance=Decimal("10000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InvestmentParams(
                account_id=acct.id,
                employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                    EmployerContributionTypeEnum.NONE,
                ),
            ))
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
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=k401_type.id,
                    name="Legacy 401k",
                    anchor_balance=Decimal("50000.00"),
                ),
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

    Routes through the ONE shared loan builder
    (:func:`tests._test_helpers.create_loan_account`), whose defaults are this
    loan: an Auto Loan originated 2026-01-01 with payment_day 1, so remaining
    months is comfortably positive (~21 from April 2026).  The factory opens
    the loan's genesis posting ledger in the same transaction as its
    ``LoanParams``, exactly as ``loan.create_params`` does in production; the
    hand-rolled block this replaced never did, so these cards rendered a loan
    in a state production cannot produce.
    """
    return create_loan_account(
        seed_user, db.session, name=name, principal=principal,
        rate=rate, term=term,
    )


def _make_confirmed_transfer(seed_user, to_account, period, amount):
    """Create a confirmed (Paid) transfer to a loan account."""
    from app.services import transfer_service  # pylint: disable=import-outside-toplevel

    return transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=to_account.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount=amount,
            status_id=ref_cache.status_id(StatusEnum.DONE),
            category_id=seed_user["categories"]["Rent"].id,
        ),
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
            # Payoff recorded as a balance true-up to $0 (cash lump sums
            # no longer auto-pay-off under the contractual schedule).
            from tests._test_helpers import insert_trueup_event  # pylint: disable=import-outside-toplevel
            insert_trueup_event(acct.loan_params, Decimal("0.00"))
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
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=acct.id,
                    pay_period_id=seed_periods[7].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("1100.00"),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=seed_user["categories"]["Rent"].id,
                ),
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
            # Payoff recorded as a balance true-up to $0.
            from tests._test_helpers import insert_trueup_event  # pylint: disable=import-outside-toplevel
            insert_trueup_event(acct.loan_params, Decimal("0.00"))
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
            # Payoff recorded as a balance true-up to $0; the $5,000 loan
            # stays active (no true-up), so only one Paid Off badge.
            from tests._test_helpers import insert_trueup_event  # pylint: disable=import-outside-toplevel
            insert_trueup_event(paid_off.loan_params, Decimal("0.00"))
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
            archived = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Hidden Savings",
                    anchor_balance=Decimal("500.00"),
                ),
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
            archived = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Old Account",
                    anchor_balance=Decimal("0"),
                ),
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
            archived = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Closed Savings",
                    anchor_balance=Decimal("0.00"),
                ),
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
            archived = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Restore Me",
                    anchor_balance=Decimal("0"),
                ),
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
        """Paid-off loan card shows the 'Paid Off' badge and an archive action.

        The Net Worth Cockpit rebuild moved archive off the card face into
        the per-card kebab (audit Surface 2 / decision 9); a paid-off loan is
        now signalled by the 'Paid Off' badge, and the archive affordance is
        the kebab's archive form (its action URL asserted below).
        """
        with app.app_context():
            acct = _create_small_loan(seed_user, name="Paid Off Archival")
            _make_confirmed_transfer(
                seed_user, acct, seed_periods[7], Decimal("1100.00"),
            )
            # Payoff recorded as a balance true-up to $0.
            from tests._test_helpers import insert_trueup_event  # pylint: disable=import-outside-toplevel
            insert_trueup_event(acct.loan_params, Decimal("0.00"))
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Paid Off" in html
            assert f"/accounts/{acct.id}/archive" in html

    def test_archived_account_no_projections(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Archived accounts show last balance, not projected balances."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            archived = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Old Savings",
                    anchor_balance=Decimal("5000.00"),
                ),
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
            active_acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Active Savings",
                    anchor_balance=Decimal("3000.00"),
                ),
                is_active=True,
            )
            archived_acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Archived Savings",
                    anchor_balance=Decimal("1000.00"),
                ),
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

            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=acct.id,
                name="Monthly Savings",
                default_amount=Decimal("500.00"),
                is_active=True,
            )
            db.session.add(template)
            db.session.flush()
            # The definition first, then the cadence onto it (plan step R-F6).
            make_cadence_rule(template, MONTHLY)

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

            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=acct.id,
                name="Biweekly Savings",
                default_amount=Decimal("500.00"),
                is_active=True,
            )
            db.session.add(template)
            db.session.flush()
            # The definition first, then the cadence onto it (plan step R-F6).
            make_cadence_rule(template, EVERY_PERIOD)

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
        """C-5.12-17: Dashboard shows debt summary when loans exist.

        D6-F fold: the standalone Debt Summary card is retired; its metrics
        now render as the Liabilities group card's footer line, so assert
        against that folded copy.
        """
        with app.app_context():
            _create_small_loan(seed_user)

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Avg rate" in html
            # X-q3 / N-99: the caption says what the date measures -- the
            # payoff of the debts that HAVE a payoff model.
            assert "Loans paid off" in html
            assert "Payoff Strategies" in html

    def test_a_revolving_balance_is_captioned_beside_the_payoff_date(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The rendered footer names the debt the payoff date cannot cover.

        Plan step X-q3, finding N-99.  The date is derived over amortizing
        loans -- the only debts with a payoff model -- so a Credit Card, which
        the seam holds FLAT at its owed magnitude and which therefore never
        reaches zero, is invisible to it.  The page says both: when the loans
        are paid off, and what that does not speak for.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import (
            create_account_of_type, create_loan_account,
        )

        with app.app_context():
            create_loan_account(seed_user, db.session, name="Auto Loan")
            create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Loans paid off" in html
            assert "excludes" in html
            assert "$500.00" in html
            assert "revolving" in html

    def test_a_borrower_whose_loans_are_all_retired_is_told_so(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The footer's THIRD state renders: "All loans paid off".

        Plan step X-s3, ruling R-BE, finding N-104.  The seam's
        :class:`~app.services.savings_dashboard_service.LoanPayoffOutlook` has
        three states and the footer rendered two: a date, or "no payoff date at
        current payments".  The third -- every loan the borrower holds is
        retired -- fell through the chain in SILENCE, because the debt summary
        copied the outlook's two stored fields and dropped its derived
        ``is_loan_free``, so the template had nothing to branch on.  The
        summary now carries the outlook whole and this is the state's first
        reader anywhere in ``app/``.

        The loan is retired the way the app's own true-up UI retires one -- a
        recorded balance true-up to ``$0.00`` with no payment rows -- which is
        the shape that reads ``is_retired`` without ``is_paid_off`` (finding
        B-16's fixture, reused deliberately).  The two states this must NOT be
        confused with are asserted absent: a payoff DATE, and the unclearing
        warning.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import insert_trueup_event, loan_params_for

        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Only Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 1),
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
            )
            db.session.commit()

            # The precondition that makes the fixture discriminating: the loan
            # owes nothing and is NOT badged, so it is retired without being
            # "paid off" in the confirmed-payment sense.
            ctx = BalanceContext.build(seed_user["user"].id)
            figures = balance_at.loan_figures(loan, ctx)
            assert figures.is_retired is True
            assert figures.is_paid_off is False

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The footer is present at all (this user HAS a loan account), and
            # it renders the congratulation rather than nothing.
            assert "Avg rate" in html
            assert "All loans paid off" in html
            # And not either of the other two states.
            assert "Loans paid off " not in html
            assert "No loan payoff date at current payments" not in html

    def test_the_loan_cell_renders_its_rate_payment_and_payoff(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The three loan-cell figures reach the page off the seam's bundle.

        Plan step X-r moved the projection dict from six flattened copies of
        ``LoanFigures`` to the value object itself, and four cockpit sites now
        read through it.  This pins three of them; the fourth, the "Paid Off"
        badge, is already covered by ``TestPaidOffBadge``.

        **What the Jinja risk actually is, measured rather than assumed.**  A
        mis-pointed attribute on the two MONEY sites and the payoff raises
        (``money()`` compares the value, ``to_percent`` constructs a
        ``Decimal``, ``strftime`` is an attribute access on ``Undefined``), so
        those fail loud on their own.  The badge guard is the one site that
        degrades SILENTLY -- ``Undefined`` is falsy, so the badge just stops
        rendering -- and it is the one this test does not need to cover.
        Both halves were confirmed by mutating the template.

        A $1,000.00 24-month auto loan at 5.000%: the rate renders to three
        decimals (the debt footer's own rate uses two, so the assertion cannot
        be satisfied by that one), and the level payment is
        1000 * r(1+r)^24 / ((1+r)^24 - 1) at r = 0.05/12 = $43.87 -- asserted
        glued to the cell's own markup, because with one loan and no escrow
        the footer's monthly total is the SAME number and a bare substring
        would be satisfied by it.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Auto Loan",
                principal=Decimal("1000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 1),
            )
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            figures = balance_at.loan_figures(loan, ctx)

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "5.000%" in html
            assert (
                'Monthly Payment <span class="font-mono">$43.87</span>' in html
            )
            # The payoff month is read back off the seam deliberately: this
            # module's clock is NOT frozen, and the fold's zero crossing moves
            # with it (an installment already due and unpaid pushes it out).
            # The DERIVATION is oracle-tested at
            # ``test_loan_payoff_date_oracle.py``; what is asserted here is
            # that the cell renders it, and the string "payoff <Mon YYYY>"
            # appears nowhere else on the page.
            assert figures.payoff_date is not None
            assert (
                "payoff " + figures.payoff_date.strftime("%b %Y")
            ) in html

    def test_dashboard_no_debt_summary_when_no_loans(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C-5.12-19: No debt summary when no loan accounts exist.

        D6-F fold: the negative case now checks the same folded-footer
        marker ("Avg rate") the positive test asserts, since the standalone
        "Debt Summary" heading no longer exists.
        """
        with app.app_context():
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Avg rate" not in html

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
            assert "DTI" in html
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


class TestDashboardNetWorthContext:
    """Tests for the net-worth cockpit data exposed by the /savings route.

    The route hands the template the money-precise ``net_worth`` figures
    (today totals + change + the forward trend series) and serializes the
    trend to a Chart.js JSON string (``net_worth_chart_json``); ``float``
    is applied only at that route boundary.  The template rebuild that
    renders this data is a later phase, so these tests inspect the context
    and the serialized contract directly.
    """

    @staticmethod
    def _capture_dashboard_context(app, auth_client):
        """Return the (template, context) captured from GET /savings.

        Uses Flask's ``template_rendered`` signal so the test reads the
        exact context the route handed ``render_template`` without parsing
        HTML.  Asserts the dashboard template rendered (a 200 page), so the
        test fails loud rather than inspecting a redirect.
        """
        # Pylint: import-outside-toplevel -- deferred import is the
        # file-wide test convention.
        from flask import template_rendered  # pylint: disable=import-outside-toplevel

        recorded = []

        def _record(sender, template, context, **extra):
            recorded.append((template, context))

        template_rendered.connect(_record, app)
        try:
            response = auth_client.get("/savings")
        finally:
            template_rendered.disconnect(_record, app)
        assert response.status_code == 200, (
            f"GET /savings returned {response.status_code}; expected 200"
        )
        records = [
            (t, c) for t, c in recorded
            if t.name == "savings/dashboard.html"
        ]
        assert records, (
            "GET /savings did not render savings/dashboard.html; rendered: "
            f"{[t.name for t, _ in recorded]!r}"
        )
        return records[0]

    def test_context_carries_net_worth_decimal_figures(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The context's net_worth dict carries money-precise Decimals.

        With the seed Checking ($1,000) plus an added Savings ($4,000) and
        no transactions, the today figures are flat:
          total_assets = 1000.00 + 4000.00 = 5000.00, liabilities 0.00,
          net_worth 5000.00, liquid 5000.00.
        """
        with app.app_context():
            _create_savings_account(
                seed_user, name="Savings",
                anchor_balance=Decimal("4000.00"),
            )
            db.session.commit()

            _template, context = self._capture_dashboard_context(
                app, auth_client,
            )
            net_worth = context["net_worth"]

            # 1000.00 + 4000.00 = 5000.00
            assert net_worth.today.total_assets == Decimal("5000.00")
            assert net_worth.today.total_liabilities == Decimal("0.00")
            assert net_worth.today.net_worth == Decimal("5000.00")
            assert net_worth.today.liquid == Decimal("5000.00")

    def test_chart_json_parses_to_expected_shape_with_floats(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """net_worth_chart_json carries both ranges as float series.

        The route serializes ONE payload with the ``2 years`` totals (parallel
        ``net`` / ``assets`` / ``liabilities`` float arrays + the per-category
        ``composition`` split) and the nested ``horizon`` range (P-AC1).  With
        the seed Checking ($1,000) plus an added Savings ($4,000) and flat
        balances, every ``2 years`` ``net`` point is ``5000.0`` (the float
        boundary), and the horizon starts there too.
        """
        # pylint: disable=import-outside-toplevel
        import json
        with app.app_context():
            _create_savings_account(
                seed_user, name="Savings",
                anchor_balance=Decimal("4000.00"),
            )
            db.session.commit()

            _template, context = self._capture_dashboard_context(
                app, auth_client,
            )
            chart = json.loads(context["net_worth_chart_json"])

            # The payload carries only what net_worth_cockpit.js reads (plan
            # step X-s1, finding N-104): the top-level ``assets`` /
            # ``liabilities`` totals it used to ship reach no consumer, since
            # ``selectRange`` never names them and the stacked bands already
            # ARE those totals -- asserted below against the PRODUCER series,
            # which keeps both keys for the cross-page equality oracle.
            assert set(chart.keys()) == {
                "labels", "net", "current_index", "composition", "horizon",
            }
            series = context["net_worth"].series
            n = len(series.periods)
            assert n > 0
            assert len(chart["labels"]) == n
            assert len(chart["net"]) == n
            # float boundary: every value is a float, not a Decimal/str.
            assert all(isinstance(v, float) for v in chart["net"])
            # Flat $5,000 net worth at every trend point -> 5000.0.
            assert chart["net"][0] == 5000.0
            # current_index (the solid/dashed boundary) passes straight
            # through from the producer's series; an int in [0, n].
            assert chart["current_index"] == series.current_index
            assert isinstance(chart["current_index"], int)
            assert 0 <= chart["current_index"] <= n

            # The 2-year composition: each band a float list of length n, and
            # the bands reconciling to ``net`` at every point.  The payload's
            # ``assets`` / ``liabilities`` totals were deleted at X-s1, and so
            # were the producer's -- they were the same per-period sums under a
            # second key -- so the reconciliation is against ``net``, the one
            # total that survives because the client draws it.
            comp = chart["composition"]
            asset_bands = ("asset", "retirement", "investment", "other")
            for band in (*asset_bands, "liability"):
                assert len(comp[band]) == n
                assert all(isinstance(v, float) for v in comp[band])
            for i in range(n):
                asset_side = sum(comp[band][i] for band in asset_bands)
                assert asset_side - comp["liability"][i] == chart["net"][i]
                assert chart["net"][i] == float(series.net[i])

            # The horizon range: a float net series that starts at the hero
            # ($5,000), plus composition bands + milestone list.
            horizon = chart["horizon"]
            assert horizon is not None
            assert horizon["net"][0] == 5000.0
            assert all(isinstance(v, float) for v in horizon["net"])
            assert isinstance(horizon["composition"], dict)
            assert isinstance(horizon["milestones"], list)
            assert horizon["current_index"] == 0

    def test_dashboard_still_renders_with_net_worth_wired(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The existing /savings page still renders 200 with net worth added.

        The template rebuild is a later phase; adding the net-worth context
        keys must not break the current render.
        """
        with app.app_context():
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Accounts" in resp.data


class TestHorizonSerialization:
    """Unit tests for the Horizon-range Chart.js serializer (P-AC1 Loop B P1).

    ``_serialize_horizon`` is the float boundary for the horizon range: it
    maps the producer's ``Decimal`` band series + net trajectory to floats,
    the annual dates to ``%b %Y`` labels, and each milestone to its chip
    ``label`` plus the fractional axis position ``x`` its ``date`` is spent
    computing (plan step X-s1 -- the ISO ``date`` and the machine ``kind`` this
    used to carry reached no client reader).
    """

    def test_maps_decimals_dates_and_milestones(self):
        """Decimals become floats, dates become labels, milestone dates ISO."""
        # pylint: disable=import-outside-toplevel
        from datetime import date
        from app.routes.savings import _serialize_horizon
        horizon = {
            "dates": [date(2026, 7, 12), date(2026, 12, 31)],
            "net": [Decimal("236184.51"), Decimal("240000.00")],
            "composition": {
                "asset": [Decimal("358034.92"), Decimal("360000.00")],
                "liability": [Decimal("192941.56"), Decimal("190000.00")],
            },
            "milestones": [
                {"date": date(2048, 12, 1), "label": "Debt-free"},
            ],
            "current_index": 0,
        }

        out = _serialize_horizon(horizon)

        assert out["labels"] == ["Jul 2026", "Dec 2026"]
        assert out["net"] == [236184.51, 240000.0]
        assert all(isinstance(v, float) for v in out["net"])
        assert out["composition"]["asset"] == [358034.92, 360000.0]
        assert all(
            isinstance(v, float) for v in out["composition"]["liability"]
        )
        # The milestone serializes its chip ``label`` plus a fractional axis
        # position ``x``, and nothing else (plan step X-s1): the producer's
        # ``date`` is SPENT here computing ``x`` and the client never reads a
        # date.  This one (2048) is beyond the two sample dates (ending
        # 2026-12-31), so ``x`` clamps to the last index (1.0) -- the flag pins
        # to the right edge.
        assert out["milestones"] == [{"label": "Debt-free", "x": 1.0}]
        assert out["current_index"] == 0
        # The serializer's OWN key set, pinned.  The mutation guard beside this
        # proves every key the PRODUCER publishes is consumed; it says nothing
        # about a key the serializer invents, so re-adding ``horizon_end`` --
        # the exact key plan step X-q2 deleted -- would otherwise pass every
        # test in this class.  Found by X-s's adversarial review.
        assert set(out) == {
            "labels", "net", "composition", "milestones", "current_index",
        }

    def test_none_passes_through(self):
        """A ``None`` horizon (no pay periods) serializes to ``None``."""
        # pylint: disable=import-outside-toplevel
        from app.routes.savings import _serialize_horizon
        assert _serialize_horizon(None) is None

    def test_every_published_key_is_read(
        self, app, db, seed_user, seed_periods,
    ):
        """Every key the producer publishes is one this serializer consumes.

        The route's half of the contract plan step X-q2 established, and it is
        a MUTATION rather than a literal: each key is removed from a REAL
        producer payload in turn and the serializer must break on it.  A key a
        future step adds without a consumer therefore fails here, where a list
        of expected names would have passed -- which is finding N-100's own
        history, since ``horizon_end`` and ``is_loan_free`` shipped for months
        as producer outputs no serializer, template or script ever named.

        **It descends into the MILESTONE dicts too** (plan step X-s1, finding
        N-104).  Stopping at the top level is how the milestones' machine
        ``kind`` survived X-q2's certification: a dead key riding inside a live
        one, invisible to a guard that only removes top-level names.

        **The fixture carries BOTH milestone builders' output, and that took
        two corrections** (X-s's adversarial review).  A loan-free fixture
        publishes an EMPTY milestone list, so the descent would iterate nothing
        and pass while proving nothing -- hence the loan, and hence the count
        asserted before the loop rather than trusted (finding N-69's shape).
        With only the loan the list holds STRUCTURAL flags alone, so a key that
        just ``_net_crossing_milestones`` emits was still invisible.  The loan
        is therefore MORTGAGE-shaped (360 months): the horizon runs to its
        payoff plus a year, which is the axis length a ``$500k`` crossing needs
        room on -- a 24-month loan gives a ~3-year window the 401(k) cannot
        grow across, measured.  Both counts are asserted below for the same
        reason the first one is.

        The producer's half is two tests, because the two key sets are pinned
        where each has a non-vacuous fixture:
        ``TestNetWorthHorizon.test_publishes_only_the_keys_the_page_reads``
        pins the TOP-LEVEL set (its loan-free fixture publishes no milestone at
        all, so the nested set cannot be asserted there), and
        ``TestNetWorthHorizon.test_debt_free_milestone_at_payoff`` pins the
        MILESTONE set over a fixture that plants one.
        """
        # pylint: disable=import-outside-toplevel
        from app.routes.savings import _serialize_horizon
        from app.services import savings_dashboard_service
        from tests._test_helpers import make_investment_account
        with app.app_context():
            _create_small_loan(
                seed_user, name="Mortgage",
                principal=Decimal("200000.00"), term=360,
            )
            make_investment_account(
                seed_user, db.session, seed_periods[0], Decimal("300000.00"),
            )
            db.session.commit()
            horizon = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["net_worth"].horizon
            assert horizon is not None
            # The unmutated payload serializes, so a failure below is the
            # missing key and never a broken fixture.
            assert _serialize_horizon(horizon) is not None

            for key in sorted(horizon):
                partial = {
                    name: value for name, value in horizon.items()
                    if name != key
                }
                with pytest.raises(KeyError) as excinfo:
                    _serialize_horizon(partial)
                # The REMOVED key must be the one that raised: a bare
                # ``raises(KeyError)`` would accept an incidental miss from
                # some later refactor and go on reading as proof.
                assert excinfo.value.args[0] == key

            # The nested arm, on the same rule.  The loan's payoff is in the
            # future, so the producer plants at least the debt-free flag.
            assert horizon["milestones"], (
                "the fixture published no milestones, so the nested arm below "
                "would pass without removing anything"
            )
            # BOTH builders contributed, so the union below is a union of two
            # key sets and not one restated.  Told apart by what only a
            # crossing carries -- a "Net $" label -- since the machine ``kind``
            # that used to distinguish them is what X-s1 deleted.  A label is
            # user-collidable in general (finding N-110, ruled acceptable at
            # plan step X-t4: identify a flag by its ``(label, date)`` pair),
            # but this fixture names its own accounts, so the prefix separates
            # the two builders here.
            crossings = [
                m for m in horizon["milestones"]
                if m["label"].startswith("Net $")
            ]
            assert crossings, (
                "the fixture published no net-crossing milestone, so a key "
                "only that builder emits would be invisible below"
            )
            assert len(crossings) < len(horizon["milestones"]), (
                "the fixture published no STRUCTURAL milestone beside them"
            )
            # The UNION of every milestone's keys, not the first one's: the
            # structural flags and the net-worth crossings are built by two
            # different functions, so a key only one of them emits is invisible
            # to a probe that reads ``milestones[0]`` -- and the structural flag
            # sorts first here.  Found by X-s's adversarial review.
            milestone_keys = set()
            for milestone in horizon["milestones"]:
                milestone_keys |= set(milestone)
            for key in sorted(milestone_keys):
                stripped = [
                    {
                        name: value for name, value in milestone.items()
                        if name != key
                    }
                    for milestone in horizon["milestones"]
                ]
                with pytest.raises(KeyError) as excinfo:
                    _serialize_horizon({**horizon, "milestones": stripped})
                assert excinfo.value.args[0] == key


class TestMilestoneAxisX:
    """Tests for the milestone fractional-index helper (P-AC1 Loop B P2).

    ``_milestone_axis_x`` places a milestone (an exact date) on the Horizon
    stream's annual category axis as a fractional index, so the client's flag
    plugin can position the flag between the year-end samples via
    ``getPixelForValue``.
    """

    def test_positions_a_milestone_between_annual_samples(self):
        """A mid-year milestone maps to a fractional index between samples.

        Samples 2026-01-01 / 2026-12-31 / 2027-12-31; a milestone on
        2027-07-01 falls in the index-1..2 bracket, 182 of the 365 days from
        the index-1 sample, so x = 1 + 182 / 365.
        """
        # pylint: disable=import-outside-toplevel
        from datetime import date
        from app.routes.savings import _milestone_axis_x
        dates = [date(2026, 1, 1), date(2026, 12, 31), date(2027, 12, 31)]
        assert _milestone_axis_x(dates, date(2027, 7, 1)) == 1 + 182 / 365

    def test_exact_sample_and_out_of_range_dates_clamp(self):
        """A date on a sample lands on its index; out-of-range dates clamp."""
        # pylint: disable=import-outside-toplevel
        from datetime import date
        from app.routes.savings import _milestone_axis_x
        dates = [date(2026, 1, 1), date(2026, 12, 31), date(2027, 12, 31)]
        # The last sample -> the last index; the middle sample -> index 1.
        assert _milestone_axis_x(dates, date(2027, 12, 31)) == 2.0
        assert _milestone_axis_x(dates, date(2026, 12, 31)) == 1.0
        # Before the first sample -> 0.0; after the last -> the last index.
        assert _milestone_axis_x(dates, date(2020, 1, 1)) == 0.0
        assert _milestone_axis_x(dates, date(2030, 1, 1)) == 2.0


class TestNetWorthStreamRender:
    """The /savings page renders the P-AC1 net-worth stream element.

    The diverging allocation bar and the old trend chart (with its
    6/13/26/All picker) were replaced by ONE element: a range toggle over a
    stream canvas plus a composition legend.
    """

    def test_renders_range_toggle_canvas_and_legend(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The stream's range toggle, canvas, and legend render on the page.

        With the seed Checking ($1,000) plus an added Savings ($4,000) there
        is a current period, so the chart region renders: the page carries
        the `2 years` / `Horizon` range toggle (Horizon default), the stream
        canvas with its serialized data-chart, and the composition legend
        with an Assets band swatch (the two accounts are assets).  The
        retired diverging-bar markup is gone.
        """
        with app.app_context():
            _create_savings_account(
                seed_user, name="Savings",
                anchor_balance=Decimal("4000.00"),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The two-mode range toggle replacing the old picker + net/split.
            assert 'data-nw-range="horizon"' in html
            assert 'data-nw-range="2yr"' in html
            # The stream canvas carrying the serialized both-ranges payload.
            assert 'id="net-worth-chart-canvas"' in html
            assert "data-chart=" in html
            # The composition legend with the Assets band swatch.
            assert "nw-legend" in html
            assert "nw-legend__swatch--asset" in html
            # The retired diverging bar is gone.
            assert "nw-alloc__bar" not in html


class TestSparklines:
    """Tests for the per-account sparkline SVG-geometry serialization."""

    def test_normalizes_series_to_inverted_svg_polyline(self):
        """A series maps to evenly-spaced x and an inverted y in the viewBox.

        Three descending points (100 -> 50 -> 0) draw a falling line: the
        high value sits at the top (y 0) and the low at the bottom (y 28),
        with x evenly spaced across the 100-wide box.
        """
        # pylint: disable=import-outside-toplevel
        from app.routes.savings import _serialize_sparklines
        result = _serialize_sparklines({
            7: [Decimal("100"), Decimal("50"), Decimal("0")],
        })
        # low=0, span=100, last=2:
        #  i0 v100 -> x 0,   y 28 - 28 = 0
        #  i1 v50  -> x 50,  y 28 - 14 = 14
        #  i2 v0   -> x 100, y 28 - 0  = 28
        assert result[7] == "0.00,0.00 50.00,14.00 100.00,28.00"

    def test_empty_sparklines_serialize_to_empty(self):
        """No informative accounts -> no polylines."""
        # pylint: disable=import-outside-toplevel
        from app.routes.savings import _serialize_sparklines
        assert _serialize_sparklines({}) == {}


class TestCockpitSection:
    """Tests for GET /savings/cockpit -- the balanceChanged refresh fragment."""

    def test_cockpit_section_renders_region_for_htmx(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An HX request returns the cockpit region fragment (hero + cards).

        The region carries the net-worth hero label and the account's
        click-to-edit balance cell, but NOT the ``#cockpit-section`` wrapper
        (that lives in the page) -- proving it is the fragment the
        balanceChanged swap consumes, not the whole page.

        **The hero's FIGURE is asserted at its own anchor** (plan step X-w3,
        rewritten at X-w6 after both adversarial reviews found the first
        version could not fire).

        Two things were wrong with that version and both are worth recording.
        Its arm was ``body.count("$1,000.00") >= 3``, and the fragment carries
        SEVEN occurrences of that figure -- at least three from outside the
        hero region (the account's balance cell, the category group-header
        subtotal, the legend's asset band), so all four hero reads could point
        at the wrong-but-present field and the count still passed.  That is
        ruling R-CF's class, a control that cannot fail, committed one step
        after R-CF.  And its stated reason was false: Jinja renders a bare
        ``{{ value }}`` on a missing attribute as an empty string, but the
        ``money`` macro opens with ``{% if value < 0 %}`` and
        ``Undefined.__lt__`` RAISES -- so a renamed money field 500s and the
        ``status_code == 200`` assertion above already covered that class.

        What is left uncovered by the status check, and is what this test now
        pins, is a producer returning the WRONG FIGURE: a hero reduced over an
        empty set reports ``$0.00`` with a 200.  So each figure is matched
        inside the element that names it.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            resp = auth_client.get(
                "/savings/cockpit", headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            body = resp.data.decode()
            assert "Net worth" in body
            # Seed Checking $1,000.00, no liabilities: hero == assets ==
            # liquid == $1,000.00, and the liability chip is a real $0.00.
            # Each figure is anchored to the element that renders it, so an
            # occurrence elsewhere on the page cannot satisfy the arm.
            assert re.search(
                r'nw-hero__num[^>]*>\s*\$1,000\.00', body,
            ), "the net-worth hero did not render $1,000.00"
            for label, figure in (
                ("Total assets", "$1,000.00"),
                ("Total liabilities", "$0.00"),
                ("Liquid", "$1,000.00"),
            ):
                assert re.search(
                    rf'pulse-chip__label">{label}</div>\s*'
                    rf'<div class="pulse-chip__value[^>]*>\s*'
                    rf'{re.escape(figure)}',
                    body,
                ), f"the {label} chip did not render {figure}"
            assert f'id="acct-balance-{acct_id}"' in body
            assert 'id="cockpit-section"' not in body

    def test_cockpit_section_non_htmx_redirects_to_dashboard(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A non-HX request redirects to the page (the section is a fragment)."""
        with app.app_context():
            resp = auth_client.get("/savings/cockpit")
            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/savings")

    def test_a_loan_cell_renders_its_caption_and_a_cash_cell_does_not(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The loan caption RENDERS, and only for the loan (plan step X-t1).

        The projection became a value object with a ``loan`` field that always
        EXISTS (``None`` for a non-loan), so every template predicate on it had
        to move from ``is defined`` to ``is not none``.  Getting that wrong is
        SILENT: Jinja renders an attribute on ``None`` -- and a mistyped
        attribute name -- as empty rather than raising, so the page still
        returns 200 with the caption simply gone (finding N-111 measured two of
        three template typos degrading that way).

        So this asserts the rendered FIGURES, not the absence of an exception:
        the loan's monthly payment and its rate reach the page, and the cash
        account's cell carries no payment caption at all.

        **Both failure modes were planted and measured** (X-t1): reverting the
        caption predicate to ``is defined`` reaches ``ad.loan.figures`` on the
        cash account -- ``'None' has no attribute 'figures'``, a 500 -- and
        mistyping the payment path raises inside the ``money`` macro rather
        than blanking, because a filter or macro call FORCES the value where a
        bare ``{{ ... }}`` would swallow it.  The status assertion catches the
        first and the figure assertions catch the second.
        """
        with app.app_context():
            loan = _create_small_loan(seed_user)
            db.session.commit()
            resp = auth_client.get(
                "/savings/cockpit", headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            body = resp.data.decode()

            # The loan's own figures, read off the SAME producer the page
            # renders, so this cannot go stale against an amortization change.
            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            loan_ad = next(
                ad for ad in data["account_data"] if ad.account.id == loan.id
            )
            payment = loan_ad.loan.figures.terms.monthly_payment
            assert payment > Decimal("0.00"), (
                "the fixture loan has no monthly payment, so the caption "
                "assertion below would pass on an empty render"
            )
            assert "Monthly Payment" in body
            assert f"${payment:,.2f}" in body
            # The rate caption, formatted as the template formats it
            # (``"%.3f"|format(rate|to_percent)``): 5.000% for this loan.
            rate_pct = loan_ad.loan.figures.terms.current_rate * Decimal("100")
            assert f"{rate_pct:.3f}%" in body

            # The cash cell: no loan caption anywhere in ITS cell.  Sliced from
            # the account-name anchor to the end of that cell so a loan caption
            # elsewhere on the page cannot satisfy it.
            cash_id = seed_user["account"].id
            cash_cell = body.split(f'id="acct-balance-{cash_id}"')[1]
            cash_cell = cash_cell.split("</div>\n    </div>")[0]
            assert "Monthly Payment" not in cash_cell


class TestCockpitBalance:
    """Tests for GET /savings/cockpit/<id>/balance -- the inline-edit revert cell."""

    def test_cockpit_balance_renders_editable_cell_for_htmx(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An HX request returns the account's click-to-edit balance cell.

        The cell carries its account-scoped id and opens the shared anchor
        editor in the cockpit (``accounts``) surface, so Cancel / Escape and
        the save round-trip thread back to this cockpit cell.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            resp = auth_client.get(
                f"/savings/cockpit/{acct_id}/balance",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            body = resp.data.decode()
            assert f'id="acct-balance-{acct_id}"' in body
            assert (
                f'hx-get="/accounts/{acct_id}/anchor-form?revert=accounts"'
                in body
            )

    def test_cockpit_balance_non_htmx_redirects_to_dashboard(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A non-HX request redirects to the dashboard page."""
        with app.app_context():
            acct_id = seed_user["account"].id
            resp = auth_client.get(f"/savings/cockpit/{acct_id}/balance")
            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/savings")

    def test_cockpit_balance_other_users_account_404(
        self, app, auth_client, seed_user, seed_second_user, seed_periods_today,
    ):
        """IDOR: another user's account id returns 404 (not found / not yours)."""
        with app.app_context():
            other_acct_id = seed_second_user["account"].id
            resp = auth_client.get(
                f"/savings/cockpit/{other_acct_id}/balance",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

    def test_cockpit_balance_loan_cell_takes_liability_ink(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A loan (liability) balance cell renders the danger-ink class.

        P-AC4: the owed balance is colored like the liabilities chip, group
        subtotal, and diverging-bar segment -- keyed on the account's
        category, never the figure's sign -- so a reverted loan cell keeps
        its danger ink.  The asset-cell contrast is asserted below.
        """
        with app.app_context():
            loan = _create_small_loan(seed_user)
            resp = auth_client.get(
                f"/savings/cockpit/{loan.id}/balance",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert "acct-card__num--liability" in resp.data.decode()

    def test_cockpit_balance_asset_cell_omits_liability_ink(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A Checking (asset) balance cell omits the liability danger-ink class."""
        with app.app_context():
            acct_id = seed_user["account"].id
            resp = auth_client.get(
                f"/savings/cockpit/{acct_id}/balance",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert "acct-card__num--liability" not in resp.data.decode()


class TestCockpitBalanceKindGate:
    """The D4 / A1 gate: a loan's cockpit balance cell is read-only."""

    def test_cockpit_balance_loan_cell_is_read_only(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A loan's cell shows the balance but never offers the anchor editor.

        Finding B-15's UI door: the cockpit's click-to-edit cell opened
        the CASH anchor editor for a loan card, whose save wrote
        ``accounts.current_anchor_balance`` on the loan.  The cell still
        renders the (ledger-derived) balance with its liability ink; the
        editor affordance (hx-get to anchor-form, the edit modifier, the
        pencil icon) is absent -- a loan's true-up lives on the loan page.
        """
        with app.app_context():
            loan = _create_small_loan(seed_user)
            resp = auth_client.get(
                f"/savings/cockpit/{loan.id}/balance",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            body = resp.data.decode()
            assert "anchor-form" not in body
            assert "acct-card__num--edit" not in body
            assert "acct-card__edit-icon" not in body
            # The balance itself still renders, with the liability ink.
            assert "acct-card__num--liability" in body
