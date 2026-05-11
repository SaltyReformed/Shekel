"""
Shekel Budget App -- Test Template Database Builder

Builds the per-pytest-worker template database (``shekel_test_template``
by default).  The script drops and rebuilds the database on every run
so two invocations produce a byte-identical end state; this is the
required property for the Phase 2 work in
``docs/audits/security-2026-04-15/per-worker-database-plan.md``.

The template is the source for PostgreSQL ``CREATE DATABASE ...
TEMPLATE shekel_test_template`` clones made at the start of each
pytest session (and each pytest-xdist worker within a session).
Cloning a populated template is roughly two orders of magnitude
faster than re-running migrations + audit infrastructure + seed for
every session, which is what unlocks parallel pytest invocations
without the per-test ``TRUNCATE`` deadlock that the current shared
``shekel_test`` database suffers from.

Three steps, in order:

1. **Recreate** the template database.  Uses an admin DSN
   (``TEST_ADMIN_DATABASE_URL``, default ``postgresql:///postgres``)
   in autocommit mode because ``CREATE DATABASE`` cannot run inside
   a transaction.  ``DROP DATABASE ... WITH (FORCE)`` severs any
   lingering connections so a previously-orphaned clone or a leftover
   pytest worker connection does not block the rebuild.  PostgreSQL
   13+ required for ``WITH (FORCE)``.
2. **Populate** the template.  Creates the five user-facing schemas,
   runs the Alembic chain to ``head`` via ``alembic.command.upgrade``
   (matches the production ``flask db upgrade head`` path so any
   model-vs-migration drift surfaces on the next template rebuild),
   applies the audit infrastructure idempotently (so the LATEST
   in-code trigger definitions win over any migration-frozen state),
   and seeds reference data via :func:`app.ref_seeds.seed_reference_data`.
   Finally truncates ``system.audit_log`` so the 18 audit rows the
   seed fires on ``ref.account_types`` (which is in
   :data:`app.audit_infrastructure.AUDITED_TABLES` after commit C-28)
   do not leak into per-session clones.  The TRUNCATE mirrors the
   per-test cleanup in ``tests/conftest.py::db`` and gives the
   template a zeroed log -- per-test assertions on audit_log row
   count are then trivially true at clone time.
3. **Verify** the populated template carries the expected state:
   ``ref.account_types`` row count equals 18 (the seed list size),
   ``pg_trigger`` count of ``audit_%`` triggers equals
   :data:`app.audit_infrastructure.EXPECTED_TRIGGER_COUNT`
   (i.e. ``len(AUDITED_TABLES)``), and ``system.audit_log`` is
   empty.  Any mismatch raises :class:`RuntimeError` with a recovery
   hint; the script does not attempt to repair partial state.

Environment variables (read at module load before app import):

* ``TEST_ADMIN_DATABASE_URL`` (default ``postgresql:///postgres``):
  DSN with permission to ``CREATE DATABASE`` / ``DROP DATABASE``.
  Must point at a database OTHER THAN the template -- DROP DATABASE
  cannot run against the connection's own database.
* ``TEST_TEMPLATE_DATABASE`` (default ``shekel_test_template``):
  Name of the template database.  Used for the DROP/CREATE and as
  the path of the application's ``TEST_DATABASE_URL`` for the
  migration + seed phase.
* ``SECRET_KEY``: Used by ``create_app('testing')``.  Defaults to a
  non-production sentinel value if unset; the template database is
  never reachable through Gunicorn so the value is purely a
  placeholder needed for app construction.
* ``DATABASE_URL_APP``: Removed from the environment if set --
  the script needs DDL privileges (the owner role, ``DATABASE_URL``),
  not the least-privilege app role.  Matches
  ``scripts/init_database.py``.

Usage::

    python scripts/build_test_template.py

    # With an explicit admin DSN (TCP + password) ----
    TEST_ADMIN_DATABASE_URL=postgresql://shekel_user:shekel_pass@localhost:5433/postgres \\
        python scripts/build_test_template.py
"""

import os
import sys
from urllib.parse import urlparse, urlunparse


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_DEFAULT_TEMPLATE_DATABASE: str = "shekel_test_template"
_DEFAULT_ADMIN_URL: str = "postgresql:///postgres"
# Non-production placeholder.  The template database is never reachable
# through Gunicorn, so the value is purely scaffolding to satisfy the
# Flask config's ``_reject_sentinel`` and 32-character minimum.  Set
# only if the caller did not pre-populate SECRET_KEY (an operator who
# already exports a value should keep theirs).
_DEFAULT_SECRET_KEY: str = "build-test-template-key-32-characters-long-not-prod"
_REQUIRED_SCHEMAS: tuple[str, ...] = ("ref", "auth", "budget", "salary", "system")
# Sourced from ``app.ref_seeds.ACCT_TYPE_SEEDS`` -- the seed currently
# defines 18 account types.  The verification step asserts the count
# round-trips through the seed call so an accidental edit to the
# seed list (e.g. a deleted row) surfaces here, not in test failures.
_EXPECTED_ACCOUNT_TYPE_COUNT: int = 18


# ---------------------------------------------------------------------------
# Resolve configuration from environment and prepare the import path.
#
# These statements MUST run before ``from app import create_app`` below:
# ``app.config.TestConfig.SQLALCHEMY_DATABASE_URI`` reads
# ``TEST_DATABASE_URL`` at class-body evaluation time, which happens
# during the first ``app`` import.  Setting the variable after that
# import would leave the app pointed at whatever DSN was in the
# environment when the module was first loaded -- which is never the
# template DB this script just created.
# ---------------------------------------------------------------------------
ADMIN_URL: str = os.environ.get("TEST_ADMIN_DATABASE_URL", _DEFAULT_ADMIN_URL)
TEMPLATE_DB: str = os.environ.get("TEST_TEMPLATE_DATABASE", _DEFAULT_TEMPLATE_DATABASE)

# Build the template DSN by replacing the database name (the URL's
# path component) in the admin DSN.  Preserves scheme, host, port,
# user, password, query, and fragment so an admin DSN like
# ``postgresql://shekel_user:shekel_pass@localhost:5433/postgres`` becomes
# ``postgresql://shekel_user:shekel_pass@localhost:5433/shekel_test_template``.
_parsed_admin = urlparse(ADMIN_URL)
TEMPLATE_URL: str = urlunparse(_parsed_admin._replace(path=f"/{TEMPLATE_DB}"))

os.environ["TEST_DATABASE_URL"] = TEMPLATE_URL
os.environ.setdefault("SECRET_KEY", _DEFAULT_SECRET_KEY)
# Force the owner role -- the script needs DDL privileges (CREATE
# TABLE, CREATE TRIGGER, ...).  ``app/config.py`` prefers
# ``DATABASE_URL_APP`` over ``DATABASE_URL`` when both are set, which
# is correct for the runtime app (least privilege) but wrong here.
# ``pop`` is process-local; the parent shell's env is untouched.
os.environ.pop("DATABASE_URL_APP", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# sys.path manipulation above must precede the local imports below.
# pylint: disable=wrong-import-position
import psycopg2
from psycopg2 import sql

from alembic import command
from alembic.config import Config

from app import create_app
from app.audit_infrastructure import EXPECTED_TRIGGER_COUNT, apply_audit_infrastructure
from app.extensions import db
from app.ref_seeds import seed_reference_data


def _recreate_template_database() -> None:
    """Drop the template database (if present) and create a fresh empty one.

    Uses an admin connection in autocommit mode.  ``DROP DATABASE
    ... WITH (FORCE)`` (PostgreSQL 13+) severs any lingering
    connections so a previously-orphaned clone or a stuck pytest
    worker connection does not block the rebuild.  Identifier
    interpolation goes through :mod:`psycopg2.sql` to defend against
    a future change that sources the template name from user input.
    """
    conn = psycopg2.connect(ADMIN_URL)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(TEMPLATE_DB)
                )
            )
            cur.execute(
                sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(TEMPLATE_DB)
                )
            )
    finally:
        conn.close()


def _populate_template(app) -> None:
    """Materialise the schema, run migrations, apply audit infra, seed.

    Steps, in order:

    1. ``CREATE SCHEMA IF NOT EXISTS`` for each entry in
       :data:`_REQUIRED_SCHEMAS`.  Migrations expect the four
       user-facing schemas to exist; the rebuild migration creates
       the ``system`` schema conditionally but the others are assumed.
    2. ``alembic.command.upgrade(..., 'head')``: same migration
       runner ``scripts/init_database.py::migrate_existing_database``
       uses.  Running against an empty database validates the
       chain end-to-end on every template rebuild -- any model-
       vs-migration drift surfaces here, not at test time.
    3. ``apply_audit_infrastructure``: idempotent re-application so
       the latest in-code trigger definitions win over any
       migration-frozen state.  Pulls in any trigger that was added
       to :data:`AUDITED_TABLES` after the rebuild migration was
       authored.
    4. ``seed_reference_data``: populates ``ref.account_types`` (18
       rows) and the other ref tables.  The INSERTs on
       ``ref.account_types`` fire the audit trigger attached in
       step 2/3 and write 18 rows into ``system.audit_log``.
    5. ``TRUNCATE system.audit_log``: clear those 18 seed-time
       audit rows so the template ships with a zeroed log.  Mirrors
       the per-test pattern in ``tests/conftest.py::db`` (line 244)
       and gives the per-session clones a clean slate.

    Args:
        app: Flask application built by ``create_app('testing')``.
            Must already be pointed at the template DSN via
            ``TEST_DATABASE_URL`` (set at module load above).
    """
    with app.app_context():
        for schema_name in _REQUIRED_SCHEMAS:
            db.session.execute(
                db.text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")
            )
        db.session.commit()

        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("script_location", "migrations")
        command.upgrade(alembic_cfg, "head")

        apply_audit_infrastructure(
            lambda statement: db.session.execute(db.text(statement))
        )
        db.session.commit()

        seed_reference_data(db.session)
        db.session.commit()

        # Clear the 18 seed-time audit rows so the template ships
        # with a zeroed log.  Same ordering as
        # ``tests/conftest.py::db`` lines 244-245: TRUNCATE after the
        # reseed commits, then commit the truncate separately.
        db.session.execute(db.text("TRUNCATE system.audit_log"))
        db.session.commit()


def _verify_template_state() -> None:
    """Assert the template carries the expected row and trigger counts.

    Three assertions, each with a specific recovery hint:

    * ``ref.account_types`` row count equals
      :data:`_EXPECTED_ACCOUNT_TYPE_COUNT`.  Catches a seed list
      edit that removed or duplicated a row.
    * ``pg_trigger`` count of non-internal ``audit_*`` triggers
      equals :data:`EXPECTED_TRIGGER_COUNT` from
      :mod:`app.audit_infrastructure`.  Catches a new table that was
      added to ``AUDITED_TABLES`` but whose trigger never attached
      (or, less likely, a stray trigger left over from a previous
      template that the DROP did not wipe).
    * ``system.audit_log`` row count equals 0.  Catches a missing
      TRUNCATE -- the template must ship with a clean log so per-
      session clones start from a known zero.

    Raises:
        RuntimeError: When any assertion fails.  The message names
            the offending count, the expected value, and a recovery
            hint pointing at the most likely root cause.
    """
    conn = psycopg2.connect(TEMPLATE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ref.account_types")
            account_type_count = cur.fetchone()[0]
            if account_type_count != _EXPECTED_ACCOUNT_TYPE_COUNT:
                raise RuntimeError(
                    f"Template ref.account_types count is "
                    f"{account_type_count}, expected "
                    f"{_EXPECTED_ACCOUNT_TYPE_COUNT}.  Check that "
                    "app.ref_seeds.ACCT_TYPE_SEEDS still has 18 entries "
                    "and that seed_reference_data committed cleanly."
                )

            cur.execute(
                "SELECT count(*) FROM pg_trigger "
                "WHERE tgname LIKE 'audit_%' AND NOT tgisinternal"
            )
            trigger_count = cur.fetchone()[0]
            if trigger_count != EXPECTED_TRIGGER_COUNT:
                raise RuntimeError(
                    f"Template audit trigger count is {trigger_count}, "
                    f"expected {EXPECTED_TRIGGER_COUNT} (from "
                    "app.audit_infrastructure.AUDITED_TABLES).  A new "
                    "AUDITED_TABLES entry without a matching table, "
                    "or a stale trigger left behind, would cause this."
                )

            cur.execute("SELECT count(*) FROM system.audit_log")
            audit_log_count = cur.fetchone()[0]
            if audit_log_count != 0:
                raise RuntimeError(
                    f"Template system.audit_log count is "
                    f"{audit_log_count}, expected 0.  The post-seed "
                    "TRUNCATE in _populate_template did not commit, or "
                    "an unaudited write fired a trigger after the "
                    "truncate."
                )
    finally:
        conn.close()


def main() -> int:
    """Drop, rebuild, populate, and verify the template database.

    Prints progress for each of the three phases plus a final
    summary line.  Exits 0 on success; failures propagate as
    exceptions (``psycopg2.OperationalError`` on connect issues,
    Alembic errors on migration failure, :class:`RuntimeError` on
    verification failure).
    """
    print(f"Building test template database: {TEMPLATE_DB}")
    print(f"  Admin DSN:    {ADMIN_URL}")
    print(f"  Template URL: {TEMPLATE_URL}")

    _recreate_template_database()
    print("  Step 1/3: dropped and recreated empty database.")

    app = create_app("testing")
    _populate_template(app)
    print("  Step 2/3: migrated to head, applied audit, seeded reference data.")

    _verify_template_state()
    print(
        f"  Step 3/3: verified "
        f"({_EXPECTED_ACCOUNT_TYPE_COUNT} account types, "
        f"{EXPECTED_TRIGGER_COUNT} audit triggers, "
        f"0 audit_log rows)."
    )

    print(f"DONE: {TEMPLATE_DB} ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
