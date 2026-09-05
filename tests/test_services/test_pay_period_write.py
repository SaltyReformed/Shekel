"""
Shekel Budget App -- Pay Period Writer Tests (plan steps C3-b and C4-c)

``pay_period_write`` is the one place in ``app/`` that changes
``budget.pay_periods``, so this is the one suite that grades what may be
written.  It came from ``test_pay_period_service.py`` when the writer left
that module; the classes carried over unchanged are the batch-shape and
refusal ones, and the classes below them are C3-b's own.

**What the writer DOES, and therefore what these tests are about:**

* **It writes one column.**  A row is one fact -- the payday -- since plan step
  ``pay_calendar:C4-c`` dropped ``end_date`` and ``period_index``.  Recording a
  payday INSERTs a row and touches no other; retiring one DELETEs it and
  touches no other (``TestAWriteTouchesNoRowItDidNotName``).  C3-b's own
  subject -- holding the two derived columns equal to
  ``pay_calendar.derive_periods`` on every write -- has no subject left, and
  the four cases that graded that machinery went with the columns.
* Every three-tuple asserted below is therefore the owner's DERIVED calendar
  (see :func:`_paydays`), which is what the writer's callers read.  The figures
  are the ones those cases were hand-computed against and they did not move;
  dropping the columns is only correct if they do not.
* The old batch guard ``_reject_overlapping_batch`` split into TWO rules, and
  only ONE survives.  The forward-only FLOOR stays, and exists only to keep plan
  step C6's mid-schedule insert closed.  The COVERAGE rule -- which refused a
  write that would take a filed row's money-day out of every paycheck -- was
  DELETED by developer ruling 2026-08-11; ``TestACoverageWithdrawalIsAccepted``
  is what grades the state it used to refuse.
* A batch that records at least one payday persists its cadence; one that
  records none leaves it alone (findings **P12**, **P29**).

Clock discipline (``.claude/rules/testing.md``): every date here is a literal
or derived from one by ``timedelta``, and nothing calls ``date.today()``, so
these pass identically under ``TZ=Pacific/Kiritimati``.
"""

import logging
import pathlib
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app import ref_cache
from app.enums import BusinessDayShiftEnum, StatusEnum, TxnTypeEnum
from app.models.pay_period import PayPeriod
from app.models.pay_schedule import CADENCE_DAYS_MIN, PaySchedule
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    pay_period_admin,
    pay_period_rolling,
    pay_period_write,
    pay_schedule_service,
    transfer_service,
)
from app.schemas.validation import PayPeriodGenerateSchema
from app.services.pay_calendar import calendar_for
from tests._test_helpers import (
    rhythm_of,
    add_txn,
    all_periods,
    capture_sql_statements,
    create_savings_account,
    displace_paydays_under,
    freeze_today,
)


#: Pinned "today", before every date this module writes.  The lock classifier
#: calls a period whose last covered day is in the past HISTORICAL and refuses
#: to delete it, so the truncate tests below would refuse on the wall clock
#: rather than on what they are grading.
FROZEN_TODAY = date(2025, 12, 1)


@pytest.fixture(autouse=True)
def _freeze(monkeypatch):
    """Pin ``date.today()`` for every test in this module."""
    freeze_today(monkeypatch, FROZEN_TODAY)


#: The repository root, derived from THIS file rather than from the working
#: directory: ``TestThereIsOneWriter`` walks ``app/`` on disk, and a relative
#: path would make that census silently empty -- and so vacuously green -- for
#: any invocation whose cwd is not the checkout root.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _paydays(user_id):
    """Return the owner's ``(payday, last covered day, ordinal)`` rows in order.

    **The last two are DERIVED and were columns until plan step
    ``pay_calendar:C4-c``.**  Every assertion in this module that names a
    three-tuple therefore says what the owner's CALENDAR looks like after the
    write under test, which is what the writer's callers actually read: the
    writer stores a payday and ``pay_calendar.derive_periods`` answers the
    rest.  The tuple shape is unchanged, deliberately -- the figures these
    cases were hand-computed against did not move, and dropping the columns is
    only correct if they do not.
    """
    return [
        (period.start_date, period.end_date, period.period_index)
        for period in calendar_for(user_id).saved()
    ]


class TestItRecordsPaydays:
    """The batch shape: which paydays a call records, and what the calendar says."""

    def test_it_records_the_requested_paydays(self, app, db, bare_user):
        """Five paydays a fortnight apart, in the order they were asked for."""
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=bare_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=5,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            assert [period.start_date for period in periods] == [
                date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
                date(2026, 2, 13), date(2026, 2, 27),
            ]

    def test_the_calendar_the_batch_leaves_runs_0_to_n_minus_1(
        self, app, db, bare_user,
    ):
        """The owner's whole calendar, end to end through the writer.

        The ordinal is the payday's position in the owner's sorted set and the
        end is the day before the next payday, so index order and date order
        cannot disagree -- there is no second value.  Before plan step C3-b the
        writer ASSIGNED ``max_index + 1`` and ``start + cadence - 1``; plan step
        ``pay_calendar:C4-c`` deleted both columns, so what the writer leaves
        behind is a payday set and this is what it derives to.
        """
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=bare_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=3,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            # Each end is the day before the next payday; the LAST is the one
            # projection, 2026-01-30 + 13.
            assert _paydays(bare_user["user"].id) == [
                (date(2026, 1, 2), date(2026, 1, 15), 0),
                (date(2026, 1, 16), date(2026, 1, 29), 1),
                (date(2026, 1, 30), date(2026, 2, 12), 2),
            ]

    def test_an_existing_payday_is_skipped_rather_than_duplicated(
        self, app, db, bare_user,
    ):
        """A re-run naming days already on the table records only the new ones."""
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            db.session.commit()

            again = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=4, rhythm=rhythm_of(14),
            )
            db.session.commit()

            assert [p.start_date for p in again] == [
                date(2026, 1, 30), date(2026, 2, 13),
            ]
            assert len(all_periods(user_id)) == 4
            assert [row[2] for row in _paydays(user_id)] == [0, 1, 2, 3]

    def test_a_second_batch_appends_and_keeps_payday_order(
        self, app, db, bare_user,
    ):
        """Appending after the current coverage extends the schedule forward."""
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=3, rhythm=rhythm_of(14),
            )
            db.session.commit()

            new = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 13),
                num_periods=2, rhythm=rhythm_of(14),
            )
            db.session.commit()

            assert [p.start_date for p in new] == [
                date(2026, 2, 13), date(2026, 2, 27),
            ]
            starts = [row[0] for row in _paydays(user_id)]
            assert starts == sorted(starts)
            assert [row[2] for row in _paydays(user_id)] == [0, 1, 2, 3, 4]

    def test_num_periods_one_records_one_payday(self, app, db, bare_user):
        """The smallest legal batch."""
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=bare_user["user"].id, first_payday=date(2026, 1, 2),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()

            assert len(periods) == 1
            assert periods[0].start_date == date(2026, 1, 2)
            assert _paydays(bare_user["user"].id) == [
                (date(2026, 1, 2), date(2026, 1, 15), 0),
            ]

    def test_a_large_batch_stays_correct_at_scale(self, app, db, bare_user):
        """104 periods -- two years of fortnights, the production shape."""
        with app.app_context():
            start = date(2026, 1, 2)
            periods = pay_period_write.record_paydays(
                user_id=bare_user["user"].id, first_payday=start,
                num_periods=104, rhythm=rhythm_of(14),
            )
            db.session.commit()

            assert len(periods) == 104
            assert periods[-1].start_date == start + timedelta(days=103 * 14)
            derived = _paydays(bare_user["user"].id)
            assert [row[2] for row in derived] == list(range(104))
            for payday, last_day, _ordinal in derived:
                assert last_day == payday + timedelta(days=13)

    def test_a_non_date_payday_is_refused(self, app, db, bare_user):
        """A string, and a ``datetime``, are both refused as form errors.

        ``datetime`` is a ``date`` SUBCLASS, so the ``isinstance`` check this
        replaced accepted one and every derived end silently carried a time
        component -- comparing unequal to the ``DATE`` column it is stored in.
        ``pay_calendar._validated`` refuses the same value, but as a
        ``PayCalendarError`` no route catches; refusing here makes it the 422
        it actually is.
        """
        with app.app_context():
            for bad in ("2026-01-02", __import__("datetime").datetime(2026, 1, 2)):
                with pytest.raises(ValidationError, match="must be a date"):
                    pay_period_write.record_paydays(
                        user_id=bare_user["user"].id, first_payday=bad,
                        num_periods=1, rhythm=rhythm_of(14),
                    )
            db.session.rollback()


class TestTheForwardOnlyFloor:
    """Ruling **R-PC1**'s structural half: no payday between two existing ones.

    It replaced ``_reject_overlapping_batch``, which bounded a batch on
    ``max(end_date)`` -- a column plan step C4-c dropped -- and which was doing
    this job by accident.  **The only thing left to refuse is a payday landing
    inside a paycheck the owner already has**, because a gap and an overlap
    stopped being expressible once the columns are derived.  Plan step C6 owns
    that insert, behind ledger row **P10**'s two unruled questions, and C6 is
    what deletes this rule.

    **The floor is ONE CADENCE, not two days, and this class carries the
    control for the correction.**  C3-b's first cut bounded at
    ``latest_payday + 2`` (the writer's cadence floor, itself deleted at plan
    step C4-c), which is a paycheck too low: the LAST period runs to ``latest_payday + cadence - 1``, so every
    day between the two split it -- reachable, and P10 says it is not.
    """

    def _two_fortnightly_periods(self, user_id):
        """Record 2026-01-02 and 2026-01-16; coverage runs to 2026-01-29."""
        return pay_period_write.record_paydays(
            user_id=user_id, first_payday=date(2026, 1, 2),
            num_periods=2, rhythm=rhythm_of(14),
        )

    def test_a_payday_between_two_existing_ones_is_refused(
        self, app, db, bare_user,
    ):
        """2026-01-09 splits the first paycheck in half; refused, nothing written."""
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_fortnightly_periods(user_id)
            db.session.commit()

            with pytest.raises(ValidationError, match="on or after 2026-01-30"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 1, 9),
                    num_periods=4, rhythm=rhythm_of(14),
                )
            db.session.rollback()
            assert len(all_periods(user_id)) == 2

    def test_the_floor_is_one_CADENCE_after_the_latest_payday(
        self, app, db, bare_user,
    ):
        """The day before the next cadence payday refuses; that day is accepted.

        Paydays 2026-01-02 and 2026-01-16 at cadence 14: the last paycheck runs
        to 2026-01-29, so the floor is 2026-01-30.  2026-01-29 is refused --
        it would split that paycheck -- and 2026-01-30 goes through, which is
        the schedule simply continuing.

        The accepted case is the control: without it a rule that refused
        everything would pass the first half.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_fortnightly_periods(user_id)
            db.session.commit()

            with pytest.raises(ValidationError, match="on or after 2026-01-30"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 1, 29),
                    num_periods=1, rhythm=rhythm_of(14),
                )
            db.session.rollback()

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 30),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()
            assert _paydays(user_id) == [
                (date(2026, 1, 2), date(2026, 1, 15), 0),
                (date(2026, 1, 16), date(2026, 1, 29), 1),
                (date(2026, 1, 30), date(2026, 2, 12), 2),
            ]

    def test_a_payday_inside_the_last_periods_span_is_REFUSED(
        self, app, db, bare_user,
    ):
        """The regression C3-b's first cut shipped, and its control.

        With a floor of ``latest_payday + 2`` this was ACCEPTED, and an
        adversarial review measured what it did: recording 2026-01-23 shrank
        the 2026-01-16 paycheck from 01-29 to 01-22, moved a row due 01-25 from
        rendering on 01-25 to rendering on 01-22, and split one paycheck in two
        -- left EMPTY by ``/pay-periods/generate`` (P10's "income understated")
        and repopulated by ``regenerate`` beside the row the shrunk half kept
        (P10's "a monthly billed twice").  Ledger row **P10** says that state is
        not constructible and plan step **C6** owns making it so.

        The submitted cadence is 7 deliberately: the floor reads the cadence the
        owner's last paycheck ALREADY runs at, not the one this batch proposes,
        so a shorter one cannot buy its way in.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_fortnightly_periods(user_id)
            db.session.commit()

            with pytest.raises(ValidationError, match="on or after 2026-01-30"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 1, 23),
                    num_periods=2, rhythm=rhythm_of(7),
                )
            db.session.rollback()

            assert _paydays(user_id) == [
                (date(2026, 1, 2), date(2026, 1, 15), 0),
                (date(2026, 1, 16), date(2026, 1, 29), 1),
            ]

    def test_the_cadence_may_still_be_SHORTENED_going_forward(
        self, app, db, bare_user,
    ):
        """What the floor costs, and what it does not.

        An owner moving from fortnightly to weekly records their next real
        payday one FORTNIGHT out -- the paycheck they are in has not ended --
        and the schedule then continues at a week.  So the "correct my cadence"
        case the implementation plan's section 6 names survives; what is refused
        is only doing it retroactively, inside a paycheck that already exists.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_fortnightly_periods(user_id)
            db.session.commit()

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 30),
                num_periods=3, rhythm=rhythm_of(7),
            )
            db.session.commit()

            assert _paydays(user_id) == [
                (date(2026, 1, 2), date(2026, 1, 15), 0),
                (date(2026, 1, 16), date(2026, 1, 29), 1),
                (date(2026, 1, 30), date(2026, 2, 5), 2),
                (date(2026, 2, 6), date(2026, 2, 12), 3),
                (date(2026, 2, 13), date(2026, 2, 19), 4),
            ]
            assert pay_schedule_service.resolve_cadence(user_id) == 7

    def test_a_first_batch_has_no_floor(self, app, db, bare_user):
        """An owner with no paydays can open a schedule on any day."""
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=bare_user["user"].id, first_payday=date(2020, 3, 1),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()
            assert periods[0].start_date == date(2020, 3, 1)


#: What the floor becomes for each convention, on the schedule
#: :class:`TestTheFloorFollowsTheProducer` records.
#:
#: The last recorded payday is 2025-12-18, an ordinary Thursday, and the
#: nominal next one is 2026-01-01 -- New Year's Day, which is closed.  So the
#: three conventions put the next paycheck's opening day in three different
#: places, and the floor is that day in each: **the R-PC47 case, worked**.
#: Hand-computed from the federal holiday set, not read back from
#: :func:`~app.utils.business_days.shift_to_business_day`, so this table is an
#: independent oracle rather than the mechanism restated.
_FLOOR_PER_CONVENTION = [
    (BusinessDayShiftEnum.NONE, date(2026, 1, 1)),
    (BusinessDayShiftEnum.PRIOR, date(2025, 12, 31)),
    (BusinessDayShiftEnum.NEXT, date(2026, 1, 2)),
]


class TestTheFloorFollowsTheProducer:
    """Plan step **C14-d**: the floor is where the last paycheck ENDS.

    ``_reject_backward_payday`` open-coded ``latest_payday + cadence_days`` and
    a sentence in its own docstring held that equal to the last saved period's
    derived end -- rule 14's tell, one value with two homes and a maintenance
    contract.  It calls
    :func:`~app.services.pay_calendar.projected_payday` now, the same producer
    :func:`~app.services.pay_calendar.derive_periods` closes that period with,
    so the fence and the boundary it guards cannot come apart.

    **These cases are driven through the substitution plan step ``C14-e``
    ships** (:func:`~tests._test_helpers.displace_paydays_under`), because
    while the convention is ``none`` the two spellings agree on every day and a
    test of the real producer would grade the deleted one just as well.  That
    is not hypothetical: an adversarial review of ``C14-c`` measured its
    sibling equality passing with the OLD end rule restored.

    The ``none`` row of :data:`_FLOOR_PER_CONVENTION` is the WITHIN-simulation
    control: it fixes the floor the displacing rows are read against, so their
    two answers are the only ones that moved.  **It is not the ``$0.00``
    claim**, and an adversarial review of this step struck a sentence saying it
    was -- ``none`` still substitutes an identity DOUBLE, so the shipped
    producer is not called on that arm either.  The unpatched controls are
    :meth:`TestTheForwardOnlyFloor.test_the_floor_is_one_CADENCE_after_the_latest_payday`,
    hand-computed at 2026-01-30 and untouched by this step, and the extend
    suite's ``test_it_is_ZERO_DOLLARS_while_the_convention_displaces_nothing``,
    which runs with no substitution at all.
    """

    def _fortnightly_through_december(self, user_id, shift):
        """Record 2025-12-04 and 2025-12-18 UNDER *shift*; the second is last.

        **The convention is stored, and an adversarial review of this step is
        why.**  A first form seeded every arm under ``none`` and argued that a
        payday is a RECORDED FACT (**R-PC47**) so the rows are the same either
        way.  The rows ARE the same -- ``_requested_paydays`` is shift-blind,
        so this records 2025-12-04 and 2025-12-18 whatever is passed -- which
        makes that a non-reason rather than a justification.  What differed was
        the SCHEDULE ROW: it said ``none`` while the case simulated a
        displacing convention, and the shipped ``C14-e`` producer reads that
        row (``pay_schedule_service.resolve_shift`` today, a widened
        ``ScheduleFacts`` after it).  So the arm pinned a world ``C14-e`` could
        not reproduce for this owner: the real producer would have answered the
        nominal day and every displacing assertion here would fail.
        """
        return pay_period_write.record_paydays(
            user_id=user_id, first_payday=date(2025, 12, 4),
            num_periods=2, rhythm=rhythm_of(14, shift),
        )

    @pytest.mark.parametrize(
        ("shift", "floor"), _FLOOR_PER_CONVENTION,
        ids=lambda value: getattr(value, "name", str(value)).lower(),
    )
    def test_the_day_below_the_floor_is_refused_and_the_floor_is_taken(
        self, app, db, bare_user, monkeypatch, shift, floor,
    ):
        """Both sides of the boundary, wherever the convention puts it.

        Asserting the refusal alone would pass for a rule that refused
        everything, and asserting the acceptance alone for one that refused
        nothing; the pair pins the floor to a day.  Across the three arms that
        day MOVES IN BOTH DIRECTIONS -- ``prior`` pulls it back to 2025-12-31
        and ``next`` pushes it out to 2026-01-02 -- so a floor that ignored the
        producer and kept answering 2026-01-01 fails two of the three.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._fortnightly_through_december(user_id, shift)
            db.session.commit()
            displace_paydays_under(monkeypatch, shift)

            below = floor - timedelta(days=1)
            with pytest.raises(
                ValidationError, match=f"on or after {floor.isoformat()}",
            ):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=below,
                    num_periods=1, rhythm=rhythm_of(14, shift),
                )
            db.session.rollback()
            assert len(all_periods(user_id)) == 2

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=floor,
                num_periods=1, rhythm=rhythm_of(14, shift),
            )
            db.session.commit()
            assert [period.start_date for period in all_periods(user_id)] == [
                date(2025, 12, 4), date(2025, 12, 18), floor,
            ]

    def test_the_accepted_payday_is_the_one_the_LAST_PAYCHECK_ends_before(
        self, app, db, bare_user, monkeypatch,
    ):
        """The floor and the derived end are ONE value, shown as a calendar.

        Under ``prior`` the 2025-12-18 paycheck runs to 2025-12-30, because the
        payday that closes it is the 2026-01-01 payroll really pays on
        2025-12-31.  The floor accepts exactly 2025-12-31, so the recorded
        payday opens the day after that end and the calendar tiles -- which is
        the property the open-coded floor could only promise.  Its own
        ``latest + cadence`` would have refused this write outright, leaving
        the owner unable to record a payday they were really paid.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._fortnightly_through_december(
                user_id, BusinessDayShiftEnum.PRIOR,
            )
            db.session.commit()
            displace_paydays_under(monkeypatch, BusinessDayShiftEnum.PRIOR)

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2025, 12, 31),
                num_periods=1, rhythm=rhythm_of(14, BusinessDayShiftEnum.PRIOR),
            )
            db.session.commit()

            assert _paydays(user_id) == [
                (date(2025, 12, 4), date(2025, 12, 17), 0),
                (date(2025, 12, 18), date(2025, 12, 30), 1),
                (date(2025, 12, 31), date(2026, 1, 13), 2),
            ]

    def test_the_floor_follows_a_RETIRED_tail_down(
        self, app, db, bare_user, monkeypatch,
    ):
        """The floor reads what the operation LEAVES, not what the table holds.

        ``record_paydays`` takes the retirements and the recordings in ONE call
        so every refusal judges the payday set the operation actually leaves
        behind, and the floor is the case that most depends on it: retire
        2025-12-18 and the last surviving payday becomes 2025-12-04, so the
        floor drops a whole cadence.  Added by an adversarial review of this
        step, which named this the one path where ``max(surviving_paydays)``
        and *the payday the derivation closes the calendar on* could come apart
        -- the equality the new floor claims cannot.

        Under ``prior`` the floor is then the day 2025-12-18's own payroll
        would really have landed on, which is 2025-12-18 itself: an ordinary
        Thursday, so the displacement is the identity and the arithmetic is
        visible.  2025-12-17 splits the surviving paycheck and is refused.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            created = self._fortnightly_through_december(
                user_id, BusinessDayShiftEnum.PRIOR,
            )
            db.session.commit()
            doomed = {created[-1].id}
            displace_paydays_under(monkeypatch, BusinessDayShiftEnum.PRIOR)

            with pytest.raises(ValidationError, match="on or after 2025-12-18"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2025, 12, 17),
                    num_periods=1,
                    rhythm=rhythm_of(14, BusinessDayShiftEnum.PRIOR),
                    retiring_ids=doomed,
                )
            db.session.rollback()

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2025, 12, 18),
                num_periods=1,
                rhythm=rhythm_of(14, BusinessDayShiftEnum.PRIOR),
                retiring_ids=doomed,
            )
            db.session.commit()
            assert [period.start_date for period in all_periods(user_id)] == [
                date(2025, 12, 4), date(2025, 12, 18),
            ]

    def test_a_displaced_ANCHOR_still_refuses_a_real_payday_and_that_is_N_495(
        self, app, db, bare_user, monkeypatch,
    ):
        """What asking the producer does NOT fix, pinned so it cannot be missed.

        :func:`~app.services.pay_calendar.projected_payday` steps from the last
        RECORDED payday, and **R-PC47** says a recorded payday may itself have
        been moved.  Under ``next`` the 2030-11-28 payroll is really paid
        2030-11-29, so a schedule whose last row is that Friday projects its
        next payday from the Friday: the floor lands on 2030-12-13 while the
        real payday is 2030-12-12, and the write is refused.

        Measured across production's own rhythm -- 1,951 paydays from
        2026-03-26 at cadence 14 out to ``CALENDAR_DATE_MAX``, probed
        2026-09-05 -- the open-coded floor refuses **58** of them under either
        convention; asking the producer takes ``prior`` to **0** and leaves
        ``next`` at those same 58, every one of them anchored on a payday that
        was itself displaced.  That residue is ledger row **N-495**, owner
        ``C14-e``, and this case is here so a later reader does not read the
        step's own ``prior`` result as the whole of it.

        **The displacement below is INERT and the case is not, which an
        adversarial review of this step made explicit.**  2030-12-13 is a
        Friday and an ordinary business day, so the floor is that day with the
        substitution, without it, and under the deleted expression alike -- the
        substitution is here to state the world, not to produce the refusal.
        What produces it is the ANCHOR: 2030-11-29 is not on the owner's grid,
        so ``latest + cadence`` lands a day past the real payday however the
        floor is spelled.  That is precisely why N-495 is an anchor repair and
        not a floor repair, and it is why this case survived the mutation that
        killed the three above it.

        It also states the direction: the floor is now wrong exactly where the
        DERIVED END is wrong, and no longer anywhere else.  The 2030-11-29
        paycheck really is derived as running through 2030-12-12, so refusing a
        payday on that day is this fence agreeing with the calendar rather than
        contradicting it -- which is why the repair belongs to the anchor and
        not here.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2030, 11, 29),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()
            displace_paydays_under(monkeypatch, BusinessDayShiftEnum.NEXT)

            with pytest.raises(ValidationError, match="on or after 2030-12-13"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2030, 12, 12),
                    num_periods=1,
                    rhythm=rhythm_of(14, BusinessDayShiftEnum.NEXT),
                )
            db.session.rollback()
            assert len(all_periods(user_id)) == 1


class TestAWriteTouchesNoRowItDidNotName:
    """Recording a payday INSERTs one row; retiring one DELETEs it.  Nothing else.

    **This class replaced ``TestItMaterialisesTheWholeDerivation`` at plan step
    ``pay_calendar:C4-c``, and the replacement is that step in one sentence.**
    While ``end_date`` and ``period_index`` were columns, every write had to
    re-materialise the owner's WHOLE calendar -- a cache refreshed only at its
    edge is a second source of truth, so an interior hole would survive every
    forward append.  Four cases graded that machinery: a hole repaired by the
    next batch, an INTERIOR hole repaired by an append at the far end (finding
    **N-127**), the WARNING the repair logs, and the delete that re-projects
    the newly-last end.  None of them can be written now: opening a hole meant
    writing a stored end BELOW the next payday, and there is no stored end.

    What replaces them is the property the deletion BUYS, which is stronger
    than any of them and is not vacuous: a write to ``budget.pay_periods``
    issues no ``UPDATE`` at all, so no row the operation did not name can move.
    Graded as a statement census rather than argued.
    """

    def test_recording_a_payday_issues_no_UPDATE(self, app, db, bare_user):
        """An append writes INSERTs, and touches no existing row.

        The whole-calendar rewrite this replaced emitted an ``UPDATE`` per row
        whose stored columns disagreed with the derivation.  With nothing
        derived stored there is nothing to rewrite, and a future writer that
        started mutating a period in place -- the shape that would re-open
        finding **P1** -- fails here.

        Deliberately an OFF-cadence append: on-cadence, a writer that still
        recomputed a stored end would coincidentally write the same value and
        emit no statement, so the census would pass on the defect.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=3, rhythm=rhythm_of(14),
            )
            db.session.commit()

            created, statements = capture_sql_statements(
                lambda: pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 2, 20),
                    num_periods=1, rhythm=rhythm_of(7),
                ),
            )
            db.session.commit()

            # **The positive control, and an adversarial review of this step is
            # why it is here** (2026-09-01).  ``updates == []`` is satisfied by
            # a write that did NOTHING -- a batch that recorded no payday, or a
            # capture whose listener never fired -- so the census had to say
            # first that there was a write to census.
            assert len(created) == 1
            inserts = [
                text for text, _params in statements
                if text.lstrip().upper().startswith("INSERT INTO BUDGET.PAY_PERIODS")
            ]
            assert len(inserts) == 1, statements

            updates = [
                text for text, _params in statements
                if text.lstrip().upper().startswith("UPDATE BUDGET.PAY_PERIODS")
            ]
            assert updates == []

    def test_retiring_a_payday_issues_no_UPDATE(self, app, db, bare_user):
        """A truncate deletes rows, and touches no survivor.

        The other direction, and it is the one the deleted machinery worked
        hardest on: a delete used to leave the newly-last survivor a stored end
        its own schedule no longer justified, so ``retire_paydays`` re-derived
        what survived.  The survivor's end is derived on every read now, so the
        delete is a delete.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=4, rhythm=rhythm_of(14),
            )
            db.session.commit()

            deleted, statements = capture_sql_statements(
                lambda: pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=created[1].id,
                ),
            )
            db.session.commit()

            # The positive control, for the reason its sibling above states:
            # a truncate that deleted nothing emits no DELETE and no UPDATE,
            # and would pass an ``updates == []`` census on its own.
            assert deleted == 2
            deletes = [
                text for text, _params in statements
                if text.lstrip().upper().startswith("DELETE FROM BUDGET.PAY_PERIODS")
            ]
            assert len(deletes) == 1, statements

            updates = [
                text for text, _params in statements
                if text.lstrip().upper().startswith("UPDATE BUDGET.PAY_PERIODS")
            ]
            assert updates == []

    def test_an_existing_period_keeps_its_id_and_its_transactions(
        self, app, db, seed_user, seed_periods,
    ):
        """Identity survives a later write, which is what the FKs rest on.

        ``budget.pay_periods.id`` is what three inbound foreign keys point at,
        so a writer that recreated rows rather than appending beside them would
        strand a transaction quietly.  Nothing here rewrites a row today; this
        is the assertion that says so from the outside.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Rent", "1200.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()
            period_id, txn_id = seed_periods[0].id, txn.id

            pay_period_admin.extend_pay_periods(user_id, 1)
            db.session.commit()

            assert db.session.get(PayPeriod, period_id) is not None
            assert db.session.get(
                Transaction, txn_id,
            ).pay_period_id == period_id


class TestTheDerivedHorizonFollowsTheStoredCadence:
    """The LAST period's end is a projection, and it reads one number.

    Every other end is dictated by the next payday and is a fact.  The last has
    no successor, so it is ``start_date + cadence_days - 1`` read from
    ``budget.pay_schedule`` -- which is why finding **P28** ("the horizon the
    app projects" disagreeing with "the end stored on the last row") has no
    subject since plan step ``pay_calendar:C4-c``: there is one value and no
    column for it to come apart from.
    """

    def test_truncate_reprojects_the_new_last_end(self, app, db, bare_user):
        """Deleting the tail moves the survivor's end from a fact to a projection.

        Paydays ``[2026-01-02, 2026-01-16, 2026-02-11]``: the January 16
        paycheck runs to 2026-02-10 because its successor opens then.  Truncate
        that successor away and it is the LAST period, so its end falls back to
        the cadence projection, 2026-01-29.  An on-cadence fixture cannot see
        this at all, because ``lead(start) - 1`` and ``start + cadence - 1``
        coincide there.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 11),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()
            assert _paydays(user_id)[1] == (
                date(2026, 1, 16), date(2026, 2, 10), 1,
            )

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=created[1].id,
            )
            db.session.commit()

            assert deleted == 1
            assert _paydays(user_id) == [
                (date(2026, 1, 2), date(2026, 1, 15), 0),
                (date(2026, 1, 16), date(2026, 1, 29), 1),
            ]

    def test_the_last_end_follows_the_STORED_cadence(self, app, db, bare_user):
        """Finding **P28** closed at the root: one cadence, not two.

        Here the batch records nothing new -- every payday already exists -- so
        the cadence rule does NOT fire, and the stored 14 is what the horizon
        keeps even though the call named 30.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            db.session.commit()

            # ONE payday, and it already exists -- the reachable shape
            # finding P12 names.  Two would step by 30 and create a new one.
            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=1, rhythm=rhythm_of(30),
            )
            db.session.commit()

            assert created == []
            assert pay_schedule_service.resolve_cadence(user_id) == 14
            assert _paydays(user_id)[-1] == (
                date(2026, 1, 16), date(2026, 1, 29), 1,
            )


class TestACoverageWithdrawalIsAccepted:
    """Shortening the schedule past a settled row's cash day is ALLOWED.

    **Ruling R-PC1's financial half was DELETED (developer, 2026-08-11), and
    this class is what replaced it.**  It refused any write that moved a day
    from COVERED to UNCOVERED underneath a SETTLED row filed in a surviving
    period, on the claim that stranding such a day reproduces ``balance:N-128``
    -- the two halves of the cash period view disagreeing.  The claim was false.

    ``_cash_periods._assemble_figures`` values each column at that period's OWN
    ``end_date`` and computes ``period_timing`` as ``moved - net``, so a settle
    day past the last reported end is absent from BOTH sides of ruling R-K's
    identity and cancels.  The money reports as a timing remainder -- the row
    ruling R-DH split out to carry exactly this -- and the balance is right
    either way: on the shortened schedule's last day the bank genuinely had not
    taken the money yet.  ``test_cash_period_view.py``'s
    ``test_a_settle_day_past_the_window_keeps_every_column_exact`` is the
    arithmetic; these are the doors.

    **Production is SILENT on this rule rather than supporting its removal**, and
    an adversarial review had to point that out: **0** of its settled rows fall
    outside the schedule's coverage, so the refused state has never occurred
    there.  What production shows is the design the deletion rests on -- 21 of
    160 settled rows settle outside their OWN paycheck (2026-08-11), carried by
    the remainder with nothing refusing.  What decided it is the measured COST:
    5 of the owner's 61 truncation points refused, one over three rows totalling
    ``$177.47`` that cleared the bank ONE day late.  Its message offered three
    ways out -- re-date the row, move it, or abandon the schedule change -- the
    first two of which falsify when money moved.
    """

    #: ``seed_periods``' last paycheck: payday 2026-05-08, covering to
    #: 2026-05-21 (10 fortnightly periods from 2026-01-02).  Named so the
    #: arithmetic below reads as dates rather than as indices.
    _LAST_PAYDAY = date(2026, 5, 8)
    _LAST_COVERED = date(2026, 5, 21)

    def _settled_row(self, db_session, seed_user, period, day):
        """File a SETTLED row in *period* whose money moved on *day*."""
        return add_txn(
            db_session, seed_user, period, "Insurance renewal", "340.00",
            status_enum=StatusEnum.DONE, due_date=period.start_date,
            settled_on=day,
        )

    def test_an_append_past_a_late_row_is_ACCEPTED(
        self, app, db, seed_user, seed_periods,
    ):
        """The false refusal R-PC1's first wording would have produced.

        The last paycheck ends 2026-05-21 and holds a row that SETTLED
        2026-05-30 -- a state production reaches routinely (7 of its 61
        paychecks hold a row 1 to 17 days past their own end, and the last
        one's margin is zero days).  Appending 2026-05-22 leaves that end
        exactly where it was, so the row's situation is unchanged and the write
        is allowed.  "Refuse any row dated on or after the new payday" would
        have refused it -- and since the rolling top-up appends with no handler
        on ``/grid`` and ``/dashboard``, that refusal was a permanent 500 on
        both pages.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            self._settled_row(
                db.session, seed_user, seed_periods[-1], date(2026, 5, 30),
            )
            db.session.commit()
            assert _paydays(user_id)[-1] == (
                self._LAST_PAYDAY, self._LAST_COVERED, 9,
            )

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 5, 22),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()

            rows = _paydays(user_id)
            assert rows[-2] == (self._LAST_PAYDAY, self._LAST_COVERED, 9)
            assert rows[-1] == (date(2026, 5, 22), date(2026, 6, 4), 10)

    def test_a_truncate_that_strands_a_settled_row_is_ACCEPTED(
        self, app, db, seed_user, seed_periods,
    ):
        """The case the deleted rule refused, and it must now go through.

        A payday recorded at 2026-07-01 stretches the 2026-05-08 paycheck to
        2026-06-30, and it holds a row that SETTLED 2026-06-15.  Truncating
        that successor away drops the paycheck back to its cadence projection,
        2026-05-21, so 2026-06-15 belongs to no paycheck afterwards.

        The truncate runs.  The row is NOT touched -- it keeps its period, its
        settle day and its existence -- because the schedule shrinking is not a
        fact about when the bank moved money, and re-dating it to satisfy a
        calendar edit would falsify the one clock the ledger reads.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            row = self._settled_row(
                db.session, seed_user, seed_periods[-1], date(2026, 6, 15),
            )
            row_id, home_period_id = row.id, seed_periods[-1].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 7, 1),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()
            assert _paydays(user_id)[-2] == (
                self._LAST_PAYDAY, date(2026, 6, 30), 9,
            )

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=seed_periods[-1].id,
                confirm_discard=True,
            )
            db.session.commit()

            assert deleted == 1
            assert _paydays(user_id)[-1] == (
                self._LAST_PAYDAY, self._LAST_COVERED, 9,
            )
            survivor = db.session.get(Transaction, row_id)
            assert survivor is not None
            assert survivor.pay_period_id == home_period_id
            # The settle day is now outside every paycheck, and untouched.
            assert survivor.settled_on == date(2026, 6, 15)

    def test_a_truncate_that_strands_NOTHING_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):
        """The control, and the point is that it reads IDENTICALLY to the above.

        Same truncate, and the row settled 2026-05-20 -- a day the shortened
        paycheck still covers.  It was the accepted half of a pair the rule
        split; it is now simply the same outcome, which is what "the writer has
        no opinion about a settled row's cash day" means.  Kept rather than
        merged so a future rule that starts discriminating on that day again
        fails one of the two.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            self._settled_row(
                db.session, seed_user, seed_periods[-1], date(2026, 5, 20),
            )
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 7, 1),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=seed_periods[-1].id,
                confirm_discard=True,
            )
            db.session.commit()

            assert deleted == 1
            assert _paydays(user_id)[-1] == (
                self._LAST_PAYDAY, self._LAST_COVERED, 9,
            )

    def test_a_stranded_TRANSFER_keeps_both_shadows_and_its_settle_day(
        self, app, db, seed_user, seed_periods,
    ):
        """The transfer invariants survive a truncate that strands the pair.

        A transfer has no ``settled_on`` COLUMN -- the day lives on its two
        shadow ``Transaction`` rows (Transfer Invariant 3) -- so a shortening
        that puts that day outside every paycheck touches BOTH shadows at once.
        Graded here rather than left to the transaction case because the failure
        mode is different: a writer that "repaired" a stranded row by moving or
        deleting it would break Invariants 1 and 2 (exactly two shadows, never
        orphaned) on this shape while looking correct on the other.  The writer
        touches neither, which is what makes that unreachable.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            xfer = transfer_service.create_transfer(transfer_service.TransferSpec(
                user_id=user_id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=seed_periods[-1].id,
                scenario_id=seed_user["scenario"].id,
                amount=Decimal("150.00"),
                status_id=ref_cache.status_id(StatusEnum.DONE),
                category_id=None,
            ))
            xfer_id = xfer.id
            db.session.query(Transaction).filter_by(
                transfer_id=xfer_id,
            ).update({"settled_on": date(2026, 6, 15)}, synchronize_session=False)
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 7, 1),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=seed_periods[-1].id,
                confirm_discard=True,
            )
            db.session.commit()

            assert deleted == 1
            assert _paydays(user_id)[-1] == (
                self._LAST_PAYDAY, self._LAST_COVERED, 9,
            )
            assert db.session.get(Transfer, xfer_id) is not None
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id,
            ).all()
            # Invariant 1 is "one EXPENSE and one INCOME", so the pair's SHAPE
            # is asserted and not just its size -- two expense shadows would
            # satisfy a bare count while breaking the invariant this names.
            assert len(shadows) == 2
            assert {shadow.transaction_type_id for shadow in shadows} == {
                ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            }
            assert {shadow.settled_on for shadow in shadows} == {date(2026, 6, 15)}

    def test_a_REGENERATE_derives_ONCE_over_the_end_state(
        self, app, db, seed_user, seed_periods, monkeypatch, caplog,
    ):
        """Retire and record are ONE write, and this is what that buys.

        Regenerate RETIRES a tail and RECORDS a new one.  While those were two
        write calls, everything downstream saw the schedule BETWEEN them -- an
        interval that exists for one statement -- and the deleted coverage rule
        measured it as a false refusal: a settled row that cleared after its own
        paycheck refused every regenerate, naming a day the rebuilt schedule
        covers comfortably.

        That rule is gone; the composition is not, because
        ``_write_derivation`` still runs against whatever it is handed.  Applied
        separately it shortens the newly-last survivor to a cadence projection
        and logs a WARNING repair, then immediately undoes both.  One call, one
        derivation, and the intermediate shape is never materialised.

        Today is pinned inside the 2026-03-13 paycheck so that regenerate
        RETAINS it (with the five before it) and rebuilds from 2026-03-27: the
        retained schedule covers only to 2026-03-26 for one statement, and the
        rebuilt one runs to 2026-07-16 -- past the 2026-06-15 the row settled
        on.
        """
        freeze_today(monkeypatch, date(2026, 3, 20))
        with app.app_context():
            user_id = seed_user["user"].id
            retained = next(
                period for period in seed_periods
                if period.start_date == date(2026, 3, 13)
            )
            self._settled_row(
                db.session, seed_user, retained, date(2026, 6, 15),
            )
            db.session.commit()

            with caplog.at_level(
                "WARNING", logger="app.services.pay_period_write",
            ):
                pay_period_admin.regenerate_pay_periods(
                    user_id, date(2026, 3, 27), 8, rhythm_of(14), confirm_discard=True,
                )
            db.session.commit()

            rows = _paydays(user_id)
            assert rows[-1] == (date(2026, 7, 3), date(2026, 7, 16), 13)
            # The day the two-call version refused on is covered by the result.
            assert any(
                start <= date(2026, 6, 15) <= end for start, end, _index in rows
            )
            # And the retained tail was never materialised at its intermediate
            # shape: a two-call regenerate rewrites 2026-03-13's end to the
            # cadence projection and logs that as a repair before undoing it.
            assert [
                record for record in caplog.records
                if getattr(record, "event", None) == "pay_periods_rematerialised"
            ] == []

    def test_the_rolling_topup_APPENDS_where_it_used_to_decline(
        self, app, db, seed_user, seed_periods,
    ):
        """The read-path write has nothing left to swallow.

        ``/grid`` and ``/dashboard`` call the top-up with no handler of their
        own, so anything raised inside it is a 500 on both of the app's main
        screens.  The deleted coverage rule COULD raise here, which is why
        ``top_up_rolling_window`` carried a ``try/except`` and an event
        (``pay_periods_topup_refused``) whose whole purpose was to turn a
        business refusal into a silent no-op on a read path.  An opportunistic
        writer needing a swallow was the clearest evidence the rule did not
        belong there; both are gone, and the top-up simply appends.

        The state that used to trip it needs a stored cadence SHORTER than the
        schedule it generated -- no door can create that since C3-b's cadence
        rule, so it is built by editing the schedule row directly, the shape
        pre-C3-b data carries (finding **P28**).  With cadence 3 the append
        lands 2026-05-11 and pulls the last paycheck's end back to 2026-05-10,
        leaving the row that settled 2026-05-18 outside every paycheck.  The
        row is untouched and the window fills.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            row = self._settled_row(
                db.session, seed_user, seed_periods[-1], date(2026, 5, 18),
            )
            row_id = row.id
            pay_schedule_service.set_rolling(
                user_id, enabled=True, target_periods=11,
            )
            db.session.query(PaySchedule).filter_by(user_id=user_id).update(
                {"cadence_days": 3}, synchronize_session=False,
            )
            db.session.commit()

            created = pay_period_rolling.top_up_rolling_window(
                user_id, as_of=date(2026, 1, 5),
            )
            db.session.commit()

            assert len(created) == 1
            rows = _paydays(user_id)
            assert rows[-1] == (date(2026, 5, 11), date(2026, 5, 13), 10)
            # The previous last paycheck gave up 2026-05-11..2026-05-21 to the
            # append, so the settle day below is now covered by nothing.
            assert rows[-2] == (self._LAST_PAYDAY, date(2026, 5, 10), 9)
            survivor = db.session.get(Transaction, row_id)
            assert survivor.settled_on == date(2026, 5, 18)
            assert not any(
                start <= date(2026, 5, 18) <= end for start, end, _index in rows
            )


class TestTheCadenceRule:
    """A batch that RECORDS a payday stores its cadence; one that records none does not.

    Findings **P12** (a batch creating nothing still rewrote the cadence) and
    **P29** (the extend door generated at a cadence it never persisted).  The
    trigger weighed and rejected was "at least TWO paydays, or the owner's
    first": it silently discards a REQUIRED form input, because a regenerate at
    ``num_periods=1, cadence_days=30`` would then build a 14-day paycheck and
    say nothing.
    """

    def test_a_batch_that_records_a_payday_stores_its_cadence(
        self, app, db, bare_user,
    ):
        """Including a ONE-payday batch, whose cadence the user typed.

        The value is deliberately not 14: an on-default fixture cannot tell
        "the cadence was stored" from "the default was".
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=1, rhythm=rhythm_of(7),
            )
            db.session.commit()

            assert pay_schedule_service.get_schedule(user_id).cadence_days == 7
            assert _paydays(user_id) == [
                (date(2026, 1, 2), date(2026, 1, 8), 0),
            ]

    def test_a_batch_that_records_nothing_leaves_the_cadence_alone(
        self, app, db, bare_user,
    ):
        """Finding **P12**, closed exactly and no wider.

        The reachable path the finding names: a ``num_periods=1`` post naming
        an existing payday.  It creates zero rows, and before this step it
        still reached ``upsert_schedule`` -- so a form post moved the horizon
        every later extend and rolling top-up continue from, while flashing
        "Generated 0 pay periods."
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()

            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=1, rhythm=rhythm_of(30),
            )
            db.session.commit()

            assert created == []
            assert pay_schedule_service.get_schedule(user_id).cadence_days == 14

    def test_a_REFUSED_batch_leaves_the_cadence_alone(
        self, app, db, bare_user,
    ):
        """A batch that does not get written does not move the forecast either.

        The control the retired ``TestEstablishSchedule`` carried, restored
        because an adversarial review of this step found the writer upserting
        the cadence BEFORE its last refusal could fire.  The upsert is now the
        second-to-last statement, after both refusals, so a refused batch
        leaves the schedule row exactly as it was -- which matters because
        ``cadence_days`` sets the derived horizon every extend and rolling
        top-up continues from.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            db.session.commit()

            with pytest.raises(ValidationError):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 1, 20),
                    num_periods=2, rhythm=rhythm_of(7),
                )
            db.session.rollback()

            assert pay_schedule_service.get_schedule(user_id).cadence_days == 14

    def test_extend_takes_no_cadence_at_all(self, app, db, bare_user):
        """Finding **P29**, closed by DELETION rather than by a new write.

        ``extend_pay_periods`` accepted a ``cadence_days`` the extend card
        renders no control for, generated at it, and never stored it -- so a
        direct POST wrote 7-day paychecks while the schedule still said 14.
        Extend CONTINUES a schedule, so the question is not its to ask; the
        parameter is gone, which is what finding **P30** objected to answering
        with a write.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            db.session.commit()

            with pytest.raises(TypeError):
                pay_period_admin.extend_pay_periods(
                    user_id, 1, cadence_days=7,
                )

            pay_period_admin.extend_pay_periods(user_id, 1)
            db.session.commit()

            assert pay_schedule_service.get_schedule(user_id).cadence_days == 14
            assert _paydays(user_id)[-1] == (
                date(2026, 1, 30), date(2026, 2, 12), 2,
            )


class TestTheWriterBoundsWhatOneCallMayCreate:
    """``reject_out_of_range_batch_size`` -- plan step X-ad-a, as C4-c left it.

    ``num_periods`` was bounded by no service at all, so a non-form caller
    could ask for zero (failing several statements later under a message about
    accounts) or for a hundred thousand.  That is the whole of what this door
    refuses now.

    **The cadence FLOOR that used to sit beside it is gone, and the first case
    below is its inverse.**  It refused a cadence of 1 because the last
    period's stored end would be its own ``start_date`` and
    ``ck_pay_periods_date_order`` would refuse it -- an unhandled
    ``IntegrityError`` 500, reproduced on both the settings form and the
    registration form.  A one-day pay cycle was always legitimate; what could
    not hold one was a stored column.  Plan step ``pay_calendar:C4-c`` dropped
    it, closing pay-calendar findings **P9** and **P33**.
    """

    def test_a_one_day_cadence_is_ACCEPTED(self, app, db, bare_user):
        """Finding **P9**, closed: two paydays a day apart are two paychecks.

        This case asserted a ``ValidationError`` until plan step
        ``pay_calendar:C4-c``, and inverting it is the point.  Three paydays a
        day apart derive three one-day periods -- each end is the day before
        the next payday, and the LAST is ``start + (1 - 1)``, its own payday.
        ``ck_pay_periods_date_order`` refused exactly this shape.
        """
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=bare_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=3,
                rhythm=rhythm_of(1),
            )
            db.session.commit()

            assert _paydays(bare_user["user"].id) == [
                (date(2026, 1, 2), date(2026, 1, 2), 0),
                (date(2026, 1, 3), date(2026, 1, 3), 1),
                (date(2026, 1, 4), date(2026, 1, 4), 2),
            ]

    def test_a_one_day_cadence_survives_the_read_path_top_up(
        self, app, db, bare_user,
    ):
        """Finding **P33**, closed structurally rather than swallowed.

        ``top_up_rolling_window`` runs on ``/grid`` and ``/dashboard`` with no
        handler of its own, so for an owner holding cadence 1 the writer's
        refusal was a permanent 500 on both screens.  The refusal is gone, so
        the top-up simply extends the schedule -- the state was never illegal,
        only unstorable.

        Both paydays fall after this module's frozen today (2025-12-01), so the
        window holds two current-and-future paychecks against a target of four
        and the deficit is two.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(1),
            )
            pay_schedule_service.set_rolling(
                user_id, enabled=True, target_periods=4,
            )
            db.session.commit()

            created = pay_period_rolling.top_up_rolling_window(user_id)
            db.session.commit()

            assert [period.start_date for period in created] == [
                date(2026, 1, 4), date(2026, 1, 5),
            ]

    def test_a_two_day_cadence_is_accepted(self, app, db, bare_user):
        """Two paydays two days apart give two-day periods.

        01-02..01-03 and 01-04..01-05 -- the case that was the INCLUSIVE-floor
        control while a floor existed, kept because it is the ordinary short
        cycle and the arithmetic is worth pinning either way.
        """
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=bare_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=2,
                rhythm=rhythm_of(2),
            )
            db.session.commit()
            assert _paydays(bare_user["user"].id) == [
                (date(2026, 1, 2), date(2026, 1, 3), 0),
                (date(2026, 1, 4), date(2026, 1, 5), 1),
            ]

    @pytest.mark.parametrize("count", [0, -1, 261, 100_000])
    def test_a_batch_size_outside_the_policy_is_refused(
        self, app, db, bare_user, count,
    ):
        """Zero, negative and oversized batches refuse and write nothing.

        Zero is the one that mattered: it created no periods, no error, and
        surfaced far downstream.  100000 is 383 years of fortnights in one
        transaction.
        """
        with app.app_context():
            with pytest.raises(ValidationError, match="between 1 and 260"):
                pay_period_write.record_paydays(
                    user_id=bare_user["user"].id,
                    first_payday=date(2026, 1, 2),
                    num_periods=count,
                    rhythm=rhythm_of(14),
                )
            db.session.rollback()
            assert all_periods(
                bare_user["user"].id,
            ) == []

    def test_the_form_bound_and_the_writer_bound_accept_the_SAME_cadence(
        self, app, db, bare_user,
    ):
        """What the generate form admits is exactly what the writer materialises.

        There were TWO cadence floors between plan steps X-ad-a and
        ``pay_calendar:C4-c``: the column admitted 1 and the schema admitted 2,
        because the writer could not put a one-day period into a stored
        ``end_date``.  ``CADENCE_DAYS_FORM_MIN`` was that second number and it
        is deleted -- a bound the schema restates is a bound that can drift
        from the column.

        Graded end to end rather than by comparing two constants: the schema's
        own ``load`` produces the payload and the writer consumes it, so a
        floor that came back on either side fails here.  It is also what keeps
        ``routes/pay_periods.generate``'s error attribution provable -- the
        schema bounds the cadence and the batch to exactly what the writer
        accepts, so the only ``ValidationError`` that can reach that handler is
        the forward-only one it renders on ``start_date``.

        **That last sentence needed a SECOND rule to stay true**, added at plan
        step ``pay_calendar:C14-b``.  The writer gained a fifth refusal -- a
        payday convention the cadence beside it cannot carry -- which no bound
        on either field alone can answer, so the schema gained
        ``validate_derivable_rhythm`` to ask it as a cross-field rule.  The
        payload below therefore threads the schema's own ``shift`` into the
        rhythm rather than defaulting one: a test that supplied ``none`` by
        hand would grade the cadence agreement and leave the pair rule
        untested at exactly the boundary this case exists to hold.
        """
        with app.app_context():
            loaded = PayPeriodGenerateSchema().load({
                "start_date": "2026-01-02",
                "num_periods": "2",
                "cadence_days": str(CADENCE_DAYS_MIN),
            })
            pay_period_write.record_paydays(
                user_id=bare_user["user"].id,
                first_payday=loaded["start_date"],
                num_periods=loaded["num_periods"],
                rhythm=rhythm_of(loaded["cadence_days"], loaded["shift"]),
            )
            db.session.commit()

            assert loaded["cadence_days"] == 1
            assert _paydays(bare_user["user"].id) == [
                (date(2026, 1, 2), date(2026, 1, 2), 0),
                (date(2026, 1, 3), date(2026, 1, 3), 1),
            ]

    def test_an_unstorable_cadence_creates_no_periods(self, app, db, bare_user):
        """A cadence the schedule column refuses writes nothing at all.

        The refusal happens before any row is added -- which is what makes the
        generate route's 422 clean.  ``establish_schedule`` used to compose the
        two calls in the other order, and a naive composition would have left
        the owner with 366-day pay periods and no schedule row.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            with pytest.raises(ValidationError, match="between 1 and 365"):
                pay_period_write.record_paydays(
                    user_id=user_id,
                    first_payday=date(2026, 1, 2),
                    num_periods=2,
                    rhythm=rhythm_of(366),
                )
            db.session.rollback()
            assert all_periods(user_id) == []
            assert pay_schedule_service.get_schedule(user_id) is None


class TestThereIsOneWriter:
    """The module boundary IS the fence, so it is asserted rather than assumed.

    Plan step C3-b consolidated three write sites into one module (developer
    ruling): the ``PayPeriod`` constructor and the two bulk ``DELETE``\\ s.  No
    pylint checker enforces it -- finding ``balance:N-147`` already records that
    two checkers police their rule with hand-maintained module-name lists, and
    a third would widen that finding.  What holds the boundary instead is that
    the census below is one grep, and this test is that grep.
    """

    def test_only_pay_period_write_constructs_or_deletes_a_pay_period(self):
        """``app/`` holds exactly one PayPeriod construction and one DELETE."""
        # pylint: disable=import-outside-toplevel
        import ast

        # Three shapes, and an adversarial review of this step is why it is
        # not one.  The first cut matched only a bare ``PayPeriod(...)`` and a
        # bare ``.delete()``, so ``models.PayPeriod(...)``, a bulk ``.update()``
        # and a raw ``execute(text(...))`` would all have slipped past a test
        # whose whole purpose is to say they cannot.
        #
        # **What it cannot see, said rather than implied**: ``session.add(x)``,
        # ``session.merge(x)`` and ``bulk_save_objects([...])`` name no class,
        # so no AST can tell a pay period from anything else there.  Shape 1
        # covers the reachable half of that -- a row has to be CONSTRUCTED
        # before it can be added -- and the rest is what the module boundary is
        # for.
        def _names_the_class(node):
            """Whether *node* refers to the ``PayPeriod`` class as a symbol."""
            return (
                isinstance(node, ast.Name) and node.id == "PayPeriod"
            ) or (
                isinstance(node, ast.Attribute) and node.attr == "PayPeriod"
            )

        def _queries_the_table(node):
            """Whether a ``.delete()`` / ``.update()`` receiver queries the table."""
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr in {"query", "filter_by", "filter"}
                    and any(
                        _names_the_class(arg)
                        or (isinstance(arg, ast.Attribute)
                            and _names_the_class(arg.value))
                        for arg in inner.args
                    )
                ):
                    return True
            return False

        writes = set()
        for path in sorted((_REPO_ROOT / "app").rglob("*.py")):
            relative = path.relative_to(_REPO_ROOT).as_posix()
            for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
                if not isinstance(node, ast.Call):
                    continue
                # 1. Constructing a row, by any spelling of the class name.
                if _names_the_class(node.func):
                    writes.add(relative)
                # 2. Deleting or bulk-updating rows through a query over them.
                elif (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"delete", "update"}
                    and _queries_the_table(node.func.value)
                ):
                    writes.add(relative)
                # 3. A raw statement naming the table, which skips the ORM.
                elif (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "text"
                    and any(
                        isinstance(arg, ast.Constant)
                        and isinstance(arg.value, str)
                        and "pay_periods" in arg.value
                        for arg in node.args
                    )
                ):
                    writes.add(relative)

        assert sorted(writes) == ["app/services/pay_period_write.py"]

    def test_the_readers_module_writes_nothing(self):
        """``pay_period_service`` holds no write of any kind after the split."""
        source = (
            _REPO_ROOT / "app" / "services" / "pay_period_service.py"
        ).read_text()
        for forbidden in ("db.session.add", "db.session.delete", ".delete("):
            assert forbidden not in source, (
                f"pay_period_service must hold no write; found {forbidden!r}"
            )


class TestTheWriterTakesIdsAndScopesThemToTheOwner:
    """Both doors take ``budget.pay_periods.id`` values and read the rows here.

    Plan step **C2-f3b**.  ``retire_paydays`` took ``(periods, doomed)`` -- two
    lists of ORM rows the caller had queried -- and ``record_paydays`` took a
    ``retiring`` list it read one attribute off.  So ``pay_period_admin`` had to
    hold ORM rows for a set of integers, which is the last thing keeping that
    module reading ``budget.pay_periods``.  The ORM read moved here, into the
    module that owns the table, and what crosses the seam is ids.

    That makes the OWNER scoping structural rather than a property of the two
    callers: the delete set is this owner's rows LESS the survivors, so an id
    naming somebody else's period (or naming nothing) can only ever retire
    nothing.  The old shape trusted the caller to have queried an owner-scoped
    list, and returned ``len(doomed)`` whether or not those rows existed.
    """

    def test_it_deletes_exactly_the_periods_the_ids_name(
        self, app, db, seed_user,
    ):
        """Three ids in, three periods gone, three reported."""
        with app.app_context():
            user_id = seed_user["user"].id
            created = pay_period_write.record_paydays(
                user_id, date(2026, 1, 2), 5, rhythm_of(14),
            )
            db.session.flush()
            doomed = {period.id for period in created[2:]}

            assert pay_period_write.retire_paydays(user_id, doomed) == 3
            survivors = {
                period.id
                for period in all_periods(user_id)
            }
            assert doomed & survivors == set()

    def test_another_owners_id_retires_nothing_and_is_not_counted(
        self, app, db, seed_user, seed_second_periods,
    ):
        """The count is the intersection, never the size of the argument.

        The first owner asks to retire a period belonging to the second.  Under
        the old signature the caller supplied both lists, so this was a
        caller-side property; here the writer resolves the rows itself and the
        foreign id is simply not in the set it can reach.  It is graded on BOTH
        sides -- nothing deleted, and nothing counted -- because a door that
        deleted nothing while reporting "1 removed" is what the settings page
        would flash at the user.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            foreign = seed_second_periods[4]
            before = {
                period.id
                for period in all_periods(
                    foreign.user_id,
                )
            }

            assert pay_period_write.retire_paydays(user_id, {foreign.id}) == 0
            after = {
                period.id
                for period in all_periods(
                    foreign.user_id,
                )
            }
            assert after == before

    def test_recording_a_batch_ignores_a_foreign_retiring_id(
        self, app, db, seed_user, seed_second_periods,
    ):
        """``record_paydays`` scopes ``retiring_ids`` the same way.

        Regenerate and reset reach the table through this door rather than
        through ``retire_paydays``, so the same property has to hold on both or
        the scoping is only half structural.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            pay_period_write.record_paydays(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            db.session.flush()
            foreign = seed_second_periods[4]

            pay_period_write.record_paydays(
                user_id, date(2026, 3, 6), 2, rhythm_of(14),
                retiring_ids={foreign.id},
            )
            db.session.flush()
            assert db.session.get(PayPeriod, foreign.id) is not None
            assert len(all_periods(user_id)) == 6

    def test_an_id_naming_no_row_at_all_is_an_idempotent_no_op(
        self, app, db, seed_user,
    ):
        """A stale id -- one a concurrent truncate already deleted -- changes nothing.

        The confirm-discard panel re-posts the id the user reviewed, so this is
        reachable rather than hypothetical; ``truncate_pay_periods`` refuses it
        at its own resolve, and this grades the writer beneath that door.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            created = pay_period_write.record_paydays(
                user_id, date(2026, 1, 2), 4, rhythm_of(14),
            )
            db.session.flush()
            before = _paydays(user_id)
            gone = max(period.id for period in created) + 10_000

            assert pay_period_write.retire_paydays(user_id, {gone}) == 0
            assert _paydays(user_id) == before


class TestTheOwnerIdReader:
    """``owner_period_ids`` is the door for a caller that means "all of them".

    Plan step **C2-f3b**, corrected by an adversarial review of it.  Reset spelled
    this ``calendar_for(user_id).saved()``, which made the door that REPAIRS a
    broken schedule depend on the schedule being derivable; the identity of a row
    is not a derived value and is not reached through one.
    """

    def test_it_returns_every_id_and_only_this_owners(
        self, app, db, seed_user, seed_second_periods,
    ):
        """Two owners, two disjoint answers."""
        with app.app_context():
            user_id = seed_user["user"].id
            created = pay_period_write.record_paydays(
                user_id, date(2026, 1, 2), 4, rhythm_of(14),
            )
            db.session.flush()

            mine = pay_period_write.owner_period_ids(user_id)
            theirs = pay_period_write.owner_period_ids(
                seed_second_periods[0].user_id,
            )
            assert {period.id for period in created} <= mine
            assert mine & theirs == set()
            assert theirs == {period.id for period in seed_second_periods}

    def test_an_owner_with_no_schedule_gets_an_empty_set(self, app, bare_user):
        """Empty is a legal answer, so reset can run on a schedule-less owner."""
        with app.app_context():
            assert pay_period_write.owner_period_ids(
                bare_user["user"].id,
            ) == set()

    def test_it_reads_no_column_a_derivation_could_move(self, app, db, seed_user):
        """The identity read is a bare id query, not a calendar.

        Graded as a statement census rather than by argument: an implementation
        that resolved the ids through ``calendar_for`` would issue that
        function's ``budget.pay_schedule`` read, and an owner whose stored
        cadence cannot define a calendar would reach ``derive_periods`` and a
        500 on the door that exists to repair them.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            pay_period_write.record_paydays(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            db.session.flush()

            ids, statements = capture_sql_statements(
                lambda: pay_period_write.owner_period_ids(user_id),
            )
            assert ids
            assert len(statements) == 1
            assert "pay_schedule" not in statements[0][0].lower()


class TestTheRetiredCountIsTheIntersection:
    """``len(current) - len(keep)`` graded BETWEEN its extremes.

    The three cases above it are all-own, all-foreign and all-stale, so an
    implementation returning ``min(len(doomed_ids), len(current))`` survives them
    (adversarial review, 2026-08-19).  A MIXED set is what separates the two.
    """

    def test_a_mixed_set_counts_and_deletes_only_the_owned_half(
        self, app, db, seed_user, seed_second_periods,
    ):
        """Two of this owner's ids, one foreign and one stale -> 2."""
        with app.app_context():
            user_id = seed_user["user"].id
            created = pay_period_write.record_paydays(
                user_id, date(2026, 1, 2), 5, rhythm_of(14),
            )
            db.session.flush()
            mine = {created[3].id, created[4].id}
            stale = max(period.id for period in created) + 10_000
            mixed = mine | {seed_second_periods[2].id, stale}

            before = {
                period.id
                for period in all_periods(user_id)
            }
            assert pay_period_write.retire_paydays(user_id, mixed) == 2
            survivors = {
                period.id
                for period in all_periods(user_id)
            }
            assert survivors & mine == set()
            assert survivors == before - mine
            # The foreign owner is untouched, which the count alone cannot say.
            assert db.session.get(
                PayPeriod, seed_second_periods[2].id,
            ) is not None

    def test_the_generated_event_reports_the_same_intersection(
        self, app, db, seed_user, seed_second_periods, caplog,
    ):
        """``retired=`` counts what went, not the size of the argument.

        The field moved from ``len(retiring_ids)`` to ``len(current) - len(keep)``
        at this step and nothing read it; a log line that says "3 retired" beside
        two deletions is a record of an operation that did not happen.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            created = pay_period_write.record_paydays(
                user_id, date(2026, 1, 2), 4, rhythm_of(14),
            )
            db.session.flush()
            mixed = {created[3].id, seed_second_periods[2].id}

            with caplog.at_level(logging.INFO):
                pay_period_write.record_paydays(
                    user_id, date(2026, 3, 6), 2, rhythm_of(14), retiring_ids=mixed,
                )
            retired = [
                record.retired for record in caplog.records
                if getattr(record, "event", None) == "pay_periods_generated"
            ]
            assert retired[-1] == 1
