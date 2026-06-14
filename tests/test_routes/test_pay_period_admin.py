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

from datetime import date

import pytest

from app.enums import StatusEnum
from app.models.pay_period import PayPeriod
from app.services import pay_period_service, pay_schedule_service
from tests._test_helpers import add_txn, freeze_today


FROZEN_TODAY = date(2026, 6, 15)


@pytest.fixture(autouse=True)
def _freeze(monkeypatch):
    """Pin ``date.today()`` so the future-period setup is deterministic."""
    freeze_today(monkeypatch, FROZEN_TODAY)


def _future_periods(db_session, seed_user, count=6):
    """Generate `count` future biweekly periods (after the bootstrap)."""
    periods = pay_period_service.generate_pay_periods(
        user_id=seed_user["user"].id,
        start_date=date(2026, 7, 3),
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
        """A valid truncate deletes everything past keep_through_index."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            keep = periods[2].period_index  # index 3
            resp = auth_client.post(
                "/pay-periods/truncate",
                data={"keep_through_index": str(keep)},
            )
            assert resp.status_code == 302
            assert max(_indices(seed_user["user"].id)) == keep

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
                data={"keep_through_index": str(periods[1].period_index)},
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
                data={"keep_through_index": str(periods[1].period_index)},
            )
            assert resp.status_code == 422
            assert b"permanently discard" in resp.data
            assert b"Confirm" in resp.data
            assert _period_count(db.session, seed_user["user"].id) == before

    def test_confirm_discard_proceeds(self, app, db, auth_client, seed_user):
        """Re-posting with confirm_discard=true completes the truncate."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            add_txn(db.session, seed_user, periods[3], "Cash", "50.00")
            db.session.commit()
            keep = periods[1].period_index
            resp = auth_client.post(
                "/pay-periods/truncate",
                data={"keep_through_index": str(keep), "confirm_discard": "true"},
            )
            assert resp.status_code == 302
            assert max(_indices(seed_user["user"].id)) == keep


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
