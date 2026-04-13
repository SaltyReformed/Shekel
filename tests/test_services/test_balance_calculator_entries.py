"""
Shekel Budget App -- Balance Calculator Entry-Aware Tests

Tests the entry-aware checking impact formula added to the balance
calculator for entry-capable transactions (scope doc Section 4.2).

The checking impact formula:
    max(estimated_amount - sum_credit_entries, sum_debit_entries)

These tests verify all 6 scenarios from the scope document table,
plus edge cases for status interactions, selectinload fallback,
and mixed transaction types.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import selectinload

from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.ref import Status, TransactionType
from app.services import balance_calculator
from app.enums import StatusEnum


class TestEntryAwareBalance:
    """Tests for the entry-aware checking impact formula.

    Formula: checking_impact = max(estimated - sum_credit, sum_debit)
    Applied only to projected expense transactions with eagerly loaded entries.
    """

    # ── Formula scenarios (scope doc Section 4.2 table) ──────────────

    def test_entry_aware_no_entries(self, app, db, seed_user, seed_periods):
        """Scenario 1: Tracked expense with no entries uses full estimated.

        est=500, debit=0, credit=0.
        Entries loaded as empty list -> fallback to effective_amount -> 500.
        Post-anchor: 5000 - 500 = 4500.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("500.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.flush()

            # Re-query with selectinload -- entries loaded as empty list.
            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # 5000 - 500 = 4500 (full reservation, no entries)
            assert balances[seed_periods[1].id] == Decimal("4500.00")

    def test_entry_aware_debit_under_budget(self, app, db, seed_user, seed_periods):
        """Scenario 2: Debit entries under budget -- full reservation held.

        est=500, debit=200, credit=0.
        max(500 - 0, 200) = max(500, 200) = 500.
        Post-anchor: 5000 - 500 = 4500.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("500.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.flush()

            entry = TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("200.00"),
                description="Kroger",
                entry_date=date(2026, 1, 20),
                is_credit=False,
            )
            db.session.add(entry)
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # max(500 - 0, 200) = 500; 5000 - 500 = 4500
            assert balances[seed_periods[1].id] == Decimal("4500.00")

    def test_entry_aware_mixed_under_budget(self, app, db, seed_user, seed_periods):
        """Scenario 3: Mixed debit+credit under budget -- credit reduces reservation.

        est=500, debit=300, credit=100.
        max(500 - 100, 300) = max(400, 300) = 400.
        Post-anchor: 5000 - 400 = 4600.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("500.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.flush()

            # $300 debit entry
            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("300.00"),
                description="Kroger",
                entry_date=date(2026, 1, 20),
                is_credit=False,
            ))
            # $100 credit entry
            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("100.00"),
                description="Amazon",
                entry_date=date(2026, 1, 21),
                is_credit=True,
            ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # max(500 - 100, 300) = max(400, 300) = 400; 5000 - 400 = 4600
            assert balances[seed_periods[1].id] == Decimal("4600.00")

    def test_entry_aware_all_credit(self, app, db, seed_user, seed_periods):
        """Scenario 4: All credit entries -- only uncovered portion hits checking.

        est=500, debit=0, credit=400.
        max(500 - 400, 0) = max(100, 0) = 100.
        Post-anchor: 5000 - 100 = 4900.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("500.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.flush()

            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("400.00"),
                description="Target CC",
                entry_date=date(2026, 1, 20),
                is_credit=True,
            ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # max(500 - 400, 0) = max(100, 0) = 100; 5000 - 100 = 4900
            assert balances[seed_periods[1].id] == Decimal("4900.00")

    def test_entry_aware_debit_over_budget(self, app, db, seed_user, seed_periods):
        """Scenario 5: Debit overspend -- actual debit total hits balance.

        est=500, debit=530, credit=0.
        max(500 - 0, 530) = max(500, 530) = 530.
        Post-anchor: 5000 - 530 = 4470.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("500.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.flush()

            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("530.00"),
                description="Costco big haul",
                entry_date=date(2026, 1, 20),
                is_credit=False,
            ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # max(500 - 0, 530) = max(500, 530) = 530; 5000 - 530 = 4470
            assert balances[seed_periods[1].id] == Decimal("4470.00")

    def test_entry_aware_mixed_over_budget(self, app, db, seed_user, seed_periods):
        """Scenario 6: Mixed overspend -- debit exceeds adjusted reservation.

        est=500, debit=400, credit=200.
        max(500 - 200, 400) = max(300, 400) = 400.
        Post-anchor: 5000 - 400 = 4600.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("500.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.flush()

            # $400 debit
            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("400.00"),
                description="Kroger",
                entry_date=date(2026, 1, 20),
                is_credit=False,
            ))
            # $200 credit
            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("200.00"),
                description="Amazon CC",
                entry_date=date(2026, 1, 21),
                is_credit=True,
            ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # max(500 - 200, 400) = max(300, 400) = 400; 5000 - 400 = 4600
            assert balances[seed_periods[1].id] == Decimal("4600.00")

    # ── Status interactions ──────────────────────────────────────────

    def test_entry_aware_paid_uses_effective_amount(self, app, db, seed_user, seed_periods):
        """Paid (DONE) transaction with entries -- excluded from balance (settled).

        Settled transactions are skipped by _sum_all (status != projected).
        Balance is anchor only.
        """
        with app.app_context():
            done = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("500.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=done.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
                actual_amount=Decimal("450.00"),
            )
            db.session.add(txn)
            db.session.flush()

            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("450.00"),
                description="Kroger",
                entry_date=date(2026, 1, 20),
                is_credit=False,
            ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # Paid txn is excluded (status != projected) -- balance unchanged.
            assert balances[seed_periods[1].id] == Decimal("5000.00")

    def test_entry_aware_cancelled_excluded(self, app, db, seed_user, seed_periods):
        """Cancelled transaction with entries loaded -- excluded from balance.

        Cancelled status is skipped by _sum_all (status != projected).
        """
        with app.app_context():
            cancelled = db.session.query(Status).filter_by(name="Cancelled").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("500.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=cancelled.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.flush()

            # Entries exist but status is cancelled.
            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("200.00"),
                description="Kroger",
                entry_date=date(2026, 1, 20),
                is_credit=False,
            ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # Cancelled txn excluded -- balance unchanged.
            assert balances[seed_periods[1].id] == Decimal("5000.00")

    def test_entry_aware_credit_status_excluded(self, app, db, seed_user, seed_periods):
        """Credit-status transaction with entries loaded -- excluded from balance.

        Legacy Credit status has excludes_from_balance=True.
        This is a legacy edge case -- entry-capable transactions should
        never reach Credit status per OQ-10.
        """
        with app.app_context():
            credit = db.session.query(Status).filter_by(name="Credit").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("500.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=credit.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.flush()

            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("300.00"),
                description="Amazon CC",
                entry_date=date(2026, 1, 20),
                is_credit=True,
            ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # Credit status excluded -- balance unchanged.
            assert balances[seed_periods[1].id] == Decimal("5000.00")

    def test_entry_aware_income_unchanged(self, app, db, seed_user, seed_periods):
        """Income transactions always use effective_amount, never entry formula.

        Even with entries loaded (which should never happen for income in
        practice), the balance calculator uses effective_amount for income.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()

            txn = Transaction(
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            db.session.add(txn)
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # Income uses effective_amount: 5000 + 2000 = 7000
            assert balances[seed_periods[1].id] == Decimal("7000.00")

    # ── Selectinload behavior ────────────────────────────────────────

    def test_entry_aware_entries_not_loaded(self, app, db, seed_user, seed_periods):
        """Entries NOT selectinloaded -- falls back to effective_amount.

        When entries are not eagerly loaded, the calculator uses
        effective_amount (estimated_amount for projected transactions)
        without triggering a lazy load.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("500.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.flush()

            # Add a $300 credit entry that WOULD reduce checking impact
            # to max(500 - 300, 0) = 200 if entries were loaded.
            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("300.00"),
                description="Amazon CC",
                entry_date=date(2026, 1, 20),
                is_credit=True,
            ))
            db.session.flush()

            # Query WITHOUT selectinload -- entries not in the instance dict.
            all_txns = (
                db.session.query(Transaction)
                .filter(Transaction.id == txn.id)
                .all()
            )

            # Verify entries are NOT loaded in the instance dict.
            assert "entries" not in all_txns[0].__dict__

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # Without entries loaded, falls back to effective_amount = 500.
            # 5000 - 500 = 4500 (not 4800 which would be the entry-aware value).
            assert balances[seed_periods[1].id] == Decimal("4500.00")

            # Verify that no lazy load was triggered during calculation.
            assert "entries" not in all_txns[0].__dict__

    # ── Formula edge cases ───────────────────────────────────────────

    def test_zero_estimated_with_debit_entries(self, app, db, seed_user, seed_periods):
        """Zero estimated amount with debit entries.

        est=0, debit=50, credit=0.
        max(0 - 0, 50) = max(0, 50) = 50.
        The debit overspend is reflected immediately.
        Post-anchor: 5000 - 50 = 4950.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("0.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("0.00"),
            )
            db.session.add(txn)
            db.session.flush()

            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("50.00"),
                description="Surprise purchase",
                entry_date=date(2026, 1, 20),
                is_credit=False,
            ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # max(0 - 0, 50) = 50; 5000 - 50 = 4950
            assert balances[seed_periods[1].id] == Decimal("4950.00")

    def test_credit_exceeds_estimated(self, app, db, seed_user, seed_periods):
        """Credit entries exceed estimated amount.

        est=500, debit=100, credit=600.
        max(500 - 600, 100) = max(-100, 100) = 100.
        Credit covered more than the budget -- only actual debit hits checking.
        Post-anchor: 5000 - 100 = 4900.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("500.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.flush()

            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("100.00"),
                description="Kroger",
                entry_date=date(2026, 1, 20),
                is_credit=False,
            ))
            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("600.00"),
                description="Costco CC",
                entry_date=date(2026, 1, 21),
                is_credit=True,
            ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # max(500 - 600, 100) = max(-100, 100) = 100; 5000 - 100 = 4900
            assert balances[seed_periods[1].id] == Decimal("4900.00")

    def test_smallest_possible_amount(self, app, db, seed_user, seed_periods):
        """Smallest possible entry amount -- one cent.

        est=500, debit=0.01, credit=0.
        max(500 - 0, 0.01) = max(500, 0.01) = 500.
        Full reservation held with one tiny debit.
        Post-anchor: 5000 - 500 = 4500.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("500.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.flush()

            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("0.01"),
                description="Penny candy",
                entry_date=date(2026, 1, 20),
                is_credit=False,
            ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # max(500 - 0, 0.01) = 500; 5000 - 500 = 4500
            assert balances[seed_periods[1].id] == Decimal("4500.00")

    def test_large_values_near_limit(self, app, db, seed_user, seed_periods):
        """Large values near Numeric(12,2) limit -- no overflow in max().

        est=9999999999.99, debit=9999999999.99, credit=0.
        max(9999999999.99 - 0, 9999999999.99) = 9999999999.99.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            large = Decimal("9999999999.99")

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Huge Expense",
                default_amount=large,
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Huge Expense",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=large,
            )
            db.session.add(txn)
            db.session.flush()

            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=large,
                description="Max purchase",
                entry_date=date(2026, 1, 20),
                is_credit=False,
            ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("10000000000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # max(9999999999.99 - 0, 9999999999.99) = 9999999999.99
            # 10000000000.00 - 9999999999.99 = 0.01
            assert balances[seed_periods[1].id] == Decimal("0.01")

    # ── Non-tracked transactions ─────────────────────────────────────

    def test_non_tracked_expense_unchanged(self, app, db, seed_user, seed_periods):
        """Non-tracked expense with no template uses effective_amount.

        This is the existing behavior for all expenses before this commit.
        No template means no entries -- effective_amount = estimated.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn = Transaction(
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1200.00"),
            )
            db.session.add(txn)
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # effective_amount = 1200; 5000 - 1200 = 3800
            assert balances[seed_periods[1].id] == Decimal("3800.00")

    # ── Multi-transaction scenarios ──────────────────────────────────

    def test_multiple_tracked_txns_in_period(self, app, db, seed_user, seed_periods):
        """Two tracked expenses in the same period -- each uses its own entries.

        Groceries: est=500, debit=200, credit=100.
        max(500 - 100, 200) = max(400, 200) = 400.

        Gas: est=80, debit=60, credit=0.
        max(80 - 0, 60) = max(80, 60) = 80.

        Total expenses: 400 + 80 = 480.
        Post-anchor: 5000 - 480 = 4520.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Groceries template
            groc_tmpl = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("500.00"),
                track_individual_purchases=True,
            )
            db.session.add(groc_tmpl)
            db.session.flush()

            groc_txn = Transaction(
                template_id=groc_tmpl.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(groc_txn)
            db.session.flush()

            # Groceries entries: $200 debit, $100 credit
            db.session.add(TransactionEntry(
                transaction_id=groc_txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("200.00"),
                description="Kroger",
                entry_date=date(2026, 1, 20),
                is_credit=False,
            ))
            db.session.add(TransactionEntry(
                transaction_id=groc_txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("100.00"),
                description="Amazon CC",
                entry_date=date(2026, 1, 21),
                is_credit=True,
            ))

            # Gas template
            gas_tmpl = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Car Payment"].id,
                transaction_type_id=expense_type.id,
                name="Gas",
                default_amount=Decimal("80.00"),
                track_individual_purchases=True,
            )
            db.session.add(gas_tmpl)
            db.session.flush()

            gas_txn = Transaction(
                template_id=gas_tmpl.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Gas",
                category_id=seed_user["categories"]["Car Payment"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("80.00"),
            )
            db.session.add(gas_txn)
            db.session.flush()

            # Gas entries: $60 debit
            db.session.add(TransactionEntry(
                transaction_id=gas_txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("60.00"),
                description="Shell",
                entry_date=date(2026, 1, 22),
                is_credit=False,
            ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id.in_([groc_txn.id, gas_txn.id]))
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # Groceries: max(500-100, 200) = 400
            # Gas: max(80-0, 60) = 80
            # 5000 - 400 - 80 = 4520
            assert balances[seed_periods[1].id] == Decimal("4520.00")

    def test_tracked_plus_non_tracked_plus_income(self, app, db, seed_user, seed_periods):
        """Mixed period: tracked expense + non-tracked expense + income.

        Tracked groceries: est=500, debit=300, credit=100.
        max(500 - 100, 300) = 400.

        Non-tracked rent: est=1200, effective_amount=1200.

        Income paycheck: est=2000, effective_amount=2000.

        Post-anchor: 5000 + 2000 - 400 - 1200 = 5400.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()

            # Tracked groceries
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("500.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            groc = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(groc)
            db.session.flush()

            db.session.add(TransactionEntry(
                transaction_id=groc.id,
                user_id=seed_user["user"].id,
                amount=Decimal("300.00"),
                description="Kroger",
                entry_date=date(2026, 1, 20),
                is_credit=False,
            ))
            db.session.add(TransactionEntry(
                transaction_id=groc.id,
                user_id=seed_user["user"].id,
                amount=Decimal("100.00"),
                description="Amazon CC",
                entry_date=date(2026, 1, 21),
                is_credit=True,
            ))

            # Non-tracked rent
            rent = Transaction(
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1200.00"),
            )
            db.session.add(rent)

            # Income paycheck
            paycheck = Transaction(
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            db.session.add(paycheck)
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id.in_([groc.id, rent.id, paycheck.id]))
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # Groceries: max(500-100, 300) = 400
            # Rent: effective_amount = 1200
            # Paycheck: effective_amount = 2000
            # 5000 + 2000 - 400 - 1200 = 5400
            assert balances[seed_periods[1].id] == Decimal("5400.00")

    def test_tracked_expense_with_transfer(self, app, db, seed_user, seed_periods):
        """Tracked expense + transfer shadow in the same period.

        The transfer shadow transaction has transfer_id IS NOT NULL and
        is handled by existing balance logic (effective_amount).
        Entry-aware formula only applies to the tracked expense.

        Tracked groceries: est=500, debit=300, credit=0.
        max(500 - 0, 300) = 500.

        Transfer shadow (expense): est=200, effective_amount=200.

        Post-anchor: 5000 - 500 - 200 = 4300.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("500.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            groc = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(groc)
            db.session.flush()

            db.session.add(TransactionEntry(
                transaction_id=groc.id,
                user_id=seed_user["user"].id,
                amount=Decimal("300.00"),
                description="Kroger",
                entry_date=date(2026, 1, 20),
                is_credit=False,
            ))

            # Create a second account for the transfer destination.
            from app.models.account import Account
            from app.models.ref import AccountType
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
            )
            db.session.add(savings)
            db.session.flush()

            from app.models.transfer import Transfer
            transfer = Transfer(
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                user_id=seed_user["user"].id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                amount=Decimal("200.00"),
            )
            db.session.add(transfer)
            db.session.flush()

            shadow = Transaction(
                transfer_id=transfer.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Transfer to Savings",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("200.00"),
            )
            db.session.add(shadow)
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id.in_([groc.id, shadow.id]))
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # Groceries: max(500-0, 300) = 500 (debit under budget)
            # Transfer shadow: effective_amount = 200 (no entries, no template)
            # 5000 - 500 - 200 = 4300
            assert balances[seed_periods[1].id] == Decimal("4300.00")

    # ── Anchor period (verifies _sum_remaining) ──────────────────────

    def test_anchor_period_entry_aware(self, app, db, seed_user, seed_periods):
        """Entry-aware formula works in the anchor period via _sum_remaining.

        This verifies that _sum_remaining (not just _sum_all) uses
        the entry-aware formula for expenses.

        est=500, debit=0, credit=400.
        max(500 - 400, 0) = max(100, 0) = 100.
        Anchor: 5000 - 100 = 4900.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("500.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            # Transaction in the ANCHOR period (periods[0]).
            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.flush()

            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("400.00"),
                description="Target CC",
                entry_date=date(2026, 1, 5),
                is_credit=True,
            ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # Anchor period uses _sum_remaining:
            # max(500 - 400, 0) = 100; 5000 - 100 = 4900
            assert balances[seed_periods[0].id] == Decimal("4900.00")

    def test_anchor_period_mixed_debit_and_credit(self, app, db, seed_user, seed_periods):
        """Anchor period with mixed entries plus income.

        Groceries: est=500, debit=300, credit=100.
        max(500 - 100, 300) = max(400, 300) = 400.

        Income: est=2000.

        Anchor: 5000 + 2000 - 400 = 6600.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                default_amount=Decimal("500.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            groc = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(groc)
            db.session.flush()

            db.session.add(TransactionEntry(
                transaction_id=groc.id,
                user_id=seed_user["user"].id,
                amount=Decimal("300.00"),
                description="Kroger",
                entry_date=date(2026, 1, 5),
                is_credit=False,
            ))
            db.session.add(TransactionEntry(
                transaction_id=groc.id,
                user_id=seed_user["user"].id,
                amount=Decimal("100.00"),
                description="Amazon CC",
                entry_date=date(2026, 1, 6),
                is_credit=True,
            ))

            paycheck = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            db.session.add(paycheck)
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id.in_([groc.id, paycheck.id]))
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # Groceries: max(500-100, 300) = 400
            # Paycheck: 2000
            # Anchor: 5000 + 2000 - 400 = 6600
            assert balances[seed_periods[0].id] == Decimal("6600.00")


class TestEntryClearedFlag:
    """Tests for the is_cleared flag and the three-bucket formula.

    Formula: checking_impact = max(
        estimated - cleared_debit - sum_credit,
        uncleared_debit,
    )

    Semantics:
      - Cleared debits are already reflected in the anchor balance.
      - Uncleared debits have hit checking but are not yet in the anchor.
      - With every is_cleared=False (the default), the formula reduces
        to max(estimated - sum_credit, sum_debit) -- the original
        pre-fix behavior, verified by the TestEntryAwareBalance scenarios
        above.

    These tests cover the new scenarios where is_cleared=True changes
    the projected reservation.
    """

    def _make_groceries(self, db, seed_user, seed_periods, est="500.00"):
        """Shared setup: create a tracked Groceries transaction in period[1].

        Returns the Transaction object, flushed.
        """
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one()

        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            name="Groceries",
            default_amount=Decimal(est),
            track_individual_purchases=True,
        )
        db.session.add(template)
        db.session.flush()

        txn = Transaction(
            template_id=template.id,
            pay_period_id=seed_periods[1].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=projected.id,
            name="Groceries",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal(est),
        )
        db.session.add(txn)
        db.session.flush()
        return txn

    def test_grocery_bug_scenario_after_true_up(
        self, app, db, seed_user, seed_periods,
    ):
        """The exact user-reported bug: $500 budget, three cleared debits.

        est=500, cleared_debit=462.34, uncleared_debit=0, credit=0.
        After anchor true-up the three purchases are all cleared.
        checking_impact = max(500 - 462.34 - 0, 0) = 37.66.
        Post-anchor: 5000 - 37.66 = 4962.34 (only the remainder is held).
        """
        with app.app_context():
            txn = self._make_groceries(db, seed_user, seed_periods)

            for amt, desc in [
                ("106.86", "Kroger"),
                ("249.71", "Amazon"),
                ("105.77", "Target"),
            ]:
                db.session.add(TransactionEntry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    amount=Decimal(amt),
                    description=desc,
                    entry_date=date(2026, 1, 20),
                    is_credit=False,
                    is_cleared=True,
                ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # max(500 - 462.34, 0) = 37.66; 5000 - 37.66 = 4962.34
            assert balances[seed_periods[1].id] == Decimal("4962.34")

    def test_partial_cleared_and_uncleared(
        self, app, db, seed_user, seed_periods,
    ):
        """Mix of cleared and uncleared debits.

        est=500, cleared_debit=100, uncleared_debit=50, credit=0.
        max(500 - 100 - 0, 50) = max(400, 50) = 400.
        Post-anchor: 5000 - 400 = 4600.

        Interpretation: anchor reflects the $100 cleared, we still
        need to reserve $400 more from checking ($350 future budget +
        $50 uncleared debit floor).
        """
        with app.app_context():
            txn = self._make_groceries(db, seed_user, seed_periods)

            db.session.add(TransactionEntry(
                transaction_id=txn.id, user_id=seed_user["user"].id,
                amount=Decimal("100.00"), description="Kroger cleared",
                entry_date=date(2026, 1, 18),
                is_credit=False, is_cleared=True,
            ))
            db.session.add(TransactionEntry(
                transaction_id=txn.id, user_id=seed_user["user"].id,
                amount=Decimal("50.00"), description="Aldi not cleared",
                entry_date=date(2026, 1, 20),
                is_credit=False, is_cleared=False,
            ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # max(500 - 100, 50) = 400; 5000 - 400 = 4600
            assert balances[seed_periods[1].id] == Decimal("4600.00")

    def test_cleared_overspend_zero_floor(
        self, app, db, seed_user, seed_periods,
    ):
        """Cleared debits exceed estimated -- reservation floors at 0.

        est=500, cleared_debit=600, uncleared_debit=0, credit=0.
        max(500 - 600, 0) = max(-100, 0) = 0.
        Post-anchor: 5000 - 0 = 5000 (overspend already in anchor).
        """
        with app.app_context():
            txn = self._make_groceries(db, seed_user, seed_periods)

            db.session.add(TransactionEntry(
                transaction_id=txn.id, user_id=seed_user["user"].id,
                amount=Decimal("600.00"), description="Overspent",
                entry_date=date(2026, 1, 20),
                is_credit=False, is_cleared=True,
            ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # max(500 - 600, 0) = 0; 5000 - 0 = 5000
            assert balances[seed_periods[1].id] == Decimal("5000.00")

    def test_all_uncleared_matches_legacy(
        self, app, db, seed_user, seed_periods,
    ):
        """Every entry uncleared => reduces to legacy formula.

        est=500, uncleared_debit=200, credit=0.
        max(500 - 0 - 0, 200) = 500. Post-anchor: 5000 - 500 = 4500.
        Identical to the existing debit-under-budget test.
        """
        with app.app_context():
            txn = self._make_groceries(db, seed_user, seed_periods)

            db.session.add(TransactionEntry(
                transaction_id=txn.id, user_id=seed_user["user"].id,
                amount=Decimal("200.00"), description="Kroger",
                entry_date=date(2026, 1, 20),
                is_credit=False, is_cleared=False,
            ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # max(500 - 0 - 0, 200) = 500; 5000 - 500 = 4500
            assert balances[seed_periods[1].id] == Decimal("4500.00")

    def test_cleared_debit_plus_credit(
        self, app, db, seed_user, seed_periods,
    ):
        """Cleared debit + credit entry.

        est=500, cleared_debit=200, uncleared_debit=0, credit=100.
        max(500 - 200 - 100, 0) = 200. Post-anchor: 5000 - 200 = 4800.

        Interpretation: $200 of debit already in anchor, $100 on CC
        will be handled by the CC Payback in the next period, so
        we only need to hold $200 more from checking for this budget.
        """
        with app.app_context():
            txn = self._make_groceries(db, seed_user, seed_periods)

            db.session.add(TransactionEntry(
                transaction_id=txn.id, user_id=seed_user["user"].id,
                amount=Decimal("200.00"), description="Cleared debit",
                entry_date=date(2026, 1, 18),
                is_credit=False, is_cleared=True,
            ))
            db.session.add(TransactionEntry(
                transaction_id=txn.id, user_id=seed_user["user"].id,
                amount=Decimal("100.00"), description="Amazon CC",
                entry_date=date(2026, 1, 20),
                is_credit=True, is_cleared=False,
            ))
            db.session.flush()

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # max(500 - 200 - 100, 0) = 200; 5000 - 200 = 4800
            assert balances[seed_periods[1].id] == Decimal("4800.00")

    def test_new_entries_default_uncleared(
        self, app, db, seed_user, seed_periods,
    ):
        """New entries inserted without specifying is_cleared default to False.

        Verifies the model column default_server_default is wired.
        A fresh entry with amount=$200 must fall into the uncleared
        bucket, matching legacy behavior exactly.
        """
        with app.app_context():
            txn = self._make_groceries(db, seed_user, seed_periods)

            entry = TransactionEntry(
                transaction_id=txn.id, user_id=seed_user["user"].id,
                amount=Decimal("200.00"), description="Kroger",
                entry_date=date(2026, 1, 20),
                is_credit=False,
                # is_cleared not specified -> should default to False
            )
            db.session.add(entry)
            db.session.flush()

            # Re-fetch to trigger server default resolution.
            db.session.refresh(entry)
            assert entry.is_cleared is False

            all_txns = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=all_txns,
            )

            # Default uncleared => legacy formula => 500 held
            assert balances[seed_periods[1].id] == Decimal("4500.00")
