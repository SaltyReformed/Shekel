"""The audit trigger's overhead, TIMED and PRINTED: a report, never a gate.

**No assertion here reads a time** (plan step balance:X-cy, ruling
**balance:R-BAL144**, 'Count work, report time').  The required check is
``tests/test_integration/test_audit_trigger_work.py``: it counts what the
trigger WRITES and pins what the trigger COSTS to run.  This file times the
same five workloads (:mod:`tests._audit_trigger_workloads`) with the trigger
on and off and prints the ratio; when a changed trigger fails that pin, this is
how it is re-measured, and the figures are recorded beside the pin.  Until this
step each workload held a wall-clock ceiling, and on GitHub's shared runners
those ceilings failed the required check four times in about fourteen hours
with no change to the trigger (finding **recurrence:REC-533**).

**A benchmark that RAISES still fails** (**R-BAL152**).  Its CI step stays
graded, because an exception is deterministic, and this directory has already
rotted unseen once: its tests errored at setup from 2026-05-20 to 2026-08-28
while nothing ran them.  So does one that HANGS: the suite's per-test timeout
(``pytest.ini``, 50 s) still applies (**R-BAL153**).  Measured 2026-09-25 on a
busy dev box: the whole report takes about 10 s, and with the trigger slowed to
about 1 ms per row (some 25-35 times today's cost) it still finished in 18 s,
so the timeout trips only on a hang or a trigger far slower than that.

The figures print on a PASSING run, through ``capsys.disabled()``, while the
app's own logs stay captured.  The directory is excluded from the default
(parallel) run, because oversubscription inflates the very ratio this prints
(``pytest.ini``).  CI runs it serially in its own step; run it the same way:

    ./scripts/test.sh tests/test_performance -q -n 0 -p no:randomly --override-ini=addopts=
"""
import time

import pytest

from app.extensions import db as _db
from tests._audit_trigger_workloads import WORKLOADS

# Number of timing iterations for more stable measurements.
ITERATIONS = 15
# Warmup iterations discarded before timing.
WARMUP = 3


def _median(values):
    """Return the middle value of ``values`` (upper median when even)."""
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _paired_overhead(sample):
    """Measure audit-trigger overhead by ALTERNATING the two arms.

    **The two arms must see the same machine, and measuring them in two
    separate windows is why this file's numbers could not be trusted.**
    Each benchmark used to time every with-trigger iteration, then
    disable the trigger and time every without-trigger iteration.  Any
    change in machine load between those two windows lands entirely in
    the ratio, and on a loaded box that is not a small effect: measured
    2026-08-28, a two-window run reported the generate benchmark at
    **-22.6% overhead** -- the audited path finishing 22 points FASTER
    than the unaudited one, which is impossible and bounds the noise at
    roughly the size of the effect being measured.

    Alternating them per iteration cancels that.  A slow moment now hits
    both arms of the same pair, so it moves both times and largely
    leaves their ratio alone.  The reported figure is the MEDIAN of the
    per-pair ratios rather than a ratio of aggregates, because a
    per-pair ratio is the quantity that is actually paired.

    Args:
        sample: Callable taking no arguments that performs its own
            untimed setup, runs ONE timed iteration, and returns the
            elapsed milliseconds.  It must leave the database ready for
            another call, since it is called many times.

    Returns:
        Tuple of (median overhead percent, median with-trigger ms,
        median without-trigger ms).
    """
    for _ in range(WARMUP):
        sample()

    with_ms, without_ms, ratios = [], [], []
    for _ in range(ITERATIONS):
        _enable_triggers()
        timed_with = sample()

        _disable_triggers()
        try:
            timed_without = sample()
        finally:
            _enable_triggers()

        with_ms.append(timed_with)
        without_ms.append(timed_without)
        ratios.append(((timed_with - timed_without) / timed_without) * 100)

    return _median(ratios), _median(with_ms), _median(without_ms)


def _disable_triggers():
    """Disable audit triggers on budget.transactions."""
    _db.session.execute(
        _db.text("ALTER TABLE budget.transactions DISABLE TRIGGER audit_transactions")
    )


def _enable_triggers():
    """Re-enable audit triggers on budget.transactions."""
    _db.session.execute(
        _db.text("ALTER TABLE budget.transactions ENABLE TRIGGER audit_transactions")
    )


def _report(capsys, label, overhead_pct, time_with, time_without):
    """Print one workload's figures past pytest's capture.  Asserts nothing (R-BAL144).

    ``capsys.disabled()`` rather than ``-s``: the figures reach the terminal on
    a passing run, and the app's JSON logs, which ``-s`` would release too
    (about 94 lines against five blocks, measured by this step's review), stay
    captured.

    Args:
        capsys: The test's ``capsys`` fixture.
        label: The workload's :attr:`~tests._audit_trigger_workloads.Workload.label`.
        overhead_pct: Overhead percent from :func:`_paired_overhead`.
        time_with: Milliseconds with the audit trigger enabled.
        time_without: Milliseconds with it disabled.
    """
    with capsys.disabled():
        print(f"\n  {label}:")
        print(f"    With triggers:    {time_with:.1f} ms")
        print(f"    Without triggers: {time_without:.1f} ms")
        print(f"    Overhead:         {overhead_pct:.1f}%")


class TestAuditTriggerOverheadReport:
    """Time every workload with and without the audit trigger, and print it."""

    @pytest.mark.parametrize("workload_class", WORKLOADS, ids=lambda cls: cls.key)
    def test_overhead_is_reported(self, app, db, perf_user, capsys, workload_class):  # pylint: disable=unused-argument
        """Time one workload through the paired harness and print the ratio.

        Each sample is one :meth:`~tests._audit_trigger_workloads.Workload.reset`
        (untimed) and one :meth:`~tests._audit_trigger_workloads.Workload.act`
        (timed); the commit after it is untimed too.

        Pylint: ``unused-argument`` -- ``app`` and ``db`` are requested for the
        application context and the test's own database.
        """
        workload = workload_class(perf_user)

        def _sample():
            workload.reset()
            start = time.perf_counter()
            workload.act()
            elapsed_ms = (time.perf_counter() - start) * 1000
            _db.session.commit()
            return elapsed_ms

        _report(capsys, workload.label, *_paired_overhead(_sample))
