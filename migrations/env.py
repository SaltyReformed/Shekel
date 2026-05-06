"""
Shekel Budget App -- Alembic Environment Configuration

This file is used by Flask-Migrate (Alembic) for database migrations.
It imports the app factory and model registry so autogenerate can
discover all models and their PostgreSQL schema mappings.
"""

import logging
from logging.config import fileConfig

from flask import current_app
from alembic import context

# Alembic Config object -- access to .ini file values.
config = context.config

# Set up Python logging from alembic.ini if it exists.
# Flask-Migrate may pass a path like "migrations/alembic.ini" that
# doesn't exist -- logging is already configured by Flask in that case.
if config.config_file_name is not None:
    import os
    if os.path.exists(config.config_file_name):
        fileConfig(config.config_file_name)

logger = logging.getLogger("alembic.env")

# Target metadata for autogenerate -- pulled from Flask-SQLAlchemy.
# Importing models/__init__.py ensures all models are registered.
import app.models  # noqa: F401, E402  pylint: disable=unused-import

target_metadata = current_app.extensions["migrate"].db.metadata


def include_name(name, type_, parent_names):
    """Include all schemas that Shekel uses in autogenerate.

    The ``system`` schema is intentionally listed even though no
    SQLAlchemy model maps to it -- :func:`include_object` filters
    individual ``system.*`` objects out below.  Excluding the schema
    here would silently hide autogenerate-driven detection of any
    new SQLAlchemy-managed object the developer might one day add
    in ``system``.
    """
    if type_ == "schema":
        return name in ("ref", "auth", "budget", "salary", "system", None)
    return True


# Tables that live outside the SQLAlchemy model registry but exist on
# the live database.  Autogenerate would otherwise reflect them and
# propose ``op.drop_table(...)`` on every migrate run.
_NON_MODEL_TABLES = frozenset({
    # public.alembic_version -- Alembic bookkeeping; see
    # ``version_table_schema`` below.
    "alembic_version",
    # system.audit_log -- created and maintained by the rebuild
    # migration (revision a5be2a99ea14) via raw SQL because PostgreSQL
    # row-level triggers and the JSONB columns the trigger writes are
    # outside the natural ORM mapping.  Adding it as a SQLAlchemy
    # model would tempt the developer to query it through the same
    # session that writes the rows being audited, which is exactly the
    # tampering vector finding F-028 calls out.  Keep it raw.
    "audit_log",
})

# Indexes belonging to the non-model tables.  Autogenerate compares
# index existence independently of the parent table, so the table
# filter alone is not sufficient -- without these entries, every run
# would propose ``op.drop_index('idx_audit_log_*')``.
_NON_MODEL_INDEXES = frozenset({
    "idx_audit_log_table",
    "idx_audit_log_executed",
    "idx_audit_log_row",
})


def include_object(object_, name, type_, reflected, compare_to):
    """Skip live objects that are intentionally outside the model registry.

    ``alembic_version`` (in ``public``) is Alembic's own bookkeeping
    and is never declared as a SQLAlchemy model.  ``system.audit_log``
    plus its three indexes are managed by the rebuild migration
    (revision a5be2a99ea14) via raw SQL; treating them as autogenerate
    candidates would propose ``op.drop_table('audit_log')`` and the
    matching index drops on every migrate run.

    Returning ``False`` at the ``"table"`` or ``"index"`` type
    short-circuits both the drop op and any child column/constraint
    comparison -- the documented Alembic idiom for excluding an
    object from autogenerate.
    """
    if type_ == "table" and name in _NON_MODEL_TABLES:
        return False
    if type_ == "index" and name in _NON_MODEL_INDEXES:
        return False
    return True


def run_migrations_offline():
    """Run migrations in 'offline' mode (generates SQL without a live DB)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_name=include_name,
        include_object=include_object,
        version_table_schema="public",
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode (applies directly to the DB)."""

    def process_revision_directives(context, revision, directives):
        """Prevent empty migration files."""
        if getattr(config.cmd_opts, "autogenerate", False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info("No changes detected -- skipping autogenerate.")

    connectable = current_app.extensions["migrate"].db.engine

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            process_revision_directives=process_revision_directives,
            include_schemas=True,
            include_name=include_name,
            include_object=include_object,
            version_table_schema="public",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()