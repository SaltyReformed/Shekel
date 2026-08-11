"""Route tests for the pay-period management actions (slice f).

Exercises the extend / truncate / regenerate routes end to end through
the HTTP layer: success redirects + DB effects, schema rejection, the
hard-lock and discard-confirm responses, the cadence-persist on generate,
owner-only access (a companion gets 404), and that the settings
"pay-periods" section renders the manage UI.  ``today`` is pinned so the
future-period setup is deterministic.  See
``docs/plans/implementation_plan_pay_period_crud.md``.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest

from app.enums import StatusEnum
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.services import (
    pay_period_admin,
    pay_period_service,
    pay_period_write,
    pay_schedule_service,
)
from tests._test_helpers import add_txn, freeze_today
from app.services import cash_ledger


FROZEN_TODAY = date(2026, 6, 15)


@pytest.fixture(autouse=True)
def _freeze(monkeypatch):
    """Pin ``date.today()`` so the future-period setup is deterministic."""
    freeze_today(monkeypatch, FROZEN_TODAY)


def _future_periods(db_session, seed_user, count=6):
    """Generate `count` future biweekly periods (after the bootstrap)."""
    periods = pay_period_write.record_paydays(
        user_id=seed_user["user"].id,
        first_payday=date(2026, 7, 3),
        num_periods=count,
        cadence_days=14,
    )
    db_session.commit()
    return periods


def _period_count(db_session, user_id):
    """Count the user's pay periods."""
    return db_session.query(PayPeriod).filter_by(user_id=user_id).count()


def _indices(user_id):
    """The user's current period indices."""
    return {p.period_index for p in pay_period_service.get_all_periods(user_id)}


def _starts(user_id):
    """The user's current period paydays.

    What the truncate assertions read since plan step C3-a: the operation is
    now defined on the payday rather than on the ordinal, and the payday is the
    column that survives plan step C4.
    """
    return {p.start_date for p in pay_period_service.get_all_periods(user_id)}


def _future_count(db_session, user_id):
    """Current-and-future periods (``end_date >= FROZEN_TODAY``)."""
    return (
        db_session.query(PayPeriod)
        .filter(
            PayPeriod.user_id == user_id,
            PayPeriod.end_date >= FROZEN_TODAY,
        )
        .count()
    )


class TestExtendRoute:
    """POST /pay-periods/extend."""

    def test_adds_periods_and_redirects(self, app, db, auth_client, seed_user):
        """A valid extend appends periods and redirects to the section."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            before = _period_count(db.session, seed_user["user"].id)
            resp = auth_client.post(
                "/pay-periods/extend", data={"num_periods": "2"},
            )
            assert resp.status_code == 302
            assert "pay-periods" in resp.headers["Location"]
            assert _period_count(
                db.session, seed_user["user"].id,
            ) == before + 2

    def test_rejects_out_of_range_count(self, app, db, auth_client, seed_user):
        """num_periods = 0 fails validation; nothing is added."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            before = _period_count(db.session, seed_user["user"].id)
            resp = auth_client.post(
                "/pay-periods/extend", data={"num_periods": "0"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"correct the form" in resp.data
            assert _period_count(db.session, seed_user["user"].id) == before


class TestTruncateRoute:
    """POST /pay-periods/truncate."""

    def test_removes_tail_and_redirects(self, app, db, auth_client, seed_user):
        """A valid truncate deletes everything past the named period."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            keep = periods[2].id  # the 3rd future period
            resp = auth_client.post(
                "/pay-periods/truncate",
                data={"keep_through_period_id": str(keep)},
            )
            assert resp.status_code == 302
            assert max(_starts(seed_user["user"].id)) == periods[2].start_date

    def test_settled_period_blocked_nothing_deleted(
        self, app, db, auth_client, seed_user,
    ):
        """A settled period in the window flashes a lock error, deletes nothing."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            add_txn(
                db.session, seed_user, periods[3], "Paid", "100.00",  # index 4
                status_enum=StatusEnum.DONE,
            )
            db.session.commit()
            before = _period_count(db.session, seed_user["user"].id)
            resp = auth_client.post(
                "/pay-periods/truncate",
                data={"keep_through_period_id": str(periods[1].id)},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"locked" in resp.data
            assert _period_count(db.session, seed_user["user"].id) == before

    def test_discard_shows_confirm_panel(
        self, app, db, auth_client, seed_user,
    ):
        """A hand-entered row triggers the 422 confirm panel, deletes nothing."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            add_txn(db.session, seed_user, periods[3], "Cash", "50.00")
            db.session.commit()
            before = _period_count(db.session, seed_user["user"].id)
            resp = auth_client.post(
                "/pay-periods/truncate",
                data={"keep_through_period_id": str(periods[1].id)},
            )
            assert resp.status_code == 422
            assert b"permanently discard" in resp.data
            assert b"Confirm" in resp.data
            # The panel must echo the ID the user chose, not its ordinal --
            # this hidden field IS the browser round trip finding P13 is
            # about, and asserting only the prose let it regress silently.
            assert (
                f'name="keep_through_period_id" value="{periods[1].id}"'
                .encode() in resp.data
            )
            assert _period_count(db.session, seed_user["user"].id) == before

    def test_confirm_discard_proceeds(self, app, db, auth_client, seed_user):
        """Re-posting what the confirm PANEL rendered completes the truncate.

        The id is parsed back out of the 422 body rather than passed by hand,
        so the assertion covers the whole round trip the finding is about: what
        the panel put on the wire is what the second post acts on.  Passing it
        by hand tested the service twice and the round trip never.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            add_txn(db.session, seed_user, periods[3], "Cash", "50.00")
            db.session.commit()
            confirm = auth_client.post(
                "/pay-periods/truncate",
                data={"keep_through_period_id": str(periods[1].id)},
            )
            assert confirm.status_code == 422
            echoed = re.search(
                rb'name="keep_through_period_id" value="(\d+)"', confirm.data,
            )
            assert echoed is not None

            resp = auth_client.post(
                "/pay-periods/truncate",
                data={
                    "keep_through_period_id": echoed.group(1).decode(),
                    "confirm_discard": "true",
                },
            )
            assert resp.status_code == 302
            assert max(_starts(seed_user["user"].id)) == periods[1].start_date

    def test_a_stale_period_id_is_refused_and_deletes_nothing(
        self, app, db, auth_client, seed_user,
    ):
        """The confirm panel's re-submitted id no longer names a period.

        Finding **P13**'s live shape at the HTTP boundary: the discard-confirm
        422 echoes the chosen period into a hidden field and the user posts it
        back, so the value crosses a request boundary that ``user_write_lock``
        cannot span.  Keyed on ``id`` (plan step C3-a) a period deleted in
        between is REFUSED; keyed on the old ordinal the same post named
        whichever period had since slid into that position.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            user_id = seed_user["user"].id
            # The user reviews period #4 in the confirm panel; a concurrent
            # truncate then removes it and everything after.
            reviewed_id = periods[3].id
            pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[1].id,
            )
            db.session.commit()
            before = _period_count(db.session, user_id)

            resp = auth_client.post(
                "/pay-periods/truncate",
                data={
                    "keep_through_period_id": str(reviewed_id),
                    "confirm_discard": "true",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"no longer exists" in resp.data
            assert _period_count(db.session, user_id) == before


class TestRegenerateRoute:
    """POST /pay-periods/regenerate."""

    def test_rebuilds_tail_and_redirects(self, app, db, auth_client, seed_user):
        """Regenerate rebuilds the future tail from the corrected start."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=6)
            resp = auth_client.post(
                "/pay-periods/regenerate",
                data={
                    "new_start_date": "2026-08-01",
                    "num_periods": "3",
                    "cadence_days": "14",
                },
            )
            assert resp.status_code == 302
            # Bootstrap (index 0) survives; the 6 future periods become 3.
            assert len(pay_period_service.get_all_periods(
                seed_user["user"].id,
            )) == 4


class TestGenerateRoute:
    """POST /pay-periods/generate persists the cadence."""

    def test_generate_persists_cadence(self, app, auth_client, seed_user):
        """Generating captures the cadence in a pay_schedule row."""
        with app.app_context():
            resp = auth_client.post(
                "/pay-periods/generate",
                data={
                    "start_date": "2027-01-01",
                    "num_periods": "4",
                    "cadence_days": "10",
                },
            )
            assert resp.status_code == 302
            schedule = pay_schedule_service.get_schedule(seed_user["user"].id)
            assert schedule is not None
            assert schedule.cadence_days == 10


class TestScheduleRoute:
    """POST /pay-periods/schedule (continuous-rolling-window config)."""

    def test_saves_rolling_config_and_redirects(
        self, app, db, auth_client, seed_user,
    ):
        """A valid post enables rolling and stores the target on the row."""
        with app.app_context():
            # A schedule row must exist first (generation captures cadence).
            pay_schedule_service.upsert_schedule(seed_user["user"].id, 14)
            db.session.commit()
            resp = auth_client.post(
                "/pay-periods/schedule",
                data={
                    "rolling_enabled": "true",
                    "rolling_target_periods": "30",
                },
            )
            assert resp.status_code == 302
            assert "pay-periods" in resp.headers["Location"]
            schedule = pay_schedule_service.get_schedule(seed_user["user"].id)
            assert schedule.rolling_enabled is True
            assert schedule.rolling_target_periods == 30

    def test_rejects_out_of_range_target(
        self, app, db, auth_client, seed_user,
    ):
        """target_periods = 0 fails validation; the row stays unchanged."""
        with app.app_context():
            pay_schedule_service.upsert_schedule(seed_user["user"].id, 14)
            db.session.commit()
            resp = auth_client.post(
                "/pay-periods/schedule",
                data={
                    "rolling_enabled": "true",
                    "rolling_target_periods": "0",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"correct the form" in resp.data
            schedule = pay_schedule_service.get_schedule(seed_user["user"].id)
            assert schedule.rolling_enabled is False

    def test_no_schedule_row_flashes_error(self, app, auth_client, seed_user):
        """Configuring rolling before generating a schedule is refused.

        seed_user has a bootstrap period but no pay_schedule row, so the
        service guard raises ValidationError and the route flashes it;
        nothing is created.
        """
        with app.app_context():
            assert pay_schedule_service.get_schedule(
                seed_user["user"].id,
            ) is None
            resp = auth_client.post(
                "/pay-periods/schedule",
                data={
                    "rolling_enabled": "true",
                    "rolling_target_periods": "10",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Generate a pay-period schedule" in resp.data
            assert pay_schedule_service.get_schedule(
                seed_user["user"].id,
            ) is None

    def test_companion_cannot_set_schedule(self, app, companion_client):
        """A companion is not the owner -- the schedule route 404s (IDOR)."""
        with app.app_context():
            resp = companion_client.post(
                "/pay-periods/schedule",
                data={
                    "rolling_enabled": "true",
                    "rolling_target_periods": "10",
                },
            )
            assert resp.status_code == 404


class TestResetRoute:
    """POST /pay-periods/reset (bounded full-schedule reset)."""

    def test_reset_rebuilds_and_reanchors(self, app, db, auth_client, seed_user):
        """A confirmed reset wipes everything and rebuilds from index 0.

        It proved the DEFERRED-FK commit path end to end through HTTP; rulings
        R-EH and R-EO deleted the FK, so what it proves now is that the rebuild
        succeeds over HTTP and the user's asserted balance survives it.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account_id = seed_user["account"].id
            _future_periods(db.session, seed_user, count=4)
            resp = auth_client.post(
                "/pay-periods/reset",
                data={
                    "new_start_date": "2026-06-05",
                    "num_periods": "4",
                    "cadence_days": "14",
                    "confirm": "true",
                },
            )
            assert resp.status_code == 302
            assert "pay-periods" in resp.headers["Location"]
            db.session.expire_all()
            periods = pay_period_service.get_all_periods(user_id)
            assert {p.period_index for p in periods} == {0, 1, 2, 3}
            account = db.session.get(Account, account_id)
            assert cash_ledger.resolve_anchor(account).balance == Decimal("1000.00")

    def test_unconfirmed_reset_refused(self, app, db, auth_client, seed_user):
        """Without the confirm box, reset changes nothing."""
        with app.app_context():
            user_id = seed_user["user"].id
            _future_periods(db.session, seed_user, count=4)
            before = _indices(user_id)
            resp = auth_client.post(
                "/pay-periods/reset",
                data={
                    "new_start_date": "2026-06-05",
                    "num_periods": "4",
                    "cadence_days": "14",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Confirm the reset" in resp.data
            assert _indices(user_id) == before

    def test_settled_blocks_reset(self, app, db, auth_client, seed_user):
        """A settled transaction makes the service refuse; nothing changes."""
        with app.app_context():
            user_id = seed_user["user"].id
            periods = _future_periods(db.session, seed_user, count=4)
            add_txn(
                db.session, seed_user, periods[1], "Paycheck", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
            )
            db.session.commit()
            before = _indices(user_id)
            resp = auth_client.post(
                "/pay-periods/reset",
                data={
                    "new_start_date": "2026-06-05",
                    "num_periods": "4",
                    "cadence_days": "14",
                    "confirm": "true",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"settled transaction" in resp.data
            assert _indices(user_id) == before

    def test_schema_rejection_changes_nothing(
        self, app, db, auth_client, seed_user,
    ):
        """A missing required field fails validation; nothing changes."""
        with app.app_context():
            user_id = seed_user["user"].id
            _future_periods(db.session, seed_user, count=4)
            before = _indices(user_id)
            resp = auth_client.post(
                "/pay-periods/reset",
                data={
                    "num_periods": "4", "cadence_days": "14", "confirm": "true",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"correct the form" in resp.data
            assert _indices(user_id) == before

    def test_companion_cannot_reset(self, app, companion_client):
        """A companion is not the owner -- the reset route 404s (IDOR)."""
        with app.app_context():
            resp = companion_client.post(
                "/pay-periods/reset",
                data={
                    "new_start_date": "2026-06-05",
                    "num_periods": "4",
                    "cadence_days": "14",
                    "confirm": "true",
                },
            )
            assert resp.status_code == 404


class TestRollingTriggerHooks:
    """The grid + dashboard entry points top up the rolling window."""

    def _setup_rolling_with_deficit(self, db_session, seed_user, target):
        """Current + one future period, rolling on at ``target`` (a deficit).

        idx 1 spans the frozen today (06-08..06-21) so a current period
        exists; idx 2 is the next future period.  ``end_date >= today``
        counts both, so the window starts at 2 and is short of ``target``.
        """
        pay_period_write.record_paydays(
            user_id=seed_user["user"].id, first_payday=date(2026, 6, 8),
            num_periods=2, cadence_days=14,
        )
        pay_schedule_service.upsert_schedule(seed_user["user"].id, 14)
        pay_schedule_service.set_rolling(
            seed_user["user"].id, enabled=True, target_periods=target,
        )
        db_session.commit()

    def test_grid_load_tops_up_window(self, app, db, auth_client, seed_user):
        """GET /grid with rolling on + a deficit fills the window to target."""
        with app.app_context():
            self._setup_rolling_with_deficit(db.session, seed_user, target=5)
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            assert _future_count(db.session, seed_user["user"].id) == 5

    def test_dashboard_load_tops_up_window(
        self, app, db, auth_client, seed_user,
    ):
        """GET /dashboard with rolling on + a deficit fills the window."""
        with app.app_context():
            self._setup_rolling_with_deficit(db.session, seed_user, target=5)
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert _future_count(db.session, seed_user["user"].id) == 5

    def test_grid_load_disabled_creates_nothing(
        self, app, db, auth_client, seed_user,
    ):
        """GET /grid with rolling disabled leaves the schedule unchanged."""
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=seed_user["user"].id, first_payday=date(2026, 6, 8),
                num_periods=2, cadence_days=14,
            )
            pay_schedule_service.upsert_schedule(seed_user["user"].id, 14)
            db.session.commit()
            before = _period_count(db.session, seed_user["user"].id)
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            assert _period_count(db.session, seed_user["user"].id) == before


class TestOwnerOnlyAndUi:
    """Owner-only access and the manage-UI render."""

    def test_companion_cannot_extend(self, app, companion_client):
        """A companion is not the owner -- the route 404s."""
        with app.app_context():
            resp = companion_client.post(
                "/pay-periods/extend", data={"num_periods": "2"},
            )
            assert resp.status_code == 404

    def test_settings_section_renders_manage_ui(
        self, app, db, auth_client, seed_user,
    ):
        """The pay-periods section shows the period list and action forms."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            resp = auth_client.get("/settings?section=pay-periods")
            assert resp.status_code == 200
            assert b"Manage Schedule" in resp.data
            assert b"Extend forward" in resp.data
            assert b"Remove the tail" in resp.data
            assert b"Regenerate the tail" in resp.data
            # The rolling-window controls render too.
            assert b"Continuous rolling window" in resp.data
            assert b'name="rolling_target_periods"' in resp.data

    def test_the_truncate_select_offers_ids_not_ordinals(
        self, app, db, auth_client, seed_user,
    ):
        """The destructive select's option VALUES are pay-period ids.

        Finding **P13**'s gate at the surface that produces it.  The service
        refusing an unresolvable id is only half the fix: if this select went
        back to rendering ``period_index`` as its value, every posted ordinal
        would resolve to no period and the control would break loudly rather
        than destroy the wrong rows -- but it would break.  Asserting the
        rendered value keeps the form and the schema on one key.

        The seeded owner's periods deliberately have ids that are NOT their
        ordinals (the bootstrap period is created first, and ids come from a
        shared sequence), so an ordinal render cannot coincidentally pass.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            user_id = seed_user["user"].id
            all_periods = pay_period_service.get_all_periods(user_id)
            assert [p.id for p in periods] != [p.period_index for p in periods]

            resp = auth_client.get("/settings?section=pay-periods")

            assert resp.status_code == 200
            assert b'name="keep_through_period_id"' in resp.data
            for period in all_periods:
                assert f'<option value="{period.id}">'.encode() in resp.data

    def test_rolling_controls_prefilled_from_schedule(
        self, app, db, auth_client, seed_user,
    ):
        """The rolling controls reflect the saved schedule (checked + target)."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            pay_schedule_service.upsert_schedule(seed_user["user"].id, 14)
            pay_schedule_service.set_rolling(
                seed_user["user"].id, enabled=True, target_periods=40,
            )
            db.session.commit()
            resp = auth_client.get("/settings?section=pay-periods")
            assert resp.status_code == 200
            # Switch reflects enabled state; target input reflects 40.
            assert b"checked" in resp.data
            assert b'value="40"' in resp.data

    def test_reset_form_shown_when_no_settled(
        self, app, db, auth_client, seed_user,
    ):
        """With no settled txns, the full-reset form (with confirm) is offered."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            resp = auth_client.get("/settings?section=pay-periods")
            assert resp.status_code == 200
            assert b"Reset entire schedule" in resp.data
            assert b'name="confirm"' in resp.data
            assert b"/pay-periods/reset" in resp.data

    def test_reset_form_hidden_when_settled(
        self, app, db, auth_client, seed_user,
    ):
        """A settled txn hides the reset form and shows the Regenerate note."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            add_txn(
                db.session, seed_user, periods[1], "Paycheck", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
            )
            db.session.commit()
            resp = auth_client.get("/settings?section=pay-periods")
            assert resp.status_code == 200
            assert b"Reset is unavailable" in resp.data
            assert b'name="confirm"' not in resp.data
