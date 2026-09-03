"""
Shekel Budget App -- Transfer Service Tests

Comprehensive tests for create_transfer, update_transfer, and
delete_transfer.  Covers all five core invariants, validation rules,
edge cases, and cross-user isolation.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy.exc

from app import ref_cache
from app.enums import SettlementBasisEnum, StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.journal_entry import JournalEntry, Posting
from app.models.ref import AccountType, Status, TransactionType
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    account_posting_service,
    account_service,
    loan_posting_service,
    pay_period_write,
    transfer_service,
)
from app.services.row_valuation import settled_figure
from app.utils.dates import display_today
from app.exceptions import NotFoundError, ValidationError
from tests._test_helpers import (
    write_past_the_amount_seam,
    add_anchor_history,
    an_entered_day,
    create_loan_account,
    settlement_basis_id,
    settlement_columns,
    shadow_amount,
)
from app.services.settle_day import record_settle_day
from app.services.state_machine import allowed_transitions
from app.services.amount_ownership import state_own_amount


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


def _ledger_nets_for_transfer(transfer_id):
    """Return ``{ledger_account_id: net}`` over a transfer's posted legs.

    Reads the posted side the way the reconcile does -- summed per ledger
    account across every journal entry the transfer wrote -- so a leg that has
    been reversed to zero drops out rather than showing as two rows.

    Args:
        transfer_id: The transfer whose postings to sum.

    Returns:
        The non-zero nets, keyed by ledger account id.
    """
    rows = (
        db.session.query(Posting.ledger_account_id, Posting.amount)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(JournalEntry.transfer_id == transfer_id)
        .all()
    )
    nets = {}
    for ledger_account_id, amount in rows:
        nets[ledger_account_id] = nets.get(
            ledger_account_id, Decimal("0"),
        ) + amount
    return {key: net for key, net in nets.items() if net != 0}


def _create_basic_transfer(td):
    """Helper: create a transfer using the standard test data."""
    return transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=td["user"].id,
            from_account_id=td["account"].id,
            to_account_id=td["savings_account"].id,
            pay_period_id=td["periods"][0].id,
            scenario_id=td["scenario"].id,
            amount=Decimal("250.00"),
            status_id=td["projected_status"].id,
            category_id=td["categories"]["Rent"].id,
        ),
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
                assert shadow_amount(s) == Decimal("250.00")
                assert s.status_id == td["projected_status"].id
                assert s.pay_period_id == td["periods"][0].id
                assert s.scenario_id == td["scenario"].id
                assert s.template_id is None
                assert s.is_override is False
                assert s.is_deleted is False
                assert s.settled_amount is None

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
                transfer_service.TransferSpec(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount=Decimal("500.00"),
                    status_id=td["projected_status"].id,
                    category_id=rent_cat.id,
                ),
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
                transfer_service.TransferSpec(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount=Decimal("200.00"),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                    transfer_template_id=td["transfer_template"].id,
                ),
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
                transfer_service.TransferSpec(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount=Decimal("300.00"),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                    name="Mortgage Payment",
                ),
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
                    transfer_service.TransferSpec(
                        user_id=td["user"].id,
                        from_account_id=td["account"].id,
                        to_account_id=td["savings_account"].id,
                        pay_period_id=td["periods"][0].id,
                        scenario_id=td["scenario"].id,
                        amount=Decimal("0"),
                        status_id=td["projected_status"].id,
                        category_id=td["categories"]["Rent"].id,
                    ),
                )

    def test_negative_amount_rejected(self, app, db, transfer_data):
        """Negative amount raises ValidationError."""
        with app.app_context():
            td = transfer_data
            with pytest.raises(ValidationError, match="positive"):
                transfer_service.create_transfer(
                    transfer_service.TransferSpec(
                        user_id=td["user"].id,
                        from_account_id=td["account"].id,
                        to_account_id=td["savings_account"].id,
                        pay_period_id=td["periods"][0].id,
                        scenario_id=td["scenario"].id,
                        amount=Decimal("-100"),
                        status_id=td["projected_status"].id,
                        category_id=td["categories"]["Rent"].id,
                    ),
                )

    def test_same_account_rejected(self, app, db, transfer_data):
        """Same from and to account raises ValidationError."""
        with app.app_context():
            td = transfer_data
            with pytest.raises(ValidationError, match="different"):
                transfer_service.create_transfer(
                    transfer_service.TransferSpec(
                        user_id=td["user"].id,
                        from_account_id=td["account"].id,
                        to_account_id=td["account"].id,
                        pay_period_id=td["periods"][0].id,
                        scenario_id=td["scenario"].id,
                        amount=Decimal("100"),
                        status_id=td["projected_status"].id,
                        category_id=td["categories"]["Rent"].id,
                    ),
                )

    def test_transfer_out_of_a_loan_is_rejected(self, app, db, transfer_data):
        """A transfer whose SOURCE is an amortizing loan raises ValidationError.

        Money cannot be transferred OUT of a loan (the loan as ``from_account``):
        the amortization engine only projects payments INTO a loan and the loan
        posting ledger assumes every loan shadow is a payment IN (review M7), so
        a disbursement would post a raw cash movement onto the loan ledger with
        no interest / escrow split.  The guard rejects it at ``create_transfer``
        -- the sole creation chokepoint -- and writes no transfer or shadow row.
        """
        with app.app_context():
            td = transfer_data
            loan = create_loan_account(
                {"user": td["user"], "scenario": td["scenario"]},
                db.session, name="Mortgage",
                principal=Decimal("250000.00"), rate=Decimal("0.06000"),
                origination_date=date(2025, 1, 1), term=360,
            )
            db.session.commit()
            transfers_before = db.session.query(Transfer).count()

            with pytest.raises(ValidationError, match="out of a loan"):
                transfer_service.create_transfer(
                    transfer_service.TransferSpec(
                        user_id=td["user"].id,
                        from_account_id=loan.id,      # loan as SOURCE: forbidden
                        to_account_id=td["account"].id,
                        pay_period_id=td["periods"][0].id,
                        scenario_id=td["scenario"].id,
                        amount=Decimal("100"),
                        status_id=td["projected_status"].id,
                        category_id=td["categories"]["Rent"].id,
                    ),
                )
            # The guard fired before any write: no transfer (and so no shadows).
            assert db.session.query(Transfer).count() == transfers_before

    def test_wrong_user_account_rejected(self, app, db, transfer_data, second_user):
        """Account belonging to another user raises NotFoundError."""
        with app.app_context():
            td = transfer_data
            with pytest.raises(NotFoundError):
                transfer_service.create_transfer(
                    transfer_service.TransferSpec(
                        user_id=td["user"].id,
                        from_account_id=second_user["account"].id,
                        to_account_id=td["savings_account"].id,
                        pay_period_id=td["periods"][0].id,
                        scenario_id=td["scenario"].id,
                        amount=Decimal("100"),
                        status_id=td["projected_status"].id,
                        category_id=td["categories"]["Rent"].id,
                    ),
                )

    def test_nonexistent_account_rejected(self, app, db, transfer_data):
        """Non-existent account ID raises NotFoundError."""
        with app.app_context():
            td = transfer_data
            with pytest.raises(NotFoundError):
                transfer_service.create_transfer(
                    transfer_service.TransferSpec(
                        user_id=td["user"].id,
                        from_account_id=99999,
                        to_account_id=td["savings_account"].id,
                        pay_period_id=td["periods"][0].id,
                        scenario_id=td["scenario"].id,
                        amount=Decimal("100"),
                        status_id=td["projected_status"].id,
                        category_id=td["categories"]["Rent"].id,
                    ),
                )

    def test_wrong_user_period_rejected(self, app, db, transfer_data, second_user):
        """Period belonging to another user raises NotFoundError."""
        with app.app_context():
            td = transfer_data
            # Create a period for the second user.
            from app.services import pay_period_service
            from datetime import date
            other_periods = pay_period_write.record_paydays(
                user_id=second_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=2,
                cadence_days=14,
            )
            db.session.flush()

            with pytest.raises(NotFoundError):
                transfer_service.create_transfer(
                    transfer_service.TransferSpec(
                        user_id=td["user"].id,
                        from_account_id=td["account"].id,
                        to_account_id=td["savings_account"].id,
                        pay_period_id=other_periods[0].id,
                        scenario_id=td["scenario"].id,
                        amount=Decimal("100"),
                        status_id=td["projected_status"].id,
                        category_id=td["categories"]["Rent"].id,
                    ),
                )

    def test_wrong_user_category_rejected(self, app, db, transfer_data, second_user):
        """Category belonging to another user raises NotFoundError."""
        with app.app_context():
            td = transfer_data
            other_cat = second_user["categories"]["Rent"]

            with pytest.raises(NotFoundError):
                transfer_service.create_transfer(
                    transfer_service.TransferSpec(
                        user_id=td["user"].id,
                        from_account_id=td["account"].id,
                        to_account_id=td["savings_account"].id,
                        pay_period_id=td["periods"][0].id,
                        scenario_id=td["scenario"].id,
                        amount=Decimal("100"),
                        status_id=td["projected_status"].id,
                        category_id=other_cat.id,
                    ),
                )

    def test_invalid_amount_string_rejected(self, app, db, transfer_data):
        """Non-numeric amount raises ValidationError."""
        with app.app_context():
            td = transfer_data
            with pytest.raises(ValidationError, match="Invalid amount"):
                transfer_service.create_transfer(
                    transfer_service.TransferSpec(
                        user_id=td["user"].id,
                        from_account_id=td["account"].id,
                        to_account_id=td["savings_account"].id,
                        pay_period_id=td["periods"][0].id,
                        scenario_id=td["scenario"].id,
                        amount="not-a-number",
                        status_id=td["projected_status"].id,
                        category_id=td["categories"]["Rent"].id,
                    ),
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
                assert shadow_amount(s) == Decimal("400.00")

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

    def test_a_figure_on_an_UNSETTLED_transfer_is_REFUSED(self, app, db, transfer_data):
        """A figure records a settle, so an unsettled pair cannot be handed one.

        **This asserted the opposite until plan step X-au-c3** -- an
        ``actual_amount`` handed to ``update_transfer`` was written onto both
        shadows whatever their status.  A figure now RECORDS what moved, and
        ``ck_transactions_settled_amount_needs_basis`` keeps one off a row whose
        money has not; the service refuses the request with a designed 400
        before any column is reached, rather than letting it reach the database.

        No form can produce it: the correction box renders only on a settled
        row.  To record a figure, settle the transfer -- the same act that
        records one.  Stated here rather than quietly rewritten, because it is a
        BEHAVIOUR change and belongs in the commit message.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            with pytest.raises(ValidationError, match="is not settling"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, settled_amount=Decimal("245.00")
                )

            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            assert len(shadows) == 2
            for shadow in shadows:
                assert shadow.settled_amount is None
                assert shadow.settled_basis_id is None

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
            other_periods = pay_period_write.record_paydays(
                user_id=second_user["user"].id,
                first_payday=date(2026, 6, 1),
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
                transfer_service.TransferSpec(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount=Decimal("100"),
                    status_id=td["projected_status"].id,
                    category_id=rent_cat.id,
                ),
            )

            transfer_service.update_transfer(
                xfer.id, td["user"].id, category_id=None
            )

            assert xfer.category_id is None
            shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            for shadow in shadows:
                assert shadow.category_id is None

    def test_a_REVERT_is_what_clears_a_settled_transfers_figure(
        self, app, db, transfer_data,
    ):
        """A revert takes back the ASSERTION and leaves what moved on the pair.

        **This asserted the opposite until the developer's 2026-08-17 ruling.**
        A draft of plan step X-au-c3 released the figure and the basis with the
        day, under a CHECK that paired them; that welded two facts with
        different lifetimes into one, so withdrawing "this moved on this day"
        also destroyed "this is what the bank took".  The full-edit popover
        TELLS the user to revert in order to edit, so the app's own instruction
        deleted a figure they had read off a statement.

        Both shadows keep it, which is Transfer Invariant 3 over the record as
        well as over the amount, and neither is worth it while unsettled --
        ``row_valuation.settled_figure`` asks the STATUS, so it answers ``None``
        for a reverted leg whatever the leg still carries.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                status_id=done_id, settled_amount=Decimal("100"),
            )
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer.id,
            ).all()
            for shadow in shadows:
                assert shadow.settled_amount == Decimal("100")

            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=projected_id,
            )

            db.session.expire_all()
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer.id,
            ).all()
            for shadow in shadows:
                # The ASSERTION is withdrawn ...
                assert shadow.settled_on is None
                assert shadow.reconciled_by_id is None
                # ... and WHAT MOVED is kept, on BOTH legs (Invariant 3).
                assert shadow.settled_amount == Decimal("100")
                assert shadow.settled_basis_id is not None
                # Kept, but not counted: the status is what decides.
                assert settled_figure(shadow) is None

    def test_settle_day_defaults_to_today_when_omitted(
        self, app, db, transfer_data
    ):
        """F-048 / C-22: settling with no explicit day defaults to the user's today.

        Defense-in-depth for the route layer: any caller that
        forgets to pass a settle day when transitioning to a settled
        status (Paid/Received/Settled) still produces shadows with
        a recorded ``settled_on`` day -- the dashboard's "paid
        on time" indicator and ``Transaction.days_paid_before_due``
        analytics rely on it.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            assert done_status.is_settled is True

            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_status.id
            )
            db.session.flush()

            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, is_deleted=False)
                .all()
            )
            assert len(shadows) == 2
            for s in shadows:
                assert s.settled_on is not None, (
                    f"Shadow {s.id} has no settled_on after settle "
                    f"without explicit kwarg; defense-in-depth failed."
                )

    def test_an_explicit_day_wins_over_the_default_on_a_settle(
        self, app, db, transfer_data
    ):
        """F-048 / C-22: an explicit settle DAY takes precedence over today.

        **This test asserted that ``settled_on=None`` could settle a transfer
        with NO day, and it pinned the defect** (finding **N-183**).  That is
        precisely the row ``balance_predicates.settled_day`` refuses -- a
        settled row whose money moved on no recorded day -- so the assertion
        was defending a 500 on every balance surface that folds the pair.  The
        caller it named is gone too: ``_apply_shadow_update`` passed an explicit
        ``None`` on a revert until plan step X-f1b0, and that was a second
        statement of the seam's own clear-on-leaving-the-band rule (finding
        **N-178**'s other half), not a value anything needed.

        What the rule really is, and what is pinned here: an explicit day wins
        over the default, and the default is the user's today.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            done_status = db.session.query(Status).filter_by(name="Paid").one()

            # Settle and correct in one call: the explicit day must survive
            # the seam's "stamp today on first entry" default.
            explicit = display_today() - timedelta(days=5)
            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                status_id=done_status.id, settle_day=an_entered_day(explicit),
            )
            db.session.flush()
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, is_deleted=False)
                .all()
            )
            assert len(shadows) == 2
            for s in shadows:
                assert s.settled_on == explicit, (
                    f"Shadow {s.id} took the default day {s.settled_on} "
                    f"instead of the explicit {explicit}."
                )

    def test_settle_day_cleared_on_revert_to_non_settled(
        self, app, db, transfer_data
    ):
        """F-048 / C-22: reverting to non-settled clears the stale settle day.

        Maintains the settled-iff-dated invariant: a Paid
        transfer reverted to Projected must have its shadows'
        settle day cleared, otherwise a
        future settle would silently inherit the stale timestamp.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            projected_status = (
                db.session.query(Status).filter_by(name="Projected").one()
            )

            # Settle (the seam records the user's today).
            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_status.id,
            )
            db.session.flush()
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, is_deleted=False)
                .all()
            )
            for s in shadows:
                assert s.settled_on is not None

            # Revert to Projected with no explicit day.
            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=projected_status.id,
            )
            db.session.flush()
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, is_deleted=False)
                .all()
            )
            for s in shadows:
                assert s.settled_on is None, (
                    f"Shadow {s.id} retained a stale settled_on after "
                    f"revert to Projected; the F-048 invariant is "
                    f"violated."
                )


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
                assert shadow_amount(s) == Decimal("777.77")

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
                assert shadow_amount(s) == Decimal("999.99")
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
            # pylint: disable-next=import-outside-toplevel
            from app.services.transfer_service._validation import (
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

    def test_repairable_status_drift_is_still_repaired(
        self, app, db, transfer_data,
    ):
        """A shadow drifted to a LEGALLY reachable status is repaired, quietly.

        The positive control for
        :meth:`test_unrepairable_status_drift_is_refused` below -- without it
        that test could pass by refusing every drift, which would silently
        break the repair this function exists for.  Projected -> Paid is a
        legal transaction transition, so the shadow is pulled back into line
        with its parent and the restore succeeds.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            paid_id = ref_cache.status_id(StatusEnum.DONE)
            transfer_service.update_transfer(
                xfer_id, td["user"].id, status_id=paid_id,
            )
            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.flush()

            # Drift ONE shadow backwards to Projected, which Paid is legally
            # reachable from.  Written directly, which is what the drift this
            # repair exists for looks like.
            drifted = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer_id).first()
            )
            drifted.status_id = ref_cache.status_id(StatusEnum.PROJECTED)
            db.session.flush()

            transfer_service.restore_transfer(xfer_id, td["user"].id)

            db.session.refresh(drifted)
            assert drifted.status_id == paid_id
            assert drifted.is_deleted is False

    def test_a_status_repair_takes_the_siblings_instant_never_today(
        self, app, db, transfer_data,
    ):
        """A drift repair must not INVENT a settle day.

        Routing the repair through the status seam (ruling R-DN) brought the
        seam's settle-day maintenance with it, and the seam's per-row rule is
        "preserve an instant, else stamp ``now()``".  For a PAIR that rule is
        wrong: the sibling shadow already records when the money moved, and
        since plan step E1a that civil day is the ``entry_date`` the re-posted
        entry is filed under -- so stamping today would move money on a repair.
        The pair-aware applier prefers the existing instant.

        This is also the only assertion in the suite that a bare
        ``shadow.status_id = xfer.status_id`` write cannot satisfy, so it is
        what pins the repair to the seam at all.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            paid_id = ref_cache.status_id(StatusEnum.DONE)
            real_settle = date(2026, 3, 20)
            transfer_service.update_transfer(
                xfer_id, td["user"].id, status_id=paid_id, settle_day=an_entered_day(real_settle),
            )
            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.flush()

            # Drift ONE shadow back to Projected and strip its instant; its
            # sibling keeps the real one.
            drifted, sibling = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer_id).order_by(Transaction.id).all()
            )
            drifted.status_id = ref_cache.status_id(StatusEnum.PROJECTED)
            # The whole record is stripped with the day, which is the LEGACY
            # drift this test is about: a row that pre-dates the settlement
            # record entirely.  ``ck_transactions_settle_day_needs_a_record`` only
            # forbids the reverse (a day naming no figure), so the RETAINED
            # shape -- record kept, day released -- is legal and is covered by
            # ``test_a_repair_prefers_the_leg_still_in_the_settled_band``.
            record_settle_day(drifted, None)
            drifted.settled_amount = None
            drifted.settled_basis_id = None
            db.session.flush()
            assert sibling.settled_on == real_settle

            transfer_service.restore_transfer(xfer_id, td["user"].id)
            db.session.flush()
            db.session.refresh(drifted)

            assert drifted.settled_on == real_settle, (
                f"the repair invented a settle day: {drifted.settled_on} "
                f"instead of the sibling's {real_settle}"
            )

    def test_a_repair_prefers_the_leg_still_in_the_settled_band(
        self, app, db, transfer_data,
    ):
        """A repair reads the LIVE leg's record, not the reverted leg's stale one.

        **The hazard is retention, and it did not exist before plan step
        X-au-c3.**  The pair's settle DAY needs no such preference, because a
        revert RELEASES ``settled_on`` -- a drifted leg carries none and the day
        loop skips it by construction.  The RECORD is the opposite: a revert
        KEEPS it, so a drifted leg still carries whatever it last settled at.

        ``TransferRows.shadows`` is ``(expense, income)``, so a repair that
        simply took the first leg holding a record would ALWAYS take the expense
        leg -- here the reverted one, carrying a stale ``$25.00``.  Writing that
        onto the income leg would price the pair at a figure one of them had
        already stopped claiming, and the posted ledger reads that figure
        (``posting_service._settle_effective``).

        The two legs are given DIFFERENT records on purpose: with equal ones the
        preference is unobservable, which is why the shape survived a suite
        whose only drifted leg was stripped bare
        (``test_a_repair_takes_the_siblings_settle_day`` above).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            paid_id = ref_cache.status_id(StatusEnum.DONE)
            transfer_service.update_transfer(
                xfer_id, td["user"].id, status_id=paid_id,
                settle_day=an_entered_day(date(2026, 3, 20)),
            )
            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.flush()

            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer_id)
                .order_by(Transaction.id)
                .all()
            )
            expense = next(s for s in shadows if s.is_expense)
            income = next(s for s in shadows if s is not expense)

            # The LIVE leg keeps the pair's real record.
            income.settled_amount = Decimal("100.00")
            income.settled_basis_id = settlement_basis_id(
                SettlementBasisEnum.CORRECTED,
            )
            # The DRIFTED leg is reverted exactly as the seam reverts: the
            # ASSERTION released, the RECORD retained -- and stale.
            expense.status_id = ref_cache.status_id(StatusEnum.PROJECTED)
            record_settle_day(expense, None)
            expense.reconciled_by_id = None
            expense.settled_amount = Decimal("25.00")
            expense.settled_basis_id = settlement_basis_id(
                SettlementBasisEnum.CORRECTED,
            )
            db.session.flush()

            transfer_service.restore_transfer(xfer_id, td["user"].id)
            db.session.flush()
            db.session.refresh(expense)
            db.session.refresh(income)

            assert income.settled_amount == Decimal("100.00"), (
                "the repair overwrote the LIVE leg's record with the reverted "
                f"leg's stale one: {income.settled_amount}"
            )
            assert expense.settled_amount == Decimal("100.00"), (
                "the repaired leg did not take its sibling's record: "
                f"{expense.settled_amount}"
            )

    def test_a_repair_into_a_projected_status_clears_the_instant(
        self, app, db, transfer_data,
    ):
        """Repairing a shadow DOWN to Projected drops its stale payment time.

        The other half of the rule above: a row that is not settled must not
        carry a settle instant, or ``days_paid_before_due`` and the paid-on-time
        indicator read a payment that has not happened.  Together the two pin
        both directions of the seam's settle-day maintenance on the repair
        path, which had none before ruling R-DO routed it through the seam.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.flush()

            # Parent stays Projected; drift ONE shadow up to Paid with a time.
            drifted = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer_id).order_by(Transaction.id).first()
            )
            drifted.status_id = ref_cache.status_id(StatusEnum.DONE)
            # A dated row carries the whole record (plan step X-au-c3); the
            # drift under test is the STATUS, so the record is coherent.
            record_settle_day(drifted, an_entered_day(date(2026, 3, 20)))
            for column, value in settlement_columns(
                date(2026, 3, 20), shadow_amount(drifted),
            ).items():
                setattr(drifted, column, value)
            db.session.flush()

            transfer_service.restore_transfer(xfer_id, td["user"].id)
            db.session.flush()
            db.session.refresh(drifted)

            assert drifted.status_id == xfer.status_id
            assert drifted.settled_on is None, (
                f"a Projected shadow kept a payment time: {drifted.settled_on}"
            )

    def test_unrepairable_status_drift_is_refused(
        self, app, db, transfer_data,
    ):
        """A shadow the state machine cannot legally move is REFUSED (R-DO).

        **The specimen had to change at plan step balance:X-am and the reason
        is the step's whole content.**  It was a ``Settled`` shadow under a
        Projected parent: the archive was TERMINAL, so nothing was reachable
        from it and no legal transition could reconcile the pair.  With the
        archive deleted, every state in both maps can reach ``Projected`` --
        so a Projected parent has NO unrepairable drift left, and a case built
        on one would assert a refusal that can never fire.

        What is still unrepairable is drift in the other direction: a
        ``Cancelled`` shadow under a ``Paid`` parent.  ``cancelled`` reaches
        only itself and ``projected``, so the parent's Paid is out of reach.
        The rule under test is unchanged -- ``assert_restorable`` asks
        ``allowed_transitions`` whether the shadow can reach the parent -- and
        it now has a specimen that exercises the map rather than a status with
        no outgoing edges at all.

        Before plan step X-aj1 the shadow was silently rewritten to the
        parent's status with no transition check at all, which destroys the
        evidence of how the row got there.  It now refuses in the same voice as
        the shadow-count and type-pairing corruption checks it sits beside.

        The refusal must also leave the transfer SOFT-DELETED: nothing is
        mutated before the preconditions run, so there is no half-restored
        state to roll back.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            # The PARENT settles first, so the pair is Paid/Paid and legal.
            transfer_service.update_transfer(
                xfer_id, td["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.flush()
            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.flush()

            drifted = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer_id).first()
            )
            # The drift: a shadow that walked to Cancelled on its own.  The
            # settle record goes with the status, because a Cancelled row
            # records nothing -- so the fixture expresses exactly one defect
            # rather than three (plan step X-au-c3).
            drifted.status_id = ref_cache.status_id(StatusEnum.CANCELLED)
            record_settle_day(drifted, None)
            for column, value in settlement_columns(None, None).items():
                setattr(drifted, column, value)
            db.session.flush()

            assert ref_cache.status_id(StatusEnum.DONE) not in (
                allowed_transitions(drifted)
            ), "the fixture's drift is repairable -- this case cannot fire"

            with pytest.raises(ValidationError, match="cannot legally"):
                transfer_service.restore_transfer(xfer_id, td["user"].id)

            # Asserted on the IN-MEMORY row, deliberately without a refresh.
            # ``restore_transfer`` never flushes on the refusal path, so a
            # refresh would re-read the un-restored DB row and pass no matter
            # what the function did -- an assertion that cannot fail.  The
            # question is whether the in-session object was left half-restored,
            # and only the un-refreshed object can answer it.
            assert xfer.is_deleted is True, (
                "the refusal left the transfer half-restored in the session"
            )

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

    def test_a_shadow_amount_CANNOT_drift_from_its_transfer(
        self, app, db, transfer_data,
    ):
        """The drift ``restore_transfer`` used to repair is now unconstructible.

        **This case graded the repair until plan step X-au-g-2c-2 and grades
        its ABSENCE now**, which is the cutover in one assertion.  It soft-
        deleted a transfer, wrote ``$999.00`` onto one shadow to simulate drift
        during the deleted period, restored, and checked both shadows had been
        put back to ``$250.00`` -- a hand-written corrector that logged a
        warning and rewrote the copy.

        A shadow stores no figure at all now: it declares ``PARENT_TRANSFER``
        and reads its parent through the amount model, so the SIMULATION is
        what fails.  ``ck_transactions_amount_ownership`` refuses a row holding
        both a declaration and a figure, and the refusal arrives at the flush --
        so there is no window, deleted or otherwise, in which the two can
        disagree, and nothing left for a restore to correct.

        Both halves are asserted: that the write is refused, and that a restore
        over an untouched pair still leaves both legs worth the transfer.  The
        first alone would pass on a schema that had stopped storing shadows at
        all.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.flush()

            shadow = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id
            ).first()
            # A SAVEPOINT, so the refused write is undone without taking the
            # fixture's own transfer with it -- the whole case runs in one
            # transaction, and a bare rollback here would leave nothing to
            # restore.
            attempt = db.session.begin_nested()
            # Past the MAPPING, for the reason
            # ``test_a_leg_cannot_hold_a_figure_while_it_declares_a_parent``
            # states: since plan step X-au-k the seam would release the
            # shadow's declaration instead of drifting it.
            write_past_the_amount_seam(shadow, Decimal("999.00"))
            # ``match`` names the constraint that IS the claim; a bare
            # ``IntegrityError`` would pass on an FK or a unique index too.
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transactions_amount_ownership",
            ):
                db.session.flush()
            attempt.rollback()

            transfer_service.restore_transfer(xfer_id, td["user"].id)

            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id
            ).all()
            assert len(shadows) == 2
            for s in shadows:
                assert s.estimated_amount is None
                assert shadow_amount(s) == Decimal("250.00")

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

    def test_corrects_drifted_shadow_due_date(self, app, db, transfer_data):
        """Verify that restore_transfer re-syncs a shadow due_date that
        drifted from the transfer's canonical due_date during the
        soft-deleted period.  The parent due_date is the source of truth
        (see ``models/transfer.py``); calendar/dashboard/year-end/
        spending-trend consumers read the shadow due_date, so a drifted
        shadow would display a wrong due date after restore.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id

            # Give the transfer a canonical due_date, mirrored to both
            # shadows through the service.
            transfer_service.update_transfer(
                xfer_id, td["user"].id, due_date=date(2026, 6, 15)
            )
            db.session.flush()

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.flush()

            # Simulate drift: directly change one shadow's due_date.
            shadow = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id
            ).first()
            shadow.due_date = date(2026, 12, 25)
            db.session.flush()

            transfer_service.restore_transfer(xfer_id, td["user"].id)

            # Both shadows must match the transfer's canonical due_date,
            # not the drifted 2026-12-25.
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id
            ).all()
            for s in shadows:
                assert s.due_date == date(2026, 6, 15)

    def test_corrects_drifted_shadow_category(self, app, db, transfer_data):
        """Verify that restore_transfer re-syncs a shadow category_id that
        drifted from the transfer's canonical category during the
        soft-deleted period.  Category is mirrored to both shadows so
        each account grid attributes the entry to the same category.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            rent_cat_id = td["categories"]["Rent"].id

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.flush()

            # Simulate drift: change one shadow's category to a different
            # owned category (Transfers: Incoming).
            shadow = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id
            ).first()
            shadow.category_id = td["incoming_cat"].id
            db.session.flush()

            transfer_service.restore_transfer(xfer_id, td["user"].id)

            # Both shadows must match the transfer's canonical category.
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id
            ).all()
            for s in shadows:
                assert s.category_id == rent_cat_id

    def test_corrects_drifted_shadow_is_override(
        self, app, db, transfer_data
    ):
        """Verify that restore_transfer re-syncs a shadow is_override flag
        that drifted from the transfer's canonical value during the
        soft-deleted period.  The override flag is mirrored to both
        shadows so the carry-forward/dedupe state stays coherent across
        the parent transfer and its two shadows.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            # create_transfer always sets is_override=False on the parent.
            assert xfer.is_override is False

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.flush()

            # Simulate drift: flip one shadow's override flag.
            shadow = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id
            ).first()
            shadow.is_override = True
            db.session.flush()

            transfer_service.restore_transfer(xfer_id, td["user"].id)

            # Both shadows must match the transfer's canonical (False)
            # override flag, not the drifted True.
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id
            ).all()
            for s in shadows:
                assert s.is_override is False

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

    def test_rejects_when_source_account_archived(
        self, app, db, transfer_data,
    ):
        """F-164 / C-20: refuse to restore a transfer onto an archived
        source account.

        Soft-delete a transfer, then archive the source account.  The
        next ``restore_transfer`` must raise ``ValidationError`` and
        leave the transfer + shadows soft-deleted (rollback the
        ``is_deleted`` flip applied at the top of the function).  This
        prevents the user from silently resurrecting balance entries
        against an account they have withdrawn from active projections.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            from_account_id = td["account"].id
            db.session.commit()

            transfer_service.delete_transfer(
                xfer_id, td["user"].id, soft=True,
            )
            db.session.commit()

            # Archive the source account post-soft-delete.
            from_account = db.session.get(Account, from_account_id)
            from_account.is_active = False
            db.session.commit()

            with pytest.raises(ValidationError, match="archived"):
                transfer_service.restore_transfer(xfer_id, td["user"].id)

            # Rollback to clear the dirty session state from the failed
            # restore, then re-fetch and confirm soft-delete persisted.
            db.session.rollback()
            xfer_after = db.session.get(Transfer, xfer_id)
            assert xfer_after.is_deleted is True
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer_id)
                .all()
            )
            assert len(shadows) == 2
            for shadow in shadows:
                assert shadow.is_deleted is True

    def test_rejects_when_destination_account_archived(
        self, app, db, transfer_data,
    ):
        """F-164 / C-20: refuse to restore a transfer onto an archived
        destination account.

        Symmetric counterpart to
        ``test_rejects_when_source_account_archived``: archives the
        ``to_account`` instead of the ``from_account`` and verifies the
        same refusal-with-rollback behaviour.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            to_account_id = td["savings_account"].id
            db.session.commit()

            transfer_service.delete_transfer(
                xfer_id, td["user"].id, soft=True,
            )
            db.session.commit()

            # Archive the destination account post-soft-delete.
            to_account = db.session.get(Account, to_account_id)
            to_account.is_active = False
            db.session.commit()

            with pytest.raises(ValidationError, match="archived"):
                transfer_service.restore_transfer(xfer_id, td["user"].id)

            db.session.rollback()
            xfer_after = db.session.get(Transfer, xfer_id)
            assert xfer_after.is_deleted is True
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer_id)
                .all()
            )
            assert len(shadows) == 2
            for shadow in shadows:
                assert shadow.is_deleted is True

    def test_rejects_when_both_accounts_archived(
        self, app, db, transfer_data,
    ):
        """F-164 / C-20: refuse to restore when both accounts are archived.

        Defense-in-depth case: if the user has archived both ends of
        the transfer, the refusal must still trigger and the error
        message must remain the same generic ``archived`` text (no
        information about which account is at fault, since both are).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            from_id = td["account"].id
            to_id = td["savings_account"].id
            db.session.commit()

            transfer_service.delete_transfer(
                xfer_id, td["user"].id, soft=True,
            )
            db.session.commit()

            db.session.get(Account, from_id).is_active = False
            db.session.get(Account, to_id).is_active = False
            db.session.commit()

            with pytest.raises(ValidationError, match="archived"):
                transfer_service.restore_transfer(xfer_id, td["user"].id)

    def test_succeeds_when_accounts_active(self, app, db, transfer_data):
        """F-164 / C-20 sanity check: the active-account guard does
        not regress the happy path.

        With both accounts active (the default), ``restore_transfer``
        must continue to undo the soft-delete and return the live
        transfer.  Companion to
        ``test_restores_transfer_and_shadows`` -- this one exists
        explicitly to verify the new guard does not break the baseline.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id

            transfer_service.delete_transfer(
                xfer_id, td["user"].id, soft=True,
            )
            db.session.flush()

            result = transfer_service.restore_transfer(
                xfer_id, td["user"].id,
            )
            assert result.is_deleted is False
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer_id)
                .all()
            )
            assert len(shadows) == 2
            for shadow in shadows:
                assert shadow.is_deleted is False


class TestDueDateAndSettleDayShadows:
    """Tests for due_date and settled_on propagation to shadow transactions."""

    def test_shadow_due_date_propagation(self, app, db, transfer_data):
        """create_transfer with due_date sets the parent and both shadows."""
        from datetime import date

        with app.app_context():
            td = transfer_data
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount=Decimal("250.00"),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                    due_date=date(2026, 1, 15),
                ),
            )
            db.session.flush()

            # Parent is canonical and carries the due date.
            assert xfer.due_date == date(2026, 1, 15)

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
                transfer_service.TransferSpec(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount=Decimal("250.00"),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                    due_date=None,
                ),
            )
            db.session.flush()

            assert xfer.due_date is None

            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            for s in shadows:
                assert s.due_date is None

    def test_shadow_due_date_update(self, app, db, transfer_data):
        """update_transfer with due_date sets the parent and both shadows."""
        from datetime import date

        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            db.session.flush()

            transfer_service.update_transfer(
                xfer.id, td["user"].id, due_date=date(2026, 2, 1)
            )

            # Parent mirrors the new due date alongside the shadows.
            assert xfer.due_date == date(2026, 2, 1)

            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            for s in shadows:
                assert s.due_date == date(2026, 2, 1)

    def test_a_settle_day_correction_lands_on_both_shadows(
        self, app, db, transfer_data,
    ):
        """A corrected settle day is mirrored to both shadows (Invariant 3).

        The ``settled_on`` edit door (ruling **R-ED**): the user read their
        statement and the money moved on a day other than the one the settle
        was recorded on.  Both shadows take the SAME day, which
        ``posting_service._entry_date`` depends on -- it reads the income
        shadow's day for the pair.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            db.session.flush()
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_status.id,
            )
            db.session.flush()

            corrected = display_today() - timedelta(days=3)
            transfer_service.update_transfer(
                xfer.id, td["user"].id, settle_day=an_entered_day(corrected),
            )
            db.session.flush()

            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            for s in shadows:
                assert s.settled_on == corrected

    def test_a_day_on_an_UNSETTLED_transfer_is_refused(
        self, app, db, transfer_data,
    ):
        """Dating a Projected transfer raises instead of recording it.

        **This test asserted the OPPOSITE until plan step X-f1b** -- it settled
        a Projected transfer's shadows with an instant and checked the column
        was non-NULL.  That is finding **N-183**: ``update_transfer`` assigned
        the column directly, so it could date a row no money had moved for,
        breaking the settled-iff-dated invariant this step establishes and
        leaving a fold that would read a settle day off an unsettled row.  The
        write goes through the status seam now, which refuses it.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            db.session.flush()

            with pytest.raises(ValidationError) as exc:
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, settle_day=an_entered_day(display_today()),
                )
            assert "not a settled status" in str(exc.value)

            for s in (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            ):
                assert s.settled_on is None

    def test_a_settled_transfer_refuses_to_have_its_day_CLEARED(
        self, app, db, transfer_data,
    ):
        """Clearing a settled transfer's day raises; reverting it is the way.

        The other half of finding **N-183**.  A settled row with no day is the
        state ``balance_predicates.settled_day`` REFUSES, so letting an edit
        produce one would turn a form submission into a 500 on every balance
        surface that folds the row.  The legitimate way to remove the day is to
        move the transfer out of the settled band, which the seam does as part
        of the status change -- asserted below so the refusal is not mistaken
        for "the day can never be removed".
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            db.session.flush()
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_status.id,
            )
            db.session.flush()

            with pytest.raises(ValidationError) as exc:
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, settle_day=None,
                )
            assert "cannot be cleared" in str(exc.value)

            # The day survived the refusal.
            for s in (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            ):
                assert s.settled_on == display_today()

            # And the supported route out clears it on both shadows.
            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                status_id=td["projected_status"].id,
            )
            db.session.flush()
            for s in (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            ):
                assert s.settled_on is None

    def test_an_undated_projected_transfer_stays_undated(self, app, db, transfer_data):
        """``settled_on=None`` on a PROJECTED transfer leaves both shadows None.

        A no-op rather than a refusal: the submitted value agrees with the row's
        state (no money has moved, so there is no day), and refusing an edit
        that asks for what is already true would reject an ordinary form
        round-trip that carried an empty date field.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            db.session.flush()

            transfer_service.update_transfer(
                xfer.id, td["user"].id, settle_day=None
            )
            db.session.flush()

            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            for s in shadows:
                assert s.settled_on is None


class TestTheStatusMirrorIsAtomic:
    """A rejected status move must leave all three rows untouched (F-047)."""

    def test_a_shadow_whose_move_is_illegal_blocks_the_whole_trio(
        self, app, db, transfer_data,
    ):
        """The pre-verify pass exists for a drifted shadow, and this is it.

        ``apply_status_to_all_three`` verifies all three rows before the seam
        assigns any.  The input that needs it: the INCOME shadow drifted to
        Received under a Projected parent being moved to Paid.  The transfer's
        move is legal (Projected -> Paid) and the income shadow's is not
        (``received: {received, projected}`` has no edge to Paid).

        The drifted status was ``Settled`` -- terminal, so nothing was
        reachable from it -- until plan step **balance:X-am** deleted it.  The
        replacement is a within-band move the maps still refuse, which is the
        same shape: a shadow whose own transition is illegal while the parent's
        is fine.

        **The EXPENSE shadow is what proves the pre-pass.**  The applier writes
        the shadows before the parent, so the parent is safe either way; a
        verify-as-you-go loop would assign the expense shadow, then raise on the
        income shadow, leaving the pair disagreeing -- Transfer Invariant 4
        broken while an error is reported.  Asserting on the parent instead
        would be a control that cannot fail, which is how this test was first
        written and what a mutation run caught.

        This matters beyond tidiness because ``_apply_shadow_update``'s own
        error path documents that a half-applied ``update_transfer`` leaves
        dirty mutations staged on the session.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            projected_id = xfer.status_id
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id).all()
            )
            expense_shadow = next(
                s for s in shadows
                if s.transaction_type_id == expense_type_id
            )
            income_shadow = next(s for s in shadows if s is not expense_shadow)
            income_shadow.status_id = ref_cache.status_id(StatusEnum.RECEIVED)
            # As above: the drift under test is the STATUS alone, so the day
            # and the RECORD come with it (plan step X-au-c3).
            record_settle_day(income_shadow, an_entered_day(display_today()))
            for column, value in settlement_columns(
                display_today(), shadow_amount(income_shadow),
            ).items():
                setattr(income_shadow, column, value)
            db.session.flush()

            with pytest.raises(ValidationError):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    status_id=ref_cache.status_id(StatusEnum.DONE),
                )

            assert expense_shadow.status_id == projected_id, (
                "the expense shadow was moved before the income shadow's own "
                "move was refused -- the pair is now half-applied"
            )
            assert xfer.status_id == projected_id


class TestTheFigureCorrectionDoorOnAPair:
    """``update_transfer``'s FIGURE correction -- the service tier of the Actual box.

    The transfer twin of ``transaction_service``'s door, and it exists because
    the two halves of one assertion had different rights: a transfer's settle
    DAY was correctable in place (ruling **R-ED**) while its FIGURE was refused
    outright, so restating what the bank moved meant reverting the transfer --
    and a revert RETAINS the recorded figure, so the re-settle re-booked the old
    number over the re-planned one (developer ruling, 2026-08-17).

    The route DROPS a figure that arrives with an unsettling status (ruling
    **R-EG**, graded at the route tier); what these grade is the SERVICE, where
    a caller stating both facts on purpose is refused instead.
    """

    @staticmethod
    def _settled_transfer(td, amount="250.00"):
        """Return a settled transfer and both its legs."""
        xfer = transfer_service.create_transfer(
            transfer_service.TransferSpec(
                user_id=td["user"].id,
                from_account_id=td["account"].id,
                to_account_id=td["savings_account"].id,
                pay_period_id=td["periods"][0].id,
                scenario_id=td["scenario"].id,
                amount=Decimal(amount),
                status_id=td["projected_status"].id,
                category_id=td["categories"]["Rent"].id,
            ),
        )
        db.session.flush()
        transfer_service.update_transfer(
            xfer.id, td["user"].id,
            status_id=ref_cache.status_id(StatusEnum.DONE),
        )
        db.session.flush()
        return xfer

    @staticmethod
    def _legs(xfer_id):
        return (
            db.session.query(Transaction)
            .filter_by(transfer_id=xfer_id, is_deleted=False)
            .order_by(Transaction.id)
            .all()
        )

    def test_a_correction_records_on_both_legs_and_on_neither_parent(
        self, app, db, transfer_data,
    ):
        """Transfer Invariant 3 for the settlement record.

        A transfer's money moves on its two legs, so each records its own and
        the two are equal -- exactly as their settle day is.  The PARENT carries
        no such column, which is what ``apply_status_to_all_three`` enforces by
        passing the record only to the shadows.
        """
        with app.app_context():
            td = transfer_data
            xfer = self._settled_transfer(td)
            day = self._legs(xfer.id)[0].settled_on

            transfer_service.update_transfer(
                xfer.id, td["user"].id, settled_amount=Decimal("263.11"),
            )
            db.session.flush()

            for leg in self._legs(xfer.id):
                assert settled_figure(leg) == Decimal("263.11")
                assert leg.settled_basis_id == settlement_basis_id(
                    SettlementBasisEnum.CORRECTED,
                )
                assert leg.settled_on == day, (
                    "a figure correction moved the pair's day"
                )
            # The PARENT went through the seam too, and came out on the same
            # status as its legs (Transfer Invariant 4).  It deliberately does
            # NOT assert ``not hasattr(xfer, "settled_amount")``: the seam gates
            # its whole record block on ``isinstance(row, Transaction)``, so
            # handing the parent a record is a silent no-op and only a SCHEMA
            # change could fail such an assertion -- the "``hasattr`` is not a
            # test" shape plan step X-aa already paid for (neutral review,
            # 2026-08-18).
            assert all(
                leg.status_id == xfer.status_id for leg in self._legs(xfer.id)
            ), "the correction moved the legs' status away from the parent's"

    def test_a_figure_on_an_UNSETTLED_transfer_is_refused_untouched(
        self, app, db, transfer_data,
    ):
        """An amount states what MOVED, and a Projected pair's money has not.

        Refused BEFORE any field is written, which is why the gate sits at the
        top of ``_apply_transfer_updates`` beside the loan-move refusal rather
        than among the field arms: ``is_override`` and ``amount`` are staged two
        statements later, so a refusal further down would leave a partially
        edited transfer behind a raised exception.

        Shown to FIRE: the accompanying ``amount`` is asserted UNWRITTEN, so a
        gate moved below the field loop fails this test rather than passing it.
        """
        with app.app_context():
            td = transfer_data
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount=Decimal("250.00"),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                ),
            )
            db.session.flush()

            assert xfer.is_override is False, "fixture precondition"

            with pytest.raises(ValidationError) as exc:
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    is_override=True,
                    amount=Decimal("999.00"),
                    settled_amount=Decimal("50.00"),
                )

            assert "has nothing to record" in str(exc.value)
            # BOTH pre-settle writes are asserted untouched, and both are
            # needed: ``is_override`` is applied FIRST and ``amount`` second, so
            # a gate that slipped BETWEEN them would leave one written and pass
            # a test that checked only the other.
            assert xfer.is_override is False, (
                "the refusal ran AFTER the is_override write"
            )
            assert xfer.amount == Decimal("250.00"), (
                "the refusal ran AFTER the amount write"
            )
            for leg in self._legs(xfer.id):
                assert leg.settled_amount is None
                assert leg.settled_basis_id is None

    def test_an_echo_leaves_the_derived_basis_standing(
        self, app, db, transfer_data,
    ):
        """Re-posting the prefilled figure must not manufacture a correction.

        The basis is the only stored signal that a human read a number off a
        statement, and this form submits every input it renders on every save.
        """
        with app.app_context():
            td = transfer_data
            xfer = self._settled_transfer(td)
            derived = settlement_basis_id(SettlementBasisEnum.DERIVED)
            assert all(
                leg.settled_basis_id == derived for leg in self._legs(xfer.id)
            )

            transfer_service.update_transfer(
                xfer.id, td["user"].id, settled_amount=Decimal("250.00"),
            )
            db.session.flush()

            for leg in self._legs(xfer.id):
                assert leg.settled_basis_id == derived

    def test_a_settle_still_owns_a_figure_arriving_with_it(
        self, app, db, transfer_data,
    ):
        """The correction door must not have stolen the SETTLE's figure.

        A figure riding a settling ``status_id`` is the settle's own
        (``_SETTLE_OWNED_FIELDS``), subject to its echo rule -- and the settle
        writes the status, the pair's day and the record as ONE act.  If the
        correction arm had taken it instead, the figure would be written twice
        and the freeze would be resolved after the status flip, where it always
        answers ``None``.
        """
        with app.app_context():
            td = transfer_data
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount=Decimal("250.00"),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                ),
            )
            db.session.flush()

            # Driven through the NAMED verb, because its return value is the
            # one observable that separates the two arms.  Asserting the end
            # state alone cannot: the echo rule makes a figure taken by the
            # CORRECTION arm instead converge on a byte-identical row (the
            # settle would record the plan, the correction would overwrite it),
            # so an end-state-only test passes either way.  ``settle_transfer``
            # reports whether the SETTLE booked a human's figure, and that is
            # False the moment the settle stops owning it (neutral review,
            # 2026-08-18).
            corrected = transfer_service.settle_transfer(
                xfer.id, td["user"].id, submitted=Decimal("241.00"),
            )
            db.session.flush()

            assert corrected is True, (
                "the SETTLE did not book the figure -- the correction arm took "
                "it, so the freeze was resolved after the status flip"
            )
            for leg in self._legs(xfer.id):
                assert settled_figure(leg) == Decimal("241.00")
                assert leg.settled_basis_id == settlement_basis_id(
                    SettlementBasisEnum.CORRECTED,
                ), "a figure that differs from the plan IS a correction"
                assert leg.settled_on is not None


class TestMovingATransferBetweenAccounts:
    """``update_transfer``'s ENDPOINT arm (plan step R10-b).

    A transfer's source and destination are two of the six columns a recurring
    definition states, and this door could write only four of them -- so a
    definition's account change reached its generated rows by DESTROYING and
    re-creating every one of them, and a NON-repeating transfer refused the same
    edit outright because nothing could carry it.  The arm moves the parent and
    BOTH legs, re-derives each leg's display name, and reconciles the ledger on
    both sides of the move.
    """

    def _other_savings(self, td, name="Second Savings"):
        """Create a second savings account for this owner.

        Through ``account_service.create_account`` rather than an ``Account``
        insert, because that factory is what PAIRS the account with its
        chart-of-accounts ledger -- and an endpoint move posts to that ledger,
        so a hand-rolled row fails the move with a missing-pairing
        ``PostingError`` rather than exercising it.
        """
        savings_type = (
            db.session.query(AccountType).filter_by(name="Savings").one()
        )
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=td["user"].id,
                account_type_id=savings_type.id,
                name=name,
                anchor_balance=Decimal("0.00"),
            ),
        )
        db.session.flush()
        return account

    def _legs(self, xfer):
        """Return this transfer's ``(expense, income)`` shadows."""
        expense_type = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
        shadows = db.session.query(Transaction).filter_by(
            transfer_id=xfer.id, is_deleted=False,
        ).all()
        assert len(shadows) == 2
        expense = [s for s in shadows if s.transaction_type_id == expense_type]
        income = [s for s in shadows if s.transaction_type_id != expense_type]
        return expense[0], income[0]

    def test_the_destination_move_carries_the_income_leg_and_its_name(
        self, app, db, transfer_data
    ):
        """Moving the destination moves the income leg and re-derives its label.

        The expense leg stays on the unchanged source, but its NAME says where
        the money is going -- so it is re-derived too.  A leg on the new account
        still labelled with the old one is a row that contradicts itself, and
        the delete-and-recreate this replaces got that right for free by
        rebuilding the pair.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            elsewhere = self._other_savings(td)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, to_account_id=elsewhere.id,
            )
            db.session.flush()

            assert xfer.from_account_id == td["account"].id
            assert xfer.to_account_id == elsewhere.id
            expense, income = self._legs(xfer)
            assert expense.account_id == td["account"].id
            assert income.account_id == elsewhere.id
            assert expense.name == f"Transfer to {elsewhere.name}"
            assert income.name == f"Transfer from {td['account'].name}"

    def test_the_source_move_carries_the_expense_leg_and_its_name(
        self, app, db, transfer_data
    ):
        """The mirror of the case above, on the other leg."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            elsewhere = self._other_savings(td, name="New Source")

            transfer_service.update_transfer(
                xfer.id, td["user"].id, from_account_id=elsewhere.id,
            )
            db.session.flush()

            assert xfer.from_account_id == elsewhere.id
            assert xfer.to_account_id == td["savings_account"].id
            expense, income = self._legs(xfer)
            assert expense.account_id == elsewhere.id
            assert income.account_id == td["savings_account"].id
            assert expense.name == (
                f"Transfer to {td['savings_account'].name}"
            )
            assert income.name == f"Transfer from {elsewhere.name}"

    def test_an_update_naming_no_account_leaves_the_pair_where_it_is(
        self, app, db, transfer_data
    ):
        """The firing control: an ordinary edit moves neither leg nor name.

        Both legs' names are derived from the endpoints, so an arm that
        re-derived them unconditionally would look correct here -- until an
        owner renamed an account and every unrelated edit silently rewrote the
        labels of rows it was not asked about.  The arm is gated on a real
        MOVE, and this pins that.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            expense, income = self._legs(xfer)
            before = (expense.name, income.name)
            td["savings_account"].name = "Renamed Since"
            db.session.flush()

            transfer_service.update_transfer(
                xfer.id, td["user"].id, amount=Decimal("300.00"),
            )
            db.session.flush()

            assert xfer.from_account_id == td["account"].id
            assert xfer.to_account_id == td["savings_account"].id
            expense, income = self._legs(xfer)
            assert (expense.name, income.name) == before

    def test_a_move_that_would_make_the_endpoints_equal_is_refused(
        self, app, db, transfer_data
    ):
        """A transfer between one account and itself moves no money.

        ``ck_transfers_different_accounts`` says so at the storage tier; the
        door says it first, so the refusal is a message rather than an
        IntegrityError that rolls back the whole enclosing transaction.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            with pytest.raises(ValidationError, match="must be different"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    to_account_id=td["account"].id,
                )
            db.session.rollback()

    def test_a_move_making_a_loan_the_SOURCE_is_refused(
        self, app, db, transfer_data, seed_user
    ):
        """A transfer OUT of a loan is a disbursement, which is not modelled.

        The create path has refused it since the loan ledger was built
        (``_reject_transfer_out_of_loan``); the edit path could not, because it
        could not move a source at all.  Now it can, so it inherits the rule --
        otherwise the one guard would have a second door straight past it.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            loan = create_loan_account(
                {"user": td["user"], "account": td["account"]}, db.session,
                name="Refusing Loan",
                origination_date=date(2020, 1, 1),
            )

            with pytest.raises(ValidationError, match="out of a loan"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, from_account_id=loan.id,
                )
            db.session.rollback()

    def test_a_legacy_loan_SOURCE_row_can_still_move_its_destination(
        self, app, db, transfer_data
    ):
        """The source refusal is asked about a source MOVE, not about every edit.

        **Found by an adversarial review of plan step R10-b.**  Asked on any
        endpoint move, ``_reject_transfer_out_of_loan`` re-graded an arrangement
        the edit does not touch: a transfer whose SOURCE is an amortizing loan
        -- written before ``create_transfer`` guarded it -- could not move its
        DESTINATION either, which froze exactly the legacy row that
        ``_reject_installment_move_before_loan`` states the discipline against.
        It also made the vacated-SOURCE arm of ``_resync_vacated_loan``
        unreachable, so that function's stated reason for taking both endpoints
        was false.

        The legacy state is PLANTED, because the create door no longer produces
        it -- which is the point: the row exists on data written before the
        guard, and an edit must not be refused for a shape it does not change.
        """
        with app.app_context():
            td = transfer_data
            loan = create_loan_account(
                {"user": td["user"], "account": td["account"]}, db.session,
                name="Legacy Source Loan",
                origination_date=date(2020, 1, 1),
            )
            elsewhere = self._other_savings(td, name="New Destination")
            xfer = _create_basic_transfer(td)
            expense, _ = self._legs(xfer)
            # The pre-guard shape: money leaving a loan.
            xfer.from_account = loan
            expense.account = loan
            db.session.flush()

            transfer_service.update_transfer(
                xfer.id, td["user"].id, to_account_id=elsewhere.id,
            )
            db.session.flush()

            assert xfer.from_account_id == loan.id, (
                "the source it did not touch stayed where it was"
            )
            assert xfer.to_account_id == elsewhere.id
            _, income = self._legs(xfer)
            assert income.account_id == elsewhere.id

            # And moving that source OFF the loan is the edit the narrowing
            # makes reachable at all -- it is the vacated-SOURCE arm of
            # ``_resync_vacated_loan``, whose stated reason for taking both
            # endpoints was false while the guard refused every such row.
            before_entries = db.session.query(JournalEntry).count()
            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                from_account_id=td["account"].id,
            )
            db.session.flush()
            assert xfer.from_account_id == td["account"].id
            expense, _ = self._legs(xfer)
            assert expense.account_id == td["account"].id
            # The loan it left was reconciled by the move: a follow-up sync
            # writes nothing.
            after_move = db.session.query(JournalEntry).count()
            loan_posting_service.sync_loan_postings(
                loan.id, td["scenario"].id,
            )
            db.session.flush()
            assert db.session.query(JournalEntry).count() == after_move, (
                "the loan the source left was not reconciled"
            )
            assert after_move >= before_entries

    def test_a_move_onto_a_loan_is_graded_against_THAT_loan_s_origination(
        self, app, db, transfer_data
    ):
        """Ruling R-C follows the destination, not just the dates.

        A payment sitting comfortably after one loan's origination can sit
        BEFORE another's without its own installment moving at all, so an
        endpoint move re-asks the question even when no date field is in the
        payload.  A payment the fold would ERASE is refused here rather than
        silently splitting against a zero balance while the cash side debits
        checking in full.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            loan = create_loan_account(
                {"user": td["user"], "account": td["account"]}, db.session,
                name="Late Loan",
                origination_date=date(2099, 1, 1),
            )

            with pytest.raises(ValidationError, match="before it originates"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, to_account_id=loan.id,
                )
            db.session.rollback()

    def test_a_move_to_another_user_s_account_is_not_found(
        self, app, db, transfer_data, seed_second_user
    ):
        """Cross-user endpoints answer 404, not 400 (the security response rule).

        The service tier checks its own ownership so a caller that skips the
        route cannot re-point a transfer across an ownership line, and the
        message is identical to a missing account's -- no existence oracle.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            with pytest.raises(NotFoundError):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    to_account_id=seed_second_user["account"].id,
                )
            db.session.rollback()

    def test_a_refused_amount_does_not_move_the_pair_first(
        self, app, db, transfer_data
    ):
        """A refusal LATER in the payload still leaves all three rows untouched.

        **RE-WRITTEN after an adversarial review of plan step R10-b showed the
        first version could not fire.**  It paired a valid amount with an
        endpoint refusal, so the refusal it triggered was ``_resolve_endpoints``'
        own -- the FIRST gate in the function, and green however the writes
        below were ordered.  The property worth pinning is the opposite pairing:
        a VALID move beside an ILLEGAL amount.  The amount's refusal used to sit
        at the arm that assigns it, two writes after the endpoint move and after
        a loan payment's split reversal, so this exact payload moved the pair
        and reversed a ledger correction before deciding the edit was illegal.

        Asserts the whole pair, not just the parent: a half-applied move leaves
        the legs on accounts the money does not move between, and their display
        names saying so.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            elsewhere = self._other_savings(td)
            expense, income = self._legs(xfer)
            before = (
                xfer.from_account_id, xfer.to_account_id, xfer.amount,
                expense.account_id, income.account_id,
                expense.name, income.name,
            )

            with pytest.raises(ValidationError, match="must be positive"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    to_account_id=elsewhere.id,
                    amount=Decimal("-5.00"),
                )

            expense, income = self._legs(xfer)
            assert (
                xfer.from_account_id, xfer.to_account_id, xfer.amount,
                expense.account_id, income.account_id,
                expense.name, income.name,
            ) == before

    def test_a_refused_move_leaves_all_three_rows_untouched(
        self, app, db, transfer_data
    ):
        """An endpoint refusal itself writes nothing either.

        The complementary half: the endpoint gates are read-only, so a move
        refused for its OWN reason -- here, endpoints that would end up equal --
        leaves the pair exactly as it was.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            expense, income = self._legs(xfer)
            before = (
                xfer.from_account_id, xfer.to_account_id, xfer.amount,
                expense.account_id, income.account_id,
                expense.name, income.name,
            )

            with pytest.raises(ValidationError, match="must be different"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    amount=Decimal("999.00"),
                    to_account_id=td["account"].id,
                )

            expense, income = self._legs(xfer)
            assert (
                xfer.from_account_id, xfer.to_account_id, xfer.amount,
                expense.account_id, income.account_id,
                expense.name, income.name,
            ) == before

    def test_moving_a_settled_payment_off_a_loan_reconciles_the_loan_it_left(
        self, app, db, transfer_data
    ):
        """The accounts a move VACATES are reconciled, and reconciled LAST.

        ``sync_transfer_postings`` heals the LEGS by itself, but the two things
        it emits that are scoped to the transfer's CURRENT endpoints reach no
        further: its Step-5 anchor self-heal names ``(from_account_id,
        to_account_id)``, and a vacated LOAN's genesis ledger and recurring
        payment window still count a payment it no longer has.  Both are
        re-derived explicitly, after the cash reconcile has moved the legs.

        **The ORDER is pinned, but not by this case** -- an adversarial review
        of R10-b measured which tests fail when the vacated walk is hoisted
        above the cash reconcile, and this is not one of them.  The three that
        are: ``test_a_vacated_ACCOUNT_s_anchor_corrections_are_re_derived``,
        ``test_moving_a_settled_payment_ONTO_a_loan_reconciles_that_loan`` and
        ``test_a_settled_transfer_s_cash_legs_follow_the_move``.  Run first, the
        vacated walk reads a ledger still holding a net for a transfer with no
        shadow on that account and ``walk_account_ledger`` raises
        ``PostingError: Ledger account N holds a nonzero net for transfer ids
        [...] but no active shadow on account M resolves them; Transfer
        Invariant 1 is broken`` -- measured on a production clone before the
        order was fixed.  What THIS case pins is the pre-move split reversal:
        remove it and the loan keeps a correction for a payment it no longer
        has.
        """
        with app.app_context():
            td = transfer_data
            loan = create_loan_account(
                {"user": td["user"], "account": td["account"]}, db.session,
                name="Vacated Loan",
                origination_date=date(2020, 1, 1),
            )
            elsewhere = self._other_savings(td, name="Not A Loan")
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=loan.id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount=Decimal("250.00"),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                ),
            )
            transfer_service.settle_transfer(
                xfer.id, td["user"].id, settle_day=an_entered_day(display_today()),
            )
            db.session.flush()
            posted = _ledger_nets_for_transfer(xfer.id)
            assert len(posted) == 2, (
                "setup: a settled payment posts a cash leg on each side"
            )

            # The move itself is the assertion: a wrong order raises here, and
            # ``sync_loan_postings``' checked-projection assert would raise if
            # the loan's ledger and its walk disagreed afterwards.
            transfer_service.update_transfer(
                xfer.id, td["user"].id, to_account_id=elsewhere.id,
            )
            db.session.flush()

            assert xfer.to_account_id == elsewhere.id
            after = _ledger_nets_for_transfer(xfer.id)
            assert sum(after.values()) == Decimal("0")
            # The loan's own ledger is left holding nothing of this transfer.
            assert set(after).isdisjoint(
                {ledger for ledger, net in posted.items() if net > 0}
            )

            # **The oracle for the vacated reconcile itself**: both walks are
            # idempotent, so re-running each one now must write NOTHING.  It is
            # the only assertion that fails when the vacated arm is DELETED
            # rather than merely mis-ordered -- the transfer's own legs
            # reconcile either way, and what is left behind is the LOAN's split
            # correction for a payment it no longer has, plus the vacated
            # account's own anchor corrections.  Without this the arm had no
            # firing control at all: removing it kept every other case green.
            before_entries = db.session.query(JournalEntry).count()
            loan_posting_service.sync_loan_postings(
                loan.id, td["scenario"].id,
            )
            account_posting_service.sync_account_anchor_postings(
                loan.id, td["scenario"].id,
            )
            db.session.flush()
            assert db.session.query(JournalEntry).count() == before_entries, (
                "the loan the payment LEFT was not reconciled by the move"
            )

    def test_moving_a_settled_payment_ONTO_a_loan_reconciles_that_loan(
        self, app, db, transfer_data
    ):
        """The loan a payment JOINS is re-derived, not just the one it left.

        **The firing control for assigning the RELATIONSHIP rather than the
        foreign key.**  ``_sync_loan_postings_if_loan`` asks which account the
        transfer's destination IS, and SQLAlchemy does not refresh
        ``Transfer.to_account`` when only ``to_account_id`` moves -- not even
        across the following flush.  Measured before the fix: after re-pointing
        a Mortgage payment at savings, ``xfer.to_account.name`` still answered
        ``Mortgage``.  So a move OFF a loan reconciled the old loan by accident
        and a move ONTO one -- this case -- reconciled NOTHING, leaving the new
        loan's genesis ledger without the payment it had just received.

        The oracle is idempotence: both walks write nothing at a state they have
        already reconciled, so a second sync that WRITES means the move did not
        run the first.
        """
        with app.app_context():
            td = transfer_data
            loan = create_loan_account(
                {"user": td["user"], "account": td["account"]}, db.session,
                name="Joined Loan",
                origination_date=date(2020, 1, 1),
            )
            xfer = _create_basic_transfer(td)
            transfer_service.settle_transfer(
                xfer.id, td["user"].id, settle_day=an_entered_day(display_today()),
            )
            db.session.flush()

            transfer_service.update_transfer(
                xfer.id, td["user"].id, to_account_id=loan.id,
            )
            db.session.flush()

            assert xfer.to_account_id == loan.id
            assert xfer.to_account.id == loan.id, (
                "the relationship must move with the column"
            )
            before_entries = db.session.query(JournalEntry).count()
            loan_posting_service.sync_loan_postings(
                loan.id, td["scenario"].id,
            )
            db.session.flush()
            assert db.session.query(JournalEntry).count() == before_entries, (
                "the loan the payment JOINED was not reconciled by the move"
            )

    def test_a_vacated_LOAN_s_downstream_payments_are_re_split(
        self, app, db, transfer_data
    ):
        """Removing one payment re-derives the ones whose balance it moved.

        **The firing control for the post-move vacated reconcile.**  The
        pre-move reversal takes back the DEPARTING payment's own split; every
        LATER payment on that loan rode the running balance the departing one
        advanced, so their splits are stale the moment it goes.  That is
        exactly what ``_resync_loan_after_payment_left`` exists for on the
        delete path, and an endpoint move needs it for the identical reason.

        The oracle is idempotence plus the loan's own checked-projection
        assert: re-running ``sync_loan_postings`` at a reconciled state writes
        nothing, and refuses to commit at an unreconciled one.
        """
        with app.app_context():
            td = transfer_data
            loan = create_loan_account(
                {"user": td["user"], "account": td["account"]}, db.session,
                name="Two Payment Loan",
                origination_date=date(2020, 1, 1),
                principal=Decimal("5000.00"),
            )
            elsewhere = self._other_savings(td, name="Off The Loan")
            payments = []
            for index in (0, 1):
                payment = transfer_service.create_transfer(
                    transfer_service.TransferSpec(
                        user_id=td["user"].id,
                        from_account_id=td["account"].id,
                        to_account_id=loan.id,
                        pay_period_id=td["periods"][index].id,
                        scenario_id=td["scenario"].id,
                        amount=Decimal("400.00"),
                        status_id=td["projected_status"].id,
                        category_id=td["categories"]["Rent"].id,
                        due_date=td["periods"][index].start_date,
                    ),
                )
                transfer_service.settle_transfer(
                    payment.id, td["user"].id, settle_day=an_entered_day(display_today()),
                )
                payments.append(payment)
            db.session.flush()

            transfer_service.update_transfer(
                payments[0].id, td["user"].id, to_account_id=elsewhere.id,
            )
            db.session.flush()

            before_entries = db.session.query(JournalEntry).count()
            loan_posting_service.sync_loan_postings(
                loan.id, td["scenario"].id,
            )
            db.session.flush()
            assert db.session.query(JournalEntry).count() == before_entries, (
                "the surviving payment's split was left riding a balance the "
                "departed payment no longer advances"
            )

    def test_a_vacated_ACCOUNT_s_anchor_corrections_are_re_derived(
        self, app, db, transfer_data
    ):
        """An account the money stopped passing through re-bases its assertion.

        The other half of the post-move vacated reconcile, and the half
        ``sync_transfer_postings`` cannot reach: its Step-5 self-heal names the
        transfer's CURRENT endpoints, so the account just vacated is outside it.
        An anchor correction is "what the owner asserted minus what the ledger
        walked", and the walk on that account just lost this transfer's leg --
        so the correction is stale until something re-derives it.

        The true-up is dated AFTER the settle deliberately: an assertion made
        BEFORE the money moved does not depend on it, so the correction would
        not move and the case would pass whether or not the arm ran.
        """
        with app.app_context():
            td = transfer_data
            savings = td["savings_account"]
            elsewhere = self._other_savings(td, name="Third Savings")
            xfer = _create_basic_transfer(td)
            transfer_service.settle_transfer(
                xfer.id, td["user"].id, settle_day=an_entered_day(display_today()),
            )
            db.session.flush()
            add_anchor_history(
                db.session, savings, td["periods"][0], Decimal("900.00"),
            )
            account_posting_service.sync_account_anchor_postings(
                savings.id, td["scenario"].id,
            )
            db.session.flush()

            transfer_service.update_transfer(
                xfer.id, td["user"].id, to_account_id=elsewhere.id,
            )
            db.session.flush()

            before_entries = db.session.query(JournalEntry).count()
            account_posting_service.sync_account_anchor_postings(
                savings.id, td["scenario"].id,
            )
            db.session.flush()
            assert db.session.query(JournalEntry).count() == before_entries, (
                "the vacated account's anchor correction still counts a "
                "transfer that no longer passes through it"
            )

    def test_a_move_and_a_settle_in_ONE_call_settle_against_the_new_loan(
        self, app, db, transfer_data
    ):
        """A move and a settle in ONE call land on the destination it LEAVES.

        The two acts compose: the cash legs post against the loan the transfer
        is re-pointed at, and that loan books the payment's split -- asserted by
        idempotence, since a follow-up ``sync_loan_postings`` that WRITES means
        the call did not.

        **It does not pin the ORDER of the endpoint apply against the settle
        dispatch, and that is said because it was checked**: moving the apply
        after the dispatch leaves this case green.  The one reader that would
        answer differently is the auto-derived loan freeze, which needs a
        template-linked derive-mode payment re-pointed between two loans while
        settling -- unreachable from any route and from the maintain pass.  See
        ``_endpoints._apply_endpoint_move`` for why the order is kept anyway.
        """
        with app.app_context():
            td = transfer_data
            loan = create_loan_account(
                {"user": td["user"], "account": td["account"]}, db.session,
                name="One Call Loan",
                origination_date=date(2020, 1, 1),
                principal=Decimal("5000.00"),
            )
            xfer = _create_basic_transfer(td)
            db.session.flush()
            before = _ledger_nets_for_transfer(xfer.id)
            assert before == {}, "setup: a projected transfer posts nothing"

            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                to_account_id=loan.id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.flush()

            assert xfer.to_account_id == loan.id
            # The cash legs posted, and against the LOAN's ledger.
            after = _ledger_nets_for_transfer(xfer.id)
            assert len(after) == 2
            assert sum(after.values()) == Decimal("0")
            # And the loan booked the payment: a follow-up sync is a no-op, so
            # the split the settle owed was written during the call.
            before_entries = db.session.query(JournalEntry).count()
            loan_posting_service.sync_loan_postings(
                loan.id, td["scenario"].id,
            )
            db.session.flush()
            assert db.session.query(JournalEntry).count() == before_entries, (
                "the settle did not book this payment against the loan it "
                "was re-pointed at"
            )

    def test_a_settled_transfer_s_cash_legs_follow_the_move(
        self, app, db, transfer_data
    ):
        """The posted ledger moves with the money it is about.

        ``reconcile_periods`` takes the per-ledger-account delta over the UNION
        of what is posted and what is targeted, so the vacated ledger account
        reverses to zero in the same pass the new one posts -- one pass, no
        residue.  Asserted on the NETS rather than on entry counts, because what
        must be true is that no account is left holding money the transfer no
        longer moves through it.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            elsewhere = self._other_savings(td)
            transfer_service.settle_transfer(
                xfer.id, td["user"].id, settle_day=an_entered_day(display_today()),
            )
            db.session.flush()
            vacated = _ledger_nets_for_transfer(xfer.id)
            assert len(vacated) == 2, "setup: a settled transfer posts two legs"

            transfer_service.update_transfer(
                xfer.id, td["user"].id, to_account_id=elsewhere.id,
            )
            db.session.flush()

            after = _ledger_nets_for_transfer(xfer.id)
            assert len(after) == 2
            # The MAGNITUDE, not just the shape: the transfer is $250.00, so
            # the source ledger holds -250.00 (money leaving) and the ledger of
            # whichever account the money now arrives at holds +250.00.  An
            # adversarial review of this step found the first version asserting
            # only "two legs summing to zero", which a mutation posting $125.00
            # each way would have passed.
            [(source_ledger, source_net)] = [
                (ledger, net) for ledger, net in vacated.items() if net < 0
            ]
            assert source_net == Decimal("-250.00")
            assert after[source_ledger] == Decimal("-250.00")
            destination = [
                ledger for ledger in after if ledger != source_ledger
            ]
            assert len(destination) == 1
            assert after[destination[0]] == Decimal("250.00")
            # And it is a DIFFERENT ledger than the one it left, which is at
            # zero and so absent from the nets entirely.
            assert destination[0] not in vacated
            assert sum(after.values()) == Decimal("0")
