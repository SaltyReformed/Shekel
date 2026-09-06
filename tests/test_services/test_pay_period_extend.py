"""Tests for pay-period CRUD slice (c): extend + the repopulation helper.

``populate_periods_from_active_templates`` fills newly-created (empty)
periods with each active template's recurring transactions AND transfers;
``extend_pay_periods`` tail-appends the periods and LEAVES THEM EMPTY.

**The two are one operation the ROUTE composes, and that is ruling R-R38**
(plan step R7d-c-1).  The door used to do both in one call -- a write and then
a read-dependent write -- so no caller could get between them to open the read
pass the generation resolves in, and the populate had to open its own.  A test
here therefore grades ONE of two contracts and says which: the door's own (what
it appends, what cadence it continues, what it refuses) calls
``extend_pay_periods`` alone, and any assertion about recurring ROWS runs
``_extend_and_populate``, which is what the route runs.  ``POST
/pay-periods/extend`` itself is graded in
``tests/test_routes/test_pay_period_admin.py``.

Because a pay period is the spine of every financial number, the extend
happy-path test asserts all four disciplines: structural invariants
(Discipline 1, ``assert_pay_period_invariants``), hand-computed as-of
balances in both the retained and the new window (Discipline 2), and the
production integrity checker passing (Discipline 3).  See
``docs/plans/implementation_plan_pay_period_crud.md``.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import BusinessDayShiftEnum
from app.exceptions import ValidationError
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.routes._period_population import populate_new_periods
from app.services import (
    pay_period_admin,
    pay_period_write,
    pay_schedule_service,
)
from app.services import pay_calendar
from app.services.pay_calendar import calendar_for
from scripts.integrity_check import (
    check_balance_anomalies,
    check_referential_integrity,
)
from tests._test_helpers import (
    rhythm_of,
    assert_pay_period_invariants,
    create_savings_account,
    derived_span,
    last_covered_day,
    make_expense_template,
    make_transfer_template,
    populate_in_a_fresh_pass,
    resolved_amount,
    seam_cash_balance_at,
)


def _future_periods(db_session, seed_user, count=4, start=date(2026, 7, 3)):
    """Generate `count` biweekly future periods (indices 1..count)."""
    periods = pay_period_write.record_paydays(
        user_id=seed_user["user"].id,
        first_payday=start,
        num_periods=count,
        rhythm=rhythm_of(14),
    )
    db_session.commit()
    return periods


def _extend_andpopulate_in_a_fresh_pass(user_id, num_periods):
    """Run BOTH halves of an extend, exactly as the route does.

    ``extend_pay_periods`` records the paydays; ``populate_new_periods`` opens
    the generate pass afterwards and fills them.  Ruling **R-R38**: the pass
    may only be opened above the service layer, and it must be opened AFTER
    the write, so the two are separate calls in that order.

    Args:
        user_id: The owning user's id.
        num_periods: How many periods to append.

    Returns:
        The newly created periods, now populated.
    """
    new_periods = pay_period_admin.extend_pay_periods(user_id, num_periods)
    populate_new_periods(user_id, new_periods)
    return new_periods


def _period_length(period):
    """Inclusive day-span of a period == its cadence."""
    return (last_covered_day(period) - period.start_date).days + 1


#: The last recorded payday :class:`TestTheExtendAnchorIsTheNOMINALGrid` builds
#: from, and the closed day its rhythm walks into.
#:
#: 2030-11-14 is an ordinary Thursday and sits on the grid; one cadence later
#: is 2030-11-28, the fourth Thursday of November and therefore Thanksgiving.
#: So the owner's next NOMINAL payday is a day no money moves on, which is the
#: only shape in which this door's two candidate answers differ at all.
_ON_GRID_PAYDAY = date(2030, 11, 14)
_CLOSED_NEXT_PAYDAY = date(2030, 11, 28)


class TestTheExtendAnchorIsTheNOMINALGrid:
    """Plan step **C14-d**: extend continues the rhythm, it does not read the cash day.

    ``extend_pay_periods`` asked
    :meth:`~app.services.pay_calendar.PayCalendar.span_containing` for the day
    past the horizon and handed it to
    :func:`~app.services.pay_period_write.record_paydays`, which spaces the
    whole batch by flat cadence arithmetic from whatever day it is given.  That
    is one answer while nothing can move a payday.  From ``C14-e`` the
    calendar's answer is the CASH day -- the nominal day displaced onto a
    business day -- so the batch would carry the displacement on every payday
    in it AND into the anchor the next extend reads.  **R-PC54** calls that
    feeding a cash date back into the rhythm.

    **These cases STATE a displacing convention on the owner's schedule row**,
    which since plan step ``C14-e-3`` is all it takes: the shipped producer
    reads it.  They drove a substituted producer until then, because with the
    convention off the grid and the projection agree on every day and the door
    could not be shown reading the right one.  The double is DELETED -- a
    stand-in for a shipped producer is a fence whose subject has landed.
    """

    def _last_payday_before_a_holiday(
        self, db_session, user_id, shift=BusinessDayShiftEnum.NONE,
    ):
        """Record 2030-11-14 alone under *shift*; the next nominal day is Thanksgiving.

        The convention is STORED and not merely simulated, which an adversarial
        review of this step required: ``extend_pay_periods`` builds its rhythm
        from the owner's stored row, and the producer ``C14-e`` ships reads
        that same row.  A case that displaced globally while the row said
        ``none`` would pin a world the shipped step cannot reproduce.
        *It read the convention through* ``pay_schedule_service.resolve_shift``
        *until plan step* ``C14-e-1``, *which put the pair on the*
        :class:`~app.services.pay_calendar.PayCalendar` *the door already
        builds and deleted that function with its duplicate query.*
        The recorded row is 2030-11-14 under every convention, because that day
        is an ordinary Thursday and so is its own displacement -- which is what
        keeps this one fixture now that ``_requested_paydays`` DISPLACES what
        it records (``C14-e-3``).
        """
        pay_period_write.record_paydays(
            user_id=user_id, first_payday=_ON_GRID_PAYDAY,
            num_periods=1, rhythm=rhythm_of(14, shift),
        )
        db_session.commit()

    def test_the_appended_paydays_stay_on_the_grid(
        self, app, db, bare_user,
    ):
        """Three appended paydays, hand-computed from the grid, under ``prior``.

        2030-11-14 + 14, + 28, + 42.  The calendar would have answered
        2030-11-27 for the first of them, which the case asserts directly
        rather than trusting the description: an anchor a day earlier makes all
        THREE wrong, and the next extend would read the third as its own
        anchor, so the error is permanent rather than confined to the batch.

        **``C14-e-3`` moved exactly ONE of them** -- the batch runs on the
        nominal grid and each element is RECORDED displaced (ledger row
        **PC-497** fault 1), so the 2030-11-28 grid day becomes the 2030-11-27
        payroll really pays.  Only the first, because 2030-12-12 and 2030-12-26
        are ordinary Thursdays and displace to themselves.  *This paragraph was
        written at ``C14-d`` PREDICTING these three days, which is the shape
        CLAUDE.md rule 5 asks for: a failing test means the code is wrong
        unless the developer has confirmed the behaviour changed, and the
        confirmation was recorded a step before the change.*
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._last_payday_before_a_holiday(
                db.session, user_id, BusinessDayShiftEnum.PRIOR,
            )
            calendar = calendar_for(user_id)
            cash_day = calendar.span_containing(
                calendar.horizon() + timedelta(days=1),
            ).start_date
            assert cash_day == date(2030, 11, 27), (
                "the door's OLD expression must answer a different day here, "
                "or this case grades the two spellings agreeing"
            )

            new_periods = pay_period_admin.extend_pay_periods(user_id, 3)
            db.session.commit()

            assert [period.start_date for period in new_periods] == [
                date(2030, 11, 27), date(2030, 12, 12), date(2030, 12, 26),
            ]

    def test_the_batch_does_not_inherit_a_displacement(
        self, app, db, bare_user,
    ):
        """Every appended payday is a whole number of cadences off the anchor.

        The property rather than the three dates: a batch anchored on a cash
        day is still evenly spaced, so spacing alone proves nothing -- what
        this asserts is that the spacing runs from the RECORDED payday the
        owner already had, which is what makes the rhythm reproducible from the
        table.  A cash anchor fails it by exactly the displacement.

        **It opens by checking that the substitution is LIVE**, because on the
        nominal path the deleted expression produces these same offsets: all of
        this case's discriminating power rests on the projection having moved,
        and an adversarial review of this step found it the one new case that
        would go quiet rather than red if the patch ever stopped reaching the
        producer.

        **The FIRST offset is 13 since ``C14-e-3``** -- the writer records each
        element displaced (ledger row **PC-497** fault 1) and the 2030-11-28
        grid day is paid 2030-11-27.  Every LATER offset is a whole cadence,
        which is the assertion: ``[13, 27, 41, 55, 69, 83]`` -- what a first
        draft of this paragraph predicted -- is what a batch ANCHORED on the
        cash day gives, the whole progression shifted, and the defect this case
        exists to refuse.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._last_payday_before_a_holiday(
                db.session, user_id, BusinessDayShiftEnum.PRIOR,
            )
            assert pay_calendar.projected_payday(
                _ON_GRID_PAYDAY, rhythm_of(14, BusinessDayShiftEnum.PRIOR), 1,
            ) == date(2030, 11, 27), (
                "the projection must have MOVED here, or these offsets are the "
                "nominal path and grade nothing"
            )

            new_periods = pay_period_admin.extend_pay_periods(user_id, 6)
            db.session.commit()

            offsets = [
                (period.start_date - _ON_GRID_PAYDAY).days
                for period in new_periods
            ]
            assert offsets == [13, 28, 42, 56, 70, 84]

    def test_a_SECOND_extend_reads_an_anchor_the_first_left_on_the_grid(
        self, app, db, bare_user,
    ):
        """"Permanently" is a claim about the NEXT extend, so it takes two.

        Each extend anchors on the last recorded payday, so a batch that ended
        off the grid hands the next one an off-grid anchor and the error never
        washes out.  Every other case here runs ONE extend and therefore grades
        the batch rather than the compounding; an adversarial review of this
        step named that gap.  Two extends of three, and the sixth appended
        payday is still a whole number of cadences from the payday the owner
        started with.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._last_payday_before_a_holiday(
                db.session, user_id, BusinessDayShiftEnum.PRIOR,
            )
            pay_period_admin.extend_pay_periods(user_id, 3)
            db.session.commit()
            second = pay_period_admin.extend_pay_periods(user_id, 3)
            db.session.commit()

            assert [
                (period.start_date - _ON_GRID_PAYDAY).days for period in second
            ] == [56, 70, 84]

    def test_a_FORWARD_convention_is_ACCEPTED_and_that_CLOSES_PC_497(
        self, app, db, bare_user,
    ):
        """The pin ``C14-d`` left, INVERTED by ``C14-e-3``.

        **It was a refusal, and the refusal was the finding.**  Until this step
        ``record_paydays`` recorded the days it was handed and spaced them
        nominally, so under a displacing convention it wrote NOMINAL paydays
        where the projection showed CASH ones.  Under ``next`` the derivation
        runs the 2030-11-14 paycheck through 2030-11-28 -- payroll pays
        2030-11-29 -- so the floor ``C14-d`` corrected refused an extend
        offering the nominal 2030-11-28, correctly: a payday there really would
        split the paycheck the calendar derives.  The case asserted that
        refusal, and said in its own docstring that what would make it fail is
        ``C14-e`` landing without the writer's half.

        **The remedy it named is what shipped**: ``_requested_paydays`` runs
        the progression on the grid and records each element DISPLACED, which
        lands 2030-11-29 -- exactly the floor -- and is accepted.  So this
        asserts the ACCEPTANCE and the three recorded days, and the ``$0.00``
        control below is what keeps it from grading the nominal path.

        **Where the refusal landed while it stood**: ``top_up_rolling_window``
        reaches this door from ``/grid`` and ``/dashboard`` with no handler,
        and ``app/error_handlers.py`` registers none for ``ValidationError``,
        so it was a 500 on both -- ledger row **N-494**'s shape through a
        second trigger, and the reason ledger row **PC-497** fault 1 was worth
        a pin rather than a note.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._last_payday_before_a_holiday(
                db.session, user_id, BusinessDayShiftEnum.NEXT,
            )
            # The floor the old spelling was refused BY, asserted before the
            # door runs: without this the acceptance below could be a floor
            # that moved rather than a writer that displaced.
            assert pay_calendar.projected_payday(
                _ON_GRID_PAYDAY, rhythm_of(14, BusinessDayShiftEnum.NEXT), 1,
            ) == date(2030, 11, 29)

            new_periods = pay_period_admin.extend_pay_periods(user_id, 3)
            db.session.commit()

            assert [period.start_date for period in new_periods] == [
                date(2030, 11, 29), date(2030, 12, 12), date(2030, 12, 26),
            ]

    def test_it_is_ZERO_DOLLARS_while_the_convention_displaces_nothing(
        self, app, db, bare_user,
    ):
        """The ``none`` path: the two producers agree, so nothing moves.

        The control for every case above, and the step's own ``$0.00`` claim --
        which since ``C14-e-3`` is a claim about an owner who has not answered
        the convention question rather than about an unwired producer.  Under
        ``none`` the displacement is the identity, so the door appends the
        NOMINAL grid days, Thanksgiving included.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._last_payday_before_a_holiday(db.session, user_id)

            calendar = calendar_for(user_id)
            assert calendar.span_containing(
                calendar.horizon() + timedelta(days=1),
            ).start_date == _CLOSED_NEXT_PAYDAY

            new_periods = pay_period_admin.extend_pay_periods(user_id, 3)
            db.session.commit()
            assert [period.start_date for period in new_periods] == [
                _CLOSED_NEXT_PAYDAY, date(2030, 12, 12), date(2030, 12, 26),
            ]


class TestTheGridIsSteppedFromTheSTOREDPHASE:
    """Plan step **C14-e-2**: extend continues the SCHEDULE's grid, not its own output.

    ``extend_pay_periods`` stepped one cadence from the owner's LAST RECORDED
    payday.  That is exact only while every recorded payday sits on the
    arithmetic grid, which is true while no convention displaces one.  From
    ``C14-e-3`` the writer records the DISPLACED day, so the anchor becomes a
    cash date and each batch re-phases the rhythm by that displacement --
    permanently, because the next extend reads THIS batch's last cash day.
    Measured over production's cadence and opening payday at a batch of one,
    the rolling top-up's steady state: **178 of 301** recorded paydays wrong
    under ``prior`` with **8 days** of final drift, against **0 of 301**
    anchored on ``budget.pay_schedule.nominal_anchor`` (ledger row **PC-497**
    fault 2, developer direction **R-PC61**).

    These cases state the convention on the schedule row -- ``C14-e-3`` ships
    the producer that reads it -- and hand the owner a displaced recorded
    payday BY HAND while leaving the stored phase where it was.  No door
    produces that pairing: every batch writes its own ``first_payday`` as the
    phase, so a record that has advanced past the anchor is the PIECEWISE
    owner ledger row **N-492** describes, and building it directly is what
    ``pay_period_write``'s own module docstring reserves the suite the right to
    do.  It is the state the grid-index search must survive, which is why
    ``nominal_payday_after`` says it does not require the anchor to place the
    day on its own grid.
    """

    def _owner_anchored_at(self, db_session, user_id, shift):
        """Record 2030-11-14 alone, which stores it as the owner's grid phase."""
        pay_period_write.record_paydays(
            user_id=user_id, first_payday=_ON_GRID_PAYDAY,
            num_periods=1, rhythm=rhythm_of(14, shift),
        )
        db_session.commit()

    def test_the_writer_co_writes_the_phase_with_the_cadence(
        self, app, db, bare_user,
    ):
        """The batch's own first payday IS the phase, and one statement writes both.

        Everything below rests on this: the anchor is DERIVED from the batch
        rather than accepted from a door, so a phase that is not on the batch's
        grid is unrepresentable rather than refused.  It is also what keeps the
        anchor meaningful when the CADENCE changes -- the two columns are
        written together or not at all, which is the property
        ``budget.pay_schedule`` can state and ledger row **N-492** is that it
        cannot state per ERA (``R-PC58``, ``C17``).
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._owner_anchored_at(db.session, user_id, BusinessDayShiftEnum.NONE)

            facts = pay_schedule_service.resolve_schedule(user_id)

            assert facts.nominal_anchor == _ON_GRID_PAYDAY
            assert facts.rhythm.cadence_days == 14

    def test_a_DISPLACED_recorded_payday_does_not_move_the_grid(
        self, app, db, bare_user,
    ):
        """The batch continues the stored grid, not the cash day it was handed.

        2030-11-14 is the stored phase.  The owner's next nominal payday,
        2030-11-28, is Thanksgiving, so under ``prior`` payroll pays 11-27 and
        ``C14-e-3``'s writer records that.  Anchored on the RECORD the next
        payday would be ``11-27 + 14 = 2030-12-11``, which is off the grid and
        would take every later payday with it.  Anchored on the stored phase it
        is 2030-12-12 -- an ordinary Thursday, and the day payroll really pays.

        **The case is the discriminator rather than an example**: the two
        answers differ by exactly the displacement, and 12-11 against 12-12 is
        what the whole column buys.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._owner_anchored_at(db.session, user_id, BusinessDayShiftEnum.PRIOR)
            # The displaced cash day this step's writer records, added
            # directly so the STORED PHASE stays at 2030-11-14 -- a door would
            # advance it to this batch's own first payday, and the anchor
            # standing still while the record moves is the discriminator.
            db.session.add(
                PayPeriod(user_id=user_id, start_date=date(2030, 11, 27)),
            )
            db.session.commit()

            new_periods = pay_period_admin.extend_pay_periods(user_id, 2)
            db.session.commit()

            assert [p.start_date for p in new_periods] == [
                date(2030, 12, 12), date(2030, 12, 26),
            ]

    def test_it_skips_a_grid_INDEX_the_owner_already_holds(
        self, app, db, bare_user,
    ):
        """Which index is next is a CASH question, and the search asks it.

        The estimate is the last grid day at or before the last recorded
        payday: from the 2030-11-14 phase, the recorded cash day 2030-11-27
        gives step 0 (11-14 is 13 days below it).  Steps 0 and 1 are both
        already paid -- 11-14 as itself and 11-28 as the 11-27 the owner holds
        -- so the answer is step 2, and a search that stopped at the estimate
        or at estimate + 1 would offer a payday the owner already has.

        Asserted as the STEP COUNT rather than as the date, because the date is
        the case above; what this pins is that the loop advanced twice, which
        is the arm ``C14-e-3`` makes reachable.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._owner_anchored_at(db.session, user_id, BusinessDayShiftEnum.PRIOR)
            db.session.add(
                PayPeriod(user_id=user_id, start_date=date(2030, 11, 27)),
            )
            db.session.commit()

            new_periods = pay_period_admin.extend_pay_periods(user_id, 1)
            db.session.commit()

            assert (
                new_periods[0].start_date - _ON_GRID_PAYDAY
            ).days == 28, "the search must advance TWO steps past the estimate"

    def test_a_schedule_that_states_no_phase_is_REFUSED(
        self, app, db, bare_user,
    ):
        """A NULL anchor and an empty schedule are one owner and one refusal.

        The migration backfilled every owner holding a payday and
        ``record_paydays`` writes the column on every batch, so the only rows
        left NULL hold no paydays at all -- and that owner is already refused
        for having nothing to extend.  Built here by clearing the column
        underneath a schedule that HAS paydays, which no door can do: the point
        is that the door refuses rather than inventing a phase, because an
        invented phase generates wrong paydays silently where a refusal is
        read.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._owner_anchored_at(db.session, user_id, BusinessDayShiftEnum.NONE)
            pay_schedule_service.get_schedule(user_id).nominal_anchor = None
            db.session.commit()

            with pytest.raises(ValidationError, match="Generate your first"):
                pay_period_admin.extend_pay_periods(user_id, 1)

    def test_a_PIECEWISE_owner_whose_tail_was_truncated_can_still_extend(
        self, app, db, bare_user,
    ):
        """The state an adversarial review of this step built, through real doors.

        A stored phase describes the grid of the batch that WROTE it, which is
        not always the grid the owner's surviving last payday sits on.  Three
        shipped doors reach that state together: record at one cadence, record
        again at another (*correct my cadence going forward*, which
        ``record_paydays`` permits and ledger row **N-492** records), then
        truncate back across the change so the surviving latest payday belongs
        to the FIRST era while the stored phase belongs to the second.

        Asked against the last recorded PAYDAY the door answers 2030-01-18,
        which falls inside the paycheck the owner still holds and which
        ``_reject_backward_payday`` refuses -- permanently, and on a read path
        with no handler, because ``top_up_rolling_window`` reaches this door
        from ``/grid`` and ``/dashboard``.  Asked against the paycheck's END,
        which is the floor's own subject, it answers 2030-01-25 and the write
        is accepted.  **The case is the discriminator**: the two answers differ
        and only one of them is a day this app can record.

        **2030-01-25 is EIGHT days on and not seven, and that is the intended
        behaviour rather than an arbitrary date** (adversarial review, second
        pass).  The stored grid runs through 2030-02-22 at a 7-day cadence, so
        its days near the horizon are 01-18 and 01-25; the first is below the
        floor, so the door SKIPS that slot and the owner gets one long
        paycheck.  It is bounded strictly below two cadences, always accepted,
        and :func:`~app.services.pay_calendar.derive_periods` closes it with no
        gap and no overlap -- which is what "the stored pair is the CURRENT
        era's rhythm and older rows are history" means for an owner ``C17``'s
        eras do not describe yet.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2030, 1, 3),
                num_periods=2, rhythm=rhythm_of(14),
            )
            # A second era, deliberately OFF the first grid: 2030-02-22 is 36
            # days after 2030-01-17, and 36 is not a multiple of 7.
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2030, 2, 22),
                num_periods=2, rhythm=rhythm_of(7),
            )
            db.session.commit()
            keep = next(
                period for period in calendar_for(user_id).saved()
                if period.start_date == date(2030, 1, 17)
            )
            pay_period_admin.truncate_pay_periods(user_id, keep.period_id)
            db.session.commit()

            new_periods = pay_period_admin.extend_pay_periods(user_id, 1)
            db.session.commit()

            assert [p.start_date for p in new_periods] == [date(2030, 1, 25)]

    def test_it_is_ZERO_DOLLARS_while_nothing_displaces(
        self, app, db, bare_user,
    ):
        """The shipped path: the stored phase and the record agree exactly.

        This step's own ``$0.00`` claim, and it is structural rather than a
        fact about stored data: with no displacement live every recorded payday
        IS a grid day, so stepping from the phase and stepping from the record
        name the same day.  Two extends, because the drift the column deletes
        only shows across batches.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._owner_anchored_at(db.session, user_id, BusinessDayShiftEnum.NONE)

            first = pay_period_admin.extend_pay_periods(user_id, 2)
            db.session.commit()
            second = pay_period_admin.extend_pay_periods(user_id, 2)
            db.session.commit()

            assert [
                (p.start_date - _ON_GRID_PAYDAY).days for p in first + second
            ] == [14, 28, 42, 56]


class TestPopulateFromActiveTemplates:
    """The repopulation helper fills periods with txns and transfers."""

    def test_populates_one_transaction_per_period(self, app, db, seed_user):
        """An active every-period template yields one txn per period."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            make_expense_template(db.session, seed_user)
            created = populate_in_a_fresh_pass(
                seed_user["user"].id, {p.id for p in periods},
            )
            db.session.commit()

            assert created == 3
            for period in periods:
                txns = (
                    db.session.query(Transaction)
                    .filter_by(pay_period_id=period.id)
                    .all()
                )
                assert len(txns) == 1
                assert resolved_amount(txns[0]) == Decimal("1200.00")

    def test_includes_transfer_templates(self, app, db, seed_user):
        """Active transfer templates generate transfers with both shadows.

        New periods must never silently miss a recurring transfer, so the
        helper runs the transfer engine too -- and each transfer keeps its
        two-shadow invariant.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            make_transfer_template(db.session, seed_user, savings)
            created = populate_in_a_fresh_pass(
                seed_user["user"].id, {p.id for p in periods},
            )
            db.session.commit()

            transfers = (
                db.session.query(Transfer)
                .filter_by(user_id=seed_user["user"].id)
                .all()
            )
            assert created == 3
            assert len(transfers) == 3
            for transfer in transfers:
                assert len(transfer.shadow_transactions) == 2
            assert_pay_period_invariants(db.session, seed_user["user"].id)

    def test_archived_template_generates_nothing(self, app, db, seed_user):
        """An inactive (archived) template produces no rows."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            make_expense_template(db.session, seed_user, is_active=False)
            created = populate_in_a_fresh_pass(
                seed_user["user"].id, {p.id for p in periods},
            )
            db.session.commit()
            assert created == 0

    def test_idempotent_second_run_creates_nothing(self, app, db, seed_user):
        """Re-running over already-populated periods creates nothing.

        ``OccurrenceClaims`` skips any occurrence already answered by a
        template-linked row, so a retried extend / top-up is safe.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            make_expense_template(db.session, seed_user)
            first = populate_in_a_fresh_pass(
                seed_user["user"].id, {p.id for p in periods},
            )
            db.session.commit()
            second = populate_in_a_fresh_pass(
                seed_user["user"].id, {p.id for p in periods},
            )
            db.session.commit()
            assert first == 3
            assert second == 0

    def test_no_baseline_scenario_returns_zero(self, app, bare_periods):
        """A user with no baseline scenario is a no-op (returns 0)."""
        with app.app_context():
            created = populate_in_a_fresh_pass(
                bare_periods[0].user_id, {p.id for p in bare_periods},
            )
            assert created == 0

    def test_empty_period_list_returns_zero(self, app, seed_user):
        """An empty period list short-circuits to 0."""
        with app.app_context():
            assert populate_in_a_fresh_pass(seed_user["user"].id, set()) == 0


class TestExtendPayPeriods:
    """``extend_pay_periods`` tail-appends; the ROUTE repopulates."""

    def test_appends_contiguously_after_last_period(self, app, db, seed_user):
        """New periods continue the index sequence and start the next day."""
        with app.app_context():
            existing = _future_periods(db.session, seed_user, count=3)
            last = existing[-1]
            new_periods = pay_period_admin.extend_pay_periods(
                seed_user["user"].id, num_periods=2,
            )
            db.session.commit()

            assert [derived_span(p).period_index for p in new_periods] == [
                derived_span(last).period_index + 1, derived_span(last).period_index + 2,
            ]
            assert new_periods[0].start_date == last_covered_day(last) + timedelta(days=1)
            assert_pay_period_invariants(db.session, seed_user["user"].id)

    def test_this_door_takes_no_cadence_at_all(self, app, db, seed_user):
        """Finding **P29**: the parameter is GONE, not newly honoured.

        This test asserted the opposite -- that an explicit ``cadence_days``
        beat the stored one -- and that was the defect: the extend card renders
        NO control for it, so the only way to reach it was a direct POST, and
        what it produced was 7-day paychecks beside a ``budget.pay_schedule``
        still saying 14.  ``resolve_cadence``, the derived horizon and the next
        rolling top-up then all continued at 14.

        Extend CONTINUES an existing schedule, so the cadence is not a question
        it gets to ask.  Plan step C3-b deleted the parameter, its Marshmallow
        field and the rolling top-up's redundant pass-through -- which closes
        the finding by making the state unreachable rather than by adding the
        write finding **P30** objected to.
        """
        with app.app_context():
            _future_periods(db.session, seed_user, count=2)

            with pytest.raises(TypeError):
                pay_period_admin.extend_pay_periods(
                    seed_user["user"].id, num_periods=1, cadence_days=7,
                )

            new_periods = pay_period_admin.extend_pay_periods(
                seed_user["user"].id, num_periods=1,
            )
            db.session.commit()
            assert _period_length(new_periods[0]) == 14
            assert_pay_period_invariants(db.session, seed_user["user"].id)

    def test_stored_schedule_cadence_used_when_unspecified(
        self, app, db, seed_user,
    ):
        """A persisted schedule cadence wins over the inferred one."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=2)
            pay_schedule_service.upsert_schedule(
                seed_user["user"].id, rhythm=rhythm_of(7),
                # "Correct my cadence going forward" restates the cadence and
                # NOT the phase, so the stored anchor is handed back.
                nominal_anchor=pay_schedule_service.resolve_schedule(
                    seed_user["user"].id,
                ).nominal_anchor,
            )
            db.session.commit()
            new_periods = pay_period_admin.extend_pay_periods(
                seed_user["user"].id, num_periods=1,
            )
            db.session.commit()
            assert _period_length(new_periods[0]) == 7

    # ``test_infers_cadence_for_legacy_user`` stood here until plan step
    # **C4-b-2**.  It deleted the owner's ``budget.pay_schedule`` row, left
    # their 14-day periods standing, and asserted the extend path still
    # produced 14 -- from ``resolve_cadence``'s inference off the last
    # period's stored length.  That state is what ``fk_pay_periods_schedule``
    # now forbids (ledger rows **P8** and **P35**), so the case was deleted
    # with its subject rather than reworded.  What extend actually does with
    # the cadence is unchanged and covered by
    # ``test_stored_schedule_cadence_used_when_unspecified`` directly above,
    # which is the only source there is now.

    def test_the_door_leaves_the_new_periods_EMPTY(self, app, db, seed_user):
        """The door RECORDS and stops; nothing recurring is generated.

        The other half of ruling **R-R38**, and the reason it is asserted
        rather than assumed: the door used to generate too, and a caller that
        re-couples the two here would make ``populate_new_periods`` a
        no-op-looking second run whose absence at a NEW call site nothing
        would catch.  The very next case runs both halves over the same
        fixture and finds the rows, so this one cannot pass by the template
        being unable to generate at all.
        """
        with app.app_context():
            _future_periods(db.session, seed_user, count=2)
            make_expense_template(db.session, seed_user)
            new_periods = pay_period_admin.extend_pay_periods(
                seed_user["user"].id, num_periods=2,
            )
            db.session.commit()
            for period in new_periods:
                assert (
                    db.session.query(Transaction)
                    .filter_by(pay_period_id=period.id)
                    .count()
                ) == 0, (
                    "extend_pay_periods generated a recurring row; since "
                    "R-R38 the read pass that resolves one may only be opened "
                    "above this layer, so the door must record and return"
                )

    def test_new_periods_get_recurring_rows(self, app, db, seed_user):
        """Extend + populate fills the new periods from the active templates."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=2)
            make_expense_template(db.session, seed_user)
            new_periods = _extend_andpopulate_in_a_fresh_pass(
                seed_user["user"].id, num_periods=2,
            )
            db.session.commit()
            for period in new_periods:
                txns = (
                    db.session.query(Transaction)
                    .filter_by(pay_period_id=period.id)
                    .all()
                )
                assert len(txns) == 1
                assert resolved_amount(txns[0]) == Decimal("1200.00")
            assert_pay_period_invariants(db.session, seed_user["user"].id)

    def test_archived_template_leaves_new_periods_empty(self, app, db, seed_user):
        """An archived template generates nothing into the new periods."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=2)
            make_expense_template(db.session, seed_user, is_active=False)
            new_periods = _extend_andpopulate_in_a_fresh_pass(
                seed_user["user"].id, num_periods=2,
            )
            db.session.commit()
            for period in new_periods:
                assert (
                    db.session.query(Transaction)
                    .filter_by(pay_period_id=period.id)
                    .count()
                ) == 0

    def test_empty_schedule_raises(self, app, bare_user_with_cadence):
        """Extending a user with no periods raises ValidationError.

        ``bare_user_with_cadence`` since plan step ``pay_calendar:C4-d``: an
        owner with no ``budget.pay_schedule`` row is refused by
        ``calendar_for`` before this door reaches its own empty branch, so a
        bare owner would grade that refusal instead of this message.  The
        refusal itself is graded at
        ``test_pay_period_admin.TestAnOwnerWithNoPaydaysReachesEveryDoor``.
        """
        with app.app_context():
            with pytest.raises(ValidationError, match="Generate your first"):
                pay_period_admin.extend_pay_periods(
                    bare_user_with_cadence["user"].id, num_periods=2,
                )

    def test_balances_correct_after_extend(self, app, db, seed_user):
        """Disciplines 1-3: as-of balances march correctly after extend.

        Anchor $1000 at the bootstrap period (index 0, no expense).  A
        $1200 every-period expense fills indices 1..4, so the projected
        end balance at index N is 1000 - N*1200.  Extending by 2 fills
        indices 5..6 with the same expense, so the projection continues to
        1000 - 6*1200 in the new window while the retained window is
        untouched, and the production integrity checker flags nothing.
        """
        account = seed_user["account"]
        scen = seed_user["scenario"].id
        user_id = seed_user["user"].id
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)  # idx 1..4
            make_expense_template(db.session, seed_user, amount="1200.00")
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            db.session.commit()

            # Pre-extend: 1000 - N*1200 at index N's end.
            assert seam_cash_balance_at(
                account, scen, last_covered_day(periods[3]),  # index 4
            ) == Decimal("-3800.00")  # 1000 - 4*1200
            retained = seam_cash_balance_at(
                account, scen, last_covered_day(periods[1]),  # index 2
            )
            assert retained == Decimal("-1400.00")  # 1000 - 2*1200

            # Extend by 2 -> indices 5, 6, each repopulated with the expense.
            new_periods = _extend_andpopulate_in_a_fresh_pass(user_id, num_periods=2)
            db.session.commit()

            # New window: the projection continues. Index 6 -> 1000 - 6*1200.
            assert seam_cash_balance_at(
                account, scen, last_covered_day(new_periods[-1]),  # index 6
            ) == Decimal("-6200.00")  # 1000 - 6*1200
            # Retained window is untouched by the append.
            assert seam_cash_balance_at(
                account, scen, last_covered_day(periods[1]),
            ) == retained

            # Discipline 1: structure sound.
            assert_pay_period_invariants(db.session, user_id)
            # Discipline 3: production integrity checker flags nothing.
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))
