"""
Shekel Budget App -- The NOMINAL pay grid.

**The rhythm before the calendar touches it**: an owner's paydays as a plain
arithmetic progression -- an anchor and a cadence -- with no convention applied
and no holiday consulted.  Two functions, and they are inverses:
:func:`nominal_payday` maps a step count to a day and :func:`cadence_steps_to`
maps a day back to a step count.

**Why it is a module of its own, and it is a distinction rather than a
filing decision** (plan step **C14-d**).  Until the pay schedule carried a
shift convention there was one answer to "where does the next paycheck land",
so the grid and the projection were the same arithmetic and lived together in
:mod:`._derive`.  Ruling **R-PC54** ended that, and ``C14-e-3`` shipped it:
:func:`~._derive.projected_payday` is the nominal day DISPLACED onto a business
day under the owner's convention, so the two questions now answer differently
on roughly 3% of paydays -- **64** of the production owner's 1,888 PROJECTED
paydays out to ``CALENDAR_DATE_MAX`` and **22** of the 684 below their record,
under either displacing convention, re-derived 2026-09-06.

Both answers have real callers, which is what makes the split load-bearing
rather than tidy:

* the PROJECTION is what a calendar shows and what money is filed against, so
  :func:`~._derive.derive_periods` and
  :func:`~._projection.project_period_after` take it;
* the GRID is what a WRITER spaces a STATED batch on.
  ``pay_period_write.record_paydays`` runs the batch it is handed on the grid
  from its first nominal payday and records each element displaced
  (``pay_period_batch.requested_paydays``), and a minted era's phase is that
  nominal day -- so an anchor read off the displaced side would put the whole
  batch a displacement off the rhythm.  That is the drift **R-PC54** names as
  "a CASH date fed back into the rhythm".  *The extend door was the other
  grid caller until plan step ``C17-c-2b``, stepping the latest era's grid
  past the horizon; it materialises the calendar's own plan now
  (``pay_period_write.continue_paydays``), which reaches the grid only
  through the projection.*

**What the split could NOT make unwritable was fixed one layer up, and an
adversarial review of ``C14-d`` struck a sentence claiming this module did it.**
``extend_pay_periods`` used to pass a RECORDED payday, and ``C14-e-3``'s writer
records DISPLACED ones -- so each batch re-anchored the grid on the previous
batch's last cash day, which nothing here can prevent.  Measured against the
true cash rhythm (production's cadence and opening payday, 301 paydays,
2026-09-05): a batch of ONE -- the rolling top-up's steady state, since
``pay_period_rolling`` appends exactly the deficit -- recorded **178** of 301
paydays wrong under ``prior`` and drifted **8 days** by the end.  The remedy
was a nominal PHASE the schedule stores, which **R-PC54** refused and
**R-PC61** directed: ``budget.pay_schedule.nominal_anchor`` shipped at
``C14-e-2``, the door steps from it, and the count is **0 of 301**.  Ledger row
**PC-497** fault 2, closed.

Placed BELOW :mod:`._derive` in the package's one-way chain (ruling
**R-PC60**, developer 2026-09-05, on a fork that costed a new module against
moving prose out of ``_derive.py`` to fit under pylint's 1,000-line ceiling).  It imports nothing
from the package, and from ``app`` only the cadence VALUES it dispatches on
(:mod:`app.services.pay_rhythm`, a pure leaf that imports ``app.enums`` and
nothing else) and the two month primitives of :mod:`app.utils.dates` (a
pure leaf too, since plan step ``C17-d-2``): the purity that lets C1's
harness drive the derivation with no application stack starts here.

**The arithmetic is dispatched on the cadence's KIND, and the kind is the
value's type** (plan step ``pay_calendar:C17-d-1``, rulings **R-PC68** and
**R-PC80**).  A :class:`~app.services.pay_rhythm.FixedDays` cadence steps by
plain day arithmetic; the day-of-month kinds (``C17-d-2``, ruling
**R-PC79**) step by MONTHS and clamp the meant day to each month's end,
which no single expression over a day count can say.  Each kind's two
bodies live in ONE table below, keyed by the value's class, so a kind with
no arithmetic cannot be projected -- the lookup refuses it -- and every
reader above reaches whichever pair the era's cadence names through the two
public names alone.  An ``isinstance`` chain would say the same thing with
the kinds' order mattering; a table says it without.

**The month arithmetic is O(1) and walks nothing**: a payday is the meant
day clamped into the month *steps* months on (:func:`_monthly_payday`), and
the inverse compares the day with the clamp in its own month
(:func:`_monthly_steps_to`).  The semi-monthly grid is the same walk over
HALF-months: grid days are numbered ``2 x month + member`` from the
anchor's month, where the member is which of the sorted pair a day is, and
the anchor's own member is read off its date.  **The clamp is
:func:`app.utils.dates.clamped_day`, the recurrence walk's own** -- moved
below both packages by this step rather than spelled a second time here,
which is rule 14's remedy for a shared leaf a layer put out of reach.
"""

from datetime import date, timedelta

from app.services.pay_rhythm import FixedDays, Monthly, SemiMonthly
from app.utils.dates import clamped_day, month_ordinal


def _fixed_days_payday(anchor: date, cadence: FixedDays, steps: int) -> date:
    """Return the grid day *steps* cadences after *anchor* on a fixed-days grid.

    :func:`nominal_payday`'s body for :class:`~app.services.pay_rhythm.FixedDays`:
    the arithmetic progression ``anchor + steps x days``.

    Args:
        anchor: A day the grid passes through.
        cadence: The kind, carrying the day count.
        steps: How many whole cadences after *anchor*; negative reads
            backward.

    Returns:
        The grid day.
    """
    return anchor + timedelta(days=steps * cadence.days)


def _fixed_days_steps_to(anchor: date, cadence: FixedDays, day: date) -> int:
    """Return the whole cadences from *anchor* to the last fixed-days grid day at or before *day*.

    :func:`cadence_steps_to`'s body for
    :class:`~app.services.pay_rhythm.FixedDays`: floor division, which is
    what makes the answer right in both directions off one expression --
    Python's ``//`` floors toward negative infinity, so a *day* below
    *anchor* gives the negative count whose grid day is still at or before
    it, where C-style truncation would round toward the anchor and name a
    day AFTER *day*.

    Args:
        anchor: A day the grid passes through.
        cadence: The kind, carrying the day count.
        day: The day to place.

    Returns:
        The signed step count.
    """
    return (day - anchor).days // cadence.days


def _monthly_payday(anchor: date, cadence: Monthly, steps: int) -> date:
    """Return the grid day *steps* months after *anchor* on a monthly grid.

    :func:`nominal_payday`'s body for :class:`~app.services.pay_rhythm.Monthly`:
    the cadence's day clamped into the month *steps* months on from the
    anchor's.  The ANCHOR supplies the month and the VALUE the day, so an
    era opening on 2026-02-28 that means the 31st projects 03-31, 04-30,
    05-31 -- the meant day never decays to the 28th (ruling **R-PC79**,
    the shape ``recurrence:R-R3`` gives a rule).  At ``steps = 0`` it is the
    anchor itself exactly when the anchor lies on its grid, which
    ``pay_era_write.reject_phase_off_grid`` and
    :func:`~._derive.validate_eras` hold.

    Args:
        anchor: A day the grid passes through.
        cadence: The kind, carrying the day of the month.
        steps: How many whole months after *anchor*; negative reads
            backward.

    Returns:
        The grid day.
    """
    return clamped_day(month_ordinal(anchor) + steps, cadence.day)


def _monthly_steps_to(anchor: date, cadence: Monthly, day: date) -> int:
    """Return the whole months from *anchor* to the last monthly grid day at or before *day*.

    :func:`cadence_steps_to`'s body for
    :class:`~app.services.pay_rhythm.Monthly`.  Each month holds exactly one
    grid day, so the answer is the month distance when *day*'s own month's
    grid day is at or before it, and one less when *day* falls earlier in
    its month than that.  Signed for the same reason the fixed-days inverse
    floors: a *day* below *anchor* counts negative months back to the grid
    day still at or before it.

    Args:
        anchor: A day the grid passes through.
        cadence: The kind, carrying the day of the month.
        day: The day to place.

    Returns:
        The signed step count.
    """
    ordinal = month_ordinal(day)
    months = ordinal - month_ordinal(anchor)
    if clamped_day(ordinal, cadence.day) <= day:
        return months
    return months - 1


def _semi_monthly_payday(anchor: date, cadence: SemiMonthly, steps: int) -> date:
    """Return the grid day *steps* paydays after *anchor* on a semi-monthly grid.

    :func:`nominal_payday`'s body for
    :class:`~app.services.pay_rhythm.SemiMonthly`.  Grid days are numbered
    ``2 x month + member`` from the anchor's month, so *steps* paydays on
    from the anchor's own number is a half-month index whose ``divmod`` by
    two names the month and which day of the pair -- ``//`` and ``%`` share
    one floor, so a negative index reads back through the previous months'
    upper and lower days in the right order.

    Args:
        anchor: A day the grid passes through.
        cadence: The kind, carrying the sorted pair.
        steps: How many paydays after *anchor*; negative reads backward.

    Returns:
        The grid day: the pair member clamped into its month.
    """
    months, member = divmod(cadence.member_of(anchor) + steps, 2)
    return clamped_day(month_ordinal(anchor) + months, cadence.days[member])


def _semi_monthly_steps_to(anchor: date, cadence: SemiMonthly, day: date) -> int:
    """Return the paydays from *anchor* to the last semi-monthly grid day at or before *day*.

    :func:`cadence_steps_to`'s body for
    :class:`~app.services.pay_rhythm.SemiMonthly`, on
    :func:`_semi_monthly_payday`'s numbering.  *day*'s own month holds two
    grid days, the lower then the upper: the last one at or before *day*
    is the upper when *day* has reached it, else the lower when *day* has
    reached that, else the PREVIOUS month's upper -- three cases, because
    the lower day clamps never (it is at most 27) and the previous month's
    upper day is always at or before the current month's first day.

    Args:
        anchor: A day the grid passes through.
        cadence: The kind, carrying the sorted pair.
        day: The day to place.

    Returns:
        The signed step count.
    """
    ordinal = month_ordinal(day)
    lower, upper = cadence.days
    if clamped_day(ordinal, upper) <= day:
        member = 1
    elif clamped_day(ordinal, lower) <= day:
        member = 0
    else:
        ordinal -= 1
        member = 1
    index = 2 * (ordinal - month_ordinal(anchor)) + member
    return index - cadence.member_of(anchor)


#: The grid arithmetic per cadence KIND: ``{kind class: (payday, steps_to)}``.
#:
#: The two bodies of each kind are one entry so a kind cannot hold a forward
#: body without its inverse; the public functions below look the pair up by
#: ``type(cadence)`` and a kind absent here is refused rather than guessed
#: (:func:`_arithmetic_for`).  Every pair holds
#: :func:`cadence_steps_to`'s contract -- ``payday(steps_to(d)) <= d <
#: payday(steps_to(d) + 1)`` -- and the day-of-month pairs are graded
#: against a brute-force listing over every legal pair, anchor and day
#: rather than argued (``tests/test_services/test_pay_grid_month_kinds.py``).
_ARITHMETIC = {
    FixedDays: (_fixed_days_payday, _fixed_days_steps_to),
    Monthly: (_monthly_payday, _monthly_steps_to),
    SemiMonthly: (_semi_monthly_payday, _semi_monthly_steps_to),
}

#: The cadence KINDS this grid has arithmetic for -- the table's keys, and
#: the ONE spelling of the kind set.  :func:`~._eras.validate_cadence` gates
#: on it, so a value the grid could not project is refused where a calendar
#: is built rather than enumerated a second time there.
KINDS = frozenset(_ARITHMETIC)


def _arithmetic_for(cadence):
    """Return the ``(payday, steps_to)`` pair for *cadence*'s kind.

    Args:
        cadence: A cadence value whose class keys :data:`_ARITHMETIC`.

    Returns:
        The pair of bodies.

    Raises:
        TypeError: *cadence* is of a kind this grid has no arithmetic for --
            a caller that built a rhythm by hand from something that is not a
            cadence value.  Python's own contract for a wrong argument type;
            :func:`~._eras.validate_cadence` refuses the same thing with the
            package's error where a calendar is built, so an era sequence
            never reaches here unvalidated.
    """
    try:
        return _ARITHMETIC[type(cadence)]
    except KeyError:
        raise TypeError(
            f"no pay grid arithmetic for a cadence of type "
            f"{type(cadence).__name__}: {cadence!r}.  A cadence is one of the "
            f"kinds pay_rhythm declares, and each is an entry of "
            f"pay_calendar._grid._ARITHMETIC."
        ) from None


def nominal_payday(anchor: date, cadence, steps: int) -> date:
    """Return the grid day *steps* whole cadences after *anchor*.

    **The rhythm's one arithmetic body**, dispatched on *cadence*'s kind.
    :func:`~._derive.projected_payday` is this function plus the owner's
    convention, and calls it rather than restating it, so the day a projection
    displaces FROM and the day a writer continues ON are one value.

    **One CALL does not compound, and that is the whole of what the step count
    buys.**  Every day is measured from the *anchor* given, so displacing the
    result at :func:`~._derive.projected_payday` cannot move a later day in the
    SAME call -- the hazard
    :func:`~app.utils.business_days.shift_to_business_day` hands explicitly to
    its caller.  **It says nothing about the anchor**, and an adversarial
    review of ``C14-d`` struck a sentence that read as though it did: a caller
    passing an anchor it took from a previous answer compounds across calls,
    and nothing here can see that.  ``extend_pay_periods`` WAS such a caller
    until ``C14-e-2`` gave it the stored phase to step from -- see this
    module's own docstring for the measurement -- and since ``C17-c-2b`` it
    steps no grid at all.

    Args:
        anchor: A day the owner's rhythm passes through.  A RECORDED payday at
            every call site today, which is not the same as a day on the grid:
            **R-PC47** says payroll may have moved it, and ledger row **N-495**
            is that the projection inherits the offset when it has.  This
            function is not where that is repaired -- it answers the question
            it was asked, from the anchor it was given.
        cadence: The era's cadence, a value of one of the kinds
            :mod:`app.services.pay_rhythm` declares, already validated by
            :func:`~._eras.validate_cadence` at the caller; re-validating
            per call would put the bound in a second place.
        steps: How many whole cadences after *anchor*.  ``1`` is the next
            payday, ``0`` is *anchor*.  NEGATIVE is reachable and not a misuse
            -- :meth:`~._calendar.PayCalendar.span_containing` asks about days
            BELOW its anchor, where :func:`cadence_steps_to` answers with a
            negative count.

    Returns:
        The grid day.  It may fall outside
        :data:`~app.utils.dates.CALENDAR_DATE_MIN` ..
        :data:`~app.utils.dates.CALENDAR_DATE_MAX`; bounding it is the
        caller's, as it is for the displacement one layer up.

    Raises:
        TypeError: *cadence* is of no kind this grid knows
            (:func:`_arithmetic_for`).
    """
    payday, _steps_to = _arithmetic_for(cadence)
    return payday(anchor, cadence, steps)


def cadence_steps_to(anchor: date, cadence, day: date) -> int:
    """Return the whole cadences from *anchor* to the last rhythm day at or before *day*.

    :func:`nominal_payday`'s INVERSE, and it is a function for the same reason:
    the progression is read from both ends.
    :func:`~._projection.project_period_after` steps it forward from the last saved
    payday; :mod:`._rhythm` steps it backward from the first, below which the
    app used to count nothing at all (ledger row **N-390**, plan step
    **balance:X-bh-2**).  Two copies of ``(day - anchor).days //
    cadence_days`` would be two places for the rhythm's own arithmetic to
    disagree, which is exactly the class ledger row **P6** counted seven of for
    the containment question.

    It answers in both directions off one contract: a *day* before *anchor*
    gives a NEGATIVE count, and ``nominal_payday(anchor, cadence, steps)`` is
    the rhythm day at or before *day* either way, with
    ``nominal_payday(anchor, cadence, steps + 1)`` strictly after it.  Every
    kind's inverse must hold that pair of bounds; the fixed-days one is floor
    division (:func:`_fixed_days_steps_to`), and the day-of-month kinds hold
    it by comparing the day with the clamp in its own month
    (:func:`_monthly_steps_to`, :func:`_semi_monthly_steps_to`).

    **It is the NOMINAL grid's inverse and not the projection's**, which is
    what puts it here rather than beside
    :func:`~._projection.project_period_after` -- one of its two callers, the other
    being :mod:`._rhythm` above.  Since ``C14-e-3``
    displaces a payday the round trip stops being exact, and that is precisely
    why that function probes its answer's NEIGHBOURS instead of trusting the
    count: the estimate is a grid question asked of a displaced world.  Since
    ``C14-e-3`` that world is the live one.

    Args:
        anchor: A day the owner is paid on.  The progression passes through it.
        cadence: The era's cadence, a validated value of one of the kinds
            :mod:`app.services.pay_rhythm` declares.
        day: The day to place.  May precede, equal or follow *anchor*.

    Returns:
        The signed number of whole cadences: ``0`` when *day* falls in
        ``[anchor, nominal_payday(anchor, cadence, 1))``, negative below
        *anchor*, positive above.

    Raises:
        TypeError: *cadence* is of no kind this grid knows
            (:func:`_arithmetic_for`).
    """
    _payday, steps_to = _arithmetic_for(cadence)
    return steps_to(anchor, cadence, day)
