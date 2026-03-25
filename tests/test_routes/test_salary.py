"""
Shekel Budget App -- Salary Route Tests

Tests for salary profile CRUD, raises, deductions, breakdown,
projection, and tax config endpoints (§2.2 of the test plan).
"""

from decimal import Decimal

from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.tax_config import FicaConfig, StateTaxConfig, TaxBracketSet
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app.models.user import User, UserSettings
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.category import Category
from app.models.ref import (
    AccountType, CalcMethod, DeductionTiming, FilingStatus,
    RaiseType, RecurrencePattern, TaxType, TransactionType,
)
from app.services.auth_service import hash_password


def _create_profile(seed_user):
    """Helper: create a salary profile with linked template and recurrence."""
    filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
    income_type = db.session.query(TransactionType).filter_by(name="income").one()
    every_period = db.session.query(RecurrencePattern).filter_by(name="every_period").one()

    # Find or create Salary category.
    cat = (
        db.session.query(Category)
        .filter_by(user_id=seed_user["user"].id, group_name="Income", item_name="Salary")
        .first()
    )
    if not cat:
        cat = Category(user_id=seed_user["user"].id, group_name="Income", item_name="Salary")
        db.session.add(cat)
        db.session.flush()

    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=every_period.id,
    )
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=cat.id,
        recurrence_rule_id=rule.id,
        transaction_type_id=income_type.id,
        name="Day Job",
        default_amount=Decimal("75000.00") / 26,
        is_active=True,
    )
    db.session.add(template)
    db.session.flush()

    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        template_id=template.id,
        filing_status_id=filing_status.id,
        name="Day Job",
        annual_salary=Decimal("75000.00"),
        state_code="NC",
        pay_periods_per_year=26,
    )
    db.session.add(profile)
    db.session.commit()
    return profile


def _create_other_user_profile():
    """Create a second user with a salary profile.

    Returns:
        dict with keys: user, profile.
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

    checking_type = db.session.query(AccountType).filter_by(name="checking").one()
    account = Account(
        user_id=other_user.id,
        account_type_id=checking_type.id,
        name="Other Checking",
        current_anchor_balance=Decimal("500.00"),
    )
    db.session.add(account)

    scenario = Scenario(
        user_id=other_user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
    income_type = db.session.query(TransactionType).filter_by(name="income").one()
    every_period = db.session.query(RecurrencePattern).filter_by(name="every_period").one()

    cat = Category(user_id=other_user.id, group_name="Income", item_name="Salary")
    db.session.add(cat)
    db.session.flush()

    rule = RecurrenceRule(user_id=other_user.id, pattern_id=every_period.id)
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=other_user.id,
        account_id=account.id,
        category_id=cat.id,
        recurrence_rule_id=rule.id,
        transaction_type_id=income_type.id,
        name="Other Job",
        default_amount=Decimal("60000.00") / 26,
        is_active=True,
    )
    db.session.add(template)
    db.session.flush()

    profile = SalaryProfile(
        user_id=other_user.id,
        scenario_id=scenario.id,
        template_id=template.id,
        filing_status_id=filing_status.id,
        name="Other Job",
        annual_salary=Decimal("60000.00"),
        state_code="NC",
        pay_periods_per_year=26,
    )
    db.session.add(profile)
    db.session.commit()

    return {"user": other_user, "profile": profile}


# ── Profile CRUD ───────────────────────────────────────────────────


class TestProfileList:
    """Tests for GET /salary and GET /salary/new."""

    def test_list_profiles(self, app, auth_client, seed_user, seed_periods):
        """GET /salary renders the salary profiles list."""
        with app.app_context():
            _create_profile(seed_user)

            response = auth_client.get("/salary")

            assert response.status_code == 200
            assert b"Day Job" in response.data

    def test_new_profile_form(self, app, auth_client, seed_user):
        """GET /salary/new renders the salary profile creation form."""
        with app.app_context():
            response = auth_client.get("/salary/new")

            assert response.status_code == 200
            assert b'name="annual_salary"' in response.data
            assert b'name="filing_status_id"' in response.data
            assert b"New Salary Profile" in response.data


class TestProfileCreate:
    """Tests for POST /salary."""

    def test_create_profile(self, app, auth_client, seed_user, seed_periods):
        """POST /salary creates a profile with linked template and recurrence."""
        with app.app_context():
            filing_status = db.session.query(FilingStatus).filter_by(name="single").one()

            response = auth_client.post("/salary", data={
                "name": "Day Job",
                "annual_salary": "75000.00",
                "filing_status_id": filing_status.id,
                "state_code": "NC",
                "pay_periods_per_year": "26",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"created" in response.data

            # Verify profile and template were created.
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=seed_user["user"].id, name="Day Job")
                .one()
            )
            assert profile.annual_salary == Decimal("75000.00")
            assert profile.template is not None
            assert profile.template.is_active is True

    def test_create_profile_template_amount(self, app, auth_client, seed_user, seed_periods):
        """Created template amount equals annual_salary / pay_periods_per_year."""
        with app.app_context():
            filing_status = db.session.query(FilingStatus).filter_by(name="single").one()

            auth_client.post("/salary", data={
                "name": "Salary Check",
                "annual_salary": "52000.00",
                "filing_status_id": filing_status.id,
                "state_code": "NC",
                "pay_periods_per_year": "26",
            }, follow_redirects=True)

            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=seed_user["user"].id, name="Salary Check")
                .one()
            )
            # 52000 / 26 = 2000
            assert profile.template.default_amount == Decimal("2000.00")

    def test_create_profile_validation_error(self, app, auth_client, seed_user):
        """POST /salary with missing fields shows a validation error."""
        with app.app_context():
            response = auth_client.post("/salary", data={
                "name": "",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_create_profile_no_baseline_scenario(self, app, auth_client, seed_user):
        """POST /salary with no baseline scenario flashes danger."""
        with app.app_context():
            # Remove the baseline scenario.
            scenario = db.session.get(Scenario, seed_user["scenario"].id)
            scenario.is_baseline = False
            db.session.commit()

            filing_status = db.session.query(FilingStatus).filter_by(name="single").one()

            response = auth_client.post("/salary", data={
                "name": "Day Job",
                "annual_salary": "75000.00",
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"No baseline scenario found" in response.data
            assert b"register" in response.data

    def test_create_profile_no_active_account(self, app, auth_client, seed_user):
        """POST /salary with no active account flashes danger."""
        with app.app_context():
            # Deactivate the account.
            account = db.session.get(Account, seed_user["account"].id)
            account.is_active = False
            db.session.commit()

            filing_status = db.session.query(FilingStatus).filter_by(name="single").one()

            response = auth_client.post("/salary", data={
                "name": "Day Job",
                "annual_salary": "75000.00",
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"active account" in response.data
            assert b"/accounts/new" in response.data

    def test_create_profile_double_submit(self, app, auth_client, seed_user, seed_periods):
        """POST /salary twice results in an error on the second attempt."""
        with app.app_context():
            filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
            data = {
                "name": "Day Job",
                "annual_salary": "75000.00",
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }

            # First submit succeeds.
            response1 = auth_client.post("/salary", data=data, follow_redirects=True)
            assert b"created" in response1.data

            # Second submit with same name triggers unique constraint error.
            response2 = auth_client.post("/salary", data=data, follow_redirects=True)
            assert b"Failed to create salary profile" in response2.data

    def test_create_profile_no_account_flash_has_link(
        self, app, auth_client, seed_user
    ):
        """POST /salary with no active account flashes a message with a create-account link."""
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)
            account.is_active = False
            db.session.commit()

            filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
            response = auth_client.post("/salary", data={
                "name": "Link Test",
                "annual_salary": "75000.00",
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"/accounts/new" in response.data


class TestProfileUpdate:
    """Tests for GET/POST /salary/<id>/edit and POST /salary/<id>/delete."""

    def test_edit_profile_form(self, app, auth_client, seed_user, seed_periods):
        """GET /salary/<id>/edit renders the edit form."""
        with app.app_context():
            profile = _create_profile(seed_user)

            response = auth_client.get(f"/salary/{profile.id}/edit")

            assert response.status_code == 200
            assert b"Day Job" in response.data

    def test_update_profile(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id> updates the profile and redirects."""
        with app.app_context():
            profile = _create_profile(seed_user)
            filing_status = db.session.query(FilingStatus).filter_by(name="single").one()

            response = auth_client.post(f"/salary/{profile.id}", data={
                "name": "Updated Job",
                "annual_salary": "80000.00",
                "filing_status_id": filing_status.id,
                "state_code": "NC",
                "pay_periods_per_year": "26",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"updated" in response.data

            db.session.refresh(profile)
            assert profile.annual_salary == Decimal("80000.00")

    def test_delete_profile(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id>/delete deactivates the profile and its template."""
        with app.app_context():
            profile = _create_profile(seed_user)
            template_id = profile.template_id

            response = auth_client.post(
                f"/salary/{profile.id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"deactivated" in response.data

            db.session.refresh(profile)
            assert profile.is_active is False

            template = db.session.get(TransactionTemplate, template_id)
            assert template.is_active is False

    def test_edit_other_users_profile_redirects(
        self, app, auth_client, seed_user
    ):
        """GET /salary/<id>/edit for another user's profile redirects."""
        with app.app_context():
            other = _create_other_user_profile()

            response = auth_client.get(
                f"/salary/{other['profile'].id}/edit",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Salary profile not found." in response.data

    def test_update_other_users_profile_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /salary/<id> for another user's profile redirects."""
        with app.app_context():
            other = _create_other_user_profile()

            response = auth_client.post(
                f"/salary/{other['profile'].id}",
                data={"name": "Hacked"},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Salary profile not found." in response.data

    def test_delete_other_users_profile_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /salary/<id>/delete for another user's profile redirects."""
        with app.app_context():
            other = _create_other_user_profile()

            response = auth_client.post(
                f"/salary/{other['profile'].id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Salary profile not found." in response.data


# ── Raises ─────────────────────────────────────────────────────────


class TestRaises:
    """Tests for raise add/delete endpoints."""

    def test_add_raise(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id>/raises adds a raise and redirects."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            response = auth_client.post(
                f"/salary/{profile.id}/raises",
                data={
                    "raise_type_id": raise_type.id,
                    "effective_month": "7",
                    "effective_year": "2026",
                    "percentage": "3",
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Raise added." in response.data

    def test_add_raise_htmx_returns_partial(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id>/raises with HX-Request returns a partial."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="cola").one()

            response = auth_client.post(
                f"/salary/{profile.id}/raises",
                data={
                    "raise_type_id": raise_type.id,
                    "effective_month": "1",
                    "effective_year": "2027",
                    "flat_amount": "2000.00",
                },
                headers={"HX-Request": "true"},
            )

            assert response.status_code == 200
            assert b"Cola" in response.data
            assert b"2000.00" in response.data

    def test_add_raise_htmx_response_contains_raises_section_id(
        self, app, auth_client, seed_user, seed_periods
    ):
        """HTMX partial must include id='raises-section' so subsequent swaps work.

        Without this id on the partial's root element, the first HTMX swap
        replaces the target and subsequent operations cannot find #raises-section.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            response = auth_client.post(
                f"/salary/{profile.id}/raises",
                data={
                    "raise_type_id": raise_type.id,
                    "effective_month": "6",
                    "effective_year": "2027",
                    "percentage": "3",
                },
                headers={"HX-Request": "true"},
            )

            assert response.status_code == 200
            assert b'id="raises-section"' in response.data

    def test_delete_raise_htmx_response_contains_raises_section_id(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Delete-raise HTMX partial must also preserve the raises-section id."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            salary_raise = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=raise_type.id,
                effective_month=3,
                effective_year=2026,
                percentage=Decimal("0.0200"),
            )
            db.session.add(salary_raise)
            db.session.commit()

            response = auth_client.post(
                f"/salary/raises/{salary_raise.id}/delete",
                headers={"HX-Request": "true"},
            )

            assert response.status_code == 200
            assert b'id="raises-section"' in response.data

    def test_delete_raise(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/raises/<id>/delete removes a raise."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            salary_raise = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=raise_type.id,
                effective_month=7,
                effective_year=2026,
                percentage=Decimal("0.0300"),
            )
            db.session.add(salary_raise)
            db.session.commit()

            response = auth_client.post(
                f"/salary/raises/{salary_raise.id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Raise removed." in response.data

    def test_add_raise_validation_error(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id>/raises with missing fields shows a validation error."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            # Missing both percentage and flat_amount.
            response = auth_client.post(
                f"/salary/{profile.id}/raises",
                data={
                    "raise_type_id": raise_type.id,
                    "effective_month": "7",
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_add_raise_without_year_rejected(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id>/raises without effective_year is rejected."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            response = auth_client.post(
                f"/salary/{profile.id}/raises",
                data={
                    "raise_type_id": raise_type.id,
                    "effective_month": "7",
                    "percentage": "3",
                    "is_recurring": "on",
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_add_raise_profile_not_found(self, app, auth_client, seed_user):
        """POST /salary/<id>/raises for non-existent profile flashes danger."""
        with app.app_context():
            response = auth_client.post(
                "/salary/999999/raises",
                data={"raise_type_id": "1", "effective_month": "1"},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Salary profile not found." in response.data

    def test_delete_other_users_raise_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /salary/raises/<id>/delete for another user's raise shows 'Not authorized'."""
        with app.app_context():
            other = _create_other_user_profile()
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            salary_raise = SalaryRaise(
                salary_profile_id=other["profile"].id,
                raise_type_id=raise_type.id,
                effective_month=7,
                effective_year=2026,
                percentage=Decimal("0.0300"),
            )
            db.session.add(salary_raise)
            db.session.commit()

            response = auth_client.post(
                f"/salary/raises/{salary_raise.id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Not authorized." in response.data


# ── Deductions ─────────────────────────────────────────────────────


class TestDeductions:
    """Tests for deduction add/delete endpoints."""

    def test_add_deduction(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id>/deductions adds a deduction."""
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            response = auth_client.post(
                f"/salary/{profile.id}/deductions",
                data={
                    "name": "401k",
                    "deduction_timing_id": pre_tax.id,
                    "calc_method_id": flat_method.id,
                    "amount": "200.00",
                    "deductions_per_year": "26",
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"401k" in response.data

    def test_delete_deduction(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/deductions/<id>/delete removes a deduction."""
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            deduction = PaycheckDeduction(
                salary_profile_id=profile.id,
                deduction_timing_id=pre_tax.id,
                calc_method_id=flat_method.id,
                name="Health Insurance",
                amount=Decimal("150.00"),
            )
            db.session.add(deduction)
            db.session.commit()

            response = auth_client.post(
                f"/salary/deductions/{deduction.id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Deduction removed." in response.data

    def test_add_deduction_validation_error(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id>/deductions with missing fields shows a validation error."""
        with app.app_context():
            profile = _create_profile(seed_user)

            response = auth_client.post(
                f"/salary/{profile.id}/deductions",
                data={"name": ""},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_delete_other_users_deduction_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /salary/deductions/<id>/delete for another user's deduction shows 'Not authorized'."""
        with app.app_context():
            other = _create_other_user_profile()
            pre_tax = db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            deduction = PaycheckDeduction(
                salary_profile_id=other["profile"].id,
                deduction_timing_id=pre_tax.id,
                calc_method_id=flat_method.id,
                name="Other 401k",
                amount=Decimal("100.00"),
            )
            db.session.add(deduction)
            db.session.commit()

            response = auth_client.post(
                f"/salary/deductions/{deduction.id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Not authorized." in response.data

    def test_add_deduction_htmx_returns_partial(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /salary/<id>/deductions with HX-Request returns a partial."""
        with app.app_context():
            profile = _create_profile(seed_user)
            post_tax = db.session.query(DeductionTiming).filter_by(name="post_tax").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            response = auth_client.post(
                f"/salary/{profile.id}/deductions",
                data={
                    "name": "Roth IRA",
                    "deduction_timing_id": post_tax.id,
                    "calc_method_id": flat_method.id,
                    "amount": "300.00",
                    "deductions_per_year": "26",
                },
                headers={"HX-Request": "true"},
            )

            assert response.status_code == 200
            assert b"Roth IRA" in response.data
            assert b"300" in response.data

    def test_add_percentage_deduction_converts_input(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Percentage input (6) is converted to decimal (0.06) for storage."""
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
            pct_method = db.session.query(CalcMethod).filter_by(name="percentage").one()

            auth_client.post(
                f"/salary/{profile.id}/deductions",
                data={
                    "name": "401k Match",
                    "deduction_timing_id": pre_tax.id,
                    "calc_method_id": pct_method.id,
                    "amount": "6",
                    "deductions_per_year": "26",
                },
                follow_redirects=True,
            )

            ded = (
                db.session.query(PaycheckDeduction)
                .filter_by(salary_profile_id=profile.id, name="401k Match")
                .one()
            )
            assert ded.amount == Decimal("0.06")


# ── Breakdown & Projection ────────────────────────────────────────


class TestBreakdown:
    """Tests for breakdown and projection views."""

    def test_breakdown_renders(self, app, auth_client, seed_user, seed_periods):
        """GET /salary/<id>/breakdown/<period_id> renders the breakdown page."""
        with app.app_context():
            profile = _create_profile(seed_user)

            response = auth_client.get(
                f"/salary/{profile.id}/breakdown/{seed_periods[0].id}"
            )

            assert response.status_code == 200
            assert b"Paycheck Breakdown" in response.data
            assert b"Gross Biweekly Pay" in response.data
            assert b"Net Biweekly Paycheck" in response.data

    def test_breakdown_current_redirects(self, app, auth_client, seed_user, seed_periods):
        """GET /salary/<id>/breakdown redirects to the current period breakdown."""
        with app.app_context():
            profile = _create_profile(seed_user)

            response = auth_client.get(f"/salary/{profile.id}/breakdown")

            assert response.status_code == 302
            assert f"/salary/{profile.id}/breakdown/" in response.location

    def test_projection_renders(self, app, auth_client, seed_user, seed_periods):
        """GET /salary/<id>/projection renders the projection table."""
        with app.app_context():
            profile = _create_profile(seed_user)

            response = auth_client.get(f"/salary/{profile.id}/projection")

            assert response.status_code == 200
            assert b"Salary Projection" in response.data
            assert b"Day Job" in response.data
            assert b"Net Biweekly" in response.data

    def test_breakdown_no_current_period(self, app, auth_client, seed_user):
        """GET /salary/<id>/breakdown with no periods flashes a warning."""
        with app.app_context():
            profile = _create_profile(seed_user)

            response = auth_client.get(
                f"/salary/{profile.id}/breakdown",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"No pay periods found" in response.data
            assert b"Generate pay periods" in response.data

    def test_breakdown_other_users_profile_redirects(
        self, app, auth_client, seed_user, seed_periods
    ):
        """GET /salary/<id>/breakdown/<period_id> for another user's profile redirects."""
        with app.app_context():
            other = _create_other_user_profile()

            response = auth_client.get(
                f"/salary/{other['profile'].id}/breakdown/{seed_periods[0].id}",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Salary profile not found." in response.data


# ── Tax Config ─────────────────────────────────────────────────────


class TestTaxConfig:
    """Tests for tax config page, state tax, and FICA config endpoints."""

    def test_tax_config_redirects_to_settings(self, app, auth_client, seed_user):
        """GET /salary/tax-config returns 302 redirect to settings dashboard."""
        with app.app_context():
            response = auth_client.get("/salary/tax-config")

            assert response.status_code == 302
            assert "/settings" in response.headers["Location"]
            assert "section=tax" in response.headers["Location"]

    def test_update_state_tax_config(self, app, auth_client, seed_user):
        """POST /salary/tax-config creates/updates a state tax config."""
        with app.app_context():
            # Seed the 'flat' tax type (needed for creating new state config).
            response = auth_client.post("/salary/tax-config", data={
                "tax_year": "2026",
                "state_code": "NC",
                "flat_rate": "4.50",
            }, follow_redirects=True)

            assert response.status_code == 200
            # Flash could be "created" or "updated" depending on state.
            assert b"State tax config for NC" in response.data

            state_config = (
                db.session.query(StateTaxConfig)
                .filter_by(user_id=seed_user["user"].id, state_code="NC", tax_year=2026)
                .one()
            )
            assert state_config.flat_rate == Decimal("0.0450")

    def test_update_fica_config(self, app, auth_client, seed_user):
        """POST /salary/fica-config creates/updates FICA configuration."""
        with app.app_context():
            response = auth_client.post("/salary/fica-config", data={
                "tax_year": "2026",
                "ss_rate": "6.20",
                "ss_wage_base": "176100.00",
                "medicare_rate": "1.45",
                "medicare_surtax_rate": "0.90",
                "medicare_surtax_threshold": "200000.00",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"FICA config for 2026" in response.data

            fica = (
                db.session.query(FicaConfig)
                .filter_by(user_id=seed_user["user"].id, tax_year=2026)
                .one()
            )
            assert fica.ss_rate == Decimal("0.0620")

    def test_update_state_tax_invalid_code(self, app, auth_client, seed_user):
        """POST /salary/tax-config with invalid state code flashes danger."""
        with app.app_context():
            response = auth_client.post("/salary/tax-config", data={
                "state_code": "X",
                "flat_rate": "5",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid state code." in response.data

    def test_update_fica_validation_error(self, app, auth_client, seed_user):
        """POST /salary/fica-config with missing fields shows a validation error."""
        with app.app_context():
            response = auth_client.post("/salary/fica-config", data={
                "tax_year": "",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data


# ── Helpers for Negative-Path Tests ───────────────────────────────


def _create_second_user_salary_profile(second_user_data):
    """Create a salary profile for the second_user fixture (IDOR testing).

    Mirrors _create_other_user_profile() but reuses the second_user
    fixture data instead of creating a new user.

    Args:
        second_user_data: Dict from the second_user conftest fixture.

    Returns:
        SalaryProfile: the created profile.
    """
    filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
    income_type = db.session.query(TransactionType).filter_by(name="income").one()
    every_period = db.session.query(RecurrencePattern).filter_by(name="every_period").one()

    cat = (
        db.session.query(Category)
        .filter_by(
            user_id=second_user_data["user"].id,
            group_name="Income",
            item_name="Salary",
        )
        .first()
    )
    if not cat:
        cat = Category(
            user_id=second_user_data["user"].id,
            group_name="Income",
            item_name="Salary",
        )
        db.session.add(cat)
        db.session.flush()

    rule = RecurrenceRule(
        user_id=second_user_data["user"].id,
        pattern_id=every_period.id,
    )
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=second_user_data["user"].id,
        account_id=second_user_data["account"].id,
        category_id=cat.id,
        recurrence_rule_id=rule.id,
        transaction_type_id=income_type.id,
        name="Other Job",
        default_amount=Decimal("60000.00") / 26,
        is_active=True,
    )
    db.session.add(template)
    db.session.flush()

    profile = SalaryProfile(
        user_id=second_user_data["user"].id,
        scenario_id=second_user_data["scenario"].id,
        template_id=template.id,
        filing_status_id=filing_status.id,
        name="Other Job",
        annual_salary=Decimal("60000.00"),
        state_code="NC",
        pay_periods_per_year=26,
    )
    db.session.add(profile)
    db.session.commit()
    return profile


# ── Negative Paths ────────────────────────────────────────────────


class TestSalaryNegativePaths:
    """Negative-path tests: nonexistent IDs, IDOR on raises/deductions/views."""

    def test_edit_nonexistent_profile(self, app, auth_client, seed_user):
        """GET /salary/999999/edit for a nonexistent profile redirects with flash."""
        with app.app_context():
            resp = auth_client.get("/salary/999999/edit", follow_redirects=True)

            assert resp.status_code == 200
            assert b"Salary profile not found." in resp.data

    def test_update_nonexistent_profile(self, app, auth_client, seed_user):
        """POST /salary/999999 for a nonexistent profile redirects with flash."""
        with app.app_context():
            filing_status = db.session.query(FilingStatus).filter_by(name="single").one()

            resp = auth_client.post("/salary/999999", data={
                "name": "Ghost",
                "annual_salary": "50000.00",
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Salary profile not found." in resp.data

    def test_delete_nonexistent_profile(self, app, auth_client, seed_user):
        """POST /salary/999999/delete for a nonexistent profile redirects with flash."""
        with app.app_context():
            resp = auth_client.post(
                "/salary/999999/delete", follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"Salary profile not found." in resp.data

    def test_add_raise_to_other_users_profile_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /salary/<id>/raises for another user's profile is blocked."""
        with app.app_context():
            other_profile = _create_second_user_salary_profile(second_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            resp = auth_client.post(
                f"/salary/{other_profile.id}/raises",
                data={
                    "raise_type_id": raise_type.id,
                    "effective_month": "7",
                    "effective_year": "2026",
                    "percentage": "3",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"Salary profile not found." in resp.data

            # Verify no raise was created for the other user's profile.
            raise_count = db.session.query(SalaryRaise).filter_by(
                salary_profile_id=other_profile.id,
            ).count()
            assert raise_count == 0

    def test_add_deduction_to_other_users_profile_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /salary/<id>/deductions for another user's profile is blocked."""
        with app.app_context():
            other_profile = _create_second_user_salary_profile(second_user)
            pre_tax = db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            resp = auth_client.post(
                f"/salary/{other_profile.id}/deductions",
                data={
                    "name": "Sneaky 401k",
                    "deduction_timing_id": pre_tax.id,
                    "calc_method_id": flat_method.id,
                    "amount": "200.00",
                    "deductions_per_year": "26",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"Salary profile not found." in resp.data

            # Verify no deduction was created for the other user's profile.
            ded_count = db.session.query(PaycheckDeduction).filter_by(
                salary_profile_id=other_profile.id,
            ).count()
            assert ded_count == 0

    def test_delete_raise_from_other_users_profile_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /salary/raises/<id>/delete for another user's raise is blocked."""
        with app.app_context():
            other_profile = _create_second_user_salary_profile(second_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            salary_raise = SalaryRaise(
                salary_profile_id=other_profile.id,
                raise_type_id=raise_type.id,
                effective_month=7,
                effective_year=2026,
                percentage=Decimal("0.0300"),
            )
            db.session.add(salary_raise)
            db.session.commit()
            raise_id = salary_raise.id

            resp = auth_client.post(
                f"/salary/raises/{raise_id}/delete",
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"Not authorized." in resp.data

            # Verify raise still exists in DB.
            db.session.expire_all()
            refreshed = db.session.get(SalaryRaise, raise_id)
            assert refreshed is not None

    def test_delete_deduction_from_other_users_profile_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /salary/deductions/<id>/delete for another user's deduction is blocked."""
        with app.app_context():
            other_profile = _create_second_user_salary_profile(second_user)
            pre_tax = db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            deduction = PaycheckDeduction(
                salary_profile_id=other_profile.id,
                deduction_timing_id=pre_tax.id,
                calc_method_id=flat_method.id,
                name="Other 401k",
                amount=Decimal("100.0000"),
            )
            db.session.add(deduction)
            db.session.commit()
            ded_id = deduction.id

            resp = auth_client.post(
                f"/salary/deductions/{ded_id}/delete",
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"Not authorized." in resp.data

            # Verify deduction still exists in DB.
            db.session.expire_all()
            refreshed = db.session.get(PaycheckDeduction, ded_id)
            assert refreshed is not None

    def test_view_other_users_breakdown_idor(
        self, app, auth_client, seed_user, seed_periods, second_user
    ):
        """GET /salary/<id>/breakdown for another user's profile is blocked."""
        with app.app_context():
            other_profile = _create_second_user_salary_profile(second_user)

            resp = auth_client.get(
                f"/salary/{other_profile.id}/breakdown",
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"Salary profile not found." in resp.data
            # No sensitive data from the other user's profile should leak.
            assert b"Other Job" not in resp.data

    def test_view_other_users_projection_idor(
        self, app, auth_client, seed_user, seed_periods, second_user
    ):
        """GET /salary/<id>/projection for another user's profile is blocked."""
        with app.app_context():
            other_profile = _create_second_user_salary_profile(second_user)

            resp = auth_client.get(
                f"/salary/{other_profile.id}/projection",
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"Salary profile not found." in resp.data


# ── Net Biweekly Mismatch Fixes (section 3.3) ────────────────────────


def _seed_state_tax(user_id, rate, tax_year=2026, state_code="NC"):
    """Create a flat state tax config for testing.

    Args:
        user_id: The owning user's ID.
        rate: Decimal flat rate in decimal form (e.g. 0.0399).
        tax_year: Tax year for the config.
        state_code: Two-letter state code.

    Returns:
        StateTaxConfig: The created config.
    """
    flat_type = db.session.query(TaxType).filter_by(name="flat").one()
    config = StateTaxConfig(
        user_id=user_id,
        state_code=state_code,
        tax_year=tax_year,
        tax_type_id=flat_type.id,
        flat_rate=rate,
        standard_deduction=Decimal("25500.00"),
    )
    db.session.add(config)
    db.session.flush()
    return config


def _seed_fica(user_id, tax_year=2026):
    """Create a standard FICA config for testing.

    Returns:
        FicaConfig: The created config.
    """
    config = FicaConfig(
        user_id=user_id,
        tax_year=tax_year,
        ss_rate=Decimal("0.0620"),
        ss_wage_base=Decimal("176100.00"),
        medicare_rate=Decimal("0.0145"),
        medicare_surtax_rate=Decimal("0.0090"),
        medicare_surtax_threshold=Decimal("200000.00"),
    )
    db.session.add(config)
    db.session.flush()
    return config


class TestNetBiweeklyMismatchFixes:
    """Tests for the three root causes of net biweekly mismatch (section 3.3).

    Verifies that:
    1. Updating state tax config regenerates salary transactions.
    2. Updating FICA config regenerates salary transactions.
    3. Creating a salary profile sets template default_amount to NET,
       not GROSS.
    """

    def test_state_tax_update_regenerates_salary_transactions(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Changing the state tax rate updates salary transaction amounts.

        Without the fix, updating the state tax config would leave stale
        transaction amounts in the grid while the salary page showed the
        new net pay.
        """
        with app.app_context():
            user = seed_user["user"]
            profile = _create_profile(seed_user)
            _seed_state_tax(user.id, Decimal("0.0399"))
            _seed_fica(user.id)
            db.session.commit()

            # Grab a projected salary transaction's amount before the change.
            txn_before = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=profile.template_id,
                    scenario_id=seed_user["scenario"].id,
                )
                .first()
            )
            amount_before = txn_before.estimated_amount if txn_before else None

            # Update the state tax rate to a higher value.
            auth_client.post("/salary/tax-config", data={
                "tax_year": "2026",
                "state_code": "NC",
                "flat_rate": "5.50",
                "standard_deduction": "25500.00",
            }, follow_redirects=True)

            # Refresh the session to see the regenerated transactions.
            db.session.expire_all()

            txn_after = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=profile.template_id,
                    scenario_id=seed_user["scenario"].id,
                )
                .first()
            )
            assert txn_after is not None

            # With a higher tax rate, net pay decreases so the amount
            # should be lower (or at least different if the rate changed).
            if amount_before is not None:
                assert txn_after.estimated_amount != amount_before, (
                    "Transaction amount should change when state tax rate changes"
                )

    def test_fica_update_regenerates_salary_transactions(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Changing the FICA SS rate updates salary transaction amounts.

        Without the fix, updating FICA would leave stale transaction
        amounts in the grid.
        """
        with app.app_context():
            user = seed_user["user"]
            profile = _create_profile(seed_user)
            _seed_state_tax(user.id, Decimal("0.0399"))
            _seed_fica(user.id)
            db.session.commit()

            txn_before = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=profile.template_id,
                    scenario_id=seed_user["scenario"].id,
                )
                .first()
            )
            amount_before = txn_before.estimated_amount if txn_before else None

            # Update SS rate to a significantly different value.
            auth_client.post("/salary/fica-config", data={
                "tax_year": "2026",
                "ss_rate": "9.00",
                "ss_wage_base": "176100.00",
                "medicare_rate": "1.45",
                "medicare_surtax_rate": "0.90",
                "medicare_surtax_threshold": "200000.00",
            }, follow_redirects=True)

            db.session.expire_all()

            txn_after = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=profile.template_id,
                    scenario_id=seed_user["scenario"].id,
                )
                .first()
            )
            assert txn_after is not None

            if amount_before is not None:
                assert txn_after.estimated_amount != amount_before, (
                    "Transaction amount should change when SS rate changes"
                )

    def test_tax_update_with_no_profiles_is_safe(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Updating tax config with no salary profiles does not error.

        The regeneration loop should gracefully handle zero profiles.
        """
        with app.app_context():
            response = auth_client.post("/salary/tax-config", data={
                "tax_year": "2026",
                "state_code": "NC",
                "flat_rate": "4.50",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"State tax config for NC" in response.data

    def test_create_profile_sets_template_default_to_net(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Creating a salary profile sets template.default_amount to NET pay.

        Before the fix, default_amount was set to GROSS (annual / 26).
        After the fix, it should be NET (after taxes and deductions).
        Without tax configs, NET equals GROSS (no taxes to deduct), so
        we seed tax configs to produce a meaningful difference.
        """
        with app.app_context():
            user = seed_user["user"]
            _seed_state_tax(user.id, Decimal("0.0399"))
            _seed_fica(user.id)
            db.session.commit()

            filing_status = db.session.query(FilingStatus).filter_by(
                name="single"
            ).one()

            auth_client.post("/salary", data={
                "name": "Net Test Job",
                "annual_salary": "75000.00",
                "filing_status_id": filing_status.id,
                "state_code": "NC",
                "pay_periods_per_year": "26",
            }, follow_redirects=True)

            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=user.id, name="Net Test Job")
                .one()
            )
            template = profile.template

            gross_biweekly = Decimal("75000.00") / 26

            # With taxes configured, net pay is less than gross.
            # The template default_amount should reflect NET, not GROSS.
            assert template.default_amount < gross_biweekly, (
                f"template.default_amount ({template.default_amount}) should "
                f"be less than gross biweekly ({gross_biweekly}) when taxes "
                f"are configured"
            )

    def test_create_profile_without_tax_configs_uses_gross_as_fallback(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Without tax configs, default_amount equals GROSS (no taxes to subtract).

        This is correct behavior -- with no bracket set, federal tax is $0;
        with no state config, state tax is $0; with no FICA config, FICA
        is $0.  NET == GROSS when there are no taxes.
        """
        with app.app_context():
            filing_status = db.session.query(FilingStatus).filter_by(
                name="single"
            ).one()

            auth_client.post("/salary", data={
                "name": "No Tax Job",
                "annual_salary": "60000.00",
                "filing_status_id": filing_status.id,
                "state_code": "NC",
                "pay_periods_per_year": "26",
            }, follow_redirects=True)

            profile = (
                db.session.query(SalaryProfile)
                .filter_by(
                    user_id=seed_user["user"].id,
                    name="No Tax Job",
                )
                .one()
            )
            template = profile.template

            gross_biweekly = Decimal("60000.00") / 26
            expected = gross_biweekly.quantize(Decimal("0.01"))

            # No taxes configured, so NET == GROSS.
            assert template.default_amount == expected, (
                f"Without tax configs, default_amount ({template.default_amount}) "
                f"should equal gross biweekly ({expected})"
            )
