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
from app.models.ref import Status, TransactionType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.services import carry_forward_service


def _create_transaction(seed_user, seed_periods, period_index=0,
                        status_name="projected", template_id=None,
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
                seed_user, seed_periods, status_name="settled",
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
            statuses = ["projected", "done", "received", "credit", "cancelled", "settled"]
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
                status_name="projected",
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
            assert target_txns[0].name == "Status-projected"

            # Source period retains 6 transactions (5 non-projected + 1 deleted).
            source_txns = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods[0].id)
                .all()
            )
            assert len(source_txns) == 6

            # Verify each non-moved transaction is still in the source.
            for status_name in ["done", "received", "credit", "cancelled", "settled"]:
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

            status = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="expense"
            ).one()

            # Baseline projected transaction.
            baseline_txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=baseline_scenario.id,
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
