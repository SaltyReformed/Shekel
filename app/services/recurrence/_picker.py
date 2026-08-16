"""What the recurrence form OFFERS, and what each option is called (R7b-2).

The picker's vocabulary, replacing the closed-set pattern list that
``_vocabulary`` held until plan step R7b-2.  A form no longer chooses a NAME
for a cadence ("Quarterly"); it authors the two axes directly -- how often
(``interval_n`` and ``unit``) and which pay period funds an occurrence
(``placement``) -- and this module is the one producer of which of those
readings may be offered and how each reads to a human.

**The offer set is DERIVED from the encoder's own table**, through
:func:`~app.services.recurrence._frequency.authorable_cadences`, and that is the
whole point of the step.  ``budget.recurrence_rules`` still names its cadence
with a closed pattern set until plan step R7c, so ``(2, MONTH)`` is perfectly
well-defined, walks correctly, and has nowhere to be written.  While the picker
iterated ``RecurrencePatternEnum`` and the encoder read ``PATTERN_DERIVATIONS``,
"nothing offers an unstorable cadence" held only because the two sets happened
to coincide -- a coincidence no gate protected.  Serving the options from the
encoder's table makes ``encode_cadence``'s refusal UNREACHABLE through the form
rather than fenced behind it (developer ruling 2026-08-12).

**A flat list of storable triples, not a nested tree**, and the shape is a DRY
decision rather than a convenience.  The form needs three linked answers -- which
units, which intervals for a unit, which placements for a ``(unit, interval)``
pair -- and emitting them as a nested structure would state the grouping rule a
second time, in a producer that could drift from the set it groups.  A consumer
filters the triples instead, which cannot mean anything the table does not.

Everything here dies at plan step R7c except the labels: with ``unit_id`` and
``interval_n`` authored columns, every ``(interval, unit, placement)`` is
storable, so the offer set stops being a subset and the interval control stops
being a select.

Pure: no Flask, no ORM, no clock.  The ``ref`` ids come from
:mod:`app.ref_cache`, the project's IDs-for-logic seam.
"""
import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass

from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.services.recurrence._bounds import (
    END_BOUND_KINDS,
    EndsAfterOccurrences,
    EndsOnDate,
    NeverEnds,
)
from app.services.recurrence._frequency import (
    PatternReading,
    authorable_cadences,
    fires_on_day_of_month,
)
from app.services.recurrence._resolution import has_day_of_month_coordinate

#: What each cadence UNIT is called on the form, singular and plural.
#:
#: Two forms rather than one because the control reads "Repeat every N <unit>"
#: and the count drives the noun: "every 1 months" is what a single label
#: produces.  Both are carried on the option so the template and its script
#: DISPLAY and never compute -- the rule :class:`CadenceOption` states.
#:
#: Keyed by enum member, so a unit added to
#: :class:`~app.enums.RecurrenceUnitEnum` without copy raises ``KeyError`` at
#: the first render rather than shipping a blank option.  ``WEEK`` is absent
#: DELIBERATELY: no closed-set pattern stores it, so
#: :func:`~app.services.recurrence._frequency.authorable_cadences` never yields
#: it and this map is never asked.  Plan step R8 is its first writer and adds
#: the copy with the pattern that stores it.
_UNIT_LABELS: dict[RecurrenceUnitEnum, tuple[str, str]] = {
    RecurrenceUnitEnum.PERIOD: ("paycheck", "paychecks"),
    RecurrenceUnitEnum.MONTH: ("month", "months"),
    RecurrenceUnitEnum.YEAR: ("year", "years"),
}

#: What each PLACEMENT is called on the form.
#:
#: Worded generically over the occurrence DATE rather than per unit, because the
#: placement is the same rule whatever the cadence counts: the old picker's
#: "Monthly (first paycheck of month)" fused a unit, an interval, an anchor day
#: and this choice into one name, which is why "every other month, funded from
#: the first paycheck" had nowhere to live.
_PLACEMENT_LABELS: dict[PeriodPlacementEnum, str] = {
    PeriodPlacementEnum.CONTAINING_DATE: "The paycheck that covers the date",
    PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER: (
        "The first paycheck starting on or after the date"
    ),
}


@dataclass(frozen=True)
class CadenceWire:
    """One storable cadence, as the SCRIPT reads it.

    **Plan ledger row D31**, closed at plan step R7c-b.  The offer set has two
    consumers that read disjoint halves of it: ``recurrence_form.js`` filters
    on ids and facts, the templates render labels.  One flat value served both
    and was serialised whole into :attr:`PickerModel.options_json`, so every
    render shipped three labels to a browser that discards them -- and the
    ``too-many-instance-attributes`` disable :class:`CadenceOption` carried was
    spent on holding the union together.  Splitting along the consumer line is
    what an adversarial review of plan step R7b-2 measured as available; it
    waited for this step because it changes the wire shape the script parses
    and that needs a browser pass.

    Attributes:
        unit_id: The ``ref.recurrence_units`` id the form posts.
        interval_n: The interval this option fixes, or ``None`` when ANY
            positive interval is storable -- which is what tells the form to
            render a number box rather than a select.
        placement_id: The ``ref.period_placements`` id the form posts.
        anchors_day_of_month: Whether this cadence's ANCHOR is derived from a
            day of the month, which is what decides whether the form shows its
            Due Day input.  Answered by the anchor router itself
            (:func:`~app.services.recurrence._frequency.fires_on_day_of_month`)
            rather than by a unit test the script could repeat, because it is
            a property of the ``(unit, placement)`` PAIR: a monthly cadence
            funded from the month's first paycheck anchors on a PAYCHECK.
        has_day_of_month_coordinate: Whether occurrences land on a day of the
            month at all, which is what decides whether the "repeating on"
            control has anything to ask.  **Not the same fact as the one
            above**, and shipping only that one was a wrong-money defect this
            step introduced: ``anchors_day_of_month`` is ``False`` for
            ``Monthly First`` while its occurrences ARE days of the month, so
            the script cleared and disabled a control the SERVER had rendered
            enabled -- and the update door reads ``nominal_day`` off the same
            presence key as ``starts_on``, so changing only "Funded from" on a
            "last day of every month" rent wrote ``nominal_day = NULL`` and
            moved every later occurrence to the 30th forever.  Answered by
            :func:`~app.services.recurrence.has_day_of_month_coordinate`, the
            same function ``offerable_nominal_days`` is built on, so the
            control the browser shows and the set the server offers cannot
            disagree.
    """

    unit_id: int
    interval_n: int | None
    placement_id: int
    anchors_day_of_month: bool
    has_day_of_month_coordinate: bool


@dataclass(frozen=True)
class CadenceOption:
    """One storable cadence, identified and worded for the form.

    A plain value so the form templates DISPLAY and never compute: every id
    goes in an ``<option value>`` and every label between the tags, with no
    lookup, no fallback expression and no ``name`` string in the template at
    all.  The ids it renders live on :attr:`wire`, which is also exactly what
    the script is sent -- so the two consumers read ONE statement of the offer
    set rather than two copies that agree.

    **``months_per_unit`` left at plan step R7c-b**, with the Month control it
    fed.  It said how many calendar months one unit spans, which decided
    whether a cycle SKIPS months and therefore whether a "Month" select
    narrowed anything; ruling R-R16 put the cycle's month on ``starts_on``, so
    there is no such control and no consumer for the fact.

    Attributes:
        wire: The ids and facts both the template and the script read.
        unit_label_one: What the unit is called after a ``1``.
        unit_label_many: What it is called after any other count.
        placement_label: The human copy for the placement option.
    """

    wire: CadenceWire
    unit_label_one: str
    unit_label_many: str
    placement_label: str

    @property
    def unit_id(self) -> int:
        """Return the ``ref.recurrence_units`` id this option posts."""
        return self.wire.unit_id

    @property
    def interval_n(self) -> int | None:
        """Return the interval this option fixes, or ``None`` when free."""
        return self.wire.interval_n

    @property
    def placement_id(self) -> int:
        """Return the ``ref.period_placements`` id this option posts."""
        return self.wire.placement_id


def cadence_options() -> tuple[CadenceOption, ...]:
    """Return every cadence the form may offer, worded.

    The single producer of the recurrence picker's options, for both template
    kinds and both the create and edit forms.

    Returns:
        One :class:`CadenceOption` per storable reading, most frequent cadence
        first (paycheck, month, year) -- the order the picker has always
        rendered.  Every entry RECURS: "does not repeat" is the form's own
        empty option, not a cadence (plan step R2e-3).

    Raises:
        RuntimeError: If ``ref_cache`` has not been initialized.
        KeyError: If a unit or placement the closed set can store has no copy
            in :data:`_UNIT_LABELS` or :data:`_PLACEMENT_LABELS`.
    """
    options = []
    for cadence in authorable_cadences():
        label_one, label_many = _UNIT_LABELS[cadence.unit]
        options.append(
            CadenceOption(
                wire=CadenceWire(
                    unit_id=ref_cache.recurrence_unit_id(cadence.unit),
                    interval_n=cadence.interval_n,
                    placement_id=ref_cache.period_placement_id(
                        cadence.placement,
                    ),
                    anchors_day_of_month=fires_on_day_of_month(
                        cadence.unit, cadence.placement,
                    ),
                    has_day_of_month_coordinate=(
                        has_day_of_month_coordinate(cadence.unit)
                    ),
                ),
                unit_label_one=label_one,
                unit_label_many=label_many,
                placement_label=_PLACEMENT_LABELS[cadence.placement],
            )
        )
    return tuple(options)


def _first_by(
    options: Iterable["CadenceOption"],
    key: Callable[["CadenceOption"], object],
) -> tuple["CadenceOption", ...]:
    """Return the first option for each distinct *key*, in offer order.

    The ONE deduplication rule the three controls share, differing only in what
    they are keyed on.  Written once here rather than three times in Jinja: the
    template's contract is to DISPLAY (see :class:`CadenceOption`), a projection
    is a computation, and the interval control shipped a visible defect from
    having taken the un-projected list -- ``MONTHLY`` and ``MONTHLY_FIRST`` are
    two triples over one ``(1, MONTH)`` interval, so it rendered "1 month"
    twice, with ``selected`` on BOTH.

    Args:
        options: The offer set.
        key: What makes two options the SAME entry on one control.

    Returns:
        tuple[CadenceOption, ...]: One option per distinct key, in the order
        the keys first appear.
    """
    seen = set()
    chosen = []
    for option in options:
        marker = key(option)
        if marker in seen:
            continue
        seen.add(marker)
        chosen.append(option)
    return tuple(chosen)


#: What each closing-bound SHAPE is called on the form, and which control it
#: needs a value from, keyed by the token its ``<option>`` posts.
#:
#: Keyed by token rather than by class so this table and
#: :data:`~app.services.recurrence._bounds.END_BOUND_KINDS` state one thing
#: each -- the shapes are the bounds module's, the copy is this module's -- and
#: a shape added there without copy here raises ``KeyError`` at the first
#: render rather than shipping a blank option.  Exactly the contract
#: :data:`_UNIT_LABELS` holds for the cadence units.
#:
#: The second entry is a DOM element id, which is more than copy, and it is
#: here rather than in the template for the reason every other projection on
#: :class:`PickerModel` is: "which input does this shape need" is a fact about
#: the shape, and a template that decided it would be a second statement of the
#: shape set -- keyed on a token STRING, which is the comparison this project
#: rules out everywhere else.  ``None`` for the shape that needs no value.
_END_BOUND_COPY: dict[str, tuple[str, str | None]] = {
    NeverEnds.token: ("Never", None),
    EndsOnDate.token: ("On a date", "field-end-date"),
    EndsAfterOccurrences.token: (
        "After a number of times", "field-max-occurrences",
    ),
}


#: What each NOMINAL DAY is called on the form.
#:
#: The days a month can fail to hold, and no others: every month holds its
#: first 28, so 29-31 is the whole domain -- the same one
#: ``ck_recurrence_rules_nominal_day`` bounds the column to and
#: :func:`~app.services.recurrence.offerable_nominal_days` selects from.
#:
#: **31 is worded as the LAST DAY rather than as a number**, and that is what
#: the value means rather than a friendlier way of saying it: the occurrence
#: walk clamps the day into each month
#: (:func:`~app.services.recurrence._months.clamped_day`), so a day-31 rule
#: fires on the 31st in January and the 30th in April.  "The 31st" would be a
#: label the cadence contradicts eight months a year.
_NOMINAL_DAY_LABELS: dict[int, str] = {
    29: "the 29th",
    30: "the 30th",
    31: "the last day of the month",
}


@dataclass(frozen=True)
class NominalDayOption:
    """One day a clamped first occurrence could have MEANT, worded.

    The offer set behind the form's "repeating on" control, which is rendered
    only where the chosen date leaves the question open -- see
    :func:`~app.services.recurrence.offerable_nominal_days` for when that is.
    A plain value so the template DISPLAYS: the day goes in the
    ``<option value>`` and the label between the tags.

    Attributes:
        day: The nominal day, 29-31, and what the control posts.
        label: The human copy.
    """

    day: int
    label: str


def nominal_day_options() -> tuple[NominalDayOption, ...]:
    """Return every nominal day the form may offer, worded, ascending.

    The WHOLE domain rather than the subset a particular date leaves open:
    which of them apply is a property of the chosen date, so the control
    renders them all and enables the ones
    :func:`~app.services.recurrence.offerable_nominal_days` names.  That is
    what lets the script re-enable them as the user edits the date without
    holding any copy of its own.

    Returns:
        One :class:`NominalDayOption` per day, ascending.
    """
    return tuple(
        NominalDayOption(day=day, label=label)
        for day, label in sorted(_NOMINAL_DAY_LABELS.items())
    )


@dataclass(frozen=True)
class EndBoundOption:
    """One shape a closing bound can take, worded for the form's mode select.

    The bound half of what the recurrence form offers, and the same shape as
    :class:`CadenceOption`: a token for the ``<option value>`` and a label
    between the tags, so the template DISPLAYS and computes nothing.

    Its token is not a ``ref`` id, and that is the difference from every other
    control on this form.  A closing bound's shape is not a stored value --
    which column is non-NULL decides it, ruling R-R13's "absence is the
    discriminator" applied to the bound -- so there is no ``ref`` table to
    carry an id, and inventing one would be the second representation that
    ruling refuses.

    Attributes:
        token: What the ``<option>`` posts, and what
            :func:`~app.services.recurrence.end_bound_from_token` dispatches
            on.
        label: The human copy for the option.
        needs_field_id: The id of the control this shape needs a value from,
            or ``None`` for the shape that needs none.  The script shows that
            one and disables the others, so exactly the input the chosen shape
            reads is the input that submits -- the same idiom the two interval
            controls use, and what keeps a stale value from a shape the user
            moved off from reaching the door.
    """

    token: str
    label: str
    needs_field_id: str | None


def end_bound_options() -> tuple[EndBoundOption, ...]:
    """Return every closing-bound shape the form may offer, worded.

    Derived from :data:`~app.services.recurrence._bounds.END_BOUND_KINDS` --
    the same tuple :func:`~app.services.recurrence.end_bound_from_token`
    dispatches over -- so a shape is offerable and submittable together or
    neither.  That is the property plan step R7b-2 gave the cadence controls by
    serving them from the encoder's own table, applied to the bound.

    Returns:
        One :class:`EndBoundOption` per shape, in the order the form offers
        them: never first, because it is the default and the form's first
        entry.

    Raises:
        KeyError: A shape has no copy in :data:`_END_BOUND_COPY`.
    """
    options = []
    for kind in END_BOUND_KINDS:
        label, needs_field_id = _END_BOUND_COPY[kind.token]
        options.append(EndBoundOption(
            token=kind.token, label=label, needs_field_id=needs_field_id,
        ))
    return tuple(options)


@dataclass(frozen=True)
class PickerModel:  # pylint: disable=too-many-instance-attributes
    """Everything the recurrence form needs to render its three controls.

    Every projection of ONE :func:`cadence_options` call, bundled because a
    route that took them separately would resolve the same producer twice in
    one request -- the redundancy this project treats as a DRY violation rather
    than a cost question, since two calls are two chances to disagree.

    Pylint: ``too-many-instance-attributes`` (8/7) -- suppressed because every
    attribute is a PROJECTION of the one ``cadence_options`` call this value
    exists to make once, and splitting the bundle to satisfy the count would
    recreate exactly the redundancy stated above: each half would resolve the
    producer again.  The eighth arrived at plan step R7c-b with
    ``nominal_days``, which is a control the form did not have before, so the
    growth is a control being added rather than the value taking on a second
    job.  Mirrors the ``RecurrenceSpec`` / ``ResolvedRecurrence`` precedent one
    package over.

    Attributes:
        options: The whole offer set.  Read by :attr:`options_json`'s own
            construction and by nothing in the templates -- each control
            renders from its projection below -- so it is the value the other
            five are derived FROM rather than one a consumer reaches for.
        options_json: The set's WIRE half as JSON, for the script that LINKS
            the controls.  It needs the whole set rather than the subset any
            one control renders: the unit decides which intervals may show, and
            the ``(unit, interval)`` pair decides which placements may.  It
            carries :class:`CadenceWire` rather than the whole option since
            plan step R7c-b, because the labels beside it were shipped to a
            browser that discards them (plan ledger row **D31**).
        nominal_days: Every day a clamped first occurrence could have MEANT,
            worded.  The "repeating on" control renders them all and enables
            the ones the chosen date leaves open, so the script re-enables them
            as the user edits that date without holding any copy of the wording.
        units: One option per UNIT, for the unit ``<select>``.  The MONTH unit
            has four offered readings and must render one entry, or the user is
            asked to choose between four things called "months".
        fixed_intervals: One option per ``(unit, interval)`` whose interval is
            FIXED, for the interval ``<select>``.  A unit that takes any
            positive interval is absent from it and uses the number box.
        placements: One option per PLACEMENT, for the funding ``<select>``.  The
            script hides the whole row where the chosen ``(unit, interval)``
            pair allows only one.
        end_bounds: Every shape the "Ends" control offers, worded.  It rides
            on this bundle rather than being resolved separately for the same
            reason the five cadence projections do: a route that took them
            apart would resolve two producers for one render.  Unlike the
            others it SURVIVES plan step R7c -- the closing bound is authored
            the same way before and after the cutover.
        free_unit_ids: The units that take ANY positive interval, and therefore
            use the number box rather than the ``<select>``.  A fourth
            projection, and it decides which of the two ``interval_n`` controls
            is ENABLED in the server's own render -- so it is the one that
            matters before any script runs.  An adversarial review of plan step
            R7b-2 found it still being computed in Jinja while the template's
            own comment said projections belong here.
    """

    options: tuple["CadenceOption", ...]
    options_json: str
    nominal_days: tuple["NominalDayOption", ...]
    end_bounds: tuple["EndBoundOption", ...]
    units: tuple["CadenceOption", ...]
    fixed_intervals: tuple["CadenceOption", ...]
    placements: tuple["CadenceOption", ...]
    free_unit_ids: frozenset[int]


def picker_model() -> PickerModel:
    """Return the form's options, their projections and their JSON, from one call.

    **Serialized and projected here rather than in each form's route or
    template** so the two template kinds cannot disagree about either shape, and
    so the set the browser filters is the same one
    :func:`~app.services.recurrence.is_authorable` validates against on the way
    back in.

    Returns:
        The :class:`PickerModel`.

    Raises:
        RuntimeError: If ``ref_cache`` has not been initialized.
        KeyError: See :func:`cadence_options`.
    """
    options = cadence_options()
    return PickerModel(
        options=options,
        options_json=json.dumps(
            [asdict(option.wire) for option in options],
        ),
        nominal_days=nominal_day_options(),
        end_bounds=end_bound_options(),
        units=_first_by(options, lambda option: option.unit_id),
        fixed_intervals=_first_by(
            [option for option in options if option.interval_n is not None],
            lambda option: (option.unit_id, option.interval_n),
        ),
        placements=_first_by(options, lambda option: option.placement_id),
        free_unit_ids=frozenset(
            option.unit_id for option in options if option.interval_n is None
        ),
    )


@dataclass(frozen=True)
class SelectedCadence:
    """Which option each of the three cadence controls starts on.

    An EDIT form's counterpart to :func:`cadence_options`: the options say what
    may be chosen, this says what IS chosen.  Ids rather than enum members for
    the same reason :class:`CadenceOption` carries them -- the template
    compares them to ``<option value>``\\ s and computes nothing.

    Attributes:
        unit_id: The ``ref.recurrence_units`` id to preselect.
        interval_n: The interval to prefill.
        placement_id: The ``ref.period_placements`` id to preselect.
    """

    unit_id: int
    interval_n: int
    placement_id: int


def selected_cadence(reading: PatternReading) -> SelectedCadence:
    """Return the controls' starting state for a STORED rule's cadence.

    Projects a rule's cadence onto the ids the form posts, so the edit form's
    preselection cannot disagree with what the rule actually means.

    **It takes the READING rather than the stored columns, and plan step R7c-b
    is why.**  It used to take ``(pattern_id, interval_n)`` and decode them
    here -- which made the edit form the ONE reader still deriving a rule's
    unit and placement from the closed pattern set, while
    :func:`~app.services.recurrence.recurrence_spec` had moved to the authored
    ``unit_id`` / ``placement_id`` columns.  Two readers of one cadence, in a
    step whose whole claim is that every reader moved across.  The reading now
    arrives from :func:`~app.services.recurrence.stored_cadence`, which is
    where the "which columns say what" boundary is stated once.

    **A ``<select>`` whose selected value is absent from its options does not
    fail -- it silently becomes a different value**, which is why this is
    derived rather than assembled per form.  Measured on the transaction edit
    form before plan step R2e-2: no option carried ``selected``, so the browser
    fell back to the first in document order, which is the empty "Does not
    repeat" entry -- and submitting THAT deletes the rule and sweeps its future
    rows.  The caller asks :func:`~app.services.recurrence.stored_cadence`
    FIRST and renders the controls unset when the answer is ``None``, so this
    function is never asked about a cadence it cannot project.

    Args:
        reading: What the stored rule says on both authored axes, from
            :func:`~app.services.recurrence.stored_cadence`.

    Returns:
        The :class:`SelectedCadence`.

    Raises:
        RuntimeError: If ``ref_cache`` has not been initialized.
    """
    return SelectedCadence(
        unit_id=ref_cache.recurrence_unit_id(reading.cadence.unit),
        interval_n=reading.cadence.interval_n,
        placement_id=ref_cache.period_placement_id(reading.placement),
    )


__all__ = [
    "CadenceOption",
    "CadenceWire",
    "EndBoundOption",
    "NominalDayOption",
    "PickerModel",
    "SelectedCadence",
    "cadence_options",
    "end_bound_options",
    "nominal_day_options",
    "picker_model",
    "selected_cadence",
]
