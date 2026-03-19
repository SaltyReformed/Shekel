"""
Shekel Budget App — Idempotency / Double-Submit Tests

Tests that every POST endpoint handles double-submission safely:
  - Login double-submit refreshes session
  - Templates allow duplicates (no unique constraint)
  - Raises allow duplicates (no unique constraint)
  - Deductions allow duplicates (no unique constraint)

NOTE: Several idempotency tests already exist in their respective
route test files: accounts, salary profiles, transfers, savings goals,
categories, and pay periods. This file covers the remaining cases.
"""

from decimal import Decimal

from app.extensions import db
from app.models.category import Category
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import (
    CalcMethod, DeductionTiming, FilingStatus, RaiseType,
    RecurrencePattern, TransactionType,
)
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.transaction_template import TransactionTemplate


# ── Helpers ──────────────────────────────────────────────────────────


def _create_profile(seed_user):
    """Helper: create a salary profile with linked template and recurrence."""
    filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
    income_type = db.session.query(TransactionType).filter_by(name="income").one()
    every_period = db.session.query(RecurrencePattern).filter_by(name="every_period").one()

    cat = (
        db.session.query(Category)
        .filter_by(user_id=seed_user["user"].id, group_name="Income", item_name="Salary")
        .first()
    )
    if not cat:
        cat = Category(user_id=seed_user["user"].id, group_name="Income", item_name="Salary")
        db.session.add(cat)
        db.session.flush()

    rule = RecurrenceRule(user_id=seed_user["user"].id, pattern_id=every_period.id)
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=cat.id,
        recurrence_rule_id=rule.id,
        transaction_type_id=income_type.id,
        name="Day Job",
        default_amount=Decimal("2884.62"),
    )
    db.session.add(template)
    db.session.flush()

    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        template_id=template.id,
        name="Day Job",
        annual_salary=Decimal("75000.00"),
        filing_status_id=filing_status.id,
        state_code="NC",
    )
    db.session.add(profile)
    db.session.commit()
    return profile


# ── Tests ────────────────────────────────────────────────────────────


class TestLoginDoubleSubmit:
    """Double login refreshes session without error."""

    def test_double_login_succeeds(self, app, client, seed_user):
        """POST /login twice with valid credentials succeeds both times
        and the session remains functional."""
        with app.app_context():
            data = {"email": "test@shekel.local", "password": "testpass"}

            # First login.
            resp1 = client.post("/login", data=data, follow_redirects=False)
            assert resp1.status_code == 302

            # Second login while already authenticated — redirects to grid.
            resp2 = client.post("/login", data=data, follow_redirects=False)
            assert resp2.status_code == 302

            # Verify session is live after double login by accessing a protected page.
            protected_resp = client.get("/settings")
            assert protected_resp.status_code == 200
            assert b"Settings" in protected_resp.data


class TestTemplateDoubleSubmit:
    """Templates have no unique constraint — duplicates are created."""

    def test_duplicate_template_creates_second(self, app, auth_client, seed_user):
        """POST /templates twice with same name creates two templates."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            data = {
                "name": "Monthly Rent",
                "default_amount": "1200.00",
                "category_id": seed_user["categories"]["Rent"].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            }

            # First submit.
            resp1 = auth_client.post("/templates", data=data, follow_redirects=True)
            assert resp1.status_code == 200

            # Second submit with same data — no unique constraint, so it succeeds.
            resp2 = auth_client.post("/templates", data=data, follow_redirects=True)
            assert resp2.status_code == 200

            # Verify two templates exist with the same name.
            count = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id,
                name="Monthly Rent",
            ).count()
            assert count == 2


class TestRaiseDoubleSubmit:
    """Raises have no unique constraint — duplicates are created."""

    def test_duplicate_raise_creates_second(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id>/raises twice creates two raise entries."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            data = {
                "raise_type_id": raise_type.id,
                "effective_month": "7",
                "effective_year": "2026",
                "percentage": "3.0000",
            }

            # First submit.
            resp1 = auth_client.post(
                f"/salary/{profile.id}/raises",
                data=data, follow_redirects=True,
            )
            assert b"Raise added." in resp1.data

            # Second submit with same data.
            resp2 = auth_client.post(
                f"/salary/{profile.id}/raises",
                data=data, follow_redirects=True,
            )
            assert b"Raise added." in resp2.data

            # Verify two raises exist.
            count = db.session.query(SalaryRaise).filter_by(
                salary_profile_id=profile.id,
            ).count()
            assert count == 2


class TestDeductionDoubleSubmit:
    """Deductions have no unique constraint — duplicates are created."""

    def test_duplicate_deduction_creates_second(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id>/deductions twice creates two deduction entries."""
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            data = {
                "name": "401k",
                "deduction_timing_id": pre_tax.id,
                "calc_method_id": flat_method.id,
                "amount": "250.0000",
            }

            # First submit.
            resp1 = auth_client.post(
                f"/salary/{profile.id}/deductions",
                data=data, follow_redirects=True,
            )
            assert b"401k" in resp1.data

            # Second submit with same data.
            resp2 = auth_client.post(
                f"/salary/{profile.id}/deductions",
                data=data, follow_redirects=True,
            )
            assert b"401k" in resp2.data

            # Verify two deductions exist.
            count = db.session.query(PaycheckDeduction).filter_by(
                salary_profile_id=profile.id,
                name="401k",
            ).count()
            assert count == 2
