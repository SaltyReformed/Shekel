"""
Shekel Budget App -- Seed Phase 2 Reference Tables

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
    AccountType,
    CalcMethod,
    DeductionTiming,
    FilingStatus,
    RaiseType,
    RecurrencePattern,
    Status,
    TaxType,
    TransactionType,
)


REF_DATA = {
    AccountType: [
        "checking", "savings", "hysa", "money_market", "cd", "hsa",
        "credit_card", "mortgage", "auto_loan", "student_loan",
        "personal_loan", "heloc",
        "401k", "roth_401k", "traditional_ira", "roth_ira",
        "brokerage", "529",
    ],
    TransactionType: ["income", "expense"],
    Status: ["projected", "done", "received", "credit", "cancelled", "settled"],
    RecurrencePattern: [
        "every_period", "every_n_periods", "monthly", "monthly_first",
        "quarterly", "semi_annual", "annual", "once",
    ],
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

    # Backfill category on account types.
    category_map = {
        "checking": "asset", "savings": "asset", "hysa": "asset",
        "money_market": "asset", "cd": "asset", "hsa": "asset",
        "credit_card": "liability", "mortgage": "liability",
        "auto_loan": "liability", "student_loan": "liability",
        "personal_loan": "liability", "heloc": "liability",
        "401k": "retirement", "roth_401k": "retirement",
        "traditional_ira": "retirement", "roth_ira": "retirement",
        "brokerage": "investment", "529": "investment",
    }
    for type_name, category in category_map.items():
        at = db.session.query(AccountType).filter_by(name=type_name).first()
        if at and at.category != category:
            at.category = category
            print(f"  ~ {at.name}: category → {category}")

    db.session.commit()
    print("\nRef table seeding complete.")


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        seed_ref_tables()
