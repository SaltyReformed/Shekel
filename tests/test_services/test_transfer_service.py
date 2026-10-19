"""
Shekel Budget App -- Transfer Service Tests

Comprehensive tests for create_transfer, update_transfer, and
delete_transfer: what each door writes on the transfer's row, validation
rules, edge cases, and cross-user isolation.

A transfer is one stored row since plan step ``balance:X-bi-6-4d-3``, and its
two sides are read off it every time, so a door's test checks what the door
WROTE, re-read from the database, and the side's own payment record, the one
thing still stored per side (ruling R-BAL290 part 1, the developer,
2026-10-14).  The edit group, :class:`TestUpdateTransfer`, reads so since
that step's checkpoint 2e-2b-1b-1, and the create and figure groups,
:class:`TestCreateTransfer` and :class:`TestTheFigureCorrectionDoorOnAPair`,
since its checkpoint 2e-2b-1b-2, and the move group,
:class:`TestMovingATransferBetweenAccounts`, since its checkpoint 2e-2b-1b-3,
and the restore and due-date groups, :class:`TestRestoreTransfer` and
:class:`TestDueDateAndSettleDayShadows`, since its checkpoint 2e-2b-2b.  The
delete group, :class:`TestDeleteTransfer`, holds one case per state of the
transfer and kind of delete since that step's checkpoint 2e-2b-2a (ruling
R-BAL290 part 2), where it took in the already-hidden and paid groups.  What
each side shows of a Projected or
Cancelled row is the ``test_transfer_legs`` package's
``TestBothSidesShowTheTransfer``, and a settled row's price that package's
``TestTheLegMapProducers::test_a_settled_leg_records_its_movements_figure``
and ``TestTheLegContributionTwins::test_a_settled_leg_is_worth_its_record_and_not_its_plan``.
One leg per side is that package's
``TestTransferLegs::test_each_account_gets_its_own_side``, and which endpoint
each side is on its ``TestLegOf`` and the projection table.  The database
refuses a second side row: the expense side's in
``test_a_transfer_is_one_row.py``'s ``test_the_database_refuses_a_twin``,
both sides' in ``test_a_transfer_shadow_is_derived.py``'s
``test_the_transfer_owns_its_figure_and_neither_twin_can_be_stored``.  The
``TestInvariants`` class that checked them here was dissolved into those
tests and this file's edit group at checkpoint 2e-2b-1b-1.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    MovementFigureSourceEnum,
    StatusEnum,
)
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.journal_entry import JournalEntry, Posting
from app.models.ref import AccountType, Status
from app.models.transfer import Transfer
from app.services import (
    account_posting_service,
    account_service,
    loan_posting_service,
    transfer_service,
)
from app.services.transfer_legs import planned_transfer_legs
from app.utils.dates import display_today
from app.exceptions import NotFoundError, ValidationError
from tests._test_helpers import (
    add_anchor_history,
    an_entered_day,
    create_loan_account,
    generate_transfer_of,
    on_both_sides,
    record_paydays_across_a_hole,
    rhythm_of,
    transfer_amount,
    transfer_family_journal_filter,
    transfer_side_figure,
    transfer_side_record,
    transfer_side_settle_day,
    typed,
)
from app.models.amount_ownership import AmountOwnership


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
        .filter(transfer_family_journal_filter(transfer_id))
        .all()
    )
    nets = {}
    for ledger_account_id, amount in rows:
        nets[ledger_account_id] = nets.get(
            ledger_account_id, Decimal("0"),
        ) + amount
    return {key: net for key, net in nets.items() if net != 0}


def _the_day_before_today():
    """Return yesterday in the owner's timezone, a day an account's books can open on.

    Money a settle dates TODAY lands on an account whose books open no later
    than this.
    """
    return display_today() - timedelta(days=1)


def _create_basic_transfer(td):
    """Helper: create a transfer using the standard test data."""
    return transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=td["user"].id,
            from_account_id=td["account"].id,
            to_account_id=td["savings_account"].id,
            pay_period_id=td["periods"][0].id,
            scenario_id=td["scenario"].id,
            amount_ownership=AmountOwnership.own(Decimal("250.00")),
            status_id=td["projected_status"].id,
            category_id=td["categories"]["Rent"].id,
        ),
    )


def _reread(xfer_id):
    """Commit, expire every loaded object, and return the transfer's row re-read.

    What a door WROTE is what the database holds after the commit, not what
    the session's in-memory object says, so an edit case reads the row this
    returns (ruling R-BAL290 part 1).
    """
    db.session.commit()
    db.session.expire_all()
    return db.session.get(Transfer, xfer_id)


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


# ── Update Tests ───────────────────────────────────────────────────


class TestUpdateTransfer:
    """Tests for transfer_service.update_transfer."""

    def test_an_amount_edit_writes_the_transfers_amount(self, app, transfer_data):
        """An amount edit writes the new amount on the transfer's row.

        What the edit wrote, re-read from the database (ruling R-BAL290
        part 1).  It also priced both legs until plan step
        ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-1b-1; what a side shows of
        the row's amount is ``test_transfer_legs/test_what_a_side_shows.py``'s
        ``TestBothSidesShowTheTransfer`` now.  The one copy of a pair: the
        invariant group's ``test_amounts_always_match_after_update`` made the
        same edit at $777.77 and was deleted there.  Renamed from
        ``test_an_amount_edit_reprices_both_legs``, and before that
        ``test_amount_syncs_shadows``.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id

            transfer_service.update_transfer(
                xfer_id, td["user"].id,
                amount_ownership=AmountOwnership.own(Decimal("400.00")),
            )

            assert _reread(xfer_id).amount == Decimal("400.00")

    def test_a_paid_edit_settles_the_transfer_and_dates_both_sides_records(
        self, app, db, transfer_data,
    ):
        """Marking Paid writes Paid on the transfer's row and a dated record on each side.

        The transfer's status is the one status; each side's payment record
        is the one thing still stored per side, read by its own SQL
        (:func:`tests._test_helpers.transfer_side_settle_day`), where each
        shadow said Paid until plan step ``balance:X-bi-6-4d-2`` (ruling
        R-BAL167 class 1).  The row is re-read from the database since that
        step's checkpoint 2e-2b-1b-1 (ruling R-BAL290 part 1).  The one copy
        of a pair: the invariant group's line-for-line twin,
        ``test_statuses_always_match_after_update``, was deleted there.
        Renamed from ``test_status_syncs_shadows``.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            sides = (xfer.from_account_id, xfer.to_account_id)
            paid_id = ref_cache.status_id(StatusEnum.DONE)

            transfer_service.update_transfer(
                xfer_id, td["user"].id, status_id=paid_id,
            )

            assert _reread(xfer_id).status_id == paid_id
            for account_id in sides:
                assert transfer_side_settle_day(
                    db.session, xfer_id, account_id,
                ) is not None

    def test_a_period_edit_writes_the_transfers_period(self, app, transfer_data):
        """A period edit writes the new period on the transfer's row.

        What the edit wrote, re-read from the database (ruling R-BAL290
        part 1).  It also read both legs' periods until plan step
        ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-1b-1; what a side shows of
        the row's period is ``test_transfer_legs/test_what_a_side_shows.py``'s
        ``TestBothSidesShowTheTransfer`` now.  The one copy of a pair: the
        invariant group's ``test_periods_always_match_after_update`` made the
        same edit to the fourth period and was deleted there.  Renamed from
        ``test_a_period_edit_moves_both_legs``, and before that
        ``test_period_syncs_shadows``.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id
            new_period = td["periods"][2]

            transfer_service.update_transfer(
                xfer_id, td["user"].id, pay_period_id=new_period.id,
            )

            assert _reread(xfer_id).pay_period_id == new_period.id

    def test_a_category_edit_files_the_transfer_under_it(self, app, transfer_data):
        """A category edit writes the new category on the transfer's row.

        It read the two shadow rows' categories until plan step
        ``balance:X-bi-6-4d-3``, whose create door stopped writing them
        (ruling R-BAL167 class 1).  **Its edit moved nothing**: the transfer
        is created under Rent and the edit named Rent, a no-op in the shadow
        version too; re-pointed onto the transfer and its legs, it PASSED with
        the edit's category arm deleted (measured at checkpoint 2e-2a-2).  The edit
        names Groceries now, a category the transfer is not under (the
        coordinator's ruling, 2026-10-13).  It also read both legs'
        categories until that step's checkpoint 2e-2b-1b-1, which re-reads
        the row from the database instead (ruling R-BAL290 part 1); what a
        side shows of the row's category is ``test_transfer_legs/test_what_a_side_shows.py``'s
        ``TestBothSidesShowTheTransfer``.  Renamed from
        ``test_a_category_edit_files_the_transfer_and_both_legs_under_it``,
        and before that ``test_category_updates_both_shadows``.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            groceries_id = td["categories"]["Groceries"].id
            # A guard on the INPUT, not a read: the edit must move the
            # category, or the case grades nothing it names.
            assert xfer.category_id != groceries_id

            transfer_service.update_transfer(
                xfer.id, td["user"].id, category_id=groceries_id,
            )

            assert _reread(xfer.id).category_id == groceries_id

    def test_a_notes_edit_writes_the_notes_and_leaves_the_amount(
        self, app, transfer_data,
    ):
        """A notes edit writes the transfer's notes and leaves its amount as it was.

        It compared the two shadow rows' amounts before and after the edit
        until plan step ``balance:X-bi-6-4d-3``, and from that step's create
        door on both reads ran over none (a vacuous read, measured at its
        checkpoint 2e-2a-2).  It then priced both legs before and after the
        edit until that step's checkpoint 2e-2b-1b-1, which reads the row's
        amount, re-read from the database (ruling R-BAL290 part 1): a side's
        price is the row's amount, which ``test_transfer_legs/test_what_a_side_shows.py``'s
        ``TestBothSidesShowTheTransfer`` grades.  Renamed from
        ``test_a_notes_edit_leaves_both_legs_priced_as_they_were``, and
        before that ``test_notes_does_not_touch_shadows``.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id

            transfer_service.update_transfer(
                xfer_id, td["user"].id, notes="Updated notes",
            )

            row = _reread(xfer_id)
            assert (row.notes, row.amount) == ("Updated notes", Decimal("250.00"))

    def test_a_figure_on_an_UNSETTLED_transfer_is_REFUSED(self, app, db, transfer_data):
        """A figure records a settle, so an unsettled pair cannot be handed one.

        **This asserted the opposite until plan step X-au-c3** -- an
        ``actual_amount`` handed to ``update_transfer`` was written onto both
        shadows whatever their status.  A figure now RECORDS what moved, and
        the seam keeps one off a row whose money has not
        (``reject_settlement_without_settled_status``); the service refuses
        the request with a designed 400 before any write.

        No form can produce it: the correction box renders only on a settled
        row.  To record a figure, settle the transfer -- the same act that
        records one.  Stated here rather than quietly rewritten, because it is a
        BEHAVIOUR change and belongs in the commit message.

        "Before any write" is read off each SIDE's payment record since plan
        step ``balance:X-bi-6-4d-3``, where it asked each of the two shadow
        rows, which that step's create door no longer writes (ruling R-BAL167
        class 1).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            with pytest.raises(ValidationError, match="is not settling"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, figure=typed(Decimal("245.00"))
                )

            for account_id in (xfer.from_account_id, xfer.to_account_id):
                assert transfer_side_record(
                    db.session, xfer.id, account_id,
                ) is None

    def test_an_override_edit_flags_the_transfer(self, app, transfer_data):
        """An override edit writes the flag on the transfer's row.

        What the edit wrote, re-read from the database (ruling R-BAL290
        part 1).  It also read both legs' flags until plan step
        ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-1b-1; what a side shows of
        the row's flag is ``test_transfer_legs/test_what_a_side_shows.py``'s
        ``TestBothSidesShowTheTransfer`` now.  Renamed from
        ``test_an_override_edit_reaches_the_transfer_and_both_legs``, and
        before that ``test_is_override_syncs_shadows``.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id

            transfer_service.update_transfer(
                xfer_id, td["user"].id, is_override=True,
            )

            assert _reread(xfer_id).is_override is True

    def test_wrong_user_rejected(self, app, db, transfer_data, second_user):
        """Update by non-owner raises NotFoundError."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            with pytest.raises(NotFoundError):
                transfer_service.update_transfer(
                    xfer.id, second_user["user"].id,
                    amount_ownership=AmountOwnership.own(Decimal("100")),
                )

    def test_nonexistent_rejected(self, app, db, transfer_data):
        """Update of non-existent transfer raises NotFoundError."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                transfer_service.update_transfer(
                    99999, transfer_data["user"].id,
                    amount_ownership=AmountOwnership.own(Decimal("100")),
                )

    def test_update_soft_deleted_transfer_raises_not_found(self, app, db, transfer_data):
        """Verify that calling update_transfer on a soft-deleted transfer
        raises NotFoundError, not a misleading data integrity ValidationError.
        The transfer service treats soft-deleted transfers as non-existent to
        prevent confusing error messages during debugging.

        An EDIT refusal, moved here from ``TestSoftDeleteHandling`` at plan
        step ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-2a, which merged that
        group's delete cases into :class:`TestDeleteTransfer` (ruling
        R-BAL290 part 2).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.flush()

            with pytest.raises(NotFoundError, match="not found"):
                transfer_service.update_transfer(
                    xfer_id, td["user"].id,
                    amount_ownership=AmountOwnership.own(Decimal("500.00")),
                )

    def test_validates_positive_amount(self, app, db, transfer_data):
        """Update with zero amount raises ValidationError."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            with pytest.raises(ValidationError, match="positive"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, amount_ownership=AmountOwnership.own(Decimal("0")), )

    def test_validates_period_ownership(self, app, db, transfer_data, second_user):
        """Update with period belonging to another user raises NotFoundError."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            other_periods = record_paydays_across_a_hole(
                user_id=second_user["user"].id,
                first_payday=date(2026, 6, 1),
                num_periods=2,
                rhythm=rhythm_of(14),
            )
            db.session.flush()

            with pytest.raises(NotFoundError):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, pay_period_id=other_periods[0].id
                )

    def test_a_category_cleared_to_none_leaves_the_transfer_under_none(
        self, app, transfer_data,
    ):
        """Clearing the category leaves the row under none.

        What the edit wrote, re-read from the database (ruling R-BAL290
        part 1).  It also read both legs' categories, as PROJECTION reads,
        until plan step ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-1b-2: both
        rows of ``test_transfer_legs/test_what_a_side_shows.py``'s ``TestBothSidesShowTheTransfer``
        carried a category until then, so a side that answered some category
        for a transfer filed under NONE failed only those reads (measured at
        checkpoint 2e-2b-1b-1, by a mutant answering ``-1`` for a missing
        category).  That table holds a transfer filed under no category
        since checkpoint 2e-2b-1b-2, and grades what a side shows of it.
        Renamed from ``test_a_category_cleared_to_none_clears_both_legs``,
        and before that ``test_category_set_to_none_propagates_none``.
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
                    amount_ownership=AmountOwnership.own(Decimal("100")),
                    status_id=td["projected_status"].id,
                    category_id=rent_cat.id,
                ),
            )

            transfer_service.update_transfer(
                xfer.id, td["user"].id, category_id=None,
            )

            assert _reread(xfer.id).category_id is None

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

        Both sides' payment records keep it, which is Transfer Invariant 3 over
        the record as well as over the amount, and neither is worth it while
        unsettled -- a side's figure asks the TRANSFER's status, so it answers
        ``None`` for a reverted side whatever its record still carries.  Each
        side is its RECORD, hung off the transfer, since plan step
        ``balance:X-bi-6-4d-2``; the shadows held it until then (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            typed_id = ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.TYPED,
            )
            sides = (xfer.from_account_id, xfer.to_account_id)

            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                status_id=done_id, figure=typed(Decimal("100")),
            )
            for account_id in sides:
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == Decimal("100")

            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=projected_id,
            )

            db.session.expire_all()
            for account_id in sides:
                record = transfer_side_record(db.session, xfer.id, account_id)
                # The ASSERTION is withdrawn ...
                assert record.settled_on is None
                assert record.reconciled_by_id is None
                # ... and WHAT MOVED is kept, on BOTH sides (Invariant 3).
                assert (record.amount, record.figure_source_id) == (
                    Decimal("100"), typed_id,
                )
                # Kept, but not counted: the status is what decides.
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) is None

    def test_settle_day_defaults_to_today_when_omitted(
        self, app, db, transfer_data
    ):
        """F-048 / C-22: settling with no explicit day defaults to the user's today.

        Defense-in-depth for the route layer: any caller that
        forgets to pass a settle day when transitioning to a settled
        status (Paid/Received/Settled) still produces side records with
        a recorded ``settled_on`` day -- the dashboard's "paid
        on time" indicator and the legs' ``days_paid_before_due``
        analytics rely on it.  Each side's day is its payment RECORD's
        since plan step ``balance:X-bi-6-4d-2``; the shadows carried it until
        then (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
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

            for account_id in (xfer.from_account_id, xfer.to_account_id):
                record = transfer_side_record(db.session, xfer.id, account_id)
                assert record is not None and record.settled_on is not None, (
                    f"The side on account {account_id} has no settled_on "
                    f"after settle without explicit kwarg; defense-in-depth "
                    f"failed."
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
        over the default, and the default is the user's today.  Each side's day
        is read off its payment RECORD since plan step ``balance:X-bi-6-4d-2``
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
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
                status_id=done_status.id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(explicit),
                ),
            )
            db.session.flush()
            for account_id in (xfer.from_account_id, xfer.to_account_id):
                day = transfer_side_record(
                    db.session, xfer.id, account_id,
                ).settled_on
                assert day == explicit, (
                    f"The side on account {account_id} took the default day "
                    f"{day} instead of the explicit {explicit}."
                )

    def test_settle_day_cleared_on_revert_to_non_settled(
        self, app, db, transfer_data
    ):
        """F-048 / C-22: reverting to non-settled clears the stale settle day.

        Maintains the settled-iff-dated invariant: a Paid
        transfer reverted to Projected must have its sides'
        settle day cleared, otherwise a
        future settle would silently inherit the stale timestamp.  Each
        side's day is its payment RECORD's since plan step
        ``balance:X-bi-6-4d-2``; the shadows carried it until then (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            projected_status = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            sides = (xfer.from_account_id, xfer.to_account_id)

            # Settle (the seam records the user's today).
            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_status.id,
            )
            db.session.flush()
            for account_id in sides:
                assert transfer_side_settle_day(
                    db.session, xfer.id, account_id,
                ) is not None

            # Revert to Projected with no explicit day.
            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=projected_status.id,
            )
            db.session.flush()
            for account_id in sides:
                assert transfer_side_settle_day(
                    db.session, xfer.id, account_id,
                ) is None, (
                    f"The side on account {account_id} retained a stale "
                    f"settle day after revert to Projected; the F-048 "
                    f"invariant is violated."
                )

    def test_a_combined_edit_writes_every_field_it_names(
        self, app, db, transfer_data,
    ):
        """An amount, Paid and a period in ONE edit land on the row; both records hold the amount.

        The settle runs after the amount is written (``_update`` applies a
        stated amount before its settle dispatch; the period this edit also
        moves is written after the settle), so each side records the $999.99
        the same edit wrote, dated, where the $250.00 the transfer was
        created at would be a settle that ran first.  The row is re-read from
        the database and each side's record read by its own SQL
        (:func:`tests._test_helpers.transfer_side_record`).  Moved here from
        the invariant group as ``test_multiple_updates_maintain_invariants``
        at plan step ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-1b-1 (ruling
        R-BAL290 part 1), where it read both legs' periods and, after the
        settle, their prices, and not the records' amounts.  A side's period
        is ``test_transfer_legs/test_what_a_side_shows.py``'s ``TestBothSidesShowTheTransfer``; a
        SETTLED side's price is not, since that table holds no settled row,
        but ``test_transfer_legs/test_the_grids_legs.py``'s
        ``TestTheLegMapProducers::test_a_settled_leg_records_its_movements_figure``
        and ``TestTheLegContributionTwins``'
        ``test_a_settled_leg_is_worth_its_record_and_not_its_plan``
        (the review of that checkpoint measured both failing under a mutant
        pricing a settled side at $0).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            sides = (xfer.from_account_id, xfer.to_account_id)
            paid_id = ref_cache.status_id(StatusEnum.DONE)
            period_id = td["periods"][4].id

            transfer_service.update_transfer(
                xfer_id, td["user"].id,
                amount_ownership=AmountOwnership.own(Decimal("999.99")),
                status_id=paid_id,
                pay_period_id=period_id,
            )

            row = _reread(xfer_id)
            assert (row.amount, row.status_id, row.pay_period_id) == (
                Decimal("999.99"), paid_id, period_id,
            )
            for account_id in sides:
                record = transfer_side_record(db.session, xfer_id, account_id)
                assert (record.amount, record.settled_on is not None) == (
                    Decimal("999.99"), True,
                )


# ── Delete Tests ───────────────────────────────────────────────────


class TestDeleteTransfer:
    """``transfer_service.delete_transfer``, one case per state and kind of delete.

    A delete case is set by two things: the transfer's state before the
    delete (planned, paid, or already hidden) and which delete runs (a soft
    delete HIDES it, a hard delete removes it for good).  Each case checks
    what that delete must leave behind (ruling R-BAL290 part 2, the
    developer, 2026-10-14).  The group has been laid out this way since plan
    step ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-2a, which merged two groups
    into it: ``TestSoftDeleteHandling`` (the already-hidden cases) and
    ``TestADeleteReturnsAPaidTransferToAPlan`` (the paid cases).

    - PLANNED: hidden (:meth:`test_soft_hides_the_transfer`), or removed
      (:meth:`test_hard_removes_the_transfer`).
    - PAID: hidden.  The delete that takes a Paid transfer's payment records
      off (ruling credit_card:R-CC75) also sets it back to Projected, so a
      restore brings back a plan, never a ``$0.00`` close, which a loan reads
      as a missed installment (ruling R-BAL246, the developer 2026-10-09,
      "Back as a plan, now";
      :meth:`test_a_paid_transfer_comes_off_as_a_hidden_plan_and_restores_as_one`).
      The set-back has two edges: a ``$0.00`` close held nothing and keeps
      Paid (:meth:`test_a_zero_close_held_nothing_and_keeps_paid`), and a
      Cancelled transfer whose kept records came off stays Cancelled
      (:meth:`test_a_cancelled_transfer_whose_kept_records_came_off_stays_cancelled`).
      A paid transfer REMOVED for good is graded in two other files and is
      not written again here: ``test_transfer_posting_lifecycle.py``'s
      ``TestDeleteAndRestore::test_hard_delete_settled_reverses_and_pairs_survive_null_links``
      (the row is gone, its posted legs reversed) and
      ``test_cc5_4a3_movement_removal.py``'s
      ``TestEveryDoorWithdrawsTheActItself`` case
      ``test_a_hard_transfer_delete_withdraws_the_act_naming_its_leg`` (the
      statement match its payment was in is withdrawn).  That the row
      is gone is the first one's alone: a hard delete that kept a transfer
      whose sides held a payment failed it and no case in this file
      (measured at checkpoint 2e-2b-2a).
    - ALREADY HIDDEN: hidden again
      (:meth:`test_delete_soft_deleted_transfer_is_idempotent`), or removed
      (:meth:`test_hard_delete_soft_deleted_transfer_succeeds`, a planned
      transfer hidden first; a PAID one hidden and then removed is
      ``test_transfer_posting_lifecycle.py``'s ``TestDeleteAndRestore`` case
      ``test_hard_delete_of_a_soft_deleted_pair_finds_the_ledger_at_zero``).
    - Someone else's transfer, or none: not found.  A live one by the hard
      delete (:meth:`test_wrong_user_rejected`), an already-hidden one by
      either delete (:meth:`test_someone_elses_hidden_transfer_is_not_found`),
      and none (:meth:`test_nonexistent_rejected`).

    Every case here holds an AD-HOC transfer.  The shape production hides
    is a transfer of a definition (the instance delete hides a linked
    transfer and removes an ad-hoc one; the template archive hides only
    linked ones), and a hide that skipped a linked transfer passes this
    group: it is graded at the route tier, by ``test_routes/test_transfers/``
    ``test_instance.py``'s ``test_delete_template_transfer_soft_deletes``
    among others (measured at checkpoint 2e-2b-2a's review).

    Editing a hidden transfer answers not found too, which is an EDIT
    refusal: :class:`TestUpdateTransfer`'s
    ``test_update_soft_deleted_transfer_raises_not_found``.  The paid cases
    read each side's record by its own SQL
    (:func:`tests._test_helpers.transfer_side_record`), never the producer.
    """

    @staticmethod
    def _sides(td, xfer_id):
        """Each side's record ``(amount, settled_on)``, or ``None``, from Checking then Savings."""
        db.session.expire_all()
        sides = []
        for account in (td["account"], td["savings_account"]):
            record = transfer_side_record(db.session, xfer_id, account.id)
            sides.append(
                None if record is None else (record.amount, record.settled_on),
            )
        return tuple(sides)

    def test_soft_hides_the_transfer(self, app, transfer_data):
        """A planned transfer hidden is flagged deleted, stays Projected, and is returned.

        What the delete wrote, re-read from the database (ruling R-BAL290
        part 1): the flag, and the status a delete leaves alone on a
        transfer whose sides held no payment (read since checkpoint
        2e-2b-2a's review: a delete that cancelled a hidden plan passed this
        file).  The door returns the session's own row, the one it hid, by
        identity, so the returned object's flag is the stored one.  It
        also asked each endpoint's plan loader for a leg of the transfer,
        before the delete and after it, until plan step
        ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-2a.  That a hidden transfer
        leaves both accounts' plans is the LOADER's fact, whichever delete
        hid it, and it is asked of this case's own stored state (saved,
        ad-hoc, Projected, hidden, no side holding a record) on BOTH sides by
        :meth:`test_delete_soft_deleted_transfer_is_idempotent` below, and on
        the expense side alone by ``test_transfer_legs/test_the_plan_half.py``'s
        ``TestTransferLegs::test_a_settled_a_cancelled_and_a_deleted_parent_yield_no_leg``,
        which asks Checking's plan only (measured at that checkpoint: a
        loader letting a hidden transfer through on the income side alone
        fails the first and passes the second; on the expense side alone it
        fails both).  Renamed from
        ``test_soft_hides_the_transfer_and_both_legs``, and before that
        ``test_soft_marks_shadows_deleted``.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id

            hidden = transfer_service.delete_transfer(
                xfer_id, td["user"].id, soft=True,
            )

            row = _reread(xfer_id)
            assert hidden is row, "the door returns the session's row, the one it hid"
            # Hidden, and still a plan: only a delete that took a payment
            # off a SETTLED transfer moves its status (ruling R-BAL246).
            assert (row.is_deleted, row.status_id) == (
                True, ref_cache.status_id(StatusEnum.PROJECTED),
            )

    def test_hard_removes_the_transfer(self, app, db, transfer_data):
        """Hard delete removes the transfer row.

        It also collected the transfer's two shadow rows and asserted each
        was gone, until plan step ``balance:X-bi-6-4d-3``; from that step's
        create door on the transfer holds none, so the collection was empty
        and the loop over it ran over none (a vacuous read, measured at its
        checkpoint 2e-2a-2), and it is dropped.  Renamed from
        ``test_hard_removes_shadows``.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            db.session.commit()

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=False)
            db.session.commit()
            db.session.expire_all()

            assert db.session.get(Transfer, xfer_id) is None

    def test_a_paid_transfer_comes_off_as_a_hidden_plan_and_restores_as_one(
        self, app, db, transfer_data,
    ):
        """Paid with $250.00 on both sides: deleted Projected, restored Projected, empty."""
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id
            transfer_service.update_transfer(
                xfer_id, td["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()
            paid_sides = self._sides(td, xfer_id)
            assert [side[0] for side in paid_sides] == [
                Decimal("250.00"), Decimal("250.00"),
            ], "precondition: the Paid transfer holds both $250.00 records"

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.commit()

            xfer = db.session.get(Transfer, xfer_id)
            assert xfer.is_deleted is True
            assert xfer.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert self._sides(td, xfer_id) == (None, None)

            transfer_service.restore_transfer(xfer_id, td["user"].id)
            db.session.commit()

            xfer = db.session.get(Transfer, xfer_id)
            assert xfer.is_deleted is False
            assert xfer.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert self._sides(td, xfer_id) == (None, None)

    def test_a_zero_close_held_nothing_and_keeps_paid(
        self, app, db, transfer_data,
    ):
        """Paid at $0.00 (no record either side): deleted Paid, restored Paid."""
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id
            paid_id = ref_cache.status_id(StatusEnum.DONE)
            transfer_service.update_transfer(
                xfer_id, td["user"].id, status_id=paid_id,
            )
            transfer_service.update_transfer(
                xfer_id, td["user"].id, figure=typed(Decimal("0")),
            )
            db.session.commit()
            assert self._sides(td, xfer_id) == (None, None), (
                "precondition: a $0.00 close keeps no record"
            )

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.commit()

            xfer = db.session.get(Transfer, xfer_id)
            assert xfer.is_deleted is True
            assert xfer.status_id == paid_id

            transfer_service.restore_transfer(xfer_id, td["user"].id)
            db.session.commit()

            assert db.session.get(Transfer, xfer_id).status_id == paid_id
            assert self._sides(td, xfer_id) == (None, None)

    def test_a_cancelled_transfer_whose_kept_records_came_off_stays_cancelled(
        self, app, db, transfer_data,
    ):
        """Only a SETTLED transfer is set back: a Cancelled one keeps its status.

        Paid, reverted (each side keeps its $250.00 record un-dated, ruling
        R-BAL61), then cancelled: the delete takes both records off, and the
        transfer it hides is still Cancelled.  The case a set-back keyed on
        the records alone would move to Projected.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id
            for status in (StatusEnum.DONE, StatusEnum.PROJECTED, StatusEnum.CANCELLED):
                transfer_service.update_transfer(
                    xfer_id, td["user"].id, status_id=ref_cache.status_id(status),
                )
            db.session.commit()
            assert self._sides(td, xfer_id) == (
                (Decimal("250.00"), None), (Decimal("250.00"), None),
            ), "precondition: the cancelled transfer kept both records un-dated"

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.commit()

            xfer = db.session.get(Transfer, xfer_id)
            assert xfer.is_deleted is True
            assert xfer.status_id == ref_cache.status_id(StatusEnum.CANCELLED)
            assert self._sides(td, xfer_id) == (None, None)

    def test_delete_soft_deleted_transfer_is_idempotent(self, app, transfer_data):
        """Hiding a hidden transfer again does not raise; it stays hidden and out of both plans.

        A repeated soft delete must not break the template deactivation
        workflow, which may process the same transfers more than once.  The
        one copy of a pair: the delete group's ``test_soft_delete_idempotent``
        made the same two deletes, asked only the flag on the transfer the
        second one returned, and was deleted at plan step
        ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-2a (ruling R-BAL290 part 2,
        "the stronger check survives"), where this case moved into the delete
        group from ``TestSoftDeleteHandling``.  What the second delete left
        is read off the row re-read from the database since then (ruling
        R-BAL290 part 1), where it read the object the door returned, and off
        each endpoint's plan loader, a PROJECTION read that holds a leg of
        the transfer on both sides before the first delete (a guard on the
        input, moved here from the planned case,
        :meth:`test_soft_hides_the_transfer`, at that checkpoint) and none
        after the second.

        It read the shadow rows' flags afterwards ("still soft-deleted")
        until plan step ``balance:X-bi-6-4d-3``; from that step's create door
        on no row can name the transfer (``ck_transactions_names_no_transfer``,
        ruling R-BAL258), so ``all()`` ran over none after the second delete
        (a vacuous read; a probe counted such rows 0 before the first delete
        and after the second, at checkpoint 2e-2a-2).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            endpoints = (xfer.from_account_id, xfer.to_account_id)
            for account_id in endpoints:
                assert [
                    leg.transfer.id for leg in planned_transfer_legs(
                        account_id, td["scenario"].id, options=(),
                    )
                ] == [xfer_id], "precondition: each endpoint plans its leg"

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            # Second call must not raise.
            again = transfer_service.delete_transfer(
                xfer_id, td["user"].id, soft=True
            )

            row = _reread(xfer_id)
            assert again is row, "the door returns the session's row, the one it hid"
            assert row.is_deleted is True
            for account_id in endpoints:
                assert planned_transfer_legs(
                    account_id, td["scenario"].id, options=(),
                ) == []

    def test_hard_delete_soft_deleted_transfer_succeeds(self, app, db, transfer_data):
        """A hidden transfer removed for good is gone.

        The hard-delete path must work regardless of the is_deleted flag.
        The one copy of a pair: the delete group's
        ``test_hard_on_already_soft_deleted`` was this case line for line and
        was deleted at plan step ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-2a
        (ruling R-BAL290 part 2), where this case moved into the delete group
        from ``TestSoftDeleteHandling``.

        It also counted the shadow rows naming the transfer afterwards (0)
        until plan step ``balance:X-bi-6-4d-3``; from that step's create door
        on no row can name one (``ck_transactions_names_no_transfer``, ruling
        R-BAL258), so the count it took after the last delete read none (a
        vacuous read; a probe counted such rows 0 before the first delete and
        after the last, at checkpoint 2e-2a-2), and it is dropped.
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

    def test_wrong_user_rejected(self, app, db, transfer_data, second_user):
        """Delete by non-owner raises NotFoundError."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            with pytest.raises(NotFoundError):
                transfer_service.delete_transfer(
                    xfer.id, second_user["user"].id
                )

    @pytest.mark.parametrize("soft", [True, False], ids=["soft", "hard"])
    def test_someone_elses_hidden_transfer_is_not_found(
        self, app, db, transfer_data, second_user, soft,
    ):
        """A non-owner's delete of the owner's HIDDEN transfer is not found and leaves it hidden.

        The delete reaches a hidden transfer on purpose (``allow_deleted``,
        so a repeated hide and a hard delete of a hidden transfer work), so
        its ownership check must hold for a hidden row, on either delete, as
        it does for a live one (:meth:`test_wrong_user_rejected`, which runs
        the hard delete only).  The refused
        delete is read off the row re-read after a COMMIT, which writes
        whatever it staged: the transfer is still there and still hidden.
        The owner's same delete then lands on the same id, so the refusal is
        the ownership check's and not a wrong id's.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id
            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.commit()

            with pytest.raises(NotFoundError, match="not found"):
                transfer_service.delete_transfer(
                    xfer_id, second_user["user"].id, soft=soft,
                )

            row = _reread(xfer_id)
            assert row is not None and row.is_deleted is True, (
                "the refused delete removed or un-hid the owner's transfer"
            )

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=soft)
            # The hard delete removes it; the soft delete leaves it in place.
            assert (_reread(xfer_id) is None) is not soft

    def test_nonexistent_rejected(self, app, db, transfer_data):
        """Delete of non-existent transfer raises NotFoundError."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                transfer_service.delete_transfer(
                    99999, transfer_data["user"].id
                )


# ── Restore Tests (M1) ──────────────────────────────────────────


class TestRestoreTransfer:
    """``transfer_service.restore_transfer``: a hidden transfer comes back unhidden, unchanged.

    A restore brings a hidden transfer back unhidden, its amount, status and
    pay period unchanged and back in both accounts' plans; refuses while
    either account is archived, leaving the transfer hidden; and answers not
    found for someone else's transfer or none (ruling R-BAL290 part 3, the
    developer, 2026-10-14, "Delete the seven").  Each case that reads the
    transfer reads the row re-read from the database since plan step
    ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-2b, where they read the
    object in the session and the transfer's two shadow rows (ruling
    R-BAL290 part 1).  The nonexistent-transfer case and the both-archived
    refusal read no row; the wrong-owner case reads it after the refusal
    and after the owner's restore since that step's vacuous-residue
    checkpoint.  "Unchanged" is the ruling's three fields: no case here
    reads a restored transfer's due date, category, override flag or notes,
    and a restore that moved its due date or cleared its category fails
    none (measured by the review of that checkpoint).

    Every case holds an AD-HOC transfer.  The shape production restores is
    a transfer of a definition (un-archiving a template restores its
    transfers), and a restore that un-hid ad-hoc transfers only fails six
    route cases (two in the templates' route tests, two in
    ``test_archived_rows_bound_the_books.py``, one each in
    ``test_archived_definition_edit.py`` and
    ``test_a_revert_below_the_books.py``; measured by that review).  A PAID transfer
    hidden and restored comes back a plan, and a ``$0.00`` close comes back
    Paid: :class:`TestDeleteTransfer`'s
    ``test_a_paid_transfer_comes_off_as_a_hidden_plan_and_restores_as_one``
    and ``test_a_zero_close_held_nothing_and_keeps_paid`` (ruling R-BAL246).

    Eight cases were deleted at that checkpoint.  Seven planted a WRONG
    value on a hidden transfer's stored side row and checked that the
    restore repaired it or that nothing read it:
    ``test_repairable_status_drift_is_still_repaired``,
    ``test_a_repair_into_a_projected_status_clears_the_instant`` and
    ``test_corrects_drifted_shadow_status`` (its status),
    ``test_corrects_drifted_shadow_due_date``,
    ``test_corrects_drifted_shadow_category``,
    ``test_corrects_drifted_shadow_is_override`` and
    ``test_a_shadow_amount_CANNOT_drift_from_its_transfer`` (its amount).
    No side row can be stored since that step's create door
    (``ck_transactions_names_no_transfer``, ruling R-BAL258), so there is
    nothing to plant; the refusal is ``test_a_transfer_is_one_row.py``'s
    ``test_the_database_refuses_a_twin`` (the expense side) and
    ``test_a_transfer_shadow_is_derived.py``'s
    ``test_the_transfer_owns_its_figure_and_neither_twin_can_be_stored``
    (both sides) until plan step ``balance:X-bi-6-5`` deletes the column
    it guards.  The eighth,
    ``test_raises_on_missing_shadows``, asked the restore to refuse a
    transfer with no side rows, which every transfer is now (ruling
    R-BAL273, "Delete it").

    Three of the seven also read a side of the restored transfer as the grid
    draws it, and the amount case priced both shadow rows (none of those
    reads ran after that step's create door: each case failed at its
    plant).  The restored transfer's amount is
    :meth:`test_restores_transfer_and_shadows`'s, and what a side shows of
    it ``test_transfer_legs/test_what_a_side_shows.py``'s ``TestBothSidesShowTheTransfer``.  Each
    state the side reads asked is asked elsewhere, failing under a mutant
    gated on that state and side (measured at that checkpoint), with one
    exception.  A restored ``$0.00`` close's side reads Paid: on the expense
    side, ``test_transfer_legs/test_the_grids_legs.py``'s ``TestTheLegMapProducers`` and
    ``TestTheLegContributionTwins`` cases
    ``test_a_settled_leg_with_no_movement_is_the_zero_record`` and
    ``test_routes/test_a_zero_close_has_no_day.py``.  On the INCOME side no
    case reads it: the mutant fails only ``test_loan_settled_legs.py``'s
    loan payments, each while building its data rather than at a read of
    the side, and limited to a destination that is not a loan it fails
    none (measured by the review of that checkpoint).  The close's own
    Paid is :class:`TestDeleteTransfer`'s
    ``test_a_zero_close_held_nothing_and_keeps_paid``.  A restored planned
    transfer's sides read Projected:
    ``test_transfer_legs/test_what_a_side_shows.py``'s
    ``TestBothSidesShowTheTransfer``, each side.  They hold no settle day:
    ``test_transfer_legs/test_the_grids_legs.py``'s
    ``test_a_planned_and_an_undated_leg_answer_none`` on the expense side,
    and ``test_statement_match/test_leg_candidates.py``'s
    ``test_each_side_is_offered_on_its_own_account_keyed_by_the_transfer``
    on the income side.
    """

    def test_restores_transfer_and_shadows(self, app, transfer_data):
        """A hidden planned transfer comes back unhidden, unchanged, and in both plans.

        The restore un-hides the row and returns it; its amount ($250.00, as
        created), status (Projected) and pay period (the first) are unchanged
        (ruling R-BAL273's condition), and each endpoint's plan holds a leg
        of it again (ruling R-BAL290 part 3).  The plan read is a PROJECTION
        read: that a live transfer is in both plans and a hidden one in
        neither is the plan loader's fact, and a loader that kept hidden
        transfers and dropped live ones fails this case first at it
        (measured at plan step ``balance:X-bi-6-4d-3``'s checkpoint
        2e-2b-2b).  Until that checkpoint it read the object in the session
        and asked the transfer's two shadow rows for their delete flags
        (ruling R-BAL290 part 1).  The name is kept: ruling R-BAL273 names
        it.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            endpoints = (xfer.from_account_id, xfer.to_account_id)
            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            assert _reread(xfer_id).is_deleted is True, "precondition: hidden"

            restored = transfer_service.restore_transfer(xfer_id, td["user"].id)

            row = _reread(xfer_id)
            assert restored is row, "the door returns the session's row, the one it restored"
            assert row.is_deleted is False
            # As created: $250.00, Projected, in the first period.
            assert (transfer_amount(row), row.status_id, row.pay_period_id) == (
                Decimal("250.00"), td["projected_status"].id, td["periods"][0].id,
            )
            for account_id in endpoints:
                assert [
                    leg.transfer.id for leg in planned_transfer_legs(
                        account_id, td["scenario"].id, options=(),
                    )
                ] == [xfer_id]

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

        The refused restore leaves the owner's transfer hidden: the row is
        read re-read after a COMMIT, which writes whatever the refused
        restore staged, so a restore that un-hid the transfer before its
        ownership refusal fails it (never a rollback before the read, which
        would discard such a staged un-hide).  The owner's own restore then
        lands on the same id, so the refusal is the ownership check's and not
        a wrong id's.  It read the raise alone until plan step
        ``balance:X-bi-6-4d-3``'s vacuous-residue checkpoint.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id
            transfer_service.delete_transfer(
                xfer_id, td["user"].id, soft=True
            )
            db.session.commit()

            with pytest.raises(NotFoundError, match="not found"):
                transfer_service.restore_transfer(
                    xfer_id, second_user["user"].id
                )

            assert _reread(xfer_id).is_deleted is True, (
                "the refused restore un-hid the owner's transfer"
            )

            transfer_service.restore_transfer(xfer_id, td["user"].id)
            assert _reread(xfer_id).is_deleted is False

    def test_idempotent_on_active_transfer(self, app, transfer_data):
        """Verify that calling restore_transfer on an already-active
        transfer completes without error.  This idempotency ensures that
        bulk reactivation workflows do not fail when processing a mix of
        deleted and active transfers.

        The row it leaves is read re-read from the database since plan step
        ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-2b, where it read the
        object the door returned (ruling R-BAL290 part 1); the door returns
        the session's own row.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            # Not deleted -- restore is a no-op.
            restored = transfer_service.restore_transfer(
                xfer.id, td["user"].id
            )

            row = _reread(xfer.id)
            assert restored is row, "the door returns the session's row"
            assert (row.is_deleted, transfer_amount(row)) == (
                False, Decimal("250.00"),
            )

    def test_rejects_when_source_account_archived(
        self, app, db, transfer_data,
    ):
        """F-164 / C-20: refuse to restore a transfer onto an archived
        source account.

        Soft-delete a transfer, then archive the source account.  The
        next ``restore_transfer`` must raise ``ValidationError`` and
        leave the transfer hidden.  This prevents the user from silently
        resurrecting balance entries against an account they have
        withdrawn from active projections.

        The refusal runs before the restore un-hides anything
        (``assert_restorable``, plan step X-aj1), so the row is read
        re-read after a COMMIT, which writes whatever the refused restore
        staged: a restore that un-hid the row before refusing fails it
        (measured at plan step ``balance:X-bi-6-4d-3``'s checkpoint
        2e-2b-2b).  Until that checkpoint it rolled the session back before
        re-reading, which discards such a staged un-hide, and asked the
        transfer's two shadow rows for their delete flags (ruling R-BAL290
        part 1).
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

            assert _reread(xfer_id).is_deleted is True

    def test_rejects_when_destination_account_archived(
        self, app, db, transfer_data,
    ):
        """F-164 / C-20: refuse to restore a transfer onto an archived
        destination account.

        Symmetric counterpart to
        ``test_rejects_when_source_account_archived``: archives the
        ``to_account`` instead of the ``from_account`` and verifies the
        same refusal, read the same way, off the row re-read after a
        commit.
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

            assert _reread(xfer_id).is_deleted is True

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

        The archived refusals' control, set up as they are (the transfer
        hidden and committed) with neither account archived, so each of
        their refusals is the archive's: the restore lands, returns the
        row, and leaves it unhidden with its amount, status and pay period
        unchanged (ruling R-BAL273's condition).  Read off the row re-read
        from the database since plan step ``balance:X-bi-6-4d-3``'s
        checkpoint 2e-2b-2b, where it read the object the door returned and
        the transfer's two shadow rows (ruling R-BAL290 part 1).  The name
        is kept: ruling R-BAL273 names it.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            db.session.commit()

            transfer_service.delete_transfer(
                xfer_id, td["user"].id, soft=True,
            )
            db.session.commit()

            restored = transfer_service.restore_transfer(
                xfer_id, td["user"].id,
            )

            row = _reread(xfer_id)
            assert restored is row, "the door returns the session's row"
            # Unhidden, and as created: $250.00, Projected, the first period.
            assert (
                row.is_deleted, transfer_amount(row), row.status_id,
                row.pay_period_id,
            ) == (
                False, Decimal("250.00"), td["projected_status"].id,
                td["periods"][0].id,
            )


class TestDueDateAndSettleDayShadows:
    """A transfer's due date and each side's settle day, as the doors write them.

    The due date is a column of the transfer's row, read re-read from the
    database; each side's settle day is its payment record's, read by its
    own SQL (:func:`tests._test_helpers.transfer_side_record`).  What a side
    shows of the row's due date is ``test_transfer_legs/test_what_a_side_shows.py``'s
    ``TestBothSidesShowTheTransfer``, both sides.  Until plan step
    ``balance:X-bi-6-4d-3`` each due-date case also read the transfer's two
    shadow rows, which that step's create door no longer writes (ruling
    R-BAL290 part 1).  The name is kept as history.
    """

    def test_a_due_date_at_create_is_written_on_the_transfer(
        self, app, transfer_data,
    ):
        """create_transfer with a due date writes it on the transfer's row.

        Read off the row re-read from the database since plan step
        ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-2b, where it read the
        object the door returned and both shadow rows' due dates (ruling
        R-BAL290 part 1).  Renamed from ``test_shadow_due_date_propagation``.
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
                    amount_ownership=AmountOwnership.own(Decimal("250.00")),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                    due_date=date(2026, 1, 15),
                ),
            )

            assert _reread(xfer.id).due_date == date(2026, 1, 15)

    def test_no_due_date_at_create_leaves_the_transfer_undated(
        self, app, transfer_data,
    ):
        """create_transfer with no due date leaves the transfer's due date empty.

        Read off the row re-read from the database since plan step
        ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-2b, where it read the
        object the door returned and both shadow rows' due dates (ruling
        R-BAL290 part 1); a create that dated an undated transfer fails it
        (measured there).  Renamed from
        ``test_shadow_due_date_null_propagation``.
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
                    amount_ownership=AmountOwnership.own(Decimal("250.00")),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                    due_date=None,
                ),
            )

            assert _reread(xfer.id).due_date is None

    def test_a_due_date_edit_writes_the_transfers_due_date(
        self, app, transfer_data,
    ):
        """An edit naming a due date writes it on the transfer's row.

        The transfer is created with none, so the edit moves it.  Read off
        the row re-read from the database since plan step
        ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-2b, where it read the
        object in the session and both shadow rows' due dates (ruling
        R-BAL290 part 1).  Renamed from ``test_shadow_due_date_update``.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            assert xfer.due_date is None, "precondition: created undated"

            transfer_service.update_transfer(
                xfer.id, td["user"].id, due_date=date(2026, 2, 1)
            )

            assert _reread(xfer.id).due_date == date(2026, 2, 1)

    def test_a_settle_day_correction_lands_on_both_shadows(
        self, app, db, transfer_data,
    ):
        """A day corrected on ONE side reaches both sides: the other borrows it.

        The correction door (ruling **R-ED**, per side since plan step
        ``balance:X-bi-6-4c-3``, ruling **R-BAL142**): the user read their
        statement and the money left the source account on a day other than
        the one Paid was pressed on.  They correct that side; the other side
        holds no day of its own -- a Paid press states none -- so it BORROWS
        the corrected day, and both sides' payment records land on it.  Since
        plan step ``balance:X-bi-6-3`` the posting writer files each side's
        entry under that side's own covering movement's day, so the two land
        together.  Each side's day is its RECORD's since plan step
        ``balance:X-bi-6-4d-2``; the shadows carried it until then (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).  The name is kept
        as history.
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
                xfer.id, td["user"].id,
                side_days=(transfer_service.SideDay(
                    xfer.from_account_id, an_entered_day(corrected),
                ),),
            )
            db.session.flush()

            for account_id in (xfer.from_account_id, xfer.to_account_id):
                assert transfer_side_record(
                    db.session, xfer.id, account_id,
                ).settled_on == corrected

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

        It also asked the transfer's shadow rows for a day after the refusal
        until plan step ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-2b.  From
        that step's create door no row can name a transfer
        (``ck_transactions_names_no_transfer``), so the loop ran over none
        (a vacuous read, structurally empty), and it is dropped, not moved
        onto each side's record: with the three refusals the update asks
        deleted (its own before the first write, the transfer status
        writer's and the status seam's), the update raises nothing, writes
        no record or day on either side and leaves the transfer Projected,
        so this case fails first at the raise; with its own refusal alone
        deleted, the transfer status writer's refuses (all measured at that
        checkpoint).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            db.session.flush()

            with pytest.raises(ValidationError) as exc:
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    side_days=(transfer_service.SideDay(
                        xfer.from_account_id, an_entered_day(display_today()),
                    ),),
                )
            assert "not a settled status" in str(exc.value)

    def test_a_settled_transfer_refuses_to_have_its_day_CLEARED(
        self, app, db, transfer_data,
    ):
        """Clearing a settled transfer's day cannot be asked; reverting is the way.

        The other half of finding **N-183**.  A settled row with no day is the
        state ``balance_predicates.settled_day`` REFUSES, so letting an edit
        produce one would turn a form submission into a 500 on every balance
        surface that folds the row.  The service refused ``settle_day=None``
        with a ``ValidationError`` ("cannot be cleared") until plan step
        ``balance:X-bi-6-4c-3``; since then a door states a day PER SIDE as a
        :class:`~app.services.transfer_service.SideDay`, which cannot wrap
        ``None``, so the request has no spelling and the refusal moved to the
        value's construction.  The legitimate way to remove the day is to move
        the transfer out of the settled band, which the seam does as part of
        the status change -- asserted below so the refusal is not mistaken for
        "the day can never be removed".  Each side's day is its payment
        RECORD's since plan step ``balance:X-bi-6-4d-2``; the shadows carried
        it until then (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
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
            sides = (xfer.from_account_id, xfer.to_account_id)

            with pytest.raises(ValueError) as exc:
                transfer_service.SideDay(xfer.from_account_id, None)
            assert "cannot wrap None" in str(exc.value)

            # The day survived the refusal.
            for account_id in sides:
                assert transfer_side_record(
                    db.session, xfer.id, account_id,
                ).settled_on == display_today()

            # And the supported route out clears it on both sides.
            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                status_id=td["projected_status"].id,
            )
            db.session.flush()
            for account_id in sides:
                assert transfer_side_settle_day(
                    db.session, xfer.id, account_id,
                ) is None

    def test_an_undated_projected_transfer_stays_undated(self, app, transfer_data):
        """Stating NO day on a PROJECTED transfer is a no-op: it stays Projected.

        A no-op rather than a refusal: the submitted value agrees with the row's
        state (no money has moved, so there is no day), and refusing an edit
        that asks for what is already true would reject an ordinary form
        round-trip that carried an empty date field.

        The status is read off the row re-read from the database since plan
        step ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-2b, where it asked
        the transfer's two shadow rows for a day (ruling R-BAL290 part 1):
        an update that read the empty statement as a settle fails it first
        (measured there).  No side's day is read.  The status seam writes no
        day for a change that neither enters nor leaves the settled band, so
        an update that dated a side would have settled the transfer and
        failed the status read first, and with every refusal of a day on an
        unsettled transfer deleted, an update stating one still writes no
        record or day on either side (measured there).
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id

            transfer_service.update_transfer(
                xfer_id, td["user"].id, side_days=(),
            )

            assert _reread(xfer_id).status_id == td["projected_status"].id


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
        """Return a transfer created Projected and then settled."""
        xfer = transfer_service.create_transfer(
            transfer_service.TransferSpec(
                user_id=td["user"].id,
                from_account_id=td["account"].id,
                to_account_id=td["savings_account"].id,
                pay_period_id=td["periods"][0].id,
                scenario_id=td["scenario"].id,
                amount_ownership=AmountOwnership.own(Decimal(amount)),
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
    def _records(xfer):
        """Return the transfer's ``(from-side, to-side)`` payment records.

        What each side RECORDS since plan step ``balance:X-bi-6-4d-2``, read by
        independent SQL over the side links (ruling R-BAL167 class 4, plan
        step balance:X-bi-6-4d-2); its two shadow rows held it until then.
        """
        return tuple(
            transfer_side_record(db.session, xfer.id, account_id)
            for account_id in (xfer.from_account_id, xfer.to_account_id)
        )

    def test_a_correction_records_on_both_legs_and_on_neither_parent(
        self, app, db, transfer_data,
    ):
        """Transfer Invariant 3 for the settlement record.

        A transfer's money moves on its two sides, so each records its own and
        the two are equal -- exactly as their settle day is.  The PARENT carries
        no such column: each side's record hangs off it by a side link.  Read
        off the sides' records since plan step ``balance:X-bi-6-4d-2``, where
        the shadows held them until then (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).  The status is read off the row re-read from
        the database since plan step ``balance:X-bi-6-4d-3``'s checkpoint
        2e-2b-1b-2, where it read the object in the session (ruling R-BAL290
        part 1).
        """
        with app.app_context():
            td = transfer_data
            xfer = self._settled_transfer(td)
            day = self._records(xfer)[0].settled_on
            assert day is not None, "setup: the settle dated the pair"
            typed_id = ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.TYPED,
            )

            transfer_service.update_transfer(
                xfer.id, td["user"].id, figure=typed(Decimal("263.11")),
            )

            row = _reread(xfer.id)
            for record in self._records(row):
                assert (record.amount, record.figure_source_id) == (
                    Decimal("263.11"), typed_id,
                )
                assert record.settled_on == day, (
                    "a figure correction moved the pair's day"
                )
            # The PARENT went through the seam too, and came out on its status,
            # Paid -- the one status the pair has, which each shadow mirrored
            # until plan step ``balance:X-bi-6-4d-2`` (Transfer Invariant 4).
            # It deliberately does NOT assert ``not hasattr(xfer,
            # "settled_amount")``: the seam gates its whole record block on
            # ``isinstance(row, Transaction)``, so handing the parent a record
            # is a silent no-op and only a SCHEMA change could fail such an
            # assertion -- the "``hasattr`` is not a test" shape plan step X-aa
            # already paid for (neutral review, 2026-08-18).
            assert row.status_id == ref_cache.status_id(StatusEnum.DONE), (
                "the correction moved the pair's status"
            )

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
        Both writes are read off the row re-read from the database since plan
        step ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-1b-2, where they read
        the object in the session (ruling R-BAL290 part 1).

        It also asked each of the transfer's shadow rows for a recorded
        settlement until that checkpoint.  From that step's create door on no
        row can name a transfer (``ck_transactions_names_no_transfer``), so
        the loop ran over none (a vacuous read: such rows counted 0 before
        the update and after it, measured at that checkpoint), and it is
        dropped, not moved onto each side's record: with this gate deleted,
        the status seam refuses the figure itself, in other words, before
        either side records anything, and this case fails first at the
        refusal's words, the flag and the amount left written (measured
        there).
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
                    amount_ownership=AmountOwnership.own(Decimal("250.00")),
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
                    amount_ownership=AmountOwnership.own(Decimal("999.00")),
                    figure=typed(Decimal("50.00")),
                )

            assert "has nothing to record" in str(exc.value)
            # BOTH pre-settle writes are asserted untouched, and both are
            # needed: ``is_override`` is applied FIRST and ``amount`` second, so
            # a gate that slipped BETWEEN them would leave one written and pass
            # a test that checked only the other.
            row = _reread(xfer.id)
            assert row.is_override is False, (
                "the refusal ran AFTER the is_override write"
            )
            assert row.amount == Decimal("250.00"), (
                "the refusal ran AFTER the amount write"
            )

    def test_an_echo_leaves_the_resolved_source_standing(
        self, app, db, transfer_data,
    ):
        """Re-posting the prefilled figure must not manufacture a correction.

        The movement's source is the only stored signal that a human read a
        number off a statement, and this form submits every input it renders
        on every save.  The movement is each side's payment record, read off
        the transfer's side links since plan step ``balance:X-bi-6-4d-2``
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            td = transfer_data
            xfer = self._settled_transfer(td)
            resolved = ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.RESOLVED,
            )
            assert all(
                record.figure_source_id == resolved
                for record in self._records(xfer)
            )

            transfer_service.update_transfer(
                xfer.id, td["user"].id, figure=typed(Decimal("250.00")),
            )
            db.session.flush()

            for record in self._records(xfer):
                assert record.figure_source_id == resolved

    def test_a_settle_still_owns_a_figure_arriving_with_it(
        self, app, db, transfer_data,
    ):
        """The correction door must not have stolen the SETTLE's figure.

        A figure riding a settling ``status_id`` is the settle's own
        (``_SETTLE_OWNED_FIELDS``), subject to its echo rule -- and the settle
        writes the status, the pair's day and the record as ONE act.  If the
        correction arm had taken it instead, the figure would be written twice
        and the freeze would be resolved after the status flip, where it always
        answers ``None``.  The record is each side's, read off the transfer's
        side links since plan step ``balance:X-bi-6-4d-2`` (ruling R-BAL167
        class 1, plan step balance:X-bi-6-4d-2).
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
                    amount_ownership=AmountOwnership.own(Decimal("250.00")),
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
                xfer.id, td["user"].id, submitted=typed(Decimal("241.00")),
            )
            db.session.flush()

            assert corrected is True, (
                "the SETTLE did not book the figure -- the correction arm took "
                "it, so the freeze was resolved after the status flip"
            )
            typed_id = ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.TYPED,
            )
            for record in self._records(xfer):
                assert (record.amount, record.figure_source_id) == (
                    Decimal("241.00"), typed_id,
                ), "a figure that differs from the plan IS a correction"
                assert record.settled_on is not None


class TestMovingATransferBetweenAccounts:
    """``update_transfer``'s ENDPOINT arm (plan step R10-b).

    A transfer's source and destination are two of the six columns a recurring
    definition states, and this door could write only four of them -- so a
    definition's account change reached its generated rows by DESTROYING and
    re-creating every one of them, and a NON-repeating transfer refused the same
    edit outright because nothing could carry it.  The arm moves the transfer
    and reconciles the ledger on both sides of the move.  Until plan step
    ``balance:X-bi-6-4d-3`` its two legs were two shadow rows, which the arm
    re-pointed and re-named (that upkeep is deleted at the step's leaf 3b);
    each is derived from the transfer's endpoints now.  So a case that grades
    where the move LEFT the transfer reads the endpoint columns of the row
    re-read from the database since that step's checkpoint 2e-2b-1b-3, where
    it read the session's columns and, in five of the six, legs' accounts or
    labels (ruling R-BAL290 part 1).  Which endpoint each side is on is
    ``test_transfer_legs/test_what_a_side_shows.py``'s ``TestLegOf`` and its
    ``TestBothSidesShowTheTransfer``, and each side's label, composed from
    the endpoints' current names, its ``TestALegsDisplayProjections``.  A
    ledger case that reads the transfer reads the session's row after the
    update's own flush, as the reconcile inside the update does, and
    :meth:`test_moving_a_settled_payment_ONTO_a_loan_reconciles_that_loan`'s
    relationship read is the firing control for a move that assigns the
    foreign key and not the relationship, which a re-read row cannot be: it
    loads the relationship by that column.
    """

    def _other_savings(self, td, name="Second Savings", observed_on=None):
        """Create a second savings account for this owner.

        Through ``account_service.create_account`` rather than an ``Account``
        insert, because that factory is what PAIRS the account with its
        chart-of-accounts ledger -- and an endpoint move posts to that ledger,
        so a hand-rolled row fails the move with a missing-pairing
        ``PostingError`` rather than exercising it.

        *observed_on* is the day its books open (today when ``None``).  A
        case that moves money SETTLED today onto it opens them a day earlier
        (:func:`_the_day_before_today`): the transfer arm refuses a side
        moving on or before its account's books open, by name, since leaf
        ``balance:X-bi-6-4d-2`` (the database refused it at commit before,
        which those cases never reach).  Setup only, approved by the
        developer 2026-10-08 (rule 5, ruling **balance:R-BAL234**): every
        figure they check is unchanged.
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
                observed_on=observed_on,
            ),
        )
        db.session.flush()
        return account

    def test_a_destination_move_writes_the_new_destination(
        self, app, transfer_data
    ):
        """Moving the destination writes the new one and leaves the source where it was.

        What the move wrote, re-read from the database (ruling R-BAL290
        part 1).  It also read both legs' accounts and labels until plan step
        ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-1b-3: the legs were two
        shadow rows whose accounts and names the arm re-wrote until that
        step, and the expense leg's label had to follow a destination it does
        not sit on.  Each leg is derived from the transfer now, so a side's
        account is its endpoint column (``test_transfer_legs/test_what_a_side_shows.py``'s
        ``TestLegOf``) and its label is composed from the endpoints' current
        names (that file's ``TestALegsDisplayProjections``).  Renamed from
        ``test_the_destination_move_carries_the_income_leg_and_its_name``.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            elsewhere = self._other_savings(td)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, to_account_id=elsewhere.id,
            )

            row = _reread(xfer.id)
            assert (row.from_account_id, row.to_account_id) == (
                td["account"].id, elsewhere.id,
            )

    def test_a_source_move_writes_the_new_source(self, app, transfer_data):
        """The mirror of the case above, on the source.

        Renamed from ``test_the_source_move_carries_the_expense_leg_and_its_name``
        at the same checkpoint, which dropped its legs' accounts and labels
        likewise.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            elsewhere = self._other_savings(td, name="New Source")

            transfer_service.update_transfer(
                xfer.id, td["user"].id, from_account_id=elsewhere.id,
            )

            row = _reread(xfer.id)
            assert (row.from_account_id, row.to_account_id) == (
                elsewhere.id, td["savings_account"].id,
            )

    def test_an_update_naming_no_account_leaves_the_transfer_where_it_is(
        self, app, transfer_data
    ):
        """The control: an ordinary edit moves neither endpoint.

        **It carried a NAME half until plan step ``balance:X-bi-6-4d-3``,
        dropped there as a DOMINATED read, with the rename that set nothing.**
        It renamed the destination account and then asserted the edit left
        the two shadow rows' STORED names alone (the arm re-wrote them only on
        a real MOVE).  The rename was set on the fixture's ``Account``, which
        is not in this test's session, so it reached no row: re-read from the
        database after its flush, the account still has its old name
        (measured by the review of that step's checkpoint 2e-1, and again at
        2e-1a).  So the half never exercised its intended subject, the arm's
        re-write of stored names gated on a real move, which goes with that
        arm at the step's leaf 3b.

        Two mutants were measured against the half.  Under one that defaults
        an absent endpoint to the OTHER side, the name half fails on its own,
        and in its original order the endpoint half fails first (measured at
        checkpoint 2e-1a and by its review).  Under one that renames the
        destination ACCOUNT on every edit (the review of checkpoint 2e-2a-1
        named it R-RENAME-ON-EDIT), the name half, re-expressed on the legs,
        fails while the endpoint half passes; in this file the source move's
        expense-label assert failed under it (measured at checkpoint 2e-2a-1a
        and by that review), and only because that case's expected label was
        read off a fixture account outside the test's session.  That case
        reads no label since that step's checkpoint 2e-2b-1b-3, and nothing
        in this file catches the mutant since (measured there).  That a
        leg's name is composed from its endpoints' current names is pinned by
        ``test_transfer_legs/test_what_a_side_shows.py``'s ``TestALegsDisplayProjections``
        (``test_the_label_is_composed_from_the_endpoints_current_names``).
        The endpoint half stands, read off the row re-read from the database
        since that checkpoint, where it read the object in the session
        (ruling R-BAL290 part 1).  Renamed from
        ``test_an_update_naming_no_account_leaves_the_pair_where_it_is``.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, amount_ownership=AmountOwnership.own(Decimal("300.00")),
            )

            row = _reread(xfer.id)
            assert (row.from_account_id, row.to_account_id) == (
                td["account"].id, td["savings_account"].id,
            )

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
        It is planted on the transfer alone since plan step
        ``balance:X-bi-6-4d-3``, where it was planted on the transfer AND its
        expense shadow: a leg is derived from its transfer, so there is no
        second row to plant it on (ruling R-BAL167 class 1).  Each move is
        read off the row re-read from the database since that step's
        checkpoint 2e-2b-1b-3, where it read the object in the session and
        the moved side's leg (ruling R-BAL290 part 1).
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
            # The pre-guard shape: money leaving a loan.
            xfer.from_account = loan
            db.session.flush()

            transfer_service.update_transfer(
                xfer.id, td["user"].id, to_account_id=elsewhere.id,
            )

            row = _reread(xfer.id)
            assert row.from_account_id == loan.id, (
                "the source it did not touch stayed where it was"
            )
            assert row.to_account_id == elsewhere.id

            # And moving that source OFF the loan is the edit the narrowing
            # makes reachable at all -- it is the vacated-SOURCE arm of
            # ``_resync_vacated_loan``, whose stated reason for taking both
            # endpoints was false while the guard refused every such row.
            before_entries = db.session.query(JournalEntry).count()
            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                from_account_id=td["account"].id,
            )
            assert _reread(xfer.id).from_account_id == td["account"].id
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

    def test_a_refused_amount_does_not_move_the_transfer_first(
        self, app, transfer_data
    ):
        """A refusal LATER in the payload still leaves the transfer and its legs untouched.

        **RE-WRITTEN after an adversarial review of plan step R10-b showed the
        first version could not fire.**  It paired a valid amount with an
        endpoint refusal, so the refusal it triggered was ``_resolve_endpoints``'
        own -- the FIRST gate in the function, and green however the writes
        below were ordered.  The property worth pinning is the opposite pairing:
        a VALID move beside an ILLEGAL amount.  The amount's refusal used to sit
        at the arm that assigns it, two writes after the endpoint move and after
        a loan payment's split reversal, so this exact payload moved the pair
        and reversed a ledger correction before deciding the edit was illegal.

        It reads what the refused update would have WRITTEN: the transfer's
        row re-read after a commit, which writes whatever the update staged
        (ruling R-BAL290 part 1, since plan step ``balance:X-bi-6-4d-3``'s
        checkpoint 2e-2b-1b-3).  A move assigns the endpoint RELATIONSHIPS
        (``_apply_endpoint_move``), whose columns sync only at a flush, and
        the update does not flush before the amount's refusal raises; with
        the refusal moved below the endpoint apply, the commit writes the
        staged move and the re-read destination column fails the case
        (measured at that checkpoint).  Until then it flushed explicitly and
        read the session's three columns and both legs' accounts and labels,
        the legs two shadow rows until that step (ruling R-BAL167 class 4);
        at checkpoint 2e-1a the flush was added, since its column elements had
        caught the move only through its leg re-read, a query whose autoflush
        wrote the staged move.  Renamed from
        ``test_a_refused_amount_does_not_move_the_pair_first``.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            elsewhere = self._other_savings(td)
            before = (xfer.from_account_id, xfer.to_account_id, xfer.amount)

            with pytest.raises(ValidationError, match="must be positive"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    to_account_id=elsewhere.id,
                    amount_ownership=AmountOwnership.own(Decimal("-5.00")),
                )

            row = _reread(xfer.id)
            assert (row.from_account_id, row.to_account_id, row.amount) == before

    def test_a_refused_move_leaves_the_transfer_untouched(
        self, app, transfer_data
    ):
        """An endpoint refusal itself writes nothing either.

        The complementary half: the endpoint gates are read-only, so a move
        refused for its OWN reason -- here, endpoints that would end up equal --
        leaves the transfer exactly as it was, beside an amount the same
        request states.  Read off the row re-read after a commit, which writes
        whatever the refused update staged (ruling R-BAL290 part 1), since
        plan step ``balance:X-bi-6-4d-3``'s checkpoint 2e-2b-1b-3, where it
        read the session's three columns and both legs' accounts and labels.
        Renamed from
        ``test_a_refused_move_leaves_the_transfer_and_its_legs_untouched``,
        and before that ``test_a_refused_move_leaves_all_three_rows_untouched``:
        the legs were the transfer's two shadow rows until that step (ruling
        R-BAL167 class 4).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            before = (xfer.from_account_id, xfer.to_account_id, xfer.amount)

            with pytest.raises(ValidationError, match="must be different"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    amount_ownership=AmountOwnership.own(Decimal("999.00")),
                    to_account_id=td["account"].id,
                )

            row = _reread(xfer.id)
            assert (row.from_account_id, row.to_account_id, row.amount) == before

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
        order was fixed.  What THIS case pins is the vacated loan's re-sync
        AFTER the move (``_loan_posting._resync_vacated_loan``): remove it and
        the loan keeps a correction for a payment it no longer has.  Through
        plan step ``balance:X-bi-6-1b`` it pinned a split reversal BEFORE the
        move as well; the split links no row since ``X-bi-6-3`` (ruling
        R-BAL102), so the after-resync alone reverses the departed key.
        """
        with app.app_context():
            td = transfer_data
            loan = create_loan_account(
                {"user": td["user"], "account": td["account"]}, db.session,
                name="Vacated Loan",
                origination_date=date(2020, 1, 1),
            )
            elsewhere = self._other_savings(
                td, name="Not A Loan", observed_on=_the_day_before_today(),
            )
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=loan.id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("250.00")),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                ),
            )
            transfer_service.settle_transfer(
                xfer.id, td["user"].id,
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
                xfer.id, td["user"].id,
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
            elsewhere = self._other_savings(
                td, name="Off The Loan", observed_on=_the_day_before_today(),
            )
            payments = []
            for index in (0, 1):
                payment = transfer_service.create_transfer(
                    transfer_service.TransferSpec(
                        user_id=td["user"].id,
                        from_account_id=td["account"].id,
                        to_account_id=loan.id,
                        pay_period_id=td["periods"][index].id,
                        scenario_id=td["scenario"].id,
                        amount_ownership=AmountOwnership.own(Decimal("400.00")),
                        status_id=td["projected_status"].id,
                        category_id=td["categories"]["Rent"].id,
                        due_date=td["periods"][index].start_date,
                    ),
                )
                transfer_service.settle_transfer(
                    payment.id, td["user"].id,
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
            elsewhere = self._other_savings(
                td, name="Third Savings", observed_on=_the_day_before_today(),
            )
            xfer = _create_basic_transfer(td)
            transfer_service.settle_transfer(
                xfer.id, td["user"].id,
            )
            db.session.flush()
            add_anchor_history(
                db.session, savings, Decimal("900.00"),
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
            elsewhere = self._other_savings(
                td, observed_on=_the_day_before_today(),
            )
            transfer_service.settle_transfer(
                xfer.id, td["user"].id,
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
