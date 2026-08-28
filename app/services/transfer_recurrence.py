"""
Shekel Budget App -- Transfer Recurrence Engine

Parallel to the ``recurrence_engine`` package but generating Transfer records
instead of Transaction records.  The model-agnostic halves of the two engines
(the gating + occurrence-matching preamble via
``recurrence_engine.resolve_generation_plan``, the per-period skip predicate,
the regenerate fetch, the whole maintain DECISION, and the cross-user audit
logging) are shared through that package and
``app/services/_recurrence_common.py`` so the two cannot drift.

**A regeneration MAINTAINS the transfers it already generated** (plan step
R10-b, ruling **R-R19**), which is what the transaction engine started doing at
plan step R10-a.  Until then this engine hard-deleted every non-overridden
generated transfer in the window and built replacements, taking each one's two
shadow rows with it through ``transactions.transfer_id``'s CASCADE.

Measured on a production clone, one routine edit per live recurring template:
99 transfers and 198 shadow rows destroyed and rebuilt with new ids -- to write
values IDENTICAL to the ones already there, on every one of them.  What the
rebuild could not rebuild was the owner's:

  - a transfer's ``notes``, which ``create_transfer`` never receives;
  - a settlement RECORD retained through a revert.  ``status_seam`` releases the
    assertion (``settled_on``, ``reconciled_by_id``) and deliberately KEEPS what
    moved (``settled_amount``, ``settled_basis_id``), because the full-edit
    popover tells the owner to revert in order to edit -- so a reverted transfer
    is Projected, not overridden, and the sweep deleted it.  Reproduced through
    this code path: a `$321.45` figure read off a bank statement, gone with no
    prompt.

That is finding **N-292**'s shape on this table.  It is unreachable now: a row
the rule still names is never deleted, and a row it stops naming is RETAINED as
a conflict the moment the owner has records against it.

Key differences from transaction recurrence:
  - No salary linkage.
  - Single amount column (no estimated/actual split).
  - Simpler amount logic: always uses template.default_amount.
  - EVERY write goes through ``transfer_service`` -- create, update and delete
    alike -- so the two shadow transactions move with their parent atomically
    (``CLAUDE.md`` Transfer Invariants 1-5).  That is also why the maintain pass
    sends only the fields that actually DIFFER: the update door reconciles the
    posting ledger and emits an audit row per call, so a pass that changes
    nothing must call it for nothing.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import NamedTuple

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services._recurrence_common import (
    MaintainOutcome,
    TemplateRowSelector,
    check_scenario_ownership,
    PlacedRow,
    classify_maintain_work,
    occurrences_to_write,
    log_resource_access_denied,
    rows_this_pass_may_maintain,
)
from app.services.recurrence_engine import compute_due_date, resolve_generation_plan
from app.services import transfer_service
from app.exceptions import RecurrenceConflict
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_RECURRENCE_CONFLICTS_RESOLVED,
    EVT_TRANSFER_RECURRENCE_GENERATED,
    EVT_TRANSFER_RECURRENCE_REGENERATED,
    log_event,
)

logger = logging.getLogger(__name__)


def _selector(template, scenario_id):
    """Name what this engine's row fetches are asking about.

    The transfer engine's half of :class:`TemplateRowSelector`, mirroring
    ``recurrence_engine._generate._selector`` exactly: the two engines differ
    in the mapped class and the template foreign-key column and in nothing
    else, so each names its pair ONCE and every shared fetch takes the value.

    Args:
        template: The TransferTemplate this pass is generating from.
        scenario_id: The scenario being written into.

    Returns:
        The :class:`~app.services._recurrence_common.TemplateRowSelector`.
    """
    return TemplateRowSelector(
        Transfer, Transfer.transfer_template_id, template, scenario_id,
    )


class DerivedTransferFields(NamedTuple):
    """What a template and a pay period DERIVE on a generated transfer.

    **THE one statement of which columns a generated transfer takes from its
    DEFINITION rather than from its owner**, and the reason
    :func:`regenerate_for_template` no longer destroys the rows it maintains.
    The twin of ``recurrence_engine.DerivedRowFields`` one table over, and it
    exists for the identical reason (plan steps R10-a and R10-b).

    Both write paths consume it: :func:`generate_for_template` splats it into a
    :class:`~app.services.transfer_service.TransferSpec`, and the maintain pass
    diffs it against an existing row and sends what differs to
    :func:`app.services.transfer_service.update_transfer`.  So a column added
    here is written on a NEW transfer and kept current on an EXISTING one from
    the same edit.

    **All six fields are writable through ONE door, and two of them were not
    until plan step R10-b.**  ``update_transfer`` accepted amount, name,
    category and due date; the two ACCOUNTS it could not express, so a
    definition's account change reached its generated rows only by deleting and
    re-creating them -- and the same gap made the non-repeating propagation
    refuse an account change outright.  The door moves a transfer between
    accounts now (:mod:`app.services.transfer_service._endpoints`), which is
    what lets this class state the whole definition rather than the part that
    happened to be writable.

    Every field here is derived and none is the owner's, which is what makes
    overwriting one on an existing row safe.  The columns that decide whether
    the row is the RULE's at all -- ``is_override``, ``is_deleted`` and
    ``status_id`` -- are deliberately absent: they are the classification the
    caller applies BEFORE deciding to write, never something a write restates.
    ``notes`` is absent for the opposite reason: it is the owner's, no
    definition states one, and destroying it is half of what this step fixes.

    **``amount_source_id`` is absent, and that is the transfer half of ledger
    row N-293.**  ``ck_transfers_amount_ownership`` pairs it with ``amount`` --
    exactly one of the two is ever set -- and ``update_transfer``'s amount arm
    clears it, so once plan step **X-au-f** empties ``amount`` for generated
    transfers this diff would see ``None != default_amount``, send the amount,
    and silently UN-derive the row.  No writer sets that column today, so the
    state is unreachable; X-au-f is the step that creates it and the step that
    owns this field's semantics.

    Attributes:
        from_account_id: The account the money leaves, from the template.
        to_account_id: The account it arrives at.  With *from_account_id* it is
            the one derived pair whose change is not always applicable: both
            shadows live on these accounts and a settled leg's records are filed
            against them, so :func:`_rows_the_definition_reattributes` routes a
            move on a record-holding row to the owner as a RETAINED conflict
            instead of applying it.
        name: The template's name.  The parent's only; each shadow's display
            name is derived from the ENDPOINTS and re-derived by the door that
            moves them.
        category_id: The template's category, or ``None``.
        amount: The template's ``default_amount``.  A transfer has one amount
            column, where a transaction splits estimated from settled.
        due_date: Derived from the rule and the period by
            :func:`~app.services.recurrence_engine.compute_due_date`.
    """

    from_account_id: int
    to_account_id: int
    name: str
    category_id: int | None
    amount: Decimal
    due_date: date | None


def _derive_row_fields(template, rule, period) -> DerivedTransferFields:
    """Resolve what *template* and *period* derive on a generated transfer.

    The single producer of :class:`DerivedTransferFields`, so the create path
    and the maintain path cannot disagree about what a generated transfer's
    definition says.

    **It takes no salary profile and no calendar**, which is the whole of what
    it does not share with ``recurrence_engine._amounts._derive_row_fields``: a
    transfer moves a stated figure between two of the owner's own accounts, so
    nothing here is priced from a paycheck.

    Args:
        template: The
            :class:`~app.models.transfer_template.TransferTemplate` being
            generated from.
        rule: The template's recurrence rule, already confirmed present by
            :func:`~app.services.recurrence_engine.resolve_generation_plan`
            (``GenerationPlan.rule``).
        period: The :class:`~app.services.pay_calendar.DerivedPeriod` this row
            lives in, straight off its ``PlannedOccurrence``.

    Returns:
        The :class:`DerivedTransferFields` for this (template, period) pair.
    """
    return DerivedTransferFields(
        from_account_id=template.from_account_id,
        to_account_id=template.to_account_id,
        name=template.name,
        category_id=template.category_id,
        amount=template.default_amount,
        due_date=compute_due_date(rule, period),
    )


def _derive_unruled_fields(template, xfer) -> DerivedTransferFields:
    """What a RULE-LESS template's definition says about the transfer it made.

    FIVE of the six columns.  A template with no recurrence places no
    occurrence, so it states no DUE DATE and the row keeps its own -- which is
    expressed by deriving that field FROM the row rather than by carrying a
    shorter tuple, so there is still exactly one statement of what a generated
    transfer's definition says.

    Args:
        template: The rule-less
            :class:`~app.models.transfer_template.TransferTemplate`.
        xfer: The transfer it materialised, which supplies the one field the
            definition does not state.

    Returns:
        The :class:`DerivedTransferFields` this definition says about *xfer*.
    """
    return DerivedTransferFields(
        from_account_id=template.from_account_id,
        to_account_id=template.to_account_id,
        name=template.name,
        category_id=template.category_id,
        amount=template.default_amount,
        due_date=xfer.due_date,
    )


def propagate_to_unruled_template(template, transfers) -> "list[int]":
    """Apply a RULE-LESS template's definition to the transfers it created.

    **The maintain pass's decision, for the one shape that does not
    regenerate.**  A transfer template with no recurrence rule still moves
    money exactly once, and its single Transfer is materialised at create time
    rather than generated -- so a regeneration would find no rule, name no
    period, and RETIRE the row (that is defect **D16**, and why the route gates
    the sweep on "the template IS or WAS recurring").  What is left is this: the
    row is never retired, and everything else is the same rule.

    **It exists because that "everything else" was NOT the same rule, and an
    adversarial review of plan step R10-b measured the gap.**  The route applied
    the definition unconditionally, so once the two ACCOUNTS became propagable
    a non-repeating transfer holding a retained settlement record had its pair
    moved in silence -- while the identical edit on a RECURRING template was
    retained and reported.  That is the very inconsistency the step removed,
    re-created in the opposite direction.  Both paths ask
    :func:`_rows_holding_owner_records` and
    :func:`_rows_the_definition_reattributes` now.

    **And only the fields that DIFFER are sent**, for the reason
    :func:`_apply_maintain_work` gives -- plus one this path has alone: sending
    ``to_account_id`` on every edit re-grades ruling **R-C** against the
    destination loan's origination, so a rename would start failing on a
    legacy pre-origination row that
    :func:`._loan_posting._reject_installment_move_before_loan` deliberately
    leaves editable in every other respect.

    Args:
        template: The updated rule-less TransferTemplate, its new field values
            already applied and flushed.
        transfers: Its live, rule-owned transfers -- projected, not overridden
            and not soft-deleted, which the caller selects.

    Returns:
        The ids this pass RETAINED: rows whose accounts the definition moved
        and which carry the owner's own records, left exactly as found.

    Raises:
        NotFoundError: From ``transfer_service.update_transfer``.
        ValidationError: From ``transfer_service.update_transfer``.
    """
    with_records = _rows_holding_owner_records(transfers)
    reattributed = _rows_the_definition_reattributes(transfers, template)
    retained = []
    for xfer in transfers:
        if xfer.id in reattributed and xfer.id in with_records:
            retained.append(xfer.id)
            continue
        changed = {
            field: value
            for field, value in _derive_unruled_fields(
                template, xfer,
            )._asdict().items()
            if getattr(xfer, field) != value
        }
        if changed:
            transfer_service.update_transfer(
                xfer.id, template.user_id, **changed,
            )
    return retained


def generate_for_template(template, schedule, scenario_id, effective_from=None):
    """Generate transfers for a template across a pay-period window.

    Args:
        template:       A TransferTemplate with a loaded recurrence_rule.
        schedule:       The owner's
                        :class:`~app.services.generation_schedule.GenerationSchedule`
                        -- their whole pay-period schedule plus the window this
                        pass may write into.
        scenario_id:    The scenario to generate into.
        effective_from: Optional date -- only generate for periods ending on or
                        after this date.  ``None`` applies no lower bound.

    Returns:
        List of newly created Transfer objects.
    """
    # Resolve the shared gating + occurrence-matching preamble (cross-user
    # defense, rule-present gating, the occurrence walk against the OWNER's
    # schedule, and the narrowing to this pass's window) via the transaction
    # engine's helper -- the transfer engine is a deliberate parallel and must
    # apply the rule identically.  A None result means generate nothing.  See
    # recurrence_engine.resolve_generation_plan.
    plan = resolve_generation_plan(
        template, schedule, scenario_id, effective_from,
        block_message="Blocked cross-user transfer recurrence generation",
    )
    if plan is None:
        return []

    # WHICH occurrences still need a transfer -- the shared decision, so this
    # loop holds only the part that is about ``budget.transfers`` and its
    # shadow pair.  See ``_recurrence_common.occurrences_to_write``.
    created = []
    for placement in occurrences_to_write(
        _selector(template, scenario_id), plan.placements,
    ):
        period = placement.period

        # No existing row -- create one, taking every derived column from the
        # ONE statement of them (:class:`DerivedTransferFields`), which the
        # maintain pass assigns onto an existing row from the same definition.
        # The four fields below that are NOT in it say what this row IS rather
        # than what the template says: whose it is, where it sits, that it is
        # the rule's own row, and that it is not yet an actual event.
        #
        # The due date inside comes from ``recurrence_engine.compute_due_date``,
        # the same shared helper the transaction engine uses: a rule with a
        # day_of_month (monthly, quarterly, and -- via
        # routes/loan/payment_transfer.py -- the mortgage payment, whose rule
        # carries day_of_month=payment_day) yields that calendar day placed in
        # the period's month, so the calendar/dashboard match the loan card's
        # true monthly due date.  Rules without one (every-paycheck, every-N)
        # fall back to period.start_date inside the helper.
        created.append(_create_from_definition(
            _derive_row_fields(template, plan.rule, period),
            template, PlacedRow(period.period_id, placement.occurrence),
            scenario_id, plan.projected_id,
        ))

    db.session.flush()
    log_event(
        logger, logging.INFO, EVT_TRANSFER_RECURRENCE_GENERATED, BUSINESS,
        "Transfers generated from template",
        user_id=template.user_id,
        template_id=template.id,
        scenario_id=scenario_id,
        count=len(created),
    )
    return created


def regenerate_for_template(template, schedule, scenario_id, effective_from=None):
    """Bring a template's future transfers into line with its definition.

    Run when a recurring transfer's amount, accounts, schedule or fields change.
    **It MAINTAINS the rows the rule still names rather than destroying and
    rebuilding them** (plan step R10-b, ruling **R-R19**), which is what the
    transaction engine's twin has done since plan step R10-a.

    Three outcomes, one per period the pass considers:

      1. the rule names the period and an auto-generated transfer is there --
         the fields that DIFFER from :class:`DerivedTransferFields` are applied
         through ``transfer_service.update_transfer``, which carries both shadow
         rows and reconciles the ledger;
      2. the rule names the period and nothing is there -- a transfer and its
         two shadows are created, exactly as :func:`generate_for_template`
         would;
      3. the rule NO LONGER names the period -- the transfer is removed through
         ``transfer_service.delete_transfer`` if it carries nothing, and
         RETAINED as a conflict if the owner has records against it.

    Overridden and soft-deleted rows are conflicts wherever they sit, as
    before; immutable rows are never touched.

    **What the old sweep destroyed, and it was not only churn.**  It hard-deleted
    every non-overridden generated transfer in the window and re-created it.  A
    transfer holds no purchases -- ``entry_service`` refuses a transfer row --
    which is why this leaf came after the transaction one, but two owner-held
    facts still went with each deletion: the transfer's ``notes``, which
    ``create_transfer`` never receives, and a settlement RECORD retained through
    a revert (``settled_amount`` + ``settled_basis_id``, kept by the status seam
    while the assertion is released, because the full-edit popover tells the
    owner to revert in order to edit).  Both were reproduced through this code
    path on a production clone, destroyed by a rename with no prompt and a
    conflict count of zero.

    **Maintaining is not a behaviour change for a transfer with nothing on it,
    and that is measured rather than hoped for.**  ``Projected`` is the ONLY
    non-immutable status in ``ref.statuses``, so this pass can reach nothing
    else, and an update writes exactly the values a re-creation would have
    written -- on a production clone all four live recurring templates came back
    with ZERO fields differing across 99 sweepable rows, which is 99 rows the
    old sweep destroyed and rebuilt to write what was already there.

    **Only the fields that actually differ are sent, and the pass is silent when
    none do.**  ``update_transfer`` is a heavier door than an ORM assignment: it
    reconciles the posting ledger, re-derives a loan's genesis when either
    endpoint is one, bumps the pair's optimistic-lock counter and emits an audit
    row.  On the two live loan-payment templates the old sweep spent 1.58 s and
    1.14 s doing that work twice per row -- once to reverse the deleted payment
    and once to post its replacement -- for an edit that moved no figure at all.

    Args:
        template:       The updated TransferTemplate.
        schedule:       The owner's
                        :class:`~app.services.generation_schedule.GenerationSchedule`.
        scenario_id:    The target scenario.
        effective_from: Date from which to maintain, or ``None`` for the whole
                        write window.  The row select and the rule take it
                        unchanged, and read it against the same derived end --
                        see ``recurrence_engine.regenerate_for_template``.

    Returns:
        List of newly created Transfer objects.  Rows this pass UPDATED are not
        in it, so the value keeps the meaning every caller already reads it
        with.

    Raises:
        RecurrenceConflict: When rows exist that this pass must not change
            unasked.  The caller should catch it, present the options, and call
            :func:`resolve_conflicts`.
    """
    # Defense-in-depth, and it also DISAMBIGUATES the plan below: a ``None``
    # plan means either "not your scenario" or "this template no longer
    # recurs", and those want opposite answers -- do nothing, versus retire
    # every row the vanished rule used to name.  Asking ownership here leaves
    # the plan's ``None`` meaning exactly one thing.  The transaction engine's
    # twin carries the same two calls for the same reason.
    if not check_scenario_ownership(
        logger, template, scenario_id,
        block_message="Blocked cross-user transfer recurrence regeneration",
    ):
        return []

    plan = resolve_generation_plan(
        template, schedule, scenario_id, effective_from,
        block_message="Blocked cross-user transfer recurrence regeneration",
    )
    existing = rows_this_pass_may_maintain(
        _selector(template, scenario_id), schedule, effective_from,
    )

    outcome = _maintain_instances(template, plan, scenario_id, existing)
    db.session.flush()

    # ONE event per pass, and it gained two fields while another LEFT -- the
    # transaction engine's twin records the identical change at plan step R10-a.
    # This used to delegate its create half to ``generate_for_template``, so
    # every template edit emitted ``EVT_TRANSFER_RECURRENCE_GENERATED`` as well;
    # the maintain pass creates transfers itself, so it no longer does.
    # ``updated_count`` and ``retained_conflict_count`` are new, and
    # ``deleted_count`` now counts only rows the rule STOPPED naming -- under
    # the old shape it counted every non-overridden row in the window, and its
    # twin ``created_count`` counted the same rows again.  A reader comparing
    # forensics across this step must not treat the two as the same number.
    #
    # Pylint: ``duplicate-code`` -- the regenerate audit-log + conflict-raise
    # tail.  This is the parallel twin of
    # ``recurrence_engine._maintain.regenerate_for_template``: the
    # model-agnostic core (ownership, the plan, the row fetch, the whole
    # maintain DECISION) is shared through ``_recurrence_common``; what remains
    # is the per-engine tail, which differs only in the audit event constant and
    # its message.  Extracting it into a shared log helper was tried and
    # REVERTED (plan.md Phase 2 working note #3): one param per ``log_event``
    # field trips ``too-many-arguments`` and -- because the helper call site
    # re-duplicates the identical kwargs -- dissolves no cluster.  Documented
    # one-sided disable instead; the partner engine stays un-disabled.
    # pylint: disable=duplicate-code
    log_event(
        logger, logging.INFO, EVT_TRANSFER_RECURRENCE_REGENERATED, BUSINESS,
        "Transfer recurrence regenerated for template",
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
    # pylint: enable=duplicate-code




def _rows_holding_owner_records(existing) -> "set[int]":
    """Return the ids of transfers carrying content their template did not put there.

    "The owner's own records" is what finding **N-292** is about, and the
    transfer table's answer differs from the transaction table's in both
    directions.  A transfer holds no PURCHASES -- ``entry_service.create_entry``
    refuses a transfer row outright -- which is why this engine was not part of
    plan step R10-a.  What it does hold is:

      - ``notes`` on the parent: free text the owner typed, which no writer
        derives and which ``create_transfer`` is never handed, so every
        delete-and-recreate dropped it silently;
      - a SETTLEMENT RECORD on either leg.  ``status_seam.apply_status_change``
        releases the ASSERTION on the way out of the settled band
        (``settled_on``, ``reconciled_by_id``) and deliberately KEEPS what moved
        (``settled_amount``, ``settled_basis_id``), because the two are
        different facts with different lifetimes (plan step X-au-c3).  So a
        transfer the owner settled and then reverted in order to edit -- which
        is what the full-edit popover instructs -- is Projected, not overridden,
        and mutable to this sweep while still recording a figure read off a bank
        statement.  Reproduced on a production clone: `$321.45` on both legs,
        destroyed by a rename.
    **A STATEMENT LINK needs no condition of its own, and that is MEASURED
    rather than reasoned.**  ``reconciled_by_id`` records which statement was
    seen to show a leg's money, and it is what makes an ACCOUNT move unsafe
    (``fk_transactions_reconciled_by`` scopes the link BY ACCOUNT) -- so it
    belongs in this answer.  It is already in it: two CHECK constraints chain
    into an implication.  ``ck_transactions_cleared_needs_settle_day`` says a
    link needs a settle day and ``ck_transactions_settle_day_needs_a_record``
    says a settle day needs a RECORD OF WHAT MOVED, so ``reconciled_by_id IS
    NOT NULL`` implies ``settled_basis_id IS NOT NULL`` and the settlement arm
    above already catches every linked row.  Verified against PostgreSQL rather
    than argued: clearing the FIGURE's basis on a linked row is refused with
    *"violates check constraint ck_transactions_settle_day_needs_a_record"*.
    **That constraint is about the figure and NOT about the day's own basis**,
    which plan step X-az added one column over as
    ``ck_transactions_settle_day_basis_pairing``; the constraint's previous
    name did not say so, which is why X-az renamed it.  The DAY's basis needs no
    arm of its own here either, and for a stronger reason: its pairing is a
    BICONDITIONAL over ``settled_on``, so any row this predicate can see
    carries it.  A third condition
    would be one no row can satisfy alone -- untestable by construction, and
    the kind of guard this project has repeatedly found sitting green over
    nothing.  If either CHECK is ever dropped, this paragraph is what says the
    arm has to come back.

    **Both legs are asked in ONE query, not one per row.**  A regeneration
    considers every future transfer of a template -- 62 of them on one live
    template on a production clone -- so reading each transfer's shadows in the
    classifier would issue a query per row on the hot path of every template
    edit.

    Args:
        existing: The transfers this pass is considering.

    Returns:
        The subset of their ids that hold a note, or a settlement record on
        either leg -- which, by the implication above, is also every row whose
        leg names a statement.
    """
    ids = [xfer.id for xfer in existing]
    if not ids:
        return set()
    holding = {
        transfer_id
        for (transfer_id,) in db.session.query(Transaction.transfer_id)
        .filter(
            Transaction.transfer_id.in_(ids),
            Transaction.settled_basis_id.isnot(None),
        )
        .distinct()
    }
    for xfer in existing:
        # ``notes`` is free text the owner typed and no writer derives; a
        # whitespace-only note is not a record worth blocking an edit over.
        if xfer.notes is not None and xfer.notes.strip():
            holding.add(xfer.id)
    return holding




def _rows_the_definition_reattributes(existing, template) -> "set[int]":
    """Return the ids of transfers whose ENDPOINTS this template has moved.

    The transfer half of what
    :func:`_recurrence_common.classify_maintain_work` needs and cannot ask for
    itself.  A transaction has ONE account; a transfer has two, and both shadow
    rows live on them -- so "the definition moved where this row's records are
    filed" is a question about the PAIR.

    The move itself is applicable: ``transfer_service.update_transfer`` carries
    a transfer and both legs between accounts (plan step R10-b), and a transfer
    carrying nothing of the owner's follows its definition freely, which is the
    ordinary case and the behaviour the old sweep had.  What is NOT applicable
    is doing it to a row that RECORDS something: a settled leg's figure is what
    moved between the OLD two accounts, and its statement link is scoped by
    account (``fk_transactions_reconciled_by``), so re-pointing the pair would
    re-file both against accounts nobody asserted them on.  Those rows are
    retained and reported instead.

    Args:
        existing: The transfers this pass is considering.
        template: The TransferTemplate, holding its endpoints NOW.

    Returns:
        The subset of their ids sitting on a different pair of accounts.
    """
    endpoints = (template.from_account_id, template.to_account_id)
    return {
        xfer.id for xfer in existing
        if (xfer.from_account_id, xfer.to_account_id) != endpoints
    }




def _create_from_definition(
    fields, template, placed, scenario_id, projected_id,
):
    """Create one transfer and its two shadows from *fields*.

    The ONE create in this engine, shared by :func:`generate_for_template` and
    the maintain pass's create arm so the two cannot come to build a generated
    transfer two ways.  Routed through ``transfer_service.create_transfer`` so
    the pair is established atomically (Transfer Invariants 1 and 3-5).

    Args:
        fields: The :class:`DerivedTransferFields` for this (template, period)
            pair.
        template: The TransferTemplate the row is linked to.
        placed: The :class:`~app.services._recurrence_common.PlacedRow` -- which
            paycheck funds this row and which occurrence it answers.  It is ONE
            argument rather than two so neither call site can pair a period with
            another period's occurrence, and so this door stays inside pylint's
            five-argument ceiling (plan step **R17**).
        scenario_id: The scenario it is written into.
        projected_id: The ``Projected`` status id.

    Returns:
        The created :class:`~app.models.transfer.Transfer`.
    """
    return transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=template.user_id,
            pay_period_id=placed.period_id,
            occurs_on=placed.occurs_on,
            scenario_id=scenario_id,
            status_id=projected_id,
            transfer_template_id=template.id,
            **fields._asdict(),
        ),
    )




def _apply_maintain_work(work, derived, template, scenario_id, projected_id):
    """Write one classified maintain pass, through the transfer service.

    The only writer in this engine's maintain path, and every one of its three
    arms goes through ``transfer_service``: ``CLAUDE.md`` Transfer Invariant 4
    says no code path mutates a shadow directly, and a maintain pass that
    assigned to a parent's columns would leave both shadows behind.  That is the
    difference from the transaction twin's applier, which assigns fields itself
    -- an ordinary row has no pair to keep equal.

    **One field's derived value can disagree with what the CREATE path stored,
    and it is bounded rather than guarded** (adversarial review of R10-b).
    ``create_transfer`` defaults a row's name from the endpoints when the spec
    states none (``spec.name or ...``), so a template named ``""`` would produce
    a row named "A to B" and this diff would send ``name=""`` on every pass
    forever -- a full ``update_transfer`` per row, per edit, permanently.  Both
    transfer-template schemas bound ``name`` at ``Length(min=1)`` and the column
    is NOT NULL, so no door can author it; the column carries no CHECK, which is
    what would make it structural.

    **The update arm sends the fields that DIFFER, and skips a row entirely when
    none do.**  ``update_transfer`` reconciles the posting ledger, re-derives a
    loan's genesis when an endpoint is one, moves the pair's optimistic-lock
    counter and emits an audit row, so calling it for a row already equal to its
    definition is not a cheap no-op -- it is all of that work, per row, for
    nothing.  On a production clone every one of the 99 sweepable rows across
    the four live templates came back with zero fields differing.

    **A loan-payment update can move the rule's own closing bound mid-pass, and
    that is a NOTE rather than a defect** (adversarial review of R10-b).
    ``update_transfer`` on a loan payment reaches
    ``loan_recurrence_sync.sync_recurring_payment_bounds``, which re-authors
    ``rule.end_date`` from the newly derived payoff -- while *work* and *derived*
    were frozen before the first write.  So raising a loan payment's amount
    (payoff moves earlier) lets this pass finish against the bound it started
    with.  It self-heals on the next regeneration, and it is strictly better
    than what it replaces: the old sweep DELETED every payment first, derived a
    payoff from zero payments, and left the rule unbounded.  Plan step **R7d**
    turns that window into a resolver and should know this.

    Args:
        work: The :class:`~app.services._recurrence_common.MaintainWork` from
            :func:`~app.services._recurrence_common.classify_maintain_work`.
        derived: ``{pay_period_id: DerivedTransferFields}`` for every period the
            rule names -- the single statement of what a generated transfer's
            definition says, consumed identically by the update and the create.
        template: The TransferTemplate being maintained; supplies the owner
            every service call is checked against, and the link every created
            row carries.
        scenario_id: The scenario every created row is written into.
        projected_id: The ``Projected`` status id for created rows.  ``None``
            only when the rule was cleared, in which case *work.create_in* is
            empty and it is never read.

    Returns:
        ``(created, updated)`` -- the rows this pass added and the rows it
        brought into line.
    """
    updated = []
    for xfer in work.update:
        changed = {
            field: value
            for field, value in derived[xfer.occurs_on]._asdict().items()
            if getattr(xfer, field) != value
        }
        if not changed:
            continue
        transfer_service.update_transfer(xfer.id, template.user_id, **changed)
        updated.append(xfer)

    created = [
        _create_from_definition(
            derived[create.occurs_on], template, create,
            scenario_id, projected_id,
        )
        for create in work.create_in
    ]

    # **This is the ONLY path here that deletes**, and it is reached only when
    # the rule stopped naming the row's OCCURRENCE (plan step R17) AND the
    # transfer carries nothing
    # of the owner's.  Routed through the canonical hard-delete path (Transfer
    # Invariant 4): ``delete_transfer`` reverses any posted effect while the
    # rows still exist to link against, takes the loan-payment split back,
    # runs the orphan-verification self-check and emits
    # ``EVT_TRANSFER_HARD_DELETED`` per deletion.  See audit B6-03 / LOW-02.
    for xfer in work.retire:
        transfer_service.delete_transfer(xfer.id, template.user_id, soft=False)

    db.session.flush()
    return created, updated




def _maintain_instances(template, plan, scenario_id, existing):
    """Resolve and apply everything one regeneration does to a template's rows.

    The body of :func:`regenerate_for_template`, split out so the orchestrator
    reads as ownership -> plan -> maintain -> report.  Runs in four steps:
    refuse an unstorable cadence, derive what the definition says for every
    period the rule names, classify each existing row against that, then write.

    Args:
        template: The updated TransferTemplate.
        plan: The :class:`~app.services.recurrence_engine.GenerationPlan` for
            this pass, or ``None`` when the template's recurrence was CLEARED --
            which names no period, so every row is considered for retirement.
        scenario_id: The scenario being maintained.
        existing: Every transfer of this template in the pass's WRITE WINDOW at
            or after its bound.  The window half is the load-bearing one: it is
            what keeps this domain a superset of the plan's, and so what makes
            the RETIRE branch reachable.

    Returns:
        The :class:`~app.services._recurrence_common.MaintainOutcome` for the
        audit event and the conflict raise.
    """
    placements = plan.placements if plan is not None else ()
    # Keyed by the OCCURRENCE; see the transaction engine's twin for the
    # KeyError and the wrong-paycheck derivation this closes.
    derived = {
        placement.occurrence: _derive_row_fields(
            template, plan.rule, placement.period,
        )
        for placement in placements
    }
    work = classify_maintain_work(
        _selector(template, scenario_id), existing, placements,
        with_records=_rows_holding_owner_records(existing),
        reattributed=_rows_the_definition_reattributes(existing, template),
    )
    created, updated = _apply_maintain_work(
        work, derived, template, scenario_id,
        plan.projected_id if plan is not None else None,
    )
    return MaintainOutcome.after(work, created, updated)




def resolve_conflicts(transfer_ids, action, user_id, new_amount=None):
    """Resolve override/delete conflicts after a regeneration.

    Routes all mutations through the transfer service so shadow
    transactions are updated atomically.  Soft-deleted transfers are
    restored via ``transfer_service.restore_transfer`` before updating.

    Each transfer is ownership-checked via its direct ``user_id`` column
    before any modification -- transfers not owned by ``user_id`` are
    silently skipped (defense-in-depth against IDOR).

    Args:
        transfer_ids: List of Transfer IDs to resolve.
        action:       'update' -- clear override/delete, apply new amount.
                      'keep' -- leave the transfer unchanged.
        user_id:      The requesting user's ID.  Transfers not owned by
                      this user are skipped.
        new_amount:   The new default amount (required if action='update').
    """
    if action == "keep":
        log_event(
            logger, logging.INFO,
            EVT_TRANSFER_RECURRENCE_CONFLICTS_RESOLVED, BUSINESS,
            "Transfer recurrence conflicts kept (no mutation)",
            user_id=user_id, action=action,
            transfer_id_count=len(transfer_ids),
        )
        return

    if action == "update":
        resolved_count = 0
        skipped_count = 0
        for xfer_id in transfer_ids:
            xfer = db.session.get(Transfer, xfer_id)
            if xfer is None:
                skipped_count += 1
                continue

            # Ownership check: Transfer has a direct user_id column.
            if xfer.user_id != user_id:
                log_resource_access_denied(
                    logger,
                    user_id=user_id,
                    model="Transfer",
                    pk=xfer_id,
                    owner_id=xfer.user_id,
                )
                skipped_count += 1
                continue

            # Soft-deleted transfers must be restored before they can
            # be updated.  restore_transfer sets is_deleted=False on the
            # transfer and both shadows, and verifies invariants.
            if xfer.is_deleted:
                transfer_service.restore_transfer(xfer_id, user_id)

            # Build the update kwargs: clear override flag and apply
            # the new amount if provided.  update_transfer propagates
            # these to both shadow transactions atomically.
            svc_kwargs = {"is_override": False}
            if new_amount is not None:
                svc_kwargs["amount"] = new_amount

            transfer_service.update_transfer(xfer_id, user_id, **svc_kwargs)
            resolved_count += 1

        db.session.flush()
        log_event(
            logger, logging.INFO,
            EVT_TRANSFER_RECURRENCE_CONFLICTS_RESOLVED, BUSINESS,
            "Transfer recurrence conflicts resolved (update)",
            user_id=user_id, action=action,
            resolved_count=resolved_count,
            skipped_count=skipped_count,
            new_amount=str(new_amount) if new_amount is not None else None,
        )
