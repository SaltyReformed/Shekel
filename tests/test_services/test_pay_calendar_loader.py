"""The one database door onto the pay calendar (plan step C2-b1).

``pay_calendar.calendar_for`` is the only impure thing in an otherwise pure
package: it reads an owner's paydays and cadence and hands them to the
derivation.  It was proven here BEFORE any consumer depended on it -- nothing
in ``app/`` called it until plan step C2-d, and plan step **C2-b2** then moved
the ten ``recurrence.calendar_for`` sites onto it and deleted the calendar they
used.  That ordering is the whole point of the decomposition, and it is the
technique C1 and C2-a used.

**The equivalence oracle that used to be the load-bearing test here is GONE**,
and its going is plan step C2-b2.  It drove the loaded
:class:`~app.services.pay_calendar.PayCalendar` and the
``recurrence.PeriodCalendar`` side by side over a real schedule so the cutover
would be a proven swap rather than a hopeful one; the cutover deleted the class
it compared against, exactly as its own docstring said it would.

**The TWO shapes where the derivation and the stored COLUMNS diverge outlive
it**, restated against those columns so nothing depends on a second value
object: a stored HOLE is absorbed rather than reproduced (ledger row **P27**),
and the stored CADENCE moves the derived horizon while the stored ``end_date``
stays put (row **P28**).  The second matters most because this suite cannot see
it by accident: on a generated schedule the horizon agrees by ARITHMETIC,
whatever the loader does with the cadence -- ``resolve_cadence`` answers the
cadence the batch was generated at and ``end = start + cadence - 1`` is its
exact inverse.  Both controls die at plan step **C4** with the columns they
read.

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
from app.services import pay_period_write, pay_schedule_service
from app.services.pay_calendar import (
    PayCalendar,
    PayCalendarError,
    calendar_at_cadence,
    calendar_for,
)
from tests._test_helpers import open_calendar_hole, all_periods

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
                for period in all_periods(seed_user["user"].id)
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


class TestTheDerivedCalendarDivergesFromTheStoredColumns:
    """The TWO shapes where the derivation and the stored columns part company.

    **What used to sit here was an equivalence ORACLE** -- every question the
    recurrence arc's ``PeriodCalendar`` exposed, asked of both values over a
    real schedule, so plan step C2-b2's cutover would be a proven swap rather
    than a hopeful one.  It retired with the class it compared against, exactly
    as its own docstring said it would; the cutover it was written for is what
    deleted it.

    **The two DIVERGENCE controls survive it, and they are the half that has to
    outlive the oracle.**  Both are measured against the stored columns rather
    than against a second value object, so neither depends on a class existing:

    * a stored HOLE is ABSORBED rather than reproduced (ledger row **P27**);
    * the stored CADENCE moves the derived horizon while the stored
      ``end_date`` stays where it was (row **P28**).

    Both die at plan step **C4**, with the columns they compare against.
    """

    def test_a_stored_hole_is_covered_by_the_derived_calendar(
        self, app, db, seed_user,
    ):
        """Ledger row **P27**, stated as a test rather than as a claim.

        A schedule written before plan step C3-b can leave days no STORED
        period covers.  The derivation does not report such a day, it absorbs
        it: the preceding paycheck runs to the day before the next payday.
        This is the ruled model working, and it is why plan step C2-b2 could
        delete the recurrence engine's schedule-gap report -- and why
        ``integrity_check`` **BA-07** exists to ask the question of the stored
        rows instead.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            stored_end = seed_user["bootstrap_period"].end_date
            _gapped_schedule(db.session, user_id, seed_user["bootstrap_period"])
            in_the_hole = stored_end + timedelta(days=30)

            # The premise: no STORED period covers the day.
            assert not any(
                period.start_date <= in_the_hole <= period.end_date
                for period in all_periods(user_id)
            )

            covering = calendar_for(user_id).period_containing(in_the_hole)

            assert covering is not None
            assert covering.start_date <= stored_end
            assert covering.end_date > stored_end, (
                "the absorbing period must run PAST its own stored end -- that "
                "is what makes this an absorption rather than a period that "
                "already covered the day"
            )

    @pytest.mark.usefixtures("seed_periods")
    def test_the_derived_horizon_moves_when_the_stored_cadence_moves(
        self, app, db, seed_user,
    ):
        """Ledger row **P28**: the SECOND divergence, and it is invisible by default.

        On any GENERATED schedule the derived horizon reproduces the stored one
        BY ARITHMETIC: ``resolve_cadence`` answers the very cadence the batch
        was generated at, and ``end = start + cadence - 1`` is its exact
        inverse.  So an agreement test on this axis cannot fail however the
        loader treats the cadence, which an adversarial review of plan step
        C2-b1 measured.  This is the control it is owed, and it asserts the
        divergence in BOTH directions.

        **No live door reaches this state, and the citation that said one did
        is withdrawn.**  This paragraph named plan finding **P12** --
        ``routes/pay_periods.py`` reaching ``upsert_schedule`` even when the
        batch created nothing -- as the door that rewrites a stored cadence
        without touching a period.  Plan step **C3-b** closed it: the route
        goes through ``pay_period_write.record_paydays``, which upserts the
        cadence only when it is recording paydays and then re-materialises
        every row from the derivation (``_write_derivation``), so the stored
        end and the stored cadence agree again at the end of the write.
        ``upsert_schedule`` has exactly one caller in ``app/`` and that is it,
        verified 2026-08-11.  What survives is legacy data and a direct
        database write -- which is what makes this a CONTROL for a divergence
        the reader must still answer for rather than a reproduction of a
        reachable bug, and it is why the fixture below calls the service twice
        instead of driving a route.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            stored_horizon = max(
                period.end_date
                for period in all_periods(user_id)
            )
            assert calendar_for(user_id).horizon() == stored_horizon

            # LENGTHENED: the derived horizon runs past the last stored end, so
            # generation would place rows in days no stored period covers.
            pay_schedule_service.upsert_schedule(user_id, CADENCE + 7)
            db.session.commit()
            assert calendar_for(user_id).horizon() == (
                stored_horizon + timedelta(days=7)
            )

            # SHORTENED: eleven days of a real, id-bearing pay period stop
            # being covered by any period at all.
            pay_schedule_service.upsert_schedule(user_id, 3)
            db.session.commit()
            shorter = calendar_for(user_id)
            assert shorter.horizon() == stored_horizon - timedelta(days=11)
            assert shorter.period_containing(stored_horizon) is None
            # ...while the STORED column still says the day is covered, which
            # is the whole divergence.
            assert any(
                period.start_date <= stored_horizon <= period.end_date
                for period in all_periods(user_id)
            )


@pytest.mark.usefixtures("seed_periods")
class TestThePartialSetHazardIsRealAndTheDoorIsWhatClosesIt:
    """Ledger row **P26**, pinned so C2-b2 inherits a test rather than a claim."""

    def test_deriving_over_a_slice_renumbers_it_from_zero(self, app, seed_user):
        """The silent half: ordinals, which are the ``Every N Periods`` phase key.

        The STORED ``period_index`` rides on the row, so a slice keeps its true
        ordinals; the derivation computes the ordinal as a position in the set
        it is HANDED, so the same slice comes back 0..n-1.  A rule whose phase
        is ``(period_index - offset) % interval_n`` therefore re-phases against
        ordinals naming no real paycheck.

        Compared against the stored column rather than against a second
        calendar value: plan step C2-b2 deleted the one that copied it.
        """
        with app.app_context():
            tail = all_periods(seed_user["user"].id)[6:]

            sliced = PayCalendar.from_paydays(
                [(period.id, period.start_date) for period in tail],
                CADENCE,
                seed_user["user"].id,
            )

            assert [period.period_index for period in tail] == [6, 7, 8, 9]
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


@pytest.mark.usefixtures("seed_periods")
class TestCalendarAtCadence:
    """The second loader door, and the equivalence the refactor rests on.

    Plan step **C4** added :func:`calendar_at_cadence` for the rolling top-up,
    which holds the owner's ``budget.pay_schedule`` row already and would
    otherwise pay for a second read of it, and REFACTORED
    :func:`calendar_for` to delegate to it.  That refactor is behaviour-
    preserving only if the two answer identically for a resolvable owner, so
    the equivalence is pinned rather than argued.
    """

    def test_it_answers_exactly_what_calendar_for_answers(
        self, app, seed_user,
    ):
        """Same owner, same schedule: the two doors build EQUAL calendars.

        ``PayCalendar`` compares on its facts -- the canonicalised paydays, the
        cadence and the owner -- with the derived periods excluded from
        equality, so this asserts the periods too by construction.  Both are
        asserted non-empty first, because two empty calendars would compare
        equal and grade nothing.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            resolved = pay_schedule_service.resolve_cadence(user_id)

            assert resolved == CADENCE
            assert calendar_at_cadence(user_id, resolved) == calendar_for(user_id)
            assert len(calendar_for(user_id).periods) == PERIOD_COUNT

    def test_it_reads_the_paydays_and_NOT_the_schedule_row(
        self, app, seed_user,
    ):
        """It answers from the cadence it is GIVEN, not the one on the row.

        This is the whole point of the door: the caller has already resolved
        the cadence and must not pay for a second read.  Handing it a cadence
        the stored row does not carry proves it never looks -- and the value
        that changes is the LAST period's end, which is the only one the
        cadence decides.
        """
        with app.app_context():
            user_id = seed_user["user"].id

            stored = calendar_at_cadence(user_id, CADENCE)
            supplied = calendar_at_cadence(user_id, CADENCE + 7)

            assert supplied != stored
            assert supplied.periods[-1].end_date == (
                stored.periods[-1].end_date + timedelta(days=7)
            )
            # Every OTHER end is dictated by the next payday, so the cadence
            # cannot have moved it.
            assert [p.end_date for p in supplied.periods[:-1]] == [
                p.end_date for p in stored.periods[:-1]
            ]

    def test_a_missing_cadence_beside_paydays_is_REFUSED(
        self, app, seed_user,
    ):
        """``None`` beside a payday is a broken invariant, not a default.

        :func:`calendar_for` cannot reach this for an owner with a schedule
        row; this door can, because its caller supplies the value.  The
        documented refusal is graded rather than trusted -- the last period's
        end has no other source, and every cadence this could invent would
        project a horizon the owner never chose.
        """
        with app.app_context():
            user_id = seed_user["user"].id

            with pytest.raises(PayCalendarError, match="no cadence"):
                calendar_at_cadence(user_id, None)
