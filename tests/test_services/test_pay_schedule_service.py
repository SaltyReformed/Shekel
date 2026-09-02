"""Tests for ``pay_schedule_service`` (pay-period CRUD Phase 1).

The service owns the per-user ``budget.pay_schedule`` row: the persisted
cadence the extend / regenerate paths continue a schedule from.  Three
behaviours matter:

  * ``get_schedule`` returns the row or ``None``.
  * ``upsert_schedule`` creates a row on first call and updates only
    ``cadence_days`` on later calls, never disturbing rolling config.
  * ``resolve_cadence`` answers the STORED cadence and nothing else.  It
    used to fall back to inferring one from the last period's length for an
    owner with periods but no schedule row; plan step **C4-b-2** made that
    owner unrepresentable (``fk_pay_periods_schedule``) and deleted the arm
    with them, closing findings **P8** and **P35**.
  * ``resolve_schedule`` answers BOTH calendar facts from that one read
    (plan step **balance:X-bh-2**), and ``set_history_opening`` is the one
    writer of the second.

See ``docs/plans/implementation_plan_pay_period_crud.md``.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.exceptions import ValidationError
from app.models.pay_period import PayPeriod
from app.models.pay_schedule import PaySchedule
from app.services.pay_calendar import (
    PayCalendar,
    PayCalendarError,
    calendar_for,
    paydays_in_month_through,
)
from app.services import (
    pay_period_admin,
    pay_period_write,
    pay_schedule_service,
)


class TestGetSchedule:
    """``get_schedule`` returns the row when present, ``None`` otherwise."""

    def test_returns_none_when_user_has_no_schedule(self, app, bare_user):
        """A user with no pay_schedule row resolves to ``None``."""
        with app.app_context():
            assert (
                pay_schedule_service.get_schedule(bare_user["user"].id) is None
            )


class TestUpsertSchedule:
    """``upsert_schedule`` creates then narrowly updates the cadence."""

    def test_creates_row_with_rolling_defaults(self, app, bare_user):
        """First upsert inserts a row at the given cadence, rolling off.

        New rows take the column server-defaults: rolling disabled and a
        52-period target (the app's ~2-year horizon).
        """
        with app.app_context():
            schedule = pay_schedule_service.upsert_schedule(
                bare_user["user"].id, cadence_days=14,
            )
            assert schedule.id is not None
            assert schedule.cadence_days == 14
            assert schedule.rolling_enabled is False
            assert schedule.rolling_target_periods == 52

    def test_second_upsert_updates_cadence_only(self, app, db, bare_user):
        """A later upsert changes cadence but leaves rolling config intact.

        Capturing a new cadence (e.g. on regenerate) must never silently
        reset a user's rolling-window settings, so the rolling columns
        are left exactly as the user set them.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            schedule = pay_schedule_service.upsert_schedule(
                user_id, cadence_days=14,
            )
            # Simulate a user having turned rolling on with a custom target.
            schedule.rolling_enabled = True
            schedule.rolling_target_periods = 30
            db.session.flush()

            updated = pay_schedule_service.upsert_schedule(
                user_id, cadence_days=7,
            )

            # Same row, new cadence, rolling config untouched.
            assert updated.id == schedule.id
            assert updated.cadence_days == 7
            assert updated.rolling_enabled is True
            assert updated.rolling_target_periods == 30
            # Exactly one row for the user -- upsert did not insert a second.
            assert pay_schedule_service.get_schedule(user_id).id == schedule.id


class TestUpsertScheduleRefusesAnUnstorableCadence:
    """``upsert_schedule`` bounds the cadence itself (plan step X-ad-a).

    ``ck_pay_schedule_cadence_range`` bounds the column to 1..365, and until
    this step the only thing standing between a caller and that CHECK was each
    caller's own Marshmallow field -- a rule held by four separate
    declarations and by whoever remembered to add a fifth.  Registration was
    that fifth door.  The refusal now lives at the one writer, so the
    failure mode it removes is an ``IntegrityError`` 500 on a value a form
    could have reported.
    """

    @pytest.mark.parametrize("cadence", [0, -1, 366, 100_000])
    def test_out_of_range_cadence_raises_before_writing(
        self, app, bare_user, cadence,
    ):
        """A cadence outside 1..365 raises and writes no schedule row.

        The four values bracket both ends: 0 and -1 below the floor (a
        zero-day cadence is a schedule with no paydays; a negative one runs
        backwards), 366 one past the ceiling, and 100000 far past it.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            with pytest.raises(ValidationError, match="between 1 and 365"):
                pay_schedule_service.upsert_schedule(user_id, cadence)
            assert pay_schedule_service.get_schedule(user_id) is None

    def test_message_names_the_offending_value(self, app, bare_user):
        """The refusal quotes the value, so a surface can render it verbatim."""
        with app.app_context():
            with pytest.raises(ValidationError) as exc:
                pay_schedule_service.upsert_schedule(
                    bare_user["user"].id, 400,
                )
            assert "got 400" in str(exc.value)

    @pytest.mark.parametrize("cadence", [1, 365])
    def test_the_bounds_themselves_are_accepted(self, app, bare_user, cadence):
        """1 and 365 are INSIDE the range -- the check is inclusive.

        A test that only proved the refusals would pass just as well against
        an off-by-one that refused the endpoints too, which is the mistake
        this pins: the CHECK reads ``BETWEEN 1 AND 365``.
        """
        with app.app_context():
            schedule = pay_schedule_service.upsert_schedule(
                bare_user["user"].id, cadence,
            )
            assert schedule.cadence_days == cadence


class TestSetRolling:
    """``set_rolling`` writes rolling config onto an existing schedule row."""

    def test_updates_rolling_config_cadence_untouched(self, app, bare_user):
        """Enabling rolling stores the flag and target; cadence is unchanged.

        ``set_rolling`` does not own cadence (generate / regenerate do),
        so a 14-day cadence stays 14 after the rolling write.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.upsert_schedule(user_id, cadence_days=14)
            updated = pay_schedule_service.set_rolling(
                user_id, enabled=True, target_periods=30,
            )
            assert updated.rolling_enabled is True
            assert updated.rolling_target_periods == 30
            assert updated.cadence_days == 14

    def test_disable_rolling_keeps_target(self, app, bare_user):
        """Disabling flips the flag off while leaving the stored target."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.upsert_schedule(user_id, cadence_days=14)
            pay_schedule_service.set_rolling(
                user_id, enabled=True, target_periods=26,
            )
            updated = pay_schedule_service.set_rolling(
                user_id, enabled=False, target_periods=26,
            )
            assert updated.rolling_enabled is False
            assert updated.rolling_target_periods == 26

    def test_raises_without_schedule_row(self, app, bare_user):
        """A user who never generated a schedule cannot configure rolling.

        Rolling grows the schedule and needs a stored cadence to extend
        at, so set_rolling refuses when no row exists.
        """
        with app.app_context():
            with pytest.raises(ValidationError):
                pay_schedule_service.set_rolling(
                    bare_user["user"].id, enabled=True, target_periods=10,
                )

    def test_upsert_after_set_rolling_preserves_config(self, app, bare_user):
        """A later cadence upsert never resets the user's rolling settings.

        set_rolling turns rolling on; a subsequent ``upsert_schedule``
        (e.g. regenerate persisting a new cadence) updates only
        ``cadence_days`` via its ON CONFLICT set, leaving rolling exactly
        as the user configured it.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.upsert_schedule(user_id, cadence_days=14)
            pay_schedule_service.set_rolling(
                user_id, enabled=True, target_periods=40,
            )
            updated = pay_schedule_service.upsert_schedule(
                user_id, cadence_days=7,
            )
            assert updated.cadence_days == 7
            assert updated.rolling_enabled is True
            assert updated.rolling_target_periods == 40


class TestResolveCadence:
    """``resolve_cadence`` answers the STORED cadence and nothing else."""

    def test_the_answer_is_the_stored_row_and_not_the_period_length(
        self, app, bare_periods,
    ):
        """The stored cadence answers even where the periods say otherwise.

        ``bare_periods`` are 14-day periods; storing 10 must make
        ``resolve_cadence`` return 10.  The two values are kept DIFFERENT on
        purpose: an owner whose stored cadence happened to match their period
        lengths could not distinguish a reader of the column from a reader of
        the rows, which is exactly how ``test_pay_calendar_loader``'s own
        adversarial review found a case passing against a loader that never
        touched the table.
        """
        user_id = bare_periods[0].user_id
        with app.app_context():
            pay_schedule_service.upsert_schedule(user_id, cadence_days=10)
            assert pay_schedule_service.resolve_cadence(user_id) == 10

    def test_an_owner_with_paydays_cannot_lose_their_cadence(
        self, app, db, bare_user,
    ):
        """Plan step **C4-b-2**: the inferring arm's input is unstorable now.

        This case REPLACES ``test_infers_from_last_period_when_no_schedule``,
        which asserted that an owner with 9-day periods and no schedule row
        resolved to 9.  That answer was circular -- since plan step C3-b
        ``record_paydays`` derives the last period's end FROM the stored
        cadence, so inverting it read back the value that produced it -- and
        the whole point of ``fk_pay_periods_schedule`` is that the owner it
        answered for can no longer exist.

        **It grades the constraint through the door the old case used**, a
        bulk ORM delete of the schedule row, so a future edit that drops the
        key fails HERE rather than silently restoring an unanswerable state.
        The refusal is the database's: ``ON DELETE RESTRICT`` (ruling
        **R-PC41**).
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id,
                first_payday=date(2026, 3, 1),
                num_periods=4,
                cadence_days=9,
            )
            db.session.flush()
            assert pay_schedule_service.get_schedule(user_id).cadence_days == 9

            with pytest.raises(IntegrityError) as excinfo:
                db.session.query(PaySchedule).filter_by(
                    user_id=user_id,
                ).delete(synchronize_session=False)
                db.session.flush()

            assert "fk_pay_periods_schedule" in str(excinfo.value)
            db.session.rollback()

    def test_returns_none_when_the_owner_has_no_schedule_row(
        self, app, bare_user,
    ):
        """No schedule row is no cadence, and it is an ordinary owner.

        Since ``fk_pay_periods_schedule`` this is the same statement as "no
        pay periods": a companion account, or any owner before their first
        recorded batch.  ``None`` is what the extend path reads as "generate
        your first schedule first".
        """
        with app.app_context():
            assert (
                pay_schedule_service.resolve_cadence(bare_user["user"].id)
                is None
            )


class TestResolveSchedule:
    """``resolve_schedule`` answers both calendar facts from one read.

    Plan step **balance:X-bh-2**.  ``resolve_cadence`` is its cadence half
    now, so the two cannot disagree about the same owner; what these grade is
    the pair, and the ASYMMETRY of the legacy fallback.
    """

    def test_it_carries_both_facts_off_the_row(self, app, bare_user):
        """The stored pair comes back as the stored pair."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.upsert_schedule(user_id, cadence_days=10)
            pay_schedule_service.set_history_opening(
                user_id, date(2024, 3, 1),
            )

            facts = pay_schedule_service.resolve_schedule(user_id)

            assert facts.cadence_days == 10
            assert facts.history_opens_on == date(2024, 3, 1)

    def test_resolve_cadence_is_its_HALF_and_not_a_second_answer(
        self, app, bare_user,
    ):
        """One derivation, two doors, so a change to either moves both.

        Two implementations of "what cadence does this owner have" is the
        drift this whole package's docstrings are about; this is the
        reconciler for the pair.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.upsert_schedule(user_id, cadence_days=7)

            assert (
                pay_schedule_service.resolve_cadence(user_id)
                == pay_schedule_service.resolve_schedule(user_id).cadence_days
                == 7
            )

    def test_both_facts_come_from_the_row_and_neither_is_derived(
        self, app, db, bare_user,
    ):
        """Both fields read the stored row, and the pay history is why.

        This case REPLACES
        ``test_the_legacy_fallback_covers_the_cadence_and_NOT_the_history``,
        which pinned an ASYMMETRY that plan step **C4-b-2** removed: the
        cadence used to be inferable for a row-less owner and
        ``history_opens_on`` never was, so one field guessed and the other did
        not.  Deleting the guess makes the two agree about what an absent row
        means.

        What survives, and is asserted here, is the reason the second field
        never had a fallback: nothing in ``budget.pay_periods`` says when a
        job began.  So the recorded paydays are set up to be *maximally*
        suggestive -- four of them, opening 2026-03-01 -- and the answer is
        still ``None``, because the first RECORDED payday is a boundary of the
        app's record and not a statement about the owner's history (ruling
        **balance:R-IA**, whose amendment priced the wrong reading at
        ``$1,437.91`` of Social Security tax not withheld).
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id,
                first_payday=date(2026, 3, 1),
                num_periods=4,
                cadence_days=9,
            )
            db.session.flush()

            facts = pay_schedule_service.resolve_schedule(user_id)

            assert facts.cadence_days == 9
            assert facts.history_opens_on is None

    def test_no_row_answers_NO_FACTS_rather_than_a_pair_of_nones(
        self, app, bare_user,
    ):
        """The companion's shape: no row, so no facts -- ONE absence, not two.

        **This case inverted at plan step ``pay_calendar:C4-d``** (ruling
        **R-PC45**) and is kept rather than deleted, because the inversion is
        the step.  It asserted ``facts.cadence_days is None`` beside
        ``facts.history_opens_on is None``: a ``ScheduleFacts`` in which BOTH
        fields were optional, which made four pairs constructible where a row
        can produce two.  The pair that could not exist was an absent cadence
        beside a STATED opening -- ``cadence_days`` is ``NOT NULL``, so no row
        says it -- and that ``int | None`` travelled into ``PayCalendar``,
        ``derive_periods`` and three projection producers, each of which
        policed the pairing in prose because the type would not.

        The absence is this function's own return now.  There is no row, so
        there are no facts, so there is no value -- one optional instead of two
        independent ones.
        """
        with app.app_context():
            assert pay_schedule_service.resolve_schedule(
                bare_user["user"].id,
            ) is None

    def test_a_stated_history_beside_NO_cadence_is_UNCONSTRUCTIBLE(self):
        """The pair no ``budget.pay_schedule`` row can produce, refused by the TYPE.

        The defect plan step C4-d removes, graded at the value rather than at
        one of its five consumers.  ``ScheduleFacts(None, date(...))`` says "I
        do not know how often this owner is paid, and I do know their paychecks
        reach back to 2020-06-01"; ``cadence_days`` is ``NOT NULL`` under
        ``ck_pay_schedule_cadence_range``, so the database cannot say it.

        **Asserted through the CALENDAR rather than on the annotation.**  A
        frozen dataclass does not enforce its own type hints, so
        ``ScheduleFacts(None, ...)`` still constructs at runtime and a test
        reading ``__annotations__`` would grade a string.  What is actually
        structural is that nothing downstream will take it: the calendar door
        that consumes this value refuses, so the pair cannot become a calendar
        however it was built.  That is the property the five prose
        preconditions used to stand in for.
        """
        impossible = pay_schedule_service.ScheduleFacts(
            cadence_days=None, history_opens_on=date(2020, 6, 1),
        )

        with pytest.raises(PayCalendarError, match="must be a plain int"):
            PayCalendar.from_paydays(
                paydays=[(1, date(2026, 1, 2))],
                cadence_days=impossible.cadence_days,
                user_id=1,
                history_opens_on=impossible.history_opens_on,
            )

    def test_the_facts_value_names_the_CALENDAR_columns(self, app, bare_user):
        """``ScheduleFacts.of`` is where "which columns" is stated.

        The rolling configuration lives on the same row and is deliberately
        NOT here: it configures a write, where these two describe the owner's
        rhythm.  A caller that took the whole row could quietly start reading
        one of them.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.upsert_schedule(user_id, cadence_days=14)
            pay_schedule_service.set_rolling(
                user_id, enabled=True, target_periods=7,
            )
            row = pay_schedule_service.get_schedule(user_id)

            facts = pay_schedule_service.ScheduleFacts.of(row)

            assert facts == pay_schedule_service.ScheduleFacts(14, None)
            assert not hasattr(facts, "rolling_enabled")


class TestSetHistoryOpening:
    """The one writer of ``history_opens_on`` (ruling **balance:R-IA**)."""

    def test_it_stores_the_day(self, app, bare_user):
        """The ordinary write, and the CONTROL for the refusals below."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.upsert_schedule(user_id, cadence_days=14)

            row = pay_schedule_service.set_history_opening(
                user_id, date(2024, 6, 1),
            )

            assert row.history_opens_on == date(2024, 6, 1)
            assert pay_schedule_service.get_schedule(
                user_id,
            ).history_opens_on == date(2024, 6, 1)

    def test_None_is_a_WRITE_and_clears_a_stored_day(self, app, bare_user):
        """Clearing the field is a real user action, not a skipped input.

        It is how an owner says "I have been paid this way longer than the
        app needs to know", and a door that treated the empty box as "no
        change" would make the field unclearable -- which is the defect
        ``_clear_nullable_empties`` exists to prevent one tier up.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.upsert_schedule(user_id, cadence_days=14)
            pay_schedule_service.set_history_opening(user_id, date(2024, 6, 1))

            pay_schedule_service.set_history_opening(user_id, None)

            assert pay_schedule_service.get_schedule(
                user_id,
            ).history_opens_on is None

    def test_it_leaves_the_cadence_and_the_rolling_config_alone(
        self, app, bare_user,
    ):
        """A door of its own, so saving one fact never restates another."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.upsert_schedule(user_id, cadence_days=9)
            pay_schedule_service.set_rolling(
                user_id, enabled=True, target_periods=13,
            )

            pay_schedule_service.set_history_opening(user_id, date(2024, 6, 1))
            row = pay_schedule_service.get_schedule(user_id)

            assert row.cadence_days == 9
            assert row.rolling_enabled is True
            assert row.rolling_target_periods == 13

    def test_an_owner_with_no_schedule_row_is_refused(self, app, bare_user):
        """A floor bounds a rhythm, and there is no rhythm without a cadence."""
        with app.app_context():
            with pytest.raises(ValidationError, match="Generate a pay-period"):
                pay_schedule_service.set_history_opening(
                    bare_user["user"].id, date(2024, 6, 1),
                )

    def test_a_day_outside_the_apps_calendar_is_REFUSED_not_500(
        self, app, bare_user,
    ):
        """``ck_pay_schedule_history_opens_range`` as a 400, not an IntegrityError.

        An HTML date input accepts a five-digit-year typo, so the value the
        CHECK refuses arrives from an ordinary form rather than from an
        attack.  A refusal the surface can render is the difference between a
        message and a stack trace.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.upsert_schedule(user_id, cadence_days=14)

            with pytest.raises(ValidationError, match="2100-12-31"):
                pay_schedule_service.set_history_opening(
                    user_id, date(9999, 1, 1),
                )

            assert pay_schedule_service.get_schedule(
                user_id,
            ).history_opens_on is None

    def test_a_day_after_the_first_recorded_payday_is_refused(
        self, app, db, bare_user,
    ):
        """Paychecks cannot have begun after the first one the app holds.

        Measured against ``min(start_date)`` rather than the lowest
        ``period_index``: the two agree only while the index is in date order,
        and that is a stored column plan step C4-c dropped.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id,
                first_payday=date(2026, 3, 1),
                num_periods=4,
                cadence_days=14,
            )
            db.session.flush()

            with pytest.raises(ValidationError, match="2026-03-01"):
                pay_schedule_service.set_history_opening(
                    user_id, date(2026, 3, 2),
                )

    def test_a_day_ON_the_first_recorded_payday_is_ACCEPTED(
        self, app, db, bare_user,
    ):
        """THE CONTROL, and it is an ordinary owner rather than an edge.

        A floor on the opening payday means "count nothing below the record",
        which is what somebody whose first payday has not happened yet states
        (``pay_calendar:R-PC14``).  Without this case the refusal above would
        pass against a door that refused every day beside a schedule.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id,
                first_payday=date(2026, 3, 1),
                num_periods=4,
                cadence_days=14,
            )
            db.session.flush()

            row = pay_schedule_service.set_history_opening(
                user_id, date(2026, 3, 1),
            )

            assert row.history_opens_on == date(2026, 3, 1)


class TestTheHistoryRefusalsAreOneRuleEach:
    """Both refusals are functions because two doors ask them.

    Registration asks them of the payday its FORM states, before the ``User``
    row exists; :func:`set_history_opening` asks them of the payday the
    schedule RECORDS.  These grade the rules directly, where the cases above
    grade them through the write door.
    """

    @pytest.mark.parametrize("day", [None, date(2000, 1, 1), date(2100, 12, 31)])
    def test_the_window_refusal_passes_the_ends_and_the_null(self, day):
        """``None`` passes -- it is the column's ordinary value, not a gap."""
        pay_schedule_service.reject_out_of_range_history_opening(day)

    @pytest.mark.parametrize("day", [date(1999, 12, 31), date(2101, 1, 1)])
    def test_the_window_refusal_names_the_offending_day(self, day):
        """A message a surface can render verbatim."""
        with pytest.raises(ValidationError, match=day.isoformat()):
            pay_schedule_service.reject_out_of_range_history_opening(day)

    @pytest.mark.parametrize(("opening", "payday"), [
        (None, date(2026, 3, 1)),
        (date(2026, 3, 1), None),
        (date(2026, 3, 1), date(2026, 3, 1)),
        (date(2026, 2, 28), date(2026, 3, 1)),
    ])
    def test_the_ordering_refusal_passes_absence_and_equality(
        self, opening, payday,
    ):
        """Either side absent is nothing to contradict; equality is ordinary."""
        pay_schedule_service.reject_history_opening_after_payday(
            opening, payday,
        )

    def test_the_ordering_refusal_names_both_days(self):
        """The owner is told what they said AND what it conflicts with."""
        with pytest.raises(ValidationError) as caught:
            pay_schedule_service.reject_history_opening_after_payday(
                date(2026, 3, 2), date(2026, 3, 1),
            )

        assert "2026-03-02" in str(caught.value)
        assert "2026-03-01" in str(caught.value)


class TestAStoredOpeningCanBeSTRANDEDByAReset:
    """The one route that outlives the rule, enumerated rather than assumed.

    ``history_opens_on <= the first recorded payday`` is checked at both write
    doors and is NOT an invariant the database can hold -- it spans two tables.
    ``/pay-periods/reset`` wipes every pay period and rebuilds from a stated
    day without touching ``budget.pay_schedule``, so a floor that was legal
    when it was written can end up above the record.

    **The direction matters and is the half that is easy to get backwards.**
    Only a reset to an EARLIER first payday can do it: the floor was already at
    or below the old opening, so an opening that moves UP stays above the floor
    and nothing is contradicted.  An adversarial review of plan step
    balance:X-bh-2 caught a docstring naming the opposite direction, which is
    the same as not having enumerated the route at all.

    These grade the whole consequence: the value SURVIVES, the rhythm goes
    inert rather than wrong, the recorded paydays below it are still counted,
    and the settings card will refuse the value it renders -- which is a real
    cost, stated here so it is a known state rather than a surprise.
    """

    @staticmethod
    def _stranded(db_session, bare_user):
        """Write a legal floor, then reset the schedule to an earlier payday."""
        user_id = bare_user["user"].id
        pay_period_write.record_paydays(
            user_id=user_id, first_payday=date(2026, 3, 1),
            num_periods=4, cadence_days=14,
        )
        db_session.flush()
        pay_schedule_service.set_history_opening(user_id, date(2026, 2, 1))
        db_session.flush()

        pay_period_admin.reset_pay_periods(
            user_id, date(2025, 1, 1), 4, 14,
        )
        db_session.flush()
        return user_id

    def test_the_reset_moves_the_opening_below_the_stored_floor(
        self, app, db, bare_user,
    ):
        """THE PREMISE, asserted before anything is concluded from it.

        Without this the three cases below could pass on a reset that never
        moved the opening at all.
        """
        with app.app_context():
            user_id = self._stranded(db.session, bare_user)

            opening = min(
                period.start_date
                for period in db.session.query(PayPeriod).filter_by(
                    user_id=user_id,
                ).all()
            )
            assert opening == date(2025, 1, 1)
            assert pay_schedule_service.get_schedule(
                user_id,
            ).history_opens_on == date(2026, 2, 1) > opening

    def test_the_stated_value_SURVIVES_the_reset(self, app, db, bare_user):
        """Reset does not clear it, and must not: it is the owner's statement.

        ``upsert_schedule``'s conflict set is ``cadence_days`` alone, so the
        rebuild cannot clobber the column.  Silently dropping a fact the owner
        entered would be worse than carrying a stale one.
        """
        with app.app_context():
            user_id = self._stranded(db.session, bare_user)

            assert pay_schedule_service.get_schedule(
                user_id,
            ).history_opens_on == date(2026, 2, 1)

    def test_the_rhythm_goes_INERT_rather_than_wrong(self, app, db, bare_user):
        """The backward half empties; the RECORD is untouched.

        This is what makes the stranded state safe rather than a money defect:
        the floor bounds the projection only, so the four recorded 2025
        paydays are still counted and nothing below them is invented.
        """
        with app.app_context():
            user_id = self._stranded(db.session, bare_user)
            calendar = calendar_for(user_id)

            # January 2025 opens the rebuilt record: 01-01, 01-15, 01-29.
            assert paydays_in_month_through(
                calendar, date(2025, 1, 31),
            ) == (date(2025, 1, 1), date(2025, 1, 15), date(2025, 1, 29))
            # And nothing is projected below it, because the floor is above.
            assert paydays_in_month_through(calendar, date(2024, 12, 31)) == ()

    def test_the_stranded_value_can_no_longer_be_RE_SAVED(
        self, app, db, bare_user,
    ):
        """The cost, stated rather than discovered by an owner.

        The settings card pre-fills the stored day, and submitting it back
        unchanged is refused -- correctly, since it now contradicts the
        record.  The message names both days, so the owner is told what to
        change rather than left to guess; clearing the box also works.
        """
        with app.app_context():
            user_id = self._stranded(db.session, bare_user)

            with pytest.raises(ValidationError) as caught:
                pay_schedule_service.set_history_opening(
                    user_id, date(2026, 2, 1),
                )

            assert "2026-02-01" in str(caught.value)
            assert "2025-01-01" in str(caught.value)
            # Clearing it is always available.
            pay_schedule_service.set_history_opening(user_id, None)
            assert pay_schedule_service.get_schedule(
                user_id,
            ).history_opens_on is None
