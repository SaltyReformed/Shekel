"""
Shekel Budget App -- Pre-fix RECEIVED-history destruction scan (OPT-2 / CRIT-05)

READ-ONLY diagnostic that cross-references ``system.audit_log`` DELETE
rows on ``budget.transactions`` against the post-Commit-21 template
hard-delete guard.  It reports affected templates, periods, amounts,
statuses, and timestamps for every settled (Paid/Received/Settled)
template-linked transaction that was destroyed before the guard
landed -- the blast radius of CRIT-05.

This script NEVER mutates the database.  It is purely diagnostic; the
audit trail it reads is the only forensic record of pre-fix damage and
deleted-row data is unrecoverable from the application schema.  The
report informs whether manual data reconstruction (e.g. from
point-in-time backups) is warranted.

Usage:
    python scripts/scan_destroyed_received_history.py [--database-url URL] [--verbose]

Options:
    --database-url URL  Override the database URL (default: from .env / Flask
                        config).  Sensitive values stay in environment variables
                        and are referenced as ``[set via environment variable]``
                        in any printed help; the URL itself is never echoed.
    --verbose           List every destroyed row (default: summary only).

Exit codes:
    0  Scan completed successfully (findings, if any, are in the report).
    3  Script error (bad arguments, database connection failure).

The script is idempotent: it writes nothing, so two consecutive runs
produce identical output against an unchanged audit trail.  No
``--force`` flag exists because no destructive action is possible.
"""

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────


@dataclass(frozen=True)
class DestroyedRow:
    """One settled template-linked transaction destroyed pre-Commit-21.

    Fields mirror the columns the scan reads from ``system.audit_log``;
    they are kept ``frozen`` so a row cannot be mutated by the report
    formatter and the dataclass can be safely passed across helpers.

    Attributes:
        audit_id: Primary key of the ``system.audit_log`` row.  Useful
            for cross-referencing the raw audit entry if the operator
            wants the full JSONB payload.
        executed_at: ``TIMESTAMPTZ`` at which the trigger fired (when
            the DELETE was committed).
        transaction_id: ``row_id`` from the audit row -- the destroyed
            transaction's primary key.  Cannot be used to fetch the
            row (it is gone), only to correlate with prior backups.
        template_id: Template the destroyed transaction was linked to.
            Always non-NULL in scan output (the SQL filter requires it).
        pay_period_id: Pay period the destroyed transaction lived in.
        status_id: The settled status id (Paid/Received/Settled).
        status_name: Human-readable status, resolved against the
            current ``ref.statuses`` table.
        transaction_name: ``name`` column from ``old_data``.
        amount: Best-effort destroyed amount -- ``actual_amount`` if it
            was set on the destroyed row (the project's DC-01 invariant
            says settled transactions must have it), otherwise
            ``estimated_amount``.  Decimal, in dollars.
        user_id: Application user id captured by the audit trigger
            from ``app.current_user_id``.  ``None`` if the deletion
            happened outside an authenticated request context.
        db_user: PostgreSQL role that executed the DELETE.  Diagnostic
            only; the runtime ``shekel_app`` role is expected in
            production damage.
    """

    audit_id: int
    executed_at: datetime
    transaction_id: int
    template_id: int
    pay_period_id: int | None
    status_id: int
    status_name: str
    transaction_name: str
    amount: Decimal
    user_id: int | None
    db_user: str | None


# ── Scan core (pure, read-only) ──────────────────────────────────


# A SELECT-only statement.  No DML.  Parameters are bound, not
# string-interpolated (raw string SQL is unavoidable here because
# ``system.audit_log`` is a project-managed forensic table outside
# the ORM, but every value is fixed and there is no untrusted input).
#
# Filter logic mirrors the post-Commit-21 guard's semantics
# (``app/utils/archive_helpers.template_has_paid_history`` and
# ``app/routes/templates.hard_delete_template``):
#   * Settled status (``ref.statuses.is_settled = TRUE``) -- the
#     guard's predicate.
#   * ``template_id IS NOT NULL`` in the destroyed row -- only
#     template-linked transactions could be reached by the template
#     hard-delete path.  Single-transaction DELETE routes are out of
#     scope for CRIT-05 and would over-report if included.
#
# The JOIN to ``ref.statuses`` uses the current is_settled boolean;
# the project's seed has only ever marked Paid/Received/Settled as
# settled, so this is faithful to the historical truth as well.
_SCAN_SQL = """
SELECT
    al.id                                                AS audit_id,
    al.executed_at                                       AS executed_at,
    al.row_id                                            AS transaction_id,
    (al.old_data->>'template_id')::INTEGER               AS template_id,
    (al.old_data->>'pay_period_id')::INTEGER             AS pay_period_id,
    (al.old_data->>'status_id')::INTEGER                 AS status_id,
    s.name                                               AS status_name,
    al.old_data->>'name'                                 AS transaction_name,
    COALESCE(
        (al.old_data->>'actual_amount')::NUMERIC,
        (al.old_data->>'estimated_amount')::NUMERIC
    )                                                    AS amount,
    al.user_id                                           AS user_id,
    al.db_user                                           AS db_user
FROM system.audit_log al
JOIN ref.statuses s
  ON s.id = (al.old_data->>'status_id')::INTEGER
WHERE al.table_schema = 'budget'
  AND al.table_name   = 'transactions'
  AND al.operation    = 'DELETE'
  AND s.is_settled    = TRUE
  AND al.old_data ? 'template_id'
  AND al.old_data->>'template_id' IS NOT NULL
ORDER BY al.executed_at ASC, al.id ASC
"""


def scan_destroyed_received_history(session) -> list[DestroyedRow]:
    """Read ``system.audit_log`` for destroyed settled template history.

    Pure read-only.  Returns every audit_log DELETE row on
    ``budget.transactions`` whose destroyed row had a settled
    ``status_id`` (Paid/Received/Settled per current ``ref.statuses``)
    and a non-NULL ``template_id``.  Those are exactly the rows that
    the post-Commit-21 guard
    (``app/utils/archive_helpers.template_has_paid_history`` plus the
    defense-in-depth filter in ``app/routes/templates.hard_delete_template``)
    would refuse to destroy today.

    Args:
        session: SQLAlchemy session bound to the target database.

    Returns:
        A list of :class:`DestroyedRow` ordered by ``executed_at``
        ascending (oldest damage first), then by ``audit_id``.  Empty
        list when the audit trail records no such DELETE.
    """
    # Local import keeps ``sqlalchemy`` off the module-level import
    # graph -- this script ships with the deploy image and may be
    # exercised from environments where the app is not yet wired up.
    from sqlalchemy import text  # pylint: disable=import-outside-toplevel

    result = session.execute(text(_SCAN_SQL))
    rows: list[DestroyedRow] = []
    for raw in result.mappings():
        amount = raw["amount"]
        # PG NUMERIC arrives as Decimal already; guard against a
        # provider returning float (sqlite shim, mock driver) which
        # would silently break monetary equality.
        if not isinstance(amount, Decimal):
            raise TypeError(
                "scan_destroyed_received_history expected Decimal amount "
                f"from NUMERIC column, got {type(amount).__name__}: {amount!r}"
            )
        rows.append(DestroyedRow(
            audit_id=raw["audit_id"],
            executed_at=raw["executed_at"],
            transaction_id=raw["transaction_id"],
            template_id=raw["template_id"],
            pay_period_id=raw["pay_period_id"],
            status_id=raw["status_id"],
            status_name=raw["status_name"],
            transaction_name=raw["transaction_name"],
            amount=amount,
            user_id=raw["user_id"],
            db_user=raw["db_user"],
        ))
    return rows


# ── Report formatting ────────────────────────────────────────────


def summarise(rows: list[DestroyedRow]) -> dict[str, object]:
    """Aggregate per-status and per-template totals for the report.

    Args:
        rows: The destroyed-row list returned by
            :func:`scan_destroyed_received_history`.

    Returns:
        A dict with keys: ``total_count``, ``total_amount``,
        ``by_status`` (mapping status name to (count, sum)),
        ``affected_templates`` (sorted unique template ids).
        Sums are Decimals so the report formatter can render them
        with cent-level accuracy.
    """
    total_amount = Decimal("0.00")
    by_status: dict[str, tuple[int, Decimal]] = {}
    templates: set[int] = set()
    for row in rows:
        total_amount += row.amount
        prev = by_status.get(row.status_name, (0, Decimal("0.00")))
        by_status[row.status_name] = (prev[0] + 1, prev[1] + row.amount)
        templates.add(row.template_id)
    return {
        "total_count": len(rows),
        "total_amount": total_amount,
        "by_status": by_status,
        "affected_templates": sorted(templates),
    }


def format_report(rows: list[DestroyedRow], verbose: bool = False) -> str:
    """Render a human-readable report.

    Args:
        rows: Destroyed-row list (possibly empty).
        verbose: If True, include the per-row detail table.  Default
            output is the summary only, which is the form an operator
            triages with first.

    Returns:
        Multi-line string.  Empty-input case is one explicit line so
        the operator can distinguish "scanned, no damage" from
        "scan failed."
    """
    if not rows:
        return (
            "Pre-fix RECEIVED-history destruction scan (CRIT-05 / OPT-2)\n"
            "  Result: no settled template-linked transactions were "
            "destroyed prior to Commit 21.\n"
            "  Action: none required.\n"
        )

    summary = summarise(rows)
    lines: list[str] = []
    lines.append("Pre-fix RECEIVED-history destruction scan (CRIT-05 / OPT-2)")
    lines.append(f"  Total destroyed rows: {summary['total_count']}")
    lines.append(f"  Total destroyed amount: ${summary['total_amount']}")
    lines.append(
        f"  Affected templates: {len(summary['affected_templates'])} "
        f"(ids: {summary['affected_templates']})"
    )
    lines.append("  Breakdown by status:")
    for status_name, (count, subtotal) in sorted(summary["by_status"].items()):
        lines.append(
            f"    {status_name}: {count} row(s), ${subtotal}"
        )

    if verbose:
        lines.append("")
        lines.append("  Detail (oldest first):")
        lines.append(
            "    "
            "audit_id | executed_at | txn_id | template_id | period_id "
            "| status | name | amount | user_id | db_user"
        )
        for row in rows:
            lines.append(
                "    "
                f"{row.audit_id} | {row.executed_at.isoformat()} | "
                f"{row.transaction_id} | {row.template_id} | "
                f"{row.pay_period_id} | {row.status_name} | "
                f"{row.transaction_name!r} | ${row.amount} | "
                f"{row.user_id} | {row.db_user}"
            )

    lines.append("")
    lines.append(
        "  Action: review against point-in-time backups.  Audit log rows "
        "above identify the destroyed transactions but the row data "
        "cannot be restored from the application schema; the post-Commit-21 "
        "guard prevents future damage."
    )
    return "\n".join(lines) + "\n"


# ── CLI ──────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        ``argparse.Namespace`` with ``database_url`` (str or None)
        and ``verbose`` (bool).
    """
    parser = argparse.ArgumentParser(
        description=(
            "Read-only scan for settled template-linked transactions "
            "destroyed prior to Commit 21 (CRIT-05 / OPT-2)."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help=(
            "Override DATABASE_URL.  Secret is read from "
            "[set via environment variable]; the value is never echoed."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="List every destroyed row (default: summary only).",
    )
    return parser.parse_args(argv)


def run_cli(database_url: str | None = None, verbose: bool = False) -> int:
    """Build the app, run the scan, print the report, return exit code.

    Args:
        database_url: Optional DSN override.  When set, written to
            ``DATABASE_URL`` so the Flask config picks it up at
            ``create_app()`` time.  Useful for pointing the scan at a
            staging restore.
        verbose: Forwarded to :func:`format_report`.

    Returns:
        0 on successful execution.  3 on script / connection error.
    """
    if database_url is not None:
        os.environ["DATABASE_URL"] = database_url

    try:
        # Local imports: defer Flask wiring until the CLI is the
        # entry point, so the scan helpers above can be unit-tested
        # against a raw session without paying the app-factory cost.
        from app import create_app  # pylint: disable=import-outside-toplevel
        from app.extensions import db  # pylint: disable=import-outside-toplevel

        app = create_app()
        with app.app_context():
            rows = scan_destroyed_received_history(db.session)
            print(format_report(rows, verbose=verbose), end="")
            return 0
    except (ImportError, RuntimeError, OSError) as exc:
        logger.error("scan_destroyed_received_history failed: %s", exc)
        return 3


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = parse_args()
    sys.exit(run_cli(database_url=args.database_url, verbose=args.verbose))
