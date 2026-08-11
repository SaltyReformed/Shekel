"""
Shekel Budget App -- The recurrence write door

Every recurrence rule in the application is written here.  Before this module
there were nine places that could write one: six that constructed a rule and
three that mutated one in place (the edit form's update path,
``loan_recurrence_sync._sync_loan_cadence``, and
``pay_period_admin._repoint_recurrence_rules``), each setting the columns it
happened to care about.

The shape that leaves nothing for a writer to get half-right:

* a caller states what it AUTHORS
  (:class:`~app.services.recurrence.RecurrenceSpec`), never a column;
* :func:`_author` writes that whole spec, and it is the only function in the
  application that assigns a column of ``budget.recurrence_rules``;
* the ONE value derived on write, ``offset_periods``, is taken from the same
  :func:`~app.services.recurrence.resolve` call that validates the spec --
  never from the payload, which is what closes defect D1.

**A partial change is expressed as a whole one.**  The three in-place writers
do not set a field; they read the rule's authored state back with
:func:`~app.services.recurrence.recurrence_spec` -- the READ door's, in
``_reading`` -- change the one fact they own with ``dataclasses.replace``, and
re-author.  So "the loan's payment day moved" is stated as a new spec, and
because the rule's first occurrence is DERIVED from ``day_of_month`` rather
than stored beside it (plan step R2d), the two cannot come to disagree at all.

Flask-isolated (plain values in, no ``request`` / ``session`` reads) and it
never commits: writes flush into the caller's transaction, which owns the
boundary.
"""
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.services.pay_calendar import PayCalendar
from app.services.recurrence._resolution import RecurrenceSpec, resolve


def _author(
    rule: RecurrenceRule, spec: RecurrenceSpec, calendar: PayCalendar,
) -> None:
    """Write *spec* onto *rule*, with the derived phase filled in.

    The ONE place a recurrence rule's columns are assigned.  It writes the
    authored spec verbatim -- every column of ``budget.recurrence_rules`` that
    a user controls -- plus the single value that is derived on write,
    ``offset_periods``.  Nothing else about the row is computed, because
    nothing else about it is a derivation: the two-axis view lives in
    :func:`~app.services.recurrence.resolve` and is never stored (plan step
    R2d).

    **Resolved BEFORE the write, and the same call does both jobs.**  A
    recurrence that cannot be resolved must not reach the table, and
    ``resolve`` is where every such refusal already lives -- an owner
    mismatch, an unmodelled pattern, a non-positive interval, an empty
    schedule.  Re-checking those four here would be a second copy of one
    judgement.  Taking the phase from that same result rather than deriving
    it again is the other half: two calls could not disagree today, but they
    would be two producers of one value, which is the shape this step exists
    to remove.

    Args:
        rule: The rule to write, new or existing.
        spec: Its complete authored state.
        calendar: The owner's pay-period schedule.

    Raises:
        RecurrenceResolutionError: When *spec* cannot be resolved against
            *calendar* -- see :func:`~app.services.recurrence.resolve`.
    """
    resolved = resolve(spec, calendar)

    rule.user_id = spec.user_id
    rule.pattern_id = spec.pattern_id
    rule.interval_n = spec.interval_n
    rule.offset_periods = resolved.offset_periods
    rule.day_of_month = spec.day_of_month
    rule.due_day_of_month = spec.due_day_of_month
    rule.month_of_year = spec.month_of_year
    rule.start_period_id = spec.start_period_id
    rule.start_date = spec.start_date
    rule.end_date = spec.end_date
    rule.max_occurrences = spec.max_occurrences


def build_transient_rule(
    spec: RecurrenceSpec, calendar: PayCalendar,
) -> RecurrenceRule:
    """Build a resolved rule WITHOUT adding it to the session.

    For the read-only caller that needs a real rule to hand to the engine but
    must not persist one: the recurrence preview endpoint
    (``templates.preview_recurrence``), which resolves the user's submitted
    form values so the preview shows what saving would actually produce.

    Args:
        spec: What to author.
        calendar: The owner's pay-period schedule.

    Returns:
        The unsaved, fully authored :class:`RecurrenceRule`.

    Raises:
        RecurrenceResolutionError: When the spec cannot be resolved -- see
            :func:`~app.services.recurrence.resolve`.
    """
    rule = RecurrenceRule()
    _author(rule, spec, calendar)
    return rule


def author_rule(
    spec: RecurrenceSpec, calendar: PayCalendar,
) -> RecurrenceRule:
    """Create a recurrence rule and flush it, so it carries an id.

    Flushed rather than merely added because every caller links the new rule
    onto a template's ``recurrence_rule_id`` immediately afterwards.

    Args:
        spec: What to author.
        calendar: The owner's pay-period schedule.

    Returns:
        The flushed :class:`RecurrenceRule`.

    Raises:
        RecurrenceResolutionError: When the spec cannot be resolved -- see
            :func:`~app.services.recurrence.resolve`.
    """
    rule = RecurrenceRule()
    _author(rule, spec, calendar)
    db.session.add(rule)
    db.session.flush()
    return rule


def reauthor_rule(
    rule: RecurrenceRule, spec: RecurrenceSpec, calendar: PayCalendar,
) -> None:
    """Replace an existing rule's entire authored state, in place.

    The rule keeps its primary key -- and therefore the owning template's
    ``recurrence_rule_id`` FK and every generated row's lineage -- while every
    column it defines is re-derived from *spec*.

    Args:
        rule: The rule to re-author.
        spec: Its complete new authored state.
        calendar: The owner's pay-period schedule.

    Raises:
        RecurrenceResolutionError: When the spec cannot be resolved -- see
            :func:`~app.services.recurrence.resolve`.
    """
    _author(rule, spec, calendar)
