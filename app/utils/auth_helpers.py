"""
Shekel Budget App - Authorization Helpers

Reusable functions for verifying resource ownership. Used by route
handlers to ensure the current user can only access their own data.

Pattern A (direct user_id): Use get_or_404() for models with a
user_id column (Account, TransactionTemplate, SavingsGoal, etc.).

Pattern B (indirect via parent): Use get_owned_via_parent() for
models scoped through a FK parent (Transaction via PayPeriod,
SalaryRaise via SalaryProfile, etc.).

Pattern C (role-based): Use @require_owner on routes restricted
to the owner role. Companions receive 404 to avoid revealing
route existence.

Pattern D (re-auth recency): Use @fresh_login_required() on routes
that perform high-value operations (anchor balance changes,
companion management, tax-config edits, account hard-delete).
The decorator redirects to /reauth when the user's last password
verification is older than ``FRESH_LOGIN_MAX_AGE_MINUTES``.  See
audit finding F-045 / commit C-10.

Every ownership-failure branch in this module emits a structured
``log_event`` so probing patterns surface in dashboards and SOC
alerting (audit finding F-144 / commit C-14).  Successful loads stay
silent: the per-request summary in ``logging_config`` already covers
the audit-trail "who hit which path" question, and emitting a record
on every successful ``get_or_404`` would drown the failure signal in
volume.
"""

import logging
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (
    abort, current_app, make_response, redirect, request,
    session as flask_session, url_for,
)
from flask_login import current_user

from app import ref_cache
from app.enums import RoleEnum
from app.extensions import db
from app.utils.log_events import (
    ACCESS,
    EVT_ACCESS_DENIED_CROSS_USER,
    EVT_ACCESS_DENIED_OWNER_ONLY,
    EVT_RESOURCE_NOT_FOUND,
    log_event,
)
from app.utils.session_helpers import FRESH_LOGIN_AT_KEY

logger = logging.getLogger(__name__)


def _safe_user_id():
    """Return ``current_user.id`` if authenticated, else ``None``.

    The ownership helpers run after ``@login_required`` in the normal
    flow so ``current_user`` is always authenticated, but the access
    decorators are also reachable in adversarial paths (a misordered
    decorator stack, a future code path that drops ``@login_required``
    by mistake, or test scaffolding that hits the helper directly).
    Returning ``None`` rather than raising on the
    ``AttributeError`` from an anonymous user keeps the audit log
    informative on every branch -- a missing ``user_id`` on a
    ``resource_not_found`` event signals "anonymous probe" rather than
    "the helper crashed".
    """
    return getattr(current_user, "id", None)


def require_owner(f):
    """Restrict a route to owner-role users only.

    Must be applied AFTER ``@login_required`` so that
    ``current_user`` is guaranteed to be authenticated.
    Companions receive 404 (not 403) per the project security
    response rule: "404 for both 'not found' and 'not yours.'"

    The ``getattr`` fallback to ``owner_id`` ensures safe behavior
    when ``role_id`` is absent (e.g. test fixtures that do not
    explicitly set it) -- the user is treated as an owner.

    On the deny branch, emits ``access_denied_owner_only`` (ACCESS
    category, WARNING level) with the offending user's id, role id,
    and the request path.  Without this event a probing companion
    would leave no application-tier trace; the event is what F-144
    closed.

    Decorator order::

        @bp.route("/example")
        @login_required      # runs first -- ensures authenticated
        @require_owner       # runs second -- checks role
        def example():
            ...
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        owner_id = ref_cache.role_id(RoleEnum.OWNER)
        actual_role_id = getattr(current_user, "role_id", owner_id)
        if actual_role_id != owner_id:
            log_event(
                logger, logging.WARNING,
                EVT_ACCESS_DENIED_OWNER_ONLY, ACCESS,
                "Non-owner blocked from owner-only route",
                user_id=_safe_user_id(),
                role_id=actual_role_id,
                path=request.path,
                method=request.method,
            )
            abort(404)
        return f(*args, **kwargs)
    return decorated


def get_or_404(model, pk, user_id_field="user_id"):
    """Load a record by primary key and verify it belongs to the current user.

    Uses the model's direct ``user_id`` column (or a custom column
    name via *user_id_field*) to check ownership.

    Both denial branches emit a structured log event so cross-user
    probing leaves a forensic trail (audit finding F-144 / commit
    C-14):

      * Missing PK -> ``resource_not_found`` at INFO.  Common in
        normal use (a deleted resource the user navigates to) so the
        level is INFO, not WARNING.
      * Cross-user PK -> ``access_denied_cross_user`` at WARNING.
        This is the IDOR signal SOC tooling alerts on; the event
        records the requesting user's id and the owning user's id so
        an analyst can correlate probes against a target account.

    Args:
        model: The SQLAlchemy model class to query.
        pk: The primary key value to look up.
        user_id_field: The column name on the model that holds the
            owning user's ID.  Defaults to ``"user_id"``.

    Returns:
        The model instance if found and owned by ``current_user``,
        or ``None`` otherwise.

    Note:
        The caller is responsible for handling a ``None`` return --
        typically by returning a 404 response or redirecting.
    """
    record = db.session.get(model, pk)
    if record is None:
        log_event(
            logger, logging.INFO,
            EVT_RESOURCE_NOT_FOUND, ACCESS,
            "Ownership check on a non-existent primary key",
            user_id=_safe_user_id(),
            model=model.__name__,
            pk=pk,
            path=request.path,
        )
        return None
    owner_id = getattr(record, user_id_field, None)
    if owner_id != _safe_user_id():
        log_event(
            logger, logging.WARNING,
            EVT_ACCESS_DENIED_CROSS_USER, ACCESS,
            "Cross-user resource access blocked",
            user_id=_safe_user_id(),
            model=model.__name__,
            pk=pk,
            owner_id=owner_id,
            path=request.path,
        )
        return None
    return record


def get_owned_via_parent(model, pk, parent_attr,
                         parent_user_id_attr="user_id"):
    """Load a record by PK and verify ownership through its parent.

    For models that lack a direct ``user_id`` column but are scoped
    through a FK parent (e.g. Transaction -> PayPeriod, SalaryRaise
    -> SalaryProfile), this function lazy-loads the parent and checks
    the parent's user_id.

    Mirrors :func:`get_or_404`'s logging contract: the missing-PK
    branch emits ``resource_not_found`` at INFO, the cross-user
    branch emits ``access_denied_cross_user`` at WARNING.  A missing
    parent attribute or missing parent ``user_id`` (e.g. orphaned
    relationship) also emits ``access_denied_cross_user`` because the
    failure mode is observationally identical to "this row belongs to
    someone else" from the requester's perspective.

    Args:
        model: The SQLAlchemy model class to query.
        pk: The primary key value to look up.
        parent_attr: The SQLAlchemy relationship attribute name on the
            model that points to the parent (e.g. ``"pay_period"``).
        parent_user_id_attr: The column name on the *parent* model
            that holds the owning user's ID.  Defaults to
            ``"user_id"``.

    Returns:
        The model instance if found and owned (via parent) by
        ``current_user``, or ``None`` otherwise.

    Examples::

        # Transaction -> PayPeriod.user_id
        get_owned_via_parent(Transaction, txn_id, "pay_period")

        # SalaryRaise -> SalaryProfile.user_id
        get_owned_via_parent(SalaryRaise, raise_id, "salary_profile")
    """
    record = db.session.get(model, pk)
    if record is None:
        log_event(
            logger, logging.INFO,
            EVT_RESOURCE_NOT_FOUND, ACCESS,
            "Ownership check on a non-existent primary key",
            user_id=_safe_user_id(),
            model=model.__name__,
            pk=pk,
            path=request.path,
        )
        return None
    parent = getattr(record, parent_attr, None)
    if parent is None:
        # Orphaned relationship or wrong parent_attr -- observationally
        # identical to "not yours" from the requester's perspective.
        # Emit the cross-user event so SOC tooling sees the probe;
        # owner_id is None to signal the orphaned-relationship case.
        log_event(
            logger, logging.WARNING,
            EVT_ACCESS_DENIED_CROSS_USER, ACCESS,
            "Cross-user resource access blocked (parent unloadable)",
            user_id=_safe_user_id(),
            model=model.__name__,
            pk=pk,
            parent_attr=parent_attr,
            owner_id=None,
            path=request.path,
        )
        return None
    parent_owner_id = getattr(parent, parent_user_id_attr, None)
    if parent_owner_id != _safe_user_id():
        log_event(
            logger, logging.WARNING,
            EVT_ACCESS_DENIED_CROSS_USER, ACCESS,
            "Cross-user resource access blocked",
            user_id=_safe_user_id(),
            model=model.__name__,
            pk=pk,
            parent_attr=parent_attr,
            owner_id=parent_owner_id,
            path=request.path,
        )
        return None
    return record


def _fresh_login_is_within(threshold: timedelta) -> bool:
    """Return True iff ``_fresh_login_at`` is within ``threshold``.

    Fail-closed for unparseable input, matching the pattern used by
    ``_idle_session_is_fresh`` (commit C-10):

      * Missing key -- the user has never re-authenticated on this
        session, or the cookie was constructed without the field.
        Reject so the only path to ``_fresh_login_at`` is via
        :func:`~app.utils.session_helpers.stamp_login_session` /
        :func:`~app.utils.session_helpers.stamp_reauth_session`.

      * Malformed (non-ISO-8601) value -- a tampered cookie that
        ``fromisoformat`` would otherwise raise on.

      * Naive timestamp -- would raise ``TypeError`` on the
        timezone-aware subtraction.

      * Age exceeds ``threshold`` -- the legitimate "step-up window
        expired" case the decorator exists to enforce.

    Future-dated timestamps (``elapsed < 0``) are treated as FRESH
    rather than rejected, mirroring ``_idle_session_is_fresh``: a
    backwards clock jump or NTP correction must not silently demote
    every active session into "needs reauth".  An attacker who
    forged a future-dated value would already need ``SECRET_KEY``,
    at which point the future-date check adds no defensive value.

    Args:
        threshold: Maximum allowed age, as a :class:`~datetime.timedelta`.

    Returns:
        True iff the session may proceed past ``fresh_login_required``;
        False if the user must be redirected to ``/reauth``.
    """
    raw = flask_session.get(FRESH_LOGIN_AT_KEY)
    if raw is None:
        return False
    try:
        fresh_at = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return False
    if fresh_at.tzinfo is None:
        return False
    elapsed = datetime.now(timezone.utc) - fresh_at
    if elapsed < timedelta(0):
        # Future-dated fresh-login is from a backwards clock jump
        # (NTP correction, manual adjustment, VM resume).  Treat as
        # fresh -- see docstring for the threat-model rationale.
        return True
    return elapsed <= threshold


def fresh_login_required(max_age_minutes: int | None = None):
    """Require a recent re-authentication for high-value operations.

    Wraps a route handler and redirects to ``/reauth`` (with
    ``?next=`` set to the original URL) when the user's last
    password verification is older than ``max_age_minutes``.  HTMX
    requests receive a ``204`` (no body) with an ``HX-Redirect``
    header instead of a 302, because htmx swaps response bodies
    rather than following redirects -- a plain redirect would render
    the /reauth HTML inside whatever fragment slot the original
    request targeted.

    Decorator order::

        @bp.route("/example", methods=["POST"])
        @login_required           # outermost: must be authenticated
        @require_owner            # then: must be the owner role
        @fresh_login_required()   # then: must be recently re-authed
        def example():
            ...

    The decorator MUST be called with parentheses
    (``@fresh_login_required()``), not as a bare reference, so the
    optional ``max_age_minutes`` keyword can be supplied.  Bare
    ``@fresh_login_required`` would pass the wrapped function into
    ``max_age_minutes`` and silently break.

    Args:
        max_age_minutes: Override for the default
            ``FRESH_LOGIN_MAX_AGE_MINUTES`` from app config.  Use
            ``None`` (the default) to inherit the config value at
            request time -- this is the right choice for almost every
            call site so that a single env-var bump propagates to
            every decorated route.  Pass an explicit integer only
            for routes that need a different window than the global
            default (e.g. a particularly destructive operation that
            should always require re-auth in the past minute).

    Returns:
        A decorator that, when applied to a Flask view function,
        returns the view's response if the freshness check passes
        and a /reauth redirect otherwise.

    Audit references: F-045 (commit C-10).
    """
    # Catch the @fresh_login_required (no-parens) misuse at import
    # time rather than letting it silently bind the view function as
    # ``max_age_minutes``.  Without this guard, a misused decorator
    # would pass a function into ``timedelta(minutes=...)`` on the
    # first request, surfacing as a confusing TypeError far from the
    # actual mistake.
    if callable(max_age_minutes):
        raise TypeError(
            "fresh_login_required must be called with parentheses: "
            "use @fresh_login_required() (or "
            "@fresh_login_required(max_age_minutes=N)), not bare "
            "@fresh_login_required."
        )

    def decorator(f):
        """Bind the freshness-checked wrapper to the view function."""
        @wraps(f)
        def wrapper(*args, **kwargs):
            """Verify _fresh_login_at then call the wrapped view."""
            configured = (
                max_age_minutes
                if max_age_minutes is not None
                else current_app.config["FRESH_LOGIN_MAX_AGE_MINUTES"]
            )
            threshold = timedelta(minutes=configured)
            if _fresh_login_is_within(threshold):
                return f(*args, **kwargs)
            # The user must re-authenticate.  Build a /reauth URL
            # carrying the current request URL as ``next`` so the
            # redirect after a successful re-auth lands them back on
            # the original action target.  ``request.url`` is the
            # full URL (including query string); the /reauth handler
            # validates it through the same ``_is_safe_redirect``
            # helper used by /login, so an attacker who somehow seeds
            # a malicious ``next`` (e.g. via an open redirect on a
            # third-party site that links into a decorated route)
            # cannot redirect the user off-origin after re-auth.
            reauth_url = url_for("auth.reauth", next=request.url)
            if request.headers.get("HX-Request"):
                # HTMX swaps response bodies; a 302 would render
                # /reauth's HTML into whatever fragment slot the
                # original request targeted.  Use ``HX-Redirect``
                # with a 204 (No Content) so htmx does a full-page
                # navigation instead.  Status 204 keeps the body
                # empty so no swap happens regardless of the
                # ``hx-target``/``hx-swap`` configuration.
                response = make_response("", 204)
                response.headers["HX-Redirect"] = reauth_url
                return response
            return redirect(reauth_url)
        return wrapper
    return decorator
