"""
Shekel Budget App -- Recurrence Engine

Given a transaction template and its recurrence rule, generates Transaction
entries into the appropriate future pay periods, and MAINTAINS the ones it has
already generated when the definition changes.

Implements the full state machine from §4.8:
  - Respects is_override and is_deleted flags.
  - Returns conflicts (overridden, deleted, and -- since plan step R10-a --
    retained) for the route layer to present to the user as prompts.
  - Never touches done/received/credit transactions.

**Which periods a rule fires in is no longer decided here.**  This module used
to carry five ``_match_*`` helpers that scanned candidate periods and asked
whether each contained the rule's target day; plan step R4a deleted them and
made an adapter, ``match_periods``, a thin wrapper over the forward occurrence
engine (:mod:`app.services.recurrence`), which walks the rule's own cadence and
then places each occurrence on a pay period.  **Plan step R4b-2 deleted the
adapter too**: ``recurrence.rule_occurrences`` answers in
``(occurrence, period)`` pairs, generation carries the pair as far as the write
loop, and an occurrence the schedule cannot host is REPORTED rather than
dropped where nobody looks (plan ledger row **D7**).  A generated row's own
DATE is still derived from its period by ``compute_due_date``, not from the
occurrence -- that is plan ledger row **D18**, and plan step R5 owns it with the
``due_date`` -> ``occurs_on`` split.  What survives here is the GENERATION half:
gating, the per-period skip predicate, amount resolution, row creation, and the
maintain / conflict state machine.

**And the schedule it is read against is the OWNER's, not the caller's**
(plan step R4b).  Every entry point below takes a
:class:`~app.services.generation_schedule.GenerationSchedule`: the owner's whole
pay-period schedule, plus the window this pass may write into.  The two used to
be one ``periods`` argument, so a caller handing over a SUBSET -- which the
schedule-extend path does on every run -- silently re-read every rule against
that subset.  That class of defect is measured in ``GenerationSchedule``'s own
docstring; the shape here is simply that a window narrows what is WRITTEN and
never what a recurrence MEANS.

What a definition can say it repeats by is the ``(interval_n, unit_id,
placement_id)`` triple ``budget.recurrence_rules`` authors -- plan step R7c-c
dropped the closed pattern set that used to be the whole vocabulary, and plan
step R9 its table; "does not recur" is ``recurrence_rule_id IS NULL`` on either
template kind, which never reaches a resolver (plan step R2e-3 retired the
``Once`` pattern that was the second way to say it).

**A PACKAGE since plan step R10-a**, which took the flat module past the
1,000-line ceiling when regeneration stopped destroying the rows it maintains.
The seam is the one the module's own sections already drew, and each leaf owns
one question:

  - ``_plan`` -- WHICH periods, and on what day: the gating + occurrence walk
    (``resolve_generation_plan``) and ``compute_due_date``;
  - ``_amounts`` -- WHAT a row's definition says: :class:`DerivedRowFields`,
    the single statement of the columns a template derives, and the salary /
    paycheck pricing behind ``estimated_amount``;
  - ``_generate`` -- filling periods that hold no row;
  - ``_maintain`` -- bringing the rows a definition already generated back into
    line with it, and refusing to do so where the owner's own records are in
    the way;
  - ``_conflicts`` -- applying the owner's keep/use decisions afterwards.

Every public name is re-exported here, so the split moved no call site.
"""

from app.services.recurrence_engine._amounts import (
    DerivedRowFields,
)
from app.services.recurrence_engine._conflicts import resolve_conflicts
from app.services.recurrence_engine._generate import (
    can_generate_in_period,
    generate_for_template,
)
from app.services.recurrence_engine._maintain import regenerate_for_template
from app.services.recurrence_engine._plan import (
    GenerationPlan,
    PlannedOccurrence,
    compute_due_date,
    resolve_generation_plan,
)

__all__ = [
    "DerivedRowFields",
    "GenerationPlan",
    "PlannedOccurrence",
    "can_generate_in_period",
    "compute_due_date",
    "generate_for_template",
    "regenerate_for_template",
    "resolve_conflicts",
    "resolve_generation_plan",
]
