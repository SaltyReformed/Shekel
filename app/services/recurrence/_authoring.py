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
  (:class:`~app.services.recurrence.RecurrenceSpec` -- a cadence since plan
  step R7b, never a ``ref.recurrence_patterns`` id, and never a column);
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
from app import ref_cache
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.services.pay_calendar import PayCalendar
from app.services.recurrence._frequency import encode_cadence
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
    mismatch, a non-positive interval, a ``(unit, placement)`` pair with no
    anchor derivation, a day or month outside its column's domain, an empty
    schedule.  Re-checking those here would be a second copy of one judgement.
    The refusal that is NOT ``resolve``'s is the encode below: a cadence the
    closed pattern set cannot name resolves perfectly well and simply has
    nowhere to be written.  Taking the phase from that same result rather than deriving
    it again is the other half: two calls could not disagree today, but they
    would be two producers of one value, which is the shape this step exists
    to remove.

    **The ENCODE step is here and nowhere else** (plan step R7b).  A caller
    authors a cadence -- an interval, a unit and a placement -- and the table
    still names its cadence with a closed pattern set, so
    :func:`~app.services.recurrence._frequency.encode_cadence` turns the first
    into ``pattern_id`` plus the ``interval_n`` COLUMN.  Its inverse is the read
    door's ``decode_pattern``, and both read one table, so the round trip
    cannot half-drift.  Plan step R7c deletes this line together with the
    columns.

    **The encode runs FIRST, and an adversarial review of plan step R7b-2
    measured why.**  It is a pure table lookup that asks "has this cadence
    anywhere to be written at all", while ``resolve`` walks the cadence against
    a real calendar -- so resolving first meant doing arbitrary month
    arithmetic on a cadence that was about to be refused anyway.  Measured:
    ``(10000, YEAR)`` reached ``_months.clamped_day``, which builds a ``date``
    from a month ordinal, and raised ``ValueError: year must be in 1..9999``
    -- OUTSIDE this package's error hierarchy, so the recurrence preview's
    ``RecurrenceResolutionError`` handler did not catch it and a signed-in GET
    was an unhandled 500.  Refusing the unstorable before walking it makes that
    unreachable for every caller rather than for the ones with a schema in
    front of them.

    Args:
        rule: The rule to write, new or existing.
        spec: Its complete authored state.
        calendar: The owner's pay-period schedule.

    Raises:
        RecurrenceResolutionError: When *spec* names a cadence the closed
            pattern set cannot store (see ``encode_cadence``), or cannot be
            resolved against *calendar* (see
            :func:`~app.services.recurrence.resolve`).
    """
    encoded = encode_cadence(spec.interval_n, spec.unit, spec.placement)
    resolved = resolve(spec, calendar)

    rule.user_id = spec.user_id
    rule.pattern_id = ref_cache.recurrence_pattern_id(encoded.pattern)
    rule.interval_n = encoded.interval_n
    rule.offset_periods = resolved.offset_periods
    rule.day_of_month = spec.day_of_month
    rule.due_day_of_month = spec.due_day_of_month
    rule.month_of_year = spec.month_of_year
    rule.start_period_id = spec.start_period_id
    rule.start_date = spec.start_date
    # The closing bound is ONE authored value and TWO columns under an
    # exclusive arc (``ck_recurrence_rules_single_end_bound``), so it is split
    # here and rejoined at the read door -- the only two places the pair is
    # ever seen apart.  Assigning them from one ``columns()`` call rather than
    # from two accessors is what makes "never both" a property of this line
    # rather than of the value's two readers agreeing (plan step R7b-3).
    end_columns = spec.end_bound.columns()
    rule.end_date = end_columns.end_date
    rule.max_occurrences = end_columns.max_occurrences


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
