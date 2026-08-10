"""Diff the FILING rule against the chain it replaced, on real data.

Plan step **C2-d** of ``docs/plans/implementation_plan_pay_calendar.md``
deleted ``loan_ledger.find_period_containing_date`` composed with
``resolve_anchor_pay_period`` -- containment, else the latest period ENDING
before the day, else the earliest -- and pointed both anchor-correction posting
writers at :meth:`app.services.pay_calendar.PayCalendar.filing_period`, which
is ONE clamp: the latest MATERIALISED period STARTING on or before the day,
else the earliest.

This script is that cutover's real-data proof, and it exists because the
docstrings had cited production numbers no artifact in the repository could
reproduce.  **If you cannot re-run it, do not quote it.**

It answers two questions, and they are different:

* **the RULES agree** -- over every day from the owner's first payday minus
  ``SPAN_DAYS`` to their last stored ``end_date`` plus ``SPAN_DAYS``, the
  deleted chain (transcribed in :func:`chain`, driven over the STORED rows with
  their real ``end_date`` and ``period_index``) and the shipped clamp (driven
  over the DERIVED calendar) name the same period id.  This is the cutover's
  safety property;
* **the POSTED ledger agrees** -- every ``loan_opening`` / ``loan_trueup`` /
  ``account_opening`` / ``account_trueup`` journal entry's stored
  ``pay_period_id`` is compared against what the clamp gives for its own
  ``entry_date``.  A row that differs is NOT necessarily a cutover defect: the
  stored value can be a stale key the reconcile has already reversed to zero,
  which is why each mismatch is reported with what the OLD chain says too.
  Old-and-new agreeing while the STORED value differs means the row predates a
  fix and self-heals; old-and-new disagreeing is a real cutover move.

**Measured 2026-08-10 against ``shekel-prod-db``** (the numbers four docstrings
quote): 1 owner, 61 paydays, **1,654 days compared, 0 rule disagreements**;
**134 posted anchor corrections, 0 that the two rules place differently**, and
2 ``account_trueup`` rows whose STORED period differs from BOTH rules -- the
pair ``account_posting_service/_anchors.py`` records from before plan step
X-ai-r, each netting ``$0.00``.

**It runs a FIRING CONTROL and the run is not valid without it.**  Both rules
agree on every well-formed schedule, so a harness that compared two copies of
the same thing would also report zero.  The control re-runs the comparison with
the chain's index reduction REVERSED (lowest index rather than highest), which
must produce disagreements on any multi-period owner -- if it does not, the two
sides are not really being driven independently.

**It never writes.**  Every row is loaded read-only and the run ends in a
rollback regardless.  Run it against a clone all the same.

**Usage** (from the repository root)::

    DATABASE_URL=postgresql://.../shekel \\
        .venv/bin/python tests/manual/verify_filing_cutover.py report.json

Exit status is ``0`` only when every owner's two comparisons pass AND the
firing control fired for at least one owner.

This file is deliberately outside pytest's collection (``pytest.ini`` sets
``python_files = test_*.py``), like its ``verify_*`` siblings: it needs a
populated database chosen by the operator, not the seeded test template.  The
suite's own half of this proof -- the shapes a live database does not supply,
including the gapped and index-scrambled ones -- is
``tests/test_services/test_pay_calendar_value.py``.
"""

import json
import pathlib
import sys
from datetime import timedelta

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so neither ``app`` nor ``tests`` is importable when this is run as
# ``.venv/bin/python tests/manual/verify_filing_cutover.py``.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``app`` before it runs.
# pylint: disable=wrong-import-position
from app import create_app
from app.extensions import db
from app.models.journal_entry import JournalEntry
from app.models.pay_period import PayPeriod
from app.models.user import User
from app import ref_cache
from app.enums import PostingSourceEnum
from app.services import pay_calendar

#: How far either side of the owner's stored coverage to probe.  Both the
#: pre-schedule clamp and the past-horizon fallback have to be crossed, and
#: production's own out-of-schedule entries sit years before the first payday.
SPAN_DAYS = 400

#: The four journal source kinds whose ``pay_period_id`` this rule decides.
ANCHOR_SOURCES = (
    PostingSourceEnum.LOAN_OPENING,
    PostingSourceEnum.LOAN_TRUEUP,
    PostingSourceEnum.ACCOUNT_OPENING,
    PostingSourceEnum.ACCOUNT_TRUEUP,
)


def chain(periods, day, reduce_by_max_index=True):
    """Answer as the DELETED chain did, over stored ``PayPeriod`` rows.

    A transcription of ``find_period_containing_date`` composed with
    ``resolve_anchor_pay_period``: the period CONTAINING *day*, else the latest
    period whose stored ``end_date`` precedes it, else ``periods[0]``.  Both
    fallbacks reduce by ``period_index`` and the last resort is list position
    in index order -- which is the half of the equivalence precondition that an
    earlier draft of this step's prose left unstated.

    Driven over the STORED rows deliberately, with their real ``end_date``: the
    whole question is whether replacing a rule that reads an end with one that
    does not moves an answer on real data.

    Args:
        periods: The owner's ``PayPeriod`` rows, ``period_index`` ascending.
        day: The date to file.
        reduce_by_max_index: The FIRING CONTROL.  ``False`` reverses both
            reductions to take the LOWEST index, which is a wrong rule and must
            produce disagreements.

    Returns:
        The chosen row's ``id``, or ``None`` for an empty *periods*.
    """
    if not periods:
        return None
    better = (
        (lambda candidate, best: candidate.period_index > best.period_index)
        if reduce_by_max_index
        else (lambda candidate, best: candidate.period_index < best.period_index)
    )
    containing, fallback = None, None
    for period in periods:
        if period.start_date <= day <= period.end_date:
            if containing is None or better(period, containing):
                containing = period
        elif period.end_date < day:
            if fallback is None or better(period, fallback):
                fallback = period
    located = containing if containing is not None else fallback
    return (located if located is not None else periods[0]).id


def _stored_periods(user_id):
    """Return the owner's pay periods, ``period_index`` ascending.

    The ordering the deleted ``owner_pay_periods`` used, so the transcription
    above receives exactly what the chain received in production.

    Args:
        user_id: The owning user.

    Returns:
        The list of :class:`~app.models.pay_period.PayPeriod` rows.
    """
    return (
        db.session.query(PayPeriod)
        .filter(PayPeriod.user_id == user_id)
        .order_by(PayPeriod.period_index)
        .all()
    )


def _compare_days(periods, calendar):
    """Compare the two rules on every day around the owner's coverage.

    Args:
        periods: The owner's stored rows.
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.

    Returns:
        ``(days_compared, disagreements, control_fired)`` -- *disagreements* a
        list of ``{day, chain, clamp}`` dicts, and *control_fired* whether the
        reversed-reduction control produced at least one difference.
    """
    day = min(p.start_date for p in periods) - timedelta(days=SPAN_DAYS)
    last = max(p.end_date for p in periods) + timedelta(days=SPAN_DAYS)
    compared, disagreements, control_fired = 0, [], False
    while day <= last:
        clamp = calendar.filing_period(day).period_id
        if chain(periods, day) != clamp:
            disagreements.append({
                "day": day.isoformat(),
                "chain": chain(periods, day),
                "clamp": clamp,
            })
        if chain(periods, day, reduce_by_max_index=False) != clamp:
            control_fired = True
        compared += 1
        day += timedelta(days=1)
    return compared, disagreements, control_fired


def _compare_entries(user_id, periods, calendar):
    """Compare every posted anchor correction's stored period against the rules.

    Args:
        user_id: The owning user.
        periods: The owner's stored rows.
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.

    Returns:
        ``(entry_count, rules_disagree, stored_differs)`` -- the second a list
        of entries the two RULES place differently (a real cutover move), the
        third a list where only the STORED value differs (a stale key the
        reconcile self-heals; reported, not failed).
    """
    source_ids = [
        ref_cache.posting_source_id(source) for source in ANCHOR_SOURCES
    ]
    entries = (
        db.session.query(JournalEntry)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.source_kind_id.in_(source_ids),
        )
        .order_by(JournalEntry.id)
        .all()
    )
    rules_disagree, stored_differs = [], []
    for entry in entries:
        clamp = calendar.filing_period(entry.entry_date).period_id
        old = chain(periods, entry.entry_date)
        record = {
            "entry_id": entry.id,
            "entry_date": entry.entry_date.isoformat(),
            "stored": entry.pay_period_id,
            "chain": old,
            "clamp": clamp,
        }
        if old != clamp:
            rules_disagree.append(record)
        elif entry.pay_period_id != clamp:
            stored_differs.append(record)
    return len(entries), rules_disagree, stored_differs


def _user_blob(user_id):
    """Measure one owner.

    Args:
        user_id: The owning user.

    Returns:
        The owner's result dict.  ``measured`` is ``False`` for an owner with
        no paydays -- the companion role holds none by design, and reporting
        success for one would be reporting success for nothing.
    """
    periods = _stored_periods(user_id)
    if not periods:
        return {"user_id": user_id, "measured": False, "period_count": 0}

    calendar = pay_calendar.calendar_for(user_id)
    days, disagreements, control_fired = _compare_days(periods, calendar)
    entry_count, rules_disagree, stored_differs = _compare_entries(
        user_id, periods, calendar,
    )
    return {
        "user_id": user_id,
        "measured": True,
        "period_count": len(periods),
        "first_payday": periods[0].start_date.isoformat(),
        "days_compared": days,
        "rule_disagreements": disagreements,
        "control_fired": control_fired,
        "entries_compared": entry_count,
        "entries_the_rules_place_differently": rules_disagree,
        "entries_whose_stored_period_is_stale": stored_differs,
    }


def _summarise(blobs):
    """Print the verdict and return the process exit code.

    Args:
        blobs: The per-owner results.

    Returns:
        ``0`` when every measured owner agrees on both comparisons AND the
        firing control fired somewhere; ``1`` otherwise.
    """
    measured = [b for b in blobs if b["measured"]]
    failed = False
    for blob in measured:
        print(
            f"user {blob['user_id']}: {blob['period_count']} paydays, "
            f"{blob['days_compared']} days, "
            f"{len(blob['rule_disagreements'])} rule disagreements; "
            f"{blob['entries_compared']} entries, "
            f"{len(blob['entries_the_rules_place_differently'])} placed "
            f"differently, "
            f"{len(blob['entries_whose_stored_period_is_stale'])} with a "
            f"stale stored period"
        )
        if blob["rule_disagreements"]:
            failed = True
        if blob["entries_the_rules_place_differently"]:
            failed = True

    if not measured:
        print("FAIL: no owner had any pay period; this run proves nothing")
        return 1
    if not any(b["control_fired"] for b in measured):
        print(
            "FAIL: the firing control never fired -- the two sides are not "
            "being driven independently, so agreement means nothing"
        )
        return 1
    print("PASS" if not failed else "FAIL")
    return 1 if failed else 0


def main(out_path):
    """Write the comparison blob for the configured database to *out_path*.

    Args:
        out_path: Where to write the JSON blob.

    Returns:
        The process exit code (see :func:`_summarise`).
    """
    app = create_app()
    with app.app_context():
        try:
            blobs = [
                _user_blob(user.id)
                for user in db.session.query(User).order_by(User.id).all()
            ]
        finally:
            # Nothing here writes; this is the belt to that braces, so a future
            # edit cannot make the harness mutate the database it measures.
            db.session.rollback()

    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(blobs, handle, indent=1, sort_keys=True)
    print(f"wrote {out_path}: {len(blobs)} users")
    return _summarise(blobs)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(
            "usage: verify_filing_cutover.py <output.json>  "
            "(DATABASE_URL selects the database)"
        )
    raise SystemExit(main(sys.argv[1]))
