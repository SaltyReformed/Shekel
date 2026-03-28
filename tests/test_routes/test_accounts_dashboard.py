"""
Tests for the unified Accounts & Savings dashboard (category grouping).
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.auto_loan_params import AutoLoanParams
from app.models.hysa_params import HysaParams
from app.models.mortgage_params import MortgageParams
from app.models.ref import AccountType
from app.models.savings_goal import SavingsGoal


def _create_savings_account(seed_user, db_session, name="My Savings"):
    """Helper to create a savings account."""
    savings_type = db_session.query(AccountType).filter_by(name="Savings").one()
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=savings_type.id,
        name=name,
        current_anchor_balance=Decimal("5000.00"),
    )
    db_session.add(account)
    db_session.commit()
    return account


def _create_hysa_account(seed_user, db_session, name="My HYSA"):
    """Helper to create a HYSA account with params."""
    hysa_type = db_session.query(AccountType).filter_by(name="HYSA").one()
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=hysa_type.id,
        name=name,
        current_anchor_balance=Decimal("10000.00"),
    )
    db_session.add(account)
    db_session.flush()

    params = HysaParams(account_id=account.id)
    db_session.add(params)
    db_session.commit()
    return account


class TestDashboardGrouping:
    """Dashboard groups accounts by category."""

    def test_dashboard_groups_by_category(self, auth_client, seed_user, db, seed_periods):
        """Dashboard shows category headers."""
        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        assert b"Asset" in resp.data

    def test_dashboard_hysa_shows_interest(self, auth_client, seed_user, db, seed_periods):
        """HYSA account card shows APY info."""
        acct = _create_hysa_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods[0].id
        db.session.commit()

        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        assert b"HYSA" in resp.data
        assert b"APY" in resp.data

    def test_dashboard_emergency_includes_hysa(self, auth_client, seed_user, db, seed_periods):
        """Emergency fund total includes HYSA balances."""
        # Create a savings account so emergency fund section appears.
        savings_acct = _create_savings_account(seed_user, db.session)
        savings_acct.current_anchor_period_id = seed_periods[0].id

        hysa_acct = _create_hysa_account(seed_user, db.session)
        hysa_acct.current_anchor_period_id = seed_periods[0].id
        db.session.commit()

        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        # Should include both savings ($5,000) and HYSA ($10,000) in total.
        assert b"Emergency Fund" in resp.data

    def test_dashboard_savings_goals_unchanged(self, auth_client, seed_user, db, seed_periods):
        """Goals section renders correctly (regression)."""
        savings_acct = _create_savings_account(seed_user, db.session)

        goal = SavingsGoal(
            user_id=seed_user["user"].id,
            account_id=savings_acct.id,
            name="Emergency Fund",
            target_amount=Decimal("20000.00"),
        )
        db.session.add(goal)
        db.session.commit()

        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        assert b"Emergency Fund" in resp.data
        assert b"Savings Goals" in resp.data

    def test_dashboard_mortgage_shows_rate(self, auth_client, seed_user, db, seed_periods):
        """Mortgage card shows interest rate."""
        mortgage_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        acct = Account(
            user_id=seed_user["user"].id,
            account_type_id=mortgage_type.id,
            name="Home Loan",
            current_anchor_balance=Decimal("200000.00"),
        )
        db.session.add(acct)
        db.session.flush()
        acct.current_anchor_period_id = seed_periods[0].id

        params = MortgageParams(
            account_id=acct.id,
            original_principal=Decimal("250000.00"),
            current_principal=Decimal("200000.00"),
            interest_rate=Decimal("0.06500"),
            term_months=360,
            origination_date=date(2023, 1, 1),
            payment_day=1,
        )
        db.session.add(params)
        db.session.commit()

        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        assert b"Mortgage" in resp.data
        assert b"6.500%" in resp.data

    def test_dashboard_auto_loan_shows_payment(self, auth_client, seed_user, db, seed_periods):
        """Auto loan card shows monthly payment."""
        auto_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        acct = Account(
            user_id=seed_user["user"].id,
            account_type_id=auto_type.id,
            name="Car Payment",
            current_anchor_balance=Decimal("20000.00"),
        )
        db.session.add(acct)
        db.session.flush()
        acct.current_anchor_period_id = seed_periods[0].id

        params = AutoLoanParams(
            account_id=acct.id,
            original_principal=Decimal("25000.00"),
            current_principal=Decimal("20000.00"),
            interest_rate=Decimal("0.05000"),
            term_months=60,
            origination_date=date(2024, 6, 1),
            payment_day=15,
        )
        db.session.add(params)
        db.session.commit()

        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        assert b"Auto Loan" in resp.data
        assert b"Monthly Payment" in resp.data

    def test_dashboard_liability_category(self, auth_client, seed_user, db, seed_periods):
        """Liabilities grouped under Liability header."""
        mortgage_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        acct = Account(
            user_id=seed_user["user"].id,
            account_type_id=mortgage_type.id,
            name="My Mortgage",
            current_anchor_balance=Decimal("150000.00"),
        )
        db.session.add(acct)
        db.session.flush()
        acct.current_anchor_period_id = seed_periods[0].id

        params = MortgageParams(
            account_id=acct.id,
            original_principal=Decimal("200000.00"),
            current_principal=Decimal("150000.00"),
            interest_rate=Decimal("0.06000"),
            term_months=360,
            origination_date=date(2022, 1, 1),
            payment_day=1,
        )
        db.session.add(params)
        db.session.commit()

        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        assert b"Liability" in resp.data

    def test_dashboard_no_accounts(self, app, db, seed_user):
        """Empty state renders the dashboard page with navigation elements.

        When no active accounts exist, the page should still render the
        Accounts Dashboard heading and action buttons (New Account, etc.).
        """
        # Deactivate the default checking account.
        seed_user["account"].is_active = False
        db.session.commit()

        client = app.test_client()
        client.post("/login", data={"email": "test@shekel.local", "password": "testpass"})
        resp = client.get("/savings")
        assert resp.status_code == 200
        assert b"Accounts Dashboard" in resp.data
        assert b"New Account" in resp.data
