"""
Shekel Budget App -- Seed Federal Tax Brackets, FICA, and State Tax Config

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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pylint: wrong-import-position -- the sys.path bootstrap above must run
# before these imports so ``app`` resolves when invoked as
# ``python scripts/seed_tax_brackets.py`` (sys.path[0] is scripts/, not
# the repo root, in that mode).
# pylint: disable=wrong-import-position
from app import create_app, ref_cache
from app.enums import TaxTypeEnum
from app.extensions import db
from app.models.ref import FilingStatus
from app.models.tax_config import (
    FicaConfig,
    StateChildDeduction,
    StateTaxConfig,
    TaxBracketSet,
)
from app.models.user import User
from app.services.tax_seed_data import (
    DEFAULT_FEDERAL_BRACKETS,
    DEFAULT_FICA,
    DEFAULT_STATE_CHILD_DEDUCTIONS,
    DEFAULT_STATE_TAX,
    build_state_child_deductions,
    build_state_tax_configs,
    build_tax_bracket_set,
    build_tax_brackets,
)
# pylint: enable=wrong-import-position


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
        # Audit finding F-114 / C-16: the seed script's stdout is
        # captured by the container log driver and forwarded off-
        # host.  Logging ``user.email`` on every container start
        # would surface a real PII value in long-term log storage
        # with no operational benefit (the operator already knows
        # which accounts exist).  user_id is the authoritative
        # identifier for cross-referencing; the email is intentionally
        # omitted from the log line.
        print(f"\nSeeding tax data for user id={user.id}")
        for tax_year, year_data in DEFAULT_FEDERAL_BRACKETS.items():
            _seed_brackets_for_user(user, filing_statuses, tax_year, year_data)
        _seed_fica_for_user(user)
        _seed_state_tax_for_user(user, filing_statuses)
        _seed_state_child_deductions_for_user(user, filing_statuses)

    db.session.commit()
    print("\nTax bracket seeding complete.")


def _seed_brackets_for_user(user, filing_statuses, tax_year, bracket_data):
    """Seed federal brackets for a user and year.

    Row construction is shared with the sign-up path via the
    ``auth_service.build_tax_*`` builders; this script owns only the
    repair-tool policy around them (per-row skip-if-exists + progress
    prints).
    """
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

        bracket_set = build_tax_bracket_set(
            user.id, fs.id, tax_year, status_name, data,
        )
        db.session.add(bracket_set)
        db.session.flush()

        for bracket in build_tax_brackets(bracket_set.id, data["brackets"]):
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


def _seed_state_tax_for_user(user, filing_statuses):
    """Seed default (per-filing-status) state tax configuration.

    Post-T-P5 the state config is keyed on filing status, so one row per
    status is built via ``build_state_tax_configs``; each is skipped
    individually if it already exists (idempotent repair).
    """
    flat_type_id = ref_cache.tax_type_id(TaxTypeEnum.FLAT)
    filing_status_ids = {name: fs.id for name, fs in filing_statuses.items()}

    for tax_year, data in DEFAULT_STATE_TAX.items():
        state_code = data["state_code"]
        for config in build_state_tax_configs(
            user.id, flat_type_id, tax_year, data, filing_status_ids,
        ):
            existing = (
                db.session.query(StateTaxConfig)
                .filter_by(
                    user_id=user.id, state_code=state_code, tax_year=tax_year,
                    filing_status_id=config.filing_status_id,
                )
                .first()
            )
            if existing:
                print(
                    f"  ~ {tax_year} {state_code} state tax config "
                    f"(status_id={config.filing_status_id}) already exists, skipping."
                )
                continue
            db.session.add(config)
            print(
                f"  + {tax_year} {state_code} state tax config "
                f"(status_id={config.filing_status_id}, flat {data['flat_rate']})"
            )


def _seed_state_child_deductions_for_user(user, filing_statuses):
    """Seed the NC per-child deduction tiers (per filing status, AGI-tiered).

    Idempotent: for a given ``(state, year, filing_status)`` the whole tier
    set is skipped when ANY tier row already exists (they are seeded as a
    unit, never partially).
    """
    filing_status_ids = {name: fs.id for name, fs in filing_statuses.items()}

    for tax_year, data in DEFAULT_STATE_CHILD_DEDUCTIONS.items():
        state_code = data["state_code"]
        existing_status_ids = {
            row.filing_status_id
            for row in db.session.query(StateChildDeduction.filing_status_id)
            .filter_by(user_id=user.id, state_code=state_code, tax_year=tax_year)
            .distinct()
        }
        added_status_ids: set[int] = set()
        for row in build_state_child_deductions(
            user.id, tax_year, data, filing_status_ids,
        ):
            if row.filing_status_id in existing_status_ids:
                continue
            db.session.add(row)
            added_status_ids.add(row.filing_status_id)
        for status_id in sorted(added_status_ids):
            print(
                f"  + {tax_year} {state_code} child deductions "
                f"(status_id={status_id})"
            )


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        seed_tax_brackets()
