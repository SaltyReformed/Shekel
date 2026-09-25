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

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

# Ensure the project root is on sys.path so 'app' and 'scripts' are importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pylint: wrong-import-position -- this import must follow the sys.path
# bootstrap above: the repo root is not on the path when the script is
# invoked as ``python scripts/integrity_check.py``.
from scripts._script_lib import (  # pylint: disable=wrong-import-position
    run_in_app_context,
    setup_script_logging,
)

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


@dataclass(frozen=True)
class CheckSpec:
    """Declarative definition of one integrity check.

    The identity fields mirror :class:`CheckResult`; ``sql`` is the
    violation query. One spec is one row in a category's check catalog.

    Attributes:
        check_id: Identifier like 'FK-01', 'OR-03', 'BA-02', 'DC-05'.
        category: One of 'referential', 'orphan', 'balance', 'consistency'.
        severity: 'critical' or 'warning'.
        description: Human-readable description of what is checked.
        sql: SQL query that returns violating rows (empty = pass).
    """

    check_id: str
    category: str
    severity: str
    description: str
    sql: str


# ── Helper ───────────────────────────────────────────────────────


def _run_check(session, spec: CheckSpec) -> CheckResult:
    """Execute a single integrity check query and return a CheckResult.

    Args:
        session: SQLAlchemy session.
        spec: The check definition: identity fields plus the SQL query
            whose result rows are the violations.

    Returns:
        CheckResult with pass/fail status and violation details.
    """
    result = session.execute(text(spec.sql))
    rows = result.fetchall()
    columns = list(result.keys()) if rows else []
    details = [dict(zip(columns, row)) for row in rows]
    return CheckResult(
        check_id=spec.check_id,
        category=spec.category,
        severity=spec.severity,
        description=spec.description,
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
        # FK-03 was "accounts pointing to a nonexistent anchor period" until
        # plan step X-f1c3c (ruling R-EH) deleted
        # ``accounts.current_anchor_period_id``.  An account references no pay
        # period now, so the dangling-reference class it looked for cannot
        # exist; the check is deleted rather than answered vacuously.  What
        # replaces it in spirit is BA-01 below: an account with no balance
        # ASSERTION, which is the state that actually breaks a producer.
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
        _run_check(session, CheckSpec(cid, "referential", "critical", desc, sql))
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
        List of CheckResult for the live OR-* checks (OR-01 and OR-03 through
        OR-06; OR-02 retired at plan step R-F6, see below).
    """
    checks = [
        # **OR-02 is DELETED, not renumbered** (plan step R-F6).  It scanned
        # for recurrence rules no template referenced -- finding **F-6**, three
        # rows on production -- and that state stopped being expressible when
        # the owning FK moved onto ``budget.recurrence_rules`` under
        # ``ck_recurrence_rules_one_owner``: a rule without an owner is refused
        # by the database, and a rule whose owner is deleted goes with it
        # (``ON DELETE CASCADE``).  A checker for a state the schema forbids
        # reports on the constraint's behalf and can only ever say zero.  The
        # id is left retired rather than reused so an old report's OR-02 still
        # means what it meant.
        ("OR-01", "Transaction templates with no recurrence rule and no transactions", """
            SELECT tt.id, tt.name
            FROM budget.transaction_templates tt
            LEFT JOIN budget.transactions t ON t.template_id = tt.id
            LEFT JOIN budget.recurrence_rules r ON r.transaction_template_id = tt.id
            WHERE r.id IS NULL
              AND t.id IS NULL
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
        _run_check(session, CheckSpec(cid, "orphan", "warning", desc, sql))
        for cid, desc, sql in checks
    ]


# ── Category 3: Balance Anomalies ────────────────────────────────


def check_balance_anomalies(session):
    """Run all BA-* balance anomaly checks.

    Flags potential issues in the anchor balance and projection system.

    Args:
        session: SQLAlchemy session.

    **Severity is per CHECK, not per family** (plan step X-f1c3c).  It was one
    ``"warning"`` constant applied to the whole list, which was true of every
    member while they were all anchor-cache smells; re-pointing BA-01 at "this
    account has no balance assertion at all" made it false, because that state
    makes ``cash_ledger.resolve_anchor`` raise on every page that renders the
    account.  A family constant silently downgraded it, so the sweep exited 2
    and ``verify_backup.sh`` logged a WARNING for a broken restore.  The
    per-check form is the one ``check_data_consistency`` already uses, so this
    is the file's own established shape rather than a new one.

    **THREE members were deleted at plan step ``pay_calendar:C4-c``, and none of
    them was replaced.**  BA-03 (an ordinal GAP), BA-04 (a date OVERLAP) and
    BA-07 (a day covered by no pay period) were three faces of one defect --
    ``budget.pay_periods`` storing ``end_date`` and ``period_index`` beside the
    paydays they derive from, with nothing reconciling them.  That step dropped
    both columns, so a period's ordinal is its position in payday order and its
    end is the day before the next payday: an ordinal cannot gap, two periods
    derived from distinct sorted paydays cannot overlap, and consecutive
    intervals leave no uncovered day between them.  A query for a state the
    schema cannot express is not a check, it is a query that always returns
    nothing.

    **What BA-07 detected, said precisely, because an adversarial review of
    C4-c found this paragraph claiming more than it should** (2026-09-01).  Its
    subject was a stored ``end_date`` FALLING SHORT of the next payday -- a
    hole between two adjacent periods.  It was never a detector for IRREGULAR
    PAYDAY SPACING: on any schedule the writer produced, the stored end already
    equalled ``lead(start) - 1``, so a payday six months after its predecessor
    passed BA-07 before the drop exactly as it passes now.  That state is still
    constructible (``/pay-periods/generate`` accepts any payday on or after the
    floor) and still unobserved by this sweep, where it presents as one
    over-long paycheck rather than as an absent day.  It is recorded as a
    finding rather than closed here silently: seeing it needs a NEW predicate
    (``next_payday - payday > cadence_days``), which is a check this sweep has
    never had and not one C4-c removed.

    Returns:
        List of CheckResult for the surviving balance/anchor checks
        (BA-01 critical; BA-05, BA-06 warnings.  BA-02 was deleted with the
        anchor cache columns at plan step X-f1c3c; BA-03, BA-04 and BA-07 with
        the derived columns at ``pay_calendar:C4-c``).
    """
    checks = [
        # BA-01 and BA-02 both keyed on ``accounts.current_anchor_*``, deleted
        # at plan step X-f1c3c (ruling R-EH): BA-01 looked for one of the pair
        # set without the other, and BA-02 for an anchor period past the end of
        # the user's schedule.  Neither state is expressible now.  BA-01 is
        # RE-POINTED at the invariant those columns existed to serve -- every
        # account carries at least one balance ASSERTION (E-19 / Commit 3) --
        # because an account the resolver cannot answer for is the state that
        # actually breaks every producer downstream.  BA-02 is deleted with no
        # replacement: an assertion carries a DAY, and a day outside the
        # schedule is legitimate (money moved before you started budgeting).
        # CRITICAL, unlike its siblings: an account with no assertion is not an
        # anomaly to look at later, it is an account ``resolve_anchor`` raises
        # for -- the balance engine has no starting point, so every producer
        # downstream of it fails.  BA-05 and BA-06 flag states worth a human's
        # attention that still render.
        # BA-01 asks about the OWNER's assertions: since plan step
        # balance:X-bj-1 the same table holds the bank's statement placements,
        # and ``resolve_anchor`` reads the owner's rows alone until the flip
        # (``balance_predicates.owner_declared_clause``; this raw SQL is that
        # predicate's one second spelling, named there).  An account whose
        # only level is a bank's is an account the resolver still raises for.
        ("BA-01", "critical", "Accounts with no balance assertion at all", """
            SELECT a.id, a.name
            FROM budget.accounts a
            LEFT JOIN budget.account_anchor_history h
                   ON h.account_id = a.id
                  AND h.statement_import_id IS NULL
            WHERE h.id IS NULL
        """),
        # BA-06 is a CHECK and deliberately not a refusal or a log line
        # (developer ruling 2026-08-11, which deleted pay_calendar C3-b's
        # coverage rule).  That rule refused any schedule write leaving a
        # settled row's cash day outside every paycheck, on the claim that it
        # breaks ruling R-K's reconciliation identity -- and it does not: each
        # column is valued at its OWN ``end_date``, so the day is absent from
        # both sides and reports as the ``period_timing`` remainder.  Nothing
        # is WRONG here, which is why this is a warning rather than a gate.
        #
        # It lives here rather than in the writer for the reason the arc keeps
        # finding: the condition is DERIVABLE from the schedule and the row's
        # own settle day, so recording it at write time would store a computed
        # claim beside no reconciler -- the same defect ``pay_calendar:C4-c``
        # removed by dropping ``end_date`` -- and it would go stale on the next
        # write.  Asked as a query it is always current, covers every owner,
        # and reports the state however it arose, including from data no writer
        # produced.
        #
        # **The predicate became a RANGE test at C4-c, and the collapse is the
        # normalization rather than a rewrite.**  It used to ask
        # ``NOT EXISTS (a period whose stored span contains the day)``, because
        # a stored ``end_date`` could fall short of the next payday and leave an
        # interior hole a settle day could land in.  Derived, the periods TILE:
        # each ends the day before the next opens, so the only days outside
        # every paycheck are the ones before the first payday and after the
        # horizon.  Two comparisons say that, and BA-07 -- which existed to see
        # the interior hole this sub-select was also catching -- is deleted.
        #
        # The horizon is ``MAX(start_date) + (cadence_days - 1)``, which is the
        # derivation's own projected end for the last period.  The cadence is
        # the LATEST ERA's since plan step ``pay_calendar:C17-a``: the
        # schedule row no longer carries one, and the last period's end reads
        # the latest era exactly as ``pay_schedule_service.ScheduleFacts.rhythm``
        # does.  An owner who holds a payday holds an era -- every batch that
        # records one mints an era when none covers it, and the C17-a
        # migration backfills one per owner -- so the join drops nobody.
        # *This SQL restates the derivation's end rule a second time, and the
        # arithmetic form here ignores the payday convention; that it should
        # be DELETED rather than made exact is the fork ledger row PC-501
        # carries for the developer, and re-pointing the join is not a ruling
        # on it.*
        #
        # Soft-deleted rows are excluded: they contribute to no figure on any
        # surface.  An owner with NO periods is excluded by the join rather
        # than reported as one giant violation.
        ("BA-06", "warning",
         "Settled transactions whose settle day no pay period covers", """
            SELECT t.id AS transaction_id, p.user_id, t.settled_on,
                   sched.first_day, sched.last_day
            FROM budget.transactions t
            JOIN budget.pay_periods p ON p.id = t.pay_period_id
            JOIN ref.statuses s ON s.id = t.status_id
            JOIN (
                SELECT pp.user_id,
                       MIN(pp.start_date) AS first_day,
                       MAX(pp.start_date) + (era.cadence_days - 1) AS last_day
                FROM budget.pay_periods pp
                JOIN (
                    SELECT DISTINCT ON (user_id) user_id, cadence_days
                    FROM budget.pay_eras
                    ORDER BY user_id, effective_from DESC
                ) era ON era.user_id = pp.user_id
                GROUP BY pp.user_id, era.cadence_days
            ) sched ON sched.user_id = p.user_id
            WHERE t.is_deleted = FALSE
              AND s.is_settled = TRUE
              AND t.settled_on IS NOT NULL
              AND (t.settled_on < sched.first_day
                   OR t.settled_on > sched.last_day)
        """),
        ("BA-05", "warning",
         "Large anchor balance jumps (>50% change between consecutive entries)",
         """
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
        _run_check(session, CheckSpec(cid, "balance", severity, desc, sql))
        for cid, severity, desc, sql in checks
    ]


# ── Category 4: Data Consistency ─────────────────────────────────


def check_data_consistency(session):
    """Run all DC-* data consistency checks.

    Cross-table logical consistency validations.

    Args:
        session: SQLAlchemy session.

    Returns:
        List of CheckResult for checks DC-02 through DC-12.

    Note:
        DC-01 ("done/received transactions without actual_amount") was
        removed 2026-06-11: settling without a manual actual is a
        designed, documented state (``MarkDoneSchema`` deliberately
        leaves the column untouched and ``Transaction.effective_amount``
        falls back to ``estimated_amount``), so the check flagged
        routine legal data on every prod run.  The remaining IDs keep
        their historical numbers so past run logs stay comparable.
    """
    results = []

    # DC-02: Transfers where from_account equals to_account (warning).
    results.append(_run_check(session, CheckSpec(
        "DC-02", "consistency", "warning",
        "Transfers where from_account equals to_account",
        """
        SELECT tr.id, tr.name, tr.from_account_id, tr.to_account_id
        FROM budget.transfers tr
        WHERE tr.from_account_id = tr.to_account_id
        """,
    )))

    # DC-03: Account type-specific params mismatch (warning).
    # Checks: interest-bearing accounts without interest_params.
    results.append(_run_check(session, CheckSpec(
        "DC-03", "consistency", "warning",
        "Typed accounts missing their type-specific params",
        """
        SELECT a.id, a.name, at.name AS type_name, atc.name AS category_name
        FROM budget.accounts a
        JOIN ref.account_types at ON a.account_type_id = at.id
        JOIN ref.account_type_categories atc ON at.category_id = atc.id
        LEFT JOIN budget.interest_params hp ON hp.account_id = a.id
        LEFT JOIN budget.loan_params lp ON lp.account_id = a.id
        WHERE (at.name = 'HYSA' AND hp.id IS NULL)
           OR (at.has_amortization = TRUE AND lp.id IS NULL)
        """,
    )))

    # DC-04: Self-referential credit payback cycles (warning).
    # A chain longer than 1: A.credit_payback_for_id -> B.credit_payback_for_id -> C.
    results.append(_run_check(session, CheckSpec(
        "DC-04", "consistency", "warning",
        "Credit payback chains longer than 1 level",
        """
        SELECT t1.id AS txn_id, t1.credit_payback_for_id AS pays_back,
               t2.credit_payback_for_id AS chain_pays_back
        FROM budget.transactions t1
        JOIN budget.transactions t2 ON t1.credit_payback_for_id = t2.id
        WHERE t2.credit_payback_for_id IS NOT NULL
        """,
    )))

    # DC-05: Active templates for inactive accounts (warning).
    results.append(_run_check(session, CheckSpec(
        "DC-05", "consistency", "warning",
        "Active templates referencing inactive accounts",
        """
        SELECT tt.id, tt.name, tt.account_id, a.name AS account_name
        FROM budget.transaction_templates tt
        JOIN budget.accounts a ON tt.account_id = a.id
        WHERE tt.is_active = TRUE
          AND a.is_active = FALSE
        """,
    )))

    # DC-06: Two generated rows answering ONE occurrence (critical).
    # The predicate mirrors the schema's own uniqueness contract, and plan
    # step **R17** re-keyed that contract off the paycheck and onto the
    # occurrence: a row answers one occurrence of its template's cadence, and
    # the pay period is only where that occurrence's money lands.  Asking the
    # old question here would report a CORRECT state as critical corruption --
    # a cadence that names one paycheck twice (a monthly bill at a pay cadence
    # of 30 days or more) legitimately stores two rows there, which is exactly
    # what the re-key made possible.
    #
    # FOUR arms: two contracts across two tables.  ``budget.transfers`` carries
    # the identical pair of indexes and the identical duplicate-money failure,
    # and it went ungraded here until plan step R17 -- which is the step that
    # makes "two rows in one paycheck" a LEGAL state, so the check that says
    # which pairs are legal now has to exist on both tables.  The ``source``
    # column names which one reported.
    #
    # Two contracts, because a row that answers an
    # occurrence is unique on it, and a row that answers NONE
    # (``occurs_on IS NULL`` -- a carry-forward roll-forward, a one-time
    # transfer) still holds its paycheck alone.  Both stay unique only WHERE
    # ``is_override = FALSE``: an override sibling legally coexists with the
    # rule-generated row for its target period.
    results.append(_run_check(session, CheckSpec(
        "DC-06", "consistency", "critical",
        "Two generated rows answering one occurrence (or one paycheck, undated)",
        """
        SELECT 'transactions' AS source, template_id, scenario_id, occurs_on,
               NULL::integer AS pay_period_id, COUNT(*) AS cnt
        FROM budget.transactions
        WHERE template_id IS NOT NULL
          AND occurs_on IS NOT NULL
          AND is_deleted = FALSE
          AND is_override = FALSE
        GROUP BY template_id, scenario_id, occurs_on
        HAVING COUNT(*) > 1
        UNION ALL
        SELECT 'transactions', template_id, scenario_id, NULL::date,
               pay_period_id, COUNT(*)
        FROM budget.transactions
        WHERE template_id IS NOT NULL
          AND occurs_on IS NULL
          AND is_deleted = FALSE
          AND is_override = FALSE
        GROUP BY template_id, scenario_id, pay_period_id
        HAVING COUNT(*) > 1
        UNION ALL
        SELECT 'transfers', transfer_template_id, scenario_id, occurs_on,
               NULL::integer, COUNT(*)
        FROM budget.transfers
        WHERE transfer_template_id IS NOT NULL
          AND occurs_on IS NOT NULL
          AND is_deleted = FALSE
          AND is_override = FALSE
        GROUP BY transfer_template_id, scenario_id, occurs_on
        HAVING COUNT(*) > 1
        UNION ALL
        SELECT 'transfers', transfer_template_id, scenario_id, NULL::date,
               pay_period_id, COUNT(*)
        FROM budget.transfers
        WHERE transfer_template_id IS NOT NULL
          AND occurs_on IS NULL
          AND is_deleted = FALSE
          AND is_override = FALSE
        GROUP BY transfer_template_id, scenario_id, pay_period_id
        HAVING COUNT(*) > 1
        """,
    )))

    # DC-07: Users without user_settings (critical).
    results.append(_run_check(session, CheckSpec(
        "DC-07", "consistency", "critical",
        "Users without a user_settings row",
        """
        SELECT u.id, u.email
        FROM auth.users u
        LEFT JOIN auth.user_settings s ON u.id = s.user_id
        WHERE s.id IS NULL
        """,
    )))

    # DC-08: Users without a baseline scenario (critical).
    # Companion-role users are excluded: a companion views the linked
    # owner's data and owns no budget rows of their own (no accounts,
    # no periods, no scenarios) by design, so "no baseline scenario"
    # is their correct steady state, not a defect.
    results.append(_run_check(session, CheckSpec(
        "DC-08", "consistency", "critical",
        "Users without a baseline scenario",
        """
        SELECT u.id, u.email
        FROM auth.users u
        JOIN ref.user_roles r ON u.role_id = r.id
        LEFT JOIN budget.scenarios s
          ON u.id = s.user_id AND s.is_baseline = TRUE
        WHERE s.id IS NULL
          AND r.name != 'companion'
        """,
    )))

    # DC-09: Salary deduction target accounts belonging to a different user (warning).
    results.append(_run_check(session, CheckSpec(
        "DC-09", "consistency", "warning",
        "Salary deductions targeting another user's account",
        """
        SELECT pd.id, pd.name AS deduction_name,
               sp.user_id AS profile_user, a.user_id AS account_user
        FROM salary.paycheck_lines pd
        JOIN salary.salary_profiles sp ON pd.salary_profile_id = sp.id
        JOIN budget.accounts a ON pd.target_account_id = a.id
        WHERE pd.target_account_id IS NOT NULL
          AND sp.user_id != a.user_id
        """,
    )))

    # DC-10: An UN-DATED movement holding a live journal leg (critical).
    #
    # ``_posting_purchases.purchase_posts`` is the write side's one statement
    # of "this movement is in the ledger": a contributing parent, a debit, a
    # RECORDED posting day, and for a transfer leg its record.  So a movement
    # with no ``settled_on`` owes the ledger nothing, and a non-zero net of
    # postings linked to it is money booked for a day nobody has stated.
    # Reachable since plan step ``balance:X-bi-3e-2``, when a revert began
    # KEEPING the status seam's covering movement un-dated (ruling **R-BAL61**):
    # the seam releases the day and the DOOR's family reconcile reverses the legs
    # (``transaction_service.apply_requested_status`` ->
    # ``posting_service.sync_transaction_postings``), so a caller that
    # reached the bare seam and never reconciled would leave exactly this
    # state -- and this arm grades it without depending on that door.  Over
    # EVERY un-dated entry, not the seam's alone: a purchase whose day was
    # cleared through ``entry_service.update_entry`` reconciles through the
    # same family walk (``_doors._resync_after_entry_change`` ->
    # ``sync_transaction_postings``) and owes the same zero.  Net per ledger
    # account: a reversed leg appears with its reversal and nets to zero, so
    # a fully-reversed movement does not report.
    results.append(_run_check(session, CheckSpec(
        "DC-10", "consistency", "critical",
        "Un-dated movements (no settled_on) holding a live journal leg",
        """
        SELECT e.id AS entry_id, e.transaction_id, e.covers_settlement,
               p.ledger_account_id, SUM(p.amount) AS net
        FROM budget.transaction_entries e
        JOIN budget.journal_entries je ON je.transaction_entry_id = e.id
        JOIN budget.account_postings p ON p.journal_entry_id = je.id
        WHERE e.settled_on IS NULL
        GROUP BY e.id, e.transaction_id, e.covers_settlement,
                 p.ledger_account_id
        HAVING SUM(p.amount) <> 0
        ORDER BY e.id, p.ledger_account_id
        """,
    )))

    # DC-11: A settled row whose money no reader can see (critical).
    #
    # Since plan step ``balance:X-bi-4a`` the cash fold and the posting
    # writer read a settled row's money as its MOVEMENTS and nothing of the
    # row (ruling **R-BAL80**), and the settled stream admits a movement by
    # ITS OWN ``settled_on``.  So two states are money the balance silently
    # omits: a row in the settled band with NO settle day, and a covering
    # movement that exists but carries NO ``settled_on`` under a settled row
    # -- the fold's real input, which the row's own day does not stand in
    # for.  The first is the state the cash walk REFUSED loudly through
    # ``X-bi-3e`` (``balance_predicates.settled_day`` raised on a dateless
    # settled row the fold read) and can no longer meet.  Neither is a
    # door's: the seam writes the status, the day and the movement in one
    # act and dates the movement on the row's day, and the cutover migration
    # ``ad573b07bede`` refused a dateless row and covered every other.  (A
    # third arm -- a stored non-zero figure with no covering movement --
    # graded the row's own ``settled_amount`` against the movement through
    # plan step ``balance:X-bi-4b-1``; the column went at ``X-bi-4b-2``,
    # migration ``45f10b870c8b``, which refused any row where the two
    # disagreed, and a settled row with no movement is the ``$0.00`` record
    # since, ruling **R-BAL82**.)
    # A settled TRANSFER's money is graded as its LEGS since leaf ``X-bi-6-4a``
    # (ruling **R-BAL106**; the UNION's second arm, one row per undated leg,
    # its ``sh`` join a second spelling ``X-bi-6-4d`` must move).  A SHADOW's
    # own missing day stays on the row arm until 6-4d; since ``X-bi-6-4b`` the
    # loan readers ask the leg's record instead (``loan_ledger._visible``).
    results.append(_run_check(session, CheckSpec(
        "DC-11", "consistency", "critical",
        "Settled rows the fold cannot see: no settle day, or a covering "
        "movement with no day; settled transfers holding an undated leg",
        """
        SELECT t.id AS transaction_id, NULL::integer AS transfer_id,
               t.account_id, s.name AS status, t.settled_on,
               (SELECT COUNT(*) FROM budget.transaction_entries e
                 WHERE e.transaction_id = t.id AND e.covers_settlement)
                 AS covering_movements,
               (SELECT COUNT(*) FROM budget.transaction_entries e
                 WHERE e.transaction_id = t.id AND e.covers_settlement
                   AND e.settled_on IS NULL)
                 AS undated_covering_movements
        FROM budget.transactions t
        JOIN ref.statuses s ON s.id = t.status_id
        WHERE s.is_settled AND NOT t.is_deleted
          AND (
            t.settled_on IS NULL
            OR t.transfer_id IS NULL AND EXISTS (
              SELECT 1 FROM budget.transaction_entries e
              WHERE e.transaction_id = t.id AND e.covers_settlement
                AND e.settled_on IS NULL
            )
          )
        UNION ALL
        SELECT NULL::integer, x.id, e.account_id, s.name, NULL::date, 1, 1
        FROM budget.transfers x
        JOIN ref.statuses s ON s.id = x.status_id
        JOIN budget.transactions sh ON sh.transfer_id = x.id AND NOT sh.is_deleted
        JOIN budget.transaction_entries e
          ON e.transaction_id = sh.id AND e.covers_settlement
        WHERE s.is_settled AND NOT x.is_deleted AND e.settled_on IS NULL
        ORDER BY transaction_id NULLS LAST, transfer_id, account_id
        """,
    )))

    # DC-12: A live transfer whose live shadows number other than two
    # (critical).
    #
    # Transfer Invariant 1: every transfer has exactly two linked shadow
    # rows, one expense and one income.  Through plan step ``balance:X-bi-6-3``
    # the posting writer was the app's only DETECTOR of a broken pair: it
    # read the INCOME shadow's record to book the pair as one entry and
    # refused when that shadow was missing, and the deploy resync warned
    # about such pairs.  Under ruling **R-BAL45**'s shape C each side's
    # covering movement posts on its own against the owner's
    # Transfers-in-transit account (ruling **R-BAL101**), so a pair with one
    # side is the honest in-transit state to the writer -- money left one
    # account and has not arrived -- and no door polices the count.  Which is
    # right for the writer and wrong for the app as a whole: a transfer whose
    # income shadow vanished would leave its cash sitting in transit with
    # nothing to arrive, visible nowhere.  So the invariant lives here, where
    # the states that are nobody's door to police already live (DC-10, DC-11),
    # read by the operator's integrity run and never by the writer.  Developer
    # ruling 2026-09-21 (the leaf's adversarial review, finding 2).  Counts
    # LIVE shadows of LIVE transfers: a soft-deleted pair carries its flag on
    # all three rows, and a hard-deleted transfer takes its shadows with it
    # (CASCADE).  Dies with the shadow rows at ``X-bi-6-5``.
    results.append(_run_check(session, CheckSpec(
        "DC-12", "consistency", "critical",
        "Live transfers whose live shadow rows number other than two",
        """
        SELECT t.id AS transfer_id, t.user_id, t.from_account_id,
               t.to_account_id,
               (SELECT COUNT(*) FROM budget.transactions sh
                 WHERE sh.transfer_id = t.id AND NOT sh.is_deleted)
                 AS live_shadows
        FROM budget.transfers t
        WHERE NOT t.is_deleted
          AND (SELECT COUNT(*) FROM budget.transactions sh
                WHERE sh.transfer_id = t.id AND NOT sh.is_deleted) <> 2
        ORDER BY t.id
        """,
    )))

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


def run_cli(
    database_url: str | None = None,
    categories: list[str] | None = None,
    verbose: bool = False,
) -> int:
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
    def _check_and_summarize(session) -> tuple[int, int]:
        """Run the requested checks and return (critical, warning) counts."""
        results = run_all_checks(session, categories, verbose)
        return summarize_results(results)

    try:
        critical, warnings = run_in_app_context(
            _check_and_summarize, database_url=database_url
        )
    # The exit-code-3 contract covers script errors: bad configuration
    # (ValueError from create_app / config validation), a missing or
    # broken app package (ImportError), Flask context problems
    # (RuntimeError), filesystem/socket failures (OSError), and any
    # database connection or query failure (SQLAlchemyError).
    except (ImportError, OSError, RuntimeError, ValueError, SQLAlchemyError) as exc:
        logger.error("Integrity check failed: %s", exc)
        return 3

    if critical > 0:
        return 1
    if warnings > 0:
        return 2
    return 0


if __name__ == "__main__":
    setup_script_logging()
    args = parse_args()
    sys.exit(run_cli(
        database_url=args.database_url,
        categories=[args.category] if args.category else None,
        verbose=args.verbose,
    ))
