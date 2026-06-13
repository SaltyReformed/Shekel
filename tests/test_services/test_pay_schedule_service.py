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

from app.services import pay_period_service, pay_schedule_service


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

        ``generate_pay_periods`` sets ``end_date = start + (cadence - 1)``,
        so a 9-day cadence yields ``(end - start).days + 1 == 9``.  Using
        9 -- distinct from both the 14-day default and the 52 horizon --
        proves the value comes from the period length, not a default.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_service.generate_pay_periods(
                user_id=user_id,
                start_date=date(2026, 3, 1),
                num_periods=4,
                cadence_days=9,
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
