"""The audit trigger's WORK is the required check; its time is only reported.

Plan step balance:X-cy, ruling **balance:R-BAL144** ('Count work, report
time'), closing finding **recurrence:REC-533**.  Until this step the gate was a
wall-clock ceiling on how much slower each write got with the trigger on, and
on GitHub's shared runners it failed four times in about fourteen hours with
no change to the trigger.  The timings still run and print
(``tests/test_performance/test_trigger_overhead.py``); what is REQUIRED is
here, in the ordinary suite, so every local run grades it and so does CI,
on whichever shard ``tests/_shard.py`` assigns each case (**R-BAL151**).
Two properties, one class each:

* :class:`TestEveryChangedRowIsAuditedOnce` -- each workload in
  :mod:`tests._audit_trigger_workloads`, run with the trigger on, leaves exactly
  one ``system.audit_log`` row per ``budget.transactions`` row it changed,
  carrying that row's id and the operation that changed it, and no audit row
  for any other table.  WHICH rows changed is read from the table itself --
  every row's value before the act against after it -- never from the audit
  log, so the two sides of the comparison share no producer beyond
  PostgreSQL's own ``to_jsonb``.  A trigger that writes no row, two rows, or a
  row under the wrong id or table fails.
* :class:`TestTheMeasuredCostSurfaceIsPinned` -- the trigger's cost surface is
  the one the report's recorded figures were measured against (**R-BAL149**;
  what it reads is :data:`tests._audit_trigger_workloads._COST_SURFACE_QUERIES`).
  It grades the MODULE's function and attachment as the test template installs
  them; a migration that changes either alone is not seen here (finding
  **balance:BAL-556**).
"""
from collections import Counter

import pytest

from app.extensions import db as _db
from tests._audit_trigger_workloads import (
    PINNED_COST_FINGERPRINT,
    WORKLOADS,
    build_owner,
    cost_surface,
    fingerprint_of,
)


@pytest.fixture(name="owner")
def _owner(app, db):  # pylint: disable=unused-argument
    """The owner every workload writes as (:func:`build_owner`).

    Pylint: ``unused-argument`` -- ``app`` and ``db`` are requested for the
    application context and the test's own database the builder writes into.
    """
    return build_owner()


def _transactions_by_id():
    """Return every ``budget.transactions`` row as JSON, keyed by id."""
    return dict(
        _db.session.execute(
            _db.text("SELECT id, to_jsonb(t) FROM budget.transactions AS t")
        ).all()
    )


def _changes(before, after):
    """Return ``(operation, row id)`` for every row that moved between two reads.

    Args:
        before: :func:`_transactions_by_id` before the act.
        after: :func:`_transactions_by_id` after it.

    Returns:
        set: One pair per changed row -- ``INSERT`` for a row only ``after``
        holds, ``DELETE`` for one only ``before`` holds, ``UPDATE`` for one
        whose value differs between them.
    """
    return (
        {("INSERT", row_id) for row_id in after.keys() - before.keys()}
        | {("DELETE", row_id) for row_id in before.keys() - after.keys()}
        | {
            ("UPDATE", row_id)
            for row_id in before.keys() & after.keys()
            if before[row_id] != after[row_id]
        }
    )


def _last_audit_id():
    """Return the newest ``system.audit_log`` id, or 0 when it is empty."""
    return _db.session.execute(
        _db.text("SELECT coalesce(max(id), 0) FROM system.audit_log")
    ).scalar_one()


def _audit_rows_since(mark):
    """Return every audit row newer than *mark*, for ANY table, counted.

    Every table, so an act that also writes a second audited table fails: its
    trigger would fire in both arms of the report and change what the ratio
    means (:class:`tests._audit_trigger_workloads.Insert` names the hazard).

    Returns:
        Counter: ``(schema, table, operation, row_id)`` to the number of audit
        rows carrying it.
    """
    rows = _db.session.execute(
        _db.text(
            "SELECT table_schema, table_name, operation, row_id "
            "FROM system.audit_log WHERE id > :mark"
        ),
        {"mark": mark},
    ).all()
    return Counter(tuple(row) for row in rows)


class TestEveryChangedRowIsAuditedOnce:
    """One audit row per changed row, for every workload, on every run."""

    @pytest.mark.parametrize("workload_class", WORKLOADS, ids=lambda cls: cls.key)
    def test_one_audit_row_per_changed_row(self, app, db, owner, workload_class):  # pylint: disable=unused-argument
        """The act's changed rows and its audit rows are the same multiset.

        **Run TWICE, because the second run is the one that went vacuous.**
        The update workload re-wrote its first run's note on every later run
        until this step, which the trigger answers with no row; a single run
        would have passed it (R-BAL150).  Each run must change EXACTLY the
        workload's declared number of rows (the count its label states and the
        report's recorded figures were measured on), all of them by its one
        operation, so a workload that shrinks or stops changing rows fails
        here rather than passing with less, or nothing, to count.

        Pylint: ``unused-argument`` -- ``app`` and ``db`` are requested for the
        application context and the test's own database.
        """
        workload = workload_class(owner)
        for run in ("first", "repeat"):
            workload.reset()
            before = _transactions_by_id()
            mark = _last_audit_id()
            workload.act()
            changed = _changes(before, _transactions_by_id())
            audited = _audit_rows_since(mark)
            _db.session.commit()
            expected = Counter(
                ("budget", "transactions", operation, row_id)
                for operation, row_id in changed
            )

            assert len(changed) == workload.rows, (
                f"{workload.key}, {run} run: the act changed {len(changed)} "
                f"row(s) of budget.transactions, not the {workload.rows} it "
                "declares; a count over fewer rows grades less than it claims"
            )
            assert {operation for operation, _ in changed} == {workload.operation}, (
                f"{workload.key}, {run} run: expected only {workload.operation} "
                f"changes, the table shows {sorted(changed)}"
            )
            assert audited == expected, (
                f"{workload.key}, {run} run: {len(changed)} changed row(s) but "
                f"{sum(audited.values())} audit row(s); missing "
                f"{sorted(expected - audited)}, extra "
                f"{sorted(audited - expected)}"
            )


class TestTheMeasuredCostSurfaceIsPinned:
    """The trigger that runs is the trigger whose overhead was recorded."""

    def test_the_cost_surface_is_the_one_measured(self, app, db):  # pylint: disable=unused-argument
        """The live cost surface's fingerprint equals the pinned one.

        A failure is not a flake and is not fixed by re-pinning alone: the
        recorded figures describe the OLD surface (R-BAL144).  The function
        and attachment read are the module's as the template installs them
        (BAL-556).

        Pylint: ``unused-argument`` -- ``app`` and ``db`` are requested for the
        application context and the test's own database the surface is read from.
        """
        surface = cost_surface()
        fingerprint = fingerprint_of(surface)
        assert fingerprint == PINNED_COST_FINGERPRINT, (
            "The audit trigger's cost surface changed (fingerprint "
            f"{fingerprint}, pinned {PINNED_COST_FINGERPRINT}).  Re-measure "
            "before re-pinning: run `./scripts/test.sh tests/test_performance "
            "-q -n 0 -p no:randomly --override-ini=addopts=` FIVE times, "
            "serially, on the dev box; record, as a new dated block in the "
            "table in tests/_audit_trigger_workloads.py, each workload's "
            "overhead range, base (median without-trigger) range and row "
            "count, the machine's load and the PostgreSQL version; point the "
            "pin's comment at that block; then set "
            "PINNED_COST_FINGERPRINT to the fingerprint above.  The live "
            f"surface:\n{surface}"
        )
