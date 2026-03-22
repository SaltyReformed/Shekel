"""
Shekel Budget App -- Database Initialization

Detects fresh vs. existing databases and initializes accordingly:
- Fresh DB: Creates all tables via SQLAlchemy metadata, then stamps
  Alembic to mark all migrations as applied.
- Existing DB: Runs incremental Alembic migrations.

Usage:
    python scripts/init_database.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alembic import command
from alembic.config import Config
from app import create_app
from app.extensions import db


def is_fresh_database():
    """Check if this is a fresh database (no application tables exist)."""
    result = db.session.execute(db.text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM information_schema.tables "
        "  WHERE table_schema = 'auth' AND table_name = 'users'"
        ")"
    ))
    return not result.scalar()


def init_fresh_database(app):
    """Create all tables from models and stamp Alembic to head."""
    print("Fresh database detected. Creating all tables...")
    db.create_all()
    print("Tables created.")

    # Stamp Alembic so it knows all migrations are "applied".
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("script_location", "migrations")
    with app.app_context():
        command.stamp(alembic_cfg, "head")
    print("Alembic stamped to head.")


def migrate_existing_database():
    """Run incremental Alembic migrations."""
    print("Existing database detected. Running migrations...")
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("script_location", "migrations")
    command.upgrade(alembic_cfg, "head")
    print("Migrations complete.")


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        if is_fresh_database():
            init_fresh_database(app)
        else:
            migrate_existing_database()
