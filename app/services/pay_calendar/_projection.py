"""The forward continuation: the projected period covering any day past the record.

**Plan step ``pay_calendar:C17-b-1``, a pure move** (ruling **R-PC69**,
developer 2026-09-11).  :func:`project_period_after` and its refusal
:func:`covering_projection` lived in :mod:`._derive` beside the rule they
continue -- a period runs from its payday to the day before the next -- and
that module stood at 998 of pylint's 1,000 lines (ledger row **PC-498**) with
the money-moving leaf ``C17-b-2`` about to edit both.  The remedy is a SPLIT
and not a trim, on the terms that row records: the derivation of the RECORD
stays in ``_derive``, and the projection PAST it lives here.  Nothing in
either function changed in the move; the docstrings carry their history.

Placed between :mod:`._derive` and :mod:`._searches` in the package's one-way
chain: it imports the derivation's values and producer and nothing above it,
and its two callers -- :func:`~._views.projected_paychecks` and
:meth:`~._calendar.PayCalendar.span_containing` -- sit above it.  Pure, on
the package's standing terms: no session, no Flask, no clock.
"""

from collections.abc import Iterable
from datetime import date, timedelta

from app.services.pay_rhythm import Rhythm

from ._derive import DerivedPeriod
from ._eras import PayCalendarError, projected_payday
from ._grid import cadence_steps_to


def covering_projection(
    candidates: "Iterable[DerivedPeriod]", day: date,
) -> DerivedPeriod:
    """Return the candidate projection covering *day*, refusing when none does.

    **A function of its own so :func:`project_period_after` states the RULE and
    this states the REFUSAL**, which is fifteen lines of it.  *An earlier form
    claimed the split was what let the probe be graded before the shift
    shipped; an adversarial review of ``C14-c`` refuted that from the suite --
    what grades the probe is a case that STATES a displacing convention and
    drives the REAL* :func:`project_period_after`, *reaching this through its
    own caller.  It substituted the producer until ``C14-e-3`` shipped one.*

    Consumed LAZILY, so the arithmetic estimate costs the one candidate it
    always did -- a property of the ORDER its caller offers them in, not of
    this function, which is order-free for correctness.  The estimate is the
    answer on EVERY call today and stays the answer on all but a fraction of a
    percent once the convention is live: over the 26,427 days the projection
    must answer for past the production owner's real horizon -- last recorded
    payday 2028-08-10, cadence 14, out to
    :data:`~app.utils.dates.CALENDAR_DATE_MAX` -- it names the wrong period on
    **64 of them, 0.24%**, under ``prior`` and again under ``next`` (measured
    2026-09-05).  The horizon and cadence are the owner's real ones; the
    CONVENTION is not -- every row is seeded ``none`` and nobody has answered
    (**R-PC56**) -- so the two conventions bracket the figure.

    **The containment test is the PERIOD's own** (:meth:`DerivedPeriod.covers`,
    **R-PC31**) rather than a comparison written here, the discipline
    :func:`~._searches.containing_index` keeps: one rule spelled two ways is how
    ledger row **P6** came to have six copies that disagreed.  While the
    collision floor holds the candidates TILE, so at most one matches and their
    order cannot change the answer; below it two can overlap, the state the
    refusal exists for.

    **The refusal is a REPORTED hole, and ruling R-PC59 is where it is
    reported.**  Nothing covers *day* only when two nominal paydays were
    displaced onto one day or past each other, which needs a cadence no longer
    than the longest run of consecutive closed days.
    ``pay_schedule_service.reject_shift_on_short_cadence`` refuses that pair at
    the column's one write door -- but a write-time refusal cannot see a row a
    LATER holiday-set change made illegal, and nothing reconciles that table
    (ledger row **N-493**).  Raising names the schedule; the alternative is
    answering with a period that does not contain the day it was asked about.

    Args:
        candidates: The projections to choose between, NON-EMPTY.  The one
            caller always offers three, so an empty set has no producer and
            gets no message of its own -- an arm no caller can reach is one a
            test could only grade against an impossible state.
        day: The calendar day to place.

    Returns:
        The candidate whose span covers *day*.

    Raises:
        PayCalendarError: No candidate covers *day*.
    """
    tried = []
    for candidate in candidates:
        if candidate.covers(day):
            return candidate
        tried.append(candidate)
    spans = ", ".join(
        f"[{c.start_date.isoformat()}..{c.end_date.isoformat()}]"
        for c in tried
    )
    raise PayCalendarError(
        f"no projected pay period covers {day.isoformat()}: the candidates "
        f"were {spans}.  A projection is the nominal rhythm displaced onto a "
        f"business day, and the candidates either side of the arithmetic "
        f"estimate cover every day the rhythm can reach while no payday moves "
        f"by a whole cadence.  Reaching here means two nominal paydays "
        f"displaced onto one day or past each other, which needs a cadence no "
        f"longer than the longest run of consecutive closed days -- "
        f"pay_schedule_service.reject_shift_on_short_cadence refuses that pair "
        f"at the write door, and ruling R-PC59 records that a write-time "
        f"refusal cannot see a stored row a later holiday-set change made "
        f"illegal."
    )


def project_period_after(
    periods: "tuple[DerivedPeriod, ...]", rhythm: Rhythm, day: date,
) -> DerivedPeriod:
    """Return the projected period covering *day*, past the last saved payday.

    **The forward continuation of :func:`~._derive.derive_periods`' rule** --
    beside which it lived until ``C17-b-1`` moved it here -- and it IS that
    rule: a projected period runs from its own payday to the day before the
    next, exactly as a saved one does, and both paydays come from
    :func:`~._derive.projected_payday`.  Two consumers ask --
    :meth:`~._calendar.PayCalendar.span_containing`, which must answer for any
    day, and :func:`~._views.axis_window`, which walks the projection to a
    horizon -- and a second implementation of "where does the next paycheck
    land" is the class ledger row **P6** counted seven of.

    Projection is ARITHMETIC rather than a walk: the period covering *day* is
    about the ``n``-th after the last saved payday, ``n`` being the whole
    cadences between them (:func:`~._grid.cadence_steps_to`, since
    ``balance:X-bh-2``), so cost does not grow with how far ahead a caller
    asks.  That property was priced when :func:`~._views.projected_paychecks`
    stepped to its answer instead of jumping -- **32 ms against 0.1 ms** for
    one render, ``balance:X-bh-1``, measured 2026-08-30 -- and a walk here
    would reintroduce it one layer down.

    **"About" is plan step C14-c's word, and the PROBE is why the jump survives
    a payday that moves** (**R-PC57**: the containment probe tolerates a moved
    boundary).  The division is exact only while every payday sits on the
    arithmetic grid; since ``C14-e-3`` displaces one, the count can name the
    period next door, so the estimate is checked against its NEIGHBOURS and
    whichever candidate covers *day* wins (:func:`covering_projection`).

    **Why ONE neighbour either side is enough, and it is a theorem rather than
    a margin.**  A displacement is bounded by the longest run of consecutive
    closed days, and
    :func:`~app.utils.business_days.shortest_collision_free_cadence` is that
    run PLUS ONE -- the floor
    ``pay_schedule_service.reject_shift_on_short_cadence`` holds a displacing
    convention to (**R-PC59**).  So no payday moves a whole cadence, which puts
    the true index within one of the estimate; a candidate two out would need a
    displacement of a full cadence or more.  Swept in
    ``tests/test_services/test_pay_calendar_derivation.py`` over both
    conventions, four anchors, every cadence from the floor to a year, and
    steps either side of the anchor -- driving THIS function, not a second copy
    of its arithmetic.

    **The theorem has a SECOND premise, and naming it is an adversarial
    review's finding.**  It is not enough that no payday moves a whole cadence:
    the count below and the candidates beside it must be measured from the SAME
    anchor, which they are, both reading ``last.start_date``.  Break that and
    the window is too narrow -- and the natural repair for ledger row **N-495**
    is what breaks it.  Anchoring the projection on a NOMINAL payday while the
    estimate still counts from the recorded one adds an offset the size of the
    displacement, making the premise
    ``cadence >= longest_closed_run + |anchor offset| + 1``.  Measured at the
    floor: recorded anchor 2030-01-01, cadence 4, ``prior``, projected from the
    nominal 2029-12-29, puts 2030-01-04's true index TWO above the estimate --
    and the consequence is :func:`covering_projection` REFUSING an ordinary
    day.  ``C14-e-3`` did NOT re-anchor, for the reason
    :func:`~._derive.projected_payday` gives, and no step may without widening this
    window.

    **The literal ``1`` was left as ``C14-e``'s design question, and
    ``C14-e-3`` answers it: it STAYS, and what holds it up is the WRITE DOOR --
    which is** :func:`covering_projection`'s **reported hole below, stated once
    there and not restated here.**  An adversarial review of ``C14-c`` read the
    ``1`` and the collision floor as one value with two homes, the general form
    being ``(longest_closed_run - 1) // cadence_days + 1``, and named the
    obstacle to deriving it as the import set of ``_derive``, where this
    function then lived -- an obstacle ``C14-e-3`` removed there by importing
    :mod:`app.utils.business_days` for the displacement; this module takes no
    such import, and deriving it would still buy nothing a caller can reach.
    *A first draft attributed the guarantee to* :func:`~._derive.derive_periods` *and an
    adversarial review measured that false: it refuses a repeated RECORDED
    payday, which* ``uq_pay_periods_user_start`` *already makes impossible, and
    never weighs a cadence against the closed run.  A sub-floor pairing passes
    it and derives a REVERSED period -- ledger row **PC-505**.*

    **The precondition below is NOT structural, and a first cut of this step
    filtered on the belief that it was.**  Candidates at step ``0`` and below
    were dropped, reasoning that *day* falls past the last period's end so
    nothing earlier could win -- and the suite refused it:
    :meth:`~._calendar.PayCalendar.span_containing` reaches here for a day
    INSIDE an unsaved interior candidate, whose materialisation filter leaves
    the total answer here.  *day* is then below the anchor, the count is
    NEGATIVE, and the rhythm is read backwards -- exactly what shipped before
    this step, and left as it was.  That answer assumes the recorded paydays
    between are ON cadence, which **R-PC47** says they need not be; reported
    rather than repaired, since repairing it moves an answer and this step
    moves none.

    Every projected period reports ``end_is_projected`` ``True`` -- the end
    comes from the projection rather than a recorded payday -- and carries
    ``period_id = None``, so a caller needing a foreign key target cannot
    mistake one for a saved row.

    Args:
        periods: The owner's SAVED periods, ``start_date`` ascending and
            non-empty.  Only the last one is read.
        rhythm: The owner's cadence and payday convention
            (:class:`~app.services.pay_rhythm.Rhythm`).  The cadence is an ``int`` rather than
            ``int | None``: a calendar holding a period cannot have been
            constructed without a cadence (:func:`~._derive.derive_periods` refuses that
            pair), and every caller reaches here only after establishing that
            *periods* is non-empty.
        day: The calendar day to place.  NORMALLY past the last saved period's
            ``end_date``, which is what both callers test for -- but that is
            not a guarantee, and this entry said it was until an adversarial
            review of ``C14-c`` read it against the paragraph above.  Believing
            the old wording is how the ``steps >= 1`` filter this step already
            had to remove gets re-added.

    Returns:
        The projected :class:`DerivedPeriod`, carrying ``period_id = None`` and
        a ``period_index`` continuing the saved sequence.

    Raises:
        PayCalendarError: No candidate covers *day* -- the state
            :func:`covering_projection` states in full.  No schedule the write
            door admits can reach it TODAY; ledger row **N-493** is the
            reported hole.
    """
    last = periods[-1]
    estimate = cadence_steps_to(last.start_date, rhythm.cadence_days, day)
    return covering_projection(
        (
            DerivedPeriod(
                period_id=None,
                period_index=last.period_index + steps,
                start_date=projected_payday(
                    last.start_date, rhythm, steps,
                ),
                end_date=projected_payday(
                    last.start_date, rhythm, steps + 1,
                ) - timedelta(days=1),
                end_is_projected=True,
            )
            # The estimate FIRST: it is the answer whenever no payday between
            # the horizon and *day* was displaced across a boundary, and the
            # generator is consumed lazily, so the common call builds one
            # candidate as it always did.
            for steps in (estimate, estimate - 1, estimate + 1)
        ),
        day,
    )
