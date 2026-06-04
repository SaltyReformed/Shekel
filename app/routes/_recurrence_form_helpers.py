"""
Shekel Budget App -- Recurrence-Form Route Helpers (F-24, F-26)

Six helpers shared between the transaction-template
(:mod:`app.routes.templates`) and transfer-template
(:mod:`app.routes.transfers`) CRUD routes:

* :func:`build_recurrence_rule_from_form` -- consumes a Marshmallow-
  validated payload, pops the recurrence-related keys, and returns a
  fresh :class:`RecurrenceRule` (added to the session and flushed),
  ``None`` when no pattern was selected, or a Flask redirect
  :class:`Response` when validation fails (invalid pattern id,
  invalid start period for every-N-periods auto-offset).  [F-24]
* :func:`update_recurrence_rule_from_form` -- sibling of the builder
  for the pattern-changed-on-an-existing-rule branch: re-points the
  template's current :class:`RecurrenceRule` in place (preserving its
  id and the owning FK), pops the recurrence keys, and returns
  ``None`` on success or a redirect :class:`Response` for an invalid
  pattern id.  [F-24]
* :func:`resolve_recurrence_rule_for_update` -- dispatches the two
  update-form branches (re-point existing rule vs build + link a new
  one) so each ``update_*`` route resolves its recurrence rule with a
  single call.  [F-24]
* :func:`commit_or_handle_stale` -- wraps the
  ``try: db.session.commit() except StaleDataError`` idiom shared by
  every form-mutation route's final commit; returns ``None`` on a
  clean commit or the conflict redirect otherwise.  [F-24]
* :func:`handle_stale_conflict` -- emits the canonical stale-data
  flash + redirect when a commit raises :class:`StaleDataError`.
  [F-24]
* :func:`handle_stale_form_conflict` -- pre-flush optimistic-locking
  guard for the ``submitted_version != template.version_id``
  branch; logs both counters so post-mortem analysis can reconstruct
  the race; redirects.  [F-26 pair 1]
* :func:`handle_recurrence_conflict` -- Phase-1 auto-keep-overrides
  advisory handler invoked from the ``except RecurrenceConflict``
  branch of the regeneration call; logs the override / delete
  counts and flashes the canonical "kept as-is" notice; returns
  ``None`` because the caller continues executing.  [F-26 pair 2]

Route-layer module rather than service because three of the four
helpers consume Flask ``flash`` / ``redirect`` / ``url_for``;
``CLAUDE.md::Architecture`` keeps services isolated from Flask
globals.  The leading underscore marks the module as route-internal.

Module-level flash-template constants centralise the canonical
"stale by another action" and "kept as-is" copy without forcing
every caller through a single wording (some routes name "while you
were editing" -- the update-template / update-transfer-template
forms; others omit it -- archive / unarchive / hard-delete).
"""
import logging
from datetime import date
from typing import Any

from flask import Response, flash, redirect, url_for
from sqlalchemy.orm.exc import StaleDataError

from app import ref_cache
from app.enums import RecurrencePatternEnum
from app.exceptions import RecurrenceConflict
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern


# Stale-conflict flash templates.  The ``{noun}`` placeholder is
# substituted by the caller ("recurring transaction" /
# "recurring transfer") so the human label matches the route's
# domain without forcing the helper to know the route taxonomy.

STALE_EDITING_MESSAGE: str = (
    "This {noun} was changed by another action while you were "
    "editing.  Please reload and try again."
)
"""Flash template for routes invoked from an edit form (update_*)."""

STALE_ACTION_MESSAGE: str = (
    "This {noun} was changed by another action.  "
    "Please reload and try again."
)
"""Flash template for non-edit-form mutations (archive / unarchive /
hard-delete) where "while you were editing" would be misleading."""

_RECURRENCE_CONFLICT_FLASH: str = (
    "Note: {overridden_count} overridden and "
    "{deleted_count} deleted entries were kept as-is."
)
"""Flash template for the Phase-1 auto-keep-overrides advisory
emitted by :func:`handle_recurrence_conflict` when a regenerate-
for-template call surfaces overridden / deleted instances.  The
counts are substituted by the caller; the wording is byte-
identical between the templates and transfers sides (the only
pre-extraction difference was the log prefix, which the
``log_label`` kwarg preserves)."""


# Keys the recurrence-rule helper pops from the validated form payload
# regardless of whether a pattern was selected.  Listed here as
# module-level constants so the "drop every recurrence key" logic
# stays in one place.

_BASE_RECURRENCE_KEYS: tuple[str, ...] = (
    "interval_n",
    "offset_periods",
    "day_of_month",
    "month_of_year",
    "end_date",
)

_DUE_DAY_KEY: str = "due_day_of_month"


def build_recurrence_rule_from_form(
    data: dict[str, Any],
    *,
    user_id: int,
    start_period_id: int | None,
    end_date_value: date | None,
    redirect_endpoint: str,
    redirect_endpoint_kwargs: dict[str, Any] | None = None,
    include_due_day_of_month: bool = False,
) -> RecurrenceRule | Response | None:
    """Build a :class:`RecurrenceRule` from a validated form payload.

    Pops every recurrence-related key from ``data`` so the caller's
    downstream ``TransactionTemplate`` / ``TransferTemplate``
    constructor does not receive stray kwargs.

    Args:
        data: Marshmallow-validated payload; mutated in place.  The
            helper pops ``recurrence_pattern``, ``interval_n``,
            ``offset_periods``, ``day_of_month``, ``month_of_year``,
            ``end_date``, and -- when ``include_due_day_of_month`` is
            ``True`` -- ``due_day_of_month``.
        user_id: Owner of the resulting :class:`RecurrenceRule` row.
        start_period_id: From the form; needed for the every-N-periods
            auto-offset derivation.  Caller pops it before calling the
            helper because the same value is later persisted on the
            :class:`RecurrenceRule`.
        end_date_value: From the form; copied verbatim onto the rule.
        redirect_endpoint: Flask endpoint name for the redirect-on-
            validation-error response (invalid pattern id or invalid
            start period for every-N-periods).
        redirect_endpoint_kwargs: Extra kwargs for ``url_for`` (e.g.
            ``{"template_id": template_id}`` for the update path).
        include_due_day_of_month: ``True`` for transaction templates,
            ``False`` for transfer templates.  Transfer-template
            schemas do not expose ``due_day_of_month``; passing
            ``True`` for a transfer payload would silently set the
            column from a key the schema never validated.

    Returns:
        * :class:`RecurrenceRule` -- newly added, flushed, ready to
          link.  The caller is responsible for setting any owning-row
          FK (e.g. ``template.recurrence_rule_id = rule.id``).
        * ``None`` -- no recurrence pattern was selected; the helper
          still popped every recurrence key from ``data``.
        * :class:`Response` -- a Flask redirect to
          ``redirect_endpoint``; the caller returns it directly so the
          route's control flow matches the pre-extraction shape.
    """
    redirect_endpoint_kwargs = redirect_endpoint_kwargs or {}
    pattern_id_str = data.pop("recurrence_pattern", None)

    if not pattern_id_str:
        # No pattern: drop every recurrence-related key so the caller's
        # model constructor does not receive stray kwargs.
        for key in _BASE_RECURRENCE_KEYS:
            data.pop(key, None)
        if include_due_day_of_month:
            data.pop(_DUE_DAY_KEY, None)
        return None

    pattern = db.session.get(RecurrencePattern, int(pattern_id_str))
    if pattern is None:
        flash("Invalid recurrence pattern.", "danger")
        return redirect(url_for(
            redirect_endpoint, **redirect_endpoint_kwargs,
        ))

    interval_n = data.pop("interval_n", 1)
    offset_periods = data.pop("offset_periods", 0)
    # Pop ``end_date`` from data even though the value comes from
    # ``end_date_value`` -- keeps the "all recurrence keys removed
    # from data" contract symmetric between the pattern and
    # no-pattern branches, so the caller's downstream model
    # constructor never receives ``end_date`` as a stray kwarg.
    data.pop("end_date", None)

    # Auto-derive offset from the start period for EVERY_N_PERIODS so
    # the rule generates against the user's chosen rhythm rather than
    # the default zero-offset cadence.
    every_n_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.EVERY_N_PERIODS,
    )
    if (int(pattern_id_str) == every_n_id
            and start_period_id and interval_n):
        start_period = db.session.get(PayPeriod, start_period_id)
        if (start_period is None
                or start_period.user_id != user_id):
            flash("Invalid start period.", "danger")
            return redirect(url_for(
                redirect_endpoint, **redirect_endpoint_kwargs,
            ))
        offset_periods = start_period.period_index % interval_n

    rule_kwargs: dict[str, Any] = {
        "user_id": user_id,
        "pattern_id": pattern.id,
        "interval_n": interval_n,
        "offset_periods": offset_periods,
        "day_of_month": data.pop("day_of_month", None),
        "month_of_year": data.pop("month_of_year", None),
        "start_period_id": start_period_id,
        "end_date": end_date_value,
    }
    if include_due_day_of_month:
        rule_kwargs["due_day_of_month"] = data.pop(_DUE_DAY_KEY, None)

    rule = RecurrenceRule(**rule_kwargs)
    db.session.add(rule)
    db.session.flush()
    return rule


def update_recurrence_rule_from_form(
    rule: RecurrenceRule,
    data: dict[str, Any],
    *,
    end_date_value: date | None,
    redirect_endpoint: str,
    redirect_endpoint_kwargs: dict[str, Any] | None = None,
    include_due_day_of_month: bool = False,
) -> Response | None:
    """Re-point an existing :class:`RecurrenceRule` from a form payload.

    Sibling of :func:`build_recurrence_rule_from_form` for the
    pattern-changed-on-an-existing-rule branch of the ``update_*``
    routes.  When a template already owns a rule, the edit mutates
    that same row in place -- preserving its primary key and the
    template's ``recurrence_rule_id`` FK -- rather than creating a
    new rule, then pops every recurrence key from ``data`` so the
    caller's downstream ``setattr`` loop never sees a stray kwarg.

    Unlike :func:`build_recurrence_rule_from_form`, this helper does
    NOT auto-derive ``offset_periods`` from a start period for the
    ``EVERY_N_PERIODS`` pattern: the update form does not re-collect
    ``start_period_id`` (it is fixed at creation), so ``offset_periods``
    is taken verbatim from the payload.  This preserves the
    pre-extraction inline behaviour exactly.

    Args:
        rule: The existing :class:`RecurrenceRule` to mutate in place.
            The caller guarantees it is non-``None`` (the branch guard
            tests ``template.recurrence_rule``).
        data: Marshmallow-validated payload; mutated in place.  Pops
            ``recurrence_pattern``, ``interval_n``, ``offset_periods``,
            ``day_of_month``, ``month_of_year``, and -- when
            ``include_due_day_of_month`` is ``True`` --
            ``due_day_of_month``.
        end_date_value: From the form (already popped by the caller);
            copied verbatim onto ``rule.end_date``.
        redirect_endpoint: Flask endpoint name for the invalid-pattern
            redirect response.
        redirect_endpoint_kwargs: Extra kwargs for ``url_for`` (e.g.
            ``{"template_id": template_id}``).
        include_due_day_of_month: ``True`` for transaction templates,
            ``False`` for transfer templates (whose schema does not
            expose ``due_day_of_month``).

    Returns:
        * ``None`` -- the rule was re-pointed successfully; the caller
          continues to the field-update loop.
        * :class:`Response` -- a Flask redirect emitted when the
          submitted ``recurrence_pattern`` id does not resolve to a
          :class:`RecurrencePattern`; the caller returns it directly
          so the route's control flow matches the pre-extraction shape.
    """
    pattern_id_str = data.pop("recurrence_pattern")
    pattern = db.session.get(RecurrencePattern, int(pattern_id_str))
    if pattern is None:
        flash("Invalid recurrence pattern.", "danger")
        return redirect(url_for(
            redirect_endpoint, **(redirect_endpoint_kwargs or {}),
        ))

    rule.pattern_id = pattern.id
    rule.interval_n = data.pop("interval_n", 1)
    rule.offset_periods = data.pop("offset_periods", 0)
    rule.day_of_month = data.pop("day_of_month", None)
    if include_due_day_of_month:
        rule.due_day_of_month = data.pop("due_day_of_month", None)
    rule.month_of_year = data.pop("month_of_year", None)
    rule.end_date = end_date_value
    return None


def resolve_recurrence_rule_for_update(
    template: Any,
    data: dict[str, Any],
    *,
    end_date_value: date | None,
    redirect_endpoint: str,
    redirect_endpoint_kwargs: dict[str, Any] | None = None,
    include_due_day_of_month: bool = False,
) -> Response | None:
    """Re-point or rebuild a template's recurrence rule for an update.

    Dispatches the two update-form branches shared by
    :func:`app.routes.templates.update_template` and
    :func:`app.routes.transfers.update_transfer_template`:

    * pattern present AND the template already owns a rule -> re-point
      that row in place via :func:`update_recurrence_rule_from_form`
      (its primary key and the template's ``recurrence_rule_id`` FK
      stay stable);
    * otherwise -> build a fresh rule via
      :func:`build_recurrence_rule_from_form` (or ``None`` when no
      pattern was selected) and link it onto
      ``template.recurrence_rule_id``.

    The owning row's user scope comes from ``template.user_id`` -- the
    caller fetched the template through an owner-scoped ``get_or_404``,
    so this equals the pre-extraction ``current_user.id``.
    ``start_period_id`` is fixed at create time, so the builder is
    invoked with ``start_period_id=None`` and never performs the
    ``EVERY_N_PERIODS`` start-period owner re-check.

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate``
            being updated.  Accessed for ``recurrence_rule``,
            ``recurrence_rule_id`` (assigned when a new rule is built),
            and ``user_id``.  Mutated in place.
        data: Marshmallow-validated payload; the recurrence keys are
            popped by the delegated helper.
        end_date_value: From the form (already popped by the caller).
        redirect_endpoint: Flask endpoint for the invalid-pattern
            redirect response.
        redirect_endpoint_kwargs: Extra kwargs for ``url_for``.
        include_due_day_of_month: ``True`` for transaction templates,
            ``False`` for transfer templates.

    Returns:
        * ``None`` -- the rule was resolved; the caller continues to
          the field-update loop.
        * :class:`Response` -- a Flask redirect for an invalid
          recurrence pattern id; the caller returns it directly.
    """
    if data.get("recurrence_pattern") and template.recurrence_rule:
        return update_recurrence_rule_from_form(
            template.recurrence_rule,
            data,
            end_date_value=end_date_value,
            redirect_endpoint=redirect_endpoint,
            redirect_endpoint_kwargs=redirect_endpoint_kwargs,
            include_due_day_of_month=include_due_day_of_month,
        )

    rule_or_redirect = build_recurrence_rule_from_form(
        data,
        user_id=template.user_id,
        start_period_id=None,
        end_date_value=end_date_value,
        redirect_endpoint=redirect_endpoint,
        redirect_endpoint_kwargs=redirect_endpoint_kwargs,
        include_due_day_of_month=include_due_day_of_month,
    )
    if isinstance(rule_or_redirect, Response):
        return rule_or_redirect
    if rule_or_redirect is not None:
        template.recurrence_rule_id = rule_or_redirect.id
    return None


def handle_stale_conflict(
    *,
    logger: logging.Logger,
    log_label: str,
    log_id: int,
    flash_message: str,
    redirect_endpoint: str,
    redirect_endpoint_kwargs: dict[str, Any] | None = None,
) -> Response:
    """Roll back, log, flash, and redirect for stale-data conflicts.

    The canonical handler for the
    ``try: db.session.commit() except StaleDataError`` pattern that
    appears across every templates / transfers mutation route.
    Called from inside the ``except`` block -- the caller is
    responsible for the ``try`` and for re-raising or handling any
    other exception (notably :class:`IntegrityError`, which
    ``update_transfer_template`` catches separately with its own
    name-uniqueness flash).

    Args:
        logger: Per-module logger; the helper does not own one because
            log records should originate at the route module so log
            grep / filtering by ``logger=app.routes.templates`` keeps
            working.
        log_label: Short label for the log message, e.g.
            ``"update_template"`` or
            ``"hard_delete_transfer_template archive-fallback"``.
        log_id: The mutating template id, used in the log message.
        flash_message: Fully-formed flash string.  Callers compose it
            via the :data:`STALE_EDITING_MESSAGE` /
            :data:`STALE_ACTION_MESSAGE` template constants exposed
            by this module, substituting the route's domain noun.
        redirect_endpoint: Flask endpoint to redirect the user to
            (typically the list or edit page).
        redirect_endpoint_kwargs: Kwargs for ``url_for``.

    Returns:
        A Flask redirect :class:`Response`.  The caller returns it
        directly so the route's control flow is identical to the
        pre-extraction shape.
    """
    db.session.rollback()
    logger.info("Stale-data conflict on %s id=%d", log_label, log_id)
    flash(flash_message, "warning")
    return redirect(url_for(
        redirect_endpoint, **(redirect_endpoint_kwargs or {}),
    ))


def commit_or_handle_stale(
    *,
    logger: logging.Logger,
    log_label: str,
    log_id: int,
    flash_message: str,
    redirect_endpoint: str,
    redirect_endpoint_kwargs: dict[str, Any] | None = None,
) -> Response | None:
    """Commit the session, converting a stale-data race into flash+redirect.

    Wraps the
    ``try: db.session.commit() except StaleDataError: ...`` idiom that
    closes every templates / transfers form-mutation route (archive,
    unarchive, hard-delete, and the update routes' final commit).  On
    a clean commit it returns ``None`` and the caller proceeds to its
    own success flash + redirect; on the optimistic-lock conflict it
    delegates to :func:`handle_stale_conflict` (rollback + log + flash
    + redirect) and returns that :class:`Response` for the caller to
    return verbatim.

    Only :class:`StaleDataError` is caught.  Routes that additionally
    translate an :class:`~sqlalchemy.exc.IntegrityError` at commit time
    (e.g. ``update_transfer_template``'s name-uniqueness flash) keep
    their explicit ``try`` block rather than calling this helper, so
    the helper stays single-purpose and does not grow a grab-bag of
    optional exception handlers (coding-standards rule 13).

    Args:
        logger: Per-module logger; passed through to
            :func:`handle_stale_conflict` so log records originate at
            the route module (keeps ``logger=app.routes.templates``
            grep filters working).
        log_label: Short label for the conflict log line, e.g.
            ``"archive_template"`` or
            ``"hard_delete_transfer_template archive-fallback"``.
        log_id: The mutating template id, used in the log message.
        flash_message: Fully-formed flash string, composed by the
            caller via the :data:`STALE_EDITING_MESSAGE` /
            :data:`STALE_ACTION_MESSAGE` templates.
        redirect_endpoint: Flask endpoint to redirect to on conflict.
        redirect_endpoint_kwargs: Kwargs for ``url_for``.

    Returns:
        * ``None`` -- the commit succeeded; the caller continues.
        * :class:`Response` -- the conflict redirect from
          :func:`handle_stale_conflict`; the caller returns it
          directly.
    """
    try:
        db.session.commit()
        return None
    except StaleDataError:
        return handle_stale_conflict(
            logger=logger,
            log_label=log_label,
            log_id=log_id,
            flash_message=flash_message,
            redirect_endpoint=redirect_endpoint,
            redirect_endpoint_kwargs=redirect_endpoint_kwargs,
        )


def handle_stale_form_conflict(
    *,
    logger: logging.Logger,
    log_label: str,
    log_id: int,
    submitted: int,
    current: int,
    flash_message: str,
    redirect_endpoint: str,
    redirect_endpoint_kwargs: dict[str, Any] | None = None,
) -> Response:
    """Optimistic-locking pre-flush form-side conflict handler (F-26).

    Mirror of :func:`handle_stale_conflict` for the
    ``submitted_version != template.version_id`` branch that fires
    before the commit attempt.  Logs both the submitted and current
    counters so post-mortem analysis can reconstruct the race
    (matching the byte-identical pre-extraction log messages on
    both the templates and transfers update routes); flashes the
    caller-supplied message; redirects.  Does NOT roll back the
    session because no DB write has been attempted yet at the
    call site.

    Args:
        logger: Per-module logger; the helper does not own one
            because log records should originate at the route
            module so log grep / filtering by
            ``logger=app.routes.templates`` keeps working.
        log_label: Short label for the log message, e.g.
            ``"update_template"`` or ``"update_transfer_template"``.
        log_id: The mutating template id, used in the log message.
        submitted: Version counter the form payload carried.
        current: Version counter on the row right now.  The two
            differ exactly when a concurrent edit has landed.
        flash_message: Fully-formed flash string.  Callers compose
            it via :data:`STALE_EDITING_MESSAGE` substituting the
            route's domain noun.
        redirect_endpoint: Flask endpoint to redirect the user to
            (typically the edit form so they can re-load).
        redirect_endpoint_kwargs: Kwargs for ``url_for``.

    Returns:
        A Flask redirect :class:`Response`.  The caller returns it
        directly so the route's control flow is identical to the
        pre-extraction shape.
    """
    logger.info(
        "Stale-form conflict on %s id=%d "
        "(submitted=%d, current=%d)",
        log_label, log_id, submitted, current,
    )
    flash(flash_message, "warning")
    return redirect(url_for(
        redirect_endpoint, **(redirect_endpoint_kwargs or {}),
    ))


def handle_recurrence_conflict(
    *,
    logger: logging.Logger,
    log_label: str,
    log_id: int,
    conflict: RecurrenceConflict,
) -> None:
    """Auto-keep-overrides Phase-1 advisory handler (F-26).

    Called from inside an ``except RecurrenceConflict as conflict:``
    block where the regenerate-for-template call surfaced
    overridden / deleted transactions that Phase-1 chooses to
    keep as-is.  Logs the override / delete counts and flashes the
    canonical advisory notice.  Returns ``None`` -- the caller
    continues executing (the helper is advisory, not control-flow),
    exactly the pre-extraction behaviour.

    Args:
        logger: Per-module logger; see :func:`handle_stale_conflict`
            for the originate-at-the-route-module rationale.
        log_label: Full prefix for the log message preserved
            verbatim from the pre-extraction wording -- e.g.
            ``"Recurrence conflict for template"`` (templates side)
            or ``"Transfer recurrence conflict for template"``
            (transfers side).  Accepting the prefix verbatim keeps
            log-grep patterns valid post-extraction.
        log_id: The template id whose regeneration surfaced the
            conflict.
        conflict: The :class:`RecurrenceConflict` instance the
            caller caught; only ``overridden`` and ``deleted`` are
            read.

    Returns:
        ``None``.  Returning ``None`` (not a :class:`Response`) is
        load-bearing: if the helper returned a Response and the
        caller did ``return helper(...)``, the route would early-
        exit before the commit attempt, dropping every other field
        change in the update payload.  The pre-extraction body did
        not return; the helper does not either.
    """
    logger.warning(
        "%s %d: %d overridden, %d deleted",
        log_label, log_id,
        len(conflict.overridden), len(conflict.deleted),
    )
    flash(
        _RECURRENCE_CONFLICT_FLASH.format(
            overridden_count=len(conflict.overridden),
            deleted_count=len(conflict.deleted),
        ),
        "warning",
    )


__all__ = [
    "STALE_EDITING_MESSAGE",
    "STALE_ACTION_MESSAGE",
    "build_recurrence_rule_from_form",
    "update_recurrence_rule_from_form",
    "resolve_recurrence_rule_for_update",
    "commit_or_handle_stale",
    "handle_stale_conflict",
    "handle_stale_form_conflict",
    "handle_recurrence_conflict",
]
