"""the pay schedule carries its shift convention

Adds ``budget.pay_schedule.shift_id`` -- what an owner's payroll does when a
payday lands on a day no money moves on -- keyed to the EXISTING
``ref.business_day_shifts`` (``none`` / ``prior`` / ``next``) seeded at plan
step ``recurrence:R2``.  Plan step **pay_calendar:C14-b**, rulings **R-PC47**,
**R-PC54** and **R-PC56**.

Every existing row is backfilled to ``none``, so behaviour is OFF until an
owner answers on one of the four forms that already ask for a cadence
(**R-PC56**).  Nothing reads the column yet: plan step ``C14-e`` is the leaf
that applies the convention at the producer, and it is the one that moves
money.

**There is deliberately NO CHECK constraint here, and that absence is a
developer ruling of 2026-09-05 rather than an omission** (**R-PC59**).  A
convention that
displaces a payday is legal only on a cadence longer than the longest run of
consecutive closed days -- shorter, and two nominal paydays displace onto ONE
day, which ``pay_calendar._derive.derive_periods`` refuses outright.  The plan
specified that rule as a CHECK.  It cannot be one, for three structural
reasons:

1. The floor is DERIVED.  It is the longest closed run plus one -- proved and
   computed by ``app.utils.business_days.shortest_collision_free_cadence`` --
   and it moves with the federal holiday set, which is not fixed
   (``business_days.JUNETEENTH_FIRST_YEAR`` records that set changing once
   inside the window this application admits).  A CHECK expression must be
   IMMUTABLE, so a constraint could only freeze a copy of a number nothing can
   recompute.
2. A constraint cannot name a FIELD.  It arrives as an ``IntegrityError``
   carrying a constraint name, where a form needs the message attached to the
   control the owner chose -- which is what
   ``schemas.validation.pay_periods.validate_derivable_rhythm`` supplies.

*A first draft of this docstring gave a third reason -- that a CHECK would
have to hard-code the ``ref`` id of ``none``, which is seed data -- and a
fourth, that PostgreSQL never revalidates a CHECK against stored rows.  An
adversarial review of 2026-09-05 struck both.  The first is defeasible (a
pinned id verified by a migration-time assertion dodges it), and the second is
false as stated: ``ADD CONSTRAINT`` without ``NOT VALID`` scans every existing
row.  What is true is narrower and applies to the write door just as much --
a refusal asked on write cannot see a stored row a later holiday change made
illegal, and nothing reconciles this table today.*

The refusal lives at the write door instead:
``pay_schedule_service.reject_shift_on_short_cadence``, asked by
``upsert_schedule`` -- which writes the cadence and the convention in ONE
statement, so the pair is judged against the state the operation leaves behind
rather than through an intermediate row neither statement means.

The column carries no ``server_default`` for the same reason a CHECK cannot
carry the id: a default in DDL would be a literal nobody can re-derive, and a
wrong one fails SILENTLY in the money-moving direction (a schedule defaulting
to ``prior`` displaces paydays its owner never asked to move).  "A new schedule
displaces nothing" is a business rule, so ``upsert_schedule`` states it and
resolves the id through ``ref_cache``, exactly as ``recurrence._authoring``
does for the same table.  A writer that forgets gets a NOT NULL violation
rather than a wrong convention.

The three-step is the documented one (``.claude/rules/database.md``): add
nullable, backfill from the ``ref`` row by NAME rather than by a hard-coded
id, verify zero NULLs survived, then tighten.  The backfill is total by
construction -- every existing row gets the same value -- and the verification
is what proves it rather than the argument.

The downgrade drops the column and its foreign key.  That is value-lossy in
the only way it can be: an owner's stated convention has nowhere to live once
the column is gone, and inventing a substitute would be worse.  No projected
figure changes in either direction, because nothing reads the column until
``C14-e``.

Revision ID: b3f7c2a91d4e
Revises: e7c3a1f9b482
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'b3f7c2a91d4e'
down_revision = 'e7c3a1f9b482'
branch_labels = None
depends_on = None


#: The backfill, resolving the ``none`` row by NAME.  A subquery is legal in an
#: ``UPDATE`` where it is not in a ``CHECK``, which is what lets this migration
#: avoid the hard-coded ``ref`` id the constraint could not have avoided.
_BACKFILL_SQL = (
    "UPDATE budget.pay_schedule "
    "SET shift_id = (SELECT id FROM ref.business_day_shifts WHERE name = 'none')"
)

#: Whether the vocabulary this column keys to actually holds its ``none`` row.
#:
#: **Asked BEFORE the backfill, and an adversarial review of 2026-09-05 is
#: why.**  The NULL count below was the only check, and it is VACUOUS on an
#: empty table -- which is every fresh database and every test-template build.
#: With no rows to update, a missing ``none`` row leaves nothing NULL, the
#: count reads zero, and the migration goes on to ``SET NOT NULL`` and the
#: foreign key against a vocabulary that cannot satisfy the first INSERT.  The
#: docstring's claim that "the verification is what proves it rather than the
#: argument" was false for exactly that case.  This is the statement that
#: measures the thing.
_SEEDED_NONE_ROWS_SQL = (
    "SELECT count(*) FROM ref.business_day_shifts WHERE name = 'none'"
)

#: What the post-backfill verification asks.  Counted rather than trusted: it
#: is the proof the UPDATE reached every row, on a table that HAS rows.
_SURVIVING_NULLS_SQL = (
    "SELECT count(*) FROM budget.pay_schedule WHERE shift_id IS NULL"
)


def upgrade():
    """Add, backfill and tighten the payday-convention column."""
    bind = op.get_bind()
    seeded = bind.execute(sa.text(_SEEDED_NONE_ROWS_SQL)).scalar()
    if seeded != 1:
        raise RuntimeError(
            f"ref.business_day_shifts holds {seeded} row(s) named 'none', "
            f"expected exactly 1 -- the vocabulary seeded by e7a4d95c2b18.  "
            f"Every budget.pay_schedule row must start at that convention and "
            f"the column's one writer resolves it by name, so the column "
            f"cannot be added against this database.  Diagnose with: SELECT "
            f"id, name FROM ref.business_day_shifts ORDER BY id;  Re-seed, "
            f"then re-run this migration."
        )

    op.add_column(
        "pay_schedule",
        sa.Column("shift_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.execute(_BACKFILL_SQL)

    surviving = bind.execute(sa.text(_SURVIVING_NULLS_SQL)).scalar()
    if surviving:
        raise RuntimeError(
            f"{surviving} budget.pay_schedule row(s) still hold a NULL "
            f"shift_id after the backfill, which means "
            f"ref.business_day_shifts holds no row named 'none' -- the "
            f"vocabulary seeded by e7a4d95c2b18.  Diagnose with: SELECT id, "
            f"name FROM ref.business_day_shifts ORDER BY id;  Re-seed that "
            f"row, then re-run this migration."
        )

    op.alter_column(
        "pay_schedule", "shift_id", nullable=False, schema="budget",
    )
    op.create_foreign_key(
        "fk_pay_schedule_shift_id", "pay_schedule",
        "business_day_shifts", ["shift_id"], ["id"],
        source_schema="budget", referent_schema="ref", ondelete="RESTRICT",
    )


def downgrade():
    """Drop the payday-convention column and its foreign key."""
    op.drop_constraint(
        "fk_pay_schedule_shift_id", "pay_schedule",
        type_="foreignkey", schema="budget",
    )
    op.drop_column("pay_schedule", "shift_id", schema="budget")
