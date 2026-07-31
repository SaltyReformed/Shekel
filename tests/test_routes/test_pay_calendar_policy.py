"""The pay-calendar policy: one answer to "no period contains this date".

Plan step X-x1, ruling R-CY.  Almost everything the app shows is anchored on the
period containing today, so a user whose calendar does not cover today has no
current figure the app can answer.  One accessor raises one named exception
(:class:`app.exceptions.PayCalendarGapError`) and one application-level handler
answers it: the setup-recovery page for a full request, ``204 No Content`` for a
safe-method HTMX request, an ERROR log event either way.

**This state is REACHABLE, unlike the no-baseline one its shape is copied from,
and the fixtures below are the three ways in.**  A schedule that has lapsed, one
that opens in the future, and a HOLE between two periods.  The hole is the one
that matters most: it is permanent, because ``top_up_rolling_window`` counts
periods ending on or after today, sees a full window, and never fires.

**What this file does NOT prove**, stated because a gate that reads as proving
more than it does is this arc's most expensive recurring lesson:

* it grades the GRID's doors, which are the only callers X-x1 moves.  The
  dozen surfaces that answer this state by SUBSTITUTING a figure are plan step
  X-x2's, and they pass every arm here today because they return 200 with a
  number -- exactly how plan step X-v1's first sweep graded a fabricated
  ``$0.00`` hero green;
* it proves nothing about a date other than today.  ``require_current_period``
  takes an ``as_of`` and the exception carries it, but no ``app/`` caller passes
  one yet, so the dated arms below exercise the accessor directly.
"""

from datetime import date, timedelta

import pytest

from app.exceptions import PayCalendarGapError
from app.models.pay_period import PayPeriod
from app.services import pay_period_service


@pytest.fixture()
def lapsed_calendar(app, db, seed_user, seed_periods_today):
    """Shift every period into the past, so nothing covers today.

    The "my schedule ran out" state.  Shifting rather than deleting is
    deliberate: the periods, their transactions and the account anchor all stay
    intact and consistent, so what the test exercises is the calendar's coverage
    of TODAY and not an empty database.
    """
    with app.app_context():
        span = (
            seed_periods_today[-1].end_date
            - seed_periods_today[0].start_date
        ).days
        shift = timedelta(days=span + 30)
        for period in db.session.query(PayPeriod).filter_by(
            user_id=seed_user["user"].id,
        ):
            period.start_date -= shift
            period.end_date -= shift
        db.session.commit()
        assert pay_period_service.get_current_period(
            seed_user["user"].id,
        ) is None, "fixture precondition: today must be uncovered"
        return seed_user["user"].id


@pytest.fixture()
def holed_calendar(app, db, seed_user, seed_periods_today):
    """Open a hole in an otherwise complete schedule, containing today.

    The PERMANENT state, and the one the writer permits today:
    ``_reject_overlapping_batch`` requires a new batch to start AFTER the latest
    existing end date, not adjacent to it.  Every period from the current one
    forward is pushed out far enough that today lands between the last past
    period and the first future one, so the user keeps a full forward window --
    which is why the rolling top-up never fires and the hole never closes.
    """
    with app.app_context():
        uid = seed_user["user"].id
        current = pay_period_service.get_current_period(uid)
        assert current is not None, "fixture precondition: today starts covered"
        # Push the current period and everything after it far enough that today
        # falls after the previous period's end and before the new start.
        shift = timedelta(days=(date.today() - current.start_date).days + 5)
        for period in db.session.query(PayPeriod).filter(
            PayPeriod.user_id == uid,
            PayPeriod.period_index >= current.period_index,
        ):
            period.start_date += shift
            period.end_date += shift
        db.session.commit()
        assert pay_period_service.get_current_period(uid) is None, (
            "fixture precondition: today must fall in the hole"
        )
        assert db.session.query(PayPeriod).filter(
            PayPeriod.user_id == uid, PayPeriod.end_date >= date.today(),
        ).count() > 0, "fixture precondition: future periods must survive"
        return uid


class TestTheAccessor:
    """``require_current_period`` / ``covers`` / ``get_current_period``."""

    def test_it_returns_the_period_that_contains_today(
        self, app, seed_user, seed_periods_today,
    ):
        """With a covered calendar it returns that exact period."""
        with app.app_context():
            got = pay_period_service.require_current_period(
                seed_user["user"].id,
            )
            expected = next(
                p for p in seed_periods_today
                if p.start_date <= date.today() <= p.end_date
            )
            assert got.id == expected.id

    def test_it_raises_when_the_calendar_has_a_hole_at_today(
        self, app, holed_calendar,
    ):
        """The hole state raises rather than answering."""
        with app.app_context():
            with pytest.raises(PayCalendarGapError) as exc:
                pay_period_service.require_current_period(holed_calendar)
            assert exc.value.user_id == holed_calendar
            assert exc.value.as_of == date.today()

    def test_it_raises_when_the_schedule_has_lapsed(
        self, app, lapsed_calendar,
    ):
        """The lapsed state raises rather than answering."""
        with app.app_context():
            with pytest.raises(PayCalendarGapError):
                pay_period_service.require_current_period(lapsed_calendar)

    def test_the_exception_carries_the_date_that_could_not_be_placed(
        self, app, seed_user, seed_periods_today,
    ):
        """A pinned ``as_of`` is what the event reports, not today.

        The whole reason :attr:`PayCalendarGapError.as_of` exists: a request
        pinned to a historical date can raise while today is perfectly well
        covered, and an event logging only the user could not tell those apart.
        """
        with app.app_context():
            long_ago = seed_periods_today[0].start_date - timedelta(days=365)
            with pytest.raises(PayCalendarGapError) as exc:
                pay_period_service.require_current_period(
                    seed_user["user"].id, as_of=long_ago,
                )
            assert exc.value.as_of == long_ago
            # ... and today is still answerable for the same user.
            assert pay_period_service.require_current_period(
                seed_user["user"].id,
            ) is not None

    def test_covers_answers_the_same_question_both_ways(
        self, app, seed_user, seed_periods_today,
    ):
        """``covers`` is true exactly when the accessor does not raise."""
        with app.app_context():
            uid = seed_user["user"].id
            assert pay_period_service.covers(uid) is True
            assert pay_period_service.covers(
                uid, as_of=seed_periods_today[0].start_date - timedelta(days=1),
            ) is False

    def test_the_nullable_form_still_answers_none(self, app, holed_calendar):
        """``get_current_period`` keeps its contract for deciding callers."""
        with app.app_context():
            assert pay_period_service.get_current_period(
                holed_calendar,
            ) is None


class TestTheHandler:
    """The three branches, exercised through the grid's real doors."""

    def test_a_page_gets_the_repair_card_naming_the_date(
        self, app, auth_client, holed_calendar,
    ):
        """A full page request renders the card, with the uncovered date."""
        resp = auth_client.get("/grid")
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "No Pay Period" in body
        assert date.today().strftime("%B %-d, %Y") in body
        assert "/pay-periods/generate" in body

    def test_a_safe_htmx_fragment_gets_204_and_swaps_nothing(
        self, app, auth_client, holed_calendar,
    ):
        """An idempotent refresh leaves the live DOM alone.

        Both fragment endpoints, because each resolves the window itself and a
        contract proved on one proves nothing about the other.
        """
        for url in ("/grid/balance-row", "/grid/subtotal-rows"):
            resp = auth_client.get(url, headers={"HX-Request": "true"})
            assert resp.status_code == 204, url
            assert resp.get_data(as_text=True) == "", url

    def test_it_logs_an_error_event_carrying_both_ids_and_the_date(
        self, app, auth_client, holed_calendar, caplog,
    ):
        """The ERROR event is the channel an operator reads.

        ``as_of`` is asserted because it is what distinguishes this event's
        three causes, and ``context_user_id`` because a caller resolving the
        wrong user is the failure the event exists to diagnose.
        """
        with caplog.at_level("ERROR"):
            auth_client.get("/grid")
        events = [
            rec for rec in caplog.records
            if getattr(rec, "event", None) == "pay_calendar_gap"
        ]
        assert len(events) == 1, [r.message for r in caplog.records]
        assert events[0].context_user_id == holed_calendar
        assert events[0].as_of == date.today().isoformat()
        assert events[0].path == "/grid"

    def test_a_covered_calendar_is_untouched(
        self, app, auth_client, seed_user, seed_periods_today, caplog,
    ):
        """The negative control: nothing about this fires on a normal user.

        Without it every arm above would pass on a handler that answered the
        card unconditionally.
        """
        with caplog.at_level("ERROR"):
            page = auth_client.get("/grid")
            fragment = auth_client.get(
                "/grid/balance-row", headers={"HX-Request": "true"},
            )
        assert page.status_code == 200
        assert "No Pay Period" not in page.get_data(as_text=True)
        assert fragment.status_code == 200
        assert not [
            rec for rec in caplog.records
            if getattr(rec, "event", None) == "pay_calendar_gap"
        ]
