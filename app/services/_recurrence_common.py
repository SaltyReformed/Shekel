"""
Shekel Budget App -- Shared Recurrence-Engine Helpers

Centralises the logic the transaction recurrence engine
(``app/services/recurrence_engine.py``) and the transfer recurrence
engine (``app/services/transfer_recurrence.py``) ran in byte-identical
form before each generated its own model-specific rows.  The two
engines are deliberate parallels (Transaction vs Transfer), so every
block that does not actually touch the model belongs here, in one
place, where the two cannot drift:

  - the cross-user ownership defense (:func:`check_scenario_ownership`),
  - the per-period skip predicate (:func:`should_skip_period`),
  - the regenerate row-partition (:func:`partition_regeneration_rows`),
  - the regenerate row fetch (:func:`query_rows_from_effective_date`),
  - the cross-user audit ``log_event(...)`` blocks (the ``log_*`` helpers
    below).

The model-specific halves -- constructing a ``Transaction`` vs routing
a ``Transfer`` through ``transfer_service`` for shadow atomicity -- stay
in their respective engines.  The pattern-matching preamble that needs
``recurrence_engine.match_periods`` lives there too
(``resolve_generation_plan``), since hoisting it here would create an
import cycle.

Keeping the audit-trail event names, message strings, and keyword
fields in one place is load-bearing for two reasons:

  1. **Forensic coherence.**  The Phase-6 audit's cross-user defense
     evidence trail depends on both engines emitting structurally
     identical events when an IDOR probe is blocked.  Drift between the
     two engines would show up as a missing or differently-shaped
     event in the SOC's alerting pipeline, which is exactly the
     regression class the testing-standards "Zero Tolerance" rule is
     designed to prevent.
  2. **DRY.**  Pylint R0801 flagged the duplicate blocks (one VERBATIM,
     one near-verbatim with only the literal message differing); a
     fix in one copy that forgets the other is the bug class the
     ``c-38-followups`` document calls out under Issue 1.

These helpers are deliberately thin wrappers around
``app.utils.log_events.log_event`` -- they exist to lock in the
event constant, category, and keyword shape, not to add behaviour.
"""

import logging

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.utils.log_events import (
    ACCESS,
    BUSINESS,
    EVT_ACCESS_DENIED_CROSS_USER,
    EVT_CROSS_USER_BLOCKED,
    log_event,
)


def log_template_cross_user_blocked(
    logger: logging.Logger,
    *,
    message: str,
    template_id: int,
    template_user_id: int,
    scenario_id: int,
) -> None:
    """Emit ``EVT_CROSS_USER_BLOCKED`` for a template/scenario mismatch.

    The transaction and transfer recurrence engines both run a
    defense-in-depth ownership check at the top of ``generate_for_template``
    and ``regenerate_for_template``: the scenario being targeted must
    belong to the same user as the template.  A mismatch indicates a
    route-layer hole or an IDOR probe and is logged at WARNING for
    SOC alerting.

    Each of the four historical call sites (two per engine) shares the
    same keyword shape but used a slightly different human-readable
    message; ``message`` is parameterised so the engines can keep
    distinguishing the *generate* vs *regenerate* paths in log output.

    Args:
        logger: Caller's module-level logger.
        message: Human-readable description.  Pass a literal so the
            ``generate_for_template`` and ``regenerate_for_template``
            paths remain distinguishable in log search.
        template_id: The (Transaction|Transfer)Template primary key
            whose ownership did not match the scenario's owner.
        template_user_id: The template's owning user id (the value
            that should have matched the scenario's user).
        scenario_id: The Scenario primary key whose owner did not
            match the template's owner.
    """
    log_event(
        logger, logging.WARNING, EVT_CROSS_USER_BLOCKED, BUSINESS,
        message,
        template_id=template_id,
        template_user_id=template_user_id,
        scenario_id=scenario_id,
    )


def log_resource_access_denied(
    logger: logging.Logger,
    *,
    user_id: int,
    model: str,
    pk: int,
    owner_id: int,
) -> None:
    """Emit ``EVT_ACCESS_DENIED_CROSS_USER`` for a row ownership violation.

    Used by both recurrence engines' ``resolve_conflicts`` paths when a
    row id arrives whose owner does not match the requesting user.  The
    event is part of the F-144 access-denied evidence trail and is
    expected to be SOC-alertable, so the keyword shape is fixed.

    Args:
        logger: Caller's module-level logger.
        user_id: The requesting user (NOT the row owner).
        model: Display name of the model that was probed -- pass
            ``"Transaction"`` or ``"Transfer"`` so the SOC can group
            events by resource family.
        pk: The primary key of the row whose ownership failed the check.
        owner_id: The actual owner of the row (the user the requester
            tried to access across).
    """
    log_event(
        logger, logging.WARNING,
        EVT_ACCESS_DENIED_CROSS_USER, ACCESS,
        "Cross-user resource access blocked",
        user_id=user_id,
        model=model,
        pk=pk,
        owner_id=owner_id,
    )


def check_scenario_ownership(
    logger: logging.Logger,
    template,
    scenario_id: int,
    *,
    block_message: str,
) -> bool:
    """Verify the target scenario belongs to the template's owner.

    Defense-in-depth ownership check run at the top of both recurrence
    engines' ``generate_for_template`` and ``regenerate_for_template``.
    The route layer already enforces this, but a mismatch here would
    silently write rows into another user's scenario (IDOR).  On a
    mismatch the block is logged at WARNING for SOC alerting via
    :func:`log_template_cross_user_blocked` and the caller aborts.

    Args:
        logger: The calling engine's module logger, so the emitted
            event is attributed to ``app.services.recurrence_engine`` or
            ``app.services.transfer_recurrence`` exactly as before.
        template: The (Transaction|Transfer)Template being generated.
        scenario_id: The scenario primary key to write into.
        block_message: Human-readable description distinguishing the
            generate vs regenerate path in log output.

    Returns:
        True when the scenario exists and is owned by the template's
        user; False (after logging the block) otherwise.
    """
    scenario = db.session.get(Scenario, scenario_id)
    if scenario is None or scenario.user_id != template.user_id:
        log_template_cross_user_blocked(
            logger,
            message=block_message,
            template_id=template.id,
            template_user_id=template.user_id,
            scenario_id=scenario_id,
        )
        return False
    return True


def should_skip_period(existing_rows: list) -> bool:
    """Return True if an existing row in a period blocks (re)generation.

    Both recurrence engines refuse to auto-generate into a period that
    already holds any template-linked row, regardless of the row's
    state.  The per-state checks below are kept explicit -- rather than
    collapsed to ``bool(existing_rows)`` -- so the WHY of each skip
    survives in one place and a future divergence (e.g. choosing to
    regenerate over a soft-deleted row) is a localized edit, not a
    rewrite:

      - immutable (historical/settled): never touched.
      - is_override: the user made a deliberate change; preserve it.
      - is_deleted: the user intentionally removed it; do not resurrect.
      - otherwise: an auto-generated, unmodified row already exists.

    Args:
        existing_rows: The existing (Transaction|Transfer) rows already
            present in the period for this template and scenario.

    Returns:
        True when the period already has a row and must be skipped;
        False when the period is empty and generation may proceed.
    """
    for row in existing_rows:
        # Never touch immutable (historical) rows.
        if row.status and row.status.is_immutable:
            return True
        # Skip overridden rows -- the user made a deliberate change.
        if row.is_override:
            return True
        # Skip soft-deleted rows -- the user intentionally removed it.
        if row.is_deleted:
            return True
        # Auto-generated and unmodified -- it already exists, skip.
        return True
    return False


def partition_regeneration_rows(existing_rows: list) -> tuple[list, list, list]:
    """Partition existing rows for the regenerate state machine.

    Shared by both recurrence engines' ``regenerate_for_template``: an
    existing template-linked row is classified per §4.8 as either a
    conflict to surface to the user (overridden or soft-deleted), an
    immutable row to leave untouched, or an auto-generated row that is
    safe to delete and regenerate.

    Args:
        existing_rows: All existing (Transaction|Transfer) rows whose
            pay period ends on or after the regeneration's effective
            date.

    Returns:
        A 3-tuple ``(overridden_ids, deleted_ids, to_delete)``: the
        first two are lists of row IDs (conflicts to report to the
        user); the third is the list of row objects safe to delete and
        regenerate.
    """
    overridden_ids = []
    deleted_ids = []
    to_delete = []
    for row in existing_rows:
        # Immutable -- never touch.
        if row.status and row.status.is_immutable:
            continue
        # Overridden -- flag as conflict for user prompt.
        if row.is_override:
            overridden_ids.append(row.id)
            continue
        # Soft-deleted -- flag as conflict for user prompt.
        if row.is_deleted:
            deleted_ids.append(row.id)
            continue
        # Auto-generated, unmodified -- safe to delete and regenerate.
        to_delete.append(row)
    return overridden_ids, deleted_ids, to_delete


def query_rows_from_effective_date(
    model,
    template_fk_col,
    template_id: int,
    scenario_id: int,
    effective_from,
) -> list:
    """Fetch template-linked rows in periods ending on or after a date.

    Shared by both recurrence engines' ``regenerate_for_template`` to
    collect the rows eligible for the delete-and-regenerate sweep.  The
    only per-engine differences are the model class and the template
    foreign-key column, so both are parameters.

    Args:
        model: The mapped class to query (``Transaction`` or
            ``Transfer``).
        template_fk_col: The model's template foreign-key column object
            (``Transaction.template_id`` or
            ``Transfer.transfer_template_id``).
        template_id: The template primary key to match.
        scenario_id: The scenario primary key to match.
        effective_from: Only rows whose pay period ends on or after this
            date are returned, so the current period is included when
            the date falls mid-period.

    Returns:
        A list of matching model instances, including soft-deleted and
        immutable rows -- the caller partitions them via
        :func:`partition_regeneration_rows`.
    """
    return (
        db.session.query(model)
        .join(PayPeriod, model.pay_period_id == PayPeriod.id)
        .filter(
            template_fk_col == template_id,
            model.scenario_id == scenario_id,
            PayPeriod.end_date >= effective_from,
        )
        .all()
    )
