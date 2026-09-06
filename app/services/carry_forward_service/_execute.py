"""Mutating carry-forward execution.

``carry_forward_unpaid`` applies the three-way partition's semantics --
settle-and-roll for envelope rows, move-whole for discrete rows, and
``transfer_service.update_transfer`` for shadows -- as one atomic batch.
The caller owns the surrounding commit; a ``ValidationError`` from the
envelope branch must roll the whole batch back.
"""

import logging
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.transaction import Transaction
from app.services import posting_service, transfer_service
from app.services.amount_ownership import state_own_amount
from app.services.cash_ledger import resolve_transaction_amount
from app.services.row_valuation import purchases_total
from app.utils.balance_predicates import is_projected_clause
from app.utils.log_events import BUSINESS, EVT_CARRY_FORWARD, log_event

from ._context import (
    _build_carry_forward_context,
    _classify_leftover_target,
    _TargetKind,
)

logger = logging.getLogger(__name__)


def carry_forward_unpaid(source_period_id, target_period_id, scenario_id,
                         *, balance_ctx):
    """Carry forward all projected items from source to target period.

    Steps:
      1. Verify both periods are in the owner's pay calendar.
      2. Find every non-deleted, projected transaction in the source
         period that belongs to the specified scenario.
      3. Partition into shadow / envelope / discrete buckets.
      4. Apply each bucket's semantic (settle-and-roll for envelope,
         move-whole for discrete, ``transfer_service`` for shadows).
      5. Return the count of carried items (envelope settle counts
         as 1; discrete move counts as 1; each transfer counts as 1
         regardless of its two shadow rows).

    The caller (typically the carry-forward route) is responsible for
    committing the surrounding transaction.  This service does not
    commit.  It does flush at the end so the route can immediately
    issue follow-up queries.

    Args:
        source_period_id: The pay_period.id to carry forward FROM.
        target_period_id: The pay_period.id to carry forward TO.
            Typically the user's current period.
        scenario_id: The scenario to carry forward within.  Prevents
            cross-scenario data corruption when multiple scenarios
            exist for the same user.
        balance_ctx: The request's
            :class:`~app.services.balance_at.BalanceContext`, opened once by
            the route and threaded down (pay-calendar plan step C2-f3c; plan
            step R7d-c-1 moved it from the calendar to the pass that derives
            one).  It names the owner, so no ``user_id`` rides beside it, and
            a period id that is not in its calendar is not this owner's --
            which is how both periods are ownership-checked.

    Returns:
        int -- the number of carried items (1 per source row processed).

    Raises:
        NotFoundError: If either period is not in *balance_ctx*'s calendar --
            it does not exist, or it is not this owner's.
        ValidationError: On two conditions, either of which fails the WHOLE
            batch -- the caller must rollback the session before issuing any
            follow-up writes.  (a) The envelope branch's ``AMBIGUOUS`` guard: a
            destination period with more than one mutable row for the same
            (template, scenario), a corrupt pre-existing state.  All other
            former block conditions (inactive template, finalised or
            soft-deleted destination) now create a fresh override row instead.
            (b) Since plan step C9b, a carried TRANSFER that is a loan payment
            whose destination period would place its installment at or before
            the loan's origination (``transfer_service.update_transfer``
            enforces ruling R-C on the period move).  The refusal itself is
            correct -- the moved payment would be erased by the fold -- but it
            costs the rest of the batch, which is recorded as a finding rather
            than fixed here: making it skip-and-report is a behaviour change to
            carry-forward's batch semantics, not to the guard.
    """
    ctx = _build_carry_forward_context(
        source_period_id, target_period_id, scenario_id, balance_ctx,
    )
    user_id = ctx.user_id

    if (not ctx.shadow_txns
            and not ctx.envelope_txns
            and not ctx.discrete_txns):
        # Includes the same-period short-circuit and the
        # genuinely-nothing-to-carry case.  No flush needed.
        return 0

    count = 0

    # The discrete and envelope branches both run inside a no_autoflush
    # block so a partially-mutated row (is_override flipped, pay_period
    # not yet flipped, etc.) cannot trigger an autoflush mid-iteration
    # via a downstream lazy-load query.  An autoflush at the wrong
    # moment violates the partial unique generation index (plan step R17
    # split it in two: idx_transactions_template_scenario_occurrence for a
    # row that answers an occurrence, ..._undated for one that does not),
    # even though the FINAL state is index-safe.  See the original 33cd21e fix and
    # docs/carry-forward-aftermath-implementation-plan.md Phase 4.
    with db.session.no_autoflush:
        # ── Discrete branch ────────────────────────────────────────
        # Conditional bulk UPDATE rather than per-row ORM mutation.
        # The Projected predicate in the WHERE clause closes the F-049
        # race: between the SELECT in ``_build_carry_forward_context``
        # and the flush, a concurrent ``mark_done`` (or ``mark_credit``
        # / ``cancel``) request can transition a row out of Projected,
        # and a per-row ``setattr(...)`` followed by a flush would
        # carry a settled row into the target period -- erasing the
        # user's prior status decision.  The bulk UPDATE atomically
        # re-checks the status as a SQL precondition, so race-loser
        # rows are silently left in place (still Paid, still in the
        # source period, untouched by this batch) and the count
        # reflects only the rows that actually moved.  Audit reference:
        # F-049 / commit C-22 of the 2026-04-15 security remediation
        # plan.  Routed through the centralized ``is_projected_clause``
        # (D6-09 / MED-02) so this re-check shares one definition with
        # the source-period SELECT above.
        #
        # Two passes are required because template-linked rows must
        # flip ``is_override = TRUE`` as part of the same SQL UPDATE
        # to keep the row index-safe (both partial unique generation
        # indexes exclude override rows, so flipping the flag and the
        # period together avoids any transient state that could collide
        # with the rule-generated row already in the target period).  Ad-hoc
        # rows (``template_id IS NULL``) sit outside that index in
        # every state and only need the period flip.
        #
        # The ``Transaction.version_id: + 1`` assignment honors the
        # optimistic-lock contract from C-17 / F-009: every UPDATE
        # bumps the counter so any concurrent ORM-level flush against
        # the same row fails its ``WHERE version_id = ?`` and surfaces
        # as ``StaleDataError`` rather than silently overwriting our
        # batch.  ``synchronize_session="fetch"`` issues a SELECT
        # before the UPDATE to identify affected rows and expires
        # those instances in the session so any later access reads
        # fresh values from the database -- preserving the
        # ``no_autoflush`` invariant that the in-memory state never
        # diverges from the database while the loop runs.
        if ctx.discrete_txns:
            template_ids = [
                t.id for t in ctx.discrete_txns if t.template_id is not None
            ]
            adhoc_ids = [
                t.id for t in ctx.discrete_txns if t.template_id is None
            ]

            if template_ids:
                count += (
                    db.session.query(Transaction)
                    .filter(
                        Transaction.id.in_(template_ids),
                        is_projected_clause(Transaction),
                        Transaction.is_deleted.is_(False),
                    )
                    .update(
                        {
                            Transaction.pay_period_id: target_period_id,
                            Transaction.is_override: True,
                            Transaction.version_id: Transaction.version_id + 1,
                        },
                        synchronize_session="fetch",
                    )
                )

            if adhoc_ids:
                count += (
                    db.session.query(Transaction)
                    .filter(
                        Transaction.id.in_(adhoc_ids),
                        is_projected_clause(Transaction),
                        Transaction.is_deleted.is_(False),
                    )
                    .update(
                        {
                            Transaction.pay_period_id: target_period_id,
                            Transaction.version_id: Transaction.version_id + 1,
                        },
                        synchronize_session="fetch",
                    )
                )

        # Envelope branch.  Each iteration settles the source row and
        # rolls the unspent leftover into the destination -- bumping an
        # existing mutable row, generating the canonical, or creating a
        # fresh override row when none exists.  The helper raises
        # ValidationError only on the AMBIGUOUS guard (>1 mutable
        # destination row); the route catches that and rolls back the
        # session for batch atomicity.
        for txn in ctx.envelope_txns:
            _settle_source_and_roll_leftover(
                txn, ctx.target_period, ctx.basis, ctx.schedule,
            )
            count += 1

    # Move transfers via the service.  De-duplicate by transfer_id because
    # the query is not account-scoped and may return both shadows from the
    # same transfer.  Each transfer counts as 1 carried-forward item.
    moved_transfer_ids = set()
    for txn in ctx.shadow_txns:
        if txn.transfer_id not in moved_transfer_ids:
            # The service moves the parent transfer AND both shadows
            # to the target period, even if only one shadow was in
            # the query results.  This self-heals any period mismatch
            # between siblings (design doc section 10A.2).
            transfer_service.update_transfer(
                txn.transfer_id,
                user_id,
                pay_period_id=target_period_id,
                is_override=True,
            )
            moved_transfer_ids.add(txn.transfer_id)
            count += 1

    db.session.flush()

    # ── Posting ledger reconcile (Build-Order Step 3) ──────────────
    # Each envelope source was settled at sum(entries) inside the loop above;
    # post its confirmed cash effect to the double-entry ledger.  Done AFTER the
    # no_autoflush block and its flush -- NOT inside settle_from_entries (which
    # runs inside that block) -- so _emit_balanced_entry's flush lands on the
    # batch's index-safe final state, never mid-loop where a partially-mutated
    # partially-mutated row could violate a generation index.  The reconcile is idempotent and
    # a no-op for the common empty-envelope rollover (effect 0); a
    # partially-spent source posts its debit-only checking outflow.  Only
    # envelope sources need a reconcile here: carry-forward moves only Projected
    # rows, so the transfers relocated above are unsettled and
    # transfer_service.update_transfer posted nothing for them.
    for source_txn in ctx.envelope_txns:
        posting_service.sync_transaction_postings(
            source_txn, settled=source_txn.status.is_settled,
        )
    # **The DISCRETE rows need one too, since plan step X-f3b** (ruling
    # **R-FM**).  They are RELOCATED rather than settled -- two bulk UPDATEs
    # above set ``pay_period_id`` to the target -- and a posting carries the
    # BUDGET column its source row is attributed to, so an ad-hoc ENVELOPE
    # (``is_envelope`` with no template, which ``_context`` routes here
    # deliberately, "moves whole, carrying its entries") would leave its
    # purchases' legs filed under the period it left.  The comment above used to
    # justify skipping them with "carry-forward moves only Projected rows",
    # which was sound while only a settled row held postings and is the same
    # premise ``routes/transactions/mutations`` re-listed ``pay_period_id``
    # for.  Empty-handed for every row whose family never posted, which is every
    # bill and every plain expense in the batch.
    for moved_txn in ctx.discrete_txns:
        posting_service.sync_transaction_postings(
            moved_txn, settled=moved_txn.status.is_settled,
        )

    log_event(logger, logging.INFO, EVT_CARRY_FORWARD, BUSINESS,
              "Carried forward unpaid items",
              user_id=user_id,
              count=count, from_period_id=source_period_id,
              to_period_id=target_period_id,
              envelope_count=len(ctx.envelope_txns),
              discrete_count=len(ctx.discrete_txns),
              transfer_count=len(moved_transfer_ids))
    return count


def _settle_source_and_roll_leftover(source_txn, target_period, basis,
                                    schedule):
    """Settle an envelope source row and roll its leftover into the target.

    Implements the envelope branch of Option F (see
    ``docs/carry-forward-aftermath-design.md``):

      1. Compute ``entries_sum`` as ``sum(e.amount for e in
         source.entries)``.  Empty entries -> ``Decimal("0")`` so the
         full estimated amount rolls forward.
      2. Compute ``leftover = max(Decimal("0"), <the source's RESOLVED
         amount> - entries_sum)``.  Overspend (``entries_sum > budget``)
         clamps to zero -- the actual overspend is recorded on the
         settled source row's settlement record and on its entries.
      3. If ``leftover > 0``, resolve the destination row via
         ``_resolve_or_create_target_row``, which (a) bumps the single
         mutable (Projected) row for ``(template_id, target period,
         scenario_id)`` when one exists, (b) lets
         ``recurrence_engine.generate_for_template`` create the canonical
         when the destination is empty and the template is active there,
         or (c) creates a fresh ``is_override`` row carrying the leftover
         when neither applies (an inactive template, or a destination
         whose only row is finalised or soft-deleted).
         Then bump the resolved row: its own resolved amount plus the
         leftover becomes the row's OWNERSHIP through
         ``amount_ownership.state_own_amount`` -- one act over one attribute
         since plan step X-au-k, where it was a figure write and a separate
         source clear whose PAIRING the CHECK had to catch -- and
         ``is_override`` flips ``True``.  The flip blocks future
         recurrence-engine passes from regenerating the row (verified by
         the ``is_override`` skip clause in
         ``app/services/recurrence_engine.py``).  A freshly created row
         starts at ``Decimal("0")`` so the bump lands it on exactly the
         leftover.  The only refusal is the ``AMBIGUOUS`` guard -- more
         than one mutable destination row, a corrupt pre-existing state.
      4. Settle the source row via
         ``transaction_service.settle_from_entries``.  The helper
         enforces its own preconditions (envelope template,
         non-deleted, mutable status, no transfer_id), all of which
         are already guaranteed by the partitioning in
         ``carry_forward_unpaid``.

    Mutations land on ``source_txn`` and the target row in place; the
    caller owns the session/commit lifecycle.  No flush happens here
    except as a side effect of ``recurrence_engine.generate_for_template``
    when it has to create a canonical (the ``CREATE`` branch only
    ``db.session.add``s, which is acceptable inside the surrounding
    ``no_autoflush`` block because an ``is_override`` row is index-safe
    in every intermediate state).

    Args:
        source_txn: A Projected, non-deleted, envelope-tracked
            transaction in the source period.  Partitioning in
            ``carry_forward_unpaid`` guarantees the preconditions.
        target_period: The target
            :class:`~app.services.pay_calendar.DerivedPeriod`.
        schedule: The request's
            :class:`~app.services.generation_schedule.GenerationSchedule`
            (``ctx.schedule``), passed through to the target-row resolution.
            Passed pre-fetched to avoid a redundant lookup; the
            caller already validated ownership.
        basis: The request's :class:`~app.services.cash_ledger.AmountBasis`
            (``ctx.basis``).  Its ``scenario_id`` scopes the target-row lookup
            and the recurrence-engine call so cross-scenario data is never
            touched, and it prices both ends of the rollover.

    Raises:
        ValidationError: Only on the ``AMBIGUOUS`` guard -- more than one
            mutable destination row for ``(template, period, scenario)``,
            a corrupt pre-existing state the user must resolve manually.
            The error names the source row and target period.
    """
    # Pylint: ``import-outside-toplevel`` -- defer the recurrence-engine
    # import to avoid a circular dependency at module load time:
    # recurrence_engine is service-layer code and this module is
    # service-layer code; importing at top level works in current code
    # but the deferred form documents the intentional one-way dependency
    # (carry-forward depends on recurrence-engine, never the reverse).
    from app.services import recurrence_engine  # pylint: disable=import-outside-toplevel
    # Pylint: ``import-outside-toplevel`` -- same deferred-import
    # rationale: transaction_service is service-layer code reached only
    # from inside this function, so the local import keeps the module's
    # top-level dependency graph free of the service-to-service cycle.
    from app.services import transaction_service  # pylint: disable=import-outside-toplevel

    # Compute leftover BEFORE looking at the target so a downstream
    # validation failure leaves source.entries (and any pending
    # mutations on this row) untouched.  Reading entries triggers a
    # lazy-load SELECT inside no_autoflush, which is safe because
    # this function never mutates entries.
    entries_sum = purchases_total(source_txn.entries)
    # The source's BUDGET, resolved rather than read off the column (plan step
    # X-au-c2b): ruling E-21 fixes an envelope's base on its own amount
    # unconditionally, and a derived row stores none.
    # **The floor is against an OVERSPENT envelope and never against a
    # refunded one** (developer ruling **bank_import:R-IK**, 2026-09-01, plan step
    # ``bank_import:X-gj-2b-3``).  Ruling **bank_import:R-II** made a merchant
    # credit a NEGATIVE purchase, so ``entries_sum`` can be negative and this
    # leftover can EXCEED the budget: an envelope budgeting `$100.00` holding
    # one `-$50.00` refund rolls `$150.00` forward and settles at `-$50.00`,
    # which the two together conserve -- `$100.00` of plan across the two
    # periods.  That is the ruled NET basis and it is what
    # ``entry_service.compute_remaining`` answers for the same row on screen;
    # capping it at the budget was put to the developer with those numbers and
    # refused, because it would make the rollover disagree with the figure the
    # owner reads beside it.
    leftover = max(
        Decimal("0"),
        resolve_transaction_amount(source_txn, basis) - entries_sum,
    )

    # Bump the target only when there is unspent leftover.  Overspend
    # and exact-spend cases (leftover == 0) settle the source without
    # touching the target -- the target's own canonical (or its
    # absence) is irrelevant to a zero rollover, and validating it
    # would fail surprises like a fully-paid target period that the
    # user does not need to mutate.
    if leftover > 0:
        target_row = _resolve_or_create_target_row(
            source_txn, target_period, basis, recurrence_engine,
            schedule,
        )
        # Resolve BEFORE writing: a topped-up row states its own figure from
        # then on, and reading the column instead of resolving it would meet a
        # ``None`` on a derived target.
        #
        # **Releasing the relation is no longer a second statement** (plan step
        # X-au-k).  This read "write the column, then clear the source", two
        # lines whose ORDER did not matter but whose PAIRING did, and a caller
        # that wrote the first without the second was an ``IntegrityError`` at
        # flush.  ``state_own_amount`` assigns one attribute holding one of two
        # shapes, so the release IS the write.
        state_own_amount(
            target_row, resolve_transaction_amount(target_row, basis) + leftover,
        )
        target_row.is_override = True

    transaction_service.settle_from_entries(source_txn)


def _resolve_or_create_target_row(source_txn, target_period,
                                  basis, recurrence_engine, schedule):
    """Return the destination row that receives *source_txn*'s leftover.

    Thin switch over ``_classify_leftover_target`` (the single source of
    truth shared with the preview):

      * ``TOP_UP`` -- exactly one mutable row already exists; return it
        for the caller to bump.
      * ``GENERATE`` -- the destination is empty and the template is
        active there; ask the recurrence engine to create the canonical
        and return it.  A race that leaves nothing falls through to
        ``CREATE`` rather than failing the batch.
      * ``CREATE`` -- no usable row and the engine will not generate one
        (inactive template, or a destination whose only row is finalised
        or soft-deleted); create a fresh override row.
      * ``AMBIGUOUS`` -- more than one mutable row for the same
        ``(template, period, scenario)`` that ``_leftover_recipient``
        cannot choose between: they answer the same occurrence, or one
        answers none.  Since plan step R17 a paycheck may legitimately hold
        several rows of one template (a cadence that names it more than
        once), and those are TOP_UP on the earliest occurrence rather than
        a refusal.  The route catches the ``ValidationError`` and rolls the
        whole batch back.

    The returned row is the caller's to bump (its resolved amount plus the
    leftover, with its ``amount_source_id`` cleared); a freshly created row
    starts at ``Decimal("0")`` so the bump lands it on exactly the leftover.

    Args:
        source_txn: The envelope source row being carried forward.
        target_period: The destination
            :class:`~app.services.pay_calendar.DerivedPeriod`.
        basis: The request's amount basis; its ``scenario_id`` is the scenario
            the rollover stays within.
        recurrence_engine: The recurrence-engine module (passed in to
            avoid a circular import at module top), used for the
            ``GENERATE`` branch's ``generate_for_template`` call.
        schedule: The request's
            :class:`~app.services.generation_schedule.GenerationSchedule`
            (``ctx.schedule``) -- the owner's whole pay-period schedule with
            its write window narrowed to *target_period*.  Threaded rather
            than built here because this runs once per envelope row.

    Returns:
        The Transaction row to bump.

    Raises:
        ValidationError: On the ``AMBIGUOUS`` corrupt-state guard.
    """
    resolution = _classify_leftover_target(
        source_txn, target_period, basis, schedule,
    )

    if resolution.kind is _TargetKind.AMBIGUOUS:
        raise ValidationError(
            f"Carry forward refused for source transaction "
            f"{source_txn.id} ('{source_txn.name}'): target period "
            f"{target_period.period_id} has more than one open row for "
            f"template {source_txn.template_id}.  Resolve the duplicate "
            f"rows manually before retrying."
        )

    if resolution.kind is _TargetKind.TOP_UP:
        return resolution.row

    if resolution.kind is _TargetKind.GENERATE:
        # The window is this ONE period; the schedule the rule is read
        # against is the owner's whole one (plan step R4b-1).  Handing the
        # single period over as both -- which this did until R4b-1 -- made a
        # ``Monthly First`` rule see one month holding one payday and answer
        # "fires here" for any period at all, so the row this branch created
        # was a duplicate the correct engine never names.
        created = recurrence_engine.generate_for_template(
            source_txn.template, schedule, basis.scenario_id,
        )
        generated = next(
            (t for t in created
             if t.pay_period_id == target_period.period_id),
            None,
        )
        if generated is not None:
            return generated

    # CREATE (or a GENERATE race that produced nothing): build a fresh
    # override row carrying the leftover.
    return _create_target_override_row(
        source_txn, target_period, basis.scenario_id,
    )


def _create_target_override_row(source_txn, target_period, scenario_id):
    """Create a fresh override row in *target_period* for the leftover.

    Used when no mutable destination row exists to top up and the
    recurrence engine will not generate one -- e.g. a yearly Father's Day
    envelope rolling into an off-anniversary period, or a destination
    whose only row is finalised or soft-deleted.  The row is created at
    ``Decimal("0")``; the caller folds the leftover on top via the same
    bump every branch uses, so the row ends at exactly the leftover
    amount.

    The row copies its identity (account, template, name, category, type)
    from *source_txn* and is flagged ``is_override = True`` so (a) it is
    excluded from both partial unique generation indexes and never
    collides with a canonical or soft-deleted sibling, and (b) the recurrence engine
    skips it on later passes (``_recurrence_common.OccurrenceClaims`` -- an
    undated row claims its whole paycheck).  ``due_date`` is
    left ``None``: the leftover is a manually-carried amount with no
    scheduled date, and -- unlike the GENERATE path -- there is no
    recurrence rule to derive one from, so copying the source's
    past-period date would render the new row as overdue.  ``template_id``
    is copied verbatim.

    No flush: the caller runs inside ``carry_forward_unpaid``'s
    ``no_autoflush`` block and an ``is_override`` row is index-safe in
    every intermediate state.

    Args:
        source_txn: The envelope source row whose identity is copied.
        target_period: The destination
            :class:`~app.services.pay_calendar.DerivedPeriod`.
        scenario_id: Scenario the new row belongs to.

    Returns:
        The newly added (unflushed) Transaction.
    """
    row = Transaction(
        # A carried-forward row is the SOURCE row's, moved (plan step
        # ``pay_calendar:C13-a``): the owner travels with the identity this
        # constructor copies, exactly as the account and template do.
        user_id=source_txn.user_id,
        account_id=source_txn.account_id,
        template_id=source_txn.template_id,
        pay_period_id=target_period.period_id,
        scenario_id=scenario_id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        name=source_txn.name,
        category_id=source_txn.category_id,
        transaction_type_id=source_txn.transaction_type_id,
        amount_ownership=AmountOwnership.own(Decimal("0")),
        due_date=None,
        is_override=True,
        is_deleted=False,
    )
    db.session.add(row)
    return row
