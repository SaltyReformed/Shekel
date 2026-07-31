"""
Shekel Budget App -- application-level error handlers.

Every response the application produces for a condition no single route owns:
the five HTTP error pages, and the ONE answer to each of the two preconditions
without which no figure is computable -- no baseline scenario (plan step X-v,
ruling R-BW) and no pay period containing the date (plan step X-x, ruling
R-CY).  The two handlers answer with the same three branches on purpose; where
their reasons differ, the difference is stated at the second one.

**Its own module because the factory has a line ceiling and this is a
concern, not plumbing.**  ``app/__init__.py`` already extracted its Jinja
filters to :mod:`app.jinja_filters` for exactly that reason; the
``BaselineMissingError`` handler is what pushed the factory over 1,000 lines,
and a registration helper that renders six responses is not the app factory's
job.  ``create_app`` calls :func:`register_error_handlers` and owns nothing
about what any of them says.
"""

import logging

from flask import render_template, request
from flask_login import current_user

from app.exceptions import BaselineMissingError, PayCalendarGapError
from app.extensions import db
from app.utils.log_events import (
    ACCESS,
    ERROR,
    EVT_BASELINE_MISSING,
    EVT_PAY_CALENDAR_GAP,
    EVT_RATE_LIMIT_EXCEEDED,
    log_event,
)


#: This module's logger.  Shared by every handler below rather than one
#: constant per handler.
_LOGGER = logging.getLogger(__name__)

#: HTTP methods that change nothing, so a fragment answering them may safely
#: swap nothing.  A MUTATING request that swaps nothing is a button that did
#: not work and said so to no one -- see :func:`register_error_handlers`'s
#: no-baseline handler.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def register_error_handlers(app):
    """Register every application-level error response on *app*.

    Args:
        app: The Flask application being built by
            :func:`app.create_app`.
    """

    @app.errorhandler(400)
    def bad_request(_e):
        """Handle 400 Bad Request errors.

        Common triggers: CSRF token validation failure (Flask-WTF
        rejects the request), malformed form data, or invalid
        request syntax.
        """
        return render_template("errors/400.html"), 400

    @app.errorhandler(403)
    def forbidden(_e):
        """Handle 403 Forbidden errors.

        Common triggers: permission denied, accessing a resource
        that exists but the user is not authorized to view.
        """
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def page_not_found(_e):
        """Handle 404 Not Found errors.

        Triggers when the requested URL does not match any route.
        """
        return render_template("errors/404.html"), 404

    @app.errorhandler(429)
    def rate_limit_exceeded(_e):
        """Return the 429 error page with a Retry-After header.

        Also emits a structured ``rate_limit_exceeded`` log event
        (audit Commit C-15 / finding F-146) so an operator can alert
        on sustained rate-limit pressure from the observability
        stack.  Without this event, a slow credential-stuffing
        campaign that stays under each individual route's per-window
        ceiling would still trigger the global default ceiling
        (``200 per hour;30 per minute``) repeatedly with no signal
        for incident response -- the rate limit successfully blocks
        the attack from succeeding, but no human ever sees the
        spike.

        The event runs under WARNING level (not ERROR -- a single
        rate-limit hit is not in itself an outage), under the
        ACCESS category so it groups with the other access-control
        events the SOC dashboard already filters on.  ``path`` and
        ``remote_addr`` go into the structured payload so a Loki
        query can pivot on either; the IP comes from
        ``request.remote_addr`` which already reflects the
        ``ProxyFix``-resolved client address (see ``gunicorn.conf.py``
        ``forwarded_allow_ips``).
        """
        log_event(
            _LOGGER,
            logging.WARNING,
            EVT_RATE_LIMIT_EXCEEDED,
            ACCESS,
            "Rate limit exceeded",
            path=request.path,
            method=request.method,
            remote_addr=request.remote_addr,
        )
        response = app.make_response(
            (render_template("errors/429.html"), 429)
        )
        # 900 seconds = 15 minutes, matching the rate limit window.
        response.headers["Retry-After"] = "900"
        return response

    @app.errorhandler(500)
    def internal_server_error(_e):
        """Handle 500 Internal Server Error.

        Triggers on unhandled exceptions in route handlers or
        service layer code.  The rollback clears any failed transaction
        so context-processor queries (e.g. inject_onboarding) can run
        and the custom error template renders instead of a blank page.
        """
        db.session.rollback()
        return render_template("errors/500.html"), 500

    @app.errorhandler(BaselineMissingError)
    def baseline_missing(error):
        """Answer "this user has no baseline scenario" -- once, for the whole app.

        THE no-baseline policy (plan step X-v, ruling R-BW).  Every balance the
        app produces is scoped to a baseline scenario, so a user without one has
        no balance any surface can answer.  Before this, each surface that could
        reach that state decided for itself, and between them they decided
        several different things -- a full-page recovery card, a ``204``, a 404,
        a blank cockpit reporting a fabricated ``$0.00`` net worth, an anchor
        cache column presented as a current balance, a balance sheet asserting
        ``in_balance`` over a ledger it could not read, a hidden chip, and three
        surfaces that raised into a 500.  Plan step X-v's own entry carries the
        census; this is the one answer that replaced them:

        * **A SAFE-method HTMX request gets 204 No Content**, which is the
          contract the grid's self-refresh partials already shipped: an
          idempotent poll must leave the live DOM alone, never swap a setup card
          into a balance cell.
        * **A MUTATING HTMX request gets the card**, because 204 there means the
          user pressed a button and nothing happened, silently and forever --
          measured on ``POST /debt-strategy/calculate``, which answered ``204``
          with an empty body until X-v2's adversarial review caught it.  The
          card swapping into the results target says why the action did nothing
          and offers the repair.
        * **Everything else gets the recovery page.**

        **Status ``200``, and the reason is htmx, not precedent.**  htmx swaps
        only 2xx responses, so any honest-looking 4xx/5xx would make the two
        HTMX branches above render NOTHING -- the silence they exist to fix.
        The response is also a complete, actionable page rather than a failure
        the browser should surface as one.  What carries the failure signal is
        the ERROR event below, which is the channel an operator alerts on; a
        status code that no client acts on is not.

        **It is quiet on screen and LOUD in the log.**  No code path produces a
        baseline-less owner -- registration writes one, nothing deletes or
        un-baselines one, no path promotes a companion (measured 2026-07-28,
        and asserted by ``scripts/integrity_check`` DC-08) -- so an occurrence
        is either data changed outside the application or a caller resolving a
        context for the WRONG user.  The event carries BOTH ids for exactly that
        second case: ``user_id`` is who asked, ``context_user_id`` is who the
        raise was resolved for, and they differ only when a caller has the wrong
        one.  Logging only the requester would have made the event blind in the
        one failure it exists to diagnose (X-v2's adversarial design review).

        The rollback matches the 500 handler's: an exception may have left the
        session in a failed transaction, and the recovery page's own context
        processors query.

        Args:
            error: The raised
                :class:`~app.exceptions.BaselineMissingError`; its message
                names the repair and it carries the user id it was resolved
                for.  Logged, never shown.

        Returns:
            ``("", 204)`` for a safe-method HTMX request, else the rendered
            recovery page.
        """
        db.session.rollback()
        log_event(
            _LOGGER, logging.ERROR, EVT_BASELINE_MISSING, ERROR,
            "Balance requested for a user with no baseline scenario",
            # ``auth_helpers._safe_user_id`` is this same read with the same
            # rationale, and is deliberately NOT imported: that module does
            # ``from app import ref_cache``, so importing it here would close a
            # cycle back into this package's own initialisation.
            user_id=getattr(current_user, "id", None),
            context_user_id=error.user_id,
            path=request.path,
            method=request.method,
            detail=str(error),
        )
        if request.headers.get("HX-Request") and request.method in _SAFE_METHODS:
            return "", 204
        return render_template("errors/no_baseline.html")

    @app.errorhandler(PayCalendarGapError)
    def pay_calendar_gap(error):
        """Answer "no pay period contains this date" -- once, for the whole app.

        THE pay-calendar policy (plan step X-x, ruling R-CY), and it is
        deliberately the same three branches as ``baseline_missing`` above --
        safe-method HTMX gets ``204``, a mutating request and a page get the
        card, status ``200`` because htmx swaps only 2xx.  Two preconditions
        without which no figure is computable should not answer differently just
        because one was implemented two steps after the other; where the reasons
        are identical they are stated once, above.

        **What differs is that this state is REACHABLE, and the event says so.**
        ``baseline_missing`` is quiet on screen and loud in the log because no
        code path produces it.  This one is produced by three ordinary
        situations: a schedule that has lapsed, one that opens in the future, and
        a hole between two periods.  So the ERROR event is a workload signal
        rather than a corruption alarm -- a steady trickle means users are
        falling out of their pay calendar, and it carries ``as_of`` because a
        request pinned to a historical date can raise this while today is
        perfectly well covered, and an event logging only the user could not tell
        those apart.

        **The card it replaced was reachable from ONE route** while about a dozen
        other surfaces answered the same state by substituting a figure: the
        savings cockpit and the investment tile presented
        ``Account.current_anchor_balance`` -- a derived cache -- as a current
        balance, four producers returned a fabricated ``$0.00``, and the
        net-worth trend returned an empty series carrying an index into it.
        Measured on a prod-shape clone, that moved a rendered net worth by
        ``$3,228.55`` while ``/grid`` showed the repair card at the same instant.

        Args:
            error: The raised
                :class:`~app.exceptions.PayCalendarGapError`; its message names
                the repair and it carries the user id it was resolved for and
                the date that could not be placed.  The DATE is rendered (it is
                what tells the user which of the three situations they are in);
                the message is logged, never shown.

        Returns:
            ``("", 204)`` for a safe-method HTMX request, else the rendered
            recovery page.
        """
        db.session.rollback()
        log_event(
            _LOGGER, logging.ERROR, EVT_PAY_CALENDAR_GAP, ERROR,
            "Figure requested for a date no pay period contains",
            # The same read, and the same non-import, as ``baseline_missing``
            # above: ``auth_helpers`` closes an import cycle back into this
            # package's initialisation.
            user_id=getattr(current_user, "id", None),
            context_user_id=error.user_id,
            as_of=error.as_of.isoformat() if error.as_of else None,
            path=request.path,
            method=request.method,
            detail=str(error),
        )
        if request.headers.get("HX-Request") and request.method in _SAFE_METHODS:
            return "", 204
        return render_template("errors/no_pay_calendar.html", as_of=error.as_of)
