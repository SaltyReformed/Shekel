"""
Shekel Budget App -- The recurrence write door

Every recurrence rule in the application is written here.  Before this module
there were nine places that could write one: six that constructed a rule and
three that mutated one in place (the edit form's update path,
``loan_recurrence_sync._sync_loan_cadence``, and
``pay_period_admin``'s schedule-rebuild re-pointer, itself deleted at plan
step R7b-4), each setting the columns it
happened to care about.

The shape that leaves nothing for a writer to get half-right:

* a caller states what it AUTHORS
  (:class:`~app.services.recurrence.RecurrenceSpec` -- a cadence since plan
  step R7b, never a ``ref.recurrence_patterns`` id, and never a column);
* :func:`_author` writes that whole spec, and it is the only function in the
  application that assigns a column of ``budget.recurrence_rules``;
* every value derived on write is taken from the same
  :func:`~app.services.recurrence.resolve` call that validates the spec --
  never from the payload, which is what closes defect D1.

**Since plan step R7c-a it writes a SECOND set of columns**, and that is
deliberate rather than a lapse into the redundancy this arc removes.  It is the
EXPAND half of an expand / migrate / contract: the closed-set columns
(``pattern_id`` and its parameters) stay authoritative and the two-axis columns
(``unit_id`` / ``placement_id`` / ``shift_id`` / ``starts_on`` /
``nominal_day``) are written beside them and read by nobody.  R7c-b moves the
readers across; R7c-c drops the closed set.  Both sides come from ONE
``resolve`` call in ONE function, so no WRITE can leave them disagreeing --
which is the property a dual write has to earn.

**The second set is not yet a whole cadence, and the missing half is the
INTERVAL.**  ``interval_n`` is the closed set's column: ``encode_cadence``
writes ``1`` for every pattern whose interval is baked into its NAME, so a
Quarterly rule stores ``(interval_n = 1, unit_id = month)``.  Every reader goes
through ``decode_pattern``, which answers ``3`` from the pattern and reads the
column only for ``Every N Periods``, so nothing is wrong today -- but the pair
must not be read at face value before R7c-c re-points the column with the
migration that drops ``pattern_id``.

**It is not the stronger property, and the difference is stated rather than
glossed.**  ``starts_on`` is measured against the owner's SCHEDULE, so a
schedule rebuilt without any rule being written moves the derivation and leaves
the column where it was.  Nothing reads the column in this leaf, so nothing is
wrong; R7c-b re-runs the backfill before it switches the readers, which is
where that window closes.

**A partial change is expressed as a whole one.**  The three in-place writers
do not set a field; they read the rule's authored state back with
:func:`~app.services.recurrence.recurrence_spec` -- the READ door's, in
``_reading`` -- change the one fact they own with ``dataclasses.replace``, and
re-author.  So "the loan's payment day moved" is stated as a new spec, and the
first occurrence is re-derived from it in the same call rather than left
holding what a previous cadence implied -- which is what keeps the stored
``starts_on`` and the day it is derived from from disagreeing across an edit.

Flask-isolated (plain values in, no ``request`` / ``session`` reads) and it
never commits: writes flush into the caller's transaction, which owns the
boundary.
"""
from app import ref_cache
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.services.pay_calendar import PayCalendar
from app.services.recurrence._frequency import encode_cadence
from app.services.recurrence._occurrence import first_occurrence
from app.services.recurrence._resolution import RecurrenceSpec, resolve


def _author(
    rule: RecurrenceRule, spec: RecurrenceSpec, calendar: PayCalendar,
) -> None:
    """Write *spec* onto *rule*, with the derived phase filled in.

    The ONE place a recurrence rule's columns are assigned.  It writes the
    authored spec verbatim -- every column of ``budget.recurrence_rules`` that
    a user controls -- plus the values derived on write: ``offset_periods``,
    and since plan step R7c-a the five two-axis columns nothing reads yet.

    **The two-axis columns are a DERIVATION being frozen, not a cache**, and
    the distinction is plan step R2d's.  Until R7c-b's readers move across they
    are written and never read, so nothing can consume a stale one; from R7c-b
    they are AUTHORED -- ``starts_on`` is what the form collects -- so for a
    form-authored rule there is no input left to lag behind.  The window in
    which a stored derivation could drift is the window in which nothing reads
    it.

    **A LOAN PAYMENT is the exception, and it is a narrow one.**  Its bound is
    written from the loan's contract rather than typed, and for a day-less loan
    rule ``starts_on`` is still the payday of the paycheck that installment
    falls in -- a function of the schedule after R7c-b as well as before.  Both
    of the developer's live loan payments fire on a day of the month, where the
    installment IS an occurrence and the value is contract-derived rather than
    schedule-derived; plan ledger row **D6** is where the day-less shape is
    tracked.

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
        RecurrenceGenerationError: When
            :func:`~app.services.recurrence.first_occurrence` finds no pay
            period spanning the anchor.  Unreachable from a value ``resolve``
            produced -- it refuses an empty schedule and returns an anchor at
            or after the opening payday -- and declared because this door is
            what a caller catches around, and a raise it does not name is one
            no handler above it is written for.
    """
    encoded = encode_cadence(spec.interval_n, spec.unit, spec.placement)
    resolved = resolve(spec, calendar)

    rule.user_id = spec.user_id
    rule.pattern_id = ref_cache.recurrence_pattern_id(encoded.pattern)
    rule.interval_n = encoded.interval_n
    rule.offset_periods = resolved.offset_periods
    # The two-axis columns, written beside the closed-set ones they replace
    # (plan step R7c-a, the EXPAND half).  **Nothing reads them until plan step
    # R7c-b**; writing them here from the SAME ``resolve`` call that produced
    # the phase is what keeps the two representations equal on every write, so
    # the leaf that switches the readers over switches them onto values the
    # door has been maintaining rather than onto a backfill that has been
    # sitting still since the migration ran.
    #
    # ``first_occurrence`` rather than ``resolved.anchor_date``, and the
    # difference is the whole of the D28 ruling: the anchor is the first
    # occurrence for a calendar cadence and the opening BOUND for a
    # pay-period one (ruling R-R8, plan ledger row D6), while ``starts_on``
    # carries one meaning for every unit.
    rule.unit_id = ref_cache.recurrence_unit_id(spec.unit)
    rule.placement_id = ref_cache.period_placement_id(spec.placement)
    rule.shift_id = ref_cache.business_day_shift_id(resolved.shift)
    rule.starts_on = first_occurrence(resolved, calendar)
    rule.nominal_day = resolved.nominal_day
    rule.day_of_month = spec.day_of_month
    rule.due_day_of_month = spec.due_day_of_month
    rule.month_of_year = spec.month_of_year
    # ``start_period_id`` is NOT assigned, and its absence is the point (plan
    # step R7b-4).  The column was the form's "First paycheck"; that step's
    # migration folded every value into ``start_date`` and left the column
    # NULL on all 46 live rules, so this door has nothing to write there and
    # no other writer exists.  Nulling it here anyway would be a fence over a
    # state nothing can reach.  Plan step R7c drops the column.
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
        RecurrenceGenerationError: See :func:`_author`.
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
        RecurrenceGenerationError: See :func:`_author`.
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
        RecurrenceGenerationError: See :func:`_author`.
    """
    _author(rule, spec, calendar)
