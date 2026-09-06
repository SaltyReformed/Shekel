"""a generated row records WHICH occurrence it answers

Revision ID: 95e7938240e4
Revises: f2a9c4d7e310
Create Date: 2026-08-27 22:41:46.030515

Plan step **recurrence:R17** of
``docs/plans/implementation_plan_recurrence_redesign.md``, section 4 -- ledger
row **D57**: *a generated row MOVED to another pay period is not durable; the
next generate pass RE-FILLS the period it left.*

Review: Josh, 2026-08-27 -- APPROVED: R17 becomes the FIRST LEAF of plan step
**R5** rather than the interim due-date match it was specified as (ruling
**recurrence:R-R46**).  The interim would have been deleted by
``pay_calendar:C5b`` (#33), which is itself the step that makes
``should_skip_period`` occurrence-aware and which waits on R5 for this very
column; building the column here closes D57 permanently and unblocks C5b from
the balance arc.

**Additive only.  No figure moves and no row changes.**  This migration adds
two nullable columns and nothing writes them yet: the engines begin writing
``occurs_on`` in this same commit, and the SKIP PREDICATE that reads it is the
second leaf.  So every existing row keeps the behaviour it has today until that
leaf ships.

**Why the column, in one sentence.**  Both recurrence engines decide "has this
already been created" by asking whether a PAY PERIOD holds a row, while the
occurrence walk names the period an occurrence's own DATE falls in -- so a row
the owner moved to a neighbouring paycheck empties the period its occurrence
named and the next whole-schedule pass writes a second one.  Measured on a
production clone 2026-08-27: **8 rows / $1,482.93 from ONE pass**, seven of them
already ``Paid`` and one the Van Payment transfer whose duplicate ``$531.94``
moves its loan's derived payoff ``2029-02-22`` -> ``2029-01-22``.

**Nullable, where R5's specification said ``NOT NULL``, and that is a
correction rather than a relaxation.**  Two live writers create a
template-linked row that no cadence ever named, so ``NOT NULL`` is
unsatisfiable as specified:

  * ``carry_forward_service._execute`` rolls an unspent envelope forward as an
    ``is_override`` row -- and writes ``due_date = None`` for the same reason,
    there being no rule to derive a date from;
  * the one-time branch of ``routes/transfers/_instances`` materialises a
    transfer whose template has no ``RecurrenceRule`` at all.

A NULL therefore MEANS "this row answers no occurrence", which is a state the
predicate leaf must read rather than a gap it must repair.

**The backfill is NOT here, and that is a developer ruling** (Josh,
2026-08-27, ruling **recurrence:R-R46**).  The value can only be computed by
the occurrence walk: the row's existing ``due_date`` is NOT its occurrence for
30 of the 780 assignable rows, because ``compute_due_date`` dates a
day-less cadence from its PERIOD's start -- a ``Monthly First`` rule such as
``Phone Allowance`` occurs on the 1st and is dated on the payday.  No migration
in this repository imports application code, and
``scripts/build_test_template.py`` rebuilds the test database by replaying this
whole chain from zero, so an import here would break the SUITE rather than
merely itself the day plan step R5 changes that walk.  The stamping is
``scripts/stamp_occurrences.py``, run by ``entrypoint.sh`` after migrations and
after the reference-data seed whose members the walk reads, gated on a
completion sentinel exactly as the user seed is -- automatic on deploy, and
once.  Its own pre-flight count CANNOT reach zero: rows it deliberately leaves
NULL stay NULL and carry-forward keeps minting more, so the sentinel rather than
the count is what makes this a one-time pass.

**Round-trips exactly.**  The downgrade drops two columns that no constraint,
index or foreign key references and that carry no value this migration wrote.
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = '95e7938240e4'
down_revision = 'f2a9c4d7e310'
branch_labels = None
depends_on = None


def upgrade():
    """Add ``occurs_on`` to both recurrence-generated row tables."""
    op.add_column(
        'transactions',
        sa.Column('occurs_on', sa.Date(), nullable=True),
        schema='budget',
    )
    op.add_column(
        'transfers',
        sa.Column('occurs_on', sa.Date(), nullable=True),
        schema='budget',
    )


def downgrade():
    """Drop both columns.  Value-lossless: nothing else references them."""
    op.drop_column('transfers', 'occurs_on', schema='budget')
    op.drop_column('transactions', 'occurs_on', schema='budget')
