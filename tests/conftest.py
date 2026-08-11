"""
Shekel Budget App -- Test Fixtures

Provides reusable pytest fixtures for the test suite: a configured
test app, a freshly-cloned per-test database, an authenticated
client, and factory helpers for creating test data.

Strategy: each test gets a brand-new database cloned from
``shekel_test_template`` via PG 18's reflink-backed
``CREATE DATABASE ... TEMPLATE ... STRATEGY FILE_COPY`` path
(Phase 3b of
``docs/audits/test_improvements/test-performance-implementation-plan.md``).
Replaces the prior per-test TRUNCATE+reseed cycle with a constant-
time metadata copy on btrfs-backed PGDATA; the per-test isolation
contract (empty ``system.audit_log``, no rows in ``budget.*`` /
``auth.*`` / ``salary.*``, full ref-data seed, in-process
``ref_cache`` matching the seeded IDs) is bit-for-bit identical
between the two mechanisms -- only the underlying delivery
changes.
"""

# pylint: disable=wrong-import-position,wrong-import-order
# Imports below are intentionally ordered so the SECRET_KEY env var
# is set AND the per-pytest-worker database is cloned from
# ``shekel_test_template`` BEFORE any ``app`` module is imported.
# Two class-body reads at first-app-import time depend on this:
#
# * ``app.config.TestConfig.SQLALCHEMY_DATABASE_URI`` reads
#   ``TEST_DATABASE_URL`` -- ``_bootstrap_worker_database`` below
#   sets it to the per-session DSN.
# * Production / ``_reject_sentinel`` defends read ``SECRET_KEY``
#   with no fallback (audit finding F-016).
#
# Setting either env var after the first ``from app import ...``
# would leave the app pointed at a stale value.

import csv
import os
import pathlib
import statistics
import time
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlparse, urlunparse

import psycopg2
from psycopg2 import sql

# IMPORTANT: SECRET_KEY must be set in the environment BEFORE the
# ``app`` package is imported, because ``app/config.py`` reads it at
# class-definition time via ``os.getenv("SECRET_KEY")``.  Production
# config has no fallback default (audit finding F-016), so without
# this setdefault Flask sessions in the test suite would fail to
# sign or verify.  ``setdefault`` so that a developer running pytest
# with their own real key in the environment is not overridden.
# The value is intentionally distinct from any placeholder rejected
# by ProdConfig and is at least 32 characters.
os.environ.setdefault(
    "SECRET_KEY",
    "test-suite-fixed-key-not-used-in-production-do-not-deploy",
)


# Name of the PostgreSQL template database the bootstrap clones from.
# Built by ``scripts/build_test_template.py``, which honors the same
# ``TEST_TEMPLATE_DATABASE`` override read here -- the two MUST resolve
# the same name or the suite clones a stale template.  The override
# exists for parallel checkouts (e.g. a feature-branch worktree whose
# migration head differs from another live checkout's): each checkout
# builds and clones its OWN template, so neither suite sees the other's
# ref rows or schema.
_TEST_TEMPLATE_DATABASE = os.environ.get(
    "TEST_TEMPLATE_DATABASE", "shekel_test_template",
)
# Prefix of the PER-WORKER database names.  The other half of the
# parallel-checkout story above, and it was missing: the template could be
# isolated but the worker databases could not, so two checkouts on one cluster
# both claimed ``shekel_test_gw0``..``gw11`` (both default to ``-n 12``) and
# the second invocation's CREATE DATABASE failed.  That is fail-loud by design
# -- see the orphan-cleanup note in :func:`_bootstrap_worker_database` -- but
# "loud" arrives as hundreds of setup errors spread across BOTH runs, which
# reads as a code regression rather than as a collision.  Set
# ``TEST_DB_PREFIX`` per checkout and the two never meet.
_TEST_DATABASE_PREFIX = os.environ.get("TEST_DB_PREFIX", "shekel_test")
# Default admin DSN (peer auth) -- overridable via env so CI and
# developer laptops that need TCP + password can point at their own
# admin DB without code change.  Must NOT be the template DB itself:
# ``CREATE DATABASE`` and ``DROP DATABASE`` cannot run against the
# connection's own database.
_DEFAULT_ADMIN_URL = "postgresql:///postgres"
# Expected ``ref.account_types`` row count in a freshly-cloned per-
# session DB.  Sourced from ``app.ref_seeds.ACCT_TYPE_SEEDS``; any
# mismatch indicates the template is corrupt and needs a rebuild.
_EXPECTED_ACCOUNT_TYPE_COUNT = 19


# ---------------------------------------------------------------------------
# Fixture profile harness (Phase 0 of test-performance-implementation-plan)
# ---------------------------------------------------------------------------
# Permanent instrumentation around the per-test ``db`` fixture inner
# steps, gated behind ``SHEKEL_TEST_FIXTURE_PROFILE=1`` so the default
# test path is unaffected.  When the flag is set, each test appends
# one row to a per-worker CSV in ``tests/.fixture-profile/`` recording
# elapsed milliseconds for rollback / TRUNCATE main / seed_ref /
# commit / TRUNCATE audit_log / refresh_ref_cache / call / teardown.
# At session end the aggregator reads every worker CSV and prints a
# summary table whose shape matches
# ``docs/audits/test_improvements/test-performance-research.md``
# section 3.1.
#
# Why this lives here and not in a sibling module: the timer
# wrappers must be physically interleaved with the fixture body, and
# the aggregator must run from ``pytest_sessionfinish``, which is a
# conftest-level hook.  Splitting helpers into a sibling module would
# add an indirection without buying isolation -- the wrappers would
# still need direct access to ``_db`` and the fixture's local state.
#
# Why the flag is checked once at module load (not per-test): we want
# zero per-test cost when disabled.  A single module-level boolean
# costs one branch per ``with _profile_step(...)`` block at fixture
# entry -- well below the noise floor of the operations it wraps.

_FIXTURE_PROFILE_ENABLED = os.environ.get("SHEKEL_TEST_FIXTURE_PROFILE") == "1"
_FIXTURE_PROFILE_DIR = pathlib.Path(__file__).parent / ".fixture-profile"

# Step names, in column order.  Drives the CSV header, the per-test
# row writer, and the row order in the summary table.  The leading
# ``setup_`` prefix tags steps that contribute to "Fixture setup
# total" in the aggregator (vs. ``call`` and ``teardown`` which are
# reported but not part of the fixture-percent column).  The names
# match the labels in the published baseline so a future reader can
# diff the two tables cell-for-cell.
_FIXTURE_PROFILE_STEPS = (
    "setup_rollback",
    "setup_drop_db",
    "setup_clone_template",
    "setup_refresh_ref_cache",
    "call",
    "teardown",
)

# Pretty labels for each step, used only by the aggregator's print
# pass.  Kept beside _FIXTURE_PROFILE_STEPS so future edits stay in
# sync.  Phase 3b replaced ``setup_truncate_main`` /
# ``setup_seed_ref`` / ``setup_commit_after_seed`` with
# ``setup_drop_db`` + ``setup_clone_template``; the published
# baseline comparison in
# ``docs/audits/test_improvements/test-performance-implementation-plan.md``
# remains diff-able per-step because the surviving step keys
# (``setup_rollback``, ``setup_refresh_ref_cache``, ``call``,
# ``teardown``) and their labels are unchanged.
_FIXTURE_PROFILE_LABELS = {
    "setup_rollback": "rollback",
    "setup_drop_db": "DROP DATABASE WITH (FORCE)",
    "setup_clone_template": "CREATE DATABASE TEMPLATE STRATEGY FILE_COPY",
    "setup_refresh_ref_cache": "refresh_ref_cache",
    "call": "Test body (call)",
    "teardown": "Teardown",
}

# Per-worker CSV path.  ``PYTEST_XDIST_WORKER`` is ``"gw0"``,
# ``"gw1"``, ... under xdist and unset under single-process pytest
# (we use ``"main"`` for the latter, matching the bootstrap's
# ``worker_id`` convention).  Each worker writes to its own file so
# concurrent appends never contend on a lock.
_FIXTURE_PROFILE_WORKER_ID = os.environ.get("PYTEST_XDIST_WORKER", "main")
_FIXTURE_PROFILE_CSV = (
    _FIXTURE_PROFILE_DIR / f"{_FIXTURE_PROFILE_WORKER_ID}.csv"
)


def _is_xdist_master():
    """Return True for the pytest-xdist controller process.

    The controller spawns workers and runs collection but does NOT
    execute tests; it sets ``PYTEST_XDIST_TESTRUNUID`` but leaves
    ``PYTEST_XDIST_WORKER`` unset.  Workers (``gw0``, ``gw1``, ...)
    set both, and single-process runs set neither.  The harness uses
    this distinction to skip the per-test CSV setup on the master
    while still running the aggregator there (the master is the only
    process that sees every worker's output after they exit).
    """
    return (
        bool(os.environ.get("PYTEST_XDIST_TESTRUNUID"))
        and not os.environ.get("PYTEST_XDIST_WORKER")
    )


def _profile_session_init():
    """Wipe stale CSVs and prepare this process's profile file.

    Two-phase:

    1. The xdist master (or single-process run) wipes any leftover
       ``*.csv`` from a previous pytest invocation before workers
       spawn.  Without this, a previous run with ``-n 16`` would
       leave ``gw13``..``gw15.csv`` on disk and the next ``-n 12``
       run's aggregator would mistakenly include their stale rows.
       Worker subprocesses load this conftest AFTER the master, so
       the wipe is finished by the time they create their own CSVs.
    2. Every process whose conftest load happens before xdist sets
       ``PYTEST_XDIST_TESTRUNUID`` creates the dir and writes a
       header row to its worker CSV.  Empirically (pytest-xdist
       3.8 on Python 3.14) this includes both single-process runs
       AND the xdist master -- the master never runs tests, so its
       ``main.csv`` ends up as a header-only stub.  Workers
       (``gw0``..``gwN``) load conftest later, with ``TESTRUNUID``
       already set, but they are detected via ``PYTEST_XDIST_WORKER``
       not via TESTRUNUID, so the ``_is_xdist_master`` check below
       is defence-in-depth for a future xdist that sets
       ``TESTRUNUID`` earlier on the master.

    Truncating-on-init means two consecutive pytest runs with the
    same worker id do not accumulate -- the second run starts from
    a clean header row.

    No-op when ``SHEKEL_TEST_FIXTURE_PROFILE`` is unset.
    """
    if not _FIXTURE_PROFILE_ENABLED:
        return

    # Phase 1: master / single-process wipes stale CSVs.  In xdist
    # mode the master loads conftest before workers spawn, so this
    # runs first; workers see a clean directory.  (The master also
    # writes its own main.csv header in phase 2 below -- the master
    # never runs tests so that file ends up as a header-only stub.
    # The aggregator handles it correctly: ``DictReader`` returns
    # zero data rows, so the stub contributes nothing to the
    # summary.  Removing it would require an extra teardown step
    # for ~150 bytes of harmless residue.)
    if not os.environ.get("PYTEST_XDIST_WORKER") and _FIXTURE_PROFILE_DIR.exists():
        for stale_csv in _FIXTURE_PROFILE_DIR.glob("*.csv"):
            stale_csv.unlink()

    # The xdist master does not run tests -- it has nothing to
    # write into its own CSV, so skip phase 2 entirely.
    if _is_xdist_master():
        return

    # Phase 2: this worker's CSV gets a fresh header.  Open mode
    # ``"w"`` truncates; subsequent per-test rows are appended in
    # mode ``"a"`` from ``_profile_write_row``.
    _FIXTURE_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with _FIXTURE_PROFILE_CSV.open("w", newline="", encoding="utf-8") as csv_fp:
        writer = csv.writer(csv_fp)
        writer.writerow(["nodeid", "worker_id", *_FIXTURE_PROFILE_STEPS])


@contextmanager
def _profile_step(timings, step_name):
    """Record elapsed milliseconds of the wrapped block into ``timings``.

    Wraps a block of fixture code so the harness can measure each
    inner step without restructuring the fixture itself.

    Args:
        timings: Either a dict (profiling enabled) keyed by step name
            with float-millisecond values, or ``None`` (profiling
            disabled).  ``None`` short-circuits the timer so the
            wrapped block runs with zero added cost.
        step_name: One of ``_FIXTURE_PROFILE_STEPS``; the key under
            which to store the elapsed time.

    Even when the wrapped block raises, the timer captures the
    elapsed time before the exception propagates.  The exception
    itself is not suppressed -- the harness must never mask test or
    fixture errors.
    """
    if timings is None:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        timings[step_name] = (time.perf_counter() - start) * 1000.0


def _profile_new_timings():
    """Allocate a per-test timings dict, or ``None`` when disabled.

    Pre-populates every step key with ``0.0`` so the CSV row is
    well-formed even if a setup step raises and short-circuits the
    rest of the fixture body: the steps that ran record real timings,
    the bypassed ones keep their ``0.0`` floor, and the aggregator
    can still parse the row instead of choking on missing columns.
    """
    if not _FIXTURE_PROFILE_ENABLED:
        return None
    return {step: 0.0 for step in _FIXTURE_PROFILE_STEPS}


def _profile_write_row(nodeid, timings):
    """Append one CSV row capturing this test's per-step timings.

    No-op when ``timings`` is ``None`` (profiling disabled) or when
    the flag was unset at module load.  Each row carries the full
    column set in ``_FIXTURE_PROFILE_STEPS`` order so the aggregator
    can read it without per-row schema lookups.
    """
    if timings is None or not _FIXTURE_PROFILE_ENABLED:
        return
    with _FIXTURE_PROFILE_CSV.open("a", newline="", encoding="utf-8") as csv_fp:
        writer = csv.writer(csv_fp)
        writer.writerow([
            nodeid,
            _FIXTURE_PROFILE_WORKER_ID,
            *(f"{timings[step]:.4f}" for step in _FIXTURE_PROFILE_STEPS),
        ])


_profile_session_init()


def _bootstrap_worker_database():
    """Create a per-pytest-worker database cloned from the test template.

    Called once at conftest module-load time, BEFORE any ``app``
    import.  Each pytest invocation (and each pytest-xdist worker
    within an invocation) gets its own database; concurrent
    invocations cannot deadlock on the per-test ``TRUNCATE CASCADE``
    because each operates on its own DB.

    Master-vs-worker detection:
        Under pytest-xdist the master process imports conftest for
        test collection but does not run tests.  It sets
        ``PYTEST_XDIST_TESTRUNUID`` but NOT ``PYTEST_XDIST_WORKER``
        (only the workers carry the latter).  The master must skip
        the bootstrap -- otherwise it would leave a per-PID DB that
        nothing uses and is never dropped.  Single-process pytest
        (no ``-n`` flag) has neither variable set and runs the
        bootstrap as ``worker_id="main"``.

    Orphan cleanup:
        On startup the function drops any leftover database that
        matches the worker's name (the Phase 3b stable form
        ``shekel_test_{worker_id}`` AND the legacy PID-suffix form
        ``shekel_test_{worker_id}_*`` from pre-Phase-3b runs) and
        has no active connections in ``pg_stat_activity``.  Handles
        the case where a previous pytest run crashed (SIGKILL,
        kernel OOM, ...) before ``pytest_sessionfinish`` could
        drop its DB.  Filtering by ``pg_stat_activity`` rather than
        name alone defends against the dropping-of-sibling trap: a
        concurrent pytest invocation (rare but documented in
        testing-standards.md) whose worker happens to share this
        worker_id would have its own live DB in the match list;
        the active-connection filter skips dropping it.  CREATE
        DATABASE later in this function will then fail with
        "database already exists" -- the right fail-loud signal
        that two concurrent invocations cannot share a cluster
        under Phase 3b's stable-name scheme.

    Template existence:
        The bootstrap fails fast with an actionable
        ``RuntimeError`` if ``shekel_test_template`` does not
        exist.  The fix is documented in the error message:
        ``python scripts/build_test_template.py``.

    Clone verification:
        After the clone, a fresh psycopg2 connection counts rows
        in ``ref.account_types``.  Anything other than the
        expected 19 means the template was corrupt at clone time
        and needs to be rebuilt; another actionable error message
        steers the operator to the fix.

    Side effects:
        Sets ``os.environ["TEST_DATABASE_URL"]`` to the per-
        session DSN.  ``app.config.TestConfig`` reads this at
        class-body evaluation time during the next ``from app
        import ...``; the env var write must precede that import.

    Returns:
        ``None`` when bootstrap is skipped (xdist master).
        ``(db_name, admin_url)`` otherwise; ``pytest_sessionfinish``
        uses these to DROP the per-session DB after the suite ends.

    Raises:
        RuntimeError: When the template DB is missing, or when
            the freshly-cloned per-session DB carries a row count
            that disagrees with the seed list size.  Both errors
            include the recovery command in the message.
    """
    # xdist master: TESTRUNUID set, WORKER not set.  Skip entirely
    # so the master process does not create a DB that nothing uses.
    if (os.environ.get("PYTEST_XDIST_TESTRUNUID")
            and not os.environ.get("PYTEST_XDIST_WORKER")):
        return None

    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
    # Phase 3b: stable per-worker DB name (no PID suffix).  Per-test
    # drop+reclone re-uses the SAME name on every test so the
    # Flask-SQLAlchemy engine's URL stays valid across test boundaries
    # -- only the underlying database is swapped atomically by
    # DROP+CREATE.  The PID-bearing form (legacy) leaked into the
    # cluster's DB list whenever a previous run crashed; orphan
    # cleanup below catches both forms.
    db_name = f"{_TEST_DATABASE_PREFIX}_{worker_id}"
    admin_url = os.environ.get(
        "TEST_ADMIN_DATABASE_URL", _DEFAULT_ADMIN_URL
    )

    admin_conn = psycopg2.connect(admin_url)
    try:
        admin_conn.autocommit = True
        with admin_conn.cursor() as cur:
            # Orphan cleanup -- match the Phase 3b stable name AND
            # legacy PID-suffix names from pre-Phase-3b runs, then
            # exclude any DB with live connections (a concurrent
            # pytest invocation against the same cluster).
            cur.execute(
                "SELECT datname FROM pg_database "
                "WHERE datname = %s OR datname LIKE %s",
                (db_name, f"{db_name}_%"),
            )
            candidate_orphans = [row[0] for row in cur.fetchall()]
            if candidate_orphans:
                cur.execute(
                    "SELECT DISTINCT datname FROM pg_stat_activity "
                    "WHERE datname = ANY(%s)",
                    (candidate_orphans,),
                )
                active = {row[0] for row in cur.fetchall()}
                for orphan in candidate_orphans:
                    if orphan not in active:
                        cur.execute(
                            sql.SQL(
                                "DROP DATABASE IF EXISTS {} WITH (FORCE)"
                            ).format(sql.Identifier(orphan))
                        )

            # Template existence -- fail fast with a recovery hint.
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (_TEST_TEMPLATE_DATABASE,),
            )
            if cur.fetchone() is None:
                raise RuntimeError(
                    f"Test template database "
                    f"{_TEST_TEMPLATE_DATABASE!r} not found.  "
                    "Run: python scripts/build_test_template.py"
                )

            # Phase 3b: initial clone uses STRATEGY FILE_COPY so PG
            # 18's file_copy_method=clone GUC engages the kernel
            # FICLONE reflink ioctl on the btrfs-backed PGDATA from
            # Phase 3a.  The default WAL_LOG strategy would NOT use
            # FICLONE for the ~50 MB template even with the GUC set
            # globally -- explicit STRATEGY FILE_COPY is the only
            # form that consumes the GUC.  Steady-state ~4-5 ms per
            # clone on btrfs (vs ~10 ms for the WAL_LOG default and
            # ~seconds without reflink).
            cur.execute(
                sql.SQL(
                    "CREATE DATABASE {} TEMPLATE {} STRATEGY FILE_COPY"
                ).format(
                    sql.Identifier(db_name),
                    sql.Identifier(_TEST_TEMPLATE_DATABASE),
                )
            )
    finally:
        admin_conn.close()

    # Verify the clone is intact -- a fresh psycopg2 connection
    # bypasses any SQLAlchemy pool state from the admin connection
    # above.  A row count mismatch means the template itself was
    # corrupt; the message names the fix.
    per_session_url = urlunparse(
        urlparse(admin_url)._replace(path=f"/{db_name}")
    )
    verify_conn = psycopg2.connect(per_session_url)
    try:
        with verify_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ref.account_types")
            account_type_count = cur.fetchone()[0]
            if account_type_count != _EXPECTED_ACCOUNT_TYPE_COUNT:
                raise RuntimeError(
                    f"Per-session DB {db_name!r} appears corrupted "
                    f"(ref.account_types count={account_type_count}, "
                    f"expected {_EXPECTED_ACCOUNT_TYPE_COUNT}).  "
                    "Rebuild the template: "
                    "python scripts/build_test_template.py"
                )
    finally:
        verify_conn.close()

    # Point the app's TestConfig at the per-session DB.  Must
    # precede the first ``from app import ...`` below.
    os.environ["TEST_DATABASE_URL"] = per_session_url

    return (db_name, admin_url)


# Execute the bootstrap at module load time.  ``None`` when the xdist
# master skipped; ``pytest_sessionfinish`` keys off this to decide
# whether to drop the per-session DB.
_BOOTSTRAP_RESULT = _bootstrap_worker_database()


# Pull the worker DB name and admin DSN into module-level constants so
# the per-test ``db`` fixture (Phase 3b) can drop+reclone without
# unpacking ``_BOOTSTRAP_RESULT`` on every call.  ``None`` when the
# bootstrap was skipped (xdist master), in which case the per-test
# fixture will refuse to run -- the master never executes tests so
# this branch should be unreachable in practice; the defensive check
# inside the fixture surfaces a clear error if it ever fires.
if _BOOTSTRAP_RESULT is not None:
    _WORKER_DB_NAME, _WORKER_ADMIN_URL = _BOOTSTRAP_RESULT
else:
    _WORKER_DB_NAME = None
    _WORKER_ADMIN_URL = None


def _drop_worker_database(db_name, admin_url):
    """Drop the per-worker test database via an admin psycopg2 connection.

    Phase 3b helper.  Called once per test by the ``db`` fixture
    (before ``_clone_worker_database`` re-creates it) and at session
    end by ``pytest_sessionfinish``.

    ``WITH (FORCE)`` (PostgreSQL 13+) terminates any leftover backend
    that escaped the previous test's ``_db.engine.dispose()``;
    without it a stuck transaction would block the drop.  Identifier
    interpolation goes through :mod:`psycopg2.sql` so the
    ``shekel_test_*`` name stays safely quoted even though it comes
    from a controlled f-string at module load time -- consistent
    with the rest of this module's admin-DSN access pattern.

    Args:
        db_name: Name of the per-worker DB to drop.
        admin_url: Admin DSN (must NOT point at ``db_name`` itself
            -- ``DROP DATABASE`` cannot run against the connection's
            own database).
    """
    admin_conn = psycopg2.connect(admin_url)
    try:
        admin_conn.autocommit = True
        with admin_conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "DROP DATABASE IF EXISTS {} WITH (FORCE)"
                ).format(sql.Identifier(db_name))
            )
    finally:
        admin_conn.close()


def _clone_worker_database(db_name, admin_url):
    """Re-create the per-worker test DB by cloning ``shekel_test_template``.

    Phase 3b helper.  Called once per test by the ``db`` fixture
    (immediately after ``_drop_worker_database``) to give every test
    the same start state the prior TRUNCATE+reseed cycle provided:
    empty ``system.audit_log``, no rows in ``budget.*`` / ``auth.*`` /
    ``salary.*``, full ref-data seed in ``ref.*``.  The template's
    contents come from ``scripts/build_test_template.py``.

    Explicit ``STRATEGY FILE_COPY`` engages PG 18's reflink path
    under ``file_copy_method=clone`` (Phase 3a's GUC) -- the default
    ``WAL_LOG`` strategy would NOT use ``FICLONE`` on a ~50 MB
    template even with the GUC set globally; the explicit form is
    the only one that consumes the GUC.  Steady-state ~4-5 ms per
    clone on btrfs PGDATA.

    Args:
        db_name: Name of the per-worker DB to create.
        admin_url: Admin DSN (must NOT point at ``db_name`` itself).
    """
    admin_conn = psycopg2.connect(admin_url)
    try:
        admin_conn.autocommit = True
        with admin_conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "CREATE DATABASE {} TEMPLATE {} STRATEGY FILE_COPY"
                ).format(
                    sql.Identifier(db_name),
                    sql.Identifier(_TEST_TEMPLATE_DATABASE),
                )
            )
    finally:
        admin_conn.close()


import pytest

from app import create_app
from app.extensions import db as _db
from app.utils.dates import DISPLAY_TIMEZONE, display_today
from app.models.user import User, UserSettings
# ``Account`` is intentionally not imported here: every test fixture
# constructs accounts via ``app.services.account_service.create_account``,
# the canonical post-E-19 factory.  Tests that need to exercise the
# storage-tier NOT NULL constraint directly use raw SQL inserts.
from app.models.scenario import Scenario
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
from app.models.transfer_template import TransferTemplate
from app.models.ref import (
    AccountType, FilingStatus, RecurrencePattern, Status, TransactionType,
)
from app.services import account_service, pay_period_write
from app.services.auth_service import hash_password
from tests._test_helpers import (
    bind_db_clock_rewriter,
    create_loan_account,
    insert_trueup_event,
    make_appreciating_account,
    make_investment_account,
    posted_loan_balance_at,
    restamp_opening_assertion,
)


# --- App & DB Fixtures ---------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def fast_bcrypt():
    """Use minimum bcrypt rounds (4) for all tests.

    Bcrypt's default work factor (12) makes each hash take ~250ms.
    Rounds=4 reduces this to ~2ms, saving 10+ seconds across the
    full suite without affecting test correctness.
    """
    import bcrypt as _bcrypt  # pylint: disable=import-outside-toplevel
    _original_gensalt = _bcrypt.gensalt

    def _fast_gensalt(rounds=4, prefix=b"2b"):
        """Generate a bcrypt salt with minimum work factor."""
        return _original_gensalt(rounds=rounds, prefix=prefix)

    _bcrypt.gensalt = _fast_gensalt
    yield
    _bcrypt.gensalt = _original_gensalt


@pytest.fixture(autouse=True)
def set_totp_key(monkeypatch):
    """Set a test TOTP encryption key for all tests."""
    from cryptography.fernet import Fernet  # pylint: disable=import-outside-toplevel
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def disable_hibp_check(monkeypatch):
    """Disable the HIBP breached-password check by default.

    ``hash_password`` is invoked from dozens of fixtures (every
    ``seed_user`` variant, plus per-test registration helpers) and
    making each one perform an outbound HTTP call would (a) break the
    suite's hermeticity, (b) slow it by an order of magnitude, and
    (c) silently mask test results during HIBP outages.

    Tests that exercise HIBP behaviour explicitly flip this back on
    via ``monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")`` after
    mocking ``requests.get``.  ``monkeypatch`` is function-scoped so
    the override is local to a single test even when the autouse
    fixture has already run.

    See audit finding F-086 / commit C-11 for the production posture
    (default-on) and ``app/services/auth_service.py:_check_pwned_password``
    for the runtime read.
    """
    monkeypatch.setenv("HIBP_CHECK_ENABLED", "false")


@pytest.fixture(scope="session", autouse=True)
def _calendar_sweep():
    """Run the whole session as if today were ``SHEKEL_FAKE_TODAY``.

    **Opt-in, and a no-op unless the variable is set**, so an ordinary run is
    byte-identical to one without this fixture.

    Four separate defects in this suite had exactly one trigger: the day of the
    month the suite happened to run on (findings N-131, N-132, R8, and the two
    loan cross-surface tests that failed on 2026-08-01).  Each was found by a
    merge gate rather than by a test, because nothing here could ask "does this
    still pass on the 1st?".  This is that instrument::

        SHEKEL_FAKE_TODAY=2026-09-01 ./scripts/test.sh

    ``tick=True`` keeps the clock RUNNING from the faked instant rather than
    freezing it, because a frozen clock makes every ``created_at`` in a session
    identical and the fold's assertion ordering (ruling R-DH: two assertions
    sharing a civil day apply in recording order) then has no order to read.

    **What it can and cannot move, stated because the gap decides how to read a
    failure.**  It moves the PYTHON clock -- ``date.today()``,
    ``datetime.now()`` and therefore :func:`app.utils.dates.display_today`.  It
    does NOT move POSTGRES: ``created_at`` / ``updated_at`` are
    ``server_default=db.func.now()`` (``app/models/mixins.py:237-263``),
    evaluated in the database.  (The settle day was a fourth such reach until
    plan step X-f1; the seam stamps ``display_today()`` into a ``DATE`` column
    now, which this instrument DOES move.)  So under a fake date every
    server-stamped row
    carries the REAL instant, and a test comparing a fixture-built date against
    a server-stamped one fails by the offset between them.  That failure is an
    artifact of this instrument, not a defect in the test -- see
    ``docs/testing-standards.md`` for how to tell the two apart.

    The instant is built in ``DISPLAY_TIMEZONE`` at midday, so the faked civil
    day is unambiguous in the user's zone and cannot straddle midnight in either
    direction.
    """
    fake = os.environ.get("SHEKEL_FAKE_TODAY")
    if not fake:
        yield
        return

    # Pylint: ``import-outside-toplevel`` -- a test-only dependency imported
    # only when the sweep is switched on, so an ordinary run never loads it.
    # pylint: disable=import-outside-toplevel
    import time_machine

    target = datetime.combine(
        date.fromisoformat(fake), time(12, 0), tzinfo=DISPLAY_TIMEZONE,
    )
    with time_machine.travel(target, tick=True):
        yield


@pytest.fixture(scope="session")
def app():
    """Create the Flask application configured for testing."""
    application = create_app("testing")
    yield application


@pytest.fixture(scope="session", autouse=True)
def setup_database(app):
    """One-time per-session prep: refresh the in-process ref cache.

    The per-session PostgreSQL database was cloned from
    ``shekel_test_template`` at conftest module-load time (see
    :func:`_bootstrap_worker_database`).  Schemas, tables, audit
    infrastructure, indexes, and reference seed data are therefore
    already present in the database when this fixture runs; the only
    Python-side initialisation remaining is the in-process ref_cache
    and the Jinja globals that mirror the seeded IDs (the templates
    read these at render time -- a missing entry would break every
    page that references one).

    Database teardown happens in :func:`pytest_sessionfinish` at the
    bottom of this module: ``DROP DATABASE ... WITH FORCE`` removes
    the whole per-session DB rather than table-by-table -- faster
    and less brittle than the previous ``drop_all`` + per-schema
    cascade.

    It also binds the frozen-clock statement rewriter to the engine
    (finding N-65, balance plan step X-h).  **Here, and not lazily when a
    test first flushes**: binding on first flush made the rewriter's
    installation depend on some earlier test in the same worker process
    having flushed ORM state under a frozen clock, so a frozen test whose
    only writes were bulk ``query.update(...)`` silently got the real wall
    clock.  Same test, same assertion, opposite result depending on
    scheduling -- a fail-OPEN gate.  The listener is inert until a test
    freezes, so binding it before any test runs costs nothing and removes
    the order dependence.
    """
    with app.app_context():
        _refresh_ref_cache_and_jinja_globals(app)
        bind_db_clock_rewriter(_db.engine)
    yield


@pytest.fixture(autouse=True)
def db(app, setup_database, request):
    """Provide a freshly-cloned database for each test.

    Drops the per-worker DB and re-clones it from
    ``shekel_test_template`` via PG 18's reflink-backed
    ``CREATE DATABASE ... TEMPLATE ... STRATEGY FILE_COPY``
    (Phase 3b of test-performance-implementation-plan.md).  Each
    test gets bit-for-bit the same start state the prior
    TRUNCATE+reseed cycle provided:

      * ``system.audit_log`` empty -- the template carries zero
        rows by construction; ``scripts/build_test_template.py``
        truncates the log after the seed commits.
      * No rows in ``budget.*`` / ``auth.*`` / ``salary.*`` -- the
        template is freshly migrated and seeded with reference
        data only.
      * Full ref-data seed in ``ref.*`` including the 19
        ``ref.account_types`` built-ins.
      * In-process ``ref_cache`` and Jinja globals re-seated to
        match the cloned DB's row IDs (which equal the template's
        IDs because ``CREATE DATABASE TEMPLATE`` preserves them).

    Mechanism, in order:

      1. ``setup_rollback`` -- defensive ``session.rollback()`` in
         case a prior test left a stale transaction.  Empirically
         a no-op (Phase 0 measured ~0.0 ms).
      2. Release the engine: ``session.remove()`` detaches the
         scoped session; ``engine.dispose()`` closes every pooled
         connection.  Prerequisites for ``DROP DATABASE WITH
         (FORCE)`` -- the FORCE clause severs leftover backends at
         the protocol level, but disposing here avoids the race
         and keeps the engine pool aligned with the freshly-cloned
         DB on the next session access.  Untimed because the
         steady-state cost is ~0 ms (the previous test's teardown
         already disposed).
      3. ``setup_drop_db`` -- admin-DSN ``DROP DATABASE IF EXISTS
         {worker_db} WITH (FORCE)``.
      4. ``setup_clone_template`` -- admin-DSN ``CREATE DATABASE
         {worker_db} TEMPLATE shekel_test_template STRATEGY
         FILE_COPY``.  Reflink-backed on btrfs PGDATA with
         ``file_copy_method=clone`` set on the cluster (Phase 3a);
         steady-state ~4-5 ms.
      5. ``setup_refresh_ref_cache`` -- re-seat the in-process
         ref_cache and Jinja globals against the cloned DB.  The
         row IDs are identical to the template's (CLONE preserves
         them), but reseating costs ~5-7 ms and covers the edge
         case where a future migration changes the seeded ID set
         without anyone updating the in-process cache eagerly.

    Why the worker DB name is stable across the session: the URL
    the Flask-SQLAlchemy engine binds to at app-creation time is
    derived from ``TEST_DATABASE_URL`` set by
    ``_bootstrap_worker_database``; that URL remains valid across
    every drop+reclone because only the underlying database is
    swapped, never the URL.  ``engine.dispose()`` between tests
    forces the pool to reconnect on the next session access, and
    the connection re-establishes against the cloned DB at the
    same URL.

    The ``_profile_step`` wrappers below are no-ops when
    ``SHEKEL_TEST_FIXTURE_PROFILE`` is unset; when set they capture
    per-step elapsed time for the Phase 0 harness (see the block
    comment near ``_FIXTURE_PROFILE_ENABLED`` at the top of this
    module).
    """
    if _WORKER_DB_NAME is None or _WORKER_ADMIN_URL is None:
        raise RuntimeError(
            "db fixture invoked from a process that skipped "
            "_bootstrap_worker_database (xdist master?).  The "
            "master should not run tests; check pytest-xdist's "
            "scheduling configuration."
        )

    timings = _profile_new_timings()
    # nodeid is only used by the profile CSV writer; skip the
    # attribute lookup when profiling is disabled so the default
    # path adds zero work beyond the existing fixture body.
    nodeid = request.node.nodeid if timings is not None else None

    with app.app_context():
        with _profile_step(timings, "setup_rollback"):
            # Clear any stale transaction state from a prior test
            # that raised an exception without committing or
            # rolling back.  Defensive; empirically a no-op since
            # the previous teardown's session.remove() detaches
            # any session and engine.dispose() closes its pool.
            _db.session.rollback()

        # Release the engine fully so the DROP below cannot race a
        # held connection.  session.remove() detaches the scoped
        # session; engine.dispose() closes every pooled connection.
        # Untimed -- the work is essentially constant and dominated
        # by Python overhead, not DB round-trips; folding it into
        # setup_drop_db's timer would blur the DROP measurement.
        _db.session.remove()
        _db.engine.dispose()

        with _profile_step(timings, "setup_drop_db"):
            _drop_worker_database(_WORKER_DB_NAME, _WORKER_ADMIN_URL)

        with _profile_step(timings, "setup_clone_template"):
            _clone_worker_database(_WORKER_DB_NAME, _WORKER_ADMIN_URL)

        with _profile_step(timings, "setup_refresh_ref_cache"):
            # Re-seat the in-process ref_cache and Jinja globals
            # against the cloned DB.  The cloned IDs equal the
            # template IDs (CREATE DATABASE TEMPLATE preserves
            # them) so the cache is normally a no-op refresh, but
            # the explicit reseat covers the edge case where a
            # future migration changes the seeded ID set and an
            # unaware test would otherwise hit a Jinja Undefined.
            # First access to _db.session here triggers a fresh
            # pool connection to the cloned DB at the (unchanged)
            # URL the engine has been bound to since app-create.
            _refresh_ref_cache_and_jinja_globals(app)

        # ``try``/``finally`` so the teardown timer and the CSV row
        # write both run even when the test raises -- a profile
        # harness that silently dropped rows for failing tests would
        # bias the summary toward the passing path.  The outer
        # ``with _profile_step(..., "call")`` captures the elapsed
        # time of the ``yield _db`` (i.e. the test body itself);
        # context manager exit fires after pytest sends back to
        # this generator, so the timer covers the test exactly.
        try:
            with _profile_step(timings, "call"):
                yield _db
        finally:
            with _profile_step(timings, "teardown"):
                # Clean up after each test: detach the scoped
                # session and close the engine pool so the next
                # test's DROP DATABASE has a clean slate.
                # Belt-and-braces with the WITH (FORCE) in
                # _drop_worker_database -- one or the other would
                # suffice, but both together make the per-test
                # contract impossible to violate via a leaked
                # connection.
                _db.session.remove()
                _db.engine.dispose()
            _profile_write_row(nodeid, timings)


@pytest.fixture()
def client(app, db):
    """Provide a Flask test client."""
    return app.test_client()


# --- Data Fixtures --------------------------------------------------------


@pytest.fixture()
def bare_user(app, db):
    """A minimal user fixture: User + UserSettings only.

    No account, no pay periods, no scenario.  Tests that exercise
    services in isolation (e.g. ``pay_period_service``) and want a
    clean user state with no pre-existing pay periods use this
    instead of ``seed_user``, which after E-19 / Commit 3 must seed
    a bootstrap period so its default account satisfies the NOT NULL
    anchor columns.

    Returns:
        dict with keys: user, settings.
    """
    user = User(
        email="bare@shekel.local",
        password_hash=hash_password("barepass-12345"),
        display_name="Bare User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    db.session.add(settings)
    db.session.commit()

    return {"user": user, "settings": settings}


@pytest.fixture()
def bare_auth_client(app, db, client, bare_user):  # pylint: disable=unused-argument
    """Provide an authenticated test client for the bare user.

    Companion to ``auth_client``: bare_user has no account, no
    scenario, no categories, and (importantly for pay_period_service
    tests) no bootstrap pay period.  Use this in tests that need a
    logged-in session against a user with no pre-existing financial
    data.
    """
    resp = client.post("/login", data={
        "email": "bare@shekel.local",
        "password": "barepass-12345",
    })
    assert resp.status_code == 302, (
        f"bare_auth_client login failed with status {resp.status_code}"
    )
    return client


@pytest.fixture()
def bare_periods(app, db, bare_user):
    """Generate 10 pay periods for ``bare_user`` starting 2026-01-02.

    The bare_user-companion to ``seed_periods`` for tests that test
    the pay-period service contract in isolation: bare_user has no
    pre-existing periods, so the generated periods take indices
    0..9, exactly as the pre-Commit-3 ``seed_periods`` did against
    seed_user.

    Returns:
        List of PayPeriod objects.
    """
    from app.services import pay_period_service  # pylint: disable=import-outside-toplevel

    periods = pay_period_write.record_paydays(
        user_id=bare_user["user"].id,
        first_payday=date(2026, 1, 2),
        num_periods=10,
        cadence_days=14,
    )
    db.session.commit()
    return periods


@pytest.fixture()
def seed_user(app, db):
    """Create and return a test user with settings, account, and scenario.

    Returns:
        dict with keys: user, settings, account, scenario, categories.
    """
    user = User(
        email="test@shekel.local",
        password_hash=hash_password("testpass"),
        display_name="Test User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    # Bootstrap pay period (E-19, Commit 3): every account row has
    # non-NULL anchor columns post-migration cfb15e782f86, so this
    # fixture needs at least one period in place before the Checking
    # account can be created.  Date is an arbitrary Friday well
    # before any test's typical 2026 range, so the bootstrap stays
    # out of the way of date-anchored assertions.  The period is
    # exposed as ``seed_user["bootstrap_period"]`` so tests that
    # create additional accounts inline can anchor them to it.
    bootstrap_period = PayPeriod(
        user_id=user.id,
        start_date=date(2024, 1, 5),
        end_date=date(2024, 1, 18),
        period_index=0,
    )
    db.session.add(bootstrap_period)
    db.session.flush()

    # Baseline scenario BEFORE the account, matching production
    # registration order (Build-Order Step 5): ``create_account`` posts the
    # opening anchor correction into every scenario, so the baseline must
    # exist first -- the seeded Checking then carries its $1000.00 opening
    # on the posting ledger from t0, exactly like a production account.
    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    # Default Checking account via the canonical factory.  Tests that
    # later true-up the anchor produce additional AccountAnchorHistory
    # rows; the factory's origination row is here from t0 so the
    # post-Commit-4 resolver reads a consistent event stream.
    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=checking_type.id,
            name="Checking",
            anchor_balance=Decimal("1000.00"),
            # Day one of the period the account is anchored to -- the
            # production shape, an account opened on day one of its own period
            # (ruling R-DH, plan step 2).  Without it the origination asserts a
            # balance on the WALL-CLOCK day while its ``pay_period_id`` points
            # at the 2024 bootstrap: two clocks on one row, years apart.
            #
            # **It is what makes "and then things happened" say so.**  An
            # assertion is the CLOSING balance for its civil day, so a settle
            # dated that day is INSIDE it.  ``tests/test_services/conftest.py``
            # freezes today, and the ordinary settle idiom dates the row on the
            # user's today -- so an origination left on the
            # frozen clock lands on the very civil day the settles do, and every
            # fixture meaning "an account existed, then money moved" silently
            # became "money moved on the opening's own day".  Those fixtures
            # passed only while the OPENING carried a partition exception
            # (finding N-133 / F1); this is N-132's shape one layer up.
            #
            # Supplied to the factory rather than re-stamped afterwards,
            # because ``create_account`` posts the opening's anchor correction
            # keyed on this day: a later re-stamp would leave the ledger
            # holding a stale key plus its reversal in every seeded database.
            observed_on=bootstrap_period.start_date,
        ),
    )

    # Create default categories.
    categories = []
    for group, item in [
        ("Income", "Salary"),
        ("Home", "Rent"),
        ("Auto", "Car Payment"),
        ("Family", "Groceries"),
        ("Credit Card", "Payback"),
    ]:
        cat = Category(
            user_id=user.id,
            group_name=group,
            item_name=item,
        )
        db.session.add(cat)
        categories.append(cat)
    db.session.flush()

    db.session.commit()

    return {
        "user": user,
        "settings": settings,
        "account": account,
        "scenario": scenario,
        "categories": {c.item_name: c for c in categories},
        "bootstrap_period": bootstrap_period,
    }


def _pin_opening_to(db, account, anchor_period):
    """Pin ``account``'s OPENING assertion to *anchor_period*'s first day.

    The cross-page fixtures keep the ``seed_user`` bootstrap period and then
    append their own anchor override, so unlike the ``seed_periods*`` fixtures
    they never reach :func:`_drop_seed_user_bootstrap`'s re-stamp.  Without
    this the opening keeps ``create_account``'s WALL-CLOCK instant, which falls
    INSIDE the fixture's anchor month and therefore AFTER the override the
    fixture writes at that month's start -- so the cash walk replays the
    $1,000.00 origination LAST and it silently supersedes the balance the
    fixture just asserted.  (The shipping producers never saw it: they read the
    newest row and ignored its date.)

    Args:
        db: The SQLAlchemy ``db`` fixture.
        account: The account whose opening to pin.
        anchor_period: The period the fixture is about to anchor against.
    """
    # Day one of the anchor period IN THE USER'S ZONE -- the same meaning, and
    # the same reason, as ``override_anchor``'s default (ruling R-DH (b)).  The
    # two must agree: pinning one to Eastern midnight and leaving the other on
    # UTC midnight puts the opening and the override in DIFFERENT periods.
    restamp_opening_assertion(
        db.session, account,
        datetime.combine(
            anchor_period.start_date, time.min, tzinfo=DISPLAY_TIMEZONE,
        ).astimezone(timezone.utc),
    )


def _drop_seed_user_bootstrap(db, seed_user, account, new_anchor_period):
    """Replace ``seed_user``'s bootstrap pay period with the supplied new
    anchor and renumber the user's remaining periods to start at 0.

    The ``seed_user`` fixture provisions a ``period_index=0`` bootstrap
    so the account factory has something to anchor against (E-19 /
    Commit 3 makes that anchor NOT NULL).  Periods fixtures
    (``seed_periods``, ``seed_periods_today``, etc.) generate the
    user's "real" pay-period set after the bootstrap; those rows
    therefore take indices 1..N.  Without cleanup, every existing
    test that counts user pay periods or asserts ``periods[0].period_index == 0``
    drifts by 1.

    This helper restores the pre-Commit-3 expectation in one place:
    (1) repoints the account's anchor (and any matching
    AccountAnchorHistory row) at the supplied ``new_anchor_period``,
    (2) deletes the bootstrap (CASCADE removes the bootstrap's
    history row and any transactions in it -- there should be none
    at fixture-setup time), (3) renumbers the surviving periods to
    start at 0.

    Args:
        db: the SQLAlchemy ``db`` fixture.
        seed_user: dict returned by the ``seed_user`` fixture.
        account: the account whose anchor must be repointed.
        new_anchor_period: the period to anchor the account against
            after the bootstrap is removed.

    Returns:
        None.  The mutation is committed before returning so
        subsequent fixture/test code sees the cleaned state.
    """
    bootstrap = seed_user.get("bootstrap_period")
    if bootstrap is None:
        return
    # Re-fetch by id -- the cached object might be stale across the
    # nested commits below.
    bootstrap_id = bootstrap.id
    # Step 1: repoint the account anchor.
    # Restamp any assertion the factory wrote against the bootstrap period.
    # It no longer has to SURVIVE anything -- ruling R-EO deleted
    # ``AccountAnchorHistory.pay_period_id`` and its CASCADE FK, so a period
    # delete cannot take an assertion with it -- but its INSTANT and its
    # business DAY still have to move onto the new anchor period, for the
    # reason below.
    from app.models.account import AccountAnchorHistory  # pylint: disable=import-outside-toplevel
    # The row's INSTANT moves with its period, not just its FK.  The account
    # factory stamps the opening with the WALL CLOCK, while the suites freeze
    # today inside their own seeded range -- so an unrestamped opening sorts
    # AFTER every controlled assertion a test writes, which silently inverts
    # which row the cash fold treats as the opening (ruling R-I books the
    # FIRST assertion into its seed and keeps every later one as a reset).
    # Pinning it to the new anchor period's first day is the production shape:
    # an account opened on day one of the period it is anchored to.
    db.session.query(AccountAnchorHistory).filter_by(
        account_id=account.id,
    ).update({
        # The BUSINESS day moves with the period and the instant.  Leaving it
        # behind is the "two clocks on one row" shape ``seed_user`` states it
        # is eliminating, recreated one layer down: the row would assert a
        # 2026 period from a 2024 day, and its posted correction would carry a
        # 2024 ``purchased_on`` inside a 2026 ``pay_period_id``.
        "observed_on": new_anchor_period.start_date,
        # Eastern midnight, converted for storage -- NOT midnight UTC, which
        # is the previous EVENING in the display zone and would file the
        # opening one day before its own period (finding N-132).
        "created_at": datetime.combine(
            new_anchor_period.start_date, time.min, tzinfo=DISPLAY_TIMEZONE,
        ).astimezone(timezone.utc),
    })
    db.session.flush()
    # Step 2: delete the bootstrap row.
    db.session.query(PayPeriod).filter_by(id=bootstrap_id).delete()
    db.session.flush()
    # Step 3: renumber remaining periods to start at 0.
    db.session.execute(_db.text(
        "UPDATE budget.pay_periods "
        "SET period_index = period_index - 1 "
        "WHERE user_id = :u"
    ), {"u": seed_user["user"].id})
    # Step 4: re-post the anchor corrections the bootstrap delete disposed
    # (Build-Order Step 5).  The seeded Checking's $1000 opening entry was
    # attributed to the bootstrap period (journal_entries.pay_period_id is
    # ON DELETE CASCADE), so step 2 took it with the period; the history
    # rows survived via the step-1 repoint, so the per-user resync
    # re-derives the openings onto the new anchor period -- the same
    # re-derivation the production pay-period reset performs.
    from app.services import account_posting_service  # pylint: disable=import-outside-toplevel
    account_posting_service.resync_user_account_anchor_postings(
        seed_user["user"].id,
    )
    db.session.commit()
    # Refresh the in-memory period rows the caller will use.
    db.session.expire_all()


@pytest.fixture()
def seed_periods(app, db, seed_user):
    """Generate 10 pay periods starting from 2026-01-02.

    Also sets the anchor period to the first period and removes the
    ``seed_user`` bootstrap (see ``_drop_seed_user_bootstrap`` for
    the rationale) so the returned periods occupy indices 0..9 as
    pre-Commit-3 tests expect.

    Returns:
        List of PayPeriod objects.
    """
    from app.services import pay_period_service

    periods = pay_period_write.record_paydays(
        user_id=seed_user["user"].id,
        first_payday=date(2026, 1, 2),
        num_periods=10,
        cadence_days=14,
    )
    db.session.flush()

    account = seed_user["account"]
    _drop_seed_user_bootstrap(db, seed_user, account, periods[0])
    # Reload periods so callers see the renumbered period_index values.
    return (
        db.session.query(PayPeriod)
        .filter_by(user_id=seed_user["user"].id)
        .order_by(PayPeriod.period_index)
        .all()
    )


def _today_relative_start_date():
    """Return start_date that places today in period 4 of a 10-period biweekly run.

    Period 4 is the middle of a 10-period window, leaving 4 historical
    periods and 5 future periods.  The start is aligned to the most
    recent Monday so period boundaries fall on weekdays consistently.
    Used by ``seed_periods_today``-style fixtures so that
    ``pay_period_service.get_current_period`` always returns a real
    period regardless of the wall-clock date.
    """
    today = display_today()
    return today - timedelta(days=today.weekday() + 4 * 14)


@pytest.fixture()
def seed_periods_today(app, db, seed_user):
    """Generate 10 biweekly pay periods so today falls in period 4.

    Use this fixture when the test exercises a code path that calls
    ``pay_period_service.get_current_period()`` (directly or via a
    route handler).  Use the regular ``seed_periods`` fixture when the
    test asserts on specific calendar dates (due_date filters,
    year-end summaries for tax_year=2026, loan origination alignment).

    A test must use one or the other, never both -- they would write
    overlapping pay_periods rows for the same user.

    Returns:
        List of PayPeriod objects, ordered by period_index.
    """
    from app.services import pay_period_service  # pylint: disable=import-outside-toplevel

    periods = pay_period_write.record_paydays(
        user_id=seed_user["user"].id,
        first_payday=_today_relative_start_date(),
        num_periods=10,
        cadence_days=14,
    )
    db.session.flush()

    account = seed_user["account"]
    _drop_seed_user_bootstrap(db, seed_user, account, periods[0])
    return (
        db.session.query(PayPeriod)
        .filter_by(user_id=seed_user["user"].id)
        .order_by(PayPeriod.period_index)
        .all()
    )


@pytest.fixture()
def auth_client(app, db, client, seed_user):
    """Provide an authenticated test client.

    Logs in via the login form to get a proper session.
    """
    resp = client.post("/login", data={
        "email": "test@shekel.local",
        "password": "testpass",
    })
    assert resp.status_code == 302, (
        f"auth_client login failed with status {resp.status_code}"
    )
    return client


def _build_cross_page_calendar_periods(db, user):
    """Build 36 calendar-monthly pay periods for the cross-page lock fixtures.

    The shared period-construction step behind ``seed_cross_page_account``
    and every per-kind cross-page fixture (loan / property / investment /
    secured).  Creates one period per calendar month spanning
    ``[today.year - 1, today.year + 1]`` (``period_index`` 1..36, sitting
    cleanly above the ``seed_user`` bootstrap at index 0), then returns the
    full ordered period list and the anchor period (the calendar month
    containing today).

    Monthly (not biweekly) periods are deliberate: the anchor period's
    ``end_date`` IS a calendar month-end, so the C9-3 boundary invariant of
    the seam's :func:`app.services.balance_at.cash_balance_at` makes the
    calendar surface's projected month-end balance equal the anchor-period
    balance for the same data.  The anchor being the month containing today also
    lets ``pay_period_service.get_current_period`` land on it with no
    date-mock plumbing.

    The ``seed_user`` bootstrap pay period is left in place rather than
    deleted via ``_drop_seed_user_bootstrap``: deleting it cascades the
    ``AccountAnchorHistory.pay_period_id`` ondelete=CASCADE and forces an
    autoflush UPDATE on the account anchor mid-flush, which races the
    just-flushed new pay periods on stricter autoflush orderings (observed:
    ``ForeignKeyViolation`` on ``current_anchor_period_id``).  Keeping the
    bootstrap is benign for the lock: it is a 2024 pre-anchor period every
    surface skips (the resolver only emits balances from the anchor period
    forward, and grid / dashboard / savings / accounts all key off
    ``get_current_period``, which matches today's month, not the bootstrap).

    Args:
        db: The SQLAlchemy ``db`` fixture.
        user: The owning :class:`~app.models.user.User` (its ``id`` scopes
            the periods).

    Returns:
        ``(all_periods, anchor_period)`` -- the user's full period list
        ordered by ``period_index`` (bootstrap at 0 plus the 36 monthly
        periods) and the anchor period containing today.
    """
    today = date.today()
    first_year = today.year - 1
    period_index = 1
    created = []
    for year in range(first_year, first_year + 3):
        for month in range(1, 13):
            start = date(year, month, 1)
            # last day of month: subtract one from the first of next month
            # (December rolls to next year).
            if month == 12:
                next_first = date(year + 1, 1, 1)
            else:
                next_first = date(year, month + 1, 1)
            end = next_first - timedelta(days=1)
            period = PayPeriod(
                user_id=user.id,
                start_date=start,
                end_date=end,
                period_index=period_index,
            )
            db.session.add(period)
            created.append(period)
            period_index += 1
    db.session.commit()

    # The anchor period is the calendar month containing today.  The
    # created list runs chronologically from January of ``first_year``, so
    # today's month sits at index ``12 + (today.month - 1)``.
    anchor_period = created[12 + (today.month - 1)]
    assert anchor_period.start_date <= today <= anchor_period.end_date, (
        f"anchor_period {anchor_period.start_date}..{anchor_period.end_date} "
        f"does not contain today={today}; fixture invariant broken"
    )

    all_periods = (
        db.session.query(PayPeriod)
        .filter_by(user_id=user.id)
        .order_by(PayPeriod.period_index)
        .all()
    )
    return all_periods, anchor_period


def _neutralize_seed_checking(db, seed_user, anchor_period):
    """Re-anchor the ``seed_user`` Checking account to $0 at *anchor_period*.

    The per-kind cross-page fixtures isolate a single non-cash account so
    the AGGREGATE surfaces (year-end net worth, the savings net-worth
    trend) reflect ONLY that account.  ``seed_user`` always provisions one
    Checking account at a $1,000 anchor, so this neutralises it: it appends
    a $0 ``AccountAnchorHistory`` row at *anchor_period* (latest-wins by
    ``created_at``, so ``resolve_anchor`` returns $0) and re-points the
    account's anchor cache to the same period and balance.  A $0 asset
    anchored at the current period contributes 0 to every net-worth sum
    from the current period forward and gates the trend at the current
    period, so the per-kind account's value stands alone.

    The account is re-fetched by primary key because the period-build
    commit expires every object loaded upstream in ``seed_user``; setting
    attributes on an expired instance does not reliably re-mark the row
    dirty in every SQLAlchemy ORM mode (the same refetch
    ``seed_cross_page_account`` performs for its own anchor override).

    Args:
        db: The SQLAlchemy ``db`` fixture.
        seed_user: The ``seed_user`` fixture dict.
        anchor_period: The period to re-anchor the Checking account to (the
            current / anchor period of the per-kind fixture).
    """
    # Pylint: ``import-outside-toplevel`` -- Account is intentionally not
    # imported at conftest top (every fixture builds accounts via the
    # canonical factory); load it lazily, as seed_cross_page_account does.
    # pylint: disable=import-outside-toplevel
    from app.models.account import Account
    from tests._test_helpers import override_anchor

    account = db.session.get(Account, seed_user["account"].id)
    _pin_opening_to(db, account, anchor_period)
    override_anchor(
        db.session, account, anchor_period, Decimal("0.00"),
    )


@pytest.fixture()
def seed_cross_page_account(app, db, seed_user):
    """Factory fixture for the PT-01 cross-page balance equality lock (HIGH-01).

    Returns a callable ``build(anchor_balance, expense_amount, entries)``
    that materialises one symptom-tuple case shared across every
    balance-rendering surface (grid, /savings, /accounts checking detail,
    dashboard, year-end net-worth per-account, calendar).  The fixture
    realises the structural premise of HIGH-01 -- the developer's worst
    two symptoms (#1 $160 grid vs $114.29 /savings; #5 /accounts matches
    nowhere) had zero falsifying tests until this lock landed (Commit 11).

    The realised data shape is invariant across cases:

      * The user's existing seed_user bootstrap pay period is removed and
        replaced with 24 calendar-monthly periods spanning
        ``[today.year - 1, today.year + 1]``.  Monthly (not biweekly)
        periods are chosen deliberately so the anchor period's
        ``end_date`` IS a calendar month-end -- the C9-3 boundary
        invariant of the seam's :func:`app.services.balance_at.cash_balance_at`
        then guarantees the calendar surface's projected month-end balance
        equals the anchor-period balance for the same data.
        Without that alignment a mid-period month-end would silently make
        the calendar surface look like a divergence even when the
        underlying math agrees, defeating the cross-page lock.
      * The anchor period is the calendar month containing
        ``date.today()`` (so the dashboard's ``get_current_period`` and
        the grid's ``get_periods_in_range(current_period.period_index,
        ...)`` both naturally land on the anchor period without any
        date-mock plumbing).
      * The account anchor is overridden -- via a fresh
        ``AccountAnchorHistory`` row + cache-column update, latest-wins
        per E-19 -- to the case's ``anchor_balance``.  ``seed_user``'s
        factory-default $1,000 anchor is irrelevant here.
      * A single Projected envelope expense in the anchor period with
        ``estimated_amount = expense_amount`` and the supplied entries
        list, each entry dated ``anchor_period.start_date`` (so all
        entries fall on or before any month-end ``as_of`` the calendar
        surface evaluates).

    The factory returns a context dict keyed:

      * ``user_id``, ``account``, ``account_id``, ``scenario``,
        ``scenario_id``: identifiers callers pass to every surface.
      * ``all_periods``, ``anchor_period``: the period list and the
        chosen anchor period.
      * ``year``, ``month``: ``anchor_period.start_date.year /
        .month`` -- the calendar/year-end surfaces consume these.

    The five remediation-plan cases (PT-01 base, zero anchor, negative
    overdraft, credit-only entries, uncleared-floor) are realised by the
    test's ``@pytest.mark.parametrize`` block, not baked into the
    fixture, so a future case can be added in one place without growing
    a conftest variant.

    Returns:
        Callable ``(anchor_balance, expense_amount, entries) ->
        dict``.  Each entry is a 3-tuple ``(amount, is_credit,
        is_settled)`` -- amount as ``Decimal`` (from string), and the two
        booleans as the entries-aware reduction's discriminants.
        ``is_settled`` stamps the purchase's ``settled_on`` with the anchor
        period's start day, which is the assertion's own ``observed_on``
        here, so the purchase reads as already inside the asserted balance
        (ruling R-DH (d)); ``False`` leaves it NULL and outstanding.
    """
    # pylint: disable=import-outside-toplevel
    from app.models.account import Account
    from app.models.transaction_entry import TransactionEntry
    from tests._test_helpers import override_anchor

    def _build(
        anchor_balance: Decimal,
        expense_amount: Decimal,
        entries: list[tuple[Decimal, bool, bool]],
    ) -> dict:
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]

        # Build the calendar-monthly period grid and locate the anchor
        # month containing today via the shared period builder (which also
        # documents why the seed_user bootstrap is deliberately kept).
        all_periods, anchor_period = _build_cross_page_calendar_periods(
            db, user,
        )

        # Override the anchor balance and matching history row.  The
        # ``seed_user`` factory already wrote an origination history
        # row at $1,000 against the bootstrap period; appending a
        # newer row makes ``resolve_anchor`` (latest-wins by
        # ``created_at``) return the case's balance instead.  The
        # cache columns are updated in the same flush so the
        # resolver's cache-reconciliation path stays quiet (cache ==
        # latest event), which keeps the test log free of spurious
        # ``EVT_ANCHOR_CACHE_RECONCILED`` entries.
        #
        # Re-fetch ``account`` against the live session because the
        # earlier ``db.session.commit()`` (after the new period
        # inserts) expires every object loaded in this session
        # (``expires_on_commit=True``).  Setting attributes on an
        # expired instance whose load fixture lives upstream in
        # ``seed_user`` does not reliably re-mark the row dirty in
        # every SQLAlchemy ORM mode -- the symptom is a silent
        # ``EVT_ANCHOR_CACHE_RECONCILED`` log entry on every surface
        # read because the cache column did not actually move.
        # Refetching by primary key gives us a known-attached
        # instance whose attribute assignments are guaranteed to
        # mark the row dirty for the next flush.
        account = db.session.get(Account, seed_user["account"].id)
        _pin_opening_to(db, account, anchor_period)
        override_anchor(
            db.session, account, anchor_period, anchor_balance,
        )

        # Single Projected envelope expense in the anchor period.
        # ``is_envelope=True`` is what makes the entries-aware
        # reduction applicable -- a non-envelope template would short-
        # circuit to ``effective_amount`` regardless of entries.
        projected_status = (
            db.session.query(Status).filter_by(name="Projected").one()
        )
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )
        groceries_cat = seed_user["categories"]["Groceries"]
        template = TransactionTemplate(
            user_id=user.id,
            account_id=account.id,
            category_id=groceries_cat.id,
            transaction_type_id=expense_type.id,
            name="PT-01 envelope expense",
            default_amount=expense_amount,
            is_envelope=True,
        )
        db.session.add(template)
        db.session.flush()

        txn = Transaction(
            template_id=template.id,
            pay_period_id=anchor_period.id,
            scenario_id=scenario.id,
            account_id=account.id,
            status_id=projected_status.id,
            name="PT-01 envelope expense",
            category_id=groceries_cat.id,
            transaction_type_id=expense_type.id,
            estimated_amount=expense_amount,
        )
        db.session.add(txn)
        db.session.flush()

        # Entries all dated on anchor_period.start_date -- a date that
        # is on or before every month-end ``as_of`` the calendar
        # surface evaluates, so the E-27 entry-date cut is a no-op
        # for this fixture and the calendar surface's balance equals
        # the resolver's anchor-period balance by construction.
        for amount, is_credit, is_settled in entries:
            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=user.id,
                amount=amount,
                description="PT-01 entry",
                purchased_on=anchor_period.start_date,
                # The assertion ``override_anchor`` wrote is observed on this
                # same day, so a purchase settled on it is INSIDE that balance
                # -- the state the retired ``settled_on=anchor_period.start_date`` flag named.
                settled_on=(
                    anchor_period.start_date if is_settled else None
                ),
                is_credit=is_credit,
            ))
        db.session.commit()

        return {
            "user_id": user.id,
            "account": account,
            "account_id": account.id,
            "scenario": scenario,
            "scenario_id": scenario.id,
            "all_periods": all_periods,
            "anchor_period": anchor_period,
            "year": anchor_period.start_date.year,
            "month": anchor_period.start_date.month,
        }

    return _build


@pytest.fixture()
def cross_page_loan_ctx(db, seed_user):
    """Single isolated amortizing loan for the cross-page equality lock.

    Builds the shared calendar-monthly period grid, neutralises the
    seed_user Checking account to $0 (so the AGGREGATE surfaces -- year-end
    net worth, the savings net-worth trend -- reflect the loan alone), then
    creates ONE amortizing loan whose original principal P differs from its
    trued-up current balance C: ``create_loan_account`` sets P = $240,000 at
    origination, and an ``insert_trueup_event`` dated today asserts C =
    $200,000.  The trueup makes the resolver schedule today-forward (first
    payment next month), so every period up to and including the anchor
    reports C held flat -- NEVER P.  ``C != P`` is what makes the boundary
    assertion non-tautological (returning P at a pre-payment period is the
    exact historical bug PR #44 / aba0242 fixed).

    Returns a ctx dict mirroring ``seed_cross_page_account`` plus ``C``,
    ``P``, and ``pre_anchor_period`` (a period strictly before the anchor,
    where the balance map must equal C and not P).
    """
    # Pylint: ``import-outside-toplevel`` -- LoanParams is loaded lazily,
    # the Account-class convention this conftest follows (no model packages
    # imported at module top).
    # pylint: disable=import-outside-toplevel
    from app.models.loan_params import LoanParams

    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    today = date.today()
    original_principal = Decimal("240000.00")  # P
    current_balance = Decimal("200000.00")     # C (trued up today); C != P
    loan = create_loan_account(
        seed_user, db.session, name="Cross-Page Loan",
        principal=original_principal, term=360,
        origination_date=date(today.year - 1, 1, 1),
    )
    params = db.session.query(LoanParams).filter_by(account_id=loan.id).one()
    insert_trueup_event(params, current_balance, anchor_date=today)
    db.session.commit()

    # Pre-anchor period: the period immediately before the anchor (current)
    # period.  The trueup is dated today and the loan's first scheduled
    # payment is next month, so this period reports C held flat -- the
    # boundary the bug used to fill with the original principal P.
    anchor_pos = next(
        i for i, p in enumerate(all_periods) if p.id == anchor_period.id
    )
    pre_anchor_period = all_periods[anchor_pos - 1]

    return {
        "user_id": user.id,
        "account": loan,
        "account_id": loan.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "C": current_balance,
        "P": original_principal,
        "pre_anchor_period": pre_anchor_period,
    }


@pytest.fixture()
def cross_page_loan_unpaid_ctx(db, seed_user):
    """An amortizing loan originated in the PAST with NO payment and NO true-up.

    The loan shape the cross-page lock could not previously see: the one in which
    a schedule-walking producer CAN phantom-pay the debt down.
    ``cross_page_loan_ctx`` asserts a true-up dated TODAY, which re-anchors the
    resolver's schedule today-forward and so leaves no past-dated UNPAID rows
    behind; this fixture deliberately leaves them.  Originated 18 months ago at
    $240,000 over 360 months, never paid and never trued up, the resolver's
    schedule carries ~17 PROJECTED installments dated on or before today.

    Every one of those rows is a payment that was never made, so not one dollar of
    principal was ever paid: the honest balance is the full $240,000 opening, on
    EVERY surface and at EVERY period.  A producer that walked the schedule instead
    of the ledger would report LESS.

    The loan IS opened in the ledger (``create_loan_account`` writes through
    production's reconcile path), exactly as a real configured loan is -- so on
    the current code every surface reads the ledger and agrees at $240,000, and
    the accompanying test PASSES.  It is a regression lock, not a reproducer: it
    holds the line against a schedule producer being reintroduced for the past,
    which is what let the /savings tile and the net-worth trend disagree on their
    own 'today' point (a no-ledger path production never took, but every loan test
    did).

    Returns a ctx dict mirroring ``cross_page_loan_ctx``, plus ``P`` (the original
    principal, which is also the correct balance everywhere) and
    ``past_period`` (a period strictly before today, where an unpaid projected
    installment would have phantom-paid the debt down).
    """
    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    today = date.today()
    original_principal = Decimal("240000.00")  # P -- never paid down
    loan = create_loan_account(
        seed_user, db.session, name="Never-Paid Loan",
        principal=original_principal, rate=Decimal("0.06000"), term=360,
        origination_date=today - timedelta(days=548),
    )
    db.session.commit()

    anchor_pos = next(
        i for i, p in enumerate(all_periods) if p.id == anchor_period.id
    )
    past_period = all_periods[anchor_pos - 1]

    return {
        "user_id": user.id,
        "account": loan,
        "account_id": loan.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "P": original_principal,
        "past_period": past_period,
    }


def _unseeded_replay_balance(loan_id, scenario_id, as_of):
    """Return a loan's un-seeded schedule-replay balance (the pre-switch value).

    The balance the resolver derives from its anchor replay ALONE -- no genesis
    seed -- so a cross-page fixture can pin what a loan's scalar surfaces showed
    BEFORE the read switch and assert the ledger diverges from it off-schedule.
    """
    # Pylint: ``import-outside-toplevel`` -- the app services are loaded lazily,
    # the convention every helper in this conftest follows.
    # pylint: disable=import-outside-toplevel
    from app.services import loan_loaders, loan_payment_service, loan_resolver
    from app.services.loan_resolver._periods import _replay_from_anchor
    from app.utils.money import round_money

    params = loan_loaders.load_loan_params(loan_id)
    ctx = loan_payment_service.load_loan_context(loan_id, scenario_id, params)
    inputs = loan_resolver.LoanInputs(
        params, loan_loaders.load_loan_anchor_facts(params),
        ctx.payments, ctx.rate_changes,
    )
    # The replay derivation directly: ``LoanState.current_balance`` carried it
    # until plan step D2a deleted the field (the seam folds displayed balances).
    periods = loan_resolver.resolve_periods(params, inputs.rate_changes)
    return round_money(
        _replay_from_anchor(inputs, periods, as_of).balance_as_of
    )


@pytest.fixture()
def cross_page_loan_off_schedule_ctx(db, seed_user):
    """A GENESIS loan with an OFF-SCHEDULE confirmed payment (the read switch).

    Unlike ``cross_page_loan_ctx`` -- which posts no genesis and so resolves via
    the anchor-replay fallback -- this loan is OPENED in the ledger (a settled
    payment fires the genesis sync: opening + true-up corrections + the payment
    split) and its one confirmed payment pays cash far above the scheduled P&I,
    so the REAL principal it books down diverges from the schedule replay.  The
    read switch (plan Section 8) makes the SCALAR surfaces -- the /savings tile
    (``_compute_loan_account``) and the loan-detail page (the ``balance_at``
    seam scalar since plan C4) -- read that real ledger balance, NOT the replay.

    Returns the reader-shaped ctx plus ``ledger`` (the genesis reader's balance,
    what the surfaces must now show) and ``replay`` (the un-seeded resolver's
    schedule balance, what they showed BEFORE the switch); the test asserts the
    two DIVERGE so the "surfaces == ledger" equality is non-vacuous.  Since the
    Section 9 per-period read switch, the year-end and net-worth-trend MAP
    surfaces ALSO read the confirmed ledger off-schedule, so the whole
    four-surface set agrees on the real balance (see
    ``test_all_surfaces_read_the_ledger_off_schedule``).
    """
    # Pylint: ``import-outside-toplevel`` -- the app services / test helpers are
    # loaded lazily, the convention every fixture in this conftest follows.
    # pylint: disable=import-outside-toplevel
    from tests._test_helpers import (
        create_loan_with_trueup,
        create_settled_transfer,
    )

    user = seed_user["user"]
    scenario = seed_user["scenario"]
    today = date.today()
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    # Origination two years back at $250k, trued up to $200k a year back (before
    # the payment), 6% fixed.  origination != anchor so the walk seeds from the
    # true-up, and the loan carries the opening + true-up genesis corrections.
    loan = create_loan_with_trueup(
        seed_user, db.session,
        origination_principal=Decimal("250000.00"),
        anchor_balance=Decimal("200000.00"),
        anchor_date=date(today.year - 1, 1, 1),
        rate=Decimal("0.06000"),
        origination_date=date(today.year - 2, 1, 1),
        name="Off-Schedule Loan",
    )

    # One OFF-SCHEDULE payment ($5,000 cash vs ~$1,499 scheduled P&I) two months
    # before today, so its pay period has begun and the ledger books the extra
    # ~$3,500 of real principal the schedule replay drops on the floor.
    anchor_idx = next(
        i for i, p in enumerate(all_periods) if p.id == anchor_period.id
    )
    payment_period = all_periods[anchor_idx - 2]
    # Settled on its own period start (a past date), so it is visible today under
    # C2's settled-date clock regardless of the UTC/display-tz offset.
    create_settled_transfer(
        seed_user, db.session, seed_user["account"], loan, payment_period,
        amount=Decimal("5000.00"),
        settled_on=payment_period.start_date,
    )
    db.session.commit()

    # The genesis reader (what the surfaces now read) vs the un-seeded schedule
    # replay (what they read before the switch): off-schedule they DIVERGE.
    ledger = posted_loan_balance_at(
        loan.id, scenario.id, today,
    )
    replay = _unseeded_replay_balance(loan.id, scenario.id, today)

    return {
        "user_id": user.id,
        "account": loan,
        "account_id": loan.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "ledger": ledger,
        "replay": replay,
    }


@pytest.fixture()
def cross_page_property_ctx(db, seed_user):
    """Single isolated appreciating Property, anchor == current period.

    Builds the shared period grid, neutralises the seed_user Checking to
    $0, then creates ONE Property whose user-set market value is V =
    $400,000, anchored at the current period.

    **Anchor == current no longer means the surfaces read V** (plan step X-g2b,
    ruling R-Y): the anchor period earns its own days now, where the shipped
    producer split on ``period_index > anchor_idx`` and served that period from
    a flat carry.  What the surfaces must still agree on is the VALUE, whatever
    it is -- which is what the cross-page classes assert, and what makes them a
    wiring lock rather than a figure pin.  Returns a ctx dict mirroring
    ``seed_cross_page_account`` plus ``V``, the ASSERTED value the modelled one
    is measured against.
    """
    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    value = Decimal("400000.00")  # V
    prop = make_appreciating_account(
        seed_user, db.session, anchor_period, value, Decimal("0.03000"),
    )

    return {
        "user_id": user.id,
        "account": prop,
        "account_id": prop.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "V": value,
    }


@pytest.fixture()
def cross_page_investment_ctx(db, seed_user):
    """Single isolated Investment, anchor == current period, no contribution.

    Builds the shared period grid, neutralises the seed_user Checking to
    $0, then creates ONE 401(k) whose balance is V = $100,000, anchored at
    the current period, with no employer match and no contribution feed.
    Anchor == current used to mean every surface read the anchor balance V,
    because the growth projection skipped the anchor period entirely.  **Ruling
    R-Y gives that period its own days** (plan step X-g2b), so the surfaces read
    V plus that growth; what they must still do is agree, which is what the
    consuming test asserts -- reading the expected figure from the seam and
    pinning it strictly above V.  Returns a ctx dict mirroring
    ``seed_cross_page_account`` plus ``V``, the ASSERTED balance.
    """
    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    value = Decimal("100000.00")  # V
    inv = make_investment_account(seed_user, db.session, anchor_period, value)

    return {
        "user_id": user.id,
        "account": inv,
        "account_id": inv.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "V": value,
    }


@pytest.fixture()
def cross_page_investment_past_anchor_ctx(db, seed_user):
    """Single isolated Investment anchored 6 monthly periods IN THE PAST.

    The model-from-anchor divergence fixture the Level 1 savings-tile reroute
    (the :mod:`app.services.balance_at` seam) unlocks.  A 401(k) with opening
    balance ``V0`` = $100,000 and a 7% assumed return
    (``make_investment_account``), anchored 6 monthly periods BEFORE today's
    period, with no contribution feed -- so the only post-anchor movement is
    compounding.  The cash-basis carry holds ``V0`` flat to today; the
    kernel's model-from-anchor map compounds ``V0`` forward to today, so the
    modeled balance at the current period is STRICTLY GREATER than ``V0``.
    That gap is what makes the cross-producer lock non-tautological: before
    the reroute the /savings tile read the flat ``V0`` while the year-end and
    net-worth-trend surfaces read the modeled value, and the reroute makes the
    tile adopt the modeled value the other kernel surfaces already report.

    Contrast ``cross_page_investment_ctx`` (anchor == current), where the
    divergence is only that period's own growth (ruling R-Y) rather than a whole
    anchor-to-today span.

    The seed_user Checking is neutralised to $0 so the AGGREGATE surfaces
    (year-end net worth, the savings net-worth trend) reflect the investment
    alone.  Returns a ctx dict mirroring the other per-kind fixtures plus
    ``V0`` (the flat cash-basis carry) and ``current_period`` (today's period,
    where every surface is read); ``anchor_period`` here is today's period
    (the dashboard's current period), NOT the account's past anchor.
    """
    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    # Anchor the investment 6 monthly periods before today's period so the
    # model-from-anchor map compounds it forward across that gap.  The grid
    # puts today's period well above index 6 (bootstrap at 0, then 12 prior
    # monthly periods before today's month), so ``anchor_pos - 6`` is always a
    # real past period.
    anchor_pos = next(
        i for i, p in enumerate(all_periods) if p.id == anchor_period.id
    )
    past_anchor_period = all_periods[anchor_pos - 6]

    opening_balance = Decimal("100000.00")  # V0 -- the flat cash-basis carry
    inv = make_investment_account(
        seed_user, db.session, past_anchor_period, opening_balance,
    )

    return {
        "user_id": user.id,
        "account": inv,
        "account_id": inv.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "current_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "V0": opening_balance,
    }


@pytest.fixture()
def cross_page_secured_ctx(db, seed_user):
    """Property (PV) secured by a mortgage (MC) for the home-equity relationship.

    Builds the shared period grid, neutralises the seed_user Checking to
    $0, then creates ONE Property (market value PV = $400,000, anchored at
    the current period) and ONE mortgage trued up to current balance MC =
    $250,000 today, linked via ``mortgage.collateral_account_id =
    property.id`` so the property's ``secured_loans`` backref lists the
    mortgage.  The relationship under test: market value (PV) minus total
    secured debt (MC) is the equity, on the equity producer's own basis.

    **Since plan step X-g2b the /savings tile reads the MODELLED property value
    while that producer still reads the ``current_anchor_balance`` cache
    column** (finding N-83, scheduled for its own commit), so the consuming test
    asserts the two differ by exactly that gap rather than matching.  Returns
    PV, MC, and both account handles plus the standard period keys.
    """
    # Pylint: ``import-outside-toplevel`` -- Account / LoanParams are loaded
    # lazily, the Account-class convention this conftest follows (no model
    # packages imported at module top).
    # pylint: disable=import-outside-toplevel
    from app.models.account import Account
    from app.models.loan_params import LoanParams

    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    today = date.today()
    property_value = Decimal("400000.00")    # PV
    mortgage_balance = Decimal("250000.00")  # MC
    prop = make_appreciating_account(
        seed_user, db.session, anchor_period, property_value,
        Decimal("0.03000"),
    )
    mortgage = create_loan_account(
        seed_user, db.session, name="Cross-Page Mortgage",
        principal=Decimal("300000.00"), term=360,
        origination_date=date(today.year - 1, 1, 1),
    )
    params = db.session.query(LoanParams).filter_by(
        account_id=mortgage.id,
    ).one()
    insert_trueup_event(params, mortgage_balance, anchor_date=today)
    # Re-fetch the mortgage by primary key so setting the collateral FK
    # reliably marks the row dirty (the create_loan_account commit expired
    # it, the same refetch reason as _neutralize_seed_checking).
    mortgage = db.session.get(Account, mortgage.id)
    mortgage.collateral_account_id = prop.id
    db.session.commit()

    return {
        "user_id": user.id,
        "property_account": prop,
        "property_account_id": prop.id,
        "mortgage_account": mortgage,
        "mortgage_account_id": mortgage.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "PV": property_value,
        "MC": mortgage_balance,
    }


@pytest.fixture()
def second_user(app, db):
    """Create a second user for IDOR and cross-user isolation testing.

    Mirrors the shape of seed_user so the two can be used interchangeably.

    Returns:
        dict with keys: user, settings, account, scenario, categories.
    """
    user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    # Bootstrap pay period (E-19, Commit 3); see ``seed_user`` for
    # the rationale.
    bootstrap_period = PayPeriod(
        user_id=user.id,
        start_date=date(2024, 1, 5),
        end_date=date(2024, 1, 18),
        period_index=0,
    )
    db.session.add(bootstrap_period)
    db.session.flush()

    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=checking_type.id,
            name="Other Checking",
            anchor_balance=Decimal("500.00"),
            # Day one of its own period -- see ``seed_user`` above for why
            # the origination must not share a civil day with the settles.
            observed_on=bootstrap_period.start_date,
        ),
    )

    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    categories = []
    for group, item in [
        ("Income", "Salary"),
        ("Home", "Rent"),
    ]:
        cat = Category(
            user_id=user.id,
            group_name=group,
            item_name=item,
        )
        db.session.add(cat)
        categories.append(cat)
    db.session.flush()

    db.session.commit()

    return {
        "user": user,
        "settings": settings,
        "account": account,
        "scenario": scenario,
        "categories": {c.item_name: c for c in categories},
        "bootstrap_period": bootstrap_period,
    }


@pytest.fixture()
def seed_periods_52(app, db, seed_user):
    """Generate 52 pay periods (2-year projection) starting from 2026-01-02.

    Sets anchor to the first period.  Use for FIN tests that require
    production-scale data volumes.

    Returns:
        List of PayPeriod objects.
    """
    from app.services import pay_period_service  # pylint: disable=import-outside-toplevel

    periods = pay_period_write.record_paydays(
        user_id=seed_user["user"].id,
        first_payday=date(2026, 1, 2),
        num_periods=52,
        cadence_days=14,
    )
    db.session.flush()

    account = seed_user["account"]
    _drop_seed_user_bootstrap(db, seed_user, account, periods[0])
    return (
        db.session.query(PayPeriod)
        .filter_by(user_id=seed_user["user"].id)
        .order_by(PayPeriod.period_index)
        .all()
    )


# --- Two-User Isolation Fixtures ------------------------------------------


@pytest.fixture()
def seed_second_user(app, db):
    """Create an independent second user for multi-user isolation testing.

    Mirrors seed_user in structure but creates entirely separate objects
    with distinguishable names and amounts.

    Returns:
        dict with keys: user, settings, account, scenario, categories.
    """
    user = User(
        email="second@shekel.local",
        password_hash=hash_password("secondpass12"),
        display_name="Second User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    # Bootstrap pay period (E-19, Commit 3); see ``seed_user`` for
    # the rationale.
    bootstrap_period = PayPeriod(
        user_id=user.id,
        start_date=date(2024, 1, 5),
        end_date=date(2024, 1, 18),
        period_index=0,
    )
    db.session.add(bootstrap_period)
    db.session.flush()

    # Baseline scenario BEFORE the account, mirroring ``seed_user`` (and
    # production registration): ``create_account`` posts the opening anchor
    # correction into every scenario, so the second Checking carries its
    # $2000.00 opening from t0.
    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=checking_type.id,
            name="Checking",
            anchor_balance=Decimal("2000.00"),
            # Day one of its own period -- see ``seed_user`` above for why
            # the origination must not share a civil day with the settles.
            observed_on=bootstrap_period.start_date,
        ),
    )

    categories = []
    for group, item in [
        ("Income", "Salary"),
        ("Home", "Rent"),
        ("Auto", "Car Payment"),
        ("Family", "Groceries"),
        ("Credit Card", "Payback"),
    ]:
        cat = Category(
            user_id=user.id,
            group_name=group,
            item_name=item,
        )
        db.session.add(cat)
        categories.append(cat)
    db.session.flush()

    db.session.commit()

    return {
        "user": user,
        "settings": settings,
        "account": account,
        "scenario": scenario,
        "categories": {c.item_name: c for c in categories},
        "bootstrap_period": bootstrap_period,
    }


@pytest.fixture()
def seed_second_periods(app, db, seed_second_user):
    """Generate 10 pay periods for the second user starting 2026-01-02.

    Sets the anchor period to the first period.

    Returns:
        List of PayPeriod objects.
    """
    from app.services import pay_period_service  # pylint: disable=import-outside-toplevel

    periods = pay_period_write.record_paydays(
        user_id=seed_second_user["user"].id,
        first_payday=date(2026, 1, 2),
        num_periods=10,
        cadence_days=14,
    )
    db.session.flush()

    account = seed_second_user["account"]
    _drop_seed_user_bootstrap(db, seed_second_user, account, periods[0])
    return (
        db.session.query(PayPeriod)
        .filter_by(user_id=seed_second_user["user"].id)
        .order_by(PayPeriod.period_index)
        .all()
    )


@pytest.fixture()
def second_auth_client(app, db, seed_second_user):
    """Provide an authenticated test client for the second user.

    Creates a NEW test client instance to avoid session conflicts
    with the primary auth_client.
    """
    second_client = app.test_client()
    resp = second_client.post("/login", data={
        "email": "second@shekel.local",
        "password": "secondpass12",
    })
    assert resp.status_code == 302, (
        f"second_auth_client login failed with status {resp.status_code}"
    )
    return second_client


def _build_full_user_data(db, seed_user, periods):
    """Build the rich-dataset payload shared by seed_full_user_data variants.

    Extracted so both ``seed_full_user_data`` (calendar-anchored) and
    ``seed_full_user_data_today`` (today-relative) can share a single
    body and only differ in which ``periods`` fixture they consume.

    Args:
        db:        SQLAlchemy db extension (the test ``db`` fixture).
        seed_user: dict from the ``seed_user`` fixture.
        periods:   List of PayPeriod objects from a periods fixture.

    Returns:
        dict merging seed_user keys plus: periods, template, transaction,
        savings_goal, recurrence_rule, savings_account,
        transfer_template, salary_profile.
    """
    user = seed_user["user"]
    account = seed_user["account"]
    scenario = seed_user["scenario"]

    # Look up reference data.
    every_period = (
        db.session.query(RecurrencePattern)
        .filter_by(name="Every Period").one()
    )
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    projected_status = (
        db.session.query(Status).filter_by(name="Projected").one()
    )
    savings_acct_type = (
        db.session.query(AccountType).filter_by(name="Savings").one()
    )
    filing_single = (
        db.session.query(FilingStatus).filter_by(name="single").one()
    )

    # a) Recurrence rule + transaction template + transaction.
    rule = RecurrenceRule(
        user_id=user.id,
        pattern_id=every_period.id,
    )
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=user.id,
        account_id=account.id,
        category_id=seed_user["categories"]["Rent"].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type.id,
        name="Rent Payment",
        default_amount=Decimal("1200.00"),
    )
    db.session.add(template)
    db.session.flush()

    txn = Transaction(
        template_id=template.id,
        pay_period_id=periods[0].id,
        scenario_id=scenario.id,
        account_id=account.id,
        status_id=projected_status.id,
        name="Rent Payment",
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("1200.00"),
    )
    db.session.add(txn)

    # b) Savings goal.
    goal = SavingsGoal(
        user_id=user.id,
        account_id=account.id,
        name="Emergency Fund",
        target_amount=Decimal("10000.00"),
    )
    db.session.add(goal)

    # c) Savings account + transfer template via the canonical
    # factory (E-19, Commit 3).
    savings_account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=savings_acct_type.id,
            name="Savings",
            anchor_balance=Decimal("500.00"),
        ),
    )

    transfer_tpl = TransferTemplate(
        user_id=user.id,
        from_account_id=account.id,
        to_account_id=savings_account.id,
        name="Monthly Savings",
        default_amount=Decimal("200.00"),
    )
    db.session.add(transfer_tpl)

    # d) Salary profile.
    salary_profile = SalaryProfile(
        user_id=user.id,
        scenario_id=scenario.id,
        filing_status_id=filing_single.id,
        name="Day Job",
        annual_salary=Decimal("75000.00"),
        state_code="NC",
    )
    db.session.add(salary_profile)

    db.session.commit()

    return {
        **seed_user,
        "periods": periods,
        "template": template,
        "transaction": txn,
        "savings_goal": goal,
        "recurrence_rule": rule,
        "savings_account": savings_account,
        "transfer_template": transfer_tpl,
        "salary_profile": salary_profile,
    }


@pytest.fixture()
def seed_full_user_data(app, db, seed_user, seed_periods):
    """Create a rich dataset for User A (the primary test user).

    Includes transaction template, transaction, savings goal, savings
    account, transfer template, and salary profile. All objects have
    distinguishable names and amounts for use in isolation testing.

    Uses the calendar-anchored ``seed_periods`` fixture, so transactions
    fall in calendar 2026.  Use ``seed_full_user_data_today`` instead
    when the test exercises a route that calls ``get_current_period``.

    Returns:
        dict merging seed_user keys plus: periods, template, transaction,
        savings_goal, recurrence_rule, savings_account,
        transfer_template, salary_profile.
    """
    return _build_full_user_data(db, seed_user, seed_periods)


@pytest.fixture()
def seed_full_user_data_today(app, db, seed_user, seed_periods_today):
    """Today-relative variant of seed_full_user_data.

    Identical payload to ``seed_full_user_data`` except the periods
    are anchored so today falls in period 4.  Use when the test
    exercises a route that internally calls
    ``pay_period_service.get_current_period`` (e.g. /dashboard).

    Returns:
        dict merging seed_user keys plus: periods, template, transaction,
        savings_goal, recurrence_rule, savings_account,
        transfer_template, salary_profile.
    """
    return _build_full_user_data(db, seed_user, seed_periods_today)


@pytest.fixture()
def seed_full_second_user_data(app, db, seed_second_user, seed_second_periods):
    """Create a rich dataset for User B (the second test user).

    Mirrors seed_full_user_data but with distinguishable names and
    amounts so isolation tests can verify data separation.

    Returns:
        dict merging seed_second_user keys plus: periods, template,
        transaction, savings_goal, recurrence_rule, savings_account,
        transfer_template, salary_profile.
    """
    user = seed_second_user["user"]
    account = seed_second_user["account"]
    scenario = seed_second_user["scenario"]
    periods = seed_second_periods

    # Look up reference data.
    every_period = (
        db.session.query(RecurrencePattern)
        .filter_by(name="Every Period").one()
    )
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    projected_status = (
        db.session.query(Status).filter_by(name="Projected").one()
    )
    savings_acct_type = (
        db.session.query(AccountType).filter_by(name="Savings").one()
    )
    filing_single = (
        db.session.query(FilingStatus).filter_by(name="single").one()
    )

    # a) Recurrence rule + transaction template + transaction.
    rule = RecurrenceRule(
        user_id=user.id,
        pattern_id=every_period.id,
    )
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=user.id,
        account_id=account.id,
        category_id=seed_second_user["categories"]["Rent"].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type.id,
        name="Second User Rent",
        default_amount=Decimal("900.00"),
    )
    db.session.add(template)
    db.session.flush()

    txn = Transaction(
        template_id=template.id,
        pay_period_id=periods[0].id,
        scenario_id=scenario.id,
        account_id=account.id,
        status_id=projected_status.id,
        name="Second User Rent",
        category_id=seed_second_user["categories"]["Rent"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("900.00"),
    )
    db.session.add(txn)

    # b) Savings goal.
    goal = SavingsGoal(
        user_id=user.id,
        account_id=account.id,
        name="Vacation Fund",
        target_amount=Decimal("5000.00"),
    )
    db.session.add(goal)

    # c) Savings account + transfer template via the canonical
    # factory (E-19, Commit 3).
    savings_account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=savings_acct_type.id,
            name="Savings",
            anchor_balance=Decimal("300.00"),
        ),
    )

    transfer_tpl = TransferTemplate(
        user_id=user.id,
        from_account_id=account.id,
        to_account_id=savings_account.id,
        name="Bi-Weekly Savings",
        default_amount=Decimal("150.00"),
    )
    db.session.add(transfer_tpl)

    # d) Salary profile.
    salary_profile = SalaryProfile(
        user_id=user.id,
        scenario_id=scenario.id,
        filing_status_id=filing_single.id,
        name="Second Job",
        annual_salary=Decimal("60000.00"),
        state_code="NC",
    )
    db.session.add(salary_profile)

    db.session.commit()

    return {
        **seed_second_user,
        "periods": periods,
        "template": template,
        "transaction": txn,
        "savings_goal": goal,
        "recurrence_rule": rule,
        "savings_account": savings_account,
        "transfer_template": transfer_tpl,
        "salary_profile": salary_profile,
    }


# --- Entry and Companion Fixtures -----------------------------------------


@pytest.fixture()
def seed_entry_template(app, db, seed_user, seed_periods):
    """Create a template with is_envelope=True and a transaction.

    The template is an expense-type template tied to the seed_user's checking
    account with a default amount of $500.  A single projected transaction is
    created in the first pay period.

    Returns:
        dict with keys: template, transaction, category, recurrence_rule.
    """
    every_period = (
        db.session.query(RecurrencePattern)
        .filter_by(name="Every Period").one()
    )
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    projected_status = (
        db.session.query(Status).filter_by(name="Projected").one()
    )

    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=every_period.id,
    )
    db.session.add(rule)
    db.session.flush()

    category = seed_user["categories"]["Groceries"]

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=category.id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type.id,
        name="Weekly Groceries",
        default_amount=Decimal("500.00"),
        is_envelope=True,
    )
    db.session.add(template)
    db.session.flush()

    txn = Transaction(
        template_id=template.id,
        pay_period_id=seed_periods[0].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected_status.id,
        name="Weekly Groceries",
        category_id=category.id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("500.00"),
    )
    db.session.add(txn)
    db.session.commit()

    return {
        "template": template,
        "transaction": txn,
        "category": category,
        "recurrence_rule": rule,
    }


@pytest.fixture()
def seed_companion(app, db, seed_user):
    """Create a companion user linked to the seed_user owner.

    The companion has role_id set to the companion role and
    linked_owner_id pointing to the primary seed_user.

    Returns:
        dict with keys: user, settings.
    """
    from app import ref_cache  # pylint: disable=import-outside-toplevel
    from app.enums import RoleEnum  # pylint: disable=import-outside-toplevel

    companion = User(
        email="companion@shekel.local",
        password_hash=hash_password("companionpass"),
        display_name="Companion User",
        role_id=ref_cache.role_id(RoleEnum.COMPANION),
        linked_owner_id=seed_user["user"].id,
    )
    db.session.add(companion)
    db.session.flush()

    settings = UserSettings(user_id=companion.id)
    db.session.add(settings)
    db.session.commit()

    return {
        "user": companion,
        "settings": settings,
    }


@pytest.fixture()
def companion_client(app, db, seed_companion):
    """Provide an authenticated test client for the companion user.

    Creates a new test client instance and logs in as the companion
    user, following the same pattern as second_auth_client.
    """
    comp_client = app.test_client()
    resp = comp_client.post("/login", data={
        "email": "companion@shekel.local",
        "password": "companionpass",
    })
    assert resp.status_code == 302, (
        f"companion_client login failed with status {resp.status_code}"
    )
    return comp_client


# --- Helpers --------------------------------------------------------------


def _refresh_ref_cache_and_jinja_globals(app):
    """Re-init ``ref_cache`` and rewrite all ID-derived Jinja globals.

    Called from two places:

      1. ``setup_database`` at session start, once the ref tables
         have been seeded for the first time.
      2. The ``db`` fixture, after the per-test TRUNCATE has wiped
         ``ref.account_types`` (via the new C-28 / F-044 FK to
         ``auth.users``) and the seed has been re-run.  The new
         seed assigns fresh IDs from the sequence; the
         pre-existing Jinja globals would otherwise point at IDs
         that no longer exist and every template that references
         one would break.

    Delegates to ``app.jinja_globals.register_ref_id_globals`` --
    the same helper ``create_app`` uses -- so the two call sites
    cannot drift out of sync.  Follow-up plan Commit 6 (F-7)
    extracted the helper; prior to that the conftest list was
    missing eight entries (timing / calc-method / goal-mode /
    income-unit IDs) and any template referencing one would raise
    ``UndefinedError`` at test time.
    """
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.jinja_globals import register_ref_id_globals

    ref_cache.init(_db.session)
    register_ref_id_globals(app)


def _profile_step_stats(values):
    """Compute summary statistics for a list of float milliseconds.

    Returns dict with keys ``avg``, ``p50``, ``p95``, ``p99``,
    ``max``.  Uses :func:`statistics.quantiles` with ``n=100`` (the
    "inclusive" method, which linearly interpolates between sample
    values) for percentiles; exact at sample sizes we expect (one
    row per test, dozens to thousands).

    Special cases:

    * Empty list -- returns all zeros so the aggregator can render a
      well-formed row even if a step contributed no samples.
    * Single sample -- ``statistics.quantiles`` rejects ``n < 2``,
      so we short-circuit to the single value for every percentile.
    """
    if not values:
        return {"avg": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    if len(values) == 1:
        single = values[0]
        return {
            "avg": single,
            "p50": single,
            "p95": single,
            "p99": single,
            "max": single,
        }

    cuts = statistics.quantiles(values, n=100, method="inclusive")
    return {
        "avg": sum(values) / len(values),
        # quantiles(n=100) returns 99 cut points; index 49 is p50,
        # index 94 is p95, index 98 is p99.
        "p50": cuts[49],
        "p95": cuts[94],
        "p99": cuts[98],
        "max": max(values),
    }


def _profile_load_rows():
    """Read every per-worker CSV in the profile dir into memory.

    Returns ``(rows, workers)`` where ``rows`` is a list of dicts
    keyed by step name with float-millisecond values, and ``workers``
    is the sorted set of worker ids that contributed.  Skips silently
    when the directory is missing (no harness output yet) so the
    aggregator can short-circuit on the empty case.
    """
    rows = []
    workers = set()
    if not _FIXTURE_PROFILE_DIR.exists():
        return rows, workers
    for csv_path in sorted(_FIXTURE_PROFILE_DIR.glob("*.csv")):
        with csv_path.open("r", newline="", encoding="utf-8") as csv_fp:
            reader = csv.DictReader(csv_fp)
            for raw in reader:
                workers.add(raw["worker_id"])
                rows.append(
                    {step: float(raw[step]) for step in _FIXTURE_PROFILE_STEPS}
                )
    return rows, sorted(workers)


def _profile_print_summary():
    """Print the per-step summary table to stdout.

    Worker-aware: aggregates across every CSV in
    ``tests/.fixture-profile/``.  Called only on the xdist controller
    or the single-process pytest run -- the two scenarios where
    ``PYTEST_XDIST_WORKER`` is unset and the process has visibility
    into every other worker's output.

    Output shape mirrors ``test-performance-research.md`` section
    3.1 (the "Per-test phase breakdown" table): one row per fixture
    inner step, then a ``Fixture setup total`` line summarising the
    sum-of-setup-steps, then ``call`` and ``teardown`` as informational
    rows (no percent column because they are not part of fixture
    setup cost).  The ``% of fixture`` column is computed relative
    to the average fixture setup total so the percentages sum to
    100 within rounding.
    """
    rows, workers = _profile_load_rows()
    if not rows:
        print()
        print("Fixture profile summary: no rows captured "
              f"(check {_FIXTURE_PROFILE_DIR})")
        print()
        return

    setup_steps = [s for s in _FIXTURE_PROFILE_STEPS if s.startswith("setup_")]
    setup_totals = [sum(row[s] for s in setup_steps) for row in rows]
    setup_avg_total = sum(setup_totals) / len(setup_totals)

    header = ["Step", "Avg", "p50", "p95", "p99", "Max", "% of fixture"]
    widths = [34, 10, 10, 10, 10, 10, 14]
    fmt = " | ".join(f"{{:<{w}}}" for w in widths)
    fmt = "| " + fmt + " |"
    sep = "|-" + "-|-".join("-" * w for w in widths) + "-|"

    print()
    print("=" * 100)
    print(f"  Fixture profile summary -- {len(rows)} tests across "
          f"{len(workers)} worker(s): {', '.join(workers)}")
    print("=" * 100)
    print(fmt.format(*header))
    print(sep)

    for step in setup_steps:
        stats = _profile_step_stats([row[step] for row in rows])
        pct = (stats["avg"] / setup_avg_total * 100.0) if setup_avg_total else 0.0
        print(fmt.format(
            _FIXTURE_PROFILE_LABELS[step],
            f"{stats['avg']:.1f} ms",
            f"{stats['p50']:.1f}",
            f"{stats['p95']:.1f}",
            f"{stats['p99']:.1f}",
            f"{stats['max']:.1f}",
            f"{pct:.1f} %",
        ))

    setup_stats = _profile_step_stats(setup_totals)
    print(fmt.format(
        "Fixture setup total",
        f"{setup_stats['avg']:.1f} ms",
        f"{setup_stats['p50']:.1f}",
        f"{setup_stats['p95']:.1f}",
        f"{setup_stats['p99']:.1f}",
        f"{setup_stats['max']:.1f}",
        "100.0 %",
    ))
    print(sep)

    for step in ("call", "teardown"):
        stats = _profile_step_stats([row[step] for row in rows])
        print(fmt.format(
            _FIXTURE_PROFILE_LABELS[step],
            f"{stats['avg']:.1f} ms",
            f"{stats['p50']:.1f}",
            f"{stats['p95']:.1f}",
            f"{stats['p99']:.1f}",
            f"{stats['max']:.1f}",
            "--",
        ))
    print()


def pytest_sessionfinish(session, exitstatus):  # pylint: disable=unused-argument
    """Drop the per-pytest-worker database AND emit the profile summary.

    Pytest invokes this hook at the end of every session -- including
    failed sessions -- so the per-session DB is cleaned up regardless
    of pass/fail.  No-op when the xdist master process skipped the
    bootstrap (``_BOOTSTRAP_RESULT`` is ``None``); only worker
    processes own a DB to drop.

    Why psycopg2 directly (not SQLAlchemy):
        Flask-SQLAlchemy 3.x scopes ``db.session`` and ``db.engine``
        to the current app context, and the
        ``pytest_sessionfinish`` hook runs AFTER the session-scoped
        ``app`` fixture has torn down -- there is no active app
        context to bind to.  Wrapping the cleanup in a fresh app
        context would require either keeping the session-scoped app
        alive via module-level state or building a new app, both
        of which add complexity for the same end state.  The per-
        test ``db`` fixture already calls ``_db.session.remove`` and
        ``_db.engine.dispose`` inside its app context after every
        test, so by the time this hook runs there are no live
        SQLAlchemy connections to release -- and the
        ``WITH (FORCE)`` clause severs any backend that did
        escape, at the protocol level.  See
        ``docs/audits/security-2026-04-15/per-worker-database-plan.md``
        Phase 3 for the broader context.

    Survives SIGKILL imperfectly: a process killed before this hook
    runs leaves an orphan DB.  The next session's bootstrap drops it
    via the ``shekel_test_{worker_id}_*`` cleanup pass (see
    :func:`_bootstrap_worker_database`), so the orphan is at worst
    a temporary disk-space cost between runs.

    Profile aggregation:
        When ``SHEKEL_TEST_FIXTURE_PROFILE`` is set, the harness
        printed by :func:`_profile_print_summary` reads every per-
        worker CSV under ``tests/.fixture-profile/`` and writes a
        single summary table to stdout.  Only the xdist controller
        (or, in single-process runs, the test process itself)
        prints; workers are write-only.  The aggregator runs AFTER
        the DB drop so a flaky summary path cannot leave per-
        session databases behind.

    Args:
        session (pytest.Session): pytest Session object (required
            by the hook signature; unused here -- the cleanup keys
            off the module-level ``_BOOTSTRAP_RESULT`` instead).
        exitstatus (int): Session exit code.  Unused: we drop the
            per-session DB regardless of pass / fail because it is
            throwaway.
    """
    if _BOOTSTRAP_RESULT is not None:
        db_name, admin_url = _BOOTSTRAP_RESULT
        admin_conn = psycopg2.connect(admin_url)
        try:
            admin_conn.autocommit = True
            with admin_conn.cursor() as cur:
                cur.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(db_name)
                    )
                )
        finally:
            admin_conn.close()

    # Only the xdist controller / single-process run aggregates and
    # prints.  Workers (PYTEST_XDIST_WORKER set) are write-only: they
    # already appended their per-test rows to their own CSV during
    # the run, and the controller's pytest_sessionfinish fires after
    # every worker has exited, so by the time we read here every
    # row is on disk.
    if _FIXTURE_PROFILE_ENABLED and not os.environ.get("PYTEST_XDIST_WORKER"):
        _profile_print_summary()
