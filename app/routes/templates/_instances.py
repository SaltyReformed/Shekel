"""
Shekel Budget App -- Recurring route package: a rule-less definition's ROWS

What happens to the ``budget.transactions`` rows a
:class:`~app.models.transaction_template.TransactionTemplate` WITH NO RULE
holds when the definition is edited.  The sibling module
:mod:`app.routes.templates.crud` owns the definition itself; a definition
with a rule reaches its rows through the maintain pass
(``regenerate_or_conflict_chooser``), which a rule-less one never enters --
a regeneration with no rule names no occurrence and would RETIRE every
record-free row (defect **D16**), so the route gates it on "the definition IS
or WAS recurring".  This module is the other way its rows are brought into
line: the transaction twin of :mod:`app.routes.transfers._instances`'s
propagation half (plan step ``balance:X-bi-7a``, ruling **R-BAL20**).

**Which rows are the definition's to speak to is decided HERE, and what to
write is the engine's** (``recurrence_engine.propagate_to_unruled_definition``),
the split the transfer twin drew after an adversarial review of plan step
R10-b measured a route applying a definition unconditionally.  The selection
is the same three conditions ``_recurrence_common.owner_hold_on`` names for
the maintain pass -- Projected, not overridden, not soft-deleted -- and the
FLASHING stays here, which is a route's job.

Route-layer module (leading underscore = route-internal) rather than a
service because it consumes Flask ``flash``; ``CLAUDE.md::Architecture`` keeps
services isolated from Flask globals.
"""

from app.extensions import db
from app.models.transaction import Transaction
from app.services import recurrence_engine
from app.utils.balance_predicates import is_projected_clause
from app.routes._recurrence_conflict_chooser import flash_retained_notice


def non_repeating_live_rows(template):
    """Return the rows a RULE-LESS definition's edit may still rewrite.

    Projected, not hand-edited, not soft-deleted -- the same three conditions
    ``_recurrence_common.classify_maintain_work`` uses to decide which rows a
    recurring definition's regeneration may rewrite.  A settled row is
    immutable history and an overridden one is a deliberate per-instance
    change (a typed figure lands OWN with the flag beside it until plan step
    ``X-bi-7b``'s restate door); neither follows the definition, here or
    there.

    Args:
        template: The rule-less ``TransactionTemplate``.

    Returns:
        The matching ``Transaction`` rows, oldest first.  Normally exactly one
        -- a one-off's placed row -- but a definition whose recurrence was
        CLEARED keeps whatever survived that sweep, and this must be correct
        for both.
    """
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.template_id == template.id,
            is_projected_clause(Transaction),
            Transaction.is_override.is_(False),
            Transaction.is_deleted.is_(False),
        )
        .order_by(Transaction.id)
        .all()
    )


def propagate_to_non_repeating_rows(template) -> None:
    """Push a RULE-LESS definition's edited fields onto the rows it holds.

    The counterpart of regeneration for the one shape that does not
    regenerate, and the reason a rule-less definition's category, account,
    type or ownership declaration reaches the grid at all: the bulk rename in
    ``crud._apply_fields_and_propagate_rename`` carries the NAME to every
    row of every definition, and nothing carried the rest.  The engine
    decides what to write and what to leave alone
    (``recurrence_engine.propagate_to_unruled_definition``: a row whose
    account the definition moved and which holds the owner's records is
    RETAINED where it is); this door reports the rows it left.

    Args:
        template: The updated ``TransactionTemplate``, its new field values
            already applied.
    """
    retained = recurrence_engine.propagate_to_unruled_definition(
        template, non_repeating_live_rows(template),
    )
    flash_retained_notice(retained)


__all__ = [
    "non_repeating_live_rows",
    "propagate_to_non_repeating_rows",
]
