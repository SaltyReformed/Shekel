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

**Since plan step R7c-b the table's AUTHORED columns and its ENCODED ones are
two clearly different things, and this function is where the line is drawn.**
The rule states its recurrence in ``unit_id`` / ``placement_id`` / ``shift_id``
/ ``starts_on`` / ``nominal_day``, which are what every reader now takes;
``pattern_id`` / ``interval_n`` / ``day_of_month`` are the closed set's STORAGE
ENCODING of that same statement, derived here and dropped at plan step R7c-c.
Two representations, one producer, and the direction runs one way -- which is
what the R7c-a leaf's dual write earned and this one keeps.

**``interval_n`` is the one value the encoding still OWNS**, and reading it off
the column is the mistake this leaf must not make.  ``encode_cadence`` writes
``1`` for every pattern whose interval is baked into its NAME, so a Quarterly
rule stores ``(interval_n = 1, unit_id = month)`` -- MONTHLY at face value, 12
occurrences a year where 4 are owed.  The read door takes it through
``_frequency.stored_interval``, which names that boundary; R7c-c re-points the
column in the migration that drops ``pattern_id``.

**The stored first occurrence no longer LAGS anything, which is what changed at
this step.**  While ``starts_on`` was derived from the closed-set columns plus
the owner's schedule, a schedule rebuilt with no rule written moved the
derivation and left the column -- so R7c-b re-ran the backfill before switching
the readers.  From here the column is AUTHORED: the form collects it, the loan
sync writes it from the loan's own contract, and neither has an input left to
lag.  The one derivation that remains is the pay-period NORMALISATION, and it
runs on every write.

**A partial change is expressed as a whole one.**  The two in-place writers do
not set a field; they read the rule's authored state back with
:func:`~app.services.recurrence.recurrence_spec` -- the READ door's, in
``_reading`` -- change the one fact they own with ``dataclasses.replace``, and
re-author.  So "the loan's payment day moved" is stated as a new spec, and every
encoded column is re-derived from it in the same call rather than left holding
what a previous cadence implied.

Flask-isolated (plain values in, no ``request`` / ``session`` reads) and it
never commits: writes flush into the caller's transaction, which owns the
boundary.
"""
from app import ref_cache
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.services.pay_calendar import PayCalendar
from app.services.recurrence._frequency import (
    encode_cadence,
    fires_on_day_of_month,
)
from app.services.recurrence._resolution import RecurrenceSpec, resolve


def _author(
    rule: RecurrenceRule, spec: RecurrenceSpec, calendar: PayCalendar,
) -> None:
    """Write *spec* onto *rule*, with every derived and encoded column filled in.

    The ONE place a recurrence rule's columns are assigned.  It writes the
    authored spec -- the four columns that state the recurrence, plus
    ``due_day_of_month`` and the closing bound's exclusive arc -- and derives
    the rest from the same ``resolve`` call that validates it: the cycle phase,
    the pay-period normalisation, and the closed set's storage encoding.

    **The two-axis columns are AUTHORED from plan step R7c-b**, which is what
    retires the "a stored derivation is a cache" objection ruling R2d raised
    against writing them at all.  ``starts_on`` is what the form collects and
    what the loan sync writes from a contract; there is no input left for it to
    lag behind.  The window in which it could drift was the window in which
    nothing read it, and that window closed with R7c-b's re-backfill.

    **A day-less LOAN PAYMENT is the one value still measured against the
    schedule**, and it is narrow.  Its date is the loan's first contractual
    installment; when the rule bills by PAYCHECK rather than on a day of the
    month, the stored value is the payday of the paycheck that installment falls
    in, so a schedule rebuilt under it moves what the column would be re-derived
    as.  Both of the developer's live loan payments fire on a day of the month,
    where the installment IS the occurrence and the value is contract-derived.
    Plan ledger row **D6** tracks the day-less shape.

    **Resolved BEFORE the write, and the same call does both jobs.**  A
    recurrence that cannot be resolved must not reach the table, and
    ``resolve`` is where every such refusal already lives -- an owner
    mismatch, a non-positive interval, a due day outside its column's domain, a
    pay-period cadence against an empty schedule.  Re-checking those here would
    be a second copy of one judgement.  The refusal that is NOT ``resolve``'s is
    the encode below: a cadence the closed pattern set cannot name resolves
    perfectly well and simply has nowhere to be written.  Taking the phase, the
    normalised date and the encoded day from that one result rather than
    deriving each again is the other half: several calls could not disagree
    today, but they would be several producers of one value, which is the shape
    this step exists to remove.

    **The ENCODE step is here and nowhere else** (plan step R7b, widened to the
    anchor columns at R7c-b).  A caller authors a cadence and a first
    occurrence; the table still names its cadence with a closed pattern set and
    still carries the scheduling day as a column, so
    :func:`~app.services.recurrence._frequency.encode_cadence` turns the first
    into ``pattern_id`` plus the ``interval_n`` COLUMN and
    :attr:`~app.services.recurrence.ResolvedRecurrence.day_of_month` supplies
    the second.  Plan step R7c-c deletes all three lines together with the
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

            **``RecurrenceGenerationError`` left this list at plan step
            R7c-b**, and it left because the function that raised it did.  The
            pay-period normalisation is ``resolve``'s now, so an empty schedule
            is refused in the resolution hierarchy rather than in the
            generation one -- one door, one error class for a caller to catch
            around.
    """
    encoded = encode_cadence(spec.interval_n, spec.unit, spec.placement)
    resolved = resolve(spec, calendar)

    rule.user_id = spec.user_id
    rule.offset_periods = resolved.offset_periods
    rule.due_day_of_month = spec.due_day_of_month
    # ---- what the rule AUTHORS (plan step R7c-b) -------------------------
    #
    # The four columns that carry the recurrence itself, written from the ONE
    # ``resolve`` call above.  ``starts_on`` is ``resolved``'s rather than
    # ``spec``'s, and the difference is the pay-period NORMALISATION: a caller
    # may author any date for a paycheck-space cadence and ``resolve`` answers
    # the payday of the paycheck that hosts it, so what reaches the column is
    # always a real occurrence (ruling **R-R16**).  For every other unit the two
    # are the same value.
    rule.unit_id = ref_cache.recurrence_unit_id(spec.unit)
    rule.placement_id = ref_cache.period_placement_id(spec.placement)
    rule.shift_id = ref_cache.business_day_shift_id(resolved.shift)
    rule.starts_on = resolved.starts_on
    rule.nominal_day = resolved.nominal_day
    # ---- what the closed set ENCODES (dies at plan step R7c-c) ------------
    #
    # ``pattern_id`` / ``interval_n`` / ``day_of_month`` are the STORAGE
    # ENCODING of what the four columns above already say, and they are derived
    # here rather than authored -- which is the relationship ``pattern_id`` has
    # had to the cadence since plan step R7b, extended to the anchor columns
    # now that the anchor is authored.  A caller states no day and no month;
    # ``encode_cadence`` chooses the pattern that stores the cadence and the
    # first occurrence's own day is what the legacy column holds.
    #
    # **``day_of_month`` is still WRITTEN because it is still READ**:
    # ``recurrence_engine.compute_due_date`` dates every generated row from it
    # and plan step R5 is what deletes that function.  Gated on
    # ``fires_on_day_of_month`` rather than taken from
    # ``resolved.day_of_month`` alone, because the two differ for exactly one
    # cadence and the difference moves dates: a ``Monthly First`` rule is a
    # MONTH-unit cadence whose occurrences are the 1st, so the accessor answers
    # ``1`` while the column has always been NULL -- and NULL is what makes
    # ``compute_due_date`` date the row from its paycheck.  Writing the ``1``
    # would be plan ledger row **D26**'s fix arriving in the wrong step
    # (measured there at 11 rows).
    #
    # **For the calendar family this column went from possibly-NULL to
    # ALWAYS-SET at plan step R7c-b, and that MOVES a generated row's date on
    # the next re-author.**  Stated rather than left to be discovered: a caller
    # used to author ``day_of_month`` directly and could leave it NULL, which
    # made ``compute_due_date`` date the row from its PAYCHECK instead of from
    # the day the cadence fires on.  ``resolved.day_of_month`` is non-``None``
    # for every MONTH- and YEAR-unit rule (the first occurrence carries it), so
    # a re-author now dates such a row from the rule's own day.  That is a FIX
    # -- dating a monthly bill from its paycheck is plan ledger row **D18**'s
    # shape -- and it is the one behaviour change here a reader would not
    # predict from the columns.
    #
    # **``month_of_year`` and ``start_date`` are deliberately NOT assigned**,
    # exactly as ``start_period_id`` has not been since plan step R7b-4: this
    # step moved their last readers onto ``starts_on``, so no value written here
    # could be read back, and writing one would churn 24 live rows to say
    # something nothing asks.  Plan step R7c-c drops all three.
    rule.pattern_id = ref_cache.recurrence_pattern_id(encoded.pattern)
    rule.interval_n = encoded.interval_n
    rule.day_of_month = (
        resolved.day_of_month
        if fires_on_day_of_month(spec.unit, spec.placement)
        else None
    )
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
