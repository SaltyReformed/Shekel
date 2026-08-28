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
  - the regenerate pass's whole DECISION -- which rows are the rule's to
    rewrite (:func:`owner_hold_on` / :func:`is_maintainable`), what one pass
    must do to each (:func:`classify_maintain_work` into
    :class:`MaintainWork`), what it then did (:class:`MaintainOutcome`) and the
    unstorable-cadence refusal that guards it
    (:func:`refuse_repeats_this_pass`),
  - the regenerate row fetch (:func:`rows_this_pass_may_maintain`),
  - the ONE statement of the query both fetches run (:func:`_rows_in_periods`),
  - the cross-user audit ``log_event(...)`` blocks (the ``log_*`` helpers
    below).

**``partition_regeneration_rows`` is gone** (plan step R10-b).  It classified a
row for the DELETE-and-recreate regenerate machine -- conflict, immutable, or
"safe to delete and regenerate" -- and that third class was a premise both
engines have now dropped.  Plan step R10-a replaced it on the transaction side
(``transaction_entries`` CASCADE, so deleting a projected envelope destroyed the
owner's purchases); R10-b replaces it on the transfer side, where a delete took
the row's ``notes``, its id and any settlement record it had retained through a
revert.  What stands in its place is :func:`classify_maintain_work`, which both
engines share -- so the shared helper is the decision that MAINTAINS rather than
the one that partitioned them for destruction.

**``regeneration_bound`` is gone** (pay-calendar plan step C2-f3c).  It existed
because the regenerate sweep was an SQL bound on ``pay_periods.end_date``, so
"no lower bound" had to be turned into a concrete date before it could be
compared against a column -- and the date it had to become was the WRITE
WINDOW's opening, or the sweep reached rows the pass would not rewrite.  The
sweep now selects on a period-ID SET taken from the pass's own window, so
``None`` needs no translation and the domain cannot exceed the window in the
first place.  The asymmetry that helper closed by arithmetic is closed by the
shape.

What both engines TAKE rather than share -- the owner's pay calendar
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
from datetime import date
from typing import NamedTuple

from app.exceptions import RecurrenceCadenceUnsupported
from app.extensions import db
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


class TemplateRowSelector(NamedTuple):
    """WHICH rows a recurrence pass is asking about.

    **The one place the two engines' difference is written down.**  A
    transaction pass and a transfer pass run identical queries against
    different tables, so every shared fetch here took the mapped class and the
    template foreign-key column as a pair of arguments beside the template and
    the scenario -- four parameters repeated at six call sites, and enough of
    them to trip ``too-many-arguments`` the moment a fetch needed one more.
    Naming the four as one value says what they are: this pass's subject.

    Built ONCE per pass by each engine, which is also what stops a pass mixing
    one model's column with another's.

    Attributes:
        model: The mapped class to query (``Transaction`` or ``Transfer``).
        template_fk_col: That model's template foreign-key column object
            (``Transaction.template_id`` or ``Transfer.transfer_template_id``).
        template: The (Transaction|Transfer)Template being generated from.
            The whole row rather than its id, because the refusal path reads
            its NAME (:func:`refuse_unstorable_repeats`) and every other reader
            wants ``template.id``; carrying both would be two spellings of one
            fact.
        scenario_id: The scenario primary key every row must match.
    """

    model: type
    template_fk_col: object
    template: object
    scenario_id: int


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
            occurrence), whose ``period`` may therefore repeat.  Each
            placement's ``period`` is a
            :class:`~app.services.pay_calendar.DerivedPeriod` since pay-calendar
            plan step C2-f3c, so the paycheck this refusal NAMES is bounded by
            the derivation rather than by two stored columns.
        existing: ``{pay_period_id: [row, ...]}`` for this template and
            scenario, as :func:`existing_rows_by_period` returns it.

    Raises:
        RecurrenceCadenceUnsupported: When a period this pass would WRITE into
            appears more than once.  Names the template, the paycheck and
            every occurrence date that lands in it.
    """
    seen: dict[int, list] = {}
    for placement in placements:
        seen.setdefault(placement.period.period_id, []).append(placement)
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


# What the owner can own about a generated row, and therefore the three ways a
# row stops being the rule's to rewrite: its PERMANENCE (an immutable status),
# its AMOUNT (``is_override``) and its EXISTENCE (``is_deleted``).  Named
# because :func:`classify_maintain_work` must tell them apart -- each routes to
# a different conflict list -- while :func:`is_maintainable` only asks whether
# there is one.
#
# Both engines' rows carry all three facts under the same names (a status with
# ``is_immutable``, ``is_override``, ``is_deleted``), which is why this reads
# neither model and lives here rather than in either engine (plan step R10-b).
BLOCK_IMMUTABLE = "immutable"
BLOCK_OVERRIDE = "override"
BLOCK_DELETED = "deleted"


def owner_hold_on(row) -> "str | None":
    """Return which owner-held fact stops *row* being the rule's to rewrite.

    **The one statement of the three FOR THE MAINTAIN PASS, so its two readers
    cannot drift.**  The classifier needs to tell them apart (each is a
    different conflict list) and the repeat refusal only needs to know whether
    there is one, so a bare boolean could not serve both, and two copies of the
    chain is what an adversarial review of plan step R10-a actually found here.
    The order is load-bearing: an immutable row is never touched whatever else
    is true of it, which is what keeps a settled row out of every list.

    **It is NOT the only place the three conditions appear, and R10-b moved it
    into the module that holds the other one.**  :func:`should_skip_period`
    spells all three out again a hundred lines up -- deliberately, and its own
    docstring says why: it answers a different question ("does anything here
    block a WRITE"), every branch returns the same answer, and the enumeration
    is commentary on a predicate that degenerates to ``bool(existing_rows)``.
    Nothing is functionally coupled, and an unqualified claim to be "the ONE
    statement of the three" would have been false in its own file, which an
    adversarial review of R10-b caught.

    Args:
        row: The Transaction or Transfer to classify.

    Returns:
        :data:`BLOCK_IMMUTABLE`, :data:`BLOCK_OVERRIDE` or
        :data:`BLOCK_DELETED`, or ``None`` when the row is the rule's own --
        auto-generated, live and still mutable.
    """
    if row.status and row.status.is_immutable:
        return BLOCK_IMMUTABLE
    if row.is_override:
        return BLOCK_OVERRIDE
    if row.is_deleted:
        return BLOCK_DELETED
    return None


def is_maintainable(row) -> bool:
    """Return True when *row* is the RULE's own row, free to be maintained.

    The boolean face of :func:`owner_hold_on`, for the callers that do not care
    WHICH hold applies -- :func:`refuse_repeats_this_pass`, which only needs to
    know whether a row blocks a write.

    Args:
        row: The Transaction or Transfer to classify.

    Returns:
        True when the row is auto-generated, live and still mutable.
    """
    return owner_hold_on(row) is None


class PlacedRow(NamedTuple):
    """A row a pass will CREATE: which paycheck funds it, what it answers.

    The ID-LEVEL twin of
    :class:`~app.services.recurrence_engine._plan.PlannedOccurrence`, for the
    write arms that hold a ``pay_periods.id`` rather than a whole derived
    period.  Both engines' create paths take one, so "which paycheck, which
    occurrence" is one value rather than two arguments that can be paired
    wrongly.

    ``create_in`` held bare ``pay_periods.id`` values until plan step **R17**,
    which is the whole of ledger row **D57**: a generated row now records WHICH
    OCCURRENCE of its template's cadence it answers (``occurs_on``), and the
    create arm cannot state that from a period id alone -- at a pay cadence of
    30 days or more one paycheck can host a definition more than once, so the
    period is not a name for the occurrence and never was.

    **It is a pair rather than a second map beside ``derived``.**  The applier
    already takes ``{period_id: DerivedRowFields}``; threading the occurrence as
    another dict keyed the same way would be two structures that have to agree,
    which is the shape this module exists to remove.  Carrying it on the
    decision itself means the classifier states it once and the applier reads
    it, and neither engine can pair a period with another period's occurrence.

    Attributes:
        period_id: The ``budget.pay_periods.id`` the new row lands in.
        occurs_on: The date this template's cadence names for it -- the value
            :class:`~app.services.recurrence_engine._plan.PlannedOccurrence`
            carries and both write loops discarded before R17.
    """

    period_id: int
    occurs_on: date


class MaintainWork(NamedTuple):
    """What a maintain pass will DO, decided before anything is written.

    :func:`classify_maintain_work` fills this by reading rows only, and each
    engine's own applier is the only thing that writes -- so what a
    regeneration decides can be asserted without a database write, and a change
    to the decision cannot hide inside a change to the write.

    Attributes:
        update: Rule-generated rows the definition still names, to be brought
            into line with what it derives for their period.
        create_in: One :class:`PlacedRow` per row to write -- the periods
            the rule names that hold no row of this template at all, each with
            the occurrence it answers.  A period holding ANY row -- immutable,
            overridden or soft-deleted -- is absent, which is the long-standing
            "one row per template per paycheck" rule
            (:func:`should_skip_period`).
        retire: Rows the rule no longer names that carry nothing of the
            owner's, to be deleted.
        overridden_ids: Conflicts -- the owner set this row's amount by hand.
        deleted_ids: Conflicts -- the owner removed this row.
        retained_ids: Conflicts -- the row carries the owner's own records, and
            applying the definition change would have destroyed or
            re-attributed them (finding **N-292**).
    """

    update: list
    create_in: "list[PlacedRow]"
    retire: list
    overridden_ids: "list[int]"
    deleted_ids: "list[int]"
    retained_ids: "list[int]"


class MaintainOutcome(NamedTuple):
    """What one maintain pass actually did, for the audit event and the raise.

    Attributes:
        created: Rows created this pass -- the value each engine's
            ``regenerate_for_template`` returns, which keeps the meaning every
            caller already reads it with.
        updated: Rows brought into line in place.  Before plan steps R10-a and
            R10-b these were deleted and recreated, so they appeared in
            *created*.
        removed: Rows the rule no longer names that carried nothing.
        overridden_ids: See :class:`MaintainWork`.
        deleted_ids: See :class:`MaintainWork`.
        retained_ids: See :class:`MaintainWork`.
    """

    created: list
    updated: list
    removed: list
    overridden_ids: "list[int]"
    deleted_ids: "list[int]"
    retained_ids: "list[int]"

    @classmethod
    def after(cls, work: "MaintainWork", created: list, updated: list):
        """Return the outcome of applying *work*, given what the write did.

        **Four of the six fields are the DECISION read back**, and only
        *created* and *updated* are news: a row the pass retired is
        ``work.retire``, and the three conflict lists were settled before
        anything was written.  Stating that mapping once is what stops the two
        engines each spelling it out -- pylint's ``duplicate-code`` measured
        exactly this ten-line tail the moment the transfer engine grew its
        maintain pass (plan step R10-b), and a one-sided disable would have
        preserved a copy rather than removed one.

        Args:
            work: The classified :class:`MaintainWork` this pass applied.
            created: The rows the write added.
            updated: The rows it brought into line.

        Returns:
            The :class:`MaintainOutcome` for the audit event and the raise.
        """
        return cls(
            created=created,
            updated=updated,
            removed=work.retire,
            overridden_ids=work.overridden_ids,
            deleted_ids=work.deleted_ids,
            retained_ids=work.retained_ids,
        )


def occurrence_by_period(placements) -> dict:
    """Return ``{pay_periods.id: occurs_on}`` for this pass's placements.

    What :func:`classify_maintain_work` takes since plan step **R17**, hoisted
    here because both engines' maintain paths build it from the same value and
    a private copy in each is the drift this module exists to prevent.  It
    duck-types ``placements`` exactly as :func:`refuse_repeats_this_pass` does
    -- reading ``.period.period_id`` and ``.occurrence`` off
    ``recurrence_engine.PlannedOccurrence`` -- because importing that type here
    would close a cycle (``_plan`` imports this module).

    **A repeated period keeps the LAST placement's occurrence**, which is not
    an arbitrary tie-break but the same collapse each engine's ``derived`` map
    already makes one line above: both are keyed by period, so a paycheck a
    rule names twice reduces to one entry in each. The two therefore agree by
    construction rather than by care. That case is the 30-day-cadence repeat of
    ledger row **D19**; :func:`refuse_unstorable_repeats` refuses it on the
    generate path, and on THIS path it is storable because ``create_in``
    excludes every occupied period. Making the pair a true one-per-occurrence
    answer is what the ``occurs_on`` index re-key owns (plan step R5).

    Args:
        placements: This pass's ``recurrence_engine.PlannedOccurrence`` values.

    Returns:
        The period-to-occurrence map, one entry per DISTINCT period named.
    """
    return {
        placement.period.period_id: placement.occurrence
        for placement in placements
    }


def classify_maintain_work(
    existing, named, *, with_records, reattributed,
) -> MaintainWork:
    """Decide what a maintain pass must do to each row, WITHOUT writing.

    The whole decision of ruling **R-R19** in one pure reduction: a row the rule
    still names is maintained, a row it no longer names is retired, and either
    becomes a conflict the moment the owner's own records are in the way.

    **A row is retained rather than changed in exactly two shapes**, and both
    are finding **N-292**: the rule no longer fires in this row's period, or
    the definition has moved the ACCOUNTS the row's records are attributed to.
    Neither is safe to apply silently, so the pass leaves the row exactly as it
    found it and asks.

    **Shared by both engines since plan step R10-b, over two ID SETS rather
    than a model.**  Every question this asks about a row is one both a
    Transaction and a Transfer answer identically -- which period it is in,
    whether the owner holds it -- and the two that are NOT (what "records"
    means, and what "the definition moved the accounts" means, since a transfer
    has two of them) arrive already resolved as sets of ids.  So there is one
    decision function rather than a transaction one and a near-copy of it, and
    each engine keeps exactly the part that is about its own table.

    Args:
        existing: Every row of this template in the pass's WRITE WINDOW at
            or after its bound.  The window half is the load-bearing one:
            it is what keeps this domain a superset of the plan's, and so
            what makes the RETIRE branch reachable.
        named: ``{pay_periods.id: occurs_on}`` -- the periods the rule names
            now, each mapped to the date its cadence names there.  Empty for a
            template whose recurrence was CLEARED, which correctly makes every
            row an orphan.  It was a bare SET of ids until plan step **R17**;
            the create arm now has to state an occurrence and cannot derive one
            from a period id (see :class:`PlacedRow`).
        with_records: Ids of rows carrying the owner's own records, resolved by
            the engine from its own table -- purchases, a note, a settlement
            record or a statement link.
        reattributed: Ids of rows whose ACCOUNTS the definition has moved.  A
            transaction has one account and a transfer has two, and that is the
            whole of the difference; both reduce to "would applying the
            definition move where this row's records are filed".

    Returns:
        The :class:`MaintainWork` this pass should apply.
    """
    work = MaintainWork([], [], [], [], [], [])
    occupied = set()
    for row in existing:
        is_named = row.pay_period_id in named
        if is_named:
            # ANY row occupies its period, so no second row is created beside
            # it -- including the immutable, overridden and soft-deleted rows
            # the loop below then declines to maintain.
            occupied.add(row.pay_period_id)
        hold = owner_hold_on(row)
        if hold == BLOCK_IMMUTABLE:
            continue
        if hold == BLOCK_OVERRIDE:
            work.overridden_ids.append(row.id)
            continue
        if hold == BLOCK_DELETED:
            work.deleted_ids.append(row.id)
            continue
        if not is_named:
            if row.id in with_records:
                work.retained_ids.append(row.id)
            else:
                work.retire.append(row)
            continue
        if row.id in reattributed and row.id in with_records:
            work.retained_ids.append(row.id)
            continue
        work.update.append(row)
    work.create_in.extend(
        PlacedRow(period_id, named[period_id])
        for period_id in sorted(set(named) - occupied)
    )
    return work


def refuse_repeats_this_pass(template, placements, existing) -> None:
    """Refuse a maintain pass that would write one paycheck's row twice.

    Both engines' unique index holds one row per ``(template, period,
    scenario)`` -- ``idx_transactions_template_period_scenario`` and
    ``idx_transfers_template_period_scenario``, each PARTIAL over
    ``is_deleted = FALSE AND is_override = FALSE`` -- and forward generation
    legitimately names a paycheck more than once at a cadence of 30 days or
    more, so an unstorable cadence must be refused before anything is written
    (:func:`refuse_unstorable_repeats`, plan ledger row **D19**).  The partial
    predicate is why the blocking set below can be narrower than the generate
    path's and still store: a row the pass may MAINTAIN is neither deleted nor
    overridden, so it is inside the index and no second row is created beside
    it.

    **The blocking set is narrower here than on the generate path, and the
    reason is PARITY rather than storage.**  An earlier revision of this
    docstring said two placements onto a maintained row "would still be two
    rows"; an adversarial review of plan step R10-a disproved it.  On this path
    they would not: ``create_in`` excludes every occupied period and ``update``
    holds at most one row per period, so a repeat is physically storable here
    and no index violation is possible.  What the narrowing preserves is the
    ANSWER the old delete-then-generate pass gave -- it deleted the rule's own
    row first, so the paycheck looked empty to the refusal and an unstorable
    cadence was reported.  Widening the set would silently start ACCEPTING a
    cadence this app has refused since plan ledger row **D19**, turning a loud
    refusal into a schedule that quietly bills one paycheck once for a rule that
    names it twice.  Verified to fire identically on both sides: a maintainable
    row does not make its paycheck safe, a non-maintainable one does.

    **It takes the flat row list and groups internally**, which is the last
    literal duplicate the two engines shared: each built the same
    ``{period_id: [row, ...]}`` dict three lines before calling this, and this
    was its only consumer.  An adversarial review of plan step R10-b named it.

    Args:
        template: The template being maintained -- read for its name by the
            refusal's message.
        placements: This pass's ``recurrence_engine.PlannedOccurrence`` values.
        existing: Every row of this template in the pass's write window, flat.

    Raises:
        RecurrenceCadenceUnsupported: See :func:`refuse_unstorable_repeats`.
    """
    blocking: dict[int, list] = defaultdict(list)
    for row in existing:
        if not is_maintainable(row):
            blocking[row.pay_period_id].append(row)
    refuse_unstorable_repeats(template, placements, dict(blocking))


def existing_rows_refusing_repeats(selector, placements) -> dict[int, list]:
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
        selector: This pass's :class:`TemplateRowSelector`.
        placements: This pass's ``recurrence_engine.PlannedOccurrence`` values,
            one per occurrence, whose ``period`` may therefore repeat.

    Returns:
        ``{pay_period_id: [row, ...]}``, as :func:`existing_rows_by_period`
        returns it.

    Raises:
        RecurrenceCadenceUnsupported: See :func:`refuse_unstorable_repeats`.
    """
    existing = existing_rows_by_period(
        selector,
        [placement.period.period_id for placement in placements],
    )
    refuse_unstorable_repeats(selector.template, placements, existing)
    return existing


def existing_rows_by_period(selector, period_ids) -> dict[int, list]:
    """Group this template's existing rows in *period_ids* by pay period.

    Shared by both recurrence engines' ``generate_for_template``, which each
    carried a byte-similar copy of it until plan step R4b-2 -- the exact
    duplication this module exists to hold, and the parameterisation is the one
    :class:`TemplateRowSelector` now carries for every fetch here: the model
    class and the template foreign-key column, and nothing else, differ between
    the two engines.

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
        selector: This pass's :class:`TemplateRowSelector`.
        period_ids: The ``budget.pay_periods.id`` values to look in.  Empty
            short-circuits without a query.

    Returns:
        ``{pay_period_id: [row, ...]}``, absent for a period holding no row.
    """
    grouped: dict[int, list] = defaultdict(list)
    for row in _rows_in_periods(selector, period_ids):
        grouped[row.pay_period_id].append(row)
    # A plain dict, not the defaultdict: the documented contract is that a
    # period holding no row is ABSENT, and a defaultdict would silently create
    # one for the next caller that indexes instead of ``.get``.
    return dict(grouped)


def rows_this_pass_may_maintain(selector, schedule, effective_from) -> list:
    """Fetch the template-linked rows a regenerate pass is allowed to act on.

    Shared by both recurrence engines' ``regenerate_for_template`` to collect
    the rows eligible for the maintain / retire decision.  The only per-engine
    differences are the model class and the template foreign-key column, so
    both are parameters.

    **It selects on a period-ID SET, and the set is the pass's own write
    window** (pay-calendar plan step C2-f3c).  It was
    ``query_rows_from_effective_date``: a ``JOIN budget.pay_periods ON ... WHERE
    pay_periods.end_date >= :effective_from``.

    **This is a CUTOVER, not the repair of a divergence, and an adversarial
    review of C2-f3c is why that is said plainly.**  A first draft of this
    paragraph claimed the old select read the STORED end while
    ``resolve_generation_plan`` filtered the DERIVED one, and it was false:
    that function resolved the ORM row out of the write window BEFORE applying
    the bound, deliberately and with a comment saying so, precisely so both
    halves read the same stored column.  They agreed.  What C2-f3c does is move
    them BOTH onto the derived end, because plan step **C4** drops the column
    they agreed on -- and the reason that is safe is a separate invariant, not
    a bug being fixed: ``pay_period_write._write_derivation`` is the only
    writer of ``end_date`` in ``app/`` and rewrites every stored end from the
    derivation on every pass.  Measured 2026-08-19 on production: 62 periods,
    zero rows where the two differ.  The one shape where they still can is
    legacy data written before plan step C3-b, and there the derived end is the
    answer this arc exists to give.

    **The domain is the WINDOW, and it must be a SUPERSET of the plan's named
    set** or ``_maintain``'s RETIRE branch could never fire: a row is retired
    precisely because the rule NO LONGER names its period, so the rows offered
    here have to include the periods the plan does not.  They do, by
    construction -- the plan is this same window intersected with the periods
    the rule names, under the same bound.  A superset rather than a strict one:
    where the rule names every period of the window the two sets are EQUAL,
    which is the ordinary case for an every-paycheck template and the case in
    which nothing is retired.  Both live callers pass a whole-schedule window,
    so the set is every one of the owner's periods.

    **Bounding by the window is also what let ``regeneration_bound`` go.** That
    helper turned "no lower bound" into the window's opening date, because a
    sweep bounded only by the SCHEDULE's opening would retire rows from the
    owner's first payday forward while regenerating inside the window alone.
    A window-shaped domain cannot do that whatever the bound is, so ``None``
    once again plainly means "no lower bound".

    Args:
        selector: This pass's :class:`TemplateRowSelector`.
        schedule: The pass's
            :class:`~app.services.generation_schedule.GenerationSchedule` --
            the owner's calendar plus the periods this pass may write into.
        effective_from: Only rows whose pay period ends on or after this date
            are returned, so the current period is included when the date falls
            mid-period.  ``None`` applies no lower bound.

    Returns:
        A list of matching model instances, including soft-deleted and
        immutable rows -- the caller classifies them.
    """
    window = schedule.write_period_ids
    period_ids = [
        period.period_id
        for period in schedule.calendar.saved()
        if period.period_id in window
        and (effective_from is None or period.end_date >= effective_from)
    ]
    return _rows_in_periods(selector, period_ids)


def _rows_in_periods(selector, period_ids) -> list:
    """Return this template's rows, in this scenario, in *period_ids*.

    **THE statement of the query both fetches run.**
    :func:`existing_rows_by_period` and :func:`rows_this_pass_may_maintain`
    differ only in the SHAPE they hand back and in how their period set is
    chosen; until pay-calendar plan step C2-f3c they differed in the query too,
    because one selected on ids and the other joined ``budget.pay_periods`` on
    a column plan step C4 drops.  With both selecting on ids, two spellings of
    one ``SELECT`` is the duplication this module exists to hold.

    Args:
        selector: This pass's :class:`TemplateRowSelector`.
        period_ids: The ``budget.pay_periods.id`` values to look in.  Empty
            short-circuits without a query -- ``IN ()`` is not valid SQL and a
            pass with no periods has nothing to find.

    Returns:
        Every matching row, in no particular order.
    """
    ids = list(period_ids)
    if not ids:
        return []
    return (
        db.session.query(selector.model)
        .filter(
            selector.template_fk_col == selector.template.id,
            selector.model.scenario_id == selector.scenario_id,
            selector.model.pay_period_id.in_(ids),
        )
        .all()
    )
