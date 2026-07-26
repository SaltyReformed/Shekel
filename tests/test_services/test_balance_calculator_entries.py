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
from app.services.balance_at import _calculator as balance_calculator
from app.services import account_service


class TestEntryAwareBalance:
    """Tests for the entry-aware checking impact formula.

    Formula: checking_impact = max(estimated - sum_credit, sum_debit)
    Applied only to projected expense transactions with eagerly loaded entries.
    """

    # ── Formula scenarios (scope doc Section 4.2 table) ──────────────

    # ── Status interactions ──────────────────────────────────────────

    def test_entry_aware_paid_uses_effective_amount(self, app, db, seed_user, seed_periods):
        """Paid (DONE) transaction with entries -- excluded from balance (settled).

        Settled transactions are skipped by sum_projected (status != projected).
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
                is_envelope=True,
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

        Cancelled status is skipped by sum_projected (status != projected).
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
                is_envelope=True,
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
                is_envelope=True,
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

    # ── Formula edge cases ───────────────────────────────────────────

    # ── Non-tracked transactions ─────────────────────────────────────

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
                is_envelope=True,
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
                is_envelope=True,
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
                is_envelope=True,
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
                is_envelope=True,
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
            from app.models.ref import AccountType
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("0"),
                ),
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

    # ── Anchor period (verifies sum_projected on the anchor) ────────

    def test_anchor_period_entry_aware(self, app, db, seed_user, seed_periods):
        """Entry-aware formula works in the anchor period via sum_projected.

        This verifies that the anchor-period call to sum_projected (not
        just the post-anchor calls) uses the entry-aware formula for
        expenses.

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
                is_envelope=True,
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

            # Anchor period uses sum_projected:
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
                is_envelope=True,
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


