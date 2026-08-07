"""
Shekel Budget App -- Recurrence authoring and resolution (plan steps R2c-1, R2d)

The single door every recurrence rule in the application is written through,
and the single producer of what a recurrence MEANS, and the public surface of
a package whose private modules are fenced by ``shekel-private-module-import``
(W9910).

Two vocabularies, one of which is computed
------------------------------------------

``budget.recurrence_rules`` stores what a user AUTHORS: the closed
``pattern_id`` set and its parameters.  The redesign's two-axis reading of the
same cadence -- an interval, a unit, a first occurrence, a placement, a shift
-- is a DERIVATION over those columns plus the owner's pay-period schedule,
and it is **not stored** (developer ruling 2026-08-07, plan step R2d).

Storing it beside its own inputs would make it a cache, and a cache drifts the
moment one writer moves one side alone.  The mechanisms proposed to stop that
-- read-only column accessors, a lint checker, a periodic integrity scan --
are all apparatus for keeping a cache honest, and none can be complete:
measured on SQLAlchemy 2.0.49, read-only accessors block attribute assignment
and keyword construction but not ORM bulk ``update()``, Core ``update()`` on
``__table__``, or assignment to the private name.  So the cache is not kept
honest; it is not kept.  :func:`resolve` is a pure function and the only
producer, so two readers cannot disagree.

The two-axis values become COLUMNS -- authored, NOT NULL, from one backfill,
in the same transaction that drops the closed-set columns -- at plan step
R7c, where the form starts collecting them.

What this package offers
------------------------

* :func:`calendar_for` -- load an owner's schedule once, thread it.
* :func:`resolve` -- ``(spec, calendar) -> ResolvedRecurrence``, the two-axis
  meaning.  Pure: no Flask, no ORM, no clock, no database.
* :func:`author_rule` / :func:`reauthor_rule` / :func:`build_transient_rule`
  -- the ORM-facing door.  A caller states what it AUTHORS
  (:class:`RecurrenceSpec`), never a column.
* :func:`recurrence_spec` -- the inverse, so a caller owning ONE fact about an
  existing rule reads the spec back, replaces that fact with
  ``dataclasses.replace``, and re-authors, rather than setting a column.

What lives where
----------------

* ``_calendar`` -- :class:`PeriodCalendar`, the pay-period schedule reduced to
  the three questions the derivation asks (plus its owner, so a resolution
  against the wrong user's schedule is refused rather than silently wrong).
* ``_resolution`` -- :class:`RecurrenceSpec`, :class:`ResolvedRecurrence` and
  :func:`resolve`, the pure derivation.
* ``_authoring`` -- the ORM-facing door: load the schedule, refuse the
  unresolvable, write the authored spec.

Plan step R3 adds the forward occurrence engine (``occurrences`` / ``place``)
here, consuming :class:`ResolvedRecurrence`; step R4 points the readers at it.
When step R7c moves the form onto the two-axis vocabulary,
:class:`RecurrenceSpec`'s fields change and :func:`resolve` shrinks to almost
nothing -- nothing above the door does.
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
    ResolvedRecurrence,
    resolve,
)

__all__ = [
    "PeriodCalendar",
    "RecurrenceResolutionError",
    "RecurrenceSpec",
    "ResolvedRecurrence",
    "SchedulePeriod",
    "author_rule",
    "build_transient_rule",
    "calendar_for",
    "reauthor_rule",
    "recurrence_spec",
    "resolve",
]
