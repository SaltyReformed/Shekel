"""
Shekel Budget App -- Shared Recurrence-Engine Helpers

Centralises the two ``log_event(...)`` blocks that the transaction
recurrence engine (``app/services/recurrence_engine.py``) and the
transfer recurrence engine (``app/services/transfer_recurrence.py``)
emitted in identical form at four (resp. two) sites each.

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
