"""A loan's SETTLED payments are legs of their transfers: plan step balance:X-bi-6-4b.

The loan family and the contribution readers stopped reading the transfer's
loan-side income SHADOW row at this step and read the to-side LEG of the
transfer instead, through the one join that attaches its covering movement as
its record.  Each case below is a state the byte-identical grade on the
2026-09-23 21:17 production dump could not reach, because production holds
none of them:

* **R-BAL139** -- a ``$0.00`` close moves no money, so its leg carries no
  record and no day of its own; it is dated by the installment it skips, and
  the ledger books the unpaid charge on that day.
* **R-BAL140** -- the settled half is the plan half's exact complement: a
  payment is PLANNED while its transfer is Projected and its side's money has
  not moved, and SETTLED otherwise.  A status drift no door writes (a twin row
  that disagrees with its transfer) is therefore counted exactly once -- by its
  money when its money moved -- in the loan walk AND in the contribution feed,
  where the old partition counted it twice or not at all.
* The posting sync's lineage probe still names the transfer of a movement that
  hangs off a DEAD shadow, so that movement's postings reverse.

Every figure is made up (ruling R-BAL132).  The loan is ``$100,000.00`` at 6%
from 2026-01-01, due on the 1st: one month's charge is
``round(100000.00 * 0.06 / 12) = 500.00``.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import LedgerAccountKindEnum, StatusEnum, TxnTypeEnum
from app.exceptions import UndatedSettleError
from app.extensions import db
from app.models.journal_entry import Posting
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import loan_loaders
from app.services.loan_ledger import (
    confirmed_shadows_through,
    payment_installments,
    payment_visible_on,
    walk_loan_ledger,
)
from app.services.loan_payment_service import get_payment_history
from app.services.loan_posting_service import sync_loan_postings
from app.services.loan_posting_service._linked_ledger import (
    _movement_nets_by_date,
)
from app.services.posting_service import _ledger_account_for
from app.services.recorded_contributions import (
    load_shadow_income_contributions_for_account,
)
from app.services.row_valuation import leg_settled_contribution
from app.services.transfer_legs import grid_transfer_leg
from tests._test_helpers import (
    basis_for,
    cover_bare_settled_row,
    create_loan_account,
    create_savings_account,
    create_settled_transfer,
    create_transfer,
    find_loan_ledger_account,
    loan_correction_entries_at,
    settle_day_columns,
)

#: The loan's contractual due day.
_PAYMENT_DAY = 1
#: The installment every payment below satisfies, and the period holding it
#: (``seed_periods[4]`` runs 2026-02-27 .. 03-12).
_DUE = date(2026, 3, 1)
_PERIOD = 4
#: The day a close or a movement is stated on, four days after the installment.
_CLOSED_ON = date(2026, 3, 5)


def _loan(seed_user):
    """A $100,000.00 loan at 6% from 2026-01-01, due on the 1st."""
    loan = create_loan_account(
        seed_user, db.session, name="Settled Leg Loan",
        principal=Decimal("100000.00"), rate=Decimal("0.06000"), term=360,
        origination_date=date(2026, 1, 1), payment_day=_PAYMENT_DAY,
    )
    db.session.commit()
    return loan


def _income_shadow(transfer, account):
    """Return *transfer*'s live income shadow on *account*."""
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.transfer_id == transfer.id,
            Transaction.account_id == account.id,
            Transaction.transaction_type_id
            == ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            Transaction.is_deleted.is_(False),
        )
        .one()
    )


def _drift_the_income_shadow(transfer, account, day, moved=None):
    """Settle ONE shadow around the service, its parent left Projected.

    A STATUS DRIFT: Transfer Invariants 3 and 4 forbid it and no door writes
    it (every status change goes through the status seam), and it is written
    here directly because the class has occurred (``transfer_service._restore``
    carries a corrector for it).  With *moved* the shadow also gets a DATED
    covering movement of that figure -- its money moved -- through the one
    writer a bare-built settled row's record goes through.
    """
    shadow = _income_shadow(transfer, account)
    for column, value in settle_day_columns(day).items():
        setattr(shadow, column, value)
    shadow.status_id = ref_cache.status_id(StatusEnum.DONE)
    db.session.flush()
    if moved is not None:
        cover_bare_settled_row(db.session, shadow, moved)
    db.session.commit()
    db.session.expire_all()


def _revert_the_income_twin_keeping_an_undated_movement(transfer, account):
    """Leave *transfer* Paid while its income twin is Projected over a kept movement.

    DRIFT B with a movement: the shape a revert of the twin ALONE would leave
    -- the seam keeps a reverted row's covering movement and releases its day
    (ruling R-BAL61) -- under a parent still Paid.  No door writes it: every
    transaction door refuses a shadow, and a transfer's revert moves all three
    rows.  Written by bulk update, as the refusal test in
    ``test_loan_payment_service`` writes its broken day.
    """
    shadow = _income_shadow(transfer, account)
    db.session.query(TransactionEntry).filter(
        TransactionEntry.transaction_id == shadow.id,
    ).update(
        {"settled_on": None, "settled_day_basis_id": None},
        synchronize_session=False,
    )
    shadow.settled_on = None
    shadow.settled_day_basis_id = None
    shadow.status_id = ref_cache.status_id(StatusEnum.PROJECTED)
    db.session.commit()
    db.session.expire_all()
    return shadow


class TestAZeroDollarCloseIsDatedByTheInstallmentItSkips:
    """Ruling R-BAL139: no movement, so no day of its own -- the installment's."""

    def test_the_close_is_visible_from_its_installment_not_its_settle_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Closed 03-05 at $0.00 against the 03-01 installment: visible 03-01.

        The leg carries no record, so :func:`payment_visible_on` answers the
        installment through ``loan_payment_due_date`` -- the day the split
        keys on -- and never the settle day the close was stated on.  The
        walk's outcome, the confirmed bound and the installment feed all read
        that ONE day: a pass on 03-02 has seen the close, a pass on 02-28 has
        not.  The payment moved nothing, so the whole charge stands unpaid:
        cash 0.00, interest 500.00, principal -500.00.
        """
        with app.app_context():
            loan = _loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"),
                settled_amount=Decimal("0.00"), settled_on=_CLOSED_ON,
                due_date=_DUE,
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id

            legs = loan_loaders.settled_income_shadows(
                loan.id, scenario_id, options=(),
            )
            assert len(legs) == 1
            assert legs[0].record is None, "a $0.00 close carries no movement"
            assert payment_visible_on(legs[0], _PAYMENT_DAY) == _DUE

            [outcome] = walk_loan_ledger(loan.id, scenario_id).settled_splits
            assert (outcome.due_date, outcome.visible_on) == (_DUE, _DUE)
            assert (outcome.cash, outcome.interest, outcome.principal) == (
                Decimal("0.00"), Decimal("500.00"), Decimal("-500.00"),
            )

            assert confirmed_shadows_through(
                loan.id, scenario_id, date(2026, 3, 2), _PAYMENT_DAY,
            ) == legs
            assert confirmed_shadows_through(
                loan.id, scenario_id, date(2026, 2, 28), _PAYMENT_DAY,
            ) == []

            [installment] = payment_installments(
                loan.id, scenario_id, _PAYMENT_DAY, options=(), leg_options=(),
            )
            assert installment.dates.settled_on == _DUE

    def test_the_ledger_books_the_unpaid_charge_on_the_installment(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The split correction is keyed (loan_payment, the period, 03-01).

        R-BAL139's second half: "the ledger books the correction on that
        day".  One correction at the installment's key and none at the
        settle day's, so the posted balance grows by the unpaid charge on
        the day the loan's own split says it did: the loan's linked row
        -500.00 (principal) against the interest row +500.00.
        """
        with app.app_context():
            loan = _loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"),
                settled_amount=Decimal("0.00"), settled_on=_CLOSED_ON,
                due_date=_DUE,
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            period_id = seed_periods[_PERIOD].id

            [entry] = loan_correction_entries_at(
                db.session, loan.id, scenario_id, period_id, _DUE,
            )
            interest_row = find_loan_ledger_account(
                db.session, loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            assert dict(
                db.session.query(Posting.ledger_account_id, Posting.amount)
                .filter(Posting.journal_entry_id == entry.id)
            ) == {
                _ledger_account_for(loan.id).id: Decimal("-500.00"),
                interest_row.id: Decimal("500.00"),
            }
            assert loan_correction_entries_at(
                db.session, loan.id, scenario_id, period_id, _CLOSED_ON,
            ) == []


class TestTheSettledHalfComplementsThePlanHalf:
    """Ruling R-BAL140: every payment into the loan is in exactly ONE half."""

    def test_every_transfer_state_lands_in_exactly_one_half(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Ten states, each placed by the transfer's status and its money.

        * Projected, nothing moved                -> planned;
        * Paid with its movement                  -> settled;
        * a $0.00 close (Paid, no movement)       -> settled;
        * twin Paid, no movement, parent Projected -> planned (the parent
          decides and no money moved);
        * twin Paid WITH a dated movement, parent Projected -> settled, by
          its money (R-BAL140);
        * parent Paid, twin still Projected       -> settled, a $0.00 close
          (the parent decides);
        * Cancelled                               -> neither;
        * Projected, its only dated movement under a DEAD twin -> planned (a
          dead twin's movement is no leg's record, in both halves alike);
        * Cancelled over a dated movement          -> neither (excluded);
        * Paid, twin reverted over a kept UN-dated movement -> settled (the
          parent decides; its walk then refuses the missing day -- see
          :class:`TestASettledPaymentWithAnUndatedRecordFailsLoud`).

        The old partition keyed the settled half on the TWIN's status, so the
        fourth state was in BOTH halves and the sixth in neither.  Each
        transfer carries its own figure because ``uq_transfers_adhoc_dedupe``
        refuses two identical ad-hoc transfers in one period.
        """
        with app.app_context():
            loan = _loan(seed_user)
            checking = seed_user["account"]
            period = seed_periods[_PERIOD]

            def projected(amount):
                transfer = create_transfer(
                    seed_user, db.session, checking, loan, period,
                    amount=Decimal(amount), due_date=_DUE,
                )
                db.session.commit()
                return transfer

            plain = projected("1000.00")
            paid = create_settled_transfer(
                seed_user, db.session, checking, loan, period,
                amount=Decimal("1001.00"), settled_on=_CLOSED_ON, due_date=_DUE,
            )
            zero = create_settled_transfer(
                seed_user, db.session, checking, loan, period,
                amount=Decimal("1002.00"), settled_amount=Decimal("0.00"),
                settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.commit()
            twin_paid_nothing_moved = projected("1003.00")
            _drift_the_income_shadow(twin_paid_nothing_moved, loan, _CLOSED_ON)
            twin_paid_money_moved = projected("1004.00")
            _drift_the_income_shadow(
                twin_paid_money_moved, loan, _CLOSED_ON, Decimal("1004.00"),
            )
            parent_paid_alone = projected("1005.00")
            db.session.get(Transfer, parent_paid_alone.id).status_id = (
                ref_cache.status_id(StatusEnum.DONE)
            )
            cancelled = projected("1006.00")
            db.session.get(Transfer, cancelled.id).status_id = (
                ref_cache.status_id(StatusEnum.CANCELLED)
            )
            db.session.commit()
            dead_twin_moved = projected("1007.00")
            _drift_the_income_shadow(
                dead_twin_moved, loan, _CLOSED_ON, Decimal("1007.00"),
            )
            _income_shadow(dead_twin_moved, loan).is_deleted = True
            db.session.commit()
            cancelled_moved = create_settled_transfer(
                seed_user, db.session, checking, loan, period,
                amount=Decimal("1008.00"), settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.get(Transfer, cancelled_moved.id).status_id = (
                ref_cache.status_id(StatusEnum.CANCELLED)
            )
            db.session.commit()
            paid_twin_reverted = create_settled_transfer(
                seed_user, db.session, checking, loan, period,
                amount=Decimal("1009.00"), settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.commit()
            _revert_the_income_twin_keeping_an_undated_movement(
                paid_twin_reverted, loan,
            )

            halves = loan_loaders.income_shadows(
                loan.id, seed_user["scenario"].id, options=(), leg_options=(),
            )
            settled = sorted(leg.transfer.id for leg in halves.settled)
            planned = sorted(leg.transfer.id for leg in halves.projected)

            assert settled == sorted([
                paid.id, zero.id, twin_paid_money_moved.id, parent_paid_alone.id,
                paid_twin_reverted.id,
            ])
            assert planned == sorted([
                plain.id, twin_paid_nothing_moved.id, dead_twin_moved.id,
            ])
            assert cancelled.id not in settled + planned
            assert cancelled_moved.id not in settled + planned

    def test_a_drifted_payment_whose_money_moved_is_counted_once_by_its_money(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """R-BAL140's case: planned at $1,000.00, $900.00 moved, twin Paid, parent Projected.

        The walk counts it ONCE, as a settled payment of the $900.00 that
        moved on 03-05 -- interest 500.00, principal 400.00 -- and the
        payment feed prices it at that record, never at the $1,000.00 plan.
        Under the first design (the transfer's status alone) it was in
        neither half: the plan skips a side whose money moved (R-BAL79) and a
        Projected parent failed the status test.
        """
        with app.app_context():
            loan = _loan(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"), due_date=_DUE,
            )
            db.session.commit()
            _drift_the_income_shadow(
                transfer, loan, _CLOSED_ON, Decimal("900.00"),
            )
            scenario_id = seed_user["scenario"].id

            [outcome] = walk_loan_ledger(loan.id, scenario_id).settled_splits
            assert (outcome.cash, outcome.visible_on) == (
                Decimal("900.00"), _CLOSED_ON,
            )
            assert (outcome.interest, outcome.principal) == (
                Decimal("500.00"), Decimal("400.00"),
            )

            [record] = get_payment_history(
                loan.id, basis_for(loan, seed_user["scenario"]), _PAYMENT_DAY,
            )
            assert record.dates.is_confirmed
            assert record.amount == Decimal("900.00")

            leg = grid_transfer_leg(db.session.get(Transfer, transfer.id), loan.id)
            assert leg_settled_contribution(leg) == Decimal("900.00")


class TestASettledPaymentWithAnUndatedRecordFailsLoud:
    """A settled transfer whose record carries no day is REFUSED, never dated by a guess."""

    def test_a_paid_transfer_over_a_reverted_twin_refuses_and_names_the_row(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Drift B over a kept movement: the walk refuses, naming the twin's row.

        The transfer says Paid, and the record its leg carries -- the kept
        covering movement -- carries no day.  Read off the transfer, that is
        the broken settled-without-a-day state the refusal exists for: a day
        guessed here would put money on a day nothing recorded, and the
        R-BAL139 installment day is for a payment that moved NOTHING, not
        for one whose movement lost its day.  The refusal names the row the
        movement hangs off -- here the twin, whose own status is Projected,
        which the message's "in a settled status" does not describe; the
        TRANSFER is the settled row.  Until plan step balance:X-bi-6-4b the
        twin's status kept this payment out of the loan entirely.
        """
        with app.app_context():
            loan = _loan(seed_user)
            transfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"),
                settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.commit()
            shadow = _revert_the_income_twin_keeping_an_undated_movement(
                transfer, loan,
            )

            with pytest.raises(UndatedSettleError, match=f"Transaction {shadow.id} "):
                walk_loan_ledger(loan.id, seed_user["scenario"].id)


class TestTheContributionFeedCountsADriftOnce:
    """The contribution feed reads the SAME partition (ruling R-BAL140).

    It ran its own until plan step balance:X-bi-6-4b -- settled TWIN rows
    beside every still-Projected transfer -- so a twin settled under a
    Projected parent was one contribution counted twice: a confirmed record
    AND a planned one.
    """

    def test_a_twin_settled_with_nothing_moved_is_one_planned_contribution(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """$250.00 planned, twin Paid, nothing moved: ONE planned $250.00."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Drift Savings", Decimal("0.00"),
            )
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[_PERIOD], amount=Decimal("250.00"),
            )
            db.session.commit()
            _drift_the_income_shadow(transfer, savings, _CLOSED_ON)

            feed = load_shadow_income_contributions_for_account(
                basis_for(savings, seed_user["scenario"]), savings.id,
                [period.id for period in seed_periods],
            )
            assert [
                (record.amount, record.is_confirmed) for record in feed.records
            ] == [(Decimal("250.00"), False)]

    def test_a_twin_settled_with_its_money_moved_is_one_confirmed_contribution(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """$250.00 planned, $240.00 moved, parent Projected: ONE confirmed $240.00."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Drift Savings", Decimal("0.00"),
            )
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[_PERIOD], amount=Decimal("250.00"),
            )
            db.session.commit()
            _drift_the_income_shadow(
                transfer, savings, _CLOSED_ON, Decimal("240.00"),
            )

            feed = load_shadow_income_contributions_for_account(
                basis_for(savings, seed_user["scenario"]), savings.id,
                [period.id for period in seed_periods],
            )
            assert [
                (record.amount, record.is_confirmed) for record in feed.records
            ] == [(Decimal("240.00"), True)]


class TestTheLineageProbeNamesADeadShadowsMovement:
    """The posting sync resolves a stale movement's transfer through ``movement_parent``."""

    def test_a_movement_under_a_dead_shadow_is_reversed(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Paid $1,000.00, then its twin soft-deleted around the service.

        The movement is no leg's record any more (a dead shadow's), so the
        walk expects no cash for it and the posted cash leg is STALE; the
        probe must still name its transfer so the transfer's pair door
        reverses it.  ``transfer_legs.transfer_movement_rows`` drops a movement
        under a dead shadow -- the loader this probe must NOT use.  The
        payment itself stays a settled $0.00 close (its transfer is live and
        Paid, and no live record holds its money), so the sync's own
        checked-projection assert holds on what is left.
        """
        with app.app_context():
            loan = _loan(seed_user)
            transfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"),
                settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            linked_id = _ledger_account_for(loan.id).id
            shadow = _income_shadow(transfer, loan)
            [movement] = shadow.covering_movements
            assert _movement_nets_by_date(linked_id, scenario_id) == {
                movement.id: {_CLOSED_ON: Decimal("1000.00")},
            }

            shadow.is_deleted = True
            db.session.commit()
            db.session.expire_all()
            sync_loan_postings(loan.id, scenario_id)
            db.session.commit()

            assert _movement_nets_by_date(linked_id, scenario_id) == {}
