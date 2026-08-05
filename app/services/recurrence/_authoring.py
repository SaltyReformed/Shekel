"""
Shekel Budget App -- The recurrence write door

Every recurrence rule in the application is written here, and the reason is
structural rather than stylistic.  ``budget.recurrence_rules`` carries TWO
vocabularies for one cadence until plan step R4 -- the closed ``pattern_id``
set the engine reads, and the two-axis columns plan step R2b added beside it --
so the four two-axis columns are a persisted copy of a derivation over the
other nine.  A copy drifts when a writer moves one side and not the other, and
before this module there were nine places that could: six that constructed a
rule and three that mutated one in place (the edit form's update path,
``loan_recurrence_sync._sync_loan_cadence``, and
``pay_period_admin._repoint_recurrence_rules``).

The shape that removes the opportunity rather than policing it:

* a caller states what it AUTHORS
  (:class:`~app.services.recurrence.RecurrenceSpec`), never a column;
* :func:`~app.services.recurrence.resolve` turns that into every column at
  once, both vocabularies from one input;
* :meth:`~app.models.recurrence_rule.RecurrenceRule.reauthor` writes the whole
  value, so there is no partial write to leave a half behind.

**A partial change is expressed as a whole one.**  The three in-place writers
do not set a field; they read the rule's authored state back with
:func:`recurrence_spec`, change the one fact they own with
``dataclasses.replace``, and re-author.  So "the loan's payment day moved" is
stated as a new spec and the anchor is RE-DERIVED from it -- which is what
stops a ``payment_day`` edit from leaving an anchor pointing at the old day.

Flask-isolated (plain values in, no ``request`` / ``session`` reads) and it
never commits: writes flush into the caller's transaction, which owns the
boundary.
"""
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.services import pay_period_service
from app.services.recurrence._calendar import PeriodCalendar
from app.services.recurrence._resolution import RecurrenceSpec, resolve


def calendar_for(user_id: int) -> PeriodCalendar:
    """Load the owner's pay-period schedule for a resolution.

    A separate call rather than a lookup hidden inside :func:`author_rule` so
    a caller authoring MANY rules loads the schedule once and threads it --
    ``pay_period_admin._repoint_recurrence_rules`` re-authors every rule a
    user owns, and a per-rule query there would be the same read repeated N
    times for one answer.

    Args:
        user_id: The owning user.

    Returns:
        The owner's :class:`~app.services.recurrence._calendar.PeriodCalendar`.
    """
    return PeriodCalendar.from_pay_periods(
        pay_period_service.get_all_periods(user_id),
    )


def recurrence_spec(rule: RecurrenceRule) -> RecurrenceSpec:
    """Read a rule's authored state back out as a spec.

    The inverse of authoring, and what makes a partial change expressible
    without a partial write: a caller that owns ONE fact about a rule reads
    the spec, replaces that fact, and re-authors the whole value.

    Args:
        rule: The rule to read.

    Returns:
        The :class:`~app.services.recurrence.RecurrenceSpec` that authored it.
        Round-trips exactly -- resolution ignores ``interval_n`` for every
        pattern but ``Every N Periods`` (where the stored value IS the
        authored one), and re-derives ``offset_periods`` from the start period
        whenever the rule names one.
    """
    return RecurrenceSpec(
        user_id=rule.user_id,
        pattern_id=rule.pattern_id,
        interval_n=rule.interval_n,
        offset_periods=rule.offset_periods,
        day_of_month=rule.day_of_month,
        due_day_of_month=rule.due_day_of_month,
        month_of_year=rule.month_of_year,
        start_period_id=rule.start_period_id,
        start_date=rule.start_date,
        end_date=rule.end_date,
        max_occurrences=rule.max_occurrences,
    )


def build_transient_rule(
    spec: RecurrenceSpec, calendar: PeriodCalendar,
) -> RecurrenceRule:
    """Build a resolved rule WITHOUT adding it to the session.

    For the read-only callers that need a real rule to hand to the engine but
    must not persist one: the recurrence preview endpoint
    (``templates.preview_recurrence``), and the R1 characterization oracle.

    Args:
        spec: What to author.
        calendar: The owner's pay-period schedule.

    Returns:
        The unsaved, fully resolved :class:`RecurrenceRule`.

    Raises:
        RecurrenceResolutionError: When the spec cannot be resolved -- see
            :func:`~app.services.recurrence.resolve`.
    """
    return RecurrenceRule.create(resolve(spec, calendar))


def author_rule(
    spec: RecurrenceSpec, calendar: PeriodCalendar,
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
    rule = RecurrenceRule.create(resolve(spec, calendar))
    db.session.add(rule)
    db.session.flush()
    return rule


def reauthor_rule(
    rule: RecurrenceRule, spec: RecurrenceSpec, calendar: PeriodCalendar,
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
    rule.reauthor(resolve(spec, calendar))
