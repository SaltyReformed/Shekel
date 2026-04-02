"""
Shekel Budget App -- Repair Orphaned Transfers

One-time data repair script that finds transfers without shadow transactions
and creates the missing shadows via transfer_service.create_transfer().

Background: A bug in create_transfer_template() caused one-time (non-recurring)
transfers to be created as Transfer records without corresponding shadow
transactions in budget.transactions.  The shadow transactions are required for
the balance calculator and grid to see the transfer.

Usage:
    python scripts/repair_orphaned_transfers.py [--dry-run] [--database-url URL]

Options:
    --dry-run          Report orphaned transfers without modifying the database.
    --database-url URL Override the database URL (default: from .env / config).

Exit codes:
    0  No orphaned transfers found (or all repaired successfully).
    1  Repair failed or errors encountered.
"""

import os
import sys

# Ensure the project root is on sys.path so 'app' is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse  # noqa: E402  pylint: disable=wrong-import-position
from decimal import Decimal  # noqa: E402  pylint: disable=wrong-import-position


def find_orphaned_transfers(db_session, Transfer, Transaction):
    """Find active transfers that do not have exactly 2 active shadow transactions.

    Returns:
        List of (Transfer, shadow_count) tuples.
    """
    all_transfers = (
        db_session.query(Transfer)
        .filter(Transfer.is_deleted.is_(False))
        .all()
    )
    orphaned = []
    for xfer in all_transfers:
        shadow_count = (
            db_session.query(Transaction)
            .filter(
                Transaction.transfer_id == xfer.id,
                Transaction.is_deleted.is_(False),
            )
            .count()
        )
        if shadow_count != 2:
            orphaned.append((xfer, shadow_count))
    return orphaned


def repair_transfer(xfer, db_session, transfer_service, ref_cache, StatusEnum, TxnTypeEnum):
    """Create missing shadow transactions for an orphaned transfer.

    Deletes any existing partial shadows and recreates both via direct
    insert (matching transfer_service.create_transfer logic) to avoid
    re-validating the already-committed transfer record.
    """
    from app.models.transaction import Transaction  # pylint: disable=import-outside-toplevel
    from app.models.category import Category  # pylint: disable=import-outside-toplevel

    # Remove any partial shadows first.
    existing_shadows = (
        db_session.query(Transaction)
        .filter(Transaction.transfer_id == xfer.id)
        .all()
    )
    for shadow in existing_shadows:
        db_session.delete(shadow)
    db_session.flush()

    # Look up reference IDs.
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

    # Look up default transfer categories.
    outgoing_cat = (
        db_session.query(Category)
        .filter_by(user_id=xfer.user_id, item_name="Outgoing")
        .first()
    )
    incoming_cat = (
        db_session.query(Category)
        .filter_by(user_id=xfer.user_id, item_name="Incoming")
        .first()
    )

    expense_category_id = xfer.category_id or (outgoing_cat.id if outgoing_cat else None)
    income_category_id = incoming_cat.id if incoming_cat else None

    # Create expense shadow (from_account -- money leaving).
    expense_shadow = Transaction(
        account_id=xfer.from_account_id,
        transfer_id=xfer.id,
        transaction_type_id=expense_type_id,
        pay_period_id=xfer.pay_period_id,
        scenario_id=xfer.scenario_id,
        status_id=xfer.status_id,
        name=f"Transfer to {xfer.to_account.name}",
        estimated_amount=xfer.amount,
        category_id=expense_category_id,
    )
    db_session.add(expense_shadow)

    # Create income shadow (to_account -- money arriving).
    income_shadow = Transaction(
        account_id=xfer.to_account_id,
        transfer_id=xfer.id,
        transaction_type_id=income_type_id,
        pay_period_id=xfer.pay_period_id,
        scenario_id=xfer.scenario_id,
        status_id=xfer.status_id,
        name=f"Transfer from {xfer.from_account.name}",
        estimated_amount=xfer.amount,
        category_id=income_category_id,
    )
    db_session.add(income_shadow)
    db_session.flush()

    return expense_shadow, income_shadow


def main():
    """Entry point for the repair script."""
    parser = argparse.ArgumentParser(description="Repair orphaned transfers.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report orphans without modifying the database.")
    parser.add_argument("--database-url", default=None,
                        help="Override the database URL.")
    args = parser.parse_args()

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    from app import create_app  # pylint: disable=import-outside-toplevel
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from app.models.transfer import Transfer  # pylint: disable=import-outside-toplevel
    from app.models.transaction import Transaction  # pylint: disable=import-outside-toplevel
    from app.services import transfer_service  # pylint: disable=import-outside-toplevel
    from app import ref_cache  # pylint: disable=import-outside-toplevel
    from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

    app = create_app()
    with app.app_context():
        # ── Step 1: Ensure all users have transfer categories ──────
        _ensure_transfer_categories(db.session)

        # ── Step 2: Find and repair orphaned transfers ─────────────
        orphaned = find_orphaned_transfers(db.session, Transfer, Transaction)

        if not orphaned:
            print("No orphaned transfers found. Database is clean.")
            return 0

        print(f"Found {len(orphaned)} orphaned transfer(s):")
        for xfer, shadow_count in orphaned:
            print(f"  Transfer #{xfer.id}: name={xfer.name!r}, "
                  f"amount={xfer.amount}, shadows={shadow_count}, "
                  f"user_id={xfer.user_id}, period_id={xfer.pay_period_id}")

        if args.dry_run:
            print("\n--dry-run: No changes made.")
            return 0

        print("\nRepairing...")
        repaired = 0
        for xfer, shadow_count in orphaned:
            try:
                expense, income = repair_transfer(
                    xfer, db.session, transfer_service,
                    ref_cache, StatusEnum, TxnTypeEnum,
                )
                repaired += 1
                print(f"  Repaired Transfer #{xfer.id}: "
                      f"expense_shadow={expense.id}, income_shadow={income.id}")
            except Exception as exc:  # pylint: disable=broad-except
                print(f"  FAILED Transfer #{xfer.id}: {exc}")
                db.session.rollback()
                return 1

        db.session.commit()
        print(f"\nDone. Repaired {repaired} transfer(s).")
        return 0


def _ensure_transfer_categories(db_session):
    """Create missing 'Transfers: Incoming' and 'Transfers: Outgoing'
    categories for all users.

    These categories are required for transfer shadow transactions to
    display correctly in the grid.  Users created before the transfer
    rework may be missing them.
    """
    from app.models.user import User  # pylint: disable=import-outside-toplevel
    from app.models.category import Category  # pylint: disable=import-outside-toplevel

    users = db_session.query(User).all()
    created = 0
    for user in users:
        for group, item in [("Transfers", "Incoming"), ("Transfers", "Outgoing")]:
            existing = db_session.query(Category).filter_by(
                user_id=user.id, group_name=group, item_name=item,
            ).first()
            if existing is None:
                db_session.add(Category(
                    user_id=user.id, group_name=group, item_name=item,
                ))
                created += 1
                print(f"  Created category '{group}: {item}' for user {user.id}")
    if created:
        db_session.commit()
        print(f"  Created {created} missing transfer categories.")
    else:
        print("All users have transfer categories.")


if __name__ == "__main__":
    sys.exit(main())
