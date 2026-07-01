"""effective-date budget.escrow_components (temporal escrow); drop is_active

Revision ID: d1e7c4a2f9b3
Revises: efca4315bf81
Create Date: 2026-07-01 10:00:00.000000

Review: solo developer, 2026-07-01 (temporal-escrow prerequisite for the
Build-Order Step 4 loan-payment split; DESTRUCTIVE -- drops
``budget.escrow_components.is_active`` and the total ``uq_escrow_account_name``
unique, replacing both with the effective-dating range columns and a partial
unique.  The downgrade restores them, reversibly on any data migrated from a
valid pre-migration state that has not since re-added a removed component under
the same name -- see the Downgrade note.)

Temporal-escrow prerequisite (see
``docs/audits/balance_architecture/implementation_plan_temporal_escrow.md``).

Turns ``budget.escrow_components`` from a set of single mutable rows gated by a
boolean ``is_active`` into an effective-dated series of versions, each valid over
a half-open range ``[effective_date, end_date)`` -- the same effective-dating
shape ``rate_history`` already uses.  This lets the loan-payment posting split
read the escrow that was in effect ON each payment's date (immutable for a past
date), so a posted split never silently moves when the user later changes escrow.

  * **effective_date** DATE NOT NULL -- when a version takes effect.  Added in
    three steps on the populated table per the coding standard's
    NOT-NULL-on-a-populated-table rule: (1) add nullable; (2) backfill every
    existing row to its loan's ``origination_date`` (a floor at or before every
    payment date, so every historical payment sees today's escrow exactly as it
    did pre-migration -- the change is a no-op on existing data), falling back to
    the row's own ``created_at`` date only if the account somehow has no
    ``loan_params`` (defensive; escrow lives only on loans); (3) verify zero
    NULLs (raise with a diagnostic SELECT otherwise) and ``SET NOT NULL`` with a
    ``CURRENT_DATE`` server default so a component added later (the route INSERT
    omits the column) takes effect that day.
  * **end_date** DATE NULL -- exclusive end of a version's active range; NULL =
    still in effect (the "currently active" set that replaces ``is_active =
    TRUE``).  Backfilled for the formerly-inactive (``is_active = FALSE``) rows
    to ``GREATEST(updated_at::date, effective_date)`` -- their real removal date
    was never recorded, so ``updated_at`` (the last mutation, i.e. the
    deactivation) is the best available proxy, floored to ``effective_date`` so
    the range CHECK holds (an equal value is a valid zero-length "never active"
    range).  DOCUMENTED LIMITATION: a payment predating such a row's true
    deactivation may read a slightly wrong escrow; on real data there are no
    inactive components, so this is vacuous today.

  * **uq_escrow_components_account_name_active** -- partial unique
    ``(account_id, name) WHERE end_date IS NULL``: at most one ACTIVE version per
    name.  Replaces the total ``uq_escrow_account_name`` (which would forbid ever
    re-adding a removed line item under the same name).
  * **ck_escrow_components_date_range** -- ``end_date IS NULL OR end_date >=
    effective_date`` (``>=`` admits a same-day add-then-delete zero-length range).
  * **ix_escrow_components_account_effective** -- ``(account_id, effective_date,
    end_date)`` for the per-payment as-of lookup.
  * **is_active** dropped -- "active" is now exactly ``end_date IS NULL``; keeping
    the boolean would be redundant state that could drift from the range.

**No audit change.**  ``budget.escrow_components`` already exists and is already
audited; adding / dropping columns does not change a table's audited status.

The column / index DDL matches the updated model in
``app/models/loan_features.py`` (a future autogenerate run yields an empty diff);
the CHECK is added by hand (Alembic does not autogenerate CHECKs).

**Self-contained.**  Imports nothing from ``app``; the backfill joins
``budget.loan_params`` by ``account_id`` (the origination floor) with the same
raw-SQL, name-resolved discipline the Step-2/3/4 backfills use.

**Downgrade.**  Drops the range index / CHECK / partial unique, restores
``is_active`` (TRUE where still in effect, FALSE where ``end_date`` is set), drops
the two range columns, and restores the total ``uq_escrow_account_name``.
Reversible for any data migrated from a valid pre-migration state (no active and
inactive row ever shared a name) that has NOT since re-added a removed component
under the same name.  If such same-name history was created post-upgrade, the
total-unique restore raises; dedupe first with:
``DELETE FROM budget.escrow_components a USING budget.escrow_components b
WHERE a.account_id = b.account_id AND a.name = b.name AND a.end_date IS NOT NULL
AND b.end_date IS NULL;`` (drops the superseded historical versions).
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'd1e7c4a2f9b3'
down_revision = 'efca4315bf81'
branch_labels = None
depends_on = None


# Step 2 of the three-step NOT NULL add: backfill every existing row's
# ``effective_date`` to its loan's origination date (a floor <= every payment
# date, so all historical payments keep seeing today's escrow), falling back to
# the row's own creation date only if the account has no ``loan_params`` row.
# Idempotent (``WHERE effective_date IS NULL``).  The ``AT TIME ZONE 'UTC'``
# cast is the storage-timezone date, the determinism convention the Step-3
# backfill uses.
_BACKFILL_EFFECTIVE_DATE_SQL = (
    "UPDATE budget.escrow_components AS ec "
    "SET effective_date = COALESCE( "
    "    (SELECT lp.origination_date FROM budget.loan_params AS lp "
    "     WHERE lp.account_id = ec.account_id), "
    "    (ec.created_at AT TIME ZONE 'UTC')::date "
    ") "
    "WHERE ec.effective_date IS NULL"
)

# Step 3 guard: any row the backfill failed to stamp aborts the migration with a
# diagnostic rather than letting the NOT NULL ALTER raise opaquely.
_COUNT_NULL_EFFECTIVE_DATE_SQL = (
    "SELECT count(*) FROM budget.escrow_components WHERE effective_date IS NULL"
)

# Close the range on the formerly-inactive rows: ``end_date`` = the last-mutation
# date (best-effort deactivation date), floored to one day past
# ``effective_date`` so ``ck_escrow_components_date_range`` holds.  Active rows
# keep ``end_date = NULL``.
_BACKFILL_END_DATE_SQL = (
    "UPDATE budget.escrow_components "
    "SET end_date = GREATEST( "
    "    (updated_at AT TIME ZONE 'UTC')::date, "
    "    effective_date "
    ") "
    "WHERE is_active = false"
)

# Downgrade backfill: the pre-temporal boolean is exactly "still in effect".
_RESTORE_IS_ACTIVE_SQL = (
    "UPDATE budget.escrow_components SET is_active = (end_date IS NULL)"
)


def upgrade():
    """Add the effective_date / end_date range, drop is_active, swap the unique.

    Ordered: add both nullable columns; backfill ``effective_date`` from each
    loan's origination date; verify no NULL survives (raise otherwise) and
    tighten to NOT NULL with a CURRENT_DATE server default; close the range on
    the formerly-inactive rows; drop ``is_active``; then swap the total unique
    for the partial one and add the range CHECK and the as-of index.  See the
    module docstring for the full rationale.
    """
    op.add_column(
        'escrow_components',
        sa.Column('effective_date', sa.Date(), nullable=True),
        schema='budget',
    )
    op.add_column(
        'escrow_components',
        sa.Column('end_date', sa.Date(), nullable=True),
        schema='budget',
    )

    # Three-step NOT NULL: backfill from the origination floor, verify, tighten.
    op.execute(_BACKFILL_EFFECTIVE_DATE_SQL)
    remaining = op.get_bind().execute(
        sa.text(_COUNT_NULL_EFFECTIVE_DATE_SQL)
    ).scalar()
    if remaining:
        raise RuntimeError(
            f"cannot set budget.escrow_components.effective_date NOT NULL: "
            f"{remaining} row(s) still NULL after the origination-floor "
            f"backfill.  Inspect with: SELECT id, account_id, created_at FROM "
            f"budget.escrow_components WHERE effective_date IS NULL"
        )
    op.alter_column(
        'escrow_components', 'effective_date', existing_type=sa.Date(),
        nullable=False, server_default=sa.text('CURRENT_DATE'), schema='budget',
    )

    # Close the range on the formerly-inactive rows, THEN drop is_active (the
    # backfill reads it).
    op.execute(_BACKFILL_END_DATE_SQL)
    op.drop_column('escrow_components', 'is_active', schema='budget')

    # Swap the total unique for the active-only partial unique, and add the
    # range CHECK + the as-of index.
    op.drop_constraint(
        'uq_escrow_account_name', 'escrow_components',
        schema='budget', type_='unique',
    )
    op.create_index(
        'uq_escrow_components_account_name_active', 'escrow_components',
        ['account_id', 'name'], unique=True, schema='budget',
        postgresql_where=sa.text('end_date IS NULL'),
    )
    op.create_check_constraint(
        'ck_escrow_components_date_range', 'escrow_components',
        'end_date IS NULL OR end_date >= effective_date', schema='budget',
    )
    op.create_index(
        'ix_escrow_components_account_effective', 'escrow_components',
        ['account_id', 'effective_date', 'end_date'], schema='budget',
    )


def downgrade():
    """Restore is_active + the total unique; drop the range columns / objects.

    Reverse of the upgrade: drop the as-of index, the range CHECK, and the
    partial unique; re-add ``is_active`` (TRUE where still in effect, FALSE where
    ``end_date`` is set); drop the two range columns; restore the total
    ``uq_escrow_account_name``.  The final step raises if same-name history was
    created post-upgrade -- see the module docstring's Downgrade note for the
    dedupe SQL.
    """
    op.drop_index(
        'ix_escrow_components_account_effective', table_name='escrow_components',
        schema='budget',
    )
    op.drop_constraint(
        'ck_escrow_components_date_range', 'escrow_components',
        schema='budget', type_='check',
    )
    op.drop_index(
        'uq_escrow_components_account_name_active',
        table_name='escrow_components', schema='budget',
        postgresql_where=sa.text('end_date IS NULL'),
    )

    # Restore is_active (server default TRUE, matching the original mixin
    # column), backfill it from the range, THEN drop the range columns.
    op.add_column(
        'escrow_components',
        sa.Column(
            'is_active', sa.Boolean(), nullable=False,
            server_default=sa.text('true'),
        ),
        schema='budget',
    )
    op.execute(_RESTORE_IS_ACTIVE_SQL)
    op.drop_column('escrow_components', 'end_date', schema='budget')
    op.drop_column('escrow_components', 'effective_date', schema='budget')

    op.create_unique_constraint(
        'uq_escrow_account_name', 'escrow_components',
        ['account_id', 'name'], schema='budget',
    )
