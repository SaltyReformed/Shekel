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
* **R-R107** (amends R-BAL139, plan step recurrence:R16-c-2) -- that
  installment is the payment's INTERVAL's, so a close due off the loan's
  contractual day is dated by the installment before it; and the walk's
  visibility bound reads the loan's own calendar to place it.
* **R-BAL140** -- the settled half is the plan half's exact complement: a
  payment is PLANNED while its transfer is Projected and its side's money has
  not moved, and SETTLED otherwise.  A status drift no door writes (a twin row
  that disagrees with its transfer) is therefore counted exactly once -- by its
  money when its money moved -- in the loan walk AND in the contribution feed,
  where the old partition counted it twice or not at all.  Since plan step
  ``balance:X-bi-6-4d-2`` a side's money is its record on the TRANSFER and no
  reader reads a twin, and the drift whose money moved -- a DATED side record
  under a Projected transfer -- is refused by the database (ruling
  **R-BAL167** class 2).  Since plan step ``balance:X-bi-6-4d-3`` the twin row
  itself is refused at the write (ruling **R-BAL258**), so the drift whose
  money did NOT move has no row to be written on.
* The posting sync's lineage probe named the transfer of a movement that hung
  off a DEAD shadow, so that movement's postings reversed; since plan step
  ``balance:X-bi-6-4d-3`` no twin can be stored to hang one off, which the
  case asserts.

Every figure is made up (ruling R-BAL132).  The loan is ``$100,000.00`` at 6%
from 2026-02-01, due on the 1st: one month's charge is
``round(100000.00 * 0.06 / 12) = 500.00``.  It originates the month before
the 03-01 installment every payment below satisfies, so no earlier
installment stands unpaid: the walk charges every contractual installment
from origination (plan step recurrence:R16-c-2), and a loan from 2026-01-01
would owe 02-01's charge too.  The origination was 2026-01-01 until that
step's leaf restated it under ruling R-R101; every figure stayed.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import LedgerAccountKindEnum, StatusEnum
from app.exceptions import UndatedSettleError
from app.extensions import db
from app.models.journal_entry import Posting
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import loan_loaders, transfer_service
from app.services.loan_ledger import _visible
from app.services.loan_ledger import (
    LoanCalendars,
    confirmed_shadows_through,
    load_loan_stream,
    payment_installments,
    payment_visible_on,
    walk_loan_ledger,
)
from app.services.loan_posting_service._linked_ledger import (
    _movement_nets_by_date,
)
from app.services.posting_service import _ledger_account_for
from app.services.recorded_contributions import (
    load_shadow_income_contributions_for_account,
)
from app.services.transfer_legs import transfer_side_leg
from app.utils.dates import display_today
from tests._test_helpers import (
    assert_a_twin_is_refused,
    basis_for,
    create_loan_account,
    create_savings_account,
    create_settled_transfer,
    create_transfer,
    figure_source_columns,
    find_loan_ledger_account,
    loan_correction_entries_at,
    loan_params_for,
    refused_by_database_rule,
    settle_day_columns,
    transfer_side_record,
)

#: The loan's origination and contractual due day.
_ORIGINATION = date(2026, 2, 1)
_PAYMENT_DAY = 1
#: The installment every payment below satisfies, and the period holding it
#: (``seed_periods[4]`` runs 2026-02-27 .. 03-12).
_DUE = date(2026, 3, 1)
_PERIOD = 4
#: The day a close or a movement is stated on, four days after the installment.
_CLOSED_ON = date(2026, 3, 5)


def _loan(seed_user):
    """A $100,000.00 loan at 6% from 2026-02-01, due on the 1st."""
    loan = create_loan_account(
        seed_user, db.session, name="Settled Leg Loan",
        principal=Decimal("100000.00"), rate=Decimal("0.06000"), term=360,
        origination_date=_ORIGINATION, payment_day=_PAYMENT_DAY,
    )
    db.session.commit()
    return loan


#: The status-band rule's two refusals (``app/side_band_infrastructure``), as
#: the database words them.
_UNSETTLED_DATED = r"is not settled but 1 of its payment records are dated"
_SETTLED_UNDATED = (
    r"is settled but its sides hold 1 dated and 1 un-dated payment record"
)


def _stage_a_dated_side_record(transfer, account, day, amount):
    """STAGE a DATED record of *amount* on *transfer*'s side on *account*, uncommitted.

    The side's money moved on *day* while the transfer stays Projected --
    ruling **R-BAL140**'s "counted once by its money" drift as it is spelled
    since plan step ``balance:X-bi-6-4d-2``, where a side's record hangs off
    the TRANSFER (ruling **R-BAL88**) and no longer off its twin.  The
    status-band rule refuses it at COMMIT, so it exists only inside this
    save; the caller commits and expects the refusal.  The to-side is the
    side on *account* in every caller here.
    """
    db.session.add(TransactionEntry(
        income_transfer_id=transfer.id,
        account_id=account.id,
        owner_id=transfer.user_id,
        user_id=transfer.user_id,
        amount=amount,
        description="Drift record",
        purchased_on=day,
        covers_settlement=True,
        **settle_day_columns(day),
        **figure_source_columns(),
    ))
    db.session.flush()


def _stage_an_undated_loan_side(transfer, account):
    """STAGE ruling R-BAL147's state, uncommitted: a Paid transfer whose loan side lost its day.

    The loan side's record -- on the TRANSFER since plan step
    ``balance:X-bi-6-4d-2`` (ruling **R-BAL88**), where it hung off the
    income twin until then -- is un-dated by bulk update, its transfer left
    Paid: the shape a revert of the twin ALONE used to leave (the seam keeps
    a reverted record and releases its day, ruling **R-BAL61**).  No door
    writes it, and since that step the status-band rule refuses it at
    COMMIT, so it exists only inside this save (ruling **R-BAL167** class
    2): the caller asks its readers there, then commits and expects the
    refusal.

    Returns:
        The loan side's record.  It returned the income twin beside it until
        plan step ``balance:X-bi-6-4d-3`` made a transfer one row (ruling
        **R-BAL258**; the helper rewritten under ruling **R-BAL167** class 4).
    """
    record = transfer_side_record(db.session, transfer.id, account.id)
    db.session.query(TransactionEntry).filter(
        TransactionEntry.id == record.id,
    ).update(
        {"settled_on": None, "settled_day_basis_id": None},
        synchronize_session=False,
    )
    db.session.flush()
    db.session.expire_all()
    return record


class TestAZeroDollarCloseIsDatedByTheInstallmentItSkips:
    """Ruling R-BAL139: no movement, so no day of its own -- the installment's."""

    def test_the_close_is_visible_from_its_installment_not_its_settle_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Closed 03-05 at $0.00 against the 03-01 installment: visible 03-01.

        The leg carries no record, so :func:`payment_visible_on` answers the
        installment it skips -- its interval's (ruling R-R107), which for a
        payment due on the contractual day is its own due date -- and never
        the settle day the close was stated on.  The
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
            assert payment_visible_on(legs[0], _ORIGINATION, _PAYMENT_DAY) == _DUE

            [outcome] = walk_loan_ledger(loan.id, scenario_id).settled_splits
            assert (outcome.due_date, outcome.visible_on) == (_DUE, _DUE)
            assert (outcome.cash, outcome.interest, outcome.principal) == (
                Decimal("0.00"), Decimal("500.00"), Decimal("-500.00"),
            )

            assert confirmed_shadows_through(
                loan.id, scenario_id, date(2026, 3, 2), _ORIGINATION, _PAYMENT_DAY,
            ) == legs
            assert confirmed_shadows_through(
                loan.id, scenario_id, date(2026, 2, 28), _ORIGINATION, _PAYMENT_DAY,
            ) == []

            [installment] = payment_installments(
                loan.id, scenario_id, loan_params_for(db.session, loan.id),
                options=(), leg_options=(),
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


#: The off-day loan: $100,000.00 at 6% from 2026-01-22, due the 22nd, so its
#: first installment is 2026-02-22 and one month's charge is 500.00.
_OFF_DAY_ORIGINATION = date(2026, 1, 22)
_OFF_DAY_PAYMENT_DAY = 22


def _off_day_loan(seed_user):
    """A $100,000.00 loan at 6% from 2026-01-22, due on the 22nd."""
    loan = create_loan_account(
        seed_user, db.session, name="Off Day Loan",
        principal=Decimal("100000.00"), rate=Decimal("0.06000"), term=360,
        origination_date=_OFF_DAY_ORIGINATION,
        payment_day=_OFF_DAY_PAYMENT_DAY,
    )
    db.session.commit()
    return loan


class TestAnOffDayZeroDollarCloseIsDatedByItsIntervalsInstallment:
    """Ruling R-R107 (amends R-BAL139): the installment it skips is its INTERVAL's.

    A payment due off the loan's contractual day belongs to the latest
    installment due on or before it -- the one its charge, its cash price and
    the plan already read (ruling R-R104) -- so a ``$0.00`` close of it is
    visible, and booked, on THAT installment's day, not on its own due date.
    """

    def test_a_close_due_mar_10_is_dated_by_the_feb_22_installment(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Due 03-10 on a loan due the 22nd, closed 03-12 at $0.00: dated 02-22.

        The latest installment on or before 03-10 is 02-22, and it is the only
        charge standing (03-22 falls after the stream's last event): 500.00 of
        interest, so cash 0.00 splits interest 500.00, principal -500.00.  The
        walk's outcome, the confirmed bound, the installment feed and the
        posted correction all read 02-22; the payment keeps 03-10 as its due
        date.  Under R-BAL139 as first built, every one of them read 03-10.
        """
        with app.app_context():
            loan = _off_day_loan(seed_user)
            due = date(2026, 3, 10)
            installment = date(2026, 2, 22)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"),
                settled_amount=Decimal("0.00"), settled_on=date(2026, 3, 12),
                due_date=due,
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id

            [leg] = loan_loaders.settled_income_shadows(
                loan.id, scenario_id, options=(),
            )
            assert leg.record is None, "a $0.00 close carries no movement"
            assert payment_visible_on(
                leg, _OFF_DAY_ORIGINATION, _OFF_DAY_PAYMENT_DAY,
            ) == installment

            [outcome] = walk_loan_ledger(loan.id, scenario_id).settled_splits
            assert (outcome.due_date, outcome.charge_date, outcome.visible_on) == (
                due, installment, installment,
            )
            assert (outcome.cash, outcome.interest, outcome.principal) == (
                Decimal("0.00"), Decimal("500.00"), Decimal("-500.00"),
            )

            assert confirmed_shadows_through(
                loan.id, scenario_id, installment,
                _OFF_DAY_ORIGINATION, _OFF_DAY_PAYMENT_DAY,
            ) == [leg]
            assert confirmed_shadows_through(
                loan.id, scenario_id, date(2026, 2, 21),
                _OFF_DAY_ORIGINATION, _OFF_DAY_PAYMENT_DAY,
            ) == []
            # The read pass's bound, which takes its origination and due day
            # off the loan's calendar: seen from 02-22, not before.
            [seen] = load_loan_stream(
                loan.id, scenario_id, LoanCalendars(), visible_by=installment,
            ).payments
            assert (seen.source, seen.visible_on) == (leg, installment)
            assert load_loan_stream(
                loan.id, scenario_id, LoanCalendars(),
                visible_by=date(2026, 2, 21),
            ).payments == []

            [fed] = payment_installments(
                loan.id, scenario_id, loan_params_for(db.session, loan.id),
                options=(), leg_options=(),
            )
            assert (fed.dates.due_date, fed.dates.settled_on) == (
                due, installment,
            )

            period_id = seed_periods[_PERIOD].id
            [entry] = loan_correction_entries_at(
                db.session, loan.id, scenario_id, period_id, installment,
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
                db.session, loan.id, scenario_id, period_id, due,
            ) == []

    def test_a_close_due_before_the_first_installment_keeps_its_due_date(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Due 02-10, before the 02-22 first installment: it skips none, dated 02-10.

        No installment falls on or before 02-10, so there is no interval to
        date it by and no charge standing against it: it keeps its own due
        date and splits nothing (0.00 / 0.00 / 0.00).
        """
        with app.app_context():
            loan = _off_day_loan(seed_user)
            due = date(2026, 2, 10)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[2], amount=Decimal("1000.00"),
                settled_amount=Decimal("0.00"), settled_on=date(2026, 2, 11),
                due_date=due,
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id

            [leg] = loan_loaders.settled_income_shadows(
                loan.id, scenario_id, options=(),
            )
            assert payment_visible_on(
                leg, _OFF_DAY_ORIGINATION, _OFF_DAY_PAYMENT_DAY,
            ) == due
            [outcome] = walk_loan_ledger(loan.id, scenario_id).settled_splits
            assert (outcome.visible_on, outcome.charge_date) == (due, None)
            assert (outcome.cash, outcome.interest, outcome.principal) == (
                Decimal("0.00"), Decimal("0.00"), Decimal("0.00"),
            )


class TestTheReadPassBoundReadsTheLoansCalendar:
    """The walk's visibility bound places a close on the loan's OWN calendar.

    :func:`~app.services.loan_ledger.load_loan_stream` hands the bound its
    calendar's origination and due day (plan step recurrence:R16-c-2, rulings
    R-R100 and R-R107).  The off-day close above grades both through the
    interval's installment; a close storing NO due date grades the due day a
    second way, as the fallback that dates it from its pay period.
    """

    def test_an_undated_close_enters_the_pass_on_its_installment(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """An ad-hoc close with no due date in the 02-27 period: seen from 03-01.

        Its due date is the first due day on or after its period's start --
        03-01 on this loan due the 1st -- which is also that interval's
        installment, so a pass visible by 03-01 holds it and one visible by
        02-28 does not.  Read on any other due day (the 17th: 03-17) the 03-01
        pass would miss it.
        """
        with app.app_context():
            loan = _loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"),
                settled_amount=Decimal("0.00"), settled_on=_CLOSED_ON,
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            [leg] = loan_loaders.settled_income_shadows(
                loan.id, scenario_id, options=(),
            )
            assert leg.due_date is None, "the close must store no due date"

            seen = load_loan_stream(
                loan.id, scenario_id, LoanCalendars(), visible_by=_DUE,
            )
            assert [event.source for event in seen.payments] == [leg]
            assert [event.visible_on for event in seen.payments] == [_DUE]
            assert load_loan_stream(
                loan.id, scenario_id, LoanCalendars(),
                visible_by=date(2026, 2, 28),
            ).payments == []


class TestTheSettledHalfComplementsThePlanHalf:
    """Ruling R-BAL140: every payment into the loan is in exactly ONE half."""

    def test_every_transfer_state_lands_in_exactly_one_half(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The states this case covers, each placed by the transfer's status and its money.

        * Projected, nothing moved                    -> planned;
        * Paid with its movements                     -> settled;
        * a $0.00 close (Paid, no movement)           -> settled;
        * Paid, then reverted to Projected through the
          door, its records kept un-dated             -> planned;
        * Cancelled                                   -> neither;
        * Cancelled over its kept un-dated records    -> neither (excluded);
        * Paid, its loan side's record staged
          un-dated                                    -> refused at commit
          (rolled back: Paid and dated, settled).

        **A soft-deleted transfer is in NEITHER half, by design**, whatever
        its status: each half filters ``Transfer.is_deleted``.  It is no
        member here, because "neither" is a different claim than "exactly
        one".  A soft delete through the door takes a Paid payment's records
        off and returns it as a plan (ruling **R-BAL246**), so the PLAN
        half's filter is what drops a deleted Projected or Paid payment,
        graded by ``test_loan_payment_service.py``'s
        ``test_excludes_deleted_transactions`` (both its cases); a ``$0.00``
        close held nothing and stays Paid (measured 2026-10-15), so the
        SETTLED half's filter is what drops that one.

        The old partition keyed the settled half on the TWIN's status, so a
        twin Paid under a Projected parent was in BOTH halves and a parent
        Paid over a still-Projected twin in neither.  Each transfer carries
        its own figure because ``uq_transfers_adhoc_dedupe`` refuses two
        identical ad-hoc transfers in one period.

        **The reverted member is the boundary of "its money moved"** (ruling
        **R-BAL140**): the revert KEEPS its records and releases their days
        (ruling **R-BAL61**), so records EXIST and none is DATED; the plan
        emits a leg for a side with no dated movement (ruling **R-BAL79**),
        and the settled half, the plan half's complement, does not hold it.
        It is a NEW member at plan step ``balance:X-bi-6-4d-3``: no member
        stood in that state before.

        **Since plan step ``balance:X-bi-6-4d-2``** each side's record hangs
        off the transfer (ruling **R-BAL88**) and no reader reads a twin
        (ruling **R-BAL167**).  Two plants are REFUSED at commit (class 2)
        and asserted so: Cancelling the Cancelled-over-records member over
        its two DATED records (the status-band rule; it was "Cancelled over a
        dated movement", and its storable neighbour -- Paid, reverted and
        Cancelled through the door, its records kept un-dated -- is what
        stands, excluded as before); and un-dating the last member's loan
        side under its Paid parent (the band again; rolled back, Paid and
        dated, settled as it was -- its walk's refusal is
        :class:`TestASettledPaymentWithAnUndatedRecordFailsLoud`'s).

        **Four members left at plan step ``balance:X-bi-6-4d-3``**, where a
        transfer became one row (ruling **R-BAL258**).  Three planted the
        loan-side twin first: Paid with nothing moved, Paid with a dated
        movement under it, and that one with its twin hidden.  The twin is
        refused at the write by ``ck_transactions_names_no_transfer``
        whatever it carries -- measured bare, dated, Paid and dated, and Paid,
        dated and hidden -- so the movement and the hide planted after it can
        never be written: ONE refused shape, pinned on the income side by
        ``test_a_transfer_shadow_is_derived.py``'s ``TestAShadowIsBornDerived``
        case ``test_the_transfer_owns_its_figure_and_neither_twin_can_be_stored``
        and, for a loan payment, by ``test_loan_plan_assembly.py``'s
        ``test_a_projected_loan_payment_is_priced_and_its_derived_twin_refused``.
        The fourth, "parent Paid, twin still Projected" (its status written
        past the seam), is without its twin the SAME stored state as the
        ``$0.00`` close: measured, every ``budget.transfers`` column is equal
        bar the literal figure and name (the close stores its PLAN figure:
        ``$0.00`` is the absence of a record, ruling **R-BAL141**), both are
        Paid over no side record, and this partition reads nothing else --
        the posting ledger included.  R-BAL140's "settled parent with no
        movement" drift is therefore no longer a state distinct from the
        close, which holds it.
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
            reverted = create_settled_transfer(
                seed_user, db.session, checking, loan, period,
                amount=Decimal("1010.00"), settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.commit()
            transfer_service.update_transfer(
                reverted.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            assert [
                transfer_side_record(
                    db.session, reverted.id, account.id,
                ).settled_on
                for account in (checking, loan)
            ] == [None, None], "the member: Projected over its two kept records"
            cancelled = projected("1006.00")
            db.session.get(Transfer, cancelled.id).status_id = (
                ref_cache.status_id(StatusEnum.CANCELLED)
            )
            db.session.commit()
            cancelled_moved = create_settled_transfer(
                seed_user, db.session, checking, loan, period,
                amount=Decimal("1008.00"), settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.commit()
            db.session.get(Transfer, cancelled_moved.id).status_id = (
                ref_cache.status_id(StatusEnum.CANCELLED)
            )
            with refused_by_database_rule(
                r"is not settled but 2 of its payment records are dated",
            ):
                db.session.commit()
            db.session.rollback()
            for status in (StatusEnum.PROJECTED, StatusEnum.CANCELLED):
                transfer_service.update_transfer(
                    cancelled_moved.id, seed_user["user"].id,
                    status_id=ref_cache.status_id(status),
                )
            db.session.commit()
            assert [
                transfer_side_record(
                    db.session, cancelled_moved.id, account.id,
                ).settled_on
                for account in (checking, loan)
            ] == [None, None], "the plant: Cancelled over its two kept records"
            paid_loan_side_undated = create_settled_transfer(
                seed_user, db.session, checking, loan, period,
                amount=Decimal("1009.00"), settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.commit()
            _stage_an_undated_loan_side(paid_loan_side_undated, loan)
            with refused_by_database_rule(_SETTLED_UNDATED):
                db.session.commit()
            db.session.rollback()

            halves = loan_loaders.income_shadows(
                loan.id, seed_user["scenario"].id, options=(), leg_options=(),
            )
            settled = sorted(leg.transfer.id for leg in halves.settled)
            planned = sorted(leg.transfer.id for leg in halves.projected)

            assert settled == sorted([
                paid.id, zero.id, paid_loan_side_undated.id,
            ])
            assert planned == sorted([plain.id, reverted.id])
            assert cancelled.id not in settled + planned
            assert cancelled_moved.id not in settled + planned

    def test_a_drifted_payment_whose_money_moved_is_counted_once_by_its_money(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """R-BAL140's case: planned at $1,000.00, $900.00 moved on the loan side, parent Projected.

        The walk counted it ONCE, as a settled payment of the $900.00 that
        moved on 03-05 -- interest 500.00, principal 400.00 -- and the
        payment feed priced it at that record, never at the $1,000.00 plan.
        Under the first design (the transfer's status alone) it was in
        neither half: the plan skips a side whose money moved (R-BAL79) and a
        Projected parent failed the status test.

        **The state is REFUSED at commit since plan step
        ``balance:X-bi-6-4d-2``** (ruling **R-BAL167** class 2): the $900.00
        hung off the income twin, which no reader reads since, and the side's
        own DATED record under a Projected transfer is what the status-band
        rule refuses.  The readers' answers on it -- the walk's (900.00 on
        03-05, interest 500.00, principal 400.00), the payment history's
        confirmed 900.00 and the leg's 900.00 contribution -- are not
        asserted: no commit can leave the state they were asked of.
        """
        with app.app_context():
            loan = _loan(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"), due_date=_DUE,
            )
            db.session.commit()
            transfer_id = transfer.id
            _stage_a_dated_side_record(
                transfer, loan, _CLOSED_ON, Decimal("900.00"),
            )

            with refused_by_database_rule(_UNSETTLED_DATED) as caught:
                db.session.commit()
            db.session.rollback()

            assert f"transfer {transfer_id} " in str(caught.value)
            assert transfer_side_record(
                db.session, transfer_id, loan.id,
            ) is None


class TestASettledPaymentWithAnUndatedRecordFailsLoud:
    """A settled transfer whose record carries no day is REFUSED, never dated by a guess."""

    def test_a_paid_transfer_over_a_reverted_twin_refuses_and_names_the_transfer(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Drift B over a kept movement: the walk refuses, naming the TRANSFER.

        The transfer says Paid, and the record its leg carries -- the kept
        covering movement -- carries no day.  Read off the transfer, that is
        the broken settled-without-a-day state the refusal exists for: a day
        guessed here would put money on a day nothing recorded, and the
        R-BAL139 installment day is for a payment that moved NOTHING, not
        for one whose movement lost its day.  The refusal names the payment as
        its owner finds it in the app -- the transfer's own name (its
        popover's title) and its two accounts, the figure, the paycheck and
        the due date -- with the
        transfer's and the movement's ids, because ruling R-BAL147's "you fix
        the row through the app" is the TRANSFER once the movement re-parents.
        It named the row the movement hangs off, the twin, whose own status is
        Projected, until leaf balance:X-bi-6-4d-1 (a re-expression under
        R-BAL167 class 1).  Until plan step balance:X-bi-6-4b the twin's
        status kept this payment out of the loan entirely.

        Two unrelated transfers are built FIRST so the transfer's and the
        movement's ids differ: in a fresh database each counts from 1, and a
        message naming the twin's id where the movement's belongs passed the
        leaf's first version of this case.  The twin's id joined the check
        until plan step ``balance:X-bi-6-4d-3`` made a transfer one row
        (ruling **R-BAL258**; ruling **R-BAL167** class 4, through the
        helper :func:`_stage_an_undated_loan_side`).

        **Staged inside the save since plan step ``balance:X-bi-6-4d-2``**
        (ruling **R-BAL167** class 2): the movement is the loan side's record
        on the transfer (ruling **R-BAL88**), and the status-band rule
        refuses a settled transfer holding an un-dated one at COMMIT, so the
        state exists only inside the save that writes it -- where the walk is
        asked, and refuses, as before -- and the commit is then asserted
        refused.
        """
        with app.app_context():
            loan = _loan(seed_user)
            decoy = create_savings_account(
                seed_user, db.session, "Decoy Savings", Decimal("0.00"),
            )
            for amount in ("10.00", "20.00"):
                create_transfer(
                    seed_user, db.session, seed_user["account"], decoy,
                    seed_periods[_PERIOD], amount=Decimal(amount),
                )
            db.session.commit()
            transfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"),
                settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.commit()
            movement = _stage_an_undated_loan_side(transfer, loan)
            assert transfer.id != movement.id, (
                "the two ids must differ for the message to grade which one "
                f"it names: transfer {transfer.id}, movement {movement.id}"
            )

            with pytest.raises(UndatedSettleError) as refused:
                walk_loan_ledger(loan.id, seed_user["scenario"].id)
            message = str(refused.value)
            assert message.startswith(
                f"The loan-side payment (movement {movement.id}) of transfer "
                f"{transfer.id} \"{transfer.name}\" (Checking to Settled Leg "
                f"Loan, $1,000.00 in the paycheck of "
                f"{seed_periods[_PERIOD].start_date}, due {_DUE}) is in a "
                "settled status but carries no settled_on"
            ), message
            assert transfer.name == "Checking to Settled Leg Loan", (
                "the create door's default name, the popover's title"
            )
            assert "\"Money moved on\" box" in message, message
            transfer_id = transfer.id

            with refused_by_database_rule(_SETTLED_UNDATED) as caught:
                db.session.commit()
            db.session.rollback()
            assert f"transfer {transfer_id} " in str(caught.value)

    def test_a_dated_payment_never_builds_the_refusals_words(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):  # pylint: disable=unused-argument
        """The walk over a payment WITH its day never composes the refusal's subject.

        The subject reads the transfer's relationships, and the walk asks
        ``payment_visible_on`` for every settled payment it folds, so it is
        built only when the refusal fires
        (:func:`~app.utils.balance_predicates.require_settled_day`).  The
        builder is replaced by one that fails the case if called.
        """
        def never(_leg):
            raise AssertionError("the refusal's subject was built on a dated payment")

        monkeypatch.setattr(_visible, "_undated_payment_subject", never)
        with app.app_context():
            loan = _loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"),
                settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.commit()

            [outcome] = walk_loan_ledger(
                loan.id, seed_user["scenario"].id,
            ).settled_splits
            assert outcome.visible_on == _CLOSED_ON


class TestTheRefusalsRepairWorksThroughTheApp:
    """The loan reads the days typed into the transfer's popover, through its own door.

    ``_visible._UNDATED_PAYMENT_CAUSE`` tells the reader of the log to type
    each account's day into that account's "Money moved on" box, and that a
    revert then Mark Paid would date the payment to the day of the click.
    What those doors do to a loan payment is graded here: the PATCH posts the
    popover's two day boxes as that door's own route tests do, and the loan
    walk reads the day each box states.  ``_CLOSED_ON`` (03-05) is the day
    the transfer was marked paid; the suite's frozen clock is the day of the
    click.  The class name is its history.

    **Each case starts from the ordinary Paid payment** since plan step
    ``balance:X-bi-6-4d-2``.  It started from the state the refusal
    describes -- Paid, its loan side's record un-dated -- and asserted the
    walk refused it first; the status-band rule refuses that state at
    commit, and a request reads only what is committed, so no request can
    meet it (developer answer 2026-10-09, "Rewrite from a Paid start": the
    broken-state setup and its error check dropped, every checked day
    unchanged).  The refusal's message is graded inside the save that stages
    the state, in :class:`TestASettledPaymentWithAnUndatedRecordFailsLoud`.

    **What Checking's day does when only the loan's box is typed is not the
    message's claim and is not asserted**: today Checking's guessed day
    follows the loan's (ruling R-BAL142), and ruling R-R116 (plan step X-cu)
    reverses that for a loan.  The message asks for EACH account's day, which
    is right under both, and :meth:`test_each_box_typed_dates_its_own_side`
    holds it.
    """

    @staticmethod
    def _paid_payment(seed_user, seed_periods):
        """The $1,000.00 loan payment, Paid on 03-05 through the door."""
        loan = _loan(seed_user)
        transfer = create_settled_transfer(
            seed_user, db.session, seed_user["account"], loan,
            seed_periods[_PERIOD], amount=Decimal("1000.00"),
            settled_on=_CLOSED_ON, due_date=_DUE,
        )
        db.session.commit()
        return loan, transfer.id

    @staticmethod
    def _days(loan, transfer_id, scenario_id):
        """Return (the loan walk's day for the payment, the source side's day)."""
        db.session.expire_all()
        [outcome] = walk_loan_ledger(loan.id, scenario_id).settled_splits
        source = transfer_side_leg(
            db.session.get(Transfer, transfer_id), is_income=False,
        )
        return outcome.visible_on, source.settled_on

    def test_a_day_typed_into_the_loan_box_dates_the_payment(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Only the loan's box typed, 03-09: the walk dates the payment 03-09."""
        with app.app_context():
            loan, transfer_id = self._paid_payment(seed_user, seed_periods)
            typed = date(2026, 3, 9)

            response = auth_client.patch(
                f"/transfers/instance/{transfer_id}",
                data={"settled_on_from": "", "settled_on_to": typed.isoformat()},
            )
            assert response.status_code == 200, response.get_data(
                as_text=True,
            )[:300]

            visible_on, _ = self._days(
                loan, transfer_id, seed_user["scenario"].id,
            )
            assert visible_on == typed

    def test_each_box_typed_dates_its_own_side(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Checking's box 03-05, the loan's 03-09: each side keeps the day typed into its box."""
        with app.app_context():
            loan, transfer_id = self._paid_payment(seed_user, seed_periods)
            typed = date(2026, 3, 9)

            response = auth_client.patch(
                f"/transfers/instance/{transfer_id}",
                data={
                    "settled_on_from": _CLOSED_ON.isoformat(),
                    "settled_on_to": typed.isoformat(),
                },
            )
            assert response.status_code == 200, response.get_data(
                as_text=True,
            )[:300]

            assert self._days(
                loan, transfer_id, seed_user["scenario"].id,
            ) == (typed, _CLOSED_ON)

    def test_a_revert_then_mark_paid_dates_it_to_the_day_of_the_click(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Reverted, then Mark Paid: the walk and both sides read the click's day."""
        with app.app_context():
            loan, transfer_id = self._paid_payment(seed_user, seed_periods)

            reverted = auth_client.patch(
                f"/transfers/instance/{transfer_id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.PROJECTED)),
                },
            )
            assert reverted.status_code == 200, reverted.get_data(
                as_text=True,
            )[:300]
            paid = auth_client.post(
                f"/transfers/instance/{transfer_id}/mark-done",
            )
            assert paid.status_code == 200, paid.get_data(as_text=True)[:300]

            clicked = display_today()
            assert clicked != _CLOSED_ON, "the click must fall on another day"
            assert self._days(
                loan, transfer_id, seed_user["scenario"].id,
            ) == (clicked, clicked)


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
        """$250.00 planned, twin Paid, nothing moved: the twin is refused, ONE planned $250.00.

        The feed read it as ONE planned $250.00: the parent decides, and no
        money moved.  **The twin is REFUSED at the write since plan step
        ``balance:X-bi-6-4d-3``** (ruling **R-BAL167** class 2, ruling
        **R-BAL258**): ``ck_transactions_names_no_transfer`` refuses the
        income twin with or without the Paid status and the 03-05 day the
        drift wrote onto it (measured both ways), so it is staged as
        :func:`~tests._test_helpers.assert_a_twin_is_refused` builds it and
        that refusal asserted.  The feed's answer on the transfer that
        stands is asserted as before, one planned $250.00, so the refused
        plant leaves nothing a reader could count twice.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Drift Savings", Decimal("0.00"),
            )
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[_PERIOD], amount=Decimal("250.00"),
            )
            db.session.commit()
            assert_a_twin_is_refused(
                db.session, db.session.get(Transfer, transfer.id), is_income=True,
            )

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
        """$250.00 planned, $240.00 moved, parent Projected: refused at commit.

        The feed read it as ONE confirmed $240.00.  **The state is REFUSED at
        commit since plan step ``balance:X-bi-6-4d-2``** (ruling **R-BAL167**
        class 2): the $240.00 hung off the income twin, which no reader reads
        since, and the side's own DATED record under a Projected transfer
        (ruling **R-BAL88**) is what the status-band rule refuses.  The
        feed's answer on it, ``[($240.00, confirmed)]``, is not asserted: no
        commit can leave the state it was asked of.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Drift Savings", Decimal("0.00"),
            )
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[_PERIOD], amount=Decimal("250.00"),
            )
            db.session.commit()
            transfer_id = transfer.id
            _stage_a_dated_side_record(
                transfer, savings, _CLOSED_ON, Decimal("240.00"),
            )

            with refused_by_database_rule(_UNSETTLED_DATED) as caught:
                db.session.commit()
            db.session.rollback()

            assert f"transfer {transfer_id} " in str(caught.value)
            assert transfer_side_record(
                db.session, transfer_id, savings.id,
            ) is None


class TestTheLineageProbeNamesADeadShadowsMovement:
    """The posting sync resolves a stale movement's transfer through ``movement_parent``."""

    def test_a_movement_under_a_dead_shadow_is_reversed(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Paid $1,000.00, then its twin soft-deleted around the service.

        The movement was no leg's record any more (a dead shadow's), so the
        walk expected no cash for it and the posted cash leg was STALE; the
        probe had to still name its transfer so the transfer's pair door
        reversed it.

        **The state is REFUSED since plan step ``balance:X-bi-6-4d-2``**
        (ruling **R-BAL167** class 2): the loan side's payment is its record
        on the TRANSFER (ruling **R-BAL88**), read here off the side link
        (class 1).  Until plan step ``balance:X-bi-6-4d-3`` a movement sat
        under a twin only where it was planted -- both records re-parented
        under their twins in one statement -- and a twin hidden holding one
        was refused by the deleted-row rule's row arm.  **Since that step the
        twin itself cannot be stored** (ruling **R-BAL258**): the loan-side
        twin the plant re-parented the movement under is refused at the write
        by ``ck_transactions_names_no_transfer``, so neither the re-parenting
        nor the hide has a row to act on, and the sync's answer on a stale
        movement under a dead shadow -- the posted cash leg reversed,
        ``_movement_nets_by_date`` reading ``{}`` -- is not asserted.  What
        stands is the movement as the side's record, posted on 03-05, read
        BEFORE the plant; the read after it graded that the refused hide's
        rollback restored the re-parented record, and went with the
        re-parenting.
        """
        with app.app_context():
            loan = _loan(seed_user)
            transfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"),
                settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.commit()
            transfer_id = transfer.id
            scenario_id = seed_user["scenario"].id
            linked_id = _ledger_account_for(loan.id).id
            movement_id = transfer_side_record(
                db.session, transfer_id, loan.id,
            ).id
            assert _movement_nets_by_date(linked_id, scenario_id) == {
                movement_id: {_CLOSED_ON: Decimal("1000.00")},
            }

            assert_a_twin_is_refused(
                db.session, db.session.get(Transfer, transfer_id), is_income=True,
            )
