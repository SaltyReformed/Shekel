"""Diff "which paycheck covers this day" against the reader it replaced.

Plan step **C2-f3a** of ``docs/plans/implementation_plan_pay_calendar.md``
DELETED ``pay_period_service.get_current_period`` and pointed its three ``app/``
call sites -- the Income Statement's default window, the transfer create form's
start-period selector, and the carry-forward target -- at
:meth:`app.services.pay_calendar.PayCalendar.period_containing`.

The retired reader was::

    SELECT ... FROM budget.pay_periods
     WHERE user_id = :uid AND start_date <= :day AND end_date >= :day
     LIMIT 1                       -- and NO ORDER BY (ledger row P19)

and it defaulted ``:day`` to the PROCESS clock (row **P49**).  The replacement
bisects a calendar derived from the owner's paydays, where a period's end is
``lead(start_date) - 1`` rather than a stored column.

This script is that cutover's real-data proof.  It answers three questions and
they are different:

* **the RULES agree on real paydays** -- over every day from the owner's first
  payday minus ``SPAN_DAYS`` to their last stored ``end_date`` plus
  ``SPAN_DAYS``, the retired SQL (transcribed in :func:`stored_reader`, driven
  over the STORED rows with their real ``end_date``) and
  ``period_containing`` name the same pay-period id, or both answer nothing.
  This is the cutover's safety property, and it can only hold while stored and
  derived agree -- which is plan finding **P1**, and is what **C4** ends by
  dropping the column;
* **the two CLOCKS agree today** -- ``date.today()`` and
  ``app.utils.dates.display_today()`` are compared, and the period each lands
  in is reported.  They differ for four hours a day in UTC and the deployed
  container pins ``TZ: America/New_York`` so that they do not; the point of
  reporting it is that the retired reader depended on that pin and the
  replacement does not;
* **the disagreement is REACHABLE** -- a firing control plants a wrong stored
  ``end_date`` on one period in memory and re-runs the day sweep, which MUST
  produce disagreements.  Without it a run over a healthy schedule reports zero
  because the two sides are equal, not because they were both driven.

**It never writes.**  Every row is loaded read-only, the planted column is
rolled back, and the run ends in a rollback regardless.  Run it against a clone
all the same.

**Usage** (from the repository root)::

    DATABASE_URL=postgresql://.../shekel \\
        .venv/bin/python tests/manual/verify_current_period_cutover.py report.json

Exit status is ``0`` only when every owner's day sweep agrees AND the firing
control fired for at least one owner.

This file is deliberately outside pytest's collection (``pytest.ini`` sets
``python_files = test_*.py``), like its ``verify_*`` siblings: it needs a
populated database chosen by the operator, not the seeded test template.  The
suite's own half of this proof -- the shapes a live database does not supply,
including a stored end that disagrees with the paydays -- is
``tests/test_routes/test_analytics.py::TestTheWindowIsAnsweredByTheDerivation``
and ``tests/test_services/test_pay_calendar_value.py``.
"""

import json
import pathlib
import sys
from datetime import date, timedelta

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so neither ``app`` nor ``tests`` is importable when this is run as
# ``.venv/bin/python tests/manual/verify_current_period_cutover.py``.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``app`` before it runs.
# pylint: disable=wrong-import-position
from app import create_app
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.user import User
from app.services import pay_calendar
from app.utils.dates import display_today

#: How far either side of the owner's stored coverage to probe.  Both ends of
#: the schedule have to be crossed: the retired reader answered ``None`` before
#: the first payday and after the last stored ``end_date``, and the derived
#: calendar answers ``None`` before the opening bound and after a horizon that
#: is projected from ``budget.pay_schedule.cadence_days``.  Those two horizons
#: are the one place the rules can legitimately part company.
SPAN_DAYS = 400


def stored_reader(periods, day):
    """Answer as the DELETED SQL did, over stored ``PayPeriod`` rows.

    A transcription of ``pay_period_service.get_current_period``: the first row
    whose stored span contains *day*.  Driven over the STORED rows with their
    real ``end_date`` deliberately -- the whole question is whether replacing a
    rule that reads that column with one that derives it moves an answer on
    real data.

    **The missing ``ORDER BY`` is reproduced as "first match in the list"**,
    which is the honest transcription: PostgreSQL's ``LIMIT 1`` with no order
    returns a plan-dependent row, and on a schedule where at most one period
    can contain a day the two are the same answer.  Where more than one
    contains it they need not be, and that is exactly what row **P19** is.

    Args:
        periods: The owner's ``PayPeriod`` rows, ``start_date`` ascending.
        day: The date to place.

    Returns:
        The matching row's ``id``, or ``None`` when no stored span covers
        *day*.
    """
    for period in periods:
        if period.start_date <= day <= period.end_date:
            return period.id
    return None


def _overlapping_days(periods):
    """Return the days more than one STORED span covers.

    Row **P19**'s subject, asked of the data rather than assumed absent: the
    retired reader's answer is plan-dependent exactly on these days.  A healthy
    schedule has none, and ``_reject_overlapping_batch`` used to be what made
    that true -- a guard plan step C3 deleted on the ground that the payday
    model makes an overlap unexpressible.

    Args:
        periods: The owner's ``PayPeriod`` rows.

    Returns:
        A sorted list of ISO days covered by two or more stored spans.
    """
    seen: dict[date, int] = {}
    for period in periods:
        day = period.start_date
        while day <= period.end_date:
            seen[day] = seen.get(day, 0) + 1
            day += timedelta(days=1)
    return sorted(d.isoformat() for d, n in seen.items() if n > 1)


def _sweep(periods, calendar, first, last):
    """Compare the two rules over every day in ``[first, last]``.

    Args:
        periods: The owner's ``PayPeriod`` rows, ``start_date`` ascending.
        calendar: The owner's derived
            :class:`~app.services.pay_calendar.PayCalendar`.
        first: The first day to probe.
        last: The last day to probe.

    Returns:
        ``(days_compared, disagreements)``, where each disagreement is a dict
        naming the day and both answers.
    """
    disagreements = []
    day, compared = first, 0
    while day <= last:
        compared += 1
        derived = calendar.period_containing(day)
        derived_id = None if derived is None else derived.period_id
        stored_id = stored_reader(periods, day)
        if derived_id != stored_id:
            disagreements.append({
                "day": day.isoformat(),
                "stored_reader": stored_id,
                "period_containing": derived_id,
            })
        day += timedelta(days=1)
    return compared, disagreements


def _clock_report(calendar):
    """Report which paycheck each of the two clocks lands in.

    Ledger row **P49**: every one of the retired reader's call sites took the
    process clock, and the three that replaced it take the owner's civil day.
    The two are equal wherever ``TZ`` is pinned to the display zone, so this
    reports rather than asserts -- what it catches is the run where they are
    NOT equal and the answers differ, which is the state the row is about.

    Args:
        calendar: The owner's derived
            :class:`~app.services.pay_calendar.PayCalendar`.

    Returns:
        A dict naming both days, both period ids, and whether they agree.
    """
    process_day, owner_day = date.today(), display_today()
    process = calendar.period_containing(process_day)
    owner = calendar.period_containing(owner_day)
    return {
        "process_day": process_day.isoformat(),
        "owner_day": owner_day.isoformat(),
        "process_period": None if process is None else process.period_id,
        "owner_period": None if owner is None else owner.period_id,
        "same_day": process_day == owner_day,
        "same_period": (
            (None if process is None else process.period_id)
            == (None if owner is None else owner.period_id)
        ),
    }


def _firing_control(periods, calendar, first, last):
    """Re-run the sweep with one stored ``end_date`` planted WRONG.

    The two rules agree on every healthy schedule, so a run reporting zero
    disagreements proves nothing on its own -- it is the same number a harness
    comparing one rule against itself would report.  Shortening the middle
    period's stored end by a week is the P1 disagreement made real, and the
    sweep MUST see it.

    **The plant is in memory and never flushed.**  The attribute is restored
    before returning and the caller rolls back regardless.

    Args:
        periods: The owner's ``PayPeriod`` rows, ``start_date`` ascending.
        calendar: The owner's derived calendar -- UNCHANGED by the plant, which
            is the point: the paydays did not move.
        first: The first day to probe.
        last: The last day to probe.

    Returns:
        The number of disagreements the plant produced.
    """
    if len(periods) < 3:
        return 0
    victim = periods[len(periods) // 2]
    original = victim.end_date
    victim.end_date = victim.start_date + timedelta(days=1)
    try:
        _compared, found = _sweep(periods, calendar, first, last)
    finally:
        victim.end_date = original
    return len(found)


def main(out_path):
    """Run the comparison for every owner with paydays and write the report.

    Args:
        out_path: Where to write the JSON report, or ``None`` for stdout only.

    Returns:
        The process exit status: ``0`` when every owner agrees and the firing
        control fired at least once.
    """
    app = create_app("development")
    owners, ok = [], True
    fired = False
    with app.app_context():
        try:
            for user in db.session.query(User).order_by(User.id).all():
                periods = (
                    db.session.query(PayPeriod)
                    .filter(PayPeriod.user_id == user.id)
                    .order_by(PayPeriod.start_date)
                    .all()
                )
                if not periods:
                    continue
                calendar = pay_calendar.calendar_for(user.id)
                first = periods[0].start_date - timedelta(days=SPAN_DAYS)
                last = max(p.end_date for p in periods) + timedelta(
                    days=SPAN_DAYS,
                )
                compared, disagreements = _sweep(
                    periods, calendar, first, last,
                )
                planted = _firing_control(periods, calendar, first, last)
                fired = fired or planted > 0
                ok = ok and not disagreements
                owners.append({
                    "user_id": user.id,
                    "paydays": len(periods),
                    "days_compared": compared,
                    "disagreements": disagreements,
                    "overlapping_stored_days": _overlapping_days(periods),
                    "firing_control_disagreements": planted,
                    "clock": _clock_report(calendar),
                })
        finally:
            db.session.rollback()

    report = {
        "owners": owners,
        "agreed": ok,
        "firing_control_fired": fired,
    }
    print(json.dumps(report, indent=2))
    if out_path is not None:
        pathlib.Path(out_path).write_text(
            json.dumps(report, indent=2), encoding="utf-8",
        )
    if not fired:
        print(
            "FAIL: the firing control produced no disagreement, so the two "
            "rules were not driven independently and the zero above means "
            "nothing.",
            file=sys.stderr,
        )
        return 2
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
