"""
Shekel Budget App -- Describing a recurrence in words (plan step R7a)

What a recurring definition's "Recurrence" cell says, produced ONCE here from
the two-axis meaning rather than eight times in a template.

Why this is not a template's job
--------------------------------

Until plan step R7a the cell was SEVEN hand-written Jinja branches keyed on
``recurrence_rules.pattern_id`` -- one per member of the closed pattern set --
each reading ``day_of_month`` / ``month_of_year`` directly, plus a fallback.
Three problems, all of them structural:

* it read the CLOSED-SET columns, every one of which plan step R7c drops, so
  the surface could not survive the cutover;
* the seven branches were written independently and had drifted into three
  different shapes -- a yearly rule showed month AND day, a quarterly rule
  showed month only, a monthly rule showed day only -- with no reason behind
  the difference beyond who wrote which branch;
* **the vocabulary itself was the ceiling.**  Cadences plan step R8 makes
  authorable -- ``(2, MONTH)``, ``(1, WEEK)`` -- have no member of the closed
  set to name them, so no branch could have been written for them; a rule the
  seven did not match fell to a fallback that titled the ``ref`` row's own
  ``name``.  That fallback was the last ``.name``-for-display coupling on THIS
  table, and the only reader of the eager-joined ``RecurrenceRule.pattern``
  relationship (plan ledger row **D17**).

:func:`describe` is one function over ``(interval_n, unit)`` plus the anchor,
so a cadence nothing has authored yet already reads correctly.  The developer
ruled the uniform shape on 2026-08-08: **every calendar cadence names its cycle
the same way, month and day**, which is what a yearly rule already did and what
quarterly and semi-annual rules did not.

Which month the phrase names, and why it MOVED
----------------------------------------------

A quarterly rule fires in months ``{m, m+3, m+6, m+9}``: naming any one of them
names the whole cycle.  The old cell named the AUTHORED ``month_of_year``; this
one names the anchor's month -- the rule's FIRST occurrence.  Measured on
production 2026-08-08, exactly 2 of the 46 LIVE rules move, both because the
authored month falls before the owner's schedule opens (2026-03-26):

```text
Anchor Disposal  quarterly, authored March, day 2   Mar -> Jun 2
Clothes          6-monthly, authored March, day 15  Mar -> Sep 15
```

Both still name the same cycle, and the moved value is the one the model can
still express after plan step R7c drops ``month_of_year``.

**Five further shapes change wording, and none is live today** -- stating the
measurement rather than the change is a claim-scope error this arc has made
before.  Each is authorable through the form (``day_of_month`` and
``month_of_year`` are optional in ``TemplateCreateSchema`` and the month
``<select>`` carries an empty option), and each new phrase is what the resolver
ALREADY generates rows for -- the old cell simply did not say so, falling
through to the picker's own label:

```text
Monthly, no day        Monthly (specific day)  ->  Monthly (day 1)
Quarterly, no month    Quarterly               ->  Quarterly (Apr 1)
Semi-annual, no month  Every 6 months          ->  Every 6 months (Jul 1)
Annual, no month       Yearly                  ->  Yearly (Jan 1)
Every N periods, N=1   Every 1 paychecks       ->  Every paycheck
```

Pure: no Flask, no ORM, no clock, no database.  It takes a
:class:`~app.services.recurrence.ResolvedRecurrence` and returns a value; the
template picks the phrase up and styles it.
"""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.exceptions import ShekelError
from app.services.recurrence._bounds import (
    EndBound,
    EndsAfterOccurrences,
    EndsOnDate,
    NeverEnds,
)
from app.services.recurrence._resolution import ResolvedRecurrence
from app.utils.dates import month_name, weekday_name

#: The cadence stems that have a NAME of their own, per unit.
#:
#: Every other interval falls back to the counted form below, which is what
#: makes a cadence nobody has authored yet ("every 2 months", "every 2 years")
#: read correctly the day plan step R8 allows it.  The named entries are
#: verbatim the copy the old per-pattern branches carried, so no definition's
#: cell changes wording for a reason unrelated to this step.
_NAMED_STEMS: dict[tuple[RecurrenceUnitEnum, int], str] = {
    (RecurrenceUnitEnum.PERIOD, 1): "Every paycheck",
    (RecurrenceUnitEnum.WEEK, 1): "Weekly",
    (RecurrenceUnitEnum.MONTH, 1): "Monthly",
    (RecurrenceUnitEnum.MONTH, 3): "Quarterly",
    (RecurrenceUnitEnum.MONTH, 6): "Every 6 months",
    (RecurrenceUnitEnum.YEAR, 1): "Yearly",
}

#: The plural noun each unit is counted in, for the fallback stem.
_UNIT_PLURALS: dict[RecurrenceUnitEnum, str] = {
    RecurrenceUnitEnum.PERIOD: "paychecks",
    RecurrenceUnitEnum.WEEK: "weeks",
    RecurrenceUnitEnum.MONTH: "months",
    RecurrenceUnitEnum.YEAR: "years",
}

#: What a DEFERRING placement is called in the phrase.
#:
#: ``CONTAINING_DATE`` is absent because it adds nothing: the occurrence falls
#: inside the paycheck that funds it, so the calendar coordinate already says
#: when the money moves.  Under
#: ``PERIOD_STARTING_ON_OR_AFTER`` it does not -- the row lands on a LATER
#: paycheck -- so the phrase has to say so or it names a date no row carries.
_DEFERRED_NOTE = "first paycheck"

#: The day-of-month a deferring monthly rule fires on when the phrase's own
#: words already imply it: "the first paycheck on or after the 1st of the
#: month" IS "the month's first paycheck", so naming the 1st beside it would
#: state the mechanism twice.  Any OTHER day is stated, because then the two
#: are different facts.
_IMPLIED_DEFERRED_DAY = 1


class RecurrenceDescriptionError(ShekelError):
    """A resolved recurrence names a unit or placement with no wording.

    A broken invariant, not user input: every member of
    :class:`~app.enums.RecurrenceUnitEnum` and
    :class:`~app.enums.PeriodPlacementEnum` must have a phrase, and a member
    added to either enum without one would otherwise render a cell that
    silently omits how a definition repeats.

    Raised rather than returning a placeholder for the reason the whole
    redesign exists: a partial function over an enum is the defect being
    removed, and a plausible-looking wrong label on a financial surface is
    worse than an error.  A :class:`~app.exceptions.ShekelError` like every
    other refusal this package makes -- the two it sits beside
    (``RecurrenceResolutionError``, ``RecurrenceGenerationError``) name broken
    invariants too, and a domain error outside that hierarchy would escape any
    handler written against it.
    """


@dataclass(frozen=True)
class RecurrenceDescription:
    """How one recurring definition repeats, in display terms.

    Attributes:
        cadence: The finished phrase -- ``"Every paycheck"``,
            ``"Monthly (day 22)"``, ``"Quarterly (Apr 21)"``.  Plain text: the
            template styles it and never assembles it, so the shape of the
            phrase is decided in one place for every cadence including the
            ones nothing authors yet.
        stops: When the recurrence stops, as ONE finished phrase --
            ``"until Mar 01, 2027"``, ``"for 12 occurrences"`` -- or ``None``
            when it runs indefinitely and the cell shows no second line.

            **It was two fields, ``until`` and ``after_occurrences``, with a
            ``__post_init__`` refusing the pair, until plan step R7b-3.**  That
            guard existed because ``ck_recurrence_rules_single_end_bound``
            refuses the pair in the TABLE and "a value built in memory is not
            the table" -- without it a cell could render "until Mar 01, 2027"
            AND "for 12 occurrences", two stop dates for one commitment.

            Carrying the two fields was itself the defect one layer up, and an
            adversarial review of this step named it: the template then
            branched on ``description.until`` and
            ``description.after_occurrences``, which is three shapes and TWO
            branches, so a fourth shape
            (plan step R8 adds one) would render nothing at all and the cell
            would read as indefinite -- a commitment the app goes on charging.
            :func:`_stops_phrase` is total over the shapes and RAISES for one
            it has no wording for, exactly as :func:`_stem` and
            :func:`_placement_note` do for their own closed sets.

            A phrase rather than a date, so the wording of a stop is decided
            here beside the wording of a cadence.  It also takes the month name
            off ``strftime('%b')``, which the cell used and which delegates to
            the platform and follows ``LC_TIME`` -- the dependence
            :data:`app.utils.dates._MONTH_NAMES_ABBR` is spelled out to escape.
            **That is a HAZARD rather than a measured failure**, and an
            adversarial review of this step corrected an earlier note here for
            claiming otherwise: CPython never calls ``setlocale``, nothing in
            ``app/`` does either, and ``%b`` measured English under
            ``LANG=de_DE.UTF-8`` in this repo's own venv.  What makes the move
            load-bearing is the totality above; the locale is why it was worth
            doing in the same pass rather than a reason of its own.
    """

    cadence: str
    stops: str | None


def _stem(unit: RecurrenceUnitEnum, interval_n: int) -> str:
    """Return the cadence's leading phrase -- ``"Quarterly"``, ``"Yearly"``.

    Args:
        unit: The cadence unit.
        interval_n: How many *unit*\\ s pass between occurrences.

    Returns:
        The named stem when the cadence has one, else the counted form
        (``"Every 2 months"``).

    Raises:
        RecurrenceDescriptionError: When *unit* has no plural noun, which is a
            member added to :class:`~app.enums.RecurrenceUnitEnum` without a
            phrase.
    """
    named = _NAMED_STEMS.get((unit, interval_n))
    if named is not None:
        return named
    plural = _UNIT_PLURALS.get(unit)
    if plural is None:
        raise RecurrenceDescriptionError(
            f"recurrence unit {unit!r} has no wording.  Every member of "
            f"RecurrenceUnitEnum must have one: a cell that omits how a "
            f"definition repeats reads as a definition that does not."
        )
    return f"Every {interval_n} {plural}"


def _coordinate(resolved: ResolvedRecurrence) -> str:
    """Return the calendar day/month the cadence fires on.

    What goes inside the parentheses before any placement note.  Two shapes,
    and which one applies is a property of the UNIT rather than of the pattern
    that used to be authored:

    * ``WEEK`` -- the anchor's weekday (``"Mondays"``), which is the whole of
      what such a rule's phase is.
    * ``MONTH`` / ``YEAR`` -- ``"day 22"`` for a rule that fires EVERY month,
      where only the day distinguishes it, and the anchor's month beside that
      day (``"Apr 21"``) for one that skips months, where which months it
      fires in is half the answer.

    Called only for a unit with a calendar coordinate; the ``PERIOD`` unit is
    answered by :func:`_parenthetical` before this runs, which is where the
    reason lives.

    Args:
        resolved: The recurrence's two-axis meaning.

    Returns:
        The coordinate phrase.

    Raises:
        RecurrenceDescriptionError: When *resolved* names a unit with neither
            a weekday nor a day-of-month reading, which is a member added to
            :class:`~app.enums.RecurrenceUnitEnum` without a coordinate shape.
    """
    if resolved.unit is RecurrenceUnitEnum.WEEK:
        # Through the shared table, NOT ``%A``: that delegates to the platform
        # ``strftime`` and follows ``LC_TIME``, which nothing in ``deploy/``
        # pins -- the same locale dependence the month names moved to
        # ``app.utils.dates`` to escape.  The plural is the honest reading: the
        # rule fires on that weekday every ``interval_n`` weeks.
        return f"{weekday_name(resolved.starts_on)}s"
    day = resolved.day_of_month
    if day is None:
        raise RecurrenceDescriptionError(
            f"recurrence unit {resolved.unit!r} names no day of the month "
            f"and no coordinate shape.  Every member of RecurrenceUnitEnum "
            f"must have one: a cell that omits when a definition fires reads "
            f"as one that fires on no particular day."
        )
    if resolved.unit is RecurrenceUnitEnum.MONTH and resolved.interval_n == 1:
        return f"day {day}"
    return f"{month_name(resolved.starts_on.month, abbr=True)} {day}"


def _placement_note(resolved: ResolvedRecurrence) -> str | None:
    """Return the phrase for a placement that DEFERS, or ``None``.

    The placement axis says which paycheck funds an occurrence.  Under
    ``CONTAINING_DATE`` that paycheck contains the occurrence, so the
    coordinate already answers "when does the money move" and a note would
    repeat it.  Under ``PERIOD_STARTING_ON_OR_AFTER`` the row lands on a LATER
    paycheck, so the cell must say so.

    Called only for a unit whose placement is not inert; see
    :func:`_parenthetical` for why the ``PERIOD`` unit's is.

    Args:
        resolved: The recurrence's two-axis meaning.

    Returns:
        The note, or ``None`` when the placement adds nothing.

    Raises:
        RecurrenceDescriptionError: When the placement is a member with no
            wording -- plan step R8 adds a third, and it must be worded here
            rather than silently read as the default.
    """
    if resolved.placement is PeriodPlacementEnum.CONTAINING_DATE:
        return None
    if resolved.placement is PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER:
        return _DEFERRED_NOTE
    raise RecurrenceDescriptionError(
        f"period placement {resolved.placement!r} has no wording.  Every "
        f"member of PeriodPlacementEnum must have one: falling back to the "
        f"containing-date phrasing would tell the user the money moves on a "
        f"day it does not."
    )


def _parenthetical(resolved: ResolvedRecurrence) -> str | None:
    """Return the bracketed half of the phrase, or ``None`` when there is none.

    Composes the coordinate and the placement note, dropping the coordinate in
    the ONE case where the note already implies it: a monthly rule whose
    occurrence is the 1st and whose placement is
    ``PERIOD_STARTING_ON_OR_AFTER`` means "the first paycheck of the month",
    and naming ``day 1`` beside that states the mechanism twice.  Any other
    day IS a second fact -- a deferring rule on the 15th funds from the first
    paycheck on or after the 15th -- so it is stated.

    **The collapse names the placement it rests on**, because the implication
    holds for that member and no other.  Plan step R8 adds a fund-in-ADVANCE
    placement (ledger row D20): "the last paycheck on or BEFORE the 1st" is
    NOT the month's first paycheck, it is the previous month's last, so a
    condition keyed only on unit / interval / day would silently delete the
    coordinate from a rule whose money moves in a different month.

    **The ``PERIOD`` unit has no parenthetical at all**, and this is the one
    place that says so.  It has no calendar coordinate -- its occurrences are
    the owner's paydays, not a day the rule names -- and its placement is
    INERT: every occurrence it emits is a period's own ``start_date``, and both
    placements carry such a date back to that same period, proven in
    :mod:`app.services.recurrence._occurrence`'s module docstring.  Naming a
    distinction that makes no difference would be noise, so "Every paycheck"
    is the whole phrase.

    Args:
        resolved: The recurrence's two-axis meaning.

    Returns:
        The parenthetical's contents without its brackets, or ``None`` when the
        cadence has none.
    """
    if resolved.unit is RecurrenceUnitEnum.PERIOD:
        return None
    coordinate = _coordinate(resolved)
    note = _placement_note(resolved)
    if note is None:
        return coordinate
    if (
        resolved.placement is PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER
        and resolved.unit is RecurrenceUnitEnum.MONTH
        and resolved.interval_n == 1
        and resolved.day_of_month == _IMPLIED_DEFERRED_DAY
    ):
        return note
    return f"{coordinate}, {note}"


def _never_stops(_bound: NeverEnds) -> None:
    """Return no phrase: an indefinite recurrence shows no second line.

    Takes the bound it will not read, because the three phrase functions are
    dispatched over one table and must share a signature -- and because a
    shape that answers "nothing to say" IS an answer, not an absence the
    dispatcher special-cases.

    Returns:
        Always ``None``.
    """
    return None


def _stops_on_date(bound: EndsOnDate) -> str:
    """Return ``"until Mar 01, 2027"``.

    The month is named from :func:`app.utils.dates.month_name`, this
    application's one month-name producer, rather than formatted with ``%b``
    -- see :class:`RecurrenceDescription` for what that escapes and for what
    it does NOT claim.  The day is zero-padded because that is what the cell
    rendered before the phrase moved here, so no live row's wording changes
    for a reason unrelated to this step.

    Args:
        bound: The date shape.

    Returns:
        The phrase.
    """
    day = bound.on
    return f"until {month_name(day.month, abbr=True)} {day.day:02d}, {day.year}"


def _stops_after_count(bound: EndsAfterOccurrences) -> str:
    """Return ``"for 12 occurrences"``, singular at one.

    Args:
        bound: The count shape.

    Returns:
        The phrase.
    """
    noun = "occurrence" if bound.count == 1 else "occurrences"
    return f"for {bound.count} {noun}"


#: How each closing-bound shape is worded, keyed by the shape itself.
#:
#: Keyed by TYPE rather than by which column projection is set, so the table is
#: total over the same closed set :data:`._bounds.END_BOUND_KINDS` names and a
#: shape missing from it raises rather than rendering blank.  It sits here
#: rather than on the shapes because this module owns display COPY -- the same
#: division :data:`_UNIT_PLURALS` and :data:`_DEFERRED_NOTE` already keep.
#:
#: Each phrase function takes its OWN shape, so the value type is ``Any``
#: rather than ``EndBound``: a table of exact-shape handlers is contravariant
#: in its argument, and annotating it with the base would be a claim the
#: members do not make.
_STOP_PHRASES: dict[type[EndBound], "Callable[[Any], str | None]"] = {
    NeverEnds: _never_stops,
    EndsOnDate: _stops_on_date,
    EndsAfterOccurrences: _stops_after_count,
}


def _stops_phrase(bound: EndBound) -> str | None:
    """Return the words for when a recurrence stops, or ``None`` for never.

    Total over :data:`~app.services.recurrence._bounds.END_BOUND_KINDS` and
    RAISING for a shape it has no wording for, which is the same contract
    :func:`_stem` and :func:`_placement_note` hold for their own closed sets --
    and here it is the difference between a cell that omits a stop date and a
    cell that says there is none.  A shape added for plan step R8 and left out
    of :data:`_STOP_PHRASES` fails at the first render rather than reading as
    an indefinite commitment.

    Keyed on the shape's TYPE rather than on which of its two column
    projections is non-``None``: the columns are storage, a shape R8 adds may
    have neither, and asking "which column is set" is how three shapes came to
    be worded by two branches.

    Args:
        bound: The recurrence's closing bound.

    Returns:
        The phrase for the cell's second line, or ``None`` when the recurrence
        is indefinite and there is no second line.

    Raises:
        RecurrenceDescriptionError: When *bound*'s shape has no wording.
    """
    phrase = _STOP_PHRASES.get(type(bound))
    if phrase is None:
        raise RecurrenceDescriptionError(
            f"recurrence bound {type(bound).__name__!r} has no wording.  Every "
            f"shape a closing bound can take must have one: a cell that omits "
            f"a stop the rule states reads as a commitment that never ends, "
            f"which is money the surface says will keep being spent."
        )
    return phrase(bound)


def describe(resolved: ResolvedRecurrence) -> RecurrenceDescription:
    """Describe a resolved recurrence in the words a surface shows.

    The single producer of a recurrence's display phrase, and total over
    ``(interval_n, unit, placement)`` rather than over the closed pattern set
    it replaced -- so ``(2, MONTH)`` and ``(1, WEEK)`` already read correctly
    though nothing authors them until plan step R8.

    Args:
        resolved: The recurrence's two-axis meaning, from
            :func:`app.services.recurrence.resolve`.

    Returns:
        The :class:`RecurrenceDescription` a surface renders.

    Raises:
        RecurrenceDescriptionError: When *resolved* names a unit or placement
            this module has no wording for.
    """
    # The stem FIRST, because it is the broader of the two refusals: a unit
    # with no plural noun has no wording at all, while a unit that has one but
    # no coordinate shape is the narrower gap :func:`_coordinate` names.  In
    # the other order the broader case never reaches its own message, and one
    # of the two raises would be unreachable rather than merely rare.
    stem = _stem(resolved.unit, resolved.interval_n)
    parenthetical = _parenthetical(resolved)
    cadence = stem if parenthetical is None else f"{stem} ({parenthetical})"
    return RecurrenceDescription(
        cadence=cadence, stops=_stops_phrase(resolved.end_bound),
    )


__all__ = ["RecurrenceDescription", "RecurrenceDescriptionError", "describe"]
