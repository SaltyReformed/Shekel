"""rebuild audit infrastructure idempotently

Closes audit finding F-028 (Top Risk #1 in the 2026-04-15 report):
migration ``a8b1c2d3e4f5_add_audit_log_and_triggers.py`` declared the
audit log table, ``system.audit_trigger_func``, and 22 row-level
triggers, but the live database carried zero audit triggers because
``scripts/init_database.py`` initialises a fresh database with
``db.create_all()`` followed by an Alembic ``stamp`` to head -- a path
that does not execute the migration.  The forensic trail on a
financial app cannot live only in the application's own stdout
(stdout is rewriteable by the app process; an attacker with RCE
controls the only record of the breach).

Two concerns ride along in this commit because they share the
"never run on the production database" failure mode:

  * Finding F-070 -- ``CREATE SCHEMA IF NOT EXISTS system`` was
    missing from the original migration.  A fresh-DB ``flask db
    upgrade`` against a database that lacked the schema would fail
    with ``relation "system.audit_log" does not exist``.
    ``scripts/init_db.sql`` already creates the schema for
    docker-compose deploys, but the migration must be self-sufficient
    for any developer running ``flask db upgrade`` directly against
    a brand-new test database.

  * Finding F-081 -- the original migration carried no GRANT for the
    least-privilege ``shekel_app`` role that this commit also
    provisions in ``scripts/init_db.sql``.  Without the GRANT, the
    app role's INSERTs into audited tables would fail at trigger
    execution time with ``permission denied for schema system``.

Idempotency strategy:

  * ``CREATE SCHEMA IF NOT EXISTS`` -- safe to re-run.
  * ``CREATE TABLE IF NOT EXISTS`` -- leaves an existing table
    untouched.  Future column changes need a separate migration that
    explicitly ALTERs the table.
  * ``CREATE OR REPLACE FUNCTION`` -- atomically swaps the function
    body if it already exists.
  * Triggers: PostgreSQL has no ``CREATE TRIGGER IF NOT EXISTS``, so
    every CREATE is preceded by ``DROP TRIGGER IF EXISTS``.  This is
    safe even on a database that has never carried the trigger.
  * GRANTs are wrapped in a ``DO $$ ... END $$`` block that checks
    ``pg_roles`` for ``shekel_app`` first, so the migration succeeds
    on a developer laptop where the production app role has not been
    provisioned.

Idempotency is verified by
``tests/test_models/test_audit_migration.py`` (upgrade -> downgrade
-> upgrade round-trip) and by the entrypoint health check
(``EXPECTED_AUDIT_TRIGGER_COUNT`` enforcement).

The canonical audited-table list and the trigger-attachment SQL live
in ``app/audit_infrastructure.py``.  ``scripts/init_database.py`` and
``tests/conftest.py`` import the same module so the migration, the
fresh-DB code path, and the test session setup all produce the same
end state.

Revision ID: a5be2a99ea14
Revises: d2883fc44071
Create Date: 2026-05-05
"""
from alembic import op

from app.audit_infrastructure import (
    apply_audit_infrastructure,
    remove_audit_infrastructure,
)


# Revision identifiers, used by Alembic.
revision = "a5be2a99ea14"
down_revision = "d2883fc44071"
branch_labels = None
depends_on = None


def upgrade():
    """Idempotently materialise ``system.audit_log`` + trigger function + triggers.

    Delegates to :func:`app.audit_infrastructure.apply_audit_infrastructure`
    so the migration, the fresh-DB initialisation path in
    ``scripts/init_database.py``, and the test-session setup in
    ``tests/conftest.py`` all run the same SQL.
    """
    apply_audit_infrastructure(op.execute)


def downgrade():
    """Drop every audit trigger, the trigger function, and ``system.audit_log``.

    Inverse of :func:`upgrade`.  The ``system`` schema itself is left
    in place: ``scripts/init_db.sql`` creates it idempotently on every
    container start, and dropping it here would force a re-upgrade to
    re-create a schema the operator never asked to touch.

    Delegates to :func:`app.audit_infrastructure.remove_audit_infrastructure`
    for the same shared-source-of-truth reason as :func:`upgrade`.
    """
    remove_audit_infrastructure(op.execute)
