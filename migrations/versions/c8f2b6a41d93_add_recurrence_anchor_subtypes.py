"""add the recurrence anchor subtypes and the count-bounded end

Plan step **R2b** of ``docs/plans/implementation_plan_recurrence_redesign.md``,
as amended by the developer ruling of 2026-08-07 (plan step R2d).

What it creates
---------------

On ``budget.recurrence_rules``:

* ``max_occurrences`` -- the count-bounded end, nullable, with
  ``CHECK (max_occurrences IS NULL OR max_occurrences > 0)`` and
  ``CHECK (end_date IS NULL OR max_occurrences IS NULL)`` (at most ONE closing
  bound: a rule that both ends on a date and after N occurrences has two
  answers to "when does this stop", and the engine would have to pick one).
  No writer until plan step R8, so it can only be NULL and neither CHECK can
  fire on a live edit.

And two 0-or-1 subtype tables, ``budget.recurrence_weekday_anchors`` (first
writer: step R8) and ``budget.recurrence_month_anchors`` (first writer: step
R7c).  Both are audited.

**Both subtypes carry a surrogate ``id``, not the plan sketch's
``recurrence_rule_id PK``.**  ``system.audit_trigger_func`` assigns
``v_row_id := NEW.id``; on a table without that column every INSERT dies with
``record "new" has no field "id"`` -- measured against a probe table on the
dev database (2026-08-05) rather than inferred.  ``UNIQUE
(recurrence_rule_id)`` enforces the identical 0-or-1 cardinality and matches
every other table in the schema.  For the same house-consistency reason the
day/week columns are ``INTEGER`` rather than the plan sketch's ``SMALLINT``:
``day_of_month`` and ``month_of_year`` on the parent table are ``INTEGER``, no
table in the project uses ``SMALLINT``, and the CHECK constraints -- not the
physical width -- are what bound the domain.

Why the two-axis COLUMNS are deliberately absent
------------------------------------------------

**This revision originally added ``unit_id`` / ``anchor_date`` /
``placement_id`` / ``shift_id`` to ``budget.recurrence_rules`` and backfilled
all 50 live rules.  That was withdrawn, and their absence here is the design,
not an oversight.  Do not add them back.**

Those four values are a DERIVATION over the columns beside them -- the closed
``pattern_id`` set plus ``day_of_month`` / ``month_of_year`` /
``start_period_id`` / ``start_date`` / ``interval_n`` and the owner's
pay-period schedule.  Storing a derivation next to its own inputs is a cache,
and a cache drifts the moment one writer moves one side alone.  Every
mechanism proposed to stop that -- read-only column accessors, a pylint
checker, a periodic integrity scan -- is apparatus for keeping a cache honest,
and none of them could be complete: measured on SQLAlchemy 2.0.49, read-only
accessors block attribute assignment and keyword construction but not ORM bulk
``update()``, Core ``update()`` on ``__table__``, or assignment to the private
name.

So the cache is not kept honest; it is not kept.  The two-axis view of a rule
is computed on demand by :func:`app.services.recurrence.resolve`, which takes
the authored spec and the owner's schedule and is the single producer.  There
is no stored copy, therefore no drift, therefore nothing to fence.

The four columns land -- NOT NULL, from one backfill, in the same transaction
that drops the closed-set columns they were derived from -- at plan step
**R7c**, which is where the recurrence form starts collecting them.  At that
point they are AUTHORED rather than derived, and storing them is correct.  The
value's nature changes at R7c and its storage changes with it.

``interval_n`` is likewise NOT rewritten here.  The original revision gave it
the two-axis meaning (3 for Quarterly, 6 for Semi-Annual, 1 elsewhere) and
carried a guard plus a downgrade restore to keep that reversible.  Under the
ruling above it keeps its single original meaning -- "repeat every N pay
periods", read only in ``match_periods``' EVERY_N_PERIODS branch -- and step
R7c derives the two-axis interval along with the rest.

**Nothing reads any of this yet**, so the R1 behaviour baseline
(``tests/oracles/recurrence_baseline.txt``) is byte-identical across this
revision; a moved line would mean this migration touched something it should
not have.

**This revision was AMENDED in place**, which is safe only because it was the
chain head and had never left ``dev``: ``origin/main`` and the production
database both predate it, and the one database that had run the withdrawn
version (the developer's dev clone) was downgraded to ``e7a4d95c2b18`` and
re-upgraded, which is a tested reversal.  **A database restored from a backup
taken between commit ``86b9eaa3`` and this amendment is the one case that
needs a hand.**  Its ``alembic_version`` already reads ``c8f2b6a41d93``, so
``flask db upgrade`` is a no-op and the withdrawn columns survive with nothing
able to remove them.  Repair it by hand before upgrading further:

```sql
ALTER TABLE budget.recurrence_rules
  DROP COLUMN IF EXISTS unit_id,      DROP COLUMN IF EXISTS anchor_date,
  DROP COLUMN IF EXISTS placement_id, DROP COLUMN IF EXISTS shift_id;
UPDATE budget.recurrence_rules r SET interval_n = 1
  FROM ref.recurrence_patterns p
 WHERE p.id = r.pattern_id AND p.name IN ('Quarterly', 'Semi-Annual');
```

The DROPs are deliberately NOT in ``upgrade``: a destructive statement needs
developer approval and a ``Review:`` line, and writing one to repair a state
no surviving database is in would be the band-aid rather than the fix.

**No ``Review:`` line.**  The project's migration rules define destructive as
drops, renames, type changes and constraint removals; this revision does none
of those.  It adds one nullable column and two empty tables, and it UPDATEs
nothing at all.

**Self-contained dependency policy.**  Imports nothing from ``app`` -- not
models, not enums, not ``ref_cache``.

Revision ID: c8f2b6a41d93
Revises: e7a4d95c2b18
Create Date: 2026-08-05

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c8f2b6a41d93'
down_revision = 'e7a4d95c2b18'
branch_labels = None
depends_on = None


#: The 0-or-1 subtype tables this revision creates, in creation order.  Both
#: are audited, so both get the ``audit_<table>`` trigger below.
_SUBTYPE_TABLES = ("recurrence_weekday_anchors", "recurrence_month_anchors")


def upgrade():
    """Add the count-bounded end and the two anchor subtype tables."""
    # -- Step 1: the count-bounded end, and the bound-exclusivity CHECK --
    op.add_column(
        "recurrence_rules",
        sa.Column("max_occurrences", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.create_check_constraint(
        "ck_recurrence_rules_positive_max_occurrences", "recurrence_rules",
        "max_occurrences IS NULL OR max_occurrences > 0", schema="budget",
    )
    op.create_check_constraint(
        "ck_recurrence_rules_single_end_bound", "recurrence_rules",
        "end_date IS NULL OR max_occurrences IS NULL", schema="budget",
    )

    # -- Step 2: the two 0-or-1 subtype tables --------------------------
    op.create_table(
        "recurrence_weekday_anchors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recurrence_rule_id", sa.Integer(), nullable=False),
        sa.Column("nth_week", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "nth_week BETWEEN -1 AND 5 AND nth_week <> 0",
            name="ck_recurrence_weekday_anchors_nth_week",
        ),
        sa.CheckConstraint(
            "weekday BETWEEN 0 AND 6",
            name="ck_recurrence_weekday_anchors_weekday",
        ),
        sa.ForeignKeyConstraint(
            ["recurrence_rule_id"], ["budget.recurrence_rules.id"],
            name="fk_recurrence_weekday_anchors_rule_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recurrence_rule_id", name="uq_recurrence_weekday_anchors_rule",
        ),
        schema="budget",
    )
    op.create_table(
        "recurrence_month_anchors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recurrence_rule_id", sa.Integer(), nullable=False),
        sa.Column("nominal_day", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "nominal_day BETWEEN 29 AND 31",
            name="ck_recurrence_month_anchors_nominal_day",
        ),
        sa.ForeignKeyConstraint(
            ["recurrence_rule_id"], ["budget.recurrence_rules.id"],
            name="fk_recurrence_month_anchors_rule_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recurrence_rule_id", name="uq_recurrence_month_anchors_rule",
        ),
        schema="budget",
    )

    # -- Step 3: attach the audit triggers ------------------------------
    # Both tables hold user-controlled budget state, so both are in
    # AUDITED_TABLES.  The shared trigger function is already in place from
    # the rebuild migration (a5be2a99ea14); the DROP IF EXISTS pair makes the
    # step idempotent against a re-run.  Trigger name ``audit_<table>``
    # matches the convention the entrypoint health check counts.
    for table in _SUBTYPE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS audit_{table} ON budget.{table}")
        op.execute(
            f"CREATE TRIGGER audit_{table} "
            f"AFTER INSERT OR UPDATE OR DELETE ON budget.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
        )


def downgrade():
    """Remove everything ``upgrade`` added.

    Exact, not approximate: this revision writes no data at all, and both
    subtype tables carried none by construction (their first writers are plan
    steps R7c and R8).  Dropping a table drops its audit trigger with it, and
    dropping ``max_occurrences`` takes both CHECKs that name it.
    """
    op.drop_constraint(
        "ck_recurrence_rules_single_end_bound", "recurrence_rules",
        schema="budget", type_="check",
    )
    op.drop_constraint(
        "ck_recurrence_rules_positive_max_occurrences", "recurrence_rules",
        schema="budget", type_="check",
    )
    op.drop_table("recurrence_month_anchors", schema="budget")
    op.drop_table("recurrence_weekday_anchors", schema="budget")
    op.drop_column("recurrence_rules", "max_occurrences", schema="budget")
