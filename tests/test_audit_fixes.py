"""
Shekel Budget App -- Audit Fix Tests

Tests for fixes identified in the 2026-02-27 adversarial code audit:
- effective_amount Decimal type invariant
- IDOR ownership validation on transfers, savings, templates
- Balance calculator with transfers
- Unique constraint double-submission prevention
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.user import User, UserSettings
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.models.savings_goal import SavingsGoal
from app.models.ref import AccountType, Status, TransactionType
from app.services.auth_service import hash_password
from app.services import balance_calculator


# ── Helpers ──────────────────────────────────────────────────────────


def _create_other_user():
    """Create a second user with their own account and scenario."""
    other = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db.session.add(other)
    db.session.flush()

    settings = UserSettings(user_id=other.id)
    db.session.add(settings)

    checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()

    account = Account(
        user_id=other.id,
        account_type_id=checking_type.id,
        name="Other Checking",
        current_anchor_balance=Decimal("500.00"),
    )
    db.session.add(account)

    savings_account = Account(
        user_id=other.id,
        account_type_id=savings_type.id,
        name="Other Savings",
        current_anchor_balance=Decimal("0.00"),
    )
    db.session.add(savings_account)

    scenario = Scenario(
        user_id=other.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)

    category = Category(
        user_id=other.id,
        group_name="Home",
        item_name="Rent",
    )
    db.session.add(category)
    db.session.flush()
    db.session.commit()

    return {
        "user": other,
        "account": account,
        "savings_account": savings_account,
        "scenario": scenario,
        "category": category,
    }


def _create_savings_account(user_id):
    """Create a savings account for the given user."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = Account(
        user_id=user_id,
        account_type_id=savings_type.id,
        name="Savings",
        current_anchor_balance=Decimal("0.00"),
    )
    db.session.add(acct)
    db.session.flush()
    return acct


# ── Section 1: effective_amount Decimal Type ─────────────────────────


class TestEffectiveAmountDecimal:
    """Verify effective_amount returns Decimal for all status paths."""

    def test_transaction_credit_returns_decimal(self, app, db, seed_user, seed_periods):
        """Cancelled/credit Transaction.effective_amount must be Decimal."""
        credit_status = db.session.query(Status).filter_by(name="Credit").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        txn = Transaction(
            template_id=None,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=credit_status.id,
            name="Test Credit",
            category_id=seed_user["categories"]["Rent"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("100.00"),
        )
        db.session.add(txn)
        db.session.flush()

        assert isinstance(txn.effective_amount, Decimal)
        assert txn.effective_amount == Decimal("0")

    def test_transaction_cancelled_returns_decimal(self, app, db, seed_user, seed_periods):
        """Cancelled Transaction.effective_amount must be Decimal."""
        cancelled_status = db.session.query(Status).filter_by(name="Cancelled").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        txn = Transaction(
            template_id=None,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=cancelled_status.id,
            name="Test Cancelled",
            category_id=seed_user["categories"]["Rent"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("50.00"),
        )
        db.session.add(txn)
        db.session.flush()

        assert isinstance(txn.effective_amount, Decimal)
        assert txn.effective_amount == Decimal("0")

    def test_transfer_cancelled_returns_decimal(self, app, db, seed_user, seed_periods):
        """Cancelled Transfer.effective_amount must be Decimal."""
        cancelled = db.session.query(Status).filter_by(name="Cancelled").one()
        savings_acct = _create_savings_account(seed_user["user"].id)

        xfer = Transfer(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings_acct.id,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=cancelled.id,
            name="Cancelled Transfer",
            amount=Decimal("200.00"),
        )
        db.session.add(xfer)
        db.session.flush()

        assert isinstance(xfer.effective_amount, Decimal)
        assert xfer.effective_amount == Decimal("0")

    def test_transfer_active_returns_decimal(self, app, db, seed_user, seed_periods):
        """Active Transfer.effective_amount returns the amount as Decimal."""
        projected = db.session.query(Status).filter_by(name="Projected").one()
        savings_acct = _create_savings_account(seed_user["user"].id)

        xfer = Transfer(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings_acct.id,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=projected.id,
            name="Active Transfer",
            amount=Decimal("200.00"),
        )
        db.session.add(xfer)
        db.session.flush()

        assert isinstance(xfer.effective_amount, Decimal)
        assert xfer.effective_amount == Decimal("200.00")


# ── Section 2: IDOR -- Transfer Account Ownership ────────────────────


class TestTransferAccountOwnership:
    """Verify transfer routes reject foreign account IDs."""

    def test_create_template_with_other_users_account(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """Creating a transfer template with another user's account returns error."""
        other = _create_other_user()
        savings_acct = _create_savings_account(seed_user["user"].id)

        resp = auth_client.post("/transfers", data={
            "name": "Malicious Transfer",
            "default_amount": "100.00",
            "from_account_id": str(other["account"].id),  # foreign account
            "to_account_id": str(savings_acct.id),
            "recurrence_pattern": "",
        }, follow_redirects=True)

        assert b"Invalid source account" in resp.data
        # Verify no template was created.
        count = db.session.query(TransferTemplate).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_template_with_other_users_to_account(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """Creating a transfer template with another user's to_account returns error."""
        other = _create_other_user()

        resp = auth_client.post("/transfers", data={
            "name": "Malicious Transfer 2",
            "default_amount": "100.00",
            "from_account_id": str(seed_user["account"].id),
            "to_account_id": str(other["account"].id),  # foreign account
            "recurrence_pattern": "",
        }, follow_redirects=True)

        assert b"Invalid destination account" in resp.data

    def test_ad_hoc_with_other_users_account(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """Creating an ad-hoc transfer with foreign account returns 404."""
        other = _create_other_user()
        savings_acct = _create_savings_account(seed_user["user"].id)

        resp = auth_client.post("/transfers/ad-hoc", data={
            "from_account_id": str(other["account"].id),
            "to_account_id": str(savings_acct.id),
            "amount": "50.00",
            "pay_period_id": str(seed_periods[0].id),
            "scenario_id": str(seed_user["scenario"].id),
        })

        assert resp.status_code == 404

    def test_update_transfer_template_with_foreign_from_account(
        self, app, db, auth_client, seed_user, seed_periods, second_user,
    ):
        """Updating a transfer template's from_account to a foreign account is rejected.

        The update route validates account ownership when from_account_id
        is included in the form data, returning a flash error and redirect.
        """
        savings_acct = _create_savings_account(seed_user["user"].id)

        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings_acct.id,
            name="My Transfer",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.commit()

        # Snapshot before attack.
        original_from = template.from_account_id
        original_to = template.to_account_id

        # Try to update from_account_id to second user's account.
        resp = auth_client.post(
            f"/transfers/{template.id}",
            data={
                "name": "My Transfer",
                "default_amount": "100.00",
                "from_account_id": str(second_user["account"].id),
                "to_account_id": str(savings_acct.id),
            },
            follow_redirects=True,
        )

        assert b"Invalid source account" in resp.data

        # Verify template is unchanged in DB.
        db.session.refresh(template)
        assert template.from_account_id == original_from
        assert template.to_account_id == original_to

    def test_update_transfer_template_with_foreign_to_account(
        self, app, db, auth_client, seed_user, seed_periods, second_user,
    ):
        """Updating a transfer template's to_account to a foreign account is rejected.

        Same IDOR vector as from_account but targeting the destination field.
        """
        savings_acct = _create_savings_account(seed_user["user"].id)

        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings_acct.id,
            name="My Transfer 2",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.commit()

        # Snapshot before attack.
        original_from = template.from_account_id
        original_to = template.to_account_id

        # Try to update to_account_id to second user's account.
        resp = auth_client.post(
            f"/transfers/{template.id}",
            data={
                "name": "My Transfer 2",
                "default_amount": "100.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(second_user["account"].id),
            },
            follow_redirects=True,
        )

        assert b"Invalid destination account" in resp.data

        # Verify template is unchanged in DB.
        db.session.refresh(template)
        assert template.from_account_id == original_from
        assert template.to_account_id == original_to


# ── Section 3: IDOR -- Savings Goal Account Ownership ────────────────


class TestSavingsGoalAccountOwnership:
    """Verify savings goal routes reject foreign account IDs."""

    def test_create_goal_with_other_users_account(
        self, app, db, auth_client, seed_user,
    ):
        """Creating a savings goal on another user's account returns error."""
        other = _create_other_user()

        resp = auth_client.post("/savings/goals", data={
            "name": "Malicious Goal",
            "target_amount": "1000.00",
            "account_id": str(other["savings_account"].id),
        }, follow_redirects=True)

        assert b"Invalid account" in resp.data
        count = db.session.query(SavingsGoal).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_update_savings_goal_with_foreign_account(
        self, app, db, auth_client, seed_user, second_user,
    ):
        """Updating a savings goal's account_id to a foreign account is rejected.

        The update route validates account ownership when account_id is
        included in the form data, returning a flash error and redirect.
        """
        savings_acct = _create_savings_account(seed_user["user"].id)

        goal = SavingsGoal(
            user_id=seed_user["user"].id,
            account_id=savings_acct.id,
            name="My Emergency Fund",
            target_amount=Decimal("10000.00"),
        )
        db.session.add(goal)
        db.session.commit()

        # Snapshot before attack.
        original_account_id = goal.account_id

        # Try to update account_id to second user's account.
        resp = auth_client.post(
            f"/savings/goals/{goal.id}",
            data={
                "name": "My Emergency Fund",
                "target_amount": "10000.00",
                "account_id": str(second_user["account"].id),
            },
            follow_redirects=True,
        )

        assert b"Invalid account" in resp.data

        # Verify goal is unchanged in DB.
        db.session.refresh(goal)
        assert goal.account_id == original_account_id


# ── Section 4: IDOR -- Template Account/Category Ownership ───────────


class TestTemplateOwnership:
    """Verify template routes reject foreign account and category IDs."""

    def test_create_template_with_other_users_account(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """Creating a transaction template with foreign account returns error."""
        other = _create_other_user()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        resp = auth_client.post("/templates", data={
            "name": "Malicious Template",
            "default_amount": "50.00",
            "account_id": str(other["account"].id),
            "category_id": str(seed_user["categories"]["Rent"].id),
            "transaction_type_id": str(expense_type.id),
            "recurrence_pattern": "",
        }, follow_redirects=True)

        assert b"Invalid account" in resp.data

    def test_create_template_with_other_users_category(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """Creating a transaction template with foreign category returns error."""
        other = _create_other_user()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        resp = auth_client.post("/templates", data={
            "name": "Malicious Template Cat",
            "default_amount": "50.00",
            "account_id": str(seed_user["account"].id),
            "category_id": str(other["category"].id),
            "transaction_type_id": str(expense_type.id),
            "recurrence_pattern": "",
        }, follow_redirects=True)

        assert b"Invalid category" in resp.data


# ── Section 5: Balance Calculator with Transfers ────────────────────


class TestBalanceWithTransfers:
    """Verify balance_calculator accounts for transfer shadows correctly."""

    def test_outgoing_transfer_reduces_balance(self, app, db, seed_user, seed_periods):
        """A shadow expense from a transfer reduces the source account's balance."""
        from app.services import transfer_service

        projected = db.session.query(Status).filter_by(name="Projected").one()
        savings_acct = _create_savings_account(seed_user["user"].id)
        account = seed_user["account"]

        xfer = transfer_service.create_transfer(
            user_id=seed_user["user"].id,
            from_account_id=account.id,
            to_account_id=savings_acct.id,
            pay_period_id=seed_periods[1].id,
            scenario_id=seed_user["scenario"].id,
            amount=Decimal("200.00"),
            status_id=projected.id,
            name="To Savings",
        )
        db.session.commit()

        # Load only the checking account's shadow (the expense shadow).
        shadow_txns = (
            db.session.query(Transaction)
            .filter_by(transfer_id=xfer.id, account_id=account.id)
            .all()
        )

        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=seed_periods[0].id,
            periods=seed_periods,
            transactions=shadow_txns,
        )

        # Period 0 (anchor): 1000
        # Period 1: 1000 - 200 = 800
        assert balances[seed_periods[1].id] == Decimal("800.00")

    def test_incoming_transfer_increases_balance(self, app, db, seed_user, seed_periods):
        """A shadow income from a transfer increases the destination account's balance."""
        from app.services import transfer_service

        projected = db.session.query(Status).filter_by(name="Projected").one()
        savings_acct = _create_savings_account(seed_user["user"].id)
        account = seed_user["account"]

        xfer = transfer_service.create_transfer(
            user_id=seed_user["user"].id,
            from_account_id=savings_acct.id,
            to_account_id=account.id,
            pay_period_id=seed_periods[1].id,
            scenario_id=seed_user["scenario"].id,
            amount=Decimal("300.00"),
            status_id=projected.id,
            name="From Savings",
        )
        db.session.commit()

        # Load only the checking account's shadow (the income shadow).
        shadow_txns = (
            db.session.query(Transaction)
            .filter_by(transfer_id=xfer.id, account_id=account.id)
            .all()
        )

        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=seed_periods[0].id,
            periods=seed_periods,
            transactions=shadow_txns,
        )

        assert balances[seed_periods[1].id] == Decimal("1300.00")

    def test_cancelled_transfer_no_effect(self, app, db, seed_user, seed_periods):
        """A cancelled transfer's shadows should not affect the balance."""
        from app.services import transfer_service

        projected = db.session.query(Status).filter_by(name="Projected").one()
        cancelled = db.session.query(Status).filter_by(name="Cancelled").one()
        savings_acct = _create_savings_account(seed_user["user"].id)
        account = seed_user["account"]

        xfer = transfer_service.create_transfer(
            user_id=seed_user["user"].id,
            from_account_id=account.id,
            to_account_id=savings_acct.id,
            pay_period_id=seed_periods[1].id,
            scenario_id=seed_user["scenario"].id,
            amount=Decimal("500.00"),
            status_id=projected.id,
            name="Cancelled",
        )
        # Cancel the transfer (updates both shadows to cancelled).
        transfer_service.update_transfer(
            xfer.id, seed_user["user"].id, status_id=cancelled.id
        )
        db.session.commit()

        shadow_txns = (
            db.session.query(Transaction)
            .filter_by(transfer_id=xfer.id, account_id=account.id)
            .all()
        )

        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=seed_periods[0].id,
            periods=seed_periods,
            transactions=shadow_txns,
        )

        # Cancelled transfer shadows should not reduce balance.
        assert balances[seed_periods[1].id] == Decimal("1000.00")


# ── Section 6: Unique Constraint Enforcement ─────────────────────────


class TestUniqueConstraints:
    """Verify duplicate-prevention via unique constraints."""

    def test_duplicate_transfer_template_name_fails(self, app, db, seed_user):
        """Two transfer templates with the same name for one user fail."""
        savings_acct = _create_savings_account(seed_user["user"].id)

        t1 = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings_acct.id,
            name="Monthly Savings",
            default_amount=Decimal("100.00"),
        )
        db.session.add(t1)
        db.session.commit()

        t2 = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings_acct.id,
            name="Monthly Savings",
            default_amount=Decimal("200.00"),
        )
        db.session.add(t2)
        with pytest.raises(IntegrityError, match="uq_transfer_templates_user_name"):
            db.session.flush()
        db.session.rollback()

    def test_duplicate_savings_goal_name_fails(self, app, db, seed_user):
        """Two savings goals with the same name+account fail."""
        savings_acct = _create_savings_account(seed_user["user"].id)

        g1 = SavingsGoal(
            user_id=seed_user["user"].id,
            account_id=savings_acct.id,
            name="Emergency Fund",
            target_amount=Decimal("5000.00"),
        )
        db.session.add(g1)
        db.session.commit()

        g2 = SavingsGoal(
            user_id=seed_user["user"].id,
            account_id=savings_acct.id,
            name="Emergency Fund",
            target_amount=Decimal("10000.00"),
        )
        db.session.add(g2)
        with pytest.raises(IntegrityError, match="uq_savings_goals_user_acct_name"):
            db.session.flush()
        db.session.rollback()


# ── Section 7: Self-Transfer Rejection ───────────────────────────────


class TestSelfTransferRejection:
    """Verify that transfers from an account to itself are rejected.

    Both the schema (validates_schema) and the DB (CHECK constraint)
    enforce from_account_id != to_account_id.
    """

    def test_create_self_transfer_template_rejected(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """Creating a transfer template where from == to is rejected.

        The TransferTemplateCreateSchema validates_schema method catches
        from_account_id == to_account_id and returns a validation error.
        The route flashes a generic error and redirects.
        """
        resp = auth_client.post("/transfers", data={
            "name": "Self Transfer",
            "default_amount": "100.00",
            "from_account_id": str(seed_user["account"].id),
            "to_account_id": str(seed_user["account"].id),
            "recurrence_pattern": "",
        })

        # Schema validation fails → redirect with flash.
        assert resp.status_code == 302

        # Verify no template was created.
        count = db.session.query(TransferTemplate).filter_by(
            user_id=seed_user["user"].id,
            name="Self Transfer",
        ).count()
        assert count == 0

    def test_create_ad_hoc_self_transfer_rejected(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """Creating an ad-hoc transfer where from == to is rejected.

        The TransferCreateSchema validates_schema method catches
        from_account_id == to_account_id and returns 400 with JSON errors.
        """
        resp = auth_client.post("/transfers/ad-hoc", data={
            "from_account_id": str(seed_user["account"].id),
            "to_account_id": str(seed_user["account"].id),
            "amount": "50.00",
            "pay_period_id": str(seed_periods[0].id),
            "scenario_id": str(seed_user["scenario"].id),
        })

        # Schema validation fails → 400 with error details.
        assert resp.status_code == 400

        # Verify no transfer was created.
        from app.models.transfer import Transfer as XferModel
        count = db.session.query(XferModel).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0
