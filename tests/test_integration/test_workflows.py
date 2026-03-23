"""
Shekel Budget App -- End-to-End Workflow Tests

Tests multi-step workflows that span services and routes:
  - Salary profile → income transactions in grid
  - Template + recurrence → transactions in correct periods
  - Transfer template → balance calculator includes effects
  - Credit workflow → payback in next period → balance unaffected
  - Anchor balance change → downstream balances recalculate
  - Carry forward → projected items moved to target period
"""

from collections import OrderedDict
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import (
    AccountType, RecurrencePattern, Status, TransactionType,
)
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import (
    balance_calculator,
    carry_forward_service,
    credit_workflow,
    recurrence_engine,
    transfer_recurrence,
)


class TestSalaryToGrid:
    """Salary profile creation → income transactions appear in grid periods."""

    def test_salary_generates_income_per_period(self, app, db, seed_user, seed_periods):
        """Creating a salary template with every_period recurrence populates all periods."""
        with app.app_context():
            income_type = db.session.query(TransactionType).filter_by(name="income").one()
            every_period = db.session.query(RecurrencePattern).filter_by(name="every_period").one()

            # Create recurrence rule and template (mimics salary profile creation).
            rule = RecurrenceRule(user_id=seed_user["user"].id, pattern_id=every_period.id)
            db.session.add(rule)
            db.session.flush()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                name="Paycheck",
                default_amount=Decimal("2884.62"),
                recurrence_rule_id=rule.id,
            )
            db.session.add(template)
            db.session.flush()

            # Generate income transactions across all 10 periods.
            txns = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.commit()

            # One income transaction per period.
            assert len(txns) == 10
            for txn in txns:
                assert txn.transaction_type.name == "income"
                assert txn.estimated_amount == Decimal("2884.62")
                assert txn.status.name == "projected"


class TestTemplateRecurrenceToGrid:
    """Template with monthly recurrence → transactions in correct periods."""

    def test_monthly_recurrence_hits_correct_periods(self, app, db, seed_user, seed_periods):
        """Monthly recurrence on day 15 places transactions in periods containing that day."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()
            monthly = db.session.query(RecurrencePattern).filter_by(name="monthly").one()

            rule = RecurrenceRule(user_id=seed_user["user"].id, pattern_id=monthly.id, day_of_month=15)
            db.session.add(rule)
            db.session.flush()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Rent",
                default_amount=Decimal("1200.00"),
                recurrence_rule_id=rule.id,
            )
            db.session.add(template)
            db.session.flush()

            txns = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.commit()

            # seed_periods starts 2026-01-02, 10 biweekly periods through 2026-05-21.
            # Monthly recurrence on day_of_month=15 hits:
            #   P0: Jan 2-15 → Jan 15, P3: Feb 13-26 → Feb 15,
            #   P5: Mar 13-26 → Mar 15, P7: Apr 10-23 → Apr 15,
            #   P9: May 8-21 → May 15
            # = 5 hits
            assert len(txns) == 5
            for txn in txns:
                assert txn.name == "Rent"
                assert txn.estimated_amount == Decimal("1200.00")
                # Each transaction's period should contain the 15th of some month.
                assert txn.pay_period.start_date.day <= 15 or txn.pay_period.end_date.day >= 15


class TestTransferToBalance:
    """Transfer template → balance calculator includes transfer effects."""

    def test_transfer_reduces_source_balance(self, app, db, seed_user, seed_periods):
        """Balance calculator subtracts outgoing transfers from the source account."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="savings").one()
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                current_anchor_balance=Decimal("0"),
            )
            db.session.add(savings)
            db.session.flush()

            projected = db.session.query(Status).filter_by(name="projected").one()

            # Create a transfer in the first period.
            xfer = Transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Save $200",
                amount=Decimal("200.00"),
            )
            db.session.add(xfer)
            db.session.commit()

            # Calculate balances without transfers.
            balances_no_xfer, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=[],
            )

            # Calculate balances with transfers (for checking account).
            balances_with_xfer, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=[],
                transfers=[xfer],
                account_id=seed_user["account"].id,
            )

            # First period balance should be $200 less with the transfer.
            diff = balances_no_xfer[seed_periods[0].id] - balances_with_xfer[seed_periods[0].id]
            assert diff == Decimal("200.00")


class TestCreditPaybackBalance:
    """Credit workflow → payback in next period → balance unaffected."""

    def test_credit_creates_payback_and_zeroes_effective(self, app, db, seed_user, seed_periods):
        """Marking as credit creates payback and makes original effective_amount 0."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Dinner Out",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("75.00"),
            )
            db.session.add(txn)
            db.session.commit()

            # Mark as credit -- creates payback in next period.
            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.commit()

            db.session.refresh(txn)

            # Original transaction is credit status → effective_amount is 0.
            assert txn.status.name == "credit"
            assert txn.effective_amount == Decimal("0")

            # Payback exists in next period with matching amount.
            assert payback.pay_period_id == seed_periods[1].id
            assert payback.estimated_amount == Decimal("75.00")
            assert payback.credit_payback_for_id == txn.id

            # Balance calculation: credit txn contributes 0, payback contributes full amount.
            all_txns = db.session.query(Transaction).filter(
                Transaction.is_deleted.is_(False),
            ).all()

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods[:3],
                transactions=all_txns,
            )
            # Period 0: credit txn → $0 effect, balance stays 1000.
            assert balances[seed_periods[0].id] == Decimal("1000.00")
            # Period 1: payback expense of $75 → balance drops.
            assert balances[seed_periods[1].id] == Decimal("925.00")


class TestAnchorTrueUpBalance:
    """Anchor balance change → all downstream period balances recalculate."""

    def test_anchor_change_shifts_all_balances(self, app, db, seed_user, seed_periods):
        """Changing anchor balance shifts all downstream period balances by the same delta."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            # Create an expense in period 1.
            txn = Transaction(
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1200.00"),
            )
            db.session.add(txn)
            db.session.commit()

            # Calculate with anchor = $2000.
            balances_2k, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("2000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods[:3],
                transactions=[txn],
            )

            # Calculate with anchor = $3000 (+$1000 difference).
            balances_3k, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("3000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods[:3],
                transactions=[txn],
            )

            # Every period's balance should differ by exactly $1000.
            for period in seed_periods[:3]:
                diff = balances_3k[period.id] - balances_2k[period.id]
                assert diff == Decimal("1000.00")


class TestCarryForwardWorkflow:
    """Carry forward → projected items moved to target period."""

    def test_carry_forward_moves_projected_items(self, app, db, seed_user, seed_periods):
        """carry_forward_unpaid moves projected transactions from source to target.

        Verifies that moved transactions retain their estimated_amount,
        status, name, category_id, and transaction_type_id exactly.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()
            groceries_cat_id = seed_user["categories"]["Groceries"].id

            # Create 2 projected expenses in period 0.
            for name, amount in [("Groceries", "85.00"), ("Gas", "45.00")]:
                txn = Transaction(
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    status_id=projected.id,
                    name=name,
                    category_id=groceries_cat_id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal(amount),
                )
                db.session.add(txn)
            db.session.commit()

            # Carry forward from period 0 → period 1.
            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.commit()

            assert count == 2

            # Period 0 should have no projected transactions.
            db.session.expire_all()
            remaining = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id,
            ).filter(
                Transaction.status.has(name="projected"),
                Transaction.is_deleted.is_(False),
            ).count()
            assert remaining == 0

            # Period 1 should now have the 2 moved transactions.
            moved = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[1].id,
            ).filter(Transaction.is_deleted.is_(False)).all()
            assert len(moved) == 2

            # Verify every moved transaction retains original values.
            by_name = {t.name: t for t in moved}
            assert by_name["Groceries"].estimated_amount == Decimal("85.00")
            assert by_name["Groceries"].pay_period_id == seed_periods[1].id
            assert by_name["Groceries"].status.name == "projected"
            assert by_name["Groceries"].category_id == groceries_cat_id
            assert by_name["Groceries"].transaction_type_id == expense_type.id

            assert by_name["Gas"].estimated_amount == Decimal("45.00")
            assert by_name["Gas"].pay_period_id == seed_periods[1].id
            assert by_name["Gas"].status.name == "projected"
            assert by_name["Gas"].category_id == groceries_cat_id
            assert by_name["Gas"].transaction_type_id == expense_type.id


# ── Additional Carry Forward Tests ───────────────────────────────────


class TestCarryForwardEdgeCases:
    """Edge-case scenarios for carry-forward operations."""

    def test_carry_forward_preserves_amounts_exact(self, app, db, seed_user, seed_periods):
        """Three transactions with distinct amounts retain exact values after move.

        Ensures carry-forward does not round, truncate, or modify
        estimated_amount, name, category_id, or transaction_type_id.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()
            groceries_cat_id = seed_user["categories"]["Groceries"].id
            rent_cat_id = seed_user["categories"]["Rent"].id

            items = [
                ("Electric", Decimal("850.00"), rent_cat_id),
                ("Snacks", Decimal("125.50"), groceries_cat_id),
                ("Coffee", Decimal("43.99"), groceries_cat_id),
            ]
            for name, amount, cat_id in items:
                txn = Transaction(
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    status_id=projected.id,
                    name=name,
                    category_id=cat_id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=amount,
                )
                db.session.add(txn)
            db.session.commit()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.commit()
            assert count == 3

            db.session.expire_all()
            moved = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[1].id,
            ).filter(Transaction.is_deleted.is_(False)).all()
            assert len(moved) == 3
            by_name = {t.name: t for t in moved}

            for name, amount, cat_id in items:
                assert by_name[name].estimated_amount == amount
                assert by_name[name].pay_period_id == seed_periods[1].id
                assert by_name[name].status.name == "projected"
                assert by_name[name].category_id == cat_id
                assert by_name[name].transaction_type_id == expense_type.id

            # Source period has 0 projected transactions remaining.
            source_count = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id,
            ).filter(
                Transaction.status.has(name="projected"),
                Transaction.is_deleted.is_(False),
            ).count()
            assert source_count == 0

    def test_carry_forward_target_has_existing_transactions(
        self, app, db, seed_user, seed_periods,
    ):
        """Carry forward into a period that already has transactions.

        Existing 'done' transactions in the target period must be unmodified.
        Carried-forward 'projected' transactions are appended, not merged.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            done = db.session.query(Status).filter_by(name="done").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            # Create 2 existing 'done' transactions in the TARGET period.
            existing_ids = []
            for name, amount in [("Internet", Decimal("500.00")), ("Phone", Decimal("200.00"))]:
                txn = Transaction(
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    status_id=done.id,
                    name=name,
                    category_id=seed_user["categories"]["Rent"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=amount,
                )
                db.session.add(txn)
                db.session.flush()
                existing_ids.append(txn.id)

            # Create 3 projected transactions in the SOURCE period.
            for name, amount in [
                ("Electric", Decimal("850.00")),
                ("Snacks", Decimal("125.50")),
                ("Coffee", Decimal("43.99")),
            ]:
                txn = Transaction(
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    status_id=projected.id,
                    name=name,
                    category_id=seed_user["categories"]["Groceries"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=amount,
                )
                db.session.add(txn)
            db.session.commit()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.commit()
            assert count == 3

            db.session.expire_all()

            # Target period now has exactly 5 transactions (2 existing + 3 moved).
            target_txns = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[1].id,
            ).filter(Transaction.is_deleted.is_(False)).all()
            assert len(target_txns) == 5

            # Original 'done' transactions are unmodified.
            for eid in existing_ids:
                orig = db.session.get(Transaction, eid)
                assert orig.status.name == "done"
                assert orig.pay_period_id == seed_periods[1].id

            # Verify carried-forward items have correct amounts.
            carried = [t for t in target_txns if t.status.name == "projected"]
            assert len(carried) == 3
            carried_amounts = sorted(t.estimated_amount for t in carried)
            assert carried_amounts == sorted([
                Decimal("850.00"), Decimal("125.50"), Decimal("43.99"),
            ])

    def test_carry_forward_skips_non_projected_transactions(
        self, app, db, seed_user, seed_periods,
    ):
        """Only 'projected' transactions move; done/cancelled/credit stay.

        Creates mixed-status transactions in the source period and verifies
        that only projected items are carried forward.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            done = db.session.query(Status).filter_by(name="done").one()
            cancelled = db.session.query(Status).filter_by(name="cancelled").one()
            credit = db.session.query(Status).filter_by(name="credit").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            # 2 projected (should move), 1 done, 1 cancelled, 1 credit (should stay).
            status_names = [
                ("Move1", Decimal("100.00"), projected),
                ("Move2", Decimal("200.00"), projected),
                ("Paid", Decimal("300.00"), done),
                ("Dropped", Decimal("50.00"), cancelled),
                ("OnCard", Decimal("75.00"), credit),
            ]
            for name, amount, status in status_names:
                txn = Transaction(
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    status_id=status.id,
                    name=name,
                    category_id=seed_user["categories"]["Groceries"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=amount,
                )
                db.session.add(txn)
            db.session.commit()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.commit()
            assert count == 2

            db.session.expire_all()

            # Target period gained exactly 2 transactions.
            target_txns = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[1].id,
            ).filter(Transaction.is_deleted.is_(False)).all()
            assert len(target_txns) == 2
            assert {t.name for t in target_txns} == {"Move1", "Move2"}

            # Source period still has 3 non-projected transactions.
            source_txns = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id,
            ).filter(Transaction.is_deleted.is_(False)).all()
            assert len(source_txns) == 3
            assert {t.name for t in source_txns} == {"Paid", "Dropped", "OnCard"}

    def test_carry_forward_empty_source_period(self, app, db, seed_user, seed_periods):
        """Carry forward from a period with no projected transactions is a no-op.

        Source has 1 done and 1 cancelled -- nothing to move. Target is unchanged.
        """
        with app.app_context():
            done = db.session.query(Status).filter_by(name="done").one()
            cancelled = db.session.query(Status).filter_by(name="cancelled").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            # Source has only non-projected transactions.
            for name, status in [("Paid", done), ("Nope", cancelled)]:
                txn = Transaction(
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    status_id=status.id,
                    name=name,
                    category_id=seed_user["categories"]["Groceries"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("100.00"),
                )
                db.session.add(txn)
            db.session.commit()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.commit()
            assert count == 0

            db.session.expire_all()

            # Target has no transactions.
            target_count = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[1].id,
            ).filter(Transaction.is_deleted.is_(False)).count()
            assert target_count == 0

            # Source unchanged.
            source_count = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id,
            ).filter(Transaction.is_deleted.is_(False)).count()
            assert source_count == 2

    def test_carry_forward_source_equals_target_period(
        self, app, db, seed_user, seed_periods,
    ):
        """Carry forward with source == target returns 0 (early guard in source).

        The carry_forward_service checks `if source_period_id == target_period_id`
        and returns 0 immediately. No transactions are duplicated.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="SelfCarry",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("99.00"),
            )
            db.session.add(txn)
            db.session.commit()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[0].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.commit()
            assert count == 0

            # Transaction is still in period 0 -- nothing changed.
            db.session.expire_all()
            txn_check = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id, name="SelfCarry",
            ).filter(Transaction.is_deleted.is_(False)).one()
            assert txn_check.estimated_amount == Decimal("99.00")
            assert txn_check.status.name == "projected"


# ── Credit Workflow Tests ────────────────────────────────────────────


class TestCreditWorkflowEdgeCases:
    """Edge-case scenarios for the mark-as-credit workflow."""

    def test_mark_as_credit_creates_payback_with_correct_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """Marking as credit creates a payback in the next period with exact amount.

        Verifies the payback transaction's name, amount, status, and
        credit_payback_for_id link back to the original.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Grocery Run",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("75.43"),
            )
            db.session.add(txn)
            db.session.commit()
            txn_id = txn.id

            payback = credit_workflow.mark_as_credit(txn_id, seed_user["user"].id)
            db.session.commit()

            db.session.expire_all()
            txn = db.session.get(Transaction, txn_id)

            # Original transaction status changed to credit.
            assert txn.status.name == "credit"
            assert txn.effective_amount == Decimal("0")

            # Payback transaction created in next period.
            assert payback.pay_period_id == seed_periods[1].id
            assert payback.estimated_amount == Decimal("75.43")
            assert payback.status.name == "projected"
            assert payback.credit_payback_for_id == txn_id
            assert payback.name == "CC Payback: Grocery Run"

    def test_mark_as_credit_on_already_credit_transaction(
        self, app, db, seed_user, seed_periods,
    ):
        """Marking as credit twice is idempotent -- returns existing payback.

        The source code has an idempotency guard: if already credit with
        an existing payback, it returns that payback instead of creating
        a second one.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Double Credit Test",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("50.00"),
            )
            db.session.add(txn)
            db.session.commit()
            txn_id = txn.id

            # First mark-as-credit.
            payback1 = credit_workflow.mark_as_credit(txn_id, seed_user["user"].id)
            db.session.commit()
            payback1_id = payback1.id

            # Second mark-as-credit -- idempotent, returns same payback.
            payback2 = credit_workflow.mark_as_credit(txn_id, seed_user["user"].id)
            db.session.commit()

            assert payback2.id == payback1_id

            # Exactly 1 payback exists.
            db.session.expire_all()
            payback_count = db.session.query(Transaction).filter_by(
                credit_payback_for_id=txn_id,
            ).filter(Transaction.is_deleted.is_(False)).count()
            assert payback_count == 1

    def test_mark_as_credit_on_done_transaction(self, app, db, seed_user, seed_periods):
        """Cannot mark a 'done' transaction as credit -- raises ValidationError.

        Only projected transactions can be newly marked as credit. The
        source code raises ValidationError for any non-projected status.
        """
        with app.app_context():
            done = db.session.query(Status).filter_by(name="done").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=done.id,
                name="Already Paid",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
            )
            db.session.add(txn)
            db.session.commit()

            from app.exceptions import ValidationError as ShekelValidationError
            with pytest.raises(ShekelValidationError, match="Cannot mark a 'done' transaction"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

    def test_mark_as_credit_on_cancelled_transaction(self, app, db, seed_user, seed_periods):
        """Cannot mark a 'cancelled' transaction as credit -- raises ValidationError.

        Cancelled transactions should not generate payback entries.
        """
        with app.app_context():
            cancelled = db.session.query(Status).filter_by(name="cancelled").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=cancelled.id,
                name="Cancelled Order",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("60.00"),
            )
            db.session.add(txn)
            db.session.commit()

            from app.exceptions import ValidationError as ShekelValidationError
            with pytest.raises(ShekelValidationError, match="Cannot mark a 'cancelled' transaction"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

    def test_mark_as_credit_last_period_no_next_period(
        self, app, db, seed_user, seed_periods,
    ):
        """Credit on last period raises ValidationError -- no next period for payback.

        The source calls pay_period_service.get_next_period() which returns
        None for the last period. The source then raises ValidationError.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            last_period = seed_periods[-1]
            txn = Transaction(
                pay_period_id=last_period.id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Last Period Purchase",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("99.99"),
            )
            db.session.add(txn)
            db.session.commit()

            from app.exceptions import ValidationError as ShekelValidationError
            with pytest.raises(ShekelValidationError, match="No next pay period"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

    def test_unmark_credit_removes_payback(self, app, db, seed_user, seed_periods):
        """Unmarking credit reverts status to projected and deletes the payback.

        After unmark, the original transaction is 'projected' again and
        the auto-generated payback transaction no longer exists.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Unmark Test",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("45.00"),
            )
            db.session.add(txn)
            db.session.commit()
            txn_id = txn.id

            # Mark as credit → creates payback.
            payback = credit_workflow.mark_as_credit(txn_id, seed_user["user"].id)
            db.session.commit()
            payback_id = payback.id

            # Verify payback exists.
            db.session.expire_all()
            assert db.session.get(Transaction, payback_id) is not None

            # Unmark credit.
            credit_workflow.unmark_credit(txn_id, seed_user["user"].id)
            db.session.commit()

            # Verify original reverted to projected.
            db.session.expire_all()
            txn = db.session.get(Transaction, txn_id)
            assert txn.status.name == "projected"

            # Verify payback is deleted.
            payback_check = db.session.get(Transaction, payback_id)
            assert payback_check is None

    def test_mark_as_credit_income_raises_validation_error(
        self, app, db, seed_user, seed_periods,
    ):
        """Cannot mark income as credit -- raises ValidationError.

        Marking a paycheck as 'credit' makes no financial sense.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="income").one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2500.00"),
            )
            db.session.add(txn)
            db.session.commit()

            from app.exceptions import ValidationError as ShekelValidationError
            with pytest.raises(ShekelValidationError, match="Cannot mark income as credit"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)


# ── Full Workflow Integration Test ───────────────────────────────────


class TestFullBudgetWorkflow:
    """End-to-end payday reconciliation workflow."""

    def test_full_workflow_create_budget_then_reconcile(
        self, app, db, seed_user, seed_periods,
    ):
        """Simulates a complete payday workflow: create → mark done →
        mark credit → carry forward. Verifies final state of all
        transactions across both periods with exact Decimal amounts.

        Steps:
          1. Create 3 projected expenses in period 0.
          2. Mark first as 'done' with actual_amount different from estimated.
          3. Mark second as 'credit' (creates payback in period 1).
          4. Leave third as 'projected'.
          5. Carry forward from period 0 → period 1 (moves the projected item).
          6. Assert final state of both periods.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            done = db.session.query(Status).filter_by(name="done").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            # Step 1: Create 3 projected expenses in period 0.
            txn1 = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1200.00"),
            )
            txn2 = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Dining Out",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("75.00"),
            )
            txn3 = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Gas Station",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("45.50"),
            )
            db.session.add_all([txn1, txn2, txn3])
            db.session.commit()
            txn1_id, txn2_id, txn3_id = txn1.id, txn2.id, txn3.id

            # Step 2: Mark Rent as 'done' with actual_amount != estimated.
            txn1.status_id = done.id
            txn1.actual_amount = Decimal("1195.00")
            db.session.commit()

            # Step 3: Mark Dining Out as 'credit' → payback in period 1.
            payback = credit_workflow.mark_as_credit(txn2_id, seed_user["user"].id)
            db.session.commit()
            payback_id = payback.id

            # Step 4: Gas Station stays projected.
            # Step 5: Carry forward from period 0 → period 1.
            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.commit()
            assert count == 1  # Only Gas Station (projected) moves.

            # Step 6: Assert final state.
            db.session.expire_all()

            # Period 0: 2 transactions -- 1 done (Rent), 1 credit (Dining Out).
            period0_txns = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id,
            ).filter(Transaction.is_deleted.is_(False)).all()
            assert len(period0_txns) == 2

            by_name_p0 = {t.name: t for t in period0_txns}
            assert by_name_p0["Rent"].status.name == "done"
            assert by_name_p0["Rent"].estimated_amount == Decimal("1200.00")
            assert by_name_p0["Rent"].actual_amount == Decimal("1195.00")
            assert by_name_p0["Dining Out"].status.name == "credit"
            assert by_name_p0["Dining Out"].estimated_amount == Decimal("75.00")
            assert by_name_p0["Dining Out"].effective_amount == Decimal("0")

            # Period 0 has no projected transactions remaining.
            p0_projected = [t for t in period0_txns if t.status.name == "projected"]
            assert len(p0_projected) == 0

            # Period 1: 2 transactions -- payback + carried-forward Gas Station.
            period1_txns = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[1].id,
            ).filter(Transaction.is_deleted.is_(False)).all()
            assert len(period1_txns) == 2

            by_name_p1 = {t.name: t for t in period1_txns}
            assert by_name_p1["CC Payback: Dining Out"].estimated_amount == Decimal("75.00")
            assert by_name_p1["CC Payback: Dining Out"].status.name == "projected"
            assert by_name_p1["CC Payback: Dining Out"].credit_payback_for_id == txn2_id

            assert by_name_p1["Gas Station"].estimated_amount == Decimal("45.50")
            assert by_name_p1["Gas Station"].status.name == "projected"
            assert by_name_p1["Gas Station"].pay_period_id == seed_periods[1].id
