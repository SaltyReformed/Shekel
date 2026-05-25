"""
Shekel Budget App -- Root-Scope Static Passthrough

Serves a small number of static files at the application root
rather than under ``/static/``.  Currently the only entry is
``/sw.js`` -- the browser scopes a service worker to the directory
its file is served from, so a worker at ``/static/sw.js`` would
only see ``/static/...`` requests and could not intercept app-
route fetches.  Hosting the worker at ``/sw.js`` widens the scope
to ``/`` so the static-asset cache also covers requests issued
from any page in the app.

The route is exempt from Flask-Limiter for the same reason
``/health`` is: the browser may request ``/sw.js`` on every page
load to check for an updated worker (per the SW HTTP cache rules),
and rate-limiting that path would surface as silent SW staleness.
The file the route serves contains no user data, so the rate-limit
budget gains nothing from defending it.
"""

from flask import Blueprint, current_app, send_from_directory

from app.extensions import limiter


static_pass_bp = Blueprint("static_pass", __name__)


@static_pass_bp.route("/sw.js")
@limiter.exempt
def service_worker():
    """Serve ``app/static/sw.js`` at the root scope ``/sw.js``.

    Returning the file via ``send_from_directory`` (rather than
    rendering a template or redirecting to ``/static/sw.js``) keeps
    the request path at exactly ``/sw.js`` so the browser scopes
    the registered worker to ``/``.  A redirect would leave the
    final-URL path under ``/static/`` and limit the worker's fetch
    interception to that prefix only.

    Returns:
        A Flask ``Response`` wrapping the on-disk ``sw.js`` file,
        with ``Content-Type: application/javascript`` (some browsers
        reject service workers served with the generic
        ``text/javascript`` MIME type, so we set it explicitly
        rather than relying on the default extension mapping).
    """
    return send_from_directory(
        current_app.static_folder,
        "sw.js",
        mimetype="application/javascript",
    )
