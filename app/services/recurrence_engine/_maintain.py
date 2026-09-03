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
from app.services import posting_service
from app.services.recurrence_engine._generate import _selector
from app.services.recurrence_engine._amounts import _derive_row_fields
from app.services.recurrence_engine._pass import (
    MaintainActs,
    PassReporting,
    regenerate_definition,
)
from app.utils.log_events import EVT_RECURRENCE_REGENERATED

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
    the DERIVED end, off the same calendar, because plan step **C4-c** dropped the
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
        RecurrenceConflict: When rows exist that this pass must not change
            unasked.  The caller should catch it, present the options, and call
            :func:`resolve_conflicts`.
    """
    return regenerate_definition(
        _PASS, template, schedule, scenario_id, effective_from,
    )




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
    a settle day, ``ck_transactions_settle_day_needs_a_record`` says a settle
    day needs a RECORD OF WHAT MOVED, so ``reconciled_by_id IS NOT NULL`` implies
    ``settled_basis_id IS NOT NULL``.  **That constraint is about the FIGURE's
    basis and not the DAY's** -- plan step X-az added the day's own as
    ``ck_transactions_settle_day_basis_pairing`` and renamed this one, because
    beside it the old name said the opposite of what its predicate says.  The
    ``elif`` that used to follow the settlement arm was therefore reached by no
    row that exists, which is why
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




def _rows_the_definition_reattributes(existing, template) -> "set[int]":
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

    **It takes the TEMPLATE rather than its account id** (plan step
    balance:X-au-d), which is the shape :class:`MaintainActs` names: a transfer
    has two endpoints and cannot be asked this with one id, so the shared
    signature is the definition and each engine reads what it needs off it.

    Args:
        existing: The rows this pass is considering.
        template: The TransactionTemplate, holding its account NOW.

    Returns:
        The subset of their ids sitting on a different account.
    """
    return {
        row.id for row in existing if row.account_id != template.account_id
    }




def _apply_maintain_work(work, derived, template, scenario_id, projected_id):
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
        template: The definition every written row is linked to.  Taken whole
            rather than as an id since plan step balance:X-au-d, because
            :class:`~app.services._recurrence_common.MaintainActs` gives both
            engines' writers one signature and a transfer's needs the template
            itself.  **Plan step ``pay_calendar:C13-a`` needs it whole for a
            second, independent reason**: a created row states its OWNER as
            well as its link, and the template is the one place both are
            known -- so passing the id would put one object's two fields in
            two parameters.  Either reason alone justifies the signature;
            removing one does not license reverting it.
        scenario_id: The scenario every created row is written into.
        projected_id: The ``Projected`` status id for created rows.  ``None``
            only when the rule was cleared, in which case *work.create_in* is
            empty and it is never read.

    Returns:
        ``(created, updated)`` -- the rows this pass added and the rows it
        brought into line.
    """
    # ``occurs_on`` is deliberately absent from this loop, and that is plan
    # step **R17**'s central claim: a row's OCCURRENCE is what it IS, not
    # something its definition re-derives per pass.  It is not in
    # ``DerivedRowFields``, so this splat cannot reach it, and a maintained row
    # therefore keeps the occurrence it was created for.  What a pass should do
    # when a rule EDIT moves the occurrence set out from under an existing row
    # is the question the skip-predicate leaf owns; baking the answer in here
    # would decide it silently.
    #
    # **The AMOUNT travels as one attribute since plan step X-au-k**, and this
    # loop is why that matters.  ``DerivedRowFields`` carries
    # ``amount_ownership``, so ``setattr`` here writes a row's whole ownership
    # or none of it -- where it used to write ``estimated_amount`` alone and
    # abort the entire template edit at flush against
    # ``ck_transactions_amount_ownership`` (finding **N-293**, closed there).
    #
    # **What it writes is a DECLARATION and never a figure** (plan step
    # X-au-e, the condition finding N-437 was closed under).  The field is
    # ``derived_ownership(AmountSourceEnum.TEMPLATE)``, one value rather than
    # a fork, so the splat cannot hand a row back to its owner -- there is no
    # arm here that states a price at all, which is the whole meaning of
    # "maintain" once a definition prices its own rows.  It carried
    # ``own(figure)`` unconditionally until X-au-k, then ``own`` OR ``derived``
    # on ``template_amount_service.owns_its_amount`` until X-au-e; a SILENT
    # hand-back was what the first would have become the moment a cutover
    # derived a row this pass can reach.
    #
    # The rows it can reach are narrower than the fetch:
    # ``_recurrence_common.classify_maintain_work`` routes an IMMUTABLE, an
    # OVERRIDDEN and a soft-DELETED row away from ``work.update`` before this
    # loop sees them, so a figure a human authored is never overwritten here.
    updated = []
    for row in work.update:
        for field, value in derived[row.occurs_on]._asdict().items():
            setattr(row, field, value)
        updated.append(row)

    created = []
    for create in work.create_in:
        txn = Transaction(
            **derived[create.occurs_on]._asdict(),
            # The OWNER sits here rather than in ``DerivedRowFields``, and the
            # loop above is exactly why (plan step ``pay_calendar:C13-a``): it
            # ``setattr``s every derived field onto an EXISTING row, and a row
            # does not change hands because its template was edited.  Same
            # argument as ``occurs_on``'s, stated at the top of this function.
            user_id=template.user_id,
            template_id=template.id,
            pay_period_id=create.period_id,
            occurs_on=create.occurs_on,
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
    # when the rule stopped naming the row's OCCURRENCE (plan step R17; it was
    # the row's PERIOD) AND the row carries nothing of the owner's.
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


#: The acts a regeneration performs that are THIS engine's own -- what
#: :class:`~._pass.MaintainActs` names, and everything about a regeneration
#: that a transaction and a transfer do not share.  The body that performs them
#: is :func:`~._pass.regenerate_definition`, ONE function for both engines
#: since plan step balance:X-au-d -- which is where this module's own copy of
#: it went.
_PASS = MaintainActs(
    PassReporting(
        logger, "Blocked cross-user recurrence regeneration",
        EVT_RECURRENCE_REGENERATED, "Recurrence regenerated for template",
    ),
    _selector, _derive_row_fields, _rows_holding_owner_records,
    _rows_the_definition_reattributes, _apply_maintain_work,
)
