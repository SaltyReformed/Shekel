"""
Shekel Budget App -- Pay Period Writer Tests (plan step C3-b)

``pay_period_write`` is the one place in ``app/`` that changes
``budget.pay_periods``, so this is the one suite that grades what may be
written.  It came from ``test_pay_period_service.py`` when the writer left
that module; the classes carried over unchanged are the batch-shape and
refusal ones, and the classes below them are C3-b's own.

**What C3-b changed, and therefore what these tests are about:**

* The stored ``end_date`` / ``period_index`` are no longer AUTHORED from
  cadence arithmetic.  They are materialised from
  ``pay_calendar.derive_periods`` over the owner's WHOLE payday set, every
  write, so the columns cannot disagree with the paydays they derive from --
  which is what makes plan step C4's ``DROP COLUMN`` a no-op.
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
from app.enums import StatusEnum, TxnTypeEnum
from app.models.pay_period import MIN_MATERIALISABLE_CADENCE_DAYS, PayPeriod
from app.models.pay_schedule import CADENCE_DAYS_MIN, PaySchedule
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.schemas.validation.pay_periods import CADENCE_DAYS_FORM_MIN
from app.services import (
    pay_period_admin,
    pay_period_rolling,
    pay_period_write,
    pay_schedule_service,
    transfer_service,
)
from tests._test_helpers import (
    all_periods,
    add_txn,
    capture_sql_statements,
    create_savings_account,
    freeze_today,
    open_calendar_hole,
)


#: Pinned "today", before every date this module writes.  The lock classifier
#: calls a period whose ``end_date`` is in the past HISTORICAL and refuses to
#: delete it, so the truncate tests below would refuse on the wall clock rather
#: than on what they are grading.
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
    """Return the owner's ``(payday, end_date, period_index)`` rows, payday order."""
    return [
        (period.start_date, period.end_date, period.period_index)
        for period in sorted(
            all_periods(user_id),
            key=lambda period: period.start_date,
        )
    ]


class TestItRecordsPaydays:
    """The batch shape: which paydays a call records, and what it stores on them."""

    def test_it_records_the_requested_paydays(self, app, db, bare_user):
        """Five paydays a fortnight apart, each period covering fourteen days."""
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=bare_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=5,
                cadence_days=14,
            )
            db.session.commit()

            assert len(periods) == 5
            for period in periods:
                assert (period.end_date - period.start_date).days + 1 == 14

    def test_the_ordinal_is_the_position_in_payday_order(self, app, db, bare_user):
        """Indices run 0..n-1, and they are READ off the derivation.

        Before plan step C3-b the writer ASSIGNED ``max_index + 1``; now the
        ordinal is the payday's position in the owner's sorted set, so index
        order and date order cannot disagree -- there is no second value.
        """
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=bare_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=3,
                cadence_days=14,
            )
            db.session.commit()

            assert [p.period_index for p in periods] == [0, 1, 2]

    def test_every_end_but_the_last_is_the_next_payday_minus_one(
        self, app, db, bare_user,
    ):
        """The definition, stored -- and the last end is the ONE projection.

        This test replaced ``test_end_date_equals_start_plus_cadence_minus_one``
        at plan step C3-b, and the replacement is the step in one assertion.
        On an on-cadence batch the two rules give the same answer, which is
        precisely why the old test could not tell them apart; what distinguishes
        them is which rule the writer is FOLLOWING, and the classes below build
        off-cadence schedules that make the difference visible.
        """
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=bare_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=3,
                cadence_days=14,
            )
            db.session.commit()

            for earlier, later in zip(periods, periods[1:]):
                assert earlier.end_date == later.start_date - timedelta(days=1)
            assert periods[-1].end_date == periods[-1].start_date + timedelta(
                days=13,
            )

    def test_an_existing_payday_is_skipped_rather_than_duplicated(
        self, app, db, bare_user,
    ):
        """A re-run naming days already on the table records only the new ones."""
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, cadence_days=14,
            )
            db.session.commit()

            again = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=4, cadence_days=14,
            )
            db.session.commit()

            assert [p.start_date for p in again] == [
                date(2026, 1, 30), date(2026, 2, 13),
            ]
            assert [p.period_index for p in again] == [2, 3]
            assert len(all_periods(user_id)) == 4

    def test_a_second_batch_appends_and_keeps_payday_order(
        self, app, db, bare_user,
    ):
        """Appending after the current coverage extends the schedule forward."""
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=3, cadence_days=14,
            )
            db.session.commit()

            new = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 13),
                num_periods=2, cadence_days=14,
            )
            db.session.commit()

            assert [p.start_date for p in new] == [
                date(2026, 2, 13), date(2026, 2, 27),
            ]
            assert [p.period_index for p in new] == [3, 4]
            starts = [row[0] for row in _paydays(user_id)]
            assert starts == sorted(starts)

    def test_num_periods_one_records_one_payday(self, app, db, bare_user):
        """The smallest legal batch."""
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=bare_user["user"].id, first_payday=date(2026, 1, 2),
                num_periods=1, cadence_days=14,
            )
            db.session.commit()

            assert len(periods) == 1
            assert periods[0].start_date == date(2026, 1, 2)
            assert periods[0].end_date == date(2026, 1, 15)

    def test_a_large_batch_stays_correct_at_scale(self, app, db, bare_user):
        """104 periods -- two years of fortnights, the production shape."""
        with app.app_context():
            start = date(2026, 1, 2)
            periods = pay_period_write.record_paydays(
                user_id=bare_user["user"].id, first_payday=start,
                num_periods=104, cadence_days=14,
            )
            db.session.commit()

            assert len(periods) == 104
            assert periods[-1].period_index == 103
            assert periods[-1].start_date == start + timedelta(days=103 * 14)
            for period in periods:
                assert period.end_date == period.start_date + timedelta(days=13)

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
                        num_periods=1, cadence_days=14,
                    )
            db.session.rollback()


class TestTheForwardOnlyFloor:
    """Ruling **R-PC1**'s structural half: no payday between two existing ones.

    It replaced ``_reject_overlapping_batch``, which bounded a batch on
    ``max(end_date)`` -- a column plan step C4 drops -- and which was doing
    this job by accident.  **The only thing left to refuse is a payday landing
    inside a paycheck the owner already has**, because a gap and an overlap
    stopped being expressible once the columns are derived.  Plan step C6 owns
    that insert, behind ledger row **P10**'s two unruled questions, and C6 is
    what deletes this rule.

    **The floor is ONE CADENCE, not two days, and this class carries the
    control for the correction.**  C3-b's first cut bounded at
    ``latest_payday + MIN_MATERIALISABLE_CADENCE_DAYS``, which is a paycheck
    too low: the LAST period runs to ``latest_payday + cadence - 1``, so every
    day between the two split it -- reachable, and P10 says it is not.
    """

    def _two_fortnightly_periods(self, user_id):
        """Record 2026-01-02 and 2026-01-16; coverage runs to 2026-01-29."""
        return pay_period_write.record_paydays(
            user_id=user_id, first_payday=date(2026, 1, 2),
            num_periods=2, cadence_days=14,
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
                    num_periods=4, cadence_days=14,
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
                    num_periods=1, cadence_days=14,
                )
            db.session.rollback()

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 30),
                num_periods=1, cadence_days=14,
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
                    num_periods=2, cadence_days=7,
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
                num_periods=3, cadence_days=7,
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
                num_periods=1, cadence_days=14,
            )
            db.session.commit()
            assert periods[0].start_date == date(2020, 3, 1)


class TestItMaterialisesTheWholeDerivation:
    """C3-b's subject: the stored columns ARE ``derive_periods``, every write.

    The developer ruled the WHOLE payday list is rewritten (2026-08-10).  The
    two stored columns are a cache of one function; a cache refreshed only at
    its edge is a second source of truth, and an interior hole would then never
    be repaired by any forward append -- so plan step C4 would silently move
    that owner's figures.
    """

    def test_a_hole_before_the_batch_is_repaired_by_it(self, app, db, bare_user):
        """The preceding paycheck absorbs the days the old writer left uncovered.

        Paydays 2026-01-02 and 2026-01-16 with a hand-punched hole after
        2026-01-22 (the shape a pre-C3-b write left behind).  Recording
        2026-02-13 rewrites the January paycheck to end the day before it.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, cadence_days=14,
            )
            open_calendar_hole(db.session, created[0], date(2026, 1, 8))
            db.session.commit()

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 13),
                num_periods=1, cadence_days=14,
            )
            db.session.commit()

            assert _paydays(user_id) == [
                (date(2026, 1, 2), date(2026, 1, 15), 0),
                (date(2026, 1, 16), date(2026, 2, 12), 1),
                (date(2026, 2, 13), date(2026, 2, 26), 2),
            ]

    def test_an_INTERIOR_hole_is_repaired_by_a_forward_append(
        self, app, db, bare_user,
    ):
        """Finding **N-127**: the hole with no working repair, now repaired.

        The hole sits between the FIRST and SECOND paydays and the append lands
        at the far end of the schedule, so nothing about the batch touches it.
        Under "rewrite the preceding end only" it would survive every future
        write; under the ruled whole-list rewrite the next write of any kind
        closes it.  That is the whole reason the fork was ruled the way it was.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=4, cadence_days=14,
            )
            hole = open_calendar_hole(db.session, created[0], date(2026, 1, 8))
            db.session.commit()
            assert hole == (date(2026, 1, 9), date(2026, 1, 15))

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 27),
                num_periods=1, cadence_days=14,
            )
            db.session.commit()

            assert _paydays(user_id)[0] == (
                date(2026, 1, 2), date(2026, 1, 15), 0,
            )

    def test_a_healthy_schedule_costs_no_UPDATE_at_all(
        self, app, db, bare_user,
    ):
        """The rewrite is free where nothing disagrees, and that is measured.

        SQLAlchemy compares each attribute against its committed state before
        building an ``UPDATE``, so assigning an identical value emits no
        statement.  Production is such a schedule -- 61 paydays, 0 index and 0
        end mismatches, measured 2026-08-10 -- so the whole-list rewrite costs
        one derivation and no SQL there.  Asserted rather than argued: without
        this, "it writes nothing on a healthy schedule" is a claim about
        SQLAlchemy that nothing in this repo checks.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=6, cadence_days=14,
            )
            db.session.commit()

            # ON cadence: the sixth payday is 2026-03-13 and covers to
            # 2026-03-26, so a payday on 2026-03-27 leaves every existing end
            # exactly where it is.  An off-cadence append would move the
            # previous last end legitimately and this would measure that
            # instead.
            _result, statements = capture_sql_statements(
                lambda: pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 3, 27),
                    num_periods=1, cadence_days=14,
                ),
            )
            db.session.commit()

            updates = [
                text for text, _params in statements
                if text.lstrip().upper().startswith("UPDATE BUDGET.PAY_PERIODS")
            ]
            assert updates == []

    def test_a_repaired_row_is_logged_at_warning(
        self, app, db, bare_user, caplog,
    ):
        """A silent repair is still a silent change to a paycheck's shape.

        The event names both values so an operator can see what moved; the
        level is WARNING because the row it rewrites was a row whose stored
        coverage disagreed with the owner's own paydays, which the model says
        cannot happen.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, cadence_days=14,
            )
            open_calendar_hole(db.session, created[0], date(2026, 1, 8))
            db.session.commit()

            # ON cadence, so the ONLY row that moves is the repaired one:
            # an off-cadence append would also move the last period's
            # projected end and this would see two events.
            with caplog.at_level("WARNING", logger="app.services.pay_period_write"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 1, 30),
                    num_periods=1, cadence_days=14,
                )
            db.session.commit()

            repaired = [
                record for record in caplog.records
                if getattr(record, "event", None) == "pay_periods_rematerialised"
            ]
            assert len(repaired) == 1
            assert repaired[0].stored_end == "2026-01-08"
            assert repaired[0].derived_end == "2026-01-15"
            assert repaired[0].payday == "2026-01-02"

    def test_truncate_reprojects_the_new_last_end(self, app, db, bare_user):
        """Deleting the tail re-derives what survives, and C1's claim needs it.

        Paydays ``[2026-01-02, 2026-01-16, 2026-02-11]``: the January 16
        paycheck runs to 2026-02-10 because its successor opens then.  Truncate
        that successor away and it is the LAST period, so its end falls back to
        the cadence projection, 2026-01-29.  A delete re-derived nothing before
        plan step C3-b, so the row kept an end its own schedule no longer
        justified -- and an on-cadence fixture cannot see that, because
        ``lead(start) - 1`` and ``start + cadence - 1`` coincide there.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, cadence_days=14,
            )
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 11),
                num_periods=1, cadence_days=14,
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

        The recompute reads ``budget.pay_schedule`` AFTER the cadence rule has
        run, so "the horizon the app projects" and "the end stored on the last
        row" are the same number by construction.  Here the batch records
        nothing new -- every payday already exists -- so the cadence rule does
        NOT fire, and the stored 14 is what the last end keeps even though the
        call named 30.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, cadence_days=14,
            )
            db.session.commit()

            # ONE payday, and it already exists -- the reachable shape
            # finding P12 names.  Two would step by 30 and create a new one.
            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=1, cadence_days=30,
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
            assert seed_periods[-1].end_date == self._LAST_COVERED

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 5, 22),
                num_periods=1, cadence_days=14,
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
                num_periods=1, cadence_days=14,
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
                num_periods=1, cadence_days=14,
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
                num_periods=1, cadence_days=14,
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
                    user_id, date(2026, 3, 27), 8, 14, confirm_discard=True,
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
                num_periods=1, cadence_days=7,
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
                num_periods=1, cadence_days=14,
            )
            db.session.commit()

            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=1, cadence_days=30,
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
                num_periods=2, cadence_days=14,
            )
            db.session.commit()

            with pytest.raises(ValidationError):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 1, 20),
                    num_periods=2, cadence_days=7,
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
                num_periods=2, cadence_days=14,
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


class TestTheWriterRefusesWhatItCannotMaterialise:
    """``reject_unmaterialisable_batch`` -- plan step X-ad-a.

    Two preconditions this writer had never stated, both measured as real
    failures rather than argued:

    * A cadence of 1 makes the last period's end its own ``start_date``, which
      ``ck_pay_periods_date_order`` refuses -- an unhandled ``IntegrityError``
      500 reproduced on both the settings form and the registration form.
      **Not because a one-day pay cycle is illegitimate**: it is legal, and
      pay-calendar step C4 legalises it by dropping the stored column.
    * ``num_periods`` was bounded by no service at all, so a non-form caller
      could ask for zero (failing several statements later under a message
      about accounts) or for a hundred thousand.
    """

    def test_a_one_day_cadence_is_refused_before_the_check_sees_it(
        self, app, db, bare_user,
    ):
        """Cadence 1 is a ValidationError, not a CheckViolation 500."""
        with app.app_context():
            with pytest.raises(ValidationError, match="at least 2"):
                pay_period_write.record_paydays(
                    user_id=bare_user["user"].id,
                    first_payday=date(2026, 1, 2),
                    num_periods=2,
                    cadence_days=1,
                )
            db.session.rollback()
            assert all_periods(
                bare_user["user"].id,
            ) == []

    def test_a_two_day_cadence_is_accepted(self, app, db, bare_user):
        """The floor is INCLUSIVE, and this is the control for the test above.

        Without it, a refusal that also rejected 2 -- or 30 -- would look
        identical.  Two paydays two days apart give two-day periods:
        01-02..01-03 and 01-04..01-05.
        """
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=bare_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=2,
                cadence_days=2,
            )
            db.session.commit()
            assert [(p.start_date, p.end_date) for p in periods] == [
                (date(2026, 1, 2), date(2026, 1, 3)),
                (date(2026, 1, 4), date(2026, 1, 5)),
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
                    cadence_days=14,
                )
            db.session.rollback()
            assert all_periods(
                bare_user["user"].id,
            ) == []

    def test_the_form_bound_and_the_writer_bound_agree(self):
        """The schema's cadence floor IS the writer's, not the column's.

        This is what makes ``routes/pay_periods.generate``'s error attribution
        provable: the schema bounds the cadence and the batch to exactly what
        the writer accepts, so the only ``ValidationError`` that can reach that
        handler is the forward-only one it renders on ``start_date``.
        """
        assert CADENCE_DAYS_FORM_MIN == MIN_MATERIALISABLE_CADENCE_DAYS
        assert CADENCE_DAYS_FORM_MIN > CADENCE_DAYS_MIN

    def test_an_unstorable_cadence_creates_no_periods(self, app, db, bare_user):
        """A cadence the schedule column refuses writes nothing at all.

        The cadence rule runs BEFORE any row is added, so the refusal happens
        with nothing to roll back -- which is what makes the generate route's
        422 clean.  ``establish_schedule`` used to compose the two calls in the
        other order, and a naive composition would have left the owner with
        366-day pay periods and no schedule row.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            with pytest.raises(ValidationError, match="between 1 and 365"):
                pay_period_write.record_paydays(
                    user_id=user_id,
                    first_payday=date(2026, 1, 2),
                    num_periods=2,
                    cadence_days=366,
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
                user_id, date(2026, 1, 2), 5, 14,
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
            pay_period_write.record_paydays(user_id, date(2026, 1, 2), 3, 14)
            db.session.flush()
            foreign = seed_second_periods[4]

            pay_period_write.record_paydays(
                user_id, date(2026, 3, 6), 2, 14,
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
                user_id, date(2026, 1, 2), 4, 14,
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
                user_id, date(2026, 1, 2), 4, 14,
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
            pay_period_write.record_paydays(user_id, date(2026, 1, 2), 3, 14)
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
                user_id, date(2026, 1, 2), 5, 14,
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
                user_id, date(2026, 1, 2), 4, 14,
            )
            db.session.flush()
            mixed = {created[3].id, seed_second_periods[2].id}

            with caplog.at_level(logging.INFO):
                pay_period_write.record_paydays(
                    user_id, date(2026, 3, 6), 2, 14, retiring_ids=mixed,
                )
            retired = [
                record.retired for record in caplog.records
                if getattr(record, "event", None) == "pay_periods_generated"
            ]
            assert retired[-1] == 1


class TestThePeriodsAlwaysEqualTheirDerivation:
    """The invariant, graded end-to-end through every door that writes.

    Each door mutates the payday set and then the whole calendar is compared
    against ``derive_periods`` over the owner's paydays and stored cadence.
    This is what plan step C4 rests on: if the two agree everywhere, dropping
    the columns cannot change a figure.
    """

    def _assert_stored_equals_derived(self, user_id):
        """Compare every stored row against the derivation over the paydays."""
        # pylint: disable=import-outside-toplevel
        from app.services.pay_calendar import derive_periods

        periods = sorted(
            all_periods(user_id),
            key=lambda period: period.start_date,
        )
        derived = derive_periods(
            [(p.id, p.start_date) for p in periods],
            pay_schedule_service.resolve_cadence(user_id),
        )
        assert [
            (p.start_date, p.end_date, p.period_index) for p in periods
        ] == [
            (d.start_date, d.end_date, d.period_index) for d in derived
        ]

    def test_after_generate(self, app, db, bare_user):
        """The first batch."""
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=bare_user["user"].id, first_payday=date(2026, 1, 2),
                num_periods=4, cadence_days=14,
            )
            db.session.commit()
            self._assert_stored_equals_derived(bare_user["user"].id)

    def test_after_extend(self, app, db, bare_user):
        """A forward append through the admin door."""
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=4, cadence_days=14,
            )
            db.session.commit()
            pay_period_admin.extend_pay_periods(user_id, 3)
            db.session.commit()
            self._assert_stored_equals_derived(user_id)

    def test_after_truncate(self, app, db, bare_user):
        """A tail delete, including the re-projection of the new last end."""
        with app.app_context():
            user_id = bare_user["user"].id
            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=4, cadence_days=14,
            )
            db.session.commit()
            pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=created[1].id,
            )
            db.session.commit()
            self._assert_stored_equals_derived(user_id)

    def test_after_regenerate(self, app, db, seed_user, seed_periods):
        """The door that RETIRES a tail and RECORDS a new one in one write."""
        with app.app_context():
            user_id = seed_user["user"].id
            pay_period_admin.regenerate_pay_periods(
                user_id, date(2026, 5, 22), 4, 7, confirm_discard=True,
            )
            db.session.commit()
            self._assert_stored_equals_derived(user_id)

    def test_after_reset(self, app, db, seed_user, seed_periods):
        """The door that wipes every period and rebuilds from nothing."""
        with app.app_context():
            user_id = seed_user["user"].id
            pay_period_admin.reset_pay_periods(
                user_id, date(2026, 9, 4), 5, 14,
            )
            db.session.commit()
            self._assert_stored_equals_derived(user_id)

    def test_after_a_repair_of_pre_c3b_data(self, app, db, bare_user):
        """A hole written before this step is gone after any door runs."""
        with app.app_context():
            user_id = bare_user["user"].id
            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=4, cadence_days=14,
            )
            open_calendar_hole(db.session, created[1], date(2026, 1, 20))
            db.session.commit()
            with pytest.raises(AssertionError):
                self._assert_stored_equals_derived(user_id)

            pay_period_admin.extend_pay_periods(user_id, 1)
            db.session.commit()
            self._assert_stored_equals_derived(user_id)


class TestThePeriodRowsSurviveTheRewrite:
    """A repair moves COLUMNS, never identity: every ``id`` and FK survives.

    The whole reason plan step C4 is two ``DROP COLUMN``s rather than a
    rewrite is that ``budget.pay_periods.id`` never moves, so all four inbound
    foreign keys are untouched.  A recompute that recreated rows would break
    that quietly -- a transaction would point at a period that no longer
    exists, or at the wrong one.
    """

    def test_a_repaired_period_keeps_its_id_and_its_transactions(
        self, app, db, seed_user, seed_periods,
    ):
        """The row is UPDATEd in place, and its rows still point at it."""
        with app.app_context():
            user_id = seed_user["user"].id
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Rent", "1200.00",
                due_date=date(2026, 1, 5),
            )
            open_calendar_hole(db.session, seed_periods[0], date(2026, 1, 8))
            db.session.commit()
            period_id, txn_id = seed_periods[0].id, txn.id

            pay_period_admin.extend_pay_periods(user_id, 1)
            db.session.commit()

            repaired = db.session.get(PayPeriod, period_id)
            assert repaired is not None
            assert repaired.end_date == date(2026, 1, 15)
            assert db.session.get(Transaction, txn_id).pay_period_id == period_id
