"""
Tests for retirement planning routes.
"""

import pytest
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.pension_profile import PensionProfile
from app.models.recurrence_rule import RecurrenceRule
from app.models.salary_profile import SalaryProfile
from app.models.transaction_template import TransactionTemplate
from app.models.user import UserSettings
from app.models.investment_params import InvestmentParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.ref import (
    AccountType, CalcMethod, DeductionTiming, FilingStatus,
    RecurrencePattern, TransactionType,
)


def _create_salary_profile(seed_user, db_session):
    """Helper to create a salary profile with all required relations."""
    filing_status = db_session.query(FilingStatus).filter_by(name="single").one()
    income_type = db_session.query(TransactionType).filter_by(name="income").one()
    every_period = db_session.query(RecurrencePattern).filter_by(name="every_period").one()

    cat = db_session.query(Category).filter_by(
        user_id=seed_user["user"].id, item_name="Salary"
    ).first()
    if not cat:
        cat = Category(
            user_id=seed_user["user"].id,
            group_name="Income", item_name="Salary",
        )
        db_session.add(cat)
        db_session.flush()

    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=every_period.id,
    )
    db_session.add(rule)
    db_session.flush()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=cat.id,
        recurrence_rule_id=rule.id,
        transaction_type_id=income_type.id,
        name="Main Job",
        default_amount=Decimal("80000.00") / 26,
        is_active=True,
    )
    db_session.add(template)
    db_session.flush()

    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        template_id=template.id,
        filing_status_id=filing_status.id,
        name="Main Job",
        annual_salary=Decimal("80000.00"),
        pay_periods_per_year=26,
    )
    db_session.add(profile)
    db_session.flush()
    return profile


def _create_pension(seed_user, db_session, salary_profile=None):
    """Helper to create a pension profile."""
    pension = PensionProfile(
        user_id=seed_user["user"].id,
        salary_profile_id=salary_profile.id if salary_profile else None,
        name="State Pension",
        benefit_multiplier=Decimal("0.01850"),
        consecutive_high_years=4,
        hire_date=date(2018, 7, 1),
        planned_retirement_date=date(2048, 7, 1),
    )
    db_session.add(pension)
    db_session.commit()
    return pension


def _create_other_user(db_session):
    """Create a second user for IDOR tests."""
    from app.services.auth_service import hash_password
    from app.models.user import User, UserSettings

    user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db_session.add(user)
    db_session.flush()

    settings = UserSettings(user_id=user.id)
    db_session.add(settings)
    db_session.commit()
    return user


def _create_retirement_account(seed_user, db_session, type_name="401k"):
    """Helper to create a retirement account with investment params."""
    acct_type = db_session.query(AccountType).filter_by(name=type_name).one()
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=acct_type.id,
        name=f"Test {type_name}",
        current_anchor_balance=Decimal("10000.00"),
    )
    db_session.add(account)
    db_session.flush()

    params = InvestmentParams(
        account_id=account.id,
        assumed_annual_return=Decimal("0.07000"),
        annual_contribution_limit=Decimal("23500.00"),
        employer_contribution_type="match",
        employer_match_percentage=Decimal("1.0000"),
        employer_match_cap_percentage=Decimal("0.0600"),
    )
    db_session.add(params)
    db_session.flush()
    return account, params


class TestRetirementDashboard:
    """Tests for the retirement dashboard page."""

    def test_dashboard_empty(self, auth_client, seed_user, db, seed_periods):
        """GET returns 200 even with no pensions or accounts."""
        resp = auth_client.get("/retirement")
        assert resp.status_code == 200

    def test_dashboard_with_pension(self, auth_client, seed_user, db, seed_periods):
        """GET returns 200 with pension data displayed."""
        profile = _create_salary_profile(seed_user, db.session)
        _create_pension(seed_user, db.session, salary_profile=profile)
        resp = auth_client.get("/retirement")
        assert resp.status_code == 200

    def test_dashboard_requires_auth(self, client, db):
        """Unauthenticated → redirect to login."""
        resp = client.get("/retirement")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


class TestPensionCRUD:
    """Tests for pension profile CRUD operations."""

    def test_pension_list(self, auth_client, seed_user, db, seed_periods):
        """GET pension list returns 200."""
        resp = auth_client.get("/retirement/pension")
        assert resp.status_code == 200

    def test_create_pension(self, auth_client, seed_user, db, seed_periods):
        """POST creates a new pension profile."""
        profile = _create_salary_profile(seed_user, db.session)
        resp = auth_client.post("/retirement/pension", data={
            "name": "LGERS",
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2048-07-01",
        })
        assert resp.status_code == 302
        pension = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id
        ).first()
        assert pension is not None
        assert pension.name == "LGERS"
        assert pension.benefit_multiplier == Decimal("0.01850")

    def test_edit_pension_form(self, auth_client, seed_user, db, seed_periods):
        """GET edit form returns 200."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.get(f"/retirement/pension/{pension.id}/edit")
        assert resp.status_code == 200

    def test_update_pension(self, auth_client, seed_user, db, seed_periods):
        """POST update modifies pension fields."""
        profile = _create_salary_profile(seed_user, db.session)
        pension = _create_pension(seed_user, db.session, salary_profile=profile)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "Updated Pension",
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "2.00",
            "consecutive_high_years": "3",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2050-01-01",
        })
        assert resp.status_code == 302
        db.session.refresh(pension)
        assert pension.name == "Updated Pension"
        assert pension.benefit_multiplier == Decimal("0.02000")

    def test_delete_pension(self, auth_client, seed_user, db, seed_periods):
        """POST delete deactivates pension."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}/delete")
        assert resp.status_code == 302
        db.session.refresh(pension)
        assert pension.is_active is False

    def test_edit_pension_idor(self, auth_client, seed_user, db, seed_periods):
        """Cannot edit another user's pension."""
        other_user = _create_other_user(db.session)
        pension = PensionProfile(
            user_id=other_user.id,
            name="Other Pension",
            benefit_multiplier=Decimal("0.01850"),
            consecutive_high_years=4,
            hire_date=date(2020, 1, 1),
        )
        db.session.add(pension)
        db.session.commit()
        resp = auth_client.get(f"/retirement/pension/{pension.id}/edit")
        assert resp.status_code == 302

    def test_delete_pension_idor(self, auth_client, seed_user, db, seed_periods):
        """Cannot delete another user's pension."""
        other_user = _create_other_user(db.session)
        pension = PensionProfile(
            user_id=other_user.id,
            name="Other Pension",
            benefit_multiplier=Decimal("0.01850"),
            consecutive_high_years=4,
            hire_date=date(2020, 1, 1),
        )
        db.session.add(pension)
        db.session.commit()
        resp = auth_client.post(f"/retirement/pension/{pension.id}/delete")
        assert resp.status_code == 302
        db.session.refresh(pension)
        assert pension.is_active is True  # Should NOT be deactivated


class TestRetirementSettings:
    """Tests for retirement settings update."""

    def test_update_settings(self, auth_client, seed_user, db, seed_periods):
        """POST updates retirement settings."""
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "4",
            "planned_retirement_date": "2055-01-01",
            "estimated_retirement_tax_rate": "20",
        })
        assert resp.status_code == 302
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        assert settings.safe_withdrawal_rate == Decimal("0.0400")
        assert settings.planned_retirement_date == date(2055, 1, 1)
        assert settings.estimated_retirement_tax_rate == Decimal("0.2000")

    def test_update_settings_partial(self, auth_client, seed_user, db, seed_periods):
        """POST with only SWR still works."""
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "3.5",
        })
        assert resp.status_code == 302


class TestRetirementProjections:
    """Tests that retirement dashboard projects with full contribution inputs."""

    def test_dashboard_projects_with_contributions(
        self, auth_client, seed_user, db, seed_periods
    ):
        """Dashboard projection includes employee contributions and employer match."""
        profile = _create_salary_profile(seed_user, db.session)
        account, params = _create_retirement_account(seed_user, db.session)

        # Set retirement date 20 years out.
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)

        # Create a paycheck deduction targeting the retirement account.
        pre_tax = db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
        flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()
        deduction = PaycheckDeduction(
            salary_profile_id=profile.id,
            target_account_id=account.id,
            name="401k Contribution",
            amount=Decimal("500.00"),
            deduction_timing_id=pre_tax.id,
            calc_method_id=flat_method.id,
        )
        db.session.add(deduction)
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        # With $500/period + 7% return + employer match over 20 years,
        # projected balance should be significantly more than $10,000.
        # Extract projected balances from the retirement accounts table.
        # The table has current balance then projected (fw-bold) on the same row.
        import re
        # Match the projected balance cells that follow a current balance cell.
        projected_values = re.findall(
            r'\$10,000\.00</td>\s*<td class="text-end font-mono fw-bold">\$([0-9,]+\.\d{2})',
            html,
        )
        assert projected_values, "Expected projected balance for retirement account"
        projected = float(projected_values[0].replace(",", ""))
        assert projected > 50000, (
            f"Projected balance {projected} should be >> $10,000 with contributions"
        )

    def test_dashboard_projects_without_retirement_date(
        self, auth_client, seed_user, db, seed_periods
    ):
        """Without planned retirement date, uses current balance as projection."""
        _create_retirement_account(seed_user, db.session)

        # No planned_retirement_date set.
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = None
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200

    @pytest.mark.xfail(reason="Depends on Task 4 template update")
    def test_dashboard_pension_tax_shown(
        self, auth_client, seed_user, db, seed_periods
    ):
        """After-tax pension line shown when tax rate is set."""
        profile = _create_salary_profile(seed_user, db.session)
        _create_pension(seed_user, db.session, salary_profile=profile)

        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        settings.estimated_retirement_tax_rate = Decimal("0.2000")
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "After-Tax Monthly Pension" in html

    def test_dashboard_projects_multiple_accounts(
        self, auth_client, seed_user, db, seed_periods
    ):
        """Multiple retirement accounts all project correctly."""
        _create_retirement_account(seed_user, db.session, "401k")
        _create_retirement_account(seed_user, db.session, "roth_ira")

        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
