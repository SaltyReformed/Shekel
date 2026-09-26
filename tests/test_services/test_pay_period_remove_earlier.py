"""Tests for "Remove earlier paychecks" (plan step ``pay_calendar:C21``).

The undo of "Add earlier paychecks" (ruling **R-PC108**).  Ruling **R-PC111**:
the owner names the paycheck to START FROM, by id, and every paycheck before
it goes.  Ruling **R-PC109**: a paycheck may go only when it holds no money --
an unpaid row its template made and nobody changed goes with it, anything else
the owner entered, changed or paid refuses the removal and is named, as does
any entry the posted ledger booked in it, a pay stub on its payday, and money
DATED inside it anywhere in the budget.  Ruling **R-PC114** (amending
R-PC109's booked-entry clause): what the posted ledger booked in a removed
paycheck is re-filed through Reset's two re-syncs, and the removal is refused
only if a posted total still moves.  Ruling **R-PC110**: at least one
paycheck of the earliest pay rhythm always stays, and that rhythm's start
moves UP to the new first paycheck (the reverse of R-PC105's move down).

Eight contracts, one class each:

* the PRODUCER (``pay_calendar.opening_rephase``) -- pure: the exact inverse
  of ``earlier_paydays``' re-phase over every cadence kind and convention,
  and ``None`` where a later era pays the opening;
* the DOOR (``pay_period_admin.remove_earlier_pay_periods``) -- what it
  removes, where it moves the phase, and that the paycheck it starts from
  stays;
* an ADD UNDONE -- add N with C18-b's door, re-sync the ledger, remove them
  with this one, template rows and a recurring transfer included: paydays,
  eras, rows and posted totals come back (the test names what it compares);
* WHAT A PAYCHECK MAY HOLD (R-PC109's filed half), one case per kind;
* the LEDGER RE-FILED (R-PC114) -- an account's and a loan's opening filed
  in an added paycheck go back, a self-cancelling pair goes, and an entry no
  re-sync rebuilds refuses in the ruled words;
* MONEY DATED inside the removed span (R-PC109's dated half), one case per
  arm, and the case the span excludes;
* the EARLIEST RHYTHM KEEPS A PAYCHECK (R-PC110);
* the refusals that name no paycheck -- an id that is not the owner's -- and
  a stated history, which a removal cannot put above the record.

``POST /pay-periods/remove-earlier`` is graded in
``tests/test_routes/test_pay_periods_remove_earlier.py``.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app import ref_cache
from app.enums import (
    BusinessDayShiftEnum,
    PostingSourceEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.exceptions import PayPeriodUnresolved, ValidationError
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry
from app.models.ledger_account import LedgerAccount
from app.models.pay_era import PayEra
from app.models.pay_stub import PayStub
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import (
    account_posting_service,
    anchor_service,
    loan_posting_service,
    pay_period_admin,
    pay_period_locks,
    pay_period_write,
    pay_schedule_service,
    posting_service,
    status_seam,
    transfer_service,
)
from app.services.pay_calendar import (
    earlier_paydays,
    first_payday_of,
    nominal_payday,
    opening_rephase,
    planned_paydays_after,
    projected_payday,
)
from app.services.pay_rhythm import Era, FixedDays, Monthly, Rhythm, SemiMonthly
from app.models.amount_ownership import AmountOwnership
from tests._test_helpers import (
    add_entry,
    add_txn,
    all_periods,
    assert_pay_period_invariants,
    create_loan_account,
    create_loan_with_trueup,
    create_savings_account,
    make_balanced_entry,
    make_cadence_rule,
    make_salary_profile,
    populate_in_a_fresh_pass,
    reassert_balance_on,
    restate_account_opening,
    rhythm_of,
    settle_cash_row,
    state_template_price,
)
from tests.oracles.recurrence_baseline import MONTHLY

NONE = BusinessDayShiftEnum.NONE
PRIOR = BusinessDayShiftEnum.PRIOR
NEXT = BusinessDayShiftEnum.NEXT
EASTERN = ZoneInfo("America/New_York")

#: One era per cadence kind and convention -- the set C18-b's producer is
#: graded over, for the inverse's sake: 2030-12-12 on a fortnight steps back
#: over Thanksgiving under both displacing conventions, and the month cases
#: step back into a February that clamps the day.
ERA_CASES = {
    "every 14 days, none": Era(date(2026, 1, 2), Rhythm(FixedDays(14), NONE)),
    "every 14 days, prior, over Thanksgiving": Era(
        date(2030, 12, 12), Rhythm(FixedDays(14), PRIOR),
    ),
    "every 14 days, next, over Thanksgiving": Era(
        date(2030, 12, 12), Rhythm(FixedDays(14), NEXT),
    ),
    "every 7 days": Era(date(2026, 1, 2), Rhythm(FixedDays(7), NONE)),
    "monthly on the 31st": Era(date(2026, 3, 31), Rhythm(Monthly(31), NONE)),
    "15th and 31st, from a 31st": Era(
        date(2026, 3, 31), Rhythm(SemiMonthly((15, 31)), NONE),
    ),
    "1st and 15th": Era(date(2026, 3, 1), Rhythm(SemiMonthly((1, 15)), NONE)),
}

#: The fortnight-then-weekly owner R-PC110's question worked: every 14 days
#: from 2026-01-08 (01-08, 01-22, 02-05, 02-19), then weekly from 03-12,
#: which replaces the fortnight's 03-05 (R-PC75).
TWO_ERAS = (
    Era(date(2026, 1, 8), Rhythm(FixedDays(14), NONE)),
    Era(date(2026, 3, 12), Rhythm(FixedDays(7), NONE)),
)


class TestTheProducerIsAddEarliersInverse:
    """``pay_calendar.opening_rephase``: the phase moved up onto the new opening."""

    @pytest.mark.parametrize("case", sorted(ERA_CASES))
    @pytest.mark.parametrize("count", (1, 2, 5, 13))
    def test_it_undoes_the_rephase_add_earlier_made(self, case, count):
        """Add *count* below the era's first payday, then open on that payday again.

        ``earlier_paydays`` hands back the era re-phased DOWN onto the
        earliest added payday; asked of the re-phased era with the original
        opening, this producer must hand back the era exactly as it was --
        the phase and, for a month kind, the day the phase means.
        """
        era = ERA_CASES[case]
        opening = first_payday_of(era)
        rephased, _added = earlier_paydays((era,), opening, count)

        assert opening_rephase((rephased,), opening) == era

    @pytest.mark.parametrize("case", sorted(ERA_CASES))
    @pytest.mark.parametrize("steps", (1, 2, 7))
    def test_the_moved_era_plans_every_payday_it_planned(self, case, steps):
        """Up *steps* grid steps: the phase lands on that step's NOMINAL day.

        The same grid, so the plan past any day is unchanged; under a
        displacing convention the opening is the step's cash day, and the
        phase is the nominal day it was paid for.
        """
        era = ERA_CASES[case]
        opening = projected_payday(era.effective_from, era.rhythm, steps)

        moved = opening_rephase((era,), opening)

        assert moved == Era(
            nominal_payday(era.effective_from, era.rhythm.cadence, steps),
            era.rhythm,
        )
        far = date(2031, 6, 1)
        before = planned_paydays_after((era,), far)
        after = planned_paydays_after((moved,), far)
        assert [next(before) for _ in range(30)] == [next(after) for _ in range(30)]

    def test_a_later_eras_paycheck_moves_nothing(self):
        """03-12 is the weekly era's first payday: the fortnight would pay none."""
        assert opening_rephase(TWO_ERAS, date(2026, 3, 12)) is None
        assert opening_rephase(TWO_ERAS, date(2026, 3, 19)) is None

    def test_the_earliest_eras_last_paycheck_is_the_highest_move(self):
        """02-19 is the fortnight's last planned payday before the seam."""
        assert opening_rephase(TWO_ERAS, date(2026, 2, 19)) == Era(
            date(2026, 2, 19), TWO_ERAS[0].rhythm,
        )

    def test_a_record_paid_a_day_early_for_the_next_era_is_that_eras(self):
        """03-11 is the 03-12 paycheck paid early (R-PC47), not the fortnight's.

        The record is matched to the planned paycheck it stands for over the
        piecewise plan, so the opening is the weekly era's and the removal
        that leaves it first would take every fortnightly paycheck.
        """
        assert opening_rephase(TWO_ERAS, date(2026, 3, 11)) is None


def _record(user_id, first_payday, num_periods, rhythm):
    """Record a batch through the writer and commit it."""
    periods = pay_period_write.record_paydays(
        user_id=user_id, first_payday=first_payday,
        num_periods=num_periods, rhythm=rhythm,
    )
    _db.session.commit()
    return periods


def _paydays(user_id):
    """Return the owner's recorded paydays, ascending."""
    return [period.start_date for period in all_periods(user_id)]


def _stored_eras(user_id):
    """Return the owner's era rows as ``(effective_from, cadence_days, nominal_day, other_day)``."""
    return [
        (row.effective_from, row.cadence_days, row.nominal_day, row.other_day)
        for row in PayEra.query.filter_by(user_id=user_id)
        .order_by(PayEra.effective_from)
    ]


def _period_on(user_id, payday):
    """Return the owner's PayPeriod opening on *payday*."""
    return next(
        period for period in all_periods(user_id) if period.start_date == payday
    )


def _unchanged(user_id, paydays, eras):
    """Assert a refused door left the owner's paydays and eras as they were.

    Read after a COMMIT and a fresh session, so a staged write a rollback
    would have hidden cannot pass as a refusal that wrote nothing.
    """
    _db.session.commit()
    _db.session.remove()
    assert _paydays(user_id) == paydays
    assert _stored_eras(user_id) == eras


class TestTheDoorRemovesTheHeadAndMovesThePhase:
    """``pay_period_admin.remove_earlier_pay_periods`` on an owner with no money."""

    def test_it_removes_every_paycheck_before_the_one_picked(
        self, app, db, bare_user,
    ):
        """Start from 01-30: 01-02 and 01-16 go, the phase moves to 01-30."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 5, rhythm_of(14))

            removed = pay_period_admin.remove_earlier_pay_periods(
                user_id, _period_on(user_id, date(2026, 1, 30)).id,
            )
            db.session.commit()

            assert removed == 2
            assert _paydays(user_id) == [
                date(2026, 1, 30), date(2026, 2, 13), date(2026, 2, 27),
            ]
            assert _stored_eras(user_id) == [(date(2026, 1, 30), 14, None, None)]
            assert_pay_period_invariants(db.session, user_id)

    def test_starting_from_the_first_removes_nothing(self, app, db, bare_user):
        """The select's default: the current first paycheck, a no-op."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            paydays, eras = _paydays(user_id), _stored_eras(user_id)

            removed = pay_period_admin.remove_earlier_pay_periods(
                user_id, _period_on(user_id, date(2026, 1, 2)).id,
            )

            assert removed == 0
            _unchanged(user_id, paydays, eras)

    def test_the_last_paycheck_can_be_the_start_and_it_stays(
        self, app, db, bare_user,
    ):
        """Start from the LAST paycheck: every other goes, that one stays.

        R-PC110's "the same rule refuses removing every paycheck", made
        structural: the door keeps the paycheck it is told to start from,
        so no choice empties the schedule.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))

            removed = pay_period_admin.remove_earlier_pay_periods(
                user_id, _period_on(user_id, date(2026, 1, 30)).id,
            )
            db.session.commit()

            assert removed == 2
            assert _paydays(user_id) == [date(2026, 1, 30)]
            assert _stored_eras(user_id) == [(date(2026, 1, 30), 14, None, None)]

    def test_the_phase_moves_to_the_NOMINAL_day_of_a_displaced_payday(
        self, app, db, bare_user,
    ):
        """Thanksgiving 2030-11-28 is paid 11-27 under ``prior``; the phase is 11-28.

        Recorded from 11-14 on a fortnight under ``prior``: 11-14, 11-27
        (the displaced Thanksgiving), 12-12.  Starting from 11-27 moves the
        phase to the day that paycheck was paid FOR, on the grid; its cash
        day is off the grid and would move every planned payday.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2030, 11, 14), 3, rhythm_of(14, PRIOR))
            assert _paydays(user_id) == [
                date(2030, 11, 14), date(2030, 11, 27), date(2030, 12, 12),
            ]

            pay_period_admin.remove_earlier_pay_periods(
                user_id, _period_on(user_id, date(2030, 11, 27)).id,
            )
            db.session.commit()

            assert _stored_eras(user_id) == [(date(2030, 11, 28), 14, None, None)]

    def test_a_month_kind_era_moves_onto_its_clamped_day(self, app, db, bare_user):
        """Monthly on the 31st from 01-31: starting from 02-28 stores the 31st it means."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 31), 4, Rhythm(Monthly(31), NONE))
            assert _paydays(user_id)[:2] == [date(2026, 1, 31), date(2026, 2, 28)]

            pay_period_admin.remove_earlier_pay_periods(
                user_id, _period_on(user_id, date(2026, 2, 28)).id,
            )
            db.session.commit()

            assert _stored_eras(user_id) == [(date(2026, 2, 28), None, 31, None)]
            assert _paydays(user_id) == [
                date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30),
            ]

    def test_a_later_era_is_left_as_it_was(self, app, db, bare_user):
        """Only the earliest era moves; the weekly one keeps its day."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            _record(user_id, date(2026, 2, 13), 2, rhythm_of(7))

            pay_period_admin.remove_earlier_pay_periods(
                user_id, _period_on(user_id, date(2026, 1, 16)).id,
            )
            db.session.commit()

            assert _stored_eras(user_id) == [
                (date(2026, 1, 16), 14, None, None),
                (date(2026, 2, 13), 7, None, None),
            ]


class TestTheEarliestRhythmKeepsAPaycheck:
    """Ruling R-PC110: at least one paycheck of the earliest rhythm stays."""

    def _two_eras(self, user_id):
        """R-PC110's worked owner: 01-08 .. 02-19 fortnightly, then weekly from 03-12."""
        _record(user_id, date(2026, 1, 8), 4, rhythm_of(14))
        _record(user_id, date(2026, 3, 12), 3, rhythm_of(7))
        assert _paydays(user_id) == [
            date(2026, 1, 8), date(2026, 1, 22), date(2026, 2, 5),
            date(2026, 2, 19), date(2026, 3, 12), date(2026, 3, 19),
            date(2026, 3, 26),
        ]

    def test_starting_from_the_later_rhythm_is_refused_in_the_ruled_words(
        self, app, db, bare_user,
    ):
        """Choosing 03-12 is refused, naming 02-19; nothing is written."""
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_eras(user_id)
            paydays, eras = _paydays(user_id), _stored_eras(user_id)

            with pytest.raises(ValidationError) as refused:
                pay_period_admin.remove_earlier_pay_periods(
                    user_id, _period_on(user_id, date(2026, 3, 12)).id,
                )

            assert str(refused.value) == (
                "Keep at least 2026-02-19, your last paycheck paid every 14 "
                "days. Removing it would erase that pay rhythm."
            )
            _unchanged(user_id, paydays, eras)

    def test_starting_further_into_the_later_rhythm_names_the_same_payday(
        self, app, db, bare_user,
    ):
        """03-19 is refused too, and the payday to keep is still 02-19."""
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_eras(user_id)

            with pytest.raises(ValidationError, match="Keep at least 2026-02-19,"):
                pay_period_admin.remove_earlier_pay_periods(
                    user_id, _period_on(user_id, date(2026, 3, 19)).id,
                )

    def test_starting_from_the_earliest_rhythms_last_paycheck_is_admitted(
        self, app, db, bare_user,
    ):
        """02-19 stays, so the fortnight still pays a paycheck; its phase moves there."""
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_eras(user_id)

            removed = pay_period_admin.remove_earlier_pay_periods(
                user_id, _period_on(user_id, date(2026, 2, 19)).id,
            )
            db.session.commit()

            assert removed == 3
            assert _stored_eras(user_id) == [
                (date(2026, 2, 19), 14, None, None),
                (date(2026, 3, 12), 7, None, None),
            ]
            assert_pay_period_invariants(db.session, user_id)


class TestTheDoorRefusesAnIdItCannotResolve:
    """An id that is not the owner's removes nothing, as at truncate."""

    def test_another_owners_period_is_refused_and_both_schedules_stand(
        self, app, db, bare_user, seed_user, seed_periods,
    ):
        """The second owner's paycheck id: the unresolved refusal, nothing removed."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            paydays, eras = _paydays(user_id), _stored_eras(user_id)
            foreign = seed_periods[5]
            theirs = _paydays(seed_user["user"].id)

            with pytest.raises(PayPeriodUnresolved) as refused:
                pay_period_admin.remove_earlier_pay_periods(user_id, foreign.id)

            assert "choose the paycheck to start from" in str(refused.value)
            _unchanged(user_id, paydays, eras)
            assert _paydays(seed_user["user"].id) == theirs

    def test_an_id_that_names_nothing_is_refused_the_same_way(
        self, app, db, bare_user,
    ):
        """A missing id carries the same message: the door is no oracle."""
        with app.app_context():
            user_id = bare_user["user"].id
            created = _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))

            with pytest.raises(PayPeriodUnresolved, match="start from"):
                pay_period_admin.remove_earlier_pay_periods(
                    user_id, max(period.id for period in created) + 10_000,
                )


class TestAStatedHistoryStaysBelowTheRecord:
    """R-PC104's predicate cannot fire after a removal: the record opens later."""

    def test_a_removal_leaves_a_stated_history_below_the_new_first_payday(
        self, app, db, bare_user,
    ):
        """History 2025-12-05, the added paychecks removed: the record opens 01-02."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            pay_period_admin.add_earlier_pay_periods(user_id, 2)
            pay_schedule_service.set_history_opening(user_id, date(2025, 12, 5))
            db.session.commit()

            pay_period_admin.remove_earlier_pay_periods(
                user_id, _period_on(user_id, date(2026, 1, 2)).id,
            )
            db.session.commit()

            facts = pay_schedule_service.resolve_schedule(user_id)
            assert facts.history_opens_on == date(2025, 12, 5)
            assert _paydays(user_id)[0] == date(2026, 1, 2)
            assert pay_schedule_service.record_below_history(
                _paydays(user_id)[0], facts.history_opens_on,
            ) is False


def _added_head(user_id, count):
    """Add *count* paychecks before the owner's first, populated, and commit.

    Through C18-b's own door and the population a route owes it, so the head
    holds exactly what an owner's "Add earlier paychecks" leaves in it.
    """
    created = pay_period_admin.add_earlier_pay_periods(user_id, count)
    populate_in_a_fresh_pass(user_id, {period.id for period in created})
    _db.session.commit()
    return sorted(created, key=lambda period: period.start_date)


def _monthly_bill_from(seed_user, starts_on, *, is_envelope=False):
    """Author a priced monthly "Rent" on the seed account from *starts_on*.

    A CALENDAR-space rule, because only such a rule can name a day below
    the record (C18-b's route suite states why): an every-paycheck bill
    starts at the record it was authored against and never reaches a
    paycheck added below it.  The seeded books open 2024-01-04, so the
    books bound admits every added paycheck here.
    """
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Rent",
        default_amount=Decimal("1200.00"),
        is_envelope=is_envelope,
    )
    _db.session.add(template)
    _db.session.flush()
    state_template_price(template)
    make_cadence_rule(template, MONTHLY, starts_on=starts_on)
    return template


def _monthly_transfer_from(seed_user, to_account, starts_on):
    """Author a priced monthly transfer, Checking to *to_account*, from *starts_on*."""
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        name="To Savings",
        default_amount=Decimal("200.00"),
    )
    _db.session.add(template)
    _db.session.flush()
    state_template_price(template)
    make_cadence_rule(template, MONTHLY, starts_on=starts_on)
    return template


def _rows_by_period(user_id):
    """Return every live row the owner holds as a sorted, comparable snapshot."""
    periods = {period.id: period.start_date for period in all_periods(user_id)}
    transactions = sorted(
        (periods[row.pay_period_id], row.id, row.name, row.status_id,
         row.transfer_id, row.is_deleted)
        for row in _db.session.query(Transaction).filter(
            Transaction.pay_period_id.in_(list(periods)),
        )
    )
    transfers = sorted(
        (periods[row.pay_period_id], row.id, row.status_id, row.is_deleted)
        for row in _db.session.query(Transfer).filter(
            Transfer.pay_period_id.in_(list(periods)),
        )
    )
    return transactions, transfers


class TestAnAddUndoneLeavesNothingBehind:
    """C18-b's door and this one, in turn, with the ledger re-synced between.

    The seeded owner (10 fortnightly paychecks from 2026-01-02, books open
    2024-01-04) with a monthly bill and a monthly transfer from 2025-11-24,
    both populated -- so the paychecks "Add earlier" records receive template
    rows and a recurring transfer with both shadows, which the removal must
    take with them (R-PC109's "template rows go").  Between the add and the
    removal the ledger is re-synced, as any loan or balance door does, which
    files the books' opening (dated 2024-01-04, before every paycheck) in the
    earliest paycheck -- an added one (R-PC114's case).
    """

    def test_paydays_eras_rows_and_the_posted_ledger_come_back(
        self, app, db, seed_user, seed_periods,
    ):
        """Add 3, re-sync, remove them.

        Compared: the paydays, the stored eras, every live row's period,
        id, name, status, transfer link and delete flag, and every posted
        total per scenario and ledger account.  Not compared: journal entry
        ROWS, which a re-sync appends to by design (a reversal and a
        re-post), and amounts on rows, which no door here writes.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            # Savings' books must reach the added paychecks, or the books
            # bound (pay_calendar:C18-a) keeps the transfer out of them.
            restate_account_opening(db.session, savings, date(2023, 6, 1))
            # From 2025-11-24: the 11-24 and 12-24 occurrences fall in the
            # 11-21 and 12-19 paychecks the add records, 01-24 on in the
            # seeded ones.
            _monthly_bill_from(seed_user, date(2025, 11, 24))
            _monthly_transfer_from(seed_user, savings, date(2025, 11, 24))
            populate_in_a_fresh_pass(user_id, {period.id for period in seed_periods})
            db.session.commit()
            paydays, eras = _paydays(user_id), _stored_eras(user_id)
            rows = _rows_by_period(user_id)
            totals = pay_period_locks.posted_totals(user_id)

            head = _added_head(user_id, 3)
            added = {period.id for period in head}
            loan_posting_service.resync_user_loan_postings(user_id)
            account_posting_service.resync_user_account_anchor_postings(user_id)
            db.session.commit()
            assert _entries_in(added), "the re-sync must file the opening in the head"
            assert _db.session.query(Transfer).filter(
                Transfer.pay_period_id.in_(added),
            ).count() == 2, "the added paychecks must hold the recurring transfer"
            assert _db.session.query(Transaction).filter(
                Transaction.pay_period_id.in_(added),
                Transaction.transfer_id.is_(None),
            ).count() == 2, "the added paychecks must hold the template bill"

            removed = pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            )
            db.session.commit()

            assert removed == 3
            assert _paydays(user_id) == paydays
            assert _stored_eras(user_id) == eras
            assert _rows_by_period(user_id) == rows
            assert pay_period_locks.posted_totals(user_id) == totals
            assert_pay_period_invariants(db.session, user_id)


def _entries_in(period_ids):
    """Return the journal entries filed in *period_ids* as ``(id, source kind id)``."""
    return _db.session.query(JournalEntry.id, JournalEntry.source_kind_id).filter(
        JournalEntry.pay_period_id.in_(list(period_ids)),
    ).all()


def _ledger_ids_of(account):
    """Return *account*'s ledger accounts, ascending (its own row first)."""
    return [
        row[0] for row in _db.session.query(LedgerAccount.id)
        .filter(LedgerAccount.account_id == account.id)
        .order_by(LedgerAccount.id)
    ]


def _two_ledger_ids(seed_user):
    """Return two ledger accounts the seeded owner's Checking is paired with.

    The account's own row and the chart row resolved for its opening -- any
    two distinct ledger accounts serve an entry and its reversal.
    """
    ids = [
        row[0] for row in _db.session.query(LedgerAccount.id)
        .filter(LedgerAccount.account_id == seed_user["account"].id)
        .order_by(LedgerAccount.id)
    ]
    assert len(ids) >= 2, "the seeded Checking pairs with two ledger accounts"
    return ids[0], ids[1]


def _held(user_id, first_kept):
    """Try the removal from *first_kept* and return the refusal's message."""
    with pytest.raises(ValidationError) as refused:
        pay_period_admin.remove_earlier_pay_periods(user_id, first_kept.id)
    return str(refused.value)


class TestWhatAPaycheckMayHold:
    """R-PC109's filed half: anything but an untouched template row refuses.

    Each case adds two paychecks before the seeded owner's 2026-01-02 (so
    2025-12-05 and 2025-12-19), puts one kind of row into the 12-19 one, and
    starts from 01-02.  Every refusal writes nothing.
    """

    def _world(self, seed_user):
        """The seeded owner with two paychecks added before 01-02."""
        user_id = seed_user["user"].id
        head = _added_head(user_id, 2)
        return user_id, head

    def test_a_typed_row_is_refused_in_the_ruled_words(
        self, app, db, seed_user, seed_periods,
    ):
        """The ruling's own example: one row the owner typed, named."""
        with app.app_context():
            user_id, head = self._world(seed_user)
            add_txn(db.session, seed_user, head[1], "Groceries", "45.00")
            db.session.commit()
            paydays, eras = _paydays(user_id), _stored_eras(user_id)

            message = _held(user_id, seed_periods[0])

            assert message == (
                "The 2025-12-19 paycheck holds 1 item you entered or changed "
                "(Groceries). Delete or move it first."
            )
            _unchanged(user_id, paydays, eras)

    def test_items_in_two_paychecks_are_each_named(
        self, app, db, seed_user, seed_periods,
    ):
        """One sentence per paycheck, then one remedy in the plural."""
        with app.app_context():
            user_id, head = self._world(seed_user)
            add_txn(db.session, seed_user, head[0], "Gas", "30.00")
            add_txn(db.session, seed_user, head[1], "Groceries", "45.00")
            add_txn(db.session, seed_user, head[1], "Books", "12.00")
            db.session.commit()

            assert _held(user_id, seed_periods[0]) == (
                "The 2025-12-05 paycheck holds 1 item you entered or changed "
                "(Gas). The 2025-12-19 paycheck holds 2 items you entered or "
                "changed (Books, Groceries). Delete or move them first."
            )

    @pytest.mark.parametrize("status", [
        StatusEnum.DONE, StatusEnum.CREDIT, StatusEnum.CANCELLED,
    ])
    def test_a_template_row_the_owner_marked_is_refused(
        self, app, db, seed_user, seed_periods, status,
    ):
        """A template row paid, put on a card or cancelled is the owner's act."""
        with app.app_context():
            user_id = seed_user["user"].id
            _monthly_bill_from(seed_user, date(2025, 12, 20))
            db.session.commit()
            head = _added_head(user_id, 2)
            row = db.session.query(Transaction).filter_by(
                pay_period_id=head[1].id,
            ).one()
            if status == StatusEnum.DONE:
                settle_cash_row(row, settled_on=head[1].start_date)
            else:
                row.status_id = ref_cache.status_id(status)
            db.session.commit()

            assert "(Rent)" in _held(user_id, seed_periods[0])

    def test_a_template_row_the_owner_changed_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """An override is the owner's figure, not the template's."""
        with app.app_context():
            user_id = seed_user["user"].id
            _monthly_bill_from(seed_user, date(2025, 12, 20))
            db.session.commit()
            head = _added_head(user_id, 2)
            db.session.query(Transaction).filter_by(
                pay_period_id=head[1].id,
            ).one().is_override = True
            db.session.commit()

            assert "(Rent)" in _held(user_id, seed_periods[0])

    def test_a_template_row_holding_a_purchase_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """Untouched but for a purchase: the purchase is the owner's money."""
        with app.app_context():
            user_id = seed_user["user"].id
            _monthly_bill_from(seed_user, date(2025, 12, 20), is_envelope=True)
            db.session.commit()
            head = _added_head(user_id, 2)
            row = db.session.query(Transaction).filter_by(
                pay_period_id=head[1].id,
            ).one()
            add_entry(db.session, seed_user, row, Decimal("20.00"), head[1].start_date)
            db.session.commit()

            assert "(Rent)" in _held(user_id, seed_periods[0])

    def test_a_transfer_made_by_hand_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A one-time transfer is named; its shadows are not counted twice."""
        with app.app_context():
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            db.session.commit()
            head = _added_head(user_id, 2)
            transfer_service.create_transfer(transfer_service.TransferSpec(
                user_id=user_id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=head[1].id,
                scenario_id=seed_user["scenario"].id,
                amount_ownership=AmountOwnership.own(Decimal("150.00")),
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                category_id=None,
            ))
            db.session.commit()

            message = _held(user_id, seed_periods[0])

            assert message.startswith("The 2025-12-19 paycheck holds 1 item ")

    def test_a_pay_stub_on_a_removed_payday_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A stub sits on a paycheck the app holds (R-SAL49)."""
        with app.app_context():
            user_id, head = self._world(seed_user)
            profile = make_salary_profile(seed_user, db.session)
            db.session.flush()
            db.session.add(PayStub(
                salary_profile_id=profile.id, payday=head[0].start_date,
                base_pay=Decimal("2884.62"),
            ))
            db.session.commit()

            assert _held(user_id, seed_periods[0]) == (
                "A pay stub is saved for 2025-12-05. Delete it first, or start "
                "from 2025-12-05 or earlier."
            )

    def test_a_template_row_paid_then_un_paid_goes_with_the_paycheck(
        self, app, db, seed_user, seed_periods,
    ):
        """The revert keeps the seam's covering mark on the row; it is no purchase.

        Since plan step balance:X-bi-3e-2 a revert keeps the settlement mark,
        un-dated, under the Projected row (``Transaction.purchases`` excludes
        it, R-BAL68).  The posting writer reverses the paid leg in the same
        paycheck and entry date, so the pair nets to $0.00 there and goes
        with the paycheck (R-PC114: no posted total moves).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _monthly_bill_from(seed_user, date(2025, 12, 20))
            db.session.commit()
            head = _added_head(user_id, 2)
            row = db.session.query(Transaction).filter_by(
                pay_period_id=head[1].id,
            ).one()
            settle_cash_row(row, settled_on=head[1].start_date)
            db.session.commit()
            # Un-paid as the route does it: the seam, then the posting
            # writer, which reverses the leg in the same paycheck and day.
            status_seam.apply_status_change(
                row, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            posting_service.sync_transaction_postings(row)
            db.session.commit()
            assert row.entries and not row.purchases, (
                "the revert must leave the covering mark and no purchase"
            )
            assert len(_entries_in({head[1].id})) == 2, (
                "the paid leg and its reversal must both sit in the paycheck"
            )

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2

    def test_template_rows_alone_go_with_the_paycheck(
        self, app, db, seed_user, seed_periods,
    ):
        """The control: the same world with nothing the owner made is removed."""
        with app.app_context():
            user_id = seed_user["user"].id
            _monthly_bill_from(seed_user, date(2025, 12, 6))
            db.session.commit()
            head = _added_head(user_id, 2)
            held = {period.id for period in head}
            assert db.session.query(Transaction).filter(
                Transaction.pay_period_id.in_(held),
            ).count() == 1

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2
            db.session.commit()

            assert db.session.query(Transaction).filter(
                Transaction.pay_period_id.in_(held),
            ).count() == 0


class TestTheLedgerIsReFiledAndLosesNothing:
    """R-PC114 (amending R-PC109): booked entries are re-filed, and no total may move.

    The ledger files an entry dated before the first paycheck in the
    EARLIEST one (R-PC53), and every re-sync re-files there -- so once a
    paycheck is added below the record, the books' or a loan's opening sits
    in it.  The removal re-files through Reset's two re-syncs, and is
    refused only if a posted total still moves.
    """

    def test_an_account_opening_filed_in_an_added_paycheck_goes_back(
        self, app, db, seed_user, seed_periods,
    ):
        """Review 1's first probe: a re-sync after the add, then the removal."""
        with app.app_context():
            user_id = seed_user["user"].id
            head = _added_head(user_id, 2)
            account_posting_service.resync_user_account_anchor_postings(user_id)
            db.session.commit()
            opening = ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING)
            assert opening in {kind for _id, kind in _entries_in({head[0].id})}
            totals = pay_period_locks.posted_totals(user_id)

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2
            db.session.commit()

            # The totals carry the case: the opening's re-post is dropped with
            # the head, and only the re-sync puts its money back.
            assert pay_period_locks.posted_totals(user_id) == totals

    def test_a_loans_opening_filed_in_an_added_paycheck_goes_back(
        self, app, db, seed_user, seed_periods,
    ):
        """Review 1's second probe: a loan from 2020, the loan re-sync after the add."""
        with app.app_context():
            user_id = seed_user["user"].id
            create_loan_account(
                seed_user, db.session, name="Car Loan",
                principal=Decimal("20000.00"), rate=Decimal("0.05"),
                origination_date=date(2020, 1, 1),
            )
            db.session.commit()
            head = _added_head(user_id, 2)
            loan_posting_service.resync_user_loan_postings(user_id)
            db.session.commit()
            loan_opening = ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING)
            assert loan_opening in {kind for _id, kind in _entries_in({head[0].id})}
            totals = pay_period_locks.posted_totals(user_id)

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2
            db.session.commit()

            assert pay_period_locks.posted_totals(user_id) == totals

    def test_a_self_cancelling_pair_goes_with_its_paycheck(
        self, app, db, seed_user, seed_periods,
    ):
        """An entry and its reversal net to $0.00 on each ledger account."""
        with app.app_context():
            user_id = seed_user["user"].id
            head = _added_head(user_id, 2)
            checking, other = _two_ledger_ids(seed_user)
            make_balanced_entry(
                db.session, seed_user, from_ledger_id=checking,
                to_ledger_id=other, period_id=head[1].id,
            )
            make_balanced_entry(
                db.session, seed_user, from_ledger_id=other,
                to_ledger_id=checking, period_id=head[1].id,
            )
            totals = pay_period_locks.posted_totals(user_id)

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2
            db.session.commit()

            assert pay_period_locks.posted_totals(user_id) == totals

    def test_an_entry_no_re_sync_rebuilds_is_refused_in_the_ruled_words(
        self, app, db, seed_user, seed_periods,
    ):
        """One entry in an added paycheck that no record rebuilds: refused, rolled back.

        Built by hand, because no door books money into a paycheck with
        nothing in it: this is the guard's own firing case, and its message
        is the ruled one.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            head = _added_head(user_id, 2)
            checking, other = _two_ledger_ids(seed_user)
            make_balanced_entry(
                db.session, seed_user, from_ledger_id=checking,
                to_ledger_id=other, period_id=head[1].id,
            )
            paydays, eras = _paydays(user_id), _stored_eras(user_id)
            totals = pay_period_locks.posted_totals(user_id)

            # No rollback here: the door rolls back its own savepoint, and
            # ``_unchanged`` COMMITS what is left, so a staged delete would
            # show.
            message = _held(user_id, seed_periods[0])

            assert message == (
                f"Removing these paychecks would change the balance the app has "
                f"booked for {seed_user['account'].name}, so nothing was "
                f"removed. Start from an earlier paycheck."
            )
            _unchanged(user_id, paydays, eras)
            assert pay_period_locks.posted_totals(user_id) == totals


    def test_a_legacy_entry_between_two_accounts_is_refused_naming_both(
        self, app, db, seed_user, seed_periods,
    ):
        """Review 2's realistic firing case: Checking to Savings, both trued up later.

        Each later true-up's correction absorbs the entry's effect on its
        account, so removing the entry moves each correction's counter leg:
        both accounts' booked balances would change, and both are named.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            db.session.commit()
            head = _added_head(user_id, 2)
            make_balanced_entry(
                db.session, seed_user,
                from_ledger_id=_ledger_ids_of(seed_user["account"])[0],
                to_ledger_id=_ledger_ids_of(savings)[0],
                period_id=head[1].id,
            )
            anchor_service.apply_anchor_true_up(
                account=seed_user["account"], new_balance=Decimal("900.00"),
            )
            anchor_service.apply_anchor_true_up(
                account=savings, new_balance=Decimal("600.00"),
            )
            paydays, eras = _paydays(user_id), _stored_eras(user_id)

            assert _held(user_id, seed_periods[0]) == (
                f"Removing these paychecks would change the balance the app has "
                f"booked for {seed_user['account'].name}, Savings, so nothing "
                f"was removed. Start from an earlier paycheck."
            )
            _unchanged(user_id, paydays, eras)


class TestMoneyDatedInsideTheHead:
    """R-PC109's dated half: the removal may not raise the floor over recorded money.

    The seeded owner's 10 paychecks open 2026-01-02; two are added
    (2025-12-05, 2025-12-19), which moves the recordable floor to 12-05.
    Money recorded on 2025-12-20 in a KEPT paycheck is below 01-02, so
    starting from 01-02 is refused, naming the 12-19 paycheck that holds
    the day.
    """

    DAY = date(2025, 12, 20)

    def _refusal(self, what):
        """The ruled shape for money dated on :attr:`DAY`."""
        return (
            f"{what} 2025-12-20, inside the paychecks you would remove, and "
            f"money can't be dated before your schedule starts. Start from "
            f"2025-12-19 or earlier."
        )

    def test_a_row_in_a_kept_paycheck_marked_paid_inside_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """Rent in the 01-02 paycheck, paid 12-20 once the floor allowed it."""
        with app.app_context():
            user_id = seed_user["user"].id
            _added_head(user_id, 2)
            add_txn(
                db.session, seed_user, seed_periods[0], "Water", "40.00",
                status_enum=StatusEnum.DONE, settled_on=self.DAY,
            )
            db.session.commit()
            paydays, eras = _paydays(user_id), _stored_eras(user_id)

            assert _held(user_id, seed_periods[0]) == self._refusal(
                "Water is marked paid on",
            )
            _unchanged(user_id, paydays, eras)

    def test_a_purchase_in_a_kept_paycheck_paid_inside_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A purchase's own bank day is money dated, whatever its row's status."""
        with app.app_context():
            user_id = seed_user["user"].id
            _added_head(user_id, 2)
            row = add_txn(db.session, seed_user, seed_periods[0], "Food", "90.00")
            add_entry(
                db.session, seed_user, row, Decimal("25.00"), self.DAY,
                settled_on=self.DAY, description="Market",
            )
            db.session.commit()

            assert _held(user_id, seed_periods[0]) == self._refusal(
                "The purchase Market is marked paid on",
            )

    def test_a_balance_recorded_inside_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """An account's assertion observed 12-20: the back-dated state the floor refuses."""
        with app.app_context():
            user_id = seed_user["user"].id
            _added_head(user_id, 2)
            reassert_balance_on(
                db.session, seed_user["account"],
                at=datetime(2025, 12, 20, 12, tzinfo=EASTERN),
            )
            db.session.commit()

            assert _held(user_id, seed_periods[0]) == self._refusal(
                f"{seed_user['account'].name}'s balance is recorded for",
            )

    def test_a_loan_balance_recorded_inside_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan true-up dated 12-20; the loan's origination in 2020 is not money dated."""
        with app.app_context():
            user_id = seed_user["user"].id
            create_loan_with_trueup(
                seed_user, db.session,
                origination_principal=Decimal("20000.00"),
                anchor_balance=Decimal("15000.00"), anchor_date=self.DAY,
                rate=Decimal("0.05"), origination_date=date(2020, 1, 1),
                name="Car Loan",
            )
            _added_head(user_id, 2)

            assert _held(user_id, seed_periods[0]) == self._refusal(
                "Car Loan's balance is recorded for",
            )

    def test_money_already_below_the_floor_is_not_the_removals(
        self, app, db, seed_user, seed_periods,
    ):
        """A settle day before 12-05 was below the floor before; it does not refuse.

        The span is where the removal MOVES the floor, [12-05, 01-02); a
        legacy day below 12-05 stays below either way.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _added_head(user_id, 2)
            add_txn(
                db.session, seed_user, seed_periods[0], "Water", "40.00",
                status_enum=StatusEnum.DONE, settled_on=date(2025, 11, 30),
            )
            db.session.commit()

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2


    def test_a_purchase_a_companion_recorded_inside_is_refused(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """The purchase is the owner's whoever typed it (review 1's M1)."""
        with app.app_context():
            user_id = seed_user["user"].id
            _added_head(user_id, 2)
            row = add_txn(db.session, seed_user, seed_periods[0], "Food", "90.00")
            add_entry(
                db.session, seed_companion, row, Decimal("25.00"), self.DAY,
                settled_on=self.DAY, description="Market",
            )
            db.session.commit()

            assert _held(user_id, seed_periods[0]) == self._refusal(
                "The purchase Market is marked paid on",
            )

    def test_a_loans_tracking_start_inside_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The balance stated at a loan's setup stands like a true-up (review 1's M2)."""
        with app.app_context():
            user_id = seed_user["user"].id
            create_loan_account(
                seed_user, db.session, name="Car Loan",
                principal=Decimal("20000.00"), rate=Decimal("0.05"),
                origination_date=date(2020, 1, 1),
                tracked_balance=Decimal("15000.00"), tracked_from=self.DAY,
            )
            db.session.commit()
            _added_head(user_id, 2)

            assert _held(user_id, seed_periods[0]) == self._refusal(
                "Car Loan's balance is recorded for",
            )

    def test_money_on_the_new_first_payday_is_admitted(
        self, app, db, seed_user, seed_periods,
    ):
        """The span stops BEFORE the paycheck kept first: its own payday is inside the schedule."""
        with app.app_context():
            user_id = seed_user["user"].id
            _added_head(user_id, 2)
            add_txn(
                db.session, seed_user, seed_periods[0], "Water", "40.00",
                status_enum=StatusEnum.DONE, settled_on=seed_periods[0].start_date,
            )
            db.session.commit()

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2
