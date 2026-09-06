"""
Shekel Budget App -- Recurrence Engine: ONE regeneration pass, for both engines.

:func:`regenerate_definition` -- what "bring this definition's rows into line
with its current definition" MEANS -- and the record each engine states to say
which five acts are its own (:class:`MaintainActs`).

**This module exists because plan step balance:X-au-d deleted the last
difference between the two engines' regenerate paths.**  The transaction engine
threaded a salary profile and a calendar through its own copy so that
generation could price a paycheck; a salary row is DECLARED now and generation
prices nothing, after which the two bodies were the same code twice --
``pylint``'s ``duplicate-code`` reported both the maintain body and the
generate loop the moment the thread came out.  ``CLAUDE.md`` rule 14 is a rule
about VALUES, and this is the same rule one tier up: one behaviour, one home.

**IT DELETED A FENCE.**  ``transfer_recurrence.regenerate_for_template``
carried a documented ``# pylint: disable=duplicate-code`` over its audit-log
and conflict-raise tail, whose rationale recorded that extracting a shared log
helper had been TRIED and REVERTED (one parameter per ``log_event`` field
trips ``too-many-arguments``, and the call site re-duplicates the kwargs).
That attempt failed because it tried to share the LOGGING; what actually
differs between the two engines is three strings and a logger, which
:class:`PassReporting` carries as one value.  With the whole pass shared there
is no duplication left to disable, so the disable is gone rather than widened.

**Why it lives in this package rather than in ``_recurrence_common``.**  The
shared pass resolves the generation plan (:func:`._plan.resolve_generation_plan`),
which lives here; a module under ``_recurrence_common`` reaching for it would
close an import cycle, and reaching for it through a private module of another
package is what ``shekel-private-module-import`` forbids.  The transfer engine
already imports ``resolve_generation_plan`` and ``compute_due_date`` from this
package and its own docstring calls itself "a deliberate parallel" of it, so
the direction is established rather than new.  ``_recurrence_common`` keeps
what is genuinely model-agnostic and free of a plan -- the classification, the
row fetches, the ownership checks -- and was at 870 of its 1,000-line ceiling
with this family in it.

Boundary discipline (``CLAUDE.md`` Architecture / B6-01): ORM rows and plain
data in, plain data out; no Flask import.  It FLUSHES (both engines' writers
do), and it does not commit.
"""

import logging
from typing import NamedTuple

from app.exceptions import RecurrenceConflict
from app.extensions import db
from app.services._recurrence_common import (
    MaintainOutcome,
    check_scenario_ownership,
    classify_maintain_work,
    occurrences_to_write,
    rows_this_pass_may_maintain,
)
from app.services.recurrence_engine._plan import resolve_generation_plan
from app.utils.log_events import BUSINESS, log_event


class PassReporting(NamedTuple):
    """How ONE engine's regeneration announces itself.

    The three strings and the logger that are all a regeneration's audit trail
    still differs by, held as one value so :class:`MaintainActs` stays inside
    the seven-attribute ceiling and so "which engine is speaking" is one fact
    rather than four parameters threaded side by side.

    Attributes:
        logger: The ENGINE's own logger.  It is per-engine rather than this
            module's deliberately: the structured-log controls capture by
            logger NAME (``tests/test_services/test_service_log_events.py``),
            so a shared logger would file both engines' events under a module
            no reader associates with either.
        block_message: What a cross-user attempt is logged as
            (:func:`~app.services._recurrence_common.check_scenario_ownership`).
        event: The ``EVT_*`` constant this engine's regeneration emits.
        event_message: The human sentence beside it.
    """

    logger: object
    block_message: str
    event: str
    event_message: str


class MaintainActs(NamedTuple):
    """The five acts of a regeneration that belong to ONE engine.

    **The seam that made the regenerate path one function instead of two**
    (plan step **balance:X-au-d**).  Everything a regeneration DECIDES was
    shared already -- which rows the rule still names, which the owner has a
    hold on, how the derivation is keyed, what the outcome reports -- and the
    last thing keeping the two bodies apart was the salary thread this module's
    docstring names.

    Each engine states these ONCE, at module level, and
    :func:`regenerate_definition` performs them.  They are the places the two
    engines genuinely differ -- a transaction has one account and a transfer
    has two, a transaction is written alone and a transfer with its shadow
    pair -- so a SIXTH act appearing here is the signal that something
    model-shaped has been pushed into the shared body.

    Attributes:
        reporting: This engine's :class:`PassReporting` -- WHO is performing
            the pass.  First because it names the engine, so a reader meets the
            identity before the five acts it carries out.
        selector_for: ``(template, scenario_id) -> TemplateRowSelector`` --
            which table and which template-FK this engine's fetches ask about.
        derive_for: ``(template, rule, period) -> <derived fields>`` -- the ONE
            statement of what a generated row takes from its definition.
        owner_records: ``(existing) -> set[int]`` -- the rows carrying the
            owner's own records, which is a question about this engine's tables.
        reattributed: ``(existing, template) -> set[int]`` -- the rows whose
            ACCOUNTS the definition has moved; one account for a transaction, a
            pair for a transfer.
        write: ``(work, derived, template, scenario_id, projected_id) ->
            (created, updated)`` -- the only writer in the maintain path.
    """

    reporting: PassReporting
    selector_for: object
    derive_for: object
    owner_records: object
    reattributed: object
    write: object


def derived_by_occurrence(plan, derive_for) -> dict:
    """Return ``{occurs_on: <what the definition derives>}`` for *plan*.

    **The occurrence-keyed derivation both engines index their write step by**,
    and one of the two halves of a maintain pass's read
    (:func:`~app.services._recurrence_common.classify_maintain_work` is the
    other).

    **KEYED BY THE OCCURRENCE, NOT BY THE PAY PERIOD**, and that is the reason
    worth having once.  Keying it by period was a defect an adversarial review
    found: ``classify_maintain_work`` routes a row to ``update`` when its
    ``occurs_on`` is named, and the row may sit in a period the rule does NOT
    name (the owner moved it, then cleared the override through the conflict
    chooser).  This map holds only named OCCURRENCES, so
    ``derived[row.pay_period_id]`` raised ``KeyError`` -- a 500 -- and where the
    landing period happened to be named it silently re-derived the row's amount
    and due date from the WRONG paycheck.

    It also collapsed a repeated paycheck: two placements sharing one period
    kept only the last one's fields, so at a pay cadence of 30 days or more
    both installments took the same date and figure.  One occurrence is one
    entry, so neither is reachable.

    Args:
        plan: The pass's :class:`~._plan.GenerationPlan`, or ``None`` for a
            CLEARED recurrence -- which names no occurrence, so the map is
            empty and every existing row is considered for retirement.
        derive_for: Called as ``derive_for(rule, period)``.  WHAT a generated
            row derives is the one thing the two engines genuinely do not
            share: a transaction takes a category and a type, a transfer takes
            two accounts.

    Returns:
        ``{occurs_on: <the engine's derived-fields value>}``, empty for a
        cleared recurrence.
    """
    if plan is None:
        return {}
    return {
        placement.occurrence: derive_for(plan.rule, placement.period)
        for placement in plan.placements
    }


def create_for_unclaimed_occurrences(selector, plan, build) -> list:
    """Create one row per occurrence of *plan* that nothing already answers.

    **The generate path's whole write loop, shared by both engines** for the
    reason this module's docstring states.
    :func:`~app.services._recurrence_common.occurrences_to_write` already owned
    the DECISION -- which occurrences still need a row -- and each engine
    wrapped it in an identical loop that differed only in the row it built.

    Args:
        selector: This pass's
            :class:`~app.services._recurrence_common.TemplateRowSelector`.
        plan: The pass's :class:`~._plan.GenerationPlan`.  Never ``None``: both
            callers return early on a plan that resolved to nothing, and
            "generate into no plan" is not a state this can answer.
        build: The engine's own row builder, called as
            ``build(period, occurrence)`` and returning the row it created.
            Both engines add to the session inside it, so what comes back is
            what this returns.

    Returns:
        The rows *build* created, in occurrence order.
    """
    return [
        build(placement.period, placement.occurrence)
        for placement in occurrences_to_write(selector, plan.placements)
    ]


def _maintain(acts: MaintainActs, template, scenario_id, plan, existing):
    """Resolve and apply everything one regeneration does to a definition's rows.

    Three steps: derive what the definition says for every occurrence the rule
    names, classify each existing row against that, then write.  Private
    because it is :func:`regenerate_definition`'s middle act rather than a door
    -- a caller that ran it without the ownership check above it would maintain
    another owner's rows, and one that ran it without the conflict raise below
    it would report success over rows it had refused to touch.

    Args:
        acts: The engine's own acts (:class:`MaintainActs`).
        template: The updated definition.
        scenario_id: The scenario being maintained.
        plan: The pass's :class:`~._plan.GenerationPlan`, or ``None`` for a
            cleared recurrence.
        existing: Every row of this definition in the pass's WRITE WINDOW at or
            after its bound.  The window half is the load-bearing one: it is
            what keeps this domain a superset of the plan's, and so what makes
            the RETIRE branch reachable.

    Returns:
        The :class:`~app.services._recurrence_common.MaintainOutcome`.
    """
    work = classify_maintain_work(
        acts.selector_for(template, scenario_id), existing,
        plan.placements if plan is not None else (),
        with_records=acts.owner_records(existing),
        reattributed=acts.reattributed(existing, template),
    )
    created, updated = acts.write(
        work,
        derived_by_occurrence(
            plan, lambda rule, period: acts.derive_for(template, rule, period),
        ),
        template, scenario_id,
        plan.projected_id if plan is not None else None,
    )
    return MaintainOutcome.after(work, created, updated)


def regenerate_definition(
    acts: MaintainActs, template, schedule, scenario_id, effective_from=None,
):
    """Bring a definition's future rows into line with its current definition.

    **ONE body for both engines** (plan step **balance:X-au-d**); see
    :class:`MaintainActs` for what each of them still supplies and why that
    list is exactly six long.  Ruling **R-R19** and plan step R10-a are what it
    performs: the rows the rule still names are MAINTAINED rather than
    destroyed and rebuilt, which is what stops a rename taking an envelope's
    purchases with it (finding **N-292**).

    Three outcomes, one per occurrence the pass considers:

      1. the rule names the occurrence and an auto-generated row answers it --
         the row is UPDATED in place from the definition's derived fields;
      2. the rule names it and nothing answers it -- a row is created;
      3. the rule NO LONGER names it -- the row is removed if it is empty, and
         RETAINED as a conflict if the owner has records against it.

    Overridden and soft-deleted rows are conflicts wherever they sit; immutable
    rows are never touched
    (:func:`~app.services._recurrence_common.classify_maintain_work`).

    **The ownership check and the plan resolution both pass a block message,
    and the duplication is deliberate**: the first answers "is this your
    scenario" and the second "does this rule still fire", and a ``None`` plan
    would otherwise mean either.  Asking ownership first leaves the plan's
    ``None`` meaning exactly one thing -- a CLEARED recurrence, which names no
    occurrence and so considers every existing row for retirement.

    Args:
        acts: The engine's own acts (:class:`MaintainActs`).
        template: The updated definition -- a ``TransactionTemplate`` or a
            ``TransferTemplate``.
        schedule: The owner's
            :class:`~app.services.generation_schedule.GenerationSchedule` --
            their whole pay-period schedule plus the window this pass may write
            into.
        scenario_id: The scenario being maintained.
        effective_from: Only rows whose pay period ends on or after this date
            are considered.  ``None`` applies no lower bound.

    Returns:
        The rows this pass CREATED.  Rows it updated are not in it, so the
        value keeps the meaning every caller already reads it with.

    Raises:
        RecurrenceConflict: When rows exist that this pass must not change
            unasked.  The caller should catch it, present the options, and call
            its engine's ``resolve_conflicts``.
    """
    if not check_scenario_ownership(
        acts.reporting.logger, template, scenario_id,
        block_message=acts.reporting.block_message,
    ):
        return []

    plan = resolve_generation_plan(
        template, schedule, scenario_id, effective_from,
        block_message=acts.reporting.block_message,
    )
    # The two reads take the SAME bound and the same window, and the second's
    # answer is a SUPERSET of the first's -- it is the window, where the plan is
    # the window intersected with the occurrences the rule names.  That is what
    # makes the RETIRE branch reachable at all.  Equal, not strictly wider, when
    # the rule names every period of the window, which is the ordinary case.
    existing = rows_this_pass_may_maintain(
        acts.selector_for(template, scenario_id), schedule, effective_from,
    )

    outcome = _maintain(acts, template, scenario_id, plan, existing)
    db.session.flush()

    # ONE event per pass.  It gained ``updated_count`` and
    # ``retained_conflict_count`` at plan step R10-a while
    # ``EVT_*_GENERATED`` left -- this used to delegate its create half to the
    # engine's ``generate_for_template``, and the maintain pass creates rows
    # itself now.  ``deleted_count`` counts only rows the rule STOPPED naming;
    # under the pre-R10-a shape it counted every non-overridden row in the
    # window and its twin ``created_count`` counted the same rows again, so a
    # reader comparing forensics across that step must not treat the two as the
    # same number.
    log_event(
        acts.reporting.logger, logging.INFO, acts.reporting.event, BUSINESS,
        acts.reporting.event_message,
        user_id=template.user_id,
        template_id=template.id,
        scenario_id=scenario_id,
        updated_count=len(outcome.updated),
        deleted_count=len(outcome.removed),
        created_count=len(outcome.created),
        overridden_conflict_count=len(outcome.overridden_ids),
        deleted_conflict_count=len(outcome.deleted_ids),
        retained_conflict_count=len(outcome.retained_ids),
    )

    if outcome.overridden_ids or outcome.deleted_ids or outcome.retained_ids:
        raise RecurrenceConflict(
            overridden=outcome.overridden_ids,
            deleted=outcome.deleted_ids,
            retained=outcome.retained_ids,
        )

    return outcome.created
