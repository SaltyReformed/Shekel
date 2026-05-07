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
    income_type = db_session.query(TransactionType).filter_by(name="Income").one()
    every_period = db_session.query(RecurrencePattern).filter_by(name="Every Period").one()

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


def _create_pension(seed_user, db_session, salary_profile=None, name="State Pension"):
    """Helper to create a pension profile."""
    pension = PensionProfile(
        user_id=seed_user["user"].id,
        salary_profile_id=salary_profile.id if salary_profile else None,
        name=name,
        benefit_multiplier=Decimal("0.01850"),
        consecutive_high_years=4,
        hire_date=date(2018, 7, 1),
        planned_retirement_date=date(2048, 7, 1),
    )
    db_session.add(pension)
    db_session.commit()
    return pension




def _create_retirement_account(seed_user, db_session, type_name="401(k)"):
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

    def test_dashboard_empty(self, auth_client, seed_user, db, seed_periods_today):
        """GET returns 200 even with no pensions or accounts."""
        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        assert b"Retirement Planning" in resp.data
        assert b"Retirement Income Gap Analysis" in resp.data
        # No pension data seeded, so pension details should not appear.
        assert b"Pension Benefit Details" not in resp.data

    def test_dashboard_with_pension(self, auth_client, seed_user, db, seed_periods_today):
        """GET returns 200 with pension data displayed."""
        profile = _create_salary_profile(seed_user, db.session)
        _create_pension(seed_user, db.session, salary_profile=profile)
        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        assert b"Retirement Planning" in resp.data
        assert b"Pension Benefit Details" in resp.data

    def test_dashboard_no_stale_settings_migration_message(
        self, auth_client, seed_user, db, seed_periods_today
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

    def test_pension_list(self, auth_client, seed_user, db, seed_periods_today):
        """GET pension list returns 200 with pension form."""
        resp = auth_client.get("/retirement/pension")
        assert resp.status_code == 200
        assert b"Pension Profiles" in resp.data
        assert b'name="benefit_multiplier"' in resp.data

    def test_create_pension(self, auth_client, seed_user, db, seed_periods_today):
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

    def test_edit_pension_form(self, auth_client, seed_user, db, seed_periods_today):
        """GET edit form returns 200 with pre-populated pension data."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.get(f"/retirement/pension/{pension.id}/edit")
        assert resp.status_code == 200
        assert b"Edit Pension" in resp.data
        assert b"State Pension" in resp.data
        assert b'name="benefit_multiplier"' in resp.data
        assert b'name="hire_date"' in resp.data

    def test_update_pension(self, auth_client, seed_user, db, seed_periods_today):
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

    def test_delete_pension(self, auth_client, seed_user, db, seed_periods_today):
        """POST delete deactivates pension."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}/delete")
        assert resp.status_code == 302
        db.session.refresh(pension)
        assert pension.is_active is False

    def test_create_pension_double_submit(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """F-105 / C-22: same-name double-submit creates exactly one pension.

        The composite unique ``uq_pension_profiles_user_name`` rejects
        the second INSERT.  The route catches the IntegrityError and
        returns idempotent success: the user lands on the retirement
        dashboard with the pension they intended to create.
        """
        profile = _create_salary_profile(seed_user, db.session)
        data = {
            "name": "DuplicateName",
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2048-07-01",
        }
        r1 = auth_client.post("/retirement/pension", data=data)
        assert r1.status_code == 302

        r2 = auth_client.post("/retirement/pension", data=data)
        assert r2.status_code == 302

        db.session.expire_all()
        rows = (
            db.session.query(PensionProfile)
            .filter_by(user_id=seed_user["user"].id, name="DuplicateName")
            .all()
        )
        assert len(rows) == 1, (
            f"Expected 1 pension after double-submit, found {len(rows)}; "
            f"F-105 dedupe failed."
        )

    def test_create_pension_different_names_allowed(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """F-105 / C-22: distinct names create distinct pension rows."""
        profile = _create_salary_profile(seed_user, db.session)
        base = {
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2048-07-01",
        }
        r1 = auth_client.post(
            "/retirement/pension", data={**base, "name": "Plan A"},
        )
        r2 = auth_client.post(
            "/retirement/pension", data={**base, "name": "Plan B"},
        )
        assert r1.status_code == 302
        assert r2.status_code == 302

        db.session.expire_all()
        count = (
            db.session.query(PensionProfile)
            .filter_by(user_id=seed_user["user"].id)
            .count()
        )
        assert count == 2

    def test_update_pension_collision_returns_422(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """F-105 / C-22: renaming pension to an existing name returns 422.

        Constraint enforcement on the update path: the user cannot
        bypass the unique by editing an existing pension to match
        another's name.
        """
        profile = _create_salary_profile(seed_user, db.session)
        first = _create_pension(
            seed_user, db.session,
            salary_profile=profile, name="First Pension",
        )
        second = _create_pension(
            seed_user, db.session,
            salary_profile=profile, name="Second Pension",
        )

        resp = auth_client.post(f"/retirement/pension/{second.id}", data={
            "name": "First Pension",  # Collision target.
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2048-07-01",
        })
        assert resp.status_code == 422
        assert b"already have a pension profile with this name" in resp.data

        db.session.expire_all()
        db.session.refresh(second)
        assert second.name == "Second Pension", (
            "Failed update should not have mutated the row."
        )

    def test_edit_pension_idor(
        self, auth_client, second_user, db, seed_periods_today,
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
        self, auth_client, second_user, db, seed_periods_today,
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

    def test_update_settings(self, auth_client, seed_user, db, seed_periods_today):
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
        self, auth_client, seed_user, db, seed_periods_today,
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
        self, auth_client, seed_user, db, seed_periods_today
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
        # Match the projected balance cells (fw-bold) that follow the
        # current balance cell and the annual return rate cell.
        projected_values = re.findall(
            r'\$10,000\.00</td>\s*<td[^>]*>.*?</td>\s*<td class="text-end font-mono fw-bold">\$([0-9,]+\.\d{2})',
            html, re.DOTALL,
        )
        assert projected_values, "Expected projected balance for retirement account"
        projected = float(projected_values[0].replace(",", ""))
        assert projected > 50000, (
            f"Projected balance {projected} should be >> $10,000 with contributions"
        )

    def test_dashboard_projects_without_retirement_date(
        self, auth_client, seed_user, db, seed_periods_today
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
        self, auth_client, seed_user, db, seed_periods_today
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
        self, auth_client, seed_user, db, seed_periods_today
    ):
        """Multiple retirement accounts all project correctly."""
        _create_retirement_account(seed_user, db.session, "401(k)")
        _create_retirement_account(seed_user, db.session, "Roth IRA")

        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        assert b"Test 401(k)" in resp.data
        assert b"Test Roth IRA" in resp.data

    def test_dashboard_uses_projected_salary_for_gap(
        self, auth_client, seed_user, db, seed_periods_today
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

    def test_gap_redirects_without_htmx(self, auth_client, seed_user, db, seed_periods_today):
        """GET /retirement/gap without HX-Request redirects to retirement dashboard."""
        resp = auth_client.get("/retirement/gap")
        assert resp.status_code == 302
        assert "/retirement" in resp.headers.get("Location", "")

    def test_gap_returns_fragment(self, auth_client, seed_user, db, seed_periods_today):
        """GET /retirement/gap with HX-Request returns gap analysis fragment."""
        resp = auth_client.get(
            "/retirement/gap",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        # Gap analysis always renders the table with income gap row.
        assert b"Monthly Income Gap" in resp.data

    def test_gap_with_swr_param(self, auth_client, seed_user, db, seed_periods_today):
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

    def test_gap_with_return_rate_param(self, auth_client, seed_user, db, seed_periods_today):
        """Return rate slider parameter is accepted."""
        profile = _create_salary_profile(seed_user, db.session)
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2050, 1, 1)
        db.session.commit()

        _create_retirement_account(seed_user, db.session, type_name="401(k)")

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
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Pension POST without name is rejected by schema (required field)."""
        resp = auth_client.post("/retirement/pension", data={
            "benefit_multiplier": "2.0",
            "consecutive_high_years": "4",
            "hire_date": "2020-01-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_missing_hire_date(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Pension POST without hire_date is rejected (required field)."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "State Pension",
            "benefit_multiplier": "2.0",
            "consecutive_high_years": "4",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_negative_multiplier(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Pension POST with negative benefit_multiplier is rejected by Range(min=0, min_inclusive=False)."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Bad Pension",
            "benefit_multiplier": "-0.01",
            "consecutive_high_years": "4",
            "hire_date": "2020-01-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_retirement_before_hire_rejected(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Planned retirement date before hire date is rejected."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Bad Dates",
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2020-01-01",
            "planned_retirement_date": "2019-01-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_retirement_in_past_rejected(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Planned retirement date in the past is rejected."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Past Retirement",
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2010-01-01",
            "planned_retirement_date": "2020-01-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_earliest_before_hire_rejected(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Earliest retirement date before hire date is rejected."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Bad Earliest",
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2020-01-01",
            "earliest_retirement_date": "2019-06-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_planned_before_earliest_rejected(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Planned retirement date before earliest retirement date is rejected."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Backwards Dates",
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2010-01-01",
            "earliest_retirement_date": "2050-01-01",
            "planned_retirement_date": "2045-01-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_valid_dates_accepted(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Valid date ordering is accepted and pension is created."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Good Dates",
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2015-01-01",
            "earliest_retirement_date": "2045-01-01",
            "planned_retirement_date": "2050-01-01",
        })
        assert resp.status_code == 302

        pension = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id, name="Good Dates",
        ).first()
        assert pension is not None
        assert pension.hire_date == date(2015, 1, 1)
        assert pension.earliest_retirement_date == date(2045, 1, 1)
        assert pension.planned_retirement_date == date(2050, 1, 1)

    def test_update_pension_retirement_before_hire_rejected(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Updating planned_retirement_date to before existing hire_date is rejected."""
        pension = _create_pension(seed_user, db.session)
        # pension.hire_date is 2018-07-01
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": pension.name,
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        assert b"Must be after hire date" in resp.data
        assert b"is-invalid" in resp.data

        # Pension should be unchanged.
        db.session.expire_all()
        after = db.session.get(PensionProfile, pension.id)
        assert after.planned_retirement_date == date(2048, 7, 1)

    def test_edit_nonexistent_pension(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET edit for nonexistent pension redirects with flash."""
        resp = auth_client.get("/retirement/pension/999999/edit")
        assert resp.status_code == 302
        assert "/retirement" in resp.headers.get("Location", "")
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Pension profile not found." in resp2.data

    def test_update_nonexistent_pension(
        self, auth_client, seed_user, db, seed_periods_today,
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
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST delete for nonexistent pension redirects with flash."""
        resp = auth_client.post("/retirement/pension/999999/delete")
        assert resp.status_code == 302
        assert "/retirement" in resp.headers.get("Location", "")
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Pension profile not found." in resp2.data

    def test_edit_pension_idor_no_data_leaked(
        self, auth_client, second_user, db, seed_periods_today,
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
        self, auth_client, seed_user, db, seed_periods_today,
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
        assert resp.status_code == 422

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.safe_withdrawal_rate == orig_swr

    def test_update_settings_negative_swr(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Negative SWR as percentage: -5 converts to -0.05, rejected by Range(min=0)."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_swr = settings.safe_withdrawal_rate

        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "-5",
        })
        assert resp.status_code == 422

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.safe_withdrawal_rate == orig_swr

    def test_update_settings_zero_swr(
        self, auth_client, seed_user, db, seed_periods_today,
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
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Non-numeric tax rate is handled; original value preserved."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_tax = settings.estimated_retirement_tax_rate

        resp = auth_client.post("/retirement/settings", data={
            "estimated_retirement_tax_rate": "abc",
        })
        assert resp.status_code == 422

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.estimated_retirement_tax_rate == orig_tax

    def test_update_settings_tax_rate_over_100(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Tax rate 150% converts to 1.50, rejected by Range(max=1)."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_tax = settings.estimated_retirement_tax_rate

        resp = auth_client.post("/retirement/settings", data={
            "estimated_retirement_tax_rate": "150",
        })
        assert resp.status_code == 422

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.estimated_retirement_tax_rate == orig_tax

    def test_update_settings_invalid_date(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Non-date retirement date is rejected; original date preserved."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_date = settings.planned_retirement_date

        resp = auth_client.post("/retirement/settings", data={
            "planned_retirement_date": "not-a-date",
        })
        assert resp.status_code == 422

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


class TestRetirementValidationUX:
    """Tests for render-on-error validation UX with field highlights and data preservation."""

    def test_pension_validation_error_preserves_all_fields(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """All submitted field values are preserved in the re-rendered form on error."""
        profile = _create_salary_profile(seed_user, db.session)
        pension = _create_pension(seed_user, db.session, salary_profile=profile)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "My Updated Name",
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "2.500",
            "consecutive_high_years": "3",
            "hire_date": "2018-07-01",
            "earliest_retirement_date": "2040-01-01",
            "planned_retirement_date": "2017-01-01",  # Before hire -> error
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        # Every submitted value must appear in the re-rendered form.
        assert 'value="My Updated Name"' in html
        assert 'value="2.500"' in html
        assert 'value="3"' in html
        assert 'value="2018-07-01"' in html
        assert 'value="2040-01-01"' in html
        assert 'value="2017-01-01"' in html

    def test_pension_validation_error_highlights_invalid_field(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Invalid field gets is-invalid class and invalid-feedback message."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": pension.name,
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        assert "is-invalid" in html
        assert "invalid-feedback" in html
        assert "Must be after hire date" in html

    def test_pension_validation_error_does_not_highlight_valid_fields(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Only the invalid field gets is-invalid; valid fields do not."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": pension.name,
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        assert "Must be after hire date" in html
        # Only planned_retirement_date should have is-invalid.
        assert html.count("is-invalid") == 1

    def test_pension_validation_multiple_errors(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Multiple date fields with errors are all highlighted."""
        pension = _create_pension(seed_user, db.session)
        # Both earliest and planned are before hire date (2018-07-01).
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": pension.name,
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "earliest_retirement_date": "2017-06-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        # Both fields should have errors.
        assert html.count("is-invalid") >= 2
        assert html.count("invalid-feedback") >= 2

    def test_pension_valid_submission_still_works(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Valid pension update still redirects and persists changes (regression)."""
        profile = _create_salary_profile(seed_user, db.session)
        pension = _create_pension(seed_user, db.session, salary_profile=profile)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "Regression Test",
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "2.000",
            "consecutive_high_years": "3",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2050-01-01",
        })
        assert resp.status_code == 302
        db.session.refresh(pension)
        assert pension.name == "Regression Test"
        assert pension.benefit_multiplier == Decimal("0.02000")

    def test_pension_validation_returns_422_not_redirect(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Validation failure returns 422 HTML, not a 302 redirect."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": pension.name,
            "benefit_multiplier": "1.850",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        assert resp.content_type.startswith("text/html")
        assert "Location" not in resp.headers

    def test_pension_form_data_not_present_on_get(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET renders the form from the pension model with no error indicators."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.get(f"/retirement/pension/{pension.id}/edit")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "is-invalid" not in html
        assert "invalid-feedback" not in html
        assert "State Pension" in html

    def test_settings_validation_error_preserves_form_data(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Settings validation error re-renders with submitted values preserved."""
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "-5",
            "planned_retirement_date": "2055-01-01",
            "estimated_retirement_tax_rate": "20",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        # The submitted SWR should be in the form (original user input).
        assert 'value="-5"' in html
        assert "is-invalid" in html

    def test_settings_validation_error_highlights_field(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Settings error highlights the invalid field with error message."""
        resp = auth_client.post("/retirement/settings", data={
            "estimated_retirement_tax_rate": "150",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        assert "is-invalid" in html
        assert "invalid-feedback" in html

    def test_settings_valid_submission_still_works(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Valid settings POST still redirects and persists (regression)."""
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "3.5",
            "planned_retirement_date": "2055-01-01",
            "estimated_retirement_tax_rate": "22",
        })
        assert resp.status_code == 302
        db.session.expire_all()
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert settings.safe_withdrawal_rate == Decimal("0.0350")
        assert settings.planned_retirement_date == date(2055, 1, 1)

    def test_pension_error_then_success(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A failed submission does not poison subsequent successful submissions."""
        pension = _create_pension(seed_user, db.session)
        # First: invalid submission.
        resp1 = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "Attempt 1",
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp1.status_code == 422

        # Second: corrected submission.
        resp2 = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "Attempt 2",
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2050-01-01",
        })
        assert resp2.status_code == 302
        db.session.refresh(pension)
        assert pension.name == "Attempt 2"
        assert pension.planned_retirement_date == date(2050, 1, 1)

    def test_pension_select_fields_preserve_selection(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Select dropdown preserves the selected salary profile on error re-render."""
        import re

        profile = _create_salary_profile(seed_user, db.session)
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "Select Test",
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        # The salary profile option should be selected.
        pattern = rf'value="{profile.id}"\s+selected'
        assert re.search(pattern, html), (
            f"Salary profile {profile.id} option not selected in re-rendered form"
        )

    def test_pension_empty_date_handled(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Empty planned_retirement_date does not crash -- treated as omitted."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "No Date",
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "",
        })
        # Empty optional date is stripped by pre_load and omitted from update.
        assert resp.status_code == 302


class TestReturnRateClarity:
    """Tests for return rate slider tooltip and per-account rate display."""

    def test_return_slider_tooltip_present(self, auth_client, seed_user, db, seed_periods_today):
        """Dashboard shows info-circle tooltip on the Assumed Annual Return label."""
        profile = _create_salary_profile(seed_user, db.session)
        _create_pension(seed_user, db.session, salary_profile=profile)
        _create_retirement_account(seed_user, db.session)
        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Recalculates the gap analysis and account projections below" in html
        assert "overriding each account&#" in html or "overriding each account" in html

    def test_per_account_rate_displayed_on_dashboard(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Each retirement account row shows its configured annual return rate."""
        account, params = _create_retirement_account(seed_user, db.session)
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        # params.assumed_annual_return is 0.07 -> displayed as "7.0%" in its own column.
        assert "7.0%" in html
        assert "Annual Return" in html

    def test_per_account_rate_accuracy_multiple_accounts(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Multiple accounts with different rates each show their own rate."""
        acct1, params1 = _create_retirement_account(
            seed_user, db.session, type_name="401(k)",
        )
        # Create a second account with a different rate.
        acct_type = db.session.query(AccountType).filter_by(name="Roth IRA").one()
        acct2 = Account(
            user_id=seed_user["user"].id,
            account_type_id=acct_type.id,
            name="Test Roth IRA",
            current_anchor_balance=Decimal("5000.00"),
        )
        db.session.add(acct2)
        db.session.flush()
        params2 = InvestmentParams(
            account_id=acct2.id,
            assumed_annual_return=Decimal("0.09500"),
            annual_contribution_limit=Decimal("7000.00"),
        )
        db.session.add(params2)

        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        # 401(k) at 7.0%, Roth IRA at 9.5% -- each in its own column cell.
        assert ">7.0%<" in html
        assert ">9.5%<" in html

    def test_htmx_gap_response_includes_account_rows_oob(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """HTMX gap analysis response includes OOB swap for account table rows."""
        _create_retirement_account(seed_user, db.session)
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        db.session.commit()

        resp = auth_client.get(
            "/retirement/gap?return_rate=10.0",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # OOB swap div must be present.
        assert 'id="retirement-accounts-content"' in html
        assert 'hx-swap-oob="innerHTML"' in html

    def test_htmx_gap_oob_uses_slider_rate(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """When slider overrides return rate, OOB account rows show the override rate."""
        _create_retirement_account(seed_user, db.session)
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        db.session.commit()

        resp = auth_client.get(
            "/retirement/gap?return_rate=10.0",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # The OOB rows should show 10.0% (slider override), not 7.0% (account default).
        assert ">10.0%<" in html
        assert ">7.0%<" not in html

    def test_initial_dashboard_no_oob_in_gap_section(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Full page load does not render OOB swap inside the gap analysis card."""
        _create_retirement_account(seed_user, db.session)
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The OOB attribute should NOT appear on the full page render.
        assert 'hx-swap-oob="innerHTML"' not in html
        # But the wrapper div with the id should exist (for the actual table).
        assert 'id="retirement-accounts-content"' in html

    def test_slider_default_value_present(self, auth_client, seed_user, db, seed_periods_today):
        """Slider element is present with its default value attribute."""
        _create_retirement_account(seed_user, db.session)
        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'id="return_slider"' in html
        assert 'id="swr_slider"' in html

    def test_htmx_gap_still_returns_gap_analysis(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """HTMX gap endpoint with return_rate still returns the gap analysis table (regression)."""
        profile = _create_salary_profile(seed_user, db.session)
        _create_retirement_account(seed_user, db.session)
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        db.session.commit()

        resp = auth_client.get(
            "/retirement/gap?return_rate=8.0&swr=3.5",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Core gap analysis content must still be present.
        assert "Monthly Income Gap" in html
        assert "Projected Retirement Savings" in html
        assert "3.5% rule" in html


# ── is_pretax Metadata Dispatch ──────────────────────────────────


class TestIsPretaxDispatch:
    """Verify that the is_pretax metadata flag on AccountType drives the
    pre-tax / post-tax distinction in retirement gap analysis, replacing
    the hardcoded TRADITIONAL_TYPE_ENUMS frozenset."""

    def test_gap_analysis_user_created_pretax_type(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """A user-created Retirement type with is_pretax=True is flagged
        as traditional (pre-tax) in the gap analysis projections."""
        from app import ref_cache
        from app.enums import AcctCategoryEnum
        from app.services.retirement_dashboard_service import compute_gap_data

        with app.app_context():
            _create_salary_profile(seed_user, db.session)
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            settings.planned_retirement_date = date(2060, 1, 1)
            db.session.commit()

            custom_type = AccountType(
                name="Test403b",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT),
                has_parameters=True,
                is_pretax=True,
                icon_class="bi-graph-up-arrow",
            )
            db.session.add(custom_type)
            db.session.flush()

            acct = Account(
                user_id=seed_user["user"].id,
                name="My 403(b)",
                account_type_id=custom_type.id,
                current_anchor_balance=Decimal("50000"),
                current_anchor_period_id=seed_periods_today[0].id,
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InvestmentParams(account_id=acct.id))
            db.session.commit()

            data = compute_gap_data(seed_user["user"].id)
            projections = data["retirement_account_projections"]
            proj = next(p for p in projections if p["account"].id == acct.id)
            assert proj["is_traditional"] is True

    def test_gap_analysis_posttax_type(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """A Retirement type with is_pretax=False is NOT flagged as traditional."""
        from app import ref_cache
        from app.enums import AcctCategoryEnum
        from app.services.retirement_dashboard_service import compute_gap_data

        with app.app_context():
            _create_salary_profile(seed_user, db.session)
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            settings.planned_retirement_date = date(2060, 1, 1)
            db.session.commit()

            custom_type = AccountType(
                name="TestRothSolo",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT),
                has_parameters=True,
                is_pretax=False,
                icon_class="bi-graph-up-arrow",
            )
            db.session.add(custom_type)
            db.session.flush()

            acct = Account(
                user_id=seed_user["user"].id,
                name="My Roth Solo 401(k)",
                account_type_id=custom_type.id,
                current_anchor_balance=Decimal("25000"),
                current_anchor_period_id=seed_periods_today[0].id,
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InvestmentParams(account_id=acct.id))
            db.session.commit()

            data = compute_gap_data(seed_user["user"].id)
            projections = data["retirement_account_projections"]
            proj = next(p for p in projections if p["account"].id == acct.id)
            assert proj["is_traditional"] is False

    def test_gap_analysis_no_retirement_accounts(
        self, app, auth_client, seed_user, db,
    ):
        """Gap analysis returns empty projections when no retirement accounts exist."""
        from app.services.retirement_dashboard_service import compute_gap_data

        with app.app_context():
            data = compute_gap_data(seed_user["user"].id)
            assert data["retirement_account_projections"] == []
