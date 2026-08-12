"""
Shekel Budget App -- The recurrence vocabulary the application MODELS (R2e-2)

``ref.recurrence_patterns`` is a table of rows.
:class:`~app.enums.RecurrencePatternEnum` is the set of cadences this
application can actually derive a meaning for.  They are two different sets,
and before this module every recurrence surface read the TABLE:

* the four form routes built the pattern ``<select>`` from
  ``db.session.query(RecurrencePattern).all()``, so a row the enum does not
  name would be OFFERED to the user;
* both write doors validated a submitted id with
  ``db.session.get(RecurrencePattern, id)``, so such a row would be ACCEPTED;
* the occurrence preview did the same.

What the application can READ is the narrower set:
:func:`app.services.recurrence.resolve` raises
:class:`~app.services.recurrence.RecurrenceResolutionError` for a pattern id no
enum member names, and no route catches it -- so the gap between the two sets
is a 500 waiting for them to diverge.  **They now HAVE diverged** (plan step
R2e-3): the ``Once`` enum member is deleted while its ``ref`` row SURVIVES to
R9, because deleting the row in the same release would leave the auto-rollback
image unable to boot (``ref_cache.init`` raises for an enum member with no row;
ruling R-R11).  With every surface asking this module instead, that surviving
row is UNREACHABLE rather than merely unoffered: it is not offered, not
accepted, and not previewed.  "Does not recur" is
``recurrence_rule_id IS NULL`` on either template kind, so no pattern has to
mean it.

**The PICKER's label sits beside the membership answer because the two
CONVERGE** -- not because separating them would let them drift.  Both are keyed
by :class:`~app.enums.RecurrencePatternEnum`, which IS the single list, and it
is that keying rather than this file that makes a member without a label a
``KeyError`` instead of a blank option.

**The DISPLAY label left at plan step R7a**, and the two were never one table:
:func:`app.services.recurrence.describe` words a RESOLVED recurrence over
``(interval_n, unit)``, which is what survives plan step R7c, while
:data:`_PATTERN_LABELS` words the closed-set option a form still POSTS, which
dies with that form at plan step R7b.  R7a deleted this module's
``pattern_labels_by_name`` -- a name-keyed projection whose only consumer was
the ``recurrence_cell`` macro's fallback branch -- together with the branch.
Until the picker authors the two-axis vocabulary, an enum-keyed table of what
the OPTIONS are called is the honest shape.

Pure: no Flask, no ORM, no clock.  The ``ref`` ids come from
:mod:`app.ref_cache`, the project's IDs-for-logic seam.

**The one property this gives up**, stated rather than discovered: the doors
used to read the table LIVE, so they also proved the FK target existed at write
time; ``ref_cache`` is loaded once per process.  A row deleted from
``ref.recurrence_patterns`` while a process runs would now be accepted here and
raise ``IntegrityError`` at flush (the FK is ``ondelete="RESTRICT"``) instead of
flashing.  Unreachable through any application path -- ``ref`` rows change only
in migrations, which restart the process -- and it is exactly what plan step
R2e-3's expand/contract exists to keep true.
"""
from dataclasses import dataclass

from app import ref_cache
from app.enums import RecurrencePatternEnum

#: What each modelled pattern is CALLED on the form's ``<select>``.
#:
#: The PICKER's copy, not the display label: a saved definition's recurrence is
#: worded by :func:`app.services.recurrence.describe` from what it MEANS, which
#: is why "Monthly (specific day)" is an option here and no cell anywhere says
#: it.  Both die at plan step R7b, when the form starts authoring
#: ``(interval, unit)`` directly.
#:
#: Keyed by enum member rather than by the ``ref`` row's ``name`` so the set of
#: patterns and the set of labels cannot drift apart: :func:`pattern_choices`
#: indexes this map directly for every member of the enum, so a member added
#: without a label raises ``KeyError`` at the first render rather than shipping
#: a blank option.  The copy is verbatim what the picker has shown since the
#: recurring cluster shipped.
_PATTERN_LABELS: dict[RecurrencePatternEnum, str] = {
    RecurrencePatternEnum.EVERY_PERIOD: "Every paycheck",
    RecurrencePatternEnum.EVERY_N_PERIODS: "Every N paychecks",
    RecurrencePatternEnum.MONTHLY: "Monthly (specific day)",
    RecurrencePatternEnum.MONTHLY_FIRST: "Monthly (first paycheck of month)",
    RecurrencePatternEnum.QUARTERLY: "Quarterly",
    RecurrencePatternEnum.SEMI_ANNUAL: "Every 6 months",
    RecurrencePatternEnum.ANNUAL: "Yearly",
}


@dataclass(frozen=True)
class PatternChoice:
    """One option of the recurrence-pattern picker.

    A plain value so the form templates DISPLAY and never compute: the id goes
    in the ``<option value>`` and the label between the tags, with no lookup,
    no fallback expression and no ``name`` string in the template at all.

    Attributes:
        pattern_id: The ``ref.recurrence_patterns`` id to submit.  An id
            because that is what the form posts and what
            :class:`~app.services.recurrence.RecurrenceSpec` stores; the enum
            member it names is this module's business, not the template's.
        label: The human copy for the option.
    """

    pattern_id: int
    label: str


def modelled_pattern(pattern_id: int) -> RecurrencePatternEnum | None:
    """Return the pattern *pattern_id* names, or ``None`` when unmodelled.

    The one question every recurrence surface asks of a submitted id.  It
    answers ``None`` rather than raising because the surfaces that ask are
    doors reading user input: an id the application does not model is bad
    input to refuse with a flash, not a broken invariant to 500 on.  The
    raising form belongs one layer down, where a rule that is already
    PERSISTED cannot be read (``_frequency.pattern_member``), and that
    function is built on this one so the two cannot disagree about the set.

    Resolved by comparing INTEGER ids through :mod:`app.ref_cache`, never by
    reading a ``name`` column -- the project-wide IDs-for-logic invariant.
    Scanning the eight cached members rather than adding an inverse map to
    ``ref_cache`` is deliberate and unchanged from the R2c-1 reading: a
    recurrence id is resolved on a template edit, not per row of a grid, so the
    inverse the account-category classifier needed (ruling R-CV, where the scan
    measured 2.3x-4.5x the single comparison) would buy nothing here.

    Args:
        pattern_id: A submitted or stored ``ref.recurrence_patterns`` id.

    Returns:
        The matching :class:`~app.enums.RecurrencePatternEnum` member, or
        ``None`` when no member names *pattern_id* -- either because the id is
        not a row at all, or because it is a row this application no longer
        models.

    Raises:
        RuntimeError: If ``ref_cache`` has not been initialized.
    """
    for member in RecurrencePatternEnum:
        if ref_cache.recurrence_pattern_id(member) == pattern_id:
            return member
    return None


def pattern_choices() -> tuple[PatternChoice, ...]:
    """Return the picker's options, in enum declaration order.

    The single producer of what the recurrence ``<select>`` offers, for both
    template kinds and both the create and edit forms.  Driven by the ENUM, so
    the picker offers exactly what :func:`app.services.recurrence.resolve` can
    read back -- a table-driven picker could author a rule nothing could
    resolve, which is this step's root cause.

    The order is the enum's own declaration order, which is also the order
    ``app.ref_seeds`` inserts the rows in.  **On PRODUCTION that left the
    rendered list unchanged** (verified against the 2026-08-05 dump: ids 1-8 in
    enum order; the 8th, ``Once``, is the row plan step R2e-3 stopped naming).
    It is not an invariant of the schema, and saying so matters twice over:

    * a database built through the MIGRATION chain is in a different order --
      ``a3b1c2d4e5f6`` appends ``quarterly`` and ``semi_annual`` after the
      initial seed, which is why the test database's picker did NOT render in
      id order before this change;
    * a seq scan returns HEAP order, and any ``UPDATE`` moves a row to the
      tail.  Measured on the live dev database inside a rolled-back
      transaction: two updates of one row's ``name`` moved ``Monthly`` from
      ctid ``(0,3)`` to ``(0,10)``, and the same unordered ``SELECT`` then
      returned it LAST -- the picker would have offered "Monthly (specific
      day)" at the BOTTOM of the list, with no test to catch it.

    So the order this returns is not merely tidier than the query's; it is the
    first order the picker has ever actually been able to rely on.

    Returns:
        One :class:`PatternChoice` per modelled pattern, most frequent cadence
        first (every paycheck through yearly) -- the order the picker has
        always rendered.  Every entry RECURS: "does not repeat" is the form's
        own empty option, not a pattern (plan step R2e-3).

    Raises:
        RuntimeError: If ``ref_cache`` has not been initialized.
        KeyError: If a :class:`~app.enums.RecurrencePatternEnum` member has no
            entry in :data:`_PATTERN_LABELS`, or no cached ``ref`` id.
    """
    return tuple(
        PatternChoice(
            pattern_id=ref_cache.recurrence_pattern_id(member),
            label=_PATTERN_LABELS[member],
        )
        for member in RecurrencePatternEnum
    )


#: What an EDIT form calls a stored pattern the application no longer models.
#:
#: Deliberately not the ``ref`` row's own ``name``: that name is an internal
#: seed string, and reading it would put a second display path back on the
#: table this step took the picker off.
UNAVAILABLE_PATTERN_LABEL: str = "Unavailable -- pick a new pattern"

#: What the form TELLS the user about that option.
#:
#: Beside the label rather than in the route that flashes it, because the two
#: are halves of one explanation of one condition: the option says WHAT is
#: wrong and this says what a save will do about it.  Split across layers, a
#: future edit changes one and leaves the other contradicting it.
UNAVAILABLE_PATTERN_MESSAGE: str = (
    "This recurring definition uses a repeat pattern that is no longer "
    "available. Pick a new pattern before saving -- saving it unchanged will "
    "be refused."
)


def pattern_choices_for(pattern_id: int | None) -> tuple[PatternChoice, ...]:
    """Return the picker's options for a form editing a rule on *pattern_id*.

    :func:`pattern_choices` plus, when the rule names a pattern this
    application no longer models, ONE trailing entry carrying that id.

    **A ``<select>`` whose selected value is absent from its options does not
    fail -- it silently becomes a different value**, and that is the whole
    reason this function exists.  Measured on the transaction edit form with a
    rule naming a surplus ``ref`` row: no option carried ``selected``, so the
    browser falls back to the first in document order, which on both forms is
    the empty "Does not repeat" entry -- and submitting THAT is the clear path
    plan step R2e-1 made destructive, deleting the rule and sweeping its future
    rows.  Before R2e-2 the picker was the table, so the row was rendered and
    pre-selected correctly and a save raised loudly; without this function the
    step would trade a 500 for a silent wrong write on the one screen where it
    is least likely to be noticed.

    Keeping the stored id as a selectable option is what makes the failure loud
    again: the form reads honestly, and saving it unchanged reaches the write
    door, which refuses an unmodelled pattern with a flash and writes nothing.
    Nothing is OFFERED that was not already stored -- the entry is a read-out of
    this rule's own value, never a cadence the picker proposes.

    **Live since plan step R2e-3**, which deleted the ``Once`` enum member while
    its ``ref`` row survives (ruling R-R11).  That step's migration re-points
    every live ``Once`` rule, so this guards the rows a migration misses, the
    auto-rollback image, and hand-edited data -- a silent cadence deletion is
    not a failure mode to leave riding on one migration's completeness.

    Args:
        pattern_id: The stored ``recurrence_rules.pattern_id``, or ``None``
            when the template names no rule (or the form is a create form).

    Returns:
        The modelled choices, with the unmodelled stored pattern appended last
        when there is one.

    Raises:
        RuntimeError: If ``ref_cache`` has not been initialized.
    """
    choices = pattern_choices()
    if pattern_id is None or modelled_pattern(pattern_id) is not None:
        return choices
    return choices + (
        PatternChoice(pattern_id=pattern_id, label=UNAVAILABLE_PATTERN_LABEL),
    )


__all__ = [
    "UNAVAILABLE_PATTERN_LABEL",
    "UNAVAILABLE_PATTERN_MESSAGE",
    "PatternChoice",
    "modelled_pattern",
    "pattern_choices",
    "pattern_choices_for",
]
