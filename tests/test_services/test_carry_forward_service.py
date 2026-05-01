"""
Dedicated unit tests for app.services.carry_forward_service.

Fills coverage gaps not addressed by existing carry-forward tests in
test_credit_workflow.py, test_workflows.py, test_hostile_qa.py, and
test_idempotency.py.  Focuses on:

  - Non-template transactions preserving is_override=False
  - Settled status exclusion (added in WU-05)
  - actual_amount preservation across carry forward
  - scenario_id preservation across carry forward
  - Comprehensive mixed-status test covering all 6 statuses + deleted
  - Envelope branch (Phase 4 of carry-forward-aftermath plan):
    settle source at entries-sum and roll the unspent leftover into
    the target period's canonical row, with one row per (template,
    period) so cell == subtotal == balance.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import RecurrencePatternEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import Account
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import (
    AccountType, RecurrencePattern, Status, TransactionType,
)
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import (
    balance_calculator,
    carry_forward_service,
    recurrence_engine,
    transfer_recurrence,
    transfer_service,
)


def _create_transaction(seed_user, seed_periods, period_index=0,
                        status_name="Projected", template_id=None,
                        is_deleted=False, name="Test Expense",
                        amount="100.00", actual_amount=None):
    """Create a test transaction in the given period.

    Args:
        seed_user: The seed_user fixture dict.
        seed_periods: The seed_periods fixture list.
        period_index: Index into seed_periods for pay_period_id.
        status_name: Name of the status to look up.
        template_id: Optional template FK.
        is_deleted: Soft-delete flag.
        name: Transaction display name.
        amount: Estimated amount as string.
        actual_amount: Optional actual amount as string.

    Returns:
        The created Transaction object (flushed, not committed).
    """
    status = db.session.query(Status).filter_by(name=status_name).one()
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

    txn = Transaction(
        pay_period_id=seed_periods[period_index].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=status.id,
        name=name,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal(amount),
        actual_amount=Decimal(actual_amount) if actual_amount else None,
        template_id=template_id,
        is_deleted=is_deleted,
    )
    db.session.add(txn)
    db.session.flush()
    return txn


class TestCarryForwardUnpaid:
    """Unit tests for carry_forward_unpaid covering gaps in existing tests."""

    def test_non_template_transaction_preserves_is_override_false(
        self, app, db, seed_user, seed_periods
    ):
        """A non-template transaction retains is_override=False after carry forward.

        Existing tests verify template-linked items ARE flagged is_override=True.
        This test verifies the inverse: ad-hoc transactions (template_id=None)
        must NOT have is_override set to True.
        """
        with app.app_context():
            txn = _create_transaction(seed_user, seed_periods, name="Ad-hoc Expense")
            assert txn.template_id is None
            assert txn.is_override is False

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.flush()

            db.session.refresh(txn)
            # Non-template transaction must remain is_override=False.
            assert txn.is_override is False
            assert txn.pay_period_id == seed_periods[1].id

    def test_settled_status_not_moved(self, app, db, seed_user, seed_periods):
        """Transactions with 'settled' status are not carried forward.

        The 'settled' status was added in WU-05. It is a terminal status
        (done/received -> settled) and must not be moved.
        """
        with app.app_context():
            txn = _create_transaction(
                seed_user, seed_periods, status_name="Settled",
                name="Settled Bill",
            )
            original_period_id = txn.pay_period_id

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.flush()

            assert count == 0
            db.session.refresh(txn)
            assert txn.pay_period_id == original_period_id

    def test_actual_amount_preserved_after_carry_forward(
        self, app, db, seed_user, seed_periods
    ):
        """Both estimated_amount and actual_amount are unchanged after carry forward.

        Existing tests verify estimated_amount preservation. This test
        explicitly checks actual_amount as well, since the service modifies
        pay_period_id and potentially is_override but must not touch amounts.
        """
        with app.app_context():
            txn = _create_transaction(
                seed_user, seed_periods,
                name="Partial Payment",
                amount="1234.56",
                actual_amount="1200.00",
            )

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.estimated_amount == Decimal("1234.56")
            assert txn.actual_amount == Decimal("1200.00")
            assert txn.pay_period_id == seed_periods[1].id

    def test_scenario_id_preserved_after_carry_forward(
        self, app, db, seed_user, seed_periods
    ):
        """The transaction's scenario_id is unchanged after carry forward.

        The service only modifies pay_period_id and is_override. This test
        verifies scenario_id is not inadvertently altered.
        """
        with app.app_context():
            txn = _create_transaction(seed_user, seed_periods, name="Scenario Check")
            original_scenario_id = txn.scenario_id

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.scenario_id == original_scenario_id

    def test_all_statuses_comprehensive(self, app, db, seed_user, seed_periods):
        """All 6 statuses plus soft-deleted: only non-deleted projected moves.

        Creates one transaction for each status (projected, done, received,
        credit, cancelled, settled) plus one projected+deleted. Verifies
        exactly 1 transaction moves and 6 remain in the source period.
        """
        with app.app_context():
            statuses = ["Projected", "Paid", "Received", "Credit", "Cancelled", "Settled"]
            original_ids = {}

            for status_name in statuses:
                txn = _create_transaction(
                    seed_user, seed_periods,
                    status_name=status_name,
                    name=f"Status-{status_name}",
                )
                original_ids[status_name] = txn.id

            # Also create a projected+deleted transaction (should NOT move).
            deleted_txn = _create_transaction(
                seed_user, seed_periods,
                status_name="Projected",
                is_deleted=True,
                name="Deleted-projected",
            )
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.flush()

            # Only the non-deleted projected transaction moved.
            assert count == 1

            # Target period has exactly 1 transaction.
            target_txns = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods[1].id)
                .all()
            )
            assert len(target_txns) == 1
            assert target_txns[0].name == "Status-Projected"

            # Source period retains 6 transactions (5 non-projected + 1 deleted).
            source_txns = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods[0].id)
                .all()
            )
            assert len(source_txns) == 6

            # Verify each non-moved transaction is still in the source.
            for status_name in ["Paid", "Received", "Credit", "Cancelled", "Settled"]:
                txn = db.session.get(Transaction, original_ids[status_name])
                assert txn.pay_period_id == seed_periods[0].id, (
                    f"{status_name} transaction should stay in source period"
                )

            # Deleted projected also stays in source.
            db.session.refresh(deleted_txn)
            assert deleted_txn.pay_period_id == seed_periods[0].id

    def test_carry_forward_only_moves_transactions_for_specified_scenario(
        self, app, db, seed_user, seed_periods
    ):
        """Carry forward with scenario_id only moves that scenario's transactions.

        Creates projected transactions in both the baseline and an alternative
        scenario, then carries forward only the baseline.  The alternative
        scenario's transactions must remain untouched.
        """
        with app.app_context():
            baseline_scenario = seed_user["scenario"]

            # Create an alternative scenario for the same user.
            alt_scenario = Scenario(
                user_id=seed_user["user"].id,
                name="What-If",
                is_baseline=False,
            )
            db.session.add(alt_scenario)
            db.session.flush()

            status = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            # Baseline projected transaction.
            baseline_txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=baseline_scenario.id,
                account_id=seed_user["account"].id,
                status_id=status.id,
                name="Baseline Expense",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("50.00"),
            )
            db.session.add(baseline_txn)

            # Alternative scenario projected transaction.
            alt_txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=alt_scenario.id,
                account_id=seed_user["account"].id,
                status_id=status.id,
                name="Alt Expense",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("75.00"),
            )
            db.session.add(alt_txn)
            db.session.flush()

            # Carry forward only the baseline scenario.
            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, baseline_scenario.id,
            )
            db.session.flush()

            # Only the baseline transaction should have moved.
            assert count == 1

            db.session.refresh(baseline_txn)
            assert baseline_txn.pay_period_id == seed_periods[1].id

            # Alternative scenario transaction must remain in source.
            db.session.refresh(alt_txn)
            assert alt_txn.pay_period_id == seed_periods[0].id


# ── Shadow Transaction Carry Forward Tests ─────────────────────────


def _create_savings(seed_user):
    """Create a savings account for transfer tests."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = Account(
        user_id=seed_user["user"].id,
        account_type_id=savings_type.id,
        name="CF Savings",
        current_anchor_balance=Decimal("0"),
    )
    db.session.add(acct)
    db.session.flush()
    return acct


def _create_transfer_in_period(seed_user, seed_periods, period_index=0):
    """Create a transfer with shadows in the given period."""
    savings = _create_savings(seed_user)
    projected = db.session.query(Status).filter_by(name="Projected").one()
    xfer = transfer_service.create_transfer(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        pay_period_id=seed_periods[period_index].id,
        scenario_id=seed_user["scenario"].id,
        amount=Decimal("200.00"),
        status_id=projected.id,
        category_id=seed_user["categories"]["Rent"].id,
        name="CF Transfer",
    )
    db.session.flush()
    return xfer


class TestCarryForwardShadowTransactions:
    """Tests for carry forward behavior with shadow transactions."""

    def test_moves_shadow_transactions_via_service(
        self, app, db, seed_user, seed_periods
    ):
        """Shadow transactions are carried forward atomically with their parent."""
        with app.app_context():
            xfer = _create_transfer_in_period(seed_user, seed_periods, 0)
            regular = _create_transaction(seed_user, seed_periods, name="Regular")
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            assert count == 2  # 1 regular + 1 transfer

            db.session.refresh(regular)
            assert regular.pay_period_id == seed_periods[1].id

            db.session.refresh(xfer)
            assert xfer.pay_period_id == seed_periods[1].id

            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer.id
            ).all()
            assert len(shadows) == 2
            for s in shadows:
                assert s.pay_period_id == seed_periods[1].id

    def test_deduplicates_shadow_pairs(
        self, app, db, seed_user, seed_periods
    ):
        """Both shadows from one transfer count as a single carried item."""
        with app.app_context():
            xfer = _create_transfer_in_period(seed_user, seed_periods, 0)
            db.session.flush()

            # Both shadows are in period 0 (query returns both).
            shadow_count = db.session.query(Transaction).filter(
                Transaction.transfer_id == xfer.id,
                Transaction.pay_period_id == seed_periods[0].id,
            ).count()
            assert shadow_count == 2

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            # Counted as 1 transfer, not 2 shadows.
            assert count == 1

    def test_sets_is_override_on_transfer_and_shadows(
        self, app, db, seed_user, seed_periods
    ):
        """Carry forward sets is_override=True on transfer and both shadows."""
        with app.app_context():
            xfer = _create_transfer_in_period(seed_user, seed_periods, 0)
            db.session.flush()

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            db.session.refresh(xfer)
            assert xfer.is_override is True

            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer.id
            ).all()
            for s in shadows:
                assert s.is_override is True

    def test_ignores_done_shadows(
        self, app, db, seed_user, seed_periods
    ):
        """Done transfer and shadows are not carried forward."""
        with app.app_context():
            xfer = _create_transfer_in_period(seed_user, seed_periods, 0)
            done = db.session.query(Status).filter_by(name="Paid").one()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, status_id=done.id
            )
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            assert count == 0
            db.session.refresh(xfer)
            assert xfer.pay_period_id == seed_periods[0].id

    def test_ignores_cancelled_shadows(
        self, app, db, seed_user, seed_periods
    ):
        """Cancelled transfer and shadows are not carried forward."""
        with app.app_context():
            xfer = _create_transfer_in_period(seed_user, seed_periods, 0)
            cancelled = db.session.query(Status).filter_by(name="Cancelled").one()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, status_id=cancelled.id
            )
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            assert count == 0
            db.session.refresh(xfer)
            assert xfer.pay_period_id == seed_periods[0].id

    def test_ignores_soft_deleted_shadows(
        self, app, db, seed_user, seed_periods
    ):
        """Soft-deleted transfer and shadows are not carried forward."""
        with app.app_context():
            xfer = _create_transfer_in_period(seed_user, seed_periods, 0)
            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=True
            )
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            assert count == 0

    def test_mixed_regular_and_shadow(
        self, app, db, seed_user, seed_periods
    ):
        """Mixed period: 2 regular + 1 transfer + 1 done regular."""
        with app.app_context():
            reg1 = _create_transaction(seed_user, seed_periods, name="Reg 1")
            reg2 = _create_transaction(seed_user, seed_periods, name="Reg 2")
            xfer = _create_transfer_in_period(seed_user, seed_periods, 0)
            done_txn = _create_transaction(
                seed_user, seed_periods, status_name="Paid", name="Done"
            )
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            assert count == 3  # 2 regular + 1 transfer

            db.session.refresh(reg1)
            db.session.refresh(reg2)
            db.session.refresh(xfer)
            db.session.refresh(done_txn)
            assert reg1.pay_period_id == seed_periods[1].id
            assert reg2.pay_period_id == seed_periods[1].id
            assert xfer.pay_period_id == seed_periods[1].id
            assert done_txn.pay_period_id == seed_periods[0].id

    def test_multiple_transfers(
        self, app, db, seed_user, seed_periods
    ):
        """Multiple transfers in one period are each carried forward once."""
        with app.app_context():
            # Need a second savings account for the second transfer.
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings"
            ).one()
            savings2 = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="CF Savings 2",
                current_anchor_balance=Decimal("0"),
            )
            db.session.add(savings2)
            db.session.flush()

            projected = db.session.query(Status).filter_by(name="Projected").one()

            xfer1 = _create_transfer_in_period(seed_user, seed_periods, 0)
            xfer2 = transfer_service.create_transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings2.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                amount=Decimal("150.00"),
                status_id=projected.id,
                category_id=seed_user["categories"]["Rent"].id,
                name="CF Transfer 2",
            )
            reg = _create_transaction(seed_user, seed_periods, name="Reg")
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            assert count == 3  # 1 regular + 2 transfers

            db.session.refresh(xfer1)
            db.session.refresh(xfer2)
            assert xfer1.pay_period_id == seed_periods[1].id
            assert xfer2.pay_period_id == seed_periods[1].id

    def test_only_shadows_in_period(
        self, app, db, seed_user, seed_periods
    ):
        """Period with only shadow transactions (no regular) carries forward."""
        with app.app_context():
            xfer = _create_transfer_in_period(seed_user, seed_periods, 0)
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            assert count == 1
            db.session.refresh(xfer)
            assert xfer.pay_period_id == seed_periods[1].id

    def test_preserves_regular_transaction_behavior(
        self, app, db, seed_user, seed_periods
    ):
        """Regular transactions behave identically to pre-rework (regression)."""
        with app.app_context():
            reg1 = _create_transaction(seed_user, seed_periods, name="Reg A")
            reg2 = _create_transaction(seed_user, seed_periods, name="Reg B")
            reg3 = _create_transaction(seed_user, seed_periods, name="Reg C")
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            assert count == 3
            for txn in [reg1, reg2, reg3]:
                db.session.refresh(txn)
                assert txn.pay_period_id == seed_periods[1].id


# ── Override-sibling Carry Forward Tests ───────────────────────────


def _create_template(seed_user, name="Recurring Bill",
                     amount="100.00", category_key="Rent"):
    """Create a TransactionTemplate without a recurrence rule.

    Used by override-sibling tests that hand-place rule-generated rows
    rather than driving them through the recurrence engine.  Returns
    the persisted template.
    """
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"][category_key].id,
        transaction_type_id=expense_type.id,
        name=name,
        default_amount=Decimal(amount),
    )
    db.session.add(template)
    db.session.flush()
    return template


class TestCarryForwardOverrideSibling:
    """Regression for the production bug: carry-forward into a target
    period that already holds a rule-generated row from the same
    template.  The relaxed partial unique index permits a carried
    is_override=True row to coexist with the rule-generated parent.
    """

    def test_carries_into_target_with_existing_rule_generated(
        self, app, db, seed_user, seed_periods
    ):
        """Override sibling coexists with rule-generated parent.

        Reproduces the production traceback:
            UniqueViolation: idx_transactions_template_period_scenario
            Key (template_id, pay_period_id, scenario_id)=(N, target, S)
            already exists.

        Under the relaxed index the carried row is permitted because
        is_override=True is excluded from the partial uniqueness
        predicate.
        """
        with app.app_context():
            template = _create_template(seed_user)

            # Rule-generated row in the source period (period 0) -- this
            # is what the user wants to carry forward.
            source = _create_transaction(
                seed_user, seed_periods, period_index=0,
                template_id=template.id, name=template.name,
                amount=str(template.default_amount),
            )
            # Rule-generated row already in the target period (period 1)
            # -- placed by the recurrence engine before the user clicked
            # "Carry Fwd."  is_override=False because it is the canonical
            # next-period instance.
            target_existing = _create_transaction(
                seed_user, seed_periods, period_index=1,
                template_id=template.id, name=template.name,
                amount=str(template.default_amount),
            )
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.flush()

            # One carried regular transaction.
            assert count == 1

            # Both rows now live in the target period.
            db.session.refresh(source)
            db.session.refresh(target_existing)
            assert source.pay_period_id == seed_periods[1].id
            assert target_existing.pay_period_id == seed_periods[1].id

            # The carried row is the override sibling; the
            # pre-existing rule-generated row keeps is_override=False.
            assert source.is_override is True
            assert target_existing.is_override is False

            # Both rows retain the template link so the companion view
            # and recurrence engine can still see them.
            assert source.template_id == template.id
            assert target_existing.template_id == template.id

            # Sanity: target period now has exactly two non-deleted
            # transactions for this template/scenario.
            rows = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).all()
            )
            assert len(rows) == 2
            override_flags = sorted(r.is_override for r in rows)
            assert override_flags == [False, True]

    def test_balance_calculator_sums_both_override_sibling_rows(
        self, app, db, seed_user, seed_periods
    ):
        """Balance calculator reflects the doubled obligation.

        After carry-forward, the target period's projected expense
        subtotal should include both the rule-generated and the
        override-sibling row.  Balance projections must drop by the
        full sum, not just one row's amount.
        """
        with app.app_context():
            template = _create_template(seed_user, amount="250.00")
            _create_transaction(
                seed_user, seed_periods, period_index=0,
                template_id=template.id, amount="250.00",
                name=template.name,
            )
            _create_transaction(
                seed_user, seed_periods, period_index=1,
                template_id=template.id, amount="250.00",
                name=template.name,
            )
            db.session.flush()

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.flush()

            # Pull every non-deleted transaction in the target period
            # and run them through the balance calculator the same way
            # the grid route does.
            target_txns = (
                db.session.query(Transaction)
                .filter_by(
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).all()
            )
            account = seed_user["account"]
            balances, _stale = balance_calculator.calculate_balances(
                anchor_balance=account.current_anchor_balance,
                anchor_period_id=account.current_anchor_period_id,
                periods=seed_periods,
                transactions=target_txns,
            )

            # Two $250 expenses pull the period balance down by $500
            # vs. the anchor's starting position.
            target_period_balance = balances[seed_periods[1].id]
            anchor_balance = balances[seed_periods[0].id]
            assert anchor_balance - target_period_balance == Decimal(
                "500.00"
            )

    def test_recurrence_engine_does_not_double_generate(
        self, app, db, seed_user, seed_periods
    ):
        """A subsequent recurrence-engine pass must not add a third row.

        The engine treats override siblings as a "this period is
        already handled" signal (recurrence_engine.py line 114), so
        re-running generation after carry-forward must leave the count
        at two.
        """
        with app.app_context():
            from app.enums import RecurrencePatternEnum
            from app.models.recurrence_rule import RecurrenceRule
            from app.models.ref import RecurrencePattern

            pattern = (
                db.session.query(RecurrencePattern)
                .filter_by(name=RecurrencePatternEnum.EVERY_PERIOD.value)
                .one()
            )
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=pattern.id,
                interval_n=1,
                offset_periods=0,
            )
            db.session.add(rule)
            db.session.flush()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Recurring with rule",
                default_amount=Decimal("400.00"),
                recurrence_rule_id=rule.id,
            )
            db.session.add(template)
            db.session.flush()
            db.session.refresh(template)

            # Initial generation populates rule-generated rows for
            # periods 0 and 1.
            recurrence_engine.generate_for_template(
                template, seed_periods[:2], seed_user["scenario"].id,
            )
            db.session.flush()

            # Carry forward the period 0 row into period 1.
            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.flush()

            # Pre-generation snapshot: target has rule-generated +
            # override sibling (= 2 rows).
            pre_count = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).count()
            )
            assert pre_count == 2

            # Re-running the engine must NOT add a third row -- the
            # override sibling signals the period is handled.
            recurrence_engine.generate_for_template(
                template, seed_periods[:2], seed_user["scenario"].id,
            )
            db.session.flush()

            post_count = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).count()
            )
            assert post_count == 2


# ── Override-sibling Carry Forward Tests for Transfers ─────────────


def _create_transfer_template(seed_user, savings_account,
                              name="Recurring Transfer",
                              amount="200.00",
                              category_key="Rent"):
    """Create a TransferTemplate without a recurrence rule.

    Mirrors _create_template but for transfers.  Used by transfer
    override-sibling tests that need a transfer_template_id link.
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings_account.id,
        category_id=seed_user["categories"][category_key].id,
        name=name,
        default_amount=Decimal(amount),
    )
    db.session.add(template)
    db.session.flush()
    return template


class TestCarryForwardOverrideSiblingTransfers:
    """Mirror TestCarryForwardOverrideSibling for transfers, exercising
    the relaxed idx_transfers_template_period_scenario index.
    """

    def test_carries_transfer_into_target_with_existing_rule_generated(
        self, app, db, seed_user, seed_periods
    ):
        """Override-sibling transfer coexists with rule-generated parent."""
        with app.app_context():
            savings = _create_savings(seed_user)
            template = _create_transfer_template(seed_user, savings)
            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )

            # Rule-generated transfer in source period (period 0).
            source_xfer = transfer_service.create_transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                amount=template.default_amount,
                status_id=projected.id,
                category_id=template.category_id,
                name=template.name,
                transfer_template_id=template.id,
            )
            # Rule-generated transfer already in target period (period 1)
            # -- the recurrence engine has already produced this period's
            # instance.
            target_xfer = transfer_service.create_transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                amount=template.default_amount,
                status_id=projected.id,
                category_id=template.category_id,
                name=template.name,
                transfer_template_id=template.id,
            )
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.flush()

            assert count == 1

            db.session.refresh(source_xfer)
            db.session.refresh(target_xfer)
            assert source_xfer.pay_period_id == seed_periods[1].id
            assert target_xfer.pay_period_id == seed_periods[1].id
            assert source_xfer.is_override is True
            assert target_xfer.is_override is False
            assert source_xfer.transfer_template_id == template.id
            assert target_xfer.transfer_template_id == template.id

            # Both transfers' shadow transactions follow them into the
            # target period -- four shadows total (two per transfer).
            shadows = (
                db.session.query(Transaction)
                .filter(
                    Transaction.transfer_id.in_(
                        [source_xfer.id, target_xfer.id]
                    ),
                    Transaction.is_deleted.is_(False),
                ).all()
            )
            assert len(shadows) == 4
            for shadow in shadows:
                assert shadow.pay_period_id == seed_periods[1].id

    def test_transfer_recurrence_does_not_double_generate(
        self, app, db, seed_user, seed_periods
    ):
        """transfer_recurrence skips a period that already has an
        override-sibling transfer.
        """
        with app.app_context():
            from app.enums import RecurrencePatternEnum
            from app.models.recurrence_rule import RecurrenceRule
            from app.models.ref import RecurrencePattern

            savings = _create_savings(seed_user)
            pattern = (
                db.session.query(RecurrencePattern)
                .filter_by(name=RecurrencePatternEnum.EVERY_PERIOD.value)
                .one()
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=pattern.id,
                interval_n=1,
                offset_periods=0,
            )
            db.session.add(rule)
            db.session.flush()

            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                category_id=seed_user["categories"]["Rent"].id,
                name="Recurring Transfer with rule",
                default_amount=Decimal("300.00"),
                recurrence_rule_id=rule.id,
            )
            db.session.add(template)
            db.session.flush()
            db.session.refresh(template)

            # Initial generation: rule-generated transfers in periods 0
            # and 1.
            transfer_recurrence.generate_for_template(
                template, seed_periods[:2], seed_user["scenario"].id,
            )
            db.session.flush()

            # Carry forward period 0 into period 1.  Period 1 now has a
            # rule-generated transfer + an override sibling.
            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.flush()

            pre_count = (
                db.session.query(Transfer)
                .filter_by(
                    transfer_template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).count()
            )
            assert pre_count == 2

            # Re-run transfer recurrence -- must not add a third row.
            transfer_recurrence.generate_for_template(
                template, seed_periods[:2], seed_user["scenario"].id,
            )
            db.session.flush()

            post_count = (
                db.session.query(Transfer)
                .filter_by(
                    transfer_template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).count()
            )
            assert post_count == 2


# ── Envelope Carry Forward (Phase 4) ───────────────────────────────


def _create_envelope_template(
    seed_user, *, name="Spending Money",
    default_amount="100.00", category_key="Groceries",
    txn_type_name="Expense", with_rule=True,
):
    """Create an envelope-tracked TransactionTemplate.

    By default the template has an EVERY_PERIOD recurrence rule so the
    recurrence engine will generate canonical rows in any seed period.
    Tests that exercise the "missing target canonical" branch (where
    the engine has not yet run) skip the rule entirely by passing
    ``with_rule=False`` -- the carry-forward branch must still attempt
    generation and fail loudly when the engine cannot create the row.
    """
    txn_type = (
        db.session.query(TransactionType)
        .filter_by(name=txn_type_name).one()
    )
    rule = None
    if with_rule:
        every_period_pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name=RecurrencePatternEnum.EVERY_PERIOD.value)
            .one()
        )
        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=every_period_pattern.id,
        )
        db.session.add(rule)
        db.session.flush()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"][category_key].id,
        recurrence_rule_id=rule.id if rule else None,
        transaction_type_id=txn_type.id,
        name=name,
        default_amount=Decimal(default_amount),
        is_envelope=True,
    )
    db.session.add(template)
    db.session.flush()
    return template


def _create_envelope_txn(
    seed_user, period, template, *,
    estimated_amount=None, status_name="Projected",
    is_override=False,
):
    """Create a single envelope transaction owned by the template.

    Mirrors the recurrence engine's per-period generation for tests
    that hand-place rows rather than driving the engine.  Defaults to
    the template's default amount and Projected status.
    """
    status = db.session.query(Status).filter_by(name=status_name).one()
    txn = Transaction(
        template_id=template.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=status.id,
        name=template.name,
        category_id=template.category_id,
        transaction_type_id=template.transaction_type_id,
        estimated_amount=Decimal(
            estimated_amount if estimated_amount is not None
            else str(template.default_amount)
        ),
        is_override=is_override,
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def _add_entry(txn, seed_user, amount, *, description="Test purchase",
               is_credit=False):
    """Attach a TransactionEntry to *txn* and flush."""
    from datetime import date as _date  # local import keeps top clean

    entry = TransactionEntry(
        transaction_id=txn.id,
        user_id=seed_user["user"].id,
        amount=Decimal(amount),
        description=description,
        entry_date=_date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


class TestCarryForwardEnvelopePartialSpend:
    """Source spent some -- leftover rolls forward to target.

    The wife's spending money envelope is the canonical example: $65
    of $100 spent, $35 unspent.  Source settles at $65 (Paid), target
    bumps to $135 with is_override=True.  Exactly one row exists per
    (template, period) afterwards so cell == period subtotal ==
    balance projection.
    """

    def test_partial_spend_settles_source_and_bumps_target(
        self, app, db, seed_user, seed_periods,
    ):
        """Source DONE at entries_sum, target +leftover, is_override=True.

        Setup: $100 envelope in source period 0, $65 of debit entries.
        Target period 1 has the rule-generated canonical at $100.

        Expected:
          source.status_id      == DONE
          source.actual_amount  == 65.00
          source.estimated      == 100.00 (untouched)
          source.pay_period_id  == seed_periods[0].id (NOT moved)
          source.is_override    == False (envelope source is settled,
                                         not relocated)
          target.estimated      == 135.00 (100 + 35 leftover)
          target.is_override    == True
          target.pay_period_id  == seed_periods[1].id
          row count in target   == 1 (no sibling created)
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            target = _create_envelope_txn(
                seed_user, seed_periods[1], template,
            )
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            assert count == 1

            db.session.refresh(source)
            db.session.refresh(target)

            done_id = ref_cache.status_id(StatusEnum.DONE)
            assert source.status_id == done_id
            # Worked example: 65 of 100 spent.
            assert source.actual_amount == Decimal("65.00")
            assert source.estimated_amount == Decimal("100.00")
            assert source.pay_period_id == seed_periods[0].id
            assert source.is_override is False

            # Target absorbed the leftover: 100 + (100 - 65) = 135.
            assert target.estimated_amount == Decimal("135.00")
            assert target.is_override is True
            assert target.pay_period_id == seed_periods[1].id

            target_rows = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).all()
            )
            assert len(target_rows) == 1, (
                "Envelope rollover must produce ONE row per "
                "(template, period) -- found "
                f"{len(target_rows)} rows in target."
            )

    def test_partial_spend_with_credit_entries_in_sum(
        self, app, db, seed_user, seed_periods,
    ):
        """Credit entries count toward entries_sum (mirrors helper contract).

        compute_actual_from_entries sums BOTH debit and credit entries
        for analytics correctness.  That is the contract the carry-
        forward branch inherits.

        Setup: $40 debit + $25 credit = $65 against $100 envelope.
        Expected: leftover = 100 - 65 = 35, target bumped by 35.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            target = _create_envelope_txn(
                seed_user, seed_periods[1], template,
            )
            _add_entry(source, seed_user, "40.00",
                       description="Kroger debit")
            _add_entry(source, seed_user, "25.00",
                       description="Amazon credit", is_credit=True)
            db.session.commit()

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            db.session.refresh(source)
            db.session.refresh(target)
            # 40 + 25 = 65, leftover = 100 - 65 = 35.
            assert source.actual_amount == Decimal("65.00")
            assert target.estimated_amount == Decimal("135.00")
            assert target.is_override is True


class TestCarryForwardEnvelopeZeroEntries:
    """Source spent nothing -- full envelope rolls forward."""

    def test_zero_entries_rolls_full_envelope(
        self, app, db, seed_user, seed_periods,
    ):
        """Empty entries: source DONE at $0, target += full estimated.

        Setup: $200 envelope, no entries against source.
        Expected:
          source.status         == DONE
          source.actual_amount  == 0.00 (NOT a fallback to estimated)
          target.estimated      == 200 + 200 = 400.00
          target.is_override    == True
        """
        with app.app_context():
            template = _create_envelope_template(
                seed_user, default_amount="200.00",
            )
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            target = _create_envelope_txn(
                seed_user, seed_periods[1], template,
            )
            db.session.commit()

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            db.session.refresh(source)
            db.session.refresh(target)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            assert source.status_id == done_id
            assert source.actual_amount == Decimal("0.00")
            # 200 + (200 - 0) = 400.
            assert target.estimated_amount == Decimal("400.00")
            assert target.is_override is True


class TestCarryForwardEnvelopeOverspend:
    """Source spent more than estimated -- no rollover, target untouched."""

    def test_overspend_settles_source_without_bumping_target(
        self, app, db, seed_user, seed_periods,
    ):
        """entries_sum > estimated: leftover clamps to 0, target unchanged.

        Setup: $80 + $40 = $120 entries against $100 envelope.
        Expected:
          source.actual_amount  == 120.00 (truth, not clamped)
          target.estimated      == 100.00 (untouched)
          target.is_override    == False (untouched)
        """
        with app.app_context():
            template = _create_envelope_template(
                seed_user, default_amount="100.00",
            )
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            target = _create_envelope_txn(
                seed_user, seed_periods[1], template,
            )
            _add_entry(source, seed_user, "80.00")
            _add_entry(source, seed_user, "40.00",
                       description="Overspend purchase")
            db.session.commit()

            target_estimated_before = target.estimated_amount
            target_is_override_before = target.is_override

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            db.session.refresh(source)
            db.session.refresh(target)
            # 80 + 40 = 120 -- exceeds the 100 estimate.
            assert source.actual_amount == Decimal("120.00")
            # Target is untouched because leftover = max(0, 100-120) = 0.
            assert target.estimated_amount == target_estimated_before
            assert target.is_override == target_is_override_before
            assert target.is_override is False

    def test_exact_spend_settles_source_without_bumping_target(
        self, app, db, seed_user, seed_periods,
    ):
        """entries_sum == estimated: leftover = 0, target unchanged.

        Setup: $50 + $50 = $100 entries against $100 envelope.
        Expected: source DONE at 100, target untouched.
        """
        with app.app_context():
            template = _create_envelope_template(
                seed_user, default_amount="100.00",
            )
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            target = _create_envelope_txn(
                seed_user, seed_periods[1], template,
            )
            _add_entry(source, seed_user, "50.00")
            _add_entry(source, seed_user, "50.00")
            db.session.commit()

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            db.session.refresh(source)
            db.session.refresh(target)
            assert source.actual_amount == Decimal("100.00")
            assert target.estimated_amount == Decimal("100.00")
            assert target.is_override is False


class TestCarryForwardEnvelopeMissingTarget:
    """Target canonical does not yet exist -- engine generates it."""

    def test_engine_creates_canonical_when_missing(
        self, app, db, seed_user, seed_periods,
    ):
        """No target row + active rule: engine creates canonical, branch bumps it.

        Setup: $100 envelope with EVERY_PERIOD rule.  Source row in
        period 0 has a $30 entry.  Period 1 has NO row (recurrence
        engine has not run for period 1 yet).

        Expected: branch calls generate_for_template, engine creates
        a canonical at $100, branch bumps it by 70 -> $170 with
        is_override=True.  The newly-created row's pay_period_id ==
        seed_periods[1].id and template_id == template.id.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            # Sanity: target period has no row before carry-forward.
            pre_count = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                ).count()
            )
            assert pre_count == 0

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            target_rows = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).all()
            )
            # Exactly one row in target after the engine generated +
            # the branch bumped.
            assert len(target_rows) == 1
            target = target_rows[0]
            # 100 (engine-generated canonical) + 70 (leftover) = 170.
            assert target.estimated_amount == Decimal("170.00")
            assert target.is_override is True

    def test_template_inactive_in_target_raises(
        self, app, db, seed_user, seed_periods,
    ):
        """Template without a recurrence rule + missing target = refuse.

        With ``with_rule=False`` the engine immediately returns []
        because ``template.recurrence_rule is None``.  The branch must
        catch the empty return and raise ValidationError so the user
        can resolve manually rather than silently dropping the
        leftover.
        """
        with app.app_context():
            template = _create_envelope_template(
                seed_user, with_rule=False,
            )
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            _add_entry(source, seed_user, "30.00")
            db.session.commit()
            source_id = source.id

            with pytest.raises(ValidationError) as exc_info:
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, seed_periods[1].id,
                    seed_user["user"].id, seed_user["scenario"].id,
                )
            db.session.rollback()

            # Error message names the source and target so the user
            # can identify the row that blocked the batch.
            assert str(source_id) in str(exc_info.value)
            assert str(seed_periods[1].id) in str(exc_info.value)


class TestCarryForwardEnvelopeSettledTarget:
    """Target canonical is finalised -- batch refuses, full rollback."""

    def test_settled_target_raises_and_rolls_back_atomically(
        self, app, db, seed_user, seed_periods,
    ):
        """Target Paid: ValidationError, source NOT settled, no commit.

        Atomicity check: the envelope branch raises BEFORE settling
        source, so a rollback by the route leaves the source unchanged
        (still Projected) and the target untouched (still Paid).

        Setup: $100 envelope, source in period 0 with $40 entries,
        target canonical in period 1 already in Paid status.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            target = _create_envelope_txn(
                seed_user, seed_periods[1], template,
                status_name="Paid",
            )
            target.actual_amount = Decimal("100.00")
            _add_entry(source, seed_user, "40.00")
            db.session.commit()

            source_id = source.id
            target_id = target.id

            with pytest.raises(ValidationError) as exc_info:
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, seed_periods[1].id,
                    seed_user["user"].id, seed_user["scenario"].id,
                )
            db.session.rollback()
            db.session.expire_all()

            # Source must be unchanged: still Projected, no
            # actual_amount, no paid_at.
            source_after = db.session.get(Transaction, source_id)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            assert source_after.status_id == projected_id
            assert source_after.actual_amount is None
            assert source_after.paid_at is None
            assert source_after.estimated_amount == Decimal("100.00")

            # Target must be unchanged: still Paid at 100.
            target_after = db.session.get(Transaction, target_id)
            paid_id = ref_cache.status_id(StatusEnum.DONE)
            assert target_after.status_id == paid_id
            assert target_after.estimated_amount == Decimal("100.00")
            assert target_after.actual_amount == Decimal("100.00")
            assert target_after.is_override is False

            # Error message names the failing row + status for the user.
            assert str(source_id) in str(exc_info.value)
            assert "finalised" in str(exc_info.value).lower()


class TestCarryForwardEnvelopeMultiHop:
    """Chain A -> B -> C: each hop settles + bumps cleanly."""

    def test_two_hop_chain_settles_each_and_bumps_each(
        self, app, db, seed_user, seed_periods,
    ):
        """A->B then B->C: both hops behave independently and correctly.

        Setup: $100 envelope per period 0, 1, 2.  No entries on any
        row.

        First call: source A (period 0, $0 entries) settles at $0,
        target B's canonical bumps to $200 (100 + 100 leftover).
        is_override flips True on B.

        Second call: source = B's canonical (now $200, is_override=True,
        Projected) -- still envelope-tracked by template flag.  $0
        entries.  Settles at $0, target C's canonical bumps by 200 ->
        $300.  is_override flips True on C.

        Net forward cash flow over A+B+C: A excluded (settled), B
        excluded (settled), C reduces balance by $300.  All three
        $100 envelopes accounted for in C.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            row_a = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            row_b = _create_envelope_txn(
                seed_user, seed_periods[1], template,
            )
            row_c = _create_envelope_txn(
                seed_user, seed_periods[2], template,
            )
            db.session.commit()

            # Hop 1: A -> B.
            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            db.session.refresh(row_a)
            db.session.refresh(row_b)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            assert row_a.status_id == done_id
            assert row_a.actual_amount == Decimal("0.00")
            # B got the full $100 leftover from A.
            assert row_b.estimated_amount == Decimal("200.00")
            assert row_b.is_override is True
            assert row_b.status_id == projected_id

            # Hop 2: B (now $200) -> C.
            carry_forward_service.carry_forward_unpaid(
                seed_periods[1].id, seed_periods[2].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            db.session.refresh(row_b)
            db.session.refresh(row_c)
            assert row_b.status_id == done_id
            assert row_b.actual_amount == Decimal("0.00")
            # C absorbs B's full $200.
            assert row_c.estimated_amount == Decimal("300.00")
            assert row_c.is_override is True
            assert row_c.status_id == projected_id

            # Sanity: still exactly one row per period for this template.
            for period in (seed_periods[0], seed_periods[1], seed_periods[2]):
                rows = (
                    db.session.query(Transaction)
                    .filter_by(
                        template_id=template.id,
                        pay_period_id=period.id,
                        scenario_id=seed_user["scenario"].id,
                        is_deleted=False,
                    ).all()
                )
                assert len(rows) == 1, (
                    f"Period {period.id}: expected 1 envelope row, "
                    f"got {len(rows)}."
                )


class TestCarryForwardEnvelopeMultipleSourcesToSameTarget:
    """Two sources from different past periods bump the same target.

    The target canonical was promoted to is_override=True by the
    first carry-forward.  The second carry-forward must find the
    SAME row (despite is_override=True) and re-bump it, NOT create a
    new sibling.  This test demonstrates why the lookup omits the
    is_override filter.
    """

    def test_two_sources_compound_into_single_target_row(
        self, app, db, seed_user, seed_periods,
    ):
        """Carry A->C then B->C: target absorbs both leftovers.

        Setup: $100 envelope.  Sources in periods 0 and 1.  Target in
        period 2 (rule-generated canonical at $100).  No entries on
        either source.

        First call: A -> C.  Target canonical at $100, lookup finds
        is_override=False row, bumps to $200, sets is_override=True.

        Second call: B -> C.  Target now at $200, is_override=True.
        Lookup MUST find this row (not skip it) and bump it again to
        $300.

        Expected: one row in target with estimated=$300, is_override=
        True.  Both sources DONE at $0.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            row_a = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            row_b = _create_envelope_txn(
                seed_user, seed_periods[1], template,
            )
            row_c = _create_envelope_txn(
                seed_user, seed_periods[2], template,
            )
            db.session.commit()

            # Hop 1: A -> C.
            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[2].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()
            db.session.refresh(row_c)
            assert row_c.estimated_amount == Decimal("200.00")
            assert row_c.is_override is True

            # Hop 2: B -> C.  C is now is_override=True; lookup must
            # still find it.
            carry_forward_service.carry_forward_unpaid(
                seed_periods[1].id, seed_periods[2].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            db.session.refresh(row_a)
            db.session.refresh(row_b)
            db.session.refresh(row_c)

            done_id = ref_cache.status_id(StatusEnum.DONE)
            assert row_a.status_id == done_id
            assert row_b.status_id == done_id
            # Two $100 leftovers compounded onto the original $100.
            assert row_c.estimated_amount == Decimal("300.00")
            assert row_c.is_override is True

            # Single row in target -- the second hop did NOT create a
            # sibling.
            target_rows = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[2].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).all()
            )
            assert len(target_rows) == 1


class TestCarryForwardEnvelopeCorruptDoubledRow:
    """Legacy doubled-row pre-fix state: refuse and require manual cleanup."""

    def test_two_non_deleted_rows_in_target_raises(
        self, app, db, seed_user, seed_periods,
    ):
        """Two non-deleted target rows for same envelope: ValidationError.

        Reproduces the production state the user has from pre-Phase-4
        carry-forwards (relaxed unique index allowed is_override=True
        + canonical to coexist).  The branch refuses rather than
        bumping one and ignoring the other; the user must clean up
        manually.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            # Pre-existing doubled-row state in target period 1.
            _create_envelope_txn(
                seed_user, seed_periods[1], template,
                is_override=False,
            )
            _create_envelope_txn(
                seed_user, seed_periods[1], template,
                is_override=True,
            )
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            with pytest.raises(ValidationError) as exc_info:
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, seed_periods[1].id,
                    seed_user["user"].id, seed_user["scenario"].id,
                )
            db.session.rollback()

            assert "duplicate" in str(exc_info.value).lower()
            assert str(seed_periods[1].id) in str(exc_info.value)


class TestCarryForwardEnvelopeMixedBatch:
    """Heterogeneous source period: each row takes the correct branch."""

    def test_mixed_envelope_discrete_transfer_batch(
        self, app, db, seed_user, seed_periods,
    ):
        """One envelope + one discrete + one transfer + one ad-hoc.

        Verifies that each row takes the correct branch and that a
        batch with all four flavors completes without cross-bucket
        interference.

        Setup (period 0):
          * Envelope template ($100), source row, $40 entry.
          * Discrete template ($50), source row.
          * Ad-hoc transaction ($30).
          * Transfer ($75 to savings).

        Target canonicals already exist for both templates in period 1
        (rule-generated rows at default amounts).
        """
        with app.app_context():
            envelope_template = _create_envelope_template(
                seed_user, name="Envelope Spending",
                default_amount="100.00", category_key="Groceries",
            )
            envelope_source = _create_envelope_txn(
                seed_user, seed_periods[0], envelope_template,
            )
            envelope_target = _create_envelope_txn(
                seed_user, seed_periods[1], envelope_template,
            )
            _add_entry(envelope_source, seed_user, "40.00")

            # Discrete template: is_envelope defaults to False.
            discrete_template = _create_template(
                seed_user, name="Recurring Bill",
                amount="50.00", category_key="Rent",
            )
            discrete_source = _create_transaction(
                seed_user, seed_periods, period_index=0,
                template_id=discrete_template.id,
                name=discrete_template.name,
                amount="50.00",
            )

            # Ad-hoc (no template).
            adhoc = _create_transaction(
                seed_user, seed_periods, period_index=0,
                name="Ad-hoc Expense",
                amount="30.00",
            )

            # Transfer (shadow rows in period 0).
            transfer = _create_transfer_in_period(
                seed_user, seed_periods, period_index=0,
            )

            db.session.commit()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            # Count semantics: 1 per envelope + 1 per discrete +
            # 1 per ad-hoc + 1 per transfer.
            assert count == 4

            # Envelope source settled, target bumped.
            db.session.refresh(envelope_source)
            db.session.refresh(envelope_target)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            assert envelope_source.status_id == done_id
            assert envelope_source.actual_amount == Decimal("40.00")
            assert envelope_source.pay_period_id == seed_periods[0].id
            # 100 + (100 - 40) = 160.
            assert envelope_target.estimated_amount == Decimal("160.00")
            assert envelope_target.is_override is True

            # Discrete source moved with is_override=True.
            db.session.refresh(discrete_source)
            assert discrete_source.pay_period_id == seed_periods[1].id
            assert discrete_source.is_override is True
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            assert discrete_source.status_id == projected_id

            # Ad-hoc moved without is_override (no template_id).
            db.session.refresh(adhoc)
            assert adhoc.pay_period_id == seed_periods[1].id
            assert adhoc.is_override is False

            # Transfer parent + both shadows moved, is_override=True.
            db.session.refresh(transfer)
            assert transfer.pay_period_id == seed_periods[1].id
            assert transfer.is_override is True
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=transfer.id
            ).all()
            assert len(shadows) == 2
            for shadow in shadows:
                assert shadow.pay_period_id == seed_periods[1].id

    def test_envelope_failure_rolls_back_other_branches(
        self, app, db, seed_user, seed_periods,
    ):
        """Settled envelope target -- atomic rollback affects every branch.

        Setup: same period 0 mix as the prior test, but envelope
        target in period 1 is in Paid status.  When the envelope
        branch refuses, the discrete moves and the transfer move
        (which would have happened mid-batch in the no_autoflush block
        / shadow loop respectively) must NOT persist.

        The route's rollback restores every prior pending mutation;
        this test verifies the atomicity contract end-to-end.
        """
        with app.app_context():
            envelope_template = _create_envelope_template(
                seed_user, name="Envelope Spending",
                default_amount="100.00", category_key="Groceries",
            )
            envelope_source = _create_envelope_txn(
                seed_user, seed_periods[0], envelope_template,
            )
            envelope_target = _create_envelope_txn(
                seed_user, seed_periods[1], envelope_template,
                status_name="Paid",
            )
            envelope_target.actual_amount = Decimal("100.00")
            _add_entry(envelope_source, seed_user, "40.00")

            discrete_template = _create_template(
                seed_user, name="Recurring Bill",
                amount="50.00", category_key="Rent",
            )
            discrete_source = _create_transaction(
                seed_user, seed_periods, period_index=0,
                template_id=discrete_template.id,
                name=discrete_template.name,
                amount="50.00",
            )

            db.session.commit()

            envelope_source_id = envelope_source.id
            discrete_source_id = discrete_source.id

            with pytest.raises(ValidationError):
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, seed_periods[1].id,
                    seed_user["user"].id, seed_user["scenario"].id,
                )
            db.session.rollback()
            db.session.expire_all()

            # Discrete source must NOT have moved or flipped is_override.
            discrete_after = db.session.get(
                Transaction, discrete_source_id,
            )
            assert discrete_after.pay_period_id == seed_periods[0].id
            assert discrete_after.is_override is False

            # Envelope source must NOT have settled.
            envelope_after = db.session.get(
                Transaction, envelope_source_id,
            )
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            assert envelope_after.status_id == projected_id
            assert envelope_after.actual_amount is None


class TestCarryForwardEnvelopeBalanceInvariant:
    """Cell == period subtotal == balance projection invariant.

    The whole point of Option F: every consumer of the post-carry
    state reads the same number for the envelope.  This test exercises
    the balance calculator on the post-carry state and asserts the
    forward cash flow matches the period subtotal.
    """

    def test_post_carry_balance_matches_subtotal(
        self, app, db, seed_user, seed_periods,
    ):
        """Source settled excludes from balance; target bumped reduces by full new estimate.

        Setup: $100 envelope, $65 entries, target rule-generated.

        Post-carry expected balance trajectory (anchor period 0,
        starting balance $1000 from seed_user):
          period 0: source DONE excluded -> end_balance = 1000
          period 1: target $135 expense -> end_balance = 1000 - 135 = 865

        Period 1 subtotal: $135 (just the bumped canonical).
        Both subtotal and balance reduction agree at $135.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            _create_envelope_txn(
                seed_user, seed_periods[1], template,
            )
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            # Pull every non-deleted transaction across the two
            # periods and run the balance calculator the same way the
            # grid route does.
            txns = (
                db.session.query(Transaction)
                .filter(
                    Transaction.scenario_id == seed_user["scenario"].id,
                    Transaction.is_deleted.is_(False),
                    Transaction.pay_period_id.in_(
                        [seed_periods[0].id, seed_periods[1].id]
                    ),
                ).all()
            )

            account = seed_user["account"]
            balances, _stale = balance_calculator.calculate_balances(
                anchor_balance=account.current_anchor_balance,
                anchor_period_id=account.current_anchor_period_id,
                periods=seed_periods,
                transactions=txns,
            )

            # Anchor balance from seed_user is $1000; settled source
            # is excluded (effective_amount returns 0 for is_settled
            # statuses on the projected-only filter).
            assert balances[seed_periods[0].id] == Decimal("1000.00")
            # Target now $135; balance drops by exactly that amount.
            assert balances[seed_periods[1].id] == Decimal("865.00")
            # Forward cash flow matches the bumped envelope exactly.
            assert (
                balances[seed_periods[0].id]
                - balances[seed_periods[1].id]
            ) == Decimal("135.00")

            # Period subtotal: sum effective_amount for projected rows
            # in period 1.  Same projected-only filter, same answer.
            period_1_txns = [
                t for t in txns
                if t.pay_period_id == seed_periods[1].id
            ]
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            subtotal = sum(
                (t.effective_amount for t in period_1_txns
                 if t.status_id == projected_id),
                Decimal("0"),
            )
            assert subtotal == Decimal("135.00")


class TestCarryForwardEnvelopeIncomeFalse:
    """Income templates may exist with is_envelope=False (Phase 2 default).

    Phase 2 rejects ``is_envelope=True`` on income at the schema layer,
    so income templates always take the discrete branch.  This test
    confirms the carry-forward branching honors that contract.
    """

    def test_income_template_takes_discrete_path(
        self, app, db, seed_user, seed_periods,
    ):
        """Income + is_envelope=False: row moved whole, is_override=True.

        Setup: an income TransactionTemplate with is_envelope=False
        (the only legal income state per Phase 2).  Projected income
        row in period 0.

        Expected: discrete branch fires -- pay_period flips to target,
        is_override=True.  No settle, no entries-sum logic.
        """
        with app.app_context():
            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="Income").one()
            )
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                name="Paycheck",
                default_amount=Decimal("2500.00"),
                is_envelope=False,
            )
            db.session.add(template)
            db.session.flush()

            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            source = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name=template.name,
                category_id=template.category_id,
                transaction_type_id=template.transaction_type_id,
                estimated_amount=Decimal("2500.00"),
            )
            db.session.add(source)
            db.session.commit()
            source_id = source.id

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            assert count == 1
            db.session.refresh(source)
            # Discrete behaviour: row moved, is_override=True, status
            # unchanged (still Projected -- not settled).
            assert source.pay_period_id == seed_periods[1].id
            assert source.is_override is True
            assert source.status_id == ref_cache.status_id(
                StatusEnum.PROJECTED,
            )
            # actual_amount must NOT have been set: discrete branch
            # never calls settle_from_entries.
            assert source.actual_amount is None
            assert source.paid_at is None

            # No new rows generated in target -- the source itself is
            # the row in target now.
            target_rows = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).all()
            )
            assert len(target_rows) == 1
            assert target_rows[0].id == source_id


class TestCarryForwardEnvelopeRecurrenceSkip:
    """Bumped canonical (is_override=True) blocks future recurrence runs."""

    def test_recurrence_engine_does_not_double_generate_after_bump(
        self, app, db, seed_user, seed_periods,
    ):
        """Re-running the engine on a bumped envelope target does not add a sibling.

        Mirrors the existing TestCarryForwardOverrideSibling test for
        the envelope path: after carry-forward bumps target's
        is_override=True, the engine's skip-on-override clause keeps
        the row count at 1.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            _create_envelope_txn(
                seed_user, seed_periods[1], template,
            )
            _add_entry(source, seed_user, "20.00")
            db.session.commit()

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            # Pre-engine: source DONE in period 0 + bumped canonical
            # in period 1 = 2 rows total for the template/scenario.
            pre_count = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).count()
            )
            assert pre_count == 2

            # Run recurrence engine for periods 0 + 1 again -- it must
            # not add a third row.  The DONE source in period 0 has
            # is_immutable status; the bumped canonical in period 1
            # has is_override=True.  Both trigger skip clauses.
            recurrence_engine.generate_for_template(
                template, seed_periods[:2], seed_user["scenario"].id,
            )
            db.session.flush()

            post_count = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).count()
            )
            assert post_count == 2


# ── Carry-Forward Preview (Phase 5) ────────────────────────────────


def _read_only_session_snapshot():
    """Return a coarse fingerprint of the session state.

    Used by preview tests to assert the service made no mutations.
    Captures the count of dirty/new/deleted objects in the session;
    a clean snapshot before AND after calling preview proves the
    function is read-only at the session level.
    """
    return (
        len(db.session.dirty),
        len(db.session.new),
        len(db.session.deleted),
    )


class TestPreviewCarryForwardEmptyAndShortCircuits:
    """Boundary cases: empty period, same-period, missing periods."""

    def test_empty_source_returns_empty_plans(
        self, app, db, seed_user, seed_periods,
    ):
        """No projected rows in source -> preview returns empty plans list.

        The carry-forward UI button is only rendered for periods in
        the past; an empty past period is unusual but legal (e.g. a
        user who manually settled every row).  Preview must not
        crash, must produce a renderable preview, and must mark
        Confirm as inactive (any_blocked is False but plans is empty,
        which the modal template treats as "nothing to do").
        """
        with app.app_context():
            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            assert preview.plans == []
            assert preview.any_blocked is False
            assert preview.envelope_count == 0
            assert preview.discrete_count == 0
            assert preview.transfer_count == 0
            assert preview.blocked_count == 0
            assert preview.source_period.id == seed_periods[0].id
            assert preview.target_period.id == seed_periods[1].id

    def test_same_period_returns_empty_plans(
        self, app, db, seed_user, seed_periods,
    ):
        """source == target short-circuits identically to carry_forward_unpaid.

        The mutating service returns 0 in this case; the preview
        returns an empty plans list so the modal renders cleanly
        rather than blowing up.
        """
        with app.app_context():
            # Add a row to the period to prove the short-circuit
            # ignores rows when source == target.
            _create_transaction(
                seed_user, seed_periods, period_index=0,
                name="Should be ignored on same-period preview",
            )
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[0].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            assert preview.plans == []
            assert preview.any_blocked is False

    def test_missing_source_period_raises_not_found(
        self, app, db, seed_user, seed_periods,
    ):
        """Unknown source_period_id raises NotFoundError (route returns 404)."""
        from app.exceptions import NotFoundError

        with app.app_context():
            with pytest.raises(NotFoundError):
                carry_forward_service.preview_carry_forward(
                    9_999_999, seed_periods[1].id,
                    seed_user["user"].id, seed_user["scenario"].id,
                )

    def test_unowned_source_period_raises_not_found(
        self, app, db, seed_user, seed_second_user, seed_periods,
        seed_second_periods,
    ):
        """Cross-user source period raises NotFoundError (404 at route).

        Defense-in-depth: the route enforces ownership before calling
        the service, but the service repeats the check so a future
        misuse cannot leak data.  Mirrors the security response rule
        in CLAUDE.md.
        """
        from app.exceptions import NotFoundError

        with app.app_context():
            other_period_id = seed_second_periods[0].id
            with pytest.raises(NotFoundError):
                carry_forward_service.preview_carry_forward(
                    other_period_id, seed_periods[1].id,
                    seed_user["user"].id, seed_user["scenario"].id,
                )


class TestPreviewCarryForwardEnvelopePlans:
    """Each envelope-row outcome in carry_forward_unpaid has a matching plan."""

    def test_partial_spend_plan_actionable_with_target_amounts(
        self, app, db, seed_user, seed_periods,
    ):
        """Mirror of TestCarryForwardEnvelopePartialSpend, no mutations.

        Worked example: $100 envelope, $65 entries, target rule-
        generated.  Preview reports:
          plan.kind                     == "envelope"
          plan.blocked                  == False
          plan.entries_sum              == 65
          plan.leftover                 == 35
          plan.target_estimated_before  == 100
          plan.target_estimated_after   == 135
          plan.target_will_be_generated == False
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            _create_envelope_txn(seed_user, seed_periods[1], template)
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            before = _read_only_session_snapshot()
            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            after = _read_only_session_snapshot()
            assert before == after, (
                "preview_carry_forward must not mutate the session."
            )

            assert len(preview.plans) == 1
            plan = preview.plans[0]
            assert plan.kind == carry_forward_service.PLAN_KIND_ENVELOPE
            assert plan.blocked is False
            assert plan.block_reason_code is None
            assert plan.entries_sum == Decimal("65.00")
            assert plan.leftover == Decimal("35.00")
            assert plan.target_estimated_before == Decimal("100.00")
            assert plan.target_estimated_after == Decimal("135.00")
            assert plan.target_will_be_generated is False

            # Counts.
            assert preview.any_blocked is False
            assert preview.envelope_count == 1
            assert preview.discrete_count == 0
            assert preview.transfer_count == 0

    def test_zero_entries_plan_rolls_full_estimate(
        self, app, db, seed_user, seed_periods,
    ):
        """Empty entries: plan.entries_sum == 0, leftover == estimated."""
        with app.app_context():
            template = _create_envelope_template(
                seed_user, default_amount="200.00",
            )
            _create_envelope_txn(seed_user, seed_periods[0], template)
            _create_envelope_txn(seed_user, seed_periods[1], template)
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            plan = preview.plans[0]
            assert plan.entries_sum == Decimal("0.00")
            assert plan.leftover == Decimal("200.00")
            # 200 + 200 = 400.
            assert plan.target_estimated_after == Decimal("400.00")
            assert plan.blocked is False

    def test_overspend_plan_zero_leftover_no_target_amounts(
        self, app, db, seed_user, seed_periods,
    ):
        """Overspend: leftover == 0, target_estimated_* are None.

        With no rollover the target row is irrelevant -- the modal
        does not render before/after numbers, so the service
        deliberately leaves them None.
        """
        with app.app_context():
            template = _create_envelope_template(
                seed_user, default_amount="100.00",
            )
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            _create_envelope_txn(seed_user, seed_periods[1], template)
            _add_entry(source, seed_user, "80.00")
            _add_entry(source, seed_user, "40.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            plan = preview.plans[0]
            assert plan.entries_sum == Decimal("120.00")
            assert plan.leftover == Decimal("0.00")
            assert plan.target_estimated_before is None
            assert plan.target_estimated_after is None
            assert plan.target_will_be_generated is False
            assert plan.blocked is False

    def test_missing_target_with_active_rule_predicts_generation(
        self, app, db, seed_user, seed_periods,
    ):
        """No target row + active rule: plan.target_will_be_generated == True.

        Mirrors TestCarryForwardEnvelopeMissingTarget, with no
        mutations.  Predicts the same numbers the engine would
        produce: canonical at template default + leftover.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            # Sanity: target period truly empty.
            target_count = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                ).count()
            )
            assert target_count == 0

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            plan = preview.plans[0]
            assert plan.blocked is False
            assert plan.target_will_be_generated is True
            assert plan.target_estimated_before == Decimal("100.00")
            # Leftover: 100 estimated - 30 entries = 70.
            # Target after: 100 (engine default) + 70 = 170.
            assert plan.entries_sum == Decimal("30.00")
            assert plan.leftover == Decimal("70.00")
            assert plan.target_estimated_after == Decimal("170.00")

            # Confirm the engine still hasn't run -- preview must
            # not actually generate the canonical.
            target_count_after = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                ).count()
            )
            assert target_count_after == 0


class TestPreviewCarryForwardEnvelopeBlocked:
    """Each ValidationError raised by carry_forward_unpaid surfaces as a blocked plan."""

    def test_settled_target_marked_blocked_with_finalised_code(
        self, app, db, seed_user, seed_periods,
    ):
        """Target Paid -> plan.blocked, code BLOCK_TARGET_FINALISED.

        Mirrors TestCarryForwardEnvelopeSettledTarget but read-only.
        The block_reason names the source row and target period so
        the modal can render an actionable message.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            target = _create_envelope_txn(
                seed_user, seed_periods[1], template,
                status_name="Paid",
            )
            target.actual_amount = Decimal("100.00")
            _add_entry(source, seed_user, "40.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            assert preview.any_blocked is True
            assert preview.blocked_count == 1
            assert preview.envelope_count == 0  # blocked excluded

            plan = preview.plans[0]
            assert plan.blocked is True
            assert plan.block_reason_code == (
                carry_forward_service.BLOCK_TARGET_FINALISED
            )
            # block_reason mentions "Paid" so the user knows which
            # status to revert.
            assert "paid" in plan.block_reason.lower()

    def test_template_inactive_marked_blocked(
        self, app, db, seed_user, seed_periods,
    ):
        """No target + no rule: BLOCK_TEMPLATE_INACTIVE."""
        with app.app_context():
            template = _create_envelope_template(
                seed_user, with_rule=False,
            )
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            plan = preview.plans[0]
            assert plan.blocked is True
            assert plan.block_reason_code == (
                carry_forward_service.BLOCK_TEMPLATE_INACTIVE
            )

    def test_duplicate_targets_marked_blocked(
        self, app, db, seed_user, seed_periods,
    ):
        """Two non-deleted target rows -> BLOCK_DUPLICATE_TARGETS."""
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            _create_envelope_txn(
                seed_user, seed_periods[1], template,
                is_override=False,
            )
            _create_envelope_txn(
                seed_user, seed_periods[1], template,
                is_override=True,
            )
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            plan = preview.plans[0]
            assert plan.blocked is True
            assert plan.block_reason_code == (
                carry_forward_service.BLOCK_DUPLICATE_TARGETS
            )
            assert "2" in plan.block_reason  # the row count is named

    def test_only_soft_deleted_target_marked_blocked(
        self, app, db, seed_user, seed_periods,
    ):
        """Only soft-deleted rows in target -> BLOCK_TARGET_SOFT_DELETED.

        The mutating path raises BLOCK_TEMPLATE_INACTIVE for this case
        (because generate_for_template returns [] when the engine
        sees soft-deleted rows).  The preview distinguishes the two
        cases for the user's benefit -- the soft-deleted message
        gives a more actionable hint ("restore or hard-delete first").
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            target = _create_envelope_txn(
                seed_user, seed_periods[1], template,
            )
            target.is_deleted = True
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            plan = preview.plans[0]
            assert plan.blocked is True
            assert plan.block_reason_code == (
                carry_forward_service.BLOCK_TARGET_SOFT_DELETED
            )

    def test_blocked_plans_excluded_from_actionable_count(
        self, app, db, seed_user, seed_periods,
    ):
        """Blocked envelope plans do not count toward envelope_count.

        envelope_count names ACTIONABLE plans for the modal's summary
        line -- the user wants to know "how many will run if I
        confirm."  Blocked plans go in blocked_count instead.
        """
        with app.app_context():
            # One actionable + one blocked (settled target).
            template_a = _create_envelope_template(
                seed_user, name="Envelope A",
                category_key="Groceries",
            )
            source_a = _create_envelope_txn(
                seed_user, seed_periods[0], template_a,
            )
            _create_envelope_txn(seed_user, seed_periods[1], template_a)
            _add_entry(source_a, seed_user, "20.00")

            template_b = _create_envelope_template(
                seed_user, name="Envelope B",
                category_key="Rent",
            )
            source_b = _create_envelope_txn(
                seed_user, seed_periods[0], template_b,
            )
            blocked_target = _create_envelope_txn(
                seed_user, seed_periods[1], template_b,
                status_name="Paid",
            )
            blocked_target.actual_amount = Decimal("100.00")
            _add_entry(source_b, seed_user, "50.00")

            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            assert len(preview.plans) == 2
            assert preview.any_blocked is True
            assert preview.blocked_count == 1
            assert preview.envelope_count == 1


class TestPreviewCarryForwardDiscreteAndTransfer:
    """Discrete and transfer plans are always actionable."""

    def test_discrete_plan_is_actionable(
        self, app, db, seed_user, seed_periods,
    ):
        """Discrete row -> single actionable plan."""
        with app.app_context():
            template = _create_template(seed_user, name="Rent",
                                        amount="1200.00",
                                        category_key="Rent")
            _create_transaction(
                seed_user, seed_periods, period_index=0,
                template_id=template.id, name=template.name,
                amount="1200.00",
            )
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            assert len(preview.plans) == 1
            plan = preview.plans[0]
            assert plan.kind == carry_forward_service.PLAN_KIND_DISCRETE
            assert plan.blocked is False
            assert plan.entries_sum is None
            assert plan.leftover is None
            assert preview.discrete_count == 1
            assert preview.envelope_count == 0
            assert preview.transfer_count == 0

    def test_transfer_plan_dedupes_shadows(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer's two shadows produce ONE transfer plan, not two.

        Mirrors the de-duplication carry_forward_unpaid does in the
        shadow loop.
        """
        with app.app_context():
            _create_transfer_in_period(seed_user, seed_periods, 0)
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            assert len(preview.plans) == 1
            plan = preview.plans[0]
            assert plan.kind == carry_forward_service.PLAN_KIND_TRANSFER
            assert plan.blocked is False
            assert preview.transfer_count == 1


class TestPreviewCarryForwardOrdering:
    """Plans are ordered envelope -> discrete -> transfer for UX clarity."""

    def test_mixed_batch_orders_envelope_first_transfer_last(
        self, app, db, seed_user, seed_periods,
    ):
        """Plans appear envelope first (can block), then discrete, then transfer.

        The modal's UX places blocking-capable rows at the top so the
        user sees the action items before the routine moves.  Same
        ordering as the mutating service's loops.
        """
        with app.app_context():
            envelope_t = _create_envelope_template(
                seed_user, category_key="Groceries",
            )
            envelope_source = _create_envelope_txn(
                seed_user, seed_periods[0], envelope_t,
            )
            _create_envelope_txn(seed_user, seed_periods[1], envelope_t)
            _add_entry(envelope_source, seed_user, "10.00")

            discrete_t = _create_template(
                seed_user, name="Rent", amount="1200.00",
                category_key="Rent",
            )
            _create_transaction(
                seed_user, seed_periods, period_index=0,
                template_id=discrete_t.id, name=discrete_t.name,
                amount="1200.00",
            )

            _create_transfer_in_period(seed_user, seed_periods, 0)

            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )

            assert len(preview.plans) == 3
            assert (
                preview.plans[0].kind
                == carry_forward_service.PLAN_KIND_ENVELOPE
            )
            assert (
                preview.plans[1].kind
                == carry_forward_service.PLAN_KIND_DISCRETE
            )
            assert (
                preview.plans[2].kind
                == carry_forward_service.PLAN_KIND_TRANSFER
            )


class TestPreviewCarryForwardParityWithMutating:
    """Preview agrees with carry_forward_unpaid on the same input.

    This is the load-bearing invariant: any case that would
    successfully execute via carry_forward_unpaid produces a non-
    blocked preview, and any case that would raise ValidationError
    produces a blocked preview with the matching reason code.

    These tests are paired: each scenario runs the preview, then
    runs the mutating call on the same fixture state and confirms
    the prediction.  Without this parity the modal can't be trusted
    to reflect the actual carry-forward behaviour.
    """

    def test_actionable_preview_matches_successful_execution(
        self, app, db, seed_user, seed_periods,
    ):
        """Preview not-blocked -> mutating call succeeds + numbers match.

        Strong parity check: preview's predicted ``target_estimated_after``
        equals the actual target row's ``estimated_amount`` after the
        mutating call.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            target = _create_envelope_txn(
                seed_user, seed_periods[1], template,
            )
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            assert preview.any_blocked is False
            predicted_after = preview.plans[0].target_estimated_after

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            assert count == 1
            db.session.refresh(target)
            assert target.estimated_amount == predicted_after, (
                "Preview's predicted target estimate must match what "
                "carry_forward_unpaid actually wrote."
            )

    def test_blocked_preview_matches_validation_error_on_execution(
        self, app, db, seed_user, seed_periods,
    ):
        """Preview blocked -> mutating call raises ValidationError.

        For each block reason, the mutating call must refuse with the
        same condition the preview surfaced.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            target = _create_envelope_txn(
                seed_user, seed_periods[1], template,
                status_name="Paid",
            )
            target.actual_amount = Decimal("100.00")
            _add_entry(source, seed_user, "40.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            assert preview.any_blocked is True
            assert preview.plans[0].block_reason_code == (
                carry_forward_service.BLOCK_TARGET_FINALISED
            )

            with pytest.raises(ValidationError):
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, seed_periods[1].id,
                    seed_user["user"].id, seed_user["scenario"].id,
                )
            db.session.rollback()

    def test_predicted_target_after_matches_after_engine_generation(
        self, app, db, seed_user, seed_periods,
    ):
        """When target is missing and engine generates: prediction matches.

        Stronger than the prior parity test because it covers the
        will-be-generated branch -- the preview's
        ``target_estimated_after`` is computed from
        ``template.default_amount`` and must agree with the engine's
        actual generation (which uses the same value for non-salary-
        linked templates -- the only kind that can be is_envelope).
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = _create_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            _add_entry(source, seed_user, "20.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            predicted_after = preview.plans[0].target_estimated_after
            assert preview.plans[0].target_will_be_generated is True

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            target = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).one()
            )
            assert target.estimated_amount == predicted_after
