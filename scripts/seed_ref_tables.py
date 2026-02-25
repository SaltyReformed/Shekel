"""
Shekel Budget App — Seed Phase 2 Reference Tables

Populates ref-schema lookup tables needed by the salary/paycheck system:
filing statuses, deduction timings, calc methods, tax types, raise types.

Uses upsert pattern to avoid duplicates on re-run.

Usage:
    python scripts/seed_ref_tables.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models.ref import (
    CalcMethod,
    DeductionTiming,
    FilingStatus,
    RaiseType,
    TaxType,
)


REF_DATA = {
    FilingStatus: ["single", "married_jointly", "married_separately", "head_of_household"],
    DeductionTiming: ["pre_tax", "post_tax"],
    CalcMethod: ["flat", "percentage"],
    TaxType: ["flat", "none", "bracket"],
    RaiseType: ["merit", "cola", "custom"],
}


def seed_ref_tables():
    """Insert reference table rows if they don't already exist."""
    for model, names in REF_DATA.items():
        for name in names:
            existing = db.session.query(model).filter_by(name=name).first()
            if existing:
                continue
            db.session.add(model(name=name))
            print(f"  + {model.__tablename__}: {name}")

    db.session.commit()
    print("\nRef table seeding complete.")


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        seed_ref_tables()
