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

The served worker's cache name is versioned by content: the
``__ASSET_VERSION__`` placeholder in ``sw.js`` is replaced at serve
time with a short hash of every cached static asset (see
:func:`_static_asset_version`).  The hash changes whenever any of
those assets changes, which changes the worker bytes the browser
sees and makes the worker's ``activate`` handler evict the prior
cache, so returning users pick up changed CSS/JS without a manual
cache-name bump.
"""

import hashlib
from pathlib import Path

from flask import Blueprint, Response, current_app

from app.extensions import limiter


static_pass_bp = Blueprint("static_pass", __name__)

# Placeholder token embedded in app/static/sw.js, replaced at serve
# time with the content hash below.  Must match the literal in sw.js.
_VERSION_PLACEHOLDER = "__ASSET_VERSION__"

# Static subdirectories and root-level files whose contents the
# service worker caches.  These MUST mirror STATIC_PREFIXES in
# app/static/sw.js: the version hash covers exactly the files the
# worker will cache, so a change to any cached asset (and nothing
# else) changes the cache name.
_CACHED_STATIC_DIRS = ("vendor", "css", "js", "img", "fonts")
_CACHED_STATIC_FILES = ("manifest.json",)

# Hex-digest prefix length used as the version token.  48 bits is
# ample to keep an accidental collision between two distinct asset
# sets effectively impossible for a single application.
_VERSION_HEX_LEN = 12

# Memoized version per static folder.  The cached assets do not change
# within a running process (a new deploy is a new process), so the
# tree walk runs once per worker rather than on every /sw.js request.
_VERSION_CACHE: dict[str, str] = {}


def _static_asset_version(static_folder: str) -> str:
    """Return a short content hash over the service-worker-cached assets.

    Walks the subdirectories and files named in ``_CACHED_STATIC_DIRS``
    and ``_CACHED_STATIC_FILES`` (which mirror ``STATIC_PREFIXES`` in
    ``app/static/sw.js``) and folds each file's path (relative to
    ``static_folder``) and its bytes into a SHA-256 digest, processing
    files in sorted-path order.  Sorting makes the digest deterministic
    across processes and machines; hashing the bytes makes it change
    exactly when a cached asset changes.

    Args:
        static_folder: Absolute path to the Flask static folder.

    Returns:
        The first ``_VERSION_HEX_LEN`` hex characters of the digest,
        memoized per ``static_folder`` so repeated calls do not re-walk
        the tree.
    """
    cached = _VERSION_CACHE.get(static_folder)
    if cached is not None:
        return cached

    root = Path(static_folder)
    files: list[Path] = []
    for subdir in _CACHED_STATIC_DIRS:
        directory = root / subdir
        if directory.is_dir():
            files.extend(p for p in directory.rglob("*") if p.is_file())
    for name in _CACHED_STATIC_FILES:
        candidate = root / name
        if candidate.is_file():
            files.append(candidate)

    digest = hashlib.sha256()
    for path in sorted(files):
        # Relative path keeps the hash independent of the install
        # location; the bytes capture the actual content.
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())

    version = digest.hexdigest()[:_VERSION_HEX_LEN]
    _VERSION_CACHE[static_folder] = version
    return version


@static_pass_bp.route("/sw.js")
@limiter.exempt
def service_worker() -> Response:
    """Serve ``app/static/sw.js`` at the root scope ``/sw.js``.

    Reads the on-disk worker and substitutes the ``__ASSET_VERSION__``
    placeholder with the content hash from
    :func:`_static_asset_version`, so the worker's cache name tracks the
    deployed static assets.  Serving at ``/sw.js`` (rather than
    redirecting to ``/static/sw.js``) keeps the request path at the root
    so the browser scopes the registered worker to ``/``.

    The response is built directly rather than via
    ``send_from_directory`` because the body is the substituted text,
    not the raw file.  ``Content-Type: application/javascript`` is set
    explicitly: some browsers reject a worker served with the generic
    ``text/javascript`` MIME type.  The app-wide after-request hook adds
    ``Cache-Control: no-store`` for this non-``static`` endpoint, so the
    browser always re-checks for an updated worker.

    Returns:
        A Flask ``Response`` with the version-substituted worker source
        and an explicit JavaScript MIME type.
    """
    static_folder = current_app.static_folder
    version = _static_asset_version(static_folder)
    source = (Path(static_folder) / "sw.js").read_text(encoding="utf-8")
    body = source.replace(_VERSION_PLACEHOLDER, version)
    return Response(body, mimetype="application/javascript")
