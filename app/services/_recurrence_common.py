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
  - the generate row fetch + repeat refusal
    (:func:`existing_rows_refusing_repeats`, over
    :func:`existing_rows_by_period` and :func:`refuse_unstorable_repeats`),
  - the regenerate row-partition (:func:`partition_regeneration_rows`) --
    **the TRANSFER engine's only, since plan step R10-a**; see its docstring,
  - the regenerate sweep bound (:func:`regeneration_bound`),
  - the regenerate row fetch (:func:`query_rows_from_effective_date`),
  - the cross-user audit ``log_event(...)`` blocks (the ``log_*`` helpers
    below).

What both engines TAKE rather than share -- the owner's pay-period schedule
and the window one pass writes into -- is
:class:`~app.services.generation_schedule.GenerationSchedule`, and it lives in
its own public module because the route layer constructs it too.  This module
is package-private (``shekel-private-module-import``), so a type a route must
name cannot live here.

The model-specific halves -- constructing a ``Transaction`` vs routing
a ``Transfer`` through ``transfer_service`` for shadow atomicity -- stay
in their respective engines.  The occurrence-matching preamble
(``resolve_generation_plan``) lives in ``recurrence_engine`` too, since
hoisting it here would create an import cycle -- the transfer engine imports
that module for it.

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
from collections import defaultdict

from app.exceptions import RecurrenceCadenceUnsupported
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


def refuse_unstorable_repeats(template, placements, existing) -> None:
    """Refuse when one paycheck must host this template's row more than once.

    Shared by both engines' ``generate_for_template``, because the storage
    limit is a property of the two UNIQUE indexes rather than of either model:
    ``idx_transactions_template_period_scenario`` and
    ``idx_transfers_template_period_scenario`` are both keyed on
    ``(template, pay_period, scenario)``.

    **Plan step R4a made this reachable, which is why it exists here rather
    than at R4b with the rest of the generation cutover.**  The reverse matcher
    it replaced deduplicated: ``_match_monthly_first`` kept one period per
    calendar month and ``_match_monthly`` one per ``(year, month)``, so a
    repeat could only come from the ONE shape whose two endpoint months
    collided.  Forward generation emits every occurrence the cadence names, so
    at a cadence of 30 days or more a monthly bill repeats a paycheck as a
    matter of course.  Measured against the deleted matcher, 12 periods from
    2026-01-01: ``Monthly First`` returned no repeat at ANY cadence and now
    returns 11 repeated periods at 90 days.  Letting those reach the flush
    turns a silent under-generation into an ``IntegrityError`` that rolls back
    the whole enclosing transaction -- ``pay_period_admin.extend_pay_periods``
    among them, which would leave the owner unable to extend their schedule at
    all.

    **Checked AFTER the per-period skip, and that is load-bearing.**  A
    paycheck that already holds a row for this template is skipped by
    :func:`should_skip_period`, so no second row is attempted and there is
    nothing to refuse; testing before the skip would make an already-populated
    schedule permanently unextendable.  Checked BEFORE any row is created, so
    the refusal never leaves a half-written pass behind.

    **It names the occurrence DATES since plan step R4b-2**, which is when
    generation started carrying them.  At R4a the engines answered in PERIODS
    and discarded the occurrence, so the refusal could state only how MANY
    times a definition fell inside the paycheck -- the developer's ruling asked
    for the dates, and naming them would have meant walking the cadence a
    second time.  ``resolve_generation_plan`` now hands over
    ``(occurrence, period)`` pairs, so the dates come from the same walk that
    found the collision.

    Args:
        template: The (Transaction|Transfer)Template being generated.
        placements: The occurrences the rule fires on inside this pass's write
            window (``recurrence_engine.PlannedOccurrence`` values, one per
            occurrence), whose ``period`` may therefore repeat.
        existing: ``{pay_period_id: [row, ...]}`` for this template and
            scenario, as :func:`existing_rows_by_period` returns it.

    Raises:
        RecurrenceCadenceUnsupported: When a period this pass would WRITE into
            appears more than once.  Names the template, the paycheck and
            every occurrence date that lands in it.
    """
    seen: dict[int, list] = {}
    for placement in placements:
        seen.setdefault(placement.period.id, []).append(placement)
    for period_id, repeats in seen.items():
        if len(repeats) < 2 or should_skip_period(existing.get(period_id, [])):
            continue
        period = repeats[0].period
        raise RecurrenceCadenceUnsupported(
            template_name=template.name,
            occurrence_dates=[repeat.occurrence for repeat in repeats],
            period_start=period.start_date,
            period_end=period.end_date,
        )


def regeneration_bound(schedule, effective_from):
    """Return the date a regenerate pass sweeps and rewrites from.

    Shared by both engines' ``regenerate_for_template`` because it is one
    decision, not two that happen to agree: the DELETE sweep is an SQL bound on
    ``pay_periods.end_date``, so "no lower bound" has to become a concrete date
    -- and it must be the same date the regeneration writes within.

    **The date is the WRITE WINDOW's opening, not the schedule's.**  Taking the
    schedule's opening instead would delete every non-override template row
    from the owner's first payday forward while regenerating only inside the
    window, destroying rows nothing would recreate.  No route reaches that
    today (both callers pass a whole-schedule window, where the two coincide),
    which is why the asymmetry is closed here rather than left to be found with
    a narrow window (plan step R4b-1, adversarial review).

    Args:
        schedule: The pass's
            :class:`~app.services.generation_schedule.GenerationSchedule`.
        effective_from: The caller's explicit bound, or ``None``.

    Returns:
        *effective_from* when the caller stated one; else the earliest payday
        in the write window; else ``None``, for a window with no periods at
        all, where the sweep has nothing to bound and nothing to delete.
    """
    if effective_from is not None:
        return effective_from
    if not schedule.write_periods:
        return None
    return min(
        period.start_date for period in schedule.write_periods.values()
    )


def partition_regeneration_rows(existing_rows: list) -> tuple[list, list, list]:
    """Partition existing rows for the DELETE-and-recreate regenerate machine.

    An existing template-linked row is classified per §4.8 as either a conflict
    to surface to the user (overridden or soft-deleted), an immutable row to
    leave untouched, or an auto-generated row that is safe to delete and
    regenerate.

    **The TRANSFER engine is its only caller since plan step R10-a.**  It was
    shared by both until that step (ruling **R-R19**) gave the transaction
    engine a pass that MAINTAINS the rows it already generated -- the last
    class in the tuple below, "safe to delete and regenerate", is the premise
    that step deleted, because ``transaction_entries`` CASCADE and a projected
    envelope holds the owner's purchases.  A transfer holds none, which is why
    the transfer engine still reads this and why moving it onto the same shape
    is plan step R10-b rather than an emergency.  **Do not extend this for the
    transaction engine**; the classifier that replaced it is
    ``recurrence_engine._maintain._classify_maintain_work``.

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
        # Immutable -- never touch.  **The Build-Order Step 3 note that stood
        # here rested on a premise plan step X-f3b DELETED** (ruling **R-FM**):
        # "a settled row is immutable, and every posted row is settled, so
        # neither the regenerate sweep nor ``resolve_conflicts`` ever touches a
        # row with ledger postings".  A PROJECTED envelope holds postings the
        # moment one of its purchases carries a recorded bank posting day, and
        # both of that sentence's consumers reach exactly such rows -- so both
        # now reconcile: the sweep reverses each row's family before deleting it
        # (``recurrence_engine.regenerate_for_template``) and the conflict
        # chooser re-syncs each row it restores (``resolve_conflicts``).  What
        # this skip still guarantees is only what it says: a settled row is
        # untouched here.
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


def existing_rows_refusing_repeats(
    model, template_fk_col, template, scenario_id, placements,
) -> dict[int, list]:
    """Fetch what is already in this pass's periods, refusing an unstorable pass.

    The two steps every generate pass runs between resolving its plan and
    writing its first row, in one call because their ORDER is load-bearing and
    was previously upheld by convention in two engines: the repeat refusal
    consults the fetched rows (a paycheck that already holds a row for this
    template is SKIPPED, so there is no second row to refuse), and it must run
    before any row is created so a refusal never leaves half a pass behind.
    Fusing them makes both facts unbreakable rather than remembered.

    Plan step R4b-2 hoisted this: reshaping the plan onto ``(occurrence,
    period)`` pairs made the two engines' copies of the block identical enough
    for pylint's ``duplicate-code`` to see what a reader always could.

    Args:
        model: The mapped class to query (``Transaction`` or ``Transfer``).
        template_fk_col: That model's template foreign-key column object.
        template: The (Transaction|Transfer)Template being generated -- read
            for its id here and for its NAME by the refusal.
        scenario_id: The scenario primary key to match.
        placements: This pass's ``recurrence_engine.PlannedOccurrence`` values,
            one per occurrence, whose ``period`` may therefore repeat.

    Returns:
        ``{pay_period_id: [row, ...]}``, as :func:`existing_rows_by_period`
        returns it.

    Raises:
        RecurrenceCadenceUnsupported: See :func:`refuse_unstorable_repeats`.
    """
    existing = existing_rows_by_period(
        model, template_fk_col, template.id, scenario_id,
        [placement.period.id for placement in placements],
    )
    refuse_unstorable_repeats(template, placements, existing)
    return existing


def existing_rows_by_period(
    model,
    template_fk_col,
    template_id: int,
    scenario_id: int,
    period_ids,
) -> dict[int, list]:
    """Group this template's existing rows in *period_ids* by pay period.

    Shared by both recurrence engines' ``generate_for_template``, which each
    carried a byte-similar copy of it until plan step R4b-2 -- the exact
    duplication this module exists to hold, and the parameterisation is the one
    :func:`query_rows_from_effective_date` already established for the sibling
    query: the model class and the template foreign-key column, and nothing
    else, differ between the two engines.

    Fetches EVERY row, including soft-deleted and immutable ones, because the
    caller's skip predicate (:func:`should_skip_period`) treats any existing row
    as "do not generate": a row per period would let a soft-deleted row hide
    behind a live one, so the value is a LIST per period rather than a row.

    Takes pay-period IDS rather than rows because ids are all it ever read, and
    since plan step R4b-2 the generate path holds ``(occurrence, period)`` pairs
    rather than a period list -- so asking for rows would make every caller
    unwrap one shape into another for a value this query reduces to ids anyway.
    A repeated id is harmless: ``IN`` is a set test, and a paycheck a rule names
    twice is refused by :func:`refuse_unstorable_repeats` before any row is
    written.

    Args:
        model: The mapped class to query (``Transaction`` or ``Transfer``).
        template_fk_col: That model's template foreign-key column object
            (``Transaction.template_id`` or ``Transfer.transfer_template_id``).
        template_id: The template primary key to match.
        scenario_id: The scenario primary key to match.
        period_ids: The ``budget.pay_periods.id`` values to look in.  Empty
            short-circuits without a query.

    Returns:
        ``{pay_period_id: [row, ...]}``, absent for a period holding no row.
    """
    ids = list(period_ids)
    if not ids:
        return {}
    rows = (
        db.session.query(model)
        .filter(
            template_fk_col == template_id,
            model.scenario_id == scenario_id,
            model.pay_period_id.in_(ids),
        )
        .all()
    )
    grouped: dict[int, list] = defaultdict(list)
    for row in rows:
        grouped[row.pay_period_id].append(row)
    # A plain dict, not the defaultdict: the documented contract is that a
    # period holding no row is ABSENT, and a defaultdict would silently create
    # one for the next caller that indexes instead of ``.get``.
    return dict(grouped)


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
