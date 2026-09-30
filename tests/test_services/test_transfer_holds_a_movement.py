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
    cover_bare_settled_row,
    create_account_of_type,
    create_settled_transfer,
    create_transfer,
    rhythm_of,
    settle_day_columns,
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
    """The $500.00 transfer Paid, then set back: each leg KEEPS its payment.

    Ruling **R-BAL61**: a revert keeps the covering movement, un-dated, so a
    bank match on it still stands.  The transfer is Projected and holds two
    entries, one per leg.
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


def _held_transfer_ids():
    """The ids of the transfers the one clause says hold a movement."""
    return {
        transfer_id for (transfer_id,) in db.session.query(Transfer.id).filter(
            transfer_holds_a_movement(),
        )
    }


class TestTheOneClause:
    """``transfer_holds_a_movement``: any entry under any shadow, live or dead."""

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
        """Projected again, and still holding two $500.00 payments."""
        with app.app_context():
            xfer = _reverted_500(seed_user, seed_periods_today)
            assert [len(leg.entries) for leg in _legs(xfer)] == [1, 1], (
                "the plant: each leg must keep its payment through the revert"
            )

            assert _held_transfer_ids() == {xfer.id}

    def test_a_hidden_legs_kept_payment_is_held(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Finding BAL-532: the occurrence delete hides the legs, payments inside.

        A period delete or a permanent delete would still take those legs,
        and the movement's key refuses it, so the clause must see a DEAD
        shadow's entry -- which a leg's RECORD (``_leg_is_record``) does not.
        """
        with app.app_context():
            xfer = _reverted_500(seed_user, seed_periods_today)
            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            legs = _legs(xfer)
            assert all(leg.is_deleted for leg in legs) and all(
                leg.entries for leg in legs
            ), "the plant: both legs hidden, both still holding the payment"

            assert _held_transfer_ids() == {xfer.id}

    def test_an_entry_that_is_no_payment_is_held_too(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The key refuses for ANY entry, so the clause asks about any entry.

        Every door refuses a purchase on a shadow
        (``Transaction.tracks_purchases``), so this $12.00 is planted around
        them: the question must not rest on which entries doors write.
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

            assert _held_transfer_ids() == {xfer.id}


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
            legs = _legs(xfer)
            assert all(is_projected(leg) for leg in legs) and not any(
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
        """Drift A: one leg set Paid alone, its $500.00 payment recorded under it.

        The transfer is Projected, so it is no SETTLED item -- the legs'
        status read made it one -- and the payment its leg holds still locks
        the period, as ``settled_items``' docstring says.
        """
        with app.app_context():
            period = seed_periods_today[4]
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"],
                _savings(seed_user, seed_periods_today), period,
                amount=Decimal("500.00"),
            )
            db.session.commit()
            leg = next(
                leg for leg in _legs(xfer)
                if leg.account_id == seed_user["account"].id
            )
            for column, value in settle_day_columns(display_today()).items():
                setattr(leg, column, value)
            leg.status_id = ref_cache.status_id(StatusEnum.DONE)
            db.session.flush()
            cover_bare_settled_row(db.session, leg, Decimal("500.00"))
            db.session.commit()
            assert is_projected(db.session.get(Transfer, xfer.id)) and (
                leg.entries
            ), "the plant: a Projected transfer over a Paid leg holding $500.00"
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
