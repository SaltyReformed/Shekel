"""
Shekel Budget App -- The recurrence vocabulary the application MODELS (R2e-2)

``ref.recurrence_units`` and ``ref.period_placements`` are tables of rows.
:class:`~app.enums.RecurrenceUnitEnum` and
:class:`~app.enums.PeriodPlacementEnum` are the sets this application can
actually derive a meaning for.  They are different sets, and before this module
every recurrence surface read the TABLE -- the form routes built their
``<select>`` from a live query, so a row the enum does not name would be
OFFERED; both write doors validated a submitted id with ``db.session.get``, so
such a row would be ACCEPTED; and the occurrence preview did the same.

What the application can READ is the narrower set:
:func:`app.services.recurrence.resolve` raises
:class:`~app.services.recurrence.RecurrenceResolutionError` for an id no enum
member names, and no route catches it -- so the gap between the two sets is a
500 waiting for them to diverge.

**The question this module asks moved from the PATTERN to the two axes at plan
step R7c-c**, with the column it was asked about.  Until then it was
``modelled_pattern(pattern_id)`` over the closed eight-member
:class:`~app.enums.RecurrencePatternEnum`, and the sets HAD diverged: plan step
R2e-3 deleted the ``Once`` member while its ``ref`` row survived to R9, because
deleting the row in the same release would leave the auto-rollback image unable
to boot (``ref_cache.init`` raises for an enum member with no row; ruling
R-R11).  ``pattern_id`` is dropped, so the divergence is no longer reachable
through any rule at all, and what is left to ask about is a rule's ``unit_id``
and ``placement_id``.  ``ref.recurrence_patterns`` and its enum outlive this
module's interest in them by exactly one step: plan step **R9** drops both, for
the same rollback-image reason R-R11 gave.

**The PICKER left at plan step R7b-2** and took the larger half of this module
with it.  The form does not choose a cadence NAME; it authors
``(interval_n, unit, placement)``, so what the options are and what they are
called is :mod:`app.services.recurrence._picker`'s -- derived there from
``_frequency.authorable_cadences``, which is what makes an unresolvable cadence
unofferable rather than merely unoffered.  ``_PATTERN_LABELS``,
``PatternChoice``, ``pattern_choices``, ``pattern_choices_for`` and
``UNAVAILABLE_PATTERN_LABEL`` went with it: with no pattern ``<select>`` there
is no option list to append a stored-but-unmodelled value to, and the
``<select>``-silently-picks-a-different-value hazard those two functions existed
to close cannot arise on controls that carry no such id at all.

What remains is the MEMBERSHIP question itself -- which ``ref`` ids this
application models -- which the read door still asks of every stored rule, and
the sentence an edit form shows when the answer is "none".

Pure: no Flask, no ORM, no clock.  The ``ref`` ids come from
:mod:`app.ref_cache`, the project's IDs-for-logic seam.

**The one property this gives up**, stated rather than discovered: the doors
used to read the table LIVE, so they also proved the FK target existed at write
time; ``ref_cache`` is loaded once per process.  A row deleted from a ``ref``
table while a process runs would now be accepted here and raise
``IntegrityError`` at flush (both foreign keys are ``ondelete="RESTRICT"``)
instead of flashing.  Unreachable through any application path -- ``ref`` rows
change only in migrations, which restart the process -- and it is exactly what
plan step R2e-3's expand/contract exists to keep true.
"""
from app import ref_cache
from app.enums import (
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)


def modelled_unit(unit_id: int) -> RecurrenceUnitEnum | None:
    """Return the cadence unit *unit_id* names, or ``None`` when unmodelled.

    The one question every recurrence surface asks of a STORED unit id, and of
    a submitted one.  It answers ``None`` rather than raising because the
    surface that asks is the edit form, reading a row it is about to let the
    user repair: a rule naming a unit the application does not model is a state
    to explain and offer a fix for, not a broken invariant to 500 on.  The
    raising form belongs one layer down, where a rule that is already PERSISTED
    cannot be read (``_frequency.unit_member``), and that function is built on
    this one so the two cannot disagree about the set.

    Resolved by comparing INTEGER ids through :mod:`app.ref_cache`, never by
    reading a ``name`` column -- the project-wide IDs-for-logic invariant.
    Scanning the four cached members rather than adding an inverse map to
    ``ref_cache`` is deliberate and unchanged from the R2c-1 reading: a
    recurrence id is resolved on a template edit, not per row of a grid, so the
    inverse the account-category classifier needed (ruling R-CV, where the scan
    measured 2.3x-4.5x the single comparison) would buy nothing here.

    Args:
        unit_id: A stored or submitted ``ref.recurrence_units`` id.

    Returns:
        The matching :class:`~app.enums.RecurrenceUnitEnum` member, or ``None``
        -- either because the id is not a row at all, or because it is a row
        this application does not model.

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
    this asks rather than raises.

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


#: What an edit form TELLS the user about a rule whose cadence it cannot read.
#:
#: Since plan step R7b-2 the form carries no pattern option to word, so this is
#: the whole of that explanation rather than half of it: the message names the
#: state and the repair, and the cadence controls it appears above are rendered
#: UNSET so the repair is the only way forward.
#:
#: **It was ``UNAVAILABLE_PATTERN_MESSAGE`` until plan step R7c-c**, and both
#: the name and the copy moved with the state they describe.  The unreadable
#: cadence used to be a ``pattern_id`` naming a ``ref.recurrence_patterns`` row
#: the closed enum did not model; that column is dropped, and the state it
#: leaves behind is a ``unit_id`` or ``placement_id`` naming a ``ref`` row the
#: two-axis enums do not model -- a seed the enums have diverged from, a hand
#: edit, or a partial restore.  Rarer, because both columns are ``NOT NULL``
#: with ``RESTRICT`` foreign keys, and the same class of state with the same
#: repair, so the path stays and only the words that named a pattern change.
#:
#: **The last sentence is a promise the ROUTE keeps**, not the schema: an unset
#: unit ``<select>`` has its empty "Does not repeat" entry selected, so saving
#: unchanged posts a legitimate CLEAR that both form schemas accept -- they must,
#: because that is how a cadence is ended.  ``app.routes
#: ._recurrence_form_refusals.UNREPAIRED_CADENCE_CANNOT_BE_CLEARED`` is the
#: refusal, made from the stored rule plus the submission, which is what keeps
#: this warning honest rather than advisory.
UNREADABLE_CADENCE_MESSAGE: str = (
    "This recurring definition repeats on a schedule this app can no longer "
    "read. Choose how often it repeats before saving -- saving it unchanged "
    "will be refused."
)


__all__ = [
    "UNREADABLE_CADENCE_MESSAGE",
    "modelled_placement",
    "modelled_unit",
]
