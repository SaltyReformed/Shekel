"""
Tests for retirement planning routes.
"""

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
        assert b"Retirement Planning" in resp.data
        assert b"Retirement Income Gap Analysis" in resp.data
        # No pension data seeded, so pension details should not appear.
        assert b"Pension Benefit Details" not in resp.data

    def test_dashboard_with_pension(self, auth_client, seed_user, db, seed_periods):
        """GET returns 200 with pension data displayed."""
        profile = _create_salary_profile(seed_user, db.session)
        _create_pension(seed_user, db.session, salary_profile=profile)
        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        assert b"Retirement Planning" in resp.data
        assert b"Pension Benefit Details" in resp.data

    def test_dashboard_no_stale_settings_migration_message(
        self, auth_client, seed_user, db, seed_periods
    ):
        """Dashboard must not contain the old 'settings have moved' notice."""
        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        assert b"have moved to" not in resp.data

    def test_dashboard_requires_auth(self, client, db):
        """Unauthenticated → redirect to login."""
        resp = client.get("/retirement")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


class TestPensionCRUD:
    """Tests for pension profile CRUD operations."""

    def test_pension_list(self, auth_client, seed_user, db, seed_periods):
        """GET pension list returns 200 with pension form."""
        resp = auth_client.get("/retirement/pension")
        assert resp.status_code == 200
        assert b"Pension Profiles" in resp.data
        assert b'name="benefit_multiplier"' in resp.data

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
        """GET edit form returns 200 with pre-populated pension data."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.get(f"/retirement/pension/{pension.id}/edit")
        assert resp.status_code == 200
        assert b"Edit Pension" in resp.data
        assert b"State Pension" in resp.data
        assert b'name="benefit_multiplier"' in resp.data
        assert b'name="hire_date"' in resp.data

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

    def test_edit_pension_idor(
        self, auth_client, second_user, db, seed_periods,
    ):
        """GET another user's pension edit form is rejected
        and does not leak victim data."""
        pension = PensionProfile(
            user_id=second_user["user"].id,
            name="Other Pension",
            benefit_multiplier=Decimal("0.01850"),
            consecutive_high_years=4,
            hire_date=date(2020, 1, 1),
        )
        db.session.add(pension)
        db.session.commit()

        resp = auth_client.get(f"/retirement/pension/{pension.id}/edit")
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "/retirement" in location, (
            f"IDOR redirect went to {location}, expected /retirement"
        )
        assert b"Other Pension" not in resp.data, (
            "IDOR response leaked victim's pension name"
        )

    def test_delete_pension_idor(
        self, auth_client, second_user, db, seed_periods,
    ):
        """POST delete on another user's pension is rejected
        and leaves all fields unchanged."""
        pension = PensionProfile(
            user_id=second_user["user"].id,
            name="Other Pension",
            benefit_multiplier=Decimal("0.01850"),
            consecutive_high_years=4,
            hire_date=date(2020, 1, 1),
        )
        db.session.add(pension)
        db.session.commit()

        # Snapshot mutable fields.
        orig_name = pension.name
        orig_multiplier = pension.benefit_multiplier
        orig_active = pension.is_active

        resp = auth_client.post(f"/retirement/pension/{pension.id}/delete")
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "/retirement" in location, (
            f"IDOR redirect went to {location}, expected /retirement"
        )

        db.session.expire_all()
        after = db.session.get(PensionProfile, pension.id)
        assert after.is_active == orig_active, (
            "IDOR attack deactivated victim's pension!"
        )
        assert after.name == orig_name, (
            "IDOR attack modified victim's pension name!"
        )
        assert after.benefit_multiplier == orig_multiplier, (
            "IDOR attack modified victim's benefit_multiplier!"
        )


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

    def test_update_settings_partial(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST with only SWR persists the value and leaves
        other retirement fields unchanged."""
        # Snapshot pre-request values.
        settings_before = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).one()
        orig_retirement_date = settings_before.planned_retirement_date
        orig_tax_rate = settings_before.estimated_retirement_tax_rate

        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "3.5",
        })
        assert resp.status_code == 302

        # Verify SWR was persisted.
        db.session.expire_all()
        settings_after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).one()
        assert settings_after.safe_withdrawal_rate == Decimal("0.0350"), (
            "SWR was not persisted correctly"
        )
        assert settings_after.planned_retirement_date == orig_retirement_date, (
            "Partial update modified planned_retirement_date!"
        )
        assert settings_after.estimated_retirement_tax_rate == orig_tax_rate, (
            "Partial update modified estimated_retirement_tax_rate!"
        )


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
        """Without planned retirement date, dashboard still renders correctly.

        The page should render the Retirement Planning heading and content
        even when no retirement date is configured.
        """
        _create_retirement_account(seed_user, db.session)

        # No planned_retirement_date set.
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = None
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        assert b"Retirement Planning" in resp.data

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
        assert b"Test 401k" in resp.data
        assert b"Test roth_ira" in resp.data

    def test_dashboard_uses_projected_salary_for_gap(
        self, auth_client, seed_user, db, seed_periods
    ):
        """Gap analysis uses projected pre-retirement income, not current."""
        from app.models.salary_raise import SalaryRaise
        from app.models.ref import RaiseType

        profile = _create_salary_profile(seed_user, db.session)

        # Add a recurring 3% annual raise.
        merit = db.session.query(RaiseType).filter_by(name="merit").one()
        raise_obj = SalaryRaise(
            salary_profile_id=profile.id,
            raise_type_id=merit.id,
            percentage=Decimal("0.0300"),
            effective_month=1,
            effective_year=date.today().year + 1,
            is_recurring=True,
        )
        db.session.add(raise_obj)

        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Should show projected pre-retirement income, not current.
        assert "Projected Pre-Retirement Income" in html


class TestGapAnalysisFragment:
    """Tests for the retirement gap analysis HTMX fragment (U3)."""

    def test_gap_redirects_without_htmx(self, auth_client, seed_user, db, seed_periods):
        """GET /retirement/gap without HX-Request redirects to retirement dashboard."""
        resp = auth_client.get("/retirement/gap")
        assert resp.status_code == 302
        assert "/retirement" in resp.headers.get("Location", "")

    def test_gap_returns_fragment(self, auth_client, seed_user, db, seed_periods):
        """GET /retirement/gap with HX-Request returns gap analysis fragment."""
        resp = auth_client.get(
            "/retirement/gap",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        # Gap analysis always renders the table with income gap row.
        assert b"Monthly Income Gap" in resp.data

    def test_gap_with_swr_param(self, auth_client, seed_user, db, seed_periods):
        """SWR slider parameter is accepted and used."""
        profile = _create_salary_profile(seed_user, db.session)
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2050, 1, 1)
        settings.safe_withdrawal_rate = Decimal("0.04")
        db.session.commit()

        resp = auth_client.get(
            "/retirement/gap?swr=3.0",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        # The fragment should show the 3.0% rate in the "Required Savings" line.
        assert b"3.0% rule" in resp.data

    def test_gap_with_return_rate_param(self, auth_client, seed_user, db, seed_periods):
        """Return rate slider parameter is accepted."""
        profile = _create_salary_profile(seed_user, db.session)
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2050, 1, 1)
        db.session.commit()

        _create_retirement_account(seed_user, db.session, type_name="401k")

        resp = auth_client.get(
            "/retirement/gap?return_rate=10.0",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        # Gap analysis table always contains income gap row.
        assert b"Monthly Income Gap" in resp.data


class TestRetirementNegativePaths:
    """Negative-path and boundary tests for retirement routes."""

    def test_create_pension_missing_name(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Pension POST without name is rejected by schema (required field)."""
        resp = auth_client.post("/retirement/pension", data={
            "benefit_multiplier": "2.0",
            "consecutive_high_years": "4",
            "hire_date": "2020-01-01",
        })
        assert resp.status_code == 302
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Please correct the highlighted errors" in resp2.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_missing_hire_date(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Pension POST without hire_date is rejected (required field)."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "State Pension",
            "benefit_multiplier": "2.0",
            "consecutive_high_years": "4",
        })
        assert resp.status_code == 302
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Please correct the highlighted errors" in resp2.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_negative_multiplier(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Pension POST with negative benefit_multiplier is rejected by Range(min=0, min_inclusive=False)."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Bad Pension",
            "benefit_multiplier": "-0.01",
            "consecutive_high_years": "4",
            "hire_date": "2020-01-01",
        })
        assert resp.status_code == 302
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Please correct the highlighted errors" in resp2.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_edit_nonexistent_pension(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """GET edit for nonexistent pension redirects with flash."""
        resp = auth_client.get("/retirement/pension/999999/edit")
        assert resp.status_code == 302
        assert "/retirement" in resp.headers.get("Location", "")
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Pension profile not found." in resp2.data

    def test_update_nonexistent_pension(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST update for nonexistent pension redirects with flash."""
        resp = auth_client.post("/retirement/pension/999999", data={
            "name": "Ghost",
            "benefit_multiplier": "2.0",
            "hire_date": "2020-01-01",
        })
        assert resp.status_code == 302
        assert "/retirement" in resp.headers.get("Location", "")
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Pension profile not found." in resp2.data

    def test_delete_nonexistent_pension(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST delete for nonexistent pension redirects with flash."""
        resp = auth_client.post("/retirement/pension/999999/delete")
        assert resp.status_code == 302
        assert "/retirement" in resp.headers.get("Location", "")
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Pension profile not found." in resp2.data

    def test_edit_pension_idor_no_data_leaked(
        self, auth_client, second_user, db, seed_periods,
    ):
        """IDOR GET to edit pension does not leak victim's pension data."""
        pension = PensionProfile(
            user_id=second_user["user"].id,
            name="Secret Pension",
            benefit_multiplier=Decimal("0.02500"),
            consecutive_high_years=4,
            hire_date=date(2020, 1, 1),
        )
        db.session.add(pension)
        db.session.commit()

        resp = auth_client.get(f"/retirement/pension/{pension.id}/edit")
        assert resp.status_code == 302
        assert b"Secret Pension" not in resp.data
        assert b"0.02500" not in resp.data

    def test_update_settings_invalid_swr(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Non-numeric SWR is handled gracefully; original value preserved."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_swr = settings.safe_withdrawal_rate

        # The route's try/except passes on conversion failure, leaving "abc"
        # as-is. The schema then rejects it as not a valid Decimal.
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "abc",
        })
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.safe_withdrawal_rate == orig_swr

    def test_update_settings_negative_swr(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Negative SWR as percentage: -5 converts to -0.05, rejected by Range(min=0)."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_swr = settings.safe_withdrawal_rate

        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "-5",
        })
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.safe_withdrawal_rate == orig_swr

    def test_update_settings_zero_swr(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """SWR of 0% converts to 0.00, which is valid per Range(min=0)."""
        # 0 / 100 = 0.00, which passes Range(min=0, max=1).
        # The gap calculator would need to handle division by zero separately.
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "0",
        })
        assert resp.status_code == 302

        db.session.expire_all()
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        # 0 / 100 = 0.0000
        assert settings.safe_withdrawal_rate == Decimal("0.0000")

    def test_update_settings_invalid_tax_rate(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Non-numeric tax rate is handled; original value preserved."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_tax = settings.estimated_retirement_tax_rate

        resp = auth_client.post("/retirement/settings", data={
            "estimated_retirement_tax_rate": "abc",
        })
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.estimated_retirement_tax_rate == orig_tax

    def test_update_settings_tax_rate_over_100(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Tax rate 150% converts to 1.50, rejected by Range(max=1)."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_tax = settings.estimated_retirement_tax_rate

        resp = auth_client.post("/retirement/settings", data={
            "estimated_retirement_tax_rate": "150",
        })
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.estimated_retirement_tax_rate == orig_tax

    def test_update_settings_invalid_date(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Non-date retirement date is rejected; original date preserved."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_date = settings.planned_retirement_date

        resp = auth_client.post("/retirement/settings", data={
            "planned_retirement_date": "not-a-date",
        })
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.planned_retirement_date == orig_date

    def test_create_pension_login_required(self, client, db):
        """Unauthenticated POST to create pension redirects to login."""
        resp = client.post("/retirement/pension", data={
            "name": "Sneaky",
            "benefit_multiplier": "2.0",
            "hire_date": "2020-01-01",
        })
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_update_settings_login_required(self, client, db):
        """Unauthenticated POST to update settings redirects to login."""
        resp = client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "4",
        })
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_gap_analysis_login_required(self, client, db):
        """Unauthenticated GET to gap analysis redirects to login."""
        resp = client.get("/retirement/gap")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
