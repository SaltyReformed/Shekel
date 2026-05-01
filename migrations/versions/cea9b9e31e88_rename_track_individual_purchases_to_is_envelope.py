"""Rename track_individual_purchases to is_envelope

The carry-forward aftermath plan repurposes the existing tracking flag
on ``budget.transaction_templates`` as the disambiguator for envelope
versus discrete carry-forward semantics.  Templates that track
individual purchases (groceries, spending money, gas) are envelope
templates; templates without entry tracking (rent, subscriptions) are
discrete obligations.  Renaming the column to ``is_envelope`` makes the
new role explicit at the schema level.

This is an identity rename: no data movement, no constraint changes,
no index changes, no default change.  PostgreSQL's
``ALTER TABLE ... RENAME COLUMN`` preserves NOT NULL, server defaults,
and audit-trigger attachment.

Revision ID: cea9b9e31e88
Revises: c79bfaef598e
Create Date: 2026-04-30
"""
from alembic import op


revision = "cea9b9e31e88"
down_revision = "c79bfaef598e"
branch_labels = None
depends_on = None


def upgrade():
    """Rename ``track_individual_purchases`` to ``is_envelope``."""
    op.alter_column(
        "transaction_templates",
        "track_individual_purchases",
        new_column_name="is_envelope",
        schema="budget",
    )


def downgrade():
    """Restore the original column name.

    The rename is fully reversible: PostgreSQL ``RENAME COLUMN`` is an
    in-place metadata change.  No data is lost in either direction.
    """
    op.alter_column(
        "transaction_templates",
        "is_envelope",
        new_column_name="track_individual_purchases",
        schema="budget",
    )
