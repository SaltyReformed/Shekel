"""Backfill account anchor + tighten anchor columns to NOT NULL (E-19)

Revision ID: cfb15e782f86
Revises: b4b588a49a0c
Create Date: 2026-05-19

Review: solo developer, 2026-05-19 (audit financial_calculations CRIT-01/E-19, Commit 3)

CRIT-01 / F-001 SCOPE_DRIFT eliminates the ``current_anchor_period_id
IS NULL`` and ``current_anchor_balance IS NULL`` states.  Before this
migration the five balance producers (grid, /accounts, /savings,
dashboard, net worth) fork four different ways for the NULL-anchor case
-- blank row, stored-balance-at-current-period projection, account
omitted -- so an account "matches nowhere" on the developer's screens.
The remediation direction (Commit 4's resolver and Commits 5-8's
producer rollout) requires the NULL-anchor state to be unreachable;
this migration is the storage-tier half of that contract.

Three-step populated-table pattern (``docs/coding-standards.md``,
"Migrations -> Add NOT NULL columns to populated tables"):

  1. **Backfill NULL anchor balances** to ``Decimal('0.00')``.  Zero
     is a real value (E-12), not "missing"; an account with no
     recorded checking activity is anchored to zero, and the resolver
     in Commit 4 returns ``Decimal("0.00")``, not None.

  2. **Backfill NULL anchor periods** via the documented derivation:
     the pay period containing the account's earliest non-deleted
     transaction's pay_period, else the earliest pay period for the
     account's user.  Deterministic and reproducible for staging
     rebuilds.  When neither source resolves, the row stays NULL and
     Step 3 raises ``RuntimeError`` with the diagnostic SELECT.

  3. **Materialize the matching AccountAnchorHistory row** for every
     account that was backfilled, so the append-only history that
     Commits 4-5 read as the date-anchored source of truth matches
     the column cache.  Idempotent via a NOT EXISTS guard against
     the partial unique expression index
     ``uq_anchor_history_account_period_balance_day`` -- re-running
     the migration on an already-backfilled database does not insert
     a duplicate history row.

  4. **Verify zero NULLs remain.**  ``SELECT COUNT(*)`` on each
     column with a diagnostic SELECT embedded in the RuntimeError so
     the operator hears the exact account_ids that could not be
     resolved, not the cryptic generic NOT NULL violation message.

  5. **Tighten both anchor columns to NOT NULL** and add a named
     CHECK constraint ``ck_accounts_anchor_balance_present`` that
     makes the intent explicit at the catalog level
     (redundant-but-documented; the NOT NULL alone is sufficient).

Downgrade re-widens the columns to nullable and drops the CHECK; the
backfilled data and history rows are retained because they are
harmless additive content.  This makes the migration reversible
(disaster recovery, staging rebuild) without losing the audit trail.

The COALESCE-derivation SQL and the diagnostic SELECT are exposed
as module-level constants so the C3 test suite can re-execute the
exact text the migration uses; two derivations would silently drift.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "cfb15e782f86"
down_revision = "b4b588a49a0c"
branch_labels = None
depends_on = None


# Backfill SQL for the anchor BALANCE column.  Any account with a NULL
# balance is set to a real Decimal zero.  Zero is a value, not
# "missing" -- E-12 (``00_priors.md``).  Idempotent via ``WHERE
# current_anchor_balance IS NULL``: re-running the migration on a
# database where the column has already been populated is a no-op.
BACKFILL_BALANCE_SQL = (
    "UPDATE budget.accounts\n"
    "SET current_anchor_balance = 0.00\n"
    "WHERE current_anchor_balance IS NULL"
)


# Derivation for the anchor PERIOD column.  For each account ``a``
# with NULL ``current_anchor_period_id``, return the most-defensible
# pay period to anchor against:
#
#   Tier 1 -- the pay period of this account's earliest non-deleted
#   transaction.  This is the most semantically accurate origin: the
#   account first "became live" when activity started against it, so
#   the period that contained that first activity is the natural
#   anchor.  Filters out soft-deleted transactions
#   (``is_deleted = FALSE``) per the project-wide rule.
#
#   Tier 2 -- the earliest pay period for the account's owning user.
#   Used when the account has no transactions yet (newly created,
#   pre-Commit-3); the user must already have at least one period for
#   this tier to resolve (a brand-new user with no periods leaves the
#   row NULL and Step 4 raises RuntimeError with the diagnostic
#   SELECT).  Ordering by ``period_index`` rather than ``start_date``
#   is identical for normally-generated periods (the service assigns
#   ``period_index`` chronologically) and slightly more robust if a
#   future operator manually inserts a period with an out-of-order
#   ``start_date``.
#
# Each tier is a correlated subquery against the outer ``budget.accounts a``;
# COALESCE returns the first non-NULL result.
RESOLVE_PERIOD_SQL = """\
COALESCE(
    (
        SELECT t.pay_period_id
        FROM budget.transactions t
        JOIN budget.pay_periods pp ON pp.id = t.pay_period_id
        WHERE t.account_id = a.id
          AND t.is_deleted = FALSE
        ORDER BY pp.period_index ASC
        LIMIT 1
    ),
    (
        SELECT pp.id
        FROM budget.pay_periods pp
        WHERE pp.user_id = a.user_id
        ORDER BY pp.period_index ASC
        LIMIT 1
    )
)"""


# Backfill SQL for the anchor PERIOD column, composed with the
# derivation above.  Idempotent via ``WHERE current_anchor_period_id
# IS NULL`` so re-running on a populated database is a no-op.
BACKFILL_PERIOD_SQL = (
    "UPDATE budget.accounts a\n"
    f"SET current_anchor_period_id = {RESOLVE_PERIOD_SQL}\n"
    "WHERE a.current_anchor_period_id IS NULL"
)


# Materialize a matching AccountAnchorHistory row for every account
# whose column anchor has no corresponding history entry on the
# (account_id, pay_period_id, anchor_balance, UTC-day) tuple.  This
# ensures the resolver in Commit 4, which reads the latest history
# row as the date-anchored source of truth, agrees with the column
# cache on every account.  NOT EXISTS makes the insert idempotent
# under the partial unique index ``uq_anchor_history_account_period_balance_day``
# (commit C-22 / F-103): re-running the migration on the same day
# does not produce a duplicate-key error.
INSERT_HISTORY_SQL = (
    "INSERT INTO budget.account_anchor_history\n"
    "    (account_id, pay_period_id, anchor_balance, notes)\n"
    "SELECT a.id, a.current_anchor_period_id, a.current_anchor_balance,\n"
    "       'origination backfill (E-19, Commit 3)'\n"
    "FROM budget.accounts a\n"
    "WHERE a.current_anchor_period_id IS NOT NULL\n"
    "  AND a.current_anchor_balance IS NOT NULL\n"
    "  AND NOT EXISTS (\n"
    "      SELECT 1\n"
    "      FROM budget.account_anchor_history h\n"
    "      WHERE h.account_id = a.id\n"
    "        AND h.pay_period_id = a.current_anchor_period_id\n"
    "        AND h.anchor_balance = a.current_anchor_balance\n"
    "  )"
)


# Diagnostic SELECT shown to the operator if any account survives the
# backfill with a NULL anchor column.  Names the owning user_id and
# both anchor columns so the operator can decide whether to provision
# pay periods for that user, delete the orphan account, or restore
# the prior database state and re-run.
DIAGNOSTIC_SELECT = (
    "  SELECT a.id AS account_id, a.user_id, a.name,\n"
    "         a.current_anchor_balance, a.current_anchor_period_id\n"
    "  FROM budget.accounts a\n"
    "  WHERE a.current_anchor_balance IS NULL\n"
    "     OR a.current_anchor_period_id IS NULL\n"
    "  ORDER BY a.user_id, a.id"
)


def upgrade():
    """Backfill NULL anchors, materialize history, tighten to NOT NULL.

    Steps 1-3 are idempotent and safe to re-run.  Step 4 raises
    RuntimeError with a diagnostic SELECT if any account remains
    unresolved (e.g. an account whose owner has zero pay periods);
    the operator must provision the missing periods before retrying
    the migration.  Step 5 is the type/constraint tighten itself.
    """
    bind = op.get_bind()

    # Step 1: backfill NULL anchor balances to a real Decimal zero.
    op.execute(BACKFILL_BALANCE_SQL)

    # Step 2: backfill NULL anchor periods via the COALESCE derivation
    # (earliest transaction's period, else earliest period for user).
    op.execute(BACKFILL_PERIOD_SQL)

    # Step 3: materialize a matching AccountAnchorHistory row for
    # every account so the resolver (Commit 4) and the column cache
    # agree.  Idempotent under the partial unique index.
    op.execute(INSERT_HISTORY_SQL)

    # Step 4: verify zero NULLs remain.  Embed the diagnostic SELECT
    # in the RuntimeError message so the operator sees actionable
    # context rather than the generic NOT NULL violation that the
    # ALTER would emit.
    unresolved = bind.execute(sa.text(
        "SELECT count(*) FROM budget.accounts "
        "WHERE current_anchor_balance IS NULL "
        "   OR current_anchor_period_id IS NULL"
    )).scalar()
    if unresolved:
        raise RuntimeError(
            f"{unresolved} accounts could not be backfilled with a "
            "non-NULL anchor (no transactions and no pay periods for "
            "the owning user).  Provision pay periods for these users "
            "and re-run the migration:\n"
            "\n"
            f"{DIAGNOSTIC_SELECT};"
        )

    # Step 5: tighten both anchor columns to NOT NULL and add the
    # named CHECK constraint.  The CHECK is redundant with the NOT
    # NULL on ``current_anchor_balance`` but is named per the project
    # coding-standards convention so a future schema audit can match
    # it against the Marshmallow contract by name.
    op.alter_column(
        "accounts", "current_anchor_balance",
        existing_type=sa.Numeric(precision=12, scale=2),
        nullable=False,
        schema="budget",
    )
    op.alter_column(
        "accounts", "current_anchor_period_id",
        existing_type=sa.Integer(),
        nullable=False,
        schema="budget",
    )
    op.create_check_constraint(
        "ck_accounts_anchor_balance_present",
        "accounts",
        "current_anchor_balance IS NOT NULL",
        schema="budget",
    )


def downgrade():
    """Re-widen the anchor columns to nullable; drop the CHECK.

    Reversible: the backfilled values and history rows are harmless
    additive content (zero balances and origination history entries),
    so the downgrade does not delete them.  An operator reverting to
    a pre-Commit-3 application revision sees the NULL state restored
    as a possibility, with the column carrying ``0.00`` for accounts
    that were previously NULL.  That is the correct semantic for the
    pre-E-19 application: zero and NULL are both interpreted as "no
    set anchor" in the legacy code paths.
    """
    op.drop_constraint(
        "ck_accounts_anchor_balance_present",
        "accounts",
        type_="check",
        schema="budget",
    )
    op.alter_column(
        "accounts", "current_anchor_period_id",
        existing_type=sa.Integer(),
        nullable=True,
        schema="budget",
    )
    op.alter_column(
        "accounts", "current_anchor_balance",
        existing_type=sa.Numeric(precision=12, scale=2),
        nullable=True,
        schema="budget",
    )
