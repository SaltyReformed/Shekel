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
* :func:`occurrences` / :func:`place` / :func:`occurrence_placements` -- the
  forward occurrence engine (plan step R3), AUTHORITATIVE since plan step R4a,
  so every pay period the application generates a row into is selected here.
  An occurrence with no pay period says WHICH of the two "no period" answers
  it is (:class:`PlacementOutcome`).
* :func:`recurrence_spec` / :func:`read_rule` / :func:`resolved_recurrence` /
  :func:`rule_occurrences` / :func:`placed_periods` -- the READ door,
  symmetric with the write door: a rule's authored state back out, the one
  resolve-then-place composition, each of its halves alone, and the projection
  three surfaces take of the second.  Since plan step R4b-2 the generation
  seam, the Recurring surface, the form preview and the frozen baseline all
  answer from one call.
* :func:`describe` -- what a recurrence's cadence is CALLED, one function over
  ``(interval_n, unit)`` (plan step R7a).  It replaced eight hand-written
  template branches keyed on the closed pattern set, so a cadence nothing
  authors yet already reads correctly.

What lives where
----------------

* ``_calendar`` -- :class:`PeriodCalendar`, the pay-period schedule reduced to
  the questions the derivation and the occurrence engine ask -- its opening
  bound and horizon, one period by id, a month's earliest payday, and the two
  placement searches -- plus its owner, so a resolution against the wrong
  user's schedule is refused rather than silently wrong.  It also REFUSES a
  schedule whose periods overlap or run backwards, because the placement
  searches bisect over that order.
* ``_resolution`` -- :class:`RecurrenceSpec`, :class:`ResolvedRecurrence` and
  :func:`resolve`, the pure derivation.
* ``_authoring`` -- the WRITE door: load the schedule, refuse the
  unresolvable, write the authored spec.  The only module here that holds a
  session.
* ``_occurrence`` -- the forward occurrence engine: walk the cadence, place
  each occurrence on a pay period.  Consumes :class:`ResolvedRecurrence`.
* ``_reading`` -- the READ door: a stored rule's authored state, its
  occurrences, and the projection onto periods.  Its own module rather than a
  line in ``_authoring`` because reading is not writing -- and because
  ``_authoring`` carries the session while nothing here needs one -- and
  rather than a line in ``_occurrence`` because that module is pure by
  contract and this one takes an ORM row.
* ``_vocabulary`` -- which patterns the application MODELS, and what the
  PICKER calls them: the set every form surface offers and every door
  validates against, so a ``ref`` row the enum does not name can be neither
  offered nor accepted (plan step R2e-2).
* ``_describe`` -- what a RESOLVED recurrence is called on a display surface.
  Its own module rather than a line in ``_vocabulary`` because the two are
  keyed on different things: the picker's labels are per closed-set pattern
  and die with the form at plan step R7b, while this one is a function of the
  two-axis meaning and is what survives.

Plan step R3 built the forward occurrence engine here, step R4a pointed the
old ``match_periods`` adapter at it (gated by
``tests/oracles/recurrence_baseline.txt``), and step R4b-2 deleted that adapter
and moved generation itself onto the ``(occurrence, period)`` pairs.
When step R7c moves the form onto the two-axis vocabulary,
:class:`RecurrenceSpec`'s fields change and :func:`resolve` shrinks to almost
nothing -- nothing above the door does.
"""
from app.services.recurrence._authoring import (
    author_rule,
    build_transient_rule,
    calendar_for,
    reauthor_rule,
)
from app.services.recurrence._calendar import (
    PeriodCalendar,
    RecurrenceScheduleError,
    SchedulePeriod,
)
from app.services.recurrence._describe import (
    RecurrenceDescription,
    RecurrenceDescriptionError,
    describe,
)
from app.services.recurrence._occurrence import (
    OccurrencePlacement,
    PlacementOutcome,
    RecurrenceGenerationError,
    occurrence_placements,
    occurrences,
    place,
)
from app.services.recurrence._reading import (
    RuleReading,
    placed_periods,
    read_rule,
    recurrence_spec,
    resolved_recurrence,
    rule_occurrences,
)
from app.services.recurrence._resolution import (
    RecurrenceResolutionError,
    RecurrenceSpec,
    ResolvedRecurrence,
    resolve,
)
from app.services.recurrence._vocabulary import (
    UNAVAILABLE_PATTERN_LABEL,
    UNAVAILABLE_PATTERN_MESSAGE,
    PatternChoice,
    modelled_pattern,
    pattern_choices,
    pattern_choices_for,
)

__all__ = [
    "UNAVAILABLE_PATTERN_LABEL",
    "UNAVAILABLE_PATTERN_MESSAGE",
    "OccurrencePlacement",
    "PatternChoice",
    "PeriodCalendar",
    "PlacementOutcome",
    "RecurrenceDescription",
    "RecurrenceDescriptionError",
    "RecurrenceGenerationError",
    "RecurrenceResolutionError",
    "RecurrenceScheduleError",
    "RecurrenceSpec",
    "ResolvedRecurrence",
    "RuleReading",
    "SchedulePeriod",
    "author_rule",
    "build_transient_rule",
    "calendar_for",
    "describe",
    "modelled_pattern",
    "occurrence_placements",
    "occurrences",
    "pattern_choices",
    "pattern_choices_for",
    "place",
    "placed_periods",
    "read_rule",
    "reauthor_rule",
    "recurrence_spec",
    "resolve",
    "resolved_recurrence",
    "rule_occurrences",
]
