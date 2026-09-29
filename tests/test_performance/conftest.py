"""Fixtures for the audit-trigger overhead report.

The owner the report times the workloads as is built by
:func:`tests._audit_trigger_workloads.build_owner`, the same builder the
required gate counts on (plan step balance:X-cy, ruling R-BAL151).
"""
import pytest

from tests._audit_trigger_workloads import build_owner


@pytest.fixture()
def perf_user(app, db):  # pylint: disable=unused-argument
    """Create the owner the benchmarks write as (a 52-paycheck calendar).

    Pylint: ``unused-argument`` -- ``app`` and ``db`` are requested for the
    application context and the test's own database the builder writes into.
    """
    return build_owner()
