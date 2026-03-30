"""
Shekel Budget App -- Seed Reference Tables

Populates ref-schema lookup tables for all phases: account types,
transaction types, statuses, recurrence patterns, filing statuses,
deduction timings, calc methods, tax types, and raise types.

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
    AccountTypeCategory,
    CalcMethod,
    DeductionTiming,
    FilingStatus,
    RaiseType,
    RecurrencePattern,
    Status,
    TaxType,
    TransactionType,
)


from app.ref_seeds import ACCT_TYPE_SEEDS

REF_DATA = [
    (TransactionType, ["Income", "Expense"]),
    (Status, [
        {"name": "Projected", "is_settled": False, "is_immutable": False, "excludes_from_balance": False},
        {"name": "Paid", "is_settled": True, "is_immutable": True, "excludes_from_balance": False},
        {"name": "Received", "is_settled": True, "is_immutable": True, "excludes_from_balance": False},
        {"name": "Credit", "is_settled": False, "is_immutable": True, "excludes_from_balance": True},
        {"name": "Cancelled", "is_settled": False, "is_immutable": True, "excludes_from_balance": True},
        {"name": "Settled", "is_settled": True, "is_immutable": True, "excludes_from_balance": False},
    ]),
    (RecurrencePattern, [
        "Every Period", "Every N Periods", "Monthly", "Monthly First",
        "Quarterly", "Semi-Annual", "Annual", "Once",
    ]),
    (FilingStatus, ["single", "married_jointly", "married_separately", "head_of_household"]),
    (DeductionTiming, ["pre_tax", "post_tax"]),
    (CalcMethod, ["flat", "percentage"]),
    (TaxType, ["flat", "none", "bracket"]),
    (RaiseType, ["merit", "cola", "custom"]),
]


def seed_ref_tables():
    """Insert reference table rows if they don't already exist."""
    # ── Seed AccountTypeCategory (must precede AccountType) ──────
    category_seeds = ["Asset", "Liability", "Retirement", "Investment"]
    for cat_name in category_seeds:
        if not db.session.query(AccountTypeCategory).filter_by(name=cat_name).first():
            db.session.add(AccountTypeCategory(name=cat_name))
            print(f"  + account_type_categories: {cat_name}")
    db.session.flush()

    # Build category name->id lookup for AccountType seeding.
    cat_lookup = {
        c.name: c.id
        for c in db.session.query(AccountTypeCategory).all()
    }

    # ── Seed AccountType with FK, booleans, metadata ──────────────
    for entry in ACCT_TYPE_SEEDS:
        (name, cat_name, has_params, has_amort,
         has_int, is_pre, is_liq, icon, max_term) = entry
        existing = db.session.query(AccountType).filter_by(name=name).first()
        if existing:
            # Update reference metadata on re-run.
            existing.has_parameters = has_params
            existing.has_amortization = has_amort
            existing.has_interest = has_int
            existing.is_pretax = is_pre
            existing.is_liquid = is_liq
            existing.icon_class = icon
            existing.max_term_months = max_term
        else:
            db.session.add(AccountType(
                name=name,
                category_id=cat_lookup[cat_name],
                has_parameters=has_params,
                has_amortization=has_amort,
                has_interest=has_int,
                is_pretax=is_pre,
                is_liquid=is_liq,
                icon_class=icon,
                max_term_months=max_term,
            ))
            print(f"  + account_types: {name}")

    # ── Seed remaining ref tables ────────────────────────────────
    for model, entries in REF_DATA:
        for entry in entries:
            if isinstance(entry, dict):
                name = entry["name"]
                existing = db.session.query(model).filter_by(name=name).first()
                if existing:
                    continue
                db.session.add(model(**entry))
                print(f"  + {model.__tablename__}: {name}")
            else:
                existing = db.session.query(model).filter_by(name=entry).first()
                if existing:
                    continue
                db.session.add(model(name=entry))
                print(f"  + {model.__tablename__}: {entry}")

    db.session.commit()
    print("\nRef table seeding complete.")


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        seed_ref_tables()
