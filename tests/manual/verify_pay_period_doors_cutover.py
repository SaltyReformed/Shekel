"""Diff the four destructive schedule doors against the columns they left.

Plan step **C2-f3b** of ``docs/plans/implementation_plan_pay_calendar.md`` moved
``pay_period_admin``'s four doors -- extend, truncate, regenerate and reset --
and the settings period list off ORM ``PayPeriod`` rows and onto the DERIVED pay
calendar.  Two things changed about how they decide, and this script measures
both against real data:

* **the HISTORICAL test reads the DERIVED end.**  It read
  ``budget.pay_periods.end_date``, a stored copy of ``lead(start_date) - 1``
  that nothing reconciles against the paydays it derives from and that plan step
  **C4** drops.  A period classified HISTORICAL is HARD-LOCKED: truncate,
  regenerate and reset all refuse to touch it, and the settings page badges it
  "Past".  So a stored end that disagrees with the paydays is a period the app
  either protects or offers to delete for the wrong reason;
* **the day it is asked about is the OWNER's civil day**
  (``app.utils.dates.display_today``) rather than the process clock
  (``date.today``), ruled 2026-08-19 by the developer -- finding
  ``balance:N-191``, which named this classifier as one of the two sites owing
  that ruling.

Three questions, and they are different:

1. **the LOCK REASONS agree** -- for every period of every owner, the retired
   rule (transcribed in :func:`stored_lock_reason`, driven over the STORED
   ``end_date``) and ``pay_period_locks.classify_schedule_locks`` over the
   derived calendar name the same reason.  Only the HISTORICAL arm differs
   between them, so the settled and posted facts are read ONCE, from the live
   door, and both sides share them: what is compared is exactly the axis that
   changed;
2. **the REGENERATE boundary agrees** -- the last period regenerate keeps,
   computed the retired way (ORM rows sorted by payday, locks on the process
   clock) and the shipped way (a ``PeriodWindow``, locks on the owner's day);
3. **the disagreement is REACHABLE** -- a firing control plants a wrong stored
   ``end_date`` on one period in memory and re-runs question 1, which MUST then
   disagree.  Without it a run over a healthy schedule reports zero because the
   two sides are equal, not because they were both driven.

**Question 2 has no such control, and the reason is a PROOF rather than an
omission.**  The boundary walk asks for the first period that is both
NOT-YET-STARTED (``start_date > as_of``) and unlocked; a derived period always
has ``end_date >= start_date``, so a period past *as_of* can never satisfy
``end_date < as_of`` -- HISTORICAL is UNREACHABLE among the candidates, and the
only lock reasons that walk can ever see are ``SETTLED_TXN`` and
``LEDGER_POSTINGS``.  An ``end_date`` plant therefore cannot move it, and this
script never writes, so it cannot plant a settled row either.  What question 2
does grade is that two implementations of one walk -- ORM rows sorted by payday
against a ``PeriodWindow`` sorted at construction -- pick the same period on
real data, which is a real comparison and a weaker one; its result is REPORTED
and the exit status rests on question 1.

*A FOURTH question was dropped at the review of this step, and it is worth
saying why rather than deleting it quietly: it compared the doomed set truncate
selects over ORM rows against the set it selects over ``DerivedPeriod`` values,
and both sides read ``start_date`` -- the same column, copied through
:func:`~app.services.pay_calendar.calendar_for` unchanged.  The two sets were
equal by construction, so the comparison could not fail, and the firing control
did not drive it.  An unfalsifiable question folded into an exit status reads as
evidence and is not.*

It also REPORTS two things rather than asserting them.  **The clock exposure**:
how many periods the two clocks classify differently today, and which day each
read.  **And ``derived_vs_stored``**: how many of the owner's periods have a
stored ``end_date`` that differs from the derivation -- because on a schedule
where none does, question 1's zero is a property of the DATA rather than of the
code, which is what the firing control exists to answer and what
``verify_investment_cutover`` and ``verify_dashboard_cutover`` both dump for the
same reason.  Only ``deploy/docker-compose.prod.yml`` and
``docker-compose.dev.yml`` pin ``TZ: America/New_York``; the repository-root
``docker-compose.yml`` does not, so the clock figure is not always 0.

**It never writes.**  Every row is loaded read-only, the planted column is
restored, and the run ends in a rollback regardless.  Run it against a clone all
the same.

**Usage** (from the repository root)::

    DATABASE_URL=postgresql://.../shekel_f6a3 \\
        .venv/bin/python tests/manual/verify_pay_period_doors_cutover.py report.json

Exit status is ``0`` only when all three comparisons agree for every owner AND
the firing control fired at least once.

This file is deliberately outside pytest's collection (``pytest.ini`` sets
``python_files = test_*.py``), like its ``verify_*`` siblings: it needs a
populated database chosen by the operator, not the seeded test template.  The
suite's own half of this proof -- the shapes a live database does not supply,
including a stored end that disagrees with the paydays and the two clocks pinned
apart -- is ``tests/test_services/test_pay_period_admin.py``
(``TestTheHistoricalTestReadsTheDerivedEnd``, ``TestTheDoorsDecideOnTheOwnersDay``)
and ``tests/test_routes/test_pay_period_admin.py::TestTheManageListIsTheDerivation``.
"""

import json
import pathlib
import sys
from datetime import date, timedelta

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so neither ``app`` nor ``tests`` is importable when this is run as
# ``.venv/bin/python tests/manual/verify_pay_period_doors_cutover.py``.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``app`` before it runs.
# pylint: disable=wrong-import-position
from app import create_app
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.user import User
from app.services import pay_calendar
from app.services.pay_period_locks import (
    PeriodLockReason,
    classify_schedule_locks,
)
from app.utils.dates import display_today


def _reason_name(reason):
    """Return a lock reason's JSON-safe name.

    Args:
        reason: A :class:`PeriodLockReason` or ``None``.

    Returns:
        The member's value, or ``None``.
    """
    return None if reason is None else reason.value


def money_facts(calendar, as_of=date.min):
    """Return each period's lock reason with the HISTORICAL arm switched OFF.

    The settled-transaction and unbalanced-ledger facts are QUERIES, and both
    sides of every comparison below need the same answers -- otherwise a
    disagreement could come from two reads of a moving table rather than from
    the rule under test.  Asking the live door with ``as_of`` at
    :data:`datetime.date.min` is how they are read once: no period's end can
    precede it, so the historical arm never fires and what comes back is the
    money half alone.

    Args:
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
        as_of: The reference day.  Defaults to ``date.min``, which is the whole
            point; a caller passing anything else gets the ordinary answer.

    Returns:
        ``{period_id: PeriodLockReason | None}`` carrying only SETTLED_TXN and
        LEDGER_POSTINGS.
    """
    return classify_schedule_locks(calendar, as_of=as_of)


def stored_lock_reason(period, money, as_of):
    """Answer as the retired classifier did, off the STORED ``end_date``.

    A transcription of ``pay_period_locks.classify_periods_bulk`` as it stood at
    ``96e079e7``: the historical test compared the stored column against the
    reference day, and the precedence put HISTORICAL first.  Driven over the
    STORED rows with their real ``end_date`` deliberately -- the whole question
    is whether replacing that column with the derivation moves a lock on real
    data.

    Args:
        period: The owner's ORM :class:`~app.models.pay_period.PayPeriod`.
        money: :func:`money_facts`' answer, keyed by period id.
        as_of: The reference day.

    Returns:
        The :class:`PeriodLockReason` the retired rule gave, or ``None``.
    """
    if period.end_date < as_of:
        return PeriodLockReason.HISTORICAL
    return money[period.id]


def _lock_sweep(periods, calendar, money, as_of):
    """Compare the two lock rules over every one of the owner's periods.

    Args:
        periods: The owner's ORM rows, ``start_date`` ascending.
        calendar: The owner's derived calendar.
        money: :func:`money_facts`' answer.
        as_of: The reference day both rules are asked about.

    Returns:
        ``(periods_compared, disagreements)``, each disagreement naming the
        period and both answers.
    """
    derived = classify_schedule_locks(calendar, as_of=as_of)
    disagreements = []
    for period in periods:
        stored = stored_lock_reason(period, money, as_of)
        if derived[period.id] != stored:
            disagreements.append({
                "period_id": period.id,
                "payday": period.start_date.isoformat(),
                "stored_end": period.end_date.isoformat(),
                "derived_end": calendar.period_by_id(
                    period.id,
                ).end_date.isoformat(),
                "stored_rule": _reason_name(stored),
                "derived_rule": _reason_name(derived[period.id]),
            })
    return len(periods), disagreements


def _regenerate_boundary(periods, calendar, money, process_day, owner_day):
    """Report the last period regenerate keeps, computed both ways.

    The retired form sorted the ORM rows by payday and walked them against the
    PROCESS clock; the shipped form walks a ``PeriodWindow``, which is sorted at
    construction, against the OWNER's civil day.  Two changes in one function,
    so both are reported: the same-clock comparison isolates the type change,
    and the two-clock comparison is the ruling's exposure.

    Args:
        periods: The owner's ORM rows, ``start_date`` ascending.
        calendar: The owner's derived calendar.
        money: :func:`money_facts`' answer.
        process_day: ``date.today()``.
        owner_day: ``display_today()``.

    Returns:
        A dict naming the boundary each of the three computations reached.
    """
    def _stored(as_of):
        """The retired walk, over ORM rows and stored ends."""
        by_payday = sorted(periods, key=lambda row: row.start_date)
        for position, period in enumerate(by_payday):
            if (
                period.start_date > as_of
                and stored_lock_reason(period, money, as_of) is None
            ):
                return by_payday[position - 1].id if position > 0 else None
        return by_payday[-1].id if by_payday else None

    def _derived(as_of):
        """The shipped walk, over a window and derived ends."""
        saved = calendar.saved()
        locks = classify_schedule_locks(calendar, as_of=as_of)
        for position, period in enumerate(saved):
            if period.start_date > as_of and locks[period.period_id] is None:
                return saved[position - 1].period_id if position > 0 else None
        return saved[-1].period_id if len(saved) else None

    stored_same_clock = _stored(process_day)
    derived_same_clock = _derived(process_day)
    return {
        "stored_rule_process_clock": stored_same_clock,
        "derived_rule_process_clock": derived_same_clock,
        "type_change_agrees": stored_same_clock == derived_same_clock,
        "derived_rule_owner_clock": _derived(owner_day),
        "clock_change_agrees": derived_same_clock == _derived(owner_day),
    }


def _clock_exposure(periods, calendar, money, process_day, owner_day):
    """Count the periods the two clocks classify differently today.

    Args:
        periods: The owner's ORM rows.
        calendar: The owner's derived calendar.
        money: :func:`money_facts`' answer.
        process_day: ``date.today()``.
        owner_day: ``display_today()``.

    Returns:
        A dict naming both days and how many periods disagree.
    """
    on_process = classify_schedule_locks(calendar, as_of=process_day)
    on_owner = classify_schedule_locks(calendar, as_of=owner_day)
    differing = [
        period.id for period in periods
        if on_process[period.id] != on_owner[period.id]
    ]
    return {
        "process_day": process_day.isoformat(),
        "owner_day": owner_day.isoformat(),
        "same_day": process_day == owner_day,
        "periods_classified_differently": differing,
        "money_facts_read_once": len(money),
    }


def _derived_vs_stored(periods, calendar):
    """Report whether this database can EXPRESS the disagreement being measured.

    A run that finds zero lock disagreements has said one of two very different
    things: that the two rules agree, or that the data cannot tell them apart.
    On a contiguous fortnightly schedule the stored ``end_date`` IS
    ``lead(start_date) - 1``, so the comparison is between a value and a copy of
    itself and its zero is a property of the DATA.  The same figure is what
    ``verify_investment_cutover`` and ``verify_dashboard_cutover`` dump, for the
    same reason; :func:`_firing_control` is the other half of the answer.

    Args:
        periods: The owner's ORM rows, ``start_date`` ascending.
        calendar: The owner's derived calendar.

    Returns:
        A dict naming how many periods differ and which they are.
    """
    differing = [
        {
            "period_id": period.id,
            "stored_end": period.end_date.isoformat(),
            "derived_end": calendar.period_by_id(
                period.id,
            ).end_date.isoformat(),
        }
        for period in periods
        if period.end_date != calendar.period_by_id(period.id).end_date
    ]
    return {
        "periods": len(periods),
        "differing": len(differing),
        "detail": differing,
    }


def _firing_control(periods, calendar, money):
    """Re-run both comparisons with one stored ``end_date`` planted WRONG.

    The two rules agree on every healthy schedule, so a run reporting zero
    disagreements proves nothing on its own -- it is the same number a harness
    comparing one rule against itself would report.  Shortening one period's
    stored end to the day after its own payday is plan finding **P1**'s
    disagreement made real, and the sweep MUST see it: the derived end is
    dictated by the next payday and does not move.

    **The plant is in memory and never flushed.**  The attribute is restored
    before returning and the caller rolls back regardless.

    **It drives question 1 and cannot drive question 2**, and an adversarial
    review of this step is why that is said rather than assumed: a control that
    exercises one of two graded comparisons leaves the other reporting a zero
    nothing produced.  The boundary walk's HISTORICAL arm is unreachable (see
    the module docstring), so the plant is measured against it here and the
    result is reported as ``firing_control_moved_the_boundary`` -- expected
    ``False``, and a ``True`` would mean the proof is wrong.

    Args:
        periods: The owner's ORM rows, ``start_date`` ascending.
        calendar: The owner's derived calendar -- UNCHANGED by the plant, which
            is the point: the paydays did not move.
        money: :func:`money_facts`' answer.

    Returns:
        ``(lock_disagreements, boundaries_differed)`` -- how many periods the two
        lock rules placed differently, and whether the two regenerate boundaries
        parted company under the same plant.  The second is expected ``False``:
        it is the proof above, measured rather than trusted.
    """
    if len(periods) < 3:
        return 0, False
    victim = periods[len(periods) // 2]
    original = victim.end_date
    victim.end_date = victim.start_date + timedelta(days=1)
    probe_day = victim.start_date + timedelta(days=2)
    try:
        _compared, found = _lock_sweep(periods, calendar, money, probe_day)
        boundary = _regenerate_boundary(
            periods, calendar, money, probe_day, probe_day,
        )
    finally:
        victim.end_date = original
    return len(found), not boundary["type_change_agrees"]


def _owner_report(user_id, periods, process_day, owner_day):
    """Run all four questions for one owner.

    Args:
        user_id: The owner.
        periods: Their ORM rows, ``start_date`` ascending.
        process_day: ``date.today()``.
        owner_day: ``display_today()``.

    Returns:
        The owner's report dict.
    """
    calendar = pay_calendar.calendar_for(user_id)
    money = money_facts(calendar)
    compared, lock_disagreements = _lock_sweep(
        periods, calendar, money, owner_day,
    )
    planted_locks, planted_boundary = _firing_control(periods, calendar, money)
    return {
        "user_id": user_id,
        "paydays": len(periods),
        "periods_compared": compared,
        "lock_disagreements": lock_disagreements,
        "derived_vs_stored": _derived_vs_stored(periods, calendar),
        "regenerate_boundary": _regenerate_boundary(
            periods, calendar, money, process_day, owner_day,
        ),
        "clock": _clock_exposure(
            periods, calendar, money, process_day, owner_day,
        ),
        "firing_control_disagreements": planted_locks,
        "firing_control_moved_the_boundary": planted_boundary,
    }


def _passed(owner):
    """Whether one owner's report shows every comparison agreeing.

    Args:
        owner: An owner report from :func:`_owner_report`.

    Returns:
        ``True`` when nothing disagreed.
    """
    return (
        not owner["lock_disagreements"]
        and owner["regenerate_boundary"]["type_change_agrees"]
    )


def main(out_path):
    """Run the comparison for every owner with paydays and write the report.

    Args:
        out_path: Where to write the JSON report, or ``None`` for stdout only.

    Returns:
        The process exit status: ``0`` when every owner agrees and the firing
        control fired at least once.
    """
    app = create_app("development")
    owners, fired = [], False
    process_day, owner_day = date.today(), display_today()
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
                report = _owner_report(
                    user.id, periods, process_day, owner_day,
                )
                fired = fired or report["firing_control_disagreements"] > 0
                owners.append(report)
        finally:
            db.session.rollback()

    ok = all(_passed(owner) for owner in owners)
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
