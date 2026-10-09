"""Plan step ``balance:X-bi-6-4a-3``: a transfer answers the pay-period doors for itself.

Rulings **R-BAL125** and **R-BAL157** (developer 2026-09-23, 2026-09-30):
every door that asks "does this transfer hold a payment or purchase" asks it
ONCE, of the transfer, through ``transfer_legs.transfer_holds_a_movement`` --
the pay-period lock, the reset gate, "Remove earlier paychecks" and the three
archive or delete doors -- so plan step ``X-bi-6-4d``, which moves a
transfer's movements off its shadow rows, has one place to move.  And the
lock and the reset read a transfer's settled state off its OWN status, not its
shadows'.  Ruling **R-BAL126**: the reset refusal counts a transfer ONCE.

Each door is graded where it already has a test that stays unchanged:
``test_cc5_4a4_history_doors`` (the recurring transfer's delete and archive,
the account delete over a HIDDEN leg's kept payment) and
``test_pay_period_remove_earlier``'s
``test_a_transfer_done_then_set_back_is_refused_once`` (both legs live, and
hidden).  What this module adds is what none of those grades: the clause's
own scope, the lock reading a transfer that no row in its period explains,
and the two reset counts at their new unit.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import PayPeriodResetBlocked
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import pay_period_admin, pay_period_gates, transfer_service
from app.services.pay_calendar import calendar_for
from app.services.pay_period_locks import (
    PeriodLockReason,
    classify_schedule_locks,
)
from app.services.transfer_legs import transfer_holds_a_movement
from app.utils.balance_predicates import is_projected
from app.utils.dates import display_today
from tests._test_helpers import (
    add_entry,
    add_txn,
    create_account_of_type,
    create_settled_transfer,
    create_transfer,
    refused_by_database_rule,
    rhythm_of,
    settle_day_columns,
    transfer_side_record,
)


def _savings(seed_user, periods):
    """A Savings account the transfers pay into, its books open before them."""
    savings = create_account_of_type(
        seed_user, db.session, "Savings", "Savings",
        anchor_balance=Decimal("0.00"), observed_on=periods[0].start_date,
    )
    db.session.commit()
    return savings


def _paid_500(seed_user, periods):
    """A $500.00 Checking -> Savings transfer in today's period, Paid today."""
    xfer = create_settled_transfer(
        seed_user, db.session, seed_user["account"],
        _savings(seed_user, periods), periods[4],
        amount=Decimal("500.00"), settled_on=display_today(),
    )
    db.session.commit()
    return xfer


def _reverted_500(seed_user, periods):
    """The $500.00 transfer Paid, then set back: each side KEEPS its payment.

    Ruling **R-BAL61**: a revert keeps the covering movement, un-dated, so a
    bank match on it still stands.  The transfer is Projected and holds two
    records, one per side -- on the TRANSFER since plan step
    ``balance:X-bi-6-4d-2`` (ruling **R-BAL88**), where they hung off its
    legs until then.
    """
    xfer = _paid_500(seed_user, periods)
    transfer_service.update_transfer(
        xfer.id, seed_user["user"].id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()
    return xfer


def _legs(xfer):
    """The transfer's two shadow rows, dead or alive."""
    return db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()


def _side_records(xfer):
    """The records the transfer's two sides hold, read by their own SQL.

    From-side then to-side, ``None`` for a side holding none
    (:func:`~tests._test_helpers.transfer_side_record`, never the clause
    under test).
    """
    return [
        transfer_side_record(db.session, xfer.id, account_id)
        for account_id in (xfer.from_account_id, xfer.to_account_id)
    ]


def _held_transfer_ids():
    """The ids of the transfers the one clause says hold a movement."""
    return {
        transfer_id for (transfer_id,) in db.session.query(Transfer.id).filter(
            transfer_holds_a_movement(),
        )
    }


class TestTheOneClause:
    """``transfer_holds_a_movement``: any record on either side, whatever its twins are.

    It asked about any entry under any shadow, live or dead, until plan step
    ``balance:X-bi-6-4d-2`` moved each side's record onto the transfer
    (ruling **R-BAL88**); a twin's own state is read by no reader since.
    """

    def test_a_transfer_holding_nothing_is_not_held(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A Projected transfer never paid holds no entry: the control."""
        with app.app_context():
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"],
                _savings(seed_user, seed_periods_today), seed_periods_today[5],
                amount=Decimal("500.00"),
            )
            db.session.commit()

            assert xfer.id not in _held_transfer_ids()

    def test_a_reverted_transfer_is_held_by_the_payment_it_kept(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Projected again, and still holding two $500.00 payments.

        Each SIDE keeps its record through the revert, where each leg kept
        its entry until plan step ``balance:X-bi-6-4d-2`` (ruling
        **R-BAL167** class 1).
        """
        with app.app_context():
            xfer = _reverted_500(seed_user, seed_periods_today)
            assert [
                record is not None for record in _side_records(xfer)
            ] == [True, True], (
                "the plant: each side must keep its payment through the revert"
            )

            assert _held_transfer_ids() == {xfer.id}

    def test_a_hidden_legs_kept_payment_is_held(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Finding BAL-532: hidden twins over the payments the transfer kept.

        Until plan step ``balance:X-bi-6-4d-2`` the occurrence delete hid the
        transfer and its legs with the payments inside, and the clause had to
        see a DEAD shadow's entry.  That state is REFUSED at commit since (the
        deleted-row rule's transfer arm; the delete takes the payments off,
        ruling **R-CC75**), so it is asserted refused (ruling **R-BAL167**
        class 2).  The subject left -- the clause has no live-twin term -- is
        the storable remainder: both twins hidden around the service while
        the live transfer keeps its two records, which the clause still
        holds (class 3: no reader reads a twin).
        """
        with app.app_context():
            xfer = _reverted_500(seed_user, seed_periods_today)
            hide_twins = db.text(
                "UPDATE budget.transactions SET is_deleted = TRUE "
                "WHERE transfer_id = :t"
            )
            db.session.execute(hide_twins, {"t": xfer.id})
            db.session.execute(
                db.text(
                    "UPDATE budget.transfers SET is_deleted = TRUE WHERE id = :t"
                ),
                {"t": xfer.id},
            )
            with refused_by_database_rule(
                "was deleted while it still holds a recorded payment",
            ) as caught:
                db.session.commit()
            db.session.rollback()
            assert f"transfer {xfer.id} " in str(caught.value)

            db.session.execute(hide_twins, {"t": xfer.id})
            db.session.commit()
            db.session.expire_all()
            legs = _legs(xfer)
            assert all(leg.is_deleted for leg in legs) and all(
                record is not None for record in _side_records(xfer)
            ), "the plant: both twins hidden, the transfer holding both payments"

            assert _held_transfer_ids() == {xfer.id}

    def test_an_entry_that_is_no_payment_is_held_too(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A $12.00 purchase planted under a TWIN is held by no clause.

        Every door refuses a purchase on a shadow
        (``Transaction.tracks_purchases``), so this $12.00 is planted around
        them.  The clause asked about ANY entry under any shadow until plan
        step ``balance:X-bi-6-4d-2``, because the movement's key refused the
        shadow's delete for any.  Since that step it asks the transfer's SIDE
        records, whose link is a payment record by
        ``ck_transaction_entries_side_link_is_a_record``, and an entry under a
        twin is read by no reader: the plant is kept and the clause does not
        hold the transfer for it (ruling **R-BAL167** class 3).  The name is
        the test's history.
        """
        with app.app_context():
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"],
                _savings(seed_user, seed_periods_today), seed_periods_today[3],
                amount=Decimal("500.00"),
            )
            db.session.commit()
            expense_leg = next(
                leg for leg in _legs(xfer)
                if leg.account_id == seed_user["account"].id
            )
            add_entry(
                db.session, seed_user, expense_leg, Decimal("12.00"),
                display_today(),
            )
            db.session.commit()
            assert not any(
                entry.covers_settlement for entry in expense_leg.entries
            ), "the plant: the one entry is a purchase, not a payment"

            assert _held_transfer_ids() == set()


class TestThePeriodLock:
    """The classifier reads a transfer's own status and the one clause."""

    def test_a_paid_transfer_alone_locks_its_period(
        self, app, db, seed_user, seed_periods_today,
    ):
        """No row of the period is settled; the $500.00 transfer is.

        On this door-written state the transfer and its legs share one
        status, so this cannot tell which of them the lock read:
        :class:`TestTheParentDecidesADrift` can.
        """
        with app.app_context():
            period = seed_periods_today[4]
            _paid_500(seed_user, seed_periods_today)
            assert db.session.query(Transaction).filter(
                Transaction.pay_period_id == period.id,
                Transaction.transfer_id.is_(None),
            ).count() == 0, "the plant: the transfer is all the period holds"

            locks = classify_schedule_locks(
                calendar_for(seed_user["user"].id), as_of=display_today(),
            )

            assert locks[period.id] is PeriodLockReason.SETTLED_TXN

    def test_a_reverted_transfer_locks_its_period_for_its_kept_payment(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Projected, so not settled; it still holds $500.00 on each leg."""
        with app.app_context():
            period = seed_periods_today[4]
            _reverted_500(seed_user, seed_periods_today)

            locks = classify_schedule_locks(
                calendar_for(seed_user["user"].id), as_of=display_today(),
            )

            assert locks[period.id] is PeriodLockReason.HOLDS_MOVEMENT


class TestTheParentDecidesADrift:
    """On the status drift Transfer Invariant 3 forbids, the TRANSFER's status is read.

    Ruling **R-JM** (a transfer's leg reads its parent), as
    ``pay_period_locks.settled_items`` states it.  No door writes either
    state -- every status change goes through ``apply_status_to_all_three``
    -- and production held neither on the 2026-09-30 00:11 dump; each is
    planted around the service, as ``test_transfer_legs``' drift class
    does, because on every door-written state the transfer and its legs
    share one status, so a lock still reading the LEGS' status passes every
    test above.  These are the states that tell the two apart.
    """

    def test_a_paid_transfer_over_projected_legs_is_settled(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Drift B: the $500.00 transfer set Paid alone; its legs say Projected.

        The legs' status read no settled item here (the period unlocked,
        a settled count of 0); the transfer's reads one.
        """
        with app.app_context():
            period = seed_periods_today[4]
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"],
                _savings(seed_user, seed_periods_today), period,
                amount=Decimal("500.00"),
            )
            db.session.commit()
            db.session.get(Transfer, xfer.id).status_id = (
                ref_cache.status_id(StatusEnum.DONE)
            )
            db.session.commit()
            db.session.expire_all()
            legs = _legs(xfer)
            assert not is_projected(db.session.get(Transfer, xfer.id)) and all(
                is_projected(leg) for leg in legs
            ) and not any(
                leg.entries for leg in legs
            ), "the plant: a Paid transfer over two Projected, empty legs"
            user_id = seed_user["user"].id

            locks = classify_schedule_locks(
                calendar_for(user_id), as_of=display_today(),
            )

            assert locks[period.id] is PeriodLockReason.SETTLED_TXN
            assert pay_period_gates.settled_transaction_count(user_id) == 1

    def test_a_projected_transfer_over_a_paid_leg_holds_by_its_movement(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Drift A: one leg set Paid alone over the $500.00 payment its side holds.

        The transfer is Projected, so it is no SETTLED item -- the legs'
        status read made it one -- and the payment its side holds still locks
        the period, as ``settled_items``' docstring says.

        The payment hung off the leg, DATED, until plan step
        ``balance:X-bi-6-4d-2``; it is the from-side's record on the transfer
        since (ruling **R-BAL88**), and a DATED record under a Projected
        transfer is refused at commit by the status-band rule, so that plant
        is asserted refused (ruling **R-BAL167** class 2).  The storable
        drift is the leg's Paid status and day planted over the side's KEPT,
        un-dated record (a revert's, ruling **R-BAL61**); the leg's status is
        read by no reader (class 3), and the three answers are unchanged.
        """
        with app.app_context():
            period = seed_periods_today[4]
            xfer = _reverted_500(seed_user, seed_periods_today)
            day = settle_day_columns(display_today())
            db.session.execute(
                db.text(
                    "UPDATE budget.transaction_entries "
                    "SET settled_on = :settled_on, "
                    "settled_day_basis_id = :settled_day_basis_id "
                    "WHERE expense_transfer_id = :t"
                ),
                {**day, "t": xfer.id},
            )
            with refused_by_database_rule(
                r"is not settled but 1 of its payment records are dated",
            ) as caught:
                db.session.commit()
            db.session.rollback()
            assert f"transfer {xfer.id} " in str(caught.value)

            leg = next(
                leg for leg in _legs(xfer)
                if leg.account_id == seed_user["account"].id
            )
            for column, value in day.items():
                setattr(leg, column, value)
            leg.status_id = ref_cache.status_id(StatusEnum.DONE)
            db.session.commit()
            db.session.expire_all()
            leg = db.session.get(Transaction, leg.id)
            assert is_projected(db.session.get(Transfer, xfer.id)) and (
                leg.status_id == ref_cache.status_id(StatusEnum.DONE)
            ) and _side_records(xfer)[0].amount == Decimal("500.00"), (
                "the plant: a Projected transfer over a Paid leg, its side "
                "holding $500.00"
            )
            user_id = seed_user["user"].id

            locks = classify_schedule_locks(
                calendar_for(user_id), as_of=display_today(),
            )

            assert locks[period.id] is PeriodLockReason.HOLDS_MOVEMENT
            assert pay_period_gates.settled_transaction_count(user_id) == 0
            assert pay_period_gates.movement_holding_row_count(user_id) == 1


class TestTheResetCounts:
    """Ruling R-BAL126: the reset refusal counts a transfer ONCE."""

    @staticmethod
    def _refusal(seed_user, seed_periods_today):
        """Press Reset and return what refused it; nothing is written."""
        with pytest.raises(PayPeriodResetBlocked) as caught:
            pay_period_admin.reset_pay_periods(
                seed_user["user"].id,
                new_start_date=seed_periods_today[0].start_date,
                num_periods=4, rhythm=rhythm_of(14),
            )
        db.session.rollback()
        return caught.value

    def test_a_paid_transfer_counts_once_beside_a_paid_row(
        self, app, db, seed_user, seed_periods_today,
    ):
        """One Received $2,000.00 paycheck + one Paid $500.00 transfer = 2.

        It read 3 until this step: the paycheck, and the transfer once per
        settled shadow.
        """
        with app.app_context():
            add_txn(
                db.session, seed_user, seed_periods_today[2], "Paycheck",
                "2000.00", status_enum=StatusEnum.RECEIVED, is_income=True,
            )
            _paid_500(seed_user, seed_periods_today)
            user_id = seed_user["user"].id

            assert pay_period_gates.settled_transaction_count(user_id) == 2
            refusal = self._refusal(seed_user, seed_periods_today)
            assert (refusal.settled_count, refusal.holding_count) == (2, 0)
            assert str(refusal).startswith(
                "Cannot reset the schedule: you have 2 settled transaction(s)."
            )

    def test_a_transfer_holding_its_payment_counts_once(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Nothing settled; one reverted transfer, its payment on both legs = 1.

        It read 2 until this step, once per holding leg.
        """
        with app.app_context():
            _reverted_500(seed_user, seed_periods_today)
            user_id = seed_user["user"].id

            assert pay_period_gates.settled_transaction_count(user_id) == 0
            assert pay_period_gates.movement_holding_row_count(user_id) == 1
            refusal = self._refusal(seed_user, seed_periods_today)
            assert (refusal.settled_count, refusal.holding_count) == (0, 1)
            assert str(refusal).startswith(
                "Cannot reset the schedule: 1 row(s) hold a recorded payment "
                "or purchase; delete it from its row first."
            )
