"""Plan step ``balance:X-bi-6-4c-4``: the entry doors ask the MOVEMENT, not its row.

``X-bi-6-4d`` re-parents a transfer's covering movements onto the transfer
(ruling **R-BAL88**), after which such a movement has no row at all.  The
purchase edit and delete doors read three things off the parent ROW that a
movement can answer for itself or through the one resolution of its parent
(``transfer_legs.movement_parent``), and each is moved here while the mirror
still holds, so 6-4d finds nothing to move in them:

* **ownership** reads the movement's own ``owner_id`` -- one value with the
  row's ``user_id`` by key (``fk_transaction_entries_owner_transaction``), so
  the case PLANTS the two apart around the key to show which one the door
  reads;
* **the payment-record refusal comes FIRST** in the edit door, as it always
  has in the delete door (ruling **R-BAL160**, a declared change): a crafted
  request re-pricing a Paid row's payment record was answered by the
  settled-row rule, which reads the row;
* **that refusal names the movement's parent** through ``movement_parent``,
  so a transfer's payment is named by its LEG's label -- the grid's, from the
  endpoints' current names -- not by the shadow row's stored copy (ruling
  **R-BAL160**).
"""

from decimal import Decimal

import pytest

from app.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service, transaction_service
from tests._test_helpers import (
    create_account_of_type,
    create_settled_transfer,
    generate_row_of,
    make_expense_template,
    typed,
)


def _a_settled_bill(seed_user, period):
    """Settle a $148.32 Electric bill through the verb; return it and its payment.

    Returns:
        ``(row, covering_movement)``.
    """
    row = generate_row_of(
        make_expense_template(
            db.session, seed_user, amount="148.32", name="Electric",
            category_key="Rent",
        ),
        period,
    )
    transaction_service.settle_transaction(row)
    db.session.commit()
    (movement,) = row.covering_movements
    return row, movement


class TestOwnershipIsTheMovementsOwn:
    """Both doors read ``TransactionEntry.owner_id``, not the parent row's owner."""

    def _plant_another_owner(self, entry_id, owner_id):
        """Point the movement's owner column away from its row's, around the key.

        ``fk_transaction_entries_owner_transaction`` holds the two equal on
        every write, so the state is planted with the referential triggers
        off -- the one way to show which column a door keys on.
        """
        db.session.execute(db.text(
            "SET session_replication_role = 'replica'"
        ))
        try:
            db.session.execute(db.text(
                "UPDATE budget.transaction_entries SET owner_id = :owner "
                "WHERE id = :id"
            ), {"owner": owner_id, "id": entry_id})
            db.session.commit()
        finally:
            db.session.execute(db.text(
                "SET session_replication_role = 'origin'"
            ))
        db.session.expire_all()

    def test_the_edit_door_answers_the_movements_owner(
        self, app, seed_user, seed_second_user, seed_periods,
    ):
        """The row's owner gets the 404 once the movement names someone else."""
        with app.app_context():
            _row, movement = _a_settled_bill(seed_user, seed_periods[0])
            movement_id = movement.id
            with pytest.raises(ValidationError):
                # The control: the row's owner reaches the refusals.
                entry_service.update_entry(
                    movement_id, seed_user["user"].id, description="Power",
                )
            db.session.rollback()

            self._plant_another_owner(movement_id, seed_second_user["user"].id)

            with pytest.raises(NotFoundError):
                entry_service.update_entry(
                    movement_id, seed_user["user"].id, description="Power",
                )

    def test_the_delete_door_answers_the_movements_owner(
        self, app, seed_user, seed_second_user, seed_periods,
    ):
        """The same plant, the delete door."""
        with app.app_context():
            _row, movement = _a_settled_bill(seed_user, seed_periods[0])
            movement_id = movement.id
            with pytest.raises(ValidationError):
                entry_service.delete_entry(movement_id, seed_user["user"].id)
            db.session.rollback()

            self._plant_another_owner(movement_id, seed_second_user["user"].id)

            with pytest.raises(NotFoundError):
                entry_service.delete_entry(movement_id, seed_user["user"].id)
            assert db.session.get(TransactionEntry, movement_id) is not None


class TestAPaymentRecordIsRefusedFirst:
    """The edit door refuses a payment record before it weighs the settled row."""

    def test_repricing_a_paid_rows_payment_names_the_record_not_the_row(
        self, app, seed_user, seed_periods,
    ):
        """A crafted $1.00 figure for a Paid Electric bill's payment.

        Both refusals would refuse it; the one that answers says whose act it
        is.  Until plan step ``balance:X-bi-6-4c-4`` the settled-row rule
        answered first: "Electric has settled; its purchases are closed".
        """
        with app.app_context():
            _row, movement = _a_settled_bill(seed_user, seed_periods[0])

            with pytest.raises(ValidationError) as refused:
                entry_service.update_entry(
                    movement.id, seed_user["user"].id,
                    figure=typed(Decimal("1.00")),
                )

            message = str(refused.value)
            assert message.startswith("This is the payment record of Electric,")
            assert "has settled; its purchases are closed" not in message
            db.session.rollback()
            assert db.session.get(
                TransactionEntry, movement.id,
            ).amount == Decimal("148.32")


class TestATransfersPaymentIsNamedByItsLeg:
    """The refusal names a transfer's payment by the leg label the grid shows."""

    @pytest.mark.parametrize("door", ["update", "delete"])
    def test_the_label_is_the_legs_not_the_shadows_stored_copy(
        self, app, seed_user, seed_periods, door,
    ):
        """A Paid $250.00 transfer checking -> Rainy Day, its shadow's name gone stale.

        The shadow's stored name is re-written around the service, the state
        a renamed account leaves (the shadow keeps the name it was written
        with; the leg's label reads the endpoints' current names).  The
        from-side payment is then named "Transfer to Rainy Day", the label
        ``transfer_legs.leg_label`` composes.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy Day",
            )
            db.session.commit()
            transfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("250.00"),
            )
            db.session.commit()
            db.session.execute(db.text(
                "UPDATE budget.transactions SET name = 'Stale Shadow Name' "
                "WHERE transfer_id = :id"
            ), {"id": transfer.id})
            db.session.commit()
            db.session.expire_all()
            expense_shadow = db.session.query(Transaction).filter_by(
                transfer_id=transfer.id, account_id=seed_user["account"].id,
            ).one()
            (movement,) = expense_shadow.covering_movements

            with pytest.raises(ValidationError) as refused:
                if door == "update":
                    entry_service.update_entry(
                        movement.id, seed_user["user"].id, description="x",
                    )
                else:
                    entry_service.delete_entry(movement.id, seed_user["user"].id)

            message = str(refused.value)
            assert message.startswith(
                "This is the payment record of Transfer to Rainy Day,"
            ), message
            assert "Stale Shadow Name" not in message
