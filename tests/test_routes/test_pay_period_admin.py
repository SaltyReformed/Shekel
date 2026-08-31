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
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import StatusEnum
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.services import (
    pay_period_admin,
    pay_period_write,
    pay_schedule_service,
)
from app.routes import settings as settings_routes
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from tests._test_helpers import (
    add_txn,
    all_periods,
    create_savings_account,
    freeze_today,
    make_expense_template,
    make_transfer_template,
    open_calendar_hole,
)
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
    return {p.period_index for p in all_periods(user_id)}


def _starts(user_id):
    """The user's current period paydays.

    What the truncate assertions read since plan step C3-a: the operation is
    now defined on the payday rather than on the ordinal, and the payday is the
    column that survives plan step C4.
    """
    return {p.start_date for p in all_periods(user_id)}


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
            assert len(all_periods(
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


class TestHistoryRoute:
    """POST /pay-periods/history (when the owner's paychecks started).

    Plan step **balance:X-bh-2**, ruling **balance:R-IA**.  The SECOND door
    onto ``budget.pay_schedule.history_opens_on``, and the only one an
    existing owner has: registration asks the question once, and every owner
    who signed up before the column existed holds ``NULL`` with no sign-up
    form left to revisit.
    """

    def test_it_saves_the_day_and_redirects(
        self, app, db, auth_client, seed_user,
    ):
        """The ordinary save, and the CONTROL for the refusals below.

        **Re-read on a FRESH session, which is the whole difference between
        this asserting a COMMIT and asserting a flush.**  An adversarial review
        of plan step balance:X-bh-2 deleted this route's ``db.session.commit()``
        and 817 cases stayed green: the test wraps its post in
        ``with app.app_context()``, Flask reuses an already-pushed app context
        rather than pushing the request's own, so ``teardown_appcontext`` never
        fires, Flask-SQLAlchemy's ``session.remove()`` never runs, and the
        request's FLUSH is still visible to a query in the same session.  In
        production the answer would be discarded at teardown while the page
        still flashed "Saved when your paychecks started."

        ``db.session.remove()`` discards anything uncommitted, so what the
        assertion below reads came off the database.  This is the project's
        own recorded lesson -- a staged mutation check cannot tell a rollback
        from a flush -- in a new instance.
        """
        with app.app_context():
            pay_schedule_service.upsert_schedule(seed_user["user"].id, 14)
            db.session.commit()

            resp = auth_client.post(
                "/pay-periods/history",
                data={"history_opens_on": "2023-06-03"},
            )

            assert resp.status_code == 302
            assert "pay-periods" in resp.headers["Location"]
            db.session.remove()
            assert pay_schedule_service.get_schedule(
                seed_user["user"].id,
            ).history_opens_on == date(2023, 6, 3)

    def test_an_empty_box_CLEARS_a_stored_day(
        self, app, db, auth_client, seed_user,
    ):
        """A cleared control is a real answer, not a missing one.

        An HTML form submits every control it renders, so the untouched field
        arrives as ``""`` -- and it has to become ``NULL`` rather than "leave
        it alone", because clearing it is how an owner says "I have been paid
        this way longer than the app needs to know".  A door that read the
        empty box as no-change would make the field unclearable from the UI.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            pay_schedule_service.upsert_schedule(user_id, 14)
            pay_schedule_service.set_history_opening(user_id, date(2023, 6, 3))
            db.session.commit()

            resp = auth_client.post(
                "/pay-periods/history", data={"history_opens_on": ""},
            )

            assert resp.status_code == 302
            # Fresh session: the CLEAR has to be committed too, and a flushed
            # NULL is indistinguishable from a committed one in the session
            # the request left behind.  See the case above.
            db.session.remove()
            assert pay_schedule_service.get_schedule(
                user_id,
            ).history_opens_on is None

    def test_a_day_outside_the_apps_calendar_flashes_and_stores_nothing(
        self, app, db, auth_client, seed_user,
    ):
        """The schema bound as a rendered message, never an IntegrityError 500.

        An ``<input type="date">`` accepts a five-digit year, so this arrives
        from an ordinary browser rather than from a crafted post.
        """
        with app.app_context():
            pay_schedule_service.upsert_schedule(seed_user["user"].id, 14)
            db.session.commit()

            resp = auth_client.post(
                "/pay-periods/history",
                data={"history_opens_on": "9999-01-01"},
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert pay_schedule_service.get_schedule(
                seed_user["user"].id,
            ).history_opens_on is None

    def test_a_day_after_the_first_payday_flashes_the_services_message(
        self, app, db, auth_client, seed_user,
    ):
        """Paychecks cannot have begun after the first one the app holds.

        The service's own sentence reaches the page, so the owner is told
        which day it conflicts with rather than being sent to look.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id,
                first_payday=date(2026, 7, 3),
                num_periods=3,
                cadence_days=14,
            )
            db.session.commit()
            opening = min(p.start_date for p in all_periods(user_id))

            resp = auth_client.post(
                "/pay-periods/history",
                data={
                    "history_opens_on": (
                        opening + timedelta(days=1)
                    ).isoformat(),
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert opening.isoformat().encode() in resp.data
            assert pay_schedule_service.get_schedule(
                user_id,
            ).history_opens_on is None

    def test_no_schedule_row_flashes_error(self, app, auth_client, seed_user):
        """A floor bounds a rhythm, and a row-less owner has no cadence."""
        with app.app_context():
            assert pay_schedule_service.get_schedule(
                seed_user["user"].id,
            ) is None

            resp = auth_client.post(
                "/pay-periods/history",
                data={"history_opens_on": "2023-06-03"},
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"Generate a pay-period schedule" in resp.data
            assert pay_schedule_service.get_schedule(
                seed_user["user"].id,
            ) is None

    def test_companion_cannot_set_the_history_opening(
        self, app, companion_client,
    ):
        """A companion is not the owner -- the route 404s (IDOR)."""
        with app.app_context():
            resp = companion_client.post(
                "/pay-periods/history",
                data={"history_opens_on": "2023-06-03"},
            )

            assert resp.status_code == 404

    def test_the_route_EXISTS_for_a_signed_in_owner(self, app, auth_client):
        """Pairs with the 404 above, which a moved route would leave passing.

        A 404 from the URL map and a 404 from ``require_owner`` are
        indistinguishable, so the ownership case alone would go on passing if
        this endpoint were renamed or removed.
        """
        with app.app_context():
            resp = auth_client.post(
                "/pay-periods/history", data={"history_opens_on": ""},
            )

            assert resp.status_code != 404


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
            periods = all_periods(user_id)
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
            # And the pay-history card (plan step balance:X-bh-2), which is
            # the only door an already-registered owner has onto that column.
            assert b"When your paychecks started" in resp.data
            assert b'name="history_opens_on"' in resp.data

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
            owner_periods = all_periods(user_id)
            assert [p.id for p in periods] != [p.period_index for p in periods]

            resp = auth_client.get("/settings?section=pay-periods")

            assert resp.status_code == 200
            assert b'name="keep_through_period_id"' in resp.data
            for period in owner_periods:
                assert f'<option value="{period.id}">'.encode() in resp.data

    def test_the_history_card_is_prefilled_from_the_schedule_row(
        self, app, db, auth_client, seed_user,
    ):
        """A stored opening comes back in the control, not as a blank box.

        Plan step **balance:X-bh-2**.  A form that renders empty over a stored
        value teaches the owner they have not answered, and the next Save
        CLEARS it -- the field's empty box is a real answer, so an unprefilled
        control is a silent write rather than a cosmetic bug.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _future_periods(db.session, seed_user, count=3)
            pay_schedule_service.set_history_opening(user_id, date(2023, 6, 3))
            db.session.commit()

            resp = auth_client.get("/settings?section=pay-periods")

            assert resp.status_code == 200
            assert b'value="2023-06-03"' in resp.data

    def test_the_history_card_renders_empty_when_nothing_is_stored(
        self, app, db, auth_client, seed_user,
    ):
        """THE CONTROL: the prefill is read, not a literal in the template."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            pay_schedule_service.set_history_opening(
                seed_user["user"].id, None,
            )
            db.session.commit()

            resp = auth_client.get("/settings?section=pay-periods")

            assert resp.status_code == 200
            assert b'id="history_opens_on"' in resp.data
            assert b'value="2023-06-03"' not in resp.data

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


class TestTheManageListIsTheDerivation:
    """The rendered period list answers from the paydays, not the columns.

    Plan step **C2-f3b**.  This page is where a user reviews the schedule
    BEFORE pressing a destructive button on it, and the button's service decides
    against the derivation -- so the list beside it has to describe the same
    periods or the confirmation is about a different schedule from the one that
    changes.  Both halves of a rendered row are graded: the LABEL, which the ORM
    accessor built from the stored ``end_date``, and the lock BADGE, whose
    "Past" chip was decided on the process clock.
    """

    def test_the_label_follows_the_paydays_not_the_stored_end(
        self, app, db, auth_client, seed_user,
    ):
        """A shortened stored ``end_date`` does not move the rendered label.

        The first generated period runs 2026-07-03 .. 2026-07-16, because the
        next payday is 2026-07-17.  Its stored column is shortened to 07-05 --
        the shape a row written before plan step C3-b can hold -- and the page
        must still say 07/16, because that is when the paycheck actually ends.
        Both strings are asserted, so a render that dropped the label entirely
        cannot pass the negative half alone.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            open_calendar_hole(db.session, periods[0], date(2026, 7, 5))
            db.session.commit()

            # FIRING CONTROL, and an adversarial review of this step is why it
            # is here: both assertions below are true of the UN-doctored row --
            # the writer already materialises the derived end -- so with the
            # fixture neutered this case was measured passing while grading
            # nothing.  Re-read by primary key, because the plant is written
            # through a re-loaded instance and the caller's is detached.
            assert db.session.get(
                PayPeriod, periods[0].id,
            ).end_date == date(2026, 7, 5)

            resp = auth_client.get("/settings?section=pay-periods")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "07/03 - 07/16" in html
            assert "07/03 - 07/05" not in html

    def test_the_past_badge_follows_the_owners_civil_day(
        self, app, db, auth_client, seed_user, monkeypatch,
    ):
        """"Past" is decided on ``display_today``, ruled 2026-08-19.

        The clocks are pinned APART: the process clock stays on this module's
        2026-06-15 and the display clock reads 2026-07-17.  TWO "Past" chips must
        render -- an exact count, so a badge map that marked everything
        historical fails here too.

        **Both chips are the display clock's, and an adversarial review of this
        step corrected a comment that said otherwise.**  ``_future_periods``
        records paydays through the writer, which re-materialises the whole
        calendar, so the 2024 bootstrap ends the day before the first generated
        payday -- 2026-07-02, not its seeded 2024-01-18.  On the process clock
        it has NOT ended either, which is why the mutation this case exists to
        kill reports ``0 == 2`` rather than ``1 == 2``.
        """
        monkeypatch.setattr(
            settings_routes, "display_today", lambda: date(2026, 7, 17),
        )
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            resp = auth_client.get("/settings?section=pay-periods")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The bootstrap (derived end 2026-07-02) and the first generated
            # period (2026-07-16); neither has ended on the process clock.
            assert html.count(">Past<") == 2


class TestEveryDoorThatCreatesAPeriodPopulatesIt:
    """Every HTTP door that creates a pay period fills it with its templates.

    **This class is what makes ruling R-R38 safe.**  Until plan step R7d-c-1
    the recurring rows were generated INSIDE ``extend_pay_periods`` /
    ``regenerate_pay_periods`` / ``reset_pay_periods``, so no caller could get
    them wrong.  They are now a second call the ROUTE makes -- the read pass
    the recurrence resolves in may only be opened at the HTTP boundary, and
    only after the paydays exist -- and a route that made the first call and
    not the second would ship paydays with no rent, no paycheck and no
    recurring transfer in them, with every period-count assertion in this file
    still green.

    So the coverage is stated where the composition now lives: one case per
    door, each asserting the ROWS rather than the count, each driven through
    the real HTTP request the browser makes.  ``test_pay_period_extend`` and
    its siblings grade the two halves separately; this grades that the doors
    call both.

    SIX doors create a pay period through HTTP: extend, regenerate, reset and
    generate directly, and the rolling top-up through ``GET /grid`` and
    ``GET /dashboard``. The sixth, ``POST /pay-periods/generate``, populated
    NOTHING until this step -- ledger row **D58**, pre-existing and found by
    censusing the writers R-R38 split. It reads as a first-time-only door and
    is not: ``record_paydays``' forward-only rule accepts any payday past the
    owner's last, so on an owner who already had a schedule it appended
    periods and skipped every template, measured through this same HTTP door
    at 3 appended periods holding 0 template rows.

    The seventh writer, ``auth_service.register_user``, has no HTTP door of
    its own here and is correct as it stands: no template can exist at
    registration, and the baseline scenario is created after that call, so a
    repopulation would return 0 on ``ctx.scenario is None``.
    """

    _AMOUNT = Decimal("1200.00")

    def _rows_in(self, db_session, period):
        """The template-linked transactions sitting in *period*."""
        return (
            db_session.query(Transaction)
            .filter_by(pay_period_id=period.id)
            .filter(Transaction.template_id.isnot(None))
            .all()
        )

    def _assert_each_period_holds_the_rent(self, db_session, periods):
        """Every period in *periods* holds exactly one row at the template's amount."""
        assert periods, "no period was created, so nothing is graded"
        for period in periods:
            rows = self._rows_in(db_session, period)
            assert len(rows) == 1, (
                f"pay period {period.id} (payday {period.start_date}) holds "
                f"{len(rows)} template rows, not 1 -- the door created the "
                f"period and its caller did not populate it"
            )
            assert rows[0].estimated_amount == self._AMOUNT

    def test_the_extend_route_populates_what_it_appends(
        self, app, db, auth_client, seed_user,
    ):
        """POST /pay-periods/extend leaves no empty paycheck behind.

        **The one case of the five that seeds BOTH engines**, because the
        producer runs two loops and a caller that ran only the transaction one
        would leave every other case here green -- "a new period never silently
        misses a recurring transfer" is that producer's own stated invariant.
        """
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            make_expense_template(db.session, seed_user, amount="1200.00")
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            make_transfer_template(db.session, seed_user, savings)
            db.session.commit()
            before = {p.id for p in all_periods(seed_user["user"].id)}

            resp = auth_client.post(
                "/pay-periods/extend", data={"num_periods": "2"},
            )
            assert resp.status_code == 302

            db.session.expire_all()
            appended = [
                p for p in all_periods(seed_user["user"].id)
                if p.id not in before
            ]
            assert len(appended) == 2
            self._assert_each_period_holds_the_rent(db.session, appended)
            for period in appended:
                transfers = (
                    db.session.query(Transfer)
                    .filter_by(pay_period_id=period.id)
                    .all()
                )
                assert len(transfers) == 1, (
                    f"pay period {period.id} holds {len(transfers)} recurring "
                    f"transfers, not 1 -- the route ran the transaction engine "
                    f"and not the transfer one"
                )
                assert len(transfers[0].shadow_transactions) == 2

    def test_the_regenerate_route_populates_the_tail_it_rebuilds(
        self, app, db, auth_client, seed_user,
    ):
        """POST /pay-periods/regenerate fills the tail it just rebuilt."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=6)
            make_expense_template(db.session, seed_user, amount="1200.00")
            db.session.commit()
            before = {p.id for p in all_periods(seed_user["user"].id)}

            resp = auth_client.post(
                "/pay-periods/regenerate",
                data={
                    "new_start_date": "2026-08-01",
                    "num_periods": "3",
                    "cadence_days": "14",
                },
            )
            assert resp.status_code == 302

            db.session.expire_all()
            rebuilt = [
                p for p in all_periods(seed_user["user"].id)
                if p.id not in before
            ]
            assert len(rebuilt) == 3
            self._assert_each_period_holds_the_rent(db.session, rebuilt)

    def test_the_reset_route_populates_the_schedule_it_rebuilds(
        self, app, db, auth_client, seed_user,
    ):
        """POST /pay-periods/reset fills every period of the new schedule.

        The reset is the door whose populate moved FURTHEST at R-R38: it used
        to run between the rebuild and the two posting re-syncs and now runs
        after both, so this case is also what grades that reordering through
        the real door.
        """
        with app.app_context():
            _future_periods(db.session, seed_user, count=4)
            make_expense_template(db.session, seed_user, amount="1200.00")
            db.session.commit()

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

            db.session.expire_all()
            periods = all_periods(seed_user["user"].id)
            assert len(periods) == 4
            self._assert_each_period_holds_the_rent(db.session, periods)

    def test_the_generate_route_populates_what_it_appends(
        self, app, db, auth_client, seed_user,
    ):
        """POST /pay-periods/generate fills the periods it records.

        **Ledger row D58, and the fixture is the shape that made it a defect
        rather than a no-op**: the owner ALREADY has a schedule and an active
        template, which is the state ``record_paydays``' forward-only rule
        admits and which this door used to append into without generating a
        row. A brand-new owner cannot exercise it -- nothing to generate.
        """
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            make_expense_template(db.session, seed_user, amount="1200.00")
            db.session.commit()
            before = {p.id for p in all_periods(seed_user["user"].id)}
            latest = max(p.start_date for p in all_periods(seed_user["user"].id))

            resp = auth_client.post(
                "/pay-periods/generate",
                data={
                    "start_date": (latest + timedelta(days=14)).isoformat(),
                    "num_periods": "3",
                    "cadence_days": "14",
                },
            )
            assert resp.status_code == 302

            db.session.expire_all()
            appended = [
                p for p in all_periods(seed_user["user"].id)
                if p.id not in before
            ]
            assert len(appended) == 3, (
                f"the door appended {len(appended)} periods, not 3; the "
                f"forward-only rule must have refused, and then this case "
                f"grades nothing"
            )
            self._assert_each_period_holds_the_rent(db.session, appended)

    def _rolling_deficit(self, db_session, seed_user, target=5):
        """A rolling owner short of *target*, holding an every-period template."""
        pay_period_write.record_paydays(
            user_id=seed_user["user"].id, first_payday=date(2026, 6, 8),
            num_periods=2, cadence_days=14,
        )
        make_expense_template(db_session, seed_user, amount="1200.00")
        pay_schedule_service.upsert_schedule(seed_user["user"].id, 14)
        pay_schedule_service.set_rolling(
            seed_user["user"].id, enabled=True, target_periods=target,
        )
        db_session.commit()
        return {p.id for p in all_periods(seed_user["user"].id)}

    def test_the_grid_render_populates_what_the_top_up_appends(
        self, app, db, auth_client, seed_user,
    ):
        """GET /grid fills the paydays its rolling top-up just appended."""
        with app.app_context():
            before = self._rolling_deficit(db.session, seed_user)
            resp = auth_client.get("/grid")
            assert resp.status_code == 200

            db.session.expire_all()
            appended = [
                p for p in all_periods(seed_user["user"].id)
                if p.id not in before
            ]
            self._assert_each_period_holds_the_rent(db.session, appended)

    def test_the_dashboard_render_populates_what_the_top_up_appends(
        self, app, db, auth_client, seed_user,
    ):
        """GET /dashboard fills the paydays its rolling top-up just appended."""
        with app.app_context():
            before = self._rolling_deficit(db.session, seed_user)
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200

            db.session.expire_all()
            appended = [
                p for p in all_periods(seed_user["user"].id)
                if p.id not in before
            ]
            self._assert_each_period_holds_the_rent(db.session, appended)
