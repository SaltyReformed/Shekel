"""
Shekel Budget App -- An owner's pay rhythm, and the ERA it ran in, as values.

How often somebody is paid, and what payroll does when a payday lands on a day
no money moves on.  ``budget.pay_eras`` stores the pair as ``cadence_days``
and ``shift_id`` on each era row; :class:`Rhythm` is that pair in the
application, and it is a TYPE rather than two arguments because the halves
carry a JOINT rule -- a convention that displaces a payday is legal only on a
cadence longer than the longest run of consecutive closed days
(:func:`~app.utils.business_days.shortest_collision_free_cadence`).
:class:`Era` is a rhythm together with the day it took effect and the KIND it
runs on -- one row of that table as a value (plan step ``pay_calendar:C17-a``,
ruling **R-PC58**).

**Why it is a module of its own, which is plan step ``C14-e-1``'s one
structural decision.**  The pair was declared in
:mod:`app.services.pay_schedule_service`, and until ``C14-e`` that was the
right place: the pay calendar read only ``cadence_days``, so the one consumer
of the pair was the write door beside it.  ``C14-e-3`` made
:func:`~app.services.pay_calendar.projected_payday` the nominal grid day
DISPLACED under the convention, and ``C14-e-1`` threaded the pair to every
producer ahead of it so that money-moving diff was one expression -- which gives
the PURE half of :mod:`app.services.pay_calendar` the convention to carry.
That half may not import a module holding a database session, nor could it,
because ``pay_calendar._loader`` imports ``pay_schedule_service`` and the edge
back would be a cycle (pylint ``R0401``, measured 2026-09-05).

Three placements were possible and two are worse.  A second, structurally
identical pair inside the pay-calendar package is one value with two homes,
which is the defect rule 14 exists to refuse.
:mod:`app.utils.business_days` is the one module already below both, and it
owns the convention and the collision floor that constrains this pair -- but a
pay CADENCE is not a business-day fact, and at plan step ``C17`` this value
grows an ``effective_from`` and a cadence KIND (**R-PC58**), which would
stretch that module's subject further with every step.  So the value gets the
module its own name describes, below both consumers and above nothing:
**this module imports** :mod:`app.enums` **and nothing else**, and that module
imports only the standard library's ``enum``.  ``C17-a`` grew the value here
exactly as this paragraph predicted, and the import set did not move.

*The ROOT CAUSE this placement works around, stated rather than claimed away:*
:mod:`app.services.pay_calendar` *is one package holding a PURE derivation and
the IMPURE loader that feeds it, and its* ``__init__`` *re-exports both -- so
importing the package pulls the schedule service in behind it.  From scratch
the reader would live inside the pay-calendar package, which would then own
this value outright and* ``pay_schedule_service`` *would be the row WRITER
importing it.  That is a package split rather than a placement, and it is not
this step's.*
"""

from dataclasses import dataclass
from datetime import date

from app.enums import BusinessDayShiftEnum, PayCadenceKindEnum


@dataclass(frozen=True)
class Rhythm:
    """How often an owner is paid, and what payroll does on a closed day.

    Plan step **C14-b**.  The pair ``budget.pay_eras`` stores as
    ``cadence_days`` and ``shift_id`` (``budget.pay_schedule`` did, until
    ``C17-a``).  Written through two statements the row passes through a
    state neither statement means, and either order refuses a legal request
    -- so :func:`~app.services.pay_schedule_service.mint_era` takes the pair,
    judges the pair, and writes the pair, and no caller is able to hand it half
    of one.

    **The halves acquired a SHARED consumer at plan step ``C14-e``, which is
    why this stopped being a writer's value.**  A
    :class:`~app.services.pay_calendar.PayCalendar` is derived from the cadence
    AND the convention -- from ``C14-e-3`` the projection is the nominal grid
    day displaced under it -- so a calendar carrying half the pair could be
    handed the other half from another owner's schedule, a mismatch that
    produces a plausible wrong payday rather than an error.  *An adversarial
    review struck a sentence claiming each half has ONE consumer and the same
    one: the cadence has ten and the convention two.  What is true is the
    narrower thing the argument needs -- they now share one.*  That is the same argument
    :class:`~app.services.pay_schedule_service.ScheduleFacts` makes for
    pairing the cadence with ``history_opens_on`` rather than resolving them
    separately.

    Attributes:
        cadence_days: Days between consecutive paydays.  Bounded by
            :func:`~app.services.pay_schedule_service.reject_out_of_range_cadence`,
            not by this type -- a range is the column's own rule and belongs
            where its one writer asks it.  Validated again, for a different
            bound and a different reason, by
            :func:`~app.services.pay_calendar.derive_periods`.
        shift: What payroll does when a payday lands on a day no money moves
            on, as the :class:`~app.enums.BusinessDayShiftEnum` member rather
            than the ``ref.business_day_shifts`` id that spells it on the
            wire.  The id is what crosses a form and what the column holds, so
            :class:`~app.schemas.validation.pay_periods.BusinessDayShiftField`
            converts on the way in and
            :func:`~app.services.pay_schedule_service.mint_era`
            converts on the way out; between them the value is a member, which
            is what :func:`~app.utils.business_days.shift_to_business_day`
            requires -- it REFUSES an integer rather than defaulting, so a
            rhythm carrying an id would hand
            :func:`~app.services.pay_calendar.projected_payday` a raise
            instead of a payday.  This is IDs-for-logic as the project means
            it: no ``name`` string is ever compared.
    """

    cadence_days: int
    shift: BusinessDayShiftEnum


@dataclass(frozen=True)
class Era:
    """One span of an owner's pay history: the rhythm, and since when.

    Plan step **pay_calendar:C17-a** (ruling **R-PC58**).  One row of
    ``budget.pay_eras`` as a value: the day the rhythm took effect, the KIND
    of rhythm it is, and the rhythm itself.  Which days an era governs is not
    a field, because it is not a fact of the row -- an era governs from its
    ``effective_from`` up to the next era's, and the earliest one also runs
    backward below the record, bounded by the owner's stated history.

    **``effective_from`` is the grid's phase.**  It is the era's first NOMINAL
    payday, so by construction a day the grid passes through, and every
    producer that continues the rhythm steps from it.  ``budget.pay_schedule``
    used to carry that phase as ``nominal_anchor`` beside a cadence the same
    batch wrote; a second field here would have to agree with this one modulo
    the cadence, which is the maintenance contract rule 14 exists to delete.

    Attributes:
        effective_from: The era's first nominal payday.
        kind: What kind of rhythm the era runs on
            (:class:`~app.enums.PayCadenceKindEnum`).  ``FIXED_DAYS`` is the
            only member until the day-of-month kinds land (``C17-d``); it is
            carried now so that leaf adds members rather than a field.
        rhythm: The cadence and the payday convention (:class:`Rhythm`).
    """

    effective_from: date
    kind: PayCadenceKindEnum
    rhythm: Rhythm


def era_covering(eras: "tuple[Era, ...]", day: date) -> Era:
    """Return the era that governs *day*.

    **The rule an era's span is DERIVED by**, written once: an era governs
    from its ``effective_from`` up to the next era's, so the era covering a
    day is the LATEST one taking effect on or before it -- and a day before
    every era is the EARLIEST era's, because that era alone runs backward
    below the record (bounded there by the owner's stated history, which is
    the calendar's question and not this function's).

    **Read in NOMINAL days, which is the writer's coordinate**: a batch states
    its first payday on the grid, and the era-mint decision asks which era's
    ``effective_from`` bounds that day.  The calendar's readers ask the same
    rule of a CASH day -- a recorded payday, a day to project for -- through
    ``pay_calendar.era_index_at``, whose bound is each era's first payday
    DISPLACED; the two part only where an era's first payday moved.

    A linear scan rather than a bisection: an owner holds a handful of eras,
    and this is asked once per batch and never per row.

    Args:
        eras: The owner's eras, ``effective_from`` ascending and NON-EMPTY --
            a caller holding a :class:`~app.services.pay_schedule_service.ScheduleFacts`
            holds at least one.
        day: The day to place.

    Returns:
        The governing :class:`Era`.
    """
    covering = eras[0]
    for era in eras:
        if era.effective_from > day:
            break
        covering = era
    return covering
