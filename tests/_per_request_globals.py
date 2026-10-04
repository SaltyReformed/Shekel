"""
Shekel Budget App -- A Test Client Whose Every Request Has a ``g`` of Its Own

Ledger row ``balance:BAL-521``, plan step ``balance:X-cr``.

**The defect.**  The ``db`` fixture runs every test inside ONE application
context, and Flask 3.1's ``RequestContext.push`` REUSES an already-pushed
application context of the same app rather than pushing a fresh one.
``flask.g`` lives on the application context, so every request a test issued
shared one ``g`` -- with the test's other requests and with the test body.
Flask-Login caches a request's user in ``g._login_user`` the first time
``current_user`` is read -- and ``login_user`` / ``logout_user`` overwrite it
-- and never re-reads the session cookie while that attribute exists.  So once
any request in a test had set it, every later request was answered as
whichever user it held, whichever client sent it, and ``load_user`` (with its
deactivation, invalidation and idle-timeout refusals) never ran again.
Measured 2026-10-04: with ``auth_client`` and ``second_auth_client`` in one
test, the second client was served the first user's pages, both inside and
without an explicit ``with app.app_context()``, and the second client's own
login POST was answered "already signed in" without ever signing it in.  The
signed-in user was only the visible half: Flask-WTF caches the rendered form
token on ``g``, and the request logger its id and start time.  (The
transaction boundary's mode, actor and write owner live there too, but its
own teardown pops them because of this shared ``g``.)  Production never had
any of it -- each request there gets an application context, and so a ``g``,
of its own.

**The fix.**  :class:`PerRequestGlobalsClient` gives every request it issues a
brand-new ``g`` -- ``app.app_ctx_globals_class()``, exactly the object a
fresh application context would build -- for the length of that request, and
puts the test's own ``g`` OBJECT back afterwards, untouched.  So a request
starts from what production's starts from (an empty ``g``), and nothing it
caches survives it.

**Why a fresh ``g`` and not a snapshot of the test's.**  Restoring a snapshot
after each request would also stop a request's additions leaking -- but the
request would START from the test body's ``g``, and the test body's ``g`` is
not empty: a sign-in performed under ``app.test_request_context()`` caches
that user on it, and every later request would be answered as that user.
``test_a_user_the_test_body_signs_in_is_not_a_request_s`` in
``tests/test_integration/test_each_request_has_its_own_user.py`` measures that
difference.

**Why not a whole application context per request**, which X-cr was first
written as and which would be the production shape exactly: Flask-SQLAlchemy
3.1 keys ``db.session`` on the application context, so each request would get
a session the test body does not share.  Measured 2026-10-04 on this tree,
that design failed 110 tests across the suite (rows a request deleted still in
the test body's identity map, stale amounts, the body's uncommitted setup
invisible to the request); the developer ruled the step to this design on the
same day.  So a request and its test still share ``db.session``, and the
reason ruling ``credit_card:R-CC123`` gave for ``app/db_transaction.py``'s
end-of-request rollback of a save -- a request's transaction outliving the
request in that shared session -- still holds.

**Where it hooks.**  ``run_wsgi_app`` is the one call Werkzeug's client makes
per request -- once per redirect hop under ``follow_redirects`` -- so each hop
is a request with a ``g`` of its own, as each is in a browser.  The window is
the whole request because ``Flask.wsgi_app`` runs dispatch, every hook, the
session save and ``teardown_request`` before it returns.  **What it cannot
cover is a body produced AFTER it returns**: a view streaming with
``stream_with_context`` re-pushes its request context while the body is read,
which would then run on the test's ``g``.  No view in ``app/`` streams (grep,
2026-10-04).
"""

from flask import has_app_context
from flask.globals import app_ctx
from flask.testing import FlaskClient


class PerRequestGlobalsClient(FlaskClient):
    """A Flask test client whose every request runs on a fresh ``g``.

    Installed for the whole session by ``tests/conftest.py``'s
    ``each_request_has_its_own_g`` fixture; see the module docstring for the
    defect and the design.

    One consequence for a test that inspects a request after the fact: under
    ``with client:`` (Flask's ``preserve_context``), ``flask.g`` after the
    request is the test's own again, not the request's -- the request's ``g``
    is gone, as it is in production.  No test in this suite reads it.
    """

    def run_wsgi_app(self, environ, buffered=False):
        """Run one request against the app on a ``g`` of its own.

        When an application context of this client's app is already pushed --
        the test's, which the request would otherwise reuse along with its
        ``g`` -- its ``g`` is swapped for a fresh one for the request and
        swapped back afterwards, on every exit path.  When none is pushed, the
        request pushes its own context and so builds its own ``g`` already;
        there is nothing to swap.

        Args:
            environ: The WSGI environment of the request.
            buffered: Passed through to Werkzeug: whether to buffer the
                response body.

        Returns:
            Werkzeug's ``(app_iter, status, headers)`` for the request.
        """
        if not has_app_context() or app_ctx.app is not self.application:
            return super().run_wsgi_app(environ, buffered=buffered)
        # The context OBJECT, taken once, so the ``finally`` restores the very
        # context it swapped rather than whichever one the proxy resolves to
        # by then.  Pylint: ``protected-access`` -- ``_get_current_object`` is
        # Werkzeug's documented way to unwrap a ``LocalProxy``.
        context = app_ctx._get_current_object()  # pylint: disable=protected-access
        tests_own_g = context.g
        context.g = self.application.app_ctx_globals_class()
        try:
            return super().run_wsgi_app(environ, buffered=buffered)
        finally:
            context.g = tests_own_g
