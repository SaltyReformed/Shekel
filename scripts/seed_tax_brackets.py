"""
Shekel Budget App — Seed Federal Tax Brackets, FICA, and State Tax Config

Seeds 2025 and 2026 federal income tax brackets and standard deductions
for all filing statuses, plus FICA configuration and a default state
tax config.

Uses upsert pattern to avoid duplicates on re-run.
Requires ref tables to be seeded first (seed_ref_tables.py).

Usage:
    python scripts/seed_tax_brackets.py
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models.ref import FilingStatus, TaxType
from app.models.tax_config import (
    FicaConfig,
    StateTaxConfig,
    TaxBracket,
    TaxBracketSet,
)
from app.models.user import User
from app.services.auth_service import (
    DEFAULT_FEDERAL_BRACKETS,
    DEFAULT_FICA,
    DEFAULT_STATE_TAX,
)


def seed_tax_brackets():
    """Seed federal brackets, FICA, and state tax config for all users."""
    users = db.session.query(User).all()
    if not users:
        print("No users found. Run seed_user.py first.")
        return

    filing_statuses = {
        fs.name: fs for fs in db.session.query(FilingStatus).all()
    }
    if not filing_statuses:
        print("No filing statuses found. Run seed_ref_tables.py first.")
        return

    for user in users:
        print(f"\nSeeding tax data for user: {user.email} (id={user.id})")
        for tax_year, year_data in DEFAULT_FEDERAL_BRACKETS.items():
            _seed_brackets_for_user(user, filing_statuses, tax_year, year_data)
        _seed_fica_for_user(user)
        _seed_state_tax_for_user(user)

    db.session.commit()
    print("\nTax bracket seeding complete.")


def _seed_brackets_for_user(user, filing_statuses, tax_year, bracket_data):
    """Seed federal brackets for a user and year."""
    for status_name, data in bracket_data.items():
        fs = filing_statuses.get(status_name)
        if not fs:
            print(f"  ! Filing status '{status_name}' not found, skipping.")
            continue

        existing = (
            db.session.query(TaxBracketSet)
            .filter_by(user_id=user.id, tax_year=tax_year, filing_status_id=fs.id)
            .first()
        )
        if existing:
            print(f"  ~ {tax_year} {status_name} brackets already exist, skipping.")
            continue

        bracket_set = TaxBracketSet(
            user_id=user.id,
            filing_status_id=fs.id,
            tax_year=tax_year,
            standard_deduction=data["standard_deduction"],
            child_credit_amount=data.get("child_credit_amount", Decimal("0")),
            other_dependent_credit_amount=data.get(
                "other_dependent_credit_amount", Decimal("0")
            ),
            description=f"{tax_year} Federal - {status_name.replace('_', ' ').title()}",
        )
        db.session.add(bracket_set)
        db.session.flush()

        for idx, (min_inc, max_inc, rate) in enumerate(data["brackets"]):
            bracket = TaxBracket(
                bracket_set_id=bracket_set.id,
                min_income=Decimal(str(min_inc)),
                max_income=Decimal(str(max_inc)) if max_inc else None,
                rate=rate,
                sort_order=idx,
            )
            db.session.add(bracket)

        print(f"  + {tax_year} {status_name}: {len(data['brackets'])} brackets")


def _seed_fica_for_user(user):
    """Seed FICA configuration for a user."""
    for tax_year, data in DEFAULT_FICA.items():
        existing = (
            db.session.query(FicaConfig)
            .filter_by(user_id=user.id, tax_year=tax_year)
            .first()
        )
        if existing:
            print(f"  ~ {tax_year} FICA config already exists, skipping.")
            continue

        fica = FicaConfig(user_id=user.id, tax_year=tax_year, **data)
        db.session.add(fica)
        print(f"  + {tax_year} FICA config")


def _seed_state_tax_for_user(user):
    """Seed default state tax configuration."""
    tax_type = (
        db.session.query(TaxType)
        .filter_by(name="flat")
        .first()
    )
    if not tax_type:
        print("  ! Tax type 'flat' not found, skipping state config.")
        return

    for tax_year, data in DEFAULT_STATE_TAX.items():
        state_code = data["state_code"]
        existing = (
            db.session.query(StateTaxConfig)
            .filter_by(user_id=user.id, state_code=state_code, tax_year=tax_year)
            .first()
        )
        if existing:
            print(f"  ~ {tax_year} {state_code} state tax config already exists, skipping.")
            continue

        state_config = StateTaxConfig(
            user_id=user.id,
            tax_type_id=tax_type.id,
            tax_year=tax_year,
            state_code=state_code,
            flat_rate=data["flat_rate"],
            standard_deduction=data.get("standard_deduction"),
        )
        db.session.add(state_config)
        print(f"  + {tax_year} {state_code} state tax config (flat {data['flat_rate']})")


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        seed_tax_brackets()
