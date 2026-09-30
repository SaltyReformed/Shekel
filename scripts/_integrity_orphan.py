"""
Shekel Budget App -- Data Integrity Check: the orphan family (OR-*)

One of the four family modules ``scripts/integrity_check.py`` runs, split
along its ``--category`` values at plan step ``balance:X-bi-6-4c-4`` (ruling
**R-BAL161**); ``scripts/_integrity_core.py`` says why.
"""

from scripts._integrity_core import CheckSpec, run_check


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
        # OR-03 asks the TRANSFERS and their definitions too (plan step
        # ``balance:X-bi-6-4c-4``, ruling **R-BAL160**), the report's twin of
        # ``archive_helpers.category_has_usage``'s transfer part (finding
        # **BAL-545**).  A transfer's category counted only through its shadow
        # rows, which ``X-bi-6-4d`` stops writing, and a recurring transfer
        # definition's not at all, so a category only a transfer definition
        # named was listed as unused.  Every transfer's category is on its
        # expense shadow too on the 2026-09-30 00:11 production dump, and
        # none of that dump's seven unused categories is a transfer
        # definition's, so the list there does not change.
        ("OR-03", "Categories not used by any template, transfer or transaction", """
            SELECT c.id, c.group_name, c.item_name
            FROM budget.categories c
            LEFT JOIN budget.transaction_templates tt ON tt.category_id = c.id
            LEFT JOIN budget.transactions t ON t.category_id = c.id
            LEFT JOIN budget.transfer_templates xt ON xt.category_id = c.id
            LEFT JOIN budget.transfers x ON x.category_id = c.id
            WHERE tt.id IS NULL
              AND t.id IS NULL
              AND xt.id IS NULL
              AND x.id IS NULL
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
        run_check(session, CheckSpec(cid, "orphan", "warning", desc, sql))
        for cid, desc, sql in checks
    ]
