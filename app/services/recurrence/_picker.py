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
from dataclasses import asdict, dataclass

from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.services.recurrence._frequency import (
    authorable_cadences,
    decode_pattern,
)

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
class CadenceOption:
    """One storable cadence, identified and worded for the form.

    A plain value so the form templates DISPLAY and never compute: every id
    goes in an ``<option value>`` and every label between the tags, with no
    lookup, no fallback expression and no ``name`` string in the template at
    all.

    Attributes:
        unit_id: The ``ref.recurrence_units`` id the form posts.  An id because
            that is the project's IDs-for-logic invariant and what plan step
            R7c's ``unit_id`` column will hold; the enum member it names is
            this package's business, not the template's.
        unit_label_one: What the unit is called after a ``1``.
        unit_label_many: What it is called after any other count.
        interval_n: The interval this option fixes, or ``None`` when ANY
            positive interval is storable -- which is what tells the form to
            render a number box rather than a select.
        placement_id: The ``ref.period_placements`` id the form posts.
        placement_label: The human copy for the placement option.
    """

    unit_id: int
    unit_label_one: str
    unit_label_many: str
    interval_n: int | None
    placement_id: int
    placement_label: str


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
                unit_id=ref_cache.recurrence_unit_id(cadence.unit),
                unit_label_one=label_one,
                unit_label_many=label_many,
                interval_n=cadence.interval_n,
                placement_id=ref_cache.period_placement_id(cadence.placement),
                placement_label=_PLACEMENT_LABELS[cadence.placement],
            )
        )
    return tuple(options)


@dataclass(frozen=True)
class PickerModel:
    """Everything the recurrence form needs to render its three controls.

    Both projections of ONE :func:`cadence_options` call, bundled because a
    route that took them separately would resolve the same producer twice in
    one request -- the redundancy this project treats as a DRY violation rather
    than a cost question, since two calls are two chances to disagree.

    Attributes:
        options: The offer set, for the server-rendered ``<option>`` lists.
        options_json: The same set as JSON, for the script that LINKS the
            controls.  It needs the whole set rather than the subset any one
            control renders: the unit decides which intervals may show, and the
            ``(unit, interval)`` pair decides which placements may.
    """

    options: tuple["CadenceOption", ...]
    options_json: str


def picker_model() -> PickerModel:
    """Return the form's options and their JSON, from one resolution.

    **Serialized here rather than in each form's route** so the two template
    kinds cannot disagree about the wire shape, and so the set the browser
    filters is the same one :func:`~app.services.recurrence.is_authorable`
    validates against on the way back in.

    Returns:
        The :class:`PickerModel`.

    Raises:
        RuntimeError: If ``ref_cache`` has not been initialized.
        KeyError: See :func:`cadence_options`.
    """
    options = cadence_options()
    return PickerModel(
        options=options,
        options_json=json.dumps([asdict(option) for option in options]),
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


def selected_cadence(pattern_id: int, interval_n: int) -> SelectedCadence:
    """Return the controls' starting state for a STORED rule.

    Decodes the closed-set columns through the same seam every other reader
    uses and projects the result onto the ids the form posts, so the edit
    form's preselection cannot disagree with what the rule actually means.

    **A ``<select>`` whose selected value is absent from its options does not
    fail -- it silently becomes a different value**, which is why this is
    derived rather than assembled per form.  Measured on the transaction edit
    form before plan step R2e-2: no option carried ``selected``, so the browser
    fell back to the first in document order, which is the empty "Does not
    repeat" entry -- and submitting THAT deletes the rule and sweeps its future
    rows.  The caller checks membership FIRST
    (:func:`~app.services.recurrence.modelled_pattern`) and renders the
    controls unset when the answer is "none", so this function is never asked
    about a pattern it cannot decode.

    Args:
        pattern_id: The stored ``recurrence_rules.pattern_id``.
        interval_n: The stored ``recurrence_rules.interval_n``.

    Returns:
        The :class:`SelectedCadence`.

    Raises:
        RecurrenceResolutionError: *pattern_id* names no modelled pattern, or
            the stored interval is not positive -- see
            :func:`~app.services.recurrence.decode_pattern`.
        RuntimeError: If ``ref_cache`` has not been initialized.
    """
    reading = decode_pattern(pattern_id, interval_n)
    return SelectedCadence(
        unit_id=ref_cache.recurrence_unit_id(reading.cadence.unit),
        interval_n=reading.cadence.interval_n,
        placement_id=ref_cache.period_placement_id(reading.placement),
    )


__all__ = [
    "CadenceOption",
    "PickerModel",
    "SelectedCadence",
    "cadence_options",
    "picker_model",
    "selected_cadence",
]
