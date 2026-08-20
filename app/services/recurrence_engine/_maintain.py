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
(:func:`~app.services._recurrence_common.classify_maintain_work` reads,
:func:`_apply_maintain_work` writes), so what a regeneration decides can be
asserted without a database write.  **The decision itself is SHARED with the
transfer engine since plan step R10-b** and lives in
:mod:`app.services._recurrence_common`; what stays here is what is about this
table -- what a generated row's definition says, what "the owner's records"
means on it, and the write.
"""
import logging

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.exceptions import RecurrenceConflict
from app.services import posting_service
from app.services._recurrence_common import (
    MaintainOutcome,
    check_scenario_ownership,
    classify_maintain_work,
    refuse_repeats_this_pass,
    rows_this_pass_may_maintain,
)
from app.services.recurrence_engine._generate import _selector
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

    **The pass and the rule share ONE bound and ONE domain, and since
    pay-calendar plan step C2-f3c both are structural rather than upheld.**
    They shared the bound before it too -- ``resolve_generation_plan`` resolved
    the ORM row before applying it, so both halves read the same STORED
    ``end_date`` -- and an adversarial review of C2-f3c corrected a draft of
    this paragraph that claimed otherwise.  What changed is that both now read
    the DERIVED end, off the same calendar, because plan step **C4** drops the
    column they used to agree on.

    What ``regeneration_bound`` actually existed for was narrower: the sweep
    was SQL, and ``end_date >= NULL`` matches no row, so "no lower bound" had
    to become a concrete date before it could be compared against a column at
    all -- and the date it became had to be the WRITE WINDOW's opening, because
    a sweep bounded by the SCHEDULE's opening would have reached every
    non-override row from the owner's first payday forward while the rule was
    resolved only inside the window, retiring rows nothing would recreate.  A
    period-ID set taken from that same window
    (``_recurrence_common.rows_this_pass_may_maintain``) needs no translation
    for ``None`` and cannot exceed the window whatever the bound is, so both
    halves of that helper's job are gone and the helper with them.

    Args:
        template:       The updated TransactionTemplate.
        schedule:       The owner's
                        :class:`~app.services.generation_schedule.GenerationSchedule`.
        scenario_id:    The target scenario.
        effective_from: Date from which to maintain, or ``None`` for the whole
                        write window.  The row select and the rule take it
                        unchanged, and read it against the same derived end.

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

    # What the rule names NOW, and what is there already.  ``plan`` is None
    # only for a CLEARED recurrence (ownership is settled above), in which case
    # the rule names no period at all and every existing row is considered for
    # retirement -- the behaviour ``regenerate_or_conflict_chooser`` documents
    # for a template whose pattern was set to "Does not repeat".
    #
    # The two calls take the SAME bound and the same window, and the second's
    # answer is a SUPERSET of the first's -- it is the window, where the plan
    # is the window intersected with the periods the rule names.  That is what
    # makes the RETIRE branch reachable at all: a row is retired precisely
    # because the rule no longer names the row's period.  Equal, not strictly
    # wider, when the rule names every period of the window -- which is the
    # ordinary case, and the case in which nothing is retired.
    plan = resolve_generation_plan(
        template, schedule, scenario_id, effective_from,
        block_message="Blocked cross-user recurrence regeneration",
    )
    existing = rows_this_pass_may_maintain(
        _selector(template, scenario_id), schedule, effective_from,
    )

    outcome = _maintain_instances(
        template, plan, schedule.calendar, scenario_id, existing,
    )
    db.session.flush()

    # **ONE event per pass, and it gained a field while another LEFT.**  This
    # used to delegate its create half to ``generate_for_template``, so every
    # template edit emitted ``EVT_RECURRENCE_GENERATED`` as well; the maintain
    # pass creates rows itself, so it no longer does.  ``updated_count`` is new
    # and ``deleted_count`` now counts only rows the rule stopped naming --
    # under the old shape it counted every row in the window, and its twin
    # ``created_count`` counted the same rows again.  A reader comparing
    # forensics across this step must not treat the two as the same number.
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

    **A STATEMENT LINK counts too, and it needs no condition of its own** --
    which plan step R10-b measured, correcting what R10-a's own adversarial
    review concluded here.  The account-move half of the retention rule is
    justified by two composite keys that scope a clearing link BY ACCOUNT
    (``fk_transaction_entries_reconciled_by`` on the purchase and
    ``fk_transactions_reconciled_by`` on the row itself), so a linked row must
    be retained -- and it already is.  Two CHECK constraints chain into an
    implication: ``ck_transactions_cleared_needs_settle_day`` says a link needs
    a settle day, ``ck_transactions_settle_day_needs_basis`` says a settle day
    needs a basis, so ``reconciled_by_id IS NOT NULL`` implies
    ``settled_basis_id IS NOT NULL``.  The ``elif`` that used to follow the
    settlement arm was therefore reached by no row that exists, which is why
    deleting it moved nothing: verified against PostgreSQL, which refuses to
    clear the basis on a linked row.  If either CHECK is ever dropped, this
    paragraph is what says the arm has to come back.

    **The SETTLEMENT arm reads the record rather than a column that used to
    proxy for it** (plan step X-au-c3).  It was ``actual_amount is not None``,
    which meant "a human typed a figure here" only because that column carried
    both the settled figure and the fact that a human had supplied it.  A row
    that has settled records what moved, whoever said so, and that is the fact
    worth holding a row for -- so the predicate reads ``settled_basis_id``.

    **Unlike the statement-link arm above it this one is REACHABLE, and the same
    step is what made it so.**  A revert releases the ASSERTION and keeps WHAT
    MOVED (``status_seam.apply_status_change``), so a row the owner settled and
    then set back to Projected is mutable to this sweep AND still carries a
    ``settled_basis_id``.  That state is the arm's real subject rather than a
    theoretical one, and holding it is the point: the retained figure is a
    number the owner read off a bank statement, and letting a template edit
    retire the row out from under it would destroy exactly what retention exists
    to keep.  The row is held back as a CONFLICT for the owner to resolve.

    Args:
        existing: The rows this pass is considering.

    Returns:
        The subset of their ids that hold purchases, a note, or a settlement
        record of their own -- which, by the implication above, is also every
        row that names a statement.
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
        elif row.settled_basis_id is not None:
            holding.add(row.id)
    return holding




def _rows_the_definition_reattributes(existing, account_id) -> "set[int]":
    """Return the ids of rows whose ACCOUNT this template has moved.

    Half of what :func:`_recurrence_common.classify_maintain_work` needs and
    cannot ask for itself: a transaction has ONE account and a transfer has two,
    so "the definition moved where this row's records are filed" is a question
    about the model rather than about maintenance (plan step R10-b).

    Applying such a move is what the retention rule refuses on a row holding
    records: ``fk_transaction_entries_parent_account`` binds a purchase's
    account to its parent's, so moving the row drags every purchase onto the new
    account, and ``fk_transaction_entries_reconciled_by`` scopes a clearing link
    BY ACCOUNT, so the statement link the purchases carry is invalidated by the
    same edit.  A row carrying NOTHING follows its template's account freely,
    which is the ordinary case and the behaviour every earlier version had.

    Args:
        existing: The rows this pass is considering.
        account_id: The template's account NOW.

    Returns:
        The subset of their ids sitting on a different account.
    """
    return {row.id for row in existing if row.account_id != account_id}




def _apply_maintain_work(work, derived, template_id, scenario_id, projected_id):
    """Write one classified maintain pass, and reconcile what it moved.

    The only writer in the maintain path.  Order is load-bearing in one place:
    the ledger reconcile runs AFTER the flush, so each updated row's postings
    are reconciled against the amount and account the flush actually stored.

    Args:
        work: The :class:`~app.services._recurrence_common.MaintainWork`
            from :func:`~app.services._recurrence_common.classify_maintain_work`.
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




def _maintain_instances(template, plan, calendar, scenario_id, existing):
    """Resolve and apply everything one regeneration does to a template's rows.

    The body of :func:`regenerate_for_template`, split out so the orchestrator
    reads as ownership -> plan -> maintain -> report.  It read
    "ownership -> bound -> plan" until pay-calendar plan step C2-f3c deleted
    the bound-resolution step (``_recurrence_common.regeneration_bound``).  Runs in four
    steps: refuse an unstorable cadence, derive what the definition says for
    every period the rule names, classify each existing row against that, then
    write.

    Args:
        template: The updated TransactionTemplate.
        plan: The :class:`GenerationPlan` for this pass, or ``None`` when the
            template's recurrence was CLEARED -- which names no period, so
            every row is considered for retirement.
        calendar: The owner's
            :class:`~app.services.pay_calendar.PayCalendar`, which is all this
            reads off the pass -- the WRITE WINDOW has already done its work in
            the plan and in *existing*.  Taking the whole
            ``GenerationSchedule`` for one field is the shape pay-calendar plan
            step C2-f3c removed from ``_amounts._derive_row_fields``, and an
            adversarial review of that step found it still here.
        scenario_id: The scenario being maintained.
        existing: Every row of this template in the pass's WRITE WINDOW at
            or after its bound.  The window half is the load-bearing one:
            it is what keeps this domain a superset of the plan's, and so
            what makes the RETIRE branch reachable.

    Returns:
        The :class:`~app.services._recurrence_common.MaintainOutcome` for
        the audit event and the conflict
        raise.
    """
    placements = plan.placements if plan is not None else ()
    refuse_repeats_this_pass(template, placements, existing)

    salary_profile = _get_salary_profile(template)
    derived = {
        placement.period.period_id: _derive_row_fields(
            template, plan.rule, salary_profile, placement.period, calendar,
        )
        for placement in placements
    }
    work = classify_maintain_work(
        existing, set(derived),
        with_records=_rows_holding_owner_records(existing),
        reattributed=_rows_the_definition_reattributes(
            existing, template.account_id,
        ),
    )
    created, updated = _apply_maintain_work(
        work, derived, template.id, scenario_id,
        plan.projected_id if plan is not None else None,
    )
    return MaintainOutcome.after(work, created, updated)
