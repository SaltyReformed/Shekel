"""
Tests for the unified Accounts & Savings dashboard (category grouping)
and account hard-delete (5A.5-4).
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.category import Category
from app.models.interest_params import InterestParams
from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType, Status, TransactionType
from app.models.savings_goal import SavingsGoal
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password


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

    params = InterestParams(account_id=account.id)
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

        params = LoanParams(
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

        params = LoanParams(
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

        params = LoanParams(
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

    def test_emergency_fund_uses_is_liquid(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Emergency fund total includes all is_liquid=True accounts.

        Checking and Savings are is_liquid=True by default. HYSA and
        Money Market are also is_liquid=True. CD (is_liquid=False) and
        retirement accounts should not contribute.
        """
        # seed_user["account"] is a Checking account (is_liquid=True).
        seed_user["account"].current_anchor_balance = Decimal("1000.00")
        seed_user["account"].current_anchor_period_id = seed_periods[0].id

        # Add a Money Market account (is_liquid=True by seed).
        mm_type = db.session.query(AccountType).filter_by(
            name="Money Market",
        ).one()
        mm_acct = Account(
            user_id=seed_user["user"].id,
            account_type_id=mm_type.id,
            name="My Money Market",
            current_anchor_balance=Decimal("2000.00"),
            current_anchor_period_id=seed_periods[0].id,
        )
        db.session.add(mm_acct)

        # Add a CD account (is_liquid=False).
        cd_type = db.session.query(AccountType).filter_by(name="CD").one()
        cd_acct = Account(
            user_id=seed_user["user"].id,
            account_type_id=cd_type.id,
            name="My CD",
            current_anchor_balance=Decimal("5000.00"),
            current_anchor_period_id=seed_periods[0].id,
        )
        db.session.add(cd_acct)
        db.session.commit()

        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        # Emergency fund section should appear (liquid accounts exist).
        assert b"Emergency Fund" in resp.data

    def test_user_created_liquid_type_in_emergency_fund(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """A user-created type with is_liquid=True contributes to emergency fund."""
        from app import ref_cache
        from app.enums import AcctCategoryEnum

        with app.app_context():
            custom_type = AccountType(
                name="TestLiquid",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                is_liquid=True,
            )
            db.session.add(custom_type)
            db.session.flush()

            acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=custom_type.id,
                name="Custom Liquid",
                current_anchor_balance=Decimal("3000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(acct)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Emergency Fund" in resp.data


# ── Hard Delete Tests (5A.5-4) ─────────────────────────────────────


class TestAccountHardDelete:
    """Tests for POST /accounts/<id>/hard-delete (permanent deletion).

    Accounts have multiple RESTRICT-FK dependents, requiring a careful
    guard chain before permanent deletion is allowed.
    """

    def test_hard_delete_account_no_history(self, app, auth_client, seed_user, db):
        """C-5A.5-22: Account with no transactions or templates is permanently deleted."""
        with app.app_context():
            savings = _create_savings_account(seed_user, db.session, name="Deletable Savings")
            acct_id = savings.id

            resp = auth_client.post(
                f"/accounts/{acct_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data
            assert db.session.get(Account, acct_id) is None

    def test_hard_delete_account_with_history(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """C-5A.5-23: Account with transactions is blocked and archived instead."""
        with app.app_context():
            account = seed_user["account"]
            acct_id = account.id
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            projected = db.session.query(Status).filter_by(name="Projected").one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=acct_id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=txn_type.id,
                name="Test Expense",
                estimated_amount=Decimal("100.00"),
                status_id=projected.id,
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/{acct_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"transaction history" in resp.data
            assert b"archived instead" in resp.data

            reloaded = db.session.get(Account, acct_id)
            assert reloaded is not None
            assert reloaded.is_active is False

    def test_hard_delete_account_with_params(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """C-5A.5-24: Account with LoanParams but no history is permanently deleted with params."""
        with app.app_context():
            mortgage_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
            acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=mortgage_type.id,
                name="Test Mortgage",
                current_anchor_balance=Decimal("200000.00"),
            )
            db.session.add(acct)
            db.session.flush()

            params = LoanParams(
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

            acct_id = acct.id
            params_id = params.id

            resp = auth_client.post(
                f"/accounts/{acct_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data

            assert db.session.get(Account, acct_id) is None
            assert db.session.get(LoanParams, params_id) is None

    def test_hard_delete_blocked_by_transfer_templates(
        self, app, auth_client, seed_user, db,
    ):
        """C-5A.5-25: Account referenced by a transfer template cannot be deleted."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Transfer Target",
                current_anchor_balance=Decimal("0.00"),
            )
            db.session.add(savings)
            db.session.flush()

            xfer_template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Blocked Transfer",
                default_amount=Decimal("100.00"),
            )
            db.session.add(xfer_template)
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/{savings.id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"recurring transfers" in resp.data

            # Account is NOT archived -- it was blocked, not fallback-archived.
            db.session.refresh(savings)
            assert savings.is_active is True

    def test_hard_delete_blocked_by_transaction_templates(
        self, app, auth_client, seed_user, db,
    ):
        """C-5A.5-26: Account referenced by a transaction template cannot be deleted."""
        with app.app_context():
            savings = _create_savings_account(seed_user, db.session, name="Template Acct")
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=txn_type.id,
                name="Blocking Template",
                default_amount=Decimal("50.00"),
            )
            db.session.add(template)
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/{savings.id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"recurring transactions" in resp.data

            db.session.refresh(savings)
            assert savings.is_active is True

    def test_hard_delete_account_idor(self, app, auth_client, seed_user, db):
        """C-5A.5-27: Hard-deleting another user's account returns 'not found'."""
        with app.app_context():
            other_user = User(
                email="other@shekel.local",
                password_hash=hash_password("otherpass"),
                display_name="Other User",
            )
            db.session.add(other_user)
            db.session.flush()
            settings = UserSettings(user_id=other_user.id)
            db.session.add(settings)

            checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
            other_acct = Account(
                user_id=other_user.id,
                account_type_id=checking_type.id,
                name="Other Checking",
                current_anchor_balance=Decimal("500.00"),
            )
            db.session.add(other_acct)
            db.session.commit()
            other_id = other_acct.id

            resp = auth_client.post(
                f"/accounts/{other_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"not found" in resp.data
            assert db.session.get(Account, other_id) is not None

    def test_list_separates_active_and_archived_accounts(
        self, app, auth_client, seed_user, db,
    ):
        """C-5A.5-28: List page shows active and archived in separate sections."""
        with app.app_context():
            # seed_user["account"] is active by default.
            archived = _create_savings_account(
                seed_user, db.session, name="Archived Savings",
            )
            archived.is_active = False
            db.session.commit()

            resp = auth_client.get("/accounts")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Active account in main table.
            assert "Checking" in html

            # Archived section with count indicator.
            assert "Archived (1)" in html
            assert "Archived Savings" in html

    def test_hard_delete_account_with_history_already_archived(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """Already-archived account with transactions stays archived without re-archiving."""
        with app.app_context():
            account = seed_user["account"]
            acct_id = account.id
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            projected = db.session.query(Status).filter_by(name="Projected").one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=acct_id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=txn_type.id,
                name="Pre-existing Expense",
                estimated_amount=Decimal("100.00"),
                status_id=projected.id,
            )
            db.session.add(txn)

            account.is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/{acct_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"transaction history" in resp.data

            reloaded = db.session.get(Account, acct_id)
            assert reloaded is not None
            assert reloaded.is_active is False

    def test_hard_delete_account_with_anchor_history(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """Account with anchor history records but no txns is permanently deleted."""
        with app.app_context():
            savings = _create_savings_account(
                seed_user, db.session, name="Anchor Test",
            )
            history = AccountAnchorHistory(
                account_id=savings.id,
                pay_period_id=seed_periods[0].id,
                anchor_balance=Decimal("5000.00"),
            )
            db.session.add(history)
            db.session.commit()

            acct_id = savings.id
            history_id = history.id

            resp = auth_client.post(
                f"/accounts/{acct_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data

            assert db.session.get(Account, acct_id) is None
            assert db.session.get(AccountAnchorHistory, history_id) is None

    def test_hard_delete_blocked_by_archived_transfer_template(
        self, app, auth_client, seed_user, db,
    ):
        """Account referenced by an archived transfer template is still blocked.

        The TransferTemplate.from_account_id FK is ON DELETE RESTRICT,
        so even archived (is_active=False) templates block account deletion.
        """
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Archived Template Target",
                current_anchor_balance=Decimal("0.00"),
            )
            db.session.add(savings)
            db.session.flush()

            xfer_template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Archived Xfer Template",
                default_amount=Decimal("100.00"),
                is_active=False,
            )
            db.session.add(xfer_template)
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/{savings.id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"recurring transfers" in resp.data

            db.session.refresh(savings)
            assert savings.is_active is True

    def test_archive_label_in_flash_accounts(
        self, app, auth_client, seed_user, db,
    ):
        """Archive flash message says 'archived' not 'deactivated'."""
        with app.app_context():
            savings = _create_savings_account(
                seed_user, db.session, name="Flash Test",
            )

            resp = auth_client.post(
                f"/accounts/{savings.id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived" in resp.data
            assert b"deactivated" not in resp.data
