"""Read-only carry-forward preview: plan vocabulary, DTOs, and builders.

``preview_carry_forward`` mirrors ``carry_forward_unpaid``'s decision
tree without mutating a single row, returning one ``CarryForwardPlan``
per source row so the carry-forward modal can show the user exactly what
would happen before any database writes.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from app.models.transaction import Transaction
from app.services.entry_service import compute_actual_from_entries

from ._context import (
    _build_carry_forward_context,
    _classify_leftover_target,
    _TargetKind,
)


# ── Plan kinds ───────────────────────────────────────────────────────

# String constants for ``CarryForwardPlan.kind``.  Defined as module
# constants (rather than an Enum) so the route layer can compare
# directly against ``plan.kind`` without an extra import in the
# template's macro context.
PLAN_KIND_ENVELOPE = "envelope"
PLAN_KIND_DISCRETE = "discrete"
PLAN_KIND_TRANSFER = "transfer"

# String constant for ``CarryForwardPlan.block_reason_code``.  The
# single remaining block is the AMBIGUOUS guard -- a destination period
# with more than one mutable row for the same (template, scenario), a
# corrupt pre-existing state.  Using a code (not a raw string) lets
# tests assert the failure type without coupling to wording.
BLOCK_AMBIGUOUS_TARGETS = "ambiguous_targets"


# ── Plan dataclasses ─────────────────────────────────────────────────


@dataclass(frozen=True)
class CarryForwardPlan:  # pylint: disable=too-many-instance-attributes
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
        target_will_be_generated: Envelope-only.  True when a new
            destination row will appear -- either the recurrence engine
            generates the canonical (empty, active-template period) or a
            fresh override row is created to carry the leftover (inactive
            template, finalised-only or soft-deleted-only destination).
            ``False`` when an existing mutable row is bumped in place.

    Pylint: ``too-many-instance-attributes`` (10/7) -- this is a cohesive
    value record -- one source row's planned carry-forward action -- read
    flat by its sole consumer, the carry-forward preview modal, which
    iterates ``preview.plans`` and renders one list item per plan.  The
    block metadata and the envelope rollover numbers are not read as
    separable units: the modal interleaves the rollover figures within a
    single rendered sentence and gates list-item styling on ``blocked``
    apart from rendering ``block_reason``.  Every field is an irreducible
    column of the row; splitting it would fragment one domain concept for
    no design gain.
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

    Shares the destination decision with the mutating path through
    ``_classify_leftover_target``: the single ``AMBIGUOUS`` outcome
    surfaces here as ``blocked=True`` with a stable ``block_reason_code``,
    and every other outcome produces an actionable plan with the matching
    ``target_estimated_after`` the bump would land on.

    Read-only -- the shared classifier queries the database for target
    rows but never mutates and never invokes
    ``recurrence_engine.generate_for_template`` (which would create rows);
    it uses ``recurrence_engine.can_generate_in_period`` instead to
    predict the engine's create-or-skip decision.

    The destination decision lives in
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

    A thin switch over ``_classify_leftover_target`` -- the same
    read-only decision the mutating path acts on -- so the preview can
    never predict an outcome execution would not take:

      * ``leftover == 0`` -> empty dict (the mutating path settles the
        source and never touches the destination).
      * ``AMBIGUOUS`` -> blocked; more than one mutable destination row
        is a corrupt state the user resolves manually.
      * ``TOP_UP`` / ``GENERATE`` / ``CREATE`` -> actionable.  The
        destination starts at ``resolution.base`` (the existing row's
        estimate, the template default the engine would generate, or
        ``Decimal("0")`` for a freshly created row) and the rollover
        bumps it to ``base + leftover``.  ``target_will_be_generated``
        flags the two cases where a new row appears (engine-generated
        canonical or fresh override row) so the modal can word it as a
        creation rather than a bump.
    """
    if leftover == Decimal("0"):
        # Overspend / exact-spend: the mutating path settles source
        # and never touches the target.  No target row inspection
        # needed; fall through with all target_* fields at default.
        return {}

    resolution = _classify_leftover_target(
        source_txn, target_period, scenario_id,
    )

    if resolution.kind is _TargetKind.AMBIGUOUS:
        return {
            "blocked": True,
            "block_reason_code": BLOCK_AMBIGUOUS_TARGETS,
            "block_reason": (
                f"Target period {target_period.label} has more than one "
                f"open row for this template.  Resolve the duplicate "
                f"rows manually before retrying."
            ),
        }

    return {
        "target_estimated_before": resolution.base,
        "target_estimated_after": resolution.base + leftover,
        "target_will_be_generated": resolution.kind in (
            _TargetKind.GENERATE, _TargetKind.CREATE,
        ),
    }


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
