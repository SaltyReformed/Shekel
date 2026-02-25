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


# ── 2025 Federal Tax Brackets ──────────────────────────────────────

BRACKETS_2025 = {
    "single": {
        "standard_deduction": Decimal("15000"),
        "brackets": [
            (0,      11925,   Decimal("0.1000")),
            (11925,  48475,   Decimal("0.1200")),
            (48475,  103350,  Decimal("0.2200")),
            (103350, 197300,  Decimal("0.2400")),
            (197300, 250525,  Decimal("0.3200")),
            (250525, 626350,  Decimal("0.3500")),
            (626350, None,    Decimal("0.3700")),
        ],
    },
    "married_jointly": {
        "standard_deduction": Decimal("30000"),
        "brackets": [
            (0,      23850,   Decimal("0.1000")),
            (23850,  96950,   Decimal("0.1200")),
            (96950,  206700,  Decimal("0.2200")),
            (206700, 394600,  Decimal("0.2400")),
            (394600, 501050,  Decimal("0.3200")),
            (501050, 751600,  Decimal("0.3500")),
            (751600, None,    Decimal("0.3700")),
        ],
    },
    "married_separately": {
        "standard_deduction": Decimal("15000"),
        "brackets": [
            (0,      11925,   Decimal("0.1000")),
            (11925,  48475,   Decimal("0.1200")),
            (48475,  103350,  Decimal("0.2200")),
            (103350, 197300,  Decimal("0.2400")),
            (197300, 250525,  Decimal("0.3200")),
            (250525, 375800,  Decimal("0.3500")),
            (375800, None,    Decimal("0.3700")),
        ],
    },
    "head_of_household": {
        "standard_deduction": Decimal("22500"),
        "brackets": [
            (0,      17000,   Decimal("0.1000")),
            (17000,  64850,   Decimal("0.1200")),
            (64850,  103350,  Decimal("0.2200")),
            (103350, 197300,  Decimal("0.2400")),
            (197300, 250500,  Decimal("0.3200")),
            (250500, 626350,  Decimal("0.3500")),
            (626350, None,    Decimal("0.3700")),
        ],
    },
}

# 2026 brackets (estimated — same structure, adjusted for inflation)
BRACKETS_2026 = {
    "single": {
        "standard_deduction": Decimal("15350"),
        "brackets": [
            (0,      12150,   Decimal("0.1000")),
            (12150,  49475,   Decimal("0.1200")),
            (49475,  105525,  Decimal("0.2200")),
            (105525, 201350,  Decimal("0.2400")),
            (201350, 255800,  Decimal("0.3200")),
            (255800, 639500,  Decimal("0.3500")),
            (639500, None,    Decimal("0.3700")),
        ],
    },
    "married_jointly": {
        "standard_deduction": Decimal("30700"),
        "brackets": [
            (0,      24300,   Decimal("0.1000")),
            (24300,  98950,   Decimal("0.1200")),
            (98950,  211050,  Decimal("0.2200")),
            (211050, 402700,  Decimal("0.2400")),
            (402700, 511500,  Decimal("0.3200")),
            (511500, 767200,  Decimal("0.3500")),
            (767200, None,    Decimal("0.3700")),
        ],
    },
    "married_separately": {
        "standard_deduction": Decimal("15350"),
        "brackets": [
            (0,      12150,   Decimal("0.1000")),
            (12150,  49475,   Decimal("0.1200")),
            (49475,  105525,  Decimal("0.2200")),
            (105525, 201350,  Decimal("0.2400")),
            (201350, 255800,  Decimal("0.3200")),
            (255800, 383600,  Decimal("0.3500")),
            (383600, None,    Decimal("0.3700")),
        ],
    },
    "head_of_household": {
        "standard_deduction": Decimal("23000"),
        "brackets": [
            (0,      17350,   Decimal("0.1000")),
            (17350,  66200,   Decimal("0.1200")),
            (66200,  105525,  Decimal("0.2200")),
            (105525, 201350,  Decimal("0.2400")),
            (201350, 255800,  Decimal("0.3200")),
            (255800, 639500,  Decimal("0.3500")),
            (639500, None,    Decimal("0.3700")),
        ],
    },
}

# ── FICA Configuration ─────────────────────────────────────────────

FICA_DATA = {
    2025: {
        "ss_rate": Decimal("0.0620"),
        "ss_wage_base": Decimal("176100"),
        "medicare_rate": Decimal("0.0145"),
        "medicare_surtax_rate": Decimal("0.0090"),
        "medicare_surtax_threshold": Decimal("200000"),
    },
    2026: {
        "ss_rate": Decimal("0.0620"),
        "ss_wage_base": Decimal("180000"),
        "medicare_rate": Decimal("0.0145"),
        "medicare_surtax_rate": Decimal("0.0090"),
        "medicare_surtax_threshold": Decimal("200000"),
    },
}

# ── Default State Tax (NC = 4.5% flat) ─────────────────────────────

DEFAULT_STATE = {
    "state_code": "NC",
    "flat_rate": Decimal("0.0450"),
    "tax_type_name": "flat",
}


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
        _seed_brackets_for_user(user, filing_statuses, 2025, BRACKETS_2025)
        _seed_brackets_for_user(user, filing_statuses, 2026, BRACKETS_2026)
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
    for tax_year, data in FICA_DATA.items():
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
    state_code = DEFAULT_STATE["state_code"]
    existing = (
        db.session.query(StateTaxConfig)
        .filter_by(user_id=user.id, state_code=state_code)
        .first()
    )
    if existing:
        print(f"  ~ {state_code} state tax config already exists, skipping.")
        return

    tax_type = (
        db.session.query(TaxType)
        .filter_by(name=DEFAULT_STATE["tax_type_name"])
        .first()
    )
    if not tax_type:
        print("  ! Tax type 'flat' not found, skipping state config.")
        return

    state_config = StateTaxConfig(
        user_id=user.id,
        tax_type_id=tax_type.id,
        state_code=state_code,
        flat_rate=DEFAULT_STATE["flat_rate"],
    )
    db.session.add(state_config)
    print(f"  + {state_code} state tax config (flat {DEFAULT_STATE['flat_rate']})")


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        seed_tax_brackets()
