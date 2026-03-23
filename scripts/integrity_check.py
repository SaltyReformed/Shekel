"""
Shekel Budget App -- Data Integrity Check

Validates referential integrity, detects orphaned records, flags balance
anomalies, and checks data consistency across all database schemas.

Designed to be:
    - Run standalone via CLI: python scripts/integrity_check.py
    - Called from verify_backup.sh against a temporary database
    - Tested by pytest against the test database

Usage:
    python scripts/integrity_check.py [--database-url URL] [--verbose] [--category CAT]

Options:
    --database-url URL   Override the database URL (for verify_backup.sh)
    --verbose            Print details for each check, not just failures
    --category CAT       Run only checks in this category
                         (referential, orphan, balance, consistency)

Exit codes:
    0   All checks passed
    1   One or more CRITICAL checks failed
    2   One or more WARNING checks flagged issues (no critical failures)
    3   Script error (bad arguments, database connection failure)

Cron example (weekly, after backup verification):
    0 3 * * 0 docker exec shekel-prod-app python scripts/integrity_check.py
"""

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field

# Ensure the project root is on sys.path so 'app' is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


# ── Data Structures ──────────────────────────────────────────────


@dataclass
class CheckResult:
    """Result of a single integrity check.

    Attributes:
        check_id: Identifier like 'FK-01', 'OR-03', 'BA-02', 'DC-05'.
        category: One of 'referential', 'orphan', 'balance', 'consistency'.
        severity: 'critical' or 'warning'.
        description: Human-readable description of what was checked.
        passed: True if no issues found.
        detail_count: Number of violations found (0 if passed).
        details: List of dicts with violation specifics (e.g., row IDs).
    """

    check_id: str
    category: str
    severity: str
    description: str
    passed: bool
    detail_count: int = 0
    details: list = field(default_factory=list)


# ── Helper ───────────────────────────────────────────────────────


def _run_check(session, check_id, category, severity, description, sql):
    """Execute a single integrity check query and return a CheckResult.

    Args:
        session: SQLAlchemy session.
        check_id: Check identifier (e.g., 'FK-01').
        category: Check category name.
        severity: 'critical' or 'warning'.
        description: Human-readable check description.
        sql: SQL query that returns violating rows (empty = pass).

    Returns:
        CheckResult with pass/fail status and violation details.
    """
    from sqlalchemy import text  # pylint: disable=import-outside-toplevel

    result = session.execute(text(sql))
    rows = result.fetchall()
    columns = list(result.keys()) if rows else []
    details = [dict(zip(columns, row)) for row in rows]
    return CheckResult(
        check_id=check_id,
        category=category,
        severity=severity,
        description=description,
        passed=len(rows) == 0,
        detail_count=len(rows),
        details=details,
    )


# ── Category 1: Referential Integrity ────────────────────────────


def check_referential_integrity(session):
    """Run all FK-* referential integrity checks.

    Verifies that foreign key references point to existing rows.
    While PostgreSQL enforces FK constraints on write, data corruption,
    partial restores, or manual SQL operations could introduce violations.

    Args:
        session: SQLAlchemy session.

    Returns:
        List of CheckResult for checks FK-01 through FK-13.
    """
    checks = [
        ("FK-01", "Accounts without a valid user", """
            SELECT a.id, a.name, a.user_id
            FROM budget.accounts a
            LEFT JOIN auth.users u ON a.user_id = u.id
            WHERE u.id IS NULL
        """),
        ("FK-02", "Accounts with invalid account type", """
            SELECT a.id, a.name, a.account_type_id
            FROM budget.accounts a
            LEFT JOIN ref.account_types t ON a.account_type_id = t.id
            WHERE t.id IS NULL
        """),
        ("FK-03", "Accounts pointing to nonexistent anchor period", """
            SELECT a.id, a.name, a.current_anchor_period_id
            FROM budget.accounts a
            LEFT JOIN budget.pay_periods p ON a.current_anchor_period_id = p.id
            WHERE a.current_anchor_period_id IS NOT NULL
              AND p.id IS NULL
        """),
        ("FK-04", "Transactions referencing nonexistent templates", """
            SELECT t.id, t.name, t.template_id
            FROM budget.transactions t
            LEFT JOIN budget.transaction_templates tt ON t.template_id = tt.id
            WHERE t.template_id IS NOT NULL
              AND tt.id IS NULL
        """),
        ("FK-05", "Transactions in nonexistent pay periods", """
            SELECT t.id, t.name, t.pay_period_id
            FROM budget.transactions t
            LEFT JOIN budget.pay_periods p ON t.pay_period_id = p.id
            WHERE p.id IS NULL
        """),
        ("FK-06", "Transactions in nonexistent scenarios", """
            SELECT t.id, t.name, t.scenario_id
            FROM budget.transactions t
            LEFT JOIN budget.scenarios s ON t.scenario_id = s.id
            WHERE s.id IS NULL
        """),
        ("FK-07", "Transactions with invalid category", """
            SELECT t.id, t.name, t.category_id
            FROM budget.transactions t
            LEFT JOIN budget.categories c ON t.category_id = c.id
            WHERE t.category_id IS NOT NULL
              AND c.id IS NULL
        """),
        ("FK-08", "Transfers from nonexistent accounts", """
            SELECT tr.id, tr.name, tr.from_account_id
            FROM budget.transfers tr
            LEFT JOIN budget.accounts a ON tr.from_account_id = a.id
            WHERE a.id IS NULL
        """),
        ("FK-09", "Transfers to nonexistent accounts", """
            SELECT tr.id, tr.name, tr.to_account_id
            FROM budget.transfers tr
            LEFT JOIN budget.accounts a ON tr.to_account_id = a.id
            WHERE a.id IS NULL
        """),
        ("FK-10", "Templates with invalid category", """
            SELECT tt.id, tt.name, tt.category_id
            FROM budget.transaction_templates tt
            LEFT JOIN budget.categories c ON tt.category_id = c.id
            WHERE c.id IS NULL
        """),
        ("FK-11", "Templates for nonexistent accounts", """
            SELECT tt.id, tt.name, tt.account_id
            FROM budget.transaction_templates tt
            LEFT JOIN budget.accounts a ON tt.account_id = a.id
            WHERE a.id IS NULL
        """),
        ("FK-12", "Salary profiles in nonexistent scenarios", """
            SELECT sp.id, sp.name, sp.scenario_id
            FROM salary.salary_profiles sp
            LEFT JOIN budget.scenarios s ON sp.scenario_id = s.id
            WHERE s.id IS NULL
        """),
        ("FK-13", "Salary profiles linked to nonexistent templates", """
            SELECT sp.id, sp.name, sp.template_id
            FROM salary.salary_profiles sp
            LEFT JOIN budget.transaction_templates tt ON sp.template_id = tt.id
            WHERE sp.template_id IS NOT NULL
              AND tt.id IS NULL
        """),
    ]
    return [
        _run_check(session, cid, "referential", "critical", desc, sql)
        for cid, desc, sql in checks
    ]


# ── Category 2: Orphan Detection ─────────────────────────────────


def check_orphaned_records(session):
    """Run all OR-* orphan detection checks.

    Finds records that exist but are functionally disconnected from the
    data model.

    Args:
        session: SQLAlchemy session.

    Returns:
        List of CheckResult for checks OR-01 through OR-06.
    """
    checks = [
        ("OR-01", "Transaction templates with no recurrence rule and no transactions", """
            SELECT tt.id, tt.name
            FROM budget.transaction_templates tt
            LEFT JOIN budget.transactions t ON t.template_id = tt.id
            WHERE tt.recurrence_rule_id IS NULL
              AND t.id IS NULL
        """),
        ("OR-02", "Recurrence rules not referenced by any template", """
            SELECT r.id
            FROM budget.recurrence_rules r
            LEFT JOIN budget.transaction_templates tt ON tt.recurrence_rule_id = r.id
            LEFT JOIN budget.transfer_templates tft ON tft.recurrence_rule_id = r.id
            WHERE tt.id IS NULL
              AND tft.id IS NULL
        """),
        ("OR-03", "Categories not used by any template or transaction", """
            SELECT c.id, c.group_name, c.item_name
            FROM budget.categories c
            LEFT JOIN budget.transaction_templates tt ON tt.category_id = c.id
            LEFT JOIN budget.transactions t ON t.category_id = c.id
            WHERE tt.id IS NULL
              AND t.id IS NULL
        """),
        ("OR-04", "Pay periods with no transactions and no transfers", """
            SELECT pp.id, pp.user_id, pp.start_date
            FROM budget.pay_periods pp
            LEFT JOIN budget.transactions t ON t.pay_period_id = pp.id
            LEFT JOIN budget.transfers tr ON tr.pay_period_id = pp.id
            WHERE t.id IS NULL
              AND tr.id IS NULL
        """),
        ("OR-05", "Active transfer templates with no transfers generated", """
            SELECT tt.id, tt.name
            FROM budget.transfer_templates tt
            LEFT JOIN budget.transfers tr ON tr.transfer_template_id = tt.id
            WHERE tt.is_active = TRUE
              AND tr.id IS NULL
        """),
        ("OR-06", "Active savings goals for inactive accounts", """
            SELECT sg.id, sg.name, sg.account_id
            FROM budget.savings_goals sg
            JOIN budget.accounts a ON sg.account_id = a.id
            WHERE sg.is_active = TRUE
              AND a.is_active = FALSE
        """),
    ]
    return [
        _run_check(session, cid, "orphan", "warning", desc, sql)
        for cid, desc, sql in checks
    ]


# ── Category 3: Balance Anomalies ────────────────────────────────


def check_balance_anomalies(session):
    """Run all BA-* balance anomaly checks.

    Flags potential issues in the anchor balance and projection system.

    Args:
        session: SQLAlchemy session.

    Returns:
        List of CheckResult for checks BA-01 through BA-05.
    """
    checks = [
        ("BA-01", "Anchor balance set but no anchor period (or vice versa)", """
            SELECT a.id, a.name, a.current_anchor_balance, a.current_anchor_period_id
            FROM budget.accounts a
            WHERE (a.current_anchor_balance IS NOT NULL AND a.current_anchor_period_id IS NULL)
               OR (a.current_anchor_balance IS NULL AND a.current_anchor_period_id IS NOT NULL)
        """),
        ("BA-02", "Anchor period is beyond the last pay period for the user", """
            SELECT a.id, a.name, pp.period_index, max_pp.max_idx
            FROM budget.accounts a
            JOIN budget.pay_periods pp ON a.current_anchor_period_id = pp.id
            JOIN (
                SELECT user_id, MAX(period_index) AS max_idx
                FROM budget.pay_periods
                GROUP BY user_id
            ) max_pp ON a.user_id = max_pp.user_id
            WHERE pp.period_index > max_pp.max_idx
        """),
        ("BA-03", "Pay period sequence gaps (non-contiguous period_index)", """
            WITH numbered AS (
                SELECT user_id, period_index,
                       LAG(period_index) OVER (
                           PARTITION BY user_id ORDER BY period_index
                       ) AS prev_idx
                FROM budget.pay_periods
            )
            SELECT user_id, prev_idx, period_index
            FROM numbered
            WHERE prev_idx IS NOT NULL
              AND period_index - prev_idx > 1
        """),
        ("BA-04", "Pay period date overlap within the same user", """
            SELECT p1.id AS period_1_id, p2.id AS period_2_id,
                   p1.user_id, p1.start_date AS p1_start, p1.end_date AS p1_end,
                   p2.start_date AS p2_start
            FROM budget.pay_periods p1
            JOIN budget.pay_periods p2
              ON p1.user_id = p2.user_id
             AND p1.id < p2.id
             AND p2.start_date > p1.start_date
             AND p2.start_date < p1.end_date
        """),
        ("BA-05", "Large anchor balance jumps (>50% change between consecutive entries)", """
            WITH ordered AS (
                SELECT id, account_id, anchor_balance,
                       LAG(anchor_balance) OVER (
                           PARTITION BY account_id ORDER BY created_at
                       ) AS prev_balance
                FROM budget.account_anchor_history
            )
            SELECT id, account_id, prev_balance, anchor_balance
            FROM ordered
            WHERE prev_balance IS NOT NULL
              AND prev_balance != 0
              AND ABS(anchor_balance - prev_balance) / ABS(prev_balance) > 0.5
        """),
    ]
    return [
        _run_check(session, cid, "balance", "warning", desc, sql)
        for cid, desc, sql in checks
    ]


# ── Category 4: Data Consistency ─────────────────────────────────


def check_data_consistency(session):
    """Run all DC-* data consistency checks.

    Cross-table logical consistency validations.

    Args:
        session: SQLAlchemy session.

    Returns:
        List of CheckResult for checks DC-01 through DC-09.
    """
    from sqlalchemy import text  # pylint: disable=import-outside-toplevel

    results = []

    # DC-01: Done/received transactions without actual_amount (critical).
    results.append(_run_check(
        session, "DC-01", "consistency", "critical",
        "Transactions with status done/received but no actual_amount",
        """
        SELECT t.id, t.name, s.name AS status
        FROM budget.transactions t
        JOIN ref.statuses s ON t.status_id = s.id
        WHERE s.name IN ('done', 'received')
          AND t.actual_amount IS NULL
        """,
    ))

    # DC-02: Transfers where from_account equals to_account (warning).
    results.append(_run_check(
        session, "DC-02", "consistency", "warning",
        "Transfers where from_account equals to_account",
        """
        SELECT tr.id, tr.name, tr.from_account_id, tr.to_account_id
        FROM budget.transfers tr
        WHERE tr.from_account_id = tr.to_account_id
        """,
    ))

    # DC-03: Account type-specific params mismatch (warning).
    # Checks: HYSA accounts without hysa_params.
    results.append(_run_check(
        session, "DC-03", "consistency", "warning",
        "Typed accounts missing their type-specific params",
        """
        SELECT a.id, a.name, at.name AS type_name, at.category
        FROM budget.accounts a
        JOIN ref.account_types at ON a.account_type_id = at.id
        LEFT JOIN budget.hysa_params hp ON hp.account_id = a.id
        LEFT JOIN budget.mortgage_params mp ON mp.account_id = a.id
        LEFT JOIN budget.auto_loan_params alp ON alp.account_id = a.id
        WHERE (at.name = 'hysa' AND hp.id IS NULL)
           OR (at.name = 'mortgage' AND mp.id IS NULL)
           OR (at.name = 'auto_loan' AND alp.id IS NULL)
        """,
    ))

    # DC-04: Self-referential credit payback cycles (warning).
    # A chain longer than 1: A.credit_payback_for_id -> B.credit_payback_for_id -> C.
    results.append(_run_check(
        session, "DC-04", "consistency", "warning",
        "Credit payback chains longer than 1 level",
        """
        SELECT t1.id AS txn_id, t1.credit_payback_for_id AS pays_back,
               t2.credit_payback_for_id AS chain_pays_back
        FROM budget.transactions t1
        JOIN budget.transactions t2 ON t1.credit_payback_for_id = t2.id
        WHERE t2.credit_payback_for_id IS NOT NULL
        """,
    ))

    # DC-05: Active templates for inactive accounts (warning).
    results.append(_run_check(
        session, "DC-05", "consistency", "warning",
        "Active templates referencing inactive accounts",
        """
        SELECT tt.id, tt.name, tt.account_id, a.name AS account_name
        FROM budget.transaction_templates tt
        JOIN budget.accounts a ON tt.account_id = a.id
        WHERE tt.is_active = TRUE
          AND a.is_active = FALSE
        """,
    ))

    # DC-06: Duplicate non-deleted transactions per template/period/scenario (critical).
    results.append(_run_check(
        session, "DC-06", "consistency", "critical",
        "Duplicate non-deleted transactions per template/period/scenario",
        """
        SELECT template_id, pay_period_id, scenario_id, COUNT(*) AS cnt
        FROM budget.transactions
        WHERE template_id IS NOT NULL
          AND is_deleted = FALSE
        GROUP BY template_id, pay_period_id, scenario_id
        HAVING COUNT(*) > 1
        """,
    ))

    # DC-07: Users without user_settings (critical).
    results.append(_run_check(
        session, "DC-07", "consistency", "critical",
        "Users without a user_settings row",
        """
        SELECT u.id, u.email
        FROM auth.users u
        LEFT JOIN auth.user_settings s ON u.id = s.user_id
        WHERE s.id IS NULL
        """,
    ))

    # DC-08: Users without a baseline scenario (critical).
    results.append(_run_check(
        session, "DC-08", "consistency", "critical",
        "Users without a baseline scenario",
        """
        SELECT u.id, u.email
        FROM auth.users u
        LEFT JOIN budget.scenarios s
          ON u.id = s.user_id AND s.is_baseline = TRUE
        WHERE s.id IS NULL
        """,
    ))

    # DC-09: Salary deduction target accounts belonging to a different user (warning).
    results.append(_run_check(
        session, "DC-09", "consistency", "warning",
        "Salary deductions targeting another user's account",
        """
        SELECT pd.id, pd.name AS deduction_name,
               sp.user_id AS profile_user, a.user_id AS account_user
        FROM salary.paycheck_deductions pd
        JOIN salary.salary_profiles sp ON pd.salary_profile_id = sp.id
        JOIN budget.accounts a ON pd.target_account_id = a.id
        WHERE pd.target_account_id IS NOT NULL
          AND sp.user_id != a.user_id
        """,
    ))

    return results


# ── Orchestration ─────────────────────────────────────────────────


# Map category names to their check functions.
CATEGORY_FUNCTIONS = {
    "referential": check_referential_integrity,
    "orphan": check_orphaned_records,
    "balance": check_balance_anomalies,
    "consistency": check_data_consistency,
}


def run_all_checks(session, categories=None, verbose=False):
    """Execute all integrity checks against the given database session.

    Args:
        session: A SQLAlchemy session connected to the target database.
        categories: Optional list of category names to filter checks.
            Valid values: 'referential', 'orphan', 'balance', 'consistency'.
            If None, all categories are run.
        verbose: If True, log details for passing checks too.

    Returns:
        List of CheckResult objects, one per check executed.
    """
    all_results = []
    target_categories = categories or list(CATEGORY_FUNCTIONS.keys())

    for cat_name in target_categories:
        check_fn = CATEGORY_FUNCTIONS.get(cat_name)
        if check_fn is None:
            logger.warning("Unknown category: %s (skipping)", cat_name)
            continue

        results = check_fn(session)
        for result in results:
            if result.passed:
                if verbose:
                    logger.info(
                        "[PASS] %s: %s", result.check_id, result.description
                    )
            else:
                level = "ERROR" if result.severity == "critical" else "WARNING"
                logger.log(
                    logging.ERROR if result.severity == "critical" else logging.WARNING,
                    "[FAIL] %s: %s (%d violation(s))",
                    result.check_id, result.description, result.detail_count,
                )
                if verbose and result.details:
                    for detail in result.details[:10]:
                        logger.info("       %s", detail)
                    if result.detail_count > 10:
                        logger.info(
                            "       ... and %d more",
                            result.detail_count - 10,
                        )

        all_results.extend(results)

    return all_results


def summarize_results(results):
    """Log a summary of all check results.

    Args:
        results: List of CheckResult objects.

    Returns:
        Tuple of (critical_failures, warning_failures) counts.
    """
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    critical = sum(1 for r in results if not r.passed and r.severity == "critical")
    warnings = sum(1 for r in results if not r.passed and r.severity == "warning")

    logger.info("=" * 50)
    logger.info("  INTEGRITY CHECK SUMMARY")
    logger.info("=" * 50)
    logger.info("  Total checks:  %d", total)
    logger.info("  Passed:        %d", passed)
    logger.info("  Critical:      %d", critical)
    logger.info("  Warnings:      %d", warnings)

    if critical == 0 and warnings == 0:
        logger.info("  Status:        ALL PASSED")
    elif critical > 0:
        logger.error("  Status:        CRITICAL FAILURES")
    else:
        logger.warning("  Status:        WARNINGS ONLY")

    logger.info("=" * 50)
    return critical, warnings


# ── CLI ───────────────────────────────────────────────────────────


def parse_args(argv=None):
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        argparse.Namespace with database_url, verbose, and category.
    """
    parser = argparse.ArgumentParser(
        description="Validate Shekel database integrity."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override DATABASE_URL (for verify_backup.sh).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print details for each check, not just failures.",
    )
    parser.add_argument(
        "--category",
        choices=list(CATEGORY_FUNCTIONS.keys()),
        default=None,
        help="Run only checks in this category.",
    )
    return parser.parse_args(argv)


def run_cli(database_url=None, categories=None, verbose=False):
    """CLI entry point: create app, run checks, print results, exit.

    If database_url is provided, it overrides the Flask config.
    This allows verify_backup.sh to point at a temporary database.

    Args:
        database_url: Optional override for DATABASE_URL.
        categories: Optional list of categories to check.
        verbose: Print details for passing checks.

    Returns:
        Exit code (0, 1, 2, or 3).
    """
    if database_url:
        os.environ["DATABASE_URL"] = database_url

    try:
        from app import create_app  # pylint: disable=import-outside-toplevel
        from app.extensions import db  # pylint: disable=import-outside-toplevel

        app = create_app()
        with app.app_context():
            results = run_all_checks(db.session, categories, verbose)
            critical, warnings = summarize_results(results)

            if critical > 0:
                return 1
            if warnings > 0:
                return 2
            return 0
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Integrity check failed: %s", exc)
        return 3


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = parse_args()
    code = run_cli(
        database_url=args.database_url,
        categories=[args.category] if args.category else None,
        verbose=args.verbose,
    )
    sys.exit(code)
