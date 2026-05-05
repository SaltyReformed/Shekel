"""
Shekel Budget App -- Database Initialization

Detects fresh vs. existing databases and initializes accordingly:

- Fresh DB: creates all tables via SQLAlchemy metadata, materialises
  the ``system.audit_log`` infrastructure (table, trigger function,
  and per-table audit triggers), then stamps Alembic to mark every
  migration as applied.  The audit-infrastructure step is the
  difference vs. ``db.create_all()`` alone -- the audit triggers,
  function, and table are raw SQL outside SQLAlchemy's model registry,
  so a bare ``create_all`` would skip them and the entrypoint health
  check would refuse to start Gunicorn.  See audit finding F-028 and
  remediation Commit C-13.

- Existing DB: runs incremental Alembic migrations.  An existing DB
  that pre-dates Commit C-13 picks up the rebuild migration on the
  next ``flask db upgrade`` and the GRANT block inside the migration
  applies once the ``shekel_app`` role has been provisioned by
  ``scripts/init_db.sql``.

Database role policy:

    This script is part of the deployment pipeline -- not the
    application's request-time path -- so it always runs as the
    owner role (``DATABASE_URL``), never as the least-privilege app
    role (``DATABASE_URL_APP``).  ``DATABASE_URL_APP`` is removed
    from ``os.environ`` at the top of the file before
    ``create_app()`` reads it; this scopes the override to this
    process only and does not affect the Gunicorn process that
    ``entrypoint.sh`` exec's afterwards.

Usage:
    python scripts/init_database.py
"""

import os
import sys

# Force the owner role for this script.  ``app/config.py`` prefers
# ``DATABASE_URL_APP`` over ``DATABASE_URL`` when both are set, which
# is correct for the runtime app (least privilege) but wrong for
# this script (needs DDL: CREATE TABLE, CREATE TRIGGER, ...).
# os.environ.pop is process-local -- the parent shell's env is
# untouched, so ``exec gunicorn`` after this script still sees
# DATABASE_URL_APP and runs as the app role.
os.environ.pop("DATABASE_URL_APP", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# sys.path manipulation above must precede the local imports below.
# pylint: disable=wrong-import-position
from alembic import command
from alembic.config import Config

from app import create_app
from app.audit_infrastructure import apply_audit_infrastructure
from app.extensions import db


def is_fresh_database():
    """Return True when the application's auth schema is empty.

    "Fresh" is defined as the absence of ``auth.users``: every other
    schema in the project depends on it (FKs from budget/salary), so
    if it does not exist neither does anything else.  Returns False
    when the table is present, which signals "run incremental
    migrations" to the caller.
    """
    result = db.session.execute(db.text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM information_schema.tables "
        "  WHERE table_schema = 'auth' AND table_name = 'users'"
        ")"
    ))
    return not result.scalar()


def init_fresh_database(app):
    """Create the schema, the audit infrastructure, and stamp Alembic.

    Three steps in order:

    1. ``db.create_all()`` -- materialise every SQLAlchemy-modeled
       table.  This covers the ``ref``, ``auth``, ``budget``, and
       ``salary`` schemas.
    2. ``apply_audit_infrastructure`` -- materialise the
       ``system.audit_log`` table, the trigger function, the indexes,
       the per-table triggers (one per entry in
       :data:`app.audit_infrastructure.AUDITED_TABLES`), and the
       conditional ``shekel_app`` GRANT block.  ``db.create_all`` does
       not know about any of these -- they are raw SQL outside the
       SQLAlchemy model registry -- so this second step is what
       distinguishes fresh-DB initialisation post-C-13 from the
       previous bypass-of-audit-triggers behaviour that audit
       finding F-028 documents.
    3. ``alembic stamp head`` -- mark every migration as applied so
       subsequent ``flask db upgrade`` calls only apply
       newly-authored migrations.

    Args:
        app (flask.Flask): Application built by ``create_app()``.
            Used for the application context that ``db.create_all``
            and the ``session.execute`` calls require.
    """
    print("Fresh database detected. Creating all tables...")
    db.create_all()
    print("Tables created.")

    print("Materialising audit infrastructure (system.audit_log + triggers)...")
    apply_audit_infrastructure(
        lambda sql: db.session.execute(db.text(sql))
    )
    db.session.commit()
    print("Audit infrastructure ready.")

    # Stamp Alembic so it knows all migrations are "applied".
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("script_location", "migrations")
    with app.app_context():
        command.stamp(alembic_cfg, "head")
    print("Alembic stamped to head.")


def migrate_existing_database():
    """Run incremental Alembic migrations against a populated database."""
    print("Existing database detected. Running migrations...")
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("script_location", "migrations")
    command.upgrade(alembic_cfg, "head")
    print("Migrations complete.")


if __name__ == "__main__":
    flask_app = create_app()
    with flask_app.app_context():
        if is_fresh_database():
            init_fresh_database(flask_app)
        else:
            migrate_existing_database()
