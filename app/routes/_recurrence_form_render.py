"""
Shekel Budget App -- What an EDIT form's recurrence controls START ON

The render-side half of :mod:`app.routes._recurrence_form_helpers`, split out
at plan step R7b-4 when that module reached the 1,000-line cap for the second
time -- the same trigger that produced
:mod:`app.routes._recurrence_conflict_chooser` at plan step R2e-1, and the same
kind of seam: everything here answers a read-only question ABOUT a stored
definition, everything there acts on a SUBMITTED payload.

One entry point, :func:`recurrence_form_state`, composing four questions --
one per control the two template forms render:

* :func:`edit_form_cadence` -- which ``(unit, interval, placement)`` triple the
  three cadence controls preselect, or ``None`` (with a flashed explanation)
  when the stored pattern is one this application no longer models;
* :func:`edit_form_starts_on` -- what the "Starts on" row holds: the date
  and, on the one date shape that leaves it open, the day it means;
* :func:`edit_form_end_bound` -- which of the "Ends" control's three shapes is
  selected, and its value;
* :func:`edit_form_bounds_are_derived` -- whether the two bound controls render
  READ-ONLY, because the app derives this definition's validity window rather
  than accepting it.

**Functions rather than Jinja attribute walks, and that is the point of the
module.**  Each of these decides something from more than one stored fact --
which shape a bound is in, whether a pattern is modelled, whether a template is
the one ``loan_recurrence_sync`` writes for -- and deciding any of them in a
template would put a second statement of the rule in a place no gate reads.
Both forms take the composed :class:`RecurrenceFormState` rather than five
context keys, so they cannot come to disagree about what the same rule means --
and ``duplicate-code`` said so the moment plan step R7b-4 made the two render
blocks identical.

Route-layer rather than service because :func:`edit_form_cadence` flashes;
``CLAUDE.md::Architecture`` keeps services isolated from Flask globals.  The
leading underscore marks the module as route-internal.
"""
from dataclasses import dataclass
from datetime import date
from typing import Any

from flask import flash

from app.schemas.validation import EFFECTIVE_DATE_MAX, EFFECTIVE_DATE_MIN
from app.services.loan_recurrence_sync import owns_validity_window
from app.services.recurrence import (
    NEVER_ENDS,
    UNREADABLE_CADENCE_MESSAGE,
    EndBound,
    PickerModel,
    SelectedCadence,
    end_bound_from_columns,
    modelled_unit,
    offerable_nominal_days,
    picker_model,
    selected_cadence,
    stored_cadence,
)
from app.utils.dates import display_today


@dataclass(frozen=True)
class RecurrenceStart:
    """What the "Starts on" row of a recurrence form starts on.

    The two fields that state WHEN a rule first fires
    (:attr:`~app.models.recurrence_rule.RecurrenceRule.starts_on` and its
    0-or-1 ``nominal_day``), held as one value because the form renders them as
    one row and the table refuses them apart
    (``ck_recurrence_rules_nominal_day``).  Two context keys would let a route
    render a nominal day beside a date that never clamped it -- which the write
    door refuses, so it would be a 500 out of an ordinary edit.

    Attributes:
        starts_on: The date the definition first happens.
        nominal_day: The day it MEANS, when that date's own month was too short
            to hold it; ``None`` for every unambiguous date.
        day_choices: Which days *starts_on* leaves OPEN, from
            :func:`~app.services.recurrence.offerable_nominal_days` -- empty
            for every date that says its own day, which is all but a handful.
            It decides whether the "repeating on" control renders at all and
            which of its options are enabled, so the form is correct before any
            script runs and cannot offer a pair the write door refuses.
    """

    starts_on: date
    nominal_day: int | None
    day_choices: tuple[int, ...] = ()


@dataclass(frozen=True)
class RecurrenceFormState:
    """Everything the recurrence controls need in order to RENDER.

    One value rather than five context keys, and it is not tidiness: both
    template forms asked the same five questions in the same order, so
    ``duplicate-code`` reported the render blocks as identical the moment plan
    step R7b-4 added the fifth.  Passing one value is what makes the two forms
    unable to diverge -- which is the same defect class this step fixed on the
    lock predicate, where one caller asked a slightly broader question than the
    writer it was supposed to mirror.

    Attributes:
        picker: The offer sets every control chooses from
            (:func:`~app.services.recurrence.picker_model`).  Identical for
            every user and every form -- what a definition can be, not what
            this one is.
        selected_cadence: Which ``(unit, interval, placement)`` triple the
            three cadence controls preselect, or ``None`` on a create form AND
            for a rule naming a pattern this application no longer models.
        selected_start: What the "Starts on" row holds -- the date and the day
            it means.
        selected_end_bound: Which of the "Ends" control's three shapes is
            selected, and its value.
        bounds_are_derived: Whether BOTH bound controls render read-only,
            because the app derives this definition's validity window.
        starts_on_min: The earliest date the "Starts on" box offers, and
            starts_on_max the latest.  The SCHEMA's own window, carried here
            rather than written in the template so the browser hint and the
            refusal cannot state two different ranges.
        starts_on_max: See *starts_on_min*.
    """

    picker: PickerModel
    selected_cadence: SelectedCadence | None
    selected_start: RecurrenceStart
    selected_end_bound: EndBound
    bounds_are_derived: bool
    starts_on_min: date = EFFECTIVE_DATE_MIN
    starts_on_max: date = EFFECTIVE_DATE_MAX


def create_form_default_starts_on() -> date:
    """Return the "Starts on" date a CREATE form opens with.

    **TODAY, on the app's own civil clock**, and it is TOTAL -- which is what
    plan step R7c-b needed of it.  ``starts_on`` is the rule's first occurrence
    and the column is ``NOT NULL``, so a create form has to open on a real date
    rather than on an empty box the user might leave empty.

    **Defaulting it at all is a money fix**, and the measurement is worth
    keeping (adversarial review of plan step R7b-4).  The control this replaced
    was a ``<select>`` of pay periods with NO empty option, so it always
    submitted and it preselected the current period; a date box defaulting to
    EMPTY is not the same request, because an empty opening bound resolved to
    the schedule's first payday and the create routes generate over every
    period the owner has, with no lower window bound.  Measured on the
    developer's schedule shape: 5 backdated rows, ``$10,000.00`` at a
    ``$2,000.00`` rent.

    **Today rather than the current paycheck's payday**, which is what the
    bound-shaped version answered.  The two are the same request for a
    pay-period cadence -- ``resolve`` normalises any date onto the paycheck
    that hosts it, and today's paycheck is the one containing today -- and for
    a MONTH or YEAR cadence today is the honest reading of a control that now
    says when the thing first HAPPENS.  Neither can backdate: today is inside
    the current pay period, so the earliest row either produces is this one.

    It also removes the ``None`` the payday version had to answer for an owner
    whose schedule does not cover today.  That state left the box empty, which
    is now a submission the schema refuses -- so a user in it could not create a
    recurring definition at all.

    Returns:
        The app's civil today (:func:`app.utils.dates.display_today`).
    """
    return display_today()


def recurrence_form_state(template: Any) -> RecurrenceFormState:
    """Return the whole render state for *template*'s recurrence controls.

    The ONE call both edit routes and both create routes make, so a control
    cannot start on one thing in the transaction form and another in the
    transfer form.

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate`` being
            edited, or ``None`` on a create form.

    Returns:
        The :class:`RecurrenceFormState`.  A create form starts with no
        cadence selected and the unbounded CLOSING shape -- which is what each
        reader answers for ``None`` -- while its "Starts on" row is DEFAULTED
        rather than empty; see :func:`create_form_default_starts_on` for why
        that difference is a money decision rather than a convenience.
    """
    return RecurrenceFormState(
        picker=picker_model(),
        selected_cadence=edit_form_cadence(template),
        selected_start=edit_form_starts_on(template),
        selected_end_bound=edit_form_end_bound(template),
        bounds_are_derived=edit_form_bounds_are_derived(template),
    )


def edit_form_cadence(template: Any) -> SelectedCadence | None:
    """Return what an EDIT form's cadence controls start on, warning if unreadable.

    The render-side counterpart of the write door below, and the reason both
    edit routes call one function rather than each decoding the rule: the
    stored pattern and the set the application models can disagree, and until
    plan step R9 drops the table they DO -- the ``Once`` row survives its
    deleted enum member (ruling R-R11), and a hand-edited or migration-missed
    rule can name it.

    **Answering ``None`` is what makes the repair reachable.**  Before plan
    step R7b-2 the form met that state by keeping the stored pattern as a
    trailing ``<select>`` option, because a ``<select>`` whose selected value is
    absent from its options silently submits a DIFFERENT one -- on both forms
    the first entry, "Does not repeat", which deletes the rule and sweeps its
    future rows.  The two-axis controls carry no pattern id to preserve, so
    there is nothing to append: they render UNSET, and the warning says to
    choose a cadence.

    **Rendering unset does NOT make the empty submission safe, and an
    adversarial review of this step caught the gap.**  Unset means the unit
    ``<select>``'s first entry is the selected one, so saving the form
    unchanged posts exactly the "Does not repeat" that deletes the rule -- the
    same destruction the trailing option existed to prevent, reached a
    different way.  What keeps ``UNREADABLE_CADENCE_MESSAGE``'s promise is the
    server-side refusal in :func:`resolve_recurrence_rule_for_update` (see
    :data:`UNREPAIRED_CADENCE_CANNOT_BE_CLEARED`), which is the only layer
    holding both facts the disposition needs.

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate`` being
            edited, or ``None`` on a create form.  Read for
            ``recurrence_rule`` only; not mutated.

    Returns:
        The :class:`~app.services.recurrence.SelectedCadence` to preselect, or
        ``None`` when there is no template, when the template does not repeat,
        OR when its rule names a unit or a placement this application does
        not model -- the last case having flashed the explanation.

    """
    rule = None if template is None else template.recurrence_rule
    if rule is None:
        return None
    # **The AUTHORED columns, not the closed set's encoding of them** (plan
    # step R7c-b).  This asked ``modelled_pattern(rule.pattern_id)`` and then
    # decoded the unit and the placement out of that pattern, which left the
    # edit form as the ONE reader the step did not move across: every other
    # reader takes them from ``unit_id`` / ``placement_id`` through
    # ``recurrence_spec``.  ``stored_cadence`` asks about both at once and
    # is the SAME predicate ``resolve_recurrence_rule_for_update`` refuses the
    # empty submission with -- which matters more than the tidiness, because
    # rendering unset and refusing the clear are two halves of one promise: a
    # rule readable by one and not the other would render "Does not repeat"
    # selected and then be deleted by an unchanged save.
    reading = stored_cadence(rule)
    if reading is None:
        flash(UNREADABLE_CADENCE_MESSAGE, "warning")
        return None
    return selected_cadence(reading)


def edit_form_starts_on(template: Any) -> RecurrenceStart:
    """Return what an EDIT form's "Starts on" controls start on.

    The OPENING half of :func:`edit_form_end_bound`, and a function rather
    than a Jinja attribute walk for the same reason its sibling is one: both
    forms ask the question and a template that reached through
    ``template.recurrence_rule.starts_on`` would state the ``None``-guard
    twice.

    **It cannot fail on an unreadable rule**, exactly like its sibling: it
    reads two authored columns and no pattern, so a rule naming a pattern the
    application no longer models still renders its start correctly while the
    cadence controls render unset above the warning.

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate`` being
            edited, or ``None`` on a create form.  Read for
            ``recurrence_rule`` only; not mutated.

    Returns:
        The stored :class:`RecurrenceStart`, or the CREATE default when the
        definition has no rule -- see :func:`create_form_default_starts_on` for
        why that default is a date rather than an empty box.
    """
    rule = None if template is None else template.recurrence_rule
    if rule is None:
        # A create form offers no nominal day in the SERVER render: nothing has
        # chosen a cadence yet, and whether a date is ambiguous is a property
        # of the ``(unit, date)`` pair rather than of the date alone.  The
        # script shows the control the moment a cadence that fires on a day of
        # the month is picked.
        return RecurrenceStart(
            starts_on=create_form_default_starts_on(), nominal_day=None,
        )
    # ``modelled_unit`` rather than a raising reader: this function's whole
    # contract is that it cannot fail on a rule the cadence controls could not
    # display (see the docstring), and a ``ref`` id the enum does not name is
    # the same class of state.  It answers no choices, which renders the
    # control hidden above the warning that tells the user to pick a cadence.
    unit = modelled_unit(rule.unit_id)
    return RecurrenceStart(
        starts_on=rule.starts_on,
        nominal_day=rule.nominal_day,
        day_choices=(
            () if unit is None
            else offerable_nominal_days(unit, rule.starts_on)
        ),
    )


def edit_form_bounds_are_derived(template: Any) -> bool:
    """Return whether the form must render BOTH bound controls read-only.

    The render-side half of the refusal
    :data:`LOAN_PAYMENT_BOUND_IS_DERIVED` states, asking the one predicate
    that names what ``loan_recurrence_sync`` actually writes for
    (plan step R7b-4).  A thin pass-through, and it earns its place by being
    the ONE name the templates and both edit routes reach for: the question
    "is this control mine to set" is asked at four call sites, and four
    direct imports of a service predicate into route and template code is how
    one of them comes to ask a slightly different question -- which is the
    defect this step fixed.

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate`` being
            edited, or ``None`` on a create form.  A create form never locks:
            nothing derives a bound for a definition that does not exist yet,
            and the loan sync writes both bounds immediately after creation
            anyway (``_instances.materialize_initial_transfers``).

    Returns:
        ``True`` when the app derives this definition's validity window.
    """
    if template is None:
        return False
    return owns_validity_window(template)


def edit_form_end_bound(template: Any) -> EndBound:
    """Return the shape and value an EDIT form's "Ends" control starts on.

    The bound half of :func:`edit_form_cadence`, and the reason both edit
    routes call a function rather than reading the columns in Jinja: the
    control's three shapes are discriminated by which column is non-NULL
    (ruling R-R13's "absence is the discriminator", applied to the bound), and
    deciding that in a template would be a second statement of the shape set
    -- keyed on a name string, which this project rules out.

    **It cannot fail on an unreadable rule**, unlike :func:`edit_form_cadence`:
    it reads the two bound columns and no cadence, so a rule naming a pattern
    the application no longer models still renders its stop correctly while the
    cadence controls render unset above the warning.  A form that lost the
    user's stop date because its cadence was unreadable would be the repair
    path destroying what it came to fix.

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate`` being
            edited, or ``None`` on a create form.  Read for
            ``recurrence_rule`` only; not mutated.

    Returns:
        The stored :class:`~app.services.recurrence.EndBound`, or
        :data:`~app.services.recurrence.NEVER_ENDS` when the definition has no
        rule -- which is what a create form starts on.

    Raises:
        RecurrenceResolutionError: The row carries both bound columns, which
            ``ck_recurrence_rules_single_end_bound`` refuses in the table.
    """
    rule = None if template is None else template.recurrence_rule
    if rule is None:
        return NEVER_ENDS
    return end_bound_from_columns(rule.end_date, rule.max_occurrences)


__all__ = [
    "RecurrenceFormState",
    "RecurrenceStart",
    "create_form_default_starts_on",
    "edit_form_bounds_are_derived",
    "edit_form_cadence",
    "edit_form_end_bound",
    "edit_form_starts_on",
    "recurrence_form_state",
]
