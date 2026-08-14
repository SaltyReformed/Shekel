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
* :func:`edit_form_start_date` -- what the "Starts on" box holds;
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
from flask_login import current_user

from app.schemas.validation import EFFECTIVE_DATE_MAX, EFFECTIVE_DATE_MIN
from app.services import pay_period_service
from app.services.loan_recurrence_sync import owns_validity_window
from app.services.recurrence import (
    NEVER_ENDS,
    UNAVAILABLE_PATTERN_MESSAGE,
    EndBound,
    PickerModel,
    SelectedCadence,
    end_bound_from_columns,
    modelled_pattern,
    picker_model,
    selected_cadence,
)


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
        selected_start_date: What the "Starts on" box holds.
        selected_end_bound: Which of the "Ends" control's three shapes is
            selected, and its value.
        bounds_are_derived: Whether BOTH bound controls render read-only,
            because the app derives this definition's validity window.
        start_date_min: The earliest date the "Starts on" box offers, and
            start_date_max the latest.  The SCHEMA's own window, carried here
            rather than written in the template so the browser hint and the
            refusal cannot state two different ranges.
        start_date_max: See *start_date_min*.
    """

    picker: PickerModel
    selected_cadence: SelectedCadence | None
    selected_start_date: date | None
    selected_end_bound: EndBound
    bounds_are_derived: bool
    start_date_min: date = EFFECTIVE_DATE_MIN
    start_date_max: date = EFFECTIVE_DATE_MAX


def create_form_default_start_date() -> date | None:
    """Return the "Starts on" date a CREATE form opens with.

    **The current paycheck's own payday, and restoring this default is a
    money fix** (adversarial review of plan step R7b-4).  The control this
    replaced was a ``<select>`` of pay periods with NO empty option, so it
    always submitted, and it preselected the current period -- which meant
    every recurring definition ever created through either form carried an
    opening bound of "the paycheck I am in".  A date box defaulting to EMPTY
    is not the same request: an empty bound resolves to the schedule's opening
    payday, and the create routes generate over
    ``GenerationSchedule.for_user`` -- every period the owner has, with no
    lower window bound -- so a rent template created today would write
    projected debits into every pay period that has already closed.  Measured
    on the developer's schedule shape: 5 backdated rows, ``$10,000.00`` at a
    ``$2,000.00`` rent.

    The bound belongs on the RULE rather than on the caller's window, which is
    why this is a form default and not an ``effective_from``: plan step R4b-1
    deleted those defaults precisely so a caller's window could not look like a
    property of the recurrence (defect **D2**).

    ``None`` when the owner's schedule does not cover today, which leaves the
    box empty and the rule unbounded -- the same answer the old ``<select>``
    gave there, since with no current period nothing was preselected and the
    browser submitted the FIRST option, the earliest period.

    Returns:
        The current pay period's ``start_date``, or ``None``.
    """
    current = pay_period_service.get_current_period(current_user.id)
    return None if current is None else current.start_date


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
        reader answers for ``None`` -- but its OPENING bound is DEFAULTED
        rather than empty; see :func:`create_form_default_start_date` for why
        that difference is the one place create and edit must not share an
        answer.
    """
    return RecurrenceFormState(
        picker=picker_model(),
        selected_cadence=edit_form_cadence(template),
        selected_start_date=(
            create_form_default_start_date() if template is None
            else edit_form_start_date(template)
        ),
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
    different way.  What keeps ``UNAVAILABLE_PATTERN_MESSAGE``'s promise is the
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
        OR when its rule names a pattern this application no longer models --
        the last case having flashed the explanation.

    """
    rule = None if template is None else template.recurrence_rule
    if rule is None:
        return None
    if modelled_pattern(rule.pattern_id) is None:
        flash(UNAVAILABLE_PATTERN_MESSAGE, "warning")
        return None
    return selected_cadence(rule.pattern_id, rule.interval_n)


def edit_form_start_date(template: Any) -> date | None:
    """Return the date an EDIT form's "Starts on" control starts on.

    The OPENING half of :func:`edit_form_end_bound`, and a function rather
    than a Jinja attribute walk for the same reason its sibling is one: both
    forms ask the question and a template that reached through
    ``template.recurrence_rule.start_date`` would state the ``None``-guard
    twice.

    **It cannot fail on an unreadable rule**, exactly like its sibling: it
    reads one bound column and no cadence, so a rule naming a pattern the
    application no longer models still renders its start correctly while the
    cadence controls render unset above the warning.

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate`` being
            edited, or ``None`` on a create form.  Read for
            ``recurrence_rule`` only; not mutated.

    Returns:
        The stored opening bound, or ``None`` when the definition has no rule
        or the rule states none -- both of which render an empty box, because
        both mean the same thing to the resolver: start with the schedule.
    """
    rule = None if template is None else template.recurrence_rule
    return None if rule is None else rule.start_date


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
    "create_form_default_start_date",
    "edit_form_bounds_are_derived",
    "edit_form_cadence",
    "edit_form_end_bound",
    "edit_form_start_date",
    "recurrence_form_state",
]
