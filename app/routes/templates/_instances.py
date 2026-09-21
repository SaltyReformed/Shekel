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

**The act itself is :mod:`app.services.definition_edit`'s since plan step
``balance:X-bi-7b``** -- which rows are the definition's to speak to
(``unruled_live_rows``) and the engine's write
(``recurrence_engine.propagate_to_unruled_definition``) -- so the grid
popover, the second door that lands an edit on a one-off's definition
(ruling **R-BAL23**), calls the same body.  What stays here is the
FLASHING, which is a route's job.

Route-layer module (leading underscore = route-internal) rather than a
service because it consumes Flask ``flash``; ``CLAUDE.md::Architecture`` keeps
services isolated from Flask globals.
"""

from app.services import definition_edit
from app.routes._recurrence_conflict_chooser import flash_retained_notice


def propagate_to_non_repeating_rows(template) -> None:
    """Push a RULE-LESS definition's edited fields onto the rows it holds, and say what stayed.

    :func:`app.services.definition_edit.propagate_to_unruled_rows` with this
    door's report: the rows the engine RETAINED are named in a flash, exactly
    as the maintain pass reports its own.  For a TRANSACTION definition that
    set is empty since plan step ``credit_card:CC-5-2`` (ruling **R-CC36**:
    a row holding records follows its definition's account move and its
    purchases stay on their own accounts); the report is kept in the shape
    the transfer twin's door still fills.

    Args:
        template: The updated ``TransactionTemplate``, its new field values
            already applied.
    """
    flash_retained_notice(definition_edit.propagate_to_unruled_rows(template))


__all__ = [
    "propagate_to_non_repeating_rows",
]
