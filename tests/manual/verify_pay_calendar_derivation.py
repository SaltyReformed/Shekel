"""Diff the derived pay calendar against the stored columns, on real data.

Plan step C1 of ``docs/plans/implementation_plan_pay_calendar.md``: the
derivation that is to replace ``budget.pay_periods.end_date`` and
``period_index`` must be proven equal to them BEFORE anything reads it, writes
it, or drops them.  This script is that proof's real-data half.  It loads every
user's complete payday set and their ``budget.pay_schedule`` cadence, drives
:func:`app.services.pay_calendar.derive_periods` over them, and writes one JSON
blob holding every row's stored and derived values side by side.

**It runs TWO controls, and the run is not valid without them.**  Byte-identity
over a clean schedule proves only that the harness reads what it reads, and
production's schedule is regular enough to hide a second thing as well:

* the PAYDAY control relocates one payday in memory
  (``tests.oracles.pay_calendar_derivation.perturb``) and requires the
  comparator to report the shifted indices and ends;
* the CADENCE control re-derives at a neighbouring cadence and requires exactly
  ONE end -- the last -- to move by exactly one day.  Measured during this
  step's review: a derivation that computed EVERY end as
  ``start + cadence - 1``, the pre-normalization defect, reproduces the clone
  byte-identically and passes the payday control.  This is what sees it.

The pass/fail rule lives in the oracle
(``tests.oracles.pay_calendar_derivation.verdict``), not here, because it had
two defects when it lived in this file and a script outside pytest's collection
cannot be tested.  This module is I/O.

**It never writes.**  Every row it compares is loaded read-only, and the
perturbed schedule is built from UNSAVED ``PayPeriod`` copies that are never
added to the session; the run ends in a rollback regardless.  Run it against a
clone all the same -- the developer's clones are ``shekel`` and
``shekel_f3_final`` on ``shekel-dev-db``.

**The one thing it cannot prove, stated so the blob is not over-read.**  For a
user with paydays but NO ``budget.pay_schedule`` row, the cadence has to be
inferred from the last period's own length
(``pay_schedule_service.resolve_cadence``), so the LAST row's end reproduces
itself by arithmetic and agreement there means nothing (plan finding P8).  Only
that one row is tainted -- every earlier end derives from the next payday and
never reads the cadence -- so the verdict disqualifies the row, not the user.
No such user exists on production today (the single owner carries a cadence-14
row), but registration writes a bootstrap payday and no schedule row at all, so
the state is one signup away.

**Usage** (from the repository root)::

    DATABASE_URL=postgresql://.../shekel \\
        .venv/bin/python tests/manual/verify_pay_calendar_derivation.py before.json
    # ... make the change ...
    DATABASE_URL=postgresql://.../shekel \\
        .venv/bin/python tests/manual/verify_pay_calendar_derivation.py after.json
    diff before.json after.json

Plan step C3 rewrites the writer while the columns are still present and still
expected to agree, so THIS blob staying byte-identical across that commit is
what makes the cutover provably behaviour-preserving.  For the before side of
such a diff use ``git worktree add``, never ``git checkout`` -- the latter
reverts the working tree and discards the change under test.

Exit status is ``0`` only when every measured user passes
:func:`~tests.oracles.pay_calendar_derivation.verdict` AND at least one user's
payday control was applicable -- a database of nothing but one-payday accounts
proves nothing and must not report success.

This file is deliberately outside pytest's collection (``pytest.ini`` sets
``python_files = test_*.py``), like its ``verify_*`` siblings here: it needs a
populated database chosen by the operator, not the seeded test template.
"""

import json
import pathlib
import sys

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so neither ``app`` nor ``tests`` is importable when this is run as
# ``.venv/bin/python tests/manual/verify_pay_calendar_derivation.py``.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``app`` before it runs.
# pylint: disable=wrong-import-position
from app import create_app
from app.extensions import db
from app.models.user import User
from app.services import pay_period_service, pay_schedule_service
from tests.oracles.pay_calendar_derivation import (
    cadence_control,
    compare,
    identified_paydays,
    perturb,
    verdict,
)


def _resolve_cadence(user_id):
    """Return ``(cadence_days, cadence_is_stored)`` for *user_id*.

    Prefers the user's stored ``budget.pay_schedule.cadence_days``.  Falls back
    to ``pay_schedule_service.resolve_cadence``, which infers the cadence from
    the last period's length -- and flags that, because the inference reads
    back the very value the derivation is being asked to produce for that row.

    Args:
        user_id: The owner whose cadence to resolve.

    Returns:
        ``(cadence_days, cadence_is_stored)``, or ``(None, False)`` when the
        user has neither a schedule row nor a period to infer from.
    """
    schedule = pay_schedule_service.get_schedule(user_id)
    if schedule is not None:
        return schedule.cadence_days, True
    return pay_schedule_service.resolve_cadence(user_id), False


def _payday_control_blob(user_id, perturbation, cadence_days, cadence_is_stored):
    """Return the payday control's result for one user.

    Args:
        user_id: The owner under test.
        perturbation: The relocated schedule, or ``None`` when the user has
            fewer than two paydays.
        cadence_days: The cadence the main comparison used.
        cadence_is_stored: Whether that cadence came from a schedule row.

    Returns:
        A JSON-safe mapping.  ``applicable`` is ``False`` when the schedule was
        too short to disturb -- which is every fresh signup, and is reported as
        a control that could not run rather than as one that failed.
    """
    if perturbation is None:
        return {
            "applicable": False,
            "fired": False,
            "why": "fewer than two paydays; there is no order to disturb",
        }
    moved = compare(
        user_id, perturbation.rows, cadence_days, cadence_is_stored,
    )
    return {
        "applicable": True,
        "fired": bool(moved.disagreements),
        "moved_payday_from": perturbation.moved_from.isoformat(),
        "moved_payday_to": perturbation.moved_to.isoformat(),
        "disagreement_count": len(moved.disagreements),
        "shifted_indices": [
            {
                "start_date": row.start_date.isoformat(),
                "stored_index": row.stored_index,
                "derived_index": row.derived_index,
            }
            for row in moved.disagreements
            if not row.index_agrees
        ],
        "shifted_ends": [
            {
                "start_date": row.start_date.isoformat(),
                "stored_end": row.stored_end.isoformat(),
                "derived_end": row.derived_end.isoformat(),
            }
            for row in moved.disagreements
            if not row.end_agrees
        ],
    }


def _user_blob(user_id):
    """Return the whole comparison for one user, both controls included.

    Args:
        user_id: The owner to measure.

    Returns:
        A JSON-safe mapping.  A user with no paydays, and a user whose cadence
        cannot be resolved at all, each report why rather than being dropped
        from the blob -- an absent user reads as a clean one.
    """
    periods = pay_period_service.get_all_periods(user_id)
    if not periods:
        return {"user_id": user_id, "row_count": 0, "measured": False,
                "why": "no paydays"}
    cadence_days, cadence_is_stored = _resolve_cadence(user_id)
    if cadence_days is None:
        return {"user_id": user_id, "row_count": len(periods),
                "measured": False,
                "why": "no pay_schedule row and no period to infer a cadence"}

    comparison = compare(user_id, periods, cadence_days, cadence_is_stored)
    perturbation = perturb(periods)
    cadence = cadence_control(identified_paydays(periods), cadence_days)
    passed, reasons = verdict(comparison, perturbation, cadence)

    blob = comparison.as_blob()
    blob["measured"] = True
    blob["passed"] = passed
    blob["failure_reasons"] = list(reasons)
    blob["payday_control"] = _payday_control_blob(
        user_id, perturbation, cadence_days, cadence_is_stored,
    )
    blob["cadence_control"] = cadence.as_blob()
    return blob


def _summarise(blobs):
    """Print the operator-facing summary and return the process exit code.

    Args:
        blobs: The per-user mappings, in user-id order.

    Returns:
        ``0`` when every measured user passed and at least one payday control
        was applicable; ``1`` otherwise.
    """
    failed = False
    any_payday_control = False
    for blob in blobs:
        if not blob["measured"]:
            print(f"user {blob['user_id']}: not measured -- {blob['why']}")
            continue
        source = "stored" if blob["cadence_is_stored"] else "INFERRED"
        print(
            f"user {blob['user_id']}: {blob['row_count']} paydays, cadence "
            f"{blob['cadence_days']} ({source}), "
            f"{blob['disagreement_count']} disagreements "
            f"({blob['provable_disagreement_count']} provable)"
        )
        if blob["last_end_is_circular"]:
            print(
                "    NOTE: the cadence was inferred from the last period's "
                "own length, so THAT ROW's end agreement is circular and "
                "proves nothing (plan finding P8).  Every earlier end still "
                "counts."
            )
        for row in blob["rows"]:
            if not row["agrees"]:
                print(
                    f"    {row['start_date']}: index "
                    f"{row['stored_index']} -> {row['derived_index']}, end "
                    f"{row['stored_end']} -> {row['derived_end']}"
                )
        payday = blob["payday_control"]
        if not payday["applicable"]:
            print(f"    payday control: NOT APPLICABLE -- {payday['why']}")
        else:
            any_payday_control = True
            print(
                f"    payday control: moved {payday['moved_payday_from']} -> "
                f"{payday['moved_payday_to']}, reported "
                f"{len(payday['shifted_indices'])} shifted indices and "
                f"{len(payday['shifted_ends'])} shifted ends"
            )
        cadence = blob["cadence_control"]
        print(
            f"    cadence control: probe {cadence['probe_cadence']}, "
            f"{len(cadence['moved'])} end(s) moved, fired={cadence['fired']}"
        )
        if not blob["passed"]:
            failed = True
            for reason in blob["failure_reasons"]:
                print(f"    FAILED: {reason}")
    if not any_payday_control:
        failed = True
        print(
            "FAILED: no user had two or more paydays, so the payday control "
            "never ran.  This database cannot prove the harness reports a "
            "difference."
        )
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
            # Nothing here writes, and the perturbed rows are never added to
            # the session -- this is the belt to that braces, so a future edit
            # cannot make the harness mutate the database it measures.
            db.session.rollback()

    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(blobs, handle, indent=1, sort_keys=True)
    measured = sum(1 for blob in blobs if blob["measured"])
    rows = sum(blob["row_count"] for blob in blobs)
    print(
        f"wrote {out_path}: {len(blobs)} users, {measured} measured, "
        f"{rows} paydays"
    )
    return _summarise(blobs)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(
            "usage: verify_pay_calendar_derivation.py <output.json>  "
            "(DATABASE_URL selects the database)"
        )
    raise SystemExit(main(sys.argv[1]))
