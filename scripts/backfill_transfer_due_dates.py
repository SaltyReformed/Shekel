"""
Shekel Budget App -- Backfill Transfer Due Dates

Aligns existing recurring transfers with the unified due-date computation
introduced alongside ``budget.transfers.due_date``.

NOTE: the production deploy already performs this backfill automatically --
migration ``48e2c7ee593d`` recomputes eligible transfers as part of
``flask db upgrade``.  This script is the manual re-run / dry-run / diagnostic
equivalent: it is idempotent and a no-op once the migration has run, useful for
verifying state or re-applying after data edits made outside the recurrence
engine.

Background
----------
Recurring transfers were historically stamped with ``due_date = period.start_date``
(the pay-period start), discarding the recurrence rule's ``day_of_month``.  The
transfer recurrence engine now computes the due date the same way the transaction
engine does, via ``recurrence_engine.compute_due_date(rule, period)``: a rule with
a ``day_of_month`` (monthly/quarterly, and the mortgage payment whose rule carries
``day_of_month = LoanParams.payment_day``) yields that calendar day placed in the
period's month; rules without one (every-paycheck, every-N) still resolve to the
period start.  Newly generated transfers pick this up automatically; this script
brings already-generated rows into line so the calendar, dashboard, year-end, and
spending-trend surfaces match the loan card immediately.

Scope (what this touches and what it deliberately skips)
--------------------------------------------------------
Updates only transfers that are all of:
  * template-linked (``transfer_template_id IS NOT NULL``) -- ad-hoc transfers have
    no recurrence rule to compute from and keep whatever due date they were given;
  * not soft-deleted;
  * not an override (``is_override = FALSE``) -- a carried-forward transfer was moved
    by the user and keeps its original-month due date, matching the ongoing
    carry-forward behavior (it does not recompute due_date either);
  * in a non-immutable status -- confirmed/paid history is left untouched.

A transfer whose recomputed due date already equals its stored value is skipped
(no version bump, no audit row).  Each updated transfer is written once through
``transfer_service.update_transfer`` so the parent and both shadow transactions stay
equal (Transfer Invariant 3) and ``version_id`` bumps exactly once; iterating parent
transfers (not shadows) avoids touching a transfer twice.  The audit trigger on
``budget.transfers`` records each change with a NULL ``current_user_id`` (a system
backfill, matching the migration's backfill); volume is bounded (3 audit rows per
updated transfer, one-time run).

Usage
-----
    python scripts/backfill_transfer_due_dates.py [--dry-run] [--force] \
        [--database-url URL]

Options
    --dry-run          Report planned changes without modifying the database.
    --force            Skip the interactive confirmation prompt (for automation).
    --database-url URL Override the database URL (default: from .env / config).

Exit codes
    0  Completed successfully (including the no-op and dry-run cases).
    1  An update failed; the transaction was rolled back.
"""

import os
import sys

# Ensure the project root is on sys.path so 'app' is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse  # noqa: E402  pylint: disable=wrong-import-position
from datetime import date  # noqa: E402  pylint: disable=wrong-import-position


def collect_due_date_changes(db_session):
    """Find recurring transfers whose computed due date differs from stored.

    Examines template-linked, non-deleted, non-override transfers in a
    non-immutable status, computes the canonical due date for each via the
    shared recurrence helper, and collects the ones that need updating.

    Args:
        db_session: The active SQLAlchemy session.

    Returns:
        Tuple ``(summary, changes)`` where ``summary`` is a dict of counts
        (``examined``, ``unchanged``, ``skipped_no_rule``, ``to_update``) and
        ``changes`` is a list of ``(Transfer, computed_due_date)`` tuples for
        the transfers whose due date should change.  ``computed_due_date`` is a
        :class:`datetime.date` or ``None``.
    """
    # Local imports: this module runs as a script under an app context built
    # in main(), so app modules must not be imported at file scope.
    from app.models.transfer import Transfer  # pylint: disable=import-outside-toplevel
    from app.models.ref import Status  # pylint: disable=import-outside-toplevel
    from app.services.recurrence_engine import compute_due_date  # pylint: disable=import-outside-toplevel

    transfers = (
        db_session.query(Transfer)
        .join(Status, Transfer.status_id == Status.id)
        .filter(
            Transfer.transfer_template_id.isnot(None),
            Transfer.is_deleted.is_(False),
            Transfer.is_override.is_(False),
            Status.is_immutable.is_(False),
        )
        .order_by(Transfer.id)
        .all()
    )

    summary = {
        "examined": len(transfers),
        "unchanged": 0,
        "skipped_no_rule": 0,
        "to_update": 0,
    }
    changes: list[tuple[object, date | None]] = []

    for xfer in transfers:
        template = xfer.template
        rule = template.recurrence_rule if template is not None else None
        if rule is None:
            # Defensive: template-linked transfers normally have a rule
            # (the column is non-NULL at create time), but a SET NULL on
            # transfer_template_id or a malformed template could leave one
            # without; there is nothing to compute from, so skip.
            summary["skipped_no_rule"] += 1
            continue

        computed = compute_due_date(rule, xfer.pay_period)
        if computed == xfer.due_date:
            summary["unchanged"] += 1
            continue

        changes.append((xfer, computed))

    summary["to_update"] = len(changes)
    return summary, changes


def apply_due_date_changes(db_session, changes):
    """Apply due-date updates through the transfer service.

    Writes each change via ``transfer_service.update_transfer`` so the parent
    transfer and both shadow transactions stay equal and the optimistic-lock
    counter bumps once per transfer.  Does NOT commit -- the caller owns the
    transaction boundary.

    Args:
        db_session: The active SQLAlchemy session (unused directly; the
            service operates on the same session, but it is accepted so the
            caller's session ownership is explicit and the signature mirrors
            :func:`collect_due_date_changes`).
        changes: List of ``(Transfer, computed_due_date)`` tuples.

    Returns:
        The number of transfers updated.
    """
    from app.services import transfer_service  # pylint: disable=import-outside-toplevel

    for xfer, computed in changes:
        transfer_service.update_transfer(
            xfer.id, xfer.user_id, due_date=computed,
        )
    return len(changes)


def _print_summary(summary, applied):
    """Print a human-readable summary of the backfill.

    Args:
        summary: The counts dict from :func:`collect_due_date_changes`.
        applied: True if the changes were committed, False for a preview /
            dry-run report.
    """
    verb = "Updated" if applied else "Would update"
    print(
        f"Examined {summary['examined']} recurring transfer(s): "
        f"{verb} {summary['to_update']}, "
        f"{summary['unchanged']} already correct, "
        f"{summary['skipped_no_rule']} skipped (no recurrence rule)."
    )


def main():
    """Entry point: parse args, build app context, preview, confirm, apply."""
    parser = argparse.ArgumentParser(
        description="Backfill recurring-transfer due dates from the recurrence rule.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Report planned changes without modifying the database.")
    parser.add_argument("--force", action="store_true",
                        help="Skip the interactive confirmation prompt.")
    parser.add_argument("--database-url", default=None,
                        help="Override the database URL.")
    args = parser.parse_args()

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    from app import create_app  # pylint: disable=import-outside-toplevel
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from app.exceptions import NotFoundError  # pylint: disable=import-outside-toplevel
    from app.exceptions import ValidationError as ShekelValidationError  # pylint: disable=import-outside-toplevel
    from sqlalchemy.orm.exc import StaleDataError  # pylint: disable=import-outside-toplevel

    app = create_app()
    with app.app_context():
        summary, changes = collect_due_date_changes(db.session)

        for xfer, computed in changes:
            print(
                f"  Transfer #{xfer.id} (user {xfer.user_id}, period "
                f"{xfer.pay_period_id}): {xfer.due_date} -> {computed}"
            )

        if args.dry_run:
            _print_summary(summary, applied=False)
            print("--dry-run: no changes made.")
            db.session.rollback()
            return 0

        if not changes:
            _print_summary(summary, applied=False)
            print("Nothing to update.")
            return 0

        _print_summary(summary, applied=False)
        if not args.force:
            response = input(
                f"Apply {summary['to_update']} due-date update(s)? [y/N] "
            )
            if response.strip().lower() not in ("y", "yes"):
                print("Aborted -- no changes made.")
                db.session.rollback()
                return 0

        try:
            apply_due_date_changes(db.session, changes)
            db.session.commit()
        except (NotFoundError, ShekelValidationError, StaleDataError) as exc:
            db.session.rollback()
            print(f"FAILED: {exc}.  Rolled back -- no changes applied.")
            return 1

        _print_summary(summary, applied=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
