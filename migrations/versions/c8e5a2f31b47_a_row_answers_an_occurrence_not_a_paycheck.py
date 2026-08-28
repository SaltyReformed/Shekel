"""a row answers an occurrence, not a paycheck

Re-keys the generation-idempotency index off the pay period and onto the
occurrence the row answers, which is plan step **R17**'s second leaf and the
storage half of ledger row **D57**.

A generated row answers ONE occurrence of its template's cadence.  The pay
period is where that occurrence's money LANDS -- a derived placement, and one
the owner may change by moving the row.  Keyed on the paycheck, the old index
made a moved row vacate its own occurrence, so the next whole-schedule generate
pass answered that occurrence a second time.  Measured on a production clone,
2026-08-28: 8 rows, ``$1,482.93``, six of them duplicating a due date a ``Paid``
row already covered.

TWO indexes replace the one, per table, because ``occurs_on`` is NULLABLE and
PostgreSQL treats NULLs as DISTINCT: a single unique index over it would let a
template hold unlimited undated rows in one paycheck, dropping the "one row per
template per paycheck" rule this table has always had.  A row that answers no
occurrence therefore keeps the OLD key.  That split is the same rule
``_recurrence_common.OccurrenceClaims`` applies in Python -- identity is the
occurrence where it is known and the paycheck where it is not -- so the storage
and the predicate state one thing rather than two that must agree.

Both indexes stay PARTIAL over ``is_deleted = FALSE AND is_override = FALSE``,
unchanged: an override sibling may coexist with its rule-generated parent, which
carry-forward relies on.

**Verified installable before writing this**: zero ``(template, scenario,
occurs_on)`` collisions among the 742 template-linked rows on the 2026-08-28
production clone, so the new unique indexes build without a data repair.

Revision ID: c8e5a2f31b47
Revises: a7c41f9d2b60
Create Date: 2026-08-28
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'c8e5a2f31b47'
down_revision = 'a7c41f9d2b60'
branch_labels = None
depends_on = None


#: The downgrade's refusal predicate, as a STANDALONE statement so it can be
#: executed against real rows rather than only read.  Formatted with the table
#: and its template foreign key.  The precedent is
#: ``tests/test_models/test_anchor_cache_downgrade.py``: this migration's DDL
#: needs an ACCESS EXCLUSIVE lock that conflicts with the xdist workers, and
#: its SELECT does not -- so the half that DECIDES is the half a suite can run.
COLLIDING_PAIRS_SQL = (
    "SELECT count(*) FROM (SELECT 1 FROM budget.{table} "
    "WHERE {fk} IS NOT NULL AND is_deleted = FALSE "
    "AND is_override = FALSE "
    "GROUP BY {fk}, pay_period_id, scenario_id "
    "HAVING count(*) > 1) AS c"
)


# (table, fk column, old index, dated index, undated index)
_TABLES = (
    (
        "transactions", "template_id",
        "idx_transactions_template_period_scenario",
        "idx_transactions_template_scenario_occurrence",
        "idx_transactions_template_scenario_undated",
    ),
    (
        "transfers", "transfer_template_id",
        "idx_transfers_template_period_scenario",
        "idx_transfers_template_scenario_occurrence",
        "idx_transfers_template_scenario_undated",
    ),
)


def upgrade():
    """Re-key both tables' generation index onto the occurrence."""
    for table, fk, old_idx, dated_idx, undated_idx in _TABLES:
        op.drop_index(old_idx, table_name=table, schema="budget")
        op.create_index(
            dated_idx, table, [fk, "scenario_id", "occurs_on"],
            unique=True, schema="budget",
            postgresql_where=(
                f"{fk} IS NOT NULL AND occurs_on IS NOT NULL "
                "AND is_deleted = FALSE AND is_override = FALSE"
            ),
        )
        op.create_index(
            undated_idx, table, [fk, "scenario_id", "pay_period_id"],
            unique=True, schema="budget",
            postgresql_where=(
                f"{fk} IS NOT NULL AND occurs_on IS NULL "
                "AND is_deleted = FALSE AND is_override = FALSE"
            ),
        )


def downgrade():
    """Restore the paycheck-keyed index.

    **Value-lossy in one direction that cannot be helped, and it is the
    downgrade rather than the upgrade that can fail.**  The old index forbids
    two non-override rows of one template in one paycheck; the new pair allows
    them when they answer different occurrences.  A schedule that generated such
    a pair while this revision was applied cannot be re-keyed back without
    deleting one of two rows that both hold real money, so this refuses rather
    than choosing -- the operator resolves the pair and re-runs.
    """
    for table, fk, old_idx, dated_idx, undated_idx in _TABLES:
        conn = op.get_bind()
        clash = conn.exec_driver_sql(
            COLLIDING_PAIRS_SQL.format(table=table, fk=fk),
        ).scalar()
        if clash:
            raise RuntimeError(
                f"budget.{table}: {clash} paycheck(s) hold more than one "
                f"generated row for the same template, which the paycheck-keyed "
                f"index cannot store.  These are rows answering different "
                f"occurrences of one cadence.  Resolve them (delete or override "
                f"one of each pair) before downgrading past c8e5a2f31b47."
            )
        op.drop_index(dated_idx, table_name=table, schema="budget")
        op.drop_index(undated_idx, table_name=table, schema="budget")
        op.create_index(
            old_idx, table, [fk, "pay_period_id", "scenario_id"],
            unique=True, schema="budget",
            postgresql_where=(
                f"{fk} IS NOT NULL "
                "AND is_deleted = FALSE AND is_override = FALSE"
            ),
        )
