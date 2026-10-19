"""The create door, transfer_service.create_transfer: the one transfer it writes with its fields,
category, template, name and default name, and the inputs it refuses (a zero, negative or
unparseable amount, the same account twice, a transfer out of a loan, another user's account,
period or category, and a missing account).
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.transfer import Transfer
from app.services import transfer_service
from app.exceptions import NotFoundError, ValidationError
from tests._test_helpers import (
    create_loan_account,
    generate_transfer_of,
    record_paydays_across_a_hole,
    rhythm_of,
    transfer_amount,
    transfer_side_record,
)
from app.models.amount_ownership import AmountOwnership

from ._helpers import _create_basic_transfer, _reread


# ── Creation Tests ─────────────────────────────────────────────────


class TestCreateTransfer:
    """Tests for transfer_service.create_transfer."""

    def test_produces_one_transfer_with_correct_fields(
        self, app, db, transfer_data,
    ):
        """create_transfer writes ONE row, the transfer, carrying the spec's fields.

        It counted two shadow rows and asserted these fields on each until
        plan step ``balance:X-bi-6-4d-3``, whose create door stopped writing
        them: a transfer is one row and its two legs are derived from it
        (ruling R-BAL167 class 1; one leg per side is
        ``test_transfer_legs/test_the_plan_half.py``'s
        ``TestTransferLegs::test_each_account_gets_its_own_side``).  It also
        asked each shadow for a recorded
        settlement, and asks each SIDE now: a Projected create records
        nothing on either side.  That read is the case's own: a create that
        hands the status seam a settled status for an unsettled transfer
        records the transfer's figure un-dated on both sides, passes every
        read above it, and fails this one first (measured at that step's
        checkpoint 2e-2a-1a, by the mutant its review named R-PROJ-RECORD).
        Each field is read off the row re-read from the database since that
        step's checkpoint 2e-2b-1b-2, where it read the object the door
        returned (ruling R-BAL290 part 1).  Renamed from
        ``test_produces_two_shadows``.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id

            row = _reread(xfer_id)
            assert transfer_amount(row) == Decimal("250.00")
            assert row.status_id == td["projected_status"].id
            assert row.pay_period_id == td["periods"][0].id
            assert row.scenario_id == td["scenario"].id
            assert row.transfer_template_id is None
            assert row.is_override is False
            assert row.is_deleted is False
            assert row.from_account_id == td["account"].id
            assert row.to_account_id == td["savings_account"].id
            for account_id in (row.from_account_id, row.to_account_id):
                assert transfer_side_record(
                    db.session, xfer_id, account_id,
                ) is None

    def test_with_category(self, app, transfer_data):
        """The transfer files under the user-selected category.

        It asserted the two shadow rows' categories, and nothing on the
        transfer, until plan step ``balance:X-bi-6-4d-3``; the transfer holds
        the category now (ruling R-BAL167 class 1).  It also read both legs'
        categories until that step's checkpoint 2e-2b-1b-2, which reads the
        row re-read from the database instead (ruling R-BAL290 part 1); what
        a side shows of the row's category is ``test_transfer_legs/test_what_a_side_shows.py``'s
        ``TestBothSidesShowTheTransfer``.
        """
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
                    amount_ownership=AmountOwnership.own(Decimal("500.00")),
                    status_id=td["projected_status"].id,
                    category_id=rent_cat.id,
                ),
            )

            assert _reread(xfer.id).category_id == rent_cat.id

    def test_with_template_id(self, app, transfer_data):
        """A template-linked transfer holds its link.

        The linked transfer is the ENGINE's (:func:`generate_transfer_of`,
        plan step balance:X-ch), which reaches ``create_transfer`` with the
        link in its spec the way the application does: a GENERATED transfer
        of the fixture's definition, whose rule recurs every period.  It
        also asked each side for a definition until plan step
        ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-1b-2, which reads the row
        re-read from the database instead (ruling R-BAL290 part 1): that a
        side names no TRANSACTION definition whatever its transfer's shape
        (the grid's row-key contract) is ``test_transfer_legs/test_what_a_side_shows.py``'s
        ``TestALegsDisplayProjections``, which asks it in memory of a
        generated, a placed and an ad-hoc transfer and, saved, of an
        engine-generated one while it is still Projected (this read's own
        state, asked there since checkpoint 2e-2b-1b-2c) and again once it
        is settled, and that file's ``TestBothSidesShowTheTransfer``, which
        asks it of committed ad-hoc transfers.
        **The link read is not graded by a mutant**: dropping the link at
        the create is refused there by the transfers' CHECK, so that mutant
        never reaches the read (measured on this read and on the one before
        it: the create raises first).
        """
        with app.app_context():
            td = transfer_data
            xfer_id = generate_transfer_of(
                td["transfer_template"], td["periods"][0],
            ).id

            assert _reread(xfer_id).transfer_template_id == (
                td["transfer_template"].id
            )

    def test_with_custom_name(self, app, transfer_data):
        """A custom name names the transfer.

        It asked the two shadow rows' names until plan step
        ``balance:X-bi-6-4d-3``, and from that step's create door on the loop
        over them ran over none (a vacuous read, measured at its checkpoint
        2e-2a-1).  It then asked each side's label, exactly, until that
        step's checkpoint 2e-2b-1b-2, which reads the row re-read from the
        database instead (ruling R-BAL290 part 1): that a custom name leaves
        each side's label composed from the endpoints is
        ``test_transfer_legs/test_what_a_side_shows.py``'s ``TestALegsDisplayProjections``, whose
        three transfers each carry one.
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
                    amount_ownership=AmountOwnership.own(Decimal("300.00")),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                    name="Mortgage Payment",
                ),
            )

            assert _reread(xfer.id).name == "Mortgage Payment"

    def test_returns_transfer_object(self, app, db, transfer_data):
        """create_transfer returns a Transfer with a valid ID."""
        with app.app_context():
            xfer = _create_basic_transfer(transfer_data)
            assert isinstance(xfer, Transfer)
            assert xfer.id is not None

    def test_default_name_generated(self, app, transfer_data):
        """Without a name, transfer gets 'from_account to to_account'.

        Read off the row re-read from the database since plan step
        ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-1b-2, where it read the
        object the door returned (ruling R-BAL290 part 1), and asked that
        each account's name was IN it: a default naming the two accounts
        the other way round passed (measured there), so it asks the whole
        name now.
        """
        with app.app_context():
            td = transfer_data
            name = _reread(_create_basic_transfer(td).id).name
            assert name == (
                f"{td['account'].name} to {td['savings_account'].name}"
            )


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
                        amount_ownership=AmountOwnership.own(Decimal("0")),
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
                        amount_ownership=AmountOwnership.own(Decimal("-100")),
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
                        amount_ownership=AmountOwnership.own(Decimal("100")),
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
                        amount_ownership=AmountOwnership.own(Decimal("100")),
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
                        amount_ownership=AmountOwnership.own(Decimal("100")),
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
                        amount_ownership=AmountOwnership.own(Decimal("100")),
                        status_id=td["projected_status"].id,
                        category_id=td["categories"]["Rent"].id,
                    ),
                )

    def test_wrong_user_period_rejected(self, app, db, transfer_data, second_user):
        """Period belonging to another user raises NotFoundError."""
        with app.app_context():
            td = transfer_data
            # Create a period for the second user.
            other_periods = record_paydays_across_a_hole(
                user_id=second_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=2,
                rhythm=rhythm_of(14),
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
                        amount_ownership=AmountOwnership.own(Decimal("100")),
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
                        amount_ownership=AmountOwnership.own(Decimal("100")),
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
                        amount_ownership=AmountOwnership.own("not-a-number"),
                        status_id=td["projected_status"].id,
                        category_id=td["categories"]["Rent"].id,
                    ),
                )
