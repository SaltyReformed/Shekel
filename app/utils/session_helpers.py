"""Shekel Budget App -- Session invalidation helpers.

Force-invalidate every session for a given user EXCEPT the one
making the current request.  Used by every auth-factor state change
(password change, MFA disable, backup-code consumption, future
WebAuthn changes) so the same correctness invariants are enforced
in one place rather than copy-pasted at each call site.

Mechanism
---------

The user record carries ``session_invalidated_at``, a timezone-aware
column updated whenever the user's other sessions must be terminated.
On every request, ``load_user`` in ``app/__init__.py`` rejects any
session whose cookie-side ``_session_created_at`` is strictly older
than the database-side ``session_invalidated_at``.

``invalidate_other_sessions`` writes a single ``now`` timestamp to
both fields atomically (commit, then refresh the cookie), so:

  * Every other live session for the user has a strictly older
    ``_session_created_at`` and is rejected on its next request.
  * The session making this request has ``_session_created_at`` equal
    to ``session_invalidated_at`` -- the strict less-than comparison
    in ``load_user`` is False, so the current session survives.

Caller contract
---------------

  * The caller must have already committed any state change that
    motivates the invalidation (password hash update, MFA disable,
    backup-code list mutation).  The helper commits its own write
    of ``session_invalidated_at`` and would inadvertently flush any
    in-flight session state with it.

  * The helper requires an active Flask request context because it
    refreshes the session cookie via ``flask.session``.  Calling it
    outside a request context is a programming error -- it will
    surface as a ``RuntimeError`` from Flask, not a silent no-op.

  * The helper assumes ``user`` is the same identity authenticated on
    the current request.  Passing a different user would invalidate
    that user's sessions but refresh the WRONG cookie -- the caller's
    session would still be rejected on the next request.  Documented
    here rather than enforced at runtime: ``flask_login.current_user``
    is a request-local proxy and importing it into this module would
    couple the helper to the auth blueprint's request lifecycle for
    no defensive gain.

Audit references: F-002, F-003, F-032 (commit C-08 of the
2026-04-15 security remediation plan).  See also
``app/routes/auth.py:change_password`` and
``app/routes/auth.py:invalidate_sessions`` for the two pre-existing
call sites that implement the same semantics inline.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import session as flask_session

from app.extensions import db
from app.models.user import User
from app.utils.log_events import AUTH, log_event


logger = logging.getLogger(__name__)


# Session-cookie key written by every code path that completes a
# login (login, mfa_verify, change_password, invalidate_sessions).
# Centralised here so the helper and the load_user check stay in
# sync.  Defined as a module-level constant rather than a magic
# string so a future renaming pass changes one place, not seven.
SESSION_CREATED_AT_KEY = "_session_created_at"


def invalidate_other_sessions(user: User, reason: str) -> None:
    """Invalidate every session for ``user`` except the one on this request.

    Stamps ``user.session_invalidated_at`` to ``datetime.now(UTC)``,
    commits, then refreshes the request's session cookie so this
    session survives the bump.  See module docstring for the
    cookie-vs-DB comparison the bump targets.

    Args:
        user: The :class:`~app.models.user.User` whose other sessions
            must be terminated.  Must be a persistent ORM instance --
            the helper writes through SQLAlchemy and commits the
            current session.  Must also be the same identity
            authenticated on the current request, or the wrong cookie
            will be refreshed (see module docstring).
        reason: Short machine-readable label for the audit log
            (``"password_change"``, ``"mfa_disabled"``,
            ``"backup_code_consumed"``).  Required, non-empty -- a
            blank reason defeats the audit trail this helper exists
            to produce.

    Raises:
        ValueError: ``user`` is None, or ``reason`` is empty/None.
            Raised before any DB or session mutation so the caller's
            state is unchanged on misuse.
        RuntimeError: No active Flask request context (raised by
            ``flask.session`` on access).
        sqlalchemy.exc.SQLAlchemyError: The commit of
            ``session_invalidated_at`` failed.  The session cookie is
            NOT updated in this case -- the existing cookie remains
            valid against the un-bumped DB column, which is the safe
            default (no surprise logouts on transient DB errors).

    Side effects:
        * Writes ``user.session_invalidated_at`` and commits.
        * Updates ``flask.session[SESSION_CREATED_AT_KEY]``.
        * Emits a structured ``other_sessions_invalidated`` audit log
          event tagged with ``reason`` and ``user_id``.
    """
    if user is None:
        raise ValueError(
            "invalidate_other_sessions requires a User instance; got None"
        )
    if not reason:
        raise ValueError(
            "invalidate_other_sessions requires a non-empty reason "
            f"for the audit log; got {reason!r}"
        )

    # Single ``now`` so the DB column and the cookie carry the
    # identical timestamp.  load_user compares ``cookie < db`` with
    # strict less-than, so equality lets the current session survive
    # while every earlier session is rejected.  Reusing one value
    # also avoids a 2nd datetime.now() call drifting microseconds
    # into the future and accidentally invalidating this session if
    # the comparison ever changes to <=.
    now = datetime.now(timezone.utc)
    user.session_invalidated_at = now
    db.session.commit()

    flask_session[SESSION_CREATED_AT_KEY] = now.isoformat()

    log_event(
        logger, logging.INFO, "other_sessions_invalidated", AUTH,
        "Other sessions invalidated",
        user_id=user.id, reason=reason,
    )
