"""
Shekel Budget App -- Recurrence authoring and resolution (plan steps R2c-1, R2d)

The single door every recurrence rule in the application is written through,
and the single producer of what a recurrence MEANS, and the public surface of
a package whose private modules are fenced by ``shekel-private-module-import``
(W9910).

What a rule AUTHORS, and what is left of the encoding
-----------------------------------------------------

``budget.recurrence_rules`` states its recurrence in five columns: ``unit_id``,
``placement_id``, ``shift_id``, ``starts_on`` and the 0-or-1 ``nominal_day``.
A caller states the same thing (:class:`RecurrenceSpec`) and every reader takes
it from there.  That is the END of plan step R7c's expand / migrate / contract:
**R7c-a** added the columns and had the write door keep them in step while
nothing read them, **R7c-b** moved every reader across and gave the form one
date to collect, **R7c-c** drops what is left of the closed set.

**What is left is an ENCODING, and it runs one way.**  ``pattern_id`` /
``interval_n`` / ``day_of_month`` / ``month_of_year`` / ``start_date`` /
``start_period_id`` / ``offset_periods`` are derived by the write door from the
five above and read by almost nothing: ``interval_n`` still carries the cadence
interval (``encode_cadence`` writes ``1`` for every pattern whose interval is in
its NAME, so the read door takes it through ``_frequency.stored_interval`` and
never off the column), ``day_of_month`` still feeds
``recurrence_engine.compute_due_date`` until plan step R5 deletes that function,
and the other four have no reader at all.

**A stored DERIVATION would have been a cache; a stored AUTHORED value is a
fact**, and that distinction is why the R2d ruling of 2026-08-07 refused these
columns and R7c-b makes them correct.  The mechanisms proposed to keep a cache
honest -- read-only column accessors, a lint checker, a periodic integrity scan
-- are all apparatus, and none can be complete: measured on SQLAlchemy 2.0.49,
read-only accessors block attribute assignment and keyword construction but not
ORM bulk ``update()``, Core ``update()`` on ``__table__``, or assignment to the
private name.  Nothing is being kept honest here, because nothing is derived
from an input that can move: the form collects ``starts_on``, the loan sync
writes it from a contract.  (A day-less LOAN payment is the one shape still
measured against the schedule; plan ledger row **D6** tracks it.)

What this package offers
------------------------

* :func:`resolve` -- ``(spec, calendar) -> ResolvedRecurrence``, the two-axis
  meaning.  Pure: no Flask, no ORM, no clock, no database.
  Its ``starts_on`` is the rule's FIRST OCCURRENCE, with ONE meaning for every
  unit (ruling **R-R16**, plan ledger row **D28**), and it is what
  ``budget.recurrence_rules.starts_on`` holds.  ``anchor_date`` and the
  standalone ``first_occurrence`` went at plan step R7c-b: that field was the
  first occurrence for a calendar cadence and the opening BOUND for a
  pay-period one (row **D6**), so one name meant two things and a second
  function existed to reconcile them.
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

* ``_frequency`` -- what a cadence means with NO schedule: :class:`Cadence`,
  the pattern table both readings share, the yearly counts every monthly
  equivalent rests on, and (since plan step R7b-2) the anchor-family router
  that says WHICH derivation a ``(unit, placement)`` uses.  Split out at plan
  step R7a-2b because ``obligations_aggregator`` and the calendar's infrequent
  badge ask "how often" and hold no calendar, so they could not use the
  two-axis vocabulary at all while it was fused to the anchor derivation.
  ``_resolution`` reads this module's tables rather than holding its own, so
  the two cannot disagree.
* ``_bounds`` -- WHEN a recurrence stops: :class:`EndBound` and its three
  shapes.  Its own module rather than a pair of fields on ``_resolution``'s two
  values because "at most one closing bound" is then a property of the TYPE
  rather than a CHECK three layers have to restate -- and because the shape set
  is what the form offers, the schema accepts and the walk asks, so one closed
  table serves all three (plan step R7b-3).
* ``_resolution`` -- :class:`RecurrenceSpec`, :class:`ResolvedRecurrence` and
  :func:`resolve`, the pure derivation of what a recurrence means AGAINST a
  schedule.  Since plan step R7c-b that derivation is TWO things -- the
  pay-period normalisation and the cycle phase -- because the first occurrence
  is authored rather than reconstructed; the three anchor derivations that
  used to live here are deleted with the columns they read.
* ``_authoring`` -- the WRITE door: refuse the unresolvable, write the
  authored spec.  The only module here that holds a session.  The SCHEDULE it
  resolves against is :class:`~app.services.pay_calendar.PayCalendar`, loaded
  through that package's one door -- this package held a second calendar type
  and a second loader until plan step **C2-b2** deleted both.
* ``_occurrence`` -- the forward occurrence engine: walk the cadence, place
  each occurrence on a pay period.  Consumes :class:`ResolvedRecurrence`.  It
  held ``first_occurrence`` until plan step R7c-b -- the walk's first element
  stated as a direct search, so the value seeding the stored column could not
  differ from the value generating the rows.  ``resolve`` normalises the date
  itself now, so ``starts_on`` IS that first element by construction and there
  are no longer two functions to keep in step.
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
from app.services.recurrence._bounds import (
    END_BOUND_KINDS,
    NEVER_ENDS,
    BoundReading,
    EndBound,
    EndBoundColumns,
    EndBoundInputError,
    EndsAfterOccurrences,
    EndsOnDate,
    NeverEnds,
    end_bound_from_columns,
    end_bound_from_token,
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
    has_ended,
    placed_periods,
    read_rule,
    recurrence_spec,
    recurrence_spec_with_cadence,
    resolved_recurrence,
    rule_occurrences,
    stored_cadence,
)
from app.services.recurrence._resolution import (
    RecurrenceSpec,
    ResolvedRecurrence,
    has_day_of_month_coordinate,
    is_offerable_nominal_day,
    offerable_nominal_days,
    resolve,
)
from app.services.recurrence._picker import (
    CadenceOption,
    EndBoundOption,
    PickerModel,
    SelectedCadence,
    cadence_options,
    end_bound_options,
    fires_on_day_of_month,
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
    "END_BOUND_KINDS",
    "NEVER_ENDS",
    "UNAVAILABLE_PATTERN_MESSAGE",
    "BoundReading",
    "Cadence",
    "CadenceOption",
    "EndBound",
    "EndBoundColumns",
    "EndBoundInputError",
    "EndBoundOption",
    "EndsAfterOccurrences",
    "EndsOnDate",
    "NeverEnds",
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
    "end_bound_from_columns",
    "end_bound_from_token",
    "end_bound_options",
    "fires_on_day_of_month",
    "has_ended",
    "is_authorable",
    "has_day_of_month_coordinate",
    "is_offerable_nominal_day",
    "modelled_pattern",
    "modelled_placement",
    "modelled_unit",
    "occurrence_placements",
    "offerable_nominal_days",
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
    "stored_cadence",
]
