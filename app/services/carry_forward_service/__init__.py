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

  * **Discrete templates / ad-hoc rows** (no recurring envelope template)
    -- the pre-existing 33cd21e behaviour: relocate the row to the target
    period and set ``is_override = True`` if template-linked so the
    recurrence engine does not regenerate over the moved row.  Ad-hoc
    envelope rows (``is_envelope`` set, no template) land here too: they
    move whole and carry their entries, because there is no recurring
    canonical to roll an unspent leftover into.

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

This package keeps the shared period-validate + three-way partition in
``_context``, the read-only preview vocabulary, DTOs, and plan builders
in ``_preview``, and the mutating execution in ``_execute``.  The public
surface (the two entry points, the ``CarryForwardPlan`` / ``CarryForwardPreview``
DTOs, and the ``PLAN_KIND_*`` / ``BLOCK_*`` constants) is re-exported
here so ``from app.services import carry_forward_service`` and every
``carry_forward_service.X`` access keep working verbatim.
"""

from ._execute import carry_forward_unpaid
from ._preview import (
    BLOCK_DUPLICATE_TARGETS,
    BLOCK_TARGET_FINALISED,
    BLOCK_TARGET_SOFT_DELETED,
    BLOCK_TEMPLATE_INACTIVE,
    BLOCK_TEMPLATE_MISSING,
    PLAN_KIND_DISCRETE,
    PLAN_KIND_ENVELOPE,
    PLAN_KIND_TRANSFER,
    CarryForwardPlan,
    CarryForwardPreview,
    preview_carry_forward,
)

__all__ = [
    "carry_forward_unpaid",
    "preview_carry_forward",
    "CarryForwardPlan",
    "CarryForwardPreview",
    "PLAN_KIND_ENVELOPE",
    "PLAN_KIND_DISCRETE",
    "PLAN_KIND_TRANSFER",
    "BLOCK_DUPLICATE_TARGETS",
    "BLOCK_TARGET_FINALISED",
    "BLOCK_TARGET_SOFT_DELETED",
    "BLOCK_TEMPLATE_INACTIVE",
    "BLOCK_TEMPLATE_MISSING",
]
