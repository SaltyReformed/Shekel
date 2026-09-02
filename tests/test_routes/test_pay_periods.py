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
from app.models.transaction import Transaction
from app.services import pay_period_write
from tests._test_helpers import (
    add_txn,
    freeze_today,
    last_covered_day,
)


def _spans(session, user_id):
    """Return the owner's ``(start_date, end_date)`` spans, payday ascending."""
    return [
        (period.start_date, last_covered_day(period))
        for period in session.query(PayPeriod)
        .filter_by(user_id=user_id)
        .order_by(PayPeriod.start_date)
        .all()
    ]


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
        step C3-b -- it was the latest ``end_date``, a column plan step C4-c
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



class TestShorteningTheSchedulePastASettledDayGoesThrough:
    """The doors that used to refuse a coverage withdrawal now carry it out.

    **Plan step C3-b shipped a refusal on four routes and the developer deleted
    it 2026-08-11**, because the defect it named was not one: a settled row's
    cash day falling outside the reported window is absent from BOTH sides of
    ruling R-K's identity and reports as the ``period_timing`` remainder
    (``test_cash_period_view.py``:
    ``test_a_settle_day_past_the_window_keeps_every_column_exact``).  What the
    refusal cost was real -- 5 of production's 61 truncation points blocked,
    with re-dating a settled row as the only way past.

    Graded at the ROUTE and not only at the service, for the reason the class
    it replaces existed: "the user can now do this" rested on reading the
    handler list, and a stale ``except`` clause would have kept flashing a
    refusal no service raises.
    """

    def _settled_row_past(self, db_session, seed_user, period, day):
        """File a SETTLED row in *period* whose money moved on *day*."""
        return add_txn(
            db_session, seed_user, period, "Tuition", "2100.00",
            status_enum=StatusEnum.DONE, due_date=period.start_date,
            settled_on=day,
        )

    def test_truncate_removes_the_tail_and_keeps_the_stranded_row(
        self, app, db, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """The door the refusal actually blocked.

        A payday at 2026-07-01 stretches the last seeded paycheck to
        2026-06-30, and a settled row inside it cleared 2026-06-15.  Truncating
        the successor away drops that paycheck back to 2026-05-21, so the
        settle day ends up covered by nothing.  The tail goes, the row stays
        exactly as it was, and the page reports success rather than the day.
        """
        freeze_today(monkeypatch, date(2025, 12, 1))
        with app.app_context():
            user_id = seed_user["user"].id
            row = self._settled_row_past(
                db.session, seed_user, seed_periods[-1], date(2026, 6, 15),
            )
            row_id = row.id
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
            assert b"2026-06-15" not in resp.data
            assert db.session.query(PayPeriod).filter_by(
                user_id=user_id,
            ).count() == before - 1
            survivor = db.session.get(Transaction, row_id)
            assert survivor.settled_on == date(2026, 6, 15)
            assert survivor.pay_period_id == seed_periods[-1].id

    def test_generate_can_only_WIDEN_the_covered_interval(
        self, app, db, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """A batch through this door cannot pull the horizon back at all.

        **This case asserted the OPPOSITE until plan step
        ``pay_calendar:C4-c``**, and the inversion is that step in one
        property.  The horizon was the LAST ROW'S STORED ``end_date``, written
        at whatever cadence the batch that created it ran at; editing
        ``budget.pay_schedule.cadence_days`` afterwards -- finding **P28**'s
        legacy shape -- left the two disagreeing, and a later generate
        rewrote the stored end DOWN to the new cadence's projection.  A door
        could therefore take a settled row's cash day out of every paycheck.

        There is one value now.  The floor is ``latest payday + the stored
        cadence`` and the new horizon is ``new payday + the new cadence - 1``,
        so a batch that RECORDS a payday leaves the horizon at least
        ``old horizon + the new cadence`` -- strictly greater, for every
        cadence in 1..365.  A batch every one of whose requested paydays
        already exists records none, and ``_apply`` then skips
        ``upsert_schedule`` entirely, so the horizon is unchanged and the post
        is still accepted: **non-decreasing** is the property this door has,
        and strictly increasing is what it has when it writes.  *An adversarial
        review corrected that sentence, 2026-09-01.*  Either way the state the
        deleted case built is unreachable through this door, and this is the
        assertion that says so rather than the absence of a test.

        Driven at the SMALLEST cadence the schema admits, because that is where
        the margin is thinnest: one day.
        """
        freeze_today(monkeypatch, date(2025, 12, 1))
        with app.app_context():
            user_id = seed_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 7, 1),
                num_periods=1, cadence_days=180,
            )
            db.session.commit()
            before_horizon = max(end for _start, end in _spans(db.session, user_id))
            # 2026-07-01 + 180 - 1.
            assert before_horizon == date(2026, 12, 27)

            # The floor is the latest payday plus the STORED cadence, so this
            # is the earliest day the door accepts.
            resp = auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-12-28", "num_periods": "1",
                "cadence_days": "1",
            })

            assert resp.status_code == 302
            after_horizon = max(end for _start, end in _spans(db.session, user_id))
            assert after_horizon == date(2026, 12, 28)
            assert after_horizon > before_horizon
