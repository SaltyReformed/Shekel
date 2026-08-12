"""The recurrence engine's frozen behaviour baseline (plan step R1).

The gate every later step of ``docs/plans/implementation_plan_recurrence_redesign.md``
is measured against.  R3 built a new occurrence engine and R4a cut the
``match_periods`` adapter over to it; this module is what proved the cutover
moved nothing it did not mean to move, and what holds R4b onward to the same
standard.

**The snapshot was re-frozen at plan step R4a** (+122 / -4 lines over exactly
the 12 shapes ruling R-R6 and plan defect D3 predicted), so it now records the
FORWARD engine.  What it recorded before is in that commit's diff; a snapshot
is a record of what shipped, not of what was replaced.

**What it captures.**  For each rule SHAPE, the exact answer the engine gives
to the only two questions it is asked in production:

* :func:`app.services.recurrence.rule_occurrences` -- which pay periods does
  this rule fire in?  (Plan step R4b-2 replaced ``match_periods`` with it; the
  capture keeps taking the PERIOD half, and dropping an unplaced occurrence
  here is what the deleted adapter did for it, so the blob is unmoved.)
* :func:`app.services.recurrence_engine.compute_due_date` -- what date does the
  generated row carry?

Both are their module's public surface (their docstrings say so) and both are
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
Days 2-14 and 16-27 differ from 15 in no branch of the walk: it clamps with
``min(day, monthrange(...))``, which is the identity for every day below 28.  ``MONTHLY`` sweeps all 31 days anyway,
because there the day IS the whole rule.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from app import ref_cache
from app.enums import RecurrencePatternEnum
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.services.pay_calendar import PayCalendar
from app.services.recurrence import RecurrenceSpec
# Both imported as MODULES, not as names, and the recurrence producer is
# imported from its DEFINITION site (``_reading``) rather than through the
# package's re-export.  ``from ... import rule_occurrences`` would bind the
# function at import time; reaching it through the package alias would make the
# firing control in ``tests/test_services/test_recurrence_baseline.py`` prove
# only that this module reads the attribute it reads.  Patching the definition
# site is the testing standards' preferred target and is what makes the control
# say something -- a harness that cannot SEE the code under test is the failure
# mode the balance arc's verification standard names (plan Section 7.2).
# ``_reading`` is package-private to ``app/`` (the W9910 checker runs on
# ``pylint app/``); a test naming it is naming the thing it is testing.
from app.services import recurrence_engine
from app.services.recurrence import _reading

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

#: A second, deliberately LONG cadence.  The reverse matcher inspected only
#: the months of a period's two endpoints, so a period spanning more than two
#: months silently dropped the interior ones (plan defect D3).  ``cadence_days``
#: is user-selectable 1..365, so this was reachable configuration, not a
#: hypothetical -- and freezing the WRONG answer here was deliberate: plan step
#: R4a changed these lines and no others, which is what made the fix visible in
#: a diff instead of asserted in a message.
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


#: The owner every spec and calendar this module builds names.  The baseline's
#: pay periods are unsaved and carry no ``user_id``, and
#: ``app.services.recurrence.resolve`` REFUSES a spec paired with another
#: user's schedule -- so one constant is what keeps the two halves agreeing.
SHAPE_USER_ID: int = 1


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
        # The owner is STATED, and until plan step R4b-1 it did not need to
        # be: period selection built the calendar from ``rule.user_id``, so
        # the resolver's owner check compared a value against itself.  The
        # calendar is an argument now, so the rule has to name the same owner
        # the schedule does -- which is that check finally doing its job.
        user_id=SHAPE_USER_ID,
        pattern_id=ref_cache.recurrence_pattern_id(shape.pattern),
        interval_n=shape.interval_n,
        offset_periods=shape.offset_periods,
        day_of_month=shape.day_of_month,
        due_day_of_month=shape.due_day_of_month,
        month_of_year=shape.month_of_year,
        start_date=shape.start_date,
        end_date=shape.end_date,
    )


#: Parses one captured blob line back into its label, period index and due
#: date.  Written beside :func:`capture_shape`, which produces the format, so
#: the two cannot drift; :func:`parse_baseline_rows` is what plan step R3's
#: parallel run reads the committed snapshot with.
_BLOB_LINE = re.compile(
    r"^(?P<label>\S+) idx=(?P<index>\d+) "
    r"period=\S+ due=(?P<due>\d{4}-\d{2}-\d{2})$"
)

#: Matches the line a shape that fires nowhere emits.
_BLOB_NONE_LINE = re.compile(r"^(?P<label>\S+) \(none\)$")


def build_shape_spec(shape: "RuleShape") -> RecurrenceSpec:
    """Return the authored spec for *shape*.

    The two-axis sibling of :func:`build_shape_rule`: that one builds what the
    OLD engine reads (a :class:`~app.models.recurrence_rule.RecurrenceRule`),
    this one builds what the new engine's producer reads (a
    :class:`~app.services.recurrence.RecurrenceSpec`, which
    ``app.services.recurrence.resolve`` turns into the two-axis meaning).
    Both are built from the SAME shape, which is the whole point: a parallel
    run that fed the two engines differently would prove nothing.

    ``start_period_id`` is deliberately left unset.  The baseline captures with
    no lower window bound (``capture_shape``), and ``resolve`` reaches the
    schedule's opening through ``PayCalendar.opening_bound()`` -- so a start
    period here would add a bound the captured answers were never measured
    under.

    Requires an app context: ``pattern_id`` resolves through
    :func:`app.ref_cache.recurrence_pattern_id`.

    Args:
        shape: The configuration to build.

    Returns:
        The :class:`~app.services.recurrence.RecurrenceSpec`.
    """
    return RecurrenceSpec(
        user_id=SHAPE_USER_ID,
        pattern_id=ref_cache.recurrence_pattern_id(shape.pattern),
        interval_n=shape.interval_n,
        offset_periods=shape.offset_periods,
        day_of_month=shape.day_of_month,
        due_day_of_month=shape.due_day_of_month,
        month_of_year=shape.month_of_year,
        start_date=shape.start_date,
        end_date=shape.end_date,
    )


def build_shape_calendar(
    periods: list[PayPeriod], cadence_days: int,
) -> PayCalendar:
    """Return the resolver's view of one of this module's schedules.

    **Built from the PAYDAYS since plan step C2-b2**, which is what the
    application does: a period's ordinal and its last covered day are derived
    from the payday set and the owner's cadence, so handing over the stored
    columns would hand over a second answer.  Both schedules here are
    contiguous and generated at one cadence, so the derived ends reproduce the
    stored ones exactly -- which is why the frozen baseline blob does not move.

    Args:
        periods: A schedule from :func:`build_schedule`.
        cadence_days: The cadence that schedule was generated at.

    Returns:
        The :class:`~app.services.pay_calendar.PayCalendar` for
        :data:`SHAPE_USER_ID`.
    """
    return PayCalendar.from_paydays(
        paydays=[(period.id, period.start_date) for period in periods],
        cadence_days=cadence_days,
        user_id=SHAPE_USER_ID,
    )


def parse_baseline_rows(blob: str) -> dict[str, list[tuple[int, date]]]:
    """Return ``{shape label: [(period_index, due date), ...]}`` from a blob.

    The inverse of :func:`capture_shape`, for plan step R3's parallel run: the
    committed snapshot is what the OLD engine answers, and this is how the new
    engine's answer is compared against it.

    **The ``due=`` half is parsed even though R3 does not generate due dates**,
    and it earns its place: for a ``MONTHLY`` shape the due date IS the
    occurrence date, so it is what lets a test check that a row the forward
    engine stops generating was dated outside its own rule's window -- against
    the snapshot rather than against a literal somebody typed.

    A shape that fires nowhere parses to an EMPTY list rather than being
    absent, so "matched nothing" and "was never captured" stay distinguishable
    on this side of the format too.

    Args:
        blob: A baseline snapshot, from the committed file or from
            :func:`capture_baseline`.

    Returns:
        One entry per shape, its matched rows in captured order.

    Raises:
        ValueError: When a non-comment line matches neither form.  A blob this
            cannot read is a format change, and reading it partially would
            silently shrink the comparison.
    """
    parsed: dict[str, list[tuple[int, date]]] = {}
    for line in blob.splitlines():
        if not line or line.startswith("#"):
            continue
        match = _BLOB_LINE.match(line)
        if match is not None:
            parsed.setdefault(match["label"], []).append(
                (int(match["index"]), date.fromisoformat(match["due"])),
            )
            continue
        none_match = _BLOB_NONE_LINE.match(line)
        if none_match is None:
            raise ValueError(
                f"unparseable baseline line {line!r}: it is neither an "
                f"'<label> idx=NNN period=... due=...' occurrence nor a "
                f"'<label> (none)'.  The blob format changed and "
                f"parse_baseline_rows was not updated."
            )
        parsed.setdefault(none_match["label"], [])
    return parsed


def parse_baseline(blob: str) -> dict[str, list[int]]:
    """Return ``{shape label: [period_index, ...]}`` from a captured blob.

    The period-index projection of :func:`parse_baseline_rows`, derived from
    it rather than parsed a second time so the two readings of one format
    cannot disagree.

    Args:
        blob: A baseline snapshot.

    Returns:
        One entry per shape, its matched period indices in captured order.
    """
    return {
        label: [index for index, _due in rows]
        for label, rows in parse_baseline_rows(blob).items()
    }


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

Both bounds are UNBYPASSABLE by a caller's ``effective_from`` -- the property
    that lets a loan payment refuse to generate before origination.  Each is
    placed mid-period and on a period boundary, in both directions, because the
    reverse matcher's comparisons were ASYMMETRIC: ``start_date`` was tested
    against a period's END and ``end_date`` against its START, which is what
    let it generate a row dated outside the window (defect D5).  Plan step R4a
    moved both onto the occurrence and four of these eight shapes dropped a row
    each -- exactly the rows ruling R-R6 predicted.
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


def _add_horizon_bound_shapes(acc: _ShapeAccumulator) -> None:
    """``Monthly First`` rules whose opening bound is past the last payday.

    **Plan ledger row D10, added at plan step R4b-2.**  That pattern's anchor is
    derived by SCANNING the schedule's own months for the first one whose own
    first paycheck clears the bound; past the last payday there is no month left
    to inspect, so ``_resolution._first_of_month_anchor`` falls back to "the 1st
    of the month after the bound".  The fallback's answer is HORIZON-DEPENDENT:
    extending the schedule can move it a month earlier.  No shape reached that
    branch, so the re-freeze at plan step R4a gated an unmeasured code path --
    a hole in a baseline is not a smaller baseline, it is a green gate over
    behaviour nobody looked at.

    Both shapes capture ``(none)``, and that is all a blob keyed on PERIODS
    can say: a rule bounded past the last payday generates NOTHING.  It is
    deliberately NOT the gate on the anchor itself -- a neutral review measured
    two one-month mutants of the fallback leaving these lines unmoved, which is
    D10's own failure direction.  What gates the anchor is
    ``tests/test_services/test_recurrence_occurrence.py``'s
    ``_EXPECTED_UNPLACED``, which these shapes FEED and which names every
    unplaced occurrence date exactly.  The two are not redundant, because they
    reach ``(none)`` differently:

    * ``biweekly`` (bound 2027-01-05, last payday 2026-12-28, horizon
      2027-01-10) -- the fallback anchors 2027-02-01, past the horizon, so the
      walk emits no occurrence at all;
    * ``long_cadence`` (bound 2026-10-01, last payday 2026-09-17, horizon
      2026-12-15) -- the fallback anchors 2026-11-01, INSIDE the horizon, so
      two occurrences are emitted and both fail to place, because
      ``PERIOD_STARTING_ON_OR_AFTER`` needs a paycheck opening on or after them
      and the schedule has none.  It is the only shape whose lines are
      dropped ENTIRELY by ``placed_periods``; two others
      (``monthly_first``, ``long_cadence.monthly_first``) lose one occurrence
      each the same way.

    ``tests/test_services/test_recurrence_resolution.py`` carries the
    measurement of the horizon dependence itself, which a blob keyed on
    periods cannot express.
    """
    acc.add(RuleShape(
        "horizon_bound.monthly_first",
        RecurrencePatternEnum.MONTHLY_FIRST,
        start_date=date(2027, 1, 5),
    ))
    acc.add(RuleShape(
        "horizon_bound.long_cadence.monthly_first",
        RecurrencePatternEnum.MONTHLY_FIRST,
        start_date=date(2026, 10, 1),
        long_cadence=True,
    ))


def _add_long_cadence_shapes(acc: _ShapeAccumulator) -> None:
    """The shapes that expose defect D3, captured against a 90-day schedule.

    The reverse matcher read only a period's ``start_date`` and ``end_date``
    months, so a period spanning four months could match at most two of them.
    These shapes froze that behaviour -- WRONG but current -- so plan step
    R4a's forward-placement cutover changed exactly these lines and no others.
    They now freeze the answer that replaced it: every month a monthly bill is
    owed in, whatever the pay cadence.

    **Every pattern whose matcher reads endpoints is covered here, and that is
    a rule rather than a selection.**  The first cut of this builder covered
    ``MONTHLY`` and ``QUARTERLY`` only, and the gap was not free: plan step
    R3's parallel run reproduced 414 of 423 shapes and could say nothing at
    all about ``MONTHLY_FIRST``, ``SEMI_ANNUAL`` or ``ANNUAL`` at a long
    cadence, because no shape asked.  ``MONTHLY_FIRST`` turned out to be the
    one that matters -- its occurrences DEFER onto the next paycheck rather
    than landing in a containing one, so several months collapse onto one
    paycheck (see the plan's ledger).  A hole in a baseline is not a smaller
    baseline; it is a green gate over an unmeasured behaviour.

    ``EVERY_PERIOD`` is captured too, as the control: pay-period-space
    generation reads no months at all, so it must NOT move at any cadence.

    **The added shapes paid for themselves immediately.**  ``ANNUAL``'s period
    selection agreed at 90 days -- the reverse matcher deduped by YEAR, which
    is total at this cadence -- but its captured ``due=`` column did not:

    ```text
    long_cadence.annual.moy01.dom01 idx=004 ... due=2025-03-01
    ```

    A January annual rule dated 1 March.  ``compute_due_date`` picks its base
    month by asking which of a period's two ENDPOINT months contains the
    ``day_of_month`` target and never consults ``month_of_year``, so at a
    cadence where the firing month is neither endpoint it dates the row in the
    wrong month entirely.  Latent at 14 days, where the firing month always is
    an endpoint.  Frozen here as it behaves; plan step R5 owns it.
    """
    for day in (1, 15, 31):
        acc.add(RuleShape(
            f"long_cadence.monthly.dom{day:02d}",
            RecurrencePatternEnum.MONTHLY,
            day_of_month=day,
            long_cadence=True,
        ))
    cycles = (
        ("quarterly", RecurrencePatternEnum.QUARTERLY),
        ("semi_annual", RecurrencePatternEnum.SEMI_ANNUAL),
        ("annual", RecurrencePatternEnum.ANNUAL),
    )
    for name, pattern in cycles:
        for day in (1, 15):
            acc.add(RuleShape(
                f"long_cadence.{name}.moy01.dom{day:02d}",
                pattern,
                month_of_year=1,
                day_of_month=day,
                long_cadence=True,
            ))
    acc.add(RuleShape(
        "long_cadence.monthly_first",
        RecurrencePatternEnum.MONTHLY_FIRST,
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
    _add_horizon_bound_shapes(acc)
    _add_long_cadence_shapes(acc)
    return sorted(acc.shapes, key=lambda shape: shape.label)


def capture_shape(
    shape: RuleShape, periods: list[PayPeriod], cadence_days: int,
) -> list[str]:
    """Return the blob lines for one shape.

    Calls the two public entry points exactly as ``generate_for_template``
    does: :func:`~app.services.recurrence.rule_occurrences` over the whole
    schedule with no lower window bound, then ``compute_due_date`` per placed
    period.

    A shape that matches nothing emits one ``(none)`` line rather than
    disappearing.  A shape that vanished silently would be indistinguishable in
    the diff from a shape that was never captured, which is how a regression
    hides.

    **A period repeated in ``matched`` emits a repeated LINE**, and since plan
    step R4a that is reachable: at a cadence of 30 days or more several
    occurrences of one monthly bill land in one paycheck.  The repeats are
    byte-identical, because ``compute_due_date`` dates a row from its PERIOD
    rather than from its occurrence and so cannot tell them apart -- which is
    plan ledger row D18 made visible rather than a defect in this capture.
    The occurrence DATES those lines stand for are pinned independently, by
    ``tests/test_services/test_recurrence_occurrence.py``'s day-by-day sweep;
    a blob keyed on periods cannot carry them.

    Args:
        shape: The configuration to capture.
        periods: The schedule to capture it against.
        cadence_days: The cadence that schedule was generated at, which the
            calendar reads for the last period's end (plan step C2-b2).

    Returns:
        One line per matched period, or a single ``(none)`` line.
    """
    rule = build_shape_rule(shape)
    # The whole schedule, as plan step R4b-1 made explicit: the baseline has
    # always captured against the FULL period list, so building the calendar
    # from the same list is the same measurement.  There is no lower window
    # bound -- the anchor's own floor is ``PayCalendar.opening_bound()``, so
    # no walk can emit an occurrence placed before it.
    #
    # **Unplaced occurrences are dropped by ``placed_periods`` since plan step
    # R4b-2**, where the retired ``match_periods`` adapter dropped them, which
    # is why the blob did not move across that step.  They are NOT rare even on
    # the contiguous schedules this module builds: three shapes have one --
    # ``PERIOD_STARTING_ON_OR_AFTER`` cannot defer an occurrence dated after
    # the last payday onto anything.  A blob keyed on PERIODS cannot record
    # them, so ``tests/test_services/test_recurrence_occurrence.py``'s
    # ``_EXPECTED_UNPLACED`` names each one exactly and is what gates them.
    matched = _reading.placed_periods(
        _reading.rule_occurrences(
            rule, build_shape_calendar(periods, cadence_days),
        ),
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
        lines.extend(
            capture_shape(shape, long_cadence, LONG_CADENCE_DAYS)
            if shape.long_cadence
            else capture_shape(shape, biweekly, SCHEDULE_CADENCE_DAYS)
        )
    return "\n".join(lines) + "\n"
