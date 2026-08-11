"""The one database door onto the pay calendar (plan step C2-b1).

``pay_calendar.calendar_for`` is the only impure thing in an otherwise pure
package: it reads an owner's paydays and cadence and hands them to the
derivation.  Nothing in ``app/`` calls it yet -- plan step C2-b2 points the ten
``recurrence.calendar_for`` sites here -- so this module is where it is proven,
BEFORE any consumer depends on it.  That ordering is the whole point of the
decomposition, and it is the technique C1 and C2-a used.

**The equivalence oracle is the load-bearing test here** and it is deliberately
temporary: ``TestItAnswersWhatTheRecurrenceCalendarAnswers`` drives the loaded
:class:`~app.services.pay_calendar.PayCalendar` and the
``recurrence.PeriodCalendar`` that C2-b2 deletes side by side over a real
schedule, so the cutover is a proven swap rather than a hopeful one.  It retires
with the class it compares against, exactly as C1's harness retires at C4.

**The TWO shapes where they diverge are pinned rather than avoided**, and there
being two is an adversarial review's correction to a docstring that named one.
A stored schedule may hold a HOLE (plan finding P2) and the derived calendar
TILES, so day-in-a-hole answers differ by construction (ledger row **P27**,
measured at 55 shapes across 53 tests of this suite).  And the stored CADENCE
may outlive the stored ``end_date`` it generated, which moves the derived
horizon and only the derived horizon (row **P28**).  The second one matters
most here because this suite cannot see it by accident: every horizon
comparison is arithmetically forced to agree.  It was forced through
``resolve_cadence``'s fallback -- no fixture wrote a ``budget.pay_schedule``
row, so the cadence came back as the exact inverse of the generator's
arithmetic -- and since plan step **C3-b** it is forced through the cadence
rule instead, which stores the very cadence the batch generated at.  Each
divergence has its own named control, and the two tests that need the
row-less state now DELETE the row rather than relying on nothing creating it.

The contiguous shape is the ``seed_periods`` fixture, which drops ``seed_user``'s
2024 bootstrap period and RENUMBERS what is left.  That renumbering matters: an
earlier draft of this module deleted the bootstrap row by hand, left the stored
ordinals at 1..10, and the oracle immediately reported the ledger row **P26**
disagreement against its own fixture.

Clock discipline (``.claude/rules/testing.md``): every date here is a literal or
derived from one by ``timedelta``, and nothing calls ``date.today()``, so these
pass identically under ``TZ=Pacific/Kiritimati``.
"""

from datetime import date, timedelta
from inspect import signature

import pytest

from app.models.pay_period import PayPeriod
from app.models.pay_schedule import PaySchedule
from app.services import pay_period_service, pay_period_write, pay_schedule_service
from app.services.pay_calendar import PayCalendar, calendar_for
from app.services.recurrence import PeriodCalendar
from tests._test_helpers import open_calendar_hole

#: ``seed_periods``' first payday, restated so the assertions below name a value
#: rather than a bare literal.  Changing the fixture without changing this is
#: caught by ``test_it_loads_every_payday_the_owner_has``.
FIRST_PAYDAY = date(2026, 1, 2)

#: ``seed_periods``' cadence, which is also production's stored cadence
#: (measured 2026-08-08: ``budget.pay_schedule.cadence_days`` = 14).
CADENCE = 14

#: How many paydays ``seed_periods`` builds.
PERIOD_COUNT = 10


def _gapped_schedule(db_session, user_id, bootstrap_period):
    """Generate the 2026 schedule and re-open the 2024 bootstrap's hole.

    The shape plan finding **P2** describes: a stored hole between the
    bootstrap period's end and the first real payday.  The batch guard of the
    day, ``_reject_overlapping_batch``, ACCEPTED it because it refused OVERLAPS
    and not GAPS.  **Plan step C3-b's writer does not**: it materialises the
    payday derivation, in which the bootstrap period ends the day before the
    first real payday, so the generate below now ABSORBS the hole.
    ``open_calendar_hole`` writes the stored end back down, which is how this
    state is reached from here on -- and in the wild it is data written before
    C3-b.  A test wanting the hole opts in here; every other test takes
    ``seed_periods``.

    Args:
        db_session: The session.
        user_id: The owning user.
        bootstrap_period: The 2024 bootstrap period the hole opens after.

    Returns:
        The generated :class:`~app.models.pay_period.PayPeriod` rows.
    """
    stored_end = bootstrap_period.end_date
    periods = pay_period_write.record_paydays(
        user_id=user_id,
        first_payday=FIRST_PAYDAY,
        num_periods=PERIOD_COUNT,
        cadence_days=CADENCE,
    )
    open_calendar_hole(db_session, bootstrap_period, stored_end)
    db_session.commit()
    return periods


@pytest.mark.usefixtures("seed_periods")
class TestItLoadsTheOwnersWholeSchedule:
    """The COMPLETE payday set, which is the value's one uncheckable precondition."""

    def test_it_loads_every_payday_the_owner_has(self, app, seed_user):
        """The whole set, because a partial one derives a wrong end AND a wrong ordinal.

        There is no ``first``/``count`` argument on this door and there must not
        be; a window is ``PayCalendar.window``, which returns a view.

        The ordinal assertion below is true BY CONSTRUCTION once the count is
        right -- ``derive_periods`` numbers by position -- so it pins the count
        and nothing more.  What actually checks ledger rows **P14** / **P26** is
        ``test_the_stored_columns_are_reproduced_on_a_contiguous_schedule``,
        which compares against the STORED ordinal, and
        ``test_deriving_over_a_slice_renumbers_it_from_zero``.
        """
        with app.app_context():
            calendar = calendar_for(seed_user["user"].id)

            assert len(calendar.periods) == PERIOD_COUNT
            assert calendar.opening_bound() == FIRST_PAYDAY
            assert [period.period_index for period in calendar.periods] == list(
                range(PERIOD_COUNT),
            )

    def test_it_carries_the_owner_it_was_asked_about(self, app, seed_user):
        """The calendar records its owner so a rule cannot resolve against another's.

        Nothing reads it off the rows: an owner with no paydays has no row to
        read it from, and that owner is legal (a companion).
        """
        with app.app_context():
            assert calendar_for(seed_user["user"].id).user_id == seed_user["user"].id

    def test_it_sees_only_the_owners_own_paydays(
        self, app, db, seed_user, seed_second_user,
    ):
        """Two owners' schedules must not merge into one calendar.

        A merged calendar would derive each owner's period ends from the
        OTHER's paydays -- a plausible wrong answer rather than an error, which
        is the failure mode this whole arc is about.
        """
        with app.app_context():
            second = seed_second_user["user"].id
            pay_period_write.record_paydays(
                user_id=second,
                first_payday=FIRST_PAYDAY + timedelta(days=7),
                num_periods=3,
                cadence_days=CADENCE,
            )
            db.session.commit()

            mine = calendar_for(seed_user["user"].id)
            theirs = calendar_for(second)

            assert len(mine.periods) == PERIOD_COUNT
            # The second user keeps their own bootstrap, so 1 + 3.
            assert len(theirs.periods) == 4
            assert not {p.period_id for p in mine.periods} & {
                p.period_id for p in theirs.periods
            }

    def test_an_owner_with_no_paydays_loads_an_empty_calendar(
        self, app, db, seed_user,
    ):
        """Answered, never refused, and this is the C2-b1 cadence rule live.

        The owner ``resolve_cadence`` answers ``None`` for, so it proves the
        rule ADMITS the empty pairing rather than raising on it.

        **Why that matters is SCHEDULED, not present-day**, and an adversarial
        review of this step corrected a docstring that said otherwise: today the
        only zero-payday owner is the companion, whom ``require_owner`` 404s
        before any calendar is built.  ``balance:X-ad`` (ruling R-DB) stops
        registration writing a bootstrap payday, and then a brand-new owner
        holds none and reaches ``/templates`` on their first visit.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            db.session.query(PayPeriod).filter_by(user_id=user_id).delete(
                synchronize_session=False,
            )
            # The schedule ROW goes too, and since plan step C3-b it has to be
            # said: the cadence rule makes every batch that records a payday
            # store one, so "no paydays" no longer implies "no cadence".  The
            # owner this test is about has neither -- a brand-new sign-up
            # before their first generate.
            db.session.query(PaySchedule).filter_by(user_id=user_id).delete(
                synchronize_session=False,
            )
            db.session.commit()

            assert pay_schedule_service.resolve_cadence(user_id) is None

            calendar = calendar_for(user_id)

            assert calendar.periods == ()
            assert calendar.cadence_days is None
            assert calendar.horizon() is None
            assert calendar.opening_bound() is None


class TestTheEndsAreDerivedRatherThanRead:
    """The loader reads ``start_date`` alone; C4 drops the other two columns."""

    @pytest.mark.usefixtures("seed_periods")
    def test_the_stored_columns_are_reproduced_on_a_contiguous_schedule(
        self, app, seed_user,
    ):
        """C1's proof, through the door: derived == stored on production's shape.

        Production carries 61 contiguous paydays and 0 rows where the derivation
        disagrees with either stored column (re-verified 2026-08-10), so on this
        shape the cutover moves nothing at all.
        """
        with app.app_context():
            stored = {
                period.start_date: (period.end_date, period.period_index)
                for period in pay_period_service.get_all_periods(seed_user["user"].id)
            }

            calendar = calendar_for(seed_user["user"].id)

            assert len(calendar.periods) == len(stored)
            for period in calendar.periods:
                end, index = stored[period.start_date]
                assert period.end_date == end, period.start_date
                assert period.period_index == index, period.start_date

    def test_a_stored_hole_is_absorbed_rather_than_reproduced(
        self, app, db, seed_user,
    ):
        """Ledger row **P27**, pinned: the one behaviour change the cutover makes.

        ``seed_user``'s bootstrap is stored as 2024-01-05..2024-01-18 and the
        schedule then jumps to 2026-01-02, which the write door accepts.  The
        derived calendar TILES, so that first period ends the day before the
        next payday instead: the hole is not reported, it is absorbed.

        Both halves are asserted.  The stored row still says one thing and the
        derived period says another, and until plan step C4 drops the column
        that disagreement IS what this leaf changes.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bootstrap_start = seed_user["bootstrap_period"].start_date
            stored_end = seed_user["bootstrap_period"].end_date
            _gapped_schedule(db.session, user_id, seed_user["bootstrap_period"])

            calendar = calendar_for(user_id)
            first = calendar.periods[0]

            assert first.start_date == bootstrap_start
            assert stored_end == bootstrap_start + timedelta(days=CADENCE - 1)
            assert first.end_date == FIRST_PAYDAY - timedelta(days=1)
            assert first.end_date > stored_end

            # The consequence, stated as the answer a consumer gets: a day the
            # stored schedule covered with NO period is now inside one.
            in_the_hole = stored_end + timedelta(days=30)
            covering = calendar.period_containing(in_the_hole)
            assert covering is not None
            assert covering.start_date == bootstrap_start

    @pytest.mark.usefixtures("seed_periods")
    def test_the_last_period_takes_its_end_from_the_cadence(self, app, seed_user):
        """The one derived end that is a PROJECTION, and it says so.

        ``end_is_projected`` cannot be recomputed by a consumer holding a single
        period out of its calendar, which is why it rides on the value.
        """
        with app.app_context():
            calendar = calendar_for(seed_user["user"].id)
            last = calendar.periods[-1]

            assert last.end_is_projected is True
            assert last.end_date == last.start_date + timedelta(days=CADENCE - 1)
            assert [p.end_is_projected for p in calendar.periods[:-1]] == [False] * (
                PERIOD_COUNT - 1
            )


@pytest.mark.usefixtures("seed_periods")
class TestTheCadenceComesFromTheScheduleService:
    """One resolver, so plan finding P8's fallback is not copied into this door."""

    def test_it_takes_the_stored_cadence_when_a_schedule_row_exists(
        self, app, db, seed_user,
    ):
        """The preferred source: ``budget.pay_schedule.cadence_days``.

        Stored as a value the FALLBACK cannot produce, which an adversarial
        review of this step is why: an earlier draft stored 14, the same number
        ``resolve_cadence`` infers from the fixture's own period lengths, so the
        one test claiming to prove the loader reads that table passed
        identically against a loader that never touched it.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            pay_schedule_service.upsert_schedule(user_id, CADENCE + 7)
            db.session.commit()

            assert calendar_for(user_id).cadence_days == CADENCE + 7

    def test_it_infers_the_cadence_when_no_schedule_row_exists(
        self, app, db, seed_user,
    ):
        """P8's circular fallback, reached rather than reimplemented.

        **The row is DELETED here, and until plan step C3-b it did not have to
        be.**  No fixture in this suite wrote a ``budget.pay_schedule`` row, so
        this was the path every other test took; C3-b's cadence rule makes
        every batch that records a payday store one, so the state finding
        **P8** is about now has to be constructed on purpose.  That is the
        finding narrowing rather than the test weakening: no door can produce a
        payday-bearing owner without a cadence any more, so what is left is
        legacy data, and this is what the loader does with it.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            db.session.query(PaySchedule).filter_by(user_id=user_id).delete(
                synchronize_session=False,
            )
            db.session.commit()

            assert pay_schedule_service.get_schedule(user_id) is None
            assert calendar_for(user_id).cadence_days == CADENCE

    def test_a_changed_cadence_moves_the_horizon_and_only_the_horizon(
        self, app, db, seed_user,
    ):
        """The cadence is read for exactly one day, and this proves the scope.

        It is also the one way the loaded calendar can disagree with the stored
        ``end_date`` on a CONTIGUOUS schedule: every interior end is dictated by
        the next payday, so a schedule row edited after generation moves the
        last end alone.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            before = calendar_for(user_id)

            pay_schedule_service.upsert_schedule(user_id, CADENCE + 7)
            db.session.commit()
            after = calendar_for(user_id)

            assert [p.end_date for p in after.periods[:-1]] == [
                p.end_date for p in before.periods[:-1]
            ]
            assert after.horizon() == before.horizon() + timedelta(days=7)


class TestItAnswersWhatTheRecurrenceCalendarAnswers:
    """The equivalence oracle for plan step C2-b2's cutover.

    Every question ``PeriodCalendar`` exposes, asked of both values over a real
    schedule.  It is what makes the cutover a proven swap; it retires with the
    class it compares against.
    """

    @staticmethod
    def _both(user_id):
        """Return the two calendars for *user_id*, built the two ways.

        Args:
            user_id: The owning user.

        Returns:
            A ``(PayCalendar, PeriodCalendar)`` pair.
        """
        return (
            calendar_for(user_id),
            PeriodCalendar.from_pay_periods(
                pay_period_service.get_all_periods(user_id), user_id,
            ),
        )

    @pytest.mark.usefixtures("seed_periods")
    def test_the_bounds_agree(self, app, seed_user):
        """``opening_bound`` and ``horizon`` bound every generation pass."""
        with app.app_context():
            new, old = self._both(seed_user["user"].id)

            assert new.opening_bound() == old.opening_bound()
            assert new.horizon() == old.horizon()

    @pytest.mark.usefixtures("seed_periods")
    def test_every_day_of_the_schedule_places_identically(self, app, seed_user):
        """The two placement searches, over every covered day plus a margin.

        Walked day by day rather than sampled: the disagreements this oracle
        exists to catch are at period BOUNDARIES, and a sampler stepping by
        anything but one day could step over every one of them.
        """
        with app.app_context():
            new, old = self._both(seed_user["user"].id)

            day = new.opening_bound() - timedelta(days=5)
            last = new.horizon() + timedelta(days=5)
            checked = 0
            while day <= last:
                mine, theirs = new.period_containing(day), old.period_containing(day)
                assert (mine is None) == (theirs is None), day
                if mine is not None:
                    assert mine.period_id == theirs.period_id, day
                    assert mine.start_date == theirs.start_date, day
                    assert mine.end_date == theirs.end_date, day
                    assert mine.period_index == theirs.period_index, day

                mine = new.period_starting_on_or_after(day)
                theirs = old.period_starting_on_or_after(day)
                assert (mine is None) == (theirs is None), day
                if mine is not None:
                    assert mine.period_id == theirs.period_id, day
                    assert mine.start_date == theirs.start_date, day

                day += timedelta(days=1)
                checked += 1

            # 10 periods x 14 days = 140 covered days (opening 2026-01-02
            # through horizon 2026-05-21 inclusive), plus the 5-day margin on
            # each side.  Asserted so the loop cannot silently walk nothing.
            assert checked == PERIOD_COUNT * CADENCE + 10

    @pytest.mark.usefixtures("seed_periods")
    def test_every_stored_id_resolves_identically(self, app, seed_user):
        """``period_by_id``, plus the two ways it answers ``None``."""
        with app.app_context():
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            new, old = self._both(seed_user["user"].id)

            for period in periods:
                mine, theirs = new.period_by_id(period.id), old.period_by_id(period.id)
                assert mine is not None and theirs is not None, period.id
                assert mine.start_date == theirs.start_date
                assert mine.period_index == theirs.period_index

            assert new.period_by_id(None) is old.period_by_id(None) is None
            unknown = max(period.id for period in periods) + 1000
            assert new.period_by_id(unknown) is old.period_by_id(unknown) is None

    @pytest.mark.usefixtures("seed_periods")
    def test_every_month_the_schedule_touches_agrees(self, app, seed_user):
        """``earliest_start_in_month``, over each month plus one either side."""
        with app.app_context():
            new, old = self._both(seed_user["user"].id)

            months = {
                (period.start_date.year, period.start_date.month)
                for period in new.periods
            }
            months |= {(2025, 12), (2027, 1)}
            answered = 0
            for year, month in sorted(months):
                mine = new.earliest_start_in_month(year, month)
                assert mine == old.earliest_start_in_month(year, month), (year, month)
                answered += mine is not None

            # The schedule spans enough months that this is not vacuously
            # asserting ``None == None`` everywhere.
            assert answered >= 4

    def test_they_diverge_on_a_hole_and_that_is_the_ruled_change(
        self, app, db, seed_user,
    ):
        """The oracle's own blind spot, made explicit (ledger row **P27**).

        Every schedule above is contiguous, so every assertion above would hold
        even if the derivation absorbed holes silently -- which it does.  This
        is the control that says so: on a GAPPED schedule the two calendars
        answer differently, deliberately, and a reader taking the agreement
        above as "the cutover changes nothing" would be wrong.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            stored_end = seed_user["bootstrap_period"].end_date
            _gapped_schedule(db.session, user_id, seed_user["bootstrap_period"])

            new, old = self._both(user_id)
            in_the_hole = stored_end + timedelta(days=30)

            assert old.period_containing(in_the_hole) is None
            assert new.period_containing(in_the_hole) is not None

    @pytest.mark.usefixtures("seed_periods")
    def test_they_diverge_when_the_stored_cadence_outlives_the_stored_end(
        self, app, db, seed_user,
    ):
        """The SECOND divergence class, and the oracle above cannot see it.

        Every agreeing assertion in this class is arithmetically forced on the
        cadence axis: no fixture writes a ``budget.pay_schedule`` row, so
        ``resolve_cadence`` falls back to ``(end - start).days + 1``, which is
        the exact inverse of the ``end = start + cadence - 1`` the generator
        wrote.  ``new.horizon() == old.horizon()`` therefore cannot fail on a
        generated schedule however the loader treats the cadence -- an
        adversarial review of this step measured that, and this is the control
        it is owed.

        The door is live rather than hypothetical: plan finding **P12** --
        ``routes/pay_periods.py:98`` reaches ``upsert_schedule`` even when the
        batch created nothing -- lets a user rewrite the stored cadence without
        touching a single period, and after plan step C2-b2 the recurrence
        horizon moves with it.  Ledger row **P28**.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            stored_horizon = calendar_for(user_id).horizon()

            # LENGTHENED: the derived horizon runs past the last stored end, so
            # generation would place rows in days no stored period covers.
            pay_schedule_service.upsert_schedule(user_id, CADENCE + 7)
            db.session.commit()
            longer, old = self._both(user_id)
            assert old.horizon() == stored_horizon
            assert longer.horizon() == stored_horizon + timedelta(days=7)

            # SHORTENED: eleven days of a real, id-bearing pay period stop
            # being covered by any period at all.
            pay_schedule_service.upsert_schedule(user_id, 3)
            db.session.commit()
            shorter, old = self._both(user_id)
            assert shorter.horizon() == stored_horizon - timedelta(days=11)
            assert old.period_containing(stored_horizon) is not None
            assert shorter.period_containing(stored_horizon) is None


@pytest.mark.usefixtures("seed_periods")
class TestThePartialSetHazardIsRealAndTheDoorIsWhatClosesIt:
    """Ledger row **P26**, pinned so C2-b2 inherits a test rather than a claim."""

    def test_deriving_over_a_slice_renumbers_it_from_zero(self, app, seed_user):
        """The silent half: ordinals, which are the ``Every N Periods`` phase key.

        ``PeriodCalendar.from_pay_periods`` COPIES ``period_index`` off each ORM
        row, so a slice keeps its true ordinals; the derivation computes the
        ordinal as a position in the set it is HANDED.  A rule whose phase is
        ``(period_index - offset) % interval_n`` therefore re-phases against
        ordinals naming no real paycheck.
        """
        with app.app_context():
            tail = pay_period_service.get_all_periods(seed_user["user"].id)[6:]

            sliced = PayCalendar.from_paydays(
                [(period.id, period.start_date) for period in tail],
                CADENCE,
                seed_user["user"].id,
            )
            kept = PeriodCalendar.from_pay_periods(tail, seed_user["user"].id)

            assert [period.period_index for period in kept.periods] == [6, 7, 8, 9]
            assert [period.period_index for period in sliced.periods] == [0, 1, 2, 3]

    def test_the_loader_cannot_be_asked_for_a_slice(self, app, seed_user):
        """Which is the remedy: one door, and it takes a user id and nothing else.

        The value cannot detect partiality -- a slice of paydays is
        indistinguishable from a short schedule -- so the guarantee has to be
        structural at the CALLER, and the loader's signature is where it is
        made.  Asserted on the signature rather than by calling it wrongly: the
        rule being protected is "no window argument is ever added here", and a
        ``TypeError`` from an extra positional would only be testing Python.
        """
        with app.app_context():
            assert list(signature(calendar_for).parameters) == ["user_id"]
            assert len(calendar_for(seed_user["user"].id).periods) == PERIOD_COUNT
