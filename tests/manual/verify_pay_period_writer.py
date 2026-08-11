"""Drive the pay-period WRITER against real data and diff what it moved.

Plan step **C3-b**.  ``pay_period_write`` stopped authoring ``end_date`` /
``period_index`` from cadence arithmetic and started materialising them from
``pay_calendar.derive_periods`` over the owner's whole payday set.  The columns
still exist and are still expected to agree, so the claim that makes the
cutover safe is a NEGATIVE one:

    on a healthy schedule the new writer moves NOTHING, and after every door
    every stored row equals the derivation over the owner's paydays.

``tests/manual/verify_pay_calendar_derivation.py`` proves the DERIVATION
against the stored columns; it cannot see this, because it never calls a
writer.  This script is the other half.

**It never commits.**  Every door runs inside a transaction this script rolls
back, and it says so in the blob (``committed: false``).  Run it against a
CLONE regardless -- the writer takes the per-user advisory lock, and a long
transaction against the live database would block real writes.

**Three doors, because they are the three SHAPES**, not because there are three
callers.  ``record_paydays`` is reached by generate, registration, extend and
the rolling top-up; ``retire_paydays`` by truncate, regenerate and reset.  What
differs between them is whether the payday set grows, shrinks, or neither:

* **append at cadence** -- the rolling top-up's exact shape, and the one that
  must move nothing.  A payday at ``latest + cadence`` leaves the previous
  last period's end where the derivation already had it.
* **retire the tail** -- the newly-last period's end falls back to the cadence
  projection.  On an on-cadence schedule that is where it already was; the
  DIFF is what says so rather than an argument that it must be.
* **re-record what exists** -- a batch naming only paydays already on the
  table.  It must create nothing, leave the cadence alone (finding **P12**),
  and still leave every row equal to its derivation.

**Usage** (from the repository root)::

    DATABASE_URL=postgresql://user:pass@host:5433/clone \\
        .venv/bin/python tests/manual/verify_pay_period_writer.py out.json

Exit status is 0 when every owner passed and 1 otherwise, so it is usable as a
gate; the blob carries the per-owner detail either way.
"""

import json
import os
import sys

from datetime import timedelta

# The script runs from the repository root, so ``app`` is importable; the
# manual verifiers share this preamble.
sys.path.insert(0, os.getcwd())

# pylint: disable=wrong-import-position
from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models.pay_period import PayPeriod  # noqa: E402
from app.exceptions import (  # noqa: E402
    PayPeriodCoverageWithdrawn,
    PayPeriodOverlapStored,
    ValidationError,
)
from app.services import pay_period_write, pay_schedule_service  # noqa: E402
from app.services.pay_calendar import PayCalendarError, derive_periods  # noqa: E402

#: Everything the writer's doors may legitimately refuse with.  Named so the
#: catch below is a set rather than a category (project rule 1).
_REFUSALS = (
    PayPeriodCoverageWithdrawn,
    PayPeriodOverlapStored,
    ValidationError,
    PayCalendarError,
)


def _snapshot(user_id):
    """Return ``{period_id: (payday, end_date, period_index)}`` for one owner."""
    return {
        period.id: (
            period.start_date.isoformat(),
            period.end_date.isoformat(),
            period.period_index,
        )
        for period in db.session.query(PayPeriod).filter_by(user_id=user_id)
    }


def _derivation_disagreements(user_id):
    """Return the rows whose stored columns differ from the derivation.

    The invariant plan step C4 rests on, evaluated after each door: if this is
    empty everywhere, dropping the two columns cannot change a figure.
    """
    periods = sorted(
        db.session.query(PayPeriod).filter_by(user_id=user_id),
        key=lambda period: period.start_date,
    )
    if not periods:
        return []
    derived = derive_periods(
        [(period.id, period.start_date) for period in periods],
        pay_schedule_service.resolve_cadence(user_id),
    )
    return [
        {
            "period_id": stored.id,
            "payday": stored.start_date.isoformat(),
            "stored_end": stored.end_date.isoformat(),
            "derived_end": expected.end_date.isoformat(),
            "stored_index": stored.period_index,
            "derived_index": expected.period_index,
        }
        for stored, expected in zip(periods, derived)
        if (stored.end_date != expected.end_date
            or stored.period_index != expected.period_index)
    ]


def _moved(before, after):
    """Return the rows whose stored columns changed between two snapshots."""
    return [
        {"period_id": period_id, "before": before[period_id], "after": values}
        for period_id, values in after.items()
        if period_id in before and before[period_id] != values
    ]


def _run_door(user_id, label, door):
    """Run one door in its own transaction, diff it, and roll it back.

    Args:
        user_id: The owner to drive.
        label: The door's name, for the blob.
        door: A zero-argument callable that performs the write.

    Returns:
        The per-door result dict.
    """
    before = _snapshot(user_id)
    result = {"door": label, "refused": None}
    try:
        door()
        db.session.flush()
    except _REFUSALS as exc:
        # The point of this script is to record what the doors do on real data,
        # INCLUDING refusing, so a refusal is a measurement rather than a crash.
        # The tuple is the writer's own refusal set rather than ``Exception``:
        # a bare catch here would report a genuine defect -- a bad query, a
        # constraint this run tripped -- as a tidy "refused" line in the blob,
        # which is the failure this instrument exists to detect.
        result["refused"] = f"{type(exc).__name__}: {exc}"
        db.session.rollback()
        return result
    after = _snapshot(user_id)
    result["created"] = len(set(after) - set(before))
    result["moved"] = _moved(before, after)
    result["disagreements"] = _derivation_disagreements(user_id)
    db.session.rollback()
    return result


def _verdict(owner):
    """Return the owner's pass/fail and why.

    Three predicates, one per door shape.  The append must move nothing and
    refuse nothing; the retire must be refused only for a stated reason; and no
    door may leave a stored row disagreeing with its derivation.
    """
    reasons = []
    for door in owner["doors"]:
        if door["refused"] is not None:
            reasons.append(f"{door['door']} was refused: {door['refused']}")
            continue
        if door["disagreements"]:
            reasons.append(
                f"{door['door']} left {len(door['disagreements'])} row(s) "
                f"disagreeing with the derivation"
            )
        if door["door"] == "append_at_cadence" and door["moved"]:
            reasons.append(
                f"append_at_cadence moved {len(door['moved'])} existing row(s); "
                f"an on-cadence append must move none"
            )
        if door["door"] == "rerecord_existing" and door["created"]:
            reasons.append(
                f"rerecord_existing created {door['created']} row(s); a batch "
                f"naming only existing paydays must create none"
            )
    return (not reasons), reasons


def main(out_path):
    """Drive every payday-owning user through the three door shapes."""
    app = create_app()
    report = {"committed": False, "owners": []}
    with app.app_context():
        user_ids = [
            row[0]
            for row in db.session.query(PayPeriod.user_id).distinct().all()
        ]
        for user_id in sorted(user_ids):
            periods = sorted(
                db.session.query(PayPeriod).filter_by(user_id=user_id),
                key=lambda period: period.start_date,
            )
            cadence = pay_schedule_service.resolve_cadence(user_id)
            latest = periods[-1].start_date
            owner = {
                "user_id": user_id,
                "paydays": len(periods),
                "first_payday": periods[0].start_date.isoformat(),
                "latest_payday": latest.isoformat(),
                "cadence_days": cadence,
                "doors": [
                    _run_door(
                        user_id, "append_at_cadence",
                        lambda uid=user_id, day=latest, cad=cadence: (
                            pay_period_write.record_paydays(
                                uid, day + timedelta(days=cad), 1, cad,
                            )
                        ),
                    ),
                    _run_door(
                        user_id, "retire_tail",
                        lambda uid=user_id, rows=periods: (
                            pay_period_write.retire_paydays(
                                uid, rows, rows[-1:],
                            )
                        ),
                    ),
                    _run_door(
                        user_id, "rerecord_existing",
                        lambda uid=user_id, day=periods[0].start_date,
                        count=len(periods), cad=cadence: (
                            pay_period_write.record_paydays(
                                uid, day, count, cad,
                            )
                        ),
                    ),
                ],
            }
            owner["passed"], owner["reasons"] = _verdict(owner)
            report["owners"].append(owner)
    report["passed"] = all(owner["passed"] for owner in report["owners"])
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    for owner in report["owners"]:
        print(
            f"user {owner['user_id']}: {owner['paydays']} paydays, cadence "
            f"{owner['cadence_days']} -- "
            f"{'PASS' if owner['passed'] else 'FAIL: ' + '; '.join(owner['reasons'])}"
        )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "writer_verify.json"))
