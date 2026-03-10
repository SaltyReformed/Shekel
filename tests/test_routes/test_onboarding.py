"""Tests for the onboarding welcome banner context processor."""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.salary_profile import SalaryProfile
from app.models.transaction_template import TransactionTemplate
from app.models.ref import FilingStatus, TransactionType


class TestOnboardingBanner:
    """Welcome banner should appear only when setup is incomplete."""

    def test_banner_shows_for_new_user(self, auth_client):
        """A fresh user with no data sees the welcome banner."""
        resp = auth_client.get("/")
        assert resp.status_code == 200
        assert b"Welcome to Shekel!" in resp.data

    def test_banner_hidden_when_all_setup_complete(
        self, auth_client, seed_user, seed_periods,
    ):
        """Banner disappears once pay periods, salary, and templates exist."""
        user = seed_user["user"]
        scenario = seed_user["scenario"]

        filing_status = db.session.query(FilingStatus).filter_by(
            name="single"
        ).one()

        # salary profile
        profile = SalaryProfile(
            user_id=user.id,
            scenario_id=scenario.id,
            filing_status_id=filing_status.id,
            name="Main",
            annual_salary=Decimal("60000"),
        )
        db.session.add(profile)

        # transaction template
        income_type = db.session.query(TransactionType).filter_by(
            name="income"
        ).one()
        tmpl = TransactionTemplate(
            user_id=user.id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Salary"].id,
            transaction_type_id=income_type.id,
            name="Paycheck",
            default_amount=Decimal("2000"),
        )
        db.session.add(tmpl)
        db.session.commit()

        resp = auth_client.get("/")
        assert resp.status_code == 200
        assert b"Welcome to Shekel!" not in resp.data

    def test_banner_shows_checkmarks_for_completed_steps(
        self, auth_client, seed_user, seed_periods,
    ):
        """Completed steps show a check icon; incomplete steps show links."""
        resp = auth_client.get("/")
        html = resp.data.decode()

        # Pay periods exist (from seed_periods), so should be checked off
        assert "bi-check-circle-fill" in html
        assert "line-through" in html

        # Salary and templates don't exist, so should show links
        assert 'href="/salary"' in html or "Set up a salary profile" in html
        assert "Set up recurring transactions" in html

    def test_banner_not_shown_to_anonymous_user(self, client):
        """Anonymous users should not see the banner (redirected to login)."""
        resp = client.get("/", follow_redirects=False)
        # Grid requires login, so redirects
        assert resp.status_code in (302, 303)
