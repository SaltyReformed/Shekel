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

**The two shapes those controls were written for outlive the columns**, and
plan step ``pay_calendar:C4-c`` restated both on the paydays rather than
deleting them.  A payday JUMP leaves ONE long paycheck rather than a hole
(ledger row **P27**): that used to need a doctored ``end_date`` and now needs
only two paydays far apart, which is a legal write.  And the stored CADENCE
still moves the derived horizon (row **P28**), measured now against the
horizon this calendar answered a statement earlier rather than against a
column -- which is what that control always needed, because on a generated
schedule the two agree by ARITHMETIC whatever the loader does with the cadence
(``resolve_cadence`` answers the cadence the batch was generated at, and
``end = start + cadence - 1`` is its exact inverse).

The contiguous shape is the ``seed_periods`` fixture, which RESETS the owner's
whole schedule through ``pay_period_admin.reset_pay_periods``, so the writer
derives ``period_index`` 0..9 when it writes the rows.  *Nothing renumbers
anything since plan step ``pay_calendar:C4-b-1``*: this said the fixture "drops
the 2024 bootstrap period and RENUMBERS what is left", which was true of the
135-line helper that step deleted.  Why it matters is unchanged -- an earlier
draft of this module deleted the bootstrap row by hand, left the stored
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
    calendar_at_schedule,
    calendar_for,
)
from tests._test_helpers import all_periods, rhythm_of

#: ``seed_periods``' first payday, restated so the assertions below name a value
#: rather than a bare literal.  Changing the fixture without changing this is
#: caught by ``test_it_loads_every_payday_the_owner_has``.
FIRST_PAYDAY = date(2026, 1, 2)

#: ``seed_periods``' cadence, which is also production's stored cadence
#: (measured 2026-08-08: ``budget.pay_schedule.cadence_days`` = 14).
CADENCE = 14

#: How many paydays ``seed_periods`` builds.
PERIOD_COUNT = 10


def _schedule_with_a_payday_jump(db_session, user_id):
    """Keep the 2024 bootstrap payday and record the 2026 schedule after it.

    **A TWO-YEAR gap between two paydays, and it is a legal write.**  The
    forward-only floor bounds a new payday at one cadence past the latest, and
    2026-01-02 is far past 2024-01-19, so the writer accepts it -- which is what
    an owner who set up an account and came back later actually produces.

    The bootstrap paycheck then runs from 2024-01-05 to 2026-01-01, because a
    period ends the day before the NEXT payday.  *Plan finding **P2** was the
    stored form of this: a schedule could hold days no period covered, since
    ``end_date`` was written independently of the next payday and
    ``_reject_overlapping_batch`` refused OVERLAPS and not GAPS.  Plan step
    ``pay_calendar:C4-c`` dropped the column, so the days between two paydays
    belong to the earlier one and there is nothing left to fail to cover.*

    A test wanting the jump opts in here; every other test takes
    ``seed_periods``, which RESETS the calendar and drops the bootstrap.

    Args:
        db_session: The session.
        user_id: The owning user.

    Returns:
        The generated :class:`~app.models.pay_period.PayPeriod` rows.
    """
    periods = pay_period_write.record_paydays(
        user_id=user_id,
        first_payday=FIRST_PAYDAY,
        num_periods=PERIOD_COUNT,
        rhythm=rhythm_of(CADENCE),
    )
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
                rhythm=rhythm_of(CADENCE),
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

    def test_an_owner_with_NO_SCHEDULE_ROW_is_REFUSED(
        self, app, db, seed_user,
    ):
        """No ``budget.pay_schedule`` row means no calendar (plan step C4-d).

        **This case inverted at plan step C4-d** (ruling **R-PC45**) and is
        kept rather than deleted, because the inversion is the step.  It
        asserted an EMPTY calendar carrying ``cadence_days is None``, which was
        the last construction site of the absent cadence and the reason
        ``int | None`` travelled into ``PayCalendar``, ``derive_periods`` and
        three projection producers.

        *The reason the old answer was called load-bearing had already
        expired.*  It cited ``balance:X-ad`` (ruling R-DB): registration would
        stop writing a bootstrap payday, so a brand-new owner would hold none
        and reach ``/templates`` on their first visit, and a raising loader
        would 500 that page.  ``balance:X-ad-a`` SHIPPED (``2a4eb477``) and
        made it false -- registration asks for the real payday, cadence and
        horizon and writes ``num_periods`` paydays -- so a brand-new owner
        holds a schedule row and a schedule.  The scheduled reason arrived and
        was refuted; C4-d re-measured it rather than inheriting it.

        The owner constructed here is the companion shape, whom
        ``require_owner`` 404s before any calendar is built.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            db.session.query(PayPeriod).filter_by(user_id=user_id).delete(
                synchronize_session=False,
            )
            # Periods FIRST: ``fk_pay_periods_schedule`` is ON DELETE RESTRICT
            # since plan step C4-b-2, so the parent cannot go under live
            # children.  The schedule ROW goes too, and since plan step C3-b it
            # has to be said: the cadence rule makes every batch that records a
            # payday store one, so "no paydays" does not imply "no cadence".
            # The owner this test is about has neither.
            db.session.query(PaySchedule).filter_by(user_id=user_id).delete(
                synchronize_session=False,
            )
            db.session.commit()

            # The premise, asserted rather than assumed: this owner really
            # holds no schedule row, so the refusal below is about the state
            # under test and not about a fixture that changed.
            assert pay_schedule_service.resolve_schedule(user_id) is None

            with pytest.raises(PayCalendarError, match="no pay calendar"):
                calendar_for(user_id)

    def test_an_owner_with_a_SCHEDULE_and_no_paydays_loads_an_EMPTY_calendar(
        self, app, db, seed_user,
    ):
        """The empty calendar that survives C4-d, and it carries a real cadence.

        The other half of the case above, and the one that keeps the refusal
        honest: "no calendar for an owner with no schedule row" must not become
        "no calendar for an owner with no paydays".  This owner is ordinary --
        ``pay_period_admin.reset_pay_periods`` passes through exactly this
        state, and so does any owner between their schedule row being written
        and their first batch landing.

        The cadence is asserted to be the STORED one rather than merely
        non-``None``: an empty calendar reading somebody else's schedule would
        satisfy the weaker claim.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            db.session.query(PayPeriod).filter_by(user_id=user_id).delete(
                synchronize_session=False,
            )
            db.session.commit()
            pay_schedule_service.upsert_schedule(user_id, rhythm_of(CADENCE + 7))
            db.session.commit()

            calendar = calendar_for(user_id)

            assert calendar.periods == ()
            assert calendar.cadence_days == CADENCE + 7
            assert calendar.cadence.cadence_days == CADENCE + 7
            assert calendar.horizon() is None
            assert calendar.opening_bound() is None


class TestTheEndsAreDerivedRatherThanRead:
    """The loader reads ``start_date`` alone, and since ``C4-c`` that is all there is.

    *This class carried C1's proof -- derived == stored, row by row, on
    production's shape -- until plan step ``pay_calendar:C4-c`` dropped both
    columns.  That comparison has no second side now; the proof it was is in
    migration ``b7a41e2c9d63``'s docstring, measured on production itself (63
    rows, 0 disagreements) on the day the columns went.*
    """

    def test_a_payday_JUMP_leaves_one_long_period_rather_than_a_hole(
        self, app, db, seed_user,
    ):
        """Ledger row **P27**: the days between two paydays belong to the earlier one.

        ``seed_user``'s bootstrap payday is 2024-01-05 and the schedule then
        jumps to 2026-01-02, which the write door accepts -- an owner who set
        up an account and came back two years later.  The derived calendar
        TILES, so that first paycheck runs to 2026-01-01 rather than stopping a
        fortnight in and leaving 714 days funded by nothing.

        A day deep inside the jump is the consequence stated as the answer a
        consumer gets, and it is asserted rather than inferred: it resolves to
        the bootstrap paycheck.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bootstrap_start = seed_user["bootstrap_period"].start_date
            _schedule_with_a_payday_jump(db.session, user_id)

            calendar = calendar_for(user_id)
            first = calendar.periods[0]

            assert first.start_date == bootstrap_start
            assert first.end_date == FIRST_PAYDAY - timedelta(days=1)
            # A fortnight in -- where the pre-C4-c stored end fell, and where a
            # writer following ``start + cadence - 1`` would still stop.
            assert first.end_date > bootstrap_start + timedelta(
                days=CADENCE - 1,
            )

            mid_jump = bootstrap_start + timedelta(days=CADENCE + 30)
            covering = calendar.period_containing(mid_jump)
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
            pay_schedule_service.upsert_schedule(user_id, rhythm_of(CADENCE + 7))
            db.session.commit()

            assert calendar_for(user_id).cadence_days == CADENCE + 7

    # ``test_it_infers_the_cadence_when_no_schedule_row_exists`` stood here
    # until plan step **C4-b-2** and was DELETED with its subject, not with
    # its coverage.  It built an owner with paydays and no
    # ``budget.pay_schedule`` row and asserted the loader inferred ``CADENCE``
    # from their period lengths -- an answer that was circular (the stored end
    # it read is derived FROM the cadence since C3-b) and unbounded above,
    # which is ledger row **P35**.  ``fk_pay_periods_schedule`` makes that
    # owner unstorable, so the loader cannot meet one; the constraint itself
    # is graded in ONE place,
    # ``tests/test_models/test_c4b2_pay_period_schedule_key.py``, rather than
    # restated at every reader that used to have to cope with it.

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

            pay_schedule_service.upsert_schedule(user_id, rhythm_of(CADENCE + 7))
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

    **The two DIVERGENCE controls survive it, and they are the half that had to
    outlive the oracle.**  Both were measured against the stored columns until
    plan step ``pay_calendar:C4-c`` dropped them, and both were restated rather
    than retired:

    * a payday JUMP leaves ONE long paycheck (ledger row **P27**), asserted in
      ``TestTheEndsAreDerivedRatherThanRead`` on two far-apart paydays;
    * the stored CADENCE moves the derived horizon (row **P28**), below,
      measured against the horizon this calendar answered a statement earlier.
    """

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
        batch created nothing -- as the door that rewrites a cadence without
        touching a period.  Plan step **C3-b** closed it: the route goes
        through ``pay_period_write.record_paydays``, which upserts the cadence
        only when it is recording paydays.  ``upsert_schedule`` has exactly one
        caller in ``app/`` and that is it, verified 2026-08-11.  What survives
        is a direct database write -- which is what makes this a CONTROL for a
        value the reader must answer for rather than a reproduction of a
        reachable bug, and it is why the fixture below calls the service twice
        instead of driving a route.

        **The baseline is the horizon this calendar answered a statement
        earlier**, not a stored column: plan step ``pay_calendar:C4-c`` dropped
        ``end_date``, and the last payday plus the cadence is what the horizon
        always was.  The measurement is still a real one, because the cadence
        is CHANGED between the reads and the horizon has to move by exactly the
        difference in both directions -- a loader that froze the cadence, or
        read a stale one, fails here.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            last_payday = max(
                period.start_date
                for period in all_periods(user_id)
            )
            baseline = calendar_for(user_id).horizon()
            assert baseline == last_payday + timedelta(days=CADENCE - 1)

            # LENGTHENED: the derived horizon runs a week further out, so
            # generation places rows in days it did not reach before.
            pay_schedule_service.upsert_schedule(user_id, rhythm_of(CADENCE + 7))
            db.session.commit()
            assert calendar_for(user_id).horizon() == (
                baseline + timedelta(days=7)
            )

            # SHORTENED: eleven days of the last paycheck stop being covered by
            # any period at all, and the day that was the horizon is now past
            # the end of the schedule.
            pay_schedule_service.upsert_schedule(user_id, rhythm_of(3))
            db.session.commit()
            shorter = calendar_for(user_id)
            assert shorter.horizon() == baseline - timedelta(days=11)
            assert shorter.period_containing(baseline) is None


@pytest.mark.usefixtures("seed_periods")
class TestThePartialSetHazardIsRealAndTheDoorIsWhatClosesIt:
    """Ledger row **P26**, pinned so C2-b2 inherits a test rather than a claim."""

    def test_deriving_over_a_slice_renumbers_it_from_zero(self, app, seed_user):
        """The silent half: ordinals, which are the ``Every N Periods`` phase key.

        The ordinal is a position in the set the derivation is HANDED, so the
        SAME four rows come back numbered 6..9 out of the owner's whole
        schedule and 0..3 out of a slice of it.  A rule whose phase is
        ``(period_index - offset) % interval_n`` therefore re-phases against
        ordinals naming no real paycheck.

        **Both sides are the derivation now**, because plan step
        ``pay_calendar:C4-c`` dropped the stored ordinal this used to compare
        against.  That does not weaken it: what the hazard IS is one row
        answering two ordinals depending on how much of the schedule was
        passed, and the two calls below are exactly that.
        """
        with app.app_context():
            whole = calendar_for(seed_user["user"].id)
            tail = whole.saved().periods[6:]

            sliced = PayCalendar.from_paydays(
                [(period.period_id, period.start_date) for period in tail],
                CADENCE,
                seed_user["user"].id,
                history_opens_on=None,
            )

            assert [period.period_index for period in tail] == [6, 7, 8, 9]
            assert [period.period_index for period in sliced.periods] == [0, 1, 2, 3]
            assert [period.period_id for period in sliced.periods] == [
                period.period_id for period in tail
            ], "the same four ROWS, renumbered -- not four different periods"

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
class TestCalendarAtSchedule:
    """The second loader door, and the equivalence the refactor rests on.

    Plan step **C4** added :func:`calendar_at_schedule` for the rolling top-up,
    which holds the owner's ``budget.pay_schedule`` row already and would
    otherwise pay for a second read of it, and REFACTORED
    :func:`calendar_for` to delegate to it.  That refactor is behaviour-
    preserving only if the two answer identically for a resolvable owner, so
    the equivalence is pinned rather than argued.

    **It took a bare cadence and was called ``calendar_at_cadence`` until plan
    step balance:X-bh-2**, which gave the calendar a second fact off the same
    row; the parameter is the pair now, so a caller cannot supply one owner's
    cadence beside another's history bound.
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
            resolved = pay_schedule_service.resolve_schedule(user_id)

            assert resolved.cadence_days == CADENCE
            assert calendar_at_schedule(user_id, resolved) == calendar_for(user_id)
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

            stored = calendar_at_schedule(
                user_id,
                pay_schedule_service.ScheduleFacts(CADENCE, None),
            )
            supplied = calendar_at_schedule(
                user_id,
                pay_schedule_service.ScheduleFacts(CADENCE + 7, None),
            )

            assert supplied != stored
            assert supplied.periods[-1].end_date == (
                stored.periods[-1].end_date + timedelta(days=7)
            )
            # Every OTHER end is dictated by the next payday, so the cadence
            # cannot have moved it.
            assert [p.end_date for p in supplied.periods[:-1]] == [
                p.end_date for p in stored.periods[:-1]
            ]

    def test_a_missing_cadence_is_REFUSED_whatever_the_payday_set(
        self, app, db, seed_user,
    ):
        """A cadence-less ``ScheduleFacts`` cannot become a calendar here either.

        **The refusal changed shape at plan step C4-d** (ruling **R-PC45**).
        It was "``None`` beside a PAYDAY is a broken invariant" -- conditional
        on the payday set, because ``None`` beside an empty one was legal.  A
        cadence is required outright now, so this door refuses the value
        whichever way the owner's paydays fall, and the message is
        ``validate_cadence``'s.

        Graded on BOTH payday shapes rather than on the seeded one alone: the
        old rule passes a test of the non-empty shape unchanged, so a case that
        checked only that would not distinguish the two rules.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            cadence_less = pay_schedule_service.ScheduleFacts(None, None)

            with pytest.raises(PayCalendarError, match="must be a plain int"):
                calendar_at_schedule(user_id, cadence_less)

            db.session.query(PayPeriod).filter_by(user_id=user_id).delete(
                synchronize_session=False,
            )
            db.session.commit()

            with pytest.raises(PayCalendarError, match="must be a plain int"):
                calendar_at_schedule(user_id, cadence_less)

    def test_it_carries_the_history_bound_it_is_GIVEN(self, app, seed_user):
        """The second fact travels too, and it is a fact about the calendar.

        Plan step **balance:X-bh-2**.  The rolling top-up asks this door a
        question that never reads ``history_opens_on`` -- how many paychecks
        are still ahead -- so the bound could have been dropped here and no
        current caller would notice.  Dropping it would make the value LIE:
        a calendar claiming an unbounded rhythm for an owner who stated one,
        handed to any producer that does read it, is a wrong figure rather
        than an error.  ``PayCalendar`` compares on its facts, so two
        otherwise identical calendars must differ on this one.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            stated = date(2020, 6, 1)

            bounded = calendar_at_schedule(
                user_id, pay_schedule_service.ScheduleFacts(CADENCE, stated),
            )
            unbounded = calendar_at_schedule(
                user_id, pay_schedule_service.ScheduleFacts(CADENCE, None),
            )

            assert bounded.history_opens_on == stated
            assert unbounded.history_opens_on is None
            assert bounded != unbounded
            assert bounded.periods == unbounded.periods
