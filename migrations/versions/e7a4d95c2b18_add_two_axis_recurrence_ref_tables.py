"""add the two-axis recurrence ref tables (units, placements, day shifts)

Plan step **R2a** of ``docs/plans/implementation_plan_recurrence_redesign.md``
-- the vocabulary half of R2.  Creates the three ``ref`` lookup tables the
redesign's two-axis model is expressed in, and nothing else: no column on
``budget.recurrence_rules`` moves, no rule is read or written, and no
behaviour changes.  Step R2b adds the columns that FK here.

  1. **ref.recurrence_units** -- ``period`` / ``week`` / ``month`` / ``year``.
     The first axis: a rule recurs every ``interval_n`` units of this kind.
     Four of ``ref.recurrence_patterns``' eight names (Monthly, Quarterly,
     Semi-Annual, Annual) are the same idea with a different integer baked
     into the NAME -- every 1, 3, 6 or 12 months -- which is the root cause
     the redesign names: one cadence family got a knob (``interval_n``, for
     Every N Periods) and the other got hardcoded constants.  ``week`` has no
     equivalent in the old set at all.
  2. **ref.period_placements** -- ``containing_date`` /
     ``period_starting_on_or_after``.  An occurrence is a calendar DATE; a
     Shekel row lives in a pay PERIOD.  This is the rule that carries one to
     the other, and it is a real user choice rather than a derived detail:
     it is the axis today's ``Monthly`` and ``Monthly First`` patterns
     differ on (they differ on the anchor day as well -- Monthly anchors on
     its ``day_of_month``, Monthly First on the 1st).
  3. **ref.business_day_shifts** -- ``none`` / ``prior`` / ``next``.  The
     weekend/holiday adjustment for an occurrence date.  Seeded here so step
     R2b can default every rule to ``none`` and step R8 turns the behaviour
     ON rather than adding a column to a populated table.

**Inline seed rationale.**  Each table is seeded in this same migration (not
deferred to the entrypoint's ``seed_reference_data`` pass) so that
``ref_cache.init()`` resolves the new ``RecurrenceUnitEnum`` /
``PeriodPlacementEnum`` / ``BusinessDayShiftEnum`` members immediately after
a bare ``flask db upgrade`` -- an enum member with no matching row is a fatal
``RuntimeError`` at app start, and a freshly-upgraded-but-not-yet-seeded
database would otherwise trip it.  ``ON CONFLICT (name) DO NOTHING`` keeps
the seed idempotent against a re-run and against the entrypoint's later
idempotent reseed (which carries the identical rows via ``app/ref_seeds.py``).
The duplication between the two seed sites is the established project pattern
(see ``f5037400dc5e`` / the posting-ledger ref tables): migrations run below
the app layer and must not import ``app`` code, so the bootstrap values live
here in raw SQL and the ongoing idempotent reseed lives in ``ref_seeds``.

**No explicit ids.**  The rows are inserted without an ``id``, so the identity
sequence stays in step (an earlier ref-table migration, ``1dc0e7a1b9e4``,
inserted literal ids and left ``goal_modes_id_seq`` behind its own table).
Nothing depends on a particular id: every reader resolves these through
``ref_cache``, and step R2b's own backfill resolves them by ``name`` subquery.

**Not audited.**  All three are read-only seed catalogues, so they are
deliberately excluded from ``app.audit_infrastructure.AUDITED_TABLES`` -- the
same inclusion criteria that keep ``ref.statuses`` and
``ref.recurrence_patterns`` out; only the multi-tenant ``ref.account_types``
is audited.  No audit trigger is attached here, and
``EXPECTED_TRIGGER_COUNT`` is unchanged.  (The plan's section 3 says "all
three new tables go into ``AUDITED_TABLES``"; measured against those criteria
that is wrong for the ref tables -- the redesign's only audited new table is
``budget.recurrence_weekday_anchors`` in step R2b, plus
``budget.recurrence_due_dates`` in Half B.)

**Self-contained dependency policy.**  This migration imports nothing from
``app`` -- not models, not enums, not ``ref_cache``.  All values are inline
raw SQL because migrations run at fragile bootstrap moments (the ref-cache
layer is itself initialising) and must survive aggressive refactors in app
code.

**Downgrade.**  Drops all three tables (their PK/unique constraints drop with
them).  Fully reversible and non-destructive: nothing references them at this
point in the chain -- the ``budget.recurrence_rules`` FKs that target them
arrive in step R2b's migration, which downgrades first -- and the seed rows
are reproduced verbatim by the inline seed on the next upgrade.  No
``Review:`` line is required: this migration drops nothing and renames
nothing, and its downgrade removes only objects it created in the same
revision.

Revision ID: e7a4d95c2b18
Revises: b5e3d9c1a7f2
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'e7a4d95c2b18'
down_revision = 'b5e3d9c1a7f2'
branch_labels = None
depends_on = None


# Inline seed SQL.  The ``name`` values MUST match the enum ``.value``
# strings in ``app/enums.py`` exactly (``RecurrenceUnitEnum``,
# ``PeriodPlacementEnum``, ``BusinessDayShiftEnum``) and the entries in
# ``app/ref_seeds.py``, or ``ref_cache.init()`` raises at app start.
# ``ON CONFLICT (name) DO NOTHING`` makes each statement idempotent against
# a partial re-run and against the entrypoint's later idempotent reseed.
_SEED_RECURRENCE_UNITS_SQL = (
    "INSERT INTO ref.recurrence_units (name) VALUES "
    "('period'), "
    "('week'), "
    "('month'), "
    "('year') "
    "ON CONFLICT (name) DO NOTHING"
)

_SEED_PERIOD_PLACEMENTS_SQL = (
    "INSERT INTO ref.period_placements (name) VALUES "
    "('containing_date'), "
    "('period_starting_on_or_after') "
    "ON CONFLICT (name) DO NOTHING"
)

_SEED_BUSINESS_DAY_SHIFTS_SQL = (
    "INSERT INTO ref.business_day_shifts (name) VALUES "
    "('none'), "
    "('prior'), "
    "('next') "
    "ON CONFLICT (name) DO NOTHING"
)


def upgrade():
    """Create and inline-seed the three two-axis recurrence ref tables."""
    op.create_table(
        "recurrence_units",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=10), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    op.execute(_SEED_RECURRENCE_UNITS_SQL)

    op.create_table(
        "period_placements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=30), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    op.execute(_SEED_PERIOD_PLACEMENTS_SQL)

    op.create_table(
        "business_day_shifts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=10), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    op.execute(_SEED_BUSINESS_DAY_SHIFTS_SQL)


def downgrade():
    """Drop the three recurrence ref tables (reverse create order)."""
    op.drop_table("business_day_shifts", schema="ref")
    op.drop_table("period_placements", schema="ref")
    op.drop_table("recurrence_units", schema="ref")
