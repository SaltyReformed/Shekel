"""
Shekel Budget App -- Recurrence authoring (plan step R2c)

The single door every recurrence rule in the application is written through,
and the public surface of a package whose private modules are fenced by
``shekel-private-module-import`` (W9910).

Why one door
------------

``budget.recurrence_rules`` carries two vocabularies for one cadence: the
closed ``pattern_id`` set the engine still dispatches on, and the two-axis
``unit_id`` / ``anchor_date`` / ``placement_id`` / ``shift_id`` columns plan
step R2b added beside it and plan step R4 will cut over to.  The second set is
DERIVED from the first, so it is a persisted copy of a derivation -- and a copy
drifts the moment some writer moves one side alone.  Nine writers could:
six constructed a rule, three mutated one in place.

Rather than detect the drift, this package removes the state:

* a caller states what it AUTHORS -- :class:`RecurrenceSpec`, the pattern and
  its parameters, never a column;
* :func:`resolve` turns a spec plus the owner's schedule into
  :class:`~app.models.recurrence_rule.ResolvedRecurrence`, EVERY column of the
  row, both vocabularies emitted together from one input;
* :meth:`~app.models.recurrence_rule.RecurrenceRule.reauthor` writes that
  whole value, so there is no half-written row to leave behind.

A caller that owns one fact about an existing rule (the loan's payment day,
the schedule's new first period) does not set a column: it reads the spec back
with :func:`recurrence_spec`, replaces that fact with ``dataclasses.replace``,
and re-authors.  The anchor is then RE-DERIVED rather than left pointing at
the state before the edit.

What lives where
----------------

* ``_calendar`` -- :class:`PeriodCalendar`, the pay-period schedule reduced to
  the three questions the derivation asks, so the derivation stays pure.
* ``_resolution`` -- :class:`RecurrenceSpec` and :func:`resolve`, the pure
  old-to-new derivation.  No Flask, no ORM, no clock, no database.
* ``_authoring`` -- the ORM-facing door: load the schedule, resolve, write.

Plan step R3 adds the forward occurrence engine (``occurrences`` / ``place``)
to this package; step R4 points the readers at it.  When step R7 moves the
form onto the two-axis vocabulary, :class:`RecurrenceSpec`'s fields change and
:func:`resolve` shrinks -- nothing above the door does.
"""
from app.services.recurrence._authoring import (
    author_rule,
    build_transient_rule,
    calendar_for,
    reauthor_rule,
    recurrence_spec,
)
from app.services.recurrence._calendar import PeriodCalendar, SchedulePeriod
from app.services.recurrence._resolution import (
    RecurrenceResolutionError,
    RecurrenceSpec,
    resolve,
)

__all__ = [
    "PeriodCalendar",
    "RecurrenceResolutionError",
    "RecurrenceSpec",
    "SchedulePeriod",
    "author_rule",
    "build_transient_rule",
    "calendar_for",
    "reauthor_rule",
    "recurrence_spec",
    "resolve",
]
