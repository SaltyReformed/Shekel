"""Mutating carry-forward execution.

``carry_forward_unpaid`` applies the three-way partition's semantics --
settle-and-roll for envelope rows, move-whole for discrete rows, and
``transfer_service.update_transfer`` for shadows -- as one atomic batch.
The caller owns the surrounding commit; a ``ValidationError`` from the
envelope branch must roll the whole batch back.
"""

import logging
from decimal import Decimal

from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import transfer_service
from app.services.entry_service import compute_actual_from_entries
from app.utils.balance_predicates import is_projected_clause
from app.utils.log_events import BUSINESS, EVT_CARRY_FORWARD, log_event

from ._context import (
    _build_carry_forward_context,
    _is_finalised,
    _target_canonical_rows,
    _target_status_label,
)

logger = logging.getLogger(__name__)


def carry_forward_unpaid(source_period_id, target_period_id, user_id,
                         scenario_id):
    """Carry forward all projected items from source to target period.

    Steps:
      1. Verify both periods belong to *user_id*.
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
        user_id: The ID of the user who owns both periods.
            Defense-in-depth: ownership is verified even if the
            caller already checked at the route level.
        scenario_id: The scenario to carry forward within.  Prevents
            cross-scenario data corruption when multiple scenarios
            exist for the same user.

    Returns:
        int -- the number of carried items (1 per source row processed).

    Raises:
        NotFoundError: If either period does not exist or does not
            belong to *user_id*.
        ValidationError: If the envelope branch encounters a state
            that prevents a clean rollover (settled target canonical,
            template inactive in target period, or a corrupt
            multi-row target state).  The whole batch fails and the
            caller must rollback the session before issuing any
            follow-up writes.
    """
    ctx = _build_carry_forward_context(
        source_period_id, target_period_id, user_id, scenario_id,
    )

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
    # moment violates the partial unique index
    # idx_transactions_template_period_scenario, even though the
    # FINAL state is index-safe.  See the original 33cd21e fix and
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
        # to keep the row index-safe (the partial unique index
        # ``idx_transactions_template_period_scenario`` excludes
        # override rows, so flipping the flag and the period together
        # avoids any transient state that could collide with the
        # rule-generated row already in the target period).  Ad-hoc
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
        # bumps the target's canonical row by the unspent leftover.
        # The helper raises ValidationError if the target row is
        # finalised, missing, or corrupt; the route catches that and
        # rolls back the session for batch atomicity.
        for txn in ctx.envelope_txns:
            _settle_source_and_roll_leftover(
                txn, ctx.target_period, scenario_id,
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
    log_event(logger, logging.INFO, EVT_CARRY_FORWARD, BUSINESS,
              "Carried forward unpaid items",
              user_id=user_id,
              count=count, from_period_id=source_period_id,
              to_period_id=target_period_id,
              envelope_count=len(ctx.envelope_txns),
              discrete_count=len(ctx.discrete_txns),
              transfer_count=len(moved_transfer_ids))
    return count


def _settle_source_and_roll_leftover(source_txn, target_period, scenario_id):
    """Settle an envelope source row and roll its leftover into the target.

    Implements the envelope branch of Option F (see
    ``docs/carry-forward-aftermath-design.md``):

      1. Compute ``entries_sum`` as ``sum(e.amount for e in
         source.entries)``.  Empty entries -> ``Decimal("0")`` so the
         full estimated amount rolls forward.
      2. Compute ``leftover = max(Decimal("0"), source.estimated_amount
         - entries_sum)``.  Overspend (``entries_sum > estimated``)
         clamps to zero -- the actual overspend is recorded on the
         settled source row's ``actual_amount`` and on its entries.
      3. If ``leftover > 0``:
         a. Locate the target period's row for ``(template_id,
            target_period.id, scenario_id, is_deleted = False)``.  The
            query intentionally does not filter on ``is_override``: a
            prior carry-forward into the same target promotes the
            canonical to ``is_override = True``, and envelope semantics
            require that subsequent carry-forwards re-bump the same
            row rather than create a sibling.  At most one row may
            match; multiple rows is a corrupt envelope state and
            raises ``ValidationError``.
         b. If no row matches, ask
            ``recurrence_engine.generate_for_template`` to create the
            canonical.  The engine returns an empty list when the
            template's recurrence rule does not apply to the target
            period (no rule, ``Once`` pattern, ``effective_from`` past
            target, ``end_date`` past target, or any pre-existing row
            in the period -- the last covers a soft-deleted target
            row that the engine refuses to overwrite).  An empty
            return raises ``ValidationError`` so the user can resolve
            manually.
         c. If the located row is in an immutable status (Paid,
            Received, Settled, Credit, Cancelled), raise
            ``ValidationError``.  Bumping a finalised row would
            silently override the user's prior status decision.
         d. Bump ``estimated_amount += leftover`` and flip
            ``is_override = True``.  The flip blocks future
            recurrence-engine passes from regenerating the row
            (verified by the ``is_override`` skip clause in
            ``app/services/recurrence_engine.py``).
      4. Settle the source row via
         ``transaction_service.settle_from_entries``.  The helper
         enforces its own preconditions (envelope template,
         non-deleted, mutable status, no transfer_id), all of which
         are already guaranteed by the partitioning in
         ``carry_forward_unpaid``.

    Mutations land on ``source_txn`` and the target row in place; the
    caller owns the session/commit lifecycle.  No flush happens here
    except as a side effect of ``recurrence_engine.generate_for_template``
    when it has to create a canonical (which is acceptable inside the
    surrounding ``no_autoflush`` block because every prior loop
    iteration's mutations are already index-safe at that point).

    Args:
        source_txn: A Projected, non-deleted, envelope-tracked
            transaction in the source period.  Partitioning in
            ``carry_forward_unpaid`` guarantees the preconditions.
        target_period: The PayPeriod object for the target period.
            Passed pre-fetched to avoid a redundant lookup; the
            caller already validated ownership.
        scenario_id: The scenario the source row belongs to.  Used in
            the target-row lookup and the recurrence-engine call so
            cross-scenario data is never touched.

    Raises:
        ValidationError: If the target row state prevents a clean
            rollover -- multiple non-deleted rows for the
            (template, period, scenario), an immutable target row,
            or a missing target that the recurrence engine declines
            to create.  The error message names the source row,
            target period, and (where relevant) the failing condition
            so the user can act on it.
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
    entries_sum = compute_actual_from_entries(source_txn.entries)
    leftover = max(Decimal("0"), source_txn.estimated_amount - entries_sum)

    # Bump the target only when there is unspent leftover.  Overspend
    # and exact-spend cases (leftover == 0) settle the source without
    # touching the target -- the target's own canonical (or its
    # absence) is irrelevant to a zero rollover, and validating it
    # would fail surprises like a fully-paid target period that the
    # user does not need to mutate.
    if leftover > 0:
        target_row = _find_or_generate_target_canonical(
            source_txn, target_period, scenario_id, recurrence_engine,
        )
        target_row.estimated_amount = (
            target_row.estimated_amount + leftover
        )
        target_row.is_override = True

    transaction_service.settle_from_entries(source_txn)


def _find_or_generate_target_canonical(source_txn, target_period,
                                       scenario_id, recurrence_engine):
    """Return the target period's bumpable row for *source_txn*'s template.

    Looks up any non-deleted row matching ``(template_id,
    target_period.id, scenario_id)``.  The lookup deliberately omits
    the ``is_override`` filter -- see ``_settle_source_and_roll_leftover``
    docstring for why.

    Behavior by row count:

      * **Zero rows**: ask ``recurrence_engine.generate_for_template``
        to create the canonical.  An empty return from the engine
        raises ``ValidationError`` -- the target period is not in the
        template's active range or has a pre-existing row the engine
        refuses to overwrite.  Typed as a "manual move required"
        condition.
      * **One row**: validate that its status is mutable (Projected).
        Any immutable status -- Paid, Received, Settled, Credit,
        Cancelled -- raises ``ValidationError`` to prevent silently
        overriding a prior user decision.
      * **More than one row**: raises ``ValidationError``.  Multiple
        non-deleted rows for the same envelope (template, period)
        is a corrupt state that the user must resolve manually
        (typically a legacy doubled-row pair from pre-fix 33cd21e
        behavior; cleanup is documented in the implementation plan
        as a manual step).

    Args:
        source_txn: The source transaction; its ``template_id`` and
            ``template`` are read.
        target_period: The PayPeriod the canonical lives in.
        scenario_id: Scenario filter for the lookup and engine call.
        recurrence_engine: The recurrence-engine module (passed
            in to avoid a circular import at module top).

    Returns:
        The Transaction row to bump.

    Raises:
        ValidationError: As described above.
    """
    existing = _target_canonical_rows(
        source_txn, target_period, scenario_id, include_deleted=False,
    )

    if len(existing) > 1:
        raise ValidationError(
            f"Carry forward refused for source transaction "
            f"{source_txn.id} ('{source_txn.name}'): target period "
            f"{target_period.id} has {len(existing)} non-deleted rows "
            f"for template {source_txn.template_id}.  Resolve the "
            f"duplicate rows manually before retrying."
        )

    if len(existing) == 1:
        target_row = existing[0]
        # Only Projected is mutable; every other status (Paid,
        # Received, Settled, Credit, Cancelled) carries
        # is_immutable=True per the seed data in conftest.py and the
        # production seed.  Bumping an immutable row would silently
        # erase the user's prior status decision and corrupt
        # historical records that downstream services (analytics,
        # year-end summary) rely on.
        if _is_finalised(target_row):
            status_label = _target_status_label(target_row)
            raise ValidationError(
                f"Carry forward refused for source transaction "
                f"{source_txn.id} ('{source_txn.name}'): target row "
                f"in period {target_period.id} is finalised "
                f"({status_label}).  Revert the target's status to "
                f"Projected or move the source manually."
            )
        return target_row

    # No row exists -- ask the engine to create the canonical.  The
    # engine's per-template generation respects the rule's pattern,
    # effective_from, and end_date, and skips any period that already
    # has an existing row (including soft-deleted ones).  Either
    # condition produces an empty return.
    if source_txn.template is None:
        # template_id is set (we partitioned on template.is_envelope),
        # so a missing relationship means the template was hard-deleted
        # mid-flight.  Refuse rather than silently fail.
        raise ValidationError(
            f"Carry forward refused for source transaction "
            f"{source_txn.id}: its template is unloaded or deleted."
        )

    created = recurrence_engine.generate_for_template(
        source_txn.template, [target_period], scenario_id,
    )
    new_canonical = next(
        (t for t in created if t.pay_period_id == target_period.id),
        None,
    )
    if new_canonical is None:
        raise ValidationError(
            f"Carry forward refused for source transaction "
            f"{source_txn.id} ('{source_txn.name}'): template "
            f"'{source_txn.template.name}' is not active in target "
            f"period {target_period.id}, or the period has a "
            f"soft-deleted row that blocks regeneration.  Move the "
            f"source manually or restore/hard-delete the blocking "
            f"row first."
        )
    return new_canonical
