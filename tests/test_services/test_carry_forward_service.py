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
"""

from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType, Status, TransactionType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import carry_forward_service, transfer_service


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
    expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

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
                name="expense"
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
    savings_type = db.session.query(AccountType).filter_by(name="savings").one()
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
                name="savings"
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
