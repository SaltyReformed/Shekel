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

Three wrappers cover the structural variants:

* :func:`commit_or_handle_stale` -- the plain case (the body's writes are
  already staged; only the ``commit`` can raise the stale race).
* :func:`regenerate_and_commit_or_stale` -- the case where an
  in-transaction regeneration step must run inside the SAME ``try`` as
  the commit because the regeneration itself flushes and can raise the
  stale race (the salary raise / deduction routes).
* :func:`regenerate_commit_or_report` -- the full-coverage variant of
  the above: it adds the two broader ``except`` arms those routes pair
  the stale guard with -- the optional known-unique-constraint
  translation (:func:`handle_unique_violation`) and the generic DB-error
  fallback (:func:`handle_db_error`) -- so a salary mutation route can
  delegate its whole "regenerate, commit, report failures" shape and
  carry no hand-written ``try``/``except`` at all.

:func:`handle_stale_conflict` is the shared body both wrappers delegate
to.

:func:`handle_db_error` (with its :class:`DbErrorContext` bundle) is the
companion fallback for the broader ``except SQLAlchemyError`` arm those
same routes close with: once the stale race and any expected
``IntegrityError`` are handled, any remaining DB-tier error rolls back,
logs the traceback (``logger.exception``), and becomes a danger flash +
redirect instead of a 500.  :func:`handle_unique_violation` (with its
:class:`UniqueViolationContext`) is the third report helper: it
recognises the one unique constraint a route expects to collide on and
turns it into a recoverable warning flash, deferring every other
IntegrityError to :func:`handle_db_error`.

Route-layer module (leading underscore = route-internal) because
the helpers consume Flask ``flash`` / ``redirect`` / ``url_for`` (the
last two via :class:`~app.routes._redirect_target.RedirectTarget`);
``CLAUDE.md::Architecture`` keeps services free of Flask globals.
"""
import logging
from collections.abc import Callable
from dataclasses import dataclass

from flask import Response, flash
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.routes._redirect_target import RedirectTarget
from app.utils.db_errors import is_unique_violation


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


@dataclass(frozen=True)
class DbErrorContext:
    """Everything needed to report a non-stale DB-tier failure to the user.

    The companion to :class:`StaleConflictContext` for the broad
    ``except SQLAlchemyError`` fallback the form-mutation routes close
    with.  The optimistic-lock race is handled separately (the stale
    helpers above, or the route's :class:`~sqlalchemy.exc.IntegrityError`
    branch); any remaining SQLAlchemy error (FK / CHECK / NUMERIC range /
    OperationalError / a regenerate-flush failure) rolls back and turns
    into a danger flash + redirect rather than a 500.  Bundles the four
    things every such handler supplies: the logging identity + message,
    the user-facing flash text, and where to send the user.

    Unlike :class:`StaleConflictContext` (whose log line is the fixed
    ``"Stale-data conflict on %s id=%d"`` at INFO), the fallback's log
    message and its ``%`` arguments vary per route, so they are carried
    verbatim and emitted via ``logger.exception`` (ERROR + traceback) to
    capture the unexpected DB error for debugging.

    Attributes:
        logger: Per-module logger; carried (not owned) so the error line
            originates at the route module, matching
            :class:`StaleConflictContext`.
        log_message: ``logging``-style format string, e.g.
            ``"user_id=%d failed to update salary profile %d"``.
        log_args: The ``%`` arguments for ``log_message`` (the user id
            and any mutated-row ids).
        flash_message: Fully-formed danger flash string shown to the user.
        redirect: Where to send the user after the failure.
    """

    logger: logging.Logger
    log_message: str
    log_args: tuple[int, ...]
    flash_message: str
    redirect: RedirectTarget


def handle_db_error(ctx: DbErrorContext) -> Response:
    """Roll back, log the traceback, flash, and redirect for DB failures.

    The canonical handler for the form-mutation routes'
    ``except SQLAlchemyError`` fallback -- the broad-DB-error twin of
    :func:`handle_stale_conflict`.  Called from inside the ``except``
    block; the caller owns the ``try`` and any earlier, more specific
    branches (notably :class:`~sqlalchemy.exc.IntegrityError`, which
    several routes translate to a friendlier message first).

    Args:
        ctx: The :class:`DbErrorContext` describing what to log, what to
            flash, and where to redirect.

    Returns:
        A Flask redirect :class:`Response`; the caller returns it
        directly so the route's control flow is identical to the
        pre-extraction shape.
    """
    db.session.rollback()
    ctx.logger.exception(ctx.log_message, *ctx.log_args)
    flash(ctx.flash_message, "danger")
    return ctx.redirect.to_response()


@dataclass(frozen=True)
class UniqueViolationContext:
    """The expected-collision half of a route's ``except IntegrityError`` arm.

    Some form-mutation routes can hit one *known* unique constraint that
    represents a recoverable user error -- renaming a deduction to a name
    a sibling already holds, or editing a raise onto a (type, year,
    month) a sibling already covers.  Those routes translate that one
    constraint into a friendly WARNING flash + redirect and let every
    *other* IntegrityError fall through to the generic
    :func:`handle_db_error` fallback.

    :func:`handle_unique_violation` consumes this bundle to recognise the
    expected constraint and report the collision; the fall-through is the
    caller's ``error_ctx`` :class:`DbErrorContext` (see
    :func:`regenerate_commit_or_report`).

    Attributes:
        logger: Per-module logger; carried (not owned) so the collision
            record originates at the route module, matching
            :class:`StaleConflictContext` / :class:`DbErrorContext`.
        constraint: The unique constraint / index name this route expects
            to collide on (e.g.
            ``"uq_salary_raises_profile_type_year_month"``).  Matched
            exactly against the IntegrityError via
            :func:`app.utils.db_errors.is_unique_violation`.
        log_message: ``logging``-style format string for the recognised
            collision, logged at INFO (a routine user error, not a fault).
        log_args: The ``%`` arguments for ``log_message``.
        flash_message: The WARNING flash shown when the collision is the
            expected one.
        redirect: Where to send the user after the collision.
    """

    logger: logging.Logger
    constraint: str
    log_message: str
    log_args: tuple[int, ...]
    flash_message: str
    redirect: RedirectTarget


def handle_unique_violation(
    exc: IntegrityError, ctx: UniqueViolationContext
) -> Response | None:
    """Report an expected unique-constraint collision, else defer.

    For the ``except IntegrityError`` arm of a form-mutation route that
    can collide on one known constraint.  When ``exc`` fired on
    ``ctx.constraint`` it is the recoverable user error: roll back, log
    it at INFO, flash the WARNING, and return the redirect
    :class:`Response`.  When it fired on anything else, return ``None``
    so the caller falls through to its generic :func:`handle_db_error`
    fallback -- the session is left untouched for that fallback to roll
    back and log the traceback.

    Args:
        exc: The :class:`~sqlalchemy.exc.IntegrityError` caught by the
            route (or by :func:`regenerate_commit_or_report`).
        ctx: The :class:`UniqueViolationContext` naming the expected
            constraint and how to report it.

    Returns:
        The collision redirect :class:`Response` when ``exc`` matched
        ``ctx.constraint``; ``None`` otherwise (defer to the generic
        fallback).
    """
    if not is_unique_violation(exc, ctx.constraint):
        return None
    db.session.rollback()
    ctx.logger.info(ctx.log_message, *ctx.log_args)
    flash(ctx.flash_message, "warning")
    return ctx.redirect.to_response()


def regenerate_commit_or_report(
    regenerate: Callable[[], None],
    *,
    stale_ctx: StaleConflictContext,
    error_ctx: DbErrorContext,
    on_integrity: UniqueViolationContext | None = None,
) -> Response | None:
    """Run regenerate+commit and report every recoverable failure mode.

    The full-coverage variant for the salary raise / deduction / profile
    routes: it runs the in-transaction regeneration + commit under the
    stale-race guard (:func:`regenerate_and_commit_or_stale`) and adds
    the two broader ``except`` arms those routes pair it with -- the
    optional known-unique-constraint translation
    (:func:`handle_unique_violation`) and the generic DB-error fallback
    (:func:`handle_db_error`) -- so the whole "regenerate, commit, report
    its failures" shape lives in one place instead of a hand-written
    ``try``/``except`` in every route.

    Failure routing (most specific first, mirroring the hand-written
    form -- :class:`~sqlalchemy.exc.IntegrityError` is a
    :class:`~sqlalchemy.exc.SQLAlchemyError`, so it must be caught
    first):

    * :class:`~sqlalchemy.orm.exc.StaleDataError` -- caught inside
      :func:`regenerate_and_commit_or_stale`; returns its stale
      flash + redirect.
    * :class:`~sqlalchemy.exc.IntegrityError` on ``on_integrity.constraint``
      (when ``on_integrity`` is supplied) -- the recoverable collision,
      via :func:`handle_unique_violation`.
    * any other :class:`~sqlalchemy.exc.SQLAlchemyError` -- including an
      IntegrityError on a different constraint, or any IntegrityError
      when ``on_integrity`` is ``None`` -- the generic fallback, via
      :func:`handle_db_error`.

    Args:
        regenerate: Zero-argument callable performing the in-transaction
            regeneration (and any other staged mutations that must be
            protected by the same guard); forwarded to
            :func:`regenerate_and_commit_or_stale`.
        stale_ctx: Context for the stale-data race.
        error_ctx: Context for the generic DB-error fallback.
        on_integrity: Optional context for the one expected unique
            constraint; ``None`` for routes that have no such constraint
            (the deletes, the profile edit), whose IntegrityErrors then
            take the generic fallback exactly as a bare
            ``except SQLAlchemyError`` would.

    Returns:
        ``None`` on a clean commit; otherwise the flash + redirect
        :class:`Response` for whichever failure mode fired, for the
        caller to return verbatim.
    """
    try:
        return regenerate_and_commit_or_stale(regenerate, ctx=stale_ctx)
    except IntegrityError as exc:
        if on_integrity is not None:
            response = handle_unique_violation(exc, on_integrity)
            if response is not None:
                return response
        return handle_db_error(error_ctx)
    except SQLAlchemyError:
        return handle_db_error(error_ctx)


__all__ = [
    "StaleConflictContext",
    "DbErrorContext",
    "UniqueViolationContext",
    "handle_stale_conflict",
    "handle_db_error",
    "handle_unique_violation",
    "commit_or_handle_stale",
    "regenerate_and_commit_or_stale",
    "regenerate_commit_or_report",
]
