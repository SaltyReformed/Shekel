"""The derivation: an owner's paydays in, their pay periods out (plan step C1).

``budget.pay_periods`` stores three values per row and only ``start_date`` is a
fact -- the day money arrived.  ``end_date`` is ``lead(start_date) - 1`` and
``period_index`` is ``row_number() - 1``, both stored beside the fact they
derive from with nothing reconciling them, which is why five separate runtime
fences exist to police one functional dependency
(``docs/plans/implementation_plan_pay_calendar.md`` section 1).  This module is
that dependency, written once::

    period_index = row_number() over (order by start_date) - 1
    end_date     = coalesce(lead(start_date) - 1,          -- the definition
                            start_date + cadence_days - 1)  -- the open last one

**Nothing in the application calls it yet.**  Plan step C1 is deliberately the
oracle and nothing else: the value has to be proven equal to what is stored --
over a clone of production and over irregular schedules the live data cannot
supply -- before anything reads it, writes it, or drops the columns.  Its proof
lives in ``tests/oracles/pay_calendar_derivation.py``,
``tests/test_services/test_pay_calendar_derivation.py`` and
``tests/manual/verify_pay_calendar_derivation.py``.

**Pure, and that is load-bearing.**  No session, no Flask, no clock: the
derivation is a function of two values, so the harness can drive it over
production's real 61 rows and over a generated sweep without a database, and
the two runs exercise the same code.  A derivation reachable only through a
query could not be diffed against the columns it is meant to replace.

**Why the last end is a different KIND of value, and says so.**  Every other
end is dictated by a fact -- the next payday.  The last one has no next payday,
so it is projected forward from ``budget.pay_schedule.cadence_days`` (ruling
2026-08-08: "a projection stated as one").  :attr:`DerivedPeriod.end_is_projected`
is that statement, and it cannot be recomputed by a consumer holding one period
out of its calendar.  It is not cosmetic: plan finding P12 -- a
``/pay-periods/generate`` post naming an already-existing payday creates zero
rows and still reaches ``upsert_schedule`` (``routes/pay_periods.py``), so the
stored cadence is rewritten by a batch that wrote nothing -- moves this end and
only this end, and today the stored column hides that.  Once the column is
gone, the flag is the only thing on the value that distinguishes a horizon
derived from a fact from one derived from a setting a no-op post can change.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.exceptions import ShekelError

#: The cadence bounds, mirroring ``ck_pay_schedule_cadence_range`` on
#: ``budget.pay_schedule.cadence_days``.  Named here rather than inlined
#: because :func:`_validate_cadence` states them in its refusal message, and a
#: message that quotes a bound the code does not enforce is how the first cut
#: of this module shipped.
MIN_CADENCE_DAYS: int = 1
MAX_CADENCE_DAYS: int = 365


class PayCalendarError(ShekelError, ValueError):
    """A payday set or cadence cannot define a pay calendar.

    A broken invariant, never user input, which is why no route catches it and
    no form field is named in the message.  ``budget.pay_periods`` already
    enforces the payday model's key (``uq_pay_periods_user_start``), so a
    duplicate payday cannot come out of the table; reaching this from the
    application would mean a caller assembled a payday set by hand and got it
    wrong.  Failing loud is the only safe disposition -- every alternative
    (de-duplicating, clamping a bad cadence) silently produces a calendar whose
    periods do not tile the days the owner's money lives on.

    Also a ``ValueError`` because it is raised for rejected function arguments,
    where that is Python's own contract; a caller catching either name gets it.

    It is NOT the successor of ``recurrence._calendar.RecurrenceScheduleError``,
    and saying so is a correction the review of this step made.  That class
    refuses an overlapping or reversed SCHEDULE at the value boundary, and the
    plan retires it rather than relocating it: C5 deletes
    ``PeriodCalendar.__post_init__``'s two refusals, which are its only raise
    sites in ``app/``, because the states they police stop being expressible.
    What this class refuses is different -- a payday SET or a cadence that
    cannot define a calendar in the first place.
    """


@dataclass(frozen=True)
class DerivedPeriod:
    """One pay period, derived rather than stored.

    The shape of the two columns plan step C4 drops, plus the one thing the
    columns could not say.  Ordered ``start_date`` ascending inside a
    :func:`derive_periods` result, with ``period_index`` matching that order by
    construction -- the disagreement between index order and date order that
    ``uq_pay_periods_user_index`` and three runtime fences exist to catch is
    not expressible here.

    Attributes:
        period_id: The ``budget.pay_periods.id`` the payday was read from, or
            ``None`` when the period is not MATERIALISED -- a projection past
            the owner's horizon, or a candidate the writer has not saved yet.
            The one field here that is carried rather than derived, and it is
            carried because identity is what separates the two questions plan
            step C2 has to keep apart: "which paycheck does this row live in"
            needs a row a foreign key can point at, while "which span does this
            day fall in" is answerable by a projection.  A value that could not
            say which it was would answer the write question wrongly and
            silently.
        period_index: The owner's 0-based ordinal for this period, which is its
            position in payday order.
        start_date: The payday that opens the period.  The only fact in the
            row; everything else here is derived from it and its neighbours.
        end_date: The last day the period covers -- the day before the next
            payday, or, for the last period, ``start_date + cadence_days - 1``.
        end_is_projected: Whether :attr:`end_date` came from the cadence rather
            than from the next payday.  ``True`` for the LAST period of a
            non-empty calendar and ``False`` for every other -- so exactly one
            per calendar, and none at all for the empty one.  A consumer
            holding a single period cannot work this out, which is why it rides
            on the value: a projected end moves when the stored cadence moves,
            and a fact-derived end does not.
    """

    period_id: "int | None"
    period_index: int
    start_date: date
    end_date: date
    end_is_projected: bool


def derive_periods(
    paydays: "Iterable[tuple[int | None, date]]", cadence_days: int,
) -> tuple[DerivedPeriod, ...]:
    """Derive an owner's whole pay calendar from their paydays and cadence.

    **Takes the owner's COMPLETE payday set, never a window.**  A period's end
    is its successor's payday, so the LAST payday in whatever list arrives here
    falls to the cadence projection -- which means a partial list makes one
    period report a different end depending on which window asked (plan finding
    P14; the sibling shape is measured in-repo at ``$150,000.00``,
    ``loan_ledger/_visible.owner_pay_periods``).  This function cannot detect
    partiality -- a slice of paydays is indistinguishable from a short schedule
    -- so the guarantee has to be structural at the caller.  Plan step C2 makes
    it so: the calendar is built once from the complete set and a window becomes
    a VIEW over it that keeps the real ends.

    **A derived end is not stable against a later write, and that is the one
    way it differs from the column it replaces** (found by adversarial review
    of this step, 2026-08-08).  A stored ``end_date`` cannot move when a
    neighbouring row is written; a derived one can.  Concretely, with paydays
    ``[01-02, 01-16]`` at cadence 14 the second period ends 01-29 (projected);
    append the payday 01-28 -- LATER than every existing one, so a forward-only
    write by the plan's own definition -- and that end moves back to 01-27.
    Only ends inside the last period's PROJECTED span can move this way; an end
    dictated by a following payday is fixed for as long as that payday is.
    A row already dated into the vacated days is not left outside its period --
    ``utils.dates.attribution_date`` clamps it -- so the damage is a row
    silently RENDERED on a different day, which is plan finding P10's shape
    reached through a door P10 does not cover.  ``_reject_overlapping_batch``
    blocks that write today because it compares against the stored end; plan
    step C3 deletes it, so the rule has to be re-established there.  The
    derivation states the property and does not police it: it is a function of
    a payday set, and which sets a user may write is the writer's question.

    Order and duplication are handled HERE rather than trusted, because the
    result's whole value is that index order and date order cannot disagree:
    the input is sorted (so a caller's query order cannot change the answer) and
    a repeated payday is refused (it would place two periods on one opening day
    and give the earlier of them an ``end_date`` before its own
    ``start_date``).

    Args:
        paydays: The owner's complete set of paydays as
            ``(period_id, payday)`` pairs, in any order.  ``period_id`` is the
            ``budget.pay_periods.id`` the payday was read from, or ``None`` for
            a period that is not materialised; it takes no part in the
            derivation and rides through onto
            :attr:`DerivedPeriod.period_id`.  Empty is a legal input and yields
            an empty calendar -- a user who has never generated a schedule, and
            the companion role, which by design holds no paydays of its own.
        cadence_days: Days between paydays, from ``budget.pay_schedule``.  Read
            only for the LAST period's end; every other end is dictated by the
            next payday, so a wrong cadence can move exactly one day in the
            result.  Validated unconditionally, before the paydays are looked
            at: the caller has to resolve a cadence to get here at all, so a
            bad one is a bad caller whether or not this particular owner has
            paydays yet, and refusing it only when the data happens to reach
            the projection branch would hide it until the day a user records
            their first payday.

    Returns:
        The owner's periods, ``start_date`` ascending, ``period_index`` running
        0..n-1 in that order.  Empty for an empty payday set.

    Raises:
        PayCalendarError: ``cadence_days`` is not an ``int``, or falls outside
            1..365; a ``period_id`` is neither an ``int`` nor ``None``; a
            payday is not a ``datetime.date``, or is a ``datetime.datetime``
            (which is a ``date`` subclass and would silently give every derived
            end a time component); or a payday appears twice.
    """
    _validate_cadence(cadence_days)
    # Sorted on the PAYDAY alone.  Sorting the pairs would break on a ``None``
    # id the moment two paydays tied -- and they cannot tie, which is checked
    # next, so keying the sort on the id would only hide that check.
    ordered = sorted(_validated(paydays), key=lambda pair: pair[1])
    for (_earlier_id, earlier), (_later_id, later) in zip(ordered, ordered[1:]):
        if earlier == later:
            raise PayCalendarError(
                f"payday {earlier.isoformat()} appears twice in the same "
                f"calendar.  A pay period is identified one-to-one by the "
                f"payday that opens it (uq_pay_periods_user_start enforces "
                f"that on the table), so two periods cannot share an opening "
                f"day; the first of them would be derived an end_date one day "
                f"before its own start_date."
            )

    last_position = len(ordered) - 1
    return tuple(
        DerivedPeriod(
            period_id=period_id,
            period_index=position,
            start_date=payday,
            end_date=(
                payday + timedelta(days=cadence_days - 1)
                if position == last_position
                else ordered[position + 1][1] - timedelta(days=1)
            ),
            end_is_projected=position == last_position,
        )
        for position, (period_id, payday) in enumerate(ordered)
    )


def _validate_cadence(cadence_days: int) -> None:
    """Refuse a cadence that is not an in-range plain integer.

    Held to the same standard as :func:`_validated` holds a payday, and for the
    same reason -- the review of this step measured what the looser check let
    through.  ``bool`` is an ``int`` subclass, so ``True`` was accepted as a
    one-day cadence; a ``float`` was accepted and silently TRUNCATED, because
    ``date.__add__`` reads only ``timedelta.days``, so ``14.9`` produced the
    same calendar as ``14``; and ``None`` -- which
    ``pay_schedule_service.resolve_cadence`` is typed to return for a user who
    has neither a schedule row nor a period to infer one from -- raised a bare
    ``TypeError`` naming no invariant.  (A brand-new signup is one step away
    rather than in that state: ``register_user`` writes a bootstrap payday and
    no schedule row, so the cadence is INFERRED from that one period's length,
    which is plan finding P8's circularity rather than a ``None``.)

    The upper bound is the stored column's own
    (``ck_pay_schedule_cadence_range``, 1..365).  Enforcing only the lower half
    while the error message quoted both was the gap; a cadence above 365 cannot
    come from a schedule row, so accepting one would mean projecting a horizon
    off a value no write door could have produced.

    Args:
        cadence_days: The candidate cadence.

    Raises:
        PayCalendarError: The value is not an ``int`` (a ``bool`` included), or
            falls outside 1..365.
    """
    if not isinstance(cadence_days, int) or isinstance(cadence_days, bool):
        raise PayCalendarError(
            f"cadence_days must be a plain int, got "
            f"{type(cadence_days).__name__} {cadence_days!r}.  A bool is an "
            f"int subclass and would pass as a one-day cadence; a float is "
            f"truncated by date arithmetic, which moves a horizon silently; "
            f"and None is what pay_schedule_service.resolve_cadence returns "
            f"for a user with no schedule row and no period to infer from."
        )
    if not MIN_CADENCE_DAYS <= cadence_days <= MAX_CADENCE_DAYS:
        raise PayCalendarError(
            f"cadence_days must be at least {MIN_CADENCE_DAYS} day and at "
            f"most {MAX_CADENCE_DAYS}, got {cadence_days}.  Below the floor "
            f"the last pay period would end before its own payday; above the "
            f"ceiling the value cannot have come from a stored schedule -- "
            f"budget.pay_schedule.cadence_days is constrained to "
            f"{MIN_CADENCE_DAYS}..{MAX_CADENCE_DAYS} by "
            f"ck_pay_schedule_cadence_range."
        )


def _validated(
    paydays: "Iterable[tuple[int | None, date]]",
) -> "list[tuple[int | None, date]]":
    """Return *paydays* as a list, refusing any pair it cannot derive from.

    ``datetime`` is a subclass of ``date``, so a bare ``isinstance`` check would
    accept one -- and every derived end would silently carry a time component,
    comparing unequal to the ``DATE`` column it is meant to reproduce and
    placing a day's money by an accident of the clock.  The stored column is
    ``DATE`` and the app's civil day is ``app.utils.dates.display_today()``, so
    a ``datetime`` reaching here is a caller that skipped the display-timezone
    conversion; refusing it names that at the boundary instead of at the diff.

    The id is checked too, even though nothing here computes with it: it rides
    onto :attr:`DerivedPeriod.period_id`, whose whole purpose is to be the
    thing a foreign key points at, so a value of the wrong type would surface
    as a failed lookup somewhere far from the caller that supplied it.

    Args:
        paydays: The candidate ``(period_id, payday)`` pairs, in any order.

    Returns:
        The same pairs as a list, unsorted.

    Raises:
        PayCalendarError: A payday is not a ``datetime.date`` or is a
            ``datetime.datetime``, or an id is neither an ``int`` nor ``None``.
    """
    checked = []
    for period_id, payday in paydays:
        if not isinstance(payday, date) or isinstance(payday, datetime):
            raise PayCalendarError(
                f"a payday must be a datetime.date, got "
                f"{type(payday).__name__} {payday!r}.  budget.pay_periods."
                f"start_date is a DATE column and the app's civil day is "
                f"app.utils.dates.display_today(); a datetime here would give "
                f"every derived end a time component and place a day's money "
                f"by the process timezone."
            )
        if period_id is not None and (
            not isinstance(period_id, int) or isinstance(period_id, bool)
        ):
            raise PayCalendarError(
                f"a period_id must be an int or None, got "
                f"{type(period_id).__name__} {period_id!r} beside payday "
                f"{payday.isoformat()}.  It is what a foreign key points at, "
                f"and None is how a period that is not materialised says so."
            )
        checked.append((period_id, payday))
    return checked
