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

**The PICKER left at plan step R7b-2** and took the larger half of this module
with it.  The form no longer chooses a pattern NAME; it authors
``(interval_n, unit, placement)`` and the write door encodes that, so what the
options are and what they are called is :mod:`app.services.recurrence._picker`'s
-- derived there from the encoder's own table rather than from this enum, which
is what makes an unstorable cadence unofferable rather than merely unoffered.
``_PATTERN_LABELS``, ``PatternChoice``, ``pattern_choices``,
``pattern_choices_for`` and ``UNAVAILABLE_PATTERN_LABEL`` went with it: with no
pattern ``<select>`` there is no option list to append a stored-but-unmodelled
pattern to, and the ``<select>``-silently-picks-a-different-value hazard those
two functions existed to close cannot arise on controls that carry no pattern
id at all.

What remains is the MEMBERSHIP question itself -- which ``ref`` ids this
application models -- which the read door still asks of every stored rule, and
the sentence an edit form shows when the answer is "none".

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
from app import ref_cache
from app.enums import (
    PeriodPlacementEnum,
    RecurrencePatternEnum,
    RecurrenceUnitEnum,
)


def modelled_pattern(pattern_id: int) -> RecurrencePatternEnum | None:
    """Return the pattern *pattern_id* names, or ``None`` when unmodelled.

    The one question every recurrence surface asks of a STORED id.  It
    answers ``None`` rather than raising because the surface that asks is the
    edit form, reading a row it is about to let the user repair: a rule naming
    a pattern the application no longer models is a state to explain and offer
    a fix for, not a broken invariant to 500 on.  The raising form belongs one
    layer down, where a rule that is already PERSISTED cannot be read
    (``_frequency.pattern_member``), and that function is built on this one so
    the two cannot disagree about the set.

    Resolved by comparing INTEGER ids through :mod:`app.ref_cache`, never by
    reading a ``name`` column -- the project-wide IDs-for-logic invariant.
    Scanning the seven cached members rather than adding an inverse map to
    ``ref_cache`` is deliberate and unchanged from the R2c-1 reading: a
    recurrence id is resolved on a template edit, not per row of a grid, so the
    inverse the account-category classifier needed (ruling R-CV, where the scan
    measured 2.3x-4.5x the single comparison) would buy nothing here.

    Args:
        pattern_id: A stored ``ref.recurrence_patterns`` id.

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


def modelled_unit(unit_id: int) -> RecurrenceUnitEnum | None:
    """Return the cadence unit *unit_id* names, or ``None`` when unmodelled.

    :func:`modelled_pattern`'s twin on the first AUTHORED axis, and it is the
    submission door's question rather than the stored row's: since plan step
    R7b-2 a form posts ``ref.recurrence_units`` ids, and an id naming no member
    is bad input to refuse with a field error.

    Args:
        unit_id: A submitted ``ref.recurrence_units`` id.

    Returns:
        The matching :class:`~app.enums.RecurrenceUnitEnum` member, or ``None``.

    Raises:
        RuntimeError: If ``ref_cache`` has not been initialized.
    """
    for member in RecurrenceUnitEnum:
        if ref_cache.recurrence_unit_id(member) == unit_id:
            return member
    return None


def modelled_placement(placement_id: int) -> PeriodPlacementEnum | None:
    """Return the placement *placement_id* names, or ``None`` when unmodelled.

    :func:`modelled_unit`'s twin on the second authored axis; see it for why
    the door asks rather than raises.

    Args:
        placement_id: A submitted ``ref.period_placements`` id.

    Returns:
        The matching :class:`~app.enums.PeriodPlacementEnum` member, or
        ``None``.

    Raises:
        RuntimeError: If ``ref_cache`` has not been initialized.
    """
    for member in PeriodPlacementEnum:
        if ref_cache.period_placement_id(member) == placement_id:
            return member
    return None


#: What an edit form TELLS the user about a rule whose pattern is gone.
#:
#: Since plan step R7b-2 the form carries no pattern option to word, so this is
#: the whole of that explanation rather than half of it: the message names the
#: state and the repair, and the cadence controls it appears above are rendered
#: UNSET so the repair is the only way forward.  Saving without choosing is
#: refused by the same validator that refuses any missing cadence, which is what
#: keeps the warning honest rather than advisory.
UNAVAILABLE_PATTERN_MESSAGE: str = (
    "This recurring definition uses a repeat pattern that is no longer "
    "available. Choose how often it repeats before saving -- saving it "
    "unchanged will be refused."
)


__all__ = [
    "UNAVAILABLE_PATTERN_MESSAGE",
    "modelled_pattern",
    "modelled_placement",
    "modelled_unit",
]
