"""
Shekel Budget App -- Recurrence Engine: bringing generated rows back into line

:func:`regenerate_for_template` and the pass behind it.  Run when a template's
amount, schedule or fields change, it MAINTAINS the rows the definition already
generated: a row the rule still names is updated in place, a period the rule
names with no row gets one, and a row the rule no longer names is retired --
unless the owner has records against it, in which case the pass leaves it
exactly as it found it and asks.

**Until plan step R10-a this deleted every auto-generated row in the window and
generated replacements** (ruling **R-R19**, finding **N-292**).  That was safe
only while a generated row was a pure projection of ``(template, period)``, and
it has not been one for a long time: ``transaction_entries`` CASCADE from their
parent, so an edit as small as a rename destroyed the PURCHASES recorded
against a part-spent envelope -- measured on a production clone at 3 records
worth ``$499.82``, taken with no prompt and an ``overridden_conflict_count`` of
0.  It also dropped ``notes``, ``is_envelope``, ``companion_visible``,
``actual_amount``, ``created_at`` and the row's own id.

The decision and the write are separate on purpose
(:func:`_classify_maintain_work` reads, :func:`_apply_maintain_work` writes),
so what a regeneration decides can be asserted without a database write.
"""
import logging
from typing import NamedTuple

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.exceptions import RecurrenceConflict
from app.services import posting_service
from app.services._recurrence_common import (
    check_scenario_ownership,
    refuse_unstorable_repeats,
    regeneration_bound,
    query_rows_from_effective_date,
)
from app.services.recurrence_engine._amounts import (
    _derive_row_fields,
    _get_salary_profile,
)
from app.services.recurrence_engine._plan import resolve_generation_plan
from app.utils.log_events import (
    BUSINESS,
    EVT_RECURRENCE_REGENERATED,
    log_event,
)

logger = logging.getLogger(__name__)



def regenerate_for_template(template, schedule, scenario_id, effective_from=None):
    """Bring a template's future rows into line with its current definition.

    Run when a template's amount, schedule or fields change.  **It MAINTAINS
    the rows the rule still names rather than destroying and rebuilding them**,
    which is plan step R10-a and ruling **R-R19**, closing finding **N-292**.

    Three outcomes, one per period the pass considers:

      1. the rule names the period and an auto-generated row is there -- the
         row is UPDATED in place from :class:`DerivedRowFields` and its
         postings reconciled;
      2. the rule names the period and nothing is there -- a row is created,
         exactly as :func:`generate_for_template` would;
      3. the rule NO LONGER names the period -- the row is removed if it is
         empty, and RETAINED as a conflict if the owner has records against it.

    Overridden and soft-deleted rows are conflicts wherever they sit, as
    before; immutable rows are never touched.

    **Until plan step R10-a this deleted every auto-generated row in the window
    and generated replacements**, which was safe only while a generated row was
    a pure projection of ``(template, period)``.  It has not been one for a
    long time: ``transaction_entries`` CASCADE from their parent, so the sweep
    destroyed the PURCHASES recorded against a part-spent envelope -- measured
    on a production clone at 3 records worth ``$499.82``, taken silently by an
    edit as small as a rename, with ``overridden_conflict_count`` reporting 0.
    It also dropped ``notes``, ``is_envelope``, ``companion_visible``,
    ``actual_amount``, ``created_at`` and the row's own id, and the lost id is
    why plan step X-f3b had to reverse a row's ledger legs on the way out.  A
    row the rule still names is now never deleted, so none of that is reachable
    on this path and no future column has to be remembered either.

    **Maintaining is not a behaviour change for a row with nothing on it, and
    that is provable rather than hoped for.**  ``Projected`` is the ONLY
    non-immutable status in ``ref.statuses``, so this pass can reach nothing
    else, and an update writes exactly the values a recreation would have
    written -- measured at 504 of 505 live sweepable rows identical, the 505th
    being the one that keeps its purchases.

    **The pass and the rule share ONE bound, and that is load-bearing.**
    ``effective_from`` bounds an SQL select over ``pay_periods.end_date``, so
    "no lower bound" has to become a date before it can be compared against a
    column -- and the date it becomes must be the opening of the WINDOW this
    pass writes into, not of the whole schedule.  Taking the schedule's opening
    instead would reach every non-override row from the owner's first payday
    forward while the rule was resolved only inside the window, retiring rows
    nothing would recreate.  No route reaches that today -- both callers pass a
    whole-schedule window, where the two bounds coincide -- which is exactly
    why the asymmetry is closed here rather than left for someone to discover
    with a narrow window.

    Args:
        template:       The updated TransactionTemplate.
        schedule:       The owner's
                        :class:`~app.services.generation_schedule.GenerationSchedule`.
        scenario_id:    The target scenario.
        effective_from: Date from which to maintain (default: the WRITE
                        WINDOW's first payday -- see above; the row select and
                        the rule must not use different bounds).

    Returns:
        List of newly created Transaction objects.  Rows this pass UPDATED are
        not in it, so the value keeps the meaning every caller already reads it
        with.

    Raises:
        RecurrenceCadenceUnsupported: When one paycheck would have to host this
            template's row more than once -- see
            :func:`_recurrence_common.refuse_unstorable_repeats`.
        RecurrenceConflict: When rows exist that this pass must not change
            unasked.  The caller should catch it, present the options, and call
            :func:`resolve_conflicts`.
    """
    # Defense-in-depth, and it also DISAMBIGUATES the plan below: a ``None``
    # plan means either "not your scenario" or "this template no longer
    # recurs", and those want opposite answers -- do nothing, versus retire
    # every row the vanished rule used to name.  Asking ownership here leaves
    # the plan's ``None`` meaning exactly one thing.
    if not check_scenario_ownership(
        logger, template, scenario_id,
        block_message="Blocked cross-user recurrence regeneration",
    ):
        return []

    effective_from = regeneration_bound(schedule, effective_from)

    # What the rule names NOW, and what is there already.  ``plan`` is None
    # only for a CLEARED recurrence (ownership is settled above), in which case
    # the rule names no period at all and every existing row is considered for
    # retirement -- the behaviour ``regenerate_or_conflict_chooser`` documents
    # for a template whose pattern was set to "Does not repeat".
    plan = resolve_generation_plan(
        template, schedule, scenario_id, effective_from,
        block_message="Blocked cross-user recurrence regeneration",
    )
    existing = query_rows_from_effective_date(
        Transaction, Transaction.template_id,
        template.id, scenario_id, effective_from,
    )

    outcome = _maintain_instances(template, plan, schedule, scenario_id, existing)
    db.session.flush()

    log_event(
        logger, logging.INFO, EVT_RECURRENCE_REGENERATED, BUSINESS,
        "Recurrence regenerated for template",
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




class _MaintainWork(NamedTuple):
    """What a maintain pass will DO, decided before anything is written.

    :func:`_classify_maintain_work` fills this by reading rows only, and
    :func:`_apply_maintain_work` is the only thing that writes -- so what a
    regeneration decides can be asserted without a database write, and a change
    to the decision cannot hide inside a change to the write.

    Attributes:
        update: Rule-generated rows the definition still names, to be brought
            into line with :class:`DerivedRowFields`.
        create_in: ``pay_periods.id`` values the rule names that hold no row of
            this template at all.  A period holding ANY row -- immutable,
            overridden or soft-deleted -- is absent, which is the long-standing
            "one row per template per paycheck" rule
            (:func:`_recurrence_common.should_skip_period`).
        retire: Rows the rule no longer names that carry nothing of the
            owner's, to be deleted.
        overridden_ids: Conflicts -- the owner set this row's amount by hand.
        deleted_ids: Conflicts -- the owner removed this row.
        retained_ids: Conflicts -- the row carries the owner's own records, and
            applying the definition change would have destroyed or
            re-attributed them (finding **N-292**).
    """

    update: list[Transaction]
    create_in: list[int]
    retire: list[Transaction]
    overridden_ids: list[int]
    deleted_ids: list[int]
    retained_ids: list[int]




class _MaintainOutcome(NamedTuple):
    """What one maintain pass actually did, for the audit event and the raise.

    Attributes:
        created: Rows created this pass -- the value
            :func:`regenerate_for_template` returns, which keeps the meaning
            every caller already reads it with.
        updated: Rows brought into line in place.  Before plan step R10-a these
            were deleted and recreated, so they appeared in *created*.
        removed: Rows the rule no longer names that carried nothing.
        overridden_ids: See :class:`_MaintainWork`.
        deleted_ids: See :class:`_MaintainWork`.
        retained_ids: See :class:`_MaintainWork`.
    """

    created: list[Transaction]
    updated: list[Transaction]
    removed: list[Transaction]
    overridden_ids: list[int]
    deleted_ids: list[int]
    retained_ids: list[int]




def _is_maintainable(row) -> bool:
    """Return True when *row* is the RULE's own row, free to be maintained.

    The one definition of "this row belongs to the definition, not to the
    owner", asked by both the classifier and the repeat refusal.  Its three
    negatives are the three things the owner can own about a row -- its
    permanence (an immutable status), its amount (``is_override``) and its
    existence (``is_deleted``) -- each of which makes the row a conflict to
    surface rather than a row to rewrite.

    Args:
        row: The Transaction to classify.

    Returns:
        True when the row is auto-generated, live and still mutable.
    """
    if row.status and row.status.is_immutable:
        return False
    return not row.is_override and not row.is_deleted




def _rows_holding_owner_records(existing) -> set[int]:
    """Return the ids of rows carrying content their template did not put there.

    "The owner's own records" is what finding **N-292** is about: a generated
    row is not a pure projection of ``(template, period)``, and the columns
    below are the ones a regeneration cannot reconstruct.  Purchases are the
    costly one -- ``transaction_entries`` CASCADE from their parent, so the old
    delete-and-recreate sweep destroyed them, measured at 3 records worth
    ``$499.82`` on one live row.

    **The purchases are asked in ONE query, not one per row.**  A regeneration
    considers every future row of a template -- 505 of them across the live
    templates on a production clone -- so reading ``row.entries`` in the
    classifier would issue a query per row on the hot path of every template
    edit.

    Args:
        existing: The rows this pass is considering.

    Returns:
        The subset of their ids that hold purchases, a note, or a hand-entered
        actual.
    """
    ids = [row.id for row in existing]
    if not ids:
        return set()
    holding = {
        transaction_id
        for (transaction_id,) in db.session.query(
            TransactionEntry.transaction_id,
        ).filter(TransactionEntry.transaction_id.in_(ids)).distinct()
    }
    for row in existing:
        # ``notes`` is free text the owner typed and no writer derives; a
        # whitespace-only note is not a record worth blocking an edit over.
        if row.notes is not None and row.notes.strip():
            holding.add(row.id)
        elif row.actual_amount is not None:
            holding.add(row.id)
    return holding




def _classify_maintain_work(existing, named_period_ids, account_id, with_records):
    """Decide what a maintain pass must do to each row, WITHOUT writing.

    The whole decision of ruling **R-R19** in one pure reduction: a row the rule
    still names is maintained, a row it no longer names is retired, and either
    becomes a conflict the moment the owner's own records are in the way.

    **A row is retained rather than changed in exactly two shapes**, and both
    are finding **N-292**: the rule no longer fires in this row's period (the
    old sweep deleted the row, and its purchases CASCADE with it), or the
    template's ACCOUNT has moved, which drags every purchase onto the new
    account -- ``fk_transaction_entries_parent_account`` binds a purchase's
    account to its parent's -- and invalidates any statement link the purchases
    carry, since ``fk_transaction_entries_reconciled_by`` scopes that link BY
    ACCOUNT.  Neither is safe to apply silently, so the pass leaves the row
    exactly as it found it and asks.

    Args:
        existing: Every row of this template at or after the pass's bound.
        named_period_ids: The ``pay_periods.id`` values the rule names now.
            Empty for a template whose recurrence was CLEARED, which correctly
            makes every row an orphan.
        account_id: The template's account NOW, compared against each row's to
            detect a move.
        with_records: Ids of rows carrying the owner's own records, from
            :func:`_rows_holding_owner_records`.

    Returns:
        The :class:`_MaintainWork` this pass should apply.
    """
    work = _MaintainWork([], [], [], [], [], [])
    occupied = set()
    for row in existing:
        named = row.pay_period_id in named_period_ids
        if named:
            # ANY row occupies its period, so no second row is created beside
            # it -- including the immutable, overridden and soft-deleted rows
            # the loop below then declines to maintain.
            occupied.add(row.pay_period_id)
        if row.status and row.status.is_immutable:
            continue
        if row.is_override:
            work.overridden_ids.append(row.id)
            continue
        if row.is_deleted:
            work.deleted_ids.append(row.id)
            continue
        if not named:
            if row.id in with_records:
                work.retained_ids.append(row.id)
            else:
                work.retire.append(row)
            continue
        if row.account_id != account_id and row.id in with_records:
            work.retained_ids.append(row.id)
            continue
        work.update.append(row)
    work.create_in.extend(sorted(named_period_ids - occupied))
    return work




def _apply_maintain_work(work, derived, template_id, scenario_id, projected_id):
    """Write one classified maintain pass, and reconcile what it moved.

    The only writer in the maintain path.  Order is load-bearing in one place:
    the ledger reconcile runs AFTER the flush, so each updated row's postings
    are reconciled against the amount and account the flush actually stored.

    Args:
        work: The :class:`_MaintainWork` from :func:`_classify_maintain_work`.
        derived: ``{pay_period_id: DerivedRowFields}`` for every period the
            rule names -- the single statement of what a generated row's
            definition says, consumed identically by the update and the create.
        template_id: The template every written row is linked to.
        scenario_id: The scenario every created row is written into.
        projected_id: The ``Projected`` status id for created rows.  ``None``
            only when the rule was cleared, in which case *work.create_in* is
            empty and it is never read.

    Returns:
        ``(created, updated)`` -- the rows this pass added and the rows it
        brought into line.
    """
    updated = []
    for row in work.update:
        for field, value in derived[row.pay_period_id]._asdict().items():
            setattr(row, field, value)
        updated.append(row)

    created = []
    for period_id in work.create_in:
        txn = Transaction(
            **derived[period_id]._asdict(),
            template_id=template_id,
            pay_period_id=period_id,
            scenario_id=scenario_id,
            status_id=projected_id,
            is_override=False,
            is_deleted=False,
        )
        db.session.add(txn)
        created.append(txn)

    # Reverse before deleting: a retired row can hold ledger legs, because a
    # PROJECTED envelope does once one of its purchases carries a recorded bank
    # posting day (plan step X-f3b, ruling **R-FM**), and
    # ``journal_entries.transaction_entry_id`` is ON DELETE SET NULL -- so
    # deleting without reversing strands both legs with nothing to offset them.
    # **This is now the ONLY path here that deletes**, and it is reached only
    # when the rule stopped naming the row's period AND the row carries nothing
    # of the owner's.
    for row in work.retire:
        posting_service.reverse_postings_before_delete(row)
        db.session.delete(row)

    db.session.flush()

    # An updated row may have moved money: its amount can change with the
    # template's, and its account with it.  Reconciling to the STORED values is
    # what keeps the ledger equal to the rows without a second definition of
    # what those rows say.
    for row in updated:
        posting_service.sync_transaction_postings(
            row, settled=row.status.is_settled,
        )
    return created, updated




def _refuse_repeats_this_pass(template, placements, existing):
    """Refuse a maintain pass that would write one paycheck's row twice.

    ``idx_transactions_template_period_scenario`` holds one row per
    ``(template, period, scenario)``, and forward generation legitimately names
    a paycheck more than once at a cadence of 30 days or more, so an unstorable
    cadence must be refused before anything is written
    (:func:`_recurrence_common.refuse_unstorable_repeats`, plan ledger row
    **D19**).

    **The blocking set is narrower here than on the generate path, and it has
    to be.**  That refusal skips a paycheck it considers already occupied, and
    on the generate path ANY existing row occupies one.  A maintain pass
    MAINTAINS the rule's own row rather than adding beside it, so such a row
    does not make the paycheck safe -- two placements onto it would still be
    two rows.  Passing only the rows that genuinely block a write keeps the
    refusal firing exactly where the index would.

    Args:
        template: The template being maintained -- read for its name by the
            refusal's message.
        placements: This pass's :class:`PlannedOccurrence` values.
        existing: ``{pay_period_id: [row, ...]}`` for this template.

    Raises:
        RecurrenceCadenceUnsupported: See
            :func:`_recurrence_common.refuse_unstorable_repeats`.
    """
    blocking = {}
    for period_id, rows in existing.items():
        held = [row for row in rows if not _is_maintainable(row)]
        if held:
            blocking[period_id] = held
    refuse_unstorable_repeats(template, placements, blocking)




def _maintain_instances(template, plan, schedule, scenario_id, existing):
    """Resolve and apply everything one regeneration does to a template's rows.

    The body of :func:`regenerate_for_template`, split out so the orchestrator
    reads as ownership -> bound -> plan -> maintain -> report.  Runs in four
    steps: refuse an unstorable cadence, derive what the definition says for
    every period the rule names, classify each existing row against that, then
    write.

    Args:
        template: The updated TransactionTemplate.
        plan: The :class:`GenerationPlan` for this pass, or ``None`` when the
            template's recurrence was CLEARED -- which names no period, so
            every row is considered for retirement.
        schedule: The owner's
            :class:`~app.services.generation_schedule.GenerationSchedule`.
        scenario_id: The scenario being maintained.
        existing: Every row of this template at or after the pass's bound.

    Returns:
        The :class:`_MaintainOutcome` for the audit event and the conflict
        raise.
    """
    placements = plan.placements if plan is not None else ()
    by_period: dict[int, list[Transaction]] = {}
    for row in existing:
        by_period.setdefault(row.pay_period_id, []).append(row)
    _refuse_repeats_this_pass(template, placements, by_period)

    salary_profile = _get_salary_profile(template)
    derived = {
        placement.period.id: _derive_row_fields(
            template, plan.rule, salary_profile, placement.period, schedule,
        )
        for placement in placements
    }
    work = _classify_maintain_work(
        existing, set(derived), template.account_id,
        _rows_holding_owner_records(existing),
    )
    created, updated = _apply_maintain_work(
        work, derived, template.id, scenario_id,
        plan.projected_id if plan is not None else None,
    )
    return _MaintainOutcome(
        created=created,
        updated=updated,
        removed=work.retire,
        overridden_ids=work.overridden_ids,
        deleted_ids=work.deleted_ids,
        retained_ids=work.retained_ids,
    )
