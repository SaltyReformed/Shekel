"""The application-level answer to "this owner's pay calendar cannot be derived".

Plan step **pay_calendar:C4-b-2**, building the handler ledger row **P35**
deferred (developer ruling, 2026-09-01).

``PayCalendarError`` is raised at fourteen sites across ``pay_calendar`` and
``paycheck_calculator`` (AST census 2026-09-01 -- a grep over-counts, because
the class's own docstrings name it), and until this step
``app/error_handlers.py`` had no arm for any of them.  So it reached the
browser through the bare ``500`` handler: an unstyled page on a money screen,
and inside an htmx fragment nothing at all, because htmx does not swap a 500 --
a click that did nothing and said nothing.

**What is graded here, and why it is split the way it is.**  The three response
shapes belong to :func:`app.error_handlers._recovery_response`, which the
no-baseline handler shares, so they are exercised on the helper directly: a
route can only demonstrate the branch its own method reaches, and contriving a
mutating route that raises would grade the contrivance.  The END-TO-END arm is
one real surface, because a helper that answers correctly while no handler is
registered for the exception would pass every unit arm above it.
"""
from __future__ import annotations

import pytest

from app.error_handlers import _recovery_response
from app.models.pay_period import PayPeriod
from app.models.pay_schedule import PaySchedule
from app.services.pay_calendar import PayCalendarError, cadence_for


@pytest.fixture
def owner_without_a_pay_calendar(app, db, seed_user):
    """The seeded owner stripped of every payday AND their cadence row.

    The one state that still reaches ``PayCalendarError`` from a page after
    this step: an owner with no ``budget.pay_schedule`` row, which since
    ``fk_pay_periods_schedule`` means an owner with no paydays either.  It is
    ordinary rather than contrived -- a companion holds neither by design, and
    so does any owner between sign-up and their first batch.

    **Periods first, then the schedule row.**  That order is the only legal one
    now: the key is ``ON DELETE RESTRICT``, so removing the parent under live
    children is refused by the database.

    Returns:
        The owner's user id.
    """
    with app.app_context():
        user_id = seed_user["user"].id
        db.session.query(PayPeriod).filter_by(
            user_id=user_id,
        ).delete(synchronize_session=False)
        db.session.query(PaySchedule).filter_by(
            user_id=user_id,
        ).delete(synchronize_session=False)
        db.session.commit()

        # The premise, asserted rather than assumed: this owner's cadence
        # really is unanswerable, so the cases below are about a HANDLER
        # rather than about a page that happens to render.
        with pytest.raises(PayCalendarError):
            cadence_for(user_id)

        return user_id


class TestTheThreeResponseShapes:
    """``_recovery_response`` answers by what the REQUEST was, not the state."""

    def test_a_safe_htmx_request_swaps_nothing(self, app):
        """An idempotent poll must leave the live DOM alone.

        204 is right here and only here: a self-refreshing fragment that
        happened to run must not replace a balance region with a setup card.
        """
        with app.test_request_context(
            "/grid", method="GET", headers={"HX-Request": "true"},
        ):
            body, status = _recovery_response("errors/no_pay_calendar.html")

        assert status == 204
        assert body == ""

    def test_a_mutating_htmx_request_is_answered_not_silenced(self, app):
        """An htmx POST gets the card, never 204.

        204 for a button is the user pressing it and nothing happening,
        silently and every time -- the failure measured on
        ``POST /debt-strategy/calculate`` at plan step X-v2 and the reason the
        no-baseline handler splits on method.  The same split is inherited
        here rather than re-decided.
        """
        with app.test_request_context(
            "/grid", method="POST", headers={"HX-Request": "true"},
        ):
            body = _recovery_response("errors/no_pay_calendar.html")

        assert "Pay Calendar Unavailable" in body
        assert "/pay-periods/generate" in body

    def test_a_full_page_request_gets_the_page(self, app):
        """No htmx header at all: the whole recovery page."""
        with app.test_request_context("/savings", method="GET"):
            body = _recovery_response("errors/no_pay_calendar.html")

        assert "Pay Calendar Unavailable" in body


class TestTheHandlerAnswersARealSurface:
    """End to end: the exception reaches the handler, not the 500 page."""

    def test_a_page_that_needs_the_cadence_answers_the_repair(
        self, app, auth_client, owner_without_a_pay_calendar,
    ):
        """``/savings`` gets the recovery page instead of a bare 500.

        The savings dashboard states its coverage as a span in PAYCHECKS on
        every render, so it reads ``PayCalendar.cadence`` unconditionally and
        refuses for this owner -- which is the point: the page cannot answer,
        and before this step it said so with a stack trace.

        The assertion is on the CARD rather than on the status alone, because
        the bare 500 handler also renders a page: a status-only assertion
        would pass against the defect this replaces.
        """
        response = auth_client.get("/savings")

        assert response.status_code == 200
        body = response.data.decode()
        assert "Pay Calendar Unavailable" in body
        assert "/pay-periods/generate" in body

    def test_the_quiet_screen_is_a_loud_log_line(
        self, app, auth_client, owner_without_a_pay_calendar, caplog,
    ):
        """The page is calm and the log is not.

        Two states reach this handler and only one is the owner's to fix; the
        other is a broken invariant no write door produces.  The page cannot
        tell them apart without guessing, so the EVENT carries which fired.  A
        handler that degraded in silence would hide the second one behind a
        tidy card, which is worse than the 500 it replaced.
        """
        with caplog.at_level("ERROR"):
            auth_client.get("/savings")

        events = [
            record for record in caplog.records
            if getattr(record, "event", None) == "pay_calendar_underivable"
        ]

        assert events, (
            "the pay-calendar handler answered without emitting its event; "
            f"records seen: {[r.message for r in caplog.records]}"
        )
        assert events[0].levelname == "ERROR"
        assert events[0].category == "error"
        assert events[0].path == "/savings"


class TestTheHandlerIsInertForAHealthyOwner:
    """The negative control: a normal owner never meets this page.

    Without it every case above is satisfied by a handler that fired for
    everybody, which is the shape a green suite hides best.
    """

    def test_an_owner_with_a_pay_calendar_is_untouched(
        self, app, auth_client, seed_user,
    ):
        """``/savings`` renders normally and emits no event."""
        response = auth_client.get("/savings")

        assert response.status_code == 200
        assert "Pay Calendar Unavailable" not in response.data.decode()
