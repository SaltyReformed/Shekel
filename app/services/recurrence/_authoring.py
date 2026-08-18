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
  step R7b, never a closed-set pattern id, and never a column);
* :func:`_author` writes that whole spec, and it is the only function in the
  application that assigns a column of ``budget.recurrence_rules``;
* every value derived on write is taken from the same
  :func:`~app.services.recurrence.resolve` call that validates the spec --
  never from the payload, which is what closes defect D1.

**Since plan step R7c-c there is ONE representation and this function writes
it.**  The rule states its recurrence in ``interval_n`` / ``unit_id`` /
``placement_id`` / ``shift_id`` / ``starts_on`` / ``nominal_day``, and that is
the whole table apart from ``due_day_of_month`` and the closing bound's
exclusive arc.  The closed set's storage encoding -- ``pattern_id``,
``day_of_month``, ``month_of_year``, ``start_date``, ``start_period_id``,
``offset_periods``, and ``interval_n``'s encoded value -- was derived here and
is dropped; two representations with one producer became one representation
with no producer to keep honest.

**``interval_n`` says what the cadence says from that step**, which it did not
before: ``encode_cadence`` wrote ``1`` for every pattern whose interval was
baked into its NAME, so a Quarterly rule stored ``(interval_n = 1, unit_id =
month)`` -- MONTHLY at face value, 12 occurrences a year where 4 were owed --
and the read door had to recover the ``3`` through the pattern.  R7c-c's
migration re-points the column on the four live rules that carried the
encoding.

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
from app.services.recurrence._resolution import RecurrenceSpec, resolve


def _author(
    rule: RecurrenceRule, spec: RecurrenceSpec, calendar: PayCalendar,
) -> None:
    """Write *spec* onto *rule*, every column of it.

    The ONE place a recurrence rule's columns are assigned.  It writes the
    authored spec -- the five columns that state the recurrence, plus
    ``due_day_of_month`` and the closing bound's exclusive arc -- taking every
    value from the same ``resolve`` call that validates it, which is also where
    the pay-period normalisation and the canonical cadence are decided.

    **There is nothing left to ENCODE since plan step R7c-c.**  The table stated
    its cadence with a closed set of pattern names until then, so this function
    wrote two representations of one fact: the authored columns, and
    ``pattern_id`` / ``interval_n`` / ``day_of_month`` derived from them.  All
    three of the second set are dropped, and what remains is a straight write of
    what the caller stated -- which is what makes "a stored authored value is a
    fact" true of every column on the row rather than of five of eight.

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
    as -- and the installment date is not recoverable from the row, which can
    put the rule's opening bound up to a pay period before the loan exists.
    Both of the developer's live loan payments fire on a day of the month,
    where the installment IS the occurrence and the value is contract-derived.
    Plan ledger row **D39** tracks the day-less shape, and owns it at plan step
    R5.  (It was **D6** until plan step R7c-c, which CLOSED that row: D6 said
    folding the opening bound into the anchor was lossy, and ruling **R-R16**
    answers that -- there is no fold, the first occurrence IS the bound -- so
    the residual was re-measured under an id of its own rather than carried
    under a wording that no longer described it.)

    **Resolved BEFORE the write, and ONE call does every job.**  A recurrence
    that cannot be resolved must not reach the table, and ``resolve`` is where
    every such refusal already lives -- an owner mismatch, a non-positive
    interval, a due day outside its column's domain, a first occurrence outside
    the calendar this application reaches, a pay-period cadence against an empty
    schedule.  Re-checking those here would be a second copy of one judgement.
    Taking the normalised date, the canonical cadence and the nominal day from
    that one result rather than deriving each again is the other half: several
    calls could not disagree today, but they would be several producers of one
    value, which is the shape this step exists to remove.

    **A refusal ran here BEFORE ``resolve`` until plan step R7c-c, and where it
    went is worth stating.**  ``encode_cadence`` was a pure table lookup asking
    "has this cadence anywhere to be written at all", and running it first was
    measured by an adversarial review of plan step R7b-2: resolving first meant
    doing arbitrary month arithmetic on a cadence about to be refused anyway,
    and ``(10000, YEAR)`` reached ``_months.clamped_day``, which builds a
    ``date`` from a month ordinal, raising ``ValueError: year must be in
    1..9999`` -- outside this package's error hierarchy, so the recurrence
    preview's handler did not catch it and a signed-in GET was an unhandled 500.
    R7c-c makes every cadence storable, so that lookup has nothing left to
    refuse -- and the hazard it incidentally covered is now closed at its own
    root instead, in ``_months.walk_months``, which stops at the last month the
    application's calendar reaches rather than walking off the end of ``date``.
    A cadence whose second occurrence lies past that point fires once.

    Args:
        rule: The rule to write, new or existing.
        spec: Its complete authored state.
        calendar: The owner's pay-period schedule.

    Raises:
        RecurrenceResolutionError: When *spec* cannot be resolved against
            *calendar* -- see :func:`~app.services.recurrence.resolve`.

            **``RecurrenceGenerationError`` left this list at plan step
            R7c-b**, and it left because the function that raised it did.  The
            pay-period normalisation is ``resolve``'s now, so an empty schedule
            is refused in the resolution hierarchy rather than in the
            generation one -- one door, one error class for a caller to catch
            around.
    """
    resolved = resolve(spec, calendar)

    rule.user_id = spec.user_id
    rule.due_day_of_month = spec.due_day_of_month
    # ---- what the rule AUTHORS -------------------------------------------
    #
    # **The WHOLE table since plan step R7c-c**: six columns, every one of them
    # a value a caller states, and no encoding beside them.  They are written
    # from the ONE ``resolve`` call above, whose cadence has already been
    # through
    # :func:`~app.services.recurrence._frequency.canonical_cadence` -- so
    # ``interval_n`` and ``unit_id`` are the
    # canonical spelling of the cadence rather than whatever the caller happened
    # to type, and every other column is derived from that same pair.
    #
    # ``starts_on`` is ``resolved``'s rather than ``spec``'s, and the difference
    # is the pay-period NORMALISATION: a caller may author any date for a
    # paycheck-space cadence and ``resolve`` answers the payday of the paycheck
    # that hosts it, so what reaches the column is always a real occurrence
    # (ruling **R-R16**).  For every other unit the two are the same value.
    rule.interval_n = resolved.interval_n
    rule.unit_id = ref_cache.recurrence_unit_id(resolved.unit)
    rule.placement_id = ref_cache.period_placement_id(resolved.placement)
    rule.shift_id = ref_cache.business_day_shift_id(resolved.shift)
    rule.starts_on = resolved.starts_on
    rule.nominal_day = resolved.nominal_day
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
