"""Tests for ``pay_schedule_service`` (pay-period CRUD Phase 1, eras at C17-a).

The service owns the per-user ``budget.pay_schedule`` row -- the owner-level
configuration a schedule cannot derive from its own rows -- and the
``budget.pay_eras`` rows that hang off it, one per *how I have been paid
since* (plan step ``pay_calendar:C17-a``, ruling **R-PC58**).  What matters:

  * ``get_schedule`` returns the row (with its eras) or ``None``.
  * ``ensure_schedule_row`` creates the row once and never disturbs it;
    ``mint_era`` is the ONE writer of an era and asks the cadence bound and
    the cadence-convention pairing before it writes; ``retire_eras`` is the
    one door that removes them.
  * ``era_to_mint`` is the ERA RULE: a batch mints an era only when it states
    a rhythm the era covering its first payday does not hold.
  * ``resolve_cadence`` answers the LATEST era's cadence and nothing else.  It
    used to fall back to inferring one from the last period's length for an
    owner with periods but no schedule row; plan step **C4-b-2** made that
    owner unrepresentable (``fk_pay_periods_schedule``) and deleted the arm
    with them, closing findings **P8** and **P35**.
  * ``resolve_schedule`` answers every era and the history bound from one
    load (plan step **balance:X-bh-2**), and ``set_history_opening`` is the
    one writer of the bound.

See ``docs/plans/implementation_plan_pay_calendar.md``.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import BusinessDayShiftEnum
from app.exceptions import ValidationError
from app.models.pay_period import PayPeriod
from app.models.pay_era import CADENCE_DAYS_MIN
from app.models.pay_schedule import PaySchedule
from app.utils.business_days import shortest_collision_free_cadence
from app.services.pay_calendar import (
    PayCalendar,
    PayCalendarError,
    calendar_for,
    paydays_in_month_through,
)
from app.services import (
    pay_era_write,
    pay_period_admin,
    pay_period_write,
    pay_schedule_service,
)
from tests._test_helpers import (
    era_of,
    mint_fixture_era,
    restate_fixture_era,
    rhythm_of,
)


class TestGetSchedule:
    """``get_schedule`` returns the row when present, ``None`` otherwise."""

    def test_returns_none_when_user_has_no_schedule(self, app, bare_user):
        """A user with no pay_schedule row resolves to ``None``."""
        with app.app_context():
            assert (
                pay_schedule_service.get_schedule(bare_user["user"].id) is None
            )


class TestTheEraDoors:
    """``ensure_schedule_row`` creates the row once; ``mint_era`` and ``retire_eras`` own the eras."""

    def test_ensure_schedule_row_creates_the_row_with_rolling_defaults(
        self, app, bare_user,
    ):
        """The first call inserts a row with rolling off and a 52-period target."""
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(bare_user["user"].id)
            schedule = pay_schedule_service.get_schedule(bare_user["user"].id)
            assert schedule.id is not None
            assert schedule.rolling_enabled is False
            assert schedule.rolling_target_periods == 52
            assert schedule.eras == []

    def test_ensure_schedule_row_never_disturbs_an_existing_row(
        self, app, db, bare_user,
    ):
        """A second call is inert: rolling config and identity survive."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(user_id)
            schedule = pay_schedule_service.get_schedule(user_id)
            schedule.rolling_enabled = True
            schedule.rolling_target_periods = 30
            db.session.flush()

            pay_schedule_service.ensure_schedule_row(user_id)

            again = pay_schedule_service.reread_schedule(user_id)
            assert again.id == schedule.id
            assert again.rolling_enabled is True
            assert again.rolling_target_periods == 30

    def test_mint_era_records_the_rhythm_and_its_day(self, app, bare_user):
        """The minted row reads back as the era that was stated."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(user_id)
            row = pay_era_write.mint_era(
                user_id, era_of(date(2026, 1, 2), 14, BusinessDayShiftEnum.PRIOR),
            )
            assert row.id is not None

            facts = pay_schedule_service.resolve_schedule(user_id)
            assert facts.eras == (
                era_of(date(2026, 1, 2), 14, BusinessDayShiftEnum.PRIOR),
            )
            assert facts.rhythm == rhythm_of(14, BusinessDayShiftEnum.PRIOR)

    def test_a_second_era_is_a_second_row_and_the_LATEST_answers(
        self, app, db, bare_user,
    ):
        """Two eras, ascending; the rhythm every reader takes is the latest's.

        The old row was OVERWRITTEN by every batch (ledger row **N-492**);
        the era relation keeps both, which is the whole step.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(user_id)
            pay_era_write.mint_era(user_id, era_of(date(2026, 1, 2), 14))
            pay_era_write.mint_era(user_id, era_of(date(2026, 2, 20), 7))
            db.session.flush()

            facts = pay_schedule_service.resolve_schedule(user_id)

            assert [e.effective_from for e in facts.eras] == [
                date(2026, 1, 2), date(2026, 2, 20),
            ]
            assert facts.latest_era.rhythm.cadence_days == 7
            assert pay_schedule_service.resolve_cadence(user_id) == 7

    def test_retire_eras_keeps_the_standing_set_or_nothing(self, app, db, bare_user):
        """``standing`` names the eras to KEEP; an empty tuple retires all.

        Since plan step ``C17-b-2`` the door takes the standing set rather
        than a boundary day, so the decision is made once, in cash days, by
        ``pay_era_write.eras_describing``.  Re-read through
        ``reread_schedule`` after each delete, because the bulk delete
        synchronises nothing and the collection is view-only (the module
        docstring's own warning).
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(user_id)
            for day, cadence in (
                (date(2026, 1, 2), 14), (date(2026, 2, 20), 7),
                (date(2026, 4, 3), 30),
            ):
                pay_era_write.mint_era(user_id, era_of(day, cadence))
            db.session.flush()

            assert pay_era_write.retire_eras(
                user_id, (date(2026, 1, 2), date(2026, 2, 20)),
            ) == 1
            assert [
                e.effective_from
                for e in pay_schedule_service.ScheduleFacts.of(
                    pay_schedule_service.reread_schedule(user_id),
                ).eras
            ] == [date(2026, 1, 2), date(2026, 2, 20)]

            assert pay_era_write.retire_eras(user_id, ()) == 2
            assert pay_schedule_service.ScheduleFacts.of(
                pay_schedule_service.reread_schedule(user_id),
            ) is None
            assert pay_schedule_service.get_schedule(user_id) is not None


class TestMintEraRefusesAnUnstorableCadence:
    """``mint_era`` bounds the cadence itself (plan step X-ad-a, carried to the era).

    ``ck_pay_eras_cadence_range`` bounds the column to 1..365, and until
    X-ad-a the only thing standing between a caller and that CHECK was each
    caller's own Marshmallow field -- a rule held by four separate
    declarations and by whoever remembered to add a fifth.  Registration was
    that fifth door.  The refusal lives at the one writer, so the failure mode
    it removes is an ``IntegrityError`` 500 on a value a form could have
    reported.
    """

    @pytest.mark.parametrize("cadence", [0, -1, 366, 100_000])
    def test_out_of_range_cadence_raises_before_writing(
        self, app, bare_user, cadence,
    ):
        """A cadence outside 1..365 raises and writes no era.

        The four values bracket both ends: 0 and -1 below the floor (a
        zero-day cadence is a schedule with no paydays; a negative one runs
        backwards), 366 one past the ceiling, and 100000 far past it.  No
        schedule row exists either, so a refusal that reached the write would
        surface as a foreign-key error rather than the message asserted.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            with pytest.raises(ValidationError, match="between 1 and 365"):
                pay_era_write.mint_era(
                    user_id, era_of(date(2026, 1, 2), cadence),
                )
            assert pay_schedule_service.get_schedule(user_id) is None

    def test_message_names_the_offending_value(self, app, bare_user):
        """The refusal quotes the value, so a surface can render it verbatim."""
        with app.app_context():
            with pytest.raises(ValidationError) as exc:
                pay_era_write.mint_era(
                    bare_user["user"].id, era_of(date(2026, 1, 2), 400),
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
            mint_fixture_era(bare_user["user"].id, date(2026, 1, 2), cadence)
            assert pay_schedule_service.resolve_cadence(
                bare_user["user"].id,
            ) == cadence


class TestSetRolling:
    """``set_rolling`` writes rolling config onto an existing schedule row."""

    def test_updates_rolling_config_cadence_untouched(self, app, bare_user):
        """Enabling rolling stores the flag and target; cadence is unchanged.

        ``set_rolling`` does not own cadence (generate / regenerate do),
        so a 14-day cadence stays 14 after the rolling write.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            mint_fixture_era(user_id, date(2026, 1, 2), 14)
            updated = pay_schedule_service.set_rolling(
                user_id, enabled=True, target_periods=30,
            )
            assert updated.rolling_enabled is True
            assert updated.rolling_target_periods == 30
            assert pay_schedule_service.resolve_cadence(user_id) == 14

    def test_disable_rolling_keeps_target(self, app, bare_user):
        """Disabling flips the flag off while leaving the stored target."""
        user_id = bare_user["user"].id
        with app.app_context():
            mint_fixture_era(user_id, date(2026, 1, 2), 14)
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

    def test_a_later_era_never_resets_the_rolling_config(self, app, bare_user):
        """A later era mint never resets the user's rolling settings.

        set_rolling turns rolling on; a subsequent era (e.g. regenerate
        persisting a new cadence) is a row of its own, so the schedule row
        and its rolling config are not written at all.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            mint_fixture_era(user_id, date(2026, 1, 2), 14)
            pay_schedule_service.set_rolling(
                user_id, enabled=True, target_periods=40,
            )
            pay_era_write.mint_era(user_id, era_of(date(2026, 3, 6), 7))
            updated = pay_schedule_service.reread_schedule(user_id)
            assert pay_schedule_service.resolve_cadence(user_id) == 7
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
            restate_fixture_era(user_id, date(2026, 1, 2), 10)
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
                rhythm=rhythm_of(9),
            )
            db.session.flush()
            assert pay_schedule_service.resolve_cadence(user_id) == 9
            # The era is a SECOND child of the row since plan step C17-a
            # (``fk_pay_eras_schedule``); it goes first so the refusal graded
            # is the payday key's, which is this case's subject.
            pay_era_write.retire_eras(user_id, ())
            db.session.flush()

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
            mint_fixture_era(user_id, date(2026, 1, 2), 10)
            pay_schedule_service.set_history_opening(
                user_id, date(2024, 3, 1),
            )

            facts = pay_schedule_service.resolve_schedule(user_id)

            assert facts.rhythm.cadence_days == 10
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
            mint_fixture_era(user_id, date(2026, 1, 2), 7)

            assert (
                pay_schedule_service.resolve_cadence(user_id)
                == pay_schedule_service.resolve_schedule(user_id).rhythm.cadence_days
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
                rhythm=rhythm_of(9),
            )
            db.session.flush()

            facts = pay_schedule_service.resolve_schedule(user_id)

            assert facts.rhythm.cadence_days == 9
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
            eras=(era_of(date(2026, 1, 2), None),),
            history_opens_on=date(2020, 6, 1),
        )

        with pytest.raises(PayCalendarError, match="must be a plain int"):
            PayCalendar.from_paydays(
                paydays=[(1, date(2026, 1, 2))],
                eras=impossible.eras,
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
            mint_fixture_era(user_id, date(2026, 1, 2), 14)
            pay_schedule_service.set_rolling(
                user_id, enabled=True, target_periods=7,
            )
            row = pay_schedule_service.reread_schedule(user_id)

            facts = pay_schedule_service.ScheduleFacts.of(row)

            assert facts == pay_schedule_service.ScheduleFacts(
                (era_of(date(2026, 1, 2), 14),), None,
            )
            assert not hasattr(facts, "rolling_enabled")

    def test_a_row_holding_no_era_has_NO_FACTS(self, app, bare_user):
        """The one state plan step C17-a adds to ``None``: a row and no rhythm.

        ``ScheduleFacts`` is the facts of an owner who holds an era -- the
        tuple is never empty -- so a row with none answers ``None`` from both
        doors rather than a value whose ``rhythm`` would raise.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(user_id)
            row = pay_schedule_service.get_schedule(user_id)

            assert pay_schedule_service.ScheduleFacts.of(row) is None
            assert pay_schedule_service.resolve_schedule(user_id) is None
            assert pay_schedule_service.resolve_cadence(user_id) is None


class TestSetHistoryOpening:
    """The one writer of ``history_opens_on`` (ruling **balance:R-IA**)."""

    def test_it_stores_the_day(self, app, bare_user):
        """The ordinary write, and the CONTROL for the refusals below."""
        user_id = bare_user["user"].id
        with app.app_context():
            mint_fixture_era(user_id, date(2026, 1, 2), 14)

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
            mint_fixture_era(user_id, date(2026, 1, 2), 14)
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
            mint_fixture_era(user_id, date(2026, 1, 2), 9)
            pay_schedule_service.set_rolling(
                user_id, enabled=True, target_periods=13,
            )

            pay_schedule_service.set_history_opening(user_id, date(2024, 6, 1))
            row = pay_schedule_service.get_schedule(user_id)

            assert pay_schedule_service.resolve_cadence(user_id) == 9
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
            mint_fixture_era(user_id, date(2026, 1, 2), 14)

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
                rhythm=rhythm_of(14),
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
                rhythm=rhythm_of(14),
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
            num_periods=4, rhythm=rhythm_of(14),
        )
        db_session.flush()
        pay_schedule_service.set_history_opening(user_id, date(2026, 2, 1))
        db_session.flush()

        pay_period_admin.reset_pay_periods(
            user_id, date(2025, 1, 1), 4, rhythm_of(14),
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

        A rebuild retires every era and mints its own (plan step
        ``pay_calendar:C17-a``), and never writes the schedule row at all --
        so the rebuild cannot clobber this column.  *The reason named the
        rhythm upsert's conflict set until that step moved the rhythm off the
        row; the conclusion was unchanged and its premise was not, which is
        why this case asserts the outcome rather than the mechanism.*
        Silently dropping a fact the owner entered would be worse than
        carrying a stale one.
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


class TestTheRhythmIsAPairAndIsJudgedAsOne:
    """Plan step ``pay_calendar:C14-b``: the cadence and the convention.

    **The rule this class exists for is a JOINT one.**  A convention that
    displaces a payday off a closed day is not injective below
    :func:`~app.utils.business_days.shortest_collision_free_cadence`: two
    nominal paydays land on ONE day, and ``pay_calendar.derive_periods``
    refuses a repeated payday outright, so the owner's whole calendar would
    raise rather than render.

    **It is refused at the write door rather than by a CHECK constraint**
    (ruling **R-PC59**, developer 2026-09-05), because the floor is DERIVED
    from the
    federal holiday set -- which has already changed once inside this
    application's calendar window -- and a CHECK expression must be immutable.
    A frozen floor would also be grandfathered onto rows PostgreSQL never
    revalidates, which is the direction that fails silently.  These cases are
    therefore the whole of the enforcement, not a second copy of it.
    """

    def test_none_is_legal_at_the_shortest_cadence_the_column_admits(
        self, app, bare_user,
    ):
        """A one-day cadence stays writable, which is finding **P9**'s ruling.

        ``pay_calendar:C4-c`` deliberately made two paydays a day apart an
        ordinary schedule, and registration's own
        ``test_a_ONE_DAY_cadence_registers_and_records_its_paydays`` pins it.
        The floor introduced here must not reach that owner: ``none``
        displaces nothing, so nothing can collide.
        """
        with app.app_context():
            mint_fixture_era(
                bare_user["user"].id, date(2026, 1, 2), CADENCE_DAYS_MIN,
                BusinessDayShiftEnum.NONE,
            )
            assert pay_schedule_service.resolve_cadence(
                bare_user["user"].id,
            ) == CADENCE_DAYS_MIN

    @pytest.mark.parametrize(
        "shift", [BusinessDayShiftEnum.PRIOR, BusinessDayShiftEnum.NEXT],
    )
    def test_a_displacing_convention_is_refused_below_the_floor(
        self, app, bare_user, shift,
    ):
        """Every cadence under the floor is refused, and nothing is written.

        Swept over the whole band rather than at one value, because a
        refusal written with ``==`` instead of ``<`` would pass a single-value
        case at the boundary and admit every shorter cadence -- which is the
        set that actually collides.
        """
        floor = shortest_collision_free_cadence()
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(bare_user["user"].id)
            for cadence in range(CADENCE_DAYS_MIN, floor):
                with pytest.raises(ValidationError, match=str(floor)):
                    pay_era_write.mint_era(
                        bare_user["user"].id,
                        era_of(date(2026, 1, 2), cadence, shift),
                    )
            assert pay_schedule_service.resolve_schedule(
                bare_user["user"].id,
            ) is None

    @pytest.mark.parametrize(
        "shift", [BusinessDayShiftEnum.PRIOR, BusinessDayShiftEnum.NEXT],
    )
    def test_a_displacing_convention_is_accepted_at_the_floor(
        self, app, bare_user, shift,
    ):
        """The boundary itself is ADMITTED, which an off-by-one would refuse.

        Paired with the case above: between them they pin the refusal to
        exactly ``cadence < floor``, so neither a ``<=`` nor a ``<`` written
        the wrong way round survives.
        """
        with app.app_context():
            row = mint_fixture_era(
                bare_user["user"].id, date(2026, 1, 2),
                shortest_collision_free_cadence(), shift,
            )
            assert row.shift_id == ref_cache.business_day_shift_id(shift)

    def test_shortening_the_cadence_WHILE_switching_the_convention_off_passes(
        self, app, db, bare_user,
    ):
        """The regression this whole design exists for: judge the END state.

        An owner paid fortnightly with an early-pay convention corrects their
        schedule to a two-day cadence AND turns the convention off, in one
        submission.  The era they are left with -- ``(2, none)`` -- is
        perfectly legal.

        Written as two statements it would be REFUSED whichever order they
        ran in: the cadence write judged against the convention still stored,
        or the convention write judged against the cadence still stored.  An
        era is ONE row judged as one, and this case is what would fail if a
        later refactor split it again.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            mint_fixture_era(
                user_id, date(2026, 1, 2), 14, BusinessDayShiftEnum.PRIOR,
            )
            db.session.flush()

            minted = pay_era_write.mint_era(
                user_id, era_of(date(2026, 3, 6), 2, BusinessDayShiftEnum.NONE),
            )

            assert minted.cadence_days == 2
            assert minted.shift_id == ref_cache.business_day_shift_id(
                BusinessDayShiftEnum.NONE,
            )
            assert pay_schedule_service.resolve_schedule(
                user_id,
            ).rhythm == rhythm_of(2, BusinessDayShiftEnum.NONE)

    def test_lengthening_the_cadence_WHILE_switching_one_on_passes(
        self, app, db, bare_user,
    ):
        """The mirror of the case above, and the other order's hole.

        A two-day schedule with no convention becomes fortnightly WITH one.
        Judging the new convention against the cadence still stored -- two --
        would refuse a submission whose end state is legal.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            mint_fixture_era(
                user_id, date(2026, 1, 2), 2, BusinessDayShiftEnum.NONE,
            )
            db.session.flush()

            minted = pay_era_write.mint_era(
                user_id, era_of(date(2026, 3, 6), 14, BusinessDayShiftEnum.NEXT),
            )

            assert minted.cadence_days == 14
            assert minted.shift_id == ref_cache.business_day_shift_id(
                BusinessDayShiftEnum.NEXT,
            )

    def test_an_illegal_pair_leaves_the_stored_rhythm_untouched(
        self, app, db, bare_user,
    ):
        """A refused write changes NEITHER half, which one row gives.

        The failure this rules out is a writer that persisted the cadence and
        then refused the convention: the owner would be left paid every two
        days, silently, by a request that was rejected.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            mint_fixture_era(
                user_id, date(2026, 1, 2), 14, BusinessDayShiftEnum.NONE,
            )
            db.session.flush()

            with pytest.raises(ValidationError):
                pay_era_write.mint_era(
                    user_id,
                    era_of(date(2026, 3, 6), 2, BusinessDayShiftEnum.PRIOR),
                )

            stored = pay_schedule_service.resolve_schedule(user_id)
            assert stored.eras == (era_of(date(2026, 1, 2), 14),)

    def test_the_refusal_names_the_floor_and_the_offending_cadence(
        self, app, bare_user,
    ):
        """A surface renders this message verbatim, so it must be readable.

        The floor is INTERPOLATED rather than spelled, so the message tracks
        the calendar it comes from -- the assertion reads the same producer
        the message does, and a hard-coded expectation here would decay the
        moment the holiday set changed.
        """
        floor = shortest_collision_free_cadence()
        with app.app_context():
            with pytest.raises(ValidationError) as excinfo:
                pay_era_write.mint_era(
                    bare_user["user"].id,
                    era_of(date(2026, 1, 2), 2, BusinessDayShiftEnum.PRIOR),
                )
            message = str(excinfo.value)
            assert f"at least {floor}" in message
            assert "got 2" in message


class TestTheEraRule:
    """``era_to_mint`` decides when a batch states a NEW era (plan step C17-a).

    The rule has three arms and one negative, and each is graded on its own
    so a rule collapsed to "always mint" or "never mint" fails here: no era
    at all; the covering era on a different rhythm; a first payday off the
    covering era's grid; and the continuation, which mints nothing.  The
    function is judged against the eras a batch LEAVES STANDING
    (``eras_describing``), which these cases hand it directly; the writer's
    own tests drive the pair together.
    """

    _FACTS = pay_schedule_service.ScheduleFacts(
        eras=(era_of(date(2026, 1, 2), 14), era_of(date(2026, 4, 3), 7)),
        history_opens_on=None,
    )

    def test_an_owner_with_no_era_mints_one_at_the_first_payday(self):
        """A first schedule is an era from its first payday."""
        assert pay_era_write.era_to_mint(
            (), date(2026, 5, 1), rhythm_of(14),
        ) == era_of(date(2026, 5, 1), 14)

    def test_a_batch_on_the_covering_eras_grid_at_its_rhythm_mints_NOTHING(
        self,
    ):
        """The continuation -- every extend and rolling top-up -- writes no era.

        2026-05-01 is four weeks past the 7-day era's day and states 7 days,
        so it is that era continuing; ledger row **N-494** is what minting
        here used to cost.
        """
        assert pay_era_write.era_to_mint(
            self._FACTS.eras, date(2026, 5, 1), rhythm_of(7),
        ) is None

    def test_a_different_rhythm_mints_an_era_and_keeps_the_covering_one(self):
        """A cadence corrected going forward is a new era, not an overwrite.

        Both halves of the pair are the rule's: a changed cadence and a
        changed convention each mint.
        """
        assert pay_era_write.era_to_mint(
            self._FACTS.eras, date(2026, 5, 1), rhythm_of(30),
        ) == era_of(date(2026, 5, 1), 30)
        assert pay_era_write.era_to_mint(
            self._FACTS.eras, date(2026, 5, 1), rhythm_of(7, BusinessDayShiftEnum.PRIOR),
        ) == era_of(date(2026, 5, 1), 7, BusinessDayShiftEnum.PRIOR)

    def test_a_first_payday_OFF_the_covering_grid_mints_an_era(self):
        """A phase corrected going forward is a new era at the corrected day.

        2026-05-02 is one day past a 7-day grid day, at the same 7-day
        rhythm: the rhythm matches and the phase does not, which is what
        ``regenerate`` with a corrected first payday expresses.
        """
        assert pay_era_write.era_to_mint(
            self._FACTS.eras, date(2026, 5, 2), rhythm_of(7),
        ) == era_of(date(2026, 5, 2), 7)

    def test_the_covering_era_is_the_LATEST_on_or_before_the_day(self):
        """A day inside the FIRST era is judged against the first era's rhythm.

        2026-02-27 sits between the two eras' days and is eight weeks past
        the 14-day era's day, so a 14-day batch there continues it and a
        7-day batch there would mint -- the 7-day era covers only days from
        2026-04-03.
        """
        assert pay_era_write.era_to_mint(
            self._FACTS.eras, date(2026, 2, 27), rhythm_of(14),
        ) is None
        assert pay_era_write.era_to_mint(
            self._FACTS.eras, date(2026, 2, 27), rhythm_of(7),
        ) == era_of(date(2026, 2, 27), 7)

    def test_a_day_before_every_era_is_the_EARLIEST_eras(self):
        """The earliest era runs backward below the record, so it covers the day."""
        assert pay_era_write.era_to_mint(
            self._FACTS.eras, date(2025, 12, 19), rhythm_of(14),
        ) is None


class TestTheStoredConventionReachesTheCalendar:
    """The stored ``shift_id`` becomes a member on :class:`ScheduleFacts`.

    **This class replaced ``TestResolveShift`` at plan step ``C14-e-1``**,
    which deleted that function.  The three properties it graded are the three
    graded here, each through the door that now owns it: a stored id round
    trips to its member, an id the application does not model is REFUSED
    rather than read as ``none``, and an owner with no schedule row reaches a
    refusal on the way to a calendar.  Re-pointed rather than deleted --
    coverage that stops having a subject is coverage that stops being read.
    """

    @pytest.mark.parametrize(
        "shift",
        [
            BusinessDayShiftEnum.NONE,
            BusinessDayShiftEnum.PRIOR,
            BusinessDayShiftEnum.NEXT,
        ],
    )
    def test_it_round_trips_every_member(self, app, db, bare_user, shift):
        """Written as a member, stored as an id, read back as the member.

        All three, because a reader built on a two-branch conditional answers
        two of them correctly and the third by accident -- and the value it
        would get wrong is the one that moves a payday.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            mint_fixture_era(user_id, date(2026, 1, 2), 14, shift)
            db.session.flush()

            facts = pay_schedule_service.resolve_schedule(user_id)

            assert facts.rhythm.shift is shift
            # The property plan step ``C14-e-1`` actually adds, and asserting
            # only the line above would leave it ungraded: the pair reaches
            # the PAY CALENDAR, which is what ``C14-e-3``'s producer reads and
            # what ``extend_pay_periods`` takes its rhythm from now that
            # ``resolve_shift`` is gone.  One read answers both, so this is
            # also the reconciler for the two doors.  Since plan step
            # ``C17-b-2`` the calendar carries the ERAS whole, so the pair
            # reaches it as the era that holds it.
            assert calendar_for(user_id).eras == facts.eras
            assert calendar_for(user_id).eras[-1].rhythm == facts.rhythm

    def test_an_UNMODELLED_id_is_refused_rather_than_read_as_none(
        self, app, db, bare_user,
    ):
        """A convention this application cannot name is an error, not ``none``.

        ``fk_pay_eras_shift_id`` admits only seeded ids, so the era is built
        through the writer and then the COLUMN is moved underneath it -- the
        state a change to ``ref.business_day_shifts`` outside the application
        would leave.  Reading it as ``none`` would silently
        un-displace every projected payday for that owner, which is a wrong
        date rather than an error; the refusal names the schedule.

        **It is the one behaviour of ``resolve_shift`` that had nowhere else
        to go**, so ``C14-e-1`` moved it into
        :meth:`~app.services.pay_schedule_service.ScheduleFacts.of` -- the one
        place a stored id becomes a member -- and this case follows it.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            mint_fixture_era(user_id, date(2026, 1, 2), 14)
            db.session.flush()
            schedule = pay_schedule_service.get_schedule(user_id)
            # Past every seeded id, so ``business_day_shift_member`` answers
            # ``None``.  Set on the instance rather than through the writer,
            # which is the point: no door can produce this.
            schedule.eras[0].shift_id = 9999

            with pytest.raises(ValidationError, match="does not model"):
                pay_schedule_service.ScheduleFacts.of(schedule)

    def test_an_owner_with_no_schedule_row_reaches_a_refusal(
        self, app, bare_user,
    ):
        """No fallback, because a fallback would INVENT a convention.

        ``resolve_shift`` raised here for its one caller, the extend door.
        That caller reads the convention off the
        :class:`~app.services.pay_calendar.PayCalendar` it already built since
        ``C14-e-1``, and ``calendar_for`` refuses an owner with no schedule row
        -- so the refusal did not disappear, it moved to the HARD door, and
        this SOFT one keeps answering ``None`` as it always has.  Both halves
        are asserted, because "the refusal moved" is only true if it arrived.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            assert pay_schedule_service.resolve_schedule(user_id) is None

            with pytest.raises(PayCalendarError, match="has no pay calendar"):
                calendar_for(user_id)
