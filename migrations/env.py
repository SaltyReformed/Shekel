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
    """Include all schemas that Shekel uses in autogenerate."""
    if type_ == "schema":
        return name in ("ref", "auth", "budget", "salary", "system", None)
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
            version_table_schema="public",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()