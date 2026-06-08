"""
Shekel Budget App -- Shared Commit / Optimistic-Lock Route Helpers

The cross-route home for the ``try: db.session.commit() except
StaleDataError`` idiom.  Every form-mutation route in the app (the
transaction- and transfer-template CRUD routes, plus the salary,
savings, and account CRUD routes) closes with the same shape: commit the
unit of work and, when SQLAlchemy's ``version_id`` optimistic lock
catches a concurrent edit, roll back and convert the
:class:`StaleDataError` into a user-facing flash + redirect rather than a
500.

Three things needed to report such a conflict -- the logging identity,
the user-facing flash text, and where to send the user -- always travel
together, so they are bundled into the frozen
:class:`StaleConflictContext`.  The same bundle drives the pre-flush
form-side mirror
:func:`app.routes._recurrence_form_helpers.handle_stale_form_conflict`,
which adds only the submitted / current version counters.

Two wrappers cover the two structural variants:

* :func:`commit_or_handle_stale` -- the plain case (the body's writes are
  already staged; only the ``commit`` can raise the stale race).
* :func:`regenerate_and_commit_or_stale` -- the case where an
  in-transaction regeneration step must run inside the SAME ``try`` as
  the commit because the regeneration itself flushes and can raise the
  stale race (the salary raise / deduction routes).

:func:`handle_stale_conflict` is the shared body both wrappers delegate
to.  Route-layer module (leading underscore = route-internal) because
the helpers consume Flask ``flash`` / ``redirect`` / ``url_for`` (the
last two via :class:`~app.routes._redirect_target.RedirectTarget`);
``CLAUDE.md::Architecture`` keeps services free of Flask globals.
"""
import logging
from collections.abc import Callable
from dataclasses import dataclass

from flask import Response, flash
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.routes._redirect_target import RedirectTarget


@dataclass(frozen=True)
class StaleConflictContext:
    """Everything needed to report a stale-data conflict to the user.

    Bundles the three things every stale-conflict handler needs and
    that the form-mutation routes always supply together: the logging
    identity (so the conflict line lands under the route's own logger
    name and names the mutating row), the fully-formed flash string,
    and the redirect destination.  Threaded unchanged through
    :func:`commit_or_handle_stale` /
    :func:`regenerate_and_commit_or_stale` (which only forward it on a
    conflict) and consumed by :func:`handle_stale_conflict`; the
    pre-flush form-side mirror
    :func:`app.routes._recurrence_form_helpers.handle_stale_form_conflict`
    reuses it verbatim, adding only the version counters.

    Attributes:
        logger: Per-module logger; the context carries it rather than
            the helper owning one so conflict records originate at the
            route module and log grep / filtering by
            ``logger=app.routes.<blueprint>`` keeps working.
        log_label: Short label for the log line, e.g.
            ``"update_template"`` or ``"archive_account"``.
        log_id: The mutating row's id, used in the log message.
        flash_message: Fully-formed flash string shown to the user.
        redirect: Where to send the user after the conflict.
    """

    logger: logging.Logger
    log_label: str
    log_id: int
    flash_message: str
    redirect: RedirectTarget


def handle_stale_conflict(ctx: StaleConflictContext) -> Response:
    """Roll back, log, flash, and redirect for stale-data conflicts.

    The canonical handler for the
    ``try: db.session.commit() except StaleDataError`` pattern.  Called
    from inside the ``except`` block (directly, or via
    :func:`commit_or_handle_stale` /
    :func:`regenerate_and_commit_or_stale`) -- the caller owns the
    ``try`` and any other exception handling (notably
    :class:`~sqlalchemy.exc.IntegrityError`, which some routes translate
    separately).

    Args:
        ctx: The :class:`StaleConflictContext` describing where to log
            the conflict, what to flash, and where to redirect.

    Returns:
        A Flask redirect :class:`Response`; the caller returns it
        directly so the route's control flow is identical to the
        pre-extraction shape.
    """
    db.session.rollback()
    ctx.logger.info(
        "Stale-data conflict on %s id=%d", ctx.log_label, ctx.log_id,
    )
    flash(ctx.flash_message, "warning")
    return ctx.redirect.to_response()


def commit_or_handle_stale(ctx: StaleConflictContext) -> Response | None:
    """Commit the session, converting a stale-data race into flash+redirect.

    Wraps the plain
    ``try: db.session.commit() except StaleDataError: ...`` idiom.  On a
    clean commit it returns ``None`` and the caller proceeds to its own
    success flash + redirect; on the optimistic-lock conflict it
    delegates to :func:`handle_stale_conflict` (rollback + log + flash +
    redirect) and returns that :class:`Response` for the caller to
    return verbatim.

    Only :class:`StaleDataError` is caught.  Routes that additionally
    translate an :class:`~sqlalchemy.exc.IntegrityError` at commit time
    keep their explicit ``try`` block so this helper stays single-purpose
    (coding-standards rule 13).  Routes whose body must run an
    in-transaction regeneration that can itself raise the stale race use
    :func:`regenerate_and_commit_or_stale` instead.

    Args:
        ctx: The :class:`StaleConflictContext` forwarded to
            :func:`handle_stale_conflict` on a conflict.

    Returns:
        ``None`` on a clean commit; the conflict redirect
        :class:`Response` otherwise.
    """
    try:
        db.session.commit()
        return None
    except StaleDataError:
        return handle_stale_conflict(ctx)


def regenerate_and_commit_or_stale(
    regenerate: Callable[[], None],
    *,
    ctx: StaleConflictContext,
) -> Response | None:
    """Run a regeneration step then commit, under one stale-race guard.

    Variant of :func:`commit_or_handle_stale` for routes whose
    in-transaction regeneration must stay INSIDE the same ``try`` as the
    commit -- the salary raise / deduction routes call
    ``_regenerate_salary_transactions`` which itself flushes and can
    raise :class:`StaleDataError`, and the pre-extraction handlers caught
    a stale race from either the regeneration or the commit.  Running the
    regeneration outside the guard would silently stop catching its stale
    race, so it is invoked here, inside the ``try``.

    Args:
        regenerate: Zero-argument callable performing the
            in-transaction regeneration (e.g.
            ``lambda: _regenerate_salary_transactions(profile)``).  Runs
            before the commit, inside the stale-race guard.
        ctx: The :class:`StaleConflictContext` forwarded to
            :func:`handle_stale_conflict` on a conflict.

    Returns:
        ``None`` on success; the conflict redirect :class:`Response`
        when the regeneration or the commit raises
        :class:`StaleDataError`.
    """
    try:
        regenerate()
        db.session.commit()
        return None
    except StaleDataError:
        return handle_stale_conflict(ctx)


__all__ = [
    "StaleConflictContext",
    "handle_stale_conflict",
    "commit_or_handle_stale",
    "regenerate_and_commit_or_stale",
]
