"""
Shekel Budget App -- Seed Reference Tables

Populates ref-schema lookup tables for all phases: account types,
transaction types, statuses, recurrence patterns, filing statuses,
deduction timings, calc methods, tax types, and raise types.

Uses upsert pattern to avoid duplicates on re-run; delegates to
``app.ref_seeds.seed_reference_data`` -- the single source of truth
for ref-table seeding across the application factory, this script,
the pytest fixture stack, and the test-template builder.  See audit
finding H-002 for the rationale.

Usage:
    python scripts/seed_ref_tables.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# pylint: disable=wrong-import-position
from app import create_app
from app.extensions import db
from app.ref_seeds import seed_reference_data


def seed_ref_tables():
    """Seed every ref table, committing the transaction at the end.

    Thin wrapper around ``seed_reference_data`` that owns the
    transaction boundary and prints one line per inserted row so the
    deploy operator gets an audit trail in the entrypoint logs.
    """
    seed_reference_data(db.session, verbose=True)
    db.session.commit()
    print("\nRef table seeding complete.")


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        seed_ref_tables()
