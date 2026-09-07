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
:mod:`._derive`.  Ruling **R-PC54** ends that: ``C14-e`` makes
:func:`~._derive.projected_payday` the nominal day DISPLACED onto a business
day under the owner's convention, and from that step the two questions have
different answers on roughly 3% of paydays (**64** of the production owner's
own 1,951 out to ``CALENDAR_DATE_MAX``, under either displacing convention,
measured 2026-09-05).

Both answers have real callers, which is what makes the split load-bearing
rather than tidy:

* the PROJECTION is what a calendar shows and what money is filed against, so
  :func:`~._derive.derive_periods` and
  :func:`~._derive.project_period_after` take it;
* the GRID is what a WRITER continues.  ``pay_period_admin.extend_pay_periods``
  appends paydays past the owner's last recorded one, and
  ``pay_period_write.record_paydays`` spaces the batch it is handed by flat
  cadence arithmetic -- so an anchor read off the displaced side puts the whole
  batch a displacement off the rhythm.  That is the drift **R-PC54** names as
  "a CASH date fed back into the rhythm".

**What the split does NOT make unwritable, and an adversarial review of
``C14-d`` struck a sentence claiming it did.**  The anchor
``extend_pay_periods`` passes is a RECORDED payday, and from ``C14-e`` the
writer records DISPLACED ones -- so each batch re-anchors the grid on the
previous batch's last cash day, and this module cannot prevent that.  Measured
against the true cash rhythm (production's cadence and opening payday, the
C14-e writer simulated, 301 paydays, 2026-09-05): a batch of ONE -- which is
the rolling top-up's steady state, since ``pay_period_rolling`` appends exactly
the deficit -- records **178** of 301 paydays wrong under ``prior`` and drifts
**8 days** by the end; a batch of 26 happens to record none wrong on this
schedule, which is where the holidays fall rather than a property.  The remedy
is a nominal PHASE the schedule stores, which **R-PC54** refuses here and
**R-PC58** places at ``C17``; the finding is the ledger's.

Placed BELOW :mod:`._derive` in the package's one-way chain (ruling
**R-PC60**, developer 2026-09-05, on a fork that costed a new module against
moving prose out of ``_derive.py`` to fit under pylint's 1,000-line ceiling).  It imports nothing
from the package and nothing from ``app``: the purity that lets C1's harness
drive the derivation with no application stack starts here.
"""

from datetime import date, timedelta


def nominal_payday(anchor: date, cadence_days: int, steps: int) -> date:
    """Return the grid day *steps* whole cadences after *anchor*.

    **The rhythm's one arithmetic body.**
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
    and nothing here can see that.  ``extend_pay_periods`` is such a caller
    from ``C14-e`` onward -- see this module's own docstring for the
    measurement.

    Args:
        anchor: A day the owner's rhythm passes through.  A RECORDED payday at
            every call site today, which is not the same as a day on the grid:
            **R-PC47** says payroll may have moved it, and ledger row **N-495**
            is that the projection inherits the offset when it has.  This
            function is not where that is repaired -- it answers the question
            it was asked, from the anchor it was given.
        cadence_days: Days between paydays, a positive ``int`` already
            validated by :func:`~._derive.validate_cadence` at the caller;
            re-validating per call would put the bound in a second place.
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
    """
    return anchor + timedelta(days=steps * cadence_days)


def cadence_steps_to(anchor: date, cadence_days: int, day: date) -> int:
    """Return the whole cadences from *anchor* to the last rhythm day at or before *day*.

    :func:`nominal_payday`'s INVERSE, and it is a function for the same reason:
    the progression is read from both ends.
    :func:`~._derive.project_period_after` steps it forward from the last saved
    payday; :mod:`._rhythm` steps it backward from the first, below which the
    app used to count nothing at all (ledger row **N-390**, plan step
    **balance:X-bh-2**).  Two copies of ``(day - anchor).days //
    cadence_days`` would be two places for the rhythm's own arithmetic to
    disagree, which is exactly the class ledger row **P6** counted seven of for
    the containment question.

    Floor division, so it answers in both directions off one expression: a
    *day* before *anchor* gives a NEGATIVE count, and
    ``nominal_payday(anchor, cadence_days, steps)`` is the rhythm day at or
    before *day* either way.  Python's ``//`` floors toward negative infinity,
    which is what makes that true rather than a coincidence -- C-style
    truncation would round a backward step toward the anchor and name a day
    AFTER *day*.

    **It is the NOMINAL grid's inverse and not the projection's**, which is
    what puts it here rather than beside
    :func:`~._derive.project_period_after` -- one of its two callers, the other
    being :mod:`._rhythm` above.  Once ``C14-e``
    displaces a payday the round trip stops being exact, and that is precisely
    why that function probes its answer's NEIGHBOURS instead of trusting the
    count: the estimate is a grid question asked of a displaced world.

    Args:
        anchor: A day the owner is paid on.  The progression passes through it.
        cadence_days: Days between paydays, a positive ``int``.
        day: The day to place.  May precede, equal or follow *anchor*.

    Returns:
        The signed number of whole cadences: ``0`` when *day* falls in
        ``[anchor, anchor + cadence_days)``, negative below *anchor*, positive
        above.
    """
    return (day - anchor).days // cadence_days
