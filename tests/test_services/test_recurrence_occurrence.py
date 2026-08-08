"""The forward occurrence engine (plan step R3).

``app.services.recurrence.occurrences`` walks a rule's cadence forward and
``place`` carries each occurrence onto a pay period.  Nothing in the
application reads either yet -- plan step R4 cuts ``match_periods``' readers
over -- so this file is the whole of R3's proof.

**The parallel run is the gate.**  :class:`TestTheParallelRun` drives the NEW
engine through ``tests/oracles/recurrence_baseline.py``'s own 428 shapes and
both of its schedules, and asserts it reproduces
``tests/oracles/recurrence_baseline.txt`` -- the snapshot plan step R1 froze
from the CURRENT engine.  416 shapes agree exactly.  Twelve move, in two
classes, and both were ruled BEFORE this step was built:

* **Four ``bounds.*`` shapes drop exactly one row each** (ruling R-R6, plan
  defect D5).  ``match_periods`` bounds PERIODS -- ``end_date`` is tested
  against a period's START and ``start_date`` against its END -- so it
  generates a row whose own occurrence date lies outside the window the user
  set.  Every dropped row is such a row, named in :data:`_BOUND_DIVERGENCES`
  and cross-checked against the snapshot's own ``due=`` column, so the test
  diffs against a prediction rather than against whatever the engine answers.
* **Eight ``long_cadence.*`` shapes gain rows** (plan defect D3).  The old
  matcher reads only the months of a period's two ENDPOINTS, so at a 90-day
  cadence a monthly rule found 13 of its 36 occurrences (returning one period
  TWICE), a quarterly rule 1 of its 12, and a semi-annual rule 1 of its 6.
  These are checked against an INDEPENDENT day-by-day sweep
  (:func:`_day_sweep_occurrences`) plus a linear-scan placer, rather than
  against the engine's own answer, because "the new number is bigger" is not
  a proof that it is right.

**Five of those eight shapes did not exist before this step.**  The R1 long-
cadence builder covered ``MONTHLY`` and ``QUARTERLY`` only, so ``MONTHLY_FIRST``,
``SEMI_ANNUAL`` and ``ANNUAL`` were unmeasured at any cadence but the
developer's -- and ``MONTHLY_FIRST`` turned out to be the one that mattered
(:meth:`TestTheParallelRun.test_monthly_first_defers_several_months_onto_one_paycheck`).
Adding them appended lines to the snapshot and moved none: what the OLD engine
answers for a shape nobody had asked about is new coverage, not a behaviour
change.  ``ANNUAL`` and ``long_cadence.every_period`` are the controls -- both
agree at 90 days.

**The plan's R3 entry said to expect "exactly one class" of divergence.  It is
two**, and ruling R-R6 already said so; the entry was written before R-R6 and
was not re-pointed.  The four ``bounds.*`` blocks move exactly as R-R6's table
predicts, figure for figure.

**No existing line is re-frozen here.**  R3 changes no reader, so nothing it
does may MOVE a committed line; ``SHEKEL_UPDATE_RECURRENCE_BASELINE=1``
belongs to R4's commit, where the cutover makes the divergence real.

The rest of the file exercises the engine directly, at exact dates against
hand-built schedules -- no database, no clock -- including the three things
``resolve`` cannot yet produce and only a hand-built
:class:`~app.services.recurrence.ResolvedRecurrence` can reach: the ``WEEK``
unit, ``max_occurrences``, and a business-day shift.
"""

import calendar as calendar_module
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.enums import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.services.recurrence import (
    PeriodCalendar,
    RecurrenceGenerationError,
    RecurrenceScheduleError,
    ResolvedRecurrence,
    SchedulePeriod,
    occurrence_placements,
    occurrences,
    place,
    resolve,
)
# Imported as a MODULE so the firing controls below can patch the functions the
# composition resolves at CALL time.  Patching this file's own imported names
# would leave ``occurrence_placements`` calling the real ones, making a control
# that passes prove nothing -- the same reasoning
# ``tests/oracles/recurrence_baseline.py`` records for the old engine.
from app.services.recurrence import _occurrence
from tests.oracles import recurrence_baseline
from tests.test_services.test_recurrence_resolution import build_calendar

#: The committed R1 snapshot the parallel run is measured against.
BASELINE_PATH = Path(recurrence_baseline.__file__).with_suffix(".txt")

#: The four ``bounds.*`` shapes ruling R-R6 predicted would move, each mapped
#: to the ONE ``(period_index, occurrence date)`` it drops.  Occurrence-bounded
#: generation refuses a row dated outside its own rule's window; every entry
#: here is such a row, and the dates are read off the committed blob's ``due=``
#: column, which for these day-15 rules IS the occurrence date.
_BOUND_DIVERGENCES: dict[str, tuple[int, date]] = {
    # start_date 2024-06-16, on period 11's last day: the June occurrence is
    # the 15th, one day BEFORE the rule begins.
    "bounds.start.on_period_end": (11, date(2024, 6, 15)),
    # end_date 2025-06-05, mid-period: the June occurrence is the 15th, ten
    # days AFTER the rule ends.
    "bounds.end.midperiod": (37, date(2025, 6, 15)),
    # end_date 2025-06-02, on period 37's first day: same June occurrence.
    "bounds.end.on_period_start": (37, date(2025, 6, 15)),
    # start 2024-06-05 + end 2025-06-05: only the end side moves.
    "bounds.window.both": (37, date(2025, 6, 15)),
}

#: The eight ``long_cadence.*`` shapes that move, each mapped to the calendar
#: cycle it names as ``(base_month, month_step, nominal_day, placement)`` --
#: read off the shape's own label, not off the engine.
#: :func:`_day_sweep_occurrences` rebuilds the occurrence list from these by
#: inspecting every calendar day, and the placement picks which independent
#: linear placer carries them onto a paycheck.
_LONG_CADENCE_DIVERGENCES: dict[
    str, tuple[int, int, int, PeriodPlacementEnum],
] = {
    "long_cadence.monthly.dom01": (1, 1, 1, PeriodPlacementEnum.CONTAINING_DATE),
    "long_cadence.monthly.dom15": (1, 1, 15, PeriodPlacementEnum.CONTAINING_DATE),
    "long_cadence.monthly.dom31": (1, 1, 31, PeriodPlacementEnum.CONTAINING_DATE),
    "long_cadence.quarterly.moy01.dom01": (
        1, 3, 1, PeriodPlacementEnum.CONTAINING_DATE,
    ),
    "long_cadence.quarterly.moy01.dom15": (
        1, 3, 15, PeriodPlacementEnum.CONTAINING_DATE,
    ),
    "long_cadence.semi_annual.moy01.dom01": (
        1, 6, 1, PeriodPlacementEnum.CONTAINING_DATE,
    ),
    "long_cadence.semi_annual.moy01.dom15": (
        1, 6, 15, PeriodPlacementEnum.CONTAINING_DATE,
    ),
    # The one that DEFERS rather than contains: its occurrences are the 1st of
    # every month and each is carried onto the next paycheck, so at a 90-day
    # cadence three months land on one.  See
    # ``TestMonthlyFirstDefersOntoOnePaycheck``.
    "long_cadence.monthly_first": (
        1, 1, 1, PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
    ),
}

#: Every shape the new engine is expected to answer differently on.
_EXPECTED_DIVERGENCES = frozenset(_BOUND_DIVERGENCES) | frozenset(
    _LONG_CADENCE_DIVERGENCES,
)

#: The occurrences the schedule cannot host, per shape, and the ONLY ones.
#:
#: **Declared because the comparison is otherwise not total.**  The committed
#: snapshot records PERIODS, so an occurrence that places nowhere has no line
#: in it and simply vanishes from an index-to-index diff -- a neutral review
#: demonstrated a mutant that emits one unplaceable occurrence per rule and
#: left every test in this file green.  Both entries are real and both are
#: ``PERIOD_STARTING_ON_OR_AFTER``: an occurrence dated after the last PAYDAY
#: has no paycheck to defer onto, even when it is still inside the schedule's
#: covered span.  The biweekly schedule's last payday is 2026-12-28 against a
#: horizon of 2027-01-10, so January 2027's occurrence is emitted and unplaced.
_EXPECTED_UNPLACED: dict[str, list[date]] = {
    "monthly_first": [date(2027, 1, 1)],
    "long_cadence.monthly_first": [
        date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1),
    ],
}

#: The owner every calendar and resolved value in this file names.
_USER_ID = 1


def resolved_value(
    *,
    unit: RecurrenceUnitEnum,
    anchor_date: date,
    interval_n: int = 1,
    offset_periods: int = 0,
    placement: PeriodPlacementEnum = PeriodPlacementEnum.CONTAINING_DATE,
    shift: BusinessDayShiftEnum = BusinessDayShiftEnum.NONE,
    end_date: date | None = None,
    max_occurrences: int | None = None,
    nominal_day: int | None = None,
) -> ResolvedRecurrence:
    """Return a two-axis value stated directly, bypassing ``resolve``.

    The engine consumes :class:`~app.services.recurrence.ResolvedRecurrence`,
    and three of its fields have no producer yet -- the ``WEEK`` unit,
    ``max_occurrences`` and the business-day shift all wait on plan step R8.
    Stating the value here is what lets those be tested before their author
    exists; the shapes ``resolve`` CAN produce are covered by the parallel run
    against all 423 of them.

    Args:
        unit: The cadence unit.
        anchor_date: The first occurrence (or, for ``PERIOD``, the bound).
        interval_n: Units between occurrences.
        offset_periods: Phase within the ``PERIOD`` cycle.
        placement: How an occurrence maps onto a pay period.
        shift: Weekend/holiday adjustment.
        end_date: The closing date bound.
        max_occurrences: The count bound.
        nominal_day: The day the rule means when the anchor month clamped it.

    Returns:
        The :class:`~app.services.recurrence.ResolvedRecurrence`.
    """
    return ResolvedRecurrence(
        offset_periods=offset_periods,
        interval_n=interval_n,
        unit=unit,
        anchor_date=anchor_date,
        placement=placement,
        shift=shift,
        end_date=end_date,
        max_occurrences=max_occurrences,
        nominal_day=nominal_day,
    )


def dates_through(
    resolved: ResolvedRecurrence, calendar: PeriodCalendar, through: date,
) -> list[date]:
    """Return the rule's occurrence dates through *through*.

    Args:
        resolved: The two-axis value.
        calendar: The owner's schedule.
        through: The last day to generate through.

    Returns:
        The occurrence dates, ascending.
    """
    return list(occurrences(resolved, calendar, through=through))


def placed_indices(
    resolved: ResolvedRecurrence,
    calendar: PeriodCalendar,
    **kwargs,
) -> list[int]:
    """Return the period indices the rule's placed occurrences land in.

    Args:
        resolved: The two-axis value.
        calendar: The owner's schedule.
        **kwargs: Forwarded to
            :func:`~app.services.recurrence.occurrence_placements`.

    Returns:
        One index per PLACED occurrence, ascending, duplicates kept.
    """
    return [
        placement.period.period_index
        for placement in occurrence_placements(resolved, calendar, **kwargs)
        if placement.period is not None
    ]


def _day_sweep_occurrences(
    first_day: date, last_day: date, base_month: int, month_step: int,
    nominal_day: int,
) -> list[date]:
    """Return a calendar cycle's occurrences by INSPECTING every day.

    The independent oracle for the long-cadence divergences.  Deliberately a
    different algorithm from the engine's: the engine STRIDES over absolute
    month ordinals, this filters the calendar one day at a time.  Two
    implementations of one idea that agree are evidence; the engine agreeing
    with itself is not.

    Args:
        first_day: The first day of the span to sweep.
        last_day: The last day of the span to sweep.
        base_month: The cycle's start month, 1-12.
        month_step: Months between occurrences.
        nominal_day: The day of the month the rule means, before clamping.

    Returns:
        The occurrence dates in the span, ascending.
    """
    found = []
    day = first_day
    while day <= last_day:
        last_of_month = calendar_module.monthrange(day.year, day.month)[1]
        if (
            day.day == min(nominal_day, last_of_month)
            and (day.month - base_month) % month_step == 0
        ):
            found.append(day)
        day += timedelta(days=1)
    return found


def _linear_place(
    day: date, periods: list, placement: PeriodPlacementEnum,
) -> int | None:
    """Return the index of the period *day* places into, by linear scan.

    The independent oracle for placement: no bisect, no calendar object, and
    the two rules written out separately rather than shared with the engine.

    Args:
        day: The date to place.
        periods: Pay periods in ``period_index`` order.
        placement: Which placement rule to apply.

    Returns:
        The period's ``period_index``, or ``None`` when none qualifies.
    """
    if placement is PeriodPlacementEnum.CONTAINING_DATE:
        for period in periods:
            if period.start_date <= day <= period.end_date:
                return period.period_index
        return None
    for period in periods:
        if period.start_date >= day:
            return period.period_index
    return None


def _baseline_schedules() -> tuple[list, list]:
    """Return the baseline's two schedules, biweekly first.

    Returns:
        ``(biweekly periods, long-cadence periods)``.
    """
    return (
        recurrence_baseline.build_schedule(
            recurrence_baseline.SCHEDULE_START,
            recurrence_baseline.SCHEDULE_CADENCE_DAYS,
            recurrence_baseline.SCHEDULE_PERIOD_COUNT,
        ),
        recurrence_baseline.build_schedule(
            recurrence_baseline.SCHEDULE_START,
            recurrence_baseline.LONG_CADENCE_DAYS,
            recurrence_baseline.LONG_CADENCE_PERIOD_COUNT,
        ),
    )


def _new_engine_placements() -> dict[str, list[tuple[date, int | None]]]:
    """Drive the NEW engine through every baseline shape.

    Requires an app context: ``resolve`` reads ``ref_cache`` for the pattern
    id.  Each shape is resolved against the SAME schedule the R1 capture used,
    then generated and placed with the engine's default window (the schedule's
    horizon).

    **Every EMITTED occurrence is returned, placed or not.**  Filtering the
    unplaced ones out here is what made an earlier version of this harness
    blind to them; see :data:`_EXPECTED_UNPLACED`.

    Returns:
        ``{shape label: [(occurrence, period_index or None), ...]}``,
        duplicates kept and in occurrence order.
    """
    biweekly, long_cadence = _baseline_schedules()
    calendars = {
        False: recurrence_baseline.build_shape_calendar(biweekly),
        True: recurrence_baseline.build_shape_calendar(long_cadence),
    }
    answers = {}
    for shape in recurrence_baseline.build_shapes():
        calendar = calendars[shape.long_cadence]
        resolved = resolve(
            recurrence_baseline.build_shape_spec(shape), calendar,
        )
        answers[shape.label] = [
            (
                item.occurrence,
                None if item.period is None else item.period.period_index,
            )
            for item in occurrence_placements(resolved, calendar)
        ]
    return answers


def _new_engine_indices() -> dict[str, list[int]]:
    """Return only the PLACED period indices, for the snapshot comparison.

    Returns:
        ``{shape label: [period_index, ...]}``, directly comparable to
        :func:`tests.oracles.recurrence_baseline.parse_baseline`'s output.
    """
    return {
        label: [index for _occurrence, index in pairs if index is not None]
        for label, pairs in _new_engine_placements().items()
    }


def _new_engine_unplaced() -> dict[str, list[date]]:
    """Return the occurrences the schedule could not host, per shape.

    Returns:
        ``{shape label: [occurrence, ...]}``, shapes with none omitted.
    """
    unplaced = {}
    for label, pairs in _new_engine_placements().items():
        dates = [occurrence for occurrence, index in pairs if index is None]
        if dates:
            unplaced[label] = dates
    return unplaced


def _committed_rows() -> dict[str, list[tuple[int, date]]]:
    """Return the committed R1 snapshot, parsed to ``(index, due date)`` rows.

    Returns:
        ``{shape label: [(period_index, due date), ...]}``.
    """
    return recurrence_baseline.parse_baseline_rows(
        BASELINE_PATH.read_text(encoding="utf-8"),
    )


def _committed_indices() -> dict[str, list[int]]:
    """Return the committed R1 snapshot, parsed to period indices.

    Returns:
        ``{shape label: [period_index, ...]}``.
    """
    return recurrence_baseline.parse_baseline(
        BASELINE_PATH.read_text(encoding="utf-8"),
    )


@pytest.mark.usefixtures("app")
class TestTheParallelRun:
    """The new engine against the frozen behaviour of the old one."""

    def test_every_shape_outside_the_declared_set_reproduces_the_snapshot(self):
        """414 of 423 shapes answer exactly what the committed blob records.

        The gate.  A failure here means the forward engine disagrees with the
        engine in production on a shape nobody ruled it should.
        """
        new = _new_engine_indices()
        committed = _committed_indices()

        assert set(new) == set(committed), (
            "the shape set moved between the snapshot and this run"
        )
        agreeing = sorted(set(committed) - _EXPECTED_DIVERGENCES)
        # 428 captured shapes less the 12 declared divergences.
        assert len(agreeing) == 416, f"{len(agreeing)} shapes outside the set"
        for label in agreeing:
            assert new[label] == committed[label], (
                f"{label}: forward engine answers {new[label]}, the committed "
                f"snapshot records {committed[label]}"
            )

    def test_no_occurrence_is_unplaceable_without_being_declared(self):
        """Every emitted occurrence either places or is named in advance.

        **This is what makes the comparison TOTAL**, and it was missing.  The
        committed snapshot records PERIODS, so an occurrence with nowhere to
        live has no line in it and disappears from an index-to-index diff: a
        neutral review built a mutant emitting one unplaceable occurrence per
        rule and left every other test in this file green.  Declaring the
        unplaced set exactly closes that -- a new one fails, and a declared
        one that stops happening fails too.
        """
        assert _new_engine_unplaced() == _EXPECTED_UNPLACED

    def test_the_declared_set_is_exactly_the_set_that_moves(self):
        """No shape moves unpredicted, and no prediction is stale.

        Both halves matter.  An unpredicted move is an unruled behaviour
        change; a stale prediction is a divergence that was fixed while the
        list still claims it, which would let a future regression hide inside
        an entry nobody re-checks.
        """
        new = _new_engine_indices()
        committed = _committed_indices()

        moved = {
            label for label in committed if new[label] != committed[label]
        }

        assert moved == set(_EXPECTED_DIVERGENCES), (
            f"unpredicted moves: {sorted(moved - _EXPECTED_DIVERGENCES)}; "
            f"stale predictions: {sorted(_EXPECTED_DIVERGENCES - moved)}"
        )

    def test_each_bound_divergence_drops_the_row_dated_outside_its_window(self):
        """The four ``bounds.*`` shapes lose exactly the row R-R6 named.

        Ruling R-R6, re-measured: ``match_periods`` admits a PERIOD that
        straddles the bound and then generates a row whose own occurrence date
        is outside it.  Each shape drops one such row and keeps every other,
        so the assertion is the committed list minus one named index -- not a
        count, and not the engine's own answer.
        """
        new = _new_engine_indices()
        committed = _committed_indices()

        for label, (dropped_index, _dropped_date) in _BOUND_DIVERGENCES.items():
            expected = list(committed[label])
            assert dropped_index in expected, (
                f"{label}: the snapshot does not contain idx={dropped_index}"
            )
            expected.remove(dropped_index)
            assert new[label] == expected, (
                f"{label}: expected the committed list minus idx="
                f"{dropped_index}, got {new[label]}"
            )

    def test_each_bound_divergence_dropped_a_row_outside_the_rules_window(self):
        """The dropped row's occurrence date really is outside the window.

        The other half of the ruling: the rows above are not merely different,
        they are rows the rule's own ``start_date`` / ``end_date`` excludes.

        **The date is read out of the committed snapshot, not trusted from
        :data:`_BOUND_DIVERGENCES`.**  An earlier version of this test checked
        only that the DECLARED date fell outside the window, which any
        fabricated out-of-window date satisfies.  Here the snapshot's own
        ``due=`` column at the dropped index supplies it -- and for these
        day-15 ``MONTHLY`` shapes the due date IS the occurrence date, because
        ``compute_due_date`` returns ``day_of_month`` in the matched period's
        month when no separate due day is set.
        """
        shapes = {
            shape.label: shape for shape in recurrence_baseline.build_shapes()
        }
        committed = _committed_rows()

        for label, (dropped_index, declared_date) in _BOUND_DIVERGENCES.items():
            shape = shapes[label]
            snapshot_dates = [
                due for index, due in committed[label] if index == dropped_index
            ]
            assert snapshot_dates == [declared_date], (
                f"{label}: the snapshot dates idx={dropped_index} as "
                f"{snapshot_dates}, but _BOUND_DIVERGENCES declares "
                f"{declared_date}"
            )
            outside_start = (
                shape.start_date is not None and declared_date < shape.start_date
            )
            outside_end = (
                shape.end_date is not None and declared_date > shape.end_date
            )
            assert outside_start or outside_end, (
                f"{label}: dropped occurrence {declared_date} lies INSIDE the "
                f"rule's window ({shape.start_date}..{shape.end_date}), so "
                f"dropping it is a regression, not defect D5's fix"
            )

    def test_each_long_cadence_divergence_matches_an_independent_day_sweep(self):
        """The gained rows are the occurrences a day-by-day sweep finds.

        Plan defect D3.  The expectation is rebuilt from the shape's own cycle
        by inspecting every calendar day in the schedule's span and placing
        each hit by linear scan -- two algorithms unrelated to the engine's
        stride-and-bisect, so agreement is evidence rather than tautology.
        """
        _biweekly, long_periods = _baseline_schedules()
        calendar = recurrence_baseline.build_shape_calendar(long_periods)
        placements = _new_engine_placements()
        first_day = long_periods[0].start_date
        last_day = calendar.horizon()

        for label, cycle in _LONG_CADENCE_DIVERGENCES.items():
            base_month, month_step, nominal_day, placement = cycle
            swept = _day_sweep_occurrences(
                first_day, last_day, base_month, month_step, nominal_day,
            )
            expected = [
                (day, _linear_place(day, long_periods, placement))
                for day in swept
            ]
            assert placements[label] == expected, (
                f"{label}: forward engine answers {placements[label]}, the "
                f"independent day sweep finds {expected}"
            )

    def test_the_long_cadence_monthly_shapes_stop_dropping_months(self):
        """D3 named the loss in months; this asserts the months came back.

        The 90-day schedule spans 2024-01-01..2026-12-15.  A monthly day-1
        rule occurs in all 36 of its months; the committed snapshot records 13
        matched periods, one of them TWICE -- 12 distinct paychecks, which is
        what a period-scanning matcher can find at that cadence.
        """
        new = _new_engine_indices()
        committed = _committed_indices()

        # 2024-01 through 2026-12 inclusive is 3 * 12 = 36 months.
        assert len(new["long_cadence.monthly.dom01"]) == 36
        assert len(committed["long_cadence.monthly.dom01"]) == 13
        # The old answer's duplicate is the IntegrityError D3 predicts: a
        # period named twice is two rows in one (template, period, scenario).
        assert committed["long_cadence.monthly.dom01"][:2] == [0, 0]
        # Quarterly starting January: 2024/2025/2026 x Jan/Apr/Jul/Oct = 12.
        assert len(new["long_cadence.quarterly.moy01.dom01"]) == 12
        assert len(committed["long_cadence.quarterly.moy01.dom01"]) == 1
        # Semi-annual starting January: 2024/2025/2026 x Jan/Jul = 6.  The old
        # matcher finds ONE of the six -- a worse loss than quarterly's, and
        # invisible until this shape was added to the oracle.
        assert len(new["long_cadence.semi_annual.moy01.dom01"]) == 6
        assert len(committed["long_cadence.semi_annual.moy01.dom01"]) == 1
        # The control: a pay-period-space matcher reads no months at all, so
        # it must not move at any cadence.
        assert (
            new["long_cadence.every_period"]
            == committed["long_cadence.every_period"] == list(range(12))
        )

    def test_monthly_first_defers_several_months_onto_one_paycheck(self):
        """A month with no payday still owes; its bill defers to the next one.

        The behaviour both neutral reviews of plan step R3 surfaced, pinned
        here so it is CHOSEN rather than discovered.  ``Monthly First``
        occurrences are the 1st of every month and the placement carries each
        onto the first paycheck on or after it, so at a 90-day cadence several
        months land on one paycheck.  The old matcher walked PAYCHECKS instead
        and emitted one row each, which silently dropped 24 of the 36 months a
        monthly bill is owed for -- defect D3 reaching a pattern D3 never
        named.

        **The rows stay separate; only the grid sums them** (developer ruling,
        2026-08-07).  Two months of rent funded by one paycheck are two
        obligations: summing at generation would lose which month is unpaid,
        break an amount that changes mid-group, and put one row in front of
        two events.  ``idx_transactions_template_period_scenario`` cannot hold
        them today -- it is UNIQUE over ``(template, period, scenario)`` -- and
        re-keying it onto the occurrence is plan step R5's work, in the
        migration that renames ``due_date`` to ``occurs_on``.
        """
        _biweekly, long_periods = _baseline_schedules()
        placements = _new_engine_placements()["long_cadence.monthly_first"]
        committed = _committed_indices()["long_cadence.monthly_first"]

        # 36 months owed over 2024-01..2026-12; the last three have no paycheck
        # left to defer onto (the final payday is 2026-09-17).
        assert len(placements) == 36
        assert sum(1 for _o, index in placements if index is None) == 3
        # The old matcher answered once per paycheck: 12 rows for 36 months.
        assert committed == list(range(12))
        # February's and March's bills both fund from the 2026-03-31 paycheck,
        # which opens period 1 -- neither month contains a payday of its own.
        assert placements[1] == (date(2024, 2, 1), 1)
        assert placements[2] == (date(2024, 3, 1), 1)
        assert long_periods[1].start_date == date(2024, 3, 31)

    def test_a_calendar_units_first_occurrence_is_its_own_anchor(self):
        """The resolver's anchor and the engine's first occurrence agree.

        The seam between the two halves of the model.  ``resolve`` derives the
        anchor by one month-ordinal walk and the engine re-walks from it; if
        they clamped differently, every rule would fire one day off in its
        first month and nothing else would say so.
        """
        biweekly, long_cadence = _baseline_schedules()
        calendars = {
            False: recurrence_baseline.build_shape_calendar(biweekly),
            True: recurrence_baseline.build_shape_calendar(long_cadence),
        }
        checked = 0
        skipped = []
        for shape in recurrence_baseline.build_shapes():
            calendar = calendars[shape.long_cadence]
            resolved = resolve(
                recurrence_baseline.build_shape_spec(shape), calendar,
            )
            if resolved.unit is RecurrenceUnitEnum.PERIOD:
                continue
            emitted = occurrence_placements(resolved, calendar)
            if not emitted:
                # Only ``bounds.window.inverted``: its end date precedes its
                # anchor, so the rule fires nowhere.  Counted, not skipped
                # silently -- a threshold plus a silent skip lets a shape that
                # quietly stops emitting hide.
                skipped.append(shape.label)
                continue
            assert emitted[0].occurrence == resolved.anchor_date, shape.label
            checked += 1
        assert skipped == ["bounds.window.inverted"], skipped
        # 428 captured shapes less the 38 pay-period-space ones, less the one
        # inverted-window shape that fires nowhere.
        assert checked == 389, f"{checked} calendar-unit shapes checked"

    def test_a_period_units_first_occurrence_is_a_payday(self):
        """A pay-period-space rule fires on paydays, not on its bound.

        The asymmetry the module docstring states: for the ``PERIOD`` unit the
        anchor is a BOUND (ruling R-R8) and the occurrence is the qualifying
        paycheck's own opening day.  Asserted over every period-space shape so
        a future change to the anchor cannot quietly make the emitted date
        something else.
        """
        biweekly, long_cadence = _baseline_schedules()
        starts = {
            False: {period.start_date for period in biweekly},
            True: {period.start_date for period in long_cadence},
        }
        calendars = {
            False: recurrence_baseline.build_shape_calendar(biweekly),
            True: recurrence_baseline.build_shape_calendar(long_cadence),
        }
        checked = 0
        for shape in recurrence_baseline.build_shapes():
            calendar = calendars[shape.long_cadence]
            resolved = resolve(
                recurrence_baseline.build_shape_spec(shape), calendar,
            )
            if resolved.unit is not RecurrenceUnitEnum.PERIOD:
                continue
            for placement in occurrence_placements(resolved, calendar):
                assert placement.occurrence in starts[shape.long_cadence], (
                    f"{shape.label}: {placement.occurrence} is not a payday"
                )
                assert placement.period is not None
                assert placement.occurrence == placement.period.start_date
            checked += 1
        # 36 ``every_n_periods`` shapes (intervals 1-8 x every legal phase)
        # plus ``every_period`` and its long-cadence twin = 38.  ``Monthly
        # First`` is NOT period-space: it resolves to the MONTH unit.
        assert checked == 38, f"{checked} period-space shapes"


@pytest.mark.usefixtures("app")
class TestTheParallelRunFiringControls:
    """The gate must FAIL when the engine changes -- shown, not asserted.

    Verification standard: "every guard gets a negative control that is shown
    to fire", and "ask of every harness: can it SEE the code under test?"
    Both controls patch inside ``app.services.recurrence._occurrence``, which
    is the only patch target that proves the composition resolves its parts at
    CALL time rather than having bound them at import.
    """

    def test_a_shifted_placement_turns_the_gate_red(self, monkeypatch):
        """Placing every occurrence one period later FAILS the snapshot test.

        Runs the gate's own assertion under the patch rather than comparing
        two captures: "the answers moved" proves only that the harness is
        sighted, and a perturbation confined to the 12 declared divergences
        would satisfy it while the gate stayed green.
        """
        real_search = _occurrence._placement_search

        def shifted(calendar, placement):
            inner = real_search(calendar, placement)

            def one_later(day):
                placed = inner(day)
                if placed is None:
                    return None
                return calendar.period_starting_on_or_after(
                    placed.end_date + timedelta(days=1),
                )

            return one_later

        # ``_placement_search`` and not ``place``: the composition resolves the
        # search ONCE per call rather than going through the public wrapper, so
        # patching ``place`` leaves the harness blind.  This control caught
        # exactly that when the eager-refusal restructure introduced it.
        monkeypatch.setattr(_occurrence, "_placement_search", shifted)

        with pytest.raises(AssertionError):
            TestTheParallelRun().\
                test_every_shape_outside_the_declared_set_reproduces_the_snapshot()

    def test_a_dropped_occurrence_turns_the_gate_red(self, monkeypatch):
        """Dropping the first occurrence of every rule FAILS the snapshot test."""
        real_occurrences = _occurrence.occurrences

        def one_fewer(resolved, calendar, *, through):
            emitted = list(real_occurrences(resolved, calendar, through=through))
            return iter(emitted[1:])

        monkeypatch.setattr(_occurrence, "occurrences", one_fewer)

        with pytest.raises(AssertionError):
            TestTheParallelRun().\
                test_every_shape_outside_the_declared_set_reproduces_the_snapshot()

    def test_an_extra_unplaceable_occurrence_turns_the_gate_red(
        self, monkeypatch,
    ):
        """The mutant that used to pass everything now fails the totality test.

        A neutral review built exactly this: emit one occurrence per rule that
        no paycheck can host.  It moved no PLACED index, so every
        snapshot-comparison test stayed green while the engine invented an
        occurrence.  :meth:`TestTheParallelRun.test_no_occurrence_is_unplaceable_without_being_declared`
        is what closes it, and this is the control proving it fires.
        """
        real_occurrences = _occurrence.occurrences

        def one_extra(resolved, calendar, *, through):
            emitted = list(real_occurrences(resolved, calendar, through=through))
            # A day past every payday but inside the covered span: emitted, and
            # placeable by neither rule.
            return iter(emitted + [calendar.horizon()])

        monkeypatch.setattr(_occurrence, "occurrences", one_extra)

        with pytest.raises(AssertionError):
            TestTheParallelRun().\
                test_no_occurrence_is_unplaceable_without_being_declared()


@pytest.mark.usefixtures("app")
class TestThePeriodUnit:
    """Pay-period-space occurrences: paychecks, not calendar dates.

    The developer's own schedule shape throughout (first payday 2026-03-26,
    14-day cadence, 61 periods), so period 0 covers 2026-03-26..2026-04-08 and
    period 1 opens 2026-04-09.
    """

    def test_every_paycheck_fires_on_every_payday(self):
        """Interval 1 emits one occurrence per period, each on its payday."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, anchor_date=date(2026, 3, 26),
        )

        emitted = dates_through(value, calendar, calendar.horizon())

        assert len(emitted) == 61
        assert emitted[0] == date(2026, 3, 26)
        # 2026-03-26 + 14 days.
        assert emitted[1] == date(2026, 4, 9)
        assert placed_indices(value, calendar) == list(range(61))

    def test_a_mid_period_bound_fires_in_the_paycheck_it_falls_in(self):
        """A bound inside period 0 still bills period 0, on its payday.

        The loan case (plan step C9a): ``loan_recurrence_sync`` stamps the
        first contractual installment onto ``start_date``, and a loan whose
        first installment falls mid-period bills in THAT period, not the next.
        ``match_periods`` gets this right today (``p.end_date >= bound``) and
        the forward engine has to keep it -- so the anchor is a BOUND here and
        the occurrence is the paycheck's own opening day, never the bound.
        """
        calendar = build_calendar()
        # 2026-04-01 sits inside period 0 (2026-03-26..2026-04-08).
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, anchor_date=date(2026, 4, 1),
        )

        emitted = dates_through(value, calendar, calendar.horizon())

        assert emitted[0] == date(2026, 3, 26)
        assert placed_indices(value, calendar)[0] == 0
        assert len(emitted) == 61

    def test_a_bound_on_a_payday_starts_that_paycheck(self):
        """A bound equal to a payday opens on that paycheck, not the previous."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, anchor_date=date(2026, 4, 9),
        )

        emitted = dates_through(value, calendar, calendar.horizon())

        assert emitted[0] == date(2026, 4, 9)
        # 61 periods less the one that ended before the bound.
        assert len(emitted) == 60
        assert placed_indices(value, calendar)[0] == 1

    def test_a_phased_rule_fires_on_every_nth_paycheck(self):
        """Every 3 paychecks phased at 2 fires on indices 2, 5, 8, ..., 59."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD,
            # ``_phased_period_anchor`` puts the anchor on period 2's payday.
            anchor_date=date(2026, 3, 26) + timedelta(days=28),
            interval_n=3, offset_periods=2,
        )

        indices = placed_indices(value, calendar)

        # 2, 5, ... 59 -- (59 - 2) / 3 + 1 = 20 occurrences.
        assert indices == list(range(2, 61, 3))
        assert len(indices) == 20

    def test_placement_is_inert_under_the_period_unit(self):
        """Both placements answer identically for a pay-period-space rule.

        Every occurrence the unit emits is a period's own ``start_date``, and
        both rules carry such a date back to that same period.  The plan's
        section 3 claims otherwise; it reads the anchor as the emitted
        occurrence, which
        :meth:`TestThePeriodUnit.test_a_mid_period_bound_fires_in_the_paycheck_it_falls_in`
        is the decision not to do.
        """
        calendar = build_calendar()
        containing = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, anchor_date=date(2026, 4, 1),
            placement=PeriodPlacementEnum.CONTAINING_DATE,
        )
        on_or_after = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, anchor_date=date(2026, 4, 1),
            placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
        )

        # 61 periods, every one of them, under either reading.  Asserted as
        # a VALUE and not merely as agreement: two empty lists are equal too.
        assert placed_indices(containing, calendar) == list(range(61))
        assert placed_indices(on_or_after, calendar) == list(range(61))

    def test_a_bound_past_the_horizon_fires_nowhere(self):
        """No paycheck exists past the schedule, so nothing is emitted."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, anchor_date=date(2030, 1, 1),
        )

        assert dates_through(value, calendar, date(2035, 1, 1)) == []
        assert occurrence_placements(value, calendar) == ()


@pytest.mark.usefixtures("app")
class TestTheCalendarUnits:
    """MONTH and YEAR, including the two cadences the old enum could not name."""

    def test_monthly_walks_one_month_at_a_time(self):
        """Interval 1 MONTH fires on the anchor's day every month."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 4, 15),
        )

        assert dates_through(value, calendar, date(2026, 8, 31)) == [
            date(2026, 4, 15), date(2026, 5, 15), date(2026, 6, 15),
            date(2026, 7, 15), date(2026, 8, 15),
        ]

    def test_every_other_month_is_expressible(self):
        """Interval 2 MONTH -- one of the two cadences that had nowhere to live.

        The plan's root cause: Monthly / Quarterly / Semi-Annual / Annual are
        the same idea with the integer baked into the NAME, so "every other
        month" was inexpressible.  With the interval a value, it is one line.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 4, 15),
            interval_n=2,
        )

        assert dates_through(value, calendar, date(2026, 8, 31)) == [
            date(2026, 4, 15), date(2026, 6, 15), date(2026, 8, 15),
        ]

    def test_quarterly_is_the_same_walk_at_interval_three(self):
        """Interval 3 MONTH reproduces the Quarterly pattern."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 4, 15),
            interval_n=3,
        )

        assert dates_through(value, calendar, date(2027, 1, 31)) == [
            date(2026, 4, 15), date(2026, 7, 15), date(2026, 10, 15),
            date(2027, 1, 15),
        ]

    def test_annual_walks_a_year_at_a_time(self):
        """Interval 1 YEAR is the twelve-month stride."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.YEAR, anchor_date=date(2026, 4, 15),
        )

        assert dates_through(value, calendar, date(2029, 1, 1)) == [
            date(2026, 4, 15), date(2027, 4, 15), date(2028, 4, 15),
        ]

    def test_every_two_years_is_expressible(self):
        """Interval 2 YEAR -- the second cadence the old enum could not name."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.YEAR, anchor_date=date(2026, 4, 15),
            interval_n=2,
        )

        assert dates_through(value, calendar, date(2031, 1, 1)) == [
            date(2026, 4, 15), date(2028, 4, 15), date(2030, 4, 15),
        ]

    def test_a_calendar_occurrence_lands_in_the_period_containing_it(self):
        """CONTAINING_DATE puts the row in the paycheck the date falls inside."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 4, 15),
        )

        placements = occurrence_placements(value, calendar)

        # 2026-04-15 falls inside period 1 (2026-04-09..2026-04-22).
        assert placements[0].occurrence == date(2026, 4, 15)
        assert placements[0].period.period_index == 1
        assert placements[0].period.start_date == date(2026, 4, 9)


@pytest.mark.usefixtures("app")
class TestTheWeekUnit:
    """The unit no pattern resolves to yet; plan step R8 is its first author."""

    def test_weekly_strides_seven_days(self):
        """Interval 1 WEEK fires every seventh day from the anchor."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.WEEK, anchor_date=date(2026, 3, 26),
        )

        assert dates_through(value, calendar, date(2026, 4, 23)) == [
            date(2026, 3, 26), date(2026, 4, 2), date(2026, 4, 9),
            date(2026, 4, 16), date(2026, 4, 23),
        ]

    def test_biweekly_by_date_strides_fourteen_days(self):
        """Interval 2 WEEK is the biweekly-by-DATE bill the old set lacked."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.WEEK, anchor_date=date(2026, 3, 26),
            interval_n=2,
        )

        assert dates_through(value, calendar, date(2026, 4, 23)) == [
            date(2026, 3, 26), date(2026, 4, 9), date(2026, 4, 23),
        ]


@pytest.mark.usefixtures("app")
class TestMonthEndClamping:
    """Ruling R-R3: the anchor's day must not decay as the walk advances."""

    def test_a_day_31_rule_takes_the_last_day_of_every_month(self):
        """Day 31 clamps per month and recovers the 31st where it exists."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 1, 31),
        )

        assert dates_through(value, calendar, date(2026, 7, 31)) == [
            # 2026 is not a leap year, so February clamps to the 28th.
            date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31),
            date(2026, 4, 30), date(2026, 5, 31), date(2026, 6, 30),
            date(2026, 7, 31),
        ]

    def test_a_clamped_anchor_recovers_its_nominal_day(self):
        """``nominal_day`` is what stops a month-end rule decaying forever.

        Ruling R-R3's own measurement, made executable.  A day-31 rule whose
        first occurrence falls in April carries ``anchor_date`` 2026-04-30 --
        April has no 31st -- and the nominal day in the
        ``recurrence_month_anchors`` subtype.  Without it the walk would take
        the ANCHOR's day, 30, and every later 31-day month would be wrong.
        """
        calendar = build_calendar()
        with_subtype = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 4, 30),
            nominal_day=31,
        )
        without_subtype = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 4, 30),
        )

        assert dates_through(with_subtype, calendar, date(2026, 7, 31)) == [
            date(2026, 4, 30), date(2026, 5, 31), date(2026, 6, 30),
            date(2026, 7, 31),
        ]
        # The regression the subtype exists to prevent: 2 of these 4 are wrong.
        assert dates_through(without_subtype, calendar, date(2026, 7, 31)) == [
            date(2026, 4, 30), date(2026, 5, 30), date(2026, 6, 30),
            date(2026, 7, 30),
        ]

    def test_an_annual_leap_day_rule_clamps_only_in_common_years(self):
        """February 29 comes back in the next leap year, not once and never."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.YEAR, anchor_date=date(2024, 2, 29),
        )

        assert dates_through(value, calendar, date(2028, 12, 31)) == [
            date(2024, 2, 29), date(2025, 2, 28), date(2026, 2, 28),
            date(2027, 2, 28), date(2028, 2, 29),
        ]

    def test_a_day_28_rule_is_never_clamped(self):
        """Every month holds a 28th, so the common case costs nothing."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 1, 28),
        )

        assert dates_through(value, calendar, date(2026, 4, 30)) == [
            date(2026, 1, 28), date(2026, 2, 28), date(2026, 3, 28),
            date(2026, 4, 28),
        ]


@pytest.mark.usefixtures("app")
class TestTheClosingBounds:
    """Ruling R-R6: the bounds are on the OCCURRENCE, not on its period."""

    def test_an_occurrence_past_the_end_date_is_never_emitted(self):
        """Defect D5 dies here: no row is dated outside its own window."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 4, 15),
            end_date=date(2026, 6, 14),
        )

        assert dates_through(value, calendar, calendar.horizon()) == [
            date(2026, 4, 15), date(2026, 5, 15),
        ]

    def test_an_occurrence_on_the_end_date_is_kept(self):
        """The bound is inclusive -- a bill due the day the rule ends is due."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 4, 15),
            end_date=date(2026, 6, 15),
        )

        assert dates_through(value, calendar, calendar.horizon()) == [
            date(2026, 4, 15), date(2026, 5, 15), date(2026, 6, 15),
        ]

    def test_max_occurrences_emits_exactly_that_many(self):
        """The count bound, whose first author is plan step R8."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 4, 15),
            max_occurrences=3,
        )

        assert dates_through(value, calendar, calendar.horizon()) == [
            date(2026, 4, 15), date(2026, 5, 15), date(2026, 6, 15),
        ]

    def test_max_occurrences_counts_occurrences_not_placed_rows(self):
        """"Stop after N" is a property of the rule, not of the schedule.

        An occurrence that lands in a schedule gap still counts: the rule says
        the bill occurs, and whether the user's pay periods can host it is a
        different question.  Counting placed rows instead would silently
        extend a count-bounded rule past its own bound.
        """
        calendar = _gapped_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 1, 20),
            max_occurrences=2,
        )

        placements = occurrence_placements(value, calendar)

        assert [item.occurrence for item in placements] == [
            date(2026, 1, 20), date(2026, 2, 20),
        ]
        # The February occurrence falls in the gap and places nowhere, but it
        # still consumed one of the two.
        assert placements[1].period is None
        assert placed_indices(value, calendar) == [1]

    def test_a_window_ending_before_the_anchor_emits_nothing(self):
        """Nothing before the first occurrence can ever be generated."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 4, 15),
        )

        assert dates_through(value, calendar, date(2026, 4, 14)) == []


def _gapped_calendar() -> PeriodCalendar:
    """Return a schedule with a real hole in it (finding D7).

    Two January periods, then a month of nothing, then two March periods.
    ``pay_period_service._reject_overlapping_batch`` rejects OVERLAPS, not
    gaps, so this is a schedule the application can actually produce.

    Returns:
        The gapped :class:`~app.services.recurrence.PeriodCalendar`.
    """
    spans = (
        (0, date(2026, 1, 1), date(2026, 1, 14)),
        (1, date(2026, 1, 15), date(2026, 1, 28)),
        # 2026-01-29 .. 2026-02-28 is covered by no period at all.
        (2, date(2026, 3, 1), date(2026, 3, 14)),
        (3, date(2026, 3, 15), date(2026, 3, 28)),
    )
    return PeriodCalendar(user_id=_USER_ID, periods=tuple(
        SchedulePeriod(
            period_id=index + 1, period_index=index,
            start_date=start, end_date=end,
        )
        for index, start, end in spans
    ))


@pytest.mark.usefixtures("app")
class TestPlacement:
    """Carrying one occurrence DATE onto the pay period the row lives in."""

    def test_containing_date_finds_the_period_a_day_falls_inside(self):
        """The first, a middle and the last day of a period all place in it."""
        calendar = build_calendar()

        for day in (date(2026, 4, 9), date(2026, 4, 15), date(2026, 4, 22)):
            found = place(
                day, calendar, PeriodPlacementEnum.CONTAINING_DATE,
            )
            assert found is not None and found.period_index == 1, day

    def test_containing_date_answers_none_for_a_day_in_a_gap(self):
        """Finding D7: a date the schedule does not cover has no period.

        The alternative -- pulling the row into the neighbouring paycheck --
        would put real money in a period whose span does not contain it.
        """
        calendar = _gapped_calendar()

        assert place(
            date(2026, 2, 20), calendar, PeriodPlacementEnum.CONTAINING_DATE,
        ) is None

    def test_containing_date_answers_none_outside_the_schedule(self):
        """Before the first payday and after the horizon both answer None."""
        calendar = build_calendar()

        assert place(
            date(2026, 3, 25), calendar, PeriodPlacementEnum.CONTAINING_DATE,
        ) is None
        assert place(
            date(2028, 7, 27), calendar, PeriodPlacementEnum.CONTAINING_DATE,
        ) is None

    def test_starting_on_or_after_takes_the_next_payday(self):
        """A date ON a payday takes that one; a later date takes the next."""
        calendar = build_calendar()
        on_or_after = PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER

        exact = place(date(2026, 4, 9), calendar, on_or_after)
        one_day_later = place(date(2026, 4, 10), calendar, on_or_after)

        assert exact is not None and exact.period_index == 1
        assert one_day_later is not None and one_day_later.period_index == 2

    def test_the_composition_places_exactly_as_place_does(self):
        """The public ``place`` is not a second implementation.

        ``occurrence_placements`` resolves the search once per call instead of
        calling :func:`~app.services.recurrence.place` per occurrence, so the
        two could in principle drift.  They cannot -- both go through
        ``_placement_search`` -- and this asserts it over both placements
        rather than leaving it to a reader of the source.
        """
        calendar = _gapped_calendar()
        for placement in PeriodPlacementEnum:
            value = resolved_value(
                unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 1, 20),
                placement=placement,
            )
            for item in occurrence_placements(value, calendar):
                assert item.period == place(
                    item.occurrence, calendar, placement,
                ), (placement, item.occurrence)

    def test_starting_on_or_after_answers_none_past_the_last_payday(self):
        """"Not yet" rather than "never" -- the schedule will extend."""
        calendar = build_calendar()

        assert place(
            date(2028, 7, 14), calendar,
            PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
        ) is None


@pytest.mark.usefixtures("app")
class TestScheduleGapsAndTheHorizon:
    """Finding D7, and what the default generation window means."""

    def test_an_occurrence_in_a_gap_is_reported_rather_than_dropped(self):
        """The composition names the hole instead of hiding it.

        ``match_periods`` returns only periods, so an occurrence with nowhere
        to live simply never appears -- indistinguishable from a rule that
        does not fire.  Here it appears with ``period=None``, which is what
        lets plan step R4 log a schedule hole instead of losing a bill.
        """
        calendar = _gapped_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 1, 20),
        )

        placements = occurrence_placements(value, calendar)

        assert [item.occurrence for item in placements] == [
            date(2026, 1, 20), date(2026, 2, 20), date(2026, 3, 20),
        ]
        assert [
            None if item.period is None else item.period.period_index
            for item in placements
        ] == [1, None, 3]

    def test_the_default_window_ends_at_the_schedules_horizon(self):
        """Past the last covered day no placement can succeed, so none is asked."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2028, 6, 15),
        )

        placements = occurrence_placements(value, calendar)

        # The schedule's last covered day is 2028-07-26, so July is the last
        # occurrence inside the default window and August is outside it.
        assert [item.occurrence for item in placements] == [
            date(2028, 6, 15), date(2028, 7, 15),
        ]

    def test_a_wider_window_reports_the_occurrences_beyond_the_horizon(self):
        """Explicitly asked for, they come back explicitly unplaced."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2028, 6, 15),
        )

        placements = occurrence_placements(
            value, calendar, through=date(2028, 9, 30),
        )

        assert [item.occurrence for item in placements] == [
            date(2028, 6, 15), date(2028, 7, 15), date(2028, 8, 15),
            date(2028, 9, 15),
        ]
        assert [item.period is None for item in placements] == [
            False, False, True, True,
        ]

    def test_an_empty_schedule_places_nothing(self):
        """No periods, no window, no placements -- and no exception.

        ``resolve`` already refuses an empty schedule, so a resolved value
        never pairs with one in the application; the engine answers rather
        than raising because "generate nothing" is what
        ``generate_for_template`` does today for an empty period list.
        """
        calendar = PeriodCalendar(user_id=_USER_ID, periods=())
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 4, 15),
        )

        assert occurrence_placements(value, calendar) == ()


@pytest.mark.usefixtures("app")
class TestRefusals:
    """What the engine refuses rather than answering wrongly."""

    @pytest.mark.parametrize("shift", [
        BusinessDayShiftEnum.PRIOR, BusinessDayShiftEnum.NEXT,
    ])
    def test_a_business_day_shift_is_refused_until_step_r8(self, shift):
        """Generating unshifted dates would silently ignore the user's choice.

        Refused EAGERLY -- the call itself raises, not the first ``next()`` --
        so a caller that builds the iterator and hands it on sees the failure
        where it was caused.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 4, 15),
            shift=shift,
        )

        with pytest.raises(RecurrenceGenerationError, match="business-day"):
            occurrences(value, calendar, through=date(2026, 12, 31))

    @pytest.mark.parametrize("interval", [0, -1])
    def test_a_non_positive_interval_is_refused(self, interval):
        """A zero stride emits the same date forever; refuse, do not spin."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 4, 15),
            interval_n=interval,
        )

        with pytest.raises(RecurrenceGenerationError, match="must be positive"):
            occurrences(value, calendar, through=date(2026, 12, 31))

    def test_a_unit_with_no_walk_is_refused(self):
        """A value outside the enum raises instead of firing nowhere.

        Unreachable through the type, and that is the point: an unrecognised
        unit answering "no occurrences" would read as a rule that never fires,
        which is a silently missing bill.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 4, 15),
        )
        not_a_unit = ResolvedRecurrence(
            **{**vars(value), "unit": object()},
        )

        with pytest.raises(RecurrenceGenerationError, match="no occurrence walk"):
            occurrences(not_a_unit, calendar, through=date(2026, 12, 31))

    def test_a_placement_with_no_rule_is_refused(self):
        """Same reasoning on the placement axis."""
        calendar = build_calendar()

        with pytest.raises(RecurrenceGenerationError, match="has no rule"):
            place(date(2026, 4, 15), calendar, object())

    def test_a_nominal_day_that_disagrees_with_its_anchor_is_refused(self):
        """The pair must state one day, and R7c is where they can diverge.

        ``nominal_day`` exists only because the anchor MONTH was too short to
        hold the day the user meant (ruling R-R3), so
        ``min(nominal_day, days in the anchor's month)`` must be the anchor's
        own day.  ``resolve`` cannot break that -- ``_month_anchor_day``
        records the day only when the anchor lands on its month's last day --
        but plan step R7c turns both into independently-authored columns whose
        only constraint is ``nominal_day BETWEEN 29 AND 31``.  Walking from a
        disagreeing pair fires the first occurrence on a day the anchor does
        not name, which nothing downstream would notice.
        """
        calendar = build_calendar()
        # April HAS a 15th, so a day-31 rule could never anchor on the 15th.
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 4, 15),
            nominal_day=31,
        )

        with pytest.raises(RecurrenceGenerationError, match="nominal_day 31"):
            occurrences(value, calendar, through=date(2026, 12, 31))

    @pytest.mark.parametrize("broken", [
        {"shift": BusinessDayShiftEnum.PRIOR},
        {"interval_n": 0},
        {"nominal_day": 31},
    ])
    def test_an_empty_schedule_does_not_excuse_a_broken_value(self, broken):
        """The composition refuses what ``occurrences`` refuses, always.

        ``occurrence_placements`` short-circuits to ``()`` for a schedule with
        no periods, and a neutral review measured that the short-circuit ran
        BEFORE the refusals -- so a business-day shift or a zero interval was
        silently accepted there and raised everywhere else.  The guards now run
        first.
        """
        empty = PeriodCalendar(user_id=_USER_ID, periods=())
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 4, 15),
            **broken,
        )

        with pytest.raises(RecurrenceGenerationError):
            occurrence_placements(value, empty)

    def test_an_empty_schedule_does_not_excuse_a_broken_placement(self):
        """Same, for the placement axis, which resolves before the walk."""
        empty = PeriodCalendar(user_id=_USER_ID, periods=())
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, anchor_date=date(2026, 4, 15),
            placement=object(),
        )

        with pytest.raises(RecurrenceGenerationError, match="has no rule"):
            occurrence_placements(value, empty)


@pytest.mark.usefixtures("app")
class TestTheScheduleSearches:
    """``PeriodCalendar``'s new surface, and the invariant it now enforces."""

    def test_the_horizon_is_the_last_covered_day(self):
        """The symmetric partner of ``opening_bound``."""
        calendar = build_calendar()

        assert calendar.opening_bound() == date(2026, 3, 26)
        # 61 periods x 14 days from 2026-03-26 ends 2028-07-26.
        assert calendar.horizon() == date(2028, 7, 26)

    def test_an_empty_schedule_has_no_horizon(self):
        """``None`` rather than a fabricated date."""
        assert PeriodCalendar(user_id=_USER_ID, periods=()).horizon() is None

    def test_a_gapped_schedule_is_accepted(self):
        """Gaps are legal -- the generator rejects overlaps, not holes."""
        calendar = _gapped_calendar()

        assert len(calendar.periods) == 4
        assert calendar.horizon() == date(2026, 3, 28)

    def test_an_overlapping_schedule_is_refused(self):
        """Two periods covering one day give the searches two answers.

        The invariant ``pay_period_service._reject_overlapping_batch``
        enforces on write, checked again at the value boundary because the
        placement searches BISECT: an overlapping schedule would return a
        plausible wrong paycheck instead of an error.
        """
        with pytest.raises(RecurrenceScheduleError, match="on or before"):
            PeriodCalendar(user_id=_USER_ID, periods=(
                SchedulePeriod(
                    period_id=1, period_index=0,
                    start_date=date(2026, 1, 1), end_date=date(2026, 1, 14),
                ),
                SchedulePeriod(
                    period_id=2, period_index=1,
                    start_date=date(2026, 1, 14), end_date=date(2026, 1, 27),
                ),
            ))

    def test_a_period_ending_before_it_starts_is_refused(self):
        """A period covering no day cannot be searched by date."""
        with pytest.raises(RecurrenceScheduleError, match="covers no day"):
            PeriodCalendar(user_id=_USER_ID, periods=(
                SchedulePeriod(
                    period_id=1, period_index=0,
                    start_date=date(2026, 1, 14), end_date=date(2026, 1, 1),
                ),
            ))
