"""
Shekel Budget App -- Transfer Service Tests

Comprehensive tests for create_transfer, update_transfer, and
delete_transfer.  Covers all five core invariants, validation rules,
edge cases, and cross-user isolation.
"""

from decimal import Decimal

import pytest

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.ref import AccountType, Status, TransactionType
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import transfer_service
from app.exceptions import NotFoundError, ValidationError


@pytest.fixture()
def transfer_data(app, db, seed_full_user_data):
    """Provide everything the transfer service needs for creation tests.

    Adds the default Transfers: Incoming and Transfers: Outgoing
    categories (which the conftest seed_user fixture does not include).

    Returns:
        dict with keys from seed_full_user_data plus:
        projected_status, incoming_cat, outgoing_cat.
    """
    data = seed_full_user_data
    user = data["user"]

    projected = db.session.query(Status).filter_by(name="Projected").one()

    # Add the default transfer categories the service needs.
    incoming_cat = Category(
        user_id=user.id,
        group_name="Transfers",
        item_name="Incoming",
        sort_order=90,
    )
    outgoing_cat = Category(
        user_id=user.id,
        group_name="Transfers",
        item_name="Outgoing",
        sort_order=91,
    )
    db.session.add_all([incoming_cat, outgoing_cat])
    db.session.commit()

    return {
        **data,
        "projected_status": projected,
        "incoming_cat": incoming_cat,
        "outgoing_cat": outgoing_cat,
    }


def _create_basic_transfer(td):
    """Helper: create a transfer using the standard test data."""
    return transfer_service.create_transfer(
        user_id=td["user"].id,
        from_account_id=td["account"].id,
        to_account_id=td["savings_account"].id,
        pay_period_id=td["periods"][0].id,
        scenario_id=td["scenario"].id,
        amount=Decimal("250.00"),
        status_id=td["projected_status"].id,
        category_id=td["categories"]["Rent"].id,
    )


# ── Creation Tests ─────────────────────────────────────────────────


class TestCreateTransfer:
    """Tests for transfer_service.create_transfer."""

    def test_produces_two_shadows(self, app, db, transfer_data):
        """create_transfer creates exactly 2 shadows with correct fields."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            db.session.flush()

            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2

            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()

            types = {s.transaction_type_id for s in shadows}
            assert types == {expense_type.id, income_type.id}

            for s in shadows:
                assert s.estimated_amount == Decimal("250.00")
                assert s.status_id == td["projected_status"].id
                assert s.pay_period_id == td["periods"][0].id
                assert s.scenario_id == td["scenario"].id
                assert s.template_id is None
                assert s.is_override is False
                assert s.is_deleted is False
                assert s.actual_amount is None

            expense = [s for s in shadows if s.transaction_type_id == expense_type.id][0]
            income = [s for s in shadows if s.transaction_type_id == income_type.id][0]
            assert expense.account_id == td["account"].id
            assert income.account_id == td["savings_account"].id

    def test_shadow_names(self, app, db, transfer_data):
        """Shadow names reference the correct account names."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()

            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            expense = [s for s in shadows if s.transaction_type_id == expense_type.id][0]
            income = [s for s in shadows if s.transaction_type_id == income_type.id][0]

            assert td["savings_account"].name in expense.name
            assert td["account"].name in income.name

    def test_with_category(self, app, db, transfer_data):
        """Both shadows use the user-selected category when provided."""
        with app.app_context():
            td = transfer_data
            rent_cat = td["categories"]["Rent"]

            xfer = transfer_service.create_transfer(
                user_id=td["user"].id,
                from_account_id=td["account"].id,
                to_account_id=td["savings_account"].id,
                pay_period_id=td["periods"][0].id,
                scenario_id=td["scenario"].id,
                amount=Decimal("500.00"),
                status_id=td["projected_status"].id,
                category_id=rent_cat.id,
            )

            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            expense = [s for s in shadows if s.transaction_type_id == expense_type.id][0]
            income = [s for s in shadows if s.transaction_type_id == income_type.id][0]

            assert expense.category_id == rent_cat.id
            assert income.category_id == rent_cat.id

    def test_with_template_id(self, app, db, transfer_data):
        """Template-linked transfer has template_id; shadows have template_id=None."""
        with app.app_context():
            td = transfer_data
            xfer = transfer_service.create_transfer(
                user_id=td["user"].id,
                from_account_id=td["account"].id,
                to_account_id=td["savings_account"].id,
                pay_period_id=td["periods"][0].id,
                scenario_id=td["scenario"].id,
                amount=Decimal("200.00"),
                status_id=td["projected_status"].id,
                category_id=td["categories"]["Rent"].id,
                transfer_template_id=td["transfer_template"].id,
            )

            assert xfer.transfer_template_id == td["transfer_template"].id
            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            for s in shadows:
                assert s.template_id is None

    def test_with_custom_name(self, app, db, transfer_data):
        """Custom name sets transfer name; shadows still use derived names."""
        with app.app_context():
            td = transfer_data
            xfer = transfer_service.create_transfer(
                user_id=td["user"].id,
                from_account_id=td["account"].id,
                to_account_id=td["savings_account"].id,
                pay_period_id=td["periods"][0].id,
                scenario_id=td["scenario"].id,
                amount=Decimal("300.00"),
                status_id=td["projected_status"].id,
                category_id=td["categories"]["Rent"].id,
                name="Mortgage Payment",
            )

            assert xfer.name == "Mortgage Payment"
            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            for s in shadows:
                assert "Transfer" in s.name

    def test_returns_transfer_object(self, app, db, transfer_data):
        """create_transfer returns a Transfer with a valid ID."""
        with app.app_context():
            xfer = _create_basic_transfer(transfer_data)
            assert isinstance(xfer, Transfer)
            assert xfer.id is not None

    def test_default_name_generated(self, app, db, transfer_data):
        """Without a name, transfer gets 'from_account to to_account'."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            assert td["account"].name in xfer.name
            assert td["savings_account"].name in xfer.name

# ── Validation Tests ───────────────────────────────────────────────


class TestCreateTransferValidation:
    """Tests for create_transfer input validation."""

    def test_zero_amount_rejected(self, app, db, transfer_data):
        """Zero amount raises ValidationError."""
        with app.app_context():
            td = transfer_data
            with pytest.raises(ValidationError, match="positive"):
                transfer_service.create_transfer(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount=Decimal("0"),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                )

    def test_negative_amount_rejected(self, app, db, transfer_data):
        """Negative amount raises ValidationError."""
        with app.app_context():
            td = transfer_data
            with pytest.raises(ValidationError, match="positive"):
                transfer_service.create_transfer(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount=Decimal("-100"),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                )

    def test_same_account_rejected(self, app, db, transfer_data):
        """Same from and to account raises ValidationError."""
        with app.app_context():
            td = transfer_data
            with pytest.raises(ValidationError, match="different"):
                transfer_service.create_transfer(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount=Decimal("100"),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                )

    def test_wrong_user_account_rejected(self, app, db, transfer_data, second_user):
        """Account belonging to another user raises NotFoundError."""
        with app.app_context():
            td = transfer_data
            with pytest.raises(NotFoundError):
                transfer_service.create_transfer(
                    user_id=td["user"].id,
                    from_account_id=second_user["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount=Decimal("100"),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                )

    def test_nonexistent_account_rejected(self, app, db, transfer_data):
        """Non-existent account ID raises NotFoundError."""
        with app.app_context():
            td = transfer_data
            with pytest.raises(NotFoundError):
                transfer_service.create_transfer(
                    user_id=td["user"].id,
                    from_account_id=99999,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount=Decimal("100"),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                )

    def test_wrong_user_period_rejected(self, app, db, transfer_data, second_user):
        """Period belonging to another user raises NotFoundError."""
        with app.app_context():
            td = transfer_data
            # Create a period for the second user.
            from app.services import pay_period_service
            from datetime import date
            other_periods = pay_period_service.generate_pay_periods(
                user_id=second_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=2,
                cadence_days=14,
            )
            db.session.flush()

            with pytest.raises(NotFoundError):
                transfer_service.create_transfer(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=other_periods[0].id,
                    scenario_id=td["scenario"].id,
                    amount=Decimal("100"),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                )

    def test_wrong_user_category_rejected(self, app, db, transfer_data, second_user):
        """Category belonging to another user raises NotFoundError."""
        with app.app_context():
            td = transfer_data
            other_cat = second_user["categories"]["Rent"]

            with pytest.raises(NotFoundError):
                transfer_service.create_transfer(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount=Decimal("100"),
                    status_id=td["projected_status"].id,
                    category_id=other_cat.id,
                )

    def test_invalid_amount_string_rejected(self, app, db, transfer_data):
        """Non-numeric amount raises ValidationError."""
        with app.app_context():
            td = transfer_data
            with pytest.raises(ValidationError, match="Invalid amount"):
                transfer_service.create_transfer(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount="not-a-number",
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                )


# ── Update Tests ───────────────────────────────────────────────────


class TestUpdateTransfer:
    """Tests for transfer_service.update_transfer."""

    def test_amount_syncs_shadows(self, app, db, transfer_data):
        """Updating amount propagates to both shadows' estimated_amount."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, amount=Decimal("400.00")
            )

            assert xfer.amount == Decimal("400.00")
            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            for s in shadows:
                assert s.estimated_amount == Decimal("400.00")

    def test_status_syncs_shadows(self, app, db, transfer_data):
        """Updating status propagates to both shadows."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            done_status = db.session.query(Status).filter_by(name="Paid").one()
            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_status.id
            )

            assert xfer.status_id == done_status.id
            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            for s in shadows:
                assert s.status_id == done_status.id

    def test_period_syncs_shadows(self, app, db, transfer_data):
        """Updating period propagates to both shadows."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            new_period = td["periods"][2]

            transfer_service.update_transfer(
                xfer.id, td["user"].id, pay_period_id=new_period.id
            )

            assert xfer.pay_period_id == new_period.id
            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            for s in shadows:
                assert s.pay_period_id == new_period.id

    def test_category_updates_both_shadows(self, app, db, transfer_data):
        """Category update propagates to both expense and income shadows."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            rent_cat = td["categories"]["Rent"]

            transfer_service.update_transfer(
                xfer.id, td["user"].id, category_id=rent_cat.id
            )

            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            expense = [s for s in shadows if s.transaction_type_id == expense_type.id][0]
            income = [s for s in shadows if s.transaction_type_id == income_type.id][0]

            assert expense.category_id == rent_cat.id
            assert income.category_id == rent_cat.id

    def test_notes_does_not_touch_shadows(self, app, db, transfer_data):
        """Notes update changes only the transfer, not shadows."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            # Record shadow state before.
            shadows_before = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            amounts_before = {s.id: s.estimated_amount for s in shadows_before}

            transfer_service.update_transfer(
                xfer.id, td["user"].id, notes="Updated notes"
            )

            assert xfer.notes == "Updated notes"
            shadows_after = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            for s in shadows_after:
                assert s.estimated_amount == amounts_before[s.id]

    def test_actual_amount_syncs_shadows(self, app, db, transfer_data):
        """actual_amount update propagates to both shadows (Transfer has no actual_amount column)."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, actual_amount=Decimal("245.00")
            )

            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            for s in shadows:
                assert s.actual_amount == Decimal("245.00")

            # Transfer model has no actual_amount column.
            assert not hasattr(xfer, "actual_amount") or getattr(xfer, "actual_amount", None) is None

    def test_is_override_syncs_shadows(self, app, db, transfer_data):
        """is_override update propagates to transfer and both shadows."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, is_override=True
            )

            assert xfer.is_override is True
            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            for s in shadows:
                assert s.is_override is True

    def test_wrong_user_rejected(self, app, db, transfer_data, second_user):
        """Update by non-owner raises NotFoundError."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            with pytest.raises(NotFoundError):
                transfer_service.update_transfer(
                    xfer.id, second_user["user"].id, amount=Decimal("100")
                )

    def test_nonexistent_rejected(self, app, db, transfer_data):
        """Update of non-existent transfer raises NotFoundError."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                transfer_service.update_transfer(
                    99999, transfer_data["user"].id, amount=Decimal("100")
                )

    def test_validates_positive_amount(self, app, db, transfer_data):
        """Update with zero amount raises ValidationError."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            with pytest.raises(ValidationError, match="positive"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, amount=Decimal("0")
                )

    def test_validates_period_ownership(self, app, db, transfer_data, second_user):
        """Update with period belonging to another user raises NotFoundError."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            from app.services import pay_period_service
            from datetime import date
            other_periods = pay_period_service.generate_pay_periods(
                user_id=second_user["user"].id,
                start_date=date(2026, 6, 1),
                num_periods=2,
                cadence_days=14,
            )
            db.session.flush()

            with pytest.raises(NotFoundError):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, pay_period_id=other_periods[0].id
                )

    def test_category_set_to_none_propagates_none(self, app, db, transfer_data):
        """Setting category_id=None propagates None to both shadow transactions."""
        with app.app_context():
            td = transfer_data
            rent_cat = td["categories"]["Rent"]

            xfer = transfer_service.create_transfer(
                user_id=td["user"].id,
                from_account_id=td["account"].id,
                to_account_id=td["savings_account"].id,
                pay_period_id=td["periods"][0].id,
                scenario_id=td["scenario"].id,
                amount=Decimal("100"),
                status_id=td["projected_status"].id,
                category_id=rent_cat.id,
            )

            transfer_service.update_transfer(
                xfer.id, td["user"].id, category_id=None
            )

            assert xfer.category_id is None
            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            for shadow in shadows:
                assert shadow.category_id is None

    def test_actual_amount_none_clears_shadows(self, app, db, transfer_data):
        """Setting actual_amount=None clears it on both shadows."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, actual_amount=Decimal("100")
            )
            transfer_service.update_transfer(
                xfer.id, td["user"].id, actual_amount=None
            )

            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            for s in shadows:
                assert s.actual_amount is None


# ── Delete Tests ───────────────────────────────────────────────────


class TestDeleteTransfer:
    """Tests for transfer_service.delete_transfer."""

    def test_hard_removes_shadows(self, app, db, transfer_data):
        """Hard delete removes transfer and both shadows via CASCADE."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            shadow_ids = [s.id for s in xfer.shadow_transactions]
            db.session.commit()

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=False)
            db.session.commit()
            db.session.expire_all()

            assert db.session.get(Transfer, xfer_id) is None
            for sid in shadow_ids:
                assert db.session.get(Transaction, sid) is None

    def test_soft_marks_shadows_deleted(self, app, db, transfer_data):
        """Soft delete flags transfer and both shadows as is_deleted."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id

            result = transfer_service.delete_transfer(
                xfer_id, td["user"].id, soft=True
            )

            assert result.is_deleted is True
            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer_id).all()
            assert len(shadows) == 2
            for s in shadows:
                assert s.is_deleted is True

    def test_hard_on_already_soft_deleted(self, app, db, transfer_data):
        """Hard delete after soft delete physically removes all records."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            db.session.commit()

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.commit()

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=False)
            db.session.commit()
            db.session.expire_all()

            assert db.session.get(Transfer, xfer_id) is None
            remaining = db.session.query(Transaction).filter_by(transfer_id=xfer_id).count()
            assert remaining == 0

    def test_wrong_user_rejected(self, app, db, transfer_data, second_user):
        """Delete by non-owner raises NotFoundError."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            with pytest.raises(NotFoundError):
                transfer_service.delete_transfer(
                    xfer.id, second_user["user"].id
                )

    def test_nonexistent_rejected(self, app, db, transfer_data):
        """Delete of non-existent transfer raises NotFoundError."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                transfer_service.delete_transfer(
                    99999, transfer_data["user"].id
                )

    def test_soft_delete_idempotent(self, app, db, transfer_data):
        """Soft delete on already soft-deleted transfer is a no-op."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            # Second soft delete should not raise.
            result = transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            assert result.is_deleted is True


# ── Invariant Verification Tests ───────────────────────────────────


class TestInvariants:
    """Tests that directly verify the five core invariants."""

    def test_shadow_count_is_exactly_two(self, app, db, transfer_data):
        """Invariant 1: every transfer has exactly two shadow transactions."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            count = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .count()
            )
            assert count == 2

    def test_shadow_types_are_one_expense_one_income(self, app, db, transfer_data):
        """Invariant 1: one shadow is expense, one is income."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()

            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            type_ids = [s.transaction_type_id for s in shadows]
            assert expense_type.id in type_ids
            assert income_type.id in type_ids

    def test_shadows_cannot_exist_without_transfer(self, app, db, transfer_data):
        """Invariant 2: deleting a transfer removes all shadows (CASCADE)."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            db.session.commit()

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=False)
            db.session.commit()
            db.session.expire_all()

            orphans = db.session.query(Transaction).filter_by(transfer_id=xfer_id).count()
            assert orphans == 0

    def test_amounts_always_match_after_update(self, app, db, transfer_data):
        """Invariant 3: shadow amounts always equal transfer amount."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, amount=Decimal("777.77")
            )

            assert xfer.amount == Decimal("777.77")
            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            for s in shadows:
                assert s.estimated_amount == Decimal("777.77")

    def test_statuses_always_match_after_update(self, app, db, transfer_data):
        """Invariant 4: shadow statuses always equal transfer status."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            done = db.session.query(Status).filter_by(name="Paid").one()
            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done.id
            )

            assert xfer.status_id == done.id
            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            for s in shadows:
                assert s.status_id == done.id

    def test_periods_always_match_after_update(self, app, db, transfer_data):
        """Invariant 5: shadow periods always equal transfer period."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            new_period = td["periods"][3]

            transfer_service.update_transfer(
                xfer.id, td["user"].id, pay_period_id=new_period.id
            )

            assert xfer.pay_period_id == new_period.id
            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            for s in shadows:
                assert s.pay_period_id == new_period.id

    def test_multiple_updates_maintain_invariants(self, app, db, transfer_data):
        """Multiple sequential updates do not break any invariant."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            done = db.session.query(Status).filter_by(name="Paid").one()
            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                amount=Decimal("999.99"),
                status_id=done.id,
                pay_period_id=td["periods"][4].id,
            )

            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            assert len(shadows) == 2
            for s in shadows:
                assert s.estimated_amount == Decimal("999.99")
                assert s.status_id == done.id
                assert s.pay_period_id == td["periods"][4].id


# ── Soft-Delete Handling Tests (M2) ──────────────────────────────


class TestSoftDeleteHandling:
    """Tests verifying that soft-deleted transfers produce clear errors
    instead of misleading data-integrity messages, and that delete
    operations remain idempotent across active/deleted states.
    """

    def test_update_soft_deleted_transfer_raises_not_found(self, app, db, transfer_data):
        """Verify that calling update_transfer on a soft-deleted transfer
        raises NotFoundError, not a misleading data integrity ValidationError.
        The transfer service treats soft-deleted transfers as non-existent to
        prevent confusing error messages during debugging.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.flush()

            with pytest.raises(NotFoundError, match="not found"):
                transfer_service.update_transfer(
                    xfer_id, td["user"].id, amount=Decimal("500.00")
                )

    def test_delete_soft_deleted_transfer_is_idempotent(self, app, db, transfer_data):
        """Verify that calling delete_transfer(soft=True) on an already
        soft-deleted transfer succeeds idempotently without raising
        NotFoundError.  Repeated soft-delete must not break the template
        deactivation workflow, which may process the same transfers
        multiple times.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            # Second call must not raise.
            result = transfer_service.delete_transfer(
                xfer_id, td["user"].id, soft=True
            )
            assert result.is_deleted is True

            # Shadows are still soft-deleted.
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer_id)
                .all()
            )
            assert all(s.is_deleted for s in shadows)

    def test_hard_delete_soft_deleted_transfer_succeeds(self, app, db, transfer_data):
        """Verify that hard-deleting a previously soft-deleted transfer
        succeeds and removes the transfer and both shadows from the
        database via CASCADE.  The hard-delete path must work regardless
        of the is_deleted flag.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            db.session.commit()

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.commit()

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=False)
            db.session.commit()
            db.session.expire_all()

            assert db.session.get(Transfer, xfer_id) is None
            remaining = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer_id)
                .count()
            )
            assert remaining == 0

    def test_shadow_error_distinguishes_deleted_from_corrupt(
        self, app, db, transfer_data
    ):
        """Verify that the shadow count validation error message accurately
        distinguishes between a soft-deleted transfer (expected state, not
        corruption) and a genuinely corrupt transfer missing shadows
        (unexpected state).  Misleading error messages waste developer time
        during debugging.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id

            # Soft-delete via service.
            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.flush()

            # Bypass _get_transfer_or_raise by importing the helper directly.
            # This simulates a future code path that allows deleted transfers
            # through and hits the shadow count check.
            from app.services.transfer_service import (  # pylint: disable=import-outside-toplevel
                _get_shadow_transactions,
            )

            with pytest.raises(ValidationError, match="soft-deleted"):
                _get_shadow_transactions(xfer_id)


# ── Restore Tests (M1) ──────────────────────────────────────────


class TestRestoreTransfer:
    """Tests for transfer_service.restore_transfer."""

    def test_restores_transfer_and_shadows(self, app, db, transfer_data):
        """Verify that restore_transfer reverses a soft-delete by setting
        is_deleted=False on the transfer and both shadow transactions.
        This is the inverse of delete_transfer(soft=True) and must restore
        all three entities atomically.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.flush()

            # Confirm all three are soft-deleted.
            assert xfer.is_deleted is True
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id
            ).all()
            assert all(s.is_deleted for s in shadows)

            # Restore.
            result = transfer_service.restore_transfer(xfer_id, td["user"].id)

            assert result.is_deleted is False
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id
            ).all()
            assert len(shadows) == 2
            for s in shadows:
                assert s.is_deleted is False
            # Amount, status, period unchanged.
            assert result.amount == Decimal("250.00")

    def test_rejects_nonexistent_transfer(self, app, db, transfer_data):
        """Verify that restore_transfer raises NotFoundError for a transfer
        ID that does not exist, using the same generic message as other
        not-found conditions to avoid leaking valid ID information.
        """
        with app.app_context():
            with pytest.raises(NotFoundError, match="not found"):
                transfer_service.restore_transfer(
                    999999, transfer_data["user"].id
                )

    def test_rejects_wrong_user(self, app, db, transfer_data, second_user):
        """Verify that restore_transfer raises NotFoundError when called
        with a user_id that does not own the transfer.  The error message
        must be identical to the nonexistent case to prevent ownership
        enumeration.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            transfer_service.delete_transfer(
                xfer.id, td["user"].id, soft=True
            )

            with pytest.raises(NotFoundError, match="not found"):
                transfer_service.restore_transfer(
                    xfer.id, second_user["user"].id
                )

    def test_idempotent_on_active_transfer(self, app, db, transfer_data):
        """Verify that calling restore_transfer on an already-active
        transfer completes without error.  This idempotency ensures that
        bulk reactivation workflows do not fail when processing a mix of
        deleted and active transfers.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            # Not deleted -- restore is a no-op.
            result = transfer_service.restore_transfer(
                xfer.id, td["user"].id
            )
            assert result.is_deleted is False
            assert result.amount == Decimal("250.00")

    def test_corrects_drifted_shadow_amounts(self, app, db, transfer_data):
        """Verify that restore_transfer detects and corrects shadow
        estimated_amount values that drifted from the transfer amount
        during the soft-deleted period.  This defense-in-depth check
        prevents restoring inconsistent shadow data that would cause
        incorrect balance calculations.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.flush()

            # Simulate drift: directly change a shadow's amount.
            shadow = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id
            ).first()
            shadow.estimated_amount = Decimal("999.00")
            db.session.flush()

            transfer_service.restore_transfer(xfer_id, td["user"].id)

            # Both shadows must match transfer amount (250.00), not 999.00.
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id
            ).all()
            for s in shadows:
                assert s.estimated_amount == Decimal("250.00")

    def test_corrects_drifted_shadow_status(self, app, db, transfer_data):
        """Verify that restore_transfer detects and corrects shadow
        status_id values that drifted from the transfer status during the
        soft-deleted period.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.flush()

            # Simulate drift: change one shadow's status.
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            shadow = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id
            ).first()
            shadow.status_id = done_status.id
            db.session.flush()

            transfer_service.restore_transfer(xfer_id, td["user"].id)

            # Both shadows must match transfer's projected status.
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id
            ).all()
            for s in shadows:
                assert s.status_id == td["projected_status"].id

    def test_raises_on_missing_shadows(self, app, db, transfer_data):
        """Verify that restore_transfer raises ValidationError when a
        soft-deleted transfer has no shadow transactions, indicating data
        corruption that cannot be automatically repaired.  The error
        message must clearly identify this as a data integrity issue.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            db.session.commit()

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.commit()

            # Simulate corruption: hard-delete shadows directly.
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id
            ).all()
            for s in shadows:
                db.session.delete(s)
            db.session.commit()

            with pytest.raises(ValidationError, match="integrity"):
                transfer_service.restore_transfer(xfer_id, td["user"].id)


class TestDueDateAndPaidAtShadows:
    """Tests for due_date and paid_at propagation to shadow transactions."""

    def test_shadow_due_date_propagation(self, app, db, transfer_data):
        """create_transfer with due_date propagates to both shadows."""
        from datetime import date

        with app.app_context():
            td = transfer_data
            xfer = transfer_service.create_transfer(
                user_id=td["user"].id,
                from_account_id=td["account"].id,
                to_account_id=td["savings_account"].id,
                pay_period_id=td["periods"][0].id,
                scenario_id=td["scenario"].id,
                amount=Decimal("250.00"),
                status_id=td["projected_status"].id,
                category_id=td["categories"]["Rent"].id,
                due_date=date(2026, 1, 15),
            )
            db.session.flush()

            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            for s in shadows:
                assert s.due_date == date(2026, 1, 15)

    def test_shadow_due_date_null_propagation(self, app, db, transfer_data):
        """create_transfer with due_date=None produces shadows with due_date=None."""
        with app.app_context():
            td = transfer_data
            xfer = transfer_service.create_transfer(
                user_id=td["user"].id,
                from_account_id=td["account"].id,
                to_account_id=td["savings_account"].id,
                pay_period_id=td["periods"][0].id,
                scenario_id=td["scenario"].id,
                amount=Decimal("250.00"),
                status_id=td["projected_status"].id,
                category_id=td["categories"]["Rent"].id,
                due_date=None,
            )
            db.session.flush()

            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            for s in shadows:
                assert s.due_date is None

    def test_shadow_due_date_update(self, app, db, transfer_data):
        """update_transfer with due_date propagates to both shadows."""
        from datetime import date

        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            db.session.flush()

            transfer_service.update_transfer(
                xfer.id, td["user"].id, due_date=date(2026, 2, 1)
            )

            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            for s in shadows:
                assert s.due_date == date(2026, 2, 1)

    def test_paid_at_transfer_shadow_both_set(self, app, db, transfer_data):
        """update_transfer with paid_at sets paid_at on both shadows."""
        from datetime import datetime, timezone

        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            db.session.flush()

            now = datetime.now(timezone.utc)
            transfer_service.update_transfer(
                xfer.id, td["user"].id, paid_at=now
            )
            db.session.flush()

            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            for s in shadows:
                assert s.paid_at is not None

    def test_paid_at_transfer_shadow_revert(self, app, db, transfer_data):
        """Setting paid_at then reverting to None clears paid_at on both shadows."""
        from datetime import datetime, timezone

        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            db.session.flush()

            # Set paid_at.
            now = datetime.now(timezone.utc)
            transfer_service.update_transfer(
                xfer.id, td["user"].id, paid_at=now
            )
            db.session.flush()

            # Verify it was set.
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            for s in shadows:
                assert s.paid_at is not None

            # Revert to None.
            transfer_service.update_transfer(
                xfer.id, td["user"].id, paid_at=None
            )
            db.session.flush()

            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            for s in shadows:
                assert s.paid_at is None

    def test_shadow_paid_at_null_propagation(self, app, db, transfer_data):
        """update_transfer with paid_at=None sets both shadows to None."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            db.session.flush()

            transfer_service.update_transfer(
                xfer.id, td["user"].id, paid_at=None
            )
            db.session.flush()

            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            for s in shadows:
                assert s.paid_at is None
