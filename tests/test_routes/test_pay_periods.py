"""
Shekel Budget App -- Pay Period Route Tests

Tests for the pay period generation form and endpoint:
  - Form rendering
  - Successful generation with defaults and custom values
  - Validation errors (missing/invalid fields)
  - Double-submit (duplicates skipped by service)
"""

from datetime import date

from app.enums import StatusEnum
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.pay_schedule import PaySchedule
from app.services import pay_period_write
from tests._test_helpers import add_txn, freeze_today


# ── Tests ────────────────────────────────────────────────────────────


class TestPayPeriodGenerate:
    """Tests for GET/POST /pay-periods/generate."""

    def test_generate_form_redirects_to_settings(self, app, bare_auth_client, bare_user):
        """GET /pay-periods/generate returns 302 redirect to settings dashboard."""
        with app.app_context():
            resp = bare_auth_client.get("/pay-periods/generate")
            assert resp.status_code == 302
            assert "/settings" in resp.headers["Location"]
            assert "section=pay-periods" in resp.headers["Location"]

    def test_generate_periods_success(self, app, bare_auth_client, bare_user):
        """POST /pay-periods/generate creates periods and redirects to grid."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-03-01",
                "num_periods": "10",
                "cadence_days": "14",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Generated 10 pay periods" in resp.data

            periods = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).all()
            assert len(periods) == 10

    def test_generate_missing_start_date(self, app, bare_auth_client, bare_user):
        """POST /pay-periods/generate without start_date returns 422 with field error."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "num_periods": "10",
            })

            assert resp.status_code == 422
            assert b"Start Date" in resp.data
            assert b"Please fix the following errors" in resp.data

    def test_generate_cadence_zero(self, app, bare_auth_client, bare_user):
        """POST /pay-periods/generate with cadence_days=0 returns 422 with field error."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-03-01",
                "cadence_days": "0",
            })

            assert resp.status_code == 422
            assert b"Cadence Days" in resp.data
            assert b"Please fix the following errors" in resp.data

    def test_generate_single_period(self, app, bare_auth_client, bare_user):
        """POST /pay-periods/generate with num_periods=1 creates one period."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-04-01",
                "num_periods": "1",
                "cadence_days": "14",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Generated 1 pay periods" in resp.data

            periods = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).all()
            assert len(periods) == 1

    def test_generate_double_submit_skips_duplicates(self, app, bare_auth_client, bare_user):
        """Double-submit with same start_date skips overlapping periods."""
        with app.app_context():
            data = {
                "start_date": "2026-05-01",
                "num_periods": "5",
                "cadence_days": "14",
            }

            # First submit.
            bare_auth_client.post("/pay-periods/generate", data=data,
                             follow_redirects=True)
            first_count = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).count()
            assert first_count == 5

            # Second submit with same data -- duplicates should be skipped.
            resp = bare_auth_client.post("/pay-periods/generate", data=data,
                                    follow_redirects=True)
            assert resp.status_code == 200

            second_count = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).count()
            # Should still be 5, not 10 (duplicates skipped).
            assert second_count == 5

    def test_generate_offset_start_rejected_422(self, app, bare_auth_client, bare_user):
        """A payday between two existing ones returns 422 and creates nothing.

        Ruling **R-PC1**'s forward-only rule, through the route.  The bound is
        the latest PAYDAY plus ``MIN_MATERIALISABLE_CADENCE_DAYS`` since plan
        step C3-b -- it was the latest ``end_date``, a column plan step C4
        drops -- and what it now refuses is exactly the mid-schedule insert
        plan step C6 defers.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            # First schedule: Jun 1 biweekly x5 -> last period Jul 27 - Aug 9.
            bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-06-01", "num_periods": "5",
                "cadence_days": "14",
            }, follow_redirects=True)
            assert db.session.query(PayPeriod).filter_by(
                user_id=user_id,
            ).count() == 5

            # Jun 8 lands between the Jun 1 and Jun 15 paydays, so it would
            # split that paycheck in half -- rejected before anything is
            # written.  The latest payday is 2026-07-27, so the floor is
            # 2026-07-29.
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-06-08", "num_periods": "5",
                "cadence_days": "14",
            })
            assert resp.status_code == 422
            # The message names the FLOOR and the payday it is measured
            # from, both of which survive plan step C4.  The latest payday is
            # 2026-07-27 and the cadence is 14, so the floor is 2026-08-10.
            assert b"must fall on or after 2026-08-10" in resp.data
            assert b"2026-07-27" in resp.data
            # Nothing created -- still exactly the original 5.
            assert db.session.query(PayPeriod).filter_by(
                user_id=user_id,
            ).count() == 5


# ── Negative Path Tests ─────────────────────────────────────────────


class TestPayPeriodNegativePaths:
    """Tests for pay period generation validation and edge cases."""

    def test_generate_invalid_date_format(self, app, bare_auth_client, bare_user):
        """Non-date string for start_date returns 422 with validation error."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "not-a-date",
                "num_periods": "10",
                "cadence_days": "14",
            })
            assert resp.status_code == 422
            assert b"Start Date" in resp.data

            count = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_negative_num_periods(self, app, bare_auth_client, bare_user):
        """Negative num_periods returns 422 (Range min=1 on schema)."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-01-02",
                "num_periods": "-5",
                "cadence_days": "14",
            })
            assert resp.status_code == 422

            count = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_zero_num_periods(self, app, bare_auth_client, bare_user):
        """Zero num_periods returns 422 (Range min=1 on schema)."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-01-02",
                "num_periods": "0",
                "cadence_days": "14",
            })
            assert resp.status_code == 422

            count = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_extremely_large_num_periods(self, app, bare_auth_client, bare_user):
        """num_periods exceeding max=260 returns 422 validation error."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-01-02",
                "num_periods": "999999",
                "cadence_days": "14",
            })
            # PayPeriodGenerateSchema has Range(min=1, max=260) on num_periods.
            assert resp.status_code == 422

            count = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_negative_cadence_days(self, app, bare_auth_client, bare_user):
        """Negative cadence_days returns 422 (Range min=1 on schema)."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-01-02",
                "num_periods": "10",
                "cadence_days": "-1",
            })
            assert resp.status_code == 422

            count = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_missing_all_fields(self, app, bare_auth_client, bare_user):
        """Empty form data returns 422 with required field errors."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={})
            assert resp.status_code == 422
            # start_date is the only truly required field
            # (num_periods and cadence_days have load_defaults).
            assert b"Start Date" in resp.data

            count = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_cadence_zero_db_state(self, app, bare_auth_client, bare_user):
        """Cadence zero returns 422 and creates no pay periods in the DB."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-03-01",
                "cadence_days": "0",
            })
            assert resp.status_code == 422
            assert b"Cadence Days" in resp.data

            count = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).count()
            assert count == 0


class TestTheCoverageRefusalReachesEveryDoor:
    """Plan step C3-b's new refusal has a handler on every route that can raise it.

    Added because an adversarial review of that step found four new
    ``except PayPeriodCoverageWithdrawn`` handlers and no route test for any of
    them -- so "the user sees a message" rested on reading the code.  Two of
    them are unreachable in practice (an append widens coverage), which is
    exactly why the two that ARE reachable need a test rather than an argument.

    The generate handler additionally renders a NEW error key, ``schedule``,
    which no template had been asked for before; the first assertion is that
    the page renders it rather than swallowing it.
    """

    def _settled_row_past(self, db_session, seed_user, period, day):
        """File a SETTLED row in *period* whose money moved on *day*."""
        return add_txn(
            db_session, seed_user, period, "Tuition", "2100.00",
            status_enum=StatusEnum.DONE, due_date=period.start_date,
            settled_on=day,
        )

    def test_truncate_flashes_it_and_deletes_nothing(
        self, app, db, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """The reachable door: removing the tail shortens the kept paycheck."""
        freeze_today(monkeypatch, date(2025, 12, 1))
        with app.app_context():
            user_id = seed_user["user"].id
            self._settled_row_past(
                db.session, seed_user, seed_periods[-1], date(2026, 6, 15),
            )
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 7, 1),
                num_periods=1, cadence_days=14,
            )
            db.session.commit()
            before = db.session.query(PayPeriod).filter_by(
                user_id=user_id,
            ).count()

            resp = auth_client.post("/pay-periods/truncate", data={
                "keep_through_period_id": str(seed_periods[-1].id),
                "confirm_discard": "true",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"2026-06-15" in resp.data
            assert db.session.query(PayPeriod).filter_by(
                user_id=user_id,
            ).count() == before

    def test_generate_renders_it_under_its_own_key(
        self, app, db, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """The 422 body carries the message, under the ``schedule`` key.

        **This door needs LEGACY data to reach the refusal at all**, and saying
        so is the point: after the forward-only floor became one full cadence,
        a batch that only ADDS paydays always widens the covered interval --
        the new horizon is at least a cadence past the old one -- so the rule
        short-circuits.  What can still get behind a settled row is a stored
        cadence SHORTER than the schedule it generated, which no door can now
        create (the cadence rule) and which pre-C3-b data carries (finding
        **P28**).  The schedule row is edited directly to build it.
        """
        freeze_today(monkeypatch, date(2025, 12, 1))
        with app.app_context():
            user_id = seed_user["user"].id
            # The row settles inside the 180-day paycheck's span, so it is
            # covered BEFORE and not after: a cadence of 2 pulls the horizon
            # back to 2026-07-04.
            self._settled_row_past(
                db.session, seed_user, seed_periods[-1], date(2026, 8, 15),
            )
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 7, 1),
                num_periods=1, cadence_days=180,
            )
            db.session.query(PaySchedule).filter_by(user_id=user_id).update(
                {"cadence_days": 2}, synchronize_session=False,
            )
            db.session.commit()

            resp = auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-07-03", "num_periods": "1",
                "cadence_days": "2",
            })

            assert resp.status_code == 422
            assert b"Schedule:" in resp.data
            assert b"2026-08-15" in resp.data
