"""Tests for ``pay_schedule_service`` (pay-period CRUD Phase 1).

The service owns the per-user ``budget.pay_schedule`` row: the persisted
cadence the extend / regenerate paths continue a schedule from.  Three
behaviours matter:

  * ``get_schedule`` returns the row or ``None``.
  * ``upsert_schedule`` creates a row on first call and updates only
    ``cadence_days`` on later calls, never disturbing rolling config.
  * ``resolve_cadence`` prefers the stored cadence and falls back to
    inferring it from the last period's length for a legacy user with
    periods but no schedule row.

See ``docs/plans/implementation_plan_pay_period_crud.md``.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.exceptions import ValidationError
from app.models.pay_schedule import PaySchedule
from app.services import pay_period_service, pay_period_write, pay_schedule_service


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
    """``resolve_cadence`` prefers the stored cadence, else infers it."""

    def test_prefers_stored_cadence_over_period_length(
        self, app, bare_periods,
    ):
        """A stored cadence wins even when it differs from the periods.

        ``bare_periods`` are 14-day periods; storing a cadence of 10 must
        make ``resolve_cadence`` return 10 (the persisted value), not the
        14 it would infer from the period length.  This proves the
        stored row takes precedence over inference.
        """
        user_id = bare_periods[0].user_id
        with app.app_context():
            pay_schedule_service.upsert_schedule(user_id, cadence_days=10)
            assert pay_schedule_service.resolve_cadence(user_id) == 10

    def test_infers_from_last_period_when_no_schedule(self, app, db, bare_user):
        """A legacy user with periods but no row infers cadence from length.

        The LAST period's end is ``start + (cadence - 1)``, so a 9-day
        cadence yields ``(end - start).days + 1 == 9``.  Using 9 -- distinct
        from both the 14-day default and the 52 horizon -- proves the value
        comes from the period length, not a default.

        **The schedule row is deleted to reach this at all**, and plan step
        C3-b is why: the cadence rule makes every batch that records a payday
        store one, so no door can now leave an owner with paydays and no
        cadence.  Finding **P8**'s state is legacy data from here on, and this
        is the fallback that reads it.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id,
                first_payday=date(2026, 3, 1),
                num_periods=4,
                cadence_days=9,
            )
            db.session.query(PaySchedule).filter_by(user_id=user_id).delete(
                synchronize_session=False,
            )
            db.session.flush()
            assert pay_schedule_service.get_schedule(user_id) is None
            assert pay_schedule_service.resolve_cadence(user_id) == 9

    def test_returns_none_with_no_schedule_and_no_periods(
        self, app, bare_user,
    ):
        """No schedule row and no periods leaves nothing to infer from."""
        with app.app_context():
            assert (
                pay_schedule_service.resolve_cadence(bare_user["user"].id)
                is None
            )
