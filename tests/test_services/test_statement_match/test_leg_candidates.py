"""Statement match offers a transfer's side as its LEG -- leaf ``balance:X-bi-6-4c-1``.

A still-planned transfer is offered on each of its accounts as the TRANSFER
(``RowKind.LEG``), keyed by the transfer's id on the screen's account
(rulings **R-BAL87**, **R-BAL159**), and a paid one's side as its LEG's
record, a SETTLEMENT whose parent is the transfer rather than a row.  Until
this leaf both were reached through the transfer's SHADOW row on the account
-- a TRANSACTION keyed by the shadow's id, and a SETTLEMENT whose parent was
the shadow -- which plan step ``X-bi-6-4d``, deleting the shadows, would have
left offering nothing.

Each class pins one fact of the new shape: what is offered and how it is
priced, labelled and dated; how an accepted leg is recorded and claimed;
what a damaged transfer does (ruling **R-BAL158**); how the two drifted
states no door writes are decided by the PARENT (ruling **R-JM**, R-BAL79);
and the re-price a stale form meets.  The before/after grade over the
2026-09-30 00:11 production dump lives with the leaf's handoff, not here.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import (
    account_service,
    pay_calendar,
    statement_match,
    transfer_service,
)
from app.services.statement_match import (
    CandidateRow,
    Candidates,
    MatchSubmission,
    ReviewedRow,
    RowKind,
    accepted_register,
    as_reviewed,
    candidates_for,
    matched_subjects,
)
from app.services.statement_match._candidates import (
    MatchedSubjects,
    unmatched_rows,
)
from app.services.statement_match._valuation import repriced
from tests._test_helpers import (
    an_entered_day,
    create_transfer,
    on_both_sides,
    open_books_before_the_first_assertion,
)

from ._builders import (
    a_bank_line,
    a_basis,
    a_later_period,
    a_scope,
    a_submission,
    a_transaction,
    an_import,
)

_AMOUNT = Decimal("250.00")


def _savings(seed_user):
    """Return a second account the transfers land on, its books open before the bootstrap period."""
    savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=seed_user["account"].account_type_id,
            name="Savings",
            anchor_balance=Decimal("100.00"),
            observed_on=seed_user["bootstrap_period"].start_date,
        )
    )
    db.session.flush()
    open_books_before_the_first_assertion(db.session, savings)
    return savings


def _a_transfer(seed_user, savings=None, amount=_AMOUNT):
    """Return a Projected transfer (``$250.00`` by default) from the seeded checking account to *savings*."""
    savings = savings or _savings(seed_user)
    transfer = create_transfer(
        seed_user, db.session, seed_user["account"], savings,
        seed_user["bootstrap_period"], amount=amount,
    )
    db.session.flush()
    return transfer


def _shadow(transfer, account_id):
    """Return *transfer*'s shadow row on *account_id* -- for planting and asserting only."""
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.transfer_id == transfer.id,
            Transaction.account_id == account_id,
        )
        .one()
    )


def _offered(seed_user, account_id):
    """Return *account_id*'s whole offer set, as the review pass derives it."""
    return candidates_for(
        account_id,
        pay_calendar.calendar_for(seed_user["user"].id),
        a_basis(seed_user),
    )


def _of_transfer(rows, transfer):
    """Return the candidates that are a side of *transfer*."""
    return [row for row in rows if row.transfer_id == transfer.id]


def _settle(seed_user, transfer, days_in=3):
    """Settle *transfer* through its own door, both sides dated *days_in* days into its period."""
    transfer_service.settle_transfer(
        transfer.id, seed_user["user"].id,
        side_days=on_both_sides(
            transfer.from_account_id, transfer.to_account_id,
            an_entered_day(
                seed_user["bootstrap_period"].start_date + timedelta(days=days_in),
            ),
        ),
    )
    db.session.flush()


def _revert(seed_user, transfer):
    """Revert *transfer* to Projected through its own door (its sides' movements are kept un-dated, R-BAL61)."""
    transfer_service.update_transfer(
        transfer.id, seed_user["user"].id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.flush()


class TestAStillPlannedTransferIsOfferedAsItsLeg:
    """A Projected transfer is the TRANSFER on each account, never its shadow row."""

    def test_each_side_is_offered_on_its_own_account_keyed_by_the_transfer(
        self, app, db, seed_user,
    ):
        """One LEG per account, signed by its side, priced and dated as the transfer."""
        savings = _savings(seed_user)
        transfer = _a_transfer(seed_user, savings)
        checking = seed_user["account"]

        (out,) = _of_transfer(_offered(seed_user, checking.id).rows, transfer)
        (into,) = _of_transfer(_offered(seed_user, savings.id).rows, transfer)

        assert (out.kind, out.row_id) == (RowKind.LEG, transfer.id)
        assert (into.kind, into.row_id) == (RowKind.LEG, transfer.id)
        assert out.cash_amount == -_AMOUNT
        assert into.cash_amount == _AMOUNT
        assert out.label == f"Transfer to {savings.name} (transfer leg)"
        assert into.label == f"Transfer from {checking.name} (transfer leg)"
        for leg in (out, into):
            assert leg.transfer_id == transfer.id
            assert leg.parent_id is None
            assert leg.transaction_id is None
            assert leg.settled_on is None and not leg.is_settled
            assert leg.version_id == transfer.version_id
            assert leg.period.period_id == transfer.pay_period_id
            # One side of a transfer is never corrected alone (invariant 3).
            assert leg.figure_is_correctable is False

    def test_several_legs_are_offered_in_transfer_id_order(
        self, app, db, seed_user,
    ):
        """The leg run's order is a function of the data: the transfer's id.

        The LOWER id is moved to a later paycheck through its door, so its
        row is written again at the heap's end and indexed after the other
        (a non-HOT update: ``pay_period_id`` is an indexed column) -- a
        loader returning physical or index order returns it SECOND, and only
        the arm's own sort by transfer id puts it first.
        """
        savings = _savings(seed_user)
        first = _a_transfer(seed_user, savings, amount=Decimal("400.00"))
        second = _a_transfer(seed_user, savings)
        assert first.id < second.id
        transfer_service.update_transfer(
            first.id, seed_user["user"].id,
            pay_period_id=a_later_period(seed_user).id,
        )
        db.session.flush()

        legs = [
            row for row in _offered(seed_user, seed_user["account"].id).rows
            if row.kind is RowKind.LEG
        ]

        assert [(row.row_id, row.cash_amount) for row in legs] == [
            (first.id, Decimal("-400.00")), (second.id, -_AMOUNT),
        ]

    def test_a_leg_is_not_claimed_by_an_entry_sharing_its_id(self):
        """A transfer's id and a movement's are separate sequences, so N names both.

        An act on this account naming entry 7 claims entry 7 -- never transfer
        7's leg, which only :attr:`MatchedSubjects.legs` claims.
        """
        leg = CandidateRow(
            kind=RowKind.LEG, row_id=7, label="Transfer to Savings (transfer leg)",
            cash_amount=-_AMOUNT, settled_on=None, is_settled=False,
            states_own_figure=True, version_id=1, transfer_id=7,
        )
        offered = Candidates(rows=[leg], unpriceable=())

        entry_seven = MatchedSubjects(
            lines=frozenset(), transactions=frozenset(),
            entries=frozenset({7}), legs=frozenset(),
        )
        leg_seven = MatchedSubjects(
            lines=frozenset(), transactions=frozenset(),
            entries=frozenset(), legs=frozenset({7}),
        )

        assert unmatched_rows(offered, entry_seven) == [leg]
        assert unmatched_rows(offered, leg_seven) == []

    def test_no_shadow_row_is_ever_a_candidate(self, app, db, seed_user):
        """Neither arm that reads a ROW offers a shadow: the leg is the transfer's only subject."""
        transfer = _a_transfer(seed_user)
        shadow = _shadow(transfer, seed_user["account"].id)
        shadow_ids = {shadow.id}

        rows = _offered(seed_user, seed_user["account"].id).rows

        assert not [
            row for row in rows
            if row.kind is RowKind.TRANSACTION and row.row_id in shadow_ids
        ]
        assert len(_of_transfer(rows, transfer)) == 1

    def test_a_deleted_transfer_is_not_offered(self, app, db, seed_user):
        """The parent's soft delete takes both sides off every screen."""
        transfer = _a_transfer(seed_user)
        transfer_service.delete_transfer(
            transfer.id, seed_user["user"].id, soft=True,
        )
        db.session.flush()

        rows = _offered(seed_user, seed_user["account"].id).rows

        assert _of_transfer(rows, transfer) == []

    def test_a_renamed_account_relabels_the_leg(self, app, db, seed_user):
        """DECLARED: the label is composed from the endpoints' CURRENT names.

        The shadow's stored ``name`` was written when the transfer was made
        and never re-labelled; a leg reads the endpoints (ruling **R-BAL87**,
        leaf ``X-bi-6-1``'s change on the grid).  0 of 256 live shadows
        differed on the 2026-09-30 00:11 production dump, so no screen moved
        on today's data.
        """
        savings = _savings(seed_user)
        transfer = _a_transfer(seed_user, savings)
        savings.name = "Emergency Fund"
        db.session.flush()

        (leg,) = _of_transfer(
            _offered(seed_user, seed_user["account"].id).rows, transfer,
        )

        assert leg.label == "Transfer to Emergency Fund (transfer leg)"
        assert _shadow(transfer, seed_user["account"].id).name == (
            "Transfer to Savings"
        )


class TestAPaidSideIsItsLegsRecord:
    """A settled transfer's side is offered as its covering movement, whose parent is the leg."""

    def test_each_paid_side_is_its_own_movement_on_its_own_account(
        self, app, db, seed_user,
    ):
        """A SETTLEMENT keyed by the side's movement, carrying the transfer and no row."""
        savings = _savings(seed_user)
        transfer = _a_transfer(seed_user, savings)
        _settle(seed_user, transfer)
        checking = seed_user["account"]

        (out,) = _of_transfer(_offered(seed_user, checking.id).rows, transfer)
        (into,) = _of_transfer(_offered(seed_user, savings.id).rows, transfer)

        out_movement = _shadow(transfer, checking.id).covering_movements[0]
        into_movement = _shadow(transfer, savings.id).covering_movements[0]
        assert (out.kind, out.row_id) == (RowKind.SETTLEMENT, out_movement.id)
        assert (into.kind, into.row_id) == (
            RowKind.SETTLEMENT, into_movement.id,
        )
        assert out.cash_amount == -_AMOUNT
        assert into.cash_amount == _AMOUNT
        for side, movement in ((out, out_movement), (into, into_movement)):
            assert side.transfer_id == transfer.id
            assert side.parent_id is None
            assert side.transaction_id is None
            assert side.settled_on == movement.settled_on
            assert side.settle_day_basis is SettledDayBasisEnum.ENTERED
            assert side.is_settled
            assert side.version_id == movement.version_id + transfer.version_id

    def test_row_and_leg_payments_are_one_run_by_recorded_day(
        self, app, db, seed_user,
    ):
        """A leg's payment takes its day's place among the rows' payments."""
        start = seed_user["bootstrap_period"].start_date
        savings = _savings(seed_user)
        early = _a_transfer(seed_user, savings)
        _settle(seed_user, early, days_in=2)
        bill = a_transaction(
            seed_user, name="Power", amount="80.00",
            status=StatusEnum.DONE, settled_on=start + timedelta(days=5),
        )
        late = _a_transfer(seed_user, savings, amount=Decimal("90.00"))
        _settle(seed_user, late, days_in=7)
        db.session.flush()

        payments = [
            (row.transfer_id, row.settled_on) for row in
            _offered(seed_user, seed_user["account"].id).rows
            if row.kind is RowKind.SETTLEMENT
        ]

        assert payments == [
            (early.id, start + timedelta(days=2)),
            (None, bill.settled_on),
            (late.id, start + timedelta(days=7)),
        ]

    def test_a_reverted_transfer_is_a_leg_again_and_its_kept_movement_is_not_offered(
        self, app, db, seed_user,
    ):
        """One subject per side per screen: the LEG, not the un-dated movement a revert kept."""
        transfer = _a_transfer(seed_user)
        _settle(seed_user, transfer)
        _revert(seed_user, transfer)
        kept = _shadow(transfer, seed_user["account"].id).covering_movements
        assert len(kept) == 1 and kept[0].settled_on is None

        (only,) = _of_transfer(
            _offered(seed_user, seed_user["account"].id).rows, transfer,
        )

        assert (only.kind, only.row_id) == (RowKind.LEG, transfer.id)
        assert only.cash_amount == -_AMOUNT


class TestAcceptingALeg:
    """The act settles the TRANSFER and records this side's covering movement."""

    def test_the_transfer_settles_on_the_banks_day_and_the_member_is_this_sides_movement(
        self, app, db, seed_user,
    ):
        """Both sides move through the transfer's door; the act names Checking's movement."""
        savings = _savings(seed_user)
        transfer = _a_transfer(seed_user, savings)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-250.00",
            posted_on=bank_day,
        )
        scope = a_scope(seed_user)

        accepted = statement_match.accept_match(
            a_submission(scope, lines=[line], transfers=[transfer]), scope,
        )
        db.session.flush()

        assert accepted.settled_count == 1
        assert transfer.status.is_settled
        out = _shadow(transfer, seed_user["account"].id).covering_movements[0]
        into = _shadow(transfer, savings.id).covering_movements[0]
        assert out.settled_on == bank_day
        assert into.settled_on == bank_day
        assert out.amount == _AMOUNT and into.amount == _AMOUNT
        assert matched_subjects(seed_user["account"].id).entries == {out.id}

    def test_one_of_several_legs_is_accepted_alone(self, app, db, seed_user):
        """The re-price narrows to the named transfer: its sibling stays planned."""
        savings = _savings(seed_user)
        other = _a_transfer(seed_user, savings, amount=Decimal("400.00"))
        named = _a_transfer(seed_user, savings)
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-250.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        scope = a_scope(seed_user)

        accepted = statement_match.accept_match(
            a_submission(scope, lines=[line], transfers=[named]), scope,
        )
        db.session.flush()

        assert accepted.settled_count == 1
        assert named.status.is_settled
        assert not other.status.is_settled

    def test_the_register_values_and_names_the_member_through_its_leg(
        self, app, db, seed_user,
    ):
        """The accepted act reads the leg's label and what its movement moves on this account."""
        savings = _savings(seed_user)
        transfer = _a_transfer(seed_user, savings)
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-250.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        scope = a_scope(seed_user)
        statement_match.accept_match(
            a_submission(scope, lines=[line], transfers=[transfer]), scope,
        )
        db.session.flush()

        register = accepted_register(
            seed_user["user"].id, seed_user["account"].id, limit=None,
        )

        (group,) = register.shown
        (row,) = group.rows
        assert row.label == f"Transfer to {savings.name}"
        assert row.cash_amount == -_AMOUNT
        assert group.agrees

    def test_a_reverted_members_kept_movement_is_worth_nothing_in_the_register(
        self, app, db, seed_user,
    ):
        """The leg's record is un-dated after a revert, so the act stops holding by arithmetic."""
        transfer = _a_transfer(seed_user)
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-250.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        scope = a_scope(seed_user)
        statement_match.accept_match(
            a_submission(scope, lines=[line], transfers=[transfer]), scope,
        )
        _revert(seed_user, transfer)

        (group,) = accepted_register(
            seed_user["user"].id, seed_user["account"].id, limit=None,
        ).shown

        assert group.rows[0].cash_amount == Decimal("0")
        assert not group.agrees

    def test_the_leg_is_claimed_through_its_kept_movement_and_the_other_side_is_not(
        self, app, db, seed_user,
    ):
        """Checking's act claims Checking's side alone; Savings' side stays on offer."""
        savings = _savings(seed_user)
        transfer = _a_transfer(seed_user, savings)
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-250.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        scope = a_scope(seed_user)
        statement_match.accept_match(
            a_submission(scope, lines=[line], transfers=[transfer]), scope,
        )
        _revert(seed_user, transfer)
        checking_id = seed_user["account"].id

        claims = matched_subjects(checking_id)
        still = unmatched_rows(_offered(seed_user, checking_id), claims)
        on_savings = unmatched_rows(
            _offered(seed_user, savings.id), matched_subjects(savings.id),
        )

        assert claims.legs == {transfer.id}
        assert _of_transfer(still, transfer) == []
        assert [(r.kind, r.row_id) for r in _of_transfer(on_savings, transfer)] == [
            (RowKind.LEG, transfer.id),
        ]


class TestADamagedTransferIsSkippedAndCounted:
    """Ruling **R-BAL158** ("Skip it and say so"): not offered, counted as unpriceable."""

    def test_a_broken_shadow_pair_is_counted_and_everything_else_is_offered(
        self, app, db, seed_user,
    ):
        """The Savings-side shadow deleted around the service -- a state no door writes."""
        savings = _savings(seed_user)
        transfer = _a_transfer(seed_user, savings)
        bill = a_transaction(seed_user, name="Power", amount="80.00")
        _shadow(transfer, savings.id).is_deleted = True
        db.session.flush()

        offered = _offered(seed_user, seed_user["account"].id)

        assert _of_transfer(offered.rows, transfer) == []
        assert (RowKind.LEG, transfer.id) in offered.unpriceable
        assert [
            row.row_id for row in offered.rows
            if row.kind is RowKind.TRANSACTION
        ] == [bill.id]
        review = statement_match.review_set(a_scope(seed_user))
        assert review.bounds.unpriceable_count == 1

    def test_a_damaged_reverted_transfer_is_counted_once(
        self, app, db, seed_user,
    ):
        """Its kept, un-dated record is never priced, so the note counts ONE transfer, not two."""
        savings = _savings(seed_user)
        transfer = _a_transfer(seed_user, savings)
        _settle(seed_user, transfer)
        _revert(seed_user, transfer)
        _shadow(transfer, savings.id).is_deleted = True
        db.session.flush()

        offered = _offered(seed_user, seed_user["account"].id)

        assert offered.unpriceable == ((RowKind.LEG, transfer.id),)
        assert _of_transfer(offered.rows, transfer) == []
        review = statement_match.review_set(a_scope(seed_user))
        assert review.bounds.unpriceable_count == 1


class TestTheParentDecidesADriftedSide:
    """DECLARED, all three states unwritable by any door (ruling **R-JM**; R-BAL79 per side, R-BAL80)."""

    def test_a_settled_transfer_over_an_undated_kept_record_offers_nothing(
        self, app, db, seed_user,
    ):
        """No LEG (not Projected) and no payment (its record is not dated).

        Through leaf ``X-bi-6-4c-2`` the Projected shadow was offered as a row,
        and an Apply of it dated the kept record on the owner's TODAY rather
        than the bank's day: the settle door keeps a settled parent's status
        and drops the day it was given (measured 2026-09-30).
        """
        transfer = _a_transfer(seed_user)
        _settle(seed_user, transfer)
        _revert(seed_user, transfer)
        transfer.status_id = ref_cache.status_id(StatusEnum.DONE)
        db.session.flush()

        rows = _offered(seed_user, seed_user["account"].id).rows

        assert _of_transfer(rows, transfer) == []

    def test_a_projected_transfer_whose_side_is_dated_offers_the_movement(
        self, app, db, seed_user,
    ):
        """The movement, not the status, decides a side: a dated one is the SETTLEMENT.

        Through leaf ``X-bi-6-4c-2`` the shadow was offered as a Projected row
        at the settle price and its movement hidden; the leg arm emits a side
        only while its dated movement does not exist, so the dated movement
        is the subject, at its recorded figure.
        """
        transfer = _a_transfer(seed_user)
        _settle(seed_user, transfer)
        projected = ref_cache.status_id(StatusEnum.PROJECTED)
        transfer.status_id = projected
        for shadow in transfer.shadow_transactions:
            shadow.status_id = projected
        db.session.flush()

        (only,) = _of_transfer(
            _offered(seed_user, seed_user["account"].id).rows, transfer,
        )

        assert only.kind is RowKind.SETTLEMENT
        assert only.cash_amount == -_AMOUNT

    def test_a_settled_transfer_over_projected_shadows_offers_nothing(
        self, app, db, seed_user,
    ):
        """No LEG (the parent is not Projected) and no movement to offer (none was written)."""
        transfer = _a_transfer(seed_user)
        transfer.status_id = ref_cache.status_id(StatusEnum.DONE)
        db.session.flush()

        rows = _offered(seed_user, seed_user["account"].id).rows

        assert _of_transfer(rows, transfer) == []


class TestAStaleFormIsRefused:
    """A leg re-reads its offer scope at Apply."""

    def test_a_transfer_settled_since_the_screen_was_drawn_is_no_longer_available(
        self, app, db, seed_user,
    ):
        """The LEG arm of the re-price answers None, and the act is refused unwritten."""
        transfer = _a_transfer(seed_user)
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-250.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        scope = a_scope(seed_user)
        (leg,) = _of_transfer(scope.candidates.rows, transfer)
        submission = MatchSubmission(
            line_ids=frozenset({line.id}),
            rows=frozenset({as_reviewed(leg)}),
        )
        _settle(seed_user, transfer)

        with pytest.raises(ValidationError, match="no longer available"):
            statement_match.accept_match(submission, scope)

    def test_a_paid_side_reverted_since_the_screen_was_drawn_re_prices_to_nothing(
        self, app, db, seed_user,
    ):
        """The leg-payment arm re-asks the partition: a reverted side is the LEG's again.

        ``repriced`` is asked directly because the Apply door's review-version
        check would refuse the act first (both counters rise on a revert), and
        a partition graded only behind that check is graded by nothing.
        """
        transfer = _a_transfer(seed_user)
        _settle(seed_user, transfer)
        scope = a_scope(seed_user)
        (paid,) = _of_transfer(scope.candidates.rows, transfer)
        assert paid.kind is RowKind.SETTLEMENT
        _revert(seed_user, transfer)

        assert repriced(
            paid, scope.calendar, scope.basis, seed_user["account"].id,
        ) is None

    def test_a_token_naming_a_transfer_off_this_account_is_refused(
        self, app, db, seed_user,
    ):
        """The screen's account IS the leg's (R-BAL159): another account's transfer is not found."""
        other = _savings(seed_user)
        elsewhere = create_transfer(
            seed_user, db.session, other,
            account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=seed_user["account"].account_type_id,
                    name="Brokerage",
                    anchor_balance=Decimal("100.00"),
                    observed_on=seed_user["bootstrap_period"].start_date,
                )
            ),
            seed_user["bootstrap_period"], amount=_AMOUNT,
        )
        db.session.flush()
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-250.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        scope = a_scope(seed_user)
        forged = ReviewedRow(
            kind=RowKind.LEG, row_id=elsewhere.id,
            cash_amount=-_AMOUNT, version_id=elsewhere.version_id,
        )

        with pytest.raises(ValidationError, match="no longer available"):
            statement_match.accept_match(
                MatchSubmission(
                    line_ids=frozenset({line.id}), rows=frozenset({forged}),
                ),
                scope,
            )
        assert not elsewhere.status.is_settled
