"""
Shekel Budget App -- Carry Forward Service

Processes the user's "Carry Forward Unpaid" action on a past pay period
(scope doc section 4.6).  Splits the source period's projected
transactions into three categories and applies the right semantic to
each:

  * **Envelope-tracked templates** (``template.is_envelope = True``) --
    settle the source row at ``sum(entries)`` and roll the unspent
    leftover (``estimated - entries_sum``) into the target period's
    canonical row by bumping its ``estimated_amount`` and flipping
    ``is_override = True``.  Result: ONE row per (template, period)
    with cell display, period subtotal, and balance projection in
    perfect agreement.  See ``docs/carry-forward-aftermath-design.md``
    Option F for the rationale.

  * **Discrete templates / ad-hoc rows** (no envelope flag) -- the
    pre-existing 33cd21e behaviour: relocate the row to the target
    period and set ``is_override = True`` if template-linked so the
    recurrence engine does not regenerate over the moved row.

  * **Transfer shadows** (``transfer_id IS NOT NULL``) -- delegate to
    ``transfer_service.update_transfer`` so the parent transfer and
    both shadow legs move atomically (transfer invariant 5).

The whole batch is atomic.  If the envelope branch raises a
``ValidationError`` (e.g. a settled target canonical, an inactive
template, or a corrupt multi-row target state), the route catches it
and rolls back the session, so no source is settled and no target is
bumped on failure.

The module also exposes ``preview_carry_forward`` -- a read-only
inspection that returns one ``CarryForwardPlan`` per source row,
labelling each with the action that ``carry_forward_unpaid`` would
take (settle-and-roll, defer, transfer-move) or the reason it would
be blocked.  The carry-forward modal route renders this preview so
the user can confirm before any database writes happen (Phase 5 of
``docs/carry-forward-aftermath-implementation-plan.md``).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional
import logging

from app.extensions import db
from app.models.transaction import Transaction
from app import ref_cache
from app.enums import StatusEnum
from app.services import transfer_service
from app.services.entry_service import compute_actual_from_entries
from app.exceptions import NotFoundError, ValidationError
from app.utils.log_events import log_event, BUSINESS

logger = logging.getLogger(__name__)


# ── Plan kinds ───────────────────────────────────────────────────────

# String constants for ``CarryForwardPlan.kind``.  Defined as module
# constants (rather than an Enum) so the route layer can compare
# directly against ``plan.kind`` without an extra import in the
# template's macro context.
PLAN_KIND_ENVELOPE = "envelope"
PLAN_KIND_DISCRETE = "discrete"
PLAN_KIND_TRANSFER = "transfer"

# String constants for ``CarryForwardPlan.block_reason_code``.  Each
# code maps to a human-readable message rendered by the modal; using
# codes (not raw strings) lets tests assert the failure type without
# coupling to wording.
BLOCK_DUPLICATE_TARGETS = "duplicate_targets"
BLOCK_TARGET_FINALISED = "target_finalised"
BLOCK_TARGET_SOFT_DELETED = "target_soft_deleted"
BLOCK_TEMPLATE_INACTIVE = "template_inactive"
BLOCK_TEMPLATE_MISSING = "template_missing"


# ── Plan dataclasses ─────────────────────────────────────────────────


@dataclass(frozen=True)
class CarryForwardPlan:
    """One row's planned action under ``carry_forward_unpaid``.

    Attributes:
        transaction: The source Transaction the plan applies to.
            Held by reference so the modal template can render
            ``txn.name``, ``txn.estimated_amount``, ``txn.entries``,
            etc. without a re-query.
        kind: One of ``PLAN_KIND_ENVELOPE`` / ``PLAN_KIND_DISCRETE`` /
            ``PLAN_KIND_TRANSFER``.  Drives the action-label rendering
            and is the partition the mutating service uses too.
        blocked: True iff ``carry_forward_unpaid`` would refuse this
            row (raising ``ValidationError`` and aborting the batch).
            Only envelope plans can be blocked; discrete and transfer
            plans are always actionable.
        block_reason_code: Stable identifier for the block category
            when ``blocked`` is True; ``None`` otherwise.  See the
            ``BLOCK_*`` module constants.
        block_reason: Human-readable explanation of the block, naming
            the source row and target period; ``None`` otherwise.
        entries_sum: Envelope-only.  ``sum(entries)`` of the source row
            -- the actual amount the source will settle at.  ``None``
            for non-envelope plans.
        leftover: Envelope-only.  ``max(0, estimated - entries_sum)``
            -- the amount that would roll forward.  ``None`` for
            non-envelope plans.
        target_estimated_before: Envelope-only.  The target row's
            estimated amount (or the engine-generated default) BEFORE
            the bump.  ``None`` when blocked or when no rollover is
            needed (``leftover == 0``).
        target_estimated_after: Envelope-only.  The target row's
            estimated amount AFTER the bump.  ``None`` when blocked
            or when no rollover is needed.
        target_will_be_generated: Envelope-only.  True when the target
            canonical does not yet exist and the recurrence engine
            would create it as part of the carry-forward.  ``False``
            otherwise.
    """

    transaction: Transaction
    kind: str
    blocked: bool = False
    block_reason_code: Optional[str] = None
    block_reason: Optional[str] = None
    entries_sum: Optional[Decimal] = None
    leftover: Optional[Decimal] = None
    target_estimated_before: Optional[Decimal] = None
    target_estimated_after: Optional[Decimal] = None
    target_will_be_generated: bool = False


@dataclass(frozen=True)
class CarryForwardPreview:
    """Aggregate view of a carry-forward batch as it would execute.

    Read-only output of ``preview_carry_forward``.  ``plans`` ordering
    is stable and matches the partition order the mutating service
    uses internally (envelope -> discrete -> transfer) so the UI
    presents the most consequential rows first (envelope rollovers
    are the only ones that can block the batch).
    """

    source_period: object  # PayPeriod -- forward-declared to avoid an import cycle.
    target_period: object  # PayPeriod
    plans: List[CarryForwardPlan]

    @property
    def any_blocked(self) -> bool:
        """True if any plan would refuse the batch.

        The mutating service is atomic on a per-batch basis: a single
        blocked envelope plan rolls back every other plan's
        mutations.  The modal disables Confirm whenever this is True
        so the user fixes the offending row first.
        """
        return any(p.blocked for p in self.plans)

    @property
    def envelope_count(self) -> int:
        """Number of actionable (non-blocked) envelope plans."""
        return sum(
            1 for p in self.plans
            if p.kind == PLAN_KIND_ENVELOPE and not p.blocked
        )

    @property
    def discrete_count(self) -> int:
        """Number of actionable (non-blocked) discrete plans."""
        return sum(
            1 for p in self.plans
            if p.kind == PLAN_KIND_DISCRETE and not p.blocked
        )

    @property
    def transfer_count(self) -> int:
        """Number of actionable (non-blocked) transfer plans."""
        return sum(
            1 for p in self.plans
            if p.kind == PLAN_KIND_TRANSFER and not p.blocked
        )

    @property
    def blocked_count(self) -> int:
        """Total number of blocked plans (always envelope rows)."""
        return sum(1 for p in self.plans if p.blocked)


@dataclass(frozen=True)
class _CarryForwardContext:
    """Internal: validated periods + partitioned source rows.

    Built by ``_build_carry_forward_context``; consumed by both
    ``carry_forward_unpaid`` and ``preview_carry_forward`` so the two
    paths see exactly the same partition (DRY: the partition logic
    lives once).
    """

    source_period: object  # PayPeriod
    target_period: object  # PayPeriod
    user_id: int
    scenario_id: int
    shadow_txns: List[Transaction]
    envelope_txns: List[Transaction]
    discrete_txns: List[Transaction]


def _build_carry_forward_context(source_period_id, target_period_id,
                                 user_id, scenario_id):
    """Validate periods, query projected source rows, three-way partition.

    Pure read-only setup shared by ``carry_forward_unpaid`` (mutating)
    and ``preview_carry_forward`` (read-only).  Raises ``NotFoundError``
    if either period is missing or not owned by *user_id* -- both
    callers want the same security response (404 at the route layer).

    The same-period short-circuit (``source == target``) returns an
    empty partition so callers can no-op cleanly without special
    casing in the loops.

    Args:
        source_period_id: pay_period.id to carry forward FROM.
        target_period_id: pay_period.id to carry forward TO.
        user_id: defense-in-depth ownership check.
        scenario_id: scenario filter (mirrors the mutating path).

    Returns:
        _CarryForwardContext.

    Raises:
        NotFoundError: if either period is missing or not owned.
    """
    from app.models.pay_period import PayPeriod  # pylint: disable=import-outside-toplevel

    source = db.session.get(PayPeriod, source_period_id)
    if source is None or source.user_id != user_id:
        raise NotFoundError(f"Source pay period {source_period_id} not found.")

    target = db.session.get(PayPeriod, target_period_id)
    if target is None or target.user_id != user_id:
        raise NotFoundError(f"Target pay period {target_period_id} not found.")

    if source_period_id == target_period_id:
        return _CarryForwardContext(
            source_period=source,
            target_period=target,
            user_id=user_id,
            scenario_id=scenario_id,
            shadow_txns=[],
            envelope_txns=[],
            discrete_txns=[],
        )

    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

    projected_txns = (
        db.session.query(Transaction)
        .filter(
            Transaction.pay_period_id == source_period_id,
            Transaction.scenario_id == scenario_id,
            Transaction.status_id == projected_id,
            Transaction.is_deleted.is_(False),
        )
        .all()
    )

    shadow_txns: List[Transaction] = []
    envelope_txns: List[Transaction] = []
    discrete_txns: List[Transaction] = []
    for txn in projected_txns:
        if txn.transfer_id is not None:
            shadow_txns.append(txn)
        elif txn.template is not None and txn.template.is_envelope:
            envelope_txns.append(txn)
        else:
            discrete_txns.append(txn)

    return _CarryForwardContext(
        source_period=source,
        target_period=target,
        user_id=user_id,
        scenario_id=scenario_id,
        shadow_txns=shadow_txns,
        envelope_txns=envelope_txns,
        discrete_txns=discrete_txns,
    )


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
        # Discrete branch first.  Running discrete before envelope is a
        # deliberate ordering: the envelope branch may invoke
        # ``recurrence_engine.generate_for_template`` (which performs
        # an explicit ``db.session.flush()``); flushing while a
        # template-linked discrete row still has ``is_override = False``
        # AND a new ``pay_period_id = target`` would collide with the
        # rule-generated row already in the target period.  Setting
        # ``is_override`` BEFORE ``pay_period_id`` keeps every loop
        # iteration self-consistent at flush time.
        for txn in ctx.discrete_txns:
            if txn.template_id is not None:
                txn.is_override = True
            txn.pay_period_id = target_period_id
            count += 1

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
    log_event(logger, logging.INFO, "carry_forward", BUSINESS,
              "Carried forward unpaid items",
              count=count, from_period_id=source_period_id,
              to_period_id=target_period_id,
              envelope_count=len(ctx.envelope_txns),
              discrete_count=len(ctx.discrete_txns),
              transfer_count=len(moved_transfer_ids))
    return count


def preview_carry_forward(
    source_period_id: int,
    target_period_id: int,
    user_id: int,
    scenario_id: int,
) -> CarryForwardPreview:
    """Return a read-only preview of a planned carry-forward batch.

    Mirrors ``carry_forward_unpaid``'s decision tree without mutating
    a single row -- the carry-forward modal route renders the result
    so the user confirms before any database writes happen (Phase 5
    of ``docs/carry-forward-aftermath-implementation-plan.md``).

    Returns one ``CarryForwardPlan`` per source row, partitioned and
    ordered the same way ``carry_forward_unpaid`` would process them
    (envelope rollovers first because they are the only ones that
    can block the batch; then discrete defers; then transfer moves
    de-duplicated by parent transfer_id).  Each plan carries enough
    structured data (entries_sum, leftover, target_estimated_after,
    block_reason_code) for the modal template to render the action
    label without re-deriving any business logic.

    The preview's ``any_blocked`` flag drives the Confirm button in
    the modal: when True, the carry-forward POST would refuse, so the
    Confirm button is disabled until the user resolves the offending
    rows.

    Args:
        source_period_id: pay_period.id to carry forward FROM.
        target_period_id: pay_period.id to carry forward TO.
            Typically the user's current period; the route resolves
            it the same way the mutating route does.
        user_id: defense-in-depth ownership check (route already
            enforced via ``@require_owner``).
        scenario_id: scenario filter; mirrors the mutating path so
            preview and execution see the same set of rows.

    Returns:
        CarryForwardPreview.  Empty plans list when source == target
        or there are no projected rows in the source period.

    Raises:
        NotFoundError: if either period is missing or not owned.

    Side effects:
        None.  All database access is read-only and no session
        mutations are made -- the modal route can re-issue the
        preview as many times as the user clicks the button without
        any persisted side effects.
    """
    ctx = _build_carry_forward_context(
        source_period_id, target_period_id, user_id, scenario_id,
    )

    plans: List[CarryForwardPlan] = []

    # Envelope rollovers first: they are the only kind that can block
    # the batch, so showing them at the top of the modal puts the
    # actionable failure cases in front of the user.
    for txn in ctx.envelope_txns:
        plans.append(
            _build_envelope_plan(txn, ctx.target_period, scenario_id),
        )

    for txn in ctx.discrete_txns:
        plans.append(_build_discrete_plan(txn))

    seen_transfers = set()
    for txn in ctx.shadow_txns:
        if txn.transfer_id in seen_transfers:
            continue
        seen_transfers.add(txn.transfer_id)
        plans.append(_build_transfer_plan(txn))

    return CarryForwardPreview(
        source_period=ctx.source_period,
        target_period=ctx.target_period,
        plans=plans,
    )


def _build_envelope_plan(source_txn, target_period, scenario_id):
    """Compute the envelope-rollover plan for *source_txn*.

    Mirrors ``_settle_source_and_roll_leftover`` and
    ``_find_or_generate_target_canonical`` exactly: every condition
    that would raise ``ValidationError`` in the mutating path
    surfaces here as ``blocked=True`` with a stable
    ``block_reason_code``, and every passing condition produces an
    actionable plan with the matching ``target_estimated_after`` the
    bump would land on.

    Read-only -- queries the database for target rows but never
    mutates and never invokes ``recurrence_engine.generate_for_template``
    (which would create rows).  The
    ``recurrence_engine.can_generate_in_period`` helper is used
    instead to predict the engine's create-or-skip decision.

    The decision tree's per-branch outcome lives in
    ``_resolve_envelope_target_fields`` so this function has a
    single return point and the dataclass is constructed exactly
    once -- making it harder for a future edit to forget a field
    on one branch.
    """
    entries_sum = compute_actual_from_entries(source_txn.entries)
    leftover = max(
        Decimal("0"), source_txn.estimated_amount - entries_sum,
    )
    target_fields = _resolve_envelope_target_fields(
        source_txn, target_period, scenario_id, leftover,
    )
    return CarryForwardPlan(
        transaction=source_txn,
        kind=PLAN_KIND_ENVELOPE,
        entries_sum=entries_sum,
        leftover=leftover,
        **target_fields,
    )


def _resolve_envelope_target_fields(source_txn, target_period,
                                    scenario_id, leftover):
    """Decide the target-row half of an envelope plan.

    Returns a dict of ``CarryForwardPlan`` fields covering the
    target-row decisions only -- ``blocked``, ``block_reason_code``,
    ``block_reason``, ``target_estimated_before``,
    ``target_estimated_after``, ``target_will_be_generated``.  The
    caller (``_build_envelope_plan``) supplies the remaining fields
    (``transaction``, ``kind``, ``entries_sum``, ``leftover``).

    The eight outcomes are encoded as eight branches of one
    if/elif/else chain so a future reviewer can read the decision
    tree top-to-bottom.  Each branch sets ``result`` exactly once
    and the function returns it at the bottom -- guarding against
    accidentally returning early on a partially-populated dict.

    Branches (in order):

      1. ``leftover == 0`` -> empty dict (mutating path skips target).
      2. >1 non-deleted target rows -> ``BLOCK_DUPLICATE_TARGETS``.
      3. exactly one immutable target -> ``BLOCK_TARGET_FINALISED``.
      4. exactly one mutable target -> actionable bump with concrete
         before/after numbers.
      5. only soft-deleted rows -> ``BLOCK_TARGET_SOFT_DELETED``.
      6. no rows + no template -> ``BLOCK_TEMPLATE_MISSING``.
      7. no rows + template inactive -> ``BLOCK_TEMPLATE_INACTIVE``.
      8. no rows + template active -> actionable, engine generates
         canonical at template default and bump applies on top.
    """
    if leftover == Decimal("0"):
        # Overspend / exact-spend: the mutating path settles source
        # and never touches the target.  No target row inspection
        # needed; fall through with all target_* fields at default.
        return {}

    # Pull every row in the target period for this template+scenario,
    # including soft-deleted ones, so we can distinguish the "no row
    # at all" case from the "only soft-deleted rows" case (the latter
    # is a known engine-skip condition that prevents auto-generation).
    all_target_rows = (
        db.session.query(Transaction)
        .filter(
            Transaction.template_id == source_txn.template_id,
            Transaction.pay_period_id == target_period.id,
            Transaction.scenario_id == scenario_id,
        )
        .all()
    )
    non_deleted = [t for t in all_target_rows if not t.is_deleted]

    if len(non_deleted) > 1:
        result = {
            "blocked": True,
            "block_reason_code": BLOCK_DUPLICATE_TARGETS,
            "block_reason": (
                f"Target period has {len(non_deleted)} non-deleted "
                f"rows for this template.  Resolve the duplicate "
                f"rows manually before retrying."
            ),
        }
    elif len(non_deleted) == 1:
        target_row = non_deleted[0]
        if target_row.status is None or target_row.status.is_immutable:
            status_label = (
                target_row.status.name
                if target_row.status is not None
                else "<unset>"
            )
            result = {
                "blocked": True,
                "block_reason_code": BLOCK_TARGET_FINALISED,
                "block_reason": (
                    f"Target row in {target_period.label} is "
                    f"finalised ({status_label}).  Revert the "
                    f"target's status to Projected or move the "
                    f"source manually."
                ),
            }
        else:
            # Mutable target -- bump in place.
            result = {
                "target_estimated_before": target_row.estimated_amount,
                "target_estimated_after": (
                    target_row.estimated_amount + leftover
                ),
                "target_will_be_generated": False,
            }
    elif all_target_rows:
        # Only soft-deleted rows -> engine refuses to regenerate.
        result = {
            "blocked": True,
            "block_reason_code": BLOCK_TARGET_SOFT_DELETED,
            "block_reason": (
                f"Target period {target_period.label} has only "
                f"soft-deleted rows for this template.  The "
                f"recurrence engine refuses to overwrite them, so "
                f"carry forward cannot create a canonical to "
                f"receive the rollover.  Restore or hard-delete "
                f"the existing rows first."
            ),
        }
    elif source_txn.template is None:
        # Defensive: template_id is set (envelope partition) but the
        # relationship is unloaded or the row was hard-deleted mid-
        # flight.  Refuse rather than silently fail.
        result = {
            "blocked": True,
            "block_reason_code": BLOCK_TEMPLATE_MISSING,
            "block_reason": (
                "The source row's template is unloaded or deleted, "
                "so carry forward cannot determine the target row."
            ),
        }
    else:
        # Fully empty period -- ask whether the engine would create
        # the canonical on its own.  Deferred import: recurrence_engine
        # and carry_forward_service are at the same layer; the
        # deferred form documents that the dependency is one-way
        # (carry-forward depends on recurrence-engine but not vice
        # versa).
        from app.services import recurrence_engine  # pylint: disable=import-outside-toplevel

        if not recurrence_engine.can_generate_in_period(
            source_txn.template, target_period, scenario_id,
        ):
            result = {
                "blocked": True,
                "block_reason_code": BLOCK_TEMPLATE_INACTIVE,
                "block_reason": (
                    f"Template '{source_txn.template.name}' is not "
                    f"active in {target_period.label} (no recurrence "
                    f"rule, the rule does not match this period, or "
                    f"its effective window has ended).  Move the "
                    f"source manually."
                ),
            }
        else:
            # Engine would create the canonical at default_amount.
            # Salary-linked envelope templates would technically
            # receive the paycheck-calculator amount instead, but
            # envelope semantics are restricted to expense templates
            # by the Phase 2 schema check, and salary profiles
            # attach to income templates -- so in practice
            # default_amount is the engine's output here.
            canonical_default = source_txn.template.default_amount
            result = {
                "target_estimated_before": canonical_default,
                "target_estimated_after": canonical_default + leftover,
                "target_will_be_generated": True,
            }

    return result


def _build_discrete_plan(source_txn):
    """Plan for a discrete (non-envelope) row: defer whole.

    Discrete carry-forward never blocks: the relaxed partial unique
    index allows the moved row to coexist with any rule-generated
    sibling in the target period, and ad-hoc rows have no template
    constraint at all.
    """
    return CarryForwardPlan(
        transaction=source_txn,
        kind=PLAN_KIND_DISCRETE,
        blocked=False,
    )


def _build_transfer_plan(shadow_txn):
    """Plan for a shadow row's parent transfer: move whole.

    The mutating path delegates to ``transfer_service.update_transfer``
    which moves the parent and both shadow legs together.  No block
    conditions exist in the carry-forward usage of that service
    (target period ownership and is_override are both already
    validated upstream).
    """
    return CarryForwardPlan(
        transaction=shadow_txn,
        kind=PLAN_KIND_TRANSFER,
        blocked=False,
    )


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
    # Defer the recurrence-engine import to avoid a circular dependency
    # at module load time: recurrence_engine is service-layer code and
    # this module is service-layer code; importing at top level works
    # in current code but the deferred form documents the intentional
    # one-way dependency (carry-forward depends on recurrence-engine,
    # never the reverse).
    from app.services import recurrence_engine  # pylint: disable=import-outside-toplevel
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
    existing = (
        db.session.query(Transaction)
        .filter(
            Transaction.template_id == source_txn.template_id,
            Transaction.pay_period_id == target_period.id,
            Transaction.scenario_id == scenario_id,
            Transaction.is_deleted.is_(False),
        )
        .all()
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
        if target_row.status is None or target_row.status.is_immutable:
            status_label = (
                target_row.status.name
                if target_row.status is not None
                else "<unset>"
            )
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
