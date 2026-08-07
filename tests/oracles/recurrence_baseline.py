"""The recurrence engine's frozen behaviour baseline (plan step R1).

The gate every later step of ``docs/plans/implementation_plan_recurrence_redesign.md``
is measured against.  R3 builds a new occurrence engine and R4 cuts generation
over to it; this module is what proves the cutover moved nothing it did not
mean to move.

**What it captures.**  For each rule SHAPE, the exact answer the CURRENT engine
gives to the only two questions it is asked in production:

* :func:`app.services.recurrence_engine.match_periods` -- which pay periods
  does this rule fire in?
* :func:`app.services.recurrence_engine.compute_due_date` -- what date does the
  generated row carry?

Both are the module's public surface (their docstrings say so) and both are
pure functions of a rule's columns plus a period list, so the baseline needs no
database rows -- only an app context, for ``ref_cache`` to resolve a pattern
enum to its integer id.

**Why the keys are ``period_index`` and an enum NAME, never a row id.**  A
snapshot keyed on ``pay_periods.id`` or ``ref.recurrence_patterns.id`` would
churn every time the test template was rebuilt, and a baseline that changes for
reasons unrelated to the code under test is a baseline nobody trusts.  Both
keys here are stable facts: ``period_index`` is the schedule's own ordinal, and
the enum member is the name the app compares by id at runtime but identifies by
in source.

**Why real model instances rather than stubs.**  ``build_shape_rule`` returns an
unsaved :class:`~app.models.recurrence_rule.RecurrenceRule` and
:func:`build_schedule` unsaved :class:`~app.models.pay_period.PayPeriod` rows,
for the reason finding B-17 records and
``tests/test_services/test_recurrence_engine.py::build_rule`` documents at
length: a hand-rolled double that mirrors the model drifts from it silently.
When ``start_date`` was added to the rule, the stub of the day kept satisfying
every assertion.  An unsaved instance costs no session and no flush, and a
column the model does not have raises ``TypeError`` here instead of passing.

It does NOT carry SQLAlchemy's ``default=`` values (those apply at INSERT), so
``interval_n`` and ``offset_periods`` are always passed explicitly -- the same
trap that module documents.

**The clock.**  Every date in this module is a literal.  Nothing reads
``date.today()`` or ``display_today()``, so the blob is identical under
``TZ=Pacific/Kiritimati`` and under the weekly ``SHEKEL_FAKE_TODAY`` sweep
(``docs/test-suite-clocks.md``).  The schedule deliberately opens on a leap
year so February 29 clamping is inside the covered span rather than argued
about.

**Coverage, stated rather than implied** (the plan's "no silent caps" rule).
The shape set is exhaustive on every axis the matcher branches on, except that
the three calendar patterns that take BOTH a month and a day
(``QUARTERLY`` / ``SEMI_ANNUAL`` / ``ANNUAL``) sweep all twelve months against
the six days that matter -- ``1`` and ``15`` for the ordinary cases and
``28``-``31`` for every month-length clamp -- rather than all 372 combinations.
Days 2-14 and 16-27 differ from 15 in no branch of ``_match_specific_months``
or ``_match_annual``: both clamp with ``min(day, monthrange(...))``, which is
the identity for every day below 28.  ``MONTHLY`` sweeps all 31 days anyway,
because there the day IS the whole rule.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from app import ref_cache
from app.enums import RecurrencePatternEnum
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
# Imported as a MODULE, not as two names.  ``from ... import match_periods``
# would bind the function at import time, and a negative control that patches
# ``app.services.recurrence_engine.match_periods`` -- the source module, which
# is the testing standards' preferred patch target -- would then not be seen
# here.  A harness that cannot see the code under test is the failure mode the
# balance arc's verification standard names ("Ask of every harness: can it SEE
# the code under test?", plan Section 7.2), so the indirection is load-bearing:
# it is what makes the firing controls in
# ``tests/test_services/test_recurrence_baseline.py`` real.
from app.services import recurrence_engine

#: The baseline schedule's first payday.  A literal, and a LEAP year, so
#: February 29 clamping is covered rather than assumed.
SCHEDULE_START: date = date(2024, 1, 1)

#: Days between paydays.  14 is the cadence the developer runs and the
#: ``generate_pay_periods`` default; the matcher's behaviour at longer cadences
#: is defect D3 in the plan and is captured by its own shapes below.
SCHEDULE_CADENCE_DAYS: int = 14

#: Periods in the baseline schedule.  79 biweekly periods span just over three
#: calendar years (2024-01-01 to 2027-01-04), which puts three year boundaries
#: and one February 29 inside the window.
SCHEDULE_PERIOD_COUNT: int = 79

#: A second, deliberately LONG cadence.  ``_match_monthly`` inspects only the
#: months of a period's two endpoints, so a period spanning more than two
#: months silently drops the interior ones (plan defect D3).  ``cadence_days``
#: is user-selectable 1..365, so this is reachable configuration, not a
#: hypothetical -- and freezing the WRONG answer here is deliberate: R4 is
#: expected to change these lines and no others, which is what makes the fix
#: visible in a diff instead of asserted in a message.
LONG_CADENCE_DAYS: int = 90

#: Periods in the long-cadence schedule -- twelve quarters, three years, so the
#: two schedules cover the same span.
LONG_CADENCE_PERIOD_COUNT: int = 12

#: Days swept for the patterns that take a month AND a day.  1 and 15 are the
#: ordinary cases; 28-31 are every distinct month-length clamp (February in a
#: common year, February in a leap year, the 30-day months, the 31-day months).
#: See the module docstring for why 2-14 and 16-27 add no branch.
_CLAMP_DAYS: tuple[int, ...] = (1, 15, 28, 29, 30, 31)

#: Scheduling days swept against every due day.  ``compute_due_date``'s
#: next-month convention triggers on ``due_dom < dom``, so the set spans a day
#: below every due day (1), a mid-month day (15), the live Van Loan's day (22),
#: and a day above every due day (31).
_DUE_SWEEP_DAYS: tuple[int, ...] = (1, 15, 22, 31)


@dataclass(frozen=True)
class RuleShape:
    """One rule configuration to freeze the engine's answer for.

    A shape is the rule's column values plus a stable label.  It is NOT a
    :class:`~app.models.recurrence_rule.RecurrenceRule`; the rule is built from
    it by :func:`build_shape_rule` at capture time, so the shape can be
    declared without an app context while the rule cannot.

    Attributes:
        label: Stable, sortable identity for this shape in the blob.  Encodes
            every field that differs from the defaults, so a reader diffing the
            snapshot can reconstruct the rule without consulting this file.
        pattern: The pattern enum member, resolved to an integer id at capture.
        interval_n: ``EVERY_N_PERIODS`` interval; 1 elsewhere.
        offset_periods: ``EVERY_N_PERIODS`` phase; 0 elsewhere.
        day_of_month: Scheduling day for the calendar patterns.
        due_day_of_month: Real bill due day when it differs from the
            scheduling day.
        month_of_year: Cycle-start month for quarterly / semi-annual / annual.
        start_date: The rule's opening validity bound (unbypassable).
        end_date: The rule's closing validity bound.
        long_cadence: Capture this shape against the 90-day schedule instead
            of the biweekly one (the D3 shapes).
    """

    label: str
    pattern: RecurrencePatternEnum
    interval_n: int = 1
    offset_periods: int = 0
    day_of_month: int | None = None
    due_day_of_month: int | None = None
    month_of_year: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    long_cadence: bool = False


@dataclass
class _ShapeAccumulator:
    """Mutable shape list under construction, so each builder appends once."""

    shapes: list[RuleShape] = field(default_factory=list)

    def add(self, shape: RuleShape) -> None:
        """Append one shape."""
        self.shapes.append(shape)


def build_schedule(
    start: date, cadence_days: int, count: int,
) -> list[PayPeriod]:
    """Return ``count`` contiguous unsaved pay periods.

    Mirrors ``pay_period_service.generate_pay_periods`` exactly: each period
    ends ``cadence_days - 1`` after it starts, and the next begins the
    following day, so the schedule is CONTIGUOUS and every calendar date falls
    in exactly one period.  That totality is the property the redesign's
    forward placement depends on, so the baseline must be built the same way
    the app builds one.

    Args:
        start: The first payday.
        cadence_days: Days between paydays.
        count: How many periods to build.

    Returns:
        Unsaved :class:`~app.models.pay_period.PayPeriod` rows ordered by
        ``period_index``, which starts at 0 as the real generator's does.
    """
    periods = []
    for index in range(count):
        period_start = start + timedelta(days=cadence_days * index)
        periods.append(PayPeriod(
            start_date=period_start,
            end_date=period_start + timedelta(days=cadence_days - 1),
            period_index=index,
        ))
    return periods


def build_shape_rule(shape: RuleShape) -> RecurrenceRule:
    """Return a real, unsaved rule for *shape*.

    Requires an app context: ``pattern_id`` is resolved through
    :func:`app.ref_cache.recurrence_pattern_id`, the same lookup production
    uses, so a shape can never name a pattern the database does not carry.

    ``interval_n`` and ``offset_periods`` are passed explicitly because
    SQLAlchemy applies ``default=`` at INSERT and this row is never inserted --
    an unflushed rule would otherwise carry ``None`` where production has 1 and
    0, and the matcher would be exercised on a shape production never sees.

    Args:
        shape: The configuration to build.

    Returns:
        An unsaved :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    return RecurrenceRule(
        pattern_id=ref_cache.recurrence_pattern_id(shape.pattern),
        interval_n=shape.interval_n,
        offset_periods=shape.offset_periods,
        day_of_month=shape.day_of_month,
        due_day_of_month=shape.due_day_of_month,
        month_of_year=shape.month_of_year,
        start_date=shape.start_date,
        end_date=shape.end_date,
    )


def _add_period_space_shapes(acc: _ShapeAccumulator) -> None:
    """Every shape of the two pay-period-space patterns.

    ``EVERY_PERIOD`` has no parameters.  ``EVERY_N_PERIODS`` is swept over
    every interval 1..8 crossed with every legal phase for that interval
    (``offset_periods`` is only meaningful modulo ``interval_n``, so
    ``0..n-1`` is the complete space, not a sample).  ``MONTHLY_FIRST``
    takes no parameters either.
    """
    acc.add(RuleShape("every_period", RecurrencePatternEnum.EVERY_PERIOD))
    acc.add(RuleShape("monthly_first", RecurrencePatternEnum.MONTHLY_FIRST))
    for interval in range(1, 9):
        for offset in range(interval):
            acc.add(RuleShape(
                f"every_n_periods.n{interval:02d}.off{offset:02d}",
                RecurrencePatternEnum.EVERY_N_PERIODS,
                interval_n=interval,
                offset_periods=offset,
            ))


def _add_monthly_shapes(acc: _ShapeAccumulator) -> None:
    """Every scheduling day for the monthly pattern, 1..31.

    Swept in full rather than at the clamp boundaries: for ``MONTHLY`` the day
    is the entire rule, and day 31 is also the app's only idiom for "last day
    of the month" (it clamps), so no day here is redundant with another.
    """
    for day in range(1, 32):
        acc.add(RuleShape(
            f"monthly.dom{day:02d}",
            RecurrencePatternEnum.MONTHLY,
            day_of_month=day,
        ))


def _add_calendar_cycle_shapes(acc: _ShapeAccumulator) -> None:
    """Every cycle-start month crossed with the clamp-relevant days.

    Covers ``QUARTERLY``, ``SEMI_ANNUAL`` and ``ANNUAL``.  The month axis is
    complete (1..12); the day axis is :data:`_CLAMP_DAYS`, which the module
    docstring justifies branch by branch.
    """
    cycles = (
        ("quarterly", RecurrencePatternEnum.QUARTERLY),
        ("semi_annual", RecurrencePatternEnum.SEMI_ANNUAL),
        ("annual", RecurrencePatternEnum.ANNUAL),
    )
    for name, pattern in cycles:
        for month in range(1, 13):
            for day in _CLAMP_DAYS:
                acc.add(RuleShape(
                    f"{name}.moy{month:02d}.dom{day:02d}",
                    pattern,
                    month_of_year=month,
                    day_of_month=day,
                ))


def _add_due_day_shapes(acc: _ShapeAccumulator) -> None:
    """Every due day 1..31 against four scheduling days.

    This is the axis ``compute_due_date`` branches on, and the one plan step R5
    rewrites: below the scheduling day the due date rolls into the FOLLOWING
    calendar month, at or above it stays in the same month, and both ends clamp
    to the month's length.  Freezing the whole due axis is what lets R5's
    explicit ``due_month_offset`` be diffed against the convention it replaces.
    """
    for dom in _DUE_SWEEP_DAYS:
        for due_dom in range(1, 32):
            acc.add(RuleShape(
                f"due_sweep.dom{dom:02d}.due{due_dom:02d}",
                RecurrencePatternEnum.MONTHLY,
                day_of_month=dom,
                due_day_of_month=due_dom,
            ))


def _add_bound_shapes(acc: _ShapeAccumulator) -> None:
    """The validity-window shapes.

    ``start_date`` and ``end_date`` are applied in ``match_periods`` itself and
    are therefore UNBYPASSABLE by a caller's ``effective_from`` -- the property
    that lets a loan payment refuse to generate before origination.  Each bound
    is placed mid-period and on a period boundary, in both directions, because
    the comparisons are asymmetric: ``start_date`` is tested against a period's
    END and ``end_date`` against its START.
    """
    bounds = (
        ("start.midperiod", date(2024, 6, 5), None),
        ("start.on_period_start", date(2024, 6, 3), None),
        ("start.on_period_end", date(2024, 6, 16), None),
        ("end.midperiod", None, date(2025, 6, 5)),
        ("end.on_period_start", None, date(2025, 6, 2)),
        ("end.on_period_end", None, date(2025, 6, 15)),
        ("window.both", date(2024, 6, 5), date(2025, 6, 5)),
        ("window.inverted", date(2025, 6, 5), date(2024, 6, 5)),
    )
    for name, start, end in bounds:
        acc.add(RuleShape(
            f"bounds.{name}",
            RecurrencePatternEnum.MONTHLY,
            day_of_month=15,
            start_date=start,
            end_date=end,
        ))


def _add_long_cadence_shapes(acc: _ShapeAccumulator) -> None:
    """The shapes that expose defect D3, captured against a 90-day schedule.

    ``_match_monthly`` and ``_match_specific_months`` read only a period's
    ``start_date`` and ``end_date`` months, so a period spanning four months
    can match at most two of them.  These shapes freeze that behaviour -- WRONG
    but current -- so R4's forward-placement rewrite changes exactly these
    lines and no others.
    """
    for day in (1, 15, 31):
        acc.add(RuleShape(
            f"long_cadence.monthly.dom{day:02d}",
            RecurrencePatternEnum.MONTHLY,
            day_of_month=day,
            long_cadence=True,
        ))
    for day in (1, 15):
        acc.add(RuleShape(
            f"long_cadence.quarterly.moy01.dom{day:02d}",
            RecurrencePatternEnum.QUARTERLY,
            month_of_year=1,
            day_of_month=day,
            long_cadence=True,
        ))
    acc.add(RuleShape(
        "long_cadence.every_period",
        RecurrencePatternEnum.EVERY_PERIOD,
        long_cadence=True,
    ))


def build_shapes() -> list[RuleShape]:
    """Return every shape the baseline covers, in stable label order.

    Sorted by label so the blob's line order is a property of the shape set
    rather than of the order the builders happen to run in -- adding a builder
    must not reshuffle unrelated lines in the diff.

    Returns:
        The shape list, deduplicated by construction (each builder owns a
        disjoint label prefix).
    """
    acc = _ShapeAccumulator()
    _add_period_space_shapes(acc)
    _add_monthly_shapes(acc)
    _add_calendar_cycle_shapes(acc)
    _add_due_day_shapes(acc)
    _add_bound_shapes(acc)
    _add_long_cadence_shapes(acc)
    return sorted(acc.shapes, key=lambda shape: shape.label)


def capture_shape(shape: RuleShape, periods: list[PayPeriod]) -> list[str]:
    """Return the blob lines for one shape.

    Calls the engine's two public entry points exactly as
    ``generate_for_template`` does: ``match_periods`` with ``effective_from``
    defaulted to the first period's start (the engine's own fallback when no
    caller supplies one), then ``compute_due_date`` per matched period.

    A shape that matches nothing emits one ``(none)`` line rather than
    disappearing.  A shape that vanished silently would be indistinguishable in
    the diff from a shape that was never captured, which is how a regression
    hides.

    Args:
        shape: The configuration to capture.
        periods: The schedule to capture it against.

    Returns:
        One line per matched period, or a single ``(none)`` line.
    """
    rule = build_shape_rule(shape)
    matched = recurrence_engine.match_periods(
        rule, rule.pattern_id, periods, periods[0].start_date,
    )
    if not matched:
        return [f"{shape.label} (none)"]
    return [
        f"{shape.label} idx={period.period_index:03d} "
        f"period={period.start_date.isoformat()}..{period.end_date.isoformat()} "
        f"due={recurrence_engine.compute_due_date(rule, period).isoformat()}"
        for period in matched
    ]


def capture_baseline() -> str:
    """Return the whole baseline blob.

    Requires an app context (``build_shape_rule`` resolves pattern ids through
    ``ref_cache``).  Deterministic: same shapes, same schedules, same literal
    dates, no clock read anywhere.

    Returns:
        The blob, newline-terminated, with a header stating the schedules and
        the shape count so a reader can tell a truncated file from a shrunk
        one.
    """
    biweekly = build_schedule(
        SCHEDULE_START, SCHEDULE_CADENCE_DAYS, SCHEDULE_PERIOD_COUNT,
    )
    long_cadence = build_schedule(
        SCHEDULE_START, LONG_CADENCE_DAYS, LONG_CADENCE_PERIOD_COUNT,
    )
    shapes = build_shapes()
    lines = [
        "# recurrence engine behaviour baseline (plan step R1)",
        "# REGENERATE: SHEKEL_UPDATE_RECURRENCE_BASELINE=1 ./scripts/test.sh "
        "tests/test_services/test_recurrence_baseline.py",
        f"# biweekly schedule: {SCHEDULE_START.isoformat()} "
        f"cadence={SCHEDULE_CADENCE_DAYS} periods={SCHEDULE_PERIOD_COUNT} "
        f"({biweekly[0].start_date.isoformat()}"
        f"..{biweekly[-1].end_date.isoformat()})",
        f"# long schedule:     {SCHEDULE_START.isoformat()} "
        f"cadence={LONG_CADENCE_DAYS} periods={LONG_CADENCE_PERIOD_COUNT} "
        f"({long_cadence[0].start_date.isoformat()}"
        f"..{long_cadence[-1].end_date.isoformat()})",
        f"# shapes: {len(shapes)}",
    ]
    for shape in shapes:
        lines.extend(capture_shape(
            shape, long_cadence if shape.long_cadence else biweekly,
        ))
    return "\n".join(lines) + "\n"
