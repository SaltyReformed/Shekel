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
from app.services.recurrence._frequency import (
    authorable_cadences,
    decode_pattern,
    fires_on_day_of_month,
)
from app.services.recurrence._months import (
    MONTH_SPANNING_UNITS,
    months_per_step,
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


def _months_per_unit(unit: RecurrenceUnitEnum) -> int | None:
    """Return the months one *unit* spans, or ``None`` when it spans none.

    :func:`~app.services.recurrence._months.months_per_step` made TOTAL over
    the unit enum, which is what a form needs: that function refuses a unit
    with no reading in months because its callers reach it only after routing
    to the calendar family, while this one is asked about every offered unit
    including the pay-period one.  Membership is tested against
    :data:`~app.services.recurrence._months.MONTH_SPANNING_UNITS`, which IS
    that function's own key set, so the guard and the call cannot come to
    disagree about which units have an answer.

    Args:
        unit: The cadence unit.

    Returns:
        1 for ``MONTH``, 12 for ``YEAR``, ``None`` for a unit not measured in
        calendar months.
    """
    if unit not in MONTH_SPANNING_UNITS:
        return None
    return months_per_step(unit, 1)


@dataclass(frozen=True)
class CadenceOption:  # pylint: disable=too-many-instance-attributes
    """One storable cadence, identified and worded for the form.

    A plain value so the form templates DISPLAY and never compute: every id
    goes in an ``<option value>`` and every label between the tags, with no
    lookup, no fallback expression and no ``name`` string in the template at
    all.

    Pylint: ``too-many-instance-attributes`` (8/7) -- one row of the offer set,
    serving TWO consumers that between them read every field, and FLATNESS is
    what both need.  The three labels are the template's (through the
    projections on :class:`PickerModel`); the five ids and facts are
    ``recurrence_form.js``'s, through :attr:`PickerModel.options_json`.
    Grouping either group into a nested value would put back exactly the lookup
    the first paragraph says this class exists to remove, in the layer least
    able to afford it.  **An adversarial review of plan step R7b-2 noted that
    the split along those two consumers IS available** -- the JSON currently
    carries three labels the browser never reads -- and it is ledger finding
    **D31** rather than this step's, because it changes the wire shape the
    script parses and that needs its own browser pass.  Mirrors the
    :class:`~app.services.recurrence.RecurrenceSpec` precedent one module over.

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
        anchors_day_of_month: Whether occurrences land on a DAY of the month,
            which is what decides whether the form shows its Day of Month
            input.  Answered by the anchor router itself
            (:func:`~app.services.recurrence._frequency.fires_on_day_of_month`)
            rather than by a unit test the script could repeat, because it is
            a property of the ``(unit, placement)`` PAIR: a monthly cadence
            funded from the month's first paycheck reads no day at all.
        months_per_unit: How many calendar months ONE of this unit spans -- 1
            for a month, 12 for a year -- or ``None`` for a unit not measured
            in months.  With the chosen interval it gives the cycle's month
            span, and a Month control narrows nothing unless that span exceeds
            one.
    """

    unit_id: int
    unit_label_one: str
    unit_label_many: str
    interval_n: int | None
    placement_id: int
    placement_label: str
    anchors_day_of_month: bool
    months_per_unit: int | None


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
                anchors_day_of_month=fires_on_day_of_month(
                    cadence.unit, cadence.placement,
                ),
                months_per_unit=_months_per_unit(cadence.unit),
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


@dataclass(frozen=True)
class PickerModel:
    """Everything the recurrence form needs to render its three controls.

    Every projection of ONE :func:`cadence_options` call, bundled because a
    route that took them separately would resolve the same producer twice in
    one request -- the redundancy this project treats as a DRY violation rather
    than a cost question, since two calls are two chances to disagree.

    Attributes:
        options: The whole offer set.  Read by :attr:`options_json`'s own
            construction and by nothing in the templates -- each control
            renders from its projection below -- so it is the value the other
            five are derived FROM rather than one a consumer reaches for.
        options_json: The same set as JSON, for the script that LINKS the
            controls.  It needs the whole set rather than the subset any one
            control renders: the unit decides which intervals may show, and the
            ``(unit, interval)`` pair decides which placements may.
        units: One option per UNIT, for the unit ``<select>``.  The MONTH unit
            has four offered readings and must render one entry, or the user is
            asked to choose between four things called "months".
        fixed_intervals: One option per ``(unit, interval)`` whose interval is
            FIXED, for the interval ``<select>``.  A unit that takes any
            positive interval is absent from it and uses the number box.
        placements: One option per PLACEMENT, for the funding ``<select>``.  The
            script hides the whole row where the chosen ``(unit, interval)``
            pair allows only one.
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
        options_json=json.dumps([asdict(option) for option in options]),
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
