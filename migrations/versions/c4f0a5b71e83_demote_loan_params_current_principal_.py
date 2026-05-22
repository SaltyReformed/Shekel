"""Demote LoanParams.current_principal and LoanParams.interest_rate to nullable.

E-18 / Commit 15: the loan resolver
(``app/services/loan_resolver.py``) is now the single source of
truth for "this loan's current principal, monthly payment, schedule,
payoff date, and life-of-loan interest" (decision D-A,
``docs/audits/financial_calculations/remediation_plan.md`` Section
2).  ``budget.loan_params.current_principal`` and
``budget.loan_params.interest_rate`` are demoted from authoritative
storage to non-authoritative seed columns; every display surface
reads the resolver instead of these columns.

This migration is **additive and reversible**: the columns retain
their data and CHECK constraints; only the NOT NULL contract is
removed.  PostgreSQL's CHECK semantics treat NULL as "unknown" so
``CHECK(current_principal >= 0)`` permits a NULL row and rejects
any non-NULL negative, matching the model's intent.  Existing rows
that already carry non-NULL values continue to satisfy the relaxed
contract.

OPT-1 (``remediation_plan.md`` Section 5) lists the optional
destructive follow-up to DROP these columns entirely, deferred
until a production cycle confirms no display path reads them.

The downgrade restores NOT NULL.  This is safe because the column
data is preserved (additive demotion), but it WILL fail loudly if
any row has been inserted with NULL between the upgrade and the
downgrade -- the diagnostic SELECT below tells the operator which
account is at fault.  No application code in the same Commit-15
release inserts NULL (the setup-flow Marshmallow schema still
requires both fields), so the failure mode is "future-introduced
NULL writer rolled back without first re-seeding"; the explicit
diagnostic is the documented recovery path.

Review: solo developer, 2026-05-20 (audit 2026-04-15, CRIT-02 /
E-18 / Commit 15 -- demote non-authoritative loan columns).
Destructive in spirit (changes the NOT NULL contract) even though
no data is lost, hence the ``Review:`` line per
``docs/coding-standards.md``.
"""

from alembic import op


# Revision identifiers, used by Alembic.
revision = "c4f0a5b71e83"
down_revision = "d3d25212504b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop NOT NULL on ``current_principal`` and ``interest_rate``.

    Pure metadata change.  No backfill, no data rewrite.  Existing
    rows keep their values; the column simply now accepts NULL on
    subsequent inserts.
    """
    op.alter_column(
        "loan_params",
        "current_principal",
        nullable=True,
        schema="budget",
    )
    op.alter_column(
        "loan_params",
        "interest_rate",
        nullable=True,
        schema="budget",
    )


def downgrade() -> None:
    """Restore NOT NULL on both columns.

    Fails loudly if a NULL row exists -- such a row would violate
    the restored constraint and the operator must re-seed the
    column manually (or roll forward instead).  The diagnostic
    SELECT in the RuntimeError message names the offending
    account.
    """
    conn = op.get_bind()
    null_principal = conn.execute(
        sa_text(
            "SELECT account_id FROM budget.loan_params "
            "WHERE current_principal IS NULL"
        )
    ).fetchall()
    null_rate = conn.execute(
        sa_text(
            "SELECT account_id FROM budget.loan_params "
            "WHERE interest_rate IS NULL"
        )
    ).fetchall()
    if null_principal or null_rate:
        raise RuntimeError(
            "Cannot downgrade: NOT NULL would reject "
            f"{len(null_principal)} NULL current_principal row(s) "
            f"(account_ids={[r[0] for r in null_principal]}) and "
            f"{len(null_rate)} NULL interest_rate row(s) "
            f"(account_ids={[r[0] for r in null_rate]}).  Re-seed "
            "the columns from the latest LoanAnchorEvent / "
            "RateHistory and retry, or roll forward to a revision "
            "that does not require NOT NULL."
        )

    op.alter_column(
        "loan_params",
        "current_principal",
        nullable=False,
        schema="budget",
    )
    op.alter_column(
        "loan_params",
        "interest_rate",
        nullable=False,
        schema="budget",
    )


def sa_text(value: str):
    """Lazy ``sqlalchemy.text`` import.

    Keeps the migration's top-level imports lean (``alembic.op`` is
    sufficient for ``alter_column``); ``sqlalchemy.text`` is only
    needed by ``downgrade``'s diagnostic queries.
    """
    # pylint: disable=import-outside-toplevel
    from sqlalchemy import text
    return text(value)
