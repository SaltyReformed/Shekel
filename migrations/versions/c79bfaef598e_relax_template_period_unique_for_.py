"""Relax template/period unique indexes to permit is_override siblings

Both ``idx_transactions_template_period_scenario`` and
``idx_transfers_template_period_scenario`` enforce uniqueness on
``(template_id, pay_period_id, scenario_id)`` with a partial WHERE
clause.  Carry-forward sets ``is_override = TRUE`` when moving a
template-linked row, but the recurrence engine has typically already
generated the next instance of the same template in the target period
with ``is_override = FALSE``.  The move violated the index and the
route 500'd silently behind ``hx-swap="none"``.

The recurrence engine in ``app/services/recurrence_engine.py`` and
``app/services/transfer_recurrence.py`` already treats existing
``is_override = TRUE`` rows as off-limits (skipping generation when
present).  Extending the same exclusion down to the partial-index
predicate keeps rule-generated rows unique per (template, period,
scenario) while permitting carried-forward override siblings to coexist.

Revision ID: c79bfaef598e
Revises: c7e3a2f9b104
Create Date: 2026-04-28
"""
from alembic import op
import sqlalchemy as sa


revision = "c79bfaef598e"
down_revision = "c7e3a2f9b104"
branch_labels = None
depends_on = None


def upgrade():
    """Relax both partial unique indexes to exclude is_override = TRUE rows."""
    # Transactions ----------------------------------------------------
    op.drop_index(
        "idx_transactions_template_period_scenario",
        table_name="transactions",
        schema="budget",
        postgresql_where=sa.text(
            "template_id IS NOT NULL AND is_deleted = FALSE"
        ),
    )
    op.create_index(
        "idx_transactions_template_period_scenario",
        "transactions",
        ["template_id", "pay_period_id", "scenario_id"],
        unique=True,
        schema="budget",
        postgresql_where=sa.text(
            "template_id IS NOT NULL "
            "AND is_deleted = FALSE "
            "AND is_override = FALSE"
        ),
    )

    # Transfers (parallel) -------------------------------------------
    op.drop_index(
        "idx_transfers_template_period_scenario",
        table_name="transfers",
        schema="budget",
        postgresql_where=sa.text(
            "transfer_template_id IS NOT NULL AND is_deleted = FALSE"
        ),
    )
    op.create_index(
        "idx_transfers_template_period_scenario",
        "transfers",
        ["transfer_template_id", "pay_period_id", "scenario_id"],
        unique=True,
        schema="budget",
        postgresql_where=sa.text(
            "transfer_template_id IS NOT NULL "
            "AND is_deleted = FALSE "
            "AND is_override = FALSE"
        ),
    )


def downgrade():
    """Tighten both indexes back to the pre-fix predicate.

    Downgrade fails by design if any rows currently violate the tighter
    constraint -- a carried-forward override-sibling next to its
    rule-generated parent would surface as a duplicate when the
    is_override = FALSE qualifier is removed.  Resolve any such rows
    (delete one, or set both is_override flags consistently) before
    running the downgrade.
    """
    # Transactions ----------------------------------------------------
    op.drop_index(
        "idx_transactions_template_period_scenario",
        table_name="transactions",
        schema="budget",
        postgresql_where=sa.text(
            "template_id IS NOT NULL "
            "AND is_deleted = FALSE "
            "AND is_override = FALSE"
        ),
    )
    op.create_index(
        "idx_transactions_template_period_scenario",
        "transactions",
        ["template_id", "pay_period_id", "scenario_id"],
        unique=True,
        schema="budget",
        postgresql_where=sa.text(
            "template_id IS NOT NULL AND is_deleted = FALSE"
        ),
    )

    # Transfers (parallel) -------------------------------------------
    op.drop_index(
        "idx_transfers_template_period_scenario",
        table_name="transfers",
        schema="budget",
        postgresql_where=sa.text(
            "transfer_template_id IS NOT NULL "
            "AND is_deleted = FALSE "
            "AND is_override = FALSE"
        ),
    )
    op.create_index(
        "idx_transfers_template_period_scenario",
        "transfers",
        ["transfer_template_id", "pay_period_id", "scenario_id"],
        unique=True,
        schema="budget",
        postgresql_where=sa.text(
            "transfer_template_id IS NOT NULL AND is_deleted = FALSE"
        ),
    )
