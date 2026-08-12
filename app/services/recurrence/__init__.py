"""
Shekel Budget App -- Recurrence authoring and resolution (plan steps R2c-1, R2d)

The single door every recurrence rule in the application is written through,
and the single producer of what a recurrence MEANS, and the public surface of
a package whose private modules are fenced by ``shekel-private-module-import``
(W9910).

Two vocabularies, one of which is computed
------------------------------------------

``budget.recurrence_rules`` STORES the closed ``pattern_id`` set and its
parameters.  Since plan step R7b that is an ENCODING rather than the authored
vocabulary: a caller states ``(interval_n, unit, placement)`` and the seam
encodes it on the way in and decodes it on the way out.  What remains a
DERIVATION over those columns plus the owner's pay-period schedule -- the first
occurrence, and the phase the ``PERIOD`` unit fires on -- is **not stored**
(developer ruling 2026-08-07, plan step R2d).

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

* :func:`resolve` -- ``(spec, calendar) -> ResolvedRecurrence``, the two-axis
  meaning.  Pure: no Flask, no ORM, no clock, no database.
* :func:`author_rule` / :func:`reauthor_rule` / :func:`build_transient_rule`
  -- the ORM-facing door.  A caller states what it AUTHORS
  (:class:`RecurrenceSpec`), never a column.
* :func:`occurrences` / :func:`place` / :func:`occurrence_placements` -- the
  forward occurrence engine (plan step R3), AUTHORITATIVE since plan step R4a,
  so every pay period the application generates a row into is selected here.
  An occurrence with no pay period means the SAVED schedule does not reach it,
  which since plan step C2-b2 is the only way to get one.
* :func:`recurrence_spec` / :func:`read_rule` / :func:`resolved_recurrence` /
  :func:`rule_occurrences` / :func:`placed_periods` -- the READ door,
  symmetric with the write door: a rule's authored state back out, the one
  resolve-then-place composition, each of its halves alone, and the projection
  three surfaces take of the second.  Since plan step R4b-2 the generation
  seam, the Recurring surface, the form preview and the frozen baseline all
  answer from one call.
* :func:`cadence_of` -- ``(pattern_id, interval_n) -> Cadence``, how often a
  stored pattern fires, with no schedule involved (plan step R7a-2b).  With
  :meth:`Cadence.units_per_year` it is what makes a monthly equivalent one
  expression instead of a branch per pattern.
* :func:`describe` -- what a recurrence's cadence is CALLED, one function over
  ``(interval_n, unit)`` (plan step R7a).  It replaced eight hand-written
  template branches keyed on the closed pattern set, so a cadence nothing
  authors yet already reads correctly.

What lives where
----------------

* ``_frequency`` -- what a pattern means with NO schedule: :class:`Cadence`,
  the pattern table both readings share, and the yearly counts every monthly
  equivalent rests on.  Split out at plan step R7a-2b because
  ``obligations_aggregator`` and the calendar's infrequent badge ask "how
  often" and hold no calendar, so they could not use the two-axis vocabulary
  at all while it was fused to the anchor derivation.  ``_resolution`` reads
  this table rather than holding its own, so the two cannot disagree.
* ``_resolution`` -- :class:`RecurrenceSpec`, :class:`ResolvedRecurrence` and
  :func:`resolve`, the pure derivation of what a recurrence means AGAINST a
  schedule.
* ``_authoring`` -- the WRITE door: refuse the unresolvable, write the
  authored spec.  The only module here that holds a session.  The SCHEDULE it
  resolves against is :class:`~app.services.pay_calendar.PayCalendar`, loaded
  through that package's one door -- this package held a second calendar type
  and a second loader until plan step **C2-b2** deleted both.
* ``_occurrence`` -- the forward occurrence engine: walk the cadence, place
  each occurrence on a pay period.  Consumes :class:`ResolvedRecurrence`.
* ``_reading`` -- the READ door: a stored rule's authored state, its
  occurrences, and the projection onto periods.  Its own module rather than a
  line in ``_authoring`` because reading is not writing -- and because
  ``_authoring`` carries the session while nothing here needs one -- and
  rather than a line in ``_occurrence`` because that module is pure by
  contract and this one takes an ORM row.
* ``_vocabulary`` -- which patterns the application MODELS, so a ``ref`` row
  the enum does not name can be neither read nor accepted (plan step R2e-2).
  It held the PICKER too until plan step R7b-2 moved that out.
* ``_picker`` -- what the recurrence form OFFERS and what each option is
  called.  Its own module rather than a line in ``_vocabulary`` because the two
  answer different questions from different tables: membership is per closed-set
  pattern and is asked of STORED rows, while the offer set is derived from the
  ENCODER's table and asked of a blank form.  Serving the options from the
  encoder is what makes a cadence the closed set cannot store unofferable
  rather than merely unoffered (plan step R7b-2).
* ``_describe`` -- what a RESOLVED recurrence is called on a display surface.
  Its own module rather than a line in ``_vocabulary`` because the two are
  keyed on different things: the picker's labels are per closed-set pattern
  and die with the form at plan step R7b, while this one is a function of the
  two-axis meaning and is what survives.

Plan step R3 built the forward occurrence engine here, step R4a pointed the
old ``match_periods`` adapter at it (gated by
``tests/oracles/recurrence_baseline.txt``), and step R4b-2 deleted that adapter
and moved generation itself onto the ``(occurrence, period)`` pairs.
:class:`RecurrenceSpec`'s fields changed at step R7b-1 and :func:`resolve`
shrank with them.  Step R7c drops the encode / decode pair and the columns
under it; what changes above the door then is the two surfaces that still read
``rule.pattern_id`` for a cadence (``calendar_infrequency``,
``obligations_aggregator``, both through :func:`cadence_of`).
"""
from app.services.recurrence._authoring import (
    author_rule,
    build_transient_rule,
    reauthor_rule,
)
from app.services.recurrence._frequency import (
    Cadence,
    PatternReading,
    RecurrenceFrequencyError,
    RecurrenceResolutionError,
    cadence_of,
    decode_pattern,
    is_authorable,
)
from app.services.recurrence._describe import (
    RecurrenceDescription,
    RecurrenceDescriptionError,
    describe,
)
from app.services.recurrence._occurrence import (
    OccurrencePlacement,
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
    recurrence_spec_with_cadence,
    resolved_recurrence,
    rule_occurrences,
)
from app.services.recurrence._resolution import (
    RecurrenceSpec,
    ResolvedRecurrence,
    resolve,
)
from app.services.recurrence._picker import (
    CadenceOption,
    PickerModel,
    SelectedCadence,
    cadence_options,
    picker_model,
    selected_cadence,
)
from app.services.recurrence._vocabulary import (
    UNAVAILABLE_PATTERN_MESSAGE,
    modelled_pattern,
    modelled_placement,
    modelled_unit,
)

__all__ = [
    "UNAVAILABLE_PATTERN_MESSAGE",
    "Cadence",
    "CadenceOption",
    "OccurrencePlacement",
    "PatternReading",
    "PickerModel",
    "RecurrenceDescription",
    "RecurrenceDescriptionError",
    "RecurrenceFrequencyError",
    "RecurrenceGenerationError",
    "RecurrenceResolutionError",
    "RecurrenceSpec",
    "ResolvedRecurrence",
    "RuleReading",
    "SelectedCadence",
    "author_rule",
    "build_transient_rule",
    "cadence_of",
    "cadence_options",
    "decode_pattern",
    "describe",
    "is_authorable",
    "modelled_pattern",
    "modelled_placement",
    "modelled_unit",
    "occurrence_placements",
    "occurrences",
    "picker_model",
    "place",
    "placed_periods",
    "read_rule",
    "reauthor_rule",
    "recurrence_spec",
    "recurrence_spec_with_cadence",
    "resolve",
    "resolved_recurrence",
    "rule_occurrences",
    "selected_cadence",
]
